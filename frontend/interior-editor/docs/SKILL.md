# interior-3d-web（公开自研实现）

本技能是超级建筑师公开源码中的**本地室内三维编辑器**。它不包含任何参考产品的固定编辑器、静态包、模型、字体或纹理；场景、家具和材质图集都由本仓库源码生成。

## 职责

- 读取后端室内任务配置和绑定本次底图的场景 JSON。
- 在离线 HTML 中展示房间、墙体、门窗、家具和底图。
- 支持选取、移动、旋转、尺寸/颜色/材质编辑、相机旋转/平移/缩放、俯视与适合视图。
- 通过 `window.FixedInterior.snapshot()` 把当前场景交回工作台；后端仍会重新校验底图哈希、页码、尺度与方案集合。
- 导出当前方案可见几何为 GLB；导出项目 JSON。下载的离线网页不依赖 CDN、云服务或账号。

## 目录与构建

源码位于公开的 `frontend/interior-editor/`。构建产物写入 `resources/skills/interior-3d-web/`（`.gitignore` 已忽略运行时资源，不入库）。

```bash
cd frontend
npm ci
npm run build:interior
```

`build:interior` 先由 Vite 生成单文件编辑器 bundle，再由 `interior-editor/scripts/build-resources.mjs` 写入：

- `SKILL.md`、`开始生成.md`、`引用规范.md`
- `生成本次网页.cjs`
- `固定编辑器/固定编辑器代码.json`
- `固定编辑器/场景校验.cjs`
- `固定编辑器/交付阶段校验.cjs`
- `固定编辑器/风格统一配置.json`
- `固定编辑器/家具目录.json`
- `NOTICE.md`、`固定编辑器/资源清单.json`

首次检出未构建时，后端 `/api/interior/capabilities` 会返回 `ready=false` 和缺失文件列表；核心启动不受影响。构建后编辑器可用。

## 场景协议（摘要）

`interior-studio/1` JSON 的关键字段：

- `source.imageData`：本次底图的内嵌 data URI；作者稿阶段可用 `workbench:primary-source` 占位，仅后端 collect 展开。
- `generation`：`taskId`、`sourceFileId`、`sourceSha256`、`page`，必须与本任务配置一致。
- `drawing`：原图像素尺寸、`origin`、`scaleX/scaleZ`（米/像素）、`estimated` 或 `user-calibrated`。
- `architecture[]`：`floor`、`wall`、`ceiling`、`door`、`window` 等，`pos` 为中心、`size` 为米制包围盒。
- `rooms[]`：房间中心、尺寸与多边形。
- `schemes`：至少 `base`、`mid` 和本次已选风格；家具放在各方案 `objects[]`。
- `delivery.stage`：草案为 `design-draft`；只有完成核对和验收才可改为 `delivery-ready`。

校验器位于 `固定编辑器/场景校验.cjs` 与 `固定编辑器/交付阶段校验.cjs`。不要在带 `workbench:primary-source` 占位符的作者稿上运行严格校验器；由后端展开底图后再运行。

## 原创与第三方

- 编辑器 UI、程序化家具、材质图集生成器、校验器、组装器：本仓库原创源码，适用仓库根目录 PolyForm Noncommercial License 1.0.0。
- Three.js：MIT，来自公开 npm 上游，随构建打包。
- 图标：本文件内联的原创 SVG 路径，不引入图标库。
- 不下载、不复制任何参考产品的固定 HTML/JS、技能、模型或纹理。

## 限制

程序化家具不是照片级模型；房间由用户描绘或本机 Agent 识别，编辑器不做自动识别或专业计算。估算尺寸、未核对开口和设计补充必须在交付前复核。
