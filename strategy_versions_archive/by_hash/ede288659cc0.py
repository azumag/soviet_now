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
         8.5. Reactive pairs board compression - Bonus for dense placement when reactive_pairs >= 3 and no immediate merge (v206: reduced)
         9. Reactive pairs default - Default to REACTIVE_PAIRS_COMPRESSION when reactive_pairs >= 1 and no immediate merge
          9.5. Current type stack merge priority - v296: Same type stacking enhanced with Russian phase handling (reactive>=2:+1200.0, reactive>=1:+1000.0, reactive=0:+500.0 for Russian phase)
          9.6. Russian phase space management - v296: Post-type-15 narrow board handling, penalize too close/too far placement when only 1 type 15 exists

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
  # v316: 即時併合優先ボーナス強化版 - 即時併合機会取りこぼし削減
 # v315 failure: 即時併合優先ボーナスと戦略的配置ボーナスのバランスが悪く、reactive_pairs>=2でも即時併合不可続き戦略的配置を選択しmax_y runawayが発生
# batch_summaryでHEIGHT_CONTROLが11.4%選択(avg_score_delta=0.0)と過剰、即時併合機会取りこぼしが主要な敗因
# advice.md「盤面がどうだろうが即時併合狙った方が絶対勝率高い」に基づき、即時併合機会を最優先する戦略へ修正
# refs: tmp/state/last_rollback_postmortem.md, tmp/state/last_rollback_analysis.md, tmp/improve_brief.md, tmp/batch_summary.txt, advice.md
#       strategy_versions/best_score5694_strategy.py (即時併合優先ボーナスの良いバランスを参考)
#       game_history/20260323_060838_score0551.jsonl (reactive_pairs>=3で即時併合不可続きmax_y runaway)
# Fixes rollback failure mode: reactive_pairsがある状況で即時併合機会を取りこぼし、戦略的配置が選ばれmax_y runaway
# refs: tmp/state/last_rollback_postmortem.md, tmp/state/last_rollback_analysis.md, tmp/improve_brief.md, tmp/batch_summary.txt, advice.md

