# 本地版实现与验收记录

验证日期：2026-11-08（America/Phoenix）。本轮后续开发、修复与验收由当前 Codex 直接完成；CodeBuddy 自动续接已取消。

## 已完成

无工作台登录、会员、积分、租户或管理服务器依赖。保留本地随机令牌、回环/同源与文件范围保护。首页为七部门入口的原创三维设计院，任务、待用户确认及成果来自本地 SQLite 与实际运行状态；不以人物走动冒充 AI 执行。场景可暂停、减少动态、键盘进入及静态降级。

原先缺失的室内编辑器已由公开源代码重建，普通 npm run build 会同时生成其资源，不从参考产品提取 HTML/JS/模型。真实浏览器已完成手工户型、添加/改名家具、保存至后端、重开、GLB 和项目 JSON 下载。导出 HTML 内嵌渲染器和程序化材质，不需要外部 CDN。

旧数据导入使用临时 DB/WAL 副本，拒绝嵌套目标和不安全标识，保留已提交 WAL、报告图片及工作区成果；失败回滚保留备份。仅验证合成副本，未迁移真实用户资料。

## 验证证据口径

| 范围 | 本轮结果 |
| --- | --- |
| 固定核心：local_core、legacy_import、local_dashboard、security、api、store、desktop_bridge、dwg_assets | 最终46 passed；后续低影响文案与人物细节不改变核心逻辑 |
| 受影响：runtime、skills、canvas_collaboration、canvas_projects、design_workflow、deliverables、creative、artifacts、interior_workflow | 139 passed，无跳过；有依赖弃用警告 |
| 室内工作流 | 30 passed，包含于上行139项；已不再因编辑器资源缺失跳过 |
| 原创室内 Node 校验契约 | 2 passed：本图证据绑定和禁止外部底图 |
| 前端与室内编辑器类型检查/生产构建 | 通过；主包约1MB，仍有体积提示 |
| 真实本地服务浏览器检查 | 19项通过，页面异常0；1440×900、1366×768、1000×640，首屏输入、任务、成果、暂停、减少动态、静态降级、键盘、编辑保存重开及GLB/JSON |
| 原生桌面窗口 | 已实际启动到本地首页，标题正确、无登录输入；WebView2用户资料位于E盘数据目录。原生视觉截图未单独采集，视觉证据为真实浏览器截图 |

固定核心命令：

```powershell
python -m pytest tests/test_local_core.py tests/test_legacy_import.py tests/test_local_dashboard.py tests/test_security.py tests/test_api.py tests/test_store.py tests/test_desktop_bridge.py tests/test_dwg_assets.py -q -rs
```

受影响命令：

```powershell
python -m pytest tests/test_runtime.py tests/test_skills.py tests/test_canvas_collaboration.py tests/test_canvas_projects.py tests/test_design_workflow.py tests/test_deliverables.py tests/test_creative.py tests/test_artifacts.py tests/test_interior_workflow.py -q -rs
npm.cmd run test:interior --prefix frontend
npm.cmd run build --prefix frontend
```

详细执行日志、截图及合成资料保存在开发机E盘隔离证据目录，不上传凭据、运行数据库或用户产物。以上为固定核心和受影响范围，未宣称所有历史实机测试全量通过。

## 独立依赖环境

使用标准 Python 3.12.14 在 E 盘新建空 venv，按 pyproject.toml 的 desktop 依赖重新安装，启动脚本 PrepareOnly 返回成功；随后该新环境实际启动 WebView2 到本地首页。未依赖参考产品的固定编辑器或SDK。

## 真实 Codex 调用

已通过本机 Codex，经工作台真实 /api/sessions/{id}/turn 调用返回 LOCAL_READY，收到完整 done 且无 error；沿用用户现有工具账户，未修改其全局配置。WorkBuddy 未再次调用。

## 保留的边界

- WorkBuddy执行器兼容代码和事件回归保留；遵照最新用户要求，本轮不再调用WorkBuddy进行开发或真实模型验收。
- 专业宿主、商业SDK、语音引擎和本地语义模型未在本轮逐项实机验证。缺语义模型时回退关键词检索。
- DWG完整查看器与参考完整作品汇报编辑器未随源码分发；DXF快速预览和本地原生文档交付可用。
- 程序化人物与家具是风格化三维模型，不宣称等同参考生图的渲染质感、施工深化或专业计算。
- 数据目录通过启动参数选择；不提供运行中的不安全热迁移。
- 本轮不发布新的安装器或签名版本；历史基线与签名记录保持不变。
