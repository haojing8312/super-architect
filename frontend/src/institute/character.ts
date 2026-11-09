import * as THREE from 'three';
import {geo, sphereGeo, hairCapGeo, boxGeo} from './textures';

/* ------------------------------------------------------------------ *
 * Original, fully procedural studio staff. Every joint is an Object3D
 * so the figure stays editable and can be re-posed without new assets.
 * ------------------------------------------------------------------ */

const DOWN = new THREE.Vector3(0, -1, 0);
const THIGH = 0.44;
const SHIN = 0.44;
const UPPER_ARM = 0.30;
const FOREARM = 0.26;
const ANKLE_Y = 0.075;

export type PersonOptions = {
  skin: number;
  shirt: number;
  trouser: number;
  shoe: number;
  hair: number;
  hairstyle?: 'short' | 'bun' | 'bob' | 'crop';
  glasses?: boolean;
  shirtStyle?: 'shirt' | 'sweater' | 'blazer';
  badge?: boolean;
};

export type PersonRig = {
  root: THREE.Group;
  hips: THREE.Group;
  spine: THREE.Group;
  chest: THREE.Group;
  neck: THREE.Group;
  head: THREE.Group;
  shoulders: THREE.Group[];
  upperArms: THREE.Group[];
  forearms: THREE.Group[];
  hands: THREE.Group[];
  fingerBases: THREE.Group[][];
  thighs: THREE.Group[];
  shins: THREE.Group[];
  feet: THREE.Group[];
  eyes: THREE.Object3D[];
  brows: THREE.Object3D[];
};

export type Person = {root: THREE.Group; rig: PersonRig; materials: THREE.Material[]};

export type HandFrame = 'down' | 'side' | 'board' | 'relaxed';
export type HandTarget = {
  x: number; y: number; z: number;
  frame?: HandFrame; yaw?: number; pitch?: number; roll?: number;
  curl?: number; tap?: number;
};
export type FootTarget = {x: number; y: number; z: number; pitch?: number; yaw?: number};
export type Pose = {
  hipY?: number; hipX?: number; hipZ?: number;
  spine?: [number, number, number];
  chest?: [number, number, number];
  neck?: [number, number];
  head?: [number, number];
  feet?: [FootTarget, FootTarget];
  hands?: [HandTarget, HandTarget];
  fingers?: [number, number];
  headTarget?: [number, number, number];
  blink?: number;
};

const FRAME_DOWN = new THREE.Quaternion().setFromRotationMatrix(new THREE.Matrix4().makeBasis(
  new THREE.Vector3(0, -1, 0), new THREE.Vector3(0, 0, -1), new THREE.Vector3(1, 0, 0),
));
const FRAME_BOARD = new THREE.Quaternion().setFromRotationMatrix(new THREE.Matrix4().makeBasis(
  new THREE.Vector3(0, 0, 1), new THREE.Vector3(0, -1, 0), new THREE.Vector3(1, 0, 0),
));

function mesh(parent: THREE.Object3D, geometry: THREE.BufferGeometry, material: THREE.Material, x: number, y: number, z: number, sx = 1, sy = 1, sz = 1, shadow = true) {
  const item = new THREE.Mesh(geometry, material);
  item.position.set(x, y, z);
  item.scale.set(sx, sy, sz);
  item.castShadow = shadow;
  parent.add(item);
  return item;
}

function group(parent: THREE.Object3D, x: number, y: number, z: number) {
  const item = new THREE.Group();
  item.position.set(x, y, z);
  parent.add(item);
  return item;
}

function quatFromDown(direction: THREE.Vector3) {
  const d = direction.clone().normalize();
  if (d.y < -0.99999) return new THREE.Quaternion();
  return new THREE.Quaternion().setFromUnitVectors(DOWN, d);
}

function aim(bone: THREE.Object3D, direction: THREE.Vector3) {
  const parentWorld = new THREE.Quaternion();
  bone.parent!.getWorldQuaternion(parentWorld);
  bone.quaternion.copy(parentWorld.invert().multiply(quatFromDown(direction)));
  bone.updateMatrixWorld(true);
}

