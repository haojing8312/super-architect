"""Explicit, capability-protected sharing of one inert canvas over a LAN.

The business API stays loopback-only. This owned listener has no business,
filesystem, runtime or SQL routes. Neither listener nor invite survives exit.
"""
from __future__ import annotations

import asyncio
from collections import deque
from contextlib import contextmanager, suppress
import copy
import hashlib
import importlib.util
import ipaddress
import json
import secrets
import socket
import time
from urllib.parse import urlsplit
import uuid

from fastapi import APIRouter, FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
import uvicorn

from .canvas_projects import CanvasProjects, MAX_JSON_BYTES, _native
from .store import now

PROTOCOL=1
MAX_MESSAGE=MAX_JSON_BYTES+4096
MAX_PEERS=8
PRIVATE_NETS=[ipaddress.ip_network(value) for value in ('10.0.0.0/8','172.16.0.0/12','192.168.0.0/16','fc00::/7')]
ELEMENT_KEYS={'id','type','x','y','w','h','color','text','href','points','groupId','fontFamily','fontSize','fontWeight','fontStyle'}
GROUP_KEYS={'id','groupId','GroupId','name','GroupName','fontSize','FontSize','fontFamily','fontWeight','fontStyle','color'}

def re_invite(value):
    return isinstance(value,str) and 20<=len(value)<=100 and all(char in 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-' for char in value)

def websocket_client():
    try:
        from websockets.asyncio.client import connect
    except ImportError as error:raise ValueError('画布协作需要本产品环境中的websockets>=15依赖') from error
    return connect

def private_address(value):
    try:
        address=ipaddress.ip_address(value)
        if isinstance(address,ipaddress.IPv6Address) and address.ipv4_mapped is not None:return False
        return address.is_loopback or any(address in network for network in PRIVATE_NETS)
    except ValueError:return False

def join_url(value):
    if not isinstance(value,str) or len(value)>250 or any(ord(char)<33 for char in value) or '%' in value or '\\' in value:
        raise ValueError('请输入literal私人LAN或回环IP的ws/wss地址')
    try:
        parsed=urlsplit(value);port=parsed.port;address=ipaddress.ip_address(parsed.hostname or '')
    except ValueError as error:raise ValueError('协作地址必须是literal IP及有效端口') from error
    if parsed.scheme not in {'ws','wss'} or parsed.username is not None or parsed.password is not None or parsed.query or parsed.fragment or parsed.path!='/ws' or not port or not private_address(str(address)):
        raise ValueError('协作地址仅支持私人LAN/回环IP、显式端口和/ws，不能使用公网/凭据/参数')
    host=f'[{address}]' if address.version==6 else str(address)
    canonical=f'{parsed.scheme}://{host}:{port}/ws'
    if value!=canonical:raise ValueError('协作地址不是规范literal IP URL')
    return canonical

def packet(raw,maximum=MAX_MESSAGE):
    if not isinstance(raw,str) or len(raw.encode('utf-8'))>maximum:raise ValueError('协作消息超过上限或不是文本')
    try:
        value=json.loads(raw,parse_constant=lambda _:(_ for _ in ()).throw(ValueError('协作JSON不接受非有限数值')))
    except (json.JSONDecodeError,RecursionError) as error:raise ValueError('协作JSON无效') from error
    if not isinstance(value,dict):raise ValueError('协作消息必须是对象')
    stack=[(value,0)]
    while stack:
        current,depth=stack.pop()
        if depth>40:raise ValueError('协作JSON嵌套过深')
        if isinstance(current,dict):stack.extend((item,depth+1) for item in current.values())
        elif isinstance(current,list):stack.extend((item,depth+1) for item in current)
    return value

def digest(document):
    return hashlib.sha256(json.dumps(document,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode('utf-8')).hexdigest()

def clean_document(projects,data,remote=False):
    """Whitelist transport fields before materializing; remote paths never read."""
    if not isinstance(data,dict) or not isinstance(data.get('elements'),list):raise ValueError('协作画布缺少elements')
    name=data.get('name') or '协作画布'
    if not isinstance(name,str) or len(name)>200 or any(ord(char)<32 for char in name):raise ValueError('协作画布名称无效')
    elements=[]
    for element in data['elements']:
        if not isinstance(element,dict):raise ValueError('协作元素无效')
        if remote and element.get('type')=='image' and (not isinstance(element.get('href'),str) or not element['href'].startswith('data:image/')):
            raise ValueError('远端协作图片必须内嵌合法位图，不能引用本机素材或任意路径')
        item={key:copy.deepcopy(value) for key,value in element.items() if key in ELEMENT_KEYS}
        if isinstance(item.get('points'),list):
            item['points']=[{key:point[key] for key in ('x','y') if key in point} if isinstance(point,dict) else point for point in item['points']]
        elements.append(item)
    viewport=data.get('viewport',{})
    if not isinstance(viewport,dict):raise ValueError('协作视口无效')
    meta=data.get('meta',{})
    if not isinstance(meta,dict):raise ValueError('协作分组元数据无效')
    groups=meta.get('groups',[])
    if not isinstance(groups,list) or len(groups)>10_000:raise ValueError('协作分组数量无效')
    cleaned_groups=[]
    for group in groups:
        if not isinstance(group,dict):raise ValueError('协作分组无效')
        item={key:copy.deepcopy(value) for key,value in group.items() if key in GROUP_KEYS}
        for key in ('id','groupId','GroupId','name','GroupName','fontFamily'):
            if key in item and (not isinstance(item[key],str) or len(item[key])>200 or any(ord(char)<32 for char in item[key])):raise ValueError('协作分组名称或标识无效')
        style={'id':'group-validation','type':'text','text':item.get('name',item.get('GroupName','')),
               'x':0,'y':0,'w':1,'h':1,'fontSize':item.get('fontSize',item.get('FontSize',16))}
        style.update({key:item[key] for key in ('fontFamily','fontWeight','fontStyle','color') if key in item})
        _native({'elements':[style],'viewport':{'x':0,'y':0,'zoom':1}})
        cleaned_groups.append(item)
    value={'name':name,'schema_version':1,'elements':elements,
           'viewport':{key:value for key,value in viewport.items() if key in {'x','y','zoom','zoomY'}},
           'meta':{'name':name,'schema_version':1,'groups':cleaned_groups}}
    return projects.materialize(value)

def merge_local_document(previous,shared):
    """Keep local-only metadata, never reintroduce removed shared fields/items."""
    def unknown(value,known):return {key:copy.deepcopy(item) for key,item in value.items() if key not in known} if isinstance(value,dict) else {}
    value={**unknown(previous,{'id','name','schema_version','elements','viewport','meta'}),**copy.deepcopy(shared)}
    old_elements={item.get('id'):item for item in previous.get('elements',[]) if isinstance(item,dict)}
    value['elements']=[{**unknown(old_elements.get(item['id'],{}),ELEMENT_KEYS),**item} for item in value['elements']]
    value['viewport']={**unknown(previous.get('viewport',{}),{'x','y','zoom','zoomY'}),**value['viewport']}
    old_meta=previous.get('meta',{});old_meta=old_meta if isinstance(old_meta,dict) else {}
    meta={**unknown(old_meta,{'name','schema_version','groups'}),**value['meta']}
    def group_key(group):return group.get('GroupId',group.get('groupId',group.get('id')))
    old_groups={group_key(group):group for group in old_meta.get('groups',[]) if isinstance(group,dict) and group_key(group) is not None}
    meta['groups']=[{**unknown(old_groups.get(group_key(group),{}),GROUP_KEYS),**group} for group in meta.get('groups',[])]
    value['meta']=meta
    return _native(value)

class OwnedServer(uvicorn.Server):
    @contextmanager
    def capture_signals(self):
        # A nested sharing server must not replace primary desktop signal hooks.
        yield

class CanvasCollaboration:
    def __init__(self,store,projects=None,watch_interval=.4):
        self.store=store;self.projects=projects or CanvasProjects(store);self.watch_interval=watch_interval
        self.peer_id=uuid.uuid4().hex;self.lock=asyncio.Lock();self.operation_lock=asyncio.Lock()
        self.role='none';self.status='idle';self.canvas_id=None;self.document=None;self.hash='';self.revision=0
        self.error='';self.conflict=None;self.local_hash='';self.updated_at='';self.pending=None
        self.peers={};self.invite_code='';self.bind_host='';self.port=None;self.share_urls=[]
        self.server=None;self.server_task=None;self.listener=None;self.watch_task=None;self.reader_task=None;self.socket=None
        self.join_settings=None;self.send_lock=asyncio.Lock();self.conflict_hashes={}
        self.last_ack_change_id=None;self.last_ack_revision=None;self.last_change_id=None;self.remote_peer_count=0

    def reset_idle_binding(self):
        """Forget private stopped-share metadata when the account epoch changes.

        The member lifecycle holds operation_lock and has checked role=none.
        Preserve that shared lock so later account/share operations still serialize.
        Canvas documents themselves remain in their original Store databases.
        """
        if self.role!='none':raise RuntimeError('Active collaboration must stop before account change')
        operation_lock=self.operation_lock
        fresh=CanvasCollaboration(self.store,self.projects,self.watch_interval)
        self.__dict__.update(fresh.__dict__)
        self.operation_lock=operation_lock

    def _check_account(self):
        if getattr(self.store,'closing',False) or (hasattr(self.store,'bound_epoch') and self.store.bound_epoch!=self.store.epoch):
            raise HTTPException(409,'会员工作区已改变，请重新打开客户端')

    def state(self,sender='',change_id=''):
        return {'type':'state','protocol':PROTOCOL,'revision':self.revision,'hash':self.hash,
                'document':self.document,'sender':sender or self.peer_id,'change_id':change_id,'peer_count':len(self.peers)}

    def status_payload(self,snapshot=False):
        value={'role':self.role,'mode':self.role,'status':self.status,'connected':self.status in {'hosting','connected'},
               'canvas_id':self.canvas_id,'revision':self.revision,'hash':self.hash,
               'peer_id':self.peer_id,'peer_count':len(self.peers) if self.role=='host' else self.remote_peer_count if self.status=='connected' else 0,
               'error':self.error,'conflict':self.conflict,
               'conflict_copy_id':self.conflict['canvas_id'] if self.conflict else None,
               'pending_change_id':self.pending['change_id'] if self.pending else None,
               'last_ack_change_id':self.last_ack_change_id,'last_ack_revision':self.last_ack_revision,'last_change_id':self.last_change_id,
               'pending':self.pending is not None,'pending_remote':False,'physical_lan_verified':False}
        if self.role=='host':value.update({'bind_host':self.bind_host,'port':self.port,'share_urls':self.share_urls,'urls':self.share_urls,'invite_code':self.invite_code})
        if snapshot:value['document']=self.document
        return value

    def _updated(self):
        if not self.canvas_id:return ''
        with self.store.connection() as db:
            row=db.execute('SELECT updated_at FROM canvases WHERE id=?',(self.canvas_id,)).fetchone()
        if row is None:raise KeyError('选定协作画布已删除')
        return row['updated_at']

    async def _document(self,data=None,remote=False):
        if remote and data is None:raise ValueError('远端协作缺少document')
        if data is None:data=await asyncio.to_thread(self.projects.snapshot,self.canvas_id)
        return await asyncio.to_thread(clean_document,self.projects,data,remote)

    async def _send(self,ws,message,lock=None):
        raw=json.dumps(message,ensure_ascii=False,allow_nan=False,separators=(',',':'))
        if len(raw.encode())>MAX_MESSAGE:raise ValueError('协作包超过上限')
        async with lock or self.send_lock:
            await asyncio.wait_for(ws.send_text(raw) if isinstance(ws,WebSocket) else ws.send(raw),5)

    async def _broadcast(self,message):
        for peer,(ws,lock) in list(self.peers.items()):
            try:await self._send(ws,message,lock)
            except (OSError,RuntimeError,TimeoutError):
                self.peers.pop(peer,None)
                with suppress(Exception):await ws.close(code=1011)

    def _compare_save(self,document,expected,local_document=None):
        # Store PUT/stdio can use other SQLite connections. A write transaction
        # closes the gap between checking updated_at and overwriting this single
        # manager-owned canvas. There is no general SQL or canvas-id parameter
        # on the sharing listener. Do not call a second connection inside it.
        with self.store.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT updated_at,data FROM canvases WHERE id=?',(self.canvas_id,)).fetchone()
            if row is None:raise KeyError('选定协作画布已删除')
            if row['updated_at']!=expected:return None
            supplied=merge_local_document(local_document,document) if local_document is not None else document
            merged=merge_local_document(json.loads(row['data']),supplied)
            timestamp=now();encoded=self.store._json({**merged,'id':self.canvas_id})
            db.execute('UPDATE canvases SET data=?,updated_at=? WHERE id=?',(encoded,timestamp,self.canvas_id))
            return timestamp

    async def _save(self,document,expected=None,local_document=None):
        timestamp=await asyncio.to_thread(self._compare_save,document,self.updated_at if expected is None else expected,local_document)
        if timestamp is None:return False
        self.local_hash=digest(document);self.updated_at=timestamp;return True

    async def _preserve(self,document,reason,change_id='',force=False):
        key=digest(document)
        if key==self.hash and not force:return
        if key not in self.conflict_hashes:
            copy_value=await asyncio.to_thread(self.projects.create,(document.get('name') or '协作画布')[:175]+'（协作冲突副本）',document)
            self.conflict_hashes[key]=copy_value['id']
            if len(self.conflict_hashes)>128:self.conflict_hashes.pop(next(iter(self.conflict_hashes)))
        self.conflict={'canvas_id':self.conflict_hashes[key],'reason':reason,'change_id':change_id,'hash':key}
        self.error=reason

    async def _refresh_host(self):
        changed=await asyncio.to_thread(self._updated)
        if changed==self.updated_at:return
        document=await self._document();value=digest(document);self.updated_at=changed
        if value==self.hash:return
        self.document=document;self.hash=value;self.local_hash=value;self.revision+=1
        self.last_change_id='local-'+uuid.uuid4().hex
        await self._broadcast(self.state(change_id=self.last_change_id))

    async def _host_update(self,document,base_revision,change_id,sender,local_document=None):
        async with self.lock:
            await self._refresh_host()
            if base_revision!=self.revision:return {'type':'conflict','change_id':change_id,'state':self.state()}
            value=digest(document)
            if value!=self.hash or local_document is not None:
                if not await self._save(document,local_document=local_document):
                    await self._refresh_host();return {'type':'conflict','change_id':change_id,'state':self.state()}
                if value!=self.hash:self.document=document;self.hash=value;self.revision+=1
            self.last_change_id=change_id
            if sender==self.peer_id:self.last_ack_change_id=change_id;self.last_ack_revision=self.revision
            state=self.state(sender,change_id);await self._broadcast(state);return state

    async def _validated_state(self,message):
        if message.get('protocol')!=PROTOCOL:raise ValueError('协作协议版本无效')
        revision=message.get('revision')
        if isinstance(revision,bool) or not isinstance(revision,int) or not 0<=revision<=10**12:raise ValueError('协作版本无效')
        if not isinstance(message.get('sender',''),str) or not isinstance(message.get('change_id',''),str) or len(message.get('change_id',''))>100:raise ValueError('协作发送者/变更标识无效')
        peers=message.get('peer_count',0)
        if isinstance(peers,bool) or not isinstance(peers,int) or not 0<=peers<=MAX_PEERS:raise ValueError('协作人数无效')
        document=await self._document(message.get('document'),True);value=digest(document)
        if value!=message.get('hash'):raise ValueError('协作内容hash不匹配')
        return revision,document,value

    async def _accept_state(self,message):
        revision,document,value=await self._validated_state(message)
        async with self.lock:
            if self.document is not None and revision<self.revision:raise ValueError('协作版本不能倒退')
            observed_at=await asyncio.to_thread(self._updated) if self.canvas_id else ''
            local=await self._document() if self.canvas_id else None
            local_digest=digest(local) if local else ''
            if self.pending and digest(self.pending['document'])!=value:
                await self._preserve(self.pending.get('local_document',self.pending['document']),'主机已有其他修改，提交内容已保留为冲突副本',self.pending['change_id'])
            elif local is not None and local_digest!=self.local_hash and local_digest!=value:
                await self._preserve(local,'本机已保存的修改与主机版本冲突，已保留副本')
            if self.canvas_id:
                for _ in range(3):
                    if await self._save(document,observed_at):break
                    observed_at=await asyncio.to_thread(self._updated);changed=await self._document()
                    if digest(changed)!=value:await self._preserve(changed,'本机并发保存与远端更新冲突，已保留副本')
                else:raise ValueError('本机持续保存，远端更新未覆盖本机内容')
            if self.pending and digest(self.pending['document'])==value:self.last_ack_change_id=self.pending['change_id'];self.last_ack_revision=revision
            elif message.get('sender')==self.peer_id:self.last_ack_change_id=message.get('change_id') or None;self.last_ack_revision=revision
            self.last_change_id=message.get('change_id') or None
            self.remote_peer_count=message.get('peer_count',0)
            self.pending=None;self.document=document;self.hash=value;self.revision=revision

    async def _watch(self):
        try:
            while True:
                await asyncio.sleep(self.watch_interval)
                if self.role=='host':
                    async with self.lock:await self._refresh_host()
                elif self.role=='guest' and self.status=='connected' and self.pending is None:
                    await self._watch_guest()
        except asyncio.CancelledError:raise
        except Exception as error:
            self.error=f'协作监测停止：{type(error).__name__}';self.status='error'

    async def _watch_guest(self):
        # Match public publish's lifecycle lock order. A remote state uses the
        # canvas lock, so sample its revision together with the Store snapshot.
        async with self.operation_lock:
            self._check_account()
            async with self.lock:
                if self.role!='guest' or self.status!='connected' or self.pending is not None:return
                changed=await asyncio.to_thread(self._updated)
                if changed==self.updated_at:return
                document=await self._document()
                if digest(document)==self.local_hash:
                    self.updated_at=changed;return
                base_revision=self.revision
            try:
                await self._publish(base_revision,document,'watch-'+uuid.uuid4().hex)
            except HTTPException as error:
                # _publish preserves rejected local content. A remote state
                # can advance during validation before it reacquires self.lock;
                # the resulting CAS conflict must not terminate monitoring.
                if error.status_code!=409:raise

    async def start_host(self,canvas_id,bind_host='0.0.0.0',port=33822):
        async with self.operation_lock:
            self._check_account()
            if self.role!='none':raise HTTPException(409,'请先停止分享或断开当前协作')
            if not isinstance(canvas_id,str) or not canvas_id or len(canvas_id)>200:raise ValueError('画布ID无效')
            if not isinstance(bind_host,str) or (bind_host!='0.0.0.0' and not private_address(bind_host)):raise ValueError('分享仅绑定本机/私人LAN接口')
            if isinstance(port,bool) or not isinstance(port,int) or not 0<=port<=65535:raise ValueError('分享端口无效')
            websocket_client()
            snapshot=await asyncio.to_thread(self.projects.snapshot,canvas_id);document=await self._document(snapshot)
            listener=socket.socket(socket.AF_INET6 if ':' in bind_host else socket.AF_INET,socket.SOCK_STREAM)
            try:
                listener.bind((bind_host,port));listener.listen(16);listener.setblocking(False)
            except OSError:
                listener.close();raise ValueError('分享端口被占用或不是可绑定的本机地址') from None
            self.peer_id=uuid.uuid4().hex
            self.role='host';self.status='starting';self.canvas_id=canvas_id;self.document=document;self.hash=digest(document);self.local_hash=self.hash;self.revision=0
            self.last_ack_change_id=None;self.last_ack_revision=None;self.last_change_id=None
            self.error='';self.conflict=None;self.conflict_hashes.clear();self.invite_code=secrets.token_urlsafe(32)
            self.bind_host=bind_host;self.port=listener.getsockname()[1];self.listener=listener;self.updated_at=await asyncio.to_thread(self._updated)
            share=FastAPI(openapi_url=None,docs_url=None,redoc_url=None)
            @share.get('/')
            def public_status():return {'product':'architecture-canvas-share','protocol':PROTOCOL}
            @share.websocket('/ws')
            async def peer(ws:WebSocket):await self._serve_peer(ws)
            websocket_protocol='websockets-sansio' if importlib.util.find_spec('uvicorn.protocols.websockets.websockets_sansio_impl') else 'websockets'
            config=uvicorn.Config(share,host=bind_host,port=self.port,log_level='error',access_log=False,ws=websocket_protocol,proxy_headers=False,
                ws_max_size=MAX_MESSAGE,ws_max_queue=2,ws_per_message_deflate=False,limit_concurrency=20,timeout_graceful_shutdown=2)
            self.server=OwnedServer(config);self.server_task=asyncio.create_task(self.server.serve(sockets=[listener]))
            try:
                for _ in range(100):
                    if self.server.started:break
                    if self.server_task.done():await self.server_task;raise RuntimeError('分享服务未启动')
                    await asyncio.sleep(.03)
                if not self.server.started:raise RuntimeError('分享服务启动超时')
            except BaseException:
                await self._close_host();self.role='none';self.status='error';self.invite_code='';raise
            addresses=[bind_host] if bind_host!='0.0.0.0' else await asyncio.to_thread(self._local_ips)
            self.share_urls=[f"ws://{'['+address+']' if ':' in address else address}:{self.port}/ws" for address in addresses]
            self.status='hosting';self.watch_task=asyncio.create_task(self._watch());return self.status_payload()

    @staticmethod
    def _local_ips():
        try:
            values={row[4][0] for row in socket.getaddrinfo(socket.gethostname(),None) if row[0]==socket.AF_INET and private_address(row[4][0])}
        except OSError:values=set()
        return sorted(values-{'::1','127.0.0.1'}) or ['127.0.0.1']

    async def _serve_peer(self,ws):
        peer_id=None
        try:
            if ws.query_params or not private_address(ws.client.host if ws.client else ''):await ws.close(code=4403);return
            origin=ws.headers.get('origin')
            if origin and urlsplit(origin).hostname!='localhost' and not private_address(urlsplit(origin).hostname or ''):await ws.close(code=4403);return
            await ws.accept()
            auth=packet(await asyncio.wait_for(ws.receive_text(),5),1024)
            code=auth.get('invite_code','');peer_id=auth.get('peer_id')
            if auth.get('type')!='auth' or auth.get('protocol')!=PROTOCOL or not isinstance(code,str) or not re_invite(code) or not secrets.compare_digest(code,self.invite_code):await ws.close(code=4401);return
            if not isinstance(peer_id,str) or len(peer_id)!=32 or any(char not in '0123456789abcdef' for char in peer_id) or peer_id==self.peer_id:await ws.close(code=4400);return
            send_lock=asyncio.Lock()
            async with self.lock:
                if peer_id in self.peers or len(self.peers)>=MAX_PEERS:await ws.close(code=4409);return
                await self._refresh_host();self.peers[peer_id]=(ws,send_lock);await self._send(ws,self.state(),send_lock)
                await self._broadcast({'type':'peers','protocol':PROTOCOL,'peer_count':len(self.peers)})
            recent=deque()
            while self.role=='host':
                message=packet(await ws.receive_text())
                stamp=time.monotonic();recent.append(stamp)
                while recent and stamp-recent[0]>1:recent.popleft()
                if len(recent)>10:raise ValueError('协作提交过于频繁')
                if message.get('type')!='update':raise ValueError('不支持该协作消息')
                base,change=self._update_fields(message)
                document=await self._document(message.get('document'),True)
                response=await self._host_update(document,base,change,peer_id)
                if response['type']=='conflict':await self._send(ws,response,send_lock)
        except (WebSocketDisconnect,asyncio.CancelledError):pass
        except (ValueError,KeyError,OSError,TimeoutError,RuntimeError):
            with suppress(Exception):await ws.close(code=4400)
        finally:
            if peer_id and self.peers.get(peer_id,(None,))[0] is ws:
                self.peers.pop(peer_id,None)
                if self.role=='host':
                    async with self.lock:await self._broadcast({'type':'peers','protocol':PROTOCOL,'peer_count':len(self.peers)})

    @staticmethod
    def _update_fields(value):
        base=value.get('base_revision');change=value.get('change_id') or uuid.uuid4().hex
        if isinstance(base,bool) or not isinstance(base,int) or not 0<=base<=10**12:raise ValueError('base_revision必须是非负版本整数')
        if not isinstance(change,str) or len(change)>100 or any(ord(char)<33 for char in change):raise ValueError('协作change_id无效')
        return base,change

    async def join(self,url,invite_code,name=None,reconnect=False):
        async with self.operation_lock:
            self._check_account()
            if self.role=='host' or self.status=='connected':raise HTTPException(409,'请先停止分享或断开当前连接')
            url=join_url(url)
            if not isinstance(invite_code,str) or not re_invite(invite_code):raise ValueError('邀请码格式无效')
            if name is not None and (not isinstance(name,str) or not name.strip() or len(name)>175):raise ValueError('加入副本名称无效')
            connect=websocket_client()
            connecting_peer_id=self.peer_id if reconnect else uuid.uuid4().hex
            ws=None
            try:
                await self._cancel_task(self.watch_task);self.watch_task=None
                await self._cancel_task(self.reader_task);self.reader_task=None
                if self.socket:
                    with suppress(Exception):await self.socket.close()
                    self.socket=None
                ws=await connect(url,proxy=None,compression=None,max_size=MAX_MESSAGE,max_queue=2,open_timeout=5,close_timeout=2)
                await self._send(ws,{'type':'auth','protocol':PROTOCOL,'invite_code':invite_code,'peer_id':connecting_peer_id})
                message=packet(await asyncio.wait_for(ws.recv(),5))
                if message.get('type')!='state' or message.get('protocol')!=PROTOCOL:raise ValueError('主机未返回可接受的画布状态')
                _,initial_document,_=await self._validated_state(message)
                if not reconnect:
                    document=initial_document
                    value=await asyncio.to_thread(self.projects.create,name or (document.get('name') or '协作画布')[:175]+'（协作副本）',document)
                    self.canvas_id=value['id'];self.document=None;self.hash='';self.local_hash=digest(await self._document(value));self.revision=0
                    self.last_ack_change_id=None;self.last_ack_revision=None;self.last_change_id=None
                    self.conflict=None;self.conflict_hashes.clear()
                self.peer_id=connecting_peer_id
                self.role='guest';self.socket=ws;self.error='';self.join_settings={'url':url,'invite_code':invite_code,'name':name}
                await self._accept_state(message)
                self.status='connected';self.reader_task=asyncio.create_task(self._read_guest());self.watch_task=asyncio.create_task(self._watch())
                return self.status_payload()
            except Exception as error:
                if ws:
                    with suppress(Exception):await ws.close()
                self.status='disconnected';self.error=f'连接未成功：{type(error).__name__}'
                raise ValueError('画布连接失败，请核对主机地址/邀请码/监听状态') from error

    async def _read_guest(self):
        try:
            async for raw in self.socket:
                message=packet(raw)
                if message.get('type')=='state':await self._accept_state(message)
                elif message.get('type')=='conflict':await self._accept_state(message['state'])
                elif message.get('type')=='peers':
                    count=message.get('peer_count')
                    if message.get('protocol')!=PROTOCOL or isinstance(count,bool) or not isinstance(count,int) or not 0<=count<=MAX_PEERS:raise ValueError('协作人数消息无效')
                    self.remote_peer_count=count
                else:raise ValueError('主机协作消息无效')
        except asyncio.CancelledError:raise
        except Exception as error:self.error=f'连接已断开：{type(error).__name__}'
        finally:
            if self.status=='connected':self.status='disconnected'

    async def publish(self,base_revision,document,change_id=None,canvas_id=None,peer_id=None):
        # Context transitions (host/join/stop/reconnect) use this same lock.
        # Check after acquiring it, not from an earlier browser GET receipt.
        async with self.operation_lock:
            self._check_account()
            if (canvas_id is not None and canvas_id!=self.canvas_id) or (peer_id is not None and peer_id!=self.peer_id):
                if not isinstance(document,dict):raise ValueError('协作提交须提供document对象')
                submitted=copy.deepcopy(document);await asyncio.to_thread(_native,submitted)
                _,change=self._update_fields({'base_revision':base_revision,'change_id':change_id})
                await self._preserve(submitted,'协作对象已切换，本次提交未写入新画布，已保留本机副本',change,force=True)
                raise HTTPException(409,self.status_payload())
            return await self._publish(base_revision,document,change_id)

    async def _publish(self,base_revision,document,change_id=None):
        base,change=self._update_fields({'base_revision':base_revision,'change_id':change_id})
        if not isinstance(document,dict):raise ValueError('协作提交须提供document对象')
        local_document=copy.deepcopy(document);await asyncio.to_thread(_native,local_document)
        document=await self._document(document)
        if self.role=='host':
            response=await self._host_update(document,base,change,self.peer_id,local_document)
            if response['type']=='conflict':await self._preserve(local_document,'主机版本已变化，提交未接受',change);raise HTTPException(409,self.status_payload())
            return {**self.status_payload(),'accepted_revision':response['revision'],'change_id':change}
        if self.role!='guest' or self.status!='connected':raise HTTPException(409,'当前未连接画布主机，本机内容未提交')
        async with self.lock:
            if base!=self.revision:
                await self._preserve(local_document,'协作版本已变化，提交未接受',change);raise HTTPException(409,self.status_payload())
            if self.pending:
                await self._preserve(local_document,'已有提交等待确认，第二份提交未接受，已保留副本',change)
                raise HTTPException(409,self.status_payload())
            if digest(document)==self.hash:
                observed=await asyncio.to_thread(self._updated)
                if not await self._save(document,observed,local_document):
                    await self._preserve(local_document,'本机保存版本同时变化，本次提交未接受',change)
                    raise HTTPException(409,self.status_payload())
                self.last_ack_change_id=change;self.last_ack_revision=self.revision
                return {**self.status_payload(),'accepted_revision':self.revision,'change_id':change}
            self.pending={'document':document,'local_document':local_document,'base_revision':base,'change_id':change}
            observed=await asyncio.to_thread(self._updated)
            if observed!=self.updated_at:
                local=await self._document()
                if digest(local)!=self.local_hash and digest(local)!=digest(document):await self._preserve(local,'本机存在另一份已保存修改，提交前已保留副本')
            if not await self._save(document,observed,local_document):
                self.pending=None;await self._preserve(local_document,'本机保存版本同时变化，本次提交未接受',change)
                raise HTTPException(409,self.status_payload())
            try:await self._send(self.socket,{'type':'update','document':document,'base_revision':base,'change_id':change})
            except Exception:
                self.status='disconnected';self.error='连接已断开，修改留在本机画布，尚未获主机确认';raise HTTPException(409,self.status_payload()) from None
            return {**self.status_payload(),'accepted_revision':None,'change_id':change}

    async def _cancel_task(self,task):
        if task and task is not asyncio.current_task():
            task.cancel()
            with suppress(asyncio.CancelledError):await task

    async def disconnect(self):
        async with self.operation_lock:
            self._check_account()
            if self.role=='host':raise HTTPException(409,'主机请使用停止分享')
            self.status='disconnected'
            await self._cancel_task(self.watch_task);self.watch_task=None
            if self.socket:
                with suppress(Exception):await self.socket.close()
                self.socket=None
            await self._cancel_task(self.reader_task);self.reader_task=None
            return self.status_payload()

    async def reconnect(self):
        if self.role!='guest' or not self.join_settings:raise HTTPException(409,'没有可重连的客端协作')
        await self.disconnect()
        return await self.join(**self.join_settings,reconnect=True)

    async def _close_host(self):
        await self._cancel_task(self.watch_task);self.watch_task=None
        for ws,_ in list(self.peers.values()):
            with suppress(Exception):await ws.close(code=1001)
        self.peers.clear()
        if self.server:self.server.should_exit=True
        if self.server_task:
            try:await asyncio.wait_for(self.server_task,4)
            except TimeoutError:self.server_task.cancel();await self._cancel_task(self.server_task)
            except Exception:pass
        if self.listener:self.listener.close()
        self.server=None;self.server_task=None;self.listener=None

    async def stop(self,force=False):
        async with self.operation_lock:
            if not force:self._check_account()
            if self.role=='host':await self._close_host()
            elif self.role=='guest':
                await self._cancel_task(self.watch_task)
                if self.socket:
                    with suppress(Exception):await self.socket.close()
                await self._cancel_task(self.reader_task)
                self.watch_task=None;self.reader_task=None;self.socket=None
            self.role='none';self.status='stopped';self.invite_code='';self.join_settings=None;self.pending=None
            return self.status_payload()

async def local_body(request):
    raw=bytearray()
    async for block in request.stream():
        raw.extend(block)
        if len(raw)>MAX_MESSAGE:raise HTTPException(413,'协作请求超过15 MB')
    try:return packet(raw.decode('utf-8')) if raw else {}
    except (ValueError,UnicodeDecodeError) as error:raise HTTPException(422,str(error)) from error

def register_canvas_collaboration(app):
    manager=CanvasCollaboration(app.state.store,getattr(app.state,'canvas_projects',None))
    app.state.canvas_collaboration=manager;app.router.add_event_handler('shutdown',manager.stop)
    router=APIRouter(prefix='/api/canvas-collaboration')
    @router.get('')
    async def status():return manager.status_payload()
    @router.get('/snapshot')
    async def snapshot():return manager.status_payload(snapshot=True)
    @router.post('/host')
    async def host(request:Request):
        value=await local_body(request);return await manager.start_host(value.get('canvas_id'),value.get('bind_host','0.0.0.0'),value.get('port',33822))
    @router.post('/stop')
    async def stop():return await manager.stop()
    @router.post('/join')
    async def join(request:Request):
        value=await local_body(request);return await manager.join(value.get('url'),value.get('invite_code'),value.get('name'))
    @router.post('/disconnect')
    async def disconnect():return await manager.disconnect()
    @router.post('/reconnect')
    async def reconnect():return await manager.reconnect()
    @router.post('/publish')
    async def publish(request:Request):
        value=await local_body(request)
        return await manager.publish(value.get('base_revision'),value.get('document'),value.get('change_id'),value.get('canvas_id'),value.get('peer_id'))
    app.include_router(router)
    return manager
