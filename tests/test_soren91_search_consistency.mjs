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

function codeOnly(source) {
  let result = '';
  let state = 'code';
  let escaped = false;
  for (let i = 0; i < source.length; i += 1) {
    const ch = source[i];
    const next = source[i + 1];
    if (state === 'line') {
      result += ch === '\n' ? '\n' : ' ';
      if (ch === '\n') state = 'code';
      continue;
    }
    if (state === 'block') {
      result += ch === '\n' ? '\n' : ' ';
      if (ch === '*' && next === '/') {
        result += ' ';
        state = 'code';
        i += 1;
      }
      continue;
    }
    if (state === 'single' || state === 'double' || state === 'template') {
      result += ch === '\n' ? '\n' : ' ';
      if (escaped) { escaped = false; continue; }
      if (ch === '\\') { escaped = true; continue; }
      if ((state === 'single' && ch === "'")
          || (state === 'double' && ch === '"')
          || (state === 'template' && ch === '`')) state = 'code';
      continue;
    }
    if (ch === '/' && next === '/') { result += '  '; state = 'line'; i += 1; continue; }
    if (ch === '/' && next === '*') { result += '  '; state = 'block'; i += 1; continue; }
    if (ch === "'") { result += ' '; state = 'single'; continue; }
    if (ch === '"') { result += ' '; state = 'double'; continue; }
    if (ch === '`') { result += ' '; state = 'template'; continue; }
    result += ch;
  }
  return result;
}

function findUnpositionedAliases(decideCode) {
  const code = codeOnly(decideCode);
  const aliases = new Set();
  const assignments = [];
  const assignmentRe = /\b(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*([^;\n]+)/g;
  for (const match of code.matchAll(assignmentRe)) assignments.push({ name: match[1], expression: match[2] });

  let changed = true;
  while (changed) {
    changed = false;
    for (const { name, expression } of assignments) {
      if (aliases.has(name)) continue;
      const fromInterface = /\bboardState\s*\.\s*(?:next|nextPieces|hold)\b/.test(expression);
      const fromAlias = [...aliases].some(alias => new RegExp(`\\b${alias}\\b`).test(expression));
      if (fromInterface || fromAlias) {
        aliases.add(name);
        changed = true;
      }
    }
  }
  return { code, aliases };
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

test('decide never reads x/y from unpositioned next, queue, or HOLD pieces', () => {
  const source = readFileSync(STRATEGY, 'utf8');
  const decide = extractDecide(source);
  const { code, aliases } = findUnpositionedAliases(decide);

  assert.doesNotMatch(
    code,
    /\bboardState\s*\.\s*(?:next|hold)\s*(?:\?\.)?\s*(?:x|y)\b/,
    'next/hold are unpositioned and have no usable x/y',
  );
  assert.doesNotMatch(
    code,
    /\bboardState\s*\.\s*nextPieces\s*(?:\?\.)?\s*\[[^\]]+\]\s*(?:\?\.)?\s*(?:x|y)\b/,
    'nextPieces entries are unpositioned and have no usable x/y',
  );

  for (const alias of aliases) {
    assert.doesNotMatch(
      code,
      new RegExp(`\\b${alias}\\s*(?:\\?\\.)?\\s*(?:x|y)\\b`),
      `${alias} is derived from an unpositioned next/HOLD/queue piece and must not use x/y`,
    );
  }
});

test('daily improvement prompt preserves search/path consistency and piece positioning contract', () => {
  const prompt = readFileSync(PROMPT, 'utf8');
  assert.match(prompt, /Search-consistency contract/);
  assert.match(prompt, /MUST NOT bypass a `search\(\)` result/);
  assert.match(prompt, /SAME search path/);
  assert.match(prompt, /Never copy future metrics from one `search\(\)` root onto a different immediate root/);
  assert.match(prompt, /HOLD must be compared against the actual non-HOLD plan/);
  assert.match(prompt, /Do not replace it with an immediate one-ply override/);
  assert.match(prompt, /Positioned \/ unpositioned piece contract/);
  assert.match(prompt, /Only `boardState\.pieces\[\]` is positioned/);
  assert.match(prompt, /`boardState\.next`.*unpositioned/);
  assert.match(prompt, /`current = normalizePiece\(boardState\.next\)` is still unpositioned/);
  assert.match(prompt, /Do not invent a cursor X/);
});
