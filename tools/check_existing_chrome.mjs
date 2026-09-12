#!/usr/bin/env node
// One-off check: can Playwright drive the existing (non-managed) Google Chrome?
import { chromium } from 'playwright';

try {
  const browser = await chromium.launch({ channel: 'chrome', headless: false });
  const page = await browser.newPage();
  await page.goto('about:blank');
  const version = await browser.version();
  console.log(JSON.stringify({ ok: true, version }));
  await browser.close();
} catch (error) {
  console.log(JSON.stringify({ ok: false, error: String(error?.message || error) }));
  process.exitCode = 1;
}
