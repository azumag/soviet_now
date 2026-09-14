#!/usr/bin/env python3
"""Bounded retention for an opencode session SQLite database.

Deletes sessions older than N days (plus their messages/parts/events) inside a
single transaction, then VACUUMs. The caller must hold the exclusive rotation
gate (flock) so that no new `opencode run` writer starts during the mutation.

See docich docs/adr/0002-opencode-db-retention.md and issue #389.
"""
import sqlite3
import sys
import time

CHILD_TABLES = (
    "todo",
    "session_share",
    "session_message",
    "session_input",
    "session_context_epoch",
)


def rotate(db_path, days, busy_timeout_ms=30000):
    cutoff = int(time.time() * 1000) - days * 86400000
    con = sqlite3.connect(db_path, timeout=busy_timeout_ms / 1000.0)
    con.isolation_level = None
    cur = con.cursor()
    cur.execute("pragma busy_timeout=%d" % busy_timeout_ms)
    try:
        old = cur.execute(
            "select count(*) from session where time_created < ?", (cutoff,)
        ).fetchone()[0]
    except sqlite3.Error as exc:
        con.close()
        raise SystemExit("opencode db: %s" % exc)
    if old:
        try:
            cur.execute("BEGIN IMMEDIATE")
            cur.execute(
                "create temp table old_sessions as select id from session where time_created < ?",
                (cutoff,),
            )
            for table in CHILD_TABLES:
                try:
                    cur.execute(
                        "delete from %s where session_id in (select id from old_sessions)" % table
                    )
                except sqlite3.Error:
                    pass
            cur.execute("delete from event where aggregate_id in (select id from old_sessions)")
            cur.execute(
                "delete from event_sequence where aggregate_id in (select id from old_sessions)"
            )
            cur.execute("delete from part where session_id in (select id from old_sessions)")
            cur.execute("delete from message where session_id in (select id from old_sessions)")
            cur.execute("delete from session where id in (select id from old_sessions)")
            cur.execute("COMMIT")
        except sqlite3.Error as exc:
            try:
                cur.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            con.close()
            raise SystemExit("opencode db rollback: %s" % exc)
    try:
        cur.execute("vacuum")
    except sqlite3.Error as exc:
        print("opencode db vacuum deferred: %s" % exc, file=sys.stderr)
    con.close()
    print("opencode db rotated old_sessions=%d" % old)
    return old


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit("usage: opencode_db_retention.py DB DAYS")
    rotate(sys.argv[1], int(sys.argv[2]))
