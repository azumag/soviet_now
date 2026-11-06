import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';
import { fileURLToPath } from 'node:url';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const fixture = { active: { game: 'robots', phase: 'RUNNING', token: 'private' } };
const expected = { active: true, game: 'robots', phase: 'running' };

function probe(docichRoot, inputFile, marker) {
  const script = [
    "import { normalizeActiveGameContext, loadActiveGameContext } from './lib/shared_overlay.mjs';",
    "const fixture = JSON.parse(process.argv[1]);",
    "const normalized = normalizeActiveGameContext(fixture, 12);",
    "const loaded = loadActiveGameContext(process.argv[2]);",
    "process.stdout.write(JSON.stringify({ normalized, loaded }));",
  ].join('\n');
  const result = spawnSync(process.execPath,
    ['--input-type=module', '-e', script, JSON.stringify(fixture), inputFile],
    {
      cwd: ROOT, env: { ...process.env, DOCICH_ROOT: docichRoot, DOCICH_CONTEXT_DELEGATE_MARKER: marker },
      encoding: 'utf8', timeout: 15000,
    });
  assert.equal(result.status, 0, result.stderr);
  const { normalized, loaded } = JSON.parse(result.stdout);
  assert.deepEqual(normalized, { ...expected, updatedAt: 12 });
  assert.deepEqual(loaded, { ...expected, updatedAt: Math.floor(fs.statSync(inputFile).mtimeMs / 1000) });
}

test('docich game context delegates when contract matches; missing, invalid or stale versions stay standalone', () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'soren-context-delegate-'));
  const dir = path.join(root, 'overlays');
  const input = path.join(root, 'game_switch.json');
  const marker = path.join(root, 'delegation-marker');
  try {
    fs.mkdirSync(dir);
    fs.writeFileSync(input, JSON.stringify(fixture));
    probe(root, input, marker);
    assert.equal(fs.existsSync(marker), false);

    const moduleFile = path.join(dir, 'active_game_context.mjs');
    fs.writeFileSync(moduleFile, 'not valid JS!');
    probe(root, input, marker);
    fs.writeFileSync(moduleFile, 'export const ACTIVE_GAME_CONTEXT_CONTRACT_VERSION=0; export const normalizeActiveGameContext=()=>({}); export const loadActiveGameContext=()=>({});');
    probe(root, input, marker);
    fs.writeFileSync(moduleFile, 'export const ACTIVE_GAME_CONTEXT_CONTRACT_VERSION=1; export const normalizeActiveGameContext=()=>({}); export const loadActiveGameContext=()=>({});');
    probe(root, input, marker);

    const fakeModule = [
      "import fs from 'node:fs';",
      "export const ACTIVE_GAME_CONTEXT_CONTRACT_VERSION=1;",
      "function safeText(v, fallback='') {",
      "  const text=String(v??'').replace(/[\\u0000-\\u001f\\u007f]/g,' ').replace(/\\s+/g,' ').trim();",
      "  return text.slice(0,80)||fallback;",
      "}",
      "export function normalizeActiveGameContext(raw, at=0) {",
      "  fs.appendFileSync(process.env.DOCICH_CONTEXT_DELEGATE_MARKER,'n');",
      "  const d=raw && typeof raw==='object' && !Array.isArray(raw) ? raw : {};",
      "  const a=d.active;",
      "  const obj=a && typeof a==='object' && !Array.isArray(a) ? a : null;",
      "  const inactive=a===false || a===null;",
      "  const name=safeText(obj?.game ?? (typeof a==='string' ? a : undefined));",
      "  const phase=safeText(obj?.phase ?? d.phase, name ? 'active':'waiting').toLowerCase();",
      "  const ok=Boolean(name) && !inactive;",
      "  return {active:ok, game:ok?name:'', phase:ok?phase:'waiting',",
      "    updatedAt:Number.isSafeInteger(Number(at))?Number(at):0};",
      "}",
      "export function loadActiveGameContext(file) {",
      "  fs.appendFileSync(process.env.DOCICH_CONTEXT_DELEGATE_MARKER,'l');",
      "  const time=Math.floor(fs.statSync(file).mtimeMs/1000);",
      "  return normalizeActiveGameContext(JSON.parse(fs.readFileSync(file,'utf8')),time);",
      "}",
    ].join('\n');
    fs.writeFileSync(moduleFile, fakeModule);
    probe(root, input, marker);
    const actions = fs.readFileSync(marker, 'utf8');
    assert.match(actions, /l/, 'matching versioned docich loader must be selected');
    assert.ok(actions.length > 6, 'normalization must also be delegated after startup probes');
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});
