"""Local, reproducible document exports with native editable Office objects.

This is the deterministic export path. AI authoring continues through the
existing local agent runtime; no remote model or identity service is used.
"""
from __future__ import annotations

import base64
import hashlib
import html
import io
import json
import re
import secrets
import shutil
import unicodedata
from pathlib import Path

from fastapi import APIRouter, Body, HTTPException
from fastapi.responses import FileResponse
from PIL import Image, ImageOps

from .store import now

THEMES = {
    "minimal": ["#F4F3EF", "#242C2B", "#687B69"],
    "dark": ["#18232C", "#F5F1E8", "#DCAD72"],
    "warm": ["#F2E9DB", "#433D36", "#A66845"],
    "blueprint": ["#EDF2F7", "#233C52", "#357AA3"],
}
TRANSITIONS = {"slide", "reveal", "zoom", "curtain", "flip", "focus", "vertical", "gallery", "light", "glitch", "fade", "none"}
FORMATS = {"html", "pptx", "docx", "xlsx", "json"}
MIMES = {"html": "text/html", "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation", "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document", "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "json": "application/json"}
ASSET_REFERENCE = re.compile(r"/api/(?:resources/[a-f0-9]{32}/file|files/[a-f0-9]{32})")


def _text(value, name, maximum=40000):
    if not isinstance(value, str) or len(value) > maximum:
        raise ValueError(f"{name}必须是长度不超过 {maximum} 的文字")
    return value


def normalize_spec(body: dict) -> dict:
    if not isinstance(body, dict):
        raise ValueError("请提供汇报配置")
    title = _text(body.get("title", ""), "标题", 300).strip()
    if not title:
        raise ValueError("请填写项目标题")
    theme, mode = body.get("theme", "minimal"), body.get("mode", "slides")
    if not isinstance(theme,str) or not isinstance(mode,str) or theme not in THEMES or mode not in {"slides", "scroll"}:
        raise ValueError("汇报样式或浏览模式无效")
    transitions = body.get("transitions") or [body.get("transition", "slide")]
    if not isinstance(transitions, list) or not transitions or len(transitions) > 12 or any(not isinstance(v,str) or v not in TRANSITIONS for v in transitions):
        raise ValueError("汇报动效无效")
    palette = body.get("palette", THEMES[theme])
    if not isinstance(palette, list) or len(palette) != 3 or any(not isinstance(v, str) or not re.fullmatch(r"#[a-fA-F0-9]{6}", v) for v in palette):
        raise ValueError("配色必须为三个十六进制颜色")
    sections = body.get("sections")
    if not isinstance(sections, list) or not 1 <= len(sections) <= 60:
        raise ValueError("汇报需要 1 至 60 个章节")
    cleaned = []
    total_text = 0
    for entry in sections:
        if not isinstance(entry, dict):
            raise ValueError("章节必须为对象")
        assets = entry.get("assets", [])
        if not isinstance(assets, list) or len(assets) > 40 or any(not isinstance(v, str) or not ASSET_REFERENCE.fullmatch(v) for v in assets):
            raise ValueError("章节图片必须来自当前本地资料库或已导入附件")
        layout = entry.get("layout", "split")
        if not isinstance(layout,str) or layout not in {"split", "gallery", "text"}:
            raise ValueError("章节版式无效")
        sources = entry.get("sources", [])
        if not isinstance(sources, list) or len(sources) > 30:
            raise ValueError("每个章节最多 30 条资料引用")
        cleaned_sources = []
        for source in sources:
            if not isinstance(source, dict):
                raise ValueError("资料引用必须为对象")
            citation = {"name": _text(source.get("name", "资料引用"), "引用名称", 300), "content": _text(source.get("content", ""), "引用摘录", 20000)}
            for key in ("source_id", "chunk_id", "collection_id", "label", "page"):
                if key in source:
                    value = source[key]
                    if isinstance(value, int) and not isinstance(value, bool):
                        citation[key] = value
                    else:
                        citation[key] = _text(value, "引用定位", 300)
            cleaned_sources.append(citation)
        section = {"title": _text(entry.get("title", ""), "章节标题", 300), "content": _text(entry.get("content", ""), "章节内容"), "notes": _text(entry.get("notes", ""), "演讲备注", 20000), "layout": layout, "assets": list(dict.fromkeys(assets)), "sources": cleaned_sources}
        total_text += sum(len(section[k]) for k in ("title", "content", "notes")) + sum(len(s["content"]) for s in cleaned_sources)
        cleaned.append(section)
    if total_text > 500000:
        raise ValueError("汇报文字总量超过 50 万字，请拆分导出")
    formats = body.get("formats", ["html", "pptx"])
    if not isinstance(formats, list) or not formats or any(not isinstance(v,str) or v not in FORMATS for v in formats):
        raise ValueError("请选择 HTML、PPTX、Word、Excel 或配置文件")
    origin = body.get("origin", {})
    if not isinstance(origin, dict):
        raise ValueError("资料来源信息无效")
    origin = {k: _text(v, "资料来源标识", 300) for k,v in origin.items() if k in {"canvas_id", "knowledge_id"}}
    return {"schema_version": 1, "title": title, "subtitle": _text(body.get("subtitle", ""), "副标题", 1000), "theme": theme, "palette": palette, "mode": mode, "transitions": list(dict.fromkeys(transitions)), "sections": cleaned, "formats": list(dict.fromkeys(formats)), "origin": origin}


def _asset_path(store, reference: str) -> Path:
    if not ASSET_REFERENCE.fullmatch(reference):
        raise ValueError("图片引用无效")
    parts = reference.split("/")
    item = store.get_resource(parts[3]) if parts[2] == "resources" else store.get_file(parts[3])
    path = store._owned_path(item["path"])
    if not path.is_file() or path.is_symlink() or path.stat().st_size > 30 * 1024 * 1024:
        raise ValueError("图片不存在、过大或不是工作台持有的普通文件")
    return path


def resolve_images(store, spec: dict) -> dict:
    images, total_bytes = {}, 0
    for section in spec["sections"]:
        for reference in section["assets"]:
            if reference in images:
                continue
            path = _asset_path(store, reference)
            total_bytes += path.stat().st_size
            if total_bytes > 30 * 1024 * 1024:
                raise ValueError("汇报图片总量超过 30 MB，请先压缩")
            if path.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}:
                raise ValueError("汇报图片支持 PNG、JPEG、WebP、GIF、BMP；活动 SVG/HTML 不可嵌入")
            try:
                decoded = Image.open(path)
            except (OSError, Image.DecompressionBombError) as exc:
                raise ValueError('图片无法解析或像素过大，请先转换并缩小图片') from exc
            with decoded as source:
                if source.width * source.height > 30000000:
                    raise ValueError("图片像素超过 3000 万，请先缩小")
                picture = ImageOps.exif_transpose(source).convert("RGBA")
                # Preserve proportions; the export copy, never the source, is resized.
                picture.thumbnail((3200, 3200))
                stream = io.BytesIO()
                picture.save(stream, "PNG")
            encoded = stream.getvalue()
            images[reference] = {"name": path.name, "bytes": encoded, "width": picture.width, "height": picture.height, "sha256": hashlib.sha256(encoded).hexdigest(), "uri": "data:image/png;base64," + base64.b64encode(encoded).decode("ascii")}
    if sum(len(item["bytes"]) for item in images.values()) > 60 * 1024 * 1024:
        raise ValueError("图片转换后总量超过 60 MB，请先压缩")
    return images


