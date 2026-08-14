from pathlib import Path
import subprocess
import unittest


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "set_game_internal_size.sh"


class SetGameInternalSizeTests(unittest.TestCase):
    def test_print_plan_is_redacted_and_keeps_720p_output(self) -> None:
        result = subprocess.run(
            ["bash", str(SCRIPT), "--print-plan", "704x396"],
            cwd=REPO_ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('"requested_internal_size": "704x396"', result.stdout)
        self.assertIn('"output_size_unchanged": "1280x720"', result.stdout)
        self.assertIn('"destination_credentials_read": false', result.stdout)
        self.assertNotIn("rtmp", result.stdout.lower())

    def test_apply_requires_exact_confirmation_before_platform_checks(self) -> None:
        result = subprocess.run(
            ["bash", str(SCRIPT), "--apply", "640x360"],
            cwd=REPO_ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("--confirm-live-restart", result.stderr)

    def test_invalid_or_unplanned_sizes_fail_before_mutation(self) -> None:
        for size in ("640x400", "1920x1080", "unsafe"):
            with self.subTest(size=size):
                result = subprocess.run(
                    ["bash", str(SCRIPT), "--apply", size, "--confirm-live-restart"],
                    cwd=REPO_ROOT,
                    text=True,
                    capture_output=True,
                    check=False,
                )
                self.assertEqual(result.returncode, 2)
                self.assertIn("unsupported internal size", result.stderr)

    def test_source_refuses_active_monitor_and_has_automatic_rollback(self) -> None:
        source = SCRIPT.read_text(encoding="utf-8")
        self.assertIn("stop the active direct-stream monitor", source)
        self.assertIn('cp -p "$BACKUP" "$ENV_FILE"', source)
        self.assertIn('systemctl restart "$RUNTIME_UNIT"', source)
        self.assertIn("game_render_health.json", source)
        self.assertNotIn("push.conf", source)


if __name__ == "__main__":
    unittest.main()
