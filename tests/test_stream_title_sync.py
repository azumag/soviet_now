import copy
import json
from pathlib import Path
import sys
import tempfile
from datetime import datetime, timezone
from unittest import mock
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'lib'))
from stream_title_sync import (EVENT_FILE, EVENT_MAX_BYTES, KICK_RESULTS, SKIP_REASONS, YOUTUBE_RESULTS, _append_title_event, main, youtube_title, kick_title, normalize_title)

class FakeYouTube:
    def __init__(self):
        self.rows=[{'id':'abcdefghijk','status':{'lifeCycleStatus':'live'},'contentDetails':{'boundStreamId':'ours'}}]
        self.snippet={'title':'old','categoryId':'20','description':'keep description','tags':['game'],'defaultLanguage':'ja','channelId':'owner','thumbnails':{'default':{'url':'public'}}}
        self.writes=[]
    def broadcasts(self):return self.rows
    def _api(self,path,*,params,method='GET',payload=None):
        assert path=='videos' and params['part']=='snippet'
        if method=='PUT':
            self.writes.append(copy.deepcopy(payload));self.snippet=payload['snippet']
        return {'items':[{'id':'abcdefghijk','snippet':self.snippet}]}

class SyncTests(unittest.TestCase):
    def test_youtube_preserves_mutable_fields_and_readback(self):
        api=FakeYouTube();self.assertEqual(youtube_title(api,'new','ours'),'updated')
        p=api.writes[0];self.assertEqual(p['snippet']['description'],'keep description')
        self.assertEqual(p['snippet']['tags'],['game']);self.assertEqual(p['snippet']['categoryId'],'20')
        self.assertNotIn('channelId',p['snippet']);self.assertNotIn('status',p)
        self.assertEqual(youtube_title(api,'new','ours'),'unchanged');self.assertEqual(len(api.writes),1)
    def test_wrong_stream_or_multiple_broadcasts_never_write(self):
        api=FakeYouTube();self.assertEqual(youtube_title(api,'new','other'),'no_unique_live_broadcast')
        api.rows*=2;self.assertEqual(youtube_title(api,'new','ours'),'no_unique_live_broadcast');self.assertEqual(api.writes,[])
    def test_missing_expected_stream_never_queries_or_writes(self):
        api=FakeYouTube();self.assertEqual(youtube_title(api,'new'),'stream_not_configured');self.assertFalse(api.writes)
    def test_ended_broadcast_never_updated(self):
        api=FakeYouTube();api.rows[0]['status']['lifeCycleStatus']='complete'
        self.assertEqual(youtube_title(api,'new','ours'),'no_unique_live_broadcast');self.assertEqual(api.writes,[])
    def test_missing_category_never_writes(self):
        api=FakeYouTube();del api.snippet['categoryId'];self.assertEqual(youtube_title(api,'new','ours'),'invalid_video_snippet');self.assertFalse(api.writes)
    def test_kick_checks_principal_live_and_only_changes_title(self):
        row={'broadcaster_user_id':123,'stream_title':'old','stream':{'is_live':True,'key':'must-not-log'}};writes=[]
        def request(method,payload=None):
            if method=='PATCH':writes.append(payload);row['stream_title']=payload['stream_title']
            return {'data':[row]}
        self.assertEqual(kick_title(request,'new','999'),'wrong_broadcaster');self.assertFalse(writes)
        self.assertEqual(kick_title(request,'new','123'),'updated');self.assertEqual(writes,[{'stream_title':'new'}])
        self.assertEqual(kick_title(request,'new','123'),'unchanged');row['stream']['is_live']=False
        self.assertEqual(kick_title(request,'next','123'),'not_live')
    def test_title_limit_and_markup_rejected(self):
        self.assertEqual(len(normalize_title('あ'*150)),100)
        for text in ('',' ','<unsafe>'):
            with self.assertRaises(ValueError):normalize_title(text)


class TitleSyncJournalTests(unittest.TestCase):
    def test_result_record_is_private_bounded_and_contains_no_title(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            stamp = datetime(2026, 10, 2, 12, 0, tzinfo=timezone.utc)
            self.assertTrue(_append_title_event(
                "result", skip_reason="none", youtube="updated", kick="unchanged",
                root=root, source_sha="a" * 40, now=stamp,
            ))
            path = root / EVENT_FILE
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            record = json.loads(path.read_text())
            self.assertEqual(record["occurred_at"], "2026-10-02T12:00:00Z")
            self.assertEqual(record["soviet_sha"], "a" * 40)
            self.assertEqual(record["youtube"], "updated")
            self.assertEqual(record["kick"], "unchanged")
            self.assertNotIn("title", record)
            self.assertNotIn("secret-title", path.read_text())
            self.assertLessEqual(path.stat().st_size, EVENT_MAX_BYTES)

    def test_writer_accepts_only_fixed_statuses_and_skip_reasons(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            now = datetime(2026, 10, 2, tzinfo=timezone.utc)
            self.assertFalse(_append_title_event(
                "result", skip_reason="none", youtube="private API response",
                kick="updated", root=root, source_sha="b" * 40, now=now,
            ))
            self.assertFalse(_append_title_event(
                "skipped", skip_reason="private title text", youtube="not_run",
                kick="not_run", root=root, source_sha="b" * 40, now=now,
            ))
            self.assertFalse(_append_title_event(
                "result", skip_reason="none", youtube="updated", kick="updated",
                root=root, source_sha="not-a-sha", now=now,
            ))
            self.assertTrue(YOUTUBE_RESULTS.isdisjoint({"private API response"}))
            self.assertTrue(KICK_RESULTS.isdisjoint({"private API response"}))
            self.assertIn("category_only", SKIP_REASONS)

    def test_skip_cli_records_fixed_reason_without_reading_input_or_emitting_output(self):
        with mock.patch("stream_title_sync._append_title_event", return_value=True) as append:
            output = []
            with mock.patch("sys.stdout.write", side_effect=lambda value: output.append(value)):
                self.assertEqual(main(["--record-skip", "category_only"]), 0)
            self.assertEqual(output, [])
            append.assert_called_once_with(
                "skipped", skip_reason="category_only", youtube="not_run", kick="not_run"
            )

    def test_journal_stays_bounded_after_repeated_updates(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            stamp = datetime(2026, 10, 2, tzinfo=timezone.utc)
            for _ in range(250):
                self.assertTrue(_append_title_event(
                    "result", skip_reason="none", youtube="unchanged", kick="not_live",
                    root=root, source_sha="e" * 40, now=stamp,
                ))
            path = root / EVENT_FILE
            self.assertLessEqual(path.stat().st_size, EVENT_MAX_BYTES)
            self.assertTrue(path.read_bytes().endswith(b"\n"))
            self.assertEqual(json.loads(path.read_text().splitlines()[-1])["kick"], "not_live")

    def test_writer_refuses_symlink_state_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            outside = root / "outside"
            outside.mkdir()
            (root / "tmp").symlink_to(outside, target_is_directory=True)
            self.assertFalse(_append_title_event(
                "skipped", skip_reason="category_only", youtube="not_run",
                kick="not_run", root=root, source_sha="c" * 40,
                now=datetime(2026, 10, 2, tzinfo=timezone.utc),
            ))
            self.assertFalse(list(outside.iterdir()))
