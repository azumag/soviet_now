"""Offline Bash integration tests for the opt-in docich bridge."""
from pathlib import Path
import json
import os
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
BRIDGE = ROOT / "broadcast/comment_screen_context.sh"


class BridgeTests(unittest.TestCase):
    def run_shell(self, extra="", *, flag="1", allow="1", cli=True, rc=0):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            prompt = root / "original prompt.txt"
            prompt.write_text("元のプロンプト\n", encoding="utf-8")
            helper = root / "fake helper"
            if cli:
                helper.write_text("#!/usr/bin/env python3\n" +
                    "import json,os,sys\n" +
                    "from pathlib import Path\n" +
                    "Path(os.environ['CAPTURE']).write_text(json.dumps({'rows':sys.stdin.read(),'args':sys.argv[1:],'attempt':os.environ.get('COMMENT_SCREEN_CAPTURE_ATTEMPT')}))\n" +
                    "print('NATIVE')\n" + f"raise SystemExit({rc})\n")
                helper.chmod(0o755)
            env = {**os.environ, "BRIDGE": str(BRIDGE), "PROMPT": str(prompt),
                   "DOCICH_COMMENT_SCREEN_CLI": str(helper), "CAPTURE": str(root/"capture"),
                   "BASE_PROMPT": str(root/"base_prompt"), "TMPDIR": directory,
                   "COMMENT_SCREEN_CONTEXT_ENABLED": flag, "DOCICH_ALLOW_REAL_AI": allow,
                   "SIDE1": str(root/"last agent"), "SIDE2": str(root/"failure kind")}
            script = r'''
set -u
ai_generate_list() { base_called=yes; cp -- "$2" "$BASE_PROMPT"; printf 'BASE'; }
source "$BRIDGE"
call_reply() {
 local classification_json='[{"index":1,"comment":"$(touch SHOULD_NOT_EXIST)"}]' attempt=1
 ai_generate_list "${LABEL:-COMMENT}" "$PROMPT" "local:vision" 90 "${VALIDATOR:-_comment_is_valid_generation_candidate}" "$SIDE1" "$SIDE2"
}
''' + (extra or "call_reply")
            result = subprocess.run(["bash", "-c", script], env=env, capture_output=True, text=True, timeout=5, cwd=root)
            capture = json.loads((root/"capture").read_text()) if (root/"capture").exists() else None
            base = (root/"base_prompt").read_text() if (root/"base_prompt").exists() else None
            self.assertFalse((root/"SHOULD_NOT_EXIST").exists())
            self.assertEqual(prompt.read_text(), "元のプロンプト\n")
            self.assertFalse(list(root.glob("eloop_comment_screen_text_*")))
            return result, capture, base

    def test_enabled_passes_rows_arguments_and_attempt(self):
        result, capture, base = self.run_shell()
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "NATIVE\n")
        self.assertEqual(capture["attempt"], "1")
        self.assertIn("$(touch SHOULD_NOT_EXIST)", capture["rows"])
        self.assertIn("local:vision", capture["args"])
        self.assertIsNone(base)

    def test_off_preserves_base_side_effects(self):
        result, capture, base = self.run_shell('call_reply; test "$base_called" = yes', flag="0")
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "BASE")
        self.assertIsNone(capture)
        self.assertEqual(base, "元のプロンプト\n")

    def test_other_labels_and_validators_bypass(self):
        for extra in ('LABEL=COMMENT_TRANSLATION call_reply', 'LABEL=RADIO call_reply', 'VALIDATOR=custom call_reply'):
            with self.subTest(extra=extra):
                result, capture, base = self.run_shell(extra)
                self.assertEqual(result.returncode, 0)
                self.assertIsNone(capture)
                self.assertEqual(base, "元のプロンプト\n")

    def test_missing_caller_scope_bypasses(self):
        result, capture, _ = self.run_shell('ai_generate_list COMMENT "$PROMPT" local:vision 90 _comment_is_valid_generation_candidate "$SIDE1" "$SIDE2"')
        self.assertEqual(result.stdout, "BASE")
        self.assertIsNone(capture)

    def test_missing_helper_or_permission_uses_unseen_text(self):
        for kwargs in ({"cli":False}, {"allow":"0"}):
            with self.subTest(kwargs=kwargs):
                result, capture, base = self.run_shell(**kwargs)
                self.assertEqual(result.returncode, 0)
                self.assertEqual(result.stdout, "BASE")
                self.assertIsNone(capture)
                self.assertIn("画面を確認できていません", base)

    def test_native_failure_codes_preserved(self):
        for rc in (1, 2, 79, 91, 92):
            with self.subTest(rc=rc):
                result, capture, base = self.run_shell(rc=rc)
                self.assertEqual(result.returncode, rc)
                self.assertIsNotNone(capture)
                self.assertIsNone(base)

    def test_retry_number_is_forwarded(self):
        result, capture, _ = self.run_shell('classification_json="[]"; attempt=3; ai_generate_list COMMENT "$PROMPT" local:vision 90 _comment_is_valid_generation_candidate "$SIDE1" "$SIDE2"')
        self.assertEqual(result.returncode, 0)
        self.assertEqual(capture["attempt"], "3")

    def test_resourcing_does_not_wrap_itself(self):
        result, _, base = self.run_shell('source "$BRIDGE"; source "$BRIDGE"; COMMENT_SCREEN_CONTEXT_ENABLED=0 call_reply')
        self.assertEqual(result.stdout, "BASE")
        self.assertEqual(base, "元のプロンプト\n")

    def test_reload_preserves_fresh_base_definition(self):
        result, _, _ = self.run_shell('ai_generate_list() { printf NEW; }; source "$BRIDGE"; COMMENT_SCREEN_CONTEXT_ENABLED=0 call_reply')
        self.assertEqual(result.stdout, "NEW")

    def test_loader_connects_after_comment_policy(self):
        source = (ROOT/"eloop_lib.sh").read_text()
        self.assertLess(source.index('source "$ELOOP_LIB_DIR/broadcast/comment_runtime_policy.sh"'),
                        source.index('source "$ELOOP_LIB_DIR/broadcast/comment_screen_context.sh"'))


if __name__ == "__main__":
    unittest.main()
