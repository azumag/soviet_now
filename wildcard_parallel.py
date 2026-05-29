#!/usr/bin/env python3
"""Parallel WILDCARD candidate runner.

Builds several isolated WILDCARD perturbation candidates, evaluates each in its
own runtime directory, and reports a single winner for the live loop to adopt.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import math
import os
import re
import shutil
import shlex
import signal
import subprocess
import sys
import tempfile
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from statistics import median
from threading import Event, Lock, Timer


REPO_ROOT = Path(__file__).resolve().parent
_ACTIVE_SESSION_DIR: Path | None = None
_ACTIVE_RESULT_FILE: Path | None = None
_ACTIVE_STATUS_FILE: Path | None = None
_ACTIVE_HTML_FILE: Path | None = None
_ACTIVE_ARGS: argparse.Namespace | None = None

# Cooperative stop: set by the signal handler so worker threads (evaluate_slot /
# evaluate_real game loop) return promptly instead of letting the ThreadPoolExecutor
# block forever joining infinite-regeneration workers. Also used to break out once the
# overall wall-clock deadline is reached so the process self-terminates instead of
# spinning + leaking chrome (which is what stranded the post-improve param run).
_STOP_EVENT = Event()
_DEADLINE_TS: float = 0.0


def _stop_now() -> bool:
    """True when a signal asked us to stop or the wall-clock deadline has passed."""
    if _STOP_EVENT.is_set():
        return True
    return _DEADLINE_TS > 0 and time.time() >= _DEADLINE_TS


_NODE_BIN_CACHE: str | None = None


def resolve_node_bin() -> str:
    """Resolve a usable `node` even when PATH lacks it.

    The orchestrator is spawned from the improve subprocess, whose PATH may not have
    nvm sourced. Calling bare "node" there raises FileNotFoundError that the callers
    swallow, so the chrome window title was never set and OBS window capture could not
    find the candidate window. Mirror obs_control.sh's fallback list so node-backed
    helpers (window title, preview capture, bridge launch) work in any launch context.
    """
    global _NODE_BIN_CACHE
    if _NODE_BIN_CACHE:
        return _NODE_BIN_CACHE
    found = shutil.which("node")
    if not found:
        for candidate in (
            os.path.expanduser("~/.nvm/versions/node/v23.10.0/bin/node"),
            "/opt/homebrew/bin/node",
            "/usr/local/bin/node",
            "/Volumes/satelite/homebrew/homebrew/bin/node",
        ):
            if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
                found = candidate
                break
    _NODE_BIN_CACHE = found or "node"
    return _NODE_BIN_CACHE


def _int(value: object, default: int) -> int:
    try:
        return int(value)
    except Exception:
        return default


def _float(value: object, default: float) -> float:
    try:
        return float(value)
    except Exception:
        return default


def quantile(values: list[int], p: float) -> float:
    if not values:
        return 0.0
    xs = sorted(float(v) for v in values)
    if len(xs) == 1:
        return xs[0]
    pos = (len(xs) - 1) * p
    lo = int(pos)
    hi = min(lo + 1, len(xs) - 1)
    frac = pos - lo
    return xs[lo] * (1.0 - frac) + xs[hi] * frac


def composite(scores: list[int]) -> float:
    if not scores:
        return 0.0
    mean = sum(scores) / len(scores)
    variance = sum((x - mean) ** 2 for x in scores) / len(scores) if len(scores) > 1 else 0.0
    lcb = mean - 1.28 * (math.sqrt(variance) / math.sqrt(len(scores)))
    return 0.55 * quantile(scores, 0.50) + 0.30 * quantile(scores, 0.25) + 0.15 * lcb


def compute_strategy_hash(path: Path) -> str:
    try:
        sys.path.insert(0, str(REPO_ROOT))
        from extract_decide_hash import compute_hash

        h = compute_hash(str(path))
        if h:
            return h
    except Exception:
        pass
    import hashlib

    return hashlib.md5(path.read_bytes()).hexdigest()[:12]


def atomic_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def overlay_visible_candidates(candidates: list[dict]) -> list[dict]:
    """Keep OBS focused on live slots and candidates that reached a decision."""
    visible_statuses = {"pending", "running", "accepted", "won"}
    visible = [c for c in candidates if str(c.get("status") or "pending") in visible_statuses]
    return sorted(visible, key=lambda c: (_int(c.get("index"), 0), str(c.get("job_id") or "")))


def no_winner_reason(candidates: list["CandidateResult"]) -> str:
    eval_candidates = [c for c in candidates if not c.score_baseline]
    if not eval_candidates:
        return "no_candidate"
    failed = [c for c in eval_candidates if c.status in ("failed", "timeout")]
    zero_game = [c for c in eval_candidates if len(c.scores) <= 0]
    errors = " ".join(c.error for c in failed if c.error)
    infra_markers = ("bridge exited", "BRIDGE-EXIT", "SIGABRT", "process did exit", "EADDRINUSE")
    if len(failed) == len(eval_candidates) and len(zero_game) == len(eval_candidates) and any(m in errors for m in infra_markers):
        return "infra_failed"
    return "no_candidate"


def wildcard_parallel_params(args: argparse.Namespace) -> dict:
    return {
        "jobs": _int(getattr(args, "jobs", 0), 0),
        "max_games": _int(getattr(args, "games", 0), 0),
        "min_successful_games": _int(getattr(args, "min_successful_games", 0), 0),
        "cull_after_games": _int(getattr(args, "cull_after_games", 0), 0),
        "cull_leader_min_games": _int(getattr(args, "cull_leader_min_games", 0), 0),
        "cull_comp_ratio": round(_float(getattr(args, "cull_comp_ratio", 0.0), 0.0), 3),
        "lingering_slot_max_culls": _int(getattr(args, "lingering_slot_max_culls", 0), 0),
        "evaluate_mode": str(getattr(args, "evaluate_mode", "") or ""),
        "random_count": bool(getattr(args, "random_count", False)),
        "serve_base_port": _int(getattr(args, "serve_base_port", 0), 0),
        "cdp_base_port": _int(getattr(args, "cdp_base_port", 0), 0),
        "deadline_fast_drop_mutate": bool(getattr(args, "deadline_fast_drop_mutate", True)),
        "deadline_fast_drop_values": [
            _bool_literal(v) for v in (getattr(args, "deadline_fast_drop_values", None) or [True, False])
        ],
        "baseline_slot1": bool(getattr(args, "baseline_slot1", False)),
        "block_main_loop": bool(getattr(args, "block_main_loop", True)),
    }


def resolve_playwright_chrome_for_testing(playwright_browsers_path: str) -> str:
    """Return Playwright's bundled Chromium executable when it is installed."""
    try:
        proc = subprocess.run(
            [
                resolve_node_bin(),
                "-e",
                "const { chromium } = require('playwright'); process.stdout.write(chromium.executablePath())",
            ],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        candidate = proc.stdout.strip()
        if candidate and Path(candidate).exists() and os.access(candidate, os.X_OK):
            return candidate
    except Exception:
        pass
    root = Path(playwright_browsers_path).expanduser()
    if sys.platform == "darwin":
        patterns = [
            "chromium-*/chrome-mac*/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing",
            "chromium-*/chrome-mac*/Chromium.app/Contents/MacOS/Chromium",
        ]
    elif sys.platform.startswith("linux"):
        patterns = ["chromium-*/chrome-linux*/chrome"]
    elif sys.platform.startswith("win"):
        patterns = ["chromium-*/chrome-win*/chrome.exe"]
    else:
        patterns = []
    for pattern in patterns:
        for candidate in sorted(root.glob(pattern), reverse=True):
            if candidate.exists() and os.access(candidate, os.X_OK):
                return str(candidate)
    return ""


def chrome_app_path_from_executable(executable_path: str) -> str:
    marker = ".app/Contents/MacOS/"
    idx = executable_path.find(marker)
    if idx < 0:
        return ""
    return executable_path[: idx + len(".app")]


def wait_for_candidate_chrome_cdp(cdp_port: int, timeout: float = 8.0) -> bool:
    deadline = time.time() + timeout
    url = f"http://127.0.0.1:{cdp_port}/json/version"
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=0.5) as response:
                return response.status == 200
        except Exception:
            time.sleep(0.2)
    return False


# Serialize + stagger Chrome spawns across slots. Launching ~6 Chrome-for-Testing
# instances concurrently races on the macOS app/window-server registration
# (GetCurrentProcess -> _RegisterApplication -> NSApplication init), which aborts
# one of them with EXC_CRASH/SIGABRT during launch. Holding a global lock through
# the spawn + a short stagger spaces the registrations out; the slow CDP-readiness
# wait stays OUTSIDE the lock so slots still warm up in parallel.
_CHROME_LAUNCH_LOCK = Lock()


def _spawn_with_launch_stagger(spawn_fn):
    stagger = _float(os.getenv("WILDCARD_PARALLEL_CHROME_LAUNCH_STAGGER_SEC"), 1.2)
    with _CHROME_LAUNCH_LOCK:
        result = spawn_fn()
        if stagger > 0:
            time.sleep(stagger)
    return result


def prelaunch_candidate_chrome(app_path: str, executable_path: str, profile_dir: str, cdp_port: int) -> bool:
    if sys.platform != "darwin":
        return False
    profile_path = Path(profile_dir)
    env_root = profile_path.parent
    chrome_home = env_root / "chrome_home"
    config_home = env_root / "config"
    cache_home = env_root / "cache"
    tmp_dir = env_root / "tmp"
    crashpad_dir = profile_path / "Crashpad"
    try:
        for path in (profile_path, chrome_home, config_home, cache_home, tmp_dir, crashpad_dir):
            path.mkdir(parents=True, exist_ok=True)
    except Exception:
        return False
    chrome_args = [
        f"--user-data-dir={profile_dir}",
        "--window-size=1300,800",
        f"--remote-debugging-port={cdp_port}",
        "--hide-crash-restore-bubble",
        "--disable-session-crashed-bubble",
        "--disable-crash-reporter",
        "--disable-crashpad",
        f"--crash-dumps-dir={crashpad_dir}",
        "--no-first-run",
        "--no-default-browser-check",
        # suppress the "Chrome for Testing ... / automated test software" infobar
        "--test-type",
        "--disable-infobars",
        "--password-store=basic",
        "--use-mock-keychain",
        "--disable-translate",
        "--autoplay-policy=no-user-gesture-required",
        "about:blank",
    ]
    env = os.environ.copy()
    env.update(
        {
            "HOME": str(chrome_home.resolve()),
            "SOREN_CHROME_HOME": str(chrome_home.resolve()),
            "XDG_CONFIG_HOME": str(config_home.resolve()),
            "XDG_CACHE_HOME": str(cache_home.resolve()),
            "TMPDIR": str(tmp_dir.resolve()),
        }
    )
    if app_path:
        open_args = [
            "/usr/bin/open",
            "-g",
            "-n",
            app_path,
            "--args",
            *chrome_args,
        ]
        try:
            _spawn_with_launch_stagger(lambda: subprocess.run(open_args, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True, env=env))
            if wait_for_candidate_chrome_cdp(cdp_port):
                return True
        except Exception:
            pass
    if not executable_path:
        return False
    try:
        proc = _spawn_with_launch_stagger(lambda: subprocess.Popen(
            [executable_path, *chrome_args],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            env=env,
            start_new_session=True,
        ))
        if wait_for_candidate_chrome_cdp(cdp_port):
            return True
        try:
            proc.terminate()
        except Exception:
            pass
        return False
    except Exception:
        return False


@dataclass
class CandidateResult:
    job_id: str
    index: int
    workdir: Path
    strategy_path: Path
    status: str = "pending"
    hash: str = ""
    seed: int = 0
    generation: int = 0
    applied: list[dict] = field(default_factory=list)
    scores: list[int] = field(default_factory=list)
    raw_scores: list[int] = field(default_factory=list)
    eval_scores: list[int] = field(default_factory=list)
    game_results: list[dict] = field(default_factory=list)
    comp: float = 0.0
    p25: float = 0.0
    p50: float = 0.0
    max_type: int = 0
    russia_count: int = 0
    soviet_count: int = 0
    error: str = ""
    cull_leader_job_id: str = ""
    cull_leader_games: int = 0
    cull_leader_comp: float = 0.0
    cull_threshold: float = 0.0
    cdp_port: int = 0
    serve_port: int = 0
    profile_dir: str = ""
    baseline: bool = False
    score_baseline: bool = False

    def public(self) -> dict:
        return {
            "job_id": self.job_id,
            "index": self.index,
            "workdir": str(self.workdir),
            "strategy_path": str(self.strategy_path),
            "status": self.status,
            "hash": self.hash,
            "seed": self.seed,
            "generation": self.generation,
            "applied": self.applied,
            "scores": self.scores,
            "raw_scores": self.raw_scores,
            "eval_scores": self.eval_scores,
            "game_results": self.game_results,
            "games": len(self.scores),
            "comp": round(self.comp, 2),
            "p25": round(self.p25, 2),
            "p50": round(self.p50, 2),
            "max_type": self.max_type,
            "russia_count": self.russia_count,
            "soviet_count": self.soviet_count,
            "error": self.error,
            "cull_leader_job_id": self.cull_leader_job_id,
            "cull_leader_games": self.cull_leader_games,
            "cull_leader_comp": round(self.cull_leader_comp, 2),
            "cull_threshold": round(self.cull_threshold, 2),
            "cdp_port": self.cdp_port,
            "serve_port": self.serve_port,
            "profile_dir": self.profile_dir,
            "preview_path": str(self.workdir / "tmp" / "preview.png"),
            "baseline": self.baseline,
            "score_baseline": self.score_baseline,
        }