function solveLimb(upper: THREE.Object3D, lower: THREE.Object3D, target: THREE.Vector3, polePoint: THREE.Vector3, lengthA: number, lengthB: number) {
  upper.updateWorldMatrix(true, false);
  const origin = new THREE.Vector3().setFromMatrixPosition(upper.matrixWorld);
  const delta = target.clone().sub(origin);
  let distance = delta.length();
  if (distance < 1e-4) { delta.set(0, -1, 0); distance = 1e-4; }
  const direction = delta.normalize();
  const reach = THREE.MathUtils.clamp(distance, Math.abs(lengthA - lengthB) + 1e-3, lengthA + lengthB - 1e-3);
  const along = (lengthA * lengthA - lengthB * lengthB + reach * reach) / (2 * reach);
  const height = Math.sqrt(Math.max(0, lengthA * lengthA - along * along));
  const pole = polePoint.clone().sub(origin);
  pole.sub(direction.clone().multiplyScalar(pole.dot(direction)));
  if (pole.lengthSq() < 1e-6) pole.set(0, 1, 0).add(direction.clone().multiplyScalar(0.3));
  pole.normalize();
  const joint = origin.clone().add(direction.clone().multiplyScalar(along)).add(pole.clone().multiplyScalar(height));
  aim(upper, joint.clone().sub(origin));
  lower.updateWorldMatrix(true, false);
  const jointWorld = new THREE.Vector3().setFromMatrixPosition(lower.matrixWorld);
  aim(lower, target.clone().sub(jointWorld));
}

function orient(bone: THREE.Object3D, frame: THREE.Quaternion, rootWorld: THREE.Quaternion, yaw: number, pitch: number, roll: number) {
  const parentWorld = new THREE.Quaternion();
  bone.parent!.getWorldQuaternion(parentWorld);
  const local = new THREE.Quaternion().setFromEuler(new THREE.Euler(pitch, yaw, roll, 'YXZ'));
  const desired = rootWorld.clone().multiply(local).multiply(frame);
  bone.quaternion.copy(parentWorld.invert().multiply(desired));
  bone.updateMatrixWorld(true);
}

