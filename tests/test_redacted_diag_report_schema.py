"""docich#33: output schema enforcement (finding/evidence_ref/confidence/
recommended_action only) and evidence_ref traceability for the diagnostic
runner. See lib/redacted_diag_report.py.
"""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from lib import redacted_diag_report as report  # noqa: E402


GOOD_REF = "chat_worker_log_tail"


def _finding(**overrides):
    base = {
        "finding": "something",
        "evidence_ref": GOOD_REF,
        "confidence": "medium",
        "recommended_action": "look at it",
    }
    base.update(overrides)
    return base


class ValidateFindingsTests(unittest.TestCase):
    def test_valid_single_finding_passes(self):
        out = report.validate_findings([_finding()], allowed_evidence_refs={GOOD_REF})
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["evidence_ref"], GOOD_REF)

    def test_not_a_list_rejected(self):
        with self.assertRaises(report.SchemaError):
            report.validate_findings({"finding": "x"}, allowed_evidence_refs={GOOD_REF})

    def test_empty_list_rejected(self):
        with self.assertRaises(report.SchemaError):
            report.validate_findings([], allowed_evidence_refs={GOOD_REF})

    def test_extra_key_rejected(self):
        with self.assertRaises(report.SchemaError):
            report.validate_findings([_finding(shell_command="rm -rf /")], allowed_evidence_refs={GOOD_REF})

    def test_missing_key_rejected(self):
        item = _finding()
        del item["recommended_action"]
        with self.assertRaises(report.SchemaError):
            report.validate_findings([item], allowed_evidence_refs={GOOD_REF})

    def test_invalid_confidence_value_rejected(self):
        with self.assertRaises(report.SchemaError):
            report.validate_findings([_finding(confidence="99%")], allowed_evidence_refs={GOOD_REF})

    def test_empty_string_field_rejected(self):
        with self.assertRaises(report.SchemaError):
            report.validate_findings([_finding(finding="   ")], allowed_evidence_refs={GOOD_REF})

    def test_evidence_ref_not_fetched_for_this_run_rejected(self):
        with self.assertRaises(report.SchemaError):
            report.validate_findings([_finding(evidence_ref="never_fetched")], allowed_evidence_refs={GOOD_REF})

    def test_non_dict_item_rejected(self):
        with self.assertRaises(report.SchemaError):
            report.validate_findings(["just a string"], allowed_evidence_refs={GOOD_REF})

    def test_code_change_shaped_field_is_not_a_recognized_key(self):
        # Defense in depth: even a plausible "fix" field is rejected because
        # the schema is closed, not merely additive.
        item = _finding()
        item["patch"] = "diff --git a/x b/x"
        with self.assertRaises(report.SchemaError):
            report.validate_findings([item], allowed_evidence_refs={GOOD_REF})


class ReportBuildersTests(unittest.TestCase):
    def test_success_report_status_ok(self):
        r = report.build_success_report(
            event_id="e1", findings=[_finding()], tmpfs_used=False, sandbox_backend="test"
        )
        self.assertEqual(r["status"], "ok")
        self.assertEqual(r["event_id"], "e1")
        self.assertIn("findings", r)

    def test_failure_report_status_failed_and_no_findings(self):
        r = report.build_failure_report(
            event_id="e1", reason="timeout", tmpfs_used=False, sandbox_backend="test"
        )
        self.assertEqual(r["status"], "failed")
        self.assertEqual(r["findings"], [])
        self.assertEqual(r["reason"], "timeout")


if __name__ == "__main__":
    unittest.main()
