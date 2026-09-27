// Contract tests for tools/soren91_windows_cdp_host.mjs. Pure contracts run on
// any platform; real-socket / real-binary checks run only where they can
// (Windows host, built helpers, a Tailscale IPv4) and are skipped elsewhere.
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import http from 'node:http';
import net from 'node:net';
import os from 'node:os';
import path from 'node:path';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import {
  assertNoScreenGrabInputs,
  assertOwnedBrowserPid,
  buildChromeArgs,
  buildFfmpegArgs,
  buildLoopbackArgs,
  defaults,
  detectChromeBin,
  framesDue,
  parseArgs,
  parseJpegSize,
  PROFILE_DIR_PREFIX,
  reapOrphans,
  resolveScreencastCrop,
  resolveVideoEncoder,
  selectOwnedProcesses,
  startCdpProxy,
  validateOptions,
  windowAuditViolations,
} from '../tools/soren91_windows_cdp_host.mjs';
import { detectTailscaleIp } from '../tools/soren91_macos_cdp_host.mjs';
import { classifyFfmpegExit } from '../tools/soren91_macos_audio.mjs';

const here = path.dirname(fileURLToPath(import.meta.url));
const SRT = 'srt://100.71.107.106:19192?mode=caller';
const base = () => ({ ...defaults({}), bindIp: '100.64.0.3', srtUrl: SRT });
const profile = path.join(os.tmpdir(), `${PROFILE_DIR_PREFIX}1700000000000`);
const CFT_DIR = 'C:\\Users\\op\\AppData\\Local\\soren91\\chrome\\154.0.8037.57\\chrome-win64';
const sink = () => ({
  chromeBin: `${CFT_DIR}\\chrome.exe`, audioSink: 'CABLE-A Input (VB-Audio Cable A)', audioSinkDir: CFT_DIR,
});

test('options: proxy binds only a Tailscale IPv4 and output is fixed 960x540', () => {
  assert.equal(validateOptions(base(), 'win32').proxyPort, 19093);
  assert.equal(validateOptions(base(), 'win32').cdpPort, 9322);
  for (const bindIp of ['0.0.0.0', '127.0.0.1', '192.168.1.10', 'localhost', '', '::']) {
    assert.throws(() => validateOptions({ ...base(), bindIp }, 'win32'), /Tailscale IPv4/, bindIp);
  }
  assert.throws(() => validateOptions({ ...base(), width: 1280, height: 720 }, 'win32'), /960x540/);
  assert.throws(() => validateOptions({ ...base(), proxyPort: 9322 }, 'win32'), /differ/);
  assert.throws(() => validateOptions({ ...base(), videoEncoder: 'x' }, 'win32'), /videoEncoder/);
});

test('options: --execute is Windows-only and needs a reviewed SRT caller URL', () => {
  assert.throws(() => validateOptions({ ...base(), execute: true }, 'darwin'), /Windows-only/);
  assert.throws(() => validateOptions({ ...base(), execute: true, srtUrl: '' }, 'win32'), /SOREN91_LOCAL_SRT_URL/);
  assert.equal(validateOptions({ ...base(), ...sink(), execute: true }, 'win32').execute, true);
  for (const srtUrl of [
    'srt://8.8.8.8:19192?mode=caller',
    'srt://100.71.107.106:19192?mode=listener',
    'srt://100.71.107.106:19192',
    'srt://100.71.107.106?mode=caller',
    'srt://u@100.71.107.106:19192?mode=caller',
    'srt://100.71.107.106:19192?mode=caller&passphrase=x',
    'udp://100.71.107.106:19192?mode=caller',
  ]) {
    assert.throws(() => validateOptions({ ...base(), srtUrl }, 'win32'), Error, srtUrl);
  }
  assert.throws(() => parseArgs(['--bogus'], {}), /unknown argument/);
  assert.equal(parseArgs(['--reap-orphans'], {}).reapOrphans, true);
});

