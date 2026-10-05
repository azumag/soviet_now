import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SHOW = ROOT / "show_status.sh"


class ShowStatusBuiltinReadTests(unittest.TestCase):
    def test_one_line_state_reads_do_not_use_cat_command_substitutions(self):
        source = SHOW.read_text(encoding="utf-8")
        self.assertNotIn("$(cat", source)
        self.assertIn("_read_first_line() {", source)

    def test_helper_preserves_missing_empty_and_value_semantics(self):
        source = SHOW.read_text(encoding="utf-8")
        start = source.index("_read_first_line() {")
        end = source.index("\n}\n", start) + 3
        helper = source[start:end]

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            value = root / "value"
            empty = root / "empty"
            missing = root / "missing"
            value.write_text("123\nignored\n", encoding="utf-8")
            empty.write_text("", encoding="utf-8")
            script = f"""
{helper}
_read_first_line {value!s} 0
print -r -- "value=$REPLY"
_read_first_line {empty!s} 7
print -r -- "empty=$REPLY"
_read_first_line {missing!s} 9
print -r -- "missing=$REPLY"
"""
            proc = subprocess.run(
                ["zsh", "-c", script],
                cwd=ROOT,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertEqual(
                proc.stdout.splitlines(),
                ["value=123", "empty=7", "missing=9"],
            )


if __name__ == "__main__":
    unittest.main()
