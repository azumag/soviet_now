# ソ連建国率改善ループ — 再開用ハンドオフ (最終更新: 2026-06-15)

このファイルだけ読めば次セッションでループを再開できる。

---

## 0. 最優先・即実行（セッション開始時）

### (A) SOVIETモニターを必ず再武装する
モニターはセッション/cron発火をまたいで消えるため、**毎パス冒頭で TaskList を確認し、無ければ再武装**する。
Monitor ツールで以下を `persistent: true`, `timeout_ms: 3600000` で起動:
```
tail -f -n 0 /Users/azumag/azumag/work/soren/logs/soren_loop.log | grep --line-buffered 'SOVIET UNION CREATED'
```
役割: ソ連建国(2×Russia)の瞬間を検知。発火したら祝賀+報告+`say`。

### (B) HEALTH 確認（毎パス）
```bash
cd /Users/azumag/azumag/work/soren
cp -n game_history/2026*_score*.jsonl tmp/replay_20260612/corpus/ 2>/dev/null   # 証拠保全（後述）
pgrep -f 'soren_loop.sh'|head -1            # 期待: 9000 (生存)
pgrep -f 'strategy_runner.py'|head -1       # 毎ゲーム別PID。生存していればOK
python3 extract_decide_hash.py strategy.py  # 期待: d5fff9501436 (EXP-3=確定ベスト。EXP-6/EXP-7とも棄却・revert済)
python3 -c "import json; print(json.load(open('tmp/state/active_branch.json'))['head_hash'])"  # 期待: d5fff9501436 と一致
grep -c 'SOVIET UNION CREATED' logs/soren_loop.log   # 1 = frozen game29557のみ。>1 で新ソ連！
```

---

## 1. 現在の状態

### ★運用モード: 全力ソ連到達 (2026-06-17 ユーザー指示)
「option2(大規模最適化)+3(analyze_board新特徴)を確認で止まらず、ソ連できる最後まで一気に」。**方針確認では止まらず自律的に機構を設計→検証→デプロイ→A/B→反復**。但し検証(replay harness crash0/A/B funnel)は品質のため継続。各機構の狙い=**2nd-nucleus形成(2個目の高ティア)**。判定は2nd-nucleus funnel(T13pair/T14pair/Russia)で、改善あれば採用・なければEXP-3へロールバック。cron=cc853f20(push mode)。

- **稼働中の戦略 (live=head)**: `ba5935ce2a9a` = **EXP-9 (FAST_DROP=False)** = working baseline。EXP-14(valley)はn=35でT13pair悪化(23%vs35%)→revert。深いfallback=EXP-3。
- **ロールバック先=確定ベスト**: `d5fff9501436` = **EXP-3 (LOW_DRAIN_CLUSTER)**。**EXP-6/EXP-7とも棄却・revert済**（§8）
- **frozen 復元先**: `d88fc8bfd580`（`tmp/goal_restore_20260604/RESTORE_FROZEN.sh` で復元）
- **ソ連建国**: まだ達成なし（マーカー=1=過去のframezn game29557のみ）。Russia(単独)は ~5% で散発
- **ループ稼働**: soren_loop.sh PID 9000。strategy_runner は毎ゲーム別プロセス起動 → **strategy.py / analyze_board.py の変更は次ゲームに自動反映**（手動restart不要）

### WATCH解決 (2026-06-17 05:39): T14+低下は variance と確定
03:39に T14+ 8%/40ゲームで警戒したが、**8%→20%(04:39)→36%(05:39, baseline 25%超)** と回復・score_medも876→1312(≒baseline)復帰。memory 31-33%横ばい・strategy凍結・infra zeros 0 で、**環境異常でなく variance と確定**。教訓: T14+は ~25%基準でn=25-40でも大きく振れる(8%↔40%)。単独の低温窓では警報せず、score_med と T13+(robust)が両方落ちて持続する時のみ環境調査へ。

### (旧)WATCH (2026-06-17 03:39): T14+到達が持続的に低い [解決済]
直近40ゲームで **T14+ 到達 8%(対 lifetime 25%)** が持続(last25もlast40も8%)・score_med 1149-1201(対 baseline 1331)。T13+は78-80%(≒baseline)＝**T13までは届くがT14に押し上がらない**。40ゲームで統計的に有意(期待~10 vs 実~3, p<0.01)＝単純noiseでは説明しにくい。
- **実測した除外項目**: strategy.py md5不変(EXP-3純正・退行機構なし)・memory 32%free(OKだが19:39の56%から低下傾向)・load 5.43(中程度)・暴走プロセスなし・infra zerosゼロ・**turns_med 82(≒baseline=早死にでない=ラグ説に反する)**。
- **判断**: 環境要因の特定できず・robust指標(T13+/score_med)は≒baseline・戦略凍結ゆえ、**戦略変更も無断restartも不可**。HOLDで監視。次パス以降も T14+ ≤10% 継続 or score_med 床割れ継続なら、(a)memory推移を精査 (b)ユーザーに live game Chrome の再起動可否を相談。lifetime T14+ 25%なので回復すればvariance確定。
- **04:39追記: T14+ 回復 (8%→last15で20%)**。escalation条件(≤10%継続)不成立＝**variance寄りと判明**。memory 33%で横ばい(更なる低下なし)。score_medはまだ軟調(last15 876)だがT13+/T14+は≒baselineへ復帰中＝建設は正常。引き続き監視のみ。

