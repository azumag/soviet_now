"""挙動シグネチャ生成と Jensen-Shannon 距離計算。

戦略の挙動を低次元の正規化分布へ圧縮し、戦略間の挙動距離を測る。
帯域脱出機構 (D: diversity premium, E: tabu) で anchor 選定と
rollback された挙動近傍のガードに使われる。
"""

from __future__ import annotations

import json
import math
from collections import Counter
from pathlib import Path
from typing import Iterable, Mapping, Sequence


REASON_TOP_K = 20
X_BIN_EDGES = [-3.0, -2.0, -1.0, 0.0, 1.0, 2.0, 3.0]  # 6 bins
HIGH_PHASE_Y_THRESHOLD = 1.8


def _x_bin_index(x: float) -> int:
    for i in range(len(X_BIN_EDGES) - 1):
        if X_BIN_EDGES[i] <= x < X_BIN_EDGES[i + 1]:
            return i
    if x < X_BIN_EDGES[0]:
        return 0
    return len(X_BIN_EDGES) - 2


def _normalize_counter(c: Mapping[str, float | int]) -> dict:
    total = sum(c.values())
    if total <= 0:
        return {}
    return {k: float(v) / total for k, v in c.items()}


def _normalize_bins(bins: Sequence[float | int]) -> list[float]:
    total = sum(bins)
    if total <= 0:
        return [0.0 for _ in bins]
    return [float(b) / total for b in bins]


