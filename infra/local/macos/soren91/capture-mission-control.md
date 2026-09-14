# Mission Control中のキャプチャ位置ずれ対策

関連: [macOS renderer](README.md)、[Issue #303](https://github.com/azumag/soviet_now/issues/303)、
[位置監視のPR #311](https://github.com/azumag/soviet_now/pull/311)、
[docich #386](https://github.com/azumag/docich/pull/386)の利用時に報告された症状。
#386自体はTwitchタイトル復元の修正であり、本変更の対象ではない。

## 修正範囲

`tools/macos/soren91_window_capture.swift` と
`tools/macos/soren91_capture_frames.swift` で、ScreenCaptureKitの出力を正規化してから、
従来どおりの固定サイズ・密詰めBGRAとしてffmpegへ渡す。

- `.complete` のサンプルと必須メタデータだけを新しい正常画像として採用する。
- `contentRect` は出力画像内のピクセル領域。画面全体の座標ではない。
  `contentRect.size / contentScale / scaleFactor` で元のウィンドウの論理サイズを確認する。
  矩形を切り抜き、固定サイズに戻してから既存のゲームCanvas cropへ渡す。
- メタデータの上原点をCore Imageの下原点へ変換する。密度を二重に掛けない。
- 本当にウィンドウがリサイズされた場合、起動時のCanvas cropは信頼できない。
  無条件に引き伸ばさず、不正フレームとして扱う（論理サイズの許容差2pt）。
- macOS 14以降では単一ウィンドウの影とglobal clipを無視する。
  #311にも同じ設定があるが、同PRのCDP watchdogや音量・二重枠変更は取り込まない。
- 対象は起動時にbundle IDとタイトルが完全一致する一つのウィンドウのみ。
  全画面キャプチャや別ウィンドウへのフォールバックは追加しない。

元となるAPIの説明:
[Apple WWDC22: Take ScreenCaptureKit to the next level](https://developer.apple.com/videos/play/wwdc2022/10155/)
（content rect / content scale / scale factor）。

## 正常フレーム保持とタイミング

補正できないフレーム、空・不完全なサンプル、捕捉の一時停止では、最後の正常画像を保持する。
最初の正常画像がまだない場合は、架空の正常画像や別の画面を送らない。

キャプチャのコールバックとstdout書き込みは別キュー。
保持するのは正規化済みの最新Data一つだけで、ScreenCaptureKitのIOSurfaceを保持しない。
stdout側の詰まりでキャプチャプールを使い切ったり、古い画像のキューを積み上げない。

ffmpegのrawvideo入力は時刻情報を持たず、フレーム数を30fpsとして解釈する。
そのため送出は単調時計による固定周期とし、更新がなければ正常画像を再送する。
短いタイマー遅延は不足フレームを補う。0.5秒を超える遅れは異常終了させ、
無制限の追いつき送信や音声との恒常的な時刻ずれを隠さない。

既存hostはキャプチャ起動後に音声tapの準備を待ち、その後にffmpegを接続する。
この初回待機を配信中の遅延と混同しないよう、出力時計は最初の書き込み完了時に開始する。
それ以後の書き込み停止は別の監視で2秒以内を目安に検知する。

最後の正常画像／有効なidle通知から10秒、または最初の不正フレームから10秒で
回復しなければ、`capture-hold-timeout:*` をstderrに出して非zero終了する。
連続する不正フレームや、その後のidleは期限を延長しない。
idleは「既存の正常静止画像に更新がない」という用途に限る。
下流終了のEPIPEは従来どおりexit 0、他の書き込み失敗は非zero。

このヘルパーは安全画面への切替やコーナー停止を制御しない。
異常終了後に受信側が最後の画像を保持する可能性があるため、OCI側の停止・本編復帰も
下記E2Eで別途確認する。正常フレームを永続的に送り続けて故障を隠す実装にはしない。

## 診断

通常は準備完了・致命的エラー以外のログを増やさない。検証時のみ、Mac agent起動環境で
`SOREN91_CAPTURE_DIAGNOSTICS=1` を設定するか、ヘルパーに `--diagnostics` を渡す。
`event=capture-frame` のJSONを最大1件/秒、準備完了JSONより後にstderrへ出す。
内容はフレーム状態、有効領域、縮尺、固定バッファサイズ、採否理由のみ。
画像・他のウィンドウタイトル・URL・tokenを記録しない。

再ビルドは `./tools/soren91_window_capture_build.sh`。
補助Swiftファイルまたはビルドスクリプトだけが変わった場合も再ビルド対象になる。
バイナリ更新後の録画権限と、実行中プロセスが新バイナリであることはMac側で確認する。

## 自動テスト

```bash
swiftc -O -parse-as-library tools/macos/soren91_capture_frames.swift \
  tests/test_soren91_capture_frames.swift -o /tmp/soren91-capture-frame-tests
/tmp/soren91-capture-frame-tests
node --test tests/test_soren91_macos_capture_privacy.mjs
```

Foundationの幾何・保持・送出周期テストはLinuxでも実行可能。
macOSでは、画面録画を伴わない合成BGRA画像でパディング除去・位置補正・上下方向・拡縮も検証する。
CIは既存のmacOS helper buildにこの実行テストを追加する。
自動テスト合格とMission Control実機検証合格は別扱いとする。

## Mac + OCIでの受入確認（未実施）

通常配信とは別の、Tailscale限定のテスト受信先を使用する。

- [ ] 通常状態でゲームだけが960x540 / 30fps、音声も正常。
- [ ] Mission Control開始・終了を10回繰り返し、補正画像または短い静止だけで構図が戻る。
- [ ] Mission Controlを数秒開いたままにする。10秒以上正常フレームが得られない場合は
      規定の異常終了を確認し、その後の本編復帰は別の責任範囲として測定する。
- [ ] Spaces切替、遮蔽、別ディスプレイへの移動で、別ウィンドウが映らない。
- [ ] 真のサイズ変更、最小化、捕捉停止で、不正な切り抜きを新しい正常画像として採用しない。
- [ ] OCI録画で映像PTSの連続性、音声とのずれ、負荷、少なくとも90秒の受信を測定する。
- [ ] 受信側を先に終了してEPIPE/exit 0を確認。stop後に関連プロセスが残らない。
- [ ] PR #311と併用する場合は、位置監視との組み合わせでも再検証する。

メタデータが正常に見えるのに画像の内部だけが変形するOS挙動は、この検証だけでは排除できない。
実機で再現する場合は診断値と映像を比較して切り分け、完治・本番適用済みとは扱わない。
