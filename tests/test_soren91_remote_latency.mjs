import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { execFileSync } from 'node:child_process';

const runnerUrl = new URL('../soren91/run_player_loop.sh', import.meta.url);
const source = readFileSync(runnerUrl, 'utf8');

function remoteDefaultBlock() {
  const match = source.match(/if \[ -n "\$\{SOREN91_REMOTE_CDP_URL:-\}" \] && \[ -z "\$\{SOREN91_RANK_POSTDROP_PROBE\+x\}" \]; then\n\texport SOREN91_RANK_POSTDROP_PROBE=0\nfi/);
  assert.ok(match, 'remote post-drop probe default block must remain explicit and bounded');
  return match[0];
}

function evalBlock(env = {}) {
  const block = remoteDefaultBlock();
  return execFileSync('bash', ['-c', `${block}\nprintf '%s' "${SOREN91_RANK_POSTDROP_PROBE-unset}"`], {
    encoding: 'utf8',
    env: { PATH: process.env.PATH, ...env },
  });
}

test('remote CDP disables redundant post-drop ranking burst by default', () => {
  assert.equal(evalBlock({ SOREN91_REMOTE_CDP_URL: 'http://100.64.0.2:9222' }), '0');
});

test('explicit remote diagnostic opt-in is preserved', () => {
  assert.equal(evalBlock({
    SOREN91_REMOTE_CDP_URL: 'http://100.64.0.2:9222',
    SOREN91_RANK_POSTDROP_PROBE: '1',
  }), '1');
});

test('local player keeps the existing post-drop probe default untouched', () => {
  assert.equal(evalBlock({}), 'unset');
});

test('runner still leaves external improve default enabled and invokes main once per attempt', () => {
  assert.match(source, /SOREN91_EXTERNAL_IMPROVE="\$\{SOREN91_EXTERNAL_IMPROVE:-1\}" node main\.mjs/);
});
