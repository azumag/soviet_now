// Reader side of the local-BGM mute ownership record (see lib/mute_flag.py).
//
// soviet_local.mjs (the 中華AI bridge driving the local Unity game) must skip
// every page interaction while tmp/mute_local_bgm exists, but a flag left behind
// by a *dead* soren91 session must be released automatically.  The old reader was
// `fs.existsSync(MUTE_FLAG_FILE)`, which could never tell the two apart: the
// 2026-09-17 incident left the flag behind and the main broadcast stopped for
// good.
//
// The decision below is a pure function of
//   - the ownership record status (`mute_flag.py status`),
//   - the identity of the browser this bridge is attached to, and
//   - how many CDP pages other than the local game page are visible,
// so it can be unit tested without a browser.  It is fail-closed: anything that
// is not provably stale keeps the mute.  Only an explicit `reap` (compare-and-
// swap on token+revision+browser_id, inside mute_flag.py) releases the flag.

/**
 * Browser-resource identity used by the ownership record: the pathname of the
 * CDP `/json/version` `webSocketDebuggerUrl`, which is unique per browser
 * instance.  A remote/standalone browser yields an id the local reader can never
 * match, so the record simply stays (fail-closed) in that setup.
 */
export function browserIdFromWebSocketUrl(webSocketUrl) {
  try {
    return new URL(String(webSocketUrl)).pathname || '';
  } catch {
    return '';
  }
}

/**
 * Decide what the reader must do with the mute flag this tick.
 *
 * @param {object} options
 * @param {object|null} options.status        parsed `mute_flag.py status` output
 * @param {string} options.ownBrowserId       identity of our own CDP browser
 * @param {number} options.foreignPages       CDP pages other than the local game
 * @returns {{muted: boolean, reap: null|{token: string, revision: number, browserId: string}, reason: string}}
 *   `muted` is the gate for the current tick.  `reap` is set only when the flag
 *   is provably stale; the caller must still run `mute_flag.py reap` (the atomic
 *   compare-and-swap) and stays muted until it reports success.
 */
export function decideMuteAction({ status, ownBrowserId, foreignPages }) {
  if (!status || status.ok !== true) {
    return { muted: true, reap: null, reason: 'status-error' };
  }
  if (status.state === 'absent') {
    return { muted: false, reap: null, reason: 'absent' };
  }
  if (status.state !== 'owned') {
    // legacy (the old empty `touch` flag) / corrupt / unreadable: keep, and
    // never guess that it is stale.
    return { muted: true, reap: null, reason: String(status.state) };
  }
  if (!status.armed) {
    return { muted: true, reap: null, reason: 'unarmed' };
  }
  if (!Array.isArray(status.owners) || status.owners.length === 0) {
    return { muted: true, reap: null, reason: 'no-owners' };
  }
  const live = status.owners.find((owner) => owner.state !== 'dead');
  if (live) {
    return { muted: true, reap: null, reason: `owner-${live.state}` };
  }
  if (!status.browser_id || status.browser_id !== ownBrowserId) {
    return { muted: true, reap: null, reason: 'browser-mismatch' };
  }
  if (!Number.isInteger(foreignPages) || foreignPages < 0) {
    return { muted: true, reap: null, reason: 'unknown-pages' };
  }
  if (foreignPages > 0) {
    return { muted: true, reap: null, reason: 'foreign-pages' };
  }
  return {
    muted: true,
    reap: {
      token: status.token,
      revision: status.revision,
      browserId: status.browser_id,
    },
    reason: 'reapable',
  };
}
