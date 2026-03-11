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
    3. Drift penalty - Penalty for post-landing drift due to polygon shape
    4. Left-right balance correction - Bonus for correcting piece count bias
    5. nextNext centering - Center for next merge opportunity if nextNext same type
    6. Chain merge bonus - Evaluate possibility of further merges after merge
    7. Early game merge priority - Bonus for NEAR merge in early game (max_y < -2.0)
    8. MEDIUM_TOWER promotion - Bonus for merge candidates at higher landing in MEDIUM phase
    9. v163: Reactive merge priority in danger - Bonus for DIRECT/NEAR merge when max_y>=2.0 and reactive_pairs>=2
    10. v161: Medium phase immediate merge priority - Filter to merge candidates when reactive_pairs >= 2 in MEDIUM phase

 Phases (determined by board max Y):
   LOW      (max_y < 0.8) : Early game. Merge priority (merge_mult=1.2)
   MEDIUM   (0.8 <= max_y < 1.8) : Mid game. Height management (height_mult=1.4), immediate merge when reactive_pairs >= 2
   HIGH     (1.8 <= max_y < 3.0) : Late game. Merge opportunity (height_mult=1.8), immediate merge when reactive_pairs >= 3
   CRITICAL (3.0 <= max_y) : Danger. DIRECT merge priority, board compression

Fixed interface:
  decide(game_state: dict, analysis: dict) -> dict
     Returns: {"x": float, "reason": str}

