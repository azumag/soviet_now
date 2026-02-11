import { chromium } from 'playwright';

const URL = 'https://43469.play.unityroom.com/?expires=1770904529&salt=283988240671188267858410999828571457930&sig=a675c225bc360d4b377ca7adcb54610496442578';

// コマンドライン引数から複数のドロップ座標を取得
const moves = [];
for (let i = 2; i < process.argv.length; i += 2) {
  const x = parseInt(process.argv[i]);
  const y = parseInt(process.argv[i + 1]) || 75;
  if (!isNaN(x)) {
    moves.push({ x, y, outputFile: `sc_${moves.length + 1}.png` });
  }
}

if (moves.length === 0) {
  console.log('Usage: node continuous_manual.mjs <x1> <y1> <x2> <y2> ...');
  console.log('Example: node continuous_manual.mjs 100 75 200 75 100 75');
  process.exit(1);
}

async function continuousPlay() {
  console.log(`Launching browser... Will execute ${moves.length} moves`);

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
  await page.screenshot({ path: 'sc_initial.png' });
  console.log('Initial screenshot: sc_initial.png');

  // 連続ドロップ実行
  for (let i = 0; i < moves.length; i++) {
    const move = moves[i];
    const moveNum = i + 1;

    console.log(`\n>>> Move ${moveNum}/${moves.length}: Dropping at (${move.x}, ${move.y})`);

    await page.mouse.move(move.x, move.y, { steps: 5 });
    await page.waitForTimeout(300);
    await page.mouse.click(move.x, move.y);
    await page.waitForTimeout(800);

    // スクリーンショット
    await page.screenshot({ path: move.outputFile });
    console.log(`Screenshot saved: ${move.outputFile}`);
  }

  console.log('\n=== All moves completed! ===');
  console.log('Browser will stay open for 30 seconds...');
  await page.waitForTimeout(30000);

  await browser.close();
  console.log('Done!');
}

continuousPlay().catch(err => {
  console.error('Error:', err);
  process.exit(1);
});
