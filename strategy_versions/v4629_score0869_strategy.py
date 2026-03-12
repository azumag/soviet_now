#!/usr/bin/env python3
"""strategy.py - Soviet Puzzle Game AI Drop Position Script

Game Overview:
  - Drop pieces, merge same type pieces (N+N -> N+1)
  - Score table: type1=1, type2=3, type3=6, ..., typeN = N*(N+1)/2
  - Board: x in [-3.0, +3.0], floor y=-4.48, deadline y=3.32
  - Player controls only drop X coordinate

Decision Logic (9 evaluation axes):
  1. Merge bonus - High score for immediate merge (DIRECT > NEAR > FAR)
  2. Height penalty - Penalty for high landing position (varies by phase, early_game: max_y < -2.0)
  3. Drift penalty - Penalty for post-landing drift due to polygon shape
  4. Left-right balance correction - Bonus for correcting piece count bias
  5. nextNext centering - Center for next merge opportunity if nextNext same type
  6. Chain merge bonus - Evaluate possibility of further merges after merge (v171: CHAIN_MERGE基本ボーナス強化)
  7. Early game merge priority - Strong bonus for merge opportunities in early game (v174: piece_count <= 12)
  8. Reactive merge priority - Bonus for merge opportunities when reactive_pairs >= 2 (v176)
  9. MEDIUM_TOWER promotion - Bonus for merge candidates at higher landing in MEDIUM phase (v174)

Phases (determined by board max Y):
  LOW      (max_y < 0.8) : Early game. Merge priority (merge_mult=1.2)
  MEDIUM   (0.8 <= max_y < 1.8) : Mid game. Height management (height_mult=1.4)
  HIGH     (1.8 <= max_y < 3.0) : Late game. Merge opportunity (height_mult=1.8)
  CRITICAL (3.0 <= max_y) : Danger. DIRECT merge priority, board compression

Fixed interface:
  decide(game_state: dict, analysis: dict) -> dict
     Returns: {"x": float, "reason": str}

AI modifiable: decide() body, helper functions, constants, imports
AI prohibited: decide() signature, if __name__ == "__main__" block

# --- Change History ---
# [BEST:5310] v159: reactor情報活用による危険局面即時併合優先版
# [BEST:5694] v160: 危険局面フィルタリング早期化強化版 - max_y>=1.8かつreactive_pairs>=3で併合機会のみを評価対象
#   - 危険局面でのFARマージボーナスを強化（200.0→1200.0）し、いずれかの併合機会を確保
#   - ワーストゲーム(score0467)の失敗パターン分析に基づき、危険局面の閾値を厳密化
# refs: tmp/batch_summary.txt, tmp/improve_brief.md, game_history/20260311_012257_score0467.jsonl turns 48-55
# v177: MEDIUMフェーズHEIGHT_CONTROL抑制強化版 - batch_summaryでHEIGHT_CONTROLが27.5%選択(avg_score_delta=0.9)と過剰であることを確認
#   - 高スコア群(23.9%)と低スコア群(32.5%)の比較で、低スコア群が8.6%も多くHEIGHT_CONTROLを選択していることを特定
#   - MEDIUMフェーズのheight_multiplierを15.0に削減し、マージ選択を促進することでHEIGHT_CONTROL選択を抑制しスコア向上を目指す
# refs: tmp/batch_summary.txt, tmp/improve_brief.md, tmp/advice.md, strategy_versions/best_score5310_strategy.py, tmp/change_log.txt
# v178: 危険局面盤面圧縮強化版 - ワーストゲーム(score0579, score0697)の終盤分析で、max_y>=2.0かつreactive_pairs>=2-8あるにもかかわらず
#   HIGH_TOWERが選択され続け即時併合機会を逃している失敗パターンを特定。
#   - 危険局面判定閾値をreactive_pairs>=2に引き下げて早期対応
#   - 危険局面でheight_multiplierを15.0に削減し、reactive_pairsによる盤面圧縮を優先
#   - 危険局面でlanding_y>0.5の場合にheight_penaltyを3倍にしてHIGH_TOWERを強力に抑制
#   - REACTIVE_MERGE_PRIORITYボーナスを500.0→800.0に増強してマージ選択を強制
# refs: tmp/batch_summary.txt, tmp/improve_brief.md, game_history/20260312_103346_score0579.jsonl, game_history/20260312_110534_score0697.jsonl
"""

