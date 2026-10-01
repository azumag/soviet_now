#!/usr/bin/env python3
"""opencode DB bounded retention のテスト (issue #389 / ADR 0002)."""
from __future__ import annotations

import importlib.util
import json
from contextlib import contextmanager
import os
import resource
import signal
import sqlite3
import subprocess
import sys
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

    def test_pressure_batches_prune_when_full_wal_budget_does_not_fit(self):
        real = retention._wal_budget
        calls = {'n': 0}

        def wrapped(pages):
            calls['n'] += 1
            if calls['n'] == 1:
                return 10**15
            return real(pages)

        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / 'opencode.db'
            self.make_large_wal_db(db)
            before = db.stat()
            metrics = {}
            with patch.object(retention, '_wal_budget', wrapped):
                self.assertEqual(retention.rotate(str(db), 3, metrics=metrics), 1)
            self.assertEqual(metrics['status'], 'completed')
            self.assertEqual(metrics['reason'], 'ok')
            self.assertEqual(metrics['deleted_sessions'], 1)
            self.assertEqual(metrics['remaining_sessions'], 0)
            self.assertEqual(metrics['prune_batches'], 1)
            self.assertEqual(self.counts(db)['session'], 1)
            self.assertEqual(db.stat().st_ino, before.st_ino)
            self.assertLess(db.stat().st_size, before.st_size // 2)
            con = sqlite3.connect(db)
            self.assertEqual(con.execute('pragma integrity_check').fetchone(), ('ok',))
            self.assertEqual(con.execute('select length(payload) from part').fetchone(), (1048576,))
            con.close()

    def test_partial_batch_failure_refreshes_post_commit_metrics(self):
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / 'opencode.db'
            self.make_db(db)
            con = sqlite3.connect(db)
            now = int(time.time() * 1000)
            for n in range(12):
                con.execute(
                    'insert into session values (?,?)',
                    (f'old-extra-{n}', now - 10 * 86400000),
                )
            con.commit()
            con.close()

            metrics = {}
            real_wal_budget = retention._wal_budget
            wal_calls = {'n': 0}

            def force_bounded(pages):
                wal_calls['n'] += 1
                if wal_calls['n'] == 1:
                    return 10**15
                return real_wal_budget(pages)

            real_checkpoint = retention._checkpoint
            injected = {'hit': False}

            def fail_after_first_commit(connection):
                if (
                    metrics.get('deleted_sessions') == retention.PRUNE_BATCH_SESSIONS
                    and metrics.get('prune_batches') == 1
                ):
                    injected['hit'] = True
                    raise sqlite3.OperationalError('synthetic checkpoint failure')
                return real_checkpoint(connection)

            with patch.object(retention, '_wal_budget', force_bounded), \
                 patch.object(retention, '_checkpoint', fail_after_first_commit):
                with self.assertRaises(SystemExit) as raised:
                    retention.rotate(str(db), 3, metrics=metrics)

            self.assertTrue(injected['hit'])
            self.assertIn(raised.exception.code, (1, retention.DEFERRED))
            self.assertEqual(metrics['deleted_sessions'], retention.PRUNE_BATCH_SESSIONS)
            self.assertEqual(metrics['remaining_sessions'], 5)
            self.assertIn(metrics['status'], ('failed', 'deferred'))

            con = sqlite3.connect(db)
            cutoff = int(time.time() * 1000) - 3 * 86400000
            self.assertEqual(
                con.execute(
                    'select count(*) from session where time_created < ?', (cutoff,)
                ).fetchone(),
                (5,),
            )
            self.assertEqual(con.execute('pragma integrity_check').fetchone(), ('ok',))
            pages = retention._pages(con)
            con.close()
            for key in ('page_size', 'page_count', 'freelist_count'):
                self.assertEqual(metrics[key], pages[key])

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
        def verify(con,db,budget,metrics,db_fd=None):
            other=sqlite3.connect(db,timeout=0.01)
            try:
                with self.assertRaises(sqlite3.OperationalError):
                    other.execute("insert into session values ('ungated',0)")
            finally:other.close()
            return original(con,db,budget,metrics,db_fd)
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

    def test_progress_callback_interrupts_an_active_delete_transaction(self):
        with tempfile.TemporaryDirectory() as td:
            db=Path(td)/'opencode.db';self.make_large_wal_db(db)
            con=sqlite3.connect(db)
            con.execute('create table todo(session_id text, value integer)')
            con.executemany("insert into todo values ('old',?)",[(n,) for n in range(1000)])
            con.commit();con.close();metrics={}
            real=retention.Budget.progress
            def interrupt(budget):
                if metrics.get('stage')=='delete':
                    budget.reason='insufficient_space'
                    return 1
                return real(budget)
            with patch.object(retention.Budget,'progress',interrupt):
                with self.assertRaises(SystemExit) as raised:retention.rotate(str(db),3,metrics=metrics)
            self.assertEqual(raised.exception.code,75)
            self.assertEqual(self.counts(db)['session'],2)
            con=sqlite3.connect(db);self.assertEqual(con.execute('select count(*) from todo').fetchone(),(1000,));con.close()

    def test_writeback_budget_failure_cleans_copy_and_keeps_database(self):
        with tempfile.TemporaryDirectory() as td:
            db=Path(td)/'opencode.db';self.make_large_wal_db(db);metrics={}
            with patch.object(retention,'_wal_budget',side_effect=[10000000,10**18]):
                with self.assertRaises(SystemExit) as raised:retention.rotate(str(db),3,metrics=metrics)
            self.assertEqual(raised.exception.code,75)
            self.assertEqual(metrics['stage'],'compact_writeback')
            self.assertEqual(self.counts(db)['session'],1)
            self.assertFalse(list(Path(td).glob('.opencode-retention-*')))

    def test_compaction_can_retry_after_prune_already_committed(self):
        with tempfile.TemporaryDirectory() as td:
            db=Path(td)/'opencode.db';self.make_large_wal_db(db)
            with patch.object(retention,'_compact',side_effect=retention.Deferred('insufficient_space')):
                with self.assertRaises(SystemExit):retention.rotate(str(db),3)
            metrics={};self.assertEqual(retention.rotate(str(db),3,metrics=metrics),0)
            self.assertEqual(metrics['freelist_count'],0)
            self.assertLess(metrics['after_bytes'],metrics['before_bytes']//2)

    def test_memory_copy_avoids_consuming_database_filesystem(self):
        with tempfile.TemporaryDirectory() as td, tempfile.TemporaryDirectory() as ram:
            db=Path(td)/'opencode.db';self.make_large_wal_db(db);metrics={}
            before=db.stat()
            real=retention._wal_budget
            calls={'n':0}
            def budget_wrap(pages):
                calls['n']+=1
                if calls['n']==1:
                    # Keep the delete itself on the non-bounded path; the
                    # remaining checks use the real estimates.
                    return 1024
                return real(pages)
            # Live-image budget: the live output fits in RAM and its measured
            # writeback WAL fits on the database filesystem, while a second
            # full image on disk would not.
            space=retention.RESERVE_BYTES+int(1.6*1024*1024)
            with patch.object(retention,'available_bytes',return_value=space), \
                 patch.object(retention,'_wal_budget',budget_wrap), \
                 patch.object(retention,'memory_copy_root',return_value=Path(ram)), \
                 patch.object(retention,'memory_headroom',return_value=10**12):
                retention.rotate(str(db),3,metrics=metrics)
            self.assertEqual(metrics['compact_storage'],'memory')
            self.assertEqual(metrics['status'],'completed')
            self.assertEqual(db.stat().st_ino,before.st_ino)
            self.assertLess(db.stat().st_size,before.st_size//2)
            self.assertEqual(self.counts(db)['session'],1)
            self.assertEqual(list(Path(ram).iterdir()),[])

    def test_memory_pressure_interrupts_copy_and_cleans_private_ram_directory(self):
        with tempfile.TemporaryDirectory() as td, tempfile.TemporaryDirectory() as ram:
            db=Path(td)/'opencode.db';self.make_large_wal_db(db);metrics={}
            real=retention._wal_budget
            calls={'n':0}
            def budget_wrap(pages):
                calls['n']+=1
                if calls['n']==1:
                    return 1024
                return real(pages)
            space=retention.RESERVE_BYTES+int(1.6*1024*1024)
            def available_ram():
                return 0 if metrics.get('stage')=='compact_copy' else 10**12
            with patch.object(retention,'available_bytes',return_value=space), \
                 patch.object(retention,'_wal_budget',budget_wrap), \
                 patch.object(retention,'memory_copy_root',return_value=Path(ram)), \
                 patch.object(retention,'memory_headroom',side_effect=available_ram):
                with self.assertRaises(SystemExit) as raised:retention.rotate(str(db),3,metrics=metrics)
            self.assertEqual(raised.exception.code,75)
            self.assertEqual(metrics['reason'],'insufficient_memory')
            self.assertEqual(self.counts(db)['session'],1)
            self.assertEqual(list(Path(ram).iterdir()),[])
            con=sqlite3.connect(db);self.assertEqual(con.execute('pragma integrity_check').fetchone(),('ok',));con.close()

    def test_cgroup_memory_limits_include_ancestor_headroom(self):
        for version in ('v1','v2'):
            with self.subTest(version=version), tempfile.TemporaryDirectory() as td:
                base=Path(td);proc=base/'proc';cg=base/'cgroup'
                (proc/'self').mkdir(parents=True)
                (proc/'meminfo').write_text('MemAvailable: 10000000 kB\n')
                if version=='v2':
                    (proc/'self/cgroup').write_text('0::/parent/child\n')
                    names=('memory.max','memory.current');mount=cg
                else:
                    (proc/'self/cgroup').write_text('7:memory:/parent/child\n')
                    names=('memory.limit_in_bytes','memory.usage_in_bytes');mount=cg/'memory'
                (mount/'parent/child').mkdir(parents=True)
                for group,limit,current in ((mount,9000000000,1000000000),(mount/'parent',7000000000,3000000000),(mount/'parent/child',6000000000,1000000000)):
                    (group/names[0]).write_text(str(limit));(group/names[1]).write_text(str(current))
                self.assertEqual(retention.memory_headroom(proc,cg),4000000000)

    def test_unknown_cgroup_memory_limits_fail_closed(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);(root/'self').mkdir()
            (root/'meminfo').write_text('MemAvailable: 10000000 kB\n')
            (root/'self/cgroup').write_text('0::/missing\n')
            with self.assertRaises(ValueError):retention.memory_headroom(root,root/'cg')

    def test_prior_freelist_prunes_before_compaction_under_pressure(self):
        with tempfile.TemporaryDirectory() as td, tempfile.TemporaryDirectory() as ram:
            db=Path(td)/'opencode.db';self.make_large_wal_db(db)
            con=sqlite3.connect(db)
            con.execute('create table old_scratch(value blob)')
            con.execute('insert into old_scratch values (zeroblob(4194304))');con.commit()
            con.execute('drop table old_scratch');con.commit();con.close()
            before=db.stat();metrics={}
            # The unrelated freelist never blocks the guarded prune; once the
            # eligible rows are gone the live image fits the pressure budget
            # and the freed pages are returned to the filesystem.
            with patch.object(retention,'available_bytes',return_value=retention.RESERVE_BYTES+9*1024**2), \
                 patch.object(retention,'memory_copy_root',return_value=Path(ram)), \
                 patch.object(retention,'memory_headroom',return_value=10**12):
                retention.rotate(str(db),3,metrics=metrics)
            self.assertEqual(metrics['status'],'completed')
            self.assertEqual(metrics['deleted_sessions'],1)
            self.assertLess(db.stat().st_size,before.st_size//2)
            self.assertEqual(db.stat().st_ino,before.st_ino)
            self.assertEqual(self.counts(db)['session'],1)
            self.assertEqual(list(Path(ram).iterdir()),[])
            con=sqlite3.connect(db)
            self.assertEqual(con.execute('pragma integrity_check').fetchone(),('ok',))
            con.close()

    def test_memory_copy_requires_explicit_opt_in(self):
        with patch.dict(retention.os.environ,{},clear=True):
            self.assertIsNone(retention.memory_copy_root(Path('/db/opencode.db'),1))

    def test_symlink_and_missing_db_fail_without_creating_live_database(self):
        with tempfile.TemporaryDirectory() as td:
            db=Path(td)/'opencode.db';link=Path(td)/'link.db';self.make_db(db);link.symlink_to(db)
            for invalid in (link,Path(td)/'missing.db'):
                with self.assertRaises(SystemExit):retention.rotate(str(invalid),3)
            self.assertEqual(self.counts(db)['session'],2)
            self.assertFalse((Path(td)/'missing.db').exists())


class HighWaterWalTests(unittest.TestCase):
    make_db = OpencodeDbRetentionTests.make_db
    counts = OpencodeDbRetentionTests.counts
    make_large_wal_db = RetentionSpaceSafetyTests.make_large_wal_db

    @classmethod
    def setUpClass(cls):
        con=sqlite3.connect(':memory:')
        print('WAL fixture runtime: platform=%s SQLite=%s secure_delete_default=%s' %
              (sys.platform,sqlite3.sqlite_version,con.execute('pragma secure_delete').fetchone()[0]))
        con.close()

    def fixture(self, db):
        self.make_large_wal_db(db)
        con = sqlite3.connect(db)
        con.execute("update session set time_created=? where id='new'", (int(time.time()*1000)-3600000,))
        con.execute("update part set payload=zeroblob(16777216) where session_id='new'")
        # Index maintenance and event payloads are inside the same WAL ceiling.
        con.execute('create index part_session on part(session_id)')
        con.execute('create index message_session on message(session_id)')
        con.execute('alter table event add column payload blob')
        con.execute("update event set payload=zeroblob(1048576) where aggregate_id='old'")
        for table in retention.CHILD_TABLES:
            con.execute('create table %s(session_id text, value integer)' % table)
            con.executemany('insert into %s values (?,1)' % table,[('old',),('new',)])
        con.commit(); con.close()

    @contextmanager
    def pressure(self, amount=12*1024**2):
        connect=sqlite3.connect
        def erasing_connect(*args, **kwargs):
            con=connect(*args, **kwargs)
            # Explicitly dirty overflow pages on DELETE on builds where the
            # default secure_delete is OFF; a blob alone need not grow WAL.
            con.execute('pragma secure_delete=on')
            return con
        with patch.object(retention, 'available_bytes', return_value=retention.RESERVE_BYTES+amount), \
             patch.object(retention.sqlite3,'connect',erasing_connect):
            yield

    def check_intact(self, db, expected=2):
        self.assertEqual(self.counts(db)['session'], expected)
        con = sqlite3.connect(db)
        self.assertEqual(con.execute('pragma integrity_check').fetchone(), ('ok',))
        self.assertEqual(con.execute('pragma foreign_key_check').fetchall(), [])
        self.assertEqual(con.execute("select length(payload) from part where session_id='new'").fetchone(), (16777216,))
        con.execute("insert into session values ('writer-returned',?)", (int(time.time()*1000),))
        con.commit(); con.close()

    def test_bounded_prune_retains_one_day_and_inode_without_claiming_shrink(self):
        with tempfile.TemporaryDirectory() as td:
            db=Path(td)/'db'; self.fixture(db); before=db.stat(); metrics={}
            limits=resource.getrlimit(resource.RLIMIT_FSIZE); sig=signal.getsignal(signal.SIGXFSZ)
            checkpoint=retention._checkpoint
            observed=[]
            def verify(con):
                self.assertEqual(resource.getrlimit(resource.RLIMIT_FSIZE), limits)
                self.assertEqual(signal.getsignal(signal.SIGXFSZ), sig)
                if metrics.get('deleted_sessions'):
                    self.assertEqual(con.execute('pragma wal_autocheckpoint').fetchone(), (1000,))
                    observed.append(Path(str(db)+'-wal').stat().st_size)
                checkpoint(con)
            with self.pressure(), patch.object(retention, '_checkpoint', verify):
                with self.assertRaises(SystemExit) as raised:retention.rotate(db,1,metrics=metrics)
            self.assertEqual(raised.exception.code,75)
            self.assertEqual(metrics['reason'],'bounded_prune_committed')
            self.assertEqual(metrics['deleted_sessions'],1)
            self.assertGreater(metrics['freelist_count'],0)
            self.assertTrue(observed)
            self.assertLessEqual(max(observed),metrics['wal_limit_bytes'])
            self.assertEqual(db.stat().st_ino,before.st_ino)
            self.assertEqual(db.stat().st_size,before.st_size)
            self.check_intact(db,1)
            # Result JSON has no leftover process file-size ceiling.
            result=Path(td)/'result';retention.write_result(result,metrics)
            self.assertTrue(result.is_file())

    def test_huge_blob_efbig_preserves_every_child_and_restores_process(self):
        with tempfile.TemporaryDirectory() as td:
            db=Path(td)/'db';self.fixture(db); before=self.counts(db);metrics={}
            limits=resource.getrlimit(resource.RLIMIT_FSIZE);sig=signal.getsignal(signal.SIGXFSZ)
            with self.pressure(6*1024**2):
                with self.assertRaises(SystemExit):retention.rotate(db,1,metrics=metrics)
            self.assertEqual(metrics['reason'],'bounded_prune_io_error')
            self.assertTrue(metrics['bounded_prune_blocked'])
            self.assertEqual(metrics['recovery_action'],'inspect_io_or_add_capacity')
            self.assertEqual(metrics['deleted_sessions'],0)
            self.assertEqual(self.counts(db),before)
            self.assertEqual(resource.getrlimit(resource.RLIMIT_FSIZE),limits)
            self.assertEqual(signal.getsignal(signal.SIGXFSZ),sig)
            con=sqlite3.connect(db)
            self.assertEqual(con.execute("select length(payload) from part where session_id='old'").fetchone(),(4194304,))
            self.assertEqual(con.execute("select length(payload) from event where aggregate_id='old'").fetchone(),(1048576,))
            for table in retention.CHILD_TABLES:
                self.assertEqual(con.execute('select count(*) from '+table).fetchone(),(2,))
            con.close();self.check_intact(db)

    def test_trigger_amplification_and_cache_spill_are_capped(self):
        with tempfile.TemporaryDirectory() as td:
            db=Path(td)/'db';self.fixture(db);con=sqlite3.connect(db)
            con.executescript("create table amplified(value blob); create trigger amplify before delete on part begin insert into amplified values(zeroblob(16777216)); end;")
            con.close();metrics={}
            with self.pressure():
                with self.assertRaises(SystemExit):retention.rotate(db,1,metrics=metrics)
            self.assertEqual(metrics['deleted_sessions'],0)
            self.assertEqual(metrics['reason'],'bounded_prune_io_error')
            con=sqlite3.connect(db);self.assertEqual(con.execute('select count(*) from amplified').fetchone(),(0,));con.close()
            self.check_intact(db)

    def test_bounded_catchup_drains_eligible_one_transaction_per_batch(self):
        with tempfile.TemporaryDirectory() as td:
            db=Path(td)/'db';self.fixture(db);con=sqlite3.connect(db)
            for n in range(20):con.execute('insert into session values (?,0)',('extra-%02d'%n,))
            con.commit();con.close();metrics={}
            with self.pressure():
                with self.assertRaises(SystemExit):retention.rotate(db,1,metrics=metrics)
            self.assertEqual(metrics['eligible_sessions'],21)
            # 8+8+5 in three guarded transactions; every batch commits or rolls
            # back on its own and the deferred outcome keeps the live image
            # (16 MiB 'new' payload) outside the 12 MiB pressure budget.
            self.assertEqual(metrics['prune_batches'],3)
            self.assertEqual(metrics['selected_sessions'],21)
            self.assertEqual(metrics['deleted_sessions'],21)
            self.assertEqual(metrics['remaining_sessions'],0)
            self.assertEqual(metrics['reason'],'bounded_prune_committed')
            self.assertEqual(self.counts(db)['session'],1)

    def test_prior_freelist_does_not_block_next_bounded_prune(self):
        with tempfile.TemporaryDirectory() as td:
            db=Path(td)/'db';self.fixture(db)
            for iteration in range(2):
                if iteration:
                    con=sqlite3.connect(db);con.execute("insert into session values ('next-old',0)");con.commit();con.close()
                metrics={}
                with self.pressure():
                    with self.assertRaises(SystemExit):retention.rotate(db,1,metrics=metrics)
                self.assertEqual(metrics['deleted_sessions'],1)
                self.assertEqual(metrics['reason'],'bounded_prune_committed')

    def test_ram_copy_available_but_writeback_insufficient_does_not_block_prune(self):
        with tempfile.TemporaryDirectory() as td, tempfile.TemporaryDirectory() as ram:
            db=Path(td)/'db';self.fixture(db)
            with self.pressure(),self.assertRaises(SystemExit):retention.rotate(db,1)
            con=sqlite3.connect(db);pages=retention._pages(con)
            self.assertGreater(pages['freelist_count'],0)
            self.assertGreater((pages['page_count']-pages['freelist_count'])*pages['page_size'],12*1024**2)
            con.close()
            with self.pressure(),patch.object(retention,'memory_copy_root',return_value=Path(ram)), \
                 patch.object(retention,'memory_headroom',return_value=10**12):
                # Prove the compact copy can be built but measured writeback
                # fails. No mutations of the live DB have committed here.
                con=sqlite3.connect(db);con.isolation_level=None;metrics={}
                try:
                    with self.assertRaises(retention.Deferred):
                        retention._compact(con,db,retention.Budget(db.parent),metrics)
                    self.assertEqual(metrics['stage'],'compact_writeback')
                finally:con.close()
                con=sqlite3.connect(db);con.execute("insert into session values ('next-old',0)");con.commit();con.close()
                metrics={}
                with patch.object(retention,'_compact',side_effect=retention.Deferred('insufficient_space')) as compact:
                    with self.assertRaises(SystemExit):retention.rotate(db,1,metrics=metrics)
                # The prune commits first; a later compaction failure only
                # defers and never rolls the committed rows back.
                compact.assert_called_once()
                self.assertEqual(metrics['deleted_sessions'],1)
                self.assertEqual(metrics['reason'],'bounded_prune_committed')
                self.assertEqual(metrics['compact_defer_reason'],'insufficient_space')
            self.assertEqual(list(Path(ram).iterdir()),[])
            self.check_intact(db,1)

    def test_bounded_catchup_stops_at_batch_cap_and_defers(self):
        with tempfile.TemporaryDirectory() as td:
            db=Path(td)/'db';self.fixture(db);con=sqlite3.connect(db)
            for n in range(20):con.execute('insert into session values (?,0)',('extra-%02d'%n,))
            con.commit();con.close();metrics={}
            with self.pressure(),patch.object(retention,'CATCHUP_MAX_BATCHES',2):
                with self.assertRaises(SystemExit) as raised:retention.rotate(db,1,metrics=metrics)
            self.assertEqual(raised.exception.code,75)
            self.assertEqual(metrics['prune_batches'],2)
            self.assertEqual(metrics['deleted_sessions'],16)
            self.assertEqual(metrics['remaining_sessions'],5)
            self.assertEqual(metrics['reason'],'bounded_prune_committed')
            self.assertNotIn('compact_defer_reason',metrics)
            self.assertEqual(self.counts(db)['session'],6)

    def test_wal_ceiling_covers_multi_hundred_megabyte_sessions(self):
        with tempfile.TemporaryDirectory() as td:
            db=Path(td)/'db';self.fixture(db)
            con=sqlite3.connect(db);con.isolation_level=None
            retention._checkpoint(con)
            budget=retention.Budget(db.parent,retention.RESERVE_BYTES)
            metrics={}
            limits=resource.getrlimit(resource.RLIMIT_FSIZE)
            # With ample free space the per-batch ceiling must stay large
            # enough for a session whose child rows alone are hundreds of MB;
            # the old 128 MiB value made such a session block catch-up.
            self.assertEqual(retention.PRUNE_WAL_LIMIT,1024**3)
            available=retention.RESERVE_BYTES+retention.PRUNE_WAL_LIMIT+64*1024**2
            with patch.object(retention,'available_bytes',return_value=available):
                with retention._bounded_wal(con,db,budget,metrics):
                    pass
            self.assertGreaterEqual(metrics['wal_limit_bytes'],512*1024**2)
            self.assertLessEqual(metrics['wal_limit_bytes'],retention.PRUNE_WAL_LIMIT)
            self.assertEqual(resource.getrlimit(resource.RLIMIT_FSIZE),limits)
            con.close()

    def test_oversized_session_is_isolated_and_catchup_continues(self):
        with tempfile.TemporaryDirectory() as td:
            db=Path(td)/'db';self.fixture(db);con=sqlite3.connect(db)
            # 'old' carries the multi-megabyte payload at rank one; ten tiny
            # sessions with their own child rows queue behind it, seven of
            # them inside its first batch (their freed pages must give the
            # post-sweep compaction something to reclaim).
            con.execute("update session set time_created=-1 where id='old'")
            for n in range(10):
                con.execute('insert into session values (?,0)',('zextra-%02d'%n,))
                con.execute('insert into part values (?,?,?,zeroblob(65536))',
                            ('pz-%02d'%n,'mz-%02d'%n,'zextra-%02d'%n))
            con.commit();con.close();metrics={}
            with self.pressure(6*1024**2):
                with self.assertRaises(SystemExit) as raised:retention.rotate(db,1,metrics=metrics)
            self.assertEqual(raised.exception.code,75)
            # The failed batch is retried one session per transaction, so only
            # the oversized session is excluded; its seven neighbours and the
            # rest of the backlog are still pruned in this run.
            self.assertEqual(metrics['skipped_sessions'],1)
            self.assertEqual(metrics['skipped_batches'],2)
            self.assertEqual(metrics['deleted_sessions'],10)
            self.assertEqual(metrics['remaining_sessions'],1)
            self.assertEqual(metrics['reason'],'bounded_prune_committed')
            self.assertEqual(metrics['compact_defer_reason'],'insufficient_space')
            self.assertIn(metrics.get('sqlite_error_code'),(sqlite3.SQLITE_IOERR,sqlite3.SQLITE_FULL))
            con=sqlite3.connect(db)
            self.assertEqual(con.execute("select length(payload) from part where session_id='old'").fetchone(),(4194304,))
            self.assertEqual(con.execute("select count(*) from session where id like 'zextra-%'").fetchone(),(0,))
            self.assertEqual(con.execute("select count(*) from part where session_id like 'zextra-%'").fetchone(),(0,))
            con.close();self.check_intact(db)

    def test_live_image_budget_compacts_after_catchup_under_pressure(self):
        with tempfile.TemporaryDirectory() as td:
            db=Path(td)/'db';self.make_large_wal_db(db)
            con=sqlite3.connect(db)
            con.execute('create table scratch(value blob)')
            con.execute('insert into scratch values (zeroblob(20971520))');con.commit()
            con.execute('drop table scratch');con.commit();con.close()
            before=db.stat();metrics={}
            # Full image (~25 MiB) cannot fit the 14 MiB slack, but the live
            # image after catch-up (~1 MiB) can: only live-image budgeting
            # reclaims freed pages on this path.
            with self.pressure(14*1024**2):
                retention.rotate(db,3,metrics=metrics)
            self.assertEqual(metrics['status'],'completed')
            self.assertEqual(metrics['deleted_sessions'],1)
            self.assertEqual(metrics['remaining_sessions'],0)
            self.assertLess(db.stat().st_size,before.st_size//2)
            self.assertEqual(db.stat().st_ino,before.st_ino)
            self.assertEqual(self.counts(db)['session'],1)
            con=sqlite3.connect(db)
            self.assertEqual(con.execute('pragma integrity_check').fetchone(),('ok',))
            con.close()
            self.assertFalse(list(Path(td).glob('.opencode-retention-*')))

    def test_nonzero_wal_is_checkpointed_before_installing_limit(self):
        with tempfile.TemporaryDirectory() as td:
            db=Path(td)/'db';self.fixture(db)
            con=sqlite3.connect(db)
            con.execute('pragma wal_autocheckpoint=0')
            con.execute("insert into session values ('recent-pending-wal',?)",(int(time.time()*1000),))
            con.commit();self.assertGreater(Path(str(db)+'-wal').stat().st_size,0)
            # Closing this connection checkpoints; use the helper directly to
            # prove an uncheckpointed WAL is rejected before any mutation.
            metrics={};budget=retention.Budget(db.parent)
            with self.pressure(), self.assertRaises(retention.Deferred) as raised:
                with retention._bounded_wal(con,db,budget,metrics):self.fail('uncheckpointed WAL admitted')
            self.assertEqual(str(raised.exception),'checkpoint_busy')
            con.close()
            with self.pressure(), self.assertRaises(SystemExit):retention.rotate(db,1,metrics=metrics)
            self.assertEqual(metrics['deleted_sessions'],1)
            self.assertEqual(self.counts(db)['session'],2)

    def test_commit_write_failure_rolls_back_and_measures_kernel_ceiling(self):
        with tempfile.TemporaryDirectory() as td:
            db=Path(td)/'db';self.fixture(db);con=sqlite3.connect(db)
            con.isolation_level=None;con.execute('pragma cache_spill=off');con.execute('pragma secure_delete=on')
            retention._checkpoint(con);metrics={};budget=retention.Budget(db.parent)
            with self.pressure(6*1024**2):
                with self.assertRaises(retention.Deferred):
                    with retention._bounded_wal(con,db,budget,metrics):
                        con.execute('begin immediate')
                        con.execute("delete from part where session_id='old'")
                        # cache spill is off: the cap failure is at COMMIT.
                        self.assertEqual(Path(str(db)+'-wal').stat().st_size,0)
                        con.execute('commit')
            self.assertLessEqual(Path(str(db)+'-wal').stat().st_size,metrics['wal_limit_bytes'])
            if con.in_transaction:con.execute('rollback')
            self.assertEqual(con.execute('pragma wal_autocheckpoint').fetchone(),(1000,))
            con.close();self.check_intact(db)

    def test_sigkill_during_uncommitted_prune_recovers_every_row(self):
        with tempfile.TemporaryDirectory() as td:
            db=Path(td)/'db';self.fixture(db);ready=Path(td)/'ready'
            code='''import importlib.util,sqlite3,sys,time,os
from pathlib import Path
s=importlib.util.spec_from_file_location('r',sys.argv[1]);r=importlib.util.module_from_spec(s);s.loader.exec_module(r)
db=Path(sys.argv[2]);con=sqlite3.connect(db);con.isolation_level=None;con.execute('pragma cache_size=10');con.execute('pragma secure_delete=on');r._checkpoint(con)
with r._bounded_wal(con,db,r.Budget(db.parent),{}):
 con.execute('begin immediate');con.execute("delete from part where session_id='old'")
 Path(sys.argv[3]).touch();time.sleep(30)
'''
            proc=subprocess.Popen([sys.executable,'-c',code,str(ROOT/'lib/opencode_db_retention.py'),str(db),str(ready)])
            try:
                deadline=time.monotonic()+10
                while not ready.exists() and proc.poll() is None and time.monotonic()<deadline:time.sleep(.02)
                self.assertTrue(ready.exists())
                self.assertGreater(Path(str(db)+'-wal').stat().st_size,0)
                proc.kill();proc.wait(timeout=5)
                self.assertEqual(self.counts(db)['part'],2)
                self.check_intact(db)
            finally:
                if proc.poll() is None:proc.kill();proc.wait(timeout=5)

    def test_nondefault_soft_limit_signal_and_autocheckpoint_are_restored(self):
        with tempfile.TemporaryDirectory() as td:
            db=Path(td)/'db';self.fixture(db);con=sqlite3.connect(db)
            retention._checkpoint(con);con.execute('pragma wal_autocheckpoint=37')
            original=resource.getrlimit(resource.RLIMIT_FSIZE);sig=signal.getsignal(signal.SIGXFSZ)
            def handler(*args):pass
            try:
                resource.setrlimit(resource.RLIMIT_FSIZE,(32*1024**2,original[1]))
                signal.signal(signal.SIGXFSZ,handler)
                expected=resource.getrlimit(resource.RLIMIT_FSIZE)
                with self.pressure():
                    with self.assertRaises(RuntimeError):
                        with retention._bounded_wal(con,db,retention.Budget(db.parent),{}):
                            self.assertEqual(resource.getrlimit(resource.RLIMIT_FSIZE)[1],expected[1])
                            self.assertEqual(signal.getsignal(signal.SIGXFSZ),signal.SIG_IGN)
                            raise RuntimeError('interrupted')
                self.assertEqual(resource.getrlimit(resource.RLIMIT_FSIZE),expected)
                self.assertIs(signal.getsignal(signal.SIGXFSZ),handler)
                self.assertEqual(con.execute('pragma wal_autocheckpoint').fetchone(),(37,))
            finally:
                resource.setrlimit(resource.RLIMIT_FSIZE,original)
                signal.signal(signal.SIGXFSZ,sig);con.close()

    def test_reserve_or_limit_install_failure_never_begins_delete(self):
        with tempfile.TemporaryDirectory() as td:
            db=Path(td)/'db';self.fixture(db);metrics={}
            with self.pressure(retention.PRUNE_OVERHEAD_BYTES+retention.PRUNE_MIN_WAL_BYTES-4096):
                with self.assertRaises(SystemExit):retention.rotate(db,1,metrics=metrics)
            self.assertEqual(metrics['deleted_sessions'],0)
            self.assertEqual(metrics['reason'],'insufficient_space')
            limits=resource.getrlimit(resource.RLIMIT_FSIZE);sig=signal.getsignal(signal.SIGXFSZ)
            setter=resource.setrlimit
            def reject_lower(which, value):
                if value != limits:raise OSError('limit unavailable')
                return setter(which,value)
            with self.pressure(),patch.object(retention.resource,'setrlimit',reject_lower):
                with self.assertRaises(SystemExit):retention.rotate(db,1,metrics=metrics)
            self.assertEqual(metrics['deleted_sessions'],0)
            self.assertEqual(resource.getrlimit(resource.RLIMIT_FSIZE),limits)
            self.assertEqual(signal.getsignal(signal.SIGXFSZ),sig)
            self.check_intact(db)

    def test_single_thread_contract_fails_closed(self):
        with tempfile.TemporaryDirectory() as td:
            db=Path(td)/'db';self.fixture(db);metrics={}
            with self.pressure(),patch.object(retention.threading,'active_count',return_value=2):
                with self.assertRaises(SystemExit):retention.rotate(db,1,metrics=metrics)
            self.assertEqual(metrics['reason'],'wal_limit_unavailable')
            self.assertEqual(metrics['deleted_sessions'],0)
            self.check_intact(db)

    def test_ungated_writer_cannot_enter_bounded_transaction(self):
        guard=retention._bounded_wal
        @contextmanager
        def verified(con,db,budget,metrics):
            with guard(con,db,budget,metrics):
                other=sqlite3.connect(db,timeout=.01)
                try:
                    with self.assertRaises(sqlite3.OperationalError):other.execute("insert into session values ('ungated',0)")
                finally:other.close()
                yield
        with tempfile.TemporaryDirectory() as td:
            db=Path(td)/'db';self.fixture(db);metrics={}
            with self.pressure(),patch.object(retention,'_bounded_wal',verified):
                with self.assertRaises(SystemExit):retention.rotate(db,1,metrics=metrics)
            self.assertEqual(metrics['deleted_sessions'],1)
            self.check_intact(db,1)


class RetentionPreflightDiagnosticsTests(unittest.TestCase):
    make_db = OpencodeDbRetentionTests.make_db
    counts = OpencodeDbRetentionTests.counts

    @staticmethod
    def sqlite_error(code):
        error = sqlite3.OperationalError('private SQL/DB/argv must not be recorded')
        error.sqlite_errorcode = code
        return error

    def test_each_sqlite_preflight_phase_keeps_busy_classification_and_rows(self):
        statements = {
            'busy_timeout': 'PRAGMA busy_timeout=30000',
            'temp_store': 'PRAGMA temp_store=MEMORY',
            'synchronous': 'PRAGMA synchronous=FULL',
            'locking_mode': 'PRAGMA locking_mode=EXCLUSIVE',
            'begin_exclusive': 'BEGIN EXCLUSIVE',
            'commit_exclusive': 'COMMIT',
            'journal_mode': 'PRAGMA journal_mode',
            'eligible_count': 'select count(*) from session where time_created < ?',
        }
        phases = ['connect', *statements, 'checkpoint', 'pages', 'delete_budget']
        # BUSY, BUSY_RECOVERY, BUSY_SNAPSHOT, LOCKED, LOCKED_SHAREDCACHE.
        for phase in phases:
            for code in (5, 261, 517, 6, 262):
                with self.subTest(phase=phase, code=code), tempfile.TemporaryDirectory() as td:
                    db = Path(td) / 'db'; self.make_db(db); metrics = {}
                    connect = sqlite3.connect
                    pages = retention._pages
                    calls = 0
                    error = self.sqlite_error(code)

                    class Connection:
                        def __init__(self, con):
                            object.__setattr__(self, '_con', con)
                        def __getattr__(self, name):
                            return getattr(self._con, name)
                        def __setattr__(self, name, value):
                            setattr(self._con, name, value)
                        def execute(self, sql, *args):
                            if sql == statements.get(phase):
                                raise error
                            return self._con.execute(sql, *args)

                    def injected_connect(*args, **kwargs):
                        if phase == 'connect':
                            raise error
                        return Connection(connect(*args, **kwargs))

                    def injected_pages(con):
                        nonlocal calls
                        calls += 1
                        if phase == 'pages' or (phase == 'delete_budget' and calls == 2):
                            raise error
                        return pages(con)

                    checkpoint = retention._checkpoint
                    def injected_checkpoint(con):
                        if phase == 'checkpoint':
                            raise error
                        return checkpoint(con)

                    with patch.object(retention.sqlite3, 'connect', injected_connect), \
                         patch.object(retention, '_pages', injected_pages), \
                         patch.object(retention, '_checkpoint', injected_checkpoint):
                        with self.assertRaises(SystemExit) as raised:
                            retention.rotate(db, 3, metrics=metrics)
                    self.assertEqual(raised.exception.code, 75)
                    self.assertEqual(metrics['status'], 'deferred')
                    self.assertEqual(metrics['reason'], 'sqlite_busy')
                    self.assertEqual(metrics['stage'], 'preflight')
                    self.assertEqual(metrics['preflight_phase'], phase)
                    self.assertIn(phase, retention.PREFLIGHT_PHASES)
                    self.assertEqual(metrics['sqlite_error_code'], code & 255)
                    self.assertEqual(metrics['sqlite_extended_error_code'], code)
                    self.assertEqual(metrics['deleted_sessions'], 0)
                    self.assertEqual(self.counts(db)['session'], 2)
                    self.assertNotIn('private', json.dumps(metrics))

    def test_real_wal_reader_busy_is_identified_without_mutation(self):
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / 'db'; self.make_db(db)
            reader = sqlite3.connect(db)
            reader.execute('PRAGMA journal_mode=WAL')
            reader.execute('BEGIN')
            reader.execute('select count(*) from session').fetchone()
            metrics = {}
            try:
                with self.assertRaises(SystemExit) as raised:
                    retention.rotate(db, 3, busy_timeout_ms=1, metrics=metrics)
                self.assertEqual(raised.exception.code, 75)
                self.assertEqual(metrics['reason'], 'sqlite_busy')
                self.assertEqual(metrics['preflight_phase'], 'begin_exclusive')
                self.assertEqual(metrics['sqlite_error_code'], sqlite3.SQLITE_BUSY)
                self.assertEqual(metrics['deleted_sessions'], 0)
            finally:
                reader.close()
            self.assertEqual(self.counts(db)['session'], 2)

    def test_nonbusy_sqlite_error_and_non_sqlite_defer_do_not_gain_busy_codes(self):
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / 'db'; self.make_db(db)
            for error, reason, rc in ((self.sqlite_error(sqlite3.SQLITE_IOERR), 'sqlite_error', 1),
                                      (retention.Deferred('deadline'), 'deadline', 75),
                                      (OSError('private text'), 'filesystem_or_input', 1)):
                with self.subTest(reason=reason):
                    metrics = {'sqlite_error_code': 5, 'sqlite_extended_error_code': 517}
                    with patch.object(retention.sqlite3, 'connect', side_effect=error):
                        with self.assertRaises(SystemExit) as raised:
                            retention.rotate(db, 3, metrics=metrics)
                    self.assertEqual(raised.exception.code, rc)
                    self.assertEqual(metrics['reason'], reason)
                    self.assertEqual(metrics['preflight_phase'], 'connect')
                    self.assertNotIn('sqlite_error_code', metrics)
                    self.assertNotIn('sqlite_extended_error_code', metrics)
                    self.assertNotIn('private', json.dumps(metrics))

    def test_success_keeps_legacy_status_and_clears_previous_busy_codes(self):
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / 'db'; self.make_db(db)
            metrics = {'sqlite_error_code': 5, 'sqlite_extended_error_code': 517}
            self.assertEqual(retention.rotate(db, 3, metrics=metrics), 1)
            legacy = {key: metrics[key] for key in ('version', 'status', 'reason', 'stage',
                                                    'retention_days', 'eligible_sessions', 'deleted_sessions')}
            self.assertEqual(legacy, dict(version=1, status='completed', reason='ok', stage='done',
                                          retention_days=3, eligible_sessions=1, deleted_sessions=1))
            self.assertEqual(metrics['preflight_phase'], 'complete')
            self.assertNotIn('sqlite_error_code', metrics)
            self.assertNotIn('sqlite_extended_error_code', metrics)
            path = Path(td) / 'status.json'; retention.write_result(path, metrics)
            self.assertEqual(json.loads(path.read_text()), metrics)

    def test_busy_after_preflight_is_not_labelled_preflight_failure(self):
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / 'db'; self.make_db(db); metrics = {}
            with patch.object(retention, '_delete_if_present', side_effect=self.sqlite_error(517)):
                with self.assertRaises(SystemExit) as raised:
                    retention.rotate(db, 3, metrics=metrics)
            self.assertEqual(raised.exception.code, 75)
            self.assertEqual(metrics['reason'], 'sqlite_busy')
            self.assertEqual(metrics['stage'], 'delete')
            self.assertEqual(metrics['preflight_phase'], 'complete')
            self.assertEqual(metrics['sqlite_extended_error_code'], 517)
            self.assertEqual(metrics['deleted_sessions'], 0)
            self.assertEqual(self.counts(db)['session'], 2)


@unittest.skipUnless(sys.platform == 'linux', 'Linux sparse reclamation')
class SparseReclaimTests(unittest.TestCase):
    @contextmanager
    def fixture(self, secure=True):
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / 'db.sqlite'
            con = sqlite3.connect(db, isolation_level=None)
            con.execute('pragma journal_mode=wal')
            con.execute('pragma secure_delete=%d' % secure)
            con.execute('create table payload(id integer primary key, data blob)')
            for i in range(8):
                con.execute('insert into payload values (?, randomblob(262144))', (i,))
            con.execute('delete from payload where id < 6')
            con.close()
            fd = os.open(db, os.O_RDWR | os.O_NOFOLLOW)
            con = sqlite3.connect(db, isolation_level=None)
            con.execute('pragma locking_mode=exclusive')
            con.execute('begin exclusive')
            con.execute('commit')
            retention._checkpoint(con)
            try:
                yield db, fd, con, retention.Budget(db.parent, reserve=0), {}
            finally:
                con.close()
                os.close(fd)

    def assert_writer_blocked(self, db):
        child = subprocess.run([sys.executable, '-c',
            "import sqlite3,sys; c=sqlite3.connect(sys.argv[1],timeout=.03); "
            "c.execute('begin immediate')", str(db)], capture_output=True)
        self.assertNotEqual(child.returncode, 0)
        self.assertIn(b'database is locked', child.stderr)

    def test_real_sparse_preserves_all_bytes_rows_identity_and_lock(self):
        import hashlib
        with self.fixture() as (db, fd, con, budget, metrics):
            before = os.fstat(fd)
            digest = hashlib.sha256(os.pread(fd, before.st_size, 0)).digest()
            rows = con.execute('select id,hex(data) from payload').fetchall()
            self.assert_writer_blocked(db)
            with patch.object(retention, 'SPARSE_WINDOW_BYTES', 256 * 1024):
                retention._sparse_reclaim(con, db, fd, budget, metrics)
            self.assert_writer_blocked(db)
            self.assertEqual(hashlib.sha256(os.pread(fd, before.st_size, 0)).digest(), digest)
            after = os.fstat(fd)
            self.assertEqual((before.st_ino, before.st_size), (after.st_ino, after.st_size))
            self.assertLess(after.st_blocks, before.st_blocks)
            self.assertEqual(con.execute('select id,hex(data) from payload').fetchall(), rows)
            self.assertEqual(con.execute('pragma integrity_check').fetchall(), [('ok',)])
            self.assertTrue(metrics['sparse_complete'])
            self.assertEqual(metrics['sparse_scanned_bytes'], before.st_size)
            self.assertGreater(metrics['sparse_reclaimed_bytes'], 1024 ** 2)

    def test_nonzero_free_pages_are_not_punched(self):
        import hashlib
        with self.fixture(False) as (db, fd, con, budget, metrics):
            before = os.fstat(fd)
            digest = hashlib.sha256(os.pread(fd, before.st_size, 0)).digest()
            retention._sparse_reclaim(con, db, fd, budget, metrics)
            self.assertEqual(hashlib.sha256(os.pread(fd, before.st_size, 0)).digest(), digest)
            self.assertLess(metrics['sparse_reclaimed_bytes'], 65536)
            self.assertEqual(con.execute('pragma integrity_check').fetchone(), ('ok',))

    def test_deadline_has_no_false_complete_and_releases_no_lock(self):
        with self.fixture() as (db, fd, con, budget, metrics):
            with patch.object(retention, 'SPARSE_TIMEOUT_SECONDS', 0):
                with self.assertRaisesRegex(retention.Deferred, 'sparse_deadline'):
                    retention._sparse_reclaim(con, db, fd, budget, metrics)
            self.assertNotIn('sparse_complete', metrics)
            self.assert_writer_blocked(db)

    def test_hardlink_is_rejected_before_tool(self):
        with self.fixture() as (db, fd, con, budget, metrics):
            os.link(db, db.parent / 'alias')
            with patch.object(retention, '_dig_zero_window') as proc:
                with self.assertRaisesRegex(retention.Deferred, 'sparse_identity_changed'):
                    retention._sparse_reclaim(con, db, fd, budget, metrics)
            proc.assert_not_called()

    def test_filesystem_failure_does_not_claim_recovery(self):
        with self.fixture() as (db, fd, con, budget, metrics):
            with patch.object(retention, '_dig_zero_window',
                              side_effect=retention.Deferred('sparse_filesystem_unsupported')):
                with self.assertRaisesRegex(retention.Deferred, 'sparse_filesystem_unsupported'):
                    retention._sparse_reclaim(con, db, fd, budget, metrics)
            self.assertNotIn('sparse_complete', metrics)
            self.assert_writer_blocked(db)

    def test_interruption_between_windows_preserves_bytes_and_lock(self):
        import hashlib
        with self.fixture() as (db, fd, con, budget, metrics):
            size = os.fstat(fd).st_size
            before = hashlib.sha256(os.pread(fd, size, 0)).digest()
            real = retention._dig_zero_window
            def interrupt(*args):
                real(*args)
                raise retention.Deferred('interrupted')
            with patch.object(retention, '_dig_zero_window', interrupt):
                with self.assertRaisesRegex(retention.Deferred, 'interrupted'):
                    retention._sparse_reclaim(con, db, fd, budget, metrics)
            self.assertEqual(hashlib.sha256(os.pread(fd, size, 0)).digest(), before)
            self.assertNotIn('sparse_complete', metrics)
            self.assert_writer_blocked(db)

    def test_sigkill_at_window_boundary_preserves_database_and_releases_lock(self):
        import hashlib
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / 'db.sqlite'
            con = sqlite3.connect(db)
            con.execute('pragma journal_mode=wal')
            con.execute('pragma secure_delete=1')
            con.execute('create table payload(data blob)')
            con.execute('insert into payload values (randomblob(2097152))')
            con.commit()
            con.execute('delete from payload')
            con.commit()
            con.close()
            before = hashlib.sha256(db.read_bytes()).digest()
            marker = Path(td) / 'window-done'
            script = """
import importlib.util,os,sqlite3,sys,time
from pathlib import Path
spec=importlib.util.spec_from_file_location('retention',sys.argv[1])
r=importlib.util.module_from_spec(spec);spec.loader.exec_module(r)
db=Path(sys.argv[2]);fd=os.open(db,os.O_RDWR)
c=sqlite3.connect(db,isolation_level=None)
c.execute('pragma locking_mode=exclusive');c.execute('begin exclusive');c.execute('commit')
r._checkpoint(c)
real=r._dig_zero_window
def pause(*args):
    real(*args)
    Path(sys.argv[3]).write_text('ready')
    time.sleep(10)
r._dig_zero_window=pause
r._sparse_reclaim(c,db,fd,r.Budget(db.parent,reserve=0),{})
"""
            child = subprocess.Popen([sys.executable, '-c', script,
                str(ROOT / 'lib/opencode_db_retention.py'), str(db), str(marker)])
            try:
                for _ in range(300):
                    if marker.exists() or child.poll() is not None:
                        break
                    time.sleep(.01)
                self.assertTrue(marker.exists())
                self.assert_writer_blocked(db)
                child.kill()
                child.wait(timeout=5)
            finally:
                if child.poll() is None:
                    child.kill(); child.wait()
            self.assertEqual(hashlib.sha256(db.read_bytes()).digest(), before)
            con = sqlite3.connect(db)
            self.assertEqual(con.execute('pragma integrity_check').fetchone(), ('ok',))
            con.execute('insert into payload values (?)', (b'new',))
            con.commit(); con.close()

    def test_nonzero_byte_in_a_block_prevents_deallocation(self):
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / 'blocks'
            block = os.statvfs(td).f_bsize
            original = bytes(block) + b'X' + bytes(block - 1) + bytes(block)
            db.write_bytes(original)
            fd = os.open(db, os.O_RDWR)
            try:
                retention._dig_zero_window(fd, 0, len(original), lambda: None)
                self.assertEqual(os.pread(fd, len(original), 0), original)
                self.assertEqual(os.fstat(fd).st_size, len(original))
            finally:
                os.close(fd)

    def test_replaced_path_is_rejected_without_touching_either_file(self):
        with self.fixture() as (db, fd, con, budget, metrics):
            db.rename(db.parent / 'original')
            db.write_bytes(b'replacement')
            with patch.object(retention, '_dig_zero_window') as proc:
                with self.assertRaisesRegex(retention.Deferred, 'sparse_identity_changed'):
                    retention._sparse_reclaim(con, db, fd, budget, metrics)
            proc.assert_not_called()
            self.assertEqual(db.read_bytes(), b'replacement')

    def test_rotate_opt_in_skips_compact_copy_and_preserves_freelist(self):
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / 'opencode.db'
            OpencodeDbRetentionTests().make_db(db)
            con = sqlite3.connect(db)
            con.execute('pragma journal_mode=wal')
            con.execute('pragma secure_delete=1')
            con.execute('create table scratch(data blob)')
            con.execute('insert into scratch values (zeroblob(2097152))')
            con.commit()
            con.execute('drop table scratch')
            con.commit()
            con.close()
            metrics = {}
            with patch.dict(os.environ, OPENCODE_RETENTION_SPARSE_RECLAIM='1'):
                retention.rotate(str(db), 3, metrics=metrics, reserve=0)
            self.assertEqual(metrics['stage'], 'sparse_reclaimed')
            self.assertEqual(metrics['deleted_sessions'], 1)
            self.assertGreater(metrics['freelist_count'], 0)
            self.assertGreater(metrics['sparse_reclaimed_bytes'], 1024 ** 2)
            self.assertNotIn('compact_storage', metrics)
            con = sqlite3.connect(db)
            self.assertEqual(con.execute('pragma integrity_check').fetchone(), ('ok',))
            con.execute('insert into session values ("post",0)')
            con.commit()
            con.close()


if __name__ == '__main__':
    unittest.main()

