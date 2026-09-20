import test from 'node:test';
import assert from 'node:assert/strict';

import { resolveDropPieceId } from '../lib/jev_drop_piece.mjs';

test('a game-provided id wins', () => {
  assert.equal(
    resolveDropPieceId({ providedId: 7, nextId: 3, pieces: [{ id: 1 }, { id: 2 }] }),
    7,
  );
});

test('next.id wins over the observed pieces', () => {
  assert.equal(resolveDropPieceId({ nextId: 3, pieces: [{ id: 1 }, { id: 9 }] }), 3);
});

test('falls back to the highest observed piece id', () => {
  assert.equal(
    resolveDropPieceId({ pieces: [{ id: 6 }, { id: 8 }, { id: 14 }, { id: 12 }] }),
    14,
  );
});

test('ignores non-integer and missing ids', () => {
  assert.equal(
    resolveDropPieceId({ pieces: [{ id: '9' }, { id: null }, {}, { id: 4 }, { id: 4.5 }] }),
    4,
  );
});

test('returns null when no id is observable', () => {
  assert.equal(resolveDropPieceId({ pieces: [] }), null);
  assert.equal(resolveDropPieceId({}), null);
  assert.equal(resolveDropPieceId({ pieces: [{ id: 'x' }] }), null);
});
