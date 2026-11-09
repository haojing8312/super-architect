from __future__ import annotations

import json
from pathlib import Path
from zipfile import ZipFile

import pytest
from PIL import Image
from fastapi.testclient import TestClient

from architecture_workbench.app import create_app
from architecture_workbench.deliverables import export_deliverable, normalize_spec, register_deliverables
from architecture_workbench.report_builder import report_state_spec
from architecture_workbench.store import Store


def synthetic_spec(store, tmp_path):
    image = tmp_path / '方案图.png'
    Image.new('RGB', (900, 360), '#486675').save(image)
    asset = store.import_resource(image)
    return {'title': '山间阅读空间', 'subtitle': '合成验收项目，非真实工程', 'theme': 'dark', 'mode': 'slides', 'transitions': ['reveal', 'slide', 'zoom'], 'formats': ['html', 'pptx', 'docx', 'xlsx', 'json'], 'origin': {'canvas_id': 'canvas-synthetic'}, 'sections': [{'title': '空间策略', 'content': '使用架空廊道连接两个阅读空间。\n保留庭院原有地形。', 'notes': '讲解廊道与庭院的关系。', 'assets': [asset['url']], 'layout': 'split', 'sources': [{'name': '合成设计资料.md', 'content': '引用仅作为资料摘录，需审查适用范围。', 'source_id': 'source-synthetic', 'chunk_id': 'chunk-synthetic', 'page': 2}]}, {'title': '文字完整性', 'content': '连续正文不能截断。'*130, 'notes': '段落较长，导出应自动分页。', 'assets': [], 'layout': 'text'}, {'title': '原文公式应为文字', 'content': '=HYPERLINK("https://invalid.example", "原文")', 'notes': '', 'assets': [asset['url']], 'layout': 'gallery'}]}


def test_native_office_exports_are_editable_and_complete(tmp_path):
    from pptx import Presentation
    from pptx.enum.shapes import MSO_SHAPE_TYPE
    from docx import Document
    from openpyxl import load_workbook
    store = Store(tmp_path / 'data')
    spec = synthetic_spec(store, tmp_path)
    result = export_deliverable(store, spec)
    assert set(result['formats']) == {'html', 'pptx', 'docx', 'xlsx', 'json'}
    assert result['spec']['sections'][0]['sources'][0]['chunk_id'] == 'chunk-synthetic'
    assert result['spec']['origin']['canvas_id'] == 'canvas-synthetic'
    deck = Presentation(result['formats']['pptx']['path'])
    text = '\n'.join(shape.text for slide in deck.slides for shape in slide.shapes if shape.has_text_frame)
    assert '使用架空廊道连接两个阅读空间。' in text
    native_body = ''.join(shape.text for slide in deck.slides for shape in slide.shapes if shape.name == '章节正文')
    assert native_body.count('连续正文不能截断。') == 130
    pictures = [shape for slide in deck.slides for shape in slide.shapes if shape.shape_type == MSO_SHAPE_TYPE.PICTURE]
    assert len(pictures) == 2
    assert all(abs(shape.width / shape.height - 2.5) < .001 for shape in pictures)
    notes = '\n'.join(slide.notes_slide.notes_text_frame.text for slide in deck.slides)
    assert '讲解廊道与庭院的关系。' in notes and '引用仅作为资料摘录' in notes
    # Demonstrate a saved native edit instead of merely inspecting ZIP XML.
    title = next(s for s in deck.slides[0].shapes if s.has_text_frame and s.text == spec['title'])
    title.text = '已在原生文字对象中修改标题'
    edited = tmp_path / 'edited.pptx'; deck.save(edited)
    assert any(s.text == '已在原生文字对象中修改标题' for s in Presentation(edited).slides[0].shapes if s.has_text_frame)
    with ZipFile(edited) as archive:
        assert any(name.startswith('ppt/media/image') for name in archive.namelist())
        assert b'<p:pic>' in archive.read('ppt/slides/slide3.xml')
        assert b'<p:transition' in archive.read('ppt/slides/slide3.xml')
    document = Document(result['formats']['docx']['path'])
    assert len(document.inline_shapes) == 2
    assert '连续正文不能截断。'*130 in '\n'.join(p.text for p in document.paragraphs)
    workbook = load_workbook(result['formats']['xlsx']['path'], data_only=False)
    formula_prose = [cell for row in workbook['章节资料'] for cell in row if isinstance(cell.value, str) and cell.value.startswith('=')]
    assert formula_prose and all(c.data_type == 's' for c in formula_prose)
    assert workbook.sheetnames == ['章节资料', '资料引用', '图片清单']


