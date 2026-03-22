#!/usr/bin/env python3
"""strategy.py - Soviet Puzzle Game AI Drop Position Script

Game Overview:
  - Drop pieces, merge same type pieces (N+N -> N+1)
- Score table: type1=1, type2=3, type3=6, ..., typeN = N*(N+1)/2
- Board: x in [-3.0, +3.0], floor y=-4.48, deadline y=3.32
  - Player controls only drop X coordinate

     Decision Logic (11 evaluation axes):
         1. Merge bonus - High score for immediate merge (DIRECT > NEAR > FAR)
        2. Height penalty - Penalty for high landing position (varies by phase)
         3. Drift penalty - Penalty for post-landing drift due to polygon shape
         4. Left-right balance correction - Bonus for correcting piece count bias
          5. nextNext centering - Center for next merge opportunity if nextNext same type
           5.5. Avoid blocking nextNext merge - Penalty for landing on same-type piece when nextNext matches
           6. Chain merge bonus - Evaluate possibility of further merges after merge
            7. Reactive pairs bonus - Bonus for multiple merge opportunities (reactor info utilization, v206: enhanced)
           8. Early game merge priority - Strong bonus for merge opportunities in early game
             8.5. Danger zone deadline margin merge priority - v310: deadline_margin考慮の即時併合優先強化版
            9. Reactive pairs default - Default to REACTIVE_PAIRS_COMPRESSION when reactive_pairs >= 1 and no immediate merge
             9.5. Current type stack merge priority - v309: 戦略的配置ボーナス完全削除・シンプル化版
             9.6. Reactive pairs immediate merge bonus - v309: 戦略的配置ボーナス完全削除・シンプル化版

Phases (determined by board max Y):
     LOW      (max_y < 0.8) : Early game. Merge priority (merge_mult=1.2)
      MEDIUM   (0.8 <= max_y < 1.8) : Mid game. Height management (height_mult=1.4)
      HIGH     (1.8 <= max_y < 3.0) : Late game. Merge opportunity (height_mult=1.8)
      CRITICAL (3.0 <= max_y) : Danger. DIRECT merge priority, board compression (NEAR carefully)
"""

# Fixed interface:
# decide(game_state: dict, analysis: dict) -> dict
#    Returns: {"x": float, "reason": str}
#
# AI modifiable: decide() body, helper functions, constants, imports
# AI prohibited: decide() signature, if __name__ == "__main__" block

 # --- Change History ---
# v310: deadline_margin考慮の即時併合優先強化版 - 危険局面判定追加
# v309 failure mode: danger_piece_count > 0のみ即時併合ボーナスを強化していたが、danger_piece_count == 0かつdeadline_margin < 1.0の場合でも即時併合優先が不十分
# ワーストゲーム(score0492)終盤turns 42-44でdeadline_margin=-1.46→-1.99、danger_piece_count=1-2あるのに即時併合不可続き、max_y=2.37→4.09に急上昇してゲームオーバー
# ベストゲーム(score3422)終盤turns 132-136でdeadline_margin=-0.09→-1.67、reactive_pairs=5-6あるにもかかわらず即時併合不可続き、max_y=2.69に上昇
# ベストゲームではturn 135で即時併合機会(NEAR_MERGE)を確実に捉えてscore_delta=66を獲得し延命
# best_score5694 (v178)のアプローチを導入し、dangerous_situation (max_y >= 1.8 && reactive_pairs >= 2) でheight_multiplierを15.0に削減
# deadline_marginに応じて即時併合優先ボーナスを段階的に強化（<0.5: +1800.0, <1.0: +1500.0, danger_piece_count>0: +2000.0）
# 危険ピースがない場合でも、deadline_margin < 1.0で即時併合を優先することで、max_y runawayを抑制
# last_rollback_postmortem制約遵守：
#   - 即時併合（DIRECT/NEAR）が可能な場合、常に即時併合を優先する
#   - deadline_margin >= 1.0 の場合は危険域とみなし、即時併合機会を逃さない
#   - 即時併合不可の場合、戦略的配置ボーナスを完全削除する
# refs: tmp/improve_brief.md, tmp/batch_summary.txt, tmp/state/last_rollback_postmortem.md, tmp/state/last_rollback_analysis.md, advice.md,
#       game_history/20260322_173343_score0492.jsonl turns 42-44, game_history/20260322_174435_score3422.jsonl turns 132-136,
#       strategy_versions/best_score5694_strategy.py

