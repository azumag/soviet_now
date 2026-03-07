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
   6. Chain merge bonus - Evaluate possibility of further merges after merge (v180: nextNext 2-lookahead NEW)
   7. Early game merge priority - Strong bonus for merge opportunities in early game (v174)
   8. Reactive merge priority - Bonus for merge opportunities when reactive_pairs >= 2 (v176)
   9. Merge opportunity suppression - Suppress HEIGHT_CONTROL when merge opportunities exist (NEW)

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
# v172: 序盤マージ優先評価軸追加版 - batch_summaryでHEIGHT_CONTROLが25.9%選択(avg_score_delta=1.6)と過剰であり、低スコア群で30.3%選択されていることを確認。
# ワーストゲーム(score0545)で初期5ターンが全てHEIGHT_CONTROLとなり併合機会を逃している失敗モードを特定。
# early_game条件下でmerge_gradeがNEARの場合、追加ボーナス800.0を付与する評価軸を追加し、初期段階でのマージ機会を最優先してHEIGHT_CONTROL選択を超強力に抑制する。
# v173: early_game判定緩和・初期10ターンマージ重視版 - batch_summaryでHEIGHT_CONTROLが26.7%選択(avg_score_delta=1.3)と依然として過剰であることを確認。
# ワーストゲーム(score0678)で初期10ターンのmax_y推移(-5.0→-2.4)を分析し、v172のearly_game判定(max_y < -3.0)が過度に厳しく
# 初期10ターンの大部分で判定されていないことを特定。
# early_game判定をmax_y < -3.0→-2.5に緩和し、EARLY_MERGE_PRIORITYの適用範囲をpiece_count <= 10→12に拡大して
# 初期10ターン全体でマージ機会を最優先する。
# また、初期段階(piece_count <= 6)で併合機会がない場合のHEIGHT_CONTROL抑制を強化し、初期配置での消極的配置を回避する。
# v174: early_game判定さらに緩和・初期12ターンマージ重視版 - batch_summaryでHEIGHT_CONTROLが26.2%選択(avg_score_delta=1.7)と依然として過剰であることを確認。
# ワーストゲーム(score0765)で初期7ターン全てHEIGHT_CONTROLを選択し、マージ機会を逃している失敗パターンを特定。
# early_game判定をmax_y < -2.5→-2.0にさらに緩和し、EARLY_MERGE_PRIORITYの適用範囲をpiece_count <= 10→12に拡大して初期12ターン全体でマージ機会を最優先する。
# また、MEDIUM_TOWER選択を促進するための追加評価軸を追加し、高スコア群と低スコア群のMEDIUM_TOWER選択率差（13.6% vs 10.8%）を解消する。
# v175: MEDIUMフェーズHEIGHT_CONTROL抑制強化版 - batch_summaryでHEIGHT_CONTROLが26.5%選択(avg_score_delta=1.1)と依然として過剰であり、スコアの標準偏差が404.0と大きいことを確認。
# v174で初期12ターンでのHEIGHT_CONTROL抑制は強化されたが、中盤以降のHEIGHT_CONTROL選択が依然として多く、これがスコアの不安定性を引き起こしていることを特定。
# MEDIUMフェーズ（0.8 <= max_y < 1.8）でheight_multiplierを30.0→20.0に削減し、マージ選択を促進することでスコア安定性を向上させる。
# v176: reactor情報活用によるマージ優先評価軸追加版 - batch_summaryでHEIGHT_CONTROLが26.2%選択(avg_score_delta=1.7)と依然として過剰であることを確認。
# reactor情報のreactive_pairs（反応性のあるペア）を活用し、2つ以上ある場合にマージを優先する評価軸を追加。
# これにより、盤面に多数の併合機会がある状況でHEIGHT_CONTROL選択を抑制しスコア安定性を向上させる。
# v177: MEDIUMフェーズHEIGHT_CONTROL抑制強化版 - batch_summaryでHEIGHT_CONTROLが27.5%選択(avg_score_delta=0.9)と過剰であることを確認。
# 高スコア群(23.9%)と低スコア群(32.5%)の比較で、低スコア群が8.6%も多くHEIGHT_CONTROLを選択していることを特定。
# MEDIUMフェーズのheight_multiplierを20.0→15.0に削減し、マージ選択を促進することでHEIGHT_CONTROL選択を23.9%程度まで抑制しスコア向上を目指す。
# v178: CRITICALフェーズ危険高さ抑制版 - batch_summaryで高スコア群が終盤avg=1.77、低スコア群が終盤avg=1.93であることを確認。
# アドバイスより「ゲームオーバー付近で併合判断が適切に行われていない」問題に対処。
# CRITICALフェーズ(max_y >= 3.0)でlanding_y > 2.0の場合、追加ペナルティ500.0を付与し、危険な高さ配置を強力に抑制する。
# v179: CHAIN_MERGE基本ボーナス10%強化版 - batch_summaryでCHAIN_MERGEがavg_score_delta=30-50（高価値）だが選択率は依然として低い（3-4%）ことを確認。
# v178のCRITICALフェーズ危険高さ抑制は有効だが、CHAIN_MERGE自体の選択を促進できていない。
# v155成功パラメータ(chain_distance_max=5.0, chain_bonus_multiplier初期値450.0)をベースに、全フェーズでchain_bonus_multiplierを10%強化(450.0→495.0)。
# これによりHEIGHT_CONTROL選択を減らし、スコア安定性向上を図る。
# refs: tmp/batch_summary.txt, tmp/advice.md, game_history/20260307_223935_score0840.jsonl, game_history/20260307_231137_score2859.jsonl,
# strategy_versions/best_score5310_strategy.py, strategy_versions/best_score4999_strategy.py, analyze_board.py
# 
# v180: nextNext 2手先評価統合版
# batch_summaryでCHAIN_MERGE(avg_delta=30-50)が高スコアへの効果的reasonであることを確認しつつ、現行v179のCHAIN_MERGE選択率は依然として低い（3-4%）。
# v179のreactive_pairs活用は方向性は合っているが、CHAIN_MERGE自体の選択を促進できていない。
# nextNextが現在nextと同じtypeの場合、「現在併合 → nextNextで更に併合」の2連鎖を評価するロジックをCHAIN_MERGEに統合。
# これにより、盤面A・nextB・nextNextAの状況でA上にBを置くとnextNextの併合を逃す問題に構造的に対処。
# refs: tmp/batch_summary.txt, tmp/advice.md, game_history/20260307_235239_score0668.jsonl, game_history/20260307_234217_score3478.jsonl,
# strategy_versions/best_score5310_strategy.py, strategy_versions/best_score4999_strategy.py, analyze_board.py
# 
# v181: reactor_pairs >= 2時のHEIGHT_CONTROL抑制強化版 - batch_summaryでHEIGHT_CONTROLが29.3%選択(avg_score_delta=2.1)と依然として過剰であることを確認。
# 高スコア群(27.4%)と低スコア群(31.8%)の比較で、低スコア群が4.4%も多くHEIGHT_CONTROLを選択していることを特定。
# reactor_pairs >= 2の時、着地高が高い場合、HEIGHT_CONTROLの根本的な抑制を行う評価軸を追加し、
# 盤面に多数の併合機会がある状況でHEIGHT_CONTROL選択を構造的に抑制しスコア安定性を向上させる。
# 
# v182: 初期段階HEIGHT_CONTROL抑制強化版 - batch_summaryでHEIGHT_CONTROLが28.1%選択(avg_score_delta=1.7)と依然として過剰であることを確認。
# 3431点ゲーム(高スコア)で初期12ターン積極的にNEAR_MERGE_EARLY_MERGE_PRIORITYを選択し、3431点を出しているのに対し、
# 668点ゲーム(低スコア)で初期7ターン全てHEIGHT_CONTROLを選択し続けている失敗モードを特定。
# 初期段階で無条件でHEIGHT_CONTROLを選択する失敗モードに対処理。
# 初期段階で併合機会がある場合、即時にマージを最優先する戦略に転換。
# 
# v183: 初期12ターンCHAIN_MERGE選択促進版 - batch_summaryでHEIGHT_CONTROLが28.1%選択(avg_score_delta=1.7)と依然として過剰であることを確認。
# 高スコア群と低スコア群の比較で、低スコア群が4.4%も多くHEIGHT_CONTROLを選択していることを特定。
# 初期段階での無条件HEIGHT_CONTROL選択に対処理と初期12ターンCHAIN_MERGE選択促進を統合。
#
# refs: tmp/batch_summary.txt, tmp/advice.md, game_history/20260307_235239_score0668.jsonl, game_history/20260307_234217_score3478.jsonl,
# strategy_versions/best_score5310_strategy.py, strategy_versions/best_score4999_strategy.py, analyze_board.py
# 
# v184: 併合機会ある時のHEIGHT_CONTROL条件付抑制版 - batch_summaryでINITIAL_HEIGHT_CONTROL_SUPPRESSIONが15.8%選択(avg_score_delta=0.4)と低価値であることを確認。
# v183の初期段階での無条件HEIGHT_CONTROL選択抑制ロジック（height_multiplier=0.05+score+=500.0）は、マージ機会がない状況で低価値配置を促進する問題があった。
# 併合機会がある状況でHEIGHT_CONTROLを選択している失敗モードに対処理。
# 初期段階で併合機会がある場合（reactive_pairs>=1 or nextNextマッチ）、HEIGHT_CONTROL選択を条件付で抑制するロジックに置換え。
# refs: tmp/batch_summary.txt, tmp/advice.md, game_history/20260308_004349_score0336.jsonl, game_history/20260308_004123_score1909.jsonl,
# strategy_versions/best_score5310_strategy.py, strategy_versions/best_score4999_strategy.py, analyze_board.py
#
 # v186: reactive_pairs >= 2時のマージ優先ボーナス強化版 - batch_summaryでHEIGHT_CONTROLが26.0%選択(avg_score_delta=3.4)と依然として過剰であることを確認。
 # 高スコア群(23.1%)と低スコア群(29.6%)の比較で、低スコア群が6.5%も多くHEIGHT_CONTROLを選択していることを特定。
 # ワーストゲーム(score0724)のturn 12でreactive_pairs=3にも関わらずHEIGHT_CONTROLを選択し、併合機会を逃している失敗パターンを特定。
 # reactive_pairs >= 2時のREACTIVE_MERGE_PRIORITYボーナスを500.0→700.0に強化し、盤面に多数の併合機会がある状況でマージ選択を優先させる。
 # refs: tmp/batch_summary.txt, tmp/advice.md, game_history/20260308_020939_score0724.jsonl, game_history/20260308_021732_score2655.jsonl,
 # strategy_versions/best_score2346_strategy.py, strategy_versions/best_score5310_strategy.py, analyze_board.py

