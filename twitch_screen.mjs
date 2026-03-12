#!/usr/bin/env node
// twitch_screen.mjs — Twitch配信スクリーンショットデーモン
// Playwrightで配信ページを開き、定期的にスクリーンショットを保存する
// 出力: tmp/twitch_stream.png (15秒ごと更新)

import { chromium } from 'playwright';
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const SCREENSHOT_PATH = path.join(__dirname, 'tmp', 'twitch_stream.png');
const PID_FILE = path.join(__dirname, 'tmp', '.twitch_screen.pid');
const TWITCH_URL = 'https://www.twitch.tv/azumagbanjo';
const INTERVAL_MS = 15000;

let browser = null;

async function cleanup() {
  console.log('[twitch_screen] Shutting down...');
  try { fs.unlinkSync(PID_FILE); } catch {}
  if (browser) {
    try { await browser.close(); } catch {}
  }
  process.exit(0);
}

process.on('SIGTERM', cleanup);
process.on('SIGINT', cleanup);

async function main() {
  // Ensure tmp directory exists
  fs.mkdirSync(path.join(__dirname, 'tmp'), { recursive: true });

  // Write PID file
  fs.writeFileSync(PID_FILE, String(process.pid));
  console.log(`[twitch_screen] PID=${process.pid}, output=${SCREENSHOT_PATH}`);

  // Try headless first, fallback to headed if video doesn't render
  let headless = true;
  browser = await chromium.launch({ headless });
  const context = await browser.newContext({
    viewport: { width: 1280, height: 720 },
    userAgent: 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
  });
  const page = await context.newPage();

  console.log(`[twitch_screen] Navigating to ${TWITCH_URL}`);
  try {
    await page.goto(TWITCH_URL, { waitUntil: 'domcontentloaded', timeout: 30000 });
  } catch (e) {
    console.error(`[twitch_screen] Navigation error: ${e.message}`);
  }

  // Handle mature content warning
  try {
    const matureButton = page.locator('button[data-a-target="content-classification-gate-overlay-start-watching-button"]');
    await matureButton.waitFor({ timeout: 5000 });
    await matureButton.click();
    console.log('[twitch_screen] Dismissed mature content warning');
  } catch {
    // No mature content warning, continue
  }

  // Wait for video player to appear
  try {
    await page.locator('video, .video-player__container, [data-a-target="video-player"]').first().waitFor({ timeout: 15000 });
    console.log('[twitch_screen] Video player detected');
  } catch {
    console.log('[twitch_screen] Video player not found, will screenshot page as-is');
  }

  // Wait a bit for content to settle
  await page.waitForTimeout(3000);

  console.log(`[twitch_screen] Starting screenshot loop (every ${INTERVAL_MS / 1000}s)`);

  // Screenshot loop
  while (true) {
    try {
      // Write to temp file first, then rename for atomicity
      const tmpPath = SCREENSHOT_PATH + '.tmp';
      await page.screenshot({ path: tmpPath, type: 'png' });
      fs.renameSync(tmpPath, SCREENSHOT_PATH);
    } catch (e) {
      console.error(`[twitch_screen] Screenshot error: ${e.message}`);
      // If page crashed, try to reload
      try {
        await page.reload({ timeout: 15000 });
      } catch {}
    }
    await page.waitForTimeout(INTERVAL_MS);
  }
}

main().catch(e => {
  console.error(`[twitch_screen] Fatal error: ${e.message}`);
  cleanup();
});
