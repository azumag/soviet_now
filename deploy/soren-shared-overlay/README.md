# soren-shared-overlay

独立した共通配信オーバーレイ（通知・ステータス枠）を Soren 本体とは別に描画する
`systemd` unit 一式。`shared_overlay.mjs` が Chromium を `:99` にアタッチモードで
起動し、ゲーム本体のウィンドウとは独立にオーバーレイだけを表示する。

## なぜ定期再起動するか

常駐 Chromium の RSS は時間とともに増える。実測では約 2.8 日稼働で
**6.1GB**（約 2GB/日）まで膨らみ、ピーク 5.8GB を記録した。メモリ不足で
即座に落ちるわけではないが、長時間放置は無駄なGC/スワップ圧を招くため、
**1日1回 再起動してメモリを回収**する。

2026-09-14 実測: 再起動で **6.10GB → 0.30GB**、オーバーレイ窓は数秒で復帰、
本番 encoder（`ffmpeg`）の PID・稼働時間は不変。

## スケジュール

`soren-shared-overlay-restart.timer`

- `OnCalendar=*-*-* 05:30:00`（サーバのローカル時刻。本番は JST）
- `RandomizedDelaySec=300`（0〜5分の揺らぎで同時刻の競合を避ける）
- `Persistent=true`（停止期間中に逃した実行を起動後に補う）

再起動の数秒間はオーバーレイが消える。視聴者の少ない早朝を選んでいる。

## インストール

```bash
sudo cp deploy/soren-shared-overlay/soren-shared-overlay.service \
        /etc/systemd/system/
sudo cp deploy/soren-shared-overlay/soren-shared-overlay-restart.service \
        deploy/soren-shared-overlay/soren-shared-overlay-restart.timer \
        /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now soren-shared-overlay.service
sudo systemctl enable --now soren-shared-overlay-restart.timer
```

## 確認

```bash
# 次の実行予定
systemctl list-timers soren-shared-overlay-restart.timer

# 再起動後のメモリと状態（数秒で復帰）
sudo systemctl restart soren-shared-overlay.service
systemctl show -p MemoryCurrent -p ActiveState soren-shared-overlay.service
DISPLAY=:99 xdotool search --name "Shared overlay"

# 本番 encoder が巻き込まれていないこと（PID が変わらない）
pgrep -af "x11grab"
```

## 注意

- これは**メモリ回収**のための再起動であり、配信のカクつき（CPU飽和）を
  直接直すものではない。CPU の主因はソフトウェア描画（SwiftShader の
  Chromium）と本番 encoder、AI ワーカー群。
- `soren-shared-overlay.service` は `Restart=always`。`restart` は停止→起動を
  行い、`NRestarts` は新インスタンスで 0 に戻る。
- 時刻を変える場合は timer の `OnCalendar` を編集して `daemon-reload` →
  `restart soren-shared-overlay-restart.timer`。
- 無効化する場合: `sudo systemctl disable --now soren-shared-overlay-restart.timer`
