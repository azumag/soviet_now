"""v763 SURFACE_DIVERSITY の重みを env ではなくモジュール定数で持つことを固定する。

なぜ (実測 2026-10-25〜11-03):
  * 候補の AST deny gate (strategy/sandbox.sh) は `import os` を禁じる。稼働中の root だけは gate を通らない。
  * root の _v763_weight() は os.environ の V763_DIVERSITY_W (本番 1.0) を読んでいた。
  * その root から派生した LLM 候補 (3fedf0802964 / ac52cd600115 / 2962d882fe13 など) は、
    os を削って _v763_weight() を `return 0.0` に書き換え gate を通していた。B 腕だけ v763 が黙って無効だった。
ここでは (1) env に関係なく本番値 1.0 が効くこと、(2) root が os を import しないこと、
(3) root がそのまま候補 gate を通ること (派生候補が何も削らずに済むこと) を固定する。
"""
import ast
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "tests"))
import strategy  # noqa: E402
from test_strategy_sandbox_no_host_exec import _run_validate  # noqa: E402


class V763WeightConstantTests(unittest.TestCase):
    def test_production_weight_is_kept_without_env(self):
        saved = os.environ.pop("V763_DIVERSITY_W", None)
        try:
            self.assertEqual(strategy._v763_weight(), 1.0)
            os.environ["V763_DIVERSITY_W"] = "0"
            self.assertEqual(strategy._v763_weight(), 1.0, "env では切り替わらない")
        finally:
            os.environ.pop("V763_DIVERSITY_W", None)
            if saved is not None:
                os.environ["V763_DIVERSITY_W"] = saved

    def test_weight_clamps_bad_values_and_saturates(self):
        saved = strategy._V763_DIVERSITY_W
        try:
            for bad, want in (("abc", 0.0), (None, 0.0), (-1.0, 0.0), (float("nan"), 0.0), (0.0, 0.0), (50.0, 8.0)):
                strategy._V763_DIVERSITY_W = bad
                self.assertEqual(strategy._v763_weight(), want, repr(bad))
        finally:
            strategy._V763_DIVERSITY_W = saved

    def test_root_does_not_import_os(self):
        tree = ast.parse((REPO_ROOT / "strategy.py").read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                self.assertNotIn("os", [a.name for a in node.names])
            elif isinstance(node, ast.ImportFrom):
                self.assertNotEqual(node.module, "os")

    def test_root_passes_candidate_gate_as_is(self):
        with tempfile.TemporaryDirectory() as cwd:
            shutil.copy(REPO_ROOT / "strategy.py", Path(cwd) / "strategy.py.staging")
            env = os.environ.copy()
            env["GAME_STATE"] = "game_state.json"
            result = _run_validate(
                'validate_strategy "strategy.py.staging" "nonexistent_helpers"\n'
                'echo "RC=$?"\n'
                'echo "VALIDATE_ERROR=$VALIDATE_ERROR"\n',
                cwd=cwd, env=env,
            )
            self.assertNotIn("ast-deny-gate rejected candidate", result.stdout + result.stderr,
                             msg=f"stdout={result.stdout}\nstderr={result.stderr}")
            self.assertIn("RC=0", result.stdout, msg=f"stdout={result.stdout}\nstderr={result.stderr}")


if __name__ == "__main__":
    unittest.main()
