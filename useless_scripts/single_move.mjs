import { chromium } from 'playwright';

const URL = 'https://43469.play.unityroom.com/?expires=1770904529&salt=283988240671188267858410999828571457930&sig=a675c225bc360d4b377ca7adcb54610496442578';

// コマンドライン引数から座標を取得
const x = parseInt(process.argv[2]) || 150;
const y = parseInt(process.argv[3]) || 75;
const outputFile = process.argv[4] || 'sc_single.png';

async function singleMove() {
  console.log(`Launching browser... Dropping at (${x}, ${y})`);

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

  // ドロップ実行
  console.log(`>>> Dropping at (${x}, ${y})`);
  await page.mouse.move(x, y, { steps: 5 });
  await page.waitForTimeout(300);
  await page.mouse.click(x, y);
  await page.waitForTimeout(800);

  // スクリーンショット
  await page.screenshot({ path: outputFile });
  console.log(`Screenshot saved: ${outputFile}`);

  console.log('\nBrowser will stay open for 10 seconds...');
  await page.waitForTimeout(10000);

  await browser.close();
  console.log('Done!');
}

singleMove().catch(err => {
  console.error('Error:', err);
  process.exit(1);
});
