import * as THREE from 'three';
import type {Department} from './data';

/** Canvas textures and shared geometry for the institute scene. */
const textures = new Map<string, THREE.Texture>();
const geometries = new Map<string, THREE.BufferGeometry>();

function canvasTexture(key: string, width: number, height: number, draw: (ctx: CanvasRenderingContext2D, w: number, h: number) => void): THREE.CanvasTexture {
  const cached = textures.get(key);
  if (cached) return cached as THREE.CanvasTexture;
  const canvas = document.createElement('canvas');
  canvas.width = width;
  canvas.height = height;
  const ctx = canvas.getContext('2d')!;
  draw(ctx, width, height);
  const texture = new THREE.CanvasTexture(canvas);
  texture.colorSpace = THREE.SRGBColorSpace;
  texture.anisotropy = 4;
  textures.set(key, texture);
  return texture;
}

export function woodFloorTexture(): THREE.CanvasTexture {
  const texture = canvasTexture('floor-wood', 1024, 1024, (ctx, w, h) => {
    ctx.fillStyle = '#d9c3a0';
    ctx.fillRect(0, 0, w, h);
    const plank = 128;
    for (let row = 0; row < h / plank; row++) {
      const offset = row % 2 ? plank / 2 : 0;
      for (let x = -plank; x < w + plank; x += 352) {
        const tone = 214 + Math.round(Math.sin(row * 2.3 + x * 0.01) * 9);
        ctx.fillStyle = `rgb(${tone + 8},${tone - 6},${tone - 44})`;
        ctx.fillRect(x + offset + 2, row * plank + 1, 348, plank - 3);
        ctx.strokeStyle = 'rgba(120,96,66,.18)';
        ctx.lineWidth = 1;
        for (let line = 0; line < 5; line++) {
          ctx.beginPath();
          const y = row * plank + 12 + line * 24 + Math.sin(row + line) * 4;
          ctx.moveTo(x + offset + 4, y);
          for (let step = 0; step <= 6; step++) ctx.lineTo(x + offset + 4 + step * 58, y + Math.sin(step * 1.7 + row) * 2.2);
          ctx.stroke();
        }
      }
      ctx.fillStyle = 'rgba(96,74,50,.28)';
      ctx.fillRect(0, row * plank, w, 2);
    }
  });
  texture.wrapS = texture.wrapT = THREE.RepeatWrapping;
  texture.repeat.set(4.4, 2.6);
  return texture;
}

export function corridorTexture(): THREE.CanvasTexture {
  const texture = canvasTexture('floor-corridor', 512, 512, (ctx, w, h) => {
    ctx.fillStyle = '#cdb895';
    ctx.fillRect(0, 0, w, h);
    for (let row = 0; row < 8; row++) {
      ctx.fillStyle = row % 2 ? '#c8b18c' : '#d4bd99';
      ctx.fillRect(0, row * 64, w, 62);
      ctx.fillStyle = 'rgba(110,88,60,.22)';
      ctx.fillRect(0, row * 64 + 62, w, 2);
    }
    // brass inlay pair along the corridor edges
    ctx.fillStyle = '#b99a62';
    ctx.fillRect(0, 26, w, 4);
    ctx.fillRect(0, h - 30, w, 4);
  });
  texture.wrapS = texture.wrapT = THREE.RepeatWrapping;
  texture.repeat.set(8, 1);
  return texture;
}

