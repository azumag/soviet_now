#!/usr/bin/env python3
"""status_dashboard.py — CUI Graphical Statistics Dashboard for Soren AI

Renders 5 panels: Header, Score Timeline (braille), Score Distribution,
Strategy Comparison, Decision Patterns.
"""

import json
import os
import subprocess
import sys
from collections import Counter
from glob import glob
from pathlib import Path

W = 57

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


# ── Panel renderers ───────────────────────────────────────────

def render_header(scores, game_state, strat_hash, strat_ver, strat_lines,
                  rejected, accumulated):
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
    r3 = f" Strategy: {hash_short}  {ver_num}  {strat_lines}L"
    lines.append(f"{C_CYAN}│{RST}{DIM}{r3:<{inner}}{RST} {C_CYAN}│{RST}")

    # Row 4: live game state
    r4_raw = f" Live: {state}  score={gscore}  pieces={gpieces}"
    if accumulated > 0:
        r4_raw_nocolor = r4_raw + f"  ♦ {accumulated} queued"
    else:
        r4_raw_nocolor = r4_raw
    r4_display = f" Live: {state_color}{state}{RST}  score={gscore}  pieces={gpieces}{live_extra}"
    pad4 = inner - len(r4_raw_nocolor)
    lines.append(f"{C_CYAN}│{RST}{r4_display}{' ' * max(pad4, 0)} {C_CYAN}│{RST}")

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
    bar_w = 38
    # hash8 + space + n3 + sep + bar38 + space + avg7 = 8+1+3+1+38+1+5 = 57

    entries = []
    for h, data in rolling.items():
        sc = data.get("scores", [])
        if not sc:
            continue
        avg = sum(sc) / len(sc)
        entries.append((h[:8], len(sc), avg))

    entries.sort(key=lambda x: -x[2])
    entries = entries[:max_rows]

    if not entries:
        return [f"  {BOLD}Strategy Comparison{RST}", f"  {DIM}(no data){RST}"]

    max_avg = max(e[2] for e in entries) if entries else 1

    lines = [f"  {BOLD}Strategy Comparison{RST} {DIM}(rolling scores){RST}"]
    for h8, n, avg in entries:
        is_current = current_hash.startswith(h8) if current_hash else False
        color = C_GREEN if is_current else C_BLUE
        marker = "►" if is_current else " "
        bar = block_bar(avg, max_avg, bar_w, color)
        lines.append(f"{marker}{color}{h8}{RST} {DIM}{n:>2}{RST}│{bar} {int(avg):>5}")
    return lines


def render_decision_patterns(reasons, max_rows=8, bar_w=30):
    # Simplify compound reasons: take primary keyword
    simplified = []
    for r in reasons:
        # Take first meaningful component
        parts = r.split("_")
        # Group by primary pattern
        if "NEAR_MERGE" in r:
            simplified.append("NEAR_MERGE")
        elif "HEIGHT_BALANCE" in r:
            side = "L" if "LEFT" in r else "R" if "RIGHT" in r else ""
            simplified.append(f"H_BAL_{side}" if side else "H_BALANCE")
        elif "HEIGHT_CONTROL" in r:
            simplified.append("HEIGHT_CTRL")
        elif "HIGH_TOWER" in r:
            simplified.append("HIGH_TOWER")
        elif "HIGH_LAYER" in r:
            simplified.append("HIGH_LAYER")
        elif "MEDIUM_TOWER" in r:
            simplified.append("MED_TOWER")
        elif "CLUSTER" in r:
            simplified.append("CLUSTER")
        elif "NEXT_SAME" in r:
            simplified.append("NEXT_SAME")
        else:
            simplified.append(r[:15])

    counter = Counter(simplified)
    top = counter.most_common(max_rows)

    if not top:
        return [f"  {BOLD}Decision Patterns{RST}", f"  {DIM}(no data){RST}"]

    max_count = top[0][1]

    pattern_colors = {
        "NEAR_MERGE": fg256(118),
        "HEIGHT_CTRL": fg256(37),
        "H_BAL_L": fg256(75),
        "H_BAL_R": fg256(75),
        "H_BALANCE": fg256(75),
        "HIGH_TOWER": fg256(196),
        "HIGH_LAYER": fg256(208),
        "MED_TOWER": fg256(220),
        "CLUSTER": fg256(141),
        "NEXT_SAME": fg256(222),
    }

    total = sum(c for _, c in top)
    lines = [f"  {BOLD}Decision Patterns{RST} {DIM}(recent {len(reasons)} turns){RST}"]
    # label 13 + sep 1 + bar 30 + space + count/pct 12 = 57
    for label, count in top:
        color = pattern_colors.get(label, C_GREY)
        bar = block_bar(count, max_count, bar_w, color)
        pct = count / total * 100 if total > 0 else 0
        lines.append(f" {label:<13}│{bar} {count:>4} {DIM}{pct:>4.0f}%{RST}")
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
    reasons = load_decision_reasons(50)

    output = []

    output += render_header(scores, game_state, strat_hash, strat_ver,
                            strat_lines, rejected, accumulated)
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
