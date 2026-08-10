import json
import os
from pathlib import Path
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

    def watchdog_check_config(self, platform: str, **overrides: str) -> dict:
        env = os.environ.copy()
        for key in (
            "OBS_WINDOW_CAPTURE_INPUT_KIND",
            "OBS_WINDOW_CAPTURE_FAMILY",
            "OBS_WINDOW_CAPTURE_WINDOW_PROPERTY",
            "OBS_XCOMPOSITE_SAME_VALUE_BOUNCE_ENABLED",
            "OBS_WEBSOCKET_TIMEOUT_MS",
        ):
            env.pop(key, None)
        env.update({"SOREN_OBS_PLATFORM": platform})
        env.update(overrides)
        result = subprocess.run(
            [self.node, str(REPO_ROOT / "obs_capture_watchdog_check.mjs"), "--print-config"],
            cwd=REPO_ROOT,
            env=env,
            text=True,
            capture_output=True,
            check=True,
        )
        return json.loads(result.stdout)

    def watchdog_shell_config(self, platform: str, **overrides: str) -> dict[str, str]:
        env = os.environ.copy()
        for key in (
            "OBS_PROCESS_NAME",
            "OBS_LOG_DIR",
            "OBS_SAFE_MODE_AUTOFIX",
            "OBS_APP_PATH",
            "OBS_LINUX_RESTART_MODE",
            "OBS_DISPLAY",
        ):
            env.pop(key, None)
        env.update({"SOREN_OBS_PLATFORM": platform})
        env.update(overrides)
        result = subprocess.run(
            ["bash", str(REPO_ROOT / "obs_capture_watchdog.sh"), "--print-config"],
            cwd=REPO_ROOT,
            env=env,
            text=True,
            capture_output=True,
            check=True,
        )
        return dict(line.split("=", 1) for line in result.stdout.splitlines())

    def evaluate_stat_fallback(
        self,
        relative_path: str,
        variable: str,
        mode: str,
    ) -> tuple[str, list[str]]:
        lines = (REPO_ROOT / relative_path).read_text().splitlines()
        start = next(
            index for index, line in enumerate(lines)
            if f"{variable}=$(stat -f" in line
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

    def test_window_capture_defaults_to_xcomposite_window_only_on_linux(self) -> None:
        config = self.window_capture_config(
            "linux",
            OBS_WINDOW_CAPTURE_AUDIO="1",
            OBS_WINDOW_AUDIO_SOURCE="legacy-mac-audio",
        )

        self.assertEqual(config["inputKind"], "xcomposite_input")
        self.assertEqual(config["captureFamily"], "xcomposite")
        self.assertEqual(config["windowPropertyName"], "capture_window")
        self.assertEqual(config["initialSettings"], {"capture_window": ""})
        self.assertEqual(config["targetSettings"], {"capture_window": "test-window"})
        self.assertFalse(config["appAudioSupported"])
        self.assertIn("Chromium", config["browserWindowPattern"])

    def test_window_capture_preserves_macos_defaults_and_audio_override(self) -> None:
        config = self.window_capture_config("darwin", OBS_WINDOW_CAPTURE_AUDIO="1")

        self.assertEqual(config["inputKind"], "screen_capture")
        self.assertEqual(config["captureFamily"], "screen_capture")
        self.assertEqual(config["windowPropertyName"], "window")
        self.assertEqual(
            config["targetSettings"],
            {
                "type": 1,
                "application": "com.google.chrome.for.testing",
                "window": "test-window",
                "show_cursor": False,
                "show_empty_names": False,
                "capture_audio": True,
                "audio": True,
            },
        )
        self.assertTrue(config["appAudioSupported"])

    def test_window_capture_supports_xshm_defaults_and_official_settings(self) -> None:
        defaults = self.window_capture_config(
            "linux",
            OBS_WINDOW_CAPTURE_INPUT_KIND="xshm_input",
        )

        self.assertEqual(defaults["captureFamily"], "xshm")
        self.assertTrue(defaults["isXshm"])
        self.assertEqual(defaults["captureMode"], "full_display")
        self.assertFalse(defaults["requiresWindowBinding"])
        self.assertFalse(defaults["appAudioSupported"])
        self.assertEqual(
            defaults["initialSettings"],
            {
                "screen": 0,
                "show_cursor": False,
                "advanced": False,
                "cut_top": 0,
                "cut_left": 0,
                "cut_right": 0,
                "cut_bot": 0,
            },
        )
        self.assertEqual(defaults["targetSettings"], defaults["initialSettings"])

        advanced = self.window_capture_config(
            "darwin",
            OBS_WINDOW_CAPTURE_INPUT_KIND="xshm_input_v2",
            OBS_XSHM_SCREEN="2",
            OBS_XSHM_SHOW_CURSOR="1",
            OBS_XSHM_ADVANCED="true",
            OBS_XSHM_SERVER=":99",
            OBS_XSHM_CUT_TOP="10",
            OBS_XSHM_CUT_LEFT="11",
            OBS_XSHM_CUT_RIGHT="12",
            OBS_XSHM_CUT_BOT="13",
        )
        self.assertEqual(advanced["captureFamily"], "xshm")
        self.assertEqual(
            advanced["initialSettings"],
            {
                "screen": 2,
                "show_cursor": True,
                "advanced": True,
                "server": ":99",
                "cut_top": 10,
                "cut_left": 11,
                "cut_right": 12,
                "cut_bot": 13,
            },
        )

    def test_window_capture_family_only_override_drives_input_kind(self) -> None:
        # A bare OBS_WINDOW_CAPTURE_FAMILY=xshm (without INPUT_KIND) must still
        # create an xshm_input source on Linux; previously it left the platform
        # default xcomposite_input in place while treating the source as XSHM.
        xshm = self.window_capture_config(
            "linux",
            OBS_WINDOW_CAPTURE_FAMILY="xshm",
        )
        self.assertEqual(xshm["captureFamily"], "xshm")
        self.assertEqual(xshm["inputKind"], "xshm_input")
        self.assertTrue(xshm["isXshm"])
        self.assertEqual(xshm["captureMode"], "full_display")
        self.assertFalse(xshm["requiresWindowBinding"])

        xcomposite = self.window_capture_config(
            "linux",
            OBS_WINDOW_CAPTURE_FAMILY="xcomposite",
        )
        self.assertEqual(xcomposite["captureFamily"], "xcomposite")
        self.assertEqual(xcomposite["inputKind"], "xcomposite_input")
        self.assertTrue(xcomposite["isXComposite"])

        # Explicit INPUT_KIND still wins over the family-derived kind.
        explicit = self.window_capture_config(
            "linux",
            OBS_WINDOW_CAPTURE_FAMILY="xshm",
            OBS_WINDOW_CAPTURE_INPUT_KIND="xshm_input_v2",
        )
        self.assertEqual(explicit["captureFamily"], "xshm")
        self.assertEqual(explicit["inputKind"], "xshm_input_v2")

    def test_xshm_ensure_is_idempotent_and_updates_only_changed_settings(self) -> None:
        desired = {
            "screen": 0,
            "show_cursor": False,
            "advanced": False,
            "cut_top": 0,
            "cut_left": 0,
            "cut_right": 0,
            "cut_bot": 0,
        }

        result, unchanged_calls, _ = self.window_capture_requests(desired)
        self.assertIn("xshm-screen-capture:sorengame:show:display=0:unchanged", result.stdout)
        self.assertFalse(any(call["requestType"] == "SetInputSettings" for call in unchanged_calls))
        self.assertFalse(
            any(call["requestType"] == "GetInputPropertiesListPropertyItems" for call in unchanged_calls)
        )
        self.assertFalse(
            any(
                call["requestType"] == "CreateInput"
                and call["requestData"].get("inputKind") == "sck_audio_capture"
                for call in unchanged_calls
            )
        )

        result, defaulted_calls, _ = self.window_capture_requests({})
        defaulted_updates = [
            call for call in defaulted_calls if call["requestType"] == "SetInputSettings"
        ]
        self.assertIn("xshm-screen-capture:sorengame:show:display=0:updated", result.stdout)
        self.assertEqual(len(defaulted_updates), 1)
        self.assertEqual(defaulted_updates[0]["requestData"]["inputSettings"], desired)
        self.assertFalse(defaulted_updates[0]["requestData"]["overlay"])

        result, changed_calls, _ = self.window_capture_requests(
            {**desired, "cut_left": 99},
            OBS_XSHM_SCREEN="1",
            OBS_XSHM_CUT_LEFT="4",
        )
        updates = [call for call in changed_calls if call["requestType"] == "SetInputSettings"]
        self.assertIn("xshm-screen-capture:sorengame:show:display=1:updated", result.stdout)
        self.assertEqual(len(updates), 1)
        self.assertFalse(updates[0]["requestData"]["overlay"])
        self.assertEqual(
            updates[0]["requestData"]["inputSettings"],
            {**desired, "screen": 1, "cut_left": 4},
        )
        self.assertFalse(
            any(call["requestType"] == "GetInputPropertiesListPropertyItems" for call in changed_calls)
        )

        result, created_calls, _ = self.window_capture_requests(None)
        creates = [call for call in created_calls if call["requestType"] == "CreateInput"]
        self.assertIn("xshm-screen-capture:sorengame:show:display=0:created", result.stdout)
        self.assertEqual(len(creates), 1)
        self.assertEqual(creates[0]["requestData"]["inputSettings"], desired)
        self.assertFalse(any(call["requestType"] == "SetInputSettings" for call in created_calls))

    def test_xshm_migration_refuses_destructive_primary_source_replacement(self) -> None:
        result, calls, websocket_created = self.window_capture_requests(
            {},
            expect_success=False,
            FAKE_OBS_EXISTING_INPUT_KIND="xcomposite_input",
            FAKE_OBS_EXISTING_UNVERSIONED_INPUT_KIND="xcomposite_input",
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("refusing to auto-replace", result.stderr)
        self.assertIn("discard OBS filters/transform", result.stderr)
        self.assertIn("recreate sorengame as xshm_input", result.stderr)
        self.assertTrue(websocket_created)
        self.assertFalse(any(call["requestType"] == "RemoveInput" for call in calls))
        self.assertFalse(any(call["requestType"] == "CreateInput" for call in calls))

    def test_xshm_invalid_explicit_settings_fail_without_obs_mutation(self) -> None:
        invalid_settings = (
            ("OBS_XSHM_SHOW_CURSOR", "maybe"),
            ("OBS_XSHM_ADVANCED", "sometimes"),
            ("OBS_XSHM_SCREEN", "-1"),
            ("OBS_XSHM_SCREEN", "1.5"),
            ("OBS_XSHM_CUT_TOP", "-4097"),
            ("OBS_XSHM_CUT_LEFT", "4097"),
            ("OBS_XSHM_CUT_RIGHT", "-1"),
            ("OBS_XSHM_CUT_BOT", "4097"),
        )
        mutating_requests = {
            "CreateInput",
            "RemoveInput",
            "CreateSceneItem",
            "SetInputSettings",
            "SetInputMute",
            "SetSceneItemEnabled",
            "SetSceneItemIndex",
        }

        for key, value in invalid_settings:
            with self.subTest(key=key, value=value):
                result, calls, websocket_created = self.window_capture_requests(
                    {},
                    expect_success=False,
                    **{key: value},
                )
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(key, result.stderr)
                self.assertFalse(websocket_created)
                self.assertFalse(
                    any(call["requestType"] in mutating_requests for call in calls),
                    calls,
                )

    def test_xcomposite_ensure_updates_only_new_or_changed_encoded_binding(self) -> None:
        encoded = "41943043\r\nUnity WebGL Player | soren-game - Chromium\r\nchromium"
        xcomposite_env = {
            "OBS_WINDOW_CAPTURE_INPUT_KIND": "xcomposite_input",
            "OBS_WINDOW_CAPTURE_FAMILY": "xcomposite",
            "FAKE_OBS_WINDOW_ITEM_VALUE": encoded,
        }

        _, unchanged_calls, _ = self.window_capture_requests(
            {"capture_window": encoded},
            **xcomposite_env,
        )
        self.assertEqual(
            [call["requestType"] for call in unchanged_calls].count("GetInputSettings"),
            1,
        )
        self.assertFalse(any(call["requestType"] == "SetInputSettings" for call in unchanged_calls))

        _, changed_calls, _ = self.window_capture_requests(
            {"capture_window": "old\r\nwindow\r\nchromium"},
            **xcomposite_env,
        )
        changed_updates = [
            call for call in changed_calls if call["requestType"] == "SetInputSettings"
        ]
        self.assertEqual(len(changed_updates), 1)
        self.assertEqual(
            changed_updates[0]["requestData"],
            {
                "inputName": "sorengame",
                "inputSettings": {"capture_window": encoded},
                "overlay": True,
            },
        )

        _, created_calls, _ = self.window_capture_requests(None, **xcomposite_env)
        creates = [call for call in created_calls if call["requestType"] == "CreateInput"]
        updates = [call for call in created_calls if call["requestType"] == "SetInputSettings"]
        self.assertEqual(len(creates), 1)
        self.assertEqual(creates[0]["requestData"]["inputSettings"], {"capture_window": ""})
        self.assertEqual(len(updates), 1)
        self.assertEqual(updates[0]["requestData"]["inputSettings"], {"capture_window": encoded})
        self.assertTrue(updates[0]["requestData"]["overlay"])

    def test_window_capture_kind_family_and_property_overrides_are_independent(self) -> None:
        versioned = self.window_capture_config(
            "darwin",
            OBS_WINDOW_CAPTURE_INPUT_KIND="xcomposite_input_v2",
            OBS_WINDOW_CAPTURE_WINDOW_PROPERTY="vm_window",
            OBS_WINDOW_CAPTURE_BROWSER_REGEX="Chrome|Chromium",
        )

        self.assertEqual(versioned["inputKind"], "xcomposite_input_v2")
        self.assertEqual(versioned["captureFamily"], "xcomposite")
        self.assertEqual(versioned["windowPropertyName"], "vm_window")
        self.assertEqual(versioned["targetSettings"], {"vm_window": "test-window"})
        self.assertEqual(versioned["browserWindowPattern"], "Chrome|Chromium")

        custom = self.window_capture_config(
            "linux",
            OBS_WINDOW_CAPTURE_INPUT_KIND="vendor_capture",
            OBS_WINDOW_CAPTURE_WINDOW_PROPERTY="vendor_window",
        )
        self.assertEqual(custom["captureFamily"], "xcomposite")
        self.assertEqual(custom["targetSettings"], {"vendor_window": "test-window"})

        forced_family = self.window_capture_config(
            "darwin",
            OBS_WINDOW_CAPTURE_INPUT_KIND="vendor_capture",
            OBS_WINDOW_CAPTURE_FAMILY="xcomposite",
            OBS_WINDOW_CAPTURE_WINDOW_PROPERTY="vendor_window",
        )
        self.assertEqual(forced_family["captureFamily"], "xcomposite")
        self.assertEqual(forced_family["targetSettings"], {"vendor_window": "test-window"})

        property_only = self.window_capture_config(
            "darwin",
            OBS_WINDOW_CAPTURE_WINDOW_PROPERTY="screen_window_v2",
        )
        self.assertEqual(property_only["captureFamily"], "screen_capture")
        self.assertEqual(property_only["targetSettings"]["screen_window_v2"], "test-window")
        self.assertNotIn("window", property_only["targetSettings"])

        explicit_screen = self.window_capture_config(
            "linux",
            OBS_WINDOW_CAPTURE_INPUT_KIND="screen_capture",
            OBS_WINDOW_CAPTURE_WINDOW_PROPERTY="linux_screen_window",
        )
        self.assertEqual(explicit_screen["captureFamily"], "screen_capture")
        self.assertEqual(
            explicit_screen["targetSettings"]["linux_screen_window"],
            "test-window",
        )
        self.assertTrue(explicit_screen["appAudioSupported"])

    def test_watchdog_check_uses_safe_linux_recovery_defaults(self) -> None:
        config = self.watchdog_check_config("linux", OBS_WEBSOCKET_TIMEOUT_MS="4321")

        self.assertEqual(config["inputKind"], "xcomposite_input")
        self.assertEqual(config["captureFamily"], "xcomposite")
        self.assertEqual(config["windowProperty"], "capture_window")
        self.assertTrue(config["obsLogDir"].endswith("/.config/obs-studio/logs"))
        self.assertEqual(config["requestTimeoutMs"], 4321)
        self.assertFalse(config["xcompositeSameValueBounceEnabled"])
        self.assertEqual(len(config["staleRecoveryPlan"]), 1)
        stale = config["staleRecoveryPlan"][0]["requestData"]
        self.assertEqual(stale["inputSettings"], {"capture_window": "test-window"})
        self.assertTrue(stale["overlay"])
        self.assertEqual(config["frozenRecoveryPlan"], [])

    def test_watchdog_check_preserves_macos_bounce_and_log_path(self) -> None:
        config = self.watchdog_check_config("darwin")

        self.assertEqual(config["inputKind"], "screen_capture")
        self.assertEqual(config["captureFamily"], "screen_capture")
        self.assertEqual(config["windowProperty"], "window")
        self.assertTrue(config["obsLogDir"].endswith("/Library/Application Support/obs-studio/logs"))
        self.assertEqual(len(config["staleRecoveryPlan"]), 2)
        first = config["staleRecoveryPlan"][0]["requestData"]
        second = config["staleRecoveryPlan"][1]["requestData"]
        self.assertFalse(first["overlay"])
        self.assertFalse(second["overlay"])
        self.assertEqual(first["inputSettings"]["type"], 2)
        self.assertEqual(second["inputSettings"]["window"], "test-window")

    def test_watchdog_check_xshm_config_is_screenshot_only_and_mutation_free(self) -> None:
        config = self.watchdog_check_config(
            "linux",
            OBS_WINDOW_CAPTURE_INPUT_KIND="xshm_input",
            OBS_WINDOW_CAPTURE_FAMILY="xshm",
        )

        self.assertEqual(config["captureFamily"], "xshm")
        self.assertTrue(config["isXshm"])
        self.assertFalse(config["bindingValidationEnabled"])
        self.assertEqual(config["captureCheckAction"], "screenshot_only_no_mutation")
        self.assertEqual(config["staleRecoveryPlan"], [])
        self.assertEqual(config["frozenRecoveryPlan"], [])

    def test_watchdog_honors_explicit_screen_capture_kind_on_linux(self) -> None:
        config = self.watchdog_check_config(
            "linux",
            OBS_WINDOW_CAPTURE_INPUT_KIND="screen_capture",
            OBS_WINDOW_CAPTURE_WINDOW_PROPERTY="linux_screen_window",
        )

        self.assertEqual(config["captureFamily"], "screen_capture")
        self.assertTrue(config["bindingValidationEnabled"])
        self.assertEqual(config["windowProperty"], "linux_screen_window")
        self.assertEqual(len(config["staleRecoveryPlan"]), 2)
        self.assertEqual(
            config["staleRecoveryPlan"][1]["requestData"]["inputSettings"]["linux_screen_window"],
            "test-window",
        )

    def test_watchdog_shell_linux_defaults_and_explicit_overrides(self) -> None:
        defaults = self.watchdog_shell_config("linux", OBS_DISPLAY=":99")
        self.assertEqual(defaults["process_name"], "obs")
        self.assertEqual(defaults["safe_mode_autofix"], "0")
        self.assertTrue(defaults["log_dir"].endswith("/.config/obs-studio/logs"))
        self.assertEqual(defaults["restart_mode"], "systemd")
        self.assertEqual(defaults["display"], ":99")

        overridden = self.watchdog_shell_config(
            "linux",
            OBS_PROCESS_NAME="custom-obs",
            OBS_LOG_DIR="/tmp/custom-obs-logs",
            OBS_SAFE_MODE_AUTOFIX="1",
            OBS_APP_PATH="/opt/obs/bin/obs",
            OBS_LINUX_RESTART_MODE="systemd",
            OBS_DISPLAY=":100",
        )
        self.assertEqual(overridden["process_name"], "custom-obs")
        self.assertEqual(overridden["log_dir"], "/tmp/custom-obs-logs")
        self.assertEqual(overridden["safe_mode_autofix"], "1")
        self.assertEqual(overridden["app_path"], "/opt/obs/bin/obs")
        self.assertEqual(overridden["restart_mode"], "systemd")
        self.assertEqual(overridden["display"], ":100")

        darwin = self.watchdog_shell_config("darwin")
        self.assertEqual(darwin["process_name"], "OBS")
        self.assertEqual(darwin["safe_mode_autofix"], "1")
        self.assertEqual(darwin["app_path"], "/Applications/OBS.app")
        self.assertTrue(darwin["log_dir"].endswith("/Library/Application Support/obs-studio/logs"))

    def test_linux_relaunch_only_touches_active_named_systemd_unit(self) -> None:
        watchdog = (REPO_ROOT / "obs_capture_watchdog.sh").read_text()

        # systemd scope (user/system) を選択可能にし、スコープごとに unit を検証してから再起動
        self.assertIn('OBS_SYSTEMD_SCOPE="${OBS_SYSTEMD_SCOPE:-user}"', watchdog)
        self.assertIn('systemctl --"$OBS_SYSTEMD_SCOPE" restart "$OBS_SYSTEMD_UNIT"', watchdog)
        self.assertIn('systemctl --"$OBS_SYSTEMD_SCOPE" is-active --quiet "$OBS_SYSTEMD_UNIT"', watchdog)
        self.assertIn('case "$OBS_SYSTEMD_SCOPE" in', watchdog)
        self.assertIn("user|system", watchdog)
        self.assertNotIn('DISPLAY="$OBS_DISPLAY" nohup "$OBS_APP_PATH"', watchdog)
        self.assertNotIn('pkill -x "$OBS_PROCESS_NAME"', watchdog)
        self.assertNotIn("OBS_LINUX_RESTART_MODE:-auto", watchdog)
        self.assertIn('if [ "$OBS_CAPTURE_PLATFORM" = "linux" ]', watchdog)

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
            "obs_capture_watchdog.sh": "mt",
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
            "obs_capture_watchdog.sh": ("mt", "0"),
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

    def test_say_enqueue_has_linux_pulseaudio_path(self) -> None:
        text = (REPO_ROOT / "say_enqueue.sh").read_text(encoding="utf-8")
        # EXPLORE_MODE=1 では音声キューに触れず即終了する
        self.assertIn('[ "${EXPLORE_MODE:-0}" = "1" ] && exit 0', text)
        self.assertIn('case "${SOREN_OBS_PLATFORM:-$(uname -s', text)
        self.assertIn('IS_LINUX=1', text)
        # Linux では paplay --device で soren_null へ再生する
        self.assertIn("_linux_play_bg", text)
        self.assertIn('paplay --device="$device"', text)
        self.assertIn('ffplay -nodisp -autoexit -loglevel error "$audio_file"', text)
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
        fake_bin = Path(tempfile.mkdtemp(prefix="say-fake-bin-"))
        try:
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

            content = fake_bin.parent / "say_test.txt"
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
        finally:
            shutil.rmtree(fake_bin.parent, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
