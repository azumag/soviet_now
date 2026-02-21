import { chromium } from 'playwright';

const URL = 'https://43469.play.unityroom.com/?expires=1770904529&salt=283988240671188267858410999828571457930&sig=a675c225bc360d4b377ca7adcb54610496442578';

async function playInVisibleMode() {
  console.log('Launching browser in visible mode...');

  const browser = await chromium.launch({
    headless: false,
    // 追加の起動オプションで安定性向上
    args: [
      '--no-first-run',
      '--no-default-browser-check',
      '--disable-blink-features=AutomationControlled',
      '--disable-web-security',
      '--disable-features=IsolateOrigins,site-per-process',
      '--disable-site-isolation-trials',
    ]
  });

  const context = await browser.newContext({
    viewport: { width: 1280, height: 720 },
    // UAを設定して検出を回避
    userAgent: 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
  });

  const page = await context.newPage();

  // ページエラーのログ
  page.on('pageerror', error => {
    console.log('Page error:', error.message);
  });

  console.log('Navigating to game...');
  await page.goto(URL, { waitUntil: 'domcontentloaded', timeout: 90000 });

  // 十分に待機してUnityゲームが読み込まれるのを待つ
  console.log('Waiting for Unity game to load...');
  await page.waitForTimeout(15000);

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
  await page.screenshot({ path: 'sc.png' });
  console.log('Initial screenshot: sc.png');

  // 操作を実行する関数 - 左右統合戦略
  const moves = [
    { x: 400, comment: '左側に配置' },
    { x: 400, comment: '左側に重ねて統合' },
    { x: 880, comment: '右側に配置' },
    { x: 880, comment: '右側に重ねて統合' },
    { x: 400, comment: '左側に配置' },
    { x: 880, comment: '右側に配置' },
    { x: 400, comment: '左側に重ねて統合' },
    { x: 880, comment: '右側に重ねて統合' },
    { x: 640, comment: '中央に配置' },
    { x: 640, comment: '中央に重ねて統合' },
  ];

  for (let i = 0; i < moves.length; i++) {
    const move = moves[i];
    const x = move.x;
    const y = canvasInfo.rectY + 150;

    console.log(`\n=== Move ${i + 1}/${moves.length} ===`);
    console.log(`Position: x=${x}, ${move.comment}`);

    try {
      await page.mouse.move(x, y, { steps: 5 });
      await page.waitForTimeout(300);
      await page.mouse.click(x, y);
      await page.waitForTimeout(800);

      // スクリーンショット保存
      const shotPath = `sc_${String(i + 1).padStart(2, '0')}.png`;
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
    } catch (e) {
      console.error('Error during move:', e.message);
      break;
    }
  }

  // 最終スクリーンショット
  await page.screenshot({ path: 'sc_final.png' });
  console.log('\n=== Final screenshot: sc_final.png ===');

  console.log('\nGame will stay open for 30 seconds. Press Ctrl+C to close early.');
  await page.waitForTimeout(30000);

  await browser.close();
  console.log('Browser closed');
}

playInVisibleMode().catch(err => {
  console.error('Error:', err);
  process.exit(1);
});
