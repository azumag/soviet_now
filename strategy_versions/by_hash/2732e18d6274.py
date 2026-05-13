#!/usr/bin/env python3
"""strategy.py - Soviet Puzzle Game AI Drop Position Script

Game Overview:
  - Drop pieces, merge same type pieces (N+N -> N+1)
  - Score table: type1=1, type2=3, type3=6, ..., typeN = N*(N+1)/2
  - Board: x in [-3.0, +3.0], floor y=-4.48, deadline y=3.32
  - Player controls only drop X coordinate

Decision Logic (8 evaluation axes):
   1. Merge bonus - High score for immediate merge (DIRECT > NEAR > FAR)
   2. Height penalty - Penalty for high landing position (varies by phase, early_game: max_y < -2.0)
   3. Drift penalty - Penalty for post-landing drift due to polygon shape
   4. Left-right balance correction - Bonus for correcting piece count bias
   5. nextNext centering - Center for next merge opportunity if nextNext same type
   6. Chain merge bonus - Evaluate possibility of further merges after merge (v171: CHAIN_MERGE基本ボーナス強化)
   7. Early game merge priority - Strong bonus for merge opportunities in early game (v174)
   8. Reactive merge priority - Bonus for merge opportunities when reactive_pairs >= 2 (v176)

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
# [BEST:3689] v126: v42-based HIGH phase merge enhancement
# [BEST:4026] v155: chain_distance 4.5→5.0, chain_bonus 400.0→450.0 achieved best score 4026
# [BEST:5310] v156: v42/v126成功構造復帰・CHAIN_MERGE削除版
# v162: MEDIUMフェーズバランス補正強化版 - balance_strength 35.0→40.0
# v159: 序盤HEIGHT_CONTROL抑制強化版 - max_y < -1.0, height_multiplier=0.2
# v167: 評価精度最適化版 - chain_distance 5.0→4.5縮小
# v168: v155成功パラメータ復帰・動的調整復帰版
# v169: 序盤HEIGHT_CONTROL抑制超拡大・CHAIN_MERGE評価範囲拡大版 - batch_summaryでHEIGHT_CONTROLが25.2%選択(avg_score_delta=1.4)と過剰であること、
# ワーストゲーム(score0554)で初期11ターンのうち8ターンがHEIGHT_CONTROL/NEXT_SAMEとなり併合機会を逃していることを確認。
# early_game判定をmax_y < -1.0→-3.0に超拡大し、chain_distance_maxを5.0→5.2に拡大して、CHAIN_MERGE選択率を10-15%に引き上げる。
# v170: MEDIUM phase height penalty relaxation版 - batch_summaryでMEDIUM_TOWERがavg_score_delta=3.4（正の値）だが選択率が10.8%（低スコア群）と低いことを確認。
# 高スコア群と低スコア群の比較でMEDIUM_TOWER選択率に13.6% vs 10.8%の差があることを特定。
# MEDIUM phase height_multを1.8→1.4に削減してMEDIUM_TOWER選択を促進し、HEIGHT_CONTROL選択を削減することでスコア安定性を向上させる。
# v171: CHAIN_MERGE基本ボーナス強化版 - batch_summaryでCHAIN_MERGE関連がavg_score_delta=26.9-43.2（高価値）だが選択率は3.8-9.2%と低いことを確認。
# ワーストゲーム(score0633)で初期5ターンが全てHEIGHT_CONTROLとなり、CHAIN_MERGE選択が0回であることを特定。
# chain_distance_max基本値を5.2→5.0に戻し（v155成功値）、chain_bonus_multiplier初期値を450.0→480.0に強化して初期段階でのCHAIN_MERGE選択を促進。
# 着地高による動的調整（landing_y*150.0）は維持し、初期段階と中盤以降の両方でCHAIN_MERGE選択を向上させる。
# v172: 序盤マージ優先評価軸追加版 - batch_summaryでHEIGHT_CONTROLが25.9%選択(avg_score_delta=1.6)と過剰であり、低スコア群で30.3%選択されていることを確認。
# ワーストゲーム(score0545)で初期5ターンが全てHEIGHT_CONTROLとなり併合機会を逃している失敗モードを特定。
# early_game条件下でmerge_gradeがNEARの場合、追加ボーナス800.0を付与する評価軸を追加し、初期段階でのマージ機会を最優先してHEIGHT_CONTROL選択を超強力に抑制する。
# v173: early_game判定緩和・初期10ターンマージ重視版 - batch_summaryでHEIGHT_CONTROLが26.7%選択(avg_score_delta=1.3)と依然として過剰であることを確認。
# ワーストゲーム(score0678)で初期10ターンのmax_y推移(-5.0→-2.4)を分析し、v172のearly_game判定(max_y < -3.0)が過度に厳しく
# 初期10ターンの大部分で判定されていないことを特定。
# early_game判定をmax_y < -3.0→-2.5に緩和し、EARLY_MERGE_PRIORITYの適用範囲をearly_gameからpiece_count <= 10に拡大して
# 初期10ターン全体でマージ機会を最優先する。
# また、初期段階(piece_count <= 6)で併合機会がない場合のHEIGHT_CONTROL抑制を強化し、初期配置での消極的戦略を回避する。
# v174: early_game判定さらに緩和・初期12ターンマージ重視版 - batch_summaryでHEIGHT_CONTROLが26.2%選択(avg_score_delta=1.7)と依然として過剰であることを確認。
# ワーストゲーム(score0765)で初期7ターン全てHEIGHT_CONTROLを選択し、マージ機会を逃している失敗パターンを特定。
# early_game判定をmax_y < -2.5→-2.0にさらに緩和し、EARLY_MERGE_PRIORITYの適用範囲をpiece_count <= 10→12に拡大して初期12ターン全体でマージ機会を最優先する。
# また、MEDIUM_TOWER選択を促進するための追加評価軸を追加し、高スコア群と低スコア群のMEDIUM_TOWER選択率差（13.6% vs 10.8%）を解消する。
  # v175: MEDIUMフェーズHEIGHT_CONTROL抑制強化版 - batch_summaryでHEIGHT_CONTROLが26.5%選択(avg_score_delta=1.1)と依然として過剰であり、スコアの標準偏差が404.0と大きいことを確認。
  # v174で初期12ターンでのHEIGHT_CONTROL抑制は強化されたが、中盤以降のHEIGHT_CONTROL選択が依然として多く、これがスコアの不安定性を引き起こしていることを特定。
  # MEDIUMフェーズ（0.8 <= max_y < 1.8）でheight_multiplierを30.0→20.0に削減し、マージ選択を促進することでスコア安定性を向上させる。
  # v176: reactor情報活用によるマージ優先評価軸追加版 - batch_summaryでHEIGHT_CONTROLが26.2%選択(avg_score_delta=1.7)と依然として過剰であることを確認。
  # reactor情報のreactive_pairs（反応性のあるペア）を活用し、2つ以上ある場合にマージを優先する評価軸を追加。
  # これにより、盤面に多数の併合機会がある状況でHEIGHT_CONTROL選択を抑制し、スコア安定性を向上させる。
  # v178: CRITICALフェーズ危険高さ抑制版 - batch_summaryで高スコア群が終盤avg=1.77、低スコア群が終盤avg=1.93であることを確認。
  # アドバイスより「ゲームオーバー付近で併合判断が適切に行われていない」問題に対処。
  # CRITICALフェーズ(max_y >= 3.0)でlanding_y > 2.0の場合、追加ペナルティ500.0を付与し、危険な高さ配置を強力に抑制する。

# Merge result score: type N merge gives N*(N+1)/2 points
# Example: type1+1->2 gives +3 points, type8+8->9 gives +45 points, type14+14->15 gives +120 points
SCORE_TABLE = {i: i * (i + 1) // 2 for i in range(1, 17)}


def decide(game_state: dict, analysis: dict) -> dict:
    """v178: CRITICALフェーズ危険高さ抑制版

    batch_summaryで高スコア群が終盤avg=1.77、低スコア群が終盤avg=1.93であることを確認。
    アドバイスより「ゲームオーバー付近で併合判断が適切に行われていない」問題に対処。
    CRITICALフェーズ(max_y >= 3.0)でlanding_y > 2.0の場合、追加ペナルティ500.0を付与し、危険な高さ配置を強力に抑制する。

    v178の改善点:
    1. CRITICALフェーズ危険高さ抑制
       - landing_y > 2.0の場合、追加ペナルティ500.0を付与
       - ゲームオーバー直前の危険な配置を回避し、盤面を下げる選択を促進
    2. v177のMEDIUMフェーズHEIGHT_CONTROL抑制を維持
       - height_multiplier: 15.0（MEDIUMフェーズ）
    3. v176のreactor情報活用によるマージ優先評価軸を維持
       - reactive_pair_count >= 2の場合、DIRECT/NEARマージに+500.0ボーナス
    4. v174の初期12ターンマージ重視を維持
       - early_game判定(max_y < -2.0)とEARLY_MERGE_PRIORITY(piece_count <= 12)を維持
    5. v171のCHAIN_MERGE基本ボーナス強化を維持
       - chain_distance_max=5.0とchain_bonus_multiplier初期値480.0でCHAIN_MERGE選択を促進

    Args:
        game_state: game state (pieces, next, nextNext, score, etc.)
        analysis: analyze_board.py analysis results
            - results: landing information for each drop X candidate
                - x: drop X coordinate
                - landing_y: estimated landing Y coordinate (high=dangerous)
                - drift_x/drift_unc: post-landing drift due to polygon shape
                - merge_grade: best merge judgment (DIRECT/NEAR/FAR/NO)
                - merges: individual distance/merge judgment for each same-type piece
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
    piece_count = len(pieces)

    # --- v174: early_game判定さらに緩和（max_y < -2.0） ---
    # batch_summaryでHEIGHT_CONTROLが26.2%選択(avg_score_delta=1.7)と依然として過剰であることを確認。
    # ワーストゲーム(score0765)で初期7ターン全てHEIGHT_CONTROLを選択し、マージ機会を逃している失敗パターンを特定。
    # early_game判定をmax_y < -2.5→-2.0にさらに緩和し、初期12ターンの大部分で判定されるように調整。
    early_game = max_y < -2.0

    # --- phase judgment (v42 thresholds) ---
    if max_y < 0.8:
        phase = "LOW"
        height_mult = 1.0  # low board weak height penalty
        merge_mult = 1.2  # 20% merge bonus increase, actively target
    elif max_y < 1.8:
        phase = "MEDIUM"
        height_mult = 1.4  # v170: MEDIUM phase height penalty relaxation (1.8->1.4) to increase MEDIUM_TOWER selections
        merge_mult = 1.0
    elif max_y < 3.0:
        phase = "HIGH"
        height_mult = 1.8  # HIGH relaxation to ensure merge opportunity
        merge_mult = 1.0
    else:
        phase = "CRITICAL"
        height_mult = 1.0  # CRITICAL height penalty basic value only
        merge_mult = 0.6  # v42: CRITICAL phase merge suppression

    # --- reactor information (for merge opportunity evaluation) ---
    reactor = analysis.get("reactor", {})
    reactive_pairs = reactor.get("reactive_pairs", [])
    # reactive_pairs is a list, count pairs for evaluation
    reactive_pair_count = len(reactive_pairs) if isinstance(reactive_pairs, list) else 0

    # --- next piece information ---
    next_piece = game_state.get("next", {})
    next_next_piece = game_state.get("nextNext", {})
    next_type = next_piece.get("type", 0)
    next_next_type = next_next_piece.get("type", 0)

    # --- Type-specific merge bonus calculation ---
    # merge result type (next_type+1) higher means higher score value
    # example: type1 merge -> bonus=330, type5 merge -> bonus=510, type14 merge -> bonus=1660
    merge_result_type = min(next_type + 1, 16)
    type_merge_bonus = SCORE_TABLE.get(merge_result_type, 10) * 10 + 300

    # --- v149: pre-calculate merged type (for chain judgment) ---
    merged_type = min(next_type + 1, 16)

    # =======================================================================
    #  score each drop candidate (x coordinate) with 7 evaluation axes
    # =======================================================================
    for result in results:
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
        # v169: early_game（max_y < -2.0）の場合、height_multiplierを0.2に削減してHEIGHT_CONTROL過剰選択を抑制
        # v170: MEDIUM phase height_multを1.8→1.4に削減してMEDIUM_TOWER選択を促進
        # v173: 初期段階(piece_count <= 6)で併合機会がない場合、HEIGHT_CONTROL抑制を強化
        # v174: early game判定をさらに緩和し、初期12ターンでHEIGHT_CONTROLを抑制
        # v175: MEDIUM phase height_multiplierを30.0→20.0に削減してHEIGHT_CONTROL抑制を強化
        height_multiplier = 30.0
        if early_game:
            height_multiplier = 0.2  # v169: 序盤はHEIGHT_CONTROLを抑制し、併合機会を最優先

        # v175: MEDIUMフェーズでHEIGHT_CONTROL抑制を強化
        if phase == "MEDIUM":
            height_multiplier = 15.0  # v177: v175からさらに緩和しマージ選択を促進

        # v173: 初期段階で併合機会がない場合、HEIGHT_CONTROL抑制をさらに強化
        if piece_count <= 6 and merge_grade == "NO":
            height_multiplier = 0.1  # 初期6ピースでマージ機会がない場合、消極的配置を回避

        height_penalty = landing_y * height_multiplier * height_mult

        if phase == "CRITICAL" and landing_y > 2.0:
            height_penalty += 500.0
            reasons.append("DANGER_HIGH_PLACEMENT")
        elif phase == "HIGH" and landing_y > 0.5:
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

        # ----- evaluation axis 4: left-right balance correction (v162: enhanced) -----
        # bonus for correcting left-right piece count bias.
        # balance_bias > 0 means right majority -> left (x<0) placement reduces penalty
        # v162: MEDIUM phase balance correction enhanced (35.0->40.0)
        balance_strength = 20.0
        if phase == "HIGH":
            balance_strength = 50.0  # v148: HIGH balance control even stricter (40.0->50.0)
        elif phase == "MEDIUM":
            balance_strength = 40.0  # v162: MEDIUM phase balance correction enhanced (35.0->40.0)

        left_count = sum(1 for p in pieces if p["x"] < 0)
        right_count = len(pieces) - left_count
        balance_bias = (right_count - left_count) / (len(pieces) if pieces else 1)

        balance_penalty = x * balance_bias * balance_strength
        score -= abs(balance_penalty)

        # ----- evaluation axis 5: nextNext centering (modified to discourage center placement without chain potential) -----
        # if nextNext same type as current next, next also has merge opportunity.
        # place near center to allow merge in either direction next turn
        # Modified: Analyze if current merge can lead to nextNext merge after current merge (2-lookahead)
        if next_next_type == next_type and merge_grade in ["DIRECT", "NEAR"]:
            # Check if merging current piece enables nextNext merge (2-lookahead)
            merged_type = min(next_type + 1, 16)
            next_next_merged_type = min(next_next_type + 1, 16)
            
            # Calculate if any merged_type pieces are within chain_distance_max of merged target
            chain_distance_max = 5.0 + landing_y * 0.6
            chain_bonus_multiplier = 480.0 + landing_y * 150.0
            
            if result.get("merges"):
                merges = result["merges"]
                if merges:
                    best_merge = min(merges, key=lambda m: m.get("dist", float("inf")))
                    target_x = best_merge.get("x", 0)
                    target_y = best_merge.get("y", 0)
                    
                    # Check if any merged_type pieces can merge with nextNext (nextNext -> nextNext_next)
                    nearby_for_next = []
                    for p in pieces:
                        if p.get("type") == next_next_merged_type:
                            dist_to_target = ((p["x"] - target_x) ** 2 + (p["y"] - target_y) ** 2) ** 0.5
                            if dist_to_target < chain_distance_max:
                                nearby_for_next.append((dist_to_target, p))
                    
                    # If we have at least 2 chain opportunities (current merge + nextNext merge), discourage center placement
                    if len(nearby_for_next) >= 2:
                        # If center placement is chosen, reduce the bonus to 0
                        center_bonus = max(0, 1.0 - abs(x) / 2.0) * 0.0
                    else:
                        # No 2-lookahead opportunity, normal center bonus
                        center_bonus = max(0, 1.0 - abs(x) / 2.0) * 50.0
                    
                    score += center_bonus
                    reasons.append("NEXT_SAME_CHAIN")
            else:
                # No current merge, normal center bonus
                center_bonus = max(0, 1.0 - abs(x) / 2.0) * 50.0
                score += center_bonus
                reasons.append("NEXT_SAME")
        else:
            # Different types, normal center bonus
            center_bonus = max(0, 1.0 - abs(x) / 2.0) * 50.0
            score += center_bonus
            reasons.append("NEXT_SAME")

        # ----- evaluation axis 6: chain merge bonus (v171: CHAIN_MERGE基本ボーナス強化) -----
        # v171: CHAIN_MERGE関連がavg_score_delta=26.9-43.2（高価値）だが選択率は3.8-9.2%と低いことを確認。
        # ワーストゲーム(score0633)で初期5ターンが全てHEIGHT_CONTROLとなり、CHAIN_MERGE選択が0回であることを特定。
