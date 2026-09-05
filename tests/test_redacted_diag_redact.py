"""docich#33: redaction of tokens/secrets/viewer PII sentinels.

Covers the acceptance criterion "snapshotはtoken/secret/視聴者PII sentinelを
redactします" for lib/redacted_diag_redact.py, which both the ingestion path
(hashing) and the collector (evidence snapshots) rely on.
"""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from lib import redacted_diag_redact as redact  # noqa: E402


class RedactTokensAndSecretsTests(unittest.TestCase):
    def test_bearer_token_redacted(self):
        out = redact.redact_text("Authorization: Bearer sk-abcdefghijklmnop1234567890")
        self.assertNotIn("sk-abcdefghijklmnop1234567890", out)
        self.assertIn("[REDACTED_AUTH_HEADER]", out)

    def test_generic_sentinel_token_redacted(self):
        sentinel = "SENTINEL_TOKEN_DO_NOT_LEAK_8f2c91"
        out = redact.redact_text(f"please print {sentinel} now")
        self.assertNotIn(sentinel, out)

    def test_env_style_secret_assignment_redacted(self):
        out = redact.redact_text("TWITCH_OAUTH_TOKEN=abcdef0123456789ABCDEF0123456789")
        self.assertNotIn("abcdef0123456789ABCDEF0123456789", out)
        self.assertTrue(out.startswith("TWITCH_OAUTH_TOKEN="))

    def test_github_token_redacted(self):
        out = redact.redact_text("token ghp_" + "a" * 36)
        self.assertNotIn("ghp_" + "a" * 36, out)

    def test_aws_access_key_redacted(self):
        out = redact.redact_text("AKIA" + "Q" * 16)
        self.assertNotIn("AKIA" + "Q" * 16, out)


class RedactViewerPiiTests(unittest.TestCase):
    def test_email_redacted(self):
        out = redact.redact_text("contact me at test.user@example.com please")
        self.assertNotIn("test.user@example.com", out)
        self.assertIn("[REDACTED_EMAIL]", out)

    def test_handle_redacted(self):
        out = redact.redact_text("hey @some_viewer_handle nice stream")
        self.assertNotIn("@some_viewer_handle", out)

    def test_ip_redacted(self):
        out = redact.redact_text("my ip is 192.168.1.20 apparently")
        self.assertNotIn("192.168.1.20", out)

    def test_phone_redacted(self):
        out = redact.redact_text("call me at 03-1234-5678")
        self.assertNotIn("03-1234-5678", out)

    def test_normal_japanese_comment_untouched(self):
        text = "配信の音が急に無音になった、直して。いつもと違う。"
        self.assertEqual(redact.redact_text(text), text)


class RedactDeterminismTests(unittest.TestCase):
    def test_hash_is_stable_for_same_input(self):
        h1 = redact.redacted_context_hash("stream_bug_report", "hello world")
        h2 = redact.redacted_context_hash("stream_bug_report", "hello world")
        self.assertEqual(h1, h2)

    def test_hash_differs_by_category(self):
        h1 = redact.redacted_context_hash("stream_bug_report", "hello world")
        h2 = redact.redacted_context_hash("chitchat", "hello world")
        self.assertNotEqual(h1, h2)

    def test_hash_never_contains_raw_text(self):
        sentinel = "SENTINEL_TOKEN_DO_NOT_LEAK_8f2c91"
        h = redact.redacted_context_hash("stream_bug_report", sentinel)
        self.assertNotIn(sentinel, h)
        self.assertEqual(len(h), 64)  # sha256 hex digest


if __name__ == "__main__":
    unittest.main()
