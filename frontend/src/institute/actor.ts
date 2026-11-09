import * as THREE from 'three';
import {applyPose, blinkAt, buildPerson, ease, lerp, mixPose, type Person, type Pose} from './character';
import {boxGeo, geo, screenTexture, sphereGeo, drawingTexture, woodFloorTexture} from './textures';

/* --------------------------- shared furniture --------------------------- */

const wood = new THREE.MeshStandardMaterial({color: 0xb08d63, roughness: 0.72});
const woodDark = new THREE.MeshStandardMaterial({color: 0x6c5942, roughness: 0.7});
const graphite = new THREE.MeshStandardMaterial({color: 0x33383a, roughness: 0.55, metalness: 0.2});
const metal = new THREE.MeshStandardMaterial({color: 0x9a9c97, roughness: 0.32, metalness: 0.7});
const paperMat = new THREE.MeshStandardMaterial({color: 0xf4efe2, roughness: 0.9, side: THREE.DoubleSide});
const accent = new THREE.MeshStandardMaterial({color: 0xc2a268, roughness: 0.42, metalness: 0.45});
const screenMat = new THREE.MeshStandardMaterial({map: screenTexture('plan'), emissive: 0xdcc48d, emissiveIntensity: 0.18, roughness: 0.35});
const fabric = new THREE.MeshStandardMaterial({color: 0x4b5250, roughness: 0.95});

function addBox(parent: THREE.Object3D, w: number, h: number, d: number, x: number, y: number, z: number, material: THREE.Material, shadow = true) {
  const item = new THREE.Mesh(boxGeo(w, h, d), material);
  item.position.set(x, y, z);
  item.castShadow = shadow;
  item.receiveShadow = true;
  parent.add(item);
  return item;
}

function addCyl(parent: THREE.Object3D, rt: number, rb: number, h: number, x: number, y: number, z: number, material: THREE.Material, shadow = true) {
  const item = new THREE.Mesh(geo(rt, rb, h), material);
  item.position.set(x, y, z);
  item.castShadow = shadow;
  parent.add(item);
  return item;
}

