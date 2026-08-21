import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';
import { fileURLToPath } from 'node:url';
import vm from 'node:vm';

import {
  DIRECT_BROADCAST_STATE_ROUTE,
  buildDirectBroadcastOverlayState,
  extractLegacyOverlayLineSegments,
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


test('legacy overlay color spans become allowlisted text segments without markup', () => {
  const html = '<pre>'
    + '<span style="color:#94a3b8"> 3801</span>│<span style="color:#facc15">\\</span>\n'
    + '<span style="color:#22d3ee">│</span><span style="font-weight:700"> SOREN/FFMPEG </span>'
    + '<span style="opacity:.68">dim note</span>\n'
    + '<span style="color:red;behavior:url(x.htc)">evil</span><script>alert(1)</script>\n'
    + 'plain &lt;tag&gt; line\n'
    + '</pre>';
  assert.deepEqual(extractLegacyOverlayLineSegments(html), [
    [
      { t: ' 3801', c: '#94a3b8' },
      { t: '│' },
      { t: '\\', c: '#facc15' },
    ],
    [
      { t: '│', c: '#22d3ee' },
      { t: ' SOREN/FFMPEG ', b: 1 },
      { t: 'dim note', o: '.68' },
    ],
    [{ t: 'evil' }, { t: 'alert(1)' }],
    [{ t: 'plain <tag> line' }],
  ]);
  assert.deepEqual(extractLegacyOverlayLineSegments('<main>no pre</main>'), []);
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
  fs.writeFileSync(ops, '<pre><span style="color:#22c55e">OPS 1</span>\nOPS 2</pre>');
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
  assert.deepEqual(state.feeds.showStatusG.segments, [[{ t: 'STAT 1' }], [{ t: 'STAT 2' }], [{ t: 'STAT 3' }]]);
  assert.equal(state.feeds.showStatusG.lineCount, 3);
  assert.equal(state.feeds.showStatus.text, 'OPS 1\nOPS 2');
  assert.equal(state.feeds.showStatus.segments, undefined, 'ops feed stays plain text to keep the state payload lean');
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
  assert.match(html, /sorenOverlayRegion/);
  assert.match(html, /data-soren-region="sidebar"/);
  assert.match(html, /data-soren-region="top"/);
  assert.match(html, /data-soren-region="bottom"/);
  assert.match(html, /data-soren-region="sidebar"[^}]+#broadcast-sidebar\s*\{\s*left:\s*0/s);
  assert.match(html, /data-soren-region="bottom"[^}]+#bottom-rail\s*\{\s*top:\s*0/s);
  assert.match(html, /id="feed-g"/);
  assert.match(html, /id="feed-s"/);
  assert.match(html, /id="feed-i"/);
  assert.match(html, /id="feed-i-status"/);
  assert.match(html, /panel-i/);
  assert.match(html, /data-improve-active="1"/);
  assert.match(html, /badge-i/);
  assert.doesNotMatch(html, /FEED_PAGE_MS/);
  assert.doesNotMatch(html, /FEED_LINES_PER_PAGE/);
  assert.match(html, /feed-line /);
  assert.match(html, /feed-line\.run/);
  assert.match(html, /feed-line\.down/);
  assert.match(html, /feed-line\.g-recent/);
  assert.match(html, /badge-g/);
  assert.match(html, /badge-s/);
  assert.match(html, /summary-line\.sum-head/);
  assert.match(html, /summary-line\.sum-live/);
  assert.match(html, /summary-line\.sum-ai/);
  assert.match(html, /const TOASTS_PER_PAGE = 3/);
  assert.match(html, /feeds[?][.]showStatusG/);
  assert.match(html, /feeds[?][.]showStatus/);
  assert.match(html, /notifications[?][.]events/);
  assert.match(html, /notifications[?][.]work/);
  assert.match(html, /notifications[?][.]generators/);
  assert.match(html, new RegExp(DIRECT_BROADCAST_STATE_ROUTE.replaceAll('/', '\\/')));
  assert.match(html, /renderFeedLines/);
  assert.match(html, /segmentLines/);
  assert.match(html, /appendFeedLineContent/);
  assert.match(html, /STATUS MERGE/);
  assert.doesNotMatch(html, /http-equiv=["']refresh/i);
  assert.doesNotMatch(html, /location[.]reload/);
  assert.doesNotMatch(html, /<iframe\b/i);
  assert.doesNotMatch(html, /innerHTML\s*=/);
});


test('broadcast state exposes an improve feed gated by wildcard activity', () => {
  const temp = fs.mkdtempSync(path.join(os.tmpdir(), 'soren-broadcast-improve-'));
  const stats = path.join(temp, 'stats.html');
  const ops = path.join(temp, 'ops.html');
  const event = path.join(temp, 'event.html');
  const improveState = path.join(temp, 'improve_state.json');
  const improveLog = path.join(temp, 'improve_ai.log');
  const wildcardState = path.join(temp, 'wildcard.json');
  fs.writeFileSync(stats, '<pre>S</pre>');
  fs.writeFileSync(ops, '<pre>O</pre>');
  fs.writeFileSync(event, 'const EVENTS = [];\nconst WORK = {};\nconst GEN = [];\nconst VISIBLE_SEC = 18;\n');
  fs.writeFileSync(improveState, JSON.stringify({
    status: 'running',
    phase: 'phase_c',
    detail: 'バッチサマリ生成中',
    progress: 40,
    pid: 123,
    started_at: 1780000000,
    updated_at: 1780000010,
  }));
  const logLines = Array.from({ length: 30 }, (_, n) => `[02:44:0${n % 10}] [IMPROVE] line ${n}`);
  logLines.push('\x1b[31m[WARN]\x1b[0m colored');
  fs.writeFileSync(improveLog, logLines.join('\n') + '\n');
  fs.writeFileSync(wildcardState, JSON.stringify({ phase: 'idle' }));
  const sources = {
    statsHtmlFile: stats,
    opsHtmlFile: ops,
    eventHtmlFile: event,
    improveStateFile: improveState,
    improveLogFile: improveLog,
    wildcardStateFile: wildcardState,
  };

  const running = buildDirectBroadcastOverlayState({ sources }, 1780000020000).feeds.improve;
  assert.equal(running.active, true);
  assert.equal(running.status, 'running');
  assert.equal(running.phase, 'phase_c');
  assert.equal(running.detail, 'バッチサマリ生成中');
  assert.equal(running.progress, 40);
  assert.equal(running.pid, 123);
  assert.equal(running.startedAt, 1780000000);
  assert.equal(running.updatedAt, 1780000010);
  assert.ok(running.logUpdatedAt > 0, 'log mtime must drive the improve age display');
  assert.equal(running.lineCount, 24);
  assert.equal(running.logLines[0], '[02:44:07] [IMPROVE] line 7');
  assert.ok(running.logLines.some((line) => line.includes('[WARN] colored') && !line.includes('\x1b')));

  fs.writeFileSync(wildcardState, JSON.stringify({ phase: 'running' }));
  const gated = buildDirectBroadcastOverlayState({ sources }, 1780000020000).feeds.improve;
  assert.equal(gated.active, false, 'wildcard evaluation must suppress the improve feed');

  fs.writeFileSync(improveState, JSON.stringify({ status: 'idle' }));
  fs.writeFileSync(wildcardState, JSON.stringify({ phase: 'idle' }));
  const idle = buildDirectBroadcastOverlayState({ sources }, 1780000020000).feeds.improve;
  assert.equal(idle.active, false);
  assert.equal(idle.status, 'idle');
  fs.rmSync(temp, { recursive: true, force: true });
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


class FakeClassList {
  constructor(host) {
    this.host = host;
    this.tokens = new Set();
  }

  toggle(token, force) {
    const on = force === undefined ? !this.tokens.has(token) : force;
    if (on) this.tokens.add(token);
    else this.tokens.delete(token);
    return on;
  }
}


class FakeElement {
  constructor(tagName) {
    this.tagName = String(tagName).toUpperCase();
    this.children = [];
    this.textContent = '';
    this.style = {};
    this.dataset = {};
    this.className = '';
    this.classList = new FakeClassList(this);
  }

  append(...nodes) {
    this.children.push(...nodes);
  }

  appendChild(node) {
    this.children.push(node);
    return node;
  }

  replaceChildren(...nodes) {
    this.children = nodes;
  }

  matches(selector) {
    if (selector === '.toast[data-live-status="1"]') {
      return this.className.split(/\s+/).includes('toast') && this.dataset.liveStatus === '1';
    }
    if (selector.startsWith('.')) {
      return this.className.split(/\s+/).includes(selector.slice(1));
    }
    return false;
  }

  querySelector(selector) {
    const stack = [...this.children];
    while (stack.length) {
      const child = stack.pop();
      if (child.matches(selector)) return child;
      stack.push(...child.children);
    }
    return null;
  }

  querySelectorAll(selector) {
    const found = [];
    const stack = [...this.children];
    while (stack.length) {
      const child = stack.pop();
      if (child.matches(selector)) found.push(child);
      stack.push(...child.children);
    }
    return found;
  }
}


async function runBroadcastOverlayScript(initialState) {
  const html = fs.readFileSync(BROADCAST_HTML, 'utf8');
  const scripts = [...html.matchAll(/<script>([\s\S]*?)<\/script>/g)].map((match) => match[1]);
  assert.ok(scripts.length >= 2, 'broadcast overlay must carry its setup and render scripts');

  const document = {
    documentElement: { dataset: {} },
    createElement: (tag) => new FakeElement(tag),
    getElementById: () => null,
  };
  const byId = {
    feed: new FakeElement('pre'),
    'feed-g': new FakeElement('div'),
    'feed-s': new FakeElement('div'),
    'feed-i': new FakeElement('div'),
    'feed-g-lines': new FakeElement('span'),
    'feed-s-lines': new FakeElement('span'),
    'feed-i-lines': new FakeElement('span'),
    'feed-i-status': new FakeElement('span'),
    'feed-label': new FakeElement('span'),
    'feed-age': new FakeElement('span'),
    'feed-progress': new FakeElement('span'),
    summary: new FakeElement('div'),
    work: new FakeElement('div'),
    'toast-grid': new FakeElement('div'),
  };
  for (const [id, element] of Object.entries(byId)) {
    element.id = id;
  }
  document.getElementById = (id) => byId[id] || null;

  let state = initialState;
  const intervalCallbacks = [];
  let fetchCount = 0;
  const calls = { toastRebuilds: 0 };
  const toastGrid = byId['toast-grid'];
  const originalReplace = toastGrid.replaceChildren.bind(toastGrid);
  toastGrid.replaceChildren = (...nodes) => {
    calls.toastRebuilds += 1;
    originalReplace(...nodes);
  };

  const RealDate = Date;
  let nowMs = 1780001000000;
  function FakeDate(...args) {
    return args.length ? new RealDate(...args) : new RealDate(nowMs);
  }
  FakeDate.now = () => nowMs;

  const context = vm.createContext({
    console,
    document,
    window: { frameElement: undefined },
    fetch: async () => {
      fetchCount += 1;
      return { ok: true, json: async () => state };
    },
    setInterval: (callback) => {
      intervalCallbacks.push(callback);
      return intervalCallbacks.length;
    },
    Date: FakeDate,
    Math,
    Number,
    String,
    Array,
    Object,
    JSON,
    RegExp,
    Promise,
  });
  for (const script of scripts) {
    vm.runInContext(script, context, { filename: 'direct_broadcast_overlay.html' });
  }
  assert.ok(intervalCallbacks.length >= 2, 'overlay script must install its refresh and render loops');
  await new Promise((resolve) => setImmediate(resolve));

  const tick = async (seconds) => {
    nowMs += seconds * 1000;
    for (const callback of [...intervalCallbacks]) {
      await callback();
    }
  };
  const toastCards = () => toastGrid.children.map((card) => ({
    className: card.className,
    liveStatus: card.dataset.liveStatus || '',
    title: card.querySelector('.toast-title')?.textContent || '',
    body: card.querySelector('.toast-body')?.textContent || '',
  }));

  return {
    calls,
    tick,
    toastCards,
    feedG: byId['feed-g'],
    feedS: byId['feed-s'],
    feedI: byId['feed-i'],
    summary: byId.summary,
    feedProgress: byId['feed-progress'],
    documentElement: document.documentElement,
    setState: (next) => { state = next; },
    fetchCount: () => fetchCount,
  };
}


test('live-status toasts stay stable and never re-animate while idle', async () => {
  const base = {
    version: 1,
    updatedAt: 1780000090,
    feeds: {
      showStatusG: {
        label: 'SHOW-STATUS-G',
        text: 'SOREN/OBS FFMPEG\nRecent30: 30.0',
        updatedAt: 1780000080,
        lineCount: 2,
      },
      showStatus: {
        label: 'SHOW-STATUS',
        text: '● Backend     FFMPEG LIVE\n◆ Game        34試合目 (games)\n▾ LastDrop   T36',
        updatedAt: 1780000080,
        lineCount: 3,
      },
    },
    notifications: { visibleSec: 18, events: [], work: { active: false }, generators: [] },
  };
  const overlay = await runBroadcastOverlayScript(base);

  const first = overlay.toastCards();
  assert.equal(first.length, 3);
  for (const card of first) {
    assert.equal(card.title, 'LIVE STATUS');
    assert.equal(card.liveStatus, '1');
    assert.doesNotMatch(card.className, /fresh/);
  }
  assert.equal(overlay.calls.toastRebuilds, 1);

  await overlay.tick(1);
  await overlay.tick(1);
  await overlay.tick(2);
  assert.equal(overlay.calls.toastRebuilds, 1, 'idle live-status cards must not rebuild every second');
});


test('only genuine events within three seconds get the fresh enter animation', async () => {
  const base = {
    version: 1,
    updatedAt: 1780000090,
    feeds: {
      showStatusG: { label: 'SHOW-STATUS-G', text: 'SOREN/OBS FFMPEG\nRecent30: 30.0', updatedAt: 1780000080, lineCount: 2 },
      showStatus: { label: 'SHOW-STATUS', text: '● Backend     FFMPEG LIVE', updatedAt: 1780000080, lineCount: 1 },
    },
    notifications: { visibleSec: 18, events: [], work: { active: false }, generators: [] },
  };
  const overlay = await runBroadcastOverlayScript(base);
  assert.equal(overlay.calls.toastRebuilds, 1);

  const now = Math.floor(1780001000000 / 1000) + 3;
  overlay.setState({
    ...base,
    notifications: {
      visibleSec: 18,
      events: [{ ts: now, category: 'chat', title: 'viewer', body: 'hello' }],
      work: { active: false },
      generators: [],
    },
  });
  await overlay.tick(3);
  const cards = overlay.toastCards();
  const chat = cards.find((card) => card.title === 'viewer');
  assert.ok(chat);
  assert.match(chat.className, /fresh/);
  assert.equal(overlay.calls.toastRebuilds, 2);

  overlay.setState({
    ...base,
    notifications: {
      visibleSec: 18,
      events: [{ ts: now - 10, category: 'chat', title: 'old viewer', body: 'stale' }],
      work: { active: false },
      generators: [],
    },
  });
  await overlay.tick(2);
  const stale = overlay.toastCards().find((card) => card.title === 'old viewer');
  assert.ok(stale);
  assert.doesNotMatch(stale.className, /fresh/);
});


test('merged sidebar renders both status feeds at once with colored lines and never switches', async () => {
  const base = {
    version: 1,
    updatedAt: 1780000090,
    feeds: {
      showStatusG: {
        label: 'SHOW-STATUS-G',
        text: 'SOREN/OBS FFMPEG #10 games\nRecent30: 1097\nStrategy: 32b5edcf\nLive: MOVE score=875',
        updatedAt: 1780000080,
        lineCount: 4,
      },
      showStatus: {
        label: 'SHOW-STATUS',
        text: '● Loop        RUNNING\n○ ImproveD    STOPPED\n◆ Game        49試合目 (games)',
        updatedAt: 1780000080,
        lineCount: 3,
      },
    },
    notifications: { visibleSec: 18, events: [], work: { active: false }, generators: [] },
  };
  const overlay = await runBroadcastOverlayScript(base);

  assert.equal(overlay.feedG.children.length, 4, 'game stats feed must be fully visible');
  assert.equal(overlay.feedS.children.length, 3, 'ops feed must be fully visible');
  const gClasses = overlay.feedG.children.map((line) => line.className);
  assert.ok(gClasses.some((cls) => cls.includes('g-head')), 'SOREN/OBS header line must be colored');
  assert.ok(gClasses.some((cls) => cls.includes('g-recent')), 'Recent30 line must be colored');
  const sClasses = overlay.feedS.children.map((line) => line.className);
  assert.ok(sClasses.some((cls) => cls.includes('run')), 'RUNNING line must be green');
  assert.ok(sClasses.some((cls) => cls.includes('down')), 'STOPPED line must be red');
  assert.ok(sClasses.some((cls) => cls.includes('yellow')), 'Game count line must be yellow');

  const summaryClasses = overlay.summary.children.map((line) => line.className);
  assert.ok(summaryClasses.some((cls) => cls.includes('sum-head')), 'summary SOREN/OBS line must be colored');
  assert.ok(summaryClasses.some((cls) => cls.includes('sum-live')), 'summary Live line must be colored');

  await overlay.tick(12);
  await overlay.tick(12);
  assert.equal(overlay.feedG.children.length, 4, 'no pagination may hide game lines');
  assert.equal(overlay.feedS.children.length, 3, 'no pagination may hide ops lines');

  overlay.setState({
    ...base,
    feeds: {
      showStatusG: { ...base.feeds.showStatusG, text: base.feeds.showStatusG.text + '\nLastDrop: T46' },
      showStatus: base.feeds.showStatus,
    },
  });
  await overlay.tick(1);
  assert.equal(overlay.feedG.children.length, 5, 'feed update must re-render the merged panel');
  assert.equal(overlay.feedS.children.length, 3);
});


test('game feed renders allowlisted color segments as styled spans without innerHTML', async () => {
  const base = {
    version: 1,
    updatedAt: 1780000090,
    feeds: {
      showStatusG: {
        label: 'SHOW-STATUS-G',
        text: 'Score Timeline\n     │  \\',
        segments: [
          [{ t: 'Score ', b: 1 }, { t: 'Timeline', c: '#67e8f9' }],
          [{ t: '     │  ' }, { t: '\\', c: '#facc15' }],
        ],
        updatedAt: 1780000080,
        lineCount: 2,
      },
      showStatus: { label: 'SHOW-STATUS', text: '● Backend FFMPEG LIVE', updatedAt: 1780000080, lineCount: 1 },
    },
    notifications: { visibleSec: 18, events: [], work: { active: false }, generators: [] },
  };
  const overlay = await runBroadcastOverlayScript(base);
  const rows = overlay.feedG.children;
  assert.equal(rows.length, 2);
  assert.equal(rows[0].children.length, 2, 'segmented line must build one span per segment');
  assert.equal(rows[0].children[0].textContent, 'Score ');
  assert.equal(rows[0].children[0].style.fontWeight, '700');
  assert.equal(rows[0].children[1].textContent, 'Timeline');
  assert.equal(rows[0].children[1].style.color, '#67e8f9');
  assert.equal(rows[1].children.length, 2);
  assert.equal(rows[1].children[1].textContent, '\\');
  assert.equal(rows[1].children[1].style.color, '#facc15');

  const opsRow = overlay.feedS.children[0];
  assert.equal(opsRow.children.length, 0, 'feeds without segments keep the plain text path');
  assert.match(opsRow.textContent, /Backend/);

  overlay.setState({
    ...base,
    feeds: {
      ...base.feeds,
      showStatusG: { ...base.feeds.showStatusG, segments: undefined, updatedAt: 1780000085 },
    },
  });
  await overlay.tick(1);
  const fallbackRows = overlay.feedG.children;
  assert.equal(fallbackRows.length, 2);
  assert.equal(fallbackRows[0].children.length, 0, 'missing segments must fall back to textContent');
  assert.match(fallbackRows[0].textContent, /Score Timeline/);
});


test('rate-limit status is promoted to the top rail with main and fallback models', async () => {
  const base = {
    version: 1,
    updatedAt: 1780000090,
    feeds: {
      showStatusG: {
        label: 'SHOW-STATUS-G',
        text: '┌──────────────────────────────┐\n'
          + '│ SOREN/FFMPEG #10 games        │\n'
          + '│ Recent30: 1097                │\n'
          + '│ Strategy: 32b5edcf            │\n'
          + '│ Live: MOVE score=875          │\n'
          + '│ AI 429 main=deepseek-v4-flash(3h59m) │\n'
          + '│        fb=minimax-m3(4h59m)          │',
        updatedAt: 1780000080,
        lineCount: 7,
      },
      showStatus: {
        label: 'SHOW-STATUS',
        text: '● Backend     FFMPEG LIVE',
        updatedAt: 1780000080,
        lineCount: 1,
      },
    },
    notifications: { visibleSec: 18, events: [], work: { active: false }, generators: [] },
  };
  const overlay = await runBroadcastOverlayScript(base);
  const aiLine = overlay.summary.children.find((line) => line.className.includes('sum-ai'));
  assert.ok(aiLine, 'rate-limit line must be visible in the top rail');
  assert.match(aiLine.textContent, /AI使用量上限/);
  assert.match(aiLine.textContent, /AI使用量上限 復旧まで:/);
  assert.match(aiLine.textContent, /主 DeepSeek Flash 3h59m/);
  assert.match(aiLine.textContent, /予備 MiniMax 4h59m/);
  assert.doesNotMatch(aiLine.textContent, /429/);
});


test('improve panel appears with colored log lines only while improve is running', async () => {
  const base = {
    version: 1,
    updatedAt: 1780000090,
    feeds: {
      showStatusG: {
        label: 'SHOW-STATUS-G',
        text: 'SOREN/OBS FFMPEG\nRecent30: failed line stays styled',
        updatedAt: 1780000080,
        lineCount: 2,
      },
      showStatus: {
        label: 'SHOW-STATUS',
        text: '● Loop        RUNNING',
        updatedAt: 1780000080,
        lineCount: 1,
      },
      improve: {
        active: false,
        status: 'idle',
        phase: '',
        detail: '',
        logLines: [],
        lineCount: 0,
        updatedAt: 0,
      },
    },
    notifications: { visibleSec: 18, events: [], work: { active: false }, generators: [] },
  };
  const overlay = await runBroadcastOverlayScript(base);

  assert.equal(overlay.documentElement.dataset.improveActive, '');
  assert.equal(overlay.feedI.children.length, 0, 'idle improve must not render the panel');
  assert.equal(overlay.feedG.children.length, 2);
  assert.equal(overlay.feedS.children.length, 1);
  assert.match(overlay.feedProgress.textContent, /^3L$/, 'idle footer must count only GAME + OPS');

  overlay.setState({
    ...base,
    feeds: {
      ...base.feeds,
        improve: {
          active: true,
          status: 'running',
          phase: 'phase_c',
          detail: 'バッチサマリ生成中',
          logLines: ['[02:44:00] [IMPROVE] start', '[02:44:01] [BRANCH] pin', '✗ FAILED something'],
          lineCount: 3,
          updatedAt: 1780000080,
          logUpdatedAt: 1780000090,
        },
      },
  });
  await overlay.tick(1);

  assert.equal(overlay.documentElement.dataset.improveActive, '1');
  assert.equal(overlay.feedI.children.length, 3, 'running improve must render its log tail');
  assert.match(overlay.feedProgress.textContent, /^6L$/, 'running footer must include improve lines');
  const iClasses = overlay.feedI.children.map((line) => line.className);
  assert.ok(iClasses.some((cls) => cls.includes('teal')), '[IMPROVE] log line must be teal');
  assert.ok(iClasses.some((cls) => cls.includes('purple')), '[BRANCH] log line must be purple');
  assert.ok(iClasses.some((cls) => cls.includes('down')), 'FAILED log line must be red');
  const gClasses = overlay.feedG.children.map((line) => line.className);
  assert.ok(gClasses.some((cls) => cls.includes('g-head')), 'GAME header line keeps its own color');
  assert.ok(gClasses.some((cls) => cls.includes('g-recent')),
    'GAME line containing the word failed must not turn red while improve is active');

  overlay.setState({
    ...base,
    feeds: {
      ...base.feeds,
      improve: { active: false, status: 'idle', phase: '', detail: '', logLines: [], lineCount: 0, updatedAt: 0 },
    },
  });
  await overlay.tick(1);
  assert.equal(overlay.documentElement.dataset.improveActive, '');
  assert.equal(overlay.feedI.children.length, 0, 'idle improve must hide the panel again');
  assert.match(overlay.feedProgress.textContent, /^3L$/, 'footer must drop improve lines when idle');
});
