import test from 'node:test';
import assert from 'node:assert/strict';
import { JevDropGuard, commandKey, validateJevDrop } from '../lib/jev_guarded_drop.mjs';

const identity = {
  run_id: '11111111-1111-4111-8111-111111111111',
  game_instance_id: '22222222-2222-4222-8222-222222222222',
  game_generation: 18,
  player_generation: 3,
  opportunity_seq: 7,
  frame_seq: 120,
  drop_piece_id: 17,
};

function command(extra = {}) {
  return {
    action: 'drop',
    player_policy: 'jev',
    command_id: '33333333-3333-4333-8333-333333333333',
    expires_at: Date.now() / 1000 + 60,
    x: 0,
    ...identity,
    expected_drop_piece_id: identity.drop_piece_id,
    candidate_id: 'c12',
    ...extra,
  };
}

test('validates identity, MOVE phase and stable candidate key', () => {
  const checked = validateJevDrop(command(), { identity, state: { state: 'MOVE' } });
  assert.equal(checked.ok, true);
  assert.equal(checked.status, 'ready');
  assert.equal(checked.key, commandKey(command()));
});

test('rejects stale identity, wrong phase, invalid x and future frame', () => {
  for (const [extra, reason] of [
    [{ opportunity_seq: 6 }, 'identity_mismatch'],
    [{ x: Number.NaN }, 'invalid_x'],
    [{ frame_seq: 121 }, 'future_frame'],
  ]) {
    assert.equal(validateJevDrop(command(extra), { identity, state: { state: 'MOVE' } }).reason, reason);
  }
  assert.equal(validateJevDrop(command(), { identity, state: { state: 'GAMEOVER' } }).reason, 'phase_not_move');
  assert.equal(validateJevDrop(command({ expected_drop_piece_id: 18 }), { identity, state: { state: 'MOVE' } }).reason, 'drop_piece_mismatch');
});

test('accepts a command once and rejects replay without a second dispatch', () => {
  const guard = new JevDropGuard();
  const context = { identity, state: { state: 'MOVE' } };
  assert.equal(guard.dispatch(command(), context).status, 'accepted');
  assert.equal(guard.dispatch(command(), context).status, 'duplicate');
});
