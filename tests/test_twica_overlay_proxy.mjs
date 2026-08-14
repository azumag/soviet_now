import { test } from 'node:test';
import assert from 'node:assert/strict';
import http from 'node:http';
import net from 'node:net';
import crypto from 'node:crypto';
import {
  rewriteTwicaOverlayUrl,
  startTwicaOverlayProxy,
} from '../lib/twica_overlay_proxy.mjs';

const WS_GUID = '258EAFA5-E914-47DA-95CA-C5AB0DC85B11';

function listen(server) {
  return new Promise((resolve, reject) => {
    server.once('error', reject);
    server.listen(0, '127.0.0.1', () => {
      resolve(server.address().port);
    });
  });
}

async function freePort() {
  const srv = http.createServer();
  const port = await listen(srv);
  await new Promise((resolve) => srv.close(resolve));
  return port;
}

test('rewriteTwicaOverlayUrl maps https public URL to local proxy preserving path and query', () => {
  assert.equal(
    rewriteTwicaOverlayUrl('https://twica.bluemoon.works/overlay/d1?pName=true', 18080),
    'http://127.0.0.1:18080/overlay/d1?pName=true',
  );
  assert.equal(rewriteTwicaOverlayUrl('', 18080), '');
});

test('proxy forwards path/query and strips X-Frame-Options and CSP', async () => {
  let seenPath = '';
  let seenSearch = '';
  let seenMethod = '';
  const upstream = http.createServer((req, res) => {
    seenPath = req.url.split('?')[0];
    seenSearch = req.url.split('?')[1] || '';
    seenMethod = req.method;
    res.writeHead(200, {
      'Content-Type': 'text/html; charset=utf-8',
      'X-Frame-Options': 'SAMEORIGIN',
      'Content-Security-Policy': "frame-ancestors 'self'",
      'Strict-Transport-Security': 'max-age=31536000',
      'X-Custom': 'kept',
    });
    res.end('<html>ok</html>');
  });
  const upstreamPort = await listen(upstream);
  const proxy = await startTwicaOverlayProxy({
    port: await freePort(),
    upstream: `http://127.0.0.1:${upstreamPort}`,
    log: { log() {}, warn() {} },
  });
  const proxyPort = proxy.address().port;

  try {
    const res = await new Promise((resolve, reject) => {
      const preq = http.request(
        {
          host: '127.0.0.1',
          port: proxyPort,
          path: '/overlay/demo?pName=true',
          method: 'GET',
          headers: { Connection: 'close' },
          agent: false,
        },
        (pres) => {
          const chunks = [];
          pres.on('data', (c) => chunks.push(c));
          pres.on('end', () => {
            resolve({
              statusCode: pres.statusCode,
              headers: pres.headers,
              body: Buffer.concat(chunks).toString('utf8'),
            });
          });
        },
      );
      preq.on('error', reject);
      preq.end();
    });
    assert.equal(res.statusCode, 200);
    assert.equal(res.body, '<html>ok</html>');
    assert.equal(seenPath, '/overlay/demo');
    assert.equal(seenSearch, 'pName=true');
    assert.equal(seenMethod, 'GET');
    assert.equal(res.headers['x-frame-options'], undefined);
    assert.equal(res.headers['content-security-policy'], undefined);
    assert.equal(res.headers['strict-transport-security'], undefined);
    assert.equal(res.headers['x-custom'], 'kept');
    assert.match(res.headers['content-type'], /text\/html/);
  } finally {
    proxy.closeAllConnections?.();
    upstream.closeAllConnections?.();
    proxy.close();
    upstream.close();
  }
});

