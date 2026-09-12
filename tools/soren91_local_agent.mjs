#!/usr/bin/env node
// Soren91 local agent (platform-generic).
//
// Shared HTTP control agent for Tier -1 local renderer hosts. Converges the
// Windows implementation from PR #131
// (`tools/soren91_local_agent.mjs` on `origin/pr-131`, `backend:
// 'local-windows'`) with the macOS host added by Issue #303 (`backend:
// 'local-macos'`, session `tools/soren91_macos_session.mjs`).
//
// Security contract (same as PR #131, both platforms):
// - Binds 127.0.0.1:19191 by default. A non-loopback bind is allowed only on
//   a Tailscale IPv4 address; wildcard/public/hostname binds fail closed.
// - Every endpoint except GET /health requires
//   `Authorization: Bearer <SOREN91_LOCAL_AGENT_TOKEN>` compared with
//   crypto.timingSafeEqual. Token comes from the environment only.
// - One session at a time (POST /v1/start while running -> 409).
// - POST /v1/start passes process.env straight to the child, so
//   SOREN91_LOCAL_SRT_URL / SOREN91_LOCAL_FFMPEG_BIN etc. are injected via
//   the agent's own environment, never via argv.
// - Additive extension vs PR #131 (whose `/v1/start` ignores any body):
//   POST /v1/start ALSO accepts an optional JSON body `{ "srtUrl": ... }`
//   (max 8KB). When present and valid, the value overrides
//   SOREN91_LOCAL_SRT_URL in the SPAWNED CHILD's env only (process.env is
//   never mutated). Absent/empty body keeps the legacy behavior above.
import http from 'node:http';
import path from 'node:path';
import { spawn, spawnSync } from 'node:child_process';
import { timingSafeEqual } from 'node:crypto';
import { fileURLToPath } from 'node:url';

const here = path.dirname(fileURLToPath(import.meta.url));

export function backendForPlatform(platform = process.platform) {
  if (platform === 'darwin') return 'local-macos';
  if (platform === 'win32') return 'local-windows';
  throw new Error(`unsupported platform for local agent: ${platform}`);
}

export function sessionScriptForPlatform(platform = process.platform, baseDir = here) {
  if (platform === 'darwin') return path.join(baseDir, 'soren91_macos_session.mjs');
  if (platform === 'win32') return path.join(baseDir, 'soren91_windows_session.mjs');
  throw new Error(`unsupported platform for local agent: ${platform}`);
}

export function defaults(env = process.env) {
  return {
    host: env.SOREN91_LOCAL_AGENT_HOST || '127.0.0.1',
    port: Number(env.SOREN91_LOCAL_AGENT_PORT || 19191),
    token: env.SOREN91_LOCAL_AGENT_TOKEN || '',
  };
}

export function validateOptions(options, platform = process.platform) {
  if (!Number.isInteger(options.port) || options.port < 1024 || options.port > 65535) {
    throw new Error('agent port must be 1024..65535');
  }
  if (typeof options.token !== 'string' || options.token.length < 24) {
    throw new Error('SOREN91_LOCAL_AGENT_TOKEN must be at least 24 characters');
  }
  if (platform !== 'darwin' && platform !== 'win32') throw new Error(platformErrorMessage(platform));
  const host = options.host || '127.0.0.1';
  if (host !== '127.0.0.1' && !isTailscaleIpv4Hostname(host)) {
    throw new Error('agent host must be 127.0.0.1 or a Tailscale IPv4 address in 100.64.0.0/10');
  }
  return { ...options, host };
}

function platformErrorMessage(platform) {
  return `local agent must run in an interactive user session (unsupported platform: ${platform}; supported: darwin/macOS, win32/Windows)`;
}

function assertPlatformSupported(platform) {
  if (platform !== 'darwin' && platform !== 'win32') throw new Error(platformErrorMessage(platform));
}

export function authorized(header, token) {
  const prefix = 'Bearer ';
  if (typeof header !== 'string' || !header.startsWith(prefix)) return false;
  const supplied = Buffer.from(header.slice(prefix.length));
  const expected = Buffer.from(token);
  return supplied.length === expected.length && timingSafeEqual(supplied, expected);
}

export function buildSessionArgs(platform = process.platform, baseDir = here) {
  return [sessionScriptForPlatform(platform, baseDir), '--execute'];
}

// Max JSON body accepted by POST /v1/start (additive srtUrl extension).
export const MAX_START_BODY_BYTES = 8192;

export function isTailscaleIpv4Hostname(hostname) {
  const octets = String(hostname || '').split('.');
  if (octets.length !== 4 || octets.some((value) => !/^\d{1,3}$/.test(value))) return false;
  const numbers = octets.map(Number);
  if (numbers.some((value) => value < 0 || value > 255)) return false;
  return numbers[0] === 100 && numbers[1] >= 64 && numbers[1] <= 127;
}

// Validates the optional `srtUrl` from the POST /v1/start JSON body. Same
// rules as soren91_macos_session.mjs validateOptions (Tailscale IPv4 caller
// URL, explicit port, no userinfo, no passphrase) so the controller can hand
// the OCI listener URL to the agent without reimplementation drift. Throws
// on invalid input; returns the URL string when valid.
export function validateStartSrtUrl(url) {
  if (typeof url !== 'string' || !url) throw new Error('srtUrl must be a non-empty string');
  let target;
  try { target = new URL(url); } catch { throw new Error('srtUrl must be a valid srt:// URL'); }
  if (target.protocol !== 'srt:') throw new Error('srtUrl must start with srt://');
  if (target.username || target.password) {
    throw new Error('srtUrl userinfo is forbidden; credentials must not be carried in the request body');
  }
  if (!target.port) throw new Error('srtUrl must include an explicit destination port');
  if ([...target.searchParams.keys()].some((key) => key.toLowerCase() === 'passphrase')) {
    throw new Error('srtUrl passphrase is forbidden; use Tailscale transport without an SRT passphrase');
  }
  const modes = target.searchParams.getAll('mode');
  if (modes.length !== 1 || modes[0].toLowerCase() !== 'caller') {
    throw new Error('srtUrl must explicitly use mode=caller for the OCI listener');
  }
  if (!isTailscaleIpv4Hostname(target.hostname)) {
    throw new Error('srtUrl host must be a Tailscale IPv4 address in 100.64.0.0/10');
  }
  return url;
}

