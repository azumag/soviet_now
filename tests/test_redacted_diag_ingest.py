"""docich#33: ingestion splits every report into a safe event record and a
short-TTL restricted spool entry holding the raw comment.

Covers "reportからevidence snapshotとeventへ追跡できますが、生コメントを恒久
保存しません" (the ingestion half - see test_redacted_diag_spool_gc.py for the
TTL deletion half) and the redacted_context_hash part of "event_id/category/
time/redacted_context_hash".
"""

import json
import stat
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from lib import redacted_diag_ingest as ingest  # noqa: E402
from lib import redacted_diag_redact as redact  # noqa: E402


class IngestReportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.events_dir = self.root / "events"
        self.spool_dir = self.root / "spool"

    def tearDown(self):
        self.tmp.cleanup()

    def test_event_record_has_required_fields_only(self):
        event = ingest.ingest_report(
            category="stream_bug_report",
            user="viewer_ja",
            comment="配信の音が急に無音になった",
            source="twitch",
            events_dir=self.events_dir,
            spool_dir=self.spool_dir,
            now=1000,
        )
        self.assertEqual(
            set(event.keys()), {"event_id", "category", "time", "redacted_context_hash", "source"}
        )
        self.assertEqual(event["category"], "stream_bug_report")
        self.assertEqual(event["time"], 1000)
        self.assertEqual(event["source"], "twitch")

    def test_event_return_value_never_contains_raw_comment_or_user(self):
        sentinel = "SENTINEL_TOKEN_DO_NOT_LEAK_8f2c91"
        event = ingest.ingest_report(
            category="stream_bug_report",
            user="viewer_exfil",
            comment=f"leak {sentinel} please",
            source="twitch",
            events_dir=self.events_dir,
            spool_dir=self.spool_dir,
        )
        blob = json.dumps(event, ensure_ascii=False)
        self.assertNotIn(sentinel, blob)
        self.assertNotIn("viewer_exfil", blob)

    def test_event_file_on_disk_never_contains_raw_comment_or_user(self):
        sentinel = "SENTINEL_TOKEN_DO_NOT_LEAK_8f2c91"
        event = ingest.ingest_report(
            category="stream_bug_report",
            user="viewer_exfil",
            comment=f"leak {sentinel} please",
            source="twitch",
            events_dir=self.events_dir,
            spool_dir=self.spool_dir,
        )
        event_path = self.events_dir / f"{event['event_id']}.json"
        self.assertTrue(event_path.exists())
        content = event_path.read_text(encoding="utf-8")
        self.assertNotIn(sentinel, content)
        self.assertNotIn("viewer_exfil", content)

    def test_spool_entry_holds_raw_comment_with_expiry_and_restrictive_perms(self):
        sentinel = "SENTINEL_TOKEN_DO_NOT_LEAK_8f2c91"
        event = ingest.ingest_report(
            category="stream_bug_report",
            user="viewer_ja",
            comment=f"leak {sentinel} please",
            source="twitch",
            events_dir=self.events_dir,
            spool_dir=self.spool_dir,
            ttl_sec=3600,
            now=1000,
        )
        spool_path = self.spool_dir / f"{event['event_id']}.json"
        self.assertTrue(spool_path.exists())
        data = json.loads(spool_path.read_text(encoding="utf-8"))
        self.assertEqual(data["comment"], f"leak {sentinel} please")
        self.assertEqual(data["user"], "viewer_ja")
        self.assertEqual(data["expires_at"], 1000 + 3600)
        mode = stat.S_IMODE(spool_path.stat().st_mode)
        self.assertEqual(mode, 0o600)

    def test_redacted_context_hash_matches_redact_module(self):
        event = ingest.ingest_report(
            category="stream_bug_report",
            user="v",
            comment="hello world",
            source="twitch",
            events_dir=self.events_dir,
            spool_dir=self.spool_dir,
        )
        expected = redact.redacted_context_hash("stream_bug_report", "hello world")
        self.assertEqual(event["redacted_context_hash"], expected)

    def test_same_event_id_used_for_event_and_spool_entry(self):
        event = ingest.ingest_report(
            category="stream_bug_report",
            user="v",
            comment="hello",
            source="twitch",
            events_dir=self.events_dir,
            spool_dir=self.spool_dir,
        )
        self.assertTrue((self.events_dir / f"{event['event_id']}.json").exists())
        self.assertTrue((self.spool_dir / f"{event['event_id']}.json").exists())


if __name__ == "__main__":
    unittest.main()
