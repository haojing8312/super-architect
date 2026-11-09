"""Policy checks and actual loopback HTTP/WebSocket host with two guest peers."""
from __future__ import annotations

import asyncio
import base64
import copy
import io
import json
import socket
import uuid

from fastapi import FastAPI
from fastapi.responses import JSONResponse
import httpx
from PIL import Image
import pytest
import uvicorn
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed

from architecture_workbench.canvas_collaboration import (CanvasCollaboration,OwnedServer,clean_document,digest,join_url,packet,
    register_canvas_collaboration,MAX_MESSAGE,merge_local_document)
from architecture_workbench.canvas_projects import CanvasProjects
from architecture_workbench.security import register_security
from architecture_workbench.store import Store

@pytest.mark.parametrize('url',['ws://127.0.0.1:33822/ws','ws://192.168.2.5:33822/ws','wss://10.1.2.3:444/ws','ws://172.16.4.2:1234/ws','ws://[::1]:33822/ws','ws://[fd01::2]:33822/ws'])
def test_literal_private_join_urls(url):assert join_url(url)==url

@pytest.mark.parametrize('url',['ws://8.8.8.8:80/ws','ws://169.254.1.2:1234/ws','ws://[fe80::1]:33822/ws','ws://localhost:33822/ws',
    'ws://example.com:1234/ws','ws://127.0.0.1:33822/ws?token=secret','ws://u:p@127.0.0.1:33822/ws','http://127.0.0.1:33822/ws',
    'ws://127.0.0.1/ws','ws://127.0.0.1:33822/other','ws://127.0.0.1:33822/ws#x','ws://127.1:33822/ws',
    'ws://192.0.2.2:33822/ws','ws://[::ffff:127.0.0.1]:33822/ws','ws://127.0.0.1:33822/%77s','ws://127.0.0.1:33822/ws\n'])
def test_untrusted_join_urls_are_rejected(url):
    with pytest.raises(ValueError):join_url(url)

def document(x=0):
    buffer=io.BytesIO();Image.new('RGB',(3,2),'#608080').save(buffer,format='PNG')
    return {'name':'独立协作测试','schema_version':1,'viewport':{'x':3,'y':4,'zoom':1,'zoomY':1.5},
        'elements':[{'id':'rectangle','type':'rect','x':x,'y':10,'w':40,'h':20,'color':'#405060','groupId':'test-group'},
            {'id':'image','type':'image','x':80,'y':20,'w':30,'h':20,'color':'#405060','href':'data:image/png;base64,'+base64.b64encode(buffer.getvalue()).decode(),'groupId':'test-group'}],
        'meta':{'groups':[{'GroupId':'test-group','GroupName':'测试组标题','FontSize':28}]}}

def test_remote_images_cannot_resolve_local_assets_and_transport_is_whitelisted(tmp_path):
    projects=CanvasProjects(Store(tmp_path/'data'));original=document();original['meta']['private_path']='C:/private/test';original['elements'][0]['sql']='SELECT 1'
    safe=clean_document(projects,original,True)
    assert 'private_path' not in safe['meta'] and 'sql' not in safe['elements'][0]
    assert safe['meta']['groups']==original['meta']['groups'] and safe['viewport']['zoomY']==1.5
    for href in ['/api/files/'+'a'*32,'C:/private/test.png','https://example.com/test.png','data:image/svg+xml;base64,PHN2Zz4=']:
        value=document();value['elements'][1]['href']=href
        with pytest.raises(ValueError):clean_document(projects,value,True)
    assert projects.list()==[]  # materialization never creates DB project rows