def candidate_from_public(data: dict) -> CandidateResult:
    candidate = CandidateResult(
        job_id=str(data.get("job_id") or ""),
        index=_int(data.get("index"), 0),
        workdir=Path(str(data.get("workdir") or REPO_ROOT)),
        strategy_path=Path(str(data.get("strategy_path") or REPO_ROOT / "strategy.py")),
        status=str(data.get("status") or "pending"),
        hash=str(data.get("hash") or ""),
        seed=_int(data.get("seed"), 0),
        generation=_int(data.get("generation"), 0),
        applied=list(data.get("applied") or []),
        scores=[_int(v, 0) for v in (data.get("scores") or [])],
        raw_scores=[_int(v, 0) for v in (data.get("raw_scores") or [])],
        eval_scores=[_int(v, 0) for v in (data.get("eval_scores") or [])],
        game_results=list(data.get("game_results") or []),
        comp=_float(data.get("comp"), 0.0),
        p25=_float(data.get("p25"), 0.0),
        p50=_float(data.get("p50"), 0.0),
        max_type=_int(data.get("max_type"), 0),
        russia_count=_int(data.get("russia_count"), 0),
        soviet_count=_int(data.get("soviet_count"), 0),
        error=str(data.get("error") or ""),
        cull_leader_job_id=str(data.get("cull_leader_job_id") or ""),
        cull_leader_games=_int(data.get("cull_leader_games"), 0),
        cull_leader_comp=_float(data.get("cull_leader_comp"), 0.0),
        cull_threshold=_float(data.get("cull_threshold"), 0.0),
        cdp_port=_int(data.get("cdp_port"), 0),
        serve_port=_int(data.get("serve_port"), 0),
        profile_dir=str(data.get("profile_dir") or ""),
        baseline=bool(data.get("baseline")),
        score_baseline=bool(data.get("score_baseline")),
    )
    return candidate


def build_parallel_result(
    args: argparse.Namespace,
    session_dir: Path,
    candidates: list[CandidateResult],
    interrupted: bool = False,
    signum: int | None = None,
) -> tuple[dict, dict, int]:
    winner = choose_winner(candidates, args.min_successful_games)
    if winner:
        winner.status = "won"
    reason = "winner_selected" if winner else no_winner_reason(candidates)
    payload = {
        "phase": "won" if winner else reason,
        "session_dir": str(session_dir),
        "params": wildcard_parallel_params(args),
        "winner": winner.public() if winner else None,
        "candidates": [c.public() for c in candidates],
    }
    result = {
        "ok": winner is not None,
        "reason": reason,
        "session_dir": str(session_dir),
        "params": wildcard_parallel_params(args),
        "winner": winner.public() if winner else None,
        "candidates": [c.public() for c in candidates],
    }
    if interrupted:
        result["interrupted"] = True
        payload["interrupted"] = True
        if signum is not None:
            result["signal"] = signum
            payload["signal"] = signum
    return result, payload, 0 if winner else 2


def write_interrupted_result_from_status(
    args: argparse.Namespace,
    status_file: Path,
    html_file: Path,
    result_file: Path,
    session_dir: Path,
    signum: int,
) -> bool:
    try:
        data = json.loads(status_file.read_text(encoding="utf-8"))
    except Exception:
        return False
    candidates = [candidate_from_public(item) for item in (data.get("candidates") or []) if isinstance(item, dict)]
    if not candidates:
        return False
    result, payload, _ = build_parallel_result(args, session_dir, candidates, interrupted=True, signum=signum)
    atomic_json(result_file, result)
    try:
        render_overlay(status_file, html_file, payload)
    except Exception:
        pass
    return True


def _overlay_preview_enabled() -> bool:
    """Whether to show per-candidate screenshot previews in the HTML overlay.

    When OBS window capture is active the live Chrome windows are already on
    screen, so the embedded screenshots are redundant. Default: off if window
    capture is on. Explicit override via WILDCARD_PARALLEL_OVERLAY_PREVIEW.
    """
    val = os.environ.get("WILDCARD_PARALLEL_OVERLAY_PREVIEW")
    if val is not None and val.strip() != "":
        return val.strip().lower() not in ("0", "false", "no", "off")
    return os.environ.get("WILDCARD_PARALLEL_OBS_WINDOW_SOURCES", "0") != "1"


