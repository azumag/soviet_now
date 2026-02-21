import { chromium } from 'playwright';

const URL = 'https://43469.play.unityroom.com/?expires=1770895465&salt=204822083100176348322172862835957129961&sig=9e18bdbb430a5b26db652e81c2c8f992f314ce7b';

// 操作する座標のリスト（一手ごとにスクリーンショットを撮る）
const MOVES = [
  { x: 320, comment: '左端に配置（基礎作り）' },
  { x: 400, comment: '少し右にずらす' },
  { x: 350, comment: '左寄りに配置' },
  { x: 450, comment: '中央よりに配置' },
  { x: 500, comment: '中央に配置' },
  { x: 380, comment: 'やや左に配置' },
  { x: 420, comment: '少し右に配置' },
  { x: 360, comment: '左寄りに配置' },
  { x: 440, comment: '右寄りに配置' },
  { x: 400, comment: '中央に配置' },
];

async function playMoves() {
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

  // ゲーム開始
  const centerX = canvasInfo.rectX + canvasInfo.width / 2;
  await page.mouse.click(centerX, canvasInfo.rectY + 200);
  await page.waitForTimeout(2000);

  // 初期スクリーンショット
  await page.screenshot({ path: 'sc_00_initial.png' });
  console.log('Initial screenshot: sc_00_initial.png');

  // 一手ずつ実行
  for (let i = 0; i < MOVES.length; i++) {
    const move = MOVES[i];
    const x = move.x;
    const y = canvasInfo.rectY + 150;

    console.log(`\n=== Move ${i + 1}/${MOVES.length} ===`);
    console.log(`Position: x=${x}, ${move.comment}`);

    await page.mouse.move(x, y, { steps: 5 });
    await page.waitForTimeout(300);
    await page.mouse.click(x, y);
    await page.waitForTimeout(800);

    // スクリーンショット保存
    const shotPath = `sc_${String(i + 1).padStart(2, '0')}_${move.comment.replace(/[^\w]/g, '_')}.png`;
    await page.screenshot({ path: shotPath });
    console.log(`Screenshot: ${shotPath}`);

    // ゲームオーバーチェック
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
      console.log('\n*** GAME OVER DETECTED ***');
      await page.screenshot({ path: 'sc_gameover.png' });
      break;
    }
  }

  // 最終スクリーンショット
  await page.screenshot({ path: 'sc_final.png' });
  console.log('\n=== Final screenshot: sc_final.png ===');
  console.log('Game will close in 10 seconds...');

  await page.waitForTimeout(10000);
  await browser.close();
  console.log('Done!');
}

playMoves().catch(err => {
  console.error('Error:', err);
  process.exit(1);
});
