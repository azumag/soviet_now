import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';

import { buildDirectBroadcastOverlayState } from '../lib/direct_broadcast_overlay.mjs';


function write(file, text) {
  fs.writeFileSync(file, text, 'utf8');
  return file;
}


test('A/B overlay shows the live arm and compares both arms from the experiment ledger', () => {
  const temp = fs.mkdtempSync(path.join(os.tmpdir(), 'soren-ab-overlay-'));
  try {
    const A = 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa';
    const B = 'bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb';
    const stats = path.join(temp, 'stats.html');
    const ops = path.join(temp, 'ops.html');
    const event = path.join(temp, 'event.html');
    const abState = path.join(temp, 'ab_state.json');
    const abGames = path.join(temp, 'ab_games.jsonl');
    const rolling = path.join(temp, 'rolling_scores.json');

    write(stats, `<pre>┌──────────────────────────────────────────────┐
│ SOREN/FFMPEG #500 games Best:9000 Avg:2000
│ Recent30:2100 Trend:+5%
│ Strategy: bbbbbbbb [B] v99 1000L
│ A/B: A aaaaaaaa vs B bbbbbbbb n=220(A110/B110) d=+1
│      k13/19 adopt-look in 24g max 96g eval
│ Live: MOVE score=1200 pieces=20
└──────────────────────────────────────────────┘

Strategy Comparison (eval, mature n>=12, rollback=*)
 rk hash      n/t  │bar                    comp p50  p25
  1 deadbeef 20/20 │████                  900  900  900

Score Distribution (n=100)</pre>`);
    write(ops, '<pre>● Backend FFMPEG LIVE</pre>');
    write(event, 'const EVENTS = [];\nconst WORK = {};\nconst GEN = [];\nconst VISIBLE_SEC = 18;\n');
    write(abState, JSON.stringify({ a_hash: A, b_hash: B, pattern: 'ABBA', primary: 'eval' }));

    const rows = [];
    for (let i = 0; i < 220; i += 1) {
      const arm = i % 2 === 0 ? 'A' : 'B';
      // B is intentionally only one point better: both should rank together,
      // and the display must use the last 100 samples per arm rather than a
      // one-game current-run window.
      rows.push(JSON.stringify({ idx: i, arm, eval: arm === 'A' ? 1000 : 1001, score: 100, tainted: false }));
    }
    write(abGames, rows.join('\n') + '\n');
    write(rolling, '{}');

    const state = buildDirectBroadcastOverlayState({
      sources: {
        statsHtmlFile: stats,
        opsHtmlFile: ops,
        eventHtmlFile: event,
        abStateFile: abState,
        abGamesFile: abGames,
        rollingScoresFile: rolling,
      },
    }, 1780000000000);

    const text = state.feeds.showStatusG.text;
    assert.match(text, /A\/B: A rk2 aaaaaaaa n=100\/110 c1000 m1000 q1000/);
    assert.match(text, /A\/B: B rk1 bbbbbbbb n=100\/110 c1001 m1001 q1001/);
    assert.doesNotMatch(text, /A\/B: [AB].*n=1\//, 'arm switching must not reset the viewer comparison to one game');

    assert.equal(state.topOverride?.enabled, true);
    assert.ok(state.topOverride.lines.some((line) => /A\/B 残り: あと24〜96試合 現在B k13\/19/.test(line)));
  } finally {
    fs.rmSync(temp, { recursive: true, force: true });
  }
});


test('non-A/B overlays are unchanged and do not synthesize a top override', () => {
  const temp = fs.mkdtempSync(path.join(os.tmpdir(), 'soren-no-ab-overlay-'));
  try {
    const stats = write(path.join(temp, 'stats.html'), '<pre>SOREN/FFMPEG #1 games\nRecent30:100\nStrategy: deadbeef\nLive: MOVE</pre>');
    const ops = write(path.join(temp, 'ops.html'), '<pre>● Backend FFMPEG LIVE</pre>');
    const event = write(path.join(temp, 'event.html'), 'const EVENTS = [];\nconst WORK = {};\nconst GEN = [];\nconst VISIBLE_SEC = 18;\n');
    const state = buildDirectBroadcastOverlayState({ sources: { statsHtmlFile: stats, opsHtmlFile: ops, eventHtmlFile: event } });
    assert.equal(state.feeds.showStatusG.text, 'SOREN/FFMPEG #1 games\nRecent30:100\nStrategy: deadbeef\nLive: MOVE');
    assert.equal(state.topOverride, null);
  } finally {
    fs.rmSync(temp, { recursive: true, force: true });
  }
});
