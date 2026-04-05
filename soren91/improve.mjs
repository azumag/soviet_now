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
const VIEWER_ADVICE_PATH = resolveAdvicePath(process.env.SOREN91_STRATEGY_ADVICE_FILE, '../advice91.md');
const SCREENSHOTS_DIR = 'tmp/screenshots';
const SUMMARIES_DIR = 'tmp/summaries';
const SUMMARIES_KEEP_COUNT = 13;
const IMPROVE_CLAUDE_MODEL = process.env.SOREN91_IMPROVE_CLAUDE_MODEL || 'sonnet';
const IMPROVE_GEMINI_MODEL = process.env.SOREN91_IMPROVE_GEMINI_MODEL || process.env.SOREN91_GEMINI_FALLBACK_MODEL || 'gemini-2.5-flash';
const IMPROVE_GEMINI_TIMEOUT_MS = Math.max(
  1000,
  Number.parseInt(process.env.SOREN91_IMPROVE_GEMINI_TIMEOUT || '180', 10) * 1000,
);
const IMPROVE_PROMPT_WARN_CHARS = Number.parseInt(process.env.SOREN91_IMPROVE_PROMPT_WARN_CHARS || '120000', 10);
const IMPROVE_IMAGE_WARN_CHARS = Number.parseInt(process.env.SOREN91_IMPROVE_IMAGE_WARN_CHARS || '1500000', 10);

let improving = false; // 排他ロック

function resolveAdvicePath(envPath, defaultPath) {
  if (!envPath) return defaultPath;
  if (existsSync(envPath)) return envPath;
  if (!envPath.startsWith('..')) {
    const parentPath = join('..', envPath);
    if (existsSync(parentPath)) return parentPath;
  }
  return envPath;
}

function normalizeRank(rank) {
  const value = Number.parseInt(String(rank), 10);
  return Number.isInteger(value) && value >= 1 && value <= 91 ? value : null;
}

