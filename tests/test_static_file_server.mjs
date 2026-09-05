// docich issue #36: the Unity static file server must bind to loopback only,
// resolve every request path through a canonical-path containment check, and
// reject anything that is not a plain GET/HEAD for an allowlisted extension.
//
// This suite exercises lib/static_file_server.mjs directly (the module
// soviet_local.mjs's startServer() now wraps), using a throwaway Unity-shaped
// build directory and an external sentinel file so the tests never touch the
// real repo checkout or the network beyond loopback.
import assert from 'node:assert/strict';
import test from 'node:test';
import fs from 'node:fs';
import os from 'node:os';
import net from 'node:net';
import path from 'node:path';
import http from 'node:http';

import {
  DEFAULT_STATIC_BIND_ADDRESS,
  STATIC_ALLOWED_EXTENSIONS,
  STATIC_MIME_TYPES,
  applyStaticServerHardening,
  createStaticFileServer,
  describeStaticServerStartup,
  hashDocumentRoot,
  isStaticServerMethodAllowed,
  requestHasBody,
  resolveStaticBindAddress,
  resolveStaticRequestPath,
  resolveStaticServerHardening,
} from '../lib/static_file_server.mjs';

// --- fixtures ---------------------------------------------------------------

// Mirrors an actual Unity WebGL build's shape/extensions (index.html,
// Build/*.loader.js + *.{data,framework.js,wasm}.gz, TemplateData/*.{ico,css,png}),
// confirmed against a real soren-game build directory while implementing this.
function makeBuildDir() {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'soviet-static-build-'));
  fs.writeFileSync(path.join(dir, 'index.html'), '<!doctype html><canvas id="unity-canvas"></canvas>');
  fs.mkdirSync(path.join(dir, 'Build'));
  fs.writeFileSync(path.join(dir, 'Build', 'soren-build.loader.js'), 'function loader(){}');
  fs.writeFileSync(path.join(dir, 'Build', 'soren-build.data.gz'), 'FAKE-GZIP-DATA-BYTES');
  fs.writeFileSync(path.join(dir, 'Build', 'soren-build.framework.js.gz'), 'FAKE-GZIP-FRAMEWORK-BYTES');
  fs.writeFileSync(path.join(dir, 'Build', 'soren-build.wasm.gz'), 'FAKE-GZIP-WASM-BYTES');
  fs.mkdirSync(path.join(dir, 'TemplateData'));
  fs.writeFileSync(path.join(dir, 'TemplateData', 'favicon.ico'), 'FAKE-ICO');
  fs.writeFileSync(path.join(dir, 'TemplateData', 'style.css'), 'body{margin:0}');
  fs.writeFileSync(path.join(dir, 'TemplateData', 'unity-logo-dark.png'), 'FAKE-PNG');
  // A file with a non-allowlisted extension, to prove the allowlist is enforced
  // even for files that legitimately exist under the build root.
  fs.writeFileSync(path.join(dir, 'notes.txt'), 'not a Unity asset');
  return { dir, realDir: fs.realpathSync(dir) };
}

function makeExternalSentinel() {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'soviet-static-external-'));
  const file = path.join(dir, 'sentinel.data');
  fs.writeFileSync(file, 'EXTERNAL-SENTINEL-MUST-NEVER-BE-SERVED');
  return { dir, file: fs.realpathSync(file) };
}

function cleanup(...dirs) {
  for (const dir of dirs) {
    try { fs.rmSync(dir, { recursive: true, force: true }); } catch { /* best effort */ }
  }
}

function listenEphemeral(handle) {
  return handle.listen(0);
}

function get(port, requestPath, options = {}) {
  return new Promise((resolve, reject) => {
    const req = http.request({
      host: '127.0.0.1',
      port,
      path: requestPath,
      method: options.method || 'GET',
      headers: options.headers,
    }, (res) => {
      const chunks = [];
      res.on('data', (chunk) => chunks.push(chunk));
      res.on('end', () => resolve({
        status: res.statusCode,
        headers: res.headers,
        body: Buffer.concat(chunks).toString('utf8'),
      }));
    });
    req.on('error', reject);
    if (options.body) req.write(options.body);
    req.end();
  });
}

