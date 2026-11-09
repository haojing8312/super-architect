import * as THREE from 'three';
import type {MaterialSpec} from './types';

let TILE = 256;
let GRID = 4;
const PATTERN_INDEX: Record<string, number> = {
  oak: 0, walnut: 1, ash: 2, limewash: 3,
  plaster: 4, concrete: 5, microcement: 6, marble: 7,
  terrazzo: 8, tile: 9, linen: 10, woven: 11,
  boucle: 11, leather: 12, wool: 13, jute: 13, silk: 13,
  metal: 14, steel: 14, brass: 14, rattan: 15,
};
const PRESET_PATTERN: Record<string, string> = {
  wood: 'oak', paint: 'plaster', plaster: 'plaster', concrete: 'concrete',
  stone: 'marble', fabric: 'linen', leather: 'leather', metal: 'metal',
  glass: 'glass', ceramic: 'ceramic', plant: 'plant', wool: 'wool',
  carpet: 'wool', paper: 'linen', rattan: 'rattan', tile: 'tile',
  terrazzo: 'terrazzo', cork: 'oak',
};
const TEXTURE_PRESETS = new Set(['wood', 'paint', 'plaster', 'concrete', 'stone', 'fabric', 'leather', 'metal', 'wool', 'carpet', 'paper', 'rattan', 'tile', 'terrazzo', 'cork']);

const tiles = new Map<number, THREE.Texture>();
let atlasImage: HTMLImageElement | null = null;

export async function initMaterials(dataUri?: string, tileSize = 256, grid = 4): Promise<boolean> {
  if (!dataUri) return false;
  TILE = Math.max(16, Math.floor(tileSize));
  GRID = Math.max(1, Math.floor(grid));
  const image = new Image();
  image.decoding = 'async';
  const loaded = await new Promise<boolean>((resolve) => {
    image.onload = () => resolve(true);
    image.onerror = () => resolve(false);
    image.src = dataUri;
  });
  if (!loaded) return false;
  atlasImage = image;
  return true;
}

function tileTexture(pattern: string): THREE.Texture | null {
  const index = PATTERN_INDEX[pattern];
  if (index === undefined || !atlasImage) return null;
  const cached = tiles.get(index);
  if (cached) return cached;
  const canvas = document.createElement('canvas');
  canvas.width = TILE;
  canvas.height = TILE;
  const context = canvas.getContext('2d');
  if (!context) return null;
  const col = index % GRID;
  const row = Math.floor(index / GRID);
  context.drawImage(atlasImage, col * TILE, row * TILE, TILE, TILE, 0, 0, TILE, TILE);
  const texture = new THREE.CanvasTexture(canvas);
  texture.colorSpace = THREE.SRGBColorSpace;
  texture.wrapS = THREE.RepeatWrapping;
  texture.wrapT = THREE.RepeatWrapping;
  texture.anisotropy = 8;
  tiles.set(index, texture);
  return texture;
}

export interface MaterialOptions {
  repeat?: [number, number];
  emissive?: string;
  side?: THREE.Side;
}

/** Build a local material from the public style profile; no remote assets. */
export function makeMaterial(spec: MaterialSpec | undefined, fallbackColor: string, options: MaterialOptions = {}): THREE.Material {
  const value = spec || {};
  const preset = String(value.preset || 'paint');
  const color = String(value.color || fallbackColor || '#d8d4cc');
  if (preset === 'glass') {
    return new THREE.MeshStandardMaterial({
      color: new THREE.Color(color), roughness: Number(value.roughness ?? 0.1), metalness: Number(value.metalness ?? 0.15),
      transparent: true, opacity: Number(value.opacity ?? 0.3), depthWrite: false, side: THREE.DoubleSide,
    });
  }
  const pattern = String(value.pattern || PRESET_PATTERN[preset] || preset);
  const useTexture = TEXTURE_PRESETS.has(preset) || PATTERN_INDEX[pattern] !== undefined;
  const material = new THREE.MeshStandardMaterial({
    color: new THREE.Color(color),
    roughness: Number(value.roughness ?? 0.78),
    metalness: Number(value.metalness ?? (preset === 'metal' ? 0.55 : 0)),
    side: options.side ?? THREE.FrontSide,
  });
  if (useTexture) {
    const base = tileTexture(pattern);
    if (base) {
      const map = base.clone();
      map.needsUpdate = true;
      const repeat = options.repeat || [1, 1];
      map.repeat.set(Math.max(0.25, repeat[0]), Math.max(0.25, repeat[1]));
      material.map = map;
    }
  }
  if (options.emissive) {
    material.emissive = new THREE.Color(options.emissive);
    material.emissiveIntensity = 0.55;
  }
  return material;
}

/** Colour-only material for selection helpers and simple devices. */
export function flatMaterial(color: string, options: {opacity?: number; emissive?: string} = {}): THREE.MeshStandardMaterial {
  return new THREE.MeshStandardMaterial({
    color: new THREE.Color(color), roughness: 0.7, metalness: 0.05,
    transparent: options.opacity !== undefined, opacity: options.opacity ?? 1,
    emissive: new THREE.Color(options.emissive || '#000000'), emissiveIntensity: options.emissive ? 0.5 : 0,
  });
}

export function disposeMaterials(root: THREE.Object3D): void {
  root.traverse((node) => {
    const mesh = node as THREE.Mesh;
    if (!mesh.isMesh) return;
    const materials = Array.isArray(mesh.material) ? mesh.material : [mesh.material];
    for (const material of materials) {
      const standard = material as THREE.MeshStandardMaterial;
      standard.map?.dispose();
      material.dispose();
    }
  });
}
