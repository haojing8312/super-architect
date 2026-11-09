import * as THREE from 'three';
import {OrbitControls} from 'three/addons/controls/OrbitControls.js';
import {TransformControls} from 'three/addons/controls/TransformControls.js';
import {GLTFExporter} from 'three/addons/exporters/GLTFExporter.js';
import type {CatalogItem, MaterialSpec, SceneDocument, SceneObject, StyleProfile} from './types';
import {cloneScene} from './types';
import {buildFurniture, type MaterialFactory} from './furniture';
import {disposeMaterials, flatMaterial, makeMaterial} from './materials';
import stylesData from '../catalog/styles.json';

const styleCatalog = (stylesData as {styles: StyleProfile[]}).styles;

const FLOORPLAN_OPACITY = 0.42;
const BACKGROUND = 0xd9d5cc;

export interface ViewerCallbacks {
  onSelect: (item: SceneObject | null, kind: 'architecture' | 'furniture' | null) => void;
  onChange: (reason: string) => void;
  onStatus: (message: string) => void;
  onError: (message: string) => void;
  onHistory: () => void;
}

interface Entry {
  data: SceneObject;
  group: THREE.Group;
  kind: 'architecture' | 'furniture';
  scheme?: string;
}

export class InteriorViewer {
  private readonly renderer: THREE.WebGLRenderer;
  private readonly scene = new THREE.Scene();
  private readonly camera: THREE.PerspectiveCamera;
  private readonly controls: OrbitControls;
  private readonly transform: TransformControls;
  private readonly raycaster = new THREE.Raycaster();
  private readonly pointer = new THREE.Vector2();
  private readonly modelGroup = new THREE.Group();
  private readonly architectureGroup = new THREE.Group();
  private readonly furnitureGroups = new Map<string, THREE.Group>();
  private readonly underlayGroup = new THREE.Group();
  private readonly gridGroup = new THREE.Group();
  private readonly labelGroup = new THREE.Group();
  private readonly helper: THREE.BoxHelper;
  private readonly container: HTMLElement;
  private readonly callbacks: ViewerCallbacks;
  private readonly resizeObserver: ResizeObserver;
  private animation = 0;
  private disposed = false;
  private down: {x: number; y: number; button: number} | null = null;
  private entries = new Map<string, Entry>();
  private selectedId: string | null = null;
  private currentScheme: string;
  private showCeiling = false;
  private showUnderlay = true;
  private showGrid = true;
  private showLabels = true;
  private underlayToken = 0;
  private undoStack: string[] = [];
  private redoStack: string[] = [];
  private pendingBefore: string | null = null;
  private lastFrame = performance.now();
  private frameCount = 0;

  constructor(container: HTMLElement, private doc: SceneDocument, callbacks: ViewerCallbacks) {
    this.container = container;
    this.callbacks = callbacks;
    this.currentScheme = doc.style && doc.schemes[doc.style] ? doc.style : (Object.keys(doc.schemes)[0] || 'base');
    this.renderer = new THREE.WebGLRenderer({antialias: true, powerPreference: 'high-performance', alpha: false});
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
    this.renderer.outputColorSpace = THREE.SRGBColorSpace;
    this.renderer.toneMapping = THREE.ACESFilmicToneMapping;
    this.renderer.toneMappingExposure = 1.05;
    this.renderer.shadowMap.enabled = true;
    this.renderer.shadowMap.type = THREE.PCFSoftShadowMap;
    this.renderer.domElement.setAttribute('aria-label', '室内三维编辑视口');
    this.renderer.domElement.tabIndex = 0;
    this.container.appendChild(this.renderer.domElement);

    this.scene.background = new THREE.Color(BACKGROUND);
    this.scene.fog = new THREE.Fog(BACKGROUND, 28, 90);
    this.camera = new THREE.PerspectiveCamera(44, 1, 0.05, 500);
    this.camera.position.set(9, 8, 11);
    this.controls = new OrbitControls(this.camera, this.renderer.domElement);
    this.controls.enableDamping = true;
    this.controls.dampingFactor = 0.08;
    this.controls.screenSpacePanning = true;
    this.controls.minDistance = 0.6;
    this.controls.maxDistance = 90;
    this.controls.maxPolarAngle = Math.PI * 0.495;
    this.controls.mouseButtons = {LEFT: THREE.MOUSE.ROTATE, MIDDLE: THREE.MOUSE.DOLLY, RIGHT: THREE.MOUSE.PAN};
    this.controls.target.set(0, 1, 0);

    this.scene.add(this.modelGroup, this.underlayGroup, this.gridGroup, this.labelGroup);
    this.modelGroup.add(this.architectureGroup);
    this.buildLights();
    this.helper = new THREE.BoxHelper(new THREE.Object3D(), 0xc9a24b);
    this.helper.visible = false;
    this.helper.renderOrder = 20;
    (this.helper.material as THREE.LineBasicMaterial).depthTest = false;
    this.scene.add(this.helper);

    this.transform = new TransformControls(this.camera, this.renderer.domElement);
    this.transform.setSize(0.72);
    this.transform.setSpace('world');
    this.scene.add(this.transform.getHelper());
    this.transform.addEventListener('dragging-changed', (event) => {
      this.controls.enabled = !event.value;
    });
    this.transform.addEventListener('mouseDown', () => this.beginMutation());
    this.transform.addEventListener('objectChange', () => this.syncSelected());
    this.transform.addEventListener('mouseUp', () => this.commitMutation());

    this.renderer.domElement.addEventListener('pointerdown', this.onPointerDown);
    this.renderer.domElement.addEventListener('pointerup', this.onPointerUp);
    this.resizeObserver = new ResizeObserver(() => this.resize());
    this.resizeObserver.observe(this.container);
    window.addEventListener('keydown', this.onKeyDown);
    document.addEventListener('visibilitychange', this.onVisibility);
    this.resize();
    this.buildDocument();
    this.animate();
  }

