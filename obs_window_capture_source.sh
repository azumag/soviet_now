#!/bin/bash
# obs_window_capture_source.sh - ensure an OBS macOS window capture source targets a real window.
#
# Usage:
#   ./obs_window_capture_source.sh ensure <scene> <source> <window-title-regex> [app-id] [show|hide]

set -euo pipefail
cd "$(dirname "$0")"
[ -f .env ] && set -a && . ./.env && set +a

usage() {
	cat <<'EOF' >&2
Usage:
  ./obs_window_capture_source.sh ensure <scene> <source> <window-title-regex> [app-id] [show|hide]
EOF
	exit 2
}

cmd="${1:-}"
[ "$cmd" = "ensure" ] || usage
[ "$#" -ge 4 ] || usage

scene="$2"
source_name="$3"
window_title_regex="$4"
app_id="${5:-com.google.chrome.for.testing}"
visibility="${6:-show}"

case "$visibility" in
show|hide) ;;
*) usage ;;
esac

if [ -z "${OBS_WEBSOCKET_PORT:-}" ] || [ -z "${OBS_WEBSOCKET_PASSWORD:-}" ]; then
	echo "[obs_window_capture_source] OBS_WEBSOCKET_PORT/PASSWORD not set" >&2
	exit 1
fi

NODE_BIN="${NODE_BIN:-$(command -v node 2>/dev/null || true)}"
if [ -z "$NODE_BIN" ]; then
	for candidate in \
		"$HOME/.nvm/versions/node/v23.10.0/bin/node" \
		"/opt/homebrew/bin/node" \
		"/usr/local/bin/node" \
		"/Volumes/satelite/homebrew/homebrew/bin/node"; do
		if [ -x "$candidate" ]; then
			NODE_BIN="$candidate"
			break
		fi
	done
fi
if [ -z "$NODE_BIN" ]; then
	echo "[obs_window_capture_source] node not found" >&2
	exit 1
fi

cleanup_stale_wildcard_candidates_for_main_game() {
	[ "${OBS_WINDOW_CAPTURE_CLEAN_STALE_WILDCARD:-1}" = "1" ] || return 0
	[ "$source_name" = "${OBS_MAIN_GAME_SOURCE:-sorengame}" ] || return 0
	case "$window_title_regex" in
	*soren-game*) ;;
	*) return 0 ;;
	esac
	[ -f ./wildcard_parallel.py ] || return 0

	mkdir -p "${TMP_DEBUG_DIR:-tmp/debug}"
	"${PYTHON:-python3}" ./wildcard_parallel.py --cleanup-stale \
		--session-root "${WILDCARD_PARALLEL_WORK_DIR:-tmp/wildcard_parallel}" \
		--status-file "${WILDCARD_PARALLEL_STATUS_FILE:-tmp/state/wildcard_parallel_status.json}" \
		--html-file "${WILDCARD_PARALLEL_HTML_FILE:-tmp/state/wildcard_parallel_overlay.html}" \
		>>"${TMP_DEBUG_DIR:-tmp/debug}/wildcard_parallel_cleanup.log" 2>&1 || true
}

cleanup_stale_wildcard_candidates_for_main_game

# Serialize the SetInputSettings below against every other process that updates a
# mac-capture source (game-capture watchdog, main soviet_local bridge, soren91,
# sibling wildcard candidate slots). Concurrent obs_source_update on mac-capture
# double-frees and crashes OBS outright. See lib/obs_source_lock.sh.
if [ -f ./lib/obs_source_lock.sh ]; then . ./lib/obs_source_lock.sh; fi
command -v obs_source_lock_acquire >/dev/null 2>&1 || obs_source_lock_acquire() { return 1; }
command -v obs_source_lock_release >/dev/null 2>&1 || obs_source_lock_release() { return 0; }
obs_source_lock_acquire || true
trap 'obs_source_lock_release || true' EXIT

"$NODE_BIN" --input-type=commonjs - "$scene" "$source_name" "$window_title_regex" "$app_id" "$visibility" <<'NODE'
const crypto = require('crypto');

