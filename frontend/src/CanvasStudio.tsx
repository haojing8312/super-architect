import {useCallback, useEffect, useId, useRef, useState} from 'react';
import type {PointerEvent as ReactPointerEvent, ClipboardEvent as ReactClipboardEvent} from 'react';
import {AlignLeft, Bold, Circle, Copy, Download, FolderOpen, Grid2X2, Group, Hand, Image, Italic, Maximize2, Minus, MousePointer2, Pencil, Plus, Redo2, Save, Square, Trash2, Type, Undo2, Ungroup, Upload, X} from 'lucide-react';
import {api, getToken, Row} from './api';
import {alignElements, arrangeGrid, cloneSelection, distributeElements, elementBounds, expandGroupSelection, intersects, resizeElements, screenToWorld, selectionBounds, selectionUnitCount, translateElements, zoomAt} from './canvasGeometry';
import type {Alignment, Bounds, CanvasElement, Point, Viewport} from './canvasGeometry';
import './CanvasStudio.css';
import {CanvasCollaboration} from './CanvasCollaboration';
import type {CollaborationActions} from './CanvasCollaboration';
import {acceptCollaborationVersion, acceptLateAcknowledgement, bindCollaboration, bindingFor, canvasWindowBranch, clearCollaborationBindings, collaborationAction, collaborationSnapshot, mergeSharedDocument, publishCanvas, readCanvasDraft, recoverPendingDraft, writeCanvasDraft} from './canvasCollaborationProtocol';
import type {CanvasDraft, CollaborationStatus} from './canvasCollaborationProtocol';

type Document = Row & {name: string; elements: CanvasElement[]; viewport: Viewport; meta: Row; schema_version: number};
type RecordState = {id: string; document: Document; revision: number; savedRevision: number; tail: Promise<void>; assets: Set<Promise<void>>; undo: Document[]; redo: Document[]; owners: Set<symbol>; recoveredDraft?: boolean};
const records = new Map<string, RecordState>();
const defaultViewport: Viewport = {x: 0, y: 0, zoom: 1};
const allowedTypes = new Set(['rect', 'ellipse', 'text', 'image', 'pencil']);
const number = (value: unknown, fallback: number) => typeof value === 'number' && Number.isFinite(value) ? value : fallback;

function groupEntries(document: Document): unknown[] {return Array.isArray(document.meta.groups) ? document.meta.groups : [];}
function metadataGroupId(value: unknown): string | null {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return null;
  const row = value as Row, id = row.GroupId ?? row.groupId;
  return typeof id === 'string' && id ? id : null;
}
function groupName(value: unknown): string {return typeof value === 'string' ? value.replace(/[\u0000-\u001f\u007f]/g, ' ').slice(0, 200) : '组合';}
function groupFontSize(value: unknown): number {return Math.max(8, Math.min(120, number(value, 24)));}
function groupInfo(document: Document, id: string) {
  const metadata = (groupEntries(document).find(entry => metadataGroupId(entry) === id) || {}) as Row;
  return {metadata, name: groupName(metadata.GroupName ?? metadata.groupName ?? metadata.name), fontSize: groupFontSize(metadata.FontSize ?? metadata.fontSize)};
}
function groupRow(metadata: Row, id: string, name: string, fontSize: number): Row {
  return {...metadata, GroupId: id, GroupName: groupName(name), FontSize: groupFontSize(fontSize),
    ...('groupId' in metadata ? {groupId: id} : {}), ...('groupName' in metadata ? {groupName: groupName(name)} : {}), ...('fontSize' in metadata ? {fontSize: groupFontSize(fontSize)} : {})};
}
function withGroupInfo(document: Document, id: string, patch: {name?: string; fontSize?: number}): Document {
  const info = groupInfo(document, id), name = patch.name ?? info.name, fontSize = patch.fontSize ?? info.fontSize;
  let found = false;
  const groups = groupEntries(document).map(entry => {if (metadataGroupId(entry) !== id) return entry; found = true; return groupRow(entry as Row, id, name, fontSize);});
  if (!found) groups.push(groupRow({}, id, name, fontSize));
  return {...document, meta: {...document.meta, groups}};
}
function pruneGroups(document: Document): Document {
  if (!Array.isArray(document.meta.groups)) return document;
  const live = new Set(document.elements.map(element => element.groupId).filter(Boolean));
  return {...document, meta: {...document.meta, groups: groupEntries(document).filter(entry => {const id = metadataGroupId(entry); return !id || live.has(id);})}};
}
function copyGroupInfo(source: Document, original: CanvasElement[], copies: CanvasElement[], destination: Document): Document {
  const seen = new Set<string>(), groups = [...groupEntries(destination)];
  original.forEach((element, index) => {
    const id = element.groupId, newId = copies[index]?.groupId;
    if (!id || !newId || seen.has(newId)) return;
    seen.add(newId); const info = groupInfo(source, id);
    groups.push(groupRow(info.metadata, newId, info.name, info.fontSize));
  });
  return {...destination, meta: {...destination.meta, groups}};
}
const titleBaseline = (box: Bounds, size: number) => box.y - Math.max(8, size * .35 + 4);

function imageReference(href: unknown): string | undefined {
  if (typeof href !== 'string') return undefined;
  if (/^data:image\/(?:png|jpeg|jpg|gif|webp|bmp);base64,/i.test(href)) return href;
  try {
    const url = new URL(href, location.origin);
    if (url.origin === location.origin && /^\/api\/(?:files\/[a-f0-9]{32}|resources\/[a-f0-9]{32}\/file)$/.test(url.pathname)) return url.pathname;
  } catch { /* unsupported image references are never executed */ }
  return undefined;
}
function normalizeElements(value: unknown): CanvasElement[] {
  if (!Array.isArray(value)) return [];
  const ids = new Set<string>();
  return value.filter(e => e && typeof e === 'object' && allowedTypes.has(e.type)).map(e => {
    const id = typeof e.id === 'string' && e.id && !ids.has(e.id) ? e.id : crypto.randomUUID(); ids.add(id);
    const result: CanvasElement = {...e, id, x: number(e.x, 0), y: number(e.y, 0), w: Math.max(1, number(e.w, 120)), h: Math.max(1, number(e.h, 40)), color: typeof e.color === 'string' ? e.color : '#2F3C48'};
    if (result.type === 'image') result.href = imageReference(e.href);
    if (e.points) result.points = Array.isArray(e.points) ? e.points.filter((p: Point) => Number.isFinite(p?.x) && Number.isFinite(p?.y)).map((p: Point) => ({x: p.x, y: p.y})) : [];
    if (result.type === 'pencil' && result.points?.length) Object.assign(result, elementBounds(result));
    return result;
  });
}
function normalizeDocument(raw: Row): Document {
  const value = raw.data && Array.isArray(raw.data.elements) ? {...raw, ...raw.data} : raw;
  const viewport = value.viewport || {}, meta = value.meta && typeof value.meta === 'object' ? value.meta : {};
  const name = String(value.name || meta.name || '未命名画布');
  return {...value, name, elements: normalizeElements(value.elements), viewport: {...viewport, x: number(viewport.x, 0), y: number(viewport.y, 0), zoom: Math.max(.01, Math.min(100, number(viewport.zoom, 1))), ...(viewport.zoomY !== undefined ? {zoomY: Math.max(.01, Math.min(100, number(viewport.zoomY, 1)))} : {})}, meta: {...meta, name}, schema_version: 1};
}
function payload(document: Document) {
  const {id: _id, data: _data, ...value} = document;
  return {...value, meta: {...document.meta, name: document.name, schema_version: 1}, schema_version: 1};
}
function enqueueSave(record: RecordState): Promise<void> {
  const revision = record.revision, document = record.document;
  const job = record.tail.catch(() => {}).then(async () => {
    if (revision <= record.savedRevision) return;
    if (bindingFor(record.id)) await publishCanvas(record.id, payload(document), revision);
    else {
      await api(`/canvas/${encodeURIComponent(record.id)}`, payload(document), 'PUT');
      if (record.recoveredDraft) await writeCanvasDraft({canvasId: record.id, document, localRevision: revision, baseRevision: null, peerId: null, dirty: false, updatedAt: Date.now()});
    }
    record.savedRevision = Math.max(record.savedRevision, revision);
  });
  record.tail = job;
  return job;
}
async function flush(record: RecordState | null) {
  if (!record) return;
  while (record.assets.size) await Promise.all([...record.assets]);
  if (record.revision > record.savedRevision) await enqueueSave(record);
  else await record.tail;
}
function releaseRecord(record: RecordState | null, owner: symbol) {
  if (!record) return;
  record.owners.delete(owner);
  if (!record.owners.size && !record.assets.size && record.savedRevision >= record.revision && records.get(record.id) === record) records.delete(record.id);
}
type Gesture = {kind: 'move' | 'resize' | 'draw' | 'pan' | 'marquee'; before: Document; start: Point; ids: string[]; pointer: number; changed: boolean; bounds?: Bounds; handle?: string; added?: string; additive?: string[]};
const tools = [['select', '选择', MousePointer2], ['hand', '移动画布', Hand], ['pencil', '自由绘制', Pencil], ['rect', '矩形', Square], ['ellipse', '圆形', Circle], ['text', '文字', Type], ['image', '图片', Image]] as const;