  private buildLights(): void {
    const hemisphere = new THREE.HemisphereLight(0xfff6e6, 0x5e5a52, 1.35);
    this.scene.add(hemisphere);
    const sun = new THREE.DirectionalLight(0xfff1d8, 2.1);
    sun.position.set(12, 18, 9);
    sun.castShadow = true;
    sun.shadow.mapSize.set(2048, 2048);
    sun.shadow.camera.near = 0.5;
    sun.shadow.camera.far = 90;
    sun.shadow.camera.left = -22;
    sun.shadow.camera.right = 22;
    sun.shadow.camera.top = 22;
    sun.shadow.camera.bottom = -22;
    sun.shadow.bias = -0.0004;
    this.scene.add(sun);
    const fill = new THREE.DirectionalLight(0xcfd8dd, 0.55);
    fill.position.set(-10, 8, -12);
    this.scene.add(fill);
    const ground = new THREE.Mesh(new THREE.PlaneGeometry(200, 200), flatMaterial('#c9c4b9'));
    ground.rotation.x = -Math.PI / 2;
    ground.position.y = -0.06;
    ground.receiveShadow = true;
    ground.name = 'ground';
    this.scene.add(ground);
  }

  private buildDocument(): void {
    this.clearModel();
    this.buildUnderlay();
    this.buildGrid();
    this.buildArchitecture();
    this.buildFurniture();
    this.buildLabels();
    this.applyVisibility();
    this.helper.visible = false;
    this.selectedId = null;
    this.callbacks.onSelect(null, null);
    if (!this.doc.architecture.length && !this.doc.rooms.length) {
      this.camera.position.set(6, 5, 7);
      this.controls.target.set(0, 0.8, 0);
    } else {
      this.fit();
    }
    this.callbacks.onStatus('场景已载入');
  }

  private clearModel(): void {
    this.transform.detach();
    for (const group of [this.architectureGroup, ...this.furnitureGroups.values()]) {
      disposeMaterials(group);
      group.clear();
      group.parent?.remove(group);
    }
    this.architectureGroup.clear();
    this.furnitureGroups.clear();
    this.modelGroup.clear();
    this.modelGroup.add(this.architectureGroup);
    this.entries.clear();
  }

  private buildArchitecture(): void {
    this.architectureGroup.name = 'architecture';
    for (const object of this.doc.architecture || []) {
      const group = this.buildObject(object, 'architecture');
      this.architectureGroup.add(group);
    }
  }

  private buildFurniture(): void {
    for (const [schemeId, scheme] of Object.entries(this.doc.schemes || {})) {
      const group = new THREE.Group();
      group.name = `scheme-${schemeId}`;
      group.userData.scheme = schemeId;
      group.visible = schemeId === this.currentScheme;
      for (const object of scheme.objects || []) {
        group.add(this.buildObject(object, 'furniture', schemeId));
      }
      this.furnitureGroups.set(schemeId, group);
      this.modelGroup.add(group);
    }
  }

