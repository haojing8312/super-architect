"""Portable canvas projects, including the original raster-only .xzpack format."""
from __future__ import annotations

import base64
import binascii
import copy
import hashlib
import io
import json
import math
from pathlib import Path, PurePosixPath
import re
import stat
import struct
from urllib.parse import quote, urlsplit
import uuid
import unicodedata
import zipfile
import zlib

from fastapi import APIRouter, File, UploadFile
from fastapi.responses import Response
from PIL import Image, ImageColor
from pydantic import BaseModel, Field

from .documents import safe_name

MAX_ARCHIVE_BYTES = 80 * 1024 * 1024
MAX_EXPANDED_BYTES = 80 * 1024 * 1024
MAX_JSON_BYTES = 15 * 1024 * 1024
MAX_IMAGE_BYTES = 20 * 1024 * 1024
MAX_IMAGE_PIXELS = 40_000_000
RASTER = {'PNG': ('image/png', '.png'), 'JPEG': ('image/jpeg', '.jpg'),
          'WEBP': ('image/webp', '.webp'), 'GIF': ('image/gif', '.gif'), 'BMP': ('image/bmp', '.bmp')}
INTERNAL_PREFIXES = ('draft-', 'report-state-', 'report-builder-')


def _internal(identifier, data):
    return identifier.startswith(INTERNAL_PREFIXES) or any(key in data for key in ('draft','report_state','state'))


def _preflight_archive(raw):
    """Bound central records before ZipFile allocates a ZipInfo for each entry."""
    end=zipfile._EndRecData(io.BytesIO(raw))
    if end is None: raise ValueError('项目包缺少ZIP目录')
    length=end[zipfile._ECD_SIZE]
    start=end[zipfile._ECD_LOCATION]-length
    if end[zipfile._ECD_SIGNATURE]==zipfile.stringEndArchive64:
        start-=zipfile.sizeEndCentDir64+zipfile.sizeEndCentDir64Locator
    if not 0<=length<=8*1024*1024 or start<0 or start+length>len(raw):
        raise ValueError('项目包ZIP目录超过上限或位置无效')
    count=0; offset=start; stop=start+length
    while offset<stop:
        if offset+zipfile.sizeCentralDir>stop: raise ValueError('项目包ZIP目录不完整')
        record=struct.unpack_from(zipfile.structCentralDir,raw,offset)
        if record[zipfile._CD_SIGNATURE]!=zipfile.stringCentralDir: raise ValueError('项目包ZIP目录损坏')
        count+=1
        if count>10_001: raise ValueError('项目包文件及目录超过10001项')
        name_length=record[zipfile._CD_FILENAME_LENGTH]
        if name_length>1024: raise ValueError('项目包单个路径过长')
        offset+=zipfile.sizeCentralDir+name_length+record[zipfile._CD_EXTRA_FIELD_LENGTH]+record[zipfile._CD_COMMENT_LENGTH]
    if offset!=stop: raise ValueError('项目包ZIP目录长度不一致')


def _json_budget(data):
    """Count encoded JSON without materializing a repeated-reference expansion."""
    size=0
    encoder=json.JSONEncoder(ensure_ascii=False,allow_nan=False)
    for part in encoder.iterencode(data):
        if len(part)>MAX_JSON_BYTES:
            raise ValueError('画布配置及嵌入图片超过15 MB')
        size+=len(part.encode('utf-8'))
        if size>MAX_JSON_BYTES:
            raise ValueError('画布配置及嵌入图片超过15 MB')
    return size


def _number(value, label, *, minimum=-1e8, maximum=1e8):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not minimum <= value <= maximum or (isinstance(value,float) and not math.isfinite(value)):
        raise ValueError(f'{label}须为范围内的有限数值')
    return value


def _image(raw: bytes, expected_mime: str = ''):
    if not raw or len(raw) > MAX_IMAGE_BYTES:
        raise ValueError('单张画布图片须不超过20 MB')
    try:
        with Image.open(io.BytesIO(raw)) as picture:
            if picture.format not in RASTER or picture.width * picture.height > MAX_IMAGE_PIXELS:
                raise ValueError('画布只接受 PNG/JPEG/WebP/GIF/BMP，单图最多4000万像素')
            mime, suffix = RASTER[picture.format]
            if expected_mime and expected_mime != mime:
                raise ValueError('画布图片内容与标注格式不一致')
            picture.verify()
    except (OSError, SyntaxError, Image.DecompressionBombError) as error:
        raise ValueError('画布图片损坏或尺寸过大') from error
    return raw, mime, suffix


