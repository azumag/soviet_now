import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { spawnSync } from 'node:child_process';
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


test('broadcast state publishes only bounded finite direct-stream system metrics', () => {
  const temp = fs.mkdtempSync(path.join(os.tmpdir(), 'soren-broadcast-system-metrics-'));
  const status = path.join(temp, 'status.json');
  fs.writeFileSync(status, JSON.stringify({ system_metrics: {
    sampled_at: 1780000000,
    cpu_percent: 44.4,
    memory_percent: 65,
    memory_used_bytes: 6500,
    memory_total_bytes: 10000,
    history: Array.from({ length: 50 }, (_, index) => ({
      ts: 1780000000 - index,
      cpu_percent: index === 49 ? Number.NaN : index,
      memory_percent: index === 48 ? 120 : 60,
    })),
    private_path: temp,
  } }));
  const state = buildDirectBroadcastOverlayState({
    sources: { directStreamStatusFile: status },
  }, 1780000000123);
  assert.deepEqual(state.feeds.systemMetrics, {
    sampledAt: 1780000000,
    cpuPercent: 44.4,
    memoryPercent: 65,
    memoryUsedBytes: 6500,
    memoryTotalBytes: 10000,
    history: Array.from({ length: 36 }, (_, index) => ({
      ts: 1780000000 - (index + 14),
      cpuPercent: index === 35 ? null : index + 14,
      memoryPercent: index === 34 ? null : 60,
    })),
  });
  assert.doesNotMatch(JSON.stringify(state.feeds.systemMetrics), new RegExp(temp.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')));
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
  assert.match(html, /GAME \+ OPS/);
  assert.match(html, /GAME STATS/);
  assert.match(html, /OPS HEALTH/);
  assert.match(html, /ops-system-metrics/);
  assert.match(html, /updateOpsSystemMetrics\(feedS, state\?\.feeds\?\.systemMetrics\)/);
  assert.match(html, /status\.textContent = valid \? '3M'/);
  assert.match(html, /STALE/);
  assert.match(html, /data-broadcast-overlay-version="4"/);
  assert.match(html, /id="feed-g-state"/);
  assert.match(html, /id="feed-s-state"/);
  assert.match(html, /Broadcast status hierarchy v4/);
  assert.match(html, /feed-line[.]ops-primary/);
  assert.match(html, /feed-line[.]ops-alert/);
  assert.match(html, /feed-line[.]game-primary/);
  assert.match(html, /HEALTH/);
  assert.match(html, /ACTIVITY/);
  assert.doesNotMatch(html, /G \+ STATUS/);
  assert.match(html, /panel-i/);
  assert.match(html, /data-improve-active="1"/);
  assert.match(html, /badge-i/);
  assert.doesNotMatch(html, /FEED_PAGE_MS/);
  assert.doesNotMatch(html, /FEED_LINES_PER_PAGE/);
  assert.match(html, /feed-line /);
  assert.match(html, /feed-line\.run/);
  assert.match(html, /feed-line\.down/);
  assert.match(html, /feed-line\.g-recent/);
  assert.match(html, /feed-line\.g-ab/);
  assert.match(html, /badge-g/);
  assert.match(html, /badge-s/);
  assert.match(html, /summary-line\.sum-head/);
  assert.match(html, /summary-line\.sum-ab/);
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

test('inline alternate-game rails can use the neutral non-blue theme', () => {
  const html = fs.readFileSync(BROADCAST_HTML, 'utf8');
  assert.match(html, /dataset[.]sorenNeutral/);
  assert.match(html, /data-soren-neutral="1"/);
  assert.match(html, /background: rgb\(5, 5, 5\)/);
  assert.match(html, /filter: grayscale\(1\)/);
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
    // Documentation may name the consumer; generators must not import or execute it.
    assert.doesNotMatch(source, /\b(?:import|from|source|exec|python3?|node|bash|sh)\s+[^\n]*direct_broadcast_overlay/);
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

  add(token) { this.tokens.add(token); }
  remove(token) { this.tokens.delete(token); }

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
    'prediction-round': new FakeElement('div'),
    'gen-top': new FakeElement('div'),
    'feed-g': new FakeElement('div'),
    'hanjuku-gap': new FakeElement('div'),
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
    prediction: byId['prediction-round'],
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


test('ai thinking banner takes the first bottom slot while generators are active', async () => {
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
    notifications: {
      visibleSec: 18,
      events: [],
      work: { active: false },
      generators: [{ key: 'radio', icon: '📻', label: 'ラジオ生成中 (jiji)', ts: 1779999970 }],
    },
  };
  const overlay = await runBroadcastOverlayScript(base);

  const cards = overlay.toastCards();
  assert.equal(cards.length, 3);
  assert.equal(cards[0].title, 'AI思考中');
  assert.match(cards[0].className, /ai-thinking/);
  assert.equal(cards[0].body, 'ラジオ生成中 (jiji)');
  assert.equal(cards[0].liveStatus, '1');
  assert.doesNotMatch(cards[0].className, /fresh/);
  assert.equal(cards[1].title, 'LIVE STATUS');
  assert.equal(cards[2].title, 'LIVE STATUS');

  await overlay.tick(2);
  assert.equal(overlay.calls.toastRebuilds, 1, 'thinking banner must not rebuild every second');

  // 思考が終われば従来の LIVE STATUS 3枚へ戻る。
  overlay.setState({
    ...base,
    notifications: { visibleSec: 18, events: [], work: { active: false }, generators: [] },
  });
  await overlay.tick(1);
  const idle = overlay.toastCards();
  assert.equal(idle.length, 3);
  for (const card of idle) {
    assert.equal(card.title, 'LIVE STATUS');
    assert.doesNotMatch(card.className, /ai-thinking/);
  }
});


test('generator toasts keep their paging role while transient events are visible', async () => {
  const base = {
    version: 1,
    updatedAt: 1780000090,
    feeds: {
      showStatusG: { label: 'SHOW-STATUS-G', text: 'SOREN/OBS FFMPEG\nRecent30: 30.0', updatedAt: 1780000080, lineCount: 2 },
      showStatus: { label: 'SHOW-STATUS', text: '● Backend     FFMPEG LIVE', updatedAt: 1780000080, lineCount: 1 },
    },
    notifications: {
      visibleSec: 18,
      events: [{ ts: Math.floor(1780001000000 / 1000) - 5, category: 'chat', title: 'viewer', body: 'hello' }],
      work: { active: false },
      generators: [{ key: 'radio', icon: '📻', label: 'ラジオ生成中', ts: Math.floor(1780001000000 / 1000) - 10 }],
    },
  };
  const overlay = await runBroadcastOverlayScript(base);

  const cards = overlay.toastCards();
  assert.ok(cards.some((card) => card.title === '📻 ラジオ生成中'), 'generator toast must remain during events');
  assert.ok(!cards.some((card) => card.title === 'AI思考中'), 'thinking banner is only for the idle bottom row');
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


test('top summary shows A/B progress while an experiment is running', async () => {
  const base = {
    version: 1,
    updatedAt: 1780000090,
    feeds: {
      showStatusG: {
        label: 'SHOW-STATUS-G',
        text: 'SOREN/OBS FFMPEG #10 games\nRecent30: 1097\nStrategy: 32b5edcf\n A/B: A 3a9bd96b vs B 015aa639 n=53(A27/B26) d=+120\nLive: MOVE score=875',
        updatedAt: 1780000080,
        lineCount: 5,
      },
      showStatus: {
        label: 'SHOW-STATUS',
        text: '● Loop        RUNNING',
        updatedAt: 1780000080,
        lineCount: 1,
      },
    },
    notifications: { visibleSec: 18, events: [], work: { active: false }, generators: [] },
  };
  const overlay = await runBroadcastOverlayScript(base);

  const summaryTexts = overlay.summary.children.map((line) => line.textContent);
  assert.equal(summaryTexts.length, 4, 'top rail keeps four slots');
  assert.ok(summaryTexts.some((text) => text.includes('A/B:')), 'A/B progress must be in the top rail');
  const summaryClasses = overlay.summary.children.map((line) => line.className);
  assert.ok(summaryClasses.some((cls) => cls.includes('sum-ab')), 'A/B summary line must be colored');
  assert.ok(!summaryClasses.some((cls) => cls.includes('sum-live')), 'Live yields its slot to A/B while running');

  const gClasses = overlay.feedG.children.map((line) => line.className);
  assert.ok(gClasses.some((cls) => cls.includes('g-ab')), 'sidebar A/B line must be colored');

  overlay.setState({
    ...base,
    feeds: {
      ...base.feeds,
      showStatusG: {
        ...base.feeds.showStatusG,
        text: 'SOREN/OBS FFMPEG #10 games\nRecent30: 1097\nStrategy: 32b5edcf\nLive: MOVE score=875',
        lineCount: 4,
      },
    },
  });
  await overlay.tick(1);
  const idleClasses = overlay.summary.children.map((line) => line.className);
  assert.ok(idleClasses.some((cls) => cls.includes('sum-live')), 'Live returns when no A/B is running');
  assert.ok(!idleClasses.some((cls) => cls.includes('sum-ab')), 'no stale A/B styling without an experiment');
});


test('top summary shows how many games remain while an A/B is running', async () => {
  const boxed = (lines) => ['┌───────────────────┐', ...lines.map((l) => `│ ${l}`), '└───────────────────┘'].join('\n');
  const base = {
    version: 1,
    updatedAt: 1780000090,
    feeds: {
      showStatusG: {
        label: 'SHOW-STATUS-G',
        // 実運用と同じ箱付きヘッダー (SOREN/FFMPEG) と A/B 残り行を渡す。
        text: boxed([
          'SOREN/FFMPEG  #54104 games   Best:22645   Avg:1424',
          'Recent30:1897  Trend:▲+33%  Rus:2%=',
          'Strategy: 3a9bd96b [A]  v50569  3380L',
          'A/B: A 3a9bd96b vs B 389f4387 n=52(A26/B26) d=-8',
          '     k13/19  adopt-look in 24g  max 96g  score',
          'Live: STOP  score=2269  pieces=39',
        ]),
        updatedAt: 1780000080,
        lineCount: 8,
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

  const summaryTexts = overlay.summary.children.map((line) => line.textContent);
  assert.equal(summaryTexts.length, 4, 'top rail keeps four slots');
  assert.ok(summaryTexts.some((text) => /A\/B:/.test(text)), 'A/B hashes and score stay in the top rail');
  assert.ok(
    summaryTexts.some((text) => /A\/B 残り:.*あと24〜96試合.*k13\/19/.test(text)),
    'remaining games must be visible in the top rail',
  );
  assert.ok(!summaryTexts.some((text) => /Strategy:/.test(text)), 'redundant Strategy slot yields to remaining games');
  assert.ok(summaryTexts.some((text) => /SOREN.*#54104/.test(text)), 'game identity and result count remain without backend branding');
  const summaryClasses = overlay.summary.children.map((line) => line.className);
  assert.ok(summaryClasses.some((cls) => cls.includes('sum-head')));
  assert.ok(summaryClasses.some((cls) => cls.includes('sum-ab')));

  // ヘッダーの箱はサイドバーからは外れるが、上部レールには残る (二重表示の回避)。
  const sidebarTexts = overlay.feedG.children.map((line) => line.textContent);
  assert.ok(!sidebarTexts.some((text) => /A\/B:/.test(text)), 'sidebar must not repeat the stripped header box');

  // look を使い切った後は「次の採用判定なし」を最長の残り試合数として出す。
  overlay.setState({
    ...base,
    feeds: {
      ...base.feeds,
      showStatusG: {
        ...base.feeds.showStatusG,
        text: boxed([
          'SOREN/FFMPEG  #54200 games   Best:22645   Avg:1424',
          'Recent30:1897  Trend:▲+33%  Rus:2%=',
          'Strategy: 3a9bd96b [A]  v50569  3380L',
          'A/B: A 3a9bd96b vs B 389f4387 n=140(A70/B70) d=+120',
          '     k37  no adopt-look left  max 0g  score',
        ]),
        updatedAt: 1780000180,
      },
    },
  });
  await overlay.tick(1);
  const finalTexts = overlay.summary.children.map((line) => line.textContent);
  assert.ok(finalTexts.some((text) => /A\/B 残り: 最長0試合/.test(text)), 'past the last look only the forced finish remains');
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

  const opsCard = overlay.feedS.children[0];
  assert.equal(opsCard.className, 'ops-dashboard', 'recognized OPS feed uses the structured dashboard path');
  assert.ok(opsCard.children.length > 0, 'structured OPS dashboard contains visible summary cards');

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


test('rate-limit status stays in show-status-g without duplication in the top rail', async () => {
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
  assert.ok(overlay.summary.children.every(line => !/AI使用量上限|AI 429|main=|fb=/.test(line.textContent)));
  assert.ok(overlay.feedG.children.some(line => /AI 429 main=deepseek-v4-flash/.test(line.textContent)));
  assert.ok(overlay.feedG.children.some(line => /fb=minimax-m3/.test(line.textContent)));
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


test('prediction panel shows exact range and excluded current game, then disappears', async () => {
  const state = {feeds: {showStatus: {text: '予想対象：#49849〜#49896｜終了1/48｜残り47試合\n#49848：今回の予想対象外'}}};
  const app = await runBroadcastOverlayScript(state);
  assert.equal(app.documentElement.dataset.predictionActive, '1');
  assert.match(app.prediction.textContent, /#49849〜#49896/);
  assert.match(app.prediction.textContent, /終了1\/48｜残り47試合/);
  assert.match(app.prediction.textContent, /今回の予想対象外/);
  app.setState({feeds: {}});
  await app.tick(2);
  assert.equal(app.documentElement.dataset.predictionActive, '0');
  assert.equal(app.prediction.textContent, '');
});


test('unboxed corner charts after an AI box stay visible in the live sidebar', async () => {
  const lines = ['┌─────────────┐', '│ AI 429 wait │', '└─────────────┘', '',
    'SOREN/CORNER: RETRO / robots / 進行中', 'Stats: count=4 mean=10',
    '┌ SCORE TIMELINE ┐', '│ ▁▃▅█ │', '└───────────────┘', 'SCORE DISTRIBUTION'];
  const overlay = await runBroadcastOverlayScript({
    version: 1, updatedAt: 1780000090,
    feeds: {
      showStatusG: { label: 'SHOW-STATUS-G', text: lines.join('\n'),
        segments: lines.map((t) => [{ t, c: '#00ff00' }]), lineCount: lines.length },
      showStatus: { label: 'SHOW-STATUS', text: 'Backend: fixture', lineCount: 1 },
    },
    notifications: { visibleSec: 18, events: [], work: { active: false }, generators: [] },
  });
  const rendered = overlay.feedG.children.map((line) => line.children.map((span) => span.textContent).join('')).join('\n');
  assert.match(rendered, /Stats: count=4/);
  assert.match(rendered, /SCORE TIMELINE/);
  assert.match(rendered, /▁▃▅█/);
  assert.match(rendered, /SCORE DISTRIBUTION/);
});

// Render a Hanjuku card with the real status_dashboard.py / docich_corner_stats
// code so the card parsers can never drift away from the producer's line format.
function renderHanjukuCardText() {
  const script = `
import json, sys, tempfile, time
from pathlib import Path
sys.path.insert(0, ${JSON.stringify(REPO_ROOT)})
from lib.docich_corner_stats import load_active_corner
import status_dashboard as sd
root = Path(tempfile.mkdtemp())
ident = dict(game='hanjuku-hero', runtime_id='g530-abcdef12', generation=530, lease_id='l')
rt = root / 'runtimes' / ident['runtime_id']
rt.mkdir(parents=True)
def w(p, v):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(v, ensure_ascii=False), encoding='utf-8')
w(root / 'retro_corner.json', dict(schema_version=1, status='active', game='hanjuku-hero', bot_identity=ident))
w(root / 'game_switch.json', dict(phase='ready', active=ident))
w(rt / 'hanjuku_run.json', dict(ident, observed_at=time.time(), phase='battle', actions_sent=412,
    battles_started=9, battles_finished=8, observations=5300, unchanged_seconds=3.2))
w(rt / 'hanjuku_bot.json', dict(decision_trace=ident, screen_kind='battle_menu', policy=dict(
    chapter=2, gold=176, month='1-11', captured=['カストーラ', 'スペンソニア'], lost=['ジョンリギ'],
    home_lost=False, active='J3', variant='chart_adjusted',
    stats=dict(wins=4, losses=1, unclassified=1, cards_confirmed=4), soldiers_seen=9, tick=5300,
    orders={'A1': 'launched', 'A2': 'failed'},
    garrison={'アルマムーン': ['ゼウス', 'ユイートル']},
    sorties={'J3': dict(general='どうし', target='ナキューメラ', status='en_route', tick=5290)},
    battle={'enemy': 'dragon', 'ally': 'ゼウス', 'enemy_hp': 31, 'ally_hp': 44})))
print("\\n".join(sd.render_docich_corner_stats(load_active_corner(root))))
`;
  const out = spawnSync('python3', ['-c', script], { encoding: 'utf8' });
  assert.equal(out.status, 0, `renderer probe failed: ${out.stderr}`);
  assert.ok(out.stdout.includes('半熟英雄 / 最終観測・記録'), out.stdout);
  return out.stdout;
}

// The card parsers anchor on the leading fields of each status_dashboard.py
// line. Keep this text byte-identical to the current renderer output so a new
// card field cannot silently stop reaching the broadcast sidebar.
function hanjukuFixture(overrides = {}) {
  return { version:1, updatedAt:1780001000,
    feeds:{showStatusG:{updatedAt:1780001000,lineCount:10,text:
      'SOREN/CORNER: RETRO / hanjuku-hero / 進行中\n半熟英雄 / 最終観測・記録\n'
      + '  第2話 / 所持金 123G\n  ゲーム内: 1年11月（最終観測）\n'
      + '  兵力: 9名 / 停滞 3秒\n  占領記録 5城（現在の城数ではない）\n'
      + '  保有: カストーラ/スペンソニア\n  戦闘結果: 4勝 / 1敗 / 未分類 1\n'
      + '  交戦HP: 敵 31（dragon） / 我 44（ゼウス）\n'
      + '  駐留: アルマムーン=ゼウス/ユイートル\n  行軍中: どうし→ナキューメラ\n'
      + '  出撃: 成立 4 / 失敗 1\n  画面: battle_menu / battle / 方針 chart_adjusted\n'
      + '  計画段階: J3（完了未確認）\n  保留計画: sortie\n'
      + '  実入力: 412回 / 観測 0秒前（5300回）', ...overrides},
      showStatus:{text:'● Backend FFMPEG LIVE',updatedAt:1780001000}},
    notifications:{visibleSec:18,events:[],work:{active:false},generators:[]} };
}

test('Hanjuku sidebar uses large current-chapter cards without changing the top rail source', async () => {
  const state = hanjukuFixture(); const ui = await runBroadcastOverlayScript(state);
  assert.equal(ui.documentElement.dataset.hanjukuActive, '1');
  assert.equal(ui.feedG.querySelector('.hanjuku-chapter').textContent, '第 2 話');
  assert.equal(ui.feedG.querySelector('.hanjuku-status').textContent, '作戦選択');
  assert.equal(ui.feedG.querySelector('.hanjuku-kpis').children[0].querySelector('.hanjuku-value').textContent, '123 G');
  assert.equal(ui.feedG.querySelector('.hanjuku-dots').children.filter(x => x.className.includes('current')).length, 1);
  assert.match(state.feeds.showStatusG.text, /SOREN\/CORNER/);
});

test('Hanjuku cached figures disappear when source stops updating even without a new payload', async () => {
  const ui = await runBroadcastOverlayScript(hanjukuFixture());
  await ui.tick(31);
  assert.equal(ui.feedG.querySelector('.hanjuku-badge').textContent, '要確認');
  assert.equal(ui.feedG.querySelector('.hanjuku-chapter').textContent, '話数 未確認');
  assert.equal(ui.feedG.querySelector('.hanjuku-kpis').children[0].querySelector('.hanjuku-value').textContent, '— G');
});

test('switching away removes Hanjuku layout and renders the new game without stale figures', async () => {
  const ui = await runBroadcastOverlayScript(hanjukuFixture());
  ui.setState(hanjukuFixture({text:'SOREN/CORNER: RETRO / robots / 進行中\nLive: next game'}));
  await ui.tick(1);
  assert.equal(ui.documentElement.dataset.hanjukuActive, '');
  assert.equal(ui.feedG.querySelector('.hanjuku-card'), null);
});

test('unavailable, future or malformed Hanjuku status never becomes fabricated progress', async () => {
  for (const overrides of [
    {text:'SOREN/CORNER: RETRO / hanjuku-hero / 進行中\n半熟英雄 / ゲーム状況\n状態: 未確認'},
    {updatedAt:1780002000},
    {text:'SOREN/CORNER: RETRO / hanjuku-hero / 進行中\n半熟英雄 / 最終観測・記録\n第99話 / 所持金 不明G\n画面: <script> / battle'},
  ]) {
    const ui = await runBroadcastOverlayScript(hanjukuFixture(overrides));
    assert.equal(ui.feedG.querySelector('.hanjuku-chapter').textContent, '話数 未確認');
    assert.equal(ui.feedG.querySelector('.hanjuku-dots').children.filter(x=>x.className.includes('current')).length, 0);
  }
});


test('Hanjuku prioritizes observed game state and omits automation counters', async () => {
  const f = hanjukuFixture();
  f.feeds.showStatusG.text = f.feeds.showStatusG.text.replace(
    '計画段階: J3（完了未確認）', '計画段階: F1（完了未確認）');
  const ui = await runBroadcastOverlayScript(f);
  assert.equal(ui.feedG.querySelector('.hanjuku-month').textContent, '1年 11月（最終観測）');
  assert.match(ui.feedG.querySelector('.hanjuku-castles').textContent, /5城.*累計/);
  assert.equal(ui.feedG.querySelector('.hanjuku-plan'), null);
  assert.equal(ui.feedG.querySelector('.hanjuku-inputs'), null);
  await ui.tick(31);
  assert.equal(ui.feedG.querySelector('.hanjuku-month').textContent, '年月 未確認');
  assert.equal(ui.feedG.querySelector('.hanjuku-inputs'), null);
  assert.equal(ui.feedG.querySelector('.hanjuku-plan'), null);
});

test('Hanjuku shows a meaningful stall while omitting brief input pauses', async () => {
  for (const seconds of [0, 29, 30, 120]) {
    const f = hanjukuFixture();
    f.feeds.showStatusG.text = f.feeds.showStatusG.text.replace('停滞 3秒', `停滞 ${seconds}秒`);
    const ui = await runBroadcastOverlayScript(f);
    const notice = ui.feedG.querySelector('.hanjuku-stalled');
    if (seconds < 30) assert.equal(notice, null);
    else assert.equal(notice.textContent, `${seconds}秒`);
    await ui.tick(31);
    assert.equal(ui.feedG.querySelector('.hanjuku-stalled'), null);
  }
});

test('Hanjuku card surfaces the live observed battle, roster and sortie state', async () => {
  const ui = await runBroadcastOverlayScript(hanjukuFixture());
  assert.equal(ui.feedG.querySelector('.hanjuku-duel').textContent, '交戦 dragon 31 / ゼウス 44');
  assert.equal(ui.feedG.querySelector('.hanjuku-holds').textContent, 'カストーラ ・ スペンソニア');
  assert.equal(ui.feedG.querySelector('.hanjuku-garrison').textContent, 'アルマムーン=ゼウス/ユイートル');
  assert.equal(ui.feedG.querySelector('.hanjuku-marching').textContent, 'どうし→ナキューメラ');
  assert.equal(ui.feedG.querySelector('.hanjuku-troops').textContent, '9名');
  assert.equal(ui.feedG.querySelector('.hanjuku-stalled'), null);
  assert.equal(ui.feedG.querySelector('.hanjuku-orders').textContent, '出撃 成立 4 / 失敗 1');
  // The battle line carries a trailing strategy field; it must not hide the screen.
  assert.equal(ui.feedG.querySelector('.hanjuku-status').textContent, '作戦選択');
  assert.equal(ui.feedG.querySelector('.hanjuku-kpis').children[1].querySelector('.hanjuku-value').textContent, '4 / 1');
});

test('Hanjuku card omits the live-battle strip when no battle is observed', async () => {
  const ui = await runBroadcastOverlayScript(hanjukuFixture({
    text:'SOREN/CORNER: RETRO / hanjuku-hero / 進行中\n半熟英雄 / 最終観測・記録\n'
      + '第2話 / 所持金 123G\n交戦HP: 戦闘記録なし\n画面: world_map / field'}));
  assert.equal(ui.feedG.querySelector('.hanjuku-duel'), null);
  assert.equal(ui.feedG.querySelector('.hanjuku-status').textContent, '戦況を確認中');
});

test('the card parsers still read every field the real renderer emits', async () => {
  // Regression guard: a hand-written fixture can drift from status_dashboard.py
  // and silently drop a value. Drive the card with the renderer's own output.
  const rendered = renderHanjukuCardText();
  const ui = await runBroadcastOverlayScript(hanjukuFixture({ text: rendered }));
  assert.equal(ui.documentElement.dataset.hanjukuActive, '1');
  assert.equal(ui.feedG.querySelector('.hanjuku-chapter').textContent, '第 2 話');
  assert.equal(ui.feedG.querySelector('.hanjuku-kpis').children[0].querySelector('.hanjuku-value').textContent, '176 G');
  assert.equal(ui.feedG.querySelector('.hanjuku-kpis').children[1].querySelector('.hanjuku-value').textContent, '4 / 1');
  assert.equal(ui.feedG.querySelector('.hanjuku-month').textContent, '1年 11月（最終観測）');
  assert.equal(ui.feedG.querySelector('.hanjuku-status').textContent, '作戦選択');
  assert.equal(ui.feedG.querySelector('.hanjuku-duel').textContent, '交戦 dragon 31 / ゼウス 44');
  assert.equal(ui.feedG.querySelector('.hanjuku-holds').textContent, 'カストーラ ・ スペンソニア');
  assert.equal(ui.feedG.querySelector('.hanjuku-garrison').textContent, 'アルマムーン=ゼウス/ユイートル');
  assert.equal(ui.feedG.querySelector('.hanjuku-marching').textContent, 'どうし→ナキューメラ');
  assert.equal(ui.feedG.querySelector('.hanjuku-troops').textContent, '9名');
  assert.equal(ui.feedG.querySelector('.hanjuku-stalled'), null);
  assert.equal(ui.feedG.querySelector('.hanjuku-orders').textContent, '出撃 成立 1 / 失敗 1');
  assert.match(ui.feedG.querySelector('.hanjuku-castles').textContent, /2城/);
  assert.equal(ui.feedG.querySelector('.hanjuku-plan'), null);
  assert.equal(ui.feedG.querySelector('.hanjuku-inputs'), null);
});


test('Soren telemetry panels preserve chart values and switch away cleanly', async () => {
  const text = fs.readFileSync(new URL('./fixtures/soren-monitor.txt', import.meta.url), 'utf8');
  const f = hanjukuFixture({text});
  const ui = await runBroadcastOverlayScript(f);
  const monitor = ui.feedG.querySelector('.soren-monitor');
  assert.ok(monitor);
  assert.equal(monitor.children.filter(e => e.className === 'monitor-section').length, 5);
  assert.equal(ui.documentElement.dataset.sorenMonitor, '1');
  const sections = monitor.children.filter(e => e.className === 'monitor-section');
  const rows = sections.flatMap(e => e.querySelector('.monitor-section-body').children.flatMap(r =>
    r.className === 'monitor-chart' ? r.children.map(line => line.textContent) : [r.textContent]));
  assert.ok(rows.some(t => t.includes('5691')));
  assert.ok(rows.some(t => t.includes('3722 4145 2822')));
  assert.ok(rows.some(t => t.includes('LastStep +240')));
  assert.ok(rows.every(t => !t.includes('FFMPEG')));
  ui.setState(hanjukuFixture()); await ui.tick(1);
  assert.equal(ui.feedG.querySelector('.soren-monitor'), null);
  assert.equal(ui.documentElement.dataset.sorenMonitor, '');
});

test('console record lines reach the actual dashboard, including empty and interrupted sessions', async () => {
  const makeText = (next, count, reason = null) => {
    const script = `
import sys, json
sys.path.insert(0, ${JSON.stringify(REPO_ROOT)})
import status_dashboard as sd
value={'history_status':'readable', 'session_count':${count}, 'session_mean':20 if ${count} else None,
       'session_best':30 if ${count} else None, 'latest':{'score':30,'at':160,'age':40} if ${count} else None,
       'next':${JSON.stringify(next)}, 'remaining':1, 'remaining_seconds':300, 'reason':${reason ? JSON.stringify(reason) : 'None'}}
corner={'kind':'retro','label':'RETRO','game':'nsnake','status':'active','target_matches':3,
        'session_matches':${count},'scores':[{'score':10},{'score':30}] if ${count} else [],'console':value}
print('\\n'.join(sd.render_docich_corner_stats(corner)))`;
    const out = spawnSync('python3', ['-c', script], { encoding:'utf8' });
    assert.equal(out.status, 0, out.stderr); return out.stdout;
  };
  const ui = await runBroadcastOverlayScript({feeds:{showStatusG:{text:makeText('collect-results',2)}}});
  let dashboard = ui.feedG.querySelector('.game-dashboard');
  const rows = dashboard.querySelector('.game-records').children.map(r=>r.textContent).join('\n');
  assert.match(rows,/Session: n=2 \/ best=30 \/ mean=20/);
  assert.match(rows,/Result: 30 .*40s ago/);
  assert.match(rows,/1 matches left \/ limit 5:00/);
  assert.match(rows,/live score unobserved/);
  for (const [next, count] of [['restore-wait',2],['unverified',0]]) {
    ui.setState({feeds:{showStatusG:{text:makeText(next,count)}}}); await ui.tick(1);
    dashboard=ui.feedG.querySelector('.game-dashboard');
    const text=dashboard.querySelector('.game-records').children.map(r=>r.textContent).join('\n');
    assert.match(text,next==='restore-wait'?/restoration pending/:/runtime unverified/);
    assert.doesNotMatch(text,/matches left/);
  }
  for (const reason of ['manual_saved_stop','manual_forced_stop','readiness_timeout']) {
    ui.setState({feeds:{showStatusG:{text:makeText('restore-wait',2,reason)}}}); await ui.tick(2);
    const records=ui.feedG.querySelector('.game-records').children.map(r=>r.textContent);
    assert.equal(records.length,4);
    assert.match(records[3],/^Reason: .*\(record\) \/ live score unobserved$/);
  }
  ui.setState(hanjukuFixture()); await ui.tick(3);
  assert.equal(ui.feedG.querySelector('.game-records'),null,'console records disappear on a game switch');
});
