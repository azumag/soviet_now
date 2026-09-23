import test from 'node:test';
import assert from 'node:assert/strict';

import { nextGameInstanceId } from '../lib/jev_game_nonce.mjs';

function counter() {
  let n = 0;
  return () => {
    n += 1;
    return `00000000-0000-4000-8000-${String(n).padStart(12, '0')}`;
  };
}

test('bridge generates a nonce on the first observation', () => {
  const generate = counter();
  const first = nextGameInstanceId({ generate });
  assert.equal(first.gameInstanceId, '00000000-0000-4000-8000-000000000001');
  assert.equal(first.rotated, false);
  assert.equal(first.phase, null);
});

test('a game-provided nonce wins and never rotates', () => {
  const generate = counter();
  const result = nextGameInstanceId({
    providedId: '22222222-2222-4222-8222-222222222222',
    previousPhase: 'GAMEOVER',
    currentPhase: 'MOVE',
    currentId: '00000000-0000-4000-8000-000000000001',
    generate,
  });
  assert.equal(result.gameInstanceId, '22222222-2222-4222-8222-222222222222');
  assert.equal(result.rotated, false);
  assert.equal(result.phase, 'MOVE');
});

test('rotates when a terminal game restarts into MOVE', () => {
  const generate = counter();
  const start = nextGameInstanceId({ currentPhase: 'MOVE', generate });
  const settled = nextGameInstanceId({
    previousPhase: 'MOVE',
    currentPhase: 'GAMEOVER',
    currentId: start.gameInstanceId,
    generate,
  });
  assert.equal(settled.gameInstanceId, start.gameInstanceId);
  assert.equal(settled.rotated, false);
  const restarted = nextGameInstanceId({
    previousPhase: 'GAMEOVER',
    currentPhase: 'MOVE',
    currentId: settled.gameInstanceId,
    generate,
  });
  assert.notEqual(restarted.gameInstanceId, start.gameInstanceId);
  assert.equal(restarted.rotated, true);
  assert.equal(restarted.phase, 'MOVE');
});

test('founding STOP returns to MOVE within the same game', () => {
  const generate = counter();
  const start = nextGameInstanceId({ currentPhase: 'MOVE', generate });
  const restarted = nextGameInstanceId({
    previousPhase: 'STOP',
    currentPhase: 'MOVE',
    currentId: start.gameInstanceId,
    generate,
  });
  assert.equal(restarted.rotated, false);
  assert.equal(restarted.gameInstanceId, start.gameInstanceId);
});

test('does not rotate within the same game', () => {
  const generate = counter();
  const start = nextGameInstanceId({ currentPhase: 'MOVE', generate });
  const again = nextGameInstanceId({
    previousPhase: 'MOVE',
    currentPhase: 'MOVE',
    currentId: start.gameInstanceId,
    generate,
  });
  assert.equal(again.gameInstanceId, start.gameInstanceId);
  assert.equal(again.rotated, false);
});

test('does not rotate on the move into a terminal phase', () => {
  const generate = counter();
  const start = nextGameInstanceId({ currentPhase: 'MOVE', generate });
  const over = nextGameInstanceId({
    previousPhase: 'MOVE',
    currentPhase: 'GAMEOVER',
    currentId: start.gameInstanceId,
    generate,
  });
  assert.equal(over.gameInstanceId, start.gameInstanceId);
  assert.equal(over.rotated, false);
  assert.equal(over.phase, 'GAMEOVER');
});
