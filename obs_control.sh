#!/bin/bash
# obs_control.sh - OBS WebSocket v5 wrapper
# Usage:
#   ./obs_control.sh show <scene> <source> [<source>...]
#   ./obs_control.sh hide <scene> <source> [<source>...]
#   ./obs_control.sh status <scene> <source> [<source>...]
#   ./obs_control.sh batch <scene> show:<src1>,<src2> hide:<src3>,<src4>
#   ./obs_control.sh transform <scene> <source> <x> <y> <scaleX> <scaleY> [<boundsW> <boundsH>]
#   ./obs_control.sh stack <scene>
#     By default this only initializes a source that still has OBS' default
#     transform. Set OBS_CONTROL_TRANSFORM_MODE=force to overwrite manually
#     adjusted OBS transforms.

set -euo pipefail
cd "$(dirname "$0")"
[ -f .env ] && set -a && . ./.env && set +a

_log() { echo "[obs_control $(date '+%H:%M:%S')] $*" >&2; }

usage() {
	cat <<'EOF' >&2
Usage:
  ./obs_control.sh show <scene> <source> [<source>...]
  ./obs_control.sh hide <scene> <source> [<source>...]
  ./obs_control.sh status <scene> <source> [<source>...]
  ./obs_control.sh batch <scene> show:<src1>,<src2> hide:<src3>,<src4>
  ./obs_control.sh transform <scene> <source> <x> <y> <scaleX> <scaleY> [<boundsW> <boundsH>]
  ./obs_control.sh stack <scene>
    Set OBS_CONTROL_TRANSFORM_MODE=force to overwrite an existing manual transform.
  ./obs_control.sh stream-status            # prints "streaming=on|off"
  ./obs_control.sh stream-start             # start streaming (no-op if already live)
  ./obs_control.sh stream-stop              # stop streaming (no-op if not live)
EOF
	exit 2
}

ACTION="${1:-}"
TARGET="${2:-}"

case "$ACTION" in
show|hide|status)
	[ -n "$TARGET" ] || usage
	[ "$#" -ge 3 ] || usage
	;;
batch)
	[ -n "$TARGET" ] || usage
	[ "$#" -ge 3 ] || usage
	;;
stack)
	[ -n "$TARGET" ] || usage
	;;
transform)
	[ -n "$TARGET" ] || usage
	[ "$#" -ge 7 ] || usage
	;;
stream-status|stream-start|stream-stop)
	# 配信(ストリーム)制御。scene/source 指定は不要。
	;;
*)
	usage
	;;
esac

if [ -z "${OBS_WEBSOCKET_PORT:-}" ]; then
	_log "ERROR: OBS_WEBSOCKET_PORT not set"
	exit 1
fi

if [ -z "${OBS_WEBSOCKET_PASSWORD:-}" ]; then
	_log "ERROR: OBS_WEBSOCKET_PASSWORD not set"
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
	_log "ERROR: node not found"
	exit 1
fi

"$NODE_BIN" - "$@" <<'NODE'
const crypto = require('crypto');

const argv = process.argv.slice(2);
const action = argv[0];
const targetName = argv[1];
const rawArgs = argv.slice(2);

const host = process.env.OBS_WEBSOCKET_HOST || '127.0.0.1';
const port = Number(process.env.OBS_WEBSOCKET_PORT || 4455);
const password = process.env.OBS_WEBSOCKET_PASSWORD || '';
const url = `ws://${host}:${port}`;
const requestTimeoutMs = Number(process.env.OBS_WEBSOCKET_TIMEOUT_MS || 8000);
const transformMode = process.env.OBS_CONTROL_TRANSFORM_MODE || 'init';
// Bounds type used when boundsW/boundsH are given. Default SCALE_INNER (letterbox,
// whole source visible). SCALE_OUTER crops to fill the box (no gaps) — used for
// the wildcard candidate window tiles. Any valid OBS_BOUNDS_* value is accepted.
const boundsType = process.env.OBS_CONTROL_BOUNDS_TYPE || 'OBS_BOUNDS_SCALE_INNER';
const enforceTopOverlayStack = process.env.OBS_ENFORCE_TOP_OVERLAY_STACK !== '0';
const topOverlaySource = process.env.OBS_TOP_OVERLAY_SOURCE || process.env.TWICA_OVERLAY_SOURCE || 'twica';
const belowTopOverlaySource = process.env.OBS_BELOW_TOP_OVERLAY_SOURCE || process.env.OBS_EVENT_OVERLAY_SOURCE || 'eventOverlay';

