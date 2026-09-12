# Soren91 macOS local renderer (Tier -1, PoC)

Mirrors the Windows local renderer (`tools/soren91_windows_renderer.mjs` /
`tools/soren91_windows_session.mjs`, PR #131) for a Mac with Apple Silicon
hardware WebGL, targeting the same `start/status/stop` contract. See
[Issue #303](https://github.com/azumag/soviet_now/issues/303).

**Status: 5-minute local PoC validated once on 2026-09-12 (Mac mini M4). Not
soaked for 30 minutes, not wired into the backend selector, not connected to
the production broadcast.** See the results appended to Issue #303 for the
actual measured numbers.

**⚠️ Known critical limitation, confirmed 2026-09-12 — do not use `--execute`
unattended yet:** the `avfoundation`-capture design below (whole-display
capture + a fixed crop rect) captures **whatever is visually topmost at those
screen coordinates, not a specific window.** In the one real SRT→OCI run, the
capture actually streamed the operator's own everyday Chrome window (which
happened to be positioned over the same screen region) for the full session
instead of the Soren91 game — confirmed by extracting frames from the
received file. This is not a fluke; it is a structural property of capturing
a display region instead of a window. Two further requirements this design
does **not** yet satisfy:
- The user explicitly wants **no visible window at all**, not just an
  isolated one.
- `--headless=new` does keep hardware WebGL (confirmed: still reports `ANGLE
  (Apple, ANGLE Metal Renderer: Apple M4, ...)`), but the only way to get
  pixels out of a truly headless page is the DevTools Protocol
  (`Page.captureScreenshot` polling or `Page.startScreencast`), and both were
  measured at **4.0fps and 5.7fps respectively at 960x540** on this Mac — far
  short of the 30fps target. Headless is therefore not currently viable for
  this use case.
- An off-screen-positioned (e.g. `--window-position=-4000,-4000`) *headed*
  window also keeps hardware WebGL, but nothing appears on any physical
  display for `avfoundation` to capture from at all once it's off-screen — a
  window-specific capture API is required instead
  ([`ScreenCaptureKit`](https://developer.apple.com/documentation/screencapturekit)'s
  `SCContentFilter` targeting a specific window, which can read a window's
  content even off-screen/occluded). **No such helper exists yet** — it would
  need a small native (Swift) capture helper, since neither Playwright/CDP
  nor ffmpeg's `avfoundation` input exposes per-window capture. This is
  tracked as follow-up work, not done in this PoC.

The Windows Tier -1 design (PR #131) has an analogous, equally unverified gap:
its `gdigrab -i title=...` capture targets a window by title rather than a
screen region, but whether that reliably captures a GPU-composited
(DirectComposition) Chrome window while it is hidden/occluded/off-screen has
never been tested on real Windows hardware (PR #131 is still an unexecuted
draft). The correct fix on Windows would likewise be a native per-window
capture API (`Windows.Graphics.Capture`) rather than `gdigrab`. Until one
platform actually implements and tests a real window-specific capture path,
"no visible window" is not achieved on either side.

None of this calls the earlier local hardware-WebGL/VideoToolbox results
into question — those were measured directly via `page.evaluate()` native
`requestAnimationFrame` timestamps and a real `h264_videotoolbox` encode,
independent of any capture method. It also does not reopen the VM's original
choppiness diagnosis (SwiftShader software rendering measured at 6-9fps via
the same native-rAF technique, `tools/soren91_iso_probe.mjs`) — that was a
genuine GPU/rendering-throughput finding, unrelated to this capture-pipeline
gap, which only matters once real GPU rendering is available at all.

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
`soren91_macos_session.mjs` to build the `ffmpeg -vf crop=...` filter.

**This only isolates the game from the rest of the desktop when nothing else
occupies that same screen region and stays on top for the whole session** —
see the "Known critical limitation" callout above. The crop rect is correct
for where *this renderer's own window* sits; it says nothing about what is
actually visible at those coordinates once the session is running. A real
SRT→OCI test streamed the operator's own Chrome window for this exact
reason. Do not describe this design as isolating the stream to
"only the Soren91 canvas" until it is backed by a window-specific capture
API (see above).

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