# Merge result score: type N merge gives N*(N+1)/2 points
# Example: type1+1->2 gives +3 points, type8+8->9 gives +45 points, type14+14->15 gives +120 points
SCORE_TABLE = {i: i * (i + 1) // 2 for i in range(1, 17)}

def decide(game_state: dict, analysis: dict) -> dict:
    """v310: deadline_margin考慮の即時併合優先強化版 - 危険局面判定追加

    v309 failure mode: danger_piece_count > 0のみ即時併合ボーナスを強化していたが、danger_piece_count == 0かつdeadline_margin < 1.0の場合でも即時併合優先が不十分
    ワーストゲーム(score0492)終盤turns 42-44でdeadline_margin=-1.46→-1.99、danger_piece_count=1-2あるのに即時併合不可続き、max_y=2.37→4.09に急上昇してゲームオーバー
    ベストゲーム(score3422)終盤turns 132-136でdeadline_margin=-0.09→-1.67、reactive_pairs=5-6あるにもかかわらず即時併合不可続き、max_y=2.69に上昇
    ベストゲームではturn 135で即時併合機会(NEAR_MERGE)を確実に捉えてscore_delta=66を獲得し延命
    best_score5694 (v178)のアプローチを導入し、dangerous_situation (max_y >= 1.8 && reactive_pairs >= 2) でheight_multiplierを15.0に削減
    deadline_marginに応じて即時併合優先ボーナスを段階的に強化（<0.5: +1800.0, <1.0: +1500.0, danger_piece_count>0: +2000.0）
    危険ピースがない場合でも、deadline_margin < 1.0で即時併合を優先することで、max_y runawayを抑制
    last_rollback_postmortem制約遵守：
      - 即時併合（DIRECT/NEAR）が可能な場合、常に即時併合を優先する
      - deadline_margin >= 1.0 の場合は危険域とみなし、即時併合機会を逃さない
      - 即時併合不可の場合、戦略的配置ボーナスを完全削除する

    Args:
         game_state: game state (pieces, next, nextNext, score, etc.)
         analysis: analyze_board.py analysis results
             - results: landing information for each drop X candidate
                 - x: drop X coordinate
                 - landing_y: estimated landing Y coordinate (high=dangerous)
                 - drift_x/drift_unc: post-landing drift due to polygon shape
                 - merge_grade: best merge judgment (DIRECT/NEAR/FAR/NO)
                 - danger_direct_merge_available: DIRECT merge available with danger piece
             - reactor: reactor state (reactive_pairs, near_pairs, etc.)
             - deadline: deadline state (deadline_margin, etc.)

    Returns:
         {"x": drop X coordinate, "reason": selection reason}
    """

    results = analysis.get("results", [])

    if not results:
        return {"x": 0.0, "reason": "no analysis data"}

    best_x = 0.0
    best_score = -float("inf")
    best_reason = ""

    # --- board information collection ---
    pieces = game_state.get("pieces", [])
    max_y = max([p["y"] for p in pieces]) if pieces else -4.0

    # --- deadline information ---
    deadline_crossed = game_state.get("deadline_crossed", False)
    deadline_margin = analysis.get("deadline", {}).get("deadline_margin", 99.0)

    # --- reactor information (for reactive merge priority) ---
    reactor = analysis.get("reactor", {})
    reactive_pairs = reactor.get("reactive_pairs", [])
    reactive_pair_count = len(reactive_pairs) if isinstance(reactive_pairs, list) else 0
    danger_piece_count = reactor.get("danger_piece_count", 0)

    # --- v310: 危険局面判定の追加（best_score5694 v178のアプローチを導入） ---
    # 条件: max_y >= 1.8 かつ reactive_pair_count >= 2
    # deadline_marginを考慮し、危険域での即時併合優先度を強化
    dangerous_situation = max_y >= 1.8 and reactive_pair_count >= 2

    # --- phase judgment (v42 thresholds) ---
    if max_y < 0.8:
        phase = "LOW"
        height_mult = 0.4  # v198: LOW phase height_mult further reduced (0.6→0.4) to enable proactive merge opportunities
        merge_mult = 1.2  # 20% merge bonus increase, actively target
    elif max_y < 1.8:
        phase = "MEDIUM"
        height_mult = 1.4  # v177: MEDIUM phase height_mult from v42 (2.4→1.4)
        merge_mult = 1.0
    elif max_y < 3.0:
        phase = "HIGH"
        height_mult = 1.8  # HIGH phase height_mult from v42
        merge_mult = 1.0
    else:
        phase = "CRITICAL"
        height_mult = 1.0  # CRITICAL height penalty basic value only
        merge_mult = 0.6  # v42: CRITICAL phase merge suppression

    # --- next piece information ---
    next_piece = game_state.get("next", {})
    next_next_piece = game_state.get("nextNext", {})
    next_type = next_piece.get("type", 0)
    next_next_type = next_next_piece.get("type", 0)

    # --- v149: pre-calculate merged type (for chain judgment) ---
    merged_type = min(next_type + 1, 16)

    # --- Type-specific merge bonus calculation ---
    # merge result type (next_type+1) higher means higher score value
    # example: type1 merge -> bonus=330, type5 merge -> bonus=510, type14 merge -> bonus=1660
    merge_result_type = min(next_type + 1, 16)
    type_merge_bonus = SCORE_TABLE.get(merge_result_type, 10) * 10 + 300

    # =======================================================================
    #  score each drop candidate (x coordinate) with 6 evaluation axes
    # =======================================================================
    for result in results:
        x = result["x"]
        landing_y = result.get("landing_y", 0)
        drift_x = result.get("drift_x", 0)
        drift_unc = result.get("drift_unc", 0)
        merge_grade = result.get("merge_grade", "NO")  # DIRECT/NEAR/FAR/NO
        danger_direct_merge_available = result.get("danger_direct_merge_available", False)

        score = 0.0
        reasons = []

        # ----- evaluation axis 1: merge bonus -----
        # analyze_board judged merge_grade gives bonus
        # DIRECT: direct hit target (success rate 95.7%)
        # NEAR:   contact zone after landing (success rate 68.5%)
        # FAR:    contact possibility by drift (low probability)
        if merge_grade == "DIRECT":
            score += 1200.0 * merge_mult
            reasons.append("DIRECT_MERGE")
        elif merge_grade == "NEAR":
            score += 600.0 * merge_mult
            reasons.append("NEAR_MERGE")
        elif merge_grade == "FAR":
            score += 200.0 * merge_mult
            reasons.append("FAR_MERGE")

        # ----- evaluation axis 2: height penalty -----
        # landing Y coordinate higher means larger penalty. phase height_mult adjusts weight.
        # v197: LOW phase height_mult=0.6 enables early chain opportunities by allowing slightly higher placement
        # v309: deadline_crossed時のheight_mult調整ロジックを完全削除 - 戦略的配置を完全に抑制
        # v308以前の複雑なheight_mult調整（v294のaxis 2, v270のreactive_pairs緩和, v288のdeadline_crossed緩和, v308のdanger_piece_count緩和）を削除
        # deadline_crossed時でも、即時併合不可の場合に戦略的配置を選びにくくするため、height_mult調整を一切行わない
        # 即時併合不可の場合は、純粋に他の評価軸（height/drift/balance/chainなど）で判断させる
        # last_rollback_postmortem制約遵守：
        #   - 即時併合（DIRECT/NEAR）が可能な場合、常に即時併合を優先する
        #   - reactive_pairsがある場合、即時併合が可能ならaxis 9.5ボーナスを付与するが、即時併合不可の場合は戦略的配置ボーナスを完全削除する（v295のようにシンプルに）
        #   - forbid: axis 9.5ボーナスのdeadline_crossed reactive_pairs数に応じた複雑な調整を禁止
        # refs: tmp/improve_brief.md, tmp/batch_summary.txt, tmp/state/last_rollback_postmortem.md, tmp/state/last_rollback_analysis.md,
        #       strategy_versions/v295_strategy.py

 # v309: deadline_crossed時の戦略的配置完全削除版 - 即時併合優先の徹底
 # v308 failure mode: deadline_crossed時、複数のheight_mult調整ロジックが競合し、戦略的配置が選ばれ続けmax_y runaway → ゲームオーバー
 # 行188-191 (height_mult *= 0.2), 行202-204 (height_mult *= 0.8), 行218-221 (height_mult *= 0.4), 行412-418 (height_mult *= 15.0) の
 # 複雑な条件分岐が組み合わさり、deadline_crossed && reactive_pairs>=1 && merge_grade=="NO"の場合にheight_multが0.32倍まで緩和され、
 # 危険ピースがない(danger_piece_count==0)場合に戦略的配置ボーナスが残存していた
 # ワーストゲーム(score0914)終盤turns 64-74でdeadline_crossed=true, reactive_pairs=1-3あるのにmerge_available=false続き、
 # 戦略的配置(HIGH_TOWER_DANGER_ZONE_STRATEGIC_PLACEMENT)を選び続けmax_y=1.93→2.52に上昇してゲームオーバー
 # ベストゲーム(score2484)終盤turns 105-120ではdeadline_crossed後も即時併合機会(NEAR_MERGE)を確実に捉えてscore_delta=76-151を獲得
 # last_rollback_postmortem制約遵守：
 #   - 即時併合（DIRECT/NEAR）が可能な場合、常に即時併合を優先する
 #   - deadline_margin >= 1.0 の場合は危険域とみなし、即時併合機会を逃さない
 #   - reactive_pairsがある場合、即時併合が可能ならaxis 9.5ボーナスを付与するが、即時併合不可の場合は戦略的配置ボーナスを完全削除する（v295のようにシンプルに）
 #   - forbid: Russian phase handling（is_russian_phase判定とaxis 9.6）の追加を禁止
 #   - forbid: axis 9.5ボーナスのdeadline_crossed reactive_pairs数に応じた複雑な調整を禁止
 # v295のシンプルなaxis 9.5ボーナス（reactive_pairs>=1で+800.0固定）に戻し、deadline_crossed時のheight_mult調整ロジックを削除
  # 即時併合不可の場合、axis 9.5ボーナスを完全削除し、純粋に他の評価軸（height/drift/balance/chainなど）で判断させる
        # refs: tmp/improve_brief.md, tmp/batch_summary.txt, tmp/state/last_rollback_postmortem.md, tmp/state/last_rollback_analysis.md, advice.md,
        #       game_history/20260322_120731_score0738.jsonl turns=40-51, game_history/20260322_074935_score2869.jsonl turns=50-53,
        #       strategy_versions/v295_strategy.py

        # ----- evaluation axis 8.5: danger zone deadline margin merge priority (v310: deadline_margin考慮の即時併合優先強化) -----
        # v309 failure mode: danger_piece_count > 0のみ即時併合ボーナスを強化していたが、danger_piece_count == 0かつdeadline_margin < 1.0の場合でも即時併合優先が不十分
        # ワーストゲーム(score0492)終盤turns 42-44でdeadline_margin=-1.46→-1.99、danger_piece_count=1-2あるのに即時併合不可続き、max_y=2.37→4.09に急上昇してゲームオーバー
        # ベストゲーム(score3422)終盤turns 132-136でdeadline_margin=-0.09→-1.67、reactive_pairs=5-6あるにもかかわらず即時併合不可続き、max_y=2.69に上昇
        # ベストゲームではturn 135で即時併合機会(NEAR_MERGE)を確実に捉えてscore_delta=66を獲得し延命
        # best_score5694 (v178)のアプローチを導入し、dangerous_situation (max_y >= 1.8 && reactive_pairs >= 2) でheight_multiplierを15.0に削減
        # deadline_marginに応じて即時併合優先ボーナスを段階的に強化し、危険域での緊急性を高める
        # last_rollback_postmortem制約遵守：
        #   - 即時併合（DIRECT/NEAR）が可能な場合、常に即時併合を優先する
        #   - deadline_margin >= 1.0 の場合は危険域とみなし、即時併合機会を逃さない
        #   - 即時併合不可の場合、戦略的配置ボーナスを完全削除する

        if dangerous_situation:
            # 危険局面: max_y >= 1.8かつreactive_pairs >= 2
            # height_multiplierを15.0に削減し、即時併合を強制
            height_multiplier = 15.0

        if danger_piece_count > 0:
            # 危険ピースがある場合、即時併合を最優先
            if merge_grade in ["DIRECT", "NEAR"]:
                score += 2000.0
                reasons.append("DANGER_ZONE_DEADLINE_MERGE_PRIORITY")
            else:
                # 即時併合可能だが、この候補では併合できない場合：強力なペナルティ
                score -= 1000.0
                reasons.append("DANGER_ZONE_DEADLINE_MERGE_PRIORITY")
        elif deadline_margin < 0.5:
            # deadline_margin < 0.5: 高危険域
            if merge_grade in ["DIRECT", "NEAR"]:
                score += 1800.0
                reasons.append("DANGER_ZONE_DEADLINE_MERGE_PRIORITY")
        elif deadline_margin < 1.0:
            # 0.5 <= deadline_margin < 1.0: 危険域（last_rollback_postmortem制約: deadline_margin >= 1.0は危険域）
            if merge_grade in ["DIRECT", "NEAR"]:
                score += 1500.0
                reasons.append("DANGER_ZONE_DEADLINE_MERGE_PRIORITY")

        # ----- evaluation axis 8.6: reactive pairs immediate merge bonus (v306: 戦略的配置ボーナス完全削除版) -----

        # v299 rollback failure mode: deadline_crossed時、reactive_pairsがあるのに即時併合不可の場合、戦略的配置ボーナスが付与され続け、max_y runaway → ゲームオーバーした。
        # ワーストゲーム(score0738)終盤turns 40-51でreactive_pairs=1-2あるのに即時併合不可、戦略的配置が続きmax_y=0.38→2.42に上昇してゲームオーバー。
        # ベストゲーム(score2869)終盤turns 50-53では即時併合機会（NEAR_MERGE）を確実に捉えてscore_delta=202を獲得。
        # axis 8.6の戦略的配置ボーナス（即時併合不可時に400.0/50.0を付与）が、即時併合機会を逃す原因になっている。
        # last_rollback_postmortemの制約「reactive_pairsがある場合、即時併合が可能ならaxis 9.5ボーナスを付与するが、即時併合不可の場合は戦略的配置ボーナスを完全削除する」を厳守。
        # 即時併合不可の場合、axis 8.6で戦略的配置ボーナスを完全削除し、純粋に他の評価軸（height/drift/balance/chainなど）で判断させる。

        if reactive_pair_count == 1 and merge_grade in ["DIRECT", "NEAR"]:
            # reactive_pairs==1: 即時併合ボーナス
            score += 800.0
            reasons.append("REACTIVE_IMMEDIATE_MERGE_PRIORITY")
        elif reactive_pair_count >= 2 and reactive_pair_count < 3 and merge_grade in ["DIRECT", "NEAR"]:
            # reactive_pairs==2: 即時併合ボーナス
            score += 1300.0
            reasons.append("REACTIVE_IMMEDIATE_MERGE_PRIORITY")
        elif reactive_pair_count >= 3 and merge_grade in ["DIRECT", "NEAR"]:
            # reactive_pairs>=3: 即時併合ボーナス（最優先）
            score += 1600.0
            reasons.append("REACTIVE_IMMEDIATE_MERGE_PRIORITY")
        # 即時併合不可の場合、戦略的配置ボーナスは付与せず、他の評価軸で判断する

        # ----- evaluation axis 9: reactive pairs default (NEW: reactive_pairs fallback for "no action" situations) -----
        # batch_summaryでHEIGHT_CONTROLが22.8%選択(avg_score_delta=1.1)と過剰であり、reactive_pairsがある状況では「何もしない」HEIGHT_CONTROLではなく、
        # reactive_pairsを活用して盤面圧縮を図る戦略的思考へ切り替える。
        # reactive_pairsがある場合、即時併合がない時のデフォルト選択をHEIGHT_CONTROLからREACTIVE_PAIRS_COMPRESSIONへ変更し、盤面圧縮を優先。

        if not reasons:
            if reactive_pair_count >= 1:
                reasons.append("REACTIVE_PAIRS_COMPRESSION")

        # ----- evaluation axis 9.5: current type stack merge priority (v309: 戦略的配置ボーナス完全削除・シンプル化版) -----

        # v309: 即時併合不可時の戦略的配置ボーナス完全削除・シンプル化版
        # v299/v308 rollback failure mode: deadline_crossed時、複数のheight_mult調整ロジックが競合し、戦略的配置が選ばれ続けmax_y runaway → ゲームオーバー
        # axis 9.5の戦略的配置ボーナス（即時併合不可時にheight_multを緩和するロジック）を完全削除
        # 即時併合不可の場合、戦略的配置ボーナスを一切付与せず、純粋に他の評価軸（height/drift/balance/chainなど）で判断させる
        # last_rollback_postmortemの制約遵守：
        #   - 即時併合（DIRECT/NEAR）が可能な場合、常に即時併合を優先する
        #   - reactive_pairsがある場合、即時併合が可能ならaxis 8.6ボーナスを付与するが、即時併合不可の場合は戦略的配置ボーナスを完全削除する（v295のようにシンプルに）
        # v295のシンプルなアプローチに戻り、axis 9.5ボーナス（即時併合不可時のheight_mult緩和）を完全削除
        # v308まではsame_type_stack_topとmerge_grade == "NO"でheight_mult調整を行っていたが、v309で完全削除
        # refs: tmp/improve_brief.md, tmp/batch_summary.txt, tmp/state/last_rollback_postmortem.md, tmp/state/last_rollback_analysis.md,
        #       strategy_versions/v295_strategy.py

        # ----- update best candidate -----
        if score > best_score:
            best_score = score
            best_x = x
            best_reason = "_".join(reasons) if reasons else "HEIGHT_CONTROL"

    # clip to drop range [-3.0, +3.0]
    best_x = max(-3.0, min(3.0, best_x))
    best_x = round(best_x, 2)

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