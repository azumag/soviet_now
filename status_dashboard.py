#!/usr/bin/env python3
"""status_dashboard.py — CUI Graphical Statistics Dashboard for Soren AI

Renders 5 panels: Header, Score Timeline (braille), Score Distribution,
Strategy Comparison, Decision Patterns.
"""

import json
import math
import os
import subprocess
import sys
from collections import Counter
from glob import glob
from pathlib import Path

W = 57
RANK_LCB_Z = 1.28
RANK_WEIGHT_P50 = 0.55
RANK_WEIGHT_P25 = 0.30
RANK_WEIGHT_LCB = 0.15
MIN_GAMES_BEFORE_IMPROVE = 12
MIN_GAMES_FOR_BEST_ROLLBACK = 12
REGRESSION_COMPOSITE_RATIO = 0.88
REGRESSION_P50_RATIO = 0.85
REGRESSION_P25_RATIO = 0.80
REGRESSION_TREND_SHORT_WINDOW = 50
REGRESSION_TREND_LONG_WINDOW = 100
REGRESSION_TREND_SHORT_RATIO = 0.94
REGRESSION_TREND_LONG_RATIO = 0.95
BEST_STRATEGY_ANCHOR_FILE = "tmp/best_strategy_anchor.json"
REJECTED_HASHES_FILE = "tmp/rejected_hashes.txt"
REJECTED_HASH_META_FILE = "tmp/rejected_hash_metrics.json"
REJECTED_REEVALUATE_MIN_NEW_GAMES = 8
STRATEGY_HASH_ARCHIVE_DIR = "strategy_versions/by_hash"
STRATEGY_VERSIONS_DIR = "strategy_versions"

# ── ANSI helpers ──────────────────────────────────────────────

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


# ── Data loading ──────────────────────────────────────────────

def load_scores():
    p = Path("score_history.txt")
    if not p.exists():
        return []
    return [int(l.strip()) for l in p.read_text().splitlines() if l.strip().isdigit()]


def load_rolling():
    p = Path("tmp/rolling_scores.json")
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


def load_rejected_hashes():
    p = Path(REJECTED_HASHES_FILE)
    if not p.exists():
        return set()
    try:
        return {line.strip() for line in p.read_text().splitlines() if line.strip()}
    except Exception:
        return set()


def load_rejected_hash_meta():
    p = Path(REJECTED_HASH_META_FILE)
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text())
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


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

    by_hash_dir = Path(STRATEGY_HASH_ARCHIVE_DIR)
    if by_hash_dir.exists():
        for f in by_hash_dir.glob("*.py"):
            restorable.add(f.stem)

    candidate_files = []
    for p in (Path("tmp/revert_strategy.py"), Path("strategy.py")):
        if p.exists():
            candidate_files.append(p)
    versions_dir = Path(STRATEGY_VERSIONS_DIR)
    if versions_dir.exists():
        candidate_files.extend(sorted(versions_dir.glob("best_score*_strategy.py")))

    for f in candidate_files:
        h = compute_decide_hash(f)
        if h:
            restorable.add(h)
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


def is_recently_rejected_for_rollback(hash_, rolling, rejected_hashes, rejected_meta):
    if not hash_ or hash_ not in rejected_hashes:
        return False
    if hash_ not in rejected_meta:
        return False
    data = rolling.get(hash_)
    if not isinstance(data, dict):
        return True
    metrics = calc_strategy_metrics(data.get("scores", []))
    if not metrics:
        return True
    rejected = rejected_meta.get(hash_, {})
    rejected_comp = float(rejected.get("comp", 0.0) or 0.0)
    rejected_total = int(rejected.get("games_total", 0) or 0)
    current_total = int(data.get("games_total", len(data.get("scores", []))) or len(data.get("scores", [])))
    if current_total >= rejected_total + REJECTED_REEVALUATE_MIN_NEW_GAMES and metrics["comp"] > rejected_comp:
        return False
    return True


def collect_rollback_candidate_hashes(rolling, current_hash):
    rejected_hashes = load_rejected_hashes()
    rejected_meta = load_rejected_hash_meta()
    restorable_hashes = load_restorable_hashes()
    current_metrics = calc_strategy_metrics(rolling.get(current_hash, {}).get("scores", [])) if current_hash else None
    current_comp = current_metrics["comp"] if current_metrics else None
    candidates = set()
    for hash_, data in rolling.items():
        if not hash_ or hash_ == current_hash:
            continue
        metrics = calc_strategy_metrics(data.get("scores", []))
        if not metrics or metrics["n"] < MIN_GAMES_FOR_BEST_ROLLBACK:
            continue
        if current_comp is not None and metrics["comp"] <= current_comp:
            continue
        if hash_ not in restorable_hashes:
            continue
        if is_recently_rejected_for_rollback(hash_, rolling, rejected_hashes, rejected_meta):
            continue
        candidates.add(hash_)
    return candidates