def test_json_size_depth_geometry_style_and_invalid_raster_limits(tmp_path):
    with pytest.raises(ValueError):packet('{}'+' '*MAX_MESSAGE)
    with pytest.raises(ValueError):packet('{"x":NaN}')
    with pytest.raises(ValueError):packet('{"x":'+('['*42)+'0'+(']'*42)+'}')
    projects=CanvasProjects(Store(tmp_path/'data'))
    for kind in ('geometry','style','raster','group'):
        value=document()
        if kind=='geometry':value['elements'][0]['x']=float('inf')
        elif kind=='style':value['elements'][0]['color']='url(https://example.com)'
        elif kind=='raster':value['elements'][1]['href']='data:image/png;base64,'+base64.b64encode(b'not a PNG').decode()
        else:value['meta']['groups'][0]['FontSize']=float('nan')
        with pytest.raises(ValueError):clean_document(projects,value,True)

def app_for(store):
    app=FastAPI();app.state.store=store;register_security(app);register_canvas_collaboration(app)
    @app.exception_handler(ValueError)
    async def value_error(_request,error):return JSONResponse({'detail':str(error)},status_code=422)
    return app

async def start_api(app):
    listener=socket.socket();listener.bind(('127.0.0.1',0));listener.listen(16);listener.setblocking(False)
    server=OwnedServer(uvicorn.Config(app,log_level='error',access_log=False,timeout_graceful_shutdown=2))
    task=asyncio.create_task(server.serve(sockets=[listener]))
    for _ in range(100):
        if server.started:break
        await asyncio.sleep(.03)
    assert server.started
    client=httpx.AsyncClient(base_url=f'http://127.0.0.1:{listener.getsockname()[1]}',trust_env=False,timeout=8)
    client.headers['X-Workbench-Token']=(await client.get('/api/bootstrap')).json()['token']
    return server,task,listener,client

async def until(predicate,timeout=5):
    deadline=asyncio.get_running_loop().time()+timeout
    while not predicate():
        assert asyncio.get_running_loop().time()<deadline,'Canvas peers did not reach expected state'
        await asyncio.sleep(.03)

def test_actual_loopback_http_two_peers_sync_conflict_reconnect_watcher_and_owned_stop(tmp_path):
    asyncio.run(two_peer_roundtrip(tmp_path))

