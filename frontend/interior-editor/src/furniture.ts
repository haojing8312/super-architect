import * as THREE from 'three';
import type {Vec3} from './types';

export type MaterialFactory = (preset: string, localColor?: string, repeat?: [number, number]) => THREE.Material;

const boxGeometry = new THREE.BoxGeometry(1, 1, 1);
const cylinderGeometry = new THREE.CylinderGeometry(1, 1, 1, 16);
const sphereGeometry = new THREE.SphereGeometry(1, 16, 12);
const torusGeometry = new THREE.TorusGeometry(1, 0.16, 10, 28);

function box(size: THREE.Vector3, material: THREE.Material, x: number, y: number, z: number, parent: THREE.Object3D): THREE.Mesh {
  const mesh = new THREE.Mesh(boxGeometry, material);
  mesh.scale.set(Math.max(size.x, 0.002), Math.max(size.y, 0.002), Math.max(size.z, 0.002));
  mesh.position.set(x, y, z);
  mesh.castShadow = true;
  mesh.receiveShadow = true;
  parent.add(mesh);
  return mesh;
}

function cylinder(radiusTop: number, radiusBottom: number, height: number, material: THREE.Material, x: number, y: number, z: number, parent: THREE.Object3D): THREE.Mesh {
  const mesh = new THREE.Mesh(new THREE.CylinderGeometry(radiusTop, radiusBottom, height, 16), material);
  mesh.position.set(x, y, z);
  mesh.castShadow = true;
  mesh.receiveShadow = true;
  parent.add(mesh);
  return mesh;
}

function sphere(radius: number, material: THREE.Material, x: number, y: number, z: number, parent: THREE.Object3D): THREE.Mesh {
  const mesh = new THREE.Mesh(sphereGeometry, material);
  mesh.scale.setScalar(radius);
  mesh.position.set(x, y, z);
  mesh.castShadow = true;
  parent.add(mesh);
  return mesh;
}

function legs(parent: THREE.Object3D, sx: number, sz: number, height: number, material: THREE.Material, inset = 0.08): void {
  const x = Math.max(0.01, sx / 2 - inset);
  const z = Math.max(0.01, sz / 2 - inset);
  const y = -height / 2;
  for (const [dx, dz] of [[-1, -1], [1, -1], [-1, 1], [1, 1]]) {
    cylinder(0.022, 0.026, height, material, dx * x, y, dz * z, parent);
  }
}

function sofa(size: THREE.Vector3, M: MaterialFactory): THREE.Group {
  const group = new THREE.Group();
  const {x: sx, y: sy, z: sz} = size;
  const fabric = M('fabric');
  const wood = M('wood');
  const base = sy * 0.36;
  box(new THREE.Vector3(sx, base, sz), fabric, 0, -sy / 2 + base / 2, 0, group);
  box(new THREE.Vector3(sx, sy * 0.5, sz * 0.18), fabric, 0, -sy / 2 + base + sy * 0.25, -sz / 2 + sz * 0.09, group);
  box(new THREE.Vector3(sx * 0.1, sy * 0.34, sz), fabric, -sx / 2 + sx * 0.05, -sy / 2 + base + sy * 0.17, 0, group);
  box(new THREE.Vector3(sx * 0.1, sy * 0.34, sz), fabric, sx / 2 - sx * 0.05, -sy / 2 + base + sy * 0.17, 0, group);
  const count = sx > 1.6 ? 3 : 2;
  const width = (sx * 0.8) / count;
  for (let index = 0; index < count; index += 1) {
    box(new THREE.Vector3(width * 0.94, sy * 0.12, sz * 0.56), fabric,
      (index - (count - 1) / 2) * width, -sy / 2 + base + sy * 0.06, sz * 0.12, group);
  }
  legs(group, sx, sz, sy * 0.12, wood);
  return group;
}

