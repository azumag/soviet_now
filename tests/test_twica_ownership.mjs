import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import vm from 'node:vm';
import { ownershipConfig, readOwnership, installTwicaOwnership,
         twicaGuardHealth, twicaGuardBrowser } from '../lib/twica_ownership.mjs';

const fixture = () => fs.mkdtempSync(path.join(os.tmpdir(), 'twica-owner-'));
function record(dir, mode, generation='a'.repeat(32)) {
  fs.writeFileSync(path.join(dir, 'owner.json'), JSON.stringify({protocol:1,mode,generation}), {mode:0o600});
}
test('ownership is opt-in; malformed and symlink records never grant a consumer', () => {
  const d = fixture();
  try {
    assert.equal(ownershipConfig({}).enabled, false);
    assert.equal(ownershipConfig({DOCICH_TWICA_COMMON_ENABLED:'1'}).enabled, true);
    assert.equal(readOwnership(d).mode,'legacy');
    record(d,'common'); assert.equal(readOwnership(d).mode,'common');
    fs.writeFileSync(path.join(d,'owner.json'), '{');
    assert.equal(readOwnership(d).mode,'blocked');
    fs.unlinkSync(path.join(d,'owner.json'));
    fs.symlinkSync('/dev/null',path.join(d,'owner.json'));
    assert.equal(readOwnership(d).mode,'blocked');
  } finally { fs.rmSync(d,{recursive:true,force:true}); }
});
test('binding accepts top-frame bounded ACK metadata only and unregisters on close', async () => {
  const d=fixture(); const frame={}; let callback, close; let installs=0;
  const page={mainFrame:()=>frame, exposeBinding:async (_n,cb)=>{callback=cb;},
    addInitScript:async()=>installs++,evaluate:async()=>{},once:(_n,cb)=>{close=cb;}};
  const config={twicaOwnership:{enabled:true,directory:d},surfaces:[{key:'twica',elementId:'t',srcUrl:'http://127.0.0.1/t',style:{}}]};
  try {
    await installTwicaOwnership(page,config);
    await installTwicaOwnership(page,config);
    assert.equal(installs,1); assert.equal(twicaGuardHealth().guard_ready,false);
    record(d,'draining');
    const choice=callback({frame},null);
    assert.throws(()=>callback({frame:{}},null));
    callback({frame},{generation:choice.generation,state:'retired',frames:0,arbitrary:'must-not-persist'});
    assert.equal(twicaGuardHealth().guard_ready,true);
    const filename=fs.readdirSync(path.join(d,'consumers'))[0];
    const saved=JSON.parse(fs.readFileSync(path.join(d,'consumers',filename)));
    assert.equal(saved.arbitrary,undefined); assert.equal(saved.frames,0);
    callback({frame},{generation:'b'.repeat(32),state:'legacy',frames:2});
    assert.deepEqual(JSON.parse(fs.readFileSync(path.join(d,'consumers',filename))),saved);
    close(); assert.equal(twicaGuardHealth().guards,0);
  } finally { fs.rmSync(d,{recursive:true,force:true}); }
});
test('browser retirement removes iframe rather than hiding it; rollback installs once', async () => {
  let choice={mode:'legacy',generation:'a'.repeat(32)}; const nodes=new Map(), timers=[]; const acknowledgements=[];
  const window={};window.top=window;window.owner=async ack=>{if(ack)acknowledgements.push(ack);return choice;};
  const document={readyState:'complete',getElementById:id=>nodes.get(id),body:{appendChild:n=>nodes.set(n.id,n)},
    createElement:()=>({style:{},dataset:{},setAttribute(){},remove(){nodes.delete(this.id);}})};
  const context={window,document,setTimeout:cb=>timers.push(cb),clearTimeout(){},payload:{binding:'owner',surface:{elementId:'twica',srcUrl:'http://127.0.0.1/overlay/test',style:{transform:'translateX(calc(100vw / 3))'}}}};
  const settle=()=>new Promise(resolve=>setImmediate(resolve));
  vm.runInNewContext(`(${twicaGuardBrowser.toString()})(payload)`,context);
  await settle(); assert.equal(nodes.size,1);const original=nodes.get('twica');
  choice={mode:'draining',generation:'b'.repeat(32)};timers.shift()();await settle();
  assert.equal(nodes.size,0);assert.equal(original.src,'about:blank');
  assert.equal(acknowledgements.at(-1).state,'retired');
  choice={mode:'common',generation:'c'.repeat(32)};timers.shift()();await settle();assert.equal(nodes.size,0);
  choice={mode:'legacy',generation:'d'.repeat(32)};timers.shift()();await settle();assert.equal(nodes.size,1);
  timers.shift()();await settle();assert.equal(nodes.size,1);
  window.__docichTwicaOwnerGuard.stop(); assert.equal(nodes.size,0);
});
test('normal and shared installers delegate only TwiCa, preserving proxy metadata and default behavior', () => {
  const source=fs.readFileSync(new URL('../lib/direct_overlay.mjs',import.meta.url),'utf8');
  assert.match(source,/twicaOwnership: ownershipConfig\(env\)/);
  assert.match(source,/item\.key !== 'twica'/);
  assert.match(source,/installTwicaOwnership\(page, config\)/);
  const proxy=fs.readFileSync(new URL('../lib/twica_overlay_proxy.mjs',import.meta.url),'utf8');
  assert.match(proxy,/__docich_twica_guard_v1/);
  const shell=fs.readFileSync(new URL('../direct_stream.sh',import.meta.url),'utf8');
  assert.match(shell,/docich\.twica_stream --runner/);
  assert.match(shell,/exec python3 "\$SCRIPT_DIR\/lib\/direct_stream.py" "\$@"/);
});
