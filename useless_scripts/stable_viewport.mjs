import { chromium } from 'playwright';

const URL = 'https://43469.play.unityroom.com/?expires=1770904529&salt=283988240671188267858410999828571457930&sig=a675c225bc360d4b377ca7adcb54610496442578';

// 固定ビューポート - 300x150に設定
const FIXED_VIEWPORT = { width: 300, height: 150 };

async function playWithFixedViewport() {
  console.log('Launching browser with viewport:', FIXED_VIEWPORT);

  const browser = await chromium.launch({
    headless: false,
    args: ['--no-sandbox', '--disable-setuid-sandbox']
  });

  const context = await browser.newContext({
    viewport: FIXED_VIEWPORT,
    deviceScaleFactor: 1
  });

  const page = await context.newPage();

  page.on('pageerror', error => {
    console.log('Page error:', error.message);
  });

  console.log('Navigating to game...');
  await page.goto(URL, { waitUntil: 'domcontentloaded', timeout: 90000 });

  console.log('Waiting for Unity game to load...');
  await page.waitForTimeout(15000);

  // Canvas情報を取得
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
  await page.mouse.click(centerX, canvasInfo.rectY + 100);
  await page.waitForTimeout(2000);

  // 初期スクリーンショット
  await page.screenshot({ path: 'sc_start.png' });
  console.log('Initial screenshot: sc_start.png');
  console.log('=== GAME STARTED! ===');
  console.log('Viewport is 300x150, so x=50 is left, x=250 is right');

  // 1手実行: 左端
  console.log('\n>>> Move 1: Left edge (x=50)');
  await page.mouse.move(50, 50, { steps: 5 });
  await page.waitForTimeout(300);
  await page.mouse.click(50, 50);
  await page.waitForTimeout(800);
  await page.screenshot({ path: 'sc_01.png' });
  console.log('Screenshot: sc_01.png');

  // 2手実行: 右端
  console.log('\n>>> Move 2: Right edge (x=250)');
  await page.mouse.move(250, 50, { steps: 5 });
  await page.waitForTimeout(300);
  await page.mouse.click(250, 50);
  await page.waitForTimeout(800);
  await page.screenshot({ path: 'sc_02.png' });
  console.log('Screenshot: sc_02.png');

  // 3手実行: 左端で統合狙い
  console.log('\n>>> Move 3: Left edge for merge (x=50)');
  await page.mouse.move(50, 50, { steps: 5 });
  await page.waitForTimeout(300);
  await page.mouse.click(50, 50);
  await page.waitForTimeout(800);
  await page.screenshot({ path: 'sc_03.png' });
  console.log('Screenshot: sc_03.png');

  // 4手実行: 右端で統合狙い
  console.log('\n>>> Move 4: Right edge for merge (x=250)');
  await page.mouse.move(250, 50, { steps: 5 });
  await page.waitForTimeout(300);
  await page.mouse.click(250, 50);
  await page.waitForTimeout(800);
  await page.screenshot({ path: 'sc_04.png' });
  console.log('Screenshot: sc_04.png');

  // 5手実行: 中央
  console.log('\n>>> Move 5: Center (x=150)');
  await page.mouse.move(150, 50, { steps: 5 });
  await page.waitForTimeout(300);
  await page.mouse.click(150, 50);
  await page.waitForTimeout(800);
  await page.screenshot({ path: 'sc_05.png' });
  console.log('Screenshot: sc_05.png');

  console.log('\n=== 5 moves completed! ===');
  console.log('Browser will stay open for 60 seconds...');
  await page.waitForTimeout(60000);

  await browser.close();
  console.log('Done!');
}

playWithFixedViewport().catch(err => {
  console.error('Error:', err);
  process.exit(1);
});
