// Hardened static file server for the Unity WebGL build.
//
// This module owns every security-relevant decision for serving files out of
// a single build directory: which address it binds to, which HTTP methods
// and paths are accepted, and how a request path is mapped to a real file
// underneath the build directory without ever escaping it (including via
// symlinks). soviet_local.mjs composes this with its own overlay-route
// dispatch and Unity canvas-size rewrite via the `beforeStatic` / `rewriteFile`
// hooks so those stay unit-testable together with the file-serving path.
import fs from 'fs';
import http from 'http';
import path from 'path';
import crypto from 'crypto';

// MIME types for the Unity WebGL build. This is also the extension allowlist:
// anything not listed here is refused (403) even if it happens to exist
// under the build directory. Extensions were taken from an actual Unity
// WebGL build (index.html, Build/*.loader.js, Build/*.{data,framework.js,wasm}.gz,
// TemplateData/*.{ico,png,css}) plus the pre-existing MIME table this file
// replaces one-for-one.
export const STATIC_MIME_TYPES = Object.freeze({
  '.html': 'text/html',
  '.js': 'application/javascript',
  '.css': 'text/css',
  '.png': 'image/png',
  '.ico': 'image/x-icon',
  '.data': 'application/octet-stream',
  '.wasm': 'application/wasm',
  '.gz': null, // handled specially: inner extension picks Content-Type, plus Content-Encoding: gzip
});

export const STATIC_ALLOWED_EXTENSIONS = new Set(Object.keys(STATIC_MIME_TYPES));

export const DEFAULT_STATIC_BIND_ADDRESS = '127.0.0.1';

// Fixed placeholder base/host used only to run request targets through the
// WHATWG URL parser. Any request whose target manages to change the parsed
// host (an authority-form or protocol-relative target such as `//evil/x`,
// or an absolute-URI target such as `http://evil/x`) is rejected instead of
// silently reinterpreted, since Node's http server never sets `req.url` that
// way for ordinary same-origin requests.
const REQUEST_URL_BASE = 'http://soviet-now-static.invalid/';
const REQUEST_URL_BASE_HOST = 'soviet-now-static.invalid';

export function resolveStaticBindAddress(env = process.env) {
  const raw = String(env.SOREN_SERVE_BIND_ADDRESS || '').trim();
  return raw || DEFAULT_STATIC_BIND_ADDRESS;
}

export function isStaticServerMethodAllowed(method) {
  return method === 'GET' || method === 'HEAD';
}

// GET/HEAD requests never carry a body on this server; treat one as a
// malformed request rather than silently reading (and buffering) it.
export function requestHasBody(req) {
  const length = Number(req.headers['content-length'] || 0);
  if (Number.isFinite(length) && length > 0) return true;
  if (req.headers['transfer-encoding']) return true;
  return false;
}

function parseIntEnv(env, name, fallback) {
  const parsed = Number.parseInt(env[name] ?? '', 10);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : fallback;
}

export function resolveStaticServerHardening(env = process.env) {
  return {
    maxHeaderBytes: parseIntEnv(env, 'SOREN_STATIC_MAX_HEADER_BYTES', 16 * 1024),
    idleTimeoutMs: parseIntEnv(env, 'SOREN_STATIC_IDLE_TIMEOUT_MS', 30_000),
    headersTimeoutMs: parseIntEnv(env, 'SOREN_STATIC_HEADERS_TIMEOUT_MS', 10_000),
    requestTimeoutMs: parseIntEnv(env, 'SOREN_STATIC_REQUEST_TIMEOUT_MS', 30_000),
    maxResponseBytes: parseIntEnv(env, 'SOREN_STATIC_MAX_RESPONSE_BYTES', 256 * 1024 * 1024),
  };
}

// Applies request/idle/header limits to an http.Server. Kept as a separate
// function (rather than inline options on http.createServer) so tests can
// exercise it directly against a plain server.
export function applyStaticServerHardening(server, hardening) {
  server.timeout = hardening.idleTimeoutMs;
  server.headersTimeout = hardening.headersTimeoutMs;
  server.requestTimeout = hardening.requestTimeoutMs;
  server.keepAliveTimeout = Math.min(hardening.idleTimeoutMs, server.keepAliveTimeout || hardening.idleTimeoutMs);
  // Node destroys a timed-out socket automatically only when nothing is
  // listening for 'timeout'; attach an explicit listener so the idle-timeout
  // limit is unconditional regardless of Node version defaults.
  server.on('timeout', (socket) => {
    try { socket.destroy(); } catch { /* already gone */ }
  });
  return server;
}

