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

## captureのCDPラウンドトリップ分解 (2026-10-08, #452)

- 実測のcapture内訳は `geometryBefore / screenshot / imageValidate / geometryAfter` の4段階で、`imageValidate` はほぼ0ms、リモートでは `geometryBefore`+`geometryAfter` が各1 CDP round-trip (実測 約345ms / 約436ms) でした。つまりcaptureコストの大部分は **画像バイトではなくCDP round-trip数** です。
- `page.screenshot({clip})` はPlaywrightの内部wrapperを通り、1枚あたり `_originalViewportSize` → `inPagePrepareForScreenshots` (全frame評価) → `document.fonts.ready` → `Page.getLayoutMetrics` → `Page.captureScreenshot` → cleanup評価 と**約9個の追加 client→serverメッセージ**を発行します。遅延150msのCDPトンネルで `createCanvasIO().capture()` を実測すると、Playwright経路が **9〜14メッセージ / wall 2.8〜4.1秒 (screenshot段 2.2〜2.8秒)**、生 `Page.captureScreenshot` (既存のgeometry用セッションを再利用、追加セッションなし) 経路が **3メッセージ / wall 0.96秒 (screenshot段 約0.35秒)** でした。フレームは両経路で **バイト一致** (実Chromium、launched/connectOverCDP、PNG/JPEG、DPR=1/2で確認)。
- したがってcapture cadenceの残りの支配要因は、JPEG化 (#621) では削れない **screenshot wrapperのround-trip** です。`realtime_io.mjs` に `SOREN91_CAPTURE_BACKEND=cdp` を追加し、`captureClip()` でPlaywrightと同じclip (viewport内に収まるregion、scrollオフセット、enclosing int size、scale 1) を計算して生コマンドを1回だけ送る経路を用意しました。geometry before/after、scale検証、freshness、calibration、input直前のvalidationは全て不変です。
- **既定は `playwright` のまま**です。ページの device-metrics override を所有していないセッションから生 `Page.captureScreenshot` を送ると、そのoverrideがセッション既定値へ置き換わります (実測: `devicePixelRatio` 2→1、`screen` 1280x720→800x600)。本番のゲームページには bot 側のviewport設定によるoverrideが載っているため、無条件に切り替えません。cdp経路はoverride破壊をscale mismatch / geometry changeとして検出し、実測値を `Emulation.setDeviceMetricsOverride` でbest-effort復元 (visible windowはリサイズしない) した上でそのプロセスでは二度と使わないため、opt-inしてもページを壊すのは高々1回です。
- 本番適用前に実機で「1回のcaptureの前後で `window.devicePixelRatio` / `innerWidth` / `innerHeight` が変わらない」ことを確認してから `SOREN91_CAPTURE_BACKEND=cdp` を設定してください。`dropSentIntervalMs` の p50/p95 と `captureStageMs.screenshot` を配備前後で比較します。
- 併せて、安定待ち再観測のpoll待ちを「このイテレーションの開始からの残り時間」に変更しました。リモートでは1回のcaptureが既に数秒かかるため、`POLL_INTERVAL_MS`=200msを毎回上乗せする分 (実測 poll段 約0.6秒/区間) は純増の遅延でした。gate判定は不変です。

## 配備前の確認

- 本番VMの `soren91/main.mjs` には 2026-09-17 時点で手動パッチ (CDP captureScreenshot) が入っている。PR #371 をマージして配備する場合、VMパッチとの差分を確認してから置換する。無確認上書きはしない。
- `SOREN91_TEST_BROWSER` を明示した時だけ実Chromiumテストが動く (通常CIはskip)。
