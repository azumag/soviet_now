import test from 'node:test';
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const STAGE = {
  enabled: true, mode: 'dashboard', elementId: 'soren-direct-stream-stage',
  outputWidth: 1280, outputHeight: 720,
  gameLeft: 0, gameTop: 90, gameWidth: 960, gameHeight: 540,
  sidebarLeft: 960, sidebarWidth: 320,
  topRailHeight: 90, bottomRailTop: 630, bottomRailHeight: 90,
};

function probe(root) {
  const script = [
    "import {loadDirectStageConfig} from './lib/direct_overlay.mjs';",
    "const env={SOREN_STREAM_BACKEND:'ffmpeg',SOREN_DIRECT_OVERLAY_ENABLED:'1'};",
    "const stage=loadDirectStageConfig(env,'linux');",
    "let rejectsBad=false;",
    "try {loadDirectStageConfig({...env,SOREN_DIRECT_GAME_DISPLAY_SIZE:'1280x720'},'linux');}",
    "catch(err){rejectsBad=/leave room/.test(String(err));}",
    "process.stdout.write(JSON.stringify({stage,delegated:stage.__docichSource===true,rejectsBad}));",
  ].join('\n');
  const result = spawnSync(process.execPath, ['--input-type=module', '-e', script], {
    cwd: ROOT, env: {...process.env, DOCICH_ROOT: root},
    encoding: 'utf8', timeout: 12000,
  });
  assert.equal(result.status, 0, result.stderr);
  return JSON.parse(result.stdout);
}

function fake(body, version = 1) {
  return [
    'export const DIRECT_STAGE_CONTRACT_VERSION = '+version+';',
    "export const DIRECT_STAGE_ELEMENT_ID = 'soren-direct-stream-stage';",
    'export function loadDirectStageConfig(env, platform) { '+body+' }',
  ].join('\n');
}

test('docich versioned stage math is preferred only with identical layout', () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'stage-docich-'));
  try {
    const absent = probe(root);
    assert.deepEqual(absent.stage, STAGE);
    assert.equal(absent.delegated, false);
    assert.equal(absent.rejectsBad, true);

    const folder = path.join(root, 'overlays');
    fs.mkdirSync(folder);
    const file = path.join(folder, 'direct_stage_config.mjs');
    // Non-enumerable marker proves the exact docich object was selected
    // without changing any live geometry or JSON-facing contract.
    fs.writeFileSync(file, fake(
      'const stage='+JSON.stringify(STAGE)+';'
      +'Object.defineProperty(stage,"__docichSource",{value:true});return stage;'
    ));
    const valid = probe(root);
    assert.deepEqual(valid.stage, STAGE);
    assert.equal(valid.delegated, true);
    assert.equal(valid.rejectsBad, true);

    fs.writeFileSync(file, fake('return {...'+JSON.stringify(STAGE)+',gameWidth:1234};'));
    const drift = probe(root);
    assert.deepEqual(drift.stage, STAGE);
    assert.equal(drift.delegated, false);

    fs.writeFileSync(file, fake('throw new Error("broken");'));
    assert.deepEqual(probe(root).stage, STAGE);

    fs.writeFileSync(file, fake('return '+JSON.stringify(STAGE)+';', 2));
    assert.equal(probe(root).delegated, false);

    fs.writeFileSync(file, 'not valid javascript!');
    assert.deepEqual(probe(root).stage, STAGE);
  } finally {
    fs.rmSync(root, {recursive:true, force:true});
  }
});
