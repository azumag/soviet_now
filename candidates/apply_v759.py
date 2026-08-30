#!/usr/bin/env python3
"""任意の root strategy.py へ v759 TIER_WEIGHTED_COVER_AVOID を適用する。

v757 が採用されたら root が変わるので、その新 root へ同じ改変を当て直すために使う。
使い方: python3 apply_v759.py <base strategy.py> <out.py>
"""
import ast, sys

BLOCK = '''

# --- v759: 高 type 被覆抑止を「その駒を埋めると何手失うか」で段階的に重み付けする ---
# 実測 (2026-08-30、実戦 75 試合 / 7041 手、tools/board_capacity.py):
#   盤面は駒総面積 49.3 で死ぬ。併合されずに終局まで残る駒 1 個あたりの寿命損失は
#   T8 4.0 / T9 5.2 / T10 6.8 / T11 9.1 / T12 9.8 / T13 12.6 / T14 16.5 手。
# 現行の HIGH_TYPE_COVER_AVOID は type>=10 を一律 -400 で扱うが、T14 を埋める損害は
# T10 の 2.4 倍ある。ここでは既存の発火条件・帯域には触れず、罰の大きさだけを段階化する。
#
# 軸感度の実測 (tools/axis_screen.py、再現率 80% の replay harness、実戦 700 手):
#   * 加点軸 37 本のうち決定を変えられるのは 8 本のみ。上位 5 本はすべて被覆回避。
#   * DIRECT 併合ボーナス 1566.9 -> 0 で変化 1.8%、x10 で 0.0%（飽和・実質不感）
#   * HIGH_TYPE_COVER_AVOID -400 -> 0 で変化 16.3%（最も生きている軸）
# したがって tier 重み付けは併合ボーナスではなくこの被覆抑止に置く。
# V759_COVER_TIER_W=0 (既定) で従来と完全一致する。
_V759_COVER_TURN_LOSS = {8: 4.0, 9: 5.2, 10: 6.8, 11: 9.1, 12: 9.8, 13: 12.6, 14: 16.5, 15: 22.1}
_V759_COVER_BASE_TYPE = 10  # 現行の -400 が対応する tier


def _v759_cover_weight():
    """V759_COVER_TIER_W: 0=従来の一律, 1=寿命損失に完全比例。中間は線形ブレンド。"""
    raw = str(os.environ.get("V759_COVER_TIER_W", "0") or "").strip()
    if raw in ("", "0", "false", "no", "off"):
        return 0.0
    try:
        w = float(raw)
    except (TypeError, ValueError):
        return 0.0
    if w != w or w < 0.0:
        return 0.0
    return min(w, 1.0)


def _v759_cover_mult(piece_type, weight):
    """埋める駒の tier から被覆罰の倍率を作る。weight=0 なら 1.0。"""
    if weight <= 0.0:
        return 1.0
    base = _V759_COVER_TURN_LOSS[_V759_COVER_BASE_TYPE]
    try:
        loss = _V759_COVER_TURN_LOSS[int(piece_type)]
    except (TypeError, ValueError, KeyError):
        return 1.0
    return (1.0 - weight) + weight * (loss / base)
'''

EDITS = [
    ("\nimport math\n", "\nimport math\nimport os\n", "import os"),
    ("from strategy_helpers import board_stats\n",
     "from strategy_helpers import board_stats\n" + BLOCK, "helper block"),
    ("            high_cover_free.append((_hc_x, _hc_r * 0.9, _hc_top - 0.25))\n",
     "            high_cover_free.append((_hc_x, _hc_r * 0.9, _hc_top - 0.25, _hc_t))\n", "tuple arity"),
    ("            for _hc_x, _hc_tol, _hc_min_y in high_cover_free:\n",
     "            _hc_w = _v759_cover_weight()\n"
     "            for _hc_x, _hc_tol, _hc_min_y, _hc_type in high_cover_free:\n", "loop unpack"),
    ("                    score -= 400.0\n",
     "                    score -= 400.0 * _v759_cover_mult(_hc_type, _hc_w)\n", "penalty"),
]


def main():
    base, out = sys.argv[1], sys.argv[2]
    src = open(base, encoding="utf-8").read()
    if "import os" in src.split("from strategy_helpers")[0]:
        EDITS[0] = (EDITS[0][0], EDITS[0][0], "import os (already present)")
    for old, new, label in EDITS:
        n = src.count(old)
        if n != 1:
            raise SystemExit("anchor %r found %d times (expected 1) for %s" % (old[:48], n, label))
        src = src.replace(old, new, 1)
    ast.parse(src)
    open(out, "w", encoding="utf-8").write(src)
    print("wrote", out)


if __name__ == "__main__":
    main()
