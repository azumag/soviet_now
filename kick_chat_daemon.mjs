#!/usr/bin/env node
// kick_chat_daemon.mjs - Kick チャット常駐プロセス (kick_chat.sh から起動される)
//
// Kick は Twitch の IRC に相当する公開エンドポイントを持たないが、web クライアントが
// 使う Pusher チャンネル (chatrooms.<chatroom_id>.v2) は匿名で購読できる。ここでは
// 読み取り専用で購読し、受信したコメントを raw.log へ 1 行ずつ追記する。
// message/user ID と本文は同じ物理行に保存し、表示名だけで本人判定しない。
//
// 使い方: node kick_chat_daemon.mjs <slug>
// 環境変数: KICK_CHATROOM_ID / KICK_CHAT_DIR / KICK_IGNORE_AUTHORS ほか

import fs from 'node:fs';
import path from 'node:path';

const SLUG = (process.argv[2] || process.env.KICK_CHANNEL || 'dociai').trim();
const CHAT_DIR = process.env.KICK_CHAT_DIR || 'tmp/.kick_chat';
const RAW_LOG = path.join(CHAT_DIR, 'raw.log');
const RECENT_IDS_FILE = path.join(CHAT_DIR, 'recent_msg_ids.log');
const DAEMON_LOG = path.join(CHAT_DIR, 'daemon.log');
const RECONNECT_LOG = path.join(CHAT_DIR, 'daemon_reconnect.log');
const STATE_FILE = path.join(CHAT_DIR, 'daemon_state.json');

// Kick web クライアントが使う公開 Pusher アプリ。認証なしで購読できる読み取り専用。
// テストは KICK_PUSHER_URL_OVERRIDE でローカルの偽サーバへ向ける。
const PUSHER_URL =
  process.env.KICK_PUSHER_URL_OVERRIDE ||
  'wss://ws-us2.pusher.com/app/32cbd69e4b950bf97679?protocol=7&client=js&version=8.4.0-rc2&flash=false';
const KICK_API = 'https://kick.com/api/v2/channels/';
const BROWSER_UA =
  'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36';

const RECENT_ID_TTL_SEC = intEnv('KICK_RECENT_DEDUP_TTL_SEC', 900);
const RECENT_ID_MAX = intEnv('KICK_RECENT_DEDUP_MAX', 4000);
const MAX_MESSAGE_CHARS = intEnv('KICK_CHAT_MAX_MESSAGE_CHARS', 400);
// fetch が来ないまま raw.log が無限に伸びるのを防ぐ上限 (fetch 側でも切り詰める)。
const RAW_LOG_MAX_LINES = intEnv('KICK_RAW_LOG_MAX_LINES', 2000);
const OVERLAY_NOTIFY = (process.env.CHAT_INGEST_OVERLAY_NOTIFY ?? '1') === '1';
// 既定は空。Twitch/YouTube と違い Kick へは何も送信していないので、自チャンネル
// (dociai) の投稿もエコーではなく配信者本人のコメントである。Kick 送信を実装したら
// そのアカウント名をここに入れないと自分の返答を読み返す。
const IGNORE_AUTHORS = (process.env.KICK_IGNORE_AUTHORS ?? '')
  .split(/\s+/)
  .filter(Boolean)
  .map((s) => s.toLowerCase());

function intEnv(name, fallback) {
  const raw = process.env[name];
  if (!raw || !/^\d+$/.test(raw)) return fallback;
  return Number(raw);
}

function log(msg) {
  const line = `[kick_chat_daemon ${new Date().toISOString()}] ${msg}\n`;
  try {
    fs.appendFileSync(DAEMON_LOG, line);
  } catch {
    /* daemon log is best effort */
  }
  process.stderr.write(line);
}

function noteReconnect(msg) {
  try {
    fs.appendFileSync(RECONNECT_LOG, `[${new Date().toISOString()}] ${msg}\n`);
  } catch {
    /* best effort */
  }
}

// --- 直近 msg-id の TTL つき重複排除 (twitch_chat_daemon.sh と同じ `ts|key` 形式) ---
function compactRecentIds() {
  let text = '';
  try {
    text = fs.readFileSync(RECENT_IDS_FILE, 'utf8');
  } catch {
    return;
  }
  const now = Math.floor(Date.now() / 1000);
  const seen = new Set();
  const kept = [];
  for (const line of text.split('\n')) {
    const [ts, key] = line.split('|');
    if (!key || !/^\d+$/.test(ts)) continue;
    if (now - Number(ts) > RECENT_ID_TTL_SEC) continue;
    if (seen.has(key)) continue;
    seen.add(key);
    kept.push(`${ts}|${key}`);
  }
  try {
    fs.writeFileSync(RECENT_IDS_FILE, kept.slice(-RECENT_ID_MAX).join('\n') + (kept.length ? '\n' : ''));
  } catch {
    /* best effort */
  }
}