test('chrome: always headless with a dedicated profile and loopback-only DevTools', () => {
  const args = buildChromeArgs({ ...base(), audio: true }, profile);
  assert.equal(args[0], '--headless=new');
  assert.ok(args.includes('--remote-debugging-port=9322'));
  assert.ok(args.includes('--remote-debugging-address=127.0.0.1'));
  assert.ok(args.includes(`--user-data-dir=${profile}`));
  assert.ok(args.includes('--window-size=1280,720'));
  assert.ok(!args.includes('--mute-audio'), 'audio must reach the process loopback');
  assert.ok(!args.some((arg) => /--window-position|--start-maximized|--kiosk|--app=/.test(arg)));
  assert.ok(buildChromeArgs({ ...base(), audio: false }, profile).includes('--mute-audio'));
  // Never the operator's profile or an arbitrary directory.
  for (const dir of [
    '', path.join(os.homedir(), 'AppData', 'Local', 'Google', 'Chrome', 'User Data'), path.join(os.tmpdir(), 'other'),
  ]) {
    assert.throws(() => buildChromeArgs(base(), dir), /dedicated/);
  }
});

test('audio: game audio is routed off the speakers, only for a dedicated Chrome (fail-closed)', () => {
  const run = { ...base(), execute: true };
  // No sink configured: refuse rather than play the game on this PC's speakers.
  assert.throws(() => validateOptions(run, 'win32'), /SOREN91_LOCAL_AUDIO_SINK/);
  assert.throws(() => validateOptions({ ...run, ...sink(), audioSinkDir: '' }, 'win32'), /SOREN91_LOCAL_AUDIO_SINK/);
  // The routing persists per executable path: never the operator's Chrome.
  assert.throws(() => validateOptions({
    ...run, ...sink(), chromeBin: 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe',
    audioSinkDir: 'C:\\Program Files\\Google\\Chrome\\Application',
  }, 'win32'), /dedicated Chrome/);
  assert.throws(() => validateOptions({ ...run, ...sink(), chromeBin: 'C:\\other\\chrome.exe' }, 'win32'), /under SOREN91_LOCAL_AUDIO_SINK_DIR/);
  assert.throws(() => validateOptions({ ...run, ...sink(), audioSinkDir: `${CFT_DIR}-evil` }, 'win32'), /under/);
  assert.equal(validateOptions({ ...run, ...sink(), audioSinkDir: `${CFT_DIR}\\` }, 'win32').execute, true);
  // Without audio there is nothing to route.
  assert.equal(validateOptions({ ...run, audio: false }, 'win32').audio, false);
  // Dry runs are not blocked.
  assert.equal(validateOptions(base(), 'win32').execute, false);

  assert.deepEqual(buildLoopbackArgs({ ...base(), ...sink() }, 11, 22), [
    '--pid', '11', '--expect-image', 'chrome.exe', '--parent-pid', '22',
    '--sink-endpoint', 'CABLE-A Input (VB-Audio Cable A)', '--sink-allowed-dir', CFT_DIR,
  ]);
  assert.deepEqual(buildLoopbackArgs(base(), 11, 22), ['--pid', '11', '--expect-image', 'chrome.exe', '--parent-pid', '22']);
  const env = defaults({ SOREN91_LOCAL_AUDIO_SINK: ' CABLE-A Input (VB-Audio Cable A) ', SOREN91_LOCAL_AUDIO_SINK_DIR: CFT_DIR });
  assert.equal(env.audioSink, 'CABLE-A Input (VB-Audio Cable A)');
  assert.equal(env.audioSinkDir, CFT_DIR);
});

test('chrome: binary override wins, otherwise the first installed candidate', () => {
  assert.equal(detectChromeBin({ SOREN91_CDP_CHROME_BIN: 'X:\\c.exe' }, () => false), 'X:\\c.exe');
  const env = { ProgramFiles: 'C:\\PF', LOCALAPPDATA: 'C:\\LA' };
  assert.equal(detectChromeBin(env, (p) => p.startsWith('C:\\LA')), 'C:\\LA\\Google\\Chrome\\Application\\chrome.exe');
  assert.equal(detectChromeBin(env, () => false), 'C:\\PF\\Google\\Chrome\\Application\\chrome.exe');
});

