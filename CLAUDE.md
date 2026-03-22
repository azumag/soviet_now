# Soren Game Project

Soviet/Soren パズルゲーム（ソ連共和国）の AI 自動プレイプロジェクト。

## サブプロジェクト

- `soren91/` — 91人対戦版 (unityroom) の自動プレイヤー。独自の CLAUDE.md を持つ。スクリーンショットベース・Node.js 実装。以下の親プロジェクト固有セクション（eloop, ラジオ, TTS, Twitch等）は soren91 には該当しない。

---
## 以下は親プロジェクト (ローカル版ソ連ゲーム) 固有の情報

## Unity WebGL ビルド

**ビルドガイド**: `sorengame/BUILD_GUIDE.md` を参照。

ビルドのポイント:
- プロジェクトソース: `sorengame/_extracted/soren-game-fixed/`
- NAS 上ではビルド不可（`._*` ファイル問題）。必ず `/tmp/soren-unity` にコピーして `dot_clean` 後にビルド
- アセットは **2つの ZIP** から補完が必要（テクスチャ GUID 問題、TMP シェーダー問題あり）
- 日本語ファイル名の展開は `ditto` を使うこと（`unzip` は文字化けする）
- commit 前に `dot_clean ./` を実行して `._*` ファイルを除去

## AI ループ (eloop)

### soren_loop.sh → eloop.sh — Self-Improving Strategy Loop
- `./soren_loop.sh` で起動（親スクリプト）
- `strategy.py` が1試合を自律プレイ、試合後に AI が `strategy.py` をバックグラウンド改善するアダプティブループ
- `soren_loop.sh`: 親スクリプト (メインループ、初期化、クリーンアップ)。安定層で AI 書き換え対象外
- `eloop.sh`: 1試合の関数群 (play_one_game, post_game_bookkeeping 等)。毎試合 source で読み込み、AI 書き換え可
- `eloop_lib.sh`: 全モジュールをsourceするshim (~40行)
- `eloop_improve.sh`: バックグラウンド改善サブプロセス (AI呼び出し/バリデーション/ラジオ生成)
- モジュール構成:
  - `core/`: config, helpers, game_state, version, phyrogenetic
  - `strategy/`: ai, sandbox, regression, improve
  - `broadcast/`: radio_engine/persona/themes/news/factcheck/corners/state/celebration, comment, comment_worker, scheduler
  - `infra/`: cleanup
- `strategy_runner.py`: 内側ループ (game_state.json → analyze_board.py → strategy.decide() → commands.txt)
- `strategy_versions/` にバージョン履歴、`game_history/` にターンログ (JSONL)
- `best_score.txt` でハイスコア管理
- アダプティブ改善: 1試合ごとに改善開始、改善中なら履歴を蓄積、完了後に統合して次の改善へ
- AI改善後にバリデーション (decide() 存在・シグネチャ・テスト実行)、失敗時は自動復元

### 建国ボーナス指標
- 戦略評価（rolling_scores, regression, 改善AI向け蓄積）には「建国ボーナス込みスコア（EVAL_SCORE）」を使用
- ゲームオーバー時の最終盤面のピースtype別ボーナスを加算: type 1-5: 0,0,1,1,2 / type 6-10: 3,4,6,10,16 / type 11-15: 26,40,70,120,240 / ソ連建国: +800
- 表示用スコア（best_score.txt, commit, ダッシュボード）は raw スコアのまま
- `strategy_runner.py` が `final_types` を出力 → `eloop.sh` の `post_game_bookkeeping()` でボーナス計算

### 粛清（regression rollback）基準
- anchor戦略（過去の安定戦略）と現戦略の composite/p50/p25 を比較
- Hard fail（即時粛清 — branch depth/patience超過時）: comp gap≥330, p50 gap≥270, p25 gap≥390 のうち2つ以上で発動
- Soft fail（通常の回帰検出）: comp gap≥180, p50 gap≥150, p25 gap≥270 のうち2つ以上で発動
- composite = 0.55×p50 + 0.30×p25 + 0.15×lcb
- Branch制御: max_depth=4, max_games=48, patience=3

## ゲーム操作

