"""Private durable receipt for Create Clip; no credentials or HTTP payloads."""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import sys
import tempfile
import time
from urllib.parse import urlparse

PHASES = {"creating", "accepted", "ready", "unknown", "retryable", "rejected",
          "expired", "disabled", "unconfirmed"}


def read(path):
    try:
        row = json.loads(Path(path).read_text())
    except FileNotFoundError:
        return {}
    if (not isinstance(row, dict) or row.get("schema") != 1 or row.get("phase") not in PHASES
            or not re.fullmatch(r"[A-Za-z0-9_-]{0,256}", row.get("clip_id", ""))):
        raise ValueError("invalid clip receipt")
    if row.get("phase") in {"accepted", "ready", "unconfirmed"} and not row["clip_id"]:
        raise ValueError("missing accepted clip ID")
    url = row.get("clip_url", "")
    if url:
        parsed = urlparse(url)
        if (parsed.scheme != "https" or parsed.hostname not in {"clips.twitch.tv", "www.twitch.tv"}
                or parsed.username or parsed.password):
            raise ValueError("invalid public clip URL")
    if row.get("phase") == "ready" and not url:
        raise ValueError("missing ready URL")
    return row


def write(path, phase, clip_id="", clip_url=""):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    old = read(path)
    row = {"schema": 1, "phase": phase, "clip_id": clip_id or old.get("clip_id", ""),
           "clip_url": clip_url or old.get("clip_url", ""),
           "created_at": old.get("created_at", time.time()), "updated_at": time.time(),
           "post_attempts": old.get("post_attempts", 0) + (1 if phase == "creating" else 0)}
    fd, name = tempfile.mkstemp(prefix=".receipt-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(row, stream, ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
        # Reuse the read validator before publishing any resumed ID or URL.
        read(name)
        os.replace(name, path)
        parent_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(parent_fd)
        finally:
            os.close(parent_fd)
    finally:
        if os.path.exists(name):
            os.unlink(name)
    return row


if __name__ == "__main__":
    try:
        path, command, *args = sys.argv[1:]
        if command == "read":
            row = read(path)
            print(row.get("phase", ""))
            print(row.get("clip_id", ""))
        else:
            write(path, command, *args)
    except Exception:
        # Never print malformed input or arbitrary exception text.
        print("clip receipt unavailable", file=sys.stderr)
        sys.exit(1)
