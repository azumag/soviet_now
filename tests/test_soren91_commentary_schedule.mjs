import test from 'node:test';
import assert from 'node:assert/strict';
import { midgameCommentStatus } from '../soren91/commentary_schedule.mjs';

const base = { sent: false, turn: 5, pieces: [{}, {}, {}], startedAt: 1000, now: 46000 };
test('slow 5-drop round gets commentary at 45 seconds, without waiting for 20 drops', () => {
  assert.equal(midgameCommentStatus(base).reason, 'elapsed-threshold');
  assert.equal(midgameCommentStatus(base).due, true);
});
test('fast round retains the original 20-drop trigger', () => {
  assert.equal(midgameCommentStatus({ ...base, turn: 20, now: 20000 }).reason, 'turn-threshold');
});
test('matching, sparse board, early turns, and duplicate requests do not trigger', () => {
  for (const patch of [{ turn: 4 }, { pieces: [] }, { pieces: [{}, {}] }, { sent: true }, { now: 45999 }, { startedAt: null }]) {
    assert.equal(midgameCommentStatus({ ...base, ...patch }).due, false, JSON.stringify(patch));
  }
});
test('new round requires its own elapsed time and sent state', () => {
  assert.equal(midgameCommentStatus({ ...base, startedAt: 46000 }).due, false);
  assert.equal(midgameCommentStatus({ ...base, sent: true, turn: 100 }).due, false);
});
