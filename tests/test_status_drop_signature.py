"""Drop detection must ignore GNU stat's filesystem-error output."""
import pathlib
import shutil
import subprocess
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]

@unittest.skipUnless(shutil.which('zsh'), 'zsh required')
class DropSignatureTest(unittest.TestCase):
    def probe(self, platform, expected):
        source = (ROOT / 'show_status.sh').read_text()
        function = source.split('_latest_drop_signature() {', 1)[1].split('\n_latest_drop_summary()', 1)[0]
        with tempfile.TemporaryDirectory() as folder:
            script = '''
OSTYPE=$1
expected=$2
LATEST_DROP_LOG=$3/drop.jsonl
print '{"turn":1}' > "$LATEST_DROP_LOG"
stat() {
    if [[ "$1" == "$expected" ]]; then
        print '100:42'
    else
        # GNU stat -f can print filesystem data before returning failure.
        local count=0
        [[ -f "$LATEST_DROP_LOG.count" ]] && read -r count < "$LATEST_DROP_LOG.count"
        (( count += 1 ))
        print "$count" > "$LATEST_DROP_LOG.count"
        print "filesystem-free-$count"
        return 1
    fi
}
''' + '_latest_drop_signature() {' + function + '''
a=$(_latest_drop_signature)
b=$(_latest_drop_signature)
[[ "$a" == "$b" ]] || exit 11
print '{"turn":2}' > "$LATEST_DROP_LOG"
c=$(_latest_drop_signature)
[[ "$b" != "$c" ]] || exit 12
'''
            result = subprocess.run(['zsh', '-c', script, 'probe', platform, expected, folder], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr or f'probe exit {result.returncode}')

    def test_linux_unchanged_file_is_stable_and_new_turn_changes(self):
        self.probe('linux-gnu', '-c')

    def test_macos_unchanged_file_is_stable_and_new_turn_changes(self):
        self.probe('darwin25.0', '-f')

if __name__ == '__main__':
    unittest.main()
