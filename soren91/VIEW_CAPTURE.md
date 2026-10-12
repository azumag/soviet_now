# 全画面canvasのview capture（#452）

## 目的と変更範囲

投下判断より画面取得が支配的という #452 の記録に対し、遠隔CDPの
スクリーンショットwrapper往復を削る。配置戦略・HOLD・認識の安定確認・
クールダウン・入力前の座標/鮮度確認・AI設定は変更しない。

`realtime_io.mjs` の既存セッションで、geometry前確認 →
`Page.captureScreenshot({fromSurface:false,captureBeyondViewport:false})` →
geometry後確認の3コマンドを使う。新しいsession、画像cache、追加capture、
Emulation設定、前面化、ウィンドウリサイズは行わない。

Chromiumの非surface経路はclip/emulationを適用せずviewを取得するため、
**可視canvasがスクロールなしのviewportを整数サイズで完全に覆う場合だけ**使う。
それ以外は既存Playwrightのcanvas clipを維持する。
参考: [Chromium PageHandler::CaptureScreenshot](https://chromium.googlesource.com/chromium/src/+/refs/heads/main/content/browser/devtools/protocol/page_handler.cc)。
PR #636のsurface captureで起きた表示倍率変更を検出してから復元する方式とは別案であり、
そのPRのpoll待ち変更は取り込まない。#636のブランチも変更しない。

## 設定と安全性

- `SOREN91_VIEW_CAPTURE`未指定: `SOREN91_REMOTE_CDP_URL`が非空なら候補にする。
- `SOREN91_VIEW_CAPTURE=0`: 従来方式。未知の非空値も無効扱い。
- `SOREN91_VIEW_CAPTURE=1`: localでも候補にするが、全画面・可視条件は必須。
- captureの戻り値`captureBackend`は`view`または`playwright`。
  これはframe metadataであり、既存loop metricsへ新項目を公開する変更ではない。

画像サイズ/形式、前後のgeometryと対象document/canvas identityを検証する。
view captureの失敗はその観測を拒否し、そのpageで次回以降は従来方式へ固定する。
失敗した同一iterationで再撮影や入力をせず、timeoutのsession退役・非重複も維持する。
入力直前に可視性も再確認する。認識不明や古い観測からの投下は許可しない。

## 回帰検証

リポジトリrootから実行する。

```sh
node --check soren91/realtime_io.mjs
node --test tests/test_soren91_view_capture.mjs
SOREN91_TEST_BROWSER=/path/to/chromium node --test tests/test_soren91_view_capture_browser.mjs
SOREN91_TEST_BROWSER=/path/to/chromium SOREN91_TEST_HEADED=1 \
  xvfb-run -a -s '-screen 0 1920x1080x24' \
  node --test tests/test_soren91_view_capture_browser.mjs
```

2026-10-13の隔離Linux検証: Node22.16.0、Chromium144.0.7559.96、
環境に導入済みPlaywright1.57.0-beta-1764944708000（lockfile版ではない）。
単体22件成功。実ブラウザはDPR1/2×PNG/JPEG×2D/WebGLの8件で、
headed/Xvfbは8件成功、headlessは2Dの4件成功・WebGL初期化不可の4件skip。
最初のheadless WebGL実行はfixture初期化で4件失敗し、環境不足として明示skipへ分離した。

既存方式との復号pixel一致、描画変更後に新pixelを取得すること、DPR/viewport/screen/scrollの維持、
入力前検証、非全画面へのfallbackを確認した。headless WebGLのskipは成功に数えない。
`SOREN91_TEST_BROWSER`未指定なら実ブラウザ8件はskipであり、通常CIの成功と実描画検証を区別する。
単体の模擬時計による1050ms対2800msは待ち段の検証用で、実測速度ではない。

## main統合・本番反映のゲート（未実施）

この変更は遠隔モードの既定挙動を変えるため、通常CIだけでは本番適用しない。
既存の全Soren91回帰、独立レビュー、実際のremote hostのOS/ChromeとUnityゲームでの
pixel・表示倍率・鮮度・入力受理の比較を完了してからmain統合を判断する。
Linuxの2D/WebGL fixtureを実機Unity・macOS/Windowsの証拠には代用しない。

承認された隔離canaryで同条件のbaseline/candidateを比較し、連続した観測から
`dropSentIntervalMs`のp50/p95、captureStageMs、誤入力/拒否/timeoutを確認する。
ゲーム側の投下受理も送信間隔とは別に確認する。2〜3秒/投下の達成は未確認。

main統合後の配備はdocichのowner-only正規経路から行い、`games/soviet_now`参照と
対象ファイルhashを照合する。共有配信基盤を再起動せず、必要な変更はSoren91だけに限定する。
取り消しは正規設定経路の`SOREN91_VIEW_CAPTURE=0`または本変更のrevert。
通常起動/対象プロセス反映まで確認し、フラグ追加だけで反映済みとしない。
#452は実機の速度・誤drop比較が終わるまでcloseしない。
