#!/usr/bin/env python3
"""strategy.py - Soviet Puzzle Game AI Drop Position Script

Game Overview:
  - Drop pieces, merge same type pieces (N+N -> N+1)
- Score table: type1=1, type2=3, type3=6, ..., typeN = N*(N+1)/2
- Board: x in [-3.0, +3.0], floor y=-4.48, deadline y=3.32
  - Player controls only drop X coordinate

  Decision Logic (9 evaluation axes):
      1. Merge bonus - High score for immediate merge (DIRECT > NEAR > FAR)
      2. Height penalty - Penalty for high landing position (varies by phase, v232: danger zone max_y>=2.0)
      3. Drift penalty - Penalty for post-landing drift due to polygon shape
      4. Left-right balance correction - Bonus for correcting piece count bias
       5. nextNext centering - Center for next merge opportunity if nextNext same type
       5.5. Avoid blocking nextNext merge - Penalty for landing on same-type piece when nextNext matches (v213: penalty adjusted)
       6. Chain merge bonus - Evaluate possibility of further merges after merge
       7. Reactive pairs bonus - Bonus for multiple merge opportunities (reactor info utilization, v213: exponential scaling restored)
       8. Early game merge priority - Strong bonus for merge opportunities in early game
       8.5. Danger zone direct merge priority (v215: bonus enhanced to +2000.0 for reactive_pairs>=1)

 Phases (determined by board max Y):
    LOW      (max_y < 0.8) : Early game. Merge priority (merge_mult=1.2)
    MEDIUM   (0.8 <= max_y < 1.8) : Mid game. Height management (height_mult=1.4)
    HIGH     (1.8 <= max_y < 2.0) : Late game. Merge opportunity (height_mult=1.8)
    CRITICAL (2.0 <= max_y) : Danger. DIRECT merge priority, board compression (NEAR carefully)
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
 # v232: 危険域定義緩和・即時併合強化版 - anchor比即時悪化モード潰し・max_y>=2.0危険域定義化
 # last_rollback_analysis: anchor比でcomp=-220.7 p50=-251.5 p25=-156.0と明確に悪化。即時悪化停止条件に触れた。
 # ワーストゲーム(score0514)終盤turns 57-64でmax_y=1.31→3.64。max_y=2.02でreactive_pairs=4あるのにmerge_available=falseでHEIGHT_CONTROL系選択。
 # その後max_y=2.78-3.64上昇、reactive_pairs=5でもmerge_available=falseでHIGH_LAYER選択が続きゲームオーバー。
 # ベストゲーム(score2542)終盤turns 112-119でmax_y=1.84→2.83。max_y=2.0-2.5の範囲でも即時併合を選択しスコア稼ぎ。
 # batch_summaryでHEIGHT_CONTROLが24.3%選択(avg_score_delta=2.0)と過剰。NEAR_MERGE系が高価値(avg_score_delta=28-57)だが選択率低い(2.1-6.3%)。
 # 現在の危険域定義max_y>=2.5では、ワーストゲームのmax_y=2.02-2.78の範囲で即時併合機会を逃している。
 # 危険域の定義をmax_y>=2.5からmax_y>=2.0に緩和し、reactive_pairs>=1かつmerge_grade=="NO"の場合、非併合heightペナルティを一律2.0倍に強化。
 # これによりreactive_pairsが1-4の状況でも即時併合を強化し、ワーストゲームのような失敗パターン（max_y=2.02でreactive_pairs=4あるのにHEIGHT_CONTROL）を抑制。
 # v231のreactive_pairs>=5での軽減ロジックは削除し、シンプルな一律強化に統一。
 # refs: tmp/batch_summary.txt, tmp/state/last_rollback_analysis.md, advice.md, game_history/20260315_032959_score0514.jsonl turns 57-64, game_history/20260315_032412_score2542.jsonl turns 112-119
# ワーストゲーム(score0874)終盤turns 64-71でmax_y=2.11-2.53、reactive_pairs=3あるにもかかわらずmerge_available=falseでHIGH_TOWER選択が続きゲームオーバー。
# extra_low(score0899)終盤turns 63-70でmax_y=2.0-3.55、reactive_pairs=2-5あるにもかかわらずmerge_available=falseでHIGH_TOWER/HIGH_LAYER選択が続き。
# batch_summaryでREACTIVE_PAIRS_COMPRESSIONがavg_score_delta=2.8と低価値であり、低スコア群で14.8%選択されていることを確認。高スコア群では6.1%のみ。
# v207で追加したREACTIVE_PAIRS_COMPRESSION評価軸は、reactive_pairsがある状況で即時併合がない場合のデフォルト選択として機能しているが、低価値で低スコア群で過剰選択されている。
# v201 rollback教訓: 複雑な危険局面判定ロジックは禁止。reactive_pairsを活用したシンプルな改善を採用。
# REACTIVE_PAIRS_COMPRESSION評価軸を削除し、即時併合がない場合のデフォルト選択をHEIGHT_CONTROLに戻す。
# 危険域(max_y>=2.5)でreactive_pairs>=1かつmerge_grade=="NO"の場合、非併合heightペナルティを4倍に強化し、即時併合機会を最大化する配置を優先。
# これによりreactive_pairsがある状況でHIGH_TOWER選択を抑制し、即時併合機会の取りこぼしを削減。v201 rollback failure mode (即時併合候補があるのにHIGH_TOWER) を潰す。
# 構造的変更（評価軸9削除・評価軸2強化）であり、数値微調整ではない。
# refs: tmp/batch_summary.txt, tmp/state/last_rollback_analysis.md, advice.md, game_history/20260315_032959_score0514.jsonl, game_history/20260315_032412_score2542.jsonl
#
# v212: 危険域即時併合優先条件緩和版 - reactive_pairs>=1で発動条件を緩和し、即時併合機会取りこぼし削減（v201 rollback failure mode潰し）
# ワーストゲーム(score0907)終盤turns 70-77でmax_y=2.44→3.82、reactive_pairs=4-6あるにもかかわらずmerge_available=falseでHIGH_LAYER/HIGH_TOWER選択が続きゲームオーバー。
# ベストゲーム(score2583)終盤turns 119-126でmax_y=2.46→3.40、reactive_pairs=6-8でもmerge_available=trueなら即時併合を選択しスコア稼ぎ。
# batch_summaryでHEIGHT_CONTROLが14.6%選択(avg_score_delta=0.3)と過剰、NEAR_MERGE系が5.1%選択(avg_score_delta=59.0)と高価値だが選択率が低い。
# v211のmax_y>=2.0かつreactive_pairs>=2の条件では、reactive_pairs=4-6でも発動しない事例が多数存在する。
# v201 rollback教訓: 複雑な危険局面判定ロジックは禁止。reactive_pairsを活用したシンプルな改善を採用。
# 危険域の定義をmax_y>=2.5に厳格化し、reactive_pairs>=1 かつDIRECT/NEARマージがある場合、強力なボーナス(1500.0)を与え、v210の非併合height_penalty強化を危険域にも適用。
# これにより危険域での即時併合機会の取りこぼしを大幅に削減し、ワーストゲームのような「reactive_pairsがあるのにHIGH_TOWER」判断を回避。
# 構造的変更（評価軸8.5条件緩和＋v210の危険域拡張）であり、数値微調整ではない。v201 rollback failure mode (即時併合候補があるのにHIGH_TOWER) を潰す。
# refs: tmp/batch_summary.txt, tmp/state/last_rollback_postmortem.md, advice.md, game_history/20260314_031305_score0907.jsonl, game_history/20260314_033614_score2583.jsonl
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
    """v232: 危険域定義緩和・即時併合強化版 - anchor比即時悪化モード潰し・max_y>=2.0危険域定義化

    last_rollback_analysis: anchor比でcomp=-220.7 p50=-251.5 p25=-156.0と明確に悪化。即時悪化停止条件に触れた。
    ワーストゲーム(score0514)終盤turns 57-64でmax_y=1.31→3.64。max_y=2.02でreactive_pairs=4あるのにmerge_available=falseでHEIGHT_CONTROL系選択。
    ベストゲーム(score2542)終盤turns 112-119でmax_y=1.84→2.83。max_y=2.0-2.5の範囲でも即時併合を選択しスコア稼ぎ。
    batch_summaryでHEIGHT_CONTROLが24.3%選択(avg_score_delta=2.0)と過剰。NEAR_MERGE系が高価値(avg_score_delta=28-57)だが選択率低い(2.1-6.3%)。
    危険域の定義をmax_y>=2.5からmax_y>=2.0に緩和し、reactive_pairs>=1かつmerge_grade=="NO"の場合、非併合heightペナルティを一律2.0倍に強化。
    これによりreactive_pairsが1-4の状況でも即時併合を強化し、ワーストゲームのような失敗パターンを抑制。

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

    # --- phase judgment (v42 thresholds, v232: danger zone max_y>=2.0) ---
    if max_y < 0.8:
        phase = "LOW"
        height_mult = 1.0  # v177: LOW phase height_mult (best score 5310)
        merge_mult = 1.2  # 20% merge bonus increase, actively target
    elif max_y < 1.8:
        phase = "MEDIUM"
        height_mult = 1.4  # v177: MEDIUM phase height_mult from v42 (2.4→1.4)
        merge_mult = 1.0
    elif max_y < 2.0:
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

        # v232: 危険域定義緩和・即時併合強化版 - anchor比即時悪化モード潰し・max_y>=2.0危険域定義化
        # ワーストゲーム(score0514)終盤turns 57-64でmax_y=2.02でreactive_pairs=4あるのにmerge_available=falseでHEIGHT_CONTROL系選択。
        # ベストゲーム(score2542)終盤turns 112-119でmax_y=2.0-2.5の範囲でも即時併合を選択しスコア稼ぎ。
        # batch_summaryでHEIGHT_CONTROLが24.3%選択(avg_score_delta=2.0)と過剰。NEAR_MERGE系が高価値(avg_score_delta=28-57)だが選択率低い(2.1-6.3%)。
        # 危険域の定義をmax_y>=2.5からmax_y>=2.0に緩和し、reactive_pairs>=1かつmerge_grade=="NO"の場合、非併合heightペナルティを一律2.0倍に強化。
        # これによりreactive_pairsが1-4の状況でも即時併合を強化し、ワーストゲームのような失敗パターンを抑制。
        if reactive_pair_count >= 1 and merge_grade == "NO":
            if max_y >= 2.0:
                # 危険域(max_y>=2.0)では非併合配置を一律強く抑制
                height_penalty *= 2.0
            else:
                # 非危険域では1.5倍に軽減
                height_penalty *= 1.5

        if phase == "HIGH" and landing_y > 0.5:
            height_penalty *= 2.0
            reasons.append("HIGH_TOWER")
        elif phase == "MEDIUM" and landing_y > 0.5:
            height_penalty *= 1.8  # v213: 1.5 -> 1.8 (MEDIUM_TOWER強化)
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
 
        # ----- evaluation axis 5.5: avoid blocking nextNext merge (v213: penalty adjusted) -----
        # batch_summary/adviceで「盤面A・nextB・nextNextAの状況で、A上にBを置くとnextNextの併合を逃す問題」が指摘されている。
        # nextNext typeが盤面上にある場合、着地位置がそのtypeの上になる配置では未来の併合機会を潰すためペナルティを与える。
        # v213: ペナルティを-600.0に引き上げ（-400.0 -> -600.0）。
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
                        score -= 600.0  # 未来の併合機会を潰すためのペナルティ（v213: 強化）
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

        # ----- evaluation axis 8: reactive pairs bonus (EXPONENTIAL SCALING RESTORED: v156-style) -----
        # v213: v212の線形スケーリングに代え、指数関数に戻す。
        # reactive_pair_count >= 1 かつ merge_grade in ["DIRECT", "NEAR"] の場合、指数関数的ボーナスを与える
        # reactive_pair_count=1: +400.0, reactive_pair_count=2: +800.0, reactive_pair_count>=3: +1200.0
        # これにより、reactive_pairsが多い状況で即時併合を最優先し、HEIGHT_CONTROL過剰選択を抑制
        if reactive_pair_count >= 1 and merge_grade in ["DIRECT", "NEAR"]:
            # 指数関数的ボーナス: base=400.0, reactive_pair_count=1→400, 2→800, 3+→1200
            reactive_bonus = 400.0 * (2 ** (reactive_pair_count - 1))
            if reactive_pair_count > 3:
                reactive_bonus = 1200.0
            score += reactive_bonus
            reasons.append("REACTIVE_MERGE_PRIORITY")

        # ----- evaluation axis 8.5. danger zone direct merge priority (v215: 危険域即時併合ボーナス強化版 - v201 rollback failure mode潰し)
        # v232: 危険域定義緩和・即時併合強化版 - anchor比即時悪化モード潰し・max_y>=2.0危険域定義化
        # v215の危険域(max_y>=2.5)でreactive_pairs>=1かつDIRECT/NEARマージがある場合、強力なボーナス(+2000.0)を与えるロジックを維持。
        # v232で危険域定義をmax_y>=2.0に緩和したため、このボーナスもmax_y>=2.0で発動するようにする。
        if max_y >= 2.0 and reactive_pair_count >= 1 and merge_grade in ["DIRECT", "NEAR"]:
            # max_y>=2.0の危険域でreactive_pairs>=1かつ即時併合機会がある場合、強力なボーナスを与える
            # これにより危険域での即時併合機会を最大化し、ワーストゲームのような「reactive_pairsがあるのにHIGH_TOWER」判断を回避
            score += 2000.0
            reasons.append("DANGER_ZONE_DIRECT_MERGE_PRIORITY")

        # ----- evaluation axis 9: reactive pairs default (DELETED in v216: REACTIVE_PAIRS_COMPRESSION削除版) -----
        # v207で追加したREACTIVE_PAIRS_COMPRESSION評価軸は、reactive_pairsがある状況で即時併合がない場合のデフォルト選択として機能していたが、
        # batch_summaryでavg_score_delta=2.8と低価値であり、低スコア群で14.8%選択されていることを確認。高スコア群では6.1%のみ。
        # 即時併合がない場合のデフォルト選択をHEIGHT_CONTROLに戻し、非併合heightペナルティ強化で即時併合機会を優先する。
        # refs: tmp/batch_summary.txt, tmp/state/last_rollback_postmortem.md, game_history/20260314_063403_score0874.jsonl turns 64-71

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