// --- resolveStaticBindAddress -----------------------------------------------

test('bind address defaults to loopback and is overridable via env', () => {
  assert.equal(DEFAULT_STATIC_BIND_ADDRESS, '127.0.0.1');
  assert.equal(resolveStaticBindAddress({}), '127.0.0.1');
  assert.equal(resolveStaticBindAddress({ SOREN_SERVE_BIND_ADDRESS: '' }), '127.0.0.1');
  assert.equal(resolveStaticBindAddress({ SOREN_SERVE_BIND_ADDRESS: '  ' }), '127.0.0.1');
  assert.equal(resolveStaticBindAddress({ SOREN_SERVE_BIND_ADDRESS: '0.0.0.0' }), '0.0.0.0');
});

// --- isStaticServerMethodAllowed / requestHasBody ----------------------------

test('only GET and HEAD are allowed methods', () => {
  assert.equal(isStaticServerMethodAllowed('GET'), true);
  assert.equal(isStaticServerMethodAllowed('HEAD'), true);
  for (const method of ['POST', 'PUT', 'DELETE', 'PATCH', 'OPTIONS', 'TRACE', 'CONNECT', 'get', '']) {
    assert.equal(isStaticServerMethodAllowed(method), false, method);
  }
});

test('requestHasBody flags a declared Content-Length or Transfer-Encoding', () => {
  assert.equal(requestHasBody({ headers: {} }), false);
  assert.equal(requestHasBody({ headers: { 'content-length': '0' } }), false);
  assert.equal(requestHasBody({ headers: { 'content-length': '1' } }), true);
  assert.equal(requestHasBody({ headers: { 'transfer-encoding': 'chunked' } }), true);
});

// --- resolveStaticRequestPath: table-driven path safety ---------------------

