"""Device-level, observable signed update jobs and a native installer handoff."""
from __future__ import annotations
import asyncio
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import sys
import time

from fastapi import APIRouter, Body, HTTPException
from fastapi.responses import JSONResponse
from . import __version__
from .signed_release import SignedReleaseError, latest, download_verified, verify_release, _safe_directory

BUSY = {'checking', 'downloading', 'verifying', 'preparing', 'installing'}

class UpdateManager:
    def __init__(self, app):
        self.app = app
        self.root = Path(app.state.remote.root_dir) / 'updates'
        self.state = 'idle'
        self.release = self.policy = self.package = None
        self.error = ''
        self.downloaded = 0
        self.speed = 0.0
        self.checked_at = None
        self.task = None
        self.lock = asyncio.Lock()
        self.installing = False
        self.operations = 0
        self.last_result = None
        result = self.root / 'last-result.json'
        if result.is_file():
            try:
                value = json.loads(result.read_text(encoding='utf-8'))
                self.last_result = {key: value.get(key) for key in ('status','version','message','finished_at')}
            except (ValueError, OSError):
                pass

    def supported(self):
        folder = Path(sys.executable).parent
        if os.name != 'nt' or not getattr(sys,'frozen',False) or not (folder/'ArchitectureWorkbenchUpdater.exe').is_file():
            return False
        import winreg
        try:
            key_name=r'Software\Microsoft\Windows\CurrentVersion\Uninstall\{6B94B4E4-4210-4A74-91B7-F5B4C6D615F0}_is1'
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER,key_name,0,winreg.KEY_READ|winreg.KEY_WOW64_64KEY) as key:
                installed=Path(winreg.QueryValueEx(key,'InstallLocation')[0]).resolve()
            return installed==folder.resolve()
        except (OSError,ValueError):
            return False

    def status(self):
        return {'current_version': __version__, 'state': self.state, 'release': self.release.projection() if self.release else None,
                'downloaded_bytes': self.downloaded, 'bytes_per_second': round(self.speed),
                'checked_at': self.checked_at, 'error': self.error, 'install_supported': self.supported(),
                'last_result': self.last_result}

    async def check(self):
        async with self.lock:
            if self.state in BUSY:
                raise HTTPException(409, '更新任务正在进行')
            self.state, self.error = 'checking', ''
        try:
            policy = self.app.state.remote.release_policy()
            release = await latest(policy.origin, __version__, policy=policy)
            self.policy, self.release = policy, release
            self.package = None
            self.downloaded = 0
            self.state = 'available' if release else 'current'
            if release:
                candidate = self.root / 'downloads' / release.filename
                def cached():
                    _safe_directory(candidate.parent)
                    if not candidate.is_file() or candidate.is_symlink() or candidate.stat().st_nlink != 1 or candidate.stat().st_size != release.size_bytes:
                        return False
                    with candidate.open('rb') as stream:
                        return hashlib.file_digest(stream,'sha256').hexdigest() == release.sha256
                if await asyncio.to_thread(cached):
                    self.package,self.downloaded,self.state=candidate,release.size_bytes,'ready'
            self.checked_at = datetime.now(timezone.utc).isoformat()
        except asyncio.CancelledError:
            self.state = 'idle'
            raise
        except (ValueError, OSError) as error:
            self.state, self.error = 'failed', str(error)
        return self.status()

    async def download(self):
        async with self.lock:
            if self.state in BUSY:
                raise HTTPException(409, '更新任务正在进行')
            if not self.release or not self.policy:
                raise HTTPException(409, '请先检查可用版本')
            if self.policy != self.app.state.remote.release_policy():
                raise HTTPException(409, '更新来源已变化，请重新检查版本')
            self.state, self.error, self.downloaded, self.speed = 'downloading', '', 0, 0
            self.task = asyncio.create_task(self._download(), name='signed-client-update')
        return self.status()

    async def _download(self):
        started = time.monotonic()
        def progress(received, total):
            self.downloaded = received
            self.speed = received / max(.1, time.monotonic() - started)
            if received == total:
                self.state = 'verifying'
        try:
            self.package = await download_verified(self.release, self.root / 'downloads', policy=self.policy,
                                                   current_version=__version__, progress=progress)
            self.state = 'ready'
        except asyncio.CancelledError:
            self.state, self.error = 'available', '已取消下载，可以重新开始'
            raise
        except (ValueError, OSError) as error:
            self.state, self.error = 'failed', str(error)
        finally:
            self.speed = 0

    async def cancel(self):
        if self.state in {'preparing','installing'}:
            raise HTTPException(409, '安装已经交接，请在升级窗口查看进度')
        if self.task and not self.task.done():
            self.task.cancel()
            try: await self.task
            except asyncio.CancelledError: pass
        return self.status()

    def assert_idle(self):
        runtime = self.app.state.runtime_manager
        worker = getattr(self.app.state, 'cloud_worker', None)
        collaboration = getattr(self.app.state, 'canvas_collaboration', None)
        if self.operations or runtime.active or runtime.probing or (worker and (worker.task and not worker.task.done())):
            raise HTTPException(409, '请先结束当前任务并关闭云任务执行开关，再升级')
        if collaboration and getattr(collaboration, 'role', None) in {'host','guest'}:
            raise HTTPException(409, '请先结束画布协作，再升级')

    async def prepare(self, version, parent_pid, port):
        staging = None
        async with self.lock:
            if self.state != 'ready' or not self.release or self.release.version != version or not self.package:
                raise HTTPException(409, '可安装版本已变化，请重新检查更新')
            if not self.supported() or parent_pid != os.getpid():
                raise HTTPException(409, '请在正式安装版的原生窗口中升级')
            if self.policy != self.app.state.remote.release_policy():
                raise HTTPException(409, '更新来源已变化，请重新检查版本')
            self.assert_idle()
            self.installing = True
            self.state = 'preparing'
        try:
            verify_release(self.release.envelope, __version__, policy=self.policy)
            def verify_package():
                with Path(self.package).open('rb') as stream:
                    return hashlib.file_digest(stream, 'sha256').hexdigest()
            if await asyncio.to_thread(verify_package) != self.release.sha256:
                raise ValueError('安装包校验失败，请重新下载')
            self.assert_idle()
            folder = Path(sys.executable).parent.resolve()
            staging = _safe_directory(self.root / ('handoff-' + secrets.token_hex(12)))
            staging.mkdir(parents=True, exist_ok=False)
            executable = staging / 'ArchitectureWorkbenchUpdater.exe'
            await asyncio.to_thread(shutil.copy2, folder / executable.name, executable)
            request = {'schema': 1, 'installation': str(folder), 'data_dir': str(self.app.state.remote.root_dir),
                       'port': port, 'parent_pid': parent_pid, 'current_version': __version__,
                       'envelope': self.release.envelope, 'policy': asdict(self.policy), 'package': str(self.package),
                       'created_at': time.time(), 'nonce': secrets.token_hex(24)}
            path = staging / 'request.json'
            path.write_text(json.dumps(request, ensure_ascii=False), encoding='utf-8')
            env = dict(os.environ)
            temp = staging / 'temp'; temp.mkdir()
            env.update(TEMP=str(temp), TMP=str(temp))
            process = subprocess.Popen([str(executable), str(path)], cwd=staging, env=env,
                                       creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0),
                                       stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            for _ in range(300):
                if process.poll() is not None:
                    raise ValueError('升级窗口未能启动，工作台仍可使用')
                receipt = staging / 'ready.json'
                if receipt.is_file():
                    value = json.loads(receipt.read_text())
                    if value.get('nonce') == request['nonce']:
                        self.assert_idle()
                        self.state = 'installing'
                        return {'ready': True, 'version': version}
                await asyncio.sleep(.1)
            (staging / 'cancel').touch()
            raise ValueError('升级窗口启动超时，请重试')
        except Exception as error:
            if staging and staging.exists():
                (staging / 'cancel').touch()
            self.installing = False
            self.state, self.error = 'ready', error.detail if isinstance(error,HTTPException) else str(error) if isinstance(error,ValueError) else '升级准备未完成，请重试或重新下载'
            raise HTTPException(409, self.error) from None