async def two_peer_roundtrip(tmp_path):
    stores=[Store(tmp_path/f'data-{n}') for n in range(3)];apps=[app_for(store) for store in stores]
    managers=[app.state.canvas_collaboration for app in apps]
    projects=[CanvasProjects(store) for store in stores]
    originals=[service.create(f'保留原项目{n}',document(n)) for n,service in enumerate(projects)]
    host_local=stores[0].get_canvas(originals[0]['id']);host_local['meta']['imported_from']='LOCAL_PRIVATE_METADATA'
    host_local['meta']['groups'][0]['legacyLabel']='LOCAL_PRIVATE_METADATA';host_local['elements'][0]['legacyNote']='LOCAL_PRIVATE_METADATA'
    stores[0].save_canvas(originals[0]['id'],host_local)
    snapshot_originals=[stores[n].get_canvas(originals[n]['id']) for n in range(3)]
    apis=[]
    try:
        for app in apps:apis.append(await start_api(app))
        clients=[api[3] for api in apis];prefix='/api/canvas-collaboration'
        # No nonce means no collaboration mutation on the primary API.
        denied=await clients[0].post(prefix+'/host',headers={'X-Workbench-Token':''},json={'canvas_id':originals[0]['id'],'bind_host':'127.0.0.1','port':0})
        assert denied.status_code==401 and managers[0].role=='none'
        started=await clients[0].post(prefix+'/host',json={'canvas_id':originals[0]['id'],'bind_host':'127.0.0.1','port':0})
        assert started.status_code==200
        host=started.json();port=host['port'];url=f'ws://127.0.0.1:{port}/ws';code=host['invite_code']
        assert host['mode']=='host' and host['connected'] and len(code)>=40 and code not in url
        async with httpx.AsyncClient(base_url=f'http://127.0.0.1:{port}',trust_env=False) as public:
            assert (await public.get('/')).json()=={'product':'architecture-canvas-share','protocol':1}
            for forbidden in ['/api/settings','/api/knowledge','/api/canvas/'+originals[0]['id'],'/images/test.png','/upload','/docs']:
                assert (await public.get(forbidden)).status_code==404
        async with connect(url,proxy=None) as untrusted:
            await untrusted.send(json.dumps({'type':'auth','protocol':1,'peer_id':uuid.uuid4().hex,'invite_code':'x'*43}))
            with pytest.raises(ConnectionClosed):await untrusted.recv()
        for n in (1,2):
            fresh_peer=managers[n].peer_id
            reply=await clients[n].post(prefix+'/join',json={'url':url,'invite_code':code})
            assert reply.status_code==200,reply.text
            assert reply.json()['canvas_id']!=originals[n]['id'] and reply.json()['mode']=='guest'
            assert reply.json()['peer_id']!=fresh_peer
            assert stores[n].get_canvas(originals[n]['id'])==snapshot_originals[n]
            assert managers[n].document['meta']['groups'][0]['GroupName']=='测试组标题'
        await until(lambda:len(managers[0].peers)==2)
        await until(lambda:all(manager.status_payload()['peer_count']==2 for manager in managers))
        assert all('LOCAL_PRIVATE_METADATA' not in json.dumps(manager.document) for manager in managers)
        guest_local=stores[1].get_canvas(managers[1].canvas_id);guest_local['meta']['imported_from']='GUEST_LOCAL_METADATA'
        stores[1].save_canvas(managers[1].canvas_id,guest_local)
        initial=copy.deepcopy(managers[0].document);updated=copy.deepcopy(initial);updated['elements'][0]['x']=10
        wrong_scope=await clients[0].post(prefix+'/publish',json={'base_revision':0,'document':document(444),'change_id':'wrong-scope',
            'canvas_id':originals[1]['id'],'peer_id':managers[0].peer_id})
        assert wrong_scope.status_code==409 and managers[0].revision==0
        assert stores[0].get_canvas(originals[0]['id'])['elements'][0]['x']==0
        assert stores[0].get_canvas(wrong_scope.json()['detail']['conflict_copy_id'])['elements'][0]['x']==444
        reply=await clients[0].post(prefix+'/publish',json={'base_revision':0,'document':updated,'change_id':'host-first'})
        assert reply.status_code==200 and reply.json()['accepted_revision']==1 and reply.json()['last_ack_revision']==1
        await until(lambda:all(manager.revision==1 for manager in managers))
        assert all(manager.document['elements'][0]['x']==10 for manager in managers)
        retained=stores[0].get_canvas(originals[0]['id'])
        assert retained['meta']['imported_from']==retained['meta']['groups'][0]['legacyLabel']==retained['elements'][0]['legacyNote']=='LOCAL_PRIVATE_METADATA'
        assert stores[1].get_canvas(managers[1].canvas_id)['meta']['imported_from']=='GUEST_LOCAL_METADATA'
        stale=copy.deepcopy(initial);stale['elements'][0]['x']=999
        conflict=await clients[1].post(prefix+'/publish',json={'base_revision':0,'document':stale,'change_id':'stale'})
        assert conflict.status_code==409
        copy_id=conflict.json()['detail']['conflict_copy_id']
        assert stores[1].get_canvas(copy_id)['elements'][0]['x']==999
        assert managers[0].document['elements'][0]['x']==10
        changed=copy.deepcopy(managers[1].document);changed['elements'][0]['x']=33
        added=copy.deepcopy(changed['elements'][0]);added['id']='duplicated-local';added['legacyNote']='NEW_LOCAL_PRIVATE_METADATA'
        changed['elements'].append(added);changed['meta']['groups'][0]['legacyLabel']='NEW_LOCAL_PRIVATE_METADATA'
        reply=await clients[1].post(prefix+'/publish',json={'base_revision':1,'document':changed,'change_id':'guest-one'})
        assert reply.status_code==200
        await until(lambda:all(manager.revision==2 for manager in managers) and managers[1].pending is None)
        assert managers[1].last_ack_change_id=='guest-one' and managers[1].last_ack_revision==2
        assert all(manager.document['elements'][0]['x']==33 for manager in managers)
        assert all('NEW_LOCAL_PRIVATE_METADATA' not in json.dumps(manager.document) for manager in managers)
        persisted=stores[1].get_canvas(managers[1].canvas_id)
        assert persisted['elements'][-1]['legacyNote']==persisted['meta']['groups'][0]['legacyLabel']=='NEW_LOCAL_PRIVATE_METADATA'
        metadata_only=copy.deepcopy(persisted);metadata_only['meta']['newLocalField']='METADATA_ONLY_PRIVATE'
        local_ack=await clients[1].post(prefix+'/publish',json={'base_revision':2,'document':metadata_only,'change_id':'metadata-only'})
        assert local_ack.status_code==200 and local_ack.json()['accepted_revision']==2
        assert stores[1].get_canvas(managers[1].canvas_id)['meta']['newLocalField']=='METADATA_ONLY_PRIVATE'
        # Changes from ordinary Store PUT/stdio are detected without Store hooks.
        local=stores[0].get_canvas(originals[0]['id']);local['elements'][0]['x']=72;stores[0].save_canvas(originals[0]['id'],local)
        await until(lambda:all(manager.revision==3 for manager in managers))
        assert managers[1].last_ack_revision==2  # a newer peer/local state must not forge our ACK base
        local=stores[2].get_canvas(managers[2].canvas_id);local['elements'][0]['x']=94;stores[2].save_canvas(managers[2].canvas_id,local)
        await until(lambda:all(manager.revision==4 for manager in managers))
        await asyncio.sleep(.9)
        assert all(manager.revision==4 for manager in managers)  # no save/echo loop
        assert (await clients[2].post(prefix+'/disconnect')).json()['status']=='disconnected'
        await until(lambda:len(managers[0].peers)==1)
        changed=copy.deepcopy(managers[0].document);changed['elements'][0]['x']=115
        assert (await clients[0].post(prefix+'/publish',json={'base_revision':4,'document':changed,'change_id':'while-offline'})).status_code==200
        offline=stores[2].get_canvas(managers[2].canvas_id);offline['elements'][0]['x']=123;stores[2].save_canvas(managers[2].canvas_id,offline)
        joined_id=managers[2].canvas_id
        reconnect_peer=managers[2].peer_id
        reconnect=await clients[2].post(prefix+'/reconnect')
        assert reconnect.status_code==200 and reconnect.json()['canvas_id']==joined_id
        assert reconnect.json()['peer_id']==reconnect_peer
        assert managers[2].revision==5 and managers[2].document['elements'][0]['x']==115
        assert stores[2].get_canvas(reconnect.json()['conflict_copy_id'])['elements'][0]['x']==123
        snapshot=(await clients[1].get(prefix+'/snapshot')).json()
        assert snapshot['document']['viewport']['zoomY']==1.5 and snapshot['last_ack_revision']==2
        stopped=await clients[0].post(prefix+'/stop');assert stopped.status_code==200 and not stopped.json()['connected']
        await until(lambda:all(manager.status=='disconnected' for manager in managers[1:]))
        with socket.socket() as probe:
            probe.settimeout(1);assert probe.connect_ex(('127.0.0.1',port))!=0
        # Owned stop did not shut down any independent primary HTTP service.
        for client in clients:assert (await client.get(prefix)).status_code==200
        assert stores[1].get_canvas(originals[1]['id'])==snapshot_originals[1]
        assert stores[2].get_canvas(originals[2]['id'])==snapshot_originals[2]
    finally:
        for manager in managers:await manager.stop()
        for server,task,listener,client in apis:
            await client.aclose();server.should_exit=True;await asyncio.wait_for(task,5);listener.close()
        assert all(manager.server_task is None and manager.watch_task is None and manager.reader_task is None for manager in managers)

