#!/usr/bin/env node
// tests/test_kick_chat_daemon.mjs - kick_chat_daemon.mjs の受信処理を、偽の Pusher
// サーバに繋いで検証する。外部ネットワークには出ない (chatroom は env で固定)。

import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import fs from 'node:fs';
import http from 'node:http';
import os from 'node:os';
import path from 'node:path';
import crypto from 'node:crypto';

const REPO_ROOT = path.resolve(import.meta.dirname, '..');
const WORK_DIR = fs.mkdtempSync(path.join(os.tmpdir(), 'kick_daemon_test-'));
const CHAT_DIR = path.join(WORK_DIR, '.kick_chat');
const RAW_LOG = path.join(CHAT_DIR, 'raw.log');

// --- 最小の Pusher 互換 WebSocket サーバ (handshake + フレーム送出のみ) ---
function acceptKey(key) {
  return crypto
    .createHash('sha1')
    .update(key + '258EAFA5-E914-47DA-95CA-C5AB0DC85B11')
    .digest('base64');
}

function encodeTextFrame(text) {
  const payload = Buffer.from(text, 'utf8');
  const len = payload.length;
  let header;
  if (len < 126) {
    header = Buffer.from([0x81, len]);
  } else {
    header = Buffer.alloc(4);
    header[0] = 0x81;
    header[1] = 126;
    header.writeUInt16BE(len, 2);
  }
  return Buffer.concat([header, payload]);
}

const received = [];
const server = http.createServer();
let socket = null;
server.on('upgrade', (req, sock) => {
  socket = sock;
  sock.write(
    'HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n' +
      `Sec-WebSocket-Accept: ${acceptKey(req.headers['sec-websocket-key'])}\r\n\r\n`,
  );
  sock.on('data', () => received.push('frame'));
  sock.write(
    encodeTextFrame(
      JSON.stringify({
        event: 'pusher:connection_established',
        data: JSON.stringify({ socket_id: '1.1', activity_timeout: 120 }),
      }),
    ),
  );
});

function chat(id, username, slug, content) {
  return encodeTextFrame(
    JSON.stringify({
      event: 'App\\Events\\ChatMessageEvent',
      channel: 'chatrooms.999.v2',
      data: JSON.stringify({ id, content, sender: { id: `uid-${slug}`, username, slug } }),
    }),
  );
}

const port = await new Promise((resolve) => {
  server.listen(0, '127.0.0.1', () => resolve(server.address().port));
});

const child = spawn('node', [path.join(REPO_ROOT, 'kick_chat_daemon.mjs'), 'testslug'], {
  cwd: REPO_ROOT,
  env: {
    ...process.env,
    KICK_CHAT_DIR: CHAT_DIR,
    KICK_CHATROOM_ID: '999',
    KICK_PUSHER_URL_OVERRIDE: `ws://127.0.0.1:${port}/`,
    CHAT_INGEST_OVERLAY_NOTIFY: '0',
  },
  stdio: ['ignore', 'ignore', 'ignore'],
});

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const waitFor = async (predicate, timeoutMs = 8000) => {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (predicate()) return true;
    await sleep(100);
  }
  return false;
};

const rawLines = () => {
  try {
    return fs.readFileSync(RAW_LOG, 'utf8').split('\n').filter(Boolean);
  } catch {
    return [];
  }
};

try {
  assert.ok(await waitFor(() => socket !== null), 'daemon should connect to the pusher endpoint');
  await sleep(300);

  socket.write(chat('m1', 'viewer1', 'viewer1', 'hello [emote:37226:KEKW] world'));
  // Nothing is sent to Kick, so the channel's own account is the broadcaster
  // commenting, not an echo. Dropping it lost real comments on 2026-08-26.
  socket.write(chat('m2', 'DoCiAI', 'dociai', 'broadcaster comment'));
  socket.write(chat('m1', 'viewer1', 'viewer1', 'hello [emote:37226:KEKW] world'));
  socket.write(chat('m4', 'viewer2', 'viewer2', 'back`tick $(x) ;rm  spaced'));
  // 2026-09-17 に実測した広告スパム。受信段階で落ること。
  socket.write(chat('m5', 'spam1', 'spam1', 'Kick viewbot, follower bot chat bot and more. Save 99% vs typical panels!'));
  socket.write(chat('m6', 'spam2', 'spam2', 'Ad d me on d1s cord'));
  // 難読化なしの普通の英文や URL はスパム判定しない。
  socket.write(chat('m7', 'viewer3', 'viewer3', 'nice stream, check https://example.com/video'));

  socket.write(chat('m8', 'viewer4', 'viewer4', 'Does this stream have a Discord server?'));
  socket.write(chat('m9', 'viewer5', 'viewer5', 'Discordでゲームの攻略を話しています'));
  socket.write(chat('m10', 'viewer6', 'viewer6', 'These viewbot ads are annoying, please block them.'));

  assert.ok(await waitFor(() => rawLines().some((line) => line.startsWith('id=m10\t'))), 'normal discussion of Discord and spam should be received');
  await sleep(500);
  const lines = rawLines();

  assert.equal(lines.length, 7, `expected exactly 7 kept lines, got ${lines.length}: ${JSON.stringify(lines)}`);
  assert.equal(
    lines[0],
    'id=m1\tuser-id=uid-viewer1\tlogin=viewer1\tdisplay=viewer1\tflags=\tviewer1: hello KEKW world',
    'emote markup becomes its bare name and stable identity stays on the same row',
  );
  assert.equal(
    lines[1],
    'id=m2\tuser-id=uid-dociai\tlogin=dociai\tdisplay=DoCiAI\tflags=\tDoCiAI: broadcaster comment',
    "the channel's own account is kept by default",
  );
  assert.equal(
    lines[2],
    'id=m4\tuser-id=uid-viewer2\tlogin=viewer2\tdisplay=viewer2\tflags=\tviewer2: backtick (x) rm spaced',
    'shell metacharacters are stripped and whitespace collapsed',
  );
  assert.equal(lines[3].split('\t').pop(), 'viewer3: nice stream, check https://example.com/video');
  assert.ok(lines.some((l) => l.endsWith('viewer4: Does this stream have a Discord server?')), 'plain Discord question is kept');
  assert.ok(lines.some((l) => l.endsWith('viewer5: Discordでゲームの攻略を話しています')), 'Japanese Discord mention is kept');
  assert.ok(lines.some((l) => l.endsWith('viewer6: These viewbot ads are annoying, please block them.')), 'complaining about viewbot ads is kept');

  // スパム2件は raw.log に現れず、daemon 自身の判断ログに記録される。
  const daemonLog = fs.readFileSync(path.join(CHAT_DIR, 'daemon.log'), 'utf8');
  assert.ok(/spam dropped \(author=spam1, reason=known-ad-template\)/.test(daemonLog), 'viewbot ad is dropped at ingest with a reason log');
  assert.ok(/spam dropped \(author=spam2, reason=known-ad-template\)/.test(daemonLog), 'obfuscated discord lure is dropped at ingest');
  assert.equal(daemonLog.match(/spam dropped/g)?.length ?? 0, 2, 'exactly the two known ad templates are dropped');

  console.log('kick_chat_daemon: all checks passed');
} finally {
  child.kill('SIGTERM');
  server.close();
  try {
    socket?.destroy();
  } catch {
    /* already gone */
  }
  fs.rmSync(WORK_DIR, { recursive: true, force: true });
}
