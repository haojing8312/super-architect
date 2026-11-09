"""Signed streaming download, cancellation, failure and SQLite backup regressions."""
import asyncio
import base64
from datetime import datetime,timedelta,timezone
import hashlib
import json
from pathlib import Path
import sqlite3
from types import SimpleNamespace
import httpx
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding,PublicFormat
from fastapi import HTTPException
from architecture_workbench import __version__
from architecture_workbench.signed_release import ReleasePolicy,canonical,verify_release,download_verified
from architecture_workbench.updates import UpdateManager
from architecture_workbench.update_runner import backup_data,verified_request

@pytest.fixture
def artifact(tmp_path):
    key=Ed25519PrivateKey.generate();public=base64.b64encode(key.public_key().public_bytes(Encoding.Raw,PublicFormat.Raw)).decode()
    policy=ReleasePolicy('https://updates.test','ArchitectureWorkbench',{'test':public})
    data=b'MZ'+b'controlled fixture bytes'*8192
    now=datetime.now(timezone.utc)
    manifest={'schema':1,'version':'99.0.0','minimum_supported_version':'0.3.0','published_at':now.isoformat(),
              'expires_at':(now+timedelta(hours=1)).isoformat(),'release_notes':['受控升级测试'],
              'platform':'windows','arch':'x64','channel':'stable',
              'installer':{'url':policy.origin+'/downloads/ArchitectureWorkbench-Setup-99.0.0-aaaaaaaa.exe',
                           'size':len(data),'sha256':hashlib.sha256(data).hexdigest(),'commit':'a'*40}}
    envelope={'manifest':manifest,'key_id':'test','signature':base64.b64encode(key.sign(canonical(manifest))).decode()}
    release=verify_release(envelope,__version__,policy=policy)
    app=SimpleNamespace(state=SimpleNamespace(remote=SimpleNamespace(root_dir=tmp_path,release_policy=lambda:policy),
                        runtime_manager=SimpleNamespace(active={},probing=False),cloud_worker=None,canvas_collaboration=None))
    manager=UpdateManager(app);manager.policy=policy;manager.release=release;manager.state='available'
    return manager,policy,release,data

class Chunks(httpx.AsyncByteStream):
    def __init__(self,data):self.data=data
    async def __aiter__(self):
        for pos in range(0,len(self.data),4096):
            await asyncio.sleep(.008)
            yield self.data[pos:pos+4096]

def transport_download(monkeypatch, data):
    async def download(release,destination,**kwargs):
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _:httpx.Response(200,stream=Chunks(data)))) as client:
            return await download_verified(release,destination,client=client,**kwargs)
    monkeypatch.setattr('architecture_workbench.updates.download_verified',download)

def test_download_is_observable_and_only_verified_bytes_become_ready(artifact,monkeypatch):
    manager,_,release,data=artifact;transport_download(monkeypatch,data)
    async def run():
        assert (await manager.download())['state']=='downloading'
        await asyncio.sleep(.055)
        state=manager.status()
        assert 0<state['downloaded_bytes']<len(data) and state['bytes_per_second']>0
        with pytest.raises(HTTPException):await manager.download()
        await manager.task
        assert manager.state=='ready' and manager.package.read_bytes()==data
        assert manager.downloaded==release.size_bytes
    asyncio.run(run())

def test_cancel_removes_partial_download_and_can_retry(artifact,monkeypatch):
    manager,_,_,data=artifact;transport_download(monkeypatch,data)
    async def run():
        await manager.download();await asyncio.sleep(.025);await manager.cancel()
        assert manager.state=='available' and manager.package is None
        assert not list((manager.root/'downloads').iterdir())
        await manager.download();await manager.task
        assert manager.state=='ready'
    asyncio.run(run())

def test_bad_network_bytes_never_become_installable(artifact,monkeypatch):
    manager,_,_,data=artifact;transport_download(monkeypatch,data[:-6]+b'broken')
    async def run():
        await manager.download();await manager.task
        assert manager.state=='failed' and manager.package is None and manager.error
        assert not list((manager.root/'downloads').iterdir())
    asyncio.run(run())

