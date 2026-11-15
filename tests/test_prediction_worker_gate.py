import json
import os
import re
import shlex
import subprocess
import tempfile
import time
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
            for name in ("_prediction_notice_mode", "_prediction_corner_busy",
                         "_prediction_ab_test_running",
                         "_prediction_cycle_start_allowed")
        )
        cls.prediction_source = PREDICTION_SCRIPT.read_text(encoding="utf-8")
        cls.create_context_function = "\n\n".join(
            _extract_function(cls.prediction_source, name)
            for name in ("_prediction_notice_mode", "_prediction_corner_busy",
                         "_prediction_create_context_allowed")
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
        notice=None,
        game_switch=None,
        rotation=None,
    ):
        with tempfile.TemporaryDirectory() as directory:
            state_dir = Path(directory)
            (state_dir / "lib").mkdir()
            (state_dir / "lib" / "corner_notice_state.py").write_text(
                (ROOT / "lib" / "corner_notice_state.py").read_text(encoding="utf-8"),
                encoding="utf-8",
            )
            docich_state = state_dir / "docich-state"
            docich_state.mkdir()
            if notice is not None:
                (docich_state / "next_corner.json").write_text(
                    json.dumps(notice), encoding="utf-8"
                )
            if game_switch is not None:
                (docich_state / "game_switch.json").write_text(
                    json.dumps(game_switch), encoding="utf-8"
                )
            if rotation is not None:
                (docich_state / "corner_rotation.json").write_text(
                    json.dumps(rotation), encoding="utf-8"
                )
            if ab_state:
                (state_dir / "ab_state.json").write_text("{}", encoding="utf-8")
            if improve_lock:
                (state_dir / "improve.lock").touch()
            if hot_streak:
                (state_dir / "hot_streak_prediction_pending").touch()
            script = f"""
_log() {{ :; }}
_PREDICTION_NOTICE_LOGGED=""
{self.functions}
TMP_STATE_DIR={shlex.quote(str(state_dir))}
export DOCICH_STATE_DIR={shlex.quote(str(docich_state))}
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
                cwd=directory,
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
        notice=None,
        game_switch=None,
        rotation=None,
    ):
        with tempfile.TemporaryDirectory() as directory:
            state_dir = Path(directory)
            (state_dir / "lib").mkdir()
            (state_dir / "lib" / "corner_notice_state.py").write_text(
                (ROOT / "lib" / "corner_notice_state.py").read_text(encoding="utf-8"),
                encoding="utf-8",
            )
            docich_state = state_dir / "docich-state"
            docich_state.mkdir()
            if notice is not None:
                (docich_state / "next_corner.json").write_text(
                    json.dumps(notice), encoding="utf-8"
                )
            if game_switch is not None:
                (docich_state / "game_switch.json").write_text(
                    json.dumps(game_switch), encoding="utf-8"
                )
            if rotation is not None:
                (docich_state / "corner_rotation.json").write_text(
                    json.dumps(rotation), encoding="utf-8"
                )
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
export DOCICH_STATE_DIR={shlex.quote(str(docich_state))}
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
                cwd=directory,
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

    def rotation(self, *, pending=False, manual=False):
        slot = {"corner": "paper", "phase": "selected", "request_id": "r1"}
        return {
            "schema_version": 1,
            "pending": slot if pending else None,
            "manual_pending": slot if manual else None,
        }

    def switch(self, game="sorengame", phase="ready"):
        return {
            "schema_version": 2,
            "phase": phase,
            "active": {"game": game,
                       "adapter": "soren" if game == "sorengame" else game.split("-")[0]},
        }

    def notice(self, hold, **extra):
        now = int(time.time())
        payload = {
            "schema": 1, "corner": "ninvaders", "label": "スペースインベーダー",
            "when": "18:00", "due_at": now + 600, "source": "auto",
            "hold": hold, "remaining_games": None,
            "decided_at": now, "updated_at": now,
        }
        payload.update(extra)
        return payload

    def test_clock_notice_blocks_a_new_prediction(self):
        self.assertEqual(self.run_gate(notice=self.notice(None)), "blocked")

    def test_pending_notice_allows_a_prediction_even_mid_batch(self):
        pending = self.notice("prediction-pending")
        self.assertEqual(
            self.run_gate(games=10, improve_status="running", improve_lock=True,
                          notice=pending),
            "allowed",
        )

    def test_pending_never_bypasses_ab_or_hot_streak(self):
        pending = self.notice("prediction-pending")
        self.assertEqual(self.run_gate(ab_state=True, notice=pending), "blocked")
        self.assertEqual(self.run_gate(hot_streak=True, notice=pending), "blocked")

    def test_prediction_hold_blocks_a_new_prediction(self):
        self.assertEqual(
            self.run_gate(notice=self.notice("prediction", remaining_games=48)),
            "blocked",
        )

    def test_unknown_hold_blocks_a_new_prediction(self):
        self.assertEqual(self.run_gate(notice=self.notice("weather")), "blocked")

    def test_running_corner_blocks_a_new_prediction(self):
        self.assertEqual(
            self.run_gate(game_switch=self.switch("paper-view")), "blocked"
        )

    def test_corner_blocks_even_during_the_pending_handoff(self):
        self.assertEqual(
            self.run_gate(notice=self.notice("prediction-pending"),
                          game_switch=self.switch("paper-view")),
            "blocked",
        )

    def test_confirmed_slot_blocks_a_new_prediction_before_the_switch(self):
        # The rotation clears next_corner.json and sets pending, then waits for
        # the game boundary/audio before game_switch changes.
        self.assertEqual(
            self.run_gate(rotation=self.rotation(pending=True)), "blocked"
        )
        self.assertEqual(
            self.run_gate(rotation=self.rotation(pending=True),
                          game_switch=self.switch()),
            "blocked",
        )
        self.assertEqual(
            self.run_gate(rotation=self.rotation(manual=True),
                          game_switch=self.switch()),
            "blocked",
        )
        self.assertEqual(
            self.run_gate(rotation=self.rotation(), game_switch=self.switch()),
            "allowed",
        )

    def test_confirmed_slot_wins_over_the_pending_handoff(self):
        self.assertEqual(
            self.run_gate(notice=self.notice("prediction-pending"),
                          rotation=self.rotation(pending=True),
                          game_switch=self.switch()),
            "blocked",
        )

    def test_switch_transition_or_failure_blocks_a_new_prediction(self):
        for phase in ("draining", "stopping", "failed", "recovery_required"):
            with self.subTest(phase=phase):
                self.assertEqual(
                    self.run_gate(game_switch=self.switch(phase=phase)), "blocked"
                )

    def test_soren_on_air_keeps_the_existing_gate(self):
        self.assertEqual(self.run_gate(game_switch=self.switch()), "allowed")
        self.assertEqual(self.run_gate(games=10, game_switch=self.switch()), "blocked")

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

    def test_create_context_honours_the_clock_notice(self):
        now = int(time.time())
        notice = {
            "schema": 1, "corner": "ninvaders", "label": "スペースインベーダー",
            "when": "18:00", "due_at": now + 600, "source": "auto",
            "hold": None, "remaining_games": None,
            "decided_at": now, "updated_at": now,
        }
        self.assertEqual(self.run_create_context(count=0, notice=notice), "blocked")

    def test_create_context_blocks_while_a_corner_is_on_air(self):
        self.assertEqual(
            self.run_create_context(count=0, game_switch=self.switch("paper-view")),
            "blocked",
        )
        self.assertEqual(
            self.run_create_context(
                count=10, improve_status="running", improve_lock=True,
                notice=self.notice("prediction-pending"),
                game_switch=self.switch("paper-view"),
            ),
            "blocked",
        )
        self.assertEqual(
            self.run_create_context(count=0, game_switch=self.switch()), "allowed"
        )

    def test_create_context_blocks_while_a_slot_is_confirmed(self):
        self.assertEqual(
            self.run_create_context(count=0, rotation=self.rotation(pending=True),
                                    game_switch=self.switch()),
            "blocked",
        )
        self.assertEqual(
            self.run_create_context(count=0, rotation=self.rotation(),
                                    game_switch=self.switch()),
            "allowed",
        )

    def test_create_context_pending_bypasses_acc_and_improvement_only(self):
        now = int(time.time())
        notice = {
            "schema": 1, "corner": "ninvaders", "label": "スペースインベーダー",
            "when": "18:00", "due_at": now + 600, "source": "auto",
            "hold": "prediction-pending", "remaining_games": None,
            "decided_at": now, "updated_at": now,
        }
        self.assertEqual(
            self.run_create_context(count=10, improve_status="running",
                                    improve_lock=True, notice=notice),
            "allowed",
        )
        self.assertEqual(self.run_create_context(ab_state=True, notice=notice), "blocked")
        self.assertEqual(self.run_create_context(hot_streak=True, notice=notice), "blocked")

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



class RemotePredictionOwnershipTests(unittest.TestCase):
    def recover(self, rows):
        import sys
        source = _extract_function(PREDICTION_SCRIPT.read_text(), "_recover_remote_active_prediction")
        marker = 'prediction_id=$(python3 - "$remote_json" <<' + "'PY' 2>/dev/null\n"
        program = source.split(marker, 1)[1].split("\nPY", 1)[0]
        result = subprocess.run([sys.executable, "-c", program, json.dumps({"data": rows})],
                                capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout) if result.stdout.strip() else None

    def own(self, status="ACTIVE"):
        labels = ["建国なし", "ロシア建国(ソ連不成立)", "ソ連建国", "粛清"]
        return {"id": "soren", "title": "次の48試合で建国できる？", "status": status,
                "outcomes": [{"id": str(i), "title": label} for i, label in enumerate(labels)]}

    def test_recovers_only_soren_active_and_locked(self):
        for status in ("ACTIVE", "LOCKED"):
            with self.subTest(status=status):
                result = self.recover([self.own(status)])
                self.assertEqual(result["prediction_id"], "soren")
                self.assertEqual(result["outcome_ids"], ["0", "1", "2", "3"])
                self.assertTrue(result["recovered"])

    def test_hanjuku_and_unrelated_predictions_are_not_adopted(self):
        for status in ("ACTIVE", "LOCKED"):
            row = {"id": "hanjuku", "title": "半熟英雄：どこまでゲーム到達できるか？ #abcdef123456",
                   "status": status, "outcomes": [{"id": str(i), "title": label} for i, label in
                   enumerate(["2話突破（新記録）", "1話突破", "1話突破できず"])]}
            self.assertIsNone(self.recover([row]))
            row = self.own(status)
            row["title"] = "別の番組の予想"
            self.assertIsNone(self.recover([row]))

    def test_rejects_wrong_order_missing_duplicate_and_nonstring_ids(self):
        cases = []
        row = self.own(); row["outcomes"].reverse(); cases.append(row)
        row = self.own(); row["outcomes"].pop(); cases.append(row)
        row = self.own(); row["outcomes"][1]["id"] = "0"; cases.append(row)
        row = self.own(); row["outcomes"][0]["id"] = 0; cases.append(row)
        row = self.own(); row["id"] = True; cases.append(row)
        row = self.own(); row["title"] = "次の0試合で建国できる？"; cases.append(row)
        row = self.own(); row["outcomes"] = {}; cases.append(row)
        for row in cases:
            with self.subTest(row=row):
                self.assertIsNone(self.recover([row]))

    def test_legacy_twelve_game_round_and_ended_remote(self):
        row = self.own(); row["title"] = "次の12試合で建国できる？"
        self.assertEqual(self.recover([row])["prediction_id"], "soren")
        for status in ("RESOLVED", "CANCELED"):
            self.assertIsNone(self.recover([self.own(status)]))

    def test_skips_unrelated_entry_without_claiming_it(self):
        other = self.own(); other["title"] = "半熟英雄"
        self.assertEqual(self.recover([None, other, self.own()])["prediction_id"], "soren")

if __name__ == "__main__":
    unittest.main()