def test_owned_listener_does_not_take_over_occupied_socket(tmp_path):
    async def verify():
        store=Store(tmp_path/'data');project=CanvasProjects(store).create('测试',document());manager=CanvasCollaboration(store)
        with socket.socket() as original:
            original.bind(('127.0.0.1',0));original.listen(1);port=original.getsockname()[1]
            with pytest.raises(ValueError):await manager.start_host(project['id'],'127.0.0.1',port)
            assert manager.role=='none' and original.fileno()>=0
        await manager.stop()
    asyncio.run(verify())

def test_atomic_canvas_save_rejects_other_sqlite_writer_without_overwriting(tmp_path):
    async def verify():
        store=Store(tmp_path/'data');project=CanvasProjects(store).create('测试',document());manager=CanvasCollaboration(store)
        manager.canvas_id=project['id'];expected=manager._updated()
        changed=document(77);store.save_canvas(project['id'],changed)
        assert manager._compare_save(document(999),expected) is None
        assert store.get_canvas(project['id'])['elements'][0]['x']==77
    asyncio.run(verify())


def test_guest_watcher_survives_remote_revision_between_sample_and_publish(tmp_path):
    async def verify():
        store=Store(tmp_path/'data');project=CanvasProjects(store).create('Watcher race fixture',document())
        manager=CanvasCollaboration(store,watch_interval=.01);manager.canvas_id=project['id'];manager.role='guest';manager.status='connected'
        manager.document=await manager._document();manager.hash=manager.local_hash=digest(manager.document);manager.updated_at=manager._updated()
        sampled=asyncio.Event();release=asyncio.Event();finished=asyncio.Event();original_publish=manager._publish
        sent=[]
        class Peer:
            async def send(self,raw):sent.append(packet(raw))
            async def close(self):pass
        manager.socket=Peer()
        async def delayed_publish(base,submitted,change=None):
            if not sampled.is_set():
                sampled.set();await release.wait()
                try:return await original_publish(base,submitted,change)
                finally:finished.set()
            return await original_publish(base,submitted,change)
        manager._publish=delayed_publish;manager.watch_task=asyncio.create_task(manager._watch())
        try:
            store.save_canvas(project['id'],document(33));await asyncio.wait_for(sampled.wait(),5)
            incoming=await manager._document(document(72),True)
            await manager._accept_state({'type':'state','protocol':1,'revision':1,'hash':digest(incoming),'document':incoming,
                'sender':'another-peer','change_id':'concurrent-remote','peer_count':1})
            release.set();await asyncio.wait_for(finished.wait(),5);await asyncio.sleep(0)
            assert manager.status=='connected' and not manager.watch_task.done()
            assert manager.revision==1 and store.get_canvas(project['id'])['elements'][0]['x']==72
            assert manager.conflict and store.get_canvas(manager.conflict['canvas_id'])['elements'][0]['x']==33
            # The next ordinary Store write must still be submitted, rather than
            # requiring a reconnect to restart a watcher killed by a CAS conflict.
            store.save_canvas(project['id'],document(99))
            await until(lambda:bool(sent) and manager.pending is not None)
            assert sent[-1]['base_revision']==1 and sent[-1]['document']['elements'][0]['x']==99
            accepted=sent[-1]['document']
            await manager._accept_state({'type':'state','protocol':1,'revision':2,'hash':digest(accepted),'document':accepted,
                'sender':manager.peer_id,'change_id':sent[-1]['change_id'],'peer_count':1})
            assert manager.status=='connected' and manager.pending is None and manager.revision==2
        finally:release.set();await manager.stop()
    asyncio.run(verify())