test('ffmpeg: pipes only (never a screen grab), 960x540@30, h264 + aac over SRT', () => {
  const nvenc = buildFfmpegArgs({ ...base(), audio: true }, { encoder: 'h264_nvenc' });
  const inputs = nvenc.flatMap((arg, i) => (arg === '-i' ? [nvenc[i + 1]] : []));
  assert.deepEqual(inputs, ['pipe:0', 'pipe:3']);
  assert.ok(nvenc.join(' ').includes('-f rawvideo -pixel_format rgb24 -video_size 960x540 -framerate 30 -i pipe:0'));
  assert.ok(nvenc.join(' ').includes('-f s16le -ar 48000 -ac 2 -channel_layout stereo -i pipe:3'));
  assert.ok(nvenc.join(' ').includes('-c:v h264_nvenc'));
  assert.ok(nvenc.join(' ').includes('-c:a aac'));
  assert.ok(nvenc.join(' ').includes('-r 30'));
  assert.deepEqual(nvenc.slice(-3), ['-f', 'mpegts', SRT]);
  const x264 = buildFfmpegArgs({ ...base(), audio: false }, { encoder: 'libx264' });
  assert.ok(x264.includes('libx264') && x264.includes('-an') && !x264.includes('pipe:3'));
  assert.ok(buildFfmpegArgs({ ...base(), audio: true, audioGain: 2 }, { encoder: 'libx264' }).join(' ').includes('-af volume=2'));
  assert.throws(() => buildFfmpegArgs({ ...base(), width: 1920 }, { encoder: 'libx264' }), /960x540/);
  assert.throws(() => buildFfmpegArgs(base(), { encoder: 'h264_amf' }), /unsupported encoder/);
  for (const bad of [
    ['-f', 'gdigrab', '-i', 'desktop'],
    ['-f', 'ddagrab', '-i', 'pipe:0'],
    ['-f', 'GDIGRAB', '-i', 'title=Google Chrome'],
    ['-f', 'dshow', '-i', 'pipe:0'],
    ['-f', 'rawvideo', '-i', 'desktop'],
    ['-f', 'rawvideo', '-i', 'C:\\capture.raw'],
  ]) {
    assert.throws(() => assertNoScreenGrabInputs(bad), /fail-closed/, bad.join(' '));
  }
});

test('session end: Windows ffmpeg "I/O error" receiver close is a normal end, other exits fail closed', () => {
  // Observed on this host when the SRT listener closed (Windows CRT strerror).
  const windowsClose = 'av_interleaved_write_frame(): I/O error\n    Last message repeated 1 times\n'
    + 'Error writing trailer of srt://100.64.0.3:19292?mode=caller: I/O error\n';
  assert.equal(classifyFfmpegExit({ code: 1, stderr: windowsClose }), 'consumer-closed');
  assert.equal(classifyFfmpegExit({ code: 1, stderr: 'Error submitting a packet to the muxer: I/O error' }), 'consumer-closed');
  // macOS spelling keeps working.
  assert.equal(classifyFfmpegExit({ code: 1, stderr: 'av_interleaved_write_frame(): Input/output error' }), 'consumer-closed');
  // Encoder failures and unexplained exits stay failures.
  assert.equal(classifyFfmpegExit({ code: 1, stderr: 'Error while opening encoder for output stream #0:0' }), 'failed');
  assert.equal(classifyFfmpegExit({ code: 1, stderr: 'Conversion failed! I/O error while reading input' }), 'failed');
  assert.equal(classifyFfmpegExit({ code: null, signal: 'SIGKILL', stderr: windowsClose }), 'failed');
});

test('encoder: auto picks NVENC only when listed AND a trial encode succeeded', () => {
  const listed = ' V....D h264_nvenc   NVIDIA NVENC H.264 encoder';
  assert.equal(resolveVideoEncoder('auto', { encodersText: listed, nvencTrialOk: true }), 'h264_nvenc');
  assert.equal(resolveVideoEncoder('auto', { encodersText: listed, nvencTrialOk: false }), 'libx264');
  assert.equal(resolveVideoEncoder('auto', { encodersText: '', nvencTrialOk: true }), 'libx264');
  assert.equal(resolveVideoEncoder('libx264', { encodersText: listed, nvencTrialOk: true }), 'libx264');
  assert.throws(() => resolveVideoEncoder('h264_nvenc', { encodersText: '' }), /h264_nvenc/);
});

function fakeJpeg(width, height, { progressive = false } = {}) {
  const app0 = Buffer.from([0xff, 0xe0, 0x00, 0x10, 0x4a, 0x46, 0x49, 0x46, 0, 1, 1, 0, 0, 1, 0, 1, 0, 0]);
  const dqt = Buffer.concat([Buffer.from([0xff, 0xdb, 0x00, 0x43, 0x00]), Buffer.alloc(64, 1)]);
  const sof = Buffer.from([0xff, progressive ? 0xc2 : 0xc0, 0x00, 0x11, 0x08,
    height >> 8, height & 0xff, width >> 8, width & 0xff, 3, 1, 0x22, 0, 2, 0x11, 1, 3, 0x11, 1]);
  return Buffer.concat([Buffer.from([0xff, 0xd8]), app0, dqt, sof, Buffer.from([0xff, 0xd9])]);
}

