/**
 * 墨参 · 加载动画素材预处理（一次性脚本，产物提交进仓库）
 *
 * 输入：D:\MiniMax Design Data\Projects\请你根据这份文档的§2 + §3 + §\*.png
 * 输出：frontend/assets/ink/*.png
 *
 * 思路：
 *   1. 以「浅色主题签名帧」为几何基准（其朱砂剑光可按色相干净分离），拆成墨迹层 + 剑光层
 *   2. 墨迹层被剑光压住的部分按邻近墨色向内生长补形，保证「行笔成弧」阶段弧线完整
 *   3. 深色主题不是再拆一张图，而是按《加载动画设计规范文档》§3.4 的深色配色对同一套
 *      alpha/密度重新着色，从工程上保证两套主题严格同形同构（对应 §2.5「参数化而非导出两份」）
 *   4. 墨晕墨点纹理拆成 1 个大墨点 + 2 个溅点；笔尖、飞白、剑气辉光裁边缩放
 */
const fs = require('fs');
const path = require('path');
const { decodePNG, encodePNG } = require('./pngcodec');

const SRC = 'D:\\MiniMax Design Data\\Projects\\请你根据这份文档的§2 + §3 + §';
const OUT = path.join(__dirname, '..', 'frontend', 'assets', 'ink');
fs.mkdirSync(OUT, { recursive: true });

const FRAME = 768; // 输出统一帧尺寸（1024 逻辑画布按此比例缩放，各层共用同一帧，无需记录偏移）

// ---------------- 基础图像操作 ----------------

function load(name) {
  const img = decodePNG(path.join(SRC, name));
  const data = new Uint8ClampedArray(img.width * img.height * 4);
  for (let y = 0; y < img.height; y++) {
    for (let x = 0; x < img.width; x++) {
      const o = (y * img.width + x) * 4;
      const p = img.get(x, y);
      data[o] = p[0]; data[o + 1] = p[1]; data[o + 2] = p[2]; data[o + 3] = p[3];
    }
  }
  return { w: img.width, h: img.height, data };
}

function save(img, name) {
  const buf = encodePNG(img.w, img.h, Buffer.from(img.data.buffer, img.data.byteOffset, img.data.length));
  fs.writeFileSync(path.join(OUT, name), buf);
  console.log(`  → ${name}  ${img.w}x${img.h}  ${(buf.length / 1024).toFixed(1)}KB`);
  return buf.length;
}

function boxDown(src, scale) {
  const ow = Math.max(1, Math.round(src.w * scale)), oh = Math.max(1, Math.round(src.h * scale));
  const out = { w: ow, h: oh, data: new Uint8ClampedArray(ow * oh * 4) };
  for (let y = 0; y < oh; y++) {
    for (let x = 0; x < ow; x++) {
      let ra = 0, ga = 0, ba = 0, aa = 0, n = 0;
      const sx0 = x / scale, sx1 = (x + 1) / scale, sy0 = y / scale, sy1 = (y + 1) / scale;
      for (let sy = Math.floor(sy0); sy < Math.ceil(sy1); sy++) {
        for (let sx = Math.floor(sx0); sx < Math.ceil(sx1); sx++) {
          if (sx < 0 || sy < 0 || sx >= src.w || sy >= src.h) continue;
          const o = (sy * src.w + sx) * 4;
          const al = src.data[o + 3] / 255;
          ra += src.data[o] * al; ga += src.data[o + 1] * al; ba += src.data[o + 2] * al;
          aa += src.data[o + 3]; n++;
        }
      }
      if (!n) continue;
      const t = (y * ow + x) * 4;
      const norm = aa > 0 ? aa / 255 : 1;
      out.data[t] = ra / norm;
      out.data[t + 1] = ga / norm;
      out.data[t + 2] = ba / norm;
      out.data[t + 3] = aa / n;
    }
  }
  return out;
}

function cropTo(src, b) {
  const out = { w: b.w, h: b.h, data: new Uint8ClampedArray(b.w * b.h * 4) };
  for (let y = 0; y < b.h; y++) {
    for (let x = 0; x < b.w; x++) {
      const o = ((b.y + y) * src.w + (b.x + x)) * 4;
      const t = (y * b.w + x) * 4;
      out.data[t] = src.data[o]; out.data[t + 1] = src.data[o + 1];
      out.data[t + 2] = src.data[o + 2]; out.data[t + 3] = src.data[o + 3];
    }
  }
  return out;
}

