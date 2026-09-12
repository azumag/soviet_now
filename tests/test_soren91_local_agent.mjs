import test from 'node:test';
import assert from 'node:assert/strict';
import { authorized, buildSessionArgs, defaults, validateOptions } from '../tools/soren91_local_agent.mjs';

test('agent binds loopback by default and requires a long token', () => {
  const options = validateOptions({ ...defaults({}), token: 'a'.repeat(32) }, 'win32');
  assert.equal(options.host, '127.0.0.1');
  assert.equal(options.port, 19191);
  assert.throws(() => validateOptions({ ...options, token: 'short' }, 'win32'), /24 characters/);
});

test('Bearer token comparison rejects missing and wrong tokens', () => {
  const token = 'x'.repeat(32);
  assert.equal(authorized(undefined, token), false);
  assert.equal(authorized(`Bearer ${'y'.repeat(32)}`, token), false);
  assert.equal(authorized(`Bearer ${token}`, token), true);
});

test('agent only launches the bounded Windows session entry point', () => {
  const args = buildSessionArgs();
  assert.match(args[0], /soren91_windows_session\.mjs$/);
  assert.deepEqual(args.slice(1), ['--execute']);
});