def test_html_is_offline_escaped_and_all_motion_candidates_survive(tmp_path):
    store = Store(tmp_path / 'data')
    spec = synthetic_spec(store, tmp_path)
    spec['title'] = '<img src=x onerror=alert(1)> & 安全标题'
    result = export_deliverable(store, spec)
    content = Path(result['formats']['html']['path']).read_text(encoding='utf-8')
    assert '&lt;img src=x onerror=alert(1)&gt;' in content
    assert '<img src=x' not in content
    assert 'data:image/png;base64,' in content and 'script-src \'sha256-' in content
    assert str(tmp_path) not in content and 'source-synthetic' not in content
    assert 'data-motion="reveal"' in content and 'data-motion="slide"' in content and 'data-motion="zoom"' in content
    assert 'connect-src \'none\'' in content and '<script src=' not in content and '<link' not in content


@pytest.mark.parametrize('reference', ['../private.png', 'file:///C:/private.png', 'https://example.com/image.png', '/api/resources/'+'a'*32+'/file?token=secret', '/api/files/not-an-id'])
def test_export_rejects_unowned_asset_paths(reference):
    with pytest.raises(ValueError, match='当前本地'):
        normalize_spec({'title': 'x', 'sections': [{'title': 'x', 'assets': [reference]}]})


def test_failure_cleans_only_the_new_owned_export(tmp_path, monkeypatch):
    import architecture_workbench.deliverables as module
    store = Store(tmp_path / 'data')
    spec = synthetic_spec(store, tmp_path)
    previous = export_deliverable(store, {**spec, 'formats': ['html']})
    previous_path = Path(previous['formats']['html']['path']); before = previous_path.read_bytes()
    def fail(*args): raise RuntimeError('controlled exporter failure')
    monkeypatch.setattr(module, 'build_pptx', fail)
    with pytest.raises(RuntimeError): export_deliverable(store, spec)
    assert previous_path.read_bytes() == before
    assert len(list((store.data_dir / 'deliverables').iterdir())) == 1


def test_local_deliverable_routes_download_and_reopen(tmp_path):
    app = create_app(tmp_path / 'data')
    if '/api/deliverables' not in app.openapi()['paths']:
        register_deliverables(app)
        # During independent worker validation the parent registration may not
        # yet exist; place this API router ahead of the root StaticFiles mount.
        app.router.routes.insert(0, app.router.routes.pop())
    with TestClient(app) as client:
        client.headers['X-Workbench-Token'] = client.get('/api/bootstrap').json()['token']
        spec = synthetic_spec(app.state.store, tmp_path)
        created = client.post('/api/deliverables', json=spec)
        assert created.status_code == 201, created.text
        imported=client.post('/api/deliverables/validate',json=spec)
        assert imported.status_code==200 and imported.json()['spec']['title']==spec['title']
        item = created.json(); ident = item['id']
        assert client.get('/api/deliverables').json()['items'][0]['id'] == ident
        app.state.store.save_canvas('deliverable-manual-canvas', {'elements':[], 'viewport':{}})
        assert len(client.get('/api/deliverables').json()['items']) == 1
        ledger=app.state.store.get_canvas('deliverable-'+ident)
        ledger['deliverable']['formats']['html']['url']='https://attacker.invalid/collect'
        ledger['deliverable']['formats']['html']['path']=str(tmp_path/'outside-private.txt')
        app.state.store.save_canvas('deliverable-'+ident,ledger)
        public=client.get('/api/deliverables/'+ident).json()
        assert public['formats']['html']['url'].startswith('/api/deliverables/')
        assert Path(public['formats']['html']['path']).parent.name == ident
        assert client.get('/api/deliverables/'+ident).json()['spec']['title'] == spec['title']
        for fmt, meta in item['formats'].items():
            file = client.get(meta['url'])
            assert file.status_code == 200 and len(file.content) == meta['size']
        preview = client.get(item['formats']['html']['url']+'&preview=true')
        assert preview.headers['content-disposition'].startswith('inline')
        assert 'sandbox allow-scripts' in preview.headers['content-security-policy']
        assert client.get('/api/deliverables/'+ident+'/file?format=private').status_code == 422


