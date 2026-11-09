"""Windows shell: Edge WebView2, tray, native pickers and optional desktop pet."""
from __future__ import annotations

import ctypes
import hashlib
import os
from pathlib import Path
import threading
import time
from urllib.parse import urlsplit

import httpx


class DesktopApi:
    def __init__(self, origin=""):
        self._window = None
        self._origin = origin
        self.maximized = False

    def _assert_trusted(self):
        if not self._window or not self._origin:
            raise ValueError("本机桥接尚未准备好")
        current = urlsplit(self._window.get_current_url() or "")
        expected = urlsplit(self._origin)
        if (current.scheme, current.hostname, current.port) != (expected.scheme, expected.hostname, expected.port):
            raise ValueError("此页面没有本机工作台权限")

    def pick_file(self):
        self._assert_trusted()
        import webview
        paths = self._window.create_file_dialog(webview.FileDialog.OPEN, allow_multiple=False)
        return str(paths[0]) if paths else None

    def pick_folder(self):
        self._assert_trusted()
        import webview
        paths = self._window.create_file_dialog(webview.FileDialog.FOLDER)
        return str(paths[0]) if paths else None

    def open_path(self, path):
        self._assert_trusted()
        source = Path(path).expanduser().resolve(strict=True)
        documents={".txt",".md",".csv",".tsv",".json",".pdf",".docx",".xlsx",".pptx",".png",".jpg",".jpeg",".gif",".webp",".bmp",".svg",".html",".htm",".mp3",".wav",".mp4",".webm",".mov",".dxf",".dwg",".skp",".3dm",".blend",".max",".rvt",".rfa",".obj",".stl",".glb",".gltf",".psd",".ai",".indd"}
        if source.is_file() and source.suffix.lower() not in documents:
            raise ValueError("工作台仅直接打开文档、媒体和设计文件；其他文件请在资源管理器中处理")
        if os.name == "nt":
            os.startfile(str(source))
        else:
            import subprocess
            subprocess.Popen(["xdg-open",str(source)])

    def open_url(self, url):
        self._assert_trusted()
        if urlsplit(url).scheme not in {"https","http"}:
            raise ValueError("外部链接仅支持网页")
        import webbrowser
        webbrowser.open(url)

    def minimize(self):
        self._assert_trusted()
        self._window.minimize()

    def maximize(self):
        self._assert_trusted()
        from .window_bounds import install_work_area_guard
        guard = install_work_area_guard(self._window) if os.name == 'nt' and getattr(self._window,'native',None) else None
        maximized = guard.maximized if guard else self.maximized
        self._window.restore() if maximized else self._window.maximize()
        self.maximized = not maximized

    def close(self):
        self._assert_trusted()
        self._window.destroy()

    def show(self):
        self._window.show()
        self._window.restore()

    def pick_color(self):
        self._assert_trusted()
        # Native overlay captures a real screen pixel chosen by the user.
        from PIL import ImageGrab, ImageTk
        import tkinter as tk
        result = {"value": ""}
        image = ImageGrab.grab(all_screens=True)
        user32 = ctypes.windll.user32
        origin_x, origin_y = user32.GetSystemMetrics(76), user32.GetSystemMetrics(77)
        overlay = tk.Tk()
        overlay.overrideredirect(True)
        overlay.attributes("-topmost", True)
        overlay.geometry(f"{image.width}x{image.height}{origin_x:+}{origin_y:+}")
        photo = ImageTk.PhotoImage(image)
        canvas = tk.Canvas(overlay, width=image.width, height=image.height, highlightthickness=0, cursor="crosshair")
        canvas.pack()
        canvas.create_image(0,0,anchor="nw",image=photo)
        canvas.create_rectangle(15,15,335,53,fill="#2F3C48",outline="")
        canvas.create_text(175,34,text="点击屏幕取色 · Esc 取消",fill="white",font=("Microsoft YaHei UI",12))
        def choose(event):
            rgb=image.getpixel((min(max(event.x,0),image.width-1),min(max(event.y,0),image.height-1)))
            result["value"]="#{:02X}{:02X}{:02X}".format(*rgb[:3])
            overlay.destroy()
        canvas.bind("<Button-1>",choose)
        overlay.bind("<Escape>",lambda _e:overlay.destroy())
        overlay.after(30000, overlay.destroy)
        overlay.focus_force()
        overlay.mainloop()
        return result["value"]


