/**
 * ranking_comment.mjs - メリケンAIコメント生成 (ランキング画面 + 試合中盤面)
 *
 * Claude haiku (vision) を使用して:
 * 1. ランキング画面のプレイヤー名認識 + 試合後コメント
 * 2. 試合中の盤面スクリーンショットから中間コメント (1試合1回)
 * 3. TTS読み上げ + Twitchチャット投稿
 */

import { existsSync, writeFileSync, unlinkSync } from 'fs';
import { execFile } from 'child_process';
import { join } from 'path';
import sharp from 'sharp';

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

    // TTS読み上げ + Twitch投稿 (非同期、エラーは無視)
    speakComment(comment, 'soren91:ranking_comment');
    postToTwitch(comment);

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

  // マークダウンのヘッダーや箇条書きが含まれていたら、最後のプレーンテキスト部分を取る
  if (trimmed.includes('**') || trimmed.includes('- ') || trimmed.includes('1.')) {
    const lines = trimmed.split('\n').map(l => l.trim()).filter(Boolean);
    // 末尾から、マークダウン記号を含まない行を探す
    for (let i = lines.length - 1; i >= 0; i--) {
      const line = lines[i];
      if (!line.startsWith('-') && !line.startsWith('*') && !line.startsWith('#') &&
          !line.match(/^\d+\./) && !line.startsWith('|') &&
          line.length >= 5 && line.length <= 120) {
        return line.replace(/^[「『]|[」』]$/g, '').trim();
      }
    }
  }

  // 短い単一行ならそのまま返す
  if (!trimmed.includes('\n') && trimmed.length <= 120) {
    return trimmed.replace(/^[「『]|[」』]$/g, '').trim();
  }

  // 複数行の場合、最後の行が短ければそれがコメント
  const lines = trimmed.split('\n').map(l => l.trim()).filter(Boolean);
  const lastLine = lines[lines.length - 1];
  if (lastLine && lastLine.length <= 120 && lastLine.length >= 5) {
    return lastLine.replace(/^[「『]|[」』]$/g, '').trim();
  }

  return trimmed.slice(0, 120);
}

function callClaudeForComment(base64Image, gameNumber, myRank) {
  const rankInfo = myRank != null ? `自分の順位: ${myRank}位/91人中。` : '';

  const promptText = `あなたは「メリケンAI」。アメリカ製AIで、ソ連ゲーム91をプレイ中。陽気なアメリカン口調の日本語で話す。
${rankInfo}
このランキング画面を見て、試合後の一言コメントを生成せよ。

ルール:
- 画面のプレイヤー名を読み取り、NPC（ロシア風の名前）以外の人間プレイヤーがいれば名前に言及
- 自然な日本語の話し言葉のみ（英語禁止）
- 1〜2文、最大50文字
- コメント本文のみ出力（分析・説明・カッコ・注釈は一切不要）`;

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
 * @param {string} screenshotPath - 盤面スクリーンショットのパス
 * @param {number} gameNumber - ゲーム番号
 * @param {number} turn - 現在のターン数
 * @param {object} boardState - 盤面状態
 */
export async function generateMidgameComment(screenshotPath, gameNumber, turn, boardState) {
  if (!screenshotPath || !existsSync(screenshotPath)) {
    console.log('[midgame_comment] No screenshot available');
    return null;
  }

  try {
    const imageBuffer = await sharp(screenshotPath)
      .resize(960, 540)
      .jpeg({ quality: 75 })
      .toBuffer();
    const base64Image = imageBuffer.toString('base64');

    const comment = await callClaudeForMidgame(base64Image, gameNumber, turn, boardState);
    if (!comment) {
      console.log('[midgame_comment] No comment generated');
      return null;
    }

    console.log(`[midgame_comment] Generated: ${comment}`);

    const logLine = `[${new Date().toISOString()}] game=#${gameNumber} turn=${turn}: ${comment}\n`;
    try { writeFileSync(COMMENT_LOG_PATH, logLine, { flag: 'a' }); } catch {}

    speakComment(comment, 'soren91:midgame_comment');
    postToTwitch(comment);

    return comment;
  } catch (err) {
    console.error(`[midgame_comment] Error: ${err.message}`);
    return null;
  }
}

function callClaudeForMidgame(base64Image, gameNumber, turn, boardState) {
  const pieces = boardState?.pieces?.length ?? '?';
  const garbageRatio = boardState?.garbage?.ratio ?? 0;
  const gauge = boardState?.garbage?.gauge ?? 0;
  const garbageInfo = garbageRatio > 0.05
    ? `おじゃまブロック: ${(garbageRatio * 100).toFixed(0)}%、ゲージ: ${(gauge * 100).toFixed(0)}%。`
    : '';

  const promptText = `あなたは「メリケンAI」。アメリカ製AIで、ソ連ゲーム91（91人対戦・落ちものパズル）をプレイ中。陽気なアメリカン口調の日本語で話す。
現在ターン${turn}、盤面にピース${pieces}個。${garbageInfo}
この盤面スクリーンショットを見て、試合中の実況コメントを生成せよ。

ルール:
- 盤面の状況（ピースの積み上がり具合、危険度、チャンスなど）に言及
- 自然な日本語の話し言葉のみ（英語禁止）
- 1〜2文、最大50文字
- コメント本文のみ出力（分析・説明・カッコ・注釈は一切不要）`;

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
      cwd: '/tmp',
    }, (err, stdout, stderr) => {
      if (err) {
        if (stderr) console.error('[midgame_comment] claude stderr:', stderr.slice(0, 300));
        return reject(err);
      }
      const raw = parseStreamJsonOutput(stdout);
      resolve(extractCommentOnly(raw));
    });

    child.stdin.on('error', () => {});
    child.stdin.write(message + '\n');
    child.stdin.end();
  });
}

// CLI: node ranking_comment.mjs <ranking_image_path> [gameNumber] [rank]
if (import.meta.url === `file://${process.argv[1]}`) {
  const { mkdirSync } = await import('fs');
  const imagePath = process.argv[2];
  const gameNum = parseInt(process.argv[3] || '0', 10);
  const rank = process.argv[4] ? parseInt(process.argv[4], 10) : null;
  if (!imagePath) {
    console.error('Usage: node ranking_comment.mjs <ranking_image_path> [gameNumber] [rank]');
    process.exit(1);
  }
  mkdirSync('tmp', { recursive: true });
  const result = await generateRankingComment(imagePath, gameNum, rank);
  console.log('Result:', result);
}
