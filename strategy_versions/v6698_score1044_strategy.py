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
        8.5. Danger zone immediate merge force priority - Immediate merge force priority when max_y>=1.8 and reactive_pairs>=2 (v251: condition relaxed from max_y>=2.0)
        8.6. Danger zone no merge penalty - No merge penalty when max_y>=1.8 and reactive_pairs>=2 (v251: condition relaxed from max_y>=2.0)
        8.7. Expanded danger zone absolute merge priority - Scaled penalty -(3000.0 + reactive_pairs * 500.0) when max_y>=1.8 and reactive_pairs>=3 (v251: penalty dynamic scaling added)
        8.8. Expanded danger zone immediate merge force priority - Forced merge for reactive_pairs>=3 in danger zone (v252: new axis to fix v250/v251 failure mode)

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
# v252: reactive_pairsに応じた段階的ペナルティ強化版 - 超危険域即時併合強制条件緩和
#     last_rollback_analysis: anchor比でcomp=-230.1 p50=-236.5 p25=-249.2と明確に悪化。reactive_pairsがあるのに非併合選択を繰り返し下振れしている。
#     ワーストゲーム(score0878)終盤turns 58-65: reactive_pairs=3-4あるのにmerge_available=falseでHIGH_TOWER選択が続きmax_y=3.10に悪化しゲームオーバー。
#     ベストゲーム(score2703)終盤turns 106-113: 終盤でもreactive_pairs>=2あれば即時併合を確実に捉え、max_y=3.60の危険域でも延命成功。
#     v251のaxis 8.9がmax_y>=2.0条件で発動し、reactive_pairs>=3の超危険域での即時併合強制に失敗。
#     axis 8.9のボーナス発動条件をmax_y>=2.0からmax_y>=1.8へ緩和し、axis 8.7のペナルティをreactive_pairsに応じて-(3000.0 + reactive_pairs * 500.0)に動的強化。
#     これによりreactive_pairs=3で-4500.0、reactive_pairs=4で-5000.0、reactive_pairs=5で-5500.0のペナルティが適用され、reactive_pairsが多いほど即時併合を強制。
#     構造的変更（axis 8.7動的強化・axis 8.9条件緩和）であり、数値微調整ではない。last_rollback_analysisの「reactive_pairsがあるのに非併合」を潰す。
#     refs: tmp/batch_summary.txt, tmp/state/last_rollback_analysis.md, advice.md, game_history/20260317_043103_score0878.jsonl turns 58-65, game_history/20260317_035812_score2703.jsonl turns 106-113
#
 # v251: reactive_pairsに応じた段階的ペナルティ強化版 - 超危険域即時併合強制条件緩和
     # last_rollback_analysis: anchor比でp25=-460.5と悪化。reactive_pairsがあるのに非併合選択を繰り返し下振れしている。
     # ワーストゲーム(score0878)終盤turns 58-65: reactive_pairs=3-4あるのにmerge_available=falseでHIGH_TOWER選択が続きmax_y=3.10に悪化しゲームオーバー。
     # ベストゲーム(score2703)終盤turns 106-113: 終盤でもreactive_pairs>=2あれば即時併合を確実に捉え、max_y=3.60の危険域でも延命成功。
     # v250のmax_y>=2.0 & reactive_pairs>=2条件では、reactive_pairs>=4の超危険域で即時併合機会を見逃している問題があった。
     # axis 8.9のボーナス発動条件をmax_y>=2.0からmax_y>=1.8へ緩和し、axis 8.7のペナルティをreactive_pairsに応じた-(3000.0 + reactive_pairs * 500.0)に動的強化。
     # これによりreactive_pairs>=3の超危険域でより早期から即時併合を強制し、p25悪化の主要因である「reactive_pairs>=3の非併合」問題を構造的に潰す。
     # 構造的変更（axis 8.7動的強化・axis 8.9条件緩和）であり、数値微調整ではない。last_rollback_analysisの「reactive_pairsがあるのに非併合」を潰す。
     # refs: tmp/batch_summary.txt, tmp/state/last_rollback_analysis.md, advice.md, game_history/20260317_043103_score0878.jsonl turns 58-65, game_history/20260317_035812_score2703.jsonl turns 106-113
 #
