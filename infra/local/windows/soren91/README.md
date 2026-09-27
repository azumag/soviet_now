# Soren91 Windows renderer host (cdp-host)

The wired Windows desktop is the **primary** Soren91 (Meriken AI) renderer;
the Mac mini (`infra/local/macos/soren91`) is the failover. docich selects
between them (azumag/docich#1176, webui "メリケンAI 描画ホスト": auto /
windows / mac) and talks to both through the same local-agent contract.

## What runs where

| Piece | File | Role |
|---|---|---|
| local agent | `tools/soren91_local_agent.mjs` | `GET /health`, `GET /v1/status`, `POST /v1/start {"srtUrl"}`, `POST /v1/stop` on the Tailscale IPv4 (Bearer token, `timingSafeEqual`) |
| cdp-host | `tools/soren91_windows_cdp_host.mjs` | spawned per session by `/v1/start` in `cdp-host` mode |
| audio helper | `tools/windows/soren91_process_loopback.cs` | ApplicationLoopback of **our Chrome's process tree only** → s16le 48k stereo |
| window audit | `tools/windows/soren91_window_audit.cs` | lists visible top-level windows of our Chrome tree (no pixels, no titles) |
| helper build | `tools/soren91_windows_helpers_build.ps1` | builds both helpers with the in-box .NET Framework `csc.exe` into `tools/windows/bin/` (gitignored) |

## Session pipeline (no screen coordinates anywhere)

1. Sweep orphans from earlier sessions (processes carrying our
   `--user-data-dir=%TEMP%\soren91-win-cdp-host-*` marker), refuse to start if
   `127.0.0.1:9322` is already taken.
2. Spawn Chrome with `--headless=new`, a fresh dedicated profile and
   `--remote-debugging-port=9322 --remote-debugging-address=127.0.0.1`.
   The operator's Chrome is never touched (separate profile ⇒ separate
   browser process). `SystemInfo.getProcessInfo` must report our spawned PID
   as the DevTools browser process, otherwise fail closed.
3. TCP proxy `<tailscale-ip>:19093 → 127.0.0.1:9322`, accepting **only** the
   OCI Tailscale IP taken from the `srtUrl` host (HTTP and WebSocket upgrade
   are both byte-relayed). docich waits for `/json/version` there, then the
   VM bot connects with `SOREN91_REMOTE_CDP_URL=http://<win-ip>:19093`.
4. When the local `/json/list` shows a `play.unityroom.com` page:
   `SOREN91_CDP_HOST_GAME_FOUND=1` (agent `driverState: ready`).
5. Video: `Page.startScreencast` on that exact page target. Every frame is
   rejected (fail closed) if the page URL is not on the reviewed game origin.
   The Unity canvas rect is re-measured every 500ms and adopted after 1s of
   stability, cropped per frame with sharp, letterboxed to 960x540 and paced
   to a constant 30fps (repeating the last frame on static screens).
6. Audio: `soren91_process_loopback.exe --pid <our chrome> --expect-image chrome.exe`
   (INCLUDE_TARGET_PROCESS_TREE). Wall-clock paced; silence is padded.
7. ffmpeg: `rawvideo pipe:0` + `s16le pipe:3` → `h264_nvenc` (p4/ll/CBR 2M,
   GOP 60; libx264 fallback when a trial NVENC encode fails) + AAC 128k →
   `mpegts` → `srt://<oci>:<port>?mode=caller`. Screen-grab inputs
   (gdigrab/ddagrab/dshow/lavfi) or non-pipe inputs throw before spawning.
8. Every 5s the window audit must report **zero** visible windows for our
   Chrome tree, otherwise fail closed.

Stop: `/v1/stop` closes the host's stdin (graceful: Chrome, proxy, loopback
and ffmpeg are killed, the profile dir is deleted, exit 143), and the agent
escalates to `taskkill /PID <host> /T /F` after 5s. After every session the
agent runs `--reap-orphans`; the next `/v1/start` waits for that sweep.

## Measured on the wired Windows desktop (2026-09-27, RTX 3060, driver 610.88, Chrome 154)

Off-production only: a temporary SRT listener on this host's own Tailscale IP
(`:19292`, not the production `:19192`), with a separate Playwright client
standing in for the OCI bot through the `:19093` proxy.

- Headless GPU: `ANGLE (NVIDIA GeForce RTX 3060 Direct3D11)`,
  `gpu_compositing/webgl: enabled`, page rAF 60fps, screencast ~55–60 fps (1666 frames/30s, 3448/60s).
- Received stream (ffprobe): h264 960x540 `30/1`, AAC 48 kHz stereo;
  75.02s → 2250 video packets, pts span 74.97s ⇒ **30.00 fps**; 62.02s →
  1860 packets ⇒ 30.00 fps; encoder `h264_nvenc`.
- Audio during play: mean −35.4 dB / max −11.7 dB (title screen is silent).
  The loopback helper reproduced a −0.05 FS 440 Hz test tone at rms 1158.5
  (expected 1158) and captured nothing when Chrome ran with `--mute-audio`.
- Window audit every 5s during streaming: 0 visible windows for our Chrome
  tree (13–17 processes), 0 visible windows for any other Chrome.
- `/v1/stop` mid-stream: host exited in 994 ms (graceful, code 143, no
  taskkill needed); afterwards no Chrome/ffmpeg/loopback/host process and no
  `soren91-win-cdp-host-*` directory remained. Listener-side close ends the
  session as `SOREN91_CDP_HOST_END=consumer-closed` (exit 0).

## Not verified yet / operator decisions

- **Residency** (logon autostart, recovery after kill) is written but not
  installed or measured.
- **Firewall**: Windows Defender Firewall blocks inbound TCP to `node.exe`
  by default (`NotifyOnListen=True`, no rule exists). The VM cannot reach
  19191/19093 until an administrator adds a rule, e.g. (elevated):
  `New-NetFirewallRule -DisplayName "Soren91 agent (Tailscale)" -Direction Inbound -Action Allow -Protocol TCP -LocalPort 19191,19093 -RemoteAddress 100.64.0.0/10 -InterfaceAlias Tailscale -Program "C:\Program Files\nodejs\node.exe"`
- **Game audio is also audible on this PC's default output device.**
  Unlike the Mac tap (`CATapMutedWhenTapped`), process loopback does not mute
  the source, and `--mute-audio` silences the capture too. Muting the Chrome
  audio session was rejected on purpose: Windows persists per-app volume/mute
  by executable path, which could leave the operator's everyday Chrome muted.
- ffmpeg: no system ffmpeg is installed; the measurements used the ffmpeg
  4.4 build bundled with Virtual Desktop Streamer (libsrt + nvenc).
  `SOREN91_LOCAL_FFMPEG_BIN` should point to a dedicated build.
- The real OCI bot (`soren91/main.mjs`) was not run against this host, and
  the OCI → Windows path (VM as the peer) was not exercised.
