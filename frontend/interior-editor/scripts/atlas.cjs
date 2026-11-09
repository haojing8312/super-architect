'use strict';
/** Deterministic, public-domain-style procedural material atlas (no downloaded imagery). */
const zlib = require('zlib');

const SIZE = 2048;
const TILE = 512;
const GRID = 4;

const CRC_TABLE = (() => {
  const table = new Uint32Array(256);
  for (let n = 0; n < 256; n += 1) {
    let c = n;
    for (let k = 0; k < 8; k += 1) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
    table[n] = c >>> 0;
  }
  return table;
})();

function crc32(buffer) {
  let crc = 0xffffffff;
  for (let index = 0; index < buffer.length; index += 1) crc = CRC_TABLE[(crc ^ buffer[index]) & 0xff] ^ (crc >>> 8);
  return (crc ^ 0xffffffff) >>> 0;
}

function chunk(type, data) {
  const length = Buffer.alloc(4);
  length.writeUInt32BE(data.length, 0);
  const name = Buffer.from(type, 'ascii');
  const crc = Buffer.alloc(4);
  crc.writeUInt32BE(crc32(Buffer.concat([name, data])), 0);
  return Buffer.concat([length, name, data, crc]);
}

function encodePNG(width, height, rgb) {
  const stride = width * 3 + 1;
  const raw = Buffer.alloc(stride * height);
  for (let y = 0; y < height; y += 1) {
    raw[y * stride] = 0;
    rgb.copy(raw, y * stride + 1, y * width * 3, (y + 1) * width * 3);
  }
  const header = Buffer.alloc(13);
  header.writeUInt32BE(width, 0);
  header.writeUInt32BE(height, 4);
  header[8] = 8;
  header[9] = 2;
  header[10] = 0;
  header[11] = 0;
  header[12] = 0;
  return Buffer.concat([
    Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]),
    chunk('IHDR', header),
    chunk('IDAT', zlib.deflateSync(raw, {level: 9})),
    chunk('IEND', Buffer.alloc(0)),
  ]);
}

function hash2(x, y) {
  let value = (x * 374761393 + y * 668265263) | 0;
  value = (value ^ (value >>> 13)) * 1274126177 | 0;
  return ((value ^ (value >>> 16)) >>> 0) / 4294967295;
}

function periodicNoise(x, y, period) {
  const px = ((x % period) + period) % period;
  const py = ((y % period) + period) % period;
  const ix = Math.floor(px);
  const iy = Math.floor(py);
  const fx = px - ix;
  const fy = py - iy;
  const ux = fx * fx * (3 - 2 * fx);
  const uy = fy * fy * (3 - 2 * fy);
  const a = hash2(ix % period, iy % period);
  const b = hash2((ix + 1) % period, iy % period);
  const c = hash2(ix % period, (iy + 1) % period);
  const d = hash2((ix + 1) % period, (iy + 1) % period);
  return (a * (1 - ux) + b * ux) * (1 - uy) + (c * (1 - ux) + d * ux) * uy;
}

function fbm(x, y, period = 16, octaves = 4) {
  let value = 0;
  let amplitude = 0.5;
  let frequency = 1;
  for (let octave = 0; octave < octaves; octave += 1) {
    value += periodicNoise((x / TILE) * period * frequency, (y / TILE) * period * frequency, period) * amplitude;
    amplitude *= 0.5;
    frequency *= 2;
  }
  return value;
}

function clamp(value) {
  return value < 0 ? 0 : value > 1 ? 1 : value;
}

function gray(value) {
  const level = Math.round(clamp(value) * 255);
  return [level, level, level];
}

function shade(base, delta) {
  return gray(clamp(base + delta));
}