# Merge result score: type N merge gives N*(N+1)/2 points
# Example: type1+1->2 gives +3 points, type8+8->9 gives +45 points, type14+14->15 gives +120 points
SCORE_TABLE = {i: i * (i + 1) // 2 for i in range(1, 17)}

def decide(game_state: dict, analysis: dict) -> dict:
    """v315: 危険局面戦略的配置ボーナス回復版 - 即時併合不可時の盤面圧縮能力強化
    ワーストゲーム(score0551)終盤でmax_y=2.98、reactive_pairs=4-6あるのに即時併合不可続きmax_y runaway。
    v314 failure: 即時併合不可時の戦略的配置ボーナス完全削除で、危険局面の盤面圧縮能力不足。
    rollback postmortem制約「即時併合不可時に戦略的配置ボーナスを維持し、盤面圧縮を優先」を厳守。
    axis 8.6: 危険局面（dangerous_situation）で即時併合不可の場合、戦略的配置ボーナスを回復して盤面圧縮能力を強化。
      - 危険局面: max_y >= 1.8 && reactive_pairs >= 2
      - ロシアフェーズ: reactive_pairs>=2 && 即時併合不可でボーナス回復
      - 通常フェーズ: reactive_pairs>=2 && 即時併合不可 && danger_piece_count==0でボーナス回復
      - danger_piece_count > 0: 即時併合優先のためボーナス抑制
    axis 9.5: 危険局面で戦略的配置ボーナスを回復して盤面圧縮能力を強化。
    ベストゲーム(score2504)終盤でmax_y>=2.0の危険域に入っても即時併合を確実に捉え、max_y runawayを抑制してスコア稼ぎ。
    ワーストゲーム(score0551)終盤でmax_y=2.98、reactive_pairs=4-6あるのに即時併合不可続きmax_y runaway。
    batch_summaryでHEIGHT_CONTROLが11.4%選択(avg_score_delta=0.0)と過剰、即時併合機会取りこぼしが主要な敗因。
    advice.md「盤面がどうだろうが即時併合狙った方が絶対勝率高い」「高さのペナルティ回避と将来性のある配置のバランスを最適化する」を参考。
    last_rollback_analysisの制約遵守：即時併合不可時に戦略的配置ボーナスを維持し、盤面圧縮を優先。

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

    # --- reactor information (for reactive merge priority) ---
    reactor = analysis.get("reactor", {})
    reactive_pairs = reactor.get("reactive_pairs", [])
    reactive_pair_count = len(reactive_pairs) if isinstance(reactive_pairs, list) else 0

    # --- v314: 危険局面判定（候補フィルタリング用） ---
    # 条件: max_y >= 1.8 && reactive_pairs >= 2
    # ワーストゲーム(score0266)終盤でreactive_pairs>=2あるのに即時併合機会が少なく、戦略的配置が続きmax_y runaway
    # 殿堂入り戦略(best_score5694)の危険局面候補フィルタリングを導入し、即時併合機会を強制的に優先
    dangerous_situation = max_y >= 1.8 and reactive_pair_count >= 2

    # --- Russian phase detection (NEW: post-type-15 narrow board handling) ---
    # ロシア建国後フェーズ: type 15(ロシア)が盤面にある場合、ボードは狭くなり高タイプのピースが支配的
    # 2つのtype 15を併合してtype 16(ソ連)を作成する必要がある
    has_russia = any(p.get("type") == 15 for p in pieces)
    is_russian_phase = has_russia
    russian_piece_count = sum(1 for p in pieces if p.get("type") == 15)

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
    #  v314: 危険局面候補フィルタリング
    # =======================================================================
    # 危険局面(max_y >= 1.8 && reactive_pairs >= 2)の場合、即時併合機会を優先
    # 即時併合候補がある場合: merge_grade in ["DIRECT", "NEAR", "FAR"]の候補のみを評価
    # 即時併合候補がない場合: 全候補を評価（フォールバック）
    # ワーストゲーム(score0266)終盤でreactive_pairs>=2あるのに即時併合機会が少なく、戦略的配置が続きmax_y runaway
    # 殿堂入り戦略(best_score5694)の危険局面候補フィルタリングを導入し、即時併合機会を強制的に優先
    if dangerous_situation:
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
        # v197: LOW phase height_mult=0.6 enables early chain opportunities by allowing slightly higher placement
        # v294: deadline_crossed reactive_pairs board compression - v291 failure mode潰し
        # ワーストゲーム(score0323)終盤turns 44-51でdeadline_crossed=true, reactive_pairs=5-6あるのに即時併合不可、
        # 戦略的配置が続きmax_y=2.15→3.51に上昇してゲームオーバー。
        # ベストゲーム(score1716)終盤turns 81-88ではdeadline_crossed=trueでも即時併合を確実に捉えてスコア1716を出している。
        # batch_summaryでHEIGHT_CONTROLが11.8%選択(avg_score_delta=0.0)と過剰、即時併合機会取りこぼしが問題。
        # v291のaxis 2 height_mult *= 0.2 がheight_penalty計算後だったため、盤面圧縮候補が選ばれなかった。
        # axis 8.8のボーナスがaxis 2の後で評価されるため、height_penaltyと競合できていなかった。
        # v290のaxis 8.8（+300-800 at axis 7.5）が有効だったパターンを再現。
        # deadline_crossed && reactive_pair_count >= 2 && merge_grade == "NO" && danger_piece_count == 0 の場合、
        # height_multを0.2に緩和し、盤面圧縮（tighter board）を優先。即時併合機会を確保する。
        # axis 8.8の複雑ロジックを削除し、deadline_crossed時の盤面圧縮をaxis 2のheight_mult緩和に統合して簡素化。
        # advice.md「盤面がどうだろうが即時併合狙った方が絶対勝率高い」に基づき、即時併合機会を最優先する戦略へ修正。
        # last_rollback_postmortemの制約遵守：max_y>=2.0を危険域判定条件に追加しない、deadline_crossed時もSAME_TYPE_STACK有効。
        # refs: tmp/improve_brief.md, tmp/batch_summary.txt, tmp/state/last_rollback_postmortem.md, tmp/state/last_rollback_analysis.md, advice.md,
        #       game_history/20260321_040215_score0323.jsonl turns 44-51, game_history/20260321_035338_score1716.jsonl turns 81-88

        if deadline_crossed and reactive_pair_count >= 2 and merge_grade == "NO" and danger_piece_count == 0:
            # deadline_crossed時、reactive_pairsが多数ある即時併合不可時に、戦略的配置の余地を確保
            # height_multを0.2に緩和して、盤面圧縮（tighter board）を優先。即時併合機会を確保
            height_mult *= 0.2

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
            # deadline_crossed時、reactive_pairs>=1で即時併合不可の場合、戦略的配置の余地を更に確保
            # height_multを0.4に緩和して、盤面圧縮を強化し、即時併合機会を確保する
            height_mult *= 0.4

        # Calculate height penalty after all height_mult modifications
        height_penalty = landing_y * 50.0 * height_mult

        if phase == "HIGH" and landing_y > 0.5:
            height_penalty *= 2.0
            reasons.append("HIGH_TOWER")
        elif phase == "MEDIUM" and landing_y > 0.5:
            height_penalty *= 1.5
            reasons.append("MEDIUM_TOWER")
        elif landing_y > 0.0:
            reasons.append("HIGH_LAYER")

        score -= height_penalty

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


        # ----- evaluation axis 8.6: reactive pairs immediate merge bonus (v316: 即時併合優先ボーナス強化版)
        # v315 failure: 即時併合優先ボーナスが強すぎて戦略的配置の余地を確保できず、max_y runawayを引き起こしていた
        # 即時併合機会の取りこぼしを削減し、戦略的配置ボーナスを抑制して即時併合優先を強化
        # batch_summary: 即時併合関連reasonsがavg_score_delta=13.3-38.2と高価値だが選択率が4.2-7.6%と低い
        # advice.md「盤面がどうだろうが即時併合狙った方が絶対勝率高い」を参考
        # refs: tmp/state/last_rollback_postmortem.md, tmp/state/last_rollback_analysis.md, tmp/improve_brief.md, tmp/batch_summary.txt, advice.md
        
        if reactive_pair_count >= 1 and merge_grade in ["DIRECT", "NEAR"]:
            # 即時併合機会がある場合、即時併合を最優先
            if is_russian_phase:
                # ロシア建国後フェーズ：即時併合優先ボーナスを強化 (1500.0 → 1800.0)
                score += 1800.0
                reasons.append("RUSSIAN_PHASE_IMMEDIATE_MERGE_PRIORITY")
            else:
                # 通常フェーズ：即時併合優先ボーナスを強化
                if reactive_pair_count == 1:
                    score += 1000.0
                    reasons.append("REACTIVE_IMMEDIATE_MERGE_PRIORITY")
                elif reactive_pair_count >= 2 and reactive_pair_count < 3:
                    score += 1500.0
                    reasons.append("REACTIVE_IMMEDIATE_MERGE_PRIORITY")
                else:
                    # reactive_pairs >= 3
                    score += 1800.0
                    reasons.append("REACTIVE_IMMEDIATE_MERGE_PRIORITY")
        elif reactive_pair_count >= 2 and merge_grade == "NO":
            # 即時併合がない場合、戦略的配置ボーナスを抑制して盤面圧縮を優先
            # 危険ピースがある場合は、即時併合を優先
            if danger_piece_count > 0:
                # 危険ピースがある場合、即時併合優先のため戦略的配置ボーナスを抑制
                height_mult *= 0.5
            elif is_russian_phase:
                # ロシア建国後フェーズ：即時併合不可時に盤面圧縮を優先
                if reactive_pair_count >= 3:
                    # reactive_pairsが多い場合、より大きい戦略的配置ボーナスを設定
                    score += 150.0
                    reasons.append("RUSSIAN_PHASE_STRATEGIC_PLACEMENT")
                    height_mult *= 0.6
                elif reactive_pair_count == 2:
                    score += 100.0
                    reasons.append("RUSSIAN_PHASE_STRATEGIC_PLACEMENT")
                    height_mult *= 0.7
                else:
                    # unreachable: reactive_pairs >= 2 is the outer condition
                    pass
            else:
                # 通常フェーズ：即時併合不可時に盤面圧縮を優先
                if reactive_pair_count >= 3:
                    score += 150.0
                    reasons.append("STRATEGIC_PLACEMENT")
                    height_mult *= 0.6
                elif reactive_pair_count == 2:
                    score += 100.0
                    reasons.append("STRATEGIC_PLACEMENT")
                    height_mult *= 0.7
                # unreachable: reactive_pairs >= 2 is the outer condition, no else needed
        elif reactive_pair_count == 1 and merge_grade == "NO":
            # reactive_pairs==1 && 即時併合不可の場合、戦略的配置ボーナスを抑制
            if danger_piece_count > 0:
                # 危険ピースがある場合、即時併合優先のため戦略的配置ボーナスを抑制
                height_mult *= 0.6
            elif is_russian_phase:
                # ロシア建国後フェーズ：即時併合不可時に盤面圧縮を優先
                score += 50.0
                reasons.append("STRATEGIC_PLACEMENT")
                height_mult *= 0.7
            else:
                # 通常フェーズ：即時併合不可時に盤面圧縮を優先
                score += 50.0
                reasons.append("STRATEGIC_PLACEMENT")
                height_mult *= 0.7
        else:
            # 即時併合機会がない場合、戦略的配置ボーナスを抑制
            if reactive_pair_count == 0:
                # 即時併合機会がない場合、戦略的配置ボーナスを抑制
                height_mult *= 0.8
       
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

         # ----- evaluation axis 9.5: current type stack merge priority (v315: 危険局面戦略的配置ボーナス回復版)
         # v284: reactive_pairs活用盤面圧縮強化版
         # v295: Russian phase handling added for post-type-15 narrow board
         # v297: v306 rollback failure mode潰し - reactive_pairs>=3時の戦略的配置ボーナス削除
         # v313: reactive_pairs>=2時の戦略的配置ボーナス完全削除版 - 即時併合不可時のmax_y runaway防止
         # v314 failure: 即時併合不可時の戦略的配置ボーナス完全削除で、危険局面の盤面圧縮能力不足
         # v315: 危険局面で戦略的配置ボーナスを回復して盤面圧縮能力を強化

         # advice.md「同じタイプが続いて来たらそのタイプの上に置き、併合チャンスを優先する」を強化。
         # batch_summaryでHEIGHT_CONTROLが11.0%選択(avg_score_delta=0.0)と過剰であり、即時併合機会を取りこぼしていることを確認。
         # 盤面上の現在タイプの最も高い位置のピースに配置を優先し、即時併合機会を最大化。
         # reactive_pairsがある場合、危険局面で戦略的配置ボーナスを回復して盤面圧縮と将来の併合を同時に狙う戦略的思考へ切り替える。
         # 危険ピースがない場合にreactive_pairsがある状況で即時併合不可の場合、このaxisで戦略的配置の余地を確保
         # v295: ロシア建国後フェーズ（type 15存在時）に即時併合優先を強化し、狭いボードでの第二type 15準備を支援
         # v315: 危険局面で戦略的配置ボーナスを回復して盤面圧縮能力を強化
         # refs: tmp/state/last_rollback_postmortem.md, tmp/state/last_rollback_analysis.md, tmp/improve_brief.md, tmp/batch_summary.txt, advice.md
         #       game_history/20260323_060838_score0551.jsonl, game_history/20260323_055916_score2504.jsonl

        if same_type_stack_top and merge_grade == "NO":
            stack_top_x = same_type_stack_top.get("x", 0)
            stack_top_y = same_type_stack_top.get("y", -10)

            if danger_piece_count == 0:
                if is_russian_phase:
                    # ロシア建国後フェーズ：即時併合機会を最優先、狭いボードでの第二type 15準備
                    if reactive_pair_count >= 2:
                        # v315: 危険局面で戦略的配置ボーナスを回復
                        if dangerous_situation:
                            # 危険局面: reactive_pairs>=2 && max_y>=1.8
                            # 即時併合不可の場合、戦略的配置ボーナスを回復して盤面圧縮能力を確保
                            score += 200.0
                            reasons.append("RUSSIAN_PHASE_DANGER_SAME_TYPE_STACK")
                            height_mult *= 0.6
                        else:
                            # 非危険局面: height_mult緩和のみ
                            height_mult *= 0.6
                    elif reactive_pair_count >= 1:
                        # v315: 危険局面で戦略的配置ボーナスを回復
                        if dangerous_situation:
                            # 危険局面: reactive_pairs>=1 && max_y>=1.8
                            # 即時併合不可の場合、戦略的配置ボーナスを回復して盤面圧縮能力を確保
                            score += 100.0
                            reasons.append("RUSSIAN_PHASE_DANGER_SAME_TYPE_STACK")
                            height_mult *= 0.7
                        else:
                            # 非危険局面: height_mult緩和のみ
                            height_mult *= 0.7
                    else:
                        # reactive_pairsがない場合も、ロシアフェーズでは即時併合を重視
                        score += 25.0
                        reasons.append("SAME_TYPE_STACK_MERGE_PRIORITY_RUSSIAN")
                        height_mult *= 0.8
                else:
                    # 通常フェーズ
                    if reactive_pair_count >= 2:
                        # v315: 危険局面で戦略的配置ボーナスを回復
                        if dangerous_situation:
                            # 危険局面: reactive_pairs>=2 && max_y>=1.8
                            # 即時併合不可の場合、戦略的配置ボーナスを回復して盤面圧縮能力を確保
                            score += 200.0
                            reasons.append("DANGER_SAME_TYPE_STACK")
                            height_mult *= 0.5
                        else:
                            # 非危険局面: height_mult緩和のみ
                            height_mult *= 0.5
                    elif reactive_pair_count >= 1:
                        score += 100.0
                        reasons.append("SAME_TYPE_STACK_MERGE_PRIORITY_REACTIVE")
                        height_mult *= 0.7
                    else:
                        # danger_piece_count > 0 の場合は即時併合優先が適用されるためボーナスを抑制
                        # axis 8.5の即時併合優先評価を妨げないよう、最小限のボーナスを維持
                        if reactive_pair_count >= 1:
                            score += 50.0
                            reasons.append("SAME_TYPE_STACK_MERGE_PRIORITY_DANGER")
                        # 危険ピースがある場合、戦略的配置の余地を最小限に抑制
                        height_mult *= 0.7
                # 配置位置が盤面上の現在タイプのピースの上になる場合、ペナルティ軽減を強化
                # danger_piece_count == 0 の場合のみペナルティ軽減を適用
                landing_y = result.get("landing_y", 0)
                if landing_y > stack_top_y:
                    horiz_dist = abs(x - stack_top_x)
                    if horiz_dist < 1.0:
                        # v315: 危険局面でペナルティ軽減を回復
                        if dangerous_situation and reactive_pair_count >= 3:
                            # 危険局面: reactive_pairs>=3 && max_y>=1.8
                            # 即時併合不可の場合、戦略的配置を回復して盤面圧縮能力を確保
                            score += 50.0
                            reasons.append("DANGER_SAME_TYPE_STACK")
                        elif reactive_pair_count >= 1:
                            score += 50.0
                            if "SAME_TYPE_STACK" not in "_".join(reasons):
                                reasons.append("SAME_TYPE_STACK")
                        else:
                            score += 25.0
                            if "SAME_TYPE_STACK" not in "_".join(reasons):
                                reasons.append("SAME_TYPE_STACK")

        # ----- evaluation axis 9.6: Russian phase space management (NEW: post-type-15 narrow board handling) -----
        # ロシア建国後フェーズで、type 15(ロシア)が1つしかない場合、第二type 15を配置するための空間管理
        # 狭いボードなので、既存のtype 15の近くに配置する際は注意が必要
        if is_russian_phase and russian_piece_count == 1 and merge_grade == "NO":
            # 2番目のtype 15を配置するための空間を確保
            # 既存のtype 15の位置を取得
            russia_pieces = [p for p in pieces if p.get("type") == 15]
            if russia_pieces:
                russia_piece = russia_pieces[0]
                russia_x = russia_piece.get("x", 0)
                russia_y = russia_piece.get("y", -10)

                # 既存のtype 15の真上に配置しようとする場合、第二type 15のスペースを確保するためにペナルティ
                landing_y = result.get("landing_y", 0)
                horiz_dist = abs(x - russia_x)

                if landing_y > russia_y and horiz_dist < 1.5:
                    # 既存のtype 15の近くに配置する場合、第二type 15を配置するためのスペースを確保
                    # ペナルティを与えて、より広い場所を探索させる
                    score -= 300.0
                    reasons.append("RUSSIAN_PHASE_SPACE_MANAGEMENT")
                elif horiz_dist > 2.5:
                    # 既存のtype 15から離れすぎた場合、第二type 15の併合が困難になるためペナルティ
                    score -= 200.0
                    reasons.append("RUSSIAN_PHASE_PROXIMITY_PENALTY")

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