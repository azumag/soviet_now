# コメント本文だけを使う Jev 分類器（実験）

追跡: azumag/docich#678（本文限定に更新したコメントが入力仕様の正本）。
本変更は無効が既定。APIキー設定、本番有効化、デプロイ、再起動は行わない。
Jev導入とSoren91のshadow実験は独立している。

## 接続と境界

`eloop_lib.sh` が `comment.sh` の直後に `comment_classifier_jev.sh` を読み込む。
既存の `comment_runtime_policy.sh` と同じラッパー方式で、元の関数を保持する。
元の `comment.sh` は変更・複製しない。docichの `chat.py` も同じloaderを参照する。
`comment.sh` だけを直接sourceする既存テスト／利用者は従来経路のままである。

backendが `jev` の場合だけ、Pythonアダプタが元のヒューリスティックと正規化関数を
先に実行する。Jev採用時も変更するのは `category` のみ。
`index/user/comment/is_english`、返答生成、英訳、バグ報告の権限フラグは維持する。
アダプタ自体の起動失敗も直接ヒューリスティックへ戻し、旧45/90秒のAI経路には入らない。
未設定または `jev` 以外なら旧フラグの意味をそのまま保つ。

送信するJSONは固定モデル設定、固定カテゴリ基準、本文、一時的な対応IDだけから新規構築する。
ペルソナ、現在のゲームや盤面、投稿者、会話履歴、視聴者メモ、ヒューリスティックの予測は
含めない。本文に書かれたゲーム名や `: ` は本文の一部として残す。
本文自体に名前などがあり得るため、これは完全な匿名化ではない。
同一バッチ内の他の本文も会話履歴として参照しないよう指示するが、干渉ゼロは実測が必要。

既知botと、従来判定が `card_gacha/raid/subscription/stream_goal/bits` の行は送信・上書きしない。
Jevが通常コメントをそれらの通知へ昇格させる結果も採用しない。
初版は通知保護を優先するため、既存の通知カテゴリの誤検出をこの実験では修正できない。

## 設定（まだ有効化しない）

レビュー・upstream統合・docich参照更新・正式な配備を終えてから、対象環境の秘密情報管理で設定する。
APIキーをチャット、Issue、コマンド引数、Gitへ貼らない。

```dotenv
COMMENT_CLASSIFIER_BACKEND=jev
# TYPESAFE_API_KEY は実行環境のsecretから注入
COMMENT_CLASSIFIER_JEV_MODEL=jev-1.13.0
COMMENT_CLASSIFIER_JEV_TIMEOUT_MS=1500
COMMENT_CLASSIFIER_JEV_MIN_CONFIDENCE=0.70
```

APIは固定の `https://api.typesafe.ai/v1/systemone` へ直接接続する。
SDK、追加パッケージ、Nodeバージョン変更、Vercel Gatewayは不要。
HTTPリダイレクトを追跡せず、環境変数のHTTPプロキシも自動利用しない。
Linux/macOSのローカルディスクとPython 3.9以降を対象とする。

1バッチにつき最大1要求、最大8本文、各本文4096 UTF-8 bytes、全要求32768 bytes。
上限外は削って推論せず、その行のヒューリスティック結果を返す。API再試行は0回。
専用stateのnonblocking flockにより同時要求は1件。混雑時は待たずにfallback。
全チャットworkerで同じstateディレクトリを使う必要がある。

DNS、接続、ボディ受信を含め通信用子プロセスを期限でkill・回収する。
親PythonへのSIGTERM/SIGINTや例外によるキャンセルでも、子のプロセスグループをkillし、
直接の子をwaitしてから終了する。SIGKILLや親の強制クラッシュは捕捉できない。
シグナル管理が可能なメインスレッドでのみ起動し、それ以外では子を作らず失敗する。
既定1500msはJev要求の時間予算であり、分類全体の保証ではない。
ヒューリスティック側の子プロセスは別に3秒で打ち切り、起動異常時はshellの旧heuristicへ戻す。
OSスケジューリング、元の処理、ファイルI/O、ログ書き込みの時間をゼロとは扱わない。
401/403は300秒、429は30秒、529/5xxは10秒、通信失敗/timeoutは5秒の専用cooldown。
429/529の数値Retry-Afterは最大300秒まで反映する。共有AI backoffには触れない。

調整用の任意設定:

```dotenv
# 省略時はリポジトリ配下の以下の場所。ローカルディスクを使用する。
# COMMENT_CLASSIFIER_JEV_STATE_DIR=/.../tmp/state/comment_classifier_jev
# COMMENT_CLASSIFIER_JEV_METRICS_DIR=/.../tmp/comment_classifier_jev
# 計測を止めるときだけ0
COMMENT_CLASSIFIER_JEV_LOG_ENABLED=1
```

