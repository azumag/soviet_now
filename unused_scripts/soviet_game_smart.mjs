import { chromium } from 'playwright';

const URL = 'https://43469.play.unityroom.com/?expires=1770895465&salt=204822083100176348322172862835957129961&sig=9e18bdbb430a5b26db652e81c2c8f992f314ce7b';

// 国旗の色を定義（近似RGB）
const FLAG_COLORS = {
  armenia: { r: 255, g: 0, b: 0, name: 'Armenia (Level 1)' },     // 赤
  estonia: { r: 0, g: 0, b: 255, name: 'Estonia (Level 2)' },     // 青
  latvia: { r: 128, g: 0, b: 128, name: 'Latvia (Level 3)' },    // 紫
  lithuania: { r: 255, g: 255, b: 0, name: 'Lithuania (Level 4)' }, // 黄
  georgia: { r: 255, g: 128, b: 0, name: 'Georgia (Level 5)' },  // オレンジ
  azerbaijan: { r: 128, g: 255, b: 0, name: 'Azerbaijan (Level 6)' }, // 黄緑
  tajik: { r: 0, g: 255, b: 0, name: 'Tajik (Level 7)' },        // 緑
  kyrgyz: { r: 0, g: 255, b: 255, name: 'Kyrgyz (Level 8)' },   // シアン
  belarus: { r: 0, g: 128, b: 255, name: 'Belarus (Level 9)' }, // 水色
  uzbek: { r: 255, g: 0, b: 255, name: 'Uzbek (Level 10)' },     // マゼンタ
  turkmen: { r: 128, g: 0, b: 255, name: 'Turkmen (Level 11)' }, // ピンク紫
  ukraine: { r: 255, g: 0, b: 128, name: 'Ukraine (Level 12)' }, // ローズ
  kazakhstan: { r: 255, g: 128, b: 128, name: 'Kazakhstan (Level 13)' }, // ピンク
  russia: { r: 200, g: 200, b: 200, name: 'Russia (Level 14)' },  // シルバー
  soviet: { r: 255, g: 215, b: 0, name: 'SOVIET!' }             // ゴールド
};

// 色から国旗を判定
function identifyFlagColor(r, g, b) {
  let minDistance = Infinity;
  let closestFlag = null;

  for (const [key, color] of Object.entries(FLAG_COLORS)) {
    const distance = Math.sqrt(
      Math.pow(r - color.r, 2) +
      Math.pow(g - color.g, 2) +
      Math.pow(b - color.b, 2)
    );
    if (distance < minDistance) {
      minDistance = distance;
      closestFlag = { key, ...color };
    }
  }

  // 黒い背景やグレーは無視
  if (r < 50 && g < 50 && b < 50) return null;

  return closestFlag;
}

// 画面から国旗の分布を分析
async function analyzeScreen(page, width, height) {
  const analysis = await page.evaluate((w, h) => {
    const canvas = document.querySelector('canvas');
    if (!canvas) return null;

    const ctx = canvas.getContext('2d');
    const sampleRate = 40; // サンプリング間隔
    const colorCounts = {};
    const positions = {};

    for (let y = h * 0.3; y < h * 0.9; y += sampleRate) {
      for (let x = w * 0.1; x < w * 0.9; x += sampleRate) {
        const pixel = ctx.getImageData(Math.floor(x), Math.floor(y), 1, 1).data;
        const r = pixel[0];
        const g = pixel[1];
        const b = pixel[2];

        // 背景色（黒/グレー）はスキップ
        if (r < 40 && g < 40 && b < 40) continue;
        if (Math.abs(r - g) < 10 && Math.abs(g - b) < 10 && r < 100) continue;

        // 色を分類（簡易版）
        let colorKey;
        if (r > 200 && g < 100 && b < 100) colorKey = 'red';
        else if (r < 100 && g < 100 && b > 200) colorKey = 'blue';
        else if (r > 100 && g < 50 && b > 100 && r > b) colorKey = 'magenta';
        else if (r > 200 && g > 200 && b < 100) colorKey = 'yellow';
        else if (r > 200 && g > 100 && g < 200 && b < 100) colorKey = 'orange';
        else if (r > 100 && g < 200 && b < 100 && g > r) colorKey = 'green';
        else if (r > 200 && b > 200 && g < 100) colorKey = 'pink';
        else if (r > 180 && g > 180 && b > 180) colorKey = 'silver';
        else if (r > 200 && g > 150 && b < 50) colorKey = 'gold';
        else colorKey = 'other';

        if (!colorCounts[colorKey]) colorCounts[colorKey] = 0;
        colorCounts[colorKey]++;

        if (!positions[colorKey]) positions[colorKey] = [];
        positions[colorKey].push({ x, y });
      }
    }

    return { colorCounts, positions };
  }, width, height);

  return analysis;
}

