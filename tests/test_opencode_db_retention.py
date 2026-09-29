#!/usr/bin/env python3
"""opencode DB bounded retention のテスト (issue #389 / ADR 0002)."""
from __future__ import annotations

import importlib.util
import sqlite3
import tempfile
import time
import unittest
from unittest.mock import patch
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



class RetentionSpaceSafetyTests(OpencodeDbRetentionTests):
    def make_large_wal_db(self, db):
        self.make_db(db)
        con = sqlite3.connect(db)
        con.execute('PRAGMA journal_mode=WAL')
        con.execute('create table payload(session_id text, value blob)')
        # Use a real child table so retention removes its payload transactionally.
        con.execute('alter table part add column payload blob')
        con.execute("update part set payload=zeroblob(4194304) where session_id='old'")
        con.execute("update part set payload=zeroblob(1048576) where session_id='new'")
        con.commit()
        con.close()

    def test_wal_compaction_preserves_inode_and_recent_data(self):
        with tempfile.TemporaryDirectory() as td:
            db=Path(td)/'opencode.db'; self.make_large_wal_db(db)
            before=db.stat(); metrics={}
            self.assertEqual(retention.rotate(str(db),3,metrics=metrics),1)
            self.assertEqual(db.stat().st_ino,before.st_ino)
            self.assertLess(db.stat().st_size,before.st_size//2)
            con=sqlite3.connect(db)
            self.assertEqual(con.execute('pragma integrity_check').fetchone(),('ok',))
            self.assertEqual(con.execute('pragma journal_mode').fetchone(),('wal',))
            self.assertEqual(con.execute('select length(payload) from part').fetchone(),(1048576,))
            con.execute("insert into session values ('after',0)");con.commit();con.close()
            self.assertEqual(metrics['status'],'completed')
            self.assertEqual(metrics['deleted_sessions'],1)
            self.assertFalse(list(Path(td).glob('.opencode-retention-*')))

    def test_insufficient_delete_budget_does_not_mutate(self):
        with tempfile.TemporaryDirectory() as td:
            db=Path(td)/'opencode.db';self.make_large_wal_db(db);metrics={}
            with patch.object(retention,'available_bytes',return_value=retention.RESERVE_BYTES+1024):
                with self.assertRaises(SystemExit) as raised:
                    retention.rotate(str(db),3,metrics=metrics)
            self.assertEqual(raised.exception.code,75)
            self.assertEqual(self.counts(db)['session'],2)
            self.assertEqual(metrics['deleted_sessions'],0)
            self.assertEqual(metrics['reason'],'insufficient_space')

    def test_failed_copy_preserves_committed_prune(self):
        with tempfile.TemporaryDirectory() as td:
            db=Path(td)/'opencode.db';self.make_large_wal_db(db);metrics={}
            with patch.object(retention,'_compact',side_effect=sqlite3.OperationalError('sensitive detail')):
                with self.assertRaises(SystemExit) as raised:
                    retention.rotate(str(db),3,metrics=metrics)
            self.assertEqual(raised.exception.code,1)
            self.assertEqual(self.counts(db)['session'],1)
            self.assertEqual(metrics['deleted_sessions'],1)
            self.assertEqual(metrics['status'],'failed')
            self.assertNotIn('sensitive',str(metrics))

    def test_backup_interruption_rolls_back_and_cleans_copy(self):
        with tempfile.TemporaryDirectory() as td:
            db=Path(td)/'opencode.db';self.make_large_wal_db(db);before=db.stat().st_size;metrics={}
            def stop(*args): raise retention.Deferred('insufficient_space')
            with patch.object(retention.Budget,'backup_progress',stop):
                with self.assertRaises(SystemExit) as raised:
                    retention.rotate(str(db),3,metrics=metrics)
            self.assertEqual(raised.exception.code,75)
            self.assertEqual(db.stat().st_size,before)
            self.assertEqual(self.counts(db)['session'],1)
            con=sqlite3.connect(db);self.assertEqual(con.execute('pragma integrity_check').fetchone(),('ok',));con.close()
            self.assertFalse(list(Path(td).glob('.opencode-retention-*')))

    def test_exclusive_sqlite_lock_blocks_ungated_writer_during_copy(self):
        original=retention._compact
        def verify(con,db,budget,metrics):
            other=sqlite3.connect(db,timeout=0.01)
            try:
                with self.assertRaises(sqlite3.OperationalError):
                    other.execute("insert into session values ('ungated',0)")
            finally:other.close()
            return original(con,db,budget,metrics)
        with tempfile.TemporaryDirectory() as td:
            db=Path(td)/'opencode.db';self.make_large_wal_db(db)
            with patch.object(retention,'_compact',verify):retention.rotate(str(db),3)

    def test_live_budget_guard_interrupts_delete_and_rolls_back(self):
        with tempfile.TemporaryDirectory() as td:
            db=Path(td)/'opencode.db';self.make_large_wal_db(db);metrics={}
            real=retention.available_bytes
            def free(path):
                return 0 if metrics.get('stage')=='delete' else real(path)
            with patch.object(retention,'available_bytes',free):
                with self.assertRaises(SystemExit) as raised:retention.rotate(str(db),3,metrics=metrics)
            self.assertEqual(raised.exception.code,75)
            self.assertEqual(self.counts(db)['session'],2)
            self.assertEqual(metrics['deleted_sessions'],0)

    def test_compaction_can_retry_after_prune_already_committed(self):
        with tempfile.TemporaryDirectory() as td:
            db=Path(td)/'opencode.db';self.make_large_wal_db(db)
            with patch.object(retention,'_compact',side_effect=retention.Deferred('insufficient_space')):
                with self.assertRaises(SystemExit):retention.rotate(str(db),3)
            metrics={};self.assertEqual(retention.rotate(str(db),3,metrics=metrics),0)
            self.assertEqual(metrics['freelist_count'],0)
            self.assertLess(metrics['after_bytes'],metrics['before_bytes']//2)

    def test_symlink_and_missing_db_fail_without_creating_live_database(self):
        with tempfile.TemporaryDirectory() as td:
            db=Path(td)/'opencode.db';link=Path(td)/'link.db';self.make_db(db);link.symlink_to(db)
            for invalid in (link,Path(td)/'missing.db'):
                with self.assertRaises(SystemExit):retention.rotate(str(invalid),3)
            self.assertEqual(self.counts(db)['session'],2)
            self.assertFalse((Path(td)/'missing.db').exists())


if __name__ == '__main__':
    unittest.main()
