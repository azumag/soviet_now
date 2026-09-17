# 同志AI (DousiAI_US) / Soren91

## プロジェクト概要
unityroom.com の91人対戦型ソ連ゲーム自動プレイヤー。スクリーンショットベースで盤面を解析し、`strategy.mjs` が投下/HOLDを判断する。

## 自動戦略改善は禁止

Soren91 自身が LLM を呼び、試合後・一定試合数ごと・日次スケジュール等で `strategy.mjs` を生成/更新したり、改善PRを自動作成する仕組みは廃止済み。

今後の戦略変更は、ユーザーから明示的な改善依頼がある場合にのみ、保存済みの試合履歴・summary・strategy snapshot・スクリーンショットを確認し、通常のブランチ/PRレビュー経路で行うこと。

禁止事項:
- 自動改善cron / timer / daemon の再導入
- 試合終了をtriggerにした戦略書換え
- `strategy.mjs` のruntime自己更新
- retained evidence からの無人candidate/PR生成
- 環境変数で旧自動改善を再有効化する経路の追加

旧互換の改善実行モジュール・PID/lock/watchdog・環境変数triggerも残さない。Soren91 runtimeに自動改善を起動する実行モジュールを再導入してはならない。

## 実行
```bash
cd soren91
npm install
node main.mjs
```

## 主な構成
```text
main.mjs                 # ブラウザ制御 + ゲームループ
screenshot_analyzer.mjs  # スクリーンショット → 盤面状態
calibration.mjs          # ボード検出 + 座標変換
observation_guard.mjs    # 観測安定性/confidence gate
strategy.mjs             # 投下/HOLD判断
strategy_contract.mjs    # 戦略契約
critical_turn_screenshots.mjs # 重要局面の証拠選別
daily_evidence.mjs       # 保存履歴の解析helper。自動triggerではない
cleanup_retention.mjs    # evidenceのage-based retention

game_history/            # JSONL試合履歴
tmp/summaries/           # summary
tmp/game_screenshots/    # 保存画像
tmp/strategy_snapshots/  # 試合時点の戦略
```

## 改善を明示的に依頼された場合
1. current main / open Issue / PR /直近変更を確認する。
2. 実戦evidenceと画像を確認して原因仮説を立てる。
3. 最小〜中規模の変更を行う。
4. unit/replay/fixtureで回帰を確認する。
5. protected mainへ直pushせず、必要ならPRにする。
6. 自動でproductionへ適用しない。

## 証拠保持
自動改善の消費ledgerは使わない。`cleanup_retention.mjs` が既定3日を超えた管理対象evidenceのみ削除し、`strategy.mjs`、`strategy_versions/`、`tmp/state/` 等には触れない。
