import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';
import { fileURLToPath } from 'node:url';

import {
  DIRECT_BROADCAST_STATE_ROUTE,
  buildDirectBroadcastOverlayState,
  extractLegacyOverlayText,
  parseLegacyEventOverlayDocument,
} from '../lib/direct_broadcast_overlay.mjs';


const TEST_DIR = path.dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = path.resolve(TEST_DIR, '..');
const BROADCAST_HTML = path.join(REPO_ROOT, 'overlays', 'direct_broadcast_overlay.html');


test('legacy status HTML is consumed as read-only plain text without presentation markup', () => {
  const html = '<meta http-equiv="refresh" content="2"><pre>'
    + '<span style="color:#22c55e">● RUNNING</span> &lt;safe&gt; &amp; ready\n次の行'
    + '</pre>';
  assert.equal(extractLegacyOverlayText(html), '● RUNNING <safe> & ready\n次の行');
  assert.equal(extractLegacyOverlayText('<main>no pre</main>'), '');
});


test('event adapter retains toast, work, generator, and visibility features', () => {
  const html = `
const EVENTS = [{"ts":1780000000,"category":"chat","title":"viewer","body":"semi;colon"}];
const WORK = {"active":true,"title":"Codex","body":"testing","ts":1779999990};
const GEN = [{"key":"radio","icon":"📻","label":"ラジオ生成中","ts":1779999995}];
const VISIBLE_SEC = 24;
`;
  assert.deepEqual(parseLegacyEventOverlayDocument(html), {
    events: [{ ts: 1780000000, category: 'chat', title: 'viewer', body: 'semi;colon' }],
    work: { active: true, title: 'Codex', body: 'testing', ts: 1779999990 },
    generators: [{ key: 'radio', icon: '📻', label: 'ラジオ生成中', ts: 1779999995 }],
    visibleSec: 24,
  });
});


test('broadcast state carries every legacy line and no source paths', () => {
  const temp = fs.mkdtempSync(path.join(os.tmpdir(), 'soren-broadcast-overlay-'));
  const stats = path.join(temp, 'stats.html');
  const ops = path.join(temp, 'ops.html');
  const event = path.join(temp, 'event.html');
  fs.writeFileSync(stats, '<pre>STAT 1\nSTAT 2\nSTAT 3</pre>');
  fs.writeFileSync(ops, '<pre>OPS 1\nOPS 2</pre>');
  fs.writeFileSync(event, `
const EVENTS = [];
const WORK = {};
const GEN = [];
const VISIBLE_SEC = 18;
`);
  const state = buildDirectBroadcastOverlayState({
    sources: { statsHtmlFile: stats, opsHtmlFile: ops, eventHtmlFile: event },
  }, 1780000000123);
  assert.equal(state.version, 1);
  assert.equal(state.updatedAt, 1780000000);
  assert.equal(state.feeds.showStatusG.text, 'STAT 1\nSTAT 2\nSTAT 3');
  assert.equal(state.feeds.showStatusG.lineCount, 3);
  assert.equal(state.feeds.showStatus.text, 'OPS 1\nOPS 2');
  assert.equal(state.feeds.showStatus.lineCount, 2);
  assert.equal(state.notifications.visibleSec, 18);
  assert.doesNotMatch(JSON.stringify(state), new RegExp(temp.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')));
  fs.rmSync(temp, { recursive: true, force: true });
});


test('broadcast overlay owns the 720p data regions and never reloads or nests legacy frames', () => {
  const html = fs.readFileSync(BROADCAST_HTML, 'utf8');
  assert.match(html, /id="broadcast-sidebar"/);
  assert.match(html, /left:\s*960px/);
  assert.match(html, /width:\s*320px/);
  assert.match(html, /id="top-rail"/);
  assert.match(html, /height:\s*90px/);
  assert.match(html, /id="bottom-rail"/);
  assert.match(html, /top:\s*630px/);
  assert.match(html, /const FEED_PAGE_MS = 12000/);
  assert.match(html, /const FEED_LINES_PER_PAGE = 58/);
  assert.match(html, /const TOASTS_PER_PAGE = 3/);
  assert.match(html, /feeds[?][.]showStatusG/);
  assert.match(html, /feeds[?][.]showStatus/);
  assert.match(html, /notifications[?][.]events/);
  assert.match(html, /notifications[?][.]work/);
  assert.match(html, /notifications[?][.]generators/);
  assert.match(html, new RegExp(DIRECT_BROADCAST_STATE_ROUTE.replaceAll('/', '\\/')));
  assert.match(html, /textContent = page[.]text/);
  assert.doesNotMatch(html, /http-equiv=["']refresh/i);
  assert.doesNotMatch(html, /location[.]reload/);
  assert.doesNotMatch(html, /<iframe\b/i);
  assert.doesNotMatch(html, /innerHTML\s*=/);
});


test('legacy generators remain independent and the bridge exposes a dedicated JSON route', () => {
  const statusGenerator = fs.readFileSync(path.join(REPO_ROOT, 'generate_status_overlay.sh'), 'utf8');
  const opsGenerator = fs.readFileSync(path.join(REPO_ROOT, 'generate_show_status_overlay.sh'), 'utf8');
  const eventGenerator = fs.readFileSync(path.join(REPO_ROOT, 'generate_event_overlay.py'), 'utf8');
  for (const source of [statusGenerator, opsGenerator, eventGenerator]) {
    assert.doesNotMatch(source, /direct_broadcast_overlay/);
  }
  const bridge = fs.readFileSync(path.join(REPO_ROOT, 'soviet_local.mjs'), 'utf8');
  assert.match(bridge, /buildDirectBroadcastOverlayState/);
  assert.match(bridge, /broadcast[?][.]stateRoute === requestPath/);
  assert.match(bridge, /application\/json; charset=utf-8/);
});
