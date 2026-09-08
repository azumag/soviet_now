"""Retain the scoring window and all histories referenced by improvement batches."""
import json
from pathlib import Path
import sys


def prune(root, min_games, refs):
    root = Path(root).resolve()
    keep = max(100, int(min_games) + 1)
    protected = set()
    try:
        for ref in refs:
            ref = root / ref
            if not ref.exists():
                continue
            data = json.loads(ref.read_text())
            files = data.get('files', [])
            if not isinstance(files, list) or not all(isinstance(p, str) and p for p in files):
                return 0
            protected.update((root / p).resolve() for p in files)
    except (OSError, ValueError, TypeError, AttributeError):
        # A partial/invalid batch cannot establish which evidence is disposable.
        return 0
    histories = sorted((root / 'game_history').glob('*_score*.jsonl'),
                       key=lambda p: (p.stat().st_mtime_ns, p.name), reverse=True)
    removed = 0
    for path in histories[keep:]:
        if path.resolve() not in protected:
            try:
                path.unlink()
                removed += 1
            except FileNotFoundError:
                pass
    return removed


if __name__ == '__main__':
    print(prune(sys.argv[1], int(sys.argv[2]), sys.argv[3:]))
