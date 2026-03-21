/**
 * improve.mjs - AI改善ループ オーケストレーター
 *
 * ゲーム後に戦略を分析・改善する:
 * 1. ゲーム履歴からテキストサマリーを生成
 * 2. Claude API に現戦略 + サマリーを送信
 * 3. 返ってきた新戦略をバリデーション
 * 4. パスしたら strategy.mjs を更新
 */

import 'dotenv/config';
import { readFileSync, writeFileSync, existsSync, mkdirSync, readdirSync, unlinkSync } from 'fs';
import { join } from 'path';
import { execFile } from 'child_process';
import sharp from 'sharp';
import {
  archiveStrategySnapshotByHash,
  buildImproveReferenceContext,
  computeStrategyHashFromSource,
  ensureLineageInitialized,
  recordImprovementTransition,
} from './lineage.mjs';

const STRATEGY_PATH = 'strategy.mjs';
const VERSIONS_DIR = 'strategy_versions';
const PROMPT_PATH = 'prompts/improve_strategy.md';
const SCREENSHOTS_DIR = 'tmp/screenshots';

let improving = false; // 排他ロック

function normalizeRank(rank) {
  const value = Number.parseInt(String(rank), 10);
  return Number.isInteger(value) && value >= 1 && value <= 91 ? value : null;
}

function readSummaryMeta(summaryPath) {
  if (!existsSync(summaryPath)) return {};
  try {
    const summary = JSON.parse(readFileSync(summaryPath, 'utf-8'));
    return {
      turns: Number(summary.turns || 0),
      rank: normalizeRank(summary.rank),
    };
  } catch {
    return {};
  }
}

function formatRankLabel(rank) {
  return rank == null ? 'unknown' : `#${rank}`;
}

function isBetterGameSummary(candidate, currentBest) {
  if (!candidate) return false;
  if (!currentBest) return true;
  const candidateRank = normalizeRank(candidate.rank);
  const bestRank = normalizeRank(currentBest.rank);
  if (candidateRank != null && bestRank != null) {
    if (candidateRank !== bestRank) return candidateRank < bestRank;
  } else if (candidateRank != null) {
    return true;
  } else if (bestRank != null) {
    return false;
  }
  const candidateTurns = Number(candidate.turns || 0);
  const bestTurns = Number(currentBest.turns || 0);
  if (candidateTurns !== bestTurns) return candidateTurns > bestTurns;
  const candidatePieces = Number(candidate.piecesAtEnd || 0);
  const bestPieces = Number(currentBest.piecesAtEnd || 0);
  return candidatePieces < bestPieces;
}

function formatRankToken(rank) {
  return rank == null ? 'na' : String(rank).padStart(2, '0');
}

/**
 * ゲーム後の改善フローを実行
 */
export async function runImprovement(gameNumber, historyPath, summaryPath) {
  if (improving) {
    console.log(`[improve] Skipping game #${gameNumber} (previous improvement still running)`);
    cleanupScreenshots();
    return;
  }
  improving = true;
  try {
    await _runImprovement(gameNumber, historyPath, summaryPath);
  } finally {
    improving = false;
  }
}