def test_local_metadata_merge_keeps_unknown_but_never_revives_known_deletions(tmp_path):
    previous=document();previous['legacyVersion']=12;previous['meta']['imported_from']='C:/local/old.xzpack'
    previous['viewport']['oldRenderer']='local-only';previous['elements'][0]['legacyNote']='local-only'
    previous['meta']['groups'][0]['oldLabelStyle']='local-only'
    previous['meta']['groups'].append({'GroupId':'deleted','GroupName':'已删除组','FontSize':16})
    shared=document(55);shared['elements'][0].pop('groupId');shared['meta']['groups'][0]['GroupName']='新共享标题'
    merged=merge_local_document(previous,shared)
    assert merged['legacyVersion']==12 and merged['meta']['imported_from']=='C:/local/old.xzpack'
    assert merged['elements'][0]['legacyNote']=='local-only' and 'groupId' not in merged['elements'][0]
    assert len(merged['meta']['groups'])==1 and merged['meta']['groups'][0]['GroupName']=='新共享标题'
    assert merged['meta']['groups'][0]['oldLabelStyle']=='local-only'
    safe=clean_document(CanvasProjects(Store(tmp_path/'data')),merged)
    assert 'local-only' not in json.dumps(safe) and 'old.xzpack' not in json.dumps(safe)