export function buildPerson(options: PersonOptions): Person {
  const skin = new THREE.MeshStandardMaterial({color: options.skin, roughness: 0.66, metalness: 0});
  const shirt = new THREE.MeshStandardMaterial({color: options.shirt, roughness: 0.85, metalness: 0});
  const shirtDark = new THREE.MeshStandardMaterial({color: new THREE.Color(options.shirt).multiplyScalar(0.78), roughness: 0.88});
  const trouser = new THREE.MeshStandardMaterial({color: options.trouser, roughness: 0.86, metalness: 0});
  const shoe = new THREE.MeshStandardMaterial({color: options.shoe, roughness: 0.55, metalness: 0.05});
  const hair = new THREE.MeshStandardMaterial({color: options.hair, roughness: 0.58, metalness: 0.02});
  const dark = new THREE.MeshStandardMaterial({color: 0x262120, roughness: 0.7});
  const eyeWhite = new THREE.MeshStandardMaterial({color: 0xf4efe6, roughness: 0.35});
  const iris = new THREE.MeshStandardMaterial({color: 0x3a2a20, roughness: 0.4});
  const lip = new THREE.MeshStandardMaterial({color: 0x9a6258, roughness: 0.6});
  const metal = new THREE.MeshStandardMaterial({color: 0x8a8578, roughness: 0.35, metalness: 0.65});
  const skinShade = new THREE.MeshStandardMaterial({color: new THREE.Color(options.skin).multiplyScalar(0.82), roughness: 0.7});
  const materials = [skin, shirt, shirtDark, trouser, shoe, hair, dark, eyeWhite, iris, lip, metal, skinShade];

  const root = new THREE.Group();
  root.name = 'studio-person';
  const hips = group(root, 0, 0.96, 0);
  hips.name = 'hips';

  mesh(hips, geo(0.172, 0.148, 0.22), trouser, 0, -0.02, 0, 1, 1, 0.74);
  mesh(hips, sphereGeo(0.135), trouser, 0, -0.02, -0.055, 1.0, 0.72, 0.62);
  mesh(hips, geo(0.157, 0.157, 0.035), dark, 0, 0.075, 0, 1, 1, 0.74, false);

  const spine = group(hips, 0, 0.06, 0);
  mesh(spine, geo(0.146, 0.156, 0.21), shirt, 0, 0.06, 0, 1, 1, 0.74);

  const chest = group(spine, 0, 0.20, 0);
  mesh(chest, geo(0.178, 0.146, 0.33), shirt, 0, 0.10, 0, 1, 1, 0.7);
  // shoulder slope + collar
  mesh(chest, geo(0.05, 0.072, 0.1), shirt, 0.152, 0.235, 0, 1, 1, 0.8);
  mesh(chest, geo(0.05, 0.072, 0.1), shirt, -0.152, 0.235, 0, 1, 1, 0.8);
  mesh(chest, geo(0.068, 0.072, 0.035), shirtDark, 0, 0.292, 0.005, 1, 1, 0.8);
  // placket, back yoke and shirt hem
  mesh(chest, boxGeo(0.012, 0.27, 0.008), shirtDark, 0, 0.14, 0.125, 1, 1, 1, false);
  mesh(chest, boxGeo(0.25, 0.1, 0.01), shirtDark, 0, 0.2, -0.118, 1, 1, 1, false);
  mesh(spine, boxGeo(0.3, 0.018, 0.01), shirtDark, 0, 0.17, 0.108, 1, 1, 1, false);
  if (options.shirtStyle === 'blazer') {
    const blazer = new THREE.MeshStandardMaterial({color: new THREE.Color(options.shirt).multiplyScalar(0.62), roughness: 0.9});
    materials.push(blazer);
    mesh(chest, boxGeo(0.11, 0.3, 0.03), blazer, -0.09, 0.12, 0.09, 1, 1, 1);
    mesh(chest, boxGeo(0.11, 0.3, 0.03), blazer, 0.09, 0.12, 0.09, 1, 1, 1);
  }
  if (options.badge !== false) {
    mesh(chest, boxGeo(0.052, 0.07, 0.006), new THREE.MeshStandardMaterial({color: 0xd9d4c6, roughness: 0.7}), 0.085, 0.17, 0.115, 1, 1, 1, false);
    mesh(chest, boxGeo(0.036, 0.036, 0.004), new THREE.MeshStandardMaterial({color: 0x485a5c, roughness: 0.6}), 0.085, 0.176, 0.119, 1, 1, 1, false);
  }

  const neck = group(chest, 0, 0.30, 0);
  mesh(neck, geo(0.046, 0.052, 0.15), skin, 0, 0.075, 0);
  const head = group(neck, 0, 0.075, 0);
  const headParts = buildHead(head, {skin, skinShade, hair, dark, eyeWhite, iris, lip, metal}, options, materials);

  const shoulders: THREE.Group[] = [];
  const upperArms: THREE.Group[] = [];
  const forearms: THREE.Group[] = [];
  const hands: THREE.Group[] = [];
  const fingerBases: THREE.Group[][] = [];
  for (let side = 0; side < 2; side++) {
    const sign = side === 0 ? -1 : 1;
    const shoulder = group(chest, sign * 0.185, 0.242, 0);
    shoulders.push(shoulder);
    mesh(shoulder, sphereGeo(0.042), shirt, 0, -0.014, 0, 1, 1.0, 0.88);
    const upper = group(shoulder, 0, 0, 0);
    upperArms.push(upper);
    mesh(upper, geo(0.048, 0.04, UPPER_ARM), options.shirtStyle === 'sweater' ? shirt : shirt, 0, 0, 0);
    mesh(upper, sphereGeo(0.042), options.shirtStyle === 'shirt' ? shirt : shirt, 0, -UPPER_ARM, 0);
    const forearm = group(upper, 0, -UPPER_ARM, 0);
    forearms.push(forearm);
    mesh(forearm, geo(0.037, 0.028, FOREARM), skin, 0, 0, 0);
    if (options.shirtStyle === 'shirt') mesh(forearm, geo(0.04, 0.037, 0.09), shirt, 0, -0.045, 0, 1, 1, 1);
    const hand = group(forearm, 0, -FOREARM, 0);
    hands.push(hand);
    fingerBases.push(buildHand(hand, skin, sign));
  }

  const thighs: THREE.Group[] = [];
  const shins: THREE.Group[] = [];
  const feet: THREE.Group[] = [];
  for (let side = 0; side < 2; side++) {
    const sign = side === 0 ? -1 : 1;
    const thighJoint = group(hips, sign * 0.086, -0.03, 0);
    thighs.push(thighJoint);
    mesh(thighJoint, geo(0.086, 0.066, THIGH), trouser, 0, 0, 0);
    mesh(thighJoint, sphereGeo(0.062), trouser, 0, -THIGH, 0, 1, 0.9, 1);
    const shin = group(thighJoint, 0, -THIGH, 0);
    shins.push(shin);
    mesh(shin, geo(0.055, 0.037, SHIN), trouser, 0, 0, 0, 1, 1, 1);
    const foot = group(shin, 0, -SHIN, 0);
    feet.push(foot);
    mesh(foot, boxGeo(0.092, 0.05, 0.235), shoe, 0, -0.05, 0.045);
    mesh(foot, boxGeo(0.1, 0.018, 0.25), dark, 0, -0.073, 0.045);
    mesh(foot, sphereGeo(0.045), shoe, 0, -0.05, 0.155, 1.05, 0.8, 1.1);
  }

  return {
    root,
    rig: {root, hips, spine, chest, neck, head, shoulders, upperArms, forearms, hands, fingerBases, thighs, shins, feet, eyes: headParts.eyes, brows: headParts.brows},
    materials,
  };
}

