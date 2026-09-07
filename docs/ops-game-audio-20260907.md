# 2026-09-07 — ゲーム音の接続先を補い、BGMと効果音の配信入力を復旧

- 稼働bridge PID 3519044にXDG_RUNTIME_DIR/PULSE_SERVERがなく、ffplayがPulseAudio接続失敗で終了・再試行。実効環境の1秒再生で再現し、PULSE_SERVER明示時はexit 0。
- VMの既存 `/home/ubuntu/.config/pulse/client.conf` を同ディレクトリへ日時付きbackup後、`default-server = unix:/run/user/1001/pulse/native` を追記。既存autospawn=no保持。アプリコード・サービス再起動なし。
- 自動再試行でffplay BGMとpaplay落下SEがsoren_nullへ接続。8秒のmonitor実測 mean -29.6 dB / max -12.7 dB。bridge 3519044・encoder 3520687維持。視聴側の聴感は未確認。起動側で環境が欠けた経緯は未調査。



設定はVMユーザーのPulseAudioクライアント設定です。元の `autospawn = no` を保持し、以下を追加しました。

```ini
default-server = unix:/run/user/1001/pulse/native
```

ロールバックは日時付きclient.conf.backupから復元します。ゲームや配信の再起動は不要でした。
