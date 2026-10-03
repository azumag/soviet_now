"""Update only existing live titles; never create broadcasts or restart video."""
from __future__ import annotations

import copy
import datetime as _datetime
import fcntl
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
import urllib.request
from pathlib import Path


YOUTUBE_RESULTS = frozenset({
    "not_configured", "stream_not_configured", "no_unique_live_broadcast",
    "invalid_video_id", "video_not_found", "invalid_video_snippet",
    "unchanged", "updated", "unconfirmed", "update_failed",
})
KICK_RESULTS = frozenset({
    "not_configured", "invalid_broadcaster", "wrong_broadcaster", "not_live",
    "unchanged", "updated", "unconfirmed", "update_failed",
})
SKIP_REASONS = frozenset({
    "category_only", "dry_run", "show_only", "twitch_read_failed",
    "twitch_update_failed", "category_not_configured", "updater_missing",
    "dispatch_failed", "invalid_title",
})
CALL_CONDITIONS = frozenset({
    "unknown", "normal", "category_only", "dry_run", "show_only",
    "title_only", "force",
})
EVENT_DIR = "tmp/state/stream_title_sync"
EVENT_FILE = EVENT_DIR + "/events.jsonl"
EVENT_LOCK_FILE = EVENT_DIR + "/lock"
EVENT_MAX_BYTES = 32 * 1024
EVENT_MAX_LINE_BYTES = 512


def _source_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _current_soren_sha(root=None) -> str | None:
    root = _source_root() if root is None else root
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--verify", "HEAD"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=2,
            check=False,
        )
        value = result.stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        return None
    return value if result.returncode == 0 and re.fullmatch(r"[0-9a-f]{40}", value) else None


def _owned_directory(path):
    try:
        info = path.lstat()
    except OSError:
        return False
    return stat.S_ISDIR(info.st_mode) and not stat.S_ISLNK(info.st_mode) and info.st_uid == os.getuid()


def _ensure_private_directory(path):
    try:
        info = path.lstat()
    except FileNotFoundError:
        try:
            path.mkdir(mode=0o700)
        except FileExistsError:
            pass
        except OSError:
            return False
        try:
            info = path.lstat()
        except OSError:
            return False
    except OSError:
        return False
    if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode) or info.st_uid != os.getuid():
        return False
    if info.st_mode & 0o077:
        try:
            os.chmod(path, 0o700, follow_symlinks=False)
            info = path.lstat()
        except OSError:
            return False
    return (
        stat.S_ISDIR(info.st_mode)
        and not stat.S_ISLNK(info.st_mode)
        and info.st_uid == os.getuid()
        and info.st_mode & 0o077 == 0
    )



def _open_private_file(path, flags):
    flags |= getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags, 0o600)
        info = os.fstat(fd)
    except OSError:
        return None
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_nlink != 1:
        os.close(fd)
        return None
    try:
        os.fchmod(fd, 0o600)
    except OSError:
        os.close(fd)
        return None
    return fd


