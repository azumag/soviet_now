# ソ連ゲーム AI プレイガイド

## 情報ソースの優先順位
1. **`game_state.json`** — 最も正確。盤面の全ピース位置・種類・危険度がテキストデータで得られる
2. **`soviet_now.png`** — 全体俯瞰の補助画像

## game_state.json の読み方
```json
{
  "gameOver": false,          // ゲームオーバー検出
  "cursor": {                 // 現在持っているピース（null=なし）
    "x": 0.5,                // ゲーム座標X
    "type": { "level": 3, "name": "latvia" },
    "scale": 1.55
  },
  "next": {                   // 次のピース（null=なし）
    "type": { "level": 1, "name": "armenia" },
    "scale": 1.0
  },
  "board": {
    "dangerLevel": "low",     // "low"/"medium"/"high"/"critical"
    "pieceCount": 5,
    "columnHeights": [0,19,21,0,0,4,5],  // 各カラムの高さ%（7列）
    "balance": -20,           // 左右バランス（負=左寄り、正=右寄り）
    "pieces": [...],          // 全ピースの詳細（位置・国名・level）
    "mergePairs": [...]       // マージ可能ペア（同国旗＋近距離）
  }
}
```

## プレイエリア
- 画面最上部の赤い線はデッドラインである

## NEXT
- `game_state.json` の `cursor` が現在持っているピース、`next` が次のピース
- 画面上では、デッドラインの上にある国旗が現在持っているピース
- NEXT と書いてある下にある国旗は、NEXTのNEXT（2手先）である

## ゲームルールと戦略
- 同じ国旗ブロックを重ねると国旗はマージされ、国旗レベルがあがり次のレベルの国旗に変化します
- 積み上がっているブロックの一番上だけマージ可能です
- `gameOver: true` → コマンドファイルに `retry` を書き込め
- 画面がいっぱいになる前に、ロシアを二つくっつけて、ソ連ができれば完成です
- `board.mergePairs` を確認し、同じ国旗が近くにあるならそこに同じ国旗を落としてマージを狙え
- `cursor.type.name` と `board.pieces` の国名を比較し、同じ国旗の近くに落とすのが基本戦略
- 先の先（`next` の国旗も含めて）を考えて落とす場所を決めよ

### 国旗レベル（低→高）— game_state.json での名前
| Level | 日本語 | game_state名 | 落下 |
|-------|--------|-------------|------|
| 1 | アルメニア | armenia | Yes |
| 2 | エストニア | estonia | Yes |
| 3 | ラトビア | latvia | Yes |
| 4 | リトアニア | lithuania | Yes |
| 5 | グルジア | georgia | Yes |
| 6 | アゼルバイジャン | azerbaijan | Yes |
| 7 | タジク | tajik | Yes |
| 8 | キルギス | kyrgyz | Yes |
| 9 | ベラルーシ | belarus | Yes |
| 10 | ウズベク | uzbek | Yes |
| 11 | トルクメン | turkmen | Yes |
| 12 | ウクライナ | ukraine | Merge |
| 13 | カザフ | kazakh | Merge |
| 14 | ロシア | russia | Merge |
| 15 | **ソ連** | **ussr** | **目標！** |

- 「落下」=Yes: ランダムに落ちてくる、Merge: マージでのみ出現
- 国旗は回転した状態で盤面に存在することがある
- 形は国の国土を表している

### 座標系
- Canvasサイズ: 1280 x 720
- 有効範囲: x=400〜900, y=340

## AIの役割

1. スクリプトを使い、ソ連ゲームを操作：README.md 参照
2. 画面上の国旗の配置を分析
3. 同じ種類の国旗を重ねる位置を計算
4. 次の一手の座標を決定してコマンド送信
