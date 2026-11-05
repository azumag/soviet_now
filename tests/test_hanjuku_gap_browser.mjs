import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import http from 'node:http';
import {fileURLToPath} from 'node:url';
const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..');
const enabled=process.env.SOREN_OVERLAY_BROWSER_TESTS==='1';

test('the measured gap shows live toasts and the sortie chart, and clears on switch', {skip:!enabled}, async()=>{
  const {chromium}=await import('playwright');
  const now=Math.floor(Date.now()/1000);
  const owned=['アルマムーン','ナキューメラ','カストーラ','スペンソニア','ハドリバーグ','ロックフォール'];
  const moves=[
    '余白出撃: パプリカ / カルーン→ジュメルズ / 成立未確認 / 段階 A:001e11d9:Mdf4142',
    '余白出撃: どうし / アルマムーン→ナキューメラ / 行軍中 / 段階 A:002 / 理由 ナキューメラの敵を討つ',
  ];
  const build=(clock, extraMoves=[], extraOwned=[], extraLines=[])=>[
    'SOREN/CORNER: RETRO / hanjuku-hero / 進行中',
    '半熟英雄 / 最終観測・記録',
    '第2話 / 所持金 123G',
    'ゲーム内: 1年11月（最終観測）',
    '兵力: 9名 / 停滞 3秒',
    '将軍数: 12人（前月の完全名簿）',
    '戦闘結果: 4勝 / 1敗 / 未分類 0',
    '戦闘: 開始 6 / 終了 5 / 切り札確定 2',
    '失った城: ジョンリギ',
    '出撃: 成立 3 / 失敗 1',
    '画面: battle_menu / battle',
    '計画段階: J3（完了未確認）',
    '保留計画: sortie',
    `投影余白: side=left x=0 w=239 until=${clock+30}`,
    `余白占領記録: ${[...owned, ...extraOwned].join(' / ')}`,
    `余白交戦HP: until=${clock+20} 敵シェーブル 55 / どうし 70`,
    `余白交戦兵数: until=${clock+20} 敵 4 / 我 7`,
    `余白戦闘計画: until=${clock+20} 攻撃 ナキューメラ / 段階 J3 / 方針 retry_with_opening_cards`,
    `余白切り札: until=${clock+20} 予定 ゼンマイン・クースカン / 使用 ゼンマイン / 現在 クースカン(menu)`,
    `余白戦闘判断: until=${clock+20} 敵の卵使用を避けるため、致死確認できる切り札を優先`,
    `余白卵対策: until=${clock+20} 奥の手選択済み`,
    ...moves, ...extraMoves, ...extraLines,
  ].join('\n');
  let text=build(now);
  let feedAt=now;
  const state=()=>({gameGapEnabled:true,updatedAt:feedAt,feeds:{showStatusG:{text,updatedAt:feedAt,lineCount:30}},notifications:{events:[],generators:[],work:{active:false}}});
  const html=fs.readFileSync(path.join(root,'overlays/direct_broadcast_overlay.html'),'utf8');
  const server=http.createServer((req,res)=>{
    if(req.url==='/__soren_overlay/broadcast/state'){res.setHeader('Content-Type','application/json');res.end(JSON.stringify(state()));return;}
    res.setHeader('Content-Type','text/html; charset=utf-8');
    if(req.url==='/game-gap'){res.end('<iframe data-soren-overlay-region="game-gap" src="/overlay" style="position:fixed;left:0;top:90px;width:960px;height:540px;border:0"></iframe>');return;}
    res.end(html.replace('<body>','<body><div style="position:absolute;left:239px;top:90px;width:721px;height:540px;background:#13223a;border:4px solid #f5df64;box-sizing:border-box"></div>'));
  });
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
  const origin=`http://127.0.0.1:${server.address().port}`;
  const browser=await chromium.launch({headless:true});
  try{
    const page=await browser.newPage({viewport:{width:1280,height:720}});
    await page.addInitScript(stamp=>{window.gapClock=stamp;Date.now=()=>window.gapClock*1000;},now);
    await page.goto(origin+'/overlay');
    await page.waitForFunction(()=>document.getElementById('hanjuku-gap').style.display==='block');
    const read=()=>page.evaluate(()=>{
      const gap=document.getElementById('hanjuku-gap'),r=gap.getBoundingClientRect();
      return {box:[r.x,r.y,r.width,r.height],text:gap.textContent,
        overflow:gap.scrollHeight>gap.clientHeight||gap.scrollWidth>gap.clientWidth,
        moves:[...gap.querySelectorAll('.hanjuku-gap-move')].map(e=>e.textContent),
        toasts:[...gap.querySelectorAll('.hanjuku-gap-toast')].map(e=>e.textContent),
        sidebar:document.getElementById('feed-g').textContent};
    });
    let result=await read();
    assert.deepEqual(result.box,[0,90,239,540]);
    assert.equal(result.overflow,false);
    // The left is a sortie chart, not a copy of the right status card.
    assert.equal(result.moves.length,2);
    for (const part of ['パプリカ','成立未確認','カルーン','ジュメルズ']) assert.ok(result.moves[0].includes(part), part);
    for (const part of ['どうし','行軍中','アルマムーン','ナキューメラ','理由 ナキューメラの敵を討つ']) assert.ok(result.moves[1].includes(part), part);
    assert.match(result.text,/出撃チャート/);
    // Battle detail lines are parsed but stay out of the fixed-height flow;
    // they surface as toasts only when they change (checked after the next poll).
    for(const old of ['現在状況','戦績・戦闘','入力・観測','AI戦闘計画']){
      assert.ok(!result.text.includes(old),`left status card removed: ${old}`);
    }
    // The right status card keeps the live status.
    assert.match(result.sidebar,/兵力/);
    assert.match(result.sidebar,/将軍数/);
    // A new sortie and a captured castle become toasts on the next observation.
    const clock=now+2; feedAt=clock;
    text=build(clock,['余白出撃: キッシュ / バスカビル→プトレマイス / 行軍中 / 段階 B:003'],['ジュメルズ'],
      [`余白戦闘判断: until=${clock+20} 防御を固めるため切り札を温存`]);
    await page.evaluate(stamp=>window.gapClock=stamp,clock);
    await page.evaluate(payload=>window.__sorenBroadcastState=payload,{...state(),feeds:{showStatusG:{text,updatedAt:clock,lineCount:30}}});
    await page.waitForTimeout(1200);
    result=await read();
    assert.equal(result.overflow,false);
    assert.equal(result.moves.length,3);
    assert.ok(result.toasts.some(t=>t.includes('出撃 キッシュ')),result.toasts.join('|'));
    assert.ok(result.toasts.some(t=>t.includes('城を奪取 ジュメルズ')),result.toasts.join('|'));
    // A changed battle decision reason becomes a battle toast.
    assert.ok(result.toasts.some(t=>t.includes('判断:')&&t.includes('防御を固めるため切り札を温存')),result.toasts.join('|'));
    // No redundant 速報 heading on the card.
    assert.ok(!result.text.includes('速報'));
    // A poll without changes must not recreate the toast DOM (flicker guard).
    await page.evaluate(()=>{const t=document.querySelector('#hanjuku-gap .hanjuku-gap-toast'); if(t) t.dataset.probe='kept';});
    await page.waitForTimeout(1200);
    assert.equal(await page.evaluate(()=>document.querySelector('#hanjuku-gap .hanjuku-gap-toast')?.dataset.probe||null),'kept');
    if(process.env.SOREN_OVERLAY_ARTIFACT_DIR){fs.mkdirSync(process.env.SOREN_OVERLAY_ARTIFACT_DIR,{recursive:true});await page.screenshot({path:path.join(process.env.SOREN_OVERLAY_ARTIFACT_DIR,'hanjuku-gap.png')});}
    // Exact deadline, even if the status feed mtime does not change.
    await page.evaluate(()=>window.gapClock+=31);
    await page.waitForTimeout(1100);
    assert.equal(await page.locator('#hanjuku-gap').isVisible(),false);
    // A game switch clears the surface and its toasts.
    text='SOREN/CORNER: RETRO / another-game / 進行中'; feedAt=now;
    await page.evaluate(stamp=>window.gapClock=stamp,now);
    await page.waitForTimeout(1100);
    assert.equal(await page.locator('#hanjuku-gap').isVisible(),false);
    // Dedicated iframe crop starts at y=0 locally, while the host owns y=90.
    await page.goto(origin+'/game-gap');
    const frame=page.frames().find(f=>f.url().endsWith('/overlay'));
    await frame.waitForFunction(()=>window.__sorenBroadcastOverlayHealth?.updatedAt>0);
    assert.equal(await frame.locator('#broadcast-sidebar').isVisible(),false);
  }finally{await browser.close();await new Promise(resolve=>server.close(resolve));}
});
