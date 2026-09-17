import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, writeFileSync, readFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { priorityActive, prependPriority } from '../lib/ai_priority_window.mjs';
import { runOpencodeText, generateTextWithFallbacks } from '../soren91/text_ai.mjs';

test('fixed window boundaries and original settings', () => {
  const start = Date.parse('2026-09-17T13:00:00Z');
  const end = Date.parse('2026-09-24T13:00:00Z');
  assert.equal(end - start, 604800000);
  assert.equal(priorityActive(start - 1), false);
  assert.equal(priorityActive(start), true);
  assert.equal(priorityActive(end - 1), true);
  assert.equal(priorityActive(end), false);
  const original = ['opencode:fixture'];
  assert.deepEqual(prependPriority(original, end), original);
  assert.deepEqual(prependPriority([], start), []);
  assert.equal(prependPriority(original, start).length, 3);
  assert.deepEqual(original, ['opencode:fixture']);
});

test('real child CLI transport: Go then OpenRouter then original; expiry restores original', async () => {
  const dir = mkdtempSync(join(tmpdir(), 'union-node-test-'));
  const calls = join(dir, 'calls');
  const oldClock = process.env.AI_PRIORITY_NOW_EPOCH;
  writeFileSync(join(dir, 'opencode'), `#!/bin/sh
while [ "$#" -gt 0 ]; do
  if [ "$1" = --model ]; then model="$2"; break; fi
  shift
done
cat >/dev/null
printf '%s\\n' "$model" >>"$CALLS"
case "$model" in
  opencode/fixture) printf '%s\\n' '{"type":"text","part":{"type":"text","text":"OK"}}'; exit 0 ;;
  *) exit 1 ;;
esac
`, { mode: 0o700 });
  const options = { opencodeAgent: 'opencode:fixture', timeoutMs: 5000,
    extraEnv: { PATH: `${dir}:${process.env.PATH}`, CALLS: calls } };
  try {
    process.env.AI_PRIORITY_NOW_EPOCH = '1789650000';
    assert.equal(await runOpencodeText('fixture', 'offline', options), 'OK');
    assert.deepEqual(readFileSync(calls, 'utf8').trim().split('\n'), [
      'opencode-go/union-alpha', 'openrouter/stealth/union-alpha', 'opencode/fixture',
    ]);
    writeFileSync(calls, '');
    assert.equal(await generateTextWithFallbacks('fixture', 'offline', {
      ...options, fallbackMode: 'opencode',
    }), 'OK');
    assert.deepEqual(readFileSync(calls, 'utf8').trim().split('\n'), [
      'opencode-go/union-alpha', 'openrouter/stealth/union-alpha', 'opencode/fixture',
    ]);
    process.env.AI_PRIORITY_NOW_EPOCH = '1790254800';
    writeFileSync(calls, '');
    assert.equal(await runOpencodeText('fixture', 'offline', options), 'OK');
    assert.equal(readFileSync(calls, 'utf8'), 'opencode/fixture\n');
    process.env.AI_PRIORITY_NOW_EPOCH = '1789650000';
    writeFileSync(calls, '');
    await assert.rejects(generateTextWithFallbacks('fixture', 'offline', {
      ...options, fallbackMode: 'opencode', includeOpencodeFallback: false,
    }));
    assert.equal(readFileSync(calls, 'utf8'), '');
  } finally {
    if (oldClock === undefined) delete process.env.AI_PRIORITY_NOW_EPOCH;
    else process.env.AI_PRIORITY_NOW_EPOCH = oldClock;
    rmSync(dir, { recursive: true, force: true });
  }
});