/** Desk used by the main architect. Origin = the seat point, facing +z. */
export function buildArchitectureDesk() {
  const group = new THREE.Group();
  group.name = 'architecture-workstation';
  const topY = 0.75;
  addBox(group, 1.9, 0.055, 0.8, 0, topY, 0.65, wood);
  addBox(group, 1.82, 0.05, 0.72, 0, topY - 0.075, 0.65, woodDark, false);
  for (const [x, z] of [[-0.86, 0.33], [0.86, 0.33], [-0.86, 0.97], [0.86, 0.97]]) addBox(group, 0.05, 0.7, 0.05, x, 0.35, z, graphite);

  // task chair (rolls back when the architect stands)
  const chair = new THREE.Group();
  chair.position.set(0, 0, -0.12);
  addBox(chair, 0.5, 0.075, 0.5, 0, 0.44, 0, fabric);
  addBox(chair, 0.46, 0.5, 0.06, 0, 0.7, -0.24, fabric);
  addCyl(chair, 0.03, 0.03, 0.4, 0, 0.22, 0, metal, false);
  addBox(chair, 0.42, 0.03, 0.06, 0, 0.05, 0, graphite);
  for (let index = 0; index < 5; index++) {
    const angle = (index / 5) * Math.PI * 2;
    const leg = addBox(chair, 0.3, 0.025, 0.045, Math.cos(angle) * 0.15, 0.045, Math.sin(angle) * 0.15, graphite, false);
    leg.rotation.y = -angle;
  }
  group.add(chair);

  // keyboard, mouse, mug
  const keyMat = new THREE.MeshStandardMaterial({color: 0x555a58, roughness: 0.8});
  addBox(group, 0.42, 0.018, 0.15, 0, 0.785, 0.4, graphite);
  for (let row = 0; row < 3; row++) for (let col = 0; col < 11; col++) addBox(group, 0.026, 0.006, 0.024, -0.18 + col * 0.036, 0.797, 0.36 + row * 0.036, keyMat, false);
  addBox(group, 0.05, 0.025, 0.08, 0.6, 0.79, 0.53, graphite, false);
  addCyl(group, 0.04, 0.035, 0.09, 0.66, 0.82, 0.86, new THREE.MeshStandardMaterial({color: 0xd8d2c4, roughness: 0.6}), false);

  // monitor turned toward the architect
  const screen = new THREE.Group();
  screen.position.set(-0.68, 1.18, 0.78);
  screen.rotation.y = 0.62;
  addBox(screen, 0.56, 0.34, 0.022, 0, 0, 0, graphite);
  const display = new THREE.Mesh(new THREE.PlaneGeometry(0.53, 0.31), screenMat);
  display.position.set(0, 0, 0.013);
  screen.add(display);
  addBox(screen, 0.06, 0.26, 0.05, 0, -0.29, -0.03, graphite);
  addBox(screen, 0.24, 0.02, 0.16, 0, -0.43, 0.02, graphite);
  group.add(screen);
  const deskLamp = new THREE.Group();
  deskLamp.position.set(-0.82, 0.78, 0.9);
  addCyl(deskLamp, 0.07, 0.08, 0.02, 0, 0, 0, graphite);
  const arm = addCyl(deskLamp, 0.012, 0.012, 0.45, 0, 0.22, 0.05, metal, false);
  arm.rotation.x = 0.2;
  addCyl(deskLamp, 0.09, 0.03, 0.07, 0, 0.44, 0.1, accent, false);
  group.add(deskLamp);

  // rolled drawing tube
  const roll = new THREE.Mesh(geo(0.045, 0.045, 0.6, 12), paperMat);
  roll.rotation.z = Math.PI / 2;
  roll.position.set(0.55, 0.79, 0.94);
  roll.castShadow = true;
  group.add(roll);

  // flip sheet on a hinge
  const paperPivot = new THREE.Group();
  paperPivot.position.set(0.28, 0.775, 0.34);
  const sheet = new THREE.Mesh(new THREE.BoxGeometry(0.36, 0.004, 0.3), new THREE.MeshStandardMaterial({map: drawingTexture('desk-plan', {title: '三层平面 · 方案 C'}), roughness: 0.92, side: THREE.DoubleSide}));
  sheet.position.set(0.18, 0, 0);
  sheet.castShadow = false;
  paperPivot.add(sheet);
  group.add(paperPivot);

  // massing model on the desk corner
  const model = new THREE.Group();
  model.position.set(0.72, 0.78, 0.86);
  addBox(model, 0.4, 0.055, 0.34, 0, 0.027, 0, woodDark);
  const white = new THREE.MeshStandardMaterial({color: 0xe6e1d4, roughness: 0.72});
  addBox(model, 0.22, 0.13, 0.16, -0.04, 0.12, 0, white);
  addBox(model, 0.13, 0.09, 0.18, 0.09, 0.1, 0.02, white);
  for (let i = 0; i < 3; i++) addBox(model, 0.2, 0.012, 0.14, -0.04, 0.065 + i * 0.04, 0, new THREE.MeshStandardMaterial({color: 0xbcb5a6, roughness: 0.8}), false);
  group.add(model);

  return {group, paperPivot, chair};
}

/* ------------------------------ main actor ------------------------------ */

export type WalkPoint = [number, number];

export const MAIN_LOOP = 48;
export const MAIN_SEGMENTS = {
  type: [0, 9],
  flip: [9, 12.2],
  inspect: [12.2, 14.6],
  stand: [14.6, 15.8],
  turnEast: [15.8, 16.6],
  walkOut: [16.6, 25.2],
  stopTurn: [25.2, 26.2],
  board: [26.2, 28.6],
  turnWest: [28.6, 29.4],
  walkBack: [29.4, 38],
  turnSit: [38, 38.8],
  sit: [38.8, 40],
  settle: [40, 48],
} as const;

export const MAIN_ACTIVITY: Array<[number, number, string]> = [
  [0, 9, '在工位绘图与录入'],
  [9, 12.2, '翻阅桌上的方案图纸'],
  [12.2, 14.6, '查看桌上的实体模型'],
  [14.6, 15.8, '起身'],
  [15.8, 16.6, '转身面向过道'],
  [16.6, 25.2, '沿过道走向院区索引'],
  [25.2, 26.2, '停步转向索引牌'],
  [26.2, 28.6, '查看院区索引'],
  [28.6, 29.4, '转身返回'],
  [29.4, 38, '沿过道返回工位'],
  [38, 38.8, '转身入座'],
  [38.8, 40, '坐下'],
  [40, 48, '在工位绘图与录入'],
];