def test_complete_editor_export_preserves_user_assignment_and_docs(tmp_path):
    store = Store(tmp_path / 'data')
    spec = synthetic_spec(store, tmp_path)
    source = tmp_path/'原文.md'; source.write_text('用户给出的设计原文，不自动判定为规范。', encoding='utf-8')
    document = store.import_resource(source)
    state = {'base': {'coverTitle': '完整编辑器验收', 'projectType': '建筑'}, 'style': {'id': 'blueprint', 'layoutIds': ['full', 'visual']}, 'interaction': {'mode': 'slides', 'transitions': ['curtain', 'flip']}, 'chapters': [{'id': 'first', 'name': '章节一', 'enabled': True, 'content': '用户正文'}, {'id': 'hidden', 'name': '禁用章节', 'enabled': False}], 'assets': [{'name':'方案.png','type':'image/png','url':spec['sections'][0]['assets'][0]+'?token=private','chapterId':'first'}, {'name':source.name,'type':'text/plain','url':document['url'],'chapterId':'first'}]}
    output = report_state_spec(state, store)
    assert output['transitions'] == ['curtain', 'flip'] and output['mode'] == 'slides'
    assert output['sections'][0]['content'] == '用户正文'
    assert output['sections'][0]['assets'] == spec['sections'][0]['assets']
    assert output['sections'][0]['sources'][0]['content'].startswith('用户给出的设计原文')
    assert len(output['sections']) == 1
    result = export_deliverable(store, output)
    assert 'private' not in json.dumps(result['spec'])
    assert set(result['formats']) == {'html', 'pptx', 'docx', 'xlsx', 'json'}


@pytest.mark.parametrize('change', [{'theme':{}},{'mode':[]},{'formats':[{}]},{'transitions':[[]]},{'sections':[{'layout':{}}]}])
def test_malformed_configuration_is_a_validation_error(change):
    with pytest.raises(ValueError):
        normalize_spec({'title':'项目', 'sections':[{'title':'章节'}], **change})


def test_native_pagination_retains_every_short_paragraph():
    from architecture_workbench.deliverables import _slide_text_pages
    content='短段落。\n'*140
    pages=_slide_text_pages(content,38,12)
    assert ''.join(pages)==content and len(pages)>10
    assert all(page.count('\n')<=10 for page in pages)


def test_oversized_image_header_is_a_validation_error_before_decoding(tmp_path):
    import struct
    import zlib
    source=tmp_path/'large-header.png';Image.new('RGB',(1,1)).save(source)
    header=bytearray(source.read_bytes())
    header[16:24]=struct.pack('>II',20000,20000)
    header[29:33]=struct.pack('>I',zlib.crc32(header[12:29])&0xffffffff)
    source.write_bytes(header)
    store=Store(tmp_path/'data');asset=store.import_resource(source)
    with pytest.raises(ValueError,match='像素过大'):
        export_deliverable(store,{'title':'大图验收','sections':[{'title':'图片','assets':[asset['url']]}]})
