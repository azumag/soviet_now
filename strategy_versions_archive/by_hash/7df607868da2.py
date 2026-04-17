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
# [BEST:5310] v156: v42/v126成功構造復帰・CHAIN_MERGE_MERGE削除版
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
# 例: landing_y=-3.0 → distance_max=3.2, multiplier=30.0（初期段階）
# 例: landing_y=0.0 → distance_max=5.0, multiplier=480.0（基本値、動的調整なし）
# 例: landing_y=1.0 → distance_max=5.6, multiplier=645.0
# 例: landing_y=2.0 → distance_max=6.2, multiplier=795.0
# 例: landing_y=3.0 → distance_max=6.8, multiplier=930.0
# v172: 序盤マージ優先評価軸追加版 - batch_summaryでHEIGHT_CONTROLが25.9%選択(avg_score_delta=1.6)と過剰であり、低スコア群で30.3%選択されていることを確認。
# ワーストゲーム(score0545)で初期5ターンが全てHEIGHT_CONTROLとなり併合機会を逃している失敗モードを特定。
# early_game条件下でmerge_gradeがNEARの場合、追加ボーナス800.0を付与する評価軸を追加し、初期段階でのマージ機会を最優先してHEIGHT_CONTROL選択を超強力に抑制する。
# v173: early_game判定緩和・初期10ターンマージ重視版 - batch_summaryでHEIGHT_CONTROLが26.7%選択(avg_score_delta=1.3)と依然として過剰であることを確認。
# ワーストゲーム(score0678)で初期10ターンのmax_y推移(-5.0→-2.4)を分析し、v172のearly_game判定(max_y < -3.0)が過度に厳しく
# 初期10ターンの大部分で判定されていないことを特定。
# early_game判定をmax_y < -3.0→-2.5に緩和し、EARLY_MERGE_PRIORITYの適用範囲をpiece_count <= 10→12に拡大して初期12ターン全体でマージ機会を最優先する。
# また、MEDIUM_TOWER選択を促進するための追加評価軸を追加し、高スコア群と低スコア群のMEDIUM_TOWER選択率差（13.6% vs 10.8%）を解消する。
# v174: early_game判定さらに緩和・初期12ターンマージ重視版 - batch_summaryでHEIGHT_CONTROLが26.2%選択(avg_score_delta=1.7)と依然として過剰であることを確認。
# ワーストゲーム(score0765)で初期7ターン全てHEIGHT_CONTROLを選択し、マージ機会を逃している失敗パターンを特定。
# early_game判定をmax_y < -2.5→-2.0にさらに緩和し、EARLY_MERGE_PRIORITYの適用範囲をpiece_count <= 10→12に拡大して初期12ターン全体でマージ機会を最優先する。
# また、MEDIUM_TOWER選択を促進するための追加評価軸を追加し、高スコア群と低スコア群のMEDIUM_TOWER選択率差（13.6% vs 10.8%）を解消する。
# v175: MEDIUMフェーズHEIGHT_CONTROL抑制強化版 - batch_summaryでHEIGHT_CONTROLが26.5%選択(avg_score_delta=1.1)と依然として過剰であり、スコアの標準偏差が404.0と大きいことを確認。
# v174で初期12ターンでのHEIGHT_CONTROL抑制は強化されたが、中盤以降のHEIGHT_CONTROL選択が依然として多く、これがスコアの不安定性を引き起こしていることを特定。
# MEDIUMフェーズ（0.8 <= max_y < 1.8）でheight_multiplierを30.0→20.0に削減してマージ選択を促進することでスコア安定性を向上させる。
# v176: reactor情報活用によるマージ優先評価軸追加版 - batch_summaryでHEIGHT_CONTROLが26.2%選択(avg_score_delta=1.7)と依然として過剰であることを確認。
# reactor情報のreactive_pairs（反応性のあるペア）を活用し、2つ以上ある場合にマージを優先する評価軸を追加。
# これにより、盤面に多数のマージ機会がある状況でHEIGHT_CONTROL選択を抑制し、スコア安定性を向上させる。
 # v178: axis 8.7 reactive_pairs>=2緩和・v253動的ペナルティ修正版 - axis 8.7発動頻度改善によるp25悪化潰し
 # last_rollback_postmortem: v253動的ペナルティ（-(2000.0 + reactive_pairs * 500.0)）がp25=-371.0の主因
 # failure_mode: axis 8.7発動頻度が低い（reactive_pairs>=3条件が厳しすぎる）ため、非併合が続きゲームオーバー
 # ワーストゲーム(score0327)終盤turns 44-51: reactive_pairs=7-8, merge_available=falseで非併合続きmax_y=3.07に悪化
 # axis 8.7閾値をreactive_pairs>=3から>=2に緩和し、中危険域（max_y>=1.8）での非併合をより早期に抑制
 # refs: tmp/state/last_rollback_postmortem.md, tmp/batch_summary.txt, game_history/20260316_230129_score0327.jsonl, game_history/20260316_230952_score2483.jsonl

