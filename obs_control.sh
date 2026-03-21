#!/bin/bash
# obs_control.sh - OBS WebSocket v5 wrapper
# Usage:
#   ./obs_control.sh show <scene> <source> [<source>...]
#   ./obs_control.sh hide <scene> <source> [<source>...]
#   ./obs_control.sh batch <scene> show:<src1>,<src2> hide:<src3>,<src4>

set -euo pipefail
cd "$(dirname "$0")"
[ -f .env ] && set -a && . ./.env && set +a

_log() { echo "[obs_control $(date '+%H:%M:%S')] $*" >&2; }

usage() {
	cat <<'EOF' >&2
Usage:
  ./obs_control.sh show <scene> <source> [<source>...]
  ./obs_control.sh hide <scene> <source> [<source>...]
  ./obs_control.sh batch <scene> show:<src1>,<src2> hide:<src3>,<src4>
EOF
	exit 2
}

ACTION="${1:-}"
TARGET="${2:-}"

case "$ACTION" in
show|hide)
	[ -n "$TARGET" ] || usage
	[ "$#" -ge 3 ] || usage
	;;
batch)
	[ -n "$TARGET" ] || usage
	[ "$#" -ge 3 ] || usage
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

node - "$@" <<'NODE'
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

function fail(message, code = 1) {
  console.error(`[obs_control] ${message}`);
  process.exit(code);
}

if (typeof WebSocket !== 'function') {
  fail('Global WebSocket is not available in this Node.js runtime');
}

function parseOperations() {
  const ops = [];

  if (action === 'show' || action === 'hide') {
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
    const sceneName = targetName;
    const operations = parseOperations();
    const response = await obs.request('GetSceneItemList', { sceneName });
    const items = Array.isArray(response.sceneItems) ? response.sceneItems : [];

    for (const op of operations) {
      const matches = items.filter(item => item.sourceName === op.sourceName);
      if (matches.length === 0) {
        throw new Error(`Source not found in scene "${sceneName}": ${op.sourceName}`);
      }

      for (const item of matches) {
        await obs.request('SetSceneItemEnabled', {
          sceneName,
          sceneItemId: item.sceneItemId,
          sceneItemEnabled: op.enabled,
        });
      }
    }

    const summary = operations
      .map(op => `${op.enabled ? 'show' : 'hide'}:${op.sourceName}`)
      .join(' ');
    console.log(summary);
  } finally {
    await obs.close();
  }
}

main().catch((err) => {
  fail(err && err.message ? err.message : String(err));
});
NODE
