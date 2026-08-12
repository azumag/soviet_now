#!/usr/bin/env python3
"""status_dashboard.py — CUI Graphical Statistics Dashboard for Soren AI

Renders 4 panels: Header, Score Timeline (braille), Score Distribution,
Strategy Comparison.
Decision Patterns logic remains available but is hidden from the dashboard layout.
"""

import json
import math
import os
import re
import subprocess
import sys
import time
import unicodedata
from datetime import datetime, timezone, timedelta
from collections import Counter
from glob import glob
from pathlib import Path

W = 57
RANK_LCB_Z = 1.28
RANK_WEIGHT_P50 = 0.55
RANK_WEIGHT_P25 = 0.30
RANK_WEIGHT_LCB = 0.15
MIN_GAMES_BEFORE_IMPROVE = 12
MIN_GAMES_BEFORE_REGRESSION = int(os.getenv("MIN_GAMES_BEFORE_REGRESSION", "12"))
MIN_GAMES_FOR_BEST_ROLLBACK = 12
REGRESSION_MAX_RANK = int(os.getenv("REGRESSION_MAX_RANK", "20"))
REGRESSION_COMPOSITE_RATIO = 0.88
REGRESSION_P50_RATIO = 0.85
REGRESSION_P25_RATIO = 0.80
REGRESSION_MIN_COMP_GAP = float(os.getenv("REGRESSION_MIN_COMP_GAP", "120"))
REGRESSION_MIN_P50_GAP = float(os.getenv("REGRESSION_MIN_P50_GAP", "100"))
REGRESSION_MIN_P25_GAP = float(os.getenv("REGRESSION_MIN_P25_GAP", "180"))
REGRESSION_MIN_BREACH_COUNT = int(os.getenv("REGRESSION_MIN_BREACH_COUNT", "2"))
BRANCH_MAX_DEPTH = int(os.getenv("BRANCH_MAX_DEPTH", "4"))
BRANCH_MAX_GAMES = int(os.getenv("BRANCH_MAX_GAMES", "48"))
BRANCH_PATIENCE = int(os.getenv("BRANCH_PATIENCE", "3"))
BRANCH_HARD_COMP_GAP = float(os.getenv("BRANCH_HARD_COMP_GAP", "220"))
BRANCH_HARD_P50_GAP = float(os.getenv("BRANCH_HARD_P50_GAP", "180"))
BRANCH_HARD_P25_GAP = float(os.getenv("BRANCH_HARD_P25_GAP", "260"))
BRANCH_HARD_MIN_BREACH_COUNT = int(os.getenv("BRANCH_HARD_MIN_BREACH_COUNT", "2"))
REGRESSION_TREND_SHORT_WINDOW = 50
REGRESSION_TREND_LONG_WINDOW = 100
REGRESSION_TREND_SHORT_RATIO = 0.94
REGRESSION_TREND_LONG_RATIO = 0.95
BEST_STRATEGY_ANCHOR_FILE = "tmp/state/best_strategy_anchor.json"
ACTIVE_BRANCH_FILE = "tmp/state/active_branch.json"
REJECTED_HASHES_FILE = "tmp/history/rejected_hashes.txt"
REJECTED_HASH_META_FILE = "tmp/state/rejected_hash_metrics.json"
REJECTED_REEVALUATE_TTL_SEC = 21600
LAST_ROLLBACK_PAIR_FILE = "tmp/state/last_rollback_pair.json"
CURRENT_STRATEGY_RUN_FILE = "tmp/state/current_strategy_run.json"
ANNEALING_OBSERVE_FILE = "tmp/state/annealing_candidates.jsonl"
WILDCARD_ATTEMPT_STATE_FILE = "tmp/state/wildcard_attempt_state.json"
VIEWER_CHAT_MONITOR_FILE = os.getenv("VIEWER_CHAT_MONITOR_FILE", "tmp/state/viewer_chat_monitor.json")
SOREN91_IMPROVE_LOCK_FILE = os.getenv("SOREN91_IMPROVE_LOCK", "soren91/tmp/soren91_improve.lock")
SOREN91_IMPROVE_PID_FILE = os.getenv("SOREN91_IMPROVE_PID_FILE", "soren91/tmp/soren91_improve.pid")
SOREN91_IMPROVE_HUNG_QUARANTINE_FILE = os.getenv(
    "SOREN91_IMPROVE_HUNG_QUARANTINE_FILE",
    "tmp/state/soren91_improve_hung_quarantine.jsonl",
)
ARCHIVE_RESTART_COOLDOWN_FILE = "tmp/state/archive_restart_cooldown.json"
ARCHIVE_RESTART_COOLDOWN_SEC = 21600
ARCHIVE_RESTART_NO_CANDIDATE_COOLDOWN_FILE = "tmp/state/.archive_restart_no_candidate"
STRATEGY_HASH_ARCHIVE_DIR = "strategy_versions/by_hash"
STRATEGY_HASH_PERMANENT_ARCHIVE_DIR = "strategy_versions_archive/by_hash"
STRATEGY_VERSIONS_DIR = "strategy_versions"
HASH_ARCHIVE_KEEP_TOP = int(os.getenv("HASH_ARCHIVE_KEEP_TOP", "100"))

# ── ANSI helpers ──────────────────────────────────────────────

ANSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")

def fg256(n):
    return f"\033[38;5;{n}m"

RST = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"
C_CYAN = fg256(37)
C_GREEN = fg256(34)
C_YELLOW = fg256(220)
C_RED = fg256(196)
C_WHITE = fg256(255)
C_GREY = fg256(245)
C_BLUE = fg256(33)

SCORE_GRADIENT = [196, 202, 208, 214, 220, 226, 190, 154, 118, 82, 46]


def char_display_width(ch):
    if unicodedata.combining(ch):
        return 0
    return 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1


def ansi_display_width(text):
    plain = ANSI_RE.sub("", str(text or ""))
    return sum(char_display_width(ch) for ch in plain)


def truncate_ansi_display(text, max_width):
    text = str(text or "")
    if max_width <= 0 or ansi_display_width(text) <= max_width:
        return text
    limit = max(0, max_width - 1)
    out = []
    width = 0
    pos = 0
    saw_ansi = False
    for match in ANSI_RE.finditer(text):
        segment = text[pos:match.start()]
        for ch in segment:
            ch_width = char_display_width(ch)
            if width + ch_width > limit:
                return "".join(out) + "…" + (RST if saw_ansi else "")
            out.append(ch)
            width += ch_width
        out.append(match.group(0))
        saw_ansi = True
        pos = match.end()
    for ch in text[pos:]:
        ch_width = char_display_width(ch)
        if width + ch_width > limit:
            return "".join(out) + "…" + (RST if saw_ansi else "")
        out.append(ch)
        width += ch_width
    return "".join(out)


def fit_dashboard_lines(lines, width=W):
    return [truncate_ansi_display(line, width) for line in lines]


def gradient_color(val, lo, hi):
    if hi <= lo:
        return fg256(SCORE_GRADIENT[len(SCORE_GRADIENT) // 2])
    ratio = min(max((val - lo) / (hi - lo), 0), 1)
    idx = int(ratio * (len(SCORE_GRADIENT) - 1))
    return fg256(SCORE_GRADIENT[idx])


# ── BrailleCanvas ─────────────────────────────────────────────
# Each braille char = 2 dot-columns × 4 dot-rows
# Dot encoding: col0 bits=[0,1,2,6], col1 bits=[3,4,5,7]

BRAILLE_BASE = 0x2800
DOT_MAP = [
    [0x01, 0x08],  # row 0
    [0x02, 0x10],  # row 1
    [0x04, 0x20],  # row 2
    [0x40, 0x80],  # row 3
]


class BrailleCanvas:
    def __init__(self, char_w, char_h):
        self.cw = char_w
        self.ch = char_h
        self.dot_w = char_w * 2
        self.dot_h = char_h * 4
        self.buf = [[0] * char_w for _ in range(char_h)]
        self.colors = [[None] * char_w for _ in range(char_h)]

    def set(self, dx, dy, color=None):
        if dx < 0 or dx >= self.dot_w or dy < 0 or dy >= self.dot_h:
            return
        cy = dy // 4
        cx = dx // 2
        ry = dy % 4
        rx = dx % 2
        self.buf[cy][cx] |= DOT_MAP[ry][rx]
        if color:
            self.colors[cy][cx] = color

    def render_lines(self):
        lines = []
        for r in range(self.ch):
            s = ""
            for c in range(self.cw):
                ch = chr(BRAILLE_BASE + self.buf[r][c])
                col = self.colors[r][c]
                if col:
                    s += col + ch + RST
                else:
                    s += ch
            lines.append(s)
        return lines


# ── Block bar rendering ───────────────────────────────────────

BAR_CHARS = " ▏▎▍▌▋▊▉█"


def block_bar(value, max_val, width, color=""):
    if max_val <= 0:
        return " " * width
    ratio = min(value / max_val, 1.0)
    full_eighths = ratio * width * 8
    full = int(full_eighths // 8)
    frac = int(full_eighths % 8)
    bar = "█" * full
    if frac > 0 and full < width:
        bar += BAR_CHARS[frac]
        full += 1
    bar += " " * (width - full)
    if color:
        return color + bar + RST
    return bar


def compact_regpreview_text(text, max_len):
    compact = str(text or "")
    replacements = [
        ("RegPreview ", "Reg "),
        ("anchor=", "a="),
        ("best=", "b="),
        ("gap=", "g="),
        (" hard", " H"),
        (" no anchor", " no-a"),
        ("current not tracked", "not tracked"),
    ]
    for old, new in replacements:
        compact = compact.replace(old, new)
    compact = re.sub(r"\s+", " ", compact).strip()
    if len(compact) <= max_len:
        return compact
    if max_len <= 3:
        return compact[:max_len]
    tail_len = min(12, max(6, max_len // 4))
    head_len = max_len - tail_len - 3
    if head_len < 1:
        return compact[: max_len - 3] + "..."
    return compact[:head_len] + "..." + compact[-tail_len:]


# ── Data loading ──────────────────────────────────────────────

def load_scores():
    p = Path("score_history.txt")
    if not p.exists():
        return []
    return [int(l.strip().split('\t')[-1]) for l in p.read_text().splitlines() if l.strip().split('\t')[-1].isdigit()]


def load_rolling():
    p = Path("tmp/state/rolling_scores.json")
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text())
    except Exception:
        return {}


def load_best_anchor():
    p = Path(BEST_STRATEGY_ANCHOR_FILE)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text())
    except Exception:
        return None


def load_active_branch():
    p = Path(ACTIVE_BRANCH_FILE)
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text())
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def load_rejected_hashes():
    p = Path(REJECTED_HASHES_FILE)
    if not p.exists():
        return set()
    try:
        hashes = {line.strip() for line in p.read_text().splitlines() if line.strip()}
    except Exception:
        return set()
    meta = load_rejected_hash_meta()
    now = int(time.time())
    active = set()
    for hash_ in hashes:
        entry = meta.get(hash_)
        if not isinstance(entry, dict):
            continue
        updated_at = int(entry.get("updated_at", 0) or 0)
        if updated_at <= 0:
            continue
        if REJECTED_REEVALUATE_TTL_SEC > 0 and now - updated_at >= REJECTED_REEVALUATE_TTL_SEC:
            continue
        active.add(hash_)
    return active


def load_rejected_hash_meta():
    p = Path(REJECTED_HASH_META_FILE)
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text())
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def load_last_rollback_pair():
    p = Path(LAST_ROLLBACK_PAIR_FILE)
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text())
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def count_fresh_games_since_last_rollback(current_hash):
    if not current_hash:
        return None
    last_pair = load_last_rollback_pair()
    if not isinstance(last_pair, dict):
        return None
    if str(last_pair.get("to_hash", "") or "") != current_hash:
        return None
    rollback_ts = int(last_pair.get("updated_at", 0) or 0)
    if rollback_ts <= 0:
        return None

    p = Path(CURRENT_STRATEGY_RUN_FILE)
    if not p.exists():
        return None
    try:
        run = json.loads(p.read_text())
    except Exception:
        return None
    if str(run.get("hash", "") or "") != current_hash:
        return None

    fresh = 0
    for archive in run.get("_recent_archives", []) or []:
        if not isinstance(archive, str) or not archive.startswith("game_history/"):
            continue
        archive_path = Path(archive)
        if not archive_path.exists():
            continue
        try:
            if int(archive_path.stat().st_mtime) >= rollback_ts:
                fresh += 1
        except Exception:
            continue
    return fresh


