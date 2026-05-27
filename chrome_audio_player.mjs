import { chromium } from 'playwright';
import fs from 'fs';
import path from 'path';

const audioFile = process.argv[2] || '';
const label = process.argv[3] || process.env.SOREN_CHROME_AUDIO_OUTPUT_LABEL || process.env.SAY_AUDIO_DEVICE || '';
const cdpUrl = process.env.SOREN_CDP_URL || `http://127.0.0.1:${process.env.SOREN_CDP_PORT || '9322'}`;
const origin = process.env.SOREN_GAME_ORIGIN || 'http://localhost:8080';
const logFile = process.env.SOREN_CHROME_AUDIO_PLAYER_LOG || 'tmp/.say_queue/chrome_audio_player.log';

function log(message) {
  const line = `[${new Date().toISOString()}] ${message}`;
  try {
    fs.mkdirSync(path.dirname(logFile), { recursive: true });
    fs.appendFileSync(logFile, `${line}\n`);
  } catch {}
}

if (!audioFile || !fs.existsSync(audioFile) || fs.statSync(audioFile).size <= 0) {
  log(`missing audio file: ${audioFile}`);
  process.exit(2);
}

let browser;
try {
  browser = await chromium.connectOverCDP(cdpUrl);
  const context = browser.contexts()[0];
  const page = context?.pages().find(p => p.url().startsWith(origin)) || context?.pages()[0];
  if (!context || !page) {
    throw new Error('no Chrome page available');
  }

  try {
    const session = await context.newCDPSession(page);
    await session.send('Browser.grantPermissions', {
      origin,
      permissions: ['speakerSelection', 'audioCapture'],
    });
  } catch (error) {
    log(`grantPermissions warning: ${error?.message || String(error)}`);
  }

  const b64 = fs.readFileSync(audioFile).toString('base64');
  const result = await page.evaluate(async ({ b64, label }) => {
    const devices = await navigator.mediaDevices.enumerateDevices();
    const outputs = devices.filter(device => device.kind === 'audiooutput');
    const lowerLabel = String(label || '').toLowerCase();
    const target = lowerLabel
      ? outputs.find(device => device.label && device.label.toLowerCase().includes(lowerLabel))
      : null;

    const audio = new Audio(`data:audio/wav;base64,${b64}`);
    audio.preload = 'auto';
    if (target && typeof audio.setSinkId === 'function') {
      await audio.setSinkId(target.deviceId);
    }

    await audio.play();
    await new Promise((resolve, reject) => {
      audio.onended = resolve;
      audio.onerror = () => reject(new Error(audio.error?.message || 'audio element error'));
    });
    return {
      ok: true,
      target: target?.label || '',
      sinkId: audio.sinkId || '',
      outputs: outputs.map(device => device.label).filter(Boolean),
    };
  }, { b64, label });

  log(`played ${audioFile} target=${result.target || 'default'} sink=${result.sinkId || 'default'}`);
  process.exit(0);
} catch (error) {
  log(`ERROR ${(error && error.stack) || error}`);
  process.exit(1);
} finally {
  await browser?.close().catch(() => {});
}
