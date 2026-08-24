#!/usr/bin/env python3
"""
tools/podcast_build.py - 日次ポッドキャスト生成 (docich#10 / soviet_now#113)

入力: backups/radio_scripts/<YYYYMMDD>/*.txt のうち news/jiji のみ
出力: output/podcast/<YYYY-MM-DD>.mp3 + feed.xml + chapters.json

使い方:
  ./tools/podcast_build.py --date 20260824 [--dry-run] [--out-dir output/podcast] [--voice 109]
  --date: YYYYMMDD または YYYY-MM-DD (無指定は昨日)
  --dry-run: 合成・MP3生成をスキップし、対象ファイルとRSSのみ表示
  --dummy: VOICEVOXが無い環境で無音WAVを生成 (テスト用)

依存: python3, ffmpeg, ffprobe, voicevox_tts.sh (VOICEVOX) or docich voicevox synth
任意: rclone/oci (アップロード時)

RSSは RSS 2.0 + iTunes 拡張。feed.xml は冪等に更新 (最新50件)。
"""

from __future__ import annotations

import argparse
import datetime
import glob
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent.parent
# backups は ELOOP_LIB_DIR 相対だが、通常は SCRIPT_DIR/backups
BACKUP_ROOT = SCRIPT_DIR / "backups" / "radio_scripts"
OUTPUT_ROOT_DEFAULT = SCRIPT_DIR / "output" / "podcast"
VOICEVOX_TTS = SCRIPT_DIR / "voicevox_tts.sh"

# 設定 (環境変数で上書き可)
PODCAST_TITLE = os.environ.get("PODCAST_TITLE", "ソ連ゲーム時事ラジオ")
PODCAST_LINK = os.environ.get("PODCAST_LINK", "https://github.com/azumag/soren-radio-archive")
PODCAST_DESCRIPTION = os.environ.get("PODCAST_DESCRIPTION", "ソ連ゲーム配信の時事ニュース・考察を1日1本にまとめたポッドキャスト。VOICEVOX:東北イタコ")
PODCAST_AUTHOR = os.environ.get("PODCAST_AUTHOR", "Soren Radio")
PODCAST_EMAIL = os.environ.get("PODCAST_EMAIL", "archive@soren.local")
PODCAST_IMAGE = os.environ.get("PODCAST_IMAGE", "https://example.com/podcast/cover.jpg")
PODCAST_CATEGORY = os.environ.get("PODCAST_CATEGORY", "News")
PODCAST_BASE_URL = os.environ.get("PODCAST_BASE_URL", "https://example.com/podcast")
PODCAST_LANGUAGE = os.environ.get("PODCAST_LANGUAGE", "ja")

def log(msg: str):
    print(f"[{datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}", file=sys.stderr)

def parse_date(arg: str) -> datetime.date:
    for fmt in ("%Y%m%d", "%Y-%m-%d"):
        try:
            return datetime.datetime.strptime(arg, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"invalid date: {arg} (expected YYYYMMDD or YYYY-MM-DD)")

def collect_files(date: datetime.date) -> list[Path]:
    yyyymmdd = date.strftime("%Y%m%d")
    pattern = str(BACKUP_ROOT / yyyymmdd / "radio_*.txt")
    files = [Path(p) for p in glob.glob(pattern)]
    # news/jiji のみ
    filtered = []
    for f in files:
        name = f.name
        # radio_<ts>_<game>_<corner>_<rand>.txt
        m = re.search(r"_([a-z_]+)_\d+\.txt$", name)
        corner = m.group(1) if m else ""
        # corner が news / jiji のみ対象 (テーマ等は除外)
        if corner in ("news", "jiji"):
            filtered.append(f)
        else:
            # 旧ファイルで corner が不明でも、内容に「ニュース」等があれば拾う? 今回は厳格に news/jiji のみ
            pass
    filtered.sort()
    return filtered

