import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import http from 'node:http';
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
  const ops = ['● Backend FFMPEG LIVE', 'Game: hanjuku-hero', 'Chat: RUNNING', 'Audio: READY',
    'YouTube: connected', 'Kick: connected', 'LastDrop: observed'];
  if (kind === 'long'||kind==='stress') ops.push(...Array.from({length:24}, (_, i) => `INFO Observer ${i+1}: ${'観測を確認中 '.repeat(12)}`));
  if (kind === 'prediction'||kind==='stress') ops.push('予想対象：#23｜終了まで20秒', '#23：今回の予想対象');
  return {version:1, updatedAt:now, feeds:{
    showStatusG:{text, updatedAt:now-(kind==='stale'?31:0), lineCount:text.split('\n').length},
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
    for(const kind of ['normal','work','generator','stale','long','long-card','improve','prediction','stress','work-two-line']) {
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
          dotsVisible:getComputedStyle(document.querySelector('.hanjuku-dots')).display!=='none',
          bars:[...document.querySelectorAll('.work-bar,.gen-top-bar,.toast-bar')].some(e=>getComputedStyle(e).display!=='none'),
          background:getComputedStyle(document.body).backgroundColor,
          feedRows:document.querySelectorAll('#feed-s .feed-line').length};
      });
      assert.deepEqual(layout.sidebar,[960,0,320,720],kind);
      assert.deepEqual(layout.top,[0,0,960,90],kind);
      assert.deepEqual(layout.bottom,[0,630,960,90],kind);
      assert.deepEqual(layout.health.layout.game,[0,90,960,540]);
      assert.equal(layout.health.error,'');
      assert.equal(layout.background,'rgba(0, 0, 0, 0)');
      assert.equal(layout.bars,false);
      assert.equal(layout.dotsVisible,false);
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
      if(kind==='stale') {
        assert.equal(await page.locator('.hanjuku-chapter').textContent(),'話数 未確認');
        assert.equal(await page.locator('.hanjuku-inputs').textContent(),'実際に送った入力 —回');
      } else if(kind!=='improve') {
        assert.match(await page.locator('.hanjuku-orders').textContent(),/成立 4 \/ 失敗 1/);
        assert.match(await page.locator('.hanjuku-plan').textContent(),/完了未確認/);
        assert.match(await page.locator('.hanjuku-note').textContent(),/観測/);
      }
      if(kind==='long'||kind==='stress') {
        assert.equal(layout.feedRows,kind==='stress'?33:31,'all log lines retained');
        const clipping=await page.locator('#feed-s').evaluate(el=>{
          const bottom=el.parentElement.getBoundingClientRect().bottom-1;
          return [...el.children].filter(row=>row.getBoundingClientRect().bottom>bottom+1).map(row=>row.textContent);
        });
        assert.deepEqual(clipping,[],'all log rows remain visibly inside the panel');
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
