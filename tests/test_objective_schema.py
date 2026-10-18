"""issue #132 Phase 1: 目的関数の単一 versioned evaluator (lib/objective.py)。

eloop.sh (試合中の EVAL_SCORE) / wildcard_parallel.py (候補選抜) /
repair_current_run_from_history.sh (current_run の修復 seed) は 2026-10-14 まで
それぞれ型ボーナス表の写しを持っていました。型ボーナス表は「T15 を 2 個持ったまま
終局 (2×12096=24192) より、実際に建国した局面のほうが 23,256 点低く評価される」
という逆転を 2026-09 に修正した経緯があり、どれか 1 件だけが古いまま残ると、
試合中の評価と修復 seed の評価が違う目的空間の値として同じ hash の標本に混ざります。

ここでは (1) 表の値が修正後の実装と一致すること、(2) 重複した写しが repo に
残っていないこと、(3) version stamp が旧履歴と新履歴を混ぜないことを固定します。
"""
import ast
import os
import re
import subprocess
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from lib.objective import (  # noqa: E402
    OBJECTIVE_SCHEMA_VERSION,
    SOVIET_CREATED_BONUS,
    TYPE_BONUS,
    eval_score,
    is_current,
    mixed_versions,
    stamp,
    terminal_bonus,
    version_of,
)

# 2026-09 修正後の値。ここを変えるのは「目的の意味が変わった」ときだけ。
EXPECTED_TABLE = {
    1: 0, 2: 0, 3: 1, 4: 3, 5: 7, 6: 15, 7: 32, 8: 67, 9: 141, 10: 296,
    11: 622, 12: 1306, 13: 2743, 14: 5760, 15: 12096, 16: 25402,
}


class TableValueTests(unittest.TestCase):
    def test_table_is_the_post_fix_values(self):
        self.assertEqual(TYPE_BONUS, EXPECTED_TABLE)

    def test_soviet_bonus_is_800(self):
        self.assertEqual(SOVIET_CREATED_BONUS, 800)

    def test_eval_score_matches_legacy_eloop_expression(self):
        """旧 eloop.sh の `raw + sum(TB.get(t, 0)) + (800 if soviet)` と同一。"""
        cases = [
            (0, [], False),
            (1234, [3, 7, 11], False),
            (1234, [3, 7, 11], True),
            (100, [15, 15], False),          # T15×2 を持ったまま終局
            (100, [16], True),               # 実際に建国
            (0, [], True),                   # raw 0 でも建国加点は乗る
        ]
        for raw, types, soviet in cases:
            with self.subTest(raw=raw, types=types, soviet=soviet):
                legacy = raw + sum(EXPECTED_TABLE.get(t, 0) for t in types) + (800 if soviet else 0)
                self.assertEqual(eval_score(raw, types, soviet), legacy)

    def test_founding_beats_two_russias_holding_nothing(self):
        """T15×2 を最終併合すると評価が下がる逆転 (issue #132 P0-0) が無い。"""
        before = eval_score(100, [15, 15, 7, 3], False)
        after = eval_score(100 + 136, [16, 7, 3], True)
        self.assertGreater(after, before)

    def test_unknown_and_junk_types_are_zero(self):
        self.assertEqual(terminal_bonus([99, None, "bad"], False), 0)
        self.assertEqual(eval_score("bad", [99], False), 0)

    def test_monotonic_in_tier(self):
        for k in range(3, 16):
            self.assertGreater(TYPE_BONUS[k + 1], TYPE_BONUS[k])


class NoDuplicateCopyTests(unittest.TestCase):
    """表の写しが repo に残っていないこと。1 件でも残すと意味がありません。"""

    def _python_table_literals(self, path):
        with open(path, encoding="utf-8") as fh:
            tree = ast.parse(fh.read())
        out = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Assign):
                continue
            value = node.value
            if isinstance(value, ast.Dict) and len(value.keys) >= 14:
                keys = []
                for key in value.keys:
                    if isinstance(key, ast.Constant) and isinstance(key.value, int):
                        keys.append(key.value)
                    else:
                        keys = []
                        break
                if sorted(keys) == list(range(1, 17)):
                    out.append(getattr(node, "lineno", -1))
        return out

    def test_wildcard_parallel_has_no_inline_table(self):
        self.assertEqual(
            self._python_table_literals(os.path.join(ROOT, "wildcard_parallel.py")), [],
            "wildcard_parallel.py に型ボーナス表の写しが復活している")

    def test_wildcard_parallel_imports_the_shared_table(self):
        src = open(os.path.join(ROOT, "wildcard_parallel.py"), encoding="utf-8").read()
        self.assertIn("from lib.objective import", src)

    def test_shell_scripts_have_no_inline_table(self):
        """eloop.sh / repair_current_run_from_history.sh のインライン表が消えている。"""
        for name in ("eloop.sh", "repair_current_run_from_history.sh"):
            src = open(os.path.join(ROOT, name), encoding="utf-8").read()
            with self.subTest(script=name):
                self.assertNotRegex(src, r"(?<![_\w])(TB|tb)\s*=\s*\{1\s*:",
                                    "%s に型ボーナス表の写しが復活している" % name)
                self.assertIn("from lib.objective import", src)

    def test_eloop_eval_score_produces_the_same_number_as_the_module(self):
        """eloop.sh のインライン python が module と同じ値を出す (実行走査)。"""
        snippet = re.search(
            r"EVAL_SCORE=\$\(echo \"\$RESULT_JSON\" \| python3 -c \"(.*?)\" 2>/dev/null",
            open(os.path.join(ROOT, "eloop.sh"), encoding="utf-8").read(), re.S)
        self.assertIsNotNone(snippet, "eloop.sh の EVAL_SCORE 計算ブロックが見つからない")
        code = snippet.group(1).replace("\\\"", "\"")  # type: ignore[union-attr]
        result = subprocess.run(
            [sys.executable, "-c", code],
            input='{"score": 100, "final_types": [15, 15, 7, 3], "soviet_created": false}\n',
            capture_output=True, text=True, cwd=ROOT, timeout=30)
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertEqual(int(result.stdout.strip()), eval_score(100, [15, 15, 7, 3], False))


class VersionStampTests(unittest.TestCase):
    def test_version_is_stamped_and_readable(self):
        rec = stamp({"score": 1})
        self.assertEqual(rec["objective_schema_version"], OBJECTIVE_SCHEMA_VERSION)
        self.assertTrue(is_current(rec))
        self.assertEqual(version_of(rec), OBJECTIVE_SCHEMA_VERSION)

    def test_legacy_records_have_no_version(self):
        self.assertIsNone(version_of({"score": 1}))
        self.assertIsNone(version_of(None))
        self.assertIsNone(version_of("nope"))
        self.assertFalse(is_current({"score": 1}))

    def test_mixed_versions_are_detected(self):
        """旧 EVAL_SCORE と新目的値を 1 つの標本へ混ぜていないことが判る。"""
        legacy = {"score": 1}
        current = stamp({"score": 2})
        self.assertEqual(mixed_versions([current, current]), [OBJECTIVE_SCHEMA_VERSION])
        self.assertEqual(mixed_versions([legacy, legacy]), [None])
        self.assertEqual(len(mixed_versions([legacy, current])), 2)

    def test_empty_sample_is_not_mixed(self):
        self.assertEqual(mixed_versions([]), [])
        self.assertEqual(mixed_versions(None), [])


if __name__ == "__main__":
    unittest.main()
