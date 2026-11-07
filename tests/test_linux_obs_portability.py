import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest


REPO_ROOT = Path(__file__).resolve().parents[1]


def find_node() -> str | None:
    resolved = shutil.which("node")
    if resolved:
        return resolved

    candidates = [
        Path.home() / ".nvm/versions/node/v23.10.0/bin/node",
        Path("/usr/bin/node"),
        Path("/opt/homebrew/bin/node"),
        Path("/usr/local/bin/node"),
        Path("/Volumes/satelite/homebrew/homebrew/bin/node"),
    ]
    candidates.extend(sorted((Path.home() / ".nvm/versions/node").glob("*/bin/node"), reverse=True))
    return next((str(path) for path in candidates if path.is_file() and os.access(path, os.X_OK)), None)


class LinuxObsPortabilityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.node = find_node()
        if not cls.node:
            raise unittest.SkipTest("node is required for OBS portability tests")

    def window_capture_config(self, platform: str, **overrides: str) -> dict:
        env = os.environ.copy()
        for key in (
            "OBS_WINDOW_CAPTURE_INPUT_KIND",
            "OBS_WINDOW_CAPTURE_FAMILY",
            "OBS_WINDOW_CAPTURE_WINDOW_PROPERTY",
            "OBS_WINDOW_CAPTURE_BROWSER_REGEX",
            "OBS_WINDOW_CAPTURE_AUDIO",
            "OBS_WINDOW_AUDIO_SOURCE",
            "OBS_XSHM_SCREEN",
            "OBS_XSHM_SHOW_CURSOR",
            "OBS_XSHM_ADVANCED",
            "OBS_XSHM_SERVER",
            "OBS_XSHM_CUT_TOP",
            "OBS_XSHM_CUT_LEFT",
            "OBS_XSHM_CUT_RIGHT",
            "OBS_XSHM_CUT_BOT",
        ):
            env.pop(key, None)
        env.update(
            {
                "NODE_BIN": self.node,
                "OBS_WEBSOCKET_PORT": "4455",
                "OBS_WEBSOCKET_PASSWORD": "test-only",
                "OBS_WINDOW_CAPTURE_CONFIG_ONLY": "1",
                "OBS_WINDOW_CAPTURE_CLEAN_STALE_WILDCARD": "0",
                "SOREN_OBS_PLATFORM": platform,
            }
        )
        env.update(overrides)
        result = subprocess.run(
            [
                "bash",
                str(REPO_ROOT / "obs_window_capture_source.sh"),
                "ensure",
                "soren",
                "sorengame",
                r"Unity WebGL Player \| soren-game",
            ],
            cwd=REPO_ROOT,
            env=env,
            text=True,
            capture_output=True,
            check=True,
        )
        return json.loads(result.stdout)

    def window_capture_requests(
        self,
        current_settings: dict | None,
        *,
        expect_success: bool = True,
        **overrides: str,
    ) -> tuple[subprocess.CompletedProcess[str], list[dict], bool]:
        env = os.environ.copy()
        for key in (
            "NODE_OPTIONS",
            "EXPLORE_MODE",
            "OBS_WINDOW_CAPTURE_CONFIG_ONLY",
            "OBS_WINDOW_AUDIO_SOURCE",
            "OBS_XSHM_SCREEN",
            "OBS_XSHM_SHOW_CURSOR",
            "OBS_XSHM_ADVANCED",
            "OBS_XSHM_SERVER",
            "OBS_XSHM_CUT_TOP",
            "OBS_XSHM_CUT_LEFT",
            "OBS_XSHM_CUT_RIGHT",
            "OBS_XSHM_CUT_BOT",
            "FAKE_OBS_EXISTING_INPUT_KIND",
            "FAKE_OBS_EXISTING_UNVERSIONED_INPUT_KIND",
            "FAKE_OBS_WINDOW_ITEM_NAME",
            "FAKE_OBS_WINDOW_ITEM_VALUE",
        ):
            env.pop(key, None)

        with tempfile.TemporaryDirectory() as temp_dir:
            trace_path = Path(temp_dir) / "requests.json"
            construct_path = Path(temp_dir) / "websocket-constructed"
            env.update(
                {
                    "NODE_BIN": self.node,
                    "NODE_OPTIONS": f"--require={REPO_ROOT / 'tests/fake_obs_websocket.cjs'}",
                    "OBS_WEBSOCKET_PORT": "4455",
                    "OBS_WEBSOCKET_PASSWORD": "test-only",
                    "OBS_WINDOW_CAPTURE_CLEAN_STALE_WILDCARD": "0",
                    "SOREN_OBS_PLATFORM": "linux",
                    "OBS_WINDOW_CAPTURE_INPUT_KIND": "xshm_input",
                    "OBS_WINDOW_CAPTURE_FAMILY": "xshm",
                    "OBS_WINDOW_AUDIO_SOURCE": "must-not-be-created",
                    "OBS_SOURCE_LOCK_DIR": str(Path(temp_dir) / "source.lock"),
                    "OBS_SOURCE_LOCK_SETTLE_SEC": "0",
                    "FAKE_OBS_TRACE_PATH": str(trace_path),
                    "FAKE_OBS_CONSTRUCT_PATH": str(construct_path),
                    "FAKE_OBS_INPUT_EXISTS": "0" if current_settings is None else "1",
                    "FAKE_OBS_CURRENT_SETTINGS": json.dumps(current_settings or {}),
                }
            )
            env.update(overrides)
            result = subprocess.run(
                [
                    "bash",
                    str(REPO_ROOT / "obs_window_capture_source.sh"),
                    "ensure",
                    "soren",
                    "sorengame",
                    r"Unity WebGL Player \| soren-game",
                ],
                cwd=REPO_ROOT,
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )
            if expect_success:
                result.check_returncode()
            calls = json.loads(trace_path.read_text()) if trace_path.exists() else []
            return result, calls, construct_path.exists()

    def evaluate_stat_fallback(
        self,
        relative_path: str,
        variable: str,
        mode: str,
        search_fragment: str | None = None,
    ) -> tuple[str, list[str]]:
        lines = (REPO_ROOT / relative_path).read_text().splitlines()
        needle = search_fragment or f"{variable}=$(stat -f"
        start = next(
            index for index, line in enumerate(lines)
            if needle in line
        )
        assignment = "\n".join(lines[start:start + 3])

        with tempfile.TemporaryDirectory() as temp_dir:
            fake_bin = Path(temp_dir)
            fake_stat = fake_bin / "stat"
            calls_path = fake_bin / "calls.txt"
            fake_stat.write_text(
                """#!/bin/sh
printf '%s\\n' "$1" >> "$FAKE_STAT_CALLS"
case "$FAKE_STAT_MODE:$1" in
  bsd-success:-f) printf '%s\\n' 111; exit 0 ;;
  gnu-success:-f) printf '%s\\n' poison-from-failed-bsd; exit 7 ;;
  gnu-success:-c) printf '%s\\n' 222; exit 0 ;;
  both-fail:-f) printf '%s\\n' poison-from-failed-bsd; exit 7 ;;
  both-fail:-c) printf '%s\\n' poison-from-failed-gnu; exit 8 ;;
  *) exit 64 ;;
esac
"""
            )
            fake_stat.chmod(0o755)
            env = os.environ.copy()
            env.update(
                {
                    "PATH": str(fake_bin),
                    "FAKE_STAT_MODE": mode,
                    "FAKE_STAT_CALLS": str(calls_path),
                }
            )
            script = "\n".join(
                (
                    "set -eu",
                    "LOCK_DIR=/unused/lock",
                    "OBS_SOURCE_LOCK_DIR=/unused/obs-lock",
                    "GAME_STATE=/unused/game-state",
                    "lock_file=/unused/lock-file",
                    "log_file=/unused/log-file",
                    "state_file=/unused/state-file",
                    "lock_dir=/unused/lock-dir",
                    "lock=/unused/internal-lock",
                    "RUNNER_LOCK_DIR=/unused/runner-lock",
                    "marker=/unused/marker",
                    "orphan=/unused/orphan",
                    "path=/unused/path",
                    "f=/unused/file",
                    "now_ts=777",
                    "now=777",
                    assignment,
                    f'printf "%s" "${{{variable}}}"',
                )
            )
            result = subprocess.run(
                ["/bin/bash", "-c", script],
                cwd=REPO_ROOT,
                env=env,
                text=True,
                capture_output=True,
                check=True,
            )
            calls = calls_path.read_text().splitlines() if calls_path.exists() else []
            return result.stdout, calls

    def test_obs_source_lock_settle_defaults_are_platform_safe(self) -> None:
        lock_path = REPO_ROOT / "lib/obs_source_lock.sh"

        def settle(platform: str, override: str | None = None) -> str:
            env = os.environ.copy()
            env["SOREN_OBS_PLATFORM"] = platform
            if override is None:
                env.pop("OBS_SOURCE_LOCK_SETTLE_SEC", None)
            else:
                env["OBS_SOURCE_LOCK_SETTLE_SEC"] = override
            result = subprocess.run(
                ["bash", "-c", '. "$1"; printf "%s" "$OBS_SOURCE_LOCK_SETTLE_SEC"', "bash", str(lock_path)],
                cwd=REPO_ROOT,
                env=env,
                text=True,
                capture_output=True,
                check=True,
            )
            return result.stdout

        self.assertEqual(settle("linux"), "0")
        self.assertEqual(settle("darwin"), "2")
        self.assertEqual(settle("linux", "7"), "7")

    def test_readme_documents_linux_capture_safety_controls(self) -> None:
        readme = (REPO_ROOT / "README.md").read_text()
        self.assertIn("OBS_WINDOW_CAPTURE_FAMILY", readme)
        self.assertIn("OBS_LINUX_RESTART_MODE=systemd", readme)
        self.assertIn("OBS_XCOMPOSITE_SAME_VALUE_BOUNCE_ENABLED=1", readme)
        self.assertIn("OBS_SOURCE_LOCK_SETTLE_SEC", readme)
        self.assertIn("OBS_WINDOW_CAPTURE_INPUT_KIND=xshm_input", readme)
        self.assertIn("OBS_WINDOW_CAPTURE_FAMILY=xshm", readme)
        self.assertIn("全画面", readme)
        self.assertIn("sourceWidth/sourceHeight=0", readme)
        self.assertIn("scene collection", readme)
        self.assertIn("自動 Remove/Create を拒否", readme)

    def test_all_planned_stat_sites_use_separate_gnu_fallback_assignment(self) -> None:
        files = {
            "twitch_chat.sh": "lock_mtime",
            "youtube_chat.sh": "lock_mtime",
            "lib/obs_source_lock.sh": "mt",
            "monitor_improve_runtime.sh": "mtime",
            "monitor_webfetch_failure.sh": "mt",
        }
        for relative_path, variable in files.items():
            with self.subTest(path=relative_path):
                text = (REPO_ROOT / relative_path).read_text()
                self.assertIn(f"{variable}=$(stat -f", text)
                self.assertIn(f"|| {variable}=$(stat -c", text)
                self.assertNotRegex(text, r"\$\(stat -f [^\n]*\|\| stat -c")

    def test_all_planned_stat_fallbacks_execute_without_mixed_epoch_output(self) -> None:
        sites = {
            "twitch_chat.sh": ("lock_mtime", "777"),
            "youtube_chat.sh": ("lock_mtime", "777"),
            "lib/obs_source_lock.sh": ("mt", "777"),
            "monitor_improve_runtime.sh": ("mtime", "0"),
            "monitor_webfetch_failure.sh": ("mt", "0"),
        }
        scenarios = {
            "bsd-success": ("111", ["-f"]),
            "gnu-success": ("222", ["-f", "-c"]),
        }

        for relative_path, (variable, fallback) in sites.items():
            for mode, (expected_epoch, expected_calls) in scenarios.items():
                with self.subTest(path=relative_path, mode=mode):
                    epoch, calls = self.evaluate_stat_fallback(relative_path, variable, mode)
                    self.assertEqual(epoch, expected_epoch)
                    self.assertEqual(calls, expected_calls)
            with self.subTest(path=relative_path, mode="both-fail"):
                epoch, calls = self.evaluate_stat_fallback(relative_path, variable, "both-fail")
                self.assertEqual(epoch, fallback)
                self.assertEqual(calls, ["-f", "-c"])

    def test_linux_runtime_mtime_sites_execute_without_gnu_stat_poison(self) -> None:
        sites = (
            ("eloop.sh", "_pg_state_mtime0", "_pg_state_mtime0=$(stat -f", "0"),
            ("eloop.sh", "_rr_mt_now", "_rr_mt_now=$(stat -f", "0"),
            ("eloop.sh", "_pg_state_mtime1", "_pg_state_mtime1=$(stat -f", "0"),
            ("start_all.sh", "log_m", "log_m=$(stat -f %m logs/soren_loop.log", "0"),
            ("start_all.sh", "lock_m", 'lock_m=$(stat -f %m "$lock_file"', "0"),
            ("start_all.sh", "log_m", 'log_m=$(stat -f %m "$log_file"', "0"),
            ("start_all.sh", "state_m", 'state_m=$(stat -f %m "$state_file"', "0"),
            ("lib/outbound_queue.sh", "mt", 'mt=$(stat -f %m "$marker"', "777"),
            ("lib/outbound_queue.sh", "mtime", 'mtime=$(stat -f %m "$f"', "777"),
            ("lib/ai_generate.sh", "mt", 'mt=$(stat -f %m "$lock_dir"', "777"),
            ("lib/ai_generate.sh", "mt", 'mt=$(stat -f %m "$lock"', "777"),
            ("soren91/run_player_loop.sh", "mt", 'mt=$(stat -f %m "$RUNNER_LOCK_DIR"', "777"),
            ("core/helpers.sh", "mt", 'mt=$(stat -f %m "$marker"', "0"),
            ("broadcast/comment_lib.sh", "mtime", 'mtime=$(stat -f %m "$orphan"', "777"),
            ("show_status.sh", "say_lock_hb", "say_lock_hb=$(stat -f %m tmp/.say_queue/.lock", "0"),
            ("show_status.sh", "recent_mod", 'recent_mod=$(stat -f %m "$f"', ""),
            ("show_status.sh", "file_mod", 'file_mod=$(stat -f %m "$f"', ""),
            ("core/runtime_toggles.sh", "mtime", 'mtime=$(stat -f %m "$path"', "0"),
        )
        scenarios = {
            "bsd-success": ("111", ["-f"]),
            "gnu-success": ("222", ["-f", "-c"]),
        }
        for relative_path, variable, fragment, fallback in sites:
            source = (REPO_ROOT / relative_path).read_text(encoding="utf-8")
            self.assertNotRegex(source, r"\$\(stat -f %m [^\n]*\|\|")
            for mode, (expected_epoch, expected_calls) in scenarios.items():
                with self.subTest(path=relative_path, fragment=fragment, mode=mode):
                    epoch, calls = self.evaluate_stat_fallback(
                        relative_path, variable, mode, fragment
                    )
                    self.assertEqual(epoch, expected_epoch)
                    self.assertEqual(calls, expected_calls)
            with self.subTest(path=relative_path, fragment=fragment, mode="both-fail"):
                epoch, calls = self.evaluate_stat_fallback(
                    relative_path, variable, "both-fail", fragment
                )
                self.assertEqual(epoch, fallback)
                self.assertEqual(calls, ["-f", "-c"])

    def test_say_enqueue_has_linux_pulseaudio_path(self) -> None:
        text = (REPO_ROOT / "say_enqueue.sh").read_text(encoding="utf-8")
        # EXPLORE_MODE=1 では音声キューに触れず即終了する
        self.assertIn('[ "${EXPLORE_MODE:-0}" = "1" ] && exit 0', text)
        self.assertIn('case "${SOREN_OBS_PLATFORM:-$(uname -s', text)
        self.assertIn('IS_LINUX=1', text)
        # Linux では paplay --device で soren_null へ再生する
        self.assertIn("_linux_play_bg", text)
        self.assertIn('paplay --device="$device"', text)
        self.assertIn('ffplay -nodisp -autoexit -loglevel error -fflags nobuffer "$audio_file"', text)
        self.assertIn('PULSE_LATENCY_MSEC="$pulse_latency"', text)
        # Linux では audiotoolbox / afplay / say の代わりに分岐
        self.assertIn('if [ "$IS_LINUX" = "1" ]; then', text)
        self.assertIn('_launch_say_bg', text)
        self.assertIn("say は macOS 専用のため Linux ではスキップ", text)
        # Linux では say 最終フォールバック全体をスキップ
        self.assertIn("Linux では say フォールバックなし", text)
        # GNU sed 対応（BSD の sed -i '' を Linux で使わない）
        sed_block = text.split('if [ "$WAV_MODE" = "false" ]; then', 1)[1]
        sed_block = sed_block.split("python3 lib/normalize_speech_text.py", 1)[0]
        linux_sed = sed_block.split('if [ "$IS_LINUX" = "1" ]; then', 1)[1]
        linux_sed = linux_sed.split("\n\telse", 1)[0]
        self.assertNotIn("sed -i ''", linux_sed)

    def test_say_enqueue_mtime_fallback_executes_cleanly_on_bsd_and_gnu_stat(self) -> None:
        source = (REPO_ROOT / "say_enqueue.sh").read_text(encoding="utf-8")
        match = re.search(r"(?ms)^_file_mtime_epoch\(\) \{\n.*?^\}\n", source)
        self.assertIsNotNone(match)
        helper = match.group(0)
        # 3 箇所: LOCK_DIR 用・VOICEVOX_SYNTH_LOCK 用・非数値時の再取得フォールバック
        self.assertEqual(source.count('lock_hb=$(_file_mtime_epoch "'), 3)

        scenarios = {
            "bsd-success": ("111", ["-f"]),
            "gnu-success": ("222", ["-f", "-c"]),
            "both-fail": ("0", ["-f", "-c"]),
        }
        for mode, (expected_epoch, expected_calls) in scenarios.items():
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as temp_dir:
                fake_bin = Path(temp_dir)
                calls_path = fake_bin / "calls.txt"
                fake_stat = fake_bin / "stat"
                fake_stat.write_text(
                    """#!/bin/sh
printf '%s\\n' "$1" >> "$FAKE_STAT_CALLS"
case "$FAKE_STAT_MODE:$1" in
  bsd-success:-f) printf '%s\\n' 111; exit 0 ;;
  gnu-success:-f) printf '%s\\n' poison-from-failed-bsd; exit 7 ;;
  gnu-success:-c) printf '%s\\n' 222; exit 0 ;;
  both-fail:-f) printf '%s\\n' poison-from-failed-bsd; exit 7 ;;
  both-fail:-c) printf '%s\\n' poison-from-failed-gnu; exit 8 ;;
  *) exit 64 ;;
esac
""",
                    encoding="utf-8",
                )
                fake_stat.chmod(0o755)
                env = os.environ.copy()
                env.update(
                    {
                        "PATH": str(fake_bin),
                        "FAKE_STAT_MODE": mode,
                        "FAKE_STAT_CALLS": str(calls_path),
                    }
                )
                result = subprocess.run(
                    ["/bin/bash", "-c", helper + "\n_file_mtime_epoch /unused/lock"],
                    cwd=REPO_ROOT,
                    env=env,
                    text=True,
                    capture_output=True,
                    check=True,
                )
                self.assertEqual(result.stdout.strip(), expected_epoch)
                self.assertEqual(calls_path.read_text().splitlines(), expected_calls)

    def test_google_tts_has_linux_playback_path(self) -> None:
        text = (REPO_ROOT / "google_tts.sh").read_text(encoding="utf-8")
        self.assertIn("IS_LINUX=1", text)
        self.assertIn("_play_tts", text)
        self.assertIn('paplay --device="${SAY_AUDIO_DEVICE:-default}"', text)
        # _play_tts 関数の Linux 分岐には afplay を含まない
        play_fn = text.split("_play_tts() {", 1)[1].split("\n}", 1)[0]
        linux_branch = play_fn.split('if [ "$IS_LINUX" = "1" ]; then', 1)[1]
        linux_branch = linux_branch.split("\n\tfi", 1)[0]
        self.assertNotIn("afplay", linux_branch)
        # macOS フォールバックとして afplay は関数末尾に残る
        self.assertIn("afplay", play_fn)
        self.assertIn("_play_tts \"$OUT\"", text)
        self.assertIn("_play_tts \"$OUTPUT\"", text)

    def test_say_enqueue_linux_missing_players_fails_cleanly(self) -> None:
        # Linux で paplay/ffplay が両方ない場合、$! 未定義（unbound variable）を
        # 出さず、明示的な失敗経路（再試行上限 → 非0）へ到達すること。
        # 専用ルートの sibling に sentinel を置き、後始末が共有親（/tmp 等）を
        # 削除しないことを、同じ実処理の終了後に検証する。
        parent = Path(tempfile.mkdtemp(prefix="say-test-root-"))
        sentinel = parent / "sentinel.txt"
        sentinel.write_text("keep me", encoding="utf-8")
        try:
            fake_bin = parent / "bin"
            fake_bin.mkdir()
            for name in (
                "bash", "sh", "sed", "awk", "grep", "cat", "cp", "mkdir", "rm",
                "rmdir", "date", "sleep", "wc", "tr", "kill", "pgrep", "xargs",
                "printf", "ffmpeg", "ffprobe", "curl", "python3", "node", "uname",
                "dirname", "basename", "nohup", "env", "timeout", "head", "tail",
                "sort", "cut", "touch", "stat", "true", "false",
            ):
                real = shutil.which(name)
                if real:
                    os.symlink(real, fake_bin / name)

            content = parent / "say_test.txt"
            content.write_text("テスト", encoding="utf-8")

            env = os.environ.copy()
            env["PATH"] = str(fake_bin)
            env["SOREN_OBS_PLATFORM"] = "linux"
            env["OBS_WEBSOCKET_PORT"] = ""
            env["OBS_WEBSOCKET_PASSWORD"] = ""
            env["SAY_AUDIO_DEVICE"] = "soren_null"
            env["SAY_RETRY_MAX"] = "1"
            env["SAY_RETRY_SLEEP_SEC"] = "0"
            env["LOCK_STALE_SEC"] = "0"
            env["EXPLORE_MODE"] = "0"

            result = subprocess.run(
                ["bash", str(REPO_ROOT / "say_enqueue.sh"), "--wav", str(content), "120", "0"],
                cwd=REPO_ROOT,
                env=env,
                text=True,
                capture_output=True,
                timeout=60,
                check=False,
            )
            combined = result.stdout + result.stderr
            self.assertNotEqual(result.returncode, 0)
            self.assertNotIn("unbound variable", combined)
            self.assertIn("再生プレイヤー起動失敗", combined)
            self.assertIn("say起動失敗", combined)
            # 後始末後も sentinel と parent が残る（共有親を再帰削除していない）
            self.assertTrue(sentinel.is_file())
            self.assertTrue(parent.is_dir())
        finally:
            shutil.rmtree(parent, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
