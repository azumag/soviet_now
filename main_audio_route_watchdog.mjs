import { chromium } from 'playwright';
import fs from 'fs';

const CDP_URL = process.env.SOREN_CDP_URL || 'http://127.0.0.1:9222';
const ORIGIN = process.env.SOREN_GAME_ORIGIN || 'http://localhost:8080';
const LABEL = process.env.SOREN_CHROME_AUDIO_OUTPUT_LABEL || '';
const PID_FILE = 'tmp/state/main_audio_route_watchdog.pid';
const LOG_FILE = 'tmp/main_audio_route_watchdog.log';
const INTERVAL_MS = Number.parseInt(process.env.MAIN_AUDIO_ROUTE_WATCHDOG_INTERVAL_MS || '1000', 10);

function log(message) {
  const line = `[${new Date().toISOString()}] ${message}`;
  console.log(line);
  try { fs.appendFileSync(LOG_FILE, `${line}\n`); } catch {}
}

function writePid() {
  try {
    fs.mkdirSync('tmp/state', { recursive: true });
    fs.writeFileSync(PID_FILE, `${process.pid}\n`);
  } catch {}
}

async function routeOnce() {
  if (!LABEL) {
    return { ok: false, error: 'per-context audio routing disabled' };
  }
  const browser = await chromium.connectOverCDP(CDP_URL);
  try {
    const context = browser.contexts()[0];
    const page = context?.pages().find(p => p.url().startsWith(ORIGIN)) || context?.pages()[0];
    if (!context || !page) return { ok: false, error: 'no page' };

    const session = await context.newCDPSession(page);
    await session.send('Browser.grantPermissions', {
      origin: ORIGIN,
      permissions: ['speakerSelection', 'audioCapture'],
    });

    return await page.evaluate(async (label) => {
      if (window.__sorenAudioHealBusy) {
        return { ok: false, error: 'audio heal busy' };
      }
      window.__sorenAudioHealBusy = true;
      try {
        const contexts = [...(window.__sorenAudioContexts || [])];
        try {
          const unityContext = (typeof Module !== 'undefined' && Module.WebAudio)
            ? Module.WebAudio.audioContext
            : null;
          if (unityContext && !contexts.includes(unityContext)) contexts.push(unityContext);
        } catch {}
        const ctx = contexts[0];
        if (!ctx || typeof ctx.setSinkId !== 'function') {
          return { ok: false, error: 'no audio context' };
        }

      const devices = await navigator.mediaDevices.enumerateDevices();
      const target = devices.find(device =>
        device.kind === 'audiooutput' &&
        device.label &&
        device.label.toLowerCase().includes(String(label).toLowerCase())
      );
      if (!target) {
        return {
          ok: false,
          error: `audio output not found: ${label}`,
          state: ctx.state,
          sinkId: ctx.sinkId || '',
        };
      }

      if ((ctx.sinkId || '') === target.deviceId && ctx.state === 'running') {
        window.__sorenAudioOutputDeviceId = target.deviceId;
        window.__sorenAudioOutputError = '';
        return { ok: true, state: ctx.state, sinkId: ctx.sinkId || '', unchanged: true };
      }

      if (ctx.state === 'suspended' && ctx.sinkId) {
        await ctx.setSinkId('');
        try { ctx.resume().catch(() => {}); } catch {}
        await new Promise(resolve => setTimeout(resolve, 500));
      }
      await ctx.setSinkId(target.deviceId);
      if (ctx.state === 'suspended') {
        try { ctx.resume().catch(() => {}); } catch {}
        await new Promise(resolve => setTimeout(resolve, 700));
      }
      window.__sorenAudioOutputDeviceId = target.deviceId;
      window.__sorenAudioOutputError = '';
        return { ok: ctx.state === 'running', state: ctx.state, sinkId: ctx.sinkId || '' };
      } finally {
        window.__sorenAudioHealBusy = false;
      }
    }, LABEL);
  } finally {
    await browser.close().catch(() => {});
  }
}

async function main() {
  writePid();
  log(`start cdp=${CDP_URL} origin=${ORIGIN} label=${LABEL}`);
  while (true) {
    try {
      const result = await routeOnce();
      log(JSON.stringify(result));
    } catch (error) {
      log(`ERROR ${(error && error.message) || String(error)}`);
    }
    await new Promise(resolve => setTimeout(resolve, Number.isFinite(INTERVAL_MS) ? INTERVAL_MS : 5000));
  }
}

main().catch(error => {
  log(`FATAL ${(error && error.stack) || error}`);
  process.exit(1);
});