def register_updates(app):
    manager = UpdateManager(app)
    app.state.updates = manager
    router = APIRouter(prefix='/api/update')
    @router.get('/status')
    def status(): return manager.status()
    @router.post('/check')
    async def check(): return await manager.check()
    @router.post('/download')
    async def download(): return await manager.download()
    @router.post('/cancel')
    async def cancel(): return await manager.cancel()
    @router.post('/prepare')
    async def prepare(body: dict = Body(...)):
        bridge = getattr(app.state, 'desktop_bridge', None)
        if not bridge or not getattr(bridge, 'update_intent', False):
            raise HTTPException(403, '请从原生客户端的升级按钮发起安装')
        return await manager.prepare(body.get('version'), os.getpid(), getattr(app.state, 'desktop_port', 0))
    @app.middleware('http')
    async def update_barrier(request, call_next):
        mutation = request.url.path.startswith('/api/') and request.method not in {'GET','HEAD','OPTIONS'} and not request.url.path.startswith('/api/update/')
        if manager.installing and mutation:
            return JSONResponse({'detail':'正在准备升级，请等待完成'}, status_code=409)
        if mutation: manager.operations += 1
        try:
            return await call_next(request)
        finally:
            if mutation: manager.operations -= 1
    app.include_router(router)
    async def shutdown():
        if not manager.installing:
            await manager.cancel()
    app.router.add_event_handler('shutdown', shutdown)
