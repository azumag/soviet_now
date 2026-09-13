#!/usr/bin/env node
// Soren91 OCI-side renderer controller (Issue #303).
//
// Runs on OCI and wires renderer selection to execution: it probes the Tier -1
// local agents (`tools/soren91_local_agent.mjs` on the Mac / Windows hosts),
// picks a backend via `tools/soren91_renderer_priority.mjs`
// (`selectRendererBackend`), starts an SRT listener on the OCI Tailscale IP,
// tells the chosen local agent to start with the matching caller URL, waits,
// then stops the agent and guarantees the listener is killed.
//
// PowerGPU fallback (actually launching a P4 host) is Issue #309's scope:
// when selection yields a `powergpu-*` backend this controller reports
// "unimplemented" and exits non-zero — it never launches cloud capacity.
//
// Safety:
// - The SRT listener binds the Tailscale IPv4 address ONLY (never 0.0.0.0 or
//   a public address). No SRT passphrase is used (Tailscale is the transport
//   security, same rule as the local session).
// - Agent control URLs must also use a Tailscale IPv4 address, so bearer
//   tokens are never sent over plaintext HTTP to a public/hostname target.
// - Agent tokens come from `SOREN91_LOCAL_AGENTS_JSON` (inject via a secret
//   store) and are NEVER written to argv, logs, or error messages.
// - Listener shutdown is guaranteed with try/finally. The controller sends
//   /v1/stop only for a session it successfully started; a 409 is treated as
//   foreign ownership and is never stopped by this controller.
// - Without `--execute` this only prints the plan as JSON and exits 0
//   (no fetch/spawn side effects beyond agent probes).
//
// Config (env; secrets never via argv):
// - SOREN91_LOCAL_AGENTS_JSON (optional, default []):
//   [{"backend":"local-macos","baseUrl":"http://<tailscale-ip>:19191","token":"..."}, ...]
// - SOREN91_OCI_TAILSCALE_IP (required): OCI Tailscale IPv4 in 100.64.0.0/10.
// - SOREN91_OCI_SRT_PORT (default 19192).
// - SOREN91_OCI_POC_OUT (default /tmp/soren91-local-poc.ts).
// - SOREN91_OCI_POC_SEC (default 90, floored to a minimum of 90).
// - SOREN91_OCI_CTRL_WAIT_MARGIN_SEC (default 240, integer 60..1200):
//   extra seconds the controller waits for the listener beyond durationSec,
//   covering local renderer startup (Unity load + match join + fps measure)
//   which can exceed 60s even when SRT is healthy.
// - SOREN91_OCI_FFMPEG_BIN (default ffmpeg).
import { spawn } from 'node:child_process';
import {
  DEFAULT_LOCAL_ORDER,
  selectRendererBackend,
} from './soren91_renderer_priority.mjs';

export const DEFAULT_SRT_PORT = 19192;
export const DEFAULT_POC_OUT = '/tmp/soren91-local-poc.ts';
export const DEFAULT_POC_SEC = 90;
export const MIN_POC_SEC = 90;
export const DEFAULT_FFMPEG_BIN = 'ffmpeg';
export const POWERGPU_UNIMPLEMENTED = 'powergpu-unimplemented';
export const AGENT_ALREADY_RUNNING = 'agent-already-running';
export const LISTENER_ERROR = 'listener-error';
export const DEFAULT_WAIT_MARGIN_SEC = 240;
export const MIN_WAIT_MARGIN_SEC = 60;
export const MAX_WAIT_MARGIN_SEC = 1200;

const LOCAL_BACKENDS = new Set(['local-macos', 'local-windows']);

export function isTailscaleIpv4(hostname) {
  const octets = String(hostname || '').split('.');
  if (octets.length !== 4 || octets.some((value) => !/^\d{1,3}$/.test(value))) return false;
  const numbers = octets.map(Number);
  if (numbers.some((value) => value < 0 || value > 255)) return false;
  return numbers[0] === 100 && numbers[1] >= 64 && numbers[1] <= 127;
}

