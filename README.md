# 超级建筑师 · SUPER ARCHITECT

建筑与空间创作工作台，采用 React、FastAPI、SQLite 与 pywebview，使用本机 Codex / WorkBuddy 执行 AI 任务。内部运行时标识仍为 `codebuddy`，界面统一称 WorkBuddy；这是同一套既有接入，不是新增适配器。

**源码公开，非商用免费，商用需取得授权。** 自有代码适用 [PolyForm Noncommercial License 1.0.0](LICENSE)，具体范围、商业授权与第三方边界见 [LICENSE-INFO.md](LICENSE-INFO.md) 和 [THIRD-PARTY-NOTICES.md](THIRD-PARTY-NOTICES.md)。本项目属于 source-available，不宣称采用 OSI-approved 开源许可证。

## 当前状态（M1 本地核心）

工作树已完成 M1 本地核心改造，但 **M2 设计院首页场景尚未开始**，不能把本仓库理解为完整新版本。

已在本轮实现并验证：

- 无工作台账号、会员等级、积分、租户和管理端配置；启动直接进入本地工作区。
- 项目、对话、附件、资料、画板、偏好、任务记录与成果保存在本机 SQLite 和所选数据目录。
- 设置直接保存到本地，不再经过远端会话；回环主机、同源、本地随机令牌和工作区文件边界保护保留。
- Codex 与 WorkBuddy（内部 `codebuddy`）执行路径、任务事件、停止和创作产物处理保留。
- 原云创作入口改为本机创作页；不再包含云项目、设备任务、会员登录或管理端入口。
- 旧匿名/会员目录支持“先备份、再离线导入”，每个旧来源成为独立本地数据目录，不自动合并，也不访问生产服务。
- 缺失的可选 CAD 查看器与汇报编辑器会明确降级，DXF 快速预览、快速文档汇报和本地核心启动不受影响。

已退役：会员登录/刷新/退出、积分与租户、管理端连接、云项目与设备调度、由管理端签名驱动的应用内更新、会员目录切换。历史安装包与签名记录保持原样，不在本次工作树内修改。

### 如何手动升级

应用内签名更新已随管理端链路退役。获取新版本时，请从公开发布渠道手动下载安装包，先退出工作台并备份当前数据目录，再安装新版本并在启动时重新选择同一数据目录。工作台不会在启动时联系旧管理端，也不会自动下载或执行升级。

未完成项按阶段保留：M2 首页场景/人物动画、M3 独立验收与公开依赖复现。真实能力矩阵和缺口见 [docs/parity-matrix.md](docs/parity-matrix.md)，可选组件见 [docs/optional-components.md](docs/optional-components.md)。

## 已有源码模块

- 设计对话、任务事件、历史、附件、草稿和本地 Agent 执行。
- 项目知识、资料检索、创意画板、素材管理。
- 空间方案、文档及演示文稿交付、CAD 与三维预览的应用层。
- 专业宿主连接、本地 MCP、提醒、运行记录和桌面壳。

功能可用性取决于对应模块的实际依赖与本机环境。首次公开不随附参考产品中的静态编辑器、CAD bundle、技能、模型、便携运行时和商业 SDK；相关入口按可选组件降级，详见第三方说明。历史安装环境的验收不等同于本公开快照的复现验收。

## 源码结构

| 路径 | 内容 |
| --- | --- |
| `backend/architecture_workbench/` | 本地 API、数据、迁移与任务服务 |
| `frontend/src/` | React 应用与现有页面 |
| `frontend/scripts/` | 前端辅助检查 |
| `integrations/` | 自研本机集成代码与插件来源信息 |
| `tests/` | 自动化测试源码；按本轮报告中的命令执行 |
| `docs/` | 已确认方案、能力矩阵与可选组件说明 |

部署凭据、用户数据库、用户产物、内部验收资料、私钥和机器安装记录不属于公开源码。内部构建与部署脚本也不随首次公开提交提供。

## 本地运行

需要 Python 3.11+、Node.js 22+；Windows 桌面壳还需要可用的 WebView2。AI 工具与专业宿主由用户自行安装、配置，账号和额度由各工具自行管理。

Windows 上请将检出目录、虚拟环境、依赖缓存、构建输出、临时目录及数据目录统一放在 E 盘，并在安装或运行前显式设置 `TEMP`、`TMP`、`PIP_CACHE_DIR`、npm 缓存和浏览器测试资料路径。不要从其他产品的私有安装目录读取资源来补齐本仓库。

```powershell
# 1. 本地 Python 环境（示例路径放在 E 盘）
py -3.11 -m venv E:/path/to/super-architect/.venv
E:/path/to/super-architect/.venv/Scripts/python -m pip install -e ".[test]"

# 2. 前端（开发模式）
cd frontend
npm ci
npm run dev   # Vite 将 /api 代理到 127.0.0.1:33821

# 3. 启动本地服务（另开终端）
architecture-workbench serve --data-dir E:/path/to/workbench-data --port 33821

# 也可构建前端后由本地服务直接托管
cd frontend && npm run build
architecture-workbench serve --data-dir E:/path/to/workbench-data
```

数据目录在启动时通过 `--data-dir` 或 `ARCHITECTURE_DATA_DIR` 选择；不指定时使用当前用户的本地应用数据目录。桌面壳还需要可选依赖 `.[desktop]` 和 WebView2。

### 导入旧版本数据

先退出旧版本并备份旧数据目录，然后执行：

```powershell
architecture-workbench import-legacy --source E:/path/to/old-data --data-dir E:/path/to/new-data
```

导入会先写入新数据目录下的 `backups/legacy-*`，再为旧匿名区和每个旧会员目录分别建立独立本地数据目录，旧的匿名区与会员目录不会合并。使用 `--dry-run` 只扫描不写入。导入完成后，用 `--data-dir` 指向需要打开的那个导入目录。也可以在工作台的“设置 → 本地数据”中扫描并导入。

## 验证

本轮固定核心与受影响测试、前端构建命令及真实退出码记录在项目经理报告 `reports/M1.json`。测试通过不代表未包含的参考静态包、商业 SDK 或专业宿主已完成复现；缺失依赖按可选组件登记。

```powershell
python -m pytest tests/test_local_core.py tests/test_legacy_import.py tests/test_security.py tests/test_api.py tests/test_store.py -q
cd frontend && npm run build
```

## 首页方向

M2 将把首页改为按建筑设计所、规划与景观所、结构设计所、机电设计所、室内设计所、BIM 与数字技术中心划分的微缩建筑设计院，中央为总师办评审区。部门空间是交互入口；专业计算、碰撞检查和审签等能力必须按实际接入情况标注。人物环境动画与真实任务状态分开处理，场景提供暂停、减少动态和静态降级；AI 联网需求由本机 Agent 决定，不承诺离线模型推理。

## 商业授权

超出 LICENSE 允许范围的商业使用，须事先取得独立书面授权。请通过本仓库联系维护者，说明使用主体、用途及部署方式；不要提交凭据或保密项目资料。
