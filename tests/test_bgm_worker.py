import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class BgmPolicyTest(unittest.TestCase):
    def test_hanjuku_suppresses_fallback_and_next_cli_game_restores_it(self):
        with tempfile.TemporaryDirectory() as directory:
            canonical = Path(directory) / "canonical.json"
            bgm = Path(directory) / "bgm.ogg"
            bgm.touch()
            env = {**os.environ, "DOCICH_CANONICAL": str(canonical), "SOREN_BGM_FILE": str(bgm)}
            for phase, game, expected in (
                ("ready", "nsnake", 0),
                ("ready", "hanjuku-hero", 1),
                ("ready", "nsnake", 0),
                ("ready", "sorengame", 1),
                ("ready", "soren91", 1),
                ("ready", None, 1),
                ("draining", "nsnake", 1),
            ):
                with self.subTest(phase=phase, game=game):
                    canonical.write_text(json.dumps({"phase": phase, "active": {"game": game}}))
                    result = subprocess.run(
                        ["bash", "-c", 'source "$1"; cli_game_active', "test", str(ROOT / "bgm_worker.sh")],
                        env=env, capture_output=True, timeout=5,
                    )
                    self.assertEqual(result.returncode, expected, result.stderr)


if __name__ == "__main__":
    unittest.main()
