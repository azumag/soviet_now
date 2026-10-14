// READY is an interface banner, not five settled country components. Match
// its observed glyph mask only on an otherwise empty calibrated playfield.
const READY = [
  '111111110011111111100011111100111111110001100011',
  '111111110011111111100111111100111111110011100011',
  '111000011011100000001110000110111000011111100011',
  '111000011011100000001110000110111000011111100011',
  '111000011011111100001110000110111000011101111111',
  '111000011011111110001110000110111000011000111110',
  '111111110011100000000111111110011000011000011100',
  '111000111011100000000110001110011000011000011100',
  '000000010000000000000110000110010000011000000100',
  '000000000000000000000000000000000000000000000000',
];

export function hasReadyBanner(data, width, height, calibration) {
  const b = calibration?.board, a = calibration?.arena;
  if (!Number.isInteger(width) || !Number.isInteger(height) || width <= 0 || height <= 0
      || !b || !a || ![b.left, b.right, b.top, b.bottom, b.width, b.height, a.top].every(Number.isFinite)
      || b.width < 100 || b.height < 100 || data?.length < width * height * 4) return false;
  const left = Math.max(0, Math.ceil(b.left + b.width * 0.22));
  const right = Math.min(width, Math.floor(b.right - b.width * 0.22));
  const top = Math.max(0, Math.floor(b.top + b.height * 0.25));
  const bottom = Math.min(height, Math.ceil(b.top + b.height * 0.38));
  const white = (x, y) => {
    const i = (y * width + x) * 4;
    return data[i] > 160 && data[i + 1] > 160 && data[i + 2] > 160;
  };
  let x0 = right, x1 = left, y0 = bottom, y1 = top, light = 0, purple = 0;
  for (let y = top; y < bottom; y++) for (let x = left; x < right; x++) {
    const i = (y * width + x) * 4, r = data[i], g = data[i + 1], blue = data[i + 2];
    if (white(x, y)) { light++; x0 = Math.min(x0, x); x1 = Math.max(x1, x + 1); y0 = Math.min(y0, y); y1 = Math.max(y1, y + 1); }
    if (blue > 70 && blue > r * 1.4 && blue > g * 1.4) purple++;
  }
  if (!light || purple < light * 0.2 || (x1 - x0) / b.width < 0.40 || (x1 - x0) / b.width > 0.49
      || (y1 - y0) / b.height < 0.055 || (y1 - y0) / b.height > 0.09
      || Math.abs((x0 + x1) / 2 - (b.left + b.right) / 2) > b.width * 0.03) return false;
  // Reject a real current or any occupied board. Exclude only the known HUD,
  // deadline and this narrow banner box, not arbitrary dark sprite pixels.
  let occupied = 0, samples = 0;
  const inset = Math.max(6, b.width * 0.02);
  for (let y = Math.ceil(a.top + inset); y < Math.min(height, b.bottom - inset); y += 3) {
    if (Math.abs(y - b.top) < inset) continue;
    for (let x = Math.max(0, Math.ceil(b.left + inset)); x < Math.min(width, b.right - inset); x += 3) {
      if (x >= left && x < right && y >= top && y < bottom) continue;
      const i = (y * width + x) * 4;
      const rgb = [data[i], data[i + 1], data[i + 2]];
      samples++;
      if (Math.min(...rgb) < 30 || Math.max(...rgb) > 85 || Math.max(...rgb) - Math.min(...rgb) > 30) occupied++;
    }
  }
  if (!samples || occupied / samples > 0.005) return false;
  let errors = 0;
  for (let gy = 0; gy < 10; gy++) for (let gx = 0; gx < 48; gx++) {
    let count = 0, total = 0;
    for (let y = Math.floor(y0 + (y1 - y0) * gy / 10); y < Math.floor(y0 + (y1 - y0) * (gy + 1) / 10); y++) {
      for (let x = Math.floor(x0 + (x1 - x0) * gx / 48); x < Math.floor(x0 + (x1 - x0) * (gx + 1) / 48); x++) {
        total++; if (white(x, y)) count++;
      }
    }
    if ((count / total >= 0.45) !== (READY[gy][gx] === '1')) errors++;
  }
  return errors / 480 <= 0.10;
}
