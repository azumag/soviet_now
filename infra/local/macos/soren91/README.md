# Soren91 macOS local renderer (Tier -1, PoC)

Mirrors the Windows local renderer (`tools/soren91_windows_renderer.mjs` /
`tools/soren91_windows_session.mjs`, PR #131) for a Mac with Apple Silicon
hardware WebGL, targeting the same `start/status/stop` contract. See
[Issue #303](https://github.com/azumag/soviet_now/issues/303).

**Status (2026-09-13): capture pipeline rebuilt around ScreenCaptureKit
window-targeted capture, after a real incident with the original
avfoundation design (see "Why not avfoundation screen capture" below).
Validated so far with a synthetic decoy window (exact-match identity
selection + occlusion resilience, both confirmed by pixel inspection) and a
single real local run against the live Soren91 match (local file only, no
SRT/OCI). Not yet re-run through the full 90s+ SRT/OCI E2E, not soaked for
30 minutes, not wired into the backend selector, not connected to the
production broadcast.**

## What this is

```text
macOS interactive user session
  existing Google Chrome (Playwright channel:'chrome', no managed Chromium download)
    -> Unity WebGL / Soren91 (sorengame91 on unityroom.com)
    -> hardware WebGL (ANGLE Metal backend)
  tools/macos/soren91_window_capture (Swift, ScreenCaptureKit)
    -> captures ONE window by exact bundle id + exact title (fail-closed)
    -> raw BGRA frames -> stdout, piped into ffmpeg's stdin
  ffmpeg
    -> crops the browser-chrome band only (window-relative offset)
    -> h264_videotoolbox
    -> SRT caller over Tailscale -> OCI listener
```

Normal SorenGame / the main broadcast keep running on OCI throughout; this
renderer is only evaluated in isolation and is not switched into production
until it passes the full E2E gate in Issue #303.

## Why not avfoundation screen capture (real incident, see Issue #303)

The first version of this PoC captured the whole display with `ffmpeg -f
avfoundation` and cropped to a **fixed screen coordinate**. During SRT/OCI
E2E testing this recorded and transmitted the operator's own everyday Chrome
window (tabs, an active Claude.ai session) for 91.7 seconds — because
avfoundation records whatever is on-screen at that coordinate, not a specific
window, and another window happened to be in front at that position. No
production stream was connected, but this is exactly the class of bug a
screen-capture design must not have.

`tools/macos/soren91_window_capture.swift` replaces that with
`SCContentFilter(desktopIndependentWindow:)` (ScreenCaptureKit), which binds
the capture to **one window's content**, selected by an **exact** match on
owning-application bundle id AND window title. If zero or more than one
window matches, it exits non-zero and writes no frame data — it never
guesses (fail-closed; see the file's header comment). Because the capture is
scoped by window identity rather than screen position, the stream can never
contain a different window's content regardless of z-order, occlusion, or
where the window sits on screen. This was directly verified (not assumed):
a synthetic decoy window was captured correctly, and — with a second window
fully covering it on the real display — the capture still returned the
correct (occluded) window's content, never the covering window's.

Chrome's app-mode window still shows its tab/address bar once you navigate
it to a different origin than it launched with (`--app=about:blank` then
`page.goto(...)`) — Chrome's own anti-spoofing behavior, confirmed by
capturing the window at its native size and visually inspecting the frame.
That chrome band is trimmed by a **window-relative** crop
(`chromeTop`/`chromeLeft`, computed from real CDP measurements — see
`calibrateWindowBounds` in the renderer), applied to frames already scoped
to the correct window by ScreenCaptureKit. If that offset were ever
miscalculated, the worst case is a misframed shot of the *same, correct*
window (e.g. a sliver of its own chrome visible) — never another window's
content, unlike the original bug.

## Prerequisites

- macOS, Apple Silicon (verified on Apple M4) or Intel with a Metal-capable GPU.
- Swift toolchain (`swiftc`, ships with Xcode Command Line Tools) to build
  the capture helper. `tools/soren91_window_capture_build.sh` builds
  `tools/macos/bin/soren91_window_capture` from
  `tools/macos/soren91_window_capture.swift` (only if missing or stale).
- An existing Google Chrome install. Playwright drives it via `channel: 'chrome'`
  (`chromium.launch({ channel: 'chrome' })`) — **no managed Chromium download is
  triggered** as long as this succeeds. Only fall back to
  `npx playwright install chromium --no-shell` if `channel: 'chrome'` fails to
  launch, and note the `~/Library/Caches/ms-playwright` size delta if you do.
- `npm install` in this checkout (installs `playwright` from `package.json`;
  does not download a browser).
- **Screen Recording permission**, for BOTH: whichever app hosts the
  `ffmpeg`/`node` process, AND the `soren91_window_capture` binary itself
  (ScreenCaptureKit's `SCShareableContent` call fails, or your Mac shows a
  fresh permission entry for the binary, if it's missing). Grant via System
  Settings → Privacy & Security → Screen Recording.
- FFmpeg with `h264_videotoolbox` (Homebrew's default `ffmpeg` formula has
  this) **and** the `srt` protocol. **Homebrew's default `ffmpeg` formula does
  NOT build libsrt** — `ffmpeg -hide_banner -protocols | grep srt` only shows
  `srtp` (unrelated SRTP), not `srt`. Verified on this Mac with
  `ffmpeg 8.0.1_4` from `homebrew-core`. To get SRT support, install the
  keg-only `ffmpeg-full` formula (`brew install ffmpeg-full`, ~47 dependencies)
  and point `SOREN91_LOCAL_FFMPEG_BIN` at
  `$(brew --prefix ffmpeg-full)/bin/ffmpeg` instead of replacing the default
  `ffmpeg` symlink.

## Login-session-only, no launchd

Playwright launches headed Chrome, and the capture helper needs a real
WindowServer connection (a bare CLI executable asserts `CGS_REQUIRE_INIT`
without one — `soren91_window_capture` establishes it at startup via
`NSApplication.shared.setActivationPolicy(.accessory)`, which stays headless
— no Dock icon). Both require a logged-in interactive session — the same
constraint as the Windows backend. Do not wrap this in a `launchd`
daemon/agent that runs outside a real GUI session.

## Files

- `tools/soren91_macos_renderer.mjs` — launches existing Chrome, calibrates
  the window's real (CDP-measured) outer size and chrome-band offset, logs
  into the live Soren91 match, measures native-rAF fps and WebGL facts for
  `SOREN91_LOCAL_MEASURE_SEC` (default 60s), writes a result JSON (including
  `capture: { bundleId, windowTitle, outerWidth, outerHeight, chromeTop,
  chromeLeft }`) to `SOREN91_LOCAL_RESULT_PATH`, then idles until stopped.
- `tools/macos/soren91_window_capture.swift` — ScreenCaptureKit CLI helper;
  captures exactly one window (fail-closed identity match) at its native
  outer size, writes tightly-packed raw BGRA8888 frames to stdout. Build via
  `tools/soren91_window_capture_build.sh`.
- `tools/soren91_macos_session.mjs` — same options/validation contract as the
  Windows session (`sessionSec` capped at 1800s / `hardMaxSec` at 2400s,
  960x540/30fps fixed output, SRT URL must be a Tailscale IPv4 `srt://` URL
  with `mode=caller`, no userinfo, an explicit port, and no `passphrase=` in
  argv). Orchestrates renderer → capture helper → ffmpeg (rawvideo pipe →
  chrome-band crop → VideoToolbox → SRT). The capture helper's first stderr
  line is its readiness/failure signal (fail-closed — see
  `parseCaptureHelperStatus`); frames are only piped into ffmpeg after an
  `ok:true` status.
- `tests/test_soren91_macos_session.mjs` — contract tests (`node --test`).

## Manual verification checklist (what to actually run)

```bash
npm install
node tools/check_existing_chrome.mjs   # optional: confirms channel:'chrome' works
tools/soren91_window_capture_build.sh  # builds tools/macos/bin/soren91_window_capture
node --test tests/test_soren91_macos_session.mjs

# Dry run (prints the plan, does not launch anything):
node tools/soren91_macos_session.mjs --srt-url srt://100.64.0.2:19192?mode=caller

# Real 5-minute PoC (joins the live match under a distinguishable player name,
# e.g. DoCiAI:MC — set SOREN91_LOCAL_PLAYER_NAME to change it):
SOREN91_LOCAL_SESSION_SEC=300 SOREN91_LOCAL_HARD_MAX_SEC=420 \
  node tools/soren91_macos_session.mjs --execute --srt-url srt://<oci-tailscale-ip>:<port>?mode=caller
```

## Troubleshooting

- **`CGS_REQUIRE_INIT` assertion from the capture helper**: this means the
  `NSApplication.shared.setActivationPolicy(.accessory)` call at the top of
  `main()` in `soren91_window_capture.swift` is missing or was optimized
  away; rebuild from the current source.
- **Capture helper exits with `found 0` / `found >1` (fail-closed)**: the
  renderer's measured `windowTitle` didn't exactly match any (or matched
  more than one) `com.google.Chrome` window at capture time — check the
  `candidate titles` list in its error output. Don't relax this to a
  substring match; that reintroduces the original ambiguity bug.
- **Capture hangs / produces no frames**: Screen Recording permission not
  granted to `soren91_window_capture`; grant it in System Settings and rerun
  (may need to rebuild/re-grant after the binary's path or hash changes).
- **`ffmpeg does not expose the SRT protocol`**: default Homebrew `ffmpeg`
  lacks libsrt; see Prerequisites above.
- **`window content size mismatch after calibration`**: some other window
  manager / Stage Manager tiling interfered with `Browser.setWindowBounds`;
  disable Stage Manager or window tiling before running.
- **Renderer reports `hardwareRenderer: false`**: `UNMASKED_RENDERER_WEBGL`
  matched `swiftshader`/`llvmpipe`/`software`, or didn't match
  `apple`/`angle`/`metal` at all — check `chrome://gpu` for GPU blocklist
  entries on this Mac.