def pick_best_reference(rolling, current_hash, anchor=None):
    ranked = []
    for h, data in rolling.items():
        if h == current_hash:
            continue
        metrics = calc_strategy_metrics(data.get("scores", []))
        if not metrics or metrics["n"] < MIN_GAMES_FOR_BEST_ROLLBACK:
            continue
        ranked.append((metrics["comp"], metrics["p50"], metrics["p25"], metrics["n"], h, metrics, "rolling"))

    best = None
    if ranked:
        ranked.sort(reverse=True)
        best = ranked[0]

    if anchor:
        anchor_hash = str(anchor.get("hash", ""))
        anchor_n = int(anchor.get("n", 0) or 0)
        if anchor_hash and anchor_hash != current_hash and anchor_n >= MIN_GAMES_FOR_BEST_ROLLBACK:
            anchor_metrics = {
                "comp": float(anchor.get("comp", 0.0)),
                "p50": float(anchor.get("p50", 0.0)),
                "p25": float(anchor.get("p25", 0.0)),
                "lcb": float(anchor.get("lcb", 0.0)),
                "n": anchor_n,
            }
            anchor_row = (
                anchor_metrics["comp"],
                anchor_metrics["p50"],
                anchor_metrics["p25"],
                anchor_metrics["n"],
                anchor_hash,
                anchor_metrics,
                "anchor",
            )
            if best is None or anchor_row[:4] > best[:4]:
                best = anchor_row
    return best


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


def calc_regression_status(rolling, current_hash, scores, anchor=None):
    best = pick_best_reference(rolling, current_hash, anchor=anchor)
    if not current_hash or current_hash not in rolling:
        if not best:
            return {
                "state": "unknown",
                "text": "RegPreview N/A current not tracked",
            }
        _, _, _, _, best_hash, _, best_source = best
        return {
            "state": "unknown",
            "text": f"RegPreview N/A vs {best_hash[:8]}({best_source}) current not tracked",
        }

    current_scores = rolling[current_hash].get("scores", [])
    current_prev_hash = str(rolling[current_hash].get("prev_hash", "") or "")
    current = calc_strategy_metrics(current_scores)
    if not current:
        if not best:
            return {
                "state": "unknown",
                "text": f"RegPreview N/A n={len(current_scores)} no best ref",
            }
        _, _, _, _, best_hash, _, best_source = best
        return {
            "state": "unknown",
                "text": f"RegPreview N/A vs {best_hash[:8]}({best_source}) n={len(current_scores)}",
            }

    if not current_prev_hash:
        return {
            "state": "safe",
            "text": f"RegPreview NO baseline(no prev_hash) n={current['n']}",
        }

    if not best:
        return {
            "state": "safe",
            "text": "RegPreview NO no best ref",
        }

    _, _, _, _, best_hash, best_metrics, best_source = best
    trigger_comp = current["comp"] < best_metrics["comp"] * REGRESSION_COMPOSITE_RATIO
    trigger_p50 = current["p50"] < best_metrics["p50"] * REGRESSION_P50_RATIO
    trigger_p25 = current["p25"] < best_metrics["p25"] * REGRESSION_P25_RATIO
    trend50, trend100 = calc_trend_flags(scores)
    trigger = (trigger_comp and (trigger_p50 or trigger_p25)) or (trend50 and trend100 and best_hash != current_hash)
    if trigger:
        reasons = []
        if trigger_comp:
            reasons.append("comp")
        if trigger_p50:
            reasons.append("p50")
        if trigger_p25:
            reasons.append("q25")
        if trend50:
            reasons.append("trend50")
        if trend100:
            reasons.append("trend100")
        return {
            "state": "trigger",
            "text": f"RegPreview YES {'+'.join(reasons)} vs {best_hash[:8]}({best_source}) n={current['n']}",
        }
    return {
        "state": "safe",
        "text": f"RegPreview NO vs {best_hash[:8]}({best_source}) n={current['n']}",
    }


def load_game_state():
    p = Path("game_state.json")
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text())
    except Exception:
        return {}


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


def get_accumulated_count():
    p = Path("tmp/accumulated_games.json")
    if not p.exists():
        return 0
    try:
        return json.loads(p.read_text()).get("count", 0)
    except Exception:
        return 0


def get_rejected_count():
    p = Path("tmp/rejected_hashes.txt")
    if not p.exists():
        return 0
    try:
        return sum(1 for l in p.read_text().splitlines() if l.strip())
    except Exception:
        return 0


