# Soren91 Vast.ai GPU PoC

Issue #23 の再開可否だけを判定する、最大5分の隔離GPU描画試験です。本配信、通常Soren、
SRT受信、日次スケジュールには接続しません。

## 固定された安全上限

- GPU実行: 60〜300秒（既定300秒）
- インスタンス作成からdestroy: 最大600秒
- on-demandのみ
- GPU単価: 最大 `$0.02/h`
- 送信単価: 最大 `$0.02/GB`
- イメージ取得回線: 100Mbps以上
- 出力: 960x540
- `--execute` を付けない限りインスタンスを作成しない

成功条件は、NVIDIA hardware renderer、WebGL2、960x540 drawing buffer、60秒平均25fps以上、
`h264_nvenc` の実エンコード成功です。

## イメージ

```bash
docker build -f infra/vast/soren91/Dockerfile -t ghcr.io/azumag/soren91-gpu-runner:<commit> .
```

専用GitHub Actionsは `codex/issue-23-vast-poc` へのpush時にlinux/amd64イメージをGHCRへ公開します。
Actionは個人トークンを使わず、短命な `GITHUB_TOKEN` の `packages: write` だけを使用します。

Vast APIキー、配信キー、OAuth tokenなどはイメージやリポジトリへ入れません。

## コントローラ

候補検索と費用表示だけを行う既定dry-run:

```bash
node tools/vast_soren91_session.mjs \
  --image ghcr.io/azumag/soren91-gpu-runner:<commit>
```

実行時だけ明示的に `--execute` を追加します。作成後は親プロセスとは別のcleanup watchdogも起動し、
親が異常終了しても作成から600秒でdestroyを再試行します。

```bash
node tools/vast_soren91_session.mjs \
  --image ghcr.io/azumag/soren91-gpu-runner:<commit> \
  --execute
```

事前に公式 `vastai` CLIをインストールし、利用者自身の設定領域へAPIキーを設定する必要があります。
キーの値をコマンド引数、`.env`、ログ、handoffへ記録しないでください。
