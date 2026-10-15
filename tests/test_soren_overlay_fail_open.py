"""Run the real unified overlay renderer without live services or checkout data."""

import json
import os
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
import textwrap
import unittest
from html.parser import HTMLParser
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FALLBACK = "stats: no output (status_dashboard.py returned empty)"
OPS = "Workers 2/2 ONLINE\nHEALTH\nLoop RUNNING\nOPS fixture: 操作 <継続> & safe"


class OverlayDocument(HTMLParser):
    """Read the raw-pre contract and meta refresh from the generated HTML."""

    def __init__(self, text):
        super().__init__(convert_charrefs=True)
        self.pre = []
        self.refresh = []
        self._current_pre = None
        self.feed(text)

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if tag == "pre":
            self._current_pre = []
        elif tag == "meta" and values.get("http-equiv", "").lower() == "refresh":
            self.refresh.append(values.get("content"))

    def handle_endtag(self, tag):
        if tag == "pre" and self._current_pre is not None:
            self.pre.append("".join(self._current_pre))
            self._current_pre = None

    def handle_data(self, data):
        if self._current_pre is not None:
            self._current_pre.append(data)


class SorenOverlayFailOpenTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="soren-overlay-fail-open-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        # Use the real dashboard and all of its local rendering dependencies.
        # Copy no runtime state, .env, watcher, OBS, or network-facing scripts.
        for name in (
            "generate_soren_overlay.sh",
            "status_dashboard.py",
            "viewer_chat_monitor.sh",
            "extract_decide_hash.py",
            "lib/background_priority.sh",
            "lib/overlay_text.py",
            "lib/overlay_dashboard_cards.py",
            "lib/viewer_chat_cache.py",
            "lib/ai_backoff_status.py",
            "lib/country_names.py",
            "lib/docich_corner_stats.py",
            "lib/strategy_archive.py",
            "tools/ab_report.py",
        ):
            target = self.root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ROOT / name, target)
        # These two shell entrypoints are deliberately side-effect-free stubs.
        (self.root / "eloop_lib.sh").write_text(
            'ELOOP_LIB_DIR="$PWD"\n', encoding="utf-8"
        )
        show_status = self.root / "show_status.sh"
        show_status.write_text(
            "#!/bin/bash\nset -euo pipefail\n"
            '[ "$1" = "--once" ]\n'
            '[ "${SHOW_STATUS_SKIP_VIEWER_CHAT_REFRESH:-}" = "1" ]\n'
            '[ "${SHOW_STATUS_NO_FLICKER:-}" = "1" ]\n'
            "cat <<'OPS_FIXTURE'\n" + OPS + "\nOPS_FIXTURE\n",
            encoding="utf-8",
        )
        show_status.chmod(0o755)
        (self.root / "score_history.txt").write_text(
            "2026-10-05T00:00:00Z\t123\n"
            "2026-10-05T00:00:01Z\t456\n"
            "2026-10-05T00:00:02Z\t789\n",
            encoding="utf-8",
        )
        self.outputs = {
            "unified": self.root / "出力 統合" / "soren overlay.html",
            "ops": self.root / "出力 OPS" / "ops legacy.html",
            "stats": self.root / "出力 STATS" / "stats legacy.html",
        }
        # Do not inherit deployment paths, credentials, Python hooks, or invalid
        # dashboard settings from the process running the tests.
        self.env = {
            "PATH": os.path.dirname(sys.executable) + os.pathsep + os.defpath,
            "HOME": str(self.root),
            "LANG": "C.UTF-8",
            "PYTHONUTF8": "1",
            "PYTHONDONTWRITEBYTECODE": "1",
            "SOREN_BACKGROUND_NICE": "0",
            "DOCICH_STATE_DIR": str(self.root / "docich-fixture"),
            "VIEWER_CHAT_MONITOR_SOURCE": str(self.root / "absent-chat-source.log"),
            "VIEWER_CHAT_MONITOR_FILE": str(self.root / "absent-chat-cache.json"),
            "SOREN_OVERLAY_HTML_FILE": str(self.outputs["unified"]),
            "SHOW_STATUS_OVERLAY_HTML_FILE": str(self.outputs["ops"]),
            "STATUS_OVERLAY_HTML_FILE": str(self.outputs["stats"]),
        }

    def run_command(self, args):
        return subprocess.run(
            args,
            cwd=self.root,
            env=self.env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=15,
            umask=0o077,
        )

    def render_once(self):
        result = self.run_command(["bash", "generate_soren_overlay.sh", "once"])
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("generated:" + str(self.outputs["unified"]), result.stdout)
        documents = {}
        for name, path in self.outputs.items():
            with self.subTest(output=name):
                self.assertTrue(path.is_file(), result.stdout + result.stderr)
                self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o644)
                text = path.read_text(encoding="utf-8")
                self.assertTrue(text.startswith("<!doctype html>"))
                document = OverlayDocument(text)
                self.assertEqual(document.refresh, ["2"])
                self.assertTrue(document.pre, name)
                documents[name] = document
        self.assertEqual(documents["unified"].pre[0], OPS)
        self.assertEqual(documents["ops"].pre[0], OPS)
        # The real helper must still render its structured OPS card.
        for name in ("unified", "ops"):
            self.assertIn(
                'class="service-label">LOOP</span>',
                self.outputs[name].read_text(encoding="utf-8"),
            )
        return documents

    def assert_stats_fallback(self, documents):
        self.assertEqual(documents["unified"].pre[1], FALLBACK)
        self.assertEqual(documents["stats"].pre[0], FALLBACK)
        self.assertNotIn("Score Timeline", "\n".join(documents["stats"].pre))

    def test_real_dashboard_normal_control_with_unicode_paths_and_restrictive_umask(self):
        documents = self.render_once()
        stats = documents["stats"].pre[0]
        self.assertEqual(documents["unified"].pre[1], stats)
        self.assertNotIn(FALLBACK, stats)
        for heading in (
            "LastDrop:", "Score Timeline", "Score Distribution", "Strategy Comparison"
        ):
            self.assertIn(heading, stats)
        self.assertIn("789", stats)

    def test_real_dashboard_import_value_error_keeps_all_overlays(self):
        self.env["MIN_GAMES_BEFORE_REGRESSION"] = "invalid"
        # Prove this is the real dashboard's import-time configuration failure.
        direct = self.run_command([sys.executable, "-c", "import status_dashboard"])
        self.assertNotEqual(direct.returncode, 0)
        self.assertIn("ValueError", direct.stderr)
        self.assertIn("MIN_GAMES_BEFORE_REGRESSION", direct.stderr)
        self.assertIn("'invalid'", direct.stderr)
        self.assert_stats_fallback(self.render_once())

    def test_nonexported_shell_config_reaches_real_chat_producer_and_dashboard(self):
        source = self.root / "会話 入力" / "custom history.log"
        monitor = self.root / "会話 cache" / "custom monitor.json"
        source.parent.mkdir()
        messages = [f"viewer: before {index:02d}" for index in range(40)]
        source.write_text("\n".join(messages) + "\n", encoding="utf-8")
        for name in (
            "VIEWER_CHAT_MONITOR_SOURCE",
            "VIEWER_CHAT_MONITOR_FILE",
            "VIEWER_CHAT_MONITOR_LOOKBACK",
        ):
            self.env.pop(name, None)
        # Mirror core/config.sh: these are shell settings, not exported env.
        (self.root / "eloop_lib.sh").write_text(
            'ELOOP_LIB_DIR="$PWD"\n'
            f"VIEWER_CHAT_MONITOR_SOURCE={shlex.quote(str(source))}\n"
            f"VIEWER_CHAT_MONITOR_FILE={shlex.quote(str(monitor))}\n"
            "VIEWER_CHAT_MONITOR_LOOKBACK=21\n",
            encoding="utf-8",
        )
        nonexported = self.run_command([
            "bash", "-c",
            "source ./eloop_lib.sh; python3 -c "
            + shlex.quote(
                "import os; assert not any(key.startswith('VIEWER_CHAT_MONITOR_') "
                "for key in os.environ)"
            ),
        ])
        self.assertEqual(nonexported.returncode, 0, nonexported.stderr)

        documents = self.render_once()
        payload = json.loads(monitor.read_text(encoding="utf-8"))
        self.assertEqual(payload["source"], str(source))
        self.assertEqual(payload["lookback"], 21)
        self.assertEqual(payload["count"], 21)
        self.assertEqual(payload["latest"], messages[-1])
        self.assertEqual(payload["recent"], messages[-3:])
        self.assertIn("ChatObs", documents["stats"].pre[0])
        self.assertIn(messages[-1], documents["stats"].pre[0])

        # An unchanged render reuses the published summary without rewriting.
        before = (monitor.read_bytes(), monitor.stat().st_mtime_ns)
        self.render_once()
        self.assertEqual((monitor.read_bytes(), monitor.stat().st_mtime_ns), before)
        changed = "viewer: after 40"
        with source.open("a", encoding="utf-8") as stream:
            stream.write(changed + "\n")
        documents = self.render_once()
        updated = json.loads(monitor.read_text(encoding="utf-8"))
        self.assertEqual(updated["source"], str(source))
        self.assertEqual(updated["lookback"], 21)
        self.assertEqual(updated["count"], 21)
        self.assertEqual(updated["latest"], changed)
        self.assertNotEqual(updated["source_snapshot"], payload["source_snapshot"])
        for stats in (documents["unified"].pre[1], documents["stats"].pre[0]):
            self.assertIn("ChatObs", stats)
            self.assertIn(changed, stats)
            self.assertNotIn(messages[-1], stats)
        self.assertFalse((self.root / "tmp/state/viewer_chat_monitor.json").exists())

    def test_real_dashboard_render_os_error_keeps_all_overlays(self):
        hook_dir = self.root / "fault-hook"
        hook_dir.mkdir()
        marker = self.root / "dashboard-chdir-fault.txt"
        # Fault only the real render function's chdir; imports, cache refresh,
        # HTML helpers, shell execution, and HTML publication remain untouched.
        (hook_dir / "sitecustomize.py").write_text(
            textwrap.dedent(
                """\
                import os
                import sys

                original_chdir = os.chdir

                def fail_dashboard_chdir(path):
                    caller = sys._getframe(1).f_code
                    if (caller.co_name == "render_dashboard_text"
                            and caller.co_filename == os.environ["DASHBOARD_FAULT_FILE"]):
                        with open(os.environ["DASHBOARD_FAULT_MARKER"], "a", encoding="utf-8") as stream:
                            stream.write("render_dashboard_text:os.chdir\\n")
                        raise OSError("injected dashboard chdir failure")
                    return original_chdir(path)

                os.chdir = fail_dashboard_chdir
                """
            ),
            encoding="utf-8",
        )
        self.env.update(
            PYTHONPATH=str(hook_dir),
            DASHBOARD_FAULT_FILE=str(self.root / "status_dashboard.py"),
            DASHBOARD_FAULT_MARKER=str(marker),
        )
        direct = self.run_command([sys.executable, "status_dashboard.py"])
        self.assertNotEqual(direct.returncode, 0)
        self.assertIn("OSError: injected dashboard chdir failure", direct.stderr)
        self.assertEqual(
            marker.read_text(encoding="utf-8"), "render_dashboard_text:os.chdir\n"
        )
        marker.unlink()
        self.assert_stats_fallback(self.render_once())
        self.assertEqual(
            marker.read_text(encoding="utf-8"), "render_dashboard_text:os.chdir\n"
        )


if __name__ == "__main__":
    unittest.main()
