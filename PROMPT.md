- README.md と SOVIET_GAME_GUIDE.md を読んで、ゲームをプレイしてください。
- STRATEGY.md を戦略の参考にすること
- ゲームサーバーは起動している前提で、自分で起動は行わない。
- **最重要: `game_state.json` を最初に読め** → 盤面の正確な構造データが得られる。画像分析よりも信頼性が高い。
  - `cursor`: 現在持っているピース（国名とlevel）
  - `next`: 次のピース（国名とlevel）
  - `board.pieces`: 盤面上の全ピース（位置・国名・level）
  - `board.columnHeights`: 各カラムの高さ（%）→ 低いカラムに優先的に落とす
  - `board.dangerLevel`: 危険度（"low"/"medium"/"high"/"critical"）
  - `board.mergePairs`: 併合可能なペア（同じ国旗が近くにある）
  - `gameOver`: ゲームオーバー検出（true/false）
- 国旗名は level 順: armenia(1), estonia(2), latvia(3), lithuania(4), georgia(5), azerbaijan(6), tajik(7), kyrgyz(8), belarus(9), uzbek(10), turkmen(11), ukraine(12), kazakh(13), russia(14), ussr(15)
- `gameOver: true` のときは `retry` コマンドを書き込め（画像確認不要）
- 画像分析は game_state.json の補助として使うこと：
  - `soviet_now.png` → 全体俯瞰・デッドラインまでの余裕を確認
- コマンドに実行するに至った考えを表示すること。
- 前回の考えは think.md に記載されていることがあるので確認するべし
- 考えは think.md に上書きすること
```
echo "考えたこと" > think.md
```
- できれば日本語で表示すること。
- 一回だけ書き込みを実行したら、終わってください
- ゲームオーバーになったら原因を分析し、戦略 STRATEGY.md をアップデートすること
- パズルゲームの戦略家として、冷静に分析してください
- 落ち着いた口調で。