async function _runImprovement(gameNumber, historyPath, summaryPath) {
  console.log(`[improve] Starting improvement for game #${gameNumber}`);

  // 1. サマリー生成
  const gameSummary = generateSummary(historyPath, summaryPath);
  console.log('[improve] Summary generated');

  // 2. 現在の戦略を読み込み
  const currentStrategy = readFileSync(STRATEGY_PATH, 'utf-8');
  const currentStrategyHash = computeStrategyHashFromSource(currentStrategy);
  archiveStrategySnapshotByHash(STRATEGY_PATH, currentStrategyHash);
  ensureLineageInitialized();
  const lineageContext = buildImproveReferenceContext(currentStrategyHash);

  // 3. AI呼び出し (claude CLI)
  const promptText = buildPromptText(gameSummary, currentStrategy, lineageContext);
  console.log('[improve] Calling claude CLI...');

  let newStrategy;
  try {
    newStrategy = await callClaude(promptText);
  } catch (err) {
    console.error('[improve] API call failed:', err.message);
    cleanupScreenshots();
    return;
  }

  if (!newStrategy) {
    console.log('[improve] No strategy improvement returned');
    cleanupScreenshots();
    return;
  }

  // 4. バリデーション
  const isValid = await validateStrategy(newStrategy);
  if (!isValid) {
    console.log('[improve] New strategy failed validation, keeping current');
    cleanupScreenshots();
    return;
  }

  // 5. 現戦略をバックアップ + 新戦略を適用
  const summaryMeta = readSummaryMeta(summaryPath);
  const versionName = `v${gameNumber}_turns${String(summaryMeta.turns || 0).padStart(3, '0')}_rank${formatRankToken(summaryMeta.rank)}_strategy.mjs`;
  const backupPath = join(VERSIONS_DIR, versionName);
  writeFileSync(backupPath, currentStrategy);
  console.log(`[improve] Backed up current strategy to ${backupPath}`);

  writeFileSync(STRATEGY_PATH, newStrategy);
  const newStrategyHash = computeStrategyHashFromSource(newStrategy);
  archiveStrategySnapshotByHash(STRATEGY_PATH, newStrategyHash);
  recordImprovementTransition({
    fromHash: currentStrategyHash,
    toHash: newStrategyHash,
    note: `single-game improve #${gameNumber}`,
    gameStart: gameNumber,
    gameEnd: gameNumber,
    bestGame: gameNumber,
  });
  console.log('[improve] New strategy applied!');

  // 6. スクリーンショット削除
  cleanupScreenshots();
  console.log('[improve] Improvement complete');
}

/**
 * ゲーム履歴からテキストサマリーを生成
 */
function generateSummary(historyPath, summaryPath) {
  const lines = [];

  // ゲームサマリー
  if (existsSync(summaryPath)) {
    const summary = JSON.parse(readFileSync(summaryPath, 'utf-8'));
    lines.push(`## Game Summary`);
    lines.push(`- Game #${summary.gameNumber}`);
    lines.push(`- Turns: ${summary.turns}`);
    lines.push(`- Rank: ${summary.rank == null ? 'unknown' : summary.rank}`);
    lines.push(`- Pieces at end: ${summary.piecesAtEnd}`);
    if (summary.resultScreenOcr?.lines?.length) {
      lines.push(`- Result screen OCR: ${summary.resultScreenOcr.lines.join(' / ')}`);
    }
    lines.push('');
  }

  // ターン履歴の分析
  if (existsSync(historyPath)) {
    const historyLines = readFileSync(historyPath, 'utf-8').trim().split('\n');
    const turns = historyLines.map(l => {
      try { return JSON.parse(l); } catch { return null; }
    }).filter(Boolean);

    if (turns.length > 0) {
      lines.push(`## Turn History (${turns.length} turns)`);

      // ドロップ位置分布
      const xs = turns.map(t => t.decision?.x || 0);
      const avgX = xs.reduce((a, b) => a + b, 0) / xs.length;
      lines.push(`- Avg drop X: ${avgX.toFixed(2)}`);
      lines.push(`- Drop X range: [${Math.min(...xs).toFixed(2)}, ${Math.max(...xs).toFixed(2)}]`);

      // 理由の分布
      const reasons = {};
      turns.forEach(t => {
        const reason = (t.decision?.reason || 'UNKNOWN').split('_')[0];
        reasons[reason] = (reasons[reason] || 0) + 1;
      });
      lines.push(`- Decision reasons: ${JSON.stringify(reasons)}`);

      // ピース数推移
      const pieceCounts = turns.map(t => t.state?.pieces?.length || 0);
      lines.push(`- Piece count range: [${Math.min(...pieceCounts)}, ${Math.max(...pieceCounts)}]`);

      // 最後の10ターンの詳細
      lines.push('');
      lines.push('## Last 10 Turns');
      const lastTurns = turns.slice(-10);
      lastTurns.forEach(t => {
        const pieces = t.state?.pieces?.length || 0;
        const x = t.decision?.x?.toFixed(2) || '?';
        const reason = t.decision?.reason || 'UNKNOWN';
        lines.push(`- Turn ${t.turn}: pieces=${pieces}, drop_x=${x}, reason=${reason}`);
      });
    }
  }

  return lines.join('\n');
}

/**
 * AI改善プロンプトのテキスト部分を構築
 */
