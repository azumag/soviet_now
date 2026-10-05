import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import vm from 'node:vm';
import { LoopMetrics } from '../soren91/loop_metrics.mjs';
import { midgameCommentStatus } from '../soren91/commentary_schedule.mjs';

// Exercise the production loop without a browser, disk writes, or network I/O.
const mainSource = readFileSync(new URL('../soren91/main.mjs', import.meta.url), 'utf8');
const start = mainSource.indexOf('async function gameLoop(');
const end = mainSource.indexOf('/**\n * HOLD', start);
assert.ok(start >= 0 && end > start);
const loopSource = mainSource.slice(start, end)
  .replaceAll('import.meta.url', '"file:///soren91/main.mjs"');
const move = (ranking = null) => ({ state: 'MOVE', ranking });
const waiting = (ranking = null) => ({ state: 'WAITING', ranking });
const repeat = (count, frame) => Array.from({ length: count }, () => ({ ...frame }));

async function replay(frames) {
  let now = 0, shots = 0, drops = 0, holds = 0, reentries = 0;
  let current;
  const ended = [], history = [], logs = [], decisions = [];
  const context = {
    join, dirname, fileURLToPath,
    HISTORY_DIR: 'history', SCREENSHOT_DIR: 'screens',
    DROP_COOLDOWN_MS: 1200, POLL_INTERVAL_MS: 200,
    CALIBRATION_MIN_PIECES: 999, CALIBRATION_MIN_CONFIDENCE: 0.55,
    MIN_RANKING_DETECTION_TURNS: 10,
    performance: { now: () => now },
    LoopMetrics: class extends LoopMetrics {
      constructor(options) { super({ ...options, now: () => now }); }
    },
    writeMetricsAtomically() {},
    console: { log: message => logs.push(message), error: message => logs.push(message) },
    process: { env: { SOREN91_RANKDIAG: '0' } },
    snapshotCurrentStrategyForGame: game => ({ strategyHash: 'fixed', snapshotPath: `${game}.mjs` }),
    existsSync: path => path === 'tmp/stop' && shots >= frames.length,
    writeFileSync() {}, copyFileSync() {},
    appendFileSync: (path, row) => history.push({ path, ...JSON.parse(row) }),
    loadCommentModule: async () => null,
    midgameCommentStatus,
    captureGameScreenshot: async () => { current = frames[shots++]; now += 1300; return null; },
    loadModule: async () => ({
      analyzeScreenshot: async () => ({
        state: current.state, pieces: current.state === 'MOVE' ? [{}] : [],
        confidence: 1, rank: current.liveRank ?? null,
        perception: { reason: current.state === 'MOVE' ? 'stable' : 'non-move' },
      }),
      detectRankingScreen: async () => current.ranking,
      detectConnectionErrorScreen: async () => false,
    }),
    loadStrategy: async () => ({ decide: state => {
      decisions.push({ shot: shots, canHold: state.canHold });
      return { x: 0, reason: 'test', hold: Boolean(current.requestHold && state.canHold) };
    } }),
    executeHold: async () => { holds++; now += 300; },
    executeDrop: async () => { drops++; now += 200; },
    handleGameOver: async (_page, game, turns, state, historyFile) => {
      ended.push({ game, turns, rank: state.rank, historyFile });
    },
    captureRankingTransitionBurst: async () => ({ detectedRank: null }),
    probeRankingImmediatelyAfterDrop: async () => ({ detectedRank: null }),
    queueRankingCommentOnce: async () => {},
    handleTitleScreen: async () => { reentries++; },
    execFile: () => ({ unref() {} }),
    sleep: async ms => { now += ms; },
  };
  const loop = vm.runInNewContext(`(${loopSource})`, context);
  await loop({}, {}, 1);
  return { drops, holds, ended, history, reentries, logs, decisions };
}

