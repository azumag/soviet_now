#!/usr/bin/env python3
"""実戦 archive を再生して候補と root の決定差分を測る (issue #132 Phase 1: shadow 差分)。

runner と同じ決定連鎖を再現する。decide() だけを呼ぶと記録と一致しない:

    decide() のみ            -> 記録された手と exact 18.9%
    + enforce_deadline_safety
      + apply_strategy_final_decision
      + game_state の deadline_crossed / deadline_y を与える  -> exact 80.0% / ±0.2 以内 93.4%

`strategy.py` は `game_state.get("deadline_crossed", True)` と既定 True で読むため、この
キーを与えないと毎手デッドライン超過扱いになり NO_MERGE_DEADLINE_GUARD_NO_VALID へ落ちる。
再現率を測らずに差分だけ見ると、壊れた harness で「差が無い」と誤結論する。

使い方:
    python3 tools/replay_shadow.py --corpus <dir of *.jsonl> [--cand candidates/x.py]
                                   [--limit 3000] [--verify] [--hash a557db55896b]
"""
import argparse
import collections
import glob
import importlib.util
import json
import os
import statistics
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

ANALYZER_DEFAULTS = {
    "ANALYZE_BOARD_VERTICAL_LANE_DIRECT": "1",
    "ANALYZE_BOARD_MERGE_TOP_MODEL": "2",
    "ANALYZE_BOARD_WALL_CLAMP": "1",
    "ANALYZE_BOARD_LANDING_ARC": "0",
}


def load_module(path, name):
    spec = importlib.util.spec_from_file_location(name, os.path.join(REPO, path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def load_shapes():
    shapes = {}
    for path in sorted(glob.glob(os.path.join(REPO, "tests/fixtures/*.json"))):
        try:
            data = json.load(open(path))
        except Exception:
            continue
        if isinstance(data.get("shapes"), dict):
            for key, val in data["shapes"].items():
                shapes.setdefault(key, val)
    return shapes


def iter_turns(corpus, shapes, ab, sr, hash_filter=None, limit=100000):
    """corpus 直下と、その 1 階層下のディレクトリの両方から *.jsonl を読む。

    直下だけを見ていると、アーカイブをサブディレクトリに分けた構成で黙って 0 手を返す
    (2026-09-04 に scratchpad 側の harness で実際に発生し、n=0 の測定を正常な結果として
    報告しかけた)。呼び出し側で必ず件数を確認すること。
    """
    seen = 0
    paths = sorted(glob.glob(os.path.join(corpus, "*.jsonl")))
    if not paths:
        paths = sorted(glob.glob(os.path.join(corpus, "*", "*.jsonl")))
    for path in paths:
        if "latest" in path:
            continue
        try:
            rows = [json.loads(l) for l in open(path) if l.strip()]
        except Exception:
            continue
        for row in rows:
            ntype = row.get("next_type")
            pieces = (row.get("state_snapshot") or {}).get("pieces") or []
            if not ntype or not pieces:
                continue
            if hash_filter and row.get("strategy_hash") != hash_filter:
                continue
            types = {str(p["type"]) for p in pieces} | {str(ntype)}
            nn = row.get("next_next_type") or 5
            gs = {
                "state": "MOVE",
                "score": row.get("score", 0),
                "pieceCount": row.get("piece_count", len(pieces)),
                "piece_count": row.get("piece_count", len(pieces)),
                "makeSorenCount": 0,
                "record": 0,
                # 既定 True で読まれるため必ず与える (これが無いと再現率 18.9% まで落ちる)
                "deadline_crossed": bool(row.get("deadline_crossed")),
                "deadline_y": row.get("deadline_y", 3.32),
                "pieces": [dict(p) for p in pieces],
                "shapes": {k: v for k, v in shapes.items() if k in types},
                "next": {"type": ntype, "r": ab.TYPE_RADII.get(ntype, 0.5), "x": 0},
                "nextNext": {"type": nn, "r": ab.TYPE_RADII.get(nn, 0.5)},
            }
            try:
                analysis = sr.build_analysis(gs)
            except Exception:
                continue
            if not analysis.get("results"):
                continue
            yield row, gs, analysis
            seen += 1
            if seen >= limit:
                return


def run_chain(mod, gs, analysis, sr):
    """runner と同じ順序: decide -> clamp -> enforce_deadline_safety -> finalize -> clamp。"""
    decision = mod.decide(gs, analysis)
    if not isinstance(decision, dict) or "x" not in decision:
        decision = {"x": 0.0, "reason": "invalid decide() return"}
    decision["x"] = max(sr.GAME_X_MIN, min(sr.GAME_X_MAX, float(decision["x"])))
    decision = sr.enforce_deadline_safety(decision, analysis, gs, mod)
    decision = sr.apply_strategy_final_decision(mod, decision, analysis, gs)
    decision["x"] = max(sr.GAME_X_MIN, min(sr.GAME_X_MAX, float(decision["x"])))
    return decision


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--corpus", required=True, help="実戦 archive (*.jsonl) のディレクトリ")
    ap.add_argument("--cand", help="比較する候補 (省略時は再現率の検証のみ)")
    ap.add_argument("--root", default="strategy.py")
    ap.add_argument("--limit", type=int, default=3000)
    ap.add_argument("--hash", dest="hash_filter", help="この strategy_hash の手だけを使う")
    ap.add_argument("--verify", action="store_true", help="記録された decision_x との再現率を出す")
    args = ap.parse_args()

    os.chdir(REPO)
    for key, val in ANALYZER_DEFAULTS.items():
        os.environ.setdefault(key, val)
    import analyze_board as ab
    import strategy_runner as sr
    sr.log = lambda *a, **k: None

    shapes = load_shapes()
    root = load_module(args.root, "shadow_root")
    cand = load_module(args.cand, "shadow_cand") if args.cand else None

    n = changed = 0
    exact = near = 0
    diffs = []
    by_type = collections.Counter()
    tot_type = collections.Counter()
    for row, gs, analysis in iter_turns(args.corpus, shapes, ab, sr,
                                        hash_filter=args.hash_filter, limit=args.limit):
        n += 1
        ntype = row.get("next_type")
        tot_type[ntype] += 1
        xa = float(run_chain(root, gs, analysis, sr)["x"])
        if args.verify and row.get("decision_x") is not None:
            d = abs(xa - float(row["decision_x"]))
            exact += d < 1e-6
            near += d <= 0.21
        if cand is not None:
            xb = float(run_chain(cand, gs, analysis, sr)["x"])
            if abs(xa - xb) > 1e-9:
                changed += 1
                by_type[ntype] += 1
                diffs.append(abs(xa - xb))
    if not n:
        print("no replayable turns —— corpus のパスと中身を確認すること "
              "(空の corpus を『差分なし』と読み違えない)")
        return 1
    if args.verify:
        print("再現率: n=%d exact %.1f%%  ±0.2 以内 %.1f%%" % (n, 100.0 * exact / n, 100.0 * near / n))
    if cand is not None:
        print("差分: %d/%d (%.2f%%)" % (changed, n, 100.0 * changed / n))
        if diffs:
            print("  next_type 別:", {t: "%.0f%%" % (100.0 * by_type[t] / tot_type[t])
                                      for t in sorted(tot_type) if tot_type[t]})
            print("  |dx| med %.2f max %.2f" % (statistics.median(diffs), max(diffs)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