AI modifiable: decide() body, helper functions, constants, imports
AI prohibited: decide() signature, if __name__ == "__main__" block

 # --- Change History ---
 # [BEST:5310] v159: reactor情報活用による危険局面即時併合優先版
 # v160: 危険局面フィルタリング早期化強化版 - max_y>=1.8かつreactive_pairs>=3で併合機会のみを評価対象
 #   - 危険局面でのFARマージボーナスを強化（200.0→1200.0）し、いずれかの併合機会を確保
 #   - ワーストゲーム(score0467)の失敗パターン分析に基づき、危険局面の閾値を厳密化
 # v161: 中盤フェーズでの即時併合優先強化版 - ワーストゲーム(score0606)の失敗パターン分析に基づき、中盤フェーズでの即時併合機会の見逃しを回避
 #   - 中盤フェーズ(0.8 <= max_y < 1.8)では reactive_pairs >= 2 の段階で併合候補のみを評価対象にする
 #   - HIGH/CRITICALフェーズでは reactive_pairs >= 3 でフィルタリング発動（v160の条件を維持）
 #   - これにより、盤面がまだ圧縮可能な段階で即時併合を優先し、盤面圧迫を回避する
 # v162: 危険局面での盤面圧縮最優先化版 - ワーストゲーム(score0681, score0780)の終盤失敗パターン分析に基づき、max_y >= 2.0 の危険局面での盤面圧縮を最優先
 #   - max_y >= 2.0 の全ての局面で height_multiplier=0.0 に設定し、height_penalty を無効化
 #   - 即時併合機会がある場合は mergeボーナスが支配的になり即時併合が最優先される
 #   - 併合候補がない場合も、盤面圧縮を優先して height_penalty によるペナルティを回避
 # v163: 危険局面reactive_pairs併合優先追加版 - ワーストゲーム(score0696)の終盤8ターン(turns 58-65)でmax_y>=2.0, reactive_pairs=4-6があるにもかかわらず
 #   merge_available=falseが続き、HIGH_TOWER/HIGH_LAYERが選択される失敗パターンを特定。
 #   v177(best_score5310)のreactive_pairs-based merge priorityを導入し、max_y >= 2.0 かつ reactive_pairs >= 2 の危険局面で
 #   即時併合候補(DIRECT/NEAR)に+1000.0ボーナスを与え、非併合配置に-2000.0ペナルティを課すことで即時併合を強力に優先。
 # refs: tmp/batch_summary.txt, tmp/improve_brief.md, tmp/advice.md, game_history/20260311_141001_score0696.jsonl turns 58-65,
 #       game_history/20260311_143033_score2348.jsonl, strategy_versions/best_score5310_strategy.py
 """

SCORE_TABLE = {i: i * (i + 1) // 2 for i in range(1, 17)}


def decide(game_state: dict, analysis: dict) -> dict:
    """v163: 危険局面reactive_pairs併合優先追加版

    ワーストゲーム(score0696)の終盤8ターン(turns 58-65)でmax_y>=2.0, reactive_pairs=4-6があるにもかかわらず
    merge_available=falseが続き、HIGH_TOWER/HIGH_LAYERが選択され続け、max_y=3.05まで上昇してゲームオーバーになる失敗パターンを特定。
    v177(best_score5310)のreactive_pairs-based merge priorityを導入し、max_y >= 2.0 かつ reactive_pairs >= 2 の危険局面で
    即時併合候補(DIRECT/NEAR)に+1000.0ボーナスを与え、非併合配置に-2000.0ペナルティを課すことで即時併合を強力に優先。
    v162の危険局面height_multiplier無効化を維持しつつ、より強力な即時併合優先を実現。

    v163の改善点：
     1. 危険局面での即時併合ボーナス追加（v177から採用）
        - max_y >= 2.0 かつ reactive_pairs >= 2 の場合、DIRECT/NEARマージに+1000.0ボーナス
        - ベストゲーム(score2348)のturns 109-111でreactive_pairs=6の状況でNEAR_MERGEを3回選択し、
          score_delta=15, 21を獲得している成功パターンを適用
     2. 危険局面での非併合ペナルティ追加（新規）
        - max_y >= 2.0 かつ reactive_pairs >= 2 の場合、非併合配置(NO)に-2000.0ペナルティ
        - ワーストゲーム(score0696)のturns 61-63でmax_y=2.17-2.18, reactive_pairs=6があるにもかかわらず
          merge_available=falseのHIGH_TOWERが選択される失敗パターンを解消
     3. v162の危険局面height_multiplier無効化を維持
        - max_y >= 2.0 の全ての局面で height_multiplier=0.0 に設定
        - 即時併合機会がある場合は mergeボーナスが支配的になり即時併合が最優先される
        - 併合候補がない場合も、盤面圧縮を優先して height_penalty によるペナルティを回避
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

    # --- reactor information ---
    reactor = analysis.get("reactor", {})
    reactive_pairs = reactor.get("reactive_pairs", [])
    reactive_pair_count = len(reactive_pairs) if isinstance(reactive_pairs, list) else 0

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

    # --- v162: 危険局面でのheight_multiplier上書き ---
    # max_y >= 2.0 の全ての局面で height_multiplier=0.0 に設定し、height_penalty を無効化
    # 即時併合機会がある場合は mergeボーナスが支配的になり即時併合が最優先される
    # 併合候補がない場合も、盤面圧縮を優先して height_penalty によるペナルティを回避
    if max_y >= 2.0:
        height_mult = 0.0

    # --- v162: 危険局面での盤面圧縮最優先化版 ---
    # ワーストゲーム(score0681, score0780)の終盤失敗パターン分析に基づき、max_y >= 2.0 の危険局面での盤面圧縮を最優先
    # max_y >= 2.0 の全ての局面で height_multiplier=0.0 に設定し、height_penalty を無効化
    # これにより、即時併合機会がある場合は mergeボーナスが支配的になり即時併合が最優先される
    # 併合候補がない場合も、盤面圧縮を優先して height_penalty によるペナルティを回避
    # フィルタリング条件: MEDIUMフェーズ(0.8 <= max_y < 1.8)では reactive_pairs >= 2
    # HIGHフェーズ(max_y >= 1.8)では reactive_pairs >= 3
    # refs: tmp/batch_summary.txt, tmp/improve_brief.md, game_history/20260311_032720_score0681.jsonl turns 52-59
    #       game_history/20260311_031742_score0780.jsonl turns 59-65
    dangerous_situation_medium = phase == "MEDIUM" and reactive_pair_count >= 2
    dangerous_situation_high = max_y >= 1.8 and reactive_pair_count >= 3
    dangerous_situation = dangerous_situation_medium or dangerous_situation_high

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
        # 盤面圧縮優先戦略
        # max_yが高い盤面では、併合候補の中でも着地Yが低いものを優先する傾向がある
        # LOW/MEDIUMフェーズのheight_multを維持（1.0, 1.4）し、HIGHフェーズはheight_mult=1.8
        height_multiplier = 30.0 if phase != "LOW" else 15.0

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

        # ----- evaluation axis 6: chain merge bonus (v159: 基礎距離4.0版） -----
        # ワーストゲームではmax_y>2.5の盤面でもchain_mergeが有効だった可能性がある
        # そのため、着地低でchain_mage_bonusが高い配置を評価する
        # ワーストゲームでの盤面圧縮戦略（盤面を下げる）と整合させるため、v159のパラメータを維持

        if merge_grade in ["DIRECT", "NEAR"] and result.get("merges"):
            merges = result["merges"]
            if merges:
                best_merge = min(merges, key=lambda m: m.get("dist", float("inf")))
                target_x = best_merge.get("x", 0)
                target_y = best_merge.get("y", 0)

                # v159: 着地高に応じた動的調整
                chain_distance_max = 4.0 + landing_y * 0.6
                chain_bonus_multiplier = 450.0 + landing_y * 150.0

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

        # ----- evaluation axis 7: early game merge priority (NEW) -----
        # 危険局面（max_y >= 1.8, reactive_pairs >= 3）は、盤面圧縮が最優先
        # 早期マージで盤面を下げることは、後の盤面圧縮を容易にする
        # max_y < -2.0 かつ piece_count <= 15 の場合、EARLY_MERGE_PRIORITYを適用
        early_game = max_y < -2.0
        piece_count_threshold = 15

        if early_game and merge_grade == "NEAR":
            score += 500.0  # 危険局面での強力な早期マージ優先
            reasons.append("EARLY_MERGE_PRIORITY")

        # ----- evaluation axis 8: MEDIUM_TOWER selection promotion (v174 from v3805) -----
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

        # ----- evaluation axis 9: Reactive merge priority in danger (v163: NEW) -----
        # ワーストゲーム(score0696)の終盤8ターン(turns 58-65)でmax_y>=2.0, reactive_pairs=4-6があるにもかかわらず
        # merge_available=falseが続き、HIGH_TOWER/HIGH_LAYERが選択され続け、max_y=3.05まで上昇してゲームオーバーになる失敗パターンを特定。
        # v177(best_score5310)のreactive_pairs-based merge priorityを導入し、max_y >= 2.0 かつ reactive_pairs >= 2 の危険局面で
        # 即時併合候補(DIRECT/NEAR)に+1000.0ボーナスを与え、非併合配置(NO)に-2000.0ペナルティを課すことで即時併合を強力に優先。
        danger_immediate_merge = max_y >= 2.0 and reactive_pair_count >= 2
        if danger_immediate_merge:
            if merge_grade in ["DIRECT", "NEAR"]:
                score += 1000.0
                reasons.append("DANGER_IMMEDIATE_MERGE_PRIORITY")
            elif merge_grade == "NO":
                score -= 2000.0
                reasons.append("DANGER_NO_MERGE_PENALTY")

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