test('a confirmed short-round ranking archives that round and the next MOVE can play', async () => {
  const result = await replay([move(), ...repeat(6, waiting(8)), move(), move()]);
  assert.deepEqual(result.ended, [{ game: 1, turns: 1, rank: 8, historyFile: 'history/latest_0001.jsonl' }]);
  assert.deepEqual(result.history.map(({ path, turn }) => [path, turn]), [
    ['history/latest_0001.jsonl', 0],
    ['history/latest_0002.jsonl', 0],
    ['history/latest_0002.jsonl', 1],
  ]);
  assert.equal(result.reentries, 0);
});

test('the final WAITING observation counts when the next round immediately becomes MOVE', async () => {
  const result = await replay([...repeat(10, move()), ...repeat(6, waiting()), move(), move()]);
  assert.equal(result.ended.length, 1);
  assert.equal(result.ended[0].turns, 10);
  assert.equal(result.drops, 12);
  assert.equal(result.history.at(-1).path, 'history/latest_0002.jsonl');
  assert.equal(result.history.at(-1).turn, 1);
  assert.ok(!result.logs.some(message => message.includes('Waiting for inter-round screen')));
});

test('ranking or matchmaking screens before any drop do not create a completed round', async () => {
  const result = await replay([...repeat(6, waiting(8)), move()]);
  assert.equal(result.ended.length, 0);
  assert.equal(result.drops, 1);
  assert.equal(result.history[0].turn, 0);
});

test('HOLD without a subsequent drop does not consume the next round\'s first HOLD', async () => {
  const result = await replay([
    move(), { ...move(), requestHold: true },
    ...repeat(6, waiting(8)),
    { ...move(), requestHold: true }, move(),
  ]);
  assert.deepEqual(result.ended, [{ game: 1, turns: 1, rank: 8, historyFile: 'history/latest_0001.jsonl' }]);
  assert.equal(result.decisions.find(({ shot }) => shot === 9).canHold, true);
  assert.equal(result.holds, 2);
  assert.equal(result.drops, 2);
  assert.deepEqual(result.history.map(({ path, turn }) => [path, turn]), [
    ['history/latest_0001.jsonl', 0],
    ['history/latest_0002.jsonl', 0],
  ]);
});

test('HOLD remains unavailable within the same turn until a drop is sent', async () => {
  const result = await replay([
    { ...move(), requestHold: true },
    { ...move(), requestHold: true },
    { ...move(), requestHold: true }, move(),
  ]);
  assert.deepEqual(result.decisions.map(({ canHold }) => canHold), [true, false, true, false]);
  assert.equal(result.holds, 2);
  assert.equal(result.drops, 2);
  assert.equal(result.ended.length, 0);
});

test('a ranking seen before the first drop cannot confirm the following short round', async () => {
  const result = await replay([
    ...repeat(6, waiting(8)), move(), ...repeat(6, waiting()), move(),
  ]);
  assert.equal(result.ended.length, 0);
  assert.equal(result.drops, 2);
  assert.equal(result.history.at(-1).path, 'history/latest_0001.jsonl');
});

for (const ranking of [null, -1]) {
  test(`a short round with unconfirmed ranking ${ranking} is not finalized from waiting alone`, async () => {
    const result = await replay([move(), ...repeat(6, { ...waiting(ranking), liveRank: 8 }), move()]);
    assert.equal(result.ended.length, 0);
    assert.equal(result.drops, 2);
    assert.equal(result.history.at(-1).path, 'history/latest_0001.jsonl');
  });
}

for (const ranking of [8, -1]) {
  test(`a lingering result screen (${ranking}) cannot receive next-round input or finish twice`, async () => {
    const result = await replay([
      ...repeat(10, move()), ...repeat(6, waiting(8)),
      ...repeat(2, move(ranking)), waiting(8), move(), move(),
    ]);
    assert.equal(result.ended.length, 1);
    assert.equal(result.drops, 12);
    assert.equal(result.logs.filter(message => message.includes('Ignoring stale post-result ranking')).length, 2);
  });
}