  private buildObject(object: SceneObject, kind: 'architecture' | 'furniture', schemeId?: string): THREE.Group {
    const group = new THREE.Group();
    group.userData.objectId = object.id;
    group.userData.kind = kind;
    group.userData.entryKey = `${kind}:${object.id}`;
    if (schemeId) group.userData.scheme = schemeId;
    group.position.set(...(object.pos || [0, 0, 0]));
    group.rotation.y = Number(object.rotation || 0);
    const size = object.size || [0.5, 0.5, 0.5];
    if (kind === 'architecture') this.decorateArchitecture(group, object, size);
    else this.decorateFurniture(group, object, size, schemeId || this.currentScheme);
    if (object.visible === false) group.visible = false;
    this.entries.set(`${kind}:${object.id}`, {data: object, group, kind, scheme: schemeId});
    return group;
  }

  private decorateArchitecture(group: THREE.Group, object: SceneObject, size: [number, number, number]): void {
    const type = String(object.type || 'wall');
    const material = this.architectureMaterial(object);
    const [sx, sy, sz] = size.map((value) => Math.max(0.01, Number(value) || 0.01)) as [number, number, number];
    if (type === 'door') {
      const frame = material;
      const wood = makeMaterial({preset: 'wood', pattern: 'walnut', color: '#8a6247'}, '#8a6247');
      const jamb = Math.max(0.045, Math.min(sx, sz) * 0.18);
      for (const side of [-1, 1]) {
        const mesh = new THREE.Mesh(new THREE.BoxGeometry(jamb, sy, sz), frame);
        mesh.position.set(side * (sx / 2 - jamb / 2), 0, 0);
        mesh.castShadow = true;
        group.add(mesh);
      }
      const top = new THREE.Mesh(new THREE.BoxGeometry(sx, Math.max(0.05, sy * 0.04), sz), frame);
      top.position.y = sy / 2 - sy * 0.02;
      group.add(top);
      const panel = new THREE.Mesh(new THREE.BoxGeometry(sx - jamb * 2, sy * 0.94, Math.max(0.025, sz * 0.35)), wood);
      panel.position.y = -sy * 0.015;
      panel.castShadow = true;
      group.add(panel);
      const knob = new THREE.Mesh(new THREE.SphereGeometry(Math.max(0.012, sx * 0.012), 12, 8), makeMaterial({preset: 'metal', color: '#b39a63'}, '#b39a63'));
      knob.position.set(sx * 0.36, 0, sz * 0.22);
      group.add(knob);
      return;
    }
    if (type === 'window') {
      const glass = makeMaterial({preset: 'glass', color: '#b9c9c7', opacity: 0.3}, '#b9c9c7');
      const panel = new THREE.Mesh(new THREE.BoxGeometry(sx * 0.96, sy * 0.9, Math.max(0.012, sz * 0.25)), glass);
      panel.position.y = sy * 0.06;
      group.add(panel);
      const frame = makeMaterial({preset: 'metal', color: '#9aa3a1'}, '#9aa3a1');
      const bar = Math.max(0.018, Math.min(sx, sy) * 0.03);
      for (const side of [-1, 1]) {
        const mesh = new THREE.Mesh(new THREE.BoxGeometry(bar, sy, sz), frame);
        mesh.position.x = side * (sx / 2 - bar / 2);
        group.add(mesh);
      }
      const top = new THREE.Mesh(new THREE.BoxGeometry(sx, bar, sz), frame);
      top.position.y = sy / 2 - bar / 2;
      group.add(top);
      const bottom = top.clone();
      bottom.position.y = -sy / 2 + bar / 2;
      group.add(bottom);
      const mullion = new THREE.Mesh(new THREE.BoxGeometry(bar, sy, sz), frame);
      group.add(mullion);
      return;
    }
    const mesh = new THREE.Mesh(new THREE.BoxGeometry(sx, sy, sz), material);
    mesh.castShadow = type !== 'floor';
    mesh.receiveShadow = true;
    group.add(mesh);
  }

  private decorateFurniture(group: THREE.Group, object: SceneObject, size: [number, number, number], schemeId: string): void {
    const profile = this.profileFor(schemeId);
    const factory: MaterialFactory = (preset, localColor, repeat) => {
      const color = localColor || object.color || this.colorForPreset(preset, profile);
      return makeMaterial({preset, pattern: this.patternFor(preset), color}, color, {repeat: repeat || [1, 1]});
    };
    const built = buildFurniture(String(object.type || 'box'), size, factory);
    for (const child of [...built.children]) group.add(child);
    if (object.type === 'rug') group.position.y = Math.max(0.013, group.position.y);
  }