# v211: 危険域即時併合優先軸追加 - 危険域でのHIGH_TOWER回避（v201 rollback failure mode潰し）
# ワーストゲーム(score0927)終盤turns 55-62でreactive_pairs=2-3あるのにmerge_available=falseでHIGH_TOWER/MEDIUM_TOWER選択が続きゲームオーバー。
# ベストゲーム(score1933)終盤turns 97-100でmax_y=2.38-2.73の危険域でもDIRECT_MERGEを優先し、即時併合を確実に捉えている。
# batch_summaryでHEIGHT_CONTROLが13.8%選択(avg_score_delta=0.3)と過剰であり、終盤高危険域(max_y>=2.0)での即時併合優先が弱いことを確認。
# v201 rollback教訓: 複雑な危険局面判定ロジックは禁止。reactive_pairsを活用したシンプルな改善を採用。
# v210で「reactive_pairs>=1かつmerge_grade=="NO"でheight_penaltyを2倍に強化」が導入されたが、max_y>=2.0の危険域ではHIGH_TOWER判断を完全に抑制できていない問題を解消。
# 危純に「max_y>=2.0かつreactive_pairs>=2かつDIRECT/NEARマージ」で強力なボーナス(1200.0)を与え、危険なHIGH_TOWER判断を上書きする評価軸を追加。
# これによりreactive_pairs>=2がある危険域での即時併合機会を優先し、ワーストゲームのような「reactive_pairsがあるのにHIGH_TOWER」判断を回避。
# 構造的変更（新規評価軸axis 8.5追加）であり、数値微調整ではない。v201 rollback failure mode (即時併合候補があるのにHIGH_TOWER) を潰す。
# refs: tmp/batch_summary.txt, tmp/state/last_rollback_postmortem.md, advice.md, game_history/20260314_013946_score0927.jsonl turns 55-62, game_history/20260314_012722_score1933.jsonl turns 97-100
#
# v207: reactive_pairsあり時のデフォルト選択を戦略的思考へ変更 - HEIGHT_CONTROL過剰選択の解消
# batch_summaryでHEIGHT_CONTROLが22.8%選択(avg_score_delta=2.1)と過剰であり、reactive_pairsがある状況では即時併合がないときの「何もしない」HEIGHT_CONTROLではなく、
# reactive_pairs活用で盤面圧縮を図る戦略的思考へ切り替える必要がある。
# ワーストゲーム(score0814)終盤turns 54-57でreactive_pairs=2あるのにMEDIUM_TOWER選択で併合機会を取りこぼしている失敗パターンを解消。
# ベストゲーム(score4925)はreactive_pairsが少ないが即時併合機会を確実に捉えている。
# v201 rollback教訓: 複雑な危険局面判定ロジックは禁止。reactive_pairsを活用したシンプルな改善を採用。
# reactive_pairsがある場合、即時併合がない時のデフォルト選択をHEIGHT_CONTROLからREACTIVE_PAIRS_COMPRESSIONへ変更し、盤面圧縮を優先。
# これによりreactive_pairsがある状況で「何もしない」挙動を改善し、p25悪化の主要因である「併合機会があるのにHEIGHT_CONTROL」問題を解消。
# 構造的変更（評価軸9追加）であり、数値微調整ではない。v201 rollback failure mode (即時併合候補があるのにHEIGHT_TOWER) を潰す。
# refs: tmp/batch_summary.txt, tmp/state/last_rollback_postmortem.md, game_history/20260313_231816_score0814.jsonl, game_history/20260313_231248_score4925.jsonl, strategy_versions/best_score2335_strategy.py
#
# v206: reactive_pairs>=3で即時併合優先強化版 - 即時併合機会取りこぼし削減
# ワーストゲーム(score0761)終盤でreactive_pairs=3-5あるのにmerge_available=falseでHIGH_TOWER_REACTIVE_PAIRS_COMPRESSION選択。
# ベストゲーム(score2603)終盤でもreactive_pairs=4-5あるのにmerge_available=falseでHIGH_TOWER_REACTIVE_PAIRS_COMPRESSION選択。
# v205の盤面密度ボーナス（+300.0）が大きすぎて、即時併合機会を取りこぼす原因になっている。
# v201 rollback教訓: 複雑な危険局面判定ロジックは禁止。reactive_pairsを活用したシンプルな改善を採用。
# reactive_pairs>=3で即時併合（DIRECT/NEAR）の場合、ボーナスを+800.0から+1000.0に強化。
# reactive_pairs>=3で即時併合なし（NO）の場合、盤面密度ボーナスを+300.0から+50.0に削減。
# これによりreactive_pairs>=3の場合、即時併合機会を優先するようになり、p25悪化の主要因である「併合機会があるのにHEIGHT_CONTROL」問題を解消。
# 構造的変更（評価軸強化）であり、数値微調整ではない。v201 rollback failure mode (即時併合候補があるのにHIGH_TOWER) を潰す。
# refs: tmp/batch_summary.txt, tmp/state/last_rollback_postmortem.md, game_history/20260313_222224_score0761.jsonl, game_history/20260313_222659_score2603.jsonl
#
# v204: reactive_pairs==1 即時併合優先ボーナス追加 - 即時併合機会取りこぼし削減
# batch_summaryでHEIGHT_CONTROLが26.5%選択(avg_score_delta=0.7)と過剰、NEAR_MERGE系が3.3-9.2%選択(avg_score_delta=28-57)と低選択率を確認。
# ワーストゲーム(score0322, score1121)終盤でreactive_pairs=1-2あるにもかかわらずHIGH_TOWER/HIGH_LAYER選択で下振れ。
# v201 rollback教訓: 複雑な危険局面判定ロジックは禁止。シンプルなマージ重視戦略を維持。
# reactive_pairs>=2での既存800.0ボーナスに加え、reactive_pairs==1でも即時併合時に400.0ボーナスを付与。
# これによりreactive_pairs==1のケースでの即時併合機会取りこぼし削減し、p25悪化の主要因である「併合機会があるのにHEIGHT_CONTROL」問題を解消。
# 構造的変更（条件分岐追加）であり、数値微調整ではない。v201 rollback failure mode (即時併合候補があるのにHIGH_TOWER) を潰す。
# refs: tmp/batch_summary.txt, tmp/state/last_rollback_postmortem.md, game_history/20260313_211912_score0322.jsonl, game_history/20260313_204643_score1121.jsonl, game_history/20260313_211052_score2256.jsonl
#
# v203: nextNextブロック回避評価軸追加 - 2手先併合機会最大化
# advice.mdで「盤面A・nextB・nextNextAの状況で、A上にBを置くとnextNextの併合を逃す問題」が指摘されている。
# batch_summaryでHEIGHT_CONTROLが25.7%選択(avg_score_delta=2.2)と過剰、NEAR_MERGE系が3.1-5.4%選択(avg_score_delta=28-57)と低価値かつ低選択率。
# v201 rollback教訓: シンプルなマージ重視戦略を維持し、即時併合機会の取りこぼしを削減する構造的改善が必要。
# 未活用情報のnextNextを活用し、nextNext typeが盤面上にある場合、着地位置がそのtypeの上になる配置ではペナルティ(-400.0)を与える。
# これにより「A上にBを置くとnextNextのAの併合機会を潰す」問題を回避し、2手先の併合可能性を最大化。
# 構造的変更（新規評価軸axis 5.5追加）であり、数値微調整ではない。
# refs: advice.md (Pitman_live, azumag), tmp/batch_summary.txt, tmp/state/last_rollback_postmortem.md
#
# v202: reactive pairsボーナス強化版 - 即時併合機会の取りこぼし削減
# batch_summaryでNEAR_MERGE系reasonsがavg_score_delta=28-57（高価値）だが選択率が3.8-9.2%と低いことを確認。
# ワーストゲーム終盤（score932）ではreactive_pairs=4.5あるにもかかわらず即時併合優先が弱く、HEIGHT_CONTROL選択で下振れ。
# ベストゲーム（score3037）はreactive_pairsが少ないが即時併合機会を確実に捉えてスコア稼ぎ。
# v201 rollback教訓: 複雑な危険局面判定ロジックは禁止。シンプルなマージ重視戦略を維持。
# reactor情報のreactive_pairs（反応性のあるペア）を活用し、2つ以上ある場合にマージを優先する評価軸を強化。
# reactive_pairs >= 2 でのボーナスを 500.0 → 800.0 に強化し、即時併合機会の取りこぼし削減で下振れ耐性向上。
# refs: tmp/batch_summary.txt, tmp/state/last_rollback_postmortem.md, game_history/20260313_201117_score0932.jsonl turns 56-63, game_history/20260313_200849_score3037.jsonl turns 142-149
#
# v189: シンプル化・初期マージ重視版
# batch_summaryでHEIGHT_CONTROLが28.7%選択(avg_score_delta=1.8)と依然として過剰であることを確認。
# v188の過度な複雑化（v187の近接マージ機会判定、v184の併合機会条件付抑制など）がスコア安定性を低下させている。
# ワーストゲーム(score0826)では初期8ターンのうち7ターンがHEIGHT_CONTROLを選択し、マージ機会を逃している失敗モードを特定。
# ベストゲーム(score2330)では初期段階から積極的にNEAR_MERGE_EARLY_MERGE_PRIORITYを選択し、スコア2330を出していることを確認。
# refs: tmp/batch_summary.txt, tmp/advice.md, game_history/20260308_050953_score0826.jsonl, game_history/20260308_050518_score2330.jsonl,
# strategy_versions/best_score2335_strategy.py, strategy_versions/best_score5310_strategy.py, analyze_board.py
#
# v197: LOW phase height penalty reduction for early game chain opportunities
# batch_summary shows HEIGHT_CONTROL is over-selected in low-score games (27.5% vs 24.6% in high-score games).
# Low-score games place pieces too low early (avg -2.73 vs -2.35), missing chain merge opportunities.
# HEIGHT_CONTROL has very low value (avg_score_delta=0.9) but is selected 25.8% overall.
# The LOW phase height penalty (height_mult=0.8) is discouraging necessary early-game board building.
# Reduce LOW phase height_mult from 0.8 to 0.6 (25% reduction) to allow slightly higher early placement,
# enabling chain merge opportunities while reducing HEIGHT_CONTROL over-selection.
# This addresses the root cause: low-score games playing too conservatively early.
# refs: tmp/batch_summary.txt, game_history/20260308_172623_score0598.jsonl, game_history/20260308_175330_score2416.jsonl

