## 起動方法

```bash
node soviet_game.mjs
```

## 仕組み

1. ブラウザを開き、ゲームを自動起動
2. WebGL Draw Callフックで盤面状態をリアルタイム取得
3. `commands.txt` を監視（500ms間隔）
4. コマンドを検出すると座標をクリック
5. 操作後に `game_state.json`（盤面データ）と `soviet_now.png`（スクリーンショット）を保存

## 出力ファイル

| ファイル | 説明 |
|---------|------|
| `game_state.json` | **主要データ** — 盤面の全ピース位置・国名・危険度・ゲームオーバー検出 |
| `soviet_now.png` | スクリーンショット（補助用） |

### game_state.json 構造

```json
{
  "gameOver": false,
  "cursor": { "x": 0.5, "type": { "level": 3, "name": "latvia" }, "scale": 1.55 },
  "next": { "type": { "level": 1, "name": "armenia" }, "scale": 1.0 },
  "board": {
    "dangerLevel": "low",
    "pieceCount": 5,
    "columnHeights": [0, 19, 21, 0, 0, 4, 5],
    "balance": -20,
    "pieces": [
      { "x": -1.04, "y": -2.86, "type": { "level": 6, "name": "azerbaijan" }, "scale": 2.11 }
    ],
    "mergePairs": [
      { "type": "georgia", "p1": { "x": 1.95, "y": -4.18 }, "p2": { "x": 2.66, "y": -4.09 }, "distance": 0.72 }
    ]
  },
  "summary": "Cursor: latvia | NEXT: armenia | Pieces: 5 | Danger: low | Heights: [0,19,21,0,0,4,5]"
}
```

### 国名マッピング（15レベル）
armenia(1) → estonia(2) → latvia(3) → lithuania(4) → georgia(5) → azerbaijan(6) → tajik(7) → kyrgyz(8) → belarus(9) → uzbek(10) → turkmen(11) → ukraine(12) → kazakh(13) → russia(14) → ussr(15)

### ゲームオーバー検出
- `gameOver: true` — カーソルとNEXTが2フレーム連続で消失し、盤面にピースが残っている状態
- コンソールに `[GAME OVER]` プレフィックスが表示される

### スケール値デバッグ
- 新しいscale値が検出されるたびに `[SCALE] New: 1.234 (board)` のようにコンソール出力
- scaleToType の閾値調整に使用する

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

### 形式3: JSON形式

```
[{"x":640,"y":350},{"x":400,"y":350}]
```

## 操作例

```bash
# 単一クリック
echo "640,350" > commands.txt

# ゲームオーバー時にリトライ
echo "retry" > commands.txt
```

## 入力ファイル

| ファイル | 説明 |
|---------|------|
| `commands.txt` | 操作コマンド入力用（外部から書き込み） |

## 座標について

- Canvasサイズ: 1280x720
- 有効範囲: x=400〜900, y=350固定
- クリック位置はCanvas内に自動調整されます
