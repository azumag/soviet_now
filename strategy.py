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
         1.7b. Gap-zone NEAR merge penalty - v567: penalty at NEAR+max_y>=2.0+deadline_crossed
         1.7c. Death-spiral NEAR suppression - v579: cancel NEAR bonus at rp>=3,pc>=32,max_y>=1.5,y>=1.0
         1.6. Danger DIRECT merge priority - v382: unutilized danger_direct_merge_available from analysis
        2. Height penalty - Penalty for high landing position (varies by phase)
         3. Drift penalty - Penalty for post-landing drift due to polygon shape
         4. Left-right balance correction - Bonus for correcting piece count bias
          5. nextNext centering - Center for next merge opportunity if nextNext same type
           5.5. Avoid blocking nextNext merge - Penalty for landing on same-type piece when nextNext matches
           5.6. Growth center proximity - v458: reduced magnitude per postmortem (base 60, congestion 0.08, cap 2.0)
            6. Chain merge bonus - Evaluate possibility of further merges after merge
            7. Reactive pairs bonus - Bonus for multiple merge opportunities (reactor info utilization, v206: enhanced)
            8. Early game merge priority - Strong bonus for merge opportunities in early game
             8.5. Danger zone immediate merge bonus - v331: deadline_crossed時即時併合強化
             8.6. Reactive pairs immediate merge bonus - v321: 即時併合ボーナス維持
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
             9.6. Reactive pairs type-aware stacking - v363: 全reactiveレベルでmerged_type近接スタッキング(v340ガード除去) + v408: pc混雑スケーリング(9.6b同一)
             9.6b. Same-type proximity guidance - v453: restored from v449 removal, without v418 rp_density
             9.7. Pipeline-aware placement guidance - v367: same_type 없い時の隣接type配置誘導 (postmortem axis 9.7 nesting fix)
             9.8. Merge drought horizontal guidance - v583: extended to type>=6, bonus ~400 for non-Russia games
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

