# Soren Game Project

Soviet/Soren パズルゲーム（ソ連共和国）の AI 自動プレイプロジェクト。

## Unity WebGL ビルド

**ビルドガイド**: `sorengame/BUILD_GUIDE.md` を参照。

ビルドのポイント:
- プロジェクトソース: `sorengame/_extracted/soren-game-fixed/`
- NAS 上ではビルド不可（`._*` ファイル問題）。必ず `/tmp/soren-unity` にコピーして `dot_clean` 後にビルド
- アセットは **2つの ZIP** から補完が必要（テクスチャ GUID 問題、TMP シェーダー問題あり）
- 日本語ファイル名の展開は `ditto` を使うこと（`unzip` は文字化けする）
- commit 前に `dot_clean ./` を実行して `._*` ファイルを除去

## AI ループ (3種類)

### soren_loop.sh → eloop.sh — Self-Improving Strategy Loop (推奨)
- `./soren_loop.sh` で起動（親スクリプト）
- `strategy.py` が1試合を自律プレイ、試合後に AI が `strategy.py` をバックグラウンド改善するアダプティブループ
- `soren_loop.sh`: 親スクリプト (メインループ、初期化、クリーンアップ)。安定層で AI 書き換え対象外
- `eloop.sh`: 1試合の関数群 (play_one_game, post_game_bookkeeping 等)。毎試合 source で読み込み、AI 書き換え可
- `eloop_lib.sh`: 共通ライブラリ (ヘルパー/ラジオ/AI実行/バリデーション等)
- `eloop_improve.sh`: バックグラウンド改善サブプロセス (AI呼び出し/バリデーション/ラジオ生成)
- `strategy_runner.py`: 内側ループ (game_state.json → analyze_board.py → strategy.decide() → commands.txt)
- `strategy_versions/` にバージョン履歴、`game_history/` にターンログ (JSONL)
- `best_score.txt` でハイスコア管理
- アダプティブ改善: 1試合ごとに改善開始、改善中なら履歴を蓄積、完了後に統合して次の改善へ
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
| `soren_loop.sh` | 親スクリプト: メインループ、初期化 (AI書き換え対象外) |
| `eloop.sh` | 1試合の関数群 (毎試合source、AI書き換え可) |
| `eloop_lib.sh` | 共通ライブラリ (ヘルパー/ラジオ/AI実行/バリデーション) |
| `eloop_improve.sh` | バックグラウンド改善サブプロセス |
| `analyze_board.py` | 盤面解析 (併合判定・着地予測・反応器状態) |
| `twitch_clip.sh` | Twitchクリップ自動作成 + チャット投稿 |
| `twitch_chat.sh` | Twitch IRC チャットデーモン管理 (start/fetch/send等) |
| `twitch_chat_daemon.sh` | IRC常駐プロセス (`!clip` コマンド対応) |
| ~~`STRATEGY.md`~~ | 廃止（jloop用、git履歴に残存） |

## Twitch クリップ自動作成

ハイスコア・ロシア建国・ソ連建国時に自動でTwitchクリップを作成し、URLをチャットに投稿する。
チャットで `!clip` と打つと視聴者もクリップ作成可能（30秒クールダウン付き）。

**現在デフォルト無効**。有効化するには `.env` に以下を追加:

```bash
TWITCH_CLIP_ENABLED=1
TWITCH_CLIENT_ID=<Developer ConsoleのClient ID>
TWITCH_BROADCASTER_ID=<Helix /users APIで取得>
```

- OAuthトークン (`TWITCH_BOT_TOKEN`) に `clips:edit` スコープが必要
- ハイスコア・建国クリップ: `.env` 変更後、次のゲームから自動反映
- `!clip` コマンド: デーモン再起動が必要 (`./twitch_chat.sh stop && ./twitch_chat.sh start`)
- 同一ゲーム内で複数イベント発火時はデデュプ（最初の1クリップのみ）
- 配信オフライン時やAPI失敗時はサイレントにスキップ


# 実装計画立案時のルール
- ユーザーに計画を提示する前に、 codex コマンドで計画のレビュー>を行うこと
- 本質的でない指摘は無視しても良い
```
# initial plan review
codex exec "please review: {plan_full_path}"

# updated plan review
codex exec resule --last "plan updated: {plan_full_path}"
```

### Codex review
```
codex exec "PROMPT"
```
上記によってレビューを依頼できる