test('jpeg: frame size is read from the SOF marker; non-JPEG is rejected', () => {
  assert.deepEqual(parseJpegSize(fakeJpeg(1280, 720)), { width: 1280, height: 720 });
  assert.deepEqual(parseJpegSize(fakeJpeg(2560, 1440, { progressive: true })), { width: 2560, height: 1440 });
  assert.equal(parseJpegSize(Buffer.from('not a jpeg')), null);
  assert.equal(parseJpegSize(Buffer.from([0xff, 0xd8, 0xff])), null);
  assert.equal(parseJpegSize(null), null);
});

test('crop: canvas rect maps into frame pixels and fails closed on mismatch', () => {
  const canvas = { x: 160, y: 0, width: 960, height: 720, iw: 1280, ih: 720, dpr: 1 };
  assert.deepEqual(resolveScreencastCrop({ frameWidth: 1280, frameHeight: 720, canvas }),
    { left: 160, top: 0, width: 960, height: 720 });
  // HiDPI / downscaled frames scale uniformly.
  assert.deepEqual(resolveScreencastCrop({ frameWidth: 2560, frameHeight: 1440, canvas }),
    { left: 320, top: 0, width: 1920, height: 1440 });
  assert.deepEqual(resolveScreencastCrop({ frameWidth: 640, frameHeight: 360, canvas }),
    { left: 80, top: 0, width: 480, height: 360 });
  // Sub-pixel overflow is clamped to the frame.
  const edge = { x: 0, y: 0, width: 1281, height: 721, iw: 1280, ih: 720, dpr: 1 };
  assert.deepEqual(resolveScreencastCrop({ frameWidth: 1280, frameHeight: 720, canvas: edge }),
    { left: 0, top: 0, width: 1280, height: 720 });
  // Frame is not a uniform scale of the measured content (viewport changed).
  assert.throws(() => resolveScreencastCrop({ frameWidth: 1280, frameHeight: 1024, canvas }), /uniform scale/);
  assert.throws(() => resolveScreencastCrop({ frameWidth: 0, frameHeight: 0, canvas }), /frame size/);
  assert.throws(() => resolveScreencastCrop({ frameWidth: 1280, frameHeight: 720, canvas: null }), /fail-closed/);
  assert.throws(() => resolveScreencastCrop({
    frameWidth: 1280, frameHeight: 720, canvas: { ...canvas, x: 1200, width: 960 },
  }), /outside content area/);
});

test('pacer: constant 30fps timeline with bounded catch-up', () => {
  assert.equal(framesDue(0, 0, 0), 1);
  assert.equal(framesDue(0, 0, 1), 0);
  assert.equal(framesDue(0, 1000, 1), 30);
  assert.equal(framesDue(0, 1000, 31), 0);
  assert.equal(framesDue(0, 60_000, 0), 60, 'burst is capped');
  assert.equal(framesDue(1000, 500, 0), 0, 'never negative');
});

test('ownership: the DevTools browser must be the Chrome this host spawned', () => {
  const info = [{ type: 'browser', id: 4242 }, { type: 'renderer', id: 5000 }, { type: 'GPU', id: 5001 }];
  assert.equal(assertOwnedBrowserPid(info, 4242), true);
  assert.throws(() => assertOwnedBrowserPid(info, 999), /not the Chrome this host spawned/);
  assert.throws(() => assertOwnedBrowserPid([{ type: 'renderer', id: 4242 }], 4242), /fail-closed/);
  assert.throws(() => assertOwnedBrowserPid(undefined, 4242), /fail-closed/);
});

test('window audit: any visible window from our tree is a violation; malformed fails closed', () => {
  assert.deepEqual(windowAuditViolations({ rootPid: 1, treePids: [1], visible: [] }), []);
  const bad = { rootPid: 1, treePids: [1, 2], visible: [{ pid: 2, className: 'Chrome_WidgetWin_1', rect: [0, 0, 10, 10] }] };
  assert.equal(windowAuditViolations(bad).length, 1);
  assert.throws(() => windowAuditViolations({}), /fail-closed/);
  assert.throws(() => windowAuditViolations(null), /fail-closed/);
});