def test_pencil_points_transport_contains_only_coordinates(tmp_path):
    value=document();value['elements'].append({'id':'pencil','type':'pencil','x':1,'y':2,'w':3,'h':4,'color':'#405060',
        'points':[{'x':1,'y':2,'localPath':'C:/private/note'}]})
    safe=clean_document(CanvasProjects(Store(tmp_path/'data')),value,True)
    assert safe['elements'][-1]['points']==[{'x':1,'y':2}]

def test_invalid_initial_revision_creates_no_join_copy(tmp_path):
    async def verify():
        malicious=app_for(Store(tmp_path/'malicious'))
        data=clean_document(CanvasProjects(malicious.state.store),document(),True)
        @malicious.websocket('/ws')
        async def invalid_state(ws):
            await ws.accept();await ws.receive_text()
            await ws.send_text(json.dumps({'type':'state','protocol':1,'revision':'invalid','hash':digest(data),'document':data}))
            with pytest.raises(Exception):await ws.receive_text()
        api=await start_api(malicious);guest_store=Store(tmp_path/'guest');guest=CanvasCollaboration(guest_store)
        try:
            port=api[2].getsockname()[1]
            with pytest.raises(ValueError):await guest.join(f'ws://127.0.0.1:{port}/ws','a'*43)
            assert CanvasProjects(guest_store).list()==[]
        finally:
            await guest.stop();await malicious.state.canvas_collaboration.stop()
            await api[3].aclose();api[0].should_exit=True;await api[1];api[2].close()
    asyncio.run(verify())

def test_second_unacknowledged_submission_is_preserved(tmp_path):
    async def verify():
        store=Store(tmp_path/'data');project=CanvasProjects(store).create('本机副本',document())
        manager=CanvasCollaboration(store);manager.role='guest';manager.status='connected';manager.canvas_id=project['id']
        manager.document=await manager._document(project);manager.hash=digest(manager.document)
        manager.pending={'document':document(33),'base_revision':0,'change_id':'first'}
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as error:await manager.publish(0,document(99),'second')
        assert error.value.status_code==409
        assert store.get_canvas(error.value.detail['conflict_copy_id'])['elements'][0]['x']==99
        assert manager.pending['change_id']=='first'
        await manager.stop()
    asyncio.run(verify())

def test_repeated_conflict_points_to_matching_existing_copy(tmp_path):
    async def verify():
        manager=CanvasCollaboration(Store(tmp_path/'data'))
        manager.document=await manager._document(document());manager.hash=digest(manager.document)
        await manager._preserve(document(10),'first','A');first=manager.conflict['canvas_id']
        await manager._preserve(document(20),'second','B');second=manager.conflict['canvas_id']
        await manager._preserve(document(10),'again','C')
        assert first!=second and manager.conflict['canvas_id']==first and manager.conflict['change_id']=='C'
        assert len(manager.projects.list())==2
        await manager.stop()
    asyncio.run(verify())

