#!/usr/bin/env python3
"""Parallel WILDCARD candidate runner.

Builds several isolated WILDCARD perturbation candidates, evaluates each in its
own runtime directory, and reports a single winner for the live loop to adopt.
"""

from __future__ import annotations

import argparse
import html
import json
import math
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from statistics import median
from threading import Lock


REPO_ROOT = Path(__file__).resolve().parent


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
    cdp_port: int = 0
    serve_port: int = 0
    profile_dir: str = ""

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
            "games": len(self.scores),
            "comp": round(self.comp, 2),
            "p25": round(self.p25, 2),
            "p50": round(self.p50, 2),
            "max_type": self.max_type,
            "russia_count": self.russia_count,
            "soviet_count": self.soviet_count,
            "error": self.error,
            "cdp_port": self.cdp_port,
            "serve_port": self.serve_port,
            "profile_dir": self.profile_dir,
            "preview_path": str(self.workdir / "tmp" / "preview.png"),
        }


def render_overlay(status_path: Path, html_path: Path, payload: dict) -> None:
    candidates = payload.get("candidates") or []
    generated = time.strftime("%H:%M:%S")
    ranked_candidates = sorted(
        [c for c in candidates if _float(c.get("comp"), 0.0) > 0 and _int(c.get("games"), 0) > 0],
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
    leader_label = str(ranked_candidates[0].get("job_id", "-")) if ranked_candidates else "-"
    cards = []
    for cand in candidates:
        applied = cand.get("applied") or []
        changes = []
        for item in applied[:6]:
            changes.append(
                f"L{html.escape(str(item.get('lineno', '?')))} "
                f"{html.escape(str(item.get('old', '?')))} -> {html.escape(str(item.get('new', '?')))}"
            )
        if not changes:
            changes = ["no perturbation yet"]
        status = str(cand.get("status") or "pending")
        display_status = "finished" if status == "accepted" else status
        klass = "bad" if status in {"failed", "timeout", "culled"} else "good" if status == "won" else "run"
        job_id = str(cand.get("job_id", "-"))
        rank = rank_by_job.get(job_id)
        if rank == 1:
            klass += " leader"
        rank_label = f"#{rank}" if rank is not None else "--"
        rank_class = f"r{rank}" if rank is not None and rank <= 3 else "rx"
        comp = _float(cand.get("comp"), 0.0)
        bar_width = 0 if best_comp <= 0 else max(0, min(100, round((comp / best_comp) * 100)))
        rank_html = (
            f"""
              <div class="rankline">
                <div class="rank-badge {rank_class}">{html.escape(rank_label)}</div>
                <div class="rank-track"><div class="rank-fill" style="width:{bar_width}%"></div></div>
                <div class="rank-score">{html.escape(str(cand.get('comp', 0)))}</div>
              </div>
            """
        )
        preview_uri = ""
        preview_path = str(cand.get("preview_path") or "")
        if preview_path:
            try:
                path_obj = Path(preview_path)
                if not path_obj.is_absolute():
                    path_obj = REPO_ROOT / path_obj
                if path_obj.exists():
                    preview_uri = path_obj.resolve().as_uri()
            except Exception:
                preview_uri = ""
        preview_html = (
            f'<img class="preview live-preview" data-src="{html.escape(preview_uri)}" src="{html.escape(preview_uri)}?t={int(time.time())}" alt="">'
            if preview_uri
            else '<div class="preview empty">waiting for preview</div>'
        )
        cards.append(
            f"""
            <section class="card {klass}">
              <div class="top"><b>{html.escape(job_id)}</b><span>{html.escape(display_status)}</span></div>
              {rank_html}
              {preview_html}
              <div class="metric">games {html.escape(str(cand.get('games', 0)))} / comp {html.escape(str(cand.get('comp', 0)))}</div>
              <div class="metric">p25 {html.escape(str(cand.get('p25', 0)))} / p50 {html.escape(str(cand.get('p50', 0)))}</div>
              <div class="metric">T{html.escape(str(cand.get('max_type', 0)))} R{html.escape(str(cand.get('russia_count', 0)))} S{html.escape(str(cand.get('soviet_count', 0)))}</div>
              <div class="hash">{html.escape(str(cand.get('hash', ''))[:12])}</div>
              <ul>{''.join(f'<li>{line}</li>' for line in changes)}</ul>
              <div class="err">{html.escape(str(cand.get('error') or ''))[:180]}</div>
            </section>
            """
        )
    if not cards:
        cards.append('<section class="card run"><div class="top"><b>waiting</b><span>pending</span></div></section>')

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
.wrap {{
  box-sizing: border-box;
  width: 100vw;
  height: 100vh;
  padding: 18px;
  background: rgba(3, 8, 13, 0.86);
  border-left: 6px solid #f59e0b;
}}
.head {{
  display: flex;
  align-items: baseline;
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
}}
.grid {{
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 12px;
  height: calc(100vh - 74px);
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
.top span {{
  color: #fde68a;
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
@media (max-height: 220px) {{
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
  .grid {{
    grid-template-columns: repeat(6, minmax(0, 1fr));
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
<main class="wrap">
  <div class="head">
    <div class="title">WILDCARD PARALLEL TRIAL</div>
    <div class="sub">{html.escape(str(payload.get('phase', 'running')))} / leader {html.escape(leader_label)} / {generated}</div>
  </div>
  <div class="grid">{''.join(cards[:6])}</div>
</main>
<script>
(() => {{
  const refresh = () => {{
    const stamp = Date.now();
    document.querySelectorAll('img.live-preview[data-src]').forEach((img) => {{
      img.src = `${{img.dataset.src}}?t=${{stamp}}`;
    }});
  }};
  setInterval(refresh, 2000);
}})();
</script>
</body>
</html>
"""
    atomic_json(status_path, payload)
    html_path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".wildcard_parallel.", suffix=".html", dir=str(html_path.parent))
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(doc)
    os.replace(tmp, html_path)


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


def prepare_candidate_dir(base_dir: Path, job_id: str, strategy_source: Path) -> Path:
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
    (workdir / "tmp" / "state").mkdir(parents=True, exist_ok=True)
    (workdir / "game_history").mkdir(exist_ok=True)
    (workdir / "commands.txt").write_text("", encoding="utf-8")
    (workdir / "game_state.json").write_text("{}", encoding="utf-8")
    (workdir / "game_count.txt").write_text("0\n", encoding="utf-8")
    (workdir / "score_history.txt").write_text("", encoding="utf-8")
    (workdir / "eval_score_history.txt").write_text("", encoding="utf-8")
    return workdir


def update_candidate_metrics(candidate: CandidateResult) -> None:
    if not candidate.scores:
        candidate.comp = 0.0
        candidate.p25 = 0.0
        candidate.p50 = 0.0
        return
    candidate.comp = composite(candidate.scores)
    candidate.p25 = quantile(candidate.scores, 0.25)
    candidate.p50 = median(candidate.scores)


def run_perturb(args: argparse.Namespace, index: int, session_dir: Path, generation: int = 0) -> CandidateResult:
    job_id = f"cand-{index + 1}" if generation <= 0 else f"cand-{index + 1}-r{generation + 1}"
    candidate_dir = session_dir / job_id
    candidate_dir.mkdir(parents=True, exist_ok=True)
    out_path = candidate_dir / "strategy.py"
    seed = args.seed + index + (generation * args.jobs)
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
    result.hash = compute_strategy_hash(out_path)
    return result


def parse_runner_result(text: str) -> dict:
    marker = "---RESULT---"
    if marker not in text:
        raise RuntimeError("strategy_runner result marker missing")
    raw = text.rsplit(marker, 1)[1].strip().splitlines()[0]
    return json.loads(raw)


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


def launch_bridge(workdir: Path, env: dict, timeout: int) -> subprocess.Popen:
    stdout_path = workdir / "tmp" / "soviet_local.stdout.log"
    stderr_path = workdir / "tmp" / "soviet_local.stderr.log"
    stdout_path.parent.mkdir(parents=True, exist_ok=True)
    stdout_f = open(stdout_path, "ab")
    stderr_f = open(stderr_path, "ab")
    proc = subprocess.Popen(
        ["node", "soviet_local.mjs"],
        cwd=workdir,
        env=env,
        stdout=stdout_f,
        stderr=stderr_f,
        start_new_session=True,
    )
    proc._soren_log_files = (stdout_f, stderr_f)  # type: ignore[attr-defined]
    deadline = time.time() + timeout
    state_path = workdir / "game_state.json"
    while time.time() < deadline:
        if proc.poll() is not None:
            stdout_f.close()
            stderr_f.close()
            raise RuntimeError(bridge_failure_detail(workdir, proc.returncode))
        try:
            data = json.loads(state_path.read_text(encoding="utf-8"))
            if data.get("state"):
                return proc
        except Exception:
            pass
        time.sleep(0.5)
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
    raise RuntimeError(bridge_failure_detail(workdir))


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
            ["ps", "-Ao", "pid=,command="],
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


def capture_candidate_preview(cdp_port: int, out_path: Path, cwd: Path) -> None:
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
            ["node", "-e", script, str(cdp_port), str(out_path)],
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
            ["node", "-e", script, str(cdp_port), title],
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
    title = f"Wildcard Parallel Cand {candidate.index + 1} | soren-game"
    window_pattern = _regex_escape(title)
    cols = max(1, _int(os.environ.get("WILDCARD_PARALLEL_OBS_CANDIDATE_COLS"), 3))
    w = int(os.environ.get("WILDCARD_PARALLEL_OBS_CANDIDATE_W", "660"))
    h = int(os.environ.get("WILDCARD_PARALLEL_OBS_CANDIDATE_H", "425"))
    x = int(os.environ.get("WILDCARD_PARALLEL_OBS_CANDIDATE_X", "-30")) + (candidate.index % cols) * w
    y = int(os.environ.get("WILDCARD_PARALLEL_OBS_CANDIDATE_Y", "180")) + (candidate.index // cols) * h
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
                env={**os.environ, "OBS_CONTROL_TRANSFORM_MODE": "force"},
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


def evaluate_real(candidate: CandidateResult, args: argparse.Namespace, session_dir: Path, progress_callback=None) -> CandidateResult:
    candidate.status = "running"
    workdir = prepare_candidate_dir(session_dir, candidate.job_id, candidate.strategy_path)
    candidate.workdir = workdir
    candidate.strategy_path = workdir / "strategy.py"
    candidate.cdp_port = args.cdp_base_port + candidate.index
    candidate.serve_port = args.serve_base_port + candidate.index
    candidate.profile_dir = str(workdir / "tmp" / "chromium_profile")
    env = os.environ.copy()
    env.pop("OBS_WEBSOCKET_PORT", None)
    env.pop("OBS_WEBSOCKET_PASSWORD", None)
    env.update(
        {
            "SOREN_CDP_PORT": str(candidate.cdp_port),
            "SOREN_SERVE_PORT": str(candidate.serve_port),
            "SOREN_LOCAL_USER_DATA_DIR": candidate.profile_dir,
            "SOREN_CHROME_NO_FOCUS_LAUNCH": "0",
            "RUSSIA_CELEBRATION_ENABLED": "0",
            "SOREN_BRIDGE_DESYNC_LIMIT": os.environ.get("WILDCARD_PARALLEL_BRIDGE_DESYNC_LIMIT", "3"),
            "SOREN_BGM_VOLUME": os.environ.get("WILDCARD_PARALLEL_BGM_VOLUME", "0"),
            "SOREN_SE_VOLUME": os.environ.get("WILDCARD_PARALLEL_SE_VOLUME", "1.5"),
        }
    )
    try:
        for game_index in range(args.games):
            bridge: subprocess.Popen | None = None
            (workdir / "commands.txt").write_text("", encoding="utf-8")
            (workdir / "game_state.json").write_text("{}", encoding="utf-8")
            try:
                (workdir / "game_history" / "latest.jsonl").write_text("", encoding="utf-8")
            except Exception:
                pass
            try:
                bridge = launch_bridge(workdir, env, args.bridge_timeout)
                maybe_show_obs_candidate_source(candidate)
            except Exception as err:
                candidate.game_results.append({"error": str(err), "game_index": game_index})
                continue
            try:
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
                while proc.poll() is None:
                    now = time.time()
                    if now >= next_preview_at:
                        capture_candidate_preview(candidate.cdp_port, workdir / "tmp" / "preview.png", workdir)
                        next_preview_at = now + 2.0
                    time.sleep(0.25)
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
                candidate.game_results.append(game)
                if "score" in game:
                    candidate.raw_scores.append(_int(game.get("score"), 0))
                    candidate.eval_scores.append(eval_score(game))
                    candidate.scores.append(eval_score(game))
                final_types = [_int(v, 0) for v in (game.get("final_types") or [])]
                candidate.max_type = max([candidate.max_type] + final_types)
                if game.get("russia_created") or candidate.max_type >= 15:
                    candidate.russia_count += 1
                if game.get("soviet_created") or candidate.max_type >= 16:
                    candidate.soviet_count += 1
                update_candidate_metrics(candidate)
                if progress_callback and progress_callback(candidate):
                    candidate.status = "culled"
                    candidate.error = (
                        f"culled after {len(candidate.scores)} games: "
                        f"comp {candidate.comp:.1f} below current leader"
                    )
                    break
            finally:
                stop_process(bridge)
                cleanup_chrome_profile_processes(candidate.profile_dir, candidate.cdp_port)
            if candidate.status == "culled":
                break
        if candidate.status == "culled":
            return candidate
        if candidate.scores:
            update_candidate_metrics(candidate)
            candidate.status = "accepted"
        else:
            candidate.status = "failed"
            candidate.error = "no successful games"
    except Exception as err:
        candidate.status = "failed"
        candidate.error = str(err)
    return candidate


def evaluate_simulated(candidate: CandidateResult, args: argparse.Namespace, session_dir: Path) -> CandidateResult:
    workdir = prepare_candidate_dir(session_dir, candidate.job_id, candidate.strategy_path)
    candidate.workdir = workdir
    candidate.strategy_path = workdir / "strategy.py"
    candidate.cdp_port = args.cdp_base_port + candidate.index
    candidate.serve_port = args.serve_base_port + candidate.index
    candidate.profile_dir = str(workdir / "tmp" / "chromium_profile")
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
    eligible = [c for c in candidates if c.status == "accepted" and len(c.scores) >= min_successful_games]
    if not eligible:
        return None
    return max(eligible, key=lambda c: (c.russia_count > 0, c.soviet_count > 0, c.comp, c.p25, c.max_type))


class CullCoordinator:
    def __init__(self, args: argparse.Namespace, status_file: Path, html_file: Path, session_dir: Path, candidates: list[CandidateResult]):
        self.args = args
        self.status_file = status_file
        self.html_file = html_file
        self.session_dir = session_dir
        self.candidates = list(candidates)
        self.lock = Lock()

    def _append_if_new(self, candidate: CandidateResult) -> None:
        if not any(c.job_id == candidate.job_id for c in self.candidates):
            self.candidates.append(candidate)

    def _snapshot_unlocked(self, phase: str = "running") -> None:
        render_overlay(
            self.status_file,
            self.html_file,
            {"phase": phase, "session_dir": str(self.session_dir), "candidates": [c.public() for c in self.candidates]},
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
            completed_games = len(candidate.scores)
            if self.args.cull_after_games <= 0 or completed_games < self.args.cull_after_games:
                self._snapshot_unlocked()
                return False
            leaders = [
                c
                for c in self.candidates
                if c.job_id != candidate.job_id
                and len(c.scores) > 0
                and c.status in {"running", "accepted", "won"}
                and c.comp > 0
            ]
            if not leaders:
                self._snapshot_unlocked()
                return False
            leader = max(leaders, key=lambda c: (c.comp, c.p25, c.max_type))
            threshold = leader.comp * self.args.cull_comp_ratio
            should = candidate.comp < threshold
            if should:
                candidate.error = f"culled after {len(candidate.scores)} games: comp {candidate.comp:.1f} < {self.args.cull_comp_ratio:.2f}x leader {leader.job_id} {leader.comp:.1f}"
            self._snapshot_unlocked()
            return should


def evaluate_slot(index: int, first_candidate: CandidateResult, args: argparse.Namespace, session_dir: Path, coordinator: CullCoordinator) -> CandidateResult:
    candidate = first_candidate
    generation = candidate.generation
    while True:
        coordinator.record(candidate)
        if candidate.status == "failed":
            return candidate
        result = evaluate_real(candidate, args, session_dir, coordinator.should_cull)
        coordinator.record(result)
        if result.status == "accepted" and coordinator.should_cull(result):
            result.status = "culled"
            coordinator.record(result)
        if result.status != "culled":
            return result
        generation += 1
        candidate = run_perturb(args, index, session_dir, generation)
        coordinator.record(candidate)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--strategy", type=Path, default=REPO_ROOT / "strategy.py")
    parser.add_argument("--jobs", type=int, default=_int(os.getenv("WILDCARD_PARALLEL_JOBS"), 6))
    parser.add_argument("--games", type=int, default=_int(os.getenv("WILDCARD_PARALLEL_GAMES"), 6))
    parser.add_argument("--count", type=int, default=1)
    parser.add_argument("--ratio-min", type=float, default=0.20)
    parser.add_argument("--ratio-max", type=float, default=0.40)
    parser.add_argument("--exclude-lines", default="")
    parser.add_argument("--prefer-lines", default="")
    parser.add_argument("--explore-rate", type=float, default=0.35)
    parser.add_argument("--seed", type=int, default=int(time.time()))
    parser.add_argument("--evaluate-mode", choices=["real", "simulate"], default=os.getenv("WILDCARD_PARALLEL_EVALUATE_MODE", "real"))
    parser.add_argument("--session-root", type=Path, default=REPO_ROOT / os.getenv("WILDCARD_PARALLEL_WORK_DIR", "tmp/wildcard_parallel"))
    parser.add_argument("--status-file", type=Path, default=REPO_ROOT / os.getenv("WILDCARD_PARALLEL_STATUS_FILE", "tmp/state/wildcard_parallel_status.json"))
    parser.add_argument("--html-file", type=Path, default=REPO_ROOT / os.getenv("WILDCARD_PARALLEL_HTML_FILE", "tmp/state/wildcard_parallel_overlay.html"))
    parser.add_argument("--result-file", type=Path, default=None)
    parser.add_argument("--cdp-base-port", type=int, default=_int(os.getenv("WILDCARD_PARALLEL_CDP_BASE_PORT"), 19320))
    parser.add_argument("--serve-base-port", type=int, default=_int(os.getenv("WILDCARD_PARALLEL_SERVE_BASE_PORT"), 18080))
    parser.add_argument("--bridge-timeout", type=int, default=_int(os.getenv("WILDCARD_PARALLEL_BRIDGE_TIMEOUT"), 45))
    parser.add_argument("--perturb-timeout", type=int, default=30)
    parser.add_argument("--min-successful-games", type=int, default=_int(os.getenv("WILDCARD_PARALLEL_MIN_SUCCESSFUL_GAMES"), 1))
    parser.add_argument("--cull-after-games", type=int, default=_int(os.getenv("WILDCARD_PARALLEL_CULL_AFTER_GAMES"), 1))
    parser.add_argument("--cull-comp-ratio", type=float, default=_float(os.getenv("WILDCARD_PARALLEL_CULL_COMP_RATIO"), 0.90))
    args = parser.parse_args()

    args.jobs = max(3, args.jobs)
    args.games = max(1, args.games)
    args.cull_after_games = max(0, min(args.cull_after_games, args.games))
    args.cull_comp_ratio = max(0.0, args.cull_comp_ratio)
    session_dir = args.session_root / time.strftime("run-%Y%m%d-%H%M%S")
    session_dir.mkdir(parents=True, exist_ok=True)
    result_file = args.result_file or (session_dir / "result.json")

    payload = {"phase": "generating", "session_dir": str(session_dir), "candidates": []}
    render_overlay(args.status_file, args.html_file, payload)

    candidates = [run_perturb(args, index, session_dir) for index in range(args.jobs)]
    payload["candidates"] = [c.public() for c in candidates]
    render_overlay(args.status_file, args.html_file, payload)
    if not any(c.status == "pending" for c in candidates):
        payload["phase"] = "failed"
        atomic_json(result_file, {"ok": False, "reason": "all_perturb_failed", "candidates": [c.public() for c in candidates]})
        render_overlay(args.status_file, args.html_file, payload)
        print(json.dumps(json.loads(result_file.read_text(encoding="utf-8")), ensure_ascii=False))
        return 2

    evaluated: list[CandidateResult] = []
    payload["phase"] = "running"
    if args.evaluate_mode == "simulate":
        with ThreadPoolExecutor(max_workers=args.jobs) as pool:
            futures = {
                pool.submit(evaluate_simulated, c, args, session_dir): c
                for c in candidates
                if c.status == "pending"
            }
            for future in as_completed(futures):
                evaluated.append(future.result())
                merged = evaluated + [c for c in candidates if c not in evaluated and c.status != "failed"]
                payload["candidates"] = [c.public() for c in merged]
                render_overlay(args.status_file, args.html_file, payload)
        failed = [c for c in candidates if c.status == "failed" and c not in evaluated]
        all_candidates = evaluated + failed
    else:
        coordinator = CullCoordinator(args, args.status_file, args.html_file, session_dir, candidates)
        with ThreadPoolExecutor(max_workers=args.jobs) as pool:
            futures = {
                pool.submit(evaluate_slot, c.index, c, args, session_dir, coordinator): c
                for c in candidates
                if c.status == "pending"
            }
            for future in as_completed(futures):
                evaluated.append(future.result())
                coordinator.snapshot()
        all_candidates = list(coordinator.candidates)
    winner = choose_winner(all_candidates, args.min_successful_games)
    if winner:
        winner.status = "won"
    payload = {
        "phase": "won" if winner else "no_candidate",
        "session_dir": str(session_dir),
        "winner": winner.public() if winner else None,
        "candidates": [c.public() for c in all_candidates],
    }
    render_overlay(args.status_file, args.html_file, payload)
    result = {
        "ok": winner is not None,
        "reason": "winner_selected" if winner else "no_candidate",
        "session_dir": str(session_dir),
        "winner": winner.public() if winner else None,
        "candidates": [c.public() for c in all_candidates],
    }
    atomic_json(result_file, result)
    print(json.dumps(result, ensure_ascii=False))
    return 0 if winner else 2


if __name__ == "__main__":
    raise SystemExit(main())
