import { chromium } from 'playwright';
import fs from 'fs';

const URL = 'https://43469.play.unityroom.com/?expires=1770904529&salt=283988240671188267858410999828571457930&sig=a675c225bc360d4b377ca7adcb54610496442578';
const CMD_FILE = '/tmp/soviet_cmd.txt';
const RESULT_FILE = '/tmp/soviet_result.txt';
const SC_PATH = '/Users/azumag/work/sandbox/soren/sc.png';

async function createStableSession() {
  console.log('Launching browser...');

  const browser = await chromium.launch({
    headless: false,
    args: [
      '--no-sandbox',
      '--disable-setuid-sandbox',
      '--disable-dev-shm-usage',
      '--disable-web-security',
      '--disable-features=VizDisplayCompositor'
    ]
  });

  const context = await browser.newContext({
    viewport: { width: 1280, height: 720 },
    deviceScaleFactor: 1
  });

  const page = await context.newPage();

  // エラーハンドリング
  page.on('error', err => console.error('Page error:', err.message));
  page.on('pageerror', err => console.error('Page error:', err.message));

  console.log('Navigating to game...');
  try {
    await page.goto(URL, { waitUntil: 'domcontentloaded', timeout: 90000 });
  } catch (e) {
    console.error('Navigation error:', e.message);
    await browser.close();
    throw e;
  }

  console.log('Waiting for game to load...');
  await page.waitForTimeout(15000);

  // Canvas情報取得
  let canvasInfo = null;
  try {
    canvasInfo = await page.evaluate(() => {
      const canvas = document.querySelector('canvas');
      if (canvas) {
        const rect = canvas.getBoundingClientRect();
        return {
          width: canvas.width,
          height: canvas.height,
          rectX: rect.x,
          rectY: rect.y,
          rectWidth: rect.width,
          rectHeight: rect.height
        };
      }
      return null;
    });
  } catch (e) {
    console.error('Failed to get canvas info:', e.message);
  }

  if (!canvasInfo) {
    console.error('Canvas not found!');
    await browser.close();
    throw new Error('Canvas not found');
  }

  console.log('Canvas info:', JSON.stringify(canvasInfo));

  // ゲーム開始
  console.log('Starting game...');
  try {
    const centerX = canvasInfo.rectX + canvasInfo.width / 2;
    await page.mouse.click(centerX, canvasInfo.rectY + 200);
    await page.waitForTimeout(2000);
  } catch (e) {
    console.error('Failed to start game:', e.message);
  }

  // 初期スクリーンショット
  try {
    await page.screenshot({ path: SC_PATH });
    console.log('Initial screenshot saved');
  } catch (e) {
    console.error('Failed to take screenshot:', e.message);
  }

  // 準備完了を通知
  fs.writeFileSync(RESULT_FILE, JSON.stringify({
    success: true,
    status: 'ready',
    canvasInfo: canvasInfo
  }));

  console.log('Session ready. Waiting for commands...');

  let moveCount = 0;
  let running = true;

  // メインループ
  while (running) {
    try {
      if (fs.existsSync(CMD_FILE)) {
        const cmd = fs.readFileSync(CMD_FILE, 'utf-8').trim();
        try {
          fs.unlinkSync(CMD_FILE);
        } catch (e) {
          // Ignore unlink errors
        }

        if (cmd) {
          const parts = cmd.split(/\s+/);
          const action = parts[0].toLowerCase();

          if (action === 'drop') {
            const x = parseInt(parts[1]);
            const y = parseInt(parts[2]) || 150;

            console.log(`Executing: drop at (${x}, ${y})`);

            try {
              await page.mouse.move(x, y, { steps: 5 });
              await page.waitForTimeout(300);
              await page.mouse.click(x, y);
              await page.waitForTimeout(800);

              moveCount++;

              await page.screenshot({ path: SC_PATH });

              fs.writeFileSync(RESULT_FILE, JSON.stringify({
                success: true,
                move: moveCount,
                x: x,
                y: y
              }));

              console.log(`Move ${moveCount} completed`);
            } catch (e) {
              console.error('Drop error:', e.message);
              fs.writeFileSync(RESULT_FILE, JSON.stringify({
                success: false,
                error: e.message
              }));
            }

          } else if (action === 'screenshot') {
            try {
              await page.screenshot({ path: SC_PATH });
              fs.writeFileSync(RESULT_FILE, JSON.stringify({ success: true }));
              console.log('Screenshot taken');
            } catch (e) {
              console.error('Screenshot error:', e.message);
            }

          } else if (action === 'close') {
            console.log('Closing session...');
            running = false;
          }
        }
      }
    } catch (e) {
      console.error('Loop error:', e.message);
    }

    await new Promise(r => setTimeout(r, 100));
  }

  await browser.close();
  console.log('Session ended');
}

createStableSession().catch(err => {
  console.error('Fatal error:', err);
  process.exit(1);
});