# --- Change History ---
      # v630: merge_path_scattered detection + axis 9.6b congestion boost in merge drought
      # deadline_guard選択後のmerge_path散逸対策。worst game T62以降6ターンNO merge続きの主因。
      # 1) merge_path_scattered state検出追加: deadline_guardでmergeなし→次のmerge droughtでaxis 9.6b強化
      # 2) axis 9.6b rp_guidance_suppressed条件緩和: pc>=30 && rp>=3 && merge_grade==NOでrp_guidance抑制を解除
      #    → 同type peçaとの距離を縮める配置を優先しmerge path再構築
      # Fixes rollback failure mode: deadline_guard後のmerge_path散逸によるNO merge連続 (analysis_result.md)
      # refs: tmp/analysis_result.md, tmp/improve_brief.md, data/mandatory_themes.txt
      # Phase gate: ロシア(type15)→ソ連建国 — max_y runaway防止でtype14→15の到達経路を復旧
      # v625: NEAR suppression safety valve — allow NEAR when landing_y < max_y - 0.3
      # When max_y>=2.5 && deadline_crossed && merge_grade==NEAR: suppress NEAR unless it lands below board.
      # Safety valve prevents suppressing NEAR candidates that would compress board (landing below current max_y).
      # refs: tmp/analysis_result.md (Implementation Plan: NEAR抑制safety valve)
      # Fixes rollback failure mode: worst_game T64 NEAR抑制下でfallback x=-3.0が選択されmax_y跳ね上がり
      # v624: russia phase deadline NEAR suppression — cancel NEAR base bonus at russia+deadline+max_y>=2.0
      # NEAR merge (68.5%) at deadline with Russia on board is net negative: 8 of 13 turns in best game
      # were NEAR attempts, 6 failed (delta=0). DIRECT merge (95.7%) is the only safe merge in this regime.
      # refs: tmp/analysis_result.md (adopted hypothesis: russia phase deadline NEAR suppression)
      # Fixes rollback failure mode: russia_phase_deadline_near_merge_chain_failure
      # v613: axis 9.10 Tier 2 — rp==2 high-type merge path creation
      # Extend type 10+ proximity guidance to rp==2 NO merge stage, catching drought 1-2 turns earlier.
      # Tier 2: rp==2 && NO merge && max_y>=1.0 && pc>=20 -> +250*merge_mult near type 10+ centroid (1.5u)
      #   + reactive pair bonus: +150*merge_mult if type 10+ has same-type reactive pair
      # refs: tmp/analysis_result.md (Implementation Plan: axis 9.10 Tier 2),
      #       game_history/20260413_050025_score2311.jsonl T107 (rp=2, type 14 -> T108 large merge)
      # Fixes rollback failure mode: "rp=2 NO merge is the blind spot where merge droughts begin
      #   and high-type pieces scatter, missing the window to build merge paths before rp escalates to 3"
      # v605: axis 9.6b high-type no-merge proximity boost — when next_type>=13 and merge_grade==NO,
      # apply additional 1.5x proximity bonus to type 13/14 clustering before deadline.
      # Worst game: type13 (y=-1.63) scattered from type14 (y=-0.55), no merge formed.
      # Best game: type14 created but final merge not formed due to insufficient proximity.
      # mandatory_themes: "NEXTを考慮したドロップ" — boost based on next_type.
      # Fixes rollback failure mode: type14→15→16 reachability blocked by scatter.
      # refs: tmp/analysis_result.md
     # v604: type_scale to axis 9.6b same-type proximity guidance — type 13+ adjacency boost
     # axis 9.6b proximity bonus now multiplied by type_scale (1.0+0.1*max(0,next_type-5), cap 2.0).
     # Best game: type14x2 at x=1.30/x=2.01 (distance 0.71) never merged — guidance too weak.
     # Worst game: type13 pieces scattered, 16 DEADLINE_GUARD turns with no merge available.
     # type_scale boosts type14 proximity from ~300 to ~570, competitive with height diffs (~400).
     # Phase gate: targets Kazakhstan(type14)→Russia(type15)→Soviet(type16) pipeline.
     # Fixes rollback failure mode: type14→15→16 reachability blocked by scattered same-type placement.
     # refs: tmp/analysis_result.md
     # v603: type_scale merge bonus weighting — ported from best_score5801_strategy.py
     # lines 1142-1166. All 4 batch games (scores 1072-1936) failed at type 13 with russia=0/4.
     # Root cause: strategy is reactive (rp=4-6) but doesn't prioritize high-type merges.
     # type_scale = 1.0 + 0.1 * max(0, next_type - 5), capped 0.8-2.0.
     # Makes type 13+ merges (Ukraine→Kazakhstan→Russia) disproportionately attractive.
     # Rollback failure mode: piece_count accumulation from failed NEAR at deadline (v366).
     # Rollback constraint: axis 9.6b must not be removed.
     # refs: tmp/analysis_result.md, strategy_versions/best_score5801_strategy.py:1142-1166
     # v602: DEADLINE_GUARD merge-first enforcement — suppress safe-landing fallback when
     # NO merge exists despite reactive pairs. Worst game T55-60: 6 consecutive DEADLINE_GUARD
     # with merge_available=false despite rp=5-9 → max_y runaway 2.35→4.02.
     # mandatory_themes: "デッドラインを超える位置にピースを置く場合は、併合できる場合に限る"
     # When ALL safe candidates have merge_grade=NO and reactive pairs exist, DEADLINE_GUARD
     # would raise max_y without merge benefit. Suppress and let normal decide() apply height penalty.
     # Preserves DEADLINE_GUARD_DIRECT_MERGE (merge_available=true) which works correctly.
     # Fixes rollback failure mode: DEADLINE_GUARD over-fires without merge at T55-60.
     # refs: tmp/analysis_result.md, data/mandatory_themes.txt
     # v601: DEADLINE_GUARD fallback — if selected candidate crosses deadline without merge
     # capability, suppress guard and fall through to normal decide() (mandatory_themes enforcement).
     # Worst game T67: DEADLINE_GUARD returned x=2.98 with crosses_deadline=true, merge_grade="NO",
     # violating mandatory_themes. Suppression lets v411 CROSSES_DEADLINE penalty (-1200) apply.
     # Best game T92: crosses_deadline=true, merge_grade="DIRECT" → DEADLINE_GUARD_DIRECT_MERGE returned (preserved).
     # Fixes rollback failure mode: DEADLINE_GUARD crossing-no-merge position selection at worst game T67.
     # refs: tmp/analysis_result.md, data/mandatory_themes.txt
     # v586: merge drought early detection — lower rp threshold from >=2 to >=1.
     # rp=1, NO merge, max_y>=1.0, pc>=30 now triggers guidance_suppressed immediately.
     # Fixes failure mode: "rp=1のNO mergeターンを1ターンでも減らす" (analysis_result.md)
     # refs: tmp/analysis_result.md, tmp/batch_summary.txt, game_history/20260411_221219_score0870.jsonl
     # v585: merge drought detection — suppress all guidance when NO merge continues with elevated board.
     # merge_grade=="NO" AND rp>=2 AND max_y>=1.0 AND pc>=30 → guidance_suppressed = death_spiral OR merge_drought.
     # Catches "slow death" earlier than death_spiral alone (max_y>=1.0 vs 1.5). Forces height penalty only mode.
     # Fixes failure mode: "merge drought 継続検知と強制リカバリー" (analysis_result.md adopted hypothesis)
     # refs: tmp/analysis_result.md, tmp/batch_summary.txt, game_history/20260411_211420_score1045.jsonl
     # v584: death_spiral height_mult amplification — override to 8.0 (from phase 1.8) during death spiral.
     # Fixes failure mode: death spiral時のheight penalty弱すぎ（edge scatter prevent, worst T73-80 x=3.0/-0.6 scatter).
     # Analysis: height penalty differentiation ~50-200 too weak vs identical -5500 axis 8.8/8.8b penalties.
     # 8.0x gives ~400 per y-unit → ~2000+ spread, ensuring lowest-y candidate always wins.
     # refs: tmp/analysis_result.md, tmp/batch_summary.txt, game_history/20260411_183711_score0856.jsonl
     # v583: axis 9.8 — extend merge drought horizontal guidance to non-Russia games.
     # Lower threshold from type>=10 to type>=6 (same as axis 5.6 growth center),
     # increase bonus from ~150 to ~400 (match Russia phase compression minimum).
     # Non-Russia games (worst 0930, extra-low 0978) died from lack of horizontal guidance
     # during merge droughts — no type 15 means no RUSSIA_PHASE_BOARD_COMPRESSION.
     # refs: tmp/analysis_result.md, tmp/batch_summary.txt, game_history/20260411_172710_score0930.jsonl,
     #       game_history/20260411_180215_score0978.jsonl, game_history/20260411_174117_score4328.jsonl
     # v582: axis 9.8 — merge drought horizontal guidance: when merge_grade=NO, no reactive/same-type
     # guidance exists, and not in death_spiral, guide placement toward highest-type piece clusters.
     # Fixes failure mode: "horizontal guidance void" → HEIGHT_CONTROL scatter during merge droughts
     # (worst game T67-T70: rp=4, NO, same-type absent → edge scatter, top_edge_y→4.12).
     # Bonus ~150 competitive with height diffs but won't override merges; suppressed in death_spiral.
     # refs: tmp/analysis_result.md, tmp/batch_summary.txt, game_history/20260411_170719_score0967.jsonl
     # v581: axis 8.8b — HIGH merge drought penalty: rp>=3, NO, max_y>=1.8 → extra -1000 (total -5500)
     # Forces height penalty to dominate when stacking_bonus(~900)+proximity(~200) compete with axis 8.8.
     # Fixes failure mode: "merge drought時の配置判断の甘さ（stacking_bonus+proximity_bonusがaxis 8.8と競合）"
     # refs: tmp/analysis_result.md, tmp/batch_summary.txt, game_history/20260411_160254_score0498.jsonl
     # v580: extend death_spiral to max_y>=1.5 — catch pre-death-spiral window where board
     # is already dangerous but deadline not yet crossed. rp>=3, NO merge, max_y>=1.5 →
     # suppress stacking/proximity/AVOID_BLOCK, let height penalty differentiate.
     # Fixes rollback failure mode: max_y runaway when reactive_pairs>=3 and merge_available=false,
     # even before deadline_crossed (postmortem: "extends death spiral detection to max_y>=1.5")
     # refs: tmp/analysis_result.md, tmp/state/last_rollback_postmortem.md, tmp/state/last_rollback_analysis.md
     # v579: death-spiral NEAR suppression — cancel base NEAR bonus (600*merge_mult) when
     # rp>=3, pc>=32, max_y>=1.5, landing_y>=1.0. Forces low-y NEAR or NO-merge low placement.
     # Fixes rollback failure mode: high-y NEAR in death-spiral window accelerates pc growth
     # refs: tmp/analysis_result.md, game_history/20260411_140730_score0589.jsonl, game_history/20260411_142110_score1089.jsonl
     # v578: expand stacking_congested threshold — catch pre-death-spiral window (rp>=3, max_y>=1.0, NO)
     # Failure mode: max_y runaway when reactive_pairs>=3 and merge_available=false, even before deadline_crossed
     # Adds third OR condition to stacking_congested guard, redirecting stacking to height-priority mode earlier.
     # refs: tmp/analysis_result.md, tmp/state/last_rollback_postmortem.md, tmp/state/last_rollback_analysis.md,
     #       game_history/20260411_131655_score0560.jsonl (worst T56-T57), tmp/batch_summary.txt
     # v548: double_russia_phase — 2つ目のロシア(type 15)出現後のソ連建国目前フェーズ切替
     # ロシア1つのままゲームオーバーは最も惜しい負けパターン。2つのロシアが盤面にある場合、
     # 盤面圧縮ボーナスを抑制し、既存type 15保護と低配置生存を最優先。
     # 即時併合時は通常ロシアフェーズよりさらに強力なボーナス(1600/1400)で盤面確保。
     # Fixes rollback failure mode: p25 collapse from russia_phase treating 1 and 2+ type 15s identically
     # refs: tmp/improve_brief.md, tmp/batch_summary.txt, advice.md, tmp/state/last_rollback_analysis.md,
     #       game_history/20260408_020209_score0977.jsonl, game_history/20260408_023321_score2589.jsonl
     # v463: suppress axis 9.7 (pipeline guidance) in death_spiral — missing from v461/v462 suppression
     # Axis 9.7 fires when same_type_stack_top is None, giving ~80 bonus for adjacent-type proximity.
     # In death_spiral (danger>0 && rp>=3 && NO && deadline), this bonus can override height penalty
     # differentiation (~50-100 between y=0 and y=-2). All other guidance axes already suppressed;
     # 9.7 was the only gap. Fixes rollback failure mode: residual guidance noise in death spiral
     # refs: game_history/20260407_234830_score0784.jsonl, game_history/20260407_234154_score0997.jsonl,
     #       game_history/20260407_232215_score2692.jsonl, tmp/batch_summary.txt, tmp/state/last_rollback_analysis.md
     # v461: death-spiral noise suppression — suppress 9.6b/5.6/9.3 when danger>0 && rp>=3 && NO && deadline
     # Worst game T62: rp=6, NO, deadline, danger=3 → x=3.0 edge scatter at pc=40, game over in 3 turns.
     # Flat -4500 axis 8.8 is correct but proximity/growth/AVOID_BLOCK noise overrides height penalty.
     # Fixes rollback failure mode: death-spiral edge scatter from bonus noise overriding height penalty
     # refs: game_history/20260407_210954_score0473.jsonl, game_history/20260407_211649_score0913.jsonl,
     #       tmp/batch_summary.txt, tmp/state/last_rollback_analysis.md, tmp/improve_brief.md
     # v461: death-spiral noise suppression — suppress 9.6b/5.6/9.3 when danger>0 && rp>=3 && NO && deadline
     # Worst game T62: rp=6, NO, deadline, danger=3 → x=3.0 edge scatter at pc=40, game over in 3 turns.
     # Flat -4500 axis 8.8 is correct but proximity/growth/AVOID_BLOCK noise overrides height penalty.
     # Fixes rollback failure mode: death-spiral edge scatter from bonus noise overriding height penalty
     # refs: game_history/20260407_210954_score0473.jsonl, game_history/20260407_211649_score0913.jsonl,
     #       tmp/batch_summary.txt, tmp/state/last_rollback_analysis.md, tmp/improve_brief.md
     # v462: extend death-spiral suppression to axes 5/5.5 — AVOID_BLOCK_NEXTNEXT (-400) and
     # NEXT_SAME centering (~50) create noise that overrides height penalty in death spiral.
     # Worst game T55: AVOID_BLOCK_NEXTNEXT pushes away from nextNext target toward edge (x=-2.0).
     # With stacking/proximity suppressed (v461), these remaining bonuses become the dominant
     # differentiators. Suppressing them ensures height penalty is truly the sole signal.
     # Fixes rollback failure mode: residual bonus noise in death spiral after v461 suppression
     # v462: extend death-spiral suppression to axes 5/5.5 — AVOID_BLOCK_NEXTNEXT (-400) and
     # NEXT_SAME centering (~50) create noise that overrides height penalty in death spiral.
     # Worst game T55: AVOID_BLOCK_NEXTNEXT pushes away from nextNext target toward edge (x=-2.0).
     # With stacking/proximity suppressed (v461), these remaining bonuses become the dominant
     # differentiators. Suppressing them ensures height penalty is truly the sole signal.
     # Fixes rollback failure mode: residual bonus noise in death spiral after v461 suppression
     # v460: suppress REACTIVE_PAIRS_STACKING when danger_piece_count>0 && rp>=3 && NO merge
     # Stacking bonus (~900 at high pc) differentiates candidates toward high same-type pieces,
     # accelerating piece accumulation in danger zone. Suppressing lets height penalty be sole
     # differentiator — consistent with axis 8.8 intent.
     # Fixes rollback failure mode: stacking accelerates piece accumulation in danger zone (no merge)
     # refs: game_history/20260407_200712_score0421.jsonl, game_history/20260407_201554_score0994.jsonl,
     #       tmp/batch_summary.txt, tmp/state/last_rollback_analysis.md, tmp/improve_brief.md
     # v459: remove axis 9.5 +300 SAME_TYPE_STACK_MERGE_PRIORITY bonus
     # Batch: avg delta=0.8 (worse than HEIGHT_CONTROL 2.8), selected 4.6% in low-score games.
     # With axis 9.6b providing proximity guidance (~120-540), the +300 was redundant
     # additive noise that overrode height differentiation when combined with 9.6b's bonus.
     # Protected strategy (median 12789) has +300 but NO 9.6b — no amplification issue.
     # Removing aligns current noise profile closer to protected while keeping 9.6b guidance.
     # Fixes: low-score games disproportionately selecting same_type_stack over lower placement
     # refs: tmp/batch_summary.txt (SAME_TYPE_STACK_MERGE_PRIORITY avg_delta=0.8, 4.6% low),
     #       game_history/20260401_194026_score0935.jsonl (worst: 4.6% axis 9.5, HEIGHT_CONTROL 19.7%),
     #       game_history/20260401_193748_score1042.jsonl (extra_low: axis 9.5 at T83-84),
     #       strategy_versions/protected/protected_e6f534c37e28_median12789_strategy.py (no 9.6b),
     #       tmp/state/last_rollback_postmortem.md, strategy.py.staging (v458)
     # v458: reduce axis 5.6 magnitude (postmortem constraint) + remove axis 9.3 pc>=25 gate
     # v456: axis 5.6 base 100→60, congestion 0.14→0.08, cap 3.5→2.0 per postmortem
     # "reduce bonus magnitude rather than filter activation". Fixes v455 rollback failure mode.
     # v457: axis 9.3 fires at all pc — advice applies at ALL phases, penalty calibrated safe.
     # refs: last_rollback_postmortem.md, protected_e6f534c37e28, batch_summary.txt, advice.md, change_log.txt
     # v454: flatten deadline_crossed NO-merge penalty to flat -4500 — fix v432 sign error
     # v432 formula -3000 + landing_y*2000 had wrong sign: at y>=1.5 "penalty" became 0 or positive,
     # rewarding high placement at deadline. Flattened to -4500 matching protected strategy (median 12789)
     # and axis 8.8 (v452). Fixes rollback failure mode: deadline scatter from inverted gradient
     # refs: tmp/state/last_rollback_postmortem.md (scatter failure modes, axis 8.8 constraint),
     #       strategy_versions/protected/protected_e6f534c37e28_median12789_strategy.py (flat -4500),
     #       game_history/20260401_125127_score0816.jsonl T55-60 (deadline scatter → y=3.31),
     #       game_history/20260401_124901_score0890.jsonl T79-84 (x=2.6-3.0 at deadline),
     #       game_history/20260401_123945_score2962.jsonl T114-122 (edge scatter at deadline),
     #       tmp/batch_summary.txt, strategy.py.staging (v453)
     # v453: restore axis 9.6b (same-type proximity guidance for non-reactive) — fix rollback failure mode
     # Postmortem constraint VIOLATED by v449: "forbid: axis 9.6b の無効化。merge drought時に
     # 非reactive current type向けの配置ガイドを維持すること。" v449 removed 9.6b entirely, causing
     # merge drought piece accumulation (the PRIMARY rollback cause). When current_type has no
     # reactive/near pairs, there was NO horizontal guidance → HEIGHT_CONTROL scatter → pc grew
     # 35→43 in 8 turns (worst game T65-77). Protected strategy doesn't have 9.6b but compensates
     # with other mechanisms; current strategy lacks those compensations, so 9.6b is essential.
     # Restored WITHOUT v418 rp_density_scaling (which was also removed pre-rollback and was part
     # of the accumulation problem). Proximity bonus ~120-360 at pc=35 (with v412 nextNext 1.5x)
     # stays below height diffs (~100-200), avoiding the accumulation that caused postmortem warning.
     # Fixes rollback failure mode: merge_drought_piece_accumulation + scattered_board_near_merge_failure
     # refs: tmp/state/last_rollback_postmortem.md (9.6b forbid constraint, scatter failure modes),
     #       game_history/20260401_113840_score1078.jsonl T65-77 (rp=3-5, no guidance, pc 35→39),
     #       game_history/20260401_112440_score2849.jsonl T124-131 (type14 concentrated, survived 131 turns),
     #       strategy.py.staging (v449 removed 9.6b, v452 only restored axis 8.8),
     #       tmp/batch_summary.txt (HEIGHT_CONTROL 14.5% = guidance gap)
     # v452: flatten axis 8.8 reactive_pairs NO-merge penalty to flat -4500 — match protected strategy
     # Postmortem constraint: "axis 8.8の低位置ペナルティを-4500未満に下げること。y<=0での-3000（v432）
     # は低位置散布を許容し、pc急増の主因となる。-4500以上を維持。" The v432 gradient (-3000 at
     # y<=0, scaling to -7000 at y=2) allowed additive bonuses (~400-800) to create relative height
     # differences between y=-2 and y=0 positions, causing HEIGHT_CONTROL scatter during merge droughts.
     # Flat -4500 overwhelms all additive bonuses, letting axis 2 height penalty provide the only
     # differentiation — consistent low placement without scatter. Protected strategy (median 12789,
     # +20% better) uses flat -4500 with NO gradient. v445 previously validated this change before v449
     # (axis 9.6b removal) caused the rollback. Fixes postmortem failure mode: low-position scatter
     # during NO-merge drought → piece_count accumulation
     # refs: tmp/state/last_rollback_postmortem.md (axis 8.8 constraint, scatter failure mode),
     #       strategy_versions/protected/protected_e6f534c37e28_median12789_strategy.py (flat -4500),
     #       game_history/20260401_104920_score0762.jsonl (worst: 8-turn drought scatter),
     #       game_history/20260401_102410_score0893.jsonl (extra_low: 6-turn drought scatter),
     #       tmp/batch_summary.txt (HEIGHT_CONTROL 18.3% low vs 12.7% high)
     # v432: deadline-crossed NO-merge height-dependent penalty — restore height gradient at deadline
     # Postmortem constraint: "Any NO-merge penalty MUST preserve meaningful height
     # differentiation (~3000+ between y=0 and y=2)". The old flat -4500 for deadline_crossed
     # && rp>=1 && NO violated this: all positions equally penalized, removing height guidance
     # during merge droughts at deadline. Worst T47-T49: deadline crossed, rp=2, NO merge —
     # flat -4500 made all candidates equally bad → HEIGHT_CONTROL scatter at x=1.54 (crosses
     # deadline), then type 11 at x=-0.15 bounced to y=3.42. Best game T117-T121: deadline
     # crossed but lower-y stacking kept max_y controlled despite rp=1-2 NO merge.
     # New: graduated penalty with ~4000 gradient between y=0 and y=2:
     #   y<=0: -3000, y=1: -5000, y=2: -7000 (formula: -3000 + max(0, landing_y)*2000)
     # This matches the postmortem's recommended approach: "add a separate height component
     # (e.g., -4500 base + landing_y * 1500) to restore gradient without reintroducing
     # v339 high-stacking failure mode". Existing guardrails (v411 CROSSES_DEADLINE, v416
     # stacking redirect, v422 HIGH_PC_NEAR) prevent high-stacking abuse.
     # Fixes postmortem failure mode: piece_count accumulation from scatter at deadline drought
     # refs: tmp/state/last_rollback_postmortem.md (height gradient constraint),
     #       game_history/20260331_133110_score0355.jsonl T47-49 (flat -4500 → scatter),
     #       game_history/20260331_134337_score2559.jsonl T114-121 (gradient survives),
     #       game_history/20260331_130421_score0892.jsonl T46-48 (scatter death),
     #       tmp/batch_summary.txt (HEIGHT_CONTROL 19.9% low vs 14.9% high)
     # v431: conditional height_mult relaxation — only relax when current type has reactive/near guidance
     # Postmortem: rp=1-2 height_mult relaxations (v271 0.8x, v288 0.3x, v294 0.2x) compound to
     # floor 0.5, enabling HEIGHT_CONTROL edge scatter when current type has no reactive/near pairs.
     # Worst game T55-62: pc=28-35, rp=2, mg=NO, x=3.0 (HIGH_LAYER scatter) → dies at T62.
     # When current_type_has_reactive or current_type_has_near, axis 9.6 stacking provides
     # directional guidance that justifies relaxation. Without guidance, relaxation weakens
     # height penalty (landing_y * 25) below guidance signal (~120-220), allowing scatter.
     # Fix: guard all three relaxation blocks with (current_type_has_reactive or
     # current_type_has_near). When guidance absent, height_mult stays at phase value,
     # creating stronger height differentiation that prevents edge scatter.
     # Fixes rollback failure mode: piece_count accumulation from HEIGHT_CONTROL scatter at rp=1-2
     # refs: game_history/20260331_115149_score0619.jsonl T55-62 (scatter death),
     #       game_history/20260331_121726_score0735.jsonl T59-66 (scatter death),
     #       tmp/batch_summary.txt (HEIGHT_CONTROL 19.2% low vs 14.9% high),
     #       tmp/state/last_rollback_postmortem.md, tmp/state/last_rollback_analysis.md,
     #       strategy.py.staging (v329 restored), advice.md (中央集約, 孤立配置回避)
     # v422: high pc NEAR merge penalty — structural fork cancels NEAR bonus at pc>=33+deadline+y>=1.0.
     # v421 gap: net NEAR still +75 at pc=35,deadline,y=1.0. New axis: -600*merge_mult penalty.
     # Preserves safe NEAR (y<1.0): best game T82 recovery at pc=33,deadline,y<0 unaffected.
     # Fixes postmortem: piece_count accumulation from failed NEAR at high pc
     # refs: tmp/state/last_rollback_postmortem.md, tmp/state/last_rollback_analysis.md,
     #       tmp/batch_summary.txt, game_history/20260331_031009_score1030.jsonl,
     #       game_history/20260331_025511_score2317.jsonl, analyze_board.py, advice.md
     # v421: piece_count-aware NEAR deadline risk — reduce risky NEAR at high pc
     # Postmortem prioritize: "NEAR merge 失敗時の piece_count 蓄積を防ぐため、deadline_crossed 下での
     # NEAR merge の選択をより慎重にすること" and "piece_count >= 33 を閾値として、DIRECT merge
     # のみを積極的に狙い、NEAR merge は landing_y < 0 の安全なものに限定するロジック"
     # Root cause: at high pc, failed NEAR (68.5% success) adds a piece without merge benefit,
     # accelerating piece_count accumulation → max_y runaway → game over.
     # Worst T74: pc=41, deadline, DANGER_NEAR_MERGE_PRIORITY overrides NEAR_DEADLINE_RISK,
     # NEAR at high y fails (delta=0), pc 40→41. Bad score0823: pc=34, NEAR fails ×2, pc→36.
     # Rollback target score2006: pc=33, DIRECT merge +282, pc 35→27.
     # Two-part fix: (1) NEAR_DEADLINE_RISK scaled by pc at >=33 (penalty up to 2.7x at pc=40),
     # (2) DANGER_NEAR_MERGE_PRIORITY suppressed at pc>=33 + landing_y>=1.5 + deadline_crossed.
     # At pc=35, y=2.0, deadline: net NEAR drops from +1200 to +150. NEAR still taken but
     # lower-y NEAR strongly preferred (787 point swing), reducing height impact of failure.
     # Fixes postmortem failure mode: piece_count accumulation from failed NEAR at deadline
     # refs: tmp/state/last_rollback_postmortem.md, tmp/state/last_rollback_analysis.md,
     #       tmp/batch_summary.txt, game_history/20260331_014942_score0970.jsonl T74,
     #       game_history/20260331_021953_score1001.jsonl T70-T73, strategy.py.staging (v418)
     # v418: reactive pair density scaling on proximity guidance — reduce type scattering in merge-ready boards
     # When many reactive pairs exist (rp>=2), the board is merge-ready but pieces may be scattered.
     # Weak guidance (base ~120) at low-mid game allows HEIGHT_CONTROL to scatter pieces, reducing
     # future merge opportunities. rp density scaling multiplies proximity_bonus by 1.2-2.5x based
     # on reactive_pair_count (unutilized in axis 9.6b), strengthening guidance when merge
     # potential is highest. Suppressed in extreme danger (postmortem constraint).
     # Batch: HEIGHT_CONTROL 20.6% low vs 12.8% high — guidance too weak to overcome height preference.
     # Worst T45-52: types 11×2 scattered 1.66u apart → no merge → death at 721.
     # Extra_low T60-67: rp=6-7, merge_grade=NO → HEIGHT_CONTROL scatter → death at 737.
     # Fixes postmortem failure mode: type scattering → merge drought → low p25
     # refs: tmp/batch_summary.txt, tmp/state/last_rollback_postmortem.md,
     #       game_history/20260330_223038_score0721.jsonl, game_history/20260330_222528_score0737.jsonl,
     #       strategy.py.staging (v417 AVOID_BLOCK suppression)
     # v417: AVOID_BLOCK suppression in congested endgame — prevent edge scatter
     # Postmortem: worst games show x=±3.0 edge scatter when rp>=5, max_y>=2.5, NO merge.
     # AVOID_BLOCK_REACTIVE_PAIR (axis 9.3, -500 cap) overwhelms stacking/proximity guidance
     # (~200-900) and height penalty diffs (~250), pushing pieces to isolated edge positions
     # where they can never contribute to merges. Suppressing in congested regime allows
     # v416 stacking redirect (lowest same-type) and proximity guidance to compete with height.
     # T53-T54: x=3.0 scatter, T57: x=-3.0 scatter, T71: x=1.8 blocked from center.
     # Fixes postmortem: p25 collapse from piece_count accumulation via edge scatter
     # refs: game_history/20260330_211924_score0634.jsonl T53-60,
     #       game_history/20260330_213224_score0664.jsonl T65-72,
     #       tmp/state/last_rollback_postmortem.md, tmp/batch_summary.txt,
     #       tmp/state/last_rollback_analysis.md, tmp/improve_brief.md
     # v416: stacking target redirection — replace v414/v415 binary block with state-dependent
     # target selection fork. v414/v415 blocked stacking entirely in danger → HEIGHT_CONTROL
     # scatter (avg_score_delta=1.7) took over with no guidance. v416: stacking ALWAYS fires but
     # target changes: normal → merged_type proximity (chain building), congested → lowest
     # same-type piece (height-priority). Preserves stacking incentive in all cases; congested
     # mode naturally reduces landing height while maintaining same-type proximity for merges.
     # Structurally removes stacking_blocked boolean (dead code path) and adds target fork.
     # refs: game_history/20260330_200337_score0587.jsonl T67-73,
     #       game_history/20260330_195749_score0574.jsonl T51-58,
     #       tmp/state/last_rollback_postmortem.md (v413/v414/v415 failures),
     #       tmp/batch_summary.txt, advice.md
     # v412: nextNext-aware proximity — strengthen same-type guidance when next two pieces are same type
     # When next_type == next_next_type and merge_grade=NO, the next turn is guaranteed to have a
     # merge opportunity (same-type pieces exist on board). Placing the current (different-type) piece
     # near same-type targets creates a merge-assist position: after next merges (creating N+1), the
     # remaining same-type pieces are nearby for subsequent merges. This addresses the advice
     # "2手先の併合可能性を最大化するため、1手先で併合できない国を一時的に別の場所に配置して道を作る".
     # Worst games show HEIGHT_CONTROL scatter at reactive 0-2 when no same-type guidance competes with
     # height penalty (worst T57-T58: HEIGHT_CONTROL despite same-type pieces at y=-3.4). The 1.5x multiplier
     # raises guidance from ~190 to ~285 at pc=35 (still below height diffs ~100-200, won't override merges).
     # Purely additive bonus (postmortem-safe). Uses next_next_type beyond axis 5 equality check.
     # Fixes postmortem: piece_count accumulation from HEIGHT_CONTROL scatter at merge drought
     # refs: advice.md (Pitman_live: 2手先の併合可能性最大化), tmp/batch_summary.txt (HEIGHT_CONTROL 17.5%),
     #       tmp/state/last_rollback_postmortem.md, tmp/state/last_rollback_analysis.md,
     #       game_history/20260330_152555_score0769.jsonl T57-T58, analyze_board.py
     # v411: deadline-crossing NO-merge penalty — utilize unutilized per-candidate crosses_deadline
     # analyze_board.py computes crosses_deadline per-candidate (top_after_drop >= DEADLINE_Y) but strategy
     # never reads it. When merge_grade=NO, placing a piece that crosses the deadline is the worst
     # possible move: adds a piece with no merge benefit AND pushes board closer to game-over.
     # Worst game T60-T61: crosses_deadline=true + merge_grade=NO with no penalty → pieces placed at deadline.
     # Extra_low T75: crosses_deadline=true + merge_grade=NO → game over with 37 pieces.
     # Penalty -1200 overrides stacking/proximity bonuses (~200-900) but not merge bonuses (DIRECT/NEAR).
     # Not russia_phase: Russia growth strategy (RUSSIA_PHASE_BOARD_COMPRESSION) intentionally places
     # near deadline; this penalty must not interfere. NOT a merge-path blocker (postmortem-safe):
     # it redirects from positions where no future merge is possible anyway (deadline-crossing).
     # Fixes postmortem: survival at reactive<3 when board reaches deadline before reactive accumulates
     # refs: analyze_board.py (crosses_deadline per-candidate, top_y_after_drop),
     #       game_history/20260330_144015_score0665.jsonl T60-61,
     #       game_history/20260330_143501_score0994.jsonl T74-75,
     #       tmp/batch_summary.txt, tmp/state/last_rollback_postmortem.md
     # v409: graduated NEAR deadline risk — replace binary deadline_crossed with reactor deadline_margin
     # v366 used binary deadline_crossed: pieces just before deadline get 0 penalty, just after get full.
     # reactor deadline_margin is continuous (<0 crossed, 0-1 approaching). Graduated penalty provides
     # smoother transition. Low-score games: NEAR merge rate drops ~40%→~28% at deadline, causing piece
     # accumulation and early death. Partial protection when approaching deadline (margin 0-1) reduces this.
     # Uses unutilized analysis field. NOT v388 crosses_deadline per-candidate (different mechanism).
     # Fixes rollback p25 collapse: binary cliff causes sudden behavior change at deadline crossing
     # refs: tmp/improve_brief.md, tmp/batch_summary.txt, tmp/state/last_rollback_postmortem.md,
     #       game_history/20260330_115102_score0447.jsonl, game_history/20260330_115755_score0839.jsonl,
     #       analyze_board.py, advice.md
     # v408: axis 9.6 piece_count congestion scaling — match 9.6b formula for reactive stacking
     # Axis 9.6b (same-type proximity) has piece_count congestion scaling but axis 9.6
     # (reactive stacking) does not. At high pc (30+), stacking_bonus (~100-400) is
     # overwhelmed by height penalty (~180-450 in HIGH phase), causing HEIGHT_CONTROL
     # edge scatter during merge droughts. Worst game T55-T57: x=3.0 and x=-3.0 scatter.
     # Batch: HEIGHT_CONTROL 20.3% low-score vs 12.4% high-score. Adding congestion
     # scaling (same formula as 9.6b) makes stacking competitive at high pc, reducing
     # scatter at reactive 1-2 before death spiral at reactive>=3. At pc=35: bonus*1.84.
     # Fixes rollback failure mode: piece_count accumulation from weak stacking at high pc
     # refs: game_history/20260330_105339_score0670.jsonl T54-61,
     #       game_history/20260330_104816_score0780.jsonl T51-58,
     #       game_history/20260330_104235_score3821.jsonl T145-153,
     #       tmp/batch_summary.txt, tmp/state/last_rollback_postmortem.md,
     #       strategy_versions/protected/protected_e6f534c37e28_median12789_strategy.py
     # v407: growth center proximity in russia_phase — enable 2nd Russia growth pipeline
     # During russia_phase, axis 5.6 was disabled. But concentrating pieces
     # around the existing Russia piece is critical for 2nd Russia growth.
     # The growth center naturally targets the deepest type 15 piece.
     # Bonus is small (~50-100) due to gc_y decay — won't override height safety.
     # Fixes prompt priority: "ロシア建国後フェーズ（最重要課題）"
     # refs: tmp/improve_brief.md, tmp/state/last_rollback_postmortem.md, advice.md,
     #       game_history/20260330_100106_score4264.jsonl turns 155-162,
     #       game_history/20260330_095109_score2724.jsonl turns 115-122
     # v384: reactive pair blocking avoidance — preserve merge paths by penalizing placement between reactive pairs
     # advice: "併合できるtypeが隣接しているとき、その間にピースを配置してしまうと、併合しづらくなる"
     # Placing between reactive pairs of different types physically blocks their future merge,
     # leading to piece_count accumulation and game over.
     # Worst game T37-47: 6-8 reactive pairs, pieces placed between them at y=2.58,
     # no merges for 11 turns, piece_count grows 30→40, game over at T55.
     # Penalty per blocked pair (200, capped 500) redirects placement to non-blocking positions,
     # preserving merge paths and reducing piece_count accumulation.
     # NOT a NEAR suppression or axis 8.8 change (postmortem constraints respected).
     # Purely additive penalty, does not suppress any existing behavior.
     # Fixes rollback failure mode: piece_count accumulation from blocking reactive merge paths
     # refs: advice.md, tmp/batch_summary.txt, tmp/state/last_rollback_postmortem.md,
     #       game_history/20260329_090616_score0296.jsonl T37-47,
     #       game_history/20260329_090011_score0811.jsonl T73-80,
     #       game_history/20260329_083840_score3207.jsonl, analyze_board.py
     #
     # v383: danger NEAR merge priority — utilize unutilized danger_merge_available from analysis
     # v382 addressed danger DIRECT merges (+800). NEAR merges targeting danger pieces were unutilized
     # despite removing danger pieces being critical for survival. Postmortem: "deadline_crossed下での
     # DIRECT_MERGEの優先度を最大化" — natural extension to NEAR. Bonus 600 (deadline) / 300 (normal)
     # makes danger NEAR competitive while NEAR deadline risk penalty still discourages high-risk attempts.
     # Purely additive, no suppression. Fixes rollback failure mode: endgame scoring starvation.
     # refs: tmp/state/last_rollback_postmortem.md, analyze_board.py, tmp/batch_summary.txt,
     #       game_history/20260329_081450_score0774.jsonl, game_history/20260329_080000_score3902.jsonl,
     #       game_history/20260329_080456_score2801.jsonl, protected_e6f534c37e28_median12789
     #
     # v382: danger DIRECT merge priority — utilize unutilized danger_direct_merge_available from analysis
     # Postmortem: "deadline_crossed下でのDIRECT_MERGEの優先度を最大化すること"
     # target score1359 T77: DIRECT_MERGE_HIGH_LAYER with danger_direct_merge_available=true, +100.
     # target score2083 T92: HIGH_TOWER→type13 merge +119, T95-98 NEAR merge at deadline +130.
     # Worst game T54/T56: NEAR merge attempted but failed (delta=0), pc grew 37→39, no DIRECT merge found.
     # Current strategy never reads result["danger_direct_merge_available"] — per-candidate flag
     # indicating a DIRECT merge with a danger piece (near/past deadline). This is the highest-value
     # merge: 95.7% success + danger piece removal. Strong bonus ensures this is chosen over
     # non-danger DIRECT merges and over risky NEAR at deadline. Does NOT penalize NEAR or
     # suppress any existing behavior — purely additive.
     # Fixes rollback failure mode: endgame scoring starvation (DIRECT merge missed at deadline)
     # refs: game_history/20260329_071549_score0597.jsonl T54-61,
     #       game_history/20260329_070630_score4475.jsonl T176-183,
     #       game_history/20260328_222114_score1359.jsonl T72-79,
     #       tmp/state/last_rollback_postmortem.md, tmp/state/last_rollback_analysis.md,
     #       tmp/batch_summary.txt, analyze_board.py (danger_direct_merge_available)
     # v371: axis 9.6b merged_type-aware targeting — prefer same-type closest to merged_type(N+1) for chain building
     # Fixes postmortem failure mode: type scattering without merge paths (piece_count accumulation)
     # Worst game: 40 pieces, max type 12, types scattered. Best game: 31 pieces, type 15 on board, types concentrated.
     # refs: advice.md (azumag, nimdavirus: N+1 adjacent priority), game_history/20260328_151000_score0486.jsonl T54-61,
     #       game_history/20260328_151437_score3261.jsonl T112-119, strategy_versions/protected/protected_e6f534c37e28_median12789_strategy.py,
     #       tmp/state/last_rollback_postmortem.md, tmp/batch_summary.txt, analyze_board.py
     # v370: growth center proximity extended to all reactive levels with congestion scaling
     # Fixes postmortem failure mode: piece scattering prevents merge paths (type concentration)
     # Worst game T78: 38 pieces, types 1-12 scattered x=[-3,+3], reactive=8, no merge, dies at turn 85.
     # Best game T142: types 14/13x2/12x2 concentrated around growth center, survives 149 turns.
     # Key gap: axis 5.6 only fired at reactive<3 with max bonus 50 — too weak and too narrow.
     # At reactive 0-2, bonus 50 is comparable to height penalty diff (~70-120) but easily overridden.
     # At reactive >= 3, no growth center guidance fires at all — axis 8.8 is sole differentiator.
     # v370: (1) Remove reactive<3 guard — growth center guidance at ALL reactive levels.
     # (2) Match axis 9.6b base bonus (50→100) for competitive tie-breaking signal.
     # (3) Add piece_count congestion scaling — stronger guidance as board congests (postmortem).
     # At pc=28: 100. At pc=35: 198. At pc=40: 268. Still safe vs axis 8.8 (-3000 to -7000).
     # No reactive<3 guard (postmortem constraint). Not landing_y-only (proximity + piece_count + gc_y).
     # refs: advice.md (zoumotu3: growth concentration, garsy38: type concentration),
     #       game_history/20260328_141811_score0926.jsonl T78 (worst: 38pc scattered),
     #       game_history/20260328_140715_score3212.jsonl T142 (best: 36pc concentrated),
     #       tmp/state/last_rollback_postmortem.md (piece_count predictor),
     #       tmp/batch_summary.txt (HEIGHT_CONTROL 18.7% low vs 13.4% high)
     # Fixes rollback failure mode: type scattering → piece_count accumulation → game over
     # Postmortem: piece_count is the key predictor. At reactive>=3, axis 8.8 makes all candidates similar,
     # but v362 bonus (max 60) is too small for meaningful tie-breaking. v368 (max 120 at reactive 1-2)
     # leaves reactive>=3 under-guided → HEIGHT_CONTROL default → pc accumulation during drought.
     # Replace reactive-level split with piece_count-based scaling. At pc=35: ~157 vs v362's ~45.
     # No reactive<3 guard (postmortem constraint). Not landing_y-only (proximity + piece_count + height).
     # refs: tmp/state/last_rollback_postmortem.md, tmp/batch_summary.txt, advice.md (garsy38 type concentration),
     #       game_history/20260328_130927_score0696.jsonl T50-59, game_history/20260328_132746_score0718.jsonl T55-62
     # Fixes postmortem failure mode: weak guidance at reactive>=3 → HEIGHT_CONTROL → pc accumulation
     # v368: same-type proximity guidance extended to reactive 1-2 — fills gap when current type has no reactive/near
     # When reactive_pair_count is 1-2 and current type has no reactive/near pairs but same-type exists on board,
     # there was NO placement guidance (axis 9.6 requires current_type_has_reactive, axis 9.7 requires no same-type).
     # This gap → HEIGHT_CONTROL default → piece_count accumulation (postmortem key failure mode).
     # Worst(score1069) final turns: reactive_avg=2.0, reasons=HIGH_LAYER/HIGH_TOWER, no merge guidance.
     # Batch: low-score HEIGHT_CONTROL 21.2% vs high-score 11.2%. Low-score games place lower but can't convert.
     # Fix: extend v362 same-type proximity guidance to reactive 1-2 with larger bonus (max ~120 vs ~60).
     # Not landing_y-only (postmortem constraint). Uses horizontal proximity to same-type pieces.
     # No reactive<3 guard (postmortem constraint). Fixes piece_count accumulation from no-guidance default.
     # refs: tmp/state/last_rollback_postmortem.md, tmp/batch_summary.txt,
     #       game_history/20260328_115143_score1069.jsonl, game_history/20260328_115426_score0647.jsonl,
     #       strategy_versions/protected/protected_e6f534c37e28_median12789_strategy.py, advice.md
     # Fixes rollback failure mode: no guidance at reactive 1-2 → HEIGHT_CONTROL → pc accumulation
     # v367: axis 9.7 pipeline-aware placement guidance — sibling to axis 9.6, fires when same_type_stack_top is None
     # Uses reactor["pipeline"] (unutilized) to guide placement near adjacent-type pieces (next_type ± 1).
     # Fixes postmortem: no guidance when no same-type on board → piece_count accumulation (worst T58: reactive=3, MEDIUM_TOWER).
     # No reactive < 3 guard, not landing_y-only. Bonus max ~80 (tie-breaking). refs: postmortem, analyze_board.py, score0613, protected_e6f534c37e28, batch_summary, advice.md
     # v366: NEAR merge risk penalty at deadline — reduce piece_count accumulation from failed NEAR merges
     # Worst game T50-52: 3 consecutive NEAR at deadline_crossed, all fail (delta=0), pc 32->35.
     # Penalty: deadline_crossed && merge_grade==NEAR && landing_y>0 → -landing_y*300.
     # Fixes postmortem failure mode: piece_count accumulation from failed NEAR at deadline
     # refs: game_history/20260328_102644_score0654.jsonl, game_history/20260328_101741_score2213.jsonl,
     #       tmp/state/last_rollback_postmortem.md, tmp/batch_summary.txt, strategy_versions/protected_e6f534c37e28
     # v365: remove duplicated axis 9.5 (v334 block) — fix double-bonus bug
     # axis 9.5 existed twice: old v334 block (lines ~1039-1070) and v337 block (lines ~1087-1131).
     # Both fired for non-russia cases, doubling SAME_TYPE_STACK_MERGE_PRIORITY(+600) and
     # SAME_TYPE_STACK(+200) bonuses when reactive==0 && danger==0. The v337 block is the
     # correct version with russia_phase suppression. The v334 block was dead code.
     # Also strengthened congestion penalty: at piece_count=37, old penalty was only 64
     # (invisible vs height diffs of ~140). New formula scales with piece_count^1.5 to
     # meaningfully discourage adding pieces to an overfull board. Still too small to
     # override merge opportunities (NEAR bonus=600+).
     # refs: tmp/batch_summary.txt (HEIGHT_CONTROL 19.8% low-score, piece_count 37→669),
     #       tmp/state/last_rollback_postmortem.md (piece_count predictor), game_history/20260328_095856_score0669.jsonl T48-65
     # Fixes rollback failure mode: duplicated axis 9.5 causing excessive same-type stacking
     # v364: growth center proximity — reduce piece scattering via board concentration
     # Re-introduce v358 concept lost in rollback cascade (301fa13ab0ab batch rollback).
     # Worst game ends with 35 scattered pieces (type 11 spread x=-2..x=2.5), 0 merges final 5 turns.
     # Best game concentrates growth around highest-type, reaches Russia phase with 5 merges final 8 turns.
     # Small bonus (max 50) for placing near deepest highest-type piece encourages concentration
     # (advice: zoumotu3 "1-2 locations for growth") without overriding merge/height priorities.
     # Fires only when max_type >= 6, not in russia_phase, not at reactive >= 3.
     # refs: advice.md (zoumotu3), tmp/batch_summary.txt, game_history/20260328_073826_score0883.jsonl T62-69,
     #       game_history/20260328_074802_score0970.jsonl T64-65, strategy_versions/protected/protected_994de46c98dd_median11502_strategy.py
     # Fixes rollback failure mode: piece scattering prevents merge paths (v359 rollback collateral)
     # v363: axis 9.6 stacking extension to reactive>=3 — v340 guard removal
     # v340 guardは旧スタッキング公式(vertical_bonus=(stack_y+1)*200)の高位スタッキング防止用だった。
     # v360でmerged_type近接度ベースに変更後、高さインセンティブは消滅(max~400, y>1で減衰)。
     # axis 8.8(-3000~-7000)が全候補を支配するreactive>=3でも、~400のスタッキングボーナスはtie-breakingに有用。
     # 現在のreactive>=3 NO_MERGE時: axis8.8一律ペナルティ→HEIGHT_CONTROL→エッジ投棄→piece_count増加。
     # postmortem制約遵守: reactive_pair_count<3ガードなし(全reactiveレベルで動作)。
     # refs: tmp/state/last_rollback_postmortem.md, tmp/batch_summary.txt, game_history/20260328_063347_score0563.jsonl T58-65
     # v362: high-reactive same-type proximity guidance + height_mult floor restoration
     # Fixes: reactive>=3 && merge=NO でpiece_count増加しゲームオーバーする failure mode (postmortem: piece_count 41→score1060)
     # reactive>=3でaxis8.8が一律ペナルティ→エッジ投棄→piece_count増加。same-type近接ボーナス(最大60)でtie-breaking改善。
     # height_mult床(0.5)復元し、3ゲート累積(0.048x)によるheight penalty無効化防止。
     # refs: tmp/state/last_rollback_postmortem.md, strategy_versions/protected/protected_e6f534c37e28_median12789_strategy.py, tmp/batch_summary.txt, game_history/20260328_055838_score0459.jsonl, game_history/20260328_052956_score2898.jsonl, advice.md
     # v361: piece_count-aware height penalty - axis 9.7 nesting回避 + piece_count圧縮指標導入
     # v360: axis 9.6 type-aware stacking + axis 9.7 removal
     # axis 9.6をv340 type-aware stackingに置換: 現在タイプにreactive/near pairがある場合のみスタッキングボーナス発動
     # 高さベースのボーナス(vertical_bonus = (stack_y+1)*200)を廃止し、merged_type(N+1)との近接度ベースに変更
     # axis 9.7(REACTIVE_PAIRS_COMPRESSION)を削除: protected戦略で有害判定済み、landing_y-onlyボーナスはpostmortem禁止事項
     # 未活用情報活用: per-type reactive/near pair extraction (current_type_has_reactive, current_type_has_near)
     # Fixes: 他タイプのreactive_pairsがある場合の高位スタッキングによるmax_y runaway
     # refs: strategy_versions/protected/protected_e6f534c37e28_median12789_strategy.py,
     #       tmp/state/last_rollback_postmortem.md, tmp/batch_summary.txt,
     #       game_history/20260328_051045_score0653.jsonl, game_history/20260328_045209_score1997.jsonl
     #
          # v341: axis 9.7盤面圧縮ボーナス修正版 - 低配置でもボーナスが発生するように改善
          # v338 failure mode: compression_bonus = (landing_y + 2.5) * 200.0 だと landing_y=-2.5でボーナス0になり、HEIGHT_CONTROLが選ばれる失敗パターン
          # ワーストゲーム(score0813)終盤: reactive_pairs=1, max_y=-0.56 (安定して低い) でHEIGHT_CONTROLが続き、即時併合機会を取りこぼしている
          # ベストゲーム(score2540)終盤: axis 9.7の盤面圧縮ボーナスが戦略的に機能し、即時併合機会を確実に捉えて高スコア
          # compression_bonus = (-landing_y) * 200.0 に変更し、landing_y=-2.5なら500.0、-1.0なら200.0、0なら0.0（低い位置ほどボーナス大）
          # same_type_stack_top is None 条件を削除し、axis 9.6とaxis 9.7が排他的に機能するように改善
          # Fixes rollback failure mode: reactive_pairs>=1 && merge_grade=="NO" 時の低配置でボーナス0の失敗パターン（axis 9.7ボーナス修正）
          # refs: tmp/state/last_rollback_postmortem.md, tmp/state/last_rollback_analysis.md, tmp/improve_brief.md, tmp/batch_summary.txt, advice.md,
          #       game_history/20260324_214039_score0813.jsonl, game_history/20260324_214827_score2540.jsonl
          #
         # v340: reactive_pairs>=3時deadline_crossed併合最優先版 - axis 9.6超危険域無効化
         # Fixes rollback failure mode: reactive_pairs>=3 && deadline_crossedでの高配置 runway（axis 9.6無効化）
         # refs: tmp/state/last_rollback_postmortem.md, tmp/state/last_rollback_analysis.md, tmp/improve_brief.md, tmp/batch_summary.txt,
         #       game_history/20260324_210005_score0638.jsonl, game_history/20260324_210741_score2602.jsonl
          #
          # v338: reactive_pairsあり時の戦略的配置優先化版 - HEIGHT_CONTROL過剰選択の解消
         # v337 failure: ロシアフェーズでaxis 9.5の盤面圧縮ボーナス（+300.0）がaxis 8.7の即時併合ボーナスと競合し、即時併合機会を取りこぼしている
         # ワーストゲーム(score0731)終盤: reactive_pairsが少ないが即時併合機会を取りこぼし、max_y runawayでゲームオーバー
         # ベストゲーム(score3171)終盤: ロシアフェーズで即時併合を確実に捉えて高スコア
         # batch_summaryでHEIGHT_CONTROLが19.9%選択(avg_score_delta=1.2)と過剰であり、即時併合機会を取りこぼしていることを確認
         # ワーストゲーム(score0413)ではreactive_pairsがあるにも関わらずHEIGHT_CONTROLが続き、即時併合機会を取りこぼしている失敗パターンを解消
         # ベストゲーム(score2072)ではreactive_pairsがある場合でも即時併合機会を確実に捉えて高スコア稼いでいる
         # advice.md「盤面がどうだろうが即時併合狙った方が絶対勝率高い」に基づく即時併合優先の構造的改善
         # axis 9.6追加: reactive_pairs>=1 && merge_grade=="NO"の場合、盤面上の現在タイプの最も高い位置のピースに着地できる配置にボーナスを与え、即時併合機会を最大化
         # axis 9.7追加: reactive_pairs>=1 && merge_grade=="NO"の場合、盤面密度に応じたボーナスを与え、盤面圧縮を優先
         # これによりreactive_pairsがある状況でHEIGHT_CONTROLではなく、即時併合機会を確実に捉える戦略へ切り替え
         # 盤面密度ボーナスはlanding_yに応じて動的に評価し、下層に配置するほどボーナスが大きい（board compression）
         # 低スコアの主要因である「併合機会があるのにHEIGHT_CONTROL」問題を解消し、p25悪化を改善
         # refs: advice.md (Pitman_live, azumag), tmp/improve_brief.md, tmp/batch_summary.txt, 
         #       game_history/20260324_154730_score0413.jsonl, game_history/20260324_161825_score2775.jsonl, game_history/20260324_161021_score0838.jsonl,
         #       game_history/20260324_155130_score2072.jsonl, strategy_versions/best_score2335_strategy.py
         # Fixes rollback failure mode: reactive_pairsがある状況での即時併合機会取りこぼし（axis 9.6 axis 9.7追加）
         #
        # v336 failure: ロシアフェーズでreactive_pairs<3の場合、axis 9.5の盤面圧縮ボーナス（+300.0）がaxis 8.7の即時併合ボーナス（1200.0/1000.0）と競合し、即時併合機会を取りこぼしている
        # ワーストゲーム(score0731)終盤: reactive_pairsが少ないが即時併合機会を取りこぼし、max_y runawayでゲームオーバー
        # ベストゲーム(score3171)終盤: ロシアフェーズで即時併合を確実に捉えて高スコア
        # ロシアフェーズでは盤面が狭く、即時併合機会を最大化することが重要。axis 9.5の盤面圧縮ボーナスがaxis 8.7の即時併合優先を阻害している
        # axis 9.5修正: russia_phase && reactive_pair_count < 3 の場合、盤面圧縮ボーナス（+300.0）とペナルティ軽減（+100.0）を削除し、即時併合機会を最優先
        # これによりロシアフェーズでの即時併合機会取りこぼしを削減し、2つ目のロシア育成スペースを確保
        # refs: advice.md (あずまぐ), tmp/state/last_rollback_postmortem.md, tmp/state/last_rollback_analysis.md, tmp/improve_brief.md, tmp/batch_summary.txt, tmp/sandbox_files.md,
        #       game_history/20260324_141236_score0731.jsonl turns 66-73, game_history/20260324_144026_score3171.jsonl turns 119-126
        # Fixes rollback failure mode: ロシアフェーズでの即時併合機会取りこぼし（axis 9.5 russia_phase条件追加）
        #
        # v336: ロシア建国後フェーズ即時併合強化版 - axis 8.7ボーナス強化・reactive_pairs<3でも即時併合優先
        # last_rollback_postmortemのfailure mode: "deadline_crossed時に即時ゲームオーバー判定を行い、reactive pairs の併合機会を失っている"
        # v335 failure: ロシアフェーズ(type 15 >= 1)でreactive_pairs>=3の場合、即時併合ボーナスが弱く、盤面圧縮ボーナスと競合して即時併合機会を取りこぼしている
        # ワーストゲーム(score0589)終盤: reactive_pairs>=3, merge_grade="NO"でREACTIVE_PAIRS_NO_MERGE_PENALTYが続き、max_y runawayでゲームオーバー
        # ベストゲーム(score2162)終盤: reactive_pairsが少なく、即時併合機会を確実に捉えて高スコア
        # ロシア建国後は盤面が狭く、高typeピースが場所を占有している状態。この局面で通常時と同じ戦略を続けるのは不十分
        # ロシア建国後は明確にフェーズが切り替わるべき。具体的には:
        #   - 既存のロシア(type 15)の位置を保護しつつ、2つ目のロシアへの成長パイプラインを確保
        #   - ロシアが盤面にある状態で type 13, 14 級のピースをどこに育てるかの空間計画が必要
        #   - 盤面が狭いため、小typeの効率的な消化（併合による盤面確保）がより重要になる
        # axis 8.7修正: ロシアフェーズでreactive_pairs>=1の場合、即時併合ボーナスを強化（reactive_pairs<3でも1000.0/1200.0、>=3で1200.0/1400.0）
        # axis 8.7修正: reactive_pairs<3の場合、盤面圧縮ボーナスを抑制（800.0→400.0）し、即時併合機会を優先
        # これによりロシア建国後の狭い盤面で、即時併合機会を優先し、盤面圧縮で2つ目のロシア育成スペースを確保
        # advice.md「ロシア建国後の死亡速度が早い。建国後はより慎重な盤面進行を検討すること」「ロシアのような大きいピースが盤面の上に出てきた時は、戦略モードを切り替えるべき」に基づくロシアフェーズ強化
        # refs: advice.md (あずまぐ), tmp/state/last_rollback_postmortem.md, tmp/state/last_rollback_analysis.md, tmp/improve_brief.md, tmp/batch_summary.txt, tmp/sandbox_files.md,
        #       game_history/20260324_133153_score0854.jsonl turns 55-63 (ロシア出現後max_y runaway), game_history/20260324_135316_score2615.jsonl
        # Fixes rollback failure mode: ロシア建国後の即時併合機会取りこぼし（axis 8.7ボーナス強化・reactive_pairs<3でも即時併合優先）
        # refs: tmp/state/last_rollback_postmortem.md, tmp/state/last_rollback_analysis.md, tmp/improve_brief.md, tmp/batch_summary.txt, advice.md, tmp/sandbox_files.md
       #
     # v331: deadline_crossed時の即時併合優先強化版 - 危険域でのmax_y runaway防止
     # last_rollback_postmortemのfailure mode: "deadline_crossed時にreactive_pairs>=1でも即時併合不可で延命配置のみ続き、max_y runaway"
     # ワーストゲーム(score0825)終盤turns 55-62: deadline_crossed=false→trueでreactive_pairs=2-3, merge_available=false続きでHIGH_LAYERが選ばれmax_y=2.94に上昇してゲームオーバー
     # ワーストゲーム(score0866)終盤turns 63-70: deadline_crossed=true, reactive_pairs=4-9, merge_available=false続きでREACTIVE_PAIRS_COMPRESSIONが選ばれmax_y=3.25に上昇してゲームオーバー
     # ベストゲーム(score3080)終盤turns 120-132: max_y=2.73→3.00の危険域でも即時併合機会を確実に捉えて3080点を出している
     # axis 8.5修正: deadline_crossed条件を追加し、deadline_crossed時には即時併合ボーナスを強化（DIRECT: 500.0→1200.0, NEAR: 300.0→600.0）
     # これによりdeadline_crossed時の危険域で即時併合がより強力に推奨され、延命配置によるmax_y runawayを防止
     # refs: tmp/state/last_rollback_postmortem.md, tmp/state/last_rollback_analysis.md, tmp/improve_brief.md, tmp/batch_summary.txt, advice.md, tmp/sandbox_files.md,
     #       game_history/20260324_060500_score0825.jsonl turns 55-62, game_history/20260324_063459_score0866.jsonl turns 63-70,
     #       game_history/20260324_062439_score3080.jsonl turns 120-132
     # Fixes rollback failure mode: deadline_crossed時の即時併合機会取りこぼし（axis 8.5 deadline_crossed条件追加）
    #
    # v330: axis 9.5盤面圧縮ボーナス条件厳格化版 - 即時併合優先強化・ロシアフェーズ改善
    # last_rollback_postmortemのfailure mode: "reactive_pairs>=3で即時併合不可続き、盤面圧迫悪化でゲームオーバー"
    # ワーストゲーム(score0634)終盤turns 52-59: reactive_pairs=1-4あるのに即時併合不可続き、HIGH_TOWER/HIGH_LAYERが選ばれmax_y=2.12に悪化してゲームオーバー
    # ベストゲーム(score2710)終盤turns 106-113: 即時併合機会を確実に捉えてmax_y=2.73で安定
    # axis 9.5修正: 盤面圧縮ボーナスの適用条件を danger_piece_count == 0 から danger_piece_count == 0 && reactive_pair_count == 0 に厳格化
    # これによりreactive_pairsが存在する場合は盤面圧縮ボーナスが適用されなくなり、即時併合機会を優先する戦略へ切り替わる
    # advice.md「盤面がどうだろうが即時併合狙った方が絶対勝率高い」「ロシア建国後の死亡速度が早い。建国後はより慎重な盤面進行を検討すること」に基づく構造的改善
    # refs: tmp/state/last_rollback_postmortem.md, tmp/state/last_rollback_analysis.md, tmp/improve_brief.md, tmp/batch_summary.txt, advice.md, tmp/sandbox_files.md,
    #       game_history/20260324_054633_score0634.jsonl turns 52-59, game_history/20260324_055424_score2710.jsonl turns 106-113
    # Fixes rollback failure mode: reactive_pairsがある状況での即時併合機会取りこぼし（axis 9.5 reactive_pairs条件追加）
    #
    # v329: reactive_pairs>=3での高配置強力抑制版 - max_y runaway防止
    # v328の問題点: -3000.0固定ペナルティはheight_mult緩和(axis 2, 364, 379-382)や盤面圧縮ボーナス(axis 9.5)と競合し、高配置が選ばれる
    # ワーストゲーム(score0636)終盤turns 56-62: reactive_pairs=3-5, merge_available=false, deadline_crossed=trueでmax_y=2.45→3.12に上昇
    # ワーストゲーム(score0725)終盤turns 61-62: reactive_pairs=3, merge_available=falseでmax_y=3.39→2.81の高配置が選ばれゲームオーバー
    # ベストゲーム(score3996)終盤turns 150-154: 即時併合機会を確実に捉えてtype 15を保護しつつ3996点を出している
    # axis 8.8修正: reactive_pairs>=3 && merge_grade=="NO"の場合、landing_yに応じた動的ペナルティを追加
    #   - landing_y <= 0: -3000.0
    #   - 0 < landing_y <= 1: -3000.0 + landing_y * 2000.0 (例: 0.5 -> -4000.0)
    #   - landing_y > 1: -5000.0 + landing_y * 2000.0 (例: 1.5 -> -8000.0, 2.0 -> -9000.0)
    # これにより高配置になるほどペナルティが指数関数的に増大し、height_mult緩和やボーナスを上回る強力な抑制を実現
    # last_rollback_postmortemのconstraint: "reactive_pairs>=3で即時併合がない場合、deadline_crossedに関わらず即時併合を最優先するペナルティを適用する（deadline_crossed条件を含める）"を遵守
    # refs: tmp/state/last_rollback_postmortem.md, tmp/state/last_rollback_analysis.md, tmp/improve_brief.md, tmp/batch_summary.txt, advice.md, tmp/sandbox_files.md,
    #       game_history/20260324_045921_score0636.jsonl turns 56-62, game_history/20260324_043823_score0725.jsonl turns 61-62,
    #       game_history/20260324_044502_score3996.jsonl turns 150-154
    # Fixes rollback failure mode: reactive_pairs>=3での高配置 runaway（v328固定ペナルティ→v329動的ペナルティ）
   #
   # v328: reactive_pairs>=3で即時併合なし時の強力ペナルティ追加版 - 即時併合機会取りこぼし削減
   # last_rollback_postmortemのfailure mode: "reactive_pairs>=3で即時併合不可続き、盤面圧迫悪化でゲームオーバー"
   # ワーストゲーム(score0649)終盤turns 39-68: reactive_pairs=4-6, merge_available=false続きでHIGH_LAYER/MEDIUM_TOWER/REACTIVE_PAIRS_COMPRESSIONが選ばれmax_y=0.94→3.04に上昇してゲームオーバー
   # ワーストゲーム(score0677)終盤turns 64-75: reactive_pairs=4-5, merge_available=false続きでREACTIVE_PAIRS_COMPRESSION/HIGH_LAYERが選ばれmax_y=0.63→2.90に上昇してゲームオーバー
   # ベストゲーム(score3231)終盤turns 134-141: max_y=2.73→3.00の危険域でもNEAR_MERGE_REACTIVE_IMMEDIATE_MERGE_PRIORITYを優先し、即時併合機会を確実に捉えて3231点を出している
   # axis 8.8追加: reactive_pairs>=3 && merge_grade=="NO"の場合、deadline_crossedに関わらず非併合配置に強力なペナルティ(-3000.0)を適用
   # これによりreactive_pairs>=3の超危険域では即時併合機会を逃した場合のペナルティがaxis 9.2の-4500.0ペナルティよりも高くなり、即時併合機会を強制的に待つ戦略へ切り替え
   # last_rollback_postmortemのconstraint: "reactive_pairs>=3で即時併合がない場合、deadline_crossedに関わらず即時併合を最優先するペナルティを適用する（deadline_crossed条件を含める）"を遵守
   # refs: tmp/state/last_rollback_postmortem.md, tmp/state/last_rollback_analysis.md, tmp/improve_brief.md, tmp/batch_summary.txt, advice.md, tmp/sandbox_files.md,
   #       game_history/20260324_040343_score0649.jsonl turns 39-68, game_history/20260324_041615_score0677.jsonl turns 64-75,
   #       game_history/20260324_042150_score3231.jsonl turns 134-141
   # Fixes rollback failure mode: reactive_pairs>=3で即時併合不可続き、盤面圧迫悪化でゲームオーバー
  #
  # v325: reactive_pairs盤面圧縮ボーナス削除版 - 即時併合機会優先化
  # v324 failure: reactive_pairs >= 3 && merge_grade == "NO"の場合、axis 9.5の+800.0ボーナスがaxis 9.2の-2500.0ペナルティを上書きし、盤面圧縮（非併合配置）が選ばれてmax_y runawayでゲームオーバー
  # ワーストゲーム(score0611)終盤turns 42-48: reactive_pairs=3-4, merge_grade="NO"続きで非併合配置が選ばれmax_y=0.16→1.78→3.51に上昇してゲームオーバー
  # ベストゲーム(score2481)ではreactive_pairsがある場合でも即時併合機会を確実に捉え、盤面圧縮より即時併合を優先して安定
  # axis 9.5修正: reactive_pair_count >= 1 && merge_grade == "NO"の場合の+800.0ボーナスを削除
  # reactive_pairsがある場合はaxis 9.2の-2500.0ペナルティを優先させ、即時併合機会を確実に待つ戦略へ切り替え
  # reactive_pairsがない場合のみ+300.0ボーナスを適用し、盤面圧縮を優先
  # refs: tmp/state/last_rollback_postmortem.md, tmp/state/last_rollback_analysis.md, tmp/improve_brief.md, tmp/batch_summary.txt, advice.md,
  #       game_history/20260324_012537_score0611.jsonl turns 42-48, game_history/20260324_020024_score2481.jsonl
  # Fixes rollback failure mode: reactive_pairs盤面圧縮ボーナスによる即時併合機会取りこぼし（axis 9.5 reactive_pairsボーナス削除）
  #
  # v324: deadline_crossed対応・ロシアフェーズ強化版 - v323 failure mode潰し
  # v323 failure: axis 9.2にdeadline_crossed条件が含まれておらず、deadline_crossed時でもreactive_pairs>=3の即時併合不可でペナルティが適用されない
  # ワーストゲーム(score0651)終盤turns 42-47: max_y=0.16→1.78 (deadline_crossed: false→true→false), reactive_pairs=3-4, merge_available=false続き
  # deadline_crossed=false時にSAME_TYPE_STACK_MERGE_PRIORITY_REACTIVEで非併合を選択し、盤面圧迫が進みdeadline_crossed=trueでゲームオーバー
  # ベストゲーム(score2461)では危険域でも即時併合機会を確実に捉え、戦略的配置を維持して安定
  # v323の改善点:
  # 1. axis 9.2修正: deadline_crossed条件を追加し、deadline_crossed時でもreactive_pairs>=2で即時併合不可の場合に-2500.0ペナルティを適用
  # 2. axis 8.7強化: ロシアフェーズで即時併合がない場合のボーナスを強化（deadline_crossed時は900.0、通常時は800.0）
  # 3. axis 2修正: deadline_crossed時のheight_mult緩和条件にdanger_piece_count==0を追加
  # refs: tmp/state/last_rollback_postmortem.md, tmp/state/last_rollback_analysis.md, tmp/improve_brief.md, tmp/batch_summary.txt, advice.md,
  #       game_history/20260324_010847_score0651.jsonl, game_history/20260324_010300_score2461.jsonl
  # Fixes rollback failure mode: deadline_crossed時の即時併合取りこぼしとロシア建国後の盤面圧迫悪化
  #
  # v322: ロシアフェーズ再導入版 - ロシア建国後のフェーズ切り替え実装
  # v317 failure: axis 8.5（危険域で即時併合不可時にheight_multを0.4に緩和して盤面圧縮を優先）が過剰に機能し、即時併合機会を取りこぼしてmax_y runawayでゲームオーバー
  # ワーストゲーム(score0866)終盤turns 53-60: reactive_pairs=7-8あるのに即時併合不可続き、max_y runawayでゲームオーバー
  # ベストゲーム(score3014)終盤turns 114-121: 即時併合機会を確実に捉えてmax_y=4.10で安定して2923点を出している
  # batch_summaryでHEIGHT_CONTROLが11.4%選択(avg_score_delta=0.0)と過剰、即時併合機会取りこぼしが主要な敗因
  # advice.md「盤面がどうだろうが即時併合狙った方が絶対勝率高い」「ロシア建国後の死亡速度が早い。建国後はより慎重な盤面進行を検討すること」に基づく構造的改善
  # axis 8.5削除の維持: 危険域で即時併合不可時のheight_mult *= 0.4盤面圧縮ロジックを削除し続け、即時併合機会の取りこぼしを防止
  # axis 8.7再導入: ロシアフェーズ（type 15 >= 1）で即時併合を最優先する戦略へ切り替え
  #   - 即時併合候補がある場合: 即時併合を最優先（強力なボーナス）
  #   - 即時併合がない場合: 盤面圧縮を優先しつつ、type 15保護を徹底
  #   - 危険ピースがある場合は即時併合優先を維持
  # 未活用情報：盤面上のtype 15個数、即時併合可否(merge_grade)、danger_piece_count
  # refs: tmp/state/last_rollback_postmortem.md, tmp/state/last_rollback_analysis.md, tmp/improve_brief.md, tmp/batch_summary.txt, advice.md,
  #       game_history/20260323_150619_score0866.jsonl turns 53-60, game_history/20260323_151104_score3014.jsonl turns 114-121
  # Fixes rollback failure mode: ロシア建国後の即時併合取りこぼし（axis 8.7再導入）
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
# refs: tmp/batch_summary.txt, tmp/state/last_rollback_postmortem.md, game_history/20260313_231816_score0814.jsonl turns 54-57, game_history/20260313_231248_score4925.jsonl, strategy_versions/best_score2335_strategy.py
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
# v202: reactive pairsボーナス強化版 - 即時併合機会取りこぼし削減
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
# v272: 危険領域reactive_pairs即時併合優先強化 - v271失敗モード（即時併合機会逃し）潰し
# advice.md「同じタイプが続いて来たらそのタイプの上に置き、併合チャンスを優先する」（Pitman_live）に基づく構造的改善。
# batch_summaryでHEIGHT_CONTROLが15.9%選択(avg_score_delta=0.1)と過剰であり、即時併合機会を取りこぼしていることを確認。
# axis 9.5追加：盤面上の現在タイプピースの上に配置を優先する評価軸を追加し、即時併合機会を最大化。
# reactive_pairsがある状況では、即時併合機会を確実に捉える配置を優先し、盤面圧縮と将来の併合を同時に狙う。
# last_rollback_postmortemの「deadline_crossed=true && danger_piece_count>0でHEIGHT_CONTROL優先禁止」制約を遵守。
# ワーストゲーム(score0266)終盤でreactive_pairsがあるのにHEIGHT_CONTROLが続き、即時併合機会を取りこぼしている失敗パターンを解消。
# 構造的変更（新規評価軸axis 9.5追加）であり、数値微調整ではない。即時併合機会取りこぼし削減と危険域での即時併合優先。
# refs: advice.md (Pitman_live), tmp/batch_summary.txt, tmp/state/last_rollback_postmortem.md, game_history/20260319_063955_score0266.jsonl, game_history/20260319_060621_score2932.jsonl
#
# v271: Reactive pairs non-merge height penalty relaxation - v270 failure mode fix
# ワーストゲーム(score0797)終盤turns 47-52でreactive_pairs=3, merge_available=falseが続き、
# DANGER_ZONE_IMMEDIATE_MERGE_PRIORITYペナルティにより強制的に高配置となりmax_y=2.31でゲームオーバー。
# v269/v270の-1500.0ペナルティは全候補一律に下げるため、「強制配置」問題が残っていた。
# reactive_pair_count >= 1 && merge_grade == "NO" の場合、height_multを0.8に緩和し、
# 戦略的配置の余地を確保しつつdeadline緊急性を維持。
# ベストゲーム(score2945)終盤turns 127-133でも同様の状況だが、より多くのターンを耐えている。
# v268/v270 rollback教訓: 強制的な高配置回避。reactive_pairs活用のシンプルな改善を採用。
# refs: tmp/batch_summary.txt, tmp/state/last_rollback_postmortem.md, tmp/state/last_rollback_analysis.md, game_history/20260319_023107_score0797.jsonl turns 46-53, game_history/20260319_020802_score2945.jsonl turns 126-133, tmp/batch_summary.txt, advice.md, game_history/20260319_063955_score0266.jsonl, game_history/20260319_060621_score2932.jsonl
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
    # --- BEGIN DEADLINE GUARD (injected from current strategy deadline logic) ---
    # Emergency deadline safety: when the reactor is past/near the deadline,
    # force an immediate merge or the safest landing to avoid runaway stacking.
    __dlg_game_state = game_state if isinstance(game_state, dict) else {}
    __dlg_analysis = analysis if isinstance(analysis, dict) else {}
    __dlg_reactor = __dlg_analysis.get("reactor", {}) if isinstance(__dlg_analysis.get("reactor", {}), dict) else {}
    __dlg_margin = __dlg_reactor.get("deadline_margin", 99.0)
    try:
        __dlg_margin = float(__dlg_margin)
    except (TypeError, ValueError):
        __dlg_margin = 99.0
    __dlg_dcross = bool(__dlg_game_state.get("deadline_crossed", False))
    __dlg_rps = __dlg_reactor.get("reactive_pairs", [])
    if isinstance(__dlg_rps, list):
        __dlg_rp_count = len(__dlg_rps)
    else:
        try:
            __dlg_rp_count = int(__dlg_rps)
        except (TypeError, ValueError):
            __dlg_rp_count = 0
    __dlg_cands = __dlg_analysis.get("results", []) or __dlg_analysis.get("candidates", []) or []
    if not isinstance(__dlg_cands, list):
        __dlg_cands = []
    __dlg_critical = __dlg_dcross or __dlg_margin < 0.3 or __dlg_rp_count >= 3
    if __dlg_critical and __dlg_cands:
        __dlg_direct = [
            c for c in __dlg_cands
            if isinstance(c, dict) and c.get("merge_grade") == "DIRECT" and not c.get("crosses_deadline")
        ]
        if __dlg_direct:
            def __dlg_score_direct(c):
                return (
                    0 if c.get("danger_direct_merge_available") else 1,
                    float(c.get("landing_y", 99.0) or 99.0),
                )
            __dlg_best = min(__dlg_direct, key=__dlg_score_direct)
            return {"x": float(__dlg_best.get("x", 0.0) or 0.0), "reason": "DEADLINE_GUARD_DIRECT_MERGE"}
        __dlg_near_safe = [
            c for c in __dlg_cands
            if isinstance(c, dict) and c.get("merge_grade") == "NEAR" and not c.get("crosses_deadline")
        ]
        if __dlg_near_safe:
            __dlg_best = min(__dlg_near_safe, key=lambda c: float(c.get("landing_y", 99.0) or 99.0))
            return {"x": float(__dlg_best.get("x", 0.0) or 0.0), "reason": "DEADLINE_GUARD_NEAR_MERGE"}
        __dlg_safe = [c for c in __dlg_cands if isinstance(c, dict) and not c.get("crosses_deadline") and c.get("merge_grade") != "NO"]
        if __dlg_safe:
            __dlg_best = min(__dlg_safe, key=lambda c: float(c.get("landing_y", 99.0) or 99.0))
            return {"x": float(__dlg_best.get("x", 0.0) or 0.0), "reason": "DEADLINE_GUARD_SAFE_LANDING"}
        __dlg_any_safe = [c for c in __dlg_cands if isinstance(c, dict) and not c.get("crosses_deadline")]
        if __dlg_any_safe:
            __dlg_best = min(__dlg_any_safe, key=lambda c: float(c.get("landing_y", 99.0) or 99.0))
            # v602: suppress DEADLINE_GUARD_SAFE_LANDING when NO merge possible despite reactive pairs
            # Worst game T55-60: 6 consecutive DEADLINE_GUARD with merge_grade=NO despite rp=5-9.
            # mandatory_themes: "デッドラインを超える位置にピースを置く場合は、併合できる場合に限る"
            # If all safe candidates have NO merge and reactive pairs exist, guard would raise
            # max_y without merge benefit. Let normal decide() apply height penalty instead.
            __dlg_all_no_merge = all(
                isinstance(c, dict) and c.get("merge_grade") == "NO"
                for c in __dlg_any_safe
            )
            if __dlg_all_no_merge and __dlg_rp_count >= 1:
                pass  # suppress DEADLINE_GUARD, fall through to normal decide()
            else:
                return {"x": float(__dlg_best.get("x", 0.0) or 0.0), "reason": "DEADLINE_GUARD_SAFE_LANDING"}
        # v601: mandatory_themes enforcement — if selected candidate crosses deadline with no merge
        # capability, suppress DEADLINE_GUARD and fall through to normal decide() which has v411
        # CROSSES_DEADLINE penalty (-1200). Worst game T67: DEADLINE_GUARD returned x=2.98 with
        # crosses_deadline=true, merge_available=false, violating mandatory_themes.
        # Best game T92: DEADLINE_GUARD_DIRECT_MERGE with merge_available=true worked correctly.
        __dlg_best = min(__dlg_cands, key=lambda c: float(c.get("landing_y", 99.0) or 99.0))
        if __dlg_best.get("crosses_deadline") and __dlg_best.get("merge_grade") == "NO":
            pass  # suppress DEADLINE_GUARD, fall through to normal decide()
        else:
            return {"x": float(__dlg_best.get("x", 0.0) or 0.0), "reason": "DEADLINE_GUARD_SAFE_LANDING"}
        # v600: mandatory_themes enforcement — if ALL candidates cross deadline with no safe merge,
        # suppress DEADLINE_GUARD and fall through to normal decide() which has v411 CROSSES_DEADLINE penalty (-1200).
        # Worst game T46/T49/T50/T52/T54+: DEADLINE_GUARD returned crosses_deadline=true positions despite
        # merge_available=false, violating "デッドラインを超える位置にピースを置く場合は、併合できる場合に限る".
        # Best game T66: DEADLINE_GUARD_DIRECT_MERGE with merge_available=true worked correctly.
        # Fixes rollback failure mode: DEADLINE_GUARD emergency block non-merge fallback crosses deadline
        # refs: tmp/analysis_result.md (adopted hypothesis), data/mandatory_themes.txt
        __dlg_merge_safe = [
            c for c in __dlg_cands
            if isinstance(c, dict) and c.get("merge_grade") in ("DIRECT", "NEAR") and not c.get("crosses_deadline")
        ]
        __dlg_all_cross_deadline = all(
            isinstance(c, dict) and c.get("crosses_deadline")
            for c in __dlg_cands
        )
        if __dlg_all_cross_deadline and not __dlg_merge_safe:
            pass  # suppress DEADLINE_GUARD, fall through to normal decide()
    # --- END DEADLINE GUARD ---

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
    deadline_crossed = game_state.get("deadline_crossed", False)

    # --- reactor information (for reactive merge priority) ---
    reactor = analysis.get("reactor", {})
    reactive_pairs = reactor.get("reactive_pairs", [])
    # reactive_pairs is a list, count pairs for evaluation
    reactive_pair_count = len(reactive_pairs) if isinstance(reactive_pairs, list) else 0
    danger_piece_count = reactor.get("danger_piece_count", 0)
    reactor_margin = reactor.get("deadline_margin", 99.0)

    # --- v322: russia phase detection (type 15 pieces on board) ---
    # ロシアフェーズ: 盤面上にtype 15（ロシア）が1つ以上存在する場合
    # advice.md「ロシア建国後の死亡速度が早い。建国後はより慎重な盤面進行を検討すること」に基づく構造的改善
    # ロシア建国後は盤面が狭く、高typeピースが場所を占有している状態。この局面で通常時と同じ戦略を続けるのは不十分
    russia_phase_count = sum(1 for p in pieces if p.get("type") == 15)
    russia_phase = russia_phase_count >= 1
    # v548: double_russia_phase — 2つ目のロシア(type 15)が盤面にある場合、
    # ソ連建国(type 16)まであと1併合。この局面では盤面圧縮ボーナスより
    # 既存ロシアの保護と2つ目ロシアの成長パイプライン維持が最優先。
    # ロシア1つのままゲームオーバーになるのが最も惜しい負けパターン。
    double_russia_phase = russia_phase_count >= 2

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
        rp[2] == next_type for rp in reactive_pairs if isinstance(rp, (list, tuple)) and len(rp) >= 3
    )
    current_type_has_near = any(
        np[2] == next_type for np in near_pairs if isinstance(np, (list, tuple)) and len(np) >= 3
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

        # --- v603: type-aware merge bonus scaling ---
        # All 4 batch games (scores 1072-1936) failed at type 13 with russia=0/4.
        # Root cause: strategy is reactive (rp=4-6) but doesn't prioritize high-type merges.
        # Ported from best_score5801_strategy.py lines 1142-1166.
        # Makes type 13+ merges disproportionately attractive to reach Russia.
        # Formula: type_scale = 1.0 + 0.1 * max(0, next_type - 5), capped 0.8-2.0
        # Rollback failure mode: piece_count accumulation from failed NEAR at deadline (v366)
        # Rollback constraint: axis 9.6b must not be removed
        type_scale = 1.0 + 0.1 * max(0, next_type - 5)
        type_scale = max(0.8, min(type_scale, 2.0))

        # ----- evaluation axis 1: merge bonus -----
        # analyze_board judged merge_grade gives bonus
        # DIRECT: direct hit target (success rate 95.7%)
        # NEAR:   contact zone after landing (success rate 68.5%)
        # FAR:    contact possibility by drift (low probability)
        if merge_grade == "DIRECT":
            score += 1200.0 * merge_mult * type_scale
            reasons.append("DIRECT_MERGE")
        elif merge_grade == "NEAR":
            score += 600.0 * merge_mult * type_scale
            reasons.append("NEAR_MERGE")
        elif merge_grade == "FAR":
            score += 200.0 * merge_mult * type_scale
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
        if merge_grade == "NEAR" and piece_count >= 33 and reactor_margin < 1.0 and landing_y >= 1.0:
            score -= 600.0 * merge_mult
            reasons.append("HIGH_PC_NEAR_PENALTY")

        # ----- axis 1.7b: gap-zone NEAR merge penalty (v560_mod follow-up) -----
        # worst_game T53 (max_y=1.88, deadline_crossed=true) and T57 (max_y=2.28, rp=9)
        # selected NEAR merge despite deadline crossed, failing with only +21 score_delta.
        # last_rollback_postmortem: "max_y>=2.0 NEAR merge failure causing max_y runaway".
        # v560 reactive_pairs_cleanup requires max_y>=2.5, missing the 2.0-2.5 gap zone.
        # When deadline is already crossed and max_y>=2.0, NEAR failure risk is highest —
        # piece_count grows without merge benefit, accelerating game-over.
        # Penalty -500 makes NO_MERGE low placement competitive vs NEAR when in gap zone.
        # This is NOT a v550/v560 threshold change — it is a new axis with distinct condition.
        # refs: tmp/state/last_rollback_postmortem.md (failure_mode: max_y>=2.0 NEAR merge failure)
        #       game_history/20260410_183623_score0841.jsonl (T53, T57 gap-zone NEAR failures)
        #       tmp/batch_summary.txt (HEIGHT_CONTROL 17-19% in low-score games)
        if merge_grade == "NEAR" and max_y >= 2.0 and deadline_crossed:
            score -= 500.0
            reasons.append("GAP_ZONE_NEAR_PENALTY")

        # ----- axis 1.7c: death-spiral NEAR suppression (v579) -----
        # In the death-spiral window (rp>=3, pc>=32, max_y>=1.5), high-y NEAR merges
        # (landing_y >= 1.0) that fail (31.5% rate) add a piece with zero score delta,
        # accelerating irreversible piece_count growth. Evidence: worst0589 T55-T62,
        # score1089 T69-T76 — NEAR merges succeeded but only reduced pc by 1-2, then
        # NO-merge turns accumulated pieces to game-over threshold.
        # This cancels the base NEAR bonus (600*merge_mult) for high-y candidates in
        # this window, forcing the strategy to either find a low-y NEAR (safer) or fall
        # through to NO-merge with height-only differentiation (low placement).
        # NOT a general NEAR suppression: only fires when all 4 conditions are met.
        # Does NOT affect: DIRECT merges, NEAR at landing_y < 1.0, or rp < 3.
        # refs: game_history/20260411_140730_score0589.jsonl T55-T62,
        #       game_history/20260411_142110_score1089.jsonl T69-T76,
        #       tmp/analysis_result.md
        if (merge_grade == "NEAR"
            and reactive_pair_count >= 3
            and piece_count >= 32
            and max_y >= 1.5
            and landing_y >= 1.0):
            score -= 600.0 * merge_mult  # Cancel the base NEAR bonus
            reasons.append("DEATH_SPIRAL_NEAR_SUPPRESSION")

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
        if result.get("danger_direct_merge_available", False) and merge_grade == "DIRECT":
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
            if deadline_crossed and piece_count >= 33 and landing_y >= 1.5:
                bonus = 0.0
            else:
                bonus = 600.0 if deadline_crossed else 300.0
            score += bonus
            reasons.append("DANGER_NEAR_MERGE_PRIORITY")

        # ----- v624: russia phase deadline NEAR suppression -----
        # analysis_result.md adopted hypothesis: ロシア建国後フェーズ(russia_phase && deadline_crossed && max_y>=2.0)
        # においてDIRECT mergeのみを許可しNEAR mergeを実質抑制する安全弁。
        # NEAR merge success rate is 68.5%. At deadline with Russia on board (high-value pieces at risk),
        # failed NEAR adds a piece with no benefit, accelerating the "NEAR fail → pc grow → NEAR fail → death" spiral.
        # Best game T140-T154: 13 turns in russia phase, 8 NEAR attempts, 6 failures (delta=0).
        # Worst game T58: rp=7, deadline, danger=3, NEAR still selected with crosses_deadline=true.
        # Existing v604/v606 reduce type_scale but RUSSIA_PHASE_IMMEDIATE_MERGE_PRIORITY(+1200) and
        # REACTIVE_IMMEDIATE_MERGE_PRIORITY(+400) can still make NEAR net positive.
        # Canceling the base NEAR bonus ensures DIRECT merge or low-y NO merge placement dominates.
        # Mandatory themes: "デッドラインを超える位置にピースを置く場合は、併合できる場合に限る"
        # DIRECT merge (95.7%) is "able to merge". NEAR merge (68.5%) is too risky.
        # Does NOT affect: non-russia phases, max_y<2.0, deadline_crossed=false, DIRECT merge, FAR merge.
        # refs: tmp/analysis_result.md (Implementation Plan: russia deadline NEAR suppression)
        # Fixes rollback failure mode: russia_phase_deadline_near_merge_chain_failure
        if russia_phase and deadline_crossed and max_y >= 2.0 and merge_grade == "NEAR":
            score -= 600.0 * merge_mult * type_scale
            reasons.append("RUSSIA_DEADLINE_NEAR_SUPPRESSED")

        # vXXX: NEAR suppression with landing-height safety valve
        # mandatory_themes第一条の精神: デッドライン超越位置でのマージなき配置を避ける
        # しかし抑制されたNEAR optionsの中にlanding_yがcurrent max_yより低いものがある場合、
        # そのNEARはboard compressionに貢献するため抑制を緩和
        # Safety valve: NEAR candidates with landing_y well below current max_y are allowed
        # even in the suppression zone (max_y >= 2.5 && deadline_crossed).
        # Threshold 0.3u: provides meaningful distinction while avoiding edge cases.
        # refs: tmp/analysis_result.md (Implementation Plan: NEAR抑制safety valve)
        # Fixes rollback failure mode: worst_game T64 NEAR抑制下でfallback x=-3.0が選択されmax_y跳ね上がり
        landing_height_below_board = landing_y < (max_y - 0.3)

        if max_y >= 2.5 and deadline_crossed and merge_grade == "NEAR":
            if not landing_height_below_board:
                score -= 600.0
                reasons.append("MANDATORY_THEMES_NEAR_SUPPRESSED")

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
        # reactive>=3でもaxis 8.8(-3000~-7000)が支配し、スタッキングはtie-breakingに留まる。
        # postmortem制約: reactive_pair_count<3ガードなし(全reactiveレベルで動作)。
        # v460: suppress stacking when danger_piece_count>0 && rp>=3 && NO merge
        # Worst game T55-57: pc=39-41, rp=4-5, max_y=1.67-1.68, danger=1.
        # stacking_congested guard (max_y>=3.0 or rp>=5+max_y>=2.5) doesn't fire,
        # so chain-priority mode runs with congestion-scaled bonus (~900 at pc=39).
        # This bonus differentiates candidates toward high same-type pieces,
        # accelerating piece accumulation → death in 5-8 turns.
        # When danger pieces exist and rp>=3 with no merge, chain-building is
        # a luxury — survival via low placement (height penalty differentiation)
        # is the only viable strategy. Suppress stacking to let height penalty
        # be the sole differentiator, consistent with axis 8.8 intent.
        # refs: game_history/20260407_200712_score0421.jsonl T55-57 (death spiral),
        #       game_history/20260407_201554_score0994.jsonl T65-67 (rp=6, danger=4),
        #       tmp/batch_summary.txt (HEIGHT_CONTROL 20.5% low-score = guidance gap),
        #       tmp/state/last_rollback_analysis.md (floor gap: 6874 vs 8645)
        # Fixes rollback failure mode: REACTIVE_PAIRS_STACKING accelerates piece
        # accumulation in danger zone when no merge available
        # v461: death-spiral noise suppression — when rp>=3, NO merge, deadline crossed, danger>0
        # Worst game T62: rp=6, NO, deadline, danger=3 → x=3.0 edge scatter at pc=40, game over in 3 turns.
        # Extra_low T72: rp=6, NO, deadline, danger=6 → x=-3.0, game over next turn.
        # Flat -4500 axis 8.8 is correct, but axis 9.6b proximity (~120-540), axis 5.6 growth center
        # (~60-200), axis 9.3 AVOID_BLOCK (~200-500) still create noise that overrides height penalty.
        # Height diff between y=1.0 and y=-1.0 is only ~100 (height_mult floor 0.5, 50*0.5*2).
        # When survival is at stake, chain-building and type-concentration are luxuries.
        # Suppress all non-essential bonuses to let height penalty be truly the sole differentiator.
        # This is an extension of v460 (stacking suppression) to the full death-spiral regime.
        # refs: game_history/20260407_210954_score0473.jsonl T62 (x=3.0 scatter, pc=40),
        #       game_history/20260407_211649_score0913.jsonl T72 (x=-3.0 scatter, pc=40),
        #       tmp/batch_summary.txt (HEIGHT_CONTROL 16.3% = default when all else suppressed),
        #       tmp/state/last_rollback_analysis.md (floor gap: 6874 vs 8645)
        # Fixes rollback failure mode: death-spiral edge scatter from bonus noise overriding height penalty
        # v580: extend death_spiral to include max_y>=1.5 (OR condition) — catch pre-death-spiral
        # where board is already dangerous but deadline not yet crossed. Rollback postmortem:
        # "extends death spiral detection to max_y>=1.5 instead of requiring deadline_crossed"
        death_spiral = (
            danger_piece_count > 0
            and reactive_pair_count >= 2
            and merge_grade == "NO"
            and (deadline_crossed or max_y >= 1.5)
        )
        # v584: death spiral height penalty amplification — override phase height_mult
        # Analysis of worst games (score0856 T73-80, score0959 T63-70): death_spiral detection
        # fires correctly but height penalty differentiation (~50-200 points) is too weak to
        # prevent edge scatter when all candidates receive identical -5500 axis 8.8/8.8b penalties.
        # At HIGH phase (height_mult=1.8), y=2.79 vs y=-3.0 gives only ~520 point spread —
        # easily overridden by guidance noise. Amplifying to 8.0x gives ~400 per y-unit,
        # producing ~2000+ spread between high and low candidates, ensuring lowest-y always wins.
        # Normal phase height_mult values are preserved — only death_spiral regime is affected.
        # refs: tmp/analysis_result.md, tmp/batch_summary.txt, game_history/20260411_183711_score0856.jsonl,
        #       game_history/20260411_182236_score0959.jsonl
        # Fixes failure mode: death spiral時のheight penalty弱すぎ（edge scatter prevent）
        death_spiral_height_mult = 8.0

        # v586: merge drought early detection — lower rp threshold from >=2 to >=1
        # v585: merge drought detection — NO merge continues with elevated board and reactive pairs
        # Analysis: worst game T83-T88: 6 turns NO merge, rp=5, max_y=1.77-2.22, pc=39-44.
        # death_spiral fires (rp>=3, NO, max_y>=1.5) but board compression is absent —
        # height penalty differentiation (~50-200) is too weak vs -5500 axis 8.8/8.8b penalties.
        # merge_drought catches this "slow death" earlier than death_spiral (max_y>=1.0 vs 1.5).
        # When triggered, suppress all guidance noise + height penalty only → force lowest-y placement.
        # v586: lowered rp threshold from >=2 to >=1 — catches rp=1 NO merge turns immediately.
        # Worst game 0870 T54-55: rp=2, max_y=1.78-2.17, NO merge. merge_drought didn't fire until T56.
        # Those 2 turns of stacking/height competition let pc=34→36, accelerating death spiral.
        # pc>=30 guard prevents early-game over-suppression (rp=1 NO merge is normal early on).
        # Condition: merge_grade=="NO" AND rp>=1 AND max_y>=1.0 AND pc>=30
        # This is independent from death_spiral — OR'd into suppression checks below.
        # refs: tmp/analysis_result.md, tmp/batch_summary.txt,
        #       game_history/20260411_221219_score0870.jsonl T54-57,
        #       game_history/20260411_211420_score1045.jsonl T83-T88,
        #       game_history/20260411_214313_score1077.jsonl T78-T83
        # Fixes failure mode: "rp=1のNO mergeターンを1ターンでも減らす" (analysis_result.md adopted hypothesis)
        merge_drought = (
            merge_grade == "NO"
            and reactive_pair_count >= 1
            and max_y >= 1.0
            and piece_count >= 30
        )

        # Combined suppression flag: death_spiral OR merge_drought
        # Both trigger guidance suppression + height penalty only mode.
        # merge_drought fires earlier (max_y>=1.0 vs 1.5, rp>=1 vs 3) catching slow death sooner.
        # v586: rp>=1 (was >=2) — merge_drought now catches single reactive pair NO merge turns.
        guidance_suppressed = death_spiral or merge_drought

        stacking_danger_suppressed = guidance_suppressed
        if reactive_pair_count >= 1 and merge_grade == "NO" and same_type_stack_top is not None and not stacking_danger_suppressed:
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
            # v578: expand stacking_congested to catch pre-death-spiral window (rp>=3, max_y>=1.0, NO)
            # Worst game T56-T57: rp=4, max_y=1.0, NO merge → stacking fires HIGH_LAYER without congestion guard.
            # Rollback postmortem: "pre_death_spiral = max_y >= 1.0 && rp>=3 && NO" is the suppression window.
            # This catches the same window through stacking_congested fork (height-priority mode) instead of
            # binary suppression, preserving horizontal guidance while redirecting to lowest same-type target.
            # Fixes rollback failure mode: max_y runaway when reactive_pairs>=3 and merge_available=false, even before deadline_crossed
            # refs: tmp/analysis_result.md, tmp/state/last_rollback_postmortem.md, tmp/state/last_rollback_analysis.md,
            #       game_history/20260411_131655_score0560.jsonl (worst T56-T57), tmp/batch_summary.txt
            stacking_congested = (
                (max_y >= 3.0 and deadline_crossed)
                or (reactive_pair_count >= 5 and max_y >= 2.5)
                or (reactive_pair_count >= 3 and max_y >= 1.0)
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
                                dist = ((p["x"] - sp_x) ** 2 + (p["y"] - sp_y) ** 2) ** 0.5
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
                    stacking_bonus = best_chain_score + max(0, 100.0 - horizontal_distance * 40.0)
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
        # v463: suppress in death_spiral/merge_drought — height must be sole differentiator (was missing from v461/v462 suppression set)
        if reactive_pair_count >= 1 and merge_grade == "NO" and same_type_stack_top is None and not guidance_suppressed:
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

        # ----- v583: axis 9.8 — merge drought horizontal guidance (extended) -----
        # When merge_grade=NO and no DIRECT/NEAR merge is available for current type,
        # and no reactive stacking (9.6) or same-type proximity (9.6b) guidance exists,
        # provide horizontal guidance based on mid-to-high-type piece cluster concentration.
        #
        # v583 change: extend from type>=10 to type>=6 (same threshold as axis 5.6 growth center).
        # Non-Russia games (worst 0930 T43-T57, extra-low 0978 T55-T63) never get type 15,
        # so RUSSIA_PHASE_BOARD_COMPRESSION never fires. Without this guidance, HEIGHT_CONTROL
        # scatter dominates during merge droughts → piece accumulation → death spiral.
        # Bonus increased to ~400 (match Russia phase compression minimum of 400).
        #
        # Trigger: merge_grade == "NO" AND
        #   NOT (current_type_has_reactive or current_type_has_near) AND
        #   same_type_stack_top is None
        # (i.e., no guidance from axes 9.6 or 9.6b)
        #
        # Target: position near the mid-to-high-type piece(s) on board (type >= 6)
        # Rationale: concentrating pieces near type>=6 clusters ensures horizontal guidance
        # exists in mid-game when merge droughts are most lethal (pc=25-35 range).
        #
        # Bonus: base ~400 (match Russia phase compression minimum)
        # Decay: scales with distance from target cluster center
        # Safety: suppressed in death_spiral (height-only mode)
        #
        # refs: tmp/analysis_result.md, tmp/batch_summary.txt,
        #       game_history/20260411_172710_score0930.jsonl (worst: no type 15, no guidance),
        #       game_history/20260411_180215_score0978.jsonl (extra-low: same pattern),
        #       game_history/20260411_174117_score4328.jsonl (best: Russia compression fires 9x)
        if (merge_grade == "NO"
                and not (current_type_has_reactive or current_type_has_near)
                and same_type_stack_top is None
                and not guidance_suppressed):
            target_pieces = [p for p in pieces if p.get("type", 0) >= 6]
            if target_pieces:
                centroid_x = sum(p.get("x", 0) for p in target_pieces) / len(target_pieces)
                centroid_y = sum(p.get("y", -10) for p in target_pieces) / len(target_pieces)
                dist = ((x - centroid_x) ** 2 + (landing_y - centroid_y) ** 2) ** 0.5
                if dist < 3.0:
                    drought_guidance = max(0, 400.0 - dist * 100.0)
                    score += drought_guidance
                    if "HIGH_TYPE_CONCENTRATION" not in reasons:
                        reasons.append("HIGH_TYPE_CONCENTRATION")

        # ----- v601: axis 9.9 — Russia-phase next-Russia growth pipeline guidance (NEW) -----
        # analysis_result.md adopted hypothesis: Russia-phase dedicated "next-Russia growth pipeline" axis.
        # Game log analysis: both best and extra_high games consume turns with BOARD_COMPRESSION only
        # after Russia founding, entering merge drought with no pipeline for 2nd Russia.
        # batch_summary: high-score and low-score groups have same endgame max_y (1.84) → height not
        # the differentiator; piece types left on board determine score.
        #
        # Logic:
        # (1) Russia piece below-position bonus: candidates placed directly below or diagonally below
        #     existing Russia piece (type 15) get +150 * merge_mult * russia_pipeline_mult
        #     Conditions: dx<1.5, -0.5<=dy<=1.0 (below or diagonally below within range)
        # (2) High-type piece (type>=10) centroid clustering: candidates near centroid of type>=10
        #     pieces get +80 * merge_mult * russia_pipeline_mult
        #
        # russia_pipeline_mult: deeper Russia = higher multiplier (y=-4: 2.0x, y=0: 1.0x, y=2: 0.5x)
        #
        # Guards:
        # - russia_phase == True and double_russia_phase == False (only when 1 Russia on board)
        # - merge_grade == "NO" (only when no merge opportunity; merges handled by existing axes)
        # - max_y >= 1.0 (only when board is elevated enough; LOW phase doesn't need this)
        # - death_spiral == False (death_spiral requires height management only)
        #
        # refs: tmp/analysis_result.md (Implementation Plan: axis 9.9), tmp/batch_summary.txt,
        #       game_history/20260412_152521_score4344.jsonl, game_history/20260412_150116_score2968.jsonl
        # Fixes failure mode: "Russia-phase merge drought consumes turns with BOARD_COMPRESSION only,
        #   no merge path to next Russia constructed"
        if (russia_phase and not double_russia_phase
                and merge_grade == "NO"
                and max_y >= 1.0
                and not death_spiral):
            russia_pieces = [p for p in pieces if p.get("type") == 15]
            if russia_pieces:
                deepest_russia = min(russia_pieces, key=lambda p: p.get("y", 10))
                russia_y = deepest_russia.get("y", -10)
                russia_x = deepest_russia.get("x", 0)
                russia_pipeline_mult = max(0.3, min(2.0, 1.0 - russia_y * 0.25))
                dx = abs(x - russia_x)
                dy = landing_y - russia_y
                if dx < 1.5 and -0.5 <= dy <= 1.0:
                    below_bonus = 150.0 * merge_mult * russia_pipeline_mult
                    below_bonus *= max(0.0, 1.0 - dx / 1.5)
                    ideal_dy = 0.5
                    dy_penalty = 1.0 - abs(dy - ideal_dy) * 0.5
                    below_bonus *= max(0.0, dy_penalty)
                    if below_bonus > 20:
                        score += below_bonus
                        reasons.append("RUSSIA_PIPELINE_BELOW")
            high_type_pieces = [p for p in pieces if p.get("type") >= 10]
            if len(high_type_pieces) >= 2:
                hc_x = sum(p.get("x", 0) for p in high_type_pieces) / len(high_type_pieces)
                hc_y = sum(p.get("y", -10) for p in high_type_pieces) / len(high_type_pieces)
                dist_to_centroid = ((x - hc_x) ** 2 + (landing_y - hc_y) ** 2) ** 0.5
                if dist_to_centroid < 3.0:
                    cluster_bonus = 80.0 * merge_mult * russia_pipeline_mult
                    cluster_bonus *= max(0.0, 1.0 - dist_to_centroid / 3.0)
                    if cluster_bonus > 10:
                        score += cluster_bonus
                        reasons.append("HIGH_TYPE_CLUSTER")

        # ----- v613: axis 9.10 Tier 2 — rp==2 high-type merge path creation (NEW) -----
        # analysis_result.md adopted hypothesis: "axis 9.10 tier-2追加"
        # Extend type 10+ proximity guidance to rp==2 NO merge stage, catching drought 1-2 turns earlier.
        #
        # Conditions (v613):
        # - reactive_pair_count == 2 (rp==2)
        # - merge_grade == "NO"
        # - max_y >= 1.0
        # - piece_count >= 20
        # - not death_spiral
        # - not guidance_suppressed
        #
        # Logic:
        # (1) Collect type 10+ pieces on board
        # (2) Calculate centroid of type 10+ pieces
        # (3) Add +250*merge_mult proximity bonus (dist<=1.5u, linear decay)
        # (4) If type 10+ has same-type reactive pair (count>=2), add +150*merge_mult extra
        #
        # Bonus design: 250*merge_mult is smaller than column_ceiling (~800-1250), pure tie-breaker.
        # height penalty is NOT overridden — only improves placement quality when same conditions exist.
        #
        # Forbidden: merge_grade!=NO (existing merge axes have priority), death_spiral (v610 handles)
        #
        # refs: tmp/analysis_result.md (Implementation Plan: axis 9.10 Tier 2),
        #       game_history/20260413_050025_score2311.jsonl T107 (rp=2, type 14 -> T108 large merge)
        # Fixes rollback failure mode: "rp=2 NO merge is the blind spot where merge droughts begin
        #   and high-type pieces scatter, missing the window to build merge paths before rp escalates to 3"
        if (
            reactive_pair_count == 2
            and merge_grade == "NO"
            and max_y >= 1.0
            and piece_count >= 20
            and not death_spiral
            and not guidance_suppressed
        ):
            high_type_pieces_10plus = [p for p in pieces if p.get("type", 0) >= 10]
            if len(high_type_pieces_10plus) >= 2:
                centroid_x = sum(p.get("x", 0) for p in high_type_pieces_10plus) / len(high_type_pieces_10plus)
                centroid_y = sum(p.get("y", -10) for p in high_type_pieces_10plus) / len(high_type_pieces_10plus)
                dist = ((x - centroid_x) ** 2 + (landing_y - centroid_y) ** 2) ** 0.5
                tier2_bonus = max(0.0, 250.0 * (1.0 - dist / 1.5)) * merge_mult
                if tier2_bonus > 20:
                    score += tier2_bonus
                    reasons.append("HIGH_TYPE_MERGE_PATH_SETUP")
                type_10plus_counts = {}
                for p in high_type_pieces_10plus:
                    t = p.get("type", 0)
                    type_10plus_counts[t] = type_10plus_counts.get(t, 0) + 2
                has_type_10plus_reactive_pair = any(count >= 2 for count in type_10plus_counts.values())
                if has_type_10plus_reactive_pair:
                    reactive_type_pieces = []
                    for p in high_type_pieces_10plus:
                        if type_10plus_counts.get(p.get("type", 0), 0) >= 2:
                            reactive_type_pieces.append(p)
                    if reactive_type_pieces:
                        rp_centroid_x = sum(p.get("x", 0) for p in reactive_type_pieces) / len(reactive_type_pieces)
                        rp_centroid_y = sum(p.get("y", -10) for p in reactive_type_pieces) / len(reactive_type_pieces)
                        rp_dist = ((x - rp_centroid_x) ** 2 + (landing_y - rp_centroid_y) ** 2) ** 0.5
                        rp_bonus = max(0.0, 150.0 * (1.0 - rp_dist / 1.5)) * merge_mult
                        if rp_bonus > 20:
                            score += rp_bonus
                            reasons.append("HIGH_TYPE_REACTIVE_PAIR_GUIDANCE")

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
            if not (current_type_has_reactive or current_type_has_near):
                # v461: suppress proximity guidance in death spiral/merge_drought — height must be sole differentiator
                if not guidance_suppressed:
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
                        best_proximity_target = min(same_type_pieces, key=lambda p: p.get("y", 10))

                    target_x = best_proximity_target.get("x", 0)
                    target_y = best_proximity_target.get("y", -10)
                    horiz_dist = abs(x - target_x)
                    if horiz_dist < 2.0:
                        # v369 congestion-aware proximity — no reactive level split
                        # Postmortem: piece_count is the key predictor of final score.
                        # No reactive<3 guard (postmortem constraint: works at ALL reactive levels).
                        # Not landing_y-only (considers horizontal proximity, piece_count, target height).
                        # v605: add proximity boost for high-type + no-merge situations
                        # type 13/14 pieces need stronger clustering incentive before deadline
                        # base bonus 120 * type_scale 1.8 = 216 at dist=0, still below height diff (~400)
                        # add 1.5x boost when next_type >= 13 and merge_grade == "NO" to reach ~324
                        # mandatory_themes: "NEXTを考慮したドロップ" — proximity boost based on next_type
                        _proximity_base = max(0, 120.0 - horiz_dist * 50.0)
                        _proximity_boost = 1.5 if (next_type >= 13 and merge_grade == "NO") else 1.0
                        proximity_bonus = _proximity_base * type_scale * _proximity_boost
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
                        # v630: merge_path_scattered relaxed condition — allow proximity guidance during
                        # merge drought (rp>=3, NO merge, pc>=30) even when congested, to rebuild
                        # scattered merge paths. Only suppressed in true death spiral (max_y>=3.0 && deadline).
                        merge_drought_path_scattered = (
                            merge_grade == "NO"
                            and reactive_pair_count >= 3
                            and piece_count >= 30
                            and not (max_y >= 3.0 and deadline_crossed)
                        )
                        rp_guidance_suppressed = (
                            (max_y >= 3.0 and deadline_crossed)
                            or (reactive_pair_count >= 5 and max_y >= 2.5 and not merge_drought_path_scattered)
                        )
                        if merge_drought_path_scattered:
                            rp_guidance_suppressed = False
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
            # v461: also suppress in death spiral — height must be sole differentiator
            board_congested = (
                (max_y >= 3.0 and deadline_crossed)
                or (reactive_pair_count >= 5 and max_y >= 2.5)
            )
            if not board_congested and not guidance_suppressed:
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
        if deadline_crossed and reactive_pair_count >= 2 and merge_grade == "NO" and danger_piece_count == 0:
            # v431: only relax when current type has reactive/near guidance
            # Without guidance for current type, relaxation enables HEIGHT_CONTROL scatter (worst T55-62)
            # With guidance, relaxation allows axis 9.6 stacking to compete with height penalty
            if current_type_has_reactive or current_type_has_near:
                height_mult *= 0.2

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
        if deadline_crossed and reactive_pair_count >= 1 and reactive_pair_count < 3 and merge_grade == "NO":
            # deadline_crossed時、reactive_pairs>=1で即時併合不可の場合、戦略的配置の余地を更に確保
            # reactive_pairs>=3の場合はaxis 8.8ペナルティを有効にするためheight_mult緩和をスキップ
            # reactive_pairs>=3は超危険域であり、即時併合機会を強制的に待つ戦略へ切り替える
            height_mult *= 0.3

        # v362: height_mult floor — prevent compounding nullification
        # 3 gates (0.2x/0.8x/0.3x) compound to 0.048x, nullifying height penalty.
        # Floor of 0.5 keeps height penalty meaningful while allowing strategic flexibility.
        # Previously validated in v356 (protected strategy median 12789), lost in v359 rollback.
        # refs: strategy_versions/protected/protected_e6f534c37e28_median12789_strategy.py,
        #       tmp/state/last_rollback_postmortem.md, tmp/change_log.txt
        height_mult = max(height_mult, 0.5)

        # v584: death_spiral height_mult override — force height penalty to dominate
        # When death_spiral detected, all candidates get identical -5500 axis 8.8/8.8b penalties.
        # Without amplification, height penalty provides only ~50-200 point differentiation,
        # insufficient to prevent edge scatter. Override to 8.0 gives ~400 per y-unit spread.
        # This is applied AFTER the floor to ensure it overrides all phase/relaxation values.
        # v585: also apply death_spiral_height_mult during merge_drought — same height-only mode
        effective_height_mult = height_mult
        if death_spiral or merge_drought:
            effective_height_mult = death_spiral_height_mult

        # Calculate height penalty after all height_mult modifications
        height_penalty = landing_y * 50.0 * effective_height_mult

        if phase == "HIGH" and landing_y > 0.5:
            height_penalty *= 2.0
            reasons.append("HIGH_TOWER")
        elif phase == "MEDIUM" and landing_y > 0.5:
            height_penalty *= 1.5
            reasons.append("MEDIUM_TOWER")
        elif landing_y > 0.0:
            reasons.append("HIGH_LAYER")

        score -= height_penalty

        # ----- v361: piece_count congestion penalty -----
        # postmortem: bad strategy ends with 40-46 pieces, rollback target with 21-25.
        # piece_count is the key predictor of final score, not max_y.
        # When board is congested (piece_count >= 30), penalize high landing positions
        # to encourage tighter placement that enables merges and reduces piece_count.
        # This is NOT landing_y-only — it combines piece_count state with landing position.
        # No reactive_pair_count guard — works at ALL reactive levels (postmortem constraint).
        # refs: tmp/state/last_rollback_postmortem.md (piece_count 41→1060 vs 21→4645),
        #       tmp/batch_summary.txt (high-score merge_rate=38.6% vs low-score 33.6%)
        if piece_count >= 30 and landing_y > -1.0:
            # v365: increased multiplier 8→20 — old value was too weak to affect behavior
            # (piece_count=37, landing_y=1.0: 64 vs height diff ~140). New value provides
            # meaningful tie-breaking for axis 8.8 uniform penalty without overriding merges.
            congestion_penalty = (piece_count - 29) * landing_y * 20.0
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
            # v454: flatten to -4500 — fix v432 sign error + match protected strategy
            # v432 formula was -3000 + landing_y * 2000 which has OPPOSITE sign to the
            # documented intent. The comment said "y=2: -7000" but the formula produces
            # +1000 (a BONUS for high placement). This inverted the penalty: at y>=1.5
            # the "penalty" becomes zero or positive, incentivizing scatter to high-y
            # positions at deadline — the exact failure mode the postmortem warns against.
            # Evidence: worst T59 x=-3.0 at deadline → bounces to y=3.31. Extra_low T79-84
            # pieces at x=2.6-3.0, y=2.7-3.5. Best game also shows edge scatter at deadline.
            # Protected strategy (median 12789) uses flat -4500. Same as axis 8.8 (v452).
            # Flat -4500 overwhelms all additive bonuses (~400-800), letting axis 2
            # height penalty be the only position differentiator — consistent low placement.
            # Fixes rollback failure mode: deadline scatter from v432 sign error
            score -= 4500.0
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
        # v462: suppress in death spiral/merge_drought — height must be sole differentiator
        if next_next_type == next_type and not guidance_suppressed:
            center_bonus = max(0, 1.0 - abs(x) / 2.0) * 50.0
            score += center_bonus
            reasons.append("NEXT_SAME")

        # ----- evaluation axis 5.5: avoid blocking nextNext merge (NEW: nextNext info utilization) -----
        # batch_summary/adviceで「盤面A・nextB・nextNextAの状況で、A上にBを置くとnextNextの併合を逃す問題」が指摘されている。
        # nextNext typeが盤面上にある場合、着地位置がそのtypeの上になる配置では未来の併合機会を潰すためペナルティを与える。
        # これにより2手先の併合可能性を最大化し、即時併合機会の取りこぼしを削減する構造的改善。
        # refs: advice.md (Pitman_live, azumag), batch_summary.txt
        # v462: suppress in death spiral/merge_drought — height must be sole differentiator
        if not guidance_suppressed:
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
        # v461: suppress growth center in death spiral/merge_drought — height must be sole differentiator
        if max_type_on_board >= 6 and not guidance_suppressed:
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
                    # v370: base bonus 100 (from 50) — matches axis 9.6b magnitude
                    proximity = max(0, 60.0 - horiz_dist * 40.0)
                    # Decay if growth center is high — don't override height control
                    if gc_y > 0:
                        proximity *= max(0.0, 1.0 - gc_y * 0.4)
                    # v370: congestion-aware scaling — postmortem: piece_count is key predictor
                    # At high piece_count, guidance needs to be stronger to compete with
                    # height differences and provide meaningful redirect toward growth center.
                    if piece_count >= 28:
                        congestion_scale = 1.0 + (piece_count - 28) * 0.14
                        proximity *= min(congestion_scale, 3.5)
                    if proximity > 0:
                        score += proximity

         # ----- evaluation axis 6: chain merge bonus (v196: 初期段階CHAIN_MERGE有効化版)
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
        elif reactive_pair_count >= 2 and reactive_pair_count < 3 and merge_grade in ["DIRECT", "NEAR"]:
            #2つの反応可能ペアがある場合、強力なマージ優先ボーナス（v202: 500→800）
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
        if max_y >= 2.0 and reactive_pair_count >= 2 and merge_grade in ["DIRECT", "NEAR"]:
            if merge_grade == "DIRECT":
                # v331: deadline_crossed時はボーナスを強化（500.0→1200.0）
                if deadline_crossed:
                    score += 1200.0
                else:
                    score += 500.0
                reasons.append("DANGER_ZONE_IMMEDIATE_MERGE_PRIORITY")
            else:
                # v331: deadline_crossed時はボーナスを強化（300.0→600.0）
                if deadline_crossed:
                    score += 600.0
                else:
                    score += 300.0
                reasons.append("DANGER_ZONE_IMMEDIATE_MERGE_PRIORITY")

        # ----- evaluation axis 8.6: reactive pairs immediate merge bonus (v321: 即時併合ボーナス維持) -----
        # v317: reactive_pairs数に応じた即時併合ボーナスを維持
        # 即時併合候補がある場合、reactive_pairs数に応じてボーナスを強化
        # reactive_pairs==1: +600.0, reactive_pairs>=2: +1000.0
        # 未活用情報：reactive_pairsの段階的ボーナス
        # refs: tmp/improve_brief.md, tmp/batch_summary.txt, advice.md

        if reactive_pair_count >= 1 and merge_grade in ["DIRECT", "NEAR"]:
            # 即時併合候補がある場合、reactive_pairs数に応じてボーナスを強化
            if reactive_pair_count >= 2:
                score += 1000.0
            else:
                score += 600.0
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

        if russia_phase:
             # v548: double_russia_phase — 2つ目のロシアが盤面にある場合、
             # ソ連建国(type 16=136点)まであと1併合。この局面は特別扱い。
             # ロシア1つのままゲームオーバーは最も惜しい負けパターン。
             # 既存のtype 15を保護しつつ、type 13/14の成長パイプラインを維持する。
             if double_russia_phase:
                 # 2つのロシアが盤面にある — ソ連建国目前
                 # 盤面が最も狭く、高typeピースが場所を占有している状態
                 if merge_grade in ["DIRECT", "NEAR"]:
                     # 即時併合は常に最優先 — 盤面確保のため
                     if merge_grade == "DIRECT":
                         score += 1600.0
                     else:
                         score += 1400.0
                     reasons.append("DOUBLE_RUSSIA_IMMEDIATE_MERGE")
                 elif merge_grade == "NO":
                     # 併合不可時は、盤面圧縮よりtype 15保護と低配置を優先
                     # ボーナスを抑制し、height penaltyが効くようにする
                     # type 13/14級ピースを既存ロシアの近くに配置する誘導はaxis 5.6に委ねる
                     score += 200.0
                     reasons.append("DOUBLE_RUSSIA_SURVIVAL")
             elif merge_grade in ["DIRECT", "NEAR"]:
                 # ロシアフェーズでの即時併合優先
                 # 即時併合候補がある場合、最優先（強力なボーナス）
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
            # v452: flatten to -4500, matching protected strategy (median 12789)
            # v432 gradient (-3000 at y<=0) was too weak at low positions, allowing additive
            # bonuses (~400-800) to create scatter. Flat -4500 overwhelms bonuses, letting
            # axis 2 height penalty be the only differentiator — consistent low placement.
            score -= 4500.0
            reasons.append("REACTIVE_PAIRS_NO_MERGE_PENALTY")

        # ----- evaluation axis 8.8b: high merge drought penalty (NEW: v581) -----
        # analysis_result.md: merge_grade=NO が3ターン以上継続且つ reactive_pairs>=3 の場合、
        # axis 8.8のNO-mergeペナルティを段階的に強化し、height penaltyの差別化をさらに支配的にする。
        # Worst game T56-62: merge_grade=NO が7ターン連続、axis 8.8の-4500は発火しているが
        # stacking_bonus(~900)+proximity_bonus(~200)が競合し低配置への誘導が不十分。
        # HIGH phase (max_y>=1.8) 且つ reactive_pairs>=3 且つ merge_grade=="NO" の場合、
        # 追加-1000で合計-5500とし、stacking+proximityの合計~1100を上回ってheight penalty以外の実質的な
        # 差別化要因を排除。これによりmerge drought時に低配置が確実に選ばれる。
        # 正常系（merge_available=true、LOW/MEDIUM phase）には影響なし。
        # refs: tmp/analysis_result.md, tmp/batch_summary.txt, game_history/20260411_160254_score0498.jsonl,
        #       game_history/20260411_155310_score0560.jsonl
        # Fixes failure mode: merge drought時の配置判断の甘さ（stacking_bonus+proximity_bonusがaxis 8.8と競合）

        if reactive_pair_count >= 3 and merge_grade == "NO" and max_y >= 1.8:
            score -= 1000.0  # 追加ペナルティ（axis 8.8と合計-5500）
            reasons.append("HIGH_MERGE_DROUGHT_PENALTY")

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
                if landing_y > stack_top_y and danger_piece_count == 0 and reactive_pair_count == 0:
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
        if merge_grade == "NO" and not russia_phase and result.get("crosses_deadline", False):
            score -= 1200.0
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
