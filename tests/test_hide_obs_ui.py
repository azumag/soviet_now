import os
from pathlib import Path
import subprocess
import tempfile
import unittest


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "hide_obs_ui.sh"


class HideObsUiTests(unittest.TestCase):
    def run_script(self, window_rows):
        with tempfile.TemporaryDirectory() as raw_tmp:
            tmp = Path(raw_tmp)
            trace = tmp / "trace.log"
            wmctrl = tmp / "wmctrl"
            xdotool = tmp / "xdotool"
            wmctrl.write_text(
                "#!/bin/sh\n"
                "printf 'wmctrl %s\\n' \"$*\" >>\"$TRACE\"\n"
                "if [ \"${1:-}\" = '-lx' ]; then printf '%s\\n' \"$WINDOW_ROWS\"; fi\n"
            )
            xdotool.write_text(
                "#!/bin/sh\n"
                "printf 'xdotool %s\\n' \"$*\" >>\"$TRACE\"\n"
            )
            wmctrl.chmod(0o755)
            xdotool.chmod(0o755)
            env = {
                **os.environ,
                "PATH": f"{tmp}:/usr/bin:/bin",
                "TRACE": str(trace),
                "WINDOW_ROWS": window_rows,
                "OBS_HIDE_UI_ATTEMPTS": "1",
                "OBS_HIDE_UI_POLL_SEC": "0",
            }
            result = subprocess.run(
                ["bash", str(SCRIPT)],
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )
            return result, trace.read_text() if trace.exists() else ""

    def test_no_mapped_obs_window_is_success(self):
        result, trace = self.run_script(
            "0x01800004 0 chromium-browser.Chromium-browser host Unity WebGL Player"
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("windowminimize", trace)

    def test_mapped_obs_is_minimized_and_game_reactivated(self):
        result, trace = self.run_script(
            "0x01800004 0 chromium-browser.Chromium-browser host Unity WebGL Player\n"
            "0x00e00009 0 obs.obs host OBS 30.0.2"
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("wmctrl -i -r 0x00e00009 -b add,hidden", trace)
        self.assertIn("xdotool windowminimize 0x00e00009", trace)
        self.assertIn("wmctrl -i -a 0x01800004", trace)


if __name__ == "__main__":
    unittest.main()
