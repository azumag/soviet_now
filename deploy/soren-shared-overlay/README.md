# soren-shared-overlay

独立した共通配信オーバーレイ（通知・ステータス枠）と、その `/healthz`
readiness gate を提供する `systemd` unit。

## 使い方（Option A: ゲーム専用モード時のみ起動）

この overlay は **boot では起動しない**。メイン SorenGame 表示中に起動すると、
overlay の不透明な blank stage（`lib/shared_overlay.mjs` が
`background:#000` / `stageMode:blank` を設定）が全画面でゲームを覆ってしまう。

overlay が必要なのは **ゲーム専用モード**（docich のゲーム切替中）だけ:

- ゲーム専用モードでは、overlay が共通の通知/ステータス枠を出し、
  さらに bridge が不可逆なゲーム停止の前後に参照する `/healthz`
  readiness gate を提供する。
- そのため `game_lifecycle_control.sh` がライフサイクルに合わせて
  `soren-shared-overlay.service` を start/stop する:
  - `stop-after-boundary` … ゲーム停止前に overlay を startし `/healthz` ready を待つ
  - `fresh-start` / `cancel` … メインゲーム復帰後に overlay を stop

`SOREN_GAME_LIFECYCLE_SHARED_OVERLAY=1`（`.env`）でこの連携が有効。

## インストール

```bash
sudo cp deploy/soren-shared-overlay/soren-shared-overlay.service \
        /etc/systemd/system/
sudo systemctl daemon-reload
# 重要: enable しない（boot 起動させない）。起動/停止は game_lifecycle_control.sh が行う。
sudo systemctl disable soren-shared-overlay.service 2>/dev/null || true
```

## 確認

```bash
systemctl is-enabled soren-shared-overlay.service   # disabled
systemctl is-active  soren-shared-overlay.service   # inactive (main モード)
curl -fsS http://127.0.0.1:8092/healthz             # ゲーム専用モード中のみ 200
```

## 注意

- メイン配信中の通知/ステータス枠は bridge 側の overlay
  （`generate_soren_overlay.sh`）が描くため、この service が止まっていても
  枠は表示される。
- この service をメインゲーム表示中に起動するとゲームが黒く覆われる。
  手動で `systemctl start` しないこと。
- 過去に「メモリ回収のための定期再起動」を試したが、再起動時に
  overlay が black stage を再インストールしてゲームを覆うため廃止した。
