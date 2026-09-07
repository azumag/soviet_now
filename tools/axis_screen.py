#!/usr/bin/env python3
"""strategy.py の加点軸を実戦局面で 1 本ずつ無効化し、生きている軸を選り分ける。

issue #132 P0-2 / Phase 3「到達不能・実質 no-op の軸を特定して整理する」用。
700 版以上の数値変異が積み上がった結果、リテラルの大きさと実際の影響力は一致しない。

出力は 3 分類:
  * 発火ゼロ        … その局面帯では条件が成立しない（例: ロシア在盤前提の軸）
  * 発火するが無影響 … ほぼ全候補に等しく効くため argmax を動かせない（定数シフト）
  * 生きている軸    … 無効化すると実際に選ぶ x が変わる

使い方:
    python3 tools/axis_screen.py --corpus <dir of *.jsonl> [--limit 700]
"""
import argparse
import ast
import glob
import importlib.util
import os
import re
import sys

sys.dont_write_bytecode = True

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

AXIS_RE = re.compile(r"^(\s+)score (?:\+|-)= ([0-9]+\.?[0-9]*)(\s*\*\s*[A-Za-z_][A-Za-z0-9_]*)?\s*$")
# 係数が変数の行 (score += bonus, score -= height_penalty ...) も同じ手順で潰す
VAR_RE = re.compile(r"^(\s+)score (\+|-)= (.+?)\s*(#.*)?$")


def _disable(line):
    """その行の寄与だけを 0 にした行を返す。"""
    m = AXIS_RE.match(line)
    if m:
        return m.group(1) + "score += 0.0" + (m.group(3) or "")
    m = VAR_RE.match(line)
    return "%sscore %s= 0.0 * (%s)" % (m.group(1), m.group(2), m.group(3))


def axis_label(lines, idx):
    for j in range(idx, min(idx + 6, len(lines))):
        m = re.search(r'reasons\.append\("([A-Z0-9_]+)"\)', lines[j])
        if m:
            return m.group(1)
    return "line%d" % (idx + 1)


def _load(text, name):
    # 同じパスへ書き続けると、サイズが同じ変異どうしで .pyc が再利用され、
    # 前の変異の結果を引き継いでしまう（実際に踏んだ: 別軸が同じ数値になる）。
    # 変異ごとに別名で書き、bytecode も書かせない。
    path = os.path.join(REPO, "candidates", "_axis_screen_%s.py" % name)
    open(path, "w", encoding="utf-8").write(text)
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod, path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--corpus", required=True)
    ap.add_argument("--limit", type=int, default=700)
    ap.add_argument("--strategy", default="strategy.py")
    args = ap.parse_args()

    from tools import replay_shadow as rs
    os.chdir(REPO)
    for key, val in rs.ANALYZER_DEFAULTS.items():
        os.environ.setdefault(key, val)
    import analyze_board as ab
    import strategy_runner as sr
    sr.log = lambda *a, **k: None
    shapes = rs.load_shapes()
    cache = list(rs.iter_turns(args.corpus, shapes, ab, sr, limit=args.limit))
    if not cache:
        print("no replayable turns")
        return 1
    print("cached %d turns" % len(cache), flush=True)

    src = open(os.path.join(REPO, args.strategy), encoding="utf-8").read()
    lines = src.split("\n")
    def _single_statement(line):
        """行の途中で式が続くもの (末尾が開き括弧など) は対象外にする。"""
        try:
            ast.parse(line.strip())
            return True
        except SyntaxError:
            return False

    targets = [(i, AXIS_RE.match(l) or VAR_RE.match(l)) for i, l in enumerate(lines)
               if (AXIS_RE.match(l) or VAR_RE.match(l))
               and not l.strip().startswith("#") and _single_statement(l)]
    anchor = next(j for j, l in enumerate(lines) if l.startswith("from strategy_helpers"))

    # 1) 発火回数を 1 度の計装で数える
    hit_lines = list(lines)
    for n, (i, m) in enumerate(targets):
        hit_lines[i] = lines[i] + "\n" + m.group(1) + "_AXIS_HITS[%d] = _AXIS_HITS[%d] + 1" % (n, n)
    hit_lines.insert(anchor + 1, "_AXIS_HITS = [0]*%d\n" % len(targets))
    mod, tmp = _load("\n".join(hit_lines), "axis_hits")
    for _, gs, an in cache:
        try:
            rs.run_chain(mod, gs, an, sr)
        except Exception:
            pass
    hits = list(mod._AXIS_HITS)

    # 2) 1 本ずつ無効化して決定差分を測る
    root, _ = _load(src, "axis_root")
    base = [float(rs.run_chain(root, gs, an, sr)["x"]) for _, gs, an in cache]
    rows = []
    for n, (i, m) in enumerate(targets):
        mut = list(lines)
        mut[i] = _disable(lines[i])
        try:
            cand, _ = _load("\n".join(mut), "axis_%d" % n)
        except Exception:
            continue
        changed = 0
        for k, (_, gs, an) in enumerate(cache):
            try:
                x = float(rs.run_chain(cand, gs, an, sr)["x"])
            except Exception:
                x = base[k]
            if abs(x - base[k]) > 1e-9:
                changed += 1
        lit = m.group(2) if AXIS_RE.match(lines[i]) else lines[i].split("= ", 1)[1].strip()[:28]
        rows.append((axis_label(lines, i), lit, i + 1, hits[n], 100.0 * changed / len(cache)))
    for stale in glob.glob(os.path.join(REPO, "candidates", "_axis_screen_*.py")):
        os.remove(stale)

    rows.sort(key=lambda r: (-r[4], -r[3]))
    print("\n%-38s %8s %6s %9s %9s" % ("axis", "literal", "line", "発火", "決定変化"))
    for name, lit, ln, hit, pct in rows:
        print("%-38s %8s %6d %9d %8.2f%%" % (name[:38], lit, ln, hit, pct))
    live = [r for r in rows if r[4] > 0]
    dead = [r for r in rows if r[3] == 0]
    sat = [r for r in rows if r[3] > 0 and r[4] == 0.0]
    print("\n生きている軸 %d / 発火ゼロ %d / 発火するが無影響 %d (計 %d)"
          % (len(live), len(dead), len(sat), len(rows)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
