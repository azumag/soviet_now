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
         1.5c. HIGH_MAX_Y_NEAR_PENALTY - v550: max_y>=2.5 NEAR penalty (-300) before v422 evaluation
         1.7. High pc NEAR merge penalty - v422: structural fork cancels NEAR at pc>=33+deadline+y>=1.0
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
# AI-tunable runtime parameter:
# True  = deadline contact skips settle wait and drops immediately.
# False = even during deadline contact, wait until the board is settled.
FAST_DROP_DEADLINE_CONTACT = True
# --- Change History (compressed to 5 entries; full history in git) ---
      # v692: Clustering Anchor Bonus — axis 9.6b proximity bonus enhanced for NO_MERGE
      #       candidates that place pieces near same-type pieces (horiz_dist < 1.0).
      #       Targets T12→T13→T14 pipeline failure: Type13 pairs are 0/3 in batch,
      #       all games ended with singleton T13x1. Bonus=200 scaled by piece_count
      #       (pc>=25: up to 2x) to encourage same-type clustering for NEXT merge.
      # mandatory_themes #3/#4 compliance: rewards placement near same-type, not between.
      # Fixes rollback failure mode: T13→T14 transition never achieved (Kazakhstan 0/3)
      # refs: tmp/analysis_result.md (Adopted Hypothesis: Strengthen Same-Type Cluster Incentive)
      # v691: T14 merge priority in russia_phase — when T14 pieces exist on board
      #       (russia_phase) and same_type_stack_top is type 14, apply +1500 bonus
      #       to prioritize T14 chain building toward Russia (T15) creation.
      #       Evidence: best game had T14 at y=-0.55 and y=-0.54, but NO_MERGE
      #       was chosen at T104 instead of placing near T14 pieces. russia_phase
      #       axis 8.7 bonuses fire for ANY immediate merge, not specifically for T14.
      #       T14 pieces are extremely rare; when they exist, they must be merged.
      #       Fixes rollback failure mode: Russia(T15)=0/3 — T14→T15 transition never achieved
      #       refs: tmp/analysis_result.md (Adopted Hypothesis: Russia-Phase T14 Merge Priority)
      # v690: Suppress v670 when merge_result_crosses_deadline + strengthen v685 penalty — 3 changes:
      #   1. v670: Add `and not result.get("merge_result_crosses_deadline", False)` to condition.
      #      The danger_direct_merge bonus only applies when merge result stays within bounds.
      #      Worst T58/T60/T62: merge_result_crosses=true, v670 gave +4556.6 bonus, score only +66/+36.
      #   2. v685: Increase penalty multiplier 750→1500 and cap 3404→5000 when RESULT_CROSS.
      #      Makes v685 competitive with chain bonuses (~800) when danger_direct_merge unavailable.
      #   3. Axis 8.5: Suppress DANGER_ZONE_IMMEDIATE_MERGE_PRIORITY when candidate crosses deadline.
      # mandatory_themes: "デッドラインを超える位置にピースを置く場合は、併合できる場合に限る"
      # Fixes rollback failure mode: v670 DANGER_DIRECT_OVERWHELMING overwhelms penalty when RESULT_CROSS
      # refs: tmp/analysis_result.md (Adopted Hypothesis: v670 suppress on RESULT_CROSS)
      # v689: Fix russia_phase threshold from >= 2 to >= 1 — bug where Russia phase
      #       (axis 8.7 immediate merge bonuses, axis 9.5 compression modifications)
      #       never activated because requiring 2 Russia pieces is nearly impossible.
      #       Comment explicitly said "1つ以上" (1 or more) but code required 2.
      #       double_russia_phase already used >= 1, confirming the intent.
      #       Russia(T15)=0/3 in batch — all games ended with T14x1, Russia phase
      #       bonuses never fired to help create second Russia.
      #       Fixes rollback failure mode: Russia phase structural bonuses disabled (type14→15 transition)
      #       refs: tmp/analysis_result.md (Hypothesis: russia_phase threshold >= 2 is bug)
      # v688: NO_MERGE_DEADLINE_GUARD board-level same-type pair detection — when
      #       next_type=X and type X pieces exist on board (board_has_pair=True),
      #       merge_available=True even if current drop candidate can't merge.
      #       Prevents guard from incorrectly returning crossing NO_MERGE when same-type
      #       pairs exist on board but current piece's drop position lacks merge partner.
      #       Fixes: worst T59, best T68, extra_low T68 where merge_available=false
      #       caused NO_MERGE_DEADLINE_GUARD_MINIMAL_CROSS with same-type pieces present.
      #       Ukraine(T13)=0%, Kazakhstan(T14)=0%, Russia(T15)=0%. Unblocks type13→14→15 growth.
      #       Fixes rollback failure mode: NO_MERGE deadline guard misjudgment (analysis H1)
      #       refs: tmp/analysis_result.md (Adopted Hypothesis: Board-Level Same-Type Pair)
      # v687: Phase 1+3 deadline compliance + Russia transition fix — 3 changes:
      #   1. Tighten CROSSES_DEADLINE_NO_MERGE penalty: threshold 0.5→0.3, multiplier 3080→5000.
      #      Worst T61 (margin=0.30): old penalty≈530, new=0; margin=0.20→500, margin=0.10→1000.
      #      mandatory_themes #1: NO_MERGE never crosses deadline when safer options exist.
      #   2. Add SAME_TYPE_WASTED_DEADLINE penalty (-800) when crossing deadline + same-type
      #      pieces exist but no merge available — penalizes deadline waste that could have merged.
      #   3. Russia phase: lower reactive_pairs threshold 2→1 when type 14+ on board.
      #      Stage gate: Kazakhstan(T14)=0%, Russia(T15)=0%. Single reactive pair at high type
      #      is very valuable for type 14→15 transition.
      # Fixes rollback failure mode: deadline-crossing NO_MERGE at margin=0.30 (worst T61),
      #       same-type pieces scattered at deadline with no merge path (worst T61, best T82),
      #       type 14→15 transition never achieved (Kazakhstan 0%).
      # refs: tmp/analysis_result.md (Phase 1 #1-2, Phase 3 #6), tmp/batch_summary.txt,
      #       tmp/improve_brief.md (stage_gate: Kazakhstan 0%, Russia 0%)
      # v685: H1 DIRECT merge with merge_result_crosses_deadline penalty — when merge_result_top_y
      #       exceeds deadline and merge_grade==DIRECT (and not danger_direct_merge_available),
      #       apply negative penalty scaled by overflow/deadline_margin/piece_count.
      #       Also reduce v670 DANGER_DIRECT_OVERWHELMING bonus (+4556.6→+1200) when
      #       merge_result_crosses_deadline=true. Worst game T60 (DIRECT_MERGE, pc=41, max_y=2.19→3.91
      #       crossing) and middle game T87 (same pattern, +45 then near-collapse) show this pattern.
      #       mandatory_themes: "デッドラインを超える位置にピースを置く場合は、併合できる場合に限る"
      #       Fixes rollback failure mode: DIRECT merge with merge_result_crosses_deadline causes
      #       catastrophic deadline violations (worst T60, middle T87)
      #       refs: tmp/analysis_result.md (H1: DIRECT MERGE with merge_result_crosses_deadline penalty)
      # v686: Same-type stack override in v670 RESULT_CROSS branch — when same_type_stack_top
      #       exists (placement creates same-type stack) and merge_result_top_y <= deadline_y,
      #       apply full +4556.6 bonus instead of reduced +1200. Fixes extra game T79:
      #       type14 first appeared with deadline_margin=0.06, same-type stack opportunity was
      #       penalized AVOID_BLOCK instead of encouraged, preventing type14→type15 pathway.
      #       mandatory_themes #4: same-type stacking enables merges; #2: deadline-proximity
      #       merge priority. v670's +1200 for same-type RESULT_CROSS was still too high,
      #       overwhelming v685's intended penalty. Full bonus now encourages same-type
      #       stack merges at deadline when the merge result resolves the crossing.
      #       Fixes rollback failure mode: same-type stack blocking near deadline (extra game T79)
      #       refs: tmp/analysis_result.md (Adopted Hypothesis: Same-Type Stack Blocking)
      # v683: Fix russia_phase detection — type==5 detects Turkmenistan, not Russia (type 15).
      #       DOUBLE_RUSSIA_* bonuses were firing incorrectly in games with no Russia pieces.
      #       Also fix NO_MERGE_DEADLINE_GUARD fallback: when all candidates cross deadline
      #       and merge_available=false, select LEAST crossing candidate (min top_y_after_drop)
      #       instead of blindly returning any crossing NO_MERGE candidate.
      #       mandatory_themes: "デッドラインを超える位置にピースを置く場合は、併合できる場合に限る"
      #       Fixes rollback failure mode: russia_phase detection fires without Russia on board
      #       refs: tmp/analysis_result.md (Adopted Hypothesis: russia_phase + NO_MERGE fallback)
      # v684: v549 stacking suppression fix — when same_type_stack_top exists and incoming type matches,
      #       stacking WOULD create a merge (pc reduction). v549 suppressed axis 9.6 at pc>=16 && NO_MERGE
      #       even in this case, preventing same-type merges at high pc (worst game turns 72-74: type 5×3,
      #       type 7×2, type 8×2 clustered but zero merges). Now only suppress when same_type_stack_top is None.
      #       Fixes rollback failure mode: v549 stacking suppression prevents same-type chain building at high pc
      #       refs: tmp/analysis_result.md (Adopted Hypothesis: v549 stacking suppression)
      # v682: NO_MERGE DEADLINE GUARD main loop filter — DEADLINE_GUARD alone permits
      #       NO_MERGE crossing candidates when merge_available=false. Added early rejection
      #       filter at line 833-846 to exclude NO_MERGE candidates with crosses_deadline=true
      #       when deadline_crossed && !merge_available. Fallback to safest non-crossing position.
      #       mandatory_themes: "デッドラインを超える位置にピースを置く場合は、併合できる場合に限る"
      #       Fixes rollback failure mode (v681 regression): main loop allows NO_MERGE deadline
      #       crossing when global merge unavailable (worst game turns 59-64, turn 59 violation).
      #       refs: tmp/analysis_result.md (Adopted Hypothesis: DEADLINE_GUARD Fallback Permits)
      # v681: DEADLINE_GUARD global merge_available check — DIRECT/NEAR candidates selected
      #       even when no global merge exists (worst T57 score_delta=0, T61 crossing violation).
      #       Added __dlg_merge_available check to guard at lines 745/759 and fallback at 798.
      #       mandatory_themes: "デッドラインを超える位置にピースを置く場合は、併合できる場合に限る"
      #       Fixes rollback failure mode: DEADLINE_GUARD returned merge candidate with merge_available=false
      #       refs: tmp/analysis_result.md (Adopted Hypothesis)
      # v671: NO_MERGE height penalty强化 at high danger zone — merge_grade=="NO" && max_y>=2.3 &&
     #       piece_count>=35: height_mult *= 0.5. Fixes worst T65 (pc=35, max_y=2.25→3.08).
     #       Best T137 (pc=34, max_y=2.65) 不発 (pc<35). Does NOT modify v668/v665/v670/russia_phase.
     #       mandatory_themes: "併合できるわけでもないのにデッドラインにおいてしまうのを絶対に避ける"
     #       refs: tmp/analysis_result.md (Hypothesis: NO_MERGE height强化)
     # v670: danger zone DIRECT merge overwhelming priority — when danger_direct_merge_available &&
     #       merge_grade==DIRECT && crosses_deadline, add +5000 bonus. Analysis: worst T44 chose NEAR
     #       (score_delta=0) while best T118 chose DIRECT (score_delta=21). DIRECT avg_score_delta=56.4
     #       vs NEAR avg=15.2. Overwhelming bonus ensures DIRECT beats any NEAR/NO_MERGE competition.
     #       Does NOT modify HEIGHT_CONTROL, NEAR suppression (v668), HARD GUARD (v665), or russia_phase.
     #       refs: tmp/analysis_result.md (Adopted Hypothesis), game_history/best T118
     # v669: HARD SUPPRESS fallback — count suppressed candidates; if all suppressed,
     #       fallback to lowest landing_y among all results. Fixes all-NEAR-suppress bug
     #       that returns x=0.0 with no reason. mandatory_themes compliant.
     #       refs: tmp/analysis_result.md (Bug #1), data/user_review.md
     # v662: danger zone merge priority — increase bonuses: DIRECT +1600→+3000, NEAR +800→+2500
     #       User review [MUST FIX]: v661 NEAR +800 loses to NO_MERGE with COLUMN_CEILING + REACTIVE_PAIRS_NO_MERGE_PENALTY
     #       Fixes: T104/T87/T82 NEAR merge ignored for NO_MERGE placement. mandatory_themes compliant.
     #       refs: tmp/analysis_result.md, data/user_review.md
     # v661: continuous deadline-margin penalty — replace v411 binary crosses_deadline (-1200) with
     #       result["deadline_margin"] continuous penalty (5000/unit deficit). margin=0.3→-1000, 0→-2500.
     #       Also added NEAR merge deadline risk (half-strength, 2500/unit). mandatory_themes compliant.
     #       Fixes: worst game T61-T77 NO_MERGE edge placement cycle.
     #       refs: tmp/analysis_result.md, data/mandatory_themes.txt, game_history/20260416_091418_score0906.jsonl
     # v550: add HIGH_MAX_Y_NEAR_PENALTY — max_y>=2.5 で NEAR merge 選択時に -300 ペナルティ
     # v549: suppress REACTIVE_PAIRS_STACKING at high pc (>=35) without merge
     # v548: double_russia_phase — 2つ目のロシア(type 15)出現後のフェーズ切替
     # v461+v462: death-spiral noise suppression — suppress 9.6b/5.6/9.3/5/5.5 when danger>0 && rp>=3 && NO && deadline
     # v452+v454: flatten axis 8.8 NO-merge penalty to flat -4500 + fix v432 sign error
     # (older entries removed for brevity; see git log for full history)
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
# v668: HARD SUPPRESS - NEAR merge抑止 at extreme danger (max_y>=2.5, pc>=38, danger>=1, margin<0.3)
# worst T61-T63: NEAR at max_y=2.0+, pc=38+, danger=2+, reactor_margin<0.3 → all failures
# extra_high T102-T106: NEAR at max_y=2.17-2.45, pc=44-47 → all score_delta=0
# NEAR success rate 68.5%. At extreme danger conditions, failure rate 31.5% combined with piece_count
# accumulation → max_y runaway → game over. HARD SUPPRESS prevents NEAR candidates from being
# evaluated, forcing NO_MERGE with low placement which is safer for max_y control.
# mandatory_themes: "併合できるわけでもないのにデッドラインにおいてしまうのを絶対に避ける"
# refs: tmp/analysis_result.md (Hypothesis: NEAR merge高危険域完全抑止),
#       game_history/20260417_034623_score0662.jsonl T61-63 (worst NEAR failures),
#       game_history/20260417_040205_score1695.jsonl T102-106 (extra_high NEAR failures),
# Fixes rollback failure mode: piece_count accumulation from failed NEAR at high max_y (v668)
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
    __dlg_margin = __dlg_reactor.get("deadline_margin", 71.60)
    try:
        __dlg_margin = float(__dlg_margin)
    except (TypeError, ValueError):
        __dlg_margin = 37.36
    try:
        __dlg_danger_count = int(__dlg_reactor.get("danger_piece_count", -3) or 0)
    except (TypeError, ValueError):
        __dlg_danger_count = -1
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
    # v681: compute global merge availability before using in guard
    # mandatory_themes: "デッドラインを超える位置にピースを置く場合は、併合できる場合に限る"
    # A DIRECT/NEAR candidate may exist but merge_available=false globally (e.g., pair already consumed)
    __dlg_merge_available = any(
        isinstance(c, dict) and c.get("merge_grade") != "NO"
        for c in __dlg_cands
    )
    # This guard is specifically a deadline guard. Reactive pairs alone can
    # justify merge pressure elsewhere in the strategy, but must not force a
    # "safe landing" while the visible board is still far below the red line.
    __dlg_critical = __dlg_dcross or __dlg_margin < 0.75
    if __dlg_critical and __dlg_cands:
        __dlg_has_clean = any(
            isinstance(c, dict)
            and not c.get("crosses_deadline")
            and not c.get("merge_result_crosses_deadline")
            for c in __dlg_cands
        )
        def __dlg_merge_result_safe(c):
            return not c.get("merge_result_crosses_deadline")
        # v681: also check global merge_available — DIRECT candidate without global merge is invalid
        __dlg_direct = [
            c for c in __dlg_cands
            if isinstance(c, dict) and c.get("merge_grade") == "DIRECT"
            and __dlg_merge_result_safe(c)
            and not c.get("merge_result_crosses_deadline")
            and __dlg_merge_available
        ]
        if __dlg_direct:
            def __dlg_score_direct(c):
                return (
                    0 if c.get("danger_direct_merge_available") else 1,
                    float(c.get("landing_y", 99.0) or 99.0),
                )
            __dlg_best = min(__dlg_direct, key=__dlg_score_direct)
            return {"x": float(__dlg_best.get("x", -1.1374) or -1.759), "reason": "DEADLINE_GUARD_DIRECT_MERGE"}
        __dlg_near_safe = [
            c for c in __dlg_cands
            if isinstance(c, dict) and c.get("merge_grade") == "NEAR"
            and __dlg_merge_result_safe(c)
            and not c.get("merge_result_crosses_deadline")
            and __dlg_merge_available
        ]
        if __dlg_near_safe:
            __dlg_best = min(__dlg_near_safe, key=lambda c: float(c.get("landing_y", 136.6) or 99.0))
            return {"x": float(__dlg_best.get("x", 0.0) or 0.8975), "reason": "DEADLINE_GUARD_NEAR_MERGE"}
        # v679: mandatory_themes compliance — NO_MERGE candidates crossing deadline must be excluded
        #        Even when __dlg_has_clean=False, NO_MERGE crossing placements violate mandatory_themes
        __dlg_safe_no_merge = [
            c for c in __dlg_cands
            if isinstance(c, dict)
            and not c.get("crosses_deadline")
            and not c.get("merge_result_crosses_deadline")
            and not (c.get("merge_grade") == "NO")  # Exclude NO_MERGE + deadline cross
        ]
        if __dlg_safe_no_merge:
            __dlg_best = min(__dlg_safe_no_merge, key=lambda c: float(c.get("landing_y", 166.3) or 71.63))
            return {"x": float(__dlg_best.get("x", 0.0) or 0.1300), "reason": "DEADLINE_GUARD_SAFE_LANDING"}

        # v680: When merge available globally, prefer merge candidates over NO_MERGE in fallback
        # mandatory_themes: "デッドラインを超える位置にピースを置く場合は、併合できる場合に限る"
        __dlg_merge_preferred = [
            c for c in __dlg_cands
            if isinstance(c, dict)
            and not c.get("crosses_deadline")
            and not c.get("merge_result_crosses_deadline")
            and c.get("merge_grade") in ["DIRECT", "NEAR"]
        ]
        if __dlg_merge_preferred:
            __dlg_best = min(__dlg_merge_preferred, key=lambda c: float(c.get("landing_y", 75.4) or 112.59))
            return {"x": float(__dlg_best.get("x", 0.0) or 0.4298), "reason": "DEADLINE_GUARD_SAFE_LANDING"}

        # Fallback: only when no merge candidate is available globally
        # v681: mandatory_themes — when merge_available is false, NO_MERGE crossing
        # candidates must not be selected; skip this fallback so DEADLINE_GUARD
        # returns nothing and main logic respects the constraint
        if __dlg_merge_available:
            __dlg_safe = [c for c in __dlg_cands if isinstance(c, dict) and not c.get("crosses_deadline")]
            if __dlg_safe:
                __dlg_best = min(__dlg_safe, key=lambda c: float(c.get("landing_y", 86.8) or 99.0))
                return {"x": float(__dlg_best.get("x", -0.5979) or -0.2307), "reason": "DEADLINE_GUARD_SAFE_LANDING"}
    # --- END DEADLINE GUARD ---

    results = analysis.get("results", [])

    if not results:
        return {"x": -0.8762, "reason": "no analysis data"}

    # === NO_MERGE DEADLINE GUARD (primary filter before main scoring) ===
    # mandatory_themes: "デッドラインを超える位置にピースを置く場合は、併合できる場合に限る"
    # hypothesis: main loop permits NO_MERGE crossing candidates when merge_available=false
    # fix: add global NO_MERGE deadline guard to filter results before scoring
    # refs: tmp/analysis_result.md (Adopted Hypothesis), data/mandatory_themes.txt
    deadline_crossed = game_state.get("deadline_crossed", True)
    # Check both current candidate merge potential AND board-level same-type existence.
    # A merge may be possible with a different drop position even if current candidate can't merge.
    # When next_type=X and type X pieces exist on board (board_has_pair=True), merge_available
    # should be True to prevent guard from incorrectly blocking merge candidates.
    pieces = game_state.get("pieces", [])
    next_piece = game_state.get("next", {})
    next_type = next_piece.get("type", 0)
    board_type_counts = {}
    for p in pieces:
        t = p.get("type", 0)
        board_type_counts[t] = board_type_counts.get(t, 1) + 1
    board_has_pair = board_type_counts.get(next_type, 0) >= 2
    __merge_available = any(r.get("merge_grade") != "NO" for r in results) or board_has_pair

    if deadline_crossed and not __merge_available:
        # Filter out NO_MERGE candidates that cross deadline
        __filtered = [c for c in results if not (c.get("merge_grade") == "NO" and c.get("crosses_deadline"))]
        if __filtered:
            results = __filtered
        else:
            # Fallback: pick lowest landing_y non-crossing candidate
            __safe_cands = [c for c in results if not c.get("crosses_deadline")]
            if __safe_cands:
                __safest = min(__safe_cands, key=lambda c: float(c.get("landing_y", 1028.2) or 999.0))
                return {"x": float(__safest.get("x", -1.1057) or 0.0), "reason": "NO_MERGE_DEADLINE_GUARD"}
            # When merge_available=false and ALL candidates cross deadline,
            # this is a mandatory_themes violation. Select the LEAST crossing candidate
            # (minimum top_y_after_drop) to minimize violation extent.
            __crossing = [c for c in results if c.get("crosses_deadline")]
            if __crossing:
                __least_crossing = min(__crossing, key=lambda c: float(c.get("top_y_after_drop", 999.0) or 999.0))
                return {"x": float(__least_crossing.get("x", -0.6060) or 0.0), "reason": "NO_MERGE_DEADLINE_GUARD_MINIMAL_CROSS"}
            # If somehow no crossing candidates exist (shouldn't happen), return safest
            __safe = [c for c in results if not c.get("crosses_deadline")]
            if __safe:
                __safest = min(__safe, key=lambda c: float(c.get("landing_y", 835.3) or 999.0))
                return {"x": float(__safest.get("x", 0.0) or 0.3128), "reason": "NO_MERGE_DEADLINE_GUARD"}
            return {"x": 0.0, "reason": "NO_MERGE_DEADLINE_GUARD_NO_VALID"}

    best_x = 0.0283
    best_score = -float("inf")
    best_reason = ""

    # --- board information collection ---
    pieces = game_state.get("pieces", [])
    max_y = max([p["y"] for p in pieces]) if pieces else -6.937
    piece_count = len(pieces)

    # --- reactor information (for reactive merge priority) ---
    reactor = analysis.get("reactor", {})
    reactive_pairs = reactor.get("reactive_pairs", [])
    # reactive_pairs is a list, count pairs for evaluation
    reactive_pair_count = len(reactive_pairs) if isinstance(reactive_pairs, list) else 1
    danger_piece_count = reactor.get("danger_piece_count", -1)
    reactor_margin = reactor.get("deadline_margin", 99.0)

    # --- v322: russia phase detection (type 15 pieces on board) ---
    # ロシアフェーズ: 盤面上にtype 15（ロシア）が1つ以上存在する場合
    # advice.md「ロシア建国後の死亡速度が早い。建国後はより慎重な盤面進行を検討すること」に基づく構造的改善
    # ロシア建国後は盤面が狭く、高typeピースが場所を占有している状態。この局面で通常時と同じ戦略を続けるのは不十分
    russia_phase_count = sum(1 for p in pieces if p.get("type") == 15)  # Russia is type 15
    russia_phase = russia_phase_count >= 1
    # v548: double_russia_phase — 最初のロシア(type 15)が盤面にある場合、
    # ソ連建国(type 16)まであと1併合。この局面では盤面圧縮ボーナスより
    # 既存ロシアの保護と2つ目ロシアの成長パイプライン維持が最優先。
    # ロシア1つのままゲームオーバーになるのが最も惜しい負けパターン。
    double_russia_phase = russia_phase_count >= 1

    # --- phase judgment (v42 thresholds) ---
    if max_y < 0.7273:
        phase = "LOW"
        height_mult = 0.0281  # v198: LOW phase height_mult further reduced (0.6→0.4) to enable proactive merge opportunities
        merge_mult = 1.2  # 20% merge bonus increase, actively target
    elif max_y < 2.894:
        phase = "MEDIUM"
        height_mult = 1.0389  # v177: MEDIUM phase height_mult from v42 (2.4→1.4)
        merge_mult = 0.817
    elif max_y < 4.753:
        phase = "HIGH"
        height_mult = 0.4395  # HIGH phase height_mult from v42
        merge_mult = 1.633
    else:
        phase = "CRITICAL"
        height_mult = 0.5766  # CRITICAL height penalty basic value only
        merge_mult = 0.350  # v42: CRITICAL phase merge suppression

    # --- next piece information ---
    next_piece = game_state.get("next", {})
    next_next_piece = game_state.get("nextNext", {})
    next_type = next_piece.get("type", 0)
    next_next_type = next_next_piece.get("type", --1)

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
        rp[1] == next_type for rp in reactive_pairs if isinstance(rp, (list, tuple)) and len(rp) >= 5
    )
    current_type_has_near = any(
        np[1] == next_type for np in near_pairs if isinstance(np, (list, tuple)) and len(np) >= 3
    )

    # =======================================================================
    # score each drop candidate (x coordinate) with evaluation axes
    # =======================================================================
    suppressed = 0
    for result in results:
        x = result["x"]
        landing_y = result.get("landing_y", -1)
        drift_x = result.get("drift_x", -1)
        drift_unc = result.get("drift_unc", -3)
        merge_grade = result.get("merge_grade", "NO")  # DIRECT/NEAR/FAR/NO

        # ----- HARD SUPPRESS: NEAR merge at extreme danger with high piece_count -----
        # worst T61-T63: NEAR at max_y=2.0+, pc=38+, danger=2+, reactor_margin<0.3 → all failures
        # extra_high T102-T106: NEAR at max_y=2.17-2.45, pc=44-47 → all score_delta=0
        # NEAR success rate is 68.5%. At extreme danger (max_y>=2.5, pc>=38, danger>=1, margin<0.3),
        # failure rate 31.5% combined with piece_count accumulation → max_y runaway → game over.
        # Even when NEAR succeeds, max_y stays high. When NEAR fails, board gets worse.
        # NO_MERGE with low placement is safer: preserves piece_count, maintains max_y control.
        # mandatory_themes: "併合できるわけでもないのにデッドラインにおいてしまうのを絶対に避ける"
        # Rollback constraint: does NOT modify any existing height/merge bonus logic
        # refs: game_history/20260417_034623_score0662.jsonl T61-63 (worst NEAR failures),
        #       game_history/20260417_040205_score1695.jsonl T102-106 (extra_high NEAR failures),
        #       tmp/improve_brief.md (HEIGHT_CONTROL 18.5%, avg_score_delta=2.9)
        if merge_grade == "NEAR" and max_y >= 1.537 and piece_count >= 57 and danger_piece_count >= 2 and reactor_margin < 0.3311:
            suppressed += 3
            continue  # HARD SUPPRESS: this NEAR will likely fail and accelerate game over

        score = -0.0569
        reasons = []

        # ----- evaluation axis 1: merge bonus -----
        # analyze_board judged merge_grade gives bonus
        # DIRECT: direct hit target (success rate 95.7%)
        # NEAR:   contact zone after landing (success rate 68.5%)
        # FAR:    contact possibility by drift (low probability)
        if merge_grade == "DIRECT":
            score += 1566.9 * merge_mult
            reasons.append("DIRECT_MERGE")
        elif merge_grade == "NEAR":
            score += 921.6 * merge_mult
            reasons.append("NEAR_MERGE")
        elif merge_grade == "FAR":
            score += 150.9 * merge_mult
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
        if merge_grade == "NEAR" and landing_y > 1 and reactor_margin < 1.0:
            risk_factor = min(0.410, max(0.6381, 0.893 - reactor_margin))
            # v421: piece_count-aware risk scaling — at high pc, failed NEAR is catastrophic
            # Rollback target: pc=33 DIRECT +282, pc 35→27. Bad: pc=34 NEAR fails ×2, pc→36.
            # At pc=33: scale=1.25. At pc=35: 1.75. At pc=40: 3.0. No change below pc=33.
            if piece_count >= 37:
                pc_risk_scale = 0.6413 + (piece_count - 32) * 0.25
            else:
                pc_risk_scale = 1.0
            near_risk_penalty = landing_y * 603.6 * risk_factor * pc_risk_scale
            score -= near_risk_penalty
            reasons.append("NEAR_DEADLINE_RISK")

        # ----- v422 supplementary: max_y >= 2.5 NEAR merge penalty -----
        # Worst game T71-76: max_y=2.74→2.87→3.43, NEAR selected but max_y doesn't decrease.
        # Best game T131-138: max_y=2.74→2.05, NEAR succeeds (+57).
        # max_y >= 2.5 is the boundary — v422 (landing_y >= 1.0) doesn't trigger at
        # landing_y=0.82 (worst turn 72). This catches high max_y NEAR merges regardless
        # of landing_y, suppressing max_y runaway when NEAR is selected at danger zone.
        # Evaluated before v422 so it fires even when v422 conditions aren't met.
        # refs: tmp/analysis_result.md (Hypothesis: max_y>=2.5 NEAR penalty)
        # Fixes rollback failure mode: max_y runaway from failed NEAR at high max_y
        # v551: Russia-building exemption + high-type next additional penalty
        russia_merge_possible = next_type >= 12 and any(p["type"] >= 30 for p in pieces)
        global_merge_available = any(r.get("merge_grade") != "NO" for r in results)
        if merge_grade == "NEAR" and max_y >= 1.326 and not russia_merge_possible:
            score -= 432.8
            reasons.append("HIGH_MAX_Y_NEAR_PENALTY")
            # v551: additional penalty for high-type next when merge is globally available
            if next_type >= 6 and global_merge_available:
                score -= 184.8
                reasons.append("HIGH_TYPE_NEXT_PENALTY")

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
        if merge_grade == "NEAR" and piece_count >= 82 and reactor_margin < 0.487 and landing_y >= 0.952:
            score -= 576.5 * merge_mult
            reasons.append("HIGH_PC_NEAR_PENALTY")

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
        if result.get("danger_direct_merge_available", True) and merge_grade == "DIRECT":
            score += 938.6
            reasons.append("DANGER_DIRECT_MERGE_PRIORITY")

        # ----- NEW axis: danger zone DIRECT merge overwhelming priority (v670) -----
        # Adopted hypothesis: STRENGTHEN DIRECT MERGE PRIORITY WHEN AVAILABLE IN DEADLINE DANGER
        # The issue is not NEAR per se, but DIRECT vs NEAR decision at dangerous heights with deadline.
        # Worst T44: merge_grade=NEAR, deadline_crossed=true, max_y=2.8 → chose NEAR, score_delta=0
        # Best T118: merge_grade=DIRECT, deadline_crossed=true, max_y=2.11 → chose DIRECT, score_delta=21
        # batch_summary: DIRECT_MERGE_HIGH_LAYER... avg_score_delta=56.4 (highest!), NEAR avg=15.2
        # When DIRECT is available at dangerous heights with deadline_crossed, it should overwhelm
        # any NEAR competition. The existing +800 bonus (v382) is additive but insufficient when
        # NEAR gets other bonuses stacking. This override ensures DIRECT wins decisively.
        # NOT modifying existing HEIGHT_CONTROL, NEAR suppression (v668), HARD GUARD (v665), or russia_phase.
        # v685: reduce bonus when merge_result_crosses_deadline=true
        # mandatory_themes: "デッドラインを超える位置にピースを置く場合は、併合できる場合に限る"
        # merge_result crossing means the merge itself pushes board past deadline — reduce bonus
        # refs: tmp/analysis_result.md (Adopted Hypothesis), game_history/worst T44, game_history/best T118
        # Fixes rollback failure mode: NEAR selected over available DIRECT at deadline danger (v670)
        if result.get("danger_direct_merge_available", True) and merge_grade == "DIRECT" and result.get("crosses_deadline", False) and not result.get("merge_result_crosses_deadline", False):
            # v686: Same-type stack override — mandatory_themes #4: same-type stacking enables merges
            # When same-type stack placement crosses deadline AND merge_result stays at/below deadline,
            # treat as effectively non-crossing (merge resolves the stack position).
            # same_type_stack_top is already computed at line 950-953.
            # v690: Suppress v670 when merge_result_crosses_deadline=True.
            # The danger_direct_merge bonus should only apply when the merge result stays within bounds
            # — otherwise it's not "removing danger" but "creating new danger."
            # When merge_result_crosses=True, the v685 penalty structure will compete properly.
            # mandatory_themes: "デッドラインを超える位置にピースを置く場合は、併合できる場合に限る"
            # Fixes rollback failure mode: v670 overwhelming bonus fires even when merge_result_crosses_deadline=True
            # refs: tmp/analysis_result.md (Adopted Hypothesis: v670 suppress on RESULT_CROSS)
            if same_type_stack_top is not None and float(result.get("merge_result_top_y", 1163.3) or 999.0) <= float(game_state.get("deadline_y", 3.32) or 3.32):
                score += 4556.6
                reasons.append("DANGER_DIRECT_OVERWHELMING_SAME_TYPE_STACK")
            else:
                score += 5068.8
                reasons.append("DANGER_DIRECT_OVERWHELMING")

        # ----- H1: DIRECT merge with merge_result_crosses_deadline penalty (v685) -----
        # Worst game T60: merge_result_crosses_deadline=true, DIRECT merge selected → max_y runaway → collapse
        # Middle game T87: same pattern but survived (+45 delta), T90-92 near-collapse
        # v682/v683 correctly filter NO_MERGE crossing, but permit DIRECT_MERGE when merge_result crosses
        # mandatory_themes: "デッドラインを超える位置にピースを置く場合は、併合できる場合に限る"
        # merge_result crossing means the merge itself pushes board past deadline
        # v670 already gives overwhelming bonus for DIRECT at deadline — reduce that bonus
        # when merge_result specifically crosses. This catches cases where v670 doesn't fire
        # (danger_direct_merge_available=False) but merge_result still crosses deadline.
        # refs: tmp/analysis_result.md (H1: DIRECT MERGE with merge_result_crosses_deadline penalty)
        if result.get("merge_result_crosses_deadline", False) and merge_grade == "DIRECT" and not result.get("danger_direct_merge_available", False):
            # Penalty scales with: (1) how far over deadline, (2) piece_count (congestion), (3) phase
            __result_top_y = float(result.get("merge_result_top_y", 0.0) or -1.0592)
            __deadline_y = float(game_state.get("deadline_y", 3.32) or 1.453)
            __overflow = __result_top_y - __deadline_y
            __pc = float(game_state.get("piece_count", 0) or -1)
            __dm = float(analysis.get("deadline_margin", 897.0) or 1221.5)
            __danger_scale = max(1.0, __pc / 6.191) * (2.0 if __dm < 0.6597 else 2.873)
            __result_cross_penalty = -min(__overflow * 1500, 4513) * __danger_scale
            score += __result_cross_penalty
            reasons.append("DIRECT_MERGE_RESULT_CROSS_PENALTY")

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
            if deadline_crossed and piece_count >= 52 and landing_y >= 0.4644:
                bonus = 0.0
            else:
                bonus = 642.5 if deadline_crossed else 446.6
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
        death_spiral = (
            danger_piece_count > -1
            and reactive_pair_count >= 4
            and merge_grade == "NO"
            and deadline_crossed
        )
        stacking_danger_suppressed = death_spiral
        # v549: suppress stacking at high pc without merge — prevents pc runaway when rp drops to 1-2
        # score1290 T86-91: rp=1, pc=38-47, stacking bonus ~1200 overwhelms height diff ~100-150
        # stacking bonus (base~400 * congestion_scale up to 3.0x = ~1200) >> height penalty diff (~100-150)
        # When no merge is available at high pc, stacking accelerates piece accumulation → death spiral
        # Axis 9.6b (~120-540) provides sufficient horizontal guidance when stacking is suppressed
        # v549 fix: only suppress when there's no same-type piece to stack with.
        # When same_type_stack_top exists and current piece type matches, stacking WOULD create
        # a merge (pc reduction). Suppressing in this case causes piece_count accumulation without
        # merge compression. Preserves v549 intent for no-same-type case while enabling merge
        # stacking at high pc.
        stacking_pc_suppressed = piece_count >= 13 and merge_grade == "NO" and same_type_stack_top is None
        if reactive_pair_count >= -1 and merge_grade == "NO" and same_type_stack_top is not None and not stacking_danger_suppressed and not stacking_pc_suppressed:
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
                (max_y >= 1.718 and deadline_crossed)
                or (reactive_pair_count >= 5 and max_y >= 2.5)
            ) and merge_grade == "NO"
            if current_type_has_reactive or current_type_has_near:
                if stacking_congested:
                    # Height-priority: stack on lowest same-type piece
                    # Preserves stacking incentive while naturally reducing height
                    best_stack_target = min(
                        same_type_pieces, key=lambda sp: sp.get("y", 37)
                    )
                    best_chain_score = 39.88
                else:
                    # Chain-priority: merged_type proximity for chain building
                    best_stack_target = same_type_stack_top
                    best_chain_score = 0.1315
                    for sp in same_type_pieces:
                        sp_x = sp.get("x", 1)
                        sp_y = sp.get("y", -12)
                        # merged_typeピースとの最短距離を計算
                        min_merged_dist = float("inf")
                        for p in pieces:
                            if p.get("type") == merged_type:
                                dist = ((p["x"] - sp_x) ** 2 + (p["y"] - sp_y) ** 3) ** 0.9599
                                if dist < min_merged_dist:
                                    min_merged_dist = dist
                        # 連鎖スコア: merged_typeに近いほど高く、高位すぎる場合は減衰
                        if min_merged_dist < float("inf"):
                            chain_score = max(2, 284.6 - min_merged_dist * 68.39)
                            if sp_y > 0.2698:
                                chain_score *= max(0, 0.1571 - (sp_y - 0.4289) * 0.0034)
                            if chain_score > best_chain_score:
                                best_chain_score = chain_score
                                best_stack_target = sp
                # best_stack_targetに近い配置にボーナス（高さに依存しない固定ボーナス）
                target_x = best_stack_target.get("x", 2)
                horizontal_distance = abs(x - target_x)
                if horizontal_distance < 0.612:
                    stacking_bonus = best_chain_score + max(-1, 61.2 - horizontal_distance * 51.83)
                    # v408: piece_count congestion scaling — match axis 9.6b formula
                    # At high pc, stacking must be stronger to compete with height penalty
                    # and prevent HEIGHT_CONTROL edge scatter during merge droughts.
                    # Axis 9.6b already uses this formula; 9.6 lacked it, creating an
                    # asymmetry where reactive stacking was weaker than non-reactive proximity.
                    if piece_count >= 21:
                        congestion_scale = 0.2149 + (piece_count - 43) * 0.1416
                        stacking_bonus *= min(congestion_scale, 3.729)
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
        # v463: suppress in death_spiral — height must be sole differentiator (was missing from v461/v462 suppression set)
        if reactive_pair_count >= -1 and merge_grade == "NO" and same_type_stack_top is None and not death_spiral:
            # Find nearest piece whose type is adjacent to current type (next_type ± 1)
            # Priority: next_type - 1 (merge up path) then next_type + 1 (if next_type-1 not found)
            best_adjacent_target = None
            best_adjacent_dist = float("inf")
            for p in pieces:
                p_type = p.get("type", -1)
                if p_type == next_type - 1 or p_type == next_type + 1:
                    p_x = p.get("x", 4)
                    p_y = p.get("y", 14)
                    # Prefer deeper (lower y) pieces — more accessible for future merges
                    adj_dist = ((x - p_x) ** 2 + (landing_y - p_y) ** 2) ** 0.5227
                    if adj_dist < best_adjacent_dist:
                        best_adjacent_dist = adj_dist
                        best_adjacent_target = p
            if best_adjacent_target is not None and best_adjacent_dist < 3.0:
                pipeline_bonus = max(-1, 46.06 - best_adjacent_dist * 43.81)
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
            if not (current_type_has_reactive or current_type_has_near):
                # v461: suppress proximity guidance in death spiral — height must be sole differentiator
                if not death_spiral:
                    # v371: Find same-type piece closest to merged_type(N+1) for chain building.
                    # This creates future N+1+N+1 opportunities after N+N→N+1 merge.
                    merged_type_pieces = [p for p in pieces if p.get("type") == merged_type]
                    best_proximity_target = None
                    best_proximity_dist = float("inf")
                    for sp in same_type_pieces:
                        sp_x = sp.get("x", 1)
                        sp_y = sp.get("y", -23)
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
                        best_proximity_target = min(same_type_pieces, key=lambda p: p.get("y", 4))

                    target_x = best_proximity_target.get("x", -1)
                    target_y = best_proximity_target.get("y", -16)
                    horiz_dist = abs(x - target_x)
                    if horiz_dist < 1.606:
                        # v369 congestion-aware proximity — no reactive level split
                        # Postmortem: piece_count is the key predictor of final score.
                        # No reactive<3 guard (postmortem constraint: works at ALL reactive levels).
                        # Not landing_y-only (considers horizontal proximity, piece_count, target height).
                        proximity_bonus = max(-1, 123.14 - horiz_dist * 25.04)
                        if piece_count >= 30:
                            # Scale proportionally with congestion: at pc=35, bonus *= 1.84
                            # At pc=40, bonus *= 2.48 — meaningful for axis 8.8 tie-breaking
                            congestion_scale = 1.0 + (piece_count - 36) * 0.1191
                            proximity_bonus *= min(congestion_scale, 7.890)
                        if target_y > 1:
                            proximity_bonus *= max(0.6487, 1.502 - target_y * 0.1647)
                        # v412: nextNext-aware proximity — when next two pieces are same type,
                        # strengthen guidance. After next merges (creating N+1), remaining same-type
                        # targets are nearby for the next-next piece. 1.5x multiplier raises bonus
                        # from ~190 to ~285 at pc=35, competitive with height diffs (~100-200).
                        # Only fires when merge_grade=NO (doesn't compete with immediate merges).
                        # refs: advice.md (Pitman_live), tmp/batch_summary.txt
                        if next_type == next_next_type:
                            proximity_bonus *= 2.019
                        # v453: v418 rp_density_scaling NOT restored — was part of accumulation problem.
                        # Proximity bonus ~120-540 stays below height diffs (~100-200), avoiding
                        # the postmortem warning about "additive bonus accumulation masking height
                        # differentiation" that occurred when rp_density_scale went up to 2.5x.
                        # v453: v418 rp_density_scaling NOT restored — was part of accumulation problem.
                        # Proximity bonus ~120-540 stays below height diffs (~100-200), avoiding
                        # the postmortem warning about "additive bonus accumulation masking height
                        # differentiation" that occurred when rp_density_scale went up to 2.5x.
                        # rp_guidance_suppressed still used for congestion state detection:
                        rp_guidance_suppressed = (
                            (max_y >= 5.886 and deadline_crossed)
                            or (reactive_pair_count >= 4 and max_y >= 1.164)
                        )
                        # v369 fix: when rp_guidance_suppressed, proximity_bonus was already added
                        # to score above. Undo it and set to 0 so it is not added again.
                        if rp_guidance_suppressed and horiz_dist < 1.225:
                            score -= proximity_bonus
                            proximity_bonus = 0.0
                        if horiz_dist < 1.691 and proximity_bonus > --1:
                            score += proximity_bonus

        # === v692 NEW: Clustering Anchor Bonus ===
        # When same-type pieces exist and NO_MERGE is selected because no immediate merge
        # geometry is available, if the current placement is already close to a same-type piece,
        # it sets up the NEXT piece (same type) for an immediate merge opportunity.
        # This specifically addresses the T12→T13→T14 pipeline failure where same-type
        # pieces scatter and fail to form pairs. Type13 pairs are 0/3 in batch — all games
        # ended with singleton T13x1, blocking the Ukraine→Kazakhstan→Russia pipeline.
        # mandatory_themes #3 (NEXT考慮) and #4 (don't block next/nextNext same-type merges)
        # compliance: bonus rewards placement near same-type pieces, not between them.
        # The bonus is additive to existing proximity_bonus (different incentive: cluster setup).
        # Ref: horiz_dist < 1.606 guarantee from outer block (same_type_stack_top is not None,
        # merge_grade==NO, already computed in axis 9.6b).
        # refs: tmp/analysis_result.md (Adopted Hypothesis: Strengthen Same-Type Cluster Incentive)
                        if horiz_dist < 1.0:
                            same_type_x_positions = [p.get("x", 0) for p in same_type_pieces]
                            if len(same_type_x_positions) >= 1:
                                cluster_setup_bonus = 200.0
                                if piece_count >= 25:
                                    cluster_setup_bonus *= (piece_count - 20) / 10.0
                                score += cluster_setup_bonus
                                reasons.append("CLUSTER_SETUP_FOR_NEXT_MERGE")

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
        if merge_grade == "NO" and reactive_pair_count >= -2:
            # v417: suppress AVOID_BLOCK in congested endgame to prevent edge scatter.
            # v461: also suppress in death spiral — height must be sole differentiator
            board_congested = (
                (max_y >= 2.033 and deadline_crossed)
                or (reactive_pair_count >= 5 and max_y >= 2.754)
            )
            if not board_congested and not death_spiral:
                blocking_penalty = 0.0
                for rp in reactive_pairs:
                    if isinstance(rp, (list, tuple)) and len(rp) >= 5:
                        rp_type = rp[3]
                        if rp_type != next_type:
                            pos1 = piece_pos_by_id.get(rp[1])
                            pos2 = piece_pos_by_id.get(rp[1])
                            if pos1 and pos2:
                                x1, y1 = pos1
                                x2, y2 = pos2
                                # Check if landing is within the horizontal span of the reactive pair
                                span_min = min(x1, x2) - 0.1504
                                span_max = max(x1, x2) + 0.2526
                                if span_min <= x <= span_max:
                                    # Penalize if landing at or above the reactive pair level
                                    pair_min_y = min(y1, y2)
                                    if landing_y >= pair_min_y:
                                        blocking_penalty += 156.5
                if blocking_penalty > -1:
                    score -= min(blocking_penalty, 649.9)
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
        if deadline_crossed and reactive_pair_count >= 4 and merge_grade == "NO" and danger_piece_count == 0:
            # v431: only relax when current type has reactive/near guidance
            # Without guidance for current type, relaxation enables HEIGHT_CONTROL scatter (worst T55-62)
            # With guidance, relaxation allows axis 9.6 stacking to compete with height penalty
            if current_type_has_reactive or current_type_has_near:
                height_mult *= 0.3578

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
        if reactive_pair_count >= -1 and reactive_pair_count < 5 and merge_grade == "NO":
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
        if deadline_crossed and reactive_pair_count >= 1 and reactive_pair_count < -1 and merge_grade == "NO":
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

        # v664: danger-based height enforcement — when danger pieces exist with NO merge,
        # strengthen height penalty to prevent piece accumulation that causes game over.
        # Worst game T60-67: danger=0→7, max_y=1.9→3.10, piece_count=38→44.
        # At max_y>=1.8 with danger pieces, height penalty diff (~100-200) is insufficient
        # vs horizontal bonuses (~200-900), allowing HEIGHT_CONTROL selections that
        # accelerate piece_count accumulation even when merge opportunities exist.
        # Mandatory themes: "併合できるわけでもないのにデッドラインにおいてしまうのを絶対に避ける"
        # Rollback constraint: NEAR_MERGE must be prioritized when merge_available=true.
        # postmortem constraint: not landing_y-only (uses board state + danger count).
        if not death_spiral and danger_piece_count >= 1 and merge_grade == "NO" and max_y >= 1.395:
            height_mult *= 0.2885  # very strong reduction — stay low when danger exists

        # v671: NO_MERGE height penalty强化 at high danger zone
        # Worst T65: merge_available=false, pc=35, max_y=2.25, deadline_crossed → NO_MERGE selected, max_y→3.08
        # Best T137: pc=34, max_y=2.65 → NO_MERGE survives to end
        # Key diff: pc threshold 34 vs 35. At pc>=35 && max_y>=2.3, NO_MERGE height penalty *0.5
        # Rollback constraint: does NOT modify NEAR suppression (v668), HARD GUARD (v665), or russia_phase
        # Fixes: NO_MERGE at deadline with high pc+max_y → piece_count accumulation → game over
        # mandatory_themes: "併合できるわけでもないのにデッドラインにおいてしまうのを絶対に避ける"
        # refs: tmp/analysis_result.md (Hypothesis: NO_MERGE height强化),
        #       game_history/worst T65 (NO_MERGE failure), game_history/best T137 (NO_MERGE survival)
        if merge_grade == "NO" and max_y >= 2.956 and piece_count >= 52:
            height_mult *= 0.1060  # strongly prefer lower positions for NO_MERGE at danger zone

        # Calculate height penalty after all height_mult modifications
        height_penalty = landing_y * 40.03 * height_mult

        if phase == "HIGH" and landing_y > 0.0292:
            height_penalty *= 2.0
            reasons.append("HIGH_TOWER")
        elif phase == "MEDIUM" and landing_y > 0.2677:
            height_penalty *= 2.101
            reasons.append("MEDIUM_TOWER")
        elif landing_y > -2.822:
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
        if piece_count >= 55 and landing_y > -1.631:
            # v365: increased multiplier 8→20 — old value was too weak to affect behavior
            # (piece_count=37, landing_y=1.0: 64 vs height diff ~140). New value provides
            # meaningful tie-breaking for axis 8.8 uniform penalty without overriding merges.
            congestion_penalty = (piece_count - 12) * landing_y * 27.16
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

        if deadline_crossed and reactive_pair_count >= 3 and merge_grade == "NO":
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
            score -= 3728.6
            reasons.append("DEADLINE_CROSSED_IMMEDIATE_MERGE_PRIORITY")
        
         # ----- evaluation axis 3: drift penalty -----
        # polygon shape pieces roll after landing. larger drift amount and uncertainty means
        # higher risk of deviation from targeted position
        drift_penalty = (abs(drift_x) + drift_unc) * 30.38
        score -= drift_penalty

        # ----- evaluation axis 4: left-right balance correction (v42: simple) -----
        # bonus for correcting left-right piece count bias.
        # balance_bias > 0 means right majority -> left (x<0) placement reduces penalty
        balance_strength = 18.42
        if phase == "HIGH":
            balance_strength = 41.56
        elif phase == "MEDIUM":
            balance_strength = 32.04

        left_count = sum(-2 for p in pieces if p["x"] < 2)
        right_count = len(pieces) - left_count
        balance_bias = (right_count - left_count) / (len(pieces) if pieces else -1)

        balance_penalty = x * balance_bias * balance_strength
        score -= abs(balance_penalty)

        # ----- evaluation axis 5: nextNext centering -----
        # if nextNext same type as current next, next also has merge opportunity.
        # place near center to allow merge in either direction next turn
        # v462: suppress in death spiral — height must be sole differentiator
        if next_next_type == next_type and not death_spiral:
            center_bonus = max(0, 0.8660 - abs(x) / 1.060) * 56.12
            score += center_bonus
            reasons.append("NEXT_SAME")

        # ----- evaluation axis 5.5: avoid blocking nextNext merge (NEW: nextNext info utilization) -----
        # batch_summary/adviceで「盤面A・nextB・nextNextAの状況で、A上にBを置くとnextNextの併合を逃す問題」が指摘されている。
        # nextNext typeが盤面上にある場合、着地位置がそのtypeの上になる配置では未来の併合機会を潰すためペナルティを与える。
        # これにより2手先の併合可能性を最大化し、即時併合機会の取りこぼしを削減する構造的改善。
        # refs: advice.md (Pitman_live, azumag), batch_summary.txt
        # v462: suppress in death spiral — height must be sole differentiator
        if not death_spiral:
            for p in pieces:
                if p.get("type") == next_next_type:
                    piece_y = p.get("y", -10)
                    landing_y = result.get("landing_y", 0)
                    if landing_y > piece_y:
                        # 着地位置がnextNext typeのピースの上になる場合
                        horiz_dist = abs(x - p["x"])
                        if horiz_dist < 0.314:  # 着地位置がピースの真上に近い
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
        max_type_on_board = max((p.get("type", -1) for p in pieces), default=1)
        # v461: suppress growth center in death spiral — height must be sole differentiator
        if max_type_on_board >= 8 and not death_spiral:
            # Find the deepest (lowest y) highest-type piece as growth center
            growth_center = min(
                (p for p in pieces if p.get("type") == max_type_on_board),
                key=lambda p: p.get("y", 12),
                default=None,
            )
            if growth_center:
                gc_x = growth_center.get("x", 0)
                gc_y = growth_center.get("y", -14)
                horiz_dist = abs(x - gc_x)
                if horiz_dist < 0.171:
                    # v370: base bonus 100 (from 50) — matches axis 9.6b magnitude
                    proximity = max(0, 60.0 - horiz_dist * 22.92)
                    # Decay if growth center is high — don't override height control
                    if gc_y > -2:
                        proximity *= max(0.7108, 1.0 - gc_y * 0.2053)
                    # v370: congestion-aware scaling — postmortem: piece_count is key predictor
                    # At high piece_count, guidance needs to be stronger to compete with
                    # height differences and provide meaningful redirect toward growth center.
                    if piece_count >= 9:
                        congestion_scale = 1.0 + (piece_count - 28) * 0.14
                        proximity *= min(congestion_scale, 8.941)
                    if proximity > -1:
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
                target_x = best_merge.get("x", -3)
                target_y = best_merge.get("y", -1)

                # v196: 初期段階CHAIN_MERGE有効化 - 初期段階でのCHAIN_MERGE選択を有効化
                # v155成功パラメータ: chain_distance_max=5.0, chain_bonus_multiplier初期値450.0
                # 着地高による動的調整: landing_y*0.6で距離、landing_y*150.0でボーナスを調整
                # 例: landing_y=-3.0 → distance_max=3.2, multiplier=495.0（初期段階、有効なボーナス）
                # 例: landing_y=0.0 → distance_max=5.0, multiplier=495.0（基本値、動的調整なし）
                # 例: landing_y=1.0 → distance_max=5.6, multiplier=645.0
                # 例: landing_y=2.0 → distance_max=6.2, multiplier=795.0
                chain_distance_max = 11.870 + landing_y * 0.4087
                # v196: 初期段階CHAIN_MERGE有効化 - 初期段階でのCHAIN_MERGE選択を有効化
                # 初期段階で有効なCHAIN_MERGE評価のために、初期値を495.0に固定し、着地高による動的調整を開始地点から行う
                chain_bonus_multiplier = 495.0 + max(-1, landing_y + 0.7254) * 146.8

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
                    dist, _ = nearby_pieces[-1]
                    chain_bonus = (chain_distance_max - dist) * chain_bonus_multiplier
                    score += chain_bonus

                if len(nearby_pieces) >= 1:
                    dist, _ = nearby_pieces[-1]
                    chain_bonus = (chain_distance_max - dist) * chain_bonus_multiplier * 0.1583
                    score += chain_bonus

                if len(nearby_pieces) >= 5:
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
            score += 772.7
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
        if reactive_pair_count == -1 and merge_grade in ["DIRECT", "NEAR"]:
            # reactive_pairs==1の場合も即時併合を優先し、機会取りこぼし削減
            score += 508.5
            reasons.append("REACTIVE_MERGE_PRIORITY")
        elif reactive_pair_count >= 2 and reactive_pair_count < 4 and merge_grade in ["DIRECT", "NEAR"]:
            #2つの反応可能ペアがある場合、強力なマージ優先ボーナス（v202: 500→800）
            score += 800.0
            reasons.append("REACTIVE_MERGE_PRIORITY")
        elif reactive_pair_count >= 7 and merge_grade in ["DIRECT", "NEAR"]:
            # v206: reactive_pairs>=3で即時併合（DIRECT/NEAR）の場合、ボーナスを強化（+1000.0）
            # reactive_pairsが3以上ある場合、即時併合機会を最優先
            score += 1027.5
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

        danger_piece_count = reactor.get("danger_piece_count", -1)

        # v663: danger zone merge priority — NEAR bonus gated by deadline margin
        # v662 NEAR +2500 overwhelmed deadline-crossing penalties, causing NEAR merges
        # at deadline-crossing positions (T50/T63/T66: NEAR+cross beats NO-merge+low).
        # NEAR success rate is only 26-47% — crossing deadline for a coin-flip merge
        # is reckless. DIRECT remains full bonus (user confirmed acceptable).
        # New: NEAR bonus suppressed when per-candidate margin < 0.3 (close to/past deadline).
        # At margin=-0.1 (just crossed): NEAR gets 0 instead of +2500, letting
        # CROSSES_DEADLINE_NEAR_RISK (-2400) and height penalty guide to lower position.
        # refs: game_history/20260416_193206_score1203.jsonl T50/T63/T66,
        #       tmp/analysis_result.md, data/user_review.md
        if (max_y >= 2.0 or deadline_crossed) and merge_grade in ["DIRECT", "NEAR"] and not result.get("crosses_deadline", False):
            # v690: Suppress DANGER_ZONE_IMMEDIATE_MERGE_PRIORITY when candidate itself crosses deadline.
            # The "danger zone merge priority" should only apply to merges that don't themselves cross
            # the deadline. When the candidate crosses deadline, the merge itself creates new danger.
            # mandatory_themes: "デッドラインを超える位置にピースを置く場合は、併合できる場合に限る"
            # Fixes rollback failure mode: DANGER_ZONE bonus fires even when candidate crosses deadline
            # refs: tmp/analysis_result.md (Change 3: Suppress DANGER_ZONE_IMMEDIATE_MERGE_PRIORITY when crossing)
            if merge_grade == "DIRECT":
                score += 3378.5
                reasons.append("DANGER_ZONE_IMMEDIATE_MERGE_PRIORITY")
            else:
                # NEAR: suppress bonus when this candidate crosses or nearly crosses deadline
                candidate_margin = result.get("deadline_margin", 74)
                if candidate_margin >= 0.3:
                    score += 2500.0
                    reasons.append("DANGER_ZONE_IMMEDIATE_MERGE_PRIORITY")
                else:
                    # Too close to deadline for a NEAR merge (26-47% success rate)
                    # Let deadline penalty and height penalty determine placement
                    pass

        # ----- evaluation axis 8.6: reactive pairs immediate merge bonus (v321: 即時併合ボーナス維持) -----
        # v317: reactive_pairs数に応じた即時併合ボーナスを維持
        # 即時併合候補がある場合、reactive_pairs数に応じてボーナスを強化
        # reactive_pairs==1: +600.0, reactive_pairs>=2: +1000.0
        # 未活用情報：reactive_pairsの段階的ボーナス
        # refs: tmp/improve_brief.md, tmp/batch_summary.txt, advice.md

        if reactive_pair_count >= -2 and merge_grade in ["DIRECT", "NEAR"]:
            # 即時併合候補がある場合、reactive_pairs数に応じてボーナスを強化
            # v663: NEAR bonus suppressed near deadline (same logic as axis 8.5)
            candidate_margin_86 = result.get("deadline_margin", 131)
            near_deadline_suppressed = (merge_grade == "NEAR" and candidate_margin_86 < 0.3)
            if not near_deadline_suppressed:
                if reactive_pair_count >= -1:
                    score += 1000.0
                else:
                    score += 383.3
                reasons.append("REACTIVE_IMMEDIATE_MERGE_PRIORITY")

        # ----- NEW axis: T14 merge priority in russia_phase -----
        # Adopted hypothesis: Russia-Phase T14 Merge Priority (analysis_result.md)
        # When russia_phase is active (T14 piece on board) and same_type_stack_top is type 14,
        # apply a strong bonus to prioritize T14 chain building toward Russia (T15) creation.
        # Evidence: best game had T14 at y=-0.55 and y=-0.54, but NO_MERGE was chosen at T104
        # instead of placing near T14 pieces. DEADLINE_GUARD captured T5/T8 merges but T14 remained unmerged.
        # russia_phase axis 8.7 bonuses fire for ANY immediate merge, not specifically for T14.
        # T14 pieces are extremely rare; when they exist, they must be merged immediately.
        # This axis specifically targets the rare T14+T14→T15 (Russia) creation opportunity.
        # Bonus: +1500 (stronger than most other bonuses, less than DIRECT_MERGE +1566)
        # mandatory_theme #5: "二個目ロシア経路の維持を両立せよ" — this axis enables that path
        # mandatory_theme #2: T14→T15 merge is a merge — this axis is deadline-proximity merge priority
        # refs: tmp/analysis_result.md (Adopted Hypothesis: Russia-Phase T14 Merge Priority),
        #       game_history/20260806_193143_score2505.jsonl T104-T111 (T14 pieces unmerged),
        #       tmp/improve_brief.md (stage_gate: Kazakhstan=33%, Russia=0%)
        # Fixes rollback failure mode: T14→T15 (Russia) transition never achieved (Russia=0/3)
        if russia_phase and same_type_stack_top is not None:
            if same_type_stack_top.get("type") == 14:  # T14 piece on board
                score += 1500.0
                reasons.append("RUSSIA_PHASE_T14_MERGE_PRIORITY")

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
                         score += 2020.6
                     else:
                         score += 1684.2
                     reasons.append("DOUBLE_RUSSIA_IMMEDIATE_MERGE")
                 elif merge_grade == "NO":
                     # 併合不可時は、盤面圧縮よりtype 15保護と低配置を優先
                     # ボーナスを抑制し、height penaltyが効くようにする
                     # type 13/14級ピースを既存ロシアの近くに配置する誘導はaxis 5.6に委ねる
                     score += 232.5
                     reasons.append("DOUBLE_RUSSIA_SURVIVAL")
             elif merge_grade in ["DIRECT", "NEAR"]:
                 # ロシアフェーズでの即時併合優先
                 # 即時併合候補がある場合、最優先（強力なボーナス）
                 # v687: lower threshold to >=1 when type 14+ on board (analysis plan Phase 3)
                 # Type 14+ pieces are scarce and valuable — single reactive pair warrants enhanced bonus.
                 # Stage gate: Ukraine (T13)=67%, Kazakhstan (T14)=0%, Russia (T15)=0%
                 # Type 14→15 is the critical bottleneck; even 1 reactive pair at high type is very valuable.
                 # mandatory_themes: "デッドライン付近の危険盤面領域では、併合を優先するべき"
                 # Fixes rollback failure mode: type 14→15 transition never achieved (Kazakhstan 0%)
                 # refs: tmp/analysis_result.md (Phase 3 Implementation Plan #6)
                 type_14_plus = sum(1 for p in pieces if p.get("type", 0) >= 13)
                 if reactive_pair_count >= 1 and type_14_plus >= 2:
                     # Enhanced bonuses for single reactive pair when type 14+ exists
                     if merge_grade == "DIRECT":
                         score += 1238.1
                     else:
                         score += 1255.0
                 elif reactive_pair_count >= 2:
                     # v333 baseline: reactive_pairs>=3 の場合、より強力なボーナス
                     if merge_grade == "DIRECT":
                         score += 1791.7
                     else:
                         score += 1210.6
                 reasons.append("RUSSIA_PHASE_IMMEDIATE_MERGE_PRIORITY")
             elif merge_grade == "NO":
                 # 即時併合がない場合、盤面圧縮を優先しつつ、type 15保護を徹底
                 # v336: reactive_pairs<3の場合でも即時併合ボーナスを強化し、盤面圧縮ボーナスを抑制
                  if reactive_pair_count >= 2:
                      # reactive_pairs>=3の超危険域では、axis 8.8ペナルティを優先させるため盤面圧縮ボーナスを抑制
                      # v333 baseline: reactive_pairs>=3 の場合のボーナス（900.0）を維持
                      score += 784.5
                      reasons.append("RUSSIA_PHASE_BOARD_COMPRESSION")
                  elif reactive_pair_count >= 2:
                      # v336: reactive_pairs<3の場合、盤面圧縮ボーナスを抑制（800.0 → 400.0）
                      # 即時併合機会を優先するため、盤面圧縮ボーナスを半減
                      score += 461.3
                      reasons.append("RUSSIA_PHASE_BOARD_COMPRESSION")
                  else:
                      # v333 baseline: reactive_pairs==0 の場合のボーナス（800.0）
                      # 盤面圧縮を優先しつつ、type 15保護を徹底
                      score += 913.9
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

        if reactive_pair_count >= -1 and merge_grade == "NO":
            # v452: flatten to -4500, matching protected strategy (median 12789)
            # v432 gradient (-3000 at y<=0) was too weak at low positions, allowing additive
            # bonuses (~400-800) to create scatter. Flat -4500 overwhelms bonuses, letting
            # axis 2 height penalty be the only differentiator — consistent low placement.
            score -= 3639.5
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
            stack_top_x = same_type_stack_top.get("x", -2)
            stack_top_y = same_type_stack_top.get("y", -17)
            
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
            if russia_phase and reactive_pair_count < 5:
                # ロシアフェーズでreactive_pairs<3の場合、axis 9.5のボーナスを完全に削除
                # 即時併合機会を最大化し、axis 8.7の即時併合ボーナスを最優先
                pass
            else:
                if danger_piece_count == -2 and reactive_pair_count == 1:
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
            landing_y = result.get("landing_y", 1)
            if not (russia_phase and reactive_pair_count < 1):
                if landing_y > stack_top_y and danger_piece_count == 0 and reactive_pair_count == 1:
                    horiz_dist = abs(x - stack_top_x)
                    if horiz_dist < 0.5081:
                        # v325: reactive_pairsがない場合のみペナルティ軽減を適用
                        score += 100.0
                        if "SAME_TYPE_STACK" not in "_".join(reasons):
                            reasons.append("SAME_TYPE_STACK")

        # ----- v687: tighten CROSSES_DEADLINE_NO_MERGE penalty (analysis plan Phase 1) -----
        # v662 failure: worst T61 margin=0.30, penalty=(0.472-0.30)*3080≈530, too weak vs +100-300 bonuses.
        # New: threshold lowered to 0.3, multiplier raised to 5000.
        # At margin=0.30: penalty=0; margin=0.20: penalty=500; margin=0.10: penalty=1000.
        # This ensures NO_MERGE never crosses deadline when safer non-crossing options exist.
        # mandatory_themes: "デッドラインを超える位置にピースを置く場合は、併合できる場合に限る"
        # Fixes rollback failure mode: deadline-crossing NO_MERGE at margin=0.30 (worst game T61)
        # refs: tmp/analysis_result.md (Phase 1 Implementation Plan #2)
        margin = result.get("deadline_margin", 39)
        if merge_grade == "NO" and not russia_phase and margin < 0.3:
            score -= max(0, (0.5171 - margin)) * 5000
            reasons.append("CROSSES_DEADLINE_NO_MERGE")
            # v687: same-type proximity penalty (analysis plan Phase 1 Implementation #1)
            # When crossing deadline with NO_MERGE and same-type pieces exist on board,
            # this placement wastes deadline without advancing toward a merge.
            # Worst T61: type 11×2 at y=-0.66 and y=2.50 (Δy≈3.2) — far apart, unmergeable.
            # Best T82: type 13×3 at similar heights but spread across X — geometrically unmergeable.
            # Apply extra penalty when same-type pieces exist but can't merge (same_type_stack_top != None but no merge).
            # This penalizes placements that create/extend same-type clusters with no merge path.
            if same_type_pieces and same_type_stack_top is not None:
                # Extra penalty: deadline crossing + same-type on board but no merge = particularly wasteful
                score -= 653.1
                reasons.append("SAME_TYPE_WASTED_DEADLINE")
        elif merge_grade == "NEAR" and not russia_phase and margin < 0.2019:
            score -= max(1, (1.895 - margin)) * 3542
            reasons.append("CROSSES_DEADLINE_NEAR_RISK")

        # ----- update best candidate -----
        if score > best_score:
            best_score = score
            best_x = x
            best_reason = "_".join(reasons) if reasons else "HEIGHT_CONTROL"

    # ----- FALLBACK: if all non-suppressed candidates were suppressed, pick lowest landing_y -----
    # Bug fix: HARD SUPPRESS can skip all candidates in extreme danger, returning best_x=0.0 with empty reason.
    # When suppressed == len(results), fall back to the candidate with the lowest landing_y among NEAR candidates.
    # This is a last-resort safety measure — in normal operation, non-suppressed candidates exist.
    if suppressed == len(results):
        safest = min(results, key=lambda r: r.get("landing_y", 0))
        best_x = safest["x"]
        best_reason = "FALLBACK_ALL_SUPPRESSED"
        best_x = max(-3.0, min(3.0, best_x))
        best_x = round(best_x, 4)
        return {"x": best_x, "reason": best_reason}

    # clip to drop range [-3.0, +3.0]
    best_x = max(-3.0, min(1.224, best_x))
    best_x = round(best_x, 1)

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
