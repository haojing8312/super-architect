"""Local document extraction, DXF rendering and portable, inert HTML reports."""
from __future__ import annotations

import base64
import html
import mimetypes
import re
import shutil
from pathlib import Path
from typing import Callable

MAX_FILE_BYTES = 80 * 1024 * 1024
TEXT_EXTENSIONS = {".txt", ".md", ".markdown", ".csv", ".json", ".py", ".js", ".ts", ".yaml", ".yml"}
KNOWLEDGE_EXTENSIONS = TEXT_EXTENSIONS | {".pdf", ".docx", ".xlsx", ".xlsm"}
IMAGE_MIMES = {"image/png", "image/jpeg", "image/gif", "image/webp", "image/bmp"}


def local_path(value: str | Path, *, directory: bool = False, root: Path | None = None) -> Path:
    """Resolve a user-selected local path; optionally enforce a workspace boundary."""
    if not isinstance(value, (str, Path)) or not str(value).strip() or "\x00" in str(value):
        raise ValueError("请提供有效的本地路径")
    path = Path(value).expanduser()
    if root is not None and not path.is_absolute():
        path = root / path
    try:
        path = path.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise ValueError("本地路径不存在或无法读取") from exc
    if root is not None and not path.is_relative_to(root.resolve()):
        raise ValueError("路径必须位于所选工作区内")
    if directory:
        if not path.is_dir():
            raise ValueError("该路径不是文件夹")
    else:
        if not path.is_file():
            raise ValueError("该路径不是普通文件")
        if path.stat().st_size > MAX_FILE_BYTES:
            raise ValueError("文件超过 80 MB 本地导入上限")
    return path


def safe_name(value: str) -> str:
    # Handle both Windows and POSIX uploads regardless of the current host.
    name = value.replace("\\", "/").split("/")[-1]
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip(" .")
    if not name or name in {".", ".."}:
        raise ValueError("无效文件名")
    return name[:180]


def file_type(path: Path) -> str:
    return mimetypes.guess_type(path.name)[0] or "application/octet-stream"


def extract_text(path: Path) -> str:
    extension = path.suffix.lower()
    if extension not in KNOWLEDGE_EXTENSIONS:
        raise ValueError("知识库支持 TXT、Markdown、PDF、DOCX、XLSX 及常见文本文件")
    if extension in TEXT_EXTENSIONS:
        raw = path.read_bytes()
        text = None
        for encoding in ("utf-8-sig", "utf-16" if raw.startswith((b"\xff\xfe", b"\xfe\xff")) else "gb18030"):
            try:
                text = raw.decode(encoding)
                break
            except UnicodeDecodeError:
                continue
        if text is None:
            raise ValueError("无法识别文本编码；请转为 UTF-8")
        if "\x00" in text:
            raise ValueError("文件包含二进制内容，不能作为文本导入")
    elif extension == ".pdf":
        from pypdf import PdfReader
        reader = PdfReader(str(path))
        if reader.is_encrypted and not reader.decrypt(""):
            raise ValueError("PDF 已加密，请先解除密码")
        pages = [page.extract_text() or "" for page in reader.pages]
        text = "\n\n".join(f"[第 {index + 1} 页]\n{content}" for index, content in enumerate(pages))
        if not any(content.strip() for content in pages):
            raise ValueError("PDF 没有可提取文本；扫描件需先执行 OCR")
    elif extension == ".docx":
        from docx import Document
        from docx.table import Table
        from docx.text.paragraph import Paragraph
        doc = Document(str(path))
        parts = []
        for node in doc.element.body.iterchildren():
            if node.tag.endswith("}p"):
                parts.append(Paragraph(node, doc).text)
            elif node.tag.endswith("}tbl"):
                parts.extend(" | ".join(cell.text for cell in row.cells) for row in Table(node, doc).rows)
        text = "\n".join(parts)
    else:
        from openpyxl import load_workbook
        workbook = load_workbook(path, read_only=True, data_only=False, keep_links=False)
        try:
            parts = []
            for sheet in workbook:
                parts.append(f"[工作表：{sheet.title}]")
                for row in sheet.iter_rows(values_only=True):
                    if any(value is not None for value in row):
                        parts.append(" | ".join("" if value is None else str(value) for value in row))
            text = "\n".join(parts)
        finally:
            workbook.close()
    text = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not text:
        raise ValueError("文件没有可提取内容")
    if len(text) > 15_000_000:
        raise ValueError("提取文本过大，请拆分文档后导入")
    return text


def text_chunks(text: str, size: int = 500, overlap: int = 80) -> list[str]:
    """Source-compatible paragraph parents and 3-sentence/step-2 child chunks."""
    from .chunking import build_knowledge_chunks
    return [chunk.content for chunk in build_knowledge_chunks(
        text, parent_max_chars=size, parent_overlap_chars=overlap)]


def query_terms(query: str) -> list[str]:
    """English words plus Chinese bigrams, retaining the exact phrase for ranking."""
    query = query.strip().lower()
    terms = [query]
    terms.extend(re.findall(r"[a-z0-9_]+|[\u3400-\u9fff]+", query))
    for word in list(terms):
        if re.fullmatch(r"[\u3400-\u9fff]{3,}", word):
            terms.extend(word[i:i + 2] for i in range(len(word) - 1))
    return list(dict.fromkeys(term for term in terms if term))[:30]


