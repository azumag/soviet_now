"""Shared broadcast cards: docich preferred, Soviet standalone fallback retained."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
RENDERER_PATH = Path("src/docich/broadcast_dashboard_cards.py")
GAME = (
    "SOREN/CORNER: SOREN91 / soren91 / ACTIVE\n"
    "Stats: 120 results / best=1 / Recent30=5.2\n"
    "Rank Timeline\nLast8: 12 9 4 7 1 3 2 5"
)


class DashboardDelegationTest(unittest.TestCase):
    def _render(self, root):
        script = (
            "import json; from lib.overlay_dashboard_cards import "
            "render_game_dashboard,render_ops_dashboard,dashboard_css;"
            "game=" + repr(GAME) + ";"
            "print(json.dumps([render_game_dashboard(game),"
            "render_ops_dashboard('Workers 3/3 ONLINE\\nBackend LIVE'),"
            "dashboard_css()]))"
        )
        run = subprocess.run(
            [sys.executable, "-c", script],
            cwd=ROOT, env={**os.environ, "DOCICH_ROOT": str(root)},
            capture_output=True, text=True, timeout=10, check=False,
        )
        self.assertEqual(run.returncode, 0, run.stderr)
        import json
        return json.loads(run.stdout)

    def test_missing_invalid_and_failing_docich_keep_local_renderer(self):
        with tempfile.TemporaryDirectory(prefix="soren-cards-") as tmp:
            root = Path(tmp)
            initial = self._render(root)
            self.assertIn("GAME PERFORMANCE", initial[0])
            self.assertIn("SYSTEM HEALTH", initial[1])
            self.assertIn(".broadcast-card", initial[2])

            file = root / RENDERER_PATH
            file.parent.mkdir(parents=True)
            file.write_text("not valid python!", encoding="utf-8")
            self.assertEqual(self._render(root), initial)

            file.write_text(
                "DASHBOARD_CARDS_CONTRACT_VERSION=999\n"
                "def render_game_dashboard(*x):return 'WRONG'\n"
                "def render_ops_dashboard(*x):return 'WRONG'\n"
                "def dashboard_css():return 'WRONG'\n",
                encoding="utf-8",
            )
            self.assertEqual(self._render(root), initial)

            file.write_text(
                "DASHBOARD_CARDS_CONTRACT_VERSION=1\n"
                "def render_game_dashboard(*x):raise SystemExit(1)\n"
                "def render_ops_dashboard(*x):raise RuntimeError('failed')\n"
                "def dashboard_css():raise RuntimeError('failed')\n",
                encoding="utf-8",
            )
            self.assertEqual(self._render(root), initial)

    def test_compatible_docich_module_has_priority(self):
        with tempfile.TemporaryDirectory(prefix="soren-cards-") as tmp:
            root = Path(tmp)
            file = root / RENDERER_PATH
            file.parent.mkdir(parents=True)
            file.write_text(
                "DASHBOARD_CARDS_CONTRACT_VERSION=1\n"
                "def render_game_dashboard(*x):return 'DOCICH_GAME'\n"
                "def render_ops_dashboard(*x):return 'DOCICH_OPS'\n"
                "def dashboard_css():return 'DOCICH_CSS'\n",
                encoding="utf-8",
            )
            self.assertEqual(
                self._render(root),
                ["DOCICH_GAME", "DOCICH_OPS", "DOCICH_CSS"],
            )


if __name__ == "__main__":
    unittest.main()
