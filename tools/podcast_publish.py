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
import re
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


# ---------- サムネイル背景 (通常動画の「タイトル+静止画」を踏襲) ----------

_THUMB_QUERY_SCHEMA = {
    "type": "object",
    "properties": {"query": {"type": "string"}},
    "required": ["query"],
    "additionalProperties": False,
}

_THUMB_QUERY_PROMPT = """次のポッドキャストエピソードの背景画像を探すための検索語を英語で1件作ってください。

要件:
- query は英語で 2〜4 語。具体的に撮影できる情景にする
  (例: wildfire smoke forest / hospital medical worker / cargo ship port)
- 抽象語 (society, impact)、数値・商標・人名は使わない
- 報道写真ではなく、話題の雰囲気に合う一般的な情景でよい

エピソード: {title} — {summary}
セクション: {sections}
"""


def _thumb_query_from_meta(meta: dict, ep_title: str) -> str:
    """エピソード情報からPexels検索語(英語)を作る。LLMが使えなければ簡易fallback。"""
    title = ep_title or str(meta.get("title") or "")
    summary = str(meta.get("summary") or "")
    sections = ", ".join(str(s) for s in (meta.get("sections") or [])[:6])
    raw = f"{title} {summary} {sections}".strip()
    # LLMで英語queryを生成 (podcast_build経由)
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "pb", SCRIPT_DIR / "tools" / "podcast_build.py"
        )
        pb = importlib.util.module_from_spec(spec)  # type: ignore
        spec.loader.exec_module(pb)  # type: ignore
        obj = pb.llm_generate(  # type: ignore
            _THUMB_QUERY_PROMPT.format(title=title[:120], summary=summary[:200], sections=sections[:200]),
            schema=_THUMB_QUERY_SCHEMA,
        )
        q = str((obj or {}).get("query") or "").strip()
        if q and len(q.split()) >= 2:
            log(f"サムネ検索語(LLM): {q}")
            return q
    except Exception as e:
        log(f"サムネ検索語のLLM生成をスキップ: {e}")
    # fallback: 日本語キーワードを英語へ簡易マッピング (Pexelsは日本語に弱い)
    # タイトル→セクション→サマリの順で見て、最初に見つかった具体語を使う。
    # 長い語を優先して汎用語(海/山)が誤って選ばれないようにする。
    mapping = {
        "豪雨": "heavy rain flood",
        "洪水": "flood water",
        "氷河": "glacier mountain",
        "雪崩": "snow avalanche mountain",
        "キーウ": "city skyline kyiv",
        "ウクライナ": "city skyline",
        "イラン": "middle east city",
        "ホルムズ海峡": "strait cargo ship",
        "海峡": "strait cargo ship",
        "円安": "currency market",
        "戦争": "military conflict",
        "地震": "earthquake damage",
        "台風": "storm clouds",
        "気候": "climate nature",
        "干ばつ": "drought cracked earth",
        "山岳災害": "mountain rescue",
        "月探査": "moon space",
        "宇宙": "space station",
        "AI": "artificial intelligence technology",
        "半導体": "semiconductor factory",
        "医療": "hospital medical",
        "火災": "wildfire smoke",
        "電力": "power plant energy",
        "海": "ocean coast",
        "山": "mountain landscape",
    }
    # 長いキーから順に優先
    sorted_keys = sorted(mapping.keys(), key=len, reverse=True)

    def _pick(text: str) -> str | None:
        for ja in sorted_keys:
            if ja in text:
                return ja
        return None

    for scope, label in [(title, "title"), (sections, "sections"), (summary, "summary"), (raw, "raw")]:
        hit = _pick(scope)
        if hit:
            en = mapping[hit]
            log(f"サムネ検索語(fallback {label} mapping {hit}): {en}")
            return en
    log("サムネ検索語: news (fallback)")
    return "news"


def _fetch_thumb_bg(doci_dir: Path, query: str, workdir: Path) -> Path | None:
    """Pexelsからサムネ背景を1枚取得。失敗はNone。取得物はworkdir配下。"""
    sys.path.insert(0, str(doci_dir))
    try:
        from doci import assets  # type: ignore
    except Exception as e:
        log(f"doci.assetsを読めない: {e}")
        return None
    out = workdir / "thumb_bg.jpg"
    try:
        got = assets.fetch_image(query, out, width=1280, height=720, orientation="landscape", variant=0)
    except Exception as e:
        log(f"サムネ背景取得に失敗 ({query}): {e}")
        return None
    if got and Path(got).exists() and Path(got).stat().st_size > 0:
        log(f"サムネ背景取得: {got} ({Path(got).stat().st_size} bytes) query={query!r}")
        return Path(got)
    log(f"サムネ背景が見つからない: query={query!r}")
    return None


