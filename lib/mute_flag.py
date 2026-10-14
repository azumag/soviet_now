#!/usr/bin/env python3
"""Ownership record for the local-game BGM mute flag (``tmp/mute_local_bgm``).

Why this exists
---------------
``soviet_local.mjs`` (the 中華AI bridge that drives the local Unity game) skips
*every* page interaction while the mute flag file exists — not just audio — so a
flag left behind by a dead メリケンAI (soren91) session stops the main broadcast
for good.  The previous writer was a bare ``touch``/``rm -f`` pair with no way to
tell "a live soren91 session owns this" from "a session died and left it".

This module replaces that boolean with an *ownership record*:

* The flag file holds a JSON record (``v``/``revision``/``token``/``browser_id``
  /``armed``/``owners``) instead of being empty.
* Writers serialise through a persistent ``flock`` on ``<flag>.lock`` — the lock
  file is never deleted, so a SIGKILLed writer releases it via the OS.
* ``armed`` flips to true only once an owner has actually joined, which closes
  the startup window between ``begin`` and the runner's ``join``.
* Release happens through :func:`cmd_reap`, which is fail-closed:

  - the caller must present the exact ``token`` + ``revision`` + ``browser_id``
    it read (a compare-and-swap, so a stale decision cannot touch a replacement
    generation);
  - every recorded owner must be provably dead;
  - the caller must have proven that the CDP-visible browser has no page other
    than its own local game page (``--foreign-pages 0``);
  - the record must be ``armed`` with at least one owner;
  - legacy / corrupt / unarmed / mismatching / unprovable records are kept.

  Only then is the flag ``os.rename``d aside (never unlinked, so the evidence
  survives).  No mtime or heartbeat deadline is consulted anywhere: time-based
  expiry was the source of the original mis-unmutes.

Owner liveness uses a PID identity probe, not the PID alone:

* Linux: ``/proc/sys/kernel/random/boot_id`` + the process start tick from
  ``/proc/<pid>/stat`` (so a reused PID is recognised as a *different* process).
* Other platforms: ``ps -p <pid> -o lstart=``.
* When the platform cannot determine the identity, the owner is reported as
  ``unknown`` and the flag is kept (fail-closed).

CLI (all subcommands print one JSON object on stdout)::

    mute_flag.py begin       --flag P --token T [--browser-id B]
    mute_flag.py join        --flag P --token T --role runner|main --pid N [--browser-id B]
    mute_flag.py leave       --flag P [--token T] [--role R] [--pid N]
    mute_flag.py abort       --flag P --token T
    mute_flag.py status      --flag P
    mute_flag.py reap        --flag P --token T --revision N --browser-id B --foreign-pages N
    mute_flag.py browser-id  --cdp-url http://127.0.0.1:9222
"""

from __future__ import annotations

import argparse
import contextlib
import fcntl
import json
import os
import subprocess
import sys
import time
import urllib.request
import uuid
from urllib.parse import urlparse

RECORD_VERSION = 1
OWNER_ROLES = ("control", "runner", "main")

# Record states reported by read_record().
STATE_ABSENT = "absent"      # no flag file at all -> the local game is free
STATE_OWNED = "owned"        # a well-formed ownership record
STATE_LEGACY = "legacy"      # the old empty `touch` flag -> fail-closed
STATE_CORRUPT = "corrupt"    # non-empty but not a valid record -> fail-closed
STATE_UNREADABLE = "unreadable"  # I/O error while reading -> fail-closed


def _utc_now():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def lock_path_for(flag_path):
    return flag_path + ".lock"


@contextlib.contextmanager
def locked(flag_path):
    """Serialise record mutations with a persistent flock (never unlinked)."""
    directory = os.path.dirname(os.path.abspath(flag_path))
    os.makedirs(directory, exist_ok=True)
    fd = os.open(lock_path_for(flag_path), os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


def read_record(flag_path):
    """Return ``(state, record)``.  ``record`` is only set for STATE_OWNED."""
    try:
        with open(flag_path, "r", encoding="utf-8") as handle:
            raw = handle.read()
    except FileNotFoundError:
        return STATE_ABSENT, None
    except OSError:
        return STATE_UNREADABLE, None
    if not raw.strip():
        # The historical writer was `touch`: an empty file carries no ownership
        # information, so it can never be reaped automatically.
        return STATE_LEGACY, None
    try:
        record = json.loads(raw)
    except (ValueError, TypeError):
        return STATE_CORRUPT, None
    if not isinstance(record, dict):
        return STATE_CORRUPT, None
    if record.get("v") != RECORD_VERSION:
        return STATE_CORRUPT, None
    if not isinstance(record.get("token"), str) or not record["token"]:
        return STATE_CORRUPT, None
    revision = record.get("revision")
    if not isinstance(revision, int) or isinstance(revision, bool) or revision < 1:
        return STATE_CORRUPT, None
    owners = record.get("owners")
    if not isinstance(owners, list):
        return STATE_CORRUPT, None
    for owner in owners:
        if not isinstance(owner, dict) or not isinstance(owner.get("pid"), int):
            return STATE_CORRUPT, None
    return STATE_OWNED, record


def _read_owned_fields(flag_path):
    """read_record() with a non-optional mapping ({} when no record is owned)."""
    state, record = read_record(flag_path)
    if not isinstance(record, dict):
        return state, {}
    return state, record


def write_record(flag_path, record):
    """Atomically replace the record (tmp file + os.replace)."""
    record["updated_at"] = _utc_now()
    directory = os.path.dirname(os.path.abspath(flag_path))
    os.makedirs(directory, exist_ok=True)
    tmp_path = "%s.tmp.%d" % (flag_path, os.getpid())
    with open(tmp_path, "w", encoding="utf-8") as handle:
        json.dump(record, handle, ensure_ascii=False, sort_keys=True)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp_path, flag_path)


