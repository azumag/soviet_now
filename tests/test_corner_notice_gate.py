import json
import os
import re
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
LOOP = REPO_ROOT / "soren_loop.sh"
IMPROVE = REPO_ROOT / "strategy" / "improve.sh"


def _extract_function(source: str, name: str) -> str:
    match = re.search(rf"(?ms)^{re.escape(name)}\(\) \{{.*?^\}}", source)
    if not match:
        raise AssertionError(f"function not found: {name}")
    return match.group(0)


class CornerNoticeImprovementGateTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.loop_source = LOOP.read_text(encoding="utf-8")
        cls.source = IMPROVE.read_text(encoding="utf-8")
        cls.function = _extract_function(cls.source, "_corner_notice_blocks_improvement")

    def run_gate(self, state_dir, *, helper=True):
        script = f"""
_log() {{ :; }}
log() {{ :; }}
log_pause_throttled() {{ :; }}
ELOOP_LIB_DIR={str(REPO_ROOT)!r}
{self.function}
if _corner_notice_blocks_improvement; then
    printf 'blocked\\n'
else
    printf 'allowed\\n'
fi
"""
        env = dict(os.environ)
        env.pop("DOCICH_STATE_DIR", None)
        if state_dir is not None:
            env["DOCICH_STATE_DIR"] = str(state_dir)
        result = subprocess.run(
            ["bash", "-c", script],
            env=env, capture_output=True, text=True, check=True, timeout=10,
        )
        return result.stdout.strip()

    def notice(self, hold, *, due_in=600):
        now = int(time.time())
        return {
            "schema": 1, "corner": "ninvaders", "label": "スペースインベーダー",
            "when": "18:00", "due_at": now + due_in, "source": "auto",
            "hold": hold, "remaining_games": None,
            "decided_at": now, "updated_at": now,
        }

    def test_future_clock_allows_improvement_and_imminent_or_hold_blocks(self):
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "next_corner.json").write_text(
                json.dumps(self.notice(None)), encoding="utf-8"
            )
            self.assertEqual(self.run_gate(tmp), "allowed")
        for hold in ("prediction-pending", "prediction", "weather"):
            with self.subTest(hold=hold), tempfile.TemporaryDirectory() as tmp:
                Path(tmp, "next_corner.json").write_text(
                    json.dumps(self.notice(hold)), encoding="utf-8"
                )
                self.assertEqual(self.run_gate(tmp), "blocked")
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "next_corner.json").write_text(
                json.dumps(self.notice(None, due_in=60)), encoding="utf-8"
            )
            self.assertEqual(self.run_gate(tmp), "blocked")

    def test_clear_or_stale_notice_keeps_the_existing_flow(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(self.run_gate(tmp), "allowed")
            Path(tmp, "next_corner.json").write_text(
                json.dumps(self.notice(None) | {"updated_at": int(time.time()) - 3600}),
                encoding="utf-8",
            )
            self.assertEqual(self.run_gate(tmp), "allowed")

    def test_missing_projection_is_fail_open(self):
        # No DOCICH_STATE_DIR and no sibling checkout means the helper sees no
        # promise; a broken projection must not stall the improvement loop.
        self.assertEqual(self.run_gate(None), "allowed")

    def test_three_improvement_paths_call_the_gate(self):
        self.assertIn("_corner_notice_blocks_improvement && return 1", self.loop_source)
        self.assertIn("! _is_improve_running &&\n\t\t\t\t! _corner_notice_blocks_improvement; then",
                      self.loop_source)
        self.assertIn("[ \"${_cycle_acc_count:-0}\" -ge \"$MIN_GAMES_BEFORE_IMPROVE\" ] &&\n\t\t\t! _corner_notice_blocks_improvement; then",
                      self.loop_source)

    def test_per_game_batch_accumulation_continues_before_the_gate(self):
        # #924: a game completed at the corner boundary is accumulated into its
        # batch; the notice gate only defers the early-lock queue. improve.sh is
        # re-sourced every loop, so this takes effect on the next match.
        start = self.source.index("record_completed_game_for_adaptive_improvement()")
        block = self.source[start:]
        gate = block.index("_corner_notice_blocks_improvement")
        first_accumulate = block.index("accumulate_game_data")
        self.assertLess(first_accumulate, gate)
        self.assertNotIn("改善バッチへの加算を保留", block)
        self.assertEqual(block.count('accumulate_game_data "$archive_file"'), 2)


if __name__ == "__main__":
    unittest.main()