def _member_name(value):
    if not isinstance(value, str) or not value or '\x00' in value or '\\' in value or ':' in value:
        raise ValueError('项目包包含无效路径')
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {'..', '.', ''} for part in value.split('/')):
        raise ValueError('项目包不允许绝对或越界路径')
    return path.as_posix()


def _data_url(raw, mime):
    return 'data:' + mime + ';base64,' + base64.b64encode(raw).decode('ascii')


def _meta(data):
    value = data.get('meta', {})
    return value if isinstance(value, dict) else {}


def _href_image(store, value):
    if not isinstance(value, str):
        raise ValueError('画布图片缺少来源')
    if value.startswith('data:'):
        match = re.fullmatch(r'data:(image/(?:png|jpeg|webp|gif|bmp));base64,([A-Za-z0-9+/=]+)', value)
        if not match or len(value) > MAX_IMAGE_BYTES * 4 // 3 + 100:
            raise ValueError('画布图片必须是合法的本机位图')
        try:
            return _image(base64.b64decode(match[2], validate=True), match[1])
        except binascii.Error as error:
            raise ValueError('画布图片编码无效') from error
    parsed = urlsplit(value)
    if parsed.netloc and (parsed.scheme not in {'http', 'https'} or parsed.hostname not in {'127.0.0.1', 'localhost', '::1'}):
        raise ValueError('项目打包不能读取外部图片地址')
    match = re.fullmatch(r'/api/files/([a-f0-9]{32})', parsed.path)
    if match:
        item = store.get_file(match[1])
    else:
        match = re.fullmatch(r'/api/resources/([a-f0-9]{32})/file', parsed.path)
        if not match:
            raise ValueError('项目打包仅支持嵌入位图或工作台素材，不能读取任意路径')
        item = store.get_resource(match[1])
    path = store._owned_path(item['path'])
    if path.stat().st_size > MAX_IMAGE_BYTES:
        raise ValueError('单张画布图片超过20 MB')
    return _image(path.read_bytes())


def _legacy_color(value):
    if isinstance(value, dict) and all(key in value for key in ('R', 'G', 'B')):
        parts = [_number(value[key], '字体颜色', minimum=0, maximum=255) for key in ('R', 'G', 'B')]
        parts.append(_number(value.get('A', 255), '字体透明度', minimum=0, maximum=255))
        return '#' + ''.join(f'{int(part):02X}' for part in parts)
    if isinstance(value, str) and re.fullmatch(r'#[a-fA-F0-9]{8}', value):
        return '#' + value[3:] + value[1:3]  # WPF ARGB -> SVG RGBA
    if isinstance(value, str) and re.fullmatch(r'#[a-fA-F0-9]{6}', value):
        return value
    return '#2F3C48'


def _legacy_group(value):
    text = str(value or '')
    return '' if text in {'0', '00000000-0000-0000-0000-000000000000', '00000000000000000000000000000000'} else text


