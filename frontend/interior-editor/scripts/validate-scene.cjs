'use strict';
/**
 * Public scene validator for the locally built interior editor.
 *
 * It enforces the JSON envelope, geometry units, count limits and provenance
 * fields that the workbench backend also checks. It deliberately does not
 * infer a floor plan, choose furniture or rewrite source evidence.
 */

const MAX_ARCHITECTURE = 3000;
const MAX_ROOMS = 100;
const MAX_OBJECTS = 10000;
const MAX_SCENE_BYTES = 40 * 1024 * 1024;

function fail(message) {
  const error = new Error(message);
  error.code = 'INTERIOR_SCENE_INVALID';
  throw error;
}

function object(value, label) {
  if (!value || typeof value !== 'object' || Array.isArray(value)) fail(`${label}必须为对象`);
  return value;
}

function array(value, label, max) {
  if (!Array.isArray(value)) fail(`${label}必须为数组`);
  if (max !== undefined && value.length > max) fail(`${label}数量超过上限 ${max}`);
  return value;
}

function finite(value, label) {
  if (typeof value !== 'number' || !Number.isFinite(value)) fail(`${label}必须为有限数字`);
  return value;
}

function text(value, label, max = 200) {
  if (typeof value !== 'string' || !value.trim()) fail(`${label}必须为非空文本`);
  if (value.length > max) fail(`${label}长度超过 ${max}`);
  if (/[<>]/.test(value)) fail(`${label}不能包含尖括号`);
  return value;
}

function vec3(value, label, positive = false) {
  array(value, label, 3);
  if (value.length !== 3) fail(`${label}必须包含 3 个数字`);
  for (let index = 0; index < 3; index += 1) {
    finite(value[index], `${label}[${index}]`);
    if (positive && value[index] <= 0) fail(`${label}[${index}]必须大于 0`);
  }
  return value;
}

function hex64(value, label) {
  if (typeof value !== 'string' || !/^[a-f0-9]{64}$/.test(value)) fail(`${label}必须为 64 位小写十六进制哈希`);
  return value;
}

function id32(value, label) {
  if (typeof value !== 'string' || !/^[a-f0-9]{32}$/.test(value)) fail(`${label}必须为 32 位小写十六进制标识`);
  return value;
}

function validateSourceEvidence(evidence, label) {
  if (evidence === undefined) return;
  object(evidence, label);
  if (evidence.sourceSha256 !== undefined) hex64(evidence.sourceSha256, `${label}.sourceSha256`);
  if (evidence.sourceFileId !== undefined) id32(evidence.sourceFileId, `${label}.sourceFileId`);
  if (evidence.pixelBounds !== undefined) {
    array(evidence.pixelBounds, `${label}.pixelBounds`, 4);
    for (let index = 0; index < evidence.pixelBounds.length; index += 1) finite(evidence.pixelBounds[index], `${label}.pixelBounds[${index}]`);
  }
  if (evidence.inferred !== undefined && !Array.isArray(evidence.inferred)) fail(`${label}.inferred 必须为数组`);
}

function validateObject(value, label, roomIds, seen) {
  object(value, label);
  const id = text(value.id, `${label}.id`, 128);
  if (seen.has(id)) fail(`${label} 的对象 id 重复：${id}`);
  seen.add(id);
  text(value.name, `${label}.name`, 200);
  text(value.type, `${label}.type`, 64);
  if (value.layer !== undefined && typeof value.layer !== 'string') fail(`${label}.layer 必须为文本`);
  vec3(value.pos, `${label}.pos`);
  vec3(value.size, `${label}.size`, true);
  if (value.rotation !== undefined) finite(value.rotation, `${label}.rotation`);
  if (value.visible !== undefined && typeof value.visible !== 'boolean') fail(`${label}.visible 必须为布尔值`);
  if (value.color !== undefined && typeof value.color !== 'string') fail(`${label}.color 必须为文本`);
  if (value.material !== undefined) object(value.material, `${label}.material`);
  if (value.furnitureDetail !== undefined) object(value.furnitureDetail, `${label}.furnitureDetail`);
  if (value.room !== undefined && value.room !== null) {
    if (typeof value.room !== 'string' || !value.room) fail(`${label}.room 必须为房间标识或 null`);
    if (roomIds.size && !roomIds.has(value.room)) fail(`${label}.room 引用了不存在的房间 ${value.room}`);
  }
  validateSourceEvidence(value.sourceEvidence, `${label}.sourceEvidence`);
  return value;
}

