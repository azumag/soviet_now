import { chromium } from 'playwright';

const URL = 'https://43469.play.unityroom.com/?expires=1770904529&salt=283988240671188267858410999828571457930&sig=a675c225bc360d4b377ca7adcb54610496442578';

async function manualPlay() {
  console.log('Launching browser...');

  const browser = await chromium.launch({
    headless: false,
    args: ['--no-sandbox', '--disable-setuid-sandbox']
  });

  const context = await browser.newContext({
    viewport: { width: 1280, height: 720 }
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

  console.log('Canvas info:', canvasInfo);

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
  await page.screenshot({ path: 'sc_start.png' });
  console.log('Initial screenshot: sc_start.png');
  console.log('READY - Game started!');

  // 一手実行関数
  window.makeMove = async (x, y) => {
    console.log(`\n>>> Dropping at (${x}, ${y})`);
    await page.mouse.move(x, y, { steps: 5 });
    await page.waitForTimeout(300);
    await page.mouse.click(x, y);
    await page.waitForTimeout(800);
    const moveNum = Math.floor(Math.random() * 10000);
    await page.screenshot({ path: `sc_${moveNum}.png` });
    console.log(`Screenshot saved: sc_${moveNum}.png`);
  };

  // 終了待機
  console.log('\nBrowser will stay open. Press Ctrl+C to close.');
  console.log('Use: await page.mouse.click(x, y) in DevTools console to make moves.');

  // 30分間待機
  await page.waitForTimeout(1800000);

  await browser.close();
  console.log('Browser closed');
}

manualPlay().catch(err => {
  console.error('Error:', err);
  process.exit(1);
});