function recentIdSeen(id) {
  if (!id) return false;
  let text = '';
  try {
    text = fs.readFileSync(RECENT_IDS_FILE, 'utf8');
  } catch {
    return false;
  }
  const now = Math.floor(Date.now() / 1000);
  for (const line of text.split('\n')) {
    const [ts, key] = line.split('|');
    if (key === id && /^\d+$/.test(ts) && now - Number(ts) <= RECENT_ID_TTL_SEC) return true;
  }
  return false;
}

function markRecentId(id) {
  if (!id) return;
  try {
    fs.appendFileSync(RECENT_IDS_FILE, `${Math.floor(Date.now() / 1000)}|${id}\n`);
  } catch {
    /* best effort */
  }
}

// Kick のエモートは `[emote:37226:KEKW]` で届く。Twitch は素の名前で届くので、
// 名前だけ残して同じ見え方に揃える。
function sanitizeMessage(text) {
  let out = String(text ?? '');
  out = out.replace(/\[emote:\d+:([^\]]*)\]/g, ' $1 ');
  out = out.replace(/[`$\\{}|;<>&]/g, '');
  out = out.replace(/[\r\n\t]+/g, ' ');
  out = out.replace(/\s+/g, ' ').trim();
  if (out.length > MAX_MESSAGE_CHARS) out = out.slice(0, MAX_MESSAGE_CHARS);
  return out;
}

function sanitizeUser(name) {
  return String(name ?? '')
    .replace(/[`$\\{}|;<>&:]/g, '')
    .replace(/\s+/g, ' ')
    .trim();
}

function sanitizeMetadataToken(value, max = 160) {
  return String(value ?? '')
    .replace(/[^0-9A-Za-z_.:@-]/g, '')
    .slice(0, max);
}

function isIgnoredAuthor(username, slug) {
  const candidates = [username, slug].filter(Boolean).map((s) => String(s).toLowerCase());
  return candidates.some((c) => IGNORE_AUTHORS.includes(c));
}

function trimRawLog() {
  let text = '';
  try {
    text = fs.readFileSync(RAW_LOG, 'utf8');
  } catch {
    return;
  }
  const lines = text.split('\n').filter((l) => l.length > 0);
  if (lines.length <= RAW_LOG_MAX_LINES) return;
  const kept = lines.slice(-RAW_LOG_MAX_LINES);
  try {
    fs.writeFileSync(RAW_LOG, kept.join('\n') + '\n');
    log(`raw.log を ${lines.length} 行から ${kept.length} 行へ切り詰め (fetch 滞留)`);
  } catch {
    /* best effort */
  }
}

function appendComment(msgId, stableId, login, displayName, line) {
  fs.appendFileSync(
    RAW_LOG,
    `id=${sanitizeMetadataToken(msgId)}\tuser-id=${sanitizeMetadataToken(stableId)}\tlogin=${sanitizeMetadataToken(login, 80)}\tdisplay=${String(displayName ?? '').replace(/[\t\r\n]/g, ' ').trim()}\tflags=\t${line}\n`,
  );
  trimRawLog();
}

async function notifyOverlay(line) {
  if (!OVERLAY_NOTIFY) return;
  try {
    const { execFile } = await import('node:child_process');
    execFile('./overlay_notify.sh', ['chat', 'Kick コメント受信', line, 'info'], { timeout: 5000 }, () => {});
  } catch {
    /* overlay notification is best effort */
  }
}

function writeState(extra) {
  try {
    fs.writeFileSync(
      STATE_FILE,
      JSON.stringify({ slug: SLUG, pid: process.pid, updated_at: new Date().toISOString(), ...extra }, null, 1) + '\n',
    );
  } catch {
    /* best effort */
  }
}

async function resolveChatroomId() {
  const configured = process.env.KICK_CHATROOM_ID;
  if (configured && /^\d+$/.test(configured)) return Number(configured);
  const res = await fetch(`${KICK_API}${encodeURIComponent(SLUG)}`, {
    headers: { Accept: 'application/json', 'User-Agent': BROWSER_UA },
    signal: AbortSignal.timeout(15000),
  });
  if (!res.ok) throw new Error(`channel lookup failed: HTTP ${res.status}`);
  const body = await res.json();
  const id = body?.chatroom?.id;
  if (!id) throw new Error('channel lookup returned no chatroom id');
  return Number(id);
}

let stopping = false;
for (const sig of ['SIGINT', 'SIGTERM', 'SIGHUP']) {
  process.on(sig, () => {
    stopping = true;
    log(`${sig} 受信 → 終了`);
    process.exit(0);
  });
}

