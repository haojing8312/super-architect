# 第三方内容与公开快照边界

本仓库的 LICENSE 不替代第三方许可证。Python 和 JavaScript 依赖由包管理器获取，分发时应保留其原有声明并履行各自许可义务。版本声明见 pyproject.toml、frontend/package.json 和 frontend/package-lock.json。

## 首次公开包含的独立许可文件

- `integrations/host-plugins/sketchup_adapter.py` 保留其原有 MIT SPDX 声明。
- `integrations/host-plugins/licenses/` 保留相应插件的第三方许可文本；这些文本不表示插件二进制或专业宿主已随仓库提供。

## 首次公开不包含

- 参考产品安装目录中的汇报编辑器、CAD 静态 bundle、技能、便携运行时及其他授权尚未核实的资源。
- 商业软件本体、原生 SDK、模型权重、宿主插件二进制及用户自行安装的 Agent。
- 用户数据库、附件、作品、配置、登录凭据、签名私钥、内部验收输出和部署资料。
- 与参考环境或内部部署绑定的准备、发布和实机验收脚本。

## CAD 与 DWG

原本地快照的 CAD 资源说明涉及 MIT 的 CAD-Viewer、GPL-3.0 的 LibreDWG 和 OFL 字体。该静态包不随首次公开提交上传，也不被重新许可为非商用代码。

后续从公开上游重建或另行分发时，必须单独明确来源、版本、对应源码及许可兼容边界；不能仅通过添加本文件，就声称已经解决完整发行包的许可问题。

## 原创室内编辑器

frontend/interior-editor 包含本项目原创编辑器、家具几何、验证器、HTML组装器与材质图集生成器，适用根许可证。渲染使用 Three.js（MIT）；构建会将其原许可证复制到生成资源。没有复制参考产品的固定编辑器、角色图集、模型或纹理。

## 当前能力边界

工作台账号与云端依赖已移除，设计院首页和原创室内编辑器已提供。尚未随附的DWG完整查看器、完整参考汇报编辑器、商业宿主/SDK、模型与语音引擎按各自边界登记，不承诺完整历史功能等价。详见 docs/local-delivery.md。