  private architectureMaterial(object: SceneObject): THREE.Material {
    const scheme = this.doc.schemes[this.currentScheme] || {objects: []};
    const override = scheme.surfaceOverrides?.[object.id];
    const baseSpec: MaterialSpec = {...(object.material || {}), ...(override?.material || {})};
    const color = String(override?.color || baseSpec.color || object.color || '#e9e7e1');
    const size = object.size || [1, 1, 1];
    const repeat: [number, number] = object.type === 'floor'
      ? [Math.max(1, size[0] * 1.4), Math.max(1, size[2] * 1.4)]
      : [Math.max(1, size[0] * 1.2), Math.max(1, size[1] * 1.2)];
    return makeMaterial({...baseSpec, color}, color, {repeat});
  }

  private profileFor(schemeId: string): StyleProfile {
    const scheme = this.doc.schemes[schemeId];
    if (scheme?.customProfile) {
      return {
        id: 'custom', name: scheme.name || '自定义风格',
        surfaceDecorProfile: scheme.customProfile.surfaces || {},
        furniture: scheme.customProfile.furniture || {},
        decor: scheme.customProfile.decor || {},
        palette: [],
      };
    }
    return styleCatalog.find((item) => item.id === schemeId) || styleCatalog[0];
  }

  private colorForPreset(preset: string, profile: StyleProfile): string {
    const key = /wood|oak|ash|walnut|rattan/.test(preset) ? 'wood'
      : /fabric|woven|linen|boucle|wool|jute|silk|leather/.test(preset) ? 'fabric'
        : /stone|marble|travertine|ceramic|terrazzo/.test(preset) ? 'stone'
          : /metal|steel|brass/.test(preset) ? 'metal'
            : /rug|carpet/.test(preset) ? 'rug'
              : /plant/.test(preset) ? 'leaf' : 'accent';
    const furniture = profile.furniture as Record<string, unknown> | undefined;
    if (furniture) {
      const direct = furniture[key];
      if (typeof direct === 'string' && direct.startsWith('#')) return direct;
    }
    const decor = (profile.decor || {}) as Record<string, unknown>;
    if (key === 'leaf' && typeof decor.leaf === 'string') return decor.leaf;
    if (key === 'accent' && typeof decor.accent === 'string') return decor.accent;
    const palette = profile.palette || [];
    if (key === 'wood') return palette[1] || '#a9845f';
    if (key === 'fabric') return palette[0] || '#c9bdab';
    if (key === 'stone') return palette[2] || '#b8b2a7';
    if (key === 'metal') return typeof decor.accent === 'string' ? decor.accent : '#9a8c76';
    if (key === 'rug') return palette[0] || '#d5c9b8';
    return palette[3] || '#6b5a4b';
  }

  private patternFor(preset: string): string {
    const map: Record<string, string> = {
      wood: 'oak', oak: 'oak', walnut: 'walnut', ash: 'ash', rattan: 'rattan',
      fabric: 'linen', woven: 'woven', linen: 'linen', boucle: 'boucle', wool: 'wool', jute: 'jute', silk: 'silk',
      stone: 'marble', marble: 'marble', travertine: 'marble', terrazzo: 'terrazzo', tile: 'tile',
      metal: 'metal', steel: 'steel', brass: 'brass', concrete: 'concrete', microcement: 'microcement',
      plaster: 'plaster', paint: 'plaster', limewash: 'limewash', leather: 'leather', paper: 'linen',
      ceramic: 'ceramic', plant: 'plant', glass: 'glass',
    };
    return map[preset] || preset;
  }

  private buildGrid(): void {
    this.gridGroup.clear();
    const bounds = this.planBounds();
    const span = Math.max(12, Math.ceil(Math.max(bounds.width, bounds.depth) * 1.3 / 2) * 2);
    const grid = new THREE.GridHelper(span, Math.round(span / 0.5), 0xb4aa97, 0xd2cbbf);
    const material = grid.material as THREE.LineBasicMaterial;
    material.transparent = true;
    material.opacity = 0.5;
    grid.position.y = 0.002;
    this.gridGroup.add(grid);
  }

