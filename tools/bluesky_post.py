#!/usr/bin/env python3
"""
tools/bluesky_post.py - Bluesky (AT Protocol) へ投稿する (docich#10)

ポッドキャスト動画を YouTube へ上げた後の告知に使う。外部ライブラリは使わず
urllib だけで XRPC を叩くので、Mac の system python でも doci の venv でも動く。

認証 (上から順に探す):
  1. 環境変数 BLUESKY_HANDLE / BLUESKY_APP_PASSWORD
  2. BLUESKY_CREDENTIALS_FILE が指す JSON
  3. ~/.config/soren/bluesky.json
     {"handle": "example.bsky.social", "app_password": "xxxx-xxxx-xxxx-xxxx"}
  ※ 必ず「アプリパスワード」(https://bsky.app/settings/app-passwords) を使う。
     本パスワードは使わない。鍵はリポジトリに置かない (AGENTS.md)。

使い方:
  ./tools/bluesky_post.py --podcast --date 20260825   # 公開済みポッドキャストを告知
  ./tools/bluesky_post.py --text "本文" --link https://... --thumb path.png
  ./tools/bluesky_post.py --podcast --dry-run         # 送信せず組み立て結果だけ表示
  ./tools/bluesky_post.py --delete https://bsky.app/profile/<handle>/post/<rkey>
  ./tools/bluesky_post.py --podcast --date 20260825 --delete   # 記録した投稿を消す

終了コード: 0=投稿した/既に投稿済み, 2=入力エラー, 3=送信失敗, 4=認証情報が無い
"""

from __future__ import annotations

import argparse
import datetime
import json
import mimetypes
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent.parent
OUTPUT_ROOT_DEFAULT = SCRIPT_DIR / "output" / "podcast"

DEFAULT_SERVICE = os.environ.get("BLUESKY_SERVICE", "https://bsky.social")
# Bluesky の本文上限 (grapheme)。日本語は概ね 1 文字 = 1 grapheme なので文字数で見る。
TEXT_LIMIT = 300
# blob の上限は約 976KB。サムネイル (実測 217KB) は収まるが念のため見る。
BLOB_LIMIT = 976 * 1024
HTTP_TIMEOUT = int(os.environ.get("BLUESKY_HTTP_TIMEOUT", "60"))

URL_RE = re.compile(r"https?://[^\s<>\)\]　]+")
TAG_RE = re.compile(r"(?:^|\s)(#[^\s#]+)")


def log(msg: str) -> None:
    print(f"[{datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] [bluesky] {msg}",
          file=sys.stderr)


def parse_date(arg: str) -> datetime.date:
    for fmt in ("%Y%m%d", "%Y-%m-%d"):
        try:
            return datetime.datetime.strptime(arg, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"invalid date: {arg}")


# --- 認証 -----------------------------------------------------------------

def credentials_paths() -> list[Path]:
    paths = []
    if os.environ.get("BLUESKY_CREDENTIALS_FILE"):
        paths.append(Path(os.environ["BLUESKY_CREDENTIALS_FILE"]).expanduser())
    paths.append(Path.home() / ".config" / "soren" / "bluesky.json")
    return paths


def load_credentials() -> dict | None:
    """handle / app_password / service を返す。見つからなければ None。"""
    handle = os.environ.get("BLUESKY_HANDLE")
    password = os.environ.get("BLUESKY_APP_PASSWORD")
    if handle and password:
        return {"handle": handle, "app_password": password,
                "service": os.environ.get("BLUESKY_SERVICE", DEFAULT_SERVICE)}
    for p in credentials_paths():
        if not p.is_file():
            continue
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except Exception as e:
            log(f"認証情報の読み込みに失敗 ({p}): {e}")
            continue
        h = data.get("handle") or data.get("identifier")
        pw = data.get("app_password") or data.get("password")
        if h and pw:
            return {"handle": h, "app_password": pw,
                    "service": data.get("service") or DEFAULT_SERVICE}
        log(f"認証情報に handle/app_password が無い ({p})")
    return None


# --- XRPC -----------------------------------------------------------------

