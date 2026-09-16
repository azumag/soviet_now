import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';

const ROOT = new URL('..', import.meta.url).pathname;
const STRATEGY = join(ROOT, 'soren91', 'strategy.mjs');
const PROMPT = join(ROOT, 'soren91', 'prompts', 'improve_strategy.md');
const DECIDE_SIGNATURE = 'export function decide(boardState)';
const DIRECT_SEARCH_BYPASS = /\b(?:candidates|evaluate|simulateDrop|compareMove)\s*\(/g;

function extractDecide(source) {
  const start = source.indexOf(DECIDE_SIGNATURE);
  assert.ok(start >= 0, 'strategy must export decide(boardState)');
  assert.equal(source.indexOf(DECIDE_SIGNATURE, start + DECIDE_SIGNATURE.length), -1, 'strategy must have one decide');
  const braceStart = source.indexOf('{', start + DECIDE_SIGNATURE.length);
  assert.ok(braceStart >= 0, 'decide body must start');

  let depth = 0;
  let state = 'code';
  let escaped = false;
  for (let i = braceStart; i < source.length; i += 1) {
    const ch = source[i];
    const next = source[i + 1];
    if (state === 'line') {
      if (ch === '\n') state = 'code';
      continue;
    }
    if (state === 'block') {
      if (ch === '*' && next === '/') {
        state = 'code';
        i += 1;
      }
      continue;
    }
    if (state === 'single' || state === 'double' || state === 'template') {
      if (escaped) {
        escaped = false;
        continue;
      }
      if (ch === '\\') {
        escaped = true;
        continue;
      }
      if ((state === 'single' && ch === "'")
          || (state === 'double' && ch === '"')
          || (state === 'template' && ch === '`')) state = 'code';
      continue;
    }
    if (ch === '/' && next === '/') { state = 'line'; i += 1; continue; }
    if (ch === '/' && next === '*') { state = 'block'; i += 1; continue; }
    if (ch === "'") { state = 'single'; continue; }
    if (ch === '"') { state = 'double'; continue; }
    if (ch === '`') { state = 'template'; continue; }
    if (ch === '{') depth += 1;
    if (ch === '}') {
      depth -= 1;
      if (depth === 0) return source.slice(start, i + 1);
      assert.ok(depth >= 0, 'decide braces must remain balanced');
    }
  }
  assert.fail('decide body must close');
}

test('decide keeps drop enumeration inside multi-ply search', () => {
  const source = readFileSync(STRATEGY, 'utf8');
  const decide = extractDecide(source);
  assert.match(decide, /\bsearch\s*\(/, 'decide should select gameplay plans through search()');
  const bypasses = [...decide.matchAll(DIRECT_SEARCH_BYPASS)].map(match => match[0]);
  assert.deepEqual(
    bypasses,
    [],
    'decide must not bypass search() with immediate candidate/evaluate/simulation re-ranking',
  );
});

test('daily improvement prompt preserves search/path consistency', () => {
  const prompt = readFileSync(PROMPT, 'utf8');
  assert.match(prompt, /Search-consistency contract/);
  assert.match(prompt, /MUST NOT bypass a `search\(\)` result/);
  assert.match(prompt, /SAME search path/);
  assert.match(prompt, /Never copy future metrics from one `search\(\)` root onto a different immediate root/);
  assert.match(prompt, /HOLD must be compared against the actual non-HOLD plan/);
  assert.match(prompt, /Do not replace it with an immediate one-ply override/);
});
