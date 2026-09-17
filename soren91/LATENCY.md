# Soren91 latency notes

## 背景

- リモートCDP越しの `locator.screenshot()` / `boundingBox()` は actionability チェックの往復で1枚2〜6秒かかり、ドロップ間隔が数十秒に伸びていた。local実測では canvas 取得を `Page.captureScreenshot` / `page.screenshot({clip})` へ置き換えて 6509ms → 263ms (PR #371 の前段パッチ)。
- PR #371 はさらに (1) クールダウン中の観測重ね合わせ、(2) 順位確認バーストを実時間バジェット化、(3) HOLD後の余分な1.2秒撤去、(4) 毎ターンの再import削減、(5) `tmp/state/soren91_loop_metrics.json` への内訳記録を行う。
- モック時計のテスト値 (1.4秒等) は回帰検査であって実戦速度ではない。ゲーム側の入力受理はクリック送信と別に観測する。

## 途中コメントのゲート (2026-09-17 追加)

- `soren91/commentary_schedule.mjs`: 1試合1回。`turn >= 20` または `elapsed >= 45s && turn >= 5`。pieces < 3 (マッチング誤検出) は除外。
- ラウンド開始時刻は最初の操作可能盤面で記録 (`roundStartedAt`)。マッチング・待機時間はカウントしない。
- ゲートごとに `[game] Midgame gate: ...` を、生成完了に `[game] Midgame completed: ... generated=` を出力。生成は非同期で投下を待たせない。
- 過去の無言試合: game_0007 が 12手で終了し、旧条件 `turn >= 20` に一度も到達していなかったのが直接原因。game_0006〜0008 の median 間隔は 15〜24秒。

## 配備前の確認

- 本番VMの `soren91/main.mjs` には 2026-09-17 時点で手動パッチ (CDP captureScreenshot) が入っている。PR #371 をマージして配備する場合、VMパッチとの差分を確認してから置換する。無確認上書きはしない。
- `SOREN91_TEST_BROWSER` を明示した時だけ実Chromiumテストが動く (通常CIはskip)。
