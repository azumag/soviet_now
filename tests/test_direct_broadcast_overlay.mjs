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
  assert.match(html, /sorenOverlayRegion/);
  assert.match(html, /data-soren-region="sidebar"/);
  assert.match(html, /data-soren-region="top"/);
  assert.match(html, /data-soren-region="bottom"/);
  assert.match(html, /data-soren-region="sidebar"[^}]+#broadcast-sidebar\s*\{\s*left:\s*0/s);
  assert.match(html, /data-soren-region="bottom"[^}]+#bottom-rail\s*\{\s*top:\s*0/s);
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
        text: '● Backend     FFMPEG LIVE\n◆ Queued      34/100 games\n▾ LastDrop   T36',
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
