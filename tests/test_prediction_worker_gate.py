import json
import os
import re
import shlex
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKER = ROOT / "workers" / "prediction_worker.sh"
PREDICTION_SCRIPT = ROOT / "twitch_predictions.sh"


def _extract_function(source: str, name: str) -> str:
    match = re.search(
        rf"(?ms)^{re.escape(name)}\(\) \{{.*?^\}}",
        source,
    )
    if not match:
        raise AssertionError(f"function not found: {name}")
    return match.group(0)


class PredictionWorkerGateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = WORKER.read_text(encoding="utf-8")
        cls.functions = "\n\n".join(
            _extract_function(cls.source, name)
            for name in ("_prediction_ab_test_running", "_prediction_cycle_start_allowed")
        )
        cls.prediction_source = PREDICTION_SCRIPT.read_text(encoding="utf-8")
        cls.create_context_function = _extract_function(
            cls.prediction_source, "_prediction_create_context_allowed"
        )

    def run_gate(
        self,
        *,
        games=0,
        threshold="48",
        ab_state=False,
        ab_toggle="",
        improve_status="idle",
        improve_lock=False,
        hot_streak=False,
    ):
        with tempfile.TemporaryDirectory() as directory:
            state_dir = Path(directory)
            if ab_state:
                (state_dir / "ab_state.json").write_text("{}", encoding="utf-8")
            if improve_lock:
                (state_dir / "improve.lock").touch()
            if hot_streak:
                (state_dir / "hot_streak_prediction_pending").touch()
            script = f"""
{self.functions}
TMP_STATE_DIR={shlex.quote(str(state_dir))}
AB_STATE_FILE="$TMP_STATE_DIR/ab_state.json"
MIN_GAMES_BEFORE_IMPROVE={shlex.quote(str(threshold))}
current_acc_count={shlex.quote(str(games))}
improve_status={shlex.quote(improve_status)}
IMPROVE_LOCK_FILE="$TMP_STATE_DIR/improve.lock"
HOT_STREAK_PREDICTION_PENDING_FILE="$TMP_STATE_DIR/hot_streak_prediction_pending"
SOREN_AB_ALT_STRATEGY={shlex.quote(ab_toggle)}
if _prediction_cycle_start_allowed; then
    printf 'allowed\\n'
else
    printf 'blocked\\n'
fi
"""
            result = subprocess.run(
                ["bash", "-c", script],
                check=True,
                capture_output=True,
                text=True,
            )
            return result.stdout.strip()

    def run_create_context(
        self,
        *,
        count=None,
        threshold="48",
        max_games="48",
        ab_state=False,
        ab_toggle="",
        improve_status="idle",
        improve_lock=False,
        hot_streak=False,
    ):
        with tempfile.TemporaryDirectory() as directory:
            state_dir = Path(directory)
            if count is not None:
                (state_dir / "accumulated_games.json").write_text(
                    f'{{"count": {count}}}', encoding="utf-8"
                )
            if ab_state:
                (state_dir / "ab_state.json").write_text("{}", encoding="utf-8")
            if improve_status:
                (state_dir / "improve_state.json").write_text(
                    json.dumps({"status": improve_status}), encoding="utf-8"
                )
            if improve_lock:
                (state_dir / "improve.lock").touch()
            if hot_streak:
                (state_dir / "hot_streak_prediction_pending").touch()
            script = f"""
_log() {{ :; }}
{self.create_context_function}
TMP_STATE_DIR={shlex.quote(str(state_dir))}
_cfg_min_games={shlex.quote(str(threshold))}
PREDICTION_MAX_GAMES={shlex.quote(str(max_games))}
IMPROVE_STATE_FILE="$TMP_STATE_DIR/improve_state.json"
IMPROVE_LOCK_FILE="$TMP_STATE_DIR/improve.lock"
HOT_STREAK_PREDICTION_PENDING_FILE="$TMP_STATE_DIR/hot_streak_prediction_pending"
AB_STATE_FILE="$TMP_STATE_DIR/ab_state.json"
SOREN_AB_ALT_STRATEGY={shlex.quote(ab_toggle)}
if _prediction_create_context_allowed; then
    printf 'allowed\\n'
else
    printf 'blocked\\n'
fi
"""
            result = subprocess.run(
                ["bash", "-c", script],
                check=True,
                capture_output=True,
                text=True,
            )
            return result.stdout.strip()

    def run_create_wrapper(
        self,
        *,
        improve_status="",
        improve_lock=False,
        hot_streak=False,
    ):
        with tempfile.TemporaryDirectory() as directory:
            state_dir = Path(directory) / "state"
            state_dir.mkdir()
            fake_bin = Path(directory) / "bin"
            fake_bin.mkdir()
            curl_log = Path(directory) / "curl.log"
            (fake_bin / "curl").write_text(
                '#!/bin/sh\nprintf \'%s\\n\' called >> "$CURL_LOG"\nexit 99\n',
                encoding="utf-8",
            )
            (fake_bin / "curl").chmod(0o755)
            (state_dir / "accumulated_games.json").write_text(
                '{"count": 0}', encoding="utf-8"
            )
            if improve_status:
                (state_dir / "improve_state.json").write_text(
                    json.dumps({"status": improve_status}), encoding="utf-8"
                )
            if improve_lock:
                (state_dir / "improve.lock").touch()
            if hot_streak:
                (state_dir / "hot_streak_prediction_pending").touch()
            env = dict(os.environ)
            env.update(
                {
                    "PATH": f"{fake_bin}:{env.get('PATH', '')}",
                    "CURL_LOG": str(curl_log),
                    "TWITCH_PREDICTIONS_ENABLED": "1",
                    "TWITCH_PREDICTIONS_TOKEN": "test-token",
                    "TWITCH_CLIENT_ID": "test-client",
                    "TWITCH_BROADCASTER_ID": "test-broadcaster",
                    "MIN_GAMES_BEFORE_IMPROVE": "48",
                    "TWITCH_PREDICTION_MAX_GAMES": "48",
                    "TMP_STATE_DIR": str(state_dir),
                    "ACCUMULATED_GAMES_FILE": str(
                        state_dir / "accumulated_games.json"
                    ),
                    "IMPROVE_STATE_FILE": str(state_dir / "improve_state.json"),
                    "IMPROVE_LOCK_FILE": str(state_dir / "improve.lock"),
                    "HOT_STREAK_PREDICTION_PENDING_FILE": str(
                        state_dir / "hot_streak_prediction_pending"
                    ),
                    "AB_STATE_FILE": str(state_dir / "ab_state.json"),
                    "SOREN_AB_ALT_STRATEGY": "",
                }
            )
            result = subprocess.run(
                ["bash", str(PREDICTION_SCRIPT), "create", "123"],
                cwd=ROOT,
                env=env,
                check=True,
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.returncode, 0)
            return curl_log.read_text(encoding="utf-8") if curl_log.exists() else ""

    def test_creation_is_allowed_only_for_clean_48_game_cycle(self):
        self.assertEqual(self.run_gate(), "allowed")
        self.assertEqual(self.run_gate(threshold="12"), "blocked")
        self.assertEqual(self.run_gate(games=1), "blocked")

    def test_creation_is_blocked_while_ab_test_is_present(self):
        self.assertEqual(self.run_gate(ab_state=True), "blocked")
        self.assertEqual(self.run_gate(ab_toggle="tmp/state/ab_alt_strategy.py"), "blocked")

    def test_existing_improvement_guards_remain_in_force(self):
        self.assertEqual(self.run_gate(improve_status="running"), "blocked")
        self.assertEqual(self.run_gate(improve_lock=True), "blocked")
        self.assertEqual(self.run_gate(hot_streak=True), "blocked")

    def test_wrapper_repeats_the_start_context_guard(self):
        self.assertEqual(self.run_create_context(count=0), "allowed")
        self.assertEqual(self.run_create_context(count=1), "blocked")
        self.assertEqual(self.run_create_context(threshold="12"), "blocked")
        self.assertEqual(self.run_create_context(max_games="12"), "blocked")
        self.assertEqual(self.run_create_context(ab_state=True), "blocked")
        self.assertEqual(
            self.run_create_context(ab_toggle="tmp/state/ab_alt_strategy.py"),
            "blocked",
        )
        self.assertEqual(self.run_create_context(improve_status="running"), "blocked")
        self.assertEqual(self.run_create_context(improve_lock=True), "blocked")
        self.assertEqual(self.run_create_context(hot_streak=True), "blocked")
        self.assertIn("if ! _prediction_create_context_allowed; then", self.prediction_source)

    def test_wrapper_blocks_ownership_states_before_any_curl(self):
        for kwargs in (
            {"improve_status": "running"},
            {"improve_lock": True},
            {"hot_streak": True},
        ):
            with self.subTest(**kwargs):
                self.assertEqual(self.run_create_wrapper(**kwargs), "")

    def test_main_loop_uses_the_shared_gate(self):
        self.assertIn("_prediction_cycle_start_allowed", self.source)
        self.assertIn("_prediction_ab_test_running", self.source)
        self.assertIn("MIN_GAMES_BEFORE_IMPROVE", self.source)


if __name__ == "__main__":
    unittest.main()
