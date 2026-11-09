// Run after tsc emits canvasGeometry.ts to .local/canvas-geometry-tests.
const assert = require('node:assert/strict');
const path = require('node:path');
const geometry = require(path.resolve(__dirname, '../../.local/canvas-geometry-tests/canvasGeometry.js'));
const element = (id, x, y, w, h, extra = {}) => ({id, type: 'rect', x, y, w, h, color: '#111111', ...extra});
const near = (actual, expected) => assert.ok(Math.abs(actual - expected) < 1e-8, `${actual} !== ${expected}`);
const v = {x: 20, y: 10, zoom: 2, zoomY: 1};
assert.deepEqual(geometry.screenToWorld({x: 160, y: 90}, v), {x: 70, y: 80});
const zoomed = geometry.zoomAt(v, {x: 160, y: 90}, 4);
assert.deepEqual(zoomed, {x: -120, y: -70, zoom: 4, zoomY: 2});
assert.deepEqual(geometry.screenToWorld({x: 160, y: 90}, zoomed), {x: 70, y: 80});
const extreme = geometry.zoomAt({x: 0, y: 0, zoom: 100, zoomY: .01}, {x: 20, y: 20}, 20);
assert.equal(extreme.zoom, 100); assert.equal(extreme.zoomY, .01); // keep both axes in the ZIP contract's range
assert.equal(geometry.selectionBounds([], []), null);

const original = [element('a', 0, 0, 10, 10), element('t', 10, 0, 10, 10, {type: 'text', text: 'T', fontSize: 20}),
  element('p', 0, 0, 20, 10, {type: 'pencil', points: [{x: 0, y: 0}, {x: 20, y: 10}]})];
const saved = JSON.stringify(original), resized = geometry.resizeElements(original, ['a', 't', 'p'], {x: 0, y: 0, w: 20, h: 10}, {x: 5, y: 7, w: 40, h: 30});
assert.deepEqual(resized[2].points, [{x: 5, y: 7}, {x: 45, y: 37}]);
assert.deepEqual(geometry.elementBounds(resized[2]), {x: 5, y: 7, w: 40, h: 30});
assert.equal(resized[1].fontSize, 60); assert.equal(resized[1].x, 25);
assert.equal(JSON.stringify(original), saved, 'resize mutated original/undo snapshot');
const moved = geometry.translateElements(original, ['a', 'p'], 12, -8);
assert.deepEqual(moved[2].points, [{x: 12, y: -8}, {x: 32, y: 2}]); assert.equal(moved[1], original[1]);

const group = [element('a', 10, 10, 10, 10, {groupId: 'g'}), element('b', 30, 15, 10, 10, {groupId: 'g'}), element('c', 100, 50, 20, 20)];
assert.deepEqual(geometry.expandGroupSelection(group, ['a']), ['a', 'b']);
assert.equal(geometry.selectionUnitCount(group, ['a', 'c']), 2);
const aligned = geometry.alignElements(group, ['a', 'c'], 'left');
assert.equal(aligned[2].x, 10); assert.equal(aligned[1].x - aligned[0].x, 20); assert.equal(aligned[1].y - aligned[0].y, 5);
const distributed = geometry.distributeElements([element('a', 10, 0, 10, 10), element('b', 40, 0, 20, 10), element('c', 130, 0, 10, 10)], ['a', 'b', 'c'], 'x');
near(distributed[1].x, 65); near(distributed[1].x - distributed[0].x - distributed[0].w, distributed[2].x - distributed[1].x - distributed[1].w);
const grid = geometry.arrangeGrid([...group, element('d', 60, 100, 50, 80), element('e', 180, 120, 20, 15)], ['a', 'c', 'd', 'e'], 2, 12);
assert.equal(grid[1].x - grid[0].x, 20); assert.equal(grid[1].y - grid[0].y, 5);
const boxes = [['a', 'b'], ['c'], ['d'], ['e']].map(ids => geometry.selectionBounds(grid, ids));
assert.equal(boxes[1].x - boxes[0].x, 62); assert.equal(boxes[2].y - boxes[0].y, 32);
assert.ok(boxes[0].x + boxes[0].w <= boxes[1].x); assert.ok(boxes[2].x + boxes[2].w <= boxes[3].x);
const cloned = geometry.cloneSelection(group, ['a']);
assert.equal(cloned.length, 2); assert.equal(cloned[0].groupId, cloned[1].groupId); assert.notEqual(cloned[0].groupId, 'g'); assert.notEqual(cloned[0].id, 'a');
assert.equal(cloned[1].x - cloned[0].x, 20); assert.equal(cloned[0].x, 34);
const longStroke = element('long', 0, 0, 1, 1, {type: 'pencil', points: Array.from({length: 150000}, (_, i) => ({x: i, y: i % 100}))});
assert.deepEqual(geometry.elementBounds(longStroke), {x: 0, y: 0, w: 149999, h: 99});
assert.equal(JSON.stringify(original), saved, 'operations mutated original snapshot');
console.log('Canvas geometry: world coordinates, anisotropic zoom anchor, affine resize, group selection/alignment/grid, equal gaps, copy identity, undo immutability and large stroke bounds passed.');
