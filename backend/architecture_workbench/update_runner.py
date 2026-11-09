"""Detached upgrade window: verifies bytes, waits for its parent, installs and restarts."""
from __future__ import annotations
from datetime import datetime, timezone
import ctypes
import hashlib
import json
import os
from pathlib import Path
import queue
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
import tkinter as tk
from tkinter import ttk
from .signed_release import ReleasePolicy, verify_release, _safe_directory

def verified_request(path):
    path = Path(path).resolve(strict=True)
    if path.name != 'request.json' or not path.parent.name.startswith('handoff-'):
        raise ValueError('升级请求位置不正确')
    _safe_directory(path.parent)
    raw = path.read_bytes()
    if len(raw) > 65536: raise ValueError('升级请求无效')
    value = json.loads(raw)
    data = Path(value['data_dir']).resolve(strict=True)
    install = Path(value['installation']).resolve(strict=True)
    if path.parent.parent != data / 'updates' or install == data or data.is_relative_to(install):
        raise ValueError('资料或安装目录不符合升级要求')
    _safe_directory(install); _safe_directory(data)
    if not 0 <= time.time() - value['created_at'] < 300 or not 1 <= int(value['port']) <= 65535:
        raise ValueError('升级请求已失效')
    if not (install / 'ArchitectureWorkbench.exe').is_file(): raise ValueError('未找到原客户端')
    metadata = json.loads((install / 'release.json').read_text(encoding='utf-8-sig'))
    if metadata.get('product') != 'architecture-workbench' or metadata.get('version') != value['current_version']:
        raise ValueError('当前安装版本已变化')
    policy = ReleasePolicy(**value['policy'])
    release = verify_release(value['envelope'], value['current_version'], policy=policy)
    package = Path(value['package']).resolve(strict=True)
    if package.parent != data / 'updates/downloads' or package.name != release.filename:
        raise ValueError('安装包位置不匹配')
    _safe_directory(package.parent)
    if package.stat().st_nlink != 1 or package.stat().st_size != release.size_bytes:
        raise ValueError('安装包大小或文件归属不匹配')
    with package.open('rb') as stream:
        if hashlib.file_digest(stream,'sha256').hexdigest() != release.sha256:
            raise ValueError('安装包校验失败')
    return value, release, hashlib.sha256(raw).hexdigest()

def wait_parent(pid, cancelled, deadline=100):
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.OpenProcess.restype = ctypes.c_void_p
    kernel.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    handle = kernel.OpenProcess(0x100000, False, int(pid))
    if not handle:
        if ctypes.get_last_error() == 87: return
        raise ValueError('不能确认原工作台是否退出')
    try:
        until = time.monotonic() + deadline
        while time.monotonic() < until:
            if cancelled(): raise ValueError('已取消升级准备')
            state = kernel.WaitForSingleObject(handle, 200)
            if state == 0: return
            if state != 258: raise ValueError('无法确认工作台进程状态')
        raise ValueError('工作台尚未退出；安装未开始，请关闭相关窗口后重试')
    finally:
        kernel.CloseHandle(handle)

def backup_data(data, destination):
    """SQLite's backup API preserves WAL contents; model/media payloads stay in place."""
    destination.mkdir()
    databases = [data / 'workbench.sqlite3', *data.glob('members/*/workbench.sqlite3')]
    for source in databases:
        if not source.is_file(): continue
        target = destination / source.relative_to(data)
        target.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(source) as db, sqlite3.connect(target) as copy:
            db.backup(copy)
    for name in ('member-session.bin','admin-manifest.json'):
        source = data / name
        if source.is_file(): shutil.copy2(source, destination / name)