def build_html(spec: dict, images: dict, destination: Path) -> dict:
    """A standalone, offline deck with contained pictures and explicit layouts."""
    esc = lambda value: html.escape(value, quote=True)
    blocks = [f'<section class="page cover" data-motion="{spec["transitions"][0]}"><p class="kicker">作品汇报 / PROJECT PRESENTATION</p><h1>{esc(spec["title"])}</h1><p class="subtitle">{esc(spec["subtitle"])}</p><ol class="contents">' + "".join(f'<li><button data-go="{i+1}">{esc(s["title"])}</button></li>' for i, s in enumerate(spec["sections"])) + '</ol></section>']
    for index, section in enumerate(spec["sections"]):
        pictures = "".join(f'<figure><button class="picture" aria-label="放大图片 {j+1}"><img width="{images[r]["width"]}" height="{images[r]["height"]}" src="{images[r]["uri"]}" alt="{esc(section["title"])} · 素材 {j+1}"></button><figcaption>素材 {j+1} · {images[r]["width"]} × {images[r]["height"]}</figcaption></figure>' for j, r in enumerate(section["assets"]))
        citations = "".join(f'<details class="source"><summary>{esc(s["name"])}</summary><div class="prose">{esc(s["content"])}</div></details>' for s in section["sources"])
        blocks.append(f'<section class="page layout-{section["layout"]}" data-motion="{spec["transitions"][index % len(spec["transitions"])]}"><header><p class="kicker">{index+1:02d} / {len(spec["sections"]):02d}</p><h2>{esc(section["title"])}</h2></header><div class="composition"><div class="copy"><div class="prose">{esc(section["content"])}</div>{citations}</div><div class="media count-{len(section["assets"])}">{pictures}</div></div><aside class="speaker-notes" hidden><strong>演讲备注</strong><div class="prose">{esc(section["notes"])}</div></aside></section>')
    css = (_HTML_CSS + _GALLERY_CSS).replace("__BG__", spec["palette"][0]).replace("__INK__", spec["palette"][1]).replace("__ACCENT__", spec["palette"][2])
    script = _HTML_JS
    digest = base64.b64encode(hashlib.sha256(script.encode()).digest()).decode()
    csp = f"default-src 'none'; img-src data:; style-src 'unsafe-inline'; script-src 'sha256-{digest}'; base-uri 'none'; form-action 'none'; connect-src 'none'; object-src 'none'"
    result = f'<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta http-equiv="Content-Security-Policy" content="{csp}"><title>{esc(spec["title"])}</title><style>{css}</style></head><body data-mode="{spec["mode"]}"><nav class="controls" aria-label="汇报操作"><button id="prev" aria-label="上一页">←</button><span id="page-count" aria-live="polite"></span><button id="next" aria-label="下一页">→</button><button id="notes" aria-pressed="false">演讲备注</button><button id="fullscreen">全屏</button><button id="print">打印 / PDF</button></nav><main>{"".join(blocks)}</main><dialog id="lightbox"><button id="close-lightbox" aria-label="关闭图片">关闭 ×</button><img alt="放大素材"></dialog><script>{script}</script></body></html>'
    destination.write_text(result, encoding="utf-8")
    return {"pages": len(blocks), "portable": True, "image_count": len(images), "script_hash": "sha256-" + digest}


