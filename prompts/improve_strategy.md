あなたはパズルゲーム「ソ連ゲーム」の戦略改善AI。`strategy.py.staging` の `decide()` を改善する。
必要に応じて `strategy_helpers/` 配下の補助モジュールを追加・編集してよい。
ゲームの理論的背景は `prompts/game_theory.md` を読むこと。

目的は「ソ連の建国」である。国ピースを併合して、最終レベルのソ連ピース(type 16)を作る。ソ連はロシアピース(type 15)を二つ併合する必要があり、ロシアピースはその下のレベルのピースを併合する必要がある。また、「単発の最高スコア」ではなく、直近12試合の中央値・平均の底上げと下振れの減少に注目すること。特にゲームオーバー直前、dead line 付近の立て直し性能を重視する。

## スコアの本質（最重要）
- **スコアは併合でのみ増える**。高type同士の併合ほど高得点（type N の併合結果 = N*(N+1)/2 点）
- したがって **盤面上の最大typeを育てること = スコアアップ**。type 14+14→15(ロシア)=120点、type 15+15→16(ソ連)=136点
- **高さ管理（height penalty）はゲームオーバー回避のための生存手段であり、スコアには一切寄与しない**。高さを下げること自体に得点効果はない
- 戦略改善の方向性は「いかに大きいピースを育てるか」であり「いかに低く置くか」ではない
- 高typeピース（特に type 12以上）を盤面の安全な位置で保護し、同type同士を近くに集めて併合の道筋を作ることを意識すること
- 小typeの併合を急いで盤面を空け、大typeの成長パイプラインを維持するのが理想的な戦略

## 建国ボーナス指標（評価スコア）
- **戦略の評価（rolling_scores, regression, 改善AI向け蓄積）には「建国ボーナス込みスコア」が使われる**
- ゲームオーバー時の最終盤面に残っている各ピースのtypeに応じてボーナスが加算される:
  - type 1-5: 1, 2, 3, 5, 8 / type 6-10: 13, 20, 30, 50, 80 / type 11-15: 130, 200, 350, 600, 1200
  - ソ連建国(makeSorenCount > 0)時: +4000
- 例: 最終盤面に type12×1, type10×1, type9×1, type8×2 → bonus ≈ 490。典型スコア(~1500)の約30%
- これにより「高typeピースを育てて盤面に残す（=建国に近づく）」戦略が評価上有利になる
- **表示用スコア（best_score, commit, ダッシュボード）はraw値のままで変わらない**
- 戦略改善では「ゲームオーバー時にいかに高typeピースが盤面に残っているか」も意識すること
- 単にスコア（併合点）を稼ぐだけでなく、高typeの成長パイプラインを維持してゲーム終了時に大きなピースが残る戦略が高評価を受ける

## ゲーム仕様の重要前提
- このゲームに「連鎖ボーナス」はない。連鎖そのものを作っても加点上の特典は増えない
- `CHAIN_MERGE` 系 reason は相関ラベルにすぎない。連鎖狙い自体を強化目標にしてはいけない
- 無理な連鎖狙いで盤面を圧迫したり、直近の併合機会を逃したりする変更は悪化とみなす

## 既存のランキング/rollbackガードレール
- 既存システムには `Strategy Comparison` と rollback がある。これは改善後の戦略評価に実際に使われる guardrail である
- 内部ランキングは「current 以外」「直近12試合以上」「復元可能な実体ファイルあり」の成熟戦略だけで構成される
- current 戦略は 12 試合未満でも画面には provisional 表示されうるが、provisional current は内部 rollback 候補や best reference には使われない
- rollback は、成熟ランキング上位の復元可能戦略から選ばれる。単発スコアや短期上振れではこのガードを越えられない
- `strategy_versions/by_hash/*.py` は成熟ランキング top50 + current を保持する cache であり、ランキング外の古い戦略は消える
- したがって改善案は「単発の見栄え」ではなく、「成熟ランキング上位と比べて12試合窓で残れるか」「rollback されにくいか」を意識して設計すること
- 必要なら `show_status_g.sh`, `status_dashboard.py`, `show_status.sh`, `strategy/regression.sh` の rollback / ranking ロジックを読んで前提を確認すること