function buildHead(parent: THREE.Group, mats: {skin: THREE.Material; skinShade: THREE.Material; hair: THREE.Material; dark: THREE.Material; eyeWhite: THREE.Material; iris: THREE.Material; lip: THREE.Material; metal: THREE.Material}, options: PersonOptions, collection: THREE.Material[]) {
  // A continuous ellipsoid avoids intersecting jaw spheres and dark seams.
  mesh(parent, sphereGeo(0.106, 32, 24), mats.skin, 0, 0.103, 0, 0.89, 1.18, 0.98);
  mesh(parent, sphereGeo(0.014), mats.skin, 0, 0.094, 0.099, 0.8, 1.0, 0.8);
  for (const sign of [-1, 1]) mesh(parent, sphereGeo(0.019), mats.skin, sign * 0.095, 0.101, 0.002, 0.36, 1.0, 0.72);

  const eyes: THREE.Object3D[] = [];
  const brows: THREE.Object3D[] = [];
  for (const sign of [-1, 1]) {
    const eye = group(parent, sign * 0.036, 0.114, 0.09);
    mesh(eye, sphereGeo(0.0165), mats.eyeWhite, 0, 0, 0, 1.05, 0.86, 0.42, false);
    mesh(eye, sphereGeo(0.0078), mats.iris, sign * 0.0012, 0, 0.0056, 1, 1, 0.42, false);
    mesh(eye, sphereGeo(0.0034), mats.dark, sign * 0.0012, 0, 0.008, 1, 1, 0.5, false);
    eyes.push(eye);
    const lash = mesh(parent, boxGeo(0.036, 0.0035, 0.006), mats.dark, sign * 0.036, 0.127, 0.0945, 1, 1, 1, false);
    lash.rotation.z = sign * -0.05;
    const brow = mesh(parent, boxGeo(0.04, 0.0055, 0.007), mats.hair, sign * 0.036, 0.1385, 0.0965, 1, 1, 1, false);
    brow.rotation.z = sign * -0.09;
    brows.push(brow);
  }
  mesh(parent, boxGeo(0.027, 0.003, 0.003), mats.lip, 0, 0.054, 0.098, 1, 1, 1, false);


  // hair: cap + back mass, keeping the face plane clear
  const cap = new THREE.Mesh(hairCapGeo(1), mats.hair);
  cap.castShadow = true;
  parent.add(cap);
  if (options.hairstyle === 'bun') {
    mesh(parent, sphereGeo(0.048), mats.hair, 0, 0.075, -0.118, 1.1, 1.05, 0.9);
    mesh(parent, sphereGeo(0.078), mats.hair, 0, 0.082, -0.052, 1.0, 0.86, 0.62);
  } else if (options.hairstyle === 'bob') {
    mesh(parent, sphereGeo(0.098), mats.hair, 0, 0.042, -0.052, 1.02, 1.18, 0.8);
    mesh(parent, boxGeo(0.042, 0.185, 0.062), mats.hair, 0.089, 0.01, 0.008, 1, 1, 1);
    mesh(parent, boxGeo(0.042, 0.185, 0.062), mats.hair, -0.089, 0.01, 0.008, 1, 1, 1);
  } else if (options.hairstyle === 'crop') {
    mesh(parent, sphereGeo(0.09), mats.hair, 0, 0.126, -0.02, 1.0, 0.78, 0.86);
  } else {
    mesh(parent, sphereGeo(0.096), mats.hair, 0, 0.1, -0.05, 1.0, 0.98, 0.72);
    mesh(parent, sphereGeo(0.045), mats.hair, 0.032, 0.18, 0.044, 1.15, 0.34, 0.72).rotation.z = -0.1;
    mesh(parent, sphereGeo(0.041), mats.hair, -0.035, 0.181, 0.04, 1.1, 0.3, 0.65).rotation.z = 0.06;
  }

  if (options.glasses) {
    const frame = new THREE.TorusGeometry(0.026, 0.0032, 5, 18);
    collection.push(mats.metal);
    for (const sign of [-1, 1]) {
      const rim = new THREE.Mesh(frame, mats.metal);
      rim.position.set(sign * 0.037, 0.114, 0.095);
      rim.castShadow = false;
      parent.add(rim);
      const arm = new THREE.Mesh(boxGeo(0.004, 0.004, 0.055), mats.metal);
      arm.position.set(sign * 0.064, 0.117, 0.065);
      parent.add(arm);
    }
    const bridge = new THREE.Mesh(boxGeo(0.022, 0.004, 0.004), mats.metal);
    bridge.position.set(0, 0.117, 0.096);
    parent.add(bridge);
  }

  return {eyes, brows};
}

