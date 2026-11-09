'use strict';
/**
 * Assemble a self-contained offline interior editor from public source.
 *
 * The generated HTML embeds the scene, the compiled public editor and a
 * procedurally generated material atlas. It has no network dependency and
 * no reference-product bundle. The node process only reads the task's own
 * configuration and scene, validates the binding, then writes one file.
 */

const crypto = require('crypto');
const fs = require('fs');
const path = require('path');

function fail(message) {
  const error = new Error(message);
  error.code = 'INTERIOR_TASK_INVALID';
  throw error;
}

function validators() {
  const sceneFile = [path.join(__dirname, '固定编辑器', '场景校验.cjs'), path.join(__dirname, 'validate-scene.cjs')]
    .find((file) => fs.existsSync(file));
  const deliveryFile = [path.join(__dirname, '固定编辑器', '交付阶段校验.cjs'), path.join(__dirname, 'validate-delivery.cjs')]
    .find((file) => fs.existsSync(file));
  if (!sceneFile || !deliveryFile) fail('室内场景校验器缺失，请重新构建公开室内编辑器资源');
  return {validateScene: require(sceneFile).validateScene, validateDelivery: require(deliveryFile).validateDelivery};
}

function readJson(file) {
  try {
    return JSON.parse(fs.readFileSync(file, 'utf8'));
  } catch (error) {
    fail(`无法读取 JSON：${path.basename(file)}（${error.message}）`);
  }
}

function isObject(value) {
  return Boolean(value) && typeof value === 'object' && !Array.isArray(value);
}

function id32(value, label) {
  if (typeof value !== 'string' || !/^[a-f0-9]{32}$/.test(value)) fail(`${label}必须为 32 位小写十六进制标识`);
}

function hex64(value, label) {
  if (typeof value !== 'string' || !/^[a-f0-9]{64}$/.test(value)) fail(`${label}必须为 64 位小写十六进制哈希`);
}

function validateConfig(config) {
  if (!isObject(config) || config.format !== 'interior-task/1.0') fail('任务配置 format 必须为 interior-task/1.0');
  id32(config.taskId, '任务配置 taskId');
  if (!isObject(config.primarySource)) fail('任务配置缺少 primarySource');
  const source = config.primarySource;
  id32(source.id, 'primarySource.id');
  if (typeof source.name !== 'string' || !source.name.trim()) fail('primarySource.name 缺失');
  hex64(source.sha256, 'primarySource.sha256');
  if (!['image', 'pdf'].includes(source.kind)) fail('primarySource.kind 必须为 image 或 pdf');
  if (!(Number(source.width) > 0) || !(Number(source.height) > 0)) fail('primarySource 像素尺寸无效');
  if (!Number.isInteger(source.page) || source.page < 1) fail('primarySource.page 必须为正整数');
  if (source.kind === 'pdf') hex64(source.rasterSha256, 'primarySource.rasterSha256');
  if (!isObject(config.config)) fail('任务配置缺少 config');
  if (!Array.isArray(config.schemeMap) || !config.schemeMap.length) fail('任务配置 schemeMap 不能为空');
  const seen = new Set();
  for (const item of config.schemeMap) {
    if (!isObject(item) || typeof item.sceneSchemeId !== 'string' || !item.sceneSchemeId) fail('schemeMap 项无效');
    if (seen.has(item.sceneSchemeId)) fail(`schemeMap 方案重复：${item.sceneSchemeId}`);
    seen.add(item.sceneSchemeId);
  }
  if (typeof config.outputDir !== 'string' || !config.outputDir) fail('任务配置 outputDir 缺失');
  return config;
}

function imageInfo(buffer) {
  if (buffer.length > 12 && buffer[0] === 0x89 && buffer.toString('ascii', 1, 4) === 'PNG') {
    if (buffer.toString('ascii', 12, 16) !== 'IHDR') fail('内嵌 PNG 缺少 IHDR');
    return {mime: 'image/png', width: buffer.readUInt32BE(16), height: buffer.readUInt32BE(20)};
  }
  if (buffer.length > 4 && buffer[0] === 0xff && buffer[1] === 0xd8) {
    let offset = 2;
    while (offset + 9 < buffer.length) {
      if (buffer[offset] !== 0xff) { offset += 1; continue; }
      const marker = buffer[offset + 1];
      if (marker === 0xd8 || marker === 0xd9) { offset += 2; continue; }
      const length = buffer.readUInt16BE(offset + 2);
      if (marker >= 0xc0 && marker <= 0xcf && ![0xc4, 0xc8, 0xcc].includes(marker)) {
        return {mime: 'image/jpeg', width: buffer.readUInt16BE(offset + 7), height: buffer.readUInt16BE(offset + 5)};
      }
      offset += 2 + length;
    }
    fail('内嵌 JPEG 未找到尺寸信息');
  }
  if (buffer.length > 30 && buffer.toString('ascii', 0, 4) === 'RIFF' && buffer.toString('ascii', 8, 12) === 'WEBP') {
    const chunk = buffer.toString('ascii', 12, 16);
    if (chunk === 'VP8X') {
      return {mime: 'image/webp', width: 1 + buffer.readUIntLE(24, 3), height: 1 + buffer.readUIntLE(27, 3)};
    }
  }
  fail('内嵌底图必须是 PNG、JPEG 或 WebP');
}

