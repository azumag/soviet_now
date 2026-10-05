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

    def test_unified_overlay_renders_stats_in_existing_python_process(self):
        gen = GEN.read_text(encoding="utf-8")
        self.assertNotIn("stats_raw=$(HIDE_STATUS_DASHBOARD_OBSERVER_SECTION=0 python3 status_dashboard.py", gen)
        self.assertNotIn("SOREN_STATS_RAW=", gen)
        self.assertIn("from status_dashboard import render_dashboard_text", gen)
        self.assertIn("stats_raw = normalize_overlay_text(render_dashboard_text())", gen)
        self.assertIn("except Exception:", gen)
        self.assertIn('stats_raw = ""', gen)

    def test_unified_overlay_renders_event_overlay_in_existing_python_process(self):
        gen = GEN.read_text(encoding="utf-8")
        self.assertNotIn('python3 "$ELOOP_LIB_DIR/generate_event_overlay.py"', gen)
        self.assertIn("from generate_event_overlay import render_event_overlay", gen)
        self.assertIn("render_event_overlay(", gen)
        self.assertIn('EVENT_OVERLAY_STATE_BASE="$ELOOP_LIB_DIR"', gen)
        self.assertIn("except Exception:", gen)

    def test_overlay_status_snapshot_skips_terminal_render_pipeline(self):
        gen = GEN.read_text(encoding="utf-8")
        status = STATUS.read_text(encoding="utf-8")
        self.assertIn("./show_status.sh --raw-once", gen)
        self.assertNotIn(
            "SHOW_STATUS_SKIP_VIEWER_CHAT_REFRESH=1 SHOW_STATUS_NO_FLICKER=1 ./show_status.sh --once",
            gen,
        )
        raw_branch = status.split('if [[ "$SHOW_STATUS_RAW_ONCE" == "1" ]]; then', 1)[1]
        raw_branch = raw_branch.split("fi", 1)[0]
        self.assertIn("show_status", raw_branch)
        self.assertIn("exit 0", raw_branch)
        self.assertNotIn("_render_status_once", raw_branch)
        self.assertLess(
            status.index('if [[ "$SHOW_STATUS_RAW_ONCE" == "1" ]]; then'),
            status.index("printf '\\033[?25l'"),
        )

    def test_unified_overlay_owns_viewer_chat_refresh(self):
        gen = GEN.read_text(encoding="utf-8")
        status = STATUS.read_text(encoding="utf-8")
        self.assertIn("refresh_viewer_chat_monitor_if_changed", gen)
        self.assertNotIn('"$source_file" -nt "$monitor_file"', gen)
        render = gen.split("<<'PY'", 1)[1]
        self.assertLess(render.index("refresh_viewer_chat_monitor_if_changed("),
                        render.index("from status_dashboard import render_dashboard_text"))
        self.assertIn("SHOW_STATUS_SKIP_VIEWER_CHAT_REFRESH=1", gen)
        self.assertIn('"${SHOW_STATUS_SKIP_VIEWER_CHAT_REFRESH:-0}" != "1"', status)
        # OPS ChatObs is intentionally filtered from the unified output, so
        # refreshing it inside show_status would be duplicate work.
        self.assertIn('"ChatObs"', gen)


if __name__ == "__main__":
    unittest.main()
