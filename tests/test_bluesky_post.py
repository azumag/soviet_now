"""tools/bluesky_post.py: 本文の組み立て・facet のバイト位置・冪等性・認証欠如時の扱い。

ネットワークへは出さない (_request を差し替える)。
"""
import datetime
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import bluesky_post as bp  # noqa: E402


class FakeXrpc:
    """_request の差し替え。呼ばれた URL と body を覚える。"""

    def __init__(self):
        self.calls = []

    def __call__(self, url, data, headers, method="POST"):
        body = json.loads(data.decode("utf-8")) if data and headers.get(
            "Content-Type") == "application/json" else data
        self.calls.append({"url": url, "body": body, "headers": headers})
        if url.endswith("com.atproto.server.createSession"):
            return {"accessJwt": "jwt", "refreshJwt": "r", "did": "did:plc:test",
                    "handle": "soren.bsky.social"}
        if url.endswith("com.atproto.repo.uploadBlob"):
            return {"blob": {"$type": "blob", "ref": {"$link": "bafy"},
                             "mimeType": "image/png", "size": len(data)}}
        if url.endswith("com.atproto.repo.createRecord"):
            return {"uri": "at://did:plc:test/app.bsky.feed.post/3kabc", "cid": "cid1"}
        if url.endswith("com.atproto.repo.deleteRecord"):
            return {}
        raise AssertionError(f"unexpected call: {url}")

    def record(self):
        for c in self.calls:
            if c["url"].endswith("createRecord"):
                return c["body"]["record"]
        return None


class ComposeTest(unittest.TestCase):
    def test_keeps_headline_and_url_within_limit(self):
        text = bp.compose_text("【8/25】" + "見" * 30, "要" * 500,
                               "https://www.youtube.com/watch?v=abcdefghijk")
        self.assertLessEqual(len(text), bp.TEXT_LIMIT)
        self.assertTrue(text.startswith("【8/25】"))
        self.assertTrue(text.endswith("https://www.youtube.com/watch?v=abcdefghijk"))
        self.assertIn("…", text)

    def test_short_summary_is_not_truncated(self):
        text = bp.compose_text("見出し", "短い要約", "https://example.com/x")
        self.assertEqual(text, "見出し\n短い要約\nhttps://example.com/x")

    def test_tags_are_appended_last(self):
        text = bp.compose_text("見出し", "要約", "https://example.com/x", ["ニュース", "#時事"])
        self.assertTrue(text.endswith("#ニュース #時事"))
        self.assertLessEqual(len(text), bp.TEXT_LIMIT)

    def test_overlong_headline_still_fits(self):
        text = bp.compose_text("長" * 400, "", "https://example.com/x")
        self.assertLessEqual(len(text), bp.TEXT_LIMIT)
        self.assertIn("https://example.com/x", text)

    def test_no_summary(self):
        self.assertEqual(bp.compose_text("見出し", "", "https://example.com/x"),
                         "見出し\nhttps://example.com/x")


class FacetTest(unittest.TestCase):
    def test_link_offsets_are_utf8_bytes(self):
        text = "日本語の見出し\nhttps://example.com/watch?v=1"
        facets = bp.build_facets(text)
        self.assertEqual(len(facets), 1)
        idx = facets[0]["index"]
        raw = text.encode("utf-8")
        self.assertEqual(raw[idx["byteStart"]:idx["byteEnd"]].decode("utf-8"),
                         "https://example.com/watch?v=1")
        self.assertEqual(facets[0]["features"][0]["$type"],
                         "app.bsky.richtext.facet#link")

    def test_tag_facets(self):
        text = "本文です https://example.com/x #ニュース #時事"
        facets = bp.build_facets(text)
        kinds = [f["features"][0]["$type"] for f in facets]
        self.assertEqual(kinds.count("app.bsky.richtext.facet#tag"), 2)
        raw = text.encode("utf-8")
        tags = [raw[f["index"]["byteStart"]:f["index"]["byteEnd"]].decode("utf-8")
                for f in facets if f["features"][0]["$type"].endswith("#tag")]
        self.assertEqual(tags, ["#ニュース", "#時事"])
        self.assertEqual([f["features"][0]["tag"] for f in facets
                          if f["features"][0]["$type"].endswith("#tag")],
                         ["ニュース", "時事"])

    def test_no_url_no_facets(self):
        self.assertEqual(bp.build_facets("ただの本文"), [])


