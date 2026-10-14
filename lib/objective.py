"""issue #132 Phase 1: ソ連建国を最優先する単一の versioned evaluator。

なぜこの module があるか
------------------------
終局盤面の評価 (`EVAL_SCORE`) は 2026-08-30 時点で 3 箇所に同じ表を持っていました。

    eloop.sh                          (インライン python の TB)
    wildcard_parallel.py              (TYPE_BONUS)
    repair_current_run_from_history.sh (tb)

3 つが一致していることは tests/test_soviet_terminal_utility.py で 2 件だけ
固定されており、repair 側は無検査でした。表は「T15 を 2 個持ったまま終局
(2×12096=24192) より、実際に建国した局面 (raw +136、soviet ボーナス +800)
のほうが 23,256 点低く評価される」という逆転を 2026-09 に修正した経緯があり、
どれか 1 件だけが古いまま残ると、試合中の評価と修復 seed の評価が違う目的空間
の値として同じ標本に混ざります。issue #132 Phase 1 の「重複した評価表を共通の
versioned evaluator に集約し」「旧 EVAL_SCORE と新目的値を同じ標本へ混ぜない」
をこの 1 本で満たします。

使い方
------
    from lib.objective import eval_score, OBJECTIVE_SCHEMA_VERSION

評価値は 2026-09 修正後と同じ数値です (配置挙動は変わりません)。
version を record に付けるには `stamp()` を使います。version が違う record を
1 つの標本にまとめようとしている場合は `mixed_versions()` が検出します。

stdlib のみ (VM に numpy/scipy は無い)。
"""

from __future__ import annotations

# 目的の「意味」が変わったときだけ bump する (数値の微調整では bump しない)。
# 違う version の score は同じ測定ではないので、1 つの標本にまとめてはいけません。
# 過去履歴には version が付いていないため、`version_of()` は None を返します。
OBJECTIVE_SCHEMA_VERSION = "soviet-terminal-v1"

# 旧 `EVAL_SCORE` / `TYPE_BONUS` / `tb` の値そのまま。隣接比 2.1 の等比。
# type 16 (ソ連) が無いと「T15×2 を持ったまま終局」が「実際の建国」より
# 高く評価され、自動探索が最終目標を回避する向きに働く (issue #132 P0-0)。
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
    # 12096 × 2.1 = 25402
    16: 25402,
}

SOVIET_CREATED_BONUS = 800


def _as_int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def terminal_bonus(final_types, soviet_created=False):
    """終局盤面の type ボーナス。未知の type は 0 (旧 `TB.get(t, 0)` と同じ)。"""
    bonus = 0
    for value in final_types or ():
        bonus += TYPE_BONUS.get(_as_int(value), 0)
    if soviet_created:
        bonus += SOVIET_CREATED_BONUS
    return bonus


def eval_score(raw_score, final_types, soviet_created=False):
    """旧 EVAL_SCORE と同一の値。raw + 終局 type ボーナス + 建国ボーナス。"""
    return _as_int(raw_score) + terminal_bonus(final_types, soviet_created)


# ---------------------------------------------------------------------------
# version stamping (issue #132 Phase 1: 旧 EVAL_SCORE と新目的値を混ぜない)
# ---------------------------------------------------------------------------

def stamp(record):
    """record にこの evaluator の version を付けて返す (in-place)。"""
    record["objective_schema_version"] = OBJECTIVE_SCHEMA_VERSION
    return record


def version_of(record):
    """record がどの version で書かれたか。未 stamp (旧履歴) は None。"""
    if not isinstance(record, dict):
        return None
    value = record.get("objective_schema_version")
    return str(value) if value else None


def is_current(record):
    return version_of(record) == OBJECTIVE_SCHEMA_VERSION


def mixed_versions(records):
    """まとめられない version の組み合わせを返す。空または 1 要素なら 1 種類だけ。

    None (未 stamp の旧履歴) も 1 つの version として数えます。返り値が
    2 要素以上なら、その標本は旧 EVAL_SCORE と新目的値を混ぜているため、
    丸ごと捨てるか version ごとに分離する必要があります。
    """
    seen = []
    for record in records or ():
        version = version_of(record)
        if version not in seen:
            seen.append(version)
    return seen
