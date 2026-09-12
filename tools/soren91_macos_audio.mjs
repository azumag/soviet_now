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
import { spawn, spawnSync } from 'node:child_process';
import readline from 'node:readline';

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

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

// Benign pipe errors: the receiving end (ffmpeg) went away first — e.g. the
// OCI listener closed the SRT session, ffmpeg exited, and in-flight frame /
// PCM writes then hit a closed pipe. These must be swallowed (debug log
// only), never thrown or left unhandled (an unhandled 'error' on a stream
// crashes node via node:events). Anything else is recorded by the caller.
export function isBenignPipeError(error) {
  const code = error?.code;
  return code === 'EPIPE'
    || code === 'ERR_STREAM_DESTROYED'
    || code === 'ERR_STREAM_WRITE_AFTER_END';
}

// Match only receiver-close signatures observed from the SRT output path.
// Do not use producer-side EPIPE/sinkClosed as proof: every ffmpeg failure
// closes its stdin, so treating that as a remote-consumer close would mask
// encoder/muxer failures. Likewise, a generic "muxer" substring is too broad
// (e.g. queue overflow is a real failure and must stay fail-closed).
function hasRemoteConsumerCloseMarker(stderr) {
  const text = String(stderr || '');
  return /broken pipe/i.test(text)
    || /error submitting a packet to the muxer:\s*input\/output error/i.test(text)
    || /av_interleaved_write_frame\(\):\s*input\/output error/i.test(text);
}

// Classifies an ffmpeg process exit observed BEFORE the session deadline.
// Pure function (Issue #303: the OCI listener's `-t 120` close makes ffmpeg
// die with an output-side EPIPE/I/O marker — a normal end of stream, not a
// failure). Returns one of:
//   'deadline'        — deadline path won (caller treats as normal end).
//   'consumer-closed' — ffmpeg stderr contains an explicit receiver-close
//                       signature from the SRT output path. Normal end.
//   'failed'          — any other early exit, non-zero exit, or signal death
//                       (caller must keep throwing, as before).
export function classifyFfmpegExit({
  code = null,
  signal = null,
  stderr = '',
  deadlineReached = false,
} = {}) {
  if (deadlineReached) return 'deadline';
  if (signal != null) return 'failed';
  if (code == null) return 'failed';
  if (hasRemoteConsumerCloseMarker(stderr)) return 'consumer-closed';
  return 'failed';
}

// Classifies ANY racer outcome of the session's Promise.race (Issue #303:
// the Swift capture helper ignores SIGPIPE and exits 0, so when the OCI
// listener closes first the `capture-exit {code:0}` racer wins over the
// ffmpeg `close` racer — without this, that normal end-of-stream fell into
// the generic throw and the session exited 1 with no SESSION_END line).
// Pure function. Returns 'deadline' | 'consumer-closed' | 'failed':
//   kind 'deadline'                        -> 'deadline' (normal end).
//   kind 'ffmpeg-exit'                     -> classifyFfmpegExit verdict
//     (tightened fail-closed semantics: ONLY explicit SRT-output markers
//     — Broken pipe / muxer I/O error / interleaved-write I/O error —
//     count. A bare code 0, sinkClosed alone (every ffmpeg death closes
//     its stdin, so it cannot prove listener-first close), or a generic
//     "muxer" substring do not).
//   kind 'capture-exit', signal set        -> 'failed' (crashed/killed).
//   kind 'capture-exit', code 0            -> 'consumer-closed'. Rationale:
//     in this pipeline the ONLY frame consumer is ffmpeg's stdin; the
//     helper exiting 0 means it stopped cleanly because its writes could
//     no longer land (SIGPIPE-ignored Swift exit) — i.e. the downstream
//     went away first. Treated as a normal end even without stderr/sink
//     evidence (evidence, when present, only strengthens this reading).
//   kind 'capture-exit', code !== 0        -> 'failed' (helper error).
//   kind 'renderer-exit' / 'audio-tap-exit'-> 'failed' (cleanup still runs
//     in the session's finally block; these are never a normal end).
export function classifySessionEnd({
  kind = '',
  value = null,
  stderr = '',
  deadlineReached = false,
  sinkClosed = false,
} = {}) {
  if (kind === 'deadline' || deadlineReached) return 'deadline';
  if (kind === 'ffmpeg-exit') {
    return classifyFfmpegExit({
      code: value?.code,
      signal: value?.signal,
      stderr,
    });
  }
  if (kind === 'capture-exit') {
    if (value?.signal != null) return 'failed';
    if (value?.code === 0) return 'consumer-closed';
    return 'failed';
  }
  return 'failed';
}