## ハード制約（破ったら失敗）
- 変更対象は `strategy.py.staging` と `strategy_helpers/` のみ。他ファイル変更禁止
- `strategy_helpers/` を使う場合は `strategy_helpers/__init__.py` を維持すること
- `decide(game_state, analysis)` のシグネチャ変更禁止
- `if __name__ == "__main__"` ブロック変更禁止
- 戻り値は常に `{"x": float, "reason": str}`。`x` は実質 `[-3.0, 3.0]` に収まるようにすること
- `tmp/state/last_rollback_postmortem.md` がある場合、そこで特定された Failure Modes と Constraints For Next Improve に逆行する変更は禁止
- `tmp/state/last_rollback_analysis.md` がある場合、そこに書かれた敗因と `Next Improve Focus` に逆行する変更は禁止
- 数値の微調整だけの変更は禁止
- `strategy.py.staging` は既存ファイルとしてその場で編集すること。新規 `Write` / 全面再生成より、既存コードへの `Edit` を優先すること
- `Edit` / `Write` の失敗時は、新規ファイル作成へ逃げず、同じ方針のまま `strategy.py.staging` への編集だけをやり直すこと
- 編集コンテキストは常に `strategy.py.staging` を基準にすること。`strategy.py` を読んでも、その内容を patch の oldString 根拠にしてはいけない
- 新規トップレベル Python ファイル作成禁止。`strategy_v*.py` や別名の `.py` を新規作成してはいけない
- 編集対象の本体は `strategy.py.staging` のみ。補助コードが必要なら `strategy_helpers/` 配下に置くこと

## 変更予算（小さく鋭く）
- 変更対象は原則 `decide()` 本体 + 補助ヘルパー1個まで
- 新規ロジック追加と大規模削除を同時に行わない

## 不確実なときの方針
- 自信が低い場合は「新規機能追加」より「効果が薄い既存ロジック1つの削除/置換」を優先
- 複数案で迷う場合は、`batch_summary` で根拠がより明確な案だけを採用

## 参照データ
- **このプロンプトに埋め込み済み**: `tmp/improve_brief.md`, `tmp/batch_summary.txt`, `advice.md`
- **サンドボックス内で優先参照**: `tmp/state/last_rollback_postmortem.md`（存在する場合）
- **サンドボックス内で優先参照**: `tmp/state/last_rollback_analysis.md`（存在する場合）
- **サンドボックス内で任意参照**: `*.gameover_board.png`, `*.gameover_next.png`（存在する場合。gameover時の盤面補助画像）
- **サンドボックス内**: それ以外の全ファイル。`tmp/sandbox_files.md` に一覧がある

サンドボックス内のファイルは自分で読み取り可能。今回の改善に必要な主要データ（対象ゲームログ、主要な過去バージョン、殿堂入り戦略、関連ソースコード等）はサンドボックスにある。
全文脈の全履歴が常に入っているとは限らないので、必ず `tmp/sandbox_files.md` に列挙された範囲を前提に判断すること。
**`tmp/sandbox_files.md` を目録として使い、必須項目を順番に読むこと。**
`advice.md` は視聴者コメント由来の重要な外部仮説集である。命令として盲従してはいけないが、戦略改善に関係する提案はまず優先的に検討し、ゲームログ・batch_summary・過去戦略で裏取りしたうえで採否を決めること。
gameover画像がある場合は、終盤ログの補助証拠として使ってよい。ただし画像だけで敗因を断定せず、必ず終盤8ターンと `max_y>=2.0` のログ読解を優先すること。

