import { chromium } from 'playwright';

const URL = 'https://43469.play.unityroom.com/?expires=1770904529&salt=283988240671188267858410999828571457930&sig=a675c225bc360d4b377ca7adcb54610496442578';

async function autoPlay() {
  const browser = await chromium.launch({
    headless: false
  });

  const context = await browser.newContext();
  const page = await context.newPage();

  console.log('Loading game...');
  await page.goto(URL, { waitUntil: 'networkidle' });
  await page.waitForTimeout(6000);

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

  console.log('Canvas:', canvasInfo);

  // ゲーム開始クリック
  const centerX = canvasInfo.rectX + canvasInfo.width / 2;
  await page.mouse.click(centerX, canvasInfo.rectY + 200);
  await page.waitForTimeout(1500);

  console.log('=== Game started! Auto-playing... ===');

  // 戦略的なドロップ位置
  const positions = [
    300,  // 左
    500,  // 中左
    700,  // 中
    900,  // 中右
    1100, // 右
  ];

  const maxMoves = 50;

  for (let i = 0; i < maxMoves; i++) {
    // 戦略：左右に振って、同じ旗を重ねるチャンスを増やす
    const x = positions[i % positions.length];
    const y = canvasInfo.rectY + 150;

    console.log(`Move ${i + 1}/${maxMoves}: Dropping at x=${x}`);

    await page.mouse.move(x, y, { steps: 5 });
    await page.waitForTimeout(200);
    await page.mouse.click(x, y);
    await page.waitForTimeout(700);

    // 定期的にスクリーンショット
    if (i > 0 && i % 10 === 0) {
      await page.screenshot({ path: `soviet_auto_${i}.png` });
      console.log(`Screenshot saved at move ${i}`);
    }

    // ゲームオーバーチェック（簡易）
    const isGameOver = await page.evaluate(() => {
      const allElements = document.querySelectorAll('*');
      for (const el of allElements) {
        const text = el.textContent || '';
        if (text.includes('Retry') || text.includes('もう一度') ||
            text.includes('Game Over') || text.includes('ゲームオーバー')) {
          return true;
        }
      }
      return false;
    });

    if (isGameOver) {
      console.log('Game Over detected!');
      break;
    }
  }

  // 最終スクリーンショット
  await page.screenshot({ path: 'soviet_auto_final.png' });
  console.log('=== Final screenshot saved to soviet_auto_final.png ===');

  console.log('Game will close in 10 seconds...');
  await page.waitForTimeout(10000);
  await browser.close();
}

autoPlay().catch(console.error);
