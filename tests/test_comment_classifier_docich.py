"""Comment classification is docich's (azumag/docich#882 / #829).

This repo only calls docich's classifier and must never classify locally:
no heuristic, no Jev transport, no rubric, no fallback of its own.
"""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import textwrap
import unittest

ROOT = Path(__file__).resolve().parents[1]

REMOVED = (
    "_classify_comments_heuristic",
    "_comment_normalize_classification_for_comments",
    "_normalize_comment_classification_json",
    "_comment_enforce_english_safety",
    "_extract_comment_classification_json",
    "_validate_comment_classification_json",
    "_classify_comments_with_edit_contract",
    "_is_valid_comment_classification_output",
)


class DocichClassifierDelegationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        self.comments = self.dir / "comments.txt"
        self.comments.write_text("viewer: BGM聞こえない？\n", encoding="utf-8")

    def fake_classifier(self, body):
        path = self.dir / "docich-comment-classify"
        path.write_text("#!/usr/bin/env bash\n" + textwrap.dedent(body), encoding="utf-8")
        path.chmod(0o755)
        return path

    def classify(self, classifier):
        return subprocess.run(
            ["bash", "-c", 'log(){ printf "%s\\n" "$*" >&2; }; source broadcast/comment.sh; _classify_comments "$1"',
             "delegation-test", str(self.comments)],
            cwd=ROOT, env={**os.environ, "DOCICH_COMMENT_CLASSIFIER": str(classifier)},
            capture_output=True, text=True, timeout=10,
        )

    def test_delegates_the_batch_file_and_returns_docich_output_verbatim(self):
        seen = self.dir / "seen"
        classifier = self.fake_classifier(f'''
            printf '%s' "$1" >"{seen}"
            printf '[{{"index":1,"user":"viewer","comment":"BGM聞こえない？","category":"stream_bug_report","is_english":false}}]\\n'
        ''')
        result = self.classify(classifier)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(seen.read_text(), str(self.comments))
        self.assertEqual(result.stdout,
                         '[{"index":1,"user":"viewer","comment":"BGM聞こえない？","category":"stream_bug_report","is_english":false}]')

    def test_missing_failing_or_empty_classifier_leaves_the_batch_unclassified(self):
        for classifier in (self.dir / "absent",
                           self.fake_classifier("printf 'PARTIAL'; exit 1\n"),
                           self.fake_classifier("exit 0\n")):
            with self.subTest(classifier=classifier.name):
                result = self.classify(classifier)
                self.assertEqual(result.returncode, 1)
                self.assertEqual(result.stdout, "")

    def test_no_local_classifier_remains(self):
        result = subprocess.run(
            ["bash", "-c", "source broadcast/comment.sh; for f in " + " ".join(REMOVED)
             + '; do declare -F "$f" && echo "STILL:$f"; done; true'],
            cwd=ROOT, capture_output=True, text=True, timeout=10,
        )
        self.assertNotIn("STILL:", result.stdout)
        for path in ("broadcast/comment_classifier_jev.sh", "lib/comment_classifier_jev.py",
                     "lib/comment_classifier_jev_report.py", "prompts/comment_classifier.md"):
            self.assertFalse((ROOT / path).exists(), path)
        self.assertNotIn("comment_classifier_jev", (ROOT / "eloop_lib.sh").read_text(encoding="utf-8"))
        self.assertNotIn("api.typesafe.ai", (ROOT / "broadcast/comment.sh").read_text(encoding="utf-8"))

    def test_worker_env_refresh_covers_the_keys_docich_reads(self):
        worker = (ROOT / "workers/chat_worker.sh").read_text(encoding="utf-8")
        supervisor = (ROOT / "start_all.sh").read_text(encoding="utf-8")
        refresh = supervisor.split("_refresh_chat_worker_env() {", 1)[1].split("\n}", 1)[0]
        for key in ("COMMENT_CLASSIFIER_BACKEND", "TYPESAFE_API_KEY",
                    "DOCICH_JEV_ROUTE", "DOCICH_JEV_VERCEL_API_KEY"):
            with self.subTest(key=key):
                self.assertIn(key, worker.split("[ -f .env ]", 1)[0])
                self.assertIn(key, refresh)

    def test_routed_reply_agent_filter_never_returns_cli_or_unknown_agents(self):
        result = subprocess.run(
            ["bash", "-c", 'source broadcast/comment.sh; _comment_api_only_agent_list "$1"',
             "route-filter-test", "codex:one, local:gemma4:12b, opencode:two, local, local:bad;touch"],
            cwd=ROOT, capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "local:gemma4:12b,local")

    def test_routed_local_api_failure_does_not_escalate_to_cli_agents(self):
        with tempfile.TemporaryDirectory() as directory:
            tmp = Path(directory)
            prompt = tmp / "prompt.txt"
            prompt.write_text("synthetic prompt", encoding="utf-8")
            attempts = tmp / "attempts.txt"
            script = r'''
source broadcast/comment.sh
source lib/ai_generate.sh
_ai_backoff_dir() { printf '%s' "$TEST_BACKOFF_DIR"; }
_ai_backoff_check() { return 0; }
_ai_dispatch() { printf '%s\n' "$2" >>"$TEST_ATTEMPTS"; return 1; }
_ai_stats_record() { :; }
_ai_fail_streak_record() { printf '1'; }
_ai_backoff_set() { :; }
route_agents=$(_comment_api_only_agent_list "$1")
ai_generate_list COMMENT "$TEST_PROMPT" "$route_agents" >/dev/null 2>&1 || true
'''
            result = subprocess.run(
                ["bash", "-c", script, "routed-api-failure-test",
                 "codex:expensive,local:gemma4:12b,opencode:another,local:qwen3"],
                cwd=ROOT, capture_output=True, text=True, timeout=10,
                env={**os.environ, "TEST_BACKOFF_DIR": str(tmp / "backoff"),
                     "TEST_ATTEMPTS": str(attempts), "TEST_PROMPT": str(prompt)})
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(attempts.read_text(encoding="utf-8").splitlines(),
                             ["local:gemma4:12b", "local:qwen3"])

    def test_routed_reply_envelope_extracts_only_valid_metadata_rows_and_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "route.json"
            route = {"schema_version": 1,
                     "rows": [{"index": 1, "user": "viewer", "comment": "current question",
                               "category": "general_question", "is_english": False}],
                     "routing": {"status": "ready", "scope": "web", "reason": "jev",
                                 "confidence": .95, "research_status": "ok",
                                 "notes": "verified note", "sources": ["https://example.org/ref"]}}
            path.write_text(json.dumps(route), encoding="utf-8")
            helper = ROOT / "lib/comment_reply_route.py"
            rows = subprocess.run(["python3", str(helper), "rows", str(path)], cwd=ROOT,
                                  capture_output=True, text=True, timeout=5)
            metadata = subprocess.run(["python3", str(helper), "metadata", str(path)], cwd=ROOT,
                                      capture_output=True, text=True, timeout=5)
            evidence = subprocess.run(["python3", str(helper), "evidence", str(path)], cwd=ROOT,
                                      capture_output=True, text=True, timeout=5)
            self.assertEqual(rows.returncode, 0, rows.stderr)
            self.assertEqual(json.loads(rows.stdout), route["rows"])
            self.assertEqual(metadata.stdout.strip(), "ready\tweb\tok")
            self.assertIn('"notes": "verified note"', evidence.stdout)
            self.assertIn('"https://example.org/ref"', evidence.stdout)

            route["routing"]["scope"] = "../tmp/anything"
            path.write_text(json.dumps(route), encoding="utf-8")
            rejected = subprocess.run(["python3", str(helper), "metadata", str(path)], cwd=ROOT,
                                      capture_output=True, text=True, timeout=5)
            self.assertNotEqual(rejected.returncode, 0)

    def test_routed_reply_envelope_rejects_more_than_the_fetch_bound(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "route.json"
            route = {"schema_version": 1,
                     "rows": [{"index": index, "user": f"viewer{index}", "comment": "こんにちは",
                               "category": "chitchat", "is_english": False}
                              for index in range(1, 12)],
                     "routing": {"status": "ready", "scope": "api_only", "reason": "jev",
                                 "confidence": .95, "research_status": "not_requested",
                                 "notes": "", "sources": []}}
            path.write_text(json.dumps(route), encoding="utf-8")
            helper = ROOT / "lib/comment_reply_route.py"
            rejected = subprocess.run(["python3", str(helper), "metadata", str(path)], cwd=ROOT,
                                      capture_output=True, text=True, timeout=5)
            self.assertNotEqual(rejected.returncode, 0)
            route["rows"] = route["rows"][:1]

            route["routing"].update(scope="web", confidence=None)
            path.write_text(json.dumps(route), encoding="utf-8")
            rejected = subprocess.run(["python3", str(helper), "metadata", str(path)], cwd=ROOT,
                                      capture_output=True, text=True, timeout=5)
            self.assertNotEqual(rejected.returncode, 0)

            route["rows"][0]["user"] = "Nightbot"
            route["routing"].update(status="hold", scope="unknown", reason="local_notification",
                                    confidence=None, research_status="not_requested",
                                    notes="", sources=[])
            path.write_text(json.dumps(route), encoding="utf-8")
            accepted_hold = subprocess.run(["python3", str(helper), "metadata", str(path)], cwd=ROOT,
                                      capture_output=True, text=True, timeout=5)
            self.assertEqual(accepted_hold.returncode, 0, accepted_hold.stderr)
            rejected_hold_rows = subprocess.run(["python3", str(helper), "rows", str(path)], cwd=ROOT,
                                      capture_output=True, text=True, timeout=5)
            self.assertNotEqual(rejected_hold_rows.returncode, 0)

            route["rows"][0]["user"] = "viewer"
            route["routing"].update(status="ready", scope="api_only")
            path.write_text(json.dumps(route), encoding="utf-8")
            rejected = subprocess.run(["python3", str(helper), "metadata", str(path)], cwd=ROOT,
                                      capture_output=True, text=True, timeout=5)
            self.assertNotEqual(rejected.returncode, 0)

    def test_routed_reply_is_opt_in_and_api_failures_cannot_reach_cli_generation(self):
        source = (ROOT / "broadcast/comment.sh").read_text(encoding="utf-8")
        routed = source.index('case "${DOCICH_REPLY_ROUTING_ENABLED:-0}" in')
        hold = source.index('if [ "$comment_route_hold" = "true" ]; then', routed)
        route_ready = source.index('if [ "$comment_route_status" != "ready" ]; then', routed)
        rows_read = source.index('classification_json=$(_comment_route_read rows', route_ready)
        hold_return = source.index('return 0', source.index(
            'if [ "$comment_route_hold" = "true" ]; then\n\t\tlog "[COMMENT] 根拠未確認', routed))
        generate = source.index('ai_generate_list "COMMENT"', hold)
        ack_after_generation = source.index('ack-batch "$comment_batch_file"', generate)
        api_filter = source.index('_comment_api_only_agent_list "$comment_agent_list"', hold)
        translation_filter = source.index('_comment_api_only_agent_list "$translation_agents"', hold)
        preflight_api = source.index('_comment_api_only_agent_list "$route_pre_agents"', routed)
        route_call = source.index('timeout --kill-after=5s 55s "$route_cli"', routed)
        full_batch = source.index('printf \'%s\\n\' "$twitch_comments_original" >"$comment_batch_file"')
        self.assertLess(routed, hold)
        self.assertLess(preflight_api, route_call)
        self.assertLess(route_ready, rows_read)
        self.assertLess(full_batch, routed)
        self.assertLess(hold_return, generate)
        self.assertLess(hold_return, ack_after_generation)
        self.assertLess(hold, generate)
        self.assertLess(api_filter, generate)
        self.assertLess(translation_filter, source.index('_comment_generate_translation', hold))
        self.assertIn('if [ "$comment_route_enabled" = "true" ]; then\n\t\t\tlog "[COMMENT] 分類結果:', source)

        # Fetch and ack operate on the same bounded source batch. Docich splits
        # JEV requests internally but returns ready only after every row passed.
        for filename in ("twitch_chat.sh", "youtube_chat.sh", "kick_chat.sh"):
            with self.subTest(filename=filename):
                self.assertIn("--limit 10", (ROOT / filename).read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