export function mainActivity(time: number) {
  const t = ((time % MAIN_LOOP) + MAIN_LOOP) % MAIN_LOOP;
  return MAIN_ACTIVITY.find(([start, end]) => t >= start && t < end)?.[2] ?? '在工位绘图与录入';
}

export function paperFlip(time: number) {
  const t = ((time % MAIN_LOOP) + MAIN_LOOP) % MAIN_LOOP;
  if (t < 9.2 || t > 11.9) return 0;
  const u = (t - 9.2) / 2.7;
  return Math.sin(Math.PI * Math.min(1, Math.max(0, u))) * 0.25;
}

const CYCLE_DISTANCE = 1.1;
const STRIDE = 0.68;

type SampledPath = {x: number; z: number; tx: number; tz: number};

function measure(points: WalkPoint[]) {
  const lengths: number[] = [];
  let total = 0;
  for (let i = 1; i < points.length; i++) {
    const length = Math.hypot(points[i][0] - points[i - 1][0], points[i][1] - points[i - 1][1]);
    lengths.push(length);
    total += length;
  }
  return {lengths, total};
}

function sample(points: WalkPoint[], lengths: number[], distance: number): SampledPath {
  let remaining = Math.max(0, distance);
  for (let i = 1; i < points.length; i++) {
    const length = lengths[i - 1];
    if (remaining <= length || i === points.length - 1) {
      const u = length ? THREE.MathUtils.clamp(remaining / length, 0, 1) : 0;
      const x = lerp(points[i - 1][0], points[i][0], u);
      const z = lerp(points[i - 1][1], points[i][1], u);
      const tx = (points[i][0] - points[i - 1][0]) / (length || 1);
      const tz = (points[i][1] - points[i - 1][1]) / (length || 1);
      return {x, z, tx, tz};
    }
    remaining -= length;
  }
  const last = points[points.length - 1];
  return {x: last[0], z: last[1], tx: 0, tz: 1};
}

function gaitFoot(phase: number, lift = 0.055): {z: number; y: number; pitch: number} {
  const p = ((phase % 1) + 1) % 1;
  if (p < 0.62) {
    const u = p / 0.62;
    const pitch = lerp(-0.11, 0.26, ease(u));
    return {z: STRIDE / 2 - u * STRIDE, y: ANKLE + Math.max(0, Math.sin(pitch)) * 0.085, pitch};
  }
  const u = (p - 0.62) / 0.38;
  const swing = ease(u);
  return {
    z: -STRIDE / 2 + swing * STRIDE,
    y: ANKLE + Math.sin(Math.PI * u) * lift,
    pitch: lerp(0.26, -0.11, swing),
  };
}

const ANKLE = 0.075;

function standingPose(time: number, lookAt?: [number, number, number]): Pose {
  const breathe = Math.sin(time * 1.35) * 0.008;
  return {
    hipY: 0.947 + breathe,
    spine: [0.03, 0, 0],
    chest: [0.015, 0, 0],
    neck: [0, 0],
    head: [0, 0],
    feet: [
      {x: -0.11, y: ANKLE, z: 0.26, yaw: 0.06},
      {x: 0.11, y: ANKLE, z: 0.26, yaw: -0.06},
    ],
    hands: [
      {x: -0.225, y: 0.79, z: 0.02, frame: 'side', curl: 0.3},
      {x: 0.225, y: 0.79, z: 0.02, frame: 'side', curl: 0.3},
    ],
    headTarget: lookAt,
    blink: blinkAt(time, 3),
  };
}

