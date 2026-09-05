"""docich#33: TTL purge of the restricted raw-comment spool.

Covers "生コメントを恒久保存しません" (the deletion half; see
test_redacted_diag_ingest.py for the ingestion/expiry-stamping half).
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from lib import redacted_diag_spool_gc as gc  # noqa: E402


class SpoolGcTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.spool_dir = Path(self.tmp.name) / "spool"
        self.spool_dir.mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def _write(self, name, data):
        (self.spool_dir / name).write_text(json.dumps(data), encoding="utf-8")

    def test_expired_entry_is_deleted(self):
        self._write("e1.json", {"event_id": "e1", "comment": "raw", "expires_at": 1000})
        result = gc.purge_expired(self.spool_dir, now=2000)
        self.assertEqual(result["purged"], 1)
        self.assertFalse((self.spool_dir / "e1.json").exists())

    def test_fresh_entry_is_kept(self):
        self._write("e1.json", {"event_id": "e1", "comment": "raw", "expires_at": 5000})
        result = gc.purge_expired(self.spool_dir, now=2000)
        self.assertEqual(result["kept"], 1)
        self.assertTrue((self.spool_dir / "e1.json").exists())

    def test_missing_expires_at_is_purged_fail_safe(self):
        self._write("e1.json", {"event_id": "e1", "comment": "raw"})
        result = gc.purge_expired(self.spool_dir, now=2000)
        self.assertEqual(result["purged"], 1)
        self.assertEqual(result["corrupt_purged"], 1)
        self.assertFalse((self.spool_dir / "e1.json").exists())

    def test_corrupt_json_is_purged_fail_safe(self):
        (self.spool_dir / "e1.json").write_text("{not valid json", encoding="utf-8")
        result = gc.purge_expired(self.spool_dir, now=2000)
        self.assertEqual(result["purged"], 1)
        self.assertFalse((self.spool_dir / "e1.json").exists())

    def test_exactly_at_expiry_boundary_is_kept(self):
        # now == expires_at should not yet be expired (only now > expires_at is)
        self._write("e1.json", {"event_id": "e1", "comment": "raw", "expires_at": 2000})
        result = gc.purge_expired(self.spool_dir, now=2000)
        self.assertEqual(result["kept"], 1)

    def test_mixed_entries_purge_summary_never_includes_raw_content(self):
        sentinel = "SENTINEL_TOKEN_DO_NOT_LEAK_8f2c91"
        self._write("expired.json", {"event_id": "e1", "user": "viewer_exfil", "comment": sentinel, "expires_at": 1000})
        self._write("fresh.json", {"event_id": "e2", "user": "v2", "comment": "other", "expires_at": 9999})
        result = gc.purge_expired(self.spool_dir, now=2000)
        blob = json.dumps(result)
        self.assertNotIn(sentinel, blob)
        self.assertNotIn("viewer_exfil", blob)
        self.assertEqual(result["purged"], 1)
        self.assertEqual(result["kept"], 1)

    def test_dry_run_does_not_delete(self):
        self._write("e1.json", {"event_id": "e1", "comment": "raw", "expires_at": 1000})
        result = gc.purge_expired(self.spool_dir, now=2000, dry_run=True)
        self.assertEqual(result["purged"], 1)
        self.assertTrue((self.spool_dir / "e1.json").exists())

    def test_empty_spool_dir_is_a_noop(self):
        result = gc.purge_expired(self.spool_dir, now=2000)
        self.assertEqual(result, {"purged": 0, "kept": 0, "corrupt_purged": 0})

    def test_nonexistent_spool_dir_is_a_noop(self):
        missing = self.spool_dir / "does_not_exist"
        result = gc.purge_expired(missing, now=2000)
        self.assertEqual(result["purged"], 0)


if __name__ == "__main__":
    unittest.main()
