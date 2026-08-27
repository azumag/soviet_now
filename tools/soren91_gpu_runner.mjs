import { chromium } from 'playwright';
import { spawnSync } from 'node:child_process';
import fs from 'node:fs';

const width = Number(process.env.SOREN91_POC_WIDTH || 960);
const height = Number(process.env.SOREN91_POC_HEIGHT || 540);
const minFps = Number(process.env.SOREN91_POC_MIN_FPS || 25);
const measureSec = Number(process.env.SOREN91_POC_MEASURE_SEC || 60);
const outputPath = process.env.SOREN91_POC_RESULT_PATH || '/tmp/soren91-poc-result.json';
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

function command(command, args, timeout = 20_000) {
  const result = spawnSync(command, args, { encoding: 'utf8', timeout });
  return {
    ok: result.status === 0,
    status: result.status,
    stdout: String(result.stdout || '').trim(),
    stderr: String(result.stderr || '').trim(),
  };
}

function emit(result) {
  fs.writeFileSync(outputPath, `${JSON.stringify(result, null, 2)}\n`);
  console.log(`SOREN91_POC_RESULT=${JSON.stringify(result)}`);
}

let browser;
try {
  const nvidia = command('nvidia-smi', ['--query-gpu=name,driver_version', '--format=csv,noheader']);
  const nvenc = command('ffmpeg', [
    '-hide_banner', '-loglevel', 'error', '-f', 'lavfi', '-i', 'color=size=960x540:rate=30',
    '-frames:v', '30', '-c:v', 'h264_nvenc', '-f', 'null', '-',
  ], 30_000);

  browser = await chromium.launch({
    headless: false,
    args: [
      '--no-sandbox', '--disable-dev-shm-usage', '--enable-gpu',
      '--use-gl=angle', '--use-angle=gl', '--ignore-gpu-blocklist',
      '--disable-software-rasterizer', '--disable-gpu-vsync', '--disable-frame-rate-limit',
      '--autoplay-policy=no-user-gesture-required', '--disable-background-timer-throttling',
      '--disable-backgrounding-occluded-windows', '--disable-renderer-backgrounding',
      `--window-size=${width},${height}`,
    ],
  });
  const context = await browser.newContext({ viewport: { width, height }, deviceScaleFactor: 1 });
  const page = await context.newPage();
  await page.addInitScript(() => {
    window.__soren91NativeRaf = window.requestAnimationFrame.bind(window);
  });
  await page.route('**/*play.unityroom.com/**', async (route) => {
    if (route.request().resourceType() !== 'document') return route.continue();
    const response = await route.fetch();
    let body = await response.text();
    body = body.replace('companyName: "empty",', 'companyName: "empty", devicePixelRatio: 1,');
    await route.fulfill({ response, body });
  });

  const landing = await fetch('https://unityroom.com/games/sorengame91', {
    headers: { 'user-agent': 'Mozilla/5.0', 'accept-language': 'ja' },
  }).then((response) => response.text());
  const match = landing.match(/(?:src|href)=["']([^"']*play\.unityroom\.com[^"']*)["']/i);
  if (!match) throw new Error('unityroom game URL not found');
  const gameUrl = new URL(match[1].replace(/&amp;/g, '&'), 'https://unityroom.com/').href;
  await page.goto(gameUrl, { waitUntil: 'domcontentloaded', timeout: 60_000 });
  await page.waitForSelector('canvas', { timeout: 90_000 });
  await page.waitForFunction(() => {
    const bar = document.getElementById('unity-loading-bar');
    return !bar || bar.style.display === 'none';
  }, null, { timeout: 90_000 });
  await page.evaluate(({ width: w, height: h }) => {
    document.body.style.margin = '0';
    document.body.style.overflow = 'hidden';
    for (const selector of ['#unity-footer', '#unity-loading-bar']) {
      const element = document.querySelector(selector);
      if (element) element.style.display = 'none';
    }
    const container = document.querySelector('#unity-container');
    const canvas = document.querySelector('#unity-canvas');
    Object.assign(container.style, { position: 'fixed', left: '0', top: '0', width: `${w}px`, height: `${h}px` });
    Object.assign(canvas.style, { width: `${w}px`, height: `${h}px`, display: 'block' });
  }, { width, height });

  const canvas = page.locator('canvas');
  const box = await canvas.boundingBox();
  if (!box) throw new Error('canvas has no bounding box');
  await page.mouse.click(box.x + box.width * (630 / 1280), box.y + box.height * (560 / 720));
  await sleep(500);
  await page.keyboard.press('Control+a');
  await page.keyboard.type('DoCiAI:US', { delay: 30 });
  await page.mouse.click(box.x + box.width * (630 / 1280), box.y + box.height * (645 / 720));
  await sleep(10_000);

  const probe = await page.evaluate(async ({ seconds }) => {
    const raf = window.__soren91NativeRaf || window.requestAnimationFrame.bind(window);
    let frames = 0;
    const deltas = [];
    let previous = performance.now();
    const started = previous;
    await new Promise((resolve) => {
      const tick = (timestamp) => {
        frames += 1;
        deltas.push(timestamp - previous);
        previous = timestamp;
        if (performance.now() - started < seconds * 1000) raf(tick);
        else resolve();
      };
      raf(tick);
    });
    deltas.shift();
    deltas.sort((a, b) => a - b);
    const percentile = (value) => deltas.length
      ? Number(deltas[Math.floor((deltas.length - 1) * value)].toFixed(1))
      : null;
    const target = document.querySelector('#unity-canvas') || document.querySelector('canvas');
    const gl = target.getContext('webgl2');
    const debug = gl?.getExtension('WEBGL_debug_renderer_info');
    return {
      fps: Number((frames / ((performance.now() - started) / 1000)).toFixed(1)),
      deltaMs: { p50: percentile(0.5), p90: percentile(0.9), p99: percentile(0.99) },
      renderer: gl ? gl.getParameter(debug ? debug.UNMASKED_RENDERER_WEBGL : gl.RENDERER) : null,
      webgl2: Boolean(gl),
      drawingBuffer: gl ? [gl.drawingBufferWidth, gl.drawingBufferHeight] : null,
      canvas: target ? [target.width, target.height, target.clientWidth, target.clientHeight] : null,
    };
  }, { seconds: measureSec });

  const renderer = String(probe.renderer || '');
  const hardwareRenderer = /NVIDIA/i.test(renderer) && !/(swiftshader|llvmpipe|software)/i.test(renderer);
  const correctBuffer = probe.drawingBuffer?.[0] === width && probe.drawingBuffer?.[1] === height;
  const result = {
    pass: Boolean(nvidia.ok && nvenc.ok && hardwareRenderer && probe.webgl2 && correctBuffer && probe.fps >= minFps),
    criteria: { minFps, width, height, measureSec },
    gpu: nvidia.stdout || nvidia.stderr,
    nvenc: { pass: nvenc.ok, error: nvenc.ok ? '' : nvenc.stderr.slice(-500) },
    probe,
    checks: { hardwareRenderer, webgl2: probe.webgl2, correctBuffer, fps: probe.fps >= minFps },
  };
  emit(result);
  process.exitCode = result.pass ? 0 : 1;
} catch (error) {
  emit({ pass: false, reason: error?.message || String(error) });
  process.exitCode = 1;
} finally {
  await browser?.close().catch(() => {});
}