function seatedPose(time: number, mode: 'type' | 'flip' | 'inspect'): Pose {
  const typing = mode === 'type';
  const tap = typing ? Math.sin(time * 12.5) * 0.22 : 0;
  const bob = typing ? Math.sin(time * 2.1) * 0.004 : 0;
  const headTarget: [number, number, number] = mode === 'inspect' ? [0.74, 1.0, 0.9] : [0.16, 0.78, 0.72];
  return {
    hipY: 0.572 + bob,
    spine: [typing ? 0.09 : mode === 'inspect' ? 0.05 : 0.11, mode === 'inspect' ? -0.12 : 0, mode === 'inspect' ? -0.03 : 0],
    chest: [typing ? 0.05 : 0.05, mode === 'inspect' ? 0.16 : 0, mode === 'inspect' ? 0.05 : 0],
    neck: [0, 0],
    head: [0, 0],
    feet: [
      {x: -0.12, y: ANKLE, z: 0.33, yaw: 0.08},
      {x: 0.12, y: ANKLE, z: 0.33, yaw: -0.08},
    ],
    hands: mode === 'flip' ? [
      {x: -0.15, y: 0.83, z: 0.28, frame: 'down', curl: 0.5, tap: 0},
      (() => {
        const lift = paperFlip(time);
        const weight = Math.min(1, lift / 0.2);
        const edgeX = 0.28 + 0.36 * Math.cos(lift * Math.PI);
        const edgeY = 0.795 + 0.36 * Math.sin(lift * Math.PI);
        return {x: 0.14 + (edgeX - 0.14) * weight, y: 0.84 + (edgeY - 0.84) * weight, z: 0.35, frame: 'down' as const, curl: 0.18, tap: 0};
      })(),
    ] : [
      {x: -0.14, y: 0.838 + (typing ? Math.sin(time * 7.1) * 0.003 : 0), z: 0.29, frame: 'down', curl: 0.35, tap},
      {x: 0.14, y: 0.838 + (typing ? Math.sin(time * 6.2 + 1) * 0.003 : 0), z: 0.29, frame: 'down', curl: 0.35, tap: -tap},
    ],
    headTarget,
    blink: blinkAt(time, 3),
  };
}

function walkPose(phase: number, time: number): Pose {
  const left = gaitFoot(phase);
  const right = gaitFoot(phase + 0.5);
  const bob = Math.cos(phase * Math.PI * 4) * 0.012;
  const swing = Math.sin(phase * Math.PI * 2);
  return {
    hipY: 0.955 + bob,
    hipX: Math.sin(phase * Math.PI * 2) * 0.014,
    spine: [0.075, Math.sin(phase * Math.PI * 2) * -0.055, 0],
    chest: [0.035, Math.sin(phase * Math.PI * 2) * 0.07, 0],
    feet: [
      {x: -0.105, y: left.y, z: left.z, pitch: left.pitch, yaw: 0.05},
      {x: 0.105, y: right.y, z: right.z, pitch: right.pitch, yaw: -0.05},
    ],
    hands: [
      {x: -0.215, y: 0.81 - Math.abs(swing) * 0.01, z: -swing * 0.17 - 0.02, frame: 'side', curl: 0.34},
      {x: 0.215, y: 0.81 - Math.abs(swing) * 0.01, z: swing * 0.17 - 0.02, frame: 'side', curl: 0.34},
    ],
    headTarget: [0.1, 1.55, 6],
    blink: blinkAt(time, 3),
  };
}

export type MainActor = {
  person: Person;
  update: (time: number) => void;
  activity: (time: number) => string;
  pose: (time: number) => Pose;
  snapshot: () => Record<string, unknown>;
};

