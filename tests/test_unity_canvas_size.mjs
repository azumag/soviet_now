import assert from 'node:assert/strict';
import test from 'node:test';

import { parseUnityCanvasSize, rewriteUnityCanvasSize } from '../lib/unity_canvas_size.mjs';

test('parses balanced 16:9 Unity canvas sizes', () => {
  assert.deepEqual(parseUnityCanvasSize('480,270'), { width: 480, height: 270 });
  assert.deepEqual(parseUnityCanvasSize('640x360'), { width: 640, height: 360 });
  assert.equal(parseUnityCanvasSize(''), null);
});

test('rejects invalid, distorted, and oversized canvas sizes', () => {
  assert.throws(() => parseUnityCanvasSize('480'), /WIDTH,HEIGHT/);
  assert.throws(() => parseUnityCanvasSize('480,300'), /16:9/);
  assert.throws(() => parseUnityCanvasSize('3840,2160'), /between/);
});

test('rewrites the Unity canvas drawing buffer without changing CSS dimensions', () => {
  const html = '<canvas id="unity-canvas" width=320 height="180" style="width: 1280px; height: 720px"></canvas>';
  const result = rewriteUnityCanvasSize(html, { width: 480, height: 270 });
  assert.match(result, /width=480/);
  assert.match(result, /height=270/);
  assert.match(result, /style="width: 1280px; height: 720px"/);
});

test('fails closed when the expected Unity canvas is absent', () => {
  assert.throws(
    () => rewriteUnityCanvasSize('<canvas id="other"></canvas>', { width: 480, height: 270 }),
    /unity-canvas/,
  );
});
