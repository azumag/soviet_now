#!/usr/bin/env python3
"""strategy.py - Soviet Puzzle Game AI Drop Position Script

Game Overview:
  - Drop pieces, merge same type pieces (N+N -> N+1)
- Score table: type1=1, type2=3, type3=6, ..., typeN = N*(N+1)/2
- Board: x in [-3.0, +3.0], floor y=-4.48, deadline y=3.32
  - Player controls only drop X coordinate

Decision Logic (9 evaluation axes):
    1. Merge bonus - High score for immediate merge (DIRECT > NEAR > FAR)
    2. Height penalty - Penalty for high landing position (varies by phase)
    3. Reactive pair bonus - Bonus for multiple merge opportunities
    4. Drift penalty - Penalty for post-landing drift due to polygon shape
    5. Left-right balance correction - Bonus for correcting piece count bias
    6. nextNext centering - Center for next merge opportunity if nextNext same type
    7. nextNext protection bonus - Position for nextNext merge opportunity if nextNext==merged_type (v195: NEW)
    8. Chain merge bonus - Evaluate possibility of further merges after merge
    9. Early game merge priority - Strong bonus for merge opportunities in early game

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
#
# v195: nextNext 2手先評価導入版 - batch_summaryでNEAR_MERGE_*_CHAIN_MERGEが3-5%選択(avg_score_delta=33-77)と高効率だが選択率が低いことを確認。
# advice.mdの「nextNextを考慮して配置を決める戦略へ改善」と「盤面A・nextB・nextNextAの状況でnextNextの併合を逃す問題」に対処理。
# nextNextとnextが同じタイプで、かつ、あるX位置に現在のnextを落とすことでnextNextが併合しやすい位置を確保できる場合、
# その配置にボーナスを付与する新しい評価軸を追加。2手先の盤面評価機能を導入。
# refs: tmp/batch_summary.txt, tmp/advice.md, strategy_versions/v2862_score2020_strategy.py, strategy_versions/best_score5310_strategy.py

# Merge result score: type N merge gives N*(N+1)/2 points
# Example: type1+1->2 gives +3 points, type8+8->9 gives +45 points, type14+14->15 gives +120 points
SCORE_TABLE = {i: i * (i + 1) // 2 for i in range(1, 17)}


def decide(game_state: dict, analysis: dict) -> dict:
    """v195: nextNext 2手先評価導入版

    batch_summaryでNEAR_MERGE_*_CHAIN_MERGEが3-5%選択(avg_score_delta=33-77)と高効率だが選択率が低いことを確認。
    advice.mdの「nextNextを考慮して配置を決める戦略へ改善」と「盤面A・nextB・nextNextAの状況でnextNextの併合を逃す問題」に対処理。
    nextNextとmerged_typeが同じタイプで、かつ、あるX位置に現在のnextを落とすことでnextNextが併合しやすい位置を確保できる場合、
    その配置にボーナスを付与する新しい評価軸を追加。2手先の盤面評価機能を導入。

    v195の改善点:
     1. nextNext protection bonus評価軸の追加（評価軸7）
         - nextNextとmerged_typeが同じタイプの場合、併合後のmerged_typeピースに近い配置にボーナス
         - これにより、nextNextが併合しやすい位置を選択するようになる
         - ボーナスは最も近いmerged_typeピースへの距離に基づいて計算（maxボーナス600.0）
     2. v194の漸層的早期マージ優先戦略を維持
         - early_game判定をmax_y < -2.5に緩和
         - マージ機会がある場合、HEIGHT_CONTROL抑制を強化（height_multiplier=0.1）
         - 初期8ターンまでEARLY_MERGE_PRIORITY条件を緩和（piece_count <= 8）
     3. v177の殿堂入り戦略の成功パラメータを維持
         - type_merge_bonus, chain_bonus_multiplier初期値480.0
         - MEDIUMフェーズ height_mult=1.4（殿堂入り戦略）

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

    # --- reactor information (for merge opportunity evaluation) ---
    reactor = analysis.get("reactor", {})
    # reactor.reactive_pairs is already a list of tuples [(id1, id2, type), ...]
    reactive_pairs = reactor.get("reactive_pairs", [])
    reactive_pair_count = len(reactive_pairs)

    # --- v194: early_game判定緩和（v193: -2.0 → v194: -2.5） ---
    # v193ではmax_y < -2.0と厳しすぎ、gapのある間のマージ機会を見逃している
    # 初期段階でのHEIGHT_CONTROL選択抑制を維持しつつ、マージ機会を優先する緩和を行う
    early_game = max_y < -2.5

    # --- phase judgment (v42 thresholds) ---
    if max_y < 0.8:
        phase = "LOW"
        height_mult = 0.8  # v193: LOWフェーズHEIGHT_CONTROL抑制強化 (1.0→0.8)
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

    # --- Type-specific merge bonus calculation (v177) ---
    # merge result type (next_type+1) higher means higher score value
    # example: type1 merge -> bonus=330, type5 merge -> bonus=510, type14 merge -> bonus=1660
    merge_result_type = min(next_type + 1, 16)
    type_merge_bonus = SCORE_TABLE.get(merge_result_type, 10) * 10 + 300

    # --- v149: pre-calculate merged type (for chain judgment) ---
    merged_type = min(next_type + 1, 16)

    # =======================================================================
    #  score each drop candidate (x coordinate) with 8 evaluation axes
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
        # v194: LOW phase height_mult=0.8 (HEIGHT_CONTROL抑制強化)
        # v194: early_game条件下での漸層的マージ優先戦略（v193の緩和版）
        height_multiplier = 50.0  # v177: 基本値30.0→50.0に変更

        # v194: 漸層的早期マージ優先戦略
        # マージ機会がある場合、HEIGHT_CONTROL抑制を強化（height_multiplier=0.1）
        # マージ機会がない場合でも消極的配置を回避（height_multiplier=0.5）
        if early_game:
            if merge_grade in ["NEAR", "DIRECT"]:
                # マージ機会がある場合、HEIGHT_CONTROL抑制を強化
                height_multiplier = 0.1  # マージ優先戦略：消極的配置を回避
            else:
                # マージ機会がない場合も、v193の0.1では低すぎ、0.5に調整
                # 完全に消極的配置を回避し、中程度の高さを許容
                height_multiplier = 0.5

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

        # ----- evaluation axis 3: reactive pair bonus (v193: Reactor直接活用版) -----
        # reactor.reactive_pairsを直接参照（v192のresultsループ全探索は廃止）
        # 反応可能なペアが2つ以上ある場合、マージを優先する評価軸を追加
        if reactive_pair_count >= 2:
            # 盤面に多数の併合機会がある状況でマージを優先
            reactive_bonus = reactive_pair_count * 150.0
            score += reactive_bonus
            reasons.append("REACTIVE_PAIRS")
        elif reactive_pair_count == 1:
            # 反応可能なペアが1つある場合も小さなボーナス
            score += 100.0
            reasons.append("REACTIVE_PAIRS")

        # ----- evaluation axis 4: drift penalty -----
        # polygon shape pieces roll after landing. larger drift amount and uncertainty means
        # higher risk of deviation from targeted position
        drift_penalty = (abs(drift_x) + drift_unc) * 30.0
        score -= drift_penalty

        # ----- evaluation axis 5: left-right balance correction (v162: enhanced) -----
        # bonus for correcting left-right piece count bias.
        # balance_bias > 0 means right majority -> left (x<0) placement reduces penalty
        balance_strength = 20.0
        if phase == "HIGH":
            balance_strength = 50.0  # v148: HIGH balance control even stricter
        elif phase == "MEDIUM":
            balance_strength = 40.0  # v162: MEDIUM phase balance correction enhanced

        left_count = sum(1 for p in pieces if p["x"] < 0)
        right_count = len(pieces) - left_count
        balance_bias = (right_count - left_count) / (len(pieces) if pieces else 1)

        balance_penalty = x * balance_bias * balance_strength
        score -= abs(balance_penalty)

        # ----- evaluation axis 6: nextNext centering -----
        # if nextNext same type as current next, next also has merge opportunity.
        # place near center to allow merge in either direction next turn
        if next_next_type == next_type:
            center_bonus = max(0, 1.0 - abs(x) / 2.0) * 50.0
            score += center_bonus
            reasons.append("NEXT_SAME")

        # ----- evaluation axis 7: nextNext protection bonus (NEW) -----
        # 盤面A・nextB・nextNextAの状況で、A上にBを置くとnextNextの併合を逃す問題に対処理。
        # 具体的には、「併合後にtype merged_typeのピースが着地することを想定し、
        # 最も近いmerged_typeピースへの距離が小さいほどボーナスが高い。
        # これにより、nextNextが併合しやすい位置を選択するようになる。
        if next_next_type == merged_type:
            # 併合後にtype merged_typeのピースがどこに着地するか予測
            # ターゲットを落とした直後のmerged_typeピースの着地Yを簡易的に予測
            # 情密にはanalyze_boardのestimate_explosion_displacementを使うべきだが、簡易的に
            # 併合後のピースは現在のnextより上に着地すると仮定
            # merged_typeピースの中で、最もYが大きいものを見つけ、そこに近い場所へのボーナスを計算
            merged_type_pieces = [p for p in pieces if p.get("type") == merged_type]
            if merged_type_pieces:
                # merged_typeピースの中で最も上にあるピースのY座標を取得
                max_piece = max(merged_type_pieces, key=lambda p: p.get("y", -5.0))
                target_y = max_piece["y"]
                target_x = max_piece["x"]
                
                # すべてのmerged_typeピースに対する距離を計算し、最も近いものを探索
                min_dist = float("inf")
                for p in merged_type_pieces:
                    if p["id"] != result.get("hit_id"):  # 併合対象のピースは除外
                        dist = ((p["x"] - x) ** 2 + (p["y"] - target_y) ** 2) ** 0.5
                        if dist < min_dist:
                            min_dist = dist
                
                # 最も近いmerged_typeピースへの距離がさいほど（4.0未満）、ボーナスを大きく付与
                # これにより、nextNextが併合後のmerged_typeのピースに近い位置を選ぶようになる
                if min_dist < 4.0:
                    next_protection_bonus = (4.0 - min_dist) * 150.0
                    if next_protection_bonus > 0:
                        score += next_protection_bonus
                        reasons.append("NEXTNXT_PROTECTION")

        # ----- evaluation axis 8: chain merge bonus (v177: type_merge_bonus強化版) -----
        # v177: type_merge_bonusを含めたCHAIN_MERGE評価軸
        if merge_grade in ["DIRECT", "NEAR"] and result.get("merges"):
            merges = result["merges"]
            if merges:
                # get best merge target (closest distance)
                best_merge = min(merges, key=lambda m: m.get("dist", float("inf")))
                target_x = best_merge.get("x", 0)
                target_y = best_merge.get("y", 0)

                # v177: type_merge_bonus基本値
                # type_merge_bonusはタイプに応じて増加するボーナス
                # type_merge_bonus = SCORE_TABLE.get(merged_type, 10) * 10 + 300

                # v155成功パラメータ: chain_distance_max=5.0
                # v177: chain_bonus_multiplier初期値480.0
                # 例: landing_y=-3.0 → distance_max=3.2, multiplier=480.0
                # 例: landing_y=0.0 → distance_max=5.0, multiplier=480.0
                # 例: landing_y=1.0 → distance_max=5.6, multiplier=630.0
                chain_distance_max = 5.0
                chain_bonus_multiplier = 480.0

                # collect all merged_type pieces within chain_distance_max of merge target
                nearby_pieces = []
                for p in pieces:
                    if p.get("type") == merged_type:
                        dist = (
                            (p["x"] - target_x) ** 2 + (p["y"] - target_y) ** 2
                        ) ** 0.5
                        if dist < chain_distance_max:
                            nearby_pieces.append((dist, p))

                # sort by distance (closest first)
                nearby_pieces.sort(key=lambda x: x[0])

                # v177: type_merge_bonus基本値を基本ボーナスとして適用
                # v155成功構造: 3つの最も近いピースに対し、距離に応じて減衰するボーナスを適用
                if len(nearby_pieces) >= 1:
                    dist, _ = nearby_pieces[0]
                    chain_bonus = (chain_distance_max - dist) * type_merge_bonus
                    score += chain_bonus

                if len(nearby_pieces) >= 2:
                    dist, _ = nearby_pieces[1]
                    chain_bonus = (
                        (chain_distance_max - dist) * type_merge_bonus * 0.5
                    )
                    score += chain_bonus

                if len(nearby_pieces) >= 3:
                    dist, _ = nearby_pieces[2]
                    chain_bonus = (
                        (chain_distance_max - dist) * type_merge_bonus * 0.25
                    )
                    score += chain_bonus

                if nearby_pieces:
                    reasons.append("CHAIN_MERGE")

        # ----- evaluation axis 8: early game merge priority (v194: 漸層的早期マージ優先戦略) -----
        # v194: 初期8ターンまでEARLY_MERGE_PRIORITY条件を緩和し、全体的にマージ機会を優先する戦略へ転換
        # v177: type_merge_bonus基本値を基本ボーナスとして適用
        # v177: chain_bonus_multiplier初期値480.0
        # v193: LOW phase height_mult=0.8 (HEIGHT_CONTROL抑制強化)

        # v194: 漸層的早期マージ優先戦略
        # 初期8ターンまでEARLY_MERGE_PRIORITY条件を緩和（piece_count <= 8）
        # 全体的にマージ機会を優先する戦略へ転換
        if piece_count <= 8 and merge_grade in ["NEAR", "DIRECT"]:
            # 初期8ターンでNEAR_MERGEまたはDIRECT_MERGE機会がある場合、強力なボーナスを付与
            # これにより初期段階全体でマージ機会を最優先し、HEIGHT_CONTROL選択を抑制
            score += 1000.0
            reasons.append("EARLY_MERGE_PRIORITY")

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