test('resolveStaticRequestPath: table-driven request path safety', (t) => {
  const { realDir } = makeBuildDir();
  const { file: externalSentinel } = makeExternalSentinel();
  t.after(() => cleanup(realDir, path.dirname(externalSentinel)));

  // Symlink escape: a file *inside* the build dir whose target is outside it.
  const symlinkPath = path.join(realDir, 'escape-link.data');
  let symlinksSupported = true;
  try {
    fs.symlinkSync(externalSentinel, symlinkPath);
  } catch {
    symlinksSupported = false;
  }

  const cases = [
    // --- accepted: real Unity assets ---
    { name: 'root maps to index.html', url: '/', expect: { ok: true, ext: '.html' } },
    { name: 'index.html directly', url: '/index.html', expect: { ok: true, ext: '.html' } },
    { name: 'loader.js', url: '/Build/soren-build.loader.js', expect: { ok: true, ext: '.js' } },
    { name: 'data.gz', url: '/Build/soren-build.data.gz', expect: { ok: true, ext: '.gz' } },
    { name: 'favicon.ico', url: '/TemplateData/favicon.ico', expect: { ok: true, ext: '.ico' } },
    { name: 'style.css', url: '/TemplateData/style.css', expect: { ok: true, ext: '.css' } },
    { name: 'template png', url: '/TemplateData/unity-logo-dark.png', expect: { ok: true, ext: '.png' } },

    // --- rejected: traversal / encoding attacks ---
    // A *literal*, unencoded '..' is already fully neutralized by the WHATWG
    // URL parser itself before this module ever sees it (RFC 3986 dot-segment
    // removal: it cannot go above the URL root, so '/..' resolves to '/' and
    // '/../secret' resolves to '/secret' -- never outside the build dir).
    // That is correct/standard URL-resolution behavior, not a gap: the cases
    // this module's own '..'-segment check exists for are the *encoded*
    // variants below, which the URL parser does NOT collapse.
    { name: 'bare .. collapses to root (URL-level, not a traversal)', url: '/..', expect: { ok: true, ext: '.html' } },
    { name: 'literal dotdot collapses within the URL parser', url: '/../secret', expect: { ok: false, status: 404, reason: 'not_found' } },
    { name: 'nested literal dotdot collapses within the URL parser', url: '/a/../../secret', expect: { ok: false, status: 404, reason: 'not_found' } },
    {
      name: 'encoded separator (%2e%2e%2f)',
      url: '/%2e%2e%2f%2e%2e%2fsecret',
      expect: { ok: false, status: 403, reason: 'path_traversal' },
    },
    {
      // '%2e%2e' alone (no encoded slash) forms a complete path segment that
      // the WHATWG URL parser itself recognizes as '..' and collapses at
      // parse time -- same as the literal '..' cases above. It never reaches
      // this module's own decode step with a residual '..' segment.
      name: 'encoded dot only (no encoded slash) also collapses in the URL parser',
      url: '/a/%2e%2e/secret',
      expect: { ok: false, status: 404, reason: 'not_found' },
    },
    {
      name: 'double-encoded separator',
      url: '/%252e%252e%252fsecret',
      expect: { ok: false, status: 400, reason: 'residual_percent_encoding' },
    },
    {
      name: 'mixed single/double encoding',
      url: '/%252e%252e%2fsecret',
      expect: { ok: false, status: 400, reason: 'residual_percent_encoding' },
    },
    { name: 'malformed percent %zz', url: '/%zz', expect: { ok: false, status: 400, reason: 'malformed_percent_encoding' } },
    { name: 'truncated percent %2', url: '/%2', expect: { ok: false, status: 400, reason: 'malformed_percent_encoding' } },
    {
      name: 'overlong UTF-8 percent sequence',
      url: '/%e0%80%af',
      expect: { ok: false, status: 400, reason: 'malformed_percent_encoding' },
    },
    { name: 'NUL byte', url: '/%00', expect: { ok: false, status: 400, reason: 'nul_byte' } },
    { name: 'embedded NUL byte', url: '/index.html%00.png', expect: { ok: false, status: 400, reason: 'nul_byte' } },
    { name: 'query string', url: '/index.html?x=1', expect: { ok: false, status: 400, reason: 'query_not_allowed' } },
    {
      name: 'protocol-relative authority trick',
      url: '//evil.example/index.html',
      expect: { ok: false, status: 400, reason: 'unexpected_authority' },
    },
    {
      name: 'absolute-URI request target',
      url: 'http://evil.example/index.html',
      expect: { ok: false, status: 400, reason: 'unexpected_authority' },
    },

    // --- rejected: policy (extension / existence) ---
    { name: 'disallowed extension that exists on disk', url: '/notes.txt', expect: { ok: false, status: 403, reason: 'extension_not_allowed' } },
    { name: 'missing file', url: '/does-not-exist.html', expect: { ok: false, status: 404, reason: 'not_found' } },
  ];

  for (const testCase of cases) {
    const result = resolveStaticRequestPath({ rawUrl: testCase.url, buildDirRealPath: realDir, allowedExtensions: STATIC_ALLOWED_EXTENSIONS });
    assert.equal(result.ok, testCase.expect.ok, `${testCase.name}: ok mismatch (got ${JSON.stringify(result)})`);
    if (testCase.expect.ok) {
      assert.equal(result.ext, testCase.expect.ext, testCase.name);
    } else {
      assert.equal(result.status, testCase.expect.status, `${testCase.name}: status mismatch (got ${JSON.stringify(result)})`);
      assert.equal(result.reason, testCase.expect.reason, testCase.name);
    }
  }

  // Absolute-path escape: requesting the sentinel file's own absolute
  // filesystem path must never return the sentinel's content. (This is the
  // classic path.resolve(base, '/abs/path') pitfall -- resolve() treats an
  // absolute second argument as replacing the base entirely -- that a naive
  // refactor of the old path.join()-based code could reintroduce.)
  const absoluteEscape = resolveStaticRequestPath({ rawUrl: externalSentinel, buildDirRealPath: realDir, allowedExtensions: STATIC_ALLOWED_EXTENSIONS });
  assert.notEqual(absoluteEscape.ok, true, `absolute path must not resolve outside the build dir: ${JSON.stringify(absoluteEscape)}`);
  if (absoluteEscape.ok) {
    assert.notEqual(absoluteEscape.filePath, externalSentinel);
  }

  // Symlink escape: the on-disk symlink resolves (via realpath) outside the
  // build dir and must be rejected even though it "exists" and has an
  // allowlisted extension.
  if (symlinksSupported) {
    const symlinkResult = resolveStaticRequestPath({ rawUrl: '/escape-link.data', buildDirRealPath: realDir, allowedExtensions: STATIC_ALLOWED_EXTENSIONS });
    assert.equal(symlinkResult.ok, false, `symlink escape: ${JSON.stringify(symlinkResult)}`);
    assert.equal(symlinkResult.status, 403);
    assert.equal(symlinkResult.reason, 'outside_build_dir');
  } else {
    t.diagnose?.('symlink creation unsupported on this filesystem; symlink-escape case skipped');
  }
});

