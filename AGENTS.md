# Codex Project Instructions

## OBS Working Indicator

When any agent (Codex, Claude Code, etc.) is actively inspecting, editing, testing, or otherwise changing this project, turn on the persistent work indicator in `eventOverlay` with fine granularity (regardless of agent type):

```bash
./codex_work_indicator.sh start "タイトル" "本文"
# 例: 解析中 → 実装中 → 検証中 → デプロイ中 とフェーズが変わるたびに再実行してタイトル/本文を更新
./codex_work_indicator.sh start "実装中" "core/config.sh の peak 改善チェーンを追加"
```

Keep it visible until the Codex work is fully finished, including verification and any live restart/check steps. Do not leave it on coarsely with a single `start`; update the title/body at each phase change so viewers can follow progress. Before sending the final response, pausing the work, or handing control back to the user, clear it:

```bash
./codex_work_indicator.sh stop
```

This indicator is for any agent's project work (regardless of Codex/Claude/etc.), not for the automatic in-game strategy improvement loop. It should update the `eventOverlay` HTML only; do not show/hide the OBS `systemMsg` source for this work. Any agent doing project work must show the overlay. The same state can also be controlled via docich webui `Overlay→作業中バナー` (`PUT /api/overlay/work_banner`).

It also enqueues the same work to the VM's audio queue. `codex_work_indicator.sh` calls `lib/outbound_queue.sh:enqueue_audio_text "$generated_text" work_indicator` on `start`/`stop`. The spoken text is **not a fixed template** — `_enqueue_work_audio` picks a natural phrasing (e.g. `現在、...の作業を進めています` / `...に取りかかっています` / `...の作業に着手しました` for start, `...の作業が完了しました。ご確認ください。` for stop) chosen by a content-hash + time seed, so the wording differs each time. Spam is suppressed by the `tmp/state/work_audio_last.json` guard in `codex_work_indicator.sh` (same title within 300s, or a contained/minor update within 180s, is skipped; `stop` always reads). On the VM it writes to `/home/ubuntu/soren/tmp/.comment_queue`, on a local checkout it also SSHs to `ubuntu@129.146.54.105` to enqueue on the VM. The `audio_worker` on the VM then speaks it. Manual enqueue: `ssh -i ~/.ssh/id_rsa ubuntu@129.146.54.105 "cd /home/ubuntu/soren && source lib/outbound_queue.sh && enqueue_audio_text \"現在、...の作業を進めています...\" work_indicator"`. Keep the spoken text in a plain polite `です・ます` style, concise and not overly deferential (avoid set phrases like `お待たせしております`, `何卒よろしくお願い申し上げます`, `でございます`).

## Viewer Feedback

Before changing this project, check `data/codex_advice.md` if it exists and contains anything other than blank lines or `（なし）`. It contains viewer comments about Codex operation, improvement-loop behavior, monitoring, workers, dashboards/status displays, OBS overlays, classification, and other system mechanisms. Treat it as high-priority input for the next Codex work loop, but verify against runtime evidence before making risky changes.

## VM 反映とリポジトリ同期

本プロジェクトでは本番 VM（`/home/ubuntu/soren`）とこのリポジトリを常に同期する。

- VM へ変更を反映する際は、**同じ変更を同時にこのリポジトリへコミット・push する**。コミット・push が済むまで「VM 反映済み」と報告しない。
- VM は git 管理外（直接編集 + バックアップ運用）のため、リポジトリが唯一の変更履歴。VM にだけ存在する変更を作業途中で放置しない。
- リポジトリと VM でファイルが乖離している場合、どちらが新しいかを確認し、新しい側へ同期してから作業を進める（例: `strategy/ai.sh` は VM が codex ハーネス版で新しい）。
- 調査用の一時ファイル・音声・worktree はリポジトリに含めず、VM の `tmp/` や別の調査ディレクトリに置く。

## config.sh 既定値の変更は worker 完全再起動で反映する

worker の reload（USR1/HUP）は `.env` とモジュールを再 source するが、`config.sh` の
`VAR="${VAR:-default}"` 形式の既定値は**シェル環境に既に設定済みの値があれば上書きしない**。
そのため **config.sh の既定値を変更した場合は必ず worker を完全再起動する**（reload では
反映されない）。

- 対象: `radio_worker` / `chat_worker` / `improve_daemon`（supervisor `start_all.sh` が
  自動 respawn する）。再起動は `kill -TERM <pid>` → supervisor の新プロセス（PID・起動時刻）
  を確認する。
- 実例（2026-08-20）: `RADIO_PREPASS_AGENTS` の既定値を共通チェーンへ変更した後、USR1 reload
  では旧リスト（amd/local 欠落）が残り、prepass が amd/local をスキップして
  deepseek-v4-flash に直行した。完全再起動で解消。
- 反映確認はログの実測で行う（例: `logs/radio_worker.log` の `prepass agents=` に新チェーンが
  出ること、`prepass provider=` が amd 等を獲得すること）。
- `.env` に明示設定されている値は reload でも更新されるため、この制約の対象外。

## GitHub とサンドボックス

サンドボックス内では `gh`（`pr view` / `pr create` / `pr diff` / `issue view` 等）はネットワーク（`api.github.com`）へ到達できないため、必ず昇格（`require_escalated`）して実行する。プライベートリモート（`azumag/soviet_now` 等）への `git push` などの外部送信も昇格が必要で、承認ゲートの対象になる。サンドボックスのままで実行せず、昇格して対象・内容を明確に示すこと。
