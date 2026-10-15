"""Phase A (docich#392) hermetic tests: strategy archive readers fall back
``<hash>.py.gz`` → ``<hash>.py``.

These tests do not touch the network or the real ``strategy_versions*`` trees.
They build temp archives from a synthetic strategy source, then exercise the
shared reader helpers plus the concrete reader entry points (``eloop.sh``,
``strategy/regression.sh``) against a gz-only archive.

Contract fixed here:
* candidate order per directory is ``<hash>.py.gz`` then ``<hash>.py``;
* a gz-only hash is resolvable and readable by every reader path;
* resolution yields a *plaintext* path (callers ``cp``/``grep``/validate it);
* with plaintext-only archives (< Phase B) behavior and paths are unchanged.
"""

from __future__ import annotations

import gzip
import json
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from lib.strategy_archive import (  # noqa: E402
    archive_paths,
    candidate_paths,
    find_path,
    is_runtime_stable,
    read_archive,
    resolve_plaintext,
)
from extract_decide_hash import compute_hash  # noqa: E402


STRATEGY_SOURCE = (
    "def decide(game_state, analysis):\n"
    "    # --- BEGIN DEADLINE GUARD (injected from current strategy deadline logic) ---\n"
    "    return {'x': 0, 'reason': 'ok'}\n"
)
UNSTABLE_SOURCE = "def decide(game_state, analysis):\n    return {'x': 1}\n"