export function CanvasStudio({canvasId, sessionId, onError, toast, onDeliverable}: {canvasId?: string; sessionId?: string; onError: (message: string) => void; toast?: (message: string) => void; onDeliverable?: (draft: Row) => void}) {
  const initialId = canvasId || sessionId || 'default', patternId = useId().replace(/:/g, '');
  const owner = useRef(Symbol('canvas')), mounted = useRef(true), active = useRef<RecordState | null>(null), epoch = useRef(0), readyRef = useRef(false);
  const networkBusy = useRef(false);
  const errorRef = useRef(onError), toastRef = useRef(toast); errorRef.current = onError; toastRef.current = toast;
  const svg = useRef<SVGSVGElement>(null), surface = useRef<HTMLDivElement>(null), imageInput = useRef<HTMLInputElement>(null), projectInput = useRef<HTMLInputElement>(null), gesture = useRef<Gesture | null>(null), space = useRef(false);
  const selectedRef = useRef<string[]>([]), imageUrls = useRef(new Map<string, string>());
  const [document, setDocument] = useState<Document>(() => normalizeDocument({elements: [], viewport: defaultViewport}));
  const [activeId, setActiveId] = useState(initialId), [ready, setReady] = useState(false), [transitioning, setTransitioning] = useState(false), [saveStatus, setSaveStatus] = useState('已保存到本机');
  const [selected, setSelected] = useState<string[]>([]), [tool, setTool] = useState('select'), [color, setColor] = useState('#2F3C48'), [fontFamily, setFontFamily] = useState('Microsoft YaHei UI'), [fontSize, setFontSize] = useState(20), [bold, setBold] = useState(false), [italic, setItalic] = useState(false);
  const [marquee, setMarquee] = useState<Bounds | null>(null), [historyVersion, setHistoryVersion] = useState(0), [assetBusy, setAssetBusy] = useState(false), [urlVersion, setUrlVersion] = useState(0), [projects, setProjects] = useState<Row[]>([]), [projectList, setProjectList] = useState(false), [projectLoading, setProjectLoading] = useState(false), [columns, setColumns] = useState(3), [gap, setGap] = useState(24);

  const select = useCallback((ids: string[]) => {selectedRef.current = ids; if (mounted.current) setSelected(ids);}, []);
  const renderRecord = useCallback((record: RecordState) => {
    if (mounted.current && active.current === record) {const binding = bindingFor(record.id); setDocument(record.document); setHistoryVersion(v => v + 1); setSaveStatus(record.savedRevision >= record.revision ? binding ? '已发布并确认' : '已保存到本机' : binding?.state.pending ? '等待主机确认，草稿已保留' : binding ? '更改待发布' : '更改待保存');}
  }, []);
  const backupRecord = useCallback(async (record: RecordState) => {
    const binding = bindingFor(record.id);
    if (!binding && !record.recoveredDraft) return;
    await writeCanvasDraft({canvasId: record.id, document: payload(record.document), localRevision: record.revision, baseRevision: binding?.baseRevision ?? null, peerId: binding?.peerId ?? null, dirty: record.revision > record.savedRevision, updatedAt: Date.now()});
  }, []);
  const update = useCallback((next: Document, history = true, record = active.current) => {
    if (!record || record.document === next) return;
    if (history) {record.undo.push(record.document); record.redo = [];}
    record.document = next; record.revision++; renderRecord(record);
    if (history && bindingFor(record.id)) void backupRecord(record).catch(error => errorRef.current('协作草稿未能持久保存：' + String(error)));
  }, [renderRecord, backupRecord]);
  const finishGesture = useCallback((cancel = false) => {
    const current = gesture.current, record = active.current; gesture.current = null; setMarquee(null);
    if (!current || !record) return;
    if (current.changed) {
      if (cancel) update(current.before, false, record);
      else {record.undo.push(current.before); record.redo = []; setHistoryVersion(v => v + 1);}
      if (bindingFor(record.id)) void backupRecord(record).catch(error => errorRef.current('协作草稿未能持久保存：' + String(error)));
    }
    try {if (svg.current?.hasPointerCapture(current.pointer)) svg.current.releasePointerCapture(current.pointer);} catch { /* pointer may already be released */ }
  }, [update, backupRecord]);
  const install = useCallback((raw: Row, fallbackId: string) => {
    const id = String(raw.id || raw.data?.id || fallbackId), value = normalizeDocument(raw), previous = active.current;
    const record = records.get(id) || {id, document: value, revision: 0, savedRevision: 0, tail: Promise.resolve(), assets: new Set<Promise<void>>(), undo: [], redo: [], owners: new Set<symbol>()};
    record.document = value; record.revision++; record.savedRevision = record.revision; record.undo = []; record.redo = []; record.owners.add(owner.current); records.set(id, record);
    if (previous !== record) releaseRecord(previous, owner.current);
    active.current = record; gesture.current = null; select([]); setMarquee(null); setDocument(value); setActiveId(id); setHistoryVersion(v => v + 1); setSaveStatus('已保存到本机'); readyRef.current = true; setReady(true);
  }, [select]);
  const changeProject = useCallback(async (fetchProject: () => Promise<Row>, targetId?: string, retainDraft = false) => {
    const ticket = ++epoch.current; finishGesture(); readyRef.current = false; setReady(false); setTransitioning(true);
    try {
      if (retainDraft && active.current) {while (active.current.assets.size) await Promise.all([...active.current.assets]); await backupRecord(active.current);}
      else await flush(active.current);
      if (targetId && records.get(targetId) !== active.current) await flush(records.get(targetId) || null);
      if (!mounted.current || epoch.current !== ticket) return;
      let result = await fetchProject();
      if (!mounted.current || epoch.current !== ticket) return;
      let id = String(result.id || targetId), draft = await readCanvasDraft(id).catch(() => null);
      if (draft?.dirty && draft.branchId && draft.branchId !== canvasWindowBranch()) {
        // A different window owns this branch. Recover it as an independent
        // project, never replay it into the original authoritative canvas.
        const originalId = id, original = normalizeDocument(result), recovered = normalizeDocument(draft.document);
        const name = (recovered.name || '画布').slice(0, 150) + '（未确认草稿）';
        result = await api('/canvas-projects', {name, data: {...payload(recovered), name, meta: {...recovered.meta, name}}});
        await writeCanvasDraft({canvasId: originalId, document: payload(original), localRevision: 0, baseRevision: null, peerId: null, dirty: false, updatedAt: Date.now()});
        id = String(result.id); draft = null; toastRef.current?.('其他窗口的未确认草稿已恢复为独立本地副本，原项目保持不变');
      }
      const status = await collaborationSnapshot().catch(() => null);
      if (!mounted.current || epoch.current !== ticket) return;
      const shared = status?.canvas_id === id && status.role !== 'none';
      if (shared) bindCollaboration(id, status!, draft?.dirty ? draft.baseRevision ?? status!.revision : status!.revision);
      const restored = draft?.dirty ? {...draft.document, id} : shared && status?.document && !status.pending ? {...mergeSharedDocument(result, status.document), id} : result;
      if (shared && !draft?.dirty && !status!.pending) acceptCollaborationVersion(id, status!);
      install(restored, id);
      const record = active.current!;
      if (draft) {record.recoveredDraft = true; record.revision = Math.max(record.revision, draft.localRevision); record.savedRevision = draft.dirty ? record.revision - 1 : record.revision; const delta = record.revision - draft.localRevision; if (shared) recoverPendingDraft(id, {...draft, localRevision: record.revision, submittedRevision: draft.submittedRevision != null ? draft.submittedRevision + delta : null}); renderRecord(record);}
      if (shared && status!.pending && !draft?.dirty) {record.savedRevision = record.revision - 1; renderRecord(record);}
      setProjectList(false);
    } catch (error) {
      if (mounted.current && epoch.current === ticket) {
        const cached = active.current || (targetId ? records.get(targetId) : null);
        if (cached && !active.current) {active.current = cached; cached.owners.add(owner.current); setDocument(cached.document); setActiveId(cached.id); setHistoryVersion(v => v + 1);}
        errorRef.current('画布未切换，原项目已保留：' + String(error)); readyRef.current = !!cached; setReady(readyRef.current); setSaveStatus(cached && cached.revision > cached.savedRevision ? '保存失败，内容保留在本机窗口' : '原项目已保留');
      }
    } finally {if (mounted.current && epoch.current === ticket) setTransitioning(false);}
  }, [finishGesture, install, backupRecord, renderRecord]);

  useEffect(() => {
    mounted.current = true;
    void changeProject(() => api(`/canvas/${encodeURIComponent(initialId)}`), initialId);
    return () => {
      mounted.current = false; epoch.current++; readyRef.current = false;
      const record = active.current;
      void flush(record).then(() => releaseRecord(record, owner.current)).catch(error => errorRef.current('画布“' + (record?.document.name || '') + '”未保存：' + String(error)));
    };
  }, [initialId, changeProject]);
  useEffect(() => {
    const record = active.current;
    if (!ready || !record || record.savedRevision >= record.revision || gesture.current) return;
    const timer = window.setTimeout(() => {
      if (active.current === record) setSaveStatus('保存中…');
      void enqueueSave(record).then(() => renderRecord(record)).catch(error => {if (mounted.current && active.current === record) setSaveStatus('保存失败'); errorRef.current('画布保存失败：' + String(error));});
    }, 500);
    return () => clearTimeout(timer);
  }, [document, ready, historyVersion, renderRecord]);
  useEffect(() => {
    const host = surface.current; if (!host) return;
    const wheel = (event: WheelEvent) => {
      if (!readyRef.current || gesture.current || !active.current) return;
      event.preventDefault(); const rect = host.getBoundingClientRect(), v = active.current.document.viewport;
      update({...active.current.document, viewport: zoomAt(v, {x: event.clientX - rect.left, y: event.clientY - rect.top}, v.zoom * Math.exp(-event.deltaY * .0015))});
    };
    host.addEventListener('wheel', wheel, {passive: false}); return () => host.removeEventListener('wheel', wheel);
  }, [update]);
  const imageReferenceKey = [...new Set(document.elements.filter(e => e.type === 'image' && e.href?.startsWith('/api/')).map(e => e.href!))].sort().join('\n');
  useEffect(() => {
    let cancelled = false;
    const references = imageReferenceKey ? imageReferenceKey.split('\n') : [];
    for (const [href, url] of imageUrls.current) if (!references.includes(href)) {URL.revokeObjectURL(url); imageUrls.current.delete(href);}
    for (const href of references) if (!imageUrls.current.has(href)) {
      void (async () => {const response = await fetch(href, {headers: {'X-Workbench-Token': await getToken()}}); if (!response.ok) throw new Error('画布图片读取失败'); const blob = await response.blob(); if (!/^image\/(png|jpeg|gif|webp|bmp)/i.test(blob.type)) throw new Error('图片格式不受支持'); const url = URL.createObjectURL(blob); if (cancelled) {URL.revokeObjectURL(url); return;} imageUrls.current.set(href, url); setUrlVersion(v => v + 1);})().catch(error => {if (!cancelled) errorRef.current(String(error));});
    }
    return () => {cancelled = true;};
  }, [imageReferenceKey]);
  useEffect(() => () => {imageUrls.current.forEach(url => URL.revokeObjectURL(url)); imageUrls.current.clear();}, []);

  const worldPoint = (event: {clientX: number; clientY: number}): Point => {
    const rect = svg.current!.getBoundingClientRect(); return screenToWorld({x: event.clientX - rect.left, y: event.clientY - rect.top}, active.current!.document.viewport);
  };
  const selectedElements = document.elements.filter(e => selected.includes(e.id)), bounds = selectionBounds(document.elements, selected), unitCount = selectionUnitCount(document.elements, selected), selectedText = selectedElements.filter(e => e.type === 'text'), singleText = selectedElements.length === 1 && selectedElements[0].type === 'text' ? selectedElements[0] : null;
  const groupBoxes = [...new Set(document.elements.map(element => element.groupId).filter((id): id is string => !!id))].map(groupId => ({groupId, box: selectionBounds(document.elements, document.elements.filter(element => element.groupId === groupId).map(element => element.id))!}));
  const selectedGroupId = selectedElements.length && selectedElements[0].groupId && selectedElements.every(element => element.groupId === selectedElements[0].groupId) ? selectedElements[0].groupId : null;
  const selectedGroup = selectedGroupId ? groupInfo(document, selectedGroupId) : null;
  const measuredText = (text: string, family: string, size: number, weight: string | number, style: string) => {
    const context = window.document.createElement('canvas').getContext('2d')!; context.font = `${style} ${weight} ${size}px "${family}", sans-serif`;
    const lines = text.split('\n'); return {w: Math.max(24, ...lines.map(line => context.measureText(line || ' ').width)), h: Math.max(size * 1.3, lines.length * size * 1.3)};
  };
  const textElement = (text: string, point: Point): CanvasElement => ({id: crypto.randomUUID(), type: 'text', ...point, ...measuredText(text, fontFamily, fontSize, bold ? 'bold' : 'normal', italic ? 'italic' : 'normal'), color, text, fontFamily, fontSize, fontWeight: bold ? 'bold' : 'normal', fontStyle: italic ? 'italic' : 'normal'});
  function down(event: ReactPointerEvent<SVGSVGElement>) {
    if (!readyRef.current || !active.current || (event.button !== 0 && event.button !== 1)) return;
    surface.current?.focus({preventScroll: true}); event.preventDefault(); const point = worldPoint(event), before = active.current.document;
    svg.current?.setPointerCapture(event.pointerId);
    const handle = (event.target as SVGElement).closest('[data-resize]')?.getAttribute('data-resize');
    let id = (event.target as SVGElement).closest('[data-element]')?.getAttribute('data-element');
    if (!id && !handle && tool === 'select') {
      // Hit the interior of outlined elements even if a renderer treats the
      // transparent paint as an unpainted region. Use the same world transform
      // as dragging, including the legacy independent Y scale.
      const hit = [...before.elements].reverse().find(element => {
        if (element.type === 'pencil') return false; // its transparent hit stroke follows the actual path
        const box = elementBounds(element);
        if (point.x < box.x || point.x > box.x + box.w || point.y < box.y || point.y > box.y + box.h) return false;
        return element.type !== 'ellipse' || ((point.x - box.x - box.w / 2) / (box.w / 2)) ** 2 + ((point.y - box.y - box.h / 2) / (box.h / 2)) ** 2 <= 1;
      });
      id = hit?.id;
      if (!id) {
        const groupId = (event.target as SVGElement).closest('[data-group-hit]')?.getAttribute('data-group-hit');
        if (groupId) id = before.elements.find(element => element.groupId === groupId)?.id;
      }
    }
    if (event.button === 1 || tool === 'hand' || space.current) {gesture.current = {kind: 'pan', before, start: {x: event.clientX, y: event.clientY}, ids: [], pointer: event.pointerId, changed: false}; return;}
    if (handle && bounds) {gesture.current = {kind: 'resize', before, start: point, ids: selectedRef.current, pointer: event.pointerId, changed: false, bounds, handle}; return;}
    if (tool === 'select') {
      const additive = event.ctrlKey || event.metaKey || event.shiftKey;
      if (id) {
        const group = expandGroupSelection(before.elements, [id]); let ids = selectedRef.current;
        if (additive) ids = ids.includes(id) ? ids.filter(value => !group.includes(value)) : [...new Set([...ids, ...group])];
        else if (!ids.includes(id)) ids = group;
        select(ids);
        if (ids.includes(id)) gesture.current = {kind: 'move', before, start: point, ids, pointer: event.pointerId, changed: false};
      } else {const base = additive ? selectedRef.current : []; select(base); setMarquee({...point, w: 0, h: 0}); gesture.current = {kind: 'marquee', before, start: point, ids: [], pointer: event.pointerId, changed: false, additive: base};}
      return;
    }
    if (tool === 'text') {const value = prompt('添加画布文字'); if (value) {const element = textElement(value, point); update({...before, elements: [...before.elements, element]}); select([element.id]);} return;}
    if (tool === 'image') {imageInput.current?.click(); return;}
    const element: CanvasElement = {id: crypto.randomUUID(), type: tool, ...point, w: 1, h: 1, color, ...(tool === 'pencil' ? {points: [point]} : {})};
    gesture.current = {kind: 'draw', before, start: point, ids: [element.id], pointer: event.pointerId, changed: true, added: element.id};
    update({...before, elements: [...before.elements, element]}, false); select([element.id]);
  }
  function move(event: ReactPointerEvent<SVGSVGElement>) {
    const current = gesture.current, record = active.current; if (!current || !record || current.pointer !== event.pointerId) return;
    const point = worldPoint(event), dx = point.x - current.start.x, dy = point.y - current.start.y;
    if (current.kind === 'marquee') {const box = {x: Math.min(point.x, current.start.x), y: Math.min(point.y, current.start.y), w: Math.abs(dx), h: Math.abs(dy)}; setMarquee(box); select(expandGroupSelection(record.document.elements, [...(current.additive || []), ...record.document.elements.filter(e => intersects(box, elementBounds(e))).map(e => e.id)])); return;}
    current.changed = true;
    if (current.kind === 'pan') {update({...current.before, viewport: {...current.before.viewport, x: current.before.viewport.x + event.clientX - current.start.x, y: current.before.viewport.y + event.clientY - current.start.y}}, false); return;}
    if (current.kind === 'move') {update({...current.before, elements: translateElements(current.before.elements, current.ids, dx, dy)}, false); return;}
    if (current.kind === 'resize') {
      const old = current.bounds!, handle = current.handle!, next = {...old};
      if (handle.includes('e')) next.w = Math.max(1, old.w + dx);
      if (handle.includes('s')) next.h = Math.max(1, old.h + dy);
      if (handle.includes('w')) {next.w = Math.max(1, old.w - dx); next.x = old.x + old.w - next.w;}
      if (handle.includes('n')) {next.h = Math.max(1, old.h - dy); next.y = old.y + old.h - next.h;}
      if (event.shiftKey) {const factor = Math.max(next.w / old.w, next.h / old.h); next.w = old.w * factor; next.h = old.h * factor; if (handle.includes('w')) next.x = old.x + old.w - next.w; if (handle.includes('n')) next.y = old.y + old.h - next.h;}
      update({...current.before, elements: resizeElements(current.before.elements, current.ids, old, next)}, false); return;
    }
    const elements = record.document.elements.map(e => {
      if (e.id !== current.added) return e;
      if (e.type === 'pencil') {const last = e.points!.at(-1)!; if (Math.hypot(point.x - last.x, point.y - last.y) < .25) return e; const next = {...e, points: [...e.points!, point]}; return {...next, ...elementBounds(next)};}
      return {...e, x: Math.min(point.x, current.start.x), y: Math.min(point.y, current.start.y), w: Math.max(1, Math.abs(dx)), h: Math.max(1, Math.abs(dy))};
    });
    update({...record.document, elements}, false);
  }
  function undo(redo = false) {
    finishGesture(); const record = active.current; if (!record) return;
    const from = redo ? record.redo : record.undo, to = redo ? record.undo : record.redo;
    const next = from.pop(); if (!next) return; to.push(record.document); update(next, false); select(selectedRef.current.filter(id => next.elements.some(e => e.id === id)));
  }
  function removeSelected() {const record = active.current; if (!record || !selectedRef.current.length) return; finishGesture(); const ids = new Set(selectedRef.current); update(pruneGroups({...record.document, elements: record.document.elements.filter(e => !ids.has(e.id))})); select([]);}
  function duplicate() {
    const record = active.current; if (!record || !selectedRef.current.length) return;
    finishGesture(); const source = record.document, ids = new Set(expandGroupSelection(source.elements, selectedRef.current));
    const original = source.elements.filter(element => ids.has(element.id)), additions = cloneSelection(source.elements, [...ids]);
    update(copyGroupInfo(source, original, additions, {...source, elements: [...source.elements, ...additions]})); select(additions.map(e => e.id));
  }
  function group(ungroup = false) {
    const record = active.current; if (!record || !selectedRef.current.length || (!ungroup && selectedRef.current.length < 2)) return;
    finishGesture(); const ids = new Set(expandGroupSelection(record.document.elements, selectedRef.current));
    const members = record.document.elements.filter(element => ids.has(element.id));
    if (!ungroup && members[0]?.groupId && members.every(element => element.groupId === members[0].groupId)) return;
    const groupId = ungroup ? undefined : crypto.randomUUID();
    let next = pruneGroups({...record.document, elements: record.document.elements.map(e => ids.has(e.id) ? {...e, groupId} : e)});
    if (groupId) next = withGroupInfo(next, groupId, {name: '组合 ' + (new Set(record.document.elements.map(element => element.groupId).filter(Boolean)).size + 1), fontSize: 24});
    update(next); select([...ids]);
  }
  function patchGroup(id: string, patch: {name?: string; fontSize?: number}) {const record = active.current; if (!record || !readyRef.current) return; finishGesture(); update(withGroupInfo(record.document, id, patch));}
  function renameGroup(id: string) {
    const record = active.current; if (!record || !readyRef.current || tool !== 'select') return;
    select(record.document.elements.filter(element => element.groupId === id).map(element => element.id));
    const value = prompt('组合名称', groupInfo(record.document, id).name); if (value !== null) patchGroup(id, {name: value});
  }
  function arrange(action: (elements: CanvasElement[], ids: string[]) => CanvasElement[]) {const record = active.current; if (record && selectedRef.current.length) update({...record.document, elements: action(record.document.elements, selectedRef.current)});}
  function patchSelected(patch: Partial<CanvasElement>, textOnly = false) {
    const record = active.current; if (!record) return; const ids = new Set(selectedRef.current);
    update({...record.document, elements: record.document.elements.map(e => {
      if (!ids.has(e.id) || (textOnly && e.type !== 'text')) return e;
      const next = {...e, ...patch};
      if (e.type === 'text' && ('text' in patch || 'fontFamily' in patch || 'fontSize' in patch || 'fontWeight' in patch || 'fontStyle' in patch)) Object.assign(next, measuredText(next.text || '', next.fontFamily || 'Microsoft YaHei UI', next.fontSize || 20, next.fontWeight || 'normal', next.fontStyle || 'normal'));
      return next;
    })});
  }
  function changeBounds(key: keyof Bounds, value: number) {
    const record = active.current, from = record && selectionBounds(record.document.elements, selectedRef.current); if (!record || !from || !Number.isFinite(value)) return;
    const to = {...from, [key]: key === 'w' || key === 'h' ? Math.max(1, value) : value};
    update({...record.document, elements: resizeElements(record.document.elements, selectedRef.current, from, to)});
  }
  async function imageFile(file: File): Promise<{href: string; width: number; height: number}> {
    if (!/^image\/(png|jpeg|gif|webp|bmp)$/i.test(file.type) || file.size > 20 * 1024 * 1024) throw new Error('请选择不超过20MB的 PNG、JPEG、GIF、WebP 或 BMP 图片');
    const href = await new Promise<string>((resolve, reject) => {const reader = new FileReader(); reader.onload = () => resolve(String(reader.result)); reader.onerror = () => reject(new Error('图片读取失败')); reader.readAsDataURL(file);});
    const image = new window.Image(); image.src = href; await image.decode(); if (!image.width || !image.height || image.width * image.height > 40_000_000) throw new Error('图片尺寸无效或超过4000万像素');
    return {href, width: image.width, height: image.height};
  }
  function addImages(files: File[], dropPoint?: Point) {
    const record = active.current; if (!record || !readyRef.current || !files.length) return; setAssetBusy(true);
    const rect = surface.current!.getBoundingClientRect(), origin = dropPoint || screenToWorld({x: rect.width / 2, y: rect.height / 2}, record.document.viewport);
    const job: Promise<void> = (async () => {
      const images = await Promise.all(files.map(imageFile));
      const additions: CanvasElement[] = images.map((image, i) => {const w = Math.min(600, image.width); return {id: crypto.randomUUID(), type: 'image', x: origin.x + i * 28, y: origin.y + i * 28, w, h: w * image.height / image.width, color, href: image.href};});
      if (active.current === record && gesture.current) finishGesture();
      const next = {...record.document, elements: [...record.document.elements, ...additions]};
      if (new TextEncoder().encode(JSON.stringify(payload(next))).length > 15 * 1024 * 1024) throw new Error('画布数据超过15MB保存上限，请缩小图片或减少素材');
      update(next, true, record);
      if (mounted.current && active.current === record) {select(additions.map(e => e.id)); setTool('select');}
    })().catch(error => errorRef.current(String(error))).finally(() => {record.assets.delete(job); if (mounted.current && active.current === record) setAssetBusy(record.assets.size > 0);});
    record.assets.add(job);
  }
  function clipboard(event: ReactClipboardEvent, cut = false) {
    if (!readyRef.current || !selectedRef.current.length || (event.target as HTMLElement).closest('input,textarea,select')) return;
    const items = active.current!.document.elements.filter(e => selectedRef.current.includes(e.id));
    const groups = new Set(items.map(element => element.groupId).filter(Boolean));
    event.preventDefault(); event.clipboardData.setData('application/x-architecture-canvas', JSON.stringify({schema_version: 1, elements: items, groups: groupEntries(active.current!.document).filter(entry => {const id = metadataGroupId(entry); return !!id && groups.has(id);})})); event.clipboardData.setData('text/plain', '源智画布：' + items.length + '个元素'); if (cut) removeSelected();
  }
  function paste(event: ReactClipboardEvent) {
    if ((event.target as HTMLElement).closest('input,textarea,select') || !readyRef.current) return;
    const files = Array.from(event.clipboardData.files).filter(f => f.type.startsWith('image/'));
    if (files.length) {event.preventDefault(); event.stopPropagation(); addImages(files); return;}
    const value = event.clipboardData.getData('application/x-architecture-canvas'); if (!value || !active.current) return;
    event.preventDefault(); event.stopPropagation(); try {
      const decoded = JSON.parse(value), original = normalizeElements(Array.isArray(decoded) ? decoded : decoded.elements), additions = cloneSelection(original, original.map(e => e.id));
      const source = normalizeDocument({elements: original, viewport: defaultViewport, meta: {groups: Array.isArray(decoded.groups) ? decoded.groups : []}});
      finishGesture(); update(copyGroupInfo(source, original, additions, {...active.current.document, elements: [...active.current.document.elements, ...additions]})); select(additions.map(e => e.id));
    } catch (error) {errorRef.current('画布粘贴失败：' + String(error));}
  }
  async function save() {
    finishGesture(); const record = active.current; if (!record) return; setSaveStatus('保存中…');
    try {await flush(record); renderRecord(record); toastRef.current?.('画布已保存');} catch (error) {if (active.current === record) setSaveStatus('保存失败'); errorRef.current(String(error));}
  }
  async function downloadZip(images = false) {
    const record = active.current; if (!record) return;
    try {
      finishGesture(); await flush(record);
      const response = await fetch(`/api/canvas/${encodeURIComponent(record.id)}/${images ? 'images.zip' : 'export.zip'}`, {headers: {'X-Workbench-Token': await getToken()}});
      if (!response.ok) {const error = await response.json().catch(() => ({})); throw new Error(error.detail || '画布导出失败');}
      downloadBlob(await response.blob(), record.document.name + (images ? '-图片.zip' : '.zip')); toastRef.current?.(images ? '图片压缩包已导出' : '画布项目已导出');
    } catch (error) {errorRef.current(String(error));}
  }
  function downloadBlob(blob: Blob, name: string) {const url = URL.createObjectURL(blob), link = window.document.createElement('a'); link.href = url; link.download = name.replace(/[<>:"/\\|?*]/g, '_'); link.click(); window.setTimeout(() => URL.revokeObjectURL(url), 2000);}
  async function exportSvg() {
    const record = active.current; if (!record || !svg.current) return;
    const snapshot = record.document;
    const content = selectionBounds(snapshot.elements, snapshot.elements.map(e => e.id)) || {x: 0, y: 0, w: 800, h: 600};
    const titleBoxes = groupBoxes.map(({groupId, box}) => {const info = groupInfo(snapshot, groupId), metrics = measuredText(info.name.trim() || '组合', 'Microsoft YaHei UI', info.fontSize, 'normal', 'normal'); return {x: box.x, y: titleBaseline(box, info.fontSize) - info.fontSize, w: metrics.w, h: info.fontSize * 1.4};});
    const x = Math.min(content.x, ...titleBoxes.map(box => box.x)), y = Math.min(content.y, ...titleBoxes.map(box => box.y));
    const all = {x, y, w: Math.max(content.x + content.w, ...titleBoxes.map(box => box.x + box.w)) - x, h: Math.max(content.y + content.h, ...titleBoxes.map(box => box.y + box.h)) - y};
    const copy = svg.current.cloneNode(true) as SVGSVGElement; copy.setAttribute('xmlns', 'http://www.w3.org/2000/svg'); copy.setAttribute('viewBox', `${all.x - 20} ${all.y - 20} ${all.w + 40} ${all.h + 40}`); copy.setAttribute('width', String(all.w + 40)); copy.setAttribute('height', String(all.h + 40));
    copy.querySelector('[data-canvas-content]')?.removeAttribute('transform'); copy.querySelectorAll('[data-editor-decoration]').forEach(e => e.remove());
    const titleFill = getComputedStyle(svg.current).getPropertyValue('--muted').trim() || '#667b8e';
    copy.querySelectorAll('[data-group-title]').forEach(title => title.setAttribute('fill', titleFill));
    try {
      await Promise.all([...copy.querySelectorAll('image')].map(async image => {
        const id = image.parentElement?.getAttribute('data-element'), source = snapshot.elements.find(e => e.id === id)?.href;
        if (!source) return;
        if (!source.startsWith('/api/')) {image.setAttribute('href', source); return;}
        const response = await fetch(source, {headers: {'X-Workbench-Token': await getToken()}}); if (!response.ok) throw new Error('导出图片读取失败');
        const blob = await response.blob(); const embedded = await new Promise<string>((resolve, reject) => {const reader = new FileReader(); reader.onload = () => resolve(String(reader.result)); reader.onerror = reject; reader.readAsDataURL(blob);});
        image.setAttribute('href', embedded);
      }));
      downloadBlob(new Blob([new XMLSerializer().serializeToString(copy)], {type: 'image/svg+xml'}), snapshot.name + '.svg');
    } catch (error) {errorRef.current('SVG导出失败：' + String(error));}
  }
  async function exportDeliverable() {
    const record=active.current;if(!record||!readyRef.current||assetBusy||!onDeliverable)return;
    const ticket=epoch.current;finishGesture();
    try {while(record.assets.size)await Promise.all([...record.assets]);await enqueueSave(record);const draft=await api(`/canvas/${encodeURIComponent(record.id)}/deliverable-draft`,{selected_ids:selectedRef.current});if(mounted.current&&epoch.current===ticket)onDeliverable(draft)}catch(error){errorRef.current(String(error))}
  }
  async function openProjects() {setProjectLoading(true); try {setProjects((await api('/canvas-projects')).items || []); setProjectList(true);} catch (error) {errorRef.current(String(error));} finally {setProjectLoading(false);}}
  async function newProject() {const name = prompt('新画布项目名称', '未命名画布'); if (!name?.trim()) return; await changeProject(() => api('/canvas-projects', {name: name.trim(), data: {elements: [], viewport: defaultViewport, meta: {name: name.trim(), schema_version: 1}}}));}
  async function importProject(file?: File) {if (!file) return; if (!/\.(zip|xzpack)$/i.test(file.name)) {errorRef.current('请选择 ZIP 或 XZPACK 画布项目'); return;} const data = new FormData(); data.append('file', file); await changeProject(() => api('/canvas-projects/import', data));}
  function zoomControl(next: number) {const record = active.current; if (!record || !readyRef.current) return; const rect = surface.current!.getBoundingClientRect(); update({...record.document, viewport: zoomAt(record.document.viewport, {x: rect.width / 2, y: rect.height / 2}, next)});}

  const applySharedSnapshot = useCallback((state: CollaborationStatus) => {
    const record = active.current;
    if (!record || !readyRef.current || networkBusy.current || state.canvas_id !== record.id || state.role === 'none') return;
    const binding = bindingFor(record.id);
    if (!binding || binding.peerId !== state.peer_id || binding.role !== state.role) return;
    const confirmed = acceptLateAcknowledgement(record.id, state);
    if (confirmed !== null) {record.savedRevision = Math.max(record.savedRevision, Math.min(record.revision, confirmed)); void backupRecord(record).catch(error => errorRef.current(String(error))); renderRecord(record);}
    // A status receipt never advances a dirty document's CAS baseline.
    if (record.revision > record.savedRevision || gesture.current || record.assets.size || binding.publishing || state.pending || !state.document || state.revision < binding.baseRevision) return;
    if (state.revision === binding.baseRevision && state.hash === binding.state.hash) return;
    const next = normalizeDocument({...mergeSharedDocument(record.document, state.document), id: record.id});
    if (JSON.stringify(payload(next)) !== JSON.stringify(payload(record.document))) {record.undo.push(record.document); record.redo = []; record.document = next; record.revision++; record.savedRevision = record.revision; select(selectedRef.current.filter(id => next.elements.some(element => element.id === id)));}
    acceptCollaborationVersion(record.id, state); renderRecord(record); void backupRecord(record).catch(error => errorRef.current(String(error)));
  }, [backupRecord, renderRecord, select]);

  async function createLocalCopy(record: RecordState, suffix: string) {
    while (record.assets.size) await Promise.all([...record.assets]);
    const snapshot = record.document, name = (snapshot.name || '画布').slice(0, 150) + suffix;
    return api('/canvas-projects', {name, data: {...payload(snapshot), name, meta: {...snapshot.meta, name}}});
  }
  async function networkAction(action: CollaborationActions, body: Row = {}) {
    if (networkBusy.current) return;
    const record = active.current; if (!record) throw new Error('请先打开画布');
    const ticket = ++epoch.current; networkBusy.current = true; finishGesture(); readyRef.current = false; setReady(false); setTransitioning(true);
    try {
      if (action === 'local-copy') {
        await backupRecord(record); const copy = await createLocalCopy(record, '（本地副本）'); if (epoch.current === ticket && mounted.current) install(copy, copy.id); return;
      }
      if (action === 'accept-remote') {
        // Preserve the complete local document before an explicit remote replacement.
        await backupRecord(record); await createLocalCopy(record, '（同步前副本）');
        const state = await collaborationSnapshot();
        if (state.canvas_id !== record.id || !state.document || state.pending || !state.connected) throw new Error('共享版本当前不可采用；本机副本已保留');
        const next = normalizeDocument({...mergeSharedDocument(record.document, state.document), id: record.id});
        record.undo.push(record.document); record.redo = []; record.document = next; record.revision++; record.savedRevision = record.revision;
        const binding = bindCollaboration(record.id, state); binding.pendingId = null; binding.pendingLocalRevision = null; acceptCollaborationVersion(record.id, state); select([]); renderRecord(record); await backupRecord(record); return;
      }
      if (action === 'host' || action === 'join') await flush(record);
      else {await backupRecord(record); await record.tail.catch(() => {});}
      const result = await collaborationAction(action, body);
      if (action === 'stop') {
        clearCollaborationBindings(); record.revision++; record.recoveredDraft = true; await enqueueSave(record); renderRecord(record); return;
      }
      if (action === 'join') {
        const id = result.canvas_id; if (!id) throw new Error('加入未返回本机副本');
        const state = await collaborationSnapshot(), local = await api(`/canvas/${encodeURIComponent(id)}`);
        if (epoch.current !== ticket || !mounted.current) return;
        bindCollaboration(id, state); install(state.document ? {...mergeSharedDocument(local, state.document), id} : local, id); await backupRecord(active.current!); return;
      }
      if (action === 'host') {
        const state = await collaborationSnapshot(); if (state.canvas_id !== record.id || state.role !== 'host') throw new Error('主机分享未就绪');
        bindCollaboration(record.id, state);
        if (state.document) {record.document = normalizeDocument({...mergeSharedDocument(record.document, state.document), id: record.id}); record.revision++; record.savedRevision = record.revision;}
        renderRecord(record); await backupRecord(record);
      }
      // Reconnect changes connection status, not a dirty UI's accepted version.
      // The next publish keeps the old base and gets a genuine conflict if needed.
    } finally {
      networkBusy.current = false;
      if (mounted.current && epoch.current === ticket) {readyRef.current = !!active.current; setReady(readyRef.current); setTransitioning(false);}
    }
  }
  const openCollaborationCanvas = useCallback(async (id: string, retainDraft = false) => {await changeProject(() => api(`/canvas/${encodeURIComponent(id)}`), id, retainDraft);}, [changeProject]);

  void urlVersion;
  return <div className="canvas-studio canvas-workbench">
    <div className="canvas-projectbar"><label className="canvas-projectname"><Pencil size={14}/><input aria-label="画布项目名称" maxLength={200} disabled={!ready} value={document.name} onChange={event => {const record = active.current; if (record) update({...record.document, name: event.target.value, meta: {...record.document.meta, name: event.target.value}});}}/></label><button disabled={transitioning || assetBusy} onClick={() => void newProject()}><Plus size={15}/>新建</button><button disabled={transitioning || assetBusy || projectLoading} onClick={() => void openProjects()}><FolderOpen size={15}/>打开项目</button><button disabled={!ready || transitioning} onClick={() => void save()}><Save size={15}/>保存</button><span className="canvas-save-state" role="status">{transitioning ? '正在切换项目…' : assetBusy ? '正在导入图片…' : saveStatus}</span><div className="titlebar-space"/><button disabled={!ready || transitioning} onClick={() => void downloadZip()}><Download size={15}/>导出项目</button><button disabled={!ready || transitioning || !document.elements.some(e => e.type === 'image')} onClick={() => void downloadZip(true)}>导出全部图片</button><button disabled={transitioning || assetBusy} onClick={() => projectInput.current?.click()}><Upload size={15}/>导入项目</button></div>
    {onDeliverable&&<div className="button-row" style={{padding:'8px 16px',borderBottom:'1px solid var(--border)'}}><button className="secondary-button" disabled={!ready||assetBusy} onClick={()=>void exportDeliverable()}><Download size={15}/>{selected.length?'所选内容生成汇报/PPT':'画布生成汇报/PPT'}</button><span className="small muted">保留文字、图片与资料引用，可继续编辑章节。</span></div>}
    <CanvasCollaboration canvasId={activeId} ready={ready && !assetBusy} bound={!!bindingFor(activeId)} onAction={networkAction} onSnapshot={applySharedSnapshot} onOpenCanvas={openCollaborationCanvas}/>
    <div className="canvas-toolbar">{tools.map(([id, label, Icon]) => <button key={id} title={label} aria-label={label} aria-pressed={tool === id} className={tool === id ? 'selected' : ''} disabled={!ready} onClick={() => {setTool(id); if (id === 'image') imageInput.current?.click();}}><Icon size={18}/></button>)}<span className="separator"/><input aria-label="绘制颜色" type="color" value={selectedElements[0]?.color || color} disabled={!ready} onChange={event => {setColor(event.target.value); if (selected.length) patchSelected({color: event.target.value});}}/><button title="撤销" aria-label="撤销" disabled={!ready || !active.current?.undo.length} onClick={() => undo()}><Undo2 size={17}/></button><button title="重做" aria-label="重做" disabled={!ready || !active.current?.redo.length} onClick={() => undo(true)}><Redo2 size={17}/></button><button title="复制选中元素" aria-label="复制选中元素" disabled={!ready || !selected.length} onClick={duplicate}><Copy size={17}/></button><button title="删除选中元素" aria-label="删除选中元素" disabled={!ready || !selected.length} onClick={removeSelected}><Trash2 size={17}/></button><button title="组合选中元素" aria-label="组合选中元素" disabled={!ready || selected.length < 2} onClick={() => group()}><Group size={17}/></button><button title="取消组合" aria-label="取消组合" disabled={!ready || !selectedElements.some(e => e.groupId)} onClick={() => group(true)}><Ungroup size={17}/></button><span className="separator"/><select aria-label="对齐方式" defaultValue="" disabled={!ready || unitCount < 2} onChange={event => {const value = event.target.value as Alignment; if (value) arrange((elements, ids) => alignElements(elements, ids, value)); event.target.value = '';}}><option value="">对齐</option><option value="left">左对齐</option><option value="center">水平居中</option><option value="right">右对齐</option><option value="top">顶端对齐</option><option value="middle">垂直居中</option><option value="bottom">底端对齐</option></select><button title="水平等距" disabled={!ready || unitCount < 3} onClick={() => arrange((elements, ids) => distributeElements(elements, ids, 'x'))}><AlignLeft size={15}/>水平等距</button><button title="垂直等距" disabled={!ready || unitCount < 3} onClick={() => arrange((elements, ids) => distributeElements(elements, ids, 'y'))}>垂直等距</button><div className="titlebar-space"/><button title="导出 SVG" disabled={!ready} onClick={() => void exportSvg()}><Download size={16}/>SVG</button></div>
    <div className="canvas-formatbar"><label>字体<select aria-label="文字字体" value={String(selectedText[0]?.fontFamily || fontFamily)} disabled={!ready} onChange={event => {setFontFamily(event.target.value); if (selectedText.length) patchSelected({fontFamily: event.target.value}, true);}}>{['Microsoft YaHei UI', 'SimSun', 'SimHei', 'Arial', 'Georgia', 'Consolas'].map(font => <option key={font}>{font}</option>)}</select></label><label>字号<input aria-label="文字字号" type="number" min="8" max="240" value={selectedText[0]?.fontSize || fontSize} disabled={!ready} onChange={event => {const value = Math.max(8, Math.min(240, Number(event.target.value) || 20)); setFontSize(value); if (selectedText.length) patchSelected({fontSize: value}, true);}}/></label><button title="粗体" aria-label="粗体" className={(selectedText.length ? selectedText[0]?.fontWeight === 'bold' || selectedText[0]?.fontWeight === 700 : bold) ? 'selected' : ''} disabled={!ready} onClick={() => {const next = selectedText.length ? !(selectedText[0]?.fontWeight === 'bold' || selectedText[0]?.fontWeight === 700) : !bold; setBold(next); if (selectedText.length) patchSelected({fontWeight: next ? 'bold' : 'normal'}, true);}}><Bold size={15}/></button><button title="斜体" aria-label="斜体" className={(selectedText.length ? selectedText[0]?.fontStyle === 'italic' : italic) ? 'selected' : ''} disabled={!ready} onClick={() => {const next = selectedText.length ? selectedText[0]?.fontStyle !== 'italic' : !italic; setItalic(next); if (selectedText.length) patchSelected({fontStyle: next ? 'italic' : 'normal'}, true);}}><Italic size={15}/></button><span className="separator"/><label>列数<input aria-label="网格列数" type="number" min="1" max="50" value={columns} onChange={event => setColumns(Math.max(1, Math.min(50, Number(event.target.value) || 1)))}/></label><label>间距<input aria-label="网格间距" type="number" min="0" max="1000" value={gap} onChange={event => setGap(Math.max(0, Math.min(1000, Number(event.target.value) || 0)))}/></label><button disabled={!ready || !selected.length} onClick={() => arrange((elements, ids) => arrangeGrid(elements, ids, columns, gap))}><Grid2X2 size={15}/>自动网格排版</button><div className="titlebar-space"/><span>{selected.length ? `选中${selected.length}个元素 / ${unitCount}个单元` : 'Ctrl/Shift 多选 · 空格拖动平移'}</span></div>
    <div ref={surface} className={'canvas-surface tool-' + tool} data-ready={ready} tabIndex={0} aria-label="设计画布" onCopy={event => clipboard(event)} onCut={event => clipboard(event, true)} onPaste={paste} onDragOver={event => event.preventDefault()} onDrop={event => {event.preventDefault(); event.stopPropagation(); if (!readyRef.current) return; const rect = svg.current!.getBoundingClientRect(); addImages(Array.from(event.dataTransfer.files).filter(file => file.type.startsWith('image/')), screenToWorld({x: event.clientX - rect.left, y: event.clientY - rect.top}, active.current!.document.viewport));}} onBlur={() => {space.current = false;}} onKeyUp={event => {if (event.key === ' ') space.current = false;}} onKeyDown={event => {
      if ((event.target as HTMLElement).closest('input,textarea,select') || !readyRef.current || !active.current) return;
      const command = event.ctrlKey || event.metaKey, key = event.key.toLowerCase();
      if (event.key === ' ') {event.preventDefault(); space.current = true;}
      if (event.key === 'Escape') {event.preventDefault(); finishGesture(true); select([]);}
      if (event.key === 'Delete' || event.key === 'Backspace') {event.preventDefault(); removeSelected();}
      if (command && key === 'a') {event.preventDefault(); select(active.current.document.elements.map(e => e.id));}
      if (command && key === 'z') {event.preventDefault(); undo(event.shiftKey);}
      if (command && key === 'y') {event.preventDefault(); undo(true);}
      if (command && key === 'd') {event.preventDefault(); duplicate();}
      if (command && key === 'g') {event.preventDefault(); group(event.shiftKey);}
      if (command && key === 's') {event.preventDefault(); void save();}
      if (event.key.startsWith('Arrow') && selectedRef.current.length) {event.preventDefault(); const step = event.shiftKey ? 10 : 1; arrange((elements, ids) => translateElements(elements, ids, event.key === 'ArrowLeft' ? -step : event.key === 'ArrowRight' ? step : 0, event.key === 'ArrowUp' ? -step : event.key === 'ArrowDown' ? step : 0));}
    }}>
      <svg ref={svg} width="100%" height="100%" onPointerDown={down} onPointerMove={move} onPointerUp={() => finishGesture()} onPointerCancel={() => finishGesture(true)} onLostPointerCapture={() => {if (gesture.current) finishGesture();}}>
        <defs><pattern id={patternId} width="24" height="24" patternUnits="userSpaceOnUse"><circle cx="1" cy="1" r=".7" fill="var(--border)"/></pattern></defs><rect width="100%" height="100%" fill={`url(#${patternId})`} data-editor-decoration="background"/>
        <g data-canvas-content transform={`translate(${document.viewport.x},${document.viewport.y}) scale(${document.viewport.zoom},${document.viewport.zoomY || document.viewport.zoom})`}>
          {tool === 'select' && groupBoxes.map(({groupId, box}) => <rect key={'hit-' + groupId} data-group-hit={groupId} data-editor-decoration="group-hit" x={box.x} y={box.y} width={box.w} height={box.h} fill="transparent" pointerEvents="all" style={{cursor: 'move'}}/>)}
          {groupBoxes.map(({groupId, box}) => {const info = groupInfo(document, groupId); return <text key={'title-' + groupId} data-group-title={groupId} data-group-hit={groupId} className="canvas-group-title" x={box.x} y={titleBaseline(box, info.fontSize)} fontSize={info.fontSize} fontFamily="Microsoft YaHei UI, sans-serif" fill="var(--muted)" pointerEvents={tool === 'select' ? 'visiblePainted' : 'none'} onDoubleClick={() => renameGroup(groupId)}>{info.name.trim() || '组合'}</text>;})}
          {document.elements.map(element => {
            const box = elementBounds(element), size = element.fontSize || 20, family = element.fontFamily || 'Microsoft YaHei UI', metrics = element.type === 'text' ? measuredText(element.text || '', family, size, element.fontWeight || 'normal', element.fontStyle || 'normal') : null;
            return <g key={element.id} data-element={element.id} style={{cursor: tool === 'select' ? 'move' : undefined}} onDoubleClick={() => {if (element.type === 'text' && readyRef.current) {select(expandGroupSelection(document.elements, [element.id])); const value = prompt('修改文字', element.text || ''); if (value !== null) {const record = active.current!; const updated = {...element, text: value, ...measuredText(value, family, size, element.fontWeight || 'normal', element.fontStyle || 'normal')}; update({...record.document, elements: record.document.elements.map(e => e.id === element.id ? updated : e)});}}}}>
              {element.type === 'rect' && <rect x={element.x} y={element.y} width={element.w} height={element.h} fill="transparent" stroke={element.color} strokeWidth="2" pointerEvents="all"/>}
              {element.type === 'ellipse' && <ellipse cx={element.x + element.w / 2} cy={element.y + element.h / 2} rx={element.w / 2} ry={element.h / 2} fill="transparent" stroke={element.color} strokeWidth="2" pointerEvents="all"/>}
              {element.type === 'text' && <g transform={`translate(${element.x},${element.y}) scale(${element.w / metrics!.w},${element.h / metrics!.h})`}><text x="0" y={size} fill={element.color} fontSize={size} fontFamily={family + ', sans-serif'} fontWeight={element.fontWeight || 'normal'} fontStyle={element.fontStyle || 'normal'}>{(element.text || '').split('\n').map((line, index) => <tspan key={index} x="0" dy={index ? size * 1.3 : 0}>{line || ' '}</tspan>)}</text></g>}
              {element.type === 'image' && <image x={element.x} y={element.y} width={element.w} height={element.h} preserveAspectRatio="none" href={element.href?.startsWith('/api/') ? imageUrls.current.get(element.href) : element.href}/>}
              {element.type === 'pencil' && <polyline points={element.points?.map(p => `${p.x},${p.y}`).join(' ')} fill="none" stroke={element.color} strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"/>}
              {element.type === 'pencil' && tool === 'select' && <polyline data-editor-decoration="path-hit" points={element.points?.map(p => `${p.x},${p.y}`).join(' ')} fill="none" stroke="transparent" strokeWidth="12" vectorEffect="non-scaling-stroke" strokeLinecap="round" strokeLinejoin="round" pointerEvents="stroke"/>}
              {selected.includes(element.id) && <rect data-editor-decoration="selection" className="canvas-item-selection" x={box.x} y={box.y} width={box.w} height={box.h} fill="none" stroke="#577b9c" strokeWidth="1" vectorEffect="non-scaling-stroke" pointerEvents="none"/>}
            </g>;
          })}
          {bounds && tool === 'select' && <g data-editor-decoration="handles"><rect className="selection-box" x={bounds.x} y={bounds.y} width={bounds.w} height={bounds.h} fill="none" stroke="#446b90" vectorEffect="non-scaling-stroke" strokeDasharray="5 3" pointerEvents="none"/>{[['nw', 0, 0], ['n', .5, 0], ['ne', 1, 0], ['e', 1, .5], ['se', 1, 1], ['s', .5, 1], ['sw', 0, 1], ['w', 0, .5]].map(([handle, x, y]) => <rect key={handle} data-resize={handle} x={bounds.x + Number(x) * bounds.w - 4 / document.viewport.zoom} y={bounds.y + Number(y) * bounds.h - 4 / (document.viewport.zoomY || document.viewport.zoom)} width={8 / document.viewport.zoom} height={8 / (document.viewport.zoomY || document.viewport.zoom)} fill="white" stroke="#446b90" vectorEffect="non-scaling-stroke" style={{cursor: String(handle) + '-resize'}}/>)}</g>}
          {marquee && <rect data-editor-decoration="marquee" x={marquee.x} y={marquee.y} width={marquee.w} height={marquee.h} fill="#7799bc22" stroke="#577b9c" vectorEffect="non-scaling-stroke" pointerEvents="none"/>}
        </g>
      </svg>
      {bounds && <aside className="canvas-selection-panel" onPointerDown={event => event.stopPropagation()}><strong>{selectedGroup ? '组合属性' : selected.length === 1 ? '元素属性' : '多选范围'}</strong><div>{(['x', 'y', 'w', 'h'] as const).map(key => <label key={key}>{({x: 'X', y: 'Y', w: '宽', h: '高'})[key]}<input aria-label={'选择范围' + key} type="number" min={key === 'w' || key === 'h' ? 1 : undefined} value={Math.round(bounds[key] * 100) / 100} disabled={!ready} onChange={event => {if (event.target.value !== '') changeBounds(key, Number(event.target.value));}}/></label>)}</div>{selectedGroup && selectedGroupId && <div className="canvas-group-fields"><label>组合名称<input aria-label="组合名称" maxLength={200} disabled={!ready} value={selectedGroup.name} onChange={event => patchGroup(selectedGroupId, {name: event.target.value})}/></label><label>组合字号<input aria-label="组合字号" type="number" min="8" max="120" step="2" disabled={!ready} value={selectedGroup.fontSize} onChange={event => {if (event.target.value !== '') patchGroup(selectedGroupId, {fontSize: Number(event.target.value)});}}/></label></div>}{singleText && <label>文字<textarea aria-label="画布文字" value={singleText.text || ''} onChange={event => patchSelected({text: event.target.value}, true)}/></label>}<button onClick={() => select([])}>取消选择</button></aside>}
      {!document.elements.length && ready && <div className="canvas-guide"><div className="canvas-guide-icon"><Pencil size={26} strokeWidth={1}/></div><p>你的设计，可以从这里展开</p><span>拖入或粘贴图片 · 框选、组合与自由排版</span></div>}
      {!ready && <div className="canvas-loading">{transitioning ? '正在读取与保存画布…' : '画布未加载'}</div>}
    </div>
    <div className="canvas-status"><span>{document.elements.length} 个元素</span><span>{selected.length} 个选中</span><div className="titlebar-space"/><button aria-label="缩小画布" disabled={!ready} onClick={() => zoomControl(document.viewport.zoom / 1.2)}><Minus size={14}/></button><span>{Math.round(document.viewport.zoom * 100)}%{document.viewport.zoomY !== undefined && Math.abs(document.viewport.zoomY - document.viewport.zoom) > .00001 ? ` × ${Math.round(document.viewport.zoomY * 100)}%` : ''}</span><button aria-label="放大画布" disabled={!ready} onClick={() => zoomControl(document.viewport.zoom * 1.2)}><Plus size={14}/></button><button title="恢复视图" disabled={!ready} onClick={() => active.current && update({...active.current.document, viewport: {...active.current.document.viewport, ...defaultViewport, zoomY: 1}})}><Maximize2 size={14}/></button></div>
    <input ref={imageInput} type="file" accept="image/png,image/jpeg,image/gif,image/webp,image/bmp" hidden multiple onChange={event => {addImages(Array.from(event.target.files || [])); event.target.value = '';}}/>
    <input ref={projectInput} type="file" accept=".zip,.xzpack" hidden onChange={event => {const file = event.target.files?.[0]; event.target.value = ''; void importProject(file);}}/>
    {projectList && <div className="canvas-project-mask" onClick={() => setProjectList(false)}><section className="canvas-project-dialog" role="dialog" aria-modal="true" aria-label="打开画布项目" onClick={event => event.stopPropagation()}><header><h2>打开画布项目</h2><button aria-label="关闭项目列表" onClick={() => setProjectList(false)}><X size={18}/></button></header><p>切换前保存当前画布；导入项目会创建独立副本。</p>{projects.length ? <div className="canvas-project-items">{projects.map(project => <button key={project.id} disabled={transitioning} onClick={() => void changeProject(() => api(`/canvas/${encodeURIComponent(project.id)}`), project.id)}><FolderOpen size={18}/><span><strong>{project.name || '未命名画布'}</strong><small>{project.element_count ?? 0} 个元素 · {(project.updated_at || '').slice(0, 16).replace('T', ' ')}</small></span>{project.id === activeId && <em>当前项目</em>}</button>)}</div> : <p>暂时没有保存的项目。可新建或导入ZIP/XZPACK。</p>}</section></div>}
  </div>;
}