def _source_file_sha256(path) -> str | None:
    """Hash one bounded regular source file without following a symlink."""
    flags = (
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_NONBLOCK", 0)
    )
    try:
        fd = os.open(path, flags)
    except OSError:
        return None
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_size > 512 * 1024:
            return None
        digest = hashlib.sha256()
        total = 0
        while True:
            chunk = os.read(fd, 64 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if total > 512 * 1024:
                return None
            digest.update(chunk)
        return digest.hexdigest()
    except OSError:
        return None
    finally:
        os.close(fd)


def _append_title_event(
    event, *, skip_reason, youtube, kick, root=None, source_sha=None,
    call_condition="unknown", now=None,
):
    """Append fixed enums and source identity to the owner-only bounded journal."""
    if event not in {"invoked", "started", "result", "skipped"}:
        return False
    if call_condition not in CALL_CONDITIONS:
        return False
    if skip_reason not in ({"none"} | SKIP_REASONS):
        return False
    if event == "result":
        if skip_reason != "none" or youtube not in YOUTUBE_RESULTS or kick not in KICK_RESULTS:
            return False
    elif event in {"invoked", "started"}:
        if skip_reason != "none" or youtube != "not_run" or kick != "not_run":
            return False
    elif skip_reason not in SKIP_REASONS or youtube != "not_run" or kick != "not_run":
        return False

    root = Path(_source_root() if root is None else root)
    source_sha = _current_soren_sha(root) if source_sha is None else source_sha
    if source_sha is not None and (
        not isinstance(source_sha, str) or not re.fullmatch(r"[0-9a-f]{40}", source_sha)
    ):
        return False
    stamp = _datetime.datetime.now(_datetime.timezone.utc) if now is None else now
    if not isinstance(stamp, _datetime.datetime) or stamp.tzinfo is None:
        return False
    stamp = stamp.astimezone(_datetime.timezone.utc).replace(microsecond=0)
    record = {
        "occurred_at": stamp.isoformat().replace("+00:00", "Z"),
        "event": event,
        "skip_reason": skip_reason,
        "youtube": youtube,
        "kick": kick,
        "execution_head": source_sha,
        "call_condition": call_condition,
        # Sample only these nonsecret IDs in this helper's effective environment.
        "youtube_stream_id_present": bool(os.environ.get("YOUTUBE_BROADCAST_STREAM_ID")),
        "kick_broadcaster_id_present": bool(os.environ.get("KICK_BROADCASTER_USER_ID")),
        "update_stream_game_sha256": _source_file_sha256(root / "update_stream_game.sh"),
        "stream_title_sync_sha256": _source_file_sha256(Path(__file__)),
    }
    encoded = (json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
    if len(encoded) > EVENT_MAX_LINE_BYTES:
        return False

    tmp_dir = root / "tmp"
    state_dir = tmp_dir / "state"
    event_dir = root / EVENT_DIR
    if (
        not _owned_directory(tmp_dir)
        or not _owned_directory(state_dir)
        or not _ensure_private_directory(event_dir)
    ):
        return False
    lock_fd = _open_private_file(root / EVENT_LOCK_FILE, os.O_CREAT | os.O_RDWR)
    if lock_fd is None:
        return False
    event_fd = None
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        event_fd = _open_private_file(root / EVENT_FILE, os.O_CREAT | os.O_RDWR | os.O_APPEND)
        if event_fd is None:
            return False
        if os.fstat(event_fd).st_size + len(encoded) > EVENT_MAX_BYTES:
            os.ftruncate(event_fd, 0)
        view = memoryview(encoded)
        while view:
            written = os.write(event_fd, view)
            if written <= 0:
                return False
            view = view[written:]
        return True
    except OSError:
        return False
    finally:
        if event_fd is not None:
            os.close(event_fd)
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
        except OSError:
            pass
        os.close(lock_fd)




def normalize_title(title: str, limit: int = 100) -> str:
    text = ' '.join(title.split())
    if not text or '<' in text or '>' in text:
        raise ValueError('invalid_title')
    return text if len(text) <= limit else text[:limit-1].rstrip() + '…'


def youtube_title(api, title: str, stream_id: str = '') -> str:
    """Resolve a unique currently live owner broadcast, preserve its snippet."""
    if not stream_id:
        return 'stream_not_configured'
    rows = [r for r in api.broadcasts() if r.get('status', {}).get('lifeCycleStatus') == 'live']
    if stream_id:
        rows = [r for r in rows if r.get('contentDetails', {}).get('boundStreamId') == stream_id]
    if len(rows) != 1:
        return 'no_unique_live_broadcast'
    video_id = rows[0].get('id')
    if not isinstance(video_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{11}',video_id):
        return 'invalid_video_id'
    data = api._api('videos',params={'part':'snippet','id':video_id})
    items = data.get('items')
    if not isinstance(items,list) or len(items) != 1 or items[0].get('id') != video_id:
        return 'video_not_found'
    snippet = items[0].get('snippet')
    if not isinstance(snippet,dict) or not isinstance(snippet.get('categoryId'),str):
        return 'invalid_video_snippet'
    wanted = normalize_title(title)
    if snippet.get('title') == wanted:
        return 'unchanged'
    # videos.update replaces mutable fields within snippet: preserve all of
    # them, but do not echo output-only channel/thumbnails/localized fields.
    payload = {k:copy.deepcopy(snippet[k]) for k in
               ('title','description','tags','categoryId','defaultLanguage','defaultAudioLanguage') if k in snippet}
    payload['title'] = wanted
    api._api('videos',params={'part':'snippet'},method='PUT',payload={'id':video_id,'snippet':payload})
    verified = api._api('videos',params={'part':'snippet','id':video_id}).get('items',[])
    return 'updated' if len(verified)==1 and verified[0].get('id')==video_id and verified[0].get('snippet',{}).get('title')==wanted else 'unconfirmed'



def kick_title(request, title: str, broadcaster_id: str) -> str:
    if not re.fullmatch(r'[1-9][0-9]{0,18}',broadcaster_id):
        return 'invalid_broadcaster'
    # No query returns the authenticated principal. A public lookup of the
    # configured ID would not establish that PATCH targets that owner.
    rows=request('GET').get('data',[])
    if len(rows)!=1 or type(rows[0].get('broadcaster_user_id')) is not int or str(rows[0]['broadcaster_user_id'])!=broadcaster_id:
        return 'wrong_broadcaster'
    if rows[0].get('stream',{}).get('is_live') is not True:
        return 'not_live'
    wanted=normalize_title(title)
    if rows[0].get('stream_title')==wanted:
        return 'unchanged'
    request('PATCH',{'stream_title':wanted})
    rows=request('GET').get('data',[])
    return 'updated' if len(rows)==1 and str(rows[0].get('broadcaster_user_id'))==broadcaster_id and rows[0].get('stream_title')==wanted else 'unconfirmed'


def kick_request(token: str):
    def request(method, payload=None):
        req=urllib.request.Request('https://api.kick.com/public/v1/channels',
            method=method, data=None if payload is None else json.dumps(payload).encode(),
            headers={'Authorization':'Bearer '+token,'Content-Type':'application/json'})
        with urllib.request.urlopen(req,timeout=10) as response:
            raw=response.read(1024*1024+1)
        if len(raw)>1024*1024:
            raise ValueError('oversized_response')
        if not raw:
            return {}
        value=json.loads(raw)
        if not isinstance(value,dict):
            raise ValueError('invalid_response')
        return value
    return request

def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else list(argv)
    call_condition = "unknown"
    skip_reason = None
    if argv:
        if (
            len(argv) == 2
            and argv[0] == "--record-invocation"
            and argv[1] in CALL_CONDITIONS - {"unknown"}
        ):
            _append_title_event(
                "invoked", skip_reason="none", youtube="not_run", kick="not_run",
                call_condition=argv[1],
            )
            return 0
        if (
            len(argv) == 4
            and argv[0] == "--record-skip"
            and argv[1] in SKIP_REASONS
            and argv[2] == "--call-condition"
            and argv[3] in CALL_CONDITIONS
        ):
            skip_reason, call_condition = argv[1], argv[3]
        elif len(argv) == 2 and argv[0] == "--record-skip" and argv[1] in SKIP_REASONS:
            skip_reason = argv[1]
        elif len(argv) == 2 and argv[0] == "--call-condition" and argv[1] in CALL_CONDITIONS:
            call_condition = argv[1]
        else:
            return 2
    if skip_reason is not None:
        _append_title_event(
            "skipped", skip_reason=skip_reason, youtube="not_run", kick="not_run",
            call_condition=call_condition,
        )
        return 0

    title = sys.stdin.read(4097)
    if len(title)>4096:
        _append_title_event(
            "skipped", skip_reason="invalid_title", youtube="not_run", kick="not_run",
            call_condition=call_condition,
        )
        return 2
    try:
        title=normalize_title(title)
    except ValueError:
        _append_title_event(
            "skipped", skip_reason="invalid_title", youtube="not_run", kick="not_run",
            call_condition=call_condition,
        )
        return 2

    source_sha = _current_soren_sha()
    _append_title_event(
        "started", skip_reason="none", youtube="not_run", kick="not_run",
        source_sha=source_sha, call_condition=call_condition,
    )
    results={}
    required=('YOUTUBE_OAUTH_CLIENT_ID','YOUTUBE_OAUTH_CLIENT_SECRET','YOUTUBE_OAUTH_REFRESH_TOKEN')
    if all(os.environ.get(k) for k in required):
        try:
            from youtube_broadcast_guard import YouTubeAPI
            api=YouTubeAPI(timeout=10);api.refresh()
            results['youtube']=youtube_title(api,title,os.environ.get('YOUTUBE_BROADCAST_STREAM_ID',''))
        except Exception:
            # Never serialize OAuth responses, request objects or API bodies.
            results['youtube']='update_failed'
    else:
        results['youtube']='not_configured'
    if os.environ.get('KICK_ACCESS_TOKEN') and os.environ.get('KICK_BROADCASTER_USER_ID'):
        try:
            results['kick']=kick_title(kick_request(os.environ['KICK_ACCESS_TOKEN']),title,os.environ['KICK_BROADCASTER_USER_ID'])
        except Exception:
            results['kick']='update_failed'
    else:
        results['kick']='not_configured'
    _append_title_event(
        "result", skip_reason="none",
        youtube=results["youtube"], kick=results["kick"], source_sha=source_sha,
        call_condition=call_condition,
    )
    print(json.dumps(results))
    return 0

if __name__=='__main__':
    raise SystemExit(main())
