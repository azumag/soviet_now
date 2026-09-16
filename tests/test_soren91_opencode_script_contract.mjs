import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import {
  DEFAULT_OPENCODE_PER_MODEL_TIMEOUT_MS,
  OPENCODE_TIMEOUT_COOLDOWN_MS,
  extractOpencodeJsonText,
  isOpencodeTimeout,
  resolvePerModelTimeoutMs,
} from '../soren91/text_ai.mjs';

const source = readFileSync(new URL('../soren91/text_ai.mjs', import.meta.url), 'utf8');

test('opencode text fallback uses direct stdin JSON transport without a TTY shell', () => {
  assert.match(
    source,
    /execFile\('opencode', \['run', '--format', 'json', '--model', model\]/,
  );
  assert.match(source, /child\.stdin\.write\(promptText\);/);
  assert.doesNotMatch(source, /execFile\('script'/);
  assert.doesNotMatch(source, /bash -[lc]/);
  assert.doesNotMatch(source, /"\$\(cat /);
});

test('opencode JSON transport joins only text events and rejects tool lifecycle', () => {
  const raw = [
    JSON.stringify({ type: 'step_start', part: { type: 'step-start' } }),
    JSON.stringify({ type: 'text', part: { type: 'text', text: 'hello ' } }),
    JSON.stringify({ type: 'text', part: { type: 'text', text: 'world' } }),
    JSON.stringify({ type: 'step_finish', part: { type: 'step-finish' } }),
  ].join('\n');
  assert.equal(extractOpencodeJsonText(raw), 'hello world');
  assert.throws(
    () => extractOpencodeJsonText(JSON.stringify({
      type: 'step_finish',
      part: { type: 'step-finish', reason: 'tool-calls', tool: 'read' },
    })),
    /tool\/error event/,
  );
  assert.throws(() => extractOpencodeJsonText('not-json'), /invalid JSON event/);
});

test('opencode non-zero exit rejects even when partial output exists', () => {
  assert.match(source, /if \(err\) return reject\(err\);/);
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
