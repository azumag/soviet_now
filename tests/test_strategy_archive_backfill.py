"""Regression tests for strict-shell strategy archive maintenance."""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


class StrategyArchiveBackfillTest(unittest.TestCase):
    def _prepare_runtime(self, root: Path) -> tuple[Path, str]:
        strategy = root / "strategy.py"
        shutil.copy2(REPO_ROOT / "strategy.py", strategy)
        (root / "extract_decide_hash.py").symlink_to(
            REPO_ROOT / "extract_decide_hash.py"
        )
        (root / "versions").mkdir()
        strategy_hash = subprocess.check_output(
            ["python3", str(REPO_ROOT / "extract_decide_hash.py"), str(strategy)],
            text=True,
        ).strip()
        return strategy, strategy_hash

    def _run_bash(self, root: Path, body: str) -> subprocess.CompletedProcess[str]:
        script = textwrap.dedent(
            f"""
            set -euo pipefail
            ELOOP_LIB_DIR='{REPO_ROOT}'
            source '{REPO_ROOT / "core/config.sh"}'
            STRATEGY_FILE='{root / "strategy.py"}'
            STRATEGY_VERSIONS_DIR='{root / "versions"}'
            STRATEGY_HASH_ARCHIVE_DIR='{root / "by_hash"}'
            STRATEGY_HASH_PERMANENT_ARCHIVE_DIR='{root / "permanent"}'
            ROLLING_SCORES_FILE='{root / "rolling_scores.json"}'
            BEST_STRATEGY_ANCHOR_FILE='{root / "best_strategy_anchor.json"}'
            CURRENT_STRATEGY_RUN_FILE='{root / "current_strategy_run.json"}'
            ACTIVE_BRANCH_FILE='{root / "active_branch.json"}'
            REJECTED_HASHES_FILE='{root / "rejected_hashes.txt"}'
            BEHAVIOR_SIGNATURES_FILE='{root / "behavior_signatures.json"}'
            TABU_SIGNATURES_FILE='{root / "tabu_signatures.jsonl"}'
            LAST_ANCHOR_CHANGE_FILE='{root / "last_anchor_change.md"}'
            DIVERSITY_PREMIUM_ENABLED=0
            TABU_ENABLED=0
            source '{REPO_ROOT / "strategy/regression.sh"}'
            {body}
            """
        )
        return subprocess.run(
            ["bash", "-c", script],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=30,
        )

    def test_backfill_omits_optional_hash_under_nounset_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _, strategy_hash = self._prepare_runtime(root)

            result = self._run_bash(
                root,
                """
                _backfill_hash_archive_from_known_versions
                _backfill_hash_archive_from_known_versions
                """,
            )

            self.assertEqual(
                result.returncode,
                0,
                msg=f"stdout={result.stdout}\nstderr={result.stderr}",
            )
            self.assertTrue((root / "by_hash" / f"{strategy_hash}.py").is_file())
            self.assertTrue((root / "permanent" / f"{strategy_hash}.py").is_file())

    def test_branch_transition_creates_pin_under_nounset(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _, base_hash = self._prepare_runtime(root)
            new_hash = "newstrategy01"
            scores = [1000 + index for index in range(12)]
            (root / "rolling_scores.json").write_text(
                json.dumps(
                    {
                        base_hash: {
                            "scores": scores,
                            "games_total": len(scores),
                            "_recent_archives": [],
                        }
                    }
                ),
                encoding="utf-8",
            )
            (root / "current_strategy_run.json").write_text(
                json.dumps(
                    {
                        "hash": base_hash,
                        "scores": scores,
                        "games_total": len(scores),
                    }
                ),
                encoding="utf-8",
            )
            (root / "best_strategy_anchor.json").write_text(
                json.dumps(
                    {
                        "hash": base_hash,
                        "comp": 1005.5,
                        "p50": 1005.5,
                        "p25": 1002.75,
                        "lcb": 1004.0,
                        "n": len(scores),
                    }
                ),
                encoding="utf-8",
            )

            result = self._run_bash(
                root,
                f'_branch_transition_after_improve "{base_hash}" "{new_hash}"',
            )

            self.assertEqual(
                result.returncode,
                0,
                msg=f"stdout={result.stdout}\nstderr={result.stderr}",
            )
            active = json.loads((root / "active_branch.json").read_text())
            self.assertEqual(active["anchor_hash"], base_hash)
            self.assertEqual(active["head_hash"], new_hash)
            self.assertEqual(active["lineage"][-1], new_hash)


if __name__ == "__main__":
    unittest.main()
