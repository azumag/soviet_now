/**
 * comment.mjs - メリケンAIコメント生成 (ランキング画面 + 試合中盤面)
 *
 * Claude を優先し、失敗時は opencode にフォールバックして:
 * 1. ランキング画面OCR + 試合後コメント
 * 2. 試合中の盤面解析データから中間コメント (1試合1回)
 * 3. TTS読み上げ + Twitchチャット投稿
 */

import { existsSync, writeFileSync, readFileSync, unlinkSync, mkdtempSync, rmSync } from 'fs';
import { execFile } from 'child_process';
import { join } from 'path';
import { tmpdir } from 'os';
import { analyzeResultScreen } from './result_screen_ocr.mjs';

const PROMPTS_DIR = join(import.meta.dirname || '.', 'prompts');

function loadPrompt(filename, vars = {}) {
  let text = readFileSync(join(PROMPTS_DIR, filename), 'utf-8');
  for (const [key, value] of Object.entries(vars)) {
    text = text.replaceAll(`{{${key}}}`, value);
  }
  return text;
}

const PARENT_DIR = join(import.meta.dirname || '.', '..');
const SAY_ENQUEUE_SCRIPT = join(PARENT_DIR, 'say_enqueue.sh');
const TWITCH_CHAT_SCRIPT = join(PARENT_DIR, 'twitch_chat.sh');
const COMMENT_LOG_PATH = 'tmp/ranking_comments.log';
const DEFAULT_CLAUDE_MODEL = process.env.SOREN91_COMMENT_CLAUDE_MODEL || 'haiku';
const DEFAULT_OPENCODE_AGENT = process.env.SOREN91_COMMENT_OPENCODE_AGENT || process.env.RADIO_FALLBACK || 'glmflash';
const DEFAULT_CLAUDE_TIMEOUT_MS = 30000;
const DEFAULT_OPENCODE_TIMEOUT_MS = Math.max(
  1000,
  Number.parseInt(process.env.SOREN91_COMMENT_OPENCODE_TIMEOUT || process.env.COMMENT_OPENCODE_TIMEOUT || '30', 10) * 1000,
);
const DEFAULT_OPENCODE_PERMISSION = process.env.SOREN91_COMMENT_OPENCODE_PERMISSION
  || process.env.COMMENT_OPENCODE_PERMISSION
  || '{"*":"deny","read":"allow","glob":"allow","grep":"allow","list":"allow","web":"allow","web-search":"allow"}';