_HTML_CSS = """
:root{--bg:__BG__;--ink:__INK__;--accent:__ACCENT__;font-family:'Segoe UI','Microsoft YaHei',sans-serif;color:var(--ink);background:var(--bg)}*{box-sizing:border-box}body{margin:0}button{font:inherit;color:inherit;cursor:pointer;background:transparent;border:1px solid currentColor;border-radius:4px;padding:.4rem .7rem}button:focus-visible{outline:3px solid var(--accent);outline-offset:4px}.controls{position:fixed;bottom:16px;left:50%;transform:translateX(-50%);display:flex;align-items:center;gap:10px;background:var(--bg);border:1px solid var(--accent);padding:10px;z-index:20;border-radius:8px;white-space:nowrap}.page{min-height:100vh;padding:64px 7vw 96px;border-bottom:1px solid var(--accent);overflow:auto}.cover{display:flex;flex-direction:column;justify-content:center}.kicker{font-size:12px;letter-spacing:.16em;color:var(--accent)}h1{font-size:clamp(36px,6vw,80px);line-height:1.12;max-width:1100px;margin:14px 0 26px;overflow-wrap:anywhere}h2{font-size:clamp(26px,3.5vw,52px);line-height:1.2;margin:12px 0 28px;overflow-wrap:anywhere}.subtitle{font-size:20px;white-space:pre-wrap}.contents{padding-left:20px;columns:2;max-width:850px;line-height:2}.contents button{border:0;text-align:left}.composition{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1.35fr);gap:4vw;align-items:start}.prose{white-space:pre-wrap;font-size:clamp(15px,1.5vw,21px);line-height:1.7;overflow-wrap:anywhere}.media{display:grid;gap:20px;grid-template-columns:repeat(auto-fit,minmax(min(240px,100%),1fr))}figure{margin:0;min-width:0}.picture{border:0;padding:0;width:100%;display:flex;align-items:center;justify-content:center}.picture img{width:100%;height:auto;max-height:65vh;object-fit:contain}figcaption{font-size:11px;opacity:.7;margin-top:8px}.source{font-size:13px;border-top:1px solid var(--accent);margin-top:24px;padding-top:14px}.source .prose{font-size:14px;margin-top:10px}.layout-gallery .composition{display:flex;flex-direction:column}.layout-gallery .media{width:100%;grid-template-columns:repeat(2,minmax(0,1fr))}.layout-text .composition{display:block;max-width:1000px}.layout-text .media{margin-top:30px;grid-template-columns:repeat(3,minmax(0,1fr))}.speaker-notes{margin-top:30px;border-left:3px solid var(--accent);padding:14px 20px}.speaker-notes .prose{font-size:15px;margin-top:8px}body[data-mode=slides] .page{height:100vh;display:none}body[data-mode=slides] .page.active{display:block}body[data-mode=slides] .cover.active{display:flex}dialog{background:var(--bg);color:var(--ink);border:0;padding:40px;max-width:95vw;max-height:95vh}dialog::backdrop{background:#000c}dialog img{display:block;max-width:85vw;max-height:80vh;object-fit:contain}#close-lightbox{position:absolute;right:8px;top:8px}.page.active[data-motion=slide]{animation:slide .55s ease-out}.page.active[data-motion=reveal] .media{animation:reveal .65s ease-out}.page.active[data-motion=zoom] .composition{animation:zoom .6s ease-out}.page.active[data-motion=curtain]{animation:curtain .65s ease-in-out}.page.active[data-motion=flip]{animation:flip .65s ease-out}.page.active[data-motion=focus] .media{animation:focus .65s ease-out}.page.active[data-motion=vertical]{animation:vertical .55s ease-out}.page.active[data-motion=gallery] figure{animation:slide .7s ease-out}.page.active[data-motion=gallery] figure:nth-child(even){animation-name:vertical}.page.active[data-motion=light] .media{animation:light .6s ease-out}.page.active[data-motion=glitch] .media{animation:glitch .3s steps(2)}.page.active[data-motion=fade] .composition{animation:fade .6s ease-out}@keyframes slide{from{transform:translateX(5vw)}to{transform:translateX(0)}}@keyframes reveal{from{clip-path:inset(0 100% 0 0)}to{clip-path:inset(0)}}@keyframes zoom{from{transform:scale(.94)}to{transform:scale(1)}}@keyframes curtain{from{clip-path:inset(0 48%)}to{clip-path:inset(0)}}@keyframes flip{from{transform:perspective(1500px) rotateY(-12deg)}to{transform:perspective(1500px) rotateY(0)}}@keyframes focus{from{clip-path:circle(12% at 50% 50%)}to{clip-path:circle(100% at 50% 50%)}}@keyframes vertical{from{transform:translateY(5vh)}to{transform:translateY(0)}}@keyframes light{from{filter:brightness(1.8)}to{filter:brightness(1)}}@keyframes glitch{0%,40%{transform:translateX(-8px)}20%,60%{transform:translateX(8px)}100%{transform:translateX(0)}}@keyframes fade{from{opacity:0}to{opacity:1}}@media(max-width:760px){.page{padding:35px 22px 120px}.composition{display:flex;flex-direction:column;gap:30px}.media{width:100%}.contents{columns:1}.controls{max-width:98vw;gap:5px;font-size:12px;flex-wrap:wrap;justify-content:center}.layout-text .media{grid-template-columns:1fr}}@media(prefers-reduced-motion:reduce){*{animation:none!important;scroll-behavior:auto!important}}@media print{body[data-mode=slides] .page{display:block!important;height:auto;min-height:0;break-before:page;padding:24px}.controls,.speaker-notes{display:none!important}.picture img{max-height:500px}.page{break-after:page}*{animation:none!important}}
"""
_HTML_JS = """'use strict';
const pages=[...document.querySelectorAll('.page')],slides=document.body.dataset.mode==='slides';let current=0;
function go(n){current=Math.max(0,Math.min(pages.length-1,n));pages.forEach((p,i)=>{p.classList.toggle('active',i===current);if(slides)p.setAttribute('aria-hidden',String(i!==current));});document.querySelector('#page-count').textContent=(current+1)+' / '+pages.length;document.querySelector('#prev').disabled=current===0;document.querySelector('#next').disabled=current===pages.length-1;if(!slides)pages[current].scrollIntoView({behavior:matchMedia('(prefers-reduced-motion:reduce)').matches?'instant':'smooth'});}
document.querySelector('#prev').onclick=()=>go(current-1);document.querySelector('#next').onclick=()=>go(current+1);document.querySelectorAll('[data-go]').forEach(b=>b.onclick=()=>go(Number(b.dataset.go)));document.querySelector('#notes').onclick=e=>{const enabled=e.currentTarget.getAttribute('aria-pressed')!=='true';e.currentTarget.setAttribute('aria-pressed',String(enabled));document.querySelectorAll('.speaker-notes').forEach(n=>n.hidden=!enabled);};document.querySelector('#print').onclick=()=>print();document.querySelector('#fullscreen').onclick=()=>{if(document.fullscreenElement)document.exitFullscreen();else document.documentElement.requestFullscreen?.().catch(()=>{});};document.addEventListener('keydown',e=>{if(['INPUT','TEXTAREA','BUTTON','SUMMARY'].includes(e.target.tagName)||document.querySelector('#lightbox').open)return;if(['ArrowRight','PageDown',' '].includes(e.key)){e.preventDefault();go(current+1);}if(['ArrowLeft','PageUp'].includes(e.key)){e.preventDefault();go(current-1);}if(e.key==='Home')go(0);if(e.key==='End')go(pages.length-1);});const lightbox=document.querySelector('#lightbox');document.querySelectorAll('.picture').forEach(b=>b.onclick=()=>{lightbox.querySelector('img').src=b.querySelector('img').src;lightbox.showModal();});document.querySelector('#close-lightbox').onclick=()=>lightbox.close();lightbox.addEventListener('click',e=>{if(e.target===lightbox)lightbox.close();});if(!slides){const observer=new IntersectionObserver(entries=>{entries.forEach(e=>{if(e.isIntersecting){current=pages.indexOf(e.target);pages.forEach(p=>p.classList.toggle('active',p===e.target));document.querySelector('#page-count').textContent=(current+1)+' / '+pages.length;document.querySelector('#prev').disabled=current===0;document.querySelector('#next').disabled=current===pages.length-1;}})},{threshold:.25});pages.forEach(p=>observer.observe(p));}go(0);
"""

