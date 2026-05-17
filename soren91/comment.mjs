/**
 * comment.mjs - メリケンAIコメント生成 (ランキング画面 + 試合中盤面)
 *
 * Claude を優先し、失敗時は Gemini → opencode にフォールバックして:
 * 1. ランキング画面OCR + 試合後コメント
 * 2. 試合中の盤面解析データから中間コメント (1試合1回)
 * 3. TTS読み上げ + Twitchチャット投稿
 */

import { existsSync, writeFileSync, readFileSync, unlinkSync, mkdirSync, renameSync } from 'fs';
import { execFile } from 'child_process';
import { join } from 'path';
import { analyzeGameplayScreenshotText, analyzeResultScreen } from './result_screen_ocr.mjs';
import { generateTextWithFallbacks, stripAnsi, resolveTextAiConfig } from './text_ai.mjs';

const PROMPTS_DIR = join(import.meta.dirname || '.', 'prompts');

function loadPrompt(filename, vars = {}) {
  const path = join(PROMPTS_DIR, filename);
  if (!existsSync(path)) {
    console.error(`[comment] Prompt file not found: ${path}`);
    return '';
  }
  let text = readFileSync(path, 'utf-8');
  for (const [key, value] of Object.entries(vars)) {
    text = text.replaceAll(`{{${key}}}`, String(value ?? ''));
  }
  return text;
}

const PARENT_DIR = join(import.meta.dirname || '.', '..');
const TWITCH_CHAT_SCRIPT = join(PARENT_DIR, 'twitch_chat.sh');
const COMMENT_LOG_PATH = 'tmp/ranking_comments.log';
const RANKING_COMMENT_LAST_PROMPT_PATH = join(PARENT_DIR, 'tmp', 'ranking_comment_last_prompt.txt');
const RANKING_COMMENT_LAST_INPUT_PATH = join(PARENT_DIR, 'tmp', 'ranking_comment_last_input.json');