function bbox(img, thresh = 6) {
  let minX = img.w, minY = img.h, maxX = -1, maxY = -1;
  for (let y = 0; y < img.h; y++) {
    for (let x = 0; x < img.w; x++) {
      if (img.data[(y * img.w + x) * 4 + 3] > thresh) {
        if (x < minX) minX = x; if (x > maxX) maxX = x;
        if (y < minY) minY = y; if (y > maxY) maxY = y;
      }
    }
  }
  return { x: minX, y: minY, w: maxX - minX + 1, h: maxY - minY + 1 };
}

/** 可分离方形结构元的膨胀 / 腐蚀（比圆盘快一个数量级） */
function morph(mask, w, h, r, mode) {
  const isMax = mode === 'dilate';
  const tmp = new Uint8Array(w * h);
  for (let y = 0; y < h; y++) {
    for (let x = 0; x < w; x++) {
      let v = isMax ? 0 : 1;
      const x0 = Math.max(0, x - r), x1 = Math.min(w - 1, x + r);
      for (let k = x0; k <= x1; k++) {
        const s = mask[y * w + k];
        if (isMax) { if (s) { v = 1; break; } }
        else if (!s) { v = 0; break; }
      }
      tmp[y * w + x] = v;
    }
  }
  const out = new Uint8Array(w * h);
  for (let y = 0; y < h; y++) {
    for (let x = 0; x < w; x++) {
      let v = isMax ? 0 : 1;
      const y0 = Math.max(0, y - r), y1 = Math.min(h - 1, y + r);
      for (let k = y0; k <= y1; k++) {
        const s = tmp[k * w + x];
        if (isMax) { if (s) { v = 1; break; } }
        else if (!s) { v = 0; break; }
      }
      out[y * w + x] = v;
    }
  }
  return out;
}

/** 按邻近像素向内生长，填满被剑光压住的墨迹缺口 */
function inpaint(img, hole, w, h) {
  const filled = Uint8Array.from(hole, (v) => (v ? 0 : 1));
  for (let i = 0; i < w * h; i++) {
    if (hole[i]) { img.data[i * 4] = 0; img.data[i * 4 + 1] = 0; img.data[i * 4 + 2] = 0; img.data[i * 4 + 3] = 0; }
  }
  const N8 = [[-1, -1], [0, -1], [1, -1], [-1, 0], [1, 0], [-1, 1], [0, 1], [1, 1]];
  let filledCount = 0;
  for (let iter = 0; iter < 600; iter++) {
    const batch = [];
    for (let y = 0; y < h; y++) {
      for (let x = 0; x < w; x++) {
        const i = y * w + x;
        if (!hole[i] || filled[i]) continue;
        let r = 0, g = 0, b = 0, a = 0, n = 0;
        for (const [dx, dy] of N8) {
          const nx = x + dx, ny = y + dy;
          if (nx < 0 || ny < 0 || nx >= w || ny >= h) continue;
          const j = ny * w + nx;
          if (!filled[j]) continue;
          r += img.data[j * 4]; g += img.data[j * 4 + 1]; b += img.data[j * 4 + 2]; a += img.data[j * 4 + 3];
          n++;
        }
        if (n) batch.push([i, r / n, g / n, b / n, a / n]);
      }
    }
    if (!batch.length) break;
    for (const [i, r, g, b, a] of batch) {
      filled[i] = 1; filledCount++;
      img.data[i * 4] = r; img.data[i * 4 + 1] = g; img.data[i * 4 + 2] = b; img.data[i * 4 + 3] = a;
    }
  }
  return filledCount;
}

const hex = (s) => [parseInt(s.slice(1, 3), 16), parseInt(s.slice(3, 5), 16), parseInt(s.slice(5, 7), 16)];
const lerp = (a, b, t) => a + (b - a) * t;
const lum = (r, g, b) => (0.299 * r + 0.587 * g + 0.114 * b) / 255;

// ============ 1. 以浅色签名帧为基准拆分 ============
console.log('【1】签名帧拆分（基准：浅色主题签名帧）');
const light = load('浅色主题签名帧.png');
const { w: W, h: H } = light;