export function createMainActor(options: {
  station: WalkPoint;
  path: WalkPoint[];
  paper: THREE.Object3D;
  chair?: THREE.Object3D;
  speed?: number;
}): MainActor {
  const person = buildPerson({
    skin: 0xd3a483,
    shirt: 0xdcd6c6,
    trouser: 0x3a4042,
    shoe: 0x2b2622,
    hair: 0x2a211c,
    hairstyle: 'short',
    shirtStyle: 'shirt',
    badge: true,
  });
  const path = options.path;
  const {lengths, total} = measure(path);
  const station = options.station;
  type Frame = {pose: Pose; x: number; z: number; yaw: number};
  const frameAt = (time: number): Frame => {
    const t = ((time % MAIN_LOOP) + MAIN_LOOP) % MAIN_LOOP;
    const {flip, inspect, stand, turnEast, walkOut, stopTurn, board, turnWest, walkBack, turnSit, sit} = MAIN_SEGMENTS;
    const here = (pose: Pose, yaw = 0): Frame => ({pose, x: station[0], z: station[1], yaw});
    if (t < flip[0]) return here(seatedPose(t, 'type'));
    if (t < inspect[0]) return here(seatedPose(t, 'flip'));
    if (t < stand[0]) return here(seatedPose(t, 'inspect'));
    if (t < turnEast[0]) {
      const u = (t - stand[0]) / (stand[1] - stand[0]);
      return here(mixPose(seatedPose(t, 'inspect'), standingPose(t), u));
    }
    if (t < walkOut[0]) {
      const u = (t - turnEast[0]) / (turnEast[1] - turnEast[0]);
      return here(standingPose(t), lerp(0, Math.PI / 2, ease(u)));
    }
    if (t < walkOut[1]) {
      const u = (t - walkOut[0]) / (walkOut[1] - walkOut[0]);
      const distance = total * ease(u);
      const point = sample(path, lengths, distance);
      const before = sample(path, lengths, Math.max(0, distance - 0.4));
      const after = sample(path, lengths, Math.min(total, distance + 0.4));
      const yaw = Math.atan2(after.x - before.x, after.z - before.z);
      return {pose: walkPose(distance / CYCLE_DISTANCE, t), x: point.x, z: point.z, yaw};
    }
    if (t < stopTurn[1]) {
      const point = sample(path, lengths, total);
      const u = ease((t - stopTurn[0]) / (stopTurn[1] - stopTurn[0]));
      return {pose: standingPose(t, [0.1, 1.55, 1.8]), x: point.x, z: point.z, yaw: lerp(Math.PI / 2, Math.PI, u)};
    }
    if (t < board[1]) {
      const point = sample(path, lengths, total);
      const settle = Math.sin((t - board[0]) * 1.2) * 0.02;
      return {pose: {...standingPose(t, [0, 1.58, 1.9]), spine: [0.02, 0, 0]}, x: point.x, z: point.z, yaw: Math.PI + settle};
    }
    if (t < turnWest[0] + 0.8) {
      const point = sample(path, lengths, total);
      const u = ease((t - turnWest[0]) / 0.8);
      return {pose: standingPose(t), x: point.x, z: point.z, yaw: lerp(Math.PI, Math.PI * 1.5, u)};
    }
    if (t < walkBack[1]) {
      const u = (t - walkBack[0]) / (walkBack[1] - walkBack[0]);
      const distance = total * (1 - ease(u));
      const point = sample(path, lengths, distance);
      const before = sample(path, lengths, Math.min(total, distance + 0.4));
      const after = sample(path, lengths, Math.max(0, distance - 0.4));
      const yaw = Math.atan2(after.x - before.x, after.z - before.z);
      return {pose: walkPose(distance / CYCLE_DISTANCE, t), x: point.x, z: point.z, yaw};
    }
    if (t < turnSit[1]) {
      const u = ease((t - turnSit[0]) / (turnSit[1] - turnSit[0]));
      return here(standingPose(t), lerp(Math.PI * 1.5, Math.PI * 2, u));
    }
    if (t < sit[1]) {
      const u = (t - sit[0]) / (sit[1] - sit[0]);
      return here(mixPose(standingPose(t), seatedPose(t, 'type'), u), Math.PI * 2);
    }
    return here(seatedPose(t, 'type'), Math.PI * 2);
  };

  let poseTime = 0;
  const update = (time: number) => {
    poseTime = time;
    const frame = frameAt(time);
    person.root.position.set(frame.x, 0, frame.z);
    person.root.rotation.y = frame.yaw;
    applyPose(person, frame.pose);
    const flip = paperFlip(time);
    options.paper.rotation.z = flip * Math.PI;
    options.paper.rotation.x = 0;
    if (options.chair) {
      const phase = ((time % MAIN_LOOP) + MAIN_LOOP) % MAIN_LOOP;
      let roll = 0;
      if (phase > MAIN_SEGMENTS.stand[0] && phase < MAIN_SEGMENTS.stand[1]) roll = ease((phase - MAIN_SEGMENTS.stand[0]) / (MAIN_SEGMENTS.stand[1] - MAIN_SEGMENTS.stand[0]));
      else if (phase >= MAIN_SEGMENTS.stand[1] && phase < MAIN_SEGMENTS.sit[0]) roll = 1;
      else if (phase >= MAIN_SEGMENTS.sit[0] && phase < MAIN_SEGMENTS.sit[1]) roll = 1 - ease((phase - MAIN_SEGMENTS.sit[0]) / (MAIN_SEGMENTS.sit[1] - MAIN_SEGMENTS.sit[0]));
      options.chair.position.z = -0.12 - 0.38 * roll;
    }
  };

  const snapshot = () => {
    const footL = new THREE.Vector3();
    const footR = new THREE.Vector3();
    person.rig.feet[0].getWorldPosition(footL);
    person.rig.feet[1].getWorldPosition(footR);
    const head = new THREE.Vector3();
    person.rig.head.getWorldPosition(head);
    return {
      root: [person.root.position.x, person.root.position.y, person.root.position.z],
      yaw: person.root.rotation.y,
      footL: footL.toArray(),
      footR: footR.toArray(),
      head: head.toArray(),
      activity: mainActivity(poseTime),
    };
  };

  return {
    person,
    update,
    activity: mainActivity,
    pose: time => frameAt(time).pose,
    snapshot,
  };
}

