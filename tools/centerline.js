/**
 * 墨参 · 动画1 行笔路径拟合（一次性分析脚本，用于确定 ink-sword.js 里的 ARC_PATH）
 *
 * 做法：读入 frontend/assets/ink/arc-light.png（已剔除剑光层的墨迹层），
 *   闭运算去零散墨点 → 取最大连通域 → BFS 测地直径定两端 → 按测地距离分带求质心得到中线
 *   → 按弧长重采样后拟合 3 段三次贝塞尔，并输出拟合误差与 ASCII 复核图。
 *
 * 用法：node tools/centerline.js arc-light.png 256
 */
const { decodePNG } = require('./pngcodec');
const path = require('path');

const OUT = path.join(__dirname, '..', 'frontend', 'assets', 'ink');
const img = decodePNG(path.join(OUT, process.argv[2] || 'arc-light.png'));
const N = Number(process.argv[3] || 256);
const step = img.width / N;
const K = img.width / 1024; // 输出坐标回 1024 逻辑画布的换算

const ink = new Uint8Array(N * N);
for (let j = 0; j < N; j++) {
  for (let i = 0; i < N; i++) {
    let acc = 0, c = 0;
    for (let a = 0; a < 3; a++) {
      for (let b = 0; b < 3; b++) {
        const x = Math.min(img.width - 1, Math.floor((i + b / 3) * step));
        const y = Math.min(img.height - 1, Math.floor((j + a / 3) * step));
        acc += img.get(x, y)[3];
        c++;
      }
    }
    ink[j * N + i] = acc / c > 90 ? 1 : 0;
  }
}
let cnt = 0;
for (let i = 0; i < N * N; i++) cnt += ink[i];
console.log(`闭运算前掩膜 ${N}x${N}，墨迹 ${cnt} 格（${((cnt / (N * N)) * 100).toFixed(1)}%）`);

// 闭运算（可分离方形结构元）+ 取最大连通域，避免零散墨点把测地直径打断
function morph(src, r, isMax) {
  const tmp = new Uint8Array(N * N);
  for (let y = 0; y < N; y++) {
    for (let x = 0; x < N; x++) {
      let v = isMax ? 0 : 1;
      for (let k = Math.max(0, x - r); k <= Math.min(N - 1, x + r); k++) {
        const s = src[y * N + k];
        if (isMax) { if (s) { v = 1; break; } } else if (!s) { v = 0; break; }
      }
      tmp[y * N + x] = v;
    }
  }
  const out = new Uint8Array(N * N);
  for (let y = 0; y < N; y++) {
    for (let x = 0; x < N; x++) {
      let v = isMax ? 0 : 1;
      for (let k = Math.max(0, y - r); k <= Math.min(N - 1, y + r); k++) {
        const s = tmp[k * N + x];
        if (isMax) { if (s) { v = 1; break; } } else if (!s) { v = 0; break; }
      }
      out[y * N + x] = v;
    }
  }
  return out;
}
(function keepLargest() {
  const closed = morph(morph(ink, 6, true), 6, false);
  const seen = new Uint8Array(N * N);
  const q = new Int32Array(N * N);
  let best = null, bestN = 0;
  for (let s = 0; s < N * N; s++) {
    if (!closed[s] || seen[s]) continue;
    let head = 0, tail = 0;
    q[tail++] = s; seen[s] = 1;
    const comp = [];
    while (head < tail) {
      const cur = q[head++];
      comp.push(cur);
      const cx = cur % N, cy = (cur - cx) / N;
      for (let dy = -1; dy <= 1; dy++) {
        for (let dx = -1; dx <= 1; dx++) {
          if (!dx && !dy) continue;
          const nx = cx + dx, ny = cy + dy;
          if (nx < 0 || ny < 0 || nx >= N || ny >= N) continue;
          const ni = ny * N + nx;
          if (!closed[ni] || seen[ni]) continue;
          seen[ni] = 1; q[tail++] = ni;
        }
      }
    }
    if (comp.length > bestN) { bestN = comp.length; best = comp; }
  }
  ink.fill(0);
  best.forEach((i) => { ink[i] = 1; });
  console.log(`闭运算后最大连通域 ${bestN} 格（占 ${((bestN / (N * N)) * 100).toFixed(1)}%）`);
})();