def render_overlay(status_path: Path, html_path: Path, payload: dict) -> None:
    now_epoch = int(time.time())
    payload.setdefault("started_at", now_epoch)
    payload["updated_at"] = now_epoch
    candidates = payload.get("candidates") or []
    display_candidates = overlay_visible_candidates(candidates)
    generated = time.strftime("%H:%M:%S")
    title = os.environ.get("WILDCARD_PARALLEL_OVERLAY_TITLE", "WILDCARD PARALLEL TRIAL")
    ranked_candidates = sorted(
        [c for c in display_candidates if _float(c.get("comp"), 0.0) > 0 and _int(c.get("games"), 0) > 0],
        key=lambda c: (
            _float(c.get("comp"), 0.0),
            _float(c.get("p25"), 0.0),
            _int(c.get("max_type"), 0),
            _int(c.get("games"), 0),
        ),
        reverse=True,
    )
    rank_by_job = {str(c.get("job_id")): idx + 1 for idx, c in enumerate(ranked_candidates)}
    best_comp = _float(ranked_candidates[0].get("comp"), 0.0) if ranked_candidates else 0.0
    if ranked_candidates:
        leader = ranked_candidates[0]
        leader_label = f"SLOT {_int(leader.get('index'), 0) + 1} / {leader.get('job_id', '-')}"
    else:
        leader_label = "-"
    params = payload.get("params") or {}
    max_games = _int(params.get("max_games"), 0)
    min_games = _int(params.get("min_successful_games"), max_games)
    cull_after = _int(params.get("cull_after_games"), 0)
    cull_leader_min = _int(params.get("cull_leader_min_games"), 0)
    cull_ratio = _float(params.get("cull_comp_ratio"), 0.0)
    lingering_max_culls = _int(params.get("lingering_slot_max_culls"), 0)
    jobs = _int(params.get("jobs"), 0)
    mode = str(params.get("evaluate_mode") or "")
    status_counts: dict[str, int] = {}
    total_games = 0
    for cand in display_candidates:
        if cand.get("score_baseline"):
            continue
        status = str(cand.get("status") or "pending")
        display_status = "finished" if status == "accepted" else status
        status_counts[display_status] = status_counts.get(display_status, 0) + 1
        total_games += _int(cand.get("games"), 0)
    total_slots = jobs or len([c for c in display_candidates if not c.get("score_baseline")])
    target_games = total_slots * max_games if total_slots and max_games else 0
    progress_pct = 0 if target_games <= 0 else max(0, min(100, round((total_games / target_games) * 100)))
    progress_label = f"{total_games}/{target_games} games" if target_games else f"{total_games} games"
    counts_label = " ".join(
        f"{key}:{status_counts.get(key, 0)}"
        for key in ("pending", "running", "finished", "won", "failed", "timeout")
        if status_counts.get(key, 0)
    ) or "pending"
    cull_label = f"{cull_after}g" if cull_after > 0 else "off"
    ratio_label = f"{cull_ratio:.0%}" if cull_ratio > 0 else "off"
    param_parts = []
    if max_games:
        param_parts.append(f"max {max_games}g")
    if min_games:
        param_parts.append(f"min {min_games}g")
    param_parts.append(f"cull {cull_label}")
    if cull_leader_min > 0:
        param_parts.append(f"leader min {cull_leader_min}g")
    param_parts.append(f"threshold {ratio_label}")
    if lingering_max_culls > 0:
        param_parts.append(f"linger >{lingering_max_culls}c")
    if jobs:
        param_parts.append(f"jobs {jobs}")
    if mode:
        param_parts.append(mode)
    params_label = " / ".join(param_parts)
    compact_params = " ".join(
        part
        for part in [
            f"J{jobs}" if jobs else "",
            f"G{max_games}" if max_games else "",
            f"MIN{min_games}" if min_games else "",
            f"CULL={cull_label}",
            f"LEADER>={cull_leader_min}g" if cull_leader_min > 0 else "",
            f"TOP>={ratio_label}",
            f"LINGER>{lingering_max_culls}C" if lingering_max_culls > 0 else "",
            mode.upper() if mode else "",
        ]
        if part
    )
    # Compact top-strip cells: one per slot showing SLOT, rank, comp, games(試行).
    # The strip is the ONLY opaque region; the area below is transparent so the
    # higher-layer candidate window tiles are never hidden by the overlay.
    strip_h = _int(os.environ.get("WILDCARD_PARALLEL_OVERLAY_STRIP_H"), 200)
    cur_game = max(
        (_int(c.get("games"), 0) for c in display_candidates if not c.get("score_baseline")),
        default=0,
    )
    game_label = f"game {cur_game}/{max_games}" if max_games else f"game {cur_game}"
    pcells = []
    for cand in display_candidates:
        status = str(cand.get("status") or "pending")
        display_status = "finished" if status == "accepted" else status
        klass = "bad" if status in {"failed", "timeout", "culled"} else "good" if status == "won" else "run"
        job_id = str(cand.get("job_id", "-"))
        slot_num = _int(cand.get("index"), 0) + 1
        slot_label = "SRC" if cand.get("score_baseline") else str(slot_num)
        rank = rank_by_job.get(job_id)
        if rank == 1:
            klass += " leader"
        rank_label = f"#{rank}" if rank is not None else "--"
        rank_class = f"r{rank}" if rank is not None and rank <= 3 else "rx"
        comp = _float(cand.get("comp"), 0.0)
        bar_width = 0 if best_comp <= 0 else max(0, min(100, round((comp / best_comp) * 100)))
        games_n = _int(cand.get("games"), 0)
        comp_label = str(int(round(comp))) if comp else "-"
        pcells.append(
            f"""
            <div class="pcell {klass}">
              <div class="pcell-head"><span class="pslot">{html.escape(slot_label)}</span><span class="prank {rank_class}">{html.escape(rank_label)}</span><span class="pstatus">{html.escape(display_status)}</span></div>
              <div class="pbar"><div class="pbar-fill" style="width:{bar_width}%"></div></div>
              <div class="pmetrics"><span class="pcomp">{html.escape(comp_label)}</span><span class="pgames">g{games_n}</span></div>
            </div>
            """
        )
    if not pcells:
        pcells.append('<div class="pcell run"><div class="pcell-head"><span class="pslot">-</span><span class="pstatus">waiting</span></div></div>')
    pcell_cols = max(1, len(pcells))

    doc = f"""<!doctype html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta http-equiv="refresh" content="2">
<style>
html, body {{
  margin: 0;
  width: 100%;
  height: 100%;
  overflow: hidden;
  background: transparent;
  color: #eaf2ff;
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
}}
.pstrip {{
  position: fixed;
  top: 0;
  left: 0;
  width: 100vw;
  height: {strip_h}px;
  box-sizing: border-box;
  padding: 12px 18px;
  background: rgba(3, 8, 13, 0.92);
  border-bottom: 4px solid #f59e0b;
  display: flex;
  flex-direction: column;
  gap: 10px;
  overflow: hidden;
}}
.phead {{
  display: flex;
  align-items: baseline;
  gap: 18px;
}}
.ptitle {{
  font-size: 26px;
  font-weight: 800;
  white-space: nowrap;
}}
.pgame {{
  font-size: 26px;
  font-weight: 800;
  color: #facc15;
  white-space: nowrap;
}}
.psub {{
  font-size: 15px;
  color: #bae6fd;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}}
.pcells {{
  display: grid;
  grid-template-columns: repeat({pcell_cols}, minmax(0, 1fr));
  gap: 10px;
  flex: 1;
  min-height: 0;
}}
.pcell {{
  min-width: 0;
  box-sizing: border-box;
  border: 1px solid rgba(255,255,255,0.18);
  border-radius: 6px;
  padding: 8px 10px;
  background: rgba(15, 23, 42, 0.85);
  display: flex;
  flex-direction: column;
  justify-content: center;
  gap: 6px;
}}
.pcell.good {{ border-color: rgba(74, 222, 128, 0.9); }}
.pcell.bad {{ border-color: rgba(248, 113, 113, 0.9); }}
.pcell.leader {{
  border-color: rgba(250, 204, 21, 1);
  box-shadow: inset 0 0 0 2px rgba(250, 204, 21, 0.35);
}}
.pcell-head {{
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 14px;
}}
.pslot {{
  display: inline-grid;
  place-items: center;
  min-width: 30px;
  height: 24px;
  padding: 0 6px;
  border-radius: 4px;
  background: #38bdf8;
  color: #020617;
  font-weight: 900;
  font-size: 15px;
}}
.prank {{
  display: inline-grid;
  place-items: center;
  min-width: 36px;
  height: 24px;
  border-radius: 4px;
  background: #94a3b8;
  color: #020617;
  font-weight: 900;
  font-size: 15px;
}}
.prank.r1 {{ background: #facc15; }}
.prank.r2 {{ background: #cbd5e1; }}
.prank.r3 {{ background: #fb923c; }}
.pstatus {{
  margin-left: auto;
  color: #fde68a;
  font-size: 12px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}}
.pbar {{
  height: 10px;
  border-radius: 999px;
  overflow: hidden;
  background: rgba(148, 163, 184, 0.25);
}}
.pbar-fill {{
  height: 100%;
  min-width: 3px;
  border-radius: 999px;
  background: linear-gradient(90deg, #38bdf8, #facc15);
}}
.pmetrics {{
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 8px;
}}
.pcomp {{
  font-size: 22px;
  font-weight: 800;
  color: #e2e8f0;
}}
.pgames {{
  font-size: 14px;
  color: #93c5fd;
}}
.head {{
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 20px;
  margin-bottom: 14px;
}}
.title {{
  font-size: 32px;
  font-weight: 800;
  letter-spacing: 0;
}}
.sub {{
  color: #fcd34d;
  font-size: 20px;
  text-align: right;
  white-space: nowrap;
}}
.params {{
  margin-top: 4px;
  color: #bae6fd;
  font-size: 16px;
  text-align: right;
  white-space: nowrap;
}}
.progress {{
  display: grid;
  grid-template-columns: minmax(180px, 1fr) auto;
  gap: 10px;
  align-items: center;
  margin-top: 8px;
  color: #dbeafe;
  font-size: 16px;
}}
.progress-track {{
  height: 12px;
  overflow: hidden;
  border-radius: 999px;
  background: rgba(148, 163, 184, 0.25);
}}
.progress-fill {{
  height: 100%;
  min-width: 3px;
  border-radius: 999px;
  background: linear-gradient(90deg, #22c55e, #facc15);
}}
.progress-text {{
  white-space: nowrap;
}}
.grid {{
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 12px;
  height: calc(100vh - 100px);
}}
.card {{
  min-width: 0;
  border: 1px solid rgba(255,255,255,0.18);
  border-radius: 6px;
  padding: 13px;
  background: rgba(15, 23, 42, 0.82);
  overflow: hidden;
}}
.card.run {{ border-color: rgba(96, 165, 250, 0.7); }}
.card.good {{ border-color: rgba(74, 222, 128, 0.9); }}
.card.bad {{ border-color: rgba(248, 113, 113, 0.9); }}
.card.leader {{
  border-color: rgba(250, 204, 21, 1);
  box-shadow: inset 0 0 0 2px rgba(250, 204, 21, 0.32);
}}
.top {{
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 8px;
  font-size: 21px;
  margin-bottom: 8px;
}}
.top b {{
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}}
.top span {{
  color: #fde68a;
}}
.slot-badge {{
  flex: 0 0 auto;
  display: inline-grid;
  place-items: center;
  min-width: 72px;
  height: 28px;
  padding: 0 8px;
  border-radius: 4px;
  background: #38bdf8;
  color: #020617 !important;
  font-size: 17px;
  font-weight: 900;
}}
.rankline {{
  display: grid;
  grid-template-columns: 48px minmax(0, 1fr) 82px;
  align-items: center;
  gap: 8px;
  margin-bottom: 10px;
}}
.rank-badge {{
  display: grid;
  place-items: center;
  height: 30px;
  border-radius: 4px;
  color: #020617;
  background: #94a3b8;
  font-size: 20px;
  font-weight: 900;
}}
.rank-badge.r1 {{ background: #facc15; }}
.rank-badge.r2 {{ background: #cbd5e1; }}
.rank-badge.r3 {{ background: #fb923c; }}
.rank-track {{
  height: 14px;
  border-radius: 999px;
  overflow: hidden;
  background: rgba(148, 163, 184, 0.25);
}}
.rank-fill {{
  height: 100%;
  min-width: 3px;
  border-radius: 999px;
  background: linear-gradient(90deg, #38bdf8, #facc15);
}}
.rank-score {{
  min-width: 0;
  color: #e2e8f0;
  font-size: 17px;
  text-align: right;
  white-space: nowrap;
}}
.metric {{
  font-size: 18px;
  line-height: 1.32;
  white-space: nowrap;
}}
.metric.source {{
  color: #bfdbfe;
  font-size: 16px;
}}
.hash {{
  margin: 9px 0;
  color: #93c5fd;
  font-size: 17px;
}}
.preview {{
  display: block;
  width: 100%;
  aspect-ratio: 16 / 9;
  object-fit: cover;
  background: rgba(0, 0, 0, 0.45);
  border: 1px solid rgba(255,255,255,0.16);
  border-radius: 4px;
  margin-bottom: 10px;
}}
.preview.empty {{
  display: grid;
  place-items: center;
  color: #94a3b8;
  font-size: 16px;
}}
/* OBS window-capture mode: reserve the tile's footprint (no aspect-ratio so it
   stays a fixed height), so the live tile overlays here and stats render below. */
.preview.reserved {{
  aspect-ratio: auto;
  height: var(--tile-h, 188px);
  background: rgba(0, 0, 0, 0.18);
  border-style: dashed;
}}
ul {{
  padding-left: 18px;
  margin: 8px 0;
  font-size: 16px;
  line-height: 1.35;
}}
.err {{
  color: #fca5a5;
  font-size: 15px;
  line-height: 1.3;
}}
@media (max-height: 360px) {{
  .wrap {{
    padding: 9px 14px;
  }}
  .head {{
    margin-bottom: 7px;
    gap: 12px;
  }}
  .title {{
    font-size: 23px;
  }}
  .sub {{
    font-size: 16px;
  }}
  .params {{
    margin-top: 2px;
    font-size: 12px;
  }}
  .progress {{
    margin-top: 4px;
    font-size: 12px;
  }}
  .grid {{
    grid-template-columns: repeat(3, minmax(0, 1fr));
    gap: 8px;
    height: auto;
  }}
  .card {{
    padding: 7px 8px;
  }}
  .top {{
    font-size: 15px;
    margin-bottom: 5px;
  }}
  .slot-badge {{
    min-width: 48px;
    height: 18px;
    padding: 0 5px;
    font-size: 11px;
  }}
  .rankline {{
    grid-template-columns: 34px minmax(0, 1fr);
    gap: 5px;
    margin-bottom: 5px;
  }}
  .rank-badge {{
    height: 20px;
    font-size: 13px;
  }}
  .rank-track {{
    height: 9px;
  }}
  .rank-score {{
    display: none;
  }}
  .preview,
  .preview.empty,
  .hash,
  ul,
  .err {{
    display: none;
  }}
  .metric {{
    font-size: 13px;
    line-height: 1.22;
  }}
}}
</style>
</head>
<body>
<div class="pstrip">
  <div class="phead">
    <div class="ptitle">{html.escape(title)}</div>
    <div class="pgame">{html.escape(game_label)}</div>
    <div class="psub">{html.escape(str(payload.get('phase', 'running')))} / {html.escape(compact_params or params_label or '-')} / {html.escape(counts_label)} / {generated}</div>
  </div>
  <div class="pcells">{''.join(pcells)}</div>
</div>
</body>
</html>
"""
    atomic_json(status_path, payload)
    html_path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".wildcard_parallel.", suffix=".html", dir=str(html_path.parent))
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(doc)
    os.replace(tmp, html_path)


def render_cleanup_overlay(status_path: Path, html_path: Path, detail: str = "cleanup_stale") -> None:
    previous: dict = {}
    try:
        previous = json.loads(status_path.read_text(encoding="utf-8"))
    except Exception:
        previous = {}
    payload = {
        "phase": "restored",
        "session_dir": previous.get("session_dir", ""),
        "params": previous.get("params", {}),
        "candidates": [],
        "ended_at": int(time.time()),
        "detail": detail,
    }
    render_overlay(status_path, html_path, payload)


def _safe_resolve(path: Path) -> Path:
    try:
        return path.resolve()
    except Exception:
        return path.absolute()


