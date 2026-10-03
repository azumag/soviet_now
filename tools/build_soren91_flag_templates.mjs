/**
 * Reproducible, labelled country sprites. No downloaded model or guessed HSV
 * country palette. The existing soviet_now.png has the ordered 15-country row;
 * current Soren91 HUD/cursor captures independently check shared flag artwork.
 * Country IDs follow soren91/country_names.mjs; author flag attribution:
 * https://unityroom.com/games/sorengame91 (使用した国旗).
 *
 * Run from the repository root: node tools/build_soren91_flag_templates.mjs
 * Source JPEGs are retained for independent/leave-one-source-out tests. Bounds
 * below isolate already-reviewed sprites; they are not runtime image ROIs.
 */
import sharp from 'sharp';
import { createHash } from 'node:crypto';
import { readFileSync, writeFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { extractSpriteComponents, createSpriteDescriptor } from '../soren91/sprite_perception.mjs';
import { COUNTRY_NAMES } from '../soren91/country_names.mjs';

const root = new URL('../', import.meta.url);
const legacy = [
  [1, 96, 652, 121, 690], [2, 133, 652, 167, 690], [3, 181, 657, 219, 690],
  [4, 239, 657, 278, 689], [5, 295, 653, 343, 690], [6, 353, 655, 407, 690],
  [7, 425, 654, 469, 690], [8, 482, 654, 535, 690], [9, 553, 655, 614, 690],
  [10, 623, 657, 673, 689], [11, 687, 652, 748, 690], [12, 767, 651, 827, 690],
  [13, 840, 650, 903, 690], [14, 917, 650, 995, 690], [15, 1021, 649, 1107, 690],
];
const sources = [
  { source: 'soviet_now.png', path: 'soviet_now.png', crops: legacy, legacy: true },
  { source: 'game_0011_turn_7.jpg', path: 'tests/fixtures/soren91-sprites/game_0011_turn_7.jpg',
    evidence: '2026-10-03T01:12:23.870Z', crops: [
      [8, 362, 104, 448, 157], [10, 378, 31, 439, 80], [6, 459, 31, 511, 82],
      [11, 523, 28, 591, 83], [6, 608, 31, 658, 82],
    ] },
  { source: 'game_0011_turn_11.jpg', path: 'tests/fixtures/soren91-sprites/game_0011_turn_11.jpg',
    evidence: '2026-10-03T01:15:42.318Z', crops: [
      [2, 460, 103, 502, 151], [2, 391, 33, 427, 79], [8, 457, 31, 511, 82],
      [7, 535, 31, 578, 83], [9, 602, 29, 659, 82],
    ] },
  { source: 'game_0011_turn_14.jpg', path: 'tests/fixtures/soren91-sprites/game_0011_turn_14.jpg',
    evidence: '2026-10-03T01:16:31.246Z', crops: [
      [2, 391, 33, 427, 79], [9, 453, 29, 513, 83], [4, 537, 32, 580, 81],
      [11, 595, 28, 664, 83],
    ] },
  { source: 'game_0013_turn_1.jpg', path: 'tests/fixtures/soren91-sprites/game_0013_turn_1.jpg',
    evidence: '2026-10-03 partial observation after PR1574 diagnostics', crops: [
      [7, 334, 104, 389, 158], [5, 462, 31, 508, 82], [3, 540, 31, 577, 82],
      [4, 609, 31, 654, 82],
    ] },
];

const generated = [];
for (const source of sources) {
  const path = fileURLToPath(new URL(source.path, root));
  const sourceSha256 = createHash('sha256').update(readFileSync(path)).digest('hex');
  const { data, info } = await sharp(path).ensureAlpha().raw().toBuffer({ resolveWithObject: true });
  for (const [type, left, top, right, bottom] of source.crops) {
    const roi = { left, top, right, bottom };
    const pixelFilter = source.legacy ? (r, g, b) => {
      const max = Math.max(r, g, b), min = Math.min(r, g, b);
      // Legacy scenery includes cream wall highlights; the flag artwork uses
      // neutral white. Keep those separate when preparing this reviewed row.
      return max - min > 80 || max < 28 || (min > 238 && max - min < 8);
    } : undefined;
    const components = extractSpriteComponents(data, info.width, info.height, roi, { pixelFilter, minPixels: source.legacy ? 8 : 16 });
    let component = components[0];
    // These manually reviewed legacy crops each contain one country. Removing
    // the photographed scenery also removes grey antialias rows between flag
    // colours; retain all those disjoint colour regions, not just the largest.
    // This crop-specific seed operation is deliberately not a runtime merge.
    if (source.legacy && components.length) {
      const pixels = Int32Array.from(components.flatMap(c => [...c.pixels]));
      const left = Math.min(...components.map(c => c.bounds.left));
      const top = Math.min(...components.map(c => c.bounds.top));
      const right = Math.max(...components.map(c => c.bounds.right));
      const bottom = Math.max(...components.map(c => c.bounds.bottom));
      component = { pixels, pixelCount: pixels.length,
        bounds: { left, top, right, bottom, width: right - left, height: bottom - top } };
    }
    if (!component || component.pixelCount < 20) throw new Error(`Missing labelled seed: ${source.source} ${type}`);
    const descriptor = createSpriteDescriptor(data, info.width, info.height, component);
    const id = `${source.source}:${type}:${left},${top}`;
    generated.push({ id, type, country: COUNTRY_NAMES[type], source: source.source, sourceSha256,
      crop: roi, bounds: component.bounds, pixelCount: component.pixelCount,
      rgba: Buffer.from(descriptor).toString('base64') });
    console.log(`${source.source} type=${type} ${COUNTRY_NAMES[type]} pixels=${component.pixelCount} bbox=${JSON.stringify(component.bounds)}`);
  }
}
const output = `// Generated by tools/build_soren91_flag_templates.mjs; do not edit pixels by hand.\n`
  + `// Country labels: country_names.mjs; flags: https://unityroom.com/games/sorengame91\n`
  + `// Distances measure registered multicolour silhouettes, not radius or an HSV type prior.\n`
  + `export const FLAG_TEMPLATE_SIZE = 32;\n`
  + `export const FLAG_TEMPLATES = ${JSON.stringify(generated, null, 2)};\n`;
writeFileSync(new URL('soren91/flag_templates.mjs', root), output);
console.log(`Generated ${generated.length} templates across ${new Set(generated.map(t => t.type)).size} types.`);
