import * as THREE from 'three';
import {departments, type Department, type DepartmentId} from './data';
import {blinkAt, type Pose} from './character';
import {createMainActor, createResident, buildArchitectureDesk, plant, floorPatch, ANKLE, MAIN_LOOP} from './actor';
import {
  corridorTexture, directoryTexture, drawingTexture, insoleTexture, screenTexture, signTexture, siteModelTexture, woodFloorTexture,
  boxGeo, geo, sphereGeo,
} from './textures';

/* ------------------------------ geometry ------------------------------ */

const FLOOR = {x0: -11.4, x1: 11.4, z0: -6.4, z1: 6.4};
const CORRIDOR = {z0: -1.4, z1: 1.4};
const WALL_H = 1.16;
const PORTAL_H = 2.45;

const mats = {
  wall: new THREE.MeshStandardMaterial({color: 0xe4dbca, roughness: 0.95}),
  wallSide: new THREE.MeshStandardMaterial({color: 0xded5c4, roughness: 0.96}),
  trim: new THREE.MeshStandardMaterial({color: 0x9a8158, roughness: 0.62, metalness: 0.12}),
  brass: new THREE.MeshStandardMaterial({color: 0xc3a166, roughness: 0.34, metalness: 0.62}),
  graphite: new THREE.MeshStandardMaterial({color: 0x35393a, roughness: 0.55, metalness: 0.18}),
  dark: new THREE.MeshStandardMaterial({color: 0x24282a, roughness: 0.6}),
  oak: new THREE.MeshStandardMaterial({color: 0xc3a172, roughness: 0.68}),
  oakDark: new THREE.MeshStandardMaterial({color: 0x8c7150, roughness: 0.7}),
  paper: new THREE.MeshStandardMaterial({color: 0xf3eddf, roughness: 0.92, side: THREE.DoubleSide}),
  white: new THREE.MeshStandardMaterial({color: 0xe8e3d6, roughness: 0.8}),
  rugWarm: new THREE.MeshStandardMaterial({color: 0xb9ac97, roughness: 0.98}),
  rugCool: new THREE.MeshStandardMaterial({color: 0x9fa39a, roughness: 0.98}),
  steel: new THREE.MeshStandardMaterial({color: 0x9da2a0, roughness: 0.4, metalness: 0.55}),
  fabricGreen: new THREE.MeshStandardMaterial({color: 0x6a7563, roughness: 0.95}),
  fabricRust: new THREE.MeshStandardMaterial({color: 0xa2765c, roughness: 0.95}),
  concrete: new THREE.MeshStandardMaterial({color: 0xbfbab0, roughness: 0.95}),
  soil: new THREE.MeshStandardMaterial({color: 0x8b8168, roughness: 1}),
  leaf: new THREE.MeshStandardMaterial({color: 0x6c7d63, roughness: 0.92}),
  glass: new THREE.MeshStandardMaterial({color: 0xa8c0bd, roughness: 0.2, metalness: 0.1, transparent: true, opacity: 0.35}),
  screenGlow: new THREE.MeshStandardMaterial({color: 0x1a1e1d, emissive: 0x8fae9e, emissiveIntensity: 0.25, roughness: 0.35}),
};

function addBox(parent: THREE.Object3D, w: number, h: number, d: number, x: number, y: number, z: number, material: THREE.Material, shadow = true) {
  const item = new THREE.Mesh(boxGeo(w, h, d), material);
  item.position.set(x, y, z);
  item.castShadow = shadow;
  item.receiveShadow = true;
  parent.add(item);
  return item;
}

function addCylinder(parent: THREE.Object3D, rt: number, rb: number, h: number, x: number, y: number, z: number, material: THREE.Material, shadow = true, segments = 12) {
  const item = new THREE.Mesh(geo(rt, rb, h, segments), material);
  item.position.set(x, y, z);
  item.castShadow = shadow;
  item.receiveShadow = true;
  parent.add(item);
  return item;
}

function addPlane(parent: THREE.Object3D, texture: THREE.Texture, w: number, h: number, x: number, y: number, z: number, rotationY = 0) {
  const item = new THREE.Mesh(new THREE.PlaneGeometry(w, h), new THREE.MeshStandardMaterial({map: texture, roughness: 0.8}));
  item.position.set(x, y, z);
  item.rotation.y = rotationY;
  item.castShadow = false;
  parent.add(item);
  return item;
}

function rug(parent: THREE.Object3D, x: number, z: number, w: number, d: number, material: THREE.Material) {
  const item = new THREE.Mesh(new THREE.PlaneGeometry(w, d), material);
  item.rotation.x = -Math.PI / 2;
  item.position.set(x, 0.008, z);
  item.receiveShadow = true;
  parent.add(item);
}

function taskChair(parent: THREE.Object3D, x: number, z: number, yaw: number) {
  const group = new THREE.Group();
  group.position.set(x, 0, z);
  group.rotation.y = yaw;
  addBox(group, 0.46, 0.06, 0.46, 0, 0.44, 0, mats.fabricGreen);
  addBox(group, 0.44, 0.46, 0.055, 0, 0.7, -0.2, mats.fabricGreen);
  addCylinder(group, 0.026, 0.026, 0.44, 0, 0.22, 0, mats.graphite, false);
  addBox(group, 0.42, 0.028, 0.05, 0, 0.05, 0, mats.graphite);
  for (let i = 0; i < 5; i++) {
    const angle = (i / 5) * Math.PI * 2;
    const leg = addBox(group, 0.28, 0.022, 0.04, Math.cos(angle) * 0.14, 0.042, Math.sin(angle) * 0.14, mats.graphite, false);
    leg.rotation.y = -angle;
  }
  parent.add(group);
  return group;
}

function desk(parent: THREE.Object3D, x: number, z: number, yaw: number, w = 1.6, d = 0.75) {
  const group = new THREE.Group();
  group.position.set(x, 0, z);
  group.rotation.y = yaw;
  addBox(group, w, 0.05, d, 0, 0.74, 0, mats.oak);
  for (const sx of [-1, 1]) for (const sz of [-1, 1]) addBox(group, 0.05, 0.72, 0.05, sx * (w / 2 - 0.08), 0.36, sz * (d / 2 - 0.06), mats.graphite);
  parent.add(group);
  return group;
}

function monitor(parent: THREE.Object3D, x: number, y: number, z: number, yaw: number, kind: 'bim' | 'plan' | 'mep' | 'material', width = 0.62) {
  const group = new THREE.Group();
  group.position.set(x, y, z);
  group.rotation.y = yaw;
  addBox(group, width, width * 0.6, 0.02, 0, 0, 0, mats.dark);
  const panel = new THREE.Mesh(new THREE.PlaneGeometry(width * 0.94, width * 0.54), new THREE.MeshStandardMaterial({map: screenTexture(kind), emissive: 0xffffff, emissiveMap: screenTexture(kind), emissiveIntensity: 0.32, roughness: 0.4}));
  panel.position.z = 0.012;
  group.add(panel);
  addBox(group, 0.05, width * 0.45, 0.05, 0, -width * 0.3 - 0.03, -0.03, mats.graphite);
  addBox(group, width * 0.38, 0.015, 0.16, 0, -width * 0.3 - 0.02, 0.02, mats.graphite);
  parent.add(group);
  return group;
}

function pinup(parent: THREE.Object3D, x: number, y: number, z: number, w: number, h: number, texture: THREE.Texture, yaw = 0) {
  const group = new THREE.Group();
  group.position.set(x, y, z);
  group.rotation.y = yaw;
  addBox(group, w + 0.06, h + 0.06, 0.035, 0, 0, 0, mats.oakDark);
  const sheet = new THREE.Mesh(new THREE.PlaneGeometry(w, h), new THREE.MeshStandardMaterial({map: texture, roughness: 0.9}));
  sheet.position.z = 0.022;
  group.add(sheet);
  parent.add(group);
  return group;
}