SCORE_TABLE = {i: i * (i + 1) // 2 for i in range(1, 17)}

def decide(game_state: dict, analysis: dict) -> dict:
    """v178: 危険局面盤面圧縮強化版

    ワーストゲーム(score0579, score0697)の終盤分析で、max_y>=2.0かつreactive_pairs>=2-8あるにもかかわらず
    HIGH_TOWERが選択され続け即時併合機会を逃している失敗パターンを特定。
    危険局面での盤面圧縮を最優先し、reactive_pairsを活用したマージ選択を強制することでスコア向上を目指す。

    v178の改善点：
     1. 危険局面判定閾値引き下げ
        - dangerous_situation: reactive_pairs >= 3 → >= 2（早期対応）
     2. 危険局面でのheight_multiplier削減
        - height_multiplierを15.0に設定してreactive_pairs優先を強化
     3. 危険局面でのHIGH_TOWER強力抑制
        - landing_y > 0.5の場合にheight_penaltyを3倍にして、reactive_pairsを活用する配置を優先
     4. REACTIVE_MERGE_PRIORITYボーナス増強
        - reactive_pair_count >= 2でDIRECT/NEARマージに+800.0ボーナス（500.0→800.0）
     5. v174/v177の初期12ターンマージ重視・MEDIUMフェーズHEIGHT_CONTROL抑制を維持
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

    # --- v174: early_game判定（max_y < -2.0） ---
    early_game = max_y < -2.0

    # --- reactor information ---
    reactor = analysis.get("reactor", {})
    reactive_pairs = reactor.get("reactive_pairs", [])
    reactive_pair_count = len(reactive_pairs) if isinstance(reactive_pairs, list) else 0

    # --- v178: 危険局面フィルタリング早期化・強化 ---
    # 条件: max_y >= 1.8 かつ reactive_pairs >= 2（v178: >=3から>=2に引き下げ）
    # ワーストゲーム(score0579, score0697)での失敗パターン分析に基づき閾値調整
    dangerous_situation = max_y >= 1.8 and reactive_pair_count >= 2

    # --- phase judgment (v42 thresholds) ---
    if max_y < 0.8:
        phase = "LOW"
        height_mult = 1.0
        merge_mult = 1.2
    elif max_y < 1.8:
        phase = "MEDIUM"
        height_mult = 1.4
        merge_mult = 1.0
    elif max_y < 3.0:
        phase = "HIGH"
        height_mult = 1.8
        merge_mult = 1.0
    else:
        phase = "CRITICAL"
        height_mult = 1.0
        merge_mult = 0.6

    # --- next piece information ---
    next_piece = game_state.get("next", {})
    next_next_piece = game_state.get("nextNext", {})
    next_type = next_piece.get("type", 0)
    next_next_type = next_next_piece.get("type", 0)

    # =======================================================================
    #  v160: 危険局面での候補フィルタリング
    # =======================================================================
    if dangerous_situation:
        merge_results = [r for r in results if r.get("merge_grade") in ["DIRECT", "NEAR", "FAR"]]
        if merge_results:
            filtered_results = merge_results
        else:
            # 全候補を評価（マージ機会がない場合のフォールバック）
            filtered_results = results
    else:
        filtered_results = results

    # =======================================================================
    #  score each drop candidate (x coordinate) with 8 evaluation axes
    # =======================================================================
    for result in filtered_results:
        x = result["x"]
        landing_y = result.get("landing_y", 0)
        drift_x = result.get("drift_x", 0)
        drift_unc = result.get("drift_unc", 0)
        merge_grade = result.get("merge_grade", "NO")

        score = 0.0
        reasons = []

        # ----- evaluation axis 1: merge bonus -----
        if merge_grade == "DIRECT":
            score += 1200.0 * merge_mult
            reasons.append("DIRECT_MERGE")
        elif merge_grade == "NEAR":
            score += 600.0 * merge_mult
            reasons.append("NEAR_MERGE")
        elif merge_grade == "FAR":
            # v160: 危険局面ではFARマージボーナスを強化（200.0→1200.0）
            # ベストゲーム（score3064）のようなmax_y>2.5の状況でも盤面を圧縮するため、
            # 盤面圧縮優先とFARマージの安全性を確保
            far_bonus = 1200.0 if dangerous_situation else 200.0
            score += far_bonus * merge_mult
            reasons.append("FAR_MERGE")

        # ----- evaluation axis 2: height penalty -----
        # v174/v177: 盤面圧縮優先戦略・HEIGHT_CONTROL抑制
        # early_game: height_multiplierを0.2に削減してHEIGHT_CONTROL過剰選択を抑制
        # v175/v177: MEDIUMフェーズでheight_multiplierを15.0に削減してマージ選択を促進
        # v173: 初期段階で併合機会がない場合、HEIGHT_CONTROL抑制を強化
        # v167: reactive_pairsに応じて盤面圧縮の強度を動的に調整
        height_multiplier = 30.0
        if early_game:
            height_multiplier = 0.2  # early_gameではHEIGHT_CONTROLを抑制

        # v175/v177: MEDIUMフェーズでHEIGHT_CONTROL抑制を強化
        if phase == "MEDIUM":
            height_multiplier = 15.0  # v177: マージ選択を促進

        # v173: 初期段階で併合機会がない場合、HEIGHT_CONTROL抑制をさらに強化
        if piece_count <= 6 and merge_grade == "NO":
            height_multiplier = 0.1

        # v178: 危険局面での盤面圧縮強化（height_multiplierを15.0に削減）
        if dangerous_situation:
            height_multiplier = 15.0

        # v167: reactive_pairsによる盤面圧縮調整
        if phase in ["MEDIUM", "HIGH"] and reactive_pair_count >= 2:
            reactive_compression_factor = max(0.3, 1.0 - (reactive_pair_count - 2) * 0.15)
            height_multiplier *= reactive_compression_factor
        elif max_y >= 2.0 and reactive_pair_count >= 2:
            height_multiplier *= 0.5

        height_penalty = landing_y * height_multiplier * height_mult

        if phase == "HIGH" and landing_y > 0.5:
            height_penalty *= 2.0
            reasons.append("HIGH_TOWER")
        elif phase == "MEDIUM" and landing_y > 0.5:
            height_penalty *= 1.5
            reasons.append("MEDIUM_TOWER")
        elif landing_y > 0.0:
            reasons.append("HIGH_LAYER")

        # v178: 危険局面での着地高すぎに対する強力なペナルティ
        if dangerous_situation and landing_y > 0.5:
            height_penalty *= 3.0

        score -= height_penalty

        # ----- evaluation axis 3: drift penalty -----
        drift_penalty = (abs(drift_x) + drift_unc) * 30.0
        score -= drift_penalty

        # ----- evaluation axis 4: left-right balance correction -----
        balance_strength = 20.0
        if phase == "HIGH":
            balance_strength = 50.0
        elif phase == "MEDIUM":
            balance_strength = 40.0

        left_count = sum(1 for p in pieces if p["x"] < 0)
        right_count = len(pieces) - left_count
        balance_bias = (right_count - left_count) / (len(pieces) if pieces else 1)

        balance_penalty = x * balance_bias * balance_strength
        score -= abs(balance_penalty)

        # ----- evaluation axis 5: nextNext centering -----
        if next_next_type == next_type:
            center_bonus = max(0, 1.0 - abs(x) / 2.0) * 50.0
            score += center_bonus
            reasons.append("NEXT_SAME")

        # ----- evaluation axis 6: chain merge bonus (v171: CHAIN_MERGE基本ボーナス強化） -----
        # v171: CHAIN_MERGE基本ボーナス強化
        # chain_distance_max = 5.0 + landing_y * 0.6 (v155成功値に戻す、着地高に応じて拡大)
        # chain_bonus_multiplier = 480.0 + landing_y * 150.0 (初期値を450.0→480.0に強化、着地高に応じて増強)

        if merge_grade in ["DIRECT", "NEAR"] and result.get("merges"):
            merges = result["merges"]
            if merges:
                best_merge = min(merges, key=lambda m: m.get("dist", float("inf")))
                target_x = best_merge.get("x", 0)
                target_y = best_merge.get("y", 0)

                # v171: CHAIN_MERGE基本ボーナス強化
                chain_distance_max = 5.0 + landing_y * 0.6
                chain_bonus_multiplier = 480.0 + landing_y * 150.0

                nearby_pieces = []
                for p in pieces:
                    if p.get("type") == next_type:
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
                    chain_bonus = (
                        (chain_distance_max - dist) * chain_bonus_multiplier * 0.5
                    )
                    score += chain_bonus

                if len(nearby_pieces) >= 3:
                    dist, _ = nearby_pieces[2]
                    chain_bonus = (
                        (chain_distance_max - dist) * chain_bonus_multiplier * 0.25
                    )
                    score += chain_bonus

                if nearby_pieces:
                    reasons.append("CHAIN_MERGE")

        # ----- evaluation axis 7: early game merge priority (v174: 初期12ターンマージ重視) -----
        # v174: early_game判定(max_y < -2.0)をさらに緩和し、EARLY_MERGE_PRIORITYの適用範囲をpiece_count <= 10→12に拡大。
        # 初期12ターンを一つのフェーズとして扱い、この期間中はマージ機会を最優先してHEIGHT_CONTROL選択を抑制する。
        if (early_game or piece_count <= 12) and merge_grade == "NEAR":
            score += 800.0  # 初期段階での強力なマージ優先
            reasons.append("EARLY_MERGE_PRIORITY")

        # ----- v178: reactive_pairs-based merge priority -----
        # v178: batch_summary分析でHEIGHT_CONTROLが27.5%選択と過剰であることを確認。
        # reactor情報のreactive_pairs（反応性のあるペア）が2つ以上ある場合、盤面に多数の併合機会があることを示唆。
        # v178: ボーナスを800.0に増強し、危険局面でのマージ選択を強制することでHEIGHT_CONTROL選択を抑制しスコア安定性を向上させる。
        if reactive_pair_count >= 2 and merge_grade in ["DIRECT", "NEAR"]:
            score += 800.0
            reasons.append("REACTIVE_MERGE_PRIORITY")

        # ----- evaluation axis 8: MEDIUM_TOWER selection promotion (v174) -----
        # ベストゲーム(score3064)の戦略から採用
        # HIGHフェーズのheight_multiplier=1.8に対して、MEDIUMフェーズでは1.4と差をつけることで、
        # 着地が高い場合でも、マージ候補の中からMEDIUM_TOWERを選んで盤面を下げる
        if phase == "MEDIUM" and landing_y > 0.5:
            score += 200.0  # ベストゲームでの成功パラメータ
            reasons.append("MEDIUM_TOWER_PROMOTION")
        elif phase == "HIGH" and landing_y > 0.5:
            # HIGHフェーズでのMEDIUM_TOWERは高すぎるため、適用なし
            # そのままHIGH_TOWERとして評価（height_penalty *= 2.0）
            pass

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
