"""Retry classification only; no AI calls, candidate execution, or VM changes."""
from pathlib import Path
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]
SHADOW = "OS隔離runner評価がpassにならなかったため適用を見送り、既存のknown-good strategyを維持する (mode=shadow。詳細はreceipt参照)"
ENFORCE = SHADOW.replace("mode=shadow", "mode=enforce")

class ShadowValidationRetryTests(unittest.TestCase):
    def classify(self, message):
        script = '''source "$1/strategy/retry_policy.sh"
if _validation_error_is_nonretryable_infrastructure "$2"; then
    printf 'blocked:%s' "${VALIDATION_RETRY_BLOCK_CODE:-}"
else
    printf 'retryable:%s' "${VALIDATION_RETRY_BLOCK_CODE:-}"
fi
'''
        return subprocess.run(["bash", "-c", script, "bash", str(ROOT), message],
                              capture_output=True, text=True, timeout=10, check=True).stdout

    def test_shadow_refusal_is_nonretryable_and_has_distinct_code(self):
        self.assertEqual(self.classify(SHADOW), "blocked:isolated_runner_shadow")

    def test_unavailable_keeps_its_failure_code(self):
        self.assertEqual(self.classify("OS隔離runner未導入のため自動適用をfail-closedで停止 (probe failed)"),
                         "blocked:isolated_runner_unavailable")

    def test_enforce_and_candidate_failures_are_not_reclassified(self):
        for message in (ENFORCE, "strategy validation failed: SyntaxError (mode=shadow)",
                        "OS隔離runner評価エラー (mode=shadow。)", "", SHADOW.replace("shadow", "shadowed")):
            with self.subTest(message=message):
                self.assertEqual(self.classify(message), "retryable:")

    def test_classification_does_not_leak_between_attempts(self):
        script = '''source "$1/strategy/retry_policy.sh"
_validation_error_is_nonretryable_infrastructure "$2"
! _validation_error_is_nonretryable_infrastructure 'SyntaxError'
[ -z "${VALIDATION_RETRY_BLOCK_CODE:-}" ]
'''
        result = subprocess.run(["bash", "-c", script, "bash", str(ROOT), SHADOW],
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_improve_records_the_classified_reason_before_stopping_retries(self):
        source = (ROOT / "eloop_improve.sh").read_text(encoding="utf-8")
        start = source.index('if _validation_error_is_nonretryable_infrastructure "${VALIDATE_ERROR:-}"; then')
        block = source[start:source.index('\n\t\t\tfi', start)]
        self.assertIn('IMPROVE_FAILURE_CODE="${VALIDATION_RETRY_BLOCK_CODE:-isolated_runner_unavailable}"', block)
        self.assertIn('fresh_retry=$((IMPROVE_MAX_RETRIES + 1))', block)
        self.assertIn('break', block)

if __name__ == "__main__":
    unittest.main()
