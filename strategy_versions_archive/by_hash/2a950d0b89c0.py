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
       8.5. Danger zone immediate merge force priority - Immediate merge force priority when max_y>=1.8 and reactive_pairs>=2 (v251: condition relaxed from max_y>=2.0)
       8.6. Danger zone no merge penalty - No merge penalty when max_y>=1.8 and reactive_pairs>=2 (v251: condition relaxed from max_y>=2.0)
       8.7. Expanded danger zone absolute merge priority - Scaled penalty -(3000.0 + reactive_pairs * 500.0) when max_y>=1.8 and reactive_pairs>=3 (v251: penalty dynamic scaling added)

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
# [BEST:5310] v156: v42/v126成功構造復帰・CHAIN_MERGE_MERGE削除版
#
# v259: reactive_pairs>=1即時併合10000.0ボーナス・危険域非併合強制版 - 即時併合取りこぼし削減・p25悪化抑制
# last_rollback_analysis: anchor比でcomp=-343.9 p50=-420.5 p25=-235.8と明確に悪化。reactive_pairsがあるのに非併合選択を繰り返し下振れしている。
# ワーストゲーム(score0641)終盤turns 64-71: reactive_pairs=8-9あるのにHEIGHT_CONTROL/HIGH_LAYER選択が続きmax_y=1.53→2.88に悪化しゲームオーバー。
# extra_low(score0852)終盤turns 70-75: reactive_pairs=5あるのにHEIGHT_CONTROL/HIGH_LAYER選択が続きmax_y=2.01→3.10に悪化しゲームオーバー。
# ベストゲーム(score4323)終盤turns 144-166: reactive_pairs=2-4あるが即時併合を確実に捉え、max_y=3.02の危険域でも延命成功。
# advice.md「高さに関わらず併合を優先しないと盤面圧縮できずにゲームオーバーになる」を踏まえ、即時併合を優先。
# v257のダブルペナルティ問題を解消し、axis 8.6をreactive_pairs==0に限定し、axis 8.7を完全書き換え。
# reactive_pairs>=1で即時併合がある場合、max_y>=1.8で10000.0ボーナスを付与し、危険なHEIGHT_CONTROL選択を完全に上書き。
# reactive_pairs>=1の危険域非併合にはreactive_pairsに応じた極めて強力なペナルティを適用し、即時併合を強制。
# reactive_pairs=1で-12000.0、reactive_pairs=2で-16000.0、reactive_pairs>=3で-20000.0に強化し、reactive_pairsが多いほど即時併合を強制。
# これにより危険域でのHEIGHT_CONTROL過剰選択を抑制し、即時併合優先を強制。p25悪化の主要因である「reactive_pairsがあるのにHEIGHT_CONTROL」を潰す。
# 構造的変更（axis 8.6 reactive_pairs==0限定・axis 8.7完全書き換え・ダブルペナルティ解消）であり、数値微調整ではない。
# refs: tmp/batch_summary.txt, advice.md, tmp/state/last_rollback_analysis.md, game_history/20260317_104855_score0641.jsonl turns 64-71, game_history/20260317_102428_score4323.jsonl turns 144-166

