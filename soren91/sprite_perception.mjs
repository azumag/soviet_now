/**
 * Country sprites are multicolour silhouettes, not solid coloured discs.
 * These helpers operate on the analyzer's existing raw RGBA capture. They do
 * not infer a country from a representative hue or from its rendered radius.
 */
import { FLAG_TEMPLATE_SIZE, FLAG_TEMPLATES } from './flag_templates.mjs';

const DESCRIPTOR_SIZE = FLAG_TEMPLATE_SIZE;
const templates = FLAG_TEMPLATES.map(t => ({ ...t, pixels: Uint8Array.from(Buffer.from(t.rgba, 'base64')) }));

function validImage(data, width, height) {
  return Number.isInteger(width) && Number.isInteger(height) && width > 0 && height > 0
    && data?.length >= width * height * 4;
}

function integerRoi(roi, width, height) {
  if (!roi || ![roi.left, roi.top, roi.right, roi.bottom].every(Number.isFinite)) return null;
  const left = Math.max(0, Math.ceil(roi.left));
  const top = Math.max(0, Math.ceil(roi.top));
  const right = Math.min(width, Math.floor(roi.right));
  const bottom = Math.min(height, Math.floor(roi.bottom));
  return right > left && bottom > top ? { left, top, right, bottom, width: right - left, height: bottom - top } : null;
}

function backgroundIn(data, width, roi) {
  // Estimate the neutral panel, never a saturated loss overlay or flag field.
  const bins = new Map();
  for (let y = roi.top; y < roi.bottom; y += 3) {
    for (let x = roi.left; x < roi.right; x += 3) {
      const i = (y * width + x) * 4;
      const rgb = [data[i], data[i + 1], data[i + 2]];
      const max = Math.max(...rgb), min = Math.min(...rgb);
      if (max - min > 25 || min < 35 || max > 205) continue;
      const key = rgb.map(v => Math.round(v / 8)).join(',');
      const value = bins.get(key) || { count: 0, sums: [0, 0, 0] };
      value.count++;
      for (let c = 0; c < 3; c++) value.sums[c] += rgb[c];
      bins.set(key, value);
    }
  }
  const best = [...bins.values()].sort((a, b) => b.count - a.count)[0];
  return best ? best.sums.map(v => v / best.count) : [51, 51, 51];
}

/**
 * Eight-connected foreground, retaining black/white and adjacent flag colours.
 * Pixel indices are absolute image indices (y * width + x), not byte offsets.
 * A caller may remove neutral garbage with pixelFilter without changing HUD
 * perception, or subtract an already-confirmed current with excludePixels.
 */