- `soviet_local.mjs` でローカルビルドの AI プレイ（Playwright + JS Bridge）
- `commands.txt` に書き込んでドロップ指示、`game_state.json` から盤面読み取り

## 主要ファイル

### コアループ

| ファイル | 役割 |
|---------|------|
| `strategy.py` | AI改善対象の決定関数 `decide(game_state, analysis) -> {x, reason}` |
| `strategy_runner.py` | eloop内側ループ: 1試合自律プレイ + JSONL履歴記録 |
| `soren_loop.sh` | 親スクリプト: メインループ、初期化 (AI書き換え対象外) |
| `eloop.sh` | 1試合の関数群 (毎試合source、AI書き換え可) |
| `eloop_lib.sh` | 全モジュールsource shim (~40行) |
| `eloop_improve.sh` | バックグラウンド改善サブプロセス |
| `stop_soren.sh` | soren_loop.sh 安全停止 (tmp/stop + SIGINT) |
| `analyze_board.py` | 盤面解析 (併合判定・着地予測・反応器状態) |
| `soviet_local.mjs` | ローカルビルドで AI プレイ（Playwright + JS Bridge） |
| `soren91_control.sh` | soren91 (メリケンAI) の起動・停止・改善キック管理 |

### モジュール (shell)

| ファイル | 役割 |
|---------|------|
| `core/config.sh` | 全定数・パス定義・mkdir初期化 |
| `core/helpers.sh` | log, commands_empty, _trim_log_file 等 |
| `core/game_state.sh` | is_game_over, wait_for_move, send_retry |
| `core/version.sh` | save_strategy_version, update_best, archive_history |
| `core/phyrogenetic.sh` | 進化系統樹の記録・投稿 |
| `strategy/ai.sh` | spinner, build_prompt, run_cmd, run_ai |
| `strategy/sandbox.sh` | validate_strategy, sandbox管理 |
| `strategy/regression.sh` | rolling scores, check_regression, rollback候補選定, postmortem |
| `strategy/improve.sh` | improve_state管理, trigger_adaptive_improvement |
| `broadcast/radio_engine.sh` | ラジオ放送コア (コーナー実行、再生管理) |
| `broadcast/radio_persona.sh` | ラジオDJペルソナ設定 |
| `broadcast/radio_themes.sh` | 雑談テーマ選択・重複回避 |
| `broadcast/radio_news.sh` | ニュース取得・フィルタ・再生・AIスパム判定 |
| `broadcast/radio_factcheck.sh` | 生成テキストのファクトチェック・修正 |
| `broadcast/radio_corners.sh` | 各コーナー定義 (theme/news/soviet/strategy/recap/rules/jiji等) |
| `broadcast/radio_state.sh` | ラジオ状態管理 |
| `broadcast/radio_celebration.sh` | 建国祝賀トーク生成 |
| `broadcast/comment.sh` | コメント応答生成・コンテキスト構築・advice抽出 |
| `broadcast/comment_worker.sh` | player/watcherデーモン管理 |
| `broadcast/scheduler.sh` | 非同期ジョブスケジュール |
| `infra/cleanup.sh` | PID停止, cleanup_all, cleanup_tmp |

### TTS / 音声

| ファイル | 役割 |
|---------|------|
| `voicevox_tts.sh` | VOICEVOX TTS wrapper（ラジオ読み上げ本番用） |
| `voicevox_sing.sh` | VOICEVOX 歌声合成ラッパー |
| `coeiroink_tts.sh` | COEIROINK v2 TTS wrapper |
| `google_tts.sh` | Google Cloud TTS wrapper（gcloud認証、開発/テスト用） |
| `say_enqueue.sh` | mkdirロックベースのsayキュー（FIFO順次再生） |
| `enqueue_audio_trigger.sh` | 手動オーディオトリガーキュー (news/soviet/strategy/theme等) |
| `config/` | VOICEVOX設定 (除外ID, イントネーション, ピッチ, テンポ) |

### Twitch 連携

| ファイル | 役割 |
|---------|------|
| `twitch_clip.sh` | Twitchクリップ自動作成 + チャット投稿 |
| `twitch_chat.sh` | Twitch IRC チャットデーモン管理 (start/fetch/send等) |
| `twitch_chat_daemon.sh` | IRC常駐プロセス (`!clip` コマンド対応) |
| `twitch_predictions.sh` | Twitch チャネルポイント予想 API wrapper (create/resolve/cancel/autovote/cleanup, azumagdev自動投票) |