function stripAnsi(text) {
  return String(text || '')
    .replace(/\u001b\[[0-9;]*[a-zA-Z]/g, '')
    .replace(/[\x00-\x09\x0b-\x0d\x0e-\x1f]/g, '')
    .replace(/\r/g, '');
}

function containsProviderErrorText(text) {
  return /invalid bearer token|authentication_error|failed to authenticat(?:e|ed)|api error[: ]|request_id|invalid error token|invalid token|not logged in|please run \/login|potentially unsafe or sensitive content|avoid using prompts that may generate sensitive content|unsafe or sensitive content in input or generation|content policy|safety policy|rate limit|rate_limit|too many requests|429\b|overloaded_error|quota/i.test(String(text || ''));
}

function containsClaudeLoginErrorText(text) {
  return /not logged in|please run \/login/i.test(String(text || ''));
}

function shellSingleQuote(value) {
  return `'${String(value).replace(/'/g, `'\\''`)}'`;
}

function cleanOpencodeOutput(raw) {
  const lines = stripAnsi(raw).split('\n');
  const kept = [];
  for (const line of lines) {
    const trimmed = line.trim();
    if (!trimmed) continue;
    if (trimmed.startsWith('>')) continue;
    if (trimmed === '^D') continue;
    if (trimmed.startsWith('Script started on ')) continue;
    if (trimmed.startsWith('Script done on ')) continue;
    if (/^\/[^ ]*$/.test(trimmed)) continue;
    if (/^\/Users\//.test(trimmed)) continue;
    if (/^⚙/.test(trimmed)) continue;
    if (/^\{\s*"query"/.test(trimmed)) continue;
    if (/^[✗✕×].*\b(read|glob|grep|ls|edit|write|multiedit)\b.*\bfailed\b/i.test(trimmed)) continue;
    if (/^[✱→►▸]\s*(read|glob|grep|ls|edit|write|multiedit)\b/i.test(trimmed)) continue;
    if (/^(read|glob|grep|ls|edit|write|multiedit)\b/i.test(trimmed)) continue;
    if (/^(error|warning)\s*:/i.test(trimmed)) continue;
    if (/file not found:|no such file or directory|permission denied|invalid arguments/i.test(trimmed)) continue;
    kept.push(
      line.replace(/<\/?(arg_name|arg_value|think|analysis|final|assistant_response|tool_call|tool_result)[^>]*>/g, '').trim(),
    );
  }
  return kept.filter(Boolean).join('\n').trim();
}

function makeProviderError(message, detail = '') {
  const err = new Error(detail ? `${message}: ${detail}` : message);
  err.providerFailure = true;
  return err;
}

function runClaudeTextComment(tag, promptText) {
  return new Promise((resolve, reject) => {
    const child = execFile('claude', [
      '-p', '--model', DEFAULT_CLAUDE_MODEL,
      '--verbose',
    ], {
      encoding: 'utf-8',
      maxBuffer: 2 * 1024 * 1024,
      timeout: DEFAULT_CLAUDE_TIMEOUT_MS,
      cwd: '/tmp',
    }, (err, stdout, stderr) => {
      const stderrPreview = String(stderr || '').slice(0, 500);
      const combined = `${stdout || ''}\n${stderr || ''}`;
      if (containsClaudeLoginErrorText(combined)) {
        console.log(`[${tag}] claude unavailable: not logged in`);
      }
      if (containsProviderErrorText(combined)) {
        if (stderrPreview) console.error(`[${tag}] claude stderr:`, stderrPreview);
        return reject(makeProviderError('claude provider/rate-limit failure', stderrPreview || String(stdout || '').slice(0, 300)));
      }
      if (err) {
        if (stderrPreview) console.error(`[${tag}] claude stderr:`, stderrPreview);
        return reject(err);
      }
      const comment = extractCommentOnly(String(stdout || '').trim());
      if (!comment) {
        return reject(new Error('claude returned empty comment'));
      }
      resolve(comment);
    });
    child.stdin.on('error', () => {});
    child.stdin.write(promptText);
    child.stdin.end();
  });
}

function runOpencodeComment(tag, promptText, agent = DEFAULT_OPENCODE_AGENT) {
  const tempDir = mkdtempSync(join(tmpdir(), 'soren91_opencode_comment_'));
  const promptFile = join(tempDir, 'prompt.txt');
  const rawFile = join(tempDir, 'raw.txt');
  writeFileSync(promptFile, promptText, 'utf-8');

  return new Promise((resolve, reject) => {
    const command = `LC_ALL=en_US.UTF-8 opencode run --agent ${shellSingleQuote(agent)} "$(cat ${shellSingleQuote(promptFile)})" 2>&1`;
    execFile('script', ['-q', rawFile, 'bash', '-lc', command], {
      encoding: 'utf-8',
      timeout: DEFAULT_OPENCODE_TIMEOUT_MS,
      env: {
        ...process.env,
        OPENCODE_PERMISSION: DEFAULT_OPENCODE_PERMISSION,
      },
      maxBuffer: 2 * 1024 * 1024,
    }, (err) => {
      try {
        const raw = existsSync(rawFile) ? readFileSync(rawFile, 'utf-8') : '';
        const cleaned = cleanOpencodeOutput(raw);
        if (containsProviderErrorText(cleaned)) {
          return reject(makeProviderError(`opencode provider failure (${agent})`, cleaned.slice(0, 300)));
        }
        if (err) {
          if (cleaned) console.error(`[${tag}] opencode raw:`, cleaned.slice(0, 500));
          return reject(err);
        }
        const comment = extractCommentOnly(cleaned);
        if (!comment) {
          return reject(new Error(`opencode returned empty comment (${agent})`));
        }
        resolve(comment);
      } finally {
        try { unlinkSync(promptFile); } catch {}
        try { unlinkSync(rawFile); } catch {}
        try { rmSync(tempDir, { recursive: true, force: true }); } catch {}
      }
    });
  });
}

async function withOpencodeFallback(tag, claudeRunner, opencodePromptBuilder) {
  try {
    return await claudeRunner();
  } catch (err) {
    console.log(`[${tag}] claude failed -> opencode fallback (${err.message})`);
    const fallbackPrompt = await opencodePromptBuilder();
    return runOpencodeComment(tag, fallbackPrompt);
  }
}

/**
 * ランキング画面からコメントを生成して読み上げ + Twitch投稿
 * @param {string} rankingImagePath - ランキングスクリーンショットのパス
 * @param {number} gameNumber - ゲーム番号
 * @param {number|null} myRank - 自分の順位
 */
export async function generateRankingComment(rankingImagePath, gameNumber, myRank) {
  if (!rankingImagePath || !existsSync(rankingImagePath)) {
    console.log('[ranking_comment] No ranking image available');
    return null;
  }

  try {
    const promptText = await buildRankingTextPrompt(rankingImagePath, myRank);
    const comment = await callClaudeForComment(promptText);
    if (!comment) {
      console.log('[ranking_comment] No comment generated');
      return null;
    }

    console.log(`[ranking_comment] Generated: ${comment}`);

    // ログ記録
    const logLine = `[${new Date().toISOString()}] game=#${gameNumber} rank=${myRank ?? '?'}: ${comment}\n`;
    try { writeFileSync(COMMENT_LOG_PATH, logLine, { flag: 'a' }); } catch {}

    // soren91 モードフラグを削除（新しい ranking_comment を再生可能にする）
    const flagFile = join(PARENT_DIR, 'tmp', '.soren91_mode_active');
    try { unlinkSync(flagFile); } catch {}

    // TTS読み上げ (非同期、エラーは無視)
    speakComment(comment, 'soren91:ranking_comment');

    return comment;
  } catch (err) {
    console.error(`[ranking_comment] Error: ${err.message}`);
    return null;
  }
}

