import { chromium } from 'playwright';
import fs from 'fs';

const URL = 'https://43469.play.unityroom.com/?expires=1770895465&salt=204822083100176348322172862835957129961&sig=9e18bdbb430a5b26db652e81c2c8f992f314ce7b';
const CMD_FILE = '/tmp/soviet_cmd.txt';
const RESULT_FILE = '/tmp/soviet_result.txt';
const SC_PATH = '/Users/azumag/work/sandbox/soren/sc.png';

async function stepByStepSession() {
  console.log('=== Starting Step-by-Step Session ===');
  console.log('Launching browser in visible mode...');

  const browser = await chromium.launch({
    headless: false,
    args: [
      '--no-first-run',
      '--no-default-browser-check',
      '--disable-blink-features=AutomationControlled',
      '--disable-web-security',
      '--disable-features=IsolateOrigins,site-per-process',
      '--disable-dev-shm-usage',
      '--disable-gpu',
    ]
  });

  const context = await browser.newContext({
    viewport: { width: 1280, height: 720 },
    userAgent: 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
  });

  const page = await context.newPage();

  page.on('pageerror', error => {
    console.log('Page error:', error.message);
  });

  console.log('Navigating to game...');
  await page.goto(URL, { waitUntil: 'domcontentloaded', timeout: 90000 });

  console.log('Waiting for Unity game to load...');
  await page.waitForTimeout(15000);

  const canvasInfo = await page.evaluate(() => {
    const canvas = document.querySelector('canvas');
    if (canvas) {
      const rect = canvas.getBoundingClientRect();
      return {
        width: canvas.width,
        height: canvas.height,
        rectX: rect.x,
        rectY: rect.y
      };
    }
    return null;
  });

  console.log('Canvas info:', JSON.stringify(canvasInfo));

  if (!canvasInfo) {
    console.error('Canvas not found!');
    await browser.close();
    return;
  }

  // ゲーム開始
  console.log('Starting game...');
  const centerX = canvasInfo.rectX + canvasInfo.width / 2;
  await page.mouse.click(centerX, canvasInfo.rectY + 200);
  await page.waitForTimeout(2000);

  // 初期スクリーンショット
  await page.screenshot({ path: SC_PATH });
  console.log('Initial screenshot: sc.png');

  // 準備完了を通知
  fs.writeFileSync(RESULT_FILE, JSON.stringify({
    success: true,
    status: 'ready',
    canvasInfo: canvasInfo
  }));

  console.log('=== Session Ready ===');
  console.log('Send commands via: echo "drop <x> <y>" > /tmp/soviet_cmd.txt');

  let moveCount = 0;
  let running = true;

  while (running) {
    try {
      if (fs.existsSync(CMD_FILE)) {
        const cmd = fs.readFileSync(CMD_FILE, 'utf-8').trim();
        try {
          fs.unlinkSync(CMD_FILE);
        } catch (e) {}

        if (cmd) {
          const parts = cmd.split(/\s+/);
          const action = parts[0].toLowerCase();

          if (action === 'drop') {
            const x = parseInt(parts[1]);
            const y = parseInt(parts[2]) || 150;

            console.log(`\n>>> Move ${moveCount + 1}: Dropping at (${x}, ${y})`);

            try {
              // pageが有効かチェック
              if (!page) {
                throw new Error('Page is null');
              }

              await page.mouse.move(x, y, { steps: 5 });
              await page.waitForTimeout(300);
              await page.mouse.click(x, y);
              await page.waitForTimeout(800);

              moveCount++;

              // スクリーンショット
              await page.screenshot({ path: SC_PATH });
              console.log(`Screenshot updated: sc.png`);

              fs.writeFileSync(RESULT_FILE, JSON.stringify({
                success: true,
                move: moveCount,
                x: x,
                y: y,
                timestamp: Date.now()
              }));

              console.log(`>>> Move ${moveCount} completed`);

            } catch (e) {
              console.error('Drop error:', e.message);
              fs.writeFileSync(RESULT_FILE, JSON.stringify({
                success: false,
                error: e.message
              }));
              // エラーが発生してもセッションを継続
              console.log('Continuing session...');
            }

          } else if (action === 'screenshot') {
            await page.screenshot({ path: SC_PATH });
            fs.writeFileSync(RESULT_FILE, JSON.stringify({ success: true }));
            console.log('Screenshot taken');

          } else if (action === 'close') {
            console.log('Closing session...');
            running = false;
          } else if (action === 'status') {
            fs.writeFileSync(RESULT_FILE, JSON.stringify({
              success: true,
              status: 'running',
              moveCount: moveCount,
              pageClosed: page.isClosed ? page.isClosed() : false
            }));
          }
        }
      }
    } catch (e) {
      console.error('Loop error:', e.message);
    }

    await new Promise(r => setTimeout(r, 100));
  }

  await browser.close();
  console.log('=== Session ended ===');
}

stepByStepSession().catch(err => {
  console.error('Fatal error:', err);
  process.exit(1);
});