### ダッシュボード / ステータス

| ファイル | 役割 |
|---------|------|
| `generate_dashboard.sh` | score_history.txt → score_dashboard.html 生成 |
| `show_status.sh` | eloop 全体のCUIステータス表示 (watchモード) |
| `show_status_g.sh` | CUI グラフィカル統計ダッシュボード (watchモード) |
| `status_dashboard.py` | CUI統計ダッシュボード描画 (brailleスコアタイムライン等) |
| `score_history.txt` | スコア履歴 (TSV: timestamp, score) |
| `score_dashboard.html` | OBS等向けHTMLダッシュボード (generate_dashboard.sh で生成) |

### Python ユーティリティ

| ファイル | 役割 |
|---------|------|
| `lib/regression_calc.py` | 回帰/ランキング計算の共有ライブラリ |
| `lib/radio_parser.py` | ラジオトーク出力のパース (body/summary/selected-news) |
| `lib/radio_text_utils.py` | ラジオテキスト処理 (dedup/sanitize/normalize_tone) |
| `lib/news_filter.py` | ニュースフィルタリング (title_key/filter_unread/resolve_title) |
| `lib/fetch_news.py` | RSS/Webニュース取得 |
| `lib/fetch_market.py` | 為替レート取得 |
| `lib/fetch_google_headlines.py` | Google News RSSトップ見出し取得 |
| `fetch_news.sh` | ニュース取得shellラッパー |
| `fetch_market.sh` | 為替取得shellラッパー |
| `fetch_radio_grounding.py` | ラジオWebグラウンディング情報取得 |
| `generate_phyrogenetic_tree.py` | Mermaid形式の戦略系統樹生成 |
| `extract_decide_hash.py` | decide()関数のAST抽出→MD5ハッシュ |
| `analyze_patterns.py` | 振り子パターン検出・履歴データ分析 |
| `batch_summary.py` | 複数ゲーム履歴JSONLからコンパクトサマリー生成 |
| `tag_best_changelog.py` | strategy.py の最新バージョンに [BEST:スコア] タグ付与 |
| `trim_changelog.py` | strategy.py の変更履歴を直近N個にトリミング |
| `backfill_celebration_history.py` | 建国履歴の補完（バックフィル用） |
| `migrate_score_history.py` | score_history.txt へのタイムスタンプ付与（git logから復元） |
| `recover_hash_archive_from_git.py` | git履歴からstrategy_versions/by_hashエントリを復元 |
| `strategy_helpers/` | 戦略ヘルパーモジュール (2turn_lookahead.py 等) |

### データ / プロンプト

| ファイル | 役割 |
|---------|------|
| `data/radio_themes.txt` | ラジオ雑談テーマリスト（685件。料理・文化・神社・日本神話・オカルト等） |
| `data/radio_soviet_themes.txt` | ソ連関連テーマリスト（209件） |
| `data/songs/` | VOICEVOX歌声合成用の楽譜JSON |
| `data/voicevox_sing_reference.md` | VOICEVOX歌声合成リファレンス |
| `prompts/improve_strategy.md` | 戦略改善AIへのプロンプト（建国ボーナス指標の説明含む） |
| `prompts/rollback_postmortem.md` | 粛清後の事後分析AIプロンプト |
| `prompts/radio_*.md` | ラジオ各コーナー用プロンプト (persona/theme/news/soviet/strategy/recap/rules/jiji/jiji_research等) |
| `prompts/comment_response.md` | コメント応答生成プロンプト |
| `prompts/celebration.md` | 建国お祝い生成プロンプト |
| `prompts/game_theory.md` | ゲーム理論プロンプト |
| `prompts/fix_validation.md` | バリデーション修正プロンプト |
| `advice.md` | 視聴者からの戦略アドバイス蓄積ファイル |

## ラジオ放送

### スケジュール

12ゲーム1サイクル（`MIN_GAMES_BEFORE_IMPROVE`）でスケジューリング。`broadcast/scheduler.sh` が管理。

#### サイクルベース（改善サイクル内の蓄積ゲーム数）

