// Contract tests for the OCI-side renderer controller
// (tools/soren91_renderer_controller.mjs, Issue #303). All I/O is mocked:
// fetchImpl/spawnImpl are injected, so no real agent, ffmpeg, or network is
// ever touched (loopback/mocks only).
import test from 'node:test';
import assert from 'node:assert/strict';
import {
  DEFAULT_WAIT_MARGIN_SEC,
  POWERGPU_UNIMPLEMENTED,
  buildCallerSrtUrl,
  buildListenerArgs,
  listenerWaitTimeoutMs,
  parseAgents,
  probeAgent,
  readControllerConfig,
  runController,
  selectBackend,
  startSession,
  stopSession,
  validateTailscaleSrtUrl,
} from '../tools/soren91_renderer_controller.mjs';

const TOKEN = 't'.repeat(32);
const TAIL = '100.71.107.106';

function agentsEnv(list) {
  return { SOREN91_LOCAL_AGENTS_JSON: JSON.stringify(list) };
}

function jsonResponse(status, body) {
  return { status, json: async () => body };
}

// --- parseAgents ---

test('parseAgents returns [] when unset or blank', () => {
  assert.deepEqual(parseAgents({}), []);
  assert.deepEqual(parseAgents({ SOREN91_LOCAL_AGENTS_JSON: '   ' }), []);
});

test('parseAgents accepts macos/windows http agents and strips trailing slashes', () => {
  const agents = parseAgents(agentsEnv([
    { backend: 'local-macos', baseUrl: 'http://100.64.0.3:19191/', token: TOKEN },
    { backend: 'local-windows', baseUrl: 'http://100.64.0.4:19191', token: TOKEN },
  ]));
  assert.equal(agents.length, 2);
  assert.equal(agents[0].baseUrl, 'http://100.64.0.3:19191');
  assert.equal(agents[0].token, TOKEN);
});

test('parseAgents rejects broken configs without leaking tokens', () => {
  const bad = [
    '{not json',
    '"just a string"',
    agentsEnv([{ backend: 'powergpu-x', baseUrl: 'http://100.64.0.3:19191', token: TOKEN }]),
    agentsEnv([{ backend: 'local-macos', baseUrl: 'https://100.64.0.3:19191', token: TOKEN }]),
    agentsEnv([{ backend: 'local-macos', baseUrl: 'http://100.64.0.3:19191', token: 'short' }]),
    agentsEnv([{ backend: 'local-macos', baseUrl: 'not a url', token: TOKEN }]),
    agentsEnv([{ backend: 'local-macos', baseUrl: 'http://user@100.64.0.3:19191', token: TOKEN }]),
  ];
  for (const entry of bad) {
    const env = typeof entry === 'string' && entry.startsWith('{')
      ? { SOREN91_LOCAL_AGENTS_JSON: entry }
      : typeof entry === 'string'
        ? { SOREN91_LOCAL_AGENTS_JSON: entry }
        : entry;
    assert.throws(() => parseAgents(env), (error) => {
      assert.ok(!String(error?.message || '').includes(TOKEN), 'token leaked in error');
      return true;
    });
  }
});

test('readControllerConfig applies defaults and floors duration to 90s', () => {
  const config = readControllerConfig({ SOREN91_OCI_TAILSCALE_IP: TAIL });
  assert.equal(config.port, 19192);
  assert.equal(config.outPath, '/tmp/soren91-local-poc.ts');
  assert.equal(config.durationSec, 90);
  assert.equal(config.waitMarginSec, 240);
  assert.equal(config.ffmpegBin, 'ffmpeg');
  assert.deepEqual(config.agents, []);
  const floored = readControllerConfig({ SOREN91_OCI_TAILSCALE_IP: TAIL, SOREN91_OCI_POC_SEC: '30' });
  assert.equal(floored.durationSec, 90);
  assert.throws(() => readControllerConfig({}), /TAILSCALE_IP/);
  assert.throws(() => readControllerConfig({ SOREN91_OCI_TAILSCALE_IP: '8.8.8.8' }), /Tailscale/);
});

