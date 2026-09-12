from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class StrategyRuntimeApplyRetryTests(unittest.TestCase):
    def _run(self, script: str, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
        merged = dict(**env)
        return subprocess.run(
            ["bash", "-c", script],
            cwd=ROOT,
            env=merged,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )

    def test_transient_callback_failure_retries_exact_bundle_once(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            source = root / "candidate.py"
            target = root / "strategy.py"
            helpers_source = root / "candidate_helpers"
            helpers_target = root / "strategy_helpers"
            counter = root / "callback_count"
            failure = root / "apply_failure.json"
            source.write_text("VALUE = 2\n", encoding="utf-8")
            target.write_text("VALUE = 1\n", encoding="utf-8")
            helpers_source.mkdir()
            (helpers_source / "__init__.py").write_text("FLAG = 2\n", encoding="utf-8")
            helpers_target.mkdir()
            (helpers_target / "__init__.py").write_text("FLAG = 1\n", encoding="utf-8")

            script = r'''
set -euo pipefail
source core/strategy_runtime.sh
transient_callback() {
  local n=0
  [ ! -f "$COUNT_FILE" ] || n=$(cat "$COUNT_FILE")
  n=$((n + 1))
  printf '%s\n' "$n" > "$COUNT_FILE"
  [ "$n" -ge 2 ]
}
STRATEGY_APPLY_RETRY_DELAY_SEC=0
STRATEGY_APPLY_FAILURE_FILE="$FAILURE_FILE"
strategy_runtime_atomic_apply_bundle_then \
  "$SOURCE_FILE" "$TARGET_FILE" "$HELPERS_SOURCE" "$HELPERS_TARGET" \
  transient_callback
'''
            env = {
                "PATH": "/usr/local/bin:/usr/bin:/bin",
                "HOME": str(root),
                "COUNT_FILE": str(counter),
                "FAILURE_FILE": str(failure),
                "SOURCE_FILE": str(source),
                "TARGET_FILE": str(target),
                "HELPERS_SOURCE": str(helpers_source),
                "HELPERS_TARGET": str(helpers_target),
                "TMP_STATE_DIR": str(root / "state"),
            }
            result = self._run(script, env)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(counter.read_text(encoding="utf-8").strip(), "2")
            self.assertEqual(target.read_text(encoding="utf-8"), "VALUE = 2\n")
            self.assertEqual(
                (helpers_target / "__init__.py").read_text(encoding="utf-8"),
                "FLAG = 2\n",
            )
            self.assertFalse(failure.exists())

    def test_exhausted_retry_records_fixed_apply_failure(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            target = root / "strategy.py"
            failure = root / "apply_failure.json"
            target.write_text("VALUE = 1\n", encoding="utf-8")
            missing_source = root / "missing.py"

            script = r'''
set -euo pipefail
source core/strategy_runtime.sh
STRATEGY_APPLY_RETRY_DELAY_SEC=0
STRATEGY_APPLY_FAILURE_FILE="$FAILURE_FILE"
if strategy_runtime_atomic_apply_bundle_then \
  "$SOURCE_FILE" "$TARGET_FILE" "" "$HELPERS_TARGET" ""; then
  exit 90
fi
'''
            env = {
                "PATH": "/usr/local/bin:/usr/bin:/bin",
                "HOME": str(root),
                "FAILURE_FILE": str(failure),
                "SOURCE_FILE": str(missing_source),
                "TARGET_FILE": str(target),
                "HELPERS_TARGET": str(root / "strategy_helpers"),
                "TMP_STATE_DIR": str(root / "state"),
            }
            result = self._run(script, env)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(target.read_text(encoding="utf-8"), "VALUE = 1\n")
            data = json.loads(failure.read_text(encoding="utf-8"))
            self.assertEqual(data["schema_version"], 1)
            self.assertEqual(data["failure_code"], "apply_bundle_failed")
            self.assertGreater(data["failed_at"], 0)


if __name__ == "__main__":
    unittest.main()