  private buildUnderlay(): void {
    this.underlayGroup.clear();
    const token = ++this.underlayToken;
    const source = this.doc.source;
    const drawing = this.doc.drawing;
    if (!source?.imageData || !drawing) return;
    const width = Number(drawing.sourceWidth || drawing.width);
    const height = Number(drawing.sourceHeight || drawing.height);
    const scaleX = Number(drawing.scaleX || 0);
    const scaleZ = Number(drawing.scaleZ || 0);
    if (!(width > 0 && height > 0 && scaleX > 0 && scaleZ > 0)) return;
    const image = new Image();
    image.decoding = 'async';
    image.onload = () => {
      if (this.disposed || token !== this.underlayToken) return;
      const texture = new THREE.Texture(image);
      texture.colorSpace = THREE.SRGBColorSpace;
      texture.flipY = false;
      texture.needsUpdate = true;
      texture.anisotropy = 8;
      const plane = new THREE.Mesh(
        new THREE.PlaneGeometry(width * scaleX, height * scaleZ),
        new THREE.MeshBasicMaterial({map: texture, transparent: true, opacity: FLOORPLAN_OPACITY, depthWrite: false, side: THREE.DoubleSide, toneMapped: false}),
      );
      plane.rotation.x = -Math.PI / 2;
      plane.position.set(
        (width / 2 - Number(drawing.origin?.[0] || 0)) * scaleX,
        -0.004,
        (Number(drawing.origin?.[1] || height) - height / 2) * scaleZ,
      );
      plane.renderOrder = -2;
      plane.name = 'floorplan-underlay';
      this.underlayGroup.add(plane);
      plane.visible = this.showUnderlay;
    };
    image.onerror = () => this.callbacks.onError('底图未能载入，场景几何仍可编辑');
    image.src = source.imageData;
  }

  private buildLabels(): void {
    this.labelGroup.clear();
    for (const room of this.doc.rooms || []) {
      const center = Array.isArray(room.center) ? room.center : [0, 0];
      const sprite = this.labelSprite(String(room.name || room.id));
      if (!sprite) continue;
      sprite.position.set(Number(center[0]) || 0, 0.55, Number(center[1]) || 0);
      this.labelGroup.add(sprite);
    }
    this.labelGroup.visible = this.showLabels;
  }

  private labelSprite(text: string): THREE.Sprite | null {
    const canvas = document.createElement('canvas');
    canvas.width = 512;
    canvas.height = 128;
    const context = canvas.getContext('2d');
    if (!context) return null;
    context.clearRect(0, 0, canvas.width, canvas.height);
    context.fillStyle = 'rgba(42,40,36,0.72)';
    context.roundRect(8, 28, 496, 72, 16);
    context.fill();
    context.fillStyle = '#f7f2e8';
    context.font = '600 38px "Microsoft YaHei", sans-serif';
    context.textAlign = 'center';
    context.textBaseline = 'middle';
    context.fillText(text.slice(0, 18), 256, 64);
    const texture = new THREE.CanvasTexture(canvas);
    texture.colorSpace = THREE.SRGBColorSpace;
    const sprite = new THREE.Sprite(new THREE.SpriteMaterial({map: texture, transparent: true, depthWrite: false, toneMapped: false}));
    sprite.scale.set(2.2, 0.55, 1);
    sprite.renderOrder = 10;
    return sprite;
  }

  private planBounds(): {width: number; depth: number} {
    const drawing = this.doc.drawing;
    if (drawing && drawing.sourceWidth && drawing.scaleX) {
      return {width: Number(drawing.sourceWidth) * Number(drawing.scaleX), depth: Number(drawing.sourceHeight) * Number(drawing.scaleZ)};
    }
    const box = new THREE.Box3();
    for (const entry of this.entries.values()) box.expandByObject(entry.group);
    const size = new THREE.Vector3();
    box.getSize(size);
    return {width: Math.max(6, size.x), depth: Math.max(6, size.z)};
  }

  private applyVisibility(): void {
    this.underlayGroup.visible = this.showUnderlay;
    this.gridGroup.visible = this.showGrid;
    this.labelGroup.visible = this.showLabels;
    for (const group of this.furnitureGroups.values()) group.visible = group.userData.scheme === this.currentScheme;
    for (const entry of this.entries.values()) {
      if (entry.kind !== 'architecture') continue;
      const ceiling = entry.data.type === 'ceiling';
      entry.group.visible = ceiling ? this.showCeiling : entry.data.visible !== false;
    }
  }

  private onPointerDown = (event: PointerEvent): void => {
    this.down = {x: event.clientX, y: event.clientY, button: event.button};
  };

  private onPointerUp = (event: PointerEvent): void => {
    if (!this.down || this.down.button !== 0 || event.button !== 0) return;
    const moved = Math.hypot(event.clientX - this.down.x, event.clientY - this.down.y);
    this.down = null;
    if (moved > 5) return;
    const axis = (this.transform as unknown as {axis?: string | null}).axis;
    if (axis) return;
    const rect = this.renderer.domElement.getBoundingClientRect();
    this.pointer.set(((event.clientX - rect.left) / rect.width) * 2 - 1, -((event.clientY - rect.top) / rect.height) * 2 + 1);
    this.raycaster.setFromCamera(this.pointer, this.camera);
    const targets: THREE.Object3D[] = [this.architectureGroup, ...[...this.furnitureGroups.values()].filter((group) => group.visible)];
    const hits = this.raycaster.intersectObjects(targets, true);
    const hit = hits.find((item) => item.object.visible && !(item.object as THREE.Sprite).isSprite);
    if (!hit) {
      this.select(null);
      return;
    }
    let node: THREE.Object3D | null = hit.object;
    while (node && !node.userData.entryKey) node = node.parent;
    if (node?.userData.objectId) this.select(String(node.userData.objectId));
  };

