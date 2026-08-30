#!/usr/bin/env python3
"""盤面容量モデル (issue #132 P1)。

ソ連建国は「盤面が満杯になるまでに、供給された価値をどれだけ高 tier へ濃縮できたか」で決まる。
このモジュールはその過程を第一原理で表す。

観測（2026-08-30、実戦 75 試合 / 7041 手、v752 30 / v757 31 / 旧 14）:

  * 駒は盤面から消えない。併合は 2 個を 1 個に置き換え、価値 2^(t-11) を保存したまま
    面積を約 0.59〜0.78 倍に圧縮する。したがって価値は保存量、面積は消費量である。
  * 落下型は 1..11 の一様分布。1 手あたり平均 面積 1.0656 / 価値 0.1817 T11 相当を供給する。
  * ゲームオーバーは駒総面積 49.3 で起きる（p10 44.6 / p90 52.3、sd 2.9）。
  * 終局の盤面価値 15.93 T11 相当 ＝ 供給 16.35 の 97.4%（＝価値保存の実測確認）。

この 3 点だけから、残留駒 (residue) の分布を与えれば持続可能な手数が決まる。
モデルは実測を area 49.2 (対 49.3) / merges 0.56 手 (対 0.564) / 終局駒数 41.1 (対 41) で再現する。
"""
import math

TYPE_RADII = {
    1: 0.207, 2: 0.259, 3: 0.316, 4: 0.380, 5: 0.414, 6: 0.470, 7: 0.559,
    8: 0.660, 9: 0.746, 10: 0.846, 11: 0.982, 12: 1.068, 13: 1.207,
    14: 1.385, 15: 1.600,
}
AREA = {t: math.pi * r * r for t, r in TYPE_RADII.items()}
VALUE = {t: 2.0 ** (t - 11) for t in TYPE_RADII}
DROP_TYPES = tuple(range(1, 12))          # 12 以上は直接落ちてこない
BOARD_AREA_CAPACITY = 49.3                # 実測 (75 試合)
MEAN_DROP_AREA = sum(AREA[t] for t in DROP_TYPES) / len(DROP_TYPES)
MEAN_DROP_VALUE = sum(VALUE[t] for t in DROP_TYPES) / len(DROP_TYPES)

# 併合 1 回が解放する面積。t=1 の 0.058 に対し t=11 は 2.476 と 42 倍の開きがある。
AREA_RELIEF = {t: 2 * AREA[t] - AREA[t + 1] for t in range(1, 15)}

# 実測の残留プロファイル (1 試合あたりの終局残留駒数、v752/v757 混在 75 試合)
OBSERVED_RESIDUE = {
    1: 3.44, 2: 3.67, 3: 4.52, 4: 3.91, 5: 3.88, 6: 3.69, 7: 3.49, 8: 3.21,
    9: 3.04, 10: 2.84, 11: 1.87, 12: 2.04, 13: 1.15, 14: 0.33, 15: 0.027,
}
OBSERVED_TURNS = 94.0


def cascade(turns, residue):
    """落下と残留から併合カスケードを解く。

    created[t] = drops[t] + merges[t-1] であり、residue を差し引いた残りが 2 個ずつ併合される。
    戻り値: (面積, 終局駒数 per tier, 併合数 per tier)。merges[15] が建国数。
    """
    drops = turns / len(DROP_TYPES)
    merges = {}
    end = {}
    carried = 0.0
    for t in range(1, 16):
        created = (drops if t in DROP_TYPES else 0.0) + carried
        left = min(max(residue.get(t, 0.0), 0.0), created)
        merges[t] = max(0.0, (created - left) / 2.0)
        end[t] = created - 2 * merges[t]
        carried = merges[t]
    end[16] = carried
    area = sum(end[t] * AREA[t] for t in end if t in AREA)
    return area, end, merges


def scale_residue(residue, turns, base_turns=OBSERVED_TURNS):
    """tier 11 以下の残留は手数に比例して増える（供給が線形なので）。12 以上は絶対数で扱う。"""
    return {t: v * (turns / base_turns if t in DROP_TYPES else 1.0) for t, v in residue.items()}


def sustainable_turns(residue, capacity=BOARD_AREA_CAPACITY, lo=20.0, hi=800.0):
    """その残留プロファイルで盤面が満杯になるまでの手数。"""
    for _ in range(60):
        mid = (lo + hi) / 2.0
        area, _, _ = cascade(mid, scale_residue(residue, mid))
        if area < capacity:
            lo = mid
        else:
            hi = mid
    return lo


def residue_leverage(residue=None, capacity=BOARD_AREA_CAPACITY):
    """残留駒を 1 個減らすと何手延びるか。tier ごとの改善価値。"""
    residue = dict(residue or OBSERVED_RESIDUE)
    base = sustainable_turns(residue, capacity)
    out = {}
    for t in sorted(residue):
        if residue[t] <= 0:
            continue
        cut = dict(residue)
        cut[t] = residue[t] * 0.5
        gained = sustainable_turns(cut, capacity) - base
        removed = residue[t] * 0.5 * (base / OBSERVED_TURNS if t in DROP_TYPES else 1.0)
        out[t] = gained / removed if removed > 1e-9 else 0.0
    return base, out


if __name__ == "__main__":
    area, end, merges = cascade(OBSERVED_TURNS, OBSERVED_RESIDUE)
    print("観測再現: area %.1f / merges %.3f per turn / end pieces %.1f"
          % (area, sum(merges.values()) / OBSERVED_TURNS, sum(end[t] for t in range(1, 16))))
    base, lev = residue_leverage()
    print("持続手数 %.0f" % base)
    print("残留 1 個あたりの損失手数:", {t: round(v, 1) for t, v in lev.items()})
    print("併合 1 回の解放面積:", {t: round(v, 3) for t, v in AREA_RELIEF.items()})
    for target in (120, 176, 250):
        lo, hi = 0.0, 1.0
        for _ in range(40):
            mid = (lo + hi) / 2.0
            if sustainable_turns({t: v * mid for t, v in OBSERVED_RESIDUE.items()}) < target:
                hi = mid
            else:
                lo = mid
        _, end_t, m_t = cascade(target, scale_residue({t: v * lo for t, v in OBSERVED_RESIDUE.items()}, target))
        print("T=%d には残留 x%.2f が必要 / merges %.3f per turn / 建国 %.2f 回per game"
              % (target, lo, sum(m_t.values()) / target, end_t[16]))