function tallBoard(parent: THREE.Object3D, x: number, z: number, w: number, h: number, yaw: number, texture: THREE.Texture) {
  const group = new THREE.Group();
  group.position.set(x, 0, z);
  group.rotation.y = yaw;
  addBox(group, w, h, 0.055, 0, h / 2 + 0.12, 0, mats.white);
  addBox(group, w + 0.05, 0.05, 0.06, 0, h + 0.14, 0, mats.trim);
  for (const sx of [-1, 1]) addBox(group, 0.05, h + 0.1, 0.05, sx * (w / 2 - 0.06), (h + 0.1) / 2, 0, mats.graphite);
  const sheet = new THREE.Mesh(new THREE.PlaneGeometry(w * 0.9, h * 0.82), new THREE.MeshStandardMaterial({map: texture, roughness: 0.9}));
  sheet.position.set(0, h / 2 + 0.14, 0.032);
  group.add(sheet);
  parent.add(group);
  return group;
}

function pinupPanel(parent: THREE.Object3D, x: number, z: number, width: number, height: number, yaw: number, sheets: THREE.Texture[]) {
  const group = new THREE.Group();
  group.position.set(x, 0, z);
  group.rotation.y = yaw;
  addBox(group, width, height, 0.06, 0, height / 2 + 0.26, 0, mats.oakDark);
  addBox(group, width + 0.06, 0.06, 0.09, 0, height + 0.29, 0, mats.trim);
  const cell = width / Math.max(1, sheets.length);
  sheets.forEach((texture, index) => {
    const sheet = new THREE.Mesh(new THREE.PlaneGeometry(cell * 0.78, height * 0.72), new THREE.MeshStandardMaterial({map: texture, roughness: 0.9}));
    sheet.position.set(-width / 2 + cell * (index + 0.5), height * 0.52 + 0.42, 0.038);
    group.add(sheet);
  });
  parent.add(group);
  return group;
}

function shelf(parent: THREE.Object3D, x: number, z: number, yaw: number, width = 1.1) {  const group = new THREE.Group();
  group.position.set(x, 0, z);
  group.rotation.y = yaw;
  addBox(group, width, 0.05, 0.32, 0, 1.32, 0, mats.oak);
  addBox(group, width, 0.05, 0.32, 0, 0.86, 0, mats.oak);
  addBox(group, 0.05, 1.35, 0.32, -width / 2, 0.675, 0, mats.graphite);
  addBox(group, 0.05, 1.35, 0.32, width / 2, 0.675, 0, mats.graphite);
  parent.add(group);
  return group;
}

/* ------------------------------- shell -------------------------------- */

function buildShell(world: THREE.Group) {
  const plinthMat = new THREE.MeshStandardMaterial({color: 0x22262a, roughness: 0.9});
  const plinth = new THREE.Mesh(new THREE.CylinderGeometry(21.5, 22.4, 0.55, 64), plinthMat);
  plinth.position.y = -0.32;
  plinth.receiveShadow = true;
  world.add(plinth);
  const brassRing = new THREE.Mesh(new THREE.TorusGeometry(21.55, 0.045, 8, 96), mats.brass);
  brassRing.rotation.x = Math.PI / 2;
  brassRing.position.y = -0.04;
  world.add(brassRing);

  const slab = addBox(world, FLOOR.x1 - FLOOR.x0, 0.14, FLOOR.z1 - FLOOR.z0, 0, -0.07, 0, new THREE.MeshStandardMaterial({color: 0x3c3f3d, roughness: 0.92}));
  slab.receiveShadow = true;

  const floorTexture = woodFloorTexture();
  const floor = new THREE.Mesh(new THREE.BoxGeometry(FLOOR.x1 - FLOOR.x0, 0.035, FLOOR.z1 - FLOOR.z0), new THREE.MeshStandardMaterial({map: floorTexture, roughness: 0.8}));
  floor.position.y = 0.017;
  floor.receiveShadow = true;
  world.add(floor);

  const corridor = new THREE.Mesh(new THREE.PlaneGeometry(FLOOR.x1 - FLOOR.x0 - 0.4, CORRIDOR.z1 - CORRIDOR.z0 - 0.12), new THREE.MeshStandardMaterial({map: corridorTexture(), roughness: 0.75}));
  corridor.rotation.x = -Math.PI / 2;
  corridor.position.set(0, 0.038, 0);
  corridor.receiveShadow = true;
  world.add(corridor);

  for (const z of [CORRIDOR.z0 + 0.1, CORRIDOR.z1 - 0.1]) addBox(world, FLOOR.x1 - FLOOR.x0 - 0.6, 0.012, 0.055, 0, 0.042, z, mats.brass, false);
  addBox(world, 0.12, 0.012, FLOOR.z1 - FLOOR.z0, FLOOR.x0 + 0.1, 0.042, 0, mats.brass, false);
  addBox(world, 0.12, 0.012, FLOOR.z1 - FLOOR.z0, FLOOR.x1 - 0.1, 0.042, 0, mats.brass, false);

  const threshold = new THREE.Mesh(new THREE.PlaneGeometry(2.6, 0.26), new THREE.MeshStandardMaterial({map: insoleTexture(), roughness: 0.5, metalness: 0.35}));
  threshold.rotation.x = -Math.PI / 2;
  threshold.position.set(5.02, 0.045, 0);
  world.add(threshold);
}

/* ------------------------------- rooms -------------------------------- */

type Room = {
  dept: Department;
  x0: number; x1: number; z0: number; z1: number;
  doorX: number;
  side: 'north' | 'south';
};

export const ROOMS: Room[] = [
  {dept: departments[0], x0: -11.4, x1: -6.2, z0: -6.4, z1: -1.4, doorX: -7.1, side: 'north'},
  {dept: departments[1], x0: -6.0, x1: -0.8, z0: -6.4, z1: -1.4, doorX: -1.7, side: 'north'},
  {dept: departments[2], x0: -0.6, x1: 4.6, z0: -6.4, z1: -1.4, doorX: 3.7, side: 'north'},
  {dept: departments[3], x0: -11.4, x1: -6.2, z0: 1.4, z1: 6.4, doorX: -7.1, side: 'south'},
  {dept: departments[4], x0: -6.0, x1: -0.8, z0: 1.4, z1: 6.4, doorX: -1.7, side: 'south'},
  {dept: departments[5], x0: -0.6, x1: 4.6, z0: 1.4, z1: 6.4, doorX: 3.7, side: 'south'},
];
const REVIEW_ROOM: Room = {dept: departments[6], x0: 5.0, x1: 11.4, z0: -6.4, z1: 6.4, doorX: 5.0, side: 'south'};

function wallSegment(parent: THREE.Object3D, x: number, z: number, length: number, horizontal: boolean, height = WALL_H, thickness = 0.12) {
  const w = horizontal ? length : thickness;
  const d = horizontal ? thickness : length;
  const item = addBox(parent, w, height, d, x, height / 2, z, mats.wall);
  addBox(parent, w + 0.01, 0.032, d + 0.01, x, height - 0.012, z, mats.trim, false);
  addBox(parent, w + 0.01, 0.07, d + 0.01, x, 0.035, z, mats.oakDark, false);
  return item;
}

