"""Extracted accumulate step for the strategy run journal (soviet_now#145).

``accumulate_game_data``（strategy/improve.sh）の inline Python と同一の
ロジックを pure-ish 関数として抽出したもの。shadow モードでは旧 acc
ファイルとは別の shadow acc を維持し、旧実装の結果と ``shadow_compare``
した差分を run journal の evidence として残す。旧経路・active strategy・
production 動作には触れない。
"""

from __future__ import annotations

import json
import os
import sys


def summarize_archive(path):
    """archive JSONL を走査して (max_type, frontier_hint, peak_counts,
    deadline_guard_count) を返す。旧実装と同一の寛容なパース仕様。"""
    max_type = 0
    peak_type_counts = {}
    deadline_guard_count = 0
    if not path or not os.path.exists(path):
        return max_type, 'no-high-type', 'none', deadline_guard_count
    try:
        with open(path, encoding='utf-8', errors='ignore') as f:
            for raw in f:
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    row = json.loads(raw)
                except Exception:
                    continue
                if 'DEADLINE_GUARD' in str(row.get('decision_reason') or ''):
                    deadline_guard_count += 1
                pieces = ((row.get('state_snapshot') or {}).get('pieces') or [])
                for piece in pieces:
                    try:
                        t = int(piece.get('type', 0) or 0)
                    except Exception:
                        continue
                    max_type = max(max_type, t)
                    if t >= 10:
                        same_type_count = 0
                        for p in pieces:
                            try:
                                if int((p or {}).get('type', 0) or 0) == t:
                                    same_type_count += 1
                            except Exception:
                                pass
                        peak_type_counts[t] = max(peak_type_counts.get(t, 0), same_type_count)
    except Exception:
        pass
    peak_counts = ' '.join(
        f'T{t}x{peak_type_counts[t]}'
        for t in sorted(peak_type_counts, reverse=True)[:4]
    ) or 'none'
    frontier_hint = 'no-high-type'
    if max_type >= 10:
        frontier_hint = (
            f'T{max_type}_peak={peak_type_counts.get(max_type, 0)}'
            f' prev_T{max_type - 1}_peak={peak_type_counts.get(max_type - 1, 0)}'
        )
    return max_type, frontier_hint, peak_counts, deadline_guard_count


def _fresh_acc(curr_hash=''):
    return {'files': [], 'scores': '', 'soviet': False, 'soviet_count': 0,
            'count': 0, 'hash': curr_hash, 'russia_count': 0}


def accumulate(acc, archive_file, score, soviet, strategy_hash,
               russia='false', raw_score=''):
    """旧 accumulate_game_data と同一の遷移を acc へ適用して返す（pure）。"""
    if acc is None or not isinstance(acc, dict):
        acc = _fresh_acc()
    else:
        acc = dict(acc)

    curr_hash = strategy_hash or ''
    if acc.get('hash') and curr_hash and acc.get('hash') != curr_hash:
        acc = _fresh_acc(curr_hash)
    elif curr_hash:
        acc['hash'] = curr_hash

    acc['files'].append(archive_file)
    acc['scores'] = (acc['scores'] + ' ' + str(score)).strip()
    if raw_score:
        acc['raw_scores'] = (acc.get('raw_scores', '') + ' ' + str(raw_score)).strip()
    if str(soviet) == 'true':
        acc['soviet'] = True
        acc['soviet_count'] = acc.get('soviet_count', 0) + 1
    if str(russia) == 'true':
        acc['russia_count'] = acc.get('russia_count', 0) + 1
    acc['count'] += 1

    max_type, frontier_hint, peak_counts, deadline_guard_count = \
        summarize_archive(archive_file)
    acc.setdefault('max_types', []).append(max_type)
    acc.setdefault('frontier_hints', []).append(frontier_hint)
    acc.setdefault('peak_high_type_counts', []).append(peak_counts)
    acc.setdefault('deadline_guard_counts', []).append(deadline_guard_count)
    acc['best_max_type'] = max(
        [int(acc.get('best_max_type', 0) or 0), max_type])
    acc['deadline_guard_total'] = int(
        acc.get('deadline_guard_total', 0) or 0) + deadline_guard_count
    return acc


def shadow_run(shadow_acc_file, archive_file, score, soviet, strategy_hash,
               russia='false', raw_score='', legacy_acc_file=None):
    """shadow acc を 1 試合分進め、旧 acc 結果との差分を返す。

    shadow acc は旧ファイルとは別管理にして旧経路を汚染しない。
    ``legacy_acc_file`` があれば（旧実装が書き終えた後の）最新 acc と
    ``shadow_compare`` した差分 dict を返す。
    """
    from strategy_run_journal import shadow_compare

    try:
        with open(shadow_acc_file) as f:
            acc = json.load(f)
    except Exception:
        acc = None
    acc = accumulate(acc, archive_file, score, soviet, strategy_hash,
                     russia=russia, raw_score=raw_score)

    tmp = shadow_acc_file + '.tmp'
    with open(tmp, 'w') as f:
        json.dump(acc, f)
    os.replace(tmp, shadow_acc_file)

    result = {'count': acc['count']}
    if legacy_acc_file:
        try:
            with open(legacy_acc_file) as f:
                legacy = json.load(f)
            result['compare'] = shadow_compare(legacy, acc)
        except Exception as e:
            result['compare'] = {'match': None, 'error': str(e)}
    return result


if __name__ == '__main__':
    # CLI: shadow <shadow_acc> <archive> <score> <soviet> <hash> <russia>
    #       [raw_score] [legacy_acc]
    if len(sys.argv) >= 7 and sys.argv[1] == 'shadow':
        out = shadow_run(
            sys.argv[2], sys.argv[3], sys.argv[4], sys.argv[5],
            sys.argv[6],
            russia=sys.argv[7] if len(sys.argv) > 7 else 'false',
            raw_score=sys.argv[8] if len(sys.argv) > 8 else '',
            legacy_acc_file=sys.argv[9] if len(sys.argv) > 9 else None,
        )
        print(json.dumps(out, ensure_ascii=False))
        raise SystemExit(0)
    print(__doc__, file=sys.stderr)
    raise SystemExit(2)
