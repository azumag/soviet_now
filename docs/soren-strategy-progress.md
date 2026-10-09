# sorengame：固定ベースラインとの進歩を確認する

Refs #132, #397。対象は本編sorengame。Soren91の成果を混ぜない。

## 比較監査

`tools/soren_strategy_progress.py` は保存済みの `ab_state.json` と
`ab_games.jsonl` を読む。標準ライブラリと `lib/soren_stage_ledger.py` のみを使う。
候補生成、ゲーム入力、戦略更新、採否変更、再起動、ネットワーク接続は行わない。

固定した旧戦略hashを必須入力とする。直前世代への連続した勝利を、過去の固定基準へ
勝った証拠としない。同じ実験内に固定基準がいない場合や、同じdecide hashのenv-only
A/Bは拒否する。helper・analyzer・runner・設定まで含む同条件性は別途必要。

建国と第一ロシアの成功数・既知数・未知数・分母、スコア平均・中央値・p25、手数、
完全な割付ブロックでの候補−基準の差を集計する。建国は明示的booleanだけを読み、
raw/eval/max_typeから推測しない。第一ロシアのみlegacyのt15=0/1も使用する。
矛盾は未知。`missing_outcome_bounds` は欠測を成功/失敗と置いた上下限で、信頼区間ではない。

保存済み採否JSONは任意入力。`provisional_win` と `significant_win` を区別して表示するが、
その採否JSONと実験の帰属は未検証とする。過去判定を現行コードで再計算しない。
`verified_improvement` は常にfalse。候補選抜と同じ標本を独立した確認実験に数えない。

## 第二ロシアの観測を永続化するproducer

`strategy/ab_interleave.sh::_ab_record_game` の既存追記直前で、
`lib.soren_stage_ledger.capture_stage_evidence` を呼び、**新規に完了記録する試合**へ
`archive_sha256` と `stage_evidence` を追加する。過去ledgerは書き換えない。

元履歴は一度のbounded readでbytesを固定する。全turnが1から連続し、予定・実行snapshot・
履歴全行の戦略hashが一致し、記録turn数が一致した場合に限り、次を保存する。

- 第一T15とT15×2を最初に観測したturn。
- 同時に観測したT15の最大個数。
- `first_russia_observed` / `two_russias_observed`（trueまたはnullのみ）。
- 元履歴bytesのSHA-256、およびidx・arm・game_num・archive名・戦略hash・turn数との対応digest。

ピースidは符号付き整数を受理し、同一frame内の重複idを拒否する。同じピースの重複記録を
第二ロシアと数えない。id欠測、不正型、途中の破損、turnリセットや欠番、mixed hash、
symlink/FIFO、読取中変更、16MiB/20,000行/256piecesの上限超過はunavailableにする。
入力のパス、本文、env、例外自由文を結果へ出さない。

helper不在や想定外の例外でも、固定理由 `stage_capture_unavailable` だけを追加し、
既存のscore/eval/建国boolean・試合数更新・A/B判定規則を維持する。この観測は採否に使わない。
従来metricsとは別の観測読取りなので、全metricsが同じbytes由来であるとは主張しない。

監査側は対応digestとschemaを検証し、元履歴の削除後もledgerから同時観測を集計できる。
別試合へのコピー、不一致digest、不正schemaは未知。壊れた新形式から古い履歴読取へ
黙ってfallbackしない。digestは内部対応の検査であり、署名や完全なゲームidentityではない。

**T15×2の同時観測は、第二ロシアの生成率そのものではない。** snapshotの間で即座に
併合されたケースは取り逃す。観測できなかった場合もfalseでなくnull（未知）。
Markdownは「観測数/有効試合数」と未知数を表示し、正例だけの既知分母で100%と見せない。
建国counterから「T15×2をsnapshotで見た」と逆算しない。再接続前の出来事も推測しない。

## 旧データと任意のraw履歴

旧ledgerにstage_evidenceがない場合は従来どおり未知とする。
任意の `--history` は、ledgerに対応するarchive_sha256がある場合だけbasenameのJSONLを
読み、連続turnとhashを検査する。legacyへ後付けのdigestを書いて通過させない。
新producerのunavailableを、後から有効な証拠へ書き換えない。

## 実行例

承認済みのowner読取経路で取得した、同じ終了済み実験の実在ファイルを指定する。
実行中stateを編集しない。タイムゾーンなしログの+09:00指定は、実際にJST記録と確認した場合のみ。

```sh
python3 tools/soren_strategy_progress.py \
  --state "$STATE_COPY" --games "$GAMES_COPY" \
  --baseline-hash "$FIXED_BASELINE_HASH" \
  --source-offset +09:00 --as-of 2026-10-10T00:00:00+09:00 \
  --days 7 --json > "$PRIVATE_REPORT_PATH"
```

`--days 30` で30日窓。未指定なら実験全体。指定した単一実験の範囲であり、未提供の全本番
7日/30日を集計したとは扱わない。`--json` を外すとMarkdown。
`--decision "$DECISION_COPY"` は任意。出力は集計と入力digestであり、raw本文等を公開しない。

## 検証と残件

```sh
bash -n strategy/ab_interleave.sh
python3 -m unittest discover -s tests -p 'test_soren_strategy_progress.py' -v
python3 -m unittest discover -s tests -p 'test_soren_stage_ledger.py' -v
```

新規回帰では、実際のshell writer→ledger→raw履歴削除→監査を通す。
追加観測部分だけを除いたproducerとの比較で、既存metricsとfrozen decision_ruleが不変か検査する。
fixtureは合成であり、本番の改善率・具体的悪手の解消を証明しない。

残件は、固定基準と現行候補を含む終了済み実験の原本と実行bundleの取得、試合開始/終端と
第一・第二T15生成イベントの完全な記録、#397の直近悪手を最終実行xまで再現すること、
独立した固定基準比較の事前登録と建国率による受入。進行中のA/B判定条件は変えない。
本変更の本番受入では、新規完了試合のstage_evidenceとarchiveの照合、履歴剪定後の再集計を確認する。