const slashRaw = new Uint8Array(W * H);
const arcSupport = new Uint8Array(W * H);
for (let i = 0; i < W * H; i++) {
  const r = light.data[i * 4], g = light.data[i * 4 + 1], b = light.data[i * 4 + 2], a = light.data[i * 4 + 3];
  if (a <= 8) continue;
  if (r - Math.max(g, b) > 24) slashRaw[i] = 1;
  else arcSupport[i] = 1;
}
const slashMask = morph(slashRaw, W, H, 3, 'dilate');
// 剑光压住墨迹的范围 = 剑光掩膜 ∩ 墨迹邻域（直接取邻域，避免闭运算在 S 形内凹处造出假墨）
const arcNearSlash = morph(arcSupport, W, H, 62, 'dilate');

const arc = { w: W, h: H, data: new Uint8ClampedArray(W * H * 4) };
const slash = { w: W, h: H, data: new Uint8ClampedArray(W * H * 4) };
const hole = new Uint8Array(W * H);
for (let i = 0; i < W * H; i++) {
  const o = i * 4;
  const a = light.data[o + 3];
  if (a <= 8) continue;
  if (slashMask[i]) {
    slash.data[o] = light.data[o]; slash.data[o + 1] = light.data[o + 1];
    slash.data[o + 2] = light.data[o + 2]; slash.data[o + 3] = a;
    if (arcNearSlash[i]) hole[i] = 1;
  } else {
    arc.data[o] = light.data[o]; arc.data[o + 1] = light.data[o + 1];
    arc.data[o + 2] = light.data[o + 2]; arc.data[o + 3] = a;
  }
}
let holes = 0;
for (let i = 0; i < W * H; i++) if (hole[i]) holes++;
const filled = inpaint(arc, hole, W, H);
console.log(`  剑光掩膜 ${slashRaw.reduce((a, b) => a + b, 0)}px，墨迹补形 ${filled}/${holes}px`);

// ============ 2. 深色主题重新着色（同形同构，只换色） ============
console.log('\n【2】深色主题→按深色配色重着色');
const INK_DEEP = hex('#7C5CFC');   // 墨迹浓处
const INK_LIGHT = hex('#A894FF');  // 墨迹淡处
const BLADE_CORE = hex('#FFFFFF'); // 剑光核心
const BLADE_EDGE = hex('#C9BCFF'); // 剑光边缘（外发光色）

const arcDark = { w: W, h: H, data: new Uint8ClampedArray(arc.data) };
const slashDark = { w: W, h: H, data: new Uint8ClampedArray(slash.data) };

for (let i = 0; i < W * H; i++) {
  const o = i * 4;
  if (arcDark.data[o + 3] > 0) {
    // 密度来自原图明度：墨越浓（越暗）密度越高
    const d = Math.min(1, Math.max(0, 1 - lum(arcDark.data[o], arcDark.data[o + 1], arcDark.data[o + 2]) * 1.18));
    for (let c = 0; c < 3; c++) arcDark.data[o + c] = lerp(INK_LIGHT[c], INK_DEEP[c], d);
  }
  if (slashDark.data[o + 3] > 0) {
    const t = Math.min(1, Math.max(0, (lum(slashDark.data[o], slashDark.data[o + 1], slashDark.data[o + 2]) - 0.16) / 0.26));
    for (let c = 0; c < 3; c++) slashDark.data[o + c] = lerp(BLADE_EDGE[c], BLADE_CORE[c], t);
  }
}

// ============ 3. 缩放并输出四层 ============
console.log('\n【3】输出图层（统一 768 帧）');
const scale = FRAME / W;
save(boxDown(arc, scale), 'arc-light.png');
save(boxDown(arcDark, scale), 'arc-dark.png');
save(boxDown(slash, scale), 'slash-light.png');
save(boxDown(slashDark, scale), 'slash-dark.png');