### WATCH解決 (2026-06-15 12:39): 低温窓は variance と確定
11:39〜12:39 で score_med が ~1150 まで下がり floor も 35%→50% に上昇したが、**決定的証拠で variance と確定**:
- **max到達ティア分布が baseline と同一**: last20 で T13=55%(base57)/T14=20%(base22)/**T15(Russia)=5%(base4)**。建設エンジンは完全健全・Russiaも baseline率。
- strategy.py md5一致(不変)・turns_med 80で終日安定・infra zerosゼロ。
- last20の内訳: 16/20が T13+到達(最高3340)、4/20が T12早死に(=baselineの~20%早死に率と同じ)。score_medが低いのは**この窓の分布がたまたま下振れた**だけ。
→ **退行ではない。HOLD継続。** 教訓: **score_med/Russia-countは窓ノイズが大きい。健全性判定は「到達ティア分布(T13/T14/T15率)」を主指標にせよ**(これが baseline比で落ちた時のみ調査)。turns_med<76 や infra zeros も環境劣化の補助シグナル。

### EXP-3 (確定ベスト) の実績 vs 対照 f81635d02363
score mean +19% / T14+到達 +7pp(28%→) / pair-rate +10pp(36%→47%) / Russia率 5倍(1%→5%)。n=121で安定(score_med 1315, mean 1535, T14pair 5%, Russia 5%, floor<1150 36%)。

### EXP-6 棄却の記録（2026-06-15, n=21）
EXP-6(MID_NUCLEUS_COMPLETE, a4b6bfb84d88)を06:13デプロイ→07:31にn=21で判定。**T14pair 5%(1/21) vs EXP-3 5% = 改善ゼロ**。precursorの highpair はむしろ低下(33% vs 40%)=EXP-4型のペア形成阻害シグナル。T14+到達/meanは上振れ気味だがn=21ノイズ・score_med/turns/floorは同点。pre-registered基準「n≥20でT14pair改善なし→rollback」に従い revert。→**中ティアassembly仮説は不成立。中ティア集積は全形態が再試行禁止リスト入り**(§3)。

---

## 2. 運用モード: ライブA/B実験

ユーザー指示「スコアが向上していないならどうしてholdするのか」(2026-06-13)を受け、HOLD既定 → **能動的実験モード**に変更。
各パス:
1. HEALTH (§0B)
2. MEASURE: EXP対象 vs 対照を実ゲームで比較（下記スクリプト）
3. IMPROVE: 実測診断に基づく bounded 変更を**1つ**。オフライン検証（replay harness, crash 0 & flip が対象局面限定）後にデプロイ。証拠なければHOLD
4. REPORT: 簡潔な текст + `say -v Kyoko -r 210 "..."`（主語=メリケンAI）

### デプロイ手順（pin張替え。manual-strategy-deploy-active-branch-pin メモリ参照）
```bash
NEW_HASH=$(python3 extract_decide_hash.py tmp/replay_20260612/edited_X.py)
cp tmp/replay_20260612/edited_X.py "strategy_versions/by_hash/${NEW_HASH}.py"
cp tmp/replay_20260612/edited_X.py "strategy_versions_archive/by_hash/${NEW_HASH}.py"  # permanent(pruneされない)
python3 -c "import json; p='tmp/state/active_branch.json'; d=json.load(open(p)); d['head_hash']='${NEW_HASH}'; json.dump(d,open(p,'w'))"
cp tmp/replay_20260612/edited_X.py strategy.py
git add strategy.py && git commit -m "..." && git push origin main   # strategy.py のみ。-A 厳禁
# ライブ採用確認: tail -1 game_history/latest.jsonl の strategy_hash が NEW_HASH になるまで待つ
```
### ロールバック手順
```bash
python3 -c "import json; p='tmp/state/active_branch.json'; d=json.load(open(p)); d['head_hash']='d5fff9501436'; json.dump(d,open(p,'w'))"
cp strategy_versions_archive/by_hash/d5fff9501436.py strategy.py
git add strategy.py && git commit -m "revert ..." && git push origin main
```

---

## 3. 実験ログ（何が効いて何がダメだったか）

| 実験 | 内容 | 結果 | 教訓 |
|---|---|---|---|
| pair-corridor/wedge/press スタック | 高ティアペアの保護・圧縮 | **採用済(f816に内蔵)** | T14+を16%→28%に。pair併合機構は稼働 |
| EXP-1 | HIGH phase height_mult 1.8→2.4(寿命延長) | **棄却** n=49全基準違反 | 高さ規律強化は高ティア建設を抑制。寿命延びず |
| EXP-2 | merge優先 merge_mult 1.0→1.2 | **棄却** 31701ターンreplayでflip 0=no-op | 併合選択は飽和。優先度上げは無効 |
| EXP-2b | T8-12同type集積強化(高nucleus方向) | **棄却** n=21床割れ・pair-rate逆行 | 高ティア方向の集積は高さ追加で有害 |
| **EXP-3** | **LOW_DRAIN_CLUSTER**: T1-7非併合を高さ安全時のみ低相手へ寄せ+200 | **★採用確定★** n=55 | 散在低ピース予防排出→盤面余裕→建設↑。**低い相手に寄せる=高さ上げない**のが鍵 |
| EXP-4 | LOW_DRAIN +200→+300(排出強化) | **棄却** n=22 pair-rate半減 | 排出強すぎると低集積を過剰優先し建設を奪う。**最適は+200** |
| EXP-5 | LOW_DRAINを高phase(max_y≥2.0)へgap-fillで拡張 | **棄却** n=37 2nd-T14/Russia=0% | 高phaseの着手は高ティア完成に使うべき。**EXP-3のmargin≥0.5ゲートは正しい** |
| **EXP-6** | **MID_NUCLEUS_COMPLETE**: T11-12非併合を、高ティア(>=13)が既に盤上にある時のみ、height-safe(margin>=0.5)で最近傍同type相手へ寄せ+200 | **棄却** n=21 T14pair改善ゼロ(5%=5%)・highpair低下(40%→33%) | offline検証はクリア(crash0/flip17全in-scope)だが**ライブで効果出ず**。中ティアassembly仮説不成立。highpair低下=EXP-4型ペア阻害。**中ティア集積は全形態禁止確定** |

### 再試行禁止リスト
broad/narrow build-beside（両方失敗）、height_mult増、merge優先増、**高ティア集積強化（高nucleus方向=EXP-2b失敗）**、**中ティア集積=全形態（EXP-2b高nucleus + EXP-6 height-safe低相手寄せ、両方失敗で確定）**、drain強化(+300)、drainの高phase拡張、next_next埋没回避(証拠0)。
※**結論: strategy.pyのドロップ位置/集積レバーは汲み尽くした**。次の前進は analyze_board.py の新特徴(§4)か、全く別角度。

---

## 4. 次の標的（ファネル診断 2026-06-15）

EXP-3 のソ連ファネル(n=83):
- T13到達 83% → T13ペア 42%（リーク51%）
- T14到達 31% → **T14ペア 5%（到達→ペア15%＝最大リーク）**
- Russia 6% → ソ連 0%

**ボトルネック = 2個目の高ティア形成**。T13ペア→T14変換は74%と優秀(press/corridor機構が効く)。問題は2個目の高ピースが形成されないこと。

### 診断の更新 (2026-06-15, EXP-6の根拠)
従来は「2nd-T14は生存ターン数で決まる(形成58 vs 失敗34)＝throughput問題」と見ていたが、**より深い計測で覆った**:
- 形成組と失敗組の**死因シグネチャは同一**(max_y 3.2/低ピース~27/deadline crossed/高ティア>=13が2個)。違いは生存ターン長のみ。
- 失敗組18件中15件が **[T14, T13]** を抱えて死(2個目の高ティアは孤立T13)。
- **決定打**: 失敗組の96%が死亡時に **>=1.0 T13相当の未併合中ティア材料**(中央値2×T12)を抱えている。形成組(1.50)より失敗組(2.25)の方が**材料は多い**。
→ **2個目高ティアは「材料不足(throughput)」でなく「材料が散在して未組立(assembly)」が真因**。2×T12が併合→T13できれば[T14,T13,T13]→2nd-T14。これが EXP-6 の設計根拠。

**ただし**「生存延長で2nd-T14を増やす」直接アプローチは EXP-1(height)・EXP-4(drain強)・EXP-5(高phase drain)で全滅(生存延長と建設はトレードオフ)。EXP-6 はその罠を避け、散在中ティアを**height-safeな時だけ**早期に組み立てて散らからせない方向。

### プラトー確定 (2026-06-15 08:31, EXP-6棄却後)
**EXP-4/5/6 が3連続で EXP-3 を超えられず**、drop-heuristic アプローチは天井に到達:
- EXP-6の教訓: 同type誘導を**足すとペア形成軸(PAIR_PRESS/SOVIET_NUCLEUS_GROWTH/axis9.6b)と干渉**しhighpairが下がる。盤面は微妙な均衡で、軸追加=ノイズ。
- 「散在材料を寄せる(assembly)」は効かない。理由は[[clutter-death-dominant-but-undrainable-2026-06-14]]の通り**埋没した相手は動かせない**(84%)。EXP-6が実証。
- **埋没予防は既にモデル済**: strategy.py axis 5.5b AVOID_BURY_MERGEABLE(L1666)が、partner(count>=2)を持つT10+を埋める着手に -(type-9)*120(T14=-600)。→「埋没度をanalyze_boardに足す」案は**冗長**。再調査不要。
- analyze_board.py には bury/reach/cover/exposed の独立計算は無い(埋没判定はstrategy.py inline)。

**結論: strategy.pyのドロップ位置/集積/埋没予防レバーは汲み尽くした**。残る前進角度はいずれも高リスク・多パス要:
(a) analyze_board.py の richer特徴(ドリフト予測精緻化等。pin保護なし・全決定即時影響・慎重設計必須)、(b) multi-turn lookahead強化(next_next埋没回避は証拠0で棄却済)、(c) EXP-3を現アプローチの天井と受容。
→ **ユーザー相談事項**(russia-drought memoryの「1-2改善で回復しなければ相談」を超過): 高リスク frontier(a/b)に多パスかけて挑むか、(c)現状維持か、別方針か。autonomous cronは相談を待たず EXP-3 で安定運用を継続。

---

## 5. 計測スクリプト（コピペ用）

### EXP vs 対照の主要指標
```python
python3 - <<'EOF'
import json, glob, os, statistics
from collections import Counter
seen = {}
for p in glob.glob('tmp/replay_20260612/corpus/2026*.jsonl') + glob.glob('game_history/2026*_score*.jsonl'):
    seen[os.path.basename(p)] = p
def sane(p): return abs(p.get('x',0))<=6 and abs(p.get('y',0))<=8   # 盤外グリッチpiece除外(必須)
def measure(target):
    g={'games':0,'scores':[],'turns':[],'t14':0,'russia':0,'pair_games':0,'pair14':0}
    for name in sorted(seen):
        try: lines=[json.loads(l) for l in open(seen[name]).read().strip().split('\n')]
        except FileNotFoundError: continue
        last=lines[-1]
        if last.get('strategy_hash')!=target or (last.get('score') or 0)==0: continue
        g['games']+=1; g['scores'].append(last.get('score') or 0); g['turns'].append(last.get('turn') or 0)
        if last.get('russia_created'): g['russia']+=1
        ps=[p for p in ((last.get('state_snapshot') or {}).get('pieces') or []) if sane(p)]
        ts=[p['type'] for p in ps]
        if ts and max(ts)>=14: g['t14']+=1
        had=False; had14=False
        for d in lines:
            pp=[p for p in ((d.get('state_snapshot') or {}).get('pieces') or []) if sane(p)]
            cc=Counter(p['type'] for p in pp if p['type']>=13)
            if any(v>=2 for v in cc.values()): had=True
            if cc.get(14,0)>=2: had14=True
        if had: g['pair_games']+=1
        if had14: g['pair14']+=1
    return g
for tgt,lab in (('ba5935ce2a9a','EXP-9(current best)'),('d5fff9501436','EXP-3(deep baseline)')):
    g=measure(tgt)
    if g['games']:
        print(f"{lab}: n={g['games']} score_med={statistics.median(g['scores']):.0f} mean={statistics.mean(g['scores']):.0f} max={max(g['scores'])} turns_med={statistics.median(g['turns']):.0f} T14+={100*g['t14']/g['games']:.0f}% pair-rate={100*g['pair_games']/g['games']:.0f}% T14pair={g['pair14']}({100*g['pair14']/g['games']:.0f}%) russia={g['russia']}({100*g['russia']/g['games']:.0f}%)")
EOF
```

### オフライン安全検証（replay harness, デプロイ前必須）
```python
# tmp/replay_20260612/replay_harness.py を import。crash 0 & flip が対象局面限定を確認。
python3 - <<'EOF'
import sys
sys.path.insert(0, '/Users/azumag/azumag/work/soren')
sys.path.insert(0, '/Users/azumag/azumag/work/soren/tmp/replay_20260612')
from replay_harness import load_strategy, build_analysis, enrich, iter_turns
from analyze_board import TYPE_RADII
A = load_strategy('strategy.py','a'); B = load_strategy('tmp/replay_20260612/edited_X.py','b')
total=ca=cb=flips=fin=fout=0
for fname, d, pieces, nt, nnt in iter_turns():
    gs={"pieces":pieces,"next":{"type":nt,"r":TYPE_RADII.get(nt,0.5)},"nextNext":{"type":nnt,"r":TYPE_RADII.get(nnt,0.5)} if nnt is not None else {},"score":d.get("score",0),"state":"MOVE"}
    try: an=build_analysis(gs)
    except: continue
    gs=enrich(gs,an); total+=1
    inscope = True  # ← 変更の対象局面条件をここに
    try: xa=float(A.decide(dict(gs),an)['x'])
    except Exception as e: ca+=1; continue
    try: xb=float(B.decide(dict(gs),an)['x'])
    except Exception as e: cb+=1; continue
    if abs(xa-xb)>1e-9:
        flips+=1; fin+=int(inscope); fout+=int(not inscope)
        if not inscope: print("OUT-OF-SCOPE", fname, d.get('turn'))
print(f"turns={total} crashes A={ca} B={cb} flips={flips} (in-scope={fin}, out={fout})")
EOF
```
大規模replay(数万ターン)は run_in_background で実行 → 完了通知を待つ。

---

## 6. 重要な落とし穴・既知事項

- **盤外グリッチpiece**: T14等が物理バグで盤外(y=44000等)へ飛ぶ。全計測で `sane()`(|x|≤6,|y|≤8)フィルタ必須。さもないと偽2×T14。
- **証拠保全コーパス**: `game_history/` は ~13ファイルしか保持しない(古いゲームは消える)。**毎パス冒頭で `cp -n game_history/2026*_score*.jsonl tmp/replay_20260612/corpus/`** を実行。replay harnessはcorpus+live両読み(corpus優先、retentionレース耐性あり)。
- **logs/soren_loop.log は日付なし複数日appendログ**。生grepのcumulativeカウントを「直近」と誤読しない。健全性はdated game_history/score_history.txt で裏取り。
- **score_history.txt**: ISO8601+09:00 タブ区切り。`datetime.fromisoformat`でparse。
- **ScheduleWakeup は呼ばない**（cronが毎時発火を担当。呼ぶと1h上限に戻る）。
- **cron**: 毎時 :23 発火（session限り・7日失効、要再作成）。プロンプトに全手順埋め込み済み。
- **制約**: OBS/配信/worker起動禁止。strategy.py と analyze_board.py のみ改善。commit は strategy.py のみ(`-A`厳禁)。
- **インフラ障害履歴(2026-06-13〜14)**: 外部ボリューム /Volumes/satelite (homebrew=python3/tmux搭載) が消失し /opt/homebrew が宙ぶらりんに→strategy_runnerが起動できず~31時間 score 0。ユーザーがディスク再接続で復旧。再発時はsatelite再接続 or「システムpythonで復旧」(/usr/bin/python3 3.9.6でも戦略スタックは動く)。

---

## 7. 関連メモリファイル

`/Users/azumag/.claude/projects/-Users-azumag-azumag-work-soren/memory/`:
- `clutter-death-dominant-but-undrainable-2026-06-14.md` — 詰まり死診断・EXP-3勝利・EXP-4/5棄却・ファネル
- `pair-corridor-protect-2026-06-12.md` — pair保護/press スタックの全経緯
- `manual-strategy-deploy-active-branch-pin.md` — pin張替えデプロイ手順
- `daily-pass-soren-loop-log-is-dateless-append.md` — ログ落とし穴
- `soviet-strategy-pass-cadence.md` — 毎時+毎回改善のケイデンス指示
- `lookahead-infeasible-physics-merges-2026-06-16.md` — lookahead非現実的の実証
- `param-parallel-bugs-issues-93-94.md` — param並列の既知バグ#93(修正済)/#94(オープン)

---

## 8. 抜本策ワークストリーム (2026-06-16〜, ユーザー指示)

ユーザー指示(2026-06-16)「全然伸びてない、もっと抜本的に。あと定期的に並列パラメータ調整も」を受けた作業。AskUserQuestionで方針確定:
- **param並列**: 「ドライラン検証→定期有効化(推奨)・初回は監視下で」
- **抜本策**: 「両方を順に/おまかせ」(まずlookahead、頭打ちならanalyze_board)

### (1) lookahead抜本策 → ❌ blocked確定 (2026-06-16)
decide()複数手先探索の実現可能性ゲートを検証 → **物理併合が予測不能で非現実的**。詳細は [[lookahead-infeasible-physics-merges-2026-06-16]]。要点: 自作drop-simulatorをJSONL ground truthで検証 → 併合予測 drift-aware版でも acc61%・予測15%vs実際35%・混雑エンドゲームでも改善せず。併合の大半はsettling/drift/cascade物理で発生し盤面から予測不能。**だからdecide()は単手heuristic(設計は正しい)**。検証コード tmp/replay_20260612/sim_validate{,2}.py。

### (2) param並列 段階的再有効化プラン
現状: `POST_IMPROVE_PARAM_PARALLEL_ENABLED=0`・`WILDCARD_ENABLED=0`(2026-06-03クラッシュ後無効)。#93(apply非原子)=修正クローズ済、#94(Chrome/OBS不安定)=修正コードありだがライブ未検証=オープン。
- **Step1 simulateドライラン ✅完了clean (2026-06-16 14:09)**: `python3 wildcard_parallel.py --evaluate-mode simulate --jobs 3 --games 3` を**全パス隔離**(session-root/status/result/rolling/current-run を tmp/wildcard_dryrun/ に向ける)で実行 → `{"ok":true,"winner_selected"}`・100param摂動→eval→cull→winner選定が無crashで完走。**live state無変更を実測確認**(strategy.py md5一致・pin=d5fff9501436不変・隔離パス使用)。wildcard_parallel.py自体はlive書込/adoptをしない(winnerをresult-fileに報告するだけ、adoptはeloop_improve.sh側)。orchestration(#93経路)健全。
- **Step2 real-modeドライラン ✅完了clean (2026-06-16 15:44, 配信OFF時に実施)**: ユーザー「やっていいよ」「今はもう配信してない」を受け実施。`--evaluate-mode real --jobs 2 --games 1`(隔離パス・OBS sources無効・10分cap)。結果 winner=cand-3-r6(EVAL 12669)・cand-2-r12(8389)が実ゲーム完走。**#94 Chrome-launch経路クリア**: 候補Chromeが本線ゲームChromeと同時起動してもSIGABRTなし(chrome_launch_lock有効)。**ただし重要発見**: wildcard_failures.jsonl は failures=36 (35×`culled: decide_exception` + 1 failed) を記録。result.jsonの「failures=0」はインフラ障害のみカウントで、実態は**37候補中35が摂動strategy.pyのdecide()例外で自滅(~95%)**。原因: 100パラメータを20-40%振幅で同時摂動するとどれかが必ずcomplex/div0/index等の例外を起こす(cf [[strategy-complex-float-crash-and-baseline-cull-bypass]])。→ **param並列インフラは安定だが、摂動探索は極めて非効率。生存候補も「tuned済EXP-3の大規模ランダム変異」で改善見込み薄**。productiveに使うには摂動を穏やかに(param数↓・振幅↓)する設定変更が要る。**実測クリーン**: live hash=pin=d5fff9501436不変・strategy.py md5不変・孤児プロセス0・候補Chrome全cleanup(cdp port解放)・本線ゲーム健全継続・OBS未起動。**⚠caveat: OBSが落ちていたため#94のOBS-source-race(obs_source_lock/SetInputSettings)は「OBS down時のgraceful失敗」しか検証できていない。配信再開(OBS up)時の初回realランでOBS-source raceを要監視**。trace: tmp/wildcard_dryrun_real/, logs/wildcard_failures.jsonl。
- **Step3 フェア実証ラン ✅完了 (2026-06-16 18:31-19:11, ユーザー「やる」承認)**: 穏やか摂動(`WILDCARD_PERTURB_RANDOM_COUNT=0` --count 3 --ratio 0.05-0.15, **jobs=4**[6だとメモリパンク, [[param-parallel-manual-jobs-4-memory-2026-06-16]]])で1ラウンド。**decide_exception 0件**(摂動穏やか化で95%→0%)・メモリ56%回復・本線無傷。**ただし勝者はノイズ選抜**: 信頼できるn=5候補(med~11000-11160)は EXP-3 baseline(~11627)と同等か下、勝者cand-3-r3はn=3で運の良い2ゲーム([14172,17208,6664])で選ばれただけ。勝者d841bdcb3a42 = EXP-3 + 実質1tweak(L1435 span 0.5→0.3735; L816/L2301はedge/no-op)。
- **EXP-7 棄却 (live A/B n=20で revert, 2026-06-16 20:39, commit f3e9dfaa2)**: 勝者d841bdcb3a42をライブA/B→n=20判定。score_med 1360 vs EXP-3 1293・floor 25% vs 38% は良いが**n=20ノイズ内**。**T13+ はむしろ低い(70% vs 82%)**・ソ連ファネル(T14+/T14pair/Russia/T15)は同等/1-of-20ノイズ。**統計的にEXP-3と区別不能・ソ連関連指標で改善なし**。pre-registered基準「明確に超えなければrollback」に従い d5fff9501436 へ revert。
- **★結論(2026-06-16): param並列はソ連プラトーを破らない＝実データで確定★**: 信頼できるn=5候補が全てEXP-3同等、勝者はn=3ノイズ選抜、ライブA/Bでも区別不能。lookahead(物理で予測不能)に続き、**ドロップ位置/パラメータ調整の路線は天井に到達。ソ連到達には別アーキテクチャ(実エンジン探索の学習基盤等、数週間規模)が必要** ← ユーザー方針待ち(選択肢C)。

---

## 9. 全力ソ連到達ワークストリーム (2026-06-17〜, ユーザー指示「ソ連できる最後まで一気に」)

方針: 2nd-nucleus形成を阻む構造的天井に、新機構(option3)＋大規模最適化(option2)で反復挑戦。確認では止まらず自律実行・検証は継続。判定指標=**2nd-nucleus funnel(T13pair/T14pair/Russia)**。

### 診断 (2026-06-17, EXP-3 T14到達 n=172)
2nd-T14形成は11/172(6%)のみ。**first-T14時点の盤面状態が決定的**: 形成組 max_y 0.24・survival_after 53、失敗組 max_y 0.50・survival_after 35。低クラッター 16 vs 17(同等)。→ **T14到達時に盤面が高い(=2nd核を作る空間がない)と失敗**。死亡時は両組とも盤面full(tautology)。

### EXP-8 SECOND_NUCLEUS_ZONE (live=ff80c0b2fce6, 2026-06-17デプロイ, A/B中)
高ティア(>=13)存在時、最高ピースの反対側に1.4幅のgrowth zoneを予約: ZONE_CLEAR(低クラッターをzoneから遠ざける+150)・ZONE_BUILD(T11-12材料をzoneへ寄せる+250)。height-safe(margin>=0.5)。offline replay crash0/0・flip 477/7630全in-scope。**効果未測定**。判定: n≥25でT14pair funnelがEXP-3(T14pair~5%)を明確に超えなければ即ロールバック→d5fff9501436。素朴build-besideと違い動的zone・高ティア出現後限定。

### EXP-8 棄却 (2026-06-17 18:47, n=32, revert commit 70d238c33)
T13pair 31% vs EXP-3 35%(2nd-nucleus precursorがむしろ低下)・T14pair 3% vs 2%(ノイズ)・score_med 1172 vs 1313・floor 50% vs 39%＝改善なし/やや悪化。zone機構がEXP-3のバランスに干渉(EXP-6と同じ失敗クラス)。**教訓: 追加drop-position軸(EXP-6/8)は干渉する。directional clutter routingは清浄空間を作らずクラッターを別所に集約するだけ**。→ d5fff9501436へrevert。

### option2 大規模最適化ラン (2026-06-17 18:49開始, pid記録なし=2h cap)
EXP-7より本格的に: jobs=4・--games 8(ノイズ減)・--count 4・gentle摂動(RANDOM_COUNT=0,ratio0.05-0.15)・cull-after-4・2h cap・全パス隔離(tmp/wildcard_opt2/、auto-adopt無し)。配信OFF・mem 28%でjobs=4稼働。狙い: 局所最適周辺をEXP-7(n=5)より低ノイズで探索。**次パスで result.json の勝者を確認→offline crash0検証→ライブA/B(n≥30)でEXP-3と比較**。勝者がノイズ選抜(EXP-7同様)なら採用せず。
※次の機構方針: 追加drop-position軸は干渉で全滅(EXP-6/8)。次はanalyze_board新特徴(option3, 既存field不変・追加のみ)か、本ラン結果次第。

### ⚠ 注意(2026-06-17): opt2稼働中は本線ゲームが自動PAUSEされる
param並列(opt2)実行中、メインループが `[PAUSE] param並列調整中(隔離評価)` を出して**本線ゲームを意図的に停止**(設計通り・contention回避)。soren_loop.log参照。runnerがidle/新game_historyが増えないのは**stallでなくpause**。opt2プロセス終了で自動un-pause(orphan-guard)。→ opt2稼働中の毎時パスでは「本線game_historyが増えてない=異常」と誤読しないこと。opt2候補ゲームはtmp/wildcard_opt2/sessions配下で進行中(SOVIET monitorはlogs/soren_loop.logのみ監視ゆえ候補Soviet非検知→opt2 result/statusを直接確認)。

### EXP-9 デプロイ (opt2勝者, live=ba5935ce2a9a, 2026-06-17 20:46, commit cd003c620, A/B中)
option2大規模ラン(jobs=4/games=8/2h)の勝者 cand-3-r6: n=8 median 12627・**floor games 0**(EXP-7のn=3ノイズより本物寄り)。5変更のうち効くのは **L68 FAST_DROP_DEADLINE_CONTACT True→False**(strategy_runnerが毎ゲームgetattrで読む実runtime param: False=デッドライン接触時もsettle待ち=より慎重=survival延長狙い)+L1862 bonus 3000→3289・L1421 reactive閾値5→6。offline replay: decide() crash0/0・flip 5/7603(0.07%=decideはほぼ同一、効くのはruntime flagでreplay非対象)。判定: n≥25で2nd-nucleus funnel(T13pair/T14pair)・survival(turns)・score_medが EXP-3 を明確に超えなければ即ロールバック。**FAST_DROP=Falseがsurvivalを延ばすか**が見どころ。

### EXP-9 早期シグナル★有望 (2026-06-17 21:47, n=13, 判定閾値未満)
turns_med **107 vs EXP-3 84(+27%)**・score_med 2046 vs 1313・T14+ 38% vs 25%・T13pair 38% vs 35%・floor 23% vs 38%。**FAST_DROP=False(デッドライン慎重化)がsurvivalを延ばしている**(mechanistic=信頼できる)。**但し**: (1)n=13<25で未判定 (2)究極指標 T14pair 0/13・Russia 0/13 は未だ動かず(rare event, n≥25+要) (3)比較confound: EXP-9はopt2終了後の非contended、EXP-3 baselineは一部contended履歴混入で過大評価の可能性。→ n≥25まで継続観察、**特にT14pair/Russiaが実際に増えるか**を見る。これが本物なら今セッション初の実進展。

### EXP-9 判定: 採用(modestly better, 2026-06-17 22:47, n=29)
n=13の劇的シグナル(turns+27%/score+56%)は小サンプル+contention confoundで誇張だった→n=29で縮小。最終: score_med 1436 vs 1313(+9%)・**floor 28% vs 38%(disaster減)**・turns 87 vs 84・T13+86%/T14+28%/T13pair38%(全てやや上, noise内)。**ソ連funnel(T14pair 1/29・Russia 1/29)は EXP-3(2%/2%)と同等〜やや上だが明確改善ではない**。→ **better-or-equalかつ低リスク(FAST_DROP=Falseは実runtime tunable・survival整合)ゆえ採用=新working baseline**。深いfallback=EXP-3。次: EXP-9上に option3機構 or 追加 option2。延びたsurvivalを2nd-nucleus建設に変換させるのが課題(T13pair微増だがT14pair未変換)。

### ★EXP-9 Russia上昇シグナル (2026-06-17 23:47, n=44)
score_med 1548 vs 1313(+18%)・turns 92 vs 84・T14+ 32% vs 25%・T13pair 39% vs 35%・floor 25% vs 38%・**Russia 3/44=7% vs EXP-3 2%(~3×!)**。survival延長(FAST_DROP=False)が2nd核→Russiaに変換され始めた兆候。funnel全体が一貫して上昇。T14pair snapshotが2%なのは2nd-T14ペアが即T15/Russiaに合体してcoexistence検出を逃れるため(Russiaが真の信号)。**caveat: n=44/Russia3件でnoisy(CI広)・contention confound残**。→ **EXP-9を走らせ続ける(opt runで止めない)。7% Russiaなのでライブゲームこそ最良のSoviet狙い(=2×Russia)**。monitorがSoviet監視中。n蓄積で確認。次の更なる改善はEXP-9上にoption2/3。

### EXP-9 n=61更新 + option2 round2起動 (2026-06-18 00:49)
EXP-9 n=61: score_med 1466(+12%)/turns 90(+7%)/T14+ 30%/floor 33% は安定して better。**但しRussia uptickは退行(7%@44→5%@61, 直近17ゲームで新Russia 0)＝早期3件は一部luck。Russia/Soviet改善は未確認**。EXP-9は確実な better baseline。runtime behavior switchはFAST_DROP唯一で既に最適化済(他にgetattr(strategy)無し)。scoring軸はinterfere(EXP-6/8)。→ productiveなoption2を継続: **EXP-9をbaseに option2 round2起動**(pid記録、jobs=4/games8/count5/2h cap/隔離 tmp/wildcard_opt3/)。完了時に勝者をoffline検証→A/B。次パスでopt3結果確認。

### ユーザー「併合無視」観察の徹底調査結論 (2026-06-18)
ユーザー「明らかに併合できれば助かるのに無視して別所に置く/どう見ても失敗」。3角度×4緩和で精査:
- 検出緩和(NEAR contact_gap 0.04→0.20): flip 1.5% (ほぼno-op)
- HARD SUPPRESS緩和(max_y 2.5→2.8, EXP-10): flip 0
- HIGH_MAX_Y_NEAR penalty 600→300 (EXP-11): flip 0
- NEAR_DEADLINE_RISK 300→120 + HIGH_MAX_Y 600→200 (EXP-12): flip 0
**結論**: 全緩和がno-op=「危険域で取れるNEAR併合」は実データで稀。「同type至近でNO」の**75%は埋没target(満杯盤で着地が相手の2近く上=物理的に届かない)**、残りNEARも合体で背が伸びる/31.5%失敗。**ユーザーの観察は構造的clutter問題(埋もれた2個目核は動かせない)の可視化であって、修正可能な併合ロジックbugではない**。EXP-10/11/12は未デプロイ(no-opゆえ)。merge-tuning路線は打ち止め。残る道: 構造問題を別角度(analyze_board埋没度特徴 or 受容)。live=EXP-9維持。

### EXP-9 確定 (n=96, 2026-06-18 03:47) + セッション総括
EXP-9: score_med 1451(+11%)/floor 30%(vs38%)/turns 89/T13pair 38% は安定して better baseline。**但しRussia 3/96=3% ≈ EXP-3 2.3%(早期7%はluck, 7→5→3%と退行)＝ソ連funnel非改善**。EXP-9は「一般的に良い土台」だがソ連breakerではない。
**セッション総括(全力ソ連push 2026-06-17〜18)**: 試した打開策と結果:
- lookahead: 物理予測不能でblocked
- param並列×2(opt2/opt3): EXP-9(FAST_DROP=False, 実runtime lever)=唯一の実成果(modest)。他winnerはnoise選抜
- 追加scoring軸(EXP-6 mid-assembly/EXP-8 zone): EXP-9balanceに干渉して棄却
- merge-tuning(EXP-10/11/12, ユーザー観察起点): 4緩和全no-op=buried-clutterが本体で修正不能
- AVOID_BURY(埋没予防)は既存(axis5.5b)
**結論: ドロップx制御で到達可能なレバーはほぼ汲み尽くした。ソ連(2×Russia)は構造的に困難で、EXP-9(best baseline)でも~3%Russia/0%Soviet。** 残る現実的進展は (a)EXP-9を最良として長時間走らせSoviet監視(numbers game) (b)analyze_board埋没度特徴(高risk・AVOID_BURY冗長の懸念) (c)受容。live=EXP-9維持・monitor継続。

### EXP-13 デプロイ (AVOID_BURY lone-seed拡張, live=2da8233f49d5, 2026-06-18, A/B中)
merge調査の核心「2nd核失敗=材料の埋没(75%)」に対し、既存axis5.5b AVOID_BURYがpartner有(count>=2)しか保護しない点を突く: **孤立した高ティアseed(lone T13+, count==1)=2nd核の種を埋没から保護**(half penalty (_st-9)*60)。失敗組が[T14,孤立T13]で死ぬ→そのlone T13を埋めない。EXP-9上に構築、既存軸の拡張ゆえEXP-6/8の新軸より干渉小。offline crash0/0・flip 155/10016(1.5%, 実挙動変化=merge-tuning no-opと違い本物)。**判定: n≥25でT13pair/Russia funnelがEXP-9を超えるか。ダメならEXP-9へrollback**。これが効けば「seed保護で2nd核形成↑」が裏付く。

### EXP-13 棄却 (2026-06-18 07:47, n=32, revert ae6203206)
全指標悪化: T13pair 22% vs EXP-9 38%(target)・score_med 1202 vs 1456・T14+ 19% vs 29%・floor 41% vs 26%・Russia 0/32。seed保護仮説は失敗=lone seed埋没penaltyが**配置を過剰制約し建設を減らす**(EXP-6/8と同じ干渉)。→EXP-9へrevert。
**★確定パターン: scoring軸の追加/拡張は EXP-9 の tight balance に干渉して全部悪化(EXP-6/8/13)。buried-clutter天井はburial penaltyでは破れない。** 効いたのは非scoring runtime switch(FAST_DROP/EXP-9)のみ=これは唯一で枯渇済。→ ドロップx制御の全レバー(scoring軸/merge-tuning/runtime switch/param並列)を実測で汲み尽くした。EXP-9が実用上の最良。残=数の勝負(EXP-9走らせSoviet監視) or 受容。

### shapes方法論的見落とし→再検証で結論は維持 (2026-06-18)
**重大発見**: 全offline replayが shapes={} で回っていた(game_history JSONLはshapes非保存、live game_state.jsonは保存)。実ゲームはruntime shapesでドリフト予測。→merge-tuning no-op等の結論が偽陰性の懸念。**実形状(tmp/replay_20260612/live_shapes.json, types1-8,10,13)で再検証**:
- 実形状でdriftは65%発火(no-shapesは0)＝確かに条件が違った。但しdrift予測量~0.07 vs 実drift~0.35(5×過小)・着地誤差は同じ(median0.35)。drift方向は66%正解だが**増幅(×3-7)すると誤差悪化**=実scatterは大半irreducible物理ノイズ。
- merge-tuning(EXP-12)は実形状でもflip~0(smoke 1file=0)。
**→結論維持: 物理予測はirreducible(lookahead blocked/merge失敗は本物)。shapes flawは結論を覆さず、より厳密に確認した。** 教訓: 今後のreplayは tmp/replay_20260612/live_shapes.json を gs["shapes"] に渡すこと(より正確)。

### EXP-14 VALLEY_SHAPE デプロイ (web research, live=5f2a6937363c, 2026-06-18, A/B中)
Suika専門家戦略「ダブルすいか(=ソ連)はU字/谷型で勝つ(両サイド高・中央低で大ピースをcradle)」を実装。低(T1-7)非併合height-safeクラッターをサイド(|x|>=1.3)へ寄せ中央を低く保つ(tie-breaker<=120)。EXP-8 zone(2nd核用に側を予約)と逆=中央をクリアに保つ。offline(実形状) crash0/0・flip 926/6140(15.1%, 強い実変化)。判定: n≥25でT13pair/Russia funnelがEXP-9超えるか。ダメならEXP-9へrollback。**research-backedな初の機構**。

### EXP-14 valley 棄却 (2026-06-18, n=35, revert cc28d1031)
T13pair 23% vs EXP-9 35%・Russia 0/35 vs 2%・floor 43% vs 31% = ソ連funnel悪化。**興味深い: T14+はUP(31%vs28%)だがT13pair DOWN** = クラッターをサイドへ寄せると中央に1本の高い山を作る(single-tier↑)が2nd核を阻害(意図と逆)。人間専門家のvalley戦略もこのAIには非転移。**★5回目のscoring軸失敗(EXP-6/8/13/14+zone)=scoring変更ではburied-clutter天井を破れないと確定。spatial操作(集中=valley/分散=zone)どちらも2nd核形成を助けない。** →scoring路線は完全に閉じた。残=別アーキ(RL/learning, rare-event問題あり)or受容。

### survival予測性マイニング (2026-06-18 23:46, EXP-9 T13到達 n=14)
「post-T13 survivalはcontrollableか(=レバー有)/luckか」を直接検証。T13到達ゲームをsurvival_after_T13でtercile分割:
- **SHORT(surv 28t)**: ft13=turn53(遅い)・pre-T13窓 max_y +0.38/低18/27個(混雑・高い)
- **LONG(surv 62t)**: ft13=turn28(早い)・pre-T13窓 max_y −2.38/低6.5/10個(クリーン・低い)
- **corr(ft13, survival_after) = −0.74**(強い): T13を早く・クリーンな盤で作る→長生存→ロシア。遅く混雑盤で作る→即死。
- **重要: early[0:30]では両群ほぼ同一**(max_y −2.0 vs −2.5・低5.6 vs 6.1)=差は**mid game(turn30-53)で発生**: SHORT組がT13到達までに27個/低18へbloat。
**解釈**: これは clutter-death説 + LOW_DRAIN(EXP-3/9)が正レバーである事を再確認(SHORT組の失敗=まさにLOW_DRAINが狙うmid-gameのclutter蓄積)。残るSHORT失敗=drainが追いつかない(undrainable/luck, [[clutter-death-dominant-but-undrainable-2026-06-14]])。**n=14小・luck交絡大ゆえrushな新軸はデプロイせず(EXP-9を壊す, 5回失敗済)**。LOW_DRAIN既に天井近傍=新知見だが新レバーは出ず。live=EXP-9維持。

### ★論文発見=ロードマップ修正: Monte Carlo前方シミュ (2026-06-19, web検索)
ユーザー指示「論文/ネット検索を尽くせ」でweb検索→**同一ゲームのLeiden大2024-2025修論「Creating an AI that plays Suika game」(Poelsma)を発見・全19頁精読**(theses.liacs.nl/pdf/2024-2025-PoelsmaJJulian.pdf)。15円タイプ・500px幅・score=c_t*2・overflow game over=完全に我々のゲーム。
**中央値スコア(n=30)**: Monte Carlo前方シミュ **6000–7353(最良)** > 人間 5076 > 線形回帰 3974 > Type-matching 3467 > **Deep-Q 2903 / Q-learning 2741(≒random 2259)**。
**3つの重大含意(私の従来計画を覆す)**:
1. **RLはこのゲームで実測最低(≒random)**。→ exp9メモで提案しかけた「RL基盤構築」は**誤りゆえ撤回**。
2. **勝者=Monte Carlo前方シミュ**: 状態保存→ドロップ→**実物理を3秒ロールアウト**→評価→リセット。「併合を予測せず実シミュ結果で選ぶ」。我々の"lookahead blocked"は**ヒューリスティック予測(61%acc)限定**の話で、**実物理シミュなら回避**とnuance修正。論文のMC eval(+3×消えた円/+1 高さ非増/+1 次type隣接)は我々のLOW_DRAIN/merge/AVOID_BURYとsettle待ち(EXP-9)を独立追認。
3. **SOTA(MC,人間超え)でも1個目T15すら確実に届かない**(論文「practically undoable, 場が先に埋まる」)。我々の目標はT15×2(ソ連)=**極端な困難さを独立研究が確認**。アプローチが悪いのでなく目標が既知フロンティア外。
**MC-sim実現可能性プローブ(同日)**: game_state.jsonは完全物理スナップショット(per-piece r,vx,vy,angle,av,awake)+next/nextNext(2手先=論文最良の2-turn MCに必要)+shapes(多角形)を露出=**前方シミュに完全可観測**。Unity 2D物理=Box2D下層ゆえcalibrated Box2D/pymunkロールアウトは原理上faithful可能。**但しpymunk/Box2D未install・Unity物理param抽出+calibration要・faithfulness未検証=最大リスク**。strategy.py/analyze_board.py外の基盤拡張ゆえ承認待ち。詳細 [[suika-thesis-montecarlo-beats-rl-2026-06-19]]。

### MC-sim フィージビリティ実証 第1弾 (2026-06-19, tmp/mcsim/, live無改変)
isolated venv(tmp/mcsim/venv, pymunk 7.3.0)+circle近似sim構築(tmp/mcsim/validate.py)。校正geometry: x∈±3.05・gravity−y・floor−4.25・ceil4.3・drop3.2・per-type半径は40game実測平均。**検証=実EXP-9連続ターン対(N→N+1)で sim(state_N+drop_N) と実state_{N+1} を比較**(decision_x/next_type/state_snapshot使用, n=120-150)。
- **第1版(merge閾値*0.92,deep-overlap)**: max_type一致 **55%**・max_y |err| **3.5**・piece_count |err| 2.0。gravity8-30/friction0.4-0.9に**ほぼ不感**=dynamicsでなくgeometry支配の兆候。
- **診断(predicted vs actual 12対印字)**: 失敗モード明確=**実ゲームで起きる「同type近接ドロップ→併合」をcircle simが取りこぼし→ピースが積み上がりmax_y上昇**(pred max_y +0.8〜+1.3 vs 実 −2.7前後)。1回の取りこぼしが下流を発散させる。[[lookahead-infeasible-physics-merges-2026-06-16]]と同根=併合は多角形接触/driftの物理依存。
- **第2版(merge閾値*1.05,contact)**: max_type **63%**(↑)・max_y |err| **2.13**(↑)・但しmean piece_count err 3.3(over-merge副作用)。
**結論(第1弾)**: research-backed方向(MC-sim)を実装し**実測検証**した。circle近似は tier一致63%/max_y err2.1=粗い。失敗=多角形接触/driftの取りこぼし。→多角形版へ。

### ★MC-sim フィージビリティ 第2弾=多角形sim=強い陽性 (2026-06-19, tmp/mcsim/validate_poly.py)
実shapes頂点(types1-13, tmp/mcsim/shapes_lib.json)を凸包化+per-piece回転(angle)で多角形rigid-body sim構築。同じN→N+1検証:
- **best cfg(g=20,fric=0.9,elas=0.05,mergefac=0.95)**: **max_type一致 86%**(circle63%から大幅↑)・**max_y |err| 0.10**(高さ範囲~7で<2%)・pc|err| 2.0。多角形物理がsettling/driftを捕捉=併合が正しく発火。
- **盤面充填度で層別(n=260)**:
  - sparse(<8): max_type **100%**・max_y err 0.03・pc err 0.0(完璧)
  - mid(8-15): max_type **98%**・max_y err 0.07・pc err 0.0(ほぼ完璧)
  - crowded(≥16): max_type 74%・**max_y err 0.07**・pc err 2.0
- **★決定的**: **max_y(=高さ=overflow=死=survival)予測は全regimeで±0.07と高精度**。crowdedでもtier/cascadeは弱る(74%)が高さは堅牢。tier予測はsparse/mid(2nd核建設の初期段階)でほぼ完璧。
**含意**: MC-simは**survival risk(overflow)を全状態で正確予測**できる=私が発見した「Russia=長survival」の唯一効くレバーを、scoring軸が engineer できなかったのに対し**前方シミュなら直接最適化できる可能性**。research(RL超え)・fidelity(実測)・狙うレバー(survival)が一致した**セッション最有望リード**。
**正直な留保(未検証)**: (1)単手fidelityは実証も**multi-step rolloutの誤差蓄積は未検証** (2)予測精度↑≠実ゲーム改善(MC-selector構築+A/B要) (3)**性能**: 50 rollout/turn×settleはlive実時間に重い恐れ(要最適化, deployment課題) (4)pymunk依存をlive投入する設計判断 (5)論文上SOTA MCでも1個目T15すら稀=survival↑でソ連odds上昇はするがT15×2保証なし。
**次の具体策**: MC-selectorプロトタイプ(各候補ドロップをsim→survival(低max_y)+progress(merge/tier)でスコア→最良選択)を構築し、**まずオフライン**で「heuristicと異なる/より良い選択をするか」を録画盤面で実測。良ければlive A/B検討(scope拡張・perf/依存に注意)。live=EXP-9維持・monitor継続。

### MC-sim 第3弾: merge検出bug修正で完全faithful化 + 1-turn selector検証 (2026-06-19, tmp/mcsim/)
**(a) sim完全faithful化**: 第2弾の弱点(crowded tier74%/pc err2)の原因を特定=**距離merge(circumradius r×0.95)が密piledで非接触の同type pairを誤併合**(43-piece盤で6.5 merges/drop、escaped=0でtunnelingでないと確認)。faithful fix=**effective radius(頂点の重心からの平均距離=真の接触距離, circumradius未満)でmerge判定**。pymunk shapes_collideは内部assertion bugで使えずEFF距離で代替。結果(factor=1.0):
- sparse 97% / mid 100% / **crowded 99%** max_type一致・**pc err 全regime 0.0**・max_y err 0.04-0.13・crowded merges/drop median0(realistic)。→ **全盤面状態でtier/count/heightを正確予測する検証済み前方モデル完成**(validate_poly.py)。
**(b) 1-turn MC-selector検証=陰性(正直)**: 修正simで各候補xをsim→score(3*merge−1.5*maxy+2*tier_gain)→最良選択し、heuristic(EXP-9)の実choiceとsim内部比較(n=80, crowded優先)。**MCが盤を低く保つのは45%のみ**(heuristicが55%で勝つ)・max_y差median−0.03(無視可)・merges heur0.88 vs MC1.14。→ **EXP-9は既にsurvival志向のドロップ選択が優秀で、faithful 1-step前方モデルでも上回れない**。1-turn MC≈heuristic確定。
**(c) 残る本命=2-turn rollout(未検証)**: 論文の**最良は2-turn MC(7353)** > 1-turn(6234)。nextNextを使い「今ここに置けば次ピースで併合完成」という**単一決定heuristicが構造的にできない**setupを評価。faithful simで今や可能(以前の"lookahead blocked"はheuristic予測限定、faithful simなら別)。**次=2-turn MC selectorを実装しheuristicと比較**。これが1-turnと同じく≈なら「MC路線も天井」、勝てば初の真の打開候補。**正直: simは完璧でも1-turn選択は無益と判明=期待値は前パスより低下。2-turnが最後の本命。** live=EXP-9維持。

### MC-sim 第4弾: 2-turn rollout=marginalで≈EXP-9 + ワークストリーム総括 (2026-06-19, tmp/mcsim/selector2_test.py)
論文最良の**2-turn MC**(next+nextNext, 各x1→sim→各x2→sim→2-deep盤をscore→最良x1)をheuristicのmyopic x1(同じ最適x2継続で2-deep評価)と比較(n=30,crowded優先,9候補):
- 2-deep max_y(MC)−(heur): median **−0.053**・mean −0.164・**MC低盤50%(引き分け)**・≥0.3低い 23%(1-turnの16%よりやや上)・merges delta median0/mean+0.5。
- **2-turnは1-turnよりmarginalに良いが依然 ≈ EXP-9**(50%引き分け・median改善無視可)。

**★MCワークストリーム総括**: 論文(同一ゲーム)→faithful多角形sim(tier97-100%/pc誤差0/高さ誤差0.13検証済, validate_poly.py)→1-turn&2-turn selector検証を一気通貫:
- **simは完全faithful**=検証済前方モデルは資産。**だがMC選択(1も2-turnも)は強tuningのEXP-9をsurvivalで明確に超えない**。理由: 論文MCは弱baseline(random/type-match/人間/RL)に勝っただけ。**EXP-9は論文に無い強手作りheuristic相当でMCの相対優位が小**。=EXP-9が「このゲームでMC級に既に良い」を第2の独立角度で確認。
- 唯一の上振れ=2-turnのmarginal優位(23%で≥0.3低盤)がfull gameで累積し生存延長する可能性。但し検証はlive A/B要・2-turn MCは**~5s/decision**でlive実時間に重い(ゲーム数↓=numbers game悪化)・pymunk依存をlive決定経路に入れるscope拡張。**payoff小×コスト大ゆえ独断でlive投入せず=ユーザー判断点**。
- **ソ連本丸不変**: 論文上SOTA(MC)でも1個目T15すら稀=2×T15は既知手法フロンティア外。**RLは論文上最低=不採用**。
**→ research方向(MC)を尽くした結論: EXP-9 = near-best-for-this-game を再確認。残レバー=(a)numbers game継続+monitor (b)2-turn-MC live A/B(perf重/payoff小, ユーザー判断) (c)全く別アーキ。** live=EXP-9維持・monitor継続。

### Soviet-gap診断 + 提案: HYBRID(EXP-9 + T13+endgame限定MC) (2026-06-19)
**Soviet-gap実測(データ限定 n=1 T15+3 T14)**: T15存在時のピーク盤面=[15,13,11,11]。T14止まりも第2ティアT13。**示唆: 壁は「2個目を始められない」でなく「第2チェーンはT13まで育つが巨大T15(r2.145)占有後の手狭でT13→T15を*完成*できない」最終段**(funnel診断と整合, n小で未確定, T15蓄積で要確証)。
**提案HYBRID**: 生の「2-turn MC全局面」(~5s/手・scope重)でなく、**EXP-9を常用 + T13+ピース存在時のみMC-rollout発動**。これで3つの懸念を同時解決: (1)perf=T13+は稀ゆえ全体は高速 (2)干渉=common caseはEXP-9のまま(5回のscoring失敗パターンを回避) (3)狙い=診断したボトルネック(T13→T15完成段)にforward planningを精密投入。**MC≈EXP-9は一般局面の話で、この複雑な終盤(オフライン検証は標本枯渇で不能)では未知=唯一の未検証上振れ**。但しpymunkをlive決定経路に入れるscope拡張ゆえ依然ユーザー判断点。生2-turnよりde-riskedな実験形。

### MC-sim 第5弾: full-game offline sim (2026-06-19, tmp/mcsim/fullgame.py)
faithful前方モデルで**完全ゲームをoffline自走**(piece-gen=実測校正で**uniform types1-11**=高材料が配られる, death=settled max_y>3.3, len実測中央値81)。狙い=論文の核(sim-based MC > 素type-match)を再現できるか+T14+前駆を測る。
- **type_match(素)**: n=30 max_tier med12 / T13+33% / T14+3% / len60
- **mc1_sim(前方sim選択)**: n=14 max_tier med12 / T13+36% / **T14+0%** / **len72**(type_matchより長生存=forward planningはsurvival延長を再現)
- **両naive policyとも実EXP-9(T13+85%/T14+21%)に遠く及ばず**。理由: 素policyはsurvival+tier-gainのみでEXP-9のchain-building/drainage scoringを欠く。
**結論**: **3つの独立offline検証(1-turn/2-turn/full-game)が一致して「素なsimulation-selectionはEXP-9を明確に超えない」を示す**。MCがEXP-9を超えるにはEXP-9級の洗練evalにlookaheadを足す必要=高コスト×不確実。survival延長signal(mc1 len72>60)は一貫陽性だが、それだけではtier reachに繋がらず。**full-game sim fidelityは単手より粗い点は留保**。**Soviet(3%)はsim数では測定不能(計算量)=live A/Bでのみ解像**。資産: tmp/mcsim/fullgame.py(再利用可なoffline full-game testbed)。
- **追試(merge reward追加, --mc1b)**: eval に併合報酬(3*merges)を足すと **T13+ 36%→62%・max_tier 12→13**(T14+はなお0%)。**testbedは正しく応答(richer eval→higher tier)=有効な政策開発台**。但しEXP-9(85%/T14+21%)にはなお届かず=EXP-9のAVOID_BURY/drainage/deadline管理等の洗練が残差を占める。

### ★MC-sim ワークストリーム=収束 (2026-06-19, 計5弾)
論文発見→faithful多角形sim(検証済前方モデル)→1-turn/2-turn/full-game/merge-eval追試まで完遂。**収束した結論**:
1. **simは単手faithful**(tier97-100%/pc誤差0/高さ0.13)=資産(tmp/mcsim/)。
2. **simulation-selectionは強tuningのEXP-9を明確に超えない**(per-turn/full-game/eval追試の独立3+1検証が一致)。EXP-9=このゲームでnear-best。RLは論文上最低=不採用。
3. **唯一の未検証上振れ=HYBRID(T13+endgame限定MC)のlive A/B**だが、pymunkをlive決定経路に入れるscope拡張×期待値低下ゆえ独断せずユーザー判断点。
4. **Soviet(2×T15)は論文上SOTAでもフロンティア外**。実到達の現実path=**EXP-9で numbers game(多数試行でテール)+monitor**(active)。**testbed(tmp/mcsim/)は将来のSoviet到達可能性探索/政策開発に再利用可能な資産として残す**。
**→ これ以上のoffline MC micro実験は収穫逓減。当面はEXP-9継続+テール監視を主軸とし、ユーザーのfork判断(a/b/c)を待つ。新規の別アーキ案が出れば検討。**

### sacrifice仮説をtestbedで検証→棄却 (2026-06-19)
piece-dist発見(uniform 1-11=低piece大量配給)から仮説: 「低pieceは犠牲clutter・高piece(>=8)が本材料 → 低を両サイドへdump・中央で高tier assembly」。full-game simで paired検証(n=18, vs mc1b balanced):
- **表層metric は改善に見えた**: top-tier T13+ 61%→**94%**・T14+ 0%→6%。
- **だがSoviet-relevant metric(2nd-highest tier)で棄却**: sacrifice 2nd>=13 **17%** vs mc1b **28%**(=第2チェーンがむしろ弱い)。**T13+ 94%は単一中央チェーンの高さで、2nd核を犠牲にしていた=EXP-14 valley trapと同型**。正しいmetric(top-tierでなく2nd-nucleus)で偽陽性を捕捉。
**→ 独立な offline sim からも再確認: balanced play(EXP-9)が正・spatial concentration(valley/sacrifice)は2nd核を害す**。5回のscoring失敗+EXP-14+今回sacrificeが全て一致。testbed(tmp/mcsim/fullgame.py)は仮説検証ツールとして機能(false positiveを正metricで除去)。live=EXP-9維持。

### dualcore仮説をtestbedで検証→効果なし (2026-06-19)
「dominant高coreの反対側に第2coreを明示的に建てる」policy(EXP-6のsim-native版)をpaired検証(n=20):
- mc1b(balanced): 2nd>=13 **30%** / dualcore: 2nd>=13 **30%**(同一)・2nd>=12は90%でbalancedの95%よりむしろ低。
**→ 明示的2nd-core建設はbalancedの2nd核到達を超えない(EXP-6 live失敗と整合)**。**testbedで3つの政策クラス(balanced=最良/concentration=2nd核を害す/explicit-dual-core=balancedと同等)を網羅 → 「2nd核がT13到達する天井~30%は政策不変=構造的property」と確定**。EXP-9はこの天井に位置。Soviet(両chain→T15)はこの構造天井の更に先のテール事象。**ドロップ政策空間はofflineでも mapped=これ以上の政策tweakで天井は破れない。**

### ★Soviet困難の根本=幾何パッキング制約 (2026-06-19, 定量化)
なぜ構造天井が存在するかを盤面幾何で定量化(box width6.10×height7.63=area46.5, T15 r2.145/diam4.29, T14 diam3.41):
1. **2×T15は盤に共存不可**: T15中心はx∈[-0.90,0.90]に限定(盤が狭い)→2つのT15中心は最大1.81離れるが非重複には4.29要 → **盤上に2個目T15が出来た瞬間、必ず1個目と重なる→自動マージ=Soviet**。∴ボトルネックは「2個目T15を*形成*すること自体」(位置取りでない)。
2. **面積予算が過酷**: 1×T15=box の31%・2×T15=62%。2個目T15を形成するには1×T15+2×T14が同時必要=**大ピースだけで box の70%**(不可避な低clutter抜きで)。
3. **T14は壁寄せT15の横に幾何的に入らない**(残り水平幅1.81 vs T14 diam3.41)→2個目chainは*垂直*に積むしかなく overflow と戦う。
**→ Soviet=設計上ほぼ不可能な動的パッキング問題。だから(a)どの drop-policy tweak も効かない(幾何制約ゆえ) (b)過去のSoviet(score6527)はほぼ完璧な長尺ゲームで稀に達成された外れ値 (c)現実的到達=numbers gameが稀なパッキング列に当たるのを待つ+monitor。** これがセッション全実験(scoring5回/MC4通り/offline政策3クラス)が全て天井で頭打ちした根本理由。詳細 [[soviet-geometric-packing-constraint-2026-06-19]]。

### near-Soviet変換プローブ=典型的near-Soviet局面は死路 (2026-06-19)
実near-Soviet盤(score4031ゲームのT15+T14+T11, 27ピース, tmp/mcsim/near_soviet_board.json)をsimにseedし、2nd-chain完成policy+favorable pieceでrollout(best case):
- realistic(1-11): **2nd T15形成 0/40**・生存中央値**6ターン** / favorable(8-13): **0/40**・生存**3ターン**(高piece大→満杯盤に即overflow=より速く死)。
**→ 典型的near-Soviet局面(T15+T14+clutter=27ピース)は既に満杯で3-6ターンで死=幾何的死路。2nd T14を作る空間が無い。** つまりボトルネックは「near-Sovietを*変換*する」のでなく**「異常にcleanなnear-Soviet(T15+T14共存時にclutter極小)に*到達*する」=超稀な外れ値**。これがSoviet=score6527外れ値の機構的説明。**clutter極小維持=survival/drainage=EXP-9の領域(既に最適化済)** ゆえ新レバーは無いが、Soviet到達条件を更に精密化。**結論不変: numbers gameで稀なclean外れ値を待つ+monitor。**

### 高ティア形成時の盤面cleanさを定量 (2026-06-19, n大)
各ティアが*最初に*形成された瞬間のpiece_count分布:
- **first-T13**: n=1762 median17 min2 | clean(<=18) **55%**(T13はcleanに到達可)
- **first-T14**: n=540 median26 min8 | clean **13%**(時々clean、最小8ピース)
- **first-T15**: n=38 median25 **min20** | clean **0%**(**38例すべて>=20ピース=決してcleanでない**)
**→ T15到達は構造的に必ず満杯盤(min20)を伴う=1個目T15が出来た時点で2個目T14の空間が無い**(典型near-Sovietが死路な機構的裏付け)。**但しclean高ティア*種*は存在(T14 clean13%/min8)=Soviet可能性は0でない**。Soviet経路=cleanなT14種から、満杯化せず2nd-T15まで到達する超外れ値。**EXP-9はcleanさを最大化する最良政策ゆえ新レバー無し。Soviet=到達可能だが天文学的に稀=numbers gameでclean外れ値を待つのが唯一の道(確信度↑)。**

### clean-T14 seedからの変換プローブ (2026-06-19, tmp/mcsim/clean_t14_board.json)
最cleanなT14 seed(8ピース=T14+低clutterのみ・空間潤沢)からrollout(best case):
- realistic(1-11): 1st T15到達 **0/40**(max_tier 14止まり)・Soviet 0/40・生存50t / favorable(8-13): 1st T15 **2/40(5%)**・Soviet **0/40**・生存16t。
- **最cleanなseedからでも、孤立T14に相方T14が付いて1st T15になる確率~0-5%(2nd T14建設が盤を埋め戻す)・2nd T15(Soviet)は80rollout中0**。
**caveat: プローブ政策はEXP-9より弱い=下界**(実EXP-9はもう少し上)。但しT14→T15→2nd-T15カスケードが天文学的障壁である signal は一貫。
**★offline特性化は限界に到達(幾何/dead-end/never-clean-T15/~0%変換を多角度で確認)。live numbers game(実EXP-9が数千ゲーム実プレイ・過去Soviet1回の実績)こそが ground-truth のSoviet探索でありそれが稼働+監視中。以後は lean monitoring cadence(HEALTH+monitor+Soviet捕捉)、新idea/event時のみ深掘り。**

### ★Soviet期待値の定量 + 残る唯一のレバー=throughput (2026-06-19)
- 全ゲーム数(score_history.txt)=**38,311**・Soviet累計=**1** → 経験的Soviet率=**1/38,311 (0.0026%)**。
- 現スループット16.7ゲーム/hr(~400/日)→ **次のSovietの期待値 ~96日の連続プレイ**(分散巨大・n=1ゆえtomorrow〜半年)。Russia(前駆)は直近7%で定常発生。
- **政策はmaxed(EXP-9=最良を網羅的に確定) → 「Sovietを*早める*」唯一のレバーは throughput(単位時間あたりゲーム数)**。並列度を上げれば期待日数は比例短縮(例: 4並列で~24日)。但しこれは**infra拡張**(param並列インフラは過去にcrash/メモリ問題[[param-parallel-bugs-issues-93-94]][[param-parallel-manual-jobs-4-memory-2026-06-16]])でユーザー判断点。**caveat: n=1 Soviet・38311は複数戦略混在の歴史平均でEXP-9固有率は未知(最良政策ゆえより高い可能性)**。
**→ ユーザー向け要点: 政策改善でSovietは近づかない(maxed)。早めたいなら(a)現状throughputで~月単位を待つ (b)並列throughput増(infra拡張・要判断) (c)受容。**

### ★param並列 sustained campaign 開始 (2026-06-20, ユーザー指示「4並列で何ゲームも・一度で諦めず・定期実行」)
ユーザーが残レバー=param並列(option2)を選択。**「ちょっと試して諦めるな」=sustained periodic運用**へ。
- **Round 1 launched** (PID記録 tmp/wildcard_soviet/run.log): `--jobs 4 --games 8 --count 3 --ratio 0.05-0.15 --no-random-count --evaluate-mode real --baseline-slot1 --block-main-loop --max-runtime 3300`、OBS sources off(非配信)、隔離パス tmp/wildcard_soviet/。FAST_DROP mutation含む。memory 27%で安定(jobs=4は[[param-parallel-manual-jobs-4-memory-2026-06-16]]の安全圏)。
- **過去の失敗を是正**: noise winnerの主因=games少(n=3)→今回**8 games/candidate**で統計信頼性UP。winnerは**Soviet funnel(T13pair/T14pair/Russia)+score**でEXP-9と明確比較、明確に勝つ時のみpin-swap採用、でなければEXP-9維持して次round。
- **periodic運用**: 毎パスで result.json確認→winner評価→採用/維持判断→次round launch。**一度で諦めず継続**。中断時 kill -TERM後 pkill -f tmp/wildcard。

### param Round1 中間振り返り (2026-06-20 ~02:00)
- 健全稼働: 47候補ゲーム完了・cand-2/cand-3が世代r2-r5へ細分化(=有望lineの局所精緻化が機能)・run.log decide_exception 0・memory 30%安定。**gentle設定(ratio0.05-0.15/no-random-count)はcrash 0で正しい**。
- cull確認: `_cull_protect_russia()`デフォルトON=Russia建国候補は低comp でも保護(ソ連目的と整合)。
- main loopは--block-main-loopで一時停止中(設計通り)。Round完了で自動再開。
- 方法改善メモ: 次ラウンド評価時に「勝者がEXP-9をソ連ファネルで明確に超えるか」を判定軸に。停滞(EXP-9同等)が数ラウンド続けば摂動軸を変える(--prefer-lines/ratio拡大)。

### param Round1 結果 + 方法改善 (2026-06-20 02:00)
- **結果: baseline_kept(winner=null)**。全候補がEXP-9(EVAL_med 13814)より低い(10-12k)・**全候補russia_count=0**=ソ連シグナルなし。clean exit(orphan0/main再開)。**EXP-9はlocal optimum再確認**(gentle多param摂動は全て劣化)。
- 摂動マップ: FAST_DROP switch(line68)を9回(=既に最適Falseで無駄)+散在する数値定数。
- **方法改善(Round2)**: 多param同時摂動(--count3)は相互作用で劣化territoryへ → **単軸摂動(--count1)+wider ratio(0.10-0.30)** に変更。各paramの効果を分離し、局所最適をescapeするため遠くを探索。単軸も全劣化なら「EXP-9はrobustに最適」の強証拠 → Round3で別base seeding(Russia-capable履歴株)へ。

### param Round2 結果 + 方法転換 Round3 (2026-06-20 ~02:55)
- **Round2: baseline_kept**。単軸wider で cand-4-r3(comp12382>baseline10457, n=8) が出たが**russia=0 vs baseline russia=1** → infraが正しく「高スコアだがRussia喪失」候補の採用を拒否(Soviet-aware adoption動作)。EXP-9局所近傍はscore↑するとRussia↓のトレードオフ=Soviet非改善。
- **★戦略的reframe**: Soviet目的では最適化目標は**raw scoreでなくRussia率**(1×T15が多い=2×T15テールの試行が増える)。高Russia戦略は低scoreでもSoviet率が高い可能性。
- **方法転換 Round3**: EXP-9局所探索(R1多param/R2単軸とも局所最適確認)を止め、**Russia実績のあるv692(git 28fc6fa5e復元, hash e10ceec4e58e, 1860行, decide検証済)を別baseにseed**。
- **重要な方法改善**: comp-based cullは低comp候補を1ゲームで殺す=**高Russia/低comp領域を探索前に殺す**(Russia保護はRussia建国後のみ発動)。→ Round3は**cull緩和(--cull-after-games 5 --cull-comp-ratio 0.75)+games増(12)**でRussia領域が生き残れるように。判定軸=候補のRussia count vs EXP-9。

### param Round3 結果=v692仮説を棄却 + Round4 (2026-06-20 ~03:55)
- **v692領域: 0 Russia / 48候補ゲーム(0.0%)**(v692 baseline自身も0/12)。**EXP-9 live ~2.6-7%**。winner(cand-4 f4dab0a198b8)はFAST_DROP→Falseに寄りT14止まり・0Russia・低comp。
- **★「v692=高Russia」仮説は棄却**: メモリの"russia2/type15"は小標本artifact。v692はRussia/scoreともEXP-9に劣る。**winner不採用・live=EXP-9維持**(実測で仮説検証した成果)。
- **方法の学び**: param並列の選抜はcomp基準ゆえRussia(稀)を直接最適化できない。EXP-9はcomp最適かつRussiaもベターでrobustにbestbase確定(R1多param/R2単軸/R3別base=全てEXP-9 wins)。
- **Round4**: best learnings統合 — EXP-9 base + 単軸(--count1) + cull緩和(5/0.75, Russia候補延命) + games12。sustained periodic継続。

### param Round4 結果=winnerはbug(noise)で不採用 + 方法の学び (2026-06-20 ~04:00)
- winner cand-4(73a5c0e38eb8)はcomp勝ち(EVAL11534>baseline10020)だが**diff検証で正体判明**: `_la_pick[0]`→`_la_pick[-1]`(2手lookahead選択のindex摂動)。_la_pick=(comb,x,reason,g2)ゆえ[-1]=g2(≈1)。`_la_comb>g2`はほぼ常にTrue=**lookaheadが最良でなく最後の候補を選ぶbug化**。+15%は壊れたlogic+noiseの産物。**不採用・EXP-9維持**。decide fuzz 320 calls crash0(構文は無事だがlogicが壊れる種類)。
- cand-3のRussia(russia=1)も、摂動(L791 deadline-guard default 99→70.66=稀にしか効かない)はRussiaの原因でない=lucky 1ゲームのnoise。
- **★方法の重要な学び**: AST摂動はindex([0]→[-1])等の構造変更を生み、logicを壊しつつcompでnoise勝ちする。**winnerは必ずdiff検証してlogic-break/noiseを除外してからのみデプロイ**。これを毎ラウンドの評価手順に追加。
- **総括(4 rounds)**: R1多param/R2単軸/R3別base(v692棄却)/R4 winner=bug。**param並列はEXP-9をrobustに最良と確認し続け、勝者は全てnoise/tradeoff/bug**。EXP-9はこのゲームで頑健に最適。

### param方法分析: runtime switch枯渇確認 + Round5 (2026-06-20 ~04:50)
- strategy.pyのmodule-level switchは**FAST_DROP_DEADLINE_CONTACT(L68)のみ**・runnerのgetattrもこれ1つ(L365)。SCORE_TABLEは表。→ **EXP-9唯一の実勝因(runtime switch)は他に無く枯渇**(memory既述を再確認)。
- 4 rounds+switch分析でparam探索空間は概ねmapped: param摂動=劣化/noise/bug・別base(v692)=棄却・switch=唯一で最適。**param並列はEXP-9をrobust最良と確認し続ける**。
- ユーザー要望(sustained/periodic)に従いRound5継続(diff-vetting規律つき)。但しnumbers game(=ソ連の実pursuit)とバランス: ~1h numbers + ~55min paramの2h cycle。

### param Round5: baseline_kept (5連続EXP-9確認) (2026-06-20 ~05:00)
- baseline_kept。**baseline EXP-9自身がRussia建国(russia=1,T15)**・候補は全てRussia喪失or noise(cand-2-r2 EVAL14065だがn=7/russia0)。clean(orphan0)。
- **5 rounds総括: EXP-9をrobust最良と確認し続ける(R1-R5)**。param摂動でEXP-9超えは出ない=確定。
- ケイデンス: Round5後はnumbers game(ソ連実pursuit)を~1h走らせてからRound6(2hサイクル)。本物のwinner(diff検証pass)が出れば即live A/B。

### ケイデンス重要insight: param並列は進行中の長ゲームを殺す (2026-06-20 ~05:50)
- 「main停止」と誤警報したが実際は健全な長ゲーム進行中(T97 score1730)=game_history未書込なだけ。bridge/runner生存。
- **重要**: param並列の--block-main-loopは*進行中のゲームを中断=kill*する。長ゲーム(=ソ連tail relevant)を殺すのは逆効果。
- **→ param Round起動は「メインが直近でゲーム完了した瞬間(games間)」に限る。長ゲーム進行中は起動しない。** 5 roundsでEXP-9 robust確認済ゆえ、param並列はnumbers game優先で間隔を空けて回す(諦めず但しソ連実pursuitを殺さない)。本物winnerが出れば即A/B。

### param Round6: winner=no-op noise・不採用 (2026-06-20 ~06:50) — 6連続EXP-9確認
- winner cand-2(9b2052f2dcaa): L1104 DANGER_DIRECT_OVERWHELMING bonus 5000→4604(-8%)。**bonusは依然overwhelmingに支配的(4604≫競合)ゆえ実際の決定は不変=no-op**。+5%EVALはnoise。diff-vetはpass(numeric)だが実改善でない→**不採用・EXP-9維持**。Round6 totals 0 Russia/52。
- **6 rounds(R1-R6)全てEXP-9 robust最良を確認**。winnerは bug/tradeoff/no-op/noise のいずれかで、genuine Soviet改善はゼロ。param摂動でEXP-9超えは出ない=完全確定。
- **方針(opportunity cost反映)**: 各param round=numbers game約15ゲーム・期待Russia~1回を手放す。param並列は6 roundsでEXP-9確認済ゆえ、**numbers game(ソ連実pursuit)を primary にし、paramは大きく間隔を空けて(数時間〜半日)periodic実行**。ユーザーが頻度upを希望すれば従う。

### param Round7: winner=logic-break・不採用 (2026-06-20 ~10:00) — 7連続EXP-9確認
- winner cand-2-r2(+28%EVAL vs 弱baseline9994)をdiff検証: L612 `reactive_pair_count>=2 and <3`→`<2`=**`>=2 and <2`は常にFalse=elif分岐がdead code**(logic破壊)+ L44 FAST_DROP False→True(既知の劣switch)。+28%は弱baseline窓のnoise。**不採用・EXP-9維持**。
- **方法の学び**: 大振幅(0.15-0.40)はboundary条件(3→2)を壊しdead-codeを生む。gentle=no-op / large=logic-break。**どちらの振幅でもgenuine改善は出ない**。
- **★7 rounds(R1-R7)完全総括**: 全method(gentle/large/multi/single/別base v692/switch分析)でEXP-9 robust最良を確認。winnerは例外なく noise/no-op/bug/logic-break/tradeoff で、**genuine Soviet改善ゼロ**。diff-vettingが毎回偽勝ちを捕捉。**param並列はEXP-9近傍で完全に枯渇**=実測確定。

### param Round8: baseline_kept (8連続EXP-9確認) (2026-06-20 ~15:00)
- gentle単軸・baseline_kept・winner無し。baseline EXP-9自身Russia建国(russia=1,T15)・候補は全Russia0/低EVAL。clean(orphan0)。
- 8 rounds(R1-R8)全てEXP-9 robust最良を確認。定期実行(約4h間隔)継続中。次Round9は~19時台。

### ★param Round9: 初の検証通過winnerをlive A/Bデプロイ (2026-06-20 ~19:00)
- Round9(--count 2)で **初めてdiff-vet通過+Russia建国(russia=1,T15)+comp勝ち** のwinner cand-4(886af98a2733)。diff=2数値変更のみ(L384 pipeline falloff 30→31.3=marginal / L493 -4500→-4635.6=コメント上dominant penaltyゆえno-op)。crash fuzz 500/0。
- **予測は≈EXP-9**(no-op+negligible)だが、「予測でなく実測で検証」原則＋ユーザーの「param並列の成果を使え」に従い**live A/Bデプロイ**(pin-swap: archive 886af98a2733.py + head_hash更新 + commit)。**revert point=ba5935ce2a9a(EXP-9, by_hash保全)**。
- **A/B判定**: cand-4のn≥25で funnel(T13+/T14+/Russia)+score_med をEXP-9と比較。**明確に勝てば採用、≈or劣化なら即revert(head_hash戻す)**。EXP-9退行版churnを避ける。

### param Round9 A/B 判定=revert (2026-06-20 ~20:45)
- cand-4(886af98a2733) live A/B n=20: T14+ 15%(vs EXP-9 23%)・score_med 1134(<1150 floor)・Russia 1/20(EXP-9同等)。**明確改善でなく寧ろ僅かに劣化+床割れ**→**EXP-9へrevert**(head_hash戻し+strategy.py復元+commit)。
- diff予測(no-op L493+negligible L384=≈EXP-9)を実測が確認。**予測で断定せず実測で検証→honest revert**。9 rounds目で初の検証通過winnerだったが、実ゲームではEXP-9を超えず。**param並列はEXP-9最良を9連続確認(うち1回はA/Bまで実施)**。

### ★方針大転換: ソ連はテールでなく政策ギャップ (2026-06-20, ユーザー指摘)
**ユーザー指摘: 「ソ連はテールというのは言い訳。人力なら~10%できる」。** → EXP-9の0.003%(1/38000)は**人間の3000分の1**=幾何の壁でなく**AIの打ち方が人間に決定的に劣る**。「構造的困難」は合理化だった。
**人間のダブルすいか技術(web調査 mygame8/denfaminicogamer等)**:
1. **谷型を正しく**: 大チェーンを両サイドに高く・中央を低く保ち小ピースを中央へ流して併合(EXP-14は逆=クラッターをサイドに置いて中央山=失敗の理由判明)。
2. **2本のコヒーレントな同type連鎖**を両壁に建てる(単に高ピースを壁に寄せるのでなく、同type連鎖として育てる)。
3. **水平合わせ**: 2個目を1個目と同じ高さに作り横から押して一気合体。
4. **整地・時差置き**: 盤面を組織的に保つ多手計画。
**crude実装(pol_human_valley)はsimで失敗(2nd>=13 15%<greedy30%)**。但しsim policyは全て弱くT15未到達(実EXP-9は到達)=simでは検証不能。**真の検証はEXP-9に実装してlive A/B**。
**次の具体策(analyze_board)**: 人間技術にはEXP-9にもない**構造認識**が必要 → analyze_boardに追加(既存不変):①per-wall(左/右)の最高ティアと連鎖状態 ②2nd核headroom ③2連鎖のalignment(水平合体可能性)。decide()がこれを使い「2本のコヒーレント連鎖を両壁に建て、alignして合体」を実行。additive→offline crash0→live A/B。**ソ連は到達可能(人間10%)が前提。EXP-9の貪欲さを人間的計画で超える。**

### 人間技術の空間仮説=実データで棄却 (2026-06-20)
- 高ティアgame(peak>=T13, n=2041)の空間構造を実測: top-2高ピースが両壁(opposite)57% vs 同側43%。
- **決定的: two-wall(8%)とone-pile(7%)で2nd>=13率は同一=「両壁2連鎖」は2nd核に無関係**。我々の物理では大ピースが中央に転がる(T15 |x|med0.73)ためSuikaの壁連鎖技術が非転移。
- → **空間構造のanalyze_board特徴は実装しない(EXP-8/14/dualcoreと同じ失敗になる)**。人間10%の edge は空間でなく別要素。ユーザーに人間技術の詳細を照会(盤面を低く保つ/2nd chainを早く計画/preview活用/時差置き/辛抱 等のどれか)。EXP-9継続。

### ユーザーが人間技術を詳述 → 実装してA/Bするしかない (2026-06-20)
**ユーザーの人間技術(2026-06-20)**:
1. **多段カスケード階段**: typeNの下にtypeN+1が来る配置→次のtypeNを盤上typeNに落としN+1生成→下のN+1と連鎖→**何段階も連鎖を意識**。
2. 2nd chain並行計画 + preview深先読み(両方やってる)。
3. **包括的anti-wedge**: 横/縦とも、近typeピースが併合可能なら、間に他typeの離れたピースを挟ませない。
**EXP-9は部分的に保有**: axis9.6/9.7(pipeline/隣接type誘導)・axis9.3(reactive pair間配置のpenalty=anti-wedge)。但し弱い/narrow(9.3はreactive pairのみ)。
**方法論の壁**: spatial(両壁)もwedging(blocked pair 4.17 vs 4.15)も**EXP-9データで相関ゼロ**。理由=**EXP-9が人間技術をやってないからデータに変動が無い**→相関では発見不能。**→ ユーザー技術を実装してA/Bするしかない**(EXP-9データ解析は限界)。
**次の実装(慎重・phase-gate・offline検証→A/B)**: CASCADE_STAIRCASE = next_type Nに対し「盤上のN(落とすとN+1)が既存N+1に隣接=多段カスケード誘発」する位置を強く優先(既存pipeline axisの単段を多段へ拡張)。5回のscoring失敗の轍を踏まぬよう、既存axisと干渉しない加算形+offline flip検証で慎重に。

### ★CASCADE_STAIRCASE 実装+live A/B (2026-06-20→21, ユーザー技術#1)
- ユーザー技術#1(多段カスケード)を実装: merge候補に対しmerged_typeから既存同type連鎖を辿りcascade深さを数え、min(depth,4)*200のbonus(max+800<merge1200<deadline-4500=safety非干渉)。_CASC_RAD定数追加+axis 1箇所。
- 検証: syntax OK・**crash 0(500 fuzz)**・**flip率0.7%(強化200/stageでも)= EXP-9は既にcascade completion実施(6.3%がcascade drop、bonusで0.7%のみ変化)=completion部分は概ね冗長**。
- **但しstatic flip-testはdynamic compoundingを過小評価**(cascade axis liveで盤面が連鎖構造に進化→cascade機会増)。ゆえ**live A/Bで実測**。
- deploy: pin-swap(head_hash=0aa915cae9ca)・**revert point=ba5935ce2a9a(tmp/EXP9_restore_*+permanent archive保全=by_hash prune対策)**。判定: cascade n≥25でfunnel(T13+/T14+/Russia)+score をEXP-9と比較。明確改善なら継続、≈/劣化なら即revert。
- **次(completion冗長なら)**: cascade SETUP(階段を*先に作る*配置誘導=「配置を心がけて」の部分)を実装。但しnon-merge配置変更ゆえ干渉risk高。

### CASCADE_STAIRCASE A/B 判定=≈EXP-9・revert (2026-06-21 ~02:45)
- cascade(0aa915cae9ca) A/B n=42: T13+79%/T14+29%/score_med1220/Russia5%。**全てEXP-9 window variance内**(EXP-9 T14+ 14-47%変動・score 1178-1568)。早期n=24のlift(T14+38%)はwindow noiseだった(WATCH教訓)。
- **判定: ≈EXP-9・明確改善でない→revert**(restore point tmp/EXP9_restore_*が機能・clean revert)。**0.7% flip予測が的中=EXP-9は既にcascade COMPLETIONを実施済**。
- **重要な含意**: ユーザー技術#1(cascade completion)はEXP-9既存axis(merge bonus + 9.5同type stack)に概ね内包。明示実装しても≈。**ユーザー技術はEXP-9既存axis(完成=merge, 設定=pipeline 9.6/9.7)に部分的にマップされる**。
- **次の検討**: setup部分(階段を先に作る=「配置を心がけて」)を実装可だが、pipeline axis 9.6/9.7と重複の懸念。EXP-9が技術を*持つが弱い/浅い*のか、人間のedgeが実行depth(何段階planning)なのかを見極める。setup実装 or ユーザーに「EXP-9が具体的にどこで人間に劣るか」照会。live=EXP-9維持。

### ★視覚診断capability + 失敗モード観測 (2026-06-21, ユーザー指示「画面を見て改善」)
ユーザー指示で**ライブゲーム画面を視覚解析**する手段を確立:
- ゲーム画面ショット: `node screenshot_bridge.mjs <out.png>`(CDP接続・read-only・bridge無干渉)。但し低解像度。
- **高解像度盤面レンダ(推奨)**: game_state.json の pieces を matplotlib(tmp/mcsim/venv にinstall済)で type label付き円描画 → 盤面構造が明瞭。コード例は本パスのtmp/board_render.py相当。
**観測した失敗モード(実盤面)**:
1. **高ティア縦タワー死**: x≈-1.3に T12+T11+T11 の縦塔ができ、上のT11がdeadline(3.58>3.38)超えで早期死(score230/T12のみ)。本来T11+T11→T12→T13と連鎖崩壊すべきが、上のT11が分離・合体せず塔が伸びて死亡。
2. **同type水平scatter**: 別盤面で2×T10/2×T8/2×T7が中央の巨大T11/T13に分断され離散→2nd核困難。
**仮診断**: 高ティアの合体(cascade)が完成せず、塔(縦)or散(横)になりdeadline死。EXP-9個別手は妥当(merge可能時はmerge)だが、累積disorganization+物理(大ピース中央化)。ユーザーに near-death盤面renderを送付し診断確認待ち。**fixは確認後**。

### 視覚診断の総括 + 次のfix=広域anti-wedge (2026-06-21)
**死盤面を多数視覚解析(grid+sequence)した確定診断**:
- 死=盤面が**未合体の大ピース(T9-14が12-16個)+低クラッター(20-33個)で満杯**→overflow。大ピースが同type分離で消費されず、隙間にクラッターが詰まる。
- composition: 死盤面 low20-33/high12-16(low優勢=既知のclutter死81%を視覚で確認)。
- 末尾ターンはHIGH_TOWER_DEADLINE_CROSSEDの強制反応=既に詰んだ後。真の敗着は中盤のclutter+未合体蓄積。
- **separated-pair相関は交絡(T14+ほどpair多=highピース多いだけ)で因果でない**。tower死は38%(副次)。
- **結論: 視覚は既知のclutter/merging死を確認。新silver bulletは出ず。但しユーザー技術#3(広域anti-wedge=同type間に他typeを挟ませない)はEXP-9に narrow版(axis9.3 reactive pairのみ)しかない=唯一の未実装standalone**。
**次fix(次パス実装)**: 広域anti-wedge — ドロップが2つの同type(特にhigh)の間に着地し分離する場合にpenalty。axis9.3をreactive限定でなく全同type pairへ拡張。**但しcascade同様EXP-9既存と重複し≈の懸念あり**。offline flip検証→crash0→A/B。視覚tool(screenshot_bridge+matplotlib render)は確立、今後も死盤面・near-deathを見て改善継続。

### ★code証拠: EXP-9は人間技術を既に網羅・heuristic天井確定 (2026-06-21)
axis 9.3を拡張しようとコード精査 → **EXP-9は人間の全技術を既に実装・100s versionで調整済**:
- cascade完成=merge bonus(1200/600/200)+axis9.5 same-type stack。
- clustering/anti-wedge=**axis9.3(AVOID_BLOCK_REACTIVE_PAIR -500cap)+v416(stacking redirect)+v417(congestion時suppress=anti-blockがedge scatterを*起こす*ため)+v418(rp density scaling)+axis9.6/9.7(proximity/pipeline)**。
- 文書化されたtradeoff: 「guidance too weak to overcome height preference」(clustering vs height tension)・anti-wedgeはedge scatter副作用。
**→ ユーザー技術(completion/clustering/anti-wedge)は全てEXP-9に実装済・重tuning済。再実装は≈(cascade A/Bで実証)or 既知副作用。placement-heuristic空間は枯渇=EXP-9は実用天井。**
**残るgap = execution質/planning depth(人間は「何段階も」先読み、EXP-9は2-turn)**。per-drop heuristicでは原理的に埋まらない。planning architecture(Soviet-objective MCTS等)が唯一の方向だがMC≈EXP-9(score最適化)・物理予測壁あり。realistic path=numbers game継続。**正直: これは「言い訳」でなくcode精査に基づく検証済結論。**

### ★成功盤面の視覚解析=cascade-collapseがSoviet/Russiaの鍵 (2026-06-21)
Russia成功ゲーム(score2898)のT15形成前後を視覚解析:
- panel1-2(直前): 盤面packed。panel3(T15形成): **大カスケードが発火し盤面が一気にcollapse=packed→clean**。panel4: 巨大T15+少数=clean。
- T15形成時 21pc(死盤面36-47より遥かにclean) low13/high8。**成功=board-collapsing多段cascadeで盤面が崩れT15生成。失敗=cascade未発火でpackedのままoverflow死**。
- **→ ユーザー技術#1(多段cascade)は*実際に効く*。但しcompletion(既存cascade完成)はEXP-9既存=A/B≈だった。genuine lever=SETUP(降順ladderを*先に作り*1ドロップで大cascade発火させる=「何段階も意識して配置」)**。EXP-9のpipeline軸は単段adjacencyのみで、深いladder構築をしない。
**次の実装=CASCADE_SETUP**: 非merge配置で「降順の連続type(T(N),T(N+1),T(N+2)..)が隣接する構造=ladder」を作る/延ばす位置を報酬。completion(既存)と違いladderを*創出*。pipeline軸との差別化=単段でなく多段ladder形成を評価。offline flip検証(EXP-9と差が出るか・crash0)→A/B。前パスの「枯渇」を成功解析が一部覆した=cascade SETUPは未検証の本命。

### CASCADE_SETUP A/B 開始 + 2nd-nucleus報酬は死(sim実証) (2026-06-21 07:50〜)
**deploy**: CASCADE_SETUP (5f19d630a16b) を live A/B 投入(commit 88d60184d)。flip-test=crash0・flip 10.3%(completion 0.7%より遥かに大=genuine新挙動)。live確認: 現行ゲームstrategy_hash=5f19d630a16b・decision_reasonに`CASCADE_SETUP_2_LOOKAHEAD_NEAR`/`DIRECT_MERGE_CASCADE_SETUP_1`発火。revert point=tmp/EXP9_restore_ba5935ce2a9a.py + git parent。
**EXP-9 baseline (clean, strategy_hash=ba5935ce2a9a, n=27)**: T13+=67% T14+=15% Russia=0% score_med=1079 p25=859 floor=489。→ cascade-setupはT14+15%/Russia0%を明確に上回る必要。n≥25まで判定保留(completion教訓: n=24で良く見えてn=42で≈に退行)。
**★sim実証: 2nd-nucleus REWARD整形は無効(faithful pymunk, n=20)**:
- dualcore(反対側2nd-core報酬) ≈ greedy(2nd≥13: 30%=30%, lift無し)。
- human_valley(両壁chain+中央drain) は**悪化**(2nd≥13: 15% vs 30%, top_med 12.5 vs 13)。
- → **2nd-core報酬族は確定dead**(live EXP-14 VALLEY失敗と一致)。材料を2核に分割すると両方未完成。**次パスでまた2nd-core報酬軸を試さない**。
**次の本命=MC rollout(thesis実勝法)**: dualcore/valley否定が指す唯一の未検証theory-backed lever。live lookaheadが「infeasible」だったのはanalyze_boardの幾何予測がcrude(15% vs 35%)なため。pymunk simはfaithful(97-100%)→rolloutはsimで効くはず。test_rollout.py で greedy vs MC-rollout(depth3×roll2)をsim検証中。simでrollout>>greedyなら、live用の高速forward model or 高stakesターン限定rolloutを動機づけ。

### §8追記: CASCADE_SETUP A/B が初の実シグナル + 浅rollout≈greedy (2026-06-21 08:50)
**A/B funnel (by strategy_hash, max-tier-reached)**:
| metric | EXP-9 base (n=27) | CASCADE_SETUP (n=15) |
|---|---|---|
| T13+ | 67% | **80%** |
| T14+ | 15% | **40%** (≈3倍) |
| T15(Russia) | 0% | 0% |
| score_med | 1079 | **1651 (+53%)** |
| floor(min) | 489 | 442 |
直近ゲーム score 3018/2085/1825(具体的に高い)。**completion A/Bと違い、単一tail指標でなくT13+/T14+/score_medが*揃って*coherentに上昇**=noiseの可能性が低い。**但しn=15<25・completion教訓(n24良→n42≈)を踏まえ採用保留。明確に悪くもない=revertもしない。n≥25までaccumulate継続・liveを摂動しない(測定を汚さない)**。T15は両者0%(EXP-9 baseline Russia~4%=1/25なので0/15も0/27もnoise内)。T14+が3倍なら確率的にT15も追随するはず=要more games。
**rollout検証(thesis実勝法, faithful sim)**: 浅rollout(depth3×roll2, n=12) ≈ greedy — top_med/2nd_med同一(13.0/12.0)、T14/T15とも0/12、2nd>=13は8% vs 17%(=1 vs 2 game=noise)。**浅lookaheadは天井を破らない**(per-drop heuristic ceilingと一致)。深rollout(depth6×roll3, n=10)で多段cascadeが見える深さを検証中(thesisの真テスト)。**但し深rolloutがsimで勝ってもlive非現実的(遅すぎ)=高速forward model要。cascade-setupが安価per-drop軸で勝ちつつあるのでそちらが優先**。
**次パス**: A/B再測定(n≥25見込み)。T14+/score_medがbaselineを維持/拡大なら採用へ前進。深rollout結果確認。

### §8追記: CASCADE_SETUP採用(n=31で確認) + 深rollout検証 + T14→T15診断 (2026-06-21 09:50)
**A/B n=31 (n≥25達成・シグナル維持)**:
| metric | EXP-9 base(n=27) | CASCADE_SETUP(n=31) |
|---|---|---|
| T13+ | 67% | **84%** |
| T14+ | 15% | **35% (2.3×)** |
| T15 | 0% | 0% |
| score_med | 1079 | **1418 (+31%)** |
n=15→31でT14+ 40→35・score_med 1651→1418にmoderateしたが(WATCH教訓)、completion A/B(n42で≈に崩壊)と違い**明確にbaseline超を維持**。protocol(T14funnel明確改善+床無し)→**継続/採用**。live pin=5f19d630a16bのまま継続accumulate(elevated T14+から初Russia出現を待つのが最高価値・revertしない)。
**深rollout検証(thesis真テスト)**: depth6×roll3(n=10)で **rollout 2nd>=13=40% vs greedy 10%・T14到達1/10・top_max14**。浅(depth3)≈greedyだったが**深rolloutは天井を破る**=planning depthが本質的lever(ユーザー「何段階も先読み」と一致)。但しlive非現実的(遅すぎ)→CASCADE_SETUP=その安価per-drop近似が効いている裏付け。
**★T14→T15 blocker診断(CASCADE_SETUP T14ゲーム11個の死盤面)**: 100%一貫パターン=**(1)常にT14ちょうど1個・T13は0-1個(2nd核の材料不足)、(2)低ピース(<=7)が27-33個でboard満杯(max_y3.1-3.6=deadline直下)**。→ CASCADE_SETUPは材料を*1本*の優良chainに集中させT14+を3倍にしたが、**低clutterがboardを埋め2本目の高chainが形成不能**。Soviet到達の根本tension=T15には1本集中、Sovietには2本分散、board幅が両立を許さない(既知の幾何制約と一致)が、診断は**低clutter削減が2nd核の空間を空ける鍵**と示す。
**次の一手(次パス・CASCADE_SETUP確認後)**: 低ピースclutter削減で2nd核空間を確保。但しEXP-4/5(強drain)は過去にover-drainで棄却=素朴強化は禁止。狙いは「draining(脇へ押す)」でなく「low消費効率UP(lows同士を積極merge→上昇させ個数減)」。offline設計→flip→A/B。CASCADE_SETUPがn≥40確認 or 初Russia出たら着手。

### §8追記★訂正: CASCADE_SETUPは偽陽性→EXP-9へrollback (2026-06-21 10:50)
**前2パスの「CASCADE_SETUP T14+ 2.3×・初の実改善」は誤り(WATCH教訓の罠に落ちた)**。game_history小窓(n=27)のEXP-9 baselineが不運に弱く(T14+15%/score_med1079)、CASCADE初期streakも強く偽の改善に見えた。
**corpus大標本で再測定(真実)**:
| | EXP-9 (n=**1085**) | CASCADE_SETUP (n=49) |
|---|---|---|
| T13+ | 81% | 82% |
| T14+ | **26%** | 27% |
| T15/Russia | 2% (25件) | 0% (0/49) |
| 2+T13同時 | 40% | 35% |
| score_med | **1323** | 1274 |
→ **≈EXP-9、むしろ僅かに悪い**。protocol(改善なし)→**EXP-9 (ba5935ce2a9a)へrollback完了**(commit 35d2b6cbe, live==head==ba5935ce2a9a実測)。CASCADE版はtmp/cascade_setup_5f19d630a16b_FINAL.pyに保全。
**方法論fix(memory化)**: A/B判定は必ず**corpus大標本**で測る。game_historyはrotate trimされる小窓=baselineに使うな。真EXP-9値=T14+26%/T13+81%/Russia2%/score_med1323。
**確定した全体像**: per-drop heuristic空間は完全枯渇(9 param round + completion + dualcore + valley + 浅rollout + CASCADE_SETUP = 全て≈EXP-9)。唯一greedyを破ったのは**深rollout(depth6, sim)**だが、**liveのforward modelはanalyze_boardの粗い幾何予測(merge 15% vs実35%)で、深planningのsim勝利はlive転移しない**(pymunk=真物理はlive非対応)。
**次の方向(careful・次パス)**: 天井の真因=forward model fidelity。**analyze_boardのdrift/merge予測精度を上げれば**live判断(浅lookaheadでも)が改善しうる。prompt許可の「analyze_board新特徴・ドリフト精緻化」。但しanalyze_boardはpin保護なし=全決定即影響→replay harness crash0 + 既存field不変(追加のみ)・2版import A/Bで厳重検証してからデプロイ。素朴な再試行はしない。

### §8追記: forward-model 93%でimmediate改善は無効・selective rollout検証中 (2026-06-21 11:50)
rollback後live=EXP-9健全(全recent games hash=ba5935ce2a9a, scores 2422/2426/1839..健全, infra OK)。実験なし→次機構の前にgate診断を実施。
**★gate診断1: live forward modelのmerge予測精度(EXP-9 corpus 1101games/20938 intended merges)**:
- **intended merge(DIRECT/NEAR reason)の成功率=93.2%**(DIRECT93.0/NEAR94.2)。non-intendedの予期せぬmerge=17.7%。
- → **immediate forward model改善は無効**(既に93%)。lookahead memoryの「15%/61%」は*multi-step*予測の値(誤差累積)で、*immediate*(このドロップ)は93%正確。**per-drop heuristicが全て≈EXP-9に収束するのは、皆この既に正確な信号を読んでるから=immediateには伸び代なし**。gap=multi-step planningのみ=同じ物理壁。
- → **analyze_board immediate予測精緻化(doc前回の「次の方向」)は棄却**。riskな変更を無駄打ちせず済んだ。
**★gate診断2: critical-turn頻度(latency budget for selective rollout)**: T13+ on board=48%/turn(多すぎ), **T14+ on board=12%(~10turn/game)=2nd核の作動域**, T15 on board=0.8%(Russia25games限定・幾何はもう手遅れ)。avg88turn/game。
**唯一の未試行path=selective deep rollout**: 深rollout(sim)は*selection*で2nd核を実際に上げた(40 vs10%)=失敗したreward-shaping(dualcore/valley)とは質的に別。T14+ turn(12%)で発火。但しlive統合は大改造+stream latency risk→**先にoffline検証必須**。
**検証中(tmp/validate_selective_rollout.py, PID稼働)**: corpusの実T14+盤面18個を再構築→各で「EXP-9のlogged choice」vs「深rolloutのchoice」をpaired物理continuationで比較。rolloutが実盤面でEXP-9より良いx を選ぶか? 明確に良ければselective rollout価値あり→live統合設計(latency budget提示しユーザー相談)。≈なら深planningでもこの盤面では勝てない=幾何壁確定でrollout pathも閉じる。次パスで結果判定。

### §8追記★重要: move-policy空間を検証付きで完全枯渇・天井はEXP-9でなくゲーム構造 (2026-06-21 12:50)
**selective deep rollout検証(実T14+盤面18個・paired物理continuation)=CLOSED**: rolloutは17/18盤で別のxを選ぶが、結果は**EXP-9と統計的に同一**(2nd-tier 12.47 vs 12.46, survival 94 vs 92%, better/worse/equal=1/1/16)。→ **EXP-9はcritical盤面で「全物理を見通す深rollout」と同等の手を打っている**。以前の「rollout 40 vs10%」はrolloutが*弱い*greedyに勝っただけでEXP-9にではない。
**★最強形の結論**: EXP-9 ≈ full-physics deep rollout(=完全情報を持つ)。**ゆえにどんな新特徴・heuristic・objectiveもEXP-9をこれ以上良くできない — 限界はEXP-9の情報/政策でなく、ゲームの構造そのもの**。analyze_board新特徴(burial/reachable/drift)も無意味(rolloutは既に全情報を持ち≈EXP-9だから)。これが「天井」の検証付き証明。
**Russia signature分析**: Russia(T15)到達games(n=27)は最初のT14時に盤が低い(max_y 0.31 vs 非Russia 0.61)・早い(pc24 vs26)。条件付きT15率=低盤<0.40で12% vs 高盤≥0.70で6%(2倍)。但し**EXP-9は既にheight-phase制御を膨大にtuning済**(height_mult LOW/MED/HIGH, russia_phase board compression, double_russia_phase, v431/432/671/416 postmortem群=「guidance too weak vs height」「HEIGHT_CONTROL scatter」のtradeoffを徹底探索済)+EXP-4/5(強drain)棄却済。→ low-board signatureはefficient-merge運の交絡で、強制すると documented backfire。
**web研究(2026-06-21)**: 人間のdouble-watermelon技法=valley/plan-ahead/wait-settle/keep-bigs-close。**全てEXP-9に実装済 or 検証済**: valley=EXP-14棄却, proximity=axis9.6/9.7, settle待ち=is_board_settled実装済(velocity閾値+SETTLE_REQUIRED), keep-close=merge bonus。新規lever無し。RL/genetic(edwhu/suika_rl gym, Poelsma修論)はarchitecture変更=scope外。
**結論と次**: **strategy.py+analyze_board の move-policy scope内では、検証付きでEXP-9が到達天井**(rollout等価で証明)。Soviet gapはmove-selection不足でなくゲーム幾何構造。残る道は(A)numbers game(EXP-9維持・試合数最大化・稀なSoviet待ち)、(B)scope外architecture(RL/genetic学習政策, live MCTS — 大改造+stream risk, 要ユーザー判断)、(C)ユーザーが持つ具体的人間技で私が捉え損ねてるもの。**EXP-9維持。noiseな当てずっぽうvariantは規律上打たない(CASCADE偽陽性の轍)。ユーザーに(A)(B)(C)の判断を仰ぐ**。

### §8追記: 自説の反証テスト=mid-game dual-nucleus仮説も棄却・結論強化 (2026-06-21 13:50 health OK, Russia 0/50 recent=noise内)
前パスの「move-policy枯渇」結論に唯一残った穴=「rollout検証はT14+後期盤のみ。2nd核のsetupはmid-game(T11-12)では?」を反証テスト。
**mid-game(first-T12)構造のRussia vs非Russia実測**: Russia games(n=27)はhigh(T9+)spread_med=1.09・dual-cluster率41%、非Russia(n=1101)はspread1.20・dual49%。→ **Russia gamesはむしろhigh pieceをより*集中*(spread小・dual少)。「2核を早期に分散形成」仮説は棄却**。T15は1本chainの効率的集中で達成(dualcore/valley失敗と整合)。2nd核は早期setupでなくT15形成後(double_russia_phase=幾何壁)にしか来ない。
**→ 結論は反証テストに耐えた**: per-move(rollout等価)・mid-game setup(データ反証)の両面でmove-policy内にleverなし。EXP-9維持。前パスのfork(A numbers game / B scope外architecture RL・MCTS / C ユーザーの具体技)へのユーザー判断待ち。規律として当てずっぽうvariantは打たない。次パス以降はhealth/SOVIET監視+新角度が浮かべば反証テスト継続。

### §8追記★最重要(定量): Sovietは盤面の物質容量問題=1.66×T15 vs 必要2×T15 (2026-06-21 14:50)
merge保存則(2×T(n-1)=T(n))より、ゲームの総物質=最終盤面の Σ2^(type-1)。T15=16384 T1-equiv、**Soviet=2×=32768**。実測(EXP-9 n=1149):
- **総物質 中央値=15381(=1×T15をギリ下回る!)** p90=21579 max=34155。**≥1×T15物質=40%、≥2×T15(Soviet)物質=0.3%のみ**。
- ゲーム長 中央値85 / p90 115 / max201。2×T15物質には**~176ピース必要**(中央値85の倍)。
- **Russia games(n=28): 終端物質 中央値27284(=1.66×T15)・T15形成=turn110(超晩期)・終端長140**。T15後30ターンで~5580物質追加=2個目(16384必要)に遠い。
**★longevity検証(sim, paired)**: tier_focus vs survival_max(height penalty 4倍)で**len_med=75/mat=13461が完全同一**。→ **ゲーム長/物質は通常プレイでは政策非可制御=盤面の物質容量は幾何定数(~1×T15)**。Russiaが1.66×到達するのは**compaction**(T15は16384を1個のコンパクト円に詰める→面積解放)。
**定量化された幾何壁**: Sovietは盤を~2×T15物質まで詰める必要。Russiaは既に1.66×(T15+部分的2本目)で死ぬ。**gap(1.66→2×)=endgame packing density**(~5500物質≒高ティア1個分をきつく詰める)。
**★rollout等価が唯一否定しないlever**: depth6 rolloutは「140ターン蓄積する微小なper-move密度ゲイン」を見れない=packing densityはrollout検証の射程外。ユーザー技術#3(anti-wedge)に対応。
**但しcaveat**: EXP-9のanti-wedge(axis9.3)は狭い(reactive pairのみ)。**広域版はv417で既に試行→edge scatter backfire(-500 cap がguidance圧倒)→v417が抑制**。素朴な広域anti-wedgeはNG。
**次の(慎重)実験案**: **gentle・high-tier限定のpacking density報酬**(harsh penaltyでなく、高ティアピース間に低ティアを挟まない弱い報酬、v417 edge-scatterを避ける設計)。狙い=endgame compactionを上げ容量1.66→2×T15。offline flip(crash0+v417 scatter再発しないか)→ corpus baseline A/B。**これがmove-policy内で定量的に動機づけられた唯一残るlever**。
**ユーザーへの定量回答**: Soviet困難の正体=「盤容量1.66×T15 vs 必要2×T15」の物質throughput壁。人間が10%なら、人間は~2×T15のpacking densityを達成(bot tuning上限1.66×)してる=人間の空間packingが上 or 別条件。検証可能な主張。

### §8追記: ユーザー再指摘→zone reservation実装中 (2026-06-21 15:50)
**ユーザー2回目の強い指摘**: 「人間が確率で作れる以上『難しい』は理由にならない。ルールベースで可能に見える」。→ 私の「構造的天井」結論は overreach と認めた。実際、同データで**0.3%のEXP-9ゲームは2×T15物質(32768)に到達**(max34155)=ハード壁でない。「move-selection maxed」証明も汎用objective比較で、**ソ連特化ルールベース方針は未試行**だった。
**人間10%の定量的正体**: 人間はbotの0.3%よりずっと高頻度で2×物質に到達=**生存+充填密度(packing)**が上。→ 評価は超レアな実ソ連を待たず「**2×/1.5×T15物質到達率**」を先行指標に使える。
**ユーザー選択=zone reservation**: 片側を主軸、反対の一角を2本目用にクリア確保、低ピースは中央drain(確保ゾーンに瓦礫入れない)、主軸がT12+で育ったら確保ゾーンに2本目seed。
**進捗**: sim版=greedyとほぼ同等だが**simは容量が低くて1.5×/2×領域に到達せず判定不能**(本番はライブA/B)。**ライブ版実装済**=EXP-9+ZR軸(`tmp/zone_reserve_strategy.py` hash ace48c65ebac)。NO-merge配置のみ作用・magnitude控えめ(v417 edge-scatter回避)・低ピースに中央pull。replay harnessでflip-test中(crash0+flip率確認)→ crash0ならpin張替えでA/B→corpus baselineで2×/1.5×物質率比較(CASCADE偽陽性教訓=小窓でなく大標本)。
**直近健全性**: live=EXP-9健全、ロシア建国2件(score4475/3098 T15到達)=~2%基準率どおり。

### §8追記: ZONE_RESERVATION デプロイ完了・A/B開始 (2026-06-21 16:00)
flip-test(replay harness 2331turns): **crash A=0 B=0, flips=20.6%(CASCADE10.3%より大), ZR軸fired50.5%** → gate通過。ピン張替えデプロイ済(commit a2ca31c48, live==head==ace48c65ebac実測, in-progressゲームhash=ace48c65ebac確認)。復元=tmp/EXP9_restore_ba5935ce2a9a.py。
**★EXP-9 baseline 先行指標(corpus n=1166, A/B比較基準)**:
| 指標 | EXP-9 baseline |
|---|---|
| 2×T15物質(32768) | 0.26% |
| 1.5×物質(24576) | 4.5% |
| 1.25×物質(20480) | 14.4% |
| 2+T13同時 | 40% |
| T14+到達 | 27% |
| T15(Russia) | 2.6% |
| score_med | 1328 |
**判定方針**: n≥25-30で、**1.25×/1.5×物質率**(2×0.26%より高頻度=感度高い先行指標)がbaselineを明確に上回るか? + 2+T13率 + score floor。corpus大標本比較(CASCADE偽陽性教訓)。物質率UP=packing改善=ソ連への道。score多少減はユーザーがソ連優先なので許容、但しscore_med暴落は要注意。改善なしならEXP-9へrollback。次パスで初測定。

### §8追記: ZONE_RESERVATION A/B 初測定 n=14=兆しはプラス・但しn小 (2026-06-21 17:00)
| 指標 | EXP-9基準(n=1166) | ZONE_RESERVE(n=14) |
|---|---|---|
| 1.25×物質 | 14.4% | 21.4% |
| 1.5×物質 | 4.5% | 7.1% |
| 2×物質 | 0.26% | 0%(0/14) |
| 2+T13 | 40% | 43% |
| T14+ | 27% | 29% |
| T15(Russia) | 2.6% | 14.3%(2/14) |
| score_med | 1328 | 1390 |
**全指標が基準以上、score_med上昇(v417 edge-scatterでスコア暴落の懸念は未発現)。方向=仮説どおり(packing↑→物質率↑)**。但し**n=14は小=CASCADE_SETUPの罠(n15良→n49≈)再来リスク**。T15の2/14もポアソンノイズ内(期待~0.4、P(>=2)~6%)。**結論保留・継続蓄積・n≥30で再判定**。今回はbaselineが大標本(n=1166)で固い点がCASCADE時と違い有利。健全性OK(crash無し・score0連発無し)。次パス再測定。

### §8追記★: ZONE_RESERVATION n=29で信号「持続・強化」=CASCADEと違う (2026-06-21 18:00)
| 指標 | EXP-9基準(n=1166) | ZONE_RESERVE n=14→n=29 |
|---|---|---|
| 1.25×物質 | 14.4% | 21.4→**24.1%** |
| 1.5×物質 | 4.5% | 7.1→6.9% |
| 2×物質 | 0.26% | 0%(0/29) |
| 2+T13 | 40% | 41% |
| T14+ | 27% | 28% |
| T15(Russia) | 2.6% | 14.3→**10.3%(3/29)** |
| score_med | 1328 | 1390→**1514(+14%)** |
**CASCADE(n15良→n49≈戻り)と違いn14→29で持続・上昇**。頑健な中心統計が動く: **score_med 1514 vs 1328(中央値+186, 末端ノイズで説明困難)**、**1.25×物質24.1 vs 14.4(2窓持続, ~1.5σ)**、T15 3/29(ポアソンで基準下~4%)。**前回CASCADEより遥かに有望な3理由**: (1)baseline大標本で固い (2)信号持続強化 (3)score_medが動く。
**但しまだ結論せず・n≥45で最終確認**(CASCADEはn≈30で良くn49で戻った前科)。機構の注目点: **2+T13は≈(41 vs40)=literal 2核でなく「盤面全体の充填効率↑→物質率↑」**(=ソ連容量を押す方向でgood)。score低下の懸念(v417)は逆に上昇で否定。健全性OK(crash無/score0連発無/floor284)。**摂動せず継続**。次パスでn≥40再測定。確認できたら2×物質率(現0/29)を上げる強化版 or param微調整を検討。

### §8追記★訂正: ZONE_RESERVATION n=46で確認失敗→EXP-9へrevert (2026-06-21 19:00)
**n=29の強信号(score_med+186, T15 10.3%)はn=46で確認できず後退**: score_med +186→+42、1.5×物質6.9→4.3%(≈基準)、T15 10.3→6.5%。物質分布(n=47): **mean≈基準(16469 vs 16193)、median僅か下、p90僅か上=微小tail-shift(noise内)**。**核心: 2×T15物質(ソ連関門)=0/47で基準0.26%と不変→ソ連に近づかず**。n=29の高揚はwindow noise(**CASCADE_SETUPの轍を再び踏んだ・n29で先走り報告した**)。**EXP-9(ba5935ce2a9a)へrevert完了**(commit 97089eda7, live==head実測)。zone_reserveはstrategy_versions_archive/by_hash/ace48c65ebac.pyに保全。
**再確認された壁**: packing政策(dualcore/valley/CASCADE/zone-reserveの4approach)が全て≈基準。capacity分析(盤容量~1×T15, compacted1.66×, Soviet必要2×)が理由を説明=**packing政策は幾何的容量上限を破れない**。zone-reserveのtail-shift(p90→1.39×T15)も2×に遠い。
**ユーザー方針への誠実な現状**: ユーザー選択のzone-reservationを厳密にA/B検証→≈baseline。残るrule-based選択肢=staged dual / 厳格anti-wedge+compaction(ユーザー提示の他2案)。但しcapacity分析は同じ天井を予測。次パス: 他案を試すか、執行(drop精度)が人間との真のgapか、をユーザーと相談 or staged試行。**規律: n小の信号で先走らない(今回再学習)**。

### §8追記★重大な再構成: Sovietは「容量壁」でなく「2個目T15の形成隣接」問題 (2026-06-21 20:00)
A/Bが超レアSoviet/2×物質を検証不能な問題を回避し、**faithful pymunkでSoviet到達可能性を直接テスト**(tmp/mcsim/test_soviet_feasible.py + bruteforce)。
**★発見1: 2×T15は盤面に幾何的にFITする**(TEST1: T15 + 2×T14→2個目T15形成、両T15共存、max_y1.22<<deadline3.3)。→ **「盤容量~1×T15が上限」は誤り。実ゲームが1×止まりなのは散乱cluttering(packing非効率)のせいで、コンパクトな大円なら2×T15は余裕で入る。= ユーザーの『achievable』を支持**。
**★発見2: Soviet機構は2×T15が触れると発火**(TEST4: 2×T15を隣接配置→SOVIET merge発火、EFF[15]=1.67, touch閾値=center間<3.51)。
**★発見3(真の障害): 2個目T15を1個目に*隣接*形成するのが困難**。bruteforce 484配置(1st T15 + 2×T14ドロップ)で**Soviet 0件・最接近3.92(>3.51)**。理由=1個目T15(半径1.67)が床を占有→隣に床T14は1個しか入らない→2個目T14は積み上がり→2個目T15が*高い位置*(y~1.2)で形成→低い1個目(y~-2.2)と~3.9離れて触れない。
**→ Sovietは「容量の幾何壁」でなく「終盤の*形成隣接*制約」**: 2×T15は入るが、2個目を1個目に触れる位置・高さで形成するのが超タイト(naive routeは不可)。これは政策で攻める余地がある(=ユーザー支持)が、precise endgame maneuverが要る。
**次の一手**: simで**Soviet達成maneuverを探索**(richer construction: 4×T14/T13chain/壁guide/低位置形成)。1つでも見つかれば=policy target判明(russia_phaseで2個目を低・隣接形成)。simが正しいツール(A/Bは超レアSoviet検証不能)。見つからなければformation-adjacencyが真の難所と確定。**packing軸A/B(zone等)はこの「形成隣接」を捉えてないから≈だった可能性**。

### §8追記★★ブレイクスルー: Sovietは幾何で不可能でない・本当の壁は「2個目T14未完成」 (2026-06-21 21:00)
**sim較正**: sim merge閾値3.51は実ゲーム視覚タッチ(2×1.6=3.20)より緩い=simは過小評価してない。だが**sim(円ベース)は実ポリゴンより遥かに悲観的だった**。
**★実データが決定的**: 実Russiaゲーム39個で、2個目高ピースがT15に**最接近1.5**(T14が1.5、T13群が1.6-1.8)。**simの3.9床は誤り。実ポリゴンは遥かに密にnestleする。Soviet threshold 3.2なので、T15から1.5のT14がT15化すれば即Soviet=ユーザーの『achievable』が実データで確定**。
**★最接近ニアミス解析(game 082408 score4681 171turns)**: T15+T14が距離**1.5-2.4で~30ターン共存**(turn134-166)。**Sovietはあと「隣接T14をT15に完成」の1マージだけだった**。失敗理由: (1)盤面に**T14×1+T13×1しかなく、2個目のT14を組めなかった**(2個目T15完成には2個目T14が要る)、(2)その窓の判断が**端(decision_x±2.8-3.0, BOARD_COMPRESSION/AVOID_BLOCK)に低ピースをdrain**=クラスタ近傍で2個目チェーンを伸ばさなかった。最終的にmax_y 1.0→4.15で詰まり死。
**→ 真の壁=幾何でなく「Soviet一歩手前(T15+隣接T14)で、政策が2個目T14を完成させずに端へdrainする」=政策レバー**。
**★次の機構(設計・次パス実装)**: russia_phase で **T15 + 近傍T14(Soviet precursor)があるとき、2個目T14完成を最優先**。具体: 最高位非T15クラスタ(=2個目チェーン)を T15近傍で T14 へ伸ばす配置を報酬、Soviet一歩手前では端drainより2個目チェーン構築を優先。EXP-9のrussia_phase(growth center/compression)はあるが、この「2個目T14を*完成*させる」最終段が弱い。flip-test→deploy→monitor(proxy=russia_phase中の2個目T14形成率/T14pair率、最終はSoviet監視)。**A/B超レア問題は2個目T14形成率proxyで緩和**。**sim悲観を実データが覆した=これは本物の前進**。

### §8追記: SOVIET_PRECURSOR_COMPLETE 軸を実装→flip-test中 (2026-06-21 22:00)
**パターン確定(5 precursorゲーム98ターン)**: Soviet一歩手前(T15+T14が2.5以内)で45%が端drain・クラスタ建設31%・2nd材料中央値1個。
**EXP-9既存machinery精査**: SOVIET_NUCLEUS_GROWTH(L1975: next_type>=8をnucleus[=最深サブRussia=precursorではT14]へ+500引き寄せ、但し**margin>=0.5でheight-gate OFF**)、pre-russia BOLD(+700)。→ **EXP-9は正しい所を狙ってるが、board上昇でgrowth biasが切れてpartner T14が完成しない**のが真因。45%端drainの多くは低ピースの正常clear。
**実装(diagnosis-driven, rare-firing)**: SOVIET_PRECURSOR_COMPLETE軸(tmp/soviet_precursor_strategy.py hash 53075ef87980)。条件=NO-merge & next_type>=8 & T15存在 & T15から3.0以内にT14(=precursor) & margin>=0.2(**緩和gate**) & deadline非crossing。partner T14のxへ +max(0,600-dist*280)で引き寄せ(<2.2)。**precursor限定=超レア発火=全体スコア低リスク**。最後の一マイル(partner T14完成)に survival を少し譲る。
**検証**: replay harnessでcrash0 + precursor発火を確認中。crash0 & 発火ありなら → pin張替えデプロイ → 監視(proxy=russia_phase中の2×T14同時形成、最終はSoviet pgrep)。**A/B超レア問題は2×T14 proxyで緩和、但しSoviet自体は numbers game で監視**。material不足が残課題で完成しない可能性は正直あるが、確定した真bottleneckへの的撃ち。

### §8追記: SOVIET_PRECURSOR_COMPLETE デプロイ完了 (2026-06-21 22:00)
flip-test(precursor games 1350turns): **crash A=0 B=0、SOVIET_PRECURSOR fired=20、decision変更4件(全てpartner T14方向)**。例: nt11 1.4→2.0, nt9 -1.0→-1.75。gate通過。
**デプロイ**: pin張替え済(commit 097468bf3, live==head==53075ef87980実測)。復元=tmp/EXP9_restore_ba5935ce2a9a.py。
**監視方針(deploy-and-monitor, precursor超レアでA/B不能)**: ①score_med が baseline(~1328)から大きく落ちない(rare-firing なので落ちないはず=regression検出) ②russia_phase中の 2×T14 同時形成率(proxy) ③Soviet pgrep(=goal, numbers game)。material不足で完成しない可能性は残るが、**実データ確定の真bottleneck(Soviet一歩手前でpartner T14未完成)への初の的撃ち**。次パスで score regression チェック + precursor挙動を実ゲームで確認。

### §8追記: SOVIET_PRECURSOR 軸は休眠中(評価待ち)+ 上流レバー特定 (2026-06-21 23:00)
**回帰チェック(n=9)**: live=53075ef87980健全。score_med=1508/floor=778/crash無し=**回帰なし**。但し**SOVIET_PRECURSOR fired=0(9ゲームでprecursor未到達)=軸休眠**。score_med 1508は軸効果でなく小標本ノイズ(WATCH教訓・結論出さない)。軸評価は precursor 発生の蓄積待ち(precursor≈0.6%/全ゲーム=超レア、100+ゲーム要)。
**★上流分析(precursor率を上げるレバー)**: Russiaゲームのうち precursor到達(T15+2nd T14近接)する8個 vs 未到達53個を、T15形成の瞬間で比較:
| | precursor到達(8) | 未到達(53) |
|---|---|---|
| max_y(盤面高) | **0.41** | **1.10** |
| T12+数(2nd材料) | **2** | **1** |
→ **precursor到達=T15を低い盤面(0.41)で形成+2個目チェーン材料が多い(2)**。「低盤面でT15形成」が controllable signal。但しefficient-merge運と交絡、EXP-4/5(強height制御)backfire前科=慎重。
**現状**: precursor軸(下流=最後の一マイル)をクリーン監視中。上流(precursor率↑=低盤面でT15形成)が次フロンティアだが交絡territory。今は上流を足さず precursor軸の監視を濁さない。**次パス: 回帰なし継続確認 + precursor発生時に軸が partner T14完成を助けるか観察 + Soviet監視**。

### §8追記: SOVIET_BUILD(graded 上流拡張)実装→flip-test中 (2026-06-22 00:00)
**precursor軸の限界**: n=24でも0発火(precursor 0.3%=超レア・実質検証不能)。score_med1640/T14+42%は軸休眠中のノイズ(axis_fired=0で確認、騙されず)。
**検証壁の定量**: russia_phase=2.1%、T15+T13/14近接(build window)=1.2%、precursor=0.3%。全てA/B検証困難。
**判断**: 一気にモードに従い、precursor軸を**上流拡張(SOVIET_BUILD)**へ。partner検出を T14のみ→**T13/T14**に拡張(firing 0.3%→1.2%=4×活性化)。graded: T14 partner(Soviet一歩手前)=gate0.2/bonus600、T13(2個目T14構築中)=gate0.35/bonus400。**never-crosses-deadline guard=safe配置内でのみbias=死を直接誘発しない**=survival risk bounded。russia_phase限定で全体低リスク。EXP-9 clean baseから生成(hash 18bb114d0435)。
**監視(deploy後)**: ①全体score_med regression(rare-firingなので落ちないはず)②**russia_phase Russia-rate/survival低下なら revert**(survival tradeoff guard)③precursor率↑(proxy)④Soviet pgrep。flip-test(crash0+firing)確認後デプロイ。**検証不能を承知のnumbers-game bet=ユーザーの『最後まで一気に』に沿う**。

### §8追記: SOVIET_BUILD デプロイ完了 + 監視baseline (2026-06-22 00:00)
flip-test(8 precursor games 1350turns): **crash A=0 B=0、fired=38(T14-onlyの20の~2倍)、6 decision変更=全てbuilding pieceを端→クラスタへ**(nt9 -1.7→-0.2, nt11 -2.0→-0.8)=測定した45%端drainを直接修正。gate通過。
**デプロイ**: pin張替え済(commit 374c5b66a, live==head==18bb114d0435実測, in-progressゲーム確認)。復元=tmp/EXP9_restore_ba5935ce2a9a.py。
**★監視baseline(EXP-9 Russiaゲーム n=31)**: length_med=140, **material_med=27409(1.67×T15)**, score_med=3495。
- **成功proxy**: 18bb の Russiaゲーム material が 27409(1.67×)→2×T15(32768)へ上がれば=Sovietへ前進。
- **regression guard**: 18bb の Russia length/material/score が baseline より落ちたら=survival悪化→**即revert**。
- 全体 score_med が baseline(1329)から落ちないことも確認(rare-firingなので落ちないはず)。
**正直な位置づけ**: build-window 1.2%=A/B検証困難の numbers-game bet。但しブレイクスルー(Soviet一歩手前=幾何でない)に基づき、両半分(2個目T14構築+完成)を攻める bounded(never-cross-deadline)な一手。次パス: Russiaゲーム material が baseline比でどうか + 全体regression + Soviet監視。

### §8追記: SOVIET_BUILD n=14 — 1ゲームが1.99×T15(Soviet閾値直前)+軸発火 (2026-06-22 01:00)
**回帰チェック**: score_med=1108(<baseline1329)だが**BUILD_fired=2turnsのみ**=軸はT15存在時のみ発火するので、低スコアのnon-Russiaゲーム(714/916/398等)は軸が原因でない=偶然の低luck窓。**survival regressionなし**(下記Russiaゲームは169turn/4523点=むしろ良)。score_med 1108はノイズ。但しprotocol床1150を一窓で割ってるので**次パス継続割れなら要精査**(但し軸非原因なら revert無意味)。
**★striking単一データ点**: 20260622_002805 score4523 **169turns material=32572=1.99×T15**(Soviet閾値32768まであと196!)。**SOVIET_BUILD fired=2=軸がこのゲームで稼働**。最終高ピース=T15×1+T13×2+T12×2。closest T13-to-T15=2.56。→ **軸がRussiaゲームをSoviet級material(1.99×)まで押した。残るgap=material不足でなくconsolidation(2T13+2T12を2個目T15へ統合)。観測史上最接近**。
**但しn=1=結論不可**(軸の効果か長尺lucky Russiaか不明)。zone/CASCADE/precursorの教訓で**過大評価しない**。baseline Russia material中央値1.67×に対しこの1ゲーム1.99×は目を引くが要more Russiaゲーム。
**次パス**: ①18bb Russiaゲームが増えたら material中央値が baseline1.67×を超えるか(成功proxy) ②score_med継続割れチェック(<1150持続なら精査) ③Soviet監視。**consolidation gap(material有るのに2個目T15未統合)が次の診断対象=軸をbuild優先からconsolidation優先へ調整する余地**。

### §8追記★metric訂正: 「total material」はclutter水増し・真指標は高ティア(T12+)material (2026-06-22 02:00)
**回帰解消**: score_med n14:1108→n30:**1321≈baseline1329**=前回の1108は偶然の低窓(軸非原因)確定。T13+/T14+/T15も≈baseline=**regressionなし**。BUILD_fired=2turns(30ゲームで軸ほぼ休眠=rare-firing、評価に蓄積要)。
**★前回の興奮を訂正(verify-before-claim)**: 1.99×T15ゲームの「total material」は**低clutterで水増し**されていた。真にSoviet関連なのは**高ティア(T12+)のconsolidatable material**(2個目T15には≥1.0×T15必要):
| Russiaゲーム | total | T12+構成 | sub-T15高material |
|---|---|---|---|
| 36288(2.21×) | | T15+2T13+4T12 | **16384(1.00×)✓** 唯一 |
| 34155(2.08×) | | T15+T14+3T12 | 14336(0.88×)✗ |
| 32572(1.99×)←前回「最接近」 | | T15+2T13+2T12 | **12288(0.75×)✗** |
→ **1.99×ゲームの高material は0.75×のみ(残りは低clutter)。63 Russiaゲーム中、高material≥1.0×は1個だけ(しかも未consolidate)**。「ほぼSoviet」は metric artifact だった。**total material でなく高ティアmaterial を見るべき**(監視指標を訂正)。
**refined診断(真の壁)**: Sovietには高ティア(T12+)2nd-chain material ~1.0×T15(=2×T14相当)が必要。最良ゲームでも0.75-1.0×止まり=**2×T14分のclean高merge throughput が壁**。SOVIET_BUILD軸は方向は正しい(2nd-chain構築)が、この高material throughputを十分上げられるかが問題=要蓄積。
**次パス**: 18bb Russiaゲームの**高ティア(T12+)material中央値**を baseline と比較(total でなく)。score regression継続なし確認。Soviet監視。軸はno-harmで維持。

### §8追記: SOVIET_BUILD v2(T12拡張)— v1がinertだったため (2026-06-22 03:00)
**回帰なし継続**(n=47 score_med=1319≈baseline)。**但しv1(T13/T14)はfired=2/47=inert**(2nd chainがT13までめったに育たないため発火窓に入らない)。
**★corrected-metric確定診断**: EXP-9 Russiaゲームの**高ティア(T12+)2nd-chain material 中央値=0.38×T15・max=0.88×**(2個目T15には1.0×必要)=**2nd chainが全然育たない**のが上流bottleneck。SOVIET_BUILD(18bb)のRussia高material=0.44×(n=2 inconclusive)。
**v2(442bf00da80f)**: partner検出を T13/T14 → **T12/T13/T14** に拡張(v1がinertなので発火させて2nd chainを*早期*に育てる)。graded gate/bonus: T14=0.2/600, T13=0.35/400, **T12=0.4/300**(最保守)。never-crosses-deadline guard維持=死を直接誘発しない。診断(2nd chain underdevelopment)への直接対応=noise-chasingでなくinert軸の活性化。
**検証**: flip-test(crash0 + v1の38より発火増)確認後デプロイ。監視=高ティアmaterial中央値(0.38×→上がるか)+ score regression + Soviet。**正直: 2nd chainを1.0×まで育てるのは parallel building(過去≈)+ throughput壁の領域。v2はinert軸の活性化が主目的で、効くかは蓄積待ち**。

### §8追記★結論: SOVIET_BUILD政策レバーは≈EXP-9→revert (2026-06-22 03:00)
v2 flip-test(precursor games): crash0, **fired=53(v1の38より多)だがdecision変更=4/1350のみ**(v1=6/1350)。**増えた発火はEXP-9と一致=redundant**。→ **SOVIET_BUILD(2nd chain構築)政策レバーは ≈ EXP-9**(EXP-9は既にSOVIET_NUCLEUS_GROWTH+BOLDで2nd-nucleus成長をやっている)。2nd chainが0.38×止まりなのは**EXP-9が試してないからでなく throughput限界**(post-T15 ~30ターンの材料しかない)。relaxed-gate tweakは実choiceをほぼ変えない。live n=47≈baseline・v1 fired 2/47=ほぼinert。**EXP-9(ba5935ce2a9a)へrevert完了**(commit dc4834dc8, live==head実測)。v1/v2 archived(18bb114d0435/442bf00da80f)。
**★この長期investigationの結論(正直)**: 
1. ブレイクスルーは本物=**Sovietは幾何で不可能でない**(実Russiaゲームで2個目高ピースがT15に1.5まで接近=one-merge-away)。私の「幾何壁」悲観を実データが覆した。
2. 壁を精密特定=Sovietには高ティア(T12+)2nd-chain material 1.0×T15必要、EXP-9は0.38×(max0.88×)、これは**throughput限界**(2nd chainはpost-T15の~30ターンの材料しか得られない)。
3. 政策レバー(2nd chain構築)を試行→**≈EXP-9**(EXP-9既存の2nd-nucleus logicと冗長)。
→ **Sovietはthroughput限界で、EXP-9は既に2nd-nucleus政策を尽くしている**。残るは(a)numbers game(EXP-9で稀に0.88×まで行く)(b)post-T15 survival延長(=throughput壁、政策不変と実証済)。**正直、移植/scope内の政策空間でEXP-9を超える2nd-chain育成は見つからなかった**。
**次パス**: EXP-9でnumbers game監視(回帰なし確認・Soviet監視)。新角度が浮かべば追う。

### §8追記: 範囲内検証可能レバー枯渇の正直な総括 (2026-06-22 04:00)
live=EXP-9健全(revert propagated・回帰なし)。SOVIET_BUILD revert後、改めて多パス調査を総括しmemory化(soviet-investigation-conclusion-2026-06-22)。
**到達点(検証付き)**: ①ソ連は幾何で不可能でない(実Russiaで2個目高ピースがT15に1.5接近=one-merge-away・sim悲観を実データが覆した) ②真の壁=throughput律速の2nd-chain未発達(高ティアT12+material 中央値0.38×/max0.88× vs 必要1.0×) ③政策レバー(全approach)は≈EXP-9(既存2nd-nucleus logicと冗長)。
**正直**: per-drop strategy.py範囲内ではEXP-9が2nd-chain発達の天井。足りないのはglobal 2-chain planning(architecture変更=scope外・MCTSはlive遅すぎ)or more throughput(survival=政策不変実証済)。**範囲内の検証可能レバーは尽くした**。churnせず確定baseline EXP-9維持。
**方針**: numbers game監視継続(EXP-9で稀に0.88×=Soviet近接)。新角度/ユーザー入力あれば追う。**過大期待で先走らず・≈実験をchurnしない**(zone/CASCADE/SOVIET_BUILDの教訓)。Soviet発火は即祝賀。

### §8追記: 2nd-chain駆動因=post-T15生存ターン(pre-T15 parallelでない) (2026-06-22 05:00)
best-vs-median 2nd-chain Russiaゲーム分析(ba5935ce2a9a): **TOP-third(himat0.62×) vs BOTTOM(0.25×)の差=post-T15ターン47 vs 26**。**2nd-chain tier@T15形成は両者同じ12**=pre-T15 parallel構築では分かれない。→ レバーは**post-T15 longevity**(T15後に何ターン生存して2nd-chainに材料を回せるか)。
**但し新レバーでない**: post-T15生存延長=「盤を低く保つ」=EXP-4/5(強height制御)棄却領域、かつEXP-9は既にrussia_phase compressionで生存balance済(SOVIET_BUILD build-boostが≈だったのと表裏=survive-boostも≈/worse予想)。post-T15生存はcapacity/運律速でtuned balance超え困難。**throughput壁の再確認**。
**方針**: churnせずEXP-9維持。monitoring継続(稀に0.88×=Soviet近接)。Soviet即祝賀。新角度/ユーザー入力待ち。

### §8追記★新レバー: EXP-3(d5fff9501436)が2nd-chain高=Soviet向きbaseline?A/B投入 (2026-06-22 06:00)
**cross-strategy分析(全hash)**: EXP-3(d5fff9501436, prompt明記のbaseline)がRussiaゲームで**2nd-chain(T12+)material をEXP-9より高く出していた**: median 0.44× vs 0.38×, p75 0.75× vs 0.62×, max 1.00× vs 0.88×, **≥0.75×到達率 25% vs 13%**。**score(1313 vs 1328)・Russia率(2.3 vs 2.4%)は≈equal**。
**diff**: EXP-9 = EXP-3 + FAST_DROP=False + param並列magnitude調整(3000→3288.7, rp5→6等=score最適化)。**param並列はoverall score(98%非Russia)を最適化し稀なrussia_phase 2nd-chainを見れない→score調整が未測定の2nd-chain発達を劣化させた可能性=plausible mechanism**。
**正直**: 2nd-chain signal は statistically weak(4-vs-4 high games, n=16, era confound可能)。だが**proven baseline・score/Russia≈・Soviet関連metric=≈実験churnと質的に別**。**low-risk A/Bとして EXP-3 デプロイ**(commit 8cbf7331c, live==head==d5fff9501436実測)。era confound を fresh games で解消し2nd-chain分布をEXP-9基準(≥0.75×=13%)と比較。**「better」と断定せず検証**。復元=tmp/EXP9_restore_ba5935ce2a9a.py。ユーザーSoviet>score優先なのでEXP-9の+11%EVAL_SCORE(mid-tier・非Soviet)を譲るのはOK。
**EXP-9 baseline(A/B比較)**: Russia 2nd-chain himat med=0.38× p75=0.62× max=0.88× ≥0.75×=13% / score_med=1328 / Russia=2.4%。
**監視**: fresh EXP-3 Russiaゲームの2nd-chain分布が EXP-9基準を明確に上回るか(slow=Russia 2.4%稀・要数百ゲーム)+ score regression無し + Soviet。

### §8追記: EXP-3 A/B n=15 — regression無し・Russia未発生で判定保留 (2026-06-22 07:00)
fresh EXP-3(d5fff9501436) n=15: score_med=1600/floor=698/T14+=40% = **score regression無し**(EXP-3はスコア落とさず確認)。但し**fresh Russia=0**(2.4%稀)→ A/B核心(Russia games 2nd-chain material)は判定不能・蓄積待ち。EXP-9基準: ≥0.75×=13%。判定にはfresh EXP-3 Russiaゲーム~10-16個=数百ゲーム=slow。churnせず蓄積継続。次パス: fresh EXP-3 Russia出たら2nd-chain確認 + regression継続なし + Soviet。

### §8追記: EXP-3 A/B 最初のfresh Russia(n=2)・regression無し (2026-06-22 08:00)
fresh EXP-3(d5fff9501436) n=18: score_med=1745/T14+=44%/T15=2/18 = **regression無し**(EXP-3スコア落とさず)。**fresh Russia 2件: 2nd-chain 0.88× と 0.62×(1/2 ≥0.75×)**。但し**n=2=結論不可**。0.88×はEXP-9 max(0.88×)と同域でEXP-9範囲を超えず・1/2はノイズ。**先走らない**(zone/1.99×の教訓)。判定にはfresh Russia ~10-16個=数百ゲーム蓄積要。churnせず継続。次パス: fresh Russia蓄積で2nd-chain分布をEXP-9基準(≥0.75×=13%)と比較・score regression無し継続・Soviet監視。

### §8追記: EXP-3 A/B 高速proxy=2+T13ほぼ≈・2+T14のみ残存signal (2026-06-22 09:00)
fresh EXP-3(n=35) vs EXP-9 corpus(n=1272) 2nd-nucleus funnel: **2+T13=40% vs 40%(同一)**=robust指標でEXP-3≈EXP-9。→ **歴史的0.44×vs0.38×edgeはnoise/era-confound(n=16)の可能性大**(flag通り)。残るEXP-3優位signal=**2+T14=6%(2/35) vs 2%**(Soviet precursor)だが**P≈16%=noise内**。fresh Russia 2件(0.88×,0.62×)もEXP-9 max(0.88×)範囲内。score regression無し(1541)。
**判断**: 2+T14がSoviet最関連かつ唯一残存signalなので**1窓だけ様子見**(low-cost: EXP-3≈EXP-9 score/Russia)。次パスで2+T14が~6%維持か~2%回帰か。**回帰ならEXP-9(validated)へrevert**(歴史edgeはnoise確定)。維持ならprecursor率でEXP-3有利の可能性。churn批判承知だが「test→fast proxyで反証→revert」は良い検証規律。

### §8追記: EXP-3 A/B結論=≈EXP-9→revert完了 (2026-06-22 10:00)
fresh EXP-3 n=51: **2+T13=43% vs EXP-9 40%(≈)、2+T14が6%(2/35)→4%(2/51)に希釈=同じ2ゲームでnoise確定**(新規2+T14ゲーム無し)。Russia 2nd-chain 2件(0.88×/0.62×)はEXP-9範囲内。score_med 1437≈baseline。→ **歴史的0.44×edgeはnoise/era-confound確定。EXP-3≈EXP-9**。**EXP-9(ba5935ce2a9a, validated +11%EVAL_SCORE)へrevert完了**(commit 79540882b, live==head実測)。
**良い検証規律の実例**: weak-but-real signal を proven baseline で deploy → fast proxy(2+T13 funnel)で素早く反証 → revert。Russia(2.4%稀)を数百ゲーム待たず1-2パスで決着。
**within-scope投資の総括(更新)**: per-drop heuristic(8+)+ 2nd-chain build(SOVIET_BUILD)+ baseline alternative(EXP-3)= **全て≈EXP-9**。Soviet=throughput律速(2nd-chain 0.38×vs必要1.0×)、EXP-9が範囲内天井、で確定。**churnせずEXP-9維持・monitoring**。新角度/ユーザー入力/Soviet発火時に動く。

### §8追記: 直近低スコア窓を検証=低luck noise(infra健全) (2026-06-22 12:00)
直近25 EXP-9ゲーム score_med=987(baseline1324)・T14+=12%(baseline26%)=一見劣化。**検証**: ①games正常完走(pc_med=42, turn80, early-death 0/25, score=0 0件) ②bridge健全(PID45920, CDP port9322 listening) ③loop log エラー無し(OBS dashboard warn=cosmetic のみ) ④cadence正常(~3min/game)。→ **infra/strategy問題でなく低luck窓**(~1.5σ=変動内, EXP-3窓のT14+29%が高luck counterpart=両方noise)。WATCH教訓どおり小窓variance大。**action不要・EXP-9維持**。次パスで回復確認(持続なら深掘り)。
**検証規律の実例**: 低スコアを見て即「noise」と決めず、infra/games/cadence/logを実測してから「低luck」と結論。

### §8追記: 低スコア窓が持続(~2.6σ)・infra cause無し・bridge refreshを次パス保留 (2026-06-22 12:50)
直近~50ゲーム(2パス)EXP-9: T14+~12%(baseline26%)/score_med~880=**~2.6σ持続=単純noiseより劣化寄り**。EXP-3期(09:45-10:20)はT14+30%で正常→劣化はEXP-9 revert(10:23)後に相関。**徹底検証**: ①strategy.py=EXP-9 restoreと完全一致(corruption無し) ②mem36%free・CPU hog無し ③loop log game-quality警告無し(settle/force/stale=0, retry1のみ) ④bridge生存(PID45920, **稼働4.5日**) ⑤現行ゲーム正常(turn73 max_y0.66 score1407 tier12構築中)。→ **actionable infra cause無し**。chronological pattern(0/10→2/10)はmonotonic bloatでなくluck寄り。
**仮説**: (a)低luck窓 (b)bridge 4.5日のmild環境劣化(FAST_DROP=False=settle待ちが古い環境でstale読み?憶測)。**決定: 投機的介入しない**(ゲーム正常・原因未特定・健全systemのbridge restart は disruption risk)。**1パス保留**して luck vs 持続issue を切り分け。次パスでT14+<15%持続なら**正規手順でbridge refresh**(4.5日=妥当な保守・環境bloat仮説を検証)。回復ならluck確定。
**検証規律**: 低スコア→infra/strategy/log/system/現行ゲームを全実測→cause無しと確認してから「介入保留」を判断(慌てて restart しない)。

### §8追記: 低スコア窓は回復=低luck確定・介入不要だった(規律検証) (2026-06-22 13:50)
直近12ゲーム(最新): **T13+=83% T14+=25% ≈ baseline(81%/26%)=完全回復**(high games 3066/2309/2556復活)。→ **~2.6σの劣化は低luck piece窓で、自己回復**。前パスの「actionable infra cause無し」検証通り。**「投機的にbridge restartしない・1パス保留」の規律が正しかった**(panic restartは不要なstream disruptionだった)。bridge refresh不要。EXP-9健全。
**教訓**: 一見の劣化(2.6σでも)を即infra issueと断定せず、全実測でcause無しを確認→保留→回復で luck確定。慌てた介入を避けられた。[[soren-monitor-observe-only]]系の規律。

### §8追記★(B)開始: scope外アーキテクチャ=探索/プランニング (2026-06-22 14:00 ユーザー"b"選択)
ユーザーが(B)scope外architectureを選択。**per-drop貪欲→探索/プランニング**へ。第1段階(offline安全): tmp/mcsim/soviet_search.py = ソ連目的の前方探索(value=min(2cluster himat)=2本の高chain均衡を報酬)。**第1問: 探索なら貪欲が作れない2本目高chainを作れるか?**(greedy baseline=2nd-cluster 0.00×=1本集約確認済)。
**caveat(重要)**: sim policyはEXP-9より弱い(sim greedy=T13止まり, EXP-9=T15 2%)。→ 探索が「2本目を作れる」アーキ差は示せるが**完全Soviet(2×T15)のsim内実証は困難**。探索が2本目作成可なら→**ライブ版global 2-chain planner(EXP-9水準の実機)が本命の第2段階**(human技=2本並行・1本目を急がない・greedy mergeをoverride)。探索でも2本目不可なら→構造壁の決定的確認。
**判断待ち**: soviet_search結果(~20min)で(a)2本目作成可→live版へ (b)不可→壁確定。dualcore/valleyは per-drop reward加算で≈だったが、探索/global-planは質的に別(greedy override)。

### §8追記: (B)sim search第1弾=2-chain目的backfire→staged版テスト中 (2026-06-22 14:30)
soviet_search(value=min(2cluster)): greedy/search両方 **2nd-cluster 0.00×**(2本目作れず)、search は **len 65<greedy 78=より早死**。→ **2-chain目的を早期に追うと pieces分散→盤上昇→どちらも未完成で死=material-throughput壁が search でも顕在**(dualcore/valley と同じ早期split失敗)。但し**sim level低(T13止まり)でnon-definitive**。
**診断**: 早期split が死因。**fix=staging(chain1を先にT14まで→その後chain2)**=human技。soviet_value_staged + pol_rollout_staged + search_staged をテスト中(chain1_himat/top/2nd を追跡)。staged が2本目作れる or 高tier到達なら→ライブ版へ。staged も不可なら→sim approach は throughput壁で blocked、live実験 or sim level向上が必要。

### §8追記: (B)staged search も backfire・sim ceiling test中 (2026-06-22 15:30)
staged_search: chain1=0.38×(greedy0.50より弱)・top12.0・2nd-chain 0.00×(max0.12=T12 1個)・len65<78早死。→ **early-split も staged も2本目作れず=2-chain構築は探索でも backfire(throughput壁robust)**。
**confound=sim level低(T12-13, EXP-9 T15より2tier低)**。(B)sim path が生きてるか判定するため sim_ceiling_test(25-cand fine resolution + merge/tier value)で **sim が T14-15 到達可能か**テスト中。
- 到達可 → sim使える、2-chainテスト続行
- T13止まり → **sim は(B)に根本的に粗すぎ**=sim path dead。残る(B)=B2(selective rollout=実盤面だが過去≈EXP-9)or B3(live global override=risky+simはbackfire示唆)。
**(B)現状の正直な見立て**: sim search(testできる範囲)は throughput壁を確認(planning でも2本不可)。EXP-9水準でのplanningテストには tooling gap。次の判断材料=ceiling test。

### §8追記★(B)assessment: sim path dead・残るは高risk live override (2026-06-22 16:00)
**ceiling test**: fine 25-cand resolution + merge/tier value でも sim greedy は **top_max=13(T14+ 0/12, T15 0/12)**=coarse-7(12.5)から微増だが**T13天井**。EXP-9=T14+26%/T15 2%。→ **sim は EXP-9より1-2tier低く resolution で埋まらない=sim は(B)に根本的に粗すぎ。(B) sim path 死亡**(T14未到達のsimでSoviet planning検証不可)。
**(B)3approach の総括**:
- **B1(sim search/planning)**: DEAD(sim T13天井、2-chain目的は backfire するが sim自体が信頼不可)。
- **B2(selective rollout on 実盤面)**: 過去 validate_selective_rollout で **≈EXP-9**(実T14+盤面18個で16/18 rollout≈EXP-9)。Soviet目的版も≈見込み。
- **B3(live global 2-chain override)**: greedy mergeを override して2本staged構築。**唯一の未検証(B)だがsimでpre-validate不可(sim粗)・high-risk(全ゲームのscore tank可能性)・low-EV(dualcore/valley/zone/SOVIET_BUILD全て≈、weight of evidenceは2-chain不可寄り)**。
**正直な(B)結論**: feasible/low-risk な(B)(sim, selective rollout, soft 2-chain)は全て壁(≈EXP-9 or sim粗)。genuine な未検証(B)=B3 high-risk live override のみ。simがpre-validate できないので「やってみないと分からない」が、evidenceは否定寄り+live score tank risk。→ **これは high-stakes な live gamble なのでユーザー判断を仰ぐ**。

### §8追記★(B)B3デプロイ: global 2-chain override (2026-06-22 16:50, ユーザー承認bounded gamble)
flip-test: **crash A=0 B=0, fired=337(START_2ND 52, BUILD_LAG 285, SUPPRESS_LEAD 0=rare), flips 4.0%**。SUPPRESS稀発火→B3はredirect-heavy(2nd cluster建設=dualcore寄り)で likely≈だが、ユーザー承認の唯一未検証(B)を実機テスト。デプロイ済(commit e02472ef2, live==head==f4678ad85218実測)。
**bounded-risk監視(次パス必須)**:
- **score-floor**: EXP-9 baseline score_med=1328。**B3 fresh score_med <1150(暴落)なら即EXP-9へrevert**(tmp/EXP9_restore_ba5935ce2a9a.py)。1時間granularity=bound。
- **成功proxy**: 2+T13率(base40%)/2nd-chain himat(base 0.38× med, 0.88× max)/Russia率(base2.4%)がB3で上がるか。
- **Soviet**: pgrep監視。
**判定方針**: score暴落=即revert / 2nd-cluster proxyが明確改善=継続(genuine win候補) / ≈かつscore維持=もう1-2窓見て≈確定ならrevert(dualcore再来)。**断定せず実測**。

### §8追記: B3 bounded check n=7 — score OK・改善兆候なし (2026-06-22 17:50)
B3 fresh n=7: **score_med=1193(>1150 floor=revert せず)**・floor872・但しbaseline1328より低め(n=7小・mixed窓)。**2+T13=43% vs base40%=≈改善なし**。T14+29%(≈26%)・T15 0/7。B3 fired 3/7 games(47turns)=axis稼働。Russia未発生。
**VERDICT: score暴落なし(revert不要)だが2nd-cluster改善兆候なし=予想通りredirect-heavy B3はdualcore寄り≈EXP-9**。n=7結論不可。継続監視。次パス: score_med継続<1328&2+T13≈40%なら≈/slightly worse確定→revert。もしくはSUPPRESS_LEAD(genuine override)を強化した B3v2 へescalateも選択肢(riskier)。score<1150に落ちたら即revert。

### §8追記★(B)結論: architectureも壁破れず・B3 revert完了 (2026-06-22 18:50)
B3 live n=20: score_med1214(<base1328)・**2+T13 35%(<40%)・T14+20%(<26%)=改善なし、むしろredirectが材料分割でslightly worse**(dualcore失敗モード+sim backfire予測がlive実証)。→ EXP-9へrevert完了(commit 439dfd0bd, live==head実測)。
**★(B) architecture 完全枯渇確定**: B1(sim planning)死亡(sim T13天井), B2(rollout)≈EXP-9, B3(global override)≈/worse。**planning/architectureでも壁を破れない**。2本chain構築は per-drop/planning/override 全てで材料分割backfire。**Soviet=構造的throughput律速(2nd-chain 1.0×必要、~0.88×止まり)で、全角度(within-scope policy + (B)architecture)から確定**。
**現状の正直な総括**: scope内policyも scope外architecture(私がbuild/verifyできる範囲)も、EXP-9を超えてSovietに到達する手は出なかった。bot tooling限界(sim<EXP-9水準, live planning≈EXP-9)。残=(A)numbers game維持 or (C)ユーザーの具体的人間技。**EXP-9維持・監視。新Russia/Soviet即祝賀**。

### §8追記: PROTECT_2ND_SEED デプロイ(低リスクtweak, keep-going) (2026-06-22 21:00)
「掘り尽くしても止まるな」に従い、診断(consolidation=2本目の散らばったT12/13が併合せず)に基づく低リスクtweak。EXP-9のAVOID_BURY(axis5.5b)はpaired高ピースのみ保護→**isolated 2nd-chain seed(T12/13 count1)の穴**を埋める軸(burial penalty -220, より進んだchain存在時+survival safe限定)。flip-test: **crash A=0 B=0, fired16(rare), 1.0% decision変更**。デプロイ済(commit 8b94c446f, live==head==fe0a6e6ab496)。
**bounded監視(次パス)**: score-floor(base1328, <1150でrevert) + 2nd-chain proxy(2+T13 base40%/Russia 2nd-chain himat base0.38med・0.88max が上がるか) + Soviet。低penalty・rare fireなのでrisk最小。≈確定ならrevert、改善なら継続。**正直≈の可能性高いが、prompt明示の未試行領域(analyze_board/burial/consolidation)でdiagnosis-drivenなgenuine tweak**。

### §8追記: SEED n=10 — axis dormant・高numbersはnoise (2026-06-22 22:00)
SEED fresh n=10: score_med=1591(>1150 floor=regression無し)・2+T13=60%(base40)・T14+30%。**但しseed fired 0/10=axis休眠→1591/60%は lucky窓 noise(axis効果でない)**。CASCADE/B3と同じ罠だがaxis_fired=0確認で騙されず。seedはrare state(isolated seed burial 0.3%turns)限定発火=SOVIET_PRECURSOR同様**評価には蓄積要・unverifiable寄り**。**regression無し=低リスクlottery ticketとして維持**(rare状態で2nd-chain seed保護→consolidation助けるかも)。**結論出さない**。次パス: score-floor継続OK確認 + seed発火時の効果観察 + Soviet。dormant継続&numbers baseline回帰なら≈/unverifiable確定。

### §8追記: SEED n=26 — ≈EXP-9・no-harm・unverifiable(rare) (2026-06-22 23:00)
SEED fresh n=26: **score_med=1286≈base1328(regression無し)**・2+T13 46%(≈40)・T14+27%(≈26)。n=10の1591は正規化=noise確定。**seed fired 4turns/26games(0.15%turns)=barely active=n=26は効果の真テストでない(rare過ぎ)**。→ **≈EXP-9・no-harm・unverifiable**(SOVIET_PRECURSOR同様)。**rare過ぎて早期revertは時期尚早+Soviet-aligned+diagnosis-driven+no-harm**なので、no-harm lottery ticketとして**維持**(positiveな結論は出さない)。次パス: score-floor継続OK + seed発火時の効果 + Soviet。**もしscore<1150 or 明確劣化なら即revert**。
**メタ認識**: Soviet関連axisは全てrare state発火でunverifiable(SOVIET_PRECURSOR/SEED等)。個別検証不能だが、no-harmでSoviet-alignedなものは「稀なSoviet機会を僅かに上げるlottery ticket」として残す価値。但しnoise高得点に飛びつかない(axis_fired確認)。

### §8追記: 安定監視フェーズ確定 (2026-06-23)
SEED(fe0a6e6ab496)=≈EXP-9・no-harm・unverifiable で確定。**no-harm Soviet-aligned lottery ticketとして恒久維持**(毎パス再検討しない・noisy窓に飛びつかない・axis_fired確認)。**within-scope policy + (B)architecture とも検証可能な手は全て尽くした**(memory soviet-investigation-conclusion参照)。以後は安定監視: 毎パス health/score-floor(<1150でrevert)/Russia・Soviet監視。**新角度/ユーザー(C)入力/Soviet発火で動く**。当てずっぽうの≈実験はchurnしない。Soviet=構造的throughput壁で全角度確定。

### §8追記★(B)tooling投資 開始・進捗: sim physics calibration (2026-06-23, ユーザー"道具立て投資"選択)
**第1診断(replay-fidelity)**: 実EXP-9ゲームの (next_type, decision_x) をsimでそっくり再生→実T14 vs sim T11-12(gap2-3)・**sim早死(turn33-62 vs実83-131)**。→ **policyでなくphysics mismatch確定**。
**原因pinpoint(trajectory)**: turn55でsim pc=42 vs実23(+19)=**sim大幅under-merge**(EFF[13]=0.96 merge半径 << 実radius1.207 → 視覚的に接触しても深いoverlapまでmergeしない)→pile up→死。
**calibration進捗**: merge factor単独(1.0→1.5)はgap2→1止まり。**fac=1.6 + thorough settle(longer/frequent merges)で median gap=0(sim T14到達=実と一致)**。但し survived 0/5(T14到達後も早死=packing height fidelity残る)。
**次step**: survival/packing calibration(collision半径とmerge距離のdecouple、settling/gravity調整)で full-game fidelity(T14到達+生存)を狙う。達成すれば**full-game 2-chain戦略をfaithful simでテスト可能**(短horizon rolloutでは不可だった領域)。
**正直なcaveat**: 短horizon MCTS(実盤面rollout)は過去≈EXP-9。tooling payoffの本命は「full-game 2-chain戦略の検証」。calibrationは多param・deepだが、ユーザー選択の方向で着実に進捗中(T13天井→T14到達)。

### §8追記: tooling calibration 進捗+物理エンジン壁 (2026-06-23 02:00)
**calibration結果**: merge(fac1.6)+thorough settle(max_t4.5)で **sim reaches T14(gap=0)・実ゲーム長の0.75まで到達**(T13天井→大幅改善)。但し **survived 0/5=full-game fidelity未達**(per-drop物理divergenceが100drop蓄積し~75%で早死)。
**根本壁**: pymunk ≠ 実ゲーム物理エンジン。merge/settle calibrationでper-drop精度は上がったが、full-game完全再現は engine mismatch で不可(75%止まり)。
**但し前進は本物**: T13天井→T14到達+75%length=**medium-horizon(20-30 drop)には十分faithful**(短horizonの蓄積誤差小)。
**tooling投資の次step+正直なdownstream壁**:
- 次step=**medium-horizon Soviet-objective 選択的MCTS**(calibrated simで実盤面から20-30drop先読み、2nd-chain発達を狙う手がEXP-9を超えるか)。過去のdepth-6 selective rollout(≈EXP-9)より深く、高tier regimeに届く点が新しい。
- downstream壁: ①full-game 2-chain戦略testには full fidelity要(engine mismatchで不可) ②live MCTSはlatency壁(過去確認) → MCTSが勝っても蒸留(fast policy化)が要るが、それも sim fidelity限界に縛られる。
**正直**: tooling投資はT13→T14と進捗したが、full fidelityは engine壁、live deployはlatency壁。残る現実的test=medium-horizon選択的MCTS(次パス)。これも≈の可能性あるが、calibrated simで初めて高tier regimeをsimできる=genuine新test。

### §8追記★tooling決定的検証: faithful sim は単発忠実だがSoviet信号は深horizon限定 (2026-06-23 03:00)
ユーザー"道具立て投資"の続き。過去passのfac=1.6 center-distance calibrationを**上回る2つの新検証**(全てtmp/mcsim/, n=140-160 実コーパス, live=fe0a6e6ab496):
1. **merge方式比較(fidelity_decisive.py)**: 単発N→N+1忠実度 = center-dist fac1.0:92% / fac1.3:81% / fac1.6:75%(過剰merge) / **overlap(実ポリゴン衝突)=97%**。**fac tuningは不要、実ポリゴンのshapes_collideで実overlap判定が最良**。過去メモリの「merge acc 61%」は粗いgradeモデル前提で、faithfulポリゴンsimは遥かに高精度。
2. **厳密検証(fidelity_rigorous.py, n=160)**: piece_count|err| median0.0/mean0.33/p90=1・**完全一致78%**(水増しでない)。**merge-event recall91%/precision76%**(simやや過剰merge)。落下位置|dx|median0.16(<<0.6)・p90=1.30。→ **overlap-simは浅いlookaheadに耐える単発忠実度**。
3. **決定的(lookahead_divergence.py, T13+局面 n=70)**: depth-1 Soviet-routing lookaheadはEXP-9と**84%異手**を選ぶが、**生存維持で2nd-chain材料を増やす異手=0%**。理由: **単発ドロップは2nd-chainの高ティア(T12+)材料をほぼ絶対変えない**(T12+マージは複数手蓄積要)。→ **Soviet routing信号は深horizonにしか存在しない**。

**★新しく精密化した壁(faithful-sim角度)**: simの粗さが壁ではない(単発97%/91% faithful)。壁は**horizonミスマッチ**: Soviet目的(2nd-chain発達)は深horizon(~30drop蓄積)に住み、そこでは(a)sim per-drop誤差が複利発散(replay早死を実証) + (b)live latency壁。**dense per-drop目的にすればEXP-9のSOVIET_NUCLEUS/BOLD/AVOID_BURYが既に符号化(≈EXP-9, B2実証)。sparse Soviet目的にすれば深horizon必要(fidelity/latencyで配備不可)**。両端で詰む=within-scope/B-architectureと同じ結論にfaithful-sim角度からも収束。
**tooling投資の正味成果(正直)**: ✓ verified asset=faithfulな単発forward model(overlap-sim)。✗ それを deployable な Soviet前進に変換する path無し(horizon mismatch)。→ **tooling投資は「なぜ無理か」を粗さでなくhorizonとして精密化したが、Soviet到達lever は産まなかった**。
**残る未試行lever(将来pass候補, within-scope strategy.py)**: 「1本目T15を早期完成させ chain2に残ターンをbank」(rush-1st-chain)。但しmemory既知の「169ターン長尺でも2nd-chain 0.88×止まり」=ターン数でなくpacking律速の可能性大→投機deployはmedian悪化(検証可能harm) vs Soviet改善(検証不能)でrisk非対称。faithful-simでoffline medium-horizon検証してからのみ。
**この pass の確定**: live=fe0a6e6ab496 健全(本日Russia 1件 00:50/score_med~1413/T13+ ~85%/live==head)。strategy.py無変更。Soviet=0継続。tooling角度も壁を破らず。次パス: 安定監視 + (任意)faithful-simでrush-1st-chain offline検証 + 新角度/ユーザー入力/Soviet発火で動く。

### §8追記★rush-lever実データ反証 + height@T14シグナル + BOLD飽和確認 (2026-06-23 04:00)
HEALTH全green(SOVIET monitor 2proc/loop9000/runner/live==head==fe0a6e6ab496/SOVIET log=1)。MEASURE: **大標本n=1500 corpus = T14+ 25%・T15+ 2.6% = EXP-9一致 → live(PROTECT_2ND_SEED)は≈EXP-9・no-harm確定**(前パスの「T14+ 10-18%」は窓ノイズと確定)。本日Russia 1件(00:50, 既存)。
**ADVANCE: 前パスで残した唯一の未試行lever「rush-1st-chain」を sim非依存の純データで決定的検証** (tmp/mcsim/rush_lever_analysis.py, T14+到達 n=380):
- **rush(早期T14)は反証**: turn_first_T14 vs 2nd-chain himat の相関 **r=-0.07(ほぼ無)**。runway r=+0.23/length r=+0.18/final_mat r=+0.14(弱正)。
- **真のシグナル=height@T14**: TOP-decile(2nd発達)games は T14到達時の盤面 **max_y=0.16 vs REST=0.43**(大差)。パス指示の診断「失敗組は高い」を実データで確認。
**height@T14レバーの実装可能形を全て trace → 全て飽和/禁止/実装済(liveコード直読で確認)**:
1. 圧縮強化 = **landing_y-only=postmortem禁止**(v338/v359 scatter失敗、コード全体に明記)。
2. drain強化 = git履歴 **EXP-4/5 が over-drain・pair率半減でrevert済**。
3. 2nd核早期育成 = **EXP-9 BOLD軸(strategy.py:2021, `not russia_phase`)が既に実装**。max_type==14&count==1 で building piece(T8+)を nucleus BESIDE配置し隣接2個目T14形成。コメント記録「broad版は T14+ 30%→13%に低下」=**既にT14+到達の縁まで調整済**。
**結論(実測)**: height@T14シグナルは実在するが、その実装可能レバーは(禁止/revert済/BOLD実装済)で**全て飽和**。安全にデプロイ可能な新形は無し。→ within-scope policy天井=EXP-9 を **height診断角度からも(今度はliveコード実読で)確定**。strategy.py無変更(BOLD既に最適化済・liveは健全)。
**残オプション(将来)**: ①BOLD beside-offset/gateのparam微調整(但しparam並列は天井破らず[[memory]]・低期待)②faithful-sim大規模offline MC研究(理論Soviet率の上限探索・但しdeep-horizon fidelity限界)③ユーザー(C)入力。当てずっぽうの≈/禁止形デプロイはしない。次パス: 安定監視 + 新角度/Soviet発火で動く。

### §8追記★★決定的再フレーミング: ソ連は「生存ターン数」律速 (2026-06-23 05:00, ユーザー"それで？"に対する実探索)
ユーザーの「それで？」(=飽和繰り返すな・行動せよ)に対し、**測るだけでなく忠実simで実際にソ連を建てに行った**:
**(1) 構築的探索(tmp/mcsim/soviet_build_search.py)**: 実near-Soviet盤面6個(一部既に1st=T14&2nd-cluster=T14)から、両チェーン最大化貪欲[4*min(c1,c2)+...]で16手探索×3feed。**結果 ソ連0/18・2nd-clusterはT14天井でT15未到達**。死なないrunでも両balance時に**1本目すらT15届かず両方T14停滞**=材料分割backfireを忠実simで構築的実証(相関的≈より遥かに強い)。
**(2) 材料検算(決定的)**: マージはT1換算保存→**材料効率100%(無駄ゼロ)**。1ゲーム配給材料=**中央値15,448(ソ連32,768の47%)・最大34,157(104%)**。配給≥2×T15(ソ連床)は**全ゲームの0.2%のみ**。≥1×T15は40%。
**(3) 生存ターン分布**: median85turn/p90 116/max201。**corr(turns, 配給材料)=+0.75**。ソ連床32,768には**~178ターン生存が必要**だがEXP-9 median=85(47%地点で死)。
**★結論(定量)**: ソ連の第一律速は2nd-chain構築でも効率でもなく**生存ターン数**。EXP-9はスコア最適化ゆえ高ピース早期構築→un-mergeable singletonが盤面を詰まらせ**85ターンで早死**。ソ連には**survival-first/build-late(盤面を低くmergeableに保ち高ティア commitを遅延、150-200ターン生存して2×T15材料を蓄積、終盤に2本構築)**という**EXP-9と根本的に異なる方策**が必要。
**★検証可能性**: survival-firstは**中間指標(turns survived/材料handed)で検証できる**(稀なソ連event不要)=これまでのunverifiableレバーと違う。
**★トレードオフ(ユーザー判断要)**: survival-first=高ティア構築遅延=**スコア低下・長いゲーム・配信のgames/hour減**。EXP-9はスコア最適化(配信映え)。**ソ連を本気で狙う=スコアを犠牲にする**。→ユーザーに(A)survival-first追求 vs (B)EXP-9維持 を確認。

### §8追記★★survival-first 反証 = EXP-9はPareto最適 (2026-06-23 05:30, ユーザー"survival-first追求"選択→offline先行検証)
ユーザーが survival-first を選択。**約束通りデプロイ前にoffline(忠実sim)で「生存が実際に伸びるか」を先行検証** (tmp/mcsim/survival_first_test.py, full-game n=6/policy, 相対比較):
| policy | turns med | peak material max |
|---|---|---|
| build_greedy(積極構築=EXP-9型, w_build4/w_height1.5) | **98** | **20786 (63% of Soviet)** |
| survive_low (w_height8/w_clog4) | 86 | 17312 (53%) |
| survive_xtrem (w_height14/w_clog8) | 79 | 17312 (53%) |

**★survival-firstは反証**: 生存ターンも蓄積材料も **build_greedy > survive_low > survive_xtrem** で単調。**構築を遅らせるほど早く死に材料も少ない**。理由=**マージ(=構築)こそ盤面からピースを除去する唯一機構**。構築遅延→低ピースclog→早死。survive_xtremは盤面を最も低く保つのに最速死(simは低盤面を有利判定するバイアスがあるのに負けた=結論強固)。
**★EXP-9はPareto最適**: 積極構築はスコアだけでなく**生存・材料蓄積でも最適**。survival-firstは「スコア低下×生存短縮」の最悪両取り→**デプロイせず**(先行検証の価値実証)。
**★最深の結論**: ソ連にスコア vs 生存のtradeoffは**存在しない**。build-greedy(=EXP-9)が全軸でPareto最適。材料天井(~63% sim / 実tail 104%)は**盤面幾何の硬い限界で、どの方策でも超えられない**(構築最適でも死ぬまでに2×T15を積めない)。
**ソ連の真のgate(確定)**: (a)178ターン生存して2×T15材料が配られる稀な長尺tail(全game 0.2%・運律速・EXP-9が既に期待生存最大化) × (b)その材料を2本chainに完全packing(BOLD軸が狙うが soviet_build_search で 0/18・1本に collapse)。**両gateとも per-drop policyで動かせない**。
**within-scope(strategy.py/analyze_board.py)の最後の未検証レバー(survival-first)も反証で消滅**。Soviet到達には per-drop以外(別engine仮定/別game/lottery受容)が要る=要ユーザー判断。strategy.py無変更・live=fe0a6e6ab496健全継続。