function decodeSource(source) {
  const image = source.imageData;
  if (image === 'workbench:primary-source') fail('作者稿占位符未经后端展开，不能直接生成网页');
  if (typeof image !== 'string') fail('场景必须内嵌本次底图');
  const match = /^data:image\/(png|jpeg|webp);base64,([A-Za-z0-9+/=]+)$/.exec(image);
  if (!match) fail('场景必须内嵌本次底图 data URI');
  const buffer = Buffer.from(match[2], 'base64');
  const info = imageInfo(buffer);
  const mime = `image/${match[1]}`;
  if (info.mime !== mime) fail('内嵌底图后缀与图片内容不符');
  return {buffer, info, mime};
}

function resolveInside(file, root, label) {
  const resolved = path.resolve(file);
  const base = path.resolve(root);
  if (resolved !== base && !resolved.startsWith(base + path.sep)) fail(`${label}越出本任务输出目录`);
  return resolved;
}

function validateBinding(config, scene) {
  const source = config.primarySource;
  const generation = scene.generation || {};
  const expected = {taskId: config.taskId, sourceFileId: source.id, sourceSha256: source.sha256, page: source.page};
  for (const [key, value] of Object.entries(expected)) {
    if (generation[key] !== value) fail(`场景没有绑定本次任务、底图与页码（${key} 不一致）`);
  }
  if (scene.source.name !== source.name) fail('场景内嵌底图名称与本任务不一致');
  const decoded = decodeSource(scene.source);
  const expectedSha = source.kind === 'pdf' ? source.rasterSha256 : source.sha256;
  const actualSha = crypto.createHash('sha256').update(decoded.buffer).digest('hex');
  if (actualSha !== expectedSha) fail('内嵌底图与原图或选定 PDF 页不一致');
  if (decoded.info.width !== Number(source.width) || decoded.info.height !== Number(source.height)) {
    fail('内嵌底图像素尺寸与本任务不一致');
  }
  if (source.kind === 'pdf') {
    if (scene.source.derivedFromSha256 !== source.sha256) fail('PDF 场景缺少原文件哈希');
    if (!Number.isInteger(scene.source.page) || scene.source.page !== source.page) fail('PDF 场景页码不一致');
  }
  const drawing = scene.drawing || {};
  if (Number(drawing.sourceWidth) !== Number(source.width) || Number(drawing.sourceHeight) !== Number(source.height)) {
    fail('场景原图像素尺寸不一致');
  }
  const expectedSchemes = new Set(config.schemeMap.map((item) => item.sceneSchemeId));
  const allowed = new Set(['base', 'mid', 'review', ...expectedSchemes]);
  const keys = new Set(Object.keys(scene.schemes || {}));
  if (!keys.has('base') || !keys.has('mid')) fail('场景必须包含 base 与 mid 兼容方案');
  for (const name of expectedSchemes) if (!keys.has(name)) fail(`场景缺少已选方案：${name}`);
  for (const name of keys) if (!allowed.has(name)) fail(`场景包含未选择的方案：${name}`);
  if (!keys.has(scene.style)) fail('场景当前方案不在已有方案中');
  if (config.config.styleMode === 'custom') {
    const profile = scene.schemes.custom && scene.schemes.custom.customProfile;
    const references = config.config.customStyleReferenceIds || [];
    if (!isObject(profile) || !Array.isArray(profile.referenceImageIds) || !profile.referenceImageIds.length
      || profile.referenceImageIds.some((id) => !references.includes(id))) {
      fail('自定义风格必须绑定本次上传的参考图片，不得沿用历史参考');
    }
  }
  const calibration = config.config.calibration;
  if (calibration) {
    const [a, b] = calibration.pointsPx;
    const pixelScale = Number(calibration.referenceLengthM) / Math.hypot(a[0] - b[0], a[1] - b[1]);
    const actualX = Number(drawing.scaleX) * Number(drawing.width) / Number(source.width);
    const actualZ = Number(drawing.scaleZ) * Number(drawing.height) / Number(source.height);
    const close = (value) => Math.abs(value - pixelScale) <= Math.max(1e-9, pixelScale * 1e-5);
    if (drawing.status !== 'user-calibrated' || !close(actualX) || !close(actualZ)) {
      fail('场景尺度与本次两点标定不一致');
    }
  } else if (drawing.status !== 'estimated') {
    fail('未标定的场景必须明确标记 estimated');
  }
  const output = path.resolve(config.outputDir);
  for (const [name, scheme] of Object.entries(scene.schemes || {})) {
    const audit = scheme.styleAudit || {};
    for (const supplied of audit.screenshots || []) {
      if (typeof supplied !== 'string' || !path.isAbsolute(supplied)) fail(`方案 ${name} 的验收截图必须为本任务内的绝对路径`);
      const resolved = resolveInside(supplied, output, '验收截图');
      if (!fs.existsSync(resolved) || !fs.statSync(resolved).isFile()) fail(`方案 ${name} 的验收截图不存在`);
      if (fs.statSync(resolved).size > 15 * 1024 * 1024) fail(`方案 ${name} 的验收截图超过 15 MB`);
    }
  }
}