function buildHand(parent: THREE.Group, skin: THREE.Material, sign: number) {
  mesh(parent, sphereGeo(0.034), skin, 0, -0.042, 0, 1.05, 1.25, 0.62);
  const bases: THREE.Group[] = [];
  const knuckleZ = [0.022, 0.007, -0.008, -0.022];
  for (let i = 0; i < 4; i++) {
    const base = group(parent, (i - 1.5) * 0.017, -0.083, knuckleZ[i] * -0.4);
    bases.push(base);
    const first = group(base, 0, 0, 0);
    mesh(first, geo(0.0068, 0.006, 0.031, 6), skin, 0, 0, 0);
    const second = group(first, 0, -0.031, 0);
    mesh(second, geo(0.006, 0.0054, 0.027, 6), skin, 0, 0, 0);
    second.rotation.z = 0.18;
  }
  const thumbBase = group(parent, sign * 0.028, -0.035, 0.012);
  thumbBase.rotation.set(0.2, 0, -sign * 0.9);
  mesh(thumbBase, geo(0.008, 0.007, 0.03, 6), skin, 0, 0, 0);
  const thumbTip = group(thumbBase, 0, -0.03, 0);
  mesh(thumbTip, geo(0.007, 0.006, 0.024, 6), skin, 0, 0, 0);
  return bases;
}

/* ------------------------------- posing ------------------------------- */

