# JEV sorengame player corner

この文書は Issue #771 の実装境界を記録する。既定のプレイヤーは
`existing` のままで、JEV は手動で明示的に準備した試合だけで有効にする。
この実装は実API canary、本番起動、VM配備を行わない。

## Player change の境界

同じ `sorengame` を再起動・複製せず、`lib/game_lifecycle.py` の既存 broker
へ `operation=player_change` を渡す。request には `run_id`、現在の
`expected_player_generation`、固定設定の SHA-256、target policy を含める。

1. bridge が `player_policy_v1` capability を、現在の `soviet_local.mjs` PID
   とともに広告する。
2. `request` は現試合を止めず、broker の同一 request identity を保存する。
3. `boundary` は runner が終了し、`game_state.json` が `GAMEOVER` または
   `STOP` になった後だけ `prepared` を返す。
4. `commit-player` が player generation をCASで1つ進め、privateな
   `player_state.json` を atomic replace する。失敗時は次試合を開始しない。
5. 次に起動した loop/bridge はこの snapshot から policy、run、generation を
   読む。欠損・不正な snapshot は JEV ではなく `existing` に fail-closed する。

JEV の one-game 完了時は `jev_one_game.json` を committed snapshot と同じ
identityで記録し、supervisor の `soren_loop` 再起動を抑止する。`finish` 側は
loopが既に終了していても broker の side-effect-free `boundary` を実行できる。
bridge は player snapshot を観測ごとに再読込し、bridge自体を再起動せずに
JEV identity と existing policy の切替を反映する。

`player-commit` は以下の固定 control surface から実行できる。

```text
./game_lifecycle_control.sh player-commit <request-id>
```

旧来の `request` に `operation` がない場合は、既存の game-only stop の意味を
維持する。共通 overlay、audio、encoder は player change では停止しない。

## JEV の1手

JEV 経路は次の順序だけを持つ。

```text
bridge observation
  -> uniform25-v1 candidate IDs
  -> bounded TypeSafe worker
  -> validated choice
  -> guarded JSON drop
  -> bridge state transition
  -> accepted ack / outcome_unknown
```

`next` は現在落下中の駒、`nextNext` は最初の preview として扱う。自由な
X座標、自由文、shell command、既存 strategy の score/reason/recommendation は
JEV入力に入れない。low confidence は正常な choice なら採用し、通信・契約・
受理障害だけを明示的 fallback として記録する。

bridge が post-drop の駒 identity/phase 遷移を観測できない場合、画面操作が
返っただけでは `accepted` にせず `outcome_unknown` とする。再送しない。

## 証拠と分離

実験証跡は `tmp/jev_player/runs/<run_id>/` の private ledger に保存する。
通常の `game_history/latest.jsonl`、score/best、prediction、改善 accumulator、
回帰判定、promotion へ JEV 試合を流さない。JEV の画像は API に送らず、実行後
レビュー用のゲーム領域 capture として別途追加する。

実行前の最低確認:

```text
python3 -m unittest discover -s tests -p 'test_jev_*.py'
node --test tests/test_jev_guarded_drop.mjs
python3 -m unittest tests.test_game_lifecycle
```

実API key、live capability、実試合の `accepted`、画像取得、VM反映はこのローカル
実装確認とは別の明示ゲートである。