_GALLERY_CSS = """.layout-gallery .media.count-1{grid-template-columns:1fr}.layout-gallery .media.count-3{grid-template-columns:2.2fr 1fr 1fr}.layout-gallery .picture{height:23vh}.layout-gallery .count-1 .picture,.layout-gallery .count-2 .picture,.layout-gallery .count-3 .picture{height:42vh}.layout-gallery .picture img{width:100%;height:100%;object-fit:contain}@media(max-width:760px){.layout-gallery .media.count-3{grid-template-columns:1fr}.layout-gallery .picture{height:32vh}}@media print{.layout-gallery .picture{height:220px}.layout-gallery .count-1 .picture,.layout-gallery .count-2 .picture,.layout-gallery .count-3 .picture{height:300px}}"""


def _segments(content: str, maximum=700):
    """Never truncate text to fit a slide; paginate at paragraph boundaries."""
    result, remaining = [], content
    while len(remaining) > maximum:
        candidates = [remaining.rfind(mark, 0, maximum) + 1 for mark in ("\n", "。", "！", "？", ". ", "; ", " ")]
        boundary = max(candidates)
        if boundary < maximum // 2:
            boundary = maximum
        result.append(remaining[:boundary]); remaining = remaining[boundary:]
    if remaining or not result:
        result.append(remaining)
    return result


