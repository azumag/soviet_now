# Codex Project Instructions

## OBS Working Indicator

When Codex itself is actively inspecting, editing, testing, or otherwise changing this project, turn on the persistent Codex work indicator in `eventOverlay`:

```bash
./codex_work_indicator.sh start
```

Keep it visible until the Codex work is fully finished, including verification and any live restart/check steps. Before sending the final response, pausing the work, or handing control back to the user, clear it:

```bash
./codex_work_indicator.sh stop
```

This indicator is for human/Codex project work, not for the automatic in-game strategy improvement loop. It should update the `eventOverlay` HTML only; do not show/hide the OBS `systemMsg` source for Codex work.

## Viewer Feedback

Before changing this project, check `data/codex_advice.md` if it exists and contains anything other than blank lines or `（なし）`. It contains viewer comments about Codex operation, improvement-loop behavior, monitoring, workers, dashboards/status displays, OBS overlays, classification, and other system mechanisms. Treat it as high-priority input for the next Codex work loop, but verify against runtime evidence before making risky changes.

## VM 反映とリポジトリ同期

本プロジェクトでは本番 VM（`/home/ubuntu/soren`）とこのリポジトリを常に同期する。

- VM へ変更を反映する際は、**同じ変更を同時にこのリポジトリへコミット・push する**。コミット・push が済むまで「VM 反映済み」と報告しない。
- VM は git 管理外（直接編集 + バックアップ運用）のため、リポジトリが唯一の変更履歴。VM にだけ存在する変更を作業途中で放置しない。
- リポジトリと VM でファイルが乖離している場合、どちらが新しいかを確認し、新しい側へ同期してから作業を進める（例: `strategy/ai.sh` は VM が codex ハーネス版で新しい）。
- 調査用の一時ファイル・音声・worktree はリポジトリに含めず、VM の `tmp/` や別の調査ディレクトリに置く。

## GitHub とサンドボックス

サンドボックス内では `gh`（`pr view` / `pr create` / `pr diff` / `issue view` 等）はネットワーク（`api.github.com`）へ到達できないため、必ず昇格（`require_escalated`）して実行する。プライベートリモート（`azumag/soviet_now` 等）への `git push` などの外部送信も昇格が必要で、承認ゲートの対象になる。サンドボックスのままで実行せず、昇格して対象・内容を明確に示すこと。
