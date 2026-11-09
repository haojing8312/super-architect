import {createHash} from 'node:crypto';
import {createRequire} from 'node:module';
import fs from 'node:fs';
import path from 'node:path';
import {fileURLToPath} from 'node:url';

const here = path.dirname(fileURLToPath(import.meta.url));
const editorRoot = path.resolve(here, '..');
const repoRoot = path.resolve(editorRoot, '..', '..');
const output = path.resolve(repoRoot, 'resources', 'skills', 'interior-3d-web');
const require = createRequire(import.meta.url);
const {buildAtlas} = require('./atlas.cjs');

const FORMAT = 'interior-editor-bundle/1.0';
const VERSION = '1.0.0';
const BUILT_AT = new Date().toISOString();

function read(file) {
  return fs.readFileSync(file, 'utf8');
}

function copy(source, target) {
  fs.mkdirSync(path.dirname(target), {recursive: true});
  fs.copyFileSync(source, target);
}

function write(target, content) {
  fs.mkdirSync(path.dirname(target), {recursive: true});
  fs.writeFileSync(target, content, 'utf8');
}

function sha256(buffer) {
  return createHash('sha256').update(buffer).digest('hex');
}

function sizeOf(file) {
  return fs.statSync(file).size;
}

const dist = path.join(editorRoot, 'dist', 'interior-editor.js');
if (!fs.existsSync(dist)) {
  console.error('缺少 Vite 构建产物 frontend/interior-editor/dist/interior-editor.js；请先执行 npm run build:interior。');
  process.exit(1);
}

const editorScript = read(dist);
const editorCss = read(path.join(editorRoot, 'src', 'editor.css'));
const template = read(path.join(editorRoot, 'scripts', 'template.html'));
const styles = read(path.join(editorRoot, 'catalog', 'styles.json'));
const furniture = read(path.join(editorRoot, 'catalog', 'furniture.json'));

for (const [label, value] of [['template', template], ['editorScript', editorScript], ['editorCss', editorCss]]) {
  if (!value.trim()) {
    console.error(`${label} 为空，拒绝构建`);
    process.exit(1);
  }
}
if (template.includes('__FROZEN_SCENE_MODULE__')) {
  console.error('模板仍包含冻结场景占位符，拒绝构建');
  process.exit(1);
}
if (!template.includes('http://www.w3.org/2000/svg') || !template.includes('window.FixedInterior')) {
  console.error('离线模板缺少 SVG 命名空间或 FixedInterior 引导代码');
  process.exit(1);
}

const atlas = buildAtlas();
const bundle = {
  format: FORMAT,
  version: VERSION,
  builtAt: BUILT_AT,
  editorScript,
  editorCss,
  template,
  atlas: `data:image/png;base64,${atlas.toString('base64')}`,
  atlasTile: 512,
  atlasGrid: 4,
  atlasBytes: atlas.length,
  source: {
    editorEntry: 'frontend/interior-editor/src/main.ts',
    template: 'frontend/interior-editor/scripts/template.html',
    styles: 'frontend/interior-editor/catalog/styles.json',
    furniture: 'frontend/interior-editor/catalog/furniture.json',
    atlasGenerator: 'frontend/interior-editor/scripts/atlas.cjs',
  },
};

fs.mkdirSync(output, {recursive: true});
fs.mkdirSync(path.join(output, '固定编辑器'), {recursive: true});

copy(path.join(editorRoot, 'scripts', 'assemble-html.cjs'), path.join(output, '生成本次网页.cjs'));
copy(path.join(editorRoot, 'scripts', 'validate-scene.cjs'), path.join(output, '固定编辑器', '场景校验.cjs'));
copy(path.join(editorRoot, 'scripts', 'validate-delivery.cjs'), path.join(output, '固定编辑器', '交付阶段校验.cjs'));
write(path.join(output, '固定编辑器', '风格统一配置.json'), styles);
write(path.join(output, '固定编辑器', '家具目录.json'), furniture);
write(path.join(output, '固定编辑器', '固定编辑器代码.json'), JSON.stringify(bundle));
copy(path.join(editorRoot, 'docs', 'SKILL.md'), path.join(output, 'SKILL.md'));
copy(path.join(editorRoot, 'docs', '开始生成.md'), path.join(output, '开始生成.md'));
copy(path.join(editorRoot, 'docs', '引用规范.md'), path.join(output, '引用规范.md'));