function buildPromptText(gameSummary, currentStrategy, lineageContext = '') {
  let promptTemplate = '';
  if (existsSync(PROMPT_PATH)) {
    promptTemplate = readFileSync(PROMPT_PATH, 'utf-8');
  }

  return `${promptTemplate}

## Game Analysis
${gameSummary}

## Current Strategy Code
\`\`\`javascript
${currentStrategy}
\`\`\`

${lineageContext ? `${lineageContext}\n` : ''}\

## Instructions
Based on the game analysis and screenshots above, improve the strategy.mjs code.
The function signature must remain: export function decide(boardState) -> { x: number, reason: string, hold?: boolean }
where boardState has: { pieces: [{type, x, y, r}], next: {type, r}, nextPieces: [{type, r}, ...] (up to 3), hold: {type, r}|null, canHold: boolean, score: number, confidence: number, garbage: {ratio, height, pixelCount} }

HOLD mechanic: right-click saves current piece to HOLD slot, or swaps with held piece.
- boardState.hold: currently held piece (null if empty)
- boardState.canHold: true if hold is available this turn (resets after each drop)
- Return hold: true to use HOLD (x is ignored, bot will re-analyze after swap)
- HOLD logic MUST be preserved in any improvement.

Prefer changes that improve stable survival turns against the current anchor.
Use rank as a strong hint only when it was actually detected; do not overfit to sparse rank samples.

Return ONLY the complete improved strategy.mjs code, enclosed in a single code block.
Focus on practical improvements based on the observed game behavior.`;
}

/**
 * ゲーム中のスクリーンショットから代表的なものを選んでbase64エンコード
 * 最大5枚: 序盤、中盤、終盤 + ゲームオーバー付近
 */
async function selectScreenshots() {
  if (!existsSync(SCREENSHOTS_DIR)) return [];
  const files = readdirSync(SCREENSHOTS_DIR)
    .filter(f => f.startsWith('turn_') && f.endsWith('.png'))
    .sort();

  if (files.length === 0) return [];

  // 均等に最大5枚選択
  const maxShots = 5;
  const step = Math.max(1, Math.floor(files.length / maxShots));
  const selected = [];
  for (let i = 0; i < files.length && selected.length < maxShots; i += step) {
    selected.push(files[i]);
  }
  // 最後のスクリーンショットも必ず含める
  if (selected[selected.length - 1] !== files[files.length - 1]) {
    selected.push(files[files.length - 1]);
  }

  const results = [];
  for (const f of selected) {
    try {
      const buf = await sharp(join(SCREENSHOTS_DIR, f))
        .resize(640, 360)
        .jpeg({ quality: 70 })
        .toBuffer();
      results.push({
        filename: f,
        base64: buf.toString('base64'),
        mediaType: 'image/jpeg',
      });
    } catch (e) {
      // skip broken files
    }
  }
  return results;
}

/**
 * claude CLI を呼び出してテキスト応答を取得 (非同期)
 */
function callClaude(promptText) {
  const promptFile = 'tmp/improve_prompt.txt';
  writeFileSync(promptFile, promptText);

  return new Promise((resolve, reject) => {
    const child = execFile('claude', ['-p', '--model', 'sonnet', '--output-format', 'text'], {
      encoding: 'utf-8',
      maxBuffer: 2 * 1024 * 1024,
    }, (err, stdout, stderr) => {
      if (err) {
        if (stderr) console.error('[improve] claude stderr:', stderr.slice(0, 500));
        return reject(err);
      }
      const text = stdout.trim();

      // コードブロックを抽出
      const codeMatch = text.match(/```(?:javascript|js)?\n([\s\S]*?)```/);
      if (codeMatch) return resolve(codeMatch[1].trim());

      // コードブロックがない場合、全文をコードとして扱う
      if (text.includes('export function decide')) return resolve(text.trim());

      resolve(null);
    });

    // stdinでプロンプトを送信
    child.stdin.write(promptText);
    child.stdin.end();
  });
}

/**
 * 新しい戦略コードをバリデーション
 */