const [sceneName, sourceName, titlePatternRaw, appId, visibility] = process.argv.slice(2);
const host = process.env.OBS_WEBSOCKET_HOST || '127.0.0.1';
const port = Number(process.env.OBS_WEBSOCKET_PORT || 4455);
const password = process.env.OBS_WEBSOCKET_PASSWORD || '';
const requestTimeoutMs = Number(process.env.OBS_WEBSOCKET_TIMEOUT_MS || 8000);
const url = `ws://${host}:${port}`;
const enabled = visibility === 'show';
const inputKind = process.env.OBS_WINDOW_CAPTURE_INPUT_KIND || 'screen_capture';
const allowReplaceWrongKind = process.env.OBS_WINDOW_CAPTURE_REPLACE_WRONG_KIND !== '0';
const enforceTopOverlayStack = process.env.OBS_ENFORCE_TOP_OVERLAY_STACK !== '0';
const topOverlaySource = process.env.OBS_TOP_OVERLAY_SOURCE || process.env.TWICA_OVERLAY_SOURCE || 'twica';
const belowTopOverlaySource = process.env.OBS_BELOW_TOP_OVERLAY_SOURCE || process.env.OBS_EVENT_OVERLAY_SOURCE || 'eventOverlay';
const titlePattern = new RegExp(titlePatternRaw);
const chromeWindowPattern = /\[Google Chrome(?: for Testing)?\]/;
const muteInputAudio = /^wildcardParallelCand\d+$/.test(sourceName)
  && process.env.WILDCARD_PARALLEL_CANDIDATE_AUDIO !== '1';
const captureAudioSetting = (() => {
  const raw = process.env.OBS_WINDOW_CAPTURE_AUDIO;
  if (raw === '1' || raw === 'true' || raw === 'yes' || raw === 'on') return true;
  if (raw === '0' || raw === 'false' || raw === 'no' || raw === 'off') return false;
  return null;
})();
const appAudioSourceName = process.env.OBS_WINDOW_AUDIO_SOURCE || '';
const appAudioSourceEnabled = (() => {
  const raw = process.env.OBS_WINDOW_AUDIO_SOURCE_ENABLED;
  if (raw === '1' || raw === 'true' || raw === 'yes' || raw === 'on') return true;
  if (raw === '0' || raw === 'false' || raw === 'no' || raw === 'off') return false;
  return enabled;
})();

function fail(message, code = 1) {
  console.error(`[obs_window_capture_source] ${message}`);
  process.exit(code);
}

if (typeof WebSocket !== 'function') {
  fail('Global WebSocket is not available in this Node.js runtime');
}

function sha256Base64(text) {
  return crypto.createHash('sha256').update(text).digest('base64');
}