function checkedPort(port, label) {
  if (!Number.isInteger(port) || port < 1024 || port > 65535) {
    throw new Error(`${label} must be an integer in 1024..65535`);
  }
  return port;
}

// Parses SOREN91_LOCAL_AGENTS_JSON. Returns [] when unset/blank. Throws on
// anything malformed. Error messages name the entry index and field but
// NEVER include token values.
export function parseAgents(env = process.env) {
  const raw = env?.SOREN91_LOCAL_AGENTS_JSON;
  if (raw == null || String(raw).trim() === '') return [];
  let parsed;
  try { parsed = JSON.parse(String(raw)); } catch {
    throw new Error('SOREN91_LOCAL_AGENTS_JSON must be valid JSON');
  }
  if (!Array.isArray(parsed)) throw new Error('SOREN91_LOCAL_AGENTS_JSON must be a JSON array');
  return parsed.map((entry, index) => {
    const where = `SOREN91_LOCAL_AGENTS_JSON[${index}]`;
    if (!entry || typeof entry !== 'object' || Array.isArray(entry)) {
      throw new Error(`${where} must be an object`);
    }
    if (!LOCAL_BACKENDS.has(entry.backend)) {
      throw new Error(`${where}.backend must be one of local-macos, local-windows`);
    }
    let base;
    try { base = new URL(String(entry.baseUrl)); } catch {
      throw new Error(`${where}.baseUrl must be a valid URL`);
    }
    if (base.protocol !== 'http:') {
      throw new Error(`${where}.baseUrl must use http:// (Tailscale transport, no TLS termination here)`);
    }
    if (base.username || base.password) {
      throw new Error(`${where}.baseUrl must not carry userinfo`);
    }
    if (!isTailscaleIpv4(base.hostname)) {
      throw new Error(`${where}.baseUrl host must be a Tailscale IPv4 address in 100.64.0.0/10`);
    }
    if (!base.port) throw new Error(`${where}.baseUrl must include an explicit port`);
    checkedPort(Number(base.port), `${where}.baseUrl port`);
    if (typeof entry.token !== 'string' || entry.token.length < 24) {
      throw new Error(`${where}.token must be at least 24 characters`);
    }
    return {
      backend: entry.backend,
      baseUrl: String(entry.baseUrl).replace(/\/+$/, ''),
      token: entry.token,
    };
  });
}

export function readControllerConfig(env = process.env) {
  const tailscaleIp = env.SOREN91_OCI_TAILSCALE_IP || '';
  if (!isTailscaleIpv4(tailscaleIp)) {
    throw new Error('SOREN91_OCI_TAILSCALE_IP must be a Tailscale IPv4 address in 100.64.0.0/10');
  }
  const port = checkedPort(
    env.SOREN91_OCI_SRT_PORT == null || env.SOREN91_OCI_SRT_PORT === ''
      ? DEFAULT_SRT_PORT
      : Number(env.SOREN91_OCI_SRT_PORT),
    'SOREN91_OCI_SRT_PORT',
  );
  const outPath = env.SOREN91_OCI_POC_OUT || DEFAULT_POC_OUT;
  if (!outPath || typeof outPath !== 'string') throw new Error('SOREN91_OCI_POC_OUT must be a non-empty path');
  const requestedSec = env.SOREN91_OCI_POC_SEC == null || env.SOREN91_OCI_POC_SEC === ''
    ? DEFAULT_POC_SEC
    : Number(env.SOREN91_OCI_POC_SEC);
  if (!Number.isFinite(requestedSec)) throw new Error('SOREN91_OCI_POC_SEC must be a number');
  const durationSec = Math.max(MIN_POC_SEC, Math.floor(requestedSec));
  const waitMarginSec = env.SOREN91_OCI_CTRL_WAIT_MARGIN_SEC == null || env.SOREN91_OCI_CTRL_WAIT_MARGIN_SEC === ''
    ? DEFAULT_WAIT_MARGIN_SEC
    : Number(env.SOREN91_OCI_CTRL_WAIT_MARGIN_SEC);
  if (!Number.isInteger(waitMarginSec) || waitMarginSec < MIN_WAIT_MARGIN_SEC || waitMarginSec > MAX_WAIT_MARGIN_SEC) {
    throw new Error(`SOREN91_OCI_CTRL_WAIT_MARGIN_SEC must be an integer in ${MIN_WAIT_MARGIN_SEC}..${MAX_WAIT_MARGIN_SEC}`);
  }
  return {
    agents: parseAgents(env),
    tailscaleIp,
    port,
    outPath,
    durationSec,
    waitMarginSec,
    ffmpegBin: env.SOREN91_OCI_FFMPEG_BIN || DEFAULT_FFMPEG_BIN,
  };
}