const tmpWorld = new THREE.Vector3();
const tmpPole = new THREE.Vector3();

export function applyPose(person: Person, pose: Pose) {
  const {rig} = person;
  const root = rig.root;
  root.updateMatrixWorld(true);
  const rootWorld = new THREE.Quaternion();
  root.getWorldQuaternion(rootWorld);

  rig.hips.position.set(pose.hipX ?? 0, pose.hipY ?? 0.96, pose.hipZ ?? 0);
  const spine = pose.spine ?? [0.02, 0, 0];
  const chest = pose.chest ?? [0.01, 0, 0];
  rig.spine.rotation.set(spine[0], spine[1], spine[2]);
  rig.chest.rotation.set(chest[0], chest[1], chest[2]);
  root.updateMatrixWorld(true);

  let neckYaw = pose.neck?.[0] ?? 0;
  let neckPitch = pose.neck?.[1] ?? 0;
  let headYaw = pose.head?.[0] ?? 0;
  let headPitch = pose.head?.[1] ?? 0;
  if (pose.headTarget) {
    const local = new THREE.Vector3(pose.headTarget[0], pose.headTarget[1], pose.headTarget[2]);
    const headWorld = new THREE.Vector3().setFromMatrixPosition(rig.head.matrixWorld);
    const origin = root.worldToLocal(headWorld.clone());
    const dx = local.x - origin.x;
    const dz = local.z - origin.z;
    const dy = local.y - origin.y;
    const yaw = THREE.MathUtils.clamp(Math.atan2(dx, Math.max(0.08, dz)), -1.15, 1.15);
    const pitch = THREE.MathUtils.clamp(-Math.atan2(dy, Math.max(0.12, Math.hypot(dx, dz))), -0.5, 0.55);
    neckYaw = yaw * 0.4;
    headYaw = yaw * 0.6;
    neckPitch = pitch * 0.35;
    headPitch = pitch * 0.65;
  }
  rig.neck.rotation.set(neckPitch, neckYaw, 0);
  rig.head.rotation.set(headPitch, headYaw, 0);

  const blink = pose.blink ?? 0;
  for (const eye of rig.eyes) eye.scale.y = 1 - 0.9 * blink;

  root.updateMatrixWorld(true);

  const feet = pose.feet;
  if (feet) {
    for (let side = 0; side < 2; side++) {
      const target = feet[side];
      tmpWorld.set(target.x, target.y, target.z);
      root.localToWorld(tmpWorld);
      tmpPole.set(target.x + (side === 0 ? -0.12 : 0.12), target.y + 0.62, target.z + 0.72);
      root.localToWorld(tmpPole);
      solveLimb(rig.thighs[side], rig.shins[side], tmpWorld, tmpPole, THIGH, SHIN);
      orient(rig.feet[side], new THREE.Quaternion(), rootWorld, target.yaw ?? (side === 0 ? 0.05 : -0.05), target.pitch ?? 0, 0);
    }
  }

  const hands = pose.hands;
  if (hands) {
    for (let side = 0; side < 2; side++) {
      const target = hands[side];
      tmpWorld.set(target.x, target.y, target.z);
      root.localToWorld(tmpWorld);
      tmpPole.set((side === 0 ? -1 : 1) * 0.3, Math.max(0.72, target.y - 0.2), 0.06);
      root.localToWorld(tmpPole);
      solveLimb(rig.upperArms[side], rig.forearms[side], tmpWorld, tmpPole, UPPER_ARM, FOREARM);
      const frame = target.frame === 'board' ? FRAME_BOARD : target.frame === 'side' || target.frame === 'relaxed' ? new THREE.Quaternion() : FRAME_DOWN;
      orient(rig.hands[side], frame, rootWorld, target.yaw ?? 0, target.pitch ?? 0, target.roll ?? 0);
    }
  }

  const curls = pose.fingers ?? [pose.hands?.[0].curl ?? 0.32, pose.hands?.[1].curl ?? 0.32];
  for (let side = 0; side < 2; side++) {
    const tap = hands?.[side].tap ?? 0;
    for (let i = 0; i < rig.fingerBases[side].length; i++) {
      const base = rig.fingerBases[side][i];
      base.rotation.z = curls[side] + (i === 1 || i === 2 ? tap : tap * 0.4);
    }
  }
}

