"""Publish only confirmed cycle completion; contains no trading or viewer actions."""
import json
import os
from pathlib import Path
import sys
import tempfile
import time


def publish(root, kind):
    if kind not in ('prediction', 'improvement'):
        raise ValueError('invalid boundary kind')
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix='.corner-boundary-', dir=root)
    try:
        with os.fdopen(fd, 'w') as handle:
            json.dump({'completed_at': time.time()}, handle)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(name, root / f'corner_boundary_{kind}.json')
    finally:
        if os.path.exists(name):
            os.unlink(name)


if __name__ == '__main__':
    publish(sys.argv[1], sys.argv[2])
