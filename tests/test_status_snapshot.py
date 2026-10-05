import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "status_snapshot", ROOT / "lib" / "status_snapshot.py"
)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


STRATEGY = """
def helper(x):
    return x + 1

def decide(state):
    return helper(1)
"""


class StatusSnapshotTests(unittest.TestCase):
    def test_build_snapshot_matches_game_and_accumulated_state(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            game = root / "game.json"
            strategy = root / "strategy.py"
            accumulated = root / "acc.json"
            strategy.write_text(STRATEGY, encoding="utf-8")
            current_hash = MODULE.compute_hash(strategy)
            game.write_text(
                json.dumps({"state": "play", "score": 123, "pieces": [1, 2, 3]}),
                encoding="utf-8",
            )
            accumulated.write_text(
                json.dumps({
                    "hash": current_hash,
                    "count": 7,
                    "scores": "1,2,3",
                    "russia_count": 4,
                    "soviet": True,
                    "best_max_type": 15,
                }),
                encoding="utf-8",
            )
            value = MODULE.build_snapshot(game, strategy, accumulated)
            self.assertEqual(value["game_state"], "play")
            self.assertEqual(value["game_score"], 123)
            self.assertEqual(value["game_pieces"], 3)
            self.assertEqual(value["current_hash_for_acc"], current_hash)
            self.assertEqual(value["acc_count"], 7)
            self.assertEqual(value["acc_scores"], "1,2,3")
            self.assertEqual(value["acc_russia_count"], 4)
            self.assertEqual(value["acc_soviet"], "true")
            self.assertEqual(value["acc_max_type"], 15)

    def test_hash_mismatch_and_invalid_json_fail_to_defaults(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            game = root / "game.json"
            strategy = root / "strategy.py"
            accumulated = root / "acc.json"
            strategy.write_text(STRATEGY, encoding="utf-8")
            game.write_text("{broken", encoding="utf-8")
            accumulated.write_text(json.dumps({"hash": "different", "count": 99}), encoding="utf-8")
            value = MODULE.build_snapshot(game, strategy, accumulated)
            self.assertEqual(value["game_state"], "")
            self.assertEqual(value["game_score"], 0)
            self.assertEqual(value["game_pieces"], 0)
            self.assertEqual(value["acc_count"], 0)
            self.assertEqual(value["acc_scores"], "")
            self.assertEqual(value["acc_russia_count"], 0)
            self.assertEqual(value["acc_soviet"], "false")
            self.assertEqual(value["acc_max_type"], 0)

    def test_rejected_ttl_and_stagnation_are_batched(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            game = root / "game.json"
            strategy = root / "strategy.py"
            accumulated = root / "acc.json"
            rejected = root / "rejected.txt"
            rejected_meta = root / "rejected-meta.json"
            stagnation = root / "stagnation.json"
            strategy.write_text(STRATEGY, encoding="utf-8")
            rejected.write_text("fresh\nstale\nmissing\n", encoding="utf-8")
            rejected_meta.write_text(json.dumps({
                "fresh": {"updated_at": 990},
                "stale": {"updated_at": 900},
            }), encoding="utf-8")
            stagnation.write_text(json.dumps({
                "consecutive_no_improve": 3,
                "regression_streak": 2,
                "last_event": "rollback wait",
                "updated_at": 939,
            }), encoding="utf-8")
            value = MODULE.build_snapshot(
                game,
                strategy,
                accumulated,
                rejected,
                rejected_meta,
                stagnation,
                60,
                now=1000,
            )
            self.assertEqual(value["rejected_count"], 1)
            self.assertEqual(value["stagnation_count"], 3)
            self.assertEqual(value["regression_streak"], 2)
            self.assertEqual(value["stagnation_event"], "rollback wait")
            self.assertEqual(value["stagnation_age"], "1m")

    def test_show_status_uses_single_snapshot_process(self):
        source = (ROOT / "show_status.sh").read_text(encoding="utf-8")
        self.assertIn("python3 lib/status_snapshot.py", source)
        self.assertNotIn("python3 extract_decide_hash.py strategy.py", source)
        self.assertNotIn("acc_count=$(python3 -c", source)
        self.assertNotIn("acc_scores=$(python3 -c", source)
        self.assertNotIn("d=json.load(open('game_state.json'))", source)
        self.assertNotIn("rejected_count=$(python3", source)
        self.assertNotIn('python3 - "$TMP_STATE_DIR/stagnation_counter.json"', source)

    def test_shell_output_quotes_values(self):
        text = MODULE.render_shell({
            "game_state": "a b",
            "game_score": 1,
            "game_pieces": 2,
            "current_hash_for_acc": "abc",
            "acc_count": 3,
            "acc_scores": "1 2",
            "acc_russia_count": 4,
            "acc_soviet": "true",
            "acc_max_type": 5,
            "rejected_count": 6,
            "stagnation_count": 7,
            "regression_streak": 8,
            "stagnation_event": "a b",
            "stagnation_age": "9m",
        })
        self.assertIn("game_state='a b'", text)
        self.assertIn("acc_scores='1 2'", text)
        self.assertIn("stagnation_event='a b'", text)


if __name__ == "__main__":
    unittest.main()
