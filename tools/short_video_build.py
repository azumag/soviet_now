#!/usr/bin/env python3
"""
tools/short_video_build.py - 単発ニュースをショート動画化 (docich#10)

入力: backups/radio_scripts/<YYYYMMDD>/radio_*_news_*.txt から1本を選定
出力: doci の channels/soren_news 経由で縦動画を生成 (doci/run_daily を外部呼出し)

使い方:
  ./tools/short_video_build.py --pick-one [--date YYYYMMDD] [--dry-run] [--no-upload]
  --pick-one: 当日の未動画化ニュースから1本を選定 (既定)
  --date: 対象日 (無指定は今日)
  --dry-run: doci 呼出しをスキップし、選定結果のみ表示
  --no-upload: doci の --no-upload を付与 (既定で付与、実投稿はしない)
  --doci-dir: doci リポジトリのパス (既定: ../doci/repo or ~/doci)

依存: python3, doci (azumag/doci), ffmpeg (doci側で利用)
"""

from __future__ import annotations

import argparse
import datetime
import glob
import json
import os
import re
import subprocess
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent.parent
# バックアップの探索: 環境変数 > Macのクローン > VMローカル
def _backup_root() -> Path:
    for env in ("SOREN_RADIO_ARCHIVE", "RADIO_ARCHIVE_DIR", "RADIO_ARCHIVE_GIT_DIR"):
        v = os.environ.get(env)
        if v:
            # RADIO_ARCHIVE_GIT_DIR は tmp/radio_archive_mirror を指すことがあるので、backups へ正規化
            p = Path(v)
            if p.name == "radio_archive_mirror":
                p = p.parent / "backups" / "radio_scripts"
            elif (p / "backups" / "radio_scripts").exists():
                p = p / "backups" / "radio_scripts"
            elif p.name == "soren-radio-archive":
                p = p / "backups" / "radio_scripts"
            if p.exists():
                return p
            # 環境変数が直接 backups/radio_scripts を指している場合
            if (p / "20260817").exists() or p.name == "radio_scripts":
                return p
    # Macのクローン
    for cand in [Path.home() / "soren-radio-archive" / "backups" / "radio_scripts",
                 Path("/Users/azumag/soren-radio-archive/backups/radio_scripts"),
                 SCRIPT_DIR / "backups" / "radio_scripts"]:
        if cand.exists():
            return cand
    return SCRIPT_DIR / "backups" / "radio_scripts"

BACKUP_ROOT = _backup_root()

# doci の探索: 環境変数 > 相対パス > 絶対パス
def find_doci() -> Path | None:
    candidates = []
    if os.environ.get("DOCI_DIR"):
        candidates.append(Path(os.environ["DOCI_DIR"]))
    # 相対: ../../doci/repo (docich から見て)
    candidates.append(SCRIPT_DIR / ".." / ".." / "azumag" / "work" / "doci" / "repo")
    candidates.append(Path("/Users/azumag/azumag/work/doci/repo"))
    candidates.append(Path("/home/ubuntu/doci"))
    candidates.append(Path("/home/ubuntu/soren/../doci"))
    for c in candidates:
        if (c / "doci" / "run_daily.py").exists():
            return c.resolve()
        if (c / "pyproject.toml").exists() and (c / "doci").exists():
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

def collect_news(date: datetime.date) -> list[Path]:
    root = _backup_root()
    yyyymmdd = date.strftime("%Y%m%d")
    pattern = str(root / yyyymmdd / "radio_*_news_*.txt")
    files = [Path(p) for p in glob.glob(pattern)]
    files.sort()
    # デバッグ用に root をログ (最初の1回だけ)
    if not hasattr(collect_news, "_logged"):
        log(f"backup_root: {root} (exists={root.exists()})")
        collect_news._logged = True
    return files