test('readControllerConfig parses SOREN91_OCI_CTRL_WAIT_MARGIN_SEC and rejects out-of-range', () => {
  const over = readControllerConfig({ SOREN91_OCI_TAILSCALE_IP: TAIL, SOREN91_OCI_CTRL_WAIT_MARGIN_SEC: '300' });
  assert.equal(over.waitMarginSec, 300);
  for (const bad of ['59', '1201', 'abc', '1.5', '']) {
    if (bad === '') continue; // blank means "unset" -> default
    assert.throws(
      () => readControllerConfig({ SOREN91_OCI_TAILSCALE_IP: TAIL, SOREN91_OCI_CTRL_WAIT_MARGIN_SEC: bad }),
      /WAIT_MARGIN/,
      `margin ${bad} must throw`,
    );
  }
  // Boundary values are accepted.
  assert.equal(readControllerConfig({ SOREN91_OCI_TAILSCALE_IP: TAIL, SOREN91_OCI_CTRL_WAIT_MARGIN_SEC: '60' }).waitMarginSec, 60);
  assert.equal(readControllerConfig({ SOREN91_OCI_TAILSCALE_IP: TAIL, SOREN91_OCI_CTRL_WAIT_MARGIN_SEC: '1200' }).waitMarginSec, 1200);
  assert.equal(DEFAULT_WAIT_MARGIN_SEC, 240);
});

test('listenerWaitTimeoutMs adds duration and margin', () => {
  assert.equal(listenerWaitTimeoutMs(90, 240), 330_000);
  assert.equal(listenerWaitTimeoutMs(120, 300), 420_000);
  assert.equal(listenerWaitTimeoutMs(90), 330_000);
  assert.throws(() => listenerWaitTimeoutMs(90, 59), /waitMarginSec/);
  assert.throws(() => listenerWaitTimeoutMs(90, 1201), /waitMarginSec/);
  assert.throws(() => listenerWaitTimeoutMs(0, 240), /durationSec/);
});

test('runController plan exposes the wait budget from config', async () => {
  const fetchImpl = async (url) => {
    if (url.endsWith('/health')) return jsonResponse(200, { ok: true });
    return jsonResponse(200, { ok: true, running: false });
  };
  const config = readControllerConfig({
    SOREN91_OCI_TAILSCALE_IP: TAIL,
    SOREN91_OCI_POC_SEC: '120',
    SOREN91_OCI_CTRL_WAIT_MARGIN_SEC: '300',
    SOREN91_LOCAL_AGENTS_JSON: JSON.stringify([MAC]),
  });
  const result = await runController(
    { ...config, execute: false },
    { fetchImpl, spawnImpl: () => { throw new Error('must not spawn in plan mode'); } },
  );
  assert.equal(result.ok, true);
  assert.equal(result.plan.durationSec, 120);
  assert.equal(result.plan.waitMarginSec, 300);
  assert.equal(result.plan.waitTimeoutMs, 420_000);
});

// --- probeAgent ---

function agentOver(over = {}) {
  return { backend: 'local-macos', baseUrl: 'http://100.64.0.3:19191', token: TOKEN, ...over };
}

test('probeAgent reports available/busy from mocked fetch', async () => {
  const idle = await probeAgent(agentOver(), {
    fetchImpl: async (url) => (url.endsWith('/health')
      ? jsonResponse(200, { ok: true })
      : jsonResponse(200, { ok: true, backend: 'local-macos', running: false, pid: null, lastExit: null })),
  });
  assert.deepEqual(idle, { backend: 'local-macos', available: true, busy: false, healthy: true });

  const busy = await probeAgent(agentOver(), {
    fetchImpl: async (url) => (url.endsWith('/health')
      ? jsonResponse(200, { ok: true })
      : jsonResponse(200, { ok: true, running: true, pid: 7 })),
  });
  assert.equal(busy.busy, true);
  assert.equal(busy.available, true);
});