async function connectAndIdentify() {
  const ws = new WebSocket(url);
  const state = { ws, requestSeq: 0, hello: null, ready: false, pending: new Map() };

  const cleanupPending = (error) => {
    for (const { reject, timer } of state.pending.values()) {
      clearTimeout(timer);
      reject(error);
    }
    state.pending.clear();
  };

  await new Promise((resolve, reject) => {
    const timer = setTimeout(() => {
      reject(new Error(`Timed out connecting to ${url}`));
      try { ws.close(); } catch (_) {}
    }, requestTimeoutMs);
    ws.addEventListener('open', () => {
      clearTimeout(timer);
      resolve();
    });
    ws.addEventListener('error', (event) => {
      clearTimeout(timer);
      reject(new Error(event && event.error && event.error.message ? event.error.message : `Failed to connect to ${url}`));
    });
  });

  ws.addEventListener('message', (event) => {
    let payload;
    try {
      payload = JSON.parse(String(event.data));
    } catch (err) {
      cleanupPending(err);
      return;
    }
    if (payload.op === 0) {
      state.hello = payload.d || {};
      return;
    }
    if (payload.op === 2) {
      state.ready = true;
      return;
    }
    if (payload.op === 7) {
      const data = payload.d || {};
      const requestId = data.requestId;
      if (!requestId || !state.pending.has(requestId)) return;
      const pending = state.pending.get(requestId);
      state.pending.delete(requestId);
      clearTimeout(pending.timer);
      const status = data.requestStatus || {};
      if (status.result) {
        pending.resolve(data.responseData || {});
      } else {
        pending.reject(new Error(`${data.requestType} failed (${status.code}): ${status.comment || 'unknown error'}`));
      }
    }
  });

  const deadline = Date.now() + requestTimeoutMs;
  while (!state.hello) {
    if (Date.now() > deadline) throw new Error('Timed out waiting for OBS Hello');
    await new Promise(resolve => setTimeout(resolve, 25));
  }

  const identify = { op: 1, d: { rpcVersion: 1, eventSubscriptions: 0 } };
  const auth = state.hello.authentication;
  if (auth && auth.challenge && auth.salt) {
    const secret = sha256Base64(password + auth.salt);
    identify.d.authentication = sha256Base64(secret + auth.challenge);
  }
  ws.send(JSON.stringify(identify));

  const readyDeadline = Date.now() + requestTimeoutMs;
  while (!state.ready) {
    if (Date.now() > readyDeadline) throw new Error('Timed out waiting for OBS Identify');
    await new Promise(resolve => setTimeout(resolve, 25));
  }

  state.request = (requestType, requestData = {}) => {
    const requestId = `req-${++state.requestSeq}`;
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        state.pending.delete(requestId);
        reject(new Error(`${requestType} timed out`));
      }, requestTimeoutMs);
      state.pending.set(requestId, { resolve, reject, timer });
      ws.send(JSON.stringify({ op: 6, d: { requestType, requestId, requestData } }));
    });
  };

  state.close = async () => {
    cleanupPending(new Error('OBS connection closed'));
    try { ws.close(); } catch (_) {}
  };

  return state;
}

async function getInput(obs, inputName) {
  const response = await obs.request('GetInputList');
  return (response.inputs || []).find(input => input.inputName === inputName) || null;
}

async function ensureInput(obs) {
  let input = await getInput(obs, sourceName);
  if (input && input.unversionedInputKind !== inputKind && input.inputKind !== inputKind) {
    if (!allowReplaceWrongKind || !/^wildcardParallelCand\d+$/.test(sourceName)) {
      throw new Error(`${sourceName} is ${input.inputKind}, not ${inputKind}`);
    }
    await obs.request('RemoveInput', { inputName: sourceName });
    input = null;
  }

  const initialSettings = {
    type: 1,
    application: appId,
    window: 0,
    show_cursor: false,
    show_empty_names: false,
  };
  if (captureAudioSetting !== null) {
    initialSettings.capture_audio = captureAudioSetting;
    initialSettings.audio = captureAudioSetting;
  }

  if (!input) {
    await obs.request('CreateInput', {
      sceneName,
      inputName: sourceName,
      inputKind,
      inputSettings: initialSettings,
      sceneItemEnabled: false,
    });
    return;
  }

  const list = await obs.request('GetSceneItemList', { sceneName });
  const items = Array.isArray(list.sceneItems) ? list.sceneItems : [];
  if (!items.some(item => item.sourceName === sourceName)) {
    await obs.request('CreateSceneItem', { sceneName, sourceName, sceneItemEnabled: false });
  }
}

async function setSceneItemEnabled(obs, sceneItemEnabled) {
  const list = await obs.request('GetSceneItemList', { sceneName });
  const items = Array.isArray(list.sceneItems) ? list.sceneItems : [];
  for (const item of items.filter(item => item.sourceName === sourceName)) {
    await obs.request('SetSceneItemEnabled', {
      sceneName,
      sceneItemId: item.sceneItemId,
      sceneItemEnabled,
    });
  }
}

