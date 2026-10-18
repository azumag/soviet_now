import assert from 'node:assert/strict';
import test from 'node:test';
import vm from 'node:vm';

import { installDirectOverlay, loadDirectOverlayConfig } from '../lib/direct_overlay.mjs';

const base = {
  SOREN_STREAM_BACKEND: 'ffmpeg',
  SOREN_DIRECT_TWICA_OVERLAY_ENABLED: '1',
  SOREN_DIRECT_TWICA_OVERLAY_URL: 'https://twica.bluemoon.works/overlay/demo?pName=true',
};
const expectedStyle = {
  inset: '0', width: '100vw', height: '100vh', zIndex: '2147483645',
  transform: 'translateX(calc(100vw / 3))',
};

for (const [name, overrides] of [
  ['dashboard', {}],
  ['fullscreen', { SOREN_DIRECT_STAGE_LAYOUT: 'fullscreen' }],
  ['legacy dashboard', { SOREN_DIRECT_BROADCAST_OVERLAY_ENABLED: '0' }],
]) {
  test(`TwiCa shifts only X, without resizing or moving other ${name} surfaces`, () => {
    const env = { ...base, ...overrides };
    const config = loadDirectOverlayConfig(env, 'linux');
    const twica = config.surfaces.find((item) => item.key === 'twica');
    assert.deepEqual(twica.style, expectedStyle);
    assert.equal(twica.srcUrl, 'http://127.0.0.1:18080/overlay/demo?pName=true');
    assert.equal(twica.upstreamUrl, base.SOREN_DIRECT_TWICA_OVERLAY_URL);
    const disabled = loadDirectOverlayConfig({
      ...env, SOREN_DIRECT_TWICA_OVERLAY_ENABLED: '0',
    }, 'linux');
    assert.deepEqual(config.stage, disabled.stage);
    assert.deepEqual(config.broadcast, disabled.broadcast);
    assert.deepEqual(config.surfaces.filter((item) => item.key !== 'twica'), disabled.surfaces);
    assert.ok(config.surfaces.filter((item) => item.key !== 'twica')
      .every((item) => item.style.transform !== expectedStyle.transform));
  });
}

for (const size of ['1920x1080', '3840x2160']) {
  test(`TwiCa uses a viewport-relative X offset at ${size}`, () => {
    const config = loadDirectOverlayConfig({ ...base, SOREN_DIRECT_STREAM_SIZE: size }, 'linux');
    assert.deepEqual(config.surfaces.find((item) => item.key === 'twica').style, expectedStyle);
  });
}

function browserFixture() {
  const frames = [];
  const window = {};
  window.top = window;
  const document = {
    readyState: 'complete',
    body: { appendChild(frame) { frames.push(frame); } },
    getElementById(id) { return frames.find((frame) => frame.id === id); },
    createElement(tag) {
      assert.equal(tag, 'iframe');
      return { style: {}, dataset: {}, setAttribute() {} };
    },
  };
  return {
    frames,
    run(callback, payload) {
      vm.runInNewContext(`(${callback.toString()})(payload)`, { window, document, payload });
    },
  };
}

test('installer preserves the offset on current page and reload, without a second external frame', async () => {
  const config = loadDirectOverlayConfig(base, 'linux');
  const twica = config.surfaces.find((item) => item.key === 'twica');
  const fixture = browserFixture();
  let init;
  const page = {
    async addInitScript(callback, payload) { init = [callback, payload]; },
    async evaluate(callback, payload) { fixture.run(callback, payload); },
  };
  const isolated = { ...config, surfaces: [twica] };
  assert.equal(await installDirectOverlay(page, isolated), true);
  assert.equal(await installDirectOverlay(page, isolated), true);
  const reloaded = browserFixture();
  reloaded.run(...init);
  for (const { frames } of [fixture, reloaded]) {
    assert.equal(frames.length, 1);
    const [frame] = frames;
    for (const [key, value] of Object.entries(expectedStyle)) assert.equal(frame.style[key], value);
    assert.equal(frame.style.position, 'fixed');
    assert.equal(frame.style.background, 'transparent');
    assert.equal(frame.style.pointerEvents, 'none');
    assert.equal(frame.dataset.sorenExternalOverlay, '1');
    assert.equal(frame.src, twica.srcUrl);
  }
});

test('disabled direct overlay does not install a frame', async () => {
  const config = loadDirectOverlayConfig({ ...base, SOREN_DIRECT_OVERLAY_ENABLED: '0' }, 'linux');
  assert.equal(await installDirectOverlay({}, config), false);
});