test('resolveStaticRequestPath: response size cap', (t) => {
  const { realDir } = makeBuildDir();
  t.after(() => cleanup(realDir));

  const big = resolveStaticRequestPath({
    rawUrl: '/Build/soren-build.data.gz',
    buildDirRealPath: realDir,
    allowedExtensions: STATIC_ALLOWED_EXTENSIONS,
    maxResponseBytes: 4, // fixture file is well over 4 bytes
  });
  assert.equal(big.ok, false);
  assert.equal(big.status, 403);
  assert.equal(big.reason, 'response_too_large');

  const fine = resolveStaticRequestPath({
    rawUrl: '/Build/soren-build.data.gz',
    buildDirRealPath: realDir,
    allowedExtensions: STATIC_ALLOWED_EXTENSIONS,
    maxResponseBytes: Infinity,
  });
  assert.equal(fine.ok, true);
});

// --- hashDocumentRoot / describeStaticServerStartup -------------------------

test('document root hash is stable, distinguishing, and never the raw path', () => {
  const hashA = hashDocumentRoot('/Users/someone/work/soviet_now/sorengame/build');
  const hashB = hashDocumentRoot('/Users/someone/work/soviet_now/sorengame/build');
  const hashC = hashDocumentRoot('/somewhere/else/build');
  assert.equal(hashA, hashB);
  assert.notEqual(hashA, hashC);
  assert.match(hashA, /^[0-9a-f]{16}$/);
  assert.ok(!hashA.includes('Users'));

  const info = describeStaticServerStartup({
    address: { address: '127.0.0.1', port: 8080, family: 'IPv4' },
    documentRootRealPath: '/some/build/dir',
  });
  assert.deepEqual(info, {
    boundAddress: '127.0.0.1',
    boundPort: 8080,
    boundFamily: 'IPv4',
    documentRootHash: hashDocumentRoot('/some/build/dir'),
  });
});

// --- applyStaticServerHardening ----------------------------------------------

test('hardening options apply request/idle timeouts to a plain http.Server', () => {
  const hardening = resolveStaticServerHardening({
    SOREN_STATIC_IDLE_TIMEOUT_MS: '1234',
    SOREN_STATIC_HEADERS_TIMEOUT_MS: '2345',
    SOREN_STATIC_REQUEST_TIMEOUT_MS: '3456',
    SOREN_STATIC_MAX_HEADER_BYTES: '4567',
    SOREN_STATIC_MAX_RESPONSE_BYTES: '5678',
  });
  assert.deepEqual(hardening, {
    maxHeaderBytes: 4567,
    idleTimeoutMs: 1234,
    headersTimeoutMs: 2345,
    requestTimeoutMs: 3456,
    maxResponseBytes: 5678,
  });

  const server = http.createServer();
  applyStaticServerHardening(server, hardening);
  assert.equal(server.timeout, 1234);
  assert.equal(server.headersTimeout, 2345);
  assert.equal(server.requestTimeout, 3456);
  assert.equal(server.keepAliveTimeout, 1234);
});

