"""soviet_now#145: strategy run journal の契約テスト。

accumulate→evaluate を shadow 記録する append-only journal の
- schema 9 フィールド常備
- 同一 run_id の open 二重開始拒否
- 中断（finish 未記録）run の open 判定 → 失敗確定/再開の根拠
- atomic append（部分行が残らない）
- shadow_compare の差分検出
を pin する。production の acc/rolling/run 経路には一切触れない。
"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import strategy_run_journal as J


class JournalTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "journal.jsonl")
        self.journal = J.StrategyRunJournal(self.path)

    def tearDown(self):
        self.tmp.cleanup()

    def _entries(self):
        with open(self.path) as f:
            return [json.loads(line) for line in f if line.strip()]

    def test_begin_writes_all_schema_fields(self):
        self.journal.begin("run-A", reason="reset", owner="strategy",
                           input_hash="abc123", evidence={"k": 1})
        (entry,) = self._entries()
        for field in J._REQUIRED_FIELDS:
            self.assertIn(field, entry)
        self.assertEqual(entry["schema"], J.SCHEMA_VERSION)
        self.assertEqual(entry["run_id"], "run-A")
        self.assertEqual(entry["to_state"], "started")
        self.assertEqual(entry["input_hash"], "abc123")
        self.assertEqual(entry["evidence"], {"k": 1})

    def test_double_begin_same_run_id_rejected(self):
        self.journal.begin("run-A")
        with self.assertRaises(ValueError):
            self.journal.begin("run-A")
        # 拒否されても journal には1行だけ残る
        self.assertEqual(len(self._entries()), 1)

    def test_begin_allowed_after_finish(self):
        self.journal.begin("run-A")
        self.journal.finish("run-A", "rejected")
        self.journal.begin("run-A")  # finished なので再 open 可能
        self.assertEqual(len(self._entries()), 3)

    def test_different_run_ids_can_be_open(self):
        self.journal.begin("run-A")
        self.journal.begin("run-B")
        self.assertEqual(self.journal._open_run_ids(), {"run-A", "run-B"})

    def test_interrupted_run_stays_open_then_can_be_failed(self):
        # evaluate 中断 = finish が書かれない run が open のまま残る
        self.journal.begin("run-A", reason="reset")
        self.journal.append("run-A", "accumulate", "accumulated", "score=100")
        self.assertEqual(self.journal._open_run_ids(), {"run-A"})
        # 再開確認後、失敗確定できる
        self.journal.finish("run-A", "failed", reason="interrupted")
        self.assertEqual(self.journal._open_run_ids(), set())
        states = [e["to_state"] for e in self._entries()]
        self.assertEqual(states, ["started", "accumulated", "finished"])

    def test_append_preserves_existing_lines_atomically(self):
        self.journal.begin("run-A")
        self.journal.append("run-A", "s1", "s2", "r")
        self.journal.append("run-A", "s2", "s3", "r")
        entries = self._entries()
        self.assertEqual(len(entries), 3)
        # tmp ファイルが残っていない（rename で置換済み）
        self.assertFalse(os.path.exists(self.path + ".tmp"))

    def test_torn_tail_line_is_ignored(self):
        # 部分書き込み（末尾の壊れた行）は読み取りで無視し、open 判定を壊さない
        self.journal.begin("run-A")
        with open(self.path, "a") as f:
            f.write('{"run_id": "run-B", "to_state": "started"')  # no close/newline
        self.assertEqual(self.journal._open_run_ids(), {"run-A"})

    def test_cli_begin_append_finish(self):
        rc = J._cli(["--journal", self.path, "begin", "cli-run",
                     "--reason", "reset", "--input-hash", "h1"])
        self.assertEqual(rc, 0)
        rc = J._cli(["--journal", self.path, "begin", "cli-run"])
        self.assertEqual(rc, 1)  # double-start は rc=1
        rc = J._cli(["--journal", self.path, "append", "cli-run",
                     "--from-state", "accumulate", "--to-state", "accumulated",
                     "--evidence-json", '{"score": 100}'])
        self.assertEqual(rc, 0)
        rc = J._cli(["--journal", self.path, "finish", "cli-run",
                     "--to-state", "applied", "--output-hash", "h2"])
        self.assertEqual(rc, 0)
        entries = self._entries()
        self.assertEqual(len(entries), 3)
        self.assertEqual(entries[1]["evidence"], {"score": 100})

    def test_shadow_compare_detects_diff_and_tolerance(self):
        res = J.shadow_compare({"a": 1, "b": "x"}, {"a": 1, "b": "y"})
        self.assertFalse(res["match"])
        self.assertEqual(res["diff"], {"b": ["x", "y"]})
        res = J.shadow_compare({"score": 100.4}, {"score": 100.5}, tolerance=0.2)
        self.assertTrue(res["match"])


if __name__ == "__main__":
    unittest.main()