def _reap_rename(flag_path, reason="reaped"):
    """Rename the flag aside so the evidence survives; never unlink."""
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    target = "%s.%s-%s-%d" % (flag_path, reason, stamp, os.getpid())
    if os.path.exists(target):
        raise FileExistsError(target)
    os.rename(flag_path, target)
    return target


def _pid_alive(pid):
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _linux_identity(pid):
    try:
        with open("/proc/sys/kernel/random/boot_id", "r", encoding="utf-8") as handle:
            boot_id = handle.read().strip()
    except OSError:
        return None
    try:
        with open("/proc/%d/stat" % pid, "r", encoding="utf-8") as handle:
            raw = handle.read()
    except OSError:
        return None
    if not boot_id:
        return None
    # `comm` may contain spaces and parentheses, so split after the LAST ')'.
    close = raw.rfind(")")
    if close < 0:
        return None
    fields = raw[close + 2:].split()
    # state is field 3, so starttime (field 22) is fields[19].
    if len(fields) < 20:
        return None
    try:
        start_ticks = int(fields[19])
    except (TypeError, ValueError):
        return None
    return {"kind": "linux-proc", "boot_id": boot_id, "start_ticks": start_ticks}


def _ps_identity(pid):
    try:
        result = subprocess.run(
            ["ps", "-p", str(pid), "-o", "lstart="],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    stamp = result.stdout.decode("utf-8", "replace").strip()
    if result.returncode != 0 or not stamp:
        return None
    return {"kind": "ps-lstart", "lstart": stamp}


def probe_identity(pid):
    """Best-effort, PID-reuse-resistant identity of ``pid`` (None when unknown)."""
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
        return None
    if os.path.isdir("/proc"):
        return _linux_identity(pid)
    return _ps_identity(pid)


def _identity_equal(left, right):
    if not isinstance(left, dict) or not isinstance(right, dict):
        return False
    if left.get("kind") != right.get("kind"):
        return False
    if left.get("kind") == "linux-proc":
        return (left.get("boot_id") == right.get("boot_id")
                and left.get("start_ticks") == right.get("start_ticks"))
    if left.get("kind") == "ps-lstart":
        return left.get("lstart") == right.get("lstart")
    return False


def owner_state(owner):
    """``alive`` / ``dead`` / ``unknown`` for one recorded owner (fail-closed)."""
    if not isinstance(owner, dict):
        return "unknown"
    pid = owner.get("pid")
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
        return "unknown"
    if not _pid_alive(pid):
        return "dead"
    now = probe_identity(pid)
    recorded = owner.get("identity")
    if not isinstance(recorded, dict):
        # The PID is alive but we never captured an identity for it: we cannot
        # rule out PID reuse, so keep the flag.
        return "unknown"
    if now is None:
        return "unknown"
    # A live PID whose identity differs is a REUSED pid: the recorded owner is
    # gone.
    return "alive" if _identity_equal(now, recorded) else "dead"


def _owner_summary(record):
    summary = []
    for owner in record.get("owners", []):
        summary.append({
            "role": owner.get("role"),
            "pid": owner.get("pid"),
            "state": owner_state(owner),
        })
    return summary


def fetch_browser_id(cdp_url, timeout=5.0):
    """Browser-resource identity: the CDP ``/json/version`` WebSocket pathname."""
    base = str(cdp_url or "").rstrip("/")
    if not base:
        return ""
    with urllib.request.urlopen(base + "/json/version", timeout=timeout) as response:
        payload = json.loads(response.read().decode("utf-8"))
    ws_url = payload.get("webSocketDebuggerUrl")
    if not isinstance(ws_url, str) or not ws_url:
        return ""
    return urlparse(ws_url).path or ""


def cmd_begin(flag_path, token=None, browser_id=None):
    token = token or str(uuid.uuid4())
    with locked(flag_path):
        state, previous = _read_owned_fields(flag_path)
        revision = 1
        if state == STATE_OWNED:
            revision = int(previous.get("revision", 0)) + 1
        record = {
            "v": RECORD_VERSION,
            "revision": revision,
            "token": token,
            "browser_id": browser_id or None,
            "armed": False,
            "owners": [],
            "created_at": _utc_now(),
            "updated_at": _utc_now(),
        }
        write_record(flag_path, record)
    return {
        "ok": True,
        "state": STATE_OWNED,
        "revision": revision,
        "token": token,
        "browser_id": browser_id or None,
        "muted": True,
    }


def cmd_join(flag_path, token, pid, role, browser_id=None):
    if not token:
        return {"ok": False, "reason": "missing-token", "muted": True}
    if role not in OWNER_ROLES:
        return {"ok": False, "reason": "bad-role", "muted": True}
    if not _pid_alive(pid):
        return {"ok": False, "reason": "dead-owner", "muted": True}
    with locked(flag_path):
        state, record = _read_owned_fields(flag_path)
        if state == STATE_ABSENT:
            return {"ok": False, "reason": "absent", "muted": False}
        if state != STATE_OWNED:
            return {"ok": False, "reason": state, "muted": True}
        if record.get("token") != token:
            return {"ok": False, "reason": "stale-token", "muted": True}
        if browser_id and not record.get("browser_id"):
            record["browser_id"] = browser_id
        kept = [owner for owner in record.get("owners", [])
                if not (owner.get("role") == role and owner.get("pid") == pid)]
        kept.append({
            "role": role,
            "pid": pid,
            "identity": probe_identity(pid),
            "joined_at": _utc_now(),
        })
        record["owners"] = kept
        record["armed"] = True
        write_record(flag_path, record)
    return {
        "ok": True,
        "state": STATE_OWNED,
        "armed": True,
        "revision": record.get("revision"),
        "browser_id": record.get("browser_id"),
        "owners": _owner_summary(record),
        "muted": True,
    }


def cmd_leave(flag_path, pid=None, role=None, token=None):
    with locked(flag_path):
        state, record = _read_owned_fields(flag_path)
        if state == STATE_ABSENT:
            return {"ok": True, "state": STATE_ABSENT, "released": False, "muted": False}
        if state != STATE_OWNED:
            return {"ok": False, "reason": state, "released": False, "muted": True}
        if token and record.get("token") != token:
            return {"ok": False, "reason": "stale-token", "released": False, "muted": True}
        owners = record.get("owners", [])
        if role is None and pid is None:
            kept = []
        else:
            kept = [owner for owner in owners
                    if not ((role is None or owner.get("role") == role)
                            and (pid is None or owner.get("pid") == pid))]
        if len(kept) == len(owners):
            return {"ok": False, "reason": "not-an-owner", "released": False, "muted": True}
        record["owners"] = kept
        if not kept:
            record["armed"] = False
            write_record(flag_path, record)
            target = _reap_rename(flag_path, "released")
            return {"ok": True, "state": "released", "released": True,
                    "muted": False, "target": target}
        write_record(flag_path, record)
        return {"ok": True, "state": STATE_OWNED, "released": False, "muted": True,
                "owners": _owner_summary(record)}


def cmd_abort(flag_path, token=None):
    """Release a record that was begun but never armed (failed soren91 start)."""
    with locked(flag_path):
        state, record = _read_owned_fields(flag_path)
        if state == STATE_ABSENT:
            return {"ok": True, "state": STATE_ABSENT, "released": False}
        if state != STATE_OWNED:
            return {"ok": False, "reason": state, "released": False, "muted": True}
        if token and record.get("token") != token:
            return {"ok": False, "reason": "stale-token", "released": False, "muted": True}
        if record.get("armed") or record.get("owners"):
            return {"ok": False, "reason": "armed", "released": False, "muted": True}
        target = _reap_rename(flag_path, "aborted")
    return {"ok": True, "state": "released", "released": True, "muted": False,
            "target": target}


def cmd_status(flag_path):
    with locked(flag_path):
        state, record = _read_owned_fields(flag_path)
        if state == STATE_ABSENT:
            return {
                "ok": True, "exists": False, "state": STATE_ABSENT, "muted": False,
                "token": None, "revision": None, "browser_id": None, "armed": False,
                "owners": [], "all_owners_dead": False, "reapable": False,
            }
        if state != STATE_OWNED:
            return {
                "ok": True, "exists": True, "state": state, "muted": True,
                "token": None, "revision": None, "browser_id": None, "armed": False,
                "owners": [], "all_owners_dead": False, "reapable": False,
            }
        owners = _owner_summary(record)
        all_dead = bool(owners) and all(owner["state"] == "dead" for owner in owners)
        return {
            "ok": True,
            "exists": True,
            "state": STATE_OWNED,
            "muted": True,
            "token": record.get("token"),
            "revision": record.get("revision"),
            "browser_id": record.get("browser_id"),
            "armed": bool(record.get("armed")),
            "owners": owners,
            "all_owners_dead": all_dead,
            "reapable": bool(all_dead and record.get("armed") and record.get("browser_id")),
        }


def cmd_reap(flag_path, token, revision, browser_id, foreign_pages):
    with locked(flag_path):
        state, record = _read_owned_fields(flag_path)
        if state == STATE_ABSENT:
            return {"ok": False, "reason": "absent", "muted": False}
        if state != STATE_OWNED:
            return {"ok": False, "reason": state, "muted": True}
        # Compare-and-swap: a decision taken against a record that has since been
        # replaced (new token/revision/browser) must not touch the new one.
        if record.get("token") != token or record.get("revision") != revision:
            return {"ok": False, "reason": "stale", "muted": True}
        if not browser_id or record.get("browser_id") != browser_id:
            return {"ok": False, "reason": "browser-mismatch", "muted": True}
        if not record.get("armed"):
            return {"ok": False, "reason": "unarmed", "muted": True}
        owners = record.get("owners", [])
        if not owners:
            return {"ok": False, "reason": "no-owners", "muted": True}
        for owner in owners:
            if owner_state(owner) != "dead":
                return {"ok": False, "reason": "owner-alive", "muted": True}
        if foreign_pages != 0:
            return {"ok": False, "reason": "foreign-pages", "muted": True}
        target = _reap_rename(flag_path)
    return {"ok": True, "state": "reaped", "muted": False, "target": target}


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    subparsers = parser.add_subparsers(dest="command", required=True)

    def add_common(sub):
        sub.add_argument("--flag", required=True, help="path of the mute flag file")

    begin = subparsers.add_parser("begin")
    add_common(begin)
    begin.add_argument("--token")
    begin.add_argument("--browser-id", dest="browser_id")

    join = subparsers.add_parser("join")
    add_common(join)
    join.add_argument("--token", required=True)
    join.add_argument("--role", required=True, choices=list(OWNER_ROLES))
    join.add_argument("--pid", required=True, type=int)
    join.add_argument("--browser-id", dest="browser_id")

    leave = subparsers.add_parser("leave")
    add_common(leave)
    leave.add_argument("--token")
    leave.add_argument("--role", choices=list(OWNER_ROLES))
    leave.add_argument("--pid", type=int)

    abort = subparsers.add_parser("abort")
    add_common(abort)
    abort.add_argument("--token")

    status = subparsers.add_parser("status")
    add_common(status)

    reap = subparsers.add_parser("reap")
    add_common(reap)
    reap.add_argument("--token", required=True)
    reap.add_argument("--revision", required=True, type=int)
    reap.add_argument("--browser-id", dest="browser_id", required=True)
    reap.add_argument("--foreign-pages", dest="foreign_pages", required=True, type=int)

    browser_id = subparsers.add_parser("browser-id")
    browser_id.add_argument("--cdp-url", dest="cdp_url", required=True)
    browser_id.add_argument("--timeout", type=float, default=5.0)

    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        if args.command == "begin":
            result = cmd_begin(args.flag, token=args.token, browser_id=args.browser_id)
        elif args.command == "join":
            result = cmd_join(args.flag, args.token, args.pid, args.role,
                              browser_id=args.browser_id)
        elif args.command == "leave":
            result = cmd_leave(args.flag, pid=args.pid, role=args.role,
                               token=args.token)
        elif args.command == "abort":
            result = cmd_abort(args.flag, token=args.token)
        elif args.command == "status":
            result = cmd_status(args.flag)
        elif args.command == "reap":
            result = cmd_reap(args.flag, args.token, args.revision,
                              args.browser_id, args.foreign_pages)
        elif args.command == "browser-id":
            # Plain value (not a decision object): the shell records it verbatim,
            # and an unreachable/unknown browser prints an empty line so the
            # writer falls back to a null browser_id (fail-closed: a null id can
            # never be auto-reaped).
            try:
                value = fetch_browser_id(args.cdp_url, timeout=args.timeout)
            except (OSError, ValueError):
                value = ""
            print(value)
            return 0
        else:  # pragma: no cover - argparse enforces the choices
            raise SystemExit(2)
    except (OSError, ValueError) as error:
        print(json.dumps({"ok": False, "reason": "error", "error": str(error)},
                         ensure_ascii=False, sort_keys=True))
        return 3
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
