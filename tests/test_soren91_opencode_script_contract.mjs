import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { DEFAULT_OPENCODE_PER_MODEL_TIMEOUT_MS, OPENCODE_TIMEOUT_COOLDOWN_MS, isOpencodeTimeout, resolvePerModelTimeoutMs } from '../soren91/text_ai.mjs';

const source = readFileSync(new URL('../soren91/text_ai.mjs', import.meta.url), 'utf8');

test('opencode text fallback uses stdin and preserves caller PATH', () => {
  assert.match(
    source,
    /opencode run --model \$\{shellSingleQuote\(model\)\} < \$\{shellSingleQuote\(promptFile\)\} 2>&1/,
  );
  assert.match(
    source,
    /const scriptCommand = `bash -c \$\{shellSingleQuote\(command\)\}`;/,
  );
  assert.match(
    source,
    /execFile\('script', \['-q', '-e', '-c', scriptCommand, rawFile\]/,
  );
  assert.doesNotMatch(source, /"\$\(cat \$\{shellSingleQuote\(promptFile\)\}\)"/);
  assert.doesNotMatch(source, /const scriptCommand = `bash -lc /);
});

test('opencode non-zero exit rejects even when partial output exists', () => {
  assert.match(source, /if \(err\) return reject\(err\);/);
  assert.doesNotMatch(source, /if \(err && !cleaned\) return reject\(err\);/);
});

test('each opencode model attempt is bounded so a hang cannot eat the chain', () => {
  // 2026-09-16: the free entries hung for the full 180s chain budget on the VM
  // (opencode run never returned), so comments never reached a working model.
  assert.equal(DEFAULT_OPENCODE_PER_MODEL_TIMEOUT_MS, 45_000);
  assert.equal(resolvePerModelTimeoutMs(180_000, undefined), 45_000);
  assert.equal(resolvePerModelTimeoutMs(180_000, 90_000), 90_000);
  assert.equal(resolvePerModelTimeoutMs(30_000, 90_000), 30_000);
  assert.equal(resolvePerModelTimeoutMs(180_000, 1_000), 5_000);
  assert.match(source, /timeoutMs: perModelTimeoutMs/);
});

test('a model that times out is cooled down instead of retried every comment', () => {
  assert.equal(OPENCODE_TIMEOUT_COOLDOWN_MS, 600_000);
  assert.equal(isOpencodeTimeout({ killed: true }), true);
  assert.equal(isOpencodeTimeout({ signal: 'SIGTERM' }), true);
  assert.equal(isOpencodeTimeout({ message: 'Command failed: timed out' }), true);
  assert.equal(isOpencodeTimeout({ message: 'provider failure' }), false);
  assert.equal(isOpencodeTimeout(null), false);
  assert.match(source, /opencode model skipped \(timed out recently\)/);
});