  private onKeyDown = (event: KeyboardEvent): void => {
    const target = event.target as HTMLElement | null;
    if (target && /^(INPUT|TEXTAREA|SELECT)$/.test(target.tagName)) return;
    if (event.key === 'Escape') this.clearSelection();
  };

  private onVisibility = (): void => {
    this.lastFrame = performance.now();
  };

  private syncSelected(): void {
    if (!this.selectedId) return;
    const entry = this.entries.get(this.selectedId) || [...this.entries.values()].find((item) => item.data.id === this.selectedId);
    if (!entry) return;
    entry.data.pos = [Number(entry.group.position.x.toFixed(4)), Number(entry.group.position.y.toFixed(4)), Number(entry.group.position.z.toFixed(4))];
    entry.data.rotation = Number(entry.group.rotation.y.toFixed(5));
    this.helper.setFromObject(entry.group);
    this.callbacks.onChange('transform');
  }

  private findEntry(id: string): Entry | null {
    return this.entries.get(id) || [...this.entries.values()].find((entry) => entry.data.id === id) || null;
  }

  select(id: string | null): void {
    if (!id) {
      this.clearSelection();
      return;
    }
    const entry = this.findEntry(id);
    if (!entry) return;
    this.selectedId = id;
    this.helper.setFromObject(entry.group);
    this.helper.visible = true;
    this.transform.attach(entry.group);
    this.callbacks.onSelect(entry.data, entry.kind);
  }

  clearSelection(): void {
    this.selectedId = null;
    this.helper.visible = false;
    this.transform.detach();
    this.callbacks.onSelect(null, null);
  }

  getSelected(): SceneObject | null {
    const entry = this.selectedId ? this.findEntry(this.selectedId) : null;
    return entry ? entry.data : null;
  }

  setTransformMode(mode: 'translate' | 'rotate'): void {
    this.transform.setMode(mode);
  }

  setScheme(schemeId: string): void {
    if (!this.doc.schemes[schemeId]) return;
    const selected = this.selectedId;
    this.currentScheme = schemeId;
    this.buildDocument();
    if (selected && this.findEntry(selected)) this.select(selected);
    this.callbacks.onChange('scheme');
  }

  getScheme(): string {
    return this.currentScheme;
  }

  getDocument(): SceneDocument {
    return this.doc;
  }

  setCeilingVisible(visible: boolean): void {
    this.showCeiling = visible;
    this.applyVisibility();
  }

  setUnderlayVisible(visible: boolean): void {
    this.showUnderlay = visible;
    this.applyVisibility();
  }

  setGridVisible(visible: boolean): void {
    this.showGrid = visible;
    this.applyVisibility();
  }

  setLabelsVisible(visible: boolean): void {
    this.showLabels = visible;
    this.applyVisibility();
  }

  fit(): void {
    this.camera.up.set(0, 1, 0);
    const box = new THREE.Box3();
    const target = new THREE.Vector3();
    for (const group of [this.architectureGroup, ...this.furnitureGroups.values()]) {
      if (!group.children.length) continue;
      box.expandByObject(group);
    }
    if (box.isEmpty()) {
      const drawing = this.doc.drawing;
      const width = Number(drawing?.sourceWidth || 8) * Number(drawing?.scaleX || 1);
      const depth = Number(drawing?.sourceHeight || 6) * Number(drawing?.scaleZ || 1);
      box.setFromCenterAndSize(new THREE.Vector3(0, 1, 0), new THREE.Vector3(width, 2.8, depth));
    }
    box.getCenter(target);
    const size = new THREE.Vector3();
    box.getSize(size);
    const radius = Math.max(size.x, size.z, size.y * 0.7, 2.5) * 0.5;
    const distance = radius / Math.tan(THREE.MathUtils.degToRad(this.camera.fov * 0.5)) * 1.55;
    this.controls.target.copy(target);
    this.camera.position.set(target.x + distance * 0.58, target.y + distance * 1.25, target.z + distance * 0.68);
    this.camera.near = Math.max(0.03, distance / 400);
    this.camera.far = Math.max(120, distance * 8);
    this.camera.updateProjectionMatrix();
    this.controls.update();
  }

