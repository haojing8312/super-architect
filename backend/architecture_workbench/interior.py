"""Task-bound interior scenes assembled by the shipped, immutable editor.

The service never infers a floor plan. Recognition is done by the existing local
Agent, or users trace their own plan. Every accepted scene remains bound to its
source bytes and selected PDF page; draft export is explicitly a draft.
"""
from __future__ import annotations

import base64
import copy
import hashlib
import io
import json
import math
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import threading
import warnings

from fastapi import APIRouter, Body, HTTPException, Request
from fastapi.responses import FileResponse, Response
from PIL import Image
from pydantic import BaseModel, Field

from .skills import atomic_json, resource_skill_root
from .store import new_id, now

ENGINE = "jzxz-interior/1.0.0"
MAX_SOURCE = 15 * 1024 * 1024
MAX_SCENE = 40 * 1024 * 1024
PRIMARY_SOURCE_REFERENCE = "workbench:primary-source"
STYLES = {"ref_warm": "暖调中古", "ref_bauhaus": "包豪斯混搭", "ref_italian": "意式雅奢",
          "minimal": "现代高级", "scandi": "北欧自然", "wabi": "原木侘寂", "oriental": "现代东方", "cream": "奶油中古"}
RASTERS = {".png": ("PNG", "image/png"), ".jpg": ("JPEG", "image/jpeg"), ".jpeg": ("JPEG", "image/jpeg"), ".webp": ("WEBP", "image/webp")}


def _linked(path):
    info = path.lstat()
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, "st_file_attributes", 0) & 0x400)


def _ordinary(path, root):
    path, root = Path(path), Path(root).resolve()
    resolved = path.resolve()
    if resolved == root or not resolved.is_relative_to(root):
        raise ValueError("室内任务文件越出当前会员工作区")
    for current in (path, *path.parents):
        if current.exists() and _linked(current):
            raise ValueError("室内任务不支持符号链接或目录连接")
        if current == root:
            break
    if path.exists() and path.is_file() and path.stat().st_nlink > 1:
        raise ValueError("室内任务不支持硬链接文件")
    return resolved


def _raster_info(data, expected=None):
    with warnings.catch_warnings():
        warnings.simplefilter("error", Image.DecompressionBombWarning)
        with Image.open(io.BytesIO(data)) as image:
            if image.format not in {"PNG", "JPEG", "WEBP"} or (expected and image.format != expected):
                raise ValueError("底图后缀与图片内容不符")
            width, height = image.size
            if width * height > 24_000_000 or width < 2 or height < 2:
                raise ValueError("底图像素数必须在 4 到 2400 万之间")
            image.verify()
    return width, height


