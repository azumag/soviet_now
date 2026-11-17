# ローカル戦略ラボ

ソ連ゲーム本編をローカルで観察し、戦略候補を同じピース列で比較するための開発用ツール。
配信の起動スクリプト、改善 daemon、本番の commands/state/history、VM は使わない。
`strategy.py` の自動変更・採用・配備は行わない。

## 準備

- Node は `.node-version`、Python は3.10以上。`npm ci` で既存依存を用意する。
- Playwright の Chromium、またはローカル Chrome（`--channel chrome`）。
- Docker とローカルの `python:3.12-slim` image。必要な場合だけ `docker pull python:3.12-slim`。
- 高速版のビルドには、購入済みゲームのソース、ライセンス有効化済み Unity 2022.3.62f3、WebGL Build Support、Unity CLI が必要。

## 評価用ビルド

```bash
python3 tools/build_simulation.py \
  --source /path/to/soren-game-fixed \
  --editor /path/to/Unity.app \
  --out tmp/local-simulation
```

`Assets`、`Packages`、`ProjectSettings` のみを別の ignored ディレクトリへコピーする。
購入済みソース、通常の WebGL build は変更しない。Library、既存ビルド、リポジトリはコピーしない。
再実行時は自分が作った `tmp/local-simulation` を削除して同じ出力先を使う。
`build.log`、入力と出力の SHA-256 manifest を出力する。購入済みコード・アセット・ビルドは Git へ追加しない。

専用 bridge が最初のピース生成前に乱数を初期化する。ピース抽選には Unity の独立した乱数 state を使い、
合体エフェクト等による global RNG 消費と分離する。元の `GetRandomNext()` を認識できなければビルドを拒否する。
元ゲームの `fixedDeltaTime`、collider、質量、重力、接触・併合処理は維持する。
`timeScale` は1〜16。自然なゲームオーバー・建国演出の停止は上書きしない。

**固定 timestep だけでは通常速度との完全な物理等価性を証明できない。**
`DropState` はフレーム単位の `deltaTime` を使うため、速度や負荷で配置可能時刻がずれる可能性がある。
高速版は探索用とし、同seed/同policyの反復、1xと高速、slots=1と複数slotsの結果・手列・盤面を照合する。
採用判断には通常速度の実プレイも使う。実効倍率は `simulation_sec / elapsed_sec` に記録される。

## 実行

```bash
# 固定seedで現行戦略を観察。初回は1並列から測る。
node tools/simulation_lab.mjs --seeds 11,12 --speed 1 --slots 1 --out tmp/lab-baseline

# 高速の候補比較。同seedの順序をAB / BAと交互にする。
node tools/simulation_lab.mjs --policy strategy.py --candidate candidates/example.py \
  --seeds 11,12,13,14 --speed 8 --slots 2 --out tmp/lab-candidate

# 既存の通常WebGL buildで、ビルド前の速度・判断を観察する。
# seedは識別用番号にすぎず固定されない。候補とのpaired比較は禁止。
node tools/simulation_lab.mjs --build sorengame/build --legacy --speed 1 \
  --channel chrome --seeds 1 --slots 1 --out tmp/lab-legacy
```

出力先は既存ディレクトリを拒否する。並列上限4、既定1。共有Macではまず1〜2で測定する。
HTTP serverは自動割当のloopback port一つ、Chromiumは一つ、各試合は別context。
外部originへのブラウザ通信は拒否する。認証済みブラウザや既存ゲームには接続しない。

各腕の最初のコード・全Python helper・runner・analyzerを最小入力としてsnapshotする。
正規 `strategy.py` のA腕だけactive AST検証、任意policyとB腕は既存candidate AST deny gateを通す。
全腕をネットワークなし・read-only・非rootのDocker workerで実行し、ホスト実行へfallbackしない。
snapshot以外をmountせず、ホスト環境変数をcontainerへ渡さない。imageは開始時のSHA IDで固定する。
コンテナごとに1 CPU / 512MB / 16 PID上限、権限を全dropする。
RPC stdoutは1行256KiB・累計16MiB、stderrは1MiB、各要求は30秒で打ち切る。

判断は `build_analysis → enrich_deadline → decide → clamp → safety → finalizer → clamp`。
静止判定とdeadline接触判定は現行runnerの関数を使う。3回の静止観測はゲーム内時刻で0.15秒間隔、
静止強制期限は30ゲーム秒。解析中は専用ビルドを止め、PythonやDockerの処理遅延による盤面変化を防ぐ。
この停止やIPCの違いもあるため、配信runnerとの時間的同一性は別途検証する。

## 記録と判定

- `manifest.json`: buildとpolicy入力のSHA-256、設定、Docker image ID。
- `A-seedN.jsonl` / `B-seedN.jsonl`: 着手前の完全state、最終実行判断、解析時間、判断時間、終了state。
- `*.png`: 終了時の実画面。`*.stderr.log`: 容量制限付きworker診断。
- `results.jsonl`: score、最大type、`makeSorenCount`、完走状態、実時間、ゲーム時間、queue、policy bundle。
- `summary.json`: 完走ペアだけの腕別指標とseedごとの差。

片腕失敗、重複、policy混在、既知queueの共通prefix不一致、証跡保存失敗は比較無効とする。
`timeout` / `turn_limit` / `interrupted` はゲームオーバー扱いにしない。異常・未完了時は終了コード1。
`comparison_valid` はデータの対応関係を示すだけで、統計的優越や本番物理への転移を証明しない。
数試合の高スコアだけで候補を採用しない。T14/T15到達と `makeSorenCount` も観察する（#132、#866）。

Ctrl-C / TERMでは開始済み試合を中断として記録し、その試合のcontextと所有containerを回収する。
SIGKILLやホスト停止で残った場合は、そのrunの `*.container.json` / `*.cid` に記録された
**自分のcontainerだけ**を `docker rm -f <記録されたID>` で回収する。全container・既存portを掃除しない。
記録は確認用に残し、不要になった自分の `tmp/lab-*` と `tmp/local-simulation` を削除する。

## 検証

```bash
node --test tests/test_simulation_lab.mjs
SOREN_TEST_DOCKER=1 node --test tests/test_simulation_docker.mjs
python3 -m pytest -q tests/test_build_simulation.py tests/test_policy_bundle.py --basetemp=tmp/pytest-simulation
```

Unityが必要な受入は別: 固定seedの再現、通常対高速、1対複数slot、終了・中断、queue照合、
GAMEOVERと建国演出、実測した速度とCPU/メモリを記録する。ビルド成功だけで完了としない。

## 初回観察（2026-11-17）

既存WebGL buildを使う通常速度の非seed試合は、101手 / 191.5秒、score=1,233、最大T13、建国0で完走した。
70手時点の中央値は解析約7.1ms、判断約1.5ms。試合全体の解析合計3.66秒、判断合計0.39秒。
少なくともこの試合では主な待ち時間は落下・静止であり、判断コードよりゲーム進行の高速化を先に整える。
これ一試合から現行戦略の平均性能や改善効果は推定しない。
