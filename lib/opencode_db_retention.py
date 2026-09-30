#!/usr/bin/env python3
"""Gated OpenCode retention with bounded disk use and observable outcomes.

The caller holds the shared-writer gate exclusively. SQLite EXCLUSIVE locking
also protects the compact snapshot from ungated connections. Never rename or
unlink the live DB/WAL: copy the compact image back with SQLite's transactional
backup API, then checkpoint. Reserve 1 GiB for streaming throughout the work.
"""
import argparse
from contextlib import contextmanager, nullcontext
import json
import os
from pathlib import Path
import sqlite3
import signal
import resource
import sys
import tempfile
import time
import threading

RESERVE_BYTES = 1024 ** 3
MEMORY_RESERVE_BYTES = 4 * 1024 ** 3
DEFERRED = 75
PRUNE_WAL_LIMIT = 128 * 1024 ** 2
PRUNE_BATCH_SESSIONS = 8
PRUNE_MIN_WAL_BYTES = 1024 ** 2
# Covers WAL-index growth even with SQLite's minimum 512-byte page size
# (128 MiB / 536 * 8 bytes), plus filesystem block rounding. Not user data.
PRUNE_OVERHEAD_BYTES = 4 * 1024 ** 2
CHILD_TABLES = ('todo', 'session_share', 'session_message', 'session_input', 'session_context_epoch')
STATUSES = {'running', 'completed', 'gate_timeout', 'disabled', 'deferred', 'failed'}
# Additive, fixed diagnostics only: never SQL, exception text or DB content.
PREFLIGHT_PHASES = frozenset({
    'input', 'budget', 'connect', 'busy_timeout', 'temp_store', 'synchronous',
    'locking_mode', 'begin_exclusive', 'commit_exclusive', 'journal_mode',
    'checkpoint', 'pages', 'eligible_count', 'delete_budget', 'complete',
})


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


