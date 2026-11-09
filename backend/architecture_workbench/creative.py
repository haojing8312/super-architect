"""Native runtime creative requests, receipts and run-owned artifact validation."""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
import re
import stat
from typing import Literal

from fastapi import APIRouter
from PIL import Image
from pydantic import BaseModel, ConfigDict, Field

from .documents import MAX_FILE_BYTES, file_type

CreativeKind = Literal["", "image", "edit", "video", "model", "render", "design", "tools"]
MEDIA_KINDS = {"image", "edit", "video", "design"}
IMAGE_KINDS = {"image", "edit", "design"}
NATIVE_MEDIA_TOOLS = {"ImageGen", "ImageEdit", "VideoGen"}
RASTER = {".png": "PNG", ".jpg": "JPEG", ".jpeg": "JPEG", ".webp": "WEBP", ".gif": "GIF", ".bmp": "BMP"}
MESH = {".blend", ".glb", ".gltf", ".obj", ".fbx", ".stl", ".ply"}
DESIGN_SKILLS = {"ppt-master", "design-presentation-web", "interior-3d-web"}


def media_permission(kind: str, skills: list[dict]) -> str:
    return kind or ("image" if any(skill.get("id") in DESIGN_SKILLS for skill in skills) else "")


class CreativeOptions(BaseModel):
    model_config = ConfigDict(extra="forbid")
    size: Literal["auto", "1024x1024", "1536x1024", "1024x1536"] = "auto"
    quality: Literal["auto", "low", "medium", "high"] = "auto"
    style: str = Field(default="", max_length=4000)
    background: Literal["auto", "opaque", "transparent"] = "auto"
    resolution: Literal["720P", "1080P"] = "1080P"
    aspect_ratio: Literal["16:9", "9:16", "1:1"] = "16:9"
    enable_audio: bool = True
    negative_prompt: str = Field(default="", max_length=2500)
    media_model: str = Field(default="", max_length=128)


def media_tools(kind: str) -> list[str]:
    if kind in {"image", "design"}:
        return ["ImageGen"]
    if kind == "edit":
        return ["ImageGen", "ImageEdit"]
    if kind == "video":
        return ["VideoGen"]
    return []


def is_link(path: Path) -> bool:
    value = path.lstat()
    return stat.S_ISLNK(value.st_mode) or bool(getattr(value, "st_file_attributes", 0) & 0x400)


def run_output_directory(workspace: str, run_id: str) -> Path:
    if not re.fullmatch(r"[a-f0-9]{32}", run_id):
        raise ValueError("运行标识无效")
    root = Path(workspace).resolve()
    # The legacy Codex collector's destination is protected too, without altering it.
    for relative in ("outputs", "outputs/runs", "outputs/generated-images", f"outputs/runs/{run_id}"):
        path = root / relative
        if not path.resolve().is_relative_to(root) or (path.exists() and is_link(path)):
            raise ValueError("创作输出目录包含越界链接")
    directory = root / "outputs" / "runs" / run_id
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def validate_creative(kind: str, provider: str, options: dict, attachments: list[dict], prompt: str = "") -> CreativeOptions:
    settings = CreativeOptions.model_validate(options)
    if kind == "video" and len(prompt + ("\n视觉风格：" + settings.style if settings.style else "")) > 2500:
        raise ValueError("视频描述（含视觉风格）最多2500字符，请精简后提交")
    if kind == "video" and provider != "codebuddy":
        raise ValueError("当前已发现的原生视频工具来自 CodeBuddy，请选择 CodeBuddy；Codex 图像能力不等同于原生生视频")
    if kind == "edit" and not attachments:
        raise ValueError("图像编辑需要至少一张真实参考图片")
    maximum = 2 if kind == "video" else 3
    if kind in MEDIA_KINDS and len(attachments) > maximum:
        raise ValueError(f"该创作最多接受 {maximum} 张参考图片")
    if kind in MEDIA_KINDS:
        for attachment in attachments:
            path = Path(attachment["path"])
            expected = RASTER.get(path.suffix.lower())
            if not expected:
                raise ValueError("原生图片/视频参考只支持 PNG、JPEG、WebP、GIF、BMP")
            with Image.open(path) as image:
                if image.format != expected:
                    raise ValueError("参考图片内容与后缀不一致")
                image.verify()
    return settings


