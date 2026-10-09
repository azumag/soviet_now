"""Exercise the real archive reader with valid row/basename identities."""
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lib import soren_stage_ledger as m


class StageReaderTests(unittest.TestCase):
    def test_reader_rejects_unreadable_symlink_fifo_and_directory(self):
        with tempfile.TemporaryDirectory(dir=Path(tempfile.gettempdir()).resolve()) as directory:
            root = Path(directory)
            (root / "source").write_bytes(b"private text")
            (root / "link.jsonl").symlink_to(root / "source")
            os.mkfifo(root / "pipe.jsonl")
            (root / "directory.jsonl").mkdir()
            for name, reason in (("missing.jsonl", "archive_unreadable"),
                                 ("link.jsonl", "symlink_archive"),
                                 ("pipe.jsonl", "not_bounded_regular_file"),
                                 ("directory.jsonl", "archive_unreadable")):
                with self.subTest(name=name):
                    row = {"idx": 0, "arm": "A", "game_num": "1", "archive": name,
                           "hash": "a"*12, "played_hash": "a"*12,
                           "history_hash": "a"*12, "turns": 1.0, "tainted": False}
                    result = m.capture_stage_evidence(root / name, row)
                    self.assertEqual(result["stage_evidence"], {
                        "schema_version": 1, "status": "unavailable", "reason": reason})
                    self.assertNotIn(directory, json.dumps(result))
                    self.assertNotIn("private text", json.dumps(result))


if __name__ == "__main__":
    unittest.main()
