// 監視用: 稼働中 bridge の Chromium に CDP 接続しゲーム画面を撮る。
// 既存ブラウザに connectOverCDP するだけ (起動・操作はしない)。
// 使い方: node screenshot_bridge.mjs [出力パス]
import { chromium } from 'playwright';
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const out = process.argv[2] || path.join(__dirname, 'tmp', 'monitor_shot.png');

let ep = 'http://localhost:9222';
try {
  const j = JSON.parse(fs.readFileSync(path.join(__dirname, 'tmp', 'cdp_endpoint.json'), 'utf-8'));
  if (j.url) ep = j.url;
} catch {}

const browser = await chromium.connectOverCDP(ep, { timeout: 8000 });
try {
  const ctx = browser.contexts()[0];
  if (!ctx) throw new Error('no context');
  const pages = ctx.pages();
  const page = pages.find(p => /localhost:8080/.test(p.url())) || pages[0];
  if (!page) throw new Error('no page');
  await page.screenshot({ path: out, timeout: 8000 });
  console.log(`shot: ${out} url=${page.url()}`);
} finally {
  await browser.close(); // CDP の connectOverCDP は detach のみ。bridge は閉じない
}