def creative_prompt(kind: str, provider: str, prompt: str, attachments: list[dict], options: CreativeOptions, output: Path) -> str:
    if not kind:
        return ""
    if kind == "tools":
        return "\n本轮是工具能力查询，仅核对可用工具与依赖，不调用图片/视频生成，不修改全局配置。"
    summary = {"request_kind": kind, "output_dir": str(output), "options": options.model_dump(),
               "reference_images": [item["path"] for item in attachments]}
    instructions = "\n本轮用户明确提交的创作参数：" + json.dumps(summary, ensure_ascii=False)
    instructions += "\n输出只能属于当前运行目录；不能读取其他用户产物、扫描用户主目录、改全局Agent配置或调用独立供应商API。不得把示例、草稿或assistant声明当成真实生成成功。"
    if kind in {"model", "render"}:
        return instructions + ("\n通过已连接的Blender MCP操作真实场景并保存产物；不调用生图冒充三维模型。"
            "图像参考只能帮助几何近似重建，这不是专用单图转3D模型。模型任务至少保存可编辑.blend及.glb/.obj，"
            "渲染任务保存实际场景及真实渲染PNG。所有路径必须使用上述output_dir。缺宿主/插件或纹理依赖时明确停止并说明。")
    instructions += "\n只执行一次本轮原生媒体生成；账号/模型/工具拒绝时报告真实错误，不切换API、不购买额度、不生成伪图或用脚本动画代替原生AI视频。"
    if provider == "codex":
        return instructions + ("\n调用内建image_gen（$imagegen）生成或编辑；参考图片已通过本机视觉附件加载。"
            "不能用Python/PIL/SVG/HTML/Blender替代本轮原生图像生成。按尺寸、质量、风格及保留项表达需求，"
            "实际尺寸以结果为准。工具输出由工作台按准确thread_id收集，你不要查找generated_images或扫描主目录，也无需Shell复制文件。")
    common = {"prompt": prompt + ("\n视觉风格：" + options.style if options.style else ""), "output_dir": str(output)}
    if kind == "video":
        tool = "VideoGen"
        parameters = {**common, "resolution": options.resolution, "enable_audio": options.enable_audio,
                      "negative_prompt": options.negative_prompt}
        if attachments:
            parameters["image"] = attachments[0]["path"]
            if len(attachments) > 1:
                parameters["last_image"] = attachments[1]["path"]
        else:
            parameters["aspect_ratio"] = options.aspect_ratio
    else:
        tool = "ImageEdit" if kind == "edit" and len(attachments) == 1 else "ImageGen"
        parameters = dict(common)
        for field in ("size", "quality", "background"):
            if getattr(options, field) != "auto":
                parameters[field] = getattr(options, field)
        if tool == "ImageEdit":
            parameters["image"] = attachments[0]["path"]
        else:
            parameters.update({f"image{index + 1}": attachment["path"] for index, attachment in enumerate(attachments)})
    return instructions + (f"\n使用CodeBuddy内建{tool}；先ToolSearch发现，再DeferExecuteTool执行。"
        "创作可能消耗该账号的额外媒体积分；使用实际回执报告用量，无数值不编造。\n原生参数："
        + json.dumps(parameters, ensure_ascii=False))


