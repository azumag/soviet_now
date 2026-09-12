# Soren91 macOS local renderer (Tier -1, PoC)

Mirrors the Windows local renderer (`tools/soren91_windows_renderer.mjs` /
`tools/soren91_windows_session.mjs`, PR #131) for a Mac with Apple Silicon
hardware WebGL, targeting the same `start/status/stop` contract. See
[Issue #303](https://github.com/azumag/soviet_now/issues/303).

**Status (2026-09-13): capture pipeline rebuilt around ScreenCaptureKit
window-targeted capture, after a real incident with the original
avfoundation design (see "Why not avfoundation screen capture" below).
Since 2026-09-13 the game window additionally lives on a **private virtual
display (offscreen by default)** — nothing game-related ever shows on a
physical screen (see "Offscreen virtual display" below). Validated so far
with a synthetic decoy window (exact-match identity selection + occlusion
resilience, both confirmed by pixel inspection), an early single real local
run against the live Soren91 match (local file only, no SRT/OCI — before
the real-game SRT/OCI run below), and a synthetic offscreen run (virtual
display + parked Chrome + zero physical overlap measured + ScreenCaptureKit
capture at 28.80fps + clean teardown), plus a real-game SRT/OCI E2E through
the offscreen pipeline — PASS (142.03s received on OCI, h264, 960x540,
30fps, 4261 frames decoded, via a temporary Tailscale-restricted listener,
never touching the production broadcast). Backend selector wiring
(platform-generic local agent + pure selection logic, see "Local agent /
backend selector" below) is integrated at the code/docs level, and the
OCI-side controller that calls the local agent is implemented at the code
level (`tools/soren91_renderer_controller.mjs`, loopback/mock tested only —
see "OCI-side controller" below; live OCI↔Mac E2E is still a separate step,
same gap as the Windows PoC, PR #131). Still not soaked for 30 minutes,
not connected to the production broadcast.**

## Offscreen virtual display (default, Issue #303)

ScreenCaptureKit captures a window by identity even when that window is
occluded or off the visible display — so the session parks the Soren91
Chrome window on a **private `CGVirtualDisplay`** (a display that exists
only in software) instead of any physical screen:

```text
tools/macos/soren91_virtual_display (Swift holder, built by
  tools/soren91_virtual_display_build.sh)
  -> creates one 1920x1080@60 virtual display, prints
     {"ok":true,"displayID":N,"bounds":{x,y,width,height}} on stderr,
     survives until SIGTERM/SIGINT (only process exit releases it)
soren91_macos_session.mjs
  -> spawns the holder BEFORE the renderer, passes the MEASURED bounds
     as SOREN91_LOCAL_VDISPLAY_BOUNDS to the renderer
  -> on shutdown SIGTERMs the holder and WAITS for its exit (proving the
     display is released) before returning
soren91_macos_renderer.mjs
  -> parks Chrome at virtual-origin + margin, calibrates, then PROVES from
     the MEASURED window rect + helper --list output that the window touches
     no physical display — even 1px of overlap fails the run (fail-closed)
  -> result JSON carries capture.offscreen = { requested, displayID, bounds,
     windowBounds, physicalOverlap: false }
```

Privacy rules enforced in code, not just docs (fail-closed everywhere):

- The holder is required by default (`SOREN91_LOCAL_OFFSCREEN=1`). If it is
  missing or fails, the session **errors out** — it never silently falls
  back to a visible window. The legacy on-screen behavior needs an explicit
  opt-in: `--allow-onscreen` or `SOREN91_LOCAL_ALLOW_ONSCREEN=1`
  (additionally required when `SOREN91_LOCAL_OFFSCREEN=0`).
- Window coordinates are **never assumed from `--window-position`**: macOS
  placed the window at y=30 when asked for y=20 (measured). Placement and
  the overlap proof both use measured bounds only.
- The overlap proof needs no pixels and no window titles — display IDs +
  bounds arithmetic only (`tools/soren91_offscreen_verify.mjs`, unit
  tested). If the proof cannot run (helper missing), the run fails.
- `CGVirtualDisplay` is a non-public API reached via runtime class lookup +
  KVC (no private headers; `NSClassFromString("CGVirtualDisplay")` etc.).
  If the classes/selectors vanish on a future macOS, the helper exits 1
  with `{"ok":false,...}` instead of crashing. Measured quirks kept as code
  comments in `tools/macos/soren91_virtual_display.swift`: mode refreshRate
  is Double (UInt32 makes `applySettings:` return NO), `setDispatchQueue:`
  must be called directly (KVC does not stick), vendor/product/serial
  setters write into the internal `_displayInfo` dict (which must be
  non-nil), and only one virtual display per process, released by process
  exit only — so a stale display from a previous run is reported as an
  explicit "still online" error naming the leaked display.
- Vendor/product/serial are fixed (`0x736F`/`0x3931`/30391) so macOS
  remembers window placement across holder restarts.

Known side effect: adding a display extends the menu bar onto the virtual
display while the holder runs; no existing user windows are moved. Seen on
macOS 26.6.1 (M4) — the only OS this is tested on.

Explicit on-screen fallback (visible window — use only for debugging):

```bash
SOREN91_LOCAL_ALLOW_ONSCREEN=1 SOREN91_LOCAL_OFFSCREEN=0 \
  node tools/soren91_macos_session.mjs --execute --srt-url srt://<oci-tailscale-ip>:<port>?mode=caller
```

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
  Settings → Privacy & Security → Screen Recording. The
  `soren91_virtual_display` holder itself needs no Screen Recording
  permission (it creates a display but captures nothing) — verified without
  any prompt on macOS 26.6.1.
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

## Local agent / backend selector (Issue #303)

The Mac is a `backend: 'local-macos'` local candidate, controlled through
the shared HTTP agent `tools/soren91_local_agent.mjs` (platform-generic:
`darwin` → `local-macos` / `soren91_macos_session.mjs`,
`win32` → `local-windows` / `soren91_windows_session.mjs`, same Bearer-token
contract as PR #131 — the two implementations are meant to converge into
this one file when PR #131 merges).

Start the agent (token from a secret store — never commit it, never pass it
via argv, never log it):

```bash
export SOREN91_LOCAL_AGENT_TOKEN="$(secret-store-read soren91/local-agent-token)" # >= 24 chars
export SOREN91_LOCAL_SRT_URL='srt://<oci-tailscale-ip>:<port>?mode=caller'
node tools/soren91_local_agent.mjs
```

Control API (all but `/health` need
`Authorization: Bearer $SOREN91_LOCAL_AGENT_TOKEN`):

```text
GET  /health    (no auth)  -> { ok, service:'soren91-local-agent', backend:'local-macos' }
GET  /v1/status (auth)     -> { ok, backend, running, pid, lastExit }
POST /v1/start  (auth)     -> 202 { ok, started, pid } / 409 { error:'already running' }
POST /v1/stop   (auth)     -> 202 { ok, stopping }
```

`POST /v1/start` accepts an optional JSON body `{ "srtUrl": "srt://..." }`
(max 8KB; additive extension — PR #131's agent ignores the body entirely).
When present, the URL must be a Tailscale IPv4 `srt://` URL with an explicit
port, `mode=caller`, no userinfo, and no `passphrase=` (same rules as the
session's `--srt-url`); it overrides `SOREN91_LOCAL_SRT_URL` for the spawned
child only (`process.env` is never mutated). Absent/empty body keeps the
legacy behavior (child inherits the agent's own environment). Invalid bodies
→ `400 { ok:false, error }`, oversized bodies → `400`/`413`.

Rules: default bind is `127.0.0.1:19191` (loopback only, never expose
publicly); only one session at a time (second `POST /v1/start` → 409);
`/v1/start` inherits the agent's own `process.env` (SRT URL, ffmpeg path,
session caps) into the child. Session length stays hard-capped by
`soren91_macos_session.mjs` (`sessionSec` ≤ 1800s, `hardMaxSec` ≤ 2400s).

Selection order lives in `tools/soren91_renderer_priority.mjs`
(`selectRendererBackend`, pure function — no cloud API calls): usable local
hosts in `localOrder` first → `powergpu-p4-interruptible` →
`powergpu-p4-ondemand` → any other usable candidate. The default
`localOrder` is provisionally `local-macos` → `local-windows`; per Issue
#303 the real Mac/Windows order is decided by measured boot time, stability,
and power draw, which are not measured yet — override `localOrder` once
real numbers exist. Falling back to a PowerGPU P4 host (actually launching
one) is Issue #309's scope, not this file's.

## OCI-side controller (Issue #303)

`tools/soren91_renderer_controller.mjs` runs on OCI and wires selection to
execution: probe the local agents → pick a backend with
`selectRendererBackend` → start an SRT listener on the OCI Tailscale IP →
`POST /v1/start { srtUrl }` to the chosen agent → wait → `POST /v1/stop` →
guaranteed listener kill (try/finally, even on failure).

```bash
# Tokens via a secret store — never commit them, never pass via argv.
export SOREN91_LOCAL_AGENTS_JSON='[{"backend":"local-macos","baseUrl":"http://<mac-tailscale-ip>:19191","token":"..."}]'
export SOREN91_OCI_TAILSCALE_IP='<oci-tailscale-ip>'   # required, 100.64.0.0/10
export SOREN91_OCI_SRT_PORT=19192                      # default 19192
export SOREN91_OCI_POC_OUT=/tmp/soren91-local-poc.ts   # default
export SOREN91_OCI_POC_SEC=90                          # default 90, minimum 90
export SOREN91_OCI_FFMPEG_BIN=ffmpeg                   # default

# Plan only (probes agents, prints the JSON plan, spawns nothing):
node tools/soren91_renderer_controller.mjs

# Execute for real (listener + agent session, then teardown):
node tools/soren91_renderer_controller.mjs --execute
```

Behavior: the listener binds the Tailscale IP only (`srt://<ip>:<port>?mode=listener`,
never `0.0.0.0`/public, no passphrase); the agent gets the matching
`srt://<ip>:<port>?mode=caller` in the `/v1/start` body (a `409` means
"already running" and is still stopped afterwards). Tokens never appear in
argv, logs, or error messages. If selection yields a `powergpu-*` backend,
the controller exits non-zero reporting it as unimplemented (Issue #309) —
it never launches cloud capacity. Extra cloud candidates can be injected
programmatically via `runController(..., { extraCandidates })`, which is
where Issue #309's launcher will plug in.

Loopback/mock testing (no real OCI/Mac connection — same scope as CI):

```bash
node --check tools/soren91_renderer_controller.mjs
node --test tests/test_soren91_renderer_controller.mjs
```

Live OCI↔Mac E2E is a separate step, not covered here.

## Files

- `tools/soren91_macos_renderer.mjs` — launches existing Chrome, calibrates
  the window's real (CDP-measured) outer size and chrome-band offset, logs
  into the live Soren91 match, measures native-rAF fps and WebGL facts for
  `SOREN91_LOCAL_MEASURE_SEC` (default 60s), writes a result JSON (including
  `capture: { bundleId, windowTitle, outerWidth, outerHeight, chromeTop,
  chromeLeft, offscreen }` — the `offscreen` proof lives inside `capture`
  when parked on the virtual display) to
  `SOREN91_LOCAL_RESULT_PATH`, then idles until stopped. When
  `SOREN91_LOCAL_VDISPLAY_BOUNDS` is set, the window is parked inside those
  bounds and the run fails unless the measured window rect provably avoids
  every physical display (see `verifyOffscreenPlacement`).
- `tools/macos/soren91_virtual_display.swift` — private-CGVirtualDisplay
  holder CLI; creates one virtual display and lives until SIGTERM/SIGINT.
  `--list` prints all online displays as JSON without creating anything.
  Build via `tools/soren91_virtual_display_build.sh`. Fixed
  vendor/product/serial; leak detection via `CGDisplayVendorNumber` /
  `CGDisplayModelNumber` / `CGDisplaySerialNumber`.
- `tools/soren91_offscreen_verify.mjs` — pure bounds-math helpers shared by
  session and renderer (bounds parsing, window placement, 1px-exact physical
  overlap proof, `--list` parsing). No pixels, no titles.
- `tools/macos/soren91_window_capture.swift` — ScreenCaptureKit CLI helper;
  captures exactly one window (fail-closed identity match) at its native
  outer size, writes tightly-packed raw BGRA8888 frames to stdout. Build via
  `tools/soren91_window_capture_build.sh`.
- `tools/soren91_macos_session.mjs` — same options/validation contract as the
  Windows session (`sessionSec` capped at 1800s / `hardMaxSec` at 2400s,
  960x540/30fps fixed output, SRT URL must be a Tailscale IPv4 `srt://` URL
  with `mode=caller`, no userinfo, an explicit port, and no `passphrase=` in
  argv). Orchestrates virtual-display holder → renderer → capture helper →
  ffmpeg (rawvideo pipe → chrome-band crop → VideoToolbox → SRT). The
  holder's first stderr line is its readiness/failure signal (fail-closed —
  see `parseVirtualDisplayStatus`); holder failure errors out unless
  on-screen was explicitly allowed. Shutdown SIGTERMs the holder and waits
  for its exit, proving the virtual display is released.
- `tests/test_soren91_macos_session.mjs` — contract tests (`node --test`).
- `tools/soren91_local_agent.mjs` — platform-generic HTTP control agent
  (`127.0.0.1:19191`, Bearer token, one session at a time): `darwin` serves
  `backend: 'local-macos'` via `soren91_macos_session.mjs --execute`,
  `win32` serves `backend: 'local-windows'` (converges PR #131's agent).
- `tools/soren91_renderer_priority.mjs` — pure `selectRendererBackend()`
  (no cloud calls): usable locals in `localOrder` (default provisionally
  `local-macos` → `local-windows`, pending measurement) →
  `powergpu-p4-interruptible` → `powergpu-p4-ondemand` → other usable.
- `tools/soren91_renderer_controller.mjs` — OCI-side controller: probes the
  local agents, selects via `selectRendererBackend`, runs the Tailscale-bound
  SRT listener, starts/stops the chosen agent (`POST /v1/start { srtUrl }`).
  Plan mode without `--execute`; PowerGPU selection exits non-zero as
  unimplemented (Issue #309). fetch/spawn injectable for mock tests.
- `tests/test_soren91_local_agent.mjs`, `tests/test_soren91_renderer_priority.mjs`
  — agent/session/token/HTTP (409/404) and selector-order contract tests.
- `tests/test_soren91_renderer_controller.mjs` — controller contract tests
  (all fetch/spawn mocked): parse/probe/select, listener args, caller-URL
  validation, start/stop, plan/execute run-through, PowerGPU-unimplemented.
- `tests/test_soren91_macos_virtual_display.mjs` — offscreen bounds-proof
  unit tests + helper CLI contract tests (live parts run on macOS only and
  never create a display).

## Manual verification checklist (what to actually run)

```bash
npm install
node tools/check_existing_chrome.mjs   # optional: confirms channel:'chrome' works
tools/soren91_window_capture_build.sh  # builds tools/macos/bin/soren91_window_capture
tools/soren91_virtual_display_build.sh # builds tools/macos/bin/soren91_virtual_display
node --test tests/test_soren91_macos_session.mjs tests/test_soren91_macos_virtual_display.mjs

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
- **Virtual display holder exits with `still online` (fail-closed)**: a
  previous holder process is still alive and holding its display (only
  process exit releases a `CGVirtualDisplay`). Find it with
  `pgrep -af soren91_virtual_display`, SIGTERM it (that holder only — never
  touch unrelated Chrome/OBS processes), then retry. The error names the
  leaked display's numeric id + bounds so you can confirm it is gone with
  the helper's `--list` afterwards.
- **Renderer fails with `offscreen violation`**: the measured Chrome window
  rect intersects a physical display — the window manager moved the window
  after placement (or the virtual display bounds shifted). Do NOT override
  this check; rerun and inspect the reported rects.
- **`offscreen verification unavailable`**: the session parked the window
  offscreen but the helper binary is missing at verify time — rebuild via
  `tools/soren91_virtual_display_build.sh` and rerun; the run refuses to
  proceed unverified.