def compute_decide_hash(path):
    try:
        r = subprocess.run(
            ["python3", "extract_decide_hash.py", str(path)],
            capture_output=True,
            text=True,
            timeout=5,
        )
        h = r.stdout.strip()
        return h if r.returncode == 0 and h else ""
    except Exception:
        return ""


def load_restorable_hashes():
    restorable = set()

    for archive_dir in (STRATEGY_HASH_ARCHIVE_DIR, STRATEGY_HASH_PERMANENT_ARCHIVE_DIR):
        by_hash_dir = Path(archive_dir)
        if by_hash_dir.exists():
            for f in by_hash_dir.glob("*.py"):
                restorable.add(f.stem)
    return restorable


def quantile(vals, p):
    xs = sorted(vals)
    if not xs:
        return 0.0
    if len(xs) == 1:
        return float(xs[0])
    pos = (len(xs) - 1) * p
    lo = int(pos)
    hi = min(lo + 1, len(xs) - 1)
    frac = pos - lo
    return xs[lo] * (1.0 - frac) + xs[hi] * frac


def calc_strategy_metrics(scores):
    xs = [int(v) for v in scores]
    if not xs:
        return None
    n = len(xs)
    mean = sum(xs) / n
    p25 = quantile(xs, 0.25)
    p50 = quantile(xs, 0.50)
    if n > 1:
        var = sum((x - mean) ** 2 for x in xs) / n
        std = math.sqrt(var)
    else:
        std = 0.0
    lcb = mean - RANK_LCB_Z * (std / math.sqrt(n))
    comp = (RANK_WEIGHT_P50 * p50) + (RANK_WEIGHT_P25 * p25) + (RANK_WEIGHT_LCB * lcb)
    return {
        "n": n,
        "mean": mean,
        "std": std,
        "p25": p25,
        "p50": p50,
        "lcb": lcb,
        "comp": comp,
    }


def is_blocked_reverse_rollback_pair(current_hash, candidate_hash, last_pair):
    if not current_hash or not candidate_hash or not isinstance(last_pair, dict):
        return False
    return (
        str(last_pair.get("to_hash", "") or "") == current_hash
        and str(last_pair.get("from_hash", "") or "") == candidate_hash
    )


def ranked_mature_entries(rolling, current_hash="", top=None, require_restorable=True):
    restorable_hashes = load_restorable_hashes() if require_restorable else None
    entries = []
    for hash_, data in rolling.items():
        if not hash_ or hash_ == current_hash:
            continue
        metrics = calc_strategy_metrics(data.get("scores", []))
        if not metrics or metrics["n"] < MIN_GAMES_FOR_BEST_ROLLBACK:
            continue
        if require_restorable and hash_ not in restorable_hashes:
            continue
        games_total = data.get("games_total", len(data.get("scores", [])))
        try:
            games_total = int(games_total)
        except Exception:
            games_total = len(data.get("scores", []))
        entries.append(
            {
                "hash": hash_,
                "h8": hash_[:8],
                "n_roll": metrics["n"],
                "n_total": games_total,
                "comp": metrics["comp"],
                "p50": metrics["p50"],
                "p25": metrics["p25"],
                "lcb": metrics["lcb"],
            }
        )
    entries.sort(key=lambda e: (e["comp"], e["p50"], e["p25"], e["n_roll"]), reverse=True)
    if top is not None:
        entries = entries[:top]
    return entries


def get_current_strategy_run_entry(current_hash):
    if not current_hash:
        return None
    p = Path(CURRENT_STRATEGY_RUN_FILE)
    if not p.exists():
        data = {}
    try:
        data = json.loads(p.read_text()) if p.exists() else data
    except Exception:
        data = {}
    if str(data.get("hash", "") or "") != current_hash:
        data = {
            "hash": current_hash,
            "scores": [],
            "games_total": 0,
        }
    scores = data.get("scores", [])
    metrics = calc_strategy_metrics(scores)
    games_total = data.get("games_total", len(scores))
    try:
        games_total = int(games_total)
    except Exception:
        games_total = len(scores)
    if metrics:
        return {
            "hash": current_hash,
            "h8": current_hash[:8],
            "n_roll": metrics["n"],
            "n_total": games_total,
            "comp": metrics["comp"],
            "p50": metrics["p50"],
            "p25": metrics["p25"],
            "lcb": metrics["lcb"],
        }
    return {
        "hash": current_hash,
        "h8": current_hash[:8],
        "n_roll": 0,
        "n_total": games_total,
        "comp": 0.0,
        "p50": 0.0,
        "p25": 0.0,
        "lcb": 0.0,
    }


def collect_rollback_candidate_hashes(rolling, current_hash):
    last_pair = load_last_rollback_pair()
    current_entry = get_current_strategy_run_entry(current_hash)
    if current_entry:
        current_comp = current_entry["comp"]
    else:
        current_metrics = calc_strategy_metrics(rolling.get(current_hash, {}).get("scores", [])) if current_hash else None
        current_comp = current_metrics["comp"] if current_metrics else None
    candidates = set()
    for entry in ranked_mature_entries(rolling, current_hash, top=HASH_ARCHIVE_KEEP_TOP, require_restorable=True):
        hash_ = entry["hash"]
        if current_comp is not None and entry["comp"] <= current_comp:
            continue
        if is_blocked_reverse_rollback_pair(current_hash, hash_, last_pair):
            continue
        candidates.add(hash_)
    return candidates


def pick_best_reference(rolling, current_hash, anchor=None):
    ranked = ranked_mature_entries(rolling, current_hash, top=HASH_ARCHIVE_KEEP_TOP, require_restorable=True)
    if not ranked:
        return None
    best = ranked[0]
    best_metrics = {
        "comp": best["comp"],
        "p50": best["p50"],
        "p25": best["p25"],
        "lcb": best["lcb"],
        "n": best["n_roll"],
    }
    return (best["comp"], best["p50"], best["p25"], best["n_roll"], best["hash"], best_metrics, "ranking")


def calc_trend_flags(scores):
    trend50 = False
    trend100 = False
    if len(scores) >= REGRESSION_TREND_SHORT_WINDOW * 2:
        recent = scores[-REGRESSION_TREND_SHORT_WINDOW:]
        prev = scores[-REGRESSION_TREND_SHORT_WINDOW * 2:-REGRESSION_TREND_SHORT_WINDOW]
        prev_avg = sum(prev) / len(prev)
        recent_avg = sum(recent) / len(recent)
        trend50 = prev_avg > 0 and recent_avg < prev_avg * REGRESSION_TREND_SHORT_RATIO
    if len(scores) >= REGRESSION_TREND_LONG_WINDOW * 2:
        recent = scores[-REGRESSION_TREND_LONG_WINDOW:]
        prev = scores[-REGRESSION_TREND_LONG_WINDOW * 2:-REGRESSION_TREND_LONG_WINDOW]
        prev_avg = sum(prev) / len(prev)
        recent_avg = sum(recent) / len(recent)
        trend100 = prev_avg > 0 and recent_avg < prev_avg * REGRESSION_TREND_LONG_RATIO
    return trend50, trend100


def metric_key(metrics):
    return (
        float(metrics.get("comp", 0.0)),
        float(metrics.get("p50", 0.0)),
        float(metrics.get("p25", 0.0)),
        int(metrics.get("n", 0)),
    )


def metric_gaps(ref, target):
    comp_gap = max(0.0, float(ref.get("comp", 0.0)) - float(target.get("comp", 0.0)))
    p50_gap = max(0.0, float(ref.get("p50", 0.0)) - float(target.get("p50", 0.0)))
    p25_gap = max(0.0, float(ref.get("p25", 0.0)) - float(target.get("p25", 0.0)))
    return comp_gap, p50_gap, p25_gap


def metric_breach_count(comp_gap, p50_gap, p25_gap, comp_th, p50_th, p25_th):
    return sum(
        [
            1 if comp_gap >= comp_th else 0,
            1 if p50_gap >= p50_th else 0,
            1 if p25_gap >= p25_th else 0,
        ]
    )


def strategy_metrics_for_hash(rolling, hash_, current_entry=None):
    if not hash_:
        return None
    if current_entry and str(current_entry.get("hash", "") or "") == hash_:
        return {
            "comp": float(current_entry.get("comp", 0.0) or 0.0),
            "p50": float(current_entry.get("p50", 0.0) or 0.0),
            "p25": float(current_entry.get("p25", 0.0) or 0.0),
            "lcb": float(current_entry.get("lcb", 0.0) or 0.0),
            "n": int(current_entry.get("n_roll", 0) or 0),
        }
    metrics = calc_strategy_metrics((rolling.get(hash_, {}) or {}).get("scores", []) or [])
    if not metrics:
        return None
    return {
        "comp": float(metrics.get("comp", 0.0) or 0.0),
        "p50": float(metrics.get("p50", 0.0) or 0.0),
        "p25": float(metrics.get("p25", 0.0) or 0.0),
        "lcb": float(metrics.get("lcb", 0.0) or 0.0),
        "n": int(metrics.get("n", 0) or 0),
    }


def derive_branch_best(rolling, active_branch, current_hash, current_entry=None):
    best_hash = str(active_branch.get("best_hash", "") or "")
    best_blob = active_branch.get("best", {}) if isinstance(active_branch.get("best"), dict) else {}
    best_metrics = None
    if best_hash:
        best_metrics = {
            "comp": float(best_blob.get("comp", 0.0) or 0.0),
            "p50": float(best_blob.get("p50", 0.0) or 0.0),
            "p25": float(best_blob.get("p25", 0.0) or 0.0),
            "lcb": float(best_blob.get("lcb", 0.0) or 0.0),
            "n": int(best_blob.get("n", 0) or 0),
        }
        if best_metrics["n"] <= 0:
            best_metrics = None

    seen = set()
    lineage = [str(x) for x in (active_branch.get("lineage", []) or []) if str(x)]
    for cand_hash in lineage + ([current_hash] if current_hash else []):
        if not cand_hash or cand_hash in seen:
            continue
        seen.add(cand_hash)
        cand_metrics = strategy_metrics_for_hash(rolling, cand_hash, current_entry=current_entry)
        if not cand_metrics:
            continue
        if best_metrics is None or metric_key(cand_metrics) > metric_key(best_metrics):
            best_hash = cand_hash
            best_metrics = cand_metrics

    if best_metrics is None and current_entry:
        best_hash = current_hash
        best_metrics = {
            "comp": float(current_entry.get("comp", 0.0) or 0.0),
            "p50": float(current_entry.get("p50", 0.0) or 0.0),
            "p25": float(current_entry.get("p25", 0.0) or 0.0),
            "lcb": float(current_entry.get("lcb", 0.0) or 0.0),
            "n": int(current_entry.get("n_roll", 0) or 0),
        }
    return best_hash, best_metrics


