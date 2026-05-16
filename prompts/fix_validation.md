前回の strategy.py.staging 改善でバリデーションが失敗した。現在の strategy.py.staging には、前回までの部分編集結果が残っている可能性がある。
以下のエラーを踏まえて、改めて改善せよ。
この retry は前回セッション継続前提である。前の分析・読込結果は保持されている想定なので、必要な再読込は最小限にし、以下の問題だけを直せ。

## 前回のエラー
${VALIDATE_ERROR}

## 修正ルール
- strategy.py.staging を改善して上記エラーを回避せよ
- `strategy.py.staging` は continuation 中の実編集対象であり、これを唯一の編集コンテキストとして扱うこと。`strategy.py` を編集根拠として読んではいけない
- まず現在の `strategy.py.staging` を1回 `Read` して、現状態を確認してから `Edit` すること
- 問題修正に直接必要な差分だけを入れ、前回の改善方針そのものはむやみに捨てないこと
- 現在の `strategy.py.staging` と今回触る周辺に明確な既存バグや partial edit 崩れがあれば、低リスクな範囲で一緒に直してよい
- 1回の改善で1つの変更のみ。シンプルに保て
- decide(game_state, analysis) のシグネチャは変更禁止
- if __name__ == "__main__" ブロックは変更禁止
- decide() は必ず {"x": float, "reason": str} を返すこと
- `strategy.py.staging` を直接 `Edit` して修正すること
- 数値だけの修正でもよいが、今回のエラー回避に必要な最小限かつログ根拠のある変更に限ること
- コメントや `reason` 文言だけの修正で済ませないこと
- `turns >= N` の固定ターン数で終盤判定を足さないこと。終盤危険局面は盤面状態で表現すること
- **【不可侵の安全不変条件】** `crosses_deadline == True` の選択肢は、`crosses_deadline == False` の安全な選択肢が1つでも存在する限り、`merge_grade`(DIRECT/NEAR/FAR を含む)・merge-drought・HIGH_LAYER 抑制・タワー戦略など**いかなる理由でも選んではならない**。デッドライン超過は即ゲームオーバーであり、投機的マージより常に回避を優先する。`AssertionError: deadline-(far|near|direct)-guard` が出ている場合、それは安全な非超過 x が存在するのに deadline 超過 x を選んでいる証拠なので、その分岐に「安全な非超過候補があるなら超過候補を絶対に選ばない」ガードを最優先で入れること。このガードを既存ロジック(merge 優先等)より前段に置くこと
- 別名の `.py` を新設して逃げないこと。前回作ってしまった不要トップレベル `.py` を再作成してはいけない
- `strategy.py.staging` 以外のトップレベル `.py` は新規作成しないこと
- `Edit` が2回連続で失敗した場合は、`strategy.py.staging` の該当箇所だけを狭く再読込して、より小さい差分でやり直すこと
- `Could not find oldString` や `No changes to apply` が出たら、同じ patch を繰り返さず、`strategy.py.staging` の実際の現状態に合わせて patch を作り直すこと