/**
 * 生の応答テキストからコメント本文のみを抽出
 * AI が分析テキストを付けてしまった場合に最後の短い文を取る
 */
function extractCommentOnly(raw) {
  if (!raw) return null;
  const trimmed = raw.trim();

  // マークダウンのヘッダーや箇条書きが含まれていたら、プレーンテキスト行だけ結合
  if (trimmed.includes('**') || trimmed.includes('- ') || trimmed.includes('1.')) {
    const lines = trimmed.split('\n').map(l => l.trim()).filter(Boolean);
    const plainLines = lines.filter(line =>
      !line.startsWith('-') && !line.startsWith('*') && !line.startsWith('#') &&
      !line.match(/^\d+\./) && !line.startsWith('|') && line.length >= 3
    );
    if (plainLines.length > 0) {
      return plainLines.join('').replace(/^[「『]|[」』]$/g, '').trim().slice(0, 350);
    }
  }

  // 200文字以内ならそのまま返す（改行は除去して結合）
  const joined = trimmed.split('\n').map(l => l.trim()).filter(Boolean).join('');
  if (joined.length <= 350) {
    return joined.replace(/^[「『]|[」』]$/g, '').trim();
  }

  return joined.slice(0, 350);
}

async function buildRankingTextPrompt(rankingImagePath, myRank) {
  const rankInfo = myRank != null ? `自分の順位: ${myRank}位/91人中。` : '';
  let ocrInfo = '- OCR抽出テキストは十分に得られませんでした。';
  try {
    const ocr = await analyzeResultScreen(rankingImagePath);
    const lines = [];
    if (ocr?.rank != null) {
      lines.push(`- OCR推定順位: ${ocr.rank}位/91人中。`);
    }
    if (ocr?.playerNames?.length) {
      lines.push(`- OCRプレイヤー名候補: ${ocr.playerNames.slice(0, 8).join(' / ')}`);
    }
    if (ocr?.lines?.length) {
      lines.push(...ocr.lines.slice(0, 8).map(line => `- ${line}`));
    }
    if (lines.length > 0) {
      ocrInfo = lines.join('\n');
    }
  } catch (err) {
    ocrInfo = `- OCR補助情報の取得失敗: ${err.message}`;
  }
  return loadPrompt('ranking_comment.md', { rankInfo, ocrInfo });
}

