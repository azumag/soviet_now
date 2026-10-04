import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { GameObservationWriter } from '../lib/game_observation.mjs';

function fixture(t) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'game-observation-'));
  t.after(() => fs.rmSync(dir, { recursive: true, force: true }));
  const file = path.join(dir, 'observation.json');
  let clock = 100;
  const writer = new GameObservationWriter(file, { now: () => clock });
  return { writer, file, read: () => JSON.parse(fs.readFileSync(file)), tick: n => { clock += n; } };
}
const stop = { state: 'STOP', score: 6401, makeSorenCount: 1, pieces: [{ type: 16 }] };

test('unchanged board gets a fresh heartbeat without changing game or STOP identity', t => {
  const f = fixture(t);
  f.writer.observe(stop);
  const before = f.read();
  f.tick(1);
  f.writer.observe(stop);
  const after = f.read();
  assert.equal(after.game_id, before.game_id);
  assert.equal(after.stop_id, before.stop_id);
  assert.equal(after.observed_epoch, 101);
});

test('MOVE and missing observations invalidate STOP even between throttled writes', t => {
  const f = fixture(t);
  f.writer.observe(stop);
  const first = f.read();
  f.writer.observe({ ...stop, state: 'MOVE' });
  assert.equal(f.read().stop_id, null);
  f.writer.observe(stop);
  assert.notEqual(f.read().stop_id, first.stop_id);
  f.writer.observe(null);
  assert.equal(fs.existsSync(f.file), false);
  f.writer.observe(stop);
  assert.ok(f.read().stop_id);
});

test('new games, bridge starts and explicit RETRY rotate identity', t => {
  const f = fixture(t);
  f.writer.observe(stop);
  const first = f.read();
  f.writer.observe({ ...stop, state: 'GAMEOVER' });
  f.writer.observe({ ...stop, state: 'MOVE' });
  assert.notEqual(f.read().game_id, first.game_id);
  f.writer.observe(stop);
  const second = f.read();
  f.writer.reset();
  f.writer.observe(stop);
  assert.notEqual(f.read().game_id, second.game_id);
  const third = f.read();
  new GameObservationWriter(f.file).observe(stop);
  assert.notEqual(f.read().game_id, third.game_id);
});

test('invalid counters cannot create a STOP token; changed boards reset it', t => {
  const f = fixture(t);
  for (const count of [0, -1, true, null, '1', NaN, Infinity, 1.5]) {
    f.writer.observe({ ...stop, makeSorenCount: count });
    assert.equal(f.read().stop_id, null);
  }
  f.writer.observe(stop);
  const before = f.read();
  f.writer.observe({ ...stop, score: 6402 });
  assert.notEqual(f.read().stop_id, before.stop_id);
});
