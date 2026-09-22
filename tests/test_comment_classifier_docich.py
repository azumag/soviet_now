"""Comment classification is docich's (azumag/docich#882 / #829).

This repo only calls docich's classifier and must never classify locally:
no heuristic, no Jev transport, no rubric, no fallback of its own.
"""
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


if __name__ == "__main__":
    unittest.main()
