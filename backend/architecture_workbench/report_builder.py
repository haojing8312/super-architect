"""Persistent host state and genuine local assets for the full report editor."""
from pathlib import Path
from fastapi import APIRouter, Body, HTTPException
from .documents import file_type, local_path, extract_text
from .deliverables import TRANSITIONS, export_deliverable
from urllib.parse import urlsplit


def report_state_spec(state: dict, store=None) -> dict:
    """Translate the existing complete editor's saved user choices, without AI.

    Unassigned material is an explicit appendix. Filename guesses never stand
    in for content-aware classification or invent project claims.
    """
    if not isinstance(state, dict):
        raise ValueError('汇报编辑状态无效')
    base, style, interaction = state.get('base', {}), state.get('style', {}), state.get('interaction', {})
    if any(not isinstance(v,dict) for v in (base,style,interaction)):
        raise ValueError('汇报基础、样式或动效配置无效')
    chapters, assets = state.get('chapters', []), state.get('assets', [])
    if not isinstance(chapters, list) or not isinstance(assets, list):
        raise ValueError('汇报章节或素材无效')
    enabled = [c for c in chapters if isinstance(c, dict) and c.get('enabled', True)]
    references, documents = [], []
    for asset in assets:
        if not isinstance(asset, dict):
            continue
        reference = urlsplit(str(asset.get('url', ''))).path
        # Files imported through the local picker are already account-owned.
        # A browser blob or original source path cannot silently become a file.
        if reference.startswith('/api/') and str(asset.get('type', '')).startswith('image/'):
            references.append((asset, reference))
        elif reference.startswith('/api/') and store is not None and Path(str(asset.get('name', ''))).suffix.lower() in {'.txt','.md','.pdf','.docx','.xlsx'}:
            from .deliverables import _asset_path
            text = extract_text(_asset_path(store, reference))
            if len(text) > 20000:
                raise ValueError('完整编辑器中的文字文档超过2万字，请在文档汇报页选择需要引用的摘录')
            documents.append((asset, {'name':asset.get('name','资料文档'),'content':text}))
        elif reference.startswith('/api/'):
            raise ValueError('本地文档导出暂不转换视频或三维模型，请移除此类素材或使用现有 AI 网页生成')
        elif str(asset.get('type', '')).startswith('image/'):
            raise ValueError('部分图片尚未导入工作台，请通过“选择本机文件”重新导入后导出')
    layouts = style.get('layoutIds') or [style.get('layoutId', 'split')]
    if not isinstance(layouts,list) or not layouts or any(not isinstance(v,str) for v in layouts):
        raise ValueError('汇报版式配置无效')
    mapped = ['gallery' if v in {'grid', 'gallery', 'visual', 'collage'} else 'text' if v in {'text', 'typography', 'editorial'} else 'split' for v in layouts]
    sections = []
    assigned = set()
    for i, chapter in enumerate(enabled):
        selected = [r for a, r in references if a.get('chapterId') == chapter.get('id')]
        assigned.update(selected)
        citations = [source for asset,source in documents if asset.get('chapterId') == chapter.get('id')]
        sections.append({'title': chapter.get('name', '章节'), 'content': chapter.get('content', chapter.get('hint', '')), 'notes': chapter.get('notes', ''), 'assets': selected, 'sources':citations, 'layout': mapped[i % len(mapped)]})
    unassigned = [r for _, r in references if r not in assigned]
    if unassigned:
        sections.append({'title': '补充素材', 'content': '以下素材尚未指定章节，保留原图供汇报时查阅。', 'assets': unassigned, 'layout': 'gallery'})
    unassigned_documents = [source for asset,source in documents if asset.get('chapterId') not in {c.get('id') for c in enabled}]
    if unassigned_documents:
        sections.append({'title':'资料附录','content':'保留导入文档的原文摘录；请根据项目适用范围核实引用。','sources':unassigned_documents,'layout':'text'})
    if not sections:
        raise ValueError('请至少添加一个章节或导入图片')
    transitions = interaction.get('transitions') or [interaction.get('transition', 'slide')]
    if not isinstance(transitions,list) or not transitions or any(not isinstance(v,str) or v not in TRANSITIONS for v in transitions):
        raise ValueError('当前动效尚不支持本地导出，请在编辑器中选择受支持的动效')
    preset_colors = {'graphite':['#8C3232','#363636','#CAD2C5'], 'dark':['#F2F2F2','#A6A6A6','#1A1A1A'], 'light':['#2C3E50','#95A5A6','#EAECEE'], 'editorial':['#9A8C73','#DBC7B5','#F4F1EE'], 'immersive':['#6D5B71','#B8B0B9','#FDFBF7'], 'contrast':['#4A5D4E','#8E9A8E','#D1D5D1'], 'space':['#BFA695','#594B42','#F2EFE9'], 'blueprint':['#282846','#F55023','#E6D7BE'], 'earth':['#F0F0E9','#B8C49B','#2A2E2D']}
    colors = style.get('projectPalette') if style.get('useProjectPalette') else preset_colors.get(style.get('id'))
    import re
    if colors is not None and (not isinstance(colors,list) or len(colors)!=3 or any(not isinstance(v,str) or not re.fullmatch(r'#[a-fA-F0-9]{6}',v) for v in colors)):
        raise ValueError('编辑器配色无效')
    # Original palette order is dominant/secondary/accent. Pick the darkest
    # and lightest endpoints for legible native text, retaining its accent.
    def luminance(color):
        return sum(int(color[i:i+2],16)*weight for i,weight in ((1,.2126),(3,.7152),(5,.0722)))
    palette = [max(colors,key=luminance),min(colors,key=luminance),colors[0]] if colors else None
    return {'title': base.get('coverTitle', ''), 'subtitle': base.get('projectType', '') + ' · ' + base.get('language', '中文'), 'theme': {'graphite': 'dark', 'earth': 'warm', 'blueprint': 'blueprint'}.get(style.get('id'), 'minimal'), **({'palette': palette} if palette else {}), 'mode': 'slides' if interaction.get('mode') in {'slides', 'slide'} else 'scroll', 'transitions': transitions, 'sections': sections, 'formats': ['html', 'pptx', 'docx', 'xlsx', 'json']}