function connectOnce(chatroomId) {
  return new Promise((resolve) => {
    const ws = new WebSocket(PUSHER_URL);
    let settled = false;
    let established = false;
    let activityTimeoutMs = 120000;
    let pingTimer = null;
    let idleTimer = null;

    const finish = (reason) => {
      if (settled) return;
      settled = true;
      clearInterval(pingTimer);
      clearTimeout(idleTimer);
      try {
        ws.close();
      } catch {
        /* already closing */
      }
      resolve({ reason, established });
    };

    // 無通信が activity_timeout を大きく超えたら、TCP が生きていても接続を捨てる。
    const armIdleTimer = () => {
      clearTimeout(idleTimer);
      idleTimer = setTimeout(() => finish('idle-timeout'), activityTimeoutMs + 30000);
    };

    ws.onopen = () => armIdleTimer();
    ws.onerror = (e) => finish(`ws-error: ${e?.message || 'unknown'}`);
    ws.onclose = (e) => finish(`ws-close: ${e?.code ?? ''} ${e?.reason ?? ''}`.trim());

    ws.onmessage = (raw) => {
      armIdleTimer();
      let frame;
      try {
        frame = JSON.parse(raw.data);
      } catch {
        return;
      }

      if (frame.event === 'pusher:connection_established') {
        let info = {};
        try {
          info = JSON.parse(frame.data);
        } catch {
          /* keep defaults */
        }
        established = true;
        if (Number(info.activity_timeout) > 0) activityTimeoutMs = Number(info.activity_timeout) * 1000;
        armIdleTimer();
        ws.send(JSON.stringify({ event: 'pusher:subscribe', data: { auth: '', channel: `chatrooms.${chatroomId}.v2` } }));
        clearInterval(pingTimer);
        pingTimer = setInterval(() => {
          try {
            ws.send(JSON.stringify({ event: 'pusher:ping', data: {} }));
          } catch {
            finish('ping-send-failed');
          }
        }, Math.max(30000, Math.floor(activityTimeoutMs / 2)));
        log(`connected (chatroom=${chatroomId}, socket=${info.socket_id ?? '?'})`);
        writeState({ chatroom_id: chatroomId, state: 'connected', socket_id: info.socket_id ?? null });
        return;
      }
      if (frame.event === 'pusher:ping') {
        try {
          ws.send(JSON.stringify({ event: 'pusher:pong', data: {} }));
        } catch {
          finish('pong-send-failed');
        }
        return;
      }
      if (frame.event === 'pusher:error') {
        log(`pusher error: ${typeof frame.data === 'string' ? frame.data : JSON.stringify(frame.data)}`);
        return;
      }
      if (!String(frame.event).includes('ChatMessage')) return;

      let payload;
      try {
        payload = typeof frame.data === 'string' ? JSON.parse(frame.data) : frame.data;
      } catch {
        return;
      }
      const msgId = String(payload?.id ?? '').replace(/[^A-Za-z0-9-]/g, '');
      const username = sanitizeUser(payload?.sender?.username);
      const senderSlug = String(payload?.sender?.slug ?? '');
      const stableUserId = payload?.sender?.id ?? payload?.sender?.user_id ?? '';
      const message = sanitizeMessage(payload?.content);
      if (!message || !username) return;
      if (isIgnoredAuthor(payload?.sender?.username, senderSlug)) return;

      compactRecentIds();
      if (msgId && recentIdSeen(msgId)) return;

      const line = `${username}: ${message}`;
      try {
        appendComment(msgId || `nid-${Date.now()}`, stableUserId, senderSlug, username, line);
      } catch (err) {
        log(`raw.log 追記に失敗: ${err?.message || err}`);
        return;
      }
      if (msgId) markRecentId(msgId);
      notifyOverlay(line);
    };
  });
}

async function main() {
  fs.mkdirSync(CHAT_DIR, { recursive: true });
  if (!fs.existsSync(RAW_LOG)) fs.writeFileSync(RAW_LOG, '');

  let chatroomId = null;
  let backoffSec = 2;
  log(`起動 (slug=${SLUG}, pid=${process.pid}, chat_dir=${CHAT_DIR})`);

  while (!stopping) {
    if (chatroomId === null) {
      try {
        chatroomId = await resolveChatroomId();
        log(`chatroom_id=${chatroomId} を解決`);
      } catch (err) {
        log(`chatroom 解決に失敗: ${err?.message || err} → ${backoffSec}s 後に再試行`);
        writeState({ state: 'resolve-failed', error: String(err?.message || err) });
        await new Promise((r) => setTimeout(r, backoffSec * 1000));
        backoffSec = Math.min(backoffSec * 2, 60);
        continue;
      }
    }

    const { reason, established } = await connectOnce(chatroomId);
    if (stopping) break;
    // 一度でも購読できた接続の切断は単発の事故として扱い、待ち時間を短く戻す。
    if (established) backoffSec = 2;
    noteReconnect(`disconnected (${reason}) → ${backoffSec}s 後に再接続`);
    writeState({ chatroom_id: chatroomId, state: 'reconnecting', last_disconnect_reason: reason });
    await new Promise((r) => setTimeout(r, backoffSec * 1000));
    backoffSec = Math.min(backoffSec * 2, 60);
    // 解決済み chatroom_id は使い回すが、連続失敗時は取り直して chatroom 変更に追従する。
    if (backoffSec >= 60) chatroomId = null;
  }
}

main().catch((err) => {
  log(`fatal: ${err?.stack || err}`);
  process.exit(1);
});
