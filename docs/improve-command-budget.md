# 改善CLIの時間予算・利用制限（#193）

## 対象

`RUN_AI_IMPROVEMENT_MODE=1` の改善CLIだけを `strategy/improve_command.py` で監督する。
通常のradio/chat/news、モデル順・provider・`.env` の再試行既定値は変更しない。
worker起動時に `.env` を読み、その後に単一のmonotonicな総期限を確定する。

- 全体: `IMPROVE_WALL_TIMEOUT`。明示的な `SOREN_IMPROVE_JOB_BUDGET_SEC` は
  `.env` source前に捕捉し、既定の上限を増やさない範囲でこのjobだけを短縮できる。
- 分析: 既定では総予算の半分。`SOREN_IMPROVE_ANALYSIS_BUDGET_SEC` を指定する場合も
  全体より短い正数を要求する。期限はjob開始から測り、再試行でリセットしない。
- 各CLIは個別timeout・総期限・分析期限の最小値で終了する。期限切れなら次候補を起動しない。
- 既存CLI待ち行列は残り秒を切り捨てて上限設定し、1秒未満なら起動前に戻る。
- OSの停止/I/Oハングや非CLIのバッチ集計・sandbox作成・隔離評価を強制停止する仕組みではない。
  それらの処理後にも期限を確認し、期限後の候補採用/出力を止める。従来の外側watchdogは維持する。

## 観測契約

実機と同じOpenCode 1.18.27の公式source `cli/cmd/run.ts` はJSON出力へ
`session.error` を転送するが、途中の `session.status=retry` を転送しない。
このため `--format json --print-logs --log-level INFO` を利用し、起動したCLIの
**専用stderrパイプ**の構造化ログを検査する。既存共有ログ/SQLiteは実装で読まない。

INFO `created` によるrun/session/実cwdの対応と、ERROR `stream error` の
同一run/session/provider/modelを要求し、その `error.error` にある利用制限だけを分類する。
stdoutはsessionに対応する正式なJSON `type=error` 以外を判定材料にしない。
本文やtool出力の「429」「rate limit」は判定に使わない。不明な形式は利用制限と決めず
通常timeoutで終了する。将来のCLI形式変更にはfixtureの更新が必要。

- `79 / rate_limited`: 利用制限。#196の既存shared backoffへ渡し同一model再試行を打ち切る。
- `124 / call_timeout`: 当該CLIのtimeout。従来のfailure-backoffへ渡す。
- `80 / job_deadline_exhausted` または `stage_deadline_exhausted`: 予算切れ。
  モデル故障ではないのでproviderのbackoffを作らず、後続modelも起動しない。
- `81`: 予算/guard/queueの構成不備。候補を成功扱いせず停止する。
- 全利用制限/全shared backoffと、利用制限＋通常失敗の混在を別理由で記録する。

CLIは専用process groupへ起動し、終了/期限/signal時にそのgroupだけTERM→KILLで回収する。
独立sessionへdaemon化して逃げた子processまで管理するcgroupではない。生成候補の隔離や
権限は別の既存sandbox契約で維持する。

## 結果記録と成功判定

呼出しごとにhost側 `tmp/state/improve_receipts/job-PID/` へ0600のJSONを原子的に保存する。
モデル・label・configured retry/timeout・当初の残り予算・実時間・終了理由を記録する。
プロンプト本文、argv、資格情報、provider error全文はreceiptへ保存しない。

改善経路では「期待ファイルが5秒安定したらCLIを終了して成功」とする旧watchdogを使わない。
CLIの正常終了と実ファイル変更を要求し、期限後に残った部分ファイルを成功へ昇格させない。
分析の**内容**や因果仮説の妥当性はこの変更だけでは証明しない。別の分析契約検査と
候補の静的/隔離/性能検証を必要とする。

## 検証・反映

fake CLIと実process/時計で、session/model取り違え、本文偽装、deadline前後、無出力hang、
大量出力、途中ファイル、子process回収、他process生存、複数model・混在失敗を検査する。
CIでは実AI/APIを呼ばない。helper→ai.sh→workerの依存を全て揃えてから使用する。
本番の独自差分をmain全体で上書きせず、source SHA/各preimage hashをレビューして限定反映する。
配信service/soren_loopの再起動、shadow解除、既存pause解除は必要ない。