async function readJsonBody(res) {
  try { return await res.json(); } catch { return null; }
}

// Probes one local agent: GET /health (no auth) then GET /v1/status
// (Bearer). Unreachable agents and non-200 responses yield
// available:false (never throws for transport/HTTP failures).
export async function probeAgent(agent, { fetchImpl = fetch } = {}) {
  const base = agent.baseUrl.replace(/\/+$/, '');
  try {
    const health = await fetchImpl(`${base}/health`);
    if (!health || health.status !== 200) {
      return { backend: agent.backend, available: false, busy: false, healthy: false };
    }
    const status = await fetchImpl(`${base}/v1/status`, {
      headers: { authorization: `Bearer ${agent.token}` },
    });
    if (!status || status.status !== 200) {
      return { backend: agent.backend, available: false, busy: false, healthy: false };
    }
    const body = await readJsonBody(status);
    return {
      backend: agent.backend,
      available: true,
      busy: body?.running === true,
      healthy: true,
    };
  } catch {
    return { backend: agent.backend, available: false, busy: false, healthy: false };
  }
}

// Probes every configured agent and runs the shared selection logic.
// `extraCandidates` are non-agent backends (e.g. PowerGPU availability
// supplied by Issue #309's launcher — none exists yet, so this defaults to
// []) appended to the pool before selection. Returns { chosen, candidates }
// where `chosen` is the selectRendererBackend() result and `candidates` are
// the probe outcomes plus any extras.
export async function selectBackend(
  agents,
  { fetchImpl = fetch, localOrder = DEFAULT_LOCAL_ORDER, extraCandidates = [] } = {},
) {
  const candidates = await Promise.all(agents.map((agent) => probeAgent(agent, { fetchImpl })));
  const pool = [...candidates, ...extraCandidates];
  const chosen = selectRendererBackend(pool, { localOrder });
  return { chosen, candidates: pool };
}

// POSTs /v1/start with body { srtUrl }. A 409 means the agent is already
// running and is returned (not thrown) as { alreadyRunning:true }; callers
// must treat that as foreign ownership and must not stop that session.
export async function startSession(agent, { srtUrl, fetchImpl = fetch } = {}) {
  if (!srtUrl) throw new Error('srtUrl is required');
  const base = agent.baseUrl.replace(/\/+$/, '');
  let res;
  try {
    res = await fetchImpl(`${base}/v1/start`, {
      method: 'POST',
      headers: { authorization: `Bearer ${agent.token}`, 'content-type': 'application/json' },
      body: JSON.stringify({ srtUrl }),
    });
  } catch (error) {
    throw new Error(`POST ${agent.backend} /v1/start failed: ${error?.message || error}`);
  }
  if (res.status === 409) return { started: false, alreadyRunning: true, detail: await readJsonBody(res) };
  if (res.status !== 202 && res.status !== 200) {
    throw new Error(`POST ${agent.backend} /v1/start rejected with HTTP ${res.status}`);
  }
  return { started: true, alreadyRunning: false, detail: await readJsonBody(res) };
}