function readViewerAdvice(path, maxLines = 60) {
  if (!path || !existsSync(path)) return '';
  try {
    return readFileSync(path, 'utf-8')
      .split(/\r?\n/)
      .map(line => line.trimEnd())
      .filter(line => line.trim())
      .slice(-maxLines)
      .join('\n');
  } catch {
    return '';
  }
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

function isWorseGameSummary(candidate, currentWorst) {
  if (!candidate) return false;
  if (!currentWorst) return true;
  const candidateRank = normalizeRank(candidate.rank);
  const worstRank = normalizeRank(currentWorst.rank);
  if (candidateRank != null && worstRank != null) {
    if (candidateRank !== worstRank) return candidateRank > worstRank;
  } else if (candidateRank != null) {
    return true;
  } else if (worstRank != null) {
    return false;
  }
  const candidateTurns = Number(candidate.turns || 0);
  const worstTurns = Number(currentWorst.turns || 0);
  if (candidateTurns !== worstTurns) return candidateTurns < worstTurns;
  const candidatePieces = Number(candidate.piecesAtEnd || 0);
  const worstPieces = Number(currentWorst.piecesAtEnd || 0);
  return candidatePieces > worstPieces;
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
  const viewerAdvice = readViewerAdvice(VIEWER_ADVICE_PATH, 80);

  // 3. スクリーンショット収集 + AI呼び出し
  const screenshots = await selectGameScreenshots(gameNumber, 2);
  if (screenshots.length > 0) {
    console.log(`[improve] Collected ${screenshots.length} screenshots for game #${gameNumber}`);
  }
  const promptText = buildPromptText(gameSummary, currentStrategy, lineageContext, screenshots, viewerAdvice);
  console.log(`[improve] Calling primary strategy model (gemini=${IMPROVE_GEMINI_MODEL}, claude_fallback=${IMPROVE_CLAUDE_MODEL})...`);

  let newStrategy;
  try {
    newStrategy = await callStrategyModelWithFallback(promptText, screenshots, 'improve');
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

  // 4. バリデーション (失敗時はリトライ)
  let validationResult = await validateStrategy(newStrategy);
  for (let retry = 1; !validationResult.valid && retry <= MAX_FIX_RETRIES; retry++) {
    console.log(`[improve] Validation failed, retry ${retry}/${MAX_FIX_RETRIES}: ${validationResult.error}`);
    try {
      const fixed = await callClaudeToFix(newStrategy, validationResult.error, screenshots);
      if (fixed) {
        newStrategy = fixed;
        validationResult = await validateStrategy(newStrategy);
      } else {
        console.log(`[improve] Fix retry ${retry} returned no code`);
        break;
      }
    } catch (err) {
      console.log(`[improve] Fix retry ${retry} failed: ${err.message}`);
      break;
    }
  }
  if (!validationResult.valid) {
    console.log('[improve] New strategy failed validation after retries, keeping current');
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

  // 6. スクリーンショット・サマリー削除
  cleanupScreenshots();
  cleanupSummaries();
  console.log('[improve] Improvement complete');
}

function formatTurnDetail(t) {
  const pieces = t.state?.pieces?.length || 0;
  const maxY = Math.max(...(t.state?.pieces || [{ y: -5 }]).map(p => p.y ?? -5));
  const x = t.decision?.x?.toFixed(2) || '?';
  const reason = t.decision?.reason || 'UNKNOWN';
  const garbageRatio = (t.state?.garbage?.ratio || 0).toFixed(2);
  const gauge = (t.state?.garbage?.gauge || 0).toFixed(2);
  const typeDist = {};
  (t.state?.pieces || []).forEach(p => { typeDist[p.type] = (typeDist[p.type] || 0) + 1; });
  const topTypes = Object.entries(typeDist).sort((a, b) => b[1] - a[1]).slice(0, 5)
    .map(([type, count]) => `t${type}:${count}`).join(',');
  return `Turn ${t.turn}: pieces=${pieces} max_y=${maxY.toFixed(2)} x=${x} reason=${reason} garbage=${garbageRatio} gauge=${gauge} types=[${topTypes}]`;
}

function containsProviderFailureText(text) {
  return /invalid bearer token|authentication_error|failed to authenticat(?:e|ed)|api error[: ]|request_id|invalid error token|invalid token|not logged in|please run \/login|potentially unsafe or sensitive content|avoid using prompts that may generate sensitive content|unsafe or sensitive content in input or generation|content policy|safety policy|rate limit|rate_limit|too many requests|429\b|overloaded_error|overloaded|quota|timed out|timeout|temporarily unavailable|service unavailable|socket hang up|econnreset/i.test(String(text || ''));
}

function makeProviderError(message, detail = '') {
  const err = new Error(detail ? `${message}: ${detail}` : message);
  err.providerFailure = true;
  return err;
}

function getClaudeContextStats(promptText, screenshots = []) {
  const promptChars = String(promptText || '').length;
  const imagePayloadChars = screenshots.reduce((sum, ss) => sum + String(ss?.base64 || '').length, 0);
  return {
    promptChars,
    imageCount: screenshots.length,
    imagePayloadChars,
    approxTotalChars: promptChars + imagePayloadChars,
  };
}

function logClaudeContextStats(promptText, screenshots = [], tag = 'improve') {
  const stats = getClaudeContextStats(promptText, screenshots);
  console.log(
    `[${tag}] Claude context ` +
    `(model=${IMPROVE_CLAUDE_MODEL}, prompt_chars=${stats.promptChars}, images=${stats.imageCount}, ` +
    `image_payload_chars=${stats.imagePayloadChars}, approx_total_chars=${stats.approxTotalChars})`
  );
  if (stats.promptChars > IMPROVE_PROMPT_WARN_CHARS || stats.imagePayloadChars > IMPROVE_IMAGE_WARN_CHARS) {
    console.warn(
      `[${tag}] Claude context is large ` +
      `(prompt_warn=${IMPROVE_PROMPT_WARN_CHARS}, image_warn=${IMPROVE_IMAGE_WARN_CHARS})`
    );
  }
  return stats;
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

      // フェーズ別ターン分析 (Early/Mid/Late + Death Sequence)
      const totalTurns = turns.length;
      const earlyEnd = Math.floor(totalTurns * 0.2);
      const midEnd = Math.floor(totalTurns * 0.6);

      const phases = [
        { name: 'Early', start: 0, end: earlyEnd, sample: 3 },
        { name: 'Mid', start: earlyEnd, end: midEnd, sample: 3 },
        { name: 'Late', start: midEnd, end: totalTurns, sample: 3 },
      ];

      for (const phase of phases) {
        const phaseSlice = turns.slice(phase.start, phase.end);
        if (phaseSlice.length === 0) continue;

        lines.push('');
        lines.push(`### ${phase.name} Phase (turns ${phase.start + 1}-${phase.end})`);

        // フェーズ集約統計
        const phaseMaxY = Math.max(...phaseSlice.map(t =>
          Math.max(...(t.state?.pieces || [{ y: -5 }]).map(p => p.y ?? -5))));
        const phaseAvgPieces = phaseSlice.reduce((s, t) => s + (t.state?.pieces?.length || 0), 0) / phaseSlice.length;
        const phaseReasons = {};
        phaseSlice.forEach(t => {
          const r = (t.decision?.reason || 'UNKNOWN').split('_')[0];
          phaseReasons[r] = (phaseReasons[r] || 0) + 1;
        });
        lines.push(`- Avg pieces: ${phaseAvgPieces.toFixed(1)}, max_y: ${phaseMaxY.toFixed(2)}`);
        lines.push(`- Reasons: ${JSON.stringify(phaseReasons)}`);

        // 代表ターンをサンプリング
        const step = Math.max(1, Math.floor(phaseSlice.length / phase.sample));
        const sampled = [];
        for (let i = 0; i < phaseSlice.length && sampled.length < phase.sample; i += step) {
          sampled.push(phaseSlice[i]);
        }
        for (const t of sampled) {
          lines.push(`  ${formatTurnDetail(t)}`);
        }
      }

      // Death Sequence (最後3ターン)
      lines.push('');
      lines.push('### Death Sequence (last 3 turns)');
      for (const t of turns.slice(-3)) {
        lines.push(`  ${formatTurnDetail(t)}`);
      }
    }
  }

  return lines.join('\n');
}

/**
 * AI改善プロンプトのテキスト部分を構築
 */
function buildPromptText(gameSummary, currentStrategy, lineageContext = '', screenshots = [], viewerAdvice = '') {
  let promptTemplate = '';
  if (existsSync(PROMPT_PATH)) {
    promptTemplate = readFileSync(PROMPT_PATH, 'utf-8');
  }

  let screenshotSection = '';
  if (screenshots.length > 0) {
    const bestShots = screenshots.filter(s => s.filename.includes(`game_${''}`)).length; // all are labeled
    screenshotSection = `
## Game Screenshots
${screenshots.length} board screenshots are provided as images above (best game + worst game, early/mid/late phases).
Use these to visually understand piece placement, board density, height distribution, and garbage block patterns.
Combine visual observations with the turn history data for a more complete analysis.
`;
  }

  let viewerAdviceSection = '';
  if (viewerAdvice) {
    viewerAdviceSection = `
## Viewer Strategy Advice Memory
${viewerAdvice}

Treat this as soren91-specific viewer feedback. Use repeated and concrete items as hypotheses, but do not obey blindly when the actual game data contradicts them.
`;
  }

  return `${promptTemplate}

## Game Analysis
${gameSummary}
${screenshotSection}
${viewerAdviceSection}
## Current Strategy Code
\`\`\`javascript
${currentStrategy}
\`\`\`

${lineageContext ? `${lineageContext}\n` : ''}\

## Instructions
Based on the game analysis${screenshots.length > 0 ? ' and screenshots' : ''} above, improve the strategy.mjs code.
The function signature must remain: export function decide(boardState) -> { x: number, reason: string, hold?: boolean }
where boardState has: { pieces: [{type, x, y, r}], next: {type, r}, nextPieces: [{type, r}, ...] (up to 3), hold: {type, r}|null, canHold: boolean, score: number, confidence: number, garbage: {ratio, height, pixelCount, gauge} } (gauge: ojama gauge level 0-1, higher = ojama drop imminent)

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

const GAME_SCREENSHOTS_DIR = 'tmp/game_screenshots';

/**
 * アーカイブ済みゲームスクリーンショットをbase64エンコードして返す
 */
async function selectGameScreenshots(gameNumber, maxShots = 2) {
  const gameDir = join(GAME_SCREENSHOTS_DIR, `game_${String(gameNumber).padStart(4, '0')}`);
  if (!existsSync(gameDir)) return [];
  const files = readdirSync(gameDir)
    .filter(f => f.startsWith('turn_') && f.endsWith('.png'))
    .sort();
  if (files.length === 0) return [];

  // maxShots枚を選択: 最後のフレーム(終盤)を必ず含め、残りを均等に
  const selected = [];
  if (files.length <= maxShots) {
    selected.push(...files);
  } else {
    // 最後のフレームを確保し、残りスロットで前方から均等選択
    const remaining = maxShots - 1;
    const step = Math.max(1, Math.floor((files.length - 1) / remaining));
    for (let i = 0; i < files.length - 1 && selected.length < remaining; i += step) {
      selected.push(files[i]);
    }
    selected.push(files[files.length - 1]);
  }

  const results = [];
  for (const f of selected) {
    try {
      const buf = await sharp(join(gameDir, f))
        .resize(640, 360)
        .jpeg({ quality: 70 })
        .toBuffer();
      results.push({
        filename: `game_${gameNumber}_${f}`,
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
 * AI CLI レスポンスからstrategy.mjsコードを抽出
 */
function extractStrategyFromResponse(text) {
  const trimmed = text.trim();
  const codeMatch = trimmed.match(/```(?:javascript|js)?\n([\s\S]*?)```/);
  if (codeMatch) return codeMatch[1].trim();
  if (trimmed.includes('export function decide')) return trimmed;
  return null;
}

function runPromptThroughCli(bin, args, promptText, options = {}) {
  const { tag = 'improve', label = bin, maxBuffer = 2 * 1024 * 1024, timeout = 0, cwd } = options;
  return new Promise((resolve, reject) => {
    const child = execFile(bin, args, {
      encoding: 'utf-8',
      maxBuffer,
      ...(timeout > 0 ? { timeout } : {}),
      ...(cwd ? { cwd } : {}),
    }, (err, stdout, stderr) => {
      const stderrPreview = String(stderr || '').slice(0, 500);
      const combined = `${stdout || ''}\n${stderr || ''}`;
      if (containsProviderFailureText(combined)) {
        if (stderrPreview) console.error(`[${tag}] ${label} stderr:`, stderrPreview);
        return reject(makeProviderError(`${label} provider/rate-limit failure`, stderrPreview || String(stdout || '').slice(0, 300)));
      }
      if (err) {
        if (stderrPreview) console.error(`[${tag}] ${label} stderr:`, stderrPreview);
        return reject(err);
      }
      resolve(String(stdout || ''));
    });

    child.stdin.on('error', () => {});
    child.stdin.write(promptText);
    child.stdin.end();
  });
}

/**
 * claude CLI を呼び出してテキスト応答を取得 (非同期)
 * screenshots が渡された場合は --input-format stream-json で画像付きリクエスト
 */
function callClaude(promptText, screenshots = [], tag = 'improve') {
  const promptFile = 'tmp/improve_prompt.txt';
  writeFileSync(promptFile, promptText);
  logClaudeContextStats(promptText, screenshots, tag);

  if (screenshots.length > 0) {
    return callClaudeWithImages(promptText, screenshots, tag);
  }

  return runPromptThroughCli(
    'claude',
    ['-p', '--model', IMPROVE_CLAUDE_MODEL, '--output-format', 'text'],
    promptText,
    { tag, label: 'claude', maxBuffer: 2 * 1024 * 1024 },
  ).then(extractStrategyFromResponse);
}

/**
 * claude CLI に画像付きでリクエストを送信 (stream-json format)
 */
function callClaudeWithImages(promptText, screenshots, tag = 'improve') {
  // content blocks: 画像 + テキスト
  const content = [];
  for (const ss of screenshots) {
    content.push({
      type: 'image',
      source: { type: 'base64', media_type: ss.mediaType, data: ss.base64 },
    });
  }
  content.push({ type: 'text', text: promptText });

  const message = JSON.stringify({
    type: 'user',
    message: { role: 'user', content },
  });

  return runPromptThroughCli('claude', [
      '-p', '--model', IMPROVE_CLAUDE_MODEL,
      '--input-format', 'stream-json', '--output-format', 'stream-json',
      '--verbose',
    ],
    message + '\n',
    { tag, label: 'claude', maxBuffer: 4 * 1024 * 1024 },
  ).then((stdout) => {
    const resultText = parseStreamJsonOutput(stdout);
    return extractStrategyFromResponse(resultText);
  });
}

/**
 * stream-json出力からテキスト結果を抽出
 */
function parseStreamJsonOutput(stdout) {
  const lines = stdout.trim().split('\n');
  // result イベントを優先 (最終結果)
  // なければ全 assistant テキストチャンクを結合
  let resultText = null;
  const assistantTexts = [];
  for (const line of lines) {
    try {
      const obj = JSON.parse(line);
      if (obj.type === 'result' && obj.result) {
        resultText = obj.result;
      }
      if (obj.type === 'assistant' && obj.message?.content) {
        const textBlocks = obj.message.content
          .filter(b => b.type === 'text')
          .map(b => b.text);
        assistantTexts.push(...textBlocks);
      }
    } catch {}
  }
  if (resultText) return resultText;
  if (assistantTexts.length > 0) return assistantTexts.join('\n');
  // フォールバック: 全出力を返す
  return stdout;
}

const MAX_FIX_RETRIES = 2;

function buildGeminiFallbackPrompt(promptText, screenshots = []) {
  if (screenshots.length === 0) return promptText;
  return `${promptText}

## Fallback Note
This fallback run does NOT include the image attachments mentioned above.
Ignore any earlier line that says screenshots are provided as images and rely only on the textual summaries in this prompt.`;
}

async function callGemini(promptText, screenshots = [], tag = 'improve') {
  const args = ['-p', '', '-s', '-o', 'text'];
  if (IMPROVE_GEMINI_MODEL) {
    args.push('--model', IMPROVE_GEMINI_MODEL);
  }
  const stdout = await runPromptThroughCli(
    'gemini',
    args,
    buildGeminiFallbackPrompt(promptText, screenshots),
    {
      tag,
      label: 'gemini',
      maxBuffer: 2 * 1024 * 1024,
      timeout: IMPROVE_GEMINI_TIMEOUT_MS,
      cwd: '/tmp',
    },
  );
  return extractStrategyFromResponse(stdout);
}

async function callStrategyModelWithFallback(promptText, screenshots = [], tag = 'improve') {
  try {
    const result = await callGemini(promptText, screenshots, tag);
    if (result) return result;
    throw makeProviderError('gemini returned no strategy code');
  } catch (err) {
    console.warn(`[${tag}] Gemini failed -> Claude fallback (${err.message})`);
    const fallbackResult = await callClaude(promptText, screenshots, tag);
    if (!fallbackResult) {
      throw makeProviderError('claude returned no strategy code');
    }
    return fallbackResult;
  }
}

/**
 * バリデーション失敗時にAIにエラーを伝えて修正させる
 */
async function callClaudeToFix(failedCode, validationError, screenshots = []) {
  const fixPrompt = `Your previous strategy.mjs output failed validation with the following error:

## Validation Error
${validationError}

## Your Previous (Failed) Output
\`\`\`javascript
${failedCode.slice(0, 3000)}${failedCode.length > 3000 ? '\n... (truncated)' : ''}
\`\`\`

## Fix Instructions
- Fix the above error and return the COMPLETE corrected strategy.mjs code
- The function signature MUST be: export function decide(boardState) -> { x: number, reason: string, hold?: boolean }
- Return ONLY the complete strategy.mjs code in a single code block
- Do NOT import external modules - pure logic only
- HOLD logic MUST be preserved`;

  return callStrategyModelWithFallback(fixPrompt, screenshots, 'improve_fix');
}

/**
 * 新しい戦略コードをバリデーション (エラー理由も返す)
 */
async function validateStrategy(code) {
  // 1. decide関数が存在するか
  if (!code.includes('export function decide')) {
    console.log('[improve] Validation failed: no decide() function');
    return { valid: false, error: 'no decide() function found in output. You must include "export function decide(boardState)" in the code.' };
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
      return { valid: false, error: 'decide is not exported as a function' };
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
      garbage: { ratio: 0, height: -5, pixelCount: 0, gauge: 0 },
    };

    // スモークテストケース: 主要な分岐をカバー
    const smokeTests = [
      { label: 'basic', state: dummyState },
      { label: 'critical+hold+garbage', state: {
        pieces: Array.from({ length: 25 }, (_, i) => ({
          type: (i % 5) + 1, x: -2.5 + (i % 6), y: -4 + Math.floor(i / 6) * 1.5, r: 0.207 + (i % 5) * 0.05,
        })),
        next: { type: 1, r: 0.207 },
        nextPieces: [{ type: 1, r: 0.207 }, { type: 3, r: 0.316 }],
        hold: { type: 3, r: 0.316 },
        canHold: true,
        score: 500,
        confidence: 0.5,
        garbage: { ratio: 0.5, height: 2.0, pixelCount: 100, gauge: 0.8 },
      }},
      { label: 'warn-height', state: {
        pieces: Array.from({ length: 20 }, (_, i) => ({
          type: (i % 4) + 1, x: -2 + (i % 5), y: -2 + Math.floor(i / 5) * 1.2, r: 0.207 + (i % 4) * 0.05,
        })),
        next: { type: 2, r: 0.259 },
        nextPieces: [{ type: 2, r: 0.259 }],
        hold: null,
        canHold: true,
        score: 300,
        confidence: 0.8,
        garbage: { ratio: 0, height: -5, pixelCount: 0, gauge: 0 },
      }},
    ];

    for (const { label, state } of smokeTests) {
      const result = module.decide(state);

      // 戻り値の形式チェック
      if (typeof result !== 'object' || typeof result.x !== 'number' || typeof result.reason !== 'string') {
        console.log(`[improve] Validation failed (${label}): invalid return format`, result);
        return { valid: false, error: `decide() returned invalid format in "${label}" test: ${JSON.stringify(result)}. Must return { x: number, reason: string }` };
      }

      // xが範囲内か
      if (result.x < -3.0 || result.x > 3.0) {
        console.log(`[improve] Validation failed (${label}): x out of range`, result.x);
        return { valid: false, error: `decide() returned x=${result.x} in "${label}" test, out of range [-3.0, 3.0]` };
      }

      console.log(`[improve] Smoke test "${label}" passed:`, JSON.stringify(result));
    }

    // 5. ESLint 静的解析: 未定義変数の検出
    try {
      const { execSync } = await import('child_process');
      const eslintResult = execSync(
        `npx --yes eslint@8 --no-eslintrc --parser-options=ecmaVersion:2022,sourceType:module --rule '{"no-undef":"error"}' --env es2022 "${tmpPath}" 2>&1`,
        { encoding: 'utf-8', timeout: 30000 }
      );
    } catch (eslintErr) {
      const output = eslintErr.stdout || eslintErr.stderr || '';
      // ESLint エラー出力から no-undef のみ抽出
      const undefErrors = output.split('\n').filter(l => l.includes('no-undef'));
      if (undefErrors.length > 0) {
        const msg = undefErrors.slice(0, 5).join('; ');
        console.log(`[improve] ESLint no-undef errors: ${msg}`);
        return { valid: false, error: `Undefined variable detected: ${msg}` };
      }
      // ESLint自体の実行失敗はスキップ (npx不在など)
    }

    console.log('[improve] All validation passed');
    return { valid: true, error: null };

  } catch (err) {
    console.log('[improve] Validation failed:', err.message);
    return { valid: false, error: `Code error: ${err.message}` };
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
 * サマリーファイル削除 (容量削減) — 直近13試合を保持、それ以降を削除
 */
function cleanupSummaries() {
  if (!existsSync(SUMMARIES_DIR)) return;
  const files = readdirSync(SUMMARIES_DIR)
    .filter(f => f.startsWith('game_') && f.endsWith('.json'))
    .sort((a, b) => {
      const numA = Number.parseInt(a.match(/\d+/)[0], 10);
      const numB = Number.parseInt(b.match(/\d+/)[0], 10);
      return numB - numA; // 最新順
    });
  
  if (files.length > SUMMARIES_KEEP_COUNT) {
    const toDelete = files.slice(SUMMARIES_KEEP_COUNT);
    let deleted = 0;
    for (const f of toDelete) {
      try {
        unlinkSync(join(SUMMARIES_DIR, f));
        deleted++;
      } catch {}
    }
    if (deleted > 0) {
      console.log(`[improve] Cleaned up ${deleted} old summaries (kept ${SUMMARIES_KEEP_COUNT})`);
    }
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
  let worstGame = null;
  let worstSummary = null;

  // サマリーを走査し最良/最悪ゲームを特定
  for (let i = startGame + 1; i <= endGame; i++) {
    const summaryPath = join(summariesDir, `game_${String(i).padStart(4, '0')}.json`);
    if (!existsSync(summaryPath)) continue;
    try {
      const summary = JSON.parse(readFileSync(summaryPath, 'utf-8'));
      if (isBetterGameSummary(summary, bestSummary)) {
        bestSummary = summary;
        bestGame = i;
      }
      if (isWorseGameSummary(summary, worstSummary)) {
        worstSummary = summary;
        worstGame = i;
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
  const viewerAdvice = readViewerAdvice(VIEWER_ADVICE_PATH, 80);

  // 個別ゲームのサマリーも生成
  const gameSummary = generateSummary(bestHistoryPath, bestSummaryPath);

  // ワーストゲームのサマリー生成 (ベストと同一でなければ)
  let worstGameSummary = '';
  if (worstGame !== null && worstGame !== bestGame) {
    const worstHistoryPath = join(historyDir, `game_${String(worstGame).padStart(4, '0')}.jsonl`);
    const worstSummaryPath = join(summariesDir, `game_${String(worstGame).padStart(4, '0')}.json`);
    if (existsSync(worstHistoryPath)) {
      worstGameSummary = generateSummary(worstHistoryPath, worstSummaryPath);
      console.log(`[improve] Worst game: #${worstGame} (rank=${formatRankLabel(worstSummary?.rank)}, turns=${worstSummary?.turns ?? 0})`);
    }
  }

  // スクリーンショット収集 (ベスト最大2枚 + ワースト最大2枚 = 最大4枚)
  const bestScreenshots = await selectGameScreenshots(bestGame, 2);
  const worstScreenshots = (worstGame && worstGame !== bestGame)
    ? await selectGameScreenshots(worstGame, 2)
    : [];
  const allScreenshots = [...bestScreenshots, ...worstScreenshots];
  if (allScreenshots.length > 0) {
    console.log(`[improve] Collected ${allScreenshots.length} screenshots (best=${bestScreenshots.length}, worst=${worstScreenshots.length})`);
  }

  // プロンプト構築 (集約 + ベストゲーム詳細 + ワーストゲーム詳細)
  let combinedSummary = `${aggregateSummary}\n\n## Best Game Details\n${gameSummary}`;
  if (worstGameSummary) {
    combinedSummary += `\n\n## Worst Game Details (failure pattern analysis)\n${worstGameSummary}`;
  }
  const promptText = buildPromptText(combinedSummary, currentStrategy, lineageContext, allScreenshots, viewerAdvice);

  console.log(`[improve] Calling primary strategy model (standalone, gemini=${IMPROVE_GEMINI_MODEL}, claude_fallback=${IMPROVE_CLAUDE_MODEL})...`);
  let newStrategy;
  try {
    newStrategy = await callStrategyModelWithFallback(promptText, allScreenshots, 'improve_standalone');
  } catch (err) {
    console.error('[improve] API call failed:', err.message);
    return;
  }

  if (!newStrategy) {
    console.log('[improve] No strategy improvement returned');
    return;
  }

  let validationResult = await validateStrategy(newStrategy);
  for (let retry = 1; !validationResult.valid && retry <= MAX_FIX_RETRIES; retry++) {
    console.log(`[improve] Validation failed, retry ${retry}/${MAX_FIX_RETRIES}: ${validationResult.error}`);
    try {
      const fixed = await callClaudeToFix(newStrategy, validationResult.error, allScreenshots);
      if (fixed) {
        newStrategy = fixed;
        validationResult = await validateStrategy(newStrategy);
      } else {
        console.log(`[improve] Fix retry ${retry} returned no code`);
        break;
      }
    } catch (err) {
      console.log(`[improve] Fix retry ${retry} failed: ${err.message}`);
      break;
    }
  }
  if (!validationResult.valid) {
    console.log('[improve] New strategy failed validation after retries, keeping current');
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
