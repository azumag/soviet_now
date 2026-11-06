import assert from 'node:assert/strict';
import test from 'node:test';
import {spawnSync} from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {fileURLToPath} from 'node:url';

const ROOT=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..');
function probe(root){
  const script=[
    "import {directOverlaySurfaceVisible} from './lib/direct_overlay.mjs';",
    "import fs from 'node:fs';import path from 'node:path';",
    "const root=process.env.DOCICH_ROOT;",
    "const wildcard=path.join(root,'wildcard.json');",
    "const improve=path.join(root,'improve.json');",
    "const inputs=[{visibility:'always'},",
    "{visibility:'dashboard',suppressStateFile:wildcard},",
    "{visibility:'wildcard',visibilityStateFile:wildcard},",
    "{visibility:'improve',visibilityStateFile:improve,suppressStateFile:wildcard}];",
    "const phases=['idle','running','generating'];const values=[];",
    "fs.writeFileSync(improve,JSON.stringify({status:'RUNNING'}));",
    "for(const phase of phases){fs.writeFileSync(wildcard,JSON.stringify({phase}));values.push(inputs.map(directOverlaySurfaceVisible));}",
    "fs.writeFileSync(wildcard,'{invalid');values.push(inputs.map(directOverlaySurfaceVisible));",
    "process.stdout.write(JSON.stringify(values));",
  ].join('\\n');
  const p=spawnSync(process.execPath,['--input-type=module','-e',script],{
    cwd:ROOT,env:{...process.env,DOCICH_ROOT:root},encoding:'utf8',timeout:12000,
  });
  assert.equal(p.status,0,p.stderr);
  return JSON.parse(p.stdout);
}

const expected=[[true,true,false,true],[true,false,true,false],[true,false,true,false],[true,true,false,true]];

test('docich visibility delegation preserves local wildcard and improvement priority',()=>{
  const root=fs.mkdtempSync(path.join(os.tmpdir(),'soren-vis-'));
  try{
    assert.deepEqual(probe(root),expected);
    const overlays=path.join(root,'overlays');fs.mkdirSync(overlays);
    const moduleFile=path.join(overlays,'direct_overlay_visibility.mjs');
    const marker=path.join(root,'delegated');
    const compatible=[
      "import fs from 'node:fs';",
      "export const DIRECT_OVERLAY_VISIBILITY_CONTRACT_VERSION = 1;",
      'export function directOverlaySurfaceVisible(item) {',
      '  fs.writeFileSync('+JSON.stringify(marker)+',"called");',
      "  if(!item||item.visibility==='always')return true;",
      "  if(item.visibility==='dashboard')return false;",
      "  if(item.visibility==='wildcard')return true;",
      "  return false;",
      '}',
    ].join('\\n');
    fs.writeFileSync(moduleFile,compatible);
    assert.deepEqual(probe(root),expected);
    assert.equal(fs.readFileSync(marker,'utf8'),'called');
    // The candidate disagrees for idle dashboard, but the final decision
    // remains the proven local result rather than the drifting module.
    fs.writeFileSync(moduleFile,compatible.replace('= 1;', '= 2;'));
    fs.rmSync(marker);
    assert.deepEqual(probe(root),expected);
    assert.equal(fs.existsSync(marker),false);
    fs.writeFileSync(moduleFile,'not valid syntax');
    assert.deepEqual(probe(root),expected);
    fs.writeFileSync(moduleFile, 'throw new Error("bad import")');
    assert.deepEqual(probe(root),expected);
  }finally{fs.rmSync(root,{recursive:true,force:true});}
});
