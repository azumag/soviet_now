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

  // ゲーム開始クリック
  await page.mouse.click(640, 200);
  await page.waitForTimeout(1500);

  // スクリーンショット保存
  await page.screenshot({ path: 'soviet_current.png' });
  console.log('Screenshot saved to soviet_current.png');

  // ブラウザを開いたままにする
  console.log('Game is ready. Current screenshot taken.');
  console.log('Browser is open. Take another screenshot after making a move?');

  // 対話モード: 継続するか確認
  process.stdin.setRawMode(true);
  process.stdin.resume();
  process.stdin.setEncoding('utf8');

  process.stdin.on('data', async (key) => {
    if (key === 'q') {
      await browser.close();
      process.exit();
    } else if (key === 's') {
      await page.screenshot({ path: `soviet_${Date.now()}.png` });
      console.log('Screenshot saved');
    } else if (key === 'd') {
      // デフォルト位置でドロップ
      await page.mouse.move(640, 200);
      await page.waitForTimeout(200);
      await page.mouse.click(640, 200);
      await page.waitForTimeout(800);
      await page.screenshot({ path: `soviet_${Date.now()}.png` });
      console.log('Dropped at center and screenshot saved');
    }
  });
}

main().catch(console.error);
