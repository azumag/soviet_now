#!/usr/bin/env python3
"""Gated OpenCode retention with bounded disk use and observable outcomes.

The caller holds the shared-writer gate exclusively. SQLite EXCLUSIVE locking
also protects the compact snapshot from ungated connections. Never rename or
unlink the live DB/WAL: copy the compact image back with SQLite's transactional
backup API, then checkpoint. Reserve 1 GiB for streaming throughout the work.
"""
import argparse
import json
import os
from pathlib import Path
import sqlite3
import signal
import sys
import tempfile
import time

RESERVE_BYTES = 1024 ** 3
MEMORY_RESERVE_BYTES = 4 * 1024 ** 3
DEFERRED = 75
CHILD_TABLES = ('todo', 'session_share', 'session_message', 'session_input', 'session_context_epoch')
STATUSES = {'running', 'completed', 'gate_timeout', 'disabled', 'deferred', 'failed'}


class Deferred(Exception):
    pass


def available_bytes(path):
    st = os.statvfs(path)
    return st.f_bavail * st.f_frsize


def memory_headroom(proc_root=Path('/proc'), cgroup_root=Path('/sys/fs/cgroup')):
    """Available RAM, constrained by this process's cgroup and ancestors."""
    available = None
    for line in (proc_root / 'meminfo').read_text().splitlines():
        if line.startswith('MemAvailable:'):
            available = int(line.split()[1]) * 1024
    if available is None:
        raise ValueError('memory accounting unavailable')
    for line in (proc_root / 'self/cgroup').read_text().splitlines():
        _, controllers, relative = line.split(':', 2)
        if controllers == '':
            base = cgroup_root; names = ('memory.max', 'memory.current')
        elif 'memory' in controllers.split(','):
            base = cgroup_root / 'memory'; names = ('memory.limit_in_bytes', 'memory.usage_in_bytes')
        else:
            continue
        if '..' in Path(relative).parts:
            raise ValueError('unknown cgroup namespace')
        group = base / relative.lstrip('/')
        accounted = False
        while True:
            limit_file = group / names[0]
            if limit_file.exists():
                accounted = True
                limit = limit_file.read_text().strip()
                if limit != 'max':
                    available = min(available, max(0, int(limit) - int((group / names[1]).read_text())))
            if group == base:
                break
            group = group.parent
        if not accounted:
            raise ValueError('cgroup memory accounting unavailable')
    return available


def memory_copy_root(db, image_bytes):
    """Opt-in only: a private tmpfs copy with independent RAM/space reserves."""
    if os.environ.get('OPENCODE_RETENTION_MEMORY_COMPACTION') != '1':
        return None
    root = Path('/dev/shm')
    try:
        mounted = any(fields[1:3] == ['/dev/shm', 'tmpfs']
                      for fields in (line.split() for line in Path('/proc/mounts').read_text().splitlines()))
        if (not mounted or root.is_symlink() or not root.is_dir()
                or root.stat().st_dev == db.parent.stat().st_dev):
            return None
        if available_bytes(root) < image_bytes + RESERVE_BYTES:
            return None
        if memory_headroom() < image_bytes + MEMORY_RESERVE_BYTES:
            return None
        return root
    except (OSError, ValueError):
        return None


def write_result(path, result):
    if not path:
        return
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix='.retention-result-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as out:
            json.dump(result, out, sort_keys=True)
            out.write('\n')
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


class Budget:
    def __init__(self, path, reserve=RESERVE_BYTES, timeout=600):
        self.path = path
        self.reserve = reserve
        try:
            remaining = int(os.environ.get('OPENCODE_RETENTION_DEADLINE_EPOCH', '0')) - time.time()
        except ValueError:
            remaining = 0
        if 'OPENCODE_RETENTION_DEADLINE_EPOCH' in os.environ:
            timeout = max(0, min(timeout, remaining))
        self.deadline = time.monotonic() + timeout
        self.reason = None
        self.memory_root = None

    def check(self, extra=0):
        if time.monotonic() >= self.deadline:
            self.reason = 'deadline'
        elif available_bytes(self.path) < self.reserve + extra:
            self.reason = 'insufficient_space'
        elif self.memory_root is not None:
            try:
                if (available_bytes(self.memory_root) < self.reserve
                        or memory_headroom() < MEMORY_RESERVE_BYTES):
                    self.reason = 'insufficient_memory'
            except (OSError, ValueError):
                self.reason = 'memory_unknown'
        if self.reason:
            raise Deferred(self.reason)

    def progress(self):
        try:
            self.check()
            return 0
        except (Deferred, OSError):
            self.reason = self.reason or 'space_unknown'
            return 1

    def backup_progress(self, status, remaining, total):
        # A DONE callback happens after commit: never describe that as rollback.
        if status != sqlite3.SQLITE_DONE:
            self.check()


