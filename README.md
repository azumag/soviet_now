# Soren Game AI

Soviet/Soren パズルゲーム（スイカゲーム風・ソ連共和国旗）の AI 自動プレイプロジェクト。

同typeのピース2個が接触すると合体進化する (`type_N + type_N → type_{N+1}`)。
プレイヤーはドロップX座標のみ指定可能。デッドライン超えでゲームオーバー。

## アーキテクチャ

```
soviet_local.mjs          ← ゲーム実行環境 (HTTP server + Playwright + Unity WebGL)
    ↕ commands.txt / game_state.json
AI ループ (3種類から選択)
    ├── eloop.sh           ← 自己改善ループ (推奨)
    ├── jloop.sh           ← JSON構造データ版ループ
    └── sloop.sh           ← 画像認識版ループ (レガシー)
```

## AI ループ

### eloop.sh — Self-Improving Strategy Loop (推奨)

Python スクリプト (`strategy.py`) が1試合を自律プレイし、試合終了後に AI がスクリプトを改善する「メタ学習ループ」。

```bash
node soviet_local.mjs &    # ゲーム起動
bash eloop.sh              # AI ループ開始
```

**フロー:**
```
eloop.sh (外側ループ)
  ├── strategy_runner.py    → 1試合を自律プレイ
  │     ├── game_state.json を読む
  │     ├── analyze_board.py で盤面解析
  │     ├── strategy.py の decide() でドロップX決定
  │     ├── commands.txt に書き込み
  │     └── game_history/latest.jsonl にターンログ記録
  ├── GAMEOVER 検知 → スコア取得
  ├── バージョン保存 (strategy_versions/vNNN_scoreXXX_strategy.py)
  ├── AI 呼び出し (prompts/improve_strategy.md)
  │     → strategy.py を解析・改善・上書き
  ├── バリデーション (decide() 存在・シグネチャ・テスト実行)
  │     → 失敗時は前バージョンに自動復元
  └── retry → 次の試合へ
```

**主要ファイル:**

| ファイル | 役割 |
|---------|------|
| `strategy.py` | AI が改善する決定関数。`decide(game_state, analysis) -> {x, reason}` |
| `strategy_runner.py` | 内側ループ。strategy.py で1試合プレイ + JSONL履歴記録 |
| `eloop.sh` | 外側ループ。試合実行→AI改善→バリデーション→リトライ |
| `analyze_board.py` | 盤面解析。マージ判定・着地予測・期待値計算 |
| `prompts/improve_strategy.md` | AI改善用プロンプト |
| `strategy_versions/` | strategy.py のバージョン履歴 |
| `game_history/` | 試合ごとのターンログ (JSONL) |
| `best_strategy.py` | 最高スコア時の strategy.py |
| `best_score.txt` | ハイスコア記録 |

### jloop.sh — JSON-based State Loop

毎ターン AI (LLM) を呼び出して盤面判断→ドロップを行う。`analyze_board.py` の解析レポートを入力とする。

```bash
node soviet_local.mjs &
bash jloop.sh
```

**ステートマシン:** `WAIT_READY → DECIDE → EXECUTE → WAIT_READY`
- DECIDE: `analyze_board.py` で盤面解析 → AI にプロンプト → `tmp/plan.md` / `tmp/plan.json` 出力
- EXECUTE: plan からドロップ座標抽出 → `commands.txt` 書き込み
- GAME_OVER: AI に振り返り → `STRATEGY.md` 更新 → retry

### sloop.sh — Simple State Loop (レガシー)

画像認識ベース。`OBSERVE → DECIDE → EXECUTE` の3段構成。スクリーンショットから盤面を読み取る。

```bash
node soviet_local.mjs &
bash sloop.sh
```

## ゲーム実行環境

### ローカルビルド (推奨)

```bash
node soviet_local.mjs     # localhost:8080 で Unity WebGL をサーブ + Playwright 操作
```

- `sorengame/build/` の Unity WebGL ビルドを HTTP サーブ
- `.gz` ファイルを `Content-Encoding: gzip` 付きで配信
- `commands.txt` をポーリングしてゲーム操作
- `game_state.json` に盤面データを自動書き出し

### オンライン版

```bash
node soviet_game.mjs      # unityroom.com 版を Playwright で操作
```

## コマンドインターフェース

`commands.txt` に書き込むとゲーム操作が実行される。

```bash
# キャンバス座標でドロップ (x: 410-830, y: 350固定)
echo "620,350" > commands.txt

# ゲームオーバー時にリトライ
echo "retry" > commands.txt

# JSON形式
echo '[{"action":"cmd","value":"FIXUI"}]' > commands.txt
```

### game_state.json

JS Bridge (`SorenBridge.cs` + `SorenBridge.jslib`) 経由で Unity から読み出した構造データ。

```json
{
  "state": "MOVE",
  "score": 1572,
  "next": {"type": 5, "r": 0.477},
  "nextNext": {"type": 9, "r": 1.068},
  "pieces": [{"id": 2, "type": 8, "x": -2.369, "y": -3.739, "r": 0.846, ...}],
  "shapes": {"8": [[x,y], ...]}
}
```

- `state`: MOVE / DROP / GAMEOVER / STOP
- `type`: 1-16 (共和国番号、1=最小、16=ソ連)
- `pieces`: 全ピースの位置・半径・速度・回転
- `shapes`: ピース種別ごとの凸ポリゴン頂点

## Unity WebGL ビルド

詳細は `sorengame/BUILD_GUIDE.md` を参照。

- Unity 2022.3.62f3 LTS
- NAS 上ではビルド不可。`/tmp/soren-unity` にコピーして `dot_clean` 後にビルド
- アセット補完: 2つの ZIP からテクスチャ + TMP フォルダ
- commit 前に `dot_clean ./` を実行

## 戦略

- `STRATEGY.md` — 現在の戦略方針 (AI が自動更新)
- `best_strategy.md` — ハイスコア時の戦略
- 人工化学フレームワーク: ゲームを空間的反応器 (S=ピース種, R=A+A→B, A=物理エンジン) として解析