export function drawingTexture(key: string, options: {title: string; mode?: 'plan' | 'section' | 'site'}): THREE.CanvasTexture {
  return canvasTexture(key, 512, 640, (ctx, w, h) => {
    ctx.fillStyle = '#f4f0e6';
    ctx.fillRect(0, 0, w, h);
    ctx.strokeStyle = '#d9d2c3';
    ctx.lineWidth = 1;
    for (let x = 24; x < w; x += 32) {
      ctx.beginPath();
      ctx.moveTo(x, 0);
      ctx.lineTo(x, h);
      ctx.stroke();
    }
    for (let y = 24; y < h; y += 32) {
      ctx.beginPath();
      ctx.moveTo(0, y);
      ctx.lineTo(w, y);
      ctx.stroke();
    }
    ctx.strokeStyle = '#4c4f4c';
    ctx.lineWidth = 3;
    if (options.mode === 'site') {
      for (let i = 0; i < 9; i++) {
        ctx.beginPath();
        for (let x = 20; x <= w - 20; x += 12) {
          const y = 90 + i * 52 + Math.sin((x + i * 30) * 0.018) * 22;
          x === 20 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
        }
        ctx.stroke();
      }
      ctx.strokeStyle = '#b08a4e';
      ctx.lineWidth = 4;
      ctx.strokeRect(110, 190, 280, 250);
      ctx.beginPath();
      ctx.moveTo(60, 500);
      ctx.lineTo(450, 470);
      ctx.stroke();
    } else if (options.mode === 'section') {
      ctx.strokeRect(60, 120, 390, 300);
      for (let i = 0; i < 5; i++) {
        ctx.beginPath();
        ctx.moveTo(60 + i * 78, 120);
        ctx.lineTo(60 + i * 78, 420);
        ctx.stroke();
      }
      ctx.strokeStyle = '#a98a55';
      ctx.lineWidth = 2;
      for (let i = 0; i < 4; i++) {
        ctx.beginPath();
        ctx.moveTo(60, 160 + i * 62);
        ctx.lineTo(450, 160 + i * 62);
        ctx.stroke();
      }
    } else {
      ctx.strokeRect(70, 90, 370, 400);
      ctx.strokeRect(120, 150, 130, 160);
      ctx.strokeRect(280, 150, 110, 90);
      ctx.strokeRect(280, 270, 110, 170);
      ctx.strokeRect(120, 340, 130, 110);
      ctx.beginPath();
      ctx.arc(200, 260, 46, 0, Math.PI * 2);
      ctx.stroke();
      ctx.fillStyle = '#bc9a63';
      ctx.fillRect(120, 150, 16, 160);
    }
    ctx.fillStyle = '#3f423f';
    ctx.font = '600 22px "Microsoft YaHei", sans-serif';
    ctx.fillText(options.title, 70, h - 70);
    ctx.font = '13px "Segoe UI", sans-serif';
    ctx.fillStyle = '#8d887c';
    ctx.fillText('SUPER ARCHITECT · LOCAL DESIGN STUDY', 70, h - 44);
  });
}

export function siteModelTexture(): THREE.CanvasTexture {
  return canvasTexture('site-model-plan', 512, 384, (ctx, w, h) => {
    ctx.fillStyle = '#e7e1d1';
    ctx.fillRect(0, 0, w, h);
    ctx.strokeStyle = '#9aa585';
    ctx.lineWidth = 2;
    for (let i = 0; i < 7; i++) {
      ctx.beginPath();
      for (let x = 0; x <= w; x += 16) {
        const y = 60 + i * 44 + Math.sin((x + i * 45) * 0.02) * 18;
        x === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
      }
      ctx.stroke();
    }
    ctx.strokeStyle = '#b3925c';
    ctx.lineWidth = 3;
    ctx.beginPath();
    ctx.moveTo(30, 330);
    ctx.bezierCurveTo(160, 300, 300, 170, 480, 120);
    ctx.stroke();
    ctx.fillStyle = '#6f7d63';
    for (let i = 0; i < 14; i++) {
      ctx.beginPath();
      ctx.arc(50 + ((i * 71) % 420), 90 + ((i * 53) % 250), 7, 0, Math.PI * 2);
      ctx.fill();
    }
  });
}