def test_publish_scope_is_checked_after_context_transition_lock_and_rejects_peer(tmp_path):
    async def verify():
        from fastapi import HTTPException
        store=Store(tmp_path/'data');projects=CanvasProjects(store)
        previous=projects.create('旧协作',document(1));current=projects.create('新协作',document(2))
        manager=CanvasCollaboration(store);manager.canvas_id=previous['id'];original_peer=manager.peer_id
        await manager.operation_lock.acquire()
        attempt=asyncio.create_task(manager.publish(0,document(333),'old-window',previous['id'],original_peer))
        await asyncio.sleep(0);assert not attempt.done()
        # A different window's lifecycle transition owns this lock first. The
        # publisher must validate the resulting context after it gets the lock.
        manager.canvas_id=current['id'];manager.document=await manager._document(current);manager.hash=digest(manager.document)
        manager.operation_lock.release()
        with pytest.raises(HTTPException) as error:await attempt
        assert error.value.status_code==409
        assert store.get_canvas(error.value.detail['conflict_copy_id'])['elements'][0]['x']==333
        assert store.get_canvas(previous['id'])['elements'][0]['x']==1
        assert store.get_canvas(current['id'])['elements'][0]['x']==2
        with pytest.raises(HTTPException) as peer_error:
            await manager.publish(0,document(444),'stale-peer',current['id'],uuid.uuid4().hex)
        assert peer_error.value.status_code==409
        assert store.get_canvas(peer_error.value.detail['conflict_copy_id'])['elements'][0]['x']==444
        assert store.get_canvas(current['id'])['elements'][0]['x']==2
        await manager.stop()
    asyncio.run(verify())

def test_same_canvas_restart_base_zero_rejects_old_peer_context(tmp_path):
    async def verify():
        from fastapi import HTTPException
        store=Store(tmp_path/'data');project=CanvasProjects(store).create('同画布重启',document());manager=CanvasCollaboration(store)
        try:
            first=await manager.start_host(project['id'],'127.0.0.1',0);old_peer=first['peer_id'];old_invite=first['invite_code']
            assert first['revision']==0
            await manager.stop();store.save_canvas(project['id'],document(77))
            fresh=await manager.start_host(project['id'],'127.0.0.1',0)
            assert fresh['revision']==0 and fresh['peer_id']!=old_peer and fresh['invite_code']!=old_invite
            with pytest.raises(HTTPException) as stale:
                await manager.publish(0,document(444),'old-context-base-zero',project['id'],old_peer)
            assert stale.value.status_code==409 and manager.revision==0
            assert store.get_canvas(stale.value.detail['conflict_copy_id'])['elements'][0]['x']==444
            assert store.get_canvas(project['id'])['elements'][0]['x']==77
            accepted=await manager.publish(0,document(88),'fresh-context',project['id'],fresh['peer_id'])
            assert accepted['accepted_revision']==1 and store.get_canvas(project['id'])['elements'][0]['x']==88
        finally:await manager.stop()
    asyncio.run(verify())

def test_simultaneous_duplicate_auth_and_peer_cap_use_same_lock(tmp_path):
    async def verify():
        store=Store(tmp_path/'data');project=CanvasProjects(store).create('测试',document());manager=CanvasCollaboration(store)
        await manager.start_host(project['id'],'127.0.0.1',0);url=manager.share_urls[0];connections=[]
        async def auth(peer_id):
            ws=await connect(url,proxy=None);connections.append(ws)
            await ws.send(json.dumps({'type':'auth','protocol':1,'invite_code':manager.invite_code,'peer_id':peer_id}))
            try:return json.loads(await asyncio.wait_for(ws.recv(),3))['type']=='state'
            except ConnectionClosed:return False
        try:
            peer=uuid.uuid4().hex
            duplicate=await asyncio.gather(auth(peer),auth(peer))
            assert sum(duplicate)==1 and len(manager.peers)==1
            capped=await asyncio.gather(*(auth(uuid.uuid4().hex) for _ in range(9)))
            assert sum(capped)==7 and len(manager.peers)==8
        finally:
            for ws in connections:await ws.close()
            await manager.stop()
    asyncio.run(verify())