  topView(): void {
    const target = this.controls.target.clone();
    const box = new THREE.Box3().setFromObject(this.architectureGroup);
    const size = new THREE.Vector3();
    box.getSize(size);
    const distance = Math.max(size.x, size.z, 5) * 1.45;
    this.camera.position.set(target.x, target.y + distance, target.z + 0.001);
    this.camera.up.set(0, 0, -1);
    this.camera.lookAt(target);
    this.controls.target.copy(target);
    this.controls.update();
  }

  focusRoom(id: string): void {
    const room = (this.doc.rooms || []).find((item) => item.id === id);
    if (!room) return;
    const center = room.center || [0, 0];
    const size = room.size || [3, 3];
    const target = new THREE.Vector3(Number(center[0]), 1.0, Number(center[1]));
    const distance = Math.max(Number(size[0]), Number(size[1]), 2.5) * 1.25;
    this.controls.target.copy(target);
    this.camera.position.set(target.x + distance * 0.55, target.y + distance * 0.8, target.z + distance * 0.7);
    this.camera.up.set(0, 1, 0);
    this.controls.update();
  }

  beginMutation(): void {
    if (this.pendingBefore === null) this.pendingBefore = JSON.stringify(this.doc);
  }

  commitMutation(): void {
    if (this.pendingBefore === null) return;
    if (this.pendingBefore !== JSON.stringify(this.doc)) {
      this.undoStack.push(this.pendingBefore);
      if (this.undoStack.length > 40) this.undoStack.shift();
      this.redoStack = [];
      this.callbacks.onHistory();
    }
    this.pendingBefore = null;
  }

  undo(): void {
    const previous = this.undoStack.pop();
    if (!previous) return;
    this.redoStack.push(JSON.stringify(this.doc));
    this.doc = JSON.parse(previous) as SceneDocument;
    this.currentScheme = this.doc.schemes[this.currentScheme] ? this.currentScheme : (this.doc.style || 'base');
    this.buildDocument();
    this.callbacks.onHistory();
  }

  redo(): void {
    const next = this.redoStack.pop();
    if (!next) return;
    this.undoStack.push(JSON.stringify(this.doc));
    this.doc = JSON.parse(next) as SceneDocument;
    this.currentScheme = this.doc.schemes[this.currentScheme] ? this.currentScheme : (this.doc.style || 'base');
    this.buildDocument();
    this.callbacks.onHistory();
  }

  canUndo(): boolean { return this.undoStack.length > 0; }
  canRedo(): boolean { return this.redoStack.length > 0; }

  addFurniture(item: CatalogItem): SceneObject {
    this.beginMutation();
    const scheme = this.doc.schemes[this.currentScheme];
    if (!scheme) throw new Error('当前方案不可编辑');
    const room = (this.doc.rooms || [])[0];
    const center = room?.center || [0, 0];
    const size = [...item.size] as [number, number, number];
    const object: SceneObject = {
      id: `human-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 6)}`,
      name: item.name, type: item.type, layer: 'design',
      room: room ? room.id : null,
      pos: [Number(center[0]) || 0, size[1] / 2, Number(center[1]) || 0],
      size, rotation: 0, visible: true, color: item.color,
      material: {preset: item.preset},
      furnitureDetail: {version: 'furniture-detail/1.0', styleId: this.currentScheme, procedural: true},
    };
    scheme.objects.push(object);
    this.rebuildPreserving(object.id);
    this.commitMutation();
    return object;
  }

  duplicateSelected(): void {
    const entry = this.selectedId ? this.findEntry(this.selectedId) : null;
    if (!entry || entry.kind !== 'furniture') return;
    this.beginMutation();
    const scheme = this.doc.schemes[this.currentScheme];
    if (!scheme) return;
    const copy = cloneScene(entry.data);
    copy.id = `human-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 6)}`;
    copy.name = `${copy.name} 副本`;
    copy.pos = [copy.pos[0] + 0.35, copy.pos[1], copy.pos[2] + 0.35];
    scheme.objects.push(copy);
    this.rebuildPreserving(copy.id);
    this.commitMutation();
  }

  removeSelected(): void {
    const entry = this.selectedId ? this.findEntry(this.selectedId) : null;
    if (!entry || entry.kind !== 'furniture') return;
    this.beginMutation();
    const scheme = this.doc.schemes[this.currentScheme];
    if (!scheme) return;
    scheme.objects = scheme.objects.filter((item) => item.id !== entry.data.id);
    this.rebuildPreserving(null);
    this.commitMutation();
  }

