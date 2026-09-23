import importlib.util
import json
import os
import shutil
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('fence', ROOT / 'lib/hanjuku_audio_fence.py')
fence = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(fence)


class HanjukuAudioFenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='hanjuku-audio-fence-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.state = self.root / 'state'
        (self.state / 'locks').mkdir(parents=True)
        (self.state / 'locks/game-switch.lock').touch()
        self.identity = dict(game='hanjuku-hero', runtime_id='g3-abcdef', generation=3,
                             lease_id='lease-3', expires_at=time.time() + 30)
        self.canonical = self.state / 'game_switch.json'
        self.run = self.state / 'runtimes/g3-abcdef/hanjuku_run.json'
        self.run.parent.mkdir(parents=True)
        self.set_state()
        self.target = self.root / 'comment_announce_1_hanjuku_commentary.txt'
        self.target.write_text('戦闘状況を確認しています。')
        fence.sidecar(self.target).write_text(json.dumps(self.identity))
        (self.root / 'tmp/.say_queue').mkdir(parents=True)
        self.env = dict(os.environ, ELOOP_LIB_DIR=str(ROOT),
                        SOREN_ACTIVE_GAME_CONTEXT_FILE=str(self.canonical),
                        OUTBOUND_CHAT_QUEUE_DIR=str(self.root / 'outbound'),
                        COMMENT_QUEUE_DIR=str(self.root / 'queue'),
                        COMMENT_AUDIO_DEDUP_TTL_SEC='0')
        (self.root / 'queue').mkdir()

    def set_state(self, *, lease=None, terminal=None, phase='ready'):
        active = {key: self.identity[key] for key in fence.KEYS}
        if lease:
            active['lease_id'] = lease
        self.canonical.write_text(json.dumps(dict(phase=phase, active=active)))
        self.run.write_text(json.dumps(dict(active, playing=True, terminal_reason=terminal)))

    def shell(self, script, *args):
        return subprocess.run(['bash', '-c', script, '_', *map(str, args)],
                              cwd=self.root, env=self.env, capture_output=True, text=True, timeout=8)

    def test_active_and_ordinary_items_remain_compatible(self):
        fence.check(self.canonical, self.target)
        fence.check(self.root / 'missing', self.root / 'ordinary.txt')
        result = self.shell('source "$1/lib/outbound_queue.sh"; enqueue_audio_text ordinary old_source 1', ROOT)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(list((self.root / 'queue').glob('*.txt'))), 1)
        self.assertEqual(len(list((self.root / 'queue').glob('*.json'))), 0)

    def test_identity_terminal_corruption_and_expiry_fail_closed(self):
        for change in ({'lease_id': 'other'}, {'runtime_id': 'g4-aaaaaa'}, {'generation': 4},
                       {'expires_at': time.time() - 1}, {'expires_at': float('nan')},
                       {'generation': True}, {'runtime_id': '../../elsewhere'}):
            with self.subTest(change=change):
                fence.sidecar(self.target).write_text(json.dumps(dict(self.identity, **change)))
                with self.assertRaises((ValueError, OSError)):
                    fence.check(self.canonical, self.target)
        fence.sidecar(self.target).write_text(json.dumps(self.identity))
        for reason in ('game_over', 'screen_stalled', 'unknown_terminal'):
            self.set_state(terminal=reason)
            with self.assertRaises(ValueError):
                fence.check(self.canonical, self.target)
        self.set_state()
        run = json.loads(self.run.read_text())
        run['terminal_candidate'] = True
        self.run.write_text(json.dumps(run))
        with self.assertRaises(ValueError):
            fence.check(self.canonical, self.target)
        self.set_state(phase='draining')
        with self.assertRaises(ValueError):
            fence.check(self.canonical, self.target)
        fence.sidecar(self.target).unlink()
        with self.assertRaises(OSError):
            fence.check(self.canonical, self.target)

    def test_locked_transition_fails_without_wait(self):
        import fcntl
        with (self.state / 'locks/game-switch.lock').open('rb') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            started = time.monotonic()
            with self.assertRaises(BlockingIOError):
                fence.check(self.canonical, self.target)
            self.assertLess(time.monotonic() - started, .2)

    def test_enqueue_contract_publishes_and_claim_cleanup_removes_sidecar(self):
        result = self.shell('source "$1/lib/outbound_queue.sh"; enqueue_audio_text text hanjuku_commentary 7 "$2"', ROOT, json.dumps(self.identity))
        self.assertEqual(result.returncode, 0, result.stderr)
        target, = (self.root / 'queue').glob('*.txt')
        self.assertEqual(json.loads(fence.sidecar(target).read_text()), self.identity)
        self.set_state(lease='new-lease')
        result = self.consume_queue()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(target.exists())
        self.assertFalse(fence.sidecar(target).exists())
        self.assertFalse(Path(str(target) + '.speaker').exists())
        self.assertFalse((self.root / 'spoken').exists())

    def consume_queue(self, after_claim=''):
        say = self.root / 'say_enqueue.sh'
        say.write_text('#!/bin/bash\ntouch spoken\n')
        say.chmod(0o755)
        return self.shell('''source "$1/broadcast/comment_lib.sh"
COMMENT_PLAYED_HASHES_FILE=hashes
RADIO_SAY_RATE=120
_cp_my_pid=$$
_broadcast_read_expected_mode() { :; }
_broadcast_host_mode() { echo main; }
_broadcast_clear_expected_mode() { :; }
_comment_clear_generation_meta() { :; }
_comment_meta_sidecar_path() { echo nonexistent; }
_comment_has_bilingual_speech() { return 1; }
_comment_declares_bilingual_speech() { return 1; }
_comment_generation_debug_summary() { %s :; }
_remember_spoken_comment() { :; }
_play_deferred_radio_queue_once() { :; }
_play_comment_queue
''' % after_claim, ROOT)

    def test_active_queue_plays_once_and_cleans_fence(self):
        queue_target = self.root / 'queue' / self.target.name
        self.target.rename(queue_target)
        fence.sidecar(self.target).rename(fence.sidecar(queue_target))
        result = self.consume_queue()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((self.root / 'spoken').exists())
        self.assertFalse(fence.sidecar(queue_target).exists())
        self.assertFalse(queue_target.exists())

    def test_claim_rechecks_after_identity_changed_before_say(self):
        queue_target = self.root / 'queue' / self.target.name
        self.target.rename(queue_target)
        fence.sidecar(self.target).rename(fence.sidecar(queue_target))
        replacement = self.root / 'replacement.json'
        replacement.write_text(json.dumps(dict(phase='ready', active={})))
        result = self.consume_queue('cp replacement.json "$SOREN_ACTIVE_GAME_CONTEXT_FILE";')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse((self.root / 'spoken').exists())
        self.assertFalse(fence.sidecar(queue_target).exists())

    def test_invalid_enqueue_cannot_publish_unfenced_or_inject_path(self):
        for source, value in [('hanjuku_commentary', ''), ('other', json.dumps(self.identity)),
                              ('../evil', ''), ('hanjuku_commentary', json.dumps(dict(self.identity, path='/tmp/no')) )]:
            result = self.shell('source "$1/lib/outbound_queue.sh"; enqueue_audio_text text "$2" "" "$3"', ROOT, source, value)
            self.assertNotEqual(result.returncode, 0)
        self.assertEqual(list((self.root / 'queue').iterdir()), [])

    def test_player_not_started_after_tts_wait_and_stopped_on_terminal(self):
        player = self.root / 'player.py'
        player.write_text('import os, time\nfrom pathlib import Path\nPath("started").write_text(str(os.getpid()))\ntime.sleep(5)\n')
        command = [sys.executable, str(ROOT / 'lib/hanjuku_audio_fence.py'), 'play',
                   str(self.canonical), str(self.target), '--', sys.executable, str(player)]
        self.set_state(lease='replacement')
        result = subprocess.run(command, cwd=self.root, capture_output=True, timeout=3)
        self.assertEqual(result.returncode, 75)
        self.assertFalse((self.root / 'started').exists())
        self.set_state()
        proc = subprocess.Popen(command, cwd=self.root)
        try:
            deadline = time.monotonic() + 3
            while not (self.root / 'started').exists() and time.monotonic() < deadline:
                time.sleep(.02)
            self.assertTrue((self.root / 'started').exists())
            self.set_state(terminal='game_over')
            self.assertEqual(proc.wait(timeout=2), 75)
            owned_pid = int((self.root / 'started').read_text())
            with self.assertRaises(ProcessLookupError):
                os.kill(owned_pid, 0)
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait()

    def test_interrupt_reaps_only_the_owned_player(self):
        code = 'import os,time; from pathlib import Path; Path("owned_pid").write_text(str(os.getpid())); time.sleep(5)'
        proc = subprocess.Popen([sys.executable, str(ROOT / 'lib/hanjuku_audio_fence.py'), 'play',
                                 str(self.canonical), str(self.target), '--', sys.executable, '-c', code],
                                cwd=self.root)
        try:
            deadline = time.monotonic() + 3
            while not (self.root / 'owned_pid').exists() and time.monotonic() < deadline:
                time.sleep(.02)
            self.assertTrue((self.root / 'owned_pid').exists())
            proc.terminate()
            self.assertEqual(proc.wait(timeout=2), 75)
            with self.assertRaises(ProcessLookupError):
                os.kill(int((self.root / 'owned_pid').read_text()), 0)
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait()

    def test_full_say_drops_expired_item_without_retry_or_failure_streak(self):
        shutil.copy2(ROOT / 'say_enqueue.sh', self.root / 'say_enqueue.sh')
        (self.root / 'lib').mkdir()
        for name in ('outbound_queue.sh', 'hanjuku_audio_fence.py'):
            shutil.copy2(ROOT / 'lib' / name, self.root / 'lib' / name)
        self.set_state(lease='replacement')
        env = dict(self.env, SOREN_OBS_PLATFORM='linux', SAY_RETRY_MAX='6',
                   SPEAKING_GRACE_SEC='0', TWITCH_SNOOZE_POLL_SEC='0')
        result = subprocess.run(['bash', './say_enqueue.sh', '--no-preempt', '--wav', str(self.target), '120', '0'],
                                cwd=self.root, env=env, capture_output=True, text=True, timeout=8)
        self.assertEqual(result.returncode, 1, result.stderr)
        played = (self.root / 'tmp/.say_queue/played.log').read_text()
        self.assertIn('skipped_hanjuku_fence', played)
        debug = (self.root / 'tmp/.say_queue/debug.log').read_text()
        self.assertNotIn('say開始 (attempt=', debug)
        self.assertFalse((self.root / 'tmp/.say_queue/lock').exists())

    def test_actual_say_launcher_uses_fence_after_synthesis(self):
        # Use the real common player launcher, without TTS/network dependencies.
        script = (ROOT / 'say_enqueue.sh').read_text()
        launcher = script[script.index('_launch_bg_exec() {'):script.index('# Linux 専用: paplay')]
        helper = self.root / 'lib'
        helper.mkdir()
        (helper / 'hanjuku_audio_fence.py').symlink_to(ROOT / 'lib/hanjuku_audio_fence.py')
        self.set_state(lease='new-lease')
        result = self.shell(launcher + '\nCONTENT_FILE="$1"; _launch_bg_exec "" touch started; wait "$!"', self.target)
        self.assertEqual(result.returncode, 75, result.stderr)
        self.assertFalse((self.root / 'started').exists())


if __name__ == '__main__':
    unittest.main()