// 次のドロップ位置を決定（戦略的）
function decideDropPosition(analysis, canvasWidth, canvasHeight, round) {
  const padding = canvasWidth * 0.08;
  const playArea = canvasWidth - padding * 2;

  // 戦略パターン
  // 初期：均等に配置して基礎を作る
  // 中盤：同じ色の近くに落とす
  // 終盤：端に寄せて積み上げる

  let x;

  if (round < 15) {
    // 初期：左から順番に配置
    x = padding + (playArea * (round % 10)) / 10;
  } else if (round < 30) {
    // 中盤：交互に振る
    x = round % 2 === 0
      ? padding + playArea * 0.2
      : padding + playArea * 0.8;
  } else if (round < 50) {
    // 中盤後半：3分割
    const sections = [0.25, 0.5, 0.75];
    x = padding + playArea * sections[round % 3];
  } else {
    // 終盤：中央に集める
    const center = canvasWidth / 2;
    const spread = Math.sin(round * 0.3) * playArea * 0.25;
    x = center + spread;
  }

  return Math.floor(x);
}

// ゲームオーバーをチェック
async function checkGameOver(page) {
  return await page.evaluate(() => {
    // ゲームオーバー表示を探す
    const allElements = document.querySelectorAll('*');
    for (const el of allElements) {
      const text = el.textContent || '';
      if (text.includes('Retry') || text.includes('もう一度') ||
          text.includes('Game Over')) {
        return true;
      }
    }
    return false;
  });
}

async function playSovietGame() {
  const browser = await chromium.launch({
    headless: false,
    args: ['--start-maximized']
  });

  const context = await browser.newContext({
    viewport: { width: 1280, height: 720 }
  });

  const page = await context.newPage();

  console.log('=== ソ連ゲームを開始します ===');
  await page.goto(URL, { waitUntil: 'networkidle' });
  await page.waitForTimeout(6000);

  const canvasInfo = await page.evaluate(() => {
    const canvas = document.querySelector('canvas');
    if (canvas) {
      return { width: canvas.width, height: canvas.height };
    }
    return null;
  });

  if (!canvasInfo) {
    console.log('Canvas not found!');
    await browser.close();
    return;
  }

  console.log(`Canvas size: ${canvasInfo.width}x${canvasInfo.height}`);

  // ゲーム開始
  await page.mouse.click(canvasInfo.width / 2, 200);
  await page.waitForTimeout(1500);

  const maxRounds = 200;
  let gameOverCount = 0;

  for (let round = 0; round < maxRounds; round++) {
    // 画面分析（5回に1回）
    let analysis = null;
    if (round % 5 === 0) {
      analysis = await analyzeScreen(page, canvasInfo.width, canvasInfo.height);
      if (analysis) {
        console.log(`Round ${round + 1}: Colors on screen:`, Object.keys(analysis.colorCounts));
      }
    }

    const x = decideDropPosition(analysis, canvasInfo.width, canvasInfo.height, round);
    const y = canvasInfo.height * 0.25;

    console.log(`Round ${round + 1}/${maxRounds}: Dropping at x=${x}`);

    await page.mouse.move(x, y, { steps: 3 });
    await page.waitForTimeout(200);
    await page.mouse.click(x, y);

    // 落下待機
    await page.waitForTimeout(600);

    // 定期スクリーンショット
    if (round > 0 && round % 25 === 0) {
      await page.screenshot({ path: `soviet_round_${round}.png` });
      console.log(`Screenshot saved at round ${round}`);

      // ゲームオーバーチェック
      const isGameOver = await checkGameOver(page);
      if (isGameOver) {
        gameOverCount++;
        console.log(`Game Over detected! Count: ${gameOverCount}`);
        if (gameOverCount >= 3) {
          console.log('Game ended - stuck in game over state');
          break;
        }
      } else {
        gameOverCount = 0;
      }
    }
  }

  await page.screenshot({ path: 'soviet_final.png' });
  console.log('=== ゲーム終了 ===');
  console.log('Final screenshot saved to soviet_final.png');

  await page.waitForTimeout(15000);
  await browser.close();
}

playSovietGame().catch(console.error);
