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
