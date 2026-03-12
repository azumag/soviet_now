#!/usr/bin/env python3
"""strategy.py - Soviet Puzzle Game AI Drop Position Script

Game Overview:
  - Drop pieces, merge same type pieces (N+N -> N+1)
  - Score table: type1=1, type2=3, type3=6, ..., typeN = N*(N+1)/2
  - Board: x in [-3.0, +3.0], floor y=-4.48, deadline y=3.32
  - Player controls only drop X coordinate

   Decision Logic (10 evaluation axes):
    1. Merge bonus - High score for immediate merge (DIRECT > NEAR > FAR)
    1.5. Dangerous situation merge enhancement - Bonus for merge opportunities in dangerous situations
    1.6. Dangerous situation nextNext enhancement - Bonus when nextNext matches merged type in dangerous situations
    2. Height penalty - Penalty for high landing position (varies by phase)
    2.5. near_pairs bonus - Bonus for near_pairs in dangerous situations when immediate merge unavailable
    3. Drift penalty - Penalty for post-landing drift due to polygon shape
    4. Left-right balance correction - Bonus for correcting piece count bias
    5. nextNext centering - Center for next merge opportunity if nextNext same type
    6. Chain merge bonus - Evaluate possibility of further merges after merge
    7. Board density bonus - Prefer placement on less-dense side of board

Phases (determined by board max Y):
  LOW      (max_y < 0.8) : Early game. Merge priority (merge_mult=1.2)
  MEDIUM   (0.8 <= max_y < 1.8) : Mid game. Height management (height_mult=1.8)
  HIGH     (1.8 <= max_y < 3.0) : Late game. Merge opportunity (height_mult=1.8)
  CRITICAL (3.0 <= max_y) : Danger. DIRECT merge priority, board compression (NEAR carefully)

 v186 additional logic:
    - Dangerous situation detection: is_dangerous_situation() helper function
    - In dangerous situations, disable BOARD_DENSITY evaluation and prioritize near_pairs bonus
    - Helper function extracts common logic for all evaluation axes to reduce code duplication
    - v186: 危険局面で即時併合候補がない場合、BOARD_DENSITY評価を無効化し、near_pairsボーナスを優先
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
 # [BEST:4319] v156: v42/v126成功構造復帰・CHAIN_MERGE削除版
 # [BEST:4324] v162: MEDIUMフェーズバランス補正強化版 - balance_strength 35.0→40.0
 # v159: 序盤HEIGHT_CONTROL抑制強化版 - max_y < -1.0, height_multiplier=0.2
 # v167: 評価精度最適化版 - chain_distance 5.0→4.5縮小
 # v168: v155成功パラメータ復帰・動的調整復帰版
 # v169: HEIGHT_CONTROLフォールバック削除 - batch_summaryでHEIGHT_CONTROLが23.9%選択(avg_score_delta=2.5)と低価値を確認
 # v170: 序盤HEIGHT_CONTROL抑制拡大版 - max_y < 0.0, height_multiplier=0.1
 # v171: ボード密度評価軸追加 - batch_summaryでDEFAULT_PLACEMENTが21.7%選択(avg_score_delta=1.4)と非常に頻繁だが価値がないことを確認。
 # 低スコア群と高スコア群のmax_y推移差(初期差0.39 vs 終盤差1.64)から、序盤の中心放置が中盤以降の高さ稼ぎに失敗しているパターンを特定。
 # DEFAULT_PLACEMENT(x=0.0)を避け、密度が低い側(左or右)を優先する評価軸を追加することで、ボードの高さ稼ぎ能力を向上させスコア安定性を改善。
 # v173: 序盤HEIGHT_CONTROL抑制超強化版 - batch_summaryでDEFAULT_PLACEMENTが20.7%選択(avg_score_delta=2.2)と依然として高いことを確認。
 # ワーストゲーム(score0705)で序盤(max_y=-5.0〜-2.02)にDEFAULT_PLACEMENTが10回選択され、併合機会を逃している失敗パターンを特定。
 # v172のearly_game判定(max_y < -2.0)をmax_y < -3.0に拡大し、height_multiplierを0.2→0.1に削減して、序盤のHEIGHT_CONTROL選択を超強力に抑制。
  # これによりDEFAULT_PLACEMENTの選択率を15%未満に減らし、併合機会を最優先することでスコア安定性を向上させる。
   # v185: 危険局面即時併合優先強化版 - rollback failure mode (p25=-249.8) の解消
   # ワーストゲーム(score0565)の終盤8ターン分析で、max_y>=1.97かつreactive_pairs=2-3あるにもかかわらず
   # MEDIUM_TOWER/HIGH_TOWERが連続で選択され、即時併合機会を完全に逃している失敗パターンを特定。
   # 危険局面判定を緩和（max_y >= 2.0 → 1.5、reactive_pairs >= 2 → 1）して早期検出を強化。
   # 危険局面でHIGH_TOWER評価を3倍にして盤面整理優先を強力に抑制し、即時併合を最優先。
   # 即時併合候補がない場合、盤面整理（BOARD_DENSITY）を優先して盤面圧迫を回避。
   # これにより危険局面での即時併合機会の取りこぼしを削減し、p25=-249.8の下振れ耐性不足を解消しcomp改善とスコア安定性を向上させる。
   # refs: tmp/batch_summary.txt, tmp/improve_brief.md, tmp/state/last_rollback_analysis.md, game_history/20260313_033353_score0565.jsonl turns 48-55, advice.md
   # v187: 危険局面near_pairsボーナス超強化版 - rollback failure mode (p25=-249.8) の解消
   # ワーストゲーム(score0672)の終盤8ターン分析で、max_y=2.03-3.11かつreactive_pairs=6-7あるにもかわらず
   # HIGH_TOWER_DANGER_NO_MERGE_PENALTY_NEAR_PAIRS_OPPORTUNITYが6ターン連続で選択され、即時併合機会を完全に逃している失敗パターンを特定。
   # v186のnear_pairsボーナスが不十分で、危険局面での即時併合機会の取りこぼしが継続していたため、near_pairsボーナスを大幅に強化。
   # near_pairs_count >= 3: 800.0→1200.0、>= 2: 400.0→800.0、>= 1: 200.0（新規追加）に強化し、危険局面での盤面圧縮を促進。
   # 併せて、危険局面でlanding_y > 0.0の場合、height_penaltyの倍率を3倍→4倍に強化してHIGH_TOWER選択をさらに抑制。
   # これにより危険局面での即時併合機会の取りこぼしを削減し、p25=-249.8の下振れ耐性不足を解消しcomp改善とスコア安定性を向上させる。
   # refs: tmp/batch_summary.txt, tmp/improve_brief.md, advice.md, tmp/state/last_rollback_analysis.md, game_history/20260313_045854_score0672.jsonl turns 53-60
    # v188: 危険局面即時併合候補near_pairs活用版 - rollback failure mode (p25=-249.8) の解消
    # ワーストゲーム(score0973)の終盤8ターン分析で、max_y>=2.58かつreactive_pairs=6-7あるにもかわらず
    # 即時併合候補にnear_pairsボーナスが適用されず、near_pairsを増やす配置が評価されず、将来の即時併合機会の可能性が最大化されていない失敗パターンを特定。
    # 危険局面で即時併合候補がある場合もnear_pairsボーナスを適用し、near_pairsを増やす配置を優先評価することで、盤面圧縮を促進し、将来の即時併合機会を最大化。
    # 即時併合候補がない場合は、v187のnear_pairsボーナス（1200.0/800.0/400.0）を維持。
    # 即時併合候補がある場合は、near_pairsボーナス（900.0/600.0/300.0）を適用し、即時併合と盤面圧縮の両立を図る。
    # これにより危険局面での即時併合機会の取りこぼしを削減し、p25=-249.8の下振れ耐性不足を解消しcomp改善とスコア安定性を向上させる。
    # refs: tmp/batch_summary.txt, tmp/improve_brief.md, tmp/state/last_rollback_analysis.md, game_history/20260313_054618_score0973.jsonl turns 66-73, game_history/20260313_054034_score1960.jsonl turns 88-95, strategy.py.staging, advice.md
    # v189: 危険局面nextNext活用強化版 - rollback failure mode (p25=-249.8) の解消
    # ワーストゲーム(score0647)の終盤8ターン分析で、reactive_pairs=2-4あるにもかかわらず、MEDIUM_TOWER/HIGH_TOWERが連続で選択され、即時併合機会を完全に逃している失敗パターンを特定。
    # advice.mdの「次の次の駒（next-next piece）を考慮して配置を決める戦略へ改善」に基づき、危険局面で即時併合候補がある場合、nextNextがマージ後のタイプと一致する配置にボーナスを追加。
    # 将来の併合機会を確保し、下振れ耐性（p25）を向上させることで、成熟ランキングに残れる再現性を重視。
    # 即時併合候補でnextNextがmerged_typeと一致する場合、+300.0ボーナスを追加することで、即時併合を取りつつ将来の併合機会を確保。
    # これにより危険局面での即時併合機会の取りこぼしを削減し、p25=-249.8の下振れ耐性不足を解消しcomp改善とスコア安定性を向上させる。
    # refs: tmp/batch_summary.txt, tmp/improve_brief.md, advice.md, tmp/state/last_rollback_analysis.md, game_history/20260313_061954_score0647.jsonl turns 49-56, game_history/20260313_064959_score2292.jsonl turns 114-121, strategy.py.staging

# Merge result score: type N merge gives N*(N+1)/2 points
# Example: type1+1->2 gives +3 points, type8+8->9 gives +45 points, type14+14->15 gives +120 points
SCORE_TABLE = {i: i * (i + 1) // 2 for i in range(1, 17)}


def is_dangerous_situation(max_y, reactive_pairs, min_reactive_pairs=1):
    """危険局面判定ヘルパー関数
    
    危険局面：max_y >= 1.5（dead line に近い）かつreactive_pairsがある状態
    v185: 閾値を緩和（max_y >= 2.0 → 1.5、reactive_pairs >= 2 → 1）して早期検出を強化
    ワーストゲーム(score0565)の終盤8ターン分析で、max_y>=1.97かつreactive_pairs=2-3あるにもかかわらず
    即時併合機会を完全に逃している失敗パターンを特定。危険局面判定を緩和して早期対応を強化。
    
    Args:
        max_y: 盤面の最高Y座標
        reactive_pairs: reactor情報のreactive_pairsリスト
        min_reactive_pairs: 危険判定に必要な最小reactive_pairs数（デフォルト: 1）
    
    Returns:
        True: 危険局面, False: 安全な局面
    """
    return max_y >= 1.5 and isinstance(reactive_pairs, list) and len(reactive_pairs) >= min_reactive_pairs


def decide(game_state: dict, analysis: dict) -> dict:
    """v186: 危険局面盤面圧縮強化版 - rollback failure mode (p25=-249.8) の解消

    batch_summaryでDEFAULT_PLACEMENTが18.0%選択(avg_score_delta=1.9)と依然として高いことを確認。
    ワーストゲーム(score0345)の終盤8ターン分析で、max_y>=2.26かつreactive_pairs=6-7の危険局面で
    HIGH_TOWER_DANGER_NO_MERGE_PENALTY_NEAR_PAIRS_OPPORTUNITYが連続で選択され、即時併合機会を完全に逃している失敗パターンを特定。

    v186の改善点:
      1. 危険局面でのBOARD_DENSITY評価無効化
         - 危険局面で即時併合候補がない場合、BOARD_DENSITY評価を無効化
         - near_pairsボーナスを優先し、盤面圧縮を促進
      2. 危険局面判定の統一
         - dangerous_situation変数をループの外で一度だけ計算
         - 各評価軸で同じ値を使用することで、一貫性を確保
      3. near_pairsボーナスの優先
         - 危険局面で即時併合がない場合、near_pairs活用を最優先
         - near_pairsが多い配置を優先し、将来の即時併合機会を確保
      4. v185の危険局面即時併合優先強化を維持
         - 危険局面判定（max_y >= 1.5、reactive_pairs >= 1）を維持
         - 危険局面でHIGH_TOWER評価を3倍にして盤面整理優先を強力に抑制

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
    
    # --- v173: 序盤判定をmax_y < -3.0に拡大 ---
    early_game = max_y < -3.0
    
    # --- reactor情報を事前取得（危険局面判定で使用） ---
    reactor = analysis.get("reactor", {})
    reactive_pairs = reactor.get("reactive_pairs", [])

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
    merge_result_type = min(next_type + 1, 16)
    type_merge_bonus = SCORE_TABLE.get(merge_result_type, 10) * 10 + 300

    # --- v149: pre-calculate merged type (for chain judgment) ---
    merged_type = min(next_type + 1, 16)

    # --- v171: Calculate board density (for new evaluation axis 7) ---
    # Count pieces and calculate weighted height on each side
    # Density is weighted by piece height to avoid stacking on already-high side
    left_density = 0.0
    right_density = 0.0
    for p in pieces:
        x = p["x"]
        y = p["y"]
        # Weight density by height (higher pieces contribute more to density)
        weight = max(0, y + 4.0)  # y=-4.48 at bottom, so shift to positive
        if x < 0:
            left_density += weight
        else:
            right_density += weight

    # Normalize densities
    total_density = left_density + right_density
    if total_density > 0:
        left_density /= total_density
        right_density /= total_density

    # =======================================================================
    #  score each drop candidate (x coordinate) with 7 evaluation axes
    # =======================================================================
    
    # ----- v186: 危険局面判定（共通変数） -----
    # 危険局面判定を一度だけ計算し、各評価軸で同じ値を使用する
    # v186: 危険局面盤面圧縮強化版 - ワーストゲーム(score0345)の終盤8ターン分析に基づき
    # 危険局面でのBOARD_DENSITY評価を無効化し、near_pairsボーナスを優先
    dangerous_situation = is_dangerous_situation(max_y, reactive_pairs, min_reactive_pairs=1)
    
    # ----- v185: 危険局面候補フィルタリング強化版 -----
    # 危険局面では即時併合候補のみを対象とし、盤面整理優先の判断を排除
    # v185: rollback failure mode (p25=-249.8) の解消
    # ワーストゲーム(score0565)の終盤8ターン分析で、max_y>=1.97かつreactive_pairs=2-3あるにもかかわらず
    # MEDIUM_TOWER/HIGH_TOWERが連続で選択され、即時併合機会を完全に逃している失敗パターンを特定。
    # 危険局面判定を緩和（max_y >= 2.0 → 1.5、reactive_pairs >= 2 → 1）して早期検出を強化。
    # 危険局面で即時併合候補がある場合、それらのみを評価して即時併合を最優先。
    # v186: 危険局面で即時併合候補がない場合、全候補を評価してnear_pairsボーナスを優先
    if dangerous_situation:
        # 即時併合可能な候補があるかチェック
        merge_candidates = [r for r in results if r.get("merge_grade", "NO") != "NO"]
        
        if merge_candidates:
            # 即時併合候補がある場合、それらのみを評価
            results = merge_candidates
        else:
            # 即時併合候補がない場合、全候補を評価してnear_pairsボーナスを優先
            # v186: BOARD_DENSITY評価を無効化し、near_pairsボーナスを優先して盤面圧縮を促進
            pass  # results = results（変更なし）
    
    for result in results:
        x = result["x"]
        landing_y = result.get("landing_y", 0)
        drift_x = result.get("drift_x", 0)
        drift_unc = result.get("drift_unc", 0)
        merge_grade = result.get("merge_grade", "NO")  # DIRECT/NEAR/FAR/NO

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
            score += 200.0 * merge_mult
            reasons.append("FAR_MERGE")

        # ----- evaluation axis 1.5: dangerous situation merge enhancement -----
        # 危険局面で即時併合機会がある場合、さらに強化する
        # v185: 危険局面判定緩和（min_reactive_pairs=1）に合わせて即時併合機会強化を更新
        # 危険局面判定閾値を max_y >= 1.5 に変更し、reactive_pairs >= 1 で早期検出
        # 危険局面での即時併合機会強化
        # v186: 共通変数 dangerous_situation を使用
        
        if dangerous_situation and merge_grade != "NO":
            # 危険局面で即時併合機会がある場合、さらに強化
            # DIRECT/NEAR に追加ボーナス
            if merge_grade in ["DIRECT", "NEAR"]:
                score += 600.0
                reasons.append("DANGER_MERGE_ENHANCEMENT")
            elif merge_grade == "FAR":
                score += 200.0
                reasons.append("DANGER_MERGE_ENHANCEMENT")
        
        # ----- evaluation axis 1.6: dangerous situation nextNext enhancement -----
        # 危険局面で即時併合候補がある場合、nextNextがマージ後のタイプ（next_type + 1）と一致するならボーナス
        # 将来の併合機会を確保し、下振れ耐性（p25）を向上させる
        # refs: advice.md, tmp/batch_summary.txt, tmp/improve_brief.md, tmp/state/last_rollback_analysis.md, game_history/20260313_061954_score0647.jsonl turns 49-56, game_history/20260313_064959_score2292.jsonl turns 114-121
        
        if dangerous_situation and merge_grade != "NO":
            merged_type = min(next_type + 1, 16)  # マージ後のタイプ
            if next_next_type == merged_type:
                score += 300.0
                reasons.append("DANGER_NEXT_SAME_MERGE")
 
        # ----- evaluation axis 2: height penalty -----
        # v173: early_game判定（max_y < -3.0）の場合、height_multiplierを0.1に削減
        # これにより序盤のHEIGHT_CONTROL選択を超強力に抑制し、併合機会を最優先
        height_multiplier = 30.0
        if early_game:
            height_multiplier = 0.1  # v173: 序盤はHEIGHT_CONTROLを超強力に抑制

        # 危険局面での即時併合優先強化（閾値1.5、reactive_pairs>=1）
        # v185: rollback failure mode (p25=-249.8) の解消
        # ワーストゲーム(score0565)の終盤8ターン分析で、max_y>=1.97かつreactive_pairs=2-3あるにもかかわらず
        # MEDIUM_TOWER/HIGH_TOWERが連続で選択され、即時併合機会を完全に逃している失敗パターンを特定。
        # 危険局面判定を緩和（max_y >= 2.0 → 1.5、reactive_pairs >= 2 → 1）して早期検出を強化。
        # 危険局面でのheight_multiplierを強化し、HIGH_TOWER評価を3倍にして即時併合を最優先。
        # v186: 共通変数 dangerous_situation を使用
        if dangerous_situation:
            height_multiplier = 5.0  # 危険局面はHEIGHT_CONTROLを強制的に抑制し、即時併合を最優先

        height_penalty = landing_y * height_multiplier * height_mult

        # v187: 危険局面でのHIGH_TOWER評価を4倍に強化して盤面整理優先をさらに抑制
        # ワーストゲーム(score0672)の終盤8ターン分析で、危険局面でHIGH_TOWER_DANGER_NO_MERGE_PENALTY_NEAR_PAIRS_OPPORTUNITYが
        # 6ターン連続で選択され、即時併合機会を完全に逃している失敗パターンを特定。
        # 危険局面でlanding_y > 0.0の場合、height_penaltyを4倍にしてHIGH_TOWER選択をさらに抑制し、即時併合を最優先。
        # v187: 共通変数 dangerous_situation を使用
        if dangerous_situation and landing_y > 0.0:
            height_penalty *= 4.0
            reasons.append("HIGH_TOWER")
        elif phase == "HIGH" and landing_y > 0.5:
            height_penalty *= 2.0
            reasons.append("HIGH_TOWER")
        elif phase == "MEDIUM" and landing_y > 0.5:
            height_penalty *= 1.5
            reasons.append("MEDIUM_TOWER")
        elif landing_y > 0.0:
            reasons.append("HIGH_LAYER")

        score -= height_penalty

        # 危険局面でmerge_grade=="NO"の場合、ペナルティを追加し、即時併合を優先
        # v185: 危険局面判定緩和（min_reactive_pairs=1）に合わせてペナルティ条件を更新
        # ワーストゲーム(score0565)の終盤8ターン分析で、max_y>=1.97かつreactive_pairs=2-3あるにもかかわらず
        # merge_grade=="NO"の候補が選択され、即時併合機会を完全に逃している失敗パターンを特定。
        # 危険局面でmerge_grade=="NO"の場合、ペナルティを追加し、即時併合を優先することで、スコア安定性を向上させる。
        # v186: 共通変数 dangerous_situation を使用
        if dangerous_situation and merge_grade == "NO":
            score -= 1000.0  # 危険局面で即時併合がない場合、ペナルティを追加
            reasons.append("DANGER_NO_MERGE_PENALTY")

        # ----- evaluation axis 2.5: near_pairs bonus in dangerous situations -----
        # 危険局面でnear_pairsを活用する配置を優先（即時併合候補がある場合も適用）
        # v185: 危険局面判定緩和（min_reactive_pairs=1）に合わせてnear_pairsボーナス条件を更新
        # v187: 危険局面near_pairsボーナスを大幅に強化して盤面圧縮を促進
        # ワーストゲーム(score0672)の終盤8ターン分析で、max_y=2.03-3.11かつreactive_pairs=6-7あるにもかわらず
        # HIGH_TOWER_DANGER_NO_MERGE_PENALTY_NEAR_PAIRS_OPPORTUNITYが6ターン連続で選択され、即時併合機会を完全に逃している失敗パターンを特定。
        # v186のnear_pairsボーナスが不十分で、危険局面での即時併合機会の取りこぼしが継続していたため、near_pairsボーナスを大幅に強化。
        # near_pairs_count >= 3: 800.0→1200.0、>= 2: 400.0→800.0、>= 1: 200.0（新規追加）に強化し、危険局面での盤面圧縮を促進。
        # v188: rollback failure mode (p25=-249.8) の解消 - 危険局面で即時併合候補がある場合もnear_pairsボーナスを適用
        # ワーストゲーム(score0973)の終盤8ターン分析で、max_y>=2.58かつreactive_pairs=6-7あるにもかわらず
        # 即時併合候補にnear_pairsボーナスが適用されず、near_pairsを増やす配置が評価されず、将来の即時併合機会の可能性が最大化されていない失敗パターンを特定。
        # 危険局面で即時併合候補がある場合もnear_pairsボーナスを適用し、near_pairsを増やす配置を優先評価することで、盤面圧縮を促進し、将来の即時併合機会を最大化。
        # 即時併合候補がない場合は、v187のnear_pairsボーナス（1200.0/800.0/400.0）を維持。
        # 即時併合候補がある場合は、near_pairsボーナス（900.0/600.0/300.0）を適用し、即時併合と盤面圧縮の両立を図る。
        # v187: 共通変数 dangerous_situation を使用
        if dangerous_situation:
            near_pairs = reactor.get("near_pairs", [])
            if isinstance(near_pairs, list):
                near_pairs_count = len(near_pairs)
                # near_pairsが多いほど将来のreactive_pairsへの昇格可能性が高い
                # reactive_pairsがある状況で、near_pairsを増やすことでさらに盤面圧縮を促進
                
                # 危険局面では即時併合候補がある場合もnear_pairsボーナスを適用
                if merge_grade == "NO":
                    # 即時併合がない場合：v187の強化ボーナスを維持
                    if near_pairs_count >= 3:
                        score += 1200.0  # v187: near_pairs活用ボーナスを強化（800.0→1200.0）
                        reasons.append("NEAR_PAIRS_OPPORTUNITY")
                    elif near_pairs_count >= 2:
                        score += 800.0  # v187: near_pairs活用ボーナスを強化（400.0→800.0）
                        reasons.append("NEAR_PAIRS_OPPORTUNITY")
                    elif near_pairs_count >= 1:
                        score += 400.0  # v187: near_pairs活用ボーナスを新規追加（near_pairs >= 1でもボーナス）
                        reasons.append("NEAR_PAIRS_OPPORTUNITY")
                else:
                    # 即時併合がある場合：near_pairsを増やす配置を優先（v188: 新規追加）
                    # 即時併合を取りつつ、将来の即時併合機会を最大化する配置を評価
                    if near_pairs_count >= 3:
                        score += 900.0  # v188: 即時併合候補でもnear_pairs活用ボーナスを適用
                        reasons.append("NEAR_PAIRS_OPPORTUNITY")
                    elif near_pairs_count >= 2:
                        score += 600.0  # v188: 即時併合候補でもnear_pairs活用ボーナスを適用
                        reasons.append("NEAR_PAIRS_OPPORTUNITY")
                    elif near_pairs_count >= 1:
                        score += 300.0  # v188: 即時併合候補でもnear_pairs活用ボーナスを適用
                        reasons.append("NEAR_PAIRS_OPPORTUNITY")

        # ----- evaluation axis 3: drift penalty -----
        drift_penalty = (abs(drift_x) + drift_unc) * 30.0
        score -= drift_penalty

        # ----- evaluation axis 4: left-right balance correction (v162: enhanced) -----
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

        # ----- evaluation axis 5: nextNext centering -----
        if next_next_type == next_type:
            center_bonus = max(0, 1.0 - abs(x) / 2.0) * 50.0
            score += center_bonus
            reasons.append("NEXT_SAME")

        # ----- evaluation axis 6: chain merge bonus (v170: v155 parameters & dynamic adjustment) -----
        if merge_grade in ["DIRECT", "NEAR"] and result.get("merges"):
            merges = result["merges"]
            if merges:
                # get best merge target (closest distance)
                best_merge = min(merges, key=lambda m: m.get("dist", float("inf")))
                target_x = best_merge.get("x", 0)
                target_y = best_merge.get("y", 0)

                # v170: v155成功パラメータ復帰 & v157/v159動的調整復帰
                chain_distance_max = 5.0 + landing_y * 0.6
                chain_bonus_multiplier = 450.0 + landing_y * 150.0

                # collect all merged_type pieces within chain_distance_max of merge target
                nearby_pieces = []
                for p in pieces:
                    if p.get("type") == merged_type:
                        dist = ((p["x"] - target_x) ** 2 + (p["y"] - target_y) ** 2) ** 0.5
                        if dist < chain_distance_max:
                            nearby_pieces.append((dist, p))

                # sort by distance (closest first)
                nearby_pieces.sort(key=lambda piece: piece[0])

                # v170: v155距離加重ボーナス復帰 - 3つの最も近いピースに対して、距離に応じて減衰するボーナスを適用
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

        # ----- evaluation axis 7: board density bonus (v171: NEW) -----
        # Prefer placement on less-dense side of board to improve height gain capability
        # This addresses problem where DEFAULT_PLACEMENT (x=0.0) is too frequent but provides low value
        # Low-score games often place pieces in center early, which reduces height gain capability in mid/late game
        # v186: 危険局面で即時併合候補がない場合、BOARD_DENSITY評価を無効化し、near_pairsボーナスを優先
        # 危険局面では盤面整理よりもnear_pairs活用を優先し、即時併合機会の取りこぼしを削減
        # 安全な局面のみでBOARD_DENSITY評価を適用
        if (not reasons or merge_grade == "NO") and not dangerous_situation:
            # 危険局面ではBOARD_DENSITY評価を無効化し、near_pairsボーナスを優先
            # 安全な局面のみでBOARD_DENSITY評価を適用
            # Only apply when no strong merge reason exists and not dangerous (avoid overriding merge opportunities in dangerous situations)
            if x < 0:
                # Placing on left side: bonus if right side is more dense
                density_bonus = (right_density - left_density) * 50.0
            else:
                # Placing on right side: bonus if left side is more dense
                density_bonus = (left_density - right_density) * 50.0

            # Apply bonus (positive means placing on less-dense side)
            if density_bonus > 10.0:  # Only add reason if density difference is significant
                score += density_bonus
                reasons.append("BOARD_DENSITY")

        # ----- update best candidate -----
        if score > best_score:
            best_score = score
            best_x = x
            # v169: HEIGHT_CONTROLフォールバック削除を維持
            best_reason = "_".join(reasons) if reasons else "DEFAULT_PLACEMENT"

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
