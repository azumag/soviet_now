# ポーリングループの待機契約（docich#2688）

2026-10-23 23:03 JST、VM の `ps` に sleep なしの python3 ポーリングが瞬間
85.7% / 70.0% で現れ、親プロセスが消えた孤児として観測された。python3 自体は
正常な one-shot 処理で、原因は「待たずに次の python3 を起動するループ」だった。

## ルール

1. 無限ループ（`while true` / `while :` / `until false`）の中で `python3` を
   起動する場合、そのループ本体は必ず待機する。
   - 常駐 worker: `docich_poll_sleep <secs>`（forkless、`secs` は 1 秒未満にならない）
   - 汎用ループ: `sleep` / `read -t` / 子プロセスの `wait` など、CPU を手放す待機
2. 待機秒数を環境変数から読む場合は `docich_poll_interval <raw> <default> <min>`
   を通す。`0`・負数・非数値は既定値または最小 1 秒へ丸める。
   `sleep 0` は「待たない」であり、ポーリングループでは常に不具合。
3. 手元の状態確認を ad-hoc な `while true; do python3 -c ...; done` で書かない。
   `tools/poll_status.sh` を使う。

```bash
# 1回だけ
tools/poll_status.sh --once

# 2秒間隔で 3 tick（round_version と decide hash を 1 python3 で取得）
tools/poll_status.sh --count 3 --interval 2

# 60秒間だけ（既定。最大 3600 秒）
tools/poll_status.sh --duration 60
```

## 回帰

`tests/test_sleepless_polling.py` が次を固定する。

- `docich_poll_interval` が 0 / 非数値を拒否する
- `docich_poll_sleep 0` が 1 秒待つ
- `tools/poll_status.sh` が指定間隔を守り、待機中に CPU を焼かない
- tracked な `*.sh` に「python3 を起動する無限ループで待機がない」ものが
  新規に増えない

`tests/test_poll_wait.py` の zero-wait 契約も同じ理由で 1 秒待機へ更新した。