rollbackでJevを無効化するときは、設定元に `COMMENT_CLASSIFIER_BACKEND=` を明示し、
対象workerを完全再起動する。`.env` の行削除やコメントアウトだけでは、再sourceしても
長寿命shellに残った `jev` はunsetされない。USR1/HUP reloadだけを完了条件にしない。
旧workerと実行中の分類子プロセスが終了し、新PID・起動時刻・実効backendが空であることを確認する。
`TYPESAFE_API_KEY` も設定元／secret注入元から削除する場合、既存プロセスからの
APIキー除去には再起動が必要。親supervisorの環境に残っていればworkerは再継承するため、
キーを保持する親も対象として、除去済みの環境から起動する。キー値は表示・記録しない。
`COMMENT_CLASSIFIER_AI_ENABLED` 等は導入前の値を保つ。
コードを戻す必要がある場合も元の分類器はそのまま残っている。
本番反映はdocichのowner-only VM control planeを使用する。手動コピー／勝手な再起動はしない。

## 計測

有効時だけメタデータJSONLを保存する。本文、投稿者、本文hash、キー、生HTTP body、
生例外は保存しない。ランダムなbatch_id、カテゴリと確率、採用理由、model、固定rubric版、
実装ファイルSHA-256、要求／分類時間、usageのみ。
UTC日付ごとに最大1MiB×2ファイル、当日を含む3暦日（通常最大6MiB）。
次回書き込み時に期限切れをGCするため、停止した環境での自動GCは行わない。
ログのロック競合・書き込み失敗は分類を妨げず、ログが欠けることはあり得る。

```bash
python3 lib/comment_classifier_jev_report.py tmp/comment_classifier_jev/metrics-*.jsonl*
```

`heuristic_ms` は元のshell関数の起動・正規化を含む。
`jev_ms` は実際に試みた要求のみ。`classification_ms` はアダプタ内のbaseline開始から
結果確定までで、外側のPython起動とJSONL書き込みは含まない。
p50/p95/p99とsample数、バッチサイズ別の時間、成功率、採用coverage、fallback理由、
カテゴリ遷移を出す。バッチ時間を件数で割って1コメントの応答時間とは呼ばない。

usage不明の失敗は0トークン／無料とみなさない。既知usage分の推定費用と不明要求数を別に出す。
費用計算は確認済みの `jev-1.13.0` のみ入力$0.042/百万tokens、出力無料を使用する。
別モデルは費用不明。価格は運用時にも再確認する。
**一致率は正解率ではない。confidenceの閾値0.70も正答率70%を意味しない。**

## 正解ラベル付き評価

常時ログに本文を足さず、別途アクセスを制限したJSONLを用意する。
形式は `{"text":"...","category":"general_question","ambiguous":false}`。
初版CLIは1行1本文を受け取り、API使用は明示フラグが必要。
合成例は `tests/fixtures/comment_classifier_jev_eval.jsonl`。実視聴者コメントではなく、
これだけで本番精度や日本語性能を評価したとは言えない。

```bash
# 実行するとTypeSafeへ本文を送信する。secretと実行許可を準備してから行う。
python3 lib/comment_classifier_jev_report.py \
  --evaluate /restricted/labelled-test.jsonl --allow-api
```

一時本文は0700の一時ディレクトリ内0600ファイルに書き、終了時に削除する。
本番stateと計測ログを汚さず、heuristic／Jev／hybridを比較する。
Jev失敗・通知保護で予測がない行も分母とcoverageに残す。
カテゴリ別precision/recall/F1、混同行列、全件に対する正解割合を出す。
曖昧とラベル付けした行は評価正解率から除き、件数を別記する。
このCLIはバッチサイズ1であり、実配信の複数本文バッチの干渉・時間はオンライン計測で別に調べる。
閾値調整用と最終評価用を分離し、通常例のランダム標本と境界例の追加標本も混ぜない。

## テストと残件

```bash
python3 -m unittest discover -s tests -p 'test_comment_classifier_jev.py' -v
bash -n broadcast/comment_classifier_jev.sh eloop_lib.sh
```

通常CIはmockのみでAPIキー不要。元リポジトリcheckoutでは、本物のヒューリスティック・
正規化と、キーなしJev経路の一致も確認する。
GitHub専用CIでLinux/macOSを対象にし、既存のComment reply quality等も維持する。
実API canary、実コメント200〜500件程度の評価、本番のp95・費用・改善効果は未測定。
初回の実API応答で公式schema・固定modelが利用可能かを必ず確認する。

一次資料（2026-09-19確認）:
- https://docs.typesafe.ai/api
- https://docs.typesafe.ai/models
- https://docs.typesafe.ai/confidence

## 運用引き継ぎ

本変更はPR段階で止め、勝手にmain統合／本番有効化しない。
upstreamがレビュー・マージされた後、docichの `games/soviet_now` 参照を更新する別PRを作る。
その後に正式な配備と少量canaryを行い、実測証跡をdocich#678に残す。
作業環境にVM/webuiへの承認済み接続がないため、OBSの作業バナー・読み上げは操作していない。
独立エージェントによるレビューは未実施。自己レビューとテスト結果をPRへ記載する。
