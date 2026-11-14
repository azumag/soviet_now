# A/B 実験と root (strategy.py) 変更の扱い (soviet_now#865)

確認段階の A/B が戦略 PR の直接マージで無判定終了した (10-31 / 11-04 / 11-15 と
3 回)。この文書は「strategy.py / strategy_helpers を変える変更を merge する
とき」「merge で root が変わったあと」の手順をまとめる。

## 仕組み

- 本番 runtime は A/B 状態を `runtime/ab-state` ブランチの `ab_status.json` へ
  publish する (`strategy/ab_status.sh`, `AB_STATUS_PUBLISH_ENABLED=0` で停止)。
- `ab_status.json` には実験の有無・両腕の hash・phase・games・
  `root_matches_a` (現 root が実験の A と一致するか) が含まれる。
- `.github/workflows/ab-guard.yml` が strategy を変える PR でこれを読み、
  実施中なら PR に警告コメント (`<!-- soren-ab-guard -->`) を残す。
  merge 自体はブロックしない (警告のみ)。
- 観測が古い/取得できない場合も「確認できない」として警告する (fail-open では
  ないが merge は止めない)。

## merge 前のチェック

1. PR の A/B guard コメントを見る。
   - `active`: 実験中。実験の終了を待つか、下の方針を確認してから merge。
   - `drifted` / `stale` / `unknown`: 本番で `./tools/ab_retroactive.sh list` と
     `tmp/state/ab_state.json` を確認してから merge。
   - `inactive`: そのまま merge してよい。
2. 本番で直接見る場合: `runtime/ab-state` ブランチの `ab_status.json`、または
   soren で `cat tmp/state/ab_state.json`。

## A/B 中に root を変える場合の扱い (`AB_ON_ROOT_DRIFT`)

`.env` の `AB_ON_ROOT_DRIFT` で選ぶ:

- `retire` (既定): 従来どおり実験を勝敗なしの `stale_base` として退役する。
  収集済み標本・alt file は `tmp/history/ab_<ts>_*_stale*` へ保全される。
  特例: 観測された新 root が B 候補と同じ hash の場合 (B が直接 root へ
  入った場合) は方針に関わらず `stale_base_b_merged` として退役する —
  rebase すると A=B の同一腕実験になり元の A との比較を失うため。
  このときの後追い比較は `start --b <state の a_hash>` で「現 root vs 元 A」。
- `rebase`: 退役せず、A を観測された新 root へ張り替えて測定を最初から
  やり直す。B (候補) はそのまま残る。confirmation 中でも selection へ戻る。
  旧 root との標本は `tmp/history/ab_<ts>_games_rootdrift.jsonl` へ保全されるが、
  旧 root との比較値なので判定標本には混ぜない。drift は state の
  `root_drift_events[]` に記録される。

どちらを既定にするかは DSH 判断 (issue #865 コメント参照)。
実装は両対応で、`.env` 行の切り替えだけで変えられる。

## 直接 merge した変更への後追い実戦比較

`tools/ab_retroactive.sh` (soren ルートで実行):

```
./tools/ab_retroactive.sh list                     # stale 退役実験の一覧
./tools/ab_retroactive.sh show <ts>                # 退役実験の詳細
./tools/ab_retroactive.sh prev-roots [n]           # git 履歴から直近 root hash を解決
./tools/ab_retroactive.sh start --b <path|hash>    # 現 root (A) vs 指定 (B) で A/B 開始
./tools/ab_retroactive.sh start-stale <ts>         # 退役実験の B 腕 vs 現 root
```

- `start` / `start-stale` は通常の `_ab_start_from_bundle` 経路でインターリーブ
  A/B を始める。state の `retroactive` に由来 (対象 ts・元の hash・退役理由) を
  記録する。
- 実験中・`active_branch.json` 存在中は開始を拒否する。
- `prev-roots` は管理 clone (`SOREN_PERSIST_REPO`) の `origin/main` から
  strategy.py の decide-hash を解決する。archive hash しか残っていない場合は
  `strategy_versions/by_hash` / `strategy_versions_archive/by_hash` から
  `start --b <hash>` で実体を取り出せる。

## 注意

- `ab_status.json` は読み取り専用の観測 (push 先は runtime/ab-state のみ)。
  merge 前の最終確認は常に本番 state を見るのが確実。
- publish は内容の変更時 + 30 分 heartbeat で走る (実験イベントでも即時 push)。
- この層は merge 側の可視化と後追い比較を足すものであり、deploy reconcile
  側の fail-close 検証 (docich) は変えない。
