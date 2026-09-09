#!/usr/bin/env python3
"""_opencode_verify_or_reset_xdg_db: heal the improvement loop's isolated OpenCode DB.

Regression cover for the 2026-09-10 outage: the isolated
`tmp/state/xdg_data/opencode/opencode.db` went corrupt, every `opencode run` in the
improve path died on `PRAGMA wal_checkpoint`, and the loop mislabelled it as
`rate_limited` and backed off for ~10h. The helper now checks that DB just before
each improve OpenCode call and moves a corrupt/oversized one aside so OpenCode
recreates it. It must never touch the shared `$HOME/.local/share/opencode` DB.
"""

from __future__ import annotations

import os
import sqlite3
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
LIB = REPO_ROOT / "lib" / "ai_generate.sh"


def _make_sqlite(path: Path, rows: int = 1) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    try:
        con.execute("CREATE TABLE IF NOT EXISTS t (a TEXT)")
        con.executemany("INSERT INTO t VALUES (?)", [("x" * 64,)] * rows)
        con.commit()
    finally:
        con.close()


def _run_helper(data_home: Path, *, env_extra: dict[str, str] | None = None,
                cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    script = textwrap.dedent(
        f"""
        set -u
        source {LIB!s}
        OPENCODE_XDG_DATA_HOME={data_home!s}
        _opencode_verify_or_reset_xdg_db
        echo "rc=$?"
        """
    )
    env = os.environ.copy()
    env.pop("OPENCODE_XDG_DB_SELF_HEAL", None)
    env.pop("OPENCODE_XDG_DB_MAX_MB", None)
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        ["bash", "-c", script],
        cwd=str(cwd or REPO_ROOT),
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


class OpencodeXdgDbRecoveryTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(dir="/tmp")
        self.root = Path(self._tmp.name)
        self.data_home = self.root / "xdg_data"
        self.db_dir = self.data_home / "opencode"
        self.db = self.db_dir / "opencode.db"
        # run from a scratch dir so `./overlay_notify.sh` is never picked up
        self.cwd = self.root / "cwd"
        self.cwd.mkdir()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _resets(self) -> list[Path]:
        return sorted(self.db_dir.glob("reset-*-*"))

    def test_healthy_db_is_left_untouched(self) -> None:
        _make_sqlite(self.db)
        before = self.db.read_bytes()
        result = _run_helper(self.data_home, cwd=self.cwd)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("rc=0", result.stdout)
        self.assertTrue(self.db.is_file())
        self.assertEqual(self.db.read_bytes(), before)
        self.assertEqual(self._resets(), [])

    def test_missing_db_is_noop(self) -> None:
        self.db_dir.mkdir(parents=True)
        result = _run_helper(self.data_home, cwd=self.cwd)
        self.assertIn("rc=0", result.stdout)
        self.assertFalse(self.db.exists())
        self.assertEqual(self._resets(), [])

    def test_corrupt_db_is_moved_aside_with_sidecars(self) -> None:
        self.db_dir.mkdir(parents=True)
        self.db.write_bytes(b"this is not a sqlite database" * 8)
        (self.db_dir / "opencode.db-wal").write_bytes(b"junk-wal")
        (self.db_dir / "opencode.db-shm").write_bytes(b"junk-shm")

        result = _run_helper(self.data_home, cwd=self.cwd)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("rc=0", result.stdout)

        # nothing named opencode.db* is left in the live dir
        self.assertEqual(sorted(self.db_dir.glob("opencode.db*")), [])

        resets = self._resets()
        self.assertEqual(len(resets), 1)
        self.assertTrue(resets[0].name.startswith("reset-corrupt-"))
        self.assertTrue((resets[0] / "opencode.db").is_file())

    def test_oversized_db_is_rotated(self) -> None:
        _make_sqlite(self.db, rows=2000)
        # pad to comfortably exceed a 1 MiB ceiling without corrupting it
        with self.db.open("ab") as fh:
            fh.write(b"\0" * (1_200_000))
        result = _run_helper(
            self.data_home,
            env_extra={"OPENCODE_XDG_DB_MAX_MB": "1"},
            cwd=self.cwd,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(self.db.exists())
        resets = self._resets()
        self.assertEqual(len(resets), 1)
        self.assertTrue(resets[0].name.startswith("reset-oversize-"))

    def test_disabled_toggle_keeps_corrupt_db(self) -> None:
        self.db_dir.mkdir(parents=True)
        self.db.write_bytes(b"corrupt")
        result = _run_helper(
            self.data_home,
            env_extra={"OPENCODE_XDG_DB_SELF_HEAL": "0"},
            cwd=self.cwd,
        )
        self.assertIn("rc=0", result.stdout)
        self.assertTrue(self.db.is_file())
        self.assertEqual(self._resets(), [])

    def test_shared_home_db_is_never_touched(self) -> None:
        fake_home = self.root / "home"
        shared = fake_home / ".local" / "share" / "opencode"
        shared.mkdir(parents=True)
        shared_db = shared / "opencode.db"
        shared_db.write_bytes(b"pretend-corrupt-but-precious")
        result = _run_helper(
            fake_home / ".local" / "share",
            env_extra={"HOME": str(fake_home)},
            cwd=self.cwd,
        )
        self.assertIn("rc=0", result.stdout)
        self.assertTrue(shared_db.is_file(), "shared DB must be left alone")
        self.assertEqual(sorted(shared.glob("reset-*-*")), [])

    def test_only_latest_five_reset_backups_are_kept(self) -> None:
        self.db_dir.mkdir(parents=True)
        for i in range(7):
            old = self.db_dir / f"reset-corrupt-2026010{i}_000000"
            old.mkdir()
            (old / "opencode.db").write_bytes(b"old")
        self.db.write_bytes(b"corrupt-now")

        result = _run_helper(self.data_home, cwd=self.cwd)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertLessEqual(len(self._resets()), 5)


if __name__ == "__main__":
    unittest.main()