# Merge result score: type N merge gives N*(N+1)/2 points
# Example: type1+1->2 gives +3 points, type8+8->9 gives +45 points, type14+14->15 gives +120 points
SCORE_TABLE = {i: i * (i + 1) // 2 for i in range(1, 17)}

def decide(game_state: dict, analysis: dict) -> dict:
    """v178: axis 8.7 reactive_pairs>=2緩和・v253動的ペナルティ修正版
    
    last_rollback_postmortem: v253動的ペナルティ（-(2000.0 + reactive_pairs * 500.0)）がp25=-371.0の主因。
    failure_mode: axis 8.7発動頻度が低い（reactive_pairs>=3条件が厳しすぎる）ため、非併合が続きゲームオーバー。
    ワーストゲーム(score0327)終盤turns 44-51: reactive_pairs=7-8, merge_available=falseで非併合続きmax_y=3.07に悪化。
    axis 8.7閾値をreactive_pairs>=3から>=2に緩和し、中危険域（max_y>=1.8）での非併合をより早期に抑制。
    
    v178の改善点:
     1. axis 8.7閾値緩和
        - reactive_pairs>=3 → >=2（発動頻度改善）
        - 固定ペナルティ-3000.0を維持（v253動的ペナルティ失敗を回避）
     2. v177のMEDIUMフェーズHEIGHT_CONTROL抑制を維持
        - height_multiplier: 15.0（MEDIUMフェーズ）
      3. v176のreactor情報活用によるマージ優先評価軸を維持
         - reactive_pair_count >= 1の場合、DIRECT/NEARマージに指数的ボーナス（400.0 * min(reactive_pair_count, 3)）
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
 
    # --- v174: early_game判定さらに緩和（max_y < -2.0）---
    # batch_summaryでHEIGHT_CONTROLが26.2%選択(avg_score_delta=1.7)と依然として過剰であることを確認。
    # ワーストゲーム(score0765)で初期7ターン全てHEIGHT_CONTROLを選択し、マージ機会を逃している失敗パターンを特定。
    # early_game判定をmax_y < -2.5→-2.0にさらに緩和し、初期12ターンの大部分で判定されるように調整。
    # 初期12ターンを一つのフェーズとして扱い、この期間中はマージ機会を最優先してHEIGHT_CONTROL選択を抑制。
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

    # --- deadline information (NEW: deadline_margin utilization for danger zone detection) ---
    deadline = analysis.get("deadline", {})
    deadline_margin = deadline.get("deadline_margin", float("inf"))
 
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
        # v173: 初期段階(piece_count <= 6)で併合機会がない場合、HEIGHT_CONTROL抑制をさらに強化
        # v174: early game判定をさらに緩和し、初期12ターンでHEIGHT_CONTROLを抑制
        # v175: MEDIUM phase height_multiplierを30.0→20.0に削減してHEIGHT_CONTROL抑制を強化
        # v177: MEDIUMフェーズHEIGHT_CONTROL抑制強化版
        # high score games (23.9%) and low score games (32.5%) in low score games is 8.6% more HEIGHT_CONTROL selected
        # MEDIUM phase height_multiplier = 20.0→15.0 to reduce HEIGHT_CONTROL selection to 23.9% and improve score
 
        height_multiplier = 30.0
        if early_game:
            height_multiplier = 0.2  # v169: 序盤はHEIGHT_CONTROLを抑制し、併合機会を最優先
        # v175: MEDIUMフェーズでHEIGHT_CONTROL抑制を強化
        if phase == "MEDIUM":
            height_multiplier = 15.0  # v177: v175からさらに緩和しマージ選択を促進
 
        # v173: 初期段階で併合機会がない場合、HEIGHT_CONTROL抑制をさらに強化
        if piece_count <= 6 and merge_grade == "NO":
            height_multiplier = 0.1  # 初期6ピースでマージ機会がない場合、消極的な配置を回避
 
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
        # これにより「A上にBを置くとnextNextのAの併合機会を潰す」問題を回避し、2手先の併合可能性を最大化し、即時併合機会の取りこぼしを削減する構造的改善。
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
 
        # ----- evaluation axis 6: chain merge bonus (v171: CHAIN_MERGE基本ボーナス強化) -----
        # v171: CHAIN_MERGE関連がavg_score_delta=26.9-43.2（高価値）だが選択率は3.8-9.2%と低いことを確認。
        # ワーストゲーム(score0633)で初期5ターンが全てHEIGHT_CONTROLとなり、CHAIN_MERGE選択が0回であることを特定。
        # chain_distance_max = 5.0 + landing_y * 0.6 (v155成功値に戻す、着地高に応じて拡大)
        # chain_bonus_multiplier = 480.0 + landing_y * 150.0 (初期値を450.0→480.0に強化、着地高に応じて増強）
        # 例: landing_y=-3.0 → distance_max=3.2, multiplier=30.0（初期段階）
        # 例: landing_y=0.0 → distance_max=5.0, multiplier=480.0（初期値強化）
        # 例: landing_y=1.0 → distance_max=5.6, multiplier=630.0
        # 例: landing_y=2.0 → distance_max=6.2, multiplier=780.0
        # 例: landing_y=3.0 → distance_max=6.8, multiplier=930.0
        # 例: landing_y=3.0 → distance_max=6.8, multiplier=930.0
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
                # 例: landing_y=3.0 → distance_max=6.8, multiplier=930.0
                # 例: landing_y=3.0 → distance_max=6.8, multiplier=930.0
                # 例: landing_y=3.0 → distance_max=6.8, multiplier=930.0

                chain_distance_max = 5.0 + landing_y * 0.6
                chain_bonus_multiplier = 480.0 + max(0, landing_y + 1.5) * 150.0

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
        # v174: early_game判定(max_y < -2.0)をさらに緩和し、EARLY_MERGE_PRIORITYの適用範囲をpiece_count <= 10→12に拡大して初期12ターン全体でマージ機会を最優先し、HEIGHT_CONTROL選択を抑制する。
        # 初期12ターンを一つのフェーズとして扱い、この期間中はマージ機会を最優先してHEIGHT_CONTROL選択を抑制
        # 初期12ターンを一つのフェーズとして扱い、この期間中はマージ機会を最優先してHEIGHT_CONTROL選択を抑制する。
        # v172の初期条件(early_game && merge_grade == "NEAR")を維持し、piece_count <= 12でも適用することで初期12ターン全体でマージ機会を最優先します。
        # 初期12ターンを一つのフェーズとして扱い、この期間中はマージ機会を最優先し、HEIGHT_CONTROL選択を抑制する。
        # 初期12ターンを一つのフェーズとして扱い、この期間中はマージ機会を最優先し、HEIGHT_CONTROL選択を抑制します。
        # v172の初期条件(early_game && merge_grade == "NEAR")を維持し、piece_count <= 12でも適用することで初期12ターン全体でマージ機会を最優先します。
        # 初期12ターンを一つのフェーズとして扱い、この期間中はマージ機会を最優先し、HEIGHT_CONTROL選択を抑制します。
        # 初期12ターンを一つのフェーズとして扱い、この期間中はマージ機会を最優先し、HEIGHT_CONTROL選択を抑制します。
        if (early_game or piece_count <= 12) and merge_grade == "NEAR":
            # 初期段階でNEARマージ機会がある場合、強力なボーナスを付与
            # これにより初期12ターン全体でマージ機会を最優先し、HEIGHT_CONTROL選択を抑制
            score += 800.0
            reasons.append("EARLY_MERGE_PRIORITY")
        # ----- evaluation axis 8: reactive pairs bonus (SIMPLIFIED: exponential scaling) -----
        # v177-style simple logic with exponential scaling for immediate merge priority
         # reactive_pair_count >= 1かつ merge_grade in ["DIRECT", "NEAR"] の場合、指数関数的にボーナスを与える
         # reactive_pair_count=1: +400.0, reactive_pair_count=2: +800.0, reactive_pair_count>=3: +1200.0
         # これにより、reactive_pairsが多い状況で即時併合を最優先し、HEIGHT_CONTROL過剰選択を抑制
        if reactive_pair_count >= 1 and merge_grade in ["DIRECT", "NEAR"]:
            # 指数関数的ボーナス: base=400.0, reactive_pair_count=1→400, 2→800, 3+→1200
            reactive_bonus = 400.0 * min(reactive_pair_count, 3)
            score += reactive_bonus
            reasons.append("REACTIVE_MERGE_PRIORITY")
 
        # ----- evaluation axis 8.5: danger zone direct merge priority (v179: max_y>=1.8 reactive_pairs>=1 統合強化) -----
        # ワーストゲーム(score0927)終盤turns 56-62でreactive_pairs=7-8あるのにmerge_available=falseでHIGH_TOWER/MEDIUM_TOWER選択が続いている失敗パターンを解消。
        # ベストゲーム(score1933)終盤turns 97-100でmax_y=2.38-2.73という危険域でもDIRECT_MERGEを確実に捉えている。
        # batch_summaryでHEIGHT_CONTROLが13.8%選択(avg_score_delta=0.3)と過剰であり、終盤危険域(max_y>=1.8)での即時併合優先が弱いことを確認。
        # v201 rollback教訓: 複雑な危険局面判定ロジックは禁止。reactive_pairsを活用したシンプルな改善を採用。
        # v178でaxis 8.6のdeadline_margin条件を追加したが、max_y>=1.8とほぼ重複し発動頻度が低い。
        # axis 8.5の条件をmax_y>=2.0→1.8に緩和し、reactive_pairs>=2→>=1に緩和することで、中危険域から即時併合を優先する構造変更。
        # これによりaxis 8.6のdeadline_margin条件を削除し、判断ロジックを簡素化。危険域での即時併合をより早期から優先。
        # refs: tmp/batch_summary.txt, tmp/state/last_rollback_postmortem.md, game_history/20260316_230129_score0327.jsonl turns 44-51
        if max_y >= 1.8 and reactive_pair_count >= 1 and merge_grade in ["DIRECT", "NEAR"]:
            # max_y>=1.8の中危険域からreactive_pairs>=1で即時併合を優先
            # 危険なHIGH_TOWER判断を上書きする強力なボーナス
            score += 2000.0
            reasons.append("DANGER_ZONE_DIRECT_MERGE_PRIORITY_FORCE")
 
        # ----- evaluation axis 8.7: expanded danger zone absolute merge priority (v178: reactive_pairs>=2 max_y>=1.8) -----
        # last_rollback_postmortem: v253動的ペナルティ（-(2000.0 + reactive_pairs * 500.0)）がp25=-371.0の主因
        # failure_mode: axis 8.7発動頻度が低い（reactive_pairs>=3条件が厳しすぎる）ため、非併合が続きゲームオーバー
        # ワーストゲーム(score0327)終盤turns 44-51: reactive_pairs=7-8, merge_available=falseで非併合続きmax_y=3.07に悪化
        # ワーストゲーム(score0983)終盤turns 72-75: reactive_pairs=2-3, merge_available=falseで非併合続きmax_y=3.12に悪化
        # ベストゲーム(score2483)終盤turns 124-131: reactive_pairs=1-4, 即時併合でmax_y=2.59-3.31の危険域でも延命成功
        # axis 8.7閾値をreactive_pairs>=3から>=2に緩和し、中危険域（max_y>=1.8）での非併合をより早期に抑制
        # v253の動的ペナルティ失敗（固定値-3000.0に戻す）とaxis 8.7発動条件緩和の2点改善
        # reactive_pairs>=2で即時併合がない場合に強力なペナルティを与え、p25悪化の「reactive_pairsがあるのに非併合」問題を構造的に潰す
        # refs: tmp/state/last_rollback_postmortem.md, tmp/batch_summary.txt, game_history/20260316_230129_score0327.jsonl turns 44-51, game_history/20260316_224837_score0983.jsonl turns 72-75, game_history/20260316_230952_score2483.jsonl turns 124-131
        if max_y >= 1.8 and reactive_pair_count >= 2 and merge_grade == "NO":
            # 危険域でreactive_pairs>=2あるのに即時併合がない場合、極めて強力なペナルティを与える
            # 超危険域での非併合選択を完全に排除し、即時併合優先を強制
            score -= 3000.0
            reasons.append("EXPANDED_DANGER_ZONE_ABSOLUTE_MERGE_PRIORITY")
 
        # ----- evaluation axis 9: reactive pairs default (NEW: reactive_pairs fallback for "no action" situations) -----
        # batch_summaryでHEIGHT_CONTROLが22.8%選択(avg_score_delta=1.1)と過剰であり、reactive_pairsがある状況では「何もしない」HEIGHT_CONTROLではなく、
        # reactive_pairs活用で盤面圧縮を図る戦略的思考へ切り替える。
        # reactive_pairsがある場合、即時併合がない時のデフォルト選択をHEIGHT_CONTROLからREACTIVE_PAIRS_COMPRESSIONへ変更し、盤面圧縮を優先。
        # refs: tmp/batch_summary.txt, tmp/state/last_rollback_postmortem.md, game_history/20260313_231816_score0814.jsonl turns 54-57
        if not reasons:
            if reactive_pair_count >= 1:
                reasons.append("REACTIVE_PAIRS_COMPRESSION")
 
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
