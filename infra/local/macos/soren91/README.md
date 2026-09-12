# Soren91 macOS local renderer (Tier -1, PoC)

Mirrors the Windows local renderer (`tools/soren91_windows_renderer.mjs` /
`tools/soren91_windows_session.mjs`, PR #131) for a Mac with Apple Silicon
hardware WebGL, targeting the same `start/status/stop` contract. See
[Issue #303](https://github.com/azumag/soviet_now/issues/303).

**Status: 5-minute local PoC validated once on 2026-09-12 (Mac mini M4). Not
soaked for 30 minutes, not wired into the backend selector, not connected to
the production broadcast.** See the results appended to Issue #303 for the
actual measured numbers.

## What this is

```text
macOS interactive user session
  existing Google Chrome (Playwright channel:'chrome', no managed Chromium download)
    -> Unity WebGL / Soren91 (sorengame91 on unityroom.com)
    -> hardware WebGL (ANGLE Metal backend)
  ffmpeg
    -> avfoundation screen capture, cropped to the game window only
    -> h264_videotoolbox
    -> SRT caller over Tailscale -> OCI listener
```

Normal SorenGame / the main broadcast keep running on OCI throughout; this
renderer is only evaluated in isolation and is not switched into production
until it passes the full E2E gate in Issue #303.

## Prerequisites

- macOS, Apple Silicon (verified on Apple M4) or Intel with a Metal-capable GPU.
- An existing Google Chrome install. Playwright drives it via `channel: 'chrome'`
  (`chromium.launch({ channel: 'chrome' })`) — **no managed Chromium download is
  triggered** as long as this succeeds. Only fall back to
  `npx playwright install chromium --no-shell` if `channel: 'chrome'` fails to
  launch, and note the `~/Library/Caches/ms-playwright` size delta if you do.
- `npm install` in this checkout (installs `playwright` from `package.json`;
  does not download a browser).
- **Screen Recording permission** for whichever app hosts the `ffmpeg`/`node`
  process (Terminal, iTerm2, or an unattended runner). Without it, avfoundation
  screen capture will hang or return black frames. Grant via System Settings →
  Privacy & Security → Screen Recording, then restart the host app.
- FFmpeg with `h264_videotoolbox` (Homebrew's default `ffmpeg` formula has
  this) **and** the `srt` protocol. **Homebrew's default `ffmpeg` formula does
  NOT build libsrt** — `ffmpeg -hide_banner -protocols | grep srt` only shows
  `srtp` (unrelated SRTP), not `srt`. Verified on this Mac with
  `ffmpeg 8.0.1_4` from `homebrew-core`. To get SRT support, install the
  keg-only `ffmpeg-full` formula (`brew install ffmpeg-full`, ~47 dependencies)
  and point `SOREN91_LOCAL_FFMPEG_BIN` at
  `$(brew --prefix ffmpeg-full)/bin/ffmpeg` instead of replacing the default
  `ffmpeg` symlink. This has not been installed as part of this PoC (ask
  before adding a new large formula) — SRT/OCI E2E is blocked on it.

## Login-session-only, no launchd

Playwright launches headed Chrome, so this must run in a logged-in interactive
session — the same constraint as the Windows backend. Do not wrap this in a
`launchd` daemon/agent that runs outside a real GUI session.

## Why the capture crop is calibrated at runtime, not hard-coded

`newContext({ viewport: {...} })` applies CDP device-metrics emulation:
`window.innerWidth/innerHeight` and `window.screenX/Y` then report **virtual**
values that do not match the real on-screen window (confirmed by capturing
with those values once — it framed the browser's tab bar and URL bar instead
of the game). The renderer instead launches with `viewport: null` and
`--app=about:blank` (no tabs/URL bar), then uses
`Browser.getWindowForTarget` / `Browser.getWindowBounds` /
`Browser.setWindowBounds` over a raw CDP session to resize the **real** native
window until its real content area is exactly 960x540, and computes the crop
origin from the real window bounds. That crop rect is written into the
renderer's result JSON (`crop.cropX`, `crop.cropY`) and consumed by
`soren91_macos_session.mjs` to build the `ffmpeg -vf crop=...` filter — so the
stream only ever contains the Soren91 canvas, never the rest of the Mac's
desktop.

## Files

- `tools/soren91_macos_renderer.mjs` — launches existing Chrome, calibrates
  window bounds, logs into the live Soren91 match, measures native-rAF fps and
  WebGL facts for `SOREN91_LOCAL_MEASURE_SEC` (default 60s), writes a result
  JSON to `SOREN91_LOCAL_RESULT_PATH`, then idles until stopped.
- `tools/soren91_macos_session.mjs` — same options/validation contract as the
  Windows session (`sessionSec` capped at 1800s / `hardMaxSec` at 2400s,
  960x540/30fps fixed, SRT URL must be a Tailscale IPv4 `srt://` URL with no
  `passphrase=` in argv). Orchestrates renderer → ffmpeg capture+encode(+SRT).
- `tests/test_soren91_macos_session.mjs` — contract tests (`node --test`).

## Manual verification checklist (what to actually run)

```bash
npm install
node tools/check_existing_chrome.mjs   # optional: confirms channel:'chrome' works
node --test tests/test_soren91_macos_session.mjs

# Dry run (prints the plan, does not launch anything):
node tools/soren91_macos_session.mjs --srt-url srt://100.64.0.2:19192?mode=caller

# Real 5-minute PoC (joins the live match under a distinguishable player name,
# e.g. DoCiAI:MC — set SOREN91_LOCAL_PLAYER_NAME to change it):
SOREN91_LOCAL_SESSION_SEC=300 SOREN91_LOCAL_HARD_MAX_SEC=420 \
  node tools/soren91_macos_session.mjs --execute --srt-url srt://<oci-tailscale-ip>:<port>?mode=caller
```

## Troubleshooting

- **Capture hangs / produces a black frame**: Screen Recording permission not
  granted to the host app; grant it and fully restart that app (not just the
  terminal tab).
- **`ffmpeg does not expose the SRT protocol`**: default Homebrew `ffmpeg`
  lacks libsrt; see Prerequisites above.
- **`window content size mismatch after calibration`**: some other window
  manager / Stage Manager tiling interfered with `Browser.setWindowBounds`;
  disable Stage Manager or window tiling before running.
- **Renderer reports `hardwareRenderer: false`**: `UNMASKED_RENDERER_WEBGL`
  matched `swiftshader`/`llvmpipe`/`software`, or didn't match
  `apple`/`angle`/`metal` at all — check `chrome://gpu` for GPU blocklist
  entries on this Mac.
