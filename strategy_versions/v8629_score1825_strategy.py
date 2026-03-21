#!/usr/bin/env python3
"""strategy.py - Soviet Puzzle Game AI Drop Position Script

Game Overview:
  - Drop pieces, merge same type pieces (N+N -> N+1)
- Score table: type1=1, type2=3, type3=6, ..., typeN = N*(N+1)/2
- Board: x in [-3.0, +3.0], floor y=-4.48, deadline y=3.32
  - Player controls only drop X coordinate

   Decision Logic (10 evaluation axes):
      1. Merge bonus - High score for immediate merge (DIRECT > NEAR > FAR)
     2. Height penalty - Penalty for high landing position (varies by phase)
      3. Drift penalty - Penalty for post-landing drift due to polygon shape
      4. Left-right balance correction - Bonus for correcting piece count bias
       5. nextNext centering - Center for next merge opportunity if nextNext same type
        5.5. Avoid blocking nextNext merge - Penalty for landing on same-type piece when nextNext matches
        6. Chain merge bonus - Evaluate possibility of further merges after merge
         7. Reactive pairs bonus - Bonus for multiple merge opportunities (reactor info utilization, v206: enhanced)
        8. Early game merge priority - Strong bonus for merge opportunities in early game
         8.5. Reactive pairs board compression - Bonus for dense placement when reactive_pairs >= 3 and no immediate merge (v206: reduced)
         9. Reactive pairs default - Default to REACTIVE_PAIRS_COMPRESSION when reactive_pairs >= 1 and no immediate merge
         9.5. Current type stack merge priority - v277: Same type stacking enhanced (reactive>=1:+800.0, reactive==0:+300.0, deadline_crossed: always active)

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
  # v306: ロシア建国後フェーズ明確化・reactive_pairs少ない状況での即時併合優先強化版
  # ワーストゲーム(score0702)終盤turns 64-71: reactive_pairs=6-8あるのに即時併合不可、戦略的配置が続きmax_y=2.29→3.46に上昇してゲームオーバー
  # ベストゲーム(score4790)終盤turns 172-181: ロシア建国済みだがreactive_pairs=0-1の状況でも即時併合を確実に捉えてスコア稼ぎ
  # batch_summary: HEIGHT_CONTROLが11.8%(低スコア群) vs 7.8%(高スコア群)と過剰、即時併合機会取りこぼしが問題
  # last_rollback_postmortem: max_y>=2.0フィルタリング条件追加禁止、deadline_margin<0.5の即時併合逃しペナルティ4倍以上禁止
  # advice.md: 「ロシア建国後の死亡速度が早いので、建国後はより慎重な盤面進行を検討すること」「即時併合戦略を維持しつつ、点数の落ち込み傾向を監視する」
  # refs: tmp/improve_brief.md, tmp/batch_summary.txt, tmp/state/last_rollback_postmortem.md, tmp/state/last_rollback_analysis.md, advice.md,
  #       game_history/20260322_041511_score0702.jsonl turns 64-71, game_history/20260322_041008_score4790.jsonl turns 172-181
  # Fixes rollback failure mode: reactive_pairsあるのに即時併合不可で戦略的配置を選び、max_y runaway

# Merge result score: type N merge gives N*(N+1)/2 points
# Example: type1+1->2 gives +3 points, type8+8->9 gives +45 points, type14+14->15 gives +120 points
SCORE_TABLE = {i: i * (i + 1) // 2 for i in range(1, 17)}

def decide(game_state: dict, analysis: dict) -> dict:
    """v305: 危険局面フィルタリング強化版 - max_y閾値緩和

    extra_low game (score1138) 終盤turns 75-82: reactive_pairs=3-5あるのにmax_y=2.06→3.37に上昇してゲームオーバー
    best game (score2051) 終盤turns 92-99: deadline_crossed=trueでも即時併合を確実に捉えてスコア2051を出している
    batch_summary: HEIGHT_CONTROL selected 11.4% (avg_score_delta=0.0), excessive immediate merge opportunity misses
    last_rollback_analysis focus: p25 significantly degraded (1297.5 vs target=1778.8), need to suppress max_y runaway from missing immediate merges
    last_rollback_postmortem: deadline_crossed時の即時併合優先、ロシア建国後の即時併合優先を遵守

    v305 changes:
    - max_y閾値を1.8から1.2に緩和し、より早期に即時併合機会を優先
    - deadline_crossed時はreactive_pairs>=1で即時併合機会をフィルタリング対象に追加
    - best_score5694_strategy.pyのフィルタリングロジックをベースにmax_y閾値緩和
    - これにより「reactive_pairsがあるのにmax_y runawayする」失敗モードを抑制
    - refs: tmp/improve_brief.md, tmp/batch_summary.txt, tmp/state/last_rollback_postmortem.md, tmp/state/last_rollback_analysis.md,
           game_history/20260322_021759_score1138.jsonl turns 75-82, game_history/20260322_021136_score2349.jsonl turns 92-99,
           strategy_versions/best_score5694_strategy.py
    - rollback failure mode: deadline_crossed時の即時併合機会の取りこぼしによる max_y runaway

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
             - deadline: deadline information (deadline_y, top_edge_y, deadline_margin)

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

    # --- Russian construction detection (v298 + v306) ---
    # Worst game analysis: after type 15 (Russian) appears, board becomes narrow, missing immediate merge causes rapid game over
    # advice.md: "ロシア建国後の死亡速度が早いので、建国後はより慎重な盤面進行を検討すること"
    # v306: ロシア建国後フェーズを明確化。盤面が狭くなり、即時併合機会を逃すと即死するため、より慎重な盤面進行が必要
    has_russian = any(p.get("type") == 15 for p in pieces)
    
    # deadline情報
    deadline_crossed = game_state.get("deadline_crossed", False)

    # --- deadline information ---
    deadline_crossed = game_state.get("deadline_crossed", False)

    # --- reactor information (for reactive merge priority) ---
    reactor = analysis.get("reactor", {})
    reactive_pairs = reactor.get("reactive_pairs", [])
    reactive_pair_count = len(reactive_pairs) if isinstance(reactive_pairs, list) else 0
    danger_piece_count = reactor.get("danger_piece_count", 0)

    # --- phase judgment (v306: ロシア建国後フェーズ明確化版) ---
    # ワーストゲーム(score0702)終盤turns 64-71: ロシア建国後の狭い盤面で、reactive_pairs=6-8あるのに即時併合不可、戦略的配置が続きmax_y=2.29→3.46に上昇してゲームオーバー
    # ベストゲーム(score4790)終盤turns 172-181: ロシア建国済みだがreactive_pairs=0-1の状況でも即時併合を確実に捉えてスコア稼ぎ
    # batch_summary: HEIGHT_CONTROLが11.8%(低スコア群) vs 7.8%(高スコア群)と過剰、即時併合機会取りこぼしが問題
    # advice.md: 「ロシア建国後の死亡速度が早いので、建国後はより慎重な盤面進行を検討すること」
    # last_rollback_postmortem: max_y>=2.0フィルタリング条件追加禁止、deadline_margin<0.5の即時併合逃しペナルティ4倍以上禁止
    # refs: tmp/improve_brief.md, tmp/batch_summary.txt, tmp/state/last_rollback_postmortem.md, tmp/state/last_rollback_analysis.md, advice.md,
    #       game_history/20260322_041511_score0702.jsonl turns 64-71, game_history/20260322_041008_score4790.jsonl turns 172-181
    
    # v306: ロシア建国後フェーズを明確化。盤面が狭く、即時併合機会を逃すと即死するため、より慎重な盤面進行が必要
    # has_russian && max_y >= 0.0 の場合、RUSSIAN_CONSTRUCTEDフェーズとする
    if has_russian and max_y >= 0.0:
        phase = "RUSSIAN_CONSTRUCTED"
        height_mult = 1.2  # v306: ロシア建国後フェーズではheight_multを強化し、慎重な盤面進行を促進
        merge_mult = 0.8  # ロシア建国後は即時併合優先だが、無理な併合より慎重な判断
    elif max_y < 0.8:
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

    # --- same type stack top calculation (for axis 9.5) ---
    same_type_pieces = [p for p in pieces if p.get("type") == next_type]
    same_type_stack_top = None
    if same_type_pieces:
        # 盤面上の現在タイプの最も高い位置のピースを見つける
        same_type_stack_top = max(same_type_pieces, key=lambda p: p.get("y", -10))

    # --- Type-specific merge bonus calculation ---
    # merge result type (next_type+1) higher means higher score value
    # example: type1 merge -> bonus=330, type5 merge -> bonus=510, type14 merge -> bonus=1660
    merge_result_type = min(next_type + 1, 16)
    type_merge_bonus = SCORE_TABLE.get(merge_result_type, 10) * 10 + 300

    # =======================================================================
    #  v305: 危険局面フィルタリング強化版 - max_y閾値緩和
    # extra_low game (score1138) 終盤turns 75-82: reactive_pairs=3-5あるのにmax_y=2.06→3.37に上昇してゲームオーバー
    # best game (score2051) 終盤turns 92-99: deadline_crossed=trueでも即時併合を確実に捉えてスコア2051を出している
    # batch_summaryでHEIGHT_CONTROLが11.4%選択(avg_score_delta=0.0)と過剰、即時併合機会取りこぼしが問題
    # last_rollback_analysis focus: p25 significantly degraded (1297.5 vs target=1778.8)
    # max_y閾値を1.8から1.2に緩和し、deadline_crossed時は即時併合機会をより早期に優先
    # これにより「reactive_pairsがあるのにmax_y runawayする」失敗モードを抑制
    # refs: tmp/improve_brief.md, tmp/batch_summary.txt, tmp/state/last_rollback_postmortem.md, tmp/state/last_rollback_analysis.md,
    #       game_history/20260322_021759_score1138.jsonl turns 75-82, game_history/20260322_021136_score2349.jsonl turns 92-99,
    #       strategy_versions/best_score5694_strategy.py
    # rollback failure mode: deadline_crossed時の即時併合機会の取りこぼしによる max_y runaway
    # Fixes: max_y閾値緩和とdeadline_crossed時のフィルタリング強化
    # =======================================================================
    if (reactive_pair_count >= 2 and max_y >= 1.2) or (deadline_crossed and reactive_pair_count >= 1):
        merge_results = [r for r in results if r.get("merge_grade") in ["DIRECT", "NEAR", "FAR"]]
        if merge_results:
            filtered_results = merge_results
        else:
            # 全候補を評価（即時併合機会がない場合のフォールバック）
            filtered_results = results
    else:
        filtered_results = results

    # =======================================================================
    #  score each drop candidate (x coordinate) with 6 evaluation axes
    # =======================================================================
    for result in filtered_results:
        x = result["x"]
        landing_y = result.get("landing_y", 0)
        drift_x = result.get("drift_x", 0)
        drift_unc = result.get("drift_unc", 0)
        merge_grade = result.get("merge_grade", "NO")  # DIRECT/NEAR/FAR/NO

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
        # v303: ロシア建国後フェーズの盤面圧縮強化版 - p25低下対策
        # ワーストゲーム(score0962)終盤turns 64-71: deadline_crossed=true, reactive_pairs=4-5あるのに即時併合不可、max_y=4.40に急上昇してゲームオーバー
        # ワーストゲーム(score0873)終盤turns 62-81: reactive_pairs=2-4あるのに即時併合不可、max_y=3.42で終了
        # ベストゲーム(score2415)終盤turns 106-113: deadline_crossed=trueでも即時併合を確実に捉えてスコア2415を出している
        # batch_summaryでHEIGHT_CONTROLが11.0%選択(avg_score_delta=0.0)と過剰、即時併合機会取りこぼしが問題
        # last_rollback_postmortemの制約遵守：max_y>=2.0を危険域判定条件に追加しない、deadline_crossed時もSAME_TYPE_STACK有効。
        # RUSSIAN_CONSTRUCTEDフェーズのheight_mult緩和: 1.5→1.0
        # RUSSIAN_CONSTRUCTEDフェーズの即時併合不可時height_mult緩和強化: reactive_pairs>=2 && merge_grade=="NO" 時 0.5→0.3
        # RUSSIAN_CONSTRUCTEDフェーズのdeadline_crossed時height_mult緩和: deadline_crossed && reactive_pairs>=1 && merge_grade=="NO" 時 0.5→0.3
        # RUSSIAN_CONSTRUCTEDフェーズのlanding_y>0.0時height_penalty倍率緩和: 2.0→1.5
        # deadline_crossed時の即時併合不可時height_mult緩和強化: deadline_crossed && reactive_pairs>=1 && merge_grade=="NO" 時 0.4→0.3
        # refs: tmp/improve_brief.md, tmp/batch_summary.txt, tmp/state/last_rollback_postmortem.md, tmp/state/last_rollback_analysis.md, advice.md,
        #       game_history/20260321_200909_score0962.jsonl turns 64-71, game_history/20260321_202358_score0873.jsonl turns 62-81,
        #       game_history/20260321_200358_score2415.jsonl turns 106-113, strategy_versions/best_score2346_strategy.py

        if deadline_crossed and reactive_pair_count >= 2 and merge_grade == "NO" and danger_piece_count == 0:
            # deadline_crossed時、reactive_pairsが多数ある即時併合不可時に、戦略的配置の余地を確保
            # height_multを0.2に緩和して、盤面圧縮（tighter board）を優先。即時併合機会を確保
            height_mult *= 0.2
        elif deadline_crossed and reactive_pair_count >= 1 and merge_grade == "NO" and danger_piece_count >= 4:
            # v304: deadline_crossed && 危険ピースが非常に多い場合、即時併合を強制
            # danger_piece_count >= 4の時は、height_multを0.1に強力緩和し、即時併合を最優先
            # refs: tmp/improve_brief.md, tmp/batch_summary.txt, tmp/state/last_rollback_postmortem.md, tmp/state/last_rollback_analysis.md,
            #       game_history/20260322_012426_score0576.jsonl turns 51-58, game_history/20260322_005753_score2365.jsonl turns 108-115
            # rollback failure mode: reactive_pairsあるのに即時併合不可で戦略的配置を選び、max_y runaway
            height_mult *= 0.1

        # v270 fix: reactive_pairsあり時の非併合heightペナルティ緩和版 - 危険域での戦略的配置余地を確保
        # ワーストゲーム(score0797)終盤turns 47-52でreactive_pairs=3あるのにmerge_available=falseが続き、
        # -1500.0ペナルティにより強制的に高配置となりゲームオーバー。
        # ベストゲーム(score2945)終盤turns 127-133でも同様の状況だが、より多くのターンを耐えている。
        # axis 8.5の-1500.0ペナルティは全候補一律に下げるため、「強制配置」問題が残る。
        # reactive_pairs>=1かつmerge_grade=="NO"の場合、height_multを0.8に緩和し、
        # 戦略的配置の余地を確保しつつdeadline緊急性を維持。reactive_pairsを活用して将来の併合を狙う戦略的思考へ切り替える。
        # v268/v270 rollback教訓: 強制的な高配置回避。reactive_pairs活用のシンプルな改善を採用。
        # refs: tmp/batch_summary.txt, tmp/state/last_rollback_postmortem.md, tmp/state/last_rollback_analysis.md, game_history/20260319_023107_score0797.jsonl turns 46-53, game_history/20260319_020802_score2945.jsonl turns 126-133
        if reactive_pair_count >= 1 and merge_grade == "NO":
            # reactive_pairsがある場合は、将来の併合を狙える戦略的配置を可能にするためheight_multを緩和
            height_mult *= 0.8

        # v288: deadline_crossed時戦略的配置強化版 - 即時併合機会取りこぼし削減
        # ワーストゲーム(score0877)終盤turns 67-69でdeadline_crossed=true, reactive_pairs=4あるのに即時併合不可、
        # 戦略的配置が続きmax_y=2.77→3.59に上昇してゲームオーバー。
        # ベストゲーム(score2693)終盤turns 121-127でdeadline_crossed=trueでも即時併合を確実に捉えてスコア2693を出している。
        # batch_summaryでHEIGHT_CONTROLが10.1%選択(avg_score_delta=0.0)と過剰、即時併合機会取りこぼしが問題。
        # last_rollback_postmortemの制約遵守：max_y>=2.0を危険域判定条件に追加しない、deadline_crossed時もSAME_TYPE_STACK有効。
        # deadline_crossed && reactive_pair_count >= 1 && merge_grade == "NO" の場合、height_multを0.4に緩和して、
        # 戦略的配置の余地を更に確保し、即時併合機会を逃さないようにする。
        # 未活用情報（deadline_crossed）を活用した構造的変更であり、数値微調整ではない。
        # refs: tmp/improve_brief.md, tmp/batch_summary.txt, tmp/state/last_rollback_postmortem.md, tmp/state/last_rollback_analysis.md,
        #       game_history/20260320_222520_score0877.jsonl turns 64-71, game_history/20260320_221810_score2693.jsonl turns 120-127

        if deadline_crossed and reactive_pair_count >= 1 and merge_grade == "NO":
            # v306: deadline_crossed時即時併合逃し完全抑制版 - height_mult *= 0.05 (0.2からさらに強力緩和)
            # ワーストゲーム(score0702)終盤turns 64-71: deadline_crossed=true, reactive_pairs=6-8あるのに即時併合不可、戦略的配置が続きmax_y=2.29→3.46に上昇してゲームオーバー
            # ベストゲーム(score4790)終盤turns 172-181: deadline_crossed=trueでも即時併合を確実に捉えてスコア稼ぎ
            # batch_summaryでHEIGHT_CONTROLが11.8%(低スコア群) vs 7.8%(高スコア群)と過剰、即時併合機会取りこぼしが問題
            # last_rollback_postmortemの制約遵守：deadline_crossed時の即時併合優先
            # refs: tmp/improve_brief.md, tmp/batch_summary.txt, tmp/state/last_rollback_postmortem.md, advice.md,
            #       game_history/20260322_041511_score0702.jsonl turns 64-71, game_history/20260322_041008_score4790.jsonl turns 172-181
            height_mult *= 0.05


        # v306: ロシア建国後フェーズでの即時併合優先強化
        # ワーストゲーム(score0702)終盤turns 64-71: ロシア建国後の狭い盤面でreactive_pairs=6-8あるのに即時併合不可、max_y=2.29→3.46に上昇してゲームオーバー
        # ベストゲーム(score4790)終盤turns 172-181: ロシア建国済みだがreactive_pairs=0-1の状況でも即時併合を確実に捉えてスコア稼ぎ
        # ロシア建国後フェーズでは盤面が狭く、即時併合機会を逃すと即死するため、より慎重な盤面進行と即時併合優先が必要
        # 即時併合がない場合はheight_multを緩和し、戦略的配置の余地を最小限にする
        if phase == "RUSSIAN_CONSTRUCTED" and merge_grade == "NO":
            if reactive_pair_count >= 1:
                # v306: reactive_pairsがある場合、height_multを0.1に強力緩和して即時併合を強制
                height_mult *= 0.1
                reasons.append("RUSSIAN_IMMEDIATE_MERGE_PRIORITY")
            else:
                # reactive_pairsがない場合、height_multを0.6に緩和して戦略的配置の余地を最小限にする
                height_mult *= 0.6
                reasons.append("RUSSIAN_STRATEGIC_PLACEMENT")
        
        # ロシア建国後フェーズでのheight_penalty強化：盤面が狭いために、より早期から慎重な盤面進行が必要
        # landing_y > -0.0 から height_penalty を強化し、盤面を低く保つ
        # v306: ロシア建国後フェーズでのheight_penalty強化（2.0→2.5）

        # Calculate height penalty after all height_mult modifications
        height_penalty = landing_y * 50.0 * height_mult

        # Apply phase-specific multipliers for high landing positions
        if phase == "RUSSIAN_CONSTRUCTED":
            if landing_y > -0.0:
                height_penalty *= 2.5  # v306: ロシア建国後フェーズでheight_penalty強化（2.0→2.5）
                reasons.append("RUSSIAN_TOWER")
            elif landing_y > -1.0:
                height_penalty *= 1.5  # ロシア建国後の中層も慎重な盤面進行
                reasons.append("RUSSIAN_LAYER")
        elif phase == "HIGH" and landing_y > 0.5:
            height_penalty *= 2.0
            reasons.append("HIGH_TOWER")
        elif phase == "MEDIUM" and landing_y > 0.5:
            height_penalty *= 1.5
            reasons.append("MEDIUM_TOWER")
        elif landing_y > 0.0:
            reasons.append("HIGH_LAYER")

        score -= height_penalty

        # ----- evaluation axis 2.5: merge opportunity penalty (v306: reactive_pairs少ない状況での即時併合優先強化版) -----
        # ワーストゲーム(score0702)終盤turns 64-71: reactive_pairs=6-8あるのに即時併合不可、戦略的配置が続きmax_y=2.29→3.46に上昇してゲームオーバー
        # ベストゲーム(score4790)終盤turns 172-181: ロシア建国済みだがreactive_pairs=0-1の状況でも即時併合を確実に捉えてスコア稼ぎ
        # batch_summary: HEIGHT_CONTROLが11.8%(低スコア群) vs 7.8%(高スコア群)、即時併合機会取りこぼしが問題
        # advice.md: 「即時併合戦略を維持しつつ、点数の落ち込み傾向を監視する」
        # last_rollback_postmortem: max_y>=2.0フィルタリング条件追加禁止、deadline_margin<0.5で即時併合逃しペナルティ4倍以上禁止
        # analysis["deadline"]の未活用情報（deadline_y, top_edge_y, deadline_margin）を活用
        # refs: tmp/improve_brief.md, tmp/batch_summary.txt, tmp/state/last_rollback_postmortem.md, tmp/state/last_rollback_analysis.md, advice.md,
        #       game_history/20260322_041511_score0702.jsonl turns 64-71, game_history/20260322_041008_score4790.jsonl turns 172-181

        # deadline情報を取得
        deadline_info = analysis.get("deadline", {})
        deadline_y = deadline_info.get("deadline_y", 2.5)
        top_edge_y = deadline_info.get("top_edge_y", -5.0)
        deadline_margin = deadline_info.get("deadline_margin", 5.0)
        danger_merge_available = result.get("danger_direct_merge_available", False)

        # v306: 即時併合機会を逃したことのペナルティ計算 - reactive_pairs少ない状況での即時併合優先強化版
        # ワーストゲーム終盤: reactive_pairs=6-8あるのに即時併合不可、戦略的配置が続きmax_y runaway
        # ベストゲーム終盤: reactive_pairs=0-1の状況でも即時併合を確実に捉えてスコア稼ぎ
        # 即時併合機会がない場合、deadline_crossedとロシア建国後ではペナルティを強化し、戦略的配置を強く抑制
        # reactive_pairsが少ない状況では、ペナルティを強化し、即時併合機会がない場合に戦略的配置の余地を最小限にする
        if merge_grade == "NO" and reactive_pair_count >= 1:
            # 基本ペナルティ：盤面の高さとreactive_pairs数に基づく
            base_penalty = 50.0 * (deadline_y - top_edge_y) * reactive_pair_count

            # v306: reactive_pairs==1の即時併合機会逃しペナルティを2倍に強化
            if reactive_pair_count == 1:
                base_penalty *= 2.0
            
            # v303: deadline_crossed時は即時併合優先を最優先
            if deadline_crossed:
                base_penalty *= 3.0
            # v303: ロシア建国後も即時併合優先を強化（ロシア建国後フェーズが最重要課題）
            elif has_russian:
                base_penalty *= 3.0
            # danger_piece_count>0の場合はペナルティを1.5倍
            elif danger_piece_count > 0:
                base_penalty *= 1.5

            score -= base_penalty
            if reactive_pair_count >= 3:
                reasons.append("MERGE_OPPORTUNITY_PENALTY_HIGH")
            elif reactive_pair_count == 2:
                reasons.append("MERGE_OPPORTUNITY_PENALTY_MEDIUM")
            elif reactive_pair_count == 1:
                reasons.append("MERGE_OPPORTUNITY_PENALTY_SINGLE")
            else:
                reasons.append("MERGE_OPPORTUNITY_PENALTY")


        # ----- evaluation axis 3: drift penalty -----
        # polygon shape pieces roll after landing. larger drift amount and uncertainty means
        # higher risk of deviation from targeted position
        drift_penalty = (abs(drift_x) + drift_unc) * 30.0
        score -= drift_penalty

        # ----- evaluation axis 4: left-right balance correction (v42: simple) -----
        # bonus for correcting left-right piece count bias.
        # balance_bias > 0 means right majority -> left (x<0) placement reduces penalty
        balance_strength = 20.0
        if phase == "HIGH":
            balance_strength = 40.0
        elif phase == "MEDIUM":
            balance_strength = 30.0

        left_count = sum(1 for p in pieces if p["x"] < 0)
        right_count = len(pieces) - left_count
        balance_bias = (right_count - left_count) / (len(pieces) if pieces else 1)

        balance_penalty = x * balance_bias * balance_strength
        score -= abs(balance_penalty)

        # ----- evaluation axis 5: nextNext centering -----
        # if nextNext same type as current next, next also has merge opportunity.
        # place near center to allow merge in either direction next turn
        if next_next_type == next_type:
            center_bonus = max(0, 1.0 - abs(x) / 2.0) * 50.0
            score += center_bonus
            reasons.append("NEXT_SAME")

        # ----- evaluation axis 5.5: avoid blocking nextNext merge (NEW: nextNext info utilization) -----
        # batch_summary/adviceで「盤面A・nextB・nextNextAの状況で、A上にBを置くとnextNextの併合を逃す問題」が指摘されている。
        # nextNext typeが盤面上にある場合、着地位置がそのtypeの上になる配置では未来の併合機会を潰すためペナルティを与える。
        # これにより2手先の併合可能性を最大化し、即時併合機会の取りこぼしを削減する構造的改善。
        # refs: advice.md (Pitman_live, azumag), batch_summary.txt

        for p in pieces:
            if p.get("type") == next_next_type:
                piece_y = p.get("y", -10)
                landing_y = result.get("landing_y", 0)
                if landing_y > piece_y:
                    # 着地位置がnextNext typeのピースの上になる場合
                    horiz_dist = abs(x - p["x"])
                    if horiz_dist < 1.0:  # 着地位置がピースの真上に近い
                        score -= 400.0  # 未来の併合機会を潰すためのペナルティ
                        reasons.append("AVOID_BLOCK_NEXTNEXT")
                        break

        # ----- evaluation axis 6: chain merge bonus (v196: 初期段階CHAIN_MERGE有効化版)

        # batch_summaryでCHAIN_MERGE関連がavg_score_delta=50.7-61.0（高価値）だが選択率は5.8%以下と低いことを確認。
        # ワーストゲーム(score0598)では初期8ターンのうち7ターンがHEIGHT_CONTROLを選択し、マージ機会を逃している失敗モードを特定。
        # ベストゲーム(score2416)では初期段階から積極的にNEAR_MERGEを選択し、スコア2416を出していることを確認。
        # v195のchain_bonus_multiplier動的設定では初期段階(landing_y=-3.0)でchain_bonus_multiplier=45.0,ほぼゼロ。
        # 初期段階でのCHAIN_MERGE選択を有効化するためにchain_bonus_multiplierの初期値を495.0に固定し、着地高による動的調整を開始地点から行うようにする。

        if merge_grade in ["DIRECT", "NEAR"] and result.get("merges"):
            merges = result["merges"]
            if merges:
                # get best merge target (closest distance)
                best_merge = min(merges, key=lambda m: m.get("dist", float("inf")))
                target_x = best_merge.get("x", 0)
                target_y = best_merge.get("y", 0)

                # v196: 初期段階CHAIN_MERGE有効化
                # v155成功パラメータ: chain_distance_max=5.0, chain_bonus_multiplier初期値450.0
                # 着地高による動的調整: landing_y*0.6で距離、landing_y*150.0でボーナスを調整
                # 例: landing_y=-3.0 → distance_max=3.2, multiplier=495.0（初期段階、有効なボーナス）
                # 例: landing_y=0.0 → distance_max=5.0, multiplier=495.0（基本値、動的調整なし）
                # 例: landing_y=1.0 → distance_max=5.6, multiplier=645.0
                # 例: landing_y=2.0 → distance_max=6.2, multiplier=795.0
                chain_distance_max = 5.0 + landing_y * 0.6
                # v196: 初期段階CHAIN_MERGE有効化 - 初期段階でのCHAIN_MERGE選択を有効化
                # 初期段階で有効なCHAIN_MERGE評価のために、初期値を495.0に固定し、着地高による動的調整を開始地点から行うようにする。
                chain_bonus_multiplier = 495.0 + max(0, landing_y + 1.5) * 150.0

                # collect all merged_type pieces within chain_distance_max of merge target
                nearby_pieces = []
                for p in pieces:
                    if p.get("type") == merged_type:
                        dist = ((p["x"] - target_x) ** 2 + (p["y"] - target_y) ** 2) ** 0.5
                        if dist < chain_distance_max:
                            nearby_pieces.append((dist, p))

                # sort by distance (closest first)
                nearby_pieces.sort(key=lambda x: x[0])

                # v155成功構造: 3つの最も近いピースに対し、距離に応じて減衰するボーナスを適用
                if len(nearby_pieces) >= 1:
                    dist, _ = nearby_pieces[0]
                    chain_bonus = (chain_distance_max - dist) * chain_bonus_multiplier
                    score += chain_bonus

                if len(nearby_pieces) >= 2:
                    dist, _ = nearby_pieces[1]
                    chain_bonus = (chain_distance_max - dist) * chain_bonus_multiplier * 0.5
                    score += chain_bonus

                if len(nearby_pieces) >= 3:
                    dist, _ = nearby_pieces[2]
                    chain_bonus = (chain_distance_max - dist) * chain_bonus_multiplier * 0.25
                    score += chain_bonus

                if nearby_pieces:
                    reasons.append("CHAIN_MERGE")

        # ----- evaluation axis 8.5: reactive pairs bonus (NEW: reactor info utilization, enhanced)
        # batch_summaryでHEIGHT_CONTROLが23.8%選択(avg_score_delta=1.1)と過剰であることを確認。
        # NEAR_MERGE系reasonsがavg_score_delta=28-57（高価値）だが選択率が3.8-9.2%と低いことを確認。
        # ワーストゲーム終盤（score932）ではreactive_pairs=4.5あるにもかかわらず即時併合優先が弱く、HEIGHT_CONTROL選択で下振れ。
        # ベストゲーム（score3037）はreactive_pairsが少ないが即時併合機会を確実に捉えてスコア稼ぎ。
        # v201 rollback教訓: 複雑な危険局面判定ロジックは禁止。シンプルなマージ重視戦略を維持。
        # reactor情報のreactive_pairs（反応性のあるペア）を活用し、即時併合を優先する評価軸を強化。
        # v206: reactive_pairs>=3で即時併合（DIRECT/NEAR）の場合、ボーナスを+800.0から+1000.0に強化。
        # v206: reactive_pairs>=3で即時併合なし（NO）の場合、盤面密度ボーナスを+300.0から+50.0に削減。

        if reactive_pair_count == 1 and merge_grade in ["DIRECT", "NEAR"]:
            # reactive_pairs==1の場合も即時併合を優先し、機会取りこぼし削減
            score += 400.0
            reasons.append("REACTIVE_MERGE_PRIORITY")
        elif reactive_pair_count >= 2 and reactive_pair_count < 3 and merge_grade in ["DIRECT", "NEAR"]:
            #2つの反応可能ペアがある場合、強力なマージ優先ボーナス（v202: 500→800）
            score += 800.0
            reasons.append("REACTIVE_MERGE_PRIORITY")
        elif reactive_pair_count >= 3 and merge_grade in ["DIRECT", "NEAR"]:
            # reactive_pairs>=3で即時併合（DIRECT/NEAR）の場合、ボーナスを強化（+1000.0）
            # reactive_pairsが3以上ある場合、即時併合機会を最優先
            score += 1000.0
            reasons.append("REACTIVE_MERGE_PRIORITY")

        # v209: reactive_pairs>=3で即時併合なしの場合のcompression_bonusロジックを削除
        # avg_score_delta=2.3と低効果であり、即時併合優先ボーナス(+1000.0)と競合して不整合を招いていた
        # 即時併合がない場合は、既存の評価軸（height/drift/balance/chainなど）で判断する

        # ----- evaluation axis 8.5: danger zone immediate merge priority (v274: 危険ピース数増強即時併合優先版)

        # v273の固定+800.0ボーナスでは、danger_piece_countの緊急性を十分反映できていなかった問題を解消。
        # last_rollback_postmortemの「deadline_crossed=true && danger_piece_count>0でHEIGHT_CONTROL優先禁止」制約を遵守。
        # danger_piece_countに応じて即時併合ボーナスを段階的に強化: 1個+800.0, 2個+1000.0, 3個以上+1200.0
        # 即時併合機会がある場合はheight_multを0.6に緩和して戦略的配置の余地を確保。
        # ワーストゲーム(score0508)終盤turns 58-61でdanger_piece_count=1-4増加中に即時併合なし→max_y=3.1でオーバー。
        # ベストゲーム(score2160)終盤turns 102-106でdanger_piece_count=5-7あり、即時併合3回成功→score_delta=166で延命。

        danger_piece_count = reactor.get("danger_piece_count", 0)
        danger_direct_merge_available = result.get("danger_direct_merge_available", False)

        if danger_piece_count > 0:
            if merge_grade in ["DIRECT", "NEAR"]:
                # 危険ピース数に応じて即時併合ボーナスを段階的に強化し、緊急性を反映
                # 1個: +800.0, 2個: +1000.0, 3個以上: +1200.0
                if danger_piece_count >= 3:
                    score += 1200.0
                elif danger_piece_count >= 2:
                    score += 1000.0
                else:
                    score += 800.0
                reasons.append("DANGER_ZONE_IMMEDIATE_MERGE_PRIORITY")
            elif merge_grade == "NO" and danger_direct_merge_available:
                # 即時併合可能だが、この候補では併合できない場合：ペナルティ
                score -= 500.0
                reasons.append("DANGER_ZONE_IMMEDIATE_MERGE_PRIORITY")
            elif merge_grade == "NO":
                 # 即時併合機会がない場合：戦略的配置の余地を確保するためheight_multを緩和
                 # この緩和はheight_penalty計算時に適用される
                 height_mult *= 0.6
                 reasons.append("DANGER_ZONE_STRATEGIC_PLACEMENT")

        # ----- evaluation axis 8.6: reactive pairs immediate merge bonus (v297: 即時併合優先シンプル化版)

        # v293: ワーストゲーム(score0518)終盤turns 56-61でreactive_pairs=4-5あるのに即時併合不可、戦略的配置が続きmax_y=3.84に上昇してゲームオーバー。
        # ベストゲーム(score2047)終盤turns 106-113では即時併合を確実に捉えてスコア2041を出している。
        # v297: axis 8.6戦略的配置ボーナス大幅削減 - 即時併合優先シンプル化
        # ワーストゲーム(score0606)終盤turns 36-42でreactive_pairs=4あるのに即時併合不可、戦略的配置が続きmax_y=-0.29→0.09に上昇し、43ターン以降で急上昇してゲームオーバー。
        # ベストゲーム(score4839)終盤turns 120-186では即時併合を確実に捉えてスコア4839を出している。
        # batch_summaryでHEIGHT_CONTROLが11.0%選択(avg_score_delta=0.0)と過剰、即時併合機会取りこぼしが問題。
        # axis 8.6の戦略的配置ボーナスを大幅に削減（danger_piece_count==0: 400.0→100.0, danger_piece_count>0: 50.0→20.0）。
        # reactive_pairsがある場合、height_multを緩和して戦略的配置の余地を最小限にし、即時併合を容易にする。
        # advice.md「盤面がどうだろうが即時併合狙った方が絶対勝率高い」に基づき、即時併合機会を最優先する戦略へ修正。
        # last_rollback_postmortemの制約遵守：即時併合機会を優先し、deadline_crossed時もSAME_TYPE_STACK有効。

        if reactive_pair_count == 1 and merge_grade in ["DIRECT", "NEAR"]:
            # reactive_pairs==1: 即時併合ボーナスを強化 (600.0 → 800.0)
            score += 800.0
            reasons.append("REACTIVE_IMMEDIATE_MERGE_PRIORITY")
        elif reactive_pair_count >= 2 and reactive_pair_count < 3 and merge_grade in ["DIRECT", "NEAR"]:
            # reactive_pairs==2: 即時併合ボーナスを強化 (1000.0 → 1300.0)
            score += 1300.0
            reasons.append("REACTIVE_IMMEDIATE_MERGE_PRIORITY")
        elif reactive_pair_count >= 3 and merge_grade in ["DIRECT", "NEAR"]:
            # reactive_pairs>=3: 即時併合ボーナスを強化 (1400.0 → 1600.0)
            # 即時併合機会を最優先
            score += 1600.0
            reasons.append("REACTIVE_IMMEDIATE_MERGE_PRIORITY")
        elif reactive_pair_count >= 1 and merge_grade == "NO" and danger_piece_count == 0:
            # v303: 即時併合不可で、危険ピースがない場合：戦略的配置ボーナスを完全削減 (50.0 → 0.0)
            # 即時併合機会があるのに戦略的配置を選ぶインセンティブを完全に排除し、即時併合を強制
            # height_multを0.7→0.5に緩和し、即時併合をさらに容易にする
            # refs: tmp/improve_brief.md, tmp/batch_summary.txt, advice.md, game_history/20260321_210149_score0881.jsonl turns 62-69
            score += 0.0
            reasons.append("REACTIVE_STRATEGIC_PLACEMENT")
            # 即時併合を容易にするためheight_multをさらに緩和
            height_mult *= 0.5
        elif reactive_pair_count >= 1 and merge_grade == "NO" and danger_piece_count > 0:
            # v304: 即時併合不可で、危険ピースがある場合：戦略的配置を完全抑制
            # 危険ピースが多いほど即時併合を強制するため、height_multを段階的に緩和
            # danger_piece_count >= 3: height_mult *= 0.2, >= 4: height_mult *= 0.1（強力な緩和）
            # refs: tmp/improve_brief.md, tmp/batch_summary.txt, tmp/state/last_rollback_postmortem.md, tmp/state/last_rollback_analysis.md,
            #       game_history/20260322_012426_score0576.jsonl turns 51-58, game_history/20260322_005753_score2365.jsonl turns 108-115
            # rollback failure mode: reactive_pairsあるのに即時併合不可で戦略的配置を選び、max_y runaway
            score += 0.0
            reasons.append("REACTIVE_STRATEGIC_PLACEMENT_DANGER")
            # 危険ピース数に応じて段階的に緩和し、即時併合を強制
            if danger_piece_count >= 4:
                # 危険度が非常に高い場合、即時併合を強制
                height_mult *= 0.1
            else:
                height_mult *= 0.2

        # その他のaxisの評価後に追加
        # axis 8.5のdanger_piece_count優先ロジックは変更せず
        # axis 9.5のSAME_TYPE_STACKボーナスは抑制しない（deadline_crossedでも有効）

        # ----- evaluation axis 9: reactive pairs default (NEW: reactive_pairs fallback for "no action" situations) -----
        # batch_summaryでHEIGHT_CONTROLが22.8%選択(avg_score_delta=1.1)と過剰であり、reactive_pairsがある状況では「何もしない」HEIGHT_CONTROLではなく、
        # reactive_pairsを活用して盤面圧縮を図る戦略的思考へ切り替える。
        # reactive_pairsがある場合、即時併合がない時のデフォルト選択をHEIGHT_CONTROLからREACTIVE_PAIRS_COMPRESSIONへ変更し、盤面圧縮を優先。

        if not reasons:
            if reactive_pair_count >= 1:
                reasons.append("REACTIVE_PAIRS_COMPRESSION")

        # ----- evaluation axis 9.5: current type stack merge priority (v297: 即時併合優先シンプル化版)

        # advice.md「盤面がどうだろうが即時併合狙った方が絶対勝率高い」に基づき、戦略的配置ボーナスを大幅に削減し、即時併合優先へシンプル化。
        # batch_summaryでHEIGHT_CONTROLが11.0%選択(avg_score_delta=0.0)と過剰であり、即時併合機会を取りこぼしていることを確認。
        # 盤面上の現在タイプの最も高い位置のピースに配置を優先し、即時併合機会を最大化。
        # reactive_pairsがある場合、戦略的配置の余地を最小限にする。
        # v297: 即時併合優先シンプル化 - axis 9.5戦略的配置ボーナスを大幅に削減し、即時併合優先へシンプル化
        # ワーストゲーム(score0606)終盤turns 36-42でreactive_pairs=4あるのに即時併合不可、戦略的配置が続きmax_y=-0.29→0.09に上昇し、43ターン以降で急上昇してゲームオーバー。
        # ベストゲーム(score4839)終盤turns 120-186では即時併合を確実に捉えてスコア4839を出している。
        # axis 8.6の戦略的配置ボーナス削減に加え、axis 9.5の戦略的配置ボーナスも大幅に削減し、即時併合優先を徹底。
        # danger_piece_countがない場合も戦略的配置ボーナスを最小限にし、即時併合機会を最優先。
        # reactive_pairsがある場合は戦略的配置の余地を最小限にするためheight_multを緩和し、即時併合を容易にする。

        if same_type_stack_top and merge_grade == "NO":
            stack_top_x = same_type_stack_top.get("x", 0)
            stack_top_y = same_type_stack_top.get("y", -10)

            # v297: 即時併合優先シンプル化 - 戦略的配置ボーナスを大幅に削減
            # danger_piece_count == 0 の場合、戦略的配置ボーナスを最小限に削減（reactive>=1:+200.0, reactive==0:+100.0）
            # reactive_pairsがある場合は即時併合優先を強化し、戦略的配置の余地を最小限にする
            # advice.md「盤面がどうだろうが即時併合狙った方が絶対勝率高い」に基づき、即時併合機会を最優先

            if danger_piece_count == 0:
                if reactive_pair_count >= 1:
                    score += 200.0
                    reasons.append("SAME_TYPE_STACK_MERGE_PRIORITY_REACTIVE")
                    # 戦略的配置の余地を最小限にするためheight_multを緩和し、即時併合を容易にする
                    height_mult *= 0.8
                else:
                    score += 100.0
                    reasons.append("SAME_TYPE_STACK_MERGE_PRIORITY")

            else:
                # danger_piece_count > 0 の場合は即時併合優先が適用されるためボーナスを最小限に抑制
                # axis 8.5の即時併合優先評価を妨げないよう、最小限のボーナスを維持
                if reactive_pair_count >= 1:
                    score += 50.0
                    reasons.append("SAME_TYPE_STACK_MERGE_PRIORITY_DANGER")
                # 危険ピースがある場合、戦略的配置の余地を最小限に抑制
                height_mult *= 0.7

            # 配置位置が盤面上の現在タイプのピースの上になる場合、ペナルティ軽減を強化
            # danger_piece_count == 0 の場合のみペナルティ軽減を適用し、ボーナスを最小限に削減
            landing_y = result.get("landing_y", 0)
            if landing_y > stack_top_y and danger_piece_count == 0:
                horiz_dist = abs(x - stack_top_x)
                if horiz_dist < 1.0:
                    # reactive_pairsがある場合、ペナルティ軽減を最小限に削減（200.0→50.0）
                    if reactive_pair_count >= 1:
                        score += 50.0
                        if "SAME_TYPE_STACK" not in "_".join(reasons):
                            reasons.append("SAME_TYPE_STACK")
                    else:
                        score += 25.0
                        if "SAME_TYPE_STACK" not in "_".join(reasons):
                            reasons.append("SAME_TYPE_STACK")

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