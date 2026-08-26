#!/usr/bin/env python3
"""ローカル（Mac 等）でのヘッドレス並列自己対戦 A/B。root(A) と候補(B) を各スロットで ABBA 交互に実プレイし、
tools/ab_report.py / tools/ab_decide.py が読める ab_games.jsonl を出力する。配信・VM には触れない。

wildcard_parallel.py の実行部 (prepare_candidate_dir / launch_bridge_with_chrome_lock / reset_bridge_for_next_game /
parse_runner_result / eval_score) を再利用する。必要物: sorengame/build、node_modules (playwright)、soviet_local.mjs、
strategy_runner.py、analyze_board.py、strategy_helpers/、lib/。

使い方 (リポジトリルート or 自己対戦ルートで):
  python3 tools/selfplay_ab.py --a strategy.py --b cand.py --slots 4 --games 8 --out tmp/selfplay/run1
  → 各スロットが ABBA… で 8 試合 (計 32 試合、腕あたり 16)。終了後 out/ab_games.jsonl と report。
本番設定は既定で SOREN_SETTLE_REQUIRED=3 / ANALYZE_BOARD_VERTICAL_LANE_DIRECT=1 / MERGE_TOP_MODEL=2 / WALL_CLAMP=1。
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)
import wildcard_parallel as wp  # noqa: E402

LOCK = threading.Lock()


def log(msg):
    print("[%s] %s" % (time.strftime("%H:%M:%S"), msg), flush=True)


def game_metrics(archive):
    rs = []
    try:
        with open(archive, encoding="utf-8") as fh:
            for line in fh:
                try:
                    rs.append(json.loads(line))
                except Exception:
                    pass
    except Exception:
        return {}
    if not rs:
        return {}
    merges = multi = 0
    for i in range(1, len(rs)):
        m = 1 - ((rs[i].get("piece_count") or 0) - (rs[i - 1].get("piece_count") or 0))
        if m >= 1:
            merges += m
        if m >= 2:
            multi += 1
    mx = 0
    for r in rs:
        for p in (r.get("state_snapshot") or {}).get("pieces") or []:
            mx = max(mx, p.get("type", 0) or 0)
    pc = {r.get("turn"): r.get("piece_count") for r in rs}
    return {"turns": len(rs), "merges_per_turn": round(merges / max(1, len(rs)), 4), "multi_merge_turns": multi,
            "pieces_at_20": pc.get(20), "pieces_at_40": pc.get(40), "max_type": mx, "t14": int(mx >= 14), "t15": int(mx >= 15),
            "crossings": sum(1 for r in rs if r.get("decision_crosses_deadline")), "history_hash": rs[-1].get("strategy_hash"),
            "settle_required": (rs[-1].get("settle") or {}).get("required"), "analyzer_modes": rs[-1].get("analyzer_modes")}


def slot_worker(slot, args, session, arms, hashes, env_base, results):
    workdir = wp.prepare_candidate_dir(session, "slot-%d" % slot, Path(arms["A"]), preserve_exact=True)
    cdp_port = args.cdp_base_port + slot
    serve_port = args.serve_base_port + slot
    profile_dir = str((workdir / "tmp" / "chromium_profile").resolve())
    env = dict(env_base)
    env.update({
        "HOME": str((workdir / "tmp" / "chrome_home").resolve()),
        "SOREN_CDP_PORT": str(cdp_port),
        "SOREN_SERVE_PORT": str(serve_port),
        "SOREN_LOCAL_USER_DATA_DIR": profile_dir,
        "SOREN_CHROME_HOME": str((workdir / "tmp" / "chrome_home").resolve()),
        "XDG_CONFIG_HOME": str((workdir / "tmp" / "config").resolve()),
        "XDG_CACHE_HOME": str((workdir / "tmp" / "cache").resolve()),
    })
    (workdir / "tmp" / "chrome_home").mkdir(parents=True, exist_ok=True)
    bridge = None
    try:
        wp.cleanup_wildcard_server_ports([cdp_port, serve_port])
        (workdir / "commands.txt").write_text("", encoding="utf-8")
        (workdir / "game_state.json").write_text("{}", encoding="utf-8")
        bridge = wp.launch_bridge_with_chrome_lock(workdir, env, args.bridge_timeout)
        log("slot %d bridge up (cdp %d serve %d)" % (slot, cdp_port, serve_port))
        pattern = args.pattern
        for g in range(args.games):
            arm = pattern[(slot + g) % len(pattern)] if args.stagger else pattern[g % len(pattern)]
            shutil.copy2(arms[arm], workdir / "strategy.py")
            (workdir / "commands.txt").write_text("", encoding="utf-8")
            try:
                (workdir / "game_history" / "latest.jsonl").write_text("", encoding="utf-8")
            except Exception:
                pass
            if g > 0:
                wp.reset_bridge_for_next_game(workdir, bridge, args.bridge_timeout)
            t0 = time.time()
            proc = subprocess.Popen([sys.executable, "strategy_runner.py"], cwd=workdir, env=env, text=True,
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            try:
                stdout, stderr = proc.communicate(timeout=args.game_timeout)
            except subprocess.TimeoutExpired:
                proc.kill()
                stdout, stderr = proc.communicate(timeout=10)
                log("slot %d game %d arm %s TIMEOUT" % (slot, g, arm))
                stdout = ""
            dt = time.time() - t0
            game = {}
            err = ""
            try:
                game = wp.parse_runner_result(stdout)
            except Exception as e:
                err = "no result: %s | %s" % (e, (stderr or "")[-300:])
            rec = {"slot": slot, "game": g, "arm": arm, "hash": hashes[arm], "score": game.get("score"), "eval": wp.eval_score(game) if game else None,
                   "turns": game.get("turns"), "error": (game.get("error") or err or "")[:200], "elapsed_sec": round(dt, 1), "ts": time.strftime("%Y-%m-%dT%H:%M:%S")}
            latest = workdir / "game_history" / "latest.jsonl"
            if latest.exists() and latest.stat().st_size > 0:
                dst = session / "games" / ("slot%d_g%02d_%s_score%s.jsonl" % (slot, g, arm, game.get("score", "NA")))
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(latest, dst)
                rec.update(game_metrics(dst))
                rec["archive"] = dst.name
            rec["tainted"] = bool(rec.get("history_hash") and rec["history_hash"] != hashes[arm]) or bool(rec["error"])
            with LOCK:
                results.append(rec)
                rec["order"] = len(results) - 1
                rec["idx"] = slot * args.games + g  # スロット内で連番 → ab_report のブロックがスロット内 ABBA になる
                with open(session / "ab_games.jsonl", "a", encoding="utf-8") as fh:
                    fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
            log("slot %d game %d arm %s score %s eval %s turns %s merges/turn %s %.0fs%s" % (slot, g, arm, rec.get("score"), rec.get("eval"), rec.get("turns"), rec.get("merges_per_turn"), dt, (" ERR " + rec["error"]) if rec["error"] else ""))
            if rec["error"] and "decide_exception" in rec["error"]:
                log("slot %d: decide_exception on arm %s → stop slot" % (slot, arm))
                break
    except Exception as e:
        log("slot %d fatal: %s" % (slot, e))
    finally:
        wp.stop_process(bridge)
        wp.cleanup_chrome_profile_processes(profile_dir, cdp_port)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--a", required=True)
    ap.add_argument("--b", required=True)
    ap.add_argument("--slots", type=int, default=4)
    ap.add_argument("--games", type=int, default=8, help="games per slot")
    ap.add_argument("--pattern", default="ABBA")
    ap.add_argument("--stagger", action="store_true", help="slot ごとに pattern の開始位置をずらす")
    ap.add_argument("--out", default="tmp/selfplay/run-%s" % time.strftime("%Y%m%d-%H%M%S"))
    ap.add_argument("--cdp-base-port", type=int, default=19420)
    ap.add_argument("--serve-base-port", type=int, default=18180)
    ap.add_argument("--bridge-timeout", type=int, default=90)
    ap.add_argument("--game-timeout", type=int, default=1200)
    ap.add_argument("--settle-required", default="3")
    ap.add_argument("--wall-clamp", default="1")
    ap.add_argument("--vertical-lane", default="1")
    ap.add_argument("--merge-top", default="2")
    ap.add_argument("--headless", default="1")
    args = ap.parse_args()
    session = Path(args.out).resolve()
    session.mkdir(parents=True, exist_ok=True)
    arms = {"A": str(Path(args.a).resolve()), "B": str(Path(args.b).resolve())}
    hashes = {k: wp.compute_strategy_hash(Path(v)) for k, v in arms.items()}
    pw_path = os.environ.get("PLAYWRIGHT_BROWSERS_PATH", str(Path.home() / "Library" / "Caches" / "ms-playwright"))
    chrome_exec = ""
    try:
        chrome_exec = wp.resolve_playwright_chrome_for_testing(pw_path)
    except Exception:
        pass
    env_base = os.environ.copy()
    for k in ("OBS_WEBSOCKET_PORT", "OBS_WEBSOCKET_PASSWORD"):
        env_base.pop(k, None)
    env_base.update({
        "PATH": ":".join(p for p in ["/opt/homebrew/bin", "/usr/local/bin", "/usr/bin", "/bin", env_base.get("PATH", "")] if p),
        "SOREN_CHROME_HEADLESS": args.headless,
        "SOREN_CHROME_FORCE_PLAYWRIGHT_LAUNCH": "1",
        "SOREN_CHROME_NO_FOCUS_LAUNCH": "1",
        "PLAYWRIGHT_BROWSERS_PATH": pw_path,
        "RUSSIA_CELEBRATION_ENABLED": "0",
        "SOREN_BRIDGE_DESYNC_LIMIT": "3",
        "SOREN_BGM_VOLUME": "0",
        "SOREN_SE_VOLUME": "0",
        "SOREN_SETTLE_REQUIRED": args.settle_required,
        "ANALYZE_BOARD_WALL_CLAMP": args.wall_clamp,
        "ANALYZE_BOARD_VERTICAL_LANE_DIRECT": args.vertical_lane,
        "ANALYZE_BOARD_MERGE_TOP_MODEL": args.merge_top,
    })
    if chrome_exec:
        env_base["SOREN_CHROME_EXECUTABLE_PATH"] = chrome_exec
    json.dump({"a_hash": hashes["A"], "b_hash": hashes["B"], "pattern": args.pattern, "slots": args.slots, "games_per_slot": args.games,
               "started_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "a_file": arms["A"], "b_file": arms["B"], "env": {k: env_base[k] for k in ("SOREN_SETTLE_REQUIRED", "ANALYZE_BOARD_WALL_CLAMP", "ANALYZE_BOARD_VERTICAL_LANE_DIRECT", "ANALYZE_BOARD_MERGE_TOP_MODEL")}},
              open(session / "ab_state.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    log("session %s A=%s B=%s slots=%d games/slot=%d pattern=%s" % (session, hashes["A"][:12], hashes["B"][:12], args.slots, args.games, args.pattern))
    results = []
    threads = []
    for s in range(args.slots):
        t = threading.Thread(target=slot_worker, args=(s, args, session, arms, hashes, env_base, results), daemon=True)
        t.start()
        threads.append(t)
        time.sleep(3)
    for t in threads:
        t.join()
    ok = [r for r in results if not r.get("error")]
    log("done: %d games (%d ok). report:" % (len(results), len(ok)))
    subprocess.run([sys.executable, str(HERE / "ab_report.py"), "--games", str(session / "ab_games.jsonl"), "--state", str(session / "ab_state.json"), "--history", str(session / "games")])
    return 0


if __name__ == "__main__":
    sys.exit(main())
