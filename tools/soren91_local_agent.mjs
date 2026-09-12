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
// - Binds 127.0.0.1:19191 by default. Never expose publicly.
// - Every endpoint except GET /health requires
//   `Authorization: Bearer <SOREN91_LOCAL_AGENT_TOKEN>` compared with
//   crypto.timingSafeEqual. Token comes from the environment only.
// - One session at a time (POST /v1/start while running -> 409).
// - POST /v1/start passes process.env straight to the child, so
//   SOREN91_LOCAL_SRT_URL / SOREN91_LOCAL_FFMPEG_BIN etc. are injected via
//   the agent's own environment, never via argv.
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
  if (platform === 'darwin' || platform === 'win32') return options;
  throw new Error(platformErrorMessage(platform));
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

  const server = http.createServer((req, res) => {
    if (req.method === 'GET' && req.url === '/health') {
      return json(res, 200, { ok: true, service: 'soren91-local-agent', backend });
    }
    if (!authorized(req.headers.authorization, options.token)) {
      return json(res, 401, { ok: false, error: 'unauthorized' });
    }
    if (req.method === 'GET' && req.url === '/v1/status') return json(res, 200, currentStatus());
    if (req.method === 'POST' && req.url === '/v1/start') {
      if (child && child.exitCode == null) return json(res, 409, { ok: false, error: 'already running' });
      child = spawnImpl(process.execPath, buildSessionArgs(platform), {
        env: process.env,
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