test('resolveStaticServerHardening falls back to safe defaults for unset/invalid env', () => {
  const defaults = resolveStaticServerHardening({});
  assert.ok(defaults.maxHeaderBytes > 0);
  assert.ok(defaults.idleTimeoutMs > 0);
  assert.ok(defaults.headersTimeoutMs > 0);
  assert.ok(defaults.requestTimeoutMs > 0);
  assert.ok(defaults.maxResponseBytes > 0);

  const invalid = resolveStaticServerHardening({ SOREN_STATIC_IDLE_TIMEOUT_MS: 'not-a-number', SOREN_STATIC_MAX_HEADER_BYTES: '-5' });
  assert.deepEqual(invalid, defaults);
});

// --- createStaticFileServer: full server, real sockets ----------------------

test('listener binds to loopback only by default (real socket inspection)', async (t) => {
  const { realDir } = makeBuildDir();
  t.after(() => cleanup(realDir));

  const handle = createStaticFileServer({ buildDir: realDir, env: {} });
  const server = await listenEphemeral(handle);
  t.after(() => new Promise((resolve) => server.close(resolve)));

  const address = server.address();
  assert.equal(address.address, '127.0.0.1');
  assert.equal(handle.bindAddress, '127.0.0.1');

  // Prove it, not just assert the reported address: any non-loopback local
  // interface must refuse a connection to this port.
  const interfaces = Object.values(os.networkInterfaces()).flat();
  const externalIPv4 = interfaces.find((entry) => entry && entry.family === 'IPv4' && !entry.internal);
  if (!externalIPv4) {
    t.diagnose?.('no non-loopback IPv4 interface available in this sandbox; skipping cross-interface probe');
    return;
  }
  await new Promise((resolve) => {
    const socket = net.connect({ host: externalIPv4.address, port: address.port, timeout: 1000 });
    const finish = (outcome) => { socket.destroy(); resolve(outcome); };
    socket.on('connect', () => finish('connected'));
    socket.on('timeout', () => finish('timeout'));
    socket.on('error', () => finish('error'));
  }).then((outcome) => {
    assert.notEqual(outcome, 'connected', `port must not be reachable via ${externalIPv4.address}`);
  });
});

test('bind address is configurable via env (still not 0.0.0.0 by accident)', async (t) => {
  const { realDir } = makeBuildDir();
  t.after(() => cleanup(realDir));

  const handle = createStaticFileServer({ buildDir: realDir, env: { SOREN_SERVE_BIND_ADDRESS: '127.0.0.1' } });
  const server = await listenEphemeral(handle);
  t.after(() => new Promise((resolve) => server.close(resolve)));
  assert.equal(server.address().address, '127.0.0.1');
});