test('proxy relays WebSocket upgrade and echoes frames', async () => {
  const upstream = http.createServer();
  const upstreamSockets = new Set();
  upstream.on('upgrade', (req, socket) => {
    upstreamSockets.add(socket);
    socket.on('close', () => upstreamSockets.delete(socket));
    const accept = crypto
      .createHash('sha1')
      .update(`${req.headers['sec-websocket-key']}${WS_GUID}`)
      .digest('base64');
    socket.write(
      'HTTP/1.1 101 Switching Protocols\r\n'
      + 'Upgrade: websocket\r\n'
      + 'Connection: Upgrade\r\n'
      + `Sec-WebSocket-Accept: ${accept}\r\n\r\n`,
    );
    socket.on('data', (buf) => {
      // unmask the client frame and echo it back unmasked
      const b0 = buf[0];
      const opcode = b0 & 0x0f;
      const b1 = buf[1];
      const masked = (b1 & 0x80) !== 0;
      let len = b1 & 0x7f;
      let offset = 2;
      if (len === 126) {
        len = buf.readUInt16BE(2);
        offset = 4;
      } else if (len === 127) {
        len = Number(buf.readBigUInt64BE(2));
        offset = 10;
      }
      const mask = masked ? buf.subarray(offset, offset + 4) : null;
      offset += masked ? 4 : 0;
      const payload = Buffer.from(buf.subarray(offset, offset + len));
      if (mask) {
        for (let i = 0; i < payload.length; i += 1) payload[i] ^= mask[i % 4];
      }
      const header = Buffer.alloc(2);
      header[0] = 0x80 | opcode;
      header[1] = payload.length;
      socket.write(Buffer.concat([header, payload]));
    });
  });
  const upstreamPort = await listen(upstream);
  const proxy = await startTwicaOverlayProxy({
    port: await freePort(),
    upstream: `http://127.0.0.1:${upstreamPort}`,
    log: { log() {}, warn() {} },
  });
  const proxyPort = proxy.address().port;

  const handshake = await new Promise((resolve, reject) => {
    const sock = net.connect(proxyPort, '127.0.0.1');
    const key = crypto.randomBytes(16).toString('base64');
    const expectedAccept = crypto
      .createHash('sha1')
      .update(`${key}${WS_GUID}`)
      .digest('base64');
    sock.on('connect', () => {
      sock.write(
        `GET /api/gacha/ws HTTP/1.1\r\n`
        + `Host: 127.0.0.1:${proxyPort}\r\n`
        + 'Upgrade: websocket\r\n'
        + 'Connection: Upgrade\r\n'
        + `Sec-WebSocket-Key: ${key}\r\n`
        + 'Sec-WebSocket-Version: 13\r\n\r\n',
      );
    });
    let data = '';
    sock.on('data', (chunk) => {
      data += chunk.toString('latin1');
      if (data.includes('\r\n\r\n')) {
        sock.removeAllListeners('data');
        resolve({ sock, data, expectedAccept });
      }
    });
    sock.on('error', reject);
  });
  try {
    assert.match(handshake.data, /^HTTP\/1\.1 101 Switching Protocols/i);
    assert.ok(handshake.data.includes(handshake.expectedAccept));

    // masked text frame "hello"
    const payload = Buffer.from('hello');
    const mask = crypto.randomBytes(4);
    const masked = Buffer.from(payload);
    for (let i = 0; i < masked.length; i += 1) masked[i] ^= mask[i % 4];
    const frame = Buffer.concat([
      Buffer.from([0x81, 0x80 | payload.length]),
      mask,
      masked,
    ]);
    const echo = await new Promise((resolve, reject) => {
      let buf = Buffer.alloc(0);
      const onData = (chunk) => {
        buf = Buffer.concat([buf, chunk]);
        if (buf.length >= 7) {
          handshake.sock.removeListener('data', onData);
          resolve(buf.toString('utf8', 2));
        }
      };
      handshake.sock.on('data', onData);
      handshake.sock.write(frame);
      setTimeout(() => reject(new Error('ws echo timeout')), 3000);
    });
    assert.equal(echo, 'hello');
  } finally {
    handshake.sock.destroy();
    for (const sock of upstreamSockets) sock.destroy();
    upstreamSockets.clear();
    proxy.closeAllConnections?.();
    upstream.closeAllConnections?.();
    proxy.close();
    upstream.close();
  }
});
