export type Point = {x: number; y: number};
export type Bounds = Point & {w: number; h: number};
export type Viewport = Point & {zoom: number; zoomY?: number};
export type CanvasElement = Bounds & {
  id: string; type: string; color: string; text?: string; href?: string;
  points?: Point[]; groupId?: string; fontFamily?: string; fontSize?: number;
  fontWeight?: string | number; fontStyle?: string; [key: string]: unknown;
};
const finite = (n: number, fallback = 0) => Number.isFinite(n) ? n : fallback;
const size = (n: number) => Math.max(1, finite(n, 1));

export function screenToWorld(point: Point, viewport: Viewport): Point {
  return {x: (point.x - viewport.x) / viewport.zoom, y: (point.y - viewport.y) / (viewport.zoomY || viewport.zoom)};
}
export function zoomAt(viewport: Viewport, anchor: Point, zoom: number): Viewport {
  const currentY = viewport.zoomY || viewport.zoom;
  const factor = Math.max(Math.max(.01 / viewport.zoom, .01 / currentY), Math.min(Math.min(100 / viewport.zoom, 100 / currentY), finite(zoom, 1) / viewport.zoom));
  const world = screenToWorld(anchor, viewport); zoom = viewport.zoom * factor;
  const zoomY = currentY * factor;
  return {...viewport, zoom, ...(viewport.zoomY !== undefined ? {zoomY} : {}), x: anchor.x - world.x * zoom, y: anchor.y - world.y * zoomY};
}
export function elementBounds(element: CanvasElement): Bounds {
  if (element.type === 'pencil' && element.points?.length) {
    let x = Infinity, y = Infinity, right = -Infinity, bottom = -Infinity;
    for (const p of element.points) {x = Math.min(x, p.x); y = Math.min(y, p.y); right = Math.max(right, p.x); bottom = Math.max(bottom, p.y);}
    return {x, y, w: size(right - x), h: size(bottom - y)};
  }
  return {x: finite(element.x), y: finite(element.y), w: size(element.w), h: size(element.h)};
}
export function selectionBounds(elements: CanvasElement[], ids: string[]): Bounds | null {
  const chosen = new Set(ids), boxes = elements.filter(e => chosen.has(e.id)).map(elementBounds);
  if (!boxes.length) return null;
  const x = Math.min(...boxes.map(b => b.x)), y = Math.min(...boxes.map(b => b.y));
  return {x, y, w: size(Math.max(...boxes.map(b => b.x + b.w)) - x), h: size(Math.max(...boxes.map(b => b.y + b.h)) - y)};
}
export function expandGroupSelection(elements: CanvasElement[], ids: string[]): string[] {
  const chosen = new Set(ids), groups = new Set(elements.filter(e => chosen.has(e.id) && e.groupId).map(e => e.groupId));
  return elements.filter(e => chosen.has(e.id) || (e.groupId && groups.has(e.groupId))).map(e => e.id);
}
export function translateElements(elements: CanvasElement[], ids: string[], dx: number, dy: number): CanvasElement[] {
  const chosen = new Set(ids); dx = finite(dx); dy = finite(dy);
  return elements.map(e => chosen.has(e.id) ? {...e, x: e.x + dx, y: e.y + dy, points: e.points?.map(p => ({x: p.x + dx, y: p.y + dy}))} : e);
}
export function resizeElements(elements: CanvasElement[], ids: string[], from: Bounds, to: Bounds): CanvasElement[] {
  const chosen = new Set(ids), sx = size(to.w) / size(from.w), sy = size(to.h) / size(from.h);
  return elements.map(e => {
    if (!chosen.has(e.id)) return e;
    const result = {...e, x: to.x + (e.x - from.x) * sx, y: to.y + (e.y - from.y) * sy,
      w: size(e.w * sx), h: size(e.h * sy), points: e.points?.map(p => ({x: to.x + (p.x - from.x) * sx, y: to.y + (p.y - from.y) * sy}))};
    if (e.type === 'text') result.fontSize = Math.max(1, (e.fontSize || 20) * sy);
    if (e.type === 'pencil' && result.points?.length) Object.assign(result, elementBounds(result));
    return result;
  });
}
function units(elements: CanvasElement[], ids: string[]) {
  const chosen = new Set(expandGroupSelection(elements, ids)), buckets = new Map<string, string[]>();
  for (const e of elements) if (chosen.has(e.id)) {
    const key = e.groupId ? `group:${e.groupId}` : `element:${e.id}`;
    buckets.set(key, [...(buckets.get(key) || []), e.id]);
  }
  return [...buckets.values()].map(ids => ({ids, box: selectionBounds(elements, ids)!}));
}
export function selectionUnitCount(elements: CanvasElement[], ids: string[]) {return units(elements, ids).length;}
export type Alignment = 'left' | 'center' | 'right' | 'top' | 'middle' | 'bottom';
export function alignElements(elements: CanvasElement[], ids: string[], mode: Alignment): CanvasElement[] {
  const chosen = expandGroupSelection(elements, ids), bounds = selectionBounds(elements, chosen);
  if (!bounds) return elements;
  let result = elements;
  for (const unit of units(elements, chosen)) {
    const b = unit.box;
    const dx = mode === 'left' ? bounds.x - b.x : mode === 'center' ? bounds.x + bounds.w / 2 - b.x - b.w / 2 : mode === 'right' ? bounds.x + bounds.w - b.x - b.w : 0;
    const dy = mode === 'top' ? bounds.y - b.y : mode === 'middle' ? bounds.y + bounds.h / 2 - b.y - b.h / 2 : mode === 'bottom' ? bounds.y + bounds.h - b.y - b.h : 0;
    result = translateElements(result, unit.ids, dx, dy);
  }
  return result;
}
export function distributeElements(elements: CanvasElement[], ids: string[], axis: 'x' | 'y'): CanvasElement[] {
  const list = units(elements, ids).sort((a, b) => a.box[axis] - b.box[axis]);
  if (list.length < 3) return elements;
  const dimension = axis === 'x' ? 'w' : 'h', first = list[0].box, last = list.at(-1)!.box;
  const gap = (last[axis] + last[dimension] - first[axis] - list.reduce((sum, unit) => sum + unit.box[dimension], 0)) / (list.length - 1);
  let cursor = first[axis], result = elements;
  for (const unit of list) {
    result = translateElements(result, unit.ids, axis === 'x' ? cursor - unit.box.x : 0, axis === 'y' ? cursor - unit.box.y : 0);
    cursor += unit.box[dimension] + gap;
  }
  return result;
}
export function arrangeGrid(elements: CanvasElement[], ids: string[], columns: number, gap: number): CanvasElement[] {
  const list = units(elements, ids), bounds = selectionBounds(elements, expandGroupSelection(elements, ids));
  if (!bounds) return elements;
  columns = Math.max(1, Math.min(list.length, Math.round(finite(columns, 1)))); gap = Math.max(0, finite(gap, 24));
  const widths = Array.from({length: columns}, () => 0), heights: number[] = [];
  list.forEach((unit, i) => {widths[i % columns] = Math.max(widths[i % columns], unit.box.w); heights[Math.floor(i / columns)] = Math.max(heights[Math.floor(i / columns)] || 0, unit.box.h);});
  let result = elements;
  list.forEach((unit, i) => {
    const column = i % columns, row = Math.floor(i / columns);
    const x = bounds.x + widths.slice(0, column).reduce((a, b) => a + b + gap, 0), y = bounds.y + heights.slice(0, row).reduce((a, b) => a + b + gap, 0);
    result = translateElements(result, unit.ids, x - unit.box.x, y - unit.box.y);
  });
  return result;
}
export function cloneSelection(elements: CanvasElement[], ids: string[], offset = 24): CanvasElement[] {
  const chosen = new Set(expandGroupSelection(elements, ids)), groups = new Map<string, string>();
  return elements.filter(e => chosen.has(e.id)).map(e => {
    let groupId: string | undefined;
    if (e.groupId) {if (!groups.has(e.groupId)) groups.set(e.groupId, crypto.randomUUID()); groupId = groups.get(e.groupId);}
    return {...e, id: crypto.randomUUID(), groupId, x: e.x + offset, y: e.y + offset, points: e.points?.map(p => ({x: p.x + offset, y: p.y + offset}))};
  });
}
export function intersects(a: Bounds, b: Bounds) {return a.x <= b.x + b.w && a.x + a.w >= b.x && a.y <= b.y + b.h && a.y + a.h >= b.y;}
