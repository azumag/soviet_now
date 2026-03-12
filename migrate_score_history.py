#!/usr/bin/env python3
"""migrate_score_history.py - score_history.txt にタイムスタンプを付与する

git log から各ゲームのコミット日時を復元し、score_history.txt を
旧形式 (スコアのみ) から新形式 (ISO8601タブスコア) に変換する。

Usage:
    python3 migrate_score_history.py [--dry-run]
"""
import subprocess
import sys
import shutil
from pathlib import Path

SCORE_FILE = Path("score_history.txt")
BACKUP_FILE = Path("score_history.txt.bak")

def get_git_game_entries():
    """git log から (ISO timestamp, score) のリストを取得"""
    result = subprocess.run(
        ["git", "log", "--format=%aI %s", "--grep=eloop Game", "--reverse"],
        capture_output=True, text=True, check=True
    )
    entries = []
    for line in result.stdout.strip().splitlines():
        if not line.strip():
            continue
        # format: "2026-02-26T13:35:34+09:00 eloop Game #123: score=1212"
        parts = line.split(" ", 1)
        if len(parts) < 2:
            continue
        timestamp = parts[0]
        subject = parts[1]
        # Extract score from "eloop Game #NNN: score=XXXX"
        if "score=" not in subject:
            continue
        try:
            score_str = subject.split("score=")[1].split()[0].rstrip(",")
            score = int(score_str)
        except (ValueError, IndexError):
            continue
        entries.append((timestamp, score))
    return entries


def load_lines():
    """score_history.txt の全行を (timestamp_or_none, score) で返す"""
    raw_lines = SCORE_FILE.read_text().splitlines()
    entries = []
    for line in raw_lines:
        stripped = line.strip()
        if not stripped:
            continue
        parts = stripped.split('\t')
        if len(parts) >= 2:
            # Already new format: "TIMESTAMP\tSCORE"
            entries.append((parts[0], int(parts[-1])))
        else:
            entries.append((None, int(parts[0])))
    return entries


def main():
    dry_run = "--dry-run" in sys.argv

    if not SCORE_FILE.exists():
        print("ERROR: score_history.txt not found")
        sys.exit(1)

    current_entries = load_lines()
    git_entries = get_git_game_entries()

    # Count lines that still need migration (no timestamp)
    needs_migration = [(i, s) for i, (ts, s) in enumerate(current_entries) if ts is None]
    already_migrated = [(i, ts, s) for i, (ts, s) in enumerate(current_entries) if ts is not None]

    print(f"score_history.txt: {len(current_entries)} entries")
    print(f"  already migrated: {len(already_migrated)}")
    print(f"  needs migration:  {len(needs_migration)}")
    print(f"git log entries:    {len(git_entries)} entries")

    if not needs_migration:
        print("All entries already have timestamps. Nothing to do.")
        return

    # Match git entries to file entries by score sequence
    # The file's score sequence should match the git log's score sequence
    current_scores = [s for _, s in current_entries]
    git_scores = [s for _, s in git_entries]

    # Verify the git scores match the file scores
    mismatches = []
    for i in range(min(len(git_entries), len(current_entries))):
        git_score = git_scores[i]
        file_score = current_scores[i]
        if git_score != file_score:
            mismatches.append((i + 1, file_score, git_score))

    if mismatches:
        print(f"ERROR: {len(mismatches)} score mismatches found:")
        for line_num, file_score, git_score in mismatches[:10]:
            print(f"  Line {line_num}: file={file_score}, git={git_score}")
        if len(mismatches) > 10:
            print(f"  ... and {len(mismatches) - 10} more")
        sys.exit(1)

    print("Score verification: OK (all matched scores are consistent)")

    # Build new content
    new_lines = []
    for i, (existing_ts, score) in enumerate(current_entries):
        if existing_ts is not None:
            # Already has timestamp
            new_lines.append(f"{existing_ts}\t{score}")
        elif i < len(git_entries):
            ts, _ = git_entries[i]
            new_lines.append(f"{ts}\t{score}")
        else:
            # No timestamp available
            new_lines.append(str(score))

    new_content = "\n".join(new_lines) + "\n"

    if dry_run:
        print("\n[DRY RUN] Would write:")
        print(f"  First line: {new_lines[0]}")
        print(f"  Last line:  {new_lines[-1]}")
        print(f"  Total lines: {len(new_lines)}")
        print("\n[DRY RUN] No changes made.")
        return

    # Backup
    shutil.copy2(SCORE_FILE, BACKUP_FILE)
    print(f"Backup: {BACKUP_FILE}")

    # Write
    SCORE_FILE.write_text(new_content)
    print(f"Written: {SCORE_FILE} ({len(new_lines)} lines)")
    print(f"First: {new_lines[0]}")
    print(f"Last:  {new_lines[-1]}")


if __name__ == "__main__":
    main()