def _request(url: str, data: bytes | None, headers: dict, method: str = "POST") -> dict:
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
            body = resp.read()
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:500]
        raise RuntimeError(f"{method} {url} -> HTTP {e.code}: {detail}") from None
    except urllib.error.URLError as e:
        raise RuntimeError(f"{method} {url} -> {e.reason}") from None
    return json.loads(body.decode("utf-8")) if body else {}


def create_session(service: str, handle: str, app_password: str) -> dict:
    return _request(
        f"{service}/xrpc/com.atproto.server.createSession",
        json.dumps({"identifier": handle, "password": app_password}).encode("utf-8"),
        {"Content-Type": "application/json"},
    )


def upload_blob(service: str, jwt: str, path: Path) -> dict:
    raw = path.read_bytes()
    if len(raw) > BLOB_LIMIT:
        raise RuntimeError(f"画像が大きすぎる ({len(raw)} bytes > {BLOB_LIMIT})")
    ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    res = _request(f"{service}/xrpc/com.atproto.repo.uploadBlob", raw,
                   {"Content-Type": ctype, "Authorization": f"Bearer {jwt}"})
    return res["blob"]


def create_record(service: str, jwt: str, did: str, record: dict) -> dict:
    return _request(
        f"{service}/xrpc/com.atproto.repo.createRecord",
        json.dumps({"repo": did, "collection": "app.bsky.feed.post",
                    "record": record}).encode("utf-8"),
        {"Content-Type": "application/json", "Authorization": f"Bearer {jwt}"},
    )


def delete_record(service: str, jwt: str, did: str, rkey: str) -> dict:
    return _request(
        f"{service}/xrpc/com.atproto.repo.deleteRecord",
        json.dumps({"repo": did, "collection": "app.bsky.feed.post",
                    "rkey": rkey}).encode("utf-8"),
        {"Content-Type": "application/json", "Authorization": f"Bearer {jwt}"},
    )


# --- 本文の組み立て -------------------------------------------------------

def build_facets(text: str) -> list[dict]:
    """URL と #タグをクリックできるようにする。オフセットは UTF-8 バイト。"""
    facets = []
    raw = text.encode("utf-8")
    for m in URL_RE.finditer(text):
        uri = m.group(0).rstrip(".,、。")
        start = len(text[:m.start()].encode("utf-8"))
        facets.append({
            "index": {"byteStart": start, "byteEnd": start + len(uri.encode("utf-8"))},
            "features": [{"$type": "app.bsky.richtext.facet#link", "uri": uri}],
        })
    for m in TAG_RE.finditer(text):
        tag = m.group(1)
        start = len(text[:m.start(1)].encode("utf-8"))
        facets.append({
            "index": {"byteStart": start, "byteEnd": start + len(tag.encode("utf-8"))},
            "features": [{"$type": "app.bsky.richtext.facet#tag", "tag": tag[1:]}],
        })
    assert all(f["index"]["byteEnd"] <= len(raw) for f in facets)
    return sorted(facets, key=lambda f: f["index"]["byteStart"])


def compose_text(headline: str, summary: str, url: str, tags: list[str] | None = None,
                 limit: int = TEXT_LIMIT) -> str:
    """見出しと URL は必ず残し、あふれる分は要約から削る。"""
    tags = tags or []
    tag_line = " ".join(f"#{t.lstrip('#')}" for t in tags)
    fixed = [headline]
    tail = [x for x in (url, tag_line) if x]
    overhead = len(headline) + sum(len(t) + 1 for t in tail)
    budget = limit - overhead - (1 if summary else 0)
    if summary and budget > 1:
        if len(summary) > budget:
            summary = summary[:budget - 1].rstrip() + "…"
        fixed.append(summary)
    text = "\n".join(fixed + tail)
    if len(text) > limit:  # 見出しだけで溢れる異常系
        keep = limit - sum(len(t) + 1 for t in tail) - 1
        text = "\n".join([headline[:max(keep, 0)].rstrip() + "…"] + tail)
    return text


