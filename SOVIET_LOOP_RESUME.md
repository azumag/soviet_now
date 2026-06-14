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
python3 extract_decide_hash.py strategy.py  # 期待: d5fff9501436 (EXP-3=確定ベスト。EXP-6は2026-06-15棄却・revert済)
python3 -c "import json; print(json.load(open('tmp/state/active_branch.json'))['head_hash'])"  # 期待: d5fff9501436 と一致
grep -c 'SOVIET UNION CREATED' logs/soren_loop.log   # 1 = frozen game29557のみ。>1 で新ソ連！
```

---

## 1. 現在の状態 (2026-06-15)

- **稼働中の戦略 (live=head)**: `d5fff9501436` = **EXP-3 (LOW_DRAIN_CLUSTER)** = 確定ベスト。**EXP-6は2026-06-15 棄却・revert済**（commit 27d123f7f）
- **frozen 復元先**: `d88fc8bfd580`（`tmp/goal_restore_20260604/RESTORE_FROZEN.sh` で復元）
- **ソ連建国**: まだ達成なし（マーカー=1=過去のframezn game29557のみ）。Russia(単独)は ~5% で散発
- **ループ稼働**: soren_loop.sh PID 9000。strategy_runner は毎ゲーム別プロセス起動 → **strategy.py / analyze_board.py の変更は次ゲームに自動反映**（手動restart不要）

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
for tgt,lab in (('d5fff9501436','EXP-3(current best)'),):   # 新実験デプロイ時に比較対象を追加
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
