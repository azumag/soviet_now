import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'fs';
import os from 'os';
import path from 'path';

import { buildDirectBroadcastOverlayState } from '../lib/direct_broadcast_overlay.mjs';


function fixture() {
  const temp = fs.mkdtempSync(path.join(os.tmpdir(), 'paper-improve-overlay-'));
  const file = (name, content = '') => {
    const target = path.join(temp, name);
    fs.writeFileSync(target, content);
    return target;
  };
  return {
    temp,
    sources: {
      eventHtmlFile: file('event.html', '<pre></pre>'),
      statsHtmlFile: file('stats.html', '<pre>GAME</pre>'),
      opsHtmlFile: file('ops.html', '<pre>OPS</pre>'),
      improveStateFile: file('improve_state.json', JSON.stringify({ status: 'idle' })),
      improveLogFile: file('improve_ai.log', ''),
      wildcardStateFile: file('wildcard.json', JSON.stringify({ phase: 'idle' })),
      paperImproveStateFile: file('paper_improve_status.json', '{}'),
      topOverrideFile: file('top.json', ''),
    },
  };
}


test('PAPER improvement uses the existing right sidebar improve feed', () => {
  const { temp, sources } = fixture();
  try {
    const nowSec = 1_800_000_000;
    fs.writeFileSync(sources.paperImproveStateFile, JSON.stringify({
      schema_version: 1,
      source: 'paper',
      status: 'running',
      phase: 'generate',
      progress: 35,
      detail: 'AIに改善候補を依頼中',
      started_at: nowSec - 10,
      updated_at: nowSec,
    }));
    const feed = buildDirectBroadcastOverlayState({ sources }, nowSec * 1000).feeds.improve;
    assert.equal(feed.active, true);
    assert.equal(feed.source, 'paper');
    assert.equal(feed.status, 'paper:running');
    assert.equal(feed.phase, 'generate');
    assert.equal(feed.progress, 35);
    assert.match(feed.logLines.join('\n'), /PAPER IMPROVE 35%/);
    assert.match(feed.logLines.join('\n'), /AIに改善候補を依頼中/);
  } finally {
    fs.rmSync(temp, { recursive: true, force: true });
  }
});


test('recent PAPER completion remains visible briefly then yields to normal status', () => {
  const { temp, sources } = fixture();
  try {
    const nowSec = 1_800_000_000;
    fs.writeFileSync(sources.paperImproveStateFile, JSON.stringify({
      status: 'improved', phase: 'done', progress: 100,
      detail: '戦略パラメータを更新', started_at: nowSec - 60, updated_at: nowSec - 30,
    }));
    const recent = buildDirectBroadcastOverlayState({ sources }, nowSec * 1000).feeds.improve;
    assert.equal(recent.active, true);
    assert.equal(recent.source, 'paper');
    assert.match(recent.logLines[0], /✓ PAPER IMPROVE 100%/);

    const expired = buildDirectBroadcastOverlayState({ sources }, (nowSec + 121) * 1000).feeds.improve;
    assert.equal(expired.active, false);
    assert.equal(expired.source, 'soren');
  } finally {
    fs.rmSync(temp, { recursive: true, force: true });
  }
});


test('Soren improvement wins when both improvement jobs are running', () => {
  const { temp, sources } = fixture();
  try {
    const nowSec = 1_800_000_000;
    fs.writeFileSync(sources.improveStateFile, JSON.stringify({
      status: 'running', phase: 'review', progress: 80, updated_at: nowSec,
    }));
    fs.writeFileSync(sources.improveLogFile, '[12:00:00] [IMPROVE] reviewing\n');
    fs.writeFileSync(sources.paperImproveStateFile, JSON.stringify({
      status: 'running', phase: 'generate', progress: 35, updated_at: nowSec,
    }));
    const feed = buildDirectBroadcastOverlayState({ sources }, nowSec * 1000).feeds.improve;
    assert.equal(feed.active, true);
    assert.equal(feed.source, 'soren');
    assert.equal(feed.status, 'running');
    assert.equal(feed.phase, 'review');
  } finally {
    fs.rmSync(temp, { recursive: true, force: true });
  }
});