def preview_dxf(path: Path) -> dict:
    if path.suffix.lower() != ".dxf":
        raise ValueError("当前可预览 DXF；DWG 需要宿主 CAD SDK，请先另存为 DXF")
    import ezdxf
    from ezdxf.addons.drawing import Frontend, RenderContext, config, layout, svg
    document = ezdxf.readfile(str(path))
    modelspace = document.modelspace()
    count = len(modelspace)
    if count == 0:
        raise ValueError("DXF 模型空间为空")
    if count > 150_000:
        raise ValueError("DXF 超过 150000 个实体，请拆分后预览")
    backend = svg.SVGBackend()
    configuration = config.Configuration(background_policy=config.BackgroundPolicy.WHITE)
    Frontend(RenderContext(document), backend, config=configuration).draw_layout(modelspace)
    rendered = backend.get_string(layout.Page(0, 0))
    types: dict[str, int] = {}
    for entity in modelspace:
        types[entity.dxftype()] = types.get(entity.dxftype(), 0) + 1
    return {
        "name": path.name, "path": str(path), "svg": rendered, "entities": count,
        "entity_types": types,
        "layers": [{"name": layer.dxf.name, "color": layer.dxf.color, "visible": not layer.is_off()} for layer in document.layers],
        "format": document.dxfversion,
    }


def build_report(destination: Path, title: str, sections: list[dict], resolve_asset: Callable[[str], Path], theme: str = "minimal") -> dict:
    """Escape all prose and embed raster images; one HTML is enough for sharing.

    Source assets are also copied beside the HTML for editing or archiving. No
    source path or active source HTML is placed into the output document.
    """
    destination.parent.mkdir(parents=True, exist_ok=True)
    assets_dir = destination.parent / "assets"
    assets_dir.mkdir(exist_ok=True)
    assets: dict[str, tuple[Path, str]] = {}
    asset_bytes = 0

    def image_fragment(reference: str, alt: str) -> str:
        nonlocal asset_bytes
        if reference not in assets:
            source = local_path(resolve_asset(reference))
            mime = file_type(source)
            if mime not in IMAGE_MIMES:
                raise ValueError("报告图片仅支持 PNG、JPEG、GIF、WebP、BMP；SVG 或 HTML 不能作为活动内容嵌入")
            asset_bytes += source.stat().st_size
            if asset_bytes > 30 * 1024 * 1024:
                raise ValueError("报告图片总量超过 30 MB，请压缩后导出")
            copied = assets_dir / f"{len(assets) + 1:03d}-{safe_name(source.name)}"
            shutil.copy2(source, copied)
            encoded = base64.b64encode(copied.read_bytes()).decode("ascii")
            assets[reference] = (copied, f"data:{mime};base64,{encoded}")
        return f'<figure><img src="{assets[reference][1]}" alt="{html.escape(alt, quote=True)}"><figcaption>{html.escape(alt)}</figcaption></figure>'

    def safe_text(value: str) -> str:
        fragments = []
        position = 0
        for match in re.finditer(r"!\[([^\]]*)\]\(([^\n]+?)\)", value):
            fragments.append(f'<div class="prose">{html.escape(value[position:match.start()])}</div>')
            fragments.append(image_fragment(match.group(2).strip().strip('"'), match.group(1)))
            position = match.end()
        fragments.append(f'<div class="prose">{html.escape(value[position:])}</div>')
        return "".join(fragments)

    rendered_sections = []
    for section in sections:
        body = safe_text(str(section.get("content", "")))
        for asset in section.get("assets", []):
            if isinstance(asset, str):
                body += image_fragment(asset, "作品图片")
            elif isinstance(asset, dict):
                body += image_fragment(str(asset.get("path") or asset.get("url") or ""), str(asset.get("name") or "作品图片"))
            else:
                raise ValueError("报告素材必须为图片路径或对象")
        rendered_sections.append(f'<section><h2>{html.escape(str(section.get("title", "")))}</h2>{body}</section>')
    result = '<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">'
    result += '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; img-src data:; style-src \'unsafe-inline\'; base-uri \'none\'; form-action \'none\'">'
    result += f'<title>{html.escape(title)}</title><style>body{{margin:0;background:#f4f2ed;color:#222;font-family:Segoe UI,Microsoft YaHei,sans-serif}}main{{max-width:1000px;margin:auto;padding:64px 36px}}header{{border-bottom:2px solid #222;padding-bottom:32px}}h1{{font-size:38px;line-height:1.25}}h2{{font-size:25px}}section{{margin-top:40px}}.prose{{white-space:pre-wrap;overflow-wrap:anywhere;line-height:1.9}}figure{{margin:24px 0}}img{{max-width:100%;height:auto}}figcaption{{font-size:13px;color:#666;margin-top:8px}}footer{{margin-top:64px;font-size:13px;color:#666}}@media print{{body{{background:white}}main{{padding:24px}}section,figure{{break-inside:avoid}}}}</style></head><body>'
    styles = {"minimal": "body{background:#f7f7f7;color:#303036}header{border-color:#2f3c48}", "dark": "body{background:#20262b;color:#e6ebee}header{border-color:#8ca4b8}figcaption,footer{color:#97a5b0}", "warm": "body{background:#f2ece2;color:#4b3f34}header{border-color:#a8785e}figcaption,footer{color:#947b68}"}
    result = result.replace("</style>", styles.get(theme, styles["minimal"]) + "</style>")
    result += f'<main><header><p>超级建筑师 · 作品汇报</p><h1>{html.escape(title)}</h1></header>{"".join(rendered_sections)}<footer>本文件可独立打开与分享；内容由工作台本地导出。</footer></main></body></html>'
    destination.write_text(result, encoding="utf-8")
    return {"asset_count": len(assets), "size": destination.stat().st_size, "portable": True}