function inspectTask(skillRoot, scene, preview, configPath) {
  const {validateScene, validateDelivery} = validators();
  const config = validateConfig(readJson(configPath));
  validateScene(scene);
  validateBinding(config, scene);
  if (!preview) validateDelivery(scene);
  return {ok: true, taskId: config.taskId, revision: scene.revision, preview: Boolean(preview)};
}

function readBundle() {
  const file = path.join(__dirname, '固定编辑器', '固定编辑器代码.json');
  if (!fs.existsSync(file)) fail('室内编辑器资源缺失：请在 frontend 目录执行 npm run build:interior 后重试');
  const bundle = readJson(file);
  for (const key of ['editorScript', 'editorCss', 'template', 'version']) {
    if (typeof bundle[key] !== 'string' || !bundle[key]) fail(`固定编辑器代码.json 缺少 ${key}`);
  }
  if (!bundle.template.includes('__INTERIOR_BOOTSTRAP__')) fail('固定编辑器模板缺少引导占位');
  return bundle;
}

function replaceAll(haystack, needle, value) {
  return haystack.split(needle).join(value);
}

function escapeHtml(value) {
  return String(value).replace(/[&<>"']/g, (char) => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[char]));
}

function buildHtml(config, scene, bundle, options = {}) {
  const preview = Boolean(options.preview);
  const runtime = {
    version: bundle.version,
    preview,
    builtAt: bundle.builtAt || '',
    engine: 'public-three-interior/1.0',
    taskId: config.taskId,
  };
  const moduleSource = [
    `window.__FIXED_INTERIOR_MODEL__=${JSON.stringify(scene)};`,
    `window.__FIXED_INTERIOR_RUNTIME__=${JSON.stringify(runtime)};`,
    `window.__FIXED_INTERIOR_ASSETS__=${JSON.stringify({atlas: bundle.atlas || ''})};`,
    bundle.editorScript,
  ].join('\n');
  const base64 = Buffer.from(moduleSource, 'utf8').toString('base64');
  const bootstrap = JSON.stringify({
    'editor-model': `data:text/javascript;base64,${base64}`,
    format: 'interior-standalone/1',
    name: scene.name,
    revision: scene.revision,
    preview,
    taskId: config.taskId,
  }).replace(/</g, '\\u003c');
  let html = bundle.template;
  html = replaceAll(html, '__INTERIOR_BOOTSTRAP__', bootstrap);
  html = replaceAll(html, '__INTERIOR_CSS__', bundle.editorCss);
  html = replaceAll(html, '__INTERIOR_TITLE__', escapeHtml(scene.name || '室内三维方案'));
  if (html.includes('__FROZEN_SCENE_MODULE__')) fail('模板包含未替换的冻结场景占位符');
  if (html.includes('https://cdn')) fail('离线网页不能依赖外部 CDN');
  return html;
}

function writeAtomic(file, html) {
  fs.mkdirSync(path.dirname(file), {recursive: true});
  const temporary = `${file}.tmp-${process.pid}-${Date.now()}`;
  fs.writeFileSync(temporary, html, 'utf8');
  try {
    fs.renameSync(temporary, file);
  } catch (error) {
    fs.writeFileSync(file, html, 'utf8');
    fs.rmSync(temporary, {force: true});
  }
}

function main(argv) {
  const args = argv.slice(2);
  const preview = args.includes('--preview');
  const positional = args.filter((item) => item !== '--preview');
  if (positional.length < 3) {
    process.stderr.write('用法：node 生成本次网页.cjs <任务配置.json> <场景.json> <输出.html> [--preview]\n');
    process.exit(2);
  }
  try {
    const [configPath, scenePath, outPath] = positional;
    const config = validateConfig(readJson(configPath));
    const scene = readJson(scenePath);
    inspectTask(__dirname, scene, preview, configPath);
    const bundle = readBundle();
    const html = buildHtml(config, scene, bundle, {preview});
    writeAtomic(outPath, html);
    process.stdout.write(JSON.stringify({ok: true, bytes: Buffer.byteLength(html, 'utf8'), preview}) + '\n');
  } catch (error) {
    process.stderr.write(String((error && error.message) || error));
    process.exit(1);
  }
}

if (require.main === module) main(process.argv);

module.exports = {inspectTask, buildHtml, validateConfig, validateBinding, imageInfo};
