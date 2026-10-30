#!/usr/bin/env python3
"""soviet_now#144: comment pipeline の characterization スキーマと corpus 検証。

本モジュールは「現行 `broadcast/comment.sh` が実際に読み書きするレコード」の
構造を固定する。正しい設計の規範ではなく、分割リファクタ前の実測契約。

カバーするレコード (現行コードで実在するもののみ):
- CommentEvent           : 媒体別 intake の1コメント (corpus の1行)
- Classification         : docich 分類器 stdout JSON 配列の1行
                           `{index,user,comment,category,is_english}`
- BugDispatchQueueEntry  : `_queue_stream_bug_reports_from_classification` が
                           `tmp/codex_bug_queue/` に書く payload
- RedactedDiagEvent      : `lib/redacted_diag_ingest.ingest_report` が
                           events_dir に書く安全メタデータ

`.comment_queue` consumer 側の DeliveryReceipt は別 seam で未 pin のため
follow-up。corpus は匿名化済み (実 username / 実コメントを含まない)。
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "comment_pipeline"

CATEGORIES = {
    "card_gacha",
    "chitchat",
    "bits",
    "subscription",
    "stream_goal",
    "sing_request",
    "raid",
    "other",
    "stream_bug_report",
    "advice",
}

SOURCES = {"twitch", "youtube", "kick"}


def validate_comment_event(row: dict) -> list[str]:
    errors = []
    if not isinstance(row.get("event_id"), str) or not row["event_id"]:
        errors.append("event_id: non-empty str required")
    if row.get("source") not in SOURCES:
        errors.append(f"source: must be one of {sorted(SOURCES)}")
    if not isinstance(row.get("user"), str):
        errors.append("user: str required (empty allowed for anonymized edge cases)")
    if not isinstance(row.get("comment"), str):
        errors.append("comment: str required")
    if "expected_category" in row and row["expected_category"] not in CATEGORIES:
        errors.append(f"expected_category: unknown {row['expected_category']}")
    return errors


def validate_classification(row: dict) -> list[str]:
    """`_classify_comments` docstring の契約 {index,user,comment,category,is_english}。"""
    errors = []
    if not isinstance(row.get("index"), int):
        errors.append("index: int required")
    if not isinstance(row.get("user"), str):
        errors.append("user: str required")
    if not isinstance(row.get("comment"), str):
        errors.append("comment: str required")
    if row.get("category") not in CATEGORIES:
        errors.append(f"category: must be one of {sorted(CATEGORIES)}")
    if "is_english" in row and not isinstance(row["is_english"], bool):
        errors.append("is_english: bool required when present")
    return errors


def validate_bug_dispatch_entry(row: dict) -> list[str]:
    """`_queue_stream_bug_reports_from_classification` の payload 構造。"""
    errors = []
    if not isinstance(row.get("created_at"), int):
        errors.append("created_at: int epoch required")
    if row.get("source") not in SOURCES:
        errors.append("source: unknown media")
    if not isinstance(row.get("batch_hash"), str):
        errors.append("batch_hash: str required")
    hash_val = row.get("hash")
    if not isinstance(hash_val, str) or len(hash_val) != 64:
        errors.append("hash: sha256 hex (64 chars) required")
    if row.get("category") != "stream_bug_report":
        errors.append("category: must be stream_bug_report")
    if row.get("status") != "pending":
        errors.append("status: must be pending at enqueue")
    if not isinstance(row.get("comment"), str) or not row["comment"]:
        errors.append("comment: non-empty str required")
    return errors


def validate_redacted_diag_event(row: dict) -> list[str]:
    """`lib/redacted_diag_ingest.ingest_report` の events_dir レコード。

    セキュリティ境界: user / comment の raw はこのレコードに存在してはならない。
    """
    errors = []
    for key in ("event_id", "category", "time", "redacted_context_hash", "source"):
        if key not in row:
            errors.append(f"{key}: missing")
    if "user" in row or "comment" in row:
        errors.append("raw user/comment must not appear in event record")
    return errors


class TestCorpus(unittest.TestCase):
    def _load(self, name: str) -> list[dict]:
        rows = []
        with (FIXTURES / name).open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    rows.append(json.loads(line))
        return rows

    def test_corpus_files_cover_three_media(self):
        for name in ("twitch_golden.jsonl", "youtube_golden.jsonl", "kick_golden.jsonl"):
            rows = self._load(name)
            self.assertGreater(len(rows), 0, f"{name} is empty")
            for i, row in enumerate(rows):
                self.assertEqual(validate_comment_event(row), [], f"{name}:{i}")

    def test_corpus_covers_required_kinds(self):
        kinds = set()
        for name in ("twitch_golden.jsonl", "youtube_golden.jsonl", "kick_golden.jsonl"):
            for row in self._load(name):
                kinds.add(row.get("kind"))
        for required in ("normal", "bug", "duplicate", "advice"):
            self.assertIn(required, kinds, f"corpus missing kind={required}")

    def test_schema_validators_accept_real_records(self):
        classification = {"index": 0, "user": "v1", "comment": "c", "category": "chitchat", "is_english": False}
        self.assertEqual(validate_classification(classification), [])
        queue_entry = {
            "created_at": 1,
            "source": "twitch",
            "batch_hash": "h",
            "hash": "a" * 64,
            "index": 0,
            "user": "v1",
            "comment": "bug",
            "category": "stream_bug_report",
            "status": "pending",
        }
        self.assertEqual(validate_bug_dispatch_entry(queue_entry), [])
        event = {
            "event_id": "e1",
            "category": "stream_bug_report",
            "time": 1,
            "redacted_context_hash": "h",
            "source": "twitch",
        }
        self.assertEqual(validate_redacted_diag_event(event), [])

    def test_redacted_event_rejects_raw_fields(self):
        event = {
            "event_id": "e1",
            "category": "c",
            "time": 1,
            "redacted_context_hash": "h",
            "source": "twitch",
            "user": "raw_name",
        }
        self.assertTrue(validate_redacted_diag_event(event))


if __name__ == "__main__":
    unittest.main()
