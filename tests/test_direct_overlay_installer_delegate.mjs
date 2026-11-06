import assert from 'node:assert/strict';
import test from 'node:test';
import { spawnSync } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const SOURCE = fs.readFileSync(path.join(ROOT, 'lib/direct_overlay.mjs'), 'utf8');
const callbackStart = SOURCE.indexOf('function overlayInstaller(surfaces) {');
const callbackEnd = SOURCE.indexOf('\n\n// #3553:', callbackStart);
assert.ok(callbackStart > 0 && callbackEnd > callbackStart);
const callback = SOURCE.slice(callbackStart, callbackEnd).trimEnd();

function probe(docichRoot) {
  const script = [
    "import {DIRECT_OVERLAY_BROWSER_INSTALLER_SOURCE,installDirectOverlay} from './lib/direct_overlay.mjs';",
    'let injected=null;let evaluated=null;',
    'const page={async addInitScript(fn){injected=fn;},async evaluate(fn){evaluated=fn;}};',
    "await installDirectOverlay(page,{enabled:true,surfaces:[{key:'event',elementId:'event',route:'/event'}]});",
    "process.stdout.write(JSON.stringify({owner:DIRECT_OVERLAY_BROWSER_INSTALLER_SOURCE,"
      +"sameCallback:injected===evaluated,serialized:injected?.toString()?.includes('frame.dataset.sorenExternalOverlay')}));",
  ].join('\n');
  const result = spawnSync(process.execPath, ['--input-type=module', '-e', script], {
    cwd: ROOT, env: { ...process.env, DOCICH_ROOT: docichRoot },
    encoding:'utf8',timeout:12000,
  });
  assert.equal(result.status, 0, result.stderr);
  return JSON.parse(result.stdout);
}

function moduleSource(body=callback, version=1) {
  return [
    'export const BROWSER_OVERLAY_INSTALLER_CONTRACT_VERSION = '+version+';',
    'export '+body,
    '',
  ].join('\n');
}

test('docich callback is chosen only for an exact, versioned, self-contained function',()=>{
  const root=fs.mkdtempSync(path.join(os.tmpdir(),'docich-browser-owner-'));
  try {
    const missing=probe(root);
    assert.deepEqual(missing, {owner:'soren-fallback',sameCallback:true,serialized:true});
    const folder=path.join(root,'overlays');
    fs.mkdirSync(folder);
    const file=path.join(folder,'browser_overlay_installer.mjs');
    fs.writeFileSync(file,moduleSource());
    assert.deepEqual(probe(root),{owner:'docich',sameCallback:true,serialized:true});

    // Even a benign source drift must be reviewed in both repositories.
    fs.writeFileSync(file,moduleSource(callback.replace(
      'function overlayInstaller(surfaces) {',
      'function overlayInstaller(surfaces) { /* changed independently */'
    )));
    assert.equal(probe(root).owner,'soren-fallback');

    fs.writeFileSync(file,moduleSource(callback,2));
    assert.equal(probe(root).owner,'soren-fallback');

    fs.writeFileSync(file,[
      'export const BROWSER_OVERLAY_INSTALLER_CONTRACT_VERSION = 1;',
      'export function notOverlayInstaller() {}',
    ].join('\n'));
    assert.equal(probe(root).owner,'soren-fallback');

    fs.writeFileSync(file,'not valid javascript!');
    assert.equal(probe(root).owner,'soren-fallback');

    fs.writeFileSync(file,'throw new Error("startup failed");');
    assert.equal(probe(root).owner,'soren-fallback');
  } finally {
    fs.rmSync(root,{recursive:true,force:true});
  }
});
