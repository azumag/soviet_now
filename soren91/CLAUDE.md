# 同志AI (DousiAI_US)

## プロジェクト概要
unityroom.com の91人対戦型ソ連ゲーム自動プレイヤー。
スクリーンショットベースの盤面解析 + AI自動改善ループ。
https://unityroom.com/games/sorengame91

親プロジェクト `soren/` のローカル版と同じゲーム基盤だが、
リモートホスト版のため JS ブリッジが使えず、スクリーンショットベースで盤面解析。

## 実行方法
```bash
cd soren91
npm install          # 初回のみ
node main.mjs        # ゲーム起動 → 自動プレイ → ラウンド間にAI改善
```

## アーキテクチャ
```
main.mjs                 # エントリポイント: ブラウザ制御 + ゲームループ
screenshot_analyzer.mjs  # スクリーンショット → 盤面状態 (Sharp)
calibration.mjs          # ゲームボード壁検出 + 座標変換
strategy.mjs             # ドロップ位置決定 (AI改変対象)
improve.mjs              # ラウンド後AI改善ループ (claude CLI)
prompts/improve_strategy.md  # AI改善プロンプト

game_history/            # ラウンドごとのJSONLターンログ
strategy_versions/       # strategy.mjs のバックアップ
tmp/screenshots/         # ゲーム中スクリーンショット (サマリー後削除)
tmp/summaries/           # ラウンドサマリーJSON
```

## ゲームフロー
```
[起動] headlessでトップページ→ゲームURL取得→閉じる
  ↓
[表示] ゲーム画面のみ非headlessで表示 (広告なし)
  ↓
[タイトル] 名前入力 (DousiAI_US) → PLAY
  ↓
[ラウンドループ]
  Matching待ち → ゲームプレイ → ランキング
  → 履歴保存 (game_NNNN.jsonl)
  → AI改善 (claude -p --model sonnet でstrategy.mjs更新)
  → 次ラウンドへ (自動)
```

## AI改善ループ
- ラウンド終了時に `claude -p --model sonnet` を非同期呼び出し
- テキストサマリー (ターン数、ドロップ分布、理由分布) を送信
- 返ってきた新strategy.mjsをバリデーション (構文 + スモークテスト)
- パスしたら適用、旧版をstrategy_versions/にバックアップ
- 排他ロック: 前の改善中は次をスキップ

## ホットリロード
全モジュールが動的importされるため、ファイル編集が即反映される (再起動不要):
- strategy.mjs — 毎ターン
- screenshot_analyzer.mjs — 毎ターン
- calibration.mjs — 毎ターン
- improve.mjs — 毎ラウンド終了時

## 盤面解析
- **状態検出**: 中央列(35-65%)の暗さ比率でMOVE/WAITING判定。dark>10%ならMOVE
- **壁検出**: 水平スキャンで明→暗遷移、暗領域150px以上でゲームボード壁と判定
- **ピース検出**: gridStep=4のblob検出、背景(brightness<60)除外、低彩度(壁/灰色)除外
- **おじゃま測定**: 灰色(brightness100-200, saturation<0.1)の割合と高さを`boardState.garbage`で提供

## ゲーム座標系
- Board X: [-3.5 wall, -3.0 drop min ... +3.0 drop max, +3.5 wall]
- Board Y: -5.0 (floor) to +3.32 (deadline)
- 15種のピース (type 1-15)、同type接触で上位typeに併合
- おじゃまブロック: 相手から送られる灰色ブロック、併合で消える

## 技術的制約
- Unity WebGLにJSブリッジなし → スクリーンショット解析必須
- 名前入力: keyboard.press()のみ (insertText不可、日本語不可、12文字制限)
- ゲームURL: 署名付き、有効期限あり
- ドロップ間隔: ゲーム側クールダウン≈1秒、bot側1.2秒