test('Unity asset fixture: gzip/content-type/no-cache headers are unchanged (regression fixture)', async (t) => {
  const { realDir } = makeBuildDir();
  t.after(() => cleanup(realDir));
  const handle = createStaticFileServer({ buildDir: realDir, env: {} });
  const server = await listenEphemeral(handle);
  t.after(() => new Promise((resolve) => server.close(resolve)));
  const port = server.address().port;

  const index = await get(port, '/');
  assert.equal(index.status, 200);
  assert.equal(index.headers['content-type'], STATIC_MIME_TYPES['.html']);
  assert.equal(index.headers['cache-control'], 'no-store, no-cache, must-revalidate');
  assert.equal(index.headers['pragma'], 'no-cache');
  assert.equal(index.headers['expires'], '0');
  assert.match(index.body, /unity-canvas/);

  const loader = await get(port, '/Build/soren-build.loader.js');
  assert.equal(loader.status, 200);
  assert.equal(loader.headers['content-type'], 'application/javascript');
  assert.equal(loader.headers['content-encoding'], undefined);
  assert.equal(loader.body, 'function loader(){}');

  const dataGz = await get(port, '/Build/soren-build.data.gz');
  assert.equal(dataGz.status, 200);
  assert.equal(dataGz.headers['content-encoding'], 'gzip');
  assert.equal(dataGz.headers['content-type'], STATIC_MIME_TYPES['.data']);
  assert.equal(dataGz.body, 'FAKE-GZIP-DATA-BYTES');

  const frameworkGz = await get(port, '/Build/soren-build.framework.js.gz');
  assert.equal(frameworkGz.status, 200);
  assert.equal(frameworkGz.headers['content-encoding'], 'gzip');
  assert.equal(frameworkGz.headers['content-type'], STATIC_MIME_TYPES['.js']);

  const wasmGz = await get(port, '/Build/soren-build.wasm.gz');
  assert.equal(wasmGz.status, 200);
  assert.equal(wasmGz.headers['content-encoding'], 'gzip');
  assert.equal(wasmGz.headers['content-type'], STATIC_MIME_TYPES['.wasm']);

  const favicon = await get(port, '/TemplateData/favicon.ico');
  assert.equal(favicon.status, 200);
  assert.equal(favicon.headers['content-type'], STATIC_MIME_TYPES['.ico']);

  const css = await get(port, '/TemplateData/style.css');
  assert.equal(css.status, 200);
  assert.equal(css.headers['content-type'], STATIC_MIME_TYPES['.css']);
});

test('HEAD returns matching headers with no body', async (t) => {
  const { realDir } = makeBuildDir();
  t.after(() => cleanup(realDir));
  const handle = createStaticFileServer({ buildDir: realDir, env: {} });
  const server = await listenEphemeral(handle);
  t.after(() => new Promise((resolve) => server.close(resolve)));
  const port = server.address().port;

  const head = await get(port, '/Build/soren-build.loader.js', { method: 'HEAD' });
  assert.equal(head.status, 200);
  assert.equal(head.headers['content-type'], 'application/javascript');
  assert.equal(head.body, '');
});

test('non-GET/HEAD methods get 405 and the server keeps serving afterwards', async (t) => {
  const { realDir } = makeBuildDir();
  t.after(() => cleanup(realDir));
  const handle = createStaticFileServer({ buildDir: realDir, env: {} });
  const server = await listenEphemeral(handle);
  t.after(() => new Promise((resolve) => server.close(resolve)));
  const port = server.address().port;

  for (const method of ['POST', 'PUT', 'DELETE', 'PATCH']) {
    const res = await get(port, '/index.html', { method });
    assert.equal(res.status, 405, method);
    assert.match(res.headers.allow || '', /GET/);
  }

  // Process/server must still be alive and correct afterwards.
  const after = await get(port, '/index.html');
  assert.equal(after.status, 200);
  assert.match(after.body, /unity-canvas/);
});

test('a GET/HEAD declaring a body is rejected with 400, connection still usable after', async (t) => {
  const { realDir } = makeBuildDir();
  t.after(() => cleanup(realDir));
  const handle = createStaticFileServer({ buildDir: realDir, env: {} });
  const server = await listenEphemeral(handle);
  t.after(() => new Promise((resolve) => server.close(resolve)));
  const port = server.address().port;

  const withBody = await get(port, '/index.html', {
    headers: { 'Content-Length': '4' },
    body: 'oops',
  });
  assert.equal(withBody.status, 400);

  const after = await get(port, '/index.html');
  assert.equal(after.status, 200);
});

