# Codex Project Instructions

## OBS Working Indicator

When Codex itself is actively inspecting, editing, testing, or otherwise changing this project, turn on the OBS source named `systemMsg` in the `soren` scene:

```bash
./obs_control.sh show soren systemMsg
```

Keep it visible until the Codex work is fully finished, including verification and any live restart/check steps. Before sending the final response, pausing the work, or handing control back to the user, turn it off:

```bash
./obs_control.sh hide soren systemMsg
```

This indicator is for human/Codex project work, not for the automatic in-game strategy improvement loop. If OBS is unavailable or the command fails, continue the requested work and mention the OBS failure in the response.