function buildRoomShell(world: THREE.Group, room: Room) {
  const {x0, x1, z0, z1, doorX} = room;
  const half = 0.86;
  const frontZ = room.side === 'north' ? z1 : z0;
  const backZ = room.side === 'north' ? z0 : z1;
  const midX = (x0 + x1) / 2;
  const width = x1 - x0;

  // side walls
  wallSegment(world, x0, (z0 + z1) / 2, z1 - z0, false);
  wallSegment(world, x1, (z0 + z1) / 2, z1 - z0, false);
  // corridor wall with a door gap
  const leftLength = doorX - half - x0;
  const rightLength = x1 - (doorX + half);
  if (leftLength > 0.1) wallSegment(world, x0 + leftLength / 2, frontZ, leftLength, true);
  if (rightLength > 0.1) wallSegment(world, doorX + half + rightLength / 2, frontZ, rightLength, true);
  // outer wall (low cut-away so the working floor stays legible)
  wallSegment(world, midX, backZ, width, true, WALL_H);

  // portal
  const portalZ = frontZ;
  for (const sx of [-1, 1]) addBox(world, 0.13, PORTAL_H, 0.16, doorX + sx * half, PORTAL_H / 2, portalZ, mats.graphite);
  addBox(world, half * 2 + 0.28, 0.14, 0.17, doorX, PORTAL_H - 0.05, portalZ, mats.trim);
  addBox(world, half * 2 + 0.02, 0.03, 0.2, doorX, 0.05, portalZ, mats.brass, false);

  addBox(world, 3.24, 0.87, 0.05, doorX, 1.95, portalZ + 0.05, mats.graphite);
  const sign = new THREE.Mesh(new THREE.PlaneGeometry(3.12, 0.78), new THREE.MeshStandardMaterial({map: signTexture(room.dept), roughness: 0.65, emissive: 0x2a2418, emissiveIntensity: 0.35}));
  sign.position.set(doorX, 1.95, portalZ + 0.083);
  sign.rotation.x = -0.08;
  world.add(sign);

  // rug + soft ceiling beam
  rug(world, midX, (z0 + z1) / 2 + (room.side === 'north' ? 0.45 : -0.45), width - 0.9, 2.6, room.dept.id === 'interior' || room.dept.id === 'review' ? mats.rugWarm : mats.rugCool);

  for (const dx of [-1, 1]) addBox(world, 0.08, 0.02, 0.5, midX + dx * (width / 2 - 0.3), 2.6, (z0 + z1) / 2, mats.brass, false);
  const pendant = new THREE.Group();
  pendant.position.set(midX, 2.85, (z0 + z1) / 2);
  addCylinder(pendant, 0.008, 0.008, 0.5, 0, 0.25, 0, mats.dark, false);
  addCylinder(pendant, 0.16, 0.1, 0.16, 0, 0, 0, mats.brass, false);
  const glow = new THREE.Mesh(sphereGeo(0.055, 10, 8), new THREE.MeshStandardMaterial({color: 0xfff0d2, emissive: 0xffd9a0, emissiveIntensity: 1.6}));
  glow.position.y = -0.07;
  pendant.add(glow);
  world.add(pendant);

  // tiny room code beside the door
  addBox(world, 0.24, 0.16, 0.03, doorX + half + 0.32, 0.78, portalZ, mats.white, false);
}

/* ---------------------------- room dressing ---------------------------- */

function buildArchitectureRoom(world: THREE.Group, room: Room, station: [number, number]) {
  const [sx, sz] = station;
  const deskAssembly = buildArchitectureDesk();
  deskAssembly.group.position.set(sx, 0, sz);
  world.add(deskAssembly.group);

  pinupPanel(world, sx, room.z0 + 0.52, 2.9, 1.45, 0, [
    drawingTexture('arch-pin-0', {title: '总平面 · 1:500', mode: 'site'}),
    drawingTexture('arch-pin-1', {title: '三层平面 · 方案 C', mode: 'plan'}),
    drawingTexture('arch-pin-2', {title: '立面研究', mode: 'section'}),
  ]);
  const drafting = new THREE.Group();
  drafting.position.set(-10.35, 0, -2.55);
  drafting.rotation.y = 0.42;
  addBox(drafting, 1.3, 0.05, 0.78, 0, 0.86, 0, mats.oak);
  for (const [dx, dz] of [[-0.55, -0.3], [0.55, -0.3], [-0.55, 0.3], [0.55, 0.3]]) addBox(drafting, 0.04, 0.84, 0.04, dx, 0.42, dz, mats.graphite);
  const sheet = new THREE.Mesh(new THREE.PlaneGeometry(1.1, 0.68), new THREE.MeshStandardMaterial({map: drawingTexture('draft-sheet', {title: '剖面研究 · A-A'}), roughness: 0.9}));
  sheet.rotation.x = -Math.PI / 2;
  sheet.rotation.z = 0.1;
  sheet.position.set(0, 0.89, 0);
  drafting.add(sheet);
  world.add(drafting);
  plant(world, -10.75, -1.95, 0.9);
  shelf(world, -6.9, room.z0 + 0.35, Math.PI / 2, 1.2);
  addCylinder(world, 0.06, 0.06, 0.7, -6.85, 1.72, room.z0 + 0.4, mats.paper, false).rotation.z = Math.PI / 2;
  floorPatch(world, sx - 0.2, -4.6, 2.6, 1.9, 0xb3a795);
  return deskAssembly;
}

function buildPlanningRoom(world: THREE.Group, room: Room) {
  const cx = -3.4;
  const tableZ = -4.35;
  addBox(world, 2.15, 0.08, 1.35, cx, 0.8, tableZ, mats.oak);
  for (const [dx, dz] of [[-0.95, -0.55], [0.95, -0.55], [-0.95, 0.55], [0.95, 0.55]]) addBox(world, 0.06, 0.78, 0.06, cx + dx, 0.39, tableZ + dz, mats.graphite);
  const site = new THREE.Mesh(new THREE.PlaneGeometry(1.9, 1.12), new THREE.MeshStandardMaterial({map: siteModelTexture(), roughness: 0.94}));
  site.rotation.x = -Math.PI / 2;
  site.position.set(cx, 0.845, tableZ);
  world.add(site);
  const contour = new THREE.MeshStandardMaterial({color: 0xded5bf, roughness: 0.95});
  for (let i = 0; i < 5; i++) {
    const slab = addBox(world, 0.9 - i * 0.1, 0.025 + i * 0.004, 0.6 - i * 0.05, cx - 0.3 + i * 0.02, 0.86 + i * 0.024, tableZ + 0.05, contour, i > 1);
    slab.rotation.y = -0.12;
  }
  for (let i = 0; i < 7; i++) {
    const x = cx + 0.35 + (i % 4) * 0.21;
    const z = tableZ + 0.18 - Math.floor(i / 4) * 0.4;
    addCylinder(world, 0.012, 0.016, 0.09, x, 0.92, z, mats.oakDark, false, 5);
    const crown = new THREE.Mesh(sphereGeo(0.075, 8, 6), mats.leaf);
    crown.position.set(x, 1.02, z);
    crown.scale.set(1, 0.8, 1);
    crown.castShadow = true;
    world.add(crown);
  }
  addBox(world, 0.1, 0.02, 0.1, cx - 0.55, 0.9, tableZ - 0.2, mats.fabricRust, false);
  pinupPanel(world, cx, room.z0 + 0.5, 2.6, 1.5, 0, [drawingTexture('planning-aerial', {title: '滨水片区 · 总图研究', mode: 'site'})]);
  tallBoard(world, room.x1 - 0.35, room.z0 + 0.9, 1.5, 1.5, Math.PI / 2, drawingTexture('planning-board', {title: '景观剖面 · 视线分析', mode: 'section'}));
  shelf(world, room.x0 + 0.55, room.z0 + 0.5, 0, 1.4);
  for (let i = 0; i < 5; i++) addCylinder(world, 0.03, 0.03, 0.45, room.x0 + 0.55 - 0.5 + i * 0.22, 1.47, room.z0 + 0.5, mats.paper, false).rotation.z = Math.PI / 2;
  plant(world, -5.65, -2.1, 1.15);
  plant(world, -1.15, -5.7, 0.85);
}