def load_improve_state():
    p = Path("tmp/improve_state.json")
    base = {
        "status": "idle",
        "pid": 0,
        "phase": "",
        "progress": 0,
        "alive": False,
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

    return base


# ── Panel renderers ───────────────────────────────────────────

def render_header(scores, game_state, strat_hash, strat_ver, strat_lines,
                  rejected, accumulated, improve, rolling):
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

    row1 = f" SOREN AI  #{game_count} games   Best:{best}   Avg:{avg_all}"
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

    anchor = load_best_anchor()
    ranked = []
    current_metrics = None
    for h, data in rolling.items():
        metrics = calc_strategy_metrics(data.get("scores", []))
        if not metrics:
            continue
        if strat_hash and h == strat_hash:
            current_metrics = metrics
        if metrics["n"] < 12:
            continue
        row = (metrics["comp"], metrics["p50"], metrics["p25"], metrics["n"], h, metrics)
        ranked.append(row)
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
        elif reg["state"] == "safe":
            reg_color = C_GREEN
        elif reg["state"] == "unknown":
            reg_color = C_YELLOW
        reg_raw = f" {reg['text']}"
        reg_disp = f" {reg_color}{reg['text']}{RST}"
        pad7 = inner - len(reg["text"]) - 1
        lines.append(f"{C_CYAN}│{RST}{reg_disp}{' ' * max(pad7, 0)} {C_CYAN}│{RST}")

    lines.append(f"{C_CYAN}└{'─' * (W - 2)}┘{RST}")
    return lines


def render_score_timeline(scores, chart_w=50, chart_h=7):
    label_w = 5  # "XXXXX"
    sep = "│"
    # total = label_w + 1(sep) + chart_w + 1(space) = 57

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

    all_entries = []
    for h, data in rolling.items():
        sc = data.get("scores", [])
        metrics = calc_strategy_metrics(sc)
        if not metrics:
            continue
        games_total = data.get("games_total", len(sc))
        try:
            games_total = int(games_total)
        except Exception:
            games_total = len(sc)
        all_entries.append({
            "hash": h,
            "h8": h[:8],
            "n_roll": metrics["n"],
            "n_total": games_total,
            "comp": metrics["comp"],
            "p50": metrics["p50"],
            "p25": metrics["p25"],
            "lcb": metrics["lcb"],
        })

    if not all_entries:
        return [f"  {BOLD}Strategy Comparison{RST}", f"  {DIM}(no data){RST}"]

    all_entries.sort(key=lambda e: (e["comp"], e["p50"], e["p25"], e["n_roll"]), reverse=True)
    for idx, e in enumerate(all_entries, start=1):
        e["rank"] = idx
    current_entry = next((e for e in all_entries if current_hash and e["hash"] == current_hash), None)
    entries = all_entries[:max_rows]
    max_comp = max(e["comp"] for e in entries) if entries else 1
    rollback_entry = next((e for e in all_entries if e["hash"] in rollback_candidates), None)

    lines = [f"  {BOLD}Strategy Comparison{RST} {DIM}(comp=0.55p50+0.30p25+0.15lcb, rollback=yellow){RST}"]
    if current_entry:
        lines.append(
            f"  {DIM}cur #{current_entry['rank']:>2} {current_entry['h8']} "
            f"c{int(current_entry['comp']):>4} m{int(current_entry['p50']):>4} "
            f"q{int(current_entry['p25']):>4} l{int(current_entry['lcb']):>4} "
            f"n{current_entry['n_roll']:>2}/{current_entry['n_total']:<3}{RST}"
        )
    # Align with numeric columns rendered as: " {comp:>4} {p50:>4} {p25:>4}"
    # p50 label is intentionally shifted 1 column left for visual column match.
    metric_header = "comp p50  p25"
    lines.append(f"{DIM} rk hash      n/t  │{'bar':<{bar_w}} {metric_header}{RST}")

    def render_entry(e, is_current=False):
        is_rollback = e["hash"] in rollback_candidates
        color = C_GREEN if is_current else (C_YELLOW if is_rollback else C_BLUE)
        marker = "►" if is_current else " "
        bar = block_bar(e["comp"], max_comp, bar_w, color)
        n_field = f"{e['n_roll']:>2}/{e['n_total']:<3}"
        return (
            f"{marker}{e['rank']:>2} {color}{e['h8']}{RST} {DIM}{n_field:>6}{RST}│"
            f"{bar} {int(e['comp']):>4} {int(e['p50']):>4} {int(e['p25']):>4}"
        )

    for e in entries:
        is_current = current_hash and e["hash"] == current_hash
        lines.append(render_entry(e, is_current=bool(is_current)))
    if rollback_entry and rollback_entry not in entries and rollback_entry is not current_entry:
        lines.append(f"{DIM} .. {'':8} {'':>6}│{'':<{bar_w}} {RST}")
        lines.append(render_entry(rollback_entry, is_current=False))
    if current_entry and current_entry not in entries:
        if not rollback_entry or rollback_entry is current_entry:
            lines.append(f"{DIM} .. {'':8} {'':>6}│{'':<{bar_w}} {RST}")
        lines.append(render_entry(current_entry, is_current=True))
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
    strat_hash = get_strategy_hash()
    strat_ver = get_strategy_version()
    strat_lines = get_strategy_lines()
    rejected = get_rejected_count()
    accumulated = get_accumulated_count()
    improve = load_improve_state()
    reasons = load_decision_reasons(50)

    output = []

    output += render_header(scores, game_state, strat_hash, strat_ver,
                            strat_lines, rejected, accumulated, improve, rolling)
    output.append("")
    output += render_score_timeline(scores)
    output.append("")
    output += render_score_distribution(scores)
    output.append("")
    output += render_strategy_comparison(rolling, strat_hash)
    output.append("")
    output += render_decision_patterns(reasons)
    output.append("")

    print("\n".join(output))


if __name__ == "__main__":
    main()
