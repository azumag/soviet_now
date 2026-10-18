import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import vm from 'node:vm';
import { EventEmitter } from 'node:events';
import { installTwicaLegacyOwner, readLegacyPolicy } from '../lib/twica_legacy_owner.mjs';

function fixture() {
  const page = new EventEmitter();
  const frames = new Map();
  const document = {
    body: { appendChild(frame) { frames.set(frame.id, frame); } },
    getElementById(id) { return frames.get(id); },
    createElement() { return { style: {}, dataset: {}, setAttribute(){}, remove(){frames.delete(this.id);} }; },
  };
  const window = {}; window.top=window;
  page.isClosed=()=>false;
  page.evaluate=async (fn,payload)=>vm.runInNewContext(`(${fn.toString()})(payload)`,{window,document,payload});
  return {page,frames};
}
const item={srcUrl:'http://127.0.0.1:18080/overlay/x',title:'TwiCa',style:{transform:'translateX(calc(100vw / 3))'}};
const pause=ms=>new Promise(resolve=>setTimeout(resolve,ms));
function write(dir, owner, gen='a'.repeat(32), role='game'){
  fs.writeFileSync(path.join(dir,'control.json'),JSON.stringify({schema:1,owner,generation:gen,legacy_role:role}),{mode:0o600});
}

test('invalid policy and deleted managed policy never grant fallback',()=>{
  const dir=fs.mkdtempSync(path.join(os.tmpdir(),'twica-policy-'));
  try {
    assert.equal(readLegacyPolicy(dir).owner,'legacy');
    assert.equal(readLegacyPolicy(dir,true).owner,'none');
    write(dir,'common');assert.equal(readLegacyPolicy(dir).owner,'common');
    fs.writeFileSync(path.join(dir,'control.json'),'bad');assert.equal(readLegacyPolicy(dir).owner,'none');
    fs.unlinkSync(path.join(dir,'control.json'));
    fs.symlinkSync(path.join(dir,'missing'),path.join(dir,'control.json'));
    assert.equal(readLegacyPolicy(dir).owner,'none');
    assert.equal(readLegacyPolicy(dir,true).owner,'none');
  } finally {fs.rmSync(dir,{recursive:true,force:true});}
});

test('common removes legacy subscriptions and navigation cannot recreate them',async()=>{
  const dir=fs.mkdtempSync(path.join(os.tmpdir(),'twica-legacy-'));
  const {page,frames}=fixture();
  write(dir,'legacy');
  const guard=await installTwicaLegacyOwner(page,item,{directory:dir,role:'game',intervalMs:10});
  try {
    assert.equal(frames.size,1);
    assert.equal(await installTwicaLegacyOwner(page,item,{directory:dir}),guard);
    write(dir,'none','b'.repeat(32));await pause(60);assert.equal(frames.size,0);
    write(dir,'common','c'.repeat(32));await pause(60);assert.equal(frames.size,0);
    frames.clear();await pause(60);assert.equal(frames.size,0);
    const mark=fs.readdirSync(dir).find(name=>name.startsWith('legacy-'));
    const report=JSON.parse(fs.readFileSync(path.join(dir,mark),'utf8'));
    assert.equal(report.generation,'c'.repeat(32));assert.equal(report.subscribed,false);
    write(dir,'legacy','d'.repeat(32),'shared');await pause(60);assert.equal(frames.size,0);
    write(dir,'legacy','e'.repeat(32),'game');await pause(60);assert.equal(frames.size,1);
    page.emit('close');await pause(20);
    assert.equal(fs.readdirSync(dir).filter(name=>name.startsWith('legacy-')).length,0);
  } finally {guard.close();fs.rmSync(dir,{recursive:true,force:true});}
});

test('both real roles acknowledge quiescence without two rollback owners',async()=>{
  const dir=fs.mkdtempSync(path.join(os.tmpdir(),'twica-roles-'));
  const game=fixture(), shared=fixture();
  write(dir,'legacy','a'.repeat(32),'game');
  const g=await installTwicaLegacyOwner(game.page,item,{directory:dir,role:'game',intervalMs:10});
  const s=await installTwicaLegacyOwner(shared.page,item,{directory:dir,role:'shared',intervalMs:10});
  try {
    assert.equal(game.frames.size+shared.frames.size,1);
    write(dir,'common','b'.repeat(32));await pause(60);
    assert.equal(game.frames.size+shared.frames.size,0);
    write(dir,'legacy','c'.repeat(32),'shared');await pause(60);
    assert.equal(game.frames.size,0);assert.equal(shared.frames.size,1);
  } finally {g.close();s.close();fs.rmSync(dir,{recursive:true,force:true});}
});