def clean_script(text: str) -> str:
    """ポッドキャスト用に時報イントロを除去し、不要な末尾を整形"""
    lines = text.strip().splitlines()
    # 先頭の時報・挨拶を除去 (最大3行)
    # 例: "こんばんは、現在時刻は0時です。" / "本日のニュースです。" / "22時を..."
    cleaned = []
    skip = 0
    for i, line in enumerate(lines):
        s = line.strip()
        if i < 3 and re.search(r"(現在時刻は|おはよう|こんばんは|こんにちは|本日のニュースです|ニュースを一本)", s):
            skip = i + 1
            continue
        if i == skip and re.search(r"^22時|本日のニュースです|ニュースを一本", s):
            skip = i + 1
            continue
        if i >= skip:
            cleaned.append(line)
    # 末尾のゲーム言及を除去? ポッドキャストでは不要だが、MVPではそのまま残す
    # 空行を詰める
    text2 = "\n".join(cleaned).strip()
    # 連続空行を1つに
    text2 = re.sub(r"\n{3,}", "\n\n", text2)
    return text2

def synthesize_wav(text: str, wav_path: Path, voice: str, dummy: bool = False) -> bool:
    """VOICEVOXでWAVを生成。dummyなら無音(1秒)を生成。"""
    if dummy:
        # 1秒無音を ffprobe で後で duration を測れるように生成
        # text 長から duration を推測して無音長を変える (100字≒10秒)
        chars = len(text)
        secs = max(2, min(30, int(chars / 10) + 2))
        cmd = ["ffmpeg", "-y", "-f", "lavfi", "-i", f"anullsrc=r=24000:cl=mono", "-t", str(secs), "-q:a", "9", "-acodec", "pcm_s16le", str(wav_path)]
        result = subprocess.run(cmd, capture_output=True)
        return result.returncode == 0 and wav_path.stat().st_size > 0
    # 本番: voicevox_tts.sh 経由
    env = os.environ.copy()
    env["VOICEVOX_SPEAKER"] = voice
    # docich が無い環境でもエラーで明確に
    if not VOICEVOX_TTS.exists():
        log(f"voicevox_tts.sh not found: {VOICEVOX_TTS}")
        return False
    # 一時ファイルにテキストを書き、voicevox_tts.sh -o wav -f txt
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", suffix=".txt", delete=False) as tf:
        tf.write(text)
        tf_path = tf.name
    try:
        cmd = [str(VOICEVOX_TTS), "-o", str(wav_path), "-f", tf_path]
        # VOICEVOX_TIMEOUT を長めに
        env["VOICEVOX_TIMEOUT"] = env.get("VOICEVOX_TIMEOUT", "120")
        result = subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=180)
        if result.returncode != 0:
            log(f"voicevox failed: {result.stderr[:500]}")
            return False
        return wav_path.exists() and wav_path.stat().st_size > 0
    except subprocess.TimeoutExpired:
        log("voicevox timeout")
        return False
    finally:
        try:
            os.unlink(tf_path)
        except:
            pass