test('malicious request paths rejected end-to-end over real HTTP', async (t) => {
  const { realDir } = makeBuildDir();
  t.after(() => cleanup(realDir));
  const handle = createStaticFileServer({ buildDir: realDir, env: {} });
  const server = await listenEphemeral(handle);
  t.after(() => new Promise((resolve) => server.close(resolve)));
  const port = server.address().port;

  const cases = [
    ['/..', 200], // collapses to '/' at the URL-parser level; see the unit-test comment above
    ['/../secret', 404], // likewise collapses to '/secret', which does not exist
    ['/%2e%2e%2f%2e%2e%2fsecret', 403],
    ['/%252e%252e%252fsecret', 400],
    ['/%zz', 400],
    ['/%2', 400],
    ['/%00', 400],
    ['/index.html?x=1', 400],
    ['//evil.example/index.html', 400],
    ['/notes.txt', 403],
    ['/does-not-exist.html', 404],
  ];
  for (const [requestPath, expectedStatus] of cases) {
    const res = await get(port, requestPath);
    assert.equal(res.status, expectedStatus, `${requestPath} -> expected ${expectedStatus}, got ${res.status}`);
  }

  // Server must still be healthy after the whole barrage.
  const after = await get(port, '/index.html');
  assert.equal(after.status, 200);
});

test('an out-of-build-root sentinel file can never be fetched, including through a symlink', async (t) => {
  const { realDir } = makeBuildDir();
  const { dir: externalDir, file: externalSentinel } = makeExternalSentinel();
  t.after(() => cleanup(realDir, externalDir));

  let symlinksSupported = true;
  try {
    fs.symlinkSync(externalSentinel, path.join(realDir, 'escape-link.data'));
  } catch {
    symlinksSupported = false;
  }

  const handle = createStaticFileServer({ buildDir: realDir, env: {} });
  const server = await listenEphemeral(handle);
  t.after(() => new Promise((resolve) => server.close(resolve)));
  const port = server.address().port;

  // Directly by its own absolute path.
  const direct = await get(port, externalSentinel);
  assert.notEqual(direct.status, 200);
  assert.doesNotMatch(direct.body, /EXTERNAL-SENTINEL/);

  // Via ../ traversal from a nested directory.
  const depth = externalSentinel.split(path.sep).filter(Boolean).length + 2;
  const traversal = `/${'../'.repeat(depth)}${externalSentinel.replace(/^\//, '')}`;
  const viaTraversal = await get(port, traversal);
  assert.notEqual(viaTraversal.status, 200);
  assert.doesNotMatch(viaTraversal.body, /EXTERNAL-SENTINEL/);

  if (symlinksSupported) {
    const viaSymlink = await get(port, '/escape-link.data');
    assert.equal(viaSymlink.status, 403);
    assert.doesNotMatch(viaSymlink.body, /EXTERNAL-SENTINEL/);
  }
});

test('idle connections are closed once the configured timeout elapses', async (t) => {
  const { realDir } = makeBuildDir();
  t.after(() => cleanup(realDir));
  const handle = createStaticFileServer({
    buildDir: realDir,
    env: {},
    hardening: resolveStaticServerHardening({ SOREN_STATIC_IDLE_TIMEOUT_MS: '150' }),
  });
  const server = await listenEphemeral(handle);
  t.after(() => new Promise((resolve) => server.close(resolve)));
  const port = server.address().port;

  await new Promise((resolve, reject) => {
    const socket = net.connect(port, '127.0.0.1', () => {
      // Connect but never send a request line: purely idle.
    });
    const timer = setTimeout(() => reject(new Error('idle socket was not closed by the configured timeout')), 2000);
    socket.on('close', () => { clearTimeout(timer); resolve(); });
    socket.on('error', () => { clearTimeout(timer); resolve(); });
  });
});

