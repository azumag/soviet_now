#!/usr/bin/env python3
"""docich#605 acceptance-evidence baseline for the SorenGame (Python) path.

Replays retained game_history/*.jsonl records through the same pair geometry
that strategy.merge_opportunity_alternatives uses, so the wedge-over-exposed
pair-lane pattern can be counted before/after a deploy:

- pair_opportunity_turns: an exposed large same-type (>=8) pair gap exists,
- recorded_top_cover_burial_strict: the recorded decision wedged a small
  different-type piece over the gap top (the loss term added by soviet_now
  PR #680; reason gate mirrors PR #681's NO_MERGE fix),
- recorded_top_cover_burial_physical: any-size/reason variant (upper bound),
- recorded_gap_fill_overlap: vertical overlap with the gap span (pre-existing
  loss term),
- merge_opportunity_trace: direct counts from records produced by a runner
  that writes the trace (post-deploy history): outcomes and redirects.

Read-only: history files are only read; decide() is never called and no VM
state is touched. strategy.py / analyze_board.py geometry is imported from
the checkout this tool ships in, so the measured definition always matches
the reviewed hook in the same tree.
"""
import glob
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import strategy  # noqa: E402  (read-only geometry import; decide() is not called)

SPECIFIC_KEYS = ('RUSSIA', 'ANCHOR', 'CONTACT', 'OPEN_TWIN', 'DEADLINE_GUARD',
                 'DIRECT', 'PROBABLE', 'MERGE')


def is_generic(reason: str) -> bool:
    """Mirrors merge_opportunity_alternatives after soviet_now PR #681: a MERGE
    occurrence that belongs to NO_MERGE is not a merge decision."""
    low = reason or ''
    for key in SPECIFIC_KEYS:
        idx = 0
        while True:
            idx = low.find(key, idx)
            if idx < 0:
                break
            if key == 'MERGE' and low[max(0, idx - 3):idx] == 'NO_':
                idx += len(key)
                continue
            return False
    return True


def pair_gaps(pieces, nt):
    """Mirrors the hook's pair construction (type >= 8, smaller next piece,
    unoccupied vertical span)."""
    pairs = []
    for i, left in enumerate(pieces):
        for j in range(i + 1, len(pieces)):
            right = pieces[j]
            if left[0] != right[0] or left[0] < 8 or nt >= left[0]:
                continue
            lo, hi = sorted((left[1], right[1]))
            bottom, top = max(left[5], right[5]), min(left[4], right[4])
            if hi - lo <= 0 or hi - lo > left[3] + right[3] + 2 * left[3]:
                continue
            if bottom >= top:
                continue
            if any(lo < p[1] < hi and p[5] < top and p[4] > bottom
                   for k, p in enumerate(pieces) if k not in (i, j)):
                continue
            pairs.append((lo, hi, bottom, top))
    return pairs


def empty_summary():
    return {
        'schema': 1,
        'files': 0,
        'records': 0,
        'usable_turns': 0,
        'pair_opportunity_turns': 0,
        'recorded_top_cover_burial_strict': 0,
        'recorded_top_cover_burial_physical': 0,
        'recorded_gap_fill_overlap': 0,
        'strategy_hashes': {},
        'merge_opportunity_trace': {'turns': 0, 'selected': 0, 'kept_original': 0, 'other': 0, 'redirects': 0},
        'examples': [],
    }


def analyze_records(records, max_examples=5):
    summary = empty_summary()
    examples = []

    def push(row):
        if len(examples) < max_examples:
            examples.append(row)

    for record in records:
        summary['records'] += 1
        pieces_raw = ((record.get('state_snapshot') or {}).get('pieces')) or []
        nt = record.get('next_type')
        px = record.get('decision_x')
        ptop = record.get('decision_top_y_after_drop')
        if not pieces_raw or not isinstance(nt, int) or px is None or ptop is None:
            continue
        if not 1 <= nt <= 11:
            continue
        try:
            pieces, drop = strategy._merge_route_geometry(
                {'pieces': pieces_raw, 'next': {'type': nt}})
        except Exception:
            continue
        summary['usable_turns'] += 1
        dt, db = drop[4], -drop[5]
        placed_bottom = ptop - dt - db
        pairs = pair_gaps(pieces, nt)
        if not pairs:
            continue
        summary['pair_opportunity_turns'] += 1
        summary['strategy_hashes'][record.get('strategy_hash') or 'unknown'] = \
            summary['strategy_hashes'].get(record.get('strategy_hash') or 'unknown', 0) + 1
        reason = record.get('decision_reason') or ''
        generic = is_generic(reason)
        buried_strict = False
        buried_physical = False
        filled = False
        for lo, hi, bottom, top in pairs:
            if lo < px < hi and placed_bottom < top and ptop > bottom:
                filled = True
            if lo + 0.25 < px < hi - 0.25 and placed_bottom >= top - 0.25:
                buried_physical = True
                if nt < 8 and generic:
                    buried_strict = True
        if buried_strict:
            summary['recorded_top_cover_burial_strict'] += 1
            push({'file': record.get('file'), 'turn': record.get('turn'), 'kind': 'recorded-burial',
                  'x': px, 'nt': nt, 'reason': reason[:60]})
        if buried_physical:
            summary['recorded_top_cover_burial_physical'] += 1
        if filled:
            summary['recorded_gap_fill_overlap'] += 1

        trace = record.get('merge_opportunity_trace')
        if isinstance(trace, dict):
            summary['merge_opportunity_trace']['turns'] += 1
            outcome = str(trace.get('outcome', 'other'))
            if outcome in ('selected', 'kept_original'):
                summary['merge_opportunity_trace'][outcome] += 1
            else:
                summary['merge_opportunity_trace']['other'] += 1
            try:
                moved = abs(float(trace.get('to_x')) - float(trace.get('from_x'))) > 1e-6
            except (TypeError, ValueError):
                moved = False
            if outcome == 'selected' and moved:
                summary['merge_opportunity_trace']['redirects'] += 1

    summary['examples'] = examples
    return summary


def load_history(path):
    name = Path(path).name
    records = []
    with open(path, encoding='utf-8') as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            record['file'] = name
            records.append(record)
    return records


def main(argv):
    files = []
    max_examples = 5
    i = 0
    while i < len(argv):
        if argv[i] == '--max-examples':
            i += 1
            max_examples = int(argv[i])
        else:
            files.extend(sorted(glob.glob(argv[i])))
        i += 1
    if not files:
        print('usage: python3 tools/merge_burial_baseline.py <game_history.jsonl...> [--max-examples N]',
              file=sys.stderr)
        return 2
    records = []
    for path in files:
        records.extend(load_history(path))
    summary = analyze_records(records, max_examples=max_examples)
    summary['files'] = len(files)
    opp = summary['pair_opportunity_turns']
    summary['burial_rate_strict_pct'] = round(100 * summary['recorded_top_cover_burial_strict'] / opp, 2) if opp else 0
    summary['burial_rate_physical_pct'] = round(100 * summary['recorded_top_cover_burial_physical'] / opp, 2) if opp else 0
    print(json.dumps(summary, ensure_ascii=False, indent=1))
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