function buildStructureRoom(world: THREE.Group, room: Room) {
  const cx = 2.35;
  const cz = -4.3;
  addBox(world, 1.6, 0.42, 1.3, cx, 0.21, cz, mats.concrete);
  addBox(world, 1.66, 0.035, 1.36, cx, 0.44, cz, mats.oakDark);
  const frameMat = mats.steel;
  for (const [dx, dz] of [[-0.55, -0.42], [0.55, -0.42], [-0.55, 0.42], [0.55, 0.42]]) addBox(world, 0.07, 1.15, 0.07, cx + dx, 1.02, cz + dz, frameMat);
  for (const level of [0.72, 1.18, 1.58]) {
    addBox(world, 1.24, 0.055, 0.07, cx, level, cz - 0.42, frameMat);
    addBox(world, 1.24, 0.055, 0.07, cx, level, cz + 0.42, frameMat);
    for (const dx of [-0.55, 0.55]) addBox(world, 0.07, 0.055, 0.92, cx + dx, level, cz, frameMat);
  }
  for (const dx of [-0.55, 0.55]) addBox(world, 0.055, 1.2, 0.055, cx + dx, 1.0, cz, frameMat, false);
  pinupPanel(world, 1.0, room.z0 + 0.5, 2.5, 1.45, 0, [
    drawingTexture('structure-beam', {title: '梁柱节点 · 结构轴网', mode: 'section'}),
    drawingTexture('structure-plan', {title: '基础平面', mode: 'plan'}),
  ]);
  shelf(world, room.x0 + 0.6, room.z0 + 0.45, 0, 1.2);
  for (let i = 0; i < 3; i++) addCylinder(world, 0.09, 0.09, 0.3, room.x0 + 0.35 + i * 0.22, 1.5, room.z0 + 0.45, mats.concrete, false);
  desk(world, 3.95, -2.2, -0.4, 1.25, 0.7);
  addCylinder(world, 0.05, 0.05, 0.24, 4.1, 0.87, -2.15, mats.white, false);
  addBox(world, 0.3, 0.02, 0.24, 3.8, 0.77, -2.2, mats.paper, false);
  plant(world, -0.1, -5.6, 0.8);
  floorPatch(world, cx, -4.1, 2.3, 1.7, 0xb0a595);
}

function buildMepRoom(world: THREE.Group, room: Room) {
  const cx = -8.55;
  // exposed duct run
  const duct = mats.steel;
  for (let i = 0; i < 4; i++) addBox(world, 1.15, 0.28, 0.34, -10.5 + i * 1.18, 2.18, 2.1, duct, false);
  addBox(world, 0.34, 0.28, 1.1, -10.9, 2.18, 2.6, duct, false);
  addBox(world, 0.34, 0.28, 0.9, -10.9, 2.18, 3.45, duct, false);
  for (let i = 0; i < 4; i++) addCylinder(world, 0.05, 0.05, 1.0, -10.2 + i * 1.2, 1.92, 2.45, mats.brass, false).rotation.x = Math.PI / 2;
  const ahu = new THREE.Group();
  ahu.position.set(-10.25, 0, 4.9);
  addBox(ahu, 1.15, 1.5, 0.85, 0, 0.78, 0, mats.steel);
  addBox(ahu, 0.9, 0.5, 0.03, 0, 1.05, 0.44, mats.dark, false);
  for (let i = 0; i < 6; i++) addBox(ahu, 0.7, 0.02, 0.02, 0, 0.85 + i * 0.06, 0.46, mats.brass, false);
  addCylinder(ahu, 0.16, 0.16, 0.03, 0, 0.45, 0.44, mats.white, false).rotation.x = Math.PI / 2;
  world.add(ahu);

  const deskGroup = desk(world, cx, 5.4, 0, 1.55, 0.7);
  monitor(world, cx - 0.25, 1.02, 5.55, Math.PI, 'mep', 0.6);
  addBox(world, 0.42, 0.018, 0.15, cx + 0.25, 0.775, 5.08, mats.graphite, false);
  addBox(world, 0.24, 0.012, 0.3, cx + 0.55, 0.775, 5.35, mats.paper, false);
  taskChair(world, cx, 4.72, 0);
  addBox(world, 0.4, 0.03, 0.5, cx + 0.62, 0.6, 4.9, mats.fabricRust, false);
  tallBoard(world, room.x1 - 0.55, 2.75, 1.5, 1.45, 0.42, drawingTexture('mep-plan', {title: '机电综合 · 三层', mode: 'plan'}));
  plant(world, -6.7, 2.1, 1.0);
}

function buildInteriorRoom(world: THREE.Group, room: Room) {
  // material board on the west wall
  tallBoard(world, room.x0 + 0.4, 3.9, 1.75, 1.5, Math.PI / 2, screenTexture('material'));
  // mock-up corner
  const chair = new THREE.Group();
  chair.position.set(-3.1, 0, 4.55);
  chair.rotation.y = -0.55;
  addBox(chair, 0.72, 0.16, 0.7, 0, 0.42, 0, mats.fabricRust);
  addBox(chair, 0.72, 0.55, 0.12, 0, 0.72, -0.34, mats.fabricRust);
  for (const [dx, dz] of [[-0.3, -0.28], [0.3, -0.28], [-0.3, 0.28], [0.3, 0.28]]) addBox(chair, 0.05, 0.34, 0.05, dx, 0.17, dz, mats.oakDark);
  world.add(chair);
  addCylinder(world, 0.26, 0.3, 0.42, -2.1, 0.21, 5.15, mats.oakDark);
  const tableTop = addCylinder(world, 0.32, 0.32, 0.04, -2.1, 0.44, 5.15, mats.white, false);
  tableTop.receiveShadow = true;
  addCylinder(world, 0.1, 0.12, 0.22, -2.1, 0.55, 5.15, mats.concrete, false);
  const lamp = new THREE.Group();
  lamp.position.set(-1.45, 0, 4.85);
  addCylinder(lamp, 0.16, 0.2, 0.02, 0, 0.02, 0, mats.graphite, false);
  addCylinder(lamp, 0.012, 0.012, 1.5, 0, 0.75, 0, mats.brass, false);
  addCylinder(lamp, 0.14, 0.07, 0.22, 0, 1.5, 0, mats.white, false);
  world.add(lamp);
  rug(world, -3.0, 4.8, 2.1, 1.6, mats.rugWarm);
  tallBoard(world, -1.15, 2.15, 1.3, 1.25, 0.3, drawingTexture('interior-mood', {title: '材质与色调研究', mode: 'plan'}));
  plant(world, -5.6, 5.6, 0.95);
  addBox(world, 0.8, 0.42, 0.4, -1.0, 0.21, 2.0, mats.oak);
  addBox(world, 0.7, 0.02, 0.3, -1.0, 0.44, 2.0, mats.paper, false);
}

function buildBimRoom(world: THREE.Group, room: Room) {
  const screen = new THREE.Group();
  screen.position.set(2.0, 0, 1.62);
  addBox(screen, 2.9, 1.62, 0.08, 0, 1.72, 0, mats.dark);
  const panel = new THREE.Mesh(new THREE.PlaneGeometry(2.78, 1.5), new THREE.MeshStandardMaterial({map: screenTexture('bim'), emissive: 0xffffff, emissiveMap: screenTexture('bim'), emissiveIntensity: 0.42, roughness: 0.4}));
  panel.position.set(0, 1.72, 0.05);
  screen.add(panel);
  for (const sx of [-1, 1]) addBox(screen, 0.06, 2.55, 0.06, sx * 1.3, 1.27, 0, mats.graphite);
  world.add(screen);

  desk(world, 3.7, 4.5, 0.1, 1.5, 0.72);
  monitor(world, 3.5, 1.0, 4.35, 0, 'bim', 0.62);
  monitor(world, 4.0, 0.95, 4.5, 0.5, 'bim', 0.5);
  taskChair(world, 3.75, 3.55, 0);
  const rack = new THREE.Group();
  rack.position.set(4.2, 0, 2.15);
  addBox(rack, 0.62, 1.85, 0.6, 0, 0.93, 0, mats.graphite);
  for (let i = 0; i < 9; i++) addBox(rack, 0.5, 0.03, 0.02, 0, 0.3 + i * 0.16, 0.31, new THREE.MeshStandardMaterial({color: i % 3 === 0 ? 0xd9b977 : 0x5d7a6d, emissive: i % 3 === 0 ? 0xb08a42 : 0x2a443c, emissiveIntensity: 0.7}), false);
  world.add(rack);
  for (let i = 0; i < 3; i++) addBox(world, 0.5, 0.05, 0.32, -0.15 + i * 0.6, 0.08, 3.0, mats.dark, false);
  addCylinder(world, 0.04, 0.04, 3.2, -0.4, 2.45, 3.0, mats.steel, false).rotation.z = Math.PI / 2;
  tallBoard(world, 0.15, 3.4, 1.15, 1.1, 0.35, drawingTexture('bim-axis', {title: '构件编码 · 轴网', mode: 'section'}));
  plant(world, 4.25, 5.9, 0.85);
}