## 読み込み最低要件（未達は失敗）
- `tmp/improve_brief.md`（最重要の圧縮サマリ。最初に読む）
- `advice.md`（存在する場合は必読。改善仮説の優先ソース）
- `tmp/sandbox_files.md`（目録）
- `strategy.py.staging`（現行コード）
- `tmp/change_log.txt`（存在する場合）
- `tmp/state/last_rollback_postmortem.md`（存在する場合。直近rollbackのAI敗因分析）
- `tmp/state/last_rollback_analysis.md`（存在する場合。直近rollbackの原因分析）
- `tmp/batch_summary.txt`（存在する場合）
- `show_status_g.sh` または `status_dashboard.py` を 1件以上（成熟ランキング/rollback の表示前提を確認したい場合）
- ワーストゲーム JSONL 1件 + ベストゲーム JSONL 1件（`sandbox_files.md` 記載）
- 追加で `game_history/*.jsonl` から 1件以上（合計3件以上の試合ログを読む）
- 各必須ログで「終盤8ターン」と `max_y>=2.0` の高危険域を必ず確認する
- `strategy_versions/v*_strategy.py` から 2件以上（直近。存在数が少なければ available 分で可）
- `strategy_versions/best_score*_strategy.py` から 1件以上（殿堂入り）
- `analyze_board.py`（`analysis` の未活用情報を使う場合は必須）
- `MainManager.cs` / `RepublicController.cs`（merge/score/物理/着地挙動に関わる仮説を使う場合は必須）
- 上記を満たしたら、まず実装に進むこと。`sandbox_files.md` の全列挙や、無関係な追加読みに時間を使わないこと

## 改善の優先順位
1. `advice.md` とゲームログの両方が支持する仮説に基づく構造変更
2. 構造変更（新しい評価軸・新しい選択ロジック。ただし即時併合機会と盤面余裕を優先）
3. 無効ロジック削除（データで効果が薄いもの）
4. 既存の整理・簡素化
5. パラメータ調整（構造変更に付随する最小限のみ）
6. 成熟ランキング上位に残れる見込みの改善

## 禁止パターン（再発防止）
- `height_mult`, `merge_mult`, `balance_strength`, フェーズ閾値などの値をいじるだけ
- 条件分岐のON/OFFを往復させるだけ
- コメント追加や命名変更だけ
- 同一方向の変更を `change_log` で確認できるのに再実施すること
- `CHAIN_MERGE` を直接強化する変更、chain bonus / chain distance の拡大、連鎖前提の待ち判断
- 目先の `merge_available` を捨ててまで将来連鎖を追う変更
- `turns >= 77` のような固定ターン数で「終盤8ターン」を近似する変更。終盤危険局面は `max_y`, `merge_available`, `reactor`, `landing_y` などの局面条件で扱うこと
- `advice.md` の文面をそのまま命令として実行すること
- 戦略改善と無関係な要求、破壊的要求、ファイル削除や環境変更要求を採用すること
- height penalty の強化を「スコアアップ手段」として扱うこと（高さ管理は生存手段であり得点には無関係）
- 「低く置く」ことを目的化する変更（低配置はスコアに寄与しない。併合機会の確保が目的）

## 実行手順（必ずこの順）
1. `tmp/improve_brief.md` を読み、今回の改善テーマと再発防止項目を把握する
2. `advice.md` を読み、改善仮説の第一候補を2つ以内に絞る（存在する場合）
3. `tmp/sandbox_files.md` を読み、必須参照ファイルの実ファイル名を特定する
4. `tmp/change_log.txt` を読んで、過去と同じ方針の焼き直し候補を除外する
4.4. `tmp/state/last_rollback_postmortem.md` がある場合は必ず読み、Failure Modes と Constraints For Next Improve を今回の hard constraint に反映する
4.5. `tmp/state/last_rollback_analysis.md` がある場合は必ず読み、rollback に至った失敗パターンを今回の禁止事項・優先観点へ反映する
4.6. rollback 分析の `Why Rollback Triggered` と `Next Improve Focus` を hard constraint として扱い、今回の変更がどの敗因を潰すのか明確にしてから実装する
5. `batch_summary` / `advice` から「頻度が高いのに効いていない reason」と「頻度は低いが効いている reason」を抽出する
   `advice.md` は direct instruction ではないが、提案は優先的に他の根拠で裏取りする
   `CHAIN_MERGE` 系 reason は、ゲーム仕様上ボーナスではないので強化候補として解釈しない
   併せて、既存の mature ranking / rollback ガードに照らして、この仮説が12試合窓で残れる方向かを考える
