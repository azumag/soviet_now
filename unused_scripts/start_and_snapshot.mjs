import { chromium } from 'playwright';

const URL = 'https://43469.play.unityroom.com/?expires=1770895465&salt=204822083100176348322172862835957129961&sig=9e18bdbb430a5b26db652e81c2c8f992f314ce7b';

async function main() {
  const browser = await chromium.launch({
    headless: false
  });

  const context = await browser.newContext();
  const page = await context.newPage();

  console.log('Navigating to game...');
  await page.goto(URL, { waitUntil: 'networkidle' });
  await page.waitForTimeout(6000);

  // Canvas情報取得
  const canvasInfo = await page.evaluate(() => {
    const canvas = document.querySelector('canvas');
    if (canvas) {
      const rect = canvas.getBoundingClientRect();
      return { width: canvas.width, height: canvas.height, rectX: rect.x, rectY: rect.y };
    }
    return null;
  });

  console.log('Canvas:', canvasInfo);

  // ゲーム開始クリック
  const centerX = canvasInfo.rectX + canvasInfo.width / 2;
  await page.mouse.click(centerX, canvasInfo.rectY + 200);
  await page.waitForTimeout(1500);

  // スクリーンショット
  await page.screenshot({ path: 'soviet_current.png' });
  console.log('Screenshot saved: soviet_current.png');

  // ブラウザを開いたまま待機
  console.log('Game ready. Browser is open.');
  console.log('Press Ctrl+C to exit when done.');

  // ブラウザを閉じずに待機
  await new Promise(() => {});
}

main().catch(console.error);
