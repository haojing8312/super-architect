# 超级建筑师 · SUPER ARCHITECT

本地建筑与空间创作工作台，采用 React、FastAPI、SQLite、Three.js 与 pywebview。无需工作台账号、会员、积分、租户或管理服务器；AI 使用用户本机已有的 Codex / WorkBuddy（内部标识仍为 codebuddy）。

**源码公开，非商用免费，商用须获得授权。** 自有代码适用 [PolyForm Noncommercial 1.0.0](LICENSE)，商业授权及第三方边界见 [许可说明](LICENSE-INFO.md) 和 [第三方说明](THIRD-PARTY-NOTICES.md)。这是 source-available 许可，不宣称属于 OSI-approved 开源。

## 本轮实现

- 首页为可互动的三维建筑设计院：建筑、规划景观、结构、机电、室内、BIM 数字技术及总师办评审区。原创人物支持打字、翻图、查看模型、起身与短程行走。
- 场景支持暂停、减少动态与静态降级；部门也有文字和键盘入口。环境动作与真实任务状态分开，任务与待确认问题、成果均来自本地服务。
- 移除工作台账号、远端管理、云项目和应用内远端更新链路；保留回环、同源、随机本地访问令牌和文件范围保护。
- 资料、画板、对话、任务历史、文档成果保存在本机。旧匿名及会员资料可先备份后导入为独立本地工作区，不自动合并。
- 室内编辑器由本仓库原创公开源码构建，支持手工描绘/本机 Agent 场景、三维编辑、保存、重开、离线 HTML、项目 JSON 和真实 GLB 导出。无需参考产品的固定编辑器或私有技能。
- Codex / WorkBuddy 沿用原有执行接入，不新增模型 API 代理。工作台和本地资料功能可断网运行；AI 联网及账户额度由所选工具决定。

## Windows 本地启动

需要 Python 3.11+、Node.js 22+ 和可用的 WebView2。AI 工具与专业宿主自行安装。

克隆后运行根目录的 **启动本地版.ps1**。默认把环境、缓存、构建和数据全部放在 E 盘；首次运行会安装公开依赖并构建。已有 Python 版本过低时，用 `-PythonExecutable` 指定合适解释器。

```powershell
.\启动本地版.ps1 -PythonExecutable 'E:/Python312/python.exe'
```

脚本从 E 盘源码运行；若源仓库在其他盘，会将已提交版本导出到 E 盘后构建，保留原目录及未提交资料。默认端口 33829，数据目录 E:/codex-work/super-architect/client-data。可用 `-DataDir`、`-Port`、`-BuildRoot` 调整；`-PrepareOnly` 仅准备环境。

开发时直接在 E 盘检出，并在任何安装/构建前将 TEMP、TMP、PIP_CACHE_DIR、npm 缓存及浏览器测试资料目录设置到 E 盘：

```powershell
python -m venv E:/work/super-architect/.venv
.\.venv\Scripts\python.exe -m pip install -e '.[desktop,test]'
npm.cmd ci --prefix frontend
npm.cmd run build --prefix frontend
.\.venv\Scripts\python.exe -m architecture_workbench desktop --data-dir E:/work/super-architect-data --port 33829
```

`npm run build` 同时构建首页与原创室内编辑器。单独编辑室内模块可用 `npm run typecheck:interior`、`npm run build:interior`、`npm run test:interior`。生成资源位于被忽略的 resources/skills/interior-3d-web；源码在 frontend/interior-editor，Three.js 保留 MIT 声明。

## 旧数据导入

退出旧版本，在“设置 → 本地数据”扫描并确认导入，或运行：

```powershell
architecture-workbench import-legacy --source E:/old-data --data-dir E:/new-data
```

预览读取临时副本；正式导入先备份 DB/WAL 与业务文件，再建立独立工作区。目标不能位于源目录内。源资料不改写；导入后用返回的目录作为 `--data-dir` 打开。未授权执行任何真实用户数据迁移。

## 验证与能力边界

测试按固定核心和受影响范围执行，含迁移/WAL保留、接口保护、原生文档、画板、运行时与室内工作流。真实浏览器验证三种窗口尺寸、状态接口、编辑保存重开与 GLB 导出。详细结果及缺口见 [验收记录](docs/local-delivery.md)、[能力矩阵](docs/parity-matrix.md) 和 [可选组件](docs/optional-components.md)。测试、模拟 Agent、真实模型和原生窗口结果分别记录，不以构建成功代替功能验收。

DWG 完整查看器、参考产品完整汇报编辑器、商业宿主/SDK、模型权重及语音引擎不随源码分发；DXF 快速预览、原生文档交付及其他本地功能仍可使用。结构/机电计算、专业校审和审签等尚未接入的能力会明确标注。具体用户场景中的专业结果仍需人工复核。

本轮交付源码与本地运行方式，不发布新的签名安装器。历史安装包与签名记录保持不可变；后续安装包须另行完成验包、签名和升级检查。
