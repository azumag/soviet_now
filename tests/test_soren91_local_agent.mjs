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
  MAX_START_BODY_BYTES,
  parseStartBody,
  sessionScriptForPlatform,
  validateOptions,
  validateStartSrtUrl,
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

test('agent bind host is limited to loopback or a Tailscale IPv4 address', () => {
  for (const platform of ['darwin', 'win32']) {
    const tailscale = validateOptions({ host: '100.64.0.3', port: 19191, token: LONG_TOKEN }, platform);
    assert.equal(tailscale.host, '100.64.0.3');
    for (const host of ['0.0.0.0', '8.8.8.8', '203.0.113.7', 'localhost', '::', '::1']) {
      assert.throws(
        () => validateOptions({ host, port: 19191, token: LONG_TOKEN }, platform),
        /127\.0\.0\.1 or a Tailscale IPv4/,
        `unsafe bind host ${host} must fail closed`,
      );
    }
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

// --- POST /v1/start optional { srtUrl } body (additive vs PR #131) ---

test('validateStartSrtUrl accepts Tailscale caller URLs and rejects the rest', () => {
  const good = 'srt://100.71.107.106:19192?mode=caller';
  assert.equal(validateStartSrtUrl(good), good);
  for (const bad of [
    '',
    'http://100.71.107.106:19192?mode=caller',
    'srt://8.8.8.8:19192?mode=caller',
    'srt://100.71.107.106:19192?mode=listener',
    'srt://100.71.107.106:19192',
    'srt://100.71.107.106?mode=caller',
    'srt://user@100.71.107.106:19192?mode=caller',
    'srt://100.71.107.106:19192?mode=caller&passphrase=x',
    'not a url',
  ]) {
    assert.throws(() => validateStartSrtUrl(bad), Error, String(bad));
  }
});

test('parseStartBody: empty body is legacy, srtUrl is validated, garbage is 400-shaped', () => {
  assert.deepEqual(parseStartBody(''), {});
  assert.deepEqual(parseStartBody('   '), {});
  assert.deepEqual(parseStartBody('{}'), {});
  assert.deepEqual(parseStartBody('{"other":1}'), {});
  const good = 'srt://100.71.107.106:19192?mode=caller';
  assert.deepEqual(parseStartBody(JSON.stringify({ srtUrl: good })), { srtUrl: good });
  assert.throws(() => parseStartBody('{oops'), /valid JSON/);
  assert.throws(() => parseStartBody('[1]'), /object/);
  assert.throws(() => parseStartBody(JSON.stringify({ srtUrl: 'srt://8.8.8.8:1?mode=caller' })), /Tailscale/);
});

async function withCapturingServer(platform, fn) {
  const seen = [];
  const options = { host: '127.0.0.1', port: 0, token: LONG_TOKEN };
  const { server } = createServer(options, {
    platform,
    spawnImpl: (bin, args, spawnOptions) => {
      seen.push({ bin, args, env: spawnOptions.env });
      return stubSpawn();
    },
  });
  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
  const base = `http://127.0.0.1:${server.address().port}`;
  try {
    await fn(base, seen);
  } finally {
    await new Promise((resolve) => server.close(resolve));
  }
}

test('HTTP: /v1/start with a valid srtUrl body overrides the child env only', async () => {
  await withCapturingServer('darwin', async (base, seen) => {
    const auth = { authorization: `Bearer ${LONG_TOKEN}`, 'content-type': 'application/json' };
    const srtUrl = 'srt://100.71.107.106:19192?mode=caller';
    const res = await fetch(`${base}/v1/start`, {
      method: 'POST', headers: auth, body: JSON.stringify({ srtUrl }),
    });
    assert.equal(res.status, 202);
    assert.equal(seen.length, 1);
    assert.equal(seen[0].env.SOREN91_LOCAL_SRT_URL, srtUrl);
    // process.env itself is untouched by the override.
    assert.notEqual(process.env.SOREN91_LOCAL_SRT_URL, srtUrl);
  });
});

test('HTTP: /v1/start without a body keeps the legacy process.env spawn', async () => {
  await withCapturingServer('darwin', async (base, seen) => {
    const auth = { authorization: `Bearer ${LONG_TOKEN}` };
    const res = await fetch(`${base}/v1/start`, { method: 'POST', headers: auth });
    assert.equal(res.status, 202);
    assert.equal(seen.length, 1);
    assert.equal(seen[0].env, process.env);
  });
});

test('HTTP: /v1/start rejects invalid and oversized bodies with 400/413', async () => {
  await withServer('darwin', async (base) => {
    const auth = { authorization: `Bearer ${LONG_TOKEN}`, 'content-type': 'application/json' };
    const bad = await fetch(`${base}/v1/start`, {
      method: 'POST', headers: auth,
      body: JSON.stringify({ srtUrl: 'srt://8.8.8.8:19192?mode=caller' }),
    });
    assert.equal(bad.status, 400);
    assert.equal((await bad.json()).ok, false);
  });
  await withServer('darwin', async (base) => {
    const auth = { authorization: `Bearer ${LONG_TOKEN}`, 'content-type': 'application/json' };
    const huge = await fetch(`${base}/v1/start`, {
      method: 'POST', headers: auth, body: 'x'.repeat(MAX_START_BODY_BYTES + 1),
    });
    assert.ok(huge.status === 400 || huge.status === 413);
    assert.equal((await huge.json()).ok, false);
  });
});
