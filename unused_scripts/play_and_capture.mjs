import { chromium } from 'playwright';

const URL = 'https://43469.play.unityroom.com/?expires=1770895465&salt=204822083100176348322172862835957129961&sig=9e18bdbb430a5b26db652e81c2c8f992f314ce7b';
const SC_PATH = '/Users/azumag/work/sandbox/soren/sc.png';

async function playGame() {
  const browser = await chromium.launch({
    headless: false,
    args: ['--disable-web-security', '--disable-features=IsolateOrigins,site-per-process']
  });

  const context = await browser.newContext({
    viewport: { width: 1280, height: 720 }
  });

  const page = await context.newPage();

  console.log('Loading game...');
  await page.goto(URL, { waitUntil: 'load', timeout: 60000 });
  await page.waitForTimeout(10000);

  // Canvas情報取得
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

  console.log('Canvas info:', canvasInfo);

  // ゲーム開始
  const centerX = canvasInfo.rectX + canvasInfo.width / 2;
  await page.mouse.click(centerX, canvasInfo.rectY + 200);
  await page.waitForTimeout(2000);

  // 初期スクリーンショット
  await page.screenshot({ path: SC_PATH });
  console.log('Initial screenshot saved to', SC_PATH);
  console.log('Game is ready. Browser will stay open.');
  console.log('Press Ctrl+C to close when done.');

  // ブラウザを開いたまま待機
  await new Promise(() => {});
}

playGame().catch(err => {
  console.error('Error:', err);
  process.exit(1);
});
