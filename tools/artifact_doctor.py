#!/usr/bin/env python3
"""artifact doctor --dry-run (soviet_now#146).

削除・変更を一切行わない棚卸しツール。artifacts.toml の分類を
top-level エントリ（tracked + untracked）へ適用し、

- unclassified（分類なし → unknown-needs-owner 扱い）
- max_size_mb 超過の generated artifact
- broken pointer（.gitignore 対象外の tracked パス欠落は発生しないため
  worktree の .git ファイルから指す gitdir 不在を検査）

を報告する。exit code: 問題があれば 1、清潔なら 0、--dry-run で必ず変更なし。
"""

from __future__ import annotations

import fnmatch
import json
import os
import subprocess
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOML_PATH = ROOT / "artifacts.toml"


def _tracked_top_level() -> set[str]:
    out = subprocess.run(
        ["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout
    return {line.split("/", 1)[0] for line in out.splitlines() if line.strip()}


def _untracked_top_level() -> set[str]:
    out = subprocess.run(
        ["git", "ls-files", "--others", "--exclude-standard", "--directory"],
        cwd=ROOT, capture_output=True, text=True, check=True,
    ).stdout
    return {line.rstrip("/").split("/", 1)[0] for line in out.splitlines() if line.strip()}


def _dir_size_mb(path: Path) -> float:
    total = 0
    for base, _dirs, files in os.walk(path):
        for name in files:
            try:
                total += (Path(base) / name).lstat().st_size
            except OSError:
                continue
    return total / (1024 * 1024)


def _classify(name: str, artifacts: list[dict]) -> dict | None:
    """先勝ちの glob マッチ。top-level エントリ名のみ比較する。"""
    for entry in artifacts:
        if fnmatch.fnmatchcase(name, entry.get("pattern", "")):
            return entry
    return None


def main() -> int:
    dry_run = "--dry-run" in sys.argv
    if not dry_run:
        print("artifact doctor は --dry-run のみ対応（変更・削除を一切行わない）", file=sys.stderr)
        return 2

    artifacts = tomllib.loads(TOML_PATH.read_text(encoding="utf-8"))["artifact"]

    tracked = _tracked_top_level()
    untracked = _untracked_top_level()
    all_entries = sorted(tracked | untracked)

    report = {
        "unclassified": [],
        "oversize": [],
        "broken_pointer": [],
        "summary": {"tracked": len(tracked), "untracked": len(untracked), "classified": 0},
    }

    for name in all_entries:
        entry = _classify(name, artifacts)
        if entry is None:
            report["unclassified"].append({"name": name, "owner": "unknown-needs-owner"})
            continue
        report["summary"]["classified"] += 1
        max_mb = entry.get("max_size_mb")
        path = ROOT / name
        if max_mb and path.is_dir():
            size = _dir_size_mb(path)
            if size > max_mb:
                report["oversize"].append(
                    {"name": name, "size_mb": round(size, 1), "max_size_mb": max_mb}
                )

    # worktree の .git ファイル → gitdir ポインタの生存検査
    for wt in (ROOT / ".worktrees").glob("*") if (ROOT / ".worktrees").is_dir() else []:
        gitfile = wt / ".git"
        if not gitfile.is_file():
            continue
        try:
            target = gitfile.read_text().split(":", 1)[1].strip()
        except (OSError, IndexError):
            continue
        if not Path(target).exists():
            report["broken_pointer"].append({"name": str(wt), "gitdir": target})

    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 1 if (report["unclassified"] or report["oversize"] or report["broken_pointer"]) else 0


if __name__ == "__main__":
    raise SystemExit(main())
