# コーナー別の統計パネル

`show-status-g` と `generate_status_overlay.sh` は共通の
`status_dashboard.py` を表示する。コーナーごとに別の監視プロセスを
起動する必要はなく、`DOCICH_STATE_DIR` のコーナー状態から表示対象を選ぶ。
複数のコーナーがactiveの場合は従来どおり競合表示にする。

| コーナー | 成績の読み取り元 | 集計対象 |
|---|---|---|
| レトロ各ゲーム | `scores/<game>.jsonl` | gameが一致する保存済み試合。過去の開催分も含む |
| NetHack | `nethack/runs/*.json` | 終了済みrunだけ。進行中・中断保存中は除外 |
| Soren91 | Soren内 `soren91/tmp/summaries/game_*.json` | 確定順位1〜91の記録 |
| JEV | Soren内 `tmp/jev_player/runs/*/report.json` | 完了レポートのスコア。旧レポートは中途終了と試合完了を区別できないため、その旨を表示 |
| PAPER | `trading/status.json` | 資金・保有数・約定履歴。ゲームスコアへ換算しない |

統計は保存されている対象成績の件数・平均・中央値・最高値。
直近30件平均と、その直前の最大30件平均との差も表示する。
推移と分布は直近100件、末尾の数値列は直近8件。
分布の区間はゲームの値域から作り、1〜2件でも推移を表示する。
Soren91は最小順位をbestとし、1位回数、上ほど良い順位となる推移を表示する。
削除済みの履歴を含む生涯成績とは限らない。

今回のコーナーの進行件数は開始時刻以降の記録だけで数える。
開始時刻が不明なら進行件数を `--` とし、過去履歴を今回の進行に流用しない。
NetHackの `current.json` はrun-id参照として解決し、終了履歴へ重複追加しない。

HTML変換では、枠なしのコーナー見出しを直前のAI backoff枠と結合しない。
これにより、推移グラフまで `.rail-only` に入って非表示になる問題を防ぐ。
通常のSorenヘッダー枠の非表示処理は維持する。

## 検証と反映

```sh
python3 -m unittest tests.test_docich_corner_stats tests.test_status_dashboard_ab tests.test_status_dashboard_founding_rate tests.test_dashboard_data
bash -n generate_status_overlay.sh
```

HTML回帰テストは本番と同じ埋め込みPythonを実行し、コーナー統計の可視性と
通常ヘッダーの非表示を確認する。運用state・ゲーム・OBSへの書き込みは行わない。

本番反映はdocichのPR/main/canonical gateway経路を使う。
Pythonの読み取り処理は毎回起動されるが、HTML変換関数は既存の
`generate_status_overlay.sh watch` プロセス内に残るため、配備時には
**対象の `status_overlay_watch` だけ**の入れ替えが必要。
配信エンコーダ・ゲーム・共通音声を再起動しない。
反映後は対象workerの新PIDと共通基盤PID維持、実際のコーナーの履歴・
推移・分布・周囲の枠を確認する。ローカルのサンプル表示は実機受入と区別する。
