import assert from 'node:assert/strict';
import test from 'node:test';

import { installAnimationFrameLimit } from '../browser_frame_limiter.mjs';


function fakeBrowserWindow() {
  let nextNativeHandle = 1;
  const nativeCallbacks = new Map();
  const fakeWindow = {
    requestAnimationFrame(callback) {
      const handle = nextNativeHandle++;
      nativeCallbacks.set(handle, callback);
      return handle;
    },
    cancelAnimationFrame(handle) {
      nativeCallbacks.delete(handle);
    },
  };
  return {
    fakeWindow,
    runNativeFrame(timestamp) {
      const ready = [...nativeCallbacks.values()];
      nativeCallbacks.clear();
      for (const callback of ready) callback(timestamp);
    },
    pendingNativeFrames: () => nativeCallbacks.size,
  };
}

test('30fps limiter batches callbacks and releases the other native frames', () => {
  const savedWindow = globalThis.window;
  const browser = fakeBrowserWindow();
  globalThis.window = browser.fakeWindow;
  try {
    installAnimationFrameLimit({ renderFps: 30 });
    const renderedAt = [];
    const loop = (timestamp) => {
      renderedAt.push(timestamp);
      if (timestamp < 1200) window.requestAnimationFrame(loop);
    };
    window.requestAnimationFrame(loop);

    for (let timestamp = 100; timestamp <= 1200; timestamp += 1000 / 60) {
      browser.runNativeFrame(timestamp);
    }

    assert.ok(renderedAt.length >= 31 && renderedAt.length <= 34, `frames=${renderedAt.length}`);
    for (let index = 1; index < renderedAt.length; index += 1) {
      assert.ok(renderedAt[index] - renderedAt[index - 1] >= 32.8);
    }
    assert.equal(window.__sorenRenderStats.limitedToFps, 30);
    assert.ok(window.__sorenRenderStats.measuredFps >= 29);
    assert.ok(window.__sorenRenderStats.measuredFps <= 31);
  } finally {
    if (savedWindow === undefined) delete globalThis.window;
    else globalThis.window = savedWindow;
  }
});

test('30fps limiter carries its deadline on a 100Hz Xvfb cadence', () => {
  const savedWindow = globalThis.window;
  const browser = fakeBrowserWindow();
  globalThis.window = browser.fakeWindow;
  try {
    installAnimationFrameLimit({ renderFps: 30 });
    const renderedAt = [];
    const loop = (timestamp) => {
      renderedAt.push(timestamp);
      if (timestamp < 2200) window.requestAnimationFrame(loop);
    };
    window.requestAnimationFrame(loop);

    for (let timestamp = 100; timestamp <= 2200; timestamp += 10) {
      browser.runNativeFrame(timestamp);
    }

    assert.ok(renderedAt.length >= 62 && renderedAt.length <= 65, `frames=${renderedAt.length}`);
    assert.equal(window.__sorenRenderStats.limitedToFps, 30);
    assert.ok(window.__sorenRenderStats.measuredFps >= 29);
    assert.ok(window.__sorenRenderStats.measuredFps <= 31);
  } finally {
    if (savedWindow === undefined) delete globalThis.window;
    else globalThis.window = savedWindow;
  }
});

test('cancelling the last callback cancels the native frame too', () => {
  const savedWindow = globalThis.window;
  const browser = fakeBrowserWindow();
  globalThis.window = browser.fakeWindow;
  try {
    installAnimationFrameLimit({ renderFps: 30 });
    const handle = window.requestAnimationFrame(() => assert.fail('cancelled callback ran'));
    assert.equal(browser.pendingNativeFrames(), 1);
    window.cancelAnimationFrame(handle);
    assert.equal(browser.pendingNativeFrames(), 0);
    browser.runNativeFrame(100);
  } finally {
    if (savedWindow === undefined) delete globalThis.window;
    else globalThis.window = savedWindow;
  }
});

test('alternate-game pause retains the Unity callback without dispatching it', () => {
  const savedWindow = globalThis.window;
  const browser = fakeBrowserWindow();
  globalThis.window = browser.fakeWindow;
  try {
    installAnimationFrameLimit({ renderFps: 30 });
    let calls = 0;
    window.__sorenRenderPaused = true;
    window.requestAnimationFrame(() => { calls += 1; });
    browser.runNativeFrame(100);
    browser.runNativeFrame(140);
    assert.equal(calls, 0);
    window.__sorenRenderPaused = false;
    browser.runNativeFrame(180);
    assert.equal(calls, 1);
  } finally {
    if (savedWindow === undefined) delete globalThis.window;
    else globalThis.window = savedWindow;
  }
});