// ============ 4. 墨晕墨点纹理 → 大墨点 + 2 溅点 ============
console.log('\n【4】墨晕墨点纹理拆分');
const blobSrc = load('墨晕墨点纹理.png');
{
  const { w, h } = blobSrc;
  const seen = new Uint8Array(w * h);
  const q = new Int32Array(w * h);
  const comps = [];
  for (let s = 0; s < w * h; s++) {
    if (seen[s] || blobSrc.data[s * 4 + 3] < 60) continue;
    let head = 0, tail = 0;
    q[tail++] = s; seen[s] = 1;
    let minX = w, minY = h, maxX = -1, maxY = -1, count = 0;
    while (head < tail) {
      const cur = q[head++];
      const cx = cur % w, cy = (cur - cx) / w;
      count++;
      if (cx < minX) minX = cx; if (cx > maxX) maxX = cx;
      if (cy < minY) minY = cy; if (cy > maxY) maxY = cy;
      for (let dy = -1; dy <= 1; dy++) {
        for (let dx = -1; dx <= 1; dx++) {
          if (!dx && !dy) continue;
          const nx = cx + dx, ny = cy + dy;
          if (nx < 0 || ny < 0 || nx >= w || ny >= h) continue;
          const ni = ny * w + nx;
          if (seen[ni] || blobSrc.data[ni * 4 + 3] < 60) continue;
          seen[ni] = 1; q[tail++] = ni;
        }
      }
    }
    if (count > 400) comps.push({ minX, minY, maxX, maxY, count });
  }
  comps.sort((a, b) => b.count - a.count);
  console.log(`  连通域（>400px）${comps.length} 个，取前三`);
  const cfg = [
    { name: 'ink-blob.png', dark: 'ink-blob-dark.png', maxDim: 420 },
    { name: 'ink-splash-a.png', dark: 'ink-splash-a-dark.png', maxDim: 220 },
    { name: 'ink-splash-b.png', dark: 'ink-splash-b-dark.png', maxDim: 200 },
  ];
  comps.slice(0, 3).forEach((c, i) => {
    const pad = 6;
    const bx = Math.max(0, c.minX - pad), by = Math.max(0, c.minY - pad);
    const bw = Math.min(w - bx, c.maxX - c.minX + 1 + pad * 2);
    const bh = Math.min(h - by, c.maxY - c.minY + 1 + pad * 2);
    const crop = cropTo(blobSrc, { x: bx, y: by, w: bw, h: bh });
    const sc = Math.min(1, cfg[i].maxDim / Math.max(bw, bh));
    const out = sc < 1 ? boxDown(crop, sc) : crop;
    save(out, cfg[i].name);
    save(recolor(out, hex('#4A2FB8'), hex('#B7A6FF')), cfg[i].dark);
  });
}

// ============ 5. 笔尖 / 飞白 / 剑气辉光 ============
console.log('\n【5】纹理裁边缩放');
function trim(name, outName, maxDim) {
  const img = load(name);
  const b = bbox(img, 6);
  const crop = cropTo(img, b);
  const sc = Math.min(1, maxDim / Math.max(b.w, b.h));
  const out = sc < 1 ? boxDown(crop, sc) : crop;
  save(out, outName);
  return { out, srcBox: b };
}

/** 按目标色重新着色（源为白/灰笔触，按亮度映射到两个色档之间） */
function recolor(src, darkC, lightC) {
  const out = { w: src.w, h: src.h, data: new Uint8ClampedArray(src.data) };
  for (let i = 0; i < src.w * src.h; i++) {
    const o = i * 4;
    if (!src.data[o + 3]) continue;
    const l = lum(src.data[o], src.data[o + 1], src.data[o + 2]);
    const t = Math.min(1, Math.max(0, (l - 0.05) / 0.75));
    for (let c = 0; c < 3; c++) out.data[o + c] = lerp(darkC[c], lightC[c], t);
  }
  return out;
}

const tipBox = trim('毛笔笔尖纹理.png', 'brush-tip.png', 384);
save(recolor(tipBox.out, hex('#6E5BC8'), hex('#FFFFFF')), 'brush-tip-dark.png');

const dryBox = trim('飞白笔刷纹理.png', 'brush-dry.png', 1024);
save(recolor(dryBox.out, hex('#F2EEFF'), hex('#F2EEFF')), 'brush-dry-dark.png');

const glowBox = trim('剑气辉光纹理.png', 'sword-glow.png', 1024);
save(recolor(glowBox.out, hex('#7A2A26'), hex('#B23A34')), 'sword-glow-light.png');

console.log('\n完成。目录：' + OUT);
console.log('brush-tip 原始裁框:', JSON.stringify(tipBox.srcBox));
console.log('brush-dry 原始裁框:', JSON.stringify(dryBox.srcBox));
console.log('sword-glow 原始裁框:', JSON.stringify(glowBox.srcBox));
