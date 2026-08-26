import json
import os
from pathlib import Path
import subprocess
import unittest


REPO_ROOT = Path(__file__).resolve().parents[1]
INSTALLER = REPO_ROOT / "install_rtmps_bridge.sh"
TEMPLATE = REPO_ROOT / "deploy" / "soren-rtmp" / "rtmps-bridge.conf.template"
UNIT = REPO_ROOT / "deploy" / "soren-rtmp" / "soren-rtmps-bridge.service"


class RtmpsBridgeTests(unittest.TestCase):
    def test_template_is_loopback_only_and_verifies_tls(self) -> None:
        template = TEMPLATE.read_text(encoding="utf-8")
        self.assertIn("client = yes", template)
        self.assertIn("accept = 127.0.0.1:__LOCAL_PORT__", template)
        self.assertIn("connect = __REMOTE_HOST__:__REMOTE_PORT__", template)
        # Without SNI and host checking the tunnel would accept any valid cert.
        self.assertIn("sni = __REMOTE_HOST__", template)
        self.assertIn("checkHost = __REMOTE_HOST__", template)
        self.assertIn("verifyChain = yes", template)
        # A live publish is one long session; an idle/close timeout would cut it.
        self.assertIn("TIMEOUTclose = 0", template)

    def test_template_carries_no_destination_or_credential(self) -> None:
        template = TEMPLATE.read_text(encoding="utf-8")
        self.assertNotIn("live-video.net", template)
        self.assertNotIn("kick.com", template)
        self.assertNotIn("rtmp://", template)
        self.assertNotIn("sk_", template)

    def test_service_is_unprivileged_and_hardened(self) -> None:
        unit = UNIT.read_text(encoding="utf-8")
        self.assertIn("User=soren-relay", unit)
        self.assertIn("NoNewPrivileges=true", unit)
        self.assertIn("ProtectSystem=strict", unit)
        self.assertIn("CapabilityBoundingSet=", unit)
        # The relay pushes into this tunnel, so it has to be up first.
        self.assertIn("Before=soren-rtmp-relay.service", unit)
        self.assertIn("Restart=always", unit)

    def test_print_config_is_non_mutating_and_carries_no_key(self) -> None:
        result = subprocess.run(
            ["bash", str(INSTALLER), "--print-config"],
            cwd=REPO_ROOT,
            env=os.environ.copy(),
            text=True,
            capture_output=True,
            check=True,
        )
        config = json.loads(result.stdout)
        self.assertTrue(config["listen"].startswith("127.0.0.1:"))
        self.assertEqual(config["service_user"], "soren-relay")
        self.assertFalse(config["stream_keys_in_bridge_config"])
        self.assertFalse(config["stream_keys_in_process_args"])

    def test_install_requires_explicit_confirmation(self) -> None:
        result = subprocess.run(
            ["bash", str(INSTALLER), "--install", "--host", "example.invalid"],
            cwd=REPO_ROOT,
            env=os.environ.copy(),
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("--confirm-package-install", result.stderr)

    def test_install_requires_a_host(self) -> None:
        result = subprocess.run(
            ["bash", str(INSTALLER), "--install", "--confirm-package-install"],
            cwd=REPO_ROOT,
            env=os.environ.copy(),
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("--host", result.stderr)

    def test_install_rejects_a_host_that_could_inject_config(self) -> None:
        for bad in ("evil.example|command", "a b", "$(id)", "-flag"):
            with self.subTest(host=bad):
                result = subprocess.run(
                    ["bash", str(INSTALLER), "--install", "--host", bad, "--confirm-package-install"],
                    cwd=REPO_ROOT,
                    env=os.environ.copy(),
                    text=True,
                    capture_output=True,
                    check=False,
                )
                self.assertEqual(result.returncode, 2)
                self.assertIn("invalid ingest host", result.stderr)

    def test_install_rejects_an_out_of_range_port(self) -> None:
        result = subprocess.run(
            [
                "bash", str(INSTALLER), "--install",
                "--host", "example.invalid", "--local-port", "99999",
                "--confirm-package-install",
            ],
            cwd=REPO_ROOT,
            env=os.environ.copy(),
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("port out of range", result.stderr)


if __name__ == "__main__":
    unittest.main()
