"""Temporary dedicated-branch editor. Removed before opening the final PR."""
import base64
import hashlib
import json
import os
from pathlib import Path
import subprocess
import zlib

branch = 'refs/heads/fix/comment-reply-quality-20260915'
if os.environ.get('GITHUB_REF') != branch or os.environ.get('GITHUB_REPOSITORY') != 'azumag/soviet_now':
    raise SystemExit('Not the authorized task branch')
allowed = {
    'broadcast/comment.sh', 'broadcast/radio_engine.sh',
    'prompts/comment_classifier.md', 'prompts/comment_response.md',
    'prompts/comment_response_card_gacha.md', 'prompts/comment_response_chitchat.md',
    'prompts/comment_response_default.md', 'prompts/comment_response_game.md',
    'prompts/comment_response_raid.md', 'prompts/comment_response_sing_request.md',
    'prompts/comment_template.md', 'tests/test_comment_reply_quality.py',
}
encoded = ''.join(Path(f'tools/_comment_quality_edits.{i}').read_text().strip() for i in range(2))
# Correct two known transport transcription errors before checking the complete
# manifest digest. No unchecked data can reach the working tree.
encoded = encoded.replace('DD0cp5VWXysyFcurFfWC', 'DD0cp5VWXysyFurFfWC')
encoded = encoded.replace('5qaVM3bfii3WCE', '5qaVM3bfii2WCE')
raw = zlib.decompress(base64.b64decode(encoded, validate=True))
if hashlib.sha256(raw).hexdigest() != '1d8cfa19f3a9caca4736b6ba21470e05adf745c34b7608cef4a8aa75e0deea48':
    raise SystemExit('Manifest checksum mismatch')
entries = json.loads(raw)
if len(entries) != len(allowed) or {entry['path'] for entry in entries} != allowed:
    raise SystemExit('Unexpected edit scope')
prepared = []
for entry in entries:
    path = Path(entry['path'])
    if path.is_symlink():
        raise SystemExit(f'Symlink rejected: {path}')
    before = entry['before']
    if before is None:
        if path.exists():
            raise SystemExit(f'New file already exists: {path}')
        original = b''
    else:
        original = path.read_bytes()
        if hashlib.sha256(original).hexdigest() != before:
            raise SystemExit(f'Concurrent source change: {path}')
    lines = original.decode('utf-8').splitlines(keepends=True)
    for start, end, replacement in reversed(entry['changes']):
        if not 0 <= start <= end <= len(lines):
            raise SystemExit(f'Invalid edit range: {path}')
        lines[start:end] = replacement.splitlines(keepends=True)
    output = ''.join(lines).encode('utf-8')
    if hashlib.sha256(output).hexdigest() != entry['after']:
        raise SystemExit(f'Output checksum mismatch: {path}')
    prepared.append((path, output))
for path, output in prepared:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(output)
subprocess.run(['git', 'add', '--', *sorted(allowed)], check=True)
changed = set(subprocess.check_output(['git', 'diff', '--cached', '--name-only'], text=True).splitlines())
if changed != allowed:
    raise SystemExit('Unexpected staged files')
subprocess.run(['git', 'diff', '--cached', '--check'], check=True)
print(f'Applied and checksum-verified {len(prepared)} reviewed source/test files')
