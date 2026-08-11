import json
import os
from pathlib import Path
import subprocess
import unittest


REPO_ROOT = Path(__file__).resolve().parents[1]
INSTALLER = REPO_ROOT / "install_direct_stream_relay.sh"


class DirectStreamRelayTests(unittest.TestCase):
    def test_relay_config_is_loopback_only_and_has_no_destination_secret(self) -> None:
        config = (REPO_ROOT / "deploy" / "soren-rtmp" / "nginx.conf").read_text(encoding="utf-8")
        self.assertIn("listen 127.0.0.1:1935;", config)
        self.assertNotIn("listen 1935;", config)
        self.assertIn("allow publish 127.0.0.1;", config)
        self.assertIn("deny publish all;", config)
        self.assertIn("include /etc/soren-rtmp/push.conf;", config)
        self.assertIn("push_reconnect 3s;", config)
        self.assertIn("http {\n    access_log off;\n}", config)
        self.assertIn("rtmp {\n    # nginx-rtmp has its own access-log module", config)
        self.assertEqual(config.count("access_log off;"), 2)
        self.assertNotIn("listen 80", config)
        self.assertNotIn("live.twitch", config)
        self.assertNotIn("youtube", config.lower())

    def test_service_is_unprivileged_and_hardened(self) -> None:
        unit = (REPO_ROOT / "deploy" / "soren-rtmp" / "soren-rtmp-relay.service").read_text(encoding="utf-8")
        self.assertIn("User=soren-relay", unit)
        self.assertIn("NoNewPrivileges=true", unit)
        self.assertIn("ProtectSystem=strict", unit)
        self.assertIn("CapabilityBoundingSet=", unit)
        self.assertIn("Restart=on-failure", unit)
        self.assertIn('-g "daemon off;"', unit)
        self.assertNotIn("daemon\\ off", unit)

    def test_print_config_is_non_mutating_and_redacted(self) -> None:
        result = subprocess.run(
            ["bash", str(INSTALLER), "--print-config"],
            cwd=REPO_ROOT,
            env=os.environ.copy(),
            text=True,
            capture_output=True,
            check=True,
        )
        config = json.loads(result.stdout)
        self.assertEqual(config["listen"], "127.0.0.1:1935")
        self.assertFalse(config["destination_credentials_in_soren_env"])
        self.assertFalse(config["destination_credentials_in_process_args"])
        self.assertNotIn("rtmp://", result.stdout)

    def test_install_requires_explicit_confirmation_before_platform_or_sudo_checks(self) -> None:
        result = subprocess.run(
            ["bash", str(INSTALLER), "--install"],
            cwd=REPO_ROOT,
            env=os.environ.copy(),
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("--confirm-package-install", result.stderr)

    def test_installer_precreates_the_runtime_dir_for_nginx_test(self) -> None:
        installer = INSTALLER.read_text(encoding="utf-8")
        create = "sudo install -d -o soren-relay -g soren-relay -m 0750 /run/soren-rtmp-relay"
        validate = 'sudo -u soren-relay /usr/sbin/nginx -t -c "$CONFIG_TARGET"'
        self.assertIn(create, installer)
        self.assertIn(validate, installer)
        self.assertLess(installer.index(create), installer.index(validate))


if __name__ == "__main__":
    unittest.main()