async function validateStrategy(code) {
  // 1. decide関数が存在するか
  if (!code.includes('export function decide')) {
    console.log('[improve] Validation failed: no decide() function');
    return false;
  }

  // 2. 構文チェック: 一時ファイルに書き出して dynamic import
  const tmpPath = 'tmp/strategy_candidate.mjs';
  writeFileSync(tmpPath, code);

  try {
    // dynamic import で構文チェック
    const candidateUrl = new URL(tmpPath, `file://${process.cwd()}/`).href;
    const module = await import(candidateUrl + `?t=${Date.now()}`);

    // 3. decide関数が export されているか
    if (typeof module.decide !== 'function') {
      console.log('[improve] Validation failed: decide is not a function');
      return false;
    }

    // 4. スモークテスト: ダミー入力で実行
    const dummyState = {
      pieces: [
        { type: 1, x: 0, y: -4, r: 0.207 },
        { type: 2, x: 1, y: -4, r: 0.259 },
      ],
      next: { type: 1, r: 0.207 },
      nextPieces: [{ type: 1, r: 0.207 }, { type: 3, r: 0.316 }, { type: 2, r: 0.259 }],
      hold: { type: 2, r: 0.259 },
      canHold: true,
      score: 100,
      confidence: 0.5,
      garbage: { ratio: 0, height: -5, pixelCount: 0 },
    };

    const result = module.decide(dummyState);

    // 戻り値の形式チェック
    if (typeof result !== 'object' || typeof result.x !== 'number' || typeof result.reason !== 'string') {
      console.log('[improve] Validation failed: invalid return format', result);
      return false;
    }

    // xが範囲内か
    if (result.x < -3.0 || result.x > 3.0) {
      console.log('[improve] Validation failed: x out of range', result.x);
      return false;
    }

    console.log('[improve] Validation passed:', JSON.stringify(result));
    return true;

  } catch (err) {
    console.log('[improve] Validation failed:', err.message);
    return false;
  } finally {
    // 一時ファイル削除
    try { unlinkSync(tmpPath); } catch {}
  }
}

/**
 * スコアをサマリーから抽出
 */
function extractScore(summaryPath) {
  try {
    const summary = JSON.parse(readFileSync(summaryPath, 'utf-8'));
    return summary.score || 0;
  } catch {
    return 0;
  }
}

/**
 * スクリーンショット削除 (容量削減)
 */
function cleanupScreenshots() {
  if (!existsSync(SCREENSHOTS_DIR)) return;
  const files = readdirSync(SCREENSHOTS_DIR);
  let deleted = 0;
  for (const f of files) {
    try {
      unlinkSync(join(SCREENSHOTS_DIR, f));
      deleted++;
    } catch {}
  }
  if (deleted > 0) {
    console.log(`[improve] Cleaned up ${deleted} screenshots`);
  }
}

/**
 * 複数ゲームの集約サマリーを生成
 */
function generateAggregateSummary(startGame, endGame) {
  const summariesDir = 'tmp/summaries';
  const lines = [];
  const games = [];

  for (let i = startGame + 1; i <= endGame; i++) {
    const summaryPath = join(summariesDir, `game_${String(i).padStart(4, '0')}.json`);
    if (!existsSync(summaryPath)) continue;
    try {
      const summary = JSON.parse(readFileSync(summaryPath, 'utf-8'));
      games.push(summary);
    } catch {}
  }

  if (games.length === 0) {
    return '## Aggregate Summary\nNo game data available.';
  }

  const turns = games.map(g => g.turns || 0);
  const ranks = games.map(g => g.rank).filter(r => r != null && r > 0);

  lines.push(`## Aggregate Summary (${games.length} games, #${startGame + 1}-#${endGame})`);
  lines.push(`- Total games: ${games.length}`);
  lines.push(`- Rank coverage: ${ranks.length}/${games.length}`);
  if (ranks.length > 0) {
    lines.push(`- Best rank: ${Math.min(...ranks)}`);
    lines.push(`- Avg rank: ${(ranks.reduce((a, b) => a + b, 0) / ranks.length).toFixed(1)}`);
    lines.push(`- Rank range: [${Math.min(...ranks)}, ${Math.max(...ranks)}]`);
  }
  lines.push(`- Turn range: [${Math.min(...turns)}, ${Math.max(...turns)}]`);
  lines.push(`- Avg turns: ${(turns.reduce((a, b) => a + b, 0) / turns.length).toFixed(1)}`);

  // ゲーム一覧
  lines.push('');
  lines.push('## Per-Game Details');
  for (const g of games) {
    const resultMemo = g.resultScreenOcr?.lines?.length ? `, result="${g.resultScreenOcr.lines.join(' / ')}"` : '';
    lines.push(`- Game #${g.gameNumber}: rank=${g.rank ?? '?'}, turns=${g.turns}, pieces=${g.piecesAtEnd ?? '?'}${resultMemo}`);
  }

  return lines.join('\n');
}

/**
 * standaloneモード: 指定範囲のゲームデータから最良ゲームを選んで改善
 */