class RecordTest(unittest.TestCase):
    def test_external_embed_and_lang(self):
        rec = bp.build_record("本文 https://example.com/x", "https://example.com/x",
                              "タイトル", "説明", {"$type": "blob"})
        self.assertEqual(rec["$type"], "app.bsky.feed.post")
        self.assertEqual(rec["langs"], ["ja"])
        self.assertTrue(rec["createdAt"].endswith("Z"))
        datetime.datetime.strptime(rec["createdAt"], "%Y-%m-%dT%H:%M:%SZ")
        ext = rec["embed"]["external"]
        self.assertEqual(rec["embed"]["$type"], "app.bsky.embed.external")
        self.assertEqual(ext["uri"], "https://example.com/x")
        self.assertEqual(ext["title"], "タイトル")
        self.assertEqual(ext["thumb"], {"$type": "blob"})

    def test_no_link_no_embed(self):
        rec = bp.build_record("本文だけ", None, "", "", None)
        self.assertNotIn("embed", rec)


class PodcastPayloadTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.out = Path(self.tmp.name)
        (self.out / "2026-08-25.publish.json").write_text(json.dumps({
            "date": "2026-08-25", "video_id": "vid1",
            "url": "https://www.youtube.com/watch?v=vid1",
            "title": "【8/25】タイトル｜同志のための時事ニュース"}), encoding="utf-8")
        (self.out / "2026-08-25.meta.json").write_text(json.dumps({
            "title": "揺らぐ世界で問われる連帯と暮らし",
            "summary": "異常気象や感染症、市民社会の抑圧を軸に世界の現在地を見渡します。"}),
            encoding="utf-8")
        (self.out / "2026-08-25.thumbnail.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 100)

    def tearDown(self):
        self.tmp.cleanup()

    def test_payload_from_publish_and_meta(self):
        p = bp.podcast_payload(self.out, "2026-08-25", [])
        self.assertIn("【8/25】揺らぐ世界で問われる連帯と暮らし", p["text"])
        self.assertIn("https://www.youtube.com/watch?v=vid1", p["text"])
        self.assertEqual(p["link"], "https://www.youtube.com/watch?v=vid1")
        self.assertEqual(p["thumb"], self.out / "2026-08-25.thumbnail.png")
        self.assertIn("同志のための時事ニュース", p["card_title"])

    def test_missing_publish_json_raises(self):
        with self.assertRaises(FileNotFoundError):
            bp.podcast_payload(self.out, "2026-08-24", [])

    def test_missing_thumbnail_is_none(self):
        (self.out / "2026-08-25.thumbnail.png").unlink()
        self.assertIsNone(bp.podcast_payload(self.out, "2026-08-25", [])["thumb"])


class MainTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.out = Path(self.tmp.name)
        (self.out / "2026-08-25.publish.json").write_text(json.dumps({
            "url": "https://www.youtube.com/watch?v=vid1", "title": "t"}), encoding="utf-8")
        (self.out / "2026-08-25.meta.json").write_text(json.dumps({
            "title": "見出し", "summary": "要約です。"}), encoding="utf-8")
        (self.out / "2026-08-25.thumbnail.png").write_bytes(b"\x89PNG" + b"0" * 100)
        self.fake = FakeXrpc()
        self.orig_request = bp._request
        bp._request = self.fake
        self.env_backup = {k: os.environ.get(k) for k in
                           ("BLUESKY_HANDLE", "BLUESKY_APP_PASSWORD",
                            "BLUESKY_CREDENTIALS_FILE", "PODCAST_BLUESKY_TAGS")}
        os.environ["BLUESKY_HANDLE"] = "soren.bsky.social"
        os.environ["BLUESKY_APP_PASSWORD"] = "test-app-password"
        os.environ.pop("PODCAST_BLUESKY_TAGS", None)
        self.argv = sys.argv

    def tearDown(self):
        bp._request = self.orig_request
        sys.argv = self.argv
        for k, v in self.env_backup.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self.tmp.cleanup()

    def run_main(self, *args):
        sys.argv = ["bluesky_post.py", *args]
        return bp.main()

    def test_posts_and_records_state(self):
        rc = self.run_main("--podcast", "--date", "20260825", "--out-dir", str(self.out))
        self.assertEqual(rc, 0)
        urls = [c["url"].rsplit("/", 1)[-1] for c in self.fake.calls]
        self.assertEqual(urls, ["com.atproto.server.createSession",
                                "com.atproto.repo.uploadBlob",
                                "com.atproto.repo.createRecord"])
        rec = self.fake.record()
        self.assertIn("見出し", rec["text"])
        self.assertEqual(rec["embed"]["external"]["thumb"]["mimeType"], "image/png")
        self.assertTrue(any(f["features"][0]["$type"].endswith("#link")
                            for f in rec["facets"]))
        state = json.loads((self.out / "2026-08-25.bluesky.json").read_text(encoding="utf-8"))
        self.assertEqual(state["post_url"],
                         "https://bsky.app/profile/soren.bsky.social/post/3kabc")

    def test_second_run_is_idempotent(self):
        self.run_main("--podcast", "--date", "20260825", "--out-dir", str(self.out))
        n = len(self.fake.calls)
        rc = self.run_main("--podcast", "--date", "20260825", "--out-dir", str(self.out))
        self.assertEqual(rc, 0)
        self.assertEqual(len(self.fake.calls), n, "投稿済みなら送信しない")
        rc = self.run_main("--podcast", "--date", "20260825", "--out-dir", str(self.out),
                           "--force")
        self.assertEqual(rc, 0)
        self.assertGreater(len(self.fake.calls), n, "--force なら再投稿する")

    def test_dry_run_sends_nothing(self):
        rc = self.run_main("--podcast", "--date", "20260825", "--out-dir", str(self.out),
                           "--dry-run")
        self.assertEqual(rc, 0)
        self.assertEqual(self.fake.calls, [])
        self.assertFalse((self.out / "2026-08-25.bluesky.json").exists())

    def test_missing_credentials_returns_4(self):
        os.environ.pop("BLUESKY_HANDLE")
        os.environ.pop("BLUESKY_APP_PASSWORD")
        os.environ["BLUESKY_CREDENTIALS_FILE"] = str(self.out / "nonexistent.json")
        orig_home = bp.Path.home
        bp.Path.home = staticmethod(lambda: Path(self.tmp.name) / "nohome")
        try:
            rc = self.run_main("--podcast", "--date", "20260825", "--out-dir", str(self.out))
        finally:
            bp.Path.home = orig_home
        self.assertEqual(rc, 4)
        self.assertEqual(self.fake.calls, [])

    def test_missing_publish_json_returns_2(self):
        rc = self.run_main("--podcast", "--date", "20260824", "--out-dir", str(self.out))
        self.assertEqual(rc, 2)
        self.assertEqual(self.fake.calls, [])

    def test_credentials_file_is_used(self):
        os.environ.pop("BLUESKY_HANDLE")
        os.environ.pop("BLUESKY_APP_PASSWORD")
        cred = self.out / "cred.json"
        cred.write_text(json.dumps({"handle": "x.bsky.social",
                                    "app_password": "aaaa-bbbb"}), encoding="utf-8")
        os.environ["BLUESKY_CREDENTIALS_FILE"] = str(cred)
        rc = self.run_main("--podcast", "--date", "20260825", "--out-dir", str(self.out))
        self.assertEqual(rc, 0)
        self.assertEqual(self.fake.calls[0]["body"]["identifier"], "x.bsky.social")

    def test_delete_recorded_post(self):
        self.run_main("--podcast", "--date", "20260825", "--out-dir", str(self.out))
        state = self.out / "2026-08-25.bluesky.json"
        self.assertTrue(state.exists())
        rc = self.run_main("--podcast", "--date", "20260825", "--out-dir", str(self.out),
                           "--delete")
        self.assertEqual(rc, 0)
        deleted = [c for c in self.fake.calls if c["url"].endswith("deleteRecord")]
        self.assertEqual(len(deleted), 1)
        self.assertEqual(deleted[0]["body"]["rkey"], "3kabc")
        self.assertEqual(deleted[0]["body"]["collection"], "app.bsky.feed.post")
        self.assertFalse(state.exists(), "記録も消える")

    def test_delete_by_url(self):
        rc = self.run_main("--delete",
                           "https://bsky.app/profile/soren.bsky.social/post/3kxyz")
        self.assertEqual(rc, 0)
        deleted = [c for c in self.fake.calls if c["url"].endswith("deleteRecord")]
        self.assertEqual(deleted[0]["body"]["rkey"], "3kxyz")

    def test_delete_without_record_returns_2(self):
        rc = self.run_main("--podcast", "--date", "20260824", "--out-dir", str(self.out),
                           "--delete")
        self.assertEqual(rc, 2)
        self.assertEqual(self.fake.calls, [])

    def test_free_text_mode(self):
        rc = self.run_main("--text", "テスト投稿 https://example.com/a")
        self.assertEqual(rc, 0)
        rec = self.fake.record()
        self.assertEqual(rec["text"], "テスト投稿 https://example.com/a")
        self.assertNotIn("embed", rec)


if __name__ == "__main__":
    unittest.main(verbosity=2)
