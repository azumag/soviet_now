import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import http from 'node:http';
import {fileURLToPath} from 'node:url';

const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..');
const artifacts=process.env.SOREN_OVERLAY_ARTIFACT_DIR;
const enabled=process.env.SOREN_OVERLAY_BROWSER_TESTS==='1';
const source=fs.readFileSync(path.join(root,'tests/fixtures/soren-monitor.txt'),'utf8');
let revision=0;
function fixture(kind) {
  const now=Math.floor(Date.now()/1000);
  let text=source.replace('strategy 3a7b11ee',`strategy 3a7b11ee / fixture ${++revision}`).replace('  Score Timeline',
    '┌──────────────────────────────────────────┐\n│ AI 429 main-muse-spark-1.3-contributor-free(5h18m) │\n└──────────────────────────────────────────┘\n  Score Timeline');
  if(kind!=='short') text=text.replace('  ChatObs OK / Radio ON / game 23\n  Anchor 3a7b11ee / Branch e815cb22\n  LastStep +240 / acceptance=0.72',
    '  ChatObs Shobon Ranking: (-_-;) 1059日 失望 第3位 / 難しい試合の結果を確認しています\n'
    +'  AnnealObs 729393be p=0.96 gap=46 temp=1300 246h observe-only\n'
    +'  WildStreak n=1 eff=1 last=none sc=1.0 escape_ai in 3');
  if(['dense','crowded','many'].includes(kind)) text=text.replace('Observer Status',Array.from({length:kind==='many'?80:28},(_,i)=>`  Candidate-${i+1} hash${i} n=24/t=60 comp=3722 p50=4145 p25=2822`).join('\n')+'\nObserver Status');
  const segments=text.split('\n').map(line=>line.startsWith('│ AI ')?[
    {t:'│ ',c:'#67e8f9'},{t:line.slice(2,-2),c:'#e7bf75'},{t:' │',c:'#67e8f9'}]:[{t:line}]);
  return {version:1,updatedAt:now,feeds:{showStatusG:{text,segments,updatedAt:now,lineCount:text.split('\n').length},
    showStatus:{text:'● Backend FFMPEG LIVE\nGame: soren\nChat: RUNNING\nAudio: READY\nYouTube: connected\nKick: connected'+(kind==='crowded'?'\n予想対象：#23｜終了まで20秒\n#23：今回の予想対象':''),updatedAt:now}},
    notifications:{events:[],generators:[],work:{active:false}}};
}

