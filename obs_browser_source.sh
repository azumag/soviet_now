#!/bin/bash
# obs_browser_source.sh - ensure an OBS browser source points at a local HTML file or URL.
#
# Usage:
#   ./obs_browser_source.sh ensure <scene> <source> <html-file-or-url> [width] [height] [show|hide]

set -euo pipefail
cd "$(dirname "$0")"
[ -f .env ] && set -a && . ./.env && set +a

usage() {
	cat <<'EOF' >&2
Usage:
  ./obs_browser_source.sh ensure <scene> <source> <html-file-or-url> [width] [height] [show|hide]
EOF
	exit 2
}

cmd="${1:-}"
[ "$cmd" = "ensure" ] || usage
[ "$#" -ge 4 ] || usage

scene="$2"
source_name="$3"
target="$4"
width="${5:-1920}"
height="${6:-1080}"
visibility="${7:-show}"

case "$visibility" in
show|hide) ;;
*) usage ;;
esac

if [ -z "${OBS_WEBSOCKET_PORT:-}" ] || [ -z "${OBS_WEBSOCKET_PASSWORD:-}" ]; then
	echo "[obs_browser_source] OBS_WEBSOCKET_PORT/PASSWORD not set" >&2
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
	echo "[obs_browser_source] node not found" >&2
	exit 1
fi

case "$target" in
http://*|https://*)
	source_url="$target"
	;;
*)
	if [ ! -f "$target" ]; then
		mkdir -p "$(dirname "$target")"
		printf '<!doctype html><meta charset="utf-8"><body style="margin:0;background:transparent"></body>\n' >"$target"
	fi
	abs_file="$(cd "$(dirname "$target")" && pwd)/$(basename "$target")"
	source_url="$("$NODE_BIN" -e "console.log(require('url').pathToFileURL(process.argv[1]).href)" "$abs_file")"
	;;
esac

"$NODE_BIN" - "$scene" "$source_name" "$source_url" "$width" "$height" "$visibility" <<'NODE'
const crypto = require('crypto');

const [sceneName, sourceName, sourceUrl, widthRaw, heightRaw, visibility] = process.argv.slice(2);
const host = process.env.OBS_WEBSOCKET_HOST || '127.0.0.1';
const port = Number(process.env.OBS_WEBSOCKET_PORT || 4455);
const password = process.env.OBS_WEBSOCKET_PASSWORD || '';
const requestTimeoutMs = Number(process.env.OBS_WEBSOCKET_TIMEOUT_MS || 8000);
const url = `ws://${host}:${port}`;
const width = Number(widthRaw || 1920);
const height = Number(heightRaw || 1080);
const enabled = visibility === 'show';

function fail(message, code = 1) {
  console.error(`[obs_browser_source] ${message}`);
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

async function main() {
  const obs = await connectAndIdentify();
  try {
    const settings = {
      url: sourceUrl,
      width,
      height,
      css: 'body { background-color: rgba(0, 0, 0, 0); overflow: hidden; }',
    };

    let response = await obs.request('GetSceneItemList', { sceneName });
    let items = Array.isArray(response.sceneItems) ? response.sceneItems : [];
    let matches = items.filter(item => item.sourceName === sourceName);
    let created = false;
    const preserveTransform = process.env.OBS_BROWSER_SOURCE_PRESERVE_TRANSFORM !== '0';
    const preserved = new Map();

    if (preserveTransform && matches.length > 0) {
      for (const item of matches) {
        try {
          const data = await obs.request('GetSceneItemTransform', {
            sceneName,
            sceneItemId: item.sceneItemId,
          });
          if (data && data.sceneItemTransform) {
            preserved.set(item.sceneItemId, data.sceneItemTransform);
          }
        } catch (_) {}
      }
    }

    if (matches.length === 0) {
      await obs.request('CreateInput', {
        sceneName,
        inputName: sourceName,
        inputKind: 'browser_source',
        inputSettings: settings,
        sceneItemEnabled: enabled,
      });
      created = true;
      response = await obs.request('GetSceneItemList', { sceneName });
      items = Array.isArray(response.sceneItems) ? response.sceneItems : [];
      matches = items.filter(item => item.sourceName === sourceName);
    } else {
      await obs.request('SetInputSettings', {
        inputName: sourceName,
        inputSettings: settings,
        overlay: true,
      });
    }

    for (const item of matches) {
      await obs.request('SetSceneItemEnabled', {
        sceneName,
        sceneItemId: item.sceneItemId,
        sceneItemEnabled: enabled,
      });
      const before = preserved.get(item.sceneItemId);
      if (before && !created) {
        let after = {};
        try {
          const data = await obs.request('GetSceneItemTransform', {
            sceneName,
            sceneItemId: item.sceneItemId,
          });
          after = data.sceneItemTransform || {};
        } catch (_) {}
        const nextSourceWidth = Number(after.sourceWidth || before.sourceWidth || 0);
        const nextSourceHeight = Number(after.sourceHeight || before.sourceHeight || 0);
        const wantedWidth = Number(before.width || 0);
        const wantedHeight = Number(before.height || 0);
        const restore = {
          positionX: Number(before.positionX || 0),
          positionY: Number(before.positionY || 0),
          rotation: Number(before.rotation || 0),
          scaleX: nextSourceWidth > 0 && wantedWidth > 0 ? wantedWidth / nextSourceWidth : Number(before.scaleX || 1),
          scaleY: nextSourceHeight > 0 && wantedHeight > 0 ? wantedHeight / nextSourceHeight : Number(before.scaleY || 1),
          cropLeft: Number(before.cropLeft || 0),
          cropTop: Number(before.cropTop || 0),
          cropRight: Number(before.cropRight || 0),
          cropBottom: Number(before.cropBottom || 0),
          alignment: Number(before.alignment || 5),
          boundsType: before.boundsType || 'OBS_BOUNDS_NONE',
        };
        if (Number(before.boundsWidth || 0) > 0) restore.boundsWidth = Number(before.boundsWidth);
        if (Number(before.boundsHeight || 0) > 0) restore.boundsHeight = Number(before.boundsHeight);
        if (Number(before.boundsAlignment || 0) > 0) restore.boundsAlignment = Number(before.boundsAlignment);
        await obs.request('SetSceneItemTransform', {
          sceneName,
          sceneItemId: item.sceneItemId,
          sceneItemTransform: restore,
        });
      }
    }

    console.log(`${created ? 'created' : 'updated'}:${sourceName}:${visibility}:${sourceUrl}`);
  } finally {
    await obs.close();
  }
}

main().catch(err => fail(err && err.message ? err.message : String(err)));
NODE
