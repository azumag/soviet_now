"""issue #132 Phase 2: policy_bundle_hash が「着手を決める一式」の同一性を表すこと。

decide hash は `decide()` の AST しか見ないため、helper・解析器・runner・解析器モードが
違っても同じ値になる。A/B の腕が「戦略以外は同一」であることを保証するにはこれでは足りない。
"""
import os
import shutil
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import policy_bundle  # noqa: E402


class PolicyBundleTests(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        os.makedirs(os.path.join(self.d, "strategy_helpers"))
        self._write("strategy.py", "from strategy_helpers import board_stats\n\n\ndef decide(gs, an):\n    return {'x': board_stats.f()}\n")
        self._write("analyze_board.py", "def analyze():\n    return 1\n")
        self._write("strategy_runner.py", "def enforce():\n    return 2\n")
        self._write("strategy_helpers/board_stats.py", "def f():\n    return 3\n")
        self._write("strategy_helpers/unused.py", "def g():\n    return 4\n")

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def _write(self, rel, text):
        with open(os.path.join(self.d, rel), "w", encoding="utf-8") as fh:
            fh.write(text)

    def _h(self, env=None):
        return policy_bundle.bundle(os.path.join(self.d, "strategy.py"), self.d, env or {})[0]

    def test_stable(self):
        self.assertEqual(self._h(), self._h())

    def test_comment_and_docstring_only_change_does_not_move_hash(self):
        base = self._h()
        self._write("strategy.py",
                    '"""説明を足しただけ。"""\nfrom strategy_helpers import board_stats\n\n\ndef decide(gs, an):\n'
                    "    # ここにコメント\n    return {'x': board_stats.f()}\n")
        self.assertEqual(self._h(), base, "コメント/docstring だけで bundle hash が変わってしまう")

    def test_helper_change_moves_hash(self):
        base = self._h()
        self._write("strategy_helpers/board_stats.py", "def f():\n    return 99\n")
        self.assertNotEqual(self._h(), base, "到達する helper の変更が bundle hash に出ていない")

    def test_unreachable_helper_change_does_not_move_hash(self):
        base = self._h()
        self._write("strategy_helpers/unused.py", "def g():\n    return 99\n")
        self.assertEqual(self._h(), base, "import していない helper で bundle hash が変わる")

    def test_analyzer_and_runner_changes_move_hash(self):
        base = self._h()
        self._write("analyze_board.py", "def analyze():\n    return 42\n")
        h2 = self._h()
        self.assertNotEqual(h2, base)
        self._write("strategy_runner.py", "def enforce():\n    return 42\n")
        self.assertNotEqual(self._h(), h2)

    def test_mode_env_moves_hash(self):
        base = self._h({})
        self.assertNotEqual(self._h({"ANALYZE_BOARD_LANDING_ARC": "3"}), base,
                            "解析器モードの違いが bundle hash に出ていない")
        self.assertNotEqual(self._h({"SOREN_SETTLE_REQUIRED": "4"}), base)

    def test_strategy_logic_change_moves_hash(self):
        base = self._h()
        self._write("strategy.py", "from strategy_helpers import board_stats\n\n\ndef decide(gs, an):\n    return {'x': -board_stats.f()}\n")
        self.assertNotEqual(self._h(), base)


if __name__ == "__main__":
    unittest.main()