def register_report_builder(app):
    router=APIRouter(prefix='/api/report-builder')
    store=app.state.store

    @router.get('/state/{context_id}')
    def get_state(context_id:str):
        return store.get_canvas('report-state-'+context_id).get('report_state',{})

    @router.put('/state/{context_id}')
    def save_state(context_id:str,body:dict=Body(...)):
        # Canvas is an arbitrary JSON document store, so reuse its persistent transactions.
        return store.save_canvas('report-state-'+context_id,{'elements':[],'viewport':{},'report_state':body})

    @router.post('/assets')
    def assets(body:dict=Body(...)):
        paths=body.get('paths',[])
        if not isinstance(paths,list) or len(paths)>200:
            raise HTTPException(422,'最多选择200个素材或一个文件夹')
        sources=[]
        for supplied in paths:
            source=Path(supplied).expanduser().resolve(strict=True)
            if source.is_dir():
                sources.extend(p for p in source.rglob('*') if p.is_file() and p.suffix.lower() in {'.png','.jpg','.jpeg','.webp','.gif','.pdf','.docx','.pptx','.xlsx','.mp4','.glb','.obj','.skp'})
            else:
                sources.append(source)
        if len(sources)>200:
            raise HTTPException(422,'文件夹含超过200个素材，请分批导入')
        rows=[]
        for source in sources:
            item=store.import_resource(str(local_path(source)))
            rows.append({'name':source.name,'fullPath':str(source),'relativePath':source.name,'type':file_type(source),'size':source.stat().st_size,'source':'host','url':item['url'],'sourceRoot':str(source.parent)})
        return {'items':rows}

    @router.post('/export/{context_id}', status_code=201)
    def export(context_id: str):
        state = store.get_canvas('report-state-'+context_id).get('report_state', {})
        target = store.current if hasattr(store, 'current') else store
        return export_deliverable(target, report_state_spec(state, target))

    app.include_router(router)
