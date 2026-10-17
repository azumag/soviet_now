import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
GEN = ROOT / "generate_soren_overlay.sh"
STATUS = ROOT / "show_status.sh"
MODULE = ROOT / "lib" / "soren_overlay_render.py"


class SorenOverlayIdleCpuContracts(unittest.TestCase):
    def test_render_loop_avoids_repeated_directory_and_chmod_processes(self):
        module = MODULE.read_text(encoding="utf-8")
        self.assertIn("os.chmod(out_file, 0o644)", module)
        self.assertIn("os.chmod(ops_legacy_file, 0o644)", module)
        self.assertIn("os.chmod(stats_legacy_file, 0o644)", module)
        render = GEN.read_text(encoding="utf-8").split("render_once() {", 1)[1]
        self.assertNotIn('dirname "$out_file"', render)
        self.assertNotIn("chmod 644 ", render)

    def test_parent_directories_are_created_once_before_render_loop(self):
        text = GEN.read_text(encoding="utf-8")
        setup_pos = text.index("_ensure_overlay_dirs() {")
        render_pos = text.index("render_once() {")
        self.assertLess(setup_pos, render_pos)
        setup = text[setup_pos:render_pos]
        self.assertIn('mkdir -p "${_overlay_dirs[@]}"', setup)
        self.assertIn("_OVERLAY_DIRS_READY=1", setup)
        self.assertIn("_ensure_overlay_dirs", text[render_pos:])

    def test_unified_overlay_renders_stats_in_existing_python_process(self):
        module = MODULE.read_text(encoding="utf-8")
        self.assertNotIn("stats_raw=$(HIDE_STATUS_DASHBOARD_OBSERVER_SECTION=0 python3 status_dashboard.py", module)
        self.assertNotIn("SOREN_STATS_RAW=", module)
        self.assertIn("from status_dashboard import render_dashboard_text", module)
        self.assertIn("stats_raw = normalize_overlay_text(render_dashboard_text())", module)
        self.assertIn("except Exception:", module)
        self.assertIn('stats_raw = ""', module)

    def test_unified_overlay_renders_event_overlay_in_existing_python_process(self):
        module = MODULE.read_text(encoding="utf-8")
        self.assertNotIn('python3 "$ELOOP_LIB_DIR/generate_event_overlay.py"', module)
        self.assertIn("from generate_event_overlay import render_event_overlay", module)
        self.assertIn("render_event_overlay(", module)
        self.assertIn("except Exception:", module)

    def test_unified_overlay_owns_viewer_chat_refresh(self):
        module = MODULE.read_text(encoding="utf-8")
        status = STATUS.read_text(encoding="utf-8")
        self.assertIn("refresh_viewer_chat_monitor_if_changed", module)
        self.assertNotIn('"$source_file" -nt "$monitor_file"', module)
        self.assertLess(module.index("refresh_viewer_chat_monitor_if_changed("),
                        module.index("from status_dashboard import render_dashboard_text"))
        self.assertIn('env["SHOW_STATUS_SKIP_VIEWER_CHAT_REFRESH"] = "1"', module)
        self.assertIn('env["SHOW_STATUS_NO_FLICKER"] = "1"', module)
        self.assertIn('"${SHOW_STATUS_SKIP_VIEWER_CHAT_REFRESH:-0}" != "1"', status)
        # OPS ChatObs is intentionally filtered from the unified output, so
        # refreshing it inside show_status would be duplicate work.
        self.assertIn('"ChatObs"', module)

    def test_watch_loop_is_one_long_lived_interpreter(self):
        """#970: no per-tick interpreter/heredoc, no shell `sleep` child."""
        text = GEN.read_text(encoding="utf-8")
        self.assertNotIn("<<'PY'", text)
        self.assertIn('python3 lib/soren_overlay_render.py serve "$interval"', text)
        self.assertIn('python3 lib/soren_overlay_render.py once "${_OVERLAY_ARGS[@]}"', text)
        self.assertNotIn('sleep "$interval"', text)
        # The supervised worker cmdline must stay `generate_soren_overlay.sh watch`.
        start_all = (ROOT / "start_all.sh").read_text(encoding="utf-8")
        self.assertIn("generate_soren_overlay[.]sh[ \\t]+watch", start_all)
        self.assertIn("./generate_soren_overlay.sh watch 2", start_all)
        module = MODULE.read_text(encoding="utf-8")
        self.assertIn("def serve(interval, **kwargs)", module)
        self.assertIn("time.sleep(interval)", module)

    def test_watch_stops_the_renderer_with_the_worker(self):
        """#675 P1: the long-lived renderer must not outlive its shell worker."""
        watch = GEN.read_text(encoding="utf-8").split("watch)", 1)[1].split("start)", 1)[0]
        # Background the interpreter itself (no helper-function subshell) and
        # record its PID so the trap can stop it.
        self.assertIn("python3 lib/soren_overlay_render.py serve", watch)
        self.assertIn("_overlay_render_pid=$!", watch)
        self.assertIn("trap '_overlay_stop_renderer' TERM INT EXIT", watch)
        self.assertIn('wait "$_overlay_render_pid"', watch)
        self.assertNotIn("_overlay_render_env python3", watch)
        # The renderer also exits on its own once reparented (SIGKILLed worker).
        module = MODULE.read_text(encoding="utf-8")
        self.assertIn("os.getppid() != parent", module)
        self.assertIn("_overlay_stop_renderer() {", GEN.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