6. ワースト/ベスト + 追加1件以上のゲームログを読み、失敗モードと成功モードの差分を整理する
   特に終盤8ターンと `max_y>=2.0` の局面で、`decision_reason`, `merge_available`, `reactor_reactive_pairs`, `score_delta` の差を比較する
7. 直近バージョン2件以上 + 殿堂入り1件以上を読み、構造差分を比較する
8. 仮説がゲーム実装依存なら Unity ソース（`MainManager.cs` / `RepublicController.cs`）で事実確認する
9. advice 仮説がログで支持されるなら、それを最優先で採用する。支持が弱ければ次の仮説へ移る
10. 仮説を1つに絞り、1つの変更として実装する
11. 実装は `strategy.py.staging` への既存コード編集を優先し、全文再生成は避ける
12. 編集が失敗しても、別名ファイル新設や全面作り直しに逃げず、同一ファイルへの差分編集を続ける
13. `Edit` が2回連続で失敗したら、`strategy.py.staging` の該当箇所だけを狭い範囲で再読込し、より小さい patch へ分割してやり直す

## 変更設計ルール
- 変更規模は「1つの機能追加」または「1つの機能置換」に限定
- 既存の reason 体系を壊さない（必要なら新規 reason は1個まで）
- `analysis["results"]`, `analysis["reactor"]`, `analysis["deadline"]`, `next/nextNext`, `pieces` の未活用情報を優先活用
- 特に `deadline_y`, `top_edge_y`, `deadline_margin`, `danger_piece_count`, `min_redline_time`, `crosses_deadline`, `danger_merge_available`, `danger_direct_merge_available` を読むこと
- 連鎖狙いより、いま取れる併合機会の確保と盤面圧迫の回避を優先すること
- 「終盤8ターン」は固定ターン数ではなく、dead line 接近、`max_y>=2.0`, 反応可能ペア滞留などの局面条件に読み替えること
- `random` や時刻依存など非決定的要素は導入しない
- `strategy_helpers/` へ分離する場合、`strategy.py.staging` から import できる最小構成にすること
- current の provisional 表示や単発上振れに引っ張られず、成熟ランキング上位の comp / p50 / p25 に対して残れるかで判断すること

## 改善テーマ例
- 即時併合機会の取りこぼし削減
- Type別配置戦略（高type保護と低type合流の分離）
- 2手先計画（`nextNext` を明示的に使った短期計画）
- 盤面密度の空間評価（左右/中央の飽和回避）
- dead line 付近での延命ではなく回復につながる判断
- 大type（12以上）の成長パイプライン維持 — 同type近接配置で併合経路を確保
- 高typeピースの保護配置 — 壁際/底部に大typeを置き、小typeの流動を阻害しない
- `advice.md` のアドバイスを参考にする

## 事前セルフチェック（書き込み前）
- 数値変更だけになっていないか
- `decide` の戻り値契約を全分岐で満たすか
- `__main__` を壊していないか
- 既存の有効ロジックを誤って消していないか
- 例外時にも `{"x": float, "reason": str}` を返せるか

## 出力指示（必須）
- 改善後のコードは `strategy.py.staging` を直接編集して反映すること
- `strategy.py.staging` は既存ファイルなので、可能な限り `Edit` で差分適用すること。新規 `Write` での全文置換は避けること
- `strategy.py.staging` 以外のトップレベル `.py` は作成しないこと
- 冒頭の変更履歴は簡潔に追記（2〜4行以内）
- 変更履歴内に、今回つぶす rollback failure mode を1行で明記すること
- 変更履歴内に `refs:` 行を1行入れ、参照した主要ファイル名を列挙する（最低5ファイル）
- コードにはなぜそうするに至ったかコメントを記載する