// POSTs /v1/stop. Non-2xx responses throw (status code only, no token).
export async function stopSession(agent, { fetchImpl = fetch } = {}) {
  const base = agent.baseUrl.replace(/\/+$/, '');
  let res;
  try {
    res = await fetchImpl(`${base}/v1/stop`, {
      method: 'POST',
      headers: { authorization: `Bearer ${agent.token}` },
    });
  } catch (error) {
    throw new Error(`POST ${agent.backend} /v1/stop failed: ${error?.message || error}`);
  }
  if (res.status !== 202 && res.status !== 200) {
    throw new Error(`POST ${agent.backend} /v1/stop rejected with HTTP ${res.status}`);
  }
  return { stopped: true, detail: await readJsonBody(res) };
}

// Validates the caller URL this controller hands to the local agent
// (srt://<oci-tailscale-ip>:<port>?mode=caller). Throws on anything else:
// public hosts, mode=listener, passphrases, userinfo, missing port.
export function validateTailscaleSrtUrl(url) {
  if (typeof url !== 'string' || !url) throw new Error('srtUrl must be a non-empty string');
  let target;
  try { target = new URL(url); } catch { throw new Error('srtUrl must be a valid srt:// URL'); }
  if (target.protocol !== 'srt:') throw new Error('srtUrl must start with srt://');
  if (target.username || target.password) throw new Error('srtUrl userinfo is forbidden');
  if (!target.port) throw new Error('srtUrl must include an explicit destination port');
  if ([...target.searchParams.keys()].some((key) => key.toLowerCase() === 'passphrase')) {
    throw new Error('srtUrl passphrase is forbidden; use Tailscale transport without an SRT passphrase');
  }
  const modes = target.searchParams.getAll('mode');
  if (modes.length !== 1 || modes[0].toLowerCase() !== 'caller') {
    throw new Error('srtUrl must explicitly use mode=caller for the OCI listener');
  }
  checkedPort(Number(target.port), 'srtUrl port');
  if (!isTailscaleIpv4(target.hostname)) {
    throw new Error('srtUrl host must be a Tailscale IPv4 address in 100.64.0.0/10');
  }
  return url;
}

export function buildCallerSrtUrl(tailscaleIp, port) {
  checkedPort(port, 'SRT port');
  if (!isTailscaleIpv4(tailscaleIp)) {
    throw new Error('SRT caller host must be a Tailscale IPv4 address in 100.64.0.0/10');
  }
  return `srt://${tailscaleIp}:${port}?mode=caller`;
}

// Pure: ffmpeg argv for the OCI-side SRT listener. The listener binds the
// Tailscale IP ONLY (never 0.0.0.0 / a public address) and sets no
// passphrase. Returns { bin, args }.
export function buildListenerArgs({ tailscaleIp, port, outPath, durationSec, ffmpegBin = DEFAULT_FFMPEG_BIN } = {}) {
  if (!isTailscaleIpv4(tailscaleIp)) {
    throw new Error('listener must bind a Tailscale IPv4 address in 100.64.0.0/10');
  }
  checkedPort(port, 'listener port');
  if (!outPath || typeof outPath !== 'string') throw new Error('listener outPath must be a non-empty path');
  if (!Number.isInteger(durationSec) || durationSec <= 0) {
    throw new Error('listener durationSec must be a positive integer');
  }
  if (!ffmpegBin || typeof ffmpegBin !== 'string') throw new Error('ffmpegBin must be a non-empty string');
  const listenUrl = `srt://${tailscaleIp}:${port}?mode=listener`;
  return {
    bin: ffmpegBin,
    args: [
      '-hide_banner', '-loglevel', 'warning',
      '-i', listenUrl,
      '-t', String(durationSec),
      '-c', 'copy',
      '-y', outPath,
    ],
  };
}

