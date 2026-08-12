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

// TwiCa の body は白背景（#fff）を返すため、全画面 iframe で配信を覆ってしまう。
// Next.js は hydration 時に <head> を再構築して style タグを削除するため、
// <body> 直後に差し込む script で html/body を透過化する（script は body 内要素として保持される）。
// また、ゲーム画面の下に TwiCa カードを重ねるため、iframe 自体も pointer-events を無効にする。
const TRANSPARENT_SCRIPT = '<script id="__soren_twica_transparent">'
  + '(function(){'
  + 'function apply(){'
  + 'var s=document.documentElement.style; s.background="transparent"; s.backgroundColor="transparent";'
  + 'var b=document.body; if(b){b.style.background="transparent"; b.style.backgroundColor="transparent";}'
  + '}'
  + 'apply();'
  + 'if(document.readyState!=="complete"){document.addEventListener("readystatechange",function(){if(document.readyState==="complete"){apply();}});}'
  + 'setInterval(apply,500);'
  + '})();'
  + '</script>';

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
          && contentType.includes('text/html')) {
          // ストリームを維持したまま <head> の直後に CSS を注入する。
          // バッファしてから送ると Next.js のチャンクド配信がハングするため、
          // 先頭から <head> タグが揃うまでだけバッファし、以降はそのまま pipe する。
          let headBuf = Buffer.alloc(0);
          let injected = false;
          pres.on('data', (chunk) => {
            if (injected) {
              res.write(chunk);
              return;
            }
            headBuf = Buffer.concat([headBuf, chunk]);
            // <body> タグの直後に script を注入する。Next.js の HTML は
            // <body ...> を必ず含む。タグが揃うまでバッファし、以降は pipe する。
            const bodyMatch = /<body[^>]*>/i.exec(headBuf.toString('utf8'));
            if (!bodyMatch) {
              // <body> がまだ揃わない。巨大化を防ぐ上限（256KB）で打ち切る。
              if (headBuf.length > 262144) {
                res.write(headBuf);
                headBuf = Buffer.alloc(0);
                injected = true;
                log?.warn?.('twica css inject: <body> not found within 256KB, skipping injection');
              }
              return;
            }
            const bodyEnd = bodyMatch.index + bodyMatch[0].length;
            const beforeBody = headBuf.subarray(0, bodyEnd);
            const rest = headBuf.subarray(bodyEnd);
            res.write(beforeBody);
            res.write(Buffer.from(TRANSPARENT_SCRIPT, 'utf8'));
            injected = true;
            if (rest.length) res.write(rest);
            headBuf = Buffer.alloc(0);
          });
          pres.on('end', () => {
            if (!injected && headBuf.length) res.write(headBuf);
            res.end();
          });
          pres.on('error', (e) => {
            log?.warn?.(`twica proxy body error: ${e.message}`);
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