def test_busy_work_and_revoked_trust_block_native_handoff(artifact,monkeypatch):
    manager,policy,release,_=artifact;manager.state='ready';manager.package=Path('not-run.exe')
    monkeypatch.setattr(manager,'supported',lambda:True)
    import os
    async def run():
        manager.app.state.runtime_manager.active={'real-job':True}
        with pytest.raises(HTTPException,match='当前任务'):
            await manager.prepare(release.version,os.getpid(),1234)
        assert not manager.installing
        manager.app.state.runtime_manager.active={}
        manager.app.state.remote.release_policy=lambda:ReleasePolicy('https://changed.test',policy.installer_prefix,policy.public_keys)
        with pytest.raises(HTTPException,match='来源已变化'):
            await manager.prepare(release.version,os.getpid(),1234)
    asyncio.run(run())

def test_backup_reads_committed_wal_and_preserves_original_media(tmp_path):
    data=tmp_path/'data';data.mkdir();db=sqlite3.connect(data/'workbench.sqlite3')
    db.execute('PRAGMA journal_mode=WAL');db.execute('CREATE TABLE projects(title TEXT)');db.execute('INSERT INTO projects VALUES(?)',('原设计',));db.commit()
    media=data/'drawing.png';media.write_bytes(b'original-image');(data/'member-session.bin').write_bytes(b'encrypted-fixture')
    backup_data(data,tmp_path/'backup')
    with sqlite3.connect(tmp_path/'backup/workbench.sqlite3') as copied:assert copied.execute('SELECT title FROM projects').fetchone()==('原设计',)
    assert media.read_bytes()==b'original-image'
    assert (tmp_path/'backup/member-session.bin').read_bytes()==b'encrypted-fixture'
    db.close()

def test_new_routes_keep_existing_account_and_openapi_working(tmp_path):
    from architecture_workbench.app import create_app
    from fastapi.testclient import TestClient
    with TestClient(create_app(tmp_path)) as client:
        client.headers['X-Workbench-Token']=client.get('/api/bootstrap').json()['token']
        assert client.get('/api/update/status').json()['current_version']==__version__
        assert client.post('/api/update/prepare',json={'version':'99.0.0'}).status_code==403
        assert '/api/update/status' in client.app.openapi()['paths']
        client.app.state.updates.installing=True
        assert client.post('/api/sessions',json={'title':'must not start'}).status_code==409
        assert client.get('/api/health').status_code==200
        client.app.state.updates.installing=False

def test_native_request_rejects_other_locations(tmp_path):
    path=tmp_path/'request.json';path.write_text('{}')
    with pytest.raises(ValueError,match='位置'):verified_request(path)

def test_native_request_requires_signed_bytes_and_same_installed_version(artifact,tmp_path):
    manager,policy,release,data=artifact
    from dataclasses import asdict
    import time,os
    folder=tmp_path/'installed';folder.mkdir()
    (folder/'ArchitectureWorkbench.exe').write_bytes(b'program marker')
    (folder/'release.json').write_text(json.dumps({'product':'architecture-workbench','version':__version__}))
    handoff=manager.root/'handoff-fixture';handoff.mkdir(parents=True)
    packages=manager.root/'downloads';packages.mkdir()
    package=packages/release.filename;package.write_bytes(data)
    path=handoff/'request.json'
    request={'data_dir':str(tmp_path),'installation':str(folder),'package':str(package),'current_version':__version__,
             'envelope':release.envelope,'policy':asdict(policy),'created_at':time.time(),'port':12345,'parent_pid':os.getpid()}
    # The install tree must be outside the data directory, as real installations are.
    request['data_dir']=str(tmp_path/'user-data')
    user=Path(request['data_dir']);user.mkdir()
    moved=user/'updates';manager.root.rename(moved)
    request['package']=str(moved/'downloads'/release.filename)
    path=moved/'handoff-fixture/request.json';path.write_text(json.dumps(request))
    assert verified_request(path)[1].sha256==release.sha256
    Path(request['package']).write_bytes(data[:-2]+b'xx')
    with pytest.raises(ValueError,match='校验'):verified_request(path)

def test_recheck_reuses_complete_verified_download(artifact,monkeypatch):
    manager,_,release,data=artifact
    folder=manager.root/'downloads';folder.mkdir(parents=True)
    (folder/release.filename).write_bytes(data)
    async def available(*args,**kwargs):return release
    monkeypatch.setattr('architecture_workbench.updates.latest',available)
    asyncio.run(manager.check())
    assert manager.state=='ready' and manager.downloaded==len(data)