function buildReviewHall(world: THREE.Group) {
  const cx = 8.2;
  // partition with a wide opening
  wallSegment(world, 5.0, -4.0, 4.8, false, WALL_H);
  wallSegment(world, 5.0, 4.0, 4.8, false, WALL_H);
  for (const sz of [-1, 1]) addBox(world, 0.18, PORTAL_H, 0.16, 5.0, PORTAL_H / 2, sz * 1.75, mats.graphite);
  addBox(world, 0.18, 0.16, 3.8, 5.0, PORTAL_H - 0.06, 0, mats.trim);
  const reviewSign = new THREE.Group();
  reviewSign.position.set(4.86, 0, 1.22);
  reviewSign.rotation.y = -0.36;
  addBox(reviewSign, 3.24, 0.87, 0.05, 0, 1.95, 0, mats.graphite);
  const sign = new THREE.Mesh(new THREE.PlaneGeometry(3.12, 0.78), new THREE.MeshStandardMaterial({map: signTexture(departments[6]), roughness: 0.65, emissive: 0x2a2418, emissiveIntensity: 0.35}));
  sign.position.set(0, 1.95, 0.033);
  reviewSign.add(sign);
  world.add(reviewSign);

  // boardroom table
  addBox(world, 3.4, 0.08, 1.5, cx, 0.75, 0.15, mats.oak);
  addBox(world, 3.1, 0.16, 1.2, cx, 0.64, 0.15, mats.oakDark, false);
  for (const dx of [-1.35, 1.35]) addBox(world, 0.15, 0.72, 0.15, cx + dx, 0.36, 0.15, mats.graphite);
  for (let i = 0; i < 4; i++) for (const side of [-1, 1]) {
    const chair = new THREE.Group();
    chair.position.set(cx - 1.1 + i * 0.75, 0, 0.15 + side * 1.08);
    chair.rotation.y = side > 0 ? Math.PI : 0;
    addBox(chair, 0.46, 0.06, 0.46, 0, 0.45, 0, mats.fabricGreen);
    addBox(chair, 0.46, 0.48, 0.06, 0, 0.7, -0.2, mats.fabricGreen);
    for (const [dx, dz] of [[-0.18, -0.18], [0.18, -0.18], [-0.18, 0.18], [0.18, 0.18]]) addBox(chair, 0.035, 0.45, 0.035, dx, 0.22, dz, mats.graphite);
    world.add(chair);
  }
  // physical massing model in the middle of the table
  addBox(world, 0.9, 0.08, 0.7, cx, 0.83, 0.15, mats.white);
  const white = mats.white;
  addBox(world, 0.42, 0.22, 0.3, cx - 0.12, 0.98, 0.15, white);
  addBox(world, 0.24, 0.36, 0.24, cx + 0.22, 1.05, 0.1, white);
  addBox(world, 0.2, 0.14, 0.34, cx + 0.02, 0.94, 0.24, white);
  addCylinder(world, 0.09, 0.09, 0.02, cx - 0.32, 0.895, 0.36, mats.oakDark, false);
  addCylinder(world, 0.05, 0.06, 0.1, cx + 0.42, 0.91, 0.4, mats.white, false);
  addBox(world, 0.44, 0.01, 0.3, cx - 0.75, 0.795, 0.4, mats.paper, false);

  // review screen on a stand facing the table
  const screenGroup = new THREE.Group();
  screenGroup.position.set(cx, 0, -4.55);
  addBox(screenGroup, 3.2, 1.75, 0.08, 0, 1.85, 0, mats.dark);
  const panel = new THREE.Mesh(new THREE.PlaneGeometry(3.05, 1.6), new THREE.MeshStandardMaterial({map: screenTexture('plan'), emissive: 0xffffff, emissiveMap: screenTexture('plan'), emissiveIntensity: 0.4, roughness: 0.4}));
  panel.position.set(0, 1.85, 0.05);
  screenGroup.add(panel);
  for (const sx of [-1, 1]) addBox(screenGroup, 0.07, 2.7, 0.07, sx * 1.45, 1.35, 0, mats.graphite);
  world.add(screenGroup);

  // sideboard and pin-ups
  addBox(world, 2.6, 0.62, 0.5, cx, 0.31, 5.9, mats.oak);
  tallBoard(world, 10.45, -2.1, 1.7, 1.45, -1.15, drawingTexture('review-board', {title: '方案评审 · 总图', mode: 'site'}));
  plant(world, 10.7, 5.6, 1.2);
  plant(world, 10.7, -5.6, 1.1);
  plant(world, 6.0, 3.4, 0.9);
  rug(world, cx, 0.15, 4.2, 3.2, mats.rugWarm);
}

/* ----------------------------- corridor ------------------------------ */

function buildCorridor(world: THREE.Group) {
  const directory = new THREE.Group();
  directory.position.set(-4.55, 0, -1.28);
  addBox(directory, 1.5, 1.05, 0.06, 0, 1.5, 0, mats.dark);
  for (const sx of [-1, 1]) addBox(directory, 0.05, 1.05, 0.05, sx * 0.62, 0.6, 0, mats.graphite);
  addBox(directory, 1.3, 1.85, 0.05, 0, 0.95, -0.04, mats.dark, false);
  const front = new THREE.Mesh(new THREE.PlaneGeometry(1.36, 0.92), new THREE.MeshStandardMaterial({map: directoryTexture(), roughness: 0.85}));
  front.position.set(0, 1.5, 0.035);
  directory.add(front);
  const back = new THREE.Mesh(new THREE.PlaneGeometry(1.36, 0.92), new THREE.MeshStandardMaterial({map: directoryTexture(), roughness: 0.85, side: THREE.BackSide}));
  back.position.set(0, 1.5, -0.035);
  directory.add(back);
  world.add(directory);

  for (let i = 0; i < 4; i++) {
    const x = -10.2 + i * 3.1;
    addCylinder(world, 0.006, 0.006, 1.35, x, 3.15, 0, mats.dark, false);
    addCylinder(world, 0.13, 0.09, 0.13, x, 2.42, 0, mats.brass, false);
    const glow = new THREE.Mesh(sphereGeo(0.05, 8, 6), new THREE.MeshStandardMaterial({color: 0xfff2d8, emissive: 0xffd6a0, emissiveIntensity: 1.8}));
    glow.position.set(x, 2.34, 0);
    world.add(glow);
  }
  for (const [x, z] of [[-9.6, 1.05], [-5.4, 1.02], [0.2, 1.05], [-1.6, -1.05], [3.0, -1.02]]) plant(world, x, z, 0.62);
  // bench
  addBox(world, 1.8, 0.09, 0.45, -0.9, 0.45, 1.02, mats.oak);
  for (const dx of [-0.7, 0.7]) addBox(world, 0.06, 0.44, 0.4, -0.9 + dx, 0.22, 1.02, mats.graphite);
  // light oak meeting ledge
  addBox(world, 0.8, 0.9, 0.36, 2.0, 0.45, -1.16, mats.oakDark);
  addBox(world, 0.7, 0.03, 0.3, 2.0, 0.92, -1.16, mats.white, false);
  addCylinder(world, 0.055, 0.06, 0.18, 2.15, 1.02, -1.16, mats.white, false);
}

/* ----------------------------- assemble ------------------------------- */

export type InstituteHandle = {
  scene: THREE.Scene;
  camera: THREE.PerspectiveCamera;
  detailCamera: THREE.PerspectiveCamera;
  renderer: THREE.WebGLRenderer;
  setPaused: (value: boolean) => void;
  setFocus: (mode: 'overview' | 'actor') => void;
  portrait: (mode: 'off' | 'body' | 'face') => void;
  setReduced: (value: boolean) => void;
  seek: (time: number) => void;
  render: () => void;
  resize: () => void;
  snapshot: () => Record<string, unknown>;
  departmentAt: (clientX: number, clientY: number) => DepartmentId | null;
  hoverAt: (clientX: number, clientY: number) => DepartmentId | null;
  project: (x: number, y: number, z: number) => {x: number; y: number; z: number};
  projectDetail: (x: number, y: number, z: number) => {x: number; y: number; z: number};
  landmarks: () => Record<string, {x: number; y: number; z: number}>;
  dispose: () => void;
};

