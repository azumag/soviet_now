#!/usr/bin/env node
// Soren91 macOS Chrome-scoped audio tap: pure, testable logic (Issue #303).
//
// Privacy design: the tap target is ONLY the descendant Chrome processes of
// the Playwright-driven automation Chrome (the session spawns the renderer,
// so the renderer's PID roots a `ps` parent/child walk). There is NO
// bundle-ID-wide fallback — tapping `com.google.Chrome` as a whole would
// also capture (and physically mute) the operator's everyday Chrome. An
// empty or ambiguous PID set is fail-closed: the session must abort instead
// of tapping.
//
// The realtime path lives in tools/macos/soren91_audio_tap.swift (built by
// tools/soren91_audio_tap_build.sh); this module only computes which PIDs to
// tap and how to wire the helper's stdout into ffmpeg's fd 3.
import { spawn } from 'node:child_process';
import readline from 'node:readline';

// The tap is ON by default; only an explicit opt-out (`0`/`false`/`no`/
// `off`, i.e. `SOREN91_LOCAL_AUDIO_TAP=0`) keeps the silent path
// (`--mute-audio`, no audio input).
export function shouldTapAudio(value) {
  if (value == null || value === '') return true;
  return !/^(0|false|no|off)$/i.test(String(value).trim());
}

// Parses `ps -ax -o pid,ppid,command` output into [{ pid, ppid, command }].
// Skips the header line and any unparsable rows (fail-closed callers treat a
// missing PID as "not found", never as a wildcard).
export function parseProcessTable(psOutput) {
  const rows = [];
  for (const line of String(psOutput || '').split('\n')) {
    const trimmed = line.trim();
    if (!trimmed) continue;
    const match = trimmed.match(/^(\d+)\s+(\d+)\s+(.*\S)\s*$/);
    if (!match) continue;
    if (/^pid\s/i.test(trimmed)) continue; // `PID PPID COMMAND` header
    rows.push({
      pid: Number(match[1]),
      ppid: Number(match[2]),
      command: match[3],
    });
  }
  return rows;
}

function isChromeCommand(command) {
  // Matches the app bundle path AND helper/utility processes
  // (".../Google Chrome.app/...", "Google Chrome Helper ..."). The real game
  // audio comes from a `com.google.Chrome.helper` AudioService utility
  // process, which also carries this marker in its command line.
  return /Google Chrome/.test(String(command || ''));
}

// Returns the PIDs to tap: the root's Chrome-family descendants (plus the
// root itself when it is a Chrome process — e.g. a Chrome main process
// handed over directly). `rootPid` is the automation renderer's PID, so
// everyday Chrome processes parented elsewhere are never included.
// Cycle-safe (visited set); unknown rootPids yield [] (fail-closed upstream).
export function collectDescendantChromePids(psOutput, rootPid) {
  const root = Number(rootPid);
  if (!Number.isInteger(root) || root <= 0) return [];
  const rows = parseProcessTable(psOutput);
  const byPid = new Map();
  const children = new Map(); // ppid -> [pid]
  for (const row of rows) {
    if (!byPid.has(row.pid)) byPid.set(row.pid, row);
    if (!children.has(row.ppid)) children.set(row.ppid, []);
    if (!children.get(row.ppid).includes(row.pid)) children.get(row.ppid).push(row.pid);
  }
  if (!byPid.has(root)) return [];
  const out = [];
  const visited = new Set([root]);
  const queue = [root];
  while (queue.length > 0) {
    const current = queue.shift();
    const row = byPid.get(current);
    if (row && isChromeCommand(row.command) && !out.includes(current)) out.push(current);
    for (const child of children.get(current) || []) {
      if (!visited.has(child)) {
        visited.add(child);
        queue.push(child);
      }
    }
  }
  return out.sort((a, b) => a - b);
}

// Throws fail-closed when no tappable PID survives (the session must abort
// instead of proceeding with a widened or silent tap presented as success).
export function resolveTapPids(psOutput, rootPid) {
  const pids = collectDescendantChromePids(psOutput, rootPid);
  if (pids.length === 0) {
    throw new Error(
      `no automation-Chrome descendant PIDs under rootPid=${rootPid} (fail-closed: refusing to tap without explicit PIDs)`,
    );
  }
  return pids;
}

