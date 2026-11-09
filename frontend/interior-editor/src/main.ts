/// <reference types="vite/client" />
import css from './editor.css?raw';
import furnitureData from '../catalog/furniture.json';
import type {CatalogItem, RuntimeInfo, SceneDocument, SceneObject, Vec3} from './types';
import {cloneScene} from './types';
import {initMaterials} from './materials';
import {InteriorViewer} from './scene';

declare global {
  interface Window {
    __FIXED_INTERIOR_MODEL__?: SceneDocument;
    __FIXED_INTERIOR_RUNTIME__?: RuntimeInfo;
    __FIXED_INTERIOR_ASSETS__?: {atlas?: string; tile?: number; grid?: number};
    __INTERIOR_LAST_SNAPSHOT__?: SceneDocument;
    __INTERIOR_EDITOR_READY__?: boolean;
    FixedInterior?: Record<string, unknown>;
  }
}

const catalog = (furnitureData as unknown as {items: CatalogItem[]}).items;
const root = document.getElementById('app');
if (!root) throw new Error('编辑器挂载点缺失');

const style = document.createElement('style');
style.textContent = css;
document.head.appendChild(style);

function icon(name: string): string {
  const paths: Record<string, string> = {
    cube: '<path d="M12 2 3 7v10l9 5 9-5V7l-9-5Zm0 2.3L19 8l-7 3.9L5 8l7-3.7ZM5 10.1l6 3.3v6.2l-6-3.3v-6.2Zm8 9.5v-6.2l6-3.3v6.2l-6 3.3Z"/>',
    move: '<path d="M12 2 8 6h3v5H6V8l-4 4 4 4v-3h5v5H8l4 4 4-4h-3v-5h5v3l4-4-4-4v3h-5V6h3l-4-4Z"/>',
    rotate: '<path d="M12 5V2L7 6l5 4V7a5 5 0 1 1-5 5H5a7 7 0 1 0 7-7Z"/>',
    fit: '<path d="M4 9V4h5v2H6v3H4Zm11-5h5v5h-2V6h-3V4ZM4 15h2v3h3v2H4v-5Zm14 0h2v5h-5v-2h3v-3Z"/>',
    top: '<path d="M12 3 2 9l10 6 10-6-10-6Zm0 8.2L5.7 7.5 12 3.8l6.3 3.7L12 11.2ZM2 13l10 6 10-6v2l-10 6-10-6v-2Z"/>',
    undo: '<path d="M9 7V3L2 9l7 6v-4c4 0 7 2 8 6 1-6-2-10-8-10Z"/>',
    redo: '<path d="M15 7V3l7 6-7 6v-4c-4 0-7 2-8 6-1-6 2-10 8-10Z"/>',
    grid: '<path d="M3 3h18v18H3V3Zm2 2v4h4V5H5Zm6 0v4h4V5h-4Zm6 0v4h2V5h-2ZM5 11v4h4v-4H5Zm6 0v4h4v-4h-4Zm6 0v2h2v-2h-2ZM5 17v2h4v-2H5Zm6 0v2h4v-2h-4Z"/>',
    plan: '<path d="M4 3h16v18H4V3Zm2 2v14h12V5H6Zm2 2h5v5H8V7Zm6 0h3v3h-3V7Zm0 4h3v5h-3v-5Zm-6 2h5v4H8v-4Z"/>',
    save: '<path d="M4 3h13l3 3v15H4V3Zm2 2v14h12V7l-2-2H6Zm2 1h7v5H8V6Zm1 8h6v4H9v-4Z"/>',
    download: '<path d="M11 3h2v9l3-3 1.4 1.4L12 16l-5.4-5.4L8 9l3 3V3ZM4 18h16v3H4v-3Z"/>',
    upload: '<path d="M11 21h2v-9l3 3 1.4-1.4L12 8l-5.4 5.6L8 15l3-3v9ZM4 3h16v3H4V3Z"/>',
    help: '<path d="M12 2a10 10 0 1 0 0 20 10 10 0 0 0 0-20Zm0 18a8 8 0 1 1 0-16 8 8 0 0 1 0 16Zm-1-6h2v2h-2v-2Zm2-2h-2c0-2.5 3-2.3 3-4.5A2.5 2.5 0 0 0 9.5 7L8 8.4A4.5 4.5 0 0 1 16.5 8C16.5 11 13 11 13 14Z"/>',
    sofa: '<path d="M4 12V9a2 2 0 0 1 2-2h12a2 2 0 0 1 2 2v3h1v5h-2v2h-2v-2H7v2H5v-2H3v-5h1Zm2 0h12V9H6v3Zm1 2h10v1H7v-1Z"/>',
    trash: '<path d="M9 3h6l1 2h4v2H4V5h4l1-2Zm-3 6h12l-1 12H7L6 9Z"/>',
    copy: '<path d="M8 2h10v3h2v14H8V2Zm2 2v13h8V7h-2V4h-6ZM4 6h2v14h10v2H4V6Z"/>',
  };
  return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" width="17" height="17" aria-hidden="true"><g fill="currentColor">${paths[name] || paths.cube}</g></svg>`;
}