def optional_media_prompt(provider: str, output: Path) -> str:
    common = ("\n本轮PPT/设计任务若需要真实配图，允许使用本机runtime原生生图，图片保存目录为 "
              + json.dumps(str(output), ensure_ascii=False)
              + "。没有配图需要时不额外生图；根据当前单张素材目标拟定图像提示词，不把整份PPT文字直接当图片内容。"
              "原技能的外部图像SDK、17后端API、生图脚本路由等不能使用。代码只可处理本机文件、布局、SVG图表和Office/Blender产物。")
    if provider == "codex":
        return common + "使用内建image_gen；工作台按准确thread_id收图，不扫描主目录或其他线程查图片，不需要shell复制。"
    return common + "使用ToolSearch/DeferExecuteTool调用ImageGen，output_dir指定上述目录。使用原生账号回执报告实际积分，没有数字不编造。"


class MediaReceipts:
    def __init__(self):
        self.calls = {}
        self.completed = {}
        self.usage = {}
        self.host_completed = False

    def observe(self, event: dict):
        kind = event.get("type")
        if kind in {"turn.completed", "result"} and isinstance(event.get("usage"), dict):
            self.usage = {key: value for key, value in event["usage"].items()
                          if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value >= 0}
        if kind == "assistant":
            content = event.get("message", {}).get("content", [])
            for block in content if isinstance(content, list) else []:
                if not isinstance(block, dict):
                    continue
                if block.get("type") != "tool_use":
                    continue
                name = block.get("name", "")
                actual = block.get("input", {}).get("toolName", name) if name == "DeferExecuteTool" else name
                self.calls[block.get("id", "")] = actual
        if kind == "user":
            content = event.get("message", {}).get("content", [])
            for block in content if isinstance(content, list) else []:
                if not isinstance(block, dict):
                    continue
                if block.get("type") != "tool_result":
                    continue
                identifier = block.get("tool_use_id", "")
                actual = self.calls.get(identifier, "")
                if "blender" in actual.lower() and not block.get("is_error"):
                    self.host_completed = True
                if actual not in NATIVE_MEDIA_TOOLS or block.get("is_error"):
                    continue
                candidates = [block.get("_meta", {}).get("rawResponse", {})]
                blocks = block.get("content", [])
                for content in blocks if isinstance(blocks, list) else []:
                    if not isinstance(content, dict):
                        continue
                    if content.get("type") == "text":
                        try:
                            candidates.append(json.loads(content.get("text", "")))
                        except ValueError:
                            pass
                for result in candidates:
                    if isinstance(result, dict) and result.get("status") == "completed" and result.get("type") in {
                            "image_gen_tool_result", "image_edit_tool_result", "video_gen_tool_result"}:
                        self.completed[identifier] = {**self.completed.get(identifier, {}), **result, "tool": actual}
        if kind == "item.completed":
            item = event.get("item", {})
            bad_result = item.get("result", {}).get("isError") if isinstance(item.get("result"), dict) else False
            if item.get("type") == "mcp_tool_call" and "blender" in str(item.get("server", "")).lower() and not item.get("error") and not bad_result and item.get("status") != "failed":
                self.host_completed = True

    def media_paths(self, directory: Path | None = None) -> set[Path]:
        result = set()
        for receipt in self.completed.values():
            for field in ("images", "videos"):
                for item in receipt.get(field, []):
                    value = item.get("localPath") if isinstance(item, dict) else None
                    if not isinstance(value, str) or value.startswith(("\\\\", "//")) or "\x00" in value:
                        continue
                    path = Path(value)
                    if directory is not None and not path.is_relative_to(directory):
                        continue
                    result.add(path.resolve())
        return result

    def usage_data(self):
        credits = [item["credit"] for item in self.completed.values() if isinstance(item.get("credit"), (int, float))
                   and not isinstance(item["credit"], bool) and math.isfinite(item["credit"]) and item["credit"] >= 0]
        return {"credits": credits, "token_usage": self.usage, "native_tools": [item["tool"] for item in self.completed.values()]}

    def usage_text(self) -> str:
        data = self.usage_data()
        pieces = []
        if data["credits"]:
            pieces.append("媒体工具回执积分：" + " + ".join(str(value) for value in data["credits"]))
        if self.usage:
            pieces.append("Agent用量回执：" + "，".join(f"{key}={value}" for key, value in self.usage.items()))
        return "\n\n" + "；".join(pieces) if pieces else "\n\n本机工具未返回明确用量数值。"


