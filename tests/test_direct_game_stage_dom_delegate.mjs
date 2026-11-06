import assert from 'node:assert/strict';
import test from 'node:test';
import {spawnSync} from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {fileURLToPath} from 'node:url';

const ROOT=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..');
const SOURCE=fs.readFileSync(path.join(ROOT,'lib/direct_overlay.mjs'),'utf8');
const start=SOURCE.indexOf('function applyDirectGameStageDom(');
const end=SOURCE.indexOf('\n\n// Playwright serializes this function',start);
assert.ok(start>=0&&end>start);
const callback=SOURCE.slice(start,end).trimEnd();

function probe(docichRoot) {
  const script=[
    "import {DIRECT_GAME_STAGE_DOM_SOURCE,installDirectGameStage} from './lib/direct_overlay.mjs';",
    "let selected=null;let config=null;",
    "const page={async evaluate(fn,payload){selected=fn;config=payload;return 'delegated';}};",
    "const result=await installDirectGameStage(page,{stage:{enabled:true,mode:'dashboard'}},{drawBufferWidth:576,drawBufferHeight:324});",
    "process.stdout.write(JSON.stringify({owner:DIRECT_GAME_STAGE_DOM_SOURCE,result,"
    +"serializable:selected.toString().startsWith('function applyDirectGameStageDom('),"
    +"width:config.drawBufferWidth,height:config.drawBufferHeight}));",
  ].join('');
  const result=spawnSync(process.execPath,['--input-type=module','-e',script],{
    cwd:ROOT,env:{...process.env,DOCICH_ROOT:docichRoot},encoding:'utf8',timeout:12000,
  });
  assert.equal(result.status,0,result.stderr);
  return JSON.parse(result.stdout);
}
function fileSource(body=callback,version=1) {
  return 'export const DIRECT_GAME_STAGE_DOM_CONTRACT_VERSION = '+version+';\nexport '+body+'\n';
}
const expected=(owner)=>({owner,result:'delegated',serializable:true,width:576,height:324});

test('browser stage is delegated only to byte-matched docich callback; missing/bad/old module is safe',()=>{
  const root=fs.mkdtempSync(path.join(os.tmpdir(),'docich-stage-owner-'));
  try{
    assert.deepEqual(probe(root),expected('soren-fallback'));
    const folder=path.join(root,'overlays');fs.mkdirSync(folder);
    const file=path.join(folder,'direct_game_stage_dom.mjs');
    fs.writeFileSync(file,fileSource());
    assert.deepEqual(probe(root),expected('docich'));
    fs.writeFileSync(file,fileSource(callback.replace(
      'function applyDirectGameStageDom(',
      'function applyDirectGameStageDom( /* altered */ '
    )));
    assert.deepEqual(probe(root),expected('soren-fallback'));
    fs.writeFileSync(file,fileSource(callback,2));
    assert.deepEqual(probe(root),expected('soren-fallback'));
    fs.writeFileSync(file,'export const DIRECT_GAME_STAGE_DOM_CONTRACT_VERSION = 1;\nexport function unrelated() {}\n');
    assert.deepEqual(probe(root),expected('soren-fallback'));
    fs.writeFileSync(file,'not valid JS');
    assert.deepEqual(probe(root),expected('soren-fallback'));
    fs.writeFileSync(file,'throw new Error("stage module broken")');
    assert.deepEqual(probe(root),expected('soren-fallback'));
  }finally{fs.rmSync(root,{recursive:true,force:true});}
});

test('disabled stage does not inject page callback',async()=>{
  const script="import {installDirectGameStage} from './lib/direct_overlay.mjs';"
    +"const page={async evaluate(){throw Error('must not run');}};"
    +"process.stdout.write(JSON.stringify(await installDirectGameStage(page,{enabled:false})));";
  const res=spawnSync(process.execPath,['--input-type=module','-e',script],{
    cwd:ROOT,env:{...process.env,DOCICH_ROOT:'/missing/docich-root'},encoding:'utf8',timeout:12000,
  });
  assert.equal(res.status,0,res.stderr);
  assert.deepEqual(JSON.parse(res.stdout),{stageMode:'fullscreen'});
});