// Pure: total listener wait budget in ms. The local renderer needs
// (Unity load + match join + fps measure) before SRT flows, which can exceed
// 60s on a healthy run — so the controller waits durationSec + waitMarginSec.
export function listenerWaitTimeoutMs(durationSec, waitMarginSec = DEFAULT_WAIT_MARGIN_SEC) {
  if (!Number.isInteger(durationSec) || durationSec <= 0) {
    throw new Error('listenerWaitTimeoutMs durationSec must be a positive integer');
  }
  if (!Number.isInteger(waitMarginSec) || waitMarginSec < MIN_WAIT_MARGIN_SEC || waitMarginSec > MAX_WAIT_MARGIN_SEC) {
    throw new Error(`listenerWaitTimeoutMs waitMarginSec must be an integer in ${MIN_WAIT_MARGIN_SEC}..${MAX_WAIT_MARGIN_SEC}`);
  }
  return (durationSec + waitMarginSec) * 1000;
}

function waitForExit(child, timeoutMs) {
  return new Promise((resolve) => {
    let done = false;
    const finish = (result) => {
      if (done) return;
      done = true;
      clearTimeout(timer);
      resolve(result);
    };
    const timer = setTimeout(() => finish({ code: null, signal: null, timedOut: true }), timeoutMs);
    if (typeof child.once === 'function') {
      child.once('exit', (code, signal) => finish({ code, signal, timedOut: false }));
      child.once('error', (error) => finish({ code: null, signal: null, timedOut: false, error }));
    } else if (typeof child.then === 'function') {
      child.then(
        (result) => finish({ code: result?.code ?? 0, signal: result?.signal ?? null, timedOut: false }),
        (error) => finish({ code: null, signal: null, timedOut: false, error }),
      );
    } else {
      finish({ code: null, signal: null, timedOut: false });
    }
  });
}

function killListener(child) {
  if (!child) return;
  try {
    if (child.exitCode != null) return;
    if (typeof child.kill === 'function') child.kill('SIGTERM');
  } catch {}
}