export function extractSpriteComponents(data, width, height, roi, options = {}) {
  if (!validImage(data, width, height)) return [];
  const area = integerRoi(roi, width, height);
  if (!area) return [];
  const background = options.background || backgroundIn(data, width, area);
  const tolerance = options.backgroundTolerance ?? 26;
  const foreground = new Uint8Array(area.width * area.height);
  const exclude = options.excludePixels;
  for (let y = area.top; y < area.bottom; y++) {
    for (let x = area.left; x < area.right; x++) {
      const pixel = y * width + x, i = pixel * 4;
      const r = data[i], g = data[i + 1], b = data[i + 2], a = data[i + 3];
      if (a < 128 || exclude?.has?.(pixel) || options.excludeMask?.[pixel]) continue;
      if (options.pixelFilter && !options.pixelFilter(r, g, b, a, x, y)) continue;
      if (Math.max(Math.abs(r - background[0]), Math.abs(g - background[1]), Math.abs(b - background[2])) <= tolerance) continue;
      foreground[(y - area.top) * area.width + x - area.left] = 1;
    }
  }
  const components = [];
  const queue = new Int32Array(foreground.length);
  for (let start = 0; start < foreground.length; start++) {
    if (!foreground[start]) continue;
    let head = 0, tail = 1;
    queue[0] = start;
    foreground[start] = 0;
    let left = area.right, right = area.left, top = area.bottom, bottom = area.top;
    let sumX = 0, sumY = 0;
    while (head < tail) {
      const local = queue[head++];
      const lx = local % area.width, ly = Math.floor(local / area.width);
      const x = lx + area.left, y = ly + area.top;
      left = Math.min(left, x); right = Math.max(right, x);
      top = Math.min(top, y); bottom = Math.max(bottom, y);
      sumX += x; sumY += y;
      for (let dy = -1; dy <= 1; dy++) {
        for (let dx = -1; dx <= 1; dx++) {
          const nx = lx + dx, ny = ly + dy;
          if ((dx === 0 && dy === 0) || nx < 0 || nx >= area.width || ny < 0 || ny >= area.height) continue;
          const index = ny * area.width + nx;
          if (!foreground[index]) continue;
          foreground[index] = 0;
          queue[tail++] = index;
        }
      }
    }
    if (tail < (options.minPixels ?? 8)) continue;
    const pixels = Int32Array.from(queue.subarray(0, tail), local => (
      (Math.floor(local / area.width) + area.top) * width + local % area.width + area.left
    ));
    const bounds = { left, top, right: right + 1, bottom: bottom + 1, width: right - left + 1, height: bottom - top + 1 };
    components.push({ bounds, pixels, pixelCount: tail, centerX: sumX / tail, centerY: sumY / tail,
      touchesBoundary: left === area.left || right + 1 === area.right || top === area.top || bottom + 1 === area.bottom,
      background: [...background] });
    if (components.length >= (options.maxComponents ?? 512)) break;
  }
  return components.sort((a, b) => b.pixelCount - a.pixelCount);
}

/** Isotropic normalisation preserves the flag's colour layout and map aspect. */
export function createSpriteDescriptor(data, width, height, component) {
  if (!validImage(data, width, height) || !component?.pixels?.length || !component.bounds) return null;
  const b = component.bounds;
  const mask = new Uint8Array(b.width * b.height);
  for (const pixel of component.pixels) {
    const x = pixel % width - b.left, y = Math.floor(pixel / width) - b.top;
    if (x >= 0 && x < b.width && y >= 0 && y < b.height) mask[y * b.width + x] = 1;
  }
  const size = DESCRIPTOR_SIZE, inner = size - 4;
  const scale = inner / Math.max(b.width, b.height);
  const offsetX = (size - b.width * scale) / 2, offsetY = (size - b.height * scale) / 2;
  const rgba = new Uint8Array(size * size * 4);
  for (let y = 0; y < size; y++) {
    for (let x = 0; x < size; x++) {
      let n = 0, r = 0, g = 0, blue = 0;
      for (const dy of [0.25, 0.75]) {
        for (const dx of [0.25, 0.75]) {
          const sx = Math.floor((x + dx - offsetX) / scale), sy = Math.floor((y + dy - offsetY) / scale);
          if (sx < 0 || sx >= b.width || sy < 0 || sy >= b.height || !mask[sy * b.width + sx]) continue;
          const i = ((sy + b.top) * width + sx + b.left) * 4;
          n++; r += data[i]; g += data[i + 1]; blue += data[i + 2];
        }
      }
      if (!n) continue;
      const i = (y * size + x) * 4;
      rgba[i] = Math.round(r / n); rgba[i + 1] = Math.round(g / n); rgba[i + 2] = Math.round(blue / n);
      rgba[i + 3] = Math.round(n * 255 / 4);
    }
  }
  return rgba;
}