@contextmanager
def _bounded_wal(con, db, budget, metrics):
    """Kernel file-size ceiling, only during one WAL DELETE transaction.

    This is process-wide: use the single-threaded retention CLI, never a
    concurrent application. Checkpoint/result-file writes happen after restore.
    The ceiling bounds blobs, triggers, indices and cache spills alike; LIMIT
    only bounds the candidate set, not the bytes SQLite may dirty.
    """
    if threading.active_count() != 1:
        raise Deferred('wal_limit_unavailable')
    previous_limit = resource.getrlimit(resource.RLIMIT_FSIZE)
    previous_signal = signal.getsignal(signal.SIGXFSZ)
    auto = con.execute('PRAGMA wal_autocheckpoint').fetchone()[0]
    cap = min(PRUNE_WAL_LIMIT, available_bytes(db.parent) - budget.reserve - PRUNE_OVERHEAD_BYTES)
    for value in previous_limit:
        if value != resource.RLIM_INFINITY:
            cap = min(cap, value)
    cap = max(0, cap // 4096 * 4096)
    if cap < PRUNE_MIN_WAL_BYTES:
        raise Deferred('insufficient_space')
    wal = Path(str(db) + '-wal')
    if wal.exists() and wal.stat().st_size:
        raise Deferred('checkpoint_busy')
    budget.check(cap + PRUNE_OVERHEAD_BYTES)
    installed = False
    try:
        con.execute('PRAGMA wal_autocheckpoint=0')
        signal.signal(signal.SIGXFSZ, signal.SIG_IGN)
        resource.setrlimit(resource.RLIMIT_FSIZE, (cap, previous_limit[1]))
        installed = True
        metrics.update(prune_mode='bounded_wal', wal_limit_bytes=cap)
        yield
    except sqlite3.Error as exc:
        code = (getattr(exc, 'sqlite_errorcode', 0) or 0) & 255
        if installed and code in (sqlite3.SQLITE_IOERR, sqlite3.SQLITE_FULL):
            # Do not mistake an arbitrary I/O error for proof the cap was hit.
            metrics.update(bounded_prune_blocked=True,
                           recovery_action='inspect_io_or_add_capacity')
            raise Deferred('bounded_prune_io_error') from None
        raise
    finally:
        # Restore BEFORE rollback/close/checkpoint or writing the result JSON.
        # No hard limit is lowered, so restoring the soft limit needs no privilege.
        resource.setrlimit(resource.RLIMIT_FSIZE, previous_limit)
        signal.signal(signal.SIGXFSZ, previous_signal)
        con.execute('PRAGMA wal_autocheckpoint=%d' % auto)


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
                   started_at=int(time.time()), retention_days=days, preflight_phase='input')
    # A caller may reuse its metrics dictionary after a previous busy attempt.
    metrics.pop('sqlite_error_code', None)
    metrics.pop('sqlite_extended_error_code', None)
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
        metrics['preflight_phase'] = 'budget'
        budget.check()
        metrics['preflight_phase'] = 'connect'
        con = sqlite3.connect(db.as_uri() + '?mode=rw', uri=True, timeout=busy_timeout_ms / 1000)
        con.isolation_level = None
        metrics['preflight_phase'] = 'busy_timeout'
        con.execute('PRAGMA busy_timeout=%d' % busy_timeout_ms)
        metrics['preflight_phase'] = 'temp_store'
        con.execute('PRAGMA temp_store=MEMORY')
        metrics['preflight_phase'] = 'synchronous'
        con.execute('PRAGMA synchronous=FULL')
        metrics['preflight_phase'] = 'locking_mode'
        con.execute('PRAGMA locking_mode=EXCLUSIVE')
        # Acquire and retain SQLite's lock across prune, snapshot and writeback.
        # This protects against a writer outside the cooperative shell gate.
        metrics['preflight_phase'] = 'begin_exclusive'
        con.execute('BEGIN EXCLUSIVE')
        metrics['preflight_phase'] = 'commit_exclusive'
        con.execute('COMMIT')
        metrics['preflight_phase'] = 'journal_mode'
        mode = con.execute('PRAGMA journal_mode').fetchone()[0]
        if mode not in ('wal', 'delete', 'truncate', 'persist'):
            raise Deferred('unsafe_journal_mode')
        metrics['preflight_phase'] = 'checkpoint'
        _checkpoint(con)
        metrics['preflight_phase'] = 'pages'
        metrics.update(_pages(con))
        cutoff = int(time.time() * 1000) - days * 86400000
        metrics['preflight_phase'] = 'eligible_count'
        old = con.execute('select count(*) from session where time_created < ?', (cutoff,)).fetchone()[0]
        metrics['eligible_sessions'] = old
        con.set_progress_handler(budget.progress, 1000)
        metrics['preflight_phase'] = 'delete_budget'
        pages = _pages(con)
        # Under pressure prune first, even if a RAM copy is possible. A compact
        # copy may fit tmpfs while its writeback WAL cannot fit this filesystem;
        # running it first would block every subsequent bounded prune.
        delete_wal_budget = _wal_budget(_pages(con)) if old else 0
        bounded = (old and mode == 'wal'
                   and available_bytes(db.parent) < budget.reserve + delete_wal_budget)
        if old and not bounded:
            budget.check(delete_wal_budget)
        metrics['preflight_phase'] = 'complete'
        if old:
            metrics['stage'] = 'delete'
            budget.check()
            try:
                guard = _bounded_wal(con, db, budget, metrics) if bounded else nullcontext()
                with guard:
                    con.execute('BEGIN IMMEDIATE')
                    con.execute('create temp table old_sessions as select id from session where time_created < ? order by time_created, id limit ?',
                                (cutoff, PRUNE_BATCH_SESSIONS if bounded else -1))
                    selected = con.execute('select count(*) from old_sessions').fetchone()[0]
                    metrics['selected_sessions'] = selected
                    for table in CHILD_TABLES:
                        _delete_if_present(con, 'delete from %s where session_id in (select id from old_sessions)' % table)
                    for table, column in (('event', 'aggregate_id'), ('event_sequence', 'aggregate_id'), ('part', 'session_id'), ('message', 'session_id')):
                        _delete_if_present(con, 'delete from %s where %s in (select id from old_sessions)' % (table, column))
                    con.execute('delete from session where id in (select id from old_sessions)')
                    con.execute('COMMIT')
                    metrics['deleted_sessions'] = selected
            except (sqlite3.Error, Deferred):
                con.set_progress_handler(None, 0)
                if con.in_transaction:
                    con.execute('ROLLBACK')
                raise
        _checkpoint(con)
        if bounded:
            # Committed prune exposes reusable pages without requiring a second
            # DB image. It does not claim filesystem shrink or complete retention.
            metrics.update(_pages(con))
            metrics.update(remaining_sessions=old - metrics['deleted_sessions'], stage='compact_deferred')
            raise Deferred('bounded_prune_committed')
        if _pages(con)['freelist_count']:
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
            metrics.update(status='deferred', reason='sqlite_busy',
                           sqlite_error_code=code & 255, sqlite_extended_error_code=code)
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