function callClaudeForComment(promptText) {
  return withOpencodeFallback(
    'ranking_comment',
    () => runClaudeTextComment('ranking_comment', promptText),
    async () => promptText,
  );
}

/**
 * TTS読み上げ (親プロジェクトのsay_enqueue.sh経由)
 */
function speakComment(comment, contextLabel = 'soren91:comment') {
  if (!existsSync(SAY_ENQUEUE_SCRIPT)) {
    console.log('[ranking_comment] say_enqueue.sh not found, skip TTS');
    return;
  }

  // 親ディレクトリの tmp/ に書き込む (say_enqueue.sh のcwdが親ディレクトリのため)
  const tmpFile = join(PARENT_DIR, 'tmp', `ranking_comment_${Date.now()}.txt`);
  try {
    writeFileSync(tmpFile, comment + '\n');
    const voicevoxSpeaker = process.env.SOREN91_VOICEVOX_SPEAKER || '46';
    execFile('/bin/bash', [SAY_ENQUEUE_SCRIPT, tmpFile, '1.0', '0'], {
      cwd: PARENT_DIR,
      env: {
        ...process.env,
        SAY_VOICEVOX_SPEAKER_OVERRIDE: voicevoxSpeaker,
        SAY_CONTEXT_LABEL: contextLabel,
      },
      timeout: 10000,
    }, (err) => {
      if (err) console.log(`[ranking_comment] TTS error: ${err.message}`);
      try { unlinkSync(tmpFile); } catch {}
    });
  } catch (err) {
    console.log(`[ranking_comment] TTS setup error: ${err.message}`);
  }
}

/**
 * Twitchチャットに投稿 (親プロジェクトのtwitch_chat.sh経由)
 */
function postToTwitch(comment) {
  if (!existsSync(TWITCH_CHAT_SCRIPT)) return;

  execFile('/bin/bash', [TWITCH_CHAT_SCRIPT, 'send', comment], {
    cwd: PARENT_DIR,
    timeout: 10000,
  }, (err) => {
    if (err && !err.killed) {
      console.log(`[ranking_comment] Twitch post error: ${err.message}`);
    }
  });
}

/**
 * 試合中の盤面コメントを生成 (1試合1回)
 * @param {number} gameNumber - ゲーム番号
 * @param {number} turn - 現在のターン数
 * @param {object} boardState - 盤面状態
 * @param {string} [_screenshotPath] - 後方互換のため残す未使用引数
 */
export async function generateMidgameComment(gameNumber, turn, boardState, _screenshotPath) {
  if (!boardState || !boardState.pieces || boardState.pieces.length === 0) {
    console.log('[midgame_comment] No board state or empty pieces');
    return null;
  }

  try {
    const comment = await callClaudeForMidgame(gameNumber, turn, boardState);
    if (!comment) {
      console.log('[midgame_comment] No comment generated');
      return null;
    }

    console.log(`[midgame_comment] Generated: ${comment}`);

    const logLine = `[${new Date().toISOString()}] game=#${gameNumber} turn=${turn}: ${comment}\n`;
    try { writeFileSync(COMMENT_LOG_PATH, logLine, { flag: 'a' }); } catch {}

    speakComment(comment, 'soren91:midgame_comment');

    return comment;
  } catch (err) {
    console.error(`[midgame_comment] Error: ${err.message}`);
    return null;
  }
}

