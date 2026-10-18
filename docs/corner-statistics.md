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

## 半熟英雄のカード

半熟英雄はスクリプト走行のコーナー（docich `retro_corner` はゲームオーバーまたは
画面300秒不変まで観測を続け、scorelogには記録しない）なので、試合成績の推移・分布と
`Strategy:` のランキングは構造的に空のままになる。空の
`Stats: no completed results yet` / `Strategy: no corner ranking data` を毎回出さず、
カード側へ観測値を置く。他のレトロゲームは従来どおりこのパネルも表示する。

カードの値は canonical `game_switch.json` の active _identity に一致する runtime の
`hanjuku_run.json` と `hanjuku_bot.json` の policy memory からの観測値に限る。
switch 中・identity 不一致・30秒超過・終了確定時は `fresh` を出さず、
全体を unavailable / stale に戻す（他ゲームの値を持ち越さない）。

| 表示 | 読み取り元 |
|---|---|
| 話数・所持金・ゲーム内年月 | policy の `chapter` / `gold` / `month`（最終観測） |
| 兵力・停滞秒数 | policy `soldiers_seen` / run `unchanged_seconds` |
| 占領記録・保有・失った城・本拠 | policy `captured` / `lost` / `home_lost` |
| 戦闘結果（勝/敗/未分類）・戦闘開始終了・切り札確定 | policy `stats` / run `battles_*` |
| 交戦HP | policy `battle` の `enemy_hp` / `ally_hp` と将軍名 |
| 駐留・行軍中・卵 | policy `garrison` / `sorties` / `egg_uses` |
| 出撃成立/失敗 | policy `orders` の `launched` / `failed` |
| 画面・方針・計画段階・保留計画・実入力・観測回数 | bot `screen_kind` / policy `variant` / `active` / run |

制約:

- `battle` がない時は交戦HPを「戦闘記録なし」とし、HPを推測しない。片側だけの
  読み取りも表示しない。
- 行軍中は policy 自身の判定（`en_route` / `launched_unconfirmed`、busy 400観測以内）
  と同じ条件で行う。行き先未確定は「未確定」と出す。解決済み・tick 不正は出さない。
- 城名・将軍名・卵・将軍名は画面から読めた名前だけを最大3件・各10文字で出す。
  未読の値は `0` ではなく「不明」にする。
- cached 文字列は ANSI/OSC escape 全体と C0/C1 制御文字を除去してから表示する。
  `isprintable()` だけでは ESC は落ちるが後続の印字可能な `[31m` が残るため、
  カードに壊れた escape が混ざる。snapshot 側とレンダラ側の両方で除去し、
  escape を失った headless な `[2J` も落とす。副作用として `array[0]` のような
  bracket 構文も削られるが、半熟英雄の将軍名・城名は日本語表示名だけでBracketを
  持たないため、実データへの影響はない。
- カード行は `show-status-g` の幅に収める。HTMLカードのパーサは各行の先頭項目
  だけを行頭固定せず拾う（後ろに観測値が増えても値を落とさない）。

## 検証と反映

```sh
python3 -m unittest tests.test_docich_corner_stats tests.test_status_dashboard_ab tests.test_status_dashboard_founding_rate tests.test_dashboard_data
node --test tests/test_direct_broadcast_overlay.mjs tests/test_shared_overlay.mjs
bash -n generate_status_overlay.sh
```

HTML回帰テストは本番と同じ埋め込みPythonを実行し、コーナー統計の可視性と
通常ヘッダーの非表示を確認する。`the card parsers still read every field the real
renderer emits` は `status_dashboard.py` の実出力（`load_active_corner` 経由）を
そのまま卡片パスへ通し、パーサと行格式のずれを検出する。fixture を手で書く関連
テストがこれを手放さないこと。運用state・ゲーム・OBSへの書き込みは行わない。

本番反映はdocichのPR/main/canonical gateway経路を使う。
Pythonの読み取り処理は毎回起動されるが、HTML変換関数は既存の
`generate_status_overlay.sh watch` プロセス内に残るため、配備時には
**対象の `status_overlay_watch` だけ**の入れ替えが必要。
配信エンコーダ・ゲーム・共通音声を再起動しない。
反映後は対象workerの新PIDと共通基盤PID維持、実際のコーナーの履歴・
推移・分布・周囲の枠を確認する。ローカルのサンプル表示は実機受入と区別する。


## 共通配信の実表示経路

共通配信は `generate_soren_overlay.sh` が出力するSTATS HTMLを
`lib/shared_overlay.mjs` 経由で読み取る。現在のcanonical gameと
`SOREN/CORNER`見出しが一致する場合だけコーナー統計を通す。
別ゲーム・不明・複数見出しは従来の待機表示へ戻し、Soren改善状態は隠す。
PAPERはpaper-view、NetHackはnethackとして照合する。

配信サイドバーのヘッダー除去は、閉じた直前のAI枠をまたがない。
統計HTMLだけでなく、このshared state APIと配信画面を確認する。
`shared_overlay.mjs` の変更は常駐Nodeプロセスに残るため、canonical
配備後に共通表示serviceの対象を確認して反映する必要がある。
ゲーム・配信encoder・共通audioを再起動せず、表示serviceの入替方法と
周囲枠・実コーナー統計の復帰を別途検証する。