def _slide_text_pages(content: str, columns: int, rows: int) -> list[str]:
    """Conservatively account for explicit newlines and wide CJK characters.

    A character-count-only page budget clips short paragraphs or long Chinese
    prose. These page boundaries preserve every original character, and the
    native text box still performs the actual font-based wrapping.
    """
    pages, pending, width, line_count = [], "", 0.0, 1.0
    for char in content:
        char_width = 1.0 if unicodedata.east_asian_width(char) in {'W','F'} else .65
        next_line = line_count + 1.3 if char == '\n' else line_count + 1 if width + char_width > columns else line_count
        if next_line > rows and pending:
            pages.append(pending); pending = ''; width = 0; line_count = 1
            next_line = 1.3 if char == '\n' else 1
        pending += char
        if char == '\n': width = 0
        elif next_line > line_count: width = char_width
        else: width += char_width
        line_count = next_line
    if pending or not pages: pages.append(pending)
    return pages


def build_pptx(spec: dict, images: dict, destination: Path) -> dict:
    from pptx import Presentation
    from pptx.dml.color import RGBColor
    from pptx.enum.shapes import MSO_SHAPE
    from pptx.util import Inches, Pt
    deck = Presentation()
    deck.slide_width, deck.slide_height = Inches(13.333), Inches(7.5)
    bg, ink, accent = [RGBColor.from_string(c[1:]) for c in spec["palette"]]
    deck.core_properties.title = spec["title"]
    deck.core_properties.subject = "建筑作品汇报：原生文字、图片与图形，可在 PowerPoint 中编辑"

    def textbox(slide, value, x, y, w, h, size=22, color=ink, bold=False):
        shape = slide.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h))
        shape.text_frame.word_wrap = True
        shape.text_frame.margin_left = shape.text_frame.margin_right = 0
        shape.text_frame.margin_top = shape.text_frame.margin_bottom = 0
        for i, line in enumerate(value.split("\n")):
            p = shape.text_frame.paragraphs[0] if i == 0 else shape.text_frame.add_paragraph()
            p.text, p.font.size, p.font.name, p.font.bold, p.font.color.rgb = line, Pt(size), "Microsoft YaHei", bold, color
            p.space_after = Pt(0)
        return shape

    def page(title, label):
        slide = deck.slides.add_slide(deck.slide_layouts[6])
        slide.background.fill.solid()
        slide.background.fill.fore_color.rgb = bg
        rule = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, Inches(.55), Inches(.48), Inches(.08), Inches(.25))
        rule.fill.solid(); rule.fill.fore_color.rgb = accent; rule.line.fill.background()
        textbox(slide, label, .8, .46, 11.8, .28, 11, accent)
        title_size = 12 if len(title)>170 else 20 if len(title)>80 else 29 if len(title)>40 else 36
        textbox(slide, title, .75, .95, 11.9, 1.15, title_size, ink, True)
        textbox(slide, spec["title"], .75, 7.06, 11.4, .24, 10, accent)
        textbox(slide, str(len(deck.slides)), 12.0, 7.02, .55, .3, 11, accent)
        return slide

    def picture(slide, reference, x, y, w, h):
        source = images[reference]
        ratio = min(w/source["width"], h/source["height"])
        width, height = source["width"]*ratio, source["height"]*ratio
        shape = slide.shapes.add_picture(io.BytesIO(source["bytes"]), Inches(x+(w-width)/2), Inches(y+(h-height)/2), Inches(width), Inches(height))
        shape.name = "项目素材 " + source["sha256"][:12]

    cover = page(spec["title"], "PROJECT PRESENTATION / 作品汇报")
    subtitle_parts = _slide_text_pages(spec["subtitle"], 40, 11)
    textbox(cover, subtitle_parts[0], .75, 2.6, 11.7, 3.8, 20)
    for part in subtitle_parts[1:]:
        slide = page("项目说明", "PROJECT BRIEF")
        textbox(slide, part, .85, 2.18, 11.5, 4.4, 20)
    # Agenda is paginated too; a long chapter list never overflows the cover.
    for part in _slide_text_pages("\n".join(f"{i+1:02d}  {s['title']}" for i, s in enumerate(spec["sections"])), 38, 12):
        slide = page("汇报目录", "CONTENTS")
        textbox(slide, part, .85, 2.05, 11.5, 4.7, 21)
    cover.notes_slide.notes_text_frame.text = spec["subtitle"]
    editable_images = 0
    for i, section in enumerate(spec["sections"]):
        columns, rows = (20, 13) if section['layout']=='split' and section['assets'] else (48, 3) if section['assets'] else (38, 12)
        text_parts = _slide_text_pages(section["content"], columns, rows)
        image_parts = [section["assets"][j:j+4] for j in range(0, len(section["assets"]), 4)] or [[]]
        page_count = max(len(text_parts), len(image_parts))
        for p in range(page_count):
            slide = page(section["title"] + (f" · {p+1}/{page_count}" if page_count > 1 else ""), f"SECTION {i+1:02d} / {section['layout'].upper()}")
            prose = text_parts[p] if p < len(text_parts) else ""
            assets = image_parts[p] if p < len(image_parts) else []
            if section["layout"] == "split" and assets:
                body_shape = textbox(slide, prose, .85, 2.18, 4.5, 4.4, 18)
                frames = [(5.8, 2.15, 6.65, 4.4)] if len(assets) == 1 else [(5.8+(j%2)*3.4, 2.15+(j//2)*2.25, 3.15, 2.08) for j in range(len(assets))]
            elif assets:
                body_shape = textbox(slide, prose, .85, 2.0, 11.6, 1.1, 16)
                if len(assets)==1:
                    frames=[(.85,3.25,11.6,3.45)]
                elif len(assets)==2:
                    frames=[(.85+j*5.9,3.25,5.55,3.45) for j in range(2)]
                elif len(assets)==3:
                    frames=[(.85,3.25,6.3,3.45),(7.5,3.25,2.35,3.45),(10.1,3.25,2.35,3.45)]
                else:
                    frames=[(.85+(j%2)*5.9,3.25+(j//2)*1.8,5.55,1.6) for j in range(4)]
            else:
                body_shape = textbox(slide, prose, .85, 2.2, 11.5, 4.5, 20)
                frames = []
            body_shape.name = '章节正文'
            for reference, frame in zip(assets, frames):
                picture(slide, reference, *frame); editable_images += 1
            references = "\n\n".join(s["name"] + "\n" + s["content"] for s in section["sources"])
            slide.notes_slide.notes_text_frame.text = section["notes"] + ("\n\n资料引用：\n"+references if references else "")
            # Standard OOXML transitions; objects stay native and editable.
            from lxml import etree
            transition = etree.Element("{http://schemas.openxmlformats.org/presentationml/2006/main}transition", spd="med")
            motion = spec["transitions"][i % len(spec["transitions"])]
            if motion != "none":
                effect = "push" if motion in {"slide", "gallery", "vertical"} else "wipe" if motion in {"reveal", "curtain"} else "zoom" if motion in {"zoom", "focus"} else "fade"
                child = etree.SubElement(transition, "{http://schemas.openxmlformats.org/presentationml/2006/main}"+effect)
                if effect in {"push", "wipe"}: child.set("dir", "u" if motion == "vertical" else "l")
                if effect == "zoom": child.set("dir", "in")
                existing = slide._element.find("{http://schemas.openxmlformats.org/presentationml/2006/main}clrMapOvr")
                if existing is not None: existing.addnext(transition)
                else: slide._element.append(transition)
        # Citation body stays in notes; Word/Excel include it as visible text.
    if len(deck.slides) > 1000:
        raise ValueError("幻灯片超过 1000 页，请拆分导出")
    deck.save(destination)
    return {"pages": len(deck.slides), "editable_text": True, "editable_images": editable_images, "native_shapes": True, "speaker_notes": True, "aspect_ratio": "16:9"}


def build_docx(spec: dict, images: dict, destination: Path) -> dict:
    from docx import Document
    from docx.shared import Inches
    document = Document()
    document.core_properties.title = spec["title"]
    document.add_heading(spec["title"], 0)
    document.add_paragraph(spec["subtitle"])
    for section in spec["sections"]:
        document.add_heading(section["title"], 1)
        for line in section["content"].split("\n"):
            document.add_paragraph(line)
        for reference in section["assets"]:
            source = images[reference]
            ratio = min(6/source["width"], 6/source["height"])
            document.add_picture(io.BytesIO(source["bytes"]), width=Inches(source["width"]*ratio), height=Inches(source["height"]*ratio))
        if section["notes"]:
            document.add_heading("演讲备注", 2); document.add_paragraph(section["notes"])
        for source in section["sources"]:
            document.add_heading("引用："+source["name"], 2); document.add_paragraph(source["content"])
    document.save(destination)
    return {"editable_text": True, "editable_images": True, "sections": len(spec["sections"])}


def build_xlsx(spec: dict, images: dict, destination: Path) -> dict:
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    workbook = Workbook()
    sheet = workbook.active; sheet.title = "章节资料"
    sheet.append(["项目", "序号", "章节", "正文", "演讲备注", "版式", "图片数量"])
    citations = workbook.create_sheet("资料引用"); citations.append(["章节", "引用名称", "引用内容"])
    assets = workbook.create_sheet("图片清单"); assets.append(["章节", "素材序号", "导出宽度", "导出高度", "SHA256"])
    for i, section in enumerate(spec["sections"]):
        # Excel cells cap text at 32767 chars; split and retain every character.
        for part in _segments(section["content"], 30000):
            sheet.append([spec["title"], i+1, section["title"], part, section["notes"], section["layout"], len(section["assets"])])
        for source in section["sources"]: citations.append([section["title"], source["name"], source["content"]])
        for j, reference in enumerate(section["assets"]):
            picture = images[reference]
            assets.append([section["title"], j+1, picture["width"], picture["height"], picture["sha256"]])
    for page in workbook:
        page.freeze_panes = "A2"; page.auto_filter.ref = page.dimensions
        for cell in page[1]:
            cell.font = Font(bold=True, color="FFFFFF"); cell.fill = PatternFill("solid", fgColor=spec["palette"][2][1:])
        for row in page.iter_rows(min_row=2):
            for cell in row:
                # User prose must remain literal, never a spreadsheet formula.
                if isinstance(cell.value, str): cell.data_type = "s"
                cell.alignment = Alignment(wrap_text=True, vertical="top")
        for column in page.columns:
            page.column_dimensions[column[0].column_letter].width = 50 if len(column)>1 and any(isinstance(c.value,str) and len(c.value)>100 for c in column) else 22
    workbook.save(destination)
    return {"sheets": workbook.sheetnames, "editable_cells": True, "formulas_from_user_text": False}


def _safe_folder(store, identifier: str, *, create=False) -> Path:
    if not re.fullmatch(r"[a-f0-9]{32}", identifier):
        raise KeyError("交付成果不存在")
    root = store.data_dir / "deliverables"
    if root.is_symlink() or not root.resolve().is_relative_to(store.data_dir.resolve()):
        raise ValueError("交付成果目录不安全")
    folder = root / identifier
    if folder.is_symlink() or not folder.resolve().is_relative_to(store.data_dir.resolve()):
        raise ValueError("交付成果目录不安全")
    if create:
        root.mkdir(exist_ok=True); folder.mkdir()
    return folder


def export_deliverable(store, body: dict) -> dict:
    spec = normalize_spec(body)
    images = resolve_images(store, spec)
    identifier = secrets.token_hex(16)
    folder = _safe_folder(store, identifier, create=True)
    item = {"id": identifier, "title": spec["title"], "created_at": now(), "formats": {}, "spec": spec}
    builders = {"html": build_html, "pptx": build_pptx, "docx": build_docx, "xlsx": build_xlsx}
    try:
        for fmt in spec["formats"]:
            destination = folder / ("report."+fmt)
            if fmt == "json":
                destination.write_text(json.dumps(spec, ensure_ascii=False, indent=2), encoding="utf-8")
                result = {"editable_recipe": True}
            else:
                result = builders[fmt](spec, images, destination)
            item["formats"][fmt] = {**result, "format": fmt, "path": str(destination), "size": destination.stat().st_size, "sha256": hashlib.sha256(destination.read_bytes()).hexdigest(), "url": f"/api/deliverables/{identifier}/file?format={fmt}"}
        store.save_canvas("deliverable-"+identifier, {"elements": [], "viewport": {}, "deliverable": item})
    except Exception:
        # The exclusively created UUID folder is the only cleanup target.
        if folder.is_dir() and not folder.is_symlink() and folder.parent == (store.data_dir / "deliverables"):
            shutil.rmtree(folder)
        raise
    return item


def get_deliverable(store, identifier):
    folder = _safe_folder(store, identifier)
    item = store.get_canvas("deliverable-"+identifier).get("deliverable")
    if not isinstance(item,dict) or not isinstance(item.get('formats'),dict) or not isinstance(item.get('title'),str) or not isinstance(item.get('spec'),dict) or not folder.is_dir():
        raise KeyError("交付成果不存在")
    # Canvas JSON is also editable by local agent tools. Never accept an
    # injected remote download URL or filesystem path from that JSON.
    files = {fmt:{**meta,'path':str(folder/('report.'+fmt)),'url':f'/api/deliverables/{identifier}/file?format={fmt}'} for fmt,meta in item['formats'].items() if fmt in FORMATS and isinstance(meta,dict)}
    return {**item,'id':identifier,'formats':files}


def register_deliverables(app):
    router = APIRouter(prefix="/api/deliverables")
    facade = app.state.store

    @router.get("")
    def list_deliverables():
        store = facade.current if hasattr(facade, "current") else facade
        with store.connection() as db:
            rows = db.execute("SELECT id FROM canvases WHERE id LIKE 'deliverable-%' ORDER BY updated_at DESC").fetchall()
        items=[]
        for row in rows:
            try:
                item=get_deliverable(store,row['id'][len('deliverable-'):])
                items.append({k:v for k,v in item.items() if k!='spec'})
            except KeyError:
                # Ordinary user canvases may share this human-readable prefix.
                continue
        return {"items": items}

    @router.post("", status_code=201)
    def create(body: dict = Body(...)):
        store = facade.current if hasattr(facade, "current") else facade
        try:
            return export_deliverable(store, body)
        except ImportError as exc:
            raise HTTPException(503, "文档导出组件未随客户端安装，请使用完整安装版") from exc

    @router.post('/validate')
    def validate(body: dict = Body(...)):
        store = facade.current if hasattr(facade, 'current') else facade
        spec=normalize_spec(body)
        for section in spec['sections']:
            for reference in section['assets']:
                _asset_path(store,reference)
        return {'spec':spec}

    @router.get("/{identifier}")
    def get(identifier: str):
        store = facade.current if hasattr(facade, "current") else facade
        return get_deliverable(store, identifier)

    @router.get("/{identifier}/file")
    def download(identifier: str, format: str = "html", preview: bool = False):
        store = facade.current if hasattr(facade, "current") else facade
        if format not in FORMATS:
            raise HTTPException(422, "文件格式无效")
        item = get_deliverable(store, identifier)
        if format not in item["formats"]:
            raise HTTPException(404, "此汇报未导出该格式")
        expected = _safe_folder(store, identifier) / ("report."+format)
        # Stored metadata cannot turn a download into arbitrary filesystem access.
        if expected.is_symlink() or not expected.is_file() or expected.resolve().parent != expected.parent.resolve():
            raise HTTPException(404, "成果文件不存在")
        headers = {"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"}
        if format == "html":
            # Scripts are hash-authorized in the generated file; the sandbox
            # removes same-origin authority when previewed from the API host.
            headers["Content-Security-Policy"] = "sandbox allow-scripts allow-modals; object-src 'none'; connect-src 'none'; base-uri 'none'"
        return FileResponse(expected, media_type=MIMES[format], filename=re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", item["title"])+"."+format, headers=headers, content_disposition_type="inline" if preview and format == "html" else "attachment")

    app.include_router(router)