function formatBoardStateForPrompt(boardState, turn) {
  const rawPieces = boardState?.pieces ?? [];
  const garbageRatio = boardState?.garbage?.ratio ?? 0;
  const gauge = boardState?.garbage?.gauge ?? 0;
  const hold = boardState?.hold;
  const nextPieces = boardState?.nextPieces ?? [];

  // UI要素の誤検出(ゴーストピース)を除外
  // デッドライン付近は座標のみ、ボード内部はtype一致も要求(本物のピースを消さない)
  const GHOST_POSITIONS = [
    { x: -3.27, y: 3.25, type: null }, { x: -1.44, y: 3.14, type: null },
    { x: -1.64, y: 1.91, type: 1 },   { x: -0.03, y: 0.77, type: 4 },
  ];
  const pieces = rawPieces.filter(p => {
    const px = p.x ?? 0, py = p.y ?? -5, pt = p.type;
    return !GHOST_POSITIONS.some(g =>
      Math.abs(px - g.x) < 0.15 && Math.abs(py - g.y) < 0.15 &&
      (g.type === null || pt === g.type)
    );
  });
  const pieceCount = pieces.length;

  // 面積ベースの盤面充填率（ピース面積合計 / 盤面面積）
  const boardArea = 7.0 * 8.32; // board width * height in game coords
  let pieceArea = 0;
  for (const p of pieces) {
    const r = p.r ?? 0.2;
    pieceArea += Math.PI * r * r;
  }
  const fillPct = Math.max(0, Math.min(100, (pieceArea / boardArea) * 100)).toFixed(0);

  // 最高地点（デッドラインへの近さ = 危険度）
  // 警告ライン(y=1.2)以下は0%、デッドライン(y=3.32)で100%
  const warnY = 1.2;
  const deadlineY = 3.32;
  const maxY = pieces.length > 0
    ? Math.max(...pieces.map(p => (p.y ?? -5) + (p.r ?? 0)))
    : -5;
  const dangerPct = Math.max(0, Math.min(100, ((maxY - warnY) / (deadlineY - warnY)) * 100)).toFixed(0);

  // ピースのtype別集計
  const typeCounts = {};
  for (const p of pieces) {
    const t = p.type ?? '?';
    typeCounts[t] = (typeCounts[t] || 0) + 1;
  }
  const typeStr = Object.entries(typeCounts)
    .sort((a, b) => Number(b[0]) - Number(a[0]))
    .map(([t, c]) => `type${t}×${c}`)
    .join(', ');

  // 状況を自然言語で表現（数値を直接見せない）
  const fillLevel = fillPct < 20 ? 'スカスカ' : fillPct < 40 ? 'まだ余裕あり' : fillPct < 60 ? 'そこそこ埋まっている' : fillPct < 80 ? 'かなり埋まっている' : 'ほぼ満杯';
  const dangerLevel = dangerPct <= 0 ? '安全' : dangerPct < 30 ? 'まだ余裕あり' : dangerPct < 50 ? 'やや高くなってきた' : dangerPct < 70 ? '危険が迫っている' : dangerPct < 90 ? 'かなり危険' : '瀕死';

  let info = `ターン${turn}、盤面にピース${pieceCount}個。`;
  info += `\n盤面の状態: ${fillLevel}`;
  info += `\n積み上がり: ${dangerLevel}`;
  info += `\nピース内訳: ${typeStr || 'なし'}`;

  if (garbageRatio > 0.05) {
    info += `\nおじゃまブロック: ${(garbageRatio * 100).toFixed(0)}%、ゲージ: ${(gauge * 100).toFixed(0)}%`;
  }
  if (hold) {
    info += `\nHOLD: type${hold.type}`;
  }
  if (nextPieces.length > 0) {
    info += `\nNEXT: ${nextPieces.map(p => `type${p.type}`).join(', ')}`;
  }

  return info;
}

async function callClaudeForMidgame(gameNumber, turn, boardState) {
  const boardInfo = formatBoardStateForPrompt(boardState, turn);
  const promptText = loadPrompt('midgame_comment.md', { boardInfo });

  return withOpencodeFallback(
    'midgame_comment',
    () => runClaudeTextComment('midgame_comment', promptText),
    async () => promptText,
  );
}

// CLI: node comment.mjs <ranking_image_path> [gameNumber] [rank]
if (import.meta.url === `file://${process.argv[1]}`) {
  const { mkdirSync } = await import('fs');
  const imagePath = process.argv[2];
  const gameNum = parseInt(process.argv[3] || '0', 10);
  const rank = process.argv[4] ? parseInt(process.argv[4], 10) : null;
  if (!imagePath) {
    console.error('Usage: node comment.mjs <ranking_image_path> [gameNumber] [rank]');
    process.exit(1);
  }
  mkdirSync('tmp', { recursive: true });
  const result = await generateRankingComment(imagePath, gameNum, rank);
  console.log('Result:', result);
}
