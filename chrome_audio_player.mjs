import { chromium } from 'playwright';
import fs from 'fs';
import path from 'path';

const stopOnly = process.argv[2] === '--stop';
const audioFile = stopOnly ? '' : (process.argv[2] || '');
const label = (stopOnly ? process.argv[3] : process.argv[3]) || process.env.SOREN_CHROME_AUDIO_OUTPUT_LABEL || process.env.SAY_AUDIO_DEVICE || '';
const cdpUrl = process.env.SOREN_CDP_URL || `http://127.0.0.1:${process.env.SOREN_CDP_PORT || '9322'}`;
const origin = process.env.SOREN_GAME_ORIGIN || 'http://localhost:8080';
const logFile = process.env.SOREN_CHROME_AUDIO_PLAYER_LOG || 'tmp/.say_queue/chrome_audio_player.log';
const playerKey = String(label || 'default').toLowerCase();

function log(message) {
  const line = `[${new Date().toISOString()}] ${message}`;
  try {
    fs.mkdirSync(path.dirname(logFile), { recursive: true });
    fs.appendFileSync(logFile, `${line}\n`);
  } catch {}
}

async function stopTaggedAudioOnPage(page, key) {
  return await page.evaluate((targetKey) => {
    const audios = new Set();
    const registry = window.__sorenChromeAudioPlayers || {};
    for (const [key, audio] of Object.entries(registry)) {
      if (!targetKey || key === targetKey) audios.add(audio);
    }
    for (const audio of document.querySelectorAll('audio[data-soren-chrome-audio-player="1"]')) {
      if (!targetKey || audio.getAttribute('data-soren-chrome-audio-key') === targetKey) {
        audios.add(audio);
      }
    }
    let stopped = 0;
    for (const audio of audios) {
      try { audio.pause(); } catch {}
      try { audio.removeAttribute('src'); } catch {}
      try { audio.load(); } catch {}
      try { audio.remove(); } catch {}
      stopped += 1;
    }
    if (window.__sorenChromeAudioPlayers) {
      for (const key of Object.keys(window.__sorenChromeAudioPlayers)) {
        if (!targetKey || key === targetKey) delete window.__sorenChromeAudioPlayers[key];
      }
    }
    return stopped;
  }, key);
}

async function stopTaggedAudio(context, key) {
  let stopped = 0;
  for (const page of context?.pages?.() || []) {
    try {
      stopped += await stopTaggedAudioOnPage(page, key);
    } catch {}
  }
  return stopped;
}

if (!stopOnly && (!audioFile || !fs.existsSync(audioFile) || fs.statSync(audioFile).size <= 0)) {
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

  if (stopOnly) {
    const stopped = await stopTaggedAudio(context, playerKey);
    log(`stopped tagged audio key=${playerKey} count=${stopped}`);
    process.exit(0);
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
  await stopTaggedAudioOnPage(page, playerKey);
  const result = await page.evaluate(async ({ b64, label, playerKey }) => {
    const devices = await navigator.mediaDevices.enumerateDevices();
    const outputs = devices.filter(device => device.kind === 'audiooutput');
    const lowerLabel = String(label || '').toLowerCase();
    const target = lowerLabel
      ? outputs.find(device => device.label && device.label.toLowerCase().includes(lowerLabel))
      : null;

    window.__sorenChromeAudioPlayers = window.__sorenChromeAudioPlayers || {};
    const existing = window.__sorenChromeAudioPlayers[playerKey];
    if (existing) {
      try { existing.pause(); } catch {}
      try { existing.removeAttribute('src'); } catch {}
      try { existing.load(); } catch {}
      try { existing.remove(); } catch {}
      delete window.__sorenChromeAudioPlayers[playerKey];
    }

    const audio = new Audio(`data:audio/wav;base64,${b64}`);
    audio.setAttribute('data-soren-chrome-audio-player', '1');
    audio.setAttribute('data-soren-chrome-audio-key', playerKey);
    audio.preload = 'auto';
    (document.body || document.documentElement).appendChild(audio);
    window.__sorenChromeAudioPlayers[playerKey] = audio;
    const cleanup = () => {
      if (window.__sorenChromeAudioPlayers?.[playerKey] === audio) {
        delete window.__sorenChromeAudioPlayers[playerKey];
      }
      try { audio.remove(); } catch {}
    };
    if (target && typeof audio.setSinkId === 'function') {
      await audio.setSinkId(target.deviceId);
    }

    await audio.play();
    await new Promise((resolve, reject) => {
      audio.onended = () => {
        cleanup();
        resolve();
      };
      audio.onerror = () => {
        cleanup();
        reject(new Error(audio.error?.message || 'audio element error'));
      };
    });
    return {
      ok: true,
      target: target?.label || '',
      sinkId: audio.sinkId || '',
      outputs: outputs.map(device => device.label).filter(Boolean),
    };
  }, { b64, label, playerKey });

  log(`played ${audioFile} target=${result.target || 'default'} sink=${result.sinkId || 'default'}`);
  process.exit(0);
} catch (error) {
  if (browser) {
    try {
      await stopTaggedAudio(browser.contexts()[0], playerKey);
    } catch {}
  }
  log(`ERROR ${(error && error.stack) || error}`);
  process.exit(stopOnly ? 0 : 1);
} finally {
  await browser?.close().catch(() => {});
}
