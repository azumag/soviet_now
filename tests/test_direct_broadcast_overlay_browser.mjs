import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import http from 'node:http';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const enabled = process.env.SOREN_OVERLAY_BROWSER_TESTS === '1';
const artifacts = process.env.SOREN_OVERLAY_ARTIFACT_DIR;

function fixture(kind) {
  const now = Math.floor(Date.now() / 1000);
  let text = 'SOREN/CORNER: RETRO / hanjuku-hero / 進行中\n半熟英雄 / 最終観測・記録\n'
    + '第2話 / 所持金 123G\nゲーム内: 1年11月（最終観測）\n'
    + '兵力: 9名 / 停滞 3秒\n占領記録 5城（現在の城数ではない）\n'
    + '保有: カストーラ/スペンソニア\n戦闘結果: 4勝 / 1敗 / 未分類 1\n'
    + '交戦HP: 敵 31（dragon） / 我 44（ゼウス）\n'
    + '駐留: アルマムーン=ゼウス/ユイートル\n行軍中: どうし→ナキューメラ\n'
    + '出撃: 成立 4 / 失敗 1\n画面: battle_menu / battle / 方針 chart_adjusted\n'
    + '計画段階: J3（完了未確認）\n保留計画: sortie\n実入力: 412回 / 観測 0秒前';
  if(kind==='long-card'||kind==='stress') text=text.replace('カストーラ/スペンソニア','保有将軍'.repeat(10))
    .replace('アルマムーン=ゼウス/ユイートル','駐留将軍'.repeat(15))
    .replace('どうし→ナキューメラ','行軍将軍'.repeat(15));
  if (kind.startsWith('console')) {
    const count = kind === 'console-empty' ? 0 : 2;
    const next = kind === 'console-restore' ? 'restore-wait' : kind === 'console-unknown' ? 'unverified' : 'collect-results';
    const script = `
import sys
sys.path.insert(0, ${JSON.stringify(root)})
import status_dashboard as sd
value={'history_status':'readable','session_count':${count},'session_mean':20 if ${count} else None,
       'session_best':30 if ${count} else None,'latest':{'score':30,'at':1780000000,'age':${kind === 'console-old' ? 86400 : 40}} if ${count} else None,
       'next':${JSON.stringify(next)},'remaining':1,'remaining_seconds':300,'reason':None}
print('\\n'.join(sd.render_docich_corner_stats({'kind':'retro','label':'RETRO','game':'pacman4console',
      'status':'restoring' if ${JSON.stringify(next)}=='restore-wait' else 'active','session_matches':${count},
      'target_matches':3,'scores':[{'score':10},{'score':30}] if ${count} else [],'console':value})))`;
    const out = spawnSync('python3', ['-c',script], {encoding:'utf8'});
    assert.equal(out.status,0,out.stderr); text=out.stdout;
  } else if (kind === 'monitor') {
    text = process.env.SOREN_MONITOR_FEED ? JSON.parse(fs.readFileSync(process.env.SOREN_MONITOR_FEED,'utf8')).text : fs.readFileSync(path.join(root, 'tests/fixtures/soren-monitor.txt'),'utf8');
  } else if (kind === 'soren91') {
    text = 'SOREN/CORNER: SOREN91 / soren91 / ACTIVE\n'
      + 'Live: this corner results 42\n'
      + 'Stats: 120 results / best=1 / Recent30=5.2\n'
      + '  Trend: -2.1 vs previous 30 / better\n'
      + '  wins=7 / lower rank is better\n'
      + 'Rank Timeline\nLast8: 12 9 4 7 1 3 2 5';
  } else if (kind === 'jev') {
    text = 'SOREN/CORNER: JEV / sorengame / ACTIVE\n'
      + 'Live: player policy=jev generation=4\n'
      + 'Stats: 52 reports / best=8080 / Recent30=6120\n'
      + '  Trend: +430.0 vs previous 22 / better\n'
      + '  Reported scores; may include interrupted runs\n'
      + 'Score Timeline\nLast8: 4100 4800 5300 5100 6200 6800 7200 8080';
  }
  const ops = [
    '━━━ SOREN OPS ━━━',
    '  HEALTH',
    '    ● Loop        RUNNING  PID=101',
    '    ● Workers     7/7 ONLINE  [████████████]',
    '    ● Backend     FFMPEG LIVE  relay=ok',
    '',
    '  ACTIVITY',
    '    ◆ Game        3試合目 (games) R1 [120,220,330]',
    '    ▸ QueueMeter  [██░░░░░░░░]  A=3 C=1 T=0',
    '    ▾ LastDrop    observed',
    '',
    '  AUDIO',
    '    ♪ Say         PLAYING  PID=202',
    '',
    '  TWITCH',
    '    ● Chat        CONNECTED  PID=303',
    '',
    '  YOUTUBE',
    '    ● Chat        CONNECTED  PID=404',
  ];
  if (kind === 'long') ops.push(...Array.from({length:24}, (_, i) => `INFO Observer ${i+1}: ${'観測を確認中 '.repeat(12)}`));
  if (kind === 'stress') ops.push('    ! Unexpected  KickW', '    ! Duplicates  DETECTED  chat_worker=10,11');
  if (kind === 'prediction'||kind==='stress') ops.push('予想対象：#23｜終了まで20秒', '#23：今回の予想対象');
  return {version:1, updatedAt:now, feeds:{
    showStatusG:{text, segments:kind==='monitor'&&process.env.SOREN_MONITOR_FEED?JSON.parse(fs.readFileSync(process.env.SOREN_MONITOR_FEED,'utf8')).segments:undefined, updatedAt:now-(kind==='stale'?31:0), lineCount:text.split('\n').length},
    showStatus:{text:ops.join('\n'), updatedAt:now, lineCount:ops.length},
    improve:{active:kind==='improve', updatedAt:now, logUpdatedAt:now, status:'running', phase:'comparison',
      detail:'検証中', logLines:['候補を比較中', '未採用 / 結果待ち'], lineCount:2},
  }, notifications:{visibleSec:18, events:[], generators:kind==='generator'?[{key:'comment',label:'コメント生成中',ts:now-60}]:[],
    work:{active:['work','long','work-two-line'].includes(kind),ts:now-240,
      title:kind==='work-two-line'?'復旧方針とコメント応答の状態を確認しています。'.repeat(8):'復旧方針の確認中',
      body:kind==='work-two-line'?'状態を確認し、復旧方針とコメント応答の経過を記録しています。'.repeat(3):
        kind==='long'?'状態確認が長引いた場合にも、通知の説明文と経過時間をゲーム領域へ重ねずに表示します。':'レイド応答の状態を確認しています'}}};
}

