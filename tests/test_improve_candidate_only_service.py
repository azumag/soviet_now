import unittest
from pathlib import Path


class ImproveCandidateOnlyServiceTest(unittest.TestCase):
    def test_production_service_prevents_live_strategy_overwrite(self):
        service = (
            Path(__file__).resolve().parents[1]
            / "deploy/soren-runtime/soren-runtime.service"
        ).read_text(encoding="utf-8")
        self.assertIn("Environment=IMPROVE_PROMOTE_VIA_PR=1", service)

    def test_improve_worker_has_candidate_only_gate(self):
        worker = (Path(__file__).resolve().parents[1] / "eloop_improve.sh").read_text(
            encoding="utf-8"
        )
        self.assertIn("_improve_promote_via_pr_record", worker)
        self.assertIn('${IMPROVE_PROMOTE_VIA_PR:-0}', worker)


if __name__ == "__main__":
    unittest.main()