// Parses the optional POST /v1/start body. Empty/absent body -> {} (legacy:
// spawn with process.env only). A present body must be a JSON object; when
// it carries `srtUrl`, the value is validated. Anything else throws with a
// message safe to return as `{ok:false,error}`.
export function parseStartBody(raw) {
  const text = raw == null ? '' : String(raw);
  if (!text.trim()) return {};
  let parsed;
  try { parsed = JSON.parse(text); } catch { throw new Error('request body must be valid JSON'); }
  if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
    throw new Error('request body must be a JSON object');
  }
  if (parsed.srtUrl == null) return {};
  return { srtUrl: validateStartSrtUrl(parsed.srtUrl) };
}

function readBody(req, limit = MAX_START_BODY_BYTES) {
  return new Promise((resolve, reject) => {
    const chunks = [];
    let size = 0;
    let tooLarge = false;
    req.on('data', (chunk) => {
      if (tooLarge) return;
      size += chunk.length;
      if (size > limit) {
        tooLarge = true;
        reject(new Error('request body too large'));
        return;
      }
      chunks.push(chunk);
    });
    req.on('end', () => {
      if (!tooLarge) resolve(Buffer.concat(chunks).toString('utf8'));
    });
    req.on('error', reject);
  });
}

export function stopProcessTree(child, platform = process.platform) {
  if (!child || child.exitCode != null) return;
  if (platform === 'win32' && child.pid) {
    spawnSync('taskkill', ['/PID', String(child.pid), '/T', '/F'], { stdio: 'ignore', windowsHide: true });
    return;
  }
  try { child.kill('SIGTERM'); } catch {}
}

function json(res, status, value) {
  const body = JSON.stringify(value);
  res.writeHead(status, { 'content-type': 'application/json', 'content-length': Buffer.byteLength(body) });
  res.end(body);
}

// Creates the HTTP server without listening, so tests can bind an ephemeral
// port. `spawnImpl` is injectable so tests never launch a real session.
export function createServer(options, { platform = process.platform, spawnImpl = spawn } = {}) {
  assertPlatformSupported(platform);
  const backend = backendForPlatform(platform);
  let child = null;
  let lastExit = null;

  const currentStatus = () => ({
    ok: true,
    backend,
    running: Boolean(child && child.exitCode == null),
    pid: child?.pid || null,
    lastExit,
  });

  const server = http.createServer(async (req, res) => {
    if (req.method === 'GET' && req.url === '/health') {
      return json(res, 200, { ok: true, service: 'soren91-local-agent', backend });
    }
    if (!authorized(req.headers.authorization, options.token)) {
      return json(res, 401, { ok: false, error: 'unauthorized' });
    }
    if (req.method === 'GET' && req.url === '/v1/status') return json(res, 200, currentStatus());
    if (req.method === 'POST' && req.url === '/v1/start') {
      if (child && child.exitCode == null) return json(res, 409, { ok: false, error: 'already running' });
      // Additive vs PR #131 (which ignores the body): an optional JSON body
      // `{ "srtUrl": ... }` overrides SOREN91_LOCAL_SRT_URL for the spawned
      // child only. process.env itself is never mutated.
      let raw = '';
      try {
        raw = await readBody(req);
      } catch (error) {
        const status = /too large/.test(error?.message || '') ? 413 : 400;
        return json(res, status, { ok: false, error: error?.message || 'invalid request body' });
      }
      let srtUrl = null;
      try {
        ({ srtUrl = null } = parseStartBody(raw));
      } catch (error) {
        return json(res, 400, { ok: false, error: error?.message || 'invalid request body' });
      }
      const childEnv = srtUrl ? { ...process.env, SOREN91_LOCAL_SRT_URL: srtUrl } : process.env;
      child = spawnImpl(process.execPath, buildSessionArgs(platform), {
        env: childEnv,
        stdio: ['ignore', 'inherit', 'inherit'],
        windowsHide: platform === 'win32' ? false : undefined,
      });
      child.once('exit', (code, signal) => {
        lastExit = { code, signal, at: new Date().toISOString() };
        child = null;
      });
      return json(res, 202, { ok: true, started: true, pid: child.pid });
    }
    if (req.method === 'POST' && req.url === '/v1/stop') {
      stopProcessTree(child, platform);
      return json(res, 202, { ok: true, stopping: true });
    }
    return json(res, 404, { ok: false, error: 'not found' });
  });

  return { server, currentStatus };
}

export async function main() {
  const options = validateOptions(defaults());
  const platform = process.platform;
  assertPlatformSupported(platform);
  const { server } = createServer(options, { platform });
  server.listen(options.port, options.host, () => {
    console.log(`soren91 local agent listening on http://${options.host}:${options.port}`);
  });
}

if (process.argv[1] && fileURLToPath(import.meta.url) === path.resolve(process.argv[1])) {
  main().catch((error) => {
    console.error(error?.stack || error);
    process.exitCode = 1;
  });
}
