"""Real shell/runtime interoperability with the policy installer's flock lease."""
import fcntl
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]

class SpawnLeaseTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.guard = Path(self.tmp.name).resolve() / '.improve_spawn.lock'
        self.lease = self.guard.with_name(self.guard.name + '.lease')

    def runtime(self, action='acquire', ttl=90):
        code = 'source "$1/strategy/improve.sh"; log() { :; }; _'+action+'_spawn_lock'
        return subprocess.run(['bash', '-c', code, 'test', str(ROOT)],
                              env={**os.environ, 'ELOOP_LIB_DIR':str(ROOT),
                                   'IMPROVE_SPAWN_LOCK_DIR':str(self.guard),
                                   'IMPROVE_SPAWN_LOCK_TTL':str(ttl)},
                              capture_output=True, text=True, timeout=10)

    def old_guard(self, owner=None, age=100):
        self.guard.mkdir()
        (self.guard/'owner').write_text(str(owner if owner is not None else os.getpid()))
        stamp = time.time()-age
        os.utime(self.guard, (stamp, stamp))

    def test_active_policy_lease_blocks_ttl_steal(self):
        self.old_guard()
        with self.lease.open('a+') as fd:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            result = self.runtime()
            self.assertEqual(result.returncode, 1, result.stdout+result.stderr)
            self.assertEqual((self.guard/'owner').read_text(), str(os.getpid()))

    def test_active_policy_lease_blocks_first_mkdir(self):
        with self.lease.open('a+') as fd:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.assertEqual(self.runtime().returncode, 1)
            self.assertFalse(self.guard.exists())

    def test_ordinary_live_owner_still_reclaims_after_ttl(self):
        self.old_guard()
        self.assertEqual(self.runtime().returncode, 0)
        self.assertNotEqual((self.guard/'owner').read_text().strip(), str(os.getpid()))

    def test_fresh_ordinary_owner_is_not_stolen(self):
        self.old_guard(age=0)
        self.assertEqual(self.runtime().returncode, 1)

    def test_installer_death_releases_kernel_lease(self):
        code = ('import fcntl,os,pathlib,sys,time; p=pathlib.Path(sys.argv[1]); '
                'f=open(str(p)+".lease","a+"); fcntl.flock(f,fcntl.LOCK_EX); '
                'p.mkdir(); (p/"owner").write_text(str(os.getpid())); '
                'print("locked",flush=True); time.sleep(30)')
        proc = subprocess.Popen([sys.executable,'-c',code,str(self.guard)], stdout=subprocess.PIPE,text=True)
        try:
            self.assertEqual(proc.stdout.readline().strip(),'locked')
            self.assertEqual(self.runtime().returncode,1)
            proc.kill(); proc.wait(timeout=5)
            self.assertEqual(self.runtime().returncode,0)
            self.assertTrue(self.lease.exists(), 'lease inode must never be unlinked')
        finally:
            if proc.poll() is None: proc.kill();proc.wait(timeout=5)
            proc.stdout.close()

    def test_symlink_lease_refuses_without_following(self):
        target=self.guard.parent/'other';target.write_text('keep')
        self.lease.symlink_to(target)
        self.assertNotEqual(self.runtime().returncode,0)
        self.assertEqual(target.read_text(),'keep')
        self.assertFalse(self.guard.exists())

    def test_release_does_not_delete_another_owner(self):
        self.old_guard(age=0)
        self.assertEqual(self.runtime('release').returncode,0)
        self.assertEqual((self.guard/'owner').read_text(),str(os.getpid()))

    def test_acquire_and_release_in_same_shell(self):
        script='source "$1/strategy/improve.sh"; log() { :; }; _acquire_spawn_lock && _release_spawn_lock'
        p=subprocess.run(['bash','-c',script,'test',str(ROOT)], env={**os.environ,'ELOOP_LIB_DIR':str(ROOT),'IMPROVE_SPAWN_LOCK_DIR':str(self.guard)},timeout=10)
        self.assertEqual(p.returncode,0);self.assertFalse(self.guard.exists())
        self.assertTrue(self.lease.exists())

if __name__=='__main__':unittest.main()