def podcast_payload(out_dir: Path, iso: str, tags: list[str]) -> dict:
    """公開済みポッドキャストから投稿内容を組み立てる。"""
    publish_file = out_dir / f"{iso}.publish.json"
    if not publish_file.is_file():
        raise FileNotFoundError(
            f"公開情報が無い: {publish_file} (先に podcast_publish.py を実行する)")
    publish = json.loads(publish_file.read_text(encoding="utf-8"))
    url = publish.get("url")
    if not url:
        raise ValueError(f"公開情報に url が無い: {publish_file}")

    meta_file = out_dir / f"{iso}.meta.json"
    meta = json.loads(meta_file.read_text(encoding="utf-8")) if meta_file.is_file() else {}
    d = datetime.date.fromisoformat(iso)
    show = os.environ.get("PODCAST_TITLE", "同志のための時事ニュース")
    ep_title = meta.get("title") or publish.get("title") or f"{iso} 時事まとめ"
    headline = f"【{d.month}/{d.day}】{ep_title}"
    summary = (meta.get("summary") or "").strip().replace("\n", " ")

    thumb = out_dir / f"{iso}.thumbnail.png"
    return {
        "text": compose_text(headline, summary, url, tags),
        "link": url,
        "thumb": thumb if thumb.is_file() else None,
        "card_title": f"{headline}｜{show}"[:300],
        "card_description": summary[:300],
    }


def build_record(text: str, link: str | None, card_title: str, card_description: str,
                 thumb_blob: dict | None, lang: str = "ja") -> dict:
    record = {
        "$type": "app.bsky.feed.post",
        "text": text,
        "createdAt": datetime.datetime.now(datetime.timezone.utc)
                             .isoformat(timespec="seconds").replace("+00:00", "Z"),
        "langs": [lang],
    }
    facets = build_facets(text)
    if facets:
        record["facets"] = facets
    if link:
        external = {"uri": link, "title": card_title or link,
                    "description": card_description or ""}
        if thumb_blob:
            external["thumb"] = thumb_blob
        record["embed"] = {"$type": "app.bsky.embed.external", "external": external}
    return record


def record_key(uri_or_url: str) -> str:
    """at://did/app.bsky.feed.post/<rkey> でも bsky.app の URL でも rkey を取る。"""
    rkey = uri_or_url.rstrip("/").rsplit("/", 1)[-1]
    if not rkey:
        raise ValueError(f"rkey を取り出せない: {uri_or_url}")
    return rkey


def post_url(handle: str, at_uri: str) -> str:
    """at://did/app.bsky.feed.post/<rkey> -> 人が開ける URL"""
    rkey = at_uri.rsplit("/", 1)[-1]
    return f"https://bsky.app/profile/{handle}/post/{rkey}"


# --- CLI ------------------------------------------------------------------