class InteriorService:
    def __init__(self, store, skill_root=None):
        self.store = store
        # Never use an imported override as executable editor code.
        self.skill_root = Path(skill_root or resource_skill_root() / "interior-3d-web").resolve()
        self.lock = threading.RLock()

    @property
    def root(self):
        target = _ordinary(Path(self.store.data_dir) / "workspaces" / "interior", self.store.data_dir)
        target.mkdir(parents=True, exist_ok=True)
        return target

    def _directory(self, task_id):
        if not re.fullmatch(r"[a-f0-9]{32}", task_id or ""):
            raise KeyError("室内任务不存在")
        directory = _ordinary(self.root / task_id, self.store.data_dir)
        if not (directory / "task.json").is_file():
            raise KeyError("室内任务不存在")
        return directory

    def _load(self, task_id):
        directory = self._directory(task_id)
        value = json.loads(_ordinary(directory / "task.json", self.store.data_dir).read_text(encoding="utf-8"))
        if value.get("id") != task_id:
            raise ValueError("室内任务记录不一致")
        return directory, value

    def _node(self):
        bundled = resource_skill_root().parent / "node" / "node.exe"
        configured = self.store.get_settings().get("node_executable")
        target = configured or (str(bundled) if bundled.is_file() else shutil.which("node"))
        if not target:
            raise ValueError("三维网页组装需要本机 Node.js，请在设置中配置")
        return str(target)

    def styles(self):
        path = self.skill_root / "固定编辑器" / "风格统一配置.json"
        if not path.is_file():
            raise ValueError("内置室内三维编辑器资源缺失")
        return json.loads(path.read_text(encoding="utf-8"))["styles"]

    def capabilities(self):
        required = ["SKILL.md", "开始生成.md", "生成本次网页.cjs", "固定编辑器/固定编辑器代码.json", "固定编辑器/场景校验.cjs", "固定编辑器/交付阶段校验.cjs"]
        missing = [name for name in required if not (self.skill_root / name).is_file()]
        try:
            import pypdfium2  # noqa: F401
            pdf = True
        except ImportError:
            pdf = False
        try:
            node = bool(self._node())
        except ValueError:
            node = False
        return {"engine": ENGINE, "ready": not missing and node, "missing": missing, "pdf": pdf, "node": node,
                "recognition": "local-agent-or-manual-tracing", "limits": {"source_bytes": MAX_SOURCE, "source_pixels": 24_000_000},
                "note": "底图识别与尺度估算需要核对；程序化家具不等同照片级模型或施工图"}

    def list(self):
        items = []
        for folder in self.root.iterdir():
            if folder.is_dir() and not _linked(folder) and re.fullmatch(r"[a-f0-9]{32}", folder.name):
                try:
                    items.append(self.get(folder.name, include_prompt=False))
                except (OSError, ValueError, KeyError):
                    continue
        return sorted(items, key=lambda item: item["created_at"], reverse=True)

    def _source(self, file_id, page=1, role="plan"):
        file = self.store.get_file(file_id)
        path = _ordinary(file["path"], self.store.data_dir)
        if not path.is_file() or not 0 < path.stat().st_size <= MAX_SOURCE:
            raise ValueError("底图必须为非空文件且不超过 15 MB")
        data = path.read_bytes()
        result = {"id": file_id, "name": file["name"], "path": str(path), "bytes": len(data),
                  "sha256": hashlib.sha256(data).hexdigest(), "role": role, "page": page}
        suffix = Path(file["name"]).suffix.lower()
        if suffix in RASTERS:
            if page != 1:
                raise ValueError("图片底图页码只能为 1")
            result.update(kind="image", mime=RASTERS[suffix][1])
            result["width"], result["height"] = _raster_info(data, RASTERS[suffix][0])
            return result, data
        if suffix != ".pdf" or not data.startswith(b"%PDF-") or role != "plan":
            raise ValueError("主底图支持 PNG、JPEG、WebP 或 PDF；风格参考须为图片")
        try:
            import pypdfium2
        except ImportError as error:
            raise ValueError("PDF 底图需要已打包的 PDF 栅格化组件") from error
        try:
            document = pypdfium2.PdfDocument(data)
        except Exception as error:
            raise ValueError("PDF 无法读取或需要密码") from error
        try:
            if not 1 <= page <= len(document):
                raise ValueError("选定 PDF 页码不存在")
            selected = document[page - 1]
            try:
                pw, ph = selected.get_size()
                if not 0 < pw <= 50000 or not 0 < ph <= 50000:
                    raise ValueError("PDF 页尺寸无效")
                scale = min(2.0, 4096 / max(pw, ph))
                rendered = selected.render(scale=scale)
                try:
                    stream = io.BytesIO()
                    rendered.to_pil().convert("RGB").save(stream, format="PNG")
                    bitmap = stream.getvalue()
                finally:
                    rendered.close()
                width, height = _raster_info(bitmap, "PNG")
                result.update(kind="pdf", mime="application/pdf", width=width, height=height, pageCount=len(document),
                              rasterSha256=hashlib.sha256(bitmap).hexdigest())
            finally:
                selected.close()
        finally:
            document.close()
        return result, bitmap

    def create(self, options):
        options = dict(options)
        styles = options.get("styles", ["scandi"])
        if not isinstance(styles, list) or not 1 <= len(styles) <= 8 or len(set(styles)) != len(styles) or any(item not in {*STYLES, "custom"} for item in styles):
            raise ValueError("请选择 1 到 8 种不同的内置风格，或单独选择自定义风格")
        custom = "custom" in styles
        if custom and styles != ["custom"]:
            raise ValueError("自定义风格需要单独选择，不与内置风格混用")
        name = options.get("name", "室内三维方案")
        if not isinstance(name, str) or not name.strip() or len(name) > 120 or any(ord(c) < 32 or c in "<>" for c in name):
            raise ValueError("请输入有效的方案名称")
        height, thickness = options.get("height_m", 2.8), options.get("wall_thickness_m", .15)
        if any(isinstance(x, bool) or not isinstance(x, (int, float)) or not math.isfinite(x) for x in [height, thickness]) or not 1.8 <= height <= 8 or not .05 <= thickness <= 1:
            raise ValueError("层高为 1.8–8 米，墙厚为 0.05–1 米")
        page = options.get("page", 1)
        if isinstance(page, bool) or not isinstance(page, int) or not 1 <= page <= 10000:
            raise ValueError("PDF 页码必须为正整数")
        source, bitmap = self._source(options.get("file_id", ""), page)
        references = options.get("reference_ids", [])
        if not isinstance(references, list) or len(references) > 8 or len(set(references)) != len(references):
            raise ValueError("最多选择 8 个风格参考附件")
        if custom and not references:
            raise ValueError("自定义风格需要至少一张本次上传的风格参考图片")
        if options.get("file_id") in references:
            raise ValueError("主底图不能同时作为风格参考附件")
        attachments = [source]
        for identifier in references:
            reference, _ = self._source(identifier, role="style-reference")
            attachments.append(reference)
        scale_policy = options.get("scale_policy", "estimate")
        calibration = None
        if scale_policy == "calibrated":
            points, length = options.get("points_px"), options.get("reference_length_m")
            if (not isinstance(points, list) or len(points) != 2 or any(not isinstance(p, list) or len(p) != 2 for p in points)
                    or any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for p in points for v in p)
                    or any(not 0 <= p[0] <= source["width"] or not 0 <= p[1] <= source["height"] for p in points)
                    or isinstance(length, bool) or not isinstance(length, (int, float)) or not math.isfinite(length) or not .05 <= length <= 1000
                    or math.dist(*points) < 1):
                raise ValueError("请在原图选择两个不同的标定点并填写真实长度（米）")
            calibration = {"pointsPx": points, "referenceLengthM": length}
        elif scale_policy != "estimate":
            raise ValueError("尺度策略必须为 estimate 或 calibrated")
        task_id = new_id()
        directory = _ordinary(self.root / task_id, self.store.data_dir)
        directory.mkdir()
        if source["kind"] == "pdf":
            (directory / "selected-page.png").write_bytes(bitmap)
        catalog = {item["id"]: item for item in self.styles()}
        briefs = ([{"id": "custom", "name": "自定义风格", "referenceImageIds": references,
                    "description": "逐张读取本次风格参考，提取色彩、材质、已实现家具和装饰选型；不可覆盖主底图户型"}] if custom else
                  [{**catalog[identifier], "referenceImageIds": [x["id"] for x in catalog[identifier].get("referenceSources", [])]} for identifier in styles])
        manifest = {"format": "interior-task/1.0", "engineVersion": ENGINE, "configurationVersion": "workbench-interior/1.0",
                    "taskId": task_id, "outputDir": str(directory), "primarySource": source, "attachments": attachments,
                    "config": {"name": name.strip(), "heightM": height, "wallThicknessM": thickness, "doorHeightM": 2.1,
                               "windowSillHeightM": .9, "scalePolicy": "estimate" if scale_policy == "estimate" else "calibrated",
                               "calibration": calibration, "styleMode": "custom" if custom else "preset", "styleBriefs": briefs,
                               "customStyleReferenceIds": references if custom else [],
                               "deliveryWorkflow": "direct-design/1.0", "uncertaintyPolicy": "draft"},
                    "schemeMap": [{"selectionId": identifier, "sceneSchemeId": identifier, "name": "自定义风格" if identifier == "custom" else STYLES[identifier]} for identifier in styles]}
        atomic_json(directory / "室内三维生成配置.json", manifest)
        session = self.store.create_session(name.strip(), workspace=str(directory), skills=["interior-3d-web"])
        task = {"id": task_id, "name": name.strip(), "created_at": now(), "updated_at": now(), "session_id": session["id"],
                "status": "configured", "styles": styles, "source": source, "revision": 0, "latest_html": None,
                "config_path": str(directory / "室内三维生成配置.json"), "candidate_scene_path": str(directory / "本次方案.scene.json")}
        atomic_json(directory / "task.json", task)
        return self.get(task_id)

    def get(self, task_id, include_prompt=True):
        directory, task = self._load(task_id)
        config = self.manifest(task_id)["config"]
        result = {**task, "output_dir": str(directory), "source_url": f"/api/interior/tasks/{task_id}/source/file",
                  "preview_url": f"/api/interior/tasks/{task_id}/preview/file" if task.get("latest_html") else None,
                  "configuration": {key: config[key] for key in ["heightM", "wallThicknessM", "scalePolicy", "calibration", "styleMode"]},
                  "reference_files": [{"id": x["id"], "name": x["name"], "url": f"/api/files/{x['id']}"} for x in self.manifest(task_id)["attachments"] if x["role"] == "style-reference"]}
        if include_prompt:
            result["prompt"] = self.prompt(task_id)
        return result

    def prompt(self, task_id):
        directory, task = self._load(task_id)
        manifest = json.loads((directory / "室内三维生成配置.json").read_text(encoding="utf-8"))
        pdf_note = f"PDF 只识别第 {manifest['primarySource']['page']} 页；已栅格化为 {directory / 'selected-page.png'}。" if task["source"]["kind"] == "pdf" else ""
        return (f"请执行内置 interior-3d-web 技能。先完整读取 {self.skill_root / 'SKILL.md'}、开始生成.md及引用规范。\n"
                f"本次唯一配置：{task['config_path']}。完整读取并核验底图SHA与页码。{pdf_note}\n"
                "本轮 shell 保持只读，不能通过 shell 写文件；这不表示授权工作台输出不可写。"
                "当前会话的 architecture MCP 已提供 session_get 和 file_write，授权范围仅为本次会话 workspace。"
                f"必须先调用 architecture.session_get，核对返回的 id={task['session_id']} 且 workspace 正好是配置 outputDir={directory}，不符则停止。"
                "随后用 architecture.file_write(path=\"本次方案.scene.json\",content=完整JSON文本,overwrite=false) 和 "
                "architecture.file_write(path=\"识别核对记录.md\",content=实际识别记录,overwrite=false) 写出两个相对路径文件。"
                "不要因 shell 的 read-only 提示直接终止；实际尝试当前已授权的 MCP.file_write。"
                "禁止覆写 task.json、配置、数据库、技能、全局 CLI 设置，禁止改 sandbox 或扩文件写权限；文件若已经存在，报告冲突，不覆盖。\n"
                f"重新识别这张主底图，创建taskId={task_id}、isSample=false的本次方案.scene.json：{task['candidate_scene_path']}。"
                "严禁沿用样板户型或跨任务几何。参考图仅提供风格。米制尺度不明时写estimated及assumptions。"
                "提供真实墙段、门窗洞口、顶面，每对象sourceEvidence绑定本图。为全部已选方案生成家具与表面材质。"
                f"本轮 MCP 文本写入上限 2 MiB，不能在作者稿 JSON 中复制大图 base64；source.imageData 必须写唯一占位字符串 {PRIMARY_SOURCE_REFERENCE!r}。"
                "source.name、generation.taskId/sourceFileId/sourceSha256/page 与 isSample=false 仍须完整准确绑定本配置。"
                "若主图为 PDF，source.derivedFromSha256 必须等于原 PDF 哈希，source.page 必须为配置选定页。"
                "这个占位符只用于本次 MCP 作者稿；工作台 collect 会核验本图原始字节/哈希和 PDF 选页位图哈希后，"
                "确定性注入本次底图 imageData，再运行原场景与任务校验器。不得用占位符省略任何几何、家具、风格或来源证据。"
                "不要重新编写编辑器、安装依赖、调用独立远端生成API或改CLI全局配置。只在本次outputDir写产物。"
                "场景采用design-draft，styleAudit不得伪造截图/视觉验收。先产出完整作者稿JSON和识别核对记录；"
                "带占位符的 raw 作者稿不是最终内嵌位图场景，不要对它运行严格 Node validator 或生成入口，"
                "只有后端 collect 展开本图后才运行这些严格校验与组装。"
                "本轮只需完成上述 JSON 与识别记录，不运行组装、截图或正式交付脚本；这些由工作台后置完成。"
                "最后明确列出实际 MCP.file_write 返回的两个文件路径与写入字节数，不以聊天输出代替落盘文件。")

    def manifest(self, task_id):
        directory, _ = self._load(task_id)
        return json.loads((directory / "室内三维生成配置.json").read_text(encoding="utf-8"))

    def scene(self, task_id):
        directory, task = self._load(task_id)
        if not task.get("scene_file"):
            raise KeyError("本任务尚未保存场景")
        return json.loads(_ordinary(directory / task["scene_file"], self.store.data_dir).read_text(encoding="utf-8"))

    def _validate_binding(self, manifest, scene):
        if not isinstance(scene, dict) or len(json.dumps(scene, ensure_ascii=False, allow_nan=False).encode()) > MAX_SCENE:
            raise ValueError("场景必须为不超过 40 MB 的 JSON 对象")
        if (not isinstance(scene.get("architecture"), list) or not isinstance(scene.get("rooms"), list)
                or not isinstance(scene.get("schemes"), dict) or any(not isinstance(x, dict) or not isinstance(x.get("objects"), list) for x in scene["schemes"].values())):
            raise ValueError("场景结构、房间和方案数组无效")
        if len(scene.get("architecture", [])) > 3000 or len(scene.get("rooms", [])) > 100 or sum(len(x.get("objects", [])) for x in scene.get("schemes", {}).values()) > 10000:
            raise ValueError("场景对象数量超过上限")
        for item in manifest["attachments"]:
            path = _ordinary(item["path"], self.store.data_dir)
            data = path.read_bytes()
            if len(data) != item["bytes"] or hashlib.sha256(data).hexdigest() != item["sha256"]:
                raise ValueError("本次底图或附件已经变化，请创建新任务")
        src = manifest["primarySource"]
        generation = {"taskId": manifest["taskId"], "sourceFileId": src["id"], "sourceSha256": src["sha256"], "page": src["page"]}
        if scene.get("generation") != generation or scene.get("isSample") is not False:
            raise ValueError("场景没有绑定本次任务、底图与页码，禁止导入旧户型")
        source = scene.get("source", {})
        match = re.fullmatch(r"data:image/(png|jpeg|webp);base64,([A-Za-z0-9+/=]+)", source.get("imageData", ""))
        if not match or source.get("name") != src["name"]:
            raise ValueError("场景必须内嵌本次底图")
        bitmap = base64.b64decode(match[2], validate=True)
        expected_sha = src["sha256"] if src["kind"] == "image" else src["rasterSha256"]
        if hashlib.sha256(bitmap).hexdigest() != expected_sha or _raster_info(bitmap) != (src["width"], src["height"]):
            raise ValueError("内嵌底图与原图或选定 PDF 页不一致")
        if src["kind"] == "pdf" and (source.get("derivedFromSha256") != src["sha256"] or source.get("page") != src["page"]):
            raise ValueError("PDF 场景缺少准确页码和原文件哈希")
        drawing = scene.get("drawing", {})
        if drawing.get("sourceWidth") != src["width"] or drawing.get("sourceHeight") != src["height"]:
            raise ValueError("场景原图像素尺寸不一致")
        expected = {s["sceneSchemeId"] for s in manifest["schemeMap"]}
        keys = set(scene.get("schemes", {}))
        if not {"base", "mid", *expected}.issubset(keys) or keys - {"base", "mid", "review", *expected} or scene.get("style") not in expected | ({"review"} if "review" in keys else set()):
            raise ValueError("场景方案必须严格对应本次已选风格")
        if manifest["config"].get("styleMode") == "custom":
            profile = scene["schemes"].get("custom", {}).get("customProfile")
            references = manifest["config"]["customStyleReferenceIds"]
            if (not isinstance(profile, dict) or not isinstance(profile.get("referenceImageIds"), list)
                    or not profile["referenceImageIds"] or any(x not in references for x in profile["referenceImageIds"])):
                raise ValueError("customProfile 必须绑定本次上传的风格参考图片，不得沿用历史参考")
        cal = manifest["config"].get("calibration")
        if cal:
            pixel_scale = cal["referenceLengthM"] / math.dist(*cal["pointsPx"])
            try:
                actual = [drawing["scaleX"] * drawing["width"] / src["width"], drawing["scaleZ"] * drawing["height"] / src["height"]]
            except (KeyError, TypeError, ZeroDivisionError) as error:
                raise ValueError("标定场景尺度映射无效") from error
            if drawing.get("status") != "user-calibrated" or any(not math.isclose(x, pixel_scale, rel_tol=1e-5) for x in actual):
                raise ValueError("场景尺度与本次两点标定不一致")
        elif drawing.get("status") != "estimated":
            raise ValueError("未标定的场景必须明确标记 estimated")
        # Evidence paths are a read capability: keep all screenshots in this task.
        output_dir = Path(manifest["outputDir"])
        for scheme in scene.get("schemes", {}).values():
            for supplied in scheme.get("styleAudit", {}).get("screenshots", []):
                if not isinstance(supplied, str) or not Path(supplied).is_absolute():
                    raise ValueError("验收截图必须为本任务内的绝对路径")
                path = _ordinary(supplied, output_dir)
                if not path.is_file() or path.stat().st_size > MAX_SOURCE:
                    raise ValueError("验收截图不存在或超过上限")
                _raster_info(path.read_bytes())

    def _run(self, args, directory):
        result = subprocess.run([self._node(), *map(str, args)], cwd=self.skill_root, capture_output=True, text=True,
                                encoding="utf-8", errors="replace", timeout=90,
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if result.returncode:
            raise ValueError((result.stderr.strip() or "固定编辑器校验失败")[:1800])
        return result

    def save_scene(self, task_id, scene):
        with self.lock:
            directory, task = self._load(task_id)
            manifest = self.manifest(task_id)
            self._validate_binding(manifest, scene)
            # Do not overwrite AI/user inputs. Each accepted version is immutable.
            revision = task["revision"] + 1
            scene = copy.deepcopy(scene)
            scene["revision"] = revision
            target = directory / f"scene-{revision:04d}.json"
            atomic_json(target, scene)
            try:
                self._run(["-e", "const fs=require('fs'),path=require('path');const s=JSON.parse(fs.readFileSync(process.argv[2],'utf8'));require(path.join(process.argv[1],'固定编辑器','场景校验.cjs')).validateScene(s);require(path.join(process.argv[1],'生成本次网页.cjs')).inspectTask(process.argv[1],s,true,process.argv[3]);",
                           self.skill_root, target, task["config_path"]], directory)
            except Exception:
                target.unlink(missing_ok=True)
                raise
            task.update(scene_file=target.name, revision=revision, status="scene-ready", updated_at=now(), latest_html=None)
            atomic_json(directory / "task.json", task)
            return self.get(task_id)

    def trace(self, task_id, body):
        """Explicit manual annotations, never a fallback recognition template."""
        manifest = self.manifest(task_id)
        src, config = manifest["primarySource"], manifest["config"]
        if config["styleMode"] == "custom":
            raise ValueError("自定义风格需先读取参考图片生成 customProfile；可用本机设计任务或导入已绑定的人工场景")
        rooms = body.get("rooms")
        if not isinstance(rooms, list) or not 1 <= len(rooms) <= 30:
            raise ValueError("请在本次底图描绘 1 到 30 个真实房间")
        cal = config.get("calibration")
        if cal:
            scale = cal["referenceLengthM"] / math.dist(*cal["pointsPx"])
            status = "user-calibrated"
        else:
            width_m = body.get("estimated_width_m")
            if isinstance(width_m, bool) or not isinstance(width_m, (int, float)) or not math.isfinite(width_m) or not .5 <= width_m <= 200:
                raise ValueError("未标定时请填写第一个描绘房间的估算宽度（米），不会当作实测")
            bounds = rooms[0].get("bounds_px", [])
            if len(bounds) != 4 or not isinstance(bounds[2], (int, float)) or bounds[2] <= 1:
                raise ValueError("房间原图范围无效")
            scale = width_m / bounds[2]
            status = "estimated"
        height, thickness = config["heightM"], config["wallThicknessM"]
        bitmap = self.source_file(task_id).read_bytes()
        mime = "image/png" if src["kind"] == "pdf" else src["mime"]
        source = {"name": src["name"], "imageData": f"data:{mime};base64," + base64.b64encode(bitmap).decode()}
        if src["kind"] == "pdf":
            source.update(derivedFromSha256=src["sha256"], page=src["page"])
        architecture, scene_rooms, walls = [], [], set()
        def evidence(bounds):
            return {"sourceFileId": src["id"], "sourceSha256": src["sha256"], "pixelBounds": bounds,
                    "inferred": ["manual-user-tracing", "height", "thickness", "untraced-openings"]}
        def obj(identifier, kind, pos, size, room, bounds):
            return {"id": identifier, "name": identifier, "type": kind, "layer": "architecture", "pos": pos, "size": size,
                    "room": room, "rotation": 0, "visible": kind != "ceiling", "color": "#e9e7e1", "material": {"preset": "paint"}, "sourceEvidence": evidence(bounds)}
        for index, room in enumerate(rooms):
            bounds = room.get("bounds_px")
            name = room.get("name", f"描绘房间 {index + 1}")
            if (not isinstance(bounds, list) or len(bounds) != 4 or any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in bounds)
                    or bounds[0] < 0 or bounds[1] < 0 or bounds[2] < 2 or bounds[3] < 2 or bounds[0] + bounds[2] > src["width"] or bounds[1] + bounds[3] > src["height"]
                    or not isinstance(name, str) or not name.strip() or len(name) > 100 or any(c in "<>" for c in name)):
                raise ValueError("房间名称或原图像素范围无效")
            px, py, pw, ph = bounds
            x1, x2 = px * scale, (px + pw) * scale
            z1, z2 = (src["height"] - py - ph) * scale, (src["height"] - py) * scale
            cx, cz = (x1 + x2) / 2, (z1 + z2) / 2
            room_id = f"room-{index+1}"
            polygon = [[x1, z1], [x2, z1], [x2, z2], [x1, z2]]
            scene_rooms.append({"id": room_id, "name": name.strip(), "center": [cx, cz], "size": [pw * scale, ph * scale], "polygon": polygon, "outdoor": False})
            architecture.append(obj("floor" if index == 0 else f"floor-{index+1}", "floor", [cx, -.025, cz], [pw * scale, .05, ph * scale], room_id, bounds))
            architecture.append(obj(f"ceiling-{index+1}", "ceiling", [cx, height + .025, cz], [pw * scale, .05, ph * scale], room_id, bounds))
            edges = [([cx, height / 2, z1], [pw * scale, height, thickness]), ([cx, height / 2, z2], [pw * scale, height, thickness]),
                     ([x1, height / 2, cz], [thickness, height, ph * scale]), ([x2, height / 2, cz], [thickness, height, ph * scale])]
            for pos, size in edges:
                signature = tuple(round(v, 6) for v in [*pos, *size])
                if signature in walls:
                    continue
                walls.add(signature)
                architecture.append(obj(f"wall-{len(walls)}", "wall", pos, size, room_id, bounds))
        schemes = {"base": {"name": "白模", "objects": []}, "mid": {"name": "兼容槽", "objects": []}}
        catalog = {item["id"]: item for item in self.styles()}
        for selected in manifest["schemeMap"]:
            identifier = selected["sceneSchemeId"]
            profile = catalog[identifier]["surfaceDecorProfile"]
            overrides = {}
            for item in architecture:
                material = profile[item["type"]]
                overrides[item["id"]] = {"color": material["color"], "material": {**material, "baseColor": material["color"]}}
            schemes[identifier] = {"name": selected["name"], "objects": [], "surfaceOverrides": overrides,
                                   "styleAudit": {"status": "draft", "items": [], "screenshots": []}}
        scene = {"format": "interior-studio/1", "engineVersion": ENGINE, "name": config["name"], "revision": 1, "isSample": False,
                 "source": source, "generation": {"taskId": task_id, "sourceFileId": src["id"], "sourceSha256": src["sha256"], "page": src["page"]},
                 "drawing": {"width": src["width"], "height": src["height"], "sourceWidth": src["width"], "sourceHeight": src["height"], "origin": [0, src["height"]], "scaleX": scale, "scaleZ": scale, "status": status},
                 "recognition": {"status": "needs-review", "summary": "用户在本次底图描绘矩形房间；未进行 AI 识别", "unresolved": ["门窗洞口未描绘，需要在场景数据中核对与补齐", "相邻房间非完全共边时墙段需核查", "家具由用户在三维编辑器添加"]},
                 "architecture": architecture, "rooms": scene_rooms, "schemes": schemes, "style": manifest["schemeMap"][0]["sceneSchemeId"],
                 "cameras": [], "assumptions": ["只依据用户描绘区域构建矩形房间，未自动识别户型", "墙厚和层高使用用户参数，开口未描绘"] + ([] if cal else ["房间宽度为用户填写的估算值，不能用作施工尺寸"]),
                 "delivery": {"stage": "design-draft"}, "display": {"layout": "dual", "mode": "material"}}
        self.save_scene(task_id, scene)
        return self.assemble(task_id)

    def save_edited_scene(self, task_id, scene):
        """Annotate explicit human design additions from a saved editor project.

        Existing source bindings are still required. Newly added furniture gets
        a source-relative projection marked design-completion, never observation.
        """
        scene = copy.deepcopy(scene)
        manifest = self.manifest(task_id)
        src = manifest["primarySource"]
        drawing = scene.get("drawing", {})
        for scheme in scene.get("schemes", {}).values():
            scheme["styleAudit"] = {"status": "draft", "items": [], "screenshots": []}
            for item in scheme.get("objects", []):
                if "sourceEvidence" not in item:
                    try:
                        x, _, z = item["pos"]
                        u = (x / drawing["scaleX"] + drawing["origin"][0]) * src["width"] / drawing["width"]
                        v = (drawing["origin"][1] - z / drawing["scaleZ"]) * src["height"] / drawing["height"]
                        px = min(max(0., u), src["width"] - 1.)
                        py = min(max(0., v), src["height"] - 1.)
                    except (KeyError, TypeError, ZeroDivisionError) as error:
                        raise ValueError("编辑项目缺少有效绘图尺度") from error
                    item["sourceEvidence"] = {"sourceFileId": src["id"], "sourceSha256": src["sha256"], "pixelBounds": [px, py, 1, 1],
                                              "inferred": ["manual-user-design-addition", "design-completion", "position", "not-observed-in-plan"]}
        scene["recognition"] = {**scene.get("recognition", {}), "status": "needs-review"}
        scene["delivery"] = {"stage": "design-draft"}
        self.save_scene(task_id, scene)
        return self.assemble(task_id)

    def collect(self, task_id):
        directory, task = self._load(task_id)
        source = _ordinary(task["candidate_scene_path"], directory)
        if not source.is_file():
            raise ValueError("本机 Agent 尚未生成本次方案.scene.json，可继续任务或手动导入场景")
        if source.stat().st_size > MAX_SCENE:
            raise ValueError("生成场景超过 40 MB")
        scene = json.loads(source.read_text(encoding="utf-8-sig"))
        scene = self._expand_authoring_source(task_id, scene)
        self.save_scene(task_id, scene)
        return self.assemble(task_id)

    def _expand_authoring_source(self, task_id, scene):
        """Resolve one task-owned source reference, only at the collect boundary.

        No geometry, design, audit or provenance correction occurs here. A
        foreign bitmap continues through the original strict rejection path.
        The authoring JSON on disk remains byte-for-byte unchanged.
        """
        if not isinstance(scene, dict) or not isinstance(scene.get("source"), dict):
            raise ValueError("作者稿场景来源字段无效")
        if scene["source"].get("imageData") != PRIMARY_SOURCE_REFERENCE:
            return scene
        directory, _ = self._load(task_id)
        manifest = self.manifest(task_id)
        src = manifest["primarySource"]
        expected = {"taskId": manifest["taskId"], "sourceFileId": src["id"], "sourceSha256": src["sha256"], "page": src["page"]}
        generation = scene.get("generation")
        if (not isinstance(generation, dict) or generation != expected or type(generation.get("page")) is not int
                or scene.get("isSample") is not False or scene["source"].get("name") != src["name"]):
            raise ValueError("作者稿占位符没有准确绑定本次任务、主图名称、哈希与页码")
        original = _ordinary(src["path"], self.store.data_dir)
        if not original.is_file() or not 0 < original.stat().st_size <= MAX_SOURCE:
            raise ValueError("本次底图文件缺失或超过上限")
        raw = original.read_bytes()
        if len(raw) != src["bytes"] or hashlib.sha256(raw).hexdigest() != src["sha256"]:
            raise ValueError("本次底图已经变化，不能展开作者稿占位符")
        if src["kind"] == "image":
            bitmap, mime = raw, src["mime"]
            expected_bitmap_sha = src["sha256"]
        elif src["kind"] == "pdf":
            if (scene["source"].get("derivedFromSha256") != src["sha256"]
                    or type(scene["source"].get("page")) is not int or scene["source"].get("page") != src["page"]):
                raise ValueError("PDF 作者稿占位符缺少本次原文件哈希与选定页码")
            selected = _ordinary(directory / "selected-page.png", directory)
            if not selected.is_file() or not 0 < selected.stat().st_size <= MAX_SCENE:
                raise ValueError("本次 PDF 选定页位图缺失或超过上限")
            bitmap, mime = selected.read_bytes(), "image/png"
            expected_bitmap_sha = src["rasterSha256"]
        else:
            raise ValueError("作者稿占位符仅支持本次图片或 PDF 选页")
        if (hashlib.sha256(bitmap).hexdigest() != expected_bitmap_sha
                or _raster_info(bitmap) != (src["width"], src["height"])):
            raise ValueError("本次底图或 PDF 选页位图与已绑定内容不一致")
        result = copy.deepcopy(scene)
        result["source"]["imageData"] = f"data:{mime};base64," + base64.b64encode(bitmap).decode("ascii")
        return result

    def assemble(self, task_id, final=False):
        with self.lock:
            directory, task = self._load(task_id)
            scene = self.scene(task_id)
            self._validate_binding(self.manifest(task_id), scene)
            html = directory / (f"{'交付' if final else '草案'}-r{task['revision']:04d}-{new_id()[:8]}.html")
            args = [self.skill_root / "生成本次网页.cjs", task["config_path"], directory / task["scene_file"], html]
            if not final:
                args.append("--preview")
            self._run(args, directory)
            task.update(latest_html=html.name, status="exported" if final else "draft-preview", updated_at=now())
            atomic_json(directory / "task.json", task)
            return self.get(task_id)

    def source_file(self, task_id):
        directory, task = self._load(task_id)
        source = task["source"]
        return _ordinary(directory / "selected-page.png", directory) if source["kind"] == "pdf" else _ordinary(source["path"], self.store.data_dir)

    def output_file(self, task_id, kind):
        directory, task = self._load(task_id)
        mapping = {"scene": task.get("scene_file"), "html": task.get("latest_html"), "config": "室内三维生成配置.json"}
        name = mapping.get(kind)
        if not name:
            raise KeyError("此任务产物尚未生成")
        return _ordinary(directory / name, directory)