test('reaper: only processes carrying our dedicated profile marker are selected', () => {
  const tmpDir = 'C:\\Users\\op\\AppData\\Local\\Temp';
  const ours = `"C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe" --headless=new "--user-data-dir=${tmpDir}\\${PROFILE_DIR_PREFIX}1700000000000" about:blank`;
  const oursChild = `chrome.exe --type=renderer --user-data-dir=${tmpDir.toLowerCase()}\\${PROFILE_DIR_PREFIX}1700000000000 --lang=ja`;
  const operator = '"C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe" --flag-switches-begin';
  const operatorChild = `chrome.exe --type=renderer --user-data-dir=C:\\Users\\op\\AppData\\Local\\Google\\Chrome\\User Data`;
  const lookalike = `chrome.exe --user-data-dir=${tmpDir}\\soren91-probe-1`;
  const list = [
    { pid: 10, commandLine: ours }, { pid: 11, commandLine: oursChild }, { pid: 20, commandLine: operator },
    { pid: 21, commandLine: operatorChild }, { pid: 22, commandLine: lookalike }, { pid: 23, commandLine: null },
    { pid: 0, commandLine: ours },
  ];
  assert.deepEqual(selectOwnedProcesses(list, { tmpDir }).map((entry) => entry.pid), [10, 11]);
  const other = `${tmpDir}\\${PROFILE_DIR_PREFIX}1800000000000`;
  assert.deepEqual(selectOwnedProcesses(list, { profileDir: other }), []);
});

test('reaper: kills owned processes and removes stale profile dirs only', () => {
  const tmpDir = 'C:\\T';
  const now = 1_800_000_000_000;
  const killed = [];
  const removed = [];
  const result = reapOrphans({
    tmpDir,
    now: () => now,
    listImpl: () => [
      { pid: 10, commandLine: `chrome.exe --user-data-dir=${tmpDir}\\${PROFILE_DIR_PREFIX}${now - 999_999}` },
      { pid: 20, commandLine: 'chrome.exe --user-data-dir=C:\\Users\\op\\Chrome' },
    ],
    killImpl: (pid) => killed.push(pid),
    readdirImpl: () => [`${PROFILE_DIR_PREFIX}${now - 999_999}`, `${PROFILE_DIR_PREFIX}${now - 1000}`, 'unrelated', 'soren91-cdp-host-1'],
    rmImpl: (dir) => removed.push(dir),
  });
  assert.deepEqual(killed, [10]);
  assert.deepEqual(removed, [path.win32.join(tmpDir, `${PROFILE_DIR_PREFIX}${now - 999_999}`)]);
  assert.deepEqual(result.killed, [10]);
});

// --- Real sockets: CDP proxy peer lock + HTTP/WebSocket relay ---

const tailscaleIp = detectTailscaleIp();

async function withUpstream(fn) {
  const upstream = http.createServer((req, res) => {
    res.writeHead(200, { 'content-type': 'application/json' });
    res.end(JSON.stringify({ Browser: 'stub', url: req.url }));
  });
  upstream.on('upgrade', (req, socket, head) => {
    socket.write('HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n\r\n');
    if (head?.length) socket.write(head); // bytes that arrived with the handshake
    socket.on('data', (chunk) => socket.write(chunk)); // echo frames
    socket.on('error', () => {});
  });
  await new Promise((resolve) => upstream.listen(0, '127.0.0.1', resolve));
  try {
    await fn(upstream.address().port);
  } finally {
    upstream.closeAllConnections?.();
    upstream.close();
  }
}

function rawExchange(host, port, payload, { waitMs = 1500 } = {}) {
  return new Promise((resolve) => {
    let data = '';
    let closed = false;
    const socket = net.connect({ host, port }, () => socket.write(payload));
    socket.on('data', (chunk) => { data += chunk; });
    socket.on('close', () => { closed = true; });
    socket.on('error', () => {});
    setTimeout(() => { socket.destroy(); resolve({ data, closed }); }, waitMs);
  });
}

test('proxy: the reviewed OCI peer gets HTTP and WebSocket upgrade relayed', { skip: !tailscaleIp && 'no Tailscale IPv4 on this host' }, async () => {
  await withUpstream(async (cdpPort) => {
    const proxy = await startCdpProxy({ bindIp: tailscaleIp, proxyPort: 0, cdpPort, allowedPeerIp: tailscaleIp });
    const port = proxy.address().port;
    try {
      assert.equal(proxy.address().address, tailscaleIp, 'proxy must bind the Tailscale IPv4 only');
      const res = await fetch(`http://${tailscaleIp}:${port}/json/version`, { headers: { connection: 'close' } });
      assert.equal(res.status, 200);
      assert.equal((await res.json()).url, '/json/version');
      const upgrade = await rawExchange(tailscaleIp, port,
        'GET /devtools/browser/x HTTP/1.1\r\nHost: x\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n'
        + 'Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\nSec-WebSocket-Version: 13\r\n\r\nPING-FRAME', { waitMs: 800 });
      assert.match(upgrade.data, /^HTTP\/1\.1 101/);
      assert.match(upgrade.data, /PING-FRAME/);
    } finally {
      proxy.close();
    }
  });
});

