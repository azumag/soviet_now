import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const improve = readFileSync(new URL('../soren91/improve.mjs', import.meta.url), 'utf8');
const runner = readFileSync(new URL('../soren91/run_player_loop.sh', import.meta.url), 'utf8');

test('retired Soren91 improvement tombstone cannot invoke models, git or strategy mutation', () => {
  assert.match(improve, /automatic strategy improvement has been retired/i);
  assert.doesNotMatch(improve, /\b(?:spawn|spawnSync|exec|execFile|execFileSync)\s*\(/);
  assert.doesNotMatch(improve, /\b(?:claude|gemini|opencode|gh)\b/i);
  assert.doesNotMatch(improve, /(?:writeFile|rename|copyFile|rmSync|unlink|createWriteStream)\s*\(/);
});

test('production runner cannot re-enable the legacy internal improvement path from env', () => {
  assert.match(runner, /SOREN91_EXTERNAL_IMPROVE=1 node main\.mjs/);
  assert.doesNotMatch(runner, /SOREN91_EXTERNAL_IMPROVE="\$\{SOREN91_EXTERNAL_IMPROVE:-1\}"/);
});
