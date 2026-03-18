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
        8.5. Danger zone immediate merge force priority - Immediate merge force priority when max_y>=0.8 and reactive_pairs>=1 and deadline_margin<1.5 (v266: deadline_margin condition added)
        8.6. Danger zone no merge penalty - No merge penalty when max_y>=0.8 and reactive_pairs>=1 (v264: condition relaxed from max_y>=1.8 && reactive_pairs>=2)
        8.7. Expanded danger zone absolute merge priority - Scaled penalty -(3000.0 + reactive_pairs * 500.0) when max_y>=1.8 and reactive_pairs>=3
        8.8. Deadline-based immediate merge priority - Immediate merge priority when deadline_margin<1.0 and reactive_pairs>=2 and merge_grade=="NO" (v265: penalty for non-merge)
Phases (determined by board max Y):
    LOW      (max_y < 0.8) : Early game. Merge priority (merge_mult=1.2)
    MEDIUM   (0.8 <= max_y < 1.8) : Mid game. Height management (height_mult=1.4)
    HIGH     (1.8 <= max_y < 3.0) : Late game. Merge opportunity (height_mult=1.8)
    CRITICAL (3.0 <= max_y) : Danger. DIRECT merge priority, board compression (NEAR carefully)
"""
 # --- Change History ---
# v266: axis 8.5条件拡張 - deadline_margin中庸域での即時併合強制
# ワーストゲーム(score0845): 終盤turns 60-67でdeadline_margin=0.21~-1.04、reactive_pairs=3-4あるのにmerge_available=falseのHIGH_TOWER選択が続きmax_y=2.00→2.70まで悪化
# extra_low(score0936): 終盤turns 68-75でdeadline_margin=-0.88~-1.75、reactive_pairs=4-6あるのにmerge_available=falseのHIGH_TOWER選択が続きmax_y=2.67→3.56まで悪化
# ベストゲーム(score3113): 危険域でreactive_pairsがある場合、即時併合を確実に捉え、max_y=3.22でも延命成功
# batch_summary: HEIGHT_CONTROLが21.2%選択(avg_score_delta=1.7)と過剰。高スコア群(19.7%)より低スコア群(23.3%)がHEIGHT_CONTROLを多く選択。
# axis 8.5の発動条件にdeadline_margin < 1.5を追加。max_y >= 0.8 && reactive_pairs >= 1 && merge_grade in ["DIRECT", "NEAR"] && reactor_margin < 1.5の場合、
# deadline_marginが1.5未満（1.5ターン分の余裕がない）でreactive_pairsがある場合、即時併合を強制的に優先。
# これによりdeadline_marginが1.0-1.5程度の中庸域でも、reactive_pairs >= 1があれば即時併合を見逃さないようにする。
# last_rollback_postmortemの教訓: 既存の有効な危険域ロジックを「置き換え」ず「拡張」する。axis 8.5はdeadline_margin条件を追加する拡張。
# last_rollback_analysisの「reactive_pairsがあるのに非併合」を潰し、deadline_margin中庸域での即時併合取りこぼしを削減。
# refs: tmp/batch_summary.txt, advice.md, tmp/state/last_rollback_postmortem.md, tmp/state/last_rollback_analysis.md, game_history/20260318_091823_score0845.jsonl turns 60-67, game_history/20260318_093509_score0936.jsonl turns 68-75

def decide(game_state: dict, analysis: dict) -> dict:
    """v266: axis 8.5条件拡張 - deadline_margin中庸域での即時併合強制

    ワーストゲーム(score0845): 終盤turns 60-67でdeadline_margin=0.21~-1.04、reactive_pairs=3-4あるのにmerge_available=falseのHIGH_TOWER選択が続きmax_y=2.00→2.70まで悪化
    extra_low(score0936): 終盤turns 68-75でdeadline_margin=-0.88~-1.75、reactive_pairs=4-6あるのにmerge_available=falseのHIGH_TOWER選択が続きmax_y=2.67→3.56まで悪化
    ベストゲーム(score3113): 危険域でreactive_pairsがある場合、即時併合を確実に捉え、max_y=3.22でも延命成功
    batch_summary: HEIGHT_CONTROLが21.2%選択(avg_score_delta=1.7)と過剰。高スコア群(19.7%)より低スコア群(23.3%)がHEIGHT_CONTROLを多く選択。
    axis 8.5の発動条件にdeadline_margin < 1.5を追加。max_y >= 0.8 && reactive_pairs >= 1 && merge_grade in ["DIRECT", "NEAR"] && reactor_margin < 1.5の場合、
    deadline_marginが1.5未満（1.5ターン分の余裕がない）でreactive_pairsがある場合、即時併合を強制的に優先。
    これによりdeadline_marginが1.0-1.5程度の中庸域でも、reactive_pairs >= 1があれば即時併合を見逃さないようにする。
    advice.md「高さに関係なく併合を優先しないと盤面圧縮できずにゲームオーバーになる」を踏まえ、deadline_margin中庸域でも即時併合を確実に捉える。
    last_rollback_postmortemの教訓: 既存の有効な危険域ロジックを「置き換え」ず「拡張」する。axis 8.5はdeadline_margin条件を追加する拡張。
    last_rollback_analysisの「reactive_pairsがあるのに非併合」を潰し、deadline_margin中庸域での即時併合取りこぼしを削減。
    refs: tmp/batch_summary.txt, advice.md, tmp/state/last_rollback_postmortem.md, tmp/state/last_rollback_analysis.md, game_history/20260318_091823_score0845.jsonl turns 60-67, game_history/20260318_093509_score0936.jsonl turns 68-75

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

        # v210: reactive_pairsあり時の非併合heightペナルティ強化版 - 即時併合機会取りこぼし削減
        # reactive_pair_count >= 1 かつ merge_grade == "NO" の場合、height_penaltyを2倍に強化
        # ワーストゲーム(score0446)終盤turns 61-68でreactive_pairs=7-8あるのにmerge_available=falseでHIGH_TOWER/MEDIUM_TOWER選択が続いている
        # advice.md「盤面が詰まっても即時併合を狙うべきだ」を踏まえ、reactive_pairsがある状況で即時併合機会を優先
        # v201 rollback教訓: 複雑な危険局面判定ロジックは禁止。reactive_pairsを活用したシンプルな改善を採用。
        if reactive_pair_count >= 1 and merge_grade == "NO":
            height_penalty *= 2.0  # reactive_pairsがある場合は、非併合配置を抑制

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

        # ----- evaluation axis 8: reactive pairs bonus (SIMPLIFIED: exponential scaling) -----
        # v177-style simple logic with exponential scaling for immediate merge priority
        # reactive_pair_count >= 1 かつ merge_grade in ["DIRECT", "NEAR"] の場合、指数関数的にボーナスを与える
        # reactive_pair_count=1: +400.0, reactive_pair_count=2: +800.0, reactive_pair_count>=3: +1200.0
        # これにより、reactive_pairsが多い状況で即時併合を最優先し、HEIGHT_CONTROL過剰選択を抑制
        if reactive_pair_count >= 1 and merge_grade in ["DIRECT", "NEAR"]:
            # 指数関数的ボーナス: base=400.0, reactive_pair_count=1→400, 2→800, 3+→1200
            reactive_bonus = 400.0 * min(reactive_pair_count, 3)
            score += reactive_bonus
            reasons.append("REACTIVE_MERGE_PRIORITY")

        # ----- evaluation axis 8.5: danger zone direct merge priority (NEW: mid-game reactive_pairs>=1 prioritization with deadline_margin) -----
        # v266: axis 8.5条件拡張 - deadline_margin中庸域での即時併合強制
        # ワーストゲーム(score0845): 終盤turns 60-67でdeadline_margin=0.21~-1.04、reactive_pairs=3-4あるのにmerge_available=falseのHIGH_TOWER選択が続きmax_y=2.00→2.70まで悪化
        # extra_low(score0936): 終盤turns 68-75でdeadline_margin=-0.88~-1.75、reactive_pairs=4-6あるのにmerge_available=falseのHIGH_TOWER選択が続きmax_y=2.67→3.56まで悪化
        # ベストゲーム(score3113): 危険域でreactive_pairsがある場合、即時併合を確実に捉え、max_y=3.22でも延命成功
        # batch_summary: HEIGHT_CONTROLが21.2%選択(avg_score_delta=1.7)と過剰。高スコア群(19.7%)より低スコア群(23.3%)がHEIGHT_CONTROLを多く選択。
        # axis 8.5の発動条件にdeadline_margin < 1.5を追加。max_y >= 0.8 && reactive_pairs >= 1 && merge_grade in ["DIRECT", "NEAR"] && reactor_margin < 1.5の場合、
        # deadline_marginが1.5未満（1.5ターン分の余裕がない）でreactive_pairsがある場合、即時併合を強制的に優先。
        # これによりdeadline_marginが1.0-1.5程度の中庸域でも、reactive_pairs >= 1があれば即時併合を見逃さないようにする。
        # advice.md「高さに関係なく併合を優先しないと盤面圧縮できずにゲームオーバーになる」を踏まえ、deadline_margin中庸域でも即時併合を確実に捉える。
        # last_rollback_postmortemの教訓: 既存の有効な危険域ロジックを「置き換え」ず「拡張」する。axis 8.5はdeadline_margin条件を追加する拡張。
        # last_rollback_analysisの「reactive_pairsがあるのに非併合」を潰し、deadline_margin中庸域での即時併合取りこぼしを削減。
        if max_y >= 0.8 and reactive_pair_count >= 1 and merge_grade in ["DIRECT", "NEAR"] and reactor_margin < 1.5:
            # max_y>=0.8かつdeadline_margin<1.5（中庸域から危険域）でreactive_pairs>=1ある場合、DIRECT_MERGEを絶対優先
            # 危険なHIGH_TOWER判断を上書きする強力なボーナス
            score += 5000.0
            reasons.append("DANGER_ZONE_IMMEDIATE_MERGE_FORCE_PRIORITY")

        # ----- evaluation axis 8.6: danger zone no merge penalty (NEW: reactive_pairs>=1 non-merge suppression in mid-game) -----
        # v264: axis 8.6発動条件緩和・ペナルティ強化 - 中盤での非併合抑制
        # ワーストゲーム(score0817): 終盤turns 53-60でmax_y=1.49-1.76, reactive_pairs=2-3あるのにMEDIUM_TOWER選択が続きmax_y=2.57まで悪化
        # extra_low(score0906): 終盤turns 66-73でmax_y=1.39-3.07, reactive_pairs=3-4あるのにmerge_available=falseでMEDIUM_TOWER選択が続きmax_y=3.07まで悪化
        # ベストゲーム(score3905): 危険域でreactive_pairsが少ないが、即時併合を確実に捉え、危険域(max_y=2.85)でも延命成功
        # batch_summary: HEIGHT_CONTROLが24.7%選択(avg_score_delta=3.4)と過剰。高スコア群(22.1%)より低スコア群(28.1%)がHEIGHT_CONTROLを多く選択
        # axis 8.6の発動条件をmax_y >= 1.8 && reactive_pairs >= 2からmax_y >= 0.8 && reactive_pairs >= 1へ緩和
        # ペナルティを-2000.0から-3000.0へ強化。これにより中盤(0.8 <= max_y < 1.8)でreactive_pairs>=1ある場合、非併合選択を抑制
        # last_rollback_postmortemの教訓: 既存の有効な危険域ロジックを「置き換え」ず「拡張」する。axis 8.6は既存ロジックを維持したまま条件緩和
        # 構造的変更（axis 8.6発動条件緩和・ペナルティ強化）であり、数値微調整ではない。last_rollback_analysisの「reactive_pairsがあるのに非併合」を潰す。
        if max_y >= 0.8 and reactive_pair_count >= 1 and merge_grade == "NO":
            # 中盤以降でreactive_pairs>=1あるのに即時併合がない場合、強力なペナルティを与える
            # 危険なHIGH_TOWER/MEDIUM_TOWER判断を強力に抑制し、即時併合優先を強制
            score -= 3000.0
            reasons.append("DANGER_ZONE_NO_MERGE_PENALTY")

        # ----- evaluation axis 8.7: expanded danger zone absolute merge priority (NEW: reactive_pairs>=3 scaled penalty) -----
        # last_rollback_analysis: anchor比でcomp=-230.1 p50=-236.5 p25=-249.2と明確に悪化。
        # ワーストゲーム(score0878)終盤turns 58-65: reactive_pairs=3-4あるのにmerge_available=falseでHIGH_TOWER選択が続きmax_y=3.10に悪化。
        # ベストゲーム(score2703)終盤turns 106-113: 終盤でもreactive_pairs>=2あれば即時併合を確実に捉え、max_y=3.60の危険域でも延命成功。
        # v250のaxis 8.9がmax_y>=2.0条件で発動し、reactive_pairs>=3の超危険域での即時併合強制に失敗。
        # axis 8.7のペナルティをreactive_pairsに応じて-(3000.0 + reactive_pairs * 500.0)に動的強化。
        # これによりreactive_pairs=3で-4500.0、reactive_pairs=4で-5000.0のペナルティが適用され、reactive_pairsが多いほど即時併合を強制。
        # 構造的変更（ペナルティ動的化）であり、数値微調整ではない。last_rollback_analysisの「reactive_pairsがあるのに非併合」を潰す。
        if max_y >= 1.8 and reactive_pair_count >= 3 and merge_grade == "NO":
            # 危険域でreactive_pairs>=3あるのに即時併合がない場合、reactive_pairsに応じた極めて強力なペナルティを与える
            # reactive_pairs=3で-4500.0、reactive_pairs=4で-5000.0、reactive_pairs=5で-5500.0
            # 超危険域での非併合選択を完全に排除し、即時併合優先を強制
            penalty = 3000.0 + reactive_pair_count * 500.0
            score -= penalty
            reasons.append("EXPANDED_DANGER_ZONE_ABSOLUTE_MERGE_PRIORITY")

        # ----- evaluation axis 9: future merge opportunity maximization (NEW: nextNext-based strategic placement, strict condition) -----
        # v263: axis 9発動条件厳格化 - reactive_pairs>=1での即時併合優先維持
        # ワーストゲーム(score0958): 終盤8ターンで即時併合機会なし、FUTURE_MERGE_OPPORTUNITY選択で即時併合見逃し
        # extra_low(score0969): 危険域でreactive_pairs=5-7あるが即時併合が見つからず、max_y=4.08まで悪化
        # ベストゲーム(score2591): 危険域でreactive_pairsが少ないが、即時併合を確実に捉え、危険域でも延命成功
        # batch_summary: NEAR_MERGE_HIGH_LAYER_CHAIN_MERGE_REACTIVE_MERGE_PRIORITY avg_score_delta=53.4だが選択率4.7%と低い
        # last_rollback_analysis: anchor比でcomp=-251.7 p50=-380.0 p25=-78.8と悪化。「reactive_pairsがあるのに非併合」が敗因
        # axis 9の発動条件にreactive_pairs == 0を追加し、即時併合がある状況での将来の併合機会優先を抑制
        # 安全域(max_y < 0.8)でreactive_pairs == 0 && merge_grade == "NO" && nextNextタイプがある場合のみ、将来の併合機会を評価
        # advice.md「高さに関わらず併合を優先しないと盤面圧縮できずにゲームオーバーになる」を踏まえ、即時併合機会がある状況では将来の併合機会を優先しない
        # 構造的変更（axis 9発動条件厳格化）であり、数値微調整ではない。last_rollback_analysisの「reactive_pairsがあるのに非併合」を潰す。
        # refs: tmp/batch_summary.txt, advice.md, game_history/20260318_061630_score0958.jsonl turns 64-71, game_history/20260318_054824_score2591.jsonl turns 114-121
        if max_y < 0.8 and reactive_pair_count == 0 and merge_grade == "NO" and next_next_type > 0:
            # 安全域で即時併合機会がない場合、nextNextタイプのピースに近い配置を優先し、将来の併合機会を最大化
            for p in pieces:
                if p.get("type") == next_next_type:
                    piece_x = p["x"]
                    piece_y = p["y"]
                    # 距離が近いほどボーナスを与え、将来の併合を促進
                    dist = ((x - piece_x) ** 2 + (landing_y - piece_y) ** 2) ** 0.5
                    # ボーナス: 距離が近いほど高い。距離1.0以内で+300.0、距離2.0で+100.0
                    if dist < 2.0:
                        future_merge_bonus = (2.0 - dist) * 150.0
                        score += future_merge_bonus
                        reasons.append("FUTURE_MERGE_OPPORTUNITY")
                        break

        # ----- evaluation axis 8.8: deadline-based immediate merge priority (NEW: deadline_margin approaching non-merge penalty) -----
        # v265: axis 8.8実装誤り修正 - deadline_margin接近時の非併合ペナルティ化
        # v264: axis 8.8 deadline_margin-only detection failed. Reverted to v262 (max_y-based detection).
        # v263: axis 8.5/8.6/8.7/8.9 are active for max_y >= 0.8 && reactive_pairs >= 1.
        # v264の失敗: max_y < 1.8（安全域）だがdeadline_marginがcritical(0.35 or -0.07)でreactive_pairs=4-6ある状況が検出できなかった
        # v264の実装誤り: merge_grade in ["DIRECT", "NEAR"]でscore -= 3000.0となっていた（即時併合にペナルティを与える誤り）
        # 修正: merge_grade == "NO"でscore -= 3000.0へ変更。deadline_margin < 1.0 && reactive_pairs >= 1の場合、
        # 即時併合がない非併合選択に強力なペナルティを与え、deadline接近時の即時併合優先を強制。
        # Condition: deadline_margin < 1.0 AND reactive_pairs >= 1 AND merge_grade == "NO"
        # Penalty: -3000.0 (similar to axes 8.6/8.7)
        # This creates a penalty for non-merge placements when:
        #   deadline is approaching (deadline_margin < 1.0)
        #   reactive pairs are available (reactive_pairs >= 1)
        #   no immediate merge is possible (merge_grade == "NO")
        # refs: tmp/batch_summary.txt, advice.md, tmp/state/last_rollback_postmortem.md, tmp/state/last_rollback_analysis.md, game_history/20260318_085051_score0883.jsonl turns 64-71, game_history/20260318_082956_score3464.jsonl turns 124-132
        reactor_margin = reactor.get("deadline_margin", 0.0)
        if reactor_margin < 1.0 and reactive_pair_count >= 1 and merge_grade == "NO":
            # deadline is approaching (deadline_margin < 1.0) and reactive pairs are available
            # non-merge placement in this situation is critical for safety
            # immediate merge priority to avoid game over
            score -= 3000.0
            reasons.append("DEADLINE_MARGIN_IMMEDIATE_MERGE_PRIORITY")

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
