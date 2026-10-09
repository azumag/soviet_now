# sorengame：固定ベースラインとの進歩を確認する

Refs #132, #397。対象は **本編 sorengame**。Soren91の成果を混ぜない。

## このPRでできること／できないこと

`tools/soren_strategy_progress.py` は既存の `ab_state.json` と
`ab_games.jsonl` の形式を読む、stdlibだけの**読取専用監査**。
候補生成、ゲーム起動、戦略更新、A/B採否、再起動、ネットワーク接続は行わない。
`ab_decide.py` の `provisional_win` を含む現行採用方針は変更しない。

固定した旧戦略のhashを必須入力とする。各実験の直前世代を毎回基準にする
連鎖比較を、「過去の固定戦略より強くなった」という証拠にしない。
同一実験に固定基準がいない場合や、同じdecide hashのenv-only A/Bは拒否する。
後者はフルpolicy bundleでのidentity導入後に別途扱う。

- 建国と第一ロシア：既存ledgerの明示的booleanを集計。第一ロシアのみ、
  legacyの `t15=0/1` も使用する。矛盾は未知。raw/eval/max_typeから建国を捏造しない。
- 未知件数、既知件数、分母を併記。`missing_outcome_bounds` は欠測の上下限であり、
  統計的な信頼区間ではない。既知試合だけの率を全体の率と呼ばない。
- スコア平均・中央値・下位25%点、手数、第一ロシア到達が既知の試合内での建国率。
- 完全な割付ブロックだけの「候補−固定基準」の差。欠番・順序違い・欠測を補完しない。
- 任意の保存済み採否JSONを `recorded_adoption` に表示するが、過去の判定を
  現在のコードで再計算しない。そのファイルと実験の帰属は未検証と表示する。
- `verified_improvement` は常にfalse。この監査単独では採用や「改善確認済み」へ昇格しない。

戦略hashが一致しても、helper・analyzer・runner・設定・ゲーム環境までは証明しない。
逐次選抜された候補の同じ標本を、独立した確認実験に数えない。
中断・timeout等、完了試合ledgerに無いattemptの件数は不明のまま。

## 実行例

終了済み実験のstate、games、必要なら採否結果を、承認済みのowner読取経路で
同じ証拠ディレクトリへ保存する。動作中のstateを手編集しない。
以下の変数には、その**実在する保存ファイル**と事前に選んだ固定基準hashを指定する。

```sh
python3 tools/soren_strategy_progress.py \
  --state "$STATE_COPY" --games "$GAMES_COPY" \
  --baseline-hash "$FIXED_BASELINE_HASH" \
  --source-offset +09:00 --as-of 2026-10-10T00:00:00+09:00 \
  --days 7 --json > "$PRIVATE_REPORT_PATH"
```

`--days 30` で30日窓。窓を指定しなければ実験全体。これは指定した実験の窓であり、
未提供の実験・未保持期間を含む全本番の7日／30日集計ではない。
実験ごとの環境・評価版を混ぜて合算しない。タイムゾーンのない過去ログは
`--source-offset` を明示しない限り採用しない。`+09:00` は実際にJST記録だと確認できた
場合に限る。`--as-of` はタイムゾーン付き必須。
`--json` を外すと日本語Markdown。`--decision "$DECISION_COPY"` は任意。
出力は集計と入力SHA-256だけで、raw env、本文、画像、ローカルパスを公開しない。

## 第二ロシアの観測はまだ不足している

現行の `strategy/ab_interleave.sh` は、第二ロシアの累積到達をledgerに残していない。
本PRはproducerや進行中実験を書き換えない。このため旧ledgerの第二段階は原則未知。

任意の `--history "$HISTORY_COPY_DIR"` は、ledgerに**対応する `archive_sha256`** が
既に記録されている場合のみ、basenameのJSONLを読み取る。現行producerはこのdigestを
書かないため、既存データに後付けでdigestを書いて通過させてはならない。
全turnの連続性（1から開始）と戦略hashを照合し、途中でT15が2個あるsnapshotが
観測できた場合だけ `two_russias_observed=true` とする。これは「第二ロシア生成率」
そのものではなく、同時観測できた下限。snapshot間での即時併合を取り逃すため、
2個を観測しなかった場合もfalseではなく未知。途中の正例の後に破損があれば採用しない。

次のproducer変更では、試合開始identity・counter基準・terminalと実行bundleを固定し、
第一／第二T15の生成イベントと建国イベントを、履歴剪定より前に永続化する必要がある。
この設計と実戦受入は本PRの完了条件には含めず、#132に残す。

## 証拠の完全性

idx、game_num、archiveの重複は先着を採らず全重複行を除外する。
割付順、三つのhash（予定・実行snapshot・履歴）、明示的tainted=false、開始時刻と
試合番号を確認する。欠落・不一致の除外件数を表示する。
入力JSONの破損・重複キー・NaN/Infinityは成功レポートを出さず終了コード2。
ファイルは最大32MiBのregular fileのみ。symlink・FIFO・読取中の変更を拒否する。
数値統計は非負かつ2^53以下の有限数だけを扱い、boolをスコアにしない。

## 次の実戦改善（未実施）

1. 固定基準と現行候補を含む実験証拠を取得し、同じ評価版・環境で比較する。
   フルbundleと独立した確認標本がなければ、率の上昇を改善確定と呼ばない。
2. #397の本編側で、直近の失敗ログから「nextNextの同型への経路をnextで破壊」を
   一件特定する。選択x→safety→finalizer→最終実行xを再現し、安全な即時併合と
   deadline制約を保つ一変更だけを実装する。新しい実戦ログが無い間は係数をいじらない。
3. 候補選抜とは別に、目標建国率・標本数・欠測規則・停止規則・固定基準を事前登録して
   再比較する。少数の成功や暫定採用数では完了にしない。

## 検証

```sh
python3 -m unittest discover -s tests -p 'test_soren_strategy_progress.py' -v
python3 -m py_compile tools/soren_strategy_progress.py
```

fixtureは全て合成。テスト成功は本番成績の向上、実戦比較完了、悪手修正を意味しない。