def status_active_session(status_path: Path) -> Path | None:
    try:
        data = json.loads(status_path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if str(data.get("phase") or "") not in {"generating", "running"}:
        return None
    session = str(data.get("session_dir") or "")
    if not session:
        return None
    return Path(session)


def cleanup_wildcard_session_dirs(
    session_root: Path,
    keep_session_dirs: list[Path] | None = None,
    keep_recent: int = 0,
) -> list[Path]:
    """Remove old WILDCARD run directories without touching active sessions."""
    keep_resolved = {_safe_resolve(path) for path in (keep_session_dirs or []) if path}
    keep_recent = max(0, _int(keep_recent, 0))
    try:
        root = _safe_resolve(session_root)
        runs = [
            path
            for path in root.iterdir()
            if path.is_dir()
            and not path.is_symlink()
            and re.fullmatch(r"run-\d{8}-\d{6}", path.name)
        ]
    except Exception:
        return []

    for path in sorted(runs, key=lambda p: p.name, reverse=True)[:keep_recent]:
        keep_resolved.add(_safe_resolve(path))

    removed: list[Path] = []
    for path in sorted(runs, key=lambda p: p.name):
        if _safe_resolve(path) in keep_resolved:
            continue
        cleanup_wildcard_chrome_processes(session_dir=path)
        try:
            shutil.rmtree(path)
        except Exception:
            continue
        removed.append(path)
    return removed


def copy_tree_or_link(src: Path, dst: Path) -> None:
    if dst.exists() or dst.is_symlink():
        return
    try:
        dst.symlink_to(src, target_is_directory=src.is_dir())
    except Exception:
        if src.is_dir():
            shutil.copytree(src, dst, symlinks=True)
        else:
            shutil.copy2(src, dst)


def _bool_literal(value: bool) -> str:
    return "True" if value else "False"


def ensure_deadline_fast_drop_param(path: Path, value: bool | None = None) -> tuple[int, str, str] | None:
    """Ensure strategy candidates expose the runtime fast-drop toggle.

    When value is None, preserve an existing assignment and only add the
    default parameter if it is missing. When value is provided, update the
    candidate so parallel WILDCARD can evaluate that runtime policy.
    """
    text = path.read_text(encoding="utf-8")
    assignment = f"FAST_DROP_DEADLINE_CONTACT = {_bool_literal(True if value is None else value)}"
    pattern = re.compile(r"^FAST_DROP_DEADLINE_CONTACT\s*=\s*(True|False|1|0)\s*$", re.MULTILINE)
    match = pattern.search(text)
    if match:
        old_line = match.group(0)
        if value is None or old_line == assignment:
            return None
        text = text[:match.start()] + assignment + text[match.end():]
        path.write_text(text, encoding="utf-8")
        line_no = text[:match.start()].count("\n") + 1
        return (line_no, old_line, assignment)

    block = (
        "\n# AI-tunable runtime parameter:\n"
        "# True  = deadline contact skips settle wait and drops immediately.\n"
        "# False = even during deadline contact, wait until the board is settled.\n"
        f"{assignment}\n"
    )
    marker = '# AI prohibited: decide() signature, if __name__ == "__main__" block'
    if marker in text:
        insert_at = text.index(marker) + len(marker)
        text = text[:insert_at] + block + text[insert_at:]
        line_no = text[:insert_at].count("\n") + 2
    else:
        text = block.lstrip("\n") + "\n" + text
        line_no = 1
    path.write_text(text, encoding="utf-8")
    return (line_no, "<missing>", assignment)


def parallel_deadline_fast_drop_value(args: argparse.Namespace, index: int, generation: int = 0) -> bool | None:
    if not getattr(args, "deadline_fast_drop_mutate", True):
        return None
    values = getattr(args, "deadline_fast_drop_values", None) or [True, False]
    if not values:
        return None
    return values[(index + generation) % len(values)]


def prepare_candidate_dir(base_dir: Path, job_id: str, strategy_source: Path, preserve_exact: bool = False) -> Path:
    workdir = base_dir / job_id
    workdir.mkdir(parents=True, exist_ok=True)
    for rel in [
        "analyze_board.py",
        "extract_decide_hash.py",
        "strategy_runner.py",
        "soviet_local.mjs",
        "package.json",
        "package-lock.json",
    ]:
        src = REPO_ROOT / rel
        if src.exists():
            shutil.copy2(src, workdir / rel)
    for rel in ["node_modules", "sorengame"]:
        src = REPO_ROOT / rel
        if src.exists():
            copy_tree_or_link(src, workdir / rel)
    helpers_src = REPO_ROOT / "strategy_helpers"
    if helpers_src.exists():
        shutil.copytree(helpers_src, workdir / "strategy_helpers", dirs_exist_ok=True)
    else:
        (workdir / "strategy_helpers").mkdir(exist_ok=True)
        (workdir / "strategy_helpers" / "__init__.py").touch()
    strategy_dst = workdir / "strategy.py"
    if strategy_source.resolve() != strategy_dst.resolve():
        shutil.copy2(strategy_source, strategy_dst)
    if not preserve_exact:
        ensure_deadline_fast_drop_param(strategy_dst)
    (workdir / "tmp" / "state").mkdir(parents=True, exist_ok=True)
    (workdir / "game_history").mkdir(exist_ok=True)
    (workdir / "commands.txt").write_text("", encoding="utf-8")
    (workdir / "game_state.json").write_text("{}", encoding="utf-8")
    (workdir / "game_count.txt").write_text("0\n", encoding="utf-8")
    (workdir / "score_history.txt").write_text("", encoding="utf-8")
    (workdir / "eval_score_history.txt").write_text("", encoding="utf-8")
    return workdir


def set_candidate_html_window_title(workdir: Path, slot_index: int) -> None:
    title = f"Wildcard Parallel Slot {slot_index + 1}"
    sorengame_dir = workdir / "sorengame"
    if sorengame_dir.is_symlink() or _safe_resolve(sorengame_dir) == _safe_resolve(REPO_ROOT / "sorengame"):
        try:
            if sorengame_dir.is_symlink():
                sorengame_dir.unlink()
            elif sorengame_dir.exists():
                shutil.rmtree(sorengame_dir)
            shutil.copytree(REPO_ROOT / "sorengame", sorengame_dir, symlinks=True)
        except Exception:
            return
    index_path = workdir / "sorengame" / "build" / "index.html"
    try:
        text = index_path.read_text(encoding="utf-8")
    except Exception:
        return
    text = re.sub(r"<title>.*?</title>", f"<title>{title}</title>", text, count=1, flags=re.S)
    text = re.sub(r'productName:\s*"[^"]*"', f'productName: "{title}"', text, count=1)
    index_path.write_text(text, encoding="utf-8")


def update_candidate_metrics(candidate: CandidateResult) -> None:
    if not candidate.scores:
        candidate.comp = 0.0
        candidate.p25 = 0.0
        candidate.p50 = 0.0
        return
    candidate.comp = composite(candidate.scores)
    candidate.p25 = quantile(candidate.scores, 0.25)
    candidate.p50 = median(candidate.scores)


def _score_list(value: object) -> list[int]:
    if not isinstance(value, list):
        return []
    return [_int(item, 0) for item in value if _int(item, 0) > 0]


def load_score_baseline(
    strategy_path: Path,
    session_dir: Path,
    rolling_scores_path: Path | None,
    current_run_path: Path | None,
) -> CandidateResult | None:
    strategy_hash = compute_strategy_hash(strategy_path)
    scores: list[int] = []
    source = ""

    if rolling_scores_path:
        try:
            rolling = json.loads(Path(rolling_scores_path).read_text(encoding="utf-8"))
            entry = rolling.get(strategy_hash) if isinstance(rolling, dict) else None
            scores = _score_list((entry or {}).get("scores")) if isinstance(entry, dict) else []
            if scores:
                source = "rolling_scores"
        except Exception:
            scores = []

    if current_run_path:
        try:
            current = json.loads(Path(current_run_path).read_text(encoding="utf-8"))
            current_scores = []
            if isinstance(current, dict) and current.get("hash") == strategy_hash:
                current_scores = _score_list(current.get("scores"))
            if current_scores:
                scores = scores + current_scores
                source = f"{source}+current_run" if source else "current_run"
        except Exception:
            pass

    if not scores:
        return None

    baseline = CandidateResult(
        job_id="baseline-score",
        index=-1,
        workdir=session_dir / "baseline-score",
        strategy_path=strategy_path,
        status="baseline",
        hash=strategy_hash,
        applied=[{"lineno": "-", "old": source or "source", "new": "virtual leader"}],
        scores=scores,
        raw_scores=list(scores),
        eval_scores=list(scores),
        score_baseline=True,
    )
    update_candidate_metrics(baseline)
    return baseline


def run_perturb(args: argparse.Namespace, index: int, session_dir: Path, generation: int = 0) -> CandidateResult:
    job_id = f"cand-{index + 1}" if generation <= 0 else f"cand-{index + 1}-r{generation + 1}"
    candidate_dir = session_dir / job_id
    candidate_dir.mkdir(parents=True, exist_ok=True)
    out_path = candidate_dir / "strategy.py"
    seed = args.seed + index + (generation * args.jobs)
    if getattr(args, "baseline_slot1", False) and index == 0 and generation == 0:
        shutil.copy2(args.strategy, out_path)
        result = CandidateResult(
            job_id=job_id,
            index=index,
            workdir=candidate_dir,
            strategy_path=out_path,
            seed=seed,
            generation=generation,
            baseline=True,
        )
        result.hash = compute_strategy_hash(out_path)
        return result
    cmd = [
        sys.executable,
        str(REPO_ROOT / "wildcard_perturb.py"),
        "--input",
        str(args.strategy),
        "--output",
        str(out_path),
        "--count",
        str(args.count),
        "--ratio-min",
        str(args.ratio_min),
        "--ratio-max",
        str(args.ratio_max),
        "--exclude-lines",
        args.exclude_lines,
        "--prefer-lines",
        args.prefer_lines,
        "--explore-rate",
        str(args.explore_rate),
        "--seed",
        str(seed),
    ]
    if getattr(args, "random_count", False):
        cmd.append("--random-count")
    result = CandidateResult(job_id=job_id, index=index, workdir=candidate_dir, strategy_path=out_path, seed=seed, generation=generation)
    proc = subprocess.run(cmd, cwd=REPO_ROOT, capture_output=True, text=True, timeout=args.perturb_timeout)
    if proc.returncode != 0:
        result.status = "failed"
        result.error = (proc.stderr or proc.stdout or f"wildcard_perturb rc={proc.returncode}").strip()
        return result
    try:
        payload = json.loads(proc.stdout)
    except Exception as err:
        result.status = "failed"
        result.error = f"invalid perturb json: {err}"
        return result
    result.applied = payload.get("applied") or []
    deadline_fast_drop_value = parallel_deadline_fast_drop_value(args, index, generation)
    deadline_param_change = ensure_deadline_fast_drop_param(out_path, deadline_fast_drop_value)
    if deadline_param_change:
        line_no, old_value, new_value = deadline_param_change
        result.applied.append(
            {
                "lineno": line_no,
                "old": old_value,
                "new": new_value,
                "runtime_param": "FAST_DROP_DEADLINE_CONTACT",
            }
        )
    result.hash = compute_strategy_hash(out_path)
    return result


def parse_runner_result(text: str) -> dict:
    marker = "---RESULT---"
    if marker not in text:
        raise RuntimeError("strategy_runner result marker missing")
    raw = text.rsplit(marker, 1)[1].strip().splitlines()[0]
    return json.loads(raw)


def runner_result_error(game: dict) -> str:
    error = str(game.get("error") or "").strip()
    if error:
        return error
    return ""


def archive_candidate_game_result(workdir: Path, job_id: str, game_index: int, game: dict) -> Path:
    history_dir = workdir / "game_history"
    history_dir.mkdir(parents=True, exist_ok=True)
    score = _int(game.get("score"), 0)
    out_path = history_dir / f"wildcard_parallel_{job_id}_game{game_index + 1}_score{score}.jsonl"
    latest = history_dir / "latest.jsonl"
    if latest.exists() and latest.stat().st_size > 0:
        shutil.copy2(latest, out_path)
        return out_path

    final_types = [_int(v, 0) for v in (game.get("final_types") or [])]
    row = {
        "turn": _int(game.get("turns"), 0),
        "score": score,
        "russia_created": bool(game.get("russia_created")) or any(t >= 15 for t in final_types),
        "soviet_created": bool(game.get("soviet_created")) or any(t >= 16 for t in final_types),
        "final_types": final_types,
        "state_snapshot": {"pieces": [{"type": t} for t in final_types]},
        "decision_reason": "wildcard_parallel_result_archive",
    }
    out_path.write_text(json.dumps(row, ensure_ascii=False) + "\n", encoding="utf-8")
    return out_path


TYPE_BONUS = {
    1: 0,
    2: 0,
    3: 1,
    4: 3,
    5: 7,
    6: 15,
    7: 32,
    8: 67,
    9: 141,
    10: 296,
    11: 622,
    12: 1306,
    13: 2743,
    14: 5760,
    15: 12096,
}


def eval_score(game: dict) -> int:
    """Match eloop.sh's eval_score_history scoring for candidate selection."""
    raw_score = _int(game.get("score"), 0)
    final_types = [_int(v, 0) for v in (game.get("final_types") or [])]
    bonus = sum(TYPE_BONUS.get(t, 0) for t in final_types)
    if game.get("soviet_created"):
        bonus += 800
    return raw_score + bonus


def tail_text(path: Path, limit: int = 500) -> str:
    try:
        if not path.exists():
            return ""
        return path.read_text(encoding="utf-8", errors="replace")[-limit:].strip()
    except Exception:
        return ""


def bridge_failure_detail(workdir: Path, rc: int | None = None) -> str:
    tmp_dir = workdir / "tmp"
    parts: list[str] = []
    if rc is not None:
        parts.append(f"bridge exited rc={rc}")
    else:
        parts.append("bridge did not produce game_state")

    for label, path in (
        ("stderr", tmp_dir / "soviet_local.stderr.log"),
        ("stdout", tmp_dir / "soviet_local.stdout.log"),
        ("exit", tmp_dir / "soviet_local.exit.log"),
    ):
        tail = tail_text(path)
        if tail:
            parts.append(f"{label}: {tail}")
        elif path.exists():
            parts.append(f"{label}: empty")
        else:
            parts.append(f"{label}: missing")
    return " | ".join(parts)


def wait_for_bridge_state(workdir: Path, proc: subprocess.Popen, timeout: int, predicate, label: str) -> dict:
    deadline = time.time() + timeout
    state_path = workdir / "game_state.json"
    while time.time() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(bridge_failure_detail(workdir, proc.returncode))
        try:
            data = json.loads(state_path.read_text(encoding="utf-8"))
            if data.get("state") and predicate(data):
                return data
        except Exception:
            pass
        time.sleep(0.5)
    raise RuntimeError(f"bridge timed out waiting for {label}: {bridge_failure_detail(workdir)}")


class TmuxBridgeProcess:
    def __init__(self, session_name: str, pid: int = 0):
        self.session_name = session_name
        self.pid = pid
        self.returncode: int | None = None

    def poll(self) -> int | None:
        if self.returncode is not None:
            return self.returncode
        proc = subprocess.run(
            ["tmux", "has-session", "-t", self.session_name],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        if proc.returncode == 0:
            return None
        self.returncode = 0
        return self.returncode

    def wait(self, timeout: float | None = None) -> int:
        deadline = None if timeout is None else time.time() + timeout
        while self.poll() is None:
            if deadline is not None and time.time() >= deadline:
                raise subprocess.TimeoutExpired(["tmux", self.session_name], timeout)
            time.sleep(0.2)
        return int(self.returncode or 0)

    def terminate(self) -> None:
        subprocess.run(["tmux", "kill-session", "-t", self.session_name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)


def _bridge_tmux_session_name(workdir: Path) -> str:
    digest = hashlib.sha1(str(workdir.resolve()).encode("utf-8")).hexdigest()[:12]
    return f"soren_wp_{digest}"


def _write_tmux_bridge_script(workdir: Path, env: dict, stdout_path: Path, stderr_path: Path) -> Path:
    script_path = workdir / "tmp" / "launch_soviet_bridge.sh"
    allowed_exact = {
        "PATH",
        "HOME",
        "TMPDIR",
        "XDG_CONFIG_HOME",
        "XDG_CACHE_HOME",
        "PLAYWRIGHT_BROWSERS_PATH",
        "RUSSIA_CELEBRATION_ENABLED",
    }
    allowed_prefixes = ("SOREN_", "WILDCARD_", "NODE_")
    lines = ["#!/bin/bash", "set -e", f"cd {shlex.quote(str(workdir.resolve()))}"]
    for key, value in sorted(env.items()):
        if key not in allowed_exact and not any(key.startswith(prefix) for prefix in allowed_prefixes):
            continue
        if value is None:
            continue
        lines.append(f"export {key}={shlex.quote(str(value))}")
    lines.append(f"exec node soviet_local.mjs >>{shlex.quote(str(stdout_path.resolve()))} 2>>{shlex.quote(str(stderr_path.resolve()))}")
    script_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    script_path.chmod(0o755)
    return script_path


def launch_bridge(workdir: Path, env: dict, timeout: int) -> subprocess.Popen:
    stdout_path = workdir / "tmp" / "soviet_local.stdout.log"
    stderr_path = workdir / "tmp" / "soviet_local.stderr.log"
    stdout_path.parent.mkdir(parents=True, exist_ok=True)
    if sys.platform == "darwin" and os.environ.get("WILDCARD_PARALLEL_BRIDGE_TMUX", "1") != "0" and shutil.which("tmux"):
        session_name = _bridge_tmux_session_name(workdir)
        script_path = _write_tmux_bridge_script(workdir, env, stdout_path, stderr_path)
        subprocess.run(["tmux", "kill-session", "-t", session_name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
        subprocess.run(
            ["tmux", "new-session", "-d", "-s", session_name, str(script_path.resolve())],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            check=True,
        )
        pid_proc = subprocess.run(
            ["tmux", "display-message", "-p", "-t", session_name, "#{pane_pid}"],
            capture_output=True,
            text=True,
            check=False,
        )
        try:
            pane_pid = int(pid_proc.stdout.strip() or "0")
        except Exception:
            pane_pid = 0
        proc = TmuxBridgeProcess(session_name, pane_pid)
        try:
            wait_for_bridge_state(workdir, proc, timeout, lambda data: True, "initial game_state")
            return proc
        except Exception:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except Exception:
                pass
            raise
    stdout_f = open(stdout_path, "ab")
    stderr_f = open(stderr_path, "ab")
    proc = subprocess.Popen(
        [resolve_node_bin(), "soviet_local.mjs"],
        cwd=workdir,
        env=env,
        stdout=stdout_f,
        stderr=stderr_f,
        start_new_session=True,
    )
    proc._soren_log_files = (stdout_f, stderr_f)  # type: ignore[attr-defined]
    try:
        wait_for_bridge_state(workdir, proc, timeout, lambda data: True, "initial game_state")
        return proc
    except Exception:
        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except Exception:
            try:
                proc.terminate()
            except Exception:
                pass
        try:
            proc.wait(timeout=5)
        except Exception:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except Exception:
                pass
        stdout_f.close()
        stderr_f.close()
        raise


def reset_bridge_for_next_game(workdir: Path, proc: subprocess.Popen, timeout: int) -> None:
    (workdir / "commands.txt").write_text("", encoding="utf-8")
    (workdir / "game_state.json").write_text("{}", encoding="utf-8")
    (workdir / "commands.txt").write_text("retry\n", encoding="utf-8")
    wait_for_bridge_state(
        workdir,
        proc,
        timeout,
        lambda data: data.get("state") == "MOVE",
        "retry MOVE state",
    )


def cleanup_chrome_profile_processes(profile_dir: str, cdp_port: int) -> None:
    """Stop orphaned candidate Chromium processes for this WILDCARD profile only."""
    if not profile_dir or "wildcard_parallel" not in profile_dir:
        return
    port_token = f"--remote-debugging-port={cdp_port}"
    profile_markers = {profile_dir}
    try:
        profile_markers.add(str(Path(profile_dir).resolve()))
    except Exception:
        pass
    try:
        proc = subprocess.run(
            ["ps", "-Ao", "pid=,command=", "-ww"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except Exception:
        return
    if proc.returncode != 0:
        return
    pids: list[int] = []
    for line in proc.stdout.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        pid_raw, _, command = stripped.partition(" ")
        try:
            pid = int(pid_raw)
        except Exception:
            continue
        if pid == os.getpid():
            continue
        if "Google Chrome for Testing" not in command and "Chromium" not in command:
            continue
        if "wildcard_parallel" not in command:
            continue
        if not any(marker and marker in command for marker in profile_markers) and port_token not in command:
            continue
        pids.append(pid)
    for sig in (signal.SIGTERM, signal.SIGKILL):
        for pid in pids:
            try:
                os.kill(pid, sig)
            except ProcessLookupError:
                pass
            except Exception:
                pass
        if sig == signal.SIGTERM and pids:
            time.sleep(0.8)


def cleanup_wildcard_chrome_processes(
    session_dir: Path | None = None,
    session_root: Path | None = None,
    exclude_markers: list[str] | None = None,
) -> None:
    """Stop WILDCARD-parallel Chromium processes left behind by interrupted runs.

    exclude_markers: if a process command contains any of these substrings it is
    spared. Used to keep the CURRENTLY ACTIVE session's Chrome alive while sweeping
    orphans from ended sessions (e.g. when a kept run dir still has live windows
    because WILDCARD_PARALLEL_KEEP_RECENT_RUNS preserved its directory).
    """
    excludes = [m for m in (exclude_markers or []) if m]
    markers = {"wildcard_parallel"}
    for path in (session_dir, session_root):
        if not path:
            continue
        try:
            markers.add(str(path))
            markers.add(str(path.resolve()))
        except Exception:
            markers.add(str(path))
    try:
        proc = subprocess.run(
            ["ps", "-Ao", "pid=,command=", "-ww"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except Exception:
        return
    if proc.returncode != 0:
        return
    pids: list[int] = []
    for line in proc.stdout.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        pid_raw, _, command = stripped.partition(" ")
        try:
            pid = int(pid_raw)
        except Exception:
            continue
        if pid == os.getpid():
            continue
        if "Google Chrome for Testing" not in command and "Chromium" not in command:
            continue
        if not any(marker and marker in command for marker in markers):
            continue
        if any(ex in command for ex in excludes):
            continue
        pids.append(pid)
    for sig in (signal.SIGTERM, signal.SIGKILL):
        for pid in pids:
            try:
                os.kill(pid, sig)
            except ProcessLookupError:
                pass
            except Exception:
                pass
        if sig == signal.SIGTERM and pids:
            time.sleep(0.8)


def cleanup_wildcard_server_ports(ports: list[int]) -> None:
    """Free stale WILDCARD candidate game servers before reusing fixed slots."""
    for port in sorted({p for p in ports if p > 0}):
        try:
            proc = subprocess.run(
                ["lsof", "-nP", f"-tiTCP:{port}", "-sTCP:LISTEN"],
                capture_output=True,
                text=True,
                timeout=3,
            )
        except Exception:
            continue
        if proc.returncode not in (0, 1):
            continue
        pids: list[int] = []
        for raw in proc.stdout.splitlines():
            try:
                pid = int(raw.strip())
            except Exception:
                continue
            if pid > 0 and pid != os.getpid():
                pids.append(pid)
        for sig in (signal.SIGTERM, signal.SIGKILL):
            for pid in pids:
                try:
                    os.kill(pid, sig)
                except ProcessLookupError:
                    pass
                except Exception:
                    pass
            if sig == signal.SIGTERM and pids:
                time.sleep(0.5)


def cleanup_ports_from_status(status_file: Path, fallback_base_port: int, fallback_jobs: int) -> list[int]:
    ports = [fallback_base_port + index for index in range(max(3, fallback_jobs))]
    try:
        data = json.loads(status_file.read_text(encoding="utf-8"))
    except Exception:
        return ports
    params = data.get("params")
    if isinstance(params, dict):
        jobs = _int(params.get("jobs"), 0)
        base = _int(params.get("serve_base_port"), 0)
        if jobs > 0 and base > 0:
            ports.extend(base + index for index in range(jobs))
    for candidate in data.get("candidates") or []:
        if not isinstance(candidate, dict):
            continue
        port = _int(candidate.get("serve_port"), 0)
        if port > 0:
            ports.append(port)
    return sorted({port for port in ports if port > 0})


def capture_candidate_preview(cdp_port: int, out_path: Path, cwd: Path) -> None:
    if not _overlay_preview_enabled():
        return
    out_path = out_path.resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    script = r"""
const { chromium } = require('playwright');
const port = Number(process.argv[1]);
const outPath = process.argv[2];
(async () => {
  const browser = await chromium.connectOverCDP(`http://127.0.0.1:${port}`);
  try {
    const pages = browser.contexts().flatMap(ctx => ctx.pages());
    const page = pages.find(p => !p.url().startsWith('about:blank')) || pages[0];
    if (!page) throw new Error('no page');
    await page.screenshot({ path: outPath, type: 'png', timeout: 5000 });
  } finally {
    await browser.close().catch(() => {});
  }
})().catch(err => {
  console.error(err && err.message ? err.message : String(err));
  process.exit(1);
});
"""
    try:
        subprocess.run(
            [resolve_node_bin(), "-e", script, str(cdp_port), str(out_path)],
            cwd=cwd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=8,
        )
    except Exception:
        pass


def set_candidate_window_title(cdp_port: int, title: str, cwd: Path) -> None:
    script = r"""
const { chromium } = require('playwright');
const port = Number(process.argv[1]);
const title = process.argv[2];
(async () => {
  const browser = await chromium.connectOverCDP(`http://127.0.0.1:${port}`);
  try {
    const pages = browser.contexts().flatMap(ctx => ctx.pages());
    const page = pages.find(p => !p.url().startsWith('about:blank')) || pages[0];
    if (!page) throw new Error('no page');
    await page.evaluate((nextTitle) => {
      document.title = nextTitle;
      const titleEl = document.querySelector('title') || document.head?.appendChild(document.createElement('title'));
      if (titleEl) titleEl.textContent = nextTitle;
    }, title);
  } finally {
    await browser.close().catch(() => {});
  }
})().catch(err => {
  console.error(err && err.message ? err.message : String(err));
  process.exit(1);
});
"""
    try:
        subprocess.run(
            [resolve_node_bin(), "-e", script, str(cdp_port), title],
            cwd=cwd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=8,
            check=False,
        )
    except Exception:
        pass


def _regex_escape(value: str) -> str:
    return re.escape(value)


def maybe_show_obs_candidate_source(candidate: CandidateResult) -> None:
    window_sources = os.environ.get(
        "WILDCARD_PARALLEL_OBS_WINDOW_SOURCES",
        os.environ.get("WILDCARD_PARALLEL_OBS_BROWSER_SOURCES", "1"),
    )
    if window_sources != "1":
        return
    if not candidate.cdp_port:
        return
    if not (REPO_ROOT / "obs_window_capture_source.sh").exists() or not (REPO_ROOT / "obs_control.sh").exists():
        return
    scene = os.environ.get("OBS_DASHBOARD_SCENE", "soren")
    prefix = os.environ.get("WILDCARD_PARALLEL_CANDIDATE_SOURCE_PREFIX", "wildcardParallelCand")
    source = f"{prefix}{candidate.index + 1}"
    title = f"Wildcard Parallel Cand {candidate.index + 1}"
    window_pattern = _regex_escape(title)
    # 3 cols x 2 rows tile grid filling the area BELOW the overlay strip:
    # x=[0,1920] (3*640), y=[200,1080] (strip 200 + 2*440). Tiles are a higher
    # OBS layer than the overlay, so they occupy the transparent area only and
    # never cover the opaque top strip.
    cols = max(1, _int(os.environ.get("WILDCARD_PARALLEL_OBS_CANDIDATE_COLS"), 3))
    w = _int(os.environ.get("WILDCARD_PARALLEL_OBS_CANDIDATE_W"), 640)
    h = _int(os.environ.get("WILDCARD_PARALLEL_OBS_CANDIDATE_H"), 440)
    col_stride = _int(os.environ.get("WILDCARD_PARALLEL_OBS_CANDIDATE_COL_STRIDE"), 640)
    row_stride = _int(os.environ.get("WILDCARD_PARALLEL_OBS_CANDIDATE_ROW_STRIDE"), 440)
    x = _int(os.environ.get("WILDCARD_PARALLEL_OBS_CANDIDATE_X"), 0) + (candidate.index % cols) * col_stride
    y = _int(os.environ.get("WILDCARD_PARALLEL_OBS_CANDIDATE_Y"), 200) + (candidate.index // cols) * row_stride
    log_path = REPO_ROOT / "tmp" / "debug" / "obs_control.err.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(log_path, "ab") as err:
            if candidate.cdp_port and candidate.workdir:
                set_candidate_window_title(candidate.cdp_port, title, candidate.workdir)
            subprocess.run(
                ["./obs_window_capture_source.sh", "ensure", scene, source, window_pattern, "com.google.chrome.for.testing", "show"],
                cwd=REPO_ROOT,
                stdout=subprocess.DEVNULL,
                stderr=err,
                timeout=8,
                check=False,
            )
            subprocess.run(
                ["./obs_control.sh", "transform", scene, source, str(x), str(y), "1", "1", str(w), str(h)],
                cwd=REPO_ROOT,
                env={
                    **os.environ,
                    "OBS_CONTROL_TRANSFORM_MODE": "force",
                    # crop-to-fill: cover the whole cell with no letterbox gaps
                    "OBS_CONTROL_BOUNDS_TYPE": os.environ.get(
                        "WILDCARD_PARALLEL_OBS_CANDIDATE_BOUNDS_TYPE",
                        "OBS_BOUNDS_SCALE_OUTER",
                    ),
                },
                stdout=subprocess.DEVNULL,
                stderr=err,
                timeout=8,
                check=False,
            )
    except Exception:
        pass


def stop_process(proc: subprocess.Popen | None) -> None:
    if not proc:
        return
    if isinstance(proc, TmuxBridgeProcess):
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except Exception:
                pass
        return
    log_files = getattr(proc, "_soren_log_files", ())
    if proc.poll() is not None:
        for f in log_files:
            try:
                f.close()
            except Exception:
                pass
        return
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except Exception:
        try:
            proc.terminate()
        except Exception:
            return
    try:
        proc.wait(timeout=5)
    except Exception:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except Exception:
            pass
    for f in log_files:
        try:
            f.close()
        except Exception:
            pass


def evaluate_real(
    candidate: CandidateResult,
    args: argparse.Namespace,
    session_dir: Path,
    progress_callback=None,
    slot_runtime: dict | None = None,
) -> CandidateResult:
    candidate.status = "running"
    reuse_slot_runtime = slot_runtime is not None
    if reuse_slot_runtime:
        runtime_workdir = slot_runtime.get("workdir")
        if runtime_workdir is None:
            runtime_workdir = session_dir / f"slot-{candidate.index + 1}"
            slot_runtime["workdir"] = runtime_workdir
        workdir = prepare_candidate_dir(Path(runtime_workdir), "", candidate.strategy_path, preserve_exact=candidate.baseline)
    else:
        workdir = prepare_candidate_dir(session_dir, candidate.job_id, candidate.strategy_path, preserve_exact=candidate.baseline)
    candidate.workdir = workdir
    candidate.strategy_path = workdir / "strategy.py"
    set_candidate_html_window_title(workdir, candidate.index)
    candidate.cdp_port = args.cdp_base_port + candidate.index
    candidate.serve_port = args.serve_base_port + candidate.index
    candidate.profile_dir = str((workdir / "tmp" / "chromium_profile").resolve())
    candidate_config_home = str((workdir / "tmp" / "config").resolve())
    candidate_cache_home = str((workdir / "tmp" / "cache").resolve())
    playwright_browsers_path = os.environ.get(
        "PLAYWRIGHT_BROWSERS_PATH",
        str(Path.home() / "Library" / "Caches" / "ms-playwright"),
    )
    chrome_app_path = os.environ.get("WILDCARD_PARALLEL_CHROME_APP_PATH", "")
    chrome_executable_path = os.environ.get("WILDCARD_PARALLEL_CHROME_EXECUTABLE_PATH", "")
    default_headless = "0" if sys.platform == "darwin" else "1"
    use_system_chrome = os.environ.get("WILDCARD_PARALLEL_USE_SYSTEM_CHROME", "0")
    if sys.platform == "darwin" and not chrome_app_path and not chrome_executable_path and use_system_chrome != "0":
        system_chrome_app = Path("/Applications/Google Chrome.app")
        system_chrome_exe = system_chrome_app / "Contents" / "MacOS" / "Google Chrome"
        if system_chrome_app.exists() and system_chrome_exe.exists():
            chrome_app_path = str(system_chrome_app)
            chrome_executable_path = chrome_executable_path or str(system_chrome_exe)
            default_headless = "0"
    if not chrome_app_path and not chrome_executable_path:
        chrome_executable_path = resolve_playwright_chrome_for_testing(playwright_browsers_path)
    if not chrome_app_path and chrome_executable_path:
        chrome_app_path = chrome_app_path_from_executable(chrome_executable_path)
    if progress_callback:
        progress_callback(candidate)
    env = os.environ.copy()
    env.pop("OBS_WEBSOCKET_PORT", None)
    env.pop("OBS_WEBSOCKET_PASSWORD", None)
    base_path = env.get("PATH", "")
    candidate_path = ":".join(
        part
        for part in [
            "/opt/homebrew/bin",
            "/usr/local/bin",
            "/usr/bin",
            "/bin",
            "/usr/sbin",
            "/sbin",
            base_path,
        ]
        if part
    )
    env.update(
        {
            "PATH": candidate_path,
            "HOME": str((workdir / "tmp" / "chrome_home").resolve()),
            "SOREN_CDP_PORT": str(candidate.cdp_port),
            "SOREN_SERVE_PORT": str(candidate.serve_port),
            "SOREN_LOCAL_USER_DATA_DIR": candidate.profile_dir,
            "SOREN_CHROME_HEADLESS": os.environ.get("WILDCARD_PARALLEL_HEADLESS", default_headless),
            "SOREN_LAUNCHSERVICES_HOME": str(Path.home()),
            "XDG_CONFIG_HOME": candidate_config_home,
            "XDG_CACHE_HOME": candidate_cache_home,
            "PLAYWRIGHT_BROWSERS_PATH": playwright_browsers_path,
            "SOREN_CHROME_HOME": str((workdir / "tmp" / "chrome_home").resolve()),
            "SOREN_CHROME_NO_FOCUS_LAUNCH": os.environ.get("WILDCARD_PARALLEL_NO_FOCUS_LAUNCH", "1"),
            "SOREN_CHROME_FORCE_PLAYWRIGHT_LAUNCH": os.environ.get("WILDCARD_PARALLEL_FORCE_PLAYWRIGHT_LAUNCH", "0"),
            "RUSSIA_CELEBRATION_ENABLED": "0",
            "SOREN_BRIDGE_DESYNC_LIMIT": os.environ.get("WILDCARD_PARALLEL_BRIDGE_DESYNC_LIMIT", "3"),
            "SOREN_BGM_VOLUME": os.environ.get("WILDCARD_PARALLEL_BGM_VOLUME", "0"),
            "SOREN_SE_VOLUME": os.environ.get("WILDCARD_PARALLEL_SE_VOLUME", "1.5"),
        }
    )
    if chrome_app_path:
        env["SOREN_CHROME_APP_PATH"] = chrome_app_path
    if chrome_executable_path:
        env["SOREN_CHROME_EXECUTABLE_PATH"] = chrome_executable_path
    existing_bridge = slot_runtime.get("bridge") if slot_runtime else None
    if existing_bridge is None or existing_bridge.poll() is not None:
        # Free any stale listener squatting on our CDP port (e.g. an orphaned chrome
        # left by a prior interrupted run) BEFORE prelaunch. Otherwise prelaunch's CDP
        # probe (wait_for_candidate_chrome_cdp) connects to the FOREIGN chrome that
        # answers on the port, the candidate silently attaches to the wrong browser,
        # never gets a real board, and is culled instantly -> infinite regeneration.
        # The main game runs on a different CDP port, so this never touches it.
        cleanup_wildcard_server_ports([candidate.cdp_port])
    if (
        existing_bridge is None
        and env.get("SOREN_CHROME_HEADLESS") not in {"1", "true", "yes", "on"}
        and env.get("SOREN_CHROME_NO_FOCUS_LAUNCH") != "0"
        and env.get("SOREN_CHROME_FORCE_PLAYWRIGHT_LAUNCH") != "1"
        and prelaunch_candidate_chrome(chrome_app_path, chrome_executable_path, candidate.profile_dir, candidate.cdp_port)
    ):
        env["SOREN_CHROME_ATTACH_ONLY"] = "1"
    try:
        bridge: subprocess.Popen | None = existing_bridge
        reused_bridge = bridge is not None and bridge.poll() is None
        if slot_runtime is not None:
            slot_runtime["env"] = env
        (workdir / "commands.txt").write_text("", encoding="utf-8")
        (workdir / "game_state.json").write_text("{}", encoding="utf-8")
        try:
            if bridge is None or bridge.poll() is not None:
                cleanup_wildcard_server_ports([candidate.serve_port])
                bridge = launch_bridge(workdir, env, args.bridge_timeout)
                if slot_runtime is not None:
                    slot_runtime["bridge"] = bridge
                maybe_show_obs_candidate_source(candidate)
        except Exception as err:
            candidate.error = str(err)
            candidate.game_results.append({"error": str(err), "game_index": 0})
            stop_process(bridge)
            cleanup_chrome_profile_processes(candidate.profile_dir, candidate.cdp_port)
            bridge = None
            if slot_runtime is not None:
                slot_runtime["bridge"] = None
        for game_index in range(args.games):
            (workdir / "commands.txt").write_text("", encoding="utf-8")
            try:
                (workdir / "game_history" / "latest.jsonl").write_text("", encoding="utf-8")
            except Exception:
                pass
            if bridge is None:
                break
            try:
                if game_index > 0 or reused_bridge:
                    reset_bridge_for_next_game(workdir, bridge, args.bridge_timeout)
                    reused_bridge = False
                capture_candidate_preview(candidate.cdp_port, workdir / "tmp" / "preview.png", workdir)
                proc = subprocess.Popen(
                    [sys.executable, "strategy_runner.py"],
                    cwd=workdir,
                    env=env,
                    text=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                )
                stdout = ""
                stderr = ""
                next_preview_at = 0.0
                game_timeout = max(30, _int(getattr(args, "game_timeout", 420), 420))
                game_deadline = time.time() + game_timeout
                stop_break = False
                while proc.poll() is None:
                    now = time.time()
                    if _stop_now():
                        # Signal received or wall-clock deadline hit: abort this game
                        # immediately instead of waiting up to game_timeout, so the
                        # worker returns and the executor can drain promptly.
                        try:
                            proc.terminate()
                            proc.communicate(timeout=5)
                        except Exception:
                            try:
                                proc.kill()
                            except Exception:
                                pass
                        if not candidate.error:
                            candidate.error = "stopped before completion (deadline/signal)"
                        stop_break = True
                        break
                    if now >= game_deadline:
                        try:
                            proc.terminate()
                            stdout, stderr = proc.communicate(timeout=5)
                        except subprocess.TimeoutExpired:
                            try:
                                proc.kill()
                            except Exception:
                                pass
                            stdout, stderr = proc.communicate(timeout=5)
                        candidate.status = "timeout"
                        candidate.error = f"strategy_runner timeout after {game_timeout}s"
                        candidate.game_results.append(
                            {
                                "error": candidate.error,
                                "game_index": game_index,
                                "stderr": (stderr or "")[-500:],
                            }
                        )
                        break
                    if now >= next_preview_at:
                        capture_candidate_preview(candidate.cdp_port, workdir / "tmp" / "preview.png", workdir)
                        next_preview_at = now + 2.0
                    time.sleep(0.25)
                if stop_break:
                    break
                if candidate.status == "timeout":
                    break
                stdout, stderr = proc.communicate(timeout=5)
                capture_candidate_preview(candidate.cdp_port, workdir / "tmp" / "preview.png", workdir)
                if proc.returncode != 0:
                    candidate.game_results.append({"error": f"strategy_runner rc={proc.returncode}", "stderr": stderr[-500:]})
                    continue
                try:
                    game = parse_runner_result(stdout)
                except Exception as err:
                    candidate.game_results.append({"error": str(err)})
                    continue
                game_error = runner_result_error(game)
                if game_error:
                    candidate.game_results.append(game)
                    candidate.status = "culled"
                    candidate.error = f"incomplete game culled without score: {game_error}"
                    break
                try:
                    game["archive_path"] = str(archive_candidate_game_result(workdir, candidate.job_id, game_index, game))
                except Exception as err:
                    game["archive_error"] = str(err)
                candidate.game_results.append(game)
                if "score" in game:
                    candidate.raw_scores.append(_int(game.get("score"), 0))
                    candidate.eval_scores.append(eval_score(game))
                    candidate.scores.append(eval_score(game))
                final_types = [_int(v, 0) for v in (game.get("final_types") or [])]
                candidate.max_type = max([candidate.max_type] + final_types)
                if game.get("russia_created") or any(t >= 15 for t in final_types):
                    candidate.russia_count += 1
                if game.get("soviet_created") or any(t >= 16 for t in final_types):
                    candidate.soviet_count += 1
                update_candidate_metrics(candidate)
                if progress_callback and progress_callback(candidate):
                    candidate.status = "culled"
                    if not candidate.error:
                        candidate.error = (
                            f"culled after {len(candidate.scores)} games: "
                            f"comp {candidate.comp:.1f} below current leader"
                        )
                    break
            finally:
                pass
            if candidate.status == "culled":
                break
        if not reuse_slot_runtime:
            stop_process(bridge)
            cleanup_chrome_profile_processes(candidate.profile_dir, candidate.cdp_port)
        if candidate.status == "culled":
            return candidate
        if candidate.scores:
            update_candidate_metrics(candidate)
            candidate.status = "accepted"
        else:
            candidate.status = "failed"
            if not candidate.error:
                candidate.error = "no successful games"
    except Exception as err:
        if not reuse_slot_runtime:
            try:
                stop_process(bridge)  # type: ignore[name-defined]
                cleanup_chrome_profile_processes(candidate.profile_dir, candidate.cdp_port)
            except Exception:
                pass
        candidate.status = "failed"
        candidate.error = str(err)
    return candidate


def evaluate_simulated(candidate: CandidateResult, args: argparse.Namespace, session_dir: Path) -> CandidateResult:
    workdir = prepare_candidate_dir(session_dir, candidate.job_id, candidate.strategy_path, preserve_exact=candidate.baseline)
    candidate.workdir = workdir
    candidate.strategy_path = workdir / "strategy.py"
    candidate.cdp_port = args.cdp_base_port + candidate.index
    candidate.serve_port = args.serve_base_port + candidate.index
    candidate.profile_dir = str((workdir / "tmp" / "chromium_profile").resolve())
    candidate.status = "accepted"
    base = 1000 + (candidate.index * 75)
    candidate.scores = [base + (i * 11) for i in range(args.games)]
    candidate.raw_scores = list(candidate.scores)
    candidate.eval_scores = list(candidate.scores)
    candidate.game_results = [{"score": score, "final_types": [12 + candidate.index]} for score in candidate.scores]
    update_candidate_metrics(candidate)
    candidate.max_type = 12 + candidate.index
    return candidate


def choose_winner(candidates: list[CandidateResult], min_successful_games: int) -> CandidateResult | None:
    def rank_key(c: CandidateResult) -> tuple[bool, bool, float, float, int]:
        return (c.russia_count > 0, c.soviet_count > 0, c.comp, c.p25, c.max_type)

    # The baseline (slot-1 played reference via --baseline-slot1, or a static
    # score_baseline loaded from history) is the comparison anchor, NOT a winner
    # candidate. A perturbed candidate must STRICTLY beat it (rank_key) to be
    # adopted — otherwise the live loop would replace the current strategy with a
    # worse one. Treat both baseline kinds the same here.
    def is_baseline(c: CandidateResult) -> bool:
        return c.score_baseline or c.baseline

    eligible = [
        c
        for c in candidates
        if not is_baseline(c) and c.status == "accepted" and len(c.scores) >= min_successful_games
    ]
    if not eligible:
        return None
    winner = max(eligible, key=rank_key)
    baseline_leaders = [c for c in candidates if is_baseline(c) and c.comp > 0]
    if baseline_leaders:
        source_leader = max(baseline_leaders, key=rank_key)
        if rank_key(winner) <= rank_key(source_leader):
            return None
    return winner


class CullCoordinator:
    def __init__(self, args: argparse.Namespace, status_file: Path, html_file: Path, session_dir: Path, candidates: list[CandidateResult]):
        self.args = args
        self.status_file = status_file
        self.html_file = html_file
        self.session_dir = session_dir
        self.candidates = list(candidates)
        self.lock = Lock()
        self.started_at = int(time.time())

    def _append_if_new(self, candidate: CandidateResult) -> None:
        for i, existing in enumerate(self.candidates):
            if existing.job_id == candidate.job_id:
                if existing is not candidate:
                    self.candidates[i] = candidate
                return
        self.candidates.append(candidate)

    def _snapshot_unlocked(self, phase: str = "running") -> None:
        render_overlay(
            self.status_file,
            self.html_file,
            {
                "phase": phase,
                "session_dir": str(self.session_dir),
                "params": wildcard_parallel_params(self.args),
                "started_at": self.started_at,
                "candidates": [c.public() for c in self.candidates],
            },
        )

    def snapshot(self, phase: str = "running") -> None:
        with self.lock:
            self._snapshot_unlocked(phase)

    def record(self, candidate: CandidateResult, phase: str = "running") -> None:
        with self.lock:
            self._append_if_new(candidate)
            self._snapshot_unlocked(phase)

    def should_cull(self, candidate: CandidateResult) -> bool:
        with self.lock:
            self._append_if_new(candidate)
            # The baseline (played slot-1 reference or static score_baseline) must
            # NEVER be culled: it has to finish its full games so it is a stable
            # comparison anchor for choose_winner. Culling it (the old bug) left the
            # run with no reference, so a worse-than-current winner got adopted.
            if candidate.baseline or candidate.score_baseline:
                self._snapshot_unlocked()
                return False
            completed_games = len(candidate.scores)
            if self.args.cull_after_games <= 0 or completed_games < self.args.cull_after_games:
                self._snapshot_unlocked()
                return False
            leaders = [
                c
                for c in self.candidates
                if c.job_id != candidate.job_id
                and (len(c.scores) >= self.args.cull_leader_min_games or c.score_baseline or c.baseline)
                and c.status in {"running", "accepted", "won", "leader", "baseline"}
                and c.comp > 0
            ]
            if not leaders:
                self._snapshot_unlocked()
                return False
            # Cull against the BASELINE's score specifically (the current strategy):
            # the intent is to keep perturbations that reach >= cull_comp_ratio of the
            # baseline. Using max-comp leader instead would let a lucky high-variance
            # candidate raise the bar and over-cull viable perturbations. Fall back to
            # the score leader only when no baseline is present in this run.
            baseline_leader = next((c for c in leaders if c.score_baseline or c.baseline), None)
            leader = baseline_leader if baseline_leader is not None else max(
                leaders, key=lambda c: (c.comp, c.p25, c.max_type)
            )
            threshold = leader.comp * self.args.cull_comp_ratio
            should = candidate.comp < threshold
            if should:
                candidate.cull_leader_job_id = leader.job_id
                candidate.cull_leader_games = len(leader.scores)
                candidate.cull_leader_comp = leader.comp
                candidate.cull_threshold = threshold
                candidate.error = f"culled after {len(candidate.scores)} games: comp {candidate.comp:.1f} < {self.args.cull_comp_ratio:.2f}x leader {leader.job_id} {leader.comp:.1f}"
            self._snapshot_unlocked()
            return should

    def _min_successful_games_unlocked(self) -> int:
        min_games = _int(getattr(self.args, "min_successful_games", 0), 0)
        if min_games <= 0:
            min_games = _int(getattr(self.args, "games", 0), 1)
        return max(1, min_games)

    def _eligible_slot_indices_unlocked(self) -> set[int]:
        min_games = self._min_successful_games_unlocked()
        return {
            c.index
            for c in self.candidates
            if not c.score_baseline and c.status in {"accepted", "won"} and len(c.scores) >= min_games
        }

    def lingering_slot_cutoff(self, index: int) -> tuple[bool, int]:
        with self.lock:
            limit = _int(getattr(self.args, "lingering_slot_max_culls", 0), 0)
            if limit <= 0:
                return (False, 0)
            cull_count = sum(1 for c in self.candidates if c.index == index and c.status == "culled")
            if cull_count > limit:
                return (True, cull_count)
            jobs = _int(getattr(self.args, "jobs", 0), 0)
            if jobs <= 0:
                jobs = max([c.index for c in self.candidates] + [index]) + 1
            eligible_slots = self._eligible_slot_indices_unlocked()
            unresolved_slots = {slot for slot in range(jobs) if slot not in eligible_slots}
            if unresolved_slots != {index}:
                return (False, 0)
            return (cull_count > limit, cull_count)


def evaluate_slot(index: int, first_candidate: CandidateResult, args: argparse.Namespace, session_dir: Path, coordinator: CullCoordinator) -> CandidateResult:
    candidate = first_candidate
    generation = candidate.generation
    slot_runtime: dict = {"workdir": session_dir / f"slot-{index + 1}"}
    try:
        while True:
            if _stop_now():
                # Stop requested (signal) or wall-clock deadline reached: do not start
                # another game; return whatever we have so the executor can drain.
                if not candidate.error:
                    candidate.error = "stopped before evaluation (deadline/signal)"
                coordinator.record(candidate)
                return candidate
            coordinator.record(candidate)
            if candidate.status == "failed":
                return candidate
            result = evaluate_real(candidate, args, session_dir, coordinator.should_cull, slot_runtime=slot_runtime)
            coordinator.record(result)
            if result.status == "accepted" and coordinator.should_cull(result):
                result.status = "culled"
                coordinator.record(result)
            if result.status != "culled":
                return result
            stop_lingering, cull_count = coordinator.lingering_slot_cutoff(index)
            if stop_lingering:
                suffix = f"lingering slot cutoff after {cull_count} culls"
                result.error = f"{result.error}; {suffix}" if result.error else suffix
                coordinator.record(result)
                return result
            if _stop_now():
                # Deadline/signal reached after a cull: stop regenerating. This is the
                # primary guard against the infinite perturb->cull->perturb loop that
                # ran 4.5h (431 generations) when every candidate kept failing to launch.
                suffix = "stopped: deadline/signal reached, not regenerating"
                result.error = f"{result.error}; {suffix}" if result.error else suffix
                coordinator.record(result)
                return result
            generation += 1
            candidate = run_perturb(args, index, session_dir, generation)
            coordinator.record(candidate)
    finally:
        bridge = slot_runtime.get("bridge")
        stop_process(bridge)
        workdir = slot_runtime.get("workdir")
        if workdir is not None:
            cleanup_chrome_profile_processes(
                str((Path(workdir) / "tmp" / "chromium_profile").resolve()),
                _int(getattr(args, "cdp_base_port", 19000), 19000) + index,
            )


def main() -> int:
    global _ACTIVE_ARGS, _ACTIVE_HTML_FILE, _ACTIVE_RESULT_FILE, _ACTIVE_SESSION_DIR, _ACTIVE_STATUS_FILE, _DEADLINE_TS
    parser = argparse.ArgumentParser()
    parser.add_argument("--cleanup-stale", action="store_true")
    parser.add_argument("--cleanup-sessions", action="store_true")
    parser.add_argument("--keep-session", type=Path, action="append", default=[])
    parser.add_argument("--keep-recent-runs", type=int, default=_int(os.getenv("WILDCARD_PARALLEL_KEEP_RECENT_RUNS"), 3))
    parser.add_argument("--strategy", type=Path, default=REPO_ROOT / "strategy.py")
    parser.add_argument("--jobs", type=int, default=_int(os.getenv("WILDCARD_PARALLEL_JOBS"), 6))
    parser.add_argument("--games", type=int, default=_int(os.getenv("WILDCARD_PARALLEL_GAMES"), 6))
    parser.add_argument("--count", type=int, default=1)
    parser.add_argument("--ratio-min", type=float, default=0.20)
    parser.add_argument("--ratio-max", type=float, default=0.40)
    parser.add_argument("--exclude-lines", default="")
    parser.add_argument("--prefer-lines", default="")
    parser.add_argument("--explore-rate", type=float, default=0.35)
    parser.add_argument(
        "--random-count",
        action=argparse.BooleanOptionalAction,
        default=os.getenv("WILDCARD_PERTURB_RANDOM_COUNT", "1").strip().lower() not in ("0", "false", "no", "off"),
    )
    parser.add_argument("--seed", type=int, default=int(time.time()))
    parser.add_argument("--evaluate-mode", choices=["real", "simulate"], default=os.getenv("WILDCARD_PARALLEL_EVALUATE_MODE", "real"))
    parser.add_argument("--session-root", type=Path, default=REPO_ROOT / os.getenv("WILDCARD_PARALLEL_WORK_DIR", "tmp/wildcard_parallel"))
    parser.add_argument("--status-file", type=Path, default=REPO_ROOT / os.getenv("WILDCARD_PARALLEL_STATUS_FILE", "tmp/state/wildcard_parallel_status.json"))
    parser.add_argument("--html-file", type=Path, default=REPO_ROOT / os.getenv("WILDCARD_PARALLEL_HTML_FILE", "tmp/state/wildcard_parallel_overlay.html"))
    parser.add_argument("--result-file", type=Path, default=None)
    parser.add_argument("--rolling-scores-file", type=Path, default=REPO_ROOT / os.getenv("ROLLING_SCORES_FILE", "tmp/state/rolling_scores.json"))
    parser.add_argument("--current-run-file", type=Path, default=REPO_ROOT / os.getenv("CURRENT_STRATEGY_RUN_FILE", "tmp/state/current_strategy_run.json"))
    parser.add_argument("--cdp-base-port", type=int, default=_int(os.getenv("WILDCARD_PARALLEL_CDP_BASE_PORT"), 19320))
    parser.add_argument("--serve-base-port", type=int, default=_int(os.getenv("WILDCARD_PARALLEL_SERVE_BASE_PORT"), 18080))
    parser.add_argument("--bridge-timeout", type=int, default=_int(os.getenv("WILDCARD_PARALLEL_BRIDGE_TIMEOUT"), 45))
    parser.add_argument("--game-timeout", type=int, default=_int(os.getenv("WILDCARD_PARALLEL_GAME_TIMEOUT"), 420))
    parser.add_argument("--max-runtime-sec", type=int, default=_int(os.getenv("WILDCARD_PARALLEL_MAX_RUNTIME_SEC"), 1500))
    parser.add_argument("--perturb-timeout", type=int, default=30)
    parser.add_argument("--min-successful-games", type=int, default=_int(os.getenv("WILDCARD_PARALLEL_MIN_SUCCESSFUL_GAMES"), 0))
    parser.add_argument("--cull-after-games", type=int, default=_int(os.getenv("WILDCARD_PARALLEL_CULL_AFTER_GAMES"), 1))
    parser.add_argument("--cull-leader-min-games", type=int, default=_int(os.getenv("WILDCARD_PARALLEL_CULL_LEADER_MIN_GAMES"), 2))
    parser.add_argument("--cull-comp-ratio", type=float, default=_float(os.getenv("WILDCARD_PARALLEL_CULL_COMP_RATIO"), 0.90))
    parser.add_argument("--lingering-slot-max-culls", type=int, default=_int(os.getenv("WILDCARD_PARALLEL_LINGERING_SLOT_MAX_CULLS"), 0))
    parser.add_argument(
        "--baseline-slot1",
        action=argparse.BooleanOptionalAction,
        default=os.getenv("WILDCARD_PARALLEL_BASELINE_SLOT1", "0").strip().lower() in ("1", "true", "yes", "on"),
    )
    parser.add_argument(
        "--block-main-loop",
        action=argparse.BooleanOptionalAction,
        default=os.getenv("WILDCARD_PARALLEL_BLOCK_MAIN_LOOP", "1").strip().lower() not in ("0", "false", "no", "off"),
    )
    args = parser.parse_args()
    args.deadline_fast_drop_mutate = os.getenv("WILDCARD_PARALLEL_FAST_DROP_DEADLINE_CONTACT_MUTATE", "1").strip().lower() not in ("0", "false", "no", "off")
    raw_deadline_fast_drop_values = os.getenv("WILDCARD_PARALLEL_FAST_DROP_DEADLINE_CONTACT_VALUES", "1,0")
    args.deadline_fast_drop_values = [
        item.strip().lower() not in ("0", "false", "no", "off")
        for item in raw_deadline_fast_drop_values.split(",")
        if item.strip()
    ]
    if not args.deadline_fast_drop_values:
        args.deadline_fast_drop_values = [True, False]

    if args.cleanup_stale:
        cleanup_wildcard_chrome_processes(session_root=args.session_root)
        cleanup_wildcard_server_ports(cleanup_ports_from_status(args.status_file, args.serve_base_port, args.jobs))
        if args.cleanup_sessions:
            cleanup_wildcard_session_dirs(
                args.session_root,
                keep_session_dirs=args.keep_session,
                keep_recent=args.keep_recent_runs,
            )
        render_cleanup_overlay(args.status_file, args.html_file)
        return 0
    if args.cleanup_sessions:
        keep_sessions = list(args.keep_session)
        active_session = status_active_session(args.status_file)
        if active_session:
            keep_sessions.append(active_session)
        cleanup_wildcard_session_dirs(
            args.session_root,
            keep_session_dirs=keep_sessions,
            keep_recent=args.keep_recent_runs,
        )
        # Sweep orphan Chrome from ENDED sessions even when their run dir is kept.
        # WILDCARD_PARALLEL_KEEP_RECENT_RUNS preserves the latest dir, and the
        # dir-removal path is the only place that killed leftover Chrome — so a
        # kept dir's windows linger forever on the main stream. Spare ONLY the
        # currently active session (None when nothing is running -> sweep all).
        exclude_markers = [str(active_session), active_session.name] if active_session else []
        cleanup_wildcard_chrome_processes(session_root=args.session_root, exclude_markers=exclude_markers)
        return 0

    args.jobs = max(3, args.jobs)
    args.games = max(1, args.games)
    args.game_timeout = max(30, args.game_timeout)
    if args.min_successful_games <= 0:
        args.min_successful_games = args.games
    args.cull_after_games = max(0, min(args.cull_after_games, args.games))
    args.cull_leader_min_games = max(1, min(args.cull_leader_min_games, args.games))
    args.cull_comp_ratio = max(0.0, args.cull_comp_ratio)
    args.lingering_slot_max_culls = max(0, args.lingering_slot_max_culls)
    args.max_runtime_sec = max(0, _int(getattr(args, "max_runtime_sec", 0), 0))
    # Overall wall-clock deadline: the orchestrator self-terminates instead of spinning
    # forever (and getting orphaned past the main-loop block timeout). 0 disables it.
    _DEADLINE_TS = (time.time() + args.max_runtime_sec) if args.max_runtime_sec > 0 else 0.0
    # Free both the candidate game-server ports AND the CDP debug ports up front so an
    # orphaned chrome from a prior interrupted run cannot squat them and starve every
    # new candidate (the root cause of the 4.5h runaway).
    cleanup_wildcard_server_ports([args.serve_base_port + index for index in range(args.jobs)])
    cleanup_wildcard_server_ports([args.cdp_base_port + index for index in range(args.jobs)])
    cleanup_wildcard_chrome_processes(session_root=args.session_root)
    keep_sessions = []
    active_session = status_active_session(args.status_file)
    if active_session:
        keep_sessions.append(active_session)
    cleanup_wildcard_session_dirs(
        args.session_root,
        keep_session_dirs=keep_sessions,
        keep_recent=args.keep_recent_runs,
    )
    session_dir = args.session_root / time.strftime("run-%Y%m%d-%H%M%S")
    _ACTIVE_SESSION_DIR = session_dir
    session_dir.mkdir(parents=True, exist_ok=True)
    result_file = args.result_file or (session_dir / "result.json")
    _ACTIVE_ARGS = args
    _ACTIVE_RESULT_FILE = result_file
    _ACTIVE_STATUS_FILE = args.status_file
    _ACTIVE_HTML_FILE = args.html_file

    params = wildcard_parallel_params(args)
    payload = {
        "phase": "generating",
        "session_dir": str(session_dir),
        "params": params,
        "block_main_loop": bool(args.block_main_loop),
        "candidates": [],
    }
    render_overlay(args.status_file, args.html_file, payload)

    score_baseline = None
    if not args.baseline_slot1:
        score_baseline = load_score_baseline(args.strategy, session_dir, args.rolling_scores_file, args.current_run_file)
    candidates = [run_perturb(args, index, session_dir) for index in range(args.jobs)]
    display_candidates = ([score_baseline] if score_baseline else []) + candidates
    payload["candidates"] = [c.public() for c in display_candidates]
    render_overlay(args.status_file, args.html_file, payload)
    if not any(c.status == "pending" for c in candidates):
        payload["phase"] = "failed"
        atomic_json(
            result_file,
            {
                "ok": False,
                "reason": "all_perturb_failed",
                "session_dir": str(session_dir),
                "params": params,
                "candidates": [c.public() for c in display_candidates],
            },
        )
        render_overlay(args.status_file, args.html_file, payload)
        print(json.dumps(json.loads(result_file.read_text(encoding="utf-8")), ensure_ascii=False))
        return 2

    evaluated: list[CandidateResult] = []
    payload["phase"] = "running"
    try:
        if args.evaluate_mode == "simulate":
            with ThreadPoolExecutor(max_workers=args.jobs) as pool:
                futures = {
                    pool.submit(evaluate_simulated, c, args, session_dir): c
                    for c in candidates
                    if c.status == "pending" and not c.score_baseline
                }
                for future in as_completed(futures):
                    evaluated.append(future.result())
                    merged = ([score_baseline] if score_baseline else []) + evaluated + [c for c in candidates if c not in evaluated and c.status != "failed"]
                    payload["candidates"] = [c.public() for c in merged]
                    render_overlay(args.status_file, args.html_file, payload)
            failed = [c for c in candidates if c.status == "failed" and c not in evaluated]
            all_candidates = ([score_baseline] if score_baseline else []) + evaluated + failed
        else:
            coordinator = CullCoordinator(
                args,
                args.status_file,
                args.html_file,
                session_dir,
                display_candidates,
            )
            with ThreadPoolExecutor(max_workers=args.jobs) as pool:
                futures = {
                    pool.submit(evaluate_slot, c.index, c, args, session_dir, coordinator): c
                    for c in candidates
                    if c.status == "pending" and not c.score_baseline
                }
                for future in as_completed(futures):
                    evaluated.append(future.result())
                    coordinator.snapshot()
            all_candidates = list(coordinator.candidates)
        result, payload, exit_code = build_parallel_result(args, session_dir, all_candidates)
        render_overlay(args.status_file, args.html_file, payload)
        atomic_json(result_file, result)
        print(json.dumps(result, ensure_ascii=False))
        return exit_code
    finally:
        cleanup_wildcard_chrome_processes(session_dir=session_dir)
        _ACTIVE_ARGS = None
        _ACTIVE_RESULT_FILE = None
        _ACTIVE_STATUS_FILE = None
        _ACTIVE_HTML_FILE = None
        _ACTIVE_SESSION_DIR = None


if __name__ == "__main__":
    # Seconds to wait for the cooperative drain before force-exiting. Kept above the
    # bridge launch timeout (45s) so a normal stop drains gracefully (workers return,
    # their finally blocks stop bridges) and the watchdog only fires for a real hang.
    _WATCHDOG_GRACE_SEC = _int(os.getenv("WILDCARD_PARALLEL_STOP_GRACE_SEC"), 60)

    def _handle_signal(signum: int, _frame: object) -> None:
        # 1) Ask every worker to stop cooperatively. The old handler raised SystemExit
        #    here, which then blocked forever in ThreadPoolExecutor.__exit__ joining
        #    workers stuck in the infinite perturb->cull loop. Setting the event instead
        #    lets the workers return so the executor can actually drain.
        _STOP_EVENT.set()
        # 2) Persist a best-effort result now, in case the graceful drain can't finish
        #    and the watchdog has to force-exit below.
        if _ACTIVE_ARGS and _ACTIVE_STATUS_FILE and _ACTIVE_HTML_FILE and _ACTIVE_RESULT_FILE and _ACTIVE_SESSION_DIR:
            try:
                write_interrupted_result_from_status(
                    _ACTIVE_ARGS,
                    _ACTIVE_STATUS_FILE,
                    _ACTIVE_HTML_FILE,
                    _ACTIVE_RESULT_FILE,
                    _ACTIVE_SESSION_DIR,
                    signum,
                )
            except Exception:
                pass

        # 3) Watchdog backstop: if the drain wedges, force the process down (and reap
        #    its chrome + node bridges) so SIGTERM can never hang like it did before.
        def _force_exit() -> None:
            try:
                cleanup_wildcard_chrome_processes(session_dir=_ACTIVE_SESSION_DIR)
            except Exception:
                pass
            try:
                if _ACTIVE_ARGS is not None:
                    jobs = max(3, _int(getattr(_ACTIVE_ARGS, "jobs", 6), 6))
                    base_serve = _int(getattr(_ACTIVE_ARGS, "serve_base_port", 18080), 18080)
                    base_cdp = _int(getattr(_ACTIVE_ARGS, "cdp_base_port", 19320), 19320)
                    # node bridges listen on the serve ports; chrome on the cdp ports.
                    cleanup_wildcard_server_ports(
                        [base_serve + i for i in range(jobs)] + [base_cdp + i for i in range(jobs)]
                    )
            except Exception:
                pass
            os._exit(128 + signum)

        watchdog = Timer(_WATCHDOG_GRACE_SEC, _force_exit)
        watchdog.daemon = True
        watchdog.start()
        # Return (do NOT raise): main() finishes via the cooperative drain and exits
        # cleanly; if it can't, the watchdog above forces termination.

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)
    raise SystemExit(main())