function armchair(size: THREE.Vector3, M: MaterialFactory): THREE.Group {
  const group = new THREE.Group();
  const {x: sx, y: sy, z: sz} = size;
  const fabric = M('fabric');
  const wood = M('wood');
  box(new THREE.Vector3(sx, sy * 0.34, sz), fabric, 0, -sy / 2 + sy * 0.17, 0, group);
  box(new THREE.Vector3(sx, sy * 0.56, sz * 0.16), fabric, 0, -sy / 2 + sy * 0.62, -sz / 2 + sz * 0.08, group);
  box(new THREE.Vector3(sx * 0.14, sy * 0.28, sz), fabric, -sx / 2 + sx * 0.07, -sy / 2 + sy * 0.34, 0, group);
  box(new THREE.Vector3(sx * 0.14, sy * 0.28, sz), fabric, sx / 2 - sx * 0.07, -sy / 2 + sy * 0.34, 0, group);
  legs(group, sx, sz, sy * 0.12, wood);
  return group;
}

function chair(size: THREE.Vector3, M: MaterialFactory): THREE.Group {
  const group = new THREE.Group();
  const {x: sx, y: sy, z: sz} = size;
  const wood = M('wood');
  box(new THREE.Vector3(sx, sy * 0.08, sz * 0.9), wood, 0, -sy / 2 + sy * 0.42, 0, group);
  box(new THREE.Vector3(sx * 0.9, sy * 0.46, sz * 0.08), wood, 0, -sy / 2 + sy * 0.7, -sz * 0.38, group);
  legs(group, sx, sz, sy * 0.42, wood, 0.05);
  return group;
}

function stool(size: THREE.Vector3, M: MaterialFactory): THREE.Group {
  const group = new THREE.Group();
  const wood = M('wood');
  box(new THREE.Vector3(size.x, size.y * 0.12, size.z), wood, 0, size.y * 0.38, 0, group);
  legs(group, size.x, size.z, size.y * 0.76, wood, 0.05);
  return group;
}

function bench(size: THREE.Vector3, M: MaterialFactory): THREE.Group {
  const group = new THREE.Group();
  const wood = M('wood');
  box(new THREE.Vector3(size.x, size.y * 0.16, size.z), wood, 0, size.y * 0.32, 0, group);
  legs(group, size.x, size.z, size.y * 0.64, wood, 0.1);
  return group;
}

function table(size: THREE.Vector3, M: MaterialFactory, preset = 'wood'): THREE.Group {
  const group = new THREE.Group();
  const material = M(preset);
  box(new THREE.Vector3(size.x, size.y * 0.1, size.z), material, 0, size.y / 2 - size.y * 0.05, 0, group);
  legs(group, size.x, size.z, size.y * 0.9, material, 0.09);
  return group;
}

function cabinet(size: THREE.Vector3, M: MaterialFactory, preset = 'wood'): THREE.Group {
  const group = new THREE.Group();
  const body = M(preset);
  box(new THREE.Vector3(size.x, size.y * 0.94, size.z), body, 0, 0, 0, group);
  const inset = M('metal');
  for (const side of [-1, 1]) {
    box(new THREE.Vector3(size.x * 0.035, size.y * 0.06, 0.03), inset, side * size.x * 0.05, 0, size.z / 2 + 0.012, group);
  }
  legs(group, size.x, size.z, size.y * 0.06, M('wood'));
  return group;
}

function bookshelf(size: THREE.Vector3, M: MaterialFactory): THREE.Group {
  const group = new THREE.Group();
  const body = M('wood');
  box(new THREE.Vector3(size.x, size.y, size.z * 0.06), body, 0, 0, -size.z / 2 + size.z * 0.03, group);
  for (const side of [-1, 1]) box(new THREE.Vector3(size.x * 0.05, size.y, size.z), body, side * (size.x / 2 - size.x * 0.025), 0, 0, group);
  for (let index = 0; index <= 4; index += 1) box(new THREE.Vector3(size.x, size.y * 0.035, size.z), body, 0, -size.y / 2 + (size.y * index) / 4, 0, group);
  return group;
}