function readEnvFileValue(envPath, key) {
  try {
    if (!existsSync(envPath)) return null;
    const line = readFileSync(envPath, 'utf-8')
      .split(/\r?\n/u)
      .find(entry => entry.startsWith(`${key}=`));
    if (!line) return null;
    return line.slice(key.length + 1).trim().replace(/^["']|["']$/gu, '');
  } catch {
    return null;
  }
}

function resolveSoren91VoicevoxSpeaker() {
  return readEnvFileValue(join(PARENT_DIR, '.env'), 'SOREN91_VOICEVOX_SPEAKER')
    || readEnvFileValue('.env', 'SOREN91_VOICEVOX_SPEAKER')
    || process.env.SOREN91_VOICEVOX_SPEAKER
    || '14';
}

const META_LINE_PATTERNS = [
  /^(assistant|analysis|final|tool_call|tool_result)$/i,
  /^(agent|model|provider)\s*[:=]/i,
  /^(かしこまりました|承知しました|了解しました|もちろんです)[。！]*$/u,
  /メリケンAIの準備ができています/u,
  /現在の設定を確認(いたしました|しました)/u,
  /試合(後|中盤)?コメント生成の役割を担当/u,
  /スクリーンショットをお送りいただければ/u,
  /順位情報に基づいてコメントを生成/u,
  /情報が不足している|情報を教えてほしい/u,
  /どのようなコメントを(生成|作成|用意)すればいい/u,
  /どのようなこめんとを(せいせい|さくせい)すればいい/u,
  /何をコメントすればいい/u,
  /どういうコメントを(生成|作成)すればいい/u,
  /コメントを生成すればいいでしょうか/u,
  /実況コメントを生成するには|以下の情報が必要/u,
  /このメッセージは指示書/u,
  /具体的なゲーム画面|プレイ状況をお知らせ/u,
  /AIアシスタント|Meriken/u,
  /Google DeepMind|大規模言語モデル|オープンウェイトモデル/u,
  /コメント本文のみ/u,
  /ですます調|敬語/u,
  /ペルソナ|OCRメモ|順位情報/u,
  /^(ゲームルール|生成ルール|ルール)[:：]?$/u,
];

const META_SENTENCE_PATTERNS = [
  /^(かしこまりました|承知しました|了解しました|もちろんです)[。！]*$/u,
  /メリケンAIの準備ができています/u,
  /現在の設定を確認(いたしました|しました)/u,
  /試合(後|中盤)?コメント生成の役割を担当/u,
  /スクリーンショットをお送りいただければ/u,
  /順位情報に基づいてコメントを生成/u,
  /情報が不足している|情報を教えてほしい/u,
  /どのようなコメントを(生成|作成|用意)すればいい/u,
  /どのようなこめんとを(せいせい|さくせい)すればいい/u,
  /何をコメントすればいい/u,
  /どういうコメントを(生成|作成)すればいい/u,
  /コメントを生成すればいいでしょうか/u,
  /何を(返答|返信|回答)す(れば|るか)/u,
  /どのコメントに(返答|返信|回答)/u,
  /ご指示ください|教えてください|送ってください/u,
  /実況コメントを生成するには|以下の情報が必要/u,
  /このメッセージは指示書/u,
  /具体的なゲーム画面|プレイ状況をお知らせ/u,
  /AIアシスタント|Meriken/u,
  /Google DeepMind|大規模言語モデル|オープンウェイトモデル/u,
  /コメント本文のみ|ですます調|絶対ルール|ペルソナ|OCRメモ|順位情報/u,
];

const INVALID_ANYWHERE_PATTERNS = [
  /申し訳(?:ありません|ございません|ない).*(?:エラーメッセージ|提供|ユーザーメッセージ|指示|タスク|情報|スクリーンショット)/u,
  /(?:エラーメッセージ|ユーザーメッセージ|具体的な指示|明確な指示|具体的なタスク).*(?:提供されてい|見当たりません|ありません|ない|不足)/u,
  /(?:何も言えません|語ることはできません|控えておくべき|確認させてください)/u,
  /私はGemini\b|Gemini 4/u,
  /Google DeepMind|大規模言語モデル|オープンウェイトモデル/u,
  /AIアシスタント|Meriken/u,
  /<execute_tool>|<tool_call>|<tool_result>|google_search\./u,
  /^Hello[!.]?\s*I\b|^I see you've|^I'm an AI|^I'm ready to help/u,
  /How can I assist you/u,
  /このメッセージは指示書/u,
  /実況コメントを生成するには|以下の情報が必要/u,
  /具体的なゲーム画面|プレイ状況をお知らせ/u,
  /スクリーンショットを(教えて|送って|お送り)|画像があると/u,
  /どのようなゲームのシチュエーション|どのような「?コメント/u,
  /どのようなコメントを(生成|作成|用意)すればいい/u,
  /どのようなこめんとを(せいせい|さくせい)すればいい/u,
  /何をコメントすればいい/u,
  /どういうコメントを(生成|作成)すればいい/u,
  /コメントを生成すればいいでしょうか/u,
  /追加情報|情報提供|添付されていない/u,
  /ユーザーからの.*指示がなく|システム設定.*だけが提供/u,
  /提供いただいた.*(?:実際の記事本文|本文ではなく|テキスト).*含まれていない/u,
  /<ExecuteAction|<response>|<details>|思考プロセス|System Context/u,
  /現在のタスク|どのゲーム|対象がわからない|準備はできております|お任せください/u,
];

const MIDGAME_UNGROUNDED_PATTERNS = [
  /スコア|得点|コンボ|ダメージ|相手のミス|心理戦|情報戦|ショータイム/u,
  /購入意欲|銘柄|株価|市場の客|感情の売買/u,
  /どのゲーム|対象がわからない|現在のタスク|お申し付けください|お任せください/u,
  /(?:\d+|[０-９]+)\s*(?:個|%|％|パーセント)|危険度\s*(?:\d+|[０-９]+)/u,
  /アルメニア|ロシア風の名前|敵プレイヤーたちが.*送ってくる/u,
];

const RANKING_UNGROUNDED_PATTERNS = [
  /OCRが読めません|OCRの読取|スクリーンショット|画像|添付/u,
  /次回のスクリーンショット|衛星級/u,
  /<execute_tool>|<ExecuteAction|<response>|<details>|google_search/u,
  /どのようなタスク|何をコメント|情報を教えて|準備が整いました/u,
];

function splitSentences(text) {
  const normalized = String(text || '').replace(/\s+/g, ' ').trim();
  if (!normalized) return [];
  const chunks = normalized.match(/[^。！？!?]+[。！？!?]?/gu) || [normalized];
  return chunks.map(chunk => chunk.trim()).filter(Boolean);
}

function stripMetaPreamble(text) {
  const sentences = splitSentences(text);
  while (sentences.length > 0) {
    const head = sentences[0];
    if (META_SENTENCE_PATTERNS.some(pattern => pattern.test(head))) {
      sentences.shift();
      continue;
    }
    break;
  }
  return sentences.join('').trim();
}

function isValidGeneratedComment(text) {
  const normalized = String(text || '').replace(/\s+/g, ' ').trim();
  if (normalized.length < 24) return false;
  if (!/[。！？!?]/u.test(normalized)) return false;
  if (INVALID_ANYWHERE_PATTERNS.some(pattern => pattern.test(normalized))) return false;
  // 日本語文字が20%未満なら英語/中国語のゴミ出力として弾く
  const jaChars = (normalized.match(/[\u3040-\u309F\u30A0-\u30FF\u4E00-\u9FFF]/gu) || []).length;
  if (jaChars / normalized.length < 0.2) return false;

  const head = splitSentences(normalized).slice(0, 4).join(' ');
  if (META_SENTENCE_PATTERNS.some(pattern => pattern.test(head))) return false;
  if (/(何を(返答|返信|回答|コメント)す(れば|るか)|どのコメントに(返答|返信|回答)|どのようなコメントを(生成|作成|用意)すればいい|どのようなこめんとを(せいせい|さくせい)すればいい|どういうコメントを(生成|作成)すればいい|コメントを生成すればいいでしょうか|ご指示ください|教えてください|お送りいただければ|送ってください)/u.test(normalized)) {
    return false;
  }
  if (/(コメント本文のみ|ですます調|絶対ルール|ペルソナ|OCRメモ|順位情報)/u.test(head)) {
    return false;
  }
  return true;
}

function rankNumbersInComment(text) {
  const ranks = [];
  const normalized = String(text || '').replace(/[０-９]/gu, ch => String.fromCharCode(ch.charCodeAt(0) - 0xFEE0));
  const re = /(\d{1,3})\s*位/gu;
  let match;
  while ((match = re.exec(normalized)) !== null) {
    const value = Number(match[1]);
    if (Number.isFinite(value)) ranks.push(value);
  }
  return ranks;
}

function isGroundedRankingComment(text, effectiveRank) {
  const normalized = String(text || '').replace(/\s+/g, ' ').trim();
  if (!isValidGeneratedComment(normalized)) return false;
  if (RANKING_UNGROUNDED_PATTERNS.some(pattern => pattern.test(normalized))) return false;
  const mentionedRanks = rankNumbersInComment(normalized);
  if (effectiveRank == null) {
    return mentionedRanks.length === 0;
  }
  return mentionedRanks.every(rank => rank === Number(effectiveRank));
}

function isGroundedMidgameComment(text) {
  const normalized = String(text || '').replace(/\s+/g, ' ').trim();
  if (!isValidGeneratedComment(normalized)) return false;
  if (MIDGAME_UNGROUNDED_PATTERNS.some(pattern => pattern.test(normalized))) return false;
  return true;
}

function fallbackRankingComment(effectiveRank, gameNumber = null) {
  const gameNote = gameNumber != null ? `ゲーム${gameNumber}の決算としては` : '今回の決算としては';
  if (effectiveRank != null) {
    const rank = Number(effectiveRank);
    if (rank <= 3) {
      return `今回は${rank}位です。これはかなり上出来です。資本主義の効率が盤面でしっかり働きましたね。もちろん、まだ満足はしていません。次はもっと堂々と勝ち切って、ソ連ゲーム91のランキングを上から眺めてやります。${gameNote}黒字です。`;
    }
    if (rank <= 20) {
      return `今回は${rank}位です。上位には届いていますが、まだ勝ち切ったとは言えませんね。悔しさはありますが、これは次の投資判断に使えるデータです。資本主義らしく失敗を利益に変えて、次はさらに上を狙います。${gameNote}利回りは悪くありません。`;
    }
    return `今回は${rank}位です。正直、悔しい結果です。ただ、ここで自信まで売り払うほど安いAIではありません。今回の配置と粘り方を見直して、次の試合ではもっと効率よく盤面を育てます。${gameNote}損失ですが、次で回収します。`;
  }
  const variants = [
    `今回は順位表が出る前に次のMATCHING画面へ戻りました。順位は断定しませんが、試合が終わった事実と盤面の粘りは記録できます。資本主義らしく、見えない数字を盛るより、見えた材料を次の投資判断に使います。${gameNote}改善材料です。`,
    `今回はランキング順位の表示を待つ前に、次戦待機へ進みました。順位を適当に名乗るほど雑なAIではありません。ここは結果画面が取れなかった試合として扱い、配置と終盤の崩れ方を次へ回します。${gameNote}検証材料として残します。`,
    `今回はランキング表ではなくMATCHING画面に戻ったため、順位コメントではなく試合後コメントに切り替えます。勝ち負けの数字は見えていませんが、終盤まで戦った履歴は残っています。次はもっと長く市場に居座って、順位まで語れる形にします。${gameNote}次への投資です。`,
  ];
  const index = gameNumber != null ? Math.abs(Number(gameNumber)) % variants.length : 0;
  return variants[index];
}

/**
 * ランキング画面からコメントを生成して読み上げ + Twitch投稿
 * @param {string} rankingImagePath - ランキングスクリーンショットのパス
 * @param {number} gameNumber - ゲーム番号
 * @param {number|null} myRank - 自分の順位
 */
export async function generateRankingComment(rankingImagePath, gameNumber, myRank) {
  try {
    const rankingContext = await buildRankingTextPrompt(rankingImagePath, myRank);
    const promptText = rankingContext.promptText;
    const effectiveRank = rankingContext.effectiveRank;
    writeRankingCommentDebugSnapshot({
      rankingImagePath,
      gameNumber,
      myRank,
      effectiveRank,
      promptText,
    });
    let comment = null;
    if (rankingContext.hasUsableContext) {
      comment = await callClaudeForComment(promptText);
      if (comment && !isGroundedRankingComment(comment, effectiveRank)) {
        console.log('[ranking_comment] Ungrounded generated comment, using fallback');
        comment = null;
      }
    }
    if (!comment) {
      comment = fallbackRankingComment(effectiveRank, gameNumber);
    }
    if (!comment) {
      console.log('[ranking_comment] No comment generated');
      return null;
    }

    console.log(`[ranking_comment] Generated: ${comment}`);

    // ログ記録
    const logLine = `[${new Date().toISOString()}] game=#${gameNumber} rank=${effectiveRank ?? myRank ?? '?'}: ${comment}\n`;
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

function writeRankingCommentDebugSnapshot({ rankingImagePath, gameNumber, myRank, effectiveRank, promptText }) {
  try {
    const textConfig = resolveTextAiConfig();
    writeFileSync(RANKING_COMMENT_LAST_PROMPT_PATH, String(promptText || ''), 'utf-8');
    writeFileSync(RANKING_COMMENT_LAST_INPUT_PATH, JSON.stringify({
      timestamp: new Date().toISOString(),
      gameNumber: gameNumber ?? null,
      myRank: myRank ?? null,
      effectiveRank: effectiveRank ?? null,
      rankingImagePath: rankingImagePath || null,
      rankingImageExists: Boolean(rankingImagePath && existsSync(rankingImagePath)),
      textGeneration: {
        claudePreset: textConfig.claudePreset,
        geminiModel: textConfig.geminiModel,
        opencodeAgent: textConfig.opencodeAgent,
        ollamaBaseUrl: textConfig.ollamaBaseUrl,
      },
    }, null, 2), 'utf-8');
  } catch (err) {
    console.log(`[ranking_comment] debug snapshot error: ${err.message}`);
  }
}

/**
 * 生の応答テキストからコメント本文のみを抽出
 * AI が分析テキストを付けてしまった場合に最後の短い文を取る
 */
function extractCommentOnly(raw, tag = 'comment') {
  if (!raw) return null;
  const lines = stripAnsi(raw)
    .split('\n')
    .map(line => line.trim())
    .filter(Boolean)
    .filter(line => !line.startsWith('```'))
    .filter(line => !META_LINE_PATTERNS.some(pattern => pattern.test(line)));

  const candidates = [];
  const plainLines = lines.filter(line =>
    !line.startsWith('-') &&
    !line.startsWith('*') &&
    !line.startsWith('#') &&
    !line.match(/^\d+\./) &&
    !line.startsWith('|') &&
    line.length >= 3
  );
  if (plainLines.length > 0) {
    candidates.push(plainLines.join(' '));
  }

  const joined = lines.join(' ');
  if (joined) {
    candidates.push(joined);
    const tailSentences = splitSentences(joined).slice(-6).join('');
    if (tailSentences) candidates.push(tailSentences);
  }

  const seen = new Set();
  for (const candidate of candidates) {
    const cleaned = stripMetaPreamble(
      candidate
        .replace(/\*\*/g, '')
        .replace(/[✓]/gu, ' ')
        .replace(/\s+/g, ' ')
        .replace(/^[「『]|[」』]$/g, '')
        .trim(),
    );
    if (!cleaned || seen.has(cleaned)) continue;
    seen.add(cleaned);
    if (isValidGeneratedComment(cleaned)) {
      return cleaned.slice(0, 350);
    }
  }

  const preview = stripAnsi(raw).replace(/\s+/g, ' ').slice(0, 220);
  console.log(`[${tag}] rejected generated comment preview: ${preview}`);
  return null;
}

async function buildMidgameScreenshotTextInfo(screenshotPath) {
  if (!screenshotPath || !existsSync(screenshotPath)) {
    return '（OCR補助メモなし）';
  }

  try {
    const ocr = await analyzeGameplayScreenshotText(screenshotPath);
    if (ocr?.lines?.length) {
      return ocr.lines.slice(0, 8).map(line => `- ${line}`).join('\n');
    }
  } catch {
    return '（OCR補助メモなし）';
  }

  return '（OCR補助メモなし）';
}

async function buildRankingTextPrompt(rankingImagePath, myRank) {
  let ocrInfo = '- ランキング画面の文字情報はありません。';
  let effectiveRank = myRank != null ? Number(myRank) : null;
  let hasOcrContext = false;
  
  if (rankingImagePath) {
    try {
      const ocr = await analyzeResultScreen(rankingImagePath);
      const lines = [];
      if (ocr?.rank != null) {
        lines.push(`- OCR推定順位: ${ocr.rank}位/91人中。`);
        if (effectiveRank == null) effectiveRank = Number(ocr.rank);
      }
      if (ocr?.playerNames?.length) {
        lines.push(`- OCRプレイヤー名候補: ${ocr.playerNames.slice(0, 8).join(' / ')}`);
      }
      if (ocr?.lines?.length) {
        lines.push(...ocr.lines.slice(0, 8).map(line => `- ${line}`));
      }
      if (lines.length > 0) {
        ocrInfo = lines.join('\n');
        hasOcrContext = true;
      }
    } catch {
      ocrInfo = '- ランキング画面の文字情報はありません。';
    }
  }
  
  return {
    promptText: loadPrompt('ranking_comment.md', {
      rankInfo: effectiveRank != null ? `自分の順位: ${effectiveRank}位/91人中。` : '自分の順位: 不明。順位を断定してはいけない。',
      ocrInfo,
    }),
    effectiveRank,
    hasUsableContext: effectiveRank != null || hasOcrContext,
  };
}

function callClaudeForComment(promptText) {
  return generateTextWithFallbacks('ranking_comment', promptText, {
    claudePreset: 'haiku',
    claudeFallbackPreset: 'qwen35e',
    parseOutput: raw => extractCommentOnly(raw, 'ranking_comment'),
    includeOpencodeFallback: true,
  });
}

/**
 * TTS読み上げ (親プロジェクトの comment queue にテキストを積み、audio_worker に再生させる)
 */
function speakComment(comment, contextLabel = 'soren91:comment') {
  const queueDir = join(PARENT_DIR, 'tmp', '.comment_queue');
  try {
    mkdirSync(queueDir, { recursive: true });
    const ts = Date.now();
    const filename = `comment_soren91_${ts}_${contextLabel.replace(/[^a-zA-Z0-9_]/g, '_')}.txt`;
    const tmpFile = join(queueDir, `.${filename}.tmp`);
    const metaFile = join(queueDir, filename.replace(/\.txt$/u, '.meta.json'));
    const metaTmpFile = join(queueDir, `.${filename}.meta.tmp`);
    const speakerFile = `${join(queueDir, filename)}.speaker`;
    const speakerTmpFile = join(queueDir, `.${filename}.speaker.tmp`);
    const destFile = join(queueDir, filename);
    const voicevoxSpeaker = resolveSoren91VoicevoxSpeaker();
    writeFileSync(tmpFile, comment + '\n');
    writeFileSync(metaTmpFile, JSON.stringify({
      generatedAt: new Date().toISOString(),
      source: 'soren91',
      mode: 'soren91',
      contextLabel,
      speaker: voicevoxSpeaker,
      chars: comment.length,
    }, null, 2) + '\n');
    writeFileSync(speakerTmpFile, voicevoxSpeaker);
    renameSync(metaTmpFile, metaFile);
    renameSync(speakerTmpFile, speakerFile);
    renameSync(tmpFile, destFile);
    console.log(`[ranking_comment] queued: ${filename}`);
  } catch (err) {
    console.log(`[ranking_comment] queue error: ${err.message}`);
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

function bucketForPieceType(type) {
  const n = Number(type || 1);
  if (n <= 4) return 'small';
  if (n <= 9) return 'medium';
  return 'large';
}

function summarizePieceBuckets(pieces) {
  const counts = { small: 0, medium: 0, large: 0 };
  for (const piece of pieces) {
    counts[bucketForPieceType(piece?.type)] += 1;
  }
  const total = counts.small + counts.medium + counts.large;
  if (total === 0) return '種類傾向: 判定材料不足';

  if (counts.small >= total * 0.75) return '種類傾向: 小さいピースにかなり偏って見える';
  if (counts.small >= total * 0.55) return '種類傾向: 小さいピースが多めに見える';
  if (counts.large >= Math.max(2, total * 0.18)) return '種類傾向: 大きめのピースも少し育っている';
  if (counts.medium >= total * 0.45) return '種類傾向: 中くらいのピースが混ざっている';
  return '種類傾向: 小型と中型が混在している';
}

function summarizeUpcomingPieces(nextPieces) {
  const detected = (nextPieces || []).filter(piece => piece && piece.fallback !== true);
  if (detected.length === 0) return 'NEXT傾向: 判定あいまい';

  const buckets = detected.map(piece => bucketForPieceType(piece.type));
  if (buckets.every(bucket => bucket === 'small')) return 'NEXT傾向: 小さいピース寄り';
  if (buckets.some(bucket => bucket === 'large')) return 'NEXT傾向: 大きめ候補が混ざる';
  if (buckets.every(bucket => bucket === 'medium')) return 'NEXT傾向: 中くらいが続きそう';
  return 'NEXT傾向: 小型と中型が混在';
}

function summarizeHoldPiece(hold) {
  if (!hold) return 'HOLD: なし';
  if (hold.fallback === true) return 'HOLD: 判定あいまい';
  const bucket = bucketForPieceType(hold.type);
  if (bucket === 'small') return 'HOLD: 小さいピースあり';
  if (bucket === 'medium') return 'HOLD: 中くらいのピースあり';
  return 'HOLD: 大きめのピースあり';
}

/**
 * 試合中の盤面コメントを生成 (1試合1回)
 * @param {number} gameNumber - ゲーム番号
 * @param {number} turn - 現在のターン数
 * @param {object} boardState - 盤面状態
 * @param {string} [_screenshotPath] - 後方互換のため残す未使用引数
 */
export async function generateMidgameComment(gameNumber, turn, boardState, screenshotPath) {
  if (!boardState || !boardState.pieces || boardState.pieces.length === 0) {
    console.log('[midgame_comment] No board state or empty pieces');
    return null;
  }

  try {
    const comment = await callClaudeForMidgame(gameNumber, turn, boardState, screenshotPath);
    let finalComment = comment;
    if (!finalComment || !isGroundedMidgameComment(finalComment)) {
      console.log('[midgame_comment] Ungrounded generated comment, using fallback');
      finalComment = fallbackMidgameComment(boardState, turn);
    }

    console.log(`[midgame_comment] Generated: ${finalComment}`);

    const logLine = `[${new Date().toISOString()}] game=#${gameNumber} turn=${turn}: ${finalComment}\n`;
    try { writeFileSync(COMMENT_LOG_PATH, logLine, { flag: 'a' }); } catch {}

    speakComment(finalComment, 'soren91:midgame_comment');

    return finalComment;
  } catch (err) {
    console.error(`[midgame_comment] Error: ${err.message}`);
    return null;
  }
}

function fallbackMidgameComment(boardState, turn) {
  const rawPieces = boardState?.pieces ?? [];
  const validPieces = rawPieces.filter(piece => piece && piece.fallback !== true);
  const maxY = validPieces.length > 0
    ? Math.max(...validPieces.map(piece => (piece.y ?? -5) + (piece.r ?? 0)))
    : -5;
  const dangerLevel = maxY >= 3.0 ? 'かなり危険'
    : maxY >= 2.4 ? '危険が迫っている'
    : maxY >= 1.6 ? '少し高くなってきた'
    : 'まだ余裕があります';
  const pieceTone = summarizePieceBuckets(validPieces).replace(/^種類傾向:\s*/u, '');
  const holdTone = summarizeHoldPiece(boardState?.hold).replace(/^HOLD:\s*/u, '');
  const nextTone = summarizeUpcomingPieces(boardState?.nextPieces).replace(/^NEXT傾向:\s*/u, '');
  const pieceSentence = pieceTone === '判定材料不足'
    ? 'ピースの傾向はまだ判定材料不足です。'
    : `ピースの傾向は${pieceTone}ので、ここは大きなことを断定せずに丁寧に育てたいですね。`;

  return `ターン${turn}の盤面は、積み上がりが「${dangerLevel}」という見え方です。${pieceSentence}HOLDは${holdTone}、NEXTは${nextTone}です。資本主義らしく、見えている材料だけで冷静に配置していきます。`;
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

  // 状況を自然言語で表現（数値を直接見せない）
  const fillLevel = fillPct < 20 ? 'スカスカ' : fillPct < 40 ? 'まだ余裕あり' : fillPct < 60 ? 'そこそこ埋まっている' : fillPct < 80 ? 'かなり埋まっている' : 'ほぼ満杯';
  const dangerLevel = dangerPct <= 0 ? '安全' : dangerPct < 30 ? 'まだ余裕あり' : dangerPct < 50 ? 'やや高くなってきた' : dangerPct < 70 ? '危険が迫っている' : dangerPct < 90 ? 'かなり危険' : '瀕死';

  let info = `ターン${turn}、盤面にピース${pieceCount}個。`;
  info += `\n盤面の状態: ${fillLevel}`;
  info += `\n積み上がり: ${dangerLevel}`;
  info += `\n${summarizePieceBuckets(pieces)}`;
  info += `\n種類推定の注意: 小さいピース側に誤認しやすいので、特定の国名や個数の断定は禁止`;

  if (garbageRatio > 0.05) {
    info += `\nおじゃまブロック: ${(garbageRatio * 100).toFixed(0)}%、ゲージ: ${(gauge * 100).toFixed(0)}%`;
  }
  info += `\n${summarizeHoldPiece(hold)}`;
  info += `\n${summarizeUpcomingPieces(nextPieces)}`;

  return info;
}

async function callClaudeForMidgame(gameNumber, turn, boardState, screenshotPath) {
  const boardInfo = formatBoardStateForPrompt(boardState, turn);
  const screenTextInfo = await buildMidgameScreenshotTextInfo(screenshotPath);
  const promptText = loadPrompt('midgame_comment.md', { boardInfo, screenTextInfo });

  return generateTextWithFallbacks('midgame_comment', promptText, {
    claudePreset: 'haiku',
    claudeFallbackPreset: 'qwen35e',
    parseOutput: raw => extractCommentOnly(raw, 'midgame_comment'),
    includeOpencodeFallback: true,
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
