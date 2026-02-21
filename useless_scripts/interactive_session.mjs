import { chromium } from 'playwright';
import fs from 'fs';

const URL = 'https://43469.play.unityroom.com/?expires=1770904529&salt=283988240671188267858410999828571457930&sig=a675c225bc360d4b377ca7adcb54610496442578';
const CMD_FILE = '/tmp/soviet_cmd.txt';
const RESULT_FILE = '/tmp/soviet_result.txt';
const SC_PATH = '/Users/azumag/work/sandbox/soren/sc.png';

async function interactiveSession() {
  const browser = await chromium.launch({
    headless: false,
    args: ['--disable-web-security']
  });

  const context = await browser.newContext({
    viewport: { width: 1280, height: 720 }
  });

  const page = await context.newPage();

  console.log('Loading game...');
  await page.goto(URL, { waitUntil: 'load', timeout: 60000 });
  await page.waitForTimeout(10000);

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

  console.log('Canvas:', JSON.stringify(canvasInfo));

  // ゲーム開始
  const centerX = canvasInfo.rectX + canvasInfo.width / 2;
  await page.mouse.click(centerX, canvasInfo.rectY + 200);
  await page.waitForTimeout(2000);

  // 初期スクリーンショット
  await page.screenshot({ path: SC_PATH });
  console.log('READY');
  fs.writeFileSync(RESULT_FILE, JSON.stringify({ success: true, status: 'ready' }));

  let moveCount = 0;
  let running = true;

  while (running) {
    try {
      if (fs.existsSync(CMD_FILE)) {
        const cmd = fs.readFileSync(CMD_FILE, 'utf-8').trim();
        fs.unlinkSync(CMD_FILE);

        if (cmd) {
          const parts = cmd.split(/\s+/);
          const action = parts[0].toLowerCase();

          if (action === 'drop') {
            const x = parseInt(parts[1]);
            const y = parseInt(parts[2]) || canvasInfo.rectY + 150;

            console.log(`Dropping at (${x}, ${y})`);
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
            console.log(`Move ${moveCount} done`);
          } else if (action === 'screenshot') {
            await page.screenshot({ path: SC_PATH });
            fs.writeFileSync(RESULT_FILE, JSON.stringify({ success: true }));
          } else if (action === 'close') {
            running = false;
          }
        }
      }
    } catch (e) {
      console.error('Error:', e.message);
      fs.writeFileSync(RESULT_FILE, JSON.stringify({ success: false, error: e.message }));
    }

    await new Promise(r => setTimeout(r, 100));
  }

  await browser.close();
  console.log('Session ended');
}

interactiveSession().catch(err => {
  console.error('Fatal error:', err);
  process.exit(1);
});