type Callbacks = {
  host: HTMLDivElement;
  onReady?: () => void;
  onEnter?: (id: DepartmentId) => void;
};

export function createInstitute({host, onEnter, onReady}: Callbacks): InstituteHandle {
  const scene = new THREE.Scene();
  const camera = new THREE.PerspectiveCamera(30, 1, 0.4, 120);
  const detailCamera = new THREE.PerspectiveCamera(31, 1, 0.2, 40);
  const renderer = new THREE.WebGLRenderer({alpha: true, antialias: true, powerPreference: 'high-performance'});
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 1.6));
  renderer.setClearColor(0x000000, 0);
  renderer.shadowMap.enabled = true;
  renderer.shadowMap.type = THREE.PCFSoftShadowMap;
  renderer.toneMapping = THREE.ACESFilmicToneMapping;
  renderer.toneMappingExposure = 1.02;
  renderer.autoClear = false;
  renderer.domElement.setAttribute('aria-hidden', 'true');
  host.appendChild(renderer.domElement);

  const world = new THREE.Group();
  scene.add(world);
  buildShell(world);

  for (const room of ROOMS) buildRoomShell(world, room);
  buildReviewHall(world);

  const station: [number, number] = [-8.8, -4.5];
  const deskAssembly = buildArchitectureRoom(world, ROOMS[0], station);
  buildPlanningRoom(world, ROOMS[1]);
  buildStructureRoom(world, ROOMS[2]);
  buildMepRoom(world, ROOMS[3]);
  buildInteriorRoom(world, ROOMS[4]);
  buildBimRoom(world, ROOMS[5]);
  buildCorridor(world);

  // main architect walk
  const path: Array<[number, number]> = [
    [-8.8, -4.5],
    [-8.78, -4.36],
    [-7.55, -4.36],
    [-7.4, -2.55],
    [-7.3, -0.95],
    [-6.85, 0.14],
    [-4.55, 0.36],
  ];
  const main = createMainActor({station, path, paper: deskAssembly.paperPivot, chair: deskAssembly.chair});
  scene.add(main.person.root);

  const worker = (palette: Parameters<typeof createResident>[0]['palette'], position: [number, number, number], yaw: number, pose: (t: number) => Pose) =>
    createResident({palette, position, yaw, pose});

  const idle = (time: number) => Math.sin(time * 1.1) * 0.006;

  const planning = worker(
    {skin: 0xc08a68, shirt: 0x6f7563, trouser: 0x4a463c, shoe: 0x2a2724, hair: 0x3b2a22, hairstyle: 'bun', shirtStyle: 'shirt'},
    [-3.4, 0, -3.1], Math.PI,
    time => {
      const lean = 0.14 + idle(time);
      return {
        hipY: 0.945 + idle(time) * 0.6,
        spine: [lean, 0, 0],
        chest: [0.06, 0.03, 0],
        feet: [{x: -0.12, y: ANKLE, z: 0.03, yaw: 0.08}, {x: 0.12, y: ANKLE, z: 0.03, yaw: -0.08}],
        hands: [
          {x: 0.13, y: 0.88, z: 0.4, frame: 'down', curl: 0.5},
          {x: 0.36, y: 0.97 + Math.sin(time * 1.7) * 0.015, z: 0.62, frame: 'down', curl: 0.2},
        ],
        headTarget: [0.1, 0.96, 0.6],
        blink: blinkAt(time, 5),
      };
    },
  );

  const structure = worker(
    {skin: 0xe0b394, shirt: 0x3f4a50, trouser: 0x2f3436, shoe: 0x252220, hair: 0x1f1a17, hairstyle: 'crop', glasses: true},
    [1.1, 0, -3.15], 2.35,
    time => ({
      hipY: 0.947,
      spine: [0.09, 0.05, 0],
      chest: [0.03, 0.08, -0.02],
      feet: [{x: -0.12, y: ANKLE, z: 0.05, yaw: 0.1}, {x: 0.12, y: ANKLE, z: -0.02, yaw: -0.05}],
      hands: [
        {x: -0.24, y: 0.94, z: 0.16, frame: 'side', curl: 0.6},
        {x: 0.07, y: 1.58 + Math.sin(time * 0.9) * 0.005, z: 0.16, frame: 'side', curl: 0.15},
      ],
      headTarget: [0.7, 1.4, 0.8],
      blink: blinkAt(time, 9),
    }),
  );

  const mep = worker(
    {skin: 0xb9805f, shirt: 0x8c9aa0, trouser: 0x33393c, shoe: 0x2a2622, hair: 0x2d241f, hairstyle: 'short', shirtStyle: 'sweater'},
    [-8.55, 0, 4.72], 0,
    time => ({
      hipY: 0.572,
      spine: [0.12, 0, 0],
      chest: [0.05, -0.03, 0],
      feet: [{x: -0.12, y: ANKLE, z: 0.33, yaw: 0.07}, {x: 0.12, y: ANKLE, z: 0.33, yaw: -0.07}],
      hands: [
        {x: -0.15, y: 0.835, z: 0.32, frame: 'down', curl: 0.45, tap: Math.sin(time * 9) * 0.18},
        {x: 0.2, y: 0.835, z: 0.34, frame: 'down', curl: 0.4},
      ],
      headTarget: [0.2, 0.94, 0.52],
      blink: blinkAt(time, 12),
    }),
  );

  const interior = worker(
    {skin: 0xd8a887, shirt: 0xd9cbb8, trouser: 0x585149, shoe: 0x2c2724, hair: 0x4a3528, hairstyle: 'bob', shirtStyle: 'blazer'},
    [-5.02, 0, 3.92], -1.5,
    time => ({
      hipY: 0.947,
      spine: [0.03, -0.05, 0],
      chest: [0.01, -0.06, 0],
      feet: [{x: -0.12, y: ANKLE, z: 0.03, yaw: 0.06}, {x: 0.12, y: ANKLE, z: 0.05, yaw: -0.08}],
      hands: [
        {x: -0.24, y: 0.86, z: 0.04, frame: 'side', curl: 0.35},
        {x: 0.08, y: 1.36 + Math.sin(time * 1.3) * 0.02, z: 0.44, frame: 'board', curl: 0.2},
      ],
      headTarget: [0.1, 1.4, 0.34],
      blink: blinkAt(time, 15),
    }),
  );

  const bim = worker(
    {skin: 0xcaa07f, shirt: 0x3c4345, trouser: 0x30373a, shoe: 0x22242a, hair: 0x231c18, hairstyle: 'short', glasses: true},
    [0.95, 0, 3.05], 2.5,
    time => ({
      hipY: 0.947,
      spine: [-0.02, 0.04, 0],
      chest: [-0.03, 0.05, 0],
      feet: [{x: -0.12, y: ANKLE, z: 0.03}, {x: 0.12, y: ANKLE, z: -0.02}],
      hands: [
        {x: -0.25, y: 0.92, z: -0.02, frame: 'side', curl: 0.4},
        {x: 0.1, y: 1.6, z: 0.12, frame: 'side', curl: 0.2},
      ],
      headTarget: [0.3, 1.8, 0.7],
      blink: blinkAt(time, 18),
    }),
  );

  const review = worker(
    {skin: 0xd0a281, shirt: 0x30363a, trouser: 0x262b2e, shoe: 0x211f1d, hair: 0x6a6a68, hairstyle: 'short', shirtStyle: 'blazer'},
    [6.32, 0, 1.36], 2.42,
    time => ({
      hipY: 0.948,
      spine: [0.2, -0.05, 0],
      chest: [0.08, -0.06, 0],
      feet: [{x: -0.13, y: ANKLE, z: 0.03}, {x: 0.13, y: ANKLE, z: 0.05}],
      hands: [
        {x: -0.26, y: 0.82, z: 0.55, frame: 'down', curl: 0.3},
        {x: 0.27, y: 0.82, z: 0.55, frame: 'down', curl: 0.3},
      ],
      headTarget: [0.15, 0.98, 0.75],
      blink: blinkAt(time, 21),
    }),
  );

  const residents = [planning, structure, mep, interior, bim, review];
  for (const resident of residents) scene.add(resident.person.root);

  // lights
  const hemi = new THREE.HemisphereLight(0xfff4e4, 0x8b8172, 1.15);
  scene.add(hemi);
  const sun = new THREE.DirectionalLight(0xffe9c9, 2.5);
  sun.position.set(-9, 18, 13);
  sun.castShadow = true;
  sun.shadow.mapSize.set(2048, 2048);
  sun.shadow.bias = -0.0009;
  sun.shadow.normalBias = 0.02;
  const shadowCamera = sun.shadow.camera as THREE.OrthographicCamera;
  shadowCamera.left = -16; shadowCamera.right = 16; shadowCamera.top = 16; shadowCamera.bottom = -16;
  shadowCamera.near = 1; shadowCamera.far = 55;
  scene.add(sun);
  const fill = new THREE.DirectionalLight(0xbcd4e2, 0.55);
  fill.position.set(12, 9, -10);
  scene.add(fill);
  scene.add(new THREE.AmbientLight(0xffffff, 0.16));
  for (const x of [-8.4, -3.4, 1.6]) {
    const lamp = new THREE.PointLight(0xffd9a6, 5.5, 9, 2);
    lamp.position.set(x, 2.35, 0);
    scene.add(lamp);
  }
  const reviewLamp = new THREE.PointLight(0xffe3b5, 6, 10, 2);
  reviewLamp.position.set(8.2, 2.6, 0.2);
  scene.add(reviewLamp);

  // camera framing
  const orbit = {yaw: 0.08, pitch: 0.84, radius: 25.9, target: new THREE.Vector3(0.3, -0.55, 0.3)};
  const applyCamera = () => {
    const cos = Math.cos(orbit.pitch);
    camera.position.set(
      orbit.target.x + orbit.radius * cos * Math.sin(orbit.yaw),
      orbit.target.y + orbit.radius * Math.sin(orbit.pitch),
      orbit.target.z + orbit.radius * cos * Math.cos(orbit.yaw),
    );
    camera.lookAt(orbit.target);
  };
  applyCamera();

  // hover highlights
  const highlight = new THREE.Group();
  scene.add(highlight);
  const outlineMat = new THREE.LineBasicMaterial({color: 0xe0c38c, transparent: true, opacity: 0.9});
  const outlines = new Map<DepartmentId, THREE.Line>();
  for (const room of [...ROOMS, REVIEW_ROOM]) {
    const points = [
      new THREE.Vector3(room.x0 + 0.08, 0.06, room.z0 + 0.08),
      new THREE.Vector3(room.x1 - 0.08, 0.06, room.z0 + 0.08),
      new THREE.Vector3(room.x1 - 0.08, 0.06, room.z1 - 0.08),
      new THREE.Vector3(room.x0 + 0.08, 0.06, room.z1 - 0.08),
    ];
    const line = new THREE.Line(new THREE.BufferGeometry().setFromPoints([...points, points[0]]), outlineMat);
    line.visible = false;
    highlight.add(line);
    outlines.set(room.dept.id, line);
  }

  let hovered: DepartmentId | null = null;
  const raycaster = new THREE.Raycaster();
  const pointer = new THREE.Vector2();
  const plane = new THREE.Plane(new THREE.Vector3(0, 1, 0), 0);
  const hit = new THREE.Vector3();

  function pick(clientX: number, clientY: number): DepartmentId | null {
    const rect = host.getBoundingClientRect();
    pointer.set(((clientX - rect.left) / rect.width) * 2 - 1, -((clientY - rect.top) / rect.height) * 2 + 1);
    raycaster.setFromCamera(pointer, camera);
    if (!raycaster.ray.intersectPlane(plane, hit)) return null;
    for (const room of [...ROOMS, REVIEW_ROOM]) {
      if (hit.x >= room.x0 && hit.x <= room.x1 && hit.z >= room.z0 && hit.z <= room.z1) return room.dept.id;
    }
    return null;
  }

  function setHover(id: DepartmentId | null) {
    if (id === hovered) return;
    if (hovered) outlines.get(hovered)!.visible = false;
    hovered = id;
    if (hovered) outlines.get(hovered)!.visible = true;
    if (!paused) renderFrame();
  }

  // interaction: drag to orbit, click a room to enter
  let dragging = false;
  let moved = 0;
  let lastX = 0;
  let lastY = 0;
  const onOverlay = (target: EventTarget | null) => !!(target as HTMLElement | null)?.closest?.('button, a, .ih-scene-hud');
  const down = (event: PointerEvent) => {
    if (onOverlay(event.target)) return;
    dragging = true;
    moved = 0;
    lastX = event.clientX;
    lastY = event.clientY;
    host.setPointerCapture(event.pointerId);
  };
  const move = (event: PointerEvent) => {
    if (dragging) {
      moved += Math.abs(event.clientX - lastX) + Math.abs(event.clientY - lastY);
      orbit.yaw = THREE.MathUtils.clamp(orbit.yaw - (event.clientX - lastX) * 0.004, -0.5, 0.6);
      orbit.pitch = THREE.MathUtils.clamp(orbit.pitch + (event.clientY - lastY) * 0.003, 0.35, 1.05);
      lastX = event.clientX;
      lastY = event.clientY;
      applyCamera();
      if (!paused) renderFrame();
      host.style.cursor = 'grabbing';
    } else {
      setHover(pick(event.clientX, event.clientY));
      host.style.cursor = hovered ? 'pointer' : 'grab';
    }
  };
  const up = (event: PointerEvent) => {
    if (onOverlay(event.target)) return;
    dragging = false;
    host.style.cursor = 'grab';
    if (moved < 6) {
      const id = pick(event.clientX, event.clientY);
      if (id) onEnter?.(id);
    }
  };
  const leave = () => { if (!dragging) setHover(null); };
  host.addEventListener('pointerdown', down);
  host.addEventListener('pointermove', move);
  host.addEventListener('pointerup', up);
  host.addEventListener('pointerleave', leave);

  // sizing
  let paused = false;
  let reduced = false;
  let focus: 'overview' | 'actor' = 'overview';
  let portraitMode: 'off' | 'body' | 'face' = 'off';
  let time = 4.2;
  let raf = 0;
  let last = 0;
  let frameCount = 0;
  let width = 1, height = 1;
  const resize = () => {
    const rect = host.getBoundingClientRect();
    if (!rect.width || !rect.height) return;
    width = rect.width;
    height = rect.height;
    renderer.setSize(width, height, false);
    camera.aspect = width / height;
    camera.updateProjectionMatrix();
    detailCamera.aspect = width / height;
    detailCamera.updateProjectionMatrix();
    renderFrame();
  };
  const observer = new ResizeObserver(resize);
  observer.observe(host);

  function updateDetailCamera(snap = false) {
    const root = main.person.root;
    const phase = ((time % MAIN_LOOP) + MAIN_LOOP) % MAIN_LOOP;
    const atDesk = phase < 16.6 || phase >= 38;
    const distance = 2.75;
    const targetHeight = atDesk ? 1.02 : 0.95;
    const offset = atDesk ? new THREE.Vector3(1.0, 1.55, distance) : new THREE.Vector3(1.5, 2.65, -2.7);
    root.localToWorld(offset);
    if (snap) detailCamera.position.copy(offset);
    else detailCamera.position.lerp(offset, 0.09);
    const target = new THREE.Vector3(atDesk ? 0.16 : 0, targetHeight, 0.08);
    root.localToWorld(target);
    detailCamera.lookAt(target);
  }

  // start the detail camera near the subject instead of the origin
  main.update(time);
  updateDetailCamera(true);

  const orbitPosition = new THREE.Vector3();
  const orbitQuaternion = new THREE.Quaternion();
  const lookHelper = new THREE.PerspectiveCamera();
  const computeOrbit = () => {
    const cos = Math.cos(orbit.pitch);
    orbitPosition.set(
      orbit.target.x + orbit.radius * cos * Math.sin(orbit.yaw),
      orbit.target.y + orbit.radius * Math.sin(orbit.pitch),
      orbit.target.z + orbit.radius * cos * Math.cos(orbit.yaw),
    );
    lookHelper.position.copy(orbitPosition);
    lookHelper.lookAt(orbit.target);
    orbitQuaternion.copy(lookHelper.quaternion);
  };

  const portraitPosition = new THREE.Vector3();
  const portraitTarget = new THREE.Vector3();
  const portraitQuaternion = new THREE.Quaternion();
  const portraitHelper = new THREE.PerspectiveCamera();
  const portraitLight = new THREE.DirectionalLight(0xfff3e2, 2.4);
  portraitLight.visible = false;
  scene.add(portraitLight);
  const portraitBounce = new THREE.DirectionalLight(0xcfd9d4, 1.1);
  portraitBounce.visible = false;
  scene.add(portraitBounce);
  scene.add(portraitLight.target);
  scene.add(portraitBounce.target);

  const headWorld = new THREE.Vector3();
  const bodyQuat = new THREE.Quaternion();
  function applyPortrait() {
    main.person.rig.head.getWorldPosition(headWorld);
    headWorld.y += 0.11;
    main.person.root.getWorldQuaternion(bodyQuat);
    if (portraitMode === 'face') {
      portraitPosition.copy(headWorld).add(new THREE.Vector3(0.2, 0.08, 0.8).applyQuaternion(bodyQuat));
      portraitTarget.copy(headWorld).add(new THREE.Vector3(0, -0.01, 0.02).applyQuaternion(bodyQuat));
    } else {
      portraitPosition.copy(headWorld).add(new THREE.Vector3(0.85, -0.5, 3.1).applyQuaternion(bodyQuat));
      portraitTarget.copy(headWorld).add(new THREE.Vector3(0, -0.62, 0).applyQuaternion(bodyQuat));
    }
    portraitHelper.position.copy(portraitPosition);
    portraitHelper.lookAt(portraitTarget);
    portraitQuaternion.copy(portraitHelper.quaternion);
    world.visible = false;
    for (const resident of residents) resident.person.root.visible = false;
    main.person.root.visible = true;
    portraitLight.visible = true;
    portraitBounce.visible = true;
    portraitLight.position.copy(portraitPosition);
    portraitLight.position.y += 0.35;
    portraitLight.target.position.copy(portraitTarget);
    portraitLight.target.updateMatrixWorld();
    const back = new THREE.Vector3(-0.5, 0.3, -1.6);
    main.person.root.localToWorld(back);
    portraitBounce.position.copy(back);
    portraitBounce.target.position.copy(portraitTarget);
    portraitBounce.target.updateMatrixWorld();
    scene.background = new THREE.Color(0x4b5450);
  }

  function renderFrame(snapCamera = false) {
    if (!width || !height) return;
    main.update(time);
    for (const resident of residents) resident.update(time);
    updateDetailCamera(snapCamera);
    computeOrbit();
    if (portraitMode !== 'off') applyPortrait();
    const targetPosition = portraitMode !== 'off' ? portraitPosition : focus === 'actor' ? detailCamera.position : orbitPosition;
    const targetQuaternion = portraitMode !== 'off' ? portraitQuaternion : focus === 'actor' ? detailCamera.quaternion : orbitQuaternion;
    if (snapCamera || frameCount === 0) {
      camera.position.copy(targetPosition);
      camera.quaternion.copy(targetQuaternion);
    } else {
      camera.position.lerp(targetPosition, 0.12);
      camera.quaternion.slerp(targetQuaternion, 0.12);
    }
    renderer.setScissorTest(false);
    renderer.setViewport(0, 0, width, height);
    renderer.setClearColor(0x000000, 0);
    renderer.clear(true, true, true);
    renderer.render(scene, camera);
    frameCount++;
  }

  resize();

  const setPaused = (value: boolean) => {
    paused = value;
    if (paused) renderFrame(true);
  };
  const setFocus = (mode: 'overview' | 'actor') => {
    focus = mode;
    portraitMode = 'off';
    world.visible = true;
    portraitLight.visible = false;
    portraitBounce.visible = false;
    scene.background = null;
    for (const resident of residents) resident.person.root.visible = true;
    if (paused || reduced) renderFrame(true);
  };
  const portrait = (mode: 'off' | 'body' | 'face') => {
    portraitMode = mode;
    if (mode === 'off') {
      world.visible = true;
      for (const resident of residents) resident.person.root.visible = true;
      portraitLight.visible = false;
      portraitBounce.visible = false;
      scene.background = null;
    }
    renderFrame(true);
  };
  const setReduced = (value: boolean) => {
    reduced = value;
    if (reduced) { time = 4.2; renderFrame(); }
  };
  const seek = (value: number) => {
    time = value;
    renderFrame(true);
  };

  const tick = (now: number) => {
    raf = requestAnimationFrame(tick);
    if (paused || document.hidden || reduced) {
      if (document.hidden) last = now;
      return;
    }
    if (!last) last = now;
    const delta = Math.min(0.05, (now - last) / 1000);
    last = now;
    time += delta;
    renderFrame();
  };
  raf = requestAnimationFrame(tick);
  requestAnimationFrame(() => onReady?.());

  const contextLost = (event: Event) => { event.preventDefault(); };
  renderer.domElement.addEventListener('webglcontextlost', contextLost);

  const projectWith = (cam: THREE.Camera, x: number, y: number, z: number) => {
    const v = new THREE.Vector3(x, y, z).project(cam);
    return {x: (v.x * 0.5 + 0.5) * width, y: (-v.y * 0.5 + 0.5) * height, z: v.z};
  };
  const projectDetailWith = (x: number, y: number, z: number) => {
    const v = new THREE.Vector3(x, y, z).project(detailCamera);
    return {x: (v.x * 0.5 + 0.5) * width, y: (-v.y * 0.5 + 0.5) * height, z: v.z};
  };
  const landmarks = () => {
    const points: Record<string, {x: number; y: number; z: number}> = {};
    const head = new THREE.Vector3();
    main.person.rig.head.getWorldPosition(head);
    points.head = projectDetailWith(head.x, head.y + 0.16, head.z);
    points.chest = projectDetailWith(head.x, head.y - 0.3, head.z);
    const hand = new THREE.Vector3();
    main.person.rig.hands[1].getWorldPosition(hand);
    points.handR = projectDetailWith(hand.x, hand.y, hand.z);
    const foot = new THREE.Vector3();
    main.person.rig.feet[0].getWorldPosition(foot);
    points.footL = projectDetailWith(foot.x, foot.y, foot.z);
    return points;
  };

  const snapshot = () => ({
    time,
    paused,
    reduced,
    focus,
    portrait: portraitMode,
    frameCount,
    activity: main.activity(time),
    actor: main.snapshot(),
    camera: camera.position.toArray(),
    detailCamera: detailCamera.position.toArray(),
    hovered,
  });

  const dispose = () => {
    cancelAnimationFrame(raf);
    observer.disconnect();
    host.removeEventListener('pointerdown', down);
    host.removeEventListener('pointermove', move);
    host.removeEventListener('pointerup', up);
    host.removeEventListener('pointerleave', leave);
    renderer.domElement.removeEventListener('webglcontextlost', contextLost);
    scene.traverse(item => {
      const mesh = item as THREE.Mesh;
      if (mesh.geometry && !mesh.geometry.userData.shared) mesh.geometry.dispose();
    });
    renderer.dispose();
    renderer.domElement.remove();
  };

  return {
    scene, camera, detailCamera, renderer,
    setPaused, setReduced, setFocus, portrait, seek, render: () => renderFrame(true), resize, snapshot, dispose,
    departmentAt: (x, y) => pick(x, y),
    hoverAt: (x, y) => pick(x, y),
    project: (x, y, z) => projectWith(camera, x, y, z),
    projectDetail: (x, y, z) => projectDetailWith(x, y, z),
    landmarks,
  };
}
