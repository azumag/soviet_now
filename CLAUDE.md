# Soren Game Project

Soviet/Soren パズルゲーム（ソ連共和国旗）の AI 自動プレイプロジェクト。

## Unity WebGL ビルド

**ビルドガイド**: `sorengame/BUILD_GUIDE.md` を参照。

ビルドのポイント:
- プロジェクトソース: `sorengame/_extracted/soren-game-fixed/`
- NAS 上ではビルド不可（`._*` ファイル問題）。必ず `/tmp/soren-unity` にコピーして `dot_clean` 後にビルド
- アセットは **2つの ZIP** から補完が必要（テクスチャ GUID 問題、TMP シェーダー問題あり）
- 日本語ファイル名の展開は `ditto` を使うこと（`unzip` は文字化けする）
- commit 前に `dot_clean ./` を実行して `._*` ファイルを除去

## AI ループ (3種類)

### eloop.sh — Self-Improving Strategy Loop (推奨)
- `strategy.py` が1試合を自律プレイ、試合後に AI が `strategy.py` を改善するメタ学習ループ
- `strategy_runner.py`: 内側ループ (game_state.json → analyze_board.py → strategy.decide() → commands.txt)
- `strategy_versions/` にバージョン履歴、`game_history/` にターンログ (JSONL)
- `best_score.txt` でハイスコア管理
- AI改善後にバリデーション (decide() 存在・シグネチャ・テスト実行)、失敗時は自動復元

### jloop.sh — JSON-based State Loop
- 毎ターン AI (LLM) を呼び出して盤面判断→ドロップ
- `analyze_board.py` で盤面解析 → AI プロンプト → `tmp/plan.md` / `tmp/plan.json`
- 思考ログ: `think.md`

### sloop.sh — Simple State Loop (レガシー)
- 画像認識ベースの OBSERVE → DECIDE → EXECUTE ループ

## ゲーム操作

- `soviet_local.mjs` - ローカルビルドで AI プレイ（Playwright + JS Bridge）
- `soviet_game.mjs` - unityroom.com オンライン版で AI プレイ
- `commands.txt` に書き込んでドロップ指示、`game_state.json` から盤面読み取り

## 主要ファイル

| ファイル | 役割 |
|---------|------|
| `strategy.py` | AI改善対象の決定関数 `decide(game_state, analysis) -> {x, reason}` |
| `strategy_runner.py` | eloop内側ループ: 1試合自律プレイ + JSONL履歴記録 |
| `eloop.sh` | eloop外側ループ: 試合→AI改善→バリデーション→リトライ |
| `analyze_board.py` | 盤面解析 (マージ判定・着地予測・反応器状態) |
| ~~`STRATEGY.md`~~ | 廃止（jloop用、git履歴に残存） |

### Codex review
```
codex exec "PROMPT"
```
上記によってレビューを依頼できる