function bfs(seed) {
  const dist = new Int32Array(N * N).fill(-1);
  const q = new Int32Array(N * N);
  let head = 0, tail = 0;
  dist[seed] = 0; q[tail++] = seed;
  let far = seed;
  while (head < tail) {
    const cur = q[head++];
    const cx = cur % N, cy = (cur - cx) / N;
    if (dist[cur] > dist[far]) far = cur;
    for (let dy = -1; dy <= 1; dy++) {
      for (let dx = -1; dx <= 1; dx++) {
        if (!dx && !dy) continue;
        const nx = cx + dx, ny = cy + dy;
        if (nx < 0 || ny < 0 || nx >= N || ny >= N) continue;
        const ni = ny * N + nx;
        if (!ink[ni] || dist[ni] >= 0) continue;
        dist[ni] = dist[cur] + 1; q[tail++] = ni;
      }
    }
  }
  return { dist, far, reached: tail };
}

let seed = 0;
for (let i = 0; i < N * N; i++) if (ink[i]) { seed = i; break; }
const a = bfs(seed);
console.log(`起点分量规模 ${a.reached} 格；端点 A=(${(a.far % N) * step / K | 0},${Math.floor(a.far / N) * step / K | 0})`);
const b = bfs(a.far);
const len = b.dist[b.far];
console.log(`测地直径 ${len} 格；端点 B=(${(b.far % N) * step / K | 0},${Math.floor(b.far / N) * step / K | 0})，BFS 覆盖 ${b.reached} 格`);

// 分带质心 → 中心线（从 B 端 → A 端，b.dist 从 B 递增）
const bands = len + 1;
const raw = [];
for (let t = 0; t <= len; t++) {
  let sx = 0, sy = 0, n = 0;
  for (let j = 0; j < N; j++) {
    for (let i = 0; i < N; i++) {
      if (b.dist[j * N + i] === t) { sx += i; sy += j; n++; }
    }
  }
  if (n) raw.push([sx / n, sy / n]);
}
const smooth = raw.map((_, idx) => {
  let sx = 0, sy = 0, n = 0;
  for (let k = -4; k <= 4; k++) {
    const p = raw[idx + k];
    if (p) { sx += p[0]; sy += p[1]; n++; }
  }
  return [sx / n, sy / n];
});
const pts = smooth.map((p) => [(p[0] * step) / K, (p[1] * step) / K]);

const cum = [0];
for (let i = 1; i < pts.length; i++) {
  cum.push(cum[i - 1] + Math.hypot(pts[i][0] - pts[i - 1][0], pts[i][1] - pts[i - 1][1]));
}
const total = cum[cum.length - 1];
const at = (s) => {
  let i = 1;
  while (i < cum.length - 1 && cum[i] < s) i++;
  const t = (s - cum[i - 1]) / Math.max(1e-6, cum[i] - cum[i - 1]);
  return [pts[i - 1][0] + (pts[i][0] - pts[i - 1][0]) * t, pts[i - 1][1] + (pts[i][1] - pts[i - 1][1]) * t];
};

// 弧长重新采样后拟合 3 段三次贝塞尔（用 7 个节点保证弯折被抓住）
const SEG = 3;
const nodes = [];
for (let k = 0; k <= SEG; k++) nodes.push(at((k * total) / SEG));
const tang = (k) => {
  const p0 = nodes[Math.max(0, k - 1)];
  const p1 = nodes[Math.min(SEG, k + 1)];
  const d = Math.hypot(p1[0] - p0[0], p1[1] - p0[1]) || 1;
  return [(p1[0] - p0[0]) / d, (p1[1] - p0[1]) / d];
};
let d = `M${nodes[0][0].toFixed(1)},${nodes[0][1].toFixed(1)}`;
for (let k = 0; k < SEG; k++) {
  const L = Math.hypot(nodes[k + 1][0] - nodes[k][0], nodes[k + 1][1] - nodes[k][1]) / 3;
  const t0 = tang(k), t1 = tang(k + 1);
  const c1 = [nodes[k][0] + t0[0] * L, nodes[k][1] + t0[1] * L];
  const c2 = [nodes[k + 1][0] - t1[0] * L, nodes[k + 1][1] - t1[1] * L];
  d += ` C${c1[0].toFixed(1)},${c1[1].toFixed(1)} ${c2[0].toFixed(1)},${c2[1].toFixed(1)} ${nodes[k + 1][0].toFixed(1)},${nodes[k + 1][1].toFixed(1)}`;
}
console.log(`弧长 ${total.toFixed(0)}（1024 空间）`);
console.log('\n节点:', nodes.map((p) => `(${p[0] | 0},${p[1] | 0})`).join(' '));
console.log('\nARC_PATH =', JSON.stringify(d));