function hasMultipleColours(data, component) {
  const bins = new Map();
  for (const pixel of component.pixels) {
    const i = pixel * 4, rgb = [data[i], data[i + 1], data[i + 2]];
    const key = rgb.map(v => Math.floor(v / 32)).join(',');
    const bin = bins.get(key) || { count: 0, sums: [0, 0, 0] };
    bin.count++;
    for (let c = 0; c < 3; c++) bin.sums[c] += rgb[c];
    bins.set(key, bin);
  }
  const colours = [];
  for (const bin of [...bins.values()].sort((a, b) => b.count - a.count)) {
    const rgb = bin.sums.map(v => v / bin.count);
    const cluster = colours.find(c => Math.max(...rgb.map((v, i) => Math.abs(v - c.rgb[i]))) < 75);
    if (cluster) cluster.count += bin.count;
    else colours.push({ rgb, count: bin.count });
  }
  return colours.filter(c => c.count >= component.pixelCount * 0.045).length >= 2;
}

/** Shape overlap and registered RGB, with a one-cell translation tolerance. */
export function spriteDescriptorDistance(a, b) {
  if (!a || !b || a.length !== b.length || a.length !== DESCRIPTOR_SIZE ** 2 * 4) return Infinity;
  const size = DESCRIPTOR_SIZE;
  let best = Infinity;
  for (let dy = -1; dy <= 1; dy++) {
    for (let dx = -1; dx <= 1; dx++) {
      let areaA = 0, areaB = 0, overlap = 0, colours = 0;
      for (let y = 0; y < size; y++) {
        for (let x = 0; x < size; x++) {
          const i = (y * size + x) * 4;
          const tx = x + dx, ty = y + dy;
          const j = (ty * size + tx) * 4;
          const aa = a[i + 3] / 255;
          const bb = tx >= 0 && tx < size && ty >= 0 && ty < size ? b[j + 3] / 255 : 0;
          areaA += aa; areaB += bb;
          const common = Math.min(aa, bb);
          overlap += common;
          if (common) colours += common * Math.max(Math.abs(a[i] - b[j]), Math.abs(a[i + 1] - b[j + 1]), Math.abs(a[i + 2] - b[j + 2])) / 255;
        }
      }
      if (!overlap) continue;
      const shape = 1 - 2 * overlap / (areaA + areaB);
      const colour = colours / overlap;
      best = Math.min(best, shape * 0.45 + colour * 0.55);
    }
  }
  return best;
}

/** A match is evidence of a labelled sprite; its apparent size is not a type. */
export function classifySpriteComponent(data, width, height, component, options = {}) {
  const unknown = reason => ({ type: null, confidence: 0, fallback: true, reason });
  if (!component || component.pixelCount < 8) return unknown('empty-sprite');
  if (!hasMultipleColours(data, component)) return unknown('single-colour-fragment');
  const descriptor = createSpriteDescriptor(data, width, height, component);
  if (!descriptor) return unknown('invalid-sprite');
  const byType = new Map();
  for (const template of templates) {
    if (options.excludeSource && template.source === options.excludeSource) continue;
    const score = spriteDescriptorDistance(descriptor, template.pixels);
    const previous = byType.get(template.type);
    if (!previous || score < previous.score) byType.set(template.type, { type: template.type, score, template });
  }
  const matches = [...byType.values()].sort((a, b) => a.score - b.score);
  const best = matches[0], second = matches[1];
  if (!best) return unknown('no-templates');
  const margin = second ? second.score - best.score : 1;
  const maximum = options.maxDistance ?? 0.18;
  const minimumMargin = options.minMargin ?? 0.045;
  const detail = { score: best.score, margin, templateId: best.template.id };
  if (best.score > maximum || margin < minimumMargin) return { ...unknown('ambiguous-flag'), ...detail };
  const confidence = Math.min(best.template.source === 'soviet_now.png' ? 0.82 : 0.94,
    0.6 + 0.34 * Math.max(0, 1 - best.score / maximum));
  return { type: best.type, confidence: Math.round(confidence * 100) / 100,
    fallback: false, recognitionSource: 'flag-template', ...detail, reason: 'matched-flag' };
}

function validLayout(calibration) {
  const b = calibration?.board, a = calibration?.arena, h = calibration?.hud;
  return calibration?.coordinateSchema === 2 && b && a && h
    && [b.left, b.right, b.top, b.bottom, b.width, a.top, h.top, h.bottom].every(Number.isFinite)
    && b.width > 20 && Math.abs(b.width - (b.right - b.left)) < 1
    && h.bottom === a.top && a.top < b.top && b.top < b.bottom;
}

