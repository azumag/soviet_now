import os
from pathlib import Path
import signal
import subprocess
import tempfile
import time
import unittest


class HeartbeatLifecycle(unittest.TestCase):
    def test_parent_death_stops_pid_writes(self):
        source = (Path(__file__).resolve().parents[1] / 'improve_daemon.sh').read_text()
        function = source.split('_start_pid_heartbeat() {', 1)[1].split('\n}\n', 1)[0]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'pid'
            script = '_start_pid_heartbeat() {' + function + '\n}\n_start_pid_heartbeat\nwait\n'
            env = dict(os.environ, IMPROVE_DAEMON_PID_FILE=str(path), WORKER_PID_HEARTBEAT_INTERVAL='0.05')
            parent = subprocess.Popen(['bash', '-c', script], env=env, start_new_session=True)
            try:
                deadline = time.monotonic() + 3
                while not path.exists() and time.monotonic() < deadline:
                    time.sleep(.02)
                self.assertTrue(path.exists())
                self.assertEqual(path.read_text().strip(), str(parent.pid))
                before = path.stat().st_mtime_ns
                time.sleep(.15)
                self.assertGreater(path.stat().st_mtime_ns, before)
                parent.kill()
                parent.wait(timeout=3)
                time.sleep(.2)
                after = path.stat().st_mtime_ns
                time.sleep(.2)
                self.assertEqual(path.stat().st_mtime_ns, after, 'orphan keeps publishing dead parent PID')
            finally:
                try:
                    os.killpg(parent.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                parent.wait(timeout=3)