/**
 * Maps a raw request URL to a real file inside buildDirRealPath, or a
 * structured rejection. buildDirRealPath MUST already be resolved with
 * fs.realpathSync so the containment check below is comparing two real paths.
 *
 * Order of checks mirrors the design in docich issue #36:
 *   1. Parse with the WHATWG URL (fixed base/host, so authority tricks and
 *      absolute-URI targets are caught by the host check below).
 *   2. Reject any query string (no legitimate request here uses one).
 *   3. Decode percent-encoding exactly once; a throw means malformed
 *      encoding (`%zz`, a lone `%`, overlong UTF-8, ...) -> 400.
 *   4. Reject an embedded NUL byte, and reject a decoded path that still
 *      contains a literal '%' (surviving a single decode means multi-layer
 *      / confusable percent-encoding was used) -> 400.
 *   5. Normalize `\` to `/`, split into segments, reject any literal `..`
 *      segment -> 403 (this also catches the single- and double-encoded
 *      separator cases once decoded).
 *   6. Build a path relative to the build root from the filtered segments
 *      (never resolving a leading '/' as an OS-root reset -- path.resolve's
 *      absolute-second-argument behavior is the classic way this class of
 *      fix regresses) and realpath it.
 *   7. Require the realpath to be buildDirRealPath itself or a descendant of
 *      it (rejects symlink escapes, since realpath follows symlinks).
 *   8. Require it to be a regular file with an allowlisted extension and a
 *      size under the configured cap.
 */
export function resolveStaticRequestPath({
  rawUrl,
  buildDirRealPath,
  allowedExtensions = STATIC_ALLOWED_EXTENSIONS,
  maxResponseBytes = Infinity,
}) {
  let parsed;
  try {
    parsed = new URL(rawUrl ?? '/', REQUEST_URL_BASE);
  } catch {
    return { ok: false, status: 400, reason: 'unparsable_url' };
  }
  if (parsed.host !== REQUEST_URL_BASE_HOST) {
    return { ok: false, status: 400, reason: 'unexpected_authority' };
  }
  if (parsed.search) {
    return { ok: false, status: 400, reason: 'query_not_allowed' };
  }

  let decoded;
  try {
    decoded = decodeURIComponent(parsed.pathname);
  } catch {
    return { ok: false, status: 400, reason: 'malformed_percent_encoding' };
  }

  if (decoded.indexOf('\u0000') !== -1) {
    return { ok: false, status: 400, reason: 'nul_byte' };
  }
  if (decoded.indexOf('%') !== -1) {
    return { ok: false, status: 400, reason: 'residual_percent_encoding' };
  }

  const normalized = decoded.split('\\').join('/');
  const segments = normalized.split('/');
  if (segments.some((segment) => segment === '..')) {
    return { ok: false, status: 403, reason: 'path_traversal' };
  }

  const relativeSegments = segments.filter((segment) => segment !== '' && segment !== '.');
  const relativePath = relativeSegments.length === 0 ? 'index.html' : relativeSegments.join(path.sep);
  const candidate = path.resolve(buildDirRealPath, relativePath);

  let realCandidate;
  try {
    realCandidate = fs.realpathSync(candidate);
  } catch (err) {
    if (err && (err.code === 'ENOENT' || err.code === 'ENOTDIR')) {
      return { ok: false, status: 404, reason: 'not_found' };
    }
    return { ok: false, status: 403, reason: 'unresolvable' };
  }

  const prefix = buildDirRealPath.endsWith(path.sep) ? buildDirRealPath : buildDirRealPath + path.sep;
  if (realCandidate !== buildDirRealPath && !realCandidate.startsWith(prefix)) {
    return { ok: false, status: 403, reason: 'outside_build_dir' };
  }

  let stat;
  try {
    stat = fs.statSync(realCandidate);
  } catch {
    return { ok: false, status: 404, reason: 'not_found' };
  }
  if (!stat.isFile()) {
    return { ok: false, status: 404, reason: 'not_found' };
  }

  const ext = path.extname(realCandidate);
  if (!allowedExtensions.has(ext)) {
    return { ok: false, status: 403, reason: 'extension_not_allowed' };
  }
  if (stat.size > maxResponseBytes) {
    return { ok: false, status: 403, reason: 'response_too_large' };
  }

  return { ok: true, filePath: realCandidate, ext, size: stat.size };
}

// A short, non-reversible fingerprint of the document root. Logged at
// startup instead of the raw filesystem path so operators can confirm which
// build is being served (and diff it across restarts/deploys) without the
// log line embedding a home-directory / username path.
export function hashDocumentRoot(documentRootRealPath) {
  return crypto.createHash('sha256').update(documentRootRealPath).digest('hex').slice(0, 16);
}