const rawModel = window.__FIXED_INTERIOR_MODEL__;
const runtime = window.__FIXED_INTERIOR_RUNTIME__ || {version: 'unknown', preview: false, builtAt: ''};
const assets = window.__FIXED_INTERIOR_ASSETS__ || {};
const atlas = assets.atlas;

function escapeText(value: string): string {
  return value.replace(/[&<>"']/g, (char) => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[char] || char));
}

if (!rawModel || !rawModel.schemes) {
  root.innerHTML = `<div class="ie-fatal"><h1>室内场景缺失</h1><p>离线网页中没有绑定本次任务的场景数据。请回到工作台重新导出。</p></div>`;
  window.FixedInterior = {ready: false, snapshot: () => null};
  throw new Error('scene model missing');
}

const model: SceneDocument = rawModel;

root.innerHTML = `
<div class="ie-shell">
  <header class="ie-top">
    <div class="ie-brand"><span class="ie-mark">${icon('cube')}</span><div><strong id="ie-name"></strong><span id="ie-subtitle"></span></div></div>
    <div class="ie-badges"><span class="ie-badge" id="ie-scheme-badge"></span><span class="ie-badge ie-badge-warm" id="ie-stage-badge"></span></div>
    <div class="ie-actions">
      <button class="ie-btn" data-action="fit" title="适合视图（F）">${icon('fit')}<span>适合</span></button>
      <button class="ie-btn" data-action="top" title="俯视（T）">${icon('top')}<span>俯视</span></button>
      <span class="ie-sep"></span>
      <button class="ie-btn" data-action="translate" title="移动（W）">${icon('move')}<span>移动</span></button>
      <button class="ie-btn" data-action="rotate" title="旋转（E）">${icon('rotate')}<span>旋转</span></button>
      <span class="ie-sep"></span>
      <button class="ie-btn" data-action="undo" title="撤销（Ctrl+Z）">${icon('undo')}</button>
      <button class="ie-btn" data-action="redo" title="重做（Ctrl+Y）">${icon('redo')}</button>
      <span class="ie-sep"></span>
      <button class="ie-btn ie-primary" data-action="export-glb" title="导出 GLB 模型">${icon('download')}<span>GLB</span></button>
      <button class="ie-btn" data-action="export-scene" title="导出项目 JSON">${icon('save')}<span>项目</span></button>
      <button class="ie-btn" data-action="import-scene" title="导入项目 JSON">${icon('upload')}</button>
      <button class="ie-btn" data-action="help" title="帮助">${icon('help')}</button>
    </div>
  </header>
  <div class="ie-body">
    <aside class="ie-panel ie-left">
      <section class="ie-section">
        <h2>方案</h2>
        <select id="ie-scheme" aria-label="方案版本"></select>
        <p class="ie-note">切换只改变当前显示；保存时保留全部方案。</p>
      </section>
      <section class="ie-section">
        <h2>房间</h2>
        <div id="ie-rooms" class="ie-rooms"></div>
      </section>
      <section class="ie-section">
        <h2>图层</h2>
        <label class="ie-toggle"><input type="checkbox" id="ie-layer-underlay" checked><span>底图</span></label>
        <label class="ie-toggle"><input type="checkbox" id="ie-layer-grid" checked><span>网格</span></label>
        <label class="ie-toggle"><input type="checkbox" id="ie-layer-ceiling"><span>顶面</span></label>
        <label class="ie-toggle"><input type="checkbox" id="ie-layer-labels" checked><span>房间名</span></label>
      </section>
      <section class="ie-section ie-provenance">
        <h2>来源</h2>
        <p id="ie-source"></p>
        <p class="ie-note" id="ie-assumption"></p>
      </section>
    </aside>
    <main class="ie-stage">
      <div id="ie-canvas" class="ie-canvas"></div>
      <div class="ie-hud"><span id="ie-hud-scheme"></span><span id="ie-hud-stats"></span></div>
      <div class="ie-empty-hint" id="ie-empty-hint">单击房间、墙体或家具进行选取；拖动工具栏的箭头移动，圆环旋转。</div>
    </main>
    <aside class="ie-panel ie-right">
      <section class="ie-section" id="ie-inspector-section">
        <h2>属性</h2>
        <div id="ie-inspector" class="ie-inspector"></div>
      </section>
      <section class="ie-section ie-catalog-section">
        <h2>添加家具</h2>
        <div class="ie-catalog-tools">
          <select id="ie-category" aria-label="家具类别"></select>
          <input id="ie-search" type="search" placeholder="搜索" aria-label="搜索家具">
        </div>
        <div id="ie-catalog" class="ie-catalog"></div>
      </section>
    </aside>
  </div>
  <footer class="ie-footer">
    <span id="ie-status">准备中</span>
    <span class="ie-footer-right"><span id="ie-version"></span><span class="ie-dot"></span><span id="ie-save-state">未修改</span></span>
  </footer>
</div>
<div class="ie-help" id="ie-help" hidden>
  <div class="ie-help-card">
    <button class="ie-close" data-action="close-help" aria-label="关闭">×</button>
    <h2>室内三维编辑器</h2>
    <p>公开源码实现的本地离线编辑器。所有几何由场景 JSON 和程序化家具生成，不使用云端服务或外部 CDN。</p>
    <h3>操作</h3>
    <ul>
      <li>左键拖动旋转，右键拖动平移，滚轮缩放。</li>
      <li>单击选取；W 移动、E 旋转、Delete 删除家具、Ctrl+D 复制。</li>
      <li>F 适合视图，T 俯视，G 网格，Esc 取消选取。</li>
    </ul>
    <h3>保存与导出</h3>
    <ul>
      <li>“项目”导出当前场景 JSON；“GLB”导出当前方案的可见几何。</li>
      <li>在工作台预览中，上方“保存当前编辑”会请求本页快照并提交后端校验。</li>
      <li>底图、尺度、任务哈希和来源证据不会被编辑器改写；新增家具标记为人工设计补充。</li>
    </ul>
    <p class="ie-note">草案中的估算尺寸、程序化家具和未核对开口仍需复核；不等同施工深化或单图转网格。</p>
  </div>
</div>
<input type="file" id="ie-import" accept=".json,application/json" hidden>`;

const $ = <T extends HTMLElement>(id: string): T => {
  const element = document.getElementById(id);
  if (!element) throw new Error(`缺少界面元素 ${id}`);
  return element as T;
};

$('ie-name').textContent = model.name || '室内三维方案';
$('ie-subtitle').textContent = `${model.generation?.taskId?.slice(0, 8) || '任务'} · r${model.revision ?? 1} · ${runtime.preview ? '草案预览' : '交付网页'}`;
$('ie-stage-badge').textContent = runtime.preview ? '草案预览' : '交付';
$('ie-version').textContent = `公开编辑器 ${runtime.version || ''}`;
$('ie-source').textContent = `${model.source?.name || '未命名底图'} · 第 ${model.generation?.page || 1} 页 · ${String(model.generation?.sourceSha256 || '').slice(0, 12)}`;
$('ie-assumption').textContent = (model.assumptions || []).slice(0, 3).join('；') || '未提供额外假设。';
$('ie-empty-hint').textContent = model.recognition?.summary || '单击房间、墙体或家具进行选取；拖动工具栏的箭头移动，圆环旋转。';

let viewer: InteriorViewer | null = null;
let selected: SceneObject | null = null;
let selectedKind: 'architecture' | 'furniture' | null = null;
let statusTimer = 0;

function setStatus(message: string): void {
  $('ie-status').textContent = message;
  window.clearTimeout(statusTimer);
  statusTimer = window.setTimeout(() => {
    if (viewer) $('ie-status').textContent = `已就绪 · ${viewer.stats().triangles.toLocaleString()} 个三角面`;
  }, 6000);
}

function markDirty(reason: string): void {
  if (reason === 'load') $('ie-save-state').textContent = '已导入项目';
  else if (reason === 'scheme') $('ie-save-state').textContent = '仅切换显示';
  else $('ie-save-state').textContent = '有未回传修改';
  refreshStats();
}

function refreshStats(): void {
  if (!viewer) return;
  const stats = viewer.stats();
  $('ie-hud-stats').textContent = `${stats.rooms} 房间 · ${stats.architecture} 构件 · ${stats.furniture} 家具`;
  $('ie-hud-scheme').textContent = viewer.getScheme();
  const undo = document.querySelector('[data-action="undo"]') as HTMLButtonElement | null;
  const redo = document.querySelector('[data-action="redo"]') as HTMLButtonElement | null;
  if (undo) undo.disabled = !viewer.canUndo();
  if (redo) redo.disabled = !viewer.canRedo();
}

function toast(message: string, tone: 'info' | 'error' = 'info'): void {
  const element = document.createElement('div');
  element.className = `ie-toast ${tone === 'error' ? 'ie-toast-error' : ''}`;
  element.textContent = message;
  document.body.appendChild(element);
  window.setTimeout(() => element.classList.add('ie-toast-out'), 2600);
  window.setTimeout(() => element.remove(), 3200);
}

function download(blob: Blob, name: string): void {
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement('a');
  anchor.href = url;
  anchor.download = name;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 5000);
}

function safeName(value: string): string {
  return (value || '室内方案').replace(/[\\/:*?"<>|]+/g, '_').slice(0, 60);
}

function field(labelText: string, input: HTMLElement, hint?: string): HTMLElement {
  const wrapper = document.createElement('label');
  wrapper.className = 'ie-field';
  const caption = document.createElement('span');
  caption.textContent = labelText;
  wrapper.append(caption, input);
  if (hint) {
    const small = document.createElement('small');
    small.textContent = hint;
    wrapper.append(small);
  }
  return wrapper;
}

function numberInput(id: string, value: number, step: number, min?: number): HTMLInputElement {
  const input = document.createElement('input');
  input.type = 'number';
  input.id = id;
  input.step = String(step);
  if (min !== undefined) input.min = String(min);
  input.value = Number(value || 0).toFixed(2);
  return input;
}

function renderSchemes(): void {
  const select = $<HTMLSelectElement>('ie-scheme');
  const doc = viewer?.getDocument() || model;
  select.replaceChildren();
  for (const [id, scheme] of Object.entries(doc.schemes || {})) {
    const option = document.createElement('option');
    option.value = id;
    option.textContent = `${scheme.name || id}${id === 'base' ? '（白模）' : ''}`;
    option.selected = id === viewer?.getScheme();
    select.append(option);
  }
  $('ie-scheme-badge').textContent = doc.schemes?.[viewer?.getScheme() || doc.style]?.name || viewer?.getScheme() || '';
}

function renderRooms(): void {
  const doc = viewer?.getDocument() || model;
  const container = $('ie-rooms');
  container.replaceChildren();
  if (!doc.rooms?.length) {
    const empty = document.createElement('p');
    empty.className = 'ie-note';
    empty.textContent = '本场景没有房间记录。';
    container.append(empty);
    return;
  }
  for (const room of doc.rooms) {
    const button = document.createElement('button');
    button.className = 'ie-room';
    button.type = 'button';
    button.textContent = room.name || room.id;
    button.addEventListener('click', () => viewer?.focusRoom(room.id));
    container.append(button);
  }
}

function selectOptions(values: string[], current: string): HTMLSelectElement {
  const select = document.createElement('select');
  for (const value of values) {
    const option = document.createElement('option');
    option.value = value;
    option.textContent = value;
    option.selected = value === current;
    select.append(option);
  }
  return select;
}

function renderInspector(): void {
  const container = $('ie-inspector');
  container.replaceChildren();
  if (!selected && selectedKind === null) {
    const empty = document.createElement('p');
    empty.className = 'ie-note';
    empty.textContent = '未选取对象。单击场景中的构件或家具。';
    container.append(empty);
    return;
  }
  const object = selected as SceneObject;
  const readOnly = selectedKind === 'architecture';
  const name = document.createElement('input');
  name.type = 'text';
  name.id = 'ie-obj-name';
  name.value = object.name || '';
  name.addEventListener('change', () => viewer?.updateSelected({name: name.value.trim() || object.name}));
  container.append(field('名称', name));

  const typeLine = document.createElement('div');
  typeLine.className = 'ie-type-line';
  typeLine.textContent = `${selectedKind === 'architecture' ? '建筑构件' : '家具'} · ${object.type}${readOnly ? '（保留来源证据）' : ''}`;
  container.append(typeLine);

  const doc = viewer?.getDocument() || model;
  const room = document.createElement('select');
  const none = document.createElement('option');
  none.value = '';
  none.textContent = '未指定房间';
  room.append(none);
  for (const item of doc.rooms || []) {
    const option = document.createElement('option');
    option.value = item.id;
    option.textContent = item.name || item.id;
    option.selected = item.id === object.room;
    room.append(option);
  }
  room.addEventListener('change', () => viewer?.updateSelected({room: room.value || null}));
  container.append(field('房间', room));

  const posGrid = document.createElement('div');
  posGrid.className = 'ie-grid3';
  const px = numberInput('ie-pos-x', object.pos[0], 0.05);
  const py = numberInput('ie-pos-y', object.pos[1], 0.05);
  const pz = numberInput('ie-pos-z', object.pos[2], 0.05);
  for (const [axis, input] of [['x', px], ['y', py], ['z', pz]] as const) {
    input.addEventListener('change', () => {
      const current = viewer?.getSelected();
      if (!current) return;
      const pos = [...current.pos] as Vec3;
      pos[axis === 'x' ? 0 : axis === 'y' ? 1 : 2] = Number(input.value) || 0;
      viewer?.updateSelected({pos});
    });
    posGrid.append(field(axis.toUpperCase(), input));
  }
  container.append(field('位置（米）', posGrid));

  const sizeGrid = document.createElement('div');
  sizeGrid.className = 'ie-grid3';
  const sx = numberInput('ie-size-x', object.size[0], 0.05, 0.02);
  const sy = numberInput('ie-size-y', object.size[1], 0.05, 0.02);
  const sz = numberInput('ie-size-z', object.size[2], 0.05, 0.02);
  for (const [axis, input] of [['x', sx], ['y', sy], ['z', sz]] as const) {
    input.addEventListener('change', () => {
      const current = viewer?.getSelected();
      if (!current) return;
      const size = [...current.size] as Vec3;
      size[axis === 'x' ? 0 : axis === 'y' ? 1 : 2] = Math.max(0.02, Number(input.value) || 0.02);
      viewer?.updateSelected({size});
    });
    sizeGrid.append(field(axis.toUpperCase(), input));
  }
  container.append(field('尺寸（米）', sizeGrid));

  const rotate = numberInput('ie-rotation', (Number(object.rotation || 0) * 180) / Math.PI, 5);
  rotate.addEventListener('change', () => viewer?.updateSelected({rotation: (Number(rotate.value) || 0) * Math.PI / 180}));
  container.append(field('旋转（度）', rotate));

  const color = document.createElement('input');
  color.type = 'color';
  color.id = 'ie-color';
  color.value = /^#[0-9a-f]{6}$/i.test(String(object.color || '')) ? String(object.color) : '#d8d0c2';
  color.addEventListener('change', () => viewer?.updateSelected({color: color.value}));
  const visible = document.createElement('input');
  visible.type = 'checkbox';
  visible.id = 'ie-visible';
  visible.checked = object.visible !== false;
  visible.addEventListener('change', () => viewer?.updateSelected({visible: visible.checked}));
  const misc = document.createElement('div');
  misc.className = 'ie-inline';
  misc.append(field('颜色', color), field('可见', visible));
  container.append(misc);

  const presets = ['paint', 'plaster', 'wood', 'stone', 'fabric', 'leather', 'metal', 'glass', 'ceramic'];
  const preset = selectOptions(presets, String(object.material?.preset || 'paint'));
  preset.id = 'ie-preset';
  preset.addEventListener('change', () => viewer?.updateSelected({material: {...(object.material || {}), preset: preset.value}}));
  container.append(field('材质', preset));

  if (!readOnly) {
    const actions = document.createElement('div');
    actions.className = 'ie-inspector-actions';
    const duplicate = document.createElement('button');
    duplicate.className = 'ie-btn';
    duplicate.type = 'button';
    duplicate.innerHTML = `${icon('copy')}<span>复制</span>`;
    duplicate.addEventListener('click', () => viewer?.duplicateSelected());
    const remove = document.createElement('button');
    remove.className = 'ie-btn ie-danger';
    remove.type = 'button';
    remove.innerHTML = `${icon('trash')}<span>删除</span>`;
    remove.addEventListener('click', () => viewer?.removeSelected());
    actions.append(duplicate, remove);
    container.append(actions);
  }
}

function syncInspectorValues(): void {
  const object = viewer?.getSelected();
  if (!object) return;
  const set = (id: string, value: string): void => {
    const input = document.getElementById(id) as HTMLInputElement | null;
    if (input && document.activeElement !== input) input.value = value;
  };
  set('ie-pos-x', object.pos[0].toFixed(2));
  set('ie-pos-y', object.pos[1].toFixed(2));
  set('ie-pos-z', object.pos[2].toFixed(2));
  set('ie-size-x', object.size[0].toFixed(2));
  set('ie-size-y', object.size[1].toFixed(2));
  set('ie-size-z', object.size[2].toFixed(2));
  set('ie-rotation', ((Number(object.rotation || 0) * 180) / Math.PI).toFixed(1));
}

function renderCatalog(): void {
  const category = $<HTMLSelectElement>('ie-category');
  if (!category.options.length) {
    const all = document.createElement('option');
    all.value = '';
    all.textContent = '全部类别';
    category.append(all);
    for (const name of [...new Set(catalog.map((item) => item.category))]) {
      const option = document.createElement('option');
      option.value = name;
      option.textContent = name;
      category.append(option);
    }
  }
  const query = $<HTMLInputElement>('ie-search').value.trim();
  const current = category.value;
  const container = $('ie-catalog');
  container.replaceChildren();
  const items = catalog.filter((item) => (!current || item.category === current) && (!query || item.name.includes(query) || item.type.includes(query)));
  if (!items.length) {
    const empty = document.createElement('p');
    empty.className = 'ie-note';
    empty.textContent = '没有匹配的家具。';
    container.append(empty);
    return;
  }
  for (const item of items) {
    const button = document.createElement('button');
    button.className = 'ie-catalog-item';
    button.type = 'button';
    button.title = `${item.name} · ${item.size.map((value) => value.toFixed(2)).join(' × ')} 米`;
    button.dataset.type = item.type;
    button.innerHTML = `${icon('sofa')}<span>${escapeText(item.name)}</span><small>${escapeText(item.category)}</small>`;
    button.addEventListener('click', () => {
      try {
        const object = viewer?.addFurniture(item);
        if (object) {
          toast(`已添加：${item.name}`);
          renderSchemes();
          refreshStats();
        }
      } catch (error) {
        toast(String(error instanceof Error ? error.message : error), 'error');
      }
    });
    container.append(button);
  }
}

function refreshAfterStructuralChange(): void {
  renderSchemes();
  renderRooms();
  renderInspector();
  refreshStats();
}

function action(name: string): void {
  if (!viewer) return;
  switch (name) {
    case 'fit': viewer.fit(); setStatus('已适合视图'); break;
    case 'top': viewer.topView(); setStatus('已切换俯视'); break;
    case 'translate': viewer.setTransformMode('translate'); setStatus('移动模式'); break;
    case 'rotate': viewer.setTransformMode('rotate'); setStatus('旋转模式'); break;
    case 'undo': viewer.undo(); refreshAfterStructuralChange(); break;
    case 'redo': viewer.redo(); refreshAfterStructuralChange(); break;
    case 'export-glb':
      void viewer.exportGlb().then((blob) => {
        download(blob, `${safeName(model.name)}-${viewer?.getScheme() || 'scheme'}.glb`);
        toast(`GLB 已导出：${(blob.size / 1024).toFixed(0)} KB`);
      }).catch((error) => toast(`GLB 导出失败：${String(error)}`, 'error'));
      break;
    case 'export-scene': {
      const snapshot = viewer.snapshot();
      window.__INTERIOR_LAST_SNAPSHOT__ = snapshot;
      download(new Blob([JSON.stringify(snapshot, null, 2)], {type: 'application/json'}), `${safeName(model.name)}-项目.json`);
      toast('项目 JSON 已导出');
      break;
    }
    case 'import-scene': $<HTMLInputElement>('ie-import').click(); break;
    case 'help': $('ie-help').hidden = false; break;
    case 'close-help': $('ie-help').hidden = true; break;
    default: break;
  }
}

document.addEventListener('click', (event) => {
  const target = (event.target as HTMLElement).closest('[data-action]') as HTMLElement | null;
  if (target?.dataset.action) action(target.dataset.action);
});

$<HTMLSelectElement>('ie-scheme').addEventListener('change', (event) => {
  const value = (event.target as HTMLSelectElement).value;
  viewer?.setScheme(value);
  renderSchemes();
  renderInspector();
});

$<HTMLInputElement>('ie-layer-underlay').addEventListener('change', (event) => viewer?.setUnderlayVisible((event.target as HTMLInputElement).checked));
$<HTMLInputElement>('ie-layer-grid').addEventListener('change', (event) => viewer?.setGridVisible((event.target as HTMLInputElement).checked));
$<HTMLInputElement>('ie-layer-ceiling').addEventListener('change', (event) => viewer?.setCeilingVisible((event.target as HTMLInputElement).checked));
$<HTMLInputElement>('ie-layer-labels').addEventListener('change', (event) => viewer?.setLabelsVisible((event.target as HTMLInputElement).checked));
$<HTMLSelectElement>('ie-category').addEventListener('change', renderCatalog);
$<HTMLInputElement>('ie-search').addEventListener('input', renderCatalog);

$<HTMLInputElement>('ie-import').addEventListener('change', async (event) => {
  const input = event.target as HTMLInputElement;
  const file = input.files?.[0];
  input.value = '';
  if (!file || !viewer) return;
  if (file.size > 40 * 1024 * 1024) {
    toast('项目 JSON 不能超过 40 MB', 'error');
    return;
  }
  try {
    const parsed = JSON.parse(await file.text()) as SceneDocument;
    if (!parsed || typeof parsed !== 'object' || !parsed.schemes || !Array.isArray(parsed.architecture)) {
      throw new Error('不是有效的室内场景 JSON');
    }
    viewer.loadScene(parsed);
    selected = null;
    selectedKind = null;
    refreshAfterStructuralChange();
    toast('项目已导入本页；回传时后端会重新校验底图绑定');
  } catch (error) {
    toast(`导入失败：${String(error instanceof Error ? error.message : error)}`, 'error');
  }
});

window.addEventListener('keydown', (event) => {
  const target = event.target as HTMLElement | null;
  if (target && /^(INPUT|TEXTAREA|SELECT)$/.test(target.tagName)) return;
  if (!viewer) return;
  const key = event.key.toLowerCase();
  if (key === 'f') { viewer.fit(); event.preventDefault(); }
  else if (key === 't') { viewer.topView(); event.preventDefault(); }
  else if (key === 'g') {
    const input = $<HTMLInputElement>('ie-layer-grid');
    input.checked = !input.checked;
    viewer.setGridVisible(input.checked);
    event.preventDefault();
  } else if (key === 'w') viewer.setTransformMode('translate');
  else if (key === 'e') viewer.setTransformMode('rotate');
  else if (key === 'escape') { viewer.clearSelection(); $('ie-help').hidden = true; }
  else if ((event.ctrlKey || event.metaKey) && key === 'z') { viewer.undo(); refreshAfterStructuralChange(); event.preventDefault(); }
  else if ((event.ctrlKey || event.metaKey) && (key === 'y' || (key === 'z' && event.shiftKey))) { viewer.redo(); refreshAfterStructuralChange(); event.preventDefault(); }
  else if ((event.ctrlKey || event.metaKey) && key === 'd') { viewer.duplicateSelected(); event.preventDefault(); }
  else if (key === 'delete' || key === 'backspace') { viewer.removeSelected(); event.preventDefault(); }
});

function boot(): void {
  const container = $('ie-canvas');
  void initMaterials(atlas, assets.tile, assets.grid).then((hasAtlas) => {
    try {
      viewer = new InteriorViewer(container, cloneScene(model), {
        onSelect: (object, kind) => {
          selected = object;
          selectedKind = kind;
          if (!viewer) return;
          renderInspector();
          $('ie-empty-hint').style.display = object ? 'none' : 'block';
        },
        onChange: (reason) => {
          markDirty(reason);
          syncInspectorValues();
          renderSchemes();
        },
        onStatus: (message) => setStatus(message),
        onError: (message) => toast(message, 'error'),
        onHistory: () => refreshStats(),
      });
      window.FixedInterior = {
        ready: true,
        version: runtime.version || '1.0.0',
        editor: 'public-three-interior/1.0',
        hasAtlas,
        snapshot: () => {
          const snapshot = viewer?.snapshot() || null;
          if (snapshot) window.__INTERIOR_LAST_SNAPSHOT__ = snapshot;
          return snapshot;
        },
        exportGlb: async () => {
          if (!viewer) throw new Error('编辑器尚未就绪');
          const blob = await viewer.exportGlb();
          return {bytes: blob.size, scheme: viewer.getScheme()};
        },
        select: (id: string | null) => viewer?.select(id || null),
        getStats: () => viewer?.stats() || null,
        getDocument: () => viewer?.getDocument() || null,
      };
      window.__INTERIOR_EDITOR_READY__ = true;
      renderSchemes();
      renderRooms();
      renderInspector();
      renderCatalog();
      refreshStats();
      setStatus(hasAtlas ? '编辑器已就绪（含材质图集）' : '编辑器已就绪（无材质图集，使用纯色材质）');
      $('ie-empty-hint').style.display = 'block';
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      container.innerHTML = `<div class="ie-fallback"><h2>无法启动三维视口</h2><p>${escapeText(message)}</p><p>场景数据仍可通过项目 JSON 导出；请使用支持 WebGL 的本机浏览器打开。</p></div>`;
      window.FixedInterior = {
        ready: false,
        snapshot: () => cloneScene(model),
        getDocument: () => cloneScene(model),
      };
      setStatus('WebGL 不可用');
      toast(`三维视口启动失败：${message}`, 'error');
    }
  });
}

boot();