test('proxy: any other tailnet peer is dropped before reaching DevTools', { skip: !tailscaleIp && 'no Tailscale IPv4 on this host' }, async () => {
  await withUpstream(async (cdpPort) => {
    let upstreamHits = 0;
    const counter = net.createServer((socket) => { upstreamHits += 1; socket.destroy(); });
    await new Promise((resolve) => counter.listen(0, '127.0.0.1', resolve));
    const proxy = await startCdpProxy({
      bindIp: tailscaleIp, proxyPort: 0, cdpPort: counter.address().port, allowedPeerIp: '100.64.0.1',
    });
    try {
      const result = await rawExchange(tailscaleIp, proxy.address().port, 'GET /json/version HTTP/1.1\r\nHost: x\r\n\r\n');
      assert.equal(result.data, '');
      assert.equal(result.closed, true);
      assert.equal(upstreamHits, 0);
    } finally {
      proxy.close();
      counter.close();
    }
  });
  assert.throws(
    () => startCdpProxy({ bindIp: tailscaleIp, proxyPort: 0, cdpPort: 1, allowedPeerIp: '8.8.8.8' }),
    /Tailscale IPv4/,
  );
});

// --- Real helper binaries (Windows, after tools/soren91_windows_helpers_build.ps1) ---

const auditBin = path.join(here, '..', 'tools', 'windows', 'bin', 'soren91_window_audit.exe');
const loopbackBin = path.join(here, '..', 'tools', 'windows', 'bin', 'soren91_process_loopback.exe');
const helpersBuilt = process.platform === 'win32' && fs.existsSync(auditBin) && fs.existsSync(loopbackBin);

test('window audit binary: reports the tree without titles and no visible window for a hidden process', { skip: !helpersBuilt && 'helpers not built' }, () => {
  const result = spawnSync(auditBin, ['--root-pid', String(process.pid)], { encoding: 'utf8', windowsHide: true });
  assert.equal(result.status, 0, result.stderr);
  const audit = JSON.parse(result.stdout);
  assert.equal(audit.rootPid, process.pid);
  assert.ok(audit.treePids.includes(process.pid));
  assert.ok(Array.isArray(audit.visible));
  assert.ok(!/title/i.test(result.stdout), 'window titles must never be emitted');
  assert.equal(spawnSync(auditBin, ['--root-pid', '0'], { windowsHide: true }).status, 2);
});

test('loopback binary: refuses a target that is not the expected image (fail-closed)', { skip: !helpersBuilt && 'helpers not built' }, () => {
  const result = spawnSync(loopbackBin, ['--pid', String(process.pid), '--expect-image', 'chrome.exe', '--duration-sec', '1'], {
    encoding: 'utf8', windowsHide: true, timeout: 15_000,
  });
  assert.equal(result.status, 2);
  assert.match(result.stderr, /target image mismatch/);
  assert.equal(result.stdout, '');
  assert.equal(spawnSync(loopbackBin, [], { windowsHide: true }).status, 2);
});

test('loopback binary: never routes audio of an executable outside the sink dir (fail-closed)', { skip: !helpersBuilt && 'helpers not built' }, () => {
  const run = (extra) => spawnSync(loopbackBin, ['--pid', String(process.pid), '--duration-sec', '1', ...extra], {
    encoding: 'utf8', windowsHide: true, timeout: 15_000,
  });
  const outside = run(['--sink-endpoint', 'CABLE-A Input (VB-Audio Cable A)', '--sink-allowed-dir', path.join(os.tmpdir(), 'not-node')]);
  assert.equal(outside.status, 2);
  assert.match(outside.stderr, /refusing to route/);
  assert.equal(outside.stdout, '');
  const half = run(['--sink-endpoint', 'CABLE-A Input (VB-Audio Cable A)']);
  assert.equal(half.status, 2);
  assert.match(half.stderr, /go together/);
});
