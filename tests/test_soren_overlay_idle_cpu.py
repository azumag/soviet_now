import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
GEN = ROOT / "generate_soren_overlay.sh"
STATUS = ROOT / "show_status.sh"


class SorenOverlayIdleCpuContracts(unittest.TestCase):
    def test_render_loop_avoids_repeated_directory_and_chmod_processes(self):
        text = GEN.read_text(encoding="utf-8")
        render = text.split("render_once() {", 1)[1].split("\n}\n\nrender_event_overlay_indicators", 1)[0]
        self.assertNotIn('dirname "$out_file"', render)
        self.assertNotIn("\n\tchmod 644 ", render)
        self.assertIn("os.chmod(out_file, 0o644)", render)
        self.assertIn("os.chmod(ops_legacy_file, 0o644)", render)
        self.assertIn("os.chmod(stats_legacy_file, 0o644)", render)

    def test_parent_directories_are_created_once_before_render_loop(self):
        text = GEN.read_text(encoding="utf-8")
        setup_pos = text.index("_ensure_overlay_dirs() {")
        render_pos = text.index("render_once() {")
        self.assertLess(setup_pos, render_pos)
        setup = text[setup_pos:render_pos]
        self.assertIn('mkdir -p "${_overlay_dirs[@]}"', setup)
        self.assertIn('_OVERLAY_DIRS_READY=1', setup)
        render = text[render_pos:text.index("render_event_overlay_indicators")]
        self.assertIn("_ensure_overlay_dirs", render)
        self.assertNotIn('mkdir -p "', render)

    def test_unified_overlay_owns_viewer_chat_refresh(self):
        gen = GEN.read_text(encoding="utf-8")
        status = STATUS.read_text(encoding="utf-8")
        self.assertIn("_refresh_viewer_chat_monitor_if_changed", gen)
        self.assertIn('"$source_file" -nt "$monitor_file"', gen)
        self.assertIn("SHOW_STATUS_SKIP_VIEWER_CHAT_REFRESH=1", gen)
        self.assertIn('"${SHOW_STATUS_SKIP_VIEWER_CHAT_REFRESH:-0}" != "1"', status)
        # OPS ChatObs is intentionally filtered from the unified output, so
        # refreshing it inside show_status would be duplicate work.
        self.assertIn('"ChatObs"', gen)


if __name__ == "__main__":
    unittest.main()
