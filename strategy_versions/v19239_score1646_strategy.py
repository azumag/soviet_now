#!/usr/bin/env python3
"""strategy.py - Soviet Puzzle Game AI Drop Position Script

Game Overview:
  - Drop pieces, merge same type pieces (N+N -> N+1)
- Score table: type1=1, type2=3, type3=6, ..., typeN = N*(N+1)/2
- Board: x in [-3.0, +3.0], floor y=-4.48, deadline y=3.32
  - Player controls only drop X coordinate

      Decision Logic (14 evaluation axes):
         1. Merge bonus - High score for immediate merge (DIRECT > NEAR > FAR)
         1.5. NEAR merge deadline risk - Graduated penalty using reactor deadline_margin (v366/v409)
         1.5b. Danger NEAR merge priority - v383: unutilized danger_merge_available for NEAR+danger
         1.7. High pc NEAR merge penalty - v422: structural fork cancels NEAR at pc>=33+deadline+y>=1.0
         1.6. Danger DIRECT merge priority - v382: unutilized danger_direct_merge_available from analysis
        2. Height penalty - Penalty for high landing position (varies by phase)
         3. Drift penalty - Penalty for post-landing drift due to polygon shape
         4. Left-right balance correction - Bonus for correcting piece count bias
          5. nextNext centering - Center for next merge opportunity if nextNext same type
           5.5. Avoid blocking nextNext merge - Penalty for landing on same-type piece when nextNext matches
           5.6. Growth center proximity - v469: base 60→80 strengthening toward target (base 100) per postmortem magnitude allowance
            6. Chain merge bonus - Evaluate possibility of further merges after merge (v466: NEAR suppressed at pc>=32+deadline)
            7. Reactive pairs bonus - Bonus for multiple merge opportunities (reactor info utilization, v206: enhanced)
            8. Early game merge priority - Strong bonus for merge opportunities in early game
             8.5. Danger zone immediate merge bonus - v331: deadline_crossed時即時併合強化
             8.6. Reactive pairs immediate merge bonus - v466: NEAR bonus 60% reduction at pc>=32+deadline (endgame NEAR risk)
              8.7. Russia phase immediate merge priority - v336: ロシア建国後フェーズ即時併合強化版 - axis 8.7ボーナス強化
              # v335 failure: ロシアフェーズ(type 15 >= 1)でreactive_pairs>=3の場合、即時併合ボーナスが弱く、盤面圧縮ボーナスと競合して即時併合機会を取りこぼす
              # ワーストゲーム(score0589)終盤: reactive_pairs>=3, merge_grade="NO"でREACTIVE_PAIRS_NO_MERGE_PENALTYが続き、max_y runawayでゲームオーバー
              # ベストゲーム(score2162)終盤: reactive_pairsが少なく、即時併合機会を確実に捉えて高スコア
              # ロシア建国後は盤面が狭く、高typeピースが場所を占有している状態。この局面で通常時と同じ戦略を続けるのは不十分
              # ロシア建国後は明確にフェーズが切り替わるべき。具体的には:
              #   - 既存のロシア(type 15)の位置を保護しつつ、2つ目のロシアへの成長パイプラインを確保
              #   - ロシアが盤面にある状態で type 13, 14 級のピースをどこに育てるかの空間計画が必要
              #   - 盤面が狭いため、小typeの効率的な消化（併合による盤面確保）がより重要になる
              # axis 8.7修正: ロシアフェーズ && reactive_pairs>=1の場合、即時併合ボーナスを強化
              #  これによりロシア建国後の狭い盤面で、即時併合機会を優先し、盤面圧縮で2つ目のロシア育成スペースを確保
              # advice.md「ロシア建国後の死亡速度が早い。建国後はより慎重な盤面進行を検討すること」に基づくロシアフェーズ強化
              # refs: advice.md (あずまぐ), tmp/state/last_rollback_postmortem.md, tmp/state/last_rollback_analysis.md, tmp/improve_brief.md, tmp/batch_summary.txt, tmp/sandbox_files.md,
              #       game_history/20260324_133153_score0854.jsonl turns 55-63 (ロシア出現後max_y runaway), game_history/20260324_135316_score2615.jsonl
              # Fixes rollback failure mode: ロシア建国後の即時併合機会取りこぼし（axis 8.7ボーナス強化）
             8.8. Reactive pairs >= 3 no merge penalty - v332: 即時併合最優先化版
             9.6. Reactive pairs type-aware stacking - v465: v357ガード復元(rp>=3+NOで抑制) + v408: pc混雑スケーリング(9.6b同一)
             9.6b. Same-type proximity guidance - v468: base 120→160 per v459 removal gap vs protected
             9.7. Pipeline-aware placement guidance - v367: same_type 없い時の隣接type配置誘導 (postmortem axis 9.7 nesting fix)
             9.2. Danger zone reactive penalty - v324: deadline_crossed対応強化版
             9.3. Reactive pair blocking avoidance - v384: landing between reactive pairs of different types
             9.5. Current type stack merge priority - v459: +300 bonus removed (9.6b provides guidance)


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

# Merge result score: type N merge gives N*(N+1)/2 points
# Example: type1+1->2 gives +3 points, type8+8->9 gives +45 points, type14+14->15 gives +120 points
SCORE_TABLE = {i: i * (i + 1) // 2 for i in range(1, 17)}

# --- Change History ---
# v503: pre-russia phase — type 14 detection + high-type merge priority + type 14 proximity guide
#   When type 14 exists (no type 15), add +800/600 for type>=10 merges, +150 proximity near type 14.
#   Disabled at deadline_crossed+NO per mandatory_themes.txt ("no placement past deadline without merge").
#   Fixes rollback failure mode: type 14→type 15 pipeline starvation (zero type 15 in 18 games)
#   refs: tmp/analysis_result.md, tmp/batch_summary.txt, advice.md, data/mandatory_themes.txt
# v502: raise NEAR CHAIN_MERGE suppression pc>=32→35 — halve multiplier at pc=32-34
#   pc=32-34+deadline: chain_bonus_multiplier *= 0.5 (partial guard against NEAR failure)
#   pc>=35+deadline: chain_suppressed=True (full suppression, unchanged)
#   DIRECT (95.7%) unaffected. pc<32 NEAR unaffected.
#   Fixes rollback failure mode: CHAIN_MERGE overrides NEAR risk at pc=32-34
#   refs: tmp/analysis_result.md, game_history/20260426_060958_score0758.jsonl
# v500: cap axis 8.5 NEAR deadline bonus 600→300 — prevent additive cascade
#   DANGER_ZONE NEAR + DANGER_NEAR(300) + REACTIVE_IMMEDIATE(400) = +1000
#   at deadline overpowered NEAR risk penalties, causing failed NEAR selection
#   at pc>=32+deadline. DIRECT (95.7%) unchanged, NEAR (68.5%) capped to 300.
#   Fixes rollback failure mode: near_merge_cascade_at_high_pc_deadline (additive overconfidence)
#   refs: tmp/analysis_result.md, tmp/improve_brief.md, tmp/batch_summary.txt,
#         game_history/20260426_045928_score0379.jsonl,
#         game_history/20260426_050920_score0596.jsonl,
#         tmp/state/last_rollback_analysis.md
# v504: axis 1.7b gap-zone NEAR merge penalty — -500*landing_y at max_y>=2.0+deadline_crossed+pc>=28
#   Suppresses risky NEAR (68.5%) in gap zone to avoid pc accumulation → CRITICAL → game over.
#   best game T80-93 uses DIRECT+danger_direct to recover; worst uses NEAR and fails.
#   Fixes rollback failure mode: gap-zone NEAR failure → piece_count accumulation → early game over
#   refs: tmp/analysis_result.md, tmp/batch_summary.txt, data/mandatory_themes.txt,
#         game_history/20260426_154439_score0493.jsonl,
#         game_history/20260426_165609_score0555.jsonl,
#         game_history/20260426_162242_score2571.jsonl

def decide(game_state: dict, analysis: dict) -> dict:
    """v340: reactive_pairs>=3時deadline_crossed併合最優先版 - v339 failure mode潰し

    v339 failure: reactive_pairs>=3 && deadline_crossed && merge_grade=="NO"の超危険域でaxis 9.6が強力に機能し、高配置 runawayでゲームオーバー
    ワーストゲーム(score0638)終盤turns 55-61: reactive_pairs=7-8, deadline_crossed=true, merge_available=false続きで
    axis 9.6のstacking bonus（stack_yが高いほど大ボーナス）がaxis 8.8の-3000~-7000ペナルティを上回り、高配置が選ばれmax_y=2.37→3.59に上昇してゲームオーバー
    ベストゲーム(score2602)終盤: reactive_pairs=1-2と少なく、即時併合機会を確実に捉えて高スコア
    v338 failure mode: axis 9.6のスタッキングボーナスが強すぎて、高配置を選んでいる可能性がある
    axis 9.6をreactive_pairs>=3 && deadline_crossed && merge_grade=="NO"の場合に無効化し、axis 8.8の即時併合ペナルティを優先
    reactive_pairs<3の場合は、盤面圧縮準備としてaxis 9.6のstacking bonusを維持
    advice.md「盤面がどうだろうが即時併合狙った方が絶対勝率高い」に基づく即時併合優先の構造的改善

    v340の改善点:
    1. axis 9.6修正: reactive_pairs>=3 && deadline_crossed && merge_grade=="NO"の場合、axis 9.6を完全に無効化
    2. 超危険域では即時併合待ちを最優先し、axis 8.8の-3000~-7000ペナルティを優先させることで高配置 runaway を防止
    3. reactive_pairs<3の場合は、盤面圧縮準備としてaxis 9.6のstacking bonusを維持
    4. v339のaxis 9.7削除によって過剰に機能するようになったaxis 9.6を抑制し、即時併合優先を回復
    5. refs: tmp/state/last_rollback_postmortem.md, tmp/state/last_rollback_analysis.md, tmp/improve_brief.md, tmp/batch_summary.txt,
          game_history/20260324_210005_score0638.jsonl, game_history/20260324_210741_score2602.jsonl

    Args:
         game_state: game state (pieces, next, nextNext, score, etc.)
         analysis: analyze_board.py analysis results
              - results: landing information for each drop X candidate
                  - x: drop X coordinate
                  - landing_y: estimated landing Y coordinate (high=dangerous)
                  - drift_x/drift_unc: post-landing drift due to polygon shape
                  - merge_grade: best merge judgment (DIRECT/NEAR/FAR/NO)
                  - danger_direct_merge_available: DIRECT merge available with danger piece
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

    # --- deadline information ---
    # v498: fix deadline_crossed data source — read from reactor (analysis) not game_state.
    # game_state does NOT contain "deadline_crossed" key — game_state.get() always
    # returned False, making ALL deadline logic permanently dormant. This disabled:
    # axis 2 height_mult*=0.2, v288 height_mult*=0.3, DANGER_NEAR 600 bonus,
    # axis 9.2 -4500, CHAIN_MERGE NEAR suppression, and ~10 other deadline axes.
    # reactor.get() reads the correct value from analyze_board.py calc_reactor_state().
    # Fallback reactor_margin < 0 handles edge case where reactor lacks the key.
    # Postmortem HARD CONSTRAINT: height_mult relaxation at deadline is FORBIDDEN —
    # disabling those blocks below even though deadline data is now correct.
    # Fixes rollback failure mode: deadline data source (all deadline logic dormant)
    # refs: tmp/state/last_rollback_postmortem.md, tmp/state/last_rollback_analysis.md,
    #       analyze_board.py (calc_reactor_state returns deadline_crossed), strategy.py.staging v497
    # --- reactor information (for reactive merge priority) ---
    reactor = analysis.get("reactor", {})
    reactive_pairs = reactor.get("reactive_pairs", [])
    # reactive_pairs is a list, count pairs for evaluation
    reactive_pair_count = len(reactive_pairs) if isinstance(reactive_pairs, list) else 0
    danger_piece_count = reactor.get("danger_piece_count", 0)
    reactor_margin = reactor.get("deadline_margin", 99.0)

    # v498: fix deadline_crossed data source — read from reactor (analysis) not game_state.
    # game_state does NOT contain "deadline_crossed" key — game_state.get() always
    # returned False, making ALL deadline logic permanently dormant. This disabled:
    # axis 2 height_mult*=0.2, v288 height_mult*=0.3, DANGER_NEAR 600 bonus,
    # axis 9.2 -4500, CHAIN_MERGE NEAR suppression, and ~10 other deadline axes.
    # reactor.get() reads the correct value from analyze_board.py calc_reactor_state().
    # Fallback reactor_margin < 0 handles edge case where reactor lacks the key.
    # Postmortem HARD CONSTRAINT: height_mult relaxation at deadline is FORBIDDEN —
    # disabling those blocks below even though deadline data is now correct.
    # Fixes rollback failure mode: deadline data source (all deadline logic dormant)
    # refs: tmp/state/last_rollback_postmortem.md, tmp/state/last_rollback_analysis.md,
    #       analyze_board.py (calc_reactor_state returns deadline_crossed), strategy.py.staging v497
    deadline_crossed = reactor.get("deadline_crossed", reactor_margin < 0)

    # --- v322: russia phase detection (type 15 pieces on board) ---
    # ロシアフェーズ: 盤面上にtype 15（ロシア）が1つ以上存在する場合
    # advice.md「ロシア建国後の死亡速度が早い。建国後はより慎重な盤面進行を検討すること」に基づく構造的改善
    # ロシア建国後は盤面が狭く、高typeピースが場所を占有している状態。この局面で通常時と同じ戦略を続けるのは不十分
    russia_phase_count = sum(1 for p in pieces if p.get("type") == 15)
    russia_phase = russia_phase_count >= 1

    # v503: pre-russia phase detection — type 14 piece(s) on board, but no type 15 yet.
    # When type 14 exists, prioritize building a second type 14 for type 15 merge.
    # This is the PRE-RUSSIA phase: the board has a type 14, the goal is to make
    # a second type 14, merge them into type 15 (Russia).
    # Analysis found: all 18 batch games had ZERO type 15 appearances.
    # russia_phase (axis 8.7) is dead code unless we reach type 15.
    # This pre-phase creates the missing pipeline: type 14 → second type 14 → type 15.
    # refs: tmp/analysis_result.md, tmp/batch_summary.txt, advice.md
    pre_russia_phase = (not russia_phase) and russia_phase_count == 0 and \
        max((p.get("type", 0) for p in pieces), default=0) >= 14

    # --- phase judgment (v42 thresholds) ---
    if max_y < 0.8:
        phase = "LOW"
        height_mult = 0.4  # v198: LOW phase height_mult further reduced (0.6→0.4) to enable proactive merge opportunities
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

    # ----- evaluation axis 9.5: current type stack merge priority (NEW: same type stacking) -----
    # advice.md「同じタイプが続いて来たらそのタイプの上に置き、併合チャンスを優先する」（Pitman_live）に基づく構造的改善。
    # batch_summaryでHEIGHT_CONTROLが15.9%選択(avg_score_delta=0.1)と過剰であり、即時併合機会を取りこぼしていることを確認。
    # 危険域（max_y >= 2.0）では、盤面圧縮より即時併合優先を優先するため、盤面圧縮ボーナスを抑制
    # refs: advice.md (Pitman_live), tmp/batch_summary.txt, last_rollback_postmortem.md
    same_type_pieces = [p for p in pieces if p.get("type") == next_type]
    same_type_stack_top = None
    if same_type_pieces:
        # 盤面上の現在タイプの最も高い位置のピースを見つける
        same_type_stack_top = max(same_type_pieces, key=lambda p: p.get("y", -10))

    # --- v360: per-type reactive/near pair extraction (unutilized reactor info) ---
    # reactive_pairs is list of (piece_id_1, piece_id_2, type) tuples
    # near_pairs is list of (piece_id_1, piece_id_2, type, gap) tuples
    # Extract which types have reactive pairs for type-aware stacking decisions
    near_pairs = reactor.get("near_pairs", [])
    # --- v367: pipeline extraction (unutilized reactor info) ---
    # pipeline is list of (type, type+1, min_distance) tuples — adjacent-type proximity
    # Used by axis 9.7 for placement guidance when no same-type on board
    pipeline = reactor.get("pipeline", [])

    # --- v384: pre-compute piece positions for reactive pair blocking avoidance ---
    # Used by axis 9.3 to check if landing position is between reactive pair pieces.
    # Computed once before the candidate loop since pieces don't change between candidates.
    piece_pos_by_id = {p["id"]: (p["x"], p["y"]) for p in pieces}
    current_type_has_reactive = any(
        rp[2] == next_type
        for rp in reactive_pairs
        if isinstance(rp, (list, tuple)) and len(rp) >= 3
    )
    current_type_has_near = any(
        np[2] == next_type
        for np in near_pairs
        if isinstance(np, (list, tuple)) and len(np) >= 3
    )

    # =======================================================================
    # score each drop candidate (x coordinate) with evaluation axes
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

        # ----- v366/v409: NEAR merge risk penalty at deadline (graduated via reactor margin) -----
        # postmortem: piece_count accumulation is the key failure predictor.
        # Worst game T50-52: 3 consecutive NEAR merges at deadline_crossed, all fail
        # (score_delta=0), piece_count grows 32->35. Best game succeeds with merges.
        # NEAR merge success rate is 68.5%. At deadline, failed NEAR adds a high piece
        # with no benefit, worsening the already dangerous board state.
        # v409: Replace binary deadline_crossed with continuous reactor deadline_margin.
        # reactor deadline_margin: <0 means deadline crossed, 0-1 means approaching.
        # Graduated risk_factor = (1.0 - reactor_margin) when margin < 1.0.
        # This avoids the cliff where pieces just before deadline get 0 penalty but
        # pieces just after get full penalty. Provides partial protection when
        # approaching deadline (margin 0-1), reducing p25 early-death rate.
        # Does NOT affect: DIRECT merges, NEAR when margin >= 1.0, or NO merge.
        # NOT v388 crosses_deadline per-candidate (different field/mechanism, no chain suppression).
        # postmortem constraint: combines landing_y with deadline proximity (not landing_y-only).
        # refs: game_history/20260330_115102_score0447.jsonl (margin cliff early death),
        #       game_history/20260330_115755_score0839.jsonl,
        #       tmp/state/last_rollback_postmortem.md (piece_count predictor),
        #       tmp/batch_summary.txt (low-score 5.4% NEAR_HIGH_LAYER vs high-score 3.9%),
        #       analyze_board.py (reactor deadline_margin field)
        # Fixes rollback failure mode: piece_count accumulation from failed NEAR at deadline (v366)
        # Fixes p25 collapse: binary cliff causes sudden behavior change at deadline crossing (v409)
        if merge_grade == "NEAR" and landing_y > 0 and reactor_margin < 1.0:
            risk_factor = min(1.0, max(0.0, 1.0 - reactor_margin))
            # v421: piece_count-aware risk scaling — at high pc, failed NEAR is catastrophic
            # Rollback target: pc=33 DIRECT +282, pc 35→27. Bad: pc=34 NEAR fails ×2, pc→36.
            # At pc=33: scale=1.25. At pc=35: 1.75. At pc=40: 3.0. No change below pc=33.
            if piece_count >= 33:
                pc_risk_scale = 1.0 + (piece_count - 32) * 0.25
            else:
                pc_risk_scale = 1.0
            near_risk_penalty = landing_y * 300.0 * risk_factor * pc_risk_scale
            score -= near_risk_penalty
            reasons.append("NEAR_DEADLINE_RISK")

        # ----- evaluation axis 1.7: high pc NEAR merge penalty (v422: structural strategy fork) -----
        # Postmortem priority: "pc>=33 で DIRECT merge のみを積極的に狙い、NEAR merge は
        # landing_y < 0 の安全なものに限定するロジック"
        # v421 added gradual pc_risk_scale but net NEAR still positive at pc=35, deadline,
        # y=1.0 (+75). Failed NEAR (31.5% success) at high pc adds piece without benefit,
        # accelerating piece_count accumulation → max_y runaway → game over.
        # Worst: pc=36, NEAR at high y fails ×2, pc→36. Best: pc=33, NEAR at y<0 (landing
        # below board surface) succeeds with chain (+267, pc 33→28, recovery).
        # New axis: at pc>=33, deadline risk (margin<1.0), landing_y>=1.0, cancel base NEAR
        # bonus (600*merge_mult). Other axes (danger, reactive, chain) still provide NEAR
        # incentive if warranted. Combined with v421 NEAR_DEADLINE_RISK, net NEAR at
        # pc=35, deadline, y=1.0: +75 → -525. At pc=33, y=1.5: +337 → -562.
        # NEAR at y<1.0 still positive — preserves safe recovery path (best game T82).
        # Structurally similar to v411 (crosses_deadline penalty) and russia_phase fork (axis 8.7).
        # Fixes postmortem failure mode: piece_count accumulation from failed NEAR at high pc
        # refs: tmp/state/last_rollback_postmortem.md, tmp/state/last_rollback_analysis.md,
        #       tmp/batch_summary.txt, strategy.py.staging (v421),
        #       game_history/20260331_031009_score1030.jsonl T76-83,
        #       game_history/20260331_025511_score2317.jsonl T82-83
        if (
            merge_grade == "NEAR"
            and piece_count >= 33
            and reactor_margin < 1.0
            and landing_y >= 1.0
        ):
            score -= 600.0 * merge_mult
            reasons.append("HIGH_PC_NEAR_PENALTY")

        # ----- evaluation axis 1.7b: gap-zone NEAR merge penalty (v504) -----
        # worst(score0493) T48-50 + extra_low(score0555) T50-51: gap-zoneで
        # NEAR merge選択 → delta=0, pc増加 → CRITICAL突入 → 早期game over.
        # best(score2571) T80-93: gap-zoneでDIRECT優先, NO_MERGE時も段階的に耐える。
        # max_y>=2.0: gap-zone開始（best T80で既に危険な高さ）
        # deadline_crossed: デッドライン超過状態
        # pc>=28: 高pc下ではNEAR失敗のコストが致命的
        # penalty: 500*abs(landing_y)（landing_y=1.0で-500, y=2.0で-1000）
        #   → NEAR base +600から引くと net +100〜-400 でNO_MERGE低配置と競合
        # 禁止: height_mult緩和しない（postmortem hard constraint）
        # 禁止: DIRECT mergeには適用しない
        # 禁止: russia_phaseでは既にaxis 8.7が別扱いのため適用しない
        if (
            merge_grade == "NEAR"
            and max_y >= 2.0
            and deadline_crossed
            and piece_count >= 28
            and not russia_phase
        ):
            score -= abs(landing_y) * 500.0
            reasons.append("GAP_ZONE_NEAR_PENALTY")

        # ----- evaluation axis 1.6: danger DIRECT merge priority (v382: unutilized analysis info) -----
        # Postmortem prioritize: "deadline_crossed下でのDIRECT_MERGEの優先度を最大化すること。
        # targetのscore1359 T77(DIRECT_MERGE_HIGH_LAYER, +100)が示す通り、danger_direct_mergeは
        # deadline下の主要スコア源。" danger_direct_merge_available is a per-candidate flag from
        # analyze_board.py indicating this drop position achieves a DIRECT merge with a danger piece
        # (piece near/past deadline y=3.32). This is the highest-value merge opportunity:
        # 1. DIRECT merge = 95.7% success rate (vs NEAR 68.5%)
        # 2. Danger piece removal = prevents game over from that piece
        # 3. At deadline_crossed, danger pieces are the most urgent targets
        # Currently UNUTILIZED — strategy reads danger_direct_merge_available from game_state but
        # not from per-candidate analysis results. Adding this ensures the strategy strongly prefers
        # DIRECT merges that also resolve danger pieces, especially at deadline.
        # Bonus magnitude: 800 — competitive with DANGER_ZONE_IMMEDIATE_MERGE_PRIORITY (500-1200)
        # but additive (stacks with it). Ensures danger DIRECT beats non-danger DIRECT at deadline.
        # NOT a NEAR penalty or suppression — purely additive bonus for DIRECT merges with danger.
        # Postmortem constraints respected: no gradient flattening, no NEAR suppression,
        # no AVOID_BLOCK_NEXTNEXT suppression, no piece_count scaling.
        # refs: game_history/20260328_222114_score1359.jsonl T77 (DIRECT_MERGE_HIGH_LAYER, +100,
        #       danger_direct_merge_available=true, deadline_crossed=true),
        #       analyze_board.py L391-397 (danger_direct_merge_available calculation),
        #       tmp/state/last_rollback_postmortem.md, tmp/state/last_rollback_analysis.md
        # Fixes rollback failure mode: endgame scoring starvation at deadline
        if (
            result.get("danger_direct_merge_available", False)
            and merge_grade == "DIRECT"
        ):
            score += 800.0
            reasons.append("DANGER_DIRECT_MERGE_PRIORITY")

        # ----- evaluation axis 1.5b: danger NEAR merge priority (v383: unutilized danger_merge_available) -----
        # Postmortem: "deadline_crossed下でのDIRECT_MERGEの優先度を最大化" — v382 addressed DIRECT.
        # danger_merge_available covers NEAR merges targeting danger pieces. Removing a danger piece
        # (redLineTime>0 or past deadline) prevents game over. Currently unutilized — strategy only
        # reads danger_direct_merge_available.
        # Worst game T58/T68/T74: NEAR+danger selected but failed (delta=0). Best game T170: NEAR+danger
        # succeeded (+144). The bonus makes danger NEAR more decisive when multiple NEAR candidates exist.
        # NEAR deadline risk penalty (landing_y*300) still discourages high-risk NEAR: at y=2.0 with
        # deadline bonus, net = 0+600-600 = 0 (marginal). At y=1.0: net = 600+600-300 = 900 (encouraged).
        # Below DIRECT merge (1200) — priority ordering maintained. Purely additive, no suppression.
        # Fixes rollback failure mode: endgame scoring starvation (danger NEAR merge undervalued)
        # refs: tmp/state/last_rollback_postmortem.md, tmp/state/last_rollback_analysis.md,
        #       tmp/batch_summary.txt, analyze_board.py (danger_merge_available L398-404),
        #       game_history/20260329_081450_score0774.jsonl, game_history/20260329_080000_score3902.jsonl,
        #       game_history/20260329_080456_score2801.jsonl, strategy_versions/protected/protected_e6f534c37e28_median12789_strategy.py
        if result.get("danger_merge_available", False) and merge_grade == "NEAR":
            # v421: suppress DANGER_NEAR bonus at high pc + high landing_y + deadline
            # Postmortem: "landing_y >= 1.5 かつ deadline_crossed 時の NEAR merge は
            # DANGER_NEAR_MERGE_PRIORITY を無効化するか NEAR_DEADLINE_RISK を増強すること"
            # At pc>=33, deadline, landing_y>=1.5: danger NEAR at high y adds piece if fails
            # (31.5% rate) with no benefit. Suppress bonus to let enhanced risk penalty work.
            # v498: cap DANGER_NEAR at 300 regardless of deadline — postmortem constraint:
            # "DANGER_NEAR_MERGE_PRIORITY bonus of 600 at deadline overpowers NEAR_DEADLINE_RISK
            # + HIGH_PC_NEAR_PENALTY at pc>=33+deadline, causing failed NEAR selection"
            # Flat 300 matches protected strategy behavior. Previously 600 at deadline enabled
            # near_merge_cascade_at_high_pc_deadline (3 of 4 NEAR attempts failed).
            if deadline_crossed and piece_count >= 33 and landing_y >= 1.5:
                bonus = 0.0
            else:
                bonus = 300.0
            score += bonus
            reasons.append("DANGER_NEAR_MERGE_PRIORITY")

        # ----- evaluation axis 9.6: reactive pairs stacking bonus (v340: reactive_pairs>=3時deadline_crossed併合最優先版) -----
        # advice.md「同じタイプが続いて来たらそのタイプの上に置き、併合チャンスを優先する」に基づく戦略的改善
        # batch_summaryでHEIGHT_CONTROLが19.9%選択(avg_score_delta=1.2)と過剛、即時併合機会を取りこぼしていることを確認
        # reactive_pairsがあるがmerge_grade=="NO"の場合、HEIGHT_CONTROLではなく戦略的配置を優先する
        # v340 failure mode: reactive_pairs>=3 && deadline_crossed && merge_grade=="NO"の超危険域でaxis 9.6が強力に機能し、高配置 runawayでゲームオーバー
        # ワーストゲーム(score0638)終盤turns 55-61: reactive_pairs=7-8, deadline_crossed=true, merge_available=false続きで
        # axis 9.6のstacking bonus（stack_yが高いほど大ボーナス）がaxis 8.8の-3000~-7000ペナルティを上回り、高配置が選ばれmax_y=2.37→3.59に上昇してゲームオーバー
        # axis 8.8の即時併合ペナルティを優先させ、超危険域での高配置 runaway を防止
        # v339 failure: axis 9.7の盤面圧縮ボーナスが削除されたため、axis 9.6のstacking bonusが過剰に機能するようになった
        # reactive_pairs>=3 && deadline_crossed && merge_grade=="NO"の場合は盤面が過密で即時併合待ちが最優先すべき局面
        # axis 9.6を無効化し、axis 8.8のペナルティを優先させることで即時併合機会の取りこぼしを削減
        # reactive_pairs<3の場合は、盤面圧縮準備としてaxis 9.6のstacking bonusを維持
        # 未活用情報：deadline_crossed, reactive_pairs>=3, merge_grade, stack_y
        # refs: tmp/state/last_rollback_postmortem.md, tmp/state/last_rollback_analysis.md, tmp/improve_brief.md, tmp/batch_summary.txt,
        #       game_history/20260324_210005_score0638.jsonl turns 55-61, game_history/20260324_210741_score2602.jsonl
        # Fixes rollback failure mode: reactive_pairs>=3 && deadline_crossedでの高配置 runaway（axis 9.6超危険域無効化）

        # ----- evaluation axis 9.6: reactive pairs stacking bonus - v363: stacking extension to reactive>=3 -----
        # v339/v340 failure: vertical_bonus = (stack_y + 1.0) * 200.0 rewards high positions,
        #   causing high-tower stacking when reactive pairs exist for other types but not current type
        # Worst(score0653): turns 57-64 reactive=1-2, REACTIVE_PAIRS_STACKING_HIGH_TOWER at y=1.1-2.7
        # Worst(score0853): reactive=5 but next_type=2 has no reactive_pairs → stacks at y=2.4 → game over
        # v360: only fire stacking when current type has reactive/near pairs (unutilized reactor type info)
        # v357: suppress stacking when reactive>=3 (axis 8.8 -4500 should dominate all candidates equally)
        # axis 9.7 (REACTIVE_PAIRS_COMPRESSION) removed: protected_e6f534c37e28 found it harmful (median 12789)
        # Bonus based on proximity to merged_type(N+1), NOT on height — prevents high-tower incentive
        # refs: strategy_versions/protected/protected_e6f534c37e28_median12789_strategy.py,
        #       tmp/state/last_rollback_postmortem.md, tmp/batch_summary.txt,
        #       game_history/20260328_051045_score0653.jsonl turns 57-64,
        #       game_history/20260327_020329_score0853.jsonl turns 69-76
        # Fixes rollback failure mode: reactive_pairsあるが現在タイプにreactive_pairsがない場合の高位スタッキング
        # v363: v340 guard(reactive<3)を除去。旧スタッキング公式の高さインセンティブはv360で解消済み。
        # v360 stackingはmerged_type近接度ベース(max~400, y>1で減衰)で高さに依存しないため、
        # reactive>=3でもaxis 8.8(-4500)が支配し、スタッキングはtie-breakingに留まる。
        # postmortem制約: reactive_pair_count<3ガードなし(全reactiveレベルで動作)。
        # v465: restore v357 guard — suppress at rp>=3+NO per protected strategy.
        # At rp>=3+NO, stacking bonus creates position noise without enabling merges.
        # Protected strategy (median 12789) suppresses here, proven effective.
        if (
            reactive_pair_count >= 1
            and reactive_pair_count < 3
            and merge_grade == "NO"
            and same_type_stack_top is not None
        ):
            # v416: stacking target redirection — replace v414/v415 binary block with
            # state-dependent target selection. Postmortem: "Reducing stacking_bonus in a
            # way that doesn't also strengthen the alternative placement logic" — blocking
            # stacking (v414/v415) removed guidance entirely, falling through to HEIGHT_CONTROL
            # scatter which avg_score_delta=1.7. Now stacking ALWAYS fires, but the TARGET
            # selection changes based on board congestion:
            #   Normal: merged_type proximity (chain building, original algorithm)
            #   Congested: lowest same-type piece (height-priority, natural height reduction)
            # Congested conditions (from v414/v415 postmortem):
            #   - max_y>=3.0 + deadline crossed: extreme danger, stacking at any height risky
            #   - rp>=5 + max_y>=2.5: board congested, high stacking makes it worse
            # In congested mode, stacking still pulls placement near a same-type piece (future
            # merge potential) but chooses the LOWEST target, naturally reducing landing height.
            # This is structurally different from blocking: stacking_bonus still competes with
            # height penalty, providing a guided alternative to HEIGHT_CONTROL scatter.
            # refs: game_history/20260330_200337_score0587.jsonl T67-73,
            #       game_history/20260330_195749_score0574.jsonl T51-58,
            #       tmp/state/last_rollback_postmortem.md (v413/v414/v415 failures)
            stacking_congested = (
                (max_y >= 3.0 and deadline_crossed)
                or (reactive_pair_count >= 5 and max_y >= 2.5)
            ) and merge_grade == "NO"
            if current_type_has_reactive or current_type_has_near:
                if stacking_congested:
                    # Height-priority: stack on lowest same-type piece
                    # Preserves stacking incentive while naturally reducing height
                    best_stack_target = min(
                        same_type_pieces, key=lambda sp: sp.get("y", 10)
                    )
                    best_chain_score = 100.0
                else:
                    # Chain-priority: merged_type proximity for chain building
                    best_stack_target = same_type_stack_top
                    best_chain_score = 0.0
                    for sp in same_type_pieces:
                        sp_x = sp.get("x", 0)
                        sp_y = sp.get("y", -10)
                        # merged_typeピースとの最短距離を計算
                        min_merged_dist = float("inf")
                        for p in pieces:
                            if p.get("type") == merged_type:
                                dist = (
                                    (p["x"] - sp_x) ** 2 + (p["y"] - sp_y) ** 2
                                ) ** 0.5
                                if dist < min_merged_dist:
                                    min_merged_dist = dist
                        # 連鎖スコア: merged_typeに近いほど高く、高位すぎる場合は減衰
                        if min_merged_dist < float("inf"):
                            chain_score = max(0, 300.0 - min_merged_dist * 80.0)
                            if sp_y > 1.0:
                                chain_score *= max(0, 1.0 - (sp_y - 1.0) * 0.5)
                            if chain_score > best_chain_score:
                                best_chain_score = chain_score
                                best_stack_target = sp
                # best_stack_targetに近い配置にボーナス（高さに依存しない固定ボーナス）
                target_x = best_stack_target.get("x", 0)
                horizontal_distance = abs(x - target_x)
                if horizontal_distance < 2.0:
                    stacking_bonus = best_chain_score + max(
                        0, 100.0 - horizontal_distance * 40.0
                    )
                    # v408: piece_count congestion scaling — match axis 9.6b formula
                    # At high pc, stacking must be stronger to compete with height penalty
                    # and prevent HEIGHT_CONTROL edge scatter during merge droughts.
                    # Axis 9.6b already uses this formula; 9.6 lacked it, creating an
                    # asymmetry where reactive stacking was weaker than non-reactive proximity.
                    if piece_count >= 28:
                        congestion_scale = 1.0 + (piece_count - 28) * 0.12
                        stacking_bonus *= min(congestion_scale, 3.0)
                    score += stacking_bonus
                    reasons.append("REACTIVE_PAIRS_STACKING")

        # ----- v367: axis 9.7 pipeline-aware placement guidance (sibling to 9.6) -----
        # Postmortem constraint: axis 9.7 should be a sibling of axis 9.6, not nested inside it.
        # Fires when: same_type_stack_top is None (no same-type on board), reactive >= 1, merge_grade == "NO"
        # This is the case that v359 (REACTIVE_PAIRS_COMPRESSION with landing_y-only bonus) tried to fix
        # but failed due to landing_y-only approach and reactive < 3 guard.
        # v359 rollback: "replaces HEIGHT_CONTROL with naive compression based solely on landing_y"
        # This version uses reactor["pipeline"] (unutilized info): list of (type, type+1, min_distance) tuples.
        # Finds nearest adjacent-type (next_type ± 1) piece on board via pipeline data.
        # Bonus for proximity to adjacent-type piece guides placement toward merge pipeline,
        # creating future merge opportunities instead of aimless low placement.
        # Worst game T58: reactive=3, type=10, no same-type 10 → MEDIUM_TOWER (no guidance) → pc grows.
        # If type 9 or type 11 pieces existed, this guidance would direct placement near them.
        # Bonus magnitude: max ~80 (tie-breaking only, won't override axis 8.8 or height penalty).
        # No reactive_pair_count < 3 guard (postmortem constraint: works at ALL reactive levels).
        # Not landing_y-only (postmortem constraint: uses pipeline proximity to specific types).
        # refs: tmp/state/last_rollback_postmortem.md (axis 9.7 nesting fix, piece_count predictor),
        #       analyze_board.py (reactor["pipeline"] structure),
        #       game_history/20260328_112219_score0613.jsonl T58 (reactive=3, no same-type, no guidance),
        #       strategy_versions/protected/protected_e6f534c37e28_median12789_strategy.py,
        #       tmp/batch_summary.txt, advice.md (zoumotu3: growth concentration)
        # Fixes postmortem failure mode: no guidance when same_type_stack_top is None → piece_count accumulation
        if (
            reactive_pair_count >= 1
            and merge_grade == "NO"
            and same_type_stack_top is None
        ):
            # Find nearest piece whose type is adjacent to current type (next_type ± 1)
            # Priority: next_type - 1 (merge up path) then next_type + 1 (if next_type-1 not found)
            best_adjacent_target = None
            best_adjacent_dist = float("inf")
            for p in pieces:
                p_type = p.get("type", 0)
                if p_type == next_type - 1 or p_type == next_type + 1:
                    p_x = p.get("x", 0)
                    p_y = p.get("y", 10)
                    # Prefer deeper (lower y) pieces — more accessible for future merges
                    adj_dist = ((x - p_x) ** 2 + (landing_y - p_y) ** 2) ** 0.5
                    if adj_dist < best_adjacent_dist:
                        best_adjacent_dist = adj_dist
                        best_adjacent_target = p
            if best_adjacent_target is not None and best_adjacent_dist < 3.0:
                pipeline_bonus = max(0, 80.0 - best_adjacent_dist * 30.0)
                score += pipeline_bonus

        # ----- v362/v368 → v369 → v371 → v453: merged_type-aware targeting + congestion-aware proximity -----
        # v371: Prefer same-type piece closest to merged_type(N+1) for chain building, not just lowest.
        # advice.md "TypeN+1と隣接している方を優先してドロップする" (azumag, nimdavirus).
        # After N+N→N+1 merge, the resulting piece is near existing N+1 → immediate N+1+N+1 opportunity.
        # v369 targeted lowest same-type (accessibility) but ignored chain potential.
        # Worst game: 40 pieces, max type 12 scattered. Best game: 31 pieces, type 15 concentrated.
        # If no merged_type piece on board, falls back to lowest (same as v369).
        # Bonus magnitude, congestion scaling, and target_y decay unchanged from v369.
        # No reactive<3 guard (postmortem constraint). Not landing_y-only (proximity + pc + target_y).
        # v453: restored from v449 removal. v418 rp_density_scaling NOT restored — was part of
        # accumulation problem per postmortem. Proximity ~120-360 stays below height diffs.
        # refs: advice.md (azumag, nimdavirus), tmp/state/last_rollback_postmortem.md,
        #       tmp/batch_summary.txt, game_history/20260328_151000_score0486.jsonl T54-61,
        #       game_history/20260328_151437_score3261.jsonl T112-119,
        #       strategy_versions/protected/protected_e6f534c37e28_median12789_strategy.py
        # Fixes postmortem failure mode: type scattering → piece_count accumulation
        if merge_grade == "NO" and same_type_stack_top is not None:
            # v467: extend to rp>=3 — fills guidance gap where neither 9.6 (v465 guard)
            # nor original 9.6b (requires no reactive) fires. At rp<3+reactive, axis 9.6
            # handles stacking; at rp>=3+reactive, this extension provides proximity tie-breaking.
            if (
                not (current_type_has_reactive or current_type_has_near)
                or reactive_pair_count >= 3
            ):
                # v371: Find same-type piece closest to merged_type(N+1) for chain building.
                # This creates future N+1+N+1 opportunities after N+N→N+1 merge.
                merged_type_pieces = [p for p in pieces if p.get("type") == merged_type]
                best_proximity_target = None
                best_proximity_dist = float("inf")
                for sp in same_type_pieces:
                    sp_x = sp.get("x", 0)
                    sp_y = sp.get("y", -10)
                    min_mt_dist = float("inf")
                    for mp in merged_type_pieces:
                        mt_dist = ((sp_x - mp["x"]) ** 2 + (sp_y - mp["y"]) ** 2) ** 0.5
                        if mt_dist < min_mt_dist:
                            min_mt_dist = mt_dist
                    if min_mt_dist < best_proximity_dist:
                        best_proximity_dist = min_mt_dist
                        best_proximity_target = sp
                # Fallback to lowest same-type if no merged_type on board
                if best_proximity_target is None or best_proximity_dist == float("inf"):
                    best_proximity_target = min(
                        same_type_pieces, key=lambda p: p.get("y", 10)
                    )

                target_x = best_proximity_target.get("x", 0)
                target_y = best_proximity_target.get("y", -10)
                horiz_dist = abs(x - target_x)
                if horiz_dist < 2.0:
                    # v369 congestion-aware proximity — no reactive level split
                    # Postmortem: piece_count is the key predictor of final score.
                    # No reactive<3 guard (postmortem constraint: works at ALL reactive levels).
                    # Not landing_y-only (considers horizontal proximity, piece_count, target height).
                    proximity_bonus = max(0, 160.0 - horiz_dist * 50.0)
                    # v467: at rp>=3+current_type has reactive (extension case), reduce bonus
                    # by 60% to respect v465 noise concern. Max ~50-120 vs axis 8.8 (-4500).
                    # Original 9.6b (no reactive) keeps full bonus for stronger guidance.
                    if reactive_pair_count >= 3 and (
                        current_type_has_reactive or current_type_has_near
                    ):
                        proximity_bonus *= 0.4
                    if piece_count >= 28:
                        # Scale proportionally with congestion: at pc=35, bonus *= 1.84
                        # At pc=40, bonus *= 2.48 — meaningful for axis 8.8 tie-breaking
                        congestion_scale = 1.0 + (piece_count - 28) * 0.12
                        proximity_bonus *= min(congestion_scale, 3.0)
                    if target_y > 0:
                        proximity_bonus *= max(0.0, 1.0 - target_y * 0.3)
                    # v412: nextNext-aware proximity — when next two pieces are same type,
                    # strengthen guidance. After next merges (creating N+1), remaining same-type
                    # targets are nearby for the next-next piece. 1.5x multiplier raises bonus
                    # from ~190 to ~285 at pc=35, competitive with height diffs (~100-200).
                    # Only fires when merge_grade=NO (doesn't compete with immediate merges).
                    # refs: advice.md (Pitman_live), tmp/batch_summary.txt
                    if next_type == next_next_type:
                        proximity_bonus *= 1.5
                    # v453: v418 rp_density_scaling NOT restored — was part of accumulation problem.
                    # Proximity bonus ~120-540 stays below height diffs (~100-200), avoiding
                    # the postmortem warning about "additive bonus accumulation masking height
                    # differentiation" that occurred when rp_density_scale went up to 2.5x.
                    # rp_guidance_suppressed still used for congestion state detection:
                    rp_guidance_suppressed = (max_y >= 3.0 and deadline_crossed) or (
                        reactive_pair_count >= 5 and max_y >= 2.5
                    )
                    if rp_guidance_suppressed:
                        proximity_bonus = 0.0
                    if proximity_bonus > 0:
                        score += proximity_bonus

        # ----- evaluation axis 9.3: reactive pair blocking avoidance (v384) -----
        # advice: "併合できるtypeが隣接しているとき、その間にピースを配置してしまうと、併合しづらくなる"
        # Placing a piece between reactive pairs of different types can physically block
        # their future merge, leading to piece_count accumulation and game over.
        # Worst game T37-47: 6-8 reactive pairs, pieces placed at y=2.58 between reactive
        # pairs, no merges for 11 turns, piece_count grows 30→40.
        # Penalty per blocked pair: 200, capped at 500 total. Small enough to not override
        # merge opportunities (DIRECT +1200, NEAR +600) or axis 8.8 (-3000 to -9000).
        # Only fires when merge_grade=="NO" (no immediate merge to suppress).
        # Uses reactive_pairs position data from analyze_board.py (rp format: (id1, id2, type)).
        # refs: advice.md, tmp/state/last_rollback_postmortem.md,
        #       game_history/20260329_090616_score0296.jsonl T37-47,
        #       game_history/20260329_090011_score0811.jsonl T73-80, analyze_board.py
        if merge_grade == "NO" and reactive_pair_count >= 1:
            # v417: suppress AVOID_BLOCK in congested endgame to prevent edge scatter.
            # In congested regime (rp>=5, max_y>=2.5 or max_y>=3.0+deadline), AVOID_BLOCK
            # overwhelms stacking/proximity guidance (~500 penalty vs ~300 bonus), pushing
            # pieces to isolated edge positions (x=±3.0). Suppressing allows guidance to work.
            board_congested = (max_y >= 3.0 and deadline_crossed) or (
                reactive_pair_count >= 5 and max_y >= 2.5
            )
            if not board_congested:
                blocking_penalty = 0.0
                for rp in reactive_pairs:
                    if isinstance(rp, (list, tuple)) and len(rp) >= 3:
                        rp_type = rp[2]
                        if rp_type != next_type:
                            pos1 = piece_pos_by_id.get(rp[0])
                            pos2 = piece_pos_by_id.get(rp[1])
                            if pos1 and pos2:
                                x1, y1 = pos1
                                x2, y2 = pos2
                                # Check if landing is within the horizontal span of the reactive pair
                                span_min = min(x1, x2) - 0.5
                                span_max = max(x1, x2) + 0.5
                                if span_min <= x <= span_max:
                                    # Penalize if landing at or above the reactive pair level
                                    pair_min_y = min(y1, y2)
                                    if landing_y >= pair_min_y:
                                        blocking_penalty += 200.0
                if blocking_penalty > 0:
                    score -= min(blocking_penalty, 500.0)
                    reasons.append("AVOID_BLOCK_REACTIVE_PAIR")

        # ----- evaluation axis 2: height penalty -----
        # landing Y coordinate higher means larger penalty. phase height_mult adjusts weight.
        # v197: LOW phase height_mult=0.6 enables early chain opportunities by allowing slightly higher placement
        # v294: deadline_crossed reactive_pairs board compression - v291 failure mode潰し
        # ワーストゲーム(score0323)終盤turns 44-51でdeadline_crossed=true, reactive_pairs=5-6あるのに即時併合不可、
        # 戦略的配置が続きmax_y=2.15→3.51に上昇してゲームオーバー。
        # ベストゲーム(score1716)終盤turns 81-88ではdeadline_crossed=trueでも即時併合を確実に捉えてスコア1716を出している。
        # v291のaxis 2 height_mult *= 0.2 がheight_penalty計算後だったため、盤面圧縮候補が選ばれなかった。
        # axis 8.8のボーナスがaxis 2の後で評価されるため、height_penaltyと競合できていなかった。
        # v290のaxis 8.8（+300-800 at axis 7.5）が有効だったパターンを再現。
        # deadline_crossed && reactive_pair_count >= 2 && merge_grade == "NO" && danger_piece_count == 0 の場合、
        # height_multを0.2に緩和し、盤面圧縮（tighter board）を優先。即時併合機会を確保する。
        # refs: tmp/improve_brief.md, tmp/batch_summary.txt, tmp/state/last_rollback_postmortem.md, tmp/state/last_rollback_analysis.md, advice.md,
        #       game_history/20260321_040215_score0323.jsonl turns 44-51, game_history/20260321_035338_score1716.jsonl turns 81-88

        # deadline_crossed reactive_pairs board compression - axis 2統合簡素化版
        # v324: danger_piece_count==0条件追加 - v323 failure mode潰し
        # v323 failure: axis 2 height_mult relaxationにdanger_piece_count==0条件がなく、危険ピースがある状況でもheight_multを0.2に緩和してしまい、戦略的配置の余地を確保できていない
        # ワーストゲーム(score0651)終盤turns 44-47: deadline_crossed=true, reactive_pairs=4, danger_piece_count=1でheight_mult緩和が適用され、即時併合がない高配置が選ばれmax_y runawayでゲームオーバー
        # ベストゲーム(score2461)ではdeadline_crossed=trueでも即時併合を確実に捉え、戦略的配置を維持して安定
        # axis 2修正: deadline_crossed && reactive_pair_count >= 2 && merge_grade == "NO" && danger_piece_count == 0 の条件にdanger_piece_count==0を追加し、
        # 危険ピースがない場合に限りheight_multを0.2に緩和して、盤面圧縮（tighter board）を優先し、即時併合機会を確保する
        # refs: tmp/state/last_rollback_postmortem.md, tmp/state/last_rollback_analysis.md, tmp/improve_brief.md, tmp/batch_summary.txt, advice.md,
        #       game_history/20260324_010847_score0651.jsonl turns 44-47, game_history/20260324_010300_score2461.jsonl
        # Fixes rollback failure mode: deadline_crossed時の危険ピース存在下での即時併合取りこぼし（axis 2 danger_piece_count条件追加）

        # deadline_crossed時、reactive_pairsが多数ある即時併合不可時に、戦略的配置の余地を確保
        # danger_piece_count==0の場合に限りheight_multを0.2に緩和して、盤面圧縮（tighter board）を優先し、即時併合機会を確保
        # v498: DISABLED — postmortem constraint: "height_mult relaxation at deadline is FORBIDDEN".
        # Original v294 reduced height_mult to 0.2x at deadline, removing primary defense against
        # max_y runaway at the most dangerous moment. When deadline_crossed was always False (bug),
        # this never fired and the strategy accidentally worked better. Now that deadline_crossed
        # is correctly sourced from reactor, this would activate and cause catastrophic runaway.
        # Evidence: v497 rollback — height_mult 0.2 allowed pieces to land 5x higher than normal
        # at deadline, compounding NEAR merge failures into irreversible cascade.
        # The deadline NO-merge penalty (axis 9.6, -4500 at line 1445) provides sufficient
        # incentive to avoid NO-merge at deadline without weakening height control.
        # if deadline_crossed and reactive_pair_count >= 2 and merge_grade == "NO" and danger_piece_count == 0:
        #     if current_type_has_reactive or current_type_has_near:
        #         height_mult *= 0.2

        # v270 fix: reactive_pairsあり時の非併合heightペナルティ緩和版 - 危険域での戦略的配置余地を確保
        # ワーストゲーム(score0797)終盤turns 47-52でreactive_pairs=3あるのにmerge_available=falseが続き、
        # -1500.0ペナルティにより強制的に高配置となりゲームオーバー。
        # ベストゲーム(score2945)終盤turns 127-133でも同様の状況だが、より多くのターンを耐えている。
        # axis 8.5の-1500.0ペナルティは全候補一律に下げるため、「強制配置」問題が残る。
        # reactive_pairs>=1かつmerge_grade=="NO"の場合、height_multを0.8に緩和し、
        # 戦略的配置の余地を確保しつつdeadline緊急性を維持。reactive_pairsを活用して将来の併合を狙う戦略的思考へ切り替える。
        # v268/v270 rollback教訓: 強制的な高配置回避。reactive_pairs活用のシンプルな改善を採用。
        # v332: reactive_pairs>=3の場合はheight_mult緩和をスキップし、即時併合を最優先する戦略へ切り替え
        # ワーストゲーム(score0754)終盤turns 58-65: reactive_pairs=3-5, merge_available=falseでheight_mult緩和が適用され、axis 8.8ペナルティが打ち消されmax_y runawayでゲームオーバー
        # ワーストゲーム(score0831)終盤turns 51-63: reactive_pairs=3-6, merge_available=false続きで同様の現象
        # reactive_pairs>=3は超危険域であり、即時併合機会を強制的に待つ戦略へ切り替える必要がある
        # refs: tmp/batch_summary.txt, tmp/state/last_rollback_postmortem.md, tmp/state/last_rollback_analysis.md,
        #       game_history/20260319_023107_score0797.jsonl turns 46-53, game_history/20260319_020802_score2945.jsonl turns 126-133,
        #       game_history/20260324_065958_score0754.jsonl turns 58-65, game_history/20260324_072048_score0831.jsonl turns 51-63
        if reactive_pair_count >= 1 and reactive_pair_count < 3 and merge_grade == "NO":
            # reactive_pairs>=3の場合はaxis 8.8ペナルティを有効にするためheight_mult緩和をスキップ
            # reactive_pairs>=3は超危険域であり、即時併合機会を強制的に待つ戦略へ切り替える
            height_mult *= 0.8

        # v288: deadline_crossed時戦略的配置強化版 - 即時併合機会取りこぼし削減
        # ワーストゲーム(score0877)終盤turns 67-69でdeadline_crossed=true, reactive_pairs=4あるのに即時併合不可、
        # 戦略的配置が続きmax_y=2.77→3.59に上昇してゲームオーバー。
        # ベストゲーム(score2693)終盤turns 121-127でdeadline_crossed=trueでも即時併合を確実に捉えてスコア2693を出している。
        # batch_summaryでHEIGHT_CONTROLが10.1%選択(avg_score_delta=0.0)と過剰、即時併合機会取りこぼしが問題。
        # last_rollback_postmortemの制約遵守：max_y>=2.0を危険域判定条件に追加しない、deadline_crossed時もSAME_TYPE_STACK有効。
        # deadline_crossed && reactive_pair_count >= 1 && merge_grade == "NO" の場合、height_multを0.4に緩和して、
        # 戦略的配置の余地を更に確保し、即時併合機会を逃さないようにする。
        # 未活用情報（deadline_crossed）を活用した構造的変更であり、数値微調整ではない。
        # v332: reactive_pairs>=3の場合はheight_mult緩和をスキップし、即時併合を最優先する戦略へ切り替え
        # ワーストゲーム(score0754)終盤turns 58-65: reactive_pairs=3-5, merge_available=falseでheight_mult緩和が適用され、axis 8.8ペナルティが打ち消されmax_y runawayでゲームオーバー
        # ワーストゲーム(score0831)終盤turns 51-63: reactive_pairs=3-6, merge_available=false続きで同様の現象
        # reactive_pairs>=3は超危険域であり、即時併合機会を強制的に待つ戦略へ切り替える必要がある
        # refs: tmp/improve_brief.md, tmp/batch_summary.txt, tmp/state/last_rollback_postmortem.md, tmp/state/last_rollback_analysis.md,
        #       game_history/20260320_222520_score0877.jsonl turns 64-71, game_history/20260320_221810_score2693.jsonl turns 120-127,
        #       game_history/20260324_065958_score0754.jsonl turns 58-65, game_history/20260324_072048_score0831.jsonl turns 51-63
        # v498: DISABLED — postmortem constraint: "height_mult relaxation at deadline is FORBIDDEN".
        # Original v288 reduced height_mult to 0.3x at deadline, compounding with axis 2 (0.2x)
        # and v270 (0.8x) to floor 0.048x — effectively nullifying height penalty at the
        # most dangerous moment. Postmortem evidence: this enabled REACTIVE_PAIRS_STACKING
        # at HIGH_TOWER without merge opportunity (7 consecutive zero-delta turns).
        # The deadline NO-merge penalty (axis 9.6, -4500) and NEAR risk penalties provide
        # sufficient deadline-specific behavior without weakening height control.
        # if deadline_crossed and reactive_pair_count >= 1 and reactive_pair_count < 3 and merge_grade == "NO":
        #     height_mult *= 0.3

        # v362: height_mult floor — prevent compounding nullification
        # 3 gates (0.2x/0.8x/0.3x) compound to 0.048x, nullifying height penalty.
        # Floor of 0.5 keeps height penalty meaningful while allowing strategic flexibility.
        # Previously validated in v356 (protected strategy median 12789), lost in v359 rollback.
        # refs: strategy_versions/protected/protected_e6f534c37e28_median12789_strategy.py,
        #       tmp/state/last_rollback_postmortem.md, tmp/change_log.txt
        height_mult = max(height_mult, 0.5)

        # Calculate height penalty after all height_mult modifications
        height_penalty = landing_y * 50.0 * height_mult

        if phase == "HIGH" and landing_y > 0.5:
            height_penalty *= 2.0
            reasons.append("HIGH_TOWER")
        elif phase == "MEDIUM" and landing_y > 0.5:
            height_penalty *= 1.5
            reasons.append("MEDIUM_TOWER")
        elif landing_y > 0.0:
            reasons.append("HIGH_LAYER")

        score -= height_penalty

        # ----- v361/v470/v499: piece_count congestion penalty -----
        # postmortem: bad strategy ends with 40-46 pieces, rollback target with 21-25.
        # piece_count is the key predictor of final score, not max_y.
        # When board is congested, penalize high landing positions to encourage tighter
        # placement that enables merges and reduces piece_count.
        # This is NOT landing_y-only — it combines piece_count state with landing position.
        # No reactive_pair_count guard — works at ALL reactive levels (postmortem constraint).
        # v499: lower threshold 30→28, raise multiplier 30→35 — provide meaningful height
        # differentiation during critical transition period (pc=28-35) when HEIGHT_CONTROL
        # scatter accumulates pieces without producing merges.
        # Evidence: batch_summary HEIGHT_CONTROL 17.3% (avg_delta=0.5), low-score 18.3%
        # vs high-score 14.0%. Worst game T50-57: 7/8 zero-delta turns, max_y jumps 2.05→3.49.
        # Protected strategy (median 12789) has NO congestion penalty but also NO post-protected
        # additive axes. This partially compensates for additive noise by strengthening height
        # signal before the endgame congests beyond recovery.
        # At pc=28, y=1.0: v470=0, v499=35. At pc=30, y=1.0: v470=30, v499=105.
        # At pc=35, y=2.0: v470=360, v499=560. Still below NEAR merge (600) — preserves merges.
        # At pc=40, y=2.0: v470=660, v499=910. Slightly exceeds NEAR at extreme — aligns
        # with postmortem constraint "pc>=38+deadline, NEAR should be net-negative".
        # Advice: "盤面の高さ余裕を優先的に管理し、駒の積み上げペースを抑制する" (akai235).
        # refs: tmp/state/last_rollback_postmortem.md (pc 41→1060 vs 21→4645),
        #       tmp/batch_summary.txt (HEIGHT_CONTROL 17.3%, merge_rate gap),
        #       advice.md (akai235, kbb246),
        #       game_history/20260404_005736_score0613.jsonl T50-57,
        #       game_history/20260404_005523_score2510.jsonl T103-111,
        #       strategy_versions/protected/protected_e6f534c37e28_median12789_strategy.py
        if piece_count >= 28 and landing_y > -1.0:
            # v499: threshold 30→28, multiplier 30→35, offset 29→27
            congestion_penalty = (piece_count - 27) * landing_y * 35.0
            score -= congestion_penalty

        # ----- evaluation axis 9.6: deadline_crossed immediate merge priority (NEW: v335: deadline_crossed時即時併合最優先強化版 - v334 failure mode潰し) -----
        # last_rollback_postmortemのfailure mode: "deadline_crossed時に即時ゲームオーバー判定を行い、reactive pairs の併合機会を失っている"
        # bad_strategy(ee2c76235324, v334): deadline_crossed時に即時ゲームオーバー判定を行い、reactive_pairsの併合機会を失っている
        # rollback_target(608f63a01e6b, v330): deadline_crossed時も danger_piece_count == 0 の場合はプレイを継続し、reactive pairs を併合して高スコアを達成している
        # v334 failure: axis 2とaxis 9.5からdanger_piece_count条件を削除したため、danger_piece_count > 0 の状況でも戦略的配置が選ばれてしまい、即時併合機会を取りこぼしている
        # ワーストゲーム(score0720)終盤turns 49-57: deadline_crossed=true, reactive_pairs=3-6, danger_piece_count=2-6で即時併合不可続きmax_y=3.21に上昇してゲームオーバー
        # ベストゲーム(score2599)終盤turns 116-123: deadline_crossed=trueでも即時併合機会を確実に捉えて2599点を出している
        # axis 9.6追加: deadline_crossed時にreactive_pairsがある場合、即時併合を逃した非併合配置に強力なペナルティ(-4500.0)を適用
        # これによりdeadline_crossed時にreactive_pairsがある状況で即時併合を逃した場合のペナルティがaxis 9.2のペナルティよりも高くなり、即時併合を強制的に待つ戦略へ切り替える
        # axis 9.5の盤面圧縮ボーナスは適用しない。即時併合機会を最大化することを目的としているため、戦略的配置ボーナスを抑制
        # 未活用情報：deadline_crossed, reactive_pair_count, merge_grade
        # refs: tmp/state/last_rollback_postmortem.md, tmp/state/last_rollback_analysis.md, tmp/improve_brief.md, tmp/batch_summary.txt, advice.md, tmp/sandbox_files.md,
        #       game_history/20260324_122310_score0720.jsonl turns 49-57, game_history/20260324_120021_score2599.jsonl turns 116-123
        # Fixes rollback failure mode: deadline_crossed時の即時併合機会取りこぼし（axis 9.6追加・axis 9.2 deadline_crossed条件追加・axis 9.5条件追加・axis 2 danger_piece_count条件維持）

        if deadline_crossed and reactive_pair_count >= 1 and merge_grade == "NO":
            # v501: restore height gradient — flat -4500 (v454) makes all candidates
            # identical at deadline. Additive axes (9.6b, 9.6, 5.6, 9.3) accumulate
            # ~800-1200 bonuses; without gradient, HEIGHT_CONTROL scatters to edges
            # (latest T116: x=1.4 crosses deadline, delta=0; T117: x=-3.0 edge scatter).
            # Gradient base -2500 at y<0 (overwhelms additive bonuses), rising to -6500
            # at y=2 (strongly penalizes high placement). ~4000 differential restores
            # meaningful height-based decision at deadline.
            # Fixes failure mode: deadline scatter from no height differentiation
            score -= 2500.0 + max(0, landing_y) * 2000.0
            reasons.append("DEADLINE_CROSSED_IMMEDIATE_MERGE_PRIORITY")

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

        # ----- evaluation axis 5.6: growth center proximity (v370: all-reactive, congestion-aware) -----
        # v364→v370: Extended growth center proximity to fire at ALL reactive levels.
        # Re-introduced in v364 but was limited to reactive<3 with max bonus 50 — too weak.
        # Worst game (score0926) T78: 38 pieces, types 1-12 scattered, reactive=8, no merge.
        # Best game (score3212) T142: types 14/13x2/12x2 concentrated, reactive=1.
        # Growth center guidance encourages placing pieces near highest-type pieces,
        # naturally concentrating types for merge path creation (advice: zoumotu3).
        # v370 changes from v364:
        # 1. Removed reactive<3 guard — guidance now fires at ALL reactive levels.
        #    At reactive>=3, axis 8.8 dominates but guidance provides better tie-breaking.
        # 2. Increased base bonus to 100 (from 50) — matches axis 9.6b magnitude for competitive signal.
        # 3. Added piece_count congestion scaling — stronger guidance as board congests.
        #    At pc=28: 100. At pc=35: ~198. At pc=40: ~268. Safe vs axis 8.8 (-3000 to -7000).
        # 4. Changed decay: gc_y > 0 now uses 0.4 decay (from 0.5) — slightly less aggressive decay.
        # Postmortem constraints: no reactive<3 guard, not landing_y-only (proximity+pc+gc_y).
        # refs: advice.md (zoumotu3, garsy38), tmp/batch_summary.txt,
        #       game_history/20260328_141811_score0926.jsonl, game_history/20260328_140715_score3212.jsonl,
        #       tmp/state/last_rollback_postmortem.md (piece_count predictor),
        #       strategy_versions/protected/protected_994de46c98dd_median11502_strategy.py
        # Fixes rollback failure mode: type scattering → piece_count accumulation
        # v407: removed russia_phase guard — growth center guidance now active in ALL phases
        max_type_on_board = max((p.get("type", 0) for p in pieces), default=0)
        if max_type_on_board >= 6:
            # Find the deepest (lowest y) highest-type piece as growth center
            growth_center = min(
                (p for p in pieces if p.get("type") == max_type_on_board),
                key=lambda p: p.get("y", 10),
                default=None,
            )
            if growth_center:
                gc_x = growth_center.get("x", 0)
                gc_y = growth_center.get("y", -10)
                horiz_dist = abs(x - gc_x)
                if horiz_dist < 2.5:
                    # v469: base 60→80 strengthening toward target (base 100) per postmortem
                    proximity = max(0, 80.0 - horiz_dist * 40.0)
                    # Decay if growth center is high — don't override height control
                    if gc_y > 0:
                        proximity *= max(0.0, 1.0 - gc_y * 0.4)
                    # v370: congestion-aware scaling — postmortem: piece_count is key predictor
                    # At high piece_count, guidance needs to be stronger to compete with
                    # height differences and provide meaningful redirect toward growth center.
                    if piece_count >= 28:
                        congestion_scale = 1.0 + (piece_count - 28) * 0.08
                        proximity *= min(congestion_scale, 2.0)
                    if proximity > 0:
                        score += proximity

        # ----- evaluation axis 6: chain merge bonus (v196: 初期段階CHAIN_MERGE有効化版)
        # batch_summaryでCHAIN_MERGE関連がavg_score_delta=50.7-61.0（高価値）だが選択率は5.8%以下と低いことを確認。
        # ワーストゲーム(score0598)では初期8ターンのうち7ターンがHEIGHT_CONTROLを選択し、マージ機会を逃している失敗モードを特定。
        # ベストゲーム(score2416)では初期段階から積極的にNEAR_MERGEを選択し、スコア2416を出していることを確認。
        # v195のchain_bonus_multiplier動的設定では初期段階(landing_y=-3.0)でchain_bonus_multiplier=45.0,ほぼゼロ。
        # 初期段階でのCHAIN_MERGE選択を有効化するためにchain_bonus_multiplierの初期値を495.0に固定し、着地高による動的調整を開始地点から行うようにする。
        if merge_grade in ["DIRECT", "NEAR"] and result.get("merges"):
            # v463: lower CHAIN_MERGE NEAR suppression from pc>=35 to pc>=28 at deadline
            # v460 (pc>=35) kicked in too late: worst games die at pc=29-34 where CHAIN_MERGE
            # bonus (~3000-6000 at deadline y) overwhelms NEAR risk penalties (~600-2000),
            # making risky NEAR (68.5% success) the "best" candidate. Failed NEAR at pc=31-32
            # is irrecoverable — adds piece without reducing pc, tightens deadline margin further.
            # Evidence: score0933 T59(pc=31), T60(pc=32) NEAR fails with CHAIN_MERGE ~4000+,
            # no suppression fires. score0933 T63(pc=33) HIGH_PC_NEAR_PENALTY fires but
            # CHAIN_MERGE still overwhelms. score0884 T62(pc=37) already past recovery.
            # v460 history: originally v451 at pc>=35+deadline, rolled back as v449 collateral,
            # re-applied as v460. v463 extends to pc>=28: covers the critical failure zone while
            # preserving CHAIN_MERGE for early-game NEAR (pc<28) where pipeline growth benefits.
            # DIRECT (95.7%) retains CHAIN_MERGE at all pc — only NEAR is suppressed.
            # Postmortem compliance: reduces additive bonus magnitude (not axis 5.6 filter).
            # Rollback failure mode: none expected — only narrows NEAR CHAIN_MERGE window.
            # Fixes failure mode: CHAIN_MERGE overrides NEAR risk at medium pc (28-34)
            # refs: game_history/20260402_010847_score0933.jsonl T59-65,
            #       game_history/20260402_004634_score0884.jsonl T62-69,
            #       game_history/20260402_011735_score2578.jsonl T118,
            #       tmp/change_log.txt (v460), strategy.py.staging (v462)
            # v466: raise CHAIN_MERGE NEAR suppression from pc>=28 to pc>=32
            # v463 (pc>=28) was too aggressive: worst games die at pc=29-34 where board
            # still has recovery room. At pc=28-31, failed NEAR adds 1 piece but board
            # isn't critically full. Best game T82: NEAR at pc=33 recovered pc 33->28.
            # At pc>=32, failure is truly catastrophic — suppression stays.
            # Fixes failure mode: NEAR merge avoidance at medium pc → piece accumulation
            # refs: game_history/20260402_035958_score1152.jsonl T77-84 (0 merges),
            #       game_history/20260402_042732_score1179.jsonl T65-75 (CROSSES x5),
            #       game_history/20260402_042432_score4489.jsonl T167 (delta=+410),
            #       tmp/batch_summary.txt, tmp/change_log.txt (v463,v464)
            # v502: raise complete suppression from pc>=32 to pc>=35 — narrow the
            # zero-CHAIN_MERGE zone to the most critical regime. At pc=32-34, instead
            # halve chain_bonus_multiplier to provide partial chain incentive while
            # restoring balance against NEAR failure risk (68.5% success rate).
            # Worst game T52-T56: NEAR+CHAIN_MERGE ~ +3000-5000 net vs NO merge
            # -2500~-6500, NEAR always wins despite 31.5% failure. Halving at pc=32-34
            # reduces chain_bonus ~2000-4000 -> ~1000-2000, competing with risk penalties.
            # pc>=35: full suppression (chain_suppressed=True), chain never fires.
            # Fixes rollback failure mode: CHAIN_MERGE overrides NEAR risk at pc=32-34
            # refs: tmp/analysis_result.md, game_history/20260426_060958_score0758.jsonl
            chain_suppressed = (
                merge_grade == "NEAR" and piece_count >= 35 and deadline_crossed
            )
            chain_mult_near_halved = (
                merge_grade == "NEAR"
                and piece_count >= 32
                and piece_count < 35
                and deadline_crossed
            )
            merges = result["merges"]
            if merges and not chain_suppressed:
                # get best merge target (closest distance)
                best_merge = min(merges, key=lambda m: m.get("dist", float("inf")))
                target_x = best_merge.get("x", 0)
                target_y = best_merge.get("y", 0)

                # v196: 初期段階CHAIN_MERGE有効化 - 初期段階でのCHAIN_MERGE選択を有効化
                # v460: NEAR at pc>=35+deadline is suppressed above (chain_suppressed)
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
                if chain_mult_near_halved:
                    chain_bonus_multiplier *= 0.5

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

                # v155成功構造: 3つの最も近いピースに対し、距離に応じて減衰するボーナスを適用
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

        # ----- evaluation axis 7: early game merge priority -----
        # 初期12ターンでマージ機会がある場合、強力なボーナスを付与
        # batch_summaryでHEIGHT_CONTROLが28.7%選択(avg_score_delta=1.8)と過剰であり、
        # ワーストゲーム(score0826)では初期8ターンのうち7ターンがHEIGHT_CONTROLを選択し、マージ機会を逃している。
        # ベストゲーム(score2330)では初期段階から積極的にNEAR_MERGE_EARLY_MERGE_PRIORITYを選択し、スコア2330を出していることを確認。
        # v194のearly_game判定(max_y < -2.5)では抑制が強すぎ、gapがある間のマージ機会を見逃している問題を解決。
        # マージ機会がある場合の優先配置を高めるため、early_gameをmax_y < -2.5に緩和し、初期段階でのHEIGHT_CONTROL選択を抑制しつつマージ優先を強化。
        # 初期8ターンまででEARLY_MERGE_PRIORITY条件を緩和し、全体的にマージ機会を優先する戦略へ転換。
        if piece_count <= 12 and merge_grade == "NEAR":
            # 初期段階でNEAR_MERGE機会がある場合、強力なボーナスを付与
            # これにより初期12ターン全体でマージ機会を最優先し、HEIGHT_CONTROL選択を抑制
            score += 1000.0
            reasons.append("EARLY_MERGE_PRIORITY")

        # ----- evaluation axis 8: reactive pairs bonus (NEW: reactor info utilization, enhanced) -----
        # batch_summaryでHEIGHT_CONTROLが23.8%選択(avg_score_delta=1.2)と過剰であることを確認。
        # NEAR_MERGE系reasonsがavg_score_delta=28-57（高価値）だが選択率が3.8-9.2%と低いことを確認。
        # ワーストゲーム終盤（score932）ではreactive_pairs=4.5あるにもかかわらず即時併合優先が弱く、HEIGHT_CONTROL選択で下振れ。
        # ベストゲーム（score3037）はreactive_pairsが少ないが即時併合機会を確実に捉えてスコア稼ぎ。
        # v201 rollback教訓: 複雑な危険局面判定ロジックは禁止。シンプルなマージ重視戦略を維持。
        # reactor情報のreactive_pairs（反応性のあるペア）を活用し、即時併合を優先する評価軸を強化。
        # v206: reactive_pairs>=3で即時併合（DIRECT/NEAR）の場合、ボーナスを+800.0から+1000.0に強化。
        # v206: reactive_pairs>=3で即時併合なし（NO）の場合、盤面密度ボーナスを+300.0から+50.0に削減。
        if reactive_pair_count == 1 and merge_grade in ["DIRECT", "NEAR"]:
            # reactive_pairs==1の場合も即時併合を優先し、機会取りこぼし削減
            score += 400.0
            reasons.append("REACTIVE_MERGE_PRIORITY")
        elif (
            reactive_pair_count >= 2
            and reactive_pair_count < 3
            and merge_grade in ["DIRECT", "NEAR"]
        ):
            # 2つの反応可能ペアがある場合、強力なマージ優先ボーナス（v202: 500→800）
            score += 800.0
            reasons.append("REACTIVE_MERGE_PRIORITY")
        elif reactive_pair_count >= 3 and merge_grade in ["DIRECT", "NEAR"]:
            # v206: reactive_pairs>=3で即時併合（DIRECT/NEAR）の場合、ボーナスを強化（+1000.0）
            # reactive_pairsが3以上ある場合、即時併合機会を最優先
            score += 1000.0
            reasons.append("REACTIVE_MERGE_PRIORITY")
        # v209: reactive_pairs>=3で即時併合なしの場合のcompression_bonusロジックを削除
        # avg_score_delta=2.3と低効果であり、即時併合優先ボーナス(+1000.0)と競合して不整合を招いていた
        # 即時併合がない場合は、既存の評価軸（height/drift/balance/chainなど）で判断する

        # ----- evaluation axis 8.5: danger zone immediate merge bonus (v321: 危険域即時併合強化・axis 8.5削除版) -----
        # v317 failure: axis 8.5（危険域で即時併合不可時にheight_multを0.4に緩和して盤面圧縮を優先）が過剰に機能し、即時併合機会を取りこぼしてmax_y runawayでゲームオーバー
        # ワーストゲーム(score0890)終盤turns 59-65: reactive_pairs=1-3あるのに即時併合不可続きmax_y=3.71に上昇してゲームオーバー
        # ベストゲーム(score2551)終盤turns 112-116: reactive_pairs=2-3あるのに即時併合を確実に捉えてmax_y=2.84で安定
        # batch_summaryでHEIGHT_CONTROLが11.1%選択(avg_score_delta=0.0)と過剰、即時併合機会取りこぼしが主要な敗因
        # advice.md「盤面がどうだろうが即時併合狙った方が絶対勝率高い」に基づき、危険域での即時併合を強力に優先
        # axis 8.5削除: 危険域で即時併合不可時のheight_mult *= 0.4盤面圧縮ロジックを削除し、即時併合機会の取りこぼしを防止
        # axis 8.5再定義: 危険域（max_y >= 2.0 && reactive_pair_count >= 2）で即時併合ボーナスを強化し、即時併合を最優先
        # 未活用情報：危険域判定(max_y>=2.0), reactive_pairs数
        # refs: tmp/state/last_rollback_postmortem.md, tmp/state/last_rollback_analysis.md, tmp/improve_brief.md, tmp/batch_summary.txt, advice.md

        danger_piece_count = reactor.get("danger_piece_count", 0)

        # 危険域での即時併合を強力に優先
        if (
            max_y >= 2.0
            and reactive_pair_count >= 2
            and merge_grade in ["DIRECT", "NEAR"]
        ):
            if merge_grade == "DIRECT":
                # v331: deadline_crossed時はボーナスを強化（500.0→1200.0）
                if deadline_crossed:
                    score += 1200.0
                else:
                    score += 500.0
                reasons.append("DANGER_ZONE_IMMEDIATE_MERGE_PRIORITY")
            else:
                # v500: cap NEAR deadline bonus 600→300 — prevent additive cascade
                # DANGER_ZONE NEAR + DANGER_NEAR(300) + REACTIVE_IMMEDIATE(400) = +1000
                # at deadline overpowered NEAR risk penalties, causing failed NEAR
                # selection at pc>=32+deadline. DIRECT (95.7%) unchanged.
                if deadline_crossed:
                    score += 300.0
                else:
                    score += 300.0
                reasons.append("DANGER_ZONE_IMMEDIATE_MERGE_PRIORITY")

        # ----- evaluation axis 8.6: reactive pairs immediate merge bonus (v464: NEAR 60% reduction at pc>=28+deadline) -----
        # v317: reactive_pairs数に応じた即時併合ボーナスを維持
        # v464: NEAR併合は68.5%成功率で高pc下では失敗コストが致命的。pc>=28+deadlineでNEARボーナスを60%削減。
        #   DIRECT(95.7%成功率)は変更なし。NEARは240/400(従来の40%)にスケールダウン。
        #   これによりNEARがNO-merge(-4500)に対して依然有利だが、高pc下のheight_penalty等の影響を受けやすくなる。
        # refs: tmp/improve_brief.md, tmp/batch_summary.txt, advice.md, tmp/change_log.txt (v463),
        #       game_history/20260402_015932_score0680.jsonl (T58: NEAR fail, pc=32),
        #       game_history/20260402_021836_score0828.jsonl (T60: NEAR fail, pc=34)

        if reactive_pair_count >= 1 and merge_grade in ["DIRECT", "NEAR"]:
            if merge_grade == "NEAR" and piece_count >= 32 and deadline_crossed:
                # v466: raise threshold from pc>=28 to pc>=32 (match CHAIN_MERGE NEAR suppression)
                # At pc=28-31, NEAR failure is recoverable. At pc>=32, catastrophic.
                bonus = 400.0 if reactive_pair_count >= 2 else 240.0
            else:
                bonus = 1000.0 if reactive_pair_count >= 2 else 600.0
            score += bonus
            reasons.append("REACTIVE_IMMEDIATE_MERGE_PRIORITY")

        # ----- evaluation axis 8.7: russia phase immediate merge priority (v337: ロシアフェーズでのaxis 9.5盤面圧縮ボーナス抑制版 - axis 8.7即時併合優先強化) -----
        # advice.md「ロシア建国後の死亡速度が早い。建国後はより慎重な盤面進行を検討すること」「ロシアのような大きいピースが盤面の上に出てきた時は、戦略モードを切り替えるべき」に基づく構造的改善
        # ロシアフェーズ（type 15 >= 1）で即時併合を最優先する戦略へ切り替え
        # 即時併合候補がある場合: 即時併合を最優先（強力なボーナス）
        # 即時併合がない場合: 危険ピースがない場合のみ盤面圧縮を優先しつつ、type 15保護を徹底
        # 危険ピースがある場合は即時併合優先を維持（axis 9.2のペナルティを優先）
        # v337: axis 9.5の盤面圧縮ボーナスがaxis 8.7の即時併合ボーナスと競合しているため、russia_phase && reactive_pair_count < 3 の場合にaxis 9.5を抑制
        #   - 即時併合ボーナス強化: DIRECT: +1400.0, NEAR: +1200.0
        #   - 盤面圧縮ボーナス抑制: reactive_pairs>=3 && merge_grade == "NO" の場合、盤面圧縮ボーナスを付与しない
        #   - v337: russia_phase && reactive_pair_count < 3 の場合、axis 9.5のボーナス（+300.0）と軽減（+100.0）を削除し、即時併合を最優先
        # 未活用情報：盤面上のtype 15個数、即時併合可否(merge_grade)、reactive_pairs、danger_piece_count
        # refs: tmp/improve_brief.md, tmp/batch_summary.txt, advice.md,
        #       game_history/20260324_141236_score0731.jsonl, game_history/20260324_144026_score3171.jsonl

        # ----- evaluation axis 8.7-pre: pre-russia phase merge priority / type 14 concentration (v503) -----
        # analysis_result.md adopted hypothesis: type 14 detection phase.
        # When type 14 exists but no type 15, prioritize building the second type 14
        # pipeline. High-type merges (>=10) get bonus; NO-merge placement guided near type 14.
        # Disabled at deadline_crossed+NO merge per mandatory_themes.txt.
        # Fixes rollback failure mode: type 14→type 15 pipeline starvation (zero type 15 in 18 games)
        # refs: tmp/analysis_result.md, tmp/batch_summary.txt, advice.md, data/mandatory_themes.txt
        if pre_russia_phase:
            if merge_grade in ["DIRECT", "NEAR"]:
                if next_type >= 10:
                    if merge_grade == "DIRECT":
                        score += 800.0
                    else:
                        score += 600.0
                    reasons.append("PRE_RUSSIA_MERGE_PRIORITY")
            elif merge_grade == "NO":
                if not deadline_crossed:
                    type14_pieces = [p for p in pieces if p.get("type") == 14]
                    if type14_pieces:
                        deepest_14 = min(type14_pieces, key=lambda p: p.get("y", 10))
                        t14_x = deepest_14.get("x", 0)
                        horiz_dist = abs(x - t14_x)
                        if horiz_dist < 2.5:
                            proximity_bonus = max(0, 150.0 - horiz_dist * 60.0)
                            score += proximity_bonus
                            reasons.append("PRE_RUSSIA_PROXIMITY")

        if russia_phase:
            # ロシアフェーズでの即時併合優先
            # 即時併合候補がある場合、最優先（強力なボーナス）
            if merge_grade in ["DIRECT", "NEAR"]:
                # v336: reactive_pairs>=1 の場合、ボーナスを強化して即時併合を最優先
                if reactive_pair_count >= 1:
                    # reactive_pairs>=1の場合、ボーナスを強化（600.0/1000.0 -> 1200.0/1400.0）
                    if merge_grade == "DIRECT":
                        score += 1400.0 if reactive_pair_count >= 3 else 1200.0
                    else:
                        score += 1200.0 if reactive_pair_count >= 3 else 1000.0
                else:
                    # v333 baseline: reactive_pairs>=3 の場合、より強力なボーナス
                    if merge_grade == "DIRECT":
                        score += 1400.0
                    else:
                        score += 1200.0
                reasons.append("RUSSIA_PHASE_IMMEDIATE_MERGE_PRIORITY")
            elif merge_grade == "NO":
                # 即時併合がない場合、盤面圧縮を優先しつつ、type 15保護を徹底
                # v336: reactive_pairs<3の場合でも即時併合ボーナスを強化し、盤面圧縮ボーナスを抑制
                if reactive_pair_count >= 3:
                    # reactive_pairs>=3の超危険域では、axis 8.8ペナルティを優先させるため盤面圧縮ボーナスを抑制
                    # v333 baseline: reactive_pairs>=3 の場合のボーナス（900.0）を維持
                    score += 900.0
                    reasons.append("RUSSIA_PHASE_BOARD_COMPRESSION")
                elif reactive_pair_count >= 1:
                    # v336: reactive_pairs<3の場合、盤面圧縮ボーナスを抑制（800.0 → 400.0）
                    # 即時併合機会を優先するため、盤面圧縮ボーナスを半減
                    score += 400.0
                    reasons.append("RUSSIA_PHASE_BOARD_COMPRESSION")
                else:
                    # v333 baseline: reactive_pairs==0 の場合のボーナス（800.0）
                    # 盤面圧縮を優先しつつ、type 15保護を徹底
                    score += 800.0
                    reasons.append("RUSSIA_PHASE_BOARD_COMPRESSION")

        # ----- evaluation axis 8.8: reactive pairs >= 3 no merge penalty (v329: 高配置強力抑制版 - reactive_pairs>=3での高配置 runaway防止) -----
        # last_rollback_postmortemのfailure mode: "reactive_pairs>=3で即時併合不可続き、盤面圧迫悪化でゲームオーバー"
        # ワーストゲーム(score0636)終盤turns 56-62: reactive_pairs=3-5, merge_available=false, deadline_crossed=trueでmax_y=2.45→3.12に上昇
        # ワーストゲーム(score0725)終盤turns 61-62: reactive_pairs=3, merge_available=falseでmax_y=3.39→2.81の高配置が選ばれゲームオーバー
        # ベストゲーム(score3996)終盤turns 150-154: 即時併合機会を確実に捉えてtype 15を保護しつつ3996点を出している
        # v328の問題点: -3000.0固定ペナルティはheight_mult緩和(axis 2, 364, 379-382)や盤面圧縮ボーナス(axis 9.5)と競合し、高配置が選ばれる
        # v329の問題点: landing_y > 1 のペナルティ計算に符号誤りがあり、高配置ほどペナルティが弱くなっていた
        #   - landing_y <= 0: -3000.0ペナルティ維持
        #   - 0 < landing_y <= 1: -3000.0 + landing_y * 2000.0 (例: landing_y=0.5 -> -4000.0) ✓ 正常
        #   - landing_y > 1: -5000.0 + (landing_y - 1.0) * 2000.0 (例: landing_y=1.5 -> -6000.0, landing_y=2.0 -> -7000.0)
        # v329修正: landing_y > 1 の場合、(landing_y - 1.0) * 2000.0 を使用して高配置ほどペナルティを強化
        # これにより高配置になるほどペナルティが線形に増大し、height_mult緩和やボーナスを上回る強力な抑制を実現
        # last_rollback_postmortemのconstraint: "reactive_pairs>=3で即時併合がない場合、deadline_crossedに関わらず即時併合を最優先するペナルティを適用する（deadline_crossed条件を含める）"を遵守
        # 未活用情報：reactive_pairs>=3, merge_grade=="NO", landing_y (着地位置の高さ)
        # refs: tmp/state/last_rollback_postmortem.md, tmp/state/last_rollback_analysis.md, tmp/improve_brief.md, tmp/batch_summary.txt, advice.md, tmp/sandbox_files.md,
        #       game_history/20260324_045921_score0636.jsonl turns 56-62, game_history/20260324_043823_score0725.jsonl turns 61-62,
        #       game_history/20260324_044502_score3996.jsonl turns 150-154
        # Fixes rollback failure mode: reactive_pairs>=3での高配置 runaway（v328固定ペナルティ→v329動的ペナルティ→v329修正版）

        if reactive_pair_count >= 3 and merge_grade == "NO":
            # v501: restore height gradient — flat -4500 (v452) made all candidates
            # identical, eliminating height differentiation needed to avoid edge scatter.
            # Latest game T110-117: flat penalty → HIGH_LAYER at x=3.0 edge → 5/8 turns
            # zero delta. Gradient creates ~4000 swing (y=-2:-2500, y=2:-6500) that
            # preserves meaningful placement decision at ALL congestion levels.
            # Base -2500 at y<0 overwhelms additive bonuses (~800-1200) while still
            # penalizing low positions less severely to encourage low placement.
            score -= 2500.0 + max(0, landing_y) * 2000.0
            reasons.append("REACTIVE_PAIRS_NO_MERGE_PENALTY")

        # ----- evaluation axis 9: reactive pairs default (NEW: reactive_pairs fallback for "no action" situations) -----
        # batch_summaryでHEIGHT_CONTROLが22.8%選択(avg_score_delta=2.1)と過剰であり、reactive_pairsがある状況では「何もしない」HEIGHT_CONTROLではなく、
        # reactive_pairs活用で盤面圧縮を図る戦略的思考へ切り替える。
        # reactive_pairsがある場合、即時併合がない時のデフォルト選択をHEIGHT_CONTROLからREACTIVE_PAIRS_COMPRESSIONへ変更し、盤面圧縮を優先。
        # refs: tmp/batch_summary.txt, tmp/state/last_rollback_postmortem.md, game_history/20260313_231816_score0814.jsonl turns 54-57
        # v365: removed duplicated axis 9.5 v334 block — bonuses were applied twice.
        # The v337 block below is the correct version with russia_phase suppression.

        # ----- evaluation axis 9.5: current type stack merge priority (v337: ロシアフェーズでのaxis 9.5盤面圧縮ボーナス抑制版) -----
        # advice.md「同じタイプが続いて来たらそのタイプの上に置き、併合チャンスを優先する」を強化。
        # batch_summaryでHEIGHT_CONTROLが11.0%選択(avg_score_delta=0.0)と過剰であり、即時併合機会を取りこぼしていることを確認。
        # 盤面上の現在タイプの最も高い位置のピースに配置を優先し、即時併合機会を最大化。
        # v325: reactive_pairsがある場合のボーナスを削除し、即時併合機会を優先する戦略へ切り替え
        # v327: 危険ピース(danger_piece_count > 0)がある場合のボーナスを削除 - axis 9.2のペナルティを優先させ即時併合を最優先
        # v330: reactive_pairsがある場合の盤面圧縮ボーナスを削除 - 即時併合優先強化
        # reactive_pairsがある状況で盤面圧縮ボーナスを適用すると、axis 9.2の-4500.0ペナルティと競合し、即時併合機会を取りこぼす
        # v335: deadline_crossed && reactive_pair_count >= 1 && merge_grade == "NO"を条件に追加し、即時併合機会を最大化
        # v337: russia_phase && reactive_pair_count < 3 の場合、ボーナスを削除しaxis 8.7即時併合優先
        # danger_piece_count == 0 && reactive_pair_count == 0 の場合のみボーナスを適用し、即時併合機会を確実に優先
        # refs: tmp/improve_brief.md, tmp/batch_summary.txt, tmp/state/last_rollback_postmortem.md, tmp/state/last_rollback_analysis.md, advice.md,
        #       game_history/20260324_141236_score0731.jsonl turns 66-73, game_history/20260324_144026_score3171.jsonl turns 119-126
        # Fixes rollback failure mode: reactive_pairsがある状況での即時併合機会取りこぼし（axis 9.5 reactive_pairs条件追加）
        #         Fixes failure mode: ロシアフェーズでの即時併合機会取りこぼし（axis 9.5 russia_phase条件追加）

        if same_type_stack_top and merge_grade == "NO":
            stack_top_x = same_type_stack_top.get("x", 0)
            stack_top_y = same_type_stack_top.get("y", -10)

            # v285: v284 rollback failure mode潰し - reactive_pairs>=3時の戦略的配置ボーナス削除
            # danger_piece_count == 0 の場合、reactive_pairs>=3 && merge_grade=="NO"の戦略的配置ボーナス+1000.0を削除
            # ワーストゲームの「reactive_pairs>=3あるのに即時併合不可で戦略的配置を選び、max_y上昇」を回避するため
            # 即時併合機会を優先する戦略へ修正
            # v325: reactive_pairsがある場合の+800.0ボーナスを削除 - 即時併合機会を優先する戦略へ
            # reactive_pair_count >= 1 && merge_grade=="NO"の場合、+800.0ボーナスがaxis 9.2の-2500.0ペナルティと競合し、
            # 盤面圧縮（非併合配置）が選ばれてmax_y runawayでゲームオーバーする失敗モードを解消
            # v327: 危険ピース(danger_piece_count > 0)がある場合のボーナスを削除 - axis 9.2のペナルティを優先させ即時併合を最優先
            # v330: reactive_pairsがある場合の盤面圧縮ボーナスを削除 - 即時併合優先強化
            # v338: ロシアフェーズ && reactive_pair_count < 3 の場合、axis 9.5盤面圧縮ボーナスを完全削除
            # v337 failure: ロシアフェーズでreactive_pairs<3の場合、axis 9.5の盤面圧縮ボーナス（+300.0）がaxis 8.7の即時併合ボーナス（1200.0/1000.0）と競合し、即時併合機会を取りこぼしている
            # 即時併合機会を最大化し、盤面圧縮で2つ目のロシア育成スペースを確保する戦略へ切り替え
            if russia_phase and reactive_pair_count < 3:
                # ロシアフェーズでreactive_pairs<3の場合、axis 9.5のボーナスを完全に削除
                # 即時併合機会を最大化し、axis 8.7の即時併合ボーナスを最優先
                pass
            else:
                if danger_piece_count == 0 and reactive_pair_count == 0:
                    # v459: +300 bonus removed — axis 9.6b already provides proximity guidance
                    # toward same-type pieces (~120-540). The +300 was redundant additive
                    # noise that overrode height differentiation when combined with 9.6b's
                    # bonus (total 420-840 > typical height diffs ~200-450). avg_delta=0.8
                    # confirmed this axis produced negligible merges vs HEIGHT_CONTROL (2.8).
                    pass
            # v327: danger_piece_count > 0 の場合のボーナスブロックを削除 - axis 9.2のペナルティを優先
            # v330: reactive_pairs >= 1 の場合のボーナスブロックを追加 - axis 9.2のペナルティを優先
            # v337: ロシアフェーズ && reactive_pair_count < 3 の場合、ボーナスブロックを適用 - axis 8.7即時併合優先
            # reactive_pairsがある状況では、即時併合を最優先する戦略へ切り替え

            # 配置位置が盤面上の現在タイプのピースの上になる場合、ペナルティ軽減を強化
            # danger_piece_count == 0 && reactive_pair_count == 0 の場合のみ、ペナルティ軽減を適用
            # v325: reactive_pairsがある場合はペナルティ軽減ボーナスを削除 - 即時併合機会優先化
            # v327: 危険ピース(danger_piece_count > 0)がある場合のペナルティ軽減ボーナスも削除 - axis 9.2のペナルティを優先
            # v330: reactive_pairs >= 1 の場合のペナルティ軽減ボーナスも削除 - 即時併合優先強化
            # v337: ロシアフェーズ && reactive_pair_count < 3 の場合、ペナルティ軽減も削除 - axis 8.7即時併合優先
            landing_y = result.get("landing_y", 0)
            if not (russia_phase and reactive_pair_count < 3):
                if (
                    landing_y > stack_top_y
                    and danger_piece_count == 0
                    and reactive_pair_count == 0
                ):
                    horiz_dist = abs(x - stack_top_x)
                    if horiz_dist < 1.0:
                        # v325: reactive_pairsがない場合のみペナルティ軽減を適用
                        score += 100.0
                        if "SAME_TYPE_STACK" not in "_".join(reasons):
                            reasons.append("SAME_TYPE_STACK")

        # ----- v411: deadline-crossing NO-merge penalty (unutilized crosses_deadline) -----
        # analyze_board.py computes crosses_deadline per-candidate: whether this drop position
        # pushes the piece's top edge past the deadline (top_after_drop >= DEADLINE_Y=3.32).
        # When no merge is available, this is the worst possible placement: it increases
        # piece count AND pushes toward game-over with zero score benefit.
        # The height penalty (axis 2) penalizes high landing_y but doesn't account for
        # piece radius (top_y_after_drop = landing_y + radius). A piece at landing_y=2.8 with
        # radius=0.5 has top_y_after_drop=3.3, crossing deadline — but axis 2 penalty at y=2.8
        # is only moderate (~250 in HIGH phase). The crosses_deadline field captures this gap.
        # Penalty (-1200) is calibrated to override stacking/proximity bonuses (~200-900 at high pc)
        # without competing with merge bonuses (DIRECT=1200, NEAR=600). Fires only at
        # merge_grade=NO and not russia_phase (Russia growth intentionally crosses deadline).
        # refs: analyze_board.py L412 (crosses_deadline computation),
        #       game_history/20260330_144015_score0665.jsonl T60-61,
        #       game_history/20260330_143501_score0994.jsonl T74-75
        if (
            merge_grade == "NO"
            and not russia_phase
            and result.get("crosses_deadline", False)
        ):
            # v461: increase from -1200 to -2000 — account for accumulated additive bonus magnitudes
            # v411 calibrated at ~200-900 additive bonus range, but subsequent axes restored
            # (9.6b v453, 9.3 gate removed v457, 5.6 reduced v458) with congestion scaling
            # push total additive to ~1000+ at pc=30+. Worst game T70: x=3.0 selected
            # despite -1200 because stacking (~467) + proximity (~360) + growth (~96) bonuses
            # partially overcame penalty. Best game never triggers this (avoids crossing).
            # -2000 provides ~1000 margin above max possible additive combination, ensuring
            # deadline-crossing without merge is always deterred. Protected strategy (median
            # 12789) has no additive axes that could overcome this penalty.
            # Fixes failure mode: deadline-crossing NO-merge placement in congested endgame
            # refs: game_history/20260401_225426_score1094.jsonl T70-T71 (CROSSES_DEADLINE_NO_MERGE),
            #       game_history/20260401_223053_score3734.jsonl T137-T144 (no CROSSES_DEADLINE),
            #       strategy_versions/protected/protected_e6f534c37e28_median12789_strategy.py
            score -= 2000.0
            reasons.append("CROSSES_DEADLINE_NO_MERGE")

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
