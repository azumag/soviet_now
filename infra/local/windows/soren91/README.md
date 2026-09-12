# Soren91 local Windows Tier -1 renderer

通常の SorenGame / docich / 本配信は OCI A1 に残し、Soren91 の描画だけを手元の Windows + NVIDIA GPU に逃がす PoC。クラウド GPU を借りる前の **Tier -1** とする。ローカルが offline / busy / probe fail の場合だけ PowerGPU Tesla P4 interruptible (Tier 0) へフォールバックする。

## 目標契約

- Windows + NVIDIA GPU（初回対象: GeForce RTX 3060）
- Unity WebGL renderer が NVIDIA hardware renderer であること
- WebGL2 / 960x540 / 60秒平均 30fps 以上
- `h264_nvenc`
- SRT 960x540@30 / 2Mbps を Tailscale 経由で OCI に送る
- 本運用は **1日30分**。開始準備・終了処理込みの hard cap は40分
- 通常 SorenGame / 本配信はローカル renderer の準備中も停止しない
- ローカルが使えない場合は配信を壊さず Tier 0 PowerGPU へ移る

## セキュリティ境界

SRT は public Internet に直接出さず、Windows と OCI の Tailscale アドレス間だけで流す。SRT URL に passphrase を埋め込むと ffmpeg argv / process list に秘密値が露出するため、この PoC では **SRT passphrase を禁止**し Tailscale の暗号化・ACLを transport security とする。

制御 agent は既定で `127.0.0.1:19191` にしか bind しない。外部から操作するときは Tailscale Serve または Windows Firewall で Tailscale interface / OCI node のみに限定する。`SOREN91_LOCAL_AGENT_TOKEN` は24文字以上を必須とし、repo・argvへ保存しない。

重要: headful Chromium を使うため、agent は Windows Service の Session 0 ではなく **ログイン済みユーザーの対話セッション**で動かす。Task Scheduler を使う場合は「ユーザーがログオンしているときのみ実行」にする。

## 1. Windows prerequisites

- Node.js
- repo root で `npm install` 済み（Playwright）
- `npx playwright install chromium`
- NVIDIA driver / `nvidia-smi`
- FFmpeg (`gdigrab`, `h264_nvenc`, `srt` が入ったbuild)
- Tailscale

音声も送る場合だけ VB-CABLE 等の仮想音声デバイスを用意し、`SOREN91_LOCAL_AUDIO_DEVICE` に ffmpeg dshow のdevice名を指定する。PoCは映像だけでも開始できる。

## 2. OCI receiver を先に起動

OCI の Tailscale IPv4 を指定して listener を起動する。public interface では待ち受けない。

```bash
export SOREN91_OCI_TAILSCALE_IP=100.x.y.z
bash infra/local/windows/soren91/receive_oci.sh
```

## 3. Windows で dry-run

PowerShell:

```powershell
$env:SOREN91_LOCAL_SRT_URL = 'srt://100.x.y.z:19192?mode=caller&transtype=live&latency=200000'
node tools/soren91_windows_session.mjs
```

出力契約だけを表示し、Chrome/FFmpegは起動しない。

## 4. 5分PoC

最初は30分ではなく5分で確認する。

```powershell
$env:SOREN91_LOCAL_SRT_URL = 'srt://100.x.y.z:19192?mode=caller&transtype=live&latency=200000'
node tools/soren91_windows_session.mjs --session-sec 300 --execute
```

合格条件:

1. `nvidia-smi` / NVENC / gdigrab が利用可能
2. Soren91 の `UNMASKED_RENDERER_WEBGL` が NVIDIA で SwiftShader ではない
3. WebGL2 / drawing buffer 960x540
4. 60秒平均30fps以上
5. OCIで90秒連続受信できる
6. 出力 H.264 960x540 30fps

PoCが通った後だけ `SOREN91_LOCAL_SESSION_SEC=1800` を本運用値にする。

## 5. agent

Windowsの対話セッションで:

```powershell
$env:SOREN91_LOCAL_AGENT_TOKEN = '<secret-storeから注入>'
$env:SOREN91_LOCAL_SRT_URL = 'srt://100.x.y.z:19192?mode=caller&transtype=live&latency=200000'
node tools/soren91_local_agent.mjs
```

API:

- `GET /health` — 非認証、aliveのみ
- `GET /v1/status` — Bearer token必須
- `POST /v1/start` — Bearer token必須、1 sessionだけ
- `POST /v1/stop` — Bearer token必須

agentは同時sessionを許可しない。renderer側のhard cap 40分も別に持つため、OCI controllerが落ちても無期限実行しない。

## captureについて

PoCは Playwright bundled Chromium のwindow title `Soren91-Remote - Chromium` を `gdigrab` で取得する。最終運用で普段使い画面への影響が気になる場合は、仮想ディスプレイまたはdummy HDMIを追加し、そのdisplayへChromiumを固定する。RDPでGUI sessionを作る方式はGPU/desktop状態を変えやすいため採用しない。

## backend priority

```text
Tier -1  local Windows RTX 3060
  -> offline / busy / infra failure
Tier 0   PowerGPU Tesla P4 interruptible
  -> interruption / capacity only
Tier 0b  PowerGPU Tesla P4 on-demand
  -> hardware renderer confirmed but <30fps
next     current cheapest provider/GPU
```

GPU名よりも「hardware WebGL + 30fps + NVENC + session total cost」を採用条件にする。
