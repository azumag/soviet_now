import os
import stat
import subprocess
import tempfile
from pathlib import Path
import unittest


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "install_pulseaudio_user_service.sh"


class PulseAudioUserServiceTests(unittest.TestCase):
    def make_stubs(self, directory: Path) -> Path:
        bin_dir = directory / "bin"
        bin_dir.mkdir()
        stubs = {
            "systemctl": """#!/bin/bash
case "$*" in
  "--user is-enabled pulseaudio.socket") echo enabled ;;
  "--user is-active pulseaudio.socket") echo active ;;
  "--user is-enabled pulseaudio.service") echo enabled ;;
  "--user is-active pulseaudio.service") echo active ;;
  "--user show -p ControlGroup --value pulseaudio.service")
    echo /user.slice/user-1001.slice/user@1001.service/session.slice/pulseaudio.service ;;
  *) exit 1 ;;
esac
""",
            "loginctl": """#!/bin/bash
echo yes
""",
            "pactl": """#!/bin/bash
exit 0
""",
        }
        for name, body in stubs.items():
            path = bin_dir / name
            path.write_text(body, encoding="utf-8")
            path.chmod(path.stat().st_mode | stat.S_IXUSR)
        return bin_dir

    def run_script(self, directory: Path, *args: str) -> subprocess.CompletedProcess[str]:
        bin_dir = self.make_stubs(directory)
        client_conf = directory / "client.conf"
        client_conf.write_text(
            "autospawn = yes\ndefault-server = unix:/tmp/pulse-native\n",
            encoding="utf-8",
        )
        env = {
            "PATH": f"{bin_dir}:/usr/bin:/bin",
            "HOME": str(directory),
            "SOREN_PULSE_CLIENT_CONF": str(client_conf),
        }
        return subprocess.run(
            ["bash", str(SCRIPT), *args],
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )

    def test_status_reports_socket_ownership_and_client_conf(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            result = self.run_script(Path(temp), "--status")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("client_conf_autospawn=yes", result.stdout)
        self.assertIn("client_conf_default_server=unix:/tmp/pulse-native", result.stdout)
        self.assertIn("socket=/run/user/1001/pulse/native", result.stdout)
        self.assertIn("socket_unit_enabled=enabled", result.stdout)
        self.assertIn("server_unit_active=active", result.stdout)
        self.assertIn(
            "server_unit_cgroup=/user.slice/user-1001.slice/user@1001.service/"
            "session.slice/pulseaudio.service",
            result.stdout,
        )
        self.assertIn("server_reachable=yes", result.stdout)

    def test_install_requires_the_explicit_audio_confirmation(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            result = self.run_script(Path(temp), "--install")
            self.assertEqual(result.returncode, 2)
            self.assertIn("--confirm-audio-session", result.stderr)
            client_conf = Path(temp) / "client.conf"
            self.assertEqual(
                client_conf.read_text(encoding="utf-8"),
                "autospawn = yes\ndefault-server = unix:/tmp/pulse-native\n",
            )

    def test_install_refuses_a_non_runtime_identity_before_writing(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            result = self.run_script(
                Path(temp), "--install", "--confirm-audio-session"
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("pulseaudio user service:", result.stderr)
            client_conf = Path(temp) / "client.conf"
            self.assertEqual(
                client_conf.read_text(encoding="utf-8"),
                "autospawn = yes\ndefault-server = unix:/tmp/pulse-native\n",
            )

    def test_unknown_arguments_print_usage(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            result = self.run_script(Path(temp), "--restart")
        self.assertEqual(result.returncode, 2)
        self.assertIn("Usage:", result.stderr)


if __name__ == "__main__":
    unittest.main()
