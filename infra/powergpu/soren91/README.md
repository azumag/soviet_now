# Soren91 PowerGPU Tier 0 PoC

Issue #23 の Soren91 再稼働に向け、通常の SorenGame と本配信は現行 OCI A1 のまま維持し、Soren91 の描画だけを一時 GPU worker へ逃がすための最安 Tier 0 PoC です。

既存の `infra/vast/soren91/` の GPU runner image は provider 非依存のため、そのまま PowerGPU でも再利用します。PoC 時点では本配信・SRT受信・日次スケジュールには接続せず、実 GPU 上で WebGL/NVENC と描画性能だけを検証します。

## Tier 0

2026-09-12 時点の PowerGPU 公開価格では Tesla P4 8GB は on-demand `$0.018/GPU-h`、interruptible `$0.009/GPU-h`。PowerGPU は秒課金、storage `$0.08/GB-month`、bandwidth `$0.01/GB` を公開している。

最初は次の固定条件で試します。

- provider: PowerGPU
- GPU: `tesla-p4`
- type: `interruptible`
- GPU単価 hard cap: `$0.010/h`
- output: `960x540`
- 成功条件: 60秒平均 **30fps以上**
- NVIDIA hardware renderer 必須（SwiftShader / llvmpipe 不可）
- WebGL2 必須
- `h264_nvenc` 実エンコード成功必須
- GPU実行: 60〜300秒
- instance作成から destroy まで最大600秒
- disk: 20GB
- `--execute` を付けない限り課金インスタンスを作らない

公開価格が hard cap を超えた場合は、実行前に fail closed します。起動後に取得できる locked rate が hard cap を超えた場合も即 destroy します。

## dry-run

PowerGPU の公開 pricing API は認証なしで参照できます。

```bash
node tools/powergpu_soren91_session.mjs \
  --image ghcr.io/azumag/soren91-gpu-runner:<commit>
```

テストでは `--pricing-json` で fixture を渡せるため、外部通信や課金なしで価格上限・コスト計算・引数契約を検証できます。

## 実GPU PoC

PowerGPU CLI を導入し、API key は `POWERGPU_API_KEY` または CLI のユーザー設定領域にだけ置きます。API key をリポジトリ、Docker image、argv、ログへ入れません。

```bash
pip install powergpu
export POWERGPU_API_KEY=pg_live_...  # secret storeから注入

node tools/powergpu_soren91_session.mjs \
  --image ghcr.io/azumag/soren91-gpu-runner:<commit> \
  --execute
```

起動後は別プロセスの cleanup watchdog も開始し、親 controller が異常終了しても hard deadline 到達後に destroy を再試行します。

## 昇格ルール

Tier 0 が失敗した場合、失敗理由を分けます。

1. `interruptible` の interruption / capacity 問題だけなら、同じ Tesla P4 の on-demand（hard cap `$0.020/h`）で再試験。
2. Xorg / NVIDIA graphics exposure / host固有問題なら、GPU性能不足とは判定せず別host/providerを試す。
3. NVIDIA hardware renderer が確認できた状態で30fps未満なら、初めて上位GPUへ昇格する。
4. 次候補は、その時点の総額が安い順に Salad / Vast / PowerGPU 上位GPU等を比較して選ぶ。provider名やGPU名を固定の昇格条件にはしない。

通常の SorenGame はこのPoCの対象外で、現行 OCI A1 上の約30fps運用を維持します。

## 参考

- PowerGPU pricing/API: https://powergpu.io/api
- PowerGPU instance lifecycle: https://powergpu.io/docs/instances
- PowerGPU custom Docker image: https://powergpu.io/docs/templates
- PowerGPU networking/bandwidth: https://powergpu.io/docs/networking