| サイクル位置 | コーナー | 備考 |
|---|---|---|
| Game 2 | 雑談テーマ | 1/3でソ連テーマ。時刻コーナー発火時はスキップ |
| Game 5 | ニュース読み上げ | `fetch_and_play_news()` |
| Game 8 | 時事ニュース（jiji） | AI Web検索でトレンド紹介。改善タイミング付近はスキップ |

#### 時刻ベース（±15分ウィンドウ、1日1回のみ）

| 時刻 | コーナー | 内容 |
|---|---|---|
| 01:00 | rakugo | 深夜の落語創作 |
| 06:00 | health | 健康情報 |
| 07:00 | breakfast | 世界の朝食 |
| 08:00 | weather | ソ連天気予報 |
| 09:00 | wiki | きょうのWikipedia |
| 10:00 | sightseeing | おすすめ観光地 |
| 11:30 | lunch | 世界の昼食 |
| 12:00 | fortune | ソ連占い |
| 13:00 | devil_dict | 悪魔の辞典 |
| 14:00 | soviet_quiz | ソ連クイズ |
| 15:30 | market | 株価・経済 |
| 16:00 | bluegrass | ブルーグラス音楽紹介 |
| 17:00 | dinner | 今日の献立 |
| 17:30 | redefine | 概念の再定義 |
| 18:00 | soviet_lifehack | ソビエト式生活改善 |
| 19:00 | world_dinner | 世界の夕食 |
| 20:00 | whatday | 今日は何の日？ |
| 21:00 | deals | お得情報 |
| 21:30 | night_snack | 世界の夜食 |
| 22:00 | survival | サバイバル知識 |

#### 再生制御
- コメント優先: コメントキューがあるとラジオ再生は deferred queue に回し、コメント消化後に再生
- 手動トリガー: `tmp/.manual_audio_triggers/` に `{corner名}.cmd` を置くと任意コーナーを強制発火（1ループ最大3個）

### コンテンツ設定

- 雑談テーマ: `data/radio_themes.txt`（685件。料理・文化・神社・日本神話・神道・オカルト等）
- ソ連テーマ: `data/radio_soviet_themes.txt`
- ニュース読み上げ: 記事内容は素直に紹介するが、政治的に中立・多角的な意見を述べること（左右どちらにも偏らない）
- 履歴保持: テーマ400件（PAST_RADIO_THEME_HISTORY_KEEP）、ニュースURL 500件、ソース 400件で重複を回避
- ニュースAIスパムフィルタ: `_news_ai_spam_check` が `opencode run --agent=glm` でタイトル+本文冒頭を判定し、広告・PR記事を除外
- Webグラウンディング: `RADIO_WEB_GROUNDING_ENABLED=1` でテーマ関連の補足情報をWeb検索で取得（TTL 6時間）
- ファクトチェック: `RADIO_FACT_CHECK_ENABLED=1` で生成テキストのファクトチェック・修正を実施

## 配信画面構成

- 画面端の小窓: メリケンAI（アメリカ製AI）が「ソ連ゲーム91」（続編・対戦版）をプレイしている様子
- こちらが「中華AI×ソ連ゲーム」、向こうは「メリケンAI×ソ連ゲーム91」
- コメント応答でメリケンAIについて聞かれたら、ライバル意識を持ちつつも認め合う姿勢で答える

## Twitch チャネルポイント予想

- 12ゲームサイクルの開始時に「建国できる？」予想を作成
- 選択肢: 建国なし / ロシア建国(ソ連不成立) / ソ連建国 / 粛清
- ソ連建国・粛清は即 resolve、ロシア建国は12ゲーム後に判定
- azumagdev ボットが GQL API でランダムに1票自動投票。`TWITCH_BOT_GQL_TOKEN` 設定時は残高の10%を賭ける。未設定時は固定 `TWITCH_AUTO_VOTE_POINTS`（デフォルト10pt）
- `cleanup` サブコマンドで `PREDICTION_MAX_GAMES`（デフォルト12）超過等の古い予想状態を自動クリア
- `TWITCH_PREDICTIONS_ENABLED=1` と `TWITCH_PREDICTIONS_TOKEN` が必要（チャネルオーナートークン）

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
