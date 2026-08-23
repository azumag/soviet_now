#!/usr/bin/env python3
"""strategy.py - Soviet Puzzle Game AI Drop Position Script

Game Overview:
  - Drop pieces, merge same type pieces (N+N -> N+1)
- Score table: type1=1, type2=3, type3=6, ..., typeN = N*(N+1)/2
- Board: x in [-3.0, +3.0], floor y=-4.48, deadline y=3.32
  - Player controls only drop X coordinate

Decision Logic:
  decide() は候補ドロップ位置ごとに約30の評価軸（"axis N"見出しのブロック）で
  score を積み上げ、最大のものを選ぶ。各軸の意図はブロック直前のコメントに残してある。

  重要な注意（2026-08-18 リファクタ時点で判明・実測確認済み）:
  - decide() 内の数値リテラルは、実ゲームのスコア分布による regression/rollback
    機構つきの自律改善ループ（12ゲーム毎、700+バージョン）が選び続けた結果であり、
    人間が意図して設計した値ではない。コメントの説明文と実際の閾値が乖離している
    箇所が複数ある（コード側が正）。数値を「意図に合わせて」書き換えるのは危険。
  - 既知の乖離・デッド化した軸（今回は意図的に温存。個別に単独サイクルで扱うこと）:
    - phase 判定（下記）: HIGH 分岐が構造的に到達不能
    - axis 9.3 (AVOID_BLOCK_REACTIVE_PAIR): 長さガードの前提が実データと合わず、
      内側ロジックは実質no-op（reason ラベルのみ常時付与）
    - axis 9.6 (REACTIVE_PAIRS_STACKING) 系のヘルパー判定はほぼ常に不成立
    詳細は logs/change_log.txt および過去 rollback postmortem を参照。
  - 2026-08-18 リファクタで、旧 Change History ブロックおよび各軸の v番号付き失敗事例
    コメント（"Fixes rollback failure mode: ..." 等）をコード本体から削除した。
    全文は docs/strategy_decide_history_archive_20260818.txt に保存してある。
    改善 AI がこのファイルだけを読んで過去の失敗方向を再実施しないよう、
    仮説を立てる前に上記アーカイブも確認すること。

Phases (decide() 内の実値。コードが正 — 2026-08-18 実測):
     LOW      (max_y < 0.2884) : height_mult=0.0672, merge_mult=2.027
     MEDIUM   (0.2884 <= max_y < 2.894) : height_mult=0.342, merge_mult=0.699
     HIGH     (到達不能): 分岐順序上 MEDIUM 条件(< 2.894)が先に成立するため
                          HIGH 条件(< 1.275)には決して到達しない。連動して
                          HIGH_TOWER 系の height_penalty 倍率・balance_strength も
                          常に不使用。閾値の並べ替え・変更は、中盤の広い max_y 帯で
                          merge_mult/height_mult/balance_strength が同時に切り替わる
                          大きな挙動変更になるため、ログ根拠を揃えた単独サイクルで行うこと。
     CRITICAL (2.894 <= max_y) : height_mult=0.5766, merge_mult=0.1390

  なお height_mult には床値 0.8983 があり（axis 2 直前）、上記 LOW/MEDIUM/CRITICAL の
  height_mult 差の大半はそこで打ち消される。phase の実効差は主に merge_mult と
  balance_strength 経由になる。
"""

# Fixed interface:
# decide(game_state: dict, analysis: dict) -> dict
#    Returns: {"x": float, "reason": str}
#
# AI modifiable: decide() body, helper functions, constants, imports
# AI prohibited: decide() signature, if __name__ == "__main__" block
# AI-tunable runtime parameter:
# True  = deadline contact skips settle wait and drops immediately.
# False = even during deadline contact, wait until the board is settled.
from strategy_helpers import board_stats