function fail(message, code = 1) {
  console.error(`[obs_control] ${message}`);
  process.exit(code);
}

if (typeof WebSocket !== 'function') {
  fail('Global WebSocket is not available in this Node.js runtime');
}

function parseOperations() {
  const ops = [];

  if (action === 'show' || action === 'hide' || action === 'status') {
    for (const sourceName of rawArgs) {
      if (!sourceName) continue;
      ops.push({ sourceName, enabled: action === 'show' });
    }
  } else if (action === 'batch') {
    for (const token of rawArgs) {
      const sep = token.indexOf(':');
      if (sep <= 0) {
        fail(`Invalid batch token: ${token}`, 2);
      }
      const verb = token.slice(0, sep);
      const list = token.slice(sep + 1);
      if (verb !== 'show' && verb !== 'hide') {
        fail(`Invalid batch verb: ${verb}`, 2);
      }
      for (const sourceName of list.split(',').map(v => v.trim()).filter(Boolean)) {
        ops.push({ sourceName, enabled: verb === 'show' });
      }
    }
  } else {
    fail(`Unknown action: ${action}`, 2);
  }

  if (ops.length === 0) {
    fail('No source operations requested', 2);
  }

  const deduped = new Map();
  for (const op of ops) {
    deduped.set(op.sourceName, op.enabled);
  }
  return Array.from(deduped.entries()).map(([sourceName, enabled]) => ({ sourceName, enabled }));
}

function sha256Base64(text) {
  return crypto.createHash('sha256').update(text).digest('base64');
}

function numericValue(value, fallback = 0) {
  const num = Number(value);
  return Number.isFinite(num) ? num : fallback;
}

function approxEqual(a, b) {
  return Math.abs(numericValue(a) - numericValue(b)) < 0.0001;
}

function isDefaultTransform(transform = {}) {
  return approxEqual(transform.positionX, 0)
    && approxEqual(transform.positionY, 0)
    && approxEqual(transform.rotation, 0)
    && approxEqual(transform.scaleX, 1)
    && approxEqual(transform.scaleY, 1)
    && approxEqual(transform.cropLeft, 0)
    && approxEqual(transform.cropTop, 0)
    && approxEqual(transform.cropRight, 0)
    && approxEqual(transform.cropBottom, 0)
    && numericValue(transform.alignment, 5) === 5
    && (!transform.boundsType || transform.boundsType === 'OBS_BOUNDS_NONE')
    && approxEqual(transform.boundsWidth, 0)
    && approxEqual(transform.boundsHeight, 0);
}

async function enforceOverlayStack(obs, sceneName) {
  if (!enforceTopOverlayStack || !topOverlaySource || !belowTopOverlaySource) return;
  if (topOverlaySource === belowTopOverlaySource) return;

  const response = await obs.request('GetSceneItemList', { sceneName });
  const items = Array.isArray(response.sceneItems) ? response.sceneItems : [];
  const stack = [belowTopOverlaySource, topOverlaySource];
  const startIndex = Math.max(0, items.length - stack.length);
  const moved = [];

  for (let offset = 0; offset < stack.length; offset += 1) {
    const sourceName = stack[offset];
    const item = items.find(entry => entry.sourceName === sourceName);
    if (!item) continue;
    const sceneItemIndex = startIndex + offset;
    await obs.request('SetSceneItemIndex', {
      sceneName,
      sceneItemId: item.sceneItemId,
      sceneItemIndex,
    });
    moved.push(`${sourceName}:${sceneItemIndex}`);
  }

  if (moved.length > 0 && process.env.OBS_CONTROL_LOG_OVERLAY_STACK === '1') {
    console.error(`[obs_control] overlay-stack ${moved.join(' ')}`);
  }
}