def _native(data):
    if not isinstance(data, dict) or not isinstance(data.get('elements'), list) or len(data['elements']) > 10_000:
        raise ValueError('项目配置须包含最多10000个elements')
    if not isinstance(data.get('viewport', {}), dict) or not isinstance(data.get('meta', {}), dict):
        raise ValueError('画布viewport/meta必须为对象')
    if _internal('',data): raise ValueError('内部草稿和报告状态不能作为画布项目')
    if data.get('schema_version',1)!=1: raise ValueError('画布项目格式版本不支持')
    identities = set()
    for element in data['elements']:
        if not isinstance(element, dict) or not isinstance(element.get('type'),str) or element.get('type') not in {'image', 'text', 'rect', 'ellipse', 'pencil'}:
            raise ValueError('项目包含不支持的画布元素')
        identifier = element.get('id')
        if not isinstance(identifier, str) or not identifier or len(identifier) > 200 or identifier in identities:
            raise ValueError('画布元素标识须唯一且非空')
        identities.add(identifier)
        if 'color' in element:
            color=element['color']
            if not isinstance(color,str) or len(color)>100:
                raise ValueError('画布颜色无效')
            if color not in {'none','transparent'}:
                try: ImageColor.getrgb(color)
                except ValueError as error: raise ValueError('画布颜色必须为本机可识别的颜色，不能引用外部资源') from error
        for field in ('x', 'y', 'w', 'h'):
            _number(element.get(field, 0), f'元素{field}', minimum=0 if field in {'w', 'h'} else -1e8)
        if 'groupId' in element and (not isinstance(element['groupId'], (str, type(None))) or len(element['groupId'] or '') > 200):
            raise ValueError('画布分组标识无效')
        if element['type'] == 'text':
            if not isinstance(element.get('text', ''), str) or len(element.get('text', '')) > 100_000:
                raise ValueError('画布文字过长或无效')
            _number(element.get('fontSize', 20), '字体大小', minimum=1, maximum=2000)
            family=element.get('fontFamily','Microsoft YaHei UI')
            if not isinstance(family,str) or len(family)>200 or any(ord(char)<32 for char in family):
                raise ValueError('字体名称无效')
            weight=element.get('fontWeight','normal')
            if isinstance(weight,(int,float)) and not isinstance(weight,bool):
                _number(weight,'字体字重',minimum=1,maximum=1000)
            elif not isinstance(weight,str) or weight not in {'normal','bold','bolder','lighter','100','200','300','400','500','600','700','800','900'}:
                raise ValueError('字体字重无效')
            if element.get('fontStyle','normal') not in ('normal','italic','oblique'):
                raise ValueError('字体样式无效')
        if element['type'] == 'pencil':
            points = element.get('points', [])
            if not isinstance(points, list) or len(points) > 250_000:
                raise ValueError('画布笔迹点数过多')
            for point in points:
                if not isinstance(point, dict):
                    raise ValueError('画布笔迹无效')
                _number(point.get('x'), '笔迹x'); _number(point.get('y'), '笔迹y')
    viewport = data.get('viewport', {})
    for key in ('x', 'y'):
        _number(viewport.get(key, 0), f'视口{key}')
    _number(viewport.get('zoom', 1), '视口缩放', minimum=.01, maximum=100)
    _number(viewport.get('zoomY', viewport.get('zoom', 1)), '视口纵向缩放', minimum=.01, maximum=100)
    _json_budget(data)
    return data


def _legacy(data):
    if not isinstance(data.get('Items', []), list) or not isinstance(data.get('TextItems', []), list):
        raise ValueError('原画布包的Items/TextItems无效')
    elements = []
    for index, item in enumerate(data.get('Items', [])):
        if not isinstance(item, dict):
            raise ValueError('原画布图片记录无效')
        elements.append({'id': str(item.get('Id') or f'legacy-image-{index}'), 'type': 'image',
            'x': item.get('X', 0), 'y': item.get('Y', 0), 'w': item.get('Width', 100), 'h': item.get('Height', 100),
            'color': '#2F3C48', 'href': item.get('FilePath').replace('\\', '/') if isinstance(item.get('FilePath'), str) else item.get('FilePath'), 'groupId': _legacy_group(item.get('GroupId')),
            'description': item.get('Description', '')})
    for index, item in enumerate(data.get('TextItems', [])):
        if not isinstance(item, dict):
            raise ValueError('原画布文字记录无效')
        size = _number(item.get('FontSize', 20), '原字体大小', minimum=1, maximum=2000)
        text = item.get('Text', '')
        if not isinstance(text, str):
            raise ValueError('原画布文字无效')
        lines = text.splitlines() or ['']
        width = max(sum(1 if unicodedata.east_asian_width(char) in {'W','F'} else .65 for char in line) for line in lines) * size
        elements.append({'id': str(item.get('Id') or f'legacy-text-{index}'), 'type': 'text', 'text': text,
            'x': item.get('X', 0), 'y': item.get('Y', 0), 'w': max(size, width), 'h': size * 1.4 * len(lines),
            'fontSize': size, 'color': _legacy_color(item.get('FontColor')), 'fontFamily': 'Microsoft YaHei UI',
            'fontWeight': 'bold' if item.get('IsBold') else 'normal', 'fontStyle': 'italic' if item.get('IsItalic') else 'normal',
            'groupId': _legacy_group(item.get('GroupId'))})
    return {'elements': elements, 'viewport': {'x': data.get('TranslateX', 0), 'y': data.get('TranslateY', 0),
        'zoom': data.get('ScaleX', 1), 'zoomY': data.get('ScaleY', data.get('ScaleX', 1))},
        'schema_version': 1, 'meta': {'schema_version': 1, 'groups': data.get('Groups', []), 'imported_from': 'jzxz-xzpack'}}