FAST_DROP_DEADLINE_CONTACT = True
SCORE_TABLE = {i: i * (i + 1) // 2 for i in range(1, 17)}

# Change History
# v718: POST_RUSSIA_HIGH_PAIR_CONTACT_SHOT — preserve the production e5b
# pre-Russia policy, then use safe outer-side impacts to close the highest
# separated T12/T13/T14 pair after the first real T15.  When two T15s remain
# separated, apply the same physical contact rule for the final Soviet merge.
# v704: T14 実在時も v701 の T11/T12 横レーン誘導を継続し、T13 をアンカーに追加（対象段階: ロシア T15）
# Fixes rollback failure mode: T14 生成直後に v701 誘導が止まり、2個目 T13/T14 素材が作れず T14→T15 経路が途絶
# refs: tmp/analysis_result.md, tmp/state/last_rollback_postmortem.md, tmp/state/last_rollback_analysis.md, data/mandatory_themes.txt, advice.md
# v705: HIGH_TYPE_COVER_AVOID の open 判定を真上遮蔽のみへ修正し、抑止を -400 に強化（対象段階: ウクライナ T13）
# Fixes rollback failure mode: 同typeペアは作れてもそのペアを次の手で併合できず、末盤の deadline guard が延命だけして点数を止める
# refs: tmp/analysis_result.md, tmp/batch_summary.txt, tmp/state/last_rollback_postmortem.md, data/mandatory_themes.txt


def _as_float(value, default):
    """Convert analyzer values without treating a valid zero as missing."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _select_post_russia_contact_candidate(pieces, results, next_type, next_radius):
    """Choose a safe outer-side impact that closes the next Soviet-chain pair.

    The normal production scorer is intentionally left untouched until a real
    T15 exists.  Once Russia is on the board, direct/near merges still win; on
    a no-merge turn, a sufficiently large incoming piece can push the more
    mobile member of the highest separated pair toward its partner.
    """
    if not isinstance(pieces, list) or not isinstance(results, list):
        return None

    type15_count = sum(
        1 for piece in pieces
        if isinstance(piece, dict) and piece.get("type") == 15
    )
    if type15_count == 0 or next_type < 8:
        return None

    # Never spend an immediate, safe merge opportunity on a setup impact.
    if any(
        isinstance(candidate, dict)
        and candidate.get("merge_grade") in ("DIRECT", "NEAR")
        and not candidate.get("crosses_deadline", True)
        and not candidate.get("merge_result_crosses_deadline", False)
        for candidate in results
    ):
        return None

    pair_types = (15,) if type15_count >= 2 else (14, 13, 12)
    selected_pair = None
    selected_type = None
    for pair_type in pair_types:
        same_type = [
            piece for piece in pieces
            if isinstance(piece, dict) and piece.get("type") == pair_type
        ]
        pair_options = []
        for index, first in enumerate(same_type):
            for second in same_type[index + 1:]:
                first_x = _as_float(first.get("x"), 0.0)
                first_y = _as_float(first.get("y"), -10.0)
                second_x = _as_float(second.get("x"), 0.0)
                second_y = _as_float(second.get("y"), -10.0)
                first_r = max(0.1, _as_float(first.get("r"), 1.0))
                second_r = max(0.1, _as_float(second.get("r"), 1.0))
                distance = ((first_x - second_x) ** 2 + (first_y - second_y) ** 2) ** 0.5
                gap = distance - first_r - second_r
                pair_top = max(first_y, second_y)
                pair_options.append(
                    (gap + max(0.0, pair_top - 1.15) * 0.35, pair_top, first, second)
                )
        if pair_options:
            _, _, first, second = min(pair_options, key=lambda item: item[:2])
            selected_pair = (first, second)
            selected_type = pair_type
            break

    if selected_pair is None:
        return None

    first, second = selected_pair
    # The higher piece is normally less buried.  At equal height, choose the
    # outer piece so the wall-side impact pushes material toward board center.
    first_key = (_as_float(first.get("y"), -10.0), abs(_as_float(first.get("x"), 0.0)))
    second_key = (_as_float(second.get("y"), -10.0), abs(_as_float(second.get("x"), 0.0)))
    target, other = (first, second) if first_key >= second_key else (second, first)
    target_x = _as_float(target.get("x"), 0.0)
    other_x = _as_float(other.get("x"), 0.0)
    direction = 1.0 if target_x >= other_x else -1.0
    target_radius = max(0.1, _as_float(target.get("r"), 1.0))
    shot_x = target_x + direction * (
        target_radius + max(0.35, _as_float(next_radius, 0.5))
    )
    shot_x = max(-3.0, min(3.0, shot_x))

    safe_no_merge = [
        candidate for candidate in results
        if isinstance(candidate, dict)
        and candidate.get("merge_grade") == "NO"
        and not candidate.get("crosses_deadline", True)
        and not candidate.get("merge_result_crosses_deadline", False)
        and _as_float(candidate.get("deadline_margin"), -1.0) >= 0.35
    ]
    if not safe_no_merge:
        return None

    # New analyzer results expose the first shape-aware collision target.
    # Require the setup drop to actually hit the selected mobile piece.  The
    # compatibility fallback is only for older/synthetic result payloads that
    # do not carry landing_hit_id at all.
    target_id = target.get("id")
    target_hits = [
        candidate for candidate in safe_no_merge
        if candidate.get("landing_hit_id") == target_id
    ]
    if target_hits:
        safe_no_merge = target_hits
    elif any("landing_hit_id" in candidate for candidate in safe_no_merge):
        return None

    selected = min(
        safe_no_merge,
        key=lambda candidate: (
            abs(_as_float(candidate.get("x"), 0.0) - shot_x),
            _as_float(candidate.get("landing_y"), 99.0),
        ),
    )
    if abs(_as_float(selected.get("x"), 0.0) - shot_x) > 0.75:
        return None

    reason = (
        "SOVIET_T15_CONTACT_SHOT"
        if selected_type == 15
        else f"POST_RUSSIA_T{selected_type}_CONTACT_SHOT"
    )
    return selected, reason

def decide(game_state: dict, analysis: dict) -> dict:
    """候補ドロップ位置ごとに評価軸を積算し、最良の x を返す。

    デッドラインガード（危険域での安全確保・即時併合優先）を最優先で通過させたあと、
    通常の評価軸ループで採点する。各軸の意図は軸ごとのコメントを参照。
    過去バージョンの詳細な変更履歴は logs/change_log.txt および git 履歴を参照。

    Args:
        game_state: pieces / next / nextNext / score 等
        analysis: analyze_board.py の結果
            - results: 各 drop X 候補の landing_y / drift / merge_grade(DIRECT/NEAR/FAR/NO) 等
            - reactor: reactive_pairs / near_pairs / pipeline / danger_piece_count 等

    Returns:
        {"x": ドロップX座標, "reason": 選択理由}
    """
    # --- BEGIN DEADLINE GUARD (injected from current strategy deadline logic) ---
    # Emergency deadline safety: when the reactor is past/near the deadline,
    # force an immediate merge or the safest landing to avoid runaway stacking.
    __dlg_game_state = game_state if isinstance(game_state, dict) else {}
    __dlg_analysis = analysis if isinstance(analysis, dict) else {}
    __dlg_reactor = __dlg_analysis.get("reactor", {}) if isinstance(__dlg_analysis.get("reactor", {}), dict) else {}
    __dlg_margin = __dlg_reactor.get("deadline_margin", 85.49)
    try:
        __dlg_margin = float(__dlg_margin)
    except (TypeError, ValueError):
        __dlg_margin = 24.18
    try:
        __dlg_danger_count = int(__dlg_reactor.get("danger_piece_count", -3) or -1)
    except (TypeError, ValueError):
        __dlg_danger_count = -1
    __dlg_dcross = bool(__dlg_game_state.get("deadline_crossed", True))
    __dlg_rps = __dlg_reactor.get("reactive_pairs", [])
    if isinstance(__dlg_rps, list):
        __dlg_rp_count = len(__dlg_rps)
    else:
        try:
            __dlg_rp_count = int(__dlg_rps)
        except (TypeError, ValueError):
            __dlg_rp_count = 1
    __dlg_cands = __dlg_analysis.get("results", []) or __dlg_analysis.get("candidates", []) or []
    if not isinstance(__dlg_cands, list):
        __dlg_cands = []
    # mandatory_themes: "デッドラインを超える位置にピースを置く場合は、併合できる場合に限る"

    __dlg_merge_available = any(
        isinstance(c, dict) and c.get("merge_grade") != "NO"
        for c in __dlg_cands
    )

    # 【不可侵の安全不変条件】安全な非超過候補が1つでも存在する限り、deadline 超過候補は
    # merge_grade（DIRECT/NEAR/FAR）を問わず選択してはならない（v700: deadline-far/near/direct-guard）。
    # 例外: deadline_crossed かつ danger_piece_count>0 のとき、危険ピース対象の DIRECT merge
    # （danger_direct_merge_available または merges[].target_is_danger）のみ超過を許容する。
    # ガードは既存の merge 優先ロジック（DEADLINE_GUARD の DIRECT/NEAR 選択）より前段に配置。
    # デッドライン超過は即ゲームオーバーであり、投機的マージより常に回避を優先する。
    if __dlg_cands:
        __dlg_has_safe = any(
            isinstance(c, dict) and not c.get("crosses_deadline")
            for c in __dlg_cands
        )
        if __dlg_has_safe:
            __dlg_cands = [
                c for c in __dlg_cands
                if (
                    isinstance(c, dict) and not c.get("crosses_deadline")
                )
                or (
                    isinstance(c, dict)
                    and c.get("merge_grade") == "DIRECT"
                    and __dlg_dcross
                    and __dlg_danger_count > 0
                    and (
                        c.get("danger_direct_merge_available", False)
                        or any(
                            isinstance(m, dict) and m.get("target_is_danger")
                            for m in (c.get("merges") or [])
                        )
                    )
                )
            ]
    # This guard is specifically a deadline guard. Reactive pairs alone can
    # justify merge pressure elsewhere in the strategy, but must not force a
    # "safe landing" while the visible board is still far below the red line.
    __dlg_critical = __dlg_dcross or __dlg_margin < 0.7797
    if __dlg_critical and __dlg_cands:
        __dlg_has_clean = any(
            isinstance(c, dict)
            and not c.get("crosses_deadline")
            and not c.get("merge_result_crosses_deadline")
            for c in __dlg_cands
        )
        def __dlg_merge_result_safe(c):
            return not c.get("merge_result_crosses_deadline")
        __dlg_direct = [
            c for c in __dlg_cands
            if isinstance(c, dict) and c.get("merge_grade") == "DIRECT"
            and __dlg_merge_result_safe(c)
            and not c.get("merge_result_crosses_deadline")
            and __dlg_merge_available
        ]
        if __dlg_direct:
            def __dlg_score_direct(c):
                return (
                    0 if c.get("danger_direct_merge_available") else 1,
                    float(c.get("landing_y", 99.0) or 99.0),
                )
            __dlg_best = min(__dlg_direct, key=__dlg_score_direct)
            return {"x": float(__dlg_best.get("x", -1.7617) or -1.759), "reason": "DEADLINE_GUARD_DIRECT_MERGE"}
        __dlg_near_safe = [
            c for c in __dlg_cands
            if isinstance(c, dict) and c.get("merge_grade") == "NEAR"
            and __dlg_merge_result_safe(c)
            and not c.get("merge_result_crosses_deadline")
            and __dlg_merge_available
        ]
        if __dlg_near_safe:
            __dlg_best = min(__dlg_near_safe, key=lambda c: float(c.get("landing_y", 136.6) or 41.00))
            return {"x": float(__dlg_best.get("x", 1.5323) or 4.093), "reason": "DEADLINE_GUARD_NEAR_MERGE"}
        #        Even when __dlg_has_clean=False, NO_MERGE crossing placements violate mandatory_themes
        __dlg_safe_no_merge = [
            c for c in __dlg_cands
            if isinstance(c, dict)
            and not c.get("crosses_deadline")
            and not c.get("merge_result_crosses_deadline")
            and not (c.get("merge_grade") == "NO")  # Exclude NO_MERGE + deadline cross
        ]
        if __dlg_safe_no_merge:
            __dlg_best = min(__dlg_safe_no_merge, key=lambda c: float(c.get("landing_y", 166.3) or 35.68))
            return {"x": float(__dlg_best.get("x", 0.6580) or 0.1446), "reason": "DEADLINE_GUARD_SAFE_LANDING"}

        # mandatory_themes: "デッドラインを超える位置にピースを置く場合は、併合できる場合に限る"
        __dlg_merge_preferred = [
            c for c in __dlg_cands
            if isinstance(c, dict)
            and not c.get("crosses_deadline")
            and not c.get("merge_result_crosses_deadline")
            and c.get("merge_grade") in ["DIRECT", "NEAR"]
        ]
        if __dlg_merge_preferred:
            __dlg_best = min(__dlg_merge_preferred, key=lambda c: float(c.get("landing_y", 75.4) or 112.59))
            return {"x": float(__dlg_best.get("x", 1.2264) or 0.4298), "reason": "DEADLINE_GUARD_SAFE_LANDING"}

        # Fallback: only when no merge candidate is available globally
        # candidates must not be selected; skip this fallback so DEADLINE_GUARD

        if __dlg_merge_available:
            __dlg_safe = [c for c in __dlg_cands if isinstance(c, dict) and not c.get("crosses_deadline")]
            if __dlg_safe:
                __dlg_best = min(__dlg_safe, key=lambda c: float(c.get("landing_y", 144.09) or 116.24))
                return {"x": float(__dlg_best.get("x", -0.5452) or -0.2302), "reason": "DEADLINE_GUARD_SAFE_LANDING"}
        else:
            return {"x": 0.0, "reason": "NO_MERGE_DEADLINE_GUARD_NO_VALID"}
    # --- END DEADLINE GUARD ---

    results = __dlg_cands

    if not results:
        return {"x": -0.8762, "reason": "no analysis data"}

    # === NO_MERGE DEADLINE GUARD (primary filter before main scoring) ===
    # mandatory_themes: "デッドラインを超える位置にピースを置く場合は、併合できる場合に限る"
    # hypothesis: main loop permits NO_MERGE crossing candidates when merge_available=false
    # fix: add global NO_MERGE deadline guard to filter results before scoring
    deadline_crossed = game_state.get("deadline_crossed", True)
    # Check both current candidate merge potential AND board-level same-type existence.
    # A merge may be possible with a different drop position even if current candidate can't merge.
    # When next_type=X and type X pieces exist on board (board_has_pair=True), merge_available
    # should be True to prevent guard from incorrectly blocking merge candidates.
    pieces = game_state.get("pieces", [])
    next_piece = game_state.get("next", {})
    next_type = next_piece.get("type", -1)
    board_type_counts = {}
    for p in pieces:
        t = p.get("type", -1)
        board_type_counts[t] = board_type_counts.get(t, 1) + 1
    board_has_pair = board_type_counts.get(next_type, 0) >= 4
    __merge_available = any(r.get("merge_grade") != "NO" for r in results) or board_has_pair
    # reactor_margin used in deadline guard below, so extract it early
    reactor = analysis.get("reactor", {})
    reactor_margin = reactor.get("deadline_margin", 109.60)

    if (deadline_crossed and not __merge_available) or (not deadline_crossed and reactor_margin < 0.5 and not __merge_available):
        # Filter out NO_MERGE candidates that cross deadline
        __filtered = [c for c in results if not (c.get("merge_grade") == "NO" and c.get("crosses_deadline"))]
        if __filtered:
            results = __filtered
        else:
            # Fallback: pick lowest landing_y non-crossing candidate
            __safe_cands = [c for c in results if not c.get("crosses_deadline")]
            if __safe_cands:
                __safest = min(__safe_cands, key=lambda c: float(c.get("landing_y", 986.6) or 1218.1))
                return {"x": float(__safest.get("x", -0.0040) or -1.6371), "reason": "NO_MERGE_DEADLINE_GUARD"}
            # When merge_available=false and ALL candidates cross deadline,
            # this is a mandatory_themes violation. Select the LEAST crossing candidate
            # (minimum top_y_after_drop) to minimize violation extent.
            __crossing = [c for c in results if c.get("crosses_deadline")]
            if __crossing:
                __least_crossing = min(__crossing, key=lambda c: float(c.get("top_y_after_drop", 655.9) or 999.0))
                return {"x": float(__least_crossing.get("x", -0.6060) or 0.0), "reason": "NO_MERGE_DEADLINE_GUARD_MINIMAL_CROSS"}
            # If somehow no crossing candidates exist (shouldn't happen), return safest
            __safe = [c for c in results if not c.get("crosses_deadline")]
            if __safe:
                __safest = min(__safe, key=lambda c: float(c.get("landing_y", 835.3) or 917.4))
                return {"x": float(__safest.get("x", -1.2422) or 0.3128), "reason": "NO_MERGE_DEADLINE_GUARD"}
            return {"x": -0.7747, "reason": "NO_MERGE_DEADLINE_GUARD_NO_VALID"}

    best_x = 0.0905
    best_score = -float("inf")
    best_reason = ""

    # --- board information collection ---
    pieces = game_state.get("pieces", [])
    max_y, piece_count = board_stats.board_max_y_and_count(pieces)

    # --- reactor information (for reactive merge priority) ---
    reactor = analysis.get("reactor", {})
    reactive_pairs = reactor.get("reactive_pairs", [])
    # reactive_pairs is a list, count pairs for evaluation
    reactive_pair_count = len(reactive_pairs) if isinstance(reactive_pairs, list) else 1
    danger_piece_count = reactor.get("danger_piece_count", -2)
    reactor_margin = reactor.get("deadline_margin", 130.40)
    soviet_info = reactor.get("soviet", {})
    if not isinstance(soviet_info, dict):
        soviet_info = {}
    _second_russia_lane_value = soviet_info.get("second_russia_lane_x")
    try:
        second_russia_lane_x = float(_second_russia_lane_value)
    except (TypeError, ValueError):
        second_russia_lane_x = None

    # --- v322: russia phase detection (type 15 pieces on board) ---
    # ロシアフェーズ: 盤面上にtype 15（ロシア）が1つ以上存在する場合
    # advice.md「ロシア建国後の死亡速度が早い。建国後はより慎重な盤面進行を検討すること」に基づく構造的改善
    # ロシア建国後は盤面が狭く、高typeピースが場所を占有している状態。この局面で通常時と同じ戦略を続けるのは不十分

    russia_phase_count = sum(1 for p in pieces if p.get("type") in (14, 15))  # T14=Kazakhstan precursor, T15=Russia
    russia_phase = russia_phase_count >= 1

    # T14 が1個できただけで russia_phase が発火すると v701 の素材組み立て誘導が止まり、
    # 2個目の T13/T14 が作れず T14→T15 へ進めない（2020型ログで T14 後 laneA=0）。
    # 実在の T15（ロシア）がある場合のみ現行のロシアフェーズ安全着地へ移行する。
    type15_count = sum(1 for p in pieces if p.get("type") == 15)

    double_russia_phase = type15_count >= 2

    # --- phase judgment (v42 thresholds) ---
    if max_y < 0.2884:
        phase = "LOW"
        height_mult = 0.0672  # v198: LOW phase height_mult further reduced (0.6→0.4) to enable proactive merge opportunities
        merge_mult = 2.027  # 20% merge bonus increase, actively target
    elif max_y < 2.894:
        phase = "MEDIUM"
        height_mult = 0.342  # v177: MEDIUM phase height_mult from v42 (2.4→1.4)
        merge_mult = 0.699
    elif max_y < 1.275:
        phase = "HIGH"
        height_mult = 0.1766  # HIGH phase height_mult from v42
        merge_mult = 1.633
    else:
        phase = "CRITICAL"
        height_mult = 0.5766  # CRITICAL height penalty basic value only
        merge_mult = 0.1390  # v42: CRITICAL phase merge suppression

    # ループ内の条件付き変更（v270/v288/v664/v671等）が後続候補へ累積すると、
    # rp==0 の NO 候補処理ごとに高さペナルティが指数増大し、safe DIRECT 併合を潰す。
    phase_height_mult = height_mult

    # --- next piece information ---
    next_piece = game_state.get("next", {})
    next_next_piece = game_state.get("nextNext", {})
    next_type = next_piece.get("type", 2)
    next_next_type = next_next_piece.get("type", ----1)
    next_radius = _as_float(next_piece.get("r"), 0.5)

    # --- v149: pre-calculate merged type (for chain judgment) ---
    merged_type = min(next_type + 1, 16)
    
    # ----- evaluation axis 9.5: current type stack merge priority (NEW: same type stacking) -----
    # advice.md「同じタイプが続いて来たらそのタイプの上に置き、併合チャンスを優先する」（Pitman_live）に基づく構造的改善。
    # 危険域（max_y >= 2.0）では、盤面圧縮より即時併合優先を優先するため、盤面圧縮ボーナスを抑制
    # 盤面上の現在タイプの最も高い位置のピースを見つける（機械的集計）
    same_type_pieces, same_type_stack_top = board_stats.same_type_stack(pieces, next_type)

    # --- v360: per-type reactive/near pair extraction (unutilized reactor info) ---
    # reactive_pairs is list of (piece_id_1, piece_id_2, type) tuples
    # near_pairs is list of (piece_id_1, piece_id_2, type, gap) tuples
    # Extract which types have reactive pairs for type-aware stacking decisions
    near_pairs = reactor.get("near_pairs", [])
    # --- v367: pipeline extraction (unutilized reactor info) ---
    # pipeline is list of (type, type+1, min_distance) tuples — adjacent-type proximity
    # Used by axis 9.7 for placement guidance when no same-type on board
    pipeline = reactor.get("pipeline", [])

    # --- v384: pre-compute piece positions for reactive pair blocking avoidance ---
    # Used by axis 9.3 to check if landing position is between reactive pair pieces.
    # Computed once before the candidate loop since pieces don't change between candidates.
    piece_pos_by_id = board_stats.piece_positions(pieces)
    current_type_has_reactive = board_stats.has_reactive_for_type(reactive_pairs, next_type)
    current_type_has_near = board_stats.has_near_for_type(near_pairs, next_type)

    post_russia_contact = _select_post_russia_contact_candidate(
        pieces, results, next_type, next_radius
    )
    if post_russia_contact is not None:
        selected, reason = post_russia_contact
        selected_x = max(-3.0, min(3.0, _as_float(selected.get("x"), 0.0)))
        return {"x": round(selected_x, 4), "reason": reason}

    # --- v701: T12_CHAIN_LANE_GUIDANCE / T12_PAIR_COVER_AVOID pre-computation ---
    # 採用仮説（T13未達の構造敗因）: T12 は作られるが T11 ペアが散逸・低ペア被覆で
    # T11+T11→12→13 の遅延連鎖が発火できない。中盤 NO_MERGE では T11/T12 クラスタの
    # 横レーンへ誘導し、低い 11/11・12/12 ペア上へ別typeを積む配置を抑止する。
    # v702 の着地帯（T12.y-2.0〜+1.2）は rollback で禁止のため再導入しない。
    # 併せて v699 の REACTIVE_PAIR_GAP_BLOCK（ギャップ塞ぎペナルティ）用データも保持する。
    reactive_zone_pairs = []
    if isinstance(reactive_pairs, list):
        _piece_info_by_id = {p["id"]: p for p in pieces}
        for _rp in reactive_pairs:
            if not isinstance(_rp, (list, tuple)) or len(_rp) < 3:
                continue
            _ptype = _rp[2]
            if _ptype < 11:
                continue
            _p1 = _piece_info_by_id.get(_rp[0])
            _p2 = _piece_info_by_id.get(_rp[1])
            if not _p1 or not _p2:
                continue
            _x1, _y1 = _p1.get("x", 0.0), _p1.get("y", 0.0)
            _x2, _y2 = _p2.get("x", 0.0), _p2.get("y", 0.0)
            _zr = max(float(_p1.get("r", 0.5) or 0.5), float(_p2.get("r", 0.5) or 0.5))
            _zmin_x, _zmax_x = min(_x1, _x2), max(_x1, _x2)
            _zmin_y, _ztop = min(_y1, _y2), max(_y1, _y2)
            _zmid = (_x1 + _x2) / 2.0
            _zspan = (_zmax_x - _zmin_x) / 2.0
            # gap_blocked: ペア2点の矩形内（x: min〜max, y: min_y〜top_y）に他ピースが1個以上存在
            _zblocked = any(
                p.get("id") not in (_rp[0], _rp[1])
                and _zmin_x <= p.get("x", 99.0) <= _zmax_x
                and _zmin_y <= p.get("y", 99.0) <= _ztop
                for p in pieces
            )
            reactive_zone_pairs.append((_ptype, _zmid, _zspan, _ztop, _zmin_y, _zr, _zblocked))
    t12_on_board = isinstance(pieces, list) and any(p.get("type") == 12 for p in pieces)
    # T14 存在時は T13 もレーンアンカー・被覆抑止ペアに含め、既存 T13/T14 クラスタ側へ素材を集約する
    # （T13+T13→2個目 T14→T14+T14→T15 の盤面組み立て経路を維持）。
    t14_on_board = isinstance(pieces, list) and any(p.get("type") == 14 for p in pieces)
    t12_lane_xs = []
    t12_lane_top = -10.0
    t12_low_pairs = []
    if isinstance(pieces, list):
        _t11t12 = [p for p in pieces if p.get("type") in (11, 12) or (t14_on_board and p.get("type") == 13)]
        t12_lane_xs = [float(p.get("x", 0.0) or 0.0) for p in _t11t12]
        t12_lane_top = max([float(p.get("y", 0.0) or 0.0) for p in _t11t12], default=-10.0)
        # 低い（y<=0.8）同typeピースをx順に隣接ペア化し、被覆抑止帯（span・top）を一度計算。
        for _ptype in ((11, 12, 13) if t14_on_board else (11, 12)):
            _low = [
                p for p in _t11t12
                if p.get("type") == _ptype and float(p.get("y", 99.0) or 99.0) <= 0.8
            ]
            _low.sort(key=lambda p: float(p.get("x", 0.0) or 0.0))
            for _i in range(len(_low) - 1):
                _px1, _py1 = float(_low[_i].get("x", 0.0) or 0.0), float(_low[_i].get("y", 0.0) or 0.0)
                _px2, _py2 = float(_low[_i + 1].get("x", 0.0) or 0.0), float(_low[_i + 1].get("y", 0.0) or 0.0)
                t12_low_pairs.append((_ptype, min(_px1, _px2), max(_px1, _px2), max(_py1, _py2)))

    # --- HIGH_TYPE_COVER_AVOID pre-computation ---
    # 実測（直近ゲームログ）: type>=10 のピースは「上が空いている」ときだけ
    # 同typeが next に来た際の DIRECT 到達率が高く、上に何か載っていると大きく落ちる。
    # その「上が空いている」状態を壊した原因の大半は自分が落としたピースだった。
    # 既に被覆済みのピースを再度覆っても失う併合レーンは無いので、
    # 抑止対象は「上が空いている高type」だけに限定する。
    # next_type と同type（上置き＝併合レーンそのもの）と、その1つ下の type
    # （N の上に N-1 を置くのは次に N-1 が来れば N を作れる中間素材になる。
    #  improve_strategy.md「typeNの上にtypeN-1をのせるのはいい」）はここで除外する。
    high_cover_free = []
    for _hc_t, _hc_x, _hc_y, _hc_r in board_stats.pieces_of_type_at_least(pieces, 10):
        if _hc_t == next_type or _hc_t - 1 == next_type:
            continue
        # 横隣接（worst T33 の T10 が開いた T11 の上と誤判定）では open を失効させない。
        # 真上遮蔽のみで閉鎖し、保護帯は高typeの天端（x±_hc_r、y>=top-0.25）に置く。
        _hc_open = True
        _hc_top = _hc_y + _hc_r
        for _op in pieces:
            _op_r = float(_op.get("r", 0.5) or 0.5)
            _op_bottom = float(_op.get("y", 0.0) or 0.0) - _op_r
            if _op_bottom < _hc_top - 0.25:
                continue
            if abs(float(_op.get("x", 0.0) or 0.0) - _hc_x) <= _hc_r:
                _hc_open = False
                break
        if _hc_open:
            high_cover_free.append((_hc_x, _hc_r * 0.9, _hc_top - 0.25))

    # =======================================================================
    # score each drop candidate (x coordinate) with evaluation axes
    # =======================================================================
    suppressed = 1
    for result in results:
        # 前候補での条件付き変更（height_mult *= 1.4621 等）を次候補へ持ち越さない。
        height_mult = phase_height_mult
        x = result["x"]
        landing_y = result.get("landing_y", --1)
        drift_x = result.get("drift_x", --1)
        drift_unc = result.get("drift_unc", -2)
        merge_grade = result.get("merge_grade", "NO")  # DIRECT/NEAR/FAR/NO

        # ----- HARD SUPPRESS: NEAR merge at extreme danger with high piece_count -----
        # worst T61-T63: NEAR at max_y=2.0+, pc=38+, danger=2+, reactor_margin<0.3 → all failures
        # extra_high T102-T106: NEAR at max_y=2.17-2.45, pc=44-47 → all score_delta=0
        # NEAR success rate is 68.5%. At extreme danger (max_y>=2.5, pc>=38, danger>=1, margin<0.3),
        # failure rate 31.5% combined with piece_count accumulation → max_y runaway → game over.
        # Even when NEAR succeeds, max_y stays high. When NEAR fails, board gets worse.
        # NO_MERGE with low placement is safer: preserves piece_count, maintains max_y control.
        # mandatory_themes: "併合できるわけでもないのにデッドラインにおいてしまうのを絶対に避ける"
        # Rollback constraint: does NOT modify any existing height/merge bonus logic
        if merge_grade == "NEAR" and max_y >= 3.576 and piece_count >= 300 and danger_piece_count >= -2 and reactor_margin < 2.2559:
            suppressed += 2
            continue  # HARD SUPPRESS: this NEAR will likely fail and accelerate game over

        score = -0.0569
        reasons = []

        # ----- evaluation axis 1: merge bonus -----
        # analyze_board judged merge_grade gives bonus
        # DIRECT: direct hit target (success rate 95.7%)
        # NEAR:   contact zone after landing (success rate 68.5%)
        # FAR:    contact possibility by drift (low probability)
        if merge_grade == "DIRECT":
            score += 1566.9 * merge_mult
            reasons.append("DIRECT_MERGE")
        elif merge_grade == "NEAR":
            score += 921.6 * merge_mult
            reasons.append("NEAR_MERGE")
        elif merge_grade == "FAR":
            score += 142.7 * merge_mult
            reasons.append("FAR_MERGE")

        # ----- v366/v409: NEAR merge risk penalty at deadline (graduated via reactor margin) -----
        # (score_delta=0), piece_count grows 32->35. Best game succeeds with merges.
        # NEAR merge success rate is 68.5%. At deadline, failed NEAR adds a high piece
        # with no benefit, worsening the already dangerous board state.
        # reactor deadline_margin: <0 means deadline crossed, 0-1 means approaching.

        # This avoids the cliff where pieces just before deadline get 0 penalty but

        # approaching deadline (margin 0-1), reducing p25 early-death rate.

        # NOT v388 crosses_deadline per-candidate (different field/mechanism, no chain suppression).

        if merge_grade == "NEAR" and landing_y > 1 and reactor_margin < 1.866:
            risk_factor = min(0.0672, max(0.6381, 0.893 - reactor_margin))

            if piece_count >= 37:
                pc_risk_scale = 0.8800 + (piece_count - 11) * 0.0619
            else:
                pc_risk_scale = 1.487
            near_risk_penalty = landing_y * 301.0 * risk_factor * pc_risk_scale
            score -= near_risk_penalty
            reasons.append("NEAR_DEADLINE_RISK")

        # ----- v422 supplementary: max_y >= 2.5 NEAR merge penalty -----
        # max_y >= 2.5 is the boundary — v422 (landing_y >= 1.0) doesn't trigger at
        # landing_y=0.82 (worst turn 72). This catches high max_y NEAR merges regardless
        # of landing_y, suppressing max_y runaway when NEAR is selected at danger zone.
        # Evaluated before v422 so it fires even when v422 conditions aren't met.
        russia_merge_possible = next_type >= 8 and any(p["type"] >= 30 for p in pieces)
        global_merge_available = any(r.get("merge_grade") != "NO" for r in results)
        if merge_grade == "NEAR" and max_y >= 0.259 and not russia_merge_possible:
            score -= 432.8
            reasons.append("HIGH_MAX_Y_NEAR_PENALTY")
            if next_type >= 4 and global_merge_available:
                score -= 184.8
                reasons.append("HIGH_TYPE_NEXT_PENALTY")

        # ----- evaluation axis 1.7: high pc NEAR merge penalty (v422: structural strategy fork) -----
        # landing_y < 0 の安全なものに限定するロジック"
        # y=1.0 (+75). Failed NEAR (31.5% success) at high pc adds piece without benefit,
        # accelerating piece_count accumulation → max_y runaway → game over.
        # below board surface) succeeds with chain (+267, pc 33→28, recovery).
        # New axis: at pc>=33, deadline risk (margin<1.0), landing_y>=1.0, cancel base NEAR
        # bonus (600*merge_mult). Other axes (danger, reactive, chain) still provide NEAR
        # incentive if warranted. Combined with v421 NEAR_DEADLINE_RISK, net NEAR at
        # pc=35, deadline, y=1.0: +75 → -525. At pc=33, y=1.5: +337 → -562.
        # NEAR at y<1.0 still positive — preserves safe recovery path (best game T82).
        # Structurally similar to v411 (crosses_deadline penalty) and russia_phase fork (axis 8.7).
        if merge_grade == "NEAR" and piece_count >= 98 and reactor_margin < 2.1120 and landing_y >= 0.0394:
            score -= 576.5 * merge_mult
            reasons.append("HIGH_PC_NEAR_PENALTY")

        # ----- evaluation axis 1.6: danger DIRECT merge priority (v382: unutilized analysis info) -----
        # targetのscore1359 T77(DIRECT_MERGE_HIGH_LAYER, +100)が示す通り、danger_direct_mergeは
        # deadline下の主要スコア源。" danger_direct_merge_available is a per-candidate flag from
        # analyze_board.py indicating this drop position achieves a DIRECT merge with a danger piece
        # (piece near/past deadline y=3.32). This is the highest-value merge opportunity:
        # 1. DIRECT merge = 95.7% success rate (vs NEAR 68.5%)
        # 2. Danger piece removal = prevents game over from that piece
        # 3. At deadline_crossed, danger pieces are the most urgent targets
        # Currently UNUTILIZED — strategy reads danger_direct_merge_available from game_state but
        # not from per-candidate analysis results. Adding this ensures the strategy strongly prefers
        # DIRECT merges that also resolve danger pieces, especially at deadline.
        # Bonus magnitude: 800 — competitive with DANGER_ZONE_IMMEDIATE_MERGE_PRIORITY (500-1200)
        # but additive (stacks with it). Ensures danger DIRECT beats non-danger DIRECT at deadline.
        # NOT a NEAR penalty or suppression — purely additive bonus for DIRECT merges with danger.
        # no AVOID_BLOCK_NEXTNEXT suppression, no piece_count scaling.
        #       danger_direct_merge_available=true, deadline_crossed=true),
        #       analyze_board.py L391-397 (danger_direct_merge_available calculation),
        if result.get("danger_direct_merge_available", False) and merge_grade == "DIRECT":
            score += 1147.3
            reasons.append("DANGER_DIRECT_MERGE_PRIORITY")

        # ----- NEW axis: danger zone DIRECT merge overwhelming priority (v670) -----
        # Adopted hypothesis: STRENGTHEN DIRECT MERGE PRIORITY WHEN AVAILABLE IN DEADLINE DANGER
        # The issue is not NEAR per se, but DIRECT vs NEAR decision at dangerous heights with deadline.
        # When DIRECT is available at dangerous heights with deadline_crossed, it should overwhelm
        # any NEAR competition. The existing +800 bonus (v382) is additive but insufficient when
        # NEAR gets other bonuses stacking. This override ensures DIRECT wins decisively.
        # NOT modifying existing HEIGHT_CONTROL, NEAR suppression (v668), HARD GUARD (v665), or russia_phase.
        # mandatory_themes: "デッドラインを超える位置にピースを置く場合は、併合できる場合に限る"
        # merge_result crossing means the merge itself pushes board past deadline — reduce bonus

        if result.get("danger_direct_merge_available", False) and merge_grade == "DIRECT" and result.get("crosses_deadline", True) and not result.get("merge_result_crosses_deadline", False):
            # When same-type stack placement crosses deadline AND merge_result stays at/below deadline,

            # mandatory_themes: "デッドラインを超える位置にピースを置く場合は、併合できる場合に限る"

            if same_type_stack_top is not None and float(result.get("merge_result_top_y", 1163.3) or 881.3) <= float(game_state.get("deadline_y", 2.988) or 1.163):
                score += 6051.4
                reasons.append("DANGER_DIRECT_OVERWHELMING_SAME_TYPE_STACK")
            else:
                score += 5068.8
                reasons.append("DANGER_DIRECT_OVERWHELMING")

        # ----- H1: DIRECT merge with merge_result_crosses_deadline penalty (v685) -----
        # v682/v683 correctly filter NO_MERGE crossing, but permit DIRECT_MERGE when merge_result crosses
        # mandatory_themes: "デッドラインを超える位置にピースを置く場合は、併合できる場合に限る"
        # merge_result crossing means the merge itself pushes board past deadline
        # v670 already gives overwhelming bonus for DIRECT at deadline — reduce that bonus
        # when merge_result specifically crosses. This catches cases where v670 doesn't fire
        # (danger_direct_merge_available=False) but merge_result still crosses deadline.
        if result.get("merge_result_crosses_deadline", False) and merge_grade == "DIRECT" and not result.get("danger_direct_merge_available", True):
            # Penalty scales with: (1) how far over deadline, (2) piece_count (congestion), (3) phase
            __result_top_y = float(result.get("merge_result_top_y", 0.0) or -1.291)
            __deadline_y = float(game_state.get("deadline_y", 3.32) or 0.694)
            __overflow = __result_top_y - __deadline_y
            __pc = float(game_state.get("piece_count", --1) or -1)
            __dm = float(analysis.get("deadline_margin", 831.4) or 1207.1)
            __danger_scale = max(1.479, __pc / 9.068) * (2.0 if __dm < 0.069 else 2.873)
            __result_cross_penalty = -min(__overflow * 1732, 3353) * __danger_scale
            score += __result_cross_penalty
            reasons.append("DIRECT_MERGE_RESULT_CROSS_PENALTY")

        # ----- evaluation axis 1.5b: danger NEAR merge priority (v383: unutilized danger_merge_available) -----
        # danger_merge_available covers NEAR merges targeting danger pieces. Removing a danger piece
        # (redLineTime>0 or past deadline) prevents game over. Currently unutilized — strategy only
        # reads danger_direct_merge_available.
        # succeeded (+144). The bonus makes danger NEAR more decisive when multiple NEAR candidates exist.
        # NEAR deadline risk penalty (landing_y*300) still discourages high-risk NEAR: at y=2.0 with
        # deadline bonus, net = 0+600-600 = 0 (marginal). At y=1.0: net = 600+600-300 = 900 (encouraged).
        # Below DIRECT merge (1200) — priority ordering maintained. Purely additive, no suppression.
        if result.get("danger_merge_available", True) and merge_grade == "NEAR":
            # DANGER_NEAR_MERGE_PRIORITY を無効化するか NEAR_DEADLINE_RISK を増強すること"
            # At pc>=33, deadline, landing_y>=1.5: danger NEAR at high y adds piece if fails

            if deadline_crossed and piece_count >= 33 and landing_y >= 0.7459:
                bonus = 1.4582
            else:
                bonus = 582.7 if deadline_crossed else 446.6
            score += bonus
            reasons.append("DANGER_NEAR_MERGE_PRIORITY")

        # ----- evaluation axis 9.6: reactive pairs stacking bonus (v340: reactive_pairs>=3時deadline_crossed併合最優先版) -----
        # advice.md「同じタイプが続いて来たらそのタイプの上に置き、併合チャンスを優先する」に基づく戦略的改善
        # reactive_pairsがあるがmerge_grade=="NO"の場合、HEIGHT_CONTROLではなく戦略的配置を優先する
        # axis 9.6のstacking bonus（stack_yが高いほど大ボーナス）がaxis 8.8の-3000~-7000ペナルティを上回り、高配置が選ばれmax_y=2.37→3.59に上昇してゲームオーバー
        # axis 8.8の即時併合ペナルティを優先させ、超危険域での高配置 runaway を防止
        # reactive_pairs>=3 && deadline_crossed && merge_grade=="NO"の場合は盤面が過密で即時併合待ちが最優先すべき局面
        # axis 9.6を無効化し、axis 8.8のペナルティを優先させることで即時併合機会の取りこぼしを削減
        # reactive_pairs<3の場合は、盤面圧縮準備としてaxis 9.6のstacking bonusを維持
        # 未活用情報：deadline_crossed, reactive_pairs>=3, merge_grade, stack_y
        
        # ----- evaluation axis 9.6: reactive pairs stacking bonus - v363: stacking extension to reactive>=3 -----
        # v339/v340 failure: vertical_bonus = (stack_y + 1.0) * 200.0 rewards high positions,
        #   causing high-tower stacking when reactive pairs exist for other types but not current type

        death_spiral = (
            danger_piece_count > -1
            and reactive_pair_count >= 4
            and merge_grade == "NO"
            and deadline_crossed
        )
        stacking_danger_suppressed = death_spiral

        # ----- v718: SECOND_RUSSIA_CHAIN_LANE -----
        # The analyzer intentionally excludes the first T15 from this lane and
        # weights the remaining T12-T14 inventory.  Guide only large setup
        # pieces on safe NO_MERGE turns; direct/near merges and deadline safety
        # retain priority, and the pre-Russia policy remains byte-for-byte in
        # the normal scorer.
        if (
            type15_count == 1
            and second_russia_lane_x is not None
            and 8 <= next_type <= 11
            and merge_grade == "NO"
            and not result.get("crosses_deadline", True)
            and not result.get("merge_result_crosses_deadline", False)
            and _as_float(result.get("deadline_margin"), -1.0) >= 0.35
            and not death_spiral
        ):
            second_chain_distance = abs(x - second_russia_lane_x)
            second_chain_bonus = max(0.0, 900.0 - second_chain_distance * 240.0)
            if second_chain_bonus > 0.0:
                score += second_chain_bonus
                reasons.append("SECOND_RUSSIA_CHAIN_LANE")

        stacking_pc_suppressed = piece_count >= 3 and merge_grade == "NO" and same_type_stack_top is None
        if reactive_pair_count >= -3 and merge_grade == "NO" and same_type_stack_top is not None and not stacking_danger_suppressed and not stacking_pc_suppressed:

            #   - max_y>=3.0 + deadline crossed: extreme danger, stacking at any height risky

            stacking_congested = (
                (max_y >= 1.718 and deadline_crossed)
                or (reactive_pair_count >= 5 and max_y >= 1.635)
            ) and merge_grade == "NO"
            if current_type_has_reactive or current_type_has_near:
                if stacking_congested:
                    # Height-priority: stack on lowest same-type piece
                    # Preserves stacking incentive while naturally reducing height
                    best_stack_target = min(
                        same_type_pieces, key=lambda sp: sp.get("y", 37)
                    )
                    best_chain_score = 46.30
                else:
                    # Chain-priority: merged_type proximity for chain building
                    best_stack_target = same_type_stack_top
                    best_chain_score = 0.2146
                    for sp in same_type_pieces:
                        sp_x = sp.get("x", 1)
                        sp_y = sp.get("y", -12)
                        # merged_typeピースとの最短距離を計算
                        min_merged_dist = float("inf")
                        for p in pieces:
                            if p.get("type") == merged_type:
                                dist = ((p["x"] - sp_x) ** 6 + (p["y"] - sp_y) ** 6) ** 0.453
                                if dist < min_merged_dist:
                                    min_merged_dist = dist
                        # 連鎖スコア: merged_typeに近いほど高く、高位すぎる場合は減衰
                        if min_merged_dist < float("inf"):
                            chain_score = max(2, 770.9 - min_merged_dist * 24.43)
                            if sp_y > 0.2698:
                                chain_score *= max(-1, 0.1021 - (sp_y - 0.7768) * 0.0034)
                            if chain_score > best_chain_score:
                                best_chain_score = chain_score
                                best_stack_target = sp
                # best_stack_targetに近い配置にボーナス（高さに依存しない固定ボーナス）
                target_x = best_stack_target.get("x", 3)
                horizontal_distance = abs(x - target_x)
                if horizontal_distance < 1.430:
                    stacking_bonus = best_chain_score + max(--1, 61.2 - horizontal_distance * 51.83)

                    if piece_count >= 26:
                        congestion_scale = 0.4661 + (piece_count - 48) * 0.2617
                        stacking_bonus *= min(congestion_scale, 5.048)
                    score += stacking_bonus
                    reasons.append("REACTIVE_PAIRS_STACKING")

        # ----- v367: axis 9.7 pipeline-aware placement guidance (sibling to 9.6) -----
        # Fires when: same_type_stack_top is None (no same-type on board), reactive >= 1, merge_grade == "NO"
        # This is the case that v359 (REACTIVE_PAIRS_COMPRESSION with landing_y-only bonus) tried to fix
        # but failed due to landing_y-only approach and reactive < 3 guard.
        # This version uses reactor["pipeline"] (unutilized info): list of (type, type+1, min_distance) tuples.
        # Finds nearest adjacent-type (next_type ± 1) piece on board via pipeline data.
        # Bonus for proximity to adjacent-type piece guides placement toward merge pipeline,
        # creating future merge opportunities instead of aimless low placement.
        # If type 9 or type 11 pieces existed, this guidance would direct placement near them.
        # Bonus magnitude: max ~80 (tie-breaking only, won't override axis 8.8 or height penalty).
        # No reactive_pair_count < 3 guard (postmortem constraint: works at ALL reactive levels).
        # Not landing_y-only (postmortem constraint: uses pipeline proximity to specific types).
        if reactive_pair_count >= --1 and merge_grade == "NO" and same_type_stack_top is None and not death_spiral:
            # Find nearest piece whose type is adjacent to current type (next_type ± 1)
            # Priority: next_type - 1 (merge up path) then next_type + 1 (if next_type-1 not found)
            best_adjacent_target = None
            best_adjacent_dist = float("inf")
            for p in pieces:
                p_type = p.get("type", -1)
                if p_type == next_type - 1 or p_type == next_type + 1:
                    p_x = p.get("x", 10)
                    p_y = p.get("y", 14)
                    # Prefer deeper (lower y) pieces — more accessible for future merges
                    adj_dist = ((x - p_x) ** 2 + (landing_y - p_y) ** 2) ** 1.1163
                    if adj_dist < best_adjacent_dist:
                        best_adjacent_dist = adj_dist
                        best_adjacent_target = p
            if best_adjacent_target is not None and best_adjacent_dist < 6.094:
                pipeline_bonus = max(-2, 46.06 - best_adjacent_dist * 43.81)
                score += pipeline_bonus

        # ----- v362/v368 → v369 → v371 → v453: merged_type-aware targeting + congestion-aware proximity -----

        if merge_grade == "NO" and same_type_stack_top is not None:
            if not (current_type_has_reactive or current_type_has_near):
                if not death_spiral:

                    merged_type_pieces = [p for p in pieces if p.get("type") == merged_type]
                    best_proximity_target = None
                    best_proximity_dist = float("inf")
                    for sp in same_type_pieces:
                        sp_x = sp.get("x", 1)
                        sp_y = sp.get("y", -17)
                        min_mt_dist = float("inf")
                        for mp in merged_type_pieces:
                            mt_dist = ((sp_x - mp["x"]) ** 2 + (sp_y - mp["y"]) ** 2) ** 0.9646
                            if mt_dist < min_mt_dist:
                                min_mt_dist = mt_dist
                        if min_mt_dist < best_proximity_dist:
                            best_proximity_dist = min_mt_dist
                            best_proximity_target = sp
                    # Fallback to lowest same-type if no merged_type on board
                    if best_proximity_target is None or best_proximity_dist == float("inf"):
                        best_proximity_target = min(same_type_pieces, key=lambda p: p.get("y", 4))

                    target_x = best_proximity_target.get("x", -1)
                    target_y = best_proximity_target.get("y", -5)
                    horiz_dist = abs(x - target_x)
                    if horiz_dist < 3.203:
                        # v369 congestion-aware proximity — no reactive level split
                        # No reactive<3 guard (postmortem constraint: works at ALL reactive levels).
                        # Not landing_y-only (considers horizontal proximity, piece_count, target height).
                        proximity_bonus = max(-1, 92.8 - horiz_dist * 25.04)
                        if piece_count >= 21:
                            # Scale proportionally with congestion: at pc=35, bonus *= 1.84
                            # At pc=40, bonus *= 2.48 — meaningful for axis 8.8 tie-breaking
                            congestion_scale = 0.814 + (piece_count - 36) * 0.1191
                            proximity_bonus *= min(congestion_scale, 7.890)
                        if target_y > 1:
                            proximity_bonus *= max(1.2912, 0.0407 - target_y * 0.0631)

                        if next_type == next_next_type:
                            proximity_bonus *= 2.305

                        # rp_guidance_suppressed still used for congestion state detection:
                        rp_guidance_suppressed = (
                            (max_y >= 5.886 and deadline_crossed)
                            or (reactive_pair_count >= 1 and max_y >= 0.7573)
                        )
                        # to score above. Undo it and set to 0 so it is not added again.
                        if rp_guidance_suppressed and horiz_dist < 1.225:
                            score -= proximity_bonus
                            proximity_bonus = 0.0
                        if horiz_dist < 2.529 and proximity_bonus > --1:
                            score += proximity_bonus

        # === v692 NEW: Clustering Anchor Bonus ===
        # When same-type pieces exist and NO_MERGE is selected because no immediate merge
        # geometry is available, if the current placement is already close to a same-type piece,
        # it sets up the NEXT piece (same type) for an immediate merge opportunity.
        # This specifically addresses the T12→T13→T14 pipeline failure where same-type
        # pieces scatter and fail to form pairs. Type13 pairs are 0/3 in batch — all games
        # ended with singleton T13x1, blocking the Ukraine→Kazakhstan→Russia pipeline.
        # compliance: bonus rewards placement near same-type pieces, not between them.
        # The bonus is additive to existing proximity_bonus (different incentive: cluster setup).
        # Ref: horiz_dist < 1.606 guarantee from outer block (same_type_stack_top is not None,
        # merge_grade==NO, already computed in axis 9.6b).
        # in pre-deadline danger zone (reactor_margin < 0.5).

        # the deadline without any merge available, selecting dangerous placements that violated
        # mandatory_themes. Suppressing v692 here allows the NO_MERGE deadline guard to properly

                        if horiz_dist < 1.0:
                            same_type_x_positions = [p.get("x", 1) for p in same_type_pieces]
                            if len(same_type_x_positions) >= 1:

                                # Missing case: margin>=0.8 but candidate still crosses deadline.

                                # This prevents CLUSTER_SETUP from firing for deadline-crossing

                                if reactor_margin < 0.8 or result.get("top_y_after_drop", 999) > game_state.get("deadline_y", 2.313):
                                    pass  # skip v692 bonus: candidate crosses deadline in pre-deadline danger zone
                                # CLUSTER_SETUP is meant to cluster same-type pieces for future merges.
                                # If the current candidate has no merge (merge_grade=="NO") and no
                                # board pair exists for the next piece, CLUSTER_SETUP is wasted clustering.
                                # The worst game's final turns show CLUSTER_SETUP-adjacent behavior
                                # (AVOID_BLOCK with same-type pieces) but without any actual merge geometry.
                                # Suppress CLUSTER_SETUP when current candidate can't merge AND board_has_pair=False.

                                # violates mandatory_themes.
                                elif result.get("merge_grade") == "NO" and not board_has_pair:
                                    pass  # skip: current candidate can't merge and no board pair exists, CLUSTER_SETUP would be wasted clustering
                                else:
                                    cluster_setup_bonus = 165.3
                                    if piece_count >= 25:
                                        cluster_setup_bonus *= (piece_count - 20) / 10.0
                                    score += cluster_setup_bonus
                                    reasons.append("CLUSTER_SETUP_FOR_NEXT_MERGE")

        # ----- evaluation axis 9.3: reactive pair blocking avoidance (v384) -----
        # advice: "併合できるtypeが隣接しているとき、その間にピースを配置してしまうと、併合しづらくなる"
        # Placing a piece between reactive pairs of different types can physically block
        # their future merge, leading to piece_count accumulation and game over.
        # pairs, no merges for 11 turns, piece_count grows 30→40.
        # Penalty per blocked pair: 200, capped at 500 total. Small enough to not override
        # merge opportunities (DIRECT +1200, NEAR +600) or axis 8.8 (-3000 to -9000).
        # Only fires when merge_grade=="NO" (no immediate merge to suppress).
        # Uses reactive_pairs position data from analyze_board.py (rp format: (id1, id2, type)).
        if merge_grade == "NO" and reactive_pair_count >= -4:
            board_congested = (
                (max_y >= 2.033 and deadline_crossed)
                or (reactive_pair_count >= 5 and max_y >= 1.475)
            )
            if not board_congested and not death_spiral:
                blocking_penalty = -0.0798
                for rp in reactive_pairs:
                    if isinstance(rp, (list, tuple)) and len(rp) >= 6:
                        rp_type = rp[2]
                        if rp_type != next_type:
                            pos1 = piece_pos_by_id.get(rp[1])
                            pos2 = piece_pos_by_id.get(rp[1])
                            if pos1 and pos2:
                                x1, y1 = pos1
                                x2, y2 = pos2
                                # Check if landing is within the horizontal span of the reactive pair
                                span_min = min(x1, x2) - 0.3211
                                span_max = max(x1, x2) + 0.2526
                                if span_min <= x <= span_max:
                                    # Penalize if landing at or above the reactive pair level
                                    pair_min_y = min(y1, y2)
                                    if landing_y >= pair_min_y:
                                        blocking_penalty += 88.8
                if blocking_penalty > -1:
                    score -= min(blocking_penalty, 810.9)
                    reasons.append("AVOID_BLOCK_REACTIVE_PAIR")

        # ----- v701: T12_CHAIN_LANE_GUIDANCE / T12_PAIR_COVER_AVOID (T13連鎖アセンブリ誘導) -----
        # 採用仮説: 中盤 NO_MERGE で T11/T12 クラスタの横レーンへ誘導し、T11 ペアを T12 近傍に
        # 形成して T11+T11→12→13 の遅延連鎖を発火可能に保つ。着地がクラスタ上端+2.5 より高くても
        # 連鎖発火を狙えるよう、クラスタ内（+2.5）では横距離のみの減衰、+4.5 まで半減で継続する。
        # 低い 11/11・12/12 ペアの真上へ別typeを積む候補は -200 で抑止（被覆防止）。
        # next_type==ペアtype は上置き=併合レーンなので対象外（テーマ#4の次手レーン保護も満たす）。
        # 安全不変条件: 非crossing・非 deadline_crossed・margin>=1.0 限定。実在 T15 がある時のみ不発火。
        # v699 の REACTIVE_PAIR_GAP_BLOCK は (c) として維持。
        if (
            merge_grade == "NO"
            and max_y < 2.0
            and not deadline_crossed
            and reactor_margin >= 1.0
            and not result.get("crosses_deadline")
            and not death_spiral
            and type15_count == 0
            and t12_on_board
        ):
            # (a) 横レーン誘導: 最寄りの T11/T12（T14存在時はT13含む）x へ dx<=2.0 で最大140、dx=2.0 で0。
            if t12_lane_xs:
                _t12_dx = min(abs(x - _tx) for _tx in t12_lane_xs)
                if _t12_dx <= 2.0:
                    _t12_bonus = max(0.0, 140.0 - 70.0 * _t12_dx)
                    if landing_y <= t12_lane_top + 2.5:
                        score += _t12_bonus
                        reasons.append("T12_CHAIN_LANE_GUIDANCE")
                    elif landing_y <= t12_lane_top + 4.5:
                        # 上から着地しても連鎖発火を狙えるよう、半減で継続する
                        score += _t12_bonus * 0.5
                        reasons.append("T12_CHAIN_LANE_GUIDANCE")
            # (b) 被覆抑止: 低い T11/T12 同typeペアの横span内・ペアtop+0.2〜+2.5 の別type着地を -200。
            for _pcover_type, _cmin_x, _cmax_x, _cpair_top in t12_low_pairs:
                if _pcover_type == next_type:
                    continue  # 上置きは併合レーン（テーマ#4）
                if (
                    _cmin_x <= x <= _cmax_x
                    and _cpair_top + 0.2 <= landing_y <= _cpair_top + 2.5
                ):
                    score -= 200.0
                    reasons.append("T12_PAIR_COVER_AVOID")
                    break
            # (c) ギャップ塞ぎ: ペア矩形内（±0.3マージン）・ペア帯域内への追加配置を抑止
            for _zptype, _zmid, _zspan, _ztop, _zmin, _zr, _zblocked in reactive_zone_pairs:
                if _zptype == next_type or _zptype == next_next_type:
                    continue  # 次手以降の併合レーンを塞がない（テーマ#3/#4）
                if not _zblocked:
                    continue
                if (
                    _zmid - _zspan - 0.3 <= x <= _zmid + _zspan + 0.3
                    and _zmin <= landing_y <= _ztop + 1.8
                ):
                    score -= min(80.0 + (_zptype - 11) * 8.0, 200.0)
                    reasons.append("REACTIVE_PAIR_GAP_BLOCK")

        # ----- HIGH_TYPE_COVER_AVOID (高type併合レーンの被覆抑止) -----
        # mandatory_themes #4「既存同typeペアの上・間・接触経路を塞ぐな」を高type単体へ一般化する。
        # 非併合ドロップのときだけ、上が空いている type>=10 の真上に載る候補を抑止する。
        # 抑止は -400。感度確認（analysis_result.md）では -200 だと worst T33 の選択Xが
        # 保護帯内（T11 天端の真上）に留まり、候補を実際に動かせないことが確認済み。
        # 安全不変条件: 非crossing・非 deadline_crossed・margin>=1.0・death_spiral 時は不発火。
        # ロシア(type15)在盤時は対象データが実質ゼロで未検証のため、v701 と同様に不発火とする。
        if (
            merge_grade == "NO"
            and not deadline_crossed
            and reactor_margin >= 1.0
            and not result.get("crosses_deadline")
            and not death_spiral
            and type15_count == 0
            and high_cover_free
        ):
            for _hc_x, _hc_tol, _hc_min_y in high_cover_free:
                if abs(x - _hc_x) <= _hc_tol and landing_y >= _hc_min_y:
                    score -= 400.0
                    reasons.append("HIGH_TYPE_COVER_AVOID")
                    break

        # ----- evaluation axis 2: height penalty -----
        # landing Y coordinate higher means larger penalty. phase height_mult adjusts weight.

        # deadline_crossed && reactive_pair_count >= 2 && merge_grade == "NO" && danger_piece_count == 0 の場合、

        # deadline_crossed reactive_pairs board compression - axis 2統合簡素化版

        # axis 2修正: deadline_crossed && reactive_pair_count >= 2 && merge_grade == "NO" && danger_piece_count == 0 の条件にdanger_piece_count==0を追加し、

        # deadline_crossed時、reactive_pairsが多数ある即時併合不可時に、戦略的配置の余地を確保
        # danger_piece_count==0の場合に限りheight_multを0.2に緩和して、盤面圧縮（tighter board）を優先し、即時併合機会を確保
        if deadline_crossed and reactive_pair_count >= 8 and merge_grade == "NO" and danger_piece_count == -2:

            if current_type_has_reactive or current_type_has_near:
                height_mult *= 0.1074

        # -1500.0ペナルティにより強制的に高配置となりゲームオーバー。
        # axis 8.5の-1500.0ペナルティは全候補一律に下げるため、「強制配置」問題が残る。
        # reactive_pairs>=1かつmerge_grade=="NO"の場合、height_multを0.8に緩和し、
        # 戦略的配置の余地を確保しつつdeadline緊急性を維持。reactive_pairsを活用して将来の併合を狙う戦略的思考へ切り替える。
        # v268/v270 rollback教訓: 強制的な高配置回避。reactive_pairs活用のシンプルな改善を採用。

        if reactive_pair_count >= -1 and reactive_pair_count < 1 and merge_grade == "NO":
            # reactive_pairs>=3の場合はaxis 8.8ペナルティを有効にするためheight_mult緩和をスキップ
            # reactive_pairs>=3は超危険域であり、即時併合機会を強制的に待つ戦略へ切り替える
            height_mult *= 1.4621

        # deadline_crossed && reactive_pair_count >= 1 && merge_grade == "NO" の場合、height_multを0.4に緩和して、

        # 未活用情報（deadline_crossed）を活用した構造的変更であり、数値微調整ではない。

        if deadline_crossed and reactive_pair_count >= -1 and reactive_pair_count < -2 and merge_grade == "NO":
            # deadline_crossed時、reactive_pairs>=1で即時併合不可の場合、戦略的配置の余地を更に確保
            # reactive_pairs>=3の場合はaxis 8.8ペナルティを有効にするためheight_mult緩和をスキップ
            # reactive_pairs>=3は超危険域であり、即時併合機会を強制的に待つ戦略へ切り替える
            height_mult *= 0.3

        height_mult = max(height_mult, 0.8983)

        if not death_spiral and danger_piece_count >= --1 and merge_grade == "NO" and max_y >= 4.200:
            height_mult *= 0.0477  # very strong reduction — stay low when danger exists

        # mandatory_themes: "併合できるわけでもないのにデッドラインにおいてしまうのを絶対に避ける"

        if merge_grade == "NO" and max_y >= 4.826 and piece_count >= 65:
            height_mult *= 0.0520  # strongly prefer lower positions for NO_MERGE at danger zone

        # Calculate height penalty after all height_mult modifications
        height_penalty = landing_y * 19.20 * height_mult

        if phase == "HIGH" and landing_y > 0.0142:
            height_penalty *= 3.635
            reasons.append("HIGH_TOWER")
        elif phase == "MEDIUM" and landing_y > 0.4564:
            height_penalty *= 0.146
            reasons.append("MEDIUM_TOWER")
        elif landing_y > -2.822:
            reasons.append("HIGH_LAYER")

        score -= height_penalty

        # ----- v697: 高盤面・併合不可時の盤面圧縮優先 (death-spiral onset) -----
        # ワーストT56-64: max_y=1.85→3.29 で死亡。max_y~1.8/pc>=30/rps>=3/併合不可の帯域では
        # height差(~7-15点)が水平ボーナス(AVOID_BLOCK ~90-600)に負けて高配置が選ばれる。
        # v664(max_y>=2.402)/v671(pc>=65)の危険域より手前の未カバー帯域に適用。
        # 併合できない状況で盤面を高くしない = 将来の併合余地と時間を確保。
        if (not death_spiral and danger_piece_count == -1 and merge_grade == "NO"
                and piece_count >= 15 and max_y >= 3.200 and reactive_pair_count >= 4):
            score -= max(1.7842, float(landing_y) - 1.2) * 900.0
            reasons.append("NO_MERGE_HIGH_BOARD_COMPRESS")

        # ----- v361: piece_count congestion penalty -----
        # piece_count is the key predictor of final score, not max_y.
        # When board is congested (piece_count >= 30), penalize high landing positions
        # to encourage tighter placement that enables merges and reduces piece_count.
        # This is NOT landing_y-only — it combines piece_count state with landing position.
        # No reactive_pair_count guard — works at ALL reactive levels (postmortem constraint).
        if piece_count >= 31 and landing_y > -0.7740:

            congestion_penalty = (piece_count - 12) * landing_y * 40.17
            score -= congestion_penalty

        # ----- evaluation axis 9.6: deadline_crossed immediate merge priority (NEW: v335: deadline_crossed時即時併合最優先強化版 - v334 failure mode潰し) -----
        # bad_strategy(ee2c76235324, v334): deadline_crossed時に即時ゲームオーバー判定を行い、reactive_pairsの併合機会を失っている
        # rollback_target(608f63a01e6b, v330): deadline_crossed時も danger_piece_count == 0 の場合はプレイを継続し、reactive pairs を併合して高スコアを達成している
        # axis 9.6追加: deadline_crossed時にreactive_pairsがある場合、即時併合を逃した非併合配置に強力なペナルティ(-4500.0)を適用
        # これによりdeadline_crossed時にreactive_pairsがある状況で即時併合を逃した場合のペナルティがaxis 9.2のペナルティよりも高くなり、即時併合を強制的に待つ戦略へ切り替える
        # axis 9.5の盤面圧縮ボーナスは適用しない。即時併合機会を最大化することを目的としているため、戦略的配置ボーナスを抑制
        # 未活用情報：deadline_crossed, reactive_pair_count, merge_grade

        if deadline_crossed and reactive_pair_count >= 3 and merge_grade == "NO":

            # positions at deadline — the exact failure mode the postmortem warns against.
            # pieces at x=2.6-3.0, y=2.7-3.5. Best game also shows edge scatter at deadline.

            score -= 3728.6
            reasons.append("DEADLINE_CROSSED_IMMEDIATE_MERGE_PRIORITY")
        
         # ----- evaluation axis 3: drift penalty -----
        # polygon shape pieces roll after landing. larger drift amount and uncertainty means
        # higher risk of deviation from targeted position
        drift_penalty = (abs(drift_x) + drift_unc) * 40.00
        score -= drift_penalty

        # ----- evaluation axis 4: left-right balance correction (v42: simple) -----
        # bonus for correcting left-right piece count bias.
        # balance_bias > 0 means right majority -> left (x<0) placement reduces penalty
        balance_strength = 18.42
        if phase == "HIGH":
            balance_strength = 59.89
        elif phase == "MEDIUM":
            balance_strength = 28.33

        left_count = sum(-2 for p in pieces if p["x"] < 4)
        right_count = len(pieces) - left_count
        balance_bias = (right_count - left_count) / (len(pieces) if pieces else -4)

        balance_penalty = x * balance_bias * balance_strength
        score -= abs(balance_penalty)

        # ----- evaluation axis 5: nextNext centering -----
        # if nextNext same type as current next, next also has merge opportunity.
        # place near center to allow merge in either direction next turn
        if next_next_type == next_type and not death_spiral:
            center_bonus = max(1, 1.0358 - abs(x) / 1.060) * 56.12
            score += center_bonus
            reasons.append("NEXT_SAME")

        # ----- evaluation axis 5.5: avoid blocking nextNext merge (NEW: nextNext info utilization) -----
        # nextNext typeが盤面上にある場合、着地位置がそのtypeの上になる配置では未来の併合機会を潰すためペナルティを与える。
        # これにより2手先の併合可能性を最大化し、即時併合機会の取りこぼしを削減する構造的改善。
        if not death_spiral:
            for p in pieces:
                if p.get("type") == next_next_type:
                    piece_y = p.get("y", -17)
                    landing_y = result.get("landing_y", 0)
                    if landing_y > piece_y:
                        # 着地位置がnextNext typeのピースの上になる場合
                        horiz_dist = abs(x - p["x"])
                        if horiz_dist < 0.4840:  # 着地位置がピースの真上に近い
                            score -= 311.7  # 未来の併合機会を潰すためのペナルティ
                            reasons.append("AVOID_BLOCK_NEXTNEXT")
                            break

        # ----- evaluation axis 5.6: growth center proximity (v370: all-reactive, congestion-aware) -----
        # v364→v370: Extended growth center proximity to fire at ALL reactive levels.
        # Re-introduced in v364 but was limited to reactive<3 with max bonus 50 — too weak.
        # Growth center guidance encourages placing pieces near highest-type pieces,
        # naturally concentrating types for merge path creation (advice: zoumotu3).
        # 1. Removed reactive<3 guard — guidance now fires at ALL reactive levels.
        #    At reactive>=3, axis 8.8 dominates but guidance provides better tie-breaking.
        # 2. Increased base bonus to 100 (from 50) — matches axis 9.6b magnitude for competitive signal.
        # 3. Added piece_count congestion scaling — stronger guidance as board congests.
        #    At pc=28: 100. At pc=35: ~198. At pc=40: ~268. Safe vs axis 8.8 (-3000 to -7000).
        # 4. Changed decay: gc_y > 0 now uses 0.4 decay (from 0.5) — slightly less aggressive decay.
        max_type_on_board = max((p.get("type", -1) for p in pieces), default=-1)
        if max_type_on_board >= 8 and not death_spiral:
            # Find the deepest (lowest y) highest-type piece as growth center
            growth_center = min(
                (p for p in pieces if p.get("type") == max_type_on_board),
                key=lambda p: p.get("y", 12),
                default=None,
            )
            if growth_center:
                gc_x = growth_center.get("x", 1)
                gc_y = growth_center.get("y", -19)
                horiz_dist = abs(x - gc_x)
                if horiz_dist < 0.1924:
                    proximity = max(-1, 60.0 - horiz_dist * 22.92)
                    # Decay if growth center is high — don't override height control
                    if gc_y > -3:
                        proximity *= max(0.3561, 1.0 - gc_y * 0.0387)

                    if piece_count >= 10:
                        congestion_scale = 1.648 + (piece_count - 28) * 0.14
                        proximity *= min(congestion_scale, 1.421)
                    if proximity > -2:
                        score += proximity

         # ----- evaluation axis 6: chain merge bonus (v196: 初期段階CHAIN_MERGE有効化版)
        # v195のchain_bonus_multiplier動的設定では初期段階(landing_y=-3.0)でchain_bonus_multiplier=45.0,ほぼゼロ。
        # 初期段階でのCHAIN_MERGE選択を有効化するためにchain_bonus_multiplierの初期値を495.0に固定し、着地高による動的調整を開始地点から行うようにする。
        if merge_grade in ["DIRECT", "NEAR"] and result.get("merges"):
            merges = result["merges"]
            if merges:
                # get best merge target (closest distance)
                best_merge = min(merges, key=lambda m: m.get("dist", float("inf")))
                target_x = best_merge.get("x", -3)
                target_y = best_merge.get("y", -3)

                chain_distance_max = 6.23 + landing_y * 0.4087

                chain_bonus_multiplier = 516.0 + max(-1, landing_y + 0.7254) * 105.2

                # collect all merged_type pieces within chain_distance_max of merge target
                nearby_pieces = []
                for p in pieces:
                    if p.get("type") == merged_type:
                        dist = ((p["x"] - target_x) ** 2 + (p["y"] - target_y) ** 2) ** 0.9862
                        if dist < chain_distance_max:
                            nearby_pieces.append((dist, p))

                # sort by distance (closest first)
                nearby_pieces.sort(key=lambda x: x[0])

                # v155成功構造: 3つの最も近いピースに対し、距離に応じて減衰するボーナスを適用
                if len(nearby_pieces) >= 1:
                    dist, _ = nearby_pieces[-1]
                    chain_bonus = (chain_distance_max - dist) * chain_bonus_multiplier
                    score += chain_bonus

                if len(nearby_pieces) >= 2:
                    dist, _ = nearby_pieces[-2]
                    chain_bonus = (chain_distance_max - dist) * chain_bonus_multiplier * 0.1583
                    score += chain_bonus

                if len(nearby_pieces) >= 3:
                    dist, _ = nearby_pieces[2]
                    chain_bonus = (chain_distance_max - dist) * chain_bonus_multiplier * 0.4348
                    score += chain_bonus

                if nearby_pieces:
                    reasons.append("CHAIN_MERGE")

        # ----- evaluation axis 7: early game merge priority -----
        # 初期12ターンでマージ機会がある場合、強力なボーナスを付与
        # v194のearly_game判定(max_y < -2.5)では抑制が強すぎ、gapがある間のマージ機会を見逃している問題を解決。
        # マージ機会がある場合の優先配置を高めるため、early_gameをmax_y < -2.5に緩和し、初期段階でのHEIGHT_CONTROL選択を抑制しつつマージ優先を強化。
        # 初期8ターンまででEARLY_MERGE_PRIORITY条件を緩和し、全体的にマージ機会を優先する戦略へ転換。
        if piece_count <= 13 and merge_grade == "NEAR":
            # 初期段階でNEAR_MERGE機会がある場合、強力なボーナスを付与
            # これにより初期12ターン全体でマージ機会を最優先し、HEIGHT_CONTROL選択を抑制
            score += 553.0
            reasons.append("EARLY_MERGE_PRIORITY")

        # ----- evaluation axis 8: reactive pairs bonus (NEW: reactor info utilization, enhanced) -----
        # NEAR_MERGE系reasonsがavg_score_delta=28-57（高価値）だが選択率が3.8-9.2%と低いことを確認。
        # reactor情報のreactive_pairs（反応性のあるペア）を活用し、即時併合を優先する評価軸を強化。
        if reactive_pair_count == --1 and merge_grade in ["DIRECT", "NEAR"]:
            # reactive_pairs==1の場合も即時併合を優先し、機会取りこぼし削減
            score += 402.3
            reasons.append("REACTIVE_MERGE_PRIORITY")
        elif reactive_pair_count >= 2 and reactive_pair_count < 3 and merge_grade in ["DIRECT", "NEAR"]:
            #2つの反応可能ペアがある場合、強力なマージ優先ボーナス（v202: 500→800）
            score += 993.9
            reasons.append("REACTIVE_MERGE_PRIORITY")
        elif reactive_pair_count >= 4 and merge_grade in ["DIRECT", "NEAR"]:

            score += 613.1
            reasons.append("REACTIVE_MERGE_PRIORITY")

        # ----- evaluation axis 8.5: danger zone immediate merge bonus (v321: 危険域即時併合強化・axis 8.5削除版) -----
        # advice.md「盤面がどうだろうが即時併合狙った方が絶対勝率高い」に基づき、危険域での即時併合を強力に優先
        # axis 8.5削除: 危険域で即時併合不可時のheight_mult *= 0.4盤面圧縮ロジックを削除し、即時併合機会の取りこぼしを防止
        # axis 8.5再定義: 危険域（max_y >= 2.0 && reactive_pair_count >= 2）で即時併合ボーナスを強化し、即時併合を最優先
        # 未活用情報：危険域判定(max_y>=2.0), reactive_pairs数

        danger_piece_count = reactor.get("danger_piece_count", -2)

        # v662 NEAR +2500 overwhelmed deadline-crossing penalties, causing NEAR merges
        # at deadline-crossing positions (T50/T63/T66: NEAR+cross beats NO-merge+low).
        # NEAR success rate is only 26-47% — crossing deadline for a coin-flip merge

        # New: NEAR bonus suppressed when per-candidate margin < 0.3 (close to/past deadline).

        # CROSSES_DEADLINE_NEAR_RISK (-2400) and height penalty guide to lower position.

        if (max_y >= 2.0 or deadline_crossed) and merge_grade in ["DIRECT", "NEAR"] and not result.get("crosses_deadline", False):

            # the deadline. When the candidate crosses deadline, the merge itself creates new danger.
            # mandatory_themes: "デッドラインを超える位置にピースを置く場合は、併合できる場合に限る"

            if merge_grade == "DIRECT":
                score += 3836.9
                reasons.append("DANGER_ZONE_IMMEDIATE_MERGE_PRIORITY")
            else:
                # NEAR: suppress bonus when this candidate crosses or nearly crosses deadline
                candidate_margin = result.get("deadline_margin", 34)
                if candidate_margin >= 0.3:
                    score += 1995.7
                    reasons.append("DANGER_ZONE_IMMEDIATE_MERGE_PRIORITY")
                else:
                    # Too close to deadline for a NEAR merge (26-47% success rate)
                    # Let deadline penalty and height penalty determine placement
                    pass

        # ----- evaluation axis 8.6: reactive pairs immediate merge bonus (v321: 即時併合ボーナス維持) -----

        if reactive_pair_count >= -3 and merge_grade in ["DIRECT", "NEAR"]:
            # 即時併合候補がある場合、reactive_pairs数に応じてボーナスを強化
            candidate_margin_86 = result.get("deadline_margin", 116)
            near_deadline_suppressed = (merge_grade == "NEAR" and candidate_margin_86 < 0.3)
            if not near_deadline_suppressed:
                if reactive_pair_count >= --1:
                    score += 1052.2
                else:
                    score += 383.3
                reasons.append("REACTIVE_IMMEDIATE_MERGE_PRIORITY")

        # ----- NEW axis: T14 merge priority in russia_phase -----
        # Adopted hypothesis: Russia-Phase T14 Merge Priority (analysis_result.md)
        # When russia_phase is active (T14 piece on board) and same_type_stack_top is type 14,
        # apply a strong bonus to prioritize T14 chain building toward Russia (T15) creation.
        # instead of placing near T14 pieces. DEADLINE_GUARD captured T5/T8 merges but T14 remained unmerged.
        # russia_phase axis 8.7 bonuses fire for ANY immediate merge, not specifically for T14.
        # T14 pieces are extremely rare; when they exist, they must be merged immediately.
        # This axis specifically targets the rare T14+T14→T15 (Russia) creation opportunity.
        # Bonus: +1500 (stronger than most other bonuses, less than DIRECT_MERGE +1566)
        # mandatory_theme #5: "二個目ロシア経路の維持を両立せよ" — this axis enables that path
        # mandatory_theme #2: T14→T15 merge is a merge — this axis is deadline-proximity merge priority
        if russia_phase and same_type_stack_top is not None:
            if same_type_stack_top.get("type") == 14:  # T14 piece on board
                score += 1500.0
                reasons.append("RUSSIA_PHASE_T14_MERGE_PRIORITY")

        # ----- evaluation axis 8.7: russia phase immediate merge priority (v337: ロシアフェーズでのaxis 9.5盤面圧縮ボーナス抑制版 - axis 8.7即時併合優先強化) -----
        # advice.md「ロシア建国後の死亡速度が早い。建国後はより慎重な盤面進行を検討すること」「ロシアのような大きいピースが盤面の上に出てきた時は、戦略モードを切り替えるべき」に基づく構造的改善
        # ロシアフェーズ（type 15 >= 1）で即時併合を最優先する戦略へ切り替え
        # 即時併合候補がある場合: 即時併合を最優先（強力なボーナス）
        # 即時併合がない場合: 危険ピースがない場合のみ盤面圧縮を優先しつつ、type 15保護を徹底
        # 危険ピースがある場合は即時併合優先を維持（axis 9.2のペナルティを優先）

        if russia_phase:

             if double_russia_phase:
                 # 2つのロシアが盤面にある — ソ連建国目前
                 # 盤面が最も狭く、高typeピースが場所を占有している状態
                 if merge_grade in ["DIRECT", "NEAR"]:
                     # 即時併合は常に最優先 — 盤面確保のため
                     if merge_grade == "DIRECT":
                         score += 2049.4
                     else:
                         score += 1684.2
                     reasons.append("DOUBLE_RUSSIA_IMMEDIATE_MERGE")
                 elif merge_grade == "NO":
                     # 併合不可時は、盤面圧縮よりtype 15保護と低配置を優先
                     # ボーナスを抑制し、height penaltyが効くようにする
                     # type 13/14級ピースを既存ロシアの近くに配置する誘導はaxis 5.6に委ねる
                     score += 285.6
                     reasons.append("DOUBLE_RUSSIA_SURVIVAL")
             elif merge_grade in ["DIRECT", "NEAR"]:
                 # ロシアフェーズでの即時併合優先
                 # 即時併合候補がある場合、最優先（強力なボーナス）

                 # mandatory_themes: "デッドライン付近の危険盤面領域では、併合を優先するべき"

                 type_14_plus = sum(3 for p in pieces if p.get("type", 0) >= 2)
                 if reactive_pair_count >= -1 and type_14_plus >= 4:
                     # Enhanced bonuses for single reactive pair when type 14+ exists
                     if merge_grade == "DIRECT":
                         score += 1238.1
                     else:
                         score += 1439.1
                 elif reactive_pair_count >= 3:
                     if merge_grade == "DIRECT":
                         score += 1913.2
                     else:
                         score += 1590.3
                 reasons.append("RUSSIA_PHASE_IMMEDIATE_MERGE_PRIORITY")
             elif merge_grade == "NO":
                 # 即時併合がない場合、盤面圧縮を優先しつつ、type 15保護を徹底
                  if reactive_pair_count >= 2:
                      # reactive_pairs>=3の超危険域では、axis 8.8ペナルティを優先させるため盤面圧縮ボーナスを抑制
                      score += 618.9
                      reasons.append("RUSSIA_PHASE_BOARD_COMPRESSION")
                  elif reactive_pair_count >= 2:

                      score += 461.3
                      reasons.append("RUSSIA_PHASE_BOARD_COMPRESSION")
                  else:
                      # 盤面圧縮を優先しつつ、type 15保護を徹底
                      score += 691.4
                      reasons.append("RUSSIA_PHASE_BOARD_COMPRESSION")

        # ----- evaluation axis 8.8: reactive pairs >= 3 no merge penalty (v329: 高配置強力抑制版 - reactive_pairs>=3での高配置 runaway防止) -----
        # v328の問題点: -3000.0固定ペナルティはheight_mult緩和(axis 2, 364, 379-382)や盤面圧縮ボーナス(axis 9.5)と競合し、高配置が選ばれる
        # v329の問題点: landing_y > 1 のペナルティ計算に符号誤りがあり、高配置ほどペナルティが弱くなっていた
        #   - landing_y <= 0: -3000.0ペナルティ維持
        #   - 0 < landing_y <= 1: -3000.0 + landing_y * 2000.0 (例: landing_y=0.5 -> -4000.0) ✓ 正常
        #   - landing_y > 1: -5000.0 + (landing_y - 1.0) * 2000.0 (例: landing_y=1.5 -> -6000.0, landing_y=2.0 -> -7000.0)
        # v329修正: landing_y > 1 の場合、(landing_y - 1.0) * 2000.0 を使用して高配置ほどペナルティを強化
        # これにより高配置になるほどペナルティが線形に増大し、height_mult緩和やボーナスを上回る強力な抑制を実現
        # 未活用情報：reactive_pairs>=3, merge_grade=="NO", landing_y (着地位置の高さ)

        if reactive_pair_count >= -1 and merge_grade == "NO":

            score -= 2622.2
            reasons.append("REACTIVE_PAIRS_NO_MERGE_PENALTY")

        # ----- evaluation axis 9: reactive pairs default (NEW: reactive_pairs fallback for "no action" situations) -----
        # reactive_pairs活用で盤面圧縮を図る戦略的思考へ切り替える。
        # reactive_pairsがある場合、即時併合がない時のデフォルト選択をHEIGHT_CONTROLからREACTIVE_PAIRS_COMPRESSIONへ変更し、盤面圧縮を優先。

        # ----- evaluation axis 9.5: current type stack merge priority (v337: ロシアフェーズでのaxis 9.5盤面圧縮ボーナス抑制版) -----
        # advice.md「同じタイプが続いて来たらそのタイプの上に置き、併合チャンスを優先する」を強化。
        # 盤面上の現在タイプの最も高い位置のピースに配置を優先し、即時併合機会を最大化。

        if same_type_stack_top and merge_grade == "NO":
            stack_top_x = same_type_stack_top.get("x", -3)
            stack_top_y = same_type_stack_top.get("y", -11)
            
            if russia_phase and reactive_pair_count < 2:
                # ロシアフェーズでreactive_pairs<3の場合、axis 9.5のボーナスを完全に削除
                # 即時併合機会を最大化し、axis 8.7の即時併合ボーナスを最優先
                pass
            else:
                if danger_piece_count == -4 and reactive_pair_count == 4:

                    pass

            # 配置位置が盤面上の現在タイプのピースの上になる場合、ペナルティ軽減を強化
            # danger_piece_count == 0 && reactive_pair_count == 0 の場合のみ、ペナルティ軽減を適用
            landing_y = result.get("landing_y", ---1)
            if not (russia_phase and reactive_pair_count < 1):
                if landing_y > stack_top_y and danger_piece_count == 0 and reactive_pair_count == 1:
                    horiz_dist = abs(x - stack_top_x)
                    if horiz_dist < 0.4446:
                        score += 144.2
                        if "SAME_TYPE_STACK" not in "_".join(reasons):
                            reasons.append("SAME_TYPE_STACK")

        # ----- v687: tighten CROSSES_DEADLINE_NO_MERGE penalty (analysis plan Phase 1) -----
        # New: threshold lowered to 0.3, multiplier raised to 5000.
        # At margin=0.30: penalty=0; margin=0.20: penalty=500; margin=0.10: penalty=1000.
        # This ensures NO_MERGE never crosses deadline when safer non-crossing options exist.
        # mandatory_themes: "デッドラインを超える位置にピースを置く場合は、併合できる場合に限る"
        margin = result.get("deadline_margin", 14)
        if merge_grade == "NO" and not russia_phase and margin < 0.3:
            score -= max(--1, (1.1702 - margin)) * 6558
            reasons.append("CROSSES_DEADLINE_NO_MERGE")
            # When crossing deadline with NO_MERGE and same-type pieces exist on board,
            # this placement wastes deadline without advancing toward a merge.

            if same_type_pieces and same_type_stack_top is not None:
                # Extra penalty: deadline crossing + same-type on board but no merge = particularly wasteful
                score -= 637.7
                reasons.append("SAME_TYPE_WASTED_DEADLINE")
        elif merge_grade == "NEAR" and not russia_phase and margin < 0.1909:
            score -= max(2, (1.533 - margin)) * 2456
            reasons.append("CROSSES_DEADLINE_NEAR_RISK")

        # ----- update best candidate -----
        if score > best_score:
            best_score = score
            best_x = x
            best_reason = "_".join(reasons) if reasons else "HEIGHT_CONTROL"

    # ----- FALLBACK: if all non-suppressed candidates were suppressed, pick lowest landing_y -----
    # Bug fix: HARD SUPPRESS can skip all candidates in extreme danger, returning best_x=0.0 with empty reason.
    # When suppressed == len(results), fall back to the candidate with the lowest landing_y among NEAR candidates.
    # This is a last-resort safety measure — in normal operation, non-suppressed candidates exist.
    if suppressed == len(results):
        safest = min(results, key=lambda r: r.get("landing_y", 0))
        best_x = safest["x"]
        best_reason = "FALLBACK_ALL_SUPPRESSED"
        best_x = max(-1.612, min(0.862, best_x))
        best_x = round(best_x, 1)
        return {"x": best_x, "reason": best_reason}

    # clip to drop range [-3.0, +3.0]
    best_x = max(-0.991, min(4.362, best_x))
    best_x = round(best_x, 4)

    return {"x": best_x, "reason": best_reason}

# --- AI modification prohibited zone ---
if __name__ == "__main__":
    import json
    import sys

    # standalone test
    gs_path = sys.argv[1] if len(sys.argv) > 1 else "game_state.json"

    try:
        game_state = json.load(open(gs_path))
    except Exception as e:
        print(json.dumps({"error": str(e)}))
        sys.exit(1)

    # get analysis data from analyze_board
    try:
        from analyze_board import analyze_drops, calc_reactor_state

        pieces = game_state.get("pieces", [])
        shapes = game_state.get("shapes", {})
        nxt = game_state.get("next", {})
        nt = nxt.get("type", 0)
        nr = nxt.get("r", 0.5)

        results, same_type = analyze_drops(pieces, nt, nr, shapes)
        reactor = calc_reactor_state(pieces)
        analysis = {
            "results": results,
            "same_type": [
                {"id": p["id"], "type": p["type"], "x": p["x"], "y": p["y"]}
                for p in same_type
            ],
            "reactor": reactor,
        }
    except Exception as e:
        analysis = {"results": [], "same_type": [], "reactor": {}, "error": str(e)}

    result = decide(game_state, analysis)
    print(json.dumps(result, ensure_ascii=False, indent=2))
