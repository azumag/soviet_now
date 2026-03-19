# soren91 - 同志AI (DousiAI_US)

91人対戦型ソ連ゲーム ([sorengame91](https://unityroom.com/games/sorengame91)) の自動プレイヤー。
スクリーンショットベースの盤面解析 + AI自動改善ループで戦略を進化させる。

親プロジェクト `soren/` のローカル版と同じゲーム基盤だが、リモートホスト版のため JS ブリッジが使えず、スクリーンショットベースで盤面を解析する。

## セットアップ

```bash
cd soren91
npm install          # 初回のみ
npx playwright install chromium  # 初回のみ
node main.mjs        # ゲーム起動 → 自動プレイ → 12ゲームごとにAI改善
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
tmp/screenshots/         # ゲーム中スクリーンショット (ラウンド後削除)
tmp/summaries/           # ラウンドサマリーJSON
```

## ゲームフロー

```
[起動] headlessでトップページ → ゲームURL取得 → 閉じる
  ↓
[表示] ゲーム画面のみ非headlessで表示 (広告なし)
  ↓
[タイトル] 名前入力 (DousiAI_US) → PLAY
  ↓
[ラウンドループ]
  Matching待ち → ゲームプレイ → ランキング
  → 履歴保存 (game_NNNN.jsonl)
  → 12ゲームごとにAI改善 (claude -p --model sonnet で strategy.mjs 更新)
  → 次ラウンドへ (自動)
```

## AI自動改善

12ゲームごとに `claude -p --model sonnet` で戦略を改善する:

1. ゲーム履歴からテキストサマリー (ターン数、ドロップ分布、理由分布) を生成
2. 現在の `strategy.mjs` + サマリーを Claude に送信
3. 返ってきた新コードをバリデーション (構文チェック + スモークテスト)
4. パスしたら適用、旧版を `strategy_versions/` にバックアップ
5. 排他ロック: 前の改善中は次をスキップ

## ホットリロード

全モジュールが実行時に動的importされるため、ファイル編集が即反映される (再起動不要):

- `strategy.mjs` / `screenshot_analyzer.mjs` / `calibration.mjs` -- 毎ターン
- `improve.mjs` -- 12ゲームごと

## 盤面解析

- **状態検出**: 中央列(35-65%)の暗さ比率で MOVE/WAITING 判定
- **壁検出**: 水平スキャンで明→暗遷移、暗領域150px以上でゲームボード壁と判定
- **ピース検出**: gridStep=4 の blob 検出、背景/低彩度を除外
- **おじゃま測定**: 灰色ブロックの割合と高さを `boardState.garbage` で提供

## ゲーム座標系

- Board X: [-3.5 wall, -3.0 drop min ... +3.0 drop max, +3.5 wall]
- Board Y: -5.0 (floor) to +3.32 (deadline)
- 15種のピース (type 1-15)、同type接触で上位typeに併合
- おじゃまブロック: 相手から送られる灰色ブロック、併合で消える

## 技術的制約

- Unity WebGL に JS ブリッジなし → スクリーンショット解析必須
- 名前入力: `keyboard.press()` のみ (insertText不可、日本語不可、12文字制限)
- ゲームURL: 署名付き、有効期限あり
- ドロップ間隔: ゲーム側クールダウン ≈ 1秒、bot側 1.2秒

## 依存関係

- [Playwright](https://playwright.dev/) -- ブラウザ自動操作
- [Sharp](https://sharp.pixelplumbing.com/) -- スクリーンショット画像解析
- [Claude CLI](https://docs.anthropic.com/) -- AI戦略改善
- [dotenv](https://github.com/motdotla/dotenv) -- 環境変数管理
