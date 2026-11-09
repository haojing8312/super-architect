'use strict';
/** Final-delivery gate: a design draft must never be exported as a delivered page. */

function validateDelivery(scene) {
  const stage = scene && scene.delivery && scene.delivery.stage;
  if (stage !== 'delivery-ready') {
    throw new Error('当前场景尚处于草案阶段，缺少交付验收证据，不能导出正式交付网页；请先完成核对、验收并显式标记 delivery.stage=delivery-ready');
  }
  const recognition = (scene.recognition && scene.recognition.status) || '';
  if (!['reviewed', 'confirmed', 'verified'].includes(recognition)) {
    throw new Error('识别核对尚未完成，缺少可复核的 review 状态，不能导出正式交付网页');
  }
  for (const [name, scheme] of Object.entries(scene.schemes || {})) {
    if (name === 'base' || name === 'mid') continue;
    const audit = scheme.styleAudit || {};
    if (audit.status !== 'approved') {
      throw new Error(`方案 ${name} 的验收状态为 ${audit.status || '缺失'}，缺少 approved 证据，不能导出正式交付网页`);
    }
    if (!Array.isArray(audit.screenshots) || !audit.screenshots.length) {
      throw new Error(`方案 ${name} 缺少实际验收截图，不能把草案标记为最终交付`);
    }
  }
  return true;
}

module.exports = {validateDelivery};