def _resolve_git_blob(ref: str) -> str | None:
    """`git:<sha>:game#N` 形式の参照を git cat-file で展開してテキストを返す。"""
    import subprocess

    parts = ref.split(":", 2)
    if len(parts) < 2:
        return None
    sha = parts[1]
    if not sha:
        return None
    try:
        out = subprocess.run(
            ["git", "cat-file", "-p", sha],
            check=False,
            capture_output=True,
            timeout=10,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    if out.returncode != 0:
        return None
    try:
        return out.stdout.decode("utf-8", errors="replace")
    except Exception:
        return None


def _iter_turns(source: str | Path) -> Iterable[dict]:
    """ファイルパス or `git:<sha>:...` 参照からターンを読み出す。"""
    s = str(source)
    if s.startswith("git:"):
        text = _resolve_git_blob(s)
        if not text:
            return
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue
        return
    with open(s, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue


def compute_signature(jsonl_paths: Sequence[str | Path]) -> dict:
    """指定 jsonl ファイル群から挙動シグネチャを生成する。

    返り値の各分布は合計 1.0 に正規化される。
    """
    reason_counter: Counter[str] = Counter()
    high_reason_counter: Counter[str] = Counter()
    x_bins = [0] * (len(X_BIN_EDGES) - 1)
    merge_available_count = 0
    merge_taken_count = 0  # merge_available=true で実際に DIRECT_MERGE / CHAIN_MERGE 等を選んだ回数
    endgame_score_deltas: list[float] = []
    n_turns = 0
    n_games = 0

    for path in jsonl_paths:
        s = str(path)
        if not s.startswith("git:") and not Path(s).exists():
            continue
        # turn iteration; only count as game if we get at least one turn
        prev_score = 0.0
        endgame_window: list[float] = []
        got_any = False
        for turn in _iter_turns(s):
            got_any = True
            n_turns += 1
            reason = str(turn.get("decision_reason", "") or "OTHER")
            reason_counter[reason] += 1
            if turn.get("max_y", -999) >= HIGH_PHASE_Y_THRESHOLD:
                high_reason_counter[reason] += 1
            try:
                dx = float(turn.get("decision_x", 0.0) or 0.0)
            except (TypeError, ValueError):
                dx = 0.0
            x_bins[_x_bin_index(dx)] += 1
            if turn.get("merge_available"):
                merge_available_count += 1
                if reason in ("DIRECT_MERGE", "CHAIN_MERGE", "DANGER_DIRECT_MERGE", "DANGER_MERGE"):
                    merge_taken_count += 1
            score = float(turn.get("score", 0) or 0)
            score_delta = score - prev_score
            prev_score = score
            deadline_margin = float(turn.get("deadline_margin", 99) or 99)
            if deadline_margin <= 2.5:
                endgame_window.append(score_delta)
        if got_any:
            n_games += 1
        if endgame_window:
            endgame_score_deltas.append(sum(endgame_window) / len(endgame_window))

    reason_top = dict(reason_counter.most_common(REASON_TOP_K))
    high_reason_top = dict(high_reason_counter.most_common(REASON_TOP_K))

    merge_take_rate = (
        merge_taken_count / merge_available_count if merge_available_count > 0 else 0.0
    )
    endgame_recovery = (
        sum(endgame_score_deltas) / len(endgame_score_deltas) if endgame_score_deltas else 0.0
    )

    return {
        "reason": _normalize_counter(reason_top),
        "x_bins": _normalize_bins(x_bins),
        "high_phase_reason": _normalize_counter(high_reason_top),
        "merge_take_rate": merge_take_rate,
        "endgame_recovery": endgame_recovery,
        "n_games": n_games,
        "n_turns": n_turns,
    }


def _jsd_categorical(p: Mapping[str, float], q: Mapping[str, float]) -> float:
    """Jensen-Shannon divergence (log base 2). 範囲 [0, 1]。"""
    keys = set(p) | set(q)
    if not keys:
        return 0.0
    m_dist = {k: 0.5 * (p.get(k, 0.0) + q.get(k, 0.0)) for k in keys}

    def kl(a: Mapping[str, float], b: Mapping[str, float]) -> float:
        s = 0.0
        for k, v in a.items():
            if v <= 0:
                continue
            mv = b.get(k, 0.0)
            if mv <= 0:
                continue
            s += v * math.log2(v / mv)
        return s

    return 0.5 * kl(p, m_dist) + 0.5 * kl(q, m_dist)


def _jsd_bins(p: Sequence[float], q: Sequence[float]) -> float:
    n = max(len(p), len(q))
    pp = list(p) + [0.0] * (n - len(p))
    qq = list(q) + [0.0] * (n - len(q))
    pd = {str(i): v for i, v in enumerate(pp)}
    qd = {str(i): v for i, v in enumerate(qq)}
    return _jsd_categorical(pd, qd)


def signature_distance(sig_a: dict, sig_b: dict) -> float:
    """2 シグネチャ間の挙動距離。重み付き JSD の和。範囲 [0, 1] 近傍。"""
    if not sig_a or not sig_b:
        return 1.0
    d_reason = _jsd_categorical(sig_a.get("reason", {}), sig_b.get("reason", {}))
    d_high = _jsd_categorical(sig_a.get("high_phase_reason", {}), sig_b.get("high_phase_reason", {}))
    d_xbins = _jsd_bins(sig_a.get("x_bins", []), sig_b.get("x_bins", []))
    d_merge = abs(
        float(sig_a.get("merge_take_rate", 0.0)) - float(sig_b.get("merge_take_rate", 0.0))
    )
    return 0.40 * d_reason + 0.25 * d_high + 0.20 * d_xbins + 0.15 * d_merge


def load_signatures_cache(cache_file: str | Path) -> dict:
    p = Path(cache_file)
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def save_signatures_cache(cache_file: str | Path, cache: dict) -> None:
    p = Path(cache_file)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
    tmp.replace(p)


def get_or_compute_signature(
    strategy_hash: str,
    jsonl_paths: Sequence[str | Path],
    cache_file: str | Path,
) -> dict:
    cache = load_signatures_cache(cache_file)
    existing = cache.get(strategy_hash)
    if existing and existing.get("n_games", 0) >= len(jsonl_paths):
        return existing
    sig = compute_signature(jsonl_paths)
    cache[strategy_hash] = sig
    save_signatures_cache(cache_file, cache)
    return sig


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("usage: behavior_signature.py <jsonl> [<jsonl>...]", file=sys.stderr)
        sys.exit(2)
    sig = compute_signature(sys.argv[1:])
    print(json.dumps(sig, ensure_ascii=False, indent=2))
