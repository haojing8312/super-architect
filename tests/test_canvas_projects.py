import base64
import io
import json
import os
import stat
import struct
import zipfile

from fastapi.testclient import TestClient
from PIL import Image
import pytest

from architecture_workbench.app import create_app
from architecture_workbench.canvas_projects import CanvasProjects
from architecture_workbench.store import Store


def picture():
    output=io.BytesIO(); Image.new('RGB',(24,16),(120,60,30)).save(output,format='PNG'); return output.getvalue()


def bundle(data,files=None):
    output=io.BytesIO()
    with zipfile.ZipFile(output,'w',compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('canvas.json',json.dumps(data,ensure_ascii=False))
        for name,value in (files or {}).items(): archive.writestr(name,value)
    return output.getvalue()


def canvas(image):
    href='data:image/png;base64,'+base64.b64encode(image).decode()
    return {'elements':[
        {'id':'one','type':'image','x':-20,'y':30,'w':240,'h':160,'href':href,'groupId':'group-one'},
        {'id':'two','type':'image','x':280,'y':30,'w':240,'h':160,'href':href,'groupId':'group-one'},
        {'id':'label','type':'text','x':30,'y':200,'w':200,'h':30,'text':'建筑方案','fontSize':24,
         'fontWeight':'bold','fontStyle':'italic','color':'#336699','groupId':'group-one'}],
        'viewport':{'x':4,'y':8,'zoom':2,'zoomY':3},'meta':{'groups':[{'name':'原分组名'}]}}


def test_canvas_zip_roundtrip_deduplicates_real_assets_and_never_overwrites(tmp_path):
    store=Store(tmp_path); projects=CanvasProjects(store)
    original=projects.create('建筑总图',canvas(picture()))
    packed=projects.export(original['id'])
    with zipfile.ZipFile(io.BytesIO(packed)) as archive:
        assert len(archive.namelist())==2
        data=json.loads(archive.read('canvas.json'))
        assert data['elements'][0]['href']==data['elements'][1]['href']
        assert archive.read(data['elements'][0]['href'])==picture()
    restored=projects.import_archive(packed,'建筑总图.zip')
    assert restored['id']!=original['id'] and restored['name']=='建筑总图'
    assert restored['elements']==original['elements'] and restored['viewport']==original['viewport']
    assert restored['meta']['groups']==original['meta']['groups']
    assert store.get_canvas(original['id'])==original
    image_bundle=projects.export(restored['id'],images_only=True)
    with zipfile.ZipFile(io.BytesIO(image_bundle)) as archive:
        assert archive.namelist()==[data['elements'][0]['href']]
    store.save_canvas(restored['id'],{'elements':restored['elements'],'viewport':restored['viewport']})
    assert store.get_canvas(restored['id'])['name']=='建筑总图'


def test_original_xzpack_pascal_case_groups_argb_and_nonuniform_viewport(tmp_path):
    projects=CanvasProjects(Store(tmp_path))
    legacy={'ScaleX':2,'ScaleY':3,'TranslateX':12,'TranslateY':-4,
        'Items':[{'Id':'image-guid','FilePath':'assets/image.png','X':10,'Y':20,'Width':240,'Height':160,'GroupId':'group-guid'}],
        'TextItems':[{'Id':'text-guid','Text':'方案说明','X':10,'Y':220,'FontSize':24,'FontColor':'#FF336699',
                      'IsBold':True,'IsItalic':True,'GroupId':'group-guid'}],
        'Groups':[{'GroupId':'group-guid','GroupName':'总平面','FontSize':28}]}
    restored=projects.import_archive(bundle(legacy,{'assets/image.png':picture()}),'源项目.xzpack')
    assert restored['name']=='源项目'
    assert restored['viewport']=={'x':12,'y':-4,'zoom':2,'zoomY':3}
    assert restored['elements'][0]['groupId']=='group-guid'
    assert restored['elements'][1]['color']=='#336699FF'
    assert restored['elements'][1]['fontWeight']=='bold' and restored['elements'][1]['fontStyle']=='italic'
    assert restored['meta']['groups'][0]['GroupName']=='总平面'
    assert restored['meta']['imported_from']=='jzxz-xzpack'


@pytest.mark.parametrize('path',['../escape.png','/absolute.png','C:/outside.png','//host/file.png','assets/missing.png'])
def test_invalid_asset_reference_rejects_whole_project(tmp_path,path):
    store=Store(tmp_path); projects=CanvasProjects(store)
    document={'elements':[{'id':'image','type':'image','x':0,'y':0,'w':10,'h':10,'href':path}]}
    with pytest.raises(ValueError): projects.import_archive(bundle(document,{'assets/image.png':picture()}),'bad.zip')
    assert projects.list()==[]
    assert list((store.data_dir/'attachments').iterdir())==[]


@pytest.mark.parametrize('name',['../outside.txt','C:/outside.txt','/outside.txt'])
def test_even_unused_unsafe_zip_member_is_rejected(tmp_path,name):
    with pytest.raises(ValueError): CanvasProjects(Store(tmp_path)).import_archive(bundle({'elements':[]},{name:b'data'}),'bad.zip')


def test_duplicate_casefold_symlink_and_invalid_raster_are_rejected(tmp_path):
    projects=CanvasProjects(Store(tmp_path))
    with pytest.raises(ValueError):
        projects.import_archive(bundle({'elements':[]},{'assets/A.png':picture(),'assets/a.png':picture()}),'duplicates.zip')
    output=io.BytesIO()
    with zipfile.ZipFile(output,'w') as archive:
        archive.writestr('canvas.json','{"elements":[]}')
        link=zipfile.ZipInfo('assets/link.png'); link.create_system=3; link.external_attr=(stat.S_IFLNK|0o777)<<16
        archive.writestr(link,'C:/private.png')
    with pytest.raises(ValueError): projects.import_archive(output.getvalue(),'links.zip')
    bad={'elements':[{'id':'image','type':'image','href':'assets/script.svg','x':0,'y':0,'w':20,'h':20}]}
    with pytest.raises(ValueError): projects.import_archive(bundle(bad,{'assets/script.svg':b'<svg onload="alert(1)"/>'}),'svg.zip')


def test_original_zip_names_are_checked_before_windows_normalization(tmp_path):
    raw=bundle({'elements':[]},{'assets/file.png':picture()})
    # Replace both central/local header names without changing payload or lengths.
    # zipfile's Windows writer otherwise sanitizes the test input before our read.
    ambiguous=raw.replace(b'assets/file.png',b'assets\\file.png')
    with pytest.raises(ValueError): CanvasProjects(Store(tmp_path)).import_archive(ambiguous,'bad.zip')


def test_project_listing_excludes_drafts_reports_and_keeps_legacy_canvas(tmp_path):
    store=Store(tmp_path); projects=CanvasProjects(store)
    store.save_canvas('default',{'elements':[]})
    store.save_canvas('draft-session',{'draft':{'text':'private'}})
    store.save_canvas('report-state-session',{'state':{'chapters':[]}})
    store.save_canvas('report-builder-session',{'elements':[]})
    assert projects.list()[0]['id']=='default' and len(projects.list())==1


def test_repeated_asset_expansion_is_bounded_before_persistence(tmp_path,monkeypatch):
    output=io.BytesIO(); Image.frombytes('RGB',(256,256),os.urandom(256*256*3)).save(output,format='PNG')
    raw=output.getvalue()
    elements=[{'id':str(i),'type':'image','x':0,'y':0,'w':10,'h':10,'href':'assets/one.png'} for i in range(100)]
    projects=CanvasProjects(Store(tmp_path))
    calls=[]; original=zipfile.ZipFile.read
    def observed(self,name,*args,**kwargs):
        calls.append(name.filename if isinstance(name,zipfile.ZipInfo) else name)
        return original(self,name,*args,**kwargs)
    monkeypatch.setattr(zipfile.ZipFile,'read',observed)
    with pytest.raises(ValueError,match='展开后'):
        projects.import_archive(bundle({'elements':elements},{'assets/one.png':raw}),'shared.zip')
    assert calls.count('assets/one.png')==1
    assert projects.list()==[]
    href='data:image/png;base64,'+base64.b64encode(raw).decode()
    for element in elements: element['href']=href
    with pytest.raises(ValueError,match='15 MB'):
        projects.create('超限项目',{'elements':elements})
    assert projects.list()==[]


def test_owned_file_reference_expansion_is_bounded_before_persistence(tmp_path):
    output=io.BytesIO(); Image.frombytes('RGB',(256,256),os.urandom(256*256*3)).save(output,format='PNG')
    store=Store(tmp_path); uploaded=store.save_attachment('actual.png',io.BytesIO(output.getvalue()))
    elements=[{'id':str(i),'type':'image','x':0,'y':0,'w':10,'h':10,'href':uploaded['url']} for i in range(100)]
    projects=CanvasProjects(store)
    with pytest.raises(ValueError,match='15 MB'):
        projects.create('实际附件复用',{'elements':elements})
    assert projects.list()==[] and store.get_file(uploaded['id'])['size']>0


@pytest.mark.parametrize('change',[
    {'type':{}},{'type':[]},{'x':10**1000},{'fontFamily':{}},{'fontWeight':False},{'fontStyle':[]},
])
def test_malformed_geometry_or_fonts_are_user_errors(tmp_path,change):
    text={'id':'text','type':'text','text':'有效文字','x':0,'y':0,'w':20,'h':20,**change}
    with pytest.raises(ValueError): CanvasProjects(Store(tmp_path)).create('无效项目',{'elements':[text]})


def test_corrupt_deflate_and_large_directory_are_rejected_before_zipinfo_allocation(tmp_path,monkeypatch):
    projects=CanvasProjects(Store(tmp_path))
    raw=bytearray(bundle({'elements':[]}))
    name_length,extra_length=struct.unpack_from('<HH',raw,26)
    raw[30+name_length+extra_length]=255  # Reserved DEFLATE block type.
    with pytest.raises(ValueError): projects.import_archive(bytes(raw),'damaged.zip')
    output=io.BytesIO()
    with zipfile.ZipFile(output,'w') as archive:
        archive.writestr('canvas.json','{"elements":[]}')
        for index in range(10_001): archive.writestr(f'directory-{index}/',b'')
    import architecture_workbench.canvas_projects as module
    def must_not_construct(*args,**kwargs): raise AssertionError('Preflight must reject before ZipInfo creation')
    monkeypatch.setattr(module.zipfile,'ZipFile',must_not_construct)
    with pytest.raises(ValueError,match='10001'):
        projects.import_archive(output.getvalue(),'directories.zip')


def test_nonprefix_internal_state_cannot_be_exported_or_created(tmp_path):
    store=Store(tmp_path); projects=CanvasProjects(store)
    store.save_canvas('unusual-id',{'elements':[],'state':{'private-report':True}})
    assert projects.list()==[]
    with pytest.raises(KeyError): projects.export('unusual-id')
    with pytest.raises(ValueError): projects.create('不得导入内部状态',{'elements':[],'draft':{'text':'private'}})


def test_canvas_real_api_create_zip_import_download_and_auth(tmp_path):
    app=create_app(tmp_path)
    with TestClient(app) as client:
        assert client.get('/api/canvas-projects').status_code==401
        client.headers['X-Workbench-Token']=client.get('/api/bootstrap').json()['token']
        project=client.post('/api/canvas-projects',json={'name':'实际API项目','data':canvas(picture())})
        assert project.status_code==201,project.text
        identifier=project.json()['id']
        downloaded=client.get(f'/api/canvas/{identifier}/export.zip')
        assert downloaded.status_code==200 and downloaded.headers['content-type']=='application/zip'
        assert 'filename*=' in downloaded.headers['content-disposition']
        restored=client.post('/api/canvas-projects/import',files={'file':('actual.zip',downloaded.content,'application/zip')})
        assert restored.status_code==201 and restored.json()['id']!=identifier
        assert len(client.get('/api/canvas-projects').json()['items'])==2
        bad=client.post('/api/canvas-projects/import',files={'file':('bad.zip',b'not a zip','application/zip')})
        assert bad.status_code==422
        assert client.get('/api/canvas/nonexistent/export.zip').status_code==404
