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

## 投下間隔の再計測 (2026-09-28)

- 本番VMの `soren91_loop_metrics.json` dropProfile 実測: 投下間 11〜16秒のターンは観測4〜6回、理由は `board-moving`/`preview-changed` が支配的。投下間 3.7秒のターンは観測1回で `stable-slow-advance` が発火していた。
- 原因: `SOREN91_SINGLE_FRAME_ADVANCE_MS` 既定 2500ms に対しリモート撮影の観測間隔が約2.4秒で、**常に閾値の直下**になり高速パスがほぼ発火しない。厳格な2フレーム安定判定へ毎回落ち、撮影1枚約2.2秒×4〜6回が支配していた。
- 変更: 既定を 1200ms（ゲーム側の最小投下間隔 `DROP_COOLDOWN_MS` と同値、env上書き可）へ。高速パスの成立条件（キュー前進の証拠）は不変で、時間下限だけを実測cadenceに合わせる。判定ログへ `trans=` と `gapMs=` を追加。
- 配備後実測（session 0912fbe7、10区間）: 投下間隔 p50 11.5→6.6秒、観測 4→2回、capture mean_calls 2.4。まだ2観測のターンが残るため、高速パスの前進証拠を「**矛盾なし・証拠1以上**」まで許容（`SOREN91_ADVANCE_SINGLE_EVIDENCE=0` で無効化）。キュー安定化は従来の厳格分類のままで、時間的NEXT補完は変えない（readinessのみ緩和）。

## 配備前の確認

- 本番VMの `soren91/main.mjs` には 2026-09-17 時点で手動パッチ (CDP captureScreenshot) が入っている。PR #371 をマージして配備する場合、VMパッチとの差分を確認してから置換する。無確認上書きはしない。
- `SOREN91_TEST_BROWSER` を明示した時だけ実Chromiumテストが動く (通常CIはskip)。
