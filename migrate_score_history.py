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
            score = int(subject.split("score=")[1].split()[0])
        except (ValueError, IndexError):
            continue
        entries.append((timestamp, score))
    return entries


def load_scores():
    """score_history.txt から現在のスコアリストを読み込み"""
    lines = SCORE_FILE.read_text().splitlines()
    scores = []
    for line in lines:
        stripped = line.strip()
        if stripped:
            scores.append(int(stripped))
    return scores


def main():
    dry_run = "--dry-run" in sys.argv

    if not SCORE_FILE.exists():
        print("ERROR: score_history.txt not found")
        sys.exit(1)

    current_scores = load_scores()
    git_entries = get_git_game_entries()

    print(f"score_history.txt: {len(current_scores)} scores")
    print(f"git log entries:   {len(git_entries)} entries")

    if len(git_entries) != len(current_scores):
        print(f"WARNING: count mismatch ({len(git_entries)} git vs {len(current_scores)} file)")
        # Try to match from the beginning
        if len(git_entries) < len(current_scores):
            print("git entries are fewer than scores - will leave extra scores without timestamp")
        else:
            print("ERROR: git entries are more than scores - cannot reconcile")
            sys.exit(1)

    # Verify scores match where we have git entries
    mismatches = []
    for i, (ts, git_score) in enumerate(git_entries):
        if i < len(current_scores) and git_score != current_scores[i]:
            mismatches.append((i + 1, current_scores[i], git_score))

    if mismatches:
        print(f"ERROR: {len(mismatches)} score mismatches found:")
        for line_num, file_score, git_score in mismatches[:10]:
            print(f"  Line {line_num}: file={file_score}, git={git_score}")
        if len(mismatches) > 10:
            print(f"  ... and {len(mismatches) - 10} more")
        sys.exit(1)

    print("Score verification: OK (all scores match)")

    # Build new content
    new_lines = []
    for i, score in enumerate(current_scores):
        if i < len(git_entries):
            ts, _ = git_entries[i]
            new_lines.append(f"{ts}\t{score}")
        else:
            # No timestamp available for extra scores
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
