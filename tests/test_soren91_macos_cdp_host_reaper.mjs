import test from 'node:test';
import assert from 'node:assert/strict';

import {
  PROFILE_DIR_PREFIX,
  reapStaleProfileDirs,
  STALE_PROFILE_DIR_MS,
} from '../tools/soren91_macos_cdp_host.mjs';

test('reapStaleProfileDirs keeps a valid stale profile when a live process still references it', () => {
  const now = 10_000_000;
  const name = `${PROFILE_DIR_PREFIX}${now - STALE_PROFILE_DIR_MS - 1}`;
  const expectedDir = `/tmp/${name}`;
  const inUseChecks = [];
  const removed = [];

  reapStaleProfileDirs({
    tmpDir: '/tmp',
    now: () => now,
    listImpl: () => [name],
    isInUse: (dir) => {
      inUseChecks.push(dir);
      return true;
    },
    rmImpl: (dir) => removed.push(dir),
  });

  assert.deepEqual(inUseChecks, [expectedDir]);
  assert.deepEqual(removed, []);
});
