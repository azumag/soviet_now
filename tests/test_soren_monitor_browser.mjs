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

test('Soren monitor fills trace width, removes event box and preserves dense observers across updates', {skip:!enabled},async()=>{
  const {chromium}=await import('playwright');
  let state=fixture('short');
  let updates=0;
  const html=fs.readFileSync(path.join(root,'overlays/direct_broadcast_overlay.html'),'utf8');
  const server=http.createServer((req,res)=>{
    if(req.url==='/__soren_overlay/broadcast/state'){
      const payload=structuredClone(state);
      // Each poll changes the renderer cache key while pages are being observed.
      payload.feeds.showStatusG.updatedAt+=++updates/100;
      res.setHeader('Content-Type','application/json');res.end(JSON.stringify(payload));return;
    }
    res.setHeader('Content-Type','text/html; charset=utf-8');
    res.end(req.url==='/crop'?'<body style="margin:0"><iframe data-soren-overlay-region="sidebar" src="/overlay" style="border:0;width:320px;height:720px"></iframe></body>':html);
  });
  await new Promise(r=>server.listen(0,'127.0.0.1',r));
  const browser=await chromium.launch({headless:true});
  const page=await browser.newPage({viewport:{width:320,height:720}});
  const issues=[],evidence=[];
  try {
    await page.clock.install();
    await page.goto(`http://127.0.0.1:${server.address().port}/crop`);
    const frame=page.frames().find(f=>f.url().endsWith('/overlay'));
    await frame.waitForFunction(()=>window.__sorenBroadcastOverlayHealth?.updatedAt>0);
    await frame.evaluate(()=>window.monitorDocumentIdentity='retained');
    if(artifacts) fs.mkdirSync(artifacts,{recursive:true});
    for(const kind of ['short','long','dense','many','crowded','short']) {
      state=fixture(kind);
      await page.clock.runFor(1100);
      await frame.waitForFunction(text=>{
        const monitor=document.querySelector('.soren-monitor');
        return monitor?._sourceText===text&&monitor._budget===Math.max(100,document.getElementById('feed').clientHeight-160);
      },state.feeds.showStatusG.text);
      const seen=new Set();
      const pages=Number(await frame.locator('.soren-monitor').getAttribute('data-pages'))||1;
      for(let p=0;p<pages;p++) {
        const layout=await frame.locator('.soren-monitor').evaluate(e=>{
          const visible=n=>{const r=n.getBoundingClientRect();return r.height>0&&r.bottom<=document.getElementById('feed').getBoundingClientRect().bottom+1;};
          const rect=n=>{const r=n.getBoundingClientRect();return {x:r.x,y:r.y,right:r.right,bottom:r.bottom,width:r.width,height:r.height};};
          const sections=[...e.querySelectorAll('.monitor-section')].filter(visible);
          const chart=e.querySelector('.monitor-chart')||e.querySelector('[data-kind="timeline"] .monitor-section-body');
          let traceRatio=null;
          if(chart&&visible(chart)) {
            const body=e.querySelector('[data-kind="timeline"] .monitor-section-body');
            const rows=[...chart.children];
            const widest=Math.max(...rows.map(n=>{const r=document.createRange();r.selectNodeContents(n);return r.getBoundingClientRect().width;}));
            traceRatio=widest/(body.clientWidth-parseFloat(getComputedStyle(body).paddingLeft)-parseFloat(getComputedStyle(body).paddingRight));
          }
          return {rect:rect(e),limit:rect(document.getElementById('feed')),traceRatio,
            rows:sections.flatMap(s=>[...s.querySelectorAll('.monitor-section-body > div,.monitor-chart > div')].filter(visible).map(n=>n.textContent)),
            eventText:sections.find(s=>s.dataset.kind==='events')?.textContent,
            eventColor:sections.find(s=>s.dataset.kind==='events')?.querySelector('.monitor-section-body > div:last-child span')?.style.color,
            overflow:sections.some(s=>s.scrollWidth>s.clientWidth+1||s.scrollHeight>s.clientHeight+1),
            observer:sections.filter(s=>s.dataset.kind==='observer').map(s=>({rect:rect(s),rows:[...s.querySelector('.monitor-section-body').children].map(rect)})),
            opsOverflow:document.querySelector('.ops-dashboard').scrollHeight>document.getElementById('feed-s').clientHeight+1,
            documentIdentity:window.monitorDocumentIdentity,health:window.__sorenBroadcastOverlayHealth};
        });
        evidence.push({kind,page:p,...layout});
        layout.rows.forEach(t=>seen.add(t));
        if(layout.traceRatio!==null&&layout.traceRatio<.95) issues.push(`${kind}: trace fills only ${layout.traceRatio}`);
        if(/[┌┐└┘─│]/.test(layout.eventText||'')) issues.push(`${kind}: event decoration remains`);
        if(layout.eventText){assert.equal(layout.eventColor,'rgb(231, 191, 117)','event data color survives frame removal');assert.ok(!layout.eventText.includes('FFMPEG'));}
        if(layout.overflow||layout.rect.bottom>layout.limit.bottom+1) issues.push(`${kind}: monitor exceeds available height`);
        if(layout.opsOverflow)issues.push(`${kind}: health dashboard exceeds available height`);
        for(const o of layout.observer)for(const r of o.rows)if(r.x<o.rect.x||r.right>o.rect.right+1||r.bottom>o.rect.bottom+1)issues.push(`${kind}: observer clipped`);
        assert.equal(layout.documentIdentity,'retained','updates must not reload document');
        assert.deepEqual(layout.health.layout.game,[0,90,960,540]);
        if(artifacts) await page.screenshot({path:path.join(artifacts,`status-${kind}-${p}.png`)});
        if(pages>1) {
          const previous=Number(await frame.locator('.soren-monitor').getAttribute('data-page'));
          await page.clock.runFor(8000);
          await frame.waitForFunction(expected=>Number(document.querySelector('.soren-monitor').dataset.page)===expected,previous%pages+1);
        }
      }
      if(['dense','crowded','many'].includes(kind)) {
        for(let i=1;i<=(kind==='many'?80:28);i++) assert.ok([...seen].some(t=>t.includes(`Candidate-${i} `)),`${kind}: candidate ${i} must become visible`);
        for(const key of ['ChatObs','AnnealObs','WildStreak'])assert.ok([...seen].some(t=>t.includes(key)),`${kind}: ${key} must become visible`);
      }else assert.equal(pages,1,'ordinary monitor remains on one page');
    }
    if(artifacts)fs.writeFileSync(path.join(artifacts,'status-bounds.json'),JSON.stringify(evidence,null,2));
    assert.deepEqual(issues,[]);
  }finally{await browser.close();await new Promise(r=>server.close(r));}
});
