import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import http from 'node:http';
import {execFile} from 'node:child_process';
import {promisify} from 'node:util';
import {fileURLToPath} from 'node:url';
import {installDirectOverlay, installInlineDirectBroadcastOverlay, loadDirectOverlayConfig} from '../lib/direct_overlay.mjs';
import {enforceVisibleWindow} from '../shared_overlay.mjs';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const run = promisify(execFile);
const html = fs.readFileSync(path.join(root, 'overlays/direct_broadcast_overlay.html'), 'utf8');
const expectedGame = [64, 128, 191];

async function contract(headed) {
  const {chromium} = await import('playwright');
  const {default: sharp} = await import('sharp');
  const now = Math.floor(Date.now() / 1000);
  let text = 'SOREN/CORNER: SOREN / sorengame / 進行中';
  let version = 1;
  let marker = 8;
  const feed = () => ({gameGapEnabled: true, updatedAt: now, feeds: {
    showStatusG: {text, updatedAt: now, lineCount: marker},
  }, notifications: {events: [], generators: [], work: {active: false}}});
  const game = `<!doctype html><style>html,body{margin:0;overflow:hidden;background:#050914}
    canvas{position:fixed;left:0;top:90px;width:960px;height:540px}
    .rail{position:fixed;left:0;top:0;width:960px;height:90px;background:#20a040}
    .side{position:fixed;left:960px;top:0;width:320px;height:720px;background:#d06020}</style>
    <div class="rail"></div><div class="side"></div><canvas id="game" width="576" height="324"></canvas>
    <script>window.fixtureFrame=0;window.fixtureNow=${now};window.fixtureSession=crypto.randomUUID();
    const gl=document.querySelector('canvas').getContext('webgl',{preserveDrawingBuffer:false});
    function draw(){gl.clearColor(.25,.5,.75,1);gl.clear(gl.COLOR_BUFFER_BIT);window.fixtureFrame++;requestAnimationFrame(draw)}draw();</script>`;
  const server = http.createServer((req, res) => {
    if (req.url === '/__soren_overlay/broadcast/state') {
      res.setHeader('Content-Type', 'application/json'); res.end(JSON.stringify(feed()));
    } else {
      res.setHeader('Content-Type', 'text/html; charset=utf-8');
      res.end(req.url === '/' ? game : html + `<!-- fixture-template-${version} -->`);
    }
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const browser = await chromium.launch({headless: !headed, args: [
    '--use-gl=angle', '--use-angle=swiftshader', '--enable-unsafe-swiftshader',
    ...(headed ? ['--kiosk', '--test-type', '--disable-infobars', '--window-position=0,0', '--window-size=1280,720'] : []),
  ], ignoreDefaultArgs: headed ? ['--enable-automation'] : []});
  const artifactDir = process.env.SOREN_OVERLAY_ARTIFACT_DIR;
  let page;
  const geometry = () => page.evaluate(() => {
    const r = document.querySelector('canvas').getBoundingClientRect();
    return {inner: [innerWidth, innerHeight], outer: [outerWidth, outerHeight],
      origin: [screenX, screenY], screen: [screen.width, screen.height], dpr: devicePixelRatio,
      canvas: [r.x, r.y, r.width, r.height]};
  });
  try {
    page = await browser.newPage({viewport: headed ? null : {width: 1280, height: 720}});
    await page.addInitScript(() => { Date.now = () => (window.parent.fixtureNow || window.fixtureNow) * 1000; });
    await page.goto(`http://127.0.0.1:${server.address().port}/`);
    await page.waitForFunction(() => window.fixtureFrame > 2);
    if (headed) {
      // Kiosk flags alone do not position a newly created CDP target at (0,0).
      // Use the existing headed-window contract and verify its physical bounds.
      await enforceVisibleWindow(page);
      await page.waitForFunction(() => innerWidth === 1280 && innerHeight === 720
        && outerWidth === 1280 && outerHeight === 720 && screenX === 0 && screenY === 0,
      undefined, {timeout: 10000});
      assert.deepEqual(await geometry(), {inner: [1280, 720], outer: [1280, 720],
        origin: [0, 0], screen: [1280, 720], dpr: 1, canvas: [0, 90, 960, 540]});
    }
    const gameSession = await page.evaluate(() => window.fixtureSession);
    const config = loadDirectOverlayConfig({SOREN_STREAM_BACKEND: 'ffmpeg'}, 'linux');
    config.surfaces = config.surfaces.filter(s => s.region === 'game-gap');
    config.surfaces[0].pollMs = 100;
    const id = config.surfaces[0].elementId;
    const frames = () => page.evaluate(id => [id, `${id}-buffer`].map(id => {
      const e = document.getElementById(id); if (!e) return null;
      const r = e.getBoundingClientRect(), s = getComputedStyle(e);
      return {id, visible: s.display !== 'none' && s.visibility === 'visible' && s.opacity !== '0',
        box: [r.x, r.y, r.width, r.height], text: e.contentDocument?.getElementById('hanjuku-gap')?.innerText || ''};
    }).filter(Boolean), id);
    async function pixels(label, gap = false) {
      let raw;
      if (headed) {
        assert.match(process.env.DISPLAY || '', /^:\d+(?:\.\d+)?$/);
        const display = process.env.DISPLAY.includes('.') ? process.env.DISPLAY : `${process.env.DISPLAY}.0`;
        const result = await run('ffmpeg', ['-v', 'error', '-filter_threads', '1', '-f', 'x11grab', '-video_size', '1280x720',
          '-i', `${display}+0,0`, '-frames:v', '1', '-threads', '1', '-pix_fmt', 'rgb24', '-f', 'rawvideo', 'pipe:1'],
        {encoding: 'buffer', maxBuffer: 5_000_000, timeout: 10000});
        raw = result.stdout;
        assert.equal(raw.length, 1280 * 720 * 3);
      } else raw = await sharp(await page.screenshot()).removeAlpha().raw().toBuffer();
      const at = (x, y) => [...raw.subarray((y * 1280 + x) * 3, (y * 1280 + x) * 3 + 3)];
      if (artifactDir) {
        fs.mkdirSync(artifactDir, {recursive: true});
        await sharp(raw, {raw: {width: 1280, height: 720, channels: 3}}).png()
          .toFile(path.join(artifactDir, `${headed ? 'xvfb' : 'headless'}-${label}.png`));
        fs.writeFileSync(path.join(artifactDir, `${headed ? 'xvfb' : 'headless'}-${label}.json`),
          JSON.stringify(await geometry(), null, 2));
      }
      for (const point of [[50, 100], [350, 350], [700, 600]]) {
        const actual = at(...point);
        assert.ok(actual.every((v, i) => Math.abs(v - expectedGame[i]) <= 2), `${label}: game ${point}: ${actual}`);
      }
      assert.deepEqual(at(400, 40), [32, 160, 64], `${label}: top rail`);
      assert.deepEqual(at(1100, 300), [208, 96, 32], `${label}: sidebar`);
      if (gap) assert.notDeepEqual(at(850, 500), expectedGame, `${label}: card must render in padding`);
      else assert.deepEqual(at(850, 500), expectedGame, `${label}: cleared padding`);
    }
    const waitHidden = () => page.waitForFunction(({id, marker}) => [id, `${id}-buffer`].some(id =>
      document.getElementById(id)?.contentWindow?.__sorenBroadcastOverlayHealth?.showStatusGLineCount === marker)
      && [id, `${id}-buffer`].every(id => {
      const e = document.getElementById(id); return !e || getComputedStyle(e).display === 'none';
    }), {id, marker});
    const waitCard = () => page.waitForFunction(({id, marker}) => [id, `${id}-buffer`].some(id => {
      const e = document.getElementById(id); return e && getComputedStyle(e).display !== 'none'
        && getComputedStyle(e).visibility === 'visible'
        && e.contentWindow?.__sorenBroadcastOverlayHealth?.showStatusGLineCount === marker
        && e.contentDocument?.getElementById('hanjuku-gap')?.innerText.includes('観測');
    }), {id, marker});
    const setGap = (left = 721) => {
      marker++;
      text = `SOREN/CORNER: RETRO / hanjuku-hero / 進行中\n半熟英雄 / 最終観測・記録\n第2話 / 所持金 123G\n`
        + `投影余白: x=${left} w=${960-left} until=${now+30}\n余白占領記録: アルマムーン / ナキューメラ\n`
        + `余白駐留: until=${now+30} アルマムーン=将軍その一/将軍その二/将軍その三`;
    };
    await installDirectOverlay(page, config);
    await page.waitForFunction(id => [id, `${id}-buffer`].some(id =>
      document.getElementById(id)?.contentWindow?.__sorenBroadcastOverlayHealth?.updatedAt > 0), id);
    await pixels('empty');
    await waitHidden();
    const startFrame = await page.evaluate(() => window.fixtureFrame);
    setGap(); await waitCard();
    let visible = (await frames()).filter(f => f.visible);
    assert.equal(visible.length, 1); assert.deepEqual(visible[0].box, [721, 90, 239, 540]);
    assert.match(visible[0].text, /将軍その一/); await pixels('card', true);
    const oldActive = visible[0].id;
    version++;
    await page.waitForFunction(({id, oldActive}) => [id, `${id}-buffer`].some(id => {
      const e = document.getElementById(id); return id !== oldActive && e && getComputedStyle(e).visibility === 'visible'
        && getComputedStyle(e).display !== 'none';
    }), {id, oldActive});
    visible = (await frames()).filter(f => f.visible);
    assert.equal(visible.length, 1); assert.deepEqual(visible[0].box, [721, 90, 239, 540]);
    await pixels('buffer-swap', true);
    await page.evaluate(stamp => window.fixtureNow = stamp, now + 31);
    await waitHidden(); await pixels('expired');
    await page.evaluate(stamp => window.fixtureNow = stamp, now);
    text = 'SOREN/CORNER: RETRO / another-game / 進行中'; marker++; version++;
    await waitHidden(); await pixels('other-game');
    setGap(720); await waitCard();
    visible = (await frames()).filter(f => f.visible);
    assert.deepEqual(visible[0].box, [720, 90, 240, 540]); await pixels('reshown', true);
    text = 'SOREN/CORNER: SOREN / sorengame / 進行中'; marker++; await waitHidden(); await pixels('restored');
    // The single inline frame has the same hide/crop/reshow contract.
    await page.evaluate(id => {document.getElementById(id)?.remove();document.getElementById(`${id}-buffer`)?.remove();}, id);
    await installInlineDirectBroadcastOverlay(page, config, {watch: false});
    await waitHidden(); await pixels('inline-empty');
    setGap(); await waitCard();
    assert.deepEqual((await frames())[0].box, [721, 90, 239, 540]); await pixels('inline-card', true);
    text = 'SOREN/CORNER: SOREN / sorengame / 進行中'; marker++; await waitHidden(); await pixels('inline-restored');
    assert.ok(await page.evaluate(start => window.fixtureFrame > start, startFrame));
    assert.equal(await page.evaluate(() => window.fixtureSession), gameSession);
    assert.deepEqual(await page.evaluate(() => [document.querySelector('canvas').width, document.querySelector('canvas').height]), [576, 324]);
  } catch (error) {
    if (artifactDir && page && !page.isClosed()) {
      fs.mkdirSync(artifactDir, {recursive: true});
      fs.writeFileSync(path.join(artifactDir, `${headed ? 'xvfb' : 'headless'}-failure.json`),
        JSON.stringify(await geometry(), null, 2));
      await page.screenshot({path: path.join(artifactDir, `${headed ? 'xvfb' : 'headless'}-failure-page.png`)});
    }
    throw error;
  } finally {await browser.close(); await new Promise(resolve => server.close(resolve));}
}

test('empty and buffered gap frames preserve the WebGL game and only occupy measured padding',
  {skip: process.env.SOREN_OVERLAY_BROWSER_TESTS !== '1', timeout: 60000}, () => contract(false));
test('actual Xvfb input keeps WebGL pixels through empty/card/swap/expiry/restore',
  {skip: process.env.SOREN_OVERLAY_XVFB_TESTS !== '1', timeout: 60000}, () => contract(true));