def _render_thumbnail(dthumb, title: str, out_path: Path, *, bg_image: Path | None, style) -> Path:
    """通常の横動画と同じ16:9構図でサムネイルを直接描画する。"""
    return dthumb.render(
        title,
        out_path,
        bg_image=bg_image,
        width=1280,
        height=720,
        style=style,
    )


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
    ap.add_argument("--thumbnail-only", action="store_true",
                    help="publish.json の既存動画へサムネイルだけを再生成・設定する")
    args = ap.parse_args()

    date = parse_date(args.date) if args.date else datetime.date.today() - datetime.timedelta(days=1)
    iso = date.isoformat()
    out_dir = Path(args.out_dir)

    mp4 = out_dir / f"{iso}.mp4"
    meta_file = out_dir / f"{iso}.meta.json"
    desc_file = out_dir / f"{iso}.description.txt"

    # 二重アップロード防止: 同じ日付を上げ直すと YouTube に重複動画ができる。
    publish_file = out_dir / f"{iso}.publish.json"
    published = None
    if publish_file.exists():
        published = json.loads(publish_file.read_text(encoding="utf-8"))
    if published and not args.force and not args.dry_run and not args.thumbnail_only:
        log(f"公開済みなのでスキップ: {published.get('url')} (--force で再アップロード)")
        print(published.get("url", ""))
        return 0

    if args.thumbnail_only and not (published or {}).get("video_id"):
        log(f"既存動画IDが無い: {publish_file}")
        return 2

    if not args.thumbnail_only and not mp4.exists():
        log(f"動画が無い: {mp4} (先に podcast_video_build.py を実行してください)")
        return 2

    meta = json.loads(meta_file.read_text(encoding="utf-8")) if meta_file.exists() else {}
    ep_title = meta.get("title") or f"{date.strftime('%Y年%m月%d日')} 時事まとめ"
    title = f"【{date.strftime('%-m/%-d')}】{ep_title}｜{PODCAST_TITLE}"[:100]
    description = desc_file.read_text(encoding="utf-8") if desc_file.exists() else meta.get("summary", "")

    log(f"タイトル: {title}")
    log(f"公開設定: {args.privacy} / 再生リスト: {PLAYLIST_TITLE} ({PLAYLIST_PRIVACY})")
    if args.thumbnail_only:
        log(f"サムネイルのみ更新: {published.get('url')}")
    else:
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

    # 1. サムネイル (16:9) — 通常の横動画と同じ「タイトル+静止画」
    #    背景はPexelsから1枚取得し、thumbnail.renderのbg_imageとして渡す。
    #    取得失敗時は従来どおりタイトルのみのタイトルカードにフォールバックする。
    #    ポッドキャスト動画は横長なので、ショート用の縦構図を経由せず1280x720へ直接描く。
    thumb = out_dir / f"{iso}.thumbnail.png"
    thumb_bg: Path | None = None
    thumb_query = _thumb_query_from_meta(meta, ep_title)
    # 一時領域はepisode毎に分けてキャッシュも兼ねる (再実行で再DLしない)
    _thumb_work = out_dir / f".thumb_cache_{iso}"
    _thumb_work.mkdir(parents=True, exist_ok=True)
    _cached_bg = _thumb_work / "bg.jpg"
    if _cached_bg.exists() and _cached_bg.stat().st_size > 0:
        # キャッシュがあればそれを使い、なければPexelsへ
        thumb_bg = _cached_bg
        log(f"サムネ背景をキャッシュから再利用: {thumb_bg}")
    else:
        thumb_bg = _fetch_thumb_bg(doci_dir, thumb_query, _thumb_work)
        if thumb_bg and thumb_bg != _cached_bg:
            try:
                import shutil
                shutil.copyfile(thumb_bg, _cached_bg)
                thumb_bg = _cached_bg
            except Exception:
                pass
    try:
        # 通常動画(soren_news)はtechテーマ(白900ゴシック+赤線+左寄せ)で生成。
        # podcastも合わせるためtechを指定。classicの生成り700明朝+金線は暗い実写で潰れて地味に見える。
        style = ThumbnailStyle(theme="tech")
        # Pexels実写は夕景などで暗いことがあるため、サムネ用に少し明るくしてから渡す
        # (動画背景の eq brightness=-0.14 とは逆)。失敗しても元画像で続行。
        if thumb_bg and thumb_bg.exists():
            try:
                import subprocess as _sp
                _bright = _thumb_work / "_bg_bright.jpg"
                # 既に明るさ補正済みキャッシュがあれば再利用
                if not _bright.exists() or _bright.stat().st_mtime < thumb_bg.stat().st_mtime:
                    _sp.run([
                        "ffmpeg", "-y", "-v", "error",
                        "-i", str(thumb_bg),
                        "-vf", "eq=brightness=0.08:saturation=1.18:contrast=1.05",
                        "-q:v", "2", str(_bright)
                    ], check=False, timeout=30)
                if _bright.exists() and _bright.stat().st_size > 0:
                    thumb_bg = _bright
            except Exception as _e:
                log(f"サムネ背景の明るさ補正をスキップ: {_e}")
        _render_thumbnail(dthumb, ep_title, thumb, bg_image=thumb_bg, style=style)
        log(f"サムネイル: {thumb} ({thumb.stat().st_size} bytes) bg={'yes' if thumb_bg else 'no'} query={thumb_query!r} style=tech")
    except Exception as e:
        log(f"サムネイル生成に失敗、サムネ無しで続行: {e}")
        thumb = None

    if args.thumbnail_only:
        if not thumb or not thumb.exists():
            log("サムネイルを生成できなかったため既存動画は変更しない")
            return 2
        video_id = str(published["video_id"])
        try:
            yt.set_thumbnail(video_id, thumb, token_file=token, client_secret_file=secret)
        except Exception as e:
            log(f"既存動画のサムネイル設定に失敗: {e}")
            return 2
        log(f"既存動画のサムネイルを更新した: {published.get('url')}")
        print(published.get("url", f"https://www.youtube.com/watch?v={video_id}"))
        return 0

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