class ArchiveHelperUnitTest(unittest.TestCase):
    """Pure-python helper contract."""

    def test_candidate_order_prefers_gz_then_plaintext(self):
        paths = list(candidate_paths("abc123", ["/by_hash", "/permanent"]))
        self.assertEqual(
            paths,
            [
                "/by_hash/abc123.py.gz",
                "/by_hash/abc123.py",
                "/permanent/abc123.py.gz",
                "/permanent/abc123.py",
            ],
        )
        self.assertEqual(
            archive_paths("abc123", "/by_hash", "/permanent"),
            paths,
        )

    def test_read_archive_transparently_decompresses(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            gz_path = root / "h.py.gz"
            with gzip.open(gz_path, "wt", encoding="utf-8") as f:
                f.write(STRATEGY_SOURCE)
            self.assertEqual(read_archive(gz_path), STRATEGY_SOURCE)
            self.assertTrue(is_runtime_stable(gz_path))

            plain = root / "h.py"
            plain.write_text(UNSTABLE_SOURCE, encoding="utf-8")
            self.assertFalse(is_runtime_stable(plain))

    def test_find_path_and_resolve_plaintext_handle_gz_only_hash(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            by_hash = root / "by_hash"
            permanent = root / "permanent"
            by_hash.mkdir()
            permanent.mkdir()
            gz_path = permanent / "deadbeef.py.gz"
            with gzip.open(gz_path, "wt", encoding="utf-8") as f:
                f.write(STRATEGY_SOURCE)

            self.assertEqual(find_path("deadbeef", [by_hash, permanent]), str(gz_path))
            self.assertFalse(find_path("deadbeef", [by_hash]))
            # predicate (deadline guard) は gz でも透過的に評価される
            self.assertEqual(
                find_path("deadbeef", [by_hash, permanent], is_runtime_stable),
                str(gz_path),
            )

            resolved = resolve_plaintext("deadbeef", str(by_hash), str(permanent))
            self.assertEqual(resolved, str(by_hash / "deadbeef.py"))
            self.assertEqual(Path(resolved).read_text(encoding="utf-8"), STRATEGY_SOURCE)
            self.assertFalse(str(resolved).endswith(".gz"))

    def test_is_runtime_stable_rejects_unstable_gz_candidate(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            by_hash = root / "by_hash"
            by_hash.mkdir()
            with gzip.open(by_hash / "cafefeed.py.gz", "wt", encoding="utf-8") as f:
                f.write(UNSTABLE_SOURCE)

            self.assertFalse(is_runtime_stable(by_hash / "cafefeed.py.gz"))
            self.assertFalse(find_path("cafefeed", [by_hash], is_runtime_stable))

    def test_extract_decide_hash_reads_gz(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            plain = root / "h.py"
            plain.write_text(STRATEGY_SOURCE, encoding="utf-8")
            gz_path = root / "h.py.gz"
            with gzip.open(gz_path, "wt", encoding="utf-8") as f:
                f.write(STRATEGY_SOURCE)
            self.assertEqual(compute_hash(gz_path), compute_hash(plain))
            self.assertTrue(compute_hash(gz_path))


class ArchiveReaderIntegrationTest(unittest.TestCase):
    """Reader paths resolve a gz-only hash without breaking plaintext archives."""

    def _prepare(self, td: Path) -> tuple[Path, Path, Path, str]:
        root = td
        by_hash = root / "by_hash"
        permanent = root / "permanent"
        by_hash.mkdir()
        permanent.mkdir()
        strategy = root / "strategy.py"
        strategy.write_text(STRATEGY_SOURCE, encoding="utf-8")
        strategy_hash = subprocess.check_output(
            ["python3", str(REPO_ROOT / "extract_decide_hash.py"), str(strategy)],
            text=True,
        ).strip()
        return by_hash, permanent, strategy, strategy_hash

    def _gzip_into(self, directory: Path, strategy_hash: str) -> Path:
        dst = directory / f"{strategy_hash}.py.gz"
        with gzip.open(dst, "wt", encoding="utf-8") as f:
            f.write(STRATEGY_SOURCE)
        return dst

    def _run_bash(self, root: Path, permanent: Path, body: str) -> subprocess.CompletedProcess[str]:
        # cwd=REPO_ROOT so the readers' relative ``python3 extract_decide_hash.py``
        # call resolves exactly as it does in the runtime.
        script = textwrap.dedent(
            f"""
            set -euo pipefail
            log() {{ :; }}
            STRATEGY_HASH_ARCHIVE_DIR='{root / "by_hash"}'
            STRATEGY_HASH_PERMANENT_ARCHIVE_DIR='{permanent}'
            STRATEGY_FILE='{root / "strategy.py"}'
            ROLLING_SCORES_FILE='{root / "rolling_scores.json"}'
            source '{REPO_ROOT / "lib/strategy_archive.sh"}'
            {body}
            """
        )
        return subprocess.run(
            ["bash", "-c", script],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=60,
        )

    def test_eloop_reader_resolves_gz_only_permanent_archive(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            by_hash, permanent, _, strategy_hash = self._prepare(root)
            gz_path = self._gzip_into(permanent, strategy_hash)

            result = self._run_bash(
                root,
                permanent,
                f"""
                source '{REPO_ROOT / "eloop.sh"}'
                _find_strategy_archive_for_hash '{strategy_hash}'
                """,
            )
            self.assertEqual(result.returncode, 0, msg=f"stderr={result.stderr}")
            resolved = result.stdout.strip()
            self.assertTrue(resolved, msg="reader returned no path")
            self.assertFalse(resolved.endswith(".gz"), resolved)
            self.assertTrue(Path(resolved).is_file(), resolved)
            # gz 一致候補は作業アーカイブへ平文展開して返る
            self.assertEqual(Path(resolved), by_hash / f"{strategy_hash}.py")
            self.assertEqual(
                subprocess.check_output(
                    ["python3", str(REPO_ROOT / "extract_decide_hash.py"), resolved],
                    text=True,
                ).strip(),
                strategy_hash,
            )
            # 判定に使った gz はそのまま残る
            self.assertTrue(gz_path.is_file())

    def test_eloop_reader_is_unchanged_for_plaintext_archive(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            by_hash, permanent, _, strategy_hash = self._prepare(root)
            plain = by_hash / f"{strategy_hash}.py"
            plain.write_text(STRATEGY_SOURCE, encoding="utf-8")
            before = sorted(p.name for p in by_hash.iterdir())

            result = self._run_bash(
                root,
                permanent,
                f"""
                source '{REPO_ROOT / "eloop.sh"}'
                _find_strategy_archive_for_hash '{strategy_hash}'
                """,
            )
            self.assertEqual(result.returncode, 0, msg=f"stderr={result.stderr}")
            self.assertEqual(result.stdout.strip(), str(plain))
            self.assertEqual(before, sorted(p.name for p in by_hash.iterdir()))

    def test_bash_helpers_resolve_read_and_copy_gz(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            by_hash, permanent, _, strategy_hash = self._prepare(root)
            self._gzip_into(permanent, strategy_hash)

            result = self._run_bash(
                root,
                permanent,
                f"""
                strategy_archive_candidates '{strategy_hash}'
                echo '=@='
                strategy_archive_read '{permanent}/{strategy_hash}.py.gz'
                echo '=@='
                strategy_archive_resolve_plaintext '{strategy_hash}'
                echo '=@='
                strategy_archive_copy '{permanent}/{strategy_hash}.py.gz' '{root}/copied.py'
                python3 '{REPO_ROOT / "extract_decide_hash.py"}' '{root}/copied.py'
                """,
            )
            self.assertEqual(result.returncode, 0, msg=f"stderr={result.stderr}")
            candidates, read_out, resolved, copied_hash = result.stdout.split("=@=\n")
            self.assertEqual(
                candidates.strip().splitlines(),
                [
                    str(by_hash / f"{strategy_hash}.py.gz"),
                    str(by_hash / f"{strategy_hash}.py"),
                    str(permanent / f"{strategy_hash}.py.gz"),
                    str(permanent / f"{strategy_hash}.py"),
                ],
            )
            self.assertEqual(read_out, STRATEGY_SOURCE)
            self.assertEqual(resolved.strip(), str(by_hash / f"{strategy_hash}.py"))
            self.assertEqual(copied_hash.strip(), strategy_hash)

    def test_regression_rollback_candidate_resolves_gz_only_archive(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            by_hash, permanent, _, strategy_hash = self._prepare(root)
            self._gzip_into(permanent, strategy_hash)

            result = self._run_bash(
                root,
                permanent,
                f"""
                source '{REPO_ROOT / "strategy/regression.sh"}'
                _find_rollback_candidate_file_for_hash '{strategy_hash}'
                """,
            )
            self.assertEqual(result.returncode, 0, msg=f"stderr={result.stderr}")
            resolved = result.stdout.strip().splitlines()[-1]
            self.assertFalse(resolved.endswith(".gz"), resolved)
            self.assertTrue(Path(resolved).is_file(), resolved)
            self.assertEqual(
                subprocess.check_output(
                    ["python3", str(REPO_ROOT / "extract_decide_hash.py"), resolved],
                    text=True,
                ).strip(),
                strategy_hash,
            )

    def test_regression_normalize_repair_materializes_gz_permanent(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            by_hash, permanent, _, strategy_hash = self._prepare(root)
            self._gzip_into(permanent, strategy_hash)
            (root / "rolling_scores.json").write_text(json.dumps({}), encoding="utf-8")

            result = self._run_bash(
                root,
                permanent,
                f"""
                source '{REPO_ROOT / "strategy/regression.sh"}'
                _merge_rolling_scores_on_normalize '{strategy_hash}' 'otherhash0000'
                echo "rc=$?"
                """,
            )
            self.assertEqual(result.returncode, 0, msg=f"stderr={result.stderr}")
            repaired = by_hash / f"{strategy_hash}.py"
            self.assertTrue(repaired.is_file(), result.stdout + result.stderr)
            self.assertEqual(repaired.read_text(encoding="utf-8"), STRATEGY_SOURCE)


class ArchiveReaderWiringTest(unittest.TestCase):
    """Every listed reader site must go through the shared gz-aware helper.

    Behaviour for the extracted helpers is covered above; these assertions keep
    each heredoc/reader site wired to that helper so a future edit cannot
    silently drop ``.py.gz`` support (the Phase B prerequisite).
    """

    def test_python_reader_sites_import_shared_helper(self):
        # 各 reader サイトが共有 gz-aware helper を import していること。
        # (alias 名はサイトごとに違うので、gz 判定に使う symbol を個別に確認する)
        expected = {
            "strategy/improve.sh": "archive_is_runtime_stable",
            "eloop_improve.sh": "archive_is_runtime_stable",
            "show_status.sh": "archive_is_runtime_stable",
            "strategy/regression.sh": "archive_is_runtime_stable",
            "status_dashboard.py": "_sa_is_runtime_stable",
        }
        for rel, symbol in expected.items():
            text = (REPO_ROOT / rel).read_text(encoding="utf-8")
            self.assertIn("lib.strategy_archive import", text, msg=rel)
            self.assertIn(symbol, text, msg=rel)

    def test_python_reader_sites_keep_find_archive_path(self):
        for rel in ("strategy/improve.sh", "eloop_improve.sh", "show_status.sh"):
            text = (REPO_ROOT / rel).read_text(encoding="utf-8")
            self.assertIn("find_archive_path", text, msg=rel)
            self.assertIn("if include_permanent and permanent_archive_dir:", text, msg=rel)

    def test_bash_reader_sites_call_shared_resolver(self):
        eloop = (REPO_ROOT / "eloop.sh").read_text(encoding="utf-8")
        self.assertIn("strategy_archive_resolve_plaintext", eloop)
        ab_ctl = (REPO_ROOT / "tools/ab_ctl.sh").read_text(encoding="utf-8")
        self.assertIn("strategy_archive_resolve_plaintext", ab_ctl)
        self.assertIn("strategy_archive_copy", ab_ctl)
        regression = (REPO_ROOT / "strategy/regression.sh").read_text(encoding="utf-8")
        self.assertIn("strategy_archive_candidates", regression)
        self.assertIn("strategy_archive_copy", regression)
        improve = (REPO_ROOT / "eloop_improve.sh").read_text(encoding="utf-8")
        self.assertIn("strategy_archive_copy", improve)

    def test_eloop_lib_sources_shared_helper(self):
        shim = (REPO_ROOT / "eloop_lib.sh").read_text(encoding="utf-8")
        self.assertIn("source \"$ELOOP_LIB_DIR/lib/strategy_archive.sh\"", shim)


if __name__ == "__main__":
    unittest.main()