// Orchestrates selection -> listener -> agent start -> wait -> agent stop.
// Plan mode (execute:false) performs agent probes + selection only and
// returns the plan as data; NOTHING is spawned and no session is started.
// Execute mode spawns the listener, starts the agent session, waits for the
// listener, stops only the session it owns, and always kills the listener.
export async function runController(
  { agents = [], tailscaleIp, port = DEFAULT_SRT_PORT, outPath = DEFAULT_POC_OUT,
    durationSec = DEFAULT_POC_SEC, waitMarginSec = DEFAULT_WAIT_MARGIN_SEC,
    execute = false, localOrder = DEFAULT_LOCAL_ORDER,
    extraCandidates = [] } = {},
  { fetchImpl = fetch, spawnImpl = spawn, ffmpegBin = DEFAULT_FFMPEG_BIN } = {},
) {
  if (!isTailscaleIpv4(tailscaleIp)) {
    throw new Error('tailscaleIp must be a Tailscale IPv4 address in 100.64.0.0/10');
  }
  checkedPort(port, 'SRT port');
  const { chosen, candidates } = await selectBackend(agents, { fetchImpl, localOrder, extraCandidates });
  if (!chosen.backend) {
    return { ok: false, mode: execute ? 'execute' : 'plan', chosen, candidates, error: 'no candidate available' };
  }
  if (!LOCAL_BACKENDS.has(chosen.backend)) {
    // PowerGPU P4 launch is Issue #309's scope: select-only here.
    return {
      ok: false,
      mode: execute ? 'execute' : 'plan',
      chosen,
      candidates,
      code: POWERGPU_UNIMPLEMENTED,
      error: `backend ${chosen.backend} selected but PowerGPU launch is unimplemented (Issue #309); stopping without spawning anything`,
    };
  }
  const agent = agents.find((entry) => entry.backend === chosen.backend);
  if (!agent) {
    return { ok: false, mode: execute ? 'execute' : 'plan', chosen, candidates, error: `no configured agent for backend ${chosen.backend}` };
  }
  const srtUrl = validateTailscaleSrtUrl(buildCallerSrtUrl(tailscaleIp, port));
  const listener = buildListenerArgs({ tailscaleIp, port, outPath, durationSec, ffmpegBin });
  const waitTimeoutMs = listenerWaitTimeoutMs(durationSec, waitMarginSec);
  if (!execute) {
    return {
      ok: true,
      mode: 'plan',
      chosen,
      candidates,
      plan: {
        listener: { bin: listener.bin, args: listener.args },
        agentBackend: agent.backend,
        agentBaseUrl: agent.baseUrl,
        srtUrl,
        durationSec,
        waitMarginSec,
        waitTimeoutMs,
        outPath,
      },
    };
  }
  let child = null;
  let start = null;
  let listenerResult = null;
  try {
    child = spawnImpl(listener.bin, listener.args, { stdio: ['ignore', 'inherit', 'inherit'] });
    // Attach exit/error listeners immediately. A missing/broken ffmpeg can
    // emit `error` before the agent POST resolves; delaying this listener
    // would turn a controlled failure into an unhandled EventEmitter error.
    const listenerExit = waitForExit(child, waitTimeoutMs);
    start = await startSession(agent, { srtUrl, fetchImpl });
    if (start.alreadyRunning) {
      return {
        ok: false,
        mode: 'execute',
        chosen,
        candidates,
        start,
        code: AGENT_ALREADY_RUNNING,
        error: 'selected agent became busy before start; refusing to stop a session this controller does not own',
      };
    }
    listenerResult = await listenerExit;
    if (listenerResult.timedOut) {
      return { ok: false, mode: 'execute', chosen, candidates, start, listener: listenerResult, error: 'listener timed out' };
    }
    if (listenerResult.error) {
      return {
        ok: false, mode: 'execute', chosen, candidates, start, listener: { ...listenerResult, error: undefined },
        code: LISTENER_ERROR,
        error: 'listener process failed to start or run',
      };
    }
    if (listenerResult.code !== 0) {
      return {
        ok: false, mode: 'execute', chosen, candidates, start, listener: listenerResult,
        error: `listener exited with code ${listenerResult.code}`,
      };
    }
    return { ok: true, mode: 'execute', chosen, candidates, start, listener: listenerResult, srtUrl, outPath };
  } finally {
    try {
      if (start?.started) await stopSession(agent, { fetchImpl });
    } catch {}
    killListener(child);
  }
}

export async function main(argv = process.argv.slice(2), env = process.env) {
  const execute = argv.includes('--execute');
  let config;
  try {
    config = readControllerConfig(env);
  } catch (error) {
    console.error(`soren91 renderer controller: ${error?.message || error}`);
    process.exitCode = 2;
    return { ok: false, error: error?.message || String(error) };
  }
  let result;
  try {
    result = await runController({ ...config, execute }, {});
  } catch (error) {
    // Tokens never reach here: none of the helpers interpolate them.
    console.error(`soren91 renderer controller: ${error?.message || error}`);
    process.exitCode = 1;
    return { ok: false, error: error?.message || String(error) };
  }
  // Token hygiene for the plan print: config holds secrets, so print only
  // the redacted result shape (baseUrls and SRT URLs carry no secrets).
  console.log(JSON.stringify(result, null, 2));
  if (!result.ok) process.exitCode = 1;
  return result;
}

const invokedAsMain = process.argv[1] && import.meta.url === `file://${process.argv[1]}`;
if (invokedAsMain) {
  main().catch((error) => {
    console.error(`soren91 renderer controller: ${error?.message || error}`);
    process.exitCode = 1;
  });
}
