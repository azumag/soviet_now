# Soren91 Vast.ai GPU PoC (fallback / comparison)

Issue #23 の再開可否を判断する隔離GPU描画試験です。現在の最初の試行先は PowerGPU Tesla P4 Tier 0 へ変更し、Vast.ai は比較・fallback候補として残します。本配信、通常Soren、SRT受信、日次スケジュールにはまだ接続しません。

## 共通のGPU runner

`infra/vast/soren91/Dockerfile` は歴史的に Vast.ai 用として作成されましたが、内容は provider 非依存です。PowerGPU の custom Docker image としても同じ `ghcr.io/azumag/soren91-gpu-runner:<commit>` を利用します。

```bash
docker build -f infra/vast/soren91/Dockerfile -t ghcr.io/azumag/soren91-gpu-runner:<commit> .
```

GPU workerでは NVIDIA hardware WebGL、WebGL2、960x540 drawing buffer、`h264_nvenc` を検証します。新しい Tier 0 の合格基準は60秒平均30fps以上です。

## Vast controller

既存 Vast controller は比較用として維持します。候補検索と費用表示だけを行う既定dry-run:

```bash
node tools/vast_soren91_session.mjs \
  --image ghcr.io/azumag/soren91-gpu-runner:<commit>
```

実行時だけ明示的に `--execute` を追加します。Vast APIキーは利用者のCLI設定領域に置き、値をコマンド引数、`.env`、ログ、handoffへ記録しないでください。

PowerGPU Tier 0 の詳細は `infra/powergpu/soren91/README.md` を参照してください。
