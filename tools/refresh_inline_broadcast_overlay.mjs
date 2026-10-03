#!/usr/bin/env node
// Refresh the already-open local Soren91 inline broadcast rails after a reviewed
// overlay deploy. This attaches over CDP, finds only a page that already owns
// the inline broadcast sidebar, replaces those srcdoc rails, then detaches.
// It never navigates the page, starts/stops a game, or changes stream state.

import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';
import { chromium } from 'playwright';
import {
  installInlineDirectBroadcastOverlay,
  loadDirectOverlayConfig,
} from '../lib/direct_overlay.mjs';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');

function cdpEndpoint() {
  const explicit = process.env.SOREN_CDP_URL || '';
  if (explicit) {
    if (!/^https?:\/\/(?:127[.]0[.]0[.]1|localhost):\d+$/.test(explicit)) {
      throw new Error('SOREN_CDP_URL must be loopback for inline rail refresh');
    }
    return explicit.replace('localhost', '127.0.0.1');
  }
  try {
    const parsed = JSON.parse(fs.readFileSync(path.join(ROOT, 'tmp', 'cdp_endpoint.json'), 'utf8'));
    if (typeof parsed?.url === 'string' && /^https?:\/\/(?:127[.]0[.]0[.]1|localhost):\d+$/.test(parsed.url)) {
      return parsed.url.replace('localhost', '127.0.0.1');
    }
  } catch {}
  const port = String(process.env.SOREN_CDP_PORT || '9222');
  if (!/^\d{2,5}$/.test(port)) throw new Error('invalid SOREN_CDP_PORT');
  return `http://127.0.0.1:${port}`;
}

async function findInlineRailPage(browser) {
  for (const context of browser.contexts()) {
    for (const page of context.pages()) {
      if (page.isClosed()) continue;
      try {
        const ownsRails = await page.evaluate(() =>
          Boolean(document.getElementById('soren-direct-stream-overlay-broadcastSidebar')));
        if (ownsRails) return page;
      } catch {}
    }
  }
  return null;
}

const config = loadDirectOverlayConfig(process.env, process.platform);
if (!config.enabled || !config.broadcast) {
  console.log(JSON.stringify({ status: 'skipped', reason: 'direct_broadcast_disabled' }));
  process.exit(0);
}

let browser;
try {
  browser = await chromium.connectOverCDP(cdpEndpoint(), { timeout: 5000 });
  const page = await findInlineRailPage(browser);
  if (!page) {
    console.log(JSON.stringify({ status: 'skipped', reason: 'inline_rails_not_active' }));
    process.exitCode = 0;
  } else {
    const refreshed = await installInlineDirectBroadcastOverlay(page, config, { watch: false });
    if (!refreshed) throw new Error('inline overlay refresh refused by config');
    await page.waitForFunction(() => {
      const frame = document.getElementById('soren-direct-stream-overlay-broadcastSidebar');
      return frame?.contentDocument?.querySelector('#broadcast-overlay')
        ?.dataset?.broadcastOverlayVersion === '4';
    }, undefined, { timeout: 3000 });
    console.log(JSON.stringify({ status: 'refreshed', version: 4 }));
  }
} finally {
  await browser?.close().catch(() => {});
}
