// Contract tests for the platform-generic soren91 local agent
// (tools/soren91_local_agent.mjs). Ported from PR #131's
// tests/test_soren91_local_agent.mjs and extended for darwin/local-macos.
// Runs on any platform: the target platform is always passed explicitly.
import test from 'node:test';
import assert from 'node:assert/strict';
import {
  authorized,
  backendForPlatform,
  buildSessionArgs,
  createServer,
  defaults,
  sessionScriptForPlatform,
  validateOptions,
} from '../tools/soren91_local_agent.mjs';

const LONG_TOKEN = 'a'.repeat(32);

test('agent binds loopback by default and requires a long token', () => {
  for (const platform of ['darwin', 'win32']) {
    const options = validateOptions({ ...defaults({}), token: LONG_TOKEN }, platform);
    assert.equal(options.host, '127.0.0.1');
    assert.equal(options.port, 19191);
    assert.throws(() => validateOptions({ ...options, token: 'short' }, platform), /24 characters/);
  }
});

test('port must be 1024..65535 on both platforms', () => {
  for (const platform of ['darwin', 'win32']) {
    assert.throws(() => validateOptions({ port: 80, token: LONG_TOKEN }, platform), /1024/);
    assert.throws(() => validateOptions({ port: 70000, token: LONG_TOKEN }, platform), /65535/);
    assert.throws(() => validateOptions({ port: NaN, token: LONG_TOKEN }, platform), /1024/);
  }
});

test('unsupported platforms are rejected', () => {
  assert.throws(() => backendForPlatform('linux'), /unsupported platform/);
  assert.throws(() => sessionScriptForPlatform('linux', '/tmp'), /unsupported platform/);
  assert.throws(
    () => validateOptions({ port: 19191, token: LONG_TOKEN }, 'linux'),
    /interactive user session/,
  );
  assert.throws(() => buildSessionArgs('linux', '/tmp'), /unsupported platform/);
});

test('backend and session script follow the platform', () => {
  assert.equal(backendForPlatform('darwin'), 'local-macos');
  assert.equal(backendForPlatform('win32'), 'local-windows');
  assert.match(sessionScriptForPlatform('darwin', '/base'), /soren91_macos_session\.mjs$/);
  assert.match(sessionScriptForPlatform('win32', '/base'), /soren91_windows_session\.mjs$/);
  assert.ok(sessionScriptForPlatform('darwin', '/base').startsWith('/base'));
});

test('Bearer token comparison rejects missing and wrong tokens', () => {
  const token = 'x'.repeat(32);
  assert.equal(authorized(undefined, token), false);
  assert.equal(authorized('Token abc', token), false);
  assert.equal(authorized(`Bearer ${'y'.repeat(32)}`, token), false);
  assert.equal(authorized(`Bearer ${token}`, token), true);
  // Different length must not throw (no timingSafeEqual length crash).
  assert.equal(authorized('Bearer short', token), false);
});

test('agent only launches the bounded session entry point for its platform', () => {
  const macArgs = buildSessionArgs('darwin', '/base');
  assert.match(macArgs[0], /soren91_macos_session\.mjs$/);
  assert.deepEqual(macArgs.slice(1), ['--execute']);
  const winArgs = buildSessionArgs('win32', '/base');
  assert.match(winArgs[0], /soren91_windows_session\.mjs$/);
  assert.deepEqual(winArgs.slice(1), ['--execute']);
  // Never imports the session file at the top level: only a path string.
  assert.equal(typeof macArgs[0], 'string');
});

// --- HTTP contract (ephemeral port, stubbed spawn so no session launches) ---

function stubSpawn() {
  const child = {
    pid: 424242,
    exitCode: null,
    once() {},
    kill() { return true; },
  };
  return child;
}

async function withServer(platform, fn) {
  const options = { host: '127.0.0.1', port: 0, token: LONG_TOKEN };
  const { server } = createServer(options, { platform, spawnImpl: () => stubSpawn() });
  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
  const base = `http://127.0.0.1:${server.address().port}`;
  try {
    await fn(base);
  } finally {
    await new Promise((resolve) => server.close(resolve));
  }
}

test('HTTP: /health needs no auth and reports the platform backend', async () => {
  await withServer('darwin', async (base) => {
    const res = await fetch(`${base}/health`);
    assert.equal(res.status, 200);
    const body = await res.json();
    assert.equal(body.ok, true);
    assert.equal(body.service, 'soren91-local-agent');
    assert.equal(body.backend, 'local-macos');
  });
  await withServer('win32', async (base) => {
    const res = await fetch(`${base}/health`);
    assert.equal((await res.json()).backend, 'local-windows');
  });
});

test('HTTP: protected endpoints reject unauthenticated callers with 401', async () => {
  await withServer('darwin', async (base) => {
    for (const [method, path] of [['GET', '/v1/status'], ['POST', '/v1/start'], ['POST', '/v1/stop']]) {
      const res = await fetch(`${base}${path}`, { method });
      assert.equal(res.status, 401, `${method} ${path}`);
    }
    const wrong = await fetch(`${base}/v1/status`, {
      headers: { authorization: `Bearer ${'z'.repeat(32)}` },
    });
    assert.equal(wrong.status, 401);
  });
});

test('HTTP: second start conflicts with 409; unknown paths 404', async () => {
  await withServer('darwin', async (base) => {
    const auth = { authorization: `Bearer ${LONG_TOKEN}` };
    const status = await fetch(`${base}/v1/status`, { headers: auth });
    assert.equal(status.status, 200);
    assert.deepEqual(await status.json(), {
      ok: true, backend: 'local-macos', running: false, pid: null, lastExit: null,
    });
    const first = await fetch(`${base}/v1/start`, { method: 'POST', headers: auth });
    assert.equal(first.status, 202);
    assert.equal((await first.json()).started, true);
    const second = await fetch(`${base}/v1/start`, { method: 'POST', headers: auth });
    assert.equal(second.status, 409);
    assert.equal((await second.json()).error, 'already running');
    const stop = await fetch(`${base}/v1/stop`, { method: 'POST', headers: auth });
    assert.equal(stop.status, 202);
    const unknown = await fetch(`${base}/v1/nope`, { headers: auth });
    assert.equal(unknown.status, 404);
  });
});