export function screenTexture(kind: 'bim' | 'plan' | 'mep' | 'material'): THREE.CanvasTexture {
  return canvasTexture('screen-' + kind, 768, 480, (ctx, w, h) => {
    ctx.fillStyle = '#171b1a';
    ctx.fillRect(0, 0, w, h);
    const line = 'rgba(158,205,187,.6)';
    if (kind === 'bim') {
      ctx.strokeStyle = line;
      ctx.lineWidth = 1.4;
      const cx = w * 0.52, base = h * 0.78;
      const corners = [[-170, 0], [120, 0], [120, -60], [-170, -60]];
      for (let level = 0; level < 9; level++) {
        const y = base - level * 34;
        ctx.beginPath();
        corners.forEach(([dx, dy], i) => (i ? ctx.lineTo(cx + dx, y + dy) : ctx.moveTo(cx + dx, y + dy)));
        ctx.closePath();
        ctx.stroke();
      }
      for (const [dx, dy] of corners) {
        ctx.beginPath();
        ctx.moveTo(cx + dx, base + dy);
        ctx.lineTo(cx + dx, base - 9 * 34 + dy);
        ctx.stroke();
      }
      ctx.strokeStyle = 'rgba(224,199,144,.55)';
      for (let i = 0; i < 12; i++) {
        ctx.beginPath();
        ctx.moveTo(cx - 170 + i * 25, base);
        ctx.lineTo(cx - 170 + i * 25, base - 8 * 34);
        ctx.stroke();
      }
      ctx.fillStyle = '#d9c08c';
      ctx.font = '600 20px "Microsoft YaHei", sans-serif';
      ctx.fillText('BIM 模型 · 结构轴网', 32, 54);
    } else if (kind === 'mep') {
      ctx.strokeStyle = 'rgba(151,190,208,.72)';
      ctx.lineWidth = 6;
      ctx.beginPath();
      ctx.moveTo(60, 120);
      ctx.lineTo(300, 120);
      ctx.lineTo(300, 300);
      ctx.stroke();
      ctx.strokeStyle = 'rgba(207,180,128,.6)';
      ctx.lineWidth = 4;
      ctx.beginPath();
      ctx.moveTo(60, 170);
      ctx.lineTo(420, 170);
      ctx.lineTo(420, 380);
      ctx.stroke();
      ctx.fillStyle = '#9fb7c2';
      ctx.font = '600 20px "Microsoft YaHei", sans-serif';
      ctx.fillText('风管与管线综合', 32, 54);
    } else if (kind === 'material') {
      const swatches = ['#b99a73', '#8d7a63', '#d8cdba', '#48514c', '#c5a98c', '#6f7c74'];
      swatches.forEach((color, i) => {
        ctx.fillStyle = color;
        ctx.fillRect(60 + (i % 3) * 200, 100 + Math.floor(i / 3) * 150, 170, 120);
      });
      ctx.strokeStyle = '#7d766a';
      ctx.strokeRect(60, 100, 530, 270);
      ctx.fillStyle = '#e7e0d2';
      ctx.font = '600 20px "Microsoft YaHei", sans-serif';
      ctx.fillText('材料样板 · 木饰面 / 石材 / 织物', 32, 54);
    } else {
      ctx.strokeStyle = 'rgba(222,194,141,.66)';
      ctx.lineWidth = 2;
      ctx.strokeRect(80, 90, 610, 320);
      ctx.strokeRect(140, 150, 230, 200);
      ctx.strokeRect(400, 150, 220, 100);
      ctx.strokeRect(400, 280, 220, 70);
      ctx.beginPath();
      ctx.moveTo(370, 90);
      ctx.lineTo(370, 410);
      ctx.stroke();
      ctx.fillStyle = '#e0c790';
      ctx.font = '600 20px "Microsoft YaHei", sans-serif';
      ctx.fillText('方案评审 · 三层平面', 32, 54);
    }
    ctx.fillStyle = 'rgba(255,255,255,.14)';
    ctx.fillRect(0, 0, w, 3);
  });
}

export function signTexture(dept: Department): THREE.Texture {
  return canvasTexture('sign-' + dept.id, 768, 192, (ctx, w, h) => {
    ctx.fillStyle = '#f2ebdd';
    ctx.fillRect(0, 0, w, h);
    ctx.strokeStyle = '#b08c53';
    ctx.lineWidth = 4;
    ctx.strokeRect(6, 6, w - 12, h - 12);
    ctx.fillStyle = '#e5d7bc';
    ctx.fillRect(10, h - 10, w - 20, 3);
    ctx.fillStyle = '#987a45';
    ctx.font = '600 26px "Segoe UI", sans-serif';
    ctx.fillText(dept.roomCode, 26, 46);
    let size = 104;
    const name = dept.plaque;
    do {
      ctx.font = `600 ${size}px "Microsoft YaHei", "Segoe UI", sans-serif`;
      size -= 4;
    } while (ctx.measureText(name).width > w - 56 && size > 54);
    ctx.fillStyle = '#22262a';
    ctx.fillText(name, 24, 136);
    ctx.fillStyle = '#8b8172';
    ctx.font = '13px "Segoe UI", sans-serif';
    ctx.fillText(dept.nameEn, 28, 168);
  });
}

