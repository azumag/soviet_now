import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const source = readFileSync(new URL('../soren91/text_ai.mjs', import.meta.url), 'utf8');

test('opencode text fallback uses util-linux script -c contract', () => {
  assert.match(
    source,
    /const scriptCommand = `bash -lc \$\{shellSingleQuote\(command\)\}`;/,
  );
  assert.match(
    source,
    /execFile\('script', \['-q', '-e', '-c', scriptCommand, rawFile\]/,
  );
  assert.doesNotMatch(
    source,
    /execFile\('script', \['-q', rawFile, 'bash', '-lc', command\]/,
  );
});