/* ------------------------------ blinking ------------------------------ */

export function blinkAt(time: number, seed: number) {
  const cycle = 4.2 + (seed % 5) * 0.43;
  const within = (time / cycle + seed * 0.37) % 1;
  if (within > 0.965 || within < 0.006) return 1;
  if (within > 0.94) return (within - 0.94) / 0.025;
  if (within < 0.02) return 1 - within / 0.02;
  return 0;
}

export function ease(value: number) {
  const t = THREE.MathUtils.clamp(value, 0, 1);
  return t * t * (3 - 2 * t);
}

export function lerp(a: number, b: number, t: number) {
  return a + (b - a) * t;
}

export function mixPose(a: Pose, b: Pose, t: number): Pose {
  const u = ease(t);
  const mixFoot = (x?: FootTarget, y?: FootTarget): FootTarget => ({
    x: lerp(x?.x ?? 0, y?.x ?? 0, u),
    y: lerp(x?.y ?? ANKLE_Y, y?.y ?? ANKLE_Y, u),
    z: lerp(x?.z ?? 0, y?.z ?? 0, u),
    pitch: lerp(x?.pitch ?? 0, y?.pitch ?? 0, u),
    yaw: lerp(x?.yaw ?? 0, y?.yaw ?? 0, u),
  });
  const mixHand = (x?: HandTarget, y?: HandTarget): HandTarget => ({
    x: lerp(x?.x ?? 0, y?.x ?? 0, u),
    y: lerp(x?.y ?? 0.8, y?.y ?? 0.8, u),
    z: lerp(x?.z ?? 0, y?.z ?? 0, u),
    frame: u > 0.5 ? y?.frame : x?.frame,
    yaw: lerp(x?.yaw ?? 0, y?.yaw ?? 0, u),
    pitch: lerp(x?.pitch ?? 0, y?.pitch ?? 0, u),
    curl: lerp(x?.curl ?? 0.3, y?.curl ?? 0.3, u),
    tap: lerp(x?.tap ?? 0, y?.tap ?? 0, u),
  });
  return {
    hipY: lerp(a.hipY ?? 0.96, b.hipY ?? 0.96, u),
    hipZ: lerp(a.hipZ ?? 0, b.hipZ ?? 0, u),
    hipX: lerp(a.hipX ?? 0, b.hipX ?? 0, u),
    spine: [lerp(a.spine?.[0] ?? 0, b.spine?.[0] ?? 0, u), lerp(a.spine?.[1] ?? 0, b.spine?.[1] ?? 0, u), lerp(a.spine?.[2] ?? 0, b.spine?.[2] ?? 0, u)],
    chest: [lerp(a.chest?.[0] ?? 0, b.chest?.[0] ?? 0, u), lerp(a.chest?.[1] ?? 0, b.chest?.[1] ?? 0, u), lerp(a.chest?.[2] ?? 0, b.chest?.[2] ?? 0, u)],
    neck: [lerp(a.neck?.[0] ?? 0, b.neck?.[0] ?? 0, u), lerp(a.neck?.[1] ?? 0, b.neck?.[1] ?? 0, u)],
    head: [lerp(a.head?.[0] ?? 0, b.head?.[0] ?? 0, u), lerp(a.head?.[1] ?? 0, b.head?.[1] ?? 0, u)],
    feet: [mixFoot(a.feet?.[0], b.feet?.[0]), mixFoot(a.feet?.[1], b.feet?.[1])],
    hands: [mixHand(a.hands?.[0], b.hands?.[0]), mixHand(a.hands?.[1], b.hands?.[1])],
    headTarget: b.headTarget ?? a.headTarget,
    blink: lerp(a.blink ?? 0, b.blink ?? 0, u),
  };
}