// CLI args for the audio helper: one --pid per tapped PID (repeatable).
// `seconds` is omitted by the session (unbounded; SIGTERM ends capture).
export function buildAudioTapArgs(pids, { seconds = null } = {}) {
  const list = [...pids];
  if (list.length === 0) throw new Error('buildAudioTapArgs requires at least one PID (fail-closed)');
  const args = [];
  for (const pid of list) args.push('--pid', String(pid));
  if (seconds != null) args.push('--seconds', String(seconds));
  return args;
}

// ffmpeg audio input reading the helper's stdout via fd 3:
// `-f s16le -ar 48000 -ac 2 -i pipe:3` (matches the helper's PCM contract).
export function buildAudioFfmpegInputArgs() {
  return ['-f', 's16le', '-ar', '48000', '-ac', '2', '-i', 'pipe:3'];
}

// ffmpeg stdio with the extra audio pipe: index 3 becomes fd 3 for pipe:3.
export function buildFfmpegStdio(audioTap) {
  if (audioTap) return ['pipe', 'inherit', 'inherit', 'pipe'];
  return ['pipe', 'inherit', 'inherit'];
}

// Reads the audio helper's first stderr line as its readiness/failure
// signal. Fail-closed: any non-ok payload, non-JSON line, ok:true without a
// tapUID, or a muteBehaviorVerified other than CATapMutedWhenTapped (2)
// throws — callers must never pipe audio on ambiguous status.
export function parseAudioTapStatus(line) {
  let payload;
  try {
    payload = JSON.parse(line);
  } catch {
    throw new Error(`audio tap helper emitted non-JSON status: ${line}`);
  }
  if (payload?.ok !== true) {
    throw new Error(`audio tap helper failed (fail-closed): ${payload?.error || JSON.stringify(payload)}`);
  }
  if (typeof payload?.tapUID !== 'string' || !payload.tapUID) {
    throw new Error(`audio tap helper status lacks tapUID (fail-closed): ${line}`);
  }
  if (payload?.muteBehaviorVerified !== 2) {
    throw new Error(
      `audio tap helper muteBehavior not verified as CATapMutedWhenTapped (fail-closed): ${line}`,
    );
  }
  return payload;
}

// Spawns the audio helper and waits for its first stderr line (readiness or
// failure). Only after an ok:true status is the pipe safe to wire into
// ffmpeg's fd 3 — see parseAudioTapStatus.
export async function startAudioTap(bin, pids, { timeoutMs = 30_000, spawnImpl = spawn, seconds = null } = {}) {
  const child = spawnImpl(bin, buildAudioTapArgs(pids, { seconds }), {
    stdio: ['ignore', 'pipe', 'pipe'],
  });
  const rl = readline.createInterface({ input: child.stderr });
  const status = await new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error('audio tap helper readiness timed out')), timeoutMs);
    let settled = false;
    rl.once('line', (line) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      try {
        resolve(parseAudioTapStatus(line));
      } catch (error) {
        reject(error);
      }
    });
    child.once('exit', (code) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      reject(new Error(`audio tap helper exited before readiness (code=${code})`));
    });
    child.once('error', (error) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      reject(error);
    });
  });
  rl.on('line', (line) => console.error(`[audiotap] ${line}`));
  return { child, status };
}

// Stops the helper and waits for its exit. Its teardown (IOProc stop ->
// aggregate destroy -> tap destroy) releases the CATapMutedWhenTapped
// physical mute, so the caller must await this before returning.
export async function stopAudioTap(child, { timeoutMs = 10_000 } = {}) {
  if (!child || child.exitCode != null || child.killed) return { exited: true };
  try {
    child.kill('SIGTERM');
  } catch {
    return { exited: false };
  }
  const exited = await new Promise((resolve) => {
    if (child.exitCode != null) return resolve(true);
    const timer = setTimeout(() => resolve(false), timeoutMs);
    child.once('exit', () => {
      clearTimeout(timer);
      resolve(true);
    });
  });
  if (!exited) {
    try {
      child.kill('SIGKILL');
    } catch {}
    await new Promise((resolve) => {
      if (child.exitCode != null) return resolve();
      child.once('exit', () => resolve());
    });
  }
  return { exited: true };
}