test('probeAgent maps 401/timeout/unreachable to available:false', async () => {
  const denied = await probeAgent(agentOver(), {
    fetchImpl: async (url) => (url.endsWith('/health')
      ? jsonResponse(200, { ok: true })
      : jsonResponse(401, { ok: false })),
  });
  assert.equal(denied.available, false);

  const down = await probeAgent(agentOver(), {
    fetchImpl: async () => { throw new Error('connect refused'); },
  });
  assert.deepEqual(down, { backend: 'local-macos', available: false, busy: false, healthy: false });

  const badHealth = await probeAgent(agentOver(), {
    fetchImpl: async () => jsonResponse(500, {}),
  });
  assert.equal(badHealth.available, false);
});

// --- selectBackend ---

function statusFetch(states) {
  // states: { 'local-macos': {reachable, running} }
  return async (url, options) => {
    const backend = url.includes('100.64.0.3') ? 'local-macos' : 'local-windows';
    const state = states[backend] || {};
    if (url.endsWith('/health')) {
      return state.reachable === false ? jsonResponse(500, {}) : jsonResponse(200, { ok: true });
    }
    if (options?.headers?.authorization !== `Bearer ${TOKEN}`) return jsonResponse(401, {});
    return jsonResponse(200, { ok: true, running: state.running === true });
  };
}

const MAC = { backend: 'local-macos', baseUrl: 'http://100.64.0.3:19191', token: TOKEN };
const WIN = { backend: 'local-windows', baseUrl: 'http://100.64.0.4:19191', token: TOKEN };

test('selectBackend prefers local-macos and avoids busy hosts', async () => {
  const both = await selectBackend([MAC, WIN], { fetchImpl: statusFetch({}) });
  assert.equal(both.chosen.backend, 'local-macos');

  const macBusy = await selectBackend([MAC, WIN], {
    fetchImpl: statusFetch({ 'local-macos': { running: true } }),
  });
  assert.equal(macBusy.chosen.backend, 'local-windows');
});

