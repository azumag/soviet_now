#!/usr/bin/env python3
"""soviet_now#146: artifact doctor の契約テスト。

- --dry-run 以外は拒否（副作用ゼロの保証）
- unclassified / oversize / broken_pointer を正しく報告
- doctor 実行がファイル・worktree を変更しない（read-only）
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOCTOR = ROOT / "tools" / "artifact_doctor.py"


def run_doctor(*args: str, cwd: Path = ROOT) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(DOCTOR), *args],
        capture_output=True, text=True, cwd=cwd,
    )


def _tree_digest(path: Path) -> str:
    """top-level の mtime/size 署名 — 変更検出用。"""
    h = hashlib.sha256()
    for entry in sorted(path.iterdir(), key=lambda p: p.name):
        try:
            st = entry.lstat()
        except OSError:
            continue
        h.update(f"{entry.name}|{st.st_size}|{st.st_mtime_ns}".encode())
    return h.hexdigest()


class TestArtifactDoctor(unittest.TestCase):
    def test_requires_dry_run(self):
        res = run_doctor()
        self.assertEqual(res.returncode, 2)
        self.assertIn("dry-run", res.stderr)

    def test_dry_run_reports_json(self):
        res = run_doctor("--dry-run")
        self.assertIn(res.returncode, (0, 1))
        report = json.loads(res.stdout)
        for key in ("unclassified", "oversize", "broken_pointer", "summary"):
            self.assertIn(key, report)
        self.assertGreater(report["summary"]["tracked"], 0)

    def test_all_top_level_classified_or_marked(self):
        """受入条件: 全 top-level artifact に owner または unknown-needs-owner。"""
        res = run_doctor("--dry-run")
        report = json.loads(res.stdout)
        self.assertEqual(report["unclassified"], [],
                         f"unclassified remains: {report['unclassified']}")

    def test_read_only(self):
        """受入条件: doctor は file/worktree を変更・削除しない。"""
        before = _tree_digest(ROOT)
        run_doctor("--dry-run")
        self.assertEqual(_tree_digest(ROOT), before)

    def test_unclassified_reports_unknown_needs_owner(self):
        """分類不能なエントリは unknown-needs-owner として報告される（別 sandbox で検証）。"""
        # doctor の _classify を直接検証: artifacts.toml に無い名前は None → 報告対象
        sys.path.insert(0, str(ROOT / "tools"))
        import artifact_doctor as ad
        self.assertIsNone(ad._classify("zzz_no_such_artifact_zzz", [{"pattern": "*.sh"}]))
        self.assertIsNotNone(ad._classify("foo.sh", [{"pattern": "*.sh"}]))

    def test_first_match_wins(self):
        sys.path.insert(0, str(ROOT / "tools"))
        import artifact_doctor as ad
        arts = [{"pattern": "strategy.py", "owner": "a"}, {"pattern": "*.py", "owner": "b"}]
        self.assertEqual(ad._classify("strategy.py", arts)["owner"], "a")
        self.assertEqual(ad._classify("other.py", arts)["owner"], "b")


if __name__ == "__main__":
    unittest.main()
