#!/usr/bin/env python3
"""strategy.py - Soviet Puzzle Game AI Drop Position Script

Game Overview:
  - Drop pieces, merge same type pieces (N+N -> N+1)
  - Score table: type1=1, type2=3, type3=6, ..., typeN = N*(N+1)/2
  - Board: x in [-3.0, +3.0], floor y=-4.48, deadline y=3.32
  - Player controls only drop X coordinate

Decision Logic (7 evaluation axes):
  1. Merge bonus - High score for immediate merge (DIRECT > NEAR > FAR)
  2. Height penalty - Penalty for high landing position (varies by phase)
  3. Drift penalty - Penalty for post-landing drift due to polygon shape
  4. Left-right balance correction - Bonus for correcting piece count bias
  5. nextNext centering - Center for next merge opportunity if nextNext same type
  6. Chain merge bonus - Evaluate possibility of further merges after merge (v160: early_game enhancement)
  7. Board dispersion bonus - Maximize left-right spread in early game with no merge (v3439: NEW)

Phases (determined by board max Y):
  LOW      (max_y < 0.8) : Early game. Merge priority (merge_mult=1.2)
  MEDIUM   (0.8 <= max_y < 1.8) : Mid game. Height management (height_mult=2.2)
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
# v151-v156: CHAIN_MERGE強化版（係数200.0→300.0→400.0、chain_distance 3.0→3.5→4.0→4.5→5.0）
# v157: 着地高動的調整・CHAIN_MERGE促進版 - v156のheight_multiplier抑制（40.0）でもHEIGHT_CONTROL選択率が高い問題を解決。
# 単純なパラメータ調整ではなく、構造的改善として着地高に応じた動的調整を導入。
# landing_yが高いほどchain_distance_maxを拡大（5.0 + landing_y*0.6）し、chain_bonus_multiplierも強化（450.0 + landing_y*150.0）することで、
# HIGH_LAYER状況でのCHAIN_MERGE選択を強制的に誘導し、HEIGHT_CONTROLの選択を減らしてスコア安定性を向上させる。
# v158: HEIGHT_CONTROL抑制精度化版 - batch_summary分析でHEIGHT_CONTROLが29.1%選択されavg_score_delta=0.8と効果がないこと、
# 低スコア群がHEIGHT_CONTROLを31.5%選択していること、序盤（max_y < -2.0）で盤面が高さを稼げない失敗パターンを確認。
# v157の動的調整を維持しつつ、(1) chain_distance_maxのベース値を5.0→4.0に縮小して評価精度を向上し、(2) 序盤（max_y < -2.0）のheight_multiplierを0.3に削減してHEIGHT_CONTROL過剰選択を抑制。
# v159: 序盤HEIGHT_CONTROL抑制強化版 - v158のHEIGHT_CONTROL抑制が不十分でHEIGHT_CONTROL選択率が依然として高い（30.5%）。
# v158のchain_distance_max=4.0がv155の成功パラメータ（5.0）より狭く、CHAIN_MERGE選択率が低下（7.6-9.6%）。
# (1) 序盤判定をmax_y < -2.0 → max_y < -1.0に拡大しheight_multiplierを0.3→0.2に削減してHEIGHT_CONTROL選択を25%未満に抑制。
# (2) chain_distance_maxのベース値を4.0→5.0に戻し（v155成功パラメータ復帰）、CHAIN_MERGE選択率を15%以上に向上。
# v3439: 初期段階での盤面分散評価軸追加版 - batch_summaryでHEIGHT_CONTROLが低スコア群で32.0%選択されavg_score_delta=3.0と効果が低いこと、
# 高スコア群は初期から高めに配置（序盤avg=-2.2）し、低スコア群は初期から低すぎ（序盤avg=-2.78）していることを確認。
# 初期5ピースかつマージ機会がない場合、盤面の左右分散を最大化する評価軸を追加。
# 最も左と右のピースの位置を計算し、現在の左右範囲外の位置にボーナスを付与。
# 範囲外への距離に応じたボーナス：距離3.0で50.0、距離5.0以上で150.0（最大）。
# これにより、初期段階での盤面分散を促進し、マージ機会を増加させる。

# Merge result score: type N merge gives N*(N+1)/2 points
# Example: type1+1->2 gives +3 points, type8+8->9 gives +45 points, type14+14->15 gives +120 points
SCORE_TABLE = {i: i * (i + 1) // 2 for i in range(1, 17)}


def decide(game_state: dict, analysis: dict) -> dict:
    """v3439: 初期段階での盤面分散評価軸追加版

    batch_summaryでHEIGHT_CONTROLが低スコア群で32.0%選択されavg_score_delta=3.0と効果が低いこと、
    高スコア群は初期から高めに配置（序盤avg=-2.2）し、低スコア群は初期から低すぎ（序盤avg=-2.78）していることを確認。
    v159のHEIGHT_CONTROL抑制（height_multiplier=0.2）が不十分で、初期段階での盤面分散が不足している。

    v159の改善点に加え、以下の変更を実装：
    1. 初期5ピースかつマージ機会がない場合、盤面の左右分散を最大化する評価軸を追加
       - 最も左と右のピースの位置を計算し、現在の左右範囲外の位置にボーナスを付与
       - 範囲外への距離に応じたボーナス：距離3.0で50.0、距離5.0以上で150.0（最大）
       - これにより、初期段階での盤面分散を促進し、マージ機会を増加させる
    2. v159のearly_game判定（max_y < -1.0）とheight_multiplier=0.2を維持
    3. v159のchain_distance_max=5.0とchain_bonus_multiplier動的調整を維持

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

    # --- v3439: 初期5ピースかつマージ機会がない場合の盤面分散ボーナス計算準備 ---
    # 初期段階（5ピース以下）でマージ機会がない場合、盤面の左右分散を最大化する
    early_game_no_merge = piece_count <= 5
    if pieces:
        min_x = min(p["x"] for p in pieces)
        max_x = max(p["x"] for p in pieces)
    else:
        min_x = max_x = 0.0

    # --- v159: 序盤判定（max_y < -1.0） ---
    # v158のmax_y < -2.0でのHEIGHT_CONTROL抑制が不十分。より広範囲（max_y < -1.0）で抑制し、height_multiplierを0.2に削減。
    # これによりHEIGHT_CONTROL選択率を25%未満に抑制し、併合機会を優先。
    early_game = max_y < -1.0

    # --- phase judgment (v42 thresholds) ---
    if max_y < 0.8:
        phase = "LOW"
        height_mult = 1.0  # low board weak height penalty
        merge_mult = 1.2  # 20% merge bonus increase, actively target
    elif max_y < 1.8:
        phase = "MEDIUM"
        height_mult = 1.8  # v151: height_mult 2.2->1.8 relaxation, ensure merge opportunity
        merge_mult = 1.0
    elif max_y < 3.0:
        phase = "HIGH"
        height_mult = 1.8  # HIGH relaxation to ensure merge opportunity
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
        # v159: early_game（max_y < -1.0）の場合、height_multiplierを0.2に削減してHEIGHT_CONTROL過剰選択を抑制
        # v158の0.3ではHEIGHT_CONTROL選択率が30.5%と依然として高い。0.2に削減して25%未満に抑制。
        # combined with dynamic chain merge adjustment (evaluation axis 6) for structural improvement
        # additional multiplier if HIGH/MEDIUM landing high (>0.5)
        height_multiplier = 30.0
        if early_game:
            height_multiplier = 0.2  # v159: 序盤はHEIGHT_CONTROLを強く抑制し、併合機会を優先

        height_penalty = landing_y * height_multiplier * height_mult

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

        # ----- evaluation axis 4: left-right balance correction (v148: enhanced) -----
        # bonus for correcting left-right piece count bias.
        # balance_bias > 0 means right majority -> left (x<0) placement reduces penalty
        # v148: higher board increases balance_strength, strictens balance control
        balance_strength = 20.0
        if phase == "HIGH":
            balance_strength = 50.0  # v148: HIGH balance control even stricter (40.0->50.0)
        elif phase == "MEDIUM":
            balance_strength = 35.0  # v148: MEDIUM also strengthen balance control (30.0->35.0)

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

        # ----- evaluation axis 6: chain merge bonus (v160: early_game enhancement版) -----
        # v160: 初期段階でのCHAIN_MERGE選択強化版
        # batch_summaryでHEIGHT_CONTROLが21.6%選択(avg_score_delta=1.1)と過剰であること、
        # CHAIN_MERGE関連がavg_score_delta=31.4-41.4と高価値だが選択率は2.4-2.7%と極端に低いことを確認。
        # worstゲームで初期6ターンが全てHEIGHT_CONTROLとなり、マージ機会を逃している失敗パターンを特定。
        # early_game（max_y < -1.0）の場合、chain_distance_maxを7.0に拡大し初期段階でのCHAIN_MERGE選択を強化。
        # 例: early_game=true → distance_max=7.0（初期段階での広範囲CHAIN_MERGE探索）
        # 例: early_game=false, landing_y=0.0 → distance_max=5.0, multiplier=450.0
        # 例: early_game=false, landing_y=1.0 → distance_max=5.6, multiplier=600.0
        # 例: early_game=false, landing_y=2.0 → distance_max=6.2, multiplier=750.0
        # 例: early_game=false, landing_y=3.0 → distance_max=6.8, multiplier=900.0
        if merge_grade in ["DIRECT", "NEAR"] and result.get("merges"):
            merges = result["merges"]
            if merges:
                # get best merge target (closest distance)
                best_merge = min(merges, key=lambda m: m.get("dist", float("inf")))
                target_x = best_merge.get("x", 0)
                target_y = best_merge.get("y", 0)

                # v160: 着地高に応じてchain_distanceとchain_bonus_multiplierを動的に調整
                # v155の成功パラメータ（chain_distance_max=5.0）を復帰し、CHAIN_MERGE選択率を向上させる
                # HIGH_LAYER状況（landing_y>0.5）ではchain_distanceを拡大し、chain_bonus_multiplierを強化
                chain_distance_max = 7.0 if early_game else (5.0 + landing_y * 0.6)
                chain_bonus_multiplier = 450.0 + landing_y * 150.0

                # collect all merged_type pieces within chain_distance_max of merge target
                nearby_pieces = []
                for p in pieces:
                    if p.get("type") == merged_type:
                        dist = ((p["x"] - target_x) ** 2 + (p["y"] - target_y) ** 2) ** 0.5
                        if dist < chain_distance_max:
                            nearby_pieces.append((dist, p))

                # sort by distance
                nearby_pieces.sort(key=lambda x: x[0])

                # bonus calculation from closest 3 pieces using dynamic multiplier
                # 1st: (chain_distance_max - dist) * chain_bonus_multiplier
                # 2nd: (chain_distance_max - dist) * chain_bonus_multiplier * 0.5
                # 3rd: (chain_distance_max - dist) * chain_bonus_multiplier * 0.25
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

        # ----- evaluation axis 7: board dispersion bonus (v3439: NEW) -----
        # v3439: 初期5ピースかつマージ機会がない場合、盤面の左右分散を最大化する
        # batch_summaryで高スコア群は初期から高めに配置（序盤avg=-2.2）し、低スコア群は初期から低すぎ（序盤avg=-2.78）していることを確認。
        # 初期段階で盤面を左右に広げることで、マージ機会を創出し、HEIGHT_CONTROL過剰選択を抑制する。
        if early_game_no_merge and merge_grade == "NO":
            # 左端と右端のピース位置を計算
            if x < min_x:
                # 現在の左端より左側に配置
                dist_outside = min_x - x
                # 距離3.0で50.0、距離5.0以上で150.0（最大）
                dispersion_bonus = min(150.0, max(0.0, (dist_outside - 3.0) * 50.0 + 50.0))
                score += dispersion_bonus
                reasons.append("BOARD_DISPERSION")
            elif x > max_x:
                # 現在の右端より右側に配置
                dist_outside = x - max_x
                # 距離3.0で50.0、距離5.0以上で150.0（最大）
                dispersion_bonus = min(150.0, max(0.0, (dist_outside - 3.0) * 50.0 + 50.0))
                score += dispersion_bonus
                reasons.append("BOARD_DISPERSION")

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