function bed(size: THREE.Vector3, M: MaterialFactory): THREE.Group {
  const group = new THREE.Group();
  const {x: sx, y: sy, z: sz} = size;
  const wood = M('wood');
  const fabric = M('fabric');
  box(new THREE.Vector3(sx, sy * 0.28, sz), wood, 0, -sy / 2 + sy * 0.14, 0, group);
  box(new THREE.Vector3(sx * 0.96, sy * 0.3, sz * 0.96), fabric, 0, -sy / 2 + sy * 0.42, sz * 0.02, group);
  box(new THREE.Vector3(sx, sy * 0.66, sz * 0.05), wood, 0, -sy / 2 + sy * 0.33, -sz / 2 + sz * 0.025, group);
  const pillow = M('fabric');
  for (const side of [-1, 1]) box(new THREE.Vector3(sx * 0.36, sy * 0.1, sz * 0.16), pillow, side * sx * 0.21, sy * 0.06, -sz * 0.32, group);
  box(new THREE.Vector3(sx * 0.94, sy * 0.06, sz * 0.3), M('fabric'), 0, sy * 0.02, sz * 0.28, group);
  return group;
}

function nightstand(size: THREE.Vector3, M: MaterialFactory): THREE.Group {
  const group = new THREE.Group();
  const body = M('wood');
  box(new THREE.Vector3(size.x, size.y * 0.86, size.z), body, 0, 0, 0, group);
  box(new THREE.Vector3(size.x * 0.8, size.y * 0.02, 0.02), M('metal'), 0, size.y * 0.08, size.z / 2 + 0.011, group);
  legs(group, size.x, size.z, size.y * 0.07, body);
  return group;
}

function lamp(size: THREE.Vector3, M: MaterialFactory, hanging = false): THREE.Group {
  const group = new THREE.Group();
  const {x: sx, y: sy, z: sz} = size;
  const metal = M('metal');
  const shade = M('fabric', undefined);
  if (hanging) {
    cylinder(0.008, 0.008, sy * 0.55, metal, 0, sy * 0.22, 0, group);
    cylinder(sx * 0.28, sx * 0.5, sy * 0.45, shade, 0, -sy * 0.1, 0, group);
  } else {
    cylinder(sx * 0.28, sx * 0.34, sy * 0.05, metal, 0, -sy / 2 + sy * 0.025, 0, group);
    cylinder(0.012, 0.012, sy * 0.7, metal, 0, -sy * 0.1, 0, group);
    cylinder(sx * 0.22, sx * 0.46, sy * 0.32, shade, 0, sy * 0.26, 0, group);
  }
  return group;
}

function rug(size: THREE.Vector3, M: MaterialFactory): THREE.Group {
  const group = new THREE.Group();
  box(new THREE.Vector3(size.x, Math.max(0.012, size.y), size.z), M('fabric'), 0, 0, 0, group);
  return group;
}

function plant(size: THREE.Vector3, M: MaterialFactory): THREE.Group {
  const group = new THREE.Group();
  const {x: sx, y: sy, z: sz} = size;
  const ceramic = M('ceramic');
  const leaf = M('plant', '#4f6b45');
  cylinder(sx * 0.28, sx * 0.36, sy * 0.32, ceramic, 0, -sy / 2 + sy * 0.16, 0, group);
  cylinder(0.018, 0.026, sy * 0.55, M('wood'), 0, sy * 0.02, 0, group);
  for (let index = 0; index < 5; index += 1) {
    const angle = (index / 5) * Math.PI * 2;
    sphere(sx * 0.24, leaf, Math.cos(angle) * sx * 0.2, sy * 0.3 - index * sy * 0.045, Math.sin(angle) * sz * 0.2, group);
  }
  return group;
}

function art(size: THREE.Vector3, M: MaterialFactory): THREE.Group {
  const group = new THREE.Group();
  box(new THREE.Vector3(size.x, size.y, size.z), M('wood'), 0, 0, 0, group);
  box(new THREE.Vector3(size.x * 0.88, size.y * 0.88, size.z * 0.35), M('fabric', '#d8cdb8'), 0, 0, size.z * 0.38, group);
  return group;
}

