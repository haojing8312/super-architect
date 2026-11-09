/** Scene data contract shared with backend/architecture_workbench/interior.py. */
export type Vec3 = [number, number, number];
export type Vec2 = [number, number];

export interface MaterialSpec {
  preset?: string;
  pattern?: string;
  color?: string;
  baseColor?: string;
  roughness?: number;
  metalness?: number;
  opacity?: number;
  [key: string]: unknown;
}

export interface SourceEvidence {
  sourceFileId?: string;
  sourceSha256?: string;
  pixelBounds?: number[];
  inferred?: string[];
}

export interface SceneObject {
  id: string;
  name: string;
  type: string;
  layer?: string;
  pos: Vec3;
  size: Vec3;
  rotation?: number;
  visible?: boolean;
  color?: string;
  material?: MaterialSpec;
  room?: string | null;
  sourceEvidence?: SourceEvidence;
  furnitureDetail?: Record<string, unknown>;
  [key: string]: unknown;
}

export interface Room {
  id: string;
  name: string;
  center: Vec2;
  size: Vec2;
  polygon: Vec2[] | number[][];
  outdoor?: boolean;
}

export interface StyleSurfaceProfile {
  preset?: string;
  pattern?: string;
  color?: string;
  roughness?: number;
  metalness?: number;
  opacity?: number;
  [key: string]: unknown;
}

export interface StyleProfile {
  id: string;
  name: string;
  tag?: string;
  palette?: string[];
  surfaceDecorProfile?: Record<string, StyleSurfaceProfile>;
  furniture?: Record<string, string>;
  decor?: Record<string, string>;
  [key: string]: unknown;
}

export interface Scheme {
  name: string;
  objects: SceneObject[];
  surfaceOverrides?: Record<string, {color?: string; material?: MaterialSpec}>;
  styleAudit?: {status?: string; items?: unknown[]; screenshots?: string[]};
  customProfile?: {
    version?: string;
    furniture?: Record<string, string>;
    decor?: Record<string, string>;
    surfaces?: Record<string, StyleSurfaceProfile>;
    referenceImageIds?: string[];
    features?: string[];
    [key: string]: unknown;
  };
  [key: string]: unknown;
}

export interface DrawingInfo {
  width: number;
  height: number;
  sourceWidth: number;
  sourceHeight: number;
  origin: Vec2;
  scaleX: number;
  scaleZ: number;
  status: string;
}

export interface SceneDocument {
  format: string;
  engineVersion: string;
  name: string;
  revision: number;
  isSample: boolean;
  source: {name: string; imageData: string; derivedFromSha256?: string; page?: number; [key: string]: unknown};
  generation: {taskId: string; sourceFileId: string; sourceSha256: string; page: number};
  drawing: DrawingInfo;
  recognition: {status: string; summary?: string; unresolved?: string[]; [key: string]: unknown};
  architecture: SceneObject[];
  rooms: Room[];
  schemes: Record<string, Scheme>;
  style: string;
  cameras?: unknown[];
  assumptions?: string[];
  delivery: {stage: string; [key: string]: unknown};
  display?: Record<string, unknown>;
  [key: string]: unknown;
}

export interface CatalogItem {
  type: string;
  name: string;
  category: string;
  size: Vec3;
  preset: string;
  color: string;
}

export interface RuntimeInfo {
  version: string;
  preview: boolean;
  builtAt: string;
  assets?: {atlas?: string};
  [key: string]: unknown;
}

export function cloneScene<T>(value: T): T {
  return JSON.parse(JSON.stringify(value)) as T;
}