def artifact_info(path: Path, source: str) -> dict | None:
    size = path.stat().st_size
    if size <= 0 or size > MAX_FILE_BYTES:
        return None
    suffix = path.suffix.lower()
    kind = "image" if suffix in RASTER else "video" if suffix == ".mp4" else "model" if suffix in MESH else ""
    if not kind:
        return None
    if kind == "image":
        try:
            with Image.open(path) as image:
                if image.format != RASTER[suffix]:
                    return None
                dimensions = list(image.size)
                image.verify()
        except (OSError, ValueError):
            return None
    else:
        dimensions = None
        with path.open("rb") as handle:
            beginning = handle.read(32)
        if suffix == ".mp4" and (len(beginning) < 12 or beginning[4:8] != b"ftyp"):
            return None
        if suffix == ".blend" and not beginning.startswith(b"BLENDER"):
            return None
        if suffix == ".glb" and not beginning.startswith(b"glTF"):
            return None
        if suffix == ".gltf":
            try:
                value = json.loads(path.read_text(encoding="utf-8"))
                if not isinstance(value.get("asset"), dict) or not value.get("asset", {}).get("version"):
                    return None
            except (ValueError, UnicodeError):
                return None
    return {"name": path.name, "path": str(path), "type": file_type(path), "size": size,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "source": source, "kind": kind,
            "image": kind == "image", **({"dimensions": dimensions} if dimensions else {})}


def collect_run_artifacts(directory: Path, started: float, kind: str, provider: str, receipts: MediaReceipts) -> list[dict]:
    workspace = directory.parents[2]
    if any(parent.exists() and is_link(parent) for parent in (directory, directory.parent, directory.parent.parent)):
        raise ValueError("运行产物目录被替换为链接，拒绝读取其他目录")
    if not directory.resolve().is_relative_to(workspace.resolve()):
        raise ValueError("运行产物越出会话工作区")
    directory = directory.resolve()
    allowed_paths = receipts.media_paths(directory) if provider == "codebuddy" and kind in MEDIA_KINDS else None
    if kind in {"model", "render"} and not receipts.host_completed:
        return []
    result = []
    for candidate in directory.rglob("*"):
        if len(result) >= 20:
            break
        if not candidate.is_file() or is_link(candidate) or not candidate.resolve().is_relative_to(directory):
            continue
        if any(is_link(parent) for parent in candidate.parents if parent != directory and parent.is_relative_to(directory)):
            continue
        path = candidate.resolve()
        if path.stat().st_mtime < started - 3 or allowed_paths is not None and path not in allowed_paths:
            continue
        if info := artifact_info(path, "codebuddy-native-media" if kind in MEDIA_KINDS else "blender-mcp"):
            if kind in IMAGE_KINDS and info["kind"] != "image" or kind == "video" and info["kind"] != "video":
                continue
            result.append(info)
    return result


def artifact_markdown(items: list[dict]) -> str:
    lines = []
    for item in items:
        name = item["name"].replace("[", "").replace("]", "")
        path = item["path"].replace("\\", "/")
        lines.append(("!" if item.get("kind") == "image" or item.get("image") else "") + f"[{name}](<{path}>)")
    return "\n\n已核验的本机产物：\n" + "\n".join(lines) if lines else ""


def register_creative(app):
    router = APIRouter(prefix="/api/creative")

    @router.get("/capabilities")
    def capabilities():
        return {"providers": {"codex": {"image": "已验证原生生图", "edit": "原生编辑待场景验收", "video": "未发现原生视频工具"},
                              "codebuddy": {"image": "已验证ImageGen", "edit": "ImageEdit待场景验收", "video": "VideoGen待账号及产物验收"}},
                "model": "通过本机Agent与Blender进行几何重建，不是专用图转3D模型",
                "account_note": "本机账号、模型、额度和工具可用性以本次真实返回为准"}

    app.include_router(router)
