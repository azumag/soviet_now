from __future__ import annotations

import json
import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

REVIEWED_STRATEGY = "REVIEWED = 1\n"
CANDIDATE_STRATEGY = "CANDIDATE = 2\n"


class ProjectionGuardTests(unittest.TestCase):
    """docich#2487: projection live-apply gate at the strategy_runtime choke point.

    The production runtime (/home/ubuntu/soren) is a git-less projection whose
    live bytes+mode must stay inside the reviewed lineage of games/soviet_now,
    or the docich deploy plane fail-closes (reconcile_refused, #2482). These
    tests pin the gate's policy matrix on a temp lineage repository.
    """

    maxDiff = None

    def _run(self, script: str, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
        merged = dict(os.environ)
        merged.update(env)
        return subprocess.run(
            ["bash", "-c", script],
            cwd=ROOT,
            env=merged,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )

    def _init_lineage_repo(
        self, root: Path, strategy_text: str, helpers: dict[str, str] | None = None
    ) -> Path:
        repo = root / "lineage"
        repo.mkdir()
        env = dict(os.environ)
        env.update(
            GIT_AUTHOR_NAME="guard-test",
            GIT_AUTHOR_EMAIL="guard-test@example.invalid",
            GIT_COMMITTER_NAME="guard-test",
            GIT_COMMITTER_EMAIL="guard-test@example.invalid",
        )

        def git(*args: str) -> None:
            subprocess.run(
                ["git", "-C", str(repo), *args],
                check=True,
                capture_output=True,
                text=True,
                env=env,
            )

        git("init", "-q", "-b", "main")
        strategy = repo / "strategy.py"
        strategy.write_text(strategy_text, encoding="utf-8")
        strategy.chmod(0o755)
        helpers_dir = repo / "strategy_helpers"
        helpers_dir.mkdir()
        (helpers_dir / "__init__.py").write_text("", encoding="utf-8")
        for name, text in (helpers or {}).items():
            target = helpers_dir / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding="utf-8")
        git("add", "-A")
        git("commit", "-q", "-m", "reviewed lineage")
        return repo

    def _workdir(
        self,
        root: Path,
        *,
        live: str,
        candidate: str,
        live_helpers: dict[str, str] | None = None,
        candidate_helpers: dict[str, str] | None = None,
    ) -> Path:
        work = root / "runtime"
        work.mkdir()
        (work / "strategy.py").write_text(live, encoding="utf-8")
        (work / "candidate.py").write_text(candidate, encoding="utf-8")
        for dirname, files in (
            ("strategy_helpers", live_helpers),
            ("candidate_helpers", candidate_helpers),
        ):
            if files is None:
                continue
            helpers_dir = work / dirname
            helpers_dir.mkdir()
            (helpers_dir / "__init__.py").write_text("", encoding="utf-8")
            for name, text in files.items():
                (helpers_dir / name).write_text(text, encoding="utf-8")
        (work / "state").mkdir()
        return work

    def _apply_script(self, kind: str) -> str:
        return (
            "set -u\n"
            'cd "$WORKDIR"\n'
            f'source "{ROOT / "core/strategy_runtime.sh"}"\n'
            "rc=0\n"
            + (
                'strategy_runtime_atomic_apply "$SOURCE" "$TARGET" || rc=$?\n'
                if kind == "single"
                else 'strategy_runtime_atomic_apply_bundle "$SOURCE" "$TARGET" "$HELPERS_SOURCE" "$HELPERS_TARGET" || rc=$?\n'
            )
            + 'printf \'%s\\n\' "rc=${rc}"\n'
        )

    def _env(self, work: Path, repo: Path, policy: str, bundle: bool) -> dict[str, str]:
        env = {
            "WORKDIR": str(work),
            "SOURCE": "candidate.py",
            "TARGET": "strategy.py",
            "TMP_STATE_DIR": str(work / "state"),
            "SOREN_PROJECTION_FORCE": "1",
            "SOREN_LINEAGE_REF_REPO": str(repo),
            "IMPROVE_LIVE_APPLY_POLICY": policy,
        }
        if bundle:
            env["HELPERS_SOURCE"] = "candidate_helpers"
            env["HELPERS_TARGET"] = "strategy_helpers"
        return env

    def _rc(self, result: subprocess.CompletedProcess[str]) -> int:
        for line in result.stdout.splitlines():
            if line.startswith("rc="):
                return int(line[3:])
        raise AssertionError(f"rc line missing; stdout={result.stdout!r} stderr={result.stderr!r}")

    def _event(self, work: Path) -> dict:
        path = work / "state" / "projection_guard_last_event.json"
        return json.loads(path.read_text(encoding="utf-8"))

    def _marker(self, work: Path) -> dict:
        path = work / "state" / "strategy_apply_last_failure.json"
        return json.loads(path.read_text(encoding="utf-8"))

    def _quarantine_dirs(self, work: Path) -> list[Path]:
        base = work / "state" / "projection_guard"
        if not base.is_dir():
            return []
        return sorted(base.iterdir())

    # --- enforce_reviewed -------------------------------------------------

    def test_enforce_refuses_unreviewed_candidate_and_quarantines(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            repo = self._init_lineage_repo(root, REVIEWED_STRATEGY)
            work = self._workdir(root, live=REVIEWED_STRATEGY, candidate=CANDIDATE_STRATEGY)
            result = self._run(
                self._apply_script("single"),
                self._env(work, repo, "enforce_reviewed", bundle=False),
            )
            self.assertEqual(self._rc(result), 3, result.stderr)
            self.assertEqual((work / "strategy.py").read_text(encoding="utf-8"), REVIEWED_STRATEGY)
            quarantine = self._quarantine_dirs(work)
            self.assertEqual(len(quarantine), 1)
            self.assertEqual(
                (quarantine[0] / "strategy.py").read_text(encoding="utf-8"), CANDIDATE_STRATEGY
            )
            self.assertEqual(self._marker(work)["failure_code"], "projection_guard_refused")
            event = self._event(work)
            self.assertEqual(event["event"], "refused")
            self.assertEqual(event["policy"], "enforce_reviewed")
            self.assertTrue(event["reason"].startswith("mismatch:"))

    def test_enforce_allows_reviewed_candidate_and_normalizes_mode(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            repo = self._init_lineage_repo(root, REVIEWED_STRATEGY)
            work = self._workdir(root, live=REVIEWED_STRATEGY, candidate=REVIEWED_STRATEGY)
            (work / "candidate.py").chmod(0o600)
            result = self._run(
                self._apply_script("single"),
                self._env(work, repo, "enforce_reviewed", bundle=False),
            )
            self.assertEqual(self._rc(result), 0, result.stderr)
            target = work / "strategy.py"
            self.assertEqual(target.read_text(encoding="utf-8"), REVIEWED_STRATEGY)
            self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o755)
            # the reviewed mode is applied to the live file; the candidate file
            # itself is never mutated by the gate.
            self.assertEqual(stat.S_IMODE((work / "candidate.py").stat().st_mode), 0o600)
            self.assertEqual(self._quarantine_dirs(work), [])
            self.assertFalse((work / "state" / "strategy_apply_last_failure.json").exists())

    def test_enforce_refuses_unreviewed_bundle_helpers(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            repo = self._init_lineage_repo(root, REVIEWED_STRATEGY, {"one.py": "A = 1\n"})
            work = self._workdir(
                root,
                live=REVIEWED_STRATEGY,
                candidate=REVIEWED_STRATEGY,
                live_helpers={"one.py": "A = 1\n"},
                candidate_helpers={"one.py": "A = 999\n"},
            )
            result = self._run(
                self._apply_script("bundle"),
                self._env(work, repo, "enforce_reviewed", bundle=True),
            )
            self.assertEqual(self._rc(result), 3, result.stderr)
            self.assertEqual(
                (work / "strategy_helpers" / "one.py").read_text(encoding="utf-8"), "A = 1\n"
            )
            quarantine = self._quarantine_dirs(work)
            self.assertEqual(len(quarantine), 1)
            self.assertTrue((quarantine[0] / "strategy_helpers" / "one.py").exists())

    def test_enforce_allows_reviewed_bundle(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            repo = self._init_lineage_repo(root, REVIEWED_STRATEGY, {"one.py": "A = 1\n"})
            work = self._workdir(
                root,
                live=REVIEWED_STRATEGY,
                candidate=REVIEWED_STRATEGY,
                live_helpers={"one.py": "A = 1\n"},
                candidate_helpers={"one.py": "A = 1\n"},
            )
            result = self._run(
                self._apply_script("bundle"),
                self._env(work, repo, "enforce_reviewed", bundle=True),
            )
            self.assertEqual(self._rc(result), 0, result.stderr)
            self.assertEqual(
                (work / "strategy_helpers" / "one.py").read_text(encoding="utf-8"), "A = 1\n"
            )

    def test_enforce_refuses_bundle_missing_reviewed_helper(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            repo = self._init_lineage_repo(root, REVIEWED_STRATEGY, {"one.py": "A = 1\n"})
            work = self._workdir(
                root,
                live=REVIEWED_STRATEGY,
                candidate=REVIEWED_STRATEGY,
                live_helpers={"one.py": "A = 1\n"},
                candidate_helpers={},
            )
            result = self._run(
                self._apply_script("bundle"),
                self._env(work, repo, "enforce_reviewed", bundle=True),
            )
            self.assertEqual(self._rc(result), 3, result.stderr)
            # Missing reviewed entries would be dropped by the bundle apply and
            # drift live helpers away from the gitlink just like extra bytes.
            self.assertEqual(
                (work / "strategy_helpers" / "one.py").read_text(encoding="utf-8"), "A = 1\n"
            )

    # --- dry_run (default) -------------------------------------------------

    def test_dry_run_default_allows_unreviewed_and_records_would_refuse(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            repo = self._init_lineage_repo(root, REVIEWED_STRATEGY)
            work = self._workdir(root, live=REVIEWED_STRATEGY, candidate=CANDIDATE_STRATEGY)
            env = self._env(work, repo, "dry_run", bundle=False)
            env.pop("IMPROVE_LIVE_APPLY_POLICY")
            result = self._run(self._apply_script("single"), env)
            self.assertEqual(self._rc(result), 0, result.stderr)
            self.assertEqual((work / "strategy.py").read_text(encoding="utf-8"), CANDIDATE_STRATEGY)
            event = self._event(work)
            self.assertEqual(event["event"], "would_refuse")
            self.assertEqual(event["policy"], "dry_run")
            self.assertEqual(self._quarantine_dirs(work), [])

    def test_auto_detection_marks_gitless_runtime_as_projection(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            repo = self._init_lineage_repo(root, REVIEWED_STRATEGY)
            work = self._workdir(root, live=REVIEWED_STRATEGY, candidate=CANDIDATE_STRATEGY)
            env = self._env(work, repo, "dry_run", bundle=False)
            env.pop("SOREN_PROJECTION_FORCE")  # auto: workdir has no .git
            result = self._run(self._apply_script("single"), env)
            self.assertEqual(self._rc(result), 0, result.stderr)
            self.assertEqual(self._event(work)["event"], "would_refuse")

    # --- escapes -------------------------------------------------------------

    def test_policy_off_disables_gate(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            repo = self._init_lineage_repo(root, REVIEWED_STRATEGY)
            work = self._workdir(root, live=REVIEWED_STRATEGY, candidate=CANDIDATE_STRATEGY)
            result = self._run(
                self._apply_script("single"),
                self._env(work, repo, "off", bundle=False),
            )
            self.assertEqual(self._rc(result), 0, result.stderr)
            self.assertEqual((work / "strategy.py").read_text(encoding="utf-8"), CANDIDATE_STRATEGY)
            self.assertFalse((work / "state" / "projection_guard_last_event.json").exists())

    def test_missing_lineage_repo_degrades_to_dry_run(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            repo = self._init_lineage_repo(root, REVIEWED_STRATEGY)
            work = self._workdir(root, live=REVIEWED_STRATEGY, candidate=CANDIDATE_STRATEGY)
            env = self._env(work, repo, "enforce_reviewed", bundle=False)
            env["SOREN_LINEAGE_REF_REPO"] = str(root / "does-not-exist")
            result = self._run(self._apply_script("single"), env)
            self.assertEqual(self._rc(result), 0, result.stderr)
            self.assertEqual((work / "strategy.py").read_text(encoding="utf-8"), CANDIDATE_STRATEGY)
            self.assertEqual(self._event(work)["event"], "gate_degraded")

    def test_non_projection_runtime_is_unguarded(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            repo = self._init_lineage_repo(root, REVIEWED_STRATEGY)
            work = self._workdir(root, live=REVIEWED_STRATEGY, candidate=CANDIDATE_STRATEGY)
            (work / ".git").mkdir()  # dev checkout: not a projection
            env = self._env(work, repo, "enforce_reviewed", bundle=False)
            env.pop("SOREN_PROJECTION_FORCE")  # auto-detect sees the .git dir
            result = self._run(
                self._apply_script("single"),
                env,
            )
            self.assertEqual(self._rc(result), 0, result.stderr)
            self.assertEqual((work / "strategy.py").read_text(encoding="utf-8"), CANDIDATE_STRATEGY)
            self.assertFalse((work / "state" / "projection_guard_last_event.json").exists())


if __name__ == "__main__":
    unittest.main()
