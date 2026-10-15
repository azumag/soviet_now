"""strategy ハッシュアーカイブ (.py / .py.gz) の透過 reader ヘルパー。

`strategy_versions/by_hash`（作業アーカイブ）と prune されない
`strategy_versions_archive/by_hash`（永久アーカイブ）には、同じ内容の戦略
スナップショットが `<hash>.py` として置かれる。将来この古い世代を gzip
(`<hash>.py.gz`) 化しても restore / backfill / rollback 経路が壊れないよう、
reader は候補パスを各ディレクトリごとに `<hash>.py.gz` → `<hash>.py` の順で
解決し、読み出しは gzip を透過的に解凍する。

Phase A は純加算的: `.py` しか無い現状では候補順・読み出し結果ともに従来と
同じ。writer は従来どおり平文 `<hash>.py` を書く。

heredoc からは `from lib.strategy_archive import ...` で読み込む（`python3 -`
は cwd を sys.path に含むため、リポジトリ直下で実行される eloop 群から使える）。
"""

from __future__ import annotations

import gzip
import os
import shutil

__all__ = [
    "candidate_paths",
    "archive_paths",
    "open_archive",
    "read_archive",
    "is_runtime_stable",
    "find_path",
    "materialize_plaintext",
    "resolve_plaintext",
]


def candidate_paths(hash_value, dirs):
    """`<dir>/<hash>.py.gz` → `<dir>/<hash>.py` をディレクトリごとに列挙する。"""
    if not hash_value:
        return
    for base in dirs:
        if not base:
            continue
        base = str(base)
        yield os.path.join(base, f"{hash_value}.py.gz")
        yield os.path.join(base, f"{hash_value}.py")


def archive_paths(hash_value, archive_dir="", permanent_archive_dir=""):
    """作業アーカイブ → 永久アーカイブの順に候補パスを列挙する。"""
    return list(candidate_paths(hash_value, [archive_dir, permanent_archive_dir]))


def open_archive(path, mode="rt", **kwargs):
    """`.py.gz` は gzip として、それ以外は素の open() として開く。"""
    if str(path).endswith(".gz"):
        return gzip.open(path, mode, **kwargs)
    return open(path, mode, **kwargs)


def read_archive(path, limit=None):
    """gz を透過的に解凍してテキストを読む。"""
    with open_archive(path, "rt", encoding="utf-8", errors="ignore") as f:
        data = f.read(limit) if limit is not None else f.read()
    if isinstance(data, bytes):
        data = data.decode("utf-8", "ignore")
    return data


def is_runtime_stable(path):
    """validate_strategy が注入する deadline guard を持つ安定 archive か。"""
    try:
        return "BEGIN DEADLINE GUARD" in read_archive(path, 200000)
    except Exception:
        return False


def find_path(hash_value, dirs, predicate=None):
    """候補のうち実在し predicate を満たす最初のパスを返す（無ければ ""）。"""
    for path in candidate_paths(hash_value, dirs):
        if not os.path.exists(path):
            continue
        if predicate is None or predicate(path):
            return path
    return ""


def materialize_plaintext(src, dst):
    """src (.py.gz 可) を平文 dst へ書き出す。成功したら dst、失敗したら ""。"""
    if not src or not dst:
        return ""
    try:
        os.makedirs(os.path.dirname(str(dst)) or ".", exist_ok=True)
        if str(src).endswith(".gz"):
            with open_archive(src, "rb") as rf, open(dst, "wb") as wf:
                shutil.copyfileobj(rf, wf)
        else:
            shutil.copy2(src, dst)
        return dst
    except Exception:
        return ""


def resolve_plaintext(hash_value, archive_dir="", permanent_archive_dir="", predicate=None):
    """平文で読める実ファイルパスを返す。

    候補が `.py.gz` の場合は作業アーカイブ (`<archive_dir>/<hash>.py`) へ解凍し、
    その平文パスを返す。下流の `cp` / `exec` / `validate_strategy` / sandbox 参照は
    平文を前提とするため、resolver は常に平文パスを返す契約にする。解決できなければ ""。
    """
    path = find_path(hash_value, [archive_dir, permanent_archive_dir], predicate)
    if not path:
        return ""
    if not str(path).endswith(".gz"):
        return path
    base = archive_dir or os.path.dirname(str(path))
    return materialize_plaintext(path, os.path.join(str(base), f"{hash_value}.py"))