def pick_one(files: list[Path], doci_dir: Path | None) -> Path | None:
    if not files:
        return None
    # 簡易: 最新の1本を選定 (ファイル名のタイムスタンプでソート済みなので末尾)
    # 将来は doci の history.jsonl と topic_ledger を見て未動画化を選定
    # MVPでは単純に最新の1本
    # 重複排除: 既に doci の output に同タイトルがあればスキップ (簡易)
    if doci_dir:
        # doci の history を見て、最近のタイトルを取得
        try:
            hist = doci_dir / "output" / "soren_news" / "history.jsonl"
            if hist.exists():
                recent = set()
                for line in hist.read_text(encoding="utf-8").splitlines()[-20:]:
                    try:
                        j = json.loads(line)
                        recent.add(j.get("title", "")[:30])
                    except:
                        pass
                # タイトルが重複するものは除外
                for f in reversed(files):
                    title = f.read_text(encoding="utf-8").splitlines()[3] if len(f.read_text(encoding="utf-8").splitlines()) > 3 else f.name
                    if title[:30] not in recent:
                        return f
        except Exception as e:
            log(f"history check failed: {e}")
    return files[-1]

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pick-one", action="store_true", default=True, help="pick one news (default)")
    ap.add_argument("--date", help="YYYYMMDD or YYYY-MM-DD (default: today)")
    ap.add_argument("--dry-run", action="store_true", help="skip doci call")
    ap.add_argument("--no-upload", action="store_true", default=True, help="pass --no-upload to doci (default true)")
    ap.add_argument("--do-upload", action="store_true", help="allow upload (override --no-upload)")
    ap.add_argument("--doci-dir", help="doci repo path")
    ap.add_argument("--corner", default="news_short", help="doci corner")
    ap.add_argument("--channel", default="soren_news", help="doci channel")
    args = ap.parse_args()

    if args.date:
        date = parse_date(args.date)
    else:
        date = datetime.date.today()

    yyyymmdd = date.strftime("%Y%m%d")
    files = collect_news(date)
    if not files:
        # 今日に無ければ昨日も探す (Podcast と同様)
        yesterday = date - datetime.timedelta(days=1)
        files = collect_news(yesterday)
        if files:
            log(f"no files for {yyyymmdd}, using yesterday {yesterday.strftime('%Y%m%d')} ({len(files)} files)")
            date = yesterday
            yyyymmdd = date.strftime("%Y%m%d")

    if not files:
        log(f"no news files for {yyyymmdd} -> skip")
        return 0

    log(f"found {len(files)} news files for {yyyymmdd}")

    doci_dir = Path(args.doci_dir) if args.doci_dir else find_doci()
    if not doci_dir or not doci_dir.exists():
        log(f"doci not found (tried {doci_dir}), skipping doci call (dry-run mode)")
        # 選定のみ表示して終了
        picked = pick_one(files, None)
        if picked:
            log(f"picked: {picked.name}")
            text = picked.read_text(encoding="utf-8", errors="ignore")
            log(f"preview: {text[:200]}...")
        return 0

    picked = pick_one(files, doci_dir)
    if not picked:
        log("no pickable file")
        return 0

    log(f"picked: {picked} ({picked.stat().st_size} bytes)")
    text = picked.read_text(encoding="utf-8", errors="ignore")
    # 先頭の時報を除去したプレビュー
    preview = text[:500].replace("\n", " ")
    log(f"preview: {preview[:200]}...")

    if args.dry_run:
        log("dry-run: would call doci")
        log(f"  doci: {doci_dir}")
        log(f"  channel: {args.channel}, corner: {args.corner}, date: {date.isoformat()}")
        # doci の history を見て重複チェックの dry-run
        return 0

    # doci 呼出し: python -m doci.run_daily --channel soren_news --corner news_short --date <iso> --no-upload
    # 環境変数 PUBLISH_DRY_RUN=1 は doci 側で安全弁として優先されるが、--no-upload で十分
    do_upload = args.do_upload and not args.no_upload
    cmd = [
        sys.executable, "-m", "doci.run_daily",
        "--channel", args.channel,
        "--corner", args.corner,
        "--date", date.isoformat(),
    ]
    if not do_upload:
        cmd.append("--no-upload")
    # 必要なら DOCI_DIR を PYTHONPATH に含める?
    env = os.environ.copy()
    # doci のための環境変数を引き継ぐ
    log(f"calling doci: {' '.join(cmd)} (cwd={doci_dir})")
    result = subprocess.run(cmd, cwd=str(doci_dir), env=env)
    if result.returncode != 0:
        log(f"doci failed with rc={result.returncode}")
        return result.returncode

    log("doci succeeded")
    # 出力の確認: output/soren_news/<date>_* に video.mp4 ができる
    out_pattern = str(doci_dir / "output" / args.channel / f"{date.isoformat()}*")
    outs = glob.glob(out_pattern)
    if outs:
        log(f"output dirs: {outs[:3]}")
        for o in outs[:1]:
            mp4 = Path(o) / "video.mp4"
            if mp4.exists():
                log(f"video: {mp4} ({mp4.stat().st_size} bytes)")
            else:
                log(f"no video.mp4 in {o}, listing: {list(Path(o).iterdir())[:5]}")

    return 0

if __name__ == "__main__":
    sys.exit(main())
