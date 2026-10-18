import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import http from 'node:http';
import {fileURLToPath} from 'node:url';
const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..');
const enabled=process.env.SOREN_OVERLAY_BROWSER_TESTS==='1';

test('measured gap keeps large complete cards, expires observations and clears on game switch', {skip:!enabled}, async()=>{
  const {chromium}=await import('playwright');
  const now=Math.floor(Date.now()/1000);
  const names=['アルマムーン','ナキューメラ','カストーラ','スペンソニア','ハドリバーグ','ロックフォール'];
  let extra=`投影余白: side=left x=0 w=239 until=${now+30}\n余白占領記録: ${names.join(' / ')}\n`
    +`余白駐留: until=${now+30} アルマムーン=長い名前の将軍です/ユイートル/ゼウス\n`
    +`余白駐留: until=${now-1} 古い城=非表示将軍\n余白行軍: どうし→ナキューメラ\n`
    +`余白交戦HP: until=${now+10} ロックフォール 51 / どうし 90\n`
    +`余白交戦兵数: until=${now+10} 敵 4 / 我 7\n`
    +`余白出撃意図: どうし / アルマムーン→ナキューメラ / 攻撃 / 状態 launched / 段階 J3\n`
    +`余白戦闘計画: until=${now+10} 攻撃 ナキューメラ / 段階 J3 / 方針 chart_adjusted\n`
    +`余白切り札: until=${now+10} 予定 ゼンマイン・クースカン / 使用 ゼンマイン / 現在 クースカン(menu)\n`
    +`余白戦闘判断: until=${now+10} 敵の卵使用を避けるため、致死確認できる切り札を優先\n`
    +`余白卵対策: until=${now+10} 奥の手選択済み`;
  let text='SOREN/CORNER: RETRO / hanjuku-hero / 進行中\n半熟英雄 / 最終観測・記録\n第2話 / 所持金 123G\nゲーム内: 1年11月（最終観測）\n兵力: 9名 / 停滞 3秒\n戦闘結果: 4勝 / 1敗 / 未分類 0\n戦闘: 開始 6 / 終了 5 / 切り札確定 2\n城失陥: 1件（全体マップで旗が敵色になった実測）\n失った城: ジョンリギ\n卵: ゼウス 2回 / どうし 1回\n出撃: 成立 3 / 失敗 1\n出撃意図: どうし / アルマムーン→ナキューメラ / 攻撃 / 状態 launched / 段階 J3\n画面: battle_menu / battle\n計画段階: J3（完了未確認）\n保留計画: sortie\n実入力: 41回 / 観測 2秒前（530回）\n'+extra;
  let feedAt=now;
  const state=()=>({gameGapEnabled:true,updatedAt:feedAt,feeds:{showStatusG:{text,updatedAt:feedAt,lineCount:20}},notifications:{events:[],generators:[],work:{active:false}}});
  const html=fs.readFileSync(path.join(root,'overlays/direct_broadcast_overlay.html'),'utf8');
  const server=http.createServer((req,res)=>{
    if(req.url==='/__soren_overlay/broadcast/state'){res.setHeader('Content-Type','application/json');res.end(JSON.stringify(state()));return;}
    res.setHeader('Content-Type','text/html; charset=utf-8');
    if(req.url==='/game-gap'){res.end('<iframe data-soren-overlay-region="game-gap" src="/overlay" style="position:fixed;left:0;top:90px;width:960px;height:540px;border:0"></iframe>');return;}
    // Synthesize the game plane behind the full overlay for the review image.
    res.end(html.replace('<body>','<body><div style="position:absolute;left:239px;top:90px;width:721px;height:540px;background:#13223a;border:4px solid #f5df64;box-sizing:border-box"></div>'));
  });
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
  const origin=`http://127.0.0.1:${server.address().port}`;
  const browser=await chromium.launch({headless:true});
  try{
    const page=await browser.newPage({viewport:{width:1280,height:720}});
    await page.addInitScript(stamp=>{const RealDate=Date;window.gapClock=stamp;Date.now=()=>window.gapClock*1000;},now);
    await page.goto(origin+'/overlay');
    await page.waitForFunction(()=>document.getElementById('hanjuku-gap').style.display==='block');
    const read=()=>page.evaluate(()=>{
      const gap=document.getElementById('hanjuku-gap'),r=gap.getBoundingClientRect();
      return {box:[r.x,r.y,r.width,r.height],text:gap.textContent,
        overflow:gap.scrollHeight>gap.clientHeight||gap.scrollWidth>gap.clientWidth,
        size:[...gap.querySelectorAll('.hanjuku-group')].map(e=>getComputedStyle(e).fontSize),
        sidebar:document.getElementById('feed-g').textContent,
        cards:[...gap.querySelectorAll('.hanjuku-group')].map(e=>e.textContent)};
    });
    let observed=new Set();
    // The bounded fixture produces several pages; move the clock across all
    // page indices while refreshing the same public observations' deadlines.
    for(let i=0;i<6;i++){
      const clock=now+i*10; feedAt=clock;
      text='SOREN/CORNER: RETRO / hanjuku-hero / 進行中\n半熟英雄 / 最終観測・記録\n第2話 / 所持金 123G\nゲーム内: 1年11月（最終観測）\n兵力: 9名 / 停滞 3秒\n戦闘結果: 4勝 / 1敗 / 未分類 0\n戦闘: 開始 6 / 終了 5 / 切り札確定 2\n城失陥: 1件（全体マップで旗が敵色になった実測）\n失った城: ジョンリギ\n卵: ゼウス 2回 / どうし 1回\n出撃: 成立 3 / 失敗 1\n出撃意図: どうし / アルマムーン→ナキューメラ / 攻撃 / 状態 launched / 段階 J3\n画面: battle_menu / battle\n計画段階: J3（完了未確認）\n保留計画: sortie\n実入力: 41回 / 観測 2秒前（530回）\n'
        +extra.replaceAll(`until=${now+30}`,`until=${clock+30}`).replaceAll(`until=${now+10}`,`until=${clock+10}`);
      await page.evaluate(stamp=>window.gapClock=stamp,clock);
      // Feed age must stay fresh, independently of source deadlines.
      await page.evaluate(payload=>window.__sorenBroadcastState=payload,{...state(),feeds:{showStatusG:{text,updatedAt:clock,lineCount:20}}});
      await page.waitForTimeout(1100);
      const result=await read();
      assert.deepEqual(result.box,[0,90,239,540]);assert.equal(result.overflow,false);
      assert.ok(result.size.every(s=>s==='18px'));assert.ok(!result.text.includes('非表示将軍'));
      assert.ok(!result.sidebar.includes('長い名前の将軍です'));
      for(const card of result.cards)observed.add(card);
    }
    assert.ok([...observed].some(s=>s.includes('長い名前の将軍です')));
    assert.ok([...observed].some(s=>s.includes('卵残回数') && s.includes('ゼウス 2回')));
    assert.ok([...observed].some(s=>s.includes('戦績・戦闘') && s.includes('城失陥 1')));
    assert.ok([...observed].some(s=>s.includes('入力・観測') && s.includes('530回')));
    assert.ok([...observed].some(s=>s.includes('交戦詳細') && s.includes('敵 4 / 我 7')));
    assert.ok([...observed].some(s=>s.includes('現在の出撃意図') && s.includes('アルマムーン→ナキューメラ')));
    assert.ok([...observed].some(s=>s.includes('AI戦闘計画') && s.includes('攻撃 ナキューメラ')));
    assert.ok([...observed].some(s=>s.includes('切り札状況') && s.includes('ゼンマイン')));
    assert.ok([...observed].some(s=>s.includes('卵・奥の手') && s.includes('奥の手選択済み')));
    assert.ok([...observed].some(s=>s.includes('判断理由') && s.includes('致死確認')));
    for(const name of names)assert.ok([...observed].some(s=>s.includes(name)),name);
    if(process.env.SOREN_OVERLAY_ARTIFACT_DIR){fs.mkdirSync(process.env.SOREN_OVERLAY_ARTIFACT_DIR,{recursive:true});await page.screenshot({path:path.join(process.env.SOREN_OVERLAY_ARTIFACT_DIR,'hanjuku-gap.png')});}
    // Exact deadline, even if status-feed mtime has not changed.
    await page.evaluate(()=>window.gapClock+=31);
    await page.waitForTimeout(1100);assert.equal(await page.locator('#hanjuku-gap').isVisible(),false);
    text='SOREN/CORNER: RETRO / another-game / 進行中'; feedAt=now;
    await page.evaluate(stamp=>window.gapClock=stamp,now);
    await page.waitForTimeout(1100);assert.equal(await page.locator('#hanjuku-gap').isVisible(),false);
    // Dedicated iframe crop starts at y=0 locally, while the host owns y=90.
    await page.goto(origin+'/game-gap');
    const frame=page.frames().find(f=>f.url().endsWith('/overlay'));
    await frame.waitForFunction(()=>window.__sorenBroadcastOverlayHealth?.updatedAt>0);
    assert.equal(await frame.locator('#broadcast-sidebar').isVisible(),false);
  }finally{await browser.close();await new Promise(resolve=>server.close(resolve));}
});
