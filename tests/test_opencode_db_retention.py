#!/usr/bin/env python3
"""opencode DB bounded retention のテスト (issue #389 / ADR 0002)."""
from __future__ import annotations

import importlib.util
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "opencode_db_retention", ROOT / "lib" / "opencode_db_retention.py"
)
retention = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(retention)

BASE_SCHEMA = """
create table session(id text primary key, time_created integer);
create table event_sequence(aggregate_id text primary key, seq integer);
create table event(id text primary key, aggregate_id text references event_sequence(aggregate_id) on delete cascade);
create table message(id text primary key, session_id text references session(id) on delete cascade);
create table part(id text primary key, message_id text references message(id) on delete cascade, session_id text);
"""


class OpencodeDbRetentionTests(unittest.TestCase):
    def make_db(self, path: Path, extra_schema: str = "") -> None:
        con = sqlite3.connect(path)
        con.executescript(BASE_SCHEMA + extra_schema)
        now = int(time.time() * 1000)
        for sid, offset_days in (("old", 10), ("new", 1)):
            stamp = now - offset_days * 86400000
            con.execute("insert into session values (?,?)", (sid, stamp))
            con.execute("insert into event_sequence values (?,1)", (sid,))
            con.execute("insert into event values (?,?)", (f"e-{sid}", sid))
            con.execute("insert into message values (?,?)", (f"m-{sid}", sid))
            con.execute("insert into part values (?,?,?)", (f"p-{sid}", f"m-{sid}", sid))
        con.commit()
        con.close()

    def counts(self, path: Path) -> dict:
        con = sqlite3.connect(path)
        out = {
            t: con.execute(f"select count(*) from {t}").fetchone()[0]
            for t in ("session", "event", "event_sequence", "message", "part")
        }
        con.close()
        return out

    def test_removes_only_old_sessions_and_children(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "opencode.db"
            self.make_db(db)
            old = retention.rotate(str(db), 3)
            self.assertEqual(old, 1)
            self.assertEqual(
                self.counts(db),
                {"session": 1, "event": 1, "event_sequence": 1, "message": 1, "part": 1},
            )

    def test_failure_during_delete_rolls_back(self) -> None:
        # A trigger aborts the session delete; the whole transaction must roll
        # back so no partial prune remains.
        trigger = (
            "create trigger stop_delete before delete on session "
            "begin select raise(abort,'blocked'); end;"
        )
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "opencode.db"
            self.make_db(db, trigger)
            with self.assertRaises(SystemExit):
                retention.rotate(str(db), 3)
            self.assertEqual(
                self.counts(db),
                {"session": 2, "event": 2, "event_sequence": 2, "message": 2, "part": 2},
            )

    def test_missing_schema_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "empty.db"
            sqlite3.connect(db).close()
            with self.assertRaises(SystemExit):
                retention.rotate(str(db), 3)


if __name__ == "__main__":
    unittest.main()