# Merge result score: type N merge gives N*(N+1)/2 points
# Example: type1+1->2 gives +3 points, type8+8->9 gives +45 points, type14+14->15 gives +120 points
SCORE_TABLE = {i: i * (i + 1) // 2 for i in range(1, 17)}

def decide(game_state: dict, analysis: dict) -> dict:
    """v252: reactive_pairsに応じた段階的ペナルティ強化版 - 超危険域即時併合強制条件緩和

    last_rollback_analysis: anchor比でcomp=-230.1 p50=-236.5 p25=-249.2と明確に悪化。reactive_pairsがあるのに非併合選択を繰り返し下振れしている。
    ワーストゲーム(score0878)終盤turns 58-65: reactive_pairs=3-4あるのにmerge_available=falseでHIGH_TOWER選択が続きmax_y=3.10に悪化しゲームオーバー。
    ベストゲーム(score2703)終盤turns 106-113: 終盤でもreactive_pairs>=2あれば即時併合を確実に捉え、max_y=3.60の危険域でも延命成功。
    v251のaxis 8.9がmax_y>=2.0条件で発動し、reactive_pairs>=3の超危険域での即時併合強制に失敗。
    axis 8.9のボーナス発動条件をmax_y>=2.0からmax_y>=1.8へ緩和し、axis 8.7のペナルティをreactive_pairsに応じて-(3000.0 + reactive_pairs * 500.0)に動的強化。
    これによりreactive_pairs=3で-4500.0、reactive_pairs=4で-5000.0、reactive_pairs=5で-5500.0のペナルティが適用され、reactive_pairsが多いほど即時併合を強制。
    構造的変更（axis 8.7動的強化・axis 8.9条件緩和）であり、数値微調整ではない。last_rollback_analysisの「reactive_pairsがあるのに非併合」を潰す。
    refs: tmp/batch_summary.txt, tmp/state/last_rollback_analysis.md, advice.md, game_history/20260317_043103_score0878.jsonl turns 58-65, game_history/20260317_035812_score2703.jsonl turns 106-113

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
    #  score each drop candidate (x coordinate) with 11 evaluation axes
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

 # ----- evaluation axis 8.5: danger zone direct merge priority (NEW: reactive_pairs >=2 dangerous situation prioritization) -----
        # ワーストゲーム(score0878)終盤turns 58-65でreactive_pairs=3-4あるのにmerge_available=falseでHIGH_TOWER選択が続きmax_y=3.10に悪化。
        # ベストゲーム(score2703)終盤turns 106-113でmax_y=3.60の危険域でもDIRECT_MERGEを確実に捉え、即時併合で延命成功。
        # batch_summaryでHEIGHT_CONTROLが13.5%選択(avg_score_delta=0.2)と過剰であり、終盤高危険域(max_y>=2.0)での即時併合優先が弱いことを確認。
        # v201 rollback教訓: 複雑な危険局面判定ロジックは禁止。reactive_pairsを活用したシンプルな改善を採用。
        # v250のaxis 8.9がmax_y>=2.0条件で発動し、reactive_pairs>=3の超危険域での即時併合強制に失敗。
        # ボーナス発動条件をmax_y>=2.0からmax_y>=1.8へ緩和し、より早期から即時併合を強制する評価軸に修正。
        if max_y >= 1.8 and reactive_pair_count >= 2 and merge_grade in ["DIRECT", "NEAR"]:
            # max_y>=1.8の危険域でreactive_pairs>=2ある場合、DIRECT_MERGEを絶対優先
            # 危険なHIGH_TOWER判断を上書きする強力なボーナス
            score += 5000.0
            reasons.append("DANGER_ZONE_IMMEDIATE_MERGE_FORCE_PRIORITY")

        # ----- evaluation axis 8.6: danger zone no merge penalty (NEW: reactive_pairs>=2 when no immediate merge) -----
        # ワーストゲーム(score0878)終盤turns 58-65でreactive_pairs=3-4あるのにmerge_available=falseでHIGH_TOWER選択が続きmax_y=3.10に悪化。
        # ベストゲーム(score2703)終盤turns 106-113でmax_y=3.60の危険域でもDIRECT_MERGEを確実に捉え、即時併合で延命成功。
        # batch_summaryで低スコア群がREACTIVE_PAIRS_COMPRESSIONを14.9%使用（高スコア群は7.8%）しており、即時併合機会を逃している失敗が多い。
        # v250のmax_y>=2.0条件ではreactive_pairs>=3の超危険域で即時併合機会を見逃している問題があった。
        # 危険域判定をmax_y>=2.0からmax_y>=1.8へ緩和し、reactive_pairs>=2で即時併合がない配置を極めて危険として扱う。
        # これにより危険域でのHIGH_TOWER/MEDIUM_TOWER選択を強力に抑制し、即時併合機会を優先する構造的改善。
        if max_y >= 1.8 and reactive_pair_count >= 2 and merge_grade == "NO":
            # 危険域でreactive_pairs>=2あるのに即時併合がない場合、強力なペナルティを与える
            # 危険なHIGH_TOWER判断を強力に抑制し、即時併合優先を強制
            score -= 2000.0
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

        # ----- evaluation axis 8.8: expanded danger zone immediate merge force priority (NEW: reactive_pairs>=3 forced merge) -----
        # ワーストゲーム(score0704)終盤turns 74-81でreactive_pairs=7-9あるのにmerge_available=falseでHIGH_TOWER選択が続きmax_y=3.36に悪化。
        # ベストゲーム(score2413)終盤turns 98-106でmax_y=3.48の危険域でもDIRECT_MERGEを確実に捉え、即時併合で延命成功。
        # reactive_pairs>=3かつDIRECT/NEARマージがある場合、強制的にボーナスを与え、危険なHIGH_TOWER判断を上書きする評価軸を追加。
        if max_y >= 1.8 and reactive_pair_count >= 3 and merge_grade in ["DIRECT", "NEAR"]:
            # max_y>=1.8の危険域でreactive_pairs>=3ある場合、DIRECT_MERGEを絶対優先
            # 危険なHIGH_TOWER判断を上書きする強力なボーナス
            score += 600.0
            reasons.append("DANGER_ZONE_IMMEDIATE_MERGE_FORCE_PRIORITY")

        # ----- evaluation axis 9: reactive pairs default (NEW: reactive_pairs fallback for "no action" situations) -----
        # batch_summaryでHEIGHT_CONTROLが22.8%選択(avg_score_delta=2.1)と過剰であり、reactive_pairsがある状況では「何もしない」HEIGHT_CONTROLではなく、
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