def execute(path, events, ready):
    value = release = None
    try:
        events.put(('stage','正在校验安装包与版本签名'))
        value, release, request_hash = verified_request(path)
        folder, data = Path(value['installation']), Path(value['data_dir'])
        root = Path(path).parent
        def cancelled(): return (root / 'cancel').exists()
        if cancelled(): raise ValueError('已取消升级准备')
        # The GUI exists before this receipt authorizes the original window to exit.
        ready.wait(timeout=15)
        if not ready.is_set(): raise ValueError('升级窗口未准备好')
        receipt = root / 'ready.tmp'
        receipt.write_text(json.dumps({'nonce':value['nonce']}))
        os.replace(receipt, root / 'ready.json')
        events.put(('stage','更新已校验，正在等待工作台退出'))
        wait_parent(value['parent_pid'], cancelled)
        if cancelled() or hashlib.sha256(Path(path).read_bytes()).hexdigest() != request_hash:
            raise ValueError('升级准备已取消或请求变化，安装未开始')
        events.put(('stage','正在备份工作区设置与项目索引'))
        backup_data(data, root / 'backup')
        # Revalidate after shutdown, immediately before executing the installer.
        verified_request(path)
        events.put(('stage','正在安装超级建筑师，请勿关闭安装进度窗口'))
        log = root / 'installer.log'
        process = subprocess.Popen([value['package'], '/SILENT', '/SUPPRESSMSGBOXES', '/NORESTART', '/SP-',
                                    '/NOICONS', '/TASKS=', '/DIR=' + str(folder), '/LOG=' + str(log)], cwd=root)
        code = process.wait(timeout=1800)
        if code != 0:
            raise ValueError('安装未完成（退出码 %s），请查看安装日志；项目备份已保留' % code)
        installed = json.loads((folder / 'release.json').read_text(encoding='utf-8-sig'))
        if installed.get('version') != release.version:
            raise ValueError('安装后的版本核验未通过，请查看安装日志')
        result = {'status':'completed','version':release.version,'message':'升级完成，已重新打开超级建筑师',
                  'finished_at':datetime.now(timezone.utc).isoformat()}
        (data / 'updates/last-result.json').write_text(json.dumps(result,ensure_ascii=False),encoding='utf-8')
        events.put(('stage','升级完成，正在打开超级建筑师'))
        subprocess.Popen([str(folder/'ArchitectureWorkbench.exe'),'desktop','--data-dir',str(data),'--port',str(value['port'])],cwd=folder)
        events.put(('done',result['message']))
    except Exception as error:
        message = str(error) if isinstance(error, ValueError) else '升级未完成，请保留安装日志后重试'
        if value:
            result = {'status':'failed','version':release.version if release else '', 'message':message,
                      'finished_at':datetime.now(timezone.utc).isoformat()}
            (Path(value['data_dir'])/'updates/last-result.json').write_text(json.dumps(result,ensure_ascii=False),encoding='utf-8')
        events.put(('failed',message))

def main():
    if os.name != 'nt' or len(sys.argv) != 2: return 2
    window = tk.Tk(); window.title('超级建筑师 · 软件更新'); window.geometry('550x260'); window.resizable(False,False)
    window.configure(bg='#1b1e1f')
    frame = tk.Frame(window,bg='#1b1e1f',padx=32,pady=26); frame.pack(fill='both',expand=True)
    tk.Label(frame,text='超级建筑师  /  更新工作台',font=('Microsoft YaHei UI',18,'bold'),fg='#dec28d',bg='#1b1e1f').pack(anchor='w')
    status = tk.StringVar(value='正在准备更新…')
    tk.Label(frame,textvariable=status,font=('Microsoft YaHei UI',11),wraplength=480,justify='left',fg='#eeefeb',bg='#1b1e1f').pack(anchor='w',pady=(22,18))
    bar = ttk.Progressbar(frame,mode='indeterminate'); bar.pack(fill='x');bar.start(12)
    tk.Label(frame,text='安装进度会在独立安装窗口中显示，已保存的项目保留。',font=('Microsoft YaHei UI',9),fg='#acafac',bg='#1b1e1f').pack(anchor='w',pady=14)
    window.protocol('WM_DELETE_WINDOW',lambda:None)
    events,ready=queue.Queue(),threading.Event()
    threading.Thread(target=execute,args=(sys.argv[1],events,ready),daemon=True).start()
    def poll():
        try:
            while True:
                kind,message=events.get_nowait();status.set(message)
                if kind in {'done','failed'}:
                    bar.stop();window.protocol('WM_DELETE_WINDOW',window.destroy)
                    ttk.Button(frame,text='关闭',command=window.destroy).pack(anchor='e')
                    if kind=='done':window.after(2500,window.destroy)
        except queue.Empty: pass
        window.after(150,poll)
    window.after(150,ready.set);poll();window.mainloop();return 0

if __name__=='__main__': raise SystemExit(main())