export function hudSpriteRois(calibration) {
  if (!validLayout(calibration)) return null;
  const { board: b, hud: h } = calibration;
  const unit = b.width / 7;
  const top = h.top + (h.bottom - h.top) * 0.29;
  const bottom = h.top + (h.bottom - h.top) * 0.86;
  const roi = (center, halfWidth) => ({ left: b.left + b.width * (center - halfWidth),
    right: b.left + b.width * (center + halfWidth), top, bottom });
  return { hold: roi(0.26, 0.12), nextPieces: [0.515, 0.765, 1.015].map(x => roi(x, 0.122)), unit };
}

function detectedInRoi(data, width, height, roi, options = {}) {
  const area = integerRoi(roi, width, height);
  const components = extractSpriteComponents(data, width, height, roi, { minPixels: 8 }).filter(c => {
    if (!area) return false;
    const b = c.bounds;
    const thin = Math.max(1, Math.ceil(Math.min(area.width, area.height) * 0.08));
    // A panel-edge rule is not HOLD occupancy. Do not generalise this to small
    // interior components: an unresolved tiny flag still makes HOLD unknown.
    // Scale the thickness with the calibrated cell, including antialiasing.
    // Only dense neutral rules qualify; coloured or irregular edge fragments
    // remain evidence of an unresolved sprite, even when they are very thin.
    const edgeRule = (b.width <= thin && b.height >= area.height * 0.8 && (b.left === area.left || b.right === area.right))
      || (b.height <= thin && b.width >= area.width * 0.8 && (b.top === area.top || b.bottom === area.bottom));
    if (!edgeRule || c.pixelCount < b.width * b.height * 0.9) return true;
    return c.pixels.some(pixel => {
      const i = pixel * 4;
      return Math.max(data[i], data[i + 1], data[i + 2]) - Math.min(data[i], data[i + 1], data[i + 2]) > 32;
    });
  });
  const substantial = components.filter(c => c.bounds.width >= 4 && c.bounds.height >= 4 && c.pixelCount >= 16);
  const matches = substantial.map(component => ({ component, result: classifySpriteComponent(data, width, height, component, options) }))
    .filter(m => m.result.type != null);
  if (matches.length !== 1) return { piece: null, empty: components.length === 0, reason: matches.length > 1 ? 'multiple-sprites' : 'unknown-sprite' };
  const match = matches[0];
  if (match.component.bounds.left === 0 || match.component.bounds.top === 0
      || match.component.bounds.right === width || match.component.bounds.bottom === height) {
    return { piece: null, empty: false, reason: 'clipped-sprite' };
  }
  // Small arrow components are allowed; another country-sized component is not.
  if (substantial.some(c => c !== match.component && c.pixelCount > Math.max(45, match.component.pixelCount * 0.25))) {
    return { piece: null, empty: false, reason: 'multiple-sprites' };
  }
  return { piece: { ...match.result }, empty: false, bounds: match.component.bounds, component: match.component };
}

export function detectHudPieces(data, width, height, calibration, options = {}) {
  const rois = hudSpriteRois(calibration);
  if (!rois || !validImage(data, width, height)) return { hold: null, nextPieces: [null, null, null], holdObservedEmpty: false, reason: 'uncalibrated' };
  const hold = detectedInRoi(data, width, height, rois.hold, options);
  const next = rois.nextPieces.map(roi => detectedInRoi(data, width, height, roi, options));
  return { hold: hold.piece, nextPieces: next.map(r => r.piece), holdObservedEmpty: hold.empty,
    bounds: { hold: hold.bounds || null, nextPieces: next.map(r => r.bounds || null) } };
}