def inspect_branch_state(rolling, current_hash, anchor=None):
    current_entry = get_current_strategy_run_entry(current_hash)
    if not current_hash or not current_entry:
        return {
            "available": False,
            "state": "unknown",
            "verdict": "N/A",
            "text": "RegPreview N/A current not tracked",
        }
    current = {
        "comp": current_entry["comp"],
        "p50": current_entry["p50"],
        "p25": current_entry["p25"],
        "n": current_entry["n_roll"],
    }
    anchor_payload = anchor or load_best_anchor()
    if current_entry["n_roll"] <= 0:
        if anchor_payload and anchor_payload.get("hash"):
            anchor_hash = str(anchor_payload.get("hash", "") or "")
            anchor_metrics = {
                "comp": float(anchor_payload.get("comp", 0.0) or 0.0),
                "p50": float(anchor_payload.get("p50", 0.0) or 0.0),
                "p25": float(anchor_payload.get("p25", 0.0) or 0.0),
                "n": int(anchor_payload.get("n", 0) or 0),
            }
            return {
                "available": True,
                "state": "unknown",
                "verdict": "WAIT",
                "text": f"RegPreview WAIT anchor={anchor_hash[:8]} n=0/{MIN_GAMES_BEFORE_REGRESSION}",
                "current_hash": current_hash,
                "current": current,
                "anchor_hash": anchor_hash,
                "anchor": anchor_metrics,
                "branch_active": False,
                "active_branch": {},
                "current_gap": {"comp": 0.0, "p50": 0.0, "p25": 0.0},
                "current_breach_count": 0,
                "hard_breach_count": 0,
                "best_hash": "",
                "best": None,
                "best_gap": None,
                "best_breach_count": 0,
                "depth": 0,
                "closed_games": 0,
                "branch_games": 0,
                "patience": 0,
                "budget_hit": [],
            }
        return {
            "available": True,
            "state": "unknown",
            "verdict": "N/A",
            "text": "RegPreview N/A n=0 no anchor",
            "current_hash": current_hash,
            "current": current,
        }
    if not anchor_payload or not anchor_payload.get("hash"):
        return {
            "available": True,
            "state": "safe",
            "verdict": "NO",
            "text": "RegPreview NO no anchor",
            "current_hash": current_hash,
            "current": current,
        }

    active_branch = load_active_branch() or {}
    branch_active = (
        str(active_branch.get("head_hash", "") or "") == current_hash
        and str(active_branch.get("anchor_hash", "") or "")
    )

    anchor_hash = str(anchor_payload.get("hash", "") or "")
    anchor_metrics = {
        "comp": float(anchor_payload.get("comp", 0.0) or 0.0),
        "p50": float(anchor_payload.get("p50", 0.0) or 0.0),
        "p25": float(anchor_payload.get("p25", 0.0) or 0.0),
        "n": int(anchor_payload.get("n", 0) or 0),
    }
    if branch_active:
        anchor_hash = str(active_branch.get("anchor_hash", "") or anchor_hash)
        anchor_blob = active_branch.get("anchor", {}) if isinstance(active_branch.get("anchor"), dict) else {}
        anchor_metrics = {
            "comp": float(anchor_blob.get("comp", anchor_metrics["comp"]) or 0.0),
            "p50": float(anchor_blob.get("p50", anchor_metrics["p50"]) or 0.0),
            "p25": float(anchor_blob.get("p25", anchor_metrics["p25"]) or 0.0),
            "n": int(anchor_blob.get("n", anchor_metrics["n"]) or 0),
        }
    comp_gap, p50_gap, p25_gap = metric_gaps(anchor_metrics, current)
    breach_count = metric_breach_count(
        comp_gap, p50_gap, p25_gap,
        REGRESSION_MIN_COMP_GAP, REGRESSION_MIN_P50_GAP, REGRESSION_MIN_P25_GAP,
    )
    hard_breach_count = metric_breach_count(
        comp_gap, p50_gap, p25_gap,
        BRANCH_HARD_COMP_GAP, BRANCH_HARD_P50_GAP, BRANCH_HARD_P25_GAP,
    )
    info = {
        "available": True,
        "state": "safe",
        "verdict": "NO",
        "current_hash": current_hash,
        "current": current,
        "anchor_hash": anchor_hash,
        "anchor": anchor_metrics,
        "branch_active": bool(branch_active),
        "active_branch": active_branch,
        "current_gap": {
            "comp": comp_gap,
            "p50": p50_gap,
            "p25": p25_gap,
        },
        "current_breach_count": breach_count,
        "hard_breach_count": hard_breach_count,
        "best_hash": "",
        "best": None,
        "best_gap": None,
        "best_breach_count": 0,
        "depth": 0,
        "closed_games": 0,
        "branch_games": int(current["n"]),
        "patience": 0,
        "budget_hit": [],
        "text": "",
    }

    if branch_active:
        best_hash, best_metrics = derive_branch_best(rolling, active_branch, current_hash, current_entry=current_entry)
        best_comp_gap, best_p50_gap, best_p25_gap = metric_gaps(anchor_metrics, best_metrics or current)
        best_breach_count = metric_breach_count(
            best_comp_gap, best_p50_gap, best_p25_gap,
            REGRESSION_MIN_COMP_GAP, REGRESSION_MIN_P50_GAP, REGRESSION_MIN_P25_GAP,
        )
        depth = int(active_branch.get("depth", 0) or 0)
        closed_games = int(active_branch.get("closed_games", 0) or 0)
        patience = int(active_branch.get("patience", 0) or 0)
        branch_games = closed_games + int(current["n"])
        budget_hit = []
        if depth >= BRANCH_MAX_DEPTH:
            budget_hit.append("d")
        if branch_games >= BRANCH_MAX_GAMES:
            budget_hit.append("g")
        if patience >= BRANCH_PATIENCE:
            budget_hit.append("p")
        info["best_hash"] = best_hash
        info["best"] = best_metrics
        info["best_gap"] = {
            "comp": best_comp_gap,
            "p50": best_p50_gap,
            "p25": best_p25_gap,
        }
        info["best_breach_count"] = best_breach_count
        info["depth"] = depth
        info["closed_games"] = closed_games
        info["branch_games"] = branch_games
        info["patience"] = patience
        info["budget_hit"] = budget_hit

    if current_hash == anchor_hash and not branch_active:
        info["text"] = f"RegPreview NO anchor={anchor_hash[:8]} n={current['n']}"
        return info

    if current["n"] < MIN_GAMES_BEFORE_REGRESSION:
        if breach_count >= REGRESSION_MIN_BREACH_COUNT:
            info["state"] = "warning"
            info["verdict"] = "WARN"
            info["text"] = (
                f"RegPreview WARN anchor={anchor_hash[:8]}"
                f" gap=c{int(round(comp_gap))}/m{int(round(p50_gap))}/q{int(round(p25_gap))}"
                f" br={breach_count}/{REGRESSION_MIN_BREACH_COUNT}"
                f" n={current['n']}/{MIN_GAMES_BEFORE_REGRESSION}"
            )
            return info
        info["verdict"] = "WAIT"
        info["text"] = f"RegPreview WAIT anchor={anchor_hash[:8]} n={current['n']}/{MIN_GAMES_BEFORE_REGRESSION}"
        return info

    if metric_key(current) > metric_key(anchor_metrics):
        info["verdict"] = "PROMOTE"
        info["text"] = f"RegPreview PROMOTE anchor={anchor_hash[:8]} n={current['n']}"
        return info

    if not branch_active:
        if hard_breach_count >= BRANCH_HARD_MIN_BREACH_COUNT:
            info["state"] = "trigger"
            info["verdict"] = "YES"
            info["text"] = (
                f"RegPreview YES anchor={anchor_hash[:8]}"
                f" hard gap=c{int(round(comp_gap))}/m{int(round(p50_gap))}/q{int(round(p25_gap))}"
                f" br={breach_count}/{REGRESSION_MIN_BREACH_COUNT}"
                f" n={current['n']}"
            )
            return info
        info["text"] = (
            f"RegPreview NO anchor={anchor_hash[:8]}"
            f" gap=c{int(round(comp_gap))}/m{int(round(p50_gap))}/q{int(round(p25_gap))}"
            f" br={breach_count}/{REGRESSION_MIN_BREACH_COUNT}"
            f" n={current['n']}"
        )
        return info

    best_hash = str(info.get("best_hash", "") or current_hash)
    best_metrics = info.get("best") or dict(current)
    best_gap = info.get("best_gap") or {"comp": 0.0, "p50": 0.0, "p25": 0.0}
    best_comp_gap = float(best_gap.get("comp", 0.0) or 0.0)
    best_p50_gap = float(best_gap.get("p50", 0.0) or 0.0)
    best_p25_gap = float(best_gap.get("p25", 0.0) or 0.0)
    best_breach_count = int(info.get("best_breach_count", 0) or 0)
    depth = int(info.get("depth", 0) or 0)
    closed_games = int(info.get("closed_games", 0) or 0)
    patience = int(info.get("patience", 0) or 0)
    branch_games = int(info.get("branch_games", int(current["n"])) or int(current["n"]))
    budget_hit = list(info.get("budget_hit", []) or [])
    budget_text = f" depth={depth}/{BRANCH_MAX_DEPTH} games={branch_games}/{BRANCH_MAX_GAMES} patience={patience}/{BRANCH_PATIENCE}"

    if hard_breach_count >= BRANCH_HARD_MIN_BREACH_COUNT:
        info["state"] = "trigger"
        info["verdict"] = "YES"
        info["text"] = (
            f"RegPreview YES anchor={anchor_hash[:8]}"
            f" hard gap=c{int(round(comp_gap))}/m{int(round(p50_gap))}/q{int(round(p25_gap))}"
            f" br={breach_count}/{REGRESSION_MIN_BREACH_COUNT}"
            f"{budget_text} n={current['n']}"
        )
        return info
    if budget_hit and best_breach_count >= REGRESSION_MIN_BREACH_COUNT:
        info["state"] = "trigger"
        info["verdict"] = "YES"
        info["text"] = (
            f"RegPreview YES anchor={anchor_hash[:8]}"
            f" gap=c{int(round(comp_gap))}/m{int(round(p50_gap))}/q{int(round(p25_gap))}"
            f" br={breach_count}/{REGRESSION_MIN_BREACH_COUNT}"
            f" best={best_hash[:8]} bc{int(round(best_comp_gap))}/bm{int(round(best_p50_gap))}/bq{int(round(best_p25_gap))}"
            f" bbr={best_breach_count}/{REGRESSION_MIN_BREACH_COUNT}"
            f"{budget_text} n={current['n']}"
        )
        return info
    if budget_hit:
        info["verdict"] = "RESET"
        info["text"] = f"RegPreview RESET anchor={anchor_hash[:8]} best={best_hash[:8]}{budget_text} n={current['n']}"
        return info
    info["text"] = (
        f"RegPreview NO anchor={anchor_hash[:8]}"
        f" gap=c{int(round(comp_gap))}/m{int(round(p50_gap))}/q{int(round(p25_gap))}"
        f" br={breach_count}/{REGRESSION_MIN_BREACH_COUNT}"
        f"{budget_text} n={current['n']}"
    )
    return info