function validateImageData(value, label) {
  if (typeof value !== 'string') fail(`${label} 必须为内嵌 data URI`);
  if (value.length > 60 * 1024 * 1024) fail(`${label} 超过 60 MB`);
  const match = /^data:image\/(png|jpeg|webp);base64,([A-Za-z0-9+/=]+)$/.exec(value);
  if (!match) fail(`${label} 必须为本次底图 PNG/JPEG/WebP 的 data URI；不得使用外部 URL 或占位符`);
  return {mime: `image/${match[1]}`, base64: match[2]};
}

function validateScene(scene) {
  object(scene, '场景');
  let bytes = 0;
  try {
    bytes = Buffer.byteLength(JSON.stringify(scene), 'utf8');
  } catch (error) {
    fail('场景无法序列化为 JSON');
  }
  if (bytes > MAX_SCENE_BYTES) fail('场景必须为不超过 40 MB 的 JSON 对象');
  if (typeof scene.format !== 'string' || !scene.format.startsWith('interior-studio/')) fail('场景 format 必须为 interior-studio/*');
  if (typeof scene.engineVersion !== 'string' || !scene.engineVersion) fail('场景 engineVersion 缺失');
  text(scene.name, '场景名称', 120);
  if (scene.isSample !== false) fail('场景必须明确标记 isSample=false，不能把样板户型冒充本次生成');
  if (!Number.isInteger(scene.revision) || scene.revision < 0) fail('场景 revision 必须为非负整数');

  object(scene.generation, '场景 generation');
  id32(scene.generation.taskId, 'generation.taskId');
  id32(scene.generation.sourceFileId, 'generation.sourceFileId');
  hex64(scene.generation.sourceSha256, 'generation.sourceSha256');
  if (!Number.isInteger(scene.generation.page) || scene.generation.page < 1) fail('generation.page 必须为正整数');

  object(scene.source, '场景 source');
  text(scene.source.name, 'source.name', 200);
  validateImageData(scene.source.imageData, 'source.imageData');
  if (scene.source.derivedFromSha256 !== undefined) hex64(scene.source.derivedFromSha256, 'source.derivedFromSha256');
  if (scene.source.page !== undefined && (!Number.isInteger(scene.source.page) || scene.source.page < 1)) fail('source.page 必须为正整数');

  const drawing = object(scene.drawing, '场景 drawing');
  for (const key of ['width', 'height', 'sourceWidth', 'sourceHeight', 'scaleX', 'scaleZ']) {
    if (!(finite(drawing[key], `drawing.${key}`) > 0)) fail(`drawing.${key} 必须大于 0`);
  }
  array(drawing.origin, 'drawing.origin', 2);
  if (drawing.origin.length !== 2) fail('drawing.origin 必须包含 2 个数字');
  finite(drawing.origin[0], 'drawing.origin[0]');
  finite(drawing.origin[1], 'drawing.origin[1]');
  if (!['estimated', 'user-calibrated'].includes(drawing.status)) fail('drawing.status 必须为 estimated 或 user-calibrated');

  const recognition = object(scene.recognition, '场景 recognition');
  if (typeof recognition.status !== 'string' || !recognition.status) fail('recognition.status 缺失');
  if (recognition.unresolved !== undefined && !Array.isArray(recognition.unresolved)) fail('recognition.unresolved 必须为数组');

  const architecture = array(scene.architecture, '场景 architecture', MAX_ARCHITECTURE);
  const rooms = array(scene.rooms, '场景 rooms', MAX_ROOMS);
  const roomIds = new Set();
  for (let index = 0; index < rooms.length; index += 1) {
    const room = object(rooms[index], `rooms[${index}]`);
    const roomId = text(room.id, `rooms[${index}].id`, 128);
    if (roomIds.has(roomId)) fail(`房间 id 重复：${roomId}`);
    roomIds.add(roomId);
    text(room.name, `rooms[${index}].name`, 120);
    array(room.center, `rooms[${index}].center`, 2);
    array(room.size, `rooms[${index}].size`, 2);
    if (room.center.length !== 2 || room.size.length !== 2) fail(`rooms[${index}] 的 center/size 必须各包含 2 个数字`);
    finite(room.center[0], `rooms[${index}].center[0]`); finite(room.center[1], `rooms[${index}].center[1]`);
    finite(room.size[0], `rooms[${index}].size[0]`); finite(room.size[1], `rooms[${index}].size[1]`);
    const polygon = array(room.polygon, `rooms[${index}].polygon`);
    if (polygon.length < 3) fail(`rooms[${index}].polygon 至少需要 3 个点`);
    for (let point = 0; point < polygon.length; point += 1) {
      array(polygon[point], `rooms[${index}].polygon[${point}]`, 2);
      if (polygon[point].length !== 2) fail(`rooms[${index}].polygon[${point}] 必须包含 2 个数字`);
      finite(polygon[point][0], `rooms[${index}].polygon[${point}][0]`);
      finite(polygon[point][1], `rooms[${index}].polygon[${point}][1]`);
    }
  }

  const architectureIds = new Set();
  function checkEvidence(item, label) {
    const evidence = item.sourceEvidence;
    if (!evidence || evidence.sourceFileId !== scene.generation.sourceFileId || evidence.sourceSha256 !== scene.generation.sourceSha256) {
      fail(`${label}.sourceEvidence 必须绑定本次底图`);
    }
  }
  for (let index = 0; index < architecture.length; index += 1) {
    validateObject(architecture[index], `architecture[${index}]`, roomIds, architectureIds);
    checkEvidence(architecture[index], `architecture[${index}]`);
  }

  const schemes = object(scene.schemes, '场景 schemes');
  const schemeNames = Object.keys(schemes);
  if (!schemeNames.length) fail('场景 schemes 不能为空');
  let totalObjects = 0;
  for (const name of schemeNames) {
    if (!/^[A-Za-z0-9_-]{1,64}$/.test(name)) fail(`方案标识无效：${name}`);
    const scheme = object(schemes[name], `schemes.${name}`);
    if (scheme.objects !== undefined) array(scheme.objects, `schemes.${name}.objects`, MAX_OBJECTS);
    const objects = scheme.objects || [];
    totalObjects += objects.length;
    if (totalObjects > MAX_OBJECTS) fail(`场景家具对象总数超过 ${MAX_OBJECTS}`);
    if (scheme.surfaceOverrides !== undefined) object(scheme.surfaceOverrides, `schemes.${name}.surfaceOverrides`);
    if (scheme.styleAudit !== undefined) object(scheme.styleAudit, `schemes.${name}.styleAudit`);
    if (scheme.customProfile !== undefined) {
      object(scheme.customProfile, `schemes.${name}.customProfile`);
      if (scheme.customProfile.referenceImageIds !== undefined) {
        array(scheme.customProfile.referenceImageIds, `schemes.${name}.customProfile.referenceImageIds`);
        for (const reference of scheme.customProfile.referenceImageIds) {
          if (typeof reference !== 'string' || !reference) fail(`schemes.${name}.customProfile.referenceImageIds 必须为文件标识`);
        }
      }
    }
    const seen = new Set();
    for (let index = 0; index < objects.length; index += 1) {
      validateObject(objects[index], `schemes.${name}.objects[${index}]`, roomIds, seen);
      checkEvidence(objects[index], `schemes.${name}.objects[${index}]`);
    }
  }
  if (typeof scene.style !== 'string' || !schemes[scene.style]) fail('scene.style 必须指向本次已有方案');
  object(scene.delivery, '场景 delivery');
  if (typeof scene.delivery.stage !== 'string' || !scene.delivery.stage) fail('delivery.stage 缺失');
  if (scene.assumptions !== undefined && (!Array.isArray(scene.assumptions) || scene.assumptions.some((item) => typeof item !== 'string'))) fail('assumptions 必须为文本数组');
  if (scene.cameras !== undefined && !Array.isArray(scene.cameras)) fail('cameras 必须为数组');
  return true;
}

module.exports = {validateScene};
