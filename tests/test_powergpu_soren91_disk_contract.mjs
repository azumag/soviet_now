import test from 'node:test';
import assert from 'node:assert/strict';

import {
  buildLaunchArgs,
  defaults,
  validateOptions,
} from '../tools/powergpu_soren91_session.mjs';

test('PowerGPU Tier 0 disk is fixed to 8GB', () => {
  const options = validateOptions({
    ...defaults({}),
    image: 'ghcr.io/azumag/soren91-gpu-runner:test',
  });

  assert.equal(options.diskGb, 8);
  assert.match(buildLaunchArgs(options).join(' '), /--disk 8(?:\s|$)/);
  assert.throws(() => validateOptions({ ...options, diskGb: 20 }), /diskGb/);
});
