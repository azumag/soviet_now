import ast
import os
import re
import subprocess
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from lib.objective import eval_score


class SharedEvaluatorContractTests(unittest.TestCase):
    def _python_table_literals(self, path):
        tree = ast.parse(open(path, encoding="utf-8").read())
        out = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Assign) or not isinstance(node.value, ast.Dict):
                continue
            keys = [k.value for k in node.value.keys if isinstance(k, ast.Constant) and isinstance(k.value, int)]
            if sorted(keys) == list(range(1, 17)):
                out.append(node.lineno)
        return out

    def test_wildcard_parallel_uses_shared_evaluator(self):
        path = os.path.join(ROOT, "wildcard_parallel.py")
        self.assertEqual(self._python_table_literals(path), [])
        self.assertIn("from lib.objective import", open(path, encoding="utf-8").read())

    def test_shell_evaluators_use_shared_module(self):
        for name in ("eloop.sh", "repair_current_run_from_history.sh"):
            src = open(os.path.join(ROOT, name), encoding="utf-8").read()
            self.assertNotRegex(src, r"(?<![_\w])(TB|tb)\s*=\s*\{1\s*:")
            self.assertIn("from lib.objective import", src)

    def test_eloop_expression_matches_module(self):
        src = open(os.path.join(ROOT, "eloop.sh"), encoding="utf-8").read()
        snippet = re.search(r'EVAL_SCORE=\$\(echo "\$RESULT_JSON" \| python3 -c "(.*?)" 2>/dev/null', src, re.S)
        self.assertIsNotNone(snippet)
        code = snippet.group(1).replace('\\"', '"')
        payload = '{"score":236,"final_types":[16,7,3],"soviet_created":true}\n'
        proc = subprocess.run([sys.executable, "-c", code], input=payload, capture_output=True, text=True, cwd=ROOT, timeout=30)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(int(proc.stdout.strip()), eval_score(236, [16, 7, 3], True))


if __name__ == "__main__":
    unittest.main()
