# Soviet Game Controller

ゲームをファイルから操作する。
操作後、盤面の状態が `game_state.json` と `soviet_now.png` に自動保存される。

## 出力ファイル

| ファイル | 説明 |
|---------|------|
| `game_state.json` | **最重要** — 盤面の構造データ（全ピース位置・国名・危険度・ゲームオーバー検出） |
| `soviet_now.png` | スクリーンショット（補助用） |

### game_state.json の主要フィールド
- `gameOver` (bool) — ゲームオーバー検出。true なら `retry` コマンドを送れ
- `cursor` — 現在持っているピース（`type.name` = 国名, `type.level` = レベル）
- `next` — 次のピース
- `board.pieces` — 盤面上の全ピース（位置・国名・レベル）
- `board.columnHeights` — 各カラム（7列）の高さ%
- `board.dangerLevel` — 危険度 ("low"/"medium"/"high"/"critical")
- `board.mergePairs` — マージ可能なペア（同じ国旗が近距離にある）

## コマンドファイル形式

`commands.txt` に以下の形式で記述：

### 形式1: x,y 形式（1行につき1コマンド）

```
640,350
400,350
800,350
```

### 形式2: retry コマンド

ゲームオーバー時（`gameOver: true`）にリトライする。

```
retry
```

## 操作例

```bash
# 単一クリック
echo "640,350" > commands.txt

# 複数クリック
cat > commands.txt << EOF
640,350
400,350
800,350
EOF

# JSONで一括指定
echo '[{"x":640,"y":180},{"x":400,"y":200}]' > commands.txt

# ゲームオーバー時にリトライ
echo "retry" > commands.txt
```

可能な範囲は x が 400-900, yが350固定である。