async function runStandaloneImprovement(startGame, endGame) {
  console.log(`[improve] Standalone improvement for games ${startGame}-${endGame}`);

  const summariesDir = 'tmp/summaries';
  const historyDir = 'game_history';
  let bestGame = null;
  let bestSummary = null;

  // サマリーを走査し最良rankを優先、同率ならturnsでベストゲームを特定
  for (let i = startGame + 1; i <= endGame; i++) {
    const summaryPath = join(summariesDir, `game_${String(i).padStart(4, '0')}.json`);
    if (!existsSync(summaryPath)) continue;
    try {
      const summary = JSON.parse(readFileSync(summaryPath, 'utf-8'));
      if (isBetterGameSummary(summary, bestSummary)) {
        bestSummary = summary;
        bestGame = i;
      }
    } catch {}
  }

  if (bestGame === null) {
    console.log('[improve] No valid game summaries found, skipping');
    return;
  }

  const bestHistoryPath = join(historyDir, `game_${String(bestGame).padStart(4, '0')}.jsonl`);
  const bestSummaryPath = join(summariesDir, `game_${String(bestGame).padStart(4, '0')}.json`);

  if (!existsSync(bestHistoryPath)) {
    console.log(`[improve] Best game history not found: ${bestHistoryPath}`);
    return;
  }

  console.log(`[improve] Best game: #${bestGame} (rank=${formatRankLabel(bestSummary?.rank)}, turns=${bestSummary?.turns ?? 0})`);

  // 集約サマリー生成
  const aggregateSummary = generateAggregateSummary(startGame, endGame);

  // 現在の戦略を読み込み
  const currentStrategy = readFileSync(STRATEGY_PATH, 'utf-8');
  const currentStrategyHash = computeStrategyHashFromSource(currentStrategy);
  archiveStrategySnapshotByHash(STRATEGY_PATH, currentStrategyHash);
  ensureLineageInitialized();
  const lineageContext = buildImproveReferenceContext(currentStrategyHash);

  // 個別ゲームのサマリーも生成
  const gameSummary = generateSummary(bestHistoryPath, bestSummaryPath);

  // プロンプト構築 (集約 + ベストゲーム詳細)
  const combinedSummary = `${aggregateSummary}\n\n## Best Game Details\n${gameSummary}`;
  const promptText = buildPromptText(combinedSummary, currentStrategy, lineageContext);

  console.log('[improve] Calling claude CLI (standalone)...');
  let newStrategy;
  try {
    newStrategy = await callClaude(promptText);
  } catch (err) {
    console.error('[improve] API call failed:', err.message);
    return;
  }

  if (!newStrategy) {
    console.log('[improve] No strategy improvement returned');
    return;
  }

  const isValid = await validateStrategy(newStrategy);
  if (!isValid) {
    console.log('[improve] New strategy failed validation, keeping current');
    return;
  }

  // バックアップ + 適用
  const bestSummaryMeta = readSummaryMeta(bestSummaryPath);
  const versionName = `v${endGame}_aggregate_turns${String(bestSummaryMeta.turns || 0).padStart(3, '0')}_rank${formatRankToken(bestSummaryMeta.rank)}_strategy.mjs`;
  const backupPath = join(VERSIONS_DIR, versionName);
  writeFileSync(backupPath, currentStrategy);
  console.log(`[improve] Backed up current strategy to ${backupPath}`);

  writeFileSync(STRATEGY_PATH, newStrategy);
  const newStrategyHash = computeStrategyHashFromSource(newStrategy);
  archiveStrategySnapshotByHash(STRATEGY_PATH, newStrategyHash);
  recordImprovementTransition({
    fromHash: currentStrategyHash,
    toHash: newStrategyHash,
    note: `standalone improve ${startGame + 1}-${endGame}`,
    gameStart: startGame + 1,
    gameEnd: endGame,
    bestGame,
  });
  console.log('[improve] New strategy applied (standalone)!');
  cleanupScreenshots();
}

// CLI: node improve.mjs --standalone <startGame> <endGame>
const args = process.argv.slice(2);
if (args[0] === '--standalone') {
  const start = parseInt(args[1], 10);
  const end = parseInt(args[2], 10);
  if (isNaN(start) || isNaN(end)) {
    console.error('Usage: node improve.mjs --standalone <startGame> <endGame>');
    process.exit(1);
  }
  runStandaloneImprovement(start, end)
    .then(() => process.exit(0))
    .catch(e => { console.error(e); process.exit(1); });
}