const threeLicense = path.join(repoRoot, 'frontend', 'node_modules', 'three', 'LICENSE');
if (fs.existsSync(threeLicense)) copy(threeLicense, path.join(output, 'THIRD-PARTY-Three.js-LICENSE.txt'));
const rootLicense = path.join(repoRoot, 'LICENSE');
if (fs.existsSync(rootLicense)) copy(rootLicense, path.join(output, 'LICENSE-CODE.txt'));

write(path.join(output, 'NOTICE.md'), `# 室内三维编辑器资源说明

本目录由 \`frontend/interior-editor\` 公开源码构建生成。

- 编辑器 UI、程序化家具、场景/交付校验器、HTML 组装器、材质图集生成器：本仓库原创，适用仓库根目录 PolyForm Noncommercial License 1.0.0。
- Three.js：MIT，版权归 three.js authors；许可证见 \`THIRD-PARTY-Three.js-LICENSE.txt\`。
- 材质图集：由 \`scripts/atlas.cjs\` 确定性生成，不含第三方图片。
- 图标：编辑器内联的原创 SVG 路径。

本目录不包含任何参考产品的固定编辑器、模型、纹理、字体、技能包或私有 SDK。
`);

const files = [
  'SKILL.md', '开始生成.md', '引用规范.md', '生成本次网页.cjs', 'NOTICE.md',
  '固定编辑器/风格统一配置.json', '固定编辑器/家具目录.json', '固定编辑器/固定编辑器代码.json',
  '固定编辑器/场景校验.cjs', '固定编辑器/交付阶段校验.cjs',
];
const manifest = {
  format: 'interior-resource-manifest/1.0',
  generatedAt: BUILT_AT,
  version: VERSION,
  build: 'cd frontend && npm run build:interior',
  sourceRoot: 'frontend/interior-editor',
  node: process.version,
  atlas: {width: 2048, height: 2048, tile: 512, grid: 4, bytes: atlas.length, sha256: sha256(atlas)},
  files: files.map((file) => {
    const full = path.join(output, file);
    const buffer = fs.readFileSync(full);
    return {path: file, bytes: buffer.length, sha256: sha256(buffer)};
  }),
  licenses: [
    {name: 'super-architect interior editor', license: 'PolyForm Noncommercial 1.0.0'},
    {name: 'three.js', license: 'MIT'},
  ],
};
write(path.join(output, '固定编辑器', '资源清单.json'), JSON.stringify(manifest, null, 2));

const sceneValidator = require(path.join(output, '固定编辑器', '场景校验.cjs'));
const assembler = require(path.join(output, '生成本次网页.cjs'));
if (typeof sceneValidator.validateScene !== 'function' || typeof assembler.inspectTask !== 'function') {
  console.error('生成的校验器或组装器无法加载');
  process.exit(1);
}

const total = ['固定编辑器/固定编辑器代码.json', '生成本次网页.cjs']
  .map((file) => sizeOf(path.join(output, file)))
  .reduce((sum, value) => sum + value, 0);

console.log(JSON.stringify({
  ok: true,
  output,
  editorBytes: editorScript.length,
  atlasBytes: atlas.length,
  bundleBytes: sizeOf(path.join(output, '固定编辑器', '固定编辑器代码.json')),
  assemblerBytes: sizeOf(path.join(output, '生成本次网页.cjs')),
  htmlBase64Estimate: Math.ceil((editorScript.length + atlas.length * 4 / 3) / 3) * 4,
  totalCoreBytes: total,
}, null, 2));