export function describeStaticServerStartup({ address, documentRootRealPath }) {
  return {
    boundAddress: address?.address ?? null,
    boundPort: address?.port ?? null,
    boundFamily: address?.family ?? null,
    documentRootHash: hashDocumentRoot(documentRootRealPath),
  };
}

/**
 * Builds (but does not start) the hardened static file server. `beforeStatic`
 * lets the caller intercept a request before it is resolved against the
 * build directory (used for the fixed, non-filesystem overlay routes);
 * return true from it once the response has been fully handled. `rewriteFile`
 * lets the caller substitute the response body for a specific matched file
 * (used for the Unity canvas-size rewrite of index.html); return a
 * string/Buffer to use as the body, or null/undefined to stream the file
 * unmodified.
 */
export function createStaticFileServer({
  buildDir,
  mimeTypes = STATIC_MIME_TYPES,
  allowedExtensions,
  env = process.env,
  bindAddress,
  hardening,
  noCacheHeaders = {
    'Cache-Control': 'no-store, no-cache, must-revalidate',
    'Pragma': 'no-cache',
    'Expires': '0',
  },
  beforeStatic,
  rewriteFile,
  onStartupInfo,
} = {}) {
  if (!buildDir) throw new Error('createStaticFileServer requires buildDir');
  const buildDirRealPath = fs.realpathSync(buildDir);
  const resolvedAllowedExtensions = allowedExtensions || new Set(Object.keys(mimeTypes));
  const resolvedHardening = hardening || resolveStaticServerHardening(env);
  const resolvedBindAddress = bindAddress || resolveStaticBindAddress(env);

  const server = http.createServer({ maxHeaderSize: resolvedHardening.maxHeaderBytes }, (req, res) => {
    if (!isStaticServerMethodAllowed(req.method)) {
      res.writeHead(405, { Allow: 'GET, HEAD', Connection: 'close' });
      res.end('Method Not Allowed');
      return;
    }
    if (requestHasBody(req)) {
      res.writeHead(400, { Connection: 'close' });
      res.end('Bad Request');
      return;
    }

    let requestPath;
    try {
      requestPath = new URL(req.url || '/', 'http://127.0.0.1').pathname;
    } catch {
      res.writeHead(400);
      res.end('Bad Request');
      return;
    }

    if (typeof beforeStatic === 'function' && beforeStatic(req, res, requestPath)) {
      return;
    }

    const resolved = resolveStaticRequestPath({
      rawUrl: req.url,
      buildDirRealPath,
      allowedExtensions: resolvedAllowedExtensions,
      maxResponseBytes: resolvedHardening.maxResponseBytes,
    });

    if (!resolved.ok) {
      res.writeHead(resolved.status);
      res.end(resolved.status === 404 ? 'Not found' : (http.STATUS_CODES[resolved.status] || 'Rejected'));
      return;
    }

    const { filePath, ext } = resolved;
    let headers;
    if (ext === '.gz') {
      // Serve .gz files with Content-Encoding: gzip and the inner file's Content-Type.
      const innerExt = path.extname(filePath.slice(0, -3)); // e.g. .js from .js.gz
      const contentType = mimeTypes[innerExt] || 'application/octet-stream';
      headers = { 'Content-Type': contentType, 'Content-Encoding': 'gzip', ...noCacheHeaders };
    } else {
      const contentType = mimeTypes[ext] || 'application/octet-stream';
      headers = { 'Content-Type': contentType, ...noCacheHeaders };
    }

    res.writeHead(200, headers);

    if (req.method === 'HEAD') {
      res.end();
      return;
    }

    const rewritten = typeof rewriteFile === 'function' ? rewriteFile(filePath) : null;
    if (rewritten != null) {
      res.end(rewritten);
      return;
    }

    fs.createReadStream(filePath).pipe(res);
  });

  applyStaticServerHardening(server, resolvedHardening);

  return {
    server,
    buildDirRealPath,
    bindAddress: resolvedBindAddress,
    hardening: resolvedHardening,
    listen(port) {
      return new Promise((resolve, reject) => {
        const onError = (err) => {
          server.off('listening', onListening);
          reject(err);
        };
        const onListening = () => {
          server.off('error', onError);
          const info = describeStaticServerStartup({ address: server.address(), documentRootRealPath: buildDirRealPath });
          if (typeof onStartupInfo === 'function') onStartupInfo(info);
          resolve(server);
        };
        server.once('error', onError);
        server.listen(port, resolvedBindAddress, onListening);
      });
    },
  };
}
