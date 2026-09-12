#!/usr/bin/env node
import http from 'node:http';
import path from 'node:path';
import { spawn } from 'node:child_process';
import { timingSafeEqual } from 'node:crypto';
import { fileURLToPath } from 'node:url';

const here = path.dirname(fileURLToPath(import.meta.url));
const sessionScript = path.join(here, 'soren91_windows_session.mjs');

export function defaults(env = process.env) {
  return {
    host: env.SOREN91_LOCAL_AGENT_HOST || '127.0.0.1',
    port: Number(env.SOREN91_LOCAL_AGENT_PORT || 19191),
    token: env.SOREN91_LOCAL_AGENT_TOKEN || '',
  };
}

export function validateOptions(options, platform = process.platform) {
  if (!Number.isInteger(options.port) || options.port < 1024 || options.port > 65535) throw new Error('agent port must be 1024..65535');
  if (options.token.length < 24) throw new Error('SOREN91_LOCAL_AGENT_TOKEN must be at least 24 characters');
  if (platform !== 'win32') throw new Error('local agent must run in an interactive Windows user session');
  return options;
}

export function authorized(header, token) {
  const prefix = 'Bearer ';
  if (typeof header !== 'string' || !header.startsWith(prefix)) return false;
  const supplied = Buffer.from(header.slice(prefix.length));
  const expected = Buffer.from(token);
  return supplied.length === expected.length && timingSafeEqual(supplied, expected);
}

export function buildSessionArgs() {
  return [sessionScript, '--execute'];
}

function json(res, status, value) {
  const body = JSON.stringify(value);
  res.writeHead(status, { 'content-type': 'application/json', 'content-length': Buffer.byteLength(body) });
  res.end(body);
}

export async function main() {
  const options = validateOptions(defaults());
  let child = null;
  let lastExit = null;

  const currentStatus = () => ({
    ok: true,
    backend: 'local-windows',
    running: Boolean(child && child.exitCode == null),
    pid: child?.pid || null,
    lastExit,
  });

  const server = http.createServer((req, res) => {
    if (req.method === 'GET' && req.url === '/health') return json(res, 200, { ok: true, service: 'soren91-local-agent' });
    if (!authorized(req.headers.authorization, options.token)) return json(res, 401, { ok: false, error: 'unauthorized' });
    if (req.method === 'GET' && req.url === '/v1/status') return json(res, 200, currentStatus());
    if (req.method === 'POST' && req.url === '/v1/start') {
      if (child && child.exitCode == null) return json(res, 409, { ok: false, error: 'already running' });
      child = spawn(process.execPath, buildSessionArgs(), { env: process.env, stdio: ['ignore', 'inherit', 'inherit'], windowsHide: false });
      child.once('exit', (code, signal) => { lastExit = { code, signal, at: new Date().toISOString() }; child = null; });
      return json(res, 202, { ok: true, started: true, pid: child.pid });
    }
    if (req.method === 'POST' && req.url === '/v1/stop') {
      if (child && child.exitCode == null) child.kill('SIGTERM');
      return json(res, 202, { ok: true, stopping: true });
    }
    return json(res, 404, { ok: false, error: 'not found' });
  });

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