def delete_post(args, out_dir: Path) -> int:
    """投稿を消す (テスト投稿の後始末用)。"""
    state_file = None
    target = args.delete
    if target == "__state__":
        if not args.podcast:
            log("--delete に URI を渡すか、--podcast と併用してください")
            return 2
        date = parse_date(args.date) if args.date else \
            datetime.date.today() - datetime.timedelta(days=1)
        state_file = out_dir / f"{date.isoformat()}.bluesky.json"
        if not state_file.is_file():
            log(f"投稿の記録が無い: {state_file}")
            return 2
        target = json.loads(state_file.read_text(encoding="utf-8"))["uri"]

    if args.dry_run:
        log(f"dry-run: {target} を削除しない")
        return 0

    creds = load_credentials()
    if not creds:
        log("認証情報が無い")
        return 4
    service = creds["service"].rstrip("/")
    try:
        session = create_session(service, creds["handle"], creds["app_password"])
        delete_record(service, session["accessJwt"], session["did"], record_key(target))
    except (RuntimeError, ValueError) as e:
        log(f"削除に失敗: {e}")
        return 3
    log(f"削除した: {target}")
    if state_file is not None:
        state_file.unlink()
        log(f"記録も消した: {state_file}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--podcast", action="store_true", help="公開済みポッドキャストを告知する")
    ap.add_argument("--date", help="YYYYMMDD or YYYY-MM-DD (default: yesterday)")
    ap.add_argument("--out-dir", default=str(OUTPUT_ROOT_DEFAULT))
    ap.add_argument("--text", help="本文 (--podcast を使わない場合)")
    ap.add_argument("--link", help="カードにする URL")
    ap.add_argument("--thumb", help="カードのサムネイル画像")
    ap.add_argument("--tags", default=os.environ.get("PODCAST_BLUESKY_TAGS", ""),
                    help="末尾に付けるハッシュタグ (カンマ区切り)")
    ap.add_argument("--dry-run", action="store_true", help="送信せず内容だけ表示")
    ap.add_argument("--force", action="store_true", help="投稿済みでも再投稿する")
    ap.add_argument("--delete", nargs="?", const="__state__", metavar="URI",
                    help="投稿を削除する (at:// か bsky.app の URL。"
                         "--podcast と併用すると記録済みの投稿を消す)")
    args = ap.parse_args()

    tags = [t.strip() for t in args.tags.split(",") if t.strip()]
    out_dir = Path(args.out_dir)
    state_file = None

    if args.delete:
        return delete_post(args, out_dir)

    if args.podcast:
        date = parse_date(args.date) if args.date else \
            datetime.date.today() - datetime.timedelta(days=1)
        iso = date.isoformat()
        state_file = out_dir / f"{iso}.bluesky.json"
        if state_file.is_file() and not args.force:
            prev = json.loads(state_file.read_text(encoding="utf-8"))
            log(f"投稿済みなのでスキップ: {prev.get('post_url') or prev.get('uri')} "
                f"(--force で再投稿)")
            return 0
        try:
            payload = podcast_payload(out_dir, iso, tags)
        except (FileNotFoundError, ValueError) as e:
            log(str(e))
            return 2
    else:
        if not args.text:
            log("--text か --podcast のどちらかが要る")
            return 2
        text = args.text
        if tags:
            text = text + "\n" + " ".join(f"#{t.lstrip('#')}" for t in tags)
        payload = {"text": text, "link": args.link,
                   "thumb": Path(args.thumb) if args.thumb else None,
                   "card_title": args.link or "", "card_description": ""}

    text = payload["text"]
    log(f"本文 ({len(text)} 文字):")
    print(text)
    if payload.get("link"):
        log(f"カード: {payload['link']} (thumb={payload.get('thumb')})")
    if len(text) > TEXT_LIMIT:
        log(f"本文が上限 {TEXT_LIMIT} 文字を超えている ({len(text)})。中止。")
        return 2

    if args.dry_run:
        log("dry-run: 送信しない")
        print(json.dumps(build_record(text, payload.get("link"), payload.get("card_title", ""),
                                      payload.get("card_description", ""), None),
                         ensure_ascii=False, indent=2))
        return 0

    creds = load_credentials()
    if not creds:
        log("認証情報が無い。BLUESKY_HANDLE/BLUESKY_APP_PASSWORD か "
            f"{Path.home() / '.config' / 'soren' / 'bluesky.json'} を用意してください。")
        return 4

    service = creds["service"].rstrip("/")
    try:
        session = create_session(service, creds["handle"], creds["app_password"])
    except RuntimeError as e:
        log(f"ログインに失敗: {e}")
        return 3
    jwt, did = session["accessJwt"], session["did"]
    handle = session.get("handle") or creds["handle"]
    log(f"ログイン: {handle} ({did})")

    thumb_blob = None
    thumb = payload.get("thumb")
    if thumb and Path(thumb).is_file():
        try:
            thumb_blob = upload_blob(service, jwt, Path(thumb))
            log(f"サムネイルを上げた ({Path(thumb).stat().st_size} bytes)")
        except RuntimeError as e:
            log(f"サムネイルの送信に失敗、カードは画像なしで続行: {e}")

    record = build_record(text, payload.get("link"), payload.get("card_title", ""),
                          payload.get("card_description", ""), thumb_blob)
    try:
        res = create_record(service, jwt, did, record)
    except RuntimeError as e:
        log(f"投稿に失敗: {e}")
        return 3

    url = post_url(handle, res["uri"])
    log(f"投稿した: {url}")
    if state_file is not None:
        state_file.write_text(json.dumps({
            "uri": res["uri"], "cid": res.get("cid"), "post_url": url,
            "handle": handle, "text": text,
            "posted_at": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
        }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        log(f"投稿情報を記録: {state_file}")
    print(url)
    return 0


if __name__ == "__main__":
    sys.exit(main())
