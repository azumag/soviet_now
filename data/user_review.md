# 再レビュー: 改善 `70114388976d` は未充足

前回レビューは改善で消費されましたが、改善後 1 本目の完了ゲームで同じ失敗が再発しています。
このレビューを次回改善で最優先に扱ってください。

## 判定

未充足です。`data/user_review.md` の前回要求「赤線越え NO を返さない」「runtime override 後も crossing NO にしない」は満たされていません。

改善後の現行 hash:
- `strategy.py` decide hash: `70114388976d`
- `tmp/state/current_strategy_run.json`: 1 game, raw score `1335`, eval `10121`
- `data/user_review.md` は改善後に空になっていたため、前回レビューは消費済み

## 失敗ログ

対象:
- `game_history/20260513_220152_score1335.jsonl`

この 1 本だけで、`decision_crosses_deadline=true` かつ `best_merge_grade=NO` が 7 回出ています。

特に悪い turn:
- T63: `best_merge_grade=NO`, `decision_crosses_deadline=true`, `RUNTIME_DEADLINE_SAFETY_OVERRIDE_NO_TO_NO`
- T75: `best_merge_grade=NO`, `decision_crosses_deadline=true`, `RUNTIME_DEADLINE_SAFETY_OVERRIDE_NO_TO_NO`
- T90: `best_merge_grade=NO`, `decision_crosses_deadline=true`, `RUNTIME_DEADLINE_SAFETY_OVERRIDE_NO_TO_NO`
- T92: `best_merge_grade=NO`, `decision_crosses_deadline=true`, `RUNTIME_DEADLINE_SAFETY_OVERRIDE_NO_TO_NO`
- T93: `best_merge_grade=NO`, `decision_crosses_deadline=true`, `RUNTIME_DEADLINE_SAFETY_OVERRIDE_NO_TO_NO`
- T94: `best_merge_grade=NO`, `decision_crosses_deadline=true`, `RUNTIME_DEADLINE_SAFETY_OVERRIDE_DIRECT_TO_NO`
- T95: `best_merge_grade=NO`, `decision_crosses_deadline=true`, `RUNTIME_DEADLINE_SAFETY_OVERRIDE_NO_TO_NO`

T94 は特に重大です。`DIRECT_MERGE` 系の理由を持つ候補から、runtime safety override が最終的に `NO` へ落としており、しかも crossing のままです。
これは「デッドライン越えは game over なので攻め筋より優先して禁止」という要求に反します。

## 実装上の未充足

### 1. `strategy.py` は候補集合を絞っていない

現行実装は `strategy.py` の候補ループ前で以下を計算しているだけです。

- `deadline_safe_drop_exists`
- `deadline_buffer_drop_exists`
- `min_deadline_risk_top`

しかしその後も `for result in results:` で全候補を評価しています。
つまり、前回レビューの「score penalty ではなく候補集合を絞る」は未実装です。

現状の問題箇所:
- `strategy.py` around L1012-L1043: safe/buffer 情報を計算するが、`active_results` のような評価対象集合を作っていない
- `strategy.py` around L1087-L1091: crossing 候補には `-1000000` を付けるだけ
- 結果として `DEADLINE_CROSS_NO_MERGE_HARD_BLOCK` が理由に入った候補が、実際に選ばれて履歴に残っています

必須修正:
- 候補ループ前に `active_results` を作ってください。
- `buffered_results` があれば `active_results = buffered_results`
- そうでなく `safe_results` があれば `active_results = safe_results`
- `safe_results` が 1 つでもある限り、`crosses_deadline=True` は候補ループに入れないでください。
- `for result in results:` ではなく `for result in active_results:` にしてください。

### 2. 全候補 crossing 時でも NO crossing を安易に許している

全候補が crossing する詰み局面でも、NO merge crossing を雑に選ぶのは不可です。
その場合は「少しでも top を下げる、または増やさない」候補だけに寄せる必要があります。

必須修正:
- 全候補 crossing の場合、第一条件は `risk_top_y_after_drop` 最小。
- `DIRECT`/`NEAR` は、`risk_top_y_after_drop <= min_risk_top + 0.05` の範囲内でのみ優先。
- `DIRECT` であっても、top を大きく上げるなら禁止。
- `NO` で `risk_top_y_after_drop > min_risk_top + 0.05` は禁止。

T94 の `RUNTIME_DEADLINE_SAFETY_OVERRIDE_DIRECT_TO_NO` は、この条件の失敗例です。

### 3. `strategy_runner.py` の override は保険になっていない

`strategy_runner.py` の `enforce_deadline_safety()` は `safe` / `buffered` を持っていますが、最終的に crossing NO を返しています。
履歴に `RUNTIME_DEADLINE_SAFETY_OVERRIDE_*_TO_NO` と `decision_crosses_deadline=true` が同時に出ているため、現状は safety override として不合格です。

改善AIが `strategy_runner.py` を編集できない制約があるなら、`strategy.py` 側でこの経路に入らないようにしてください。
編集できるなら、runner 側も次を満たしてください。

必須修正:
- `safe` がある場合は `safe` 以外へ絶対に差し替えない。
- `buffered` がある場合は、grade より `risk_top` を優先する。
- `safe` がない場合でも、`NO` crossing は `risk_top` 最小候補に限る。
- override 後の `replacement` が `crosses_deadline=True` かつ `merge_grade=NO` なら、理由を付けて終わりではなく、再選択する。

### 4. axis 8.8 の危険域スキップがまだ残っている

現行実装では、axis 8.8 が次の条件で NO merge penalty を丸ごとスキップしています。

```python
if reactive_pair_count >= 3 and merge_grade == "NO":
    if not (deadline_crossed and max_y >= 2.5):
        ...
```

これは前回レビューで「逆効果」と指摘した箇所が未修正です。
今回の `score1335` でも後半は `REACTIVE_PAIRS_NO_MERGE_GRAVITY_PENALTY` と crossing NO が絡んでいます。

必須修正:
- `deadline_crossed and max_y >= 2.5` で axis 8.8 を無効化しない。
- 危険域ではむしろ `risk_top_y_after_drop` が高い NO 候補ほど強く罰する。
- 全候補一律ではなく、`risk_top_y_after_drop - min_risk_top` の差分で罰する。

### 5. `no_merge_streak` 依存はまだ未解決

現行 `strategy.py` は `no_merge_streak = game_state.get("no_merge_streak", 0)` のままです。
`strategy_runner.py` 側に `no_merge_streak` を注入する実装は確認できません。
したがって axis 9.16 は、今回も期待通り効いていない可能性が高いです。

必須修正:
- `strategy.py` 側で `_decision_history` があれば読む。
- 履歴がない場合は `same_type_pieces >= 2 and reactive_pair_count >= 3 and merge_grade == "NO"` を drought とみなす補助条件を使う。
- ただし drought traction は「reactive pair centroid に寄せる」だけでは不十分。反応回廊の間に差し込む候補は罰する。

## 次回改善の合格条件

次回改善後、少なくとも以下を満たすこと。

- 改善後の最初の完了ゲームで `decision_crosses_deadline=true and best_merge_grade=NO` が 0 件。
- `RUNTIME_DEADLINE_SAFETY_OVERRIDE_*_TO_NO` と `decision_crosses_deadline=true` の組み合わせが 0 件。
- `strategy.py` に `active_results` 相当の候補集合フィルタが入り、safe/buffered 候補があるとき crossing 候補を評価しない。
- axis 8.8 が `deadline_crossed and max_y >= 2.5` で無効化されない。
- `no_merge_streak` が runner から供給されない前提でも drought 判定が機能する。