def _pages(con):
    return {key: con.execute('PRAGMA ' + key).fetchone()[0]
            for key in ('page_size', 'page_count', 'freelist_count')}


def _wal_budget(pages):
    # One complete DB image in WAL, including each frame's 24-byte header.
    # The runtime reserve guard also bounds cache spills/repeated dirty pages.
    return pages['page_count'] * (pages['page_size'] + 24) + 65536


def _checkpoint(con):
    busy, _, _ = con.execute('PRAGMA wal_checkpoint(TRUNCATE)').fetchone()
    if busy:
        raise Deferred('checkpoint_busy')


def _delete_if_present(cur, statement):
    try:
        cur.execute(statement)
    except sqlite3.Error as exc:
        if 'no such table' not in str(exc).lower():
            raise


def _compact(con, db, budget, metrics):
    pages = _pages(con)
    # VACUUM INTO needs only the output image here. A normal VACUUM also
    # allocates its writeback WAL at the same time, which caused ENOSPC.
    image_bytes = pages['page_count'] * pages['page_size'] + 65536
    copy_root = db.parent
    if available_bytes(db.parent) < 2 * image_bytes + budget.reserve:
        memory_root = memory_copy_root(db, image_bytes)
        if memory_root is not None:
            copy_root = budget.memory_root = memory_root
    budget.check(0 if budget.memory_root else image_bytes)
    metrics['compact_storage'] = 'memory' if budget.memory_root else 'disk'
    metrics['stage'] = 'compact_copy'
    with tempfile.TemporaryDirectory(prefix='.opencode-retention-', dir=copy_root) as td:
        compact = Path(td) / 'compact.db'
        con.execute('VACUUM INTO ?', (str(compact),))
        budget.check()
        source = sqlite3.connect(compact.as_uri() + '?mode=ro', uri=True)
        try:
            source.set_progress_handler(budget.progress, 1000)
            if source.execute('PRAGMA quick_check').fetchone() != ('ok',):
                raise sqlite3.DatabaseError('compact integrity check failed')
            small = _pages(source)
            metrics['compact_bytes'] = compact.stat().st_size
            # The copy already occupies disk. Budget the writeback separately,
            # using the measured compact page count, not an optimistic estimate.
            metrics['stage'] = 'compact_writeback'
            budget.check(_wal_budget(small))
            source.backup(con, pages=128, progress=budget.backup_progress, sleep=0.05)
        finally:
            source.close()
        metrics['stage'] = 'checkpoint'
        _checkpoint(con)