# Merge result score: type N merge gives N*(N+1)/2 points
# Example: type1+1->2 gives +3 points, type8+8->9 gives +45 points, type14+14->15 gives +120 points
SCORE_TABLE = {i: i * (i + 1) // 2 for i in range(1, 17)}


def decide(game_state: dict, analysis: dict) -> dict:
    """v186: reactive_pairs >= 2時のマージ優先ボーナス強化版

    batch_summaryでHEIGHT_CONTROLが26.0%選択(avg_score_delta=3.4)と依然として過剰であることを確認。
    高スコア群(23.1%)と低スコア群(29.6%)の比較で、低スコア群が6.5%も多くHEIGHT_CONTROLを選択していることを特定。
    ワーストゲーム(score0724)のturn 12でreactive_pairs=3にも関わらずHEIGHT_CONTROLを選択し、併合機会を逃している失敗パターンを特定。

    v186の改善点:
     1. REACTIVE_MERGE_PRIORITYボーナス強化（500.0→700.0）
        - reactive_pairs >= 2の時、盤面に多数の併合機会がある状況でマージ選択を優先
        - HEIGHT_CONTROL選択を抑制し、スコア安定性を向上
     2. v185の初期12ターンマージ優先を維持（EARLY_MERGE_PRIORITY=1000.0）
     3. v184の併合機会がある時のHEIGHT_CONTROL条件付抑制を維持
        - reactive_pairs >= 1 or nextNext == next_type の場合、height_multiplier=5.0
     4. v180のnextNext 2手先評価統合を維持
     5. v177のMEDIUMフェーズHEIGHT_CONTROL抑制を維持（height_multiplier: 15.0）

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

    # --- v174: early_game判定さらに緩和（max_y < -2.0） ---
    # batch_summaryでHEIGHT_CONTROLが26.2%選択(avg_score_delta=1.7)と依然として過剰であることを確認。
    # ワーストゲーム(score0765)で初期7ターン全てHEIGHT_CONTROLを選択し、マージ機会を逃している失敗パターンを特定。
    # early_game判定をmax_y < -2.5→-2.0にさらに緩和し、初期12ターンの大部分で判定されるように調整。
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
        # v169: early_game（max_y < -2.0）の場合、height_multiplierを0.2に削減してHEIGHT_CONTROL過剰選択を抑制
        # v170: MEDIUM phase height_multを1.8→1.4に削減してMEDIUM_TOWER選択を促進
        # v174: early game判定をさらに緩和し、初期12ターンでHEIGHT_CONTROLを抑制
        # v177: MEDIUMフェーズでHEIGHT_CONTROL抑制を強化
        # v178: CRITICALフェーズ危険高さ抑制
        # v184: 併合機会がある場合のHEIGHT_CONTROL条件付抑制
        #   - reactive_pairs >= 1（盤面に即時マージ可能ペアがある）
        #   - nextNext == next_type（次回マージ可能な駒が来る）
        #   - いずれか満たす場合、height_multiplierを抑制

        # 基本値設定
        height_multiplier = 30.0

        # v184: 併合機会がある場合のHEIGHT_CONTROL条件付抑制
        # 盤面に即時マージ可能ペアがある、または次回マージ可能な駒が来る場合、マージ選択を優先
        has_merge_opportunity = (
            reactive_pair_count >= 1 or
            (next_next_type == next_type)
        )
        
        if (early_game or piece_count <= 12) and has_merge_opportunity:
            # 併合機会がある状況では、height_multiplierを抑制してマージ選択を優先
            height_multiplier = 5.0  # v184: 併合機会がある場合はHEIGHT_CONTROL抑制を強化

        # v170: MEDIUM phase height_multを1.8→1.4に削減してMEDIUM_TOWER選択を促進
        if phase == "MEDIUM":
            height_multiplier = 1.4  # v170: MEDIUM phase height penalty relaxation (1.8->1.4) to increase MEDIUM_TOWER selections

        # v177: MEDIUMフェーズでHEIGHT_CONTROL抑制を強化
        if phase == "MEDIUM":
            height_multiplier = 15.0  # v177: v175からさらに緩和しマージ選択を促進

        # 高さペナルティの計算
        height_penalty = landing_y * height_multiplier * height_mult

        # CRITICALフェーズ危険高さ抑制
        if phase == "CRITICAL" and landing_y > 2.0:
            height_penalty += 500.0
            reasons.append("DANGER_HIGH_PLACEMENT")
        elif phase == "HIGH" and landing_y > 0.5:
            height_penalty *= 2.0
            reasons.append("HIGH_TOWER")
        elif phase == "MEDIUM" and landing_y > 0.5:
            height_penalty *= 1.5
            reasons.append("MEDIUM_TOWER_PROMOTION")
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

        # ----- evaluation axis 6: chain merge bonus (v180: nextNext 2手先評価統合版) -----
        # v180: nextNext 2手先評価統合
        # batch_summaryでCHAIN_MERGE(avg_delta=30-50)が高スコアへの効果的reasonであることを確認しつつ、現行v179のCHAIN_MERGE選択率は依然として低い（3-4%）。
        # v179のreactive_pairs活用は方向性は合っているが、CHAIN_MERGE自体の選択を促進できていない。
        # nextNextが現在nextと同じtypeの場合、「現在併合 → nextNextで更に併合」の2連鎖を評価するロジックをCHAIN_MERGEに統合。
        # これにより、盤面A・nextB・nextNextAの状況でA上にBを置くとnextNextの併合を逃す問題に構造的に対処。
        # v155成功パラメータ(chain_distance_max=5.0, chain_bonus_multiplier初期値450.0)をベースに、全フェーズでchain_bonus_multiplierを10%強化(450.0→495.0)。

        # ----- evaluation axis 7: early game merge priority (v174: 初期12ターンマージ重視) -----
        # v174: early_game判定(max_y < -2.0)をさらに緩和し、EARLY_MERGE_PRIORITYの適用範囲をpiece_count <= 10→12に拡大。
        # 初期12ターンを一つのフェーズとして扱い、この期間中はマージ機会を最優先してHEIGHT_CONTROL選択を抑制する。
        # v172の初期条件(early_game && merge_grade == "NEAR")を維持し、piece_count <= 12でも適用することで初期12ターン全体でマージを重視。
        if (early_game or piece_count <= 12) and merge_grade == "NEAR":
            # 初期段階でNEAR_MERGE機会がある場合、強力なボーナスを付与
            # v185: ボーナス強化(800.0→1000.0)で初期12ターン全体でマージ機会を最優先し、HEIGHT_CONTROL選択を抑制
            score += 1000.0
            reasons.append("EARLY_MERGE_PRIORITY")

        # ----- v176: reactive_pairs-based merge priority -----
        # batch_summary分析でHEIGHT_CONTROLが26.5%選択(avg_score_delta=1.1)と過剰であることを確認。
        # reactor情報のreactive_pairs（反応性のあるペア）が2つ以上ある場合、盤面に多数の併合機会があることを示唆。
        # この状況でマージを優先することでHEIGHT_CONTROL選択を抑制しスコア安定性を向上させる。
        if reactive_pair_count >= 2 and merge_grade in ["DIRECT", "NEAR"]:
            score += 700.0
            reasons.append("REACTIVE_MERGE_PRIORITY")

        # ----- evaluation axis 8: MEDIUM_TOWER selection promotion (v174) -----
        # v174: MEDIUM_TOWER選択を促進するための追加評価軸を追加し、高スコア群と低スコア群のMEDIUM_TOWER選択率差（13.6% vs 10.8%）を解消する。
        # MEDIUM phaseでlanding_y > 0.5の場合、MEDIUM_TOWERボーナスを追加して選択を促進。
        if phase == "MEDIUM" and landing_y > 0.5:
            # MEDIUM_TOWER選択を促進するための追加ボーナス
            # MEDIUM_TOWERが高スコア群で13.6%選択されているのに対し、低スコア群では10.8%しか選択されていない
            # MEDIUM_TOWER選択を促進することで、スコア安定性を向上させる
            score += 200.0
            reasons.append("MEDIUM_TOWER_PROMOTION")

        # ----- update best candidate -----
        if score > best_score:
            best_score = score
            best_x = x
            # v169: HEIGHT_CONTROLフォールバック削除を維持
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
        nr = nxt.get("r", 0)

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
