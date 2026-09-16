const fs = require('fs');
const zlib = require('zlib');

function decodePNG(file) {
  const buf = fs.readFileSync(file);
  if (buf.readUInt32BE(0) !== 0x89504e47) throw new Error('not png');
  let pos = 8;
  let width = 0, height = 0, bitDepth = 0, colorType = 0, interlace = 0;
  const idat = [];
  let palette = null, trns = null;
  while (pos < buf.length) {
    const len = buf.readUInt32BE(pos);
    const type = buf.toString('ascii', pos + 4, pos + 8);
    const data = buf.slice(pos + 8, pos + 8 + len);
    if (type === 'IHDR') {
      width = data.readUInt32BE(0);
      height = data.readUInt32BE(4);
      bitDepth = data[8];
      colorType = data[9];
      interlace = data[12];
    } else if (type === 'PLTE') palette = data;
    else if (type === 'tRNS') trns = data;
    else if (type === 'IDAT') idat.push(data);
    else if (type === 'IEND') break;
    pos += 12 + len;
  }
  const raw = zlib.inflateSync(Buffer.concat(idat));
  const channels = { 0: 1, 2: 3, 3: 1, 4: 2, 6: 4 }[colorType];
  const bpp = Math.ceil((channels * bitDepth) / 8);
  const stride = Math.ceil((channels * bitDepth * width) / 8);
  const out = Buffer.alloc(stride * height);
  let rp = 0;
  for (let y = 0; y < height; y++) {
    const f = raw[rp++];
    const line = raw.slice(rp, rp + stride);
    rp += stride;
    const prev = y > 0 ? out.slice((y - 1) * stride, y * stride) : Buffer.alloc(stride);
    const cur = out.slice(y * stride, (y + 1) * stride);
    for (let i = 0; i < stride; i++) {
      const a = i >= bpp ? cur[i - bpp] : 0;
      const b = prev[i];
      const c = i >= bpp ? prev[i - bpp] : 0;
      let v = line[i];
      if (f === 1) v += a;
      else if (f === 2) v += b;
      else if (f === 3) v += (a + b) >> 1;
      else if (f === 4) {
        const p = a + b - c;
        const pa = Math.abs(p - a), pb = Math.abs(p - b), pc = Math.abs(p - c);
        v += pa <= pb && pa <= pc ? a : pb <= pc ? b : c;
      }
      cur[i] = v & 0xff;
    }
  }
  const get = (x, y) => {
    const o = y * stride;
    if (colorType === 6) return [out[o + x * 4], out[o + x * 4 + 1], out[o + x * 4 + 2], out[o + x * 4 + 3]];
    if (colorType === 2) return [out[o + x * 3], out[o + x * 3 + 1], out[o + x * 3 + 2], 255];
    if (colorType === 4) return [out[o + x * 2], out[o + x * 2], out[o + x * 2], out[o + x * 2 + 1]];
    if (colorType === 0) { const g = out[o + x]; return [g, g, g, 255]; }
    if (colorType === 3) {
      const idx = out[o + x];
      const a = trns && idx < trns.length ? trns[idx] : 255;
      return [palette[idx * 3], palette[idx * 3 + 1], palette[idx * 3 + 2], a];
    }
    throw new Error('unsupported');
  };
  return { width, height, bitDepth, colorType, interlace, get };
}

let CRC_TABLE = null;
function crc32(buf) {
  if (!CRC_TABLE) {
    CRC_TABLE = new Int32Array(256);
    for (let n = 0; n < 256; n++) {
      let c = n;
      for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
      CRC_TABLE[n] = c;
    }
  }
  let c = -1;
  for (let i = 0; i < buf.length; i++) c = CRC_TABLE[(c ^ buf[i]) & 0xff] ^ (c >>> 8);
  return (c ^ -1) >>> 0;
}

function encodePNG(width, height, rgba) {
  const stride = width * 4;
  const raw = Buffer.alloc((stride + 1) * height);
  const prev = Buffer.alloc(stride);
  const cur = Buffer.alloc(stride);
  const cand = [Buffer.alloc(stride), Buffer.alloc(stride), Buffer.alloc(stride), Buffer.alloc(stride), Buffer.alloc(stride)];
  for (let y = 0; y < height; y++) {
    rgba.copy(cur, 0, y * stride, (y + 1) * stride);
    let best = 0, bestScore = -1;
    for (let f = 0; f < 5; f++) {
      const out = cand[f];
      let score = 0;
      for (let i = 0; i < stride; i++) {
        const a = i >= 4 ? cur[i - 4] : 0;
        const b = prev[i];
        const c = i >= 4 ? prev[i - 4] : 0;
        let v;
        if (f === 0) v = cur[i];
        else if (f === 1) v = cur[i] - a;
        else if (f === 2) v = cur[i] - b;
        else if (f === 3) v = cur[i] - ((a + b) >> 1);
        else {
          const p = a + b - c;
          const pa = Math.abs(p - a), pb = Math.abs(p - b), pc = Math.abs(p - c);
          v = cur[i] - (pa <= pb && pa <= pc ? a : pb <= pc ? b : c);
        }
        v &= 0xff;
        out[i] = v;
        score += v < 128 ? v : 256 - v;
      }
      if (bestScore < 0 || score < bestScore) { bestScore = score; best = f; }
    }
    raw[y * (stride + 1)] = best;
    cand[best].copy(raw, y * (stride + 1) + 1);
    cur.copy(prev);
  }
  const idat = zlib.deflateSync(raw, { level: 9 });
  const chunk = (type, data) => {
    const len = Buffer.alloc(4);
    len.writeUInt32BE(data.length);
    const body = Buffer.concat([Buffer.from(type, 'ascii'), data]);
    const crc = Buffer.alloc(4);
    crc.writeUInt32BE(crc32(body) >>> 0);
    return Buffer.concat([len, body, crc]);
  };
  const ihdr = Buffer.alloc(13);
  ihdr.writeUInt32BE(width, 0);
  ihdr.writeUInt32BE(height, 4);
  ihdr[8] = 8; ihdr[9] = 6; ihdr[10] = 0; ihdr[11] = 0; ihdr[12] = 0;
  return Buffer.concat([
    Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]),
    chunk('IHDR', ihdr),
    chunk('IDAT', idat),
    chunk('IEND', Buffer.alloc(0)),
  ]);
}

module.exports = { decodePNG, encodePNG };

if (require.main === module) {
  const dir = 'D:\\MiniMax Design Data\\Projects\\请你根据这份文档的§2 + §3 + §';
  for (const f of fs.readdirSync(dir)) {
    if (!f.endsWith('.png')) continue;
    const img = decodePNG(dir + '\\' + f);
    console.log(`${f}  ${img.width}x${img.height}  depth=${img.bitDepth} colorType=${img.colorType} interlace=${img.interlace}`);
  }
}