function mirror(size: THREE.Vector3, M: MaterialFactory): THREE.Group {
  const group = new THREE.Group();
  box(new THREE.Vector3(size.x, size.y, size.z), M('metal'), 0, 0, 0, group);
  box(new THREE.Vector3(size.x * 0.86, size.y * 0.9, size.z * 0.4), M('glass'), 0, 0, size.z * 0.36, group);
  return group;
}

function tv(size: THREE.Vector3, M: MaterialFactory): THREE.Group {
  const group = new THREE.Group();
  box(new THREE.Vector3(size.x, size.y, size.z), M('glass', '#151819'), 0, 0, 0, group);
  box(new THREE.Vector3(size.x * 0.94, size.y * 0.9, size.z * 0.35), M('glass', '#243234'), 0, 0, size.z * 0.4, group);
  box(new THREE.Vector3(size.x * 0.18, size.y * 0.04, 0.04), M('metal'), 0, -size.y * 0.62, 0, group);
  return group;
}

function appliance(size: THREE.Vector3, M: MaterialFactory): THREE.Group {
  const group = new THREE.Group();
  box(new THREE.Vector3(size.x, size.y, size.z), M('metal'), 0, 0, 0, group);
  box(new THREE.Vector3(size.x * 0.02, size.y * 0.55, 0.03), M('glass'), size.x * 0.32, 0, size.z / 2 + 0.016, group);
  return group;
}

function kitchenIsland(size: THREE.Vector3, M: MaterialFactory): THREE.Group {
  const group = new THREE.Group();
  const body = M('paint');
  box(new THREE.Vector3(size.x, size.y * 0.92, size.z), body, 0, 0, 0, group);
  box(new THREE.Vector3(size.x * 1.02, size.y * 0.06, size.z * 1.04), M('stone'), 0, size.y * 0.46, 0, group);
  return group;
}

function toilet(size: THREE.Vector3, M: MaterialFactory): THREE.Group {
  const group = new THREE.Group();
  const ceramic = M('ceramic');
  cylinder(size.x * 0.34, size.x * 0.4, size.y * 0.28, ceramic, 0, -size.y * 0.34, size.z * 0.12, group);
  box(new THREE.Vector3(size.x * 0.92, size.y * 0.16, size.z * 0.9), ceramic, 0, -size.y * 0.16, size.z * 0.12, group);
  box(new THREE.Vector3(size.x * 0.92, size.y * 0.5, size.z * 0.22), ceramic, 0, size.y * 0.16, -size.z * 0.32, group);
  return group;
}

function sink(size: THREE.Vector3, M: MaterialFactory): THREE.Group {
  const group = new THREE.Group();
  box(new THREE.Vector3(size.x, size.y * 0.84, size.z), M('wood'), 0, -size.y * 0.04, 0, group);
  box(new THREE.Vector3(size.x * 0.96, size.y * 0.08, size.z * 0.9), M('ceramic'), 0, size.y * 0.42, 0, group);
  cylinder(0.02, 0.02, size.y * 0.26, M('metal'), 0, size.y * 0.56, -size.z * 0.28, group);
  return group;
}

function bathtub(size: THREE.Vector3, M: MaterialFactory): THREE.Group {
  const group = new THREE.Group();
  const ceramic = M('ceramic');
  box(new THREE.Vector3(size.x, size.y, size.z), ceramic, 0, 0, 0, group);
  box(new THREE.Vector3(size.x * 0.86, size.y * 0.42, size.z * 0.8), M('glass', '#dfe6e4'), 0, size.y * 0.28, 0, group);
  return group;
}

function shower(size: THREE.Vector3, M: MaterialFactory): THREE.Group {
  const group = new THREE.Group();
  box(new THREE.Vector3(size.x, size.y * 0.04, size.z), M('ceramic'), 0, -size.y / 2 + size.y * 0.02, 0, group);
  box(new THREE.Vector3(size.x * 0.06, size.y * 0.96, size.z), M('glass'), -size.x / 2 + size.x * 0.03, 0, 0, group);
  box(new THREE.Vector3(size.x, size.y * 0.96, size.z * 0.06), M('glass'), 0, 0, -size.z / 2 + size.z * 0.03, group);
  cylinder(0.025, 0.025, size.y * 0.3, M('metal'), size.x * 0.36, size.y * 0.34, -size.z * 0.36, group);
  return group;
}