test('Soren keeps telemetry stable while observer and health crossfade in a fixed slot', {skip:!enabled},async()=>{
  const {chromium}=await import('playwright');
  let state=fixture('short'),updates=0;
  const html=fs.readFileSync(path.join(root,'overlays/direct_broadcast_overlay.html'),'utf8');
  const server=http.createServer((req,res)=>{
    if(req.url==='/__soren_overlay/broadcast/state'){
      const payload=structuredClone(state);
      // Both data cards update continuously while the same outer elements fade.
      for(const feed of Object.values(payload.feeds))feed.updatedAt+=++updates/100;
      res.setHeader('Content-Type','application/json');res.end(JSON.stringify(payload));return;
    }
    res.setHeader('Content-Type','text/html; charset=utf-8');
    res.end(req.url==='/crop'?'<body style="margin:0"><iframe data-soren-overlay-region="sidebar" src="/overlay" style="border:0;width:320px;height:720px"></iframe></body>':html);
  });
  await new Promise(r=>server.listen(0,'127.0.0.1',r));
  const browser=await chromium.launch({headless:true});
  const page=await browser.newPage({viewport:{width:320,height:720}});
  const evidence=[];
  if(artifacts)fs.mkdirSync(artifacts,{recursive:true});
  try{
    await page.clock.install();
    await page.goto(`http://127.0.0.1:${server.address().port}/crop`);
    const frame=page.frames().find(f=>f.url().endsWith('/overlay'));
    await frame.waitForFunction(()=>document.querySelector('.monitor-viewport'));
    await page.clock.fastForward(100);
    await frame.evaluate(()=>{window.healthSlot=document.getElementById('feed-s');window.observerSlot=document.getElementById('monitor-observer');});
    const capture=async name=>{
      const result=await frame.evaluate(()=>{
        const rect=n=>{const r=n.getBoundingClientRect();return {x:r.x,y:r.y,width:r.width,height:r.height,bottom:r.bottom};};
        const root=document.querySelector('.soren-monitor'),health=document.getElementById('feed-s'),observer=document.getElementById('monitor-observer');
        const chart=root.querySelector('.monitor-chart'),body=chart.parentElement;
        const widest=Math.max(...[...chart.children].map(n=>{const r=document.createRange();r.selectNodeContents(n);return r.getBoundingClientRect().width;}));
        const viewport=root.querySelector('.monitor-viewport');
        return {view:document.documentElement.dataset.monitorView,deck:rect(health.parentElement),upper:rect(root),limit:rect(document.getElementById('feed')),
          healthOpacity:Number(getComputedStyle(health).opacity),observerOpacity:Number(getComputedStyle(observer).opacity),
          stable:health===window.healthSlot&&observer===window.observerSlot,
          traceRatio:widest/(body.clientWidth-parseFloat(getComputedStyle(body).paddingLeft)-parseFloat(getComputedStyle(body).paddingRight)),
          event:root.querySelector('[data-kind="events"]').textContent,
          observerText:observer.textContent,scroll:viewport.scrollTop,scrollLimit:viewport.scrollHeight-viewport.clientHeight,
          rows:[...root.querySelectorAll('.monitor-section-body > div')].map(n=>n.textContent),
          visible:[...root.querySelectorAll('.monitor-section-body > div')].filter(n=>{const r=n.getBoundingClientRect(),v=viewport.getBoundingClientRect();return r.top>=v.top&&r.bottom<=v.bottom;}).map(n=>n.textContent),
          opsOverflow:health.scrollHeight>health.clientHeight+1,
          observerOverflow:[...observer.querySelectorAll('.monitor-section-body > div')].some(n=>n.scrollWidth>n.clientWidth+1),
          health:window.__sorenBroadcastOverlayHealth};
      });
      evidence.push({name,...result});
      assert.ok(result.stable,'polling retains both transition targets');
      assert.deepEqual(result.health.layout.game,[0,90,960,540]);
      assert.ok(result.traceRatio>.95&&result.traceRatio<1.05,'trace fills its width');
      assert.ok(!/[┌┐└┘─│]/.test(result.event),'terminal frame stays removed');
      assert.ok(result.upper.bottom<=result.deck.y+1&&result.deck.bottom<=result.limit.bottom+1,'cards fit the sidebar');
      assert.equal(result.opsOverflow,false,'health fits its fixed area');
      assert.equal(result.observerOverflow,false,'observer wraps within its width');
      if(artifacts)await page.screenshot({path:path.join(artifacts,`${name}.png`)});
      return result;
    };
    const before=await capture('short-health');
    assert.equal(before.view,'health');assert.equal(before.scrollLimit,0);
    // No old 8-second page flip, including repeated source updates.
    await page.clock.fastForward(43000);
    const still=await capture('short-before-transition');
    assert.equal(still.view,'health');assert.equal(still.scroll,0);assert.deepEqual(still.deck,before.deck);
    await page.clock.fastForward(2200);
    assert.equal(await frame.evaluate(()=>document.documentElement.dataset.monitorView),'observer');
    // Inspect a real CSS transition at its midpoint, independent of fake timers.
    await frame.evaluate(()=>{
      for(const id of ['feed-s','monitor-observer'])for(const animation of document.getElementById(id).getAnimations()){
        animation.pause();animation.currentTime=300;
      }
    });
    const mid=await capture('short-transition-midpoint');
    assert.ok(mid.healthOpacity>.1&&mid.healthOpacity<.9);assert.ok(Math.abs(mid.healthOpacity+mid.observerOpacity-1)<.02);
    await page.clock.fastForward(1100);
    const updatedMid=await capture('short-transition-updated');
    assert.ok(Math.abs(updatedMid.healthOpacity-mid.healthOpacity)<.01,'data updates do not restart a running transition');
    await frame.evaluate(()=>{for(const id of ['feed-s','monitor-observer'])for(const animation of document.getElementById(id).getAnimations())animation.finish();});
    const after=await capture('short-observer');
    assert.equal(after.observerOpacity,1);assert.deepEqual(after.deck,before.deck);assert.deepEqual(after.upper,before.upper);
    await page.clock.fastForward(45000);
    await frame.evaluate(()=>{for(const id of ['feed-s','monitor-observer'])for(const animation of document.getElementById(id).getAnimations())animation.finish();});
    assert.equal((await capture('short-return-health')).view,'health','clock survives continuous polling');
    for(const kind of ['long','dense','many','crowded','short']){
      state=fixture(kind);await page.clock.fastForward(1100);
      await frame.waitForFunction(text=>document.querySelector('.soren-monitor')?._sourceText===text,state.feeds.showStatusG.text);
      const start=await capture(`${kind}-initial`);
      assert.equal(start.deck.height,before.deck.height,'source size cannot resize the footer');
      assert.equal(start.deck.width,before.deck.width);
      assert.ok(start.observerText.includes(kind==='short'?'LastStep':'WildStreak'));
      await page.clock.fastForward(45000);
      await frame.evaluate(()=>{for(const id of ['feed-s','monitor-observer'])for(const animation of document.getElementById(id).getAnimations()){animation.pause();animation.currentTime=300;}});
      const midpoint=await capture(`${kind}-midpoint`);
      assert.deepEqual(midpoint.deck,start.deck);
      await frame.evaluate(()=>{for(const id of ['feed-s','monitor-observer'])for(const animation of document.getElementById(id).getAnimations())animation.finish();});
      const switched=await capture(`${kind}-switched`);
      assert.notEqual(switched.view,start.view);
      assert.deepEqual(switched.deck,start.deck,'switching does not move the footer');
      assert.deepEqual(switched.upper,start.upper,'switching does not move telemetry');
      if(['dense','many','crowded'].includes(kind)){
        const seen=new Set(start.visible),count=kind==='many'?80:28;
        for(let tick=0;tick<Math.ceil(start.scrollLimit/2)+30;tick++){
          await page.clock.fastForward(1000);
          const visible=await frame.evaluate(()=>{const v=document.querySelector('.monitor-viewport').getBoundingClientRect();return [...document.querySelectorAll('.monitor-viewport .monitor-section-body > div')].filter(n=>{const r=n.getBoundingClientRect();return r.top>=v.top&&r.bottom<=v.bottom;}).map(n=>n.textContent);});
          visible.forEach(t=>seen.add(t));
        }
        for(let i=1;i<=count;i++)assert.ok([...seen].some(t=>t.includes(`Candidate-${i} `)),`${kind}: candidate ${i} becomes visible without page replacement`);
        await capture(`${kind}-panned`);
      }
    }
    await page.emulateMedia({reducedMotion:'reduce'});
    state=fixture('dense');await page.clock.fastForward(1100);
    assert.equal(await frame.evaluate(()=>getComputedStyle(document.getElementById('monitor-observer')).transitionDuration),'0s');
    const scroll=await frame.evaluate(()=>document.querySelector('.monitor-viewport').scrollTop);
    await page.clock.fastForward(3000);
    assert.equal(await frame.evaluate(()=>document.querySelector('.monitor-viewport').scrollTop),scroll,'reduced motion has no continuous pan');
    await page.clock.fastForward(45000);await capture('reduced-motion');
    if(await frame.evaluate(()=>document.documentElement.dataset.monitorView)!=='observer')await page.clock.fastForward(45000);
    assert.equal(await frame.evaluate(()=>document.getElementById('feed-s').ariaHidden),'true');
    state.feeds.improve={active:true,updatedAt:Date.now()/1000,logLines:['Comparison in progress']};
    await page.clock.fastForward(1100);
    await frame.waitForFunction(()=>document.documentElement.dataset.improveActive==='1');
    assert.equal(await frame.evaluate(()=>document.getElementById('feed-s').ariaHidden),'false','improve restores visible health accessibility');
    assert.equal(await frame.evaluate(()=>getComputedStyle(document.getElementById('monitor-observer')).display),'none');
    state.feeds.improve.active=false;await page.clock.fastForward(1100);
    await frame.waitForFunction(()=>document.documentElement.dataset.improveActive!=='1');
    await page.clock.fastForward(100);await capture('improve-restored');
    // Another game's layout must restore its normal health region and stop the deck.
    state={...fixture('short'),feeds:{showStatusG:{text:'Another game status',updatedAt:Date.now()/1000},showStatus:fixture('short').feeds.showStatus}};
    await page.clock.fastForward(1100);
    await frame.waitForFunction(()=>!document.querySelector('.soren-monitor'));
    assert.equal(await frame.evaluate(()=>getComputedStyle(document.getElementById('monitor-observer')).display),'none');
    assert.equal(await frame.evaluate(()=>document.getElementById('feed-s').ariaHidden),'false');
    if(artifacts)fs.writeFileSync(path.join(artifacts,'transition-bounds.json'),JSON.stringify(evidence,null,2));
  }finally{await browser.close();server.closeAllConnections();await new Promise(r=>server.close(r));}
});