export function directoryTexture(): THREE.CanvasTexture {
  return canvasTexture('directory', 640, 420, (ctx, w, h) => {
    ctx.fillStyle = '#f0eadf';
    ctx.fillRect(0, 0, w, h);
    ctx.fillStyle = '#2b2e2c';
    ctx.fillRect(0, 0, w, 64);
    ctx.fillStyle = '#e0c790';
    ctx.font = '600 24px "Microsoft YaHei", sans-serif';
    ctx.fillText('院区索引 · DEPARTMENT DIRECTORY', 26, 42);
    const rows = [['A-01', '建筑设计所'], ['A-02', '规划与景观所'], ['A-03', '结构设计所'], ['B-01', '机电设计所'], ['B-02', '室内设计所'], ['B-03', 'BIM 与数字技术中心'], ['R-00', '总师办评审区']];
    ctx.font = '18px "Microsoft YaHei", sans-serif';
    rows.forEach(([code, name], i) => {
      const y = 112 + i * 42;
      ctx.fillStyle = '#9b7c49';
      ctx.fillText(code, 30, y);
      ctx.fillStyle = '#34383a';
      ctx.fillText(name, 100, y);
      ctx.strokeStyle = 'rgba(90,86,76,.2)';
      ctx.beginPath();
      ctx.moveTo(26, y + 14);
      ctx.lineTo(w - 26, y + 14);
      ctx.stroke();
    });
    ctx.fillStyle = '#b08d58';
    ctx.beginPath();
    ctx.moveTo(w - 120, 130);
    ctx.lineTo(w - 60, 130);
    ctx.lineTo(w - 60, 118);
    ctx.lineTo(w - 34, 137);
    ctx.lineTo(w - 60, 156);
    ctx.lineTo(w - 60, 144);
    ctx.lineTo(w - 120, 144);
    ctx.closePath();
    ctx.fill();
  });
}

export function insoleTexture(): THREE.Texture {
  return canvasTexture('threshold', 512, 128, (ctx, w, h) => {
    ctx.fillStyle = '#c0a267';
    ctx.fillRect(0, 0, w, h);
    ctx.fillStyle = '#9c7f4b';
    for (let i = 0; i < 8; i++) ctx.fillRect(i * 64 + 8, 0, 3, h);
    ctx.fillStyle = 'rgba(255,244,214,.28)';
    ctx.fillRect(0, 0, w, 4);
  });
}

export function geo(rt: number, rb: number, length: number, segments = 12): THREE.BufferGeometry {
  const key = `cyl:${rt}:${rb}:${length}:${segments}`;
  let value = geometries.get(key);
  if (!value) {
    value = new THREE.CylinderGeometry(rt, rb, length, segments, 1);
    value.translate(0, -length / 2, 0);
    value.userData.shared = true;
    geometries.set(key, value);
  }
  return value;
}

export function boxGeo(width: number, height: number, depth: number): THREE.BufferGeometry {
  const key = `box:${width}:${height}:${depth}`;
  let value = geometries.get(key);
  if (!value) {
    value = new THREE.BoxGeometry(width, height, depth);
    value.userData.shared = true;
    geometries.set(key, value);
  }
  return value;
}

export function sphereGeo(radius = 1, width = 20, height = 16): THREE.BufferGeometry {
  const key = `sphere:${radius}:${width}:${height}`;
  let value = geometries.get(key);
  if (!value) {
    value = new THREE.SphereGeometry(radius, width, height);
    value.userData.shared = true;
    geometries.set(key, value);
  }
  return value;
}

export function hairCapGeo(scale = 1): THREE.BufferGeometry {
  const key = 'haircap:' + scale;
  let value = geometries.get(key);
  if (!value) {
    value = new THREE.SphereGeometry(0.111 * scale, 20, 14, 0, Math.PI * 2, 0, Math.PI * 0.41);
    value.translate(0, 0.132 * scale, -0.004);
    value.userData.shared = true;
    geometries.set(key, value);
  }
  return value;
}

export function disposeInstituteTextures() {
  textures.forEach(item => item.dispose());
  textures.clear();
  geometries.forEach(item => item.dispose());
  geometries.clear();
}