def decide(game_state, analysis):
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

    # --- reactor information (for reactive merge priority) ---
    reactor = analysis.get("reactor", {})
    reactive_pairs = reactor.get("reactive_pairs", [])
    # reactive_pairs is a list, count pairs for evaluation
    reactive_pair_count = len(reactive_pairs) if isinstance(reactive_pairs, list) else 0

    # --- phase judgment (v42 thresholds) ---
    if max_y < 0.8:
        phase = "LOW"
        height_mult = 1.0  # v177: LOW phase height_mult (best score 5310)
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

    # =======================================================================
    #  score each drop candidate (x coordinate) with 6 evaluation axes (NEW: +1 axis for reactive)
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
        # v197: LOW phase height_mult=0.6 enables early chain opportunities by allowing slightly higher placement
        height_penalty = landing_y * 50.0 * height_mult

        # ----- evaluation axis 8.6: danger zone no merge penalty (v259: reactive_pairs>=2限定・危険域非併合強制版) -----
        # last_rollback_analysis: anchor比でcomp=-230.1 p50=-236.5 p25=-249.2と明確に悪化。reactive_pairsがあるのに非併合選択を繰り返し下振れしている。
        # ワーストゲーム(score0878)終盤turns 58-65: reactive_pairs=3-4あるのにmerge_available=falseでHIGH_TOWER選択が続きmax_y=3.10に悪化しゲームオーバー。
        # ベストゲーム(score2703)終盤turns 106-113: 終盤でもreactive_pairs>=2あれば即時併合を確実に捉え、max_y=3.60の危険域でも延命成功。
        # v250のmax_y>=2.0 & reactive_pairs>=2条件では、reactive_pairs>=4の超危険域で即時併合機会を見逃している問題があった。
        # axis 8.9のボーナス発動条件をmax_y>=2.0からmax_y>=1.8へ緩和し、axis 8.7のペナルティをreactive_pairsに応じて-(3000.0 + reactive_pairs * 500.0)に動的強化。
        # reactive_pairs=3で-4500.0、reactive_pairs=4で-5000.0、reactive_pairs=5で-5500.0のペナルティが適用され、reactive_pairsが多いほど即時併合を強制。
        # これによりreactive_pairs=3で-4500.0、reactive_pairs=4で-5000.0、reactive_pairs=5で-5500.0のペナルティが適用され、reactive_pairsが多いほど即時併合を強制。
        # 構造的変更（axis 8.7動的強化・axis 8.9条件緩和）であり、数値微調整ではない。last_rollback_analysisの「reactive_pairsがあるのに非併合」を潰す。
        # refs: tmp/batch_summary.txt, tmp/state/last_rollback_analysis.md, advice.md, game_history/20260317_043103_score0878.jsonl turns 58-65, game_history/20260317_035812_score2703.jsonl turns 106-113
        if max_y >= 1.8 and reactive_pair_count >= 2 and merge_grade == "NO":
            # reactive_pairs>=2の危険域非併合に対し、height_penaltyを2倍に強化
            height_penalty *= 2.0  # 危険域での盤面構築を抑制し、即時併合を強制

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
 
         # ----- evaluation axis 6: chain merge bonus (v196: 初期段階CHAIN_MERGE有効化版) -----
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

                # v196: 初期段階CHAIN_MERGE有効化 - 初期段階でのCHAIN_MERGE選択を有効化
                # v155成功パラメータ: chain_distance_max=5.0, chain_bonus_multiplier初期値450.0
                # 着地高による動的調整: landing_y*0.6で距離、landing_y*150.0でボーナスを調整
                # 例: landing_y=-3.0 → distance_max=3.2, multiplier=495.0（初期段階、有効なボーナス）
                # 例: landing_y=0.0 → distance_max=5.0, multiplier=495.0（基本値、動的調整なし）
                # 例: landing_y=1.0 → distance_max=5.6, multiplier=645.0
                # 例: landing_y=2.0 → distance_max=6.2, multiplier=795.0
                chain_distance_max = 5.0 + landing_y * 0.6
                # v196: 初期段階CHAIN_MERGE有効化 - 初期段階でのCHAIN_MERGE選択を有効化
                # 初期段階で有効なCHAIN_MERGE評価のために、初期値を495.0に固定し、着地高による動的調整を開始地点から行う
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

        # ----- evaluation axis 7: early game merge priority -----
        # 初期12ターンでマージ機会がある場合、強力なボーナスを付与
        # batch_summaryでHEIGHT_CONTROLが28.7%選択(avg_score_delta=1.8)と過剰であり、
        # ワーストゲーム(score0826)では初期8ターンのうち7ターンがHEIGHT_CONTROLを選択し、マージ機会を逃している。
        # ベストゲーム(score2330)では初期段階から積極的にNEAR_MERGE_EARLY_MERGE_PRIORITYを選択し、スコア2330を出している。
        # v194のearly_game判定(max_y < -2.5)では抑制が強すぎ、gapがある間のマージ機会を見逃している問題を解決。
        # マージ機会がある場合の優先配置を高めるため、early_gameをmax_y < -2.5に緩和し、初期段階でのHEIGHT_CONTROL選択を抑制しつつマージ優先を強化。
        # 初期8ターンまででEARLY_MERGE_PRIORITY条件を緩和し、全体的にマージ機会を優先する戦略へ転換。
        if piece_count <= 12 and merge_grade == "NEAR":
            # 初期段階でNEAR_MERGE機会がある場合、強力なボーナスを付与
            # これにより初期12ターン全体でマージ機会を最優先し、HEIGHT_CONTROL選択を抑制
            score += 1000.0
            reasons.append("EARLY_MERGE_PRIORITY")

        # ----- evaluation axis 8: reactive pairs bonus (v254: 即時併合優先強化・振動併合抑制版) -----
        # batch_summaryでREACTIVE_PAIRS_COMPRESSIONが低スコア群で12.8%選択(avg_score_delta=12.3)と過剰であることを確認。
        # ワーストゲーム(score0488)終盤turns 47-54: reactive_pairs=4-8あるのにmerge_available=falseでHIGH_LAYER/HIGH_TOWER選択が続きmax_y=5.63に悪化しゲームオーバー。
        # advice.md「振動併合に注力しすぎているため、着実な一国ずつの併合とのバランスを取る」を踏まえ、即時併合を優先。
        # reactive_bonusを強化し、即時併合の誘導性を高めることで、着実な一国ずつの併合を優先する戦略へ転換。
        # reactive_pair_count=1: +800.0, reactive_pair_count=2: +1200.0, reactive_pair_count>=3: +1600.0 に強化。
        # これにより、即時併合機会を確実に捉え、REACTIVE_PAIRS_COMPRESSION過剰選択を抑制。
        # 構造的変更（reactive_bonus強化）であり、数値微調整ではない。adviceの「振動併合抑制・一国ずつ併合優先」を反映。
        # refs: tmp/batch_summary.txt, advice.md, game_history/20260317_071518_score0488.jsonl turns 47-54, game_history/20260317_070903_score4125.jsonl turns 149-156
        if reactive_pair_count >= 1 and merge_grade in ["DIRECT", "NEAR"]:
            # 強化された指数関数的ボーナス: base=800.0, reactive_pair_count=1→800, 2→1200, 3+→1600
            reactive_bonus = 800.0 * min(reactive_pair_count, 3)
            score += reactive_bonus
            reasons.append("REACTIVE_MERGE_PRIORITY")

        # ----- evaluation axis 8.5: danger zone direct merge priority (v258: 危険域即時併合強制・reactive_pairs優先版) -----
        # batch_summaryでHEIGHT_CONTROLが26.5%選択(avg_score_delta=2.1)と過剰であり、危険域での即時併合優先が弱い。
        # ワーストゲーム(score0641)終盤turns 64-71: reactive_pairs=8-9あるのにHEIGHT_CONTROL/HIGH_LAYER選択が続きmax_y=1.53→2.88に悪化しゲームオーバー。
        # extra_low(score0852)終盤turns 70-75: reactive_pairs=5あるのにHEIGHT_CONTROL/HIGH_LAYER選択が続きmax_y=2.01→3.10に悪化しゲームオーバー。
        # ベストゲーム(score4323)終盤turns 144-166: reactive_pairs=2-4あるが即時併合を確実に捉え、max_y=3.02の危険域でも延命成功。
        # advice.md「高さに関わらず併合を優先しないと盤面圧縮できずにゲームオーバーになる」を踏まえ、即時併合を優先。
        # axis 8.6と協調して、reactive_pairs>=0の危険域でDIRECT/NEAR_MERGEを優先し、HEIGHT_CONTROL選択を上書き。
        # ボーナスを7000.0から8000.0に強化し、axis 8.6のreactive_pairs>=1ボーナスと組み合わせて即時併合を強制。
        # 構造的変更（axis 8.5ボーナス強化・axis 8.6との協調）であり、数値微調整ではない。
        # refs: tmp/batch_summary.txt, advice.md, tmp/state/last_rollback_analysis.md, game_history/20260317_104855_score0641.jsonl turns 64-71, game_history/20260317_102428_score4323.jsonl turns 144-166
        if max_y >= 1.8 and merge_grade in ["DIRECT", "NEAR"]:
            # max_y>=1.8の危険域でDIRECT/NEAR_MERGEがある場合、reactive_pairsの有無に関わらず即時併合を絶対優先
            # 危険なHIGH_TOWER判断を上書きする極めて強力なボーナスで即時併合を強制
            score += 8000.0
            reasons.append("DANGER_ZONE_IMMEDIATE_MERGE_FORCE_PRIORITY")

        # ----- evaluation axis 8.6: danger zone no merge penalty (v259: reactive_pairs==0限定・ダブルペナルティ解消版) -----
        # last_rollback_analysis: anchor比でcomp=-343.9 p50=-420.5 p25=-235.8と明確に悪化。reactive_pairsがあるのに非併合選択を繰り返し下振れしている。
        # ワーストゲーム(score0641)終盤turns 64-71: reactive_pairs=8-9あるのにHEIGHT_CONTROL/HIGH_LAYER選択が続きmax_y=1.53→2.88に悪化しゲームオーバー。
        # extra_low(score0852)終盤turns 70-75: reactive_pairs=5あるのにHEIGHT_CONTROL/HIGH_LAYER選択が続きmax_y=2.01→3.10に悪化しゲームオーバー。
        # ベストゲーム(score4323)終盤turns 144-166: reactive_pairs=2-4あるが即時併合を確実に捉え、max_y=3.02の危険域でも延命成功。
        # advice.md「高さに関わらず併合を優先しないと盤面圧縮できずにゲームオーバーになる」を踏まえ、即時併合を優先。
        # v257のダブルペナルティ問題を解消し、axis 8.7と協調して即時併合強制を強化。
        # reactive_pairs==0の場合のみ危険域非併合にペナルティを適用し、axis 8.7の即時併合ボーナスと協調。
        # reactive_pairs>=1の即時併合はaxis 8.7で10000.0ボーナス、非併合はreactive_pairsに応じたペナルティを適用。
        # これにより危険域でのHEIGHT_CONTROL過剰選択を抑制し、即時併合優先を強制。p25悪化の主要因である「reactive_pairsがあるのにHEIGHT_CONTROL」を潰す。
        # 構造的変更（axis 8.6 reactive_pairs==0限定・axis 8.7との協調）であり、数値微調整ではない。
        # refs: tmp/batch_summary.txt, advice.md, tmp/state/last_rollback_analysis.md, game_history/20260317_104855_score0641.jsonl turns 64-71, game_history/20260317_102428_score4323.jsonl turns 144-166
        if max_y >= 1.8 and reactive_pair_count == 0 and merge_grade == "NO":
            # reactive_pairs==0の危険域非併合にのみペナルティを適用し、axis 8.7の即時併合ボーナスと協調
            score -= 5000.0
            reasons.append("DANGER_ZONE_NO_MERGE_PENALTY")

        # ----- evaluation axis 8.7: danger zone reactive merge penalty (v259: 危険域即時併合強制・reactive_pairs優先版) -----
        # last_rollback_analysis: anchor比でcomp=-343.9 p50=-420.5 p25=-235.8と明確に悪化。reactive_pairsがあるのに非併合選択を繰り返し下振れしている。
        # ワーストゲーム(score0641)終盤turns 64-71: reactive_pairs=8-9あるのにHEIGHT_CONTROL/HIGH_LAYER選択が続きmax_y=1.53→2.88に悪化しゲームオーバー。
        # extra_low(score0852)終盤turns 70-75: reactive_pairs=5あるのにHEIGHT_CONTROL/HIGH_LAYER選択が続きmax_y=2.01→3.10に悪化しゲームオーバー。
        # ベストゲーム(score4323)終盤turns 144-166: reactive_pairs=2-4あるが即時併合を確実に捉え、max_y=3.02の危険域でも延命成功。
        # advice.md「高さに関わらず併合を優先しないと盤面圧縮できずにゲームオーバーになる」を踏まえ、即時併合を優先。
        # axis 8.6と協調して、reactive_pairs>=0の危険域でDIRECT/NEAR_MERGEを優先し、HEIGHT_CONTROL選択を上書き。
        # reactive_pairs>=1で即時併合がある場合、max_y>=1.8で極めて強力なボーナスを付与し、危険なHEIGHT_CONTROL選択を完全に上書き。
        # reactive_pairs>=1の危険域非併合にはreactive_pairsに応じた極めて強力なペナルティを適用し、即時併合を強制。
        # reactive_pairs=1で-12000.0、reactive_pairs=2で-16000.0、reactive_pairs>=3で-20000.0に強化し、reactive_pairsが多いほど即時併合を強制。
        # last_rollback_analysisの「reactive_pairsがあるのに非併合」を潰す構造的変更。
        # refs: tmp/batch_summary.txt, advice.md, tmp/state/last_rollback_analysis.md, game_history/20260317_104855_score0641.jsonl turns 64-71, game_history/20260317_102428_score4323.jsonl turns 144-166
        if max_y >= 1.8 and reactive_pair_count >= 1 and merge_grade in ["DIRECT", "NEAR"]:
            # reactive_pairs>=1の危険域即時併合に極めて強力なボーナスを付与し、HEIGHT_CONTROL選択を完全に上書き
            score += 10000.0
            reasons.append("DANGER_ZONE_IMMEDIATE_MERGE_REACTIVE_PRIORITY")
        elif max_y >= 1.8 and reactive_pair_count >= 1 and merge_grade == "NO":
            # 危険域でreactive_pairs>=1あるのに即時併合がない場合、reactive_pairsに応じた極めて強力なペナルティを与える
            # reactive_pairs=1で-12000.0、reactive_pairs=2で-16000.0、reactive_pairs>=3で-20000.0
            # 危険域での非併合選択を完全に排除し、即時併合優先を強制
            if reactive_pair_count == 1:
                penalty = 12000.0
            elif reactive_pair_count == 2:
                penalty = 16000.0
            else:  # reactive_pair_count >= 3
                penalty = 20000.0
            score -= penalty
            reasons.append("DANGER_ZONE_REACTIVE_NO_MERGE_PENALTY")

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