class TaskInput(BaseModel):
    file_id: str = Field(min_length=1, max_length=128)
    name: str = Field(default="室内三维方案", min_length=1, max_length=120)
    page: int = 1
    styles: list[str] = Field(default_factory=lambda: ["scandi"])
    height_m: float = 2.8
    wall_thickness_m: float = .15
    scale_policy: str = "estimate"
    reference_length_m: float | None = None
    points_px: list[list[float]] | None = None
    reference_ids: list[str] = Field(default_factory=list)


def register_interior(app):
    service = InteriorService(app.state.store)
    app.state.interior = service
    router = APIRouter(prefix="/api/interior")

    def call(method, *args, **kwargs):
        try:
            return method(*args, **kwargs)
        except KeyError as error:
            raise HTTPException(404, str(error.args[0])) from error
        except (ValueError, OSError, subprocess.TimeoutExpired, Image.DecompressionBombError, Image.DecompressionBombWarning) as error:
            raise HTTPException(422, str(error)[:1800]) from error

    @router.get("/capabilities")
    def capabilities():
        return service.capabilities()

    @router.get("/styles")
    def styles():
        return {"items": [{"id": x["id"], "name": x["name"], "tag": x.get("tag", ""), "palette": x.get("palette", [])} for x in call(service.styles)] +
                         [{"id": "custom", "name": "自定义风格", "tag": "上传本次参考图片", "palette": []}]}

    @router.get("/tasks")
    def tasks():
        return {"items": call(service.list)}

    @router.post("/tasks", status_code=201)
    def create(body: TaskInput):
        return call(service.create, body.model_dump())

    @router.get("/tasks/{task_id}")
    def task(task_id: str):
        return call(service.get, task_id)

    @router.get("/tasks/{task_id}/scene")
    def scene(task_id: str):
        return call(service.scene, task_id)

    @router.put("/tasks/{task_id}/scene")
    def save_scene(task_id: str, body: dict = Body(...)):
        return call(service.save_scene, task_id, body)

    @router.post("/tasks/{task_id}/collect")
    def collect(task_id: str):
        return call(service.collect, task_id)

    @router.post("/tasks/{task_id}/trace")
    def trace(task_id: str, body: dict = Body(...)):
        return call(service.trace, task_id, body)

    @router.put("/tasks/{task_id}/edited-scene")
    def edited_scene(task_id: str, body: dict = Body(...)):
        return call(service.save_edited_scene, task_id, body)

    @router.post("/tasks/{task_id}/assemble")
    def assemble(task_id: str, body: dict = Body(default={})):
        if type(body.get("final", False)) is not bool:
            raise HTTPException(422, "final 必须为布尔值")
        return call(service.assemble, task_id, body.get("final", False))

    @router.get("/tasks/{task_id}/source/file")
    def source(task_id: str):
        path = call(service.source_file, task_id)
        return FileResponse(path, headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})

    @router.get("/tasks/{task_id}/preview/file")
    def preview(task_id: str, request: Request, channel: str = ""):
        path = call(service.output_file, task_id, "html")
        # The frozen editor runs in an opaque origin, with no network or parent
        # authority. Imported scene data cannot execute script in the workbench.
        headers = {"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "sandbox allow-scripts allow-downloads allow-forms; default-src 'none'; script-src 'unsafe-inline' data: blob:; style-src 'unsafe-inline'; img-src data: blob:; font-src data:; connect-src 'none'; form-action 'none'; worker-src 'none'"}
        if not channel:
            return FileResponse(path, media_type="text/html", headers=headers)
        if not re.fullmatch(r"[a-f0-9-]{32,64}", channel):
            raise HTTPException(422, "编辑通道无效")
        # A public random correlation ID, never the API capability. The child
        # only offers its data snapshot to the real parent; it gets no tokens.
        origin = f"{request.url.scheme}://{request.url.netloc}"
        bridge = "<script>addEventListener('message',e=>{if(e.source!==parent||e.origin!==" + json.dumps(origin) + ")return;const c=" + json.dumps(channel) + ";if(e.data?.type==='interior-snapshot-request'&&e.data.channel===c&&window.FixedInterior){parent.postMessage({type:'interior-scene',channel:c,scene:FixedInterior.snapshot()}," + json.dumps(origin) + ")}});</script>"
        html = path.read_text(encoding="utf-8")
        return Response(html.replace("</body>", bridge + "</body>"), media_type="text/html", headers=headers)

    @router.get("/tasks/{task_id}/files/{kind}/file")
    def download(task_id: str, kind: str):
        path = call(service.output_file, task_id, kind)
        return FileResponse(path, filename=path.name, media_type="application/octet-stream", headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})

    app.include_router(router)