def launch(data_dir=None, port=33821):
    import webview
    import uvicorn
    from .app import create_app
    from .store import default_data_dir
    directory=Path(data_dir).resolve() if data_dir else default_data_dir()
    url=f"http://127.0.0.1:{port}"
    mutex=None
    if os.name=="nt":
        kernel=ctypes.WinDLL("kernel32",use_last_error=True)
        kernel.CreateMutexW.restype=ctypes.c_void_p
        mutex=kernel.CreateMutexW(None,False,"Local\\ArchitectureWorkbench-"+hashlib.sha256(str(directory).encode()).hexdigest()[:20])
        if ctypes.get_last_error()==183:
            try:
                token=httpx.get(url+"/api/bootstrap",timeout=2).json()["token"]
                httpx.post(url+"/api/desktop/show",headers={"X-Workbench-Token":token},timeout=2)
            except Exception:
                pass
            kernel.CloseHandle(ctypes.c_void_p(mutex))
            return
    try:
        existing=httpx.get(url+"/api/health",timeout=1)
        if existing.is_success:
            raise RuntimeError(f"端口 {port} 已有服务，请关闭此前开发预览或指定 --port")
    except httpx.RequestError:
        pass
    app=create_app(directory)
    bridge=DesktopApi(url)
    app.state.desktop_bridge=bridge
    app.state.desktop_port=port
    tray=None
    def reminder_notification(reminder):
        if tray is not None:
            tray.notify(reminder["title"], "超级建筑师提醒")
    app.state.reminder_notifier=reminder_notification
    @app.post("/api/desktop/show")
    def show_window():
        bridge.show()
        return {"shown":True}
    # Insert desktop route before the frontend catch-all.
    route=app.router.routes.pop()
    index=next((i for i,r in enumerate(app.router.routes) if getattr(r,"name","")=="frontend"),len(app.router.routes))
    app.router.routes.insert(index,route)
    server=uvicorn.Server(uvicorn.Config(app,host="127.0.0.1",port=port,access_log=False,log_level="warning"))
    thread=threading.Thread(target=server.run,daemon=True)
    thread.start()
    deadline=time.monotonic()+12
    while not server.started:
        if not thread.is_alive() or time.monotonic()>deadline:
            raise RuntimeError("本地工作台服务启动失败")
        time.sleep(.05)
    window=webview.create_window("超级建筑师",url=url,js_api=bridge,width=1320,height=900,min_size=(1000,640),frameless=True,easy_drag=False,background_color="#141617",text_select=True)
    bridge._window=window
    from .window_bounds import install_work_area_guard
    window.events.shown += lambda: install_work_area_guard(window)
    window.events.maximized += lambda: setattr(bridge,'maximized',True)
    window.events.restored += lambda: setattr(bridge,'maximized',False)
    webview.settings["DRAG_REGION_SELECTOR"]=".titlebar"
    def confirm_close():
        return window.create_confirmation_dialog("退出超级建筑师","退出会停止正在运行的任务，确定退出吗？")
    window.events.closing+=confirm_close
    def close_auxiliary_windows():
        for other in list(webview.windows):
            if other is not window:
                other.destroy()
        server.should_exit=True
    window.events.closed+=close_auxiliary_windows
    try:
        import pystray
        from PIL import Image,ImageDraw
        brand_paths=[Path(__file__).parent/'frontend/brand/yuangou.png',Path(__file__).resolve().parents[2]/'frontend/public/brand/yuangou.png']
        brand_path=next((path for path in brand_paths if path.is_file()),None)
        icon=Image.open(brand_path).convert('RGBA') if brand_path else Image.new('RGBA',(64,64),'#dec28d')
        def exit_app(_icon=None,_item=None):
            window.destroy()
        tray=pystray.Icon("architecture-workbench",icon,"超级建筑师",menu=pystray.Menu(pystray.MenuItem("打开工作台",lambda *_:bridge.show(),default=True),pystray.MenuItem("隐藏到托盘",lambda *_:window.hide()),pystray.MenuItem("退出工作台",exit_app)))
        tray.run_detached()
    except Exception:
        # A tray failure must not prevent the client from starting.
        tray=None
    if app.state.store.get_settings().get("desktop_pet"):
        pet_html='''<!doctype html><html><style>html,body{margin:0;background:transparent;overflow:hidden}body{display:grid;place-items:center;height:76px;cursor:grab}svg{filter:drop-shadow(0 3px 3px #0002);animation:float 3s ease-in-out infinite}@keyframes float{50%{transform:translateY(-4px)}}</style><body ondblclick="window.pywebview.api.show()"><svg width="65" height="65" viewBox="0 0 65 65"><path d="M12 51V21L32 8 53 21V51L32 61Z" fill="#3C5266"/><path d="M17 24L32 14 48 24V45H17Z" fill="#E4ECF1"/><circle cx="26" cy="31" r="3" fill="#50677C"/><circle cx="39" cy="31" r="3" fill="#50677C"/><path d="M27 38Q32 42 38 38" fill="none" stroke="#50677C" stroke-width="2"/></svg></body></html>'''
        webview.create_window("桌面设计助手",html=pet_html,js_api=bridge,width=76,height=76,frameless=True,easy_drag=True,on_top=True,transparent=True,x=40,y=80)
    try:
        icons=[Path(__file__).parent/'frontend/brand/yuangou.ico',Path(__file__).resolve().parents[2]/'frontend/public/brand/yuangou.ico']
        native_icon=next((str(path) for path in icons if path.is_file()),None)
        webview.start(gui="edgechromium",debug=False,icon=native_icon)
    finally:
        if tray:
            tray.stop()
        server.should_exit=True
        thread.join(timeout=12)
        if mutex:
            ctypes.windll.kernel32.CloseHandle(ctypes.c_void_p(mutex))