// 采样 + 拟合误差
console.log('\n弧长采样（每 5%）:');
for (let k = 0; k <= 20; k++) {
  const p = at((k * total) / 20);
  console.log(`  ${String(k * 5).padStart(3)}%  (${p[0] | 0}, ${p[1] | 0})`);
}

// 覆盖度：拟合曲线能否覆盖墨迹（把曲线按粗描边采样，检查每个墨迹格是否有近邻）
function bez(p0, c1, c2, p1, t) {
  const u = 1 - t;
  return [
    u * u * u * p0[0] + 3 * u * u * t * c1[0] + 3 * u * t * t * c2[0] + t * t * t * p1[0],
    u * u * u * p0[1] + 3 * u * u * t * c1[1] + 3 * u * t * t * c2[1] + t * t * t * p1[1],
  ];
}
const fit = [];
for (let k = 0; k < SEG; k++) {
  const L = Math.hypot(nodes[k + 1][0] - nodes[k][0], nodes[k + 1][1] - nodes[k][1]) / 3;
  const t0 = tang(k), t1 = tang(k + 1);
  fit.push([
    nodes[k],
    [nodes[k][0] + t0[0] * L, nodes[k][1] + t0[1] * L],
    [nodes[k + 1][0] - t1[0] * L, nodes[k + 1][1] - t1[1] * L],
    nodes[k + 1],
  ]);
}
const samples = [];
for (const seg of fit) for (let t = 0; t <= 1; t += 0.01) samples.push(bez(seg[0], seg[1], seg[2], seg[3], t));
let maxD = 0, sumD = 0, n0 = 0, over = 0;
for (let j = 0; j < N; j++) {
  for (let i = 0; i < N; i++) {
    if (!ink[j * N + i]) continue;
    const x = ((i + 0.5) * step) / K, y = ((j + 0.5) * step) / K;
    let best = 1e9;
    for (const s of samples) {
      const dd = (s[0] - x) * (s[0] - x) + (s[1] - y) * (s[1] - y);
      if (dd < best) best = dd;
    }
    best = Math.sqrt(best);
    if (best > maxD) maxD = best;
    sumD += best; n0++;
    if (best > 180) over++;
  }
}
console.log(`\n拟合曲线到墨迹的最大距离 ${maxD.toFixed(0)}，平均 ${(sumD / n0).toFixed(0)}，超过 180 的墨迹格占比 ${((over / n0) * 100).toFixed(1)}%`);

// ASCII 叠加复核
const M = 56;
const grid = [];
for (let j = 0; j < M; j++) grid.push(new Array(M).fill(' '));
for (let j = 0; j < M; j++) {
  for (let i = 0; i < M; i++) {
    const gi = Math.floor((i * N) / M), gj = Math.floor((j * N) / M);
    if (ink[gj * N + gi]) grid[j][i] = '·';
  }
}
samples.forEach((p) => {
  const i = Math.round((p[0] * K / step / N) * M), j = Math.round((p[1] * K / step / N) * M);
  if (i >= 0 && i < M && j >= 0 && j < M) grid[j][i] = '#';
});
[0, 0.25, 0.5, 0.75, 1].forEach((f) => {
  const p = at(f * total);
  const i = Math.round((p[0] * K / step / N) * M), j = Math.round((p[1] * K / step / N) * M);
  if (i >= 0 && i < M && j >= 0 && j < M) grid[j][i] = String(Math.round(f * 9));
});
console.log('\n复核（# = 拟合曲线，· = 墨迹，0/2/4/6/9 = 行程 0/25/50/75/100%）:');
console.log(grid.map((r) => r.join('')).join('\n'));