// PCM drain (Issue #303 early-attach): while ffmpeg is not yet running,
// the early-attached helper's stdout must be READ (and discarded) so the
// IOProc keeps flowing and the CATapMutedWhenTapped physical mute stays in
// effect. Returns detach(): the session calls it right before piping
// audiotap.stdout into ffmpeg's fd 3. The switch-over drops in-flight
// chunks at most — never crashes, never stacks listeners.
export function attachPcmDrain(child) {
  const stdout = child?.stdout;
  if (!stdout?.on) return () => {};
  const discard = () => {};
  stdout.on('data', discard);
  try { stdout.resume?.(); } catch {}
  return () => {
    try { stdout.removeListener('data', discard); } catch {}
    try { stdout.pause?.(); } catch {}
  };
}

// Early audio-tap attach (Issue #303): poll `ps` for the automation
// Chrome's descendant PIDs starting right after the renderer spawns, and
// start the tap helper as soon as they appear — BEFORE the game starts
// making sound. (Waiting for renderer readiness muted nothing: the old
// flow started the tap only after the result, so boot-phase audio played
// out loud — observed live as "only the beginning was audible".)
// The helper's PCM is drain-read (see attachPcmDrain) until ffmpeg takes
// over the pipe. A "no audio process" helper failure (ok:false) is
// retried, not fatal, until deadlineMs. Returns { child, status,
// detachDrain }, or null on deadline/cancel — the caller then falls back
// to the post-readiness retry and finally fail-closed. All I/O is
// injectable for tests (psImpl/startTapImpl/sleepImpl).
//
// Known limit: the tap pins the AudioService PIDs found here. If Chrome
// restarts its AudioService mid-session (new PID), the tap does NOT follow
// — a fresh session (fresh tap) is needed. See README.
export async function earlyAttachAudioTap({
  rendererPid,
  audioTapBin,
  deadlineMs,
  pollIntervalMs = 500,
  tapTimeoutMs = 10_000,
  psImpl = () => spawnSync('ps', ['-ax', '-o', 'pid,ppid,command'], { encoding: 'utf8' }),
  startTapImpl = (bin, pids, opts) => startAudioTap(bin, pids, opts),
  sleepImpl = sleep,
  isCancelled = null,
} = {}) {
  if (!audioTapBin) throw new Error('earlyAttachAudioTap requires audioTapBin');
  for (;;) {
    if (isCancelled?.()) return null;
    const remaining = deadlineMs - Date.now();
    if (!(remaining > 0)) return null;
    let pids = null;
    try {
      const ps = psImpl();
      if (!ps?.error) {
        try { pids = resolveTapPids(ps.stdout || '', rendererPid); } catch { pids = null; }
      }
    } catch { pids = null; }
    if (pids) {
      try {
        const started = await startTapImpl(audioTapBin, pids, {
          timeoutMs: Math.max(1000, Math.min(tapTimeoutMs, remaining)),
        });
        if (isCancelled?.()) {
          // Lost the race with teardown: never hand out a live helper the
          // session will not own — stop it instead of leaking it.
          try { started?.child?.kill?.('SIGTERM'); } catch {}
          return null;
        }
        return { ...started, detachDrain: attachPcmDrain(started.child) };
      } catch {
        // Helper "no audio process" (ok:false) or handshake timeout:
        // retry until the deadline.
      }
    }
    const waitMs = Math.max(0, Math.min(pollIntervalMs, deadlineMs - Date.now()));
    if (!(waitMs > 0)) return null;
    await sleepImpl(waitMs);
  }
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