async function ensureAppAudioSource(obs) {
  if (!appAudioSourceName) return;

  let input = await getInput(obs, appAudioSourceName);
  if (!input) {
    await obs.request('CreateInput', {
      sceneName,
      inputName: appAudioSourceName,
      inputKind: 'sck_audio_capture',
      inputSettings: {
        type: 1,
        application: appId,
      },
      sceneItemEnabled: false,
    });
  } else {
    await obs.request('SetInputSettings', {
      inputName: appAudioSourceName,
      inputSettings: {
        type: 1,
        application: appId,
      },
      overlay: true,
    });
  }

  await obs.request('SetInputMute', { inputName: appAudioSourceName, inputMuted: false });

  const list = await obs.request('GetSceneItemList', { sceneName });
  const items = Array.isArray(list.sceneItems) ? list.sceneItems : [];
  if (!items.some(item => item.sourceName === appAudioSourceName)) {
    await obs.request('CreateSceneItem', {
      sceneName,
      sourceName: appAudioSourceName,
      sceneItemEnabled: false,
    });
  }
}

async function setAppAudioSourceEnabled(obs, sceneItemEnabled) {
  if (!appAudioSourceName) return;
  const list = await obs.request('GetSceneItemList', { sceneName });
  const items = Array.isArray(list.sceneItems) ? list.sceneItems : [];
  for (const item of items.filter(item => item.sourceName === appAudioSourceName)) {
    await obs.request('SetSceneItemEnabled', {
      sceneName,
      sceneItemId: item.sceneItemId,
      sceneItemEnabled,
    });
  }
}

async function enforceOverlayStack(obs) {
  if (!enforceTopOverlayStack || !topOverlaySource || !belowTopOverlaySource) return;
  if (topOverlaySource === belowTopOverlaySource) return;

  const list = await obs.request('GetSceneItemList', { sceneName });
  const items = Array.isArray(list.sceneItems) ? list.sceneItems : [];
  const stack = [belowTopOverlaySource, topOverlaySource];
  const startIndex = Math.max(0, items.length - stack.length);

  for (let offset = 0; offset < stack.length; offset += 1) {
    const item = items.find(entry => entry.sourceName === stack[offset]);
    if (!item) continue;
    await obs.request('SetSceneItemIndex', {
      sceneName,
      sceneItemId: item.sceneItemId,
      sceneItemIndex: startIndex + offset,
    });
  }
}

async function main() {
  const obs = await connectAndIdentify();
  try {
    await ensureInput(obs);
    await ensureAppAudioSource(obs);

    const response = await obs.request('GetInputPropertiesListPropertyItems', {
      inputName: sourceName,
      propertyName: 'window',
    });
    const windows = Array.isArray(response.propertyItems) ? response.propertyItems : [];
    const target = windows.find(item =>
      chromeWindowPattern.test(item.itemName || '') && titlePattern.test(item.itemName || '')
    );
    if (!target) {
      await setSceneItemEnabled(obs, false).catch(() => {});
      await setAppAudioSourceEnabled(obs, false).catch(() => {});
      throw new Error(`target window not found for ${sourceName}: /${titlePatternRaw}/`);
    }

    const inputSettings = {
      type: 1,
      application: appId,
      window: target.itemValue,
      show_cursor: false,
      show_empty_names: false,
    };
    if (captureAudioSetting !== null) {
      inputSettings.capture_audio = captureAudioSetting;
      inputSettings.audio = captureAudioSetting;
    }

    await obs.request('SetInputSettings', {
      inputName: sourceName,
      inputSettings,
      overlay: true,
    });
    if (muteInputAudio) {
      await obs.request('SetInputMute', { inputName: sourceName, inputMuted: true });
    }

    await setSceneItemEnabled(obs, enabled);
    await setAppAudioSourceEnabled(obs, appAudioSourceEnabled);
    await enforceOverlayStack(obs);

    console.log(`window-capture:${sourceName}:${visibility}:${target.itemName} (${target.itemValue})`);
  } finally {
    await obs.close();
  }
}

main().catch(err => fail(err && err.message ? err.message : String(err)));
NODE
