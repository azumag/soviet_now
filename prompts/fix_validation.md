前回の strategy.py.staging 改善でバリデーションが失敗した。strategy.py.staging はオリジナルに戻してある。
以下のエラーを踏まえて、改めて改善せよ。

## 前回のエラー
${VALIDATE_ERROR}

## 修正ルール
- strategy.py.staging を改善して上記エラーを回避せよ
- 1回の改善で1つの変更のみ。シンプルに保て
- decide(game_state, analysis) のシグネチャは変更禁止
- if __name__ == "__main__" ブロックは変更禁止
- decide() は必ず {"x": float, "reason": str} を返すこと
- `strategy.py.staging` を直接 `Edit` して修正すること
- `strategy.py.staging` 以外のトップレベル `.py` は新規作成しないこと