async function connectAndIdentify() {
  const ws = new WebSocket(url);

  const state = {
    ws,
    requestSeq: 0,
    hello: null,
    ready: false,
    pending: new Map(),
  };

  const cleanupPending = (error) => {
    for (const { reject, timer } of state.pending.values()) {
      clearTimeout(timer);
      reject(error);
    }
    state.pending.clear();
  };

  const connectPromise = new Promise((resolve, reject) => {
    const connectTimer = setTimeout(() => {
      reject(new Error(`Timed out connecting to ${url}`));
      try { ws.close(); } catch (_) {}
    }, requestTimeoutMs);

    ws.addEventListener('open', () => {
      clearTimeout(connectTimer);
      resolve();
    });

    ws.addEventListener('error', (event) => {
      clearTimeout(connectTimer);
      const reason = event && event.error && event.error.message
        ? event.error.message
        : `Failed to connect to ${url}`;
      reject(new Error(reason));
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

  ws.addEventListener('close', (event) => {
    if (!state.ready) {
      cleanupPending(new Error(`OBS socket closed before ready (${event.code})`));
    }
  });

  await connectPromise;

  const helloDeadline = Date.now() + requestTimeoutMs;
  while (!state.hello) {
    if (Date.now() > helloDeadline) {
      throw new Error('Timed out waiting for OBS Hello');
    }
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
    if (Date.now() > readyDeadline) {
      throw new Error('Timed out waiting for OBS Identify');
    }
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
      ws.send(JSON.stringify({
        op: 6,
        d: { requestType, requestId, requestData },
      }));
    });
  };

  state.close = async () => {
    cleanupPending(new Error('OBS connection closed'));
    if (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING) {
      try { ws.close(); } catch (_) {}
    }
  };

  return state;
}

async function main() {
  const obs = await connectAndIdentify();

  try {
    if (action === 'stream-status' || action === 'stream-start' || action === 'stream-stop') {
      const status = await obs.request('GetStreamStatus', {});
      const active = !!(status && status.outputActive);
      if (action === 'stream-status') {
        console.log(`streaming=${active ? 'on' : 'off'}`);
      } else if (action === 'stream-start') {
        // 既に配信中なら何もしない(コメント連投での誤発火・悪用を防ぐ)
        if (active) {
          console.log('stream-start:already-live');
        } else {
          await obs.request('StartStream', {});
          console.log('stream-start:started');
        }
      } else {
        if (!active) {
          console.log('stream-stop:not-live');
        } else {
          await obs.request('StopStream', {});
          console.log('stream-stop:stopped');
        }
      }
      return;
    }

    const sceneName = targetName;
    const response = await obs.request('GetSceneItemList', { sceneName });
    const items = Array.isArray(response.sceneItems) ? response.sceneItems : [];

    if (action === 'stack') {
      await enforceOverlayStack(obs, sceneName);
      console.log(`stack:${belowTopOverlaySource}<${topOverlaySource}`);
      return;
    }

    if (action === 'transform') {
      const [sourceName, xRaw, yRaw, scaleXRaw, scaleYRaw, boundsWRaw, boundsHRaw] = rawArgs;
      const matches = items.filter(item => item.sourceName === sourceName);
      if (matches.length === 0) {
        console.error(`[obs_control] Sources not found in scene "${sceneName}": ${sourceName}`);
        return;
      }
      const applied = [];
      const preserved = [];
      for (const item of matches) {
        const current = await obs.request('GetSceneItemTransform', {
          sceneName,
          sceneItemId: item.sceneItemId,
        });
        const currentTransform = current.sceneItemTransform || {};
        if (transformMode !== 'force' && !isDefaultTransform(currentTransform)) {
          preserved.push(item.sceneItemId);
          continue;
        }
        const sceneItemTransform = {
          positionX: Number(xRaw),
          positionY: Number(yRaw),
          rotation: 0,
          scaleX: Number(scaleXRaw),
          scaleY: Number(scaleYRaw),
          cropLeft: 0,
          cropTop: 0,
          cropRight: 0,
          cropBottom: 0,
          alignment: 5,
          boundsType: 'OBS_BOUNDS_NONE',
        };
        if (boundsWRaw !== undefined && boundsHRaw !== undefined) {
          sceneItemTransform.boundsType = boundsType;
          sceneItemTransform.boundsWidth = Number(boundsWRaw);
          sceneItemTransform.boundsHeight = Number(boundsHRaw);
          sceneItemTransform.boundsAlignment = 5;
        }
        await obs.request('SetSceneItemTransform', {
          sceneName,
          sceneItemId: item.sceneItemId,
          sceneItemTransform,
        });
        applied.push(item.sceneItemId);
      }
      if (applied.length > 0) {
        console.log(`transform:${sourceName}:x=${xRaw}:y=${yRaw}:sx=${scaleXRaw}:sy=${scaleYRaw}:applied=${applied.length}`);
      }
      if (preserved.length > 0) {
        console.log(`transform-preserved:${sourceName}:items=${preserved.length}:mode=${transformMode}`);
      }
      await enforceOverlayStack(obs, sceneName);
      return;
    }

    const operations = parseOperations();
    const applied = [];
    const missing = [];
    if (action === 'status') {
      const lines = [];
      for (const op of operations) {
        const matches = items.filter(item => item.sourceName === op.sourceName);
        if (matches.length === 0) {
          missing.push(op.sourceName);
          lines.push(`${op.sourceName}=missing`);
          continue;
        }
        const enabled = matches.some(item => item.sceneItemEnabled === true);
        lines.push(`${op.sourceName}=${enabled ? 'on' : 'off'}`);
      }
      console.log(lines.join('\n') || '(no-op)');
      if (missing.length > 0) {
        console.error(`[obs_control] Sources not found in scene "${sceneName}": ${missing.join(', ')}`);
      }
      return;
    }
    for (const op of operations) {
      const matches = items.filter(item => item.sourceName === op.sourceName);
      if (matches.length === 0) {
        missing.push(op.sourceName);
        continue;
      }

      for (const item of matches) {
        await obs.request('SetSceneItemEnabled', {
          sceneName,
          sceneItemId: item.sceneItemId,
          sceneItemEnabled: op.enabled,
        });
      }
      applied.push(op);
    }

    const summary = applied
      .map(op => `${op.enabled ? 'show' : 'hide'}:${op.sourceName}`)
      .join(' ');
    console.log(summary || '(no-op)');
    if (missing.length > 0) {
      console.error(`[obs_control] Sources not found in scene "${sceneName}": ${missing.join(', ')}`);
    }
    await enforceOverlayStack(obs, sceneName);
  } finally {
    await obs.close();
  }
}

main().catch((err) => {
  fail(err && err.message ? err.message : String(err));
});
NODE

notify_system_msg_overlay() {
	[ -x ./overlay_notify.sh ] || return 0
	local op="" src="" title="" body=""
	case "$ACTION" in
	show|hide)
		op="$ACTION"
		shift 2
		for src in "$@"; do
			[ "$src" = "systemMsg" ] || continue
			title="systemMsg ${op}"
			body="OBS source ${op}: ${TARGET}/systemMsg"
			OVERLAY_NOTIFY_OBS_SHOW=0 ./overlay_notify.sh system "$title" "$body" "info" >/dev/null 2>&1 || true
		done
		;;
	batch)
		shift 2
		for token in "$@"; do
			case "$token" in
			show:*systemMsg*) op="show" ;;
			hide:*systemMsg*) op="hide" ;;
			*) continue ;;
			esac
			title="systemMsg ${op}"
			body="OBS source ${op}: ${TARGET}/systemMsg"
			OVERLAY_NOTIFY_OBS_SHOW=0 ./overlay_notify.sh system "$title" "$body" "info" >/dev/null 2>&1 || true
		done
		;;
	esac
}

notify_system_msg_overlay "$@"
