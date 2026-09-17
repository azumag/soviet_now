# soren91 - 同志AI (DousiAI_US)

91人対戦型ソ連ゲーム ([sorengame91](https://unityroom.com/games/sorengame91)) の自動プレイヤーです。Unity WebGL に直接のゲーム状態ブリッジを持たないため、スクリーンショットから盤面・NEXT・HOLD・おじゃま等を推定して `strategy.mjs` が投下位置を決めます。

## 重要: 戦略の自動改善は廃止

Soren91 が試合後・一定試合数ごと・日次スケジュール等で LLM を呼び、`strategy.mjs` を自動生成・自動更新したり、改善PRを自動作成する機能は廃止しました。

- 旧互換の改善実行モジュール・PID/lock/watchdog・環境変数triggerも削除済みで、runtimeから自動改善を起動する経路はありません。
- 試合履歴、summary、strategy snapshot、スクリーンショットは引き続き保存し、明示的な手動レビューや再現評価に利用できます。
- 戦略変更は、原因仮説・評価・テストを伴う通常のレビュー済みリポジトリ変更として行います。
- runtime evidence の保持は自動改善の消費状態に依存せず、`cleanup_retention.mjs` が既定3日で age-based cleanup します。

## 起動時の枠の準備

ローカル共有ブラウザでは、Unity読込後にstageを構築し、親ページへ初期stateを渡してから枠のiframeを作ります。各枠の描画healthを最大10秒待ってから、設定で有効な全画面化・前面化を行います。remote CDPでは従来どおりゲームだけを描画し、VM側の枠は操作しません。

描画待ちがタイムアウトした場合は警告を出して起動を継続します。その場合は枠未完成の表示を防げません。また、タブ作成による自動前面化や配信側のwindow capture切替はこの待機の対象外です。配信映像でのちらつき解消は、正規コーナーで別途確認が必要です。

回帰テスト: `node --test tests/test_soren91_startup_rails.mjs`。実HTMLの描画テストは `SOREN91_TEST_BROWSER=/path/to/chrome node --test tests/test_soren91_startup_rails_browser.mjs`（独立headless、外部通信なし）。

## セットアップ

```bash
cd soren91
npm install
npx playwright install chromium
node main.mjs
```

## 主な構成

```text
main.mjs                 # ブラウザ制御 + ゲームループ
screenshot_analyzer.mjs  # スクリーンショット → 盤面状態
calibration.mjs          # ゲームボード検出 + 座標変換
observation_guard.mjs    # 観測の安定性・confidence gate
strategy.mjs             # ドロップ/HOLD判断
strategy_contract.mjs    # 戦略の共通契約
comment.mjs              # 試合中/結果コメント
result_screen_ocr.mjs    # ランキング画面解析
critical_turn_screenshots.mjs # 重要局面の証拠選別
cleanup_retention.mjs    # 証拠データの保持期間管理

game_history/            # ラウンドごとのJSONL
tmp/summaries/           # ラウンドsummary
tmp/game_screenshots/    # 保存スクリーンショット
tmp/strategy_snapshots/  # 試合時点のstrategy snapshot
```

## ゲームフロー

```text
ゲーム起動
  → マッチング
  → スクリーンショット解析
  → strategy.mjs で判断
  → 投下/HOLD
  → 結果取得
  → 履歴・summary・必要な画像を保存
  → 次ラウンド
```

試合終了後に戦略を書き換える処理はありません。

## 手動分析・評価

保存された evidence は、明示的な改善作業で以下に利用できます。

- 認識失敗の目視確認
- 過去試合replay / fixture化
- 戦略変更前後のオフライン比較
- 回帰テスト追加
- 手動での hall-of-fame 保存

```bash
node hall_of_fame.mjs --note "strong run"
node lineage.mjs rebuild
node backfill_result_ranks.mjs
```

## runtime_config.json

`runtime_config.json` はコメント生成などのテキストAI設定を保持します。自動戦略改善の間隔設定はありません。

## 投下速度と途中コメント

投下速度の計測・撮影方式は [LATENCY.md](LATENCY.md) を参照してください。
2〜3秒に1回の投下を目標に余分な待機を減らしますが、認識不明・古い画像・座標変更時の入力抑止は維持します。
モック時計での速度は実戦速度ではありません。配備後に `tmp/state/soren91_loop_metrics.json` の送信間隔とゲーム側の入力受理を別々に確認します。

途中コメントは1試合1回、盤面に3ピース以上あり、20手到達または45秒経過かつ5手以上で非同期生成します。
開始時刻は最初の操作可能な盤面から計り、マッチング時間を含めません。
`Midgame gate` → `Midgame completed` → 既存の `queued:` と音声workerの再生記録で発火・生成・再生を切り分けます。
生成モデルやコメント返しの経路は変更しません。

## ホットリロード

`strategy.mjs`、`screenshot_analyzer.mjs`、`calibration.mjs` 等は実行時に再読込されます。ただし、それらをSoren91自身が自動編集することはありません。

## ゲーム座標系

- Board X: `[-3.5, +3.5]`（実投下可能範囲はピース半径で狭まる）
- Board Y: `-5.0` floor 〜 `+3.32` deadline
- 15種のピース。同type接触で上位typeへ併合

## 依存関係

- Playwright — ブラウザ操作
- Sharp — 画像解析
- dotenv — runtime設定
- テキストAI provider — コメント等。戦略の自動変更には使用しません
