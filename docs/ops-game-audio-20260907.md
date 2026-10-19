# 2026-09-07 — ゲーム音の接続先を補い、BGMと効果音の配信入力を復旧

- 稼働bridge PID 3519044にXDG_RUNTIME_DIR/PULSE_SERVERがなく、ffplayがPulseAudio接続失敗で終了・再試行。実効環境の1秒再生で再現し、PULSE_SERVER明示時はexit 0。
- VMの既存 `/home/ubuntu/.config/pulse/client.conf` を同ディレクトリへ日時付きbackup後、`default-server = unix:/run/user/1001/pulse/native` を追記。既存autospawn=no保持。アプリコード・サービス再起動なし。
- 自動再試行でffplay BGMとpaplay落下SEがsoren_nullへ接続。8秒のmonitor実測 mean -29.6 dB / max -12.7 dB。bridge 3519044・encoder 3520687維持。視聴側の聴感は未確認。起動側で環境が欠けた経緯は未調査。



設定はVMユーザーのPulseAudioクライアント設定です。元の `autospawn = no` を保持し、以下を追加しました。

```ini
default-server = unix:/run/user/1001/pulse/native
```

ロールバックは日時付きclient.conf.backupから復元します。ゲームや配信の再起動は不要でした。

## 2026-10-20 — 配信PulseAudioの所有者をユーザーserviceへ固定し、capture喪失を監視する (docich#2008)

2026-10-11 05:55 に deploy の service 再起動が、`soren-bgm.service` の cgroup に
autospawn されていた PulseAudio を巻き添えで停止した。autospawn し直した server は
XDG_RUNTIME_DIR を持たず `/tmp/pulse-*/native` にソケットを作ったため、encoder は
`Error during demuxing` で音声入力を失い、映像だけを配信し続けた。

### 恒久設定（owner 承認の保守窓で1回だけ）

`install_pulseaudio_user_service.sh` は次を冪等に適用する。`--status` は変更なしで
現在の所有者・ソケット・client.conf を表示する。

```bash
./install_pulseaudio_user_service.sh --status
# 音声が一瞬途切れるため、owner 承認済みの保守窓でのみ:
./install_pulseaudio_user_service.sh --install --confirm-audio-session
```

- `pulseaudio.socket` / `pulseaudio.service` を user service として enable し、
  server の cgroup を `user@1001.service/...` に固定する。`loginctl enable-linger` も
  確認する。
- `~/.config/pulse/client.conf` を日時付き backup のうえ、`autospawn = no` と
  `default-server = unix:/run/user/1001/pulse/native` の2行だけを管理する。
- ソケット `/run/user/1001/pulse/native`、`pactl info`、`soren_null` sink を実測し、
  欠けていれば null sink を load する。

`deploy/soren-bgm/soren-bgm.service` は `XDG_RUNTIME_DIR` と `PULSE_SERVER` を明示し、
BGM worker の子プロセスが private server を autospawn できないようにした。system unit の
変更は `sudo systemctl daemon-reload && sudo systemctl restart soren-bgm.service` で
反映する（BGM のみ再起動し、encoder と配信は維持する）。

### encoder の capture 喪失監視

`lib/direct_stream.py` は live 配信中、既定10秒間隔で `pactl list source-outputs` を
読み、この FFmpeg の source-output が実際に存在するかを確認する。

- 一度でも capture を観測したあと、daemon は応答するのに source-output が
  2回連続で消えた場合だけ FFmpeg を再起動する（`pulse_capture_lost`）。
  listing 形式の変化や pid 非公開で誤再起動しないよう、未観測なら再起動しない。
- daemon 自体が無応答の間は映像を維持し、復帰後に再起動する。2026-10-11 の
  「映像だけ継続」を停止へ拡大しないための境界。
- 状態は `status.json` の `audio_capture` に `status` / `consecutive_unhealthy` /
  `checked_at` / `pulse_server` / `source` として残る。

調整は `SOREN_DIRECT_STREAM_AUDIO_WATCHDOG_ENABLED`（既定1）、
`..._PROBE_SEC`（既定10、5–120）、`..._LOSS_THRESHOLD`（既定2、1–10）。
`PULSE_SERVER` は local unix socket のみ受け付ける。

コード配備後の反映は、この変更を含む Soren runtime の通常 deploy で行う。
`audio_capture` が `status=ok` を保ち、deploy 後に encoder の source-output が
維持されることを diagnostics と `direct_stream.sh status` で確認する。

