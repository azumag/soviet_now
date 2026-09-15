# soren-shared-overlay

独立した共通配信オーバーレイ（通知・ステータス枠）と、その `/healthz`
readiness gate を提供する `systemd` unit。

## 役割

- ゲームから独立した共通レールを描く（ゲームの起動/停止に連動しない）。
- ゲーム切替の不可逆な停止前に bridge が確認する `/healthz`
  readiness gate（既定 `http://127.0.0.1:8092/healthz`）を提供する。
- ゲーム専用モード（docich のゲーム切替中、docich が presenter を出している間）
  に、共通レールと presenter を合成して配信に出す。

## 配置（スタッキング契約）

overlay は 1280x720 の fullscreen Chromium ウィンドウで、**X スタックの最下層**
に固定する。ゲームのウィンドウはその上に載る。

- ゲーム専用モード: presenter（`docich-present-*`）はゲーム矩形
  `(0, 90, 960, 540)` ちょうどのサイズなので、周囲の共通レールが overlay から、
  ゲーム領域が presenter から見える。
- メインゲーム表示中: ゲームページ（1280x720）が overlay を完全に覆う。
  メインのレールはゲームページ側が描くため、見た目は変わらない。

`start_shared_overlay_service.sh` が overlay ウィンドウへ `_NET_WM_STATE_BELOW` を
付け、`docich-present-*` を `above` に保つ（5 秒ごとに再表明）。

**重要（2026-09-15 の障害）**: 以前はこの unit をゲーム専用モードの開始時に
start していた。fullscreen ウィンドウは「後から map された方が上」なので、
すでに表示中のゲームウィンドウの上に overlay が載り、配信が**4分間まっくら**
になった（レール＋空のゲーム領域だけ）。overlay を常時起動して最下層に固定
することで、この経路自体を無くしている。

## 使い方（常時起動）

overlay は **boot から常時起動**する。ゲーム切替やコーナーのために
start/stop しない（`game_lifecycle_control.sh` も toggle しない）。

```bash
sudo cp deploy/soren-shared-overlay/soren-shared-overlay.service \
        /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now soren-shared-overlay.service
```

`enable` を忘れないこと。`disable` のままにすると起動せず、bridge の
readiness gate が fail-open（`unsupported`）になり、不可逆な停止は行われない
（旧ゲームが継続するだけで、配信は壊れない）。

`SOREN_SHARED_OVERLAY_DISPLAY`（既定 `:99`）と
`SOREN_SHARED_OVERLAY_ATTACH=1` で、配信 X ディスプレイ上に描画する。

## 確認

```bash
systemctl is-enabled soren-shared-overlay.service   # enabled
systemctl is-active  soren-shared-overlay.service   # active
curl -fsS http://127.0.0.1:8092/healthz             # ok/ready/browserReady/layoutReady/overlayReady
```

スタッキングの実測（ゲーム表示中でも overlay が最下層であること）:

```bash
DISPLAY=:99 xdotool search --name "^Shared overlay"   # overlay の window id
DISPLAY=:99 wmctrl -l -G                              # ゲーム/presenter が overlay より上
```

## 注意

- この service はゲームプロセスを探索・停止しない。逆にゲームの停止・再起動は
  この service に波及しない（通知枠・ステータス枠・配信エンコーダ・共通音声と
  同様、ゲームから独立した共通基盤）。
- 配信エンコーダや X server は再起動しない。overlay の再起動でエンコーダ PID は
  変わらない。
- overlay のウィンドウを最前面に上げないこと（ゲームを覆う）。
  再起動直後は `start_shared_overlay_service.sh` の stacking ループが
  自動で `below` を再表明する。
