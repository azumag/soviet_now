#!/usr/bin/env python3
"""任意の root strategy.py へ v760 SURFACE_LEVEL を適用する。
使い方: python3 apply_v760.py <base strategy.py> <out.py>
"""
import ast, sys

BLOCK = '''

# --- v760: 盤面の天井を押し上げる手を罰する（実際の死因に直接効く軸） ---
# 実測 (2026-08-30):
#   * 終了理由が記録された 122 試合のうち 121 件 (99.2%) が deadline_crossed。
#     盤面を殺すのは総面積ではなく「どこか 1 列が y=3.38 に届くこと」。
#   * 終局盤面の列別天端 (実戦 75 試合、x 四捨五入 7 列の中央値):
#       x=-3:1.88  -2:3.41  -1:2.59  0:2.97  +1:2.66  +2:2.94  +3:2.20
#     中央がデッドラインに達して終わる一方、両端は 1.2〜1.5 の余白を残している。
#
# 設計上の注意 (一度失敗した形): 「表面中央値より高い着地を罰する」形にすると、中央値は
# その手の中では定数であり、next_type も固定なので top = landing_y + r_next の定数差しかない。
# つまり既存の height_penalty (landing_y * 19.20 * height_mult) と順位付けが等価で、
# 実質「高さ罰の係数を上げただけ」になる。実測でも着地天端の中央値超過は +1.502 -> +1.396 と
# ほとんど動かず、98.6% の手が中央値より上という状態は変わらなかった。
#
# そこで罰する対象を「盤面の天井 (最大の列天端) をどれだけ押し上げるか」にする。
# 現在の天井より下に着地する候補はすべて 0 で区別されず、天井を上げる候補だけが罰される。
# これは絶対高さとは順位付けが異なり、実際の死因そのものを直接見ている。
# V760_CEILING_RAISE_W=0 (既定) で従来と完全一致する。
_V760_FLOOR_Y = -4.48            # 空の列の表面は床
_V760_BASE_PENALTY = 300.0       # 天井を 1 押し上げるごとの罰 (W=1 のとき)


def _v760_weight():
    """V760_CEILING_RAISE_W: 0=無効。1 で「天井を +1 上げる着地」に -300。"""
    raw = str(os.environ.get("V760_CEILING_RAISE_W", "0") or "").strip()
    if raw in ("", "0", "false", "no", "off"):
        return 0.0
    try:
        w = float(raw)
    except (TypeError, ValueError):
        return 0.0
    if w != w or w < 0.0:
        return 0.0
    return min(w, 8.0)


def _v760_ceiling(pieces):
    """現在の盤面の天井 (列別天端の最大値) を返す。駒が無ければ床。"""
    ceiling = _V760_FLOOR_Y
    for piece in pieces or []:
        try:
            top = float(piece.get("y", 0.0)) + float(board_stats.seed_top_radius(int(piece.get("type", 1))))
        except (TypeError, ValueError):
            continue
        if top > ceiling:
            ceiling = top
    return ceiling
'''

EDITS = [
    ("\nimport math\n", "\nimport math\nimport os\n", "import os"),
    ("from strategy_helpers import board_stats\n",
     "from strategy_helpers import board_stats\n" + BLOCK, "helper block"),
    # 候補ループの直前に 1 度だけ表面を測る
    ('    next_type = next_piece.get("type", 2)\n',
     '    next_type = next_piece.get("type", 2)\n'
     "    _v760_w = _v760_weight()\n"
     "    _v760_ceiling_y = _v760_ceiling(pieces) if _v760_w > 0.0 else 0.0\n",
     "ceiling precompute"),
    # balance 軸のすぐ後ろ、同じ「水平配置」の文脈に置く
    ("        score -= abs(balance_penalty)\n",
     "        score -= abs(balance_penalty)\n"
     "\n"
     "        if _v760_w > 0.0:\n"
     "            _v760_top = result.get(\"top_y_after_drop\")\n"
     "            if _v760_top is not None:\n"
     "                try:\n"
     "                    _v760_excess = float(_v760_top) - _v760_ceiling_y\n"
     "                except (TypeError, ValueError):\n"
     "                    _v760_excess = 0.0\n"
     "                if _v760_excess > 0.0:\n"
     "                    score -= _v760_w * _V760_BASE_PENALTY * _v760_excess\n"
     "                    reasons.append(\"CEILING_RAISE\")\n",
     "levelness penalty"),
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
