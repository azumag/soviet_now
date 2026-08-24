#!/usr/bin/env python3
"""
tools/soviet_video_build.py - ソ連ネタを動画化 (docich#10)

入力: backups/radio_scripts/<YYYYMMDD>/radio_*_soviet_*.txt 等から1本を選定
出力: doci の channels/ideology (communism) または channels/soren_news 経由で動画生成

使い方:
  ./tools/soviet_video_build.py [--date YYYYMMDD] [--dry-run]
  --dry-run: doci呼出しをスキップ

soviet, soviet_quiz, soviet_lifehack, theme(soviet) などが対象。
doci の ideology チャンネルは既に communism コーナーでソ連ネタを扱っているため、
MVPでは ideology を再利用する。将来 soren_news へ統合する場合は --channel soren_news --corner news_short を使う。
"""

import argparse
import datetime
import glob
import os
import re
import subprocess
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent.parent
BACKUP_ROOT = SCRIPT_DIR / "backups" / "radio_scripts"

def find_doci() -> Path | None:
    candidates = []
    if os.environ.get("DOCI_DIR"):
        candidates.append(Path(os.environ["DOCI_DIR"]))
    candidates.append(SCRIPT_DIR / ".." / ".." / "azumag" / "work" / "doci" / "repo")
    candidates.append(Path("/Users/azumag/azumag/work/doci/repo"))
    candidates.append(Path("/home/ubuntu/doci"))
    for c in candidates:
        if (c / "doci" / "run_daily.py").exists():
            return c.resolve()
    return None

def log(msg: str):
    print(f"[{datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}", file=sys.stderr)

def parse_date(arg: str) -> datetime.date:
    for fmt in ("%Y%m%d", "%Y-%m-%d"):
        try:
            return datetime.datetime.strptime(arg, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"invalid date: {arg}")

def collect_soviet(date: datetime.date) -> list[Path]:
    yyyymmdd = date.strftime("%Y%m%d")
    # soviet, soviet_quiz, soviet_lifehack, theme (soviet系) を対象
    patterns = [
        str(BACKUP_ROOT / yyyymmdd / "radio_*_soviet*.txt"),
        str(BACKUP_ROOT / yyyymmdd / "radio_*_soviet_*.txt"),
        str(BACKUP_ROOT / yyyymmdd / "radio_*_theme_*.txt"),
    ]
    files = []
    for pat in patterns:
        files.extend(Path(p) for p in glob.glob(pat))
    # theme のうち、内容にソ連関連が含まれるもののみ（簡易）
    filtered = []
    for f in files:
        if "theme" in f.name:
            try:
                txt = f.read_text(encoding="utf-8", errors="ignore")
                if re.search(r"ソ連|ソビエト|ロシア|共産|レーニン|スターリン", txt):
                    filtered.append(f)
            except:
                pass
        else:
            filtered.append(f)
    # 重複排除
    uniq = sorted(set(filtered))
    return uniq

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", help="YYYYMMDD or YYYY-MM-DD (default: today)")
    ap.add_argument("--dry-run", action="store_true", help="skip doci call")
    ap.add_argument("--doci-dir", help="doci repo path")
    ap.add_argument("--channel", default="ideology", help="doci channel (default: ideology for reuse)")
    ap.add_argument("--corner", default="communism", help="doci corner (default: communism)")
    args = ap.parse_args()

    if args.date:
        date = parse_date(args.date)
    else:
        date = datetime.date.today()

    files = collect_soviet(date)
    if not files:
        yesterday = date - datetime.timedelta(days=1)
        files = collect_soviet(yesterday)
        if files:
            log(f"no files for {date.strftime('%Y%m%d')}, using yesterday {yesterday.strftime('%Y%m%d')} ({len(files)} files)")
            date = yesterday

    if not files:
        log(f"no soviet files for {date.strftime('%Y%m%d')} -> skip")
        return 0

    log(f"found {len(files)} soviet files for {date.strftime('%Y%m%d')}")
    # 最新の1本
    picked = sorted(files)[-1]
    log(f"picked: {picked} ({picked.stat().st_size} bytes)")
    text = picked.read_text(encoding="utf-8", errors="ignore")
    preview = text[:500].replace("\n", " ")
    log(f"preview: {preview[:200]}...")

    doci_dir = Path(args.doci_dir) if args.doci_dir else find_doci()
    if not doci_dir or not doci_dir.exists():
        log(f"doci not found (tried {doci_dir}), skipping doci call (dry-run)")
        return 0

    if args.dry_run:
        log("dry-run: would call doci")
        log(f"  doci: {doci_dir}")
        log(f"  channel: {args.channel}, corner: {args.corner}, date: {date.isoformat()}")
        return 0

    cmd = [
        sys.executable, "-m", "doci.run_daily",
        "--channel", args.channel,
        "--corner", args.corner,
        "--date", date.isoformat(),
        "--no-upload",
    ]
    log(f"calling doci: {' '.join(cmd)} (cwd={doci_dir})")
    result = subprocess.run(cmd, cwd=str(doci_dir))
    if result.returncode != 0:
        log(f"doci failed with rc={result.returncode}")
        return result.returncode
    log("doci succeeded")
    return 0

if __name__ == "__main__":
    sys.exit(main())
