// TwiCa overlay 用ローカルリバースプロキシ。
// TwiCa は X-Frame-Options: SAMEORIGIN を返すため、ゲームページへの iframe 直埋めが
// Chromium に拒否される。OBS ブラウザソースはトップレベルなので問題ないが、当環境の
// 直接配信は iframe 注入方式のため、同一オリジンのローカルプロキシ経由で
// X-Frame-Options/CSP を除去して表示する。Next.js の相対リソース・RSC リクエスト・
// WebSocket（https→wss 変換）も全て透過的に中継する。
import http from 'http';
import https from 'https';
import crypto from 'crypto';
import { URL } from 'url';

const WS_GUID = '258EAFA5-E914-47DA-95CA-C5AB0DC85B11';

// iframe 埋め込みを妨げるヘッダー。X-Frame-Options が主目的。
const STRIP_RESPONSE_HEADERS = new Set([
  'x-frame-options',
  'content-security-policy',
  'content-security-policy-report-only',
  'strict-transport-security',
  'upgrade-insecure-requests',
]);

// クライアント→プロキシ間で意味を持たない hop-by-hop ヘッダー
const HOP_BY_HOP = new Set([
  'connection',
  'keep-alive',
  'proxy-authenticate',
  'proxy-authorization',
  'te',
  'trailer',
  'transfer-encoding',
  'upgrade',
]);

// TwiCa の body は白背景（var(--background)）を返すため、全画面 iframe で配信を覆ってしまう。
// Next.js は hydration 時に <head>/<body> を再構築するため、HTML への style/script 注入は
// 削除される。そこで、Next.js のグローバル CSS レスポンス自体に透明化ルールを追記する
// （同じ特異度なら後勝ち、!important で確実に上書き）。
const TRANSPARENT_CSS_RULE = '\nhtml,body{background:transparent !important;background-color:transparent !important;}\n';

export function rewriteTwicaOverlayUrl(publicUrl, proxyPort) {
  if (!publicUrl) return '';
  const parsed = new URL(publicUrl);
  return `http://127.0.0.1:${proxyPort}${parsed.pathname}${parsed.search}`;
}

export async function startTwicaOverlayProxy({
  port,
  upstream,
  injectTransparentCss = true,
  log = console,
}) {
  const upstreamBase = new URL(upstream);
  const isTls = upstreamBase.protocol === 'https:';
  const client = isTls ? https : http;
  const server = http.createServer((req, res) => {
    const target = new URL(req.url, upstreamBase);
    const headers = { ...req.headers };
    delete headers.host;
    delete headers.connection;
    const preq = client.request(
      {
        protocol: target.protocol,
        hostname: target.hostname,
        port: target.port || undefined,
        path: `${target.pathname}${target.search}`,
        method: req.method,
        headers,
      },
      (pres) => {
        const outHeaders = {};
        for (const [name, value] of Object.entries(pres.headers)) {
          if (HOP_BY_HOP.has(name.toLowerCase())) continue;
          if (STRIP_RESPONSE_HEADERS.has(name.toLowerCase())) continue;
          // HTML へ CSS を注入すると長さが変わるため、Content-Length は無効化して
          // chunked で送る（Next.js は元々チャンクドで、Content-Length も返さない）。
          if (name.toLowerCase() === 'content-length' && injectTransparentCss) continue;
          outHeaders[name] = value;
        }
        try {
          res.writeHead(pres.statusCode, outHeaders);
        } catch (e) {
          log?.warn?.(`twica proxy writeHead failed: ${e.message}`);
          pres.destroy();
          res.destroy();
          return;
        }
        const contentType = String(pres.headers['content-type'] || '');
        if (injectTransparentCss
          && contentType.includes('text/css')) {
          // CSS の末尾に透明化ルールを追記する（後勝ち・!important で確実に上書き）。
          // Content-Length が変わるため、バッファしてから送る（CSS は十分小さい）。
          const cssChunks = [];
          pres.on('data', (c) => cssChunks.push(c));
          pres.on('end', () => {
            res.write(Buffer.concat(cssChunks));
            res.write(Buffer.from(TRANSPARENT_CSS_RULE, 'utf8'));
            res.end();
          });
          pres.on('error', (e) => {
            log?.warn?.(`twica proxy css error: ${e.message}`);
            try { res.end(); } catch (_) {}
          });
          return;
        }
        pres.pipe(res);
      },
    );
    preq.on('error', (e) => {
      log?.warn?.(`twica proxy request error: ${e.message}`);
      if (!res.headersSent) {
        try {
          res.writeHead(502, { 'Content-Type': 'text/plain; charset=utf-8' });
        } catch (_) {}
      }
      res.end(`twica proxy error: ${e.message}`);
    });
    req.pipe(preq);
  });

  // WebSocket アップグレードの中継。ブラウザは ws://127.0.0.1:PORT へ接続し、
  // 上流（wss://）へヘッダーをそのまま転送して 101 を返す。
  server.on('upgrade', (req, clientSocket, head) => {
    const target = new URL(req.url, upstreamBase);
    const headers = { ...req.headers };
    delete headers.host;
    headers.connection = 'Upgrade';
    headers.upgrade = 'websocket';
    const preq = client.request({
      protocol: target.protocol,
      hostname: target.hostname,
      port: target.port || undefined,
      path: `${target.pathname}${target.search}`,
      method: 'GET',
      headers,
    });
    preq.on('upgrade', (pres, upstreamSocket, upstreamHead) => {
      const accept = crypto
        .createHash('sha1')
        .update(`${req.headers['sec-websocket-key'] || ''}${WS_GUID}`)
        .digest('base64');
      clientSocket.write(
        'HTTP/1.1 101 Switching Protocols\r\n'
        + 'Upgrade: websocket\r\n'
        + 'Connection: Upgrade\r\n'
        + `Sec-WebSocket-Accept: ${accept}\r\n\r\n`,
      );
      if (upstreamHead && upstreamHead.length) upstreamSocket.unshift(upstreamHead);
      upstreamSocket.pipe(clientSocket);
      clientSocket.pipe(upstreamSocket);
      const teardown = () => {
        clientSocket.destroy();
        upstreamSocket.destroy();
      };
      for (const sock of [clientSocket, upstreamSocket]) {
        sock.on('error', teardown);
        sock.on('end', teardown);
        sock.on('close', teardown);
      }
    });
    preq.on('error', (e) => {
      log?.warn?.(`twica proxy upgrade error: ${e.message}`);
      clientSocket.destroy();
    });
    if (head && head.length) preq.write(head);
    preq.end();
  });

  await new Promise((resolve, reject) => {
    server.once('error', reject);
    server.listen(port, '127.0.0.1', () => resolve(server));
  });
  log?.log?.(`TwiCa overlay proxy on http://127.0.0.1:${port} -> ${upstreamBase.origin}`);
  return server;
}