export function createResident(options: {
  palette: {skin: number; shirt: number; trouser: number; shoe: number; hair: number; hairstyle?: 'short' | 'bun' | 'bob' | 'crop'; glasses?: boolean; shirtStyle?: 'shirt' | 'sweater' | 'blazer'};
  position: [number, number, number];
  yaw: number;
  pose: (time: number) => Pose;
}): {person: Person; update: (time: number) => void} {
  const person = buildPerson(options.palette);
  person.root.position.set(...options.position);
  person.root.rotation.y = options.yaw;
  return {
    person,
    update: (time: number) => applyPose(person, options.pose(time)),
  };
}

/* reusable room props ------------------------------------------------ */

const vase = new THREE.MeshStandardMaterial({color: 0xd7cdb9, roughness: 0.85});
const leaf = new THREE.MeshStandardMaterial({color: 0x62745a, roughness: 0.9});
const stone = new THREE.MeshStandardMaterial({color: 0xcfc7b8, roughness: 0.95});

export function plant(parent: THREE.Object3D, x: number, z: number, size = 1) {
  const pot = addCyl(parent, 0.16 * size, 0.2 * size, 0.3 * size, x, 0.15 * size, z, vase);
  pot.castShadow = true;
  addCyl(parent, 0.035 * size, 0.05 * size, 0.7 * size, x, 0.6 * size, z, woodDark);
  const leaves: [number, number, number, number][] = [[0, 0.95, 0, 0.26], [0.18, 0.85, 0.07, 0.2], [-0.16, 0.88, -0.06, 0.22], [0.06, 1.05, -0.14, 0.19], [-0.1, 1.02, 0.15, 0.18]];
  for (const [dx, y, dz, radius] of leaves) {
    const blob = new THREE.Mesh(sphereGeo(radius * size, 10, 8), leaf);
    blob.position.set(x + dx * size, y * size, z + dz * size);
    blob.scale.set(1, 0.8, 1);
    blob.castShadow = true;
    parent.add(blob);
  }
}

export function buildBreakoutTable(parent: THREE.Object3D, x: number, z: number, yaw = 0) {
  const group = new THREE.Group();
  group.position.set(x, 0, z);
  group.rotation.y = yaw;
  addBox(group, 1.1, 0.05, 0.8, 0, 0.74, 0, wood);
  for (const [dx, dz] of [[-0.45, -0.3], [0.45, -0.3], [-0.45, 0.3], [0.45, 0.3]]) addCyl(group, 0.028, 0.028, 0.72, dx, 0.36, dz, graphite);
  for (const [dx, dz, rot] of [[-0.45, 0.75, 0], [0.45, 0.75, 0], [-0.45, -0.75, Math.PI], [0.45, -0.75, Math.PI]]) {
    const chair = new THREE.Group();
    chair.position.set(dx, 0, dz);
    chair.rotation.y = rot;
    addBox(chair, 0.44, 0.05, 0.44, 0, 0.44, 0, fabric);
    addBox(chair, 0.44, 0.42, 0.04, 0, 0.66, -0.2, fabric);
    for (const [cx, cz] of [[-0.18, -0.18], [0.18, -0.18], [-0.18, 0.18], [0.18, 0.18]]) addCyl(chair, 0.02, 0.02, 0.44, cx, 0.22, cz, graphite);
    group.add(chair);
  }
  addCyl(group, 0.05, 0.06, 0.24, 0.2, 0.86, 0.1, woodDark);
  addBox(group, 0.3, 0.02, 0.22, 0.2, 0.99, 0.1, paperMat);
  parent.add(group);
  return group;
}

export function floorPatch(parent: THREE.Object3D, x: number, z: number, w: number, d: number, color: number) {
  const pad = new THREE.Mesh(new THREE.PlaneGeometry(w, d), new THREE.MeshStandardMaterial({color, roughness: 0.96}));
  pad.rotation.x = -Math.PI / 2;
  pad.position.set(x, 0.004, z);
  pad.receiveShadow = true;
  parent.add(pad);
  return pad;
}

export {ANKLE};