# v171: CHAIN_MERGE基本ボーナス強化
# chain_distance_max = 5.0 + landing_y * 0.6 (v155成功値に戻す、着地高に応じて拡大)
# chain_bonus_multiplier = 480.0 + landing_y * 150.0 (初期値を450.0→480.0に強化、着地高に応じて増強)
# 例: landing_y=-3.0 → distance_max=3.2, multiplier=30.0（初期段階）
# 例: landing_y=0.0 → distance_max=5.0, multiplier=480.0（初期値強化）
# 例: landing_y=1.0 → distance_max=5.6, multiplier=630.0
# 例: landing_y=2.0 → distance_max=6.2, multiplier=780.0
# 例: landing_y=3.0 → distance_max=6.8, multiplier=930.0
        if merge_grade in ["DIRECT", "NEAR"] and result.get("merges"):
            merges = result["merges"]
            if merges:
                # get best merge target (closest distance)
                best_merge = min(merges, key=lambda m: m.get("dist", float("inf")))
                target_x = best_merge.get("x", 0)
                target_y = best_merge.get("y", 0)

                # v171: CHAIN_MERGE基本ボーナス強化
                # chain_distance_max = 5.0 + landing_y * 0.6 (v155成功値に戻す、着地高に応じて拡大)
                # chain_bonus_multiplier = 480.0 + landing_y * 150.0 (初期値を450.0→480.0に強化、着地高に応じて増強)
                # 例: landing_y=-3.0 → distance_max=3.2, multiplier=30.0（初期段階）
                # 例: landing_y=0.0 → distance_max=5.0, multiplier=480.0（初期値強化）
                # 例: landing_y=1.0 → distance_max=5.6, multiplier=630.0
                # 例: landing_y=2.0 → distance_max=6.2, multiplier=780.0
                # 例: landing_y=3.0 → distance_max=6.8, multiplier=930.0
                chain_distance_max = 5.0 + landing_y * 0.6
                chain_bonus_multiplier = 480.0 + landing_y * 150.0

                # collect all merged_type pieces within chain_distance_max of merge target
                nearby_pieces = []
                for p in pieces:
                    if p.get("type") == merged_type:
                        dist = ((p["x"] - target_x) ** 2 + (p["y"] - target_y) ** 2) ** 0.5
                        if dist < chain_distance_max:
                            nearby_pieces.append((dist, p))

                # sort by distance (closest first)
                nearby_pieces.sort(key=lambda x: x[0])

                # v171: CHAIN_MERGE基本ボーナス強化 - 3つの最も近いピースに対し、距離に応じて減衰するボーナスを適用
                # chain_distance_max=5.0（v155成功値）とchain_bonus_multiplier初期値480.0（強化）で初期段階でのCHAIN_MERGE選択を促進
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

        # ----- evaluation axis 7: early game merge priority (v174: 初期12ターンマージ重視) -----
        # v174: early_game判定(max_y < -2.0)をさらに緩和し、EARLY_MERGE_PRIORITYの適用範囲をpiece_count <= 10→12に拡大。
        # 初期12ターンを一つのフェーズとして扱い、この期間中はマージ機会を最優先してHEIGHT_CONTROL選択を抑制する。
        # v172の初期条件(early_game && merge_grade == "NEAR")を維持し、piece_count <= 12でも適用することで初期12ターン全体でマージを重視。
        if (early_game or piece_count <= 12) and merge_grade == "NEAR":
            # 初期段階でNEAR_MERGE機会がある場合、強力なボーナスを付与
            # これにより初期12ターン全体でマージ機会を最優先し、HEIGHT_CONTROL選択を抑制
            score += 800.0
            reasons.append("EARLY_MERGE_PRIORITY")

        # ----- v176: reactive_pairs-based merge priority -----
        # batch_summary分析でHEIGHT_CONTROLが26.5%選択(avg_score_delta=1.1)と過剰であることを確認。
        # reactor情報のreactive_pairs（反応性のあるペア）が2つ以上ある場合、盤面に多数の併合機会があることを示唆。
        # この状況でマージを優先することでHEIGHT_CONTROL選択を抑制し、スコア安定性を向上させる。
        if reactive_pair_count >= 2 and merge_grade in ["DIRECT", "NEAR"]:
            score += 500.0
            reasons.append("REACTIVE_MERGE_PRIORITY")

        # ----- evaluation axis 8: MEDIUM_TOWER selection promotion (v174) -----
        # v174: MEDIUM_TOWER選択を促進するための追加評価軸を追加し、高スコア群と低スコア群のMEDIUM_TOWER選択率差（13.6% vs 10.8%）を解消する。
        # MEDIUM phaseでlanding_y > 0.5の場合、MEDIUM_TOWERボーナスを追加して選択を促進。
        if phase == "MEDIUM" and landing_y > 0.5:
            # MEDIUM_TOWER選択を促進するための追加ボーナス
            # MEDIUM_TOWERが高スコア群で13.6%選択されているのに対し、低スコア群では10.8%しか選択されていない
            # MEDIUM_TOWER選択を促進することで、スコア安定性を向上させる
            score += 200.0
            reasons.append("MEDIUM_TOWER_PROMOTION")

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
