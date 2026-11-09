# 独立 Blender 本机环境

由 `scripts/setup-blender.py` 从官方 4.5 LTS 发布站下载 Windows portable 并校验官方 SHA256。
启动与建模脚本仅使用本工程 `.local` 下的独立数据与 `--factory-startup`，不改变用户现有 Blender 配置。

```powershell
.\.venv\Scripts\python.exe scripts/setup-blender.py
.\.venv\Scripts\python.exe integrations/blender/host.py start
.\.venv\Scripts\python.exe integrations/blender/host.py status
.\.venv\Scripts\python.exe integrations/blender/verify.py
```

独立宿主使用原 addon 的 TCP 9877 协议和主线程命令队列。验证脚本通过本工程原 MCP launcher 创建 4 × 3 × 3 米立方体、CPU 渲染 PNG、保存 BLEND 并导出 GLB。
实际产物在 `.local/blender-host/outputs`，证据在 `docs/evidence/blender-host.json`。使用 `host.py stop` 只停止这个受控宿主。
原生 GUI 视口截图在后台模式不可用，视觉验收使用实际相机渲染。