function generic(size: THREE.Vector3, M: MaterialFactory): THREE.Group {
  const group = new THREE.Group();
  box(new THREE.Vector3(size.x, size.y, size.z), M('paint'), 0, 0, 0, group);
  box(new THREE.Vector3(size.x * 0.9, size.y * 0.02, size.z * 0.9), M('metal'), 0, size.y * 0.5, 0, group);
  return group;
}

/** Build a centred procedural furniture group whose bounds match the requested size. */
export function buildFurniture(type: string, size: Vec3, material: MaterialFactory): THREE.Group {
  const vector = new THREE.Vector3(...size);
  switch (type) {
    case 'sofa': return sofa(vector, material);
    case 'armchair': return armchair(vector, material);
    case 'chair': return chair(vector, material);
    case 'stool': return stool(vector, material);
    case 'bench': return bench(vector, material);
    case 'coffee-table':
    case 'dining-table':
    case 'side-table':
    case 'desk': return table(vector, material, type === 'side-table' ? 'stone' : 'wood');
    case 'cabinet':
    case 'sideboard':
    case 'tv-console': return cabinet(vector, material);
    case 'bookshelf': return bookshelf(vector, material);
    case 'wardrobe': return cabinet(vector, material, 'paint');
    case 'dresser': return cabinet(vector, material, 'paint');
    case 'bed':
    case 'single-bed': return bed(vector, material);
    case 'nightstand': return nightstand(vector, material);
    case 'floor-lamp': return lamp(vector, material);
    case 'table-lamp': return lamp(vector, material);
    case 'pendant': return lamp(vector, material, true);
    case 'rug': return rug(vector, material);
    case 'plant': return plant(vector, material);
    case 'vase': return cylinderGroup(vector, material);
    case 'art': return art(vector, material);
    case 'mirror': return mirror(vector, material);
    case 'curtain': return curtain(vector, material);
    case 'tv': return tv(vector, material);
    case 'fridge': return appliance(vector, material);
    case 'kitchen-island': return kitchenIsland(vector, material);
    case 'toilet': return toilet(vector, material);
    case 'sink': return sink(vector, material);
    case 'bathtub': return bathtub(vector, material);
    case 'shower': return shower(vector, material);
    default: return generic(vector, material);
  }
}

function cylinderGroup(size: THREE.Vector3, M: MaterialFactory): THREE.Group {
  const group = new THREE.Group();
  const {x: sx, y: sy, z: sz} = size;
  const ceramic = M('ceramic');
  cylinder(sx * 0.32, sx * 0.42, sy * 0.72, ceramic, 0, -sy * 0.14, 0, group);
  cylinder(sx * 0.12, sx * 0.16, sy * 0.16, ceramic, 0, sy * 0.36, 0, group);
  for (let index = 0; index < 3; index += 1) {
    const angle = index * 2.1;
    cylinder(0.008, 0.008, sy * 0.35, M('plant'), Math.cos(angle) * sx * 0.05, sy * 0.48, Math.sin(angle) * sz * 0.05, group);
  }
  return group;
}

function curtain(size: THREE.Vector3, M: MaterialFactory): THREE.Group {
  const group = new THREE.Group();
  const fabric = M('fabric');
  const count = 6;
  for (let index = 0; index < count; index += 1) {
    const width = size.x / count;
    box(new THREE.Vector3(width * 0.82, size.y, Math.max(0.02, size.z * 0.7)), fabric,
      -size.x / 2 + width * (index + 0.5), 0, Math.sin(index) * size.z * 0.16, group);
  }
  cylinder(0.016, 0.016, size.x, M('metal'), 0, size.y / 2 + 0.02, 0, group).rotation.z = Math.PI / 2;
  return group;
}

export function sofaBoundsWarning(): void {
  // Kept as a named export for tree-shaking stability of the public module.
}