def get_duration(path: Path) -> float:
    try:
        out = subprocess.check_output(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", str(path)], text=True)
        return float(out.strip())
    except:
        return 0.0

def rss_escape(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;").replace("'", "&apos;")

def generate_rss(episodes: list[dict], out_path: Path):
    """episodes: list of {date, title, description, mp3_path, duration, pubDate, guid, url, length} sorted desc"""
    now = datetime.datetime.now(datetime.timezone.utc)
    rss = []
    rss.append('<?xml version="1.0" encoding="UTF-8"?>')
    rss.append('<rss version="2.0" xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd" xmlns:podcast="https://podcastindex.org/namespace/1.0">')
    rss.append('<channel>')
    rss.append(f'  <title>{rss_escape(PODCAST_TITLE)}</title>')
    rss.append(f'  <link>{rss_escape(PODCAST_LINK)}</link>')
    rss.append(f'  <description>{rss_escape(PODCAST_DESCRIPTION)}</description>')
    rss.append(f'  <language>{PODCAST_LANGUAGE}</language>')
    rss.append(f'  <itunes:author>{rss_escape(PODCAST_AUTHOR)}</itunes:author>')
    rss.append(f'  <itunes:owner><itunes:name>{rss_escape(PODCAST_AUTHOR)}</itunes:name><itunes:email>{PODCAST_EMAIL}</itunes:email></itunes:owner>')
    rss.append(f'  <itunes:explicit>false</itunes:explicit>')
    rss.append(f'  <itunes:category text="{rss_escape(PODCAST_CATEGORY)}" />')
    rss.append(f'  <itunes:image href="{rss_escape(PODCAST_IMAGE)}" />')
    rss.append(f'  <lastBuildDate>{now.strftime("%a, %d %b %Y %H:%M:%S %z")}</lastBuildDate>')
    for ep in episodes[:50]:
        rss.append('  <item>')
        rss.append(f'    <title>{rss_escape(ep["title"])}</title>')
        rss.append(f'    <description>{rss_escape(ep["description"])}</description>')
        rss.append(f'    <pubDate>{ep["pubDate"]}</pubDate>')
        rss.append(f'    <guid isPermaLink="false">{rss_escape(ep["guid"])}</guid>')
        rss.append(f'    <enclosure url="{rss_escape(ep["url"])}" length="{ep["length"]}" type="audio/mpeg" />')
        rss.append(f'    <itunes:duration>{int(ep["duration"])}</itunes:duration>')
        rss.append(f'    <itunes:explicit>false</itunes:explicit>')
        rss.append('  </item>')
    rss.append('</channel>')
    rss.append('</rss>')
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(rss) + "\n", encoding="utf-8")

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", help="YYYYMMDD or YYYY-MM-DD (default: yesterday)")
    ap.add_argument("--dry-run", action="store_true", help="skip synth and mp3")
    ap.add_argument("--dummy", action="store_true", help="use silent wav instead of VOICEVOX (for test)")
    ap.add_argument("--out-dir", default=str(OUTPUT_ROOT_DEFAULT), help="output dir")
    ap.add_argument("--voice", default="109", help="VOICEVOX speaker id")
    ap.add_argument("--base-url", default=PODCAST_BASE_URL, help="podcast base URL for enclosure")
    args = ap.parse_args()

    if args.date:
        date = parse_date(args.date)
    else:
        date = datetime.date.today() - datetime.timedelta(days=1)
    yyyymmdd = date.strftime("%Y%m%d")
    iso = date.isoformat()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    mp3_path = out_dir / f"{iso}.mp3"
    # 既にMP3があり、ソースより新しければスキップ (冪等)
    files = collect_files(date)
    if not files:
        log(f"no source files for {yyyymmdd} (news/jiji) -> skip")
        # 既存の RSS は更新しない
        return 0
    log(f"found {len(files)} source files for {yyyymmdd}:")
    for f in files:
        log(f"  {f.name}")

    # 既存MP3の新しさチェック
    if mp3_path.exists() and not args.dry_run:
        mtime = mp3_path.stat().st_mtime
        newest_src = max(p.stat().st_mtime for p in files)
        if mtime >= newest_src:
            log(f"mp3 already up to date: {mp3_path} -> skip synth")
            # RSS は更新が必要かチェック (後で)
            skip_synth = True
        else:
            skip_synth = False
    else:
        skip_synth = False

    if args.dry_run:
        log(f"dry-run: would synthesize {len(files)} files -> {mp3_path}")
        # RSS dry-run 表示
        log(f"dry-run: would update feed.xml with {iso}")
        return 0

    if skip_synth:
        duration = get_duration(mp3_path)
        length = mp3_path.stat().st_size
    else:
        # 合成
        tmpdir = Path(tempfile.mkdtemp(prefix="podcast_"))
        wavs = []
        try:
            # 冒頭イントロ
            intro_text = f"ソ連ゲーム時事ラジオ、{date.month}月{date.day}日のニュースをお届けします。"
            intro_wav = tmpdir / "intro.wav"
            if not synthesize_wav(intro_text, intro_wav, args.voice, dummy=args.dummy):
                log("intro synth failed, skipping intro")
            else:
                wavs.append(intro_wav)
            # 各原稿
            for idx, src in enumerate(files):
                text = src.read_text(encoding="utf-8", errors="ignore")
                cleaned = clean_script(text)
                if not cleaned:
                    log(f"skip empty after clean: {src.name}")
                    continue
                wav = tmpdir / f"{idx:03d}.wav"
                log(f"synth {src.name} -> {wav.name} ({len(cleaned)} chars)")
                if not synthesize_wav(cleaned, wav, args.voice, dummy=args.dummy):
                    log(f"synth failed for {src.name}, skipping")
                    continue
                wavs.append(wav)
                # 各原稿間に1秒無音を入れるため、無音WAVを挿入 (最後以外)
                if idx < len(files) - 1:
                    silence = tmpdir / f"silence_{idx}.wav"
                    # 1秒無音
                    subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i", "anullsrc=r=24000:cl=mono", "-t", "1", "-q:a", "9", "-acodec", "pcm_s16le", str(silence)], capture_output=True)
                    if silence.exists():
                        wavs.append(silence)
            # アウトロ
            outro_text = "以上、ソ連ゲーム時事ラジオでした。"
            outro_wav = tmpdir / "outro.wav"
            if synthesize_wav(outro_text, outro_wav, args.voice, dummy=args.dummy):
                wavs.append(outro_wav)

            if not wavs:
                log("no wavs generated, abort")
                return 2

            # concat list
            concat_list = tmpdir / "concat.txt"
            with open(concat_list, "w", encoding="utf-8") as f:
                for w in wavs:
                    # ffmpeg concat demuxer は絶対パス or 相対パス
                    f.write(f"file '{w.resolve()}'\n")

            # ffmpeg concat + loudnorm + mp3
            # 中間を wav にしてから mp3 にする (loudnorm は wav で)
            concat_wav = tmpdir / "concat.wav"
            cmd1 = ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(concat_list), "-c", "copy", str(concat_wav)]
            log(f"concat {len(wavs)} wavs -> {concat_wav}")
            r1 = subprocess.run(cmd1, capture_output=True, text=True)
            if r1.returncode != 0:
                log(f"concat failed: {r1.stderr[:1000]}")
                return 2

            # loudnorm + resample + mp3
            # 2-pass loudnorm は手間なので 1-pass で I=-16
            cmd2 = ["ffmpeg", "-y", "-i", str(concat_wav), "-filter_complex", "loudnorm=I=-16:TP=-1.5:LRA=11,aresample=44100", "-c:a", "libmp3lame", "-q:a", "2", "-id3v2_version", "3", "-write_id3v1", "1", str(mp3_path)]
            log(f"encode mp3 -> {mp3_path}")
            r2 = subprocess.run(cmd2, capture_output=True, text=True)
            if r2.returncode != 0:
                log(f"mp3 encode failed: {r2.stderr[:1000]}")
                return 2

            duration = get_duration(mp3_path)
            length = mp3_path.stat().st_size
            log(f"mp3 done: {mp3_path} ({length} bytes, {duration:.1f}s)")

        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    # RSS 更新: 既存の output 内の mp3 を列挙して feed を再生成 (最新50件)
    episodes = []
    for mp3 in sorted(out_dir.glob("*.mp3")):
        # ファイル名が YYYY-MM-DD.mp3 形式のみ対象
        m = re.match(r"(\d{4})-(\d{2})-(\d{2})\.mp3$", mp3.name)
        if not m:
            continue
        y, mo, d = m.groups()
        try:
            d_obj = datetime.date(int(y), int(mo), int(d))
        except:
            continue
        pub = datetime.datetime.combine(d_obj, datetime.time(6, 0), tzinfo=datetime.timezone(datetime.timedelta(hours=9)))
        # RFC2822
        pub_str = pub.astimezone(datetime.timezone.utc).strftime("%a, %d %b %Y %H:%M:%S %z")
        dur = get_duration(mp3)
        ln = mp3.stat().st_size
        base = args.base_url.rstrip("/")
        url = f"{base}/episodes/{mp3.name}"
        # この日付のソースから description を作る (最初の原稿の冒頭を要約)
        desc = f"{d_obj.strftime('%Y年%m月%d日')}のニュースまとめ。{len(files)}本の原稿から構成。"
        # 既存のエピソードなら上書き、無いなら新規
        episodes.append({
            "date": d_obj,
            "title": f"{d_obj.strftime('%Y年%m月%d日')} 時事ニュースまとめ",
            "description": desc,
            "mp3_path": mp3,
            "duration": dur,
            "pubDate": pub_str,
            "guid": f"soren-radio-{d_obj.isoformat()}",
            "url": url,
            "length": ln,
        })
    episodes.sort(key=lambda x: x["date"], reverse=True)
    feed_path = out_dir / "feed.xml"
    generate_rss(episodes, feed_path)
    log(f"feed updated: {feed_path} ({len(episodes)} episodes)")

    # chapters.json (簡易: 各原稿の開始時刻を等分で推測)
    if episodes and not args.dry_run:
        # 今回のエピソードのチャプターを生成 (intro + 各原稿)
        # duration を等分して概算 (正確には各wavのdurationを足すのが理想だが、今回は簡易)
        if len(files) > 0 and 'duration' in locals():
            # 簡易: 各ファイルの文字数から推測した秒数を累積
            # 実際には各wavのdurationが必要だが、MVPでは均等割り
            per = duration / len(wavs) if 'wavs' in locals() and wavs else duration / len(files)
            chapters = []
            cur = 0.0
            # intro
            chapters.append({"startTime": 0, "title": "イントロ"})
            cur += per
            for i, f in enumerate(files):
                corner = re.search(r"_([a-z_]+)_\d+\.txt$", f.name)
                c = corner.group(1) if corner else "news"
                chapters.append({"startTime": int(cur), "title": f"{c} {i+1}"})
                cur += per * 2  # wav + silence
            chapters_path = out_dir / f"{iso}.chapters.json"
            chapters_path.write_text(json.dumps({"version": "1.0.0", "chapters": chapters}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            log(f"chapters written: {chapters_path}")

    # 検証: xmllint があれば feed.xml を検証
    if shutil.which("xmllint"):
        r = subprocess.run(["xmllint", "--noout", str(feed_path)], capture_output=True)
        if r.returncode != 0:
            log(f"xmllint failed: {r.stderr.decode()[:500]}")
            return 2
        else:
            log("xmllint OK")

    # ホスティング: rclone で public bucket へアップロード (任意)
    rclone_enabled = os.environ.get("PODCAST_RCLONE_ENABLED", "0") == "1"
    rclone_remote = os.environ.get("PODCAST_RCLONE_REMOTE", "")
    rclone_bucket = os.environ.get("PODCAST_RCLONE_BUCKET", "soren-radio-archive")
    rclone_prefix = os.environ.get("PODCAST_RCLONE_PREFIX", "podcast")
    if rclone_enabled and rclone_remote and not args.dry_run:
        if shutil.which("rclone"):
            dest = f"{rclone_remote}:{rclone_bucket}/{rclone_prefix}/"
            log(f"rclone copy {out_dir} -> {dest}")
            r = subprocess.run(["rclone", "copy", str(out_dir), dest, "--progress"], capture_output=True, text=True)
            if r.returncode != 0:
                log(f"rclone copy failed: {r.stderr[:1000]}")
                # 失敗しても feed は生成済みなので警告のみで続行
            else:
                log(f"rclone copy succeeded to {dest}")
        elif shutil.which("oci"):
            # oci bulk-upload
            log(f"oci bulk-upload {out_dir} -> {rclone_bucket}/{rclone_prefix}/")
            r = subprocess.run(["oci", "os", "object", "bulk-upload", "--bucket-name", rclone_bucket, "--src-dir", str(out_dir), "--object-prefix", f"{rclone_prefix}/"], capture_output=True, text=True)
            if r.returncode != 0:
                log(f"oci bulk-upload failed: {r.stderr[:1000]}")
            else:
                log(f"oci bulk-upload succeeded")
        else:
            log("rclone/oci not found, skipping upload")

    log("podcast_build done")
    return 0

if __name__ == "__main__":
    sys.exit(main())