def rotate(db_path, days, busy_timeout_ms=30000, *, metrics=None, reserve=RESERVE_BYTES):
    metrics = metrics if metrics is not None else {}
    metrics.update(version=1, status='running', stage='preflight', deleted_sessions=0,
                   started_at=int(time.time()), retention_days=days)
    db = Path(db_path).absolute()
    con = None
    budget = Budget(db.parent, reserve)
    try:
        if db.is_symlink() or not db.is_file() or db.stat().st_nlink != 1:
            raise ValueError('invalid_db')
        if not 1 <= days <= 365:
            raise ValueError('invalid_days')
        metrics['before_bytes'] = db.stat().st_size
        metrics['available_before_bytes'] = available_bytes(db.parent)
        budget.check()
        con = sqlite3.connect(db.as_uri() + '?mode=rw', uri=True, timeout=busy_timeout_ms / 1000)
        con.isolation_level = None
        con.execute('PRAGMA busy_timeout=%d' % busy_timeout_ms)
        con.execute('PRAGMA temp_store=MEMORY')
        con.execute('PRAGMA synchronous=FULL')
        con.execute('PRAGMA locking_mode=EXCLUSIVE')
        # Acquire and retain SQLite's lock across prune, snapshot and writeback.
        # This protects against a writer outside the cooperative shell gate.
        con.execute('BEGIN EXCLUSIVE')
        con.execute('COMMIT')
        mode = con.execute('PRAGMA journal_mode').fetchone()[0]
        if mode not in ('wal', 'delete', 'truncate', 'persist'):
            raise Deferred('unsafe_journal_mode')
        _checkpoint(con)
        metrics.update(_pages(con))
        cutoff = int(time.time() * 1000) - days * 86400000
        old = con.execute('select count(*) from session where time_created < ?', (cutoff,)).fetchone()[0]
        metrics['eligible_sessions'] = old
        con.set_progress_handler(budget.progress, 1000)
        compacted_first = False
        pages = _pages(con)
        if (old and mode == 'wal' and pages['freelist_count']
                and available_bytes(db.parent) < budget.reserve + _wal_budget(pages)):
            # Recover space from an earlier committed prune before budgeting
            # a full-size WAL just to remove newly expired sessions.
            _compact(con, db, budget, metrics)
            compacted_first = True
        if old:
            budget.check(_wal_budget(_pages(con)))
        if old:
            metrics['stage'] = 'delete'
            budget.check()
            try:
                con.execute('BEGIN IMMEDIATE')
                con.execute('create temp table old_sessions as select id from session where time_created < ?', (cutoff,))
                for table in CHILD_TABLES:
                    _delete_if_present(con, 'delete from %s where session_id in (select id from old_sessions)' % table)
                for table, column in (('event', 'aggregate_id'), ('event_sequence', 'aggregate_id'), ('part', 'session_id'), ('message', 'session_id')):
                    _delete_if_present(con, 'delete from %s where %s in (select id from old_sessions)' % (table, column))
                con.execute('delete from session where id in (select id from old_sessions)')
                con.execute('COMMIT')
                metrics['deleted_sessions'] = old
            except (sqlite3.Error, Deferred):
                con.set_progress_handler(None, 0)
                if con.in_transaction:
                    con.execute('ROLLBACK')
                raise
        _checkpoint(con)
        # Keep the new freelist reusable after pressure recovery; avoid
        # copying the same database twice in one maintenance pass.
        if _pages(con)['freelist_count'] and not compacted_first:
            if mode == 'wal':
                _compact(con, db, budget, metrics)
            else:
                # Legacy rollback-journal DBs use the documented 2x bound.
                p = _pages(con)
                budget.check(2 * p['page_count'] * p['page_size'] + 65536)
                metrics['stage'] = 'vacuum'
                con.execute('VACUUM')
        metrics.update(_pages(con))
        metrics.update(status='completed', stage='done', reason='ok')
        return old
    except Deferred as exc:
        metrics.update(status='deferred', reason=str(exc))
        raise SystemExit(DEFERRED) from None
    except sqlite3.Error as exc:
        reason = budget.reason
        if reason:
            metrics.update(status='deferred', reason=reason)
            raise SystemExit(DEFERRED) from None
        code = getattr(exc, 'sqlite_errorcode', 0) or 0
        if (code & 255) in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED):
            metrics.update(status='deferred', reason='sqlite_busy')
            raise SystemExit(DEFERRED) from None
        metrics.update(status='failed', reason='sqlite_error')
        raise SystemExit(1) from None
    except (OSError, ValueError):
        metrics.update(status='failed', reason='filesystem_or_input')
        raise SystemExit(1) from None
    finally:
        if con is not None:
            con.set_progress_handler(None, 0)
            con.close()
        metrics['completed_at'] = int(time.time())
        try:
            metrics['after_bytes'] = db.stat().st_size
            metrics['available_after_bytes'] = available_bytes(db.parent)
        except OSError:
            pass


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('db', nargs='?')
    parser.add_argument('days', nargs='?', type=int)
    parser.add_argument('--result-file')
    parser.add_argument('--record', choices=sorted(STATUSES))
    args = parser.parse_args()
    if args.record:
        result = dict(version=1, status=args.record, completed_at=int(time.time()))
        write_result(args.result_file, result)
        return 0
    if args.db is None or args.days is None:
        parser.error('DB and DAYS are required')
    def interrupted(signum, frame):
        raise Deferred('interrupted')
    signal.signal(signal.SIGTERM, interrupted)
    result = {}
    rc = 0
    try:
        rotate(args.db, args.days, metrics=result)
    except SystemExit as exc:
        rc = exc.code
    write_result(args.result_file, result)
    print(json.dumps(result, sort_keys=True))
    return rc


if __name__ == '__main__':
    raise SystemExit(main())