test('approved v2 rails keep geometry, observed details and region crops in Chromium', {skip:!enabled}, async () => {
  const {chromium} = await import('playwright');
  let state = fixture('normal');
  const html = fs.readFileSync(path.join(root,'overlays/direct_broadcast_overlay.html'),'utf8');
  const server = http.createServer((req,res) => {
    if(req.url==='/__soren_overlay/broadcast/state') {res.setHeader('Content-Type','application/json');res.end(JSON.stringify(state));return;}
    res.setHeader('Content-Type','text/html; charset=utf-8');
    if(req.url.startsWith('/harness/')) {
      const region=req.url.split('/')[2];
      const [w,h]=region==='sidebar'?[320,720]:[960,90];
      res.end(`<body style="margin:0"><iframe data-soren-overlay-region="${region}" data-soren-neutral-theme="${req.url.endsWith('/neutral')?'1':''}" src="/overlay" style="border:0;width:${w}px;height:${h}px"></iframe></body>`);return;
    }
    res.end(html);
  });
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
  const origin=`http://127.0.0.1:${server.address().port}`;
  const browser=await chromium.launch({headless:true});
  try {
    if(artifacts) fs.mkdirSync(artifacts,{recursive:true});
    const page=await browser.newPage({viewport:{width:1280,height:720}});
    for(const kind of ['normal','work','generator','stale','long','long-card','improve','prediction','stress','work-two-line','soren91','jev','monitor','console','console-empty','console-restore','console-unknown','console-old']) {
      state=fixture(kind);
      await page.goto(origin+'/overlay');
      await page.waitForFunction(()=>window.__sorenBroadcastOverlayHealth?.updatedAt>0);
      if(artifacts) await page.screenshot({path:path.join(artifacts,`${kind}.png`)});
      const layout=await page.evaluate(()=>{
        const box=id=>{const r=document.getElementById(id).getBoundingClientRect();return [r.x,r.y,r.width,r.height];};
        const card=document.querySelector('.hanjuku-card');
        const bounds=card?.getBoundingClientRect();
        const panel=document.querySelector('.panel-g').getBoundingClientRect();
        return {sidebar:box('broadcast-sidebar'),top:box('top-rail'),bottom:box('bottom-rail'),
          health:window.__sorenBroadcastOverlayHealth, cardClipped:bounds?bounds.bottom>Math.min(panel.bottom,document.getElementById('feed').getBoundingClientRect().bottom)+1:false,
          cardWidthClipped:card?card.scrollWidth>card.clientWidth+1:false,
          dotsVisible:document.querySelector('.hanjuku-dots')
            ? getComputedStyle(document.querySelector('.hanjuku-dots')).display!=='none' : false,
          bars:[...document.querySelectorAll('.work-bar,.gen-top-bar,.toast-bar')].some(e=>getComputedStyle(e).display!=='none'),
          background:getComputedStyle(document.body).backgroundColor,
          headingVisible:getComputedStyle(document.querySelector('.feed-head')).display!=='none',
          contentInsets:card?['.hanjuku-card','.ops-dashboard'].map(selector=>{const e=document.querySelector(selector),r=e.getBoundingClientRect(),s=getComputedStyle(e);return [r.left+parseFloat(s.paddingLeft),r.right-parseFloat(s.paddingRight)];}):null,
          opsBrands:document.querySelector('.ops-dashboard')?.textContent || '',
          feedRows:document.querySelectorAll('#feed-s .feed-line').length,
          gameDashboard:Boolean(document.querySelector('.game-dashboard')),
          opsDashboard:Boolean(document.querySelector('.ops-dashboard')),
          opsClass:document.querySelector('.ops-dashboard')?.className || '',
          opsChrome:(()=>{const panel=document.querySelector('.panel-s'),head=panel?.querySelector('.panel-head'),kpi=panel?.querySelector('.dash-kpi'); if(!panel)return null; const p=getComputedStyle(panel); return {border:[p.borderTopWidth,p.borderRightWidth,p.borderBottomWidth,p.borderLeftWidth],shadow:p.boxShadow,headDisplay:head?getComputedStyle(head).display:null,kpiShadow:kpi?getComputedStyle(kpi).boxShadow:null};})(),
          gameChrome:(()=>{const panel=document.querySelector('.panel-g'),head=panel?.querySelector('.panel-head'),kpi=panel?.querySelector('.dash-kpi'); if(!panel)return null; const p=getComputedStyle(panel); return {structured:panel.classList.contains('structured'),border:[p.borderTopWidth,p.borderRightWidth,p.borderBottomWidth,p.borderLeftWidth],shadow:p.boxShadow,headDisplay:head?getComputedStyle(head).display:null,kpiShadow:kpi?getComputedStyle(kpi).boxShadow:null};})(),
          gameOverflow:(()=>{const el=document.querySelector('.game-dashboard');return el?el.scrollHeight>el.clientHeight+1:false;})(),
          opsOverflow:(()=>{const el=document.querySelector('.ops-dashboard');return el?el.scrollHeight>el.clientHeight+1:false;})(),
          opsAlerts:document.querySelectorAll('.ops-alert').length};
      });
      assert.deepEqual(layout.sidebar,[960,0,320,720],kind);
      assert.deepEqual(layout.top,[0,0,960,90],kind);
      assert.deepEqual(layout.bottom,[0,630,960,90],kind);
      assert.deepEqual(layout.health.layout.game,[0,90,960,540]);
      assert.equal(layout.health.error,'');
      assert.equal(layout.background,'rgba(0, 0, 0, 0)');
      assert.equal(layout.bars,false);
      assert.equal(layout.dotsVisible,false);
      assert.equal(layout.headingVisible,false,`${kind}: redundant data heading removed`);
      assert.doesNotMatch(layout.opsBrands,/FFMPEG|OBS/);
      if(layout.contentInsets && kind!=='improve') assert.deepEqual(layout.contentInsets[0],layout.contentInsets[1],`${kind}: game and health content edges align`);
      if(['work','long','work-two-line'].includes(kind)) {
        const workBounds=await page.locator('#work').evaluate(el=>{
          const rect=e=>{const r=e.getBoundingClientRect();return {left:r.left,right:r.right,top:r.top,bottom:r.bottom,height:r.height};};
          const style=getComputedStyle(el), box=rect(el);
          const inner={left:box.left+parseFloat(style.borderLeftWidth)+parseFloat(style.paddingLeft),
            right:box.right-parseFloat(style.borderRightWidth)-parseFloat(style.paddingRight),
            top:box.top+parseFloat(style.borderTopWidth)+parseFloat(style.paddingTop),
            bottom:box.bottom-parseFloat(style.borderBottomWidth)-parseFloat(style.paddingBottom)};
          const title=el.querySelector('.work-title'), body=el.querySelector('.work-body');
          return {box,inner,rail:rect(el.parentElement),children:[title,body,el.querySelector('.work-elapsed')].map(rect),
            bodyLines:rect(body).height/parseFloat(getComputedStyle(body).lineHeight),bodyOverflow:body.scrollHeight>body.clientHeight+1,
            titleEllipsis:title.scrollWidth>title.clientWidth,titleSize:getComputedStyle(title).fontSize};
        });
        if(artifacts) fs.writeFileSync(path.join(artifacts,`${kind}-bounds.json`),JSON.stringify(workBounds,null,2));
        for(const box of workBounds.children) {
          for(const edge of ['left','top']) assert.ok(box[edge]>=workBounds.inner[edge]-.5,`${kind}: ${edge} inside work padding`);
          for(const edge of ['right','bottom']) assert.ok(box[edge]<=workBounds.inner[edge]+.5,`${kind}: ${edge} inside work padding`);
        }
        assert.ok(workBounds.box.top-workBounds.rail.top>=8,`${kind}: rail top margin`);
        assert.ok(workBounds.rail.bottom-workBounds.box.bottom>=8,`${kind}: rail bottom margin`);
        assert.equal(workBounds.titleSize,'21px');
        if(kind==='work-two-line') {
          assert.ok(Math.abs(workBounds.bodyLines-2)<.06,'fixture must render exactly two body lines');
          assert.equal(workBounds.bodyOverflow,false,'two lines fit without hidden extra lines');
          assert.equal(workBounds.titleEllipsis,true,'long title retains ellipsis');
        }
      }
      if(kind!=='improve') {assert.equal(layout.cardClipped,false,kind);assert.equal(layout.cardWidthClipped,false,kind);}
      if(!kind.startsWith('console') && !['improve','soren91','jev','monitor'].includes(kind)) {
        assert.equal(layout.gameChrome.headDisplay,'none',`${kind}: redundant Hanjuku panel header hidden`);
      }
      if(kind==='stale') {
        assert.equal(await page.locator('.hanjuku-chapter').textContent(),'話数 未確認');
        assert.equal(await page.locator('.hanjuku-inputs').count(),0);
      } else if(!kind.startsWith('console') && !['improve','soren91','jev','monitor'].includes(kind)) {
        assert.match(await page.locator('.hanjuku-orders').textContent(),/成立 4 \/ 失敗 1/);
        assert.equal(await page.locator('.hanjuku-plan').count(),0);
        assert.equal(await page.locator('.hanjuku-inputs').count(),0);
        assert.equal(await page.locator('.hanjuku-troops').evaluate(e=>parseFloat(getComputedStyle(e).fontSize)),24);
        for(const selector of ['.hanjuku-duel','.hanjuku-holds','.hanjuku-garrison','.hanjuku-marching']) {
          assert.equal(await page.locator(selector).evaluate(e=>parseFloat(getComputedStyle(e).fontSize)),18,`${kind}: ${selector} stays readable`);
        }
        assert.equal(await page.locator('.hanjuku-orders').evaluate(e=>parseFloat(getComputedStyle(e).fontSize)),16);
      }
      if(kind==='monitor') {
        assert.ok(await page.locator('.monitor-section').count()>=4);
        const checks=await page.locator('.soren-monitor').evaluate(e=>({
          bottom:e.getBoundingClientRect().bottom,
          limit:Math.min(document.querySelector('.panel-g').getBoundingClientRect().bottom,document.querySelector('#feed').getBoundingClientRect().bottom),
          overflow:[...e.querySelectorAll('.monitor-section-body')].some(b=>b.scrollWidth>b.clientWidth+1),
          borders:[...e.querySelectorAll('.monitor-section')].every(b=>getComputedStyle(b).borderLeftWidth==='1px'),
        }));
        assert.ok(checks.bottom<=checks.limit+1,'all Soren monitor sections fit');
        assert.equal(checks.overflow,false,'ASCII graphs retain their full width');
        if(artifacts) await page.screenshot({path:path.join(artifacts,'monitor-sidebar.png'),clip:{x:960,y:0,width:320,height:720}});
      }
      assert.equal(layout.opsDashboard,true,`${kind}: OPS uses structured dashboard`);
      assert.deepEqual(layout.opsChrome.border,['0px','0px','0px','0px'],`${kind}: OPS outer panel border removed`);
      assert.equal(layout.opsChrome.shadow,'none',`${kind}: OPS outer panel shadow removed`);
      assert.equal(layout.opsChrome.headDisplay,'none',`${kind}: redundant OPS panel header hidden`);
      assert.equal(layout.opsChrome.kpiShadow,'none',`${kind}: decorative KPI left accent removed`);
      if(kind==='long') {
        assert.equal(layout.feedRows,0,'long raw OPS logs are summarized instead of shrinking typography');
        assert.equal(layout.opsAlerts,0,'informational observer rows do not become alerts');
      }
      if(['normal','prediction','stress'].includes(kind)) {
        assert.equal(layout.opsOverflow,false,`${kind}: adaptive OPS dashboard fits the available rail height`);
      }
      if(kind==='stress') {
        assert.equal(layout.feedRows,0,'stress raw OPS logs are summarized');
        assert.ok(layout.opsAlerts>=1,'stress faults remain visible as attention rows');
        assert.equal(layout.opsOverflow,false,'stress attention fits without clipping');
      }
      if(kind.startsWith('console')) {
        const records=await page.locator('.game-record').allTextContents();
        assert.equal(records.length,4,`${kind}: all four observation/plan rows visible`);
        assert.match(records[3],/live score unobserved/);
        assert.match(records[2],kind==='console-restore'?/restoration pending/:kind==='console-unknown'?/runtime unverified/:/matches left/);
        assert.equal(layout.gameOverflow,false,`${kind}: records fit without shrinking`);
        const bounds=await page.locator('.game-records').evaluate(el=>({
          overflow:el.scrollWidth>el.clientWidth+1, bottom:el.getBoundingClientRect().bottom,
          limit:document.querySelector('.panel-g').getBoundingClientRect().bottom,
          size:getComputedStyle(el.firstElementChild).fontSize,
          leftBorder:getComputedStyle(el.firstElementChild).borderLeftWidth}));
        assert.equal(bounds.overflow,false); assert.ok(bounds.bottom<=bounds.limit+1);
        assert.equal(bounds.size,'10px'); assert.equal(bounds.leftBorder,'0px');
        if(artifacts) await page.screenshot({path:path.join(artifacts,`${kind}-sidebar.png`),clip:{x:960,y:0,width:320,height:720}});
      }
      if(kind==='soren91'||kind==='jev'||kind.startsWith('console')) {
        assert.equal(layout.gameDashboard,true,`${kind}: GAME uses structured score dashboard`);
        assert.deepEqual(layout.gameChrome.border,['0px','0px','0px','0px'],`${kind}: GAME outer panel border removed`);
        assert.equal(layout.gameChrome.headDisplay,'none',`${kind}: redundant GAME panel header hidden`);
        assert.equal(layout.gameChrome.kpiShadow,'none',`${kind}: GAME KPI left accent removed`);
        assert.equal(layout.gameOverflow,false,`${kind}: GAME dashboard fits the panel`);
        assert.equal(layout.opsOverflow,false,`${kind}: OPS dashboard fits the panel`);
        const values=await page.locator('#feed-g .dash-kpi-value').allTextContents();
        assert.equal(values.length,2,`${kind}: two primary game KPIs`);
        if(kind!=='console-empty') assert.ok(await page.locator('#feed-g .game-bars').count(),`${kind}: recent result bars are visible`);
      }
    }
    state=fixture('work-two-line');
    for(const region of ['sidebar','top','bottom']) {
      await page.goto(origin+`/harness/${region}`);
      const frame=page.frames().find(f=>f.url()===origin+'/overlay');
      await frame.waitForFunction(()=>window.__sorenBroadcastOverlayHealth?.updatedAt>0);
      assert.equal(await frame.evaluate(()=>window.__sorenBroadcastOverlayHealth.region),region);
      const expected=region==='sidebar'?[320,720]:[960,90];
      assert.deepEqual(await frame.evaluate(()=>[document.body.clientWidth,document.body.clientHeight]),expected);
      if(region==='top') {
        const cropped=await frame.locator('#work').evaluate(el=>{
          const b=el.getBoundingClientRect();
          return {box:[b.left,b.top,b.right,b.bottom],children:[...el.querySelectorAll('.work-title,.work-body,.work-elapsed')].map(e=>{
            const r=e.getBoundingClientRect();return [r.left,r.top,r.right,r.bottom];
          })};
        });
        assert.deepEqual(cropped.box,[8,8,952,81],'fixed card retains crop margins');
        for(const [left,top,right,bottom] of cropped.children) {
          assert.ok(left>=21 && right<=939.5 && top>=13 && bottom<=76.5,'work children retain padding in actual top crop');
        }
      }
      if(artifacts) await page.screenshot({path:path.join(artifacts,`${region}.png`),clip:{x:0,y:0,width:expected[0],height:expected[1]}});
    }
    await page.goto(origin+'/harness/sidebar/neutral');
    const neutral=page.frames().find(f=>f.url()===origin+'/overlay');
    await neutral.waitForFunction(()=>window.__sorenBroadcastOverlayHealth?.updatedAt>0);
    assert.deepEqual(await neutral.evaluate(()=>[
      getComputedStyle(document.getElementById('broadcast-overlay')).filter,
      getComputedStyle(document.getElementById('broadcast-sidebar')).backgroundColor,
    ]),['grayscale(1)','rgb(5, 5, 5)']);
  } finally {await browser.close(); await new Promise(resolve=>server.close(resolve));}
});
