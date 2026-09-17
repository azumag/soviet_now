# Soren91: 最適化前の投下間プロファイル

## 目的と非目的

前の `dropSent()` から次の `dropSent()` までを、同一試合内で計測する。
速度設定・戦略・観測の安全条件・撮影方式・クリック処理は変更しない。
測るのは送信完了同士の経過時間。ゲーム側の入力受理数・投下位置・勝率は未計測であり、送信数を実効投下数として扱わない。
テストのモック時計は本番実測ではない。

## 記録

既存 `tmp/state/soren91_loop_metrics.json` のトップレベル schemaVersion=1 と既存フィールドを維持し、`dropProfile` を追加する。
既存 `stageMs` はターン単位のまま。**投下間の分析には必ず `dropProfile.records` を使う。**

- `basis=sent-to-sent`、`acceptedDropsMeasured=false`。
- プロセスごとのランダムな `session` と単調増加する `sample`。
- `game`、`fromTurn`、`toTurn` は既存mainの0始まりターン番号。
- `endedAtMs` は記録の照合用Unix時刻。経過時間そのものは既存の単調時計で測る。
- `durationMs` と同一区間の `stageMs`、処理回数 `phaseCalls`、観測回数、HOLD送信回数、errorループ数、固定分類の観測理由。
- 各試合の最初の投下は始点のみ。試合間待機・次の投下に至らなかった末尾区間は完了サンプルにしない。
- 完了サンプルは試合をまたいで直近128区間を保持する。`totalSamples` / `evictedSamples` を明示する。再起動で新しいsessionになるため、停止・再起動前に既存スナップショットを正規経路で採取する。
- 既存の保存先・原子的な0600書込み・保存周期を使用する。追加のデーモン・timer・ネットワーク送信はない。ただしJSON拡大による保存負荷はゼロではない。

## フェーズの意味

| フィールド | 含むもの |
|---|---|
| capture | mainの通常観測の撮影・転送・保存。複数回なら区間内で合算 |
| analyze | 通常観測の画像解析・状態判定とモジュール読込 |
| ranking | 既存の順位確認ラッパー全体。その中の追加撮影や待機も含む |
| decide | 戦略読込と判断。HOLD判断後の再判断も合算 |
| input | HOLD以外の入力処理全体。座標確認・照準後の200ms・クリックを含む |
| holdInput | `holdSent()` 直前の入力処理。既存のHOLD後300msを含む |
| cooldown | 計測された実際のクールダウンsleepのみ。設定値1.2秒を別途加算しない |
| poll | 計測された実際の状態ポーリングsleep |
| overlap | 複数のmeasureが重なった時間。二重計上や推測による片側への帰属をしない |
| unattributed | ログ・履歴書込・計測自身・既存の未計測sleep等を含む残差 |

`capture` の回数はmainの通常撮影呼出し回数であり、ranking内の追加撮影枚数ではない。
エラー後の既存の1秒sleepは `unattributed` に入る。`errors` と併せて確認する。
入力の細分化や順位確認内部の追加撮影枚数は今回追加していない。`unattributed` や大きな上位フェーズが支配的なら、次にその中だけ計測を追加する。原因の確定前に動作を変えない。

全区間について `durationMs ≒ sum(stageMs)` を検査し、丸め差を `accountingErrorMs` に出す。1msを超える不整合または時計の逆行は `accountingValid=false` として集計から除外する。
フェーズ別中央値同士を足して全体中央値と比較しない。構成比は合計時間から求める。

## 読み取り・集計

計測修正のレビュー・正規配備後、所有者が利用できる承認済みの読取経路でJSONを採取する。
現在のowner-only VM gatewayの診断に当該JSONが公開されているとは限らない。権限・forced-command境界を迂回しない。
以下はファイルを取得済みの環境、または承認された本番シェルで実行する読取専用コマンドである。

```sh
# soviet_nowリポジトリのルートから
node soren91/summarize_drop_profile.mjs soren91/tmp/state/soren91_loop_metrics.json
node soren91/summarize_drop_profile.mjs --json soren91/tmp/state/soren91_loop_metrics.json
# 採取済みの複数ファイルも可。同一session/sampleは重複排除する。
node soren91/summarize_drop_profile.mjs --json snapshot-1.json snapshot-2.json
```

既存の古いJSONに `dropProfile` がなければ終了コード1で未取得扱い。完了区間が0件なら秒数はN/A、終了コード2。欠落・除外数は必ず報告し、欠測を0秒で補わない。
通常表示はフェーズごとの平均・中央値・95パーセンタイル・最大・構成比・平均呼出し回数。
JSONには投下ごとの内訳、HOLDあり/なし、試合別、前回投下のターン番号が10未満/以上の集計も含む。後者はmainの投下後順位確認条件と対応する。
`latestDropAtMs` を実際の運転時刻と照合する。ファイル更新が新しくても、保持された最後の投下が古いことがある。

## 本番ベースラインの採取条件

1. docichのgitlink、配備先の実際のSoren revision・dirty状態、稼働プロセスの開始時刻、撮影モードを記録する。リポジトリmainと本番が同一とは仮定しない。
2. 設定・戦略は固定。通常の正規コーナーで複数試合、まず30以上の完了投下間を目安に採取する。足りない場合は実数と不足を報告する。
3. 序盤/後半・HOLD有無・認識保留理由・error/overlap/残差も併記する。
4. 欠落のない対象範囲で内訳表を作り、支配的なフェーズを特定してから改善案を決める。
5. ゲームで実際に落ちた回数との照合は別検証。送信間隔だけで速度改善・受理成功を宣言しない。

この文書の追加時点では、本番JSONの取得・配備・実戦計測は未実施。自動マージ・本番上書き・稼働中ゲームの再起動は行わない。

## 回帰

```sh
node --test tests/test_soren91_drop_profile.mjs
node --test tests/test_soren91_*.mjs
```

新規テストでは、投下後順位確認の次区間への帰属、再観測、HOLD、実sleep、重複計上、区間をまたぐmeasure、試合境界、残差、保存上限、秘匿、書込失敗、重複排除、不整合と欠測、読取専用CLIを確認する。
