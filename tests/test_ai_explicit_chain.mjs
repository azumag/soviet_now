import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, writeFileSync, readFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { runOpencodeText, generateTextWithFallbacks } from '../soren91/text_ai.mjs';

test('explicit chain crosses child CLI in order; dates and custom agents are respected', async () => {
  const dir = mkdtempSync(join(tmpdir(), 'explicit-chain-'));
  const calls = join(dir, 'calls');
  const saved = { AI_COMMON_AGENTS: process.env.AI_COMMON_AGENTS,
    AI_PRIORITY_NOW_EPOCH: process.env.AI_PRIORITY_NOW_EPOCH };
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
  const options = { timeoutMs: 5000,
    extraEnv: { PATH: `${dir}:${process.env.PATH}`, CALLS: calls } };
  try {
    process.env.AI_COMMON_AGENTS = 'opencode-go:union-alpha,openrouter:stealth/union-alpha,opencode:fixture';
    for (const epoch of ['0', '1789650000', '1790254800', '2000000000']) {
      process.env.AI_PRIORITY_NOW_EPOCH = epoch;
      writeFileSync(calls, '');
      assert.equal(await runOpencodeText('fixture', 'offline', options), 'OK');
      assert.deepEqual(readFileSync(calls, 'utf8').trim().split('\n'), [
        'opencode-go/union-alpha', 'openrouter/stealth/union-alpha', 'opencode/fixture',
      ]);
    }
    writeFileSync(calls, '');
    assert.equal(await runOpencodeText('fixture', 'offline', {
      ...options, opencodeAgent: 'opencode:fixture',
    }), 'OK');
    assert.equal(readFileSync(calls, 'utf8'), 'opencode/fixture\n');
    process.env.AI_COMMON_AGENTS = 'opencode:fixture';
    writeFileSync(calls, '');
    assert.equal(await generateTextWithFallbacks('fixture', 'offline', {
      ...options, fallbackMode: 'opencode',
    }), 'OK');
    assert.equal(readFileSync(calls, 'utf8'), 'opencode/fixture\n');
    writeFileSync(calls, '');
    await assert.rejects(generateTextWithFallbacks('fixture', 'offline', {
      ...options, fallbackMode: 'opencode', includeOpencodeFallback: false,
    }));
    assert.equal(readFileSync(calls, 'utf8'), '');
  } finally {
    for (const [key, value] of Object.entries(saved)) {
      if (value === undefined) delete process.env[key];
      else process.env[key] = value;
    }
    rmSync(dir, { recursive: true, force: true });
  }
});
