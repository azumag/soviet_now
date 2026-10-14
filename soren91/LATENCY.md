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

## captureタイムアウトによるbot停止 (2026-09-29)

- 現象: 手動Meriken枠で投下1〜2回の後に `capture-timeout` が連続し、11回目で「Too many consecutive errors, stopping」→ botが終了。コーナーは残り時間そのまま更新されない（視聴者には白/停止画面）。
- 実測: リモート撮影コストは p50 約1.9秒/枚・p95 4.6秒（#519配備後の dropProfile）。既定予算3秒では負荷時に超えやすい。リトライは1秒固定・上限10回だった。
- 変更: capture予算の既定を **3000→6000ms**（env `SOREN91_CAPTURE_TIMEOUT_MS` で最大9000）。連続エラーは**指数バックオフ**（1s→2s→…最大15s、`SOREN91_ERROR_BACKOFF_MAX_MS`）で再試行し、停止上限を **30回**（`SOREN91_ERROR_LIMIT`）へ。一時的な負荷でコーナーが死なないようにする。

## 状態ポーリング待ちの二重課金 (2026-10-15, #452)

- 実測: リモートでは1 captureが既に p50 約1.9秒 / p95 4.6秒かかるため、`POLL_INTERVAL_MS`=200ms を「capture後の待ち」として毎回フルで足す分は純増だった。dropProfile の `poll` 段は区間あたり約0.6秒（再観測3回分）を占め、投下間隔に対して無条件の追加遅延になっていた。
- 変更: `gameLoop` の各iteration先頭で `iterationStartedAt` を取り、MOVE以外の状態待ちは `max(0, POLL_INTERVAL_MS - 経過)` だけsleepする。`POLL_INTERVAL_MS` は **cadence floor**（iteration開始からの下限）として意味を保つため、captureが200ms未満の環境では従来どおり200ms待つ。cadenceが締まることはない。
- 不変: stability gate（`observation_guard` の2フレーム判定・`preview-changed`/`board-moving`/`stable-slow-advance` 分類）、geometry validation、input-before-confirmation、capture cadence、strategy、risk評価、provider/security boundary。gate判定に使う時間は capture の実経過時間のみで、poll sleep の長さは判定に影響しない。
- 回帰: `tests/test_soren91_round_lifecycle.mjs` に、capture 1300ms（インターバル超過）で余分な200msが乗らないこと、capture 50ms（インターバル未満）で200ms floor が維持されることを確認する2件を追加。投下回数・観測回数・round lifecycle は既存12テストと同一。
- 本番配備後の `dropSentIntervalMs` p50/p95 と `stageMs.poll` の前後比較は未実施（fresh contiguous runtime evidence が必要）。

## 認識保留の bounded escape (2026-10-15)

- 背景: #654 の配備で投下間隔 mean 9.58→3.01秒（probe session `0de2df17`）になったが、別の停滞モードが残った。現ピースの本体だけが field と同色 (50,50,48) で描画され、emblem しか見えないフレームでは、配備版の検出が `clipped-current`、ROI を2px内側にしても `ambiguous-flag`（`type=null`）となり現ピースを同定できない。`unknown-current` は入力なし → 盤面が変わらない → 次の観測も同じ、という静止ループになる。実測 `partial/game_0008/original/turn_0002.jpg`（`YOUR 91/91` の新ラウンド）で12観測・約10秒、`game_0007` は最後の投下から93秒後にラウンドが終了した（停滞で時間を消費して敗退）。
- 変更: `observation_guard.mjs` の `createHoldEscapePolicy()` が連続 `unknown-current` を数え、`main.mjs` の非MOVE分岐が上限付きの代替投下（既定 `x=0`）を行う。env:
  - `SOREN91_HOLD_ESCAPE_FRAMES`（既定 6、0で無効、上限200）
  - `SOREN91_HOLD_ESCAPE_INTERVAL_MS`（既定 4000、上限60000）
  - `SOREN91_HOLD_ESCAPE_LIMIT`（既定 3、0で無効、上限100）
- 前提: `unknown-current` は「校正と盤面は健全で、現在ピースだけが同定できない」を意味する。`observation_guard.mjs` の分類順を `uncalibrated` → `invalid-board` → `unknown-current` にし、壊れた校正・盤面がこのラベルへ丸められないようにした（`usable` / `frameValid` の意味は不変）。加えてループ側でも現フレームの `usableCalibration(...)` を要求し、`next` のないフレームでは発動しない。
- 実判定の投下（`noteDrop`）またはラウンド境界（`reset`）で連続数と残回数をリセットする。上限に達した後は従来どおり待機する。
- 観測性: telemetry schema は変えない。発動は「`reasonCounts['unknown-current']` が6以上・`holds=0` のまま1区間が閉じた dropProfile レコード」とプレイヤーログ `[game] Recognition hold escape: ...` で確認できる。

## 配備前の確認

- 本番VMの `soren91/main.mjs` には 2026-09-17 時点で手動パッチ (CDP captureScreenshot) が入っている。PR #371 をマージして配備する場合、VMパッチとの差分を確認してから置換する。無確認上書きはしない。
- `SOREN91_TEST_BROWSER` を明示した時だけ実Chromiumテストが動く (通常CIはskip)。