/**
 * A single upright, isolated spawn-row candidate. This is visual evidence,
 * not proof of game ownership: an identical unsupported sprite copied to the
 * same pixels is indistinguishable in one frame. The caller must retain the
 * observation guard's temporal confirmation before using it for a move.
 */
export function detectCurrentPiece(data, width, height, calibration, options = {}) {
  const unknown = (reason, candidateCount = 0) => ({ piece: null, bounds: null, component: null, reason, candidateCount });
  if (!validLayout(calibration) || !validImage(data, width, height)) return unknown('uncalibrated');
  const { board: b, arena: a } = calibration;
  const unit = b.width / 7, centerY = b.top - unit;
  // The HUD's white bottom border touches the cursor in actual captures. Omit
  // this thin interface before connected-component extraction; retaining it
  // would join the cursor to a board-wide horizontal line.
  const roi = { left: b.left + 1, right: b.right - 1, top: a.top + Math.max(2, height / 135), bottom: b.top - 2 };
  const components = extractSpriteComponents(data, width, height, roi, { minPixels: 16 });
  // Locate the physical cursor before assigning its type. Higher settled pieces
  // are not removed merely because they happen to lie above the deadline.
  const candidates = components.filter(c => c.bounds.width >= 6 && c.bounds.height >= 7
    && Math.abs((c.bounds.top + c.bounds.bottom) / 2 - centerY) <= unit * 0.32
    && c.pixelCount >= 25);
  if (candidates.length !== 1) return unknown(candidates.length > 1 ? 'multiple-current-candidates' : 'missing-current', candidates.length);
  const component = candidates[0];
  // The very top can be hidden by the HUD. A legal cursor can also reach a
  // wall, so a one-pixel side contact is checked against the complete labelled
  // silhouette below instead of unconditionally blocking the next move.
  const integer = integerRoi(roi, width, height);
  const sideContact = component.bounds.left <= integer.left || component.bounds.right >= integer.right;
  if (component.bounds.bottom >= integer.bottom || component.bounds.width > b.width * 0.65) {
    return unknown('clipped-current', 1);
  }
  // A supported high country can sit near the spawn row. Require visible
  // background clearance below the candidate; neutral garbage counts as
  // support here too. Touching/overlapping pieces are conservatively unknown.
  // The red deadline is a UI line, not a physical support surface.
  const clearance = Math.max(4, Math.round(unit * 0.20));
  // Wall pixels themselves cannot support the cursor from below. Keep this
  // probe inside the calibrated interior, including the wall's antialias edge.
  const wallInset = Math.max(2, height / 135);
  const supportRoi = { left: Math.max(b.left + wallInset, component.bounds.left - 1),
    right: Math.min(b.right - wallInset, component.bounds.right + 1),
    top: component.bounds.bottom, bottom: Math.min(a.bottom ?? b.bottom, component.bounds.bottom + clearance) };
  const supports = extractSpriteComponents(data, width, height, supportRoi, {
    background: component.background, minPixels: Math.max(4, Math.round(unit * 0.08)),
    pixelFilter: (_r, _g, _blue, _alpha, _x, y) => Math.abs(y - b.top) > Math.max(2, height / 180),
  });
  if (supports.length) return unknown('current-not-isolated', 1);
  const result = classifySpriteComponent(data, width, height, component, options);
  if (result.type == null) return unknown(result.reason, 1);
  if (sideContact) {
    const template = templates.find(t => t.id === result.templateId);
    const observedAspect = component.bounds.width / component.bounds.height;
    const referenceAspect = template?.bounds?.width / template?.bounds?.height;
    // A complete-match score plus preserved horizontal extent allows a small
    // wall contact. Normalising a heavily truncated flag must not make it look
    // complete; reject a >10% width loss against the matched reference shape.
    if (result.score > 0.12 || !Number.isFinite(referenceAspect) || observedAspect < referenceAspect * 0.90) {
      return unknown('clipped-current', 1);
    }
  }
  return { piece: result, bounds: component.bounds, component, reason: 'detected-current', candidateCount: 1 };
}
