import assert from 'node:assert/strict';
import test from 'node:test';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {spawnSync} from 'node:child_process';
import {fileURLToPath} from 'node:url';

const ROOT=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..');

function probe(docichRoot,flags={}) {
  const script=[
    "import {loadDirectOverlayConfig} from './lib/direct_overlay.mjs';",
    'const flags=JSON.parse(process.env.OVERLAY_SELECTION_FLAGS);',
    "const config=loadDirectOverlayConfig({SOREN_STREAM_BACKEND:'ffmpeg',...flags},'linux');",
    'process.stdout.write(JSON.stringify({keys:config.surfaces.map(x=>x.key),',
    'broadcast:config.broadcast?.gameGapEnabled??null,docich:config.surfaces[0]?.__docich===true}));',
  ].join('');
  const result=spawnSync(process.execPath,['--input-type=module','-e',script],{
    cwd:ROOT,env:{...process.env,DOCICH_ROOT:docichRoot,
      OVERLAY_SELECTION_FLAGS:JSON.stringify(flags)},encoding:'utf8',timeout:12000,
  });
  assert.equal(result.status,0,result.stderr);
  return JSON.parse(result.stdout);
}

const dashboard=['broadcastSidebar','broadcastTop','broadcastGameGap','broadcastBottom','twica','wildcard','avsync'];
const fullscreen=['event','stats','ops','improve','wildcard','avsync','twica'];
const goodModule=`
export const DIRECT_OVERLAY_SURFACE_SELECTION_CONTRACT_VERSION=1;
export function selectDirectOverlaySurfaces({stage,env,broadcastSurfaces,twicaSurface,definitions}){
  const candidates=stage.enabled
    ? [...broadcastSurfaces,twicaSurface,...definitions.filter(x=>['wildcard','avsync'].includes(x.key))]
    : [...definitions,twicaSurface];
  const surfaces=JSON.parse(JSON.stringify(candidates));
  if(surfaces.length)Object.defineProperty(surfaces[0],'__docich',{value:true});
  return {surfaces,broadcastEnabled:surfaces.some(x=>x.region==='sidebar')};
}
`;

test('docich selection is accepted only for matching full ordered surfaces',()=>{
  const root=fs.mkdtempSync(path.join(os.tmpdir(),'soren-overlay-selection-'));
  try {
    assert.deepEqual(probe(root),{keys:dashboard,broadcast:true,docich:false});
    const folder=path.join(root,'overlays');fs.mkdirSync(folder);
    const modulePath=path.join(folder,'direct_overlay_surface_selection.mjs');
    fs.writeFileSync(modulePath,goodModule);
    assert.deepEqual(probe(root),{keys:dashboard,broadcast:true,docich:true});
    assert.deepEqual(probe(root,{SOREN_DIRECT_STAGE_LAYOUT:'fullscreen'}),{
      keys:fullscreen,broadcast:null,docich:true,
    });
    // A candidate that ignores legacy disable flags must be rejected.
    assert.deepEqual(probe(root,{SOREN_DIRECT_WILDCARD_OVERLAY_ENABLED:'0'}),{
      keys:dashboard.filter(x=>x!=='wildcard'),broadcast:true,docich:false,
    });
    fs.writeFileSync(modulePath,goodModule.replace('VERSION=1','VERSION=2'));
    assert.deepEqual(probe(root),{keys:dashboard,broadcast:true,docich:false});
    fs.writeFileSync(modulePath,goodModule.replace(
      'broadcastEnabled:surfaces.some(x=>x.region===\'sidebar\')',
      'broadcastEnabled:false'
    ));
    assert.deepEqual(probe(root),{keys:dashboard,broadcast:true,docich:false});
    fs.writeFileSync(modulePath,'this is invalid javascript!');
    assert.deepEqual(probe(root),{keys:dashboard,broadcast:true,docich:false});
    fs.writeFileSync(modulePath,'throw new Error("broken module")');
    assert.deepEqual(probe(root),{keys:dashboard,broadcast:true,docich:false});
  }finally{fs.rmSync(root,{recursive:true,force:true});}
});