function tilePattern(row, col, x, y) {
  const u = x / TILE;
  const v = y / TILE;
  switch (row * GRID + col) {
    case 0: { // oak
      const grain = fbm(x * 0.5, y * 1.6, 10, 3);
      const line = Math.sin((v * 9 + grain * 2.2) * Math.PI * 2) * 0.06;
      return shade(0.86, line + (grain - 0.5) * 0.12);
    }
    case 1: { // walnut
      const grain = fbm(x * 0.7, y * 2.2, 8, 4);
      const line = Math.sin((v * 12 + grain * 3) * Math.PI * 2) * 0.09;
      return shade(0.7, line + (grain - 0.5) * 0.18);
    }
    case 2: { // ash
      const grain = fbm(x * 0.4, y * 1.2, 14, 3);
      const line = Math.sin((v * 7 + grain * 2) * Math.PI * 2) * 0.04;
      return shade(0.9, line + (grain - 0.5) * 0.09);
    }
    case 3: { // limewash
      const n = fbm(x, y, 6, 5);
      const cloud = fbm(x * 0.4 + 40, y * 0.4, 5, 4);
      return shade(0.93, (n - 0.5) * 0.05 + (cloud - 0.5) * 0.08);
    }
    case 4: { // plaster
      const n = fbm(x, y, 12, 4);
      return shade(0.95, (n - 0.5) * 0.035);
    }
    case 5: { // concrete
      const n = fbm(x, y, 9, 5);
      const speck = hash2(x + 13, y + 7);
      return shade(0.84, (n - 0.5) * 0.15 + (speck > 0.985 ? -0.12 : 0));
    }
    case 6: { // microcement
      const n = fbm(x * 0.8, y * 0.8, 7, 5);
      return shade(0.88, (n - 0.5) * 0.08);
    }
    case 7: { // marble
      const n = fbm(x * 0.6, y * 0.6, 8, 5);
      const vein = Math.abs(Math.sin((u * 3 + v * 2 + n * 2.4) * Math.PI * 2));
      return shade(0.95, -Math.pow(1 - vein, 7) * 0.22 + (n - 0.5) * 0.04);
    }
    case 8: { // terrazzo
      const base = fbm(x * 0.3, y * 0.3, 5, 3);
      const speck = hash2(x * 3 + 1, y * 3 + 5);
      const tint = speck > 0.93 ? -0.3 : speck > 0.86 ? -0.12 : 0;
      return shade(0.93, (base - 0.5) * 0.04 + tint);
    }
    case 9: { // tile
      const gx = Math.min(u, 1 - u);
      const gy = Math.min(v, 1 - v);
      const line = Math.min(gx, gy) < 0.012 ? -0.22 : 0;
      const n = fbm(x, y, 8, 3);
      return shade(0.94, line + (n - 0.5) * 0.03);
    }
    case 10: { // linen
      const weave = (Math.sin(u * Math.PI * 36) * Math.sin(v * Math.PI * 36)) * 0.035;
      const n = fbm(x, y, 20, 3);
      return shade(0.92, weave + (n - 0.5) * 0.05);
    }
    case 11: { // woven / boucle
      const weave = (Math.sin(u * Math.PI * 22) * Math.sin(v * Math.PI * 22)) * 0.06;
      const n = fbm(x, y, 14, 4);
      return shade(0.88, weave + (n - 0.5) * 0.1);
    }
    case 12: { // leather
      const cell = fbm(x * 2.4, y * 2.4, 23, 3);
      const grain = Math.pow(Math.abs(Math.sin((u + v) * Math.PI * 50 + cell * 3)), 8) * 0.08;
      return shade(0.78, (cell - 0.5) * 0.16 + grain);
    }
    case 13: { // wool / jute
      const fiber = Math.sin((u * 2 + Math.sin(v * 18)) * Math.PI * 30) * 0.03;
      const n = fbm(x, y, 26, 3);
      return shade(0.9, fiber + (n - 0.5) * 0.08);
    }
    case 14: { // brushed metal
      const streak = fbm(x * 0.08, y * 2.4, 18, 4);
      return shade(0.86, (streak - 0.5) * 0.14);
    }
    case 15: { // rattan
      const diagonal = Math.sin((u + v) * Math.PI * 28) * 0.05;
      const cross = Math.sin((u - v) * Math.PI * 28) * 0.04;
      const n = fbm(x, y, 10, 3);
      return shade(0.82, diagonal + cross + (n - 0.5) * 0.08);
    }
    default:
      return gray(0.9);
  }
}

function buildAtlas() {
  const width = SIZE;
  const height = SIZE;
  const rgb = Buffer.alloc(width * height * 3);
  for (let y = 0; y < height; y += 1) {
    const row = Math.floor(y / TILE);
    for (let x = 0; x < width; x += 1) {
      const col = Math.floor(x / TILE);
      const localX = x % TILE;
      const localY = y % TILE;
      const color = tilePattern(row, col, localX, localY);
      const offset = (y * width + x) * 3;
      rgb[offset] = color[0];
      rgb[offset + 1] = color[1];
      rgb[offset + 2] = color[2];
    }
  }
  return encodePNG(width, height, rgb);
}

module.exports = {buildAtlas, encodePNG, SIZE, TILE};

if (require.main === module) {
  const png = buildAtlas();
  process.stdout.write(JSON.stringify({bytes: png.length, width: SIZE, height: SIZE}) + '\n');
}