test('oversized request headers are refused (431) instead of buffered without bound', async (t) => {
  const { realDir } = makeBuildDir();
  t.after(() => cleanup(realDir));
  const handle = createStaticFileServer({
    buildDir: realDir,
    env: {},
    hardening: resolveStaticServerHardening({ SOREN_STATIC_MAX_HEADER_BYTES: '200' }),
  });
  const server = await listenEphemeral(handle);
  t.after(() => new Promise((resolve) => server.close(resolve)));
  const port = server.address().port;

  const statusLine = await new Promise((resolve, reject) => {
    const socket = net.connect(port, '127.0.0.1', () => {
      const bigValue = 'x'.repeat(5000);
      socket.write(`GET / HTTP/1.1\r\nHost: localhost\r\nX-Big: ${bigValue}\r\n\r\n`);
    });
    let data = '';
    socket.on('data', (chunk) => { data += chunk.toString(); });
    socket.on('close', () => resolve(data.split('\r\n')[0] || ''));
    socket.on('error', reject);
  });
  assert.match(statusLine, /431/);
});

test('response size cap is enforced end-to-end, not just in the resolver unit test', async (t) => {
  const { realDir } = makeBuildDir();
  t.after(() => cleanup(realDir));
  const handle = createStaticFileServer({
    buildDir: realDir,
    env: {},
    hardening: resolveStaticServerHardening({ SOREN_STATIC_MAX_RESPONSE_BYTES: '4' }),
  });
  const server = await listenEphemeral(handle);
  t.after(() => new Promise((resolve) => server.close(resolve)));
  const port = server.address().port;

  const res = await get(port, '/Build/soren-build.data.gz');
  assert.equal(res.status, 403);
});

test('beforeStatic hook can intercept routes before filesystem resolution (overlay-route parity)', async (t) => {
  const { realDir } = makeBuildDir();
  t.after(() => cleanup(realDir));
  const handle = createStaticFileServer({
    buildDir: realDir,
    env: {},
    beforeStatic(req, res, requestPath) {
      if (requestPath === '/__custom_route') {
        res.writeHead(200, { 'Content-Type': 'application/json' });
        res.end(JSON.stringify({ ok: true }));
        return true;
      }
      return false;
    },
  });
  const server = await listenEphemeral(handle);
  t.after(() => new Promise((resolve) => server.close(resolve)));
  const port = server.address().port;

  const custom = await get(port, '/__custom_route');
  assert.equal(custom.status, 200);
  assert.deepEqual(JSON.parse(custom.body), { ok: true });

  // A non-matching path still falls through to the static file resolver.
  const fallthrough = await get(port, '/index.html');
  assert.equal(fallthrough.status, 200);
});

test('rewriteFile hook can substitute a matched file\'s body (Unity canvas-size rewrite parity)', async (t) => {
  const { realDir } = makeBuildDir();
  t.after(() => cleanup(realDir));
  const handle = createStaticFileServer({
    buildDir: realDir,
    env: {},
    rewriteFile(filePath) {
      if (path.basename(filePath) === 'index.html') return '<!doctype html><rewritten/>';
      return null;
    },
  });
  const server = await listenEphemeral(handle);
  t.after(() => new Promise((resolve) => server.close(resolve)));
  const port = server.address().port;

  const index = await get(port, '/');
  assert.equal(index.status, 200);
  assert.equal(index.body, '<!doctype html><rewritten/>');

  // Non-rewritten files stream unmodified.
  const loader = await get(port, '/Build/soren-build.loader.js');
  assert.equal(loader.body, 'function loader(){}');
});

test('onStartupInfo receives the real bound address/port and a document-root hash, no raw path', async (t) => {
  const { realDir } = makeBuildDir();
  t.after(() => cleanup(realDir));

  let captured = null;
  const handle = createStaticFileServer({
    buildDir: realDir,
    env: {},
    onStartupInfo(info) { captured = info; },
  });
  const server = await listenEphemeral(handle);
  t.after(() => new Promise((resolve) => server.close(resolve)));

  assert.ok(captured);
  assert.equal(captured.boundAddress, '127.0.0.1');
  assert.equal(captured.boundPort, server.address().port);
  assert.match(captured.documentRootHash, /^[0-9a-f]{16}$/);
  assert.equal(captured.documentRootHash, hashDocumentRoot(realDir));
  assert.ok(!JSON.stringify(captured).includes(realDir));
});
