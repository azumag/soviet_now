#!/usr/bin/env python3
"""
tools/podcast_publish.py - ポッドキャスト動画を YouTube へ出す (docich#10)

やること:
  1. サムネイルを作る (doci.thumbnail)
  2. 動画をアップロードする (doci.youtube.upload)
  3. サムネイルを設定する
  4. 「<番組名>」再生リストへ入れる (doci.youtube.ensure_playlist / add_video_to_playlist)
  5. その再生リストを Podcast として指定する (playlists.update の status.podcastStatus)

YouTube では Podcast = 「Podcast 指定された再生リスト」、Episode = その中の通常動画
という構造なので、動画は普通にアップロードして通常のアルゴリズムに乗せたうえで、
再生リスト側を Podcast にする。

使い方:
  ./tools/podcast_publish.py --date 20260825 [--dry-run] [--privacy unlisted]
  --dry-run: 何も送信せず、送る内容だけ表示する
  --force:   既に <日付>.publish.json がある回でも再アップロードする

同じ日付を二度上げると YouTube に重複動画ができるため、publish.json がある回は
既定でスキップする (2026-08-26 に検証実行で実際に重複アップロードを起こした)。

必要な認証 (再生リスト操作に force-ssl が要るので --manage 付きで取ること):
  cd <doci> && ./.venv/bin/python -m doci.youtube --auth --channel soren_news --manage
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent.parent
OUTPUT_ROOT_DEFAULT = SCRIPT_DIR / "output" / "podcast"

PODCAST_TITLE = os.environ.get("PODCAST_TITLE", "同志のための時事ニュース")
PLAYLIST_TITLE = os.environ.get("PODCAST_PLAYLIST_TITLE", PODCAST_TITLE)
PLAYLIST_PRIVACY = os.environ.get("PODCAST_PLAYLIST_PRIVACY", "public")
VIDEO_PRIVACY = os.environ.get("PODCAST_VIDEO_PRIVACY", "public")
CHANNEL_ID = os.environ.get("PODCAST_YT_CHANNEL", "soren_news")
TAGS = [t for t in os.environ.get(
    "PODCAST_VIDEO_TAGS", "時事,ニュース,解説,ポッドキャスト,国際情勢").split(",") if t]


# Google は increm 認可で「要求より多いスコープ」を返すことがあり、
# oauthlib はその差を例外にする (実測: force-ssl と yt-analytics が増えて停止)。
# 差を許容させる。付与されたスコープはトークン側で検証しているので緩めても安全。
os.environ.setdefault("OAUTHLIB_RELAX_TOKEN_SCOPE", "1")


def log(msg: str) -> None:
    print(f"[{datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}", file=sys.stderr)


def parse_date(arg: str) -> datetime.date:
    for fmt in ("%Y%m%d", "%Y-%m-%d"):
        try:
            return datetime.datetime.strptime(arg, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"invalid date: {arg}")


def find_doci() -> Path | None:
    cands = []
    if os.environ.get("DOCI_DIR"):
        cands.append(Path(os.environ["DOCI_DIR"]))
    cands += [
        Path("/Users/azumag/azumag/work/doci/repo"),
        Path.home() / "azumag" / "work" / "doci" / "repo",
        Path("/home/ubuntu/doci"),
    ]
    for c in cands:
        if (c / "doci" / "youtube.py").exists():
            return c.resolve()
    return None


def channel_secrets(doci_dir: Path) -> tuple[Path, Path]:
    """チャンネルの client_secret / token のパスを返す。"""
    base = doci_dir / "secrets" / CHANNEL_ID
    return base / "client_secret.json", base / "youtube_token.json"


def set_playlist_podcast(doci_dir: Path, playlist_id: str, title: str,
                         description: str, token: Path, secret: Path) -> bool:
    """再生リストを Podcast として指定する。

    YouTube Data API v3 の playlists.update に status.podcastStatus="enabled" を
    渡す。doci 側にはこの機能が無いので、認証だけ借りて直接叩く。
    """
    sys.path.insert(0, str(doci_dir))
    from doci import youtube as yt
    creds = yt._load_credentials(interactive=False, token_file=token,
                                 client_secret_file=secret, scopes=yt.MANAGE_SCOPES)
    service = yt._build_service(creds)
    try:
        cur = service.playlists().list(part="status", id=playlist_id).execute()
        items = cur.get("items") or []
        already = (items[0].get("status", {}).get("podcastStatus") if items else None)
        if already == "enabled":
            log(f"再生リストは既に Podcast 指定済み ({playlist_id})")
            return True
        service.playlists().update(
            part="snippet,status",
            body={
                "id": playlist_id,
                "snippet": {"title": title, "description": description},
                "status": {"privacyStatus": PLAYLIST_PRIVACY, "podcastStatus": "enabled"},
            },
        ).execute()
    except Exception as e:
        log(f"Podcast 指定に失敗: {e}")
        return False
    log(f"再生リストを Podcast に指定した ({playlist_id})")
    return True


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", help="YYYYMMDD or YYYY-MM-DD (default: yesterday)")
    ap.add_argument("--out-dir", default=str(OUTPUT_ROOT_DEFAULT))
    ap.add_argument("--dry-run", action="store_true", help="送信せず内容だけ表示")
    ap.add_argument("--privacy", default=VIDEO_PRIVACY, help="public/unlisted/private")
    ap.add_argument("--no-playlist", action="store_true", help="再生リストへ入れない")
    ap.add_argument("--force", action="store_true",
                    help="公開済み (publish.json がある) でも再アップロードする")
    args = ap.parse_args()

    date = parse_date(args.date) if args.date else datetime.date.today() - datetime.timedelta(days=1)
    iso = date.isoformat()
    out_dir = Path(args.out_dir)

    mp4 = out_dir / f"{iso}.mp4"
    meta_file = out_dir / f"{iso}.meta.json"
    desc_file = out_dir / f"{iso}.description.txt"

    # 二重アップロード防止: 同じ日付を上げ直すと YouTube に重複動画ができる。
    publish_file = out_dir / f"{iso}.publish.json"
    if publish_file.exists() and not args.force and not args.dry_run:
        prev = json.loads(publish_file.read_text(encoding="utf-8"))
        log(f"公開済みなのでスキップ: {prev.get('url')} (--force で再アップロード)")
        print(prev.get("url", ""))
        return 0

    if not mp4.exists():
        log(f"動画が無い: {mp4} (先に podcast_video_build.py を実行してください)")
        return 2

    meta = json.loads(meta_file.read_text(encoding="utf-8")) if meta_file.exists() else {}
    ep_title = meta.get("title") or f"{date.strftime('%Y年%m月%d日')} 時事まとめ"
    title = f"【{date.strftime('%-m/%-d')}】{ep_title}｜{PODCAST_TITLE}"[:100]
    description = desc_file.read_text(encoding="utf-8") if desc_file.exists() else meta.get("summary", "")

    log(f"タイトル: {title}")
    log(f"公開設定: {args.privacy} / 再生リスト: {PLAYLIST_TITLE} ({PLAYLIST_PRIVACY})")
    log(f"動画: {mp4} ({mp4.stat().st_size / 1048576:.1f} MB)")

    doci_dir = find_doci()
    if not doci_dir:
        log("doci が見つからない (DOCI_DIR を設定してください)")
        return 2
    secret, token = channel_secrets(doci_dir)
    if not token.exists():
        log(f"YouTube token が無い: {token}")
        log(f"  cd {doci_dir} && ./.venv/bin/python -m doci.youtube "
            f"--auth --channel {CHANNEL_ID} --manage")
        return 3

    if args.dry_run:
        log("dry-run: 送信しない")
        print("--- 説明欄 ---")
        print(description)
        return 0

    sys.path.insert(0, str(doci_dir))
    from doci import thumbnail as dthumb
    from doci import youtube as yt
    from doci.channel import ThumbnailStyle

    # 1. サムネイル (16:9)
    thumb = out_dir / f"{iso}.thumbnail.png"
    try:
        dthumb.render(ep_title, thumb, width=1280, height=720, style=ThumbnailStyle())
        log(f"サムネイル: {thumb} ({thumb.stat().st_size} bytes)")
    except Exception as e:
        log(f"サムネイル生成に失敗、サムネ無しで続行: {e}")
        thumb = None

    # 2. アップロード
    log("アップロード開始")
    video_id = yt.upload(mp4, title, description, TAGS, args.privacy,
                         token_file=token, client_secret_file=secret)
    if not video_id:
        log("アップロードに失敗")
        return 2
    url = f"https://www.youtube.com/watch?v={video_id}"
    log(f"アップロード完了: {url}")

    # 3. サムネイル設定
    if thumb and thumb.exists():
        try:
            yt.set_thumbnail(video_id, thumb, token_file=token, client_secret_file=secret)
            log("サムネイルを設定した")
        except Exception as e:
            log(f"サムネイル設定に失敗: {e}")

    # 4-5. 再生リストへ入れて Podcast 指定
    playlist_id = None
    if not args.no_playlist:
        try:
            playlist_id = yt.ensure_playlist(
                PLAYLIST_TITLE, description=meta.get("summary", ""),
                privacy=PLAYLIST_PRIVACY, token_file=token, client_secret_file=secret)
            yt.add_video_to_playlist(playlist_id, video_id,
                                     token_file=token, client_secret_file=secret)
            log(f"再生リストへ追加: {playlist_id}")
            set_playlist_podcast(doci_dir, playlist_id, PLAYLIST_TITLE,
                                 meta.get("summary", ""), token, secret)
        except Exception as e:
            log(f"再生リスト操作に失敗 (動画自体は公開済み): {e}")
            log("  force-ssl スコープが要る。--manage 付きで再認証してください")

    result = {"date": iso, "video_id": video_id, "url": url,
              "playlist_id": playlist_id, "title": title, "privacy": args.privacy}
    publish_file.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    log(f"公開情報を記録: {publish_file}")
    print(url)
    return 0


if __name__ == "__main__":
    sys.exit(main())