  updateSelected(patch: Partial<SceneObject>): void {
    const entry = this.selectedId ? this.findEntry(this.selectedId) : null;
    if (!entry) return;
    this.beginMutation();
    Object.assign(entry.data, patch);
    const structural = 'size' in patch || 'type' in patch || 'material' in patch || 'color' in patch || 'visible' in patch;
    if (structural) {
      this.rebuildPreserving(entry.data.id);
    } else {
      entry.group.position.set(...(entry.data.pos || [0, 0, 0]));
      entry.group.rotation.y = Number(entry.data.rotation || 0);
      this.helper.setFromObject(entry.group);
    }
    this.callbacks.onChange('edit');
    this.commitMutation();
  }

  private rebuildPreserving(id: string | null): void {
    this.buildDocument();
    if (id && this.findEntry(id)) this.select(id);
    this.callbacks.onChange('rebuild');
  }

  loadScene(doc: SceneDocument): void {
    this.doc = cloneScene(doc);
    this.currentScheme = this.doc.style && this.doc.schemes[this.doc.style] ? this.doc.style : (Object.keys(this.doc.schemes)[0] || 'base');
    this.undoStack = [];
    this.redoStack = [];
    this.buildDocument();
    this.callbacks.onHistory();
    this.callbacks.onChange('load');
  }

  snapshot(): SceneDocument {
    return cloneScene(this.doc);
  }

  stats(): {rooms: number; architecture: number; furniture: number; triangles: number} {
    return {
      rooms: this.doc.rooms?.length || 0,
      architecture: this.doc.architecture?.length || 0,
      furniture: this.doc.schemes[this.currentScheme]?.objects?.length || 0,
      triangles: this.renderer.info.render.triangles,
    };
  }

  async exportGlb(): Promise<Blob> {
    const clone = this.modelGroup.clone(true);
    const drop: THREE.Object3D[] = [];
    clone.traverse((node) => {
      if (node.userData.scheme && node.userData.scheme !== this.currentScheme) drop.push(node);
      if (node.userData.kind === 'architecture' && node.userData.objectId) {
        const entry = this.findEntry(node.userData.objectId);
        if (entry?.data.type === 'ceiling' && !this.showCeiling) drop.push(node);
      }
    });
    for (const node of drop) node.parent?.remove(node);
    const exporter = new GLTFExporter();
    const result = await new Promise<ArrayBuffer>((resolve, reject) => {
      exporter.parse(clone, (data) => resolve(data as ArrayBuffer), (error) => reject(error), {
        binary: true, onlyVisible: true, embedImages: true, maxTextureSize: 1024,
      });
    });
    disposeMaterials(clone);
    return new Blob([result], {type: 'model/gltf-binary'});
  }

  private resize(): void {
    const width = Math.max(1, this.container.clientWidth);
    const height = Math.max(1, this.container.clientHeight);
    this.renderer.setSize(width, height, false);
    this.camera.aspect = width / height;
    this.camera.updateProjectionMatrix();
  }

  private animate = (): void => {
    if (this.disposed) return;
    this.animation = requestAnimationFrame(this.animate);
    if (document.hidden) return;
    this.controls.update();
    this.renderer.render(this.scene, this.camera);
    this.frameCount += 1;
    if (this.frameCount % 30 === 0) {
      this.callbacks.onStatus(`已渲染 ${this.renderer.info.render.triangles.toLocaleString()} 个三角面`);
    }
  };

  dispose(): void {
    this.disposed = true;
    cancelAnimationFrame(this.animation);
    this.resizeObserver.disconnect();
    this.renderer.domElement.removeEventListener('pointerdown', this.onPointerDown);
    this.renderer.domElement.removeEventListener('pointerup', this.onPointerUp);
    window.removeEventListener('keydown', this.onKeyDown);
    document.removeEventListener('visibilitychange', this.onVisibility);
    this.transform.detach();
    this.transform.dispose();
    this.controls.dispose();
    disposeMaterials(this.modelGroup);
    this.renderer.dispose();
    this.renderer.domElement.remove();
    this.underlayGroup.traverse((node) => {
      const mesh = node as THREE.Mesh;
      const material = mesh.material as THREE.MeshBasicMaterial | undefined;
      material?.map?.dispose();
      material?.dispose();
    });
    this.labelGroup.traverse((node) => {
      const sprite = node as THREE.Sprite;
      sprite.material?.map?.dispose();
      sprite.material?.dispose();
    });
  }
}
