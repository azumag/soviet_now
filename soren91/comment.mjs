/**
 * comment.mjs - メリケンAIコメント生成 (ランキング画面 + 試合中盤面)
 *
 * Claude haiku (vision) を使用して:
 * 1. ランキング画面のプレイヤー名認識 + 試合後コメント
 * 2. 試合中の盤面スクリーンショットから中間コメント (1試合1回)
 * 3. TTS読み上げ + Twitchチャット投稿
 */

import { existsSync, writeFileSync, readFileSync, unlinkSync } from 'fs';
import { execFile } from 'child_process';
import { join } from 'path';
import sharp from 'sharp';

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
    const imageBuffer = await sharp(rankingImagePath)
      .resize(960, 540)
      .jpeg({ quality: 75 })
      .toBuffer();
    const base64Image = imageBuffer.toString('base64');

    const comment = await callClaudeForComment(base64Image, gameNumber, myRank);
    if (!comment) {
      console.log('[ranking_comment] No comment generated');
      return null;
    }

    console.log(`[ranking_comment] Generated: ${comment}`);

    // ログ記録
    const logLine = `[${new Date().toISOString()}] game=#${gameNumber} rank=${myRank ?? '?'}: ${comment}\n`;
    try { writeFileSync(COMMENT_LOG_PATH, logLine, { flag: 'a' }); } catch {}

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

function callClaudeForComment(base64Image, gameNumber, myRank) {
  const rankInfo = myRank != null ? `自分の順位: ${myRank}位/91人中。` : '';
  const promptText = loadPrompt('ranking_comment.md', { rankInfo });

  const content = [
    {
      type: 'image',
      source: { type: 'base64', media_type: 'image/jpeg', data: base64Image },
    },
    { type: 'text', text: promptText },
  ];

  const message = JSON.stringify({
    type: 'user',
    message: { role: 'user', content },
  });

  return new Promise((resolve, reject) => {
    const child = execFile('claude', [
      '-p', '--model', 'haiku',
      '--input-format', 'stream-json', '--output-format', 'stream-json',
      '--verbose',
    ], {
      encoding: 'utf-8',
      maxBuffer: 2 * 1024 * 1024,
      timeout: 30000,
      cwd: '/tmp', // CLAUDE.md 読み込みを回避してトークン節約
    }, (err, stdout, stderr) => {
      if (err) {
        if (stderr) console.error('[ranking_comment] claude stderr:', stderr.slice(0, 300));
        return reject(err);
      }
      const raw = parseStreamJsonOutput(stdout);
      resolve(extractCommentOnly(raw));
    });

    // EPIPE エラーを無視 (claude プロセスが先に終了した場合)
    child.stdin.on('error', () => {});
    child.stdin.write(message + '\n');
    child.stdin.end();
  });
}

function parseStreamJsonOutput(stdout) {
  const lines = stdout.trim().split('\n');
  for (const line of lines) {
    try {
      const obj = JSON.parse(line);
      if (obj.type === 'result' && obj.result) return obj.result;
      if (obj.type === 'assistant' && obj.message?.content) {
        const textBlocks = obj.message.content
          .filter(b => b.type === 'text')
          .map(b => b.text);
        if (textBlocks.length > 0) return textBlocks.join('\n');
      }
    } catch {}
  }
  return stdout;
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
 */
export async function generateMidgameComment(gameNumber, turn, boardState) {
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
  const deadlineY = 3.32;
  const maxY = pieces.length > 0
    ? Math.max(...pieces.map(p => (p.y ?? -5) + (p.r ?? 0)))
    : -5;
  const dangerPct = Math.max(0, Math.min(100, ((maxY + 5) / (deadlineY + 5)) * 100)).toFixed(0);

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

  let info = `ターン${turn}、盤面にピース${pieceCount}個。`;
  info += `\n盤面充填率: ${fillPct}%（ピース面積ベース）`;
  info += `\nデッドライン危険度: ${dangerPct}%（最高地点y=${maxY.toFixed(1)}）`;
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

function callClaudeForMidgame(gameNumber, turn, boardState) {
  const boardInfo = formatBoardStateForPrompt(boardState, turn);

  const promptText = loadPrompt('midgame_comment.md', { boardInfo });

  return new Promise((resolve, reject) => {
    const child = execFile('claude', [
      '-p', '--model', 'haiku',
      '--verbose',
    ], {
      encoding: 'utf-8',
      maxBuffer: 2 * 1024 * 1024,
      timeout: 30000,
      cwd: '/tmp',
      env: { ...process.env, CLAUDE_INPUT: promptText },
    }, (err, stdout, stderr) => {
      if (err) {
        if (stderr) console.error('[midgame_comment] claude stderr:', stderr.slice(0, 300));
        return reject(err);
      }
      resolve(extractCommentOnly(stdout.trim()));
    });

    child.stdin.on('error', () => {});
    child.stdin.write(promptText);
    child.stdin.end();
  });
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