class CanvasProjects:
    def __init__(self, store):
        self.store = store

    def list(self):
        with self.store.connection() as db:
            rows = db.execute('SELECT id,data,updated_at FROM canvases ORDER BY updated_at DESC').fetchall()
        output = []
        for row in rows:
            data = json.loads(row['data'])
            if _internal(row['id'],data):
                continue
            if not isinstance(data.get('elements'), list):
                continue
            output.append({'id': row['id'], 'name': data.get('name') or _meta(data).get('name') or ('默认画布' if row['id']=='default' else '会话画布'),
                           'element_count': len(data['elements']), 'updated_at': row['updated_at']})
        return output

    def materialize(self, data):
        """Prepare an inert portable snapshot without creating database rows."""
        supplied=data or {'elements': [], 'viewport': {'x':0,'y':0,'zoom':1}}
        _native(supplied)
        value = copy.deepcopy(supplied)
        value = _native(value)
        budget=_json_budget(value); image_cache={}
        for element in value['elements']:
            if element['type']=='image':
                source=element.get('href')
                if not isinstance(source,str): raise ValueError('画布图片缺少来源')
                old_size=len(json.dumps(source,ensure_ascii=False).encode('utf-8'))-2
                if source not in image_cache:
                    raw, mime, _ = _href_image(self.store, source)
                    encoded_size=len('data:'+mime+';base64,')+4*((len(raw)+2)//3)
                    if budget+encoded_size-old_size>MAX_JSON_BYTES: raise ValueError('画布配置及嵌入图片超过15 MB')
                    image_cache[source]=_data_url(raw,mime)
                href=image_cache[source]
                budget+=len(href)-old_size
                if budget>MAX_JSON_BYTES: raise ValueError('画布配置及嵌入图片超过15 MB')
                element['href']=href
        _native(value)
        return value

    def snapshot(self, identifier):
        with self.store.connection() as db:
            exists=db.execute('SELECT 1 FROM canvases WHERE id=?',(identifier,)).fetchone()
        if exists is None: raise KeyError('画布项目不存在')
        data=self.store.get_canvas(identifier)
        if _internal(identifier,data): raise KeyError('画布项目不存在')
        return self.materialize(data)

    def create(self, name, data=None):
        if not isinstance(name, str) or not name.strip() or len(name) > 200:
            raise ValueError('画布项目名须为1至200字符')
        value=self.materialize(data)
        value['name'] = name.strip()
        value['schema_version'] = 1
        value['meta'] = {**value.get('meta', {}), 'name': name.strip(), 'schema_version': 1}
        return self.store.save_canvas('canvas-' + uuid.uuid4().hex, value)

    def export(self, identifier, *, images_only=False):
        with self.store.connection() as db:
            exists = db.execute('SELECT 1 FROM canvases WHERE id=?', (identifier,)).fetchone()
        if exists is None:
            raise KeyError('画布项目不存在')
        data=self.store.get_canvas(identifier)
        if _internal(identifier,data): raise KeyError('画布项目不存在')
        data = _native(data)
        assets = {}; total = 0; references={}
        for element in data['elements']:
            if element['type']!='image': continue
            source=element.get('href')
            if isinstance(source,str) and source in references:
                element['href']=references[source]; continue
            raw, mime, suffix = _href_image(self.store, source)
            name = 'assets/' + hashlib.sha256(raw).hexdigest() + suffix
            if name not in assets:
                total += len(raw)
                if total > MAX_EXPANDED_BYTES: raise ValueError('项目素材超过80 MB导出上限')
                assets[name] = raw
            element['href'] = name
            references[source]=name
        manifest = json.dumps({**data, 'schema_version':1}, ensure_ascii=False, allow_nan=False).encode('utf-8')
        if len(manifest)>MAX_JSON_BYTES: raise ValueError('项目配置超过15 MB')
        buffer=io.BytesIO()
        with zipfile.ZipFile(buffer, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
            if not images_only: archive.writestr('canvas.json',manifest)
            for name,raw in assets.items(): archive.writestr(name,raw)
        return buffer.getvalue()

    def import_archive(self, supplied, name):
        if len(supplied)>MAX_ARCHIVE_BYTES: raise ValueError('画布项目包超过80 MB')
        try:
            _preflight_archive(supplied)
            with zipfile.ZipFile(io.BytesIO(supplied)) as archive:
                entries={}; total=0; all_names=set()
                for item in archive.infolist():
                    # ZipInfo normalizes backslashes on Windows and truncates
                    # NULs. Validate the original decoded name before trusting it.
                    original=item.orig_filename
                    normalized=_member_name(original.rstrip('/') if item.is_dir() else original)
                    folded=normalized.casefold()
                    if folded in all_names: raise ValueError('项目包有重复的文件或目录路径')
                    all_names.add(folded)
                    if stat.S_ISLNK(item.external_attr>>16) or item.flag_bits&1:
                        raise ValueError('项目包不允许链接或加密文件')
                    if item.is_dir(): continue
                    if item.compress_type not in {zipfile.ZIP_STORED,zipfile.ZIP_DEFLATED}:
                        raise ValueError('项目包仅支持普通ZIP存储或DEFLATE压缩')
                    if folded in entries: raise ValueError('项目包有重复的文件路径')
                    if item.file_size<0: raise ValueError('项目包文件长度无效')
                    total+=item.file_size
                    if total>MAX_EXPANDED_BYTES or len(entries)>=10_001:
                        raise ValueError('项目包解压大小或文件数超过上限')
                    entries[folded]=item
                if 'canvas.json' not in entries or entries['canvas.json'].file_size>MAX_JSON_BYTES:
                    raise ValueError('项目包缺少有效的canvas.json')
                data=json.loads(archive.read(entries['canvas.json']).decode('utf-8-sig'))
                if not isinstance(data,dict): raise ValueError('项目配置必须为对象')
                if 'elements' not in data and ('Items' in data or 'TextItems' in data): data=_legacy(data)
                _native(data)
                budget=_json_budget(data)
                image_cache={}
                for element in data['elements']:
                    if element['type']!='image': continue
                    path=_member_name(element.get('href'))
                    if not path.startswith('assets/') or path.casefold() not in entries:
                        raise ValueError('画布图片必须是包内存在的assets素材')
                    item=entries[path.casefold()]
                    if item.file_size>MAX_IMAGE_BYTES: raise ValueError('单张画布图片超过20 MB')
                    old_size=len(json.dumps(element['href'],ensure_ascii=False).encode('utf-8'))-2
                    # Reject before generating base64, including repeated hrefs.
                    upper_size=len('data:image/jpeg;base64,')+4*((item.file_size+2)//3)
                    if budget+upper_size-old_size>MAX_JSON_BYTES:
                        raise ValueError('项目重复素材展开后超过15 MB，请减少重复图片或压缩原图')
                    if path.casefold() not in image_cache:
                        raw,mime,_=_image(archive.read(item))
                        image_cache[path.casefold()]=_data_url(raw,mime)
                    href=image_cache[path.casefold()]
                    budget+=len(href)-old_size
                    element['href']=href
                # Persist only after the whole archive has been validated.
                return self.create(data.get('name') or data.get('meta',{}).get('name') or Path(name).stem, data)
        except (zipfile.BadZipFile, zipfile.LargeZipFile, UnicodeDecodeError, json.JSONDecodeError, RuntimeError, RecursionError, zlib.error, struct.error) as error:
            raise ValueError('画布项目包损坏或格式不支持') from error


class NewCanvasProject(BaseModel):
    name: str = Field(min_length=1,max_length=200)
    data: dict | None = None


def register_canvas_projects(app):
    service=CanvasProjects(app.state.store)
    app.state.canvas_projects=service
    router=APIRouter(prefix='/api')
    @router.get('/canvas-projects')
    def projects(): return {'items':service.list()}
    @router.post('/canvas-projects',status_code=201)
    def create(body:NewCanvasProject): return service.create(body.name,body.data)
    @router.post('/canvas-projects/import',status_code=201)
    def import_project(file:UploadFile=File(...)):
        try:
            if Path(file.filename or '').suffix.lower() not in {'.zip','.xzpack'}:
                raise ValueError('请选择ZIP或原XZPACK画布项目')
            raw=file.file.read(MAX_ARCHIVE_BYTES+1)
            return service.import_archive(raw,file.filename)
        finally: file.file.close()
    def download(identifier,images_only=False):
        data=app.state.store.get_canvas(identifier)
        name=safe_name(str(data.get('name') or _meta(data).get('name') or '设计画布'))+('-图片' if images_only else '')+'.zip'
        return Response(service.export(identifier,images_only=images_only),media_type='application/zip',
            headers={'Content-Disposition':f"attachment; filename=canvas.zip; filename*=UTF-8''{quote(name)}"})
    @router.get('/canvas/{identifier}/export.zip')
    @router.get('/canvas-projects/{identifier}/export.zip',include_in_schema=False)
    def export_project(identifier:str): return download(identifier)
    @router.get('/canvas/{identifier}/images.zip')
    @router.get('/canvas-projects/{identifier}/images.zip',include_in_schema=False)
    def export_images(identifier:str): return download(identifier,True)
    app.include_router(router)
