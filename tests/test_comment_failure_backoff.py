import subprocess
import tempfile
import time
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
COMMENT_SCRIPT = REPO_ROOT / "broadcast" / "comment.sh"
CONFIG_SCRIPT = REPO_ROOT / "core" / "config.sh"


class CommentFailureBackoffTests(unittest.TestCase):
    def run_bash(self, script: str, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["bash", "-c", script, "bash", *args],
            text=True,
            capture_output=True,
            check=False,
        )

    def test_backoff_marker_round_trip_and_expiry(self):
        with tempfile.TemporaryDirectory() as tmp:
            marker = Path(tmp) / "state" / "comment_backoff"
            result = self.run_bash(
                r'''
set -e
source "$1"
COMMENT_FAILURE_BACKOFF_FILE="$2"
_comment_failure_backoff_set 120
remaining=$(_comment_failure_backoff_remaining)
[ "$remaining" -ge 119 ]
[ "$remaining" -le 120 ]
printf '1\n' >"$COMMENT_FAILURE_BACKOFF_FILE"
[ "$(_comment_failure_backoff_remaining)" = 0 ]
[ ! -e "$COMMENT_FAILURE_BACKOFF_FILE" ]
''',
                str(COMMENT_SCRIPT),
                str(marker),
            )
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_active_backoff_keeps_pending_without_entering_generation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            chat = root / "twitch_chat.sh"
            chat.write_text(
                "#!/bin/bash\n"
                "mkdir -p tmp\n"
                "printf '%s\\n' 'viewer: hello' >tmp/twitch_comments.txt\n",
                encoding="utf-8",
            )
            chat.chmod(0o755)
            marker = root / "state" / "comment_backoff"
            marker.parent.mkdir(parents=True)
            marker.write_text(f"{int(time.time()) + 600}\n", encoding="utf-8")
            sentinel = root / "generation_started"

            result = self.run_bash(
                r'''
set -e
repo="$1"
root="$2"
cd "$root"
source "$repo/broadcast/comment.sh"
COMMENT_FAILURE_BACKOFF_FILE="$root/state/comment_backoff"
COMMENT_GEN_STATE_FILE="$root/state/comment_gen"
COMMENT_BATCH_INFLIGHT_FILE="$root/state/inflight"
_kill_comment_gen() { :; }
_comment_has_manual_claude_trigger() { return 1; }
_filter_already_processed_comment_lines() {
    touch "$root/generation_started"
    cat
}
generate_comment_response twitch
''',
                str(REPO_ROOT),
                str(root),
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse(sentinel.exists())
            self.assertEqual(
                (root / "tmp" / "twitch_comments.txt").read_text(encoding="utf-8"),
                "viewer: hello\n",
            )

    def test_failure_and_success_paths_manage_backoff(self):
        comment_source = COMMENT_SCRIPT.read_text(encoding="utf-8")
        config_source = CONFIG_SCRIPT.read_text(encoding="utf-8")
        self.assertIn(
            'COMMENT_FAILURE_BACKOFF_SEC="${COMMENT_FAILURE_BACKOFF_SEC:-${AI_AGENT_BACKOFF_SEC:-600}}"',
            config_source,
        )
        self.assertIn('_comment_failure_backoff_set "$comment_failure_backoff_sec"', comment_source)
        self.assertIn("_comment_failure_backoff_clear", comment_source)
        self.assertIn("pending維持・${comment_failure_backoff_sec}秒後に再試行", comment_source)


if __name__ == "__main__":
    unittest.main()