test('selectBackend with no usable local yields a PowerGPU candidate (unimplemented path)', async () => {
  const down = statusFetch({ 'local-macos': { reachable: false }, 'local-windows': { running: true } });
  const none = await selectBackend([MAC, WIN], { fetchImpl: down });
  assert.equal(none.chosen.backend, null);

  // When both locals are unusable but cloud capacity is offered (Issue #309
  // will supply real availability), the selector falls through to PowerGPU
  // and runController must refuse with the unimplemented code — spawning
  // nothing and starting no session.
  const extraCandidates = [{ backend: 'powergpu-p4-interruptible', available: true, busy: false }];
  const fellThrough = await selectBackend([MAC, WIN], { fetchImpl: down, extraCandidates });
  assert.equal(fellThrough.chosen.backend, 'powergpu-p4-interruptible');

  const calls = [];
  const run = await runController(
    {
      agents: [MAC, WIN], tailscaleIp: TAIL, port: 19192, outPath: '/tmp/x.ts',
      durationSec: 90, execute: true, extraCandidates,
    },
    {
      fetchImpl: async (url, options) => { calls.push({ url, method: options?.method || 'GET' }); return down(url, options); },
      spawnImpl: () => { throw new Error('must not spawn for PowerGPU'); },
    },
  );
  assert.equal(run.ok, false);
  assert.equal(run.code, 'powergpu-unimplemented');
  assert.match(run.error, /Issue #309/);
  assert.ok(calls.every((call) => call.method === 'GET'), 'unimplemented path must not POST');
});

// --- listener args / URL validation ---

test('buildListenerArgs binds the Tailscale IP with -t/-c copy/-y and no passphrase', () => {
  const { bin, args } = buildListenerArgs({
    tailscaleIp: TAIL, port: 19192, outPath: '/tmp/x.ts', durationSec: 90,
  });
  assert.equal(bin, 'ffmpeg');
  const i = args.indexOf('-i');
  assert.ok(i >= 0);
  assert.match(args[i + 1], new RegExp(`^srt://${TAIL.replace(/\./g, '\\.')}:19192\\?mode=listener`));
  assert.ok(!args.join(' ').includes('passphrase'));
  assert.ok(!args.join(' ').includes('0.0.0.0'));
  assert.deepEqual(args.slice(args.indexOf('-t'), args.indexOf('-t') + 2), ['-t', '90']);
  assert.deepEqual(args.slice(args.indexOf('-c'), args.indexOf('-c') + 2), ['-c', 'copy']);
  assert.equal(args[args.length - 1], '/tmp/x.ts');
  assert.ok(args.includes('-y'));
  assert.throws(() => buildListenerArgs({ tailscaleIp: '0.0.0.0', port: 19192, outPath: 'x', durationSec: 90 }), /Tailscale/);
  assert.throws(() => buildListenerArgs({ tailscaleIp: '203.0.113.7', port: 19192, outPath: 'x', durationSec: 90 }), /Tailscale/);
});

test('validateTailscaleSrtUrl rejects public hosts, listener mode, passphrases', () => {
  assert.equal(validateTailscaleSrtUrl(`srt://${TAIL}:19192?mode=caller`), `srt://${TAIL}:19192?mode=caller`);
  assert.equal(buildCallerSrtUrl(TAIL, 19192), `srt://${TAIL}:19192?mode=caller`);
  for (const bad of [
    `srt://${TAIL}:19192?mode=listener`,
    `srt://8.8.8.8:19192?mode=caller`,
    `srt://${TAIL}:19192?mode=caller&passphrase=secret`,
    `srt://user@${TAIL}:19192?mode=caller`,
    `srt://${TAIL}?mode=caller`,
    `srt://${TAIL}:19192`,
    `http://${TAIL}:19192?mode=caller`,
  ]) {
    assert.throws(() => validateTailscaleSrtUrl(bad), Error, bad);
  }
});

// --- start/stop ---

test('startSession POSTs {srtUrl} and maps 409 to alreadyRunning', async () => {
  const seen = [];
  const fetchImpl = async (url, options) => {
    seen.push({ url, options });
    if (seen.length === 1) return jsonResponse(202, { ok: true, started: true });
    return jsonResponse(409, { ok: false, error: 'already running' });
  };
  const srtUrl = `srt://${TAIL}:19192?mode=caller`;
  const first = await startSession(MAC, { srtUrl, fetchImpl });
  assert.deepEqual(first, { started: true, alreadyRunning: false, detail: { ok: true, started: true } });
  assert.equal(seen[0].url, 'http://100.64.0.3:19191/v1/start');
  assert.deepEqual(JSON.parse(seen[0].options.body), { srtUrl });
  assert.equal(seen[0].options.headers.authorization, `Bearer ${TOKEN}`);

  const second = await startSession(MAC, { srtUrl, fetchImpl });
  assert.equal(second.alreadyRunning, true);

  const denied = await startSession(MAC, {
    srtUrl,
    fetchImpl: async () => jsonResponse(401, { ok: false }),
  }).then(() => null, (error) => error);
  assert.match(denied.message, /401/);
  assert.ok(!denied.message.includes(TOKEN));
});

test('stopSession POSTs /v1/stop and throws on non-2xx without leaking the token', async () => {
  const seen = [];
  const ok = await stopSession(MAC, {
    fetchImpl: async (url, options) => { seen.push({ url, options }); return jsonResponse(202, { ok: true }); },
  });
  assert.equal(ok.stopped, true);
  assert.equal(seen[0].url, 'http://100.64.0.3:19191/v1/stop');
  const err = await stopSession(MAC, {
    fetchImpl: async () => jsonResponse(404, { ok: false }),
  }).then(() => null, (error) => error);
  assert.match(err.message, /404/);
  assert.ok(!err.message.includes(TOKEN));
});

// --- runController end-to-end with mocks ---

function mockChild() {
  const listeners = {};
  const child = {
    pid: 4242,
    exitCode: null,
    killCalls: [],
    kill(signal) { this.killCalls.push(signal); return true; },
    once(event, fn) { (listeners[event] ||= []).push(fn); },
    emit(event, ...args) { for (const fn of listeners[event] || []) fn(...args); },
  };
  return child;
}

test('runController plan mode probes but spawns nothing and starts nothing', async () => {
  const calls = [];
  const fetchImpl = async (url, options) => {
    calls.push({ url, method: options?.method || 'GET' });
    if (url.endsWith('/health')) return jsonResponse(200, { ok: true });
    return jsonResponse(200, { ok: true, running: false });
  };
  const result = await runController(
    { agents: [MAC], tailscaleIp: TAIL, port: 19192, outPath: '/tmp/x.ts', durationSec: 90, execute: false },
    { fetchImpl, spawnImpl: () => { throw new Error('must not spawn in plan mode'); } },
  );
  assert.equal(result.ok, true);
  assert.equal(result.mode, 'plan');
  assert.equal(result.chosen.backend, 'local-macos');
  assert.equal(result.plan.srtUrl, `srt://${TAIL}:19192?mode=caller`);
  assert.ok(result.plan.listener.args.includes(`srt://${TAIL}:19192?mode=listener`));
  assert.ok(calls.every((call) => call.method === 'GET'), 'plan must not POST');
});

test('runController execute mode starts then stops the agent and kills the listener', async () => {
  const order = [];
  let child;
  const fetchImpl = async (url, options) => {
    const method = options?.method || 'GET';
    if (url.endsWith('/health')) return jsonResponse(200, { ok: true });
    if (url.endsWith('/v1/status')) return jsonResponse(200, { ok: true, running: false });
    if (url.endsWith('/v1/start')) {
      order.push('start');
      assert.deepEqual(JSON.parse(options.body), { srtUrl: `srt://${TAIL}:19192?mode=caller` });
      // Listener "receives" the stream and finishes right after start.
      setImmediate(() => { child.exitCode = 0; child.emit('exit', 0, null); });
      return jsonResponse(202, { ok: true, started: true });
    }
    if (url.endsWith('/v1/stop')) {
      order.push('stop');
      return jsonResponse(202, { ok: true });
    }
    throw new Error(`unexpected ${method} ${url}`);
  };
  const spawned = [];
  const result = await runController(
    { agents: [MAC], tailscaleIp: TAIL, port: 19192, outPath: '/tmp/x.ts', durationSec: 90, execute: true },
    {
      fetchImpl,
      spawnImpl: (bin, args) => {
        spawned.push({ bin, args });
        child = mockChild();
        return child;
      },
    },
  );
  assert.equal(result.ok, true);
  assert.deepEqual(order, ['start', 'stop']);
  assert.equal(spawned[0].bin, 'ffmpeg');
  assert.ok(spawned[0].args.includes(`srt://${TAIL}:19192?mode=listener`));
  assert.deepEqual(child.killCalls, [], 'clean exit needs no SIGTERM');
});

test('runController execute mode stops the agent and kills the listener on failure', async () => {
  const order = [];
  let child;
  const fetchImpl = async (url) => {
    if (url.endsWith('/health')) return jsonResponse(200, { ok: true });
    if (url.endsWith('/v1/status')) return jsonResponse(200, { ok: true, running: false });
    if (url.endsWith('/v1/start')) {
      order.push('start');
      setImmediate(() => { child.exitCode = 1; child.emit('exit', 1, null); });
      return jsonResponse(202, { ok: true, started: true });
    }
    if (url.endsWith('/v1/stop')) { order.push('stop'); return jsonResponse(202, { ok: true }); }
    throw new Error(`unexpected ${url}`);
  };
  const result = await runController(
    { agents: [MAC], tailscaleIp: TAIL, port: 19192, outPath: '/tmp/x.ts', durationSec: 90, execute: true },
    { fetchImpl, spawnImpl: () => { child = mockChild(); return child; } },
  );
  assert.equal(result.ok, false);
  assert.deepEqual(order, ['start', 'stop']);
});

test('runController reports PowerGPU selection as unimplemented without spawning', async () => {
  const run = await runController(
    { agents: [], tailscaleIp: TAIL, port: 19192, outPath: '/tmp/x.ts', durationSec: 90, execute: true },
    {
      fetchImpl: async () => { throw new Error('no agents means no probes'); },
      spawnImpl: () => { throw new Error('must not spawn'); },
    },
  );
  assert.equal(run.ok, false);
  assert.match(run.error, /no candidate available/);
  assert.equal(POWERGPU_UNIMPLEMENTED, 'powergpu-unimplemented');
});
