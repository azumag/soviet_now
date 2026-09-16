# Soren91 投下遅延の修正と検証

2026-09-16: ユーザー観察は **実投下間隔が約20秒**。過去の「1ループ5〜6秒」とは区別する。
この変更は撮影・入力周期の実装修正であり、実戦で20秒が解消した証明ではない。

## 変更

- remote CDPでは、毎回の `locator.screenshot()` に代えて、即時に取得・検証したcanvas矩形を `page.screenshot({clip, scale:'css', timeout})` で撮影する。要素のactionability/安定待ち・scroll処理を撮影ごとに追加しない。撮影前後と入力直前にcanvas個体・ページ・矩形・DPR・scroll・viewportを照合する。
- 生の `Page.captureScreenshot` を新規CDP sessionから呼ぶ方式は採用しない。検証用Chromiumで別sessionのDPR emulationを変える挙動を確認したため、Playwright自身の撮影sessionを使う。
- 撮影は既定3秒、有限の設定範囲内でタイムアウトする。期限切れの画像を保存/解析/入力に使わず、終わらない撮影を無制限に並列化しない。ブラウザー全体を閉じたり前面化/resizeしない。
- 1.2秒の最低入力間隔は維持し、その間に観測する。待った後は必ず再撮影し、古い解析をそのまま投下しない。HOLDは既存300ms待ちを維持し、実投下用の1.2秒を再加算しない。
- post-drop ranking probeのremote既定OFFをNode側でも評価し、`.env`や直接起動でrunner側の設定を通らなくても適用する。明示の`1`は尊重する。
- ranking burstは撮影回数だけでなく単調時計による期限と、残り撮影timeoutで制限する。解析中の同期CPU時間までは強制中断できないため、厳密なリアルタイム保証とは扱わない。
- analyzer/calibration等はファイルstatが変わったときだけ再importする。不変のラウンド戦略snapshotは同一URLを再利用し、毎ターンの再評価とESMキャッシュ蓄積を避ける。戦略係数・認識ガード・自動改善のモデル/頻度は変更しない。

## 数値の確認

`tmp/state/soren91_loop_metrics.json` は0600でatomic更新する。通常観測は最大毎秒1回、入力/エラー時は即時更新。秘密情報、コメント、プロンプト、任意のエラー本文は出力しない。

- `stageMs`: 同じ投下ターン内のcapture/analyze/ranking/decide/input/cooldown/poll累積時間。
- `observations`, `holds`, `reasonCounts`, `elapsedMs`: 何回の観測や保留で1投下に至ったか。
- `dropSentIntervalMs`: HOLDを除いた**クリック送信完了間隔**。最新128サンプルのp50/p95/max。試合間の待機を混ぜない。

これはゲームに受理された投下の計数ではない。既存`Decision`間隔も同様に実投下間隔と混同しない。実戦画面での入力受理・順位・早期敗退は別途確認する。

## 設定

- `SOREN91_CAPTURE_TIMEOUT_MS`: 既定3000、200〜5000ms。
- `SOREN91_INPUT_MAX_FRAME_AGE_MS`: 既定2500、500〜10000ms。撮影要求開始からの保守的な年齢。期限切れはクリックしない。
- `SOREN91_CAPTURE_MODE=locator`: 撮影方式だけを旧方式へ戻す診断用設定。設定未指定のremote経路は新方式、local経路は旧方式を維持。
- `SOREN91_RANK_POSTDROP_PROBE=1`: 追加順位確認の明示opt-in。remote既定はOFF。

未知ピース・未校正・動いている盤面をtimeoutで無理やり投下する変更はしていない。

## テスト

```sh
node --check soren91/main.mjs
node --test tests/test_soren91_realtime_io.mjs
node --test tests/test_soren91_*.mjs
```

実ブラウザー回帰は使い捨てのheadless Chromiumだけで行う。ブラウザーを自動downloadせず、既存の配信Chromeには接続しない。

```sh
SOREN91_TEST_BROWSER=/path/to/chromium node --test tests/test_soren91_realtime_browser.mjs
```

通常はlockfileの`playwright`を使う。別環境で明示的に検証するときのみ `SOREN91_TEST_PLAYWRIGHT` にimport可能なモジュールURLを指定できる。ブラウザー未指定時はbrowserテストをskipする。

## 配備と未確認事項

soviet_nowのPR/最新CIを確認しmainへ統合後、docichのgitlinkを更新し、owner-only VM control planeで配備する。unknown driftは上書きしない。試合境界でプレイヤーのみを切り替え、新PID・実行SHA・配信/共通音声PID維持を確認する。

本実装時点では本番VM/Macへ接続せず、作業バナー・作業音声・実ゲーム操作・本番反映は実施していない。独立エージェントレビューは未実施。実戦前後の数値とゲーム受理の確認が終わるまで、復旧済みとはしない。