def calc_regression_status(rolling, current_hash, scores, anchor=None):
    info = inspect_branch_state(rolling, current_hash, anchor=anchor)
    return {
        "state": info.get("state", "unknown"),
        "text": info.get("text", "RegPreview N/A"),
    }


def load_game_state():
    p = Path("game_state.json")
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text())
    except Exception:
        return {}


def load_latest_drop():
    p = Path("game_history/latest.jsonl")
    if not p.exists():
        return ""

    last = ""
    try:
        with p.open(encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    last = line
    except Exception:
        return ""

    if not last:
        return ""

    try:
        d = json.loads(last)
    except Exception:
        return ""

    def number(value, digits=2, signed=False):
        try:
            x = float(value)
        except Exception:
            return "?"
        if abs(x - round(x)) < 1e-9:
            return f"{int(round(x)):+d}" if signed else str(int(round(x)))
        sign = "+" if signed else ""
        return f"{x:{sign}.{digits}f}"

    reason = re.sub(r"\s+", "_", str(d.get("decision_reason", "") or "")).strip("_")
    labels = []
    try:
        src = Path("strategy.py").read_text(encoding="utf-8")
        labels = re.findall(r"reasons\.append\(\s*['\"]([^'\"]+)['\"]\s*\)", src)
    except Exception:
        labels = []
    labels = sorted(set(labels), key=len, reverse=True)

    matches = [label for label in labels if label and label in reason]
    noise_words = ("PENALTY", "CROSSES_DEADLINE_NO_MERGE")
    decision = next((label for label in matches if not any(word in label for word in noise_words)), "")
    if not decision:
        decision = matches[0] if matches else (reason or "?")
    turn_flag = "!" if (d.get("deadline_crossed") or d.get("decision_crosses_deadline")) else ""
    parts = [
        f"T{d.get('turn', '?')}{turn_flag}",
        f"x={number(d.get('decision_x'), 2, signed=True)}",
        f"D={decision}",
    ]
    return " ".join(parts)


def get_strategy_hash():
    try:
        r = subprocess.run(
            ["python3", "extract_decide_hash.py", "strategy.py"],
            capture_output=True, text=True, timeout=5
        )
        return r.stdout.strip()
    except Exception:
        return ""


def load_decision_reasons(n_files=50):
    files = sorted(glob("game_history/*.jsonl"), reverse=True)[:n_files]
    reasons = []
    for fp in files:
        if os.path.basename(fp) == "latest.jsonl":
            continue
        try:
            with open(fp) as f:
                for line in f:
                    d = json.loads(line)
                    r = d.get("decision_reason", "")
                    if r:
                        reasons.append(r)
        except Exception:
            pass
    return reasons


def get_strategy_version():
    files = sorted(glob("strategy_versions/v[0-9]*_strategy.py"), reverse=True)
    return os.path.basename(files[0]) if files else "?"


def get_strategy_lines():
    try:
        return sum(1 for _ in open("strategy.py"))
    except Exception:
        return 0


def get_accumulated_count(current_hash=""):
    p = Path("tmp/state/accumulated_games.json")
    if not p.exists():
        return 0
    try:
        data = json.loads(p.read_text())
        acc_hash = data.get("hash", "")
        if current_hash and (not acc_hash or acc_hash != current_hash):
            return 0
        return data.get("count", 0)
    except Exception:
        return 0


def get_rejected_count():
    return len(load_rejected_hashes())


def load_improve_state():
    p = Path("tmp/state/improve_state.json")
    monitor_p = Path("tmp/state/improve_monitor_status.json")
    base = {
        "status": "idle",
        "pid": 0,
        "phase": "",
        "progress": 0,
        "alive": False,
        "state_activity_fresh": False,
        "monitor_stale_sec": 0,
    }
    if not p.exists():
        return base
    try:
        d = json.loads(p.read_text())
    except Exception:
        return base

    base["status"] = d.get("status", "idle")
    try:
        base["pid"] = int(d.get("pid", 0) or 0)
    except Exception:
        base["pid"] = 0
    base["phase"] = str(d.get("phase", "") or "")
    try:
        base["progress"] = int(float(d.get("progress", 0) or 0))
    except Exception:
        base["progress"] = 0
    base["progress"] = max(0, min(100, base["progress"]))

    if base["status"] == "running" and base["pid"] > 0:
        try:
            os.kill(base["pid"], 0)
            cmd = subprocess.run(
                ["ps", "-p", str(base["pid"]), "-o", "command="],
                capture_output=True, text=True, timeout=2
            ).stdout.strip()
            base["alive"] = "eloop_improve" in cmd
        except Exception:
            base["alive"] = False

    try:
        monitor = json.loads(monitor_p.read_text())
        if (
            base["status"] == "running"
            and monitor.get("status") == "running"
            and monitor.get("action") == "state_activity_fresh"
        ):
            base["state_activity_fresh"] = True
            try:
                base["monitor_stale_sec"] = int(monitor.get("stale_sec", 0) or 0)
            except Exception:
                base["monitor_stale_sec"] = 0
    except Exception:
        pass

    return base


def fmt_age(seconds):
    seconds = max(0, int(seconds or 0))
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m"
    return f"{seconds // 3600}h"


def load_viewer_chat_monitor():
    p = Path(VIEWER_CHAT_MONITOR_FILE)
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8", errors="ignore"))
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    latest = str(data.get("latest", "") or "").strip()
    recent = data.get("recent", []) or []
    if not latest and isinstance(recent, list) and recent:
        latest = str(recent[-1] or "").strip()
    if not latest:
        return None
    try:
        age = fmt_age(int(time.time()) - int(data.get("epoch", 0) or 0))
    except Exception:
        age = ""
    return {
        "latest": latest[:96],
        "count": int(data.get("count", 0) or 0),
        "age": age,
    }


def load_improve_backoff_status():
    p = Path(os.getenv("IMPROVE_RATE_LIMIT_BACKOFF_FILE", "tmp/state/rate_limit_backoff"))
    if not p.exists():
        return None
    try:
        lines = p.read_text(encoding="utf-8", errors="ignore").splitlines()
        count = int(lines[0]) if len(lines) > 0 else 1
        ts = int(lines[1]) if len(lines) > 1 else 0
    except Exception:
        return None
    exp = min(max(count - 1, 0), 5)
    wait = 300 * (1 << exp)
    remaining = max(0, wait - max(0, int(time.time()) - ts))
    return {
        "count": count,
        "remaining": fmt_age(remaining),
        "wait": fmt_age(wait),
    }


def load_soren91_improve_watchdog_status():
    lock_path = Path(SOREN91_IMPROVE_LOCK_FILE)
    pid_path = Path(SOREN91_IMPROVE_PID_FILE)
    q_path = Path(SOREN91_IMPROVE_HUNG_QUARANTINE_FILE)
    now = int(time.time())
    status = None

    if lock_path.exists():
        try:
            lock_age = max(0, now - int(lock_path.stat().st_mtime))
        except Exception:
            lock_age = 0
        pid = ""
        if pid_path.exists():
            try:
                pid = pid_path.read_text(encoding="utf-8", errors="ignore").strip()
            except Exception:
                pid = ""
        status = {
            "kind": "lock",
            "label": f"lock {fmt_age(lock_age)} pid={pid or '?'}",
            "age_sec": lock_age,
        }

    last = None
    if q_path.exists():
        try:
            for raw in q_path.read_text(encoding="utf-8", errors="ignore").splitlines():
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    row = json.loads(raw)
                except Exception:
                    continue
                if isinstance(row, dict):
                    last = row
        except Exception:
            last = None

    if last:
        try:
            age = max(0, now - int(last.get("epoch", 0) or 0))
        except Exception:
            age = 0
        reason = str(last.get("reason", "") or last.get("event", "") or "unknown")
        pid = last.get("pid")
        pid_text = "?" if pid in (None, "") else str(pid)
        q_label = f"last {reason} {fmt_age(age)} pid={pid_text}"
        if status:
            status["last_label"] = q_label
            return status
        return {
            "kind": "last",
            "label": q_label,
            "age_sec": age,
        }

    return status


def load_latest_annealing_candidate():
    p = Path(ANNEALING_OBSERVE_FILE)
    if not p.exists():
        return None
    last = None
    try:
        for raw in p.read_text(encoding="utf-8", errors="ignore").splitlines():
            raw = raw.strip()
            if not raw:
                continue
            try:
                row = json.loads(raw)
            except Exception:
                continue
            if isinstance(row, dict):
                last = row
    except Exception:
        return None
    if not last:
        return None
    try:
        age = fmt_age(int(time.time()) - int(last.get("epoch", 0) or 0))
    except Exception:
        age = ""
    return {
        "hash": str(last.get("hash", "") or "")[:8],
        "prob": float(last.get("accept_probability", 0.0) or 0.0),
        "gap": int(float(last.get("comp_gap", 0.0) or 0.0)),
        "temp": int(float(last.get("temperature", 0.0) or 0.0)),
        "age": age,
    }


def load_wildcard_attempt_status():
    p = Path(WILDCARD_ATTEMPT_STATE_FILE)
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8", errors="ignore"))
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    try:
        streak = int(data.get("consecutive_wildcards", 0) or 0)
    except Exception:
        streak = 0
    try:
        escalate_at = int(os.getenv("WILDCARD_AI_ESCALATE_STREAK", "3") or 3)
    except Exception:
        escalate_at = 3
    try:
        archive_restart_at = int(os.getenv("ARCHIVE_RESTART_STREAK", "3") or 3)
    except Exception:
        archive_restart_at = 3
    archive_restart_enabled = os.getenv("ARCHIVE_RESTART_ENABLED", "1") == "1"
    effective_streak = streak
    failed_origins = []
    try:
        origin_path = Path("tmp/state/wildcard_origin.json")
        origin = json.loads(origin_path.read_text(encoding="utf-8", errors="ignore")) if origin_path.exists() else {}
        rejected_path = Path(REJECTED_HASH_META_FILE)
        rejected = json.loads(rejected_path.read_text(encoding="utf-8", errors="ignore")) if rejected_path.exists() else {}
        rolling = load_rolling()
        anchor = load_best_anchor() or {}
        anchor_comp = float(anchor.get("comp", 0.0) or 0.0)
        for h, meta in (origin or {}).items():
            is_failed = h in rejected
            metrics = calc_strategy_metrics((rolling.get(h) or {}).get("scores", []) or [])
            try:
                max_games = int((meta or {}).get("max_games_override", 12) or 12)
            except Exception:
                max_games = 12
            if (
                not is_failed
                and metrics
                and metrics.get("n", 0) >= max_games
                and anchor_comp > 0
                and metrics.get("comp", 0.0) < anchor_comp
            ):
                is_failed = True
            if is_failed:
                failed_origins.append(str(h)[:8])
        effective_streak = max(streak, len(failed_origins))
    except Exception:
        effective_streak = streak
    applied = data.get("recent_applied_lines", []) or []
    applied_lines = []
    for raw in applied[-4:]:
        try:
            applied_lines.append(str(int(raw)))
        except Exception:
            pass
    return {
        "streak": streak,
        "effective_streak": effective_streak,
        "failed_origin_count": len(failed_origins),
        "escalate_at": escalate_at,
        "archive_restart_at": archive_restart_at,
        "archive_restart_enabled": archive_restart_enabled,
        "to_archive_restart": max(0, archive_restart_at - streak),
        "to_escape_ai": max(0, escalate_at - streak),
        "last_event": str(data.get("last_wildcard_outcome", "") or data.get("last_regression_event", "") or "none"),
        "last_hash": str(data.get("last_wildcard_outcome_hash", "") or data.get("last_regression_hash", "") or "")[:8],
        "scale": data.get("scale", 1.0),
        "lines": ",".join(applied_lines) or "none",
    }


def load_archive_restart_candidate():
    if os.getenv("ARCHIVE_RESTART_ENABLED", "1") != "1":
        return None
    rolling = load_rolling()
    anchor = load_best_anchor() or {}
    if not rolling:
        return None
    no_candidate_marker = Path(os.getenv(
        "ARCHIVE_RESTART_NO_CANDIDATE_COOLDOWN_FILE",
        ARCHIVE_RESTART_NO_CANDIDATE_COOLDOWN_FILE,
    ))
    no_candidate_cooldown = None
    if no_candidate_marker.exists():
        try:
            ttl = int(os.getenv("ARCHIVE_RESTART_NO_CANDIDATE_COOLDOWN_SEC", "900") or 900)
        except Exception:
            ttl = 900
        try:
            age = max(0, int(time.time()) - int(no_candidate_marker.stat().st_mtime))
        except Exception:
            age = ttl + 1
        if age < ttl:
            no_candidate_cooldown = {"status": "no_candidate_cooldown", "age": fmt_age(age), "ttl": fmt_age(ttl)}
    rejected = set()
    p = Path(REJECTED_HASH_META_FILE)
    if p.exists():
        try:
            rejected = set((json.loads(p.read_text(encoding="utf-8", errors="ignore")) or {}).keys())
        except Exception:
            rejected = set()
    origin_map = {}
    origin = set()
    p = Path("tmp/state/wildcard_origin.json")
    if p.exists():
        try:
            origin_map = json.loads(p.read_text(encoding="utf-8", errors="ignore")) or {}
            origin = set(origin_map.keys())
        except Exception:
            origin_map = {}
            origin = set()
    cooldown_map = {}
    cooldown = set()
    p = Path(ARCHIVE_RESTART_COOLDOWN_FILE)
    if p.exists():
        try:
            cooldown_map = json.loads(p.read_text(encoding="utf-8", errors="ignore")) or {}
            cooldown = set(cooldown_map.keys())
        except Exception:
            cooldown_map = {}
            cooldown = set()
    anchor_hash = str(anchor.get("hash", "") or "")
    try:
        anchor_comp = float(anchor.get("comp", 0.0) or 0.0)
    except Exception:
        anchor_comp = 0.0
    try:
        anchor_russia = int(anchor.get("russia_count", 0) or 0)
        anchor_soviet = int(anchor.get("soviet_count", 0) or 0)
    except Exception:
        anchor_russia = anchor_soviet = 0
    try:
        min_ratio = float(os.getenv("ARCHIVE_RESTART_MIN_COMP_RATIO", "0.92") or 0.92)
    except Exception:
        min_ratio = 0.92
    try:
        min_best_type = int(os.getenv("ARCHIVE_RESTART_MIN_BEST_TYPE", "14") or 14)
    except Exception:
        min_best_type = 14
    include_permanent = os.getenv("ARCHIVE_RESTART_INCLUDE_PERMANENT", "1").strip().lower() not in {"0", "false", "no", "off"}
    allow_origin_retry = os.getenv("ARCHIVE_RESTART_ALLOW_ORIGIN_RETRY", "1").strip().lower() not in {"0", "false", "no", "off"}

    def find_archive_path(hash_value):
        candidates = [Path(STRATEGY_HASH_ARCHIVE_DIR, f"{hash_value}.py")]
        if include_permanent:
            candidates.append(Path(STRATEGY_HASH_PERMANENT_ARCHIVE_DIR, f"{hash_value}.py"))
        for path in candidates:
            if path.exists():
                try:
                    if "BEGIN DEADLINE GUARD" not in path.read_text(encoding="utf-8", errors="ignore")[:200000]:
                        continue
                except Exception:
                    continue
                return path
        return None

    def archive_path_blocker(hash_value):
        candidates = [Path(STRATEGY_HASH_ARCHIVE_DIR, f"{hash_value}.py")]
        if include_permanent:
            candidates.append(Path(STRATEGY_HASH_PERMANENT_ARCHIVE_DIR, f"{hash_value}.py"))
        saw_file = False
        for path in candidates:
            if not path.exists():
                continue
            saw_file = True
            try:
                if "BEGIN DEADLINE GUARD" in path.read_text(encoding="utf-8", errors="ignore")[:200000]:
                    return ""
            except Exception:
                continue
        return "unstable" if saw_file else "miss"

    def is_cooled_down(hash_value):
        if hash_value not in cooldown:
            return False
        try:
            ttl = int(os.getenv("ARCHIVE_RESTART_COOLDOWN_SEC", str(ARCHIVE_RESTART_COOLDOWN_SEC)) or ARCHIVE_RESTART_COOLDOWN_SEC)
        except Exception:
            ttl = ARCHIVE_RESTART_COOLDOWN_SEC
        if ttl <= 0:
            return True
        meta = cooldown_map.get(hash_value) if isinstance(cooldown_map.get(hash_value), dict) else {}
        try:
            epoch = int(meta.get("epoch", 0) or 0)
        except Exception:
            epoch = 0
        return epoch <= 0 or (int(time.time()) - epoch) < ttl

    threshold = anchor_comp * max(0.0, min(1.0, min_ratio)) if anchor_comp > 0 else 0.0
    rows = []
    for h, entry in rolling.items():
        h = str(h)
        if not h or h == anchor_hash or h in rejected or is_cooled_down(h):
            continue
        if find_archive_path(h) is None:
            continue
        metrics = calc_strategy_metrics((entry or {}).get("scores", []) or [])
        if not metrics or metrics.get("n", 0) < MIN_GAMES_FOR_BEST_ROLLBACK:
            continue
        if metrics["comp"] < threshold:
            continue
        russia = int((entry or {}).get("russia_count", 0) or 0)
        soviet = int((entry or {}).get("soviet_count", 0) or 0)
        best_type = int((entry or {}).get("best_max_type", 0) or 0)
        if best_type >= 15 and russia <= 0:
            russia = 1
        if best_type >= 16 and soviet <= 0:
            soviet = 1
        if anchor_soviet > 0 and soviet <= 0:
            continue
        if anchor_russia > 0 and russia <= 0:
            continue
        if russia <= 0 and soviet <= 0 and best_type < min_best_type:
            continue
        origin_type = str((origin_map.get(h) or {}).get("origin_type") or "") if isinstance(origin_map.get(h), dict) else ("legacy_origin" if h in origin else "")
        if origin_type and not (allow_origin_retry and (russia > 0 or soviet > 0 or best_type >= min_best_type)):
            continue
        objective_score = soviet * 100000 + russia * 12000 + max(0, best_type - 13) * 2500
        objective_score += float(metrics.get("p25", 0.0) or 0.0) * 0.08 + float(metrics.get("comp", 0.0) or 0.0)
        rows.append((objective_score, h, metrics, russia, soviet, best_type, origin_type))
    rows.sort(reverse=True)
    if not rows:
        if no_candidate_cooldown is not None:
            return no_candidate_cooldown
        blockers = {}

        def bump(name):
            blockers[name] = blockers.get(name, 0) + 1

        for h, entry in rolling.items():
            h = str(h)
            if not h or h == anchor_hash:
                continue
            metrics = calc_strategy_metrics((entry or {}).get("scores", []) or [])
            if not metrics or metrics.get("n", 0) < MIN_GAMES_FOR_BEST_ROLLBACK:
                continue
            if metrics["comp"] < threshold:
                continue
            try:
                russia = int((entry or {}).get("russia_count", 0) or 0)
                soviet = int((entry or {}).get("soviet_count", 0) or 0)
                best_type = int((entry or {}).get("best_max_type", 0) or 0)
            except Exception:
                russia = soviet = best_type = 0
            if best_type >= 15 and russia <= 0:
                russia = 1
            if best_type >= 16 and soviet <= 0:
                soviet = 1
            if russia <= 0 and soviet <= 0 and best_type < min_best_type:
                continue
            if h in rejected:
                bump("reject")
                continue
            if is_cooled_down(h):
                bump("cool")
                continue
            path_blocker = archive_path_blocker(h)
            if path_blocker:
                bump(path_blocker)
                continue
            if anchor_soviet > 0 and soviet <= 0:
                bump("S0")
                continue
            if anchor_russia > 0 and russia <= 0:
                bump("R0")
                continue
            origin_type = str((origin_map.get(h) or {}).get("origin_type") or "") if isinstance(origin_map.get(h), dict) else ("legacy_origin" if h in origin else "")
            if origin_type and not (allow_origin_retry and (russia > 0 or soviet > 0 or best_type >= min_best_type)):
                bump("origin")
        return {
            "status": "no_candidate",
            "threshold": int(round(threshold)),
            "min_best_type": min_best_type,
            "blockers": blockers,
        }
    candidates = []
    for _, row_h, row_metrics, row_russia, row_soviet, row_best_type, row_origin_type in rows[:10]:
        candidates.append({
            "hash": row_h[:8],
            "comp": int(round(row_metrics.get("comp", 0.0))),
            "p25": int(round(row_metrics.get("p25", 0.0))),
            "n": int(row_metrics.get("n", 0) or 0),
            "russia": row_russia,
            "soviet": row_soviet,
            "best_type": row_best_type,
            "origin_retry": bool(row_origin_type),
        })
    _, h, metrics, russia, soviet, best_type, origin_type = rows[0]
    return {
        "hash": h[:8],
        "comp": int(round(metrics.get("comp", 0.0))),
        "p25": int(round(metrics.get("p25", 0.0))),
        "n": int(metrics.get("n", 0) or 0),
        "russia": russia,
        "soviet": soviet,
        "best_type": best_type,
        "count": len(rows),
        "origin_retry": bool(origin_type),
        "candidates": candidates,
    }


# ── Panel renderers ───────────────────────────────────────────

def render_header(scores, game_state, latest_drop, strat_hash, strat_ver,
                  strat_lines, rejected, accumulated, improve, rolling):
    game_count = len(scores)
    best = max(scores) if scores else 0
    avg_all = int(sum(scores) / len(scores)) if scores else 0

    recent30 = scores[-30:] if len(scores) >= 30 else scores
    avg_recent = int(sum(recent30) / len(recent30)) if recent30 else 0

    trend_pct = ""
    if avg_all > 0 and len(scores) >= 30:
        diff = (avg_recent - avg_all) / avg_all * 100
        arrow = "▲" if diff >= 0 else "▼"
        trend_color = C_GREEN if diff >= 0 else C_RED
        trend_pct = f"{trend_color}{arrow}{diff:+.0f}%{RST}"

    state = game_state.get("state", "?")
    gscore = game_state.get("score", 0)
    gpieces = len(game_state.get("pieces", []))

    state_color = C_GREY
    if state == "MOVE":
        state_color = C_GREEN
    elif state in ("GAMEOVER", "STOP"):
        state_color = C_RED

    live_extra = ""
    if accumulated > 0:
        live_extra = f"  {C_YELLOW}♦ {accumulated} queued{RST}"

    lines = []
    inner = W - 3  # │ + content(inner) + space + │ = W
    lines.append(f"{C_CYAN}┌{'─' * (W - 2)}┐{RST}")

    stream_backend = str(os.getenv("SOREN_STREAM_BACKEND", "obs") or "obs").strip().lower()
    if stream_backend not in {"obs", "ffmpeg"}:
        stream_backend = "invalid"
    row1 = f" SOREN/{stream_backend.upper()}  #{game_count} games   Best:{best}   Avg:{avg_all}"
    lines.append(f"{C_CYAN}│{RST}{BOLD}{row1:<{inner}}{RST} {C_CYAN}│{RST}")

    # Row 2: Recent30, Trend, Rejected
    r2_parts = [f" Recent30:{avg_recent}"]
    if trend_pct:
        r2_parts.append(f"  Trend:{trend_pct}")
    if rejected > 0:
        r2_parts.append(f"  Rejected:{rejected}")
    # For ANSI-safe padding, build raw text separately
    r2_raw = f" Recent30:{avg_recent}"
    if avg_all > 0 and len(scores) >= 30:
        diff = (avg_recent - avg_all) / avg_all * 100
        arrow_raw = "^" if diff >= 0 else "v"
        r2_raw += f"  Trend:{arrow_raw}{diff:+.0f}%"
    if rejected > 0:
        r2_raw += f"  Rejected:{rejected}"
    r2_display = "".join(r2_parts)
    pad2 = inner - len(r2_raw)
    lines.append(f"{C_CYAN}│{RST}{r2_display}{' ' * max(pad2, 0)} {C_CYAN}│{RST}")

    hash_short = strat_hash[:8] if strat_hash else "?"
    ver_num = ""
    if strat_ver and strat_ver != "?":
        ver_num = strat_ver.split("_")[0]  # e.g. "v775"
    r3_raw = f" Strategy: {hash_short}  {ver_num}  {strat_lines}L"
    r3_display = f"{DIM}{r3_raw}{RST}"
    if improve.get("status") == "running":
        phase = (improve.get("phase") or "running").replace("_", "-")
        if len(phase) > 10:
            phase = phase[:10]
        if improve.get("alive"):
            imp_raw = f"  Imp:{improve.get('progress', 0):>3}% {phase}"
            imp_disp = f"  {C_YELLOW}Imp:{improve.get('progress', 0):>3}% {phase}{RST}"
        elif improve.get("state_activity_fresh"):
            imp_raw = f"  Imp:{improve.get('progress', 0):>3}% {phase} log"
            imp_disp = f"  {C_YELLOW}Imp:{improve.get('progress', 0):>3}% {phase} log{RST}"
        else:
            imp_raw = f"  Imp:stale {phase}"
            imp_disp = f"  {C_RED}Imp:stale {phase}{RST}"
        r3_raw += imp_raw
        r3_display += imp_disp
    pad3 = inner - len(r3_raw)
    lines.append(f"{C_CYAN}│{RST}{r3_display}{' ' * max(pad3, 0)} {C_CYAN}│{RST}")

    # Row 4: live game state
    r4_raw = f" Live: {state}  score={gscore}  pieces={gpieces}"
    if accumulated > 0:
        r4_raw_nocolor = r4_raw + f"  ♦ {accumulated} queued"
    else:
        r4_raw_nocolor = r4_raw
    r4_display = f" Live: {state_color}{state}{RST}  score={gscore}  pieces={gpieces}{live_extra}"
    pad4 = inner - len(r4_raw_nocolor)
    lines.append(f"{C_CYAN}│{RST}{r4_display}{' ' * max(pad4, 0)} {C_CYAN}│{RST}")

    if latest_drop:
        label = " LastDrop: "
        drop_text = compact_regpreview_text(str(latest_drop), inner - len(label))
        drop_raw = f"{label}{drop_text}"
        drop_display = f"{label}{DIM}{drop_text}{RST}"
        pad_drop = inner - len(drop_raw)
        lines.append(f"{C_CYAN}│{RST}{drop_display}{' ' * max(pad_drop, 0)} {C_CYAN}│{RST}")

    anchor = load_best_anchor()
    ranked = []
    current_metrics = None
    for h, data in rolling.items():
        metrics = calc_strategy_metrics(data.get("scores", []))
        if not metrics:
            continue
        if metrics["n"] < 12:
            continue
        row = (metrics["comp"], metrics["p50"], metrics["p25"], metrics["n"], h, metrics)
        ranked.append(row)
    current_entry = get_current_strategy_run_entry(strat_hash)
    if current_entry and current_entry["n_roll"] > 0:
        current_metrics = {
            "n": current_entry["n_roll"],
            "comp": current_entry["comp"],
            "p50": current_entry["p50"],
            "p25": current_entry["p25"],
        }
    best_ref = pick_best_reference(rolling, strat_hash, anchor=anchor)
    if best_ref:
        _, _, _, _, best_hash, best_metrics, best_source = best_ref
        if current_metrics:
            best_short = best_hash[:8]
            curr_tag = "Curr*" if current_metrics["n"] < 12 else "Curr"
            curr_raw = (
                f" {curr_tag} c{int(current_metrics['comp'])} m{int(current_metrics['p50'])} "
                f"q{int(current_metrics['p25'])} n{int(current_metrics['n'])}"
            )
            curr_disp = (
                f" {C_YELLOW}{curr_tag}{RST} c{int(current_metrics['comp'])} "
                f"m{int(current_metrics['p50'])} q{int(current_metrics['p25'])} "
                f"n{int(current_metrics['n'])}"
            )
            pad5 = inner - len(curr_raw)
            lines.append(f"{C_CYAN}│{RST}{curr_disp}{' ' * max(pad5, 0)} {C_CYAN}│{RST}")
            best_raw = (
                f" Best  {best_short}/{best_source[:1]} c{int(best_metrics['comp'])} "
                f"m{int(best_metrics['p50'])} q{int(best_metrics['p25'])} n{int(best_metrics['n'])}"
            )
            best_disp = (
                f" {C_GREEN}Best{RST}  {best_short}/{best_source[:1]} c{int(best_metrics['comp'])} "
                f"m{int(best_metrics['p50'])} q{int(best_metrics['p25'])} n{int(best_metrics['n'])}"
            )
            pad6 = inner - len(best_raw)
            lines.append(f"{C_CYAN}│{RST}{best_disp}{' ' * max(pad6, 0)} {C_CYAN}│{RST}")
    elif current_metrics:
        curr_tag = "Curr*" if current_metrics["n"] < 12 else "Curr"
        curr_raw = (
            f" {curr_tag} c{int(current_metrics['comp'])} m{int(current_metrics['p50'])} "
            f"q{int(current_metrics['p25'])} n{int(current_metrics['n'])}"
        )
        curr_disp = (
            f" {C_YELLOW}{curr_tag}{RST} c{int(current_metrics['comp'])} "
            f"m{int(current_metrics['p50'])} q{int(current_metrics['p25'])} "
            f"n{int(current_metrics['n'])}"
        )
        pad5 = inner - len(curr_raw)
        lines.append(f"{C_CYAN}│{RST}{curr_disp}{' ' * max(pad5, 0)} {C_CYAN}│{RST}")

    reg = calc_regression_status(rolling, strat_hash, scores, anchor=anchor)
    if reg:
        reg_color = DIM
        if reg["state"] == "trigger":
            reg_color = C_RED
        elif reg["state"] == "warning":
            reg_color = C_RED
        elif reg["state"] == "safe":
            reg_color = C_GREEN
        elif reg["state"] == "unknown":
            reg_color = C_YELLOW
        reg_text = compact_regpreview_text(reg["text"], inner - 1)
        reg_raw = f" {reg_text}"
        reg_disp = f" {reg_color}{reg_text}{RST}"
        pad7 = inner - len(reg_text) - 1
        lines.append(f"{C_CYAN}│{RST}{reg_disp}{' ' * max(pad7, 0)} {C_CYAN}│{RST}")

    lines.append(f"{C_CYAN}└{'─' * (W - 2)}┘{RST}")
    return lines


def render_score_timeline(scores, chart_w=42, chart_h=7):
    label_w = 5  # "XXXXX"
    sep = "│"
    # Braille glyphs render a little wider in OBS/browser than in terminals.
    # Keep this below the old 50-wide graph so it fills the frame without spilling.

    if len(scores) < 3:
        lines = [f"{'':>{label_w}}{sep} {'(not enough data)':^{chart_w}}"]
        lines += [f"{'':>{label_w}}{sep}{' ' * chart_w}"] * (chart_h - 1)
        return [f"  {DIM}Score Timeline{RST}"] + lines

    window = scores[-100:]
    lo = min(window)
    hi = max(window)
    rng = max(hi - lo, 1)

    canvas = BrailleCanvas(chart_w, chart_h)
    dot_w = chart_w * 2
    dot_h = chart_h * 4

    n = len(window)
    for i, s in enumerate(window):
        dx = int(i * (dot_w - 1) / max(n - 1, 1))
        norm = (s - lo) / rng
        top_dot = int(norm * (dot_h - 1))
        for dy in range(0, top_dot + 1):
            canvas.set(dx, dot_h - 1 - dy, C_CYAN)

    braille_lines = canvas.render_lines()
    lines = [f"  {BOLD}Score Timeline{RST} {DIM}(last {n} games){RST}"]
    for i, bl in enumerate(braille_lines):
        if i == 0:
            lbl = f"{hi:>5}"
        elif i == chart_h - 1:
            lbl = f"{lo:>5}"
        else:
            lbl = ""
        lines.append(f"{C_GREY}{lbl:>5}{RST}{sep}{bl}")
    return lines


def render_score_distribution(scores, bar_w=40):
    bands = [
        (0, 500), (500, 1000), (1000, 1500), (1500, 2000),
        (2000, 2500), (2500, 3000), (3000, 3500), (3500, 99999),
    ]
    band_labels = [
        "  0-500", "500-1K", " 1K-1.5K", "1.5K-2K",
        " 2K-2.5K", "2.5K-3K", " 3K-3.5K", "  3500+",
    ]
    band_colors = [
        fg256(196), fg256(202), fg256(208), fg256(220),
        fg256(226), fg256(154), fg256(118), fg256(46),
    ]

    counts = [0] * len(bands)
    for s in scores:
        for j, (lo, hi) in enumerate(bands):
            if lo <= s < hi:
                counts[j] += 1
                break

    max_count = max(counts) if counts else 1

    lines = [f"  {BOLD}Score Distribution{RST} {DIM}(n={len(scores)}){RST}"]
    # label 8 + sep 1 + bar 40 + space + count 6 = 56~57
    for i in range(len(bands)):
        label = f"{band_labels[i]:>8}"
        bar = block_bar(counts[i], max_count, bar_w, band_colors[i])
        cnt = f"{counts[i]:>5}" if counts[i] > 0 else f"{DIM}    0{RST}"
        lines.append(f" {label}┃{bar} {cnt}")
    return lines


def render_strategy_comparison(rolling, current_hash, max_rows=7):
    bar_w = 22
    # marker1 + rank3 + space + hash8 + space + n/t6 + sep1 + bar22 + metrics
    rollback_candidates = collect_rollback_candidate_hashes(rolling, current_hash)
    sort_key = lambda e: (e["comp"], e["p50"], e["p25"], e["n_roll"])

    all_entries = ranked_mature_entries(rolling, current_hash, top=HASH_ARCHIVE_KEEP_TOP, require_restorable=True)
    current_entry = None
    provisional_current = None
    if current_hash:
        current_like = get_current_strategy_run_entry(current_hash)
        if current_like:
            if current_like["n_roll"] >= MIN_GAMES_FOR_BEST_ROLLBACK:
                current_entry = current_like
            else:
                provisional_current = current_like

    combined_entries = list(all_entries)
    if current_entry:
        combined_entries.append(current_entry)
    elif provisional_current:
        combined_entries.append(provisional_current)
    combined_entries.sort(key=sort_key, reverse=True)
    for idx, e in enumerate(combined_entries, start=1):
        e["overall_rank"] = idx

    if not all_entries:
        lines = [f"  {BOLD}Strategy Comparison{RST} {DIM}(mature n>={MIN_GAMES_FOR_BEST_ROLLBACK}){RST}"]
        if current_entry or provisional_current:
            metric_header = "comp p50  p25"
            lone_current = current_entry or provisional_current
            lone_current["rank"] = lone_current.get("overall_rank", 0)
            lines.append(f"{DIM} rk hash      n/t  │{'bar':<{bar_w}} {metric_header}{RST}")
            max_comp = max(lone_current["comp"], 1)
            n_field = f"{lone_current['n_roll']:>2}/{lone_current['n_total']:<3}"
            bar = block_bar(lone_current["comp"], max_comp, bar_w, C_GREEN)
            lines.append(
                f"►{lone_current['rank']:>2} {C_GREEN}{lone_current['h8']}{RST} {DIM}{n_field:>6}{RST}│"
                f"{bar} {int(lone_current['comp']):>4} {int(lone_current['p50']):>4} {int(lone_current['p25']):>4}"
            )
        else:
            lines.append(f"  {DIM}(no mature data){RST}")
        return lines

    for idx, e in enumerate(all_entries, start=1):
        e["rank"] = idx

    show_provisional_inline = bool(
        provisional_current and provisional_current.get("overall_rank", max_rows + 1) <= max_rows
    )
    show_current_inline = bool(
        current_entry and current_entry.get("overall_rank", max_rows + 1) <= max_rows
    )
    if show_current_inline:
        entries = sorted(all_entries + [current_entry], key=sort_key, reverse=True)[:max_rows]
        for idx, e in enumerate(entries, start=1):
            e["display_rank"] = idx
    elif show_provisional_inline:
        entries = sorted(all_entries + [provisional_current], key=sort_key, reverse=True)[:max_rows]
        for idx, e in enumerate(entries, start=1):
            e["display_rank"] = idx
    else:
        entries = all_entries[:max_rows]
        for idx, e in enumerate(entries, start=1):
            e["display_rank"] = idx

    max_comp = max(e["comp"] for e in entries) if entries else 1
    rollback_entry = next((e for e in all_entries if e["hash"] in rollback_candidates), None)

    lines = [f"  {BOLD}Strategy Comparison{RST} {DIM}(mature n>={MIN_GAMES_FOR_BEST_ROLLBACK}, rollback=*){RST}"]
    # Align with numeric columns rendered as: " {comp:>4} {p50:>4} {p25:>4}"
    # p50 label is intentionally shifted 1 column left for visual column match.
    metric_header = "comp p50  p25"
    lines.append(f"{DIM} rk hash      n/t  │{'bar':<{bar_w}} {metric_header}{RST}")

    def render_entry(e, is_current=False, rank_override=None):
        is_rollback = e["hash"] in rollback_candidates
        color = C_GREEN if is_current else (C_YELLOW if is_rollback else C_BLUE)
        marker = "►" if is_current else " "
        bar = block_bar(e["comp"], max_comp, bar_w, color)
        n_field = f"{e['n_roll']:>2}/{e['n_total']:<3}"
        rank_value = e.get("display_rank", e.get("rank", e.get("overall_rank", 0))) if rank_override is None else rank_override
        return (
            f"{marker}{rank_value:>2} {color}{e['h8']}{RST} {DIM}{n_field:>6}{RST}│"
            f"{bar} {int(e['comp']):>4} {int(e['p50']):>4} {int(e['p25']):>4}"
        )

    for e in entries:
        is_current = current_hash and e["hash"] == current_hash
        lines.append(render_entry(e, is_current=bool(is_current)))
    if rollback_entry and rollback_entry not in entries and rollback_entry is not current_entry:
        lines.append(f"{DIM} .. {'':8} {'':>6}│{'':<{bar_w}} {RST}")
        lines.append(render_entry(rollback_entry, is_current=False))
    if current_entry and not show_current_inline and current_entry not in entries:
        if not rollback_entry or rollback_entry is current_entry:
            lines.append(f"{DIM} .. {'':8} {'':>6}│{'':<{bar_w}} {RST}")
        lines.append(render_entry(current_entry, is_current=True))
    elif provisional_current and not show_provisional_inline:
        lines.append(f"{DIM} .. {'':8} {'':>6}│{'':<{bar_w}} {RST}")
        lines.append(
            render_entry(
                provisional_current,
                is_current=True,
                rank_override=provisional_current.get("overall_rank", 0),
            )
        )
    return lines


def render_wildcard_status(rolling, current_hash=""):
    """Show WILDCARD origins only while the current strategy is origin-tracked."""
    p = Path("tmp/state/wildcard_origin.json")
    if not p.exists():
        return []
    try:
        wo = json.loads(p.read_text())
    except Exception:
        return []
    if not isinstance(wo, dict) or not wo:
        return []
    wildcard_origins = {
        str(h): meta
        for h, meta in wo.items()
        if str((meta or {}).get("origin_type") or "wildcard") == "wildcard"
    }
    if not wildcard_origins:
        return []
    if not current_hash:
        return []
    current_origin_hash = next(
        (
            h for h in wildcard_origins
            if str(h).startswith(current_hash[:12]) or current_hash.startswith(str(h)[:12])
        ),
        None,
    )
    if not current_origin_hash:
        return []
    anchor = load_best_anchor() or {}
    anchor_comp = anchor.get("comp")
    anchor_h8 = str(anchor.get("hash", ""))[:8]
    anchor_label = f"{anchor_h8}={int(anchor_comp)}" if isinstance(anchor_comp, (int, float)) else "NA"
    lines = [
        f"  {BOLD}WILDCARD origins{RST} {DIM}(n/max comp delta vs {anchor_label}){RST}",
        f"{DIM}    hash     n/max     comp    p50    p25      dA{RST}",
    ]
    origin_items = list(reversed(list(wildcard_origins.items())))
    origin_items.sort(key=lambda item: 0 if item[0] == current_origin_hash else 1)
    for h, meta in origin_items[:5]:
        scores = (rolling.get(h, {}) or {}).get("scores", []) or []
        score_source = ""
        if not scores and isinstance(meta, dict):
            parallel_result = meta.get("parallel_result") or {}
            if isinstance(parallel_result, dict):
                scores = parallel_result.get("scores") or parallel_result.get("eval_scores") or []
                if scores:
                    score_source = " trial"
        m = calc_strategy_metrics(scores)
        n = len(scores)
        maxg = int((meta or {}).get("max_games_override", MIN_GAMES_FOR_BEST_ROLLBACK) or MIN_GAMES_FOR_BEST_ROLLBACK)
        is_cur = bool(current_hash and h.startswith(current_hash[:12]) or current_hash and current_hash.startswith(h[:12]))
        mark = "►" if is_cur else " "
        if m is None:
            lines.append(f"{mark} {C_BLUE}{h[:8]}{RST} {DIM}{n:>2}/{maxg:<2} scores none{RST}")
            continue
        comp = m.get("comp", 0.0)
        delta = (comp - anchor_comp) if isinstance(anchor_comp, (int, float)) else None
        if delta is None:
            delta_text = f"{DIM}   NA{RST}"
        elif delta >= 0:
            delta_text = f"{C_GREEN}{int(delta):+5d}{RST}"
        else:
            delta_text = f"{C_YELLOW}{int(delta):+5d}{RST}"
        n_color = C_GREEN if n >= maxg else DIM
        lines.append(
            f"{mark} {C_BLUE}{h[:8]}{RST} {n_color}{n:>2}/{maxg:<2}{RST}{DIM}{score_source:<6}{RST} "
            f"{int(comp):>8} {int(m.get('p50',0)):>6} {int(m.get('p25',0)):>6} {delta_text}"
        )
    return lines


def render_archive_restart_candidates():
    archive_next = load_archive_restart_candidate()
    lines = []
    if archive_next and archive_next.get("status") == "no_candidate_cooldown":
        lines.append(
            f"  {C_YELLOW}ArchiveRestart candidates{RST} "
            f"{DIM}total=0 cooldown {archive_next.get('age', '')}/{archive_next.get('ttl', '')}; escape_ai direct{RST}"
        )
    elif archive_next and archive_next.get("status") == "no_candidate":
        blockers = archive_next.get("blockers", {}) or {}
        blocker_text = " ".join(
            f"{name}={blockers.get(name)}"
            for name in ("R0", "unstable", "miss", "cool", "reject", "S0", "origin")
            if blockers.get(name)
        )
        blocker_text = f" {blocker_text}" if blocker_text else ""
        lines.append(
            f"  {C_YELLOW}ArchiveRestart candidates{RST} "
            f"{DIM}total=0 threshold c>={archive_next.get('threshold', 0)}"
            f"{blocker_text}; escape_ai direct{RST}"
        )
    elif archive_next:
        candidates = archive_next.get("candidates") or []
        total = int(archive_next.get("count", len(candidates)) or 0)
        lines.append(
            f"  {C_GREEN}ArchiveRestart candidates{RST} "
            f"{DIM}top={min(10, total)} total={total}{RST}"
        )
        lines.append(f"{DIM}    hash       comp     p25   n    ru sv  t origin{RST}")
        for idx, cand in enumerate(candidates, start=1):
            origin = "Y" if cand.get("origin_retry") else "-"
            lines.append(
                f"  {idx:>1}. {C_BLUE}{cand.get('hash', '')}{RST} "
                f"{int(cand.get('comp', 0)):>8} {int(cand.get('p25', 0)):>7} "
                f"{int(cand.get('n', 0)):>3} "
                f"{int(cand.get('russia', 0)):>3} {int(cand.get('soviet', 0)):>2} "
                f"{int(cand.get('best_type', 0)):>2}   {origin}"
            )
    return lines


def render_observer_status():
    if os.getenv("HIDE_STATUS_DASHBOARD_OBSERVER_SECTION", "0") == "1":
        return []

    lines = [f"  {BOLD}Observer Status{RST} {DIM}(annealing observe-only){RST}"]
    viewer_chat = load_viewer_chat_monitor()
    if viewer_chat:
        lines.append(
            f" {C_CYAN}ChatObs{RST} {viewer_chat.get('latest', '')} "
            f"{DIM}{viewer_chat.get('age', '')} n={viewer_chat.get('count', 0)}{RST}"
        )
    backoff = load_improve_backoff_status()
    if backoff:
        lines.append(
            f" {C_YELLOW}ImproveBackoff{RST} count={backoff.get('count', 0)} "
            f"rem={backoff.get('remaining', '')} wait={backoff.get('wait', '')}"
        )
    soren91_watchdog = load_soren91_improve_watchdog_status()
    if soren91_watchdog:
        color = C_RED if soren91_watchdog.get("kind") == "lock" else C_YELLOW
        detail = soren91_watchdog.get("label", "")
        if soren91_watchdog.get("last_label"):
            detail = f"{detail}; {soren91_watchdog.get('last_label')}"
        lines.append(f" {color}S91Improve{RST} {detail}")

    anneal = load_latest_annealing_candidate()
    if anneal:
        prob = anneal.get("prob", 0.0)
        color = C_GREEN if prob >= 0.50 else (C_YELLOW if prob >= 0.10 else C_RED)
        lines.append(
            f" {color}AnnealObs{RST} {anneal.get('hash', '')} "
            f"p={prob:.2f} gap={anneal.get('gap', 0)} temp={anneal.get('temp', 0)} "
            f"{DIM}{anneal.get('age', '')} observe-only{RST}"
        )
    else:
        lines.append(f" {DIM}AnnealObs none yet{RST}")
    wildcard = load_wildcard_attempt_status()
    if wildcard:
        streak = int(wildcard.get("streak", 0) or 0)
        failed_origin_count = int(wildcard.get("failed_origin_count", 0) or 0)
        color = C_YELLOW if streak >= 2 else C_BLUE
        if streak <= 0 and failed_origin_count:
            escape_note = f"failed-origin pool {failed_origin_count}"
        elif wildcard.get("archive_restart_enabled") and wildcard.get("to_archive_restart", 1) <= 1:
            escape_note = "archive_restart next"
        elif wildcard.get("archive_restart_enabled"):
            escape_note = f"archive_restart in {wildcard.get('to_archive_restart')}"
        else:
            escape_note = "escape_ai next" if wildcard.get("to_escape_ai", 1) <= 1 else f"escape_ai in {wildcard.get('to_escape_ai')}"
        lines.append(
            f" {color}WildStreak{RST} n={streak} "
            f"eff={wildcard.get('effective_streak', wildcard.get('streak', 0))} "
            f"last={wildcard.get('last_event', 'none')} {wildcard.get('last_hash', '')} "
            f"sc={wildcard.get('scale', 1.0)} {DIM}{escape_note}{RST}"
        )
    return lines


def render_branch_overview(rolling, current_hash):
    info = inspect_branch_state(rolling, current_hash)
    lines = [f"  {BOLD}Branch Rollback View{RST} {DIM}(anchor / best / head){RST}"]
    if not info.get("available"):
        lines.append(f"  {DIM}(current strategy not tracked){RST}")
        return lines

    anchor = info.get("anchor") or {}
    current = info.get("current") or {}
    anchor_hash = str(info.get("anchor_hash", "") or "-")
    current_hash = str(info.get("current_hash", "") or "-")
    best_hash = str(info.get("best_hash", "") or current_hash)
    best_metrics = info.get("best") or current
    branch_active = bool(info.get("branch_active"))

    verdict = str(info.get("verdict", "NO") or "NO")
    state = str(info.get("state", "safe") or "safe")
    verdict_color = C_GREEN
    if state == "trigger":
        verdict_color = C_RED
    elif state == "warning":
        verdict_color = C_RED
    elif verdict in ("WAIT", "RESET"):
        verdict_color = C_YELLOW
    elif verdict == "PROMOTE":
        verdict_color = C_GREEN
    elif branch_active:
        verdict_color = C_BLUE

    def node(label, hash8, metrics, color):
        width = 15
        comp = int(round(float(metrics.get("comp", 0.0) or 0.0)))
        n_val = int(metrics.get("n", 0) or 0)
        rows = [
            f"{color}┌{'─' * width}┐{RST}",
            f"{color}│{label.center(width)}│{RST}",
            f"{color}│{hash8[:8].center(width)}│{RST}",
            f"{color}│{f'c{comp} n{n_val}'.center(width)}│{RST}",
            f"{color}└{'─' * width}┘{RST}",
        ]
        return rows

    best_label = "Branch best" if branch_active else "Branch idle"
    best_hash_disp = best_hash if branch_active else "--"
    best_metrics_disp = best_metrics if branch_active else {"comp": 0, "n": 0}

    anchor_label = "Branch anchor" if branch_active else "Anchor top1"
    anchor_box = node(anchor_label, anchor_hash, anchor, C_GREEN)
    best_box = node(best_label, best_hash_disp, best_metrics_disp, C_BLUE if branch_active else C_GREY)
    current_box = node("Current head", current_hash, current, verdict_color)

    for idx in range(len(anchor_box)):
        lines.append(f" {anchor_box[idx]} {best_box[idx]} {current_box[idx]}")

    curr_gap = info.get("current_gap") or {"comp": 0.0, "p50": 0.0, "p25": 0.0}
    gap_line = (
        f"  A→H c{int(round(curr_gap['comp']))}/m{int(round(curr_gap['p50']))}/q{int(round(curr_gap['p25']))}"
        f"  br {int(info.get('current_breach_count', 0))}/{REGRESSION_MIN_BREACH_COUNT}"
    )
    if branch_active:
        best_gap = info.get("best_gap") or {"comp": 0.0, "p50": 0.0, "p25": 0.0}
        gap_line += (
            f"  A→B c{int(round(best_gap['comp']))}/m{int(round(best_gap['p50']))}/q{int(round(best_gap['p25']))}"
        )
    lines.append(gap_line)

    depth = int(info.get("depth", 0) or 0)
    branch_games = int(info.get("branch_games", int(current.get("n", 0) or 0)) or 0)
    patience = int(info.get("patience", 0) or 0)
    budget_line = (
        f"  budget d{block_bar(depth, BRANCH_MAX_DEPTH, 4, C_YELLOW)} {depth}/{BRANCH_MAX_DEPTH}"
        f"  g{block_bar(branch_games, BRANCH_MAX_GAMES, 4, C_YELLOW)} {branch_games}/{BRANCH_MAX_GAMES}"
        f"  p{block_bar(patience, BRANCH_PATIENCE, 4, C_YELLOW)} {patience}/{BRANCH_PATIENCE}"
    )
    lines.append(budget_line)

    lineage = []
    active_branch = info.get("active_branch") or {}
    if branch_active:
        lineage = [str(x)[:8] for x in (active_branch.get("lineage", []) or []) if str(x)]
    if lineage:
        path = " -> ".join(lineage[-4:])
        if len(path) > 50:
            path = "..." + path[-47:]
        lines.append(f"  path: {path}")
    else:
        lines.append(f"  path: {anchor_hash[:8]} -> {current_hash[:8]} {DIM}(no active branch){RST}")

    verdict_text = f"  verdict: {verdict}"
    if branch_active and info.get("budget_hit"):
        verdict_text += f" / budget={'/'.join(info['budget_hit'])}"
    elif not branch_active:
        verdict_text += " / direct anchor watch"
    lines.append(f"{verdict_color}{verdict_text}{RST}")
    return lines


# Rotating palette for dynamic color assignment
_COLOR_PALETTE = [118, 37, 75, 196, 208, 220, 141, 222, 33, 170, 82, 214, 51, 183, 46]


def _tokenize_reason(reason):
    """Split compound reason like 'NEAR_MERGE_HIGH_TOWER' into keyword tokens.

    Recognizes multi-word tokens (e.g. NEAR_MERGE, HIGH_TOWER) by greedy
    matching from left to right."""
    parts = reason.split("_")
    tokens = []
    i = 0
    while i < len(parts):
        # Try 2-word token first (e.g. NEAR+MERGE, HIGH+TOWER)
        if i + 1 < len(parts):
            two = parts[i] + "_" + parts[i + 1]
            tokens.append(two)
            i += 2
        else:
            tokens.append(parts[i])
            i += 1
    return tokens


def render_decision_patterns(reasons, max_rows=8, bar_w=30):
    # Decompose compound reasons into individual keyword tokens
    counter = Counter()
    for r in reasons:
        for token in _tokenize_reason(r):
            counter[token] += 1

    top = counter.most_common(max_rows)

    if not top:
        return [f"  {BOLD}Decision Patterns{RST}", f"  {DIM}(no data){RST}"]

    max_count = top[0][1]

    # Assign colors dynamically from palette (stable across restarts via hashlib)
    def keyword_color(kw):
        import hashlib
        h = int(hashlib.md5(kw.encode()).hexdigest(), 16)
        return fg256(_COLOR_PALETTE[h % len(_COLOR_PALETTE)])

    total = sum(c for _, c in top)
    lines = [f"  {BOLD}Decision Patterns{RST} {DIM}(recent {len(reasons)} turns){RST}"]
    # label 13 + sep 1 + bar 30 + space + count/pct 12 = 57
    for label, count in top:
        color = keyword_color(label)
        bar = block_bar(count, max_count, bar_w, color)
        pct = count / total * 100 if total > 0 else 0
        # Auto-shorten long labels: remove vowels from second word, then truncate
        disp = label
        if len(disp) > 13 and "_" in disp:
            parts = disp.split("_", 1)
            shortened = parts[1].translate(str.maketrans("", "", "AEIOU"))
            disp = parts[0] + "_" + shortened
        disp = disp[:13]
        lines.append(f" {disp:<13}│{bar} {count:>4} {DIM}{pct:>4.0f}%{RST}")
    return lines


# ── Main ──────────────────────────────────────────────────────

def main():
    os.chdir(os.path.dirname(os.path.abspath(__file__)) or ".")

    scores = load_scores()
    rolling = load_rolling()
    game_state = load_game_state()
    latest_drop = load_latest_drop() or "(no drop log)"
    strat_hash = get_strategy_hash()
    strat_ver = get_strategy_version()
    strat_lines = get_strategy_lines()
    rejected = get_rejected_count()
    accumulated = get_accumulated_count(strat_hash)
    improve = load_improve_state()
    reasons = load_decision_reasons(50)

    output = []

    output += render_header(scores, game_state, latest_drop, strat_hash, strat_ver,
                            strat_lines, rejected, accumulated, improve, rolling)
    output.append("")
    output += render_score_timeline(scores)
    output.append("")
    output += render_score_distribution(scores)
    output.append("")
    output += render_strategy_comparison(rolling, strat_hash)
    output.append("")
    output += render_observer_status()
    output.append("")
    _wc = render_wildcard_status(rolling, strat_hash)
    if _wc:
        output += _wc
    _archive = render_archive_restart_candidates()
    if _archive:
        output += _archive
    if _wc or _archive:
        output.append("")
    print("\n".join(fit_dashboard_lines(output)))


if __name__ == "__main__":
    main()
