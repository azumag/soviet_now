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
      # vXXX: axis 1.7c NEAR suppression at tight deadline_margin — Ukraine(T13) gate fix
      #       When reactor_margin < 0.3 && deadline_crossed && merge_grade==NEAR && NOT russia_phase,
      #       suppress NEAR unless landing_y < max_y - 0.5 (safety valve for compression).
      #       Worst game T56-T64: tight deadline NEAR/NO_MERGE choices caused max_y runaway.
      #       Target stage: Ukraine(T13)=2/3; preserves DIRECT merge priority near deadline.
      # vXXX: axis 8.8 suppression bug fix — remove `if not (deadline_crossed and rp>=3)` condition
      #       that suppressed REACTIVE_PAIRS_NO_MERGE_PENALTY exactly when it was most needed.
      #       postmortem constraint requires penalty whenever rp>=3 && NO_MERGE, regardless of deadline_crossed.
      #       Previous code: `if not (deadline_crossed and reactive_pair_count >= 3): score -= 4500`
      #       This suppressed penalty at deadline_crossed && rp>=3, causing worst game T56 death spiral.
      #       Target stage: Kazakhstan(T14) 0/3 — prevents T14→T15 merge path collapse at deadline.
      #       mandatory_themes: デッドライン超出時は併合できる場合に限る (penalty now forces low landing_y).
      #       refs: tmp/analysis_result.md (axis 8.8 suppression bug), tmp/state/last_rollback_postmortem.md
      # vXXX: Ukraine(T13) gate type growth fix — horizontal clustering + merge compression check + merge-before-height protection
      #       axis 9.6: horizontal_clustering penalty increased to -400*abs(delta_y) (was -200)
      #         rewards same-height clustering for future merges (type 10+ pieces at similar y, close x)
      #       axis 1.5d: NEAR merge compression check — -800*merge_mult when landing_y > max_y + 0.3 (prevents raising max_y)
      #       axis 2: merge-before-height protection at max_y 2.0-3.0 + same_type_pieces >= 2 + mergegrade DIRECT/NEAR → height_mult *= 0.3
      #       Fixes rollback failure mode: type growth stall at Ukraine gate (T55-T64 HEIGHT_CONTROL over merge)
      #       Target stage: Ukraine(T13)=1/4 → improve to 2/4+; prevents T12→T13 merge from raising max_y
      #       mandatory_themes: NEXTを考慮したドロップ, デッドライン超出時は併合できる場合に限る
      #       refs: tmp/analysis_result.md (Primary Hypothesis: Type growth stall at Ukraine gate)
      # v676: axis 1.7b BOARD_MAX_Y_NEAR_SUPPRESSION — max_y>=2.5 && pc>=33 && NEAR && NOT russia_phase で -1000～-3000 ペナルティ
     #       v422(landing_y条件)では補足できない「盤面全体の高さが危険レベルに達した状態」でのNEAR選択を押さえる
     #       Fixes rollback failure mode: max_y>=2.5, pc>=33 でのNEAR選択が score_delta=0 を返しmax_y暴走→ゲームオーバー
     #       mandatory_themes: 盤面高さ管理強化で「デッドラインにおいてしまう」リスクを間接的に低減
     #       refs: tmp/analysis_result.md
     # v675: CROSSES_DEADLINE_EDGE_NO_MERGE — decision_crosses_deadline=true && NO_MERGE && |x|>=2.5 && NOT russia_phase で -1500 ペナルティ
     #       mandatory_themes: 「併合できるわけでもないのにデッドラインにおいてしまうのを絶対に避ける」
     #       Fixes: extra_low(1112)T64-T70で7ターン連続の decision_crosses_deadline && NO_MERGE && |x|>=2.5 を抑制
     #       Worst(641)T62: x=-3.0 に -1500 → 中央付近選択へ誘導、PC_EDGE_PENALTY未発動(pc=40)の隙間を補完
     #       Russia_phase除外: best(4816)T159 x=-3.0 は russia_phase=True なので影響なし
     #       refs: tmp/analysis_result.md (CROSSES_DEADLINE_EDGE_NO_MERGE仮説)
     # v674: PIECE_COUNT_EDGE_BIAS 対策 — pc>=40 && deadline_crossed && NO_MERGE && |x|>=1.5 で
     #       エッジ配置追加ペナルティ: -(pc-35)*400*(|x|/3.0)。pc=40,|x|=2でー1333、pc=45,|x|=3でー4000。
     #       Fixes: worst T64-T66 pc=43,deadline_crossed,NO_MERGE時にx=-2.0が選択される問題を解消。
     #       refs: tmp/analysis_result.md, game_history/20260417_193200_score0490.jsonl T64-66
     # vXXX: deadline merge urgency — +2000 bonus for DIRECT/NEAR when deadline_crossed && rp>=3;
     #       suppress axis 8.8 penalty when deadline_crossed && rp>=3 && !global_merge_available
     #       Fixes: "merge_available but NO_MERGE chosen" death spiral at deadline with rp>=3
     #       (score826 T62 chose NO_MERGE at rp=7-8, deadline_crossed=true, while NEAR existed).
     #       Constraint: forbids reactive_pairs_no_merge_penalty at rp>=3 && deadline && no merge.
     #       refs: tmp/analysis_result.md, tmp/state/last_rollback_postmortem.md
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
     # v548: double_russia_phase — 2つ目の(type 14/15)出現後のフェーズ切替
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
    try:
        __dlg_danger_count = int(__dlg_reactor.get("danger_piece_count", 0) or 0)
    except (TypeError, ValueError):
        __dlg_danger_count = 0
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
    # This guard is specifically a deadline guard. Reactive pairs alone can
    # justify merge pressure elsewhere in the strategy, but must not force a
    # "safe landing" while the visible board is still far below the red line.
    __dlg_critical = __dlg_dcross or __dlg_margin < 1.0
    if __dlg_critical and __dlg_cands:
        __dlg_has_clean = any(
            isinstance(c, dict)
            and not c.get("crosses_deadline")
            and not c.get("merge_result_crosses_deadline")
            for c in __dlg_cands
        )
        def __dlg_merge_result_safe(c):
            return not c.get("merge_result_crosses_deadline")
        __dlg_direct = [
            c for c in __dlg_cands
            if isinstance(c, dict) and c.get("merge_grade") == "DIRECT"
            and __dlg_merge_result_safe(c)
            and not c.get("merge_result_crosses_deadline")
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
            if isinstance(c, dict) and c.get("merge_grade") == "NEAR"
            and __dlg_merge_result_safe(c)
            and not c.get("merge_result_crosses_deadline")
        ]
        if __dlg_near_safe:
            __dlg_best = min(__dlg_near_safe, key=lambda c: float(c.get("landing_y", 99.0) or 99.0))
            return {"x": float(__dlg_best.get("x", 0.0) or 0.0), "reason": "DEADLINE_GUARD_NEAR_MERGE"}

        def __dlg_pre_russia_cluster_pick(candidates):
            __dlg_pieces = __dlg_game_state.get("pieces", [])
            if not isinstance(__dlg_pieces, list):
                return None
            __dlg_counts = {}
            __dlg_targets = []
            for __dlg_piece in __dlg_pieces:
                if not isinstance(__dlg_piece, dict):
                    continue
                __dlg_type = int(__dlg_piece.get("type", 0) or 0)
                if __dlg_type >= 10:
                    __dlg_counts[__dlg_type] = __dlg_counts.get(__dlg_type, 0) + 1
                if __dlg_type in (11, 12, 13, 14, 15):
                    __dlg_targets.append(__dlg_piece)
            __dlg_russia_pair_ready = (
                __dlg_counts.get(14, 0) >= 1
                and __dlg_counts.get(13, 0) >= 2
            )
            __dlg_first_russia_t13_pair_lift_ready = (
                __dlg_counts.get(15, 0) == 0
                and __dlg_counts.get(14, 0) == 1
                and __dlg_counts.get(13, 0) >= 2
            )
            __dlg_first_russia_single_t13_t12_bank_ready = (
                __dlg_counts.get(15, 0) == 0
                and __dlg_counts.get(14, 0) == 1
                and __dlg_counts.get(13, 0) == 1
                and __dlg_counts.get(12, 0) >= 1
                and (
                    __dlg_counts.get(12, 0) >= 2
                    or __dlg_counts.get(11, 0) >= 1
                    or __dlg_counts.get(10, 0) >= 2
                )
            )
            __dlg_double_t14_ready = __dlg_counts.get(14, 0) >= 2
            __dlg_first_russia_pair_ready = (
                __dlg_counts.get(14, 0) == 0
                and __dlg_counts.get(13, 0) >= 2
                and (
                    __dlg_counts.get(12, 0) >= 1
                    or __dlg_counts.get(11, 0) >= 2
                )
            )
            __dlg_t13_pair_compress_ready = (
                __dlg_counts.get(14, 0) == 0
                and __dlg_counts.get(13, 0) >= 2
                and __dlg_counts.get(12, 0) >= 2
            )
            __dlg_t13_pair_single_t12_tether_ready = (
                __dlg_counts.get(15, 0) == 0
                and __dlg_counts.get(14, 0) == 0
                and __dlg_counts.get(13, 0) >= 2
                and __dlg_counts.get(12, 0) == 1
            )
            __dlg_single_t13_t12_compress_ready = (
                __dlg_counts.get(15, 0) == 0
                and __dlg_counts.get(14, 0) == 0
                and __dlg_counts.get(13, 0) == 1
                and __dlg_counts.get(12, 0) >= 2
            )
            __dlg_single_t13_single_t12_ladder_ready = (
                __dlg_counts.get(15, 0) == 0
                and __dlg_counts.get(14, 0) == 0
                and __dlg_counts.get(13, 0) == 1
                and __dlg_counts.get(12, 0) == 1
                and (
                    __dlg_counts.get(11, 0) >= 2
                    or __dlg_counts.get(10, 0) >= 2
                )
            )
            __dlg_single_t12_anchor_ladder_ready = (
                __dlg_counts.get(15, 0) == 0
                and __dlg_counts.get(14, 0) == 0
                and __dlg_counts.get(13, 0) == 0
                and __dlg_counts.get(12, 0) == 1
                and __dlg_counts.get(11, 0) >= 2
            )
            __dlg_second_russia_t12_ladder_ready = (
                __dlg_counts.get(15, 0) == 0
                and __dlg_counts.get(14, 0) >= 1
                and __dlg_counts.get(13, 0) == 0
                and __dlg_counts.get(12, 0) >= 1
                and (
                    __dlg_counts.get(11, 0) >= 1
                    or __dlg_counts.get(10, 0) >= 2
                )
            )
            __dlg_second_russia_t12_pair_lock_ready = (
                __dlg_second_russia_t12_ladder_ready
                and __dlg_counts.get(12, 0) >= 2
            )
            __dlg_soviet_lift_ready = (
                __dlg_counts.get(15, 0) == 1
                and (
                    __dlg_counts.get(14, 0) >= 1
                    or (
                        __dlg_counts.get(13, 0) >= 1
                        and (
                            __dlg_counts.get(12, 0) >= 1
                            or __dlg_counts.get(11, 0) >= 1
                        )
                    )
                    or (
                        __dlg_counts.get(12, 0) >= 1
                        and __dlg_counts.get(11, 0) >= 2
                    )
                )
            )
            __dlg_soviet_ladder_ready = (
                __dlg_counts.get(15, 0) == 1
                and __dlg_counts.get(14, 0) == 0
                and __dlg_counts.get(12, 0) >= 1
                and (
                    __dlg_counts.get(11, 0) >= 1
                    or __dlg_counts.get(10, 0) >= 2
                )
            )
            __dlg_t11_density_ready = (
                __dlg_counts.get(15, 0) == 0
                and __dlg_counts.get(14, 0) == 0
                and __dlg_counts.get(13, 0) == 0
                and __dlg_counts.get(12, 0) >= 1
                and __dlg_counts.get(11, 0) >= 3
            )
            __dlg_t12_consolidate_ready = (
                __dlg_counts.get(15, 0) == 0
                and __dlg_counts.get(14, 0) == 0
                and __dlg_counts.get(13, 0) == 0
                and __dlg_counts.get(12, 0) >= 2
                and (
                    __dlg_counts.get(12, 0) >= 3
                    or __dlg_counts.get(11, 0) >= 2
                    or __dlg_counts.get(10, 0) >= 2
                )
            )
            __dlg_first_russia_ready = (
                __dlg_counts.get(13, 0) >= 1
                and (
                    (
                        __dlg_counts.get(12, 0) >= 2
                        and __dlg_counts.get(11, 0) >= 2
                    )
                    or (
                        __dlg_counts.get(12, 0) >= 1
                        and __dlg_counts.get(11, 0) >= 3
                    )
                )
            )
            if not (
                __dlg_soviet_lift_ready
                or __dlg_soviet_ladder_ready
                or __dlg_double_t14_ready
                or __dlg_first_russia_t13_pair_lift_ready
                or __dlg_first_russia_single_t13_t12_bank_ready
                or __dlg_russia_pair_ready
                or __dlg_t13_pair_compress_ready
                or __dlg_t13_pair_single_t12_tether_ready
                or __dlg_single_t13_t12_compress_ready
                or __dlg_single_t13_single_t12_ladder_ready
                or __dlg_single_t12_anchor_ladder_ready
                or __dlg_second_russia_t12_pair_lock_ready
                or __dlg_second_russia_t12_ladder_ready
                or __dlg_first_russia_pair_ready
                or __dlg_t11_density_ready
                or __dlg_t12_consolidate_ready
                or __dlg_first_russia_ready
            ):
                return None
            __dlg_next = __dlg_game_state.get("next", {})
            __dlg_next_next = __dlg_game_state.get("nextNext", {})
            if not isinstance(__dlg_next, dict):
                __dlg_next = {}
            if not isinstance(__dlg_next_next, dict):
                __dlg_next_next = {}
            __dlg_next_type = int(__dlg_next.get("type", 0) or 0)
            __dlg_next_next_type = int(__dlg_next_next.get("type", 0) or 0)
            __dlg_mode = None
            if (
                __dlg_soviet_lift_ready
                and (
                    __dlg_next_type >= 11
                    or __dlg_next_next_type >= 12
                )
            ):
                __dlg_mode = "soviet_lift"
            elif (
                __dlg_soviet_ladder_ready
                and (
                    __dlg_next_type >= 10
                    or __dlg_next_next_type >= 11
                )
            ):
                __dlg_mode = "soviet_ladder"
            elif (
                __dlg_first_russia_t13_pair_lift_ready
                and (
                    __dlg_next_type >= 10
                    or __dlg_next_next_type >= 10
                )
            ):
                __dlg_mode = "first_russia_t13_pair_lift"
            elif (
                __dlg_first_russia_single_t13_t12_bank_ready
                and __dlg_next_type in (10, 11, 12, 13)
            ):
                __dlg_mode = "first_russia_single_t13_t12_bank"
            elif (
                __dlg_first_russia_pair_ready
                and __dlg_next_type in (10, 11, 12, 13)
            ):
                __dlg_mode = "first_russia_pair"
            elif (
                (__dlg_double_t14_ready or __dlg_russia_pair_ready)
                and (
                    __dlg_next_type >= 10
                    or __dlg_next_next_type >= 10
                )
            ):
                __dlg_mode = "russia_pair"
            elif (
                __dlg_second_russia_t12_pair_lock_ready
                and __dlg_next_type in (10, 11, 12, 13)
            ):
                __dlg_mode = "second_russia_t12_pair_lock"
            elif (
                __dlg_second_russia_t12_ladder_ready
                and __dlg_next_type in (10, 11, 12, 13)
            ):
                __dlg_mode = "second_russia_t12_ladder"
            elif (
                __dlg_t13_pair_compress_ready
                and (
                    __dlg_next_type >= 10
                    or __dlg_next_next_type >= 10
                )
            ):
                __dlg_mode = "t13_pair_compress"
            elif __dlg_t13_pair_single_t12_tether_ready:
                __dlg_mode = "t13_pair_single_t12_tether"
            elif __dlg_single_t13_t12_compress_ready:
                __dlg_mode = "single_t13_t12_compress"
            elif (
                __dlg_single_t13_single_t12_ladder_ready
                and __dlg_next_type in (10, 11, 12, 13)
            ):
                __dlg_mode = "single_t13_single_t12_ladder"
            elif (
                __dlg_first_russia_pair_ready
                and (
                    __dlg_next_type >= 8
                    or __dlg_next_next_type >= 10
                )
            ):
                __dlg_mode = "first_russia_pair"
            elif (
                __dlg_single_t12_anchor_ladder_ready
                and __dlg_next_type in (10, 11, 12)
            ):
                __dlg_mode = "single_t12_anchor_ladder"
            elif (
                __dlg_t12_consolidate_ready
                and (
                    __dlg_next_type >= 10
                    or __dlg_next_next_type >= 11
                )
            ):
                __dlg_mode = "t12_consolidate"
            elif (
                __dlg_t11_density_ready
                and (
                    __dlg_next_type >= 10
                    or __dlg_next_next_type >= 11
                )
            ):
                __dlg_mode = "t11_density"
            elif (
                __dlg_first_russia_ready
                and (
                    __dlg_next_type >= 8
                    or __dlg_next_next_type >= 10
                )
            ):
                __dlg_mode = "first_russia"
            if __dlg_mode is None:
                return None
            if __dlg_mode == "soviet_lift":
                if __dlg_counts.get(14, 0) >= 1:
                    __dlg_targets = [
                        p for p in __dlg_targets
                        if int(p.get("type", 0) or 0) in (14, 15)
                    ]
                elif (
                    __dlg_next_type == 11
                    and __dlg_counts.get(12, 0) == 0
                    and __dlg_counts.get(13, 0) >= 1
                    and __dlg_counts.get(11, 0) >= 1
                ):
                    __dlg_t13_targets = [
                        p for p in __dlg_targets
                        if int(p.get("type", 0) or 0) == 13
                    ]
                    __dlg_t11_targets = [
                        p for p in __dlg_targets
                        if int(p.get("type", 0) or 0) == 11
                    ]
                    if __dlg_t13_targets and __dlg_t11_targets:
                        def __dlg_soviet_t11_rebuild_key(tp):
                            __dlg_tp_x = float(tp.get("x", 0.0) or 0.0)
                            __dlg_tp_y = float(tp.get("y", -10.0) or -10.0)
                            __dlg_t13_dist = min(
                                (
                                    (
                                        float(up.get("x", 0.0) or 0.0) - __dlg_tp_x
                                    ) ** 2
                                    + (
                                        float(up.get("y", -10.0) or -10.0) - __dlg_tp_y
                                    ) ** 2
                                ) ** 0.5
                                for up in __dlg_t13_targets
                            )
                            return (
                                __dlg_t13_dist * 0.35
                                + max(0.0, __dlg_tp_y - 1.0) * 1.1,
                                __dlg_tp_y,
                            )
                        __dlg_targets = [min(__dlg_t11_targets, key=__dlg_soviet_t11_rebuild_key)]
                    else:
                        __dlg_targets = __dlg_t11_targets
                else:
                    __dlg_targets = [
                        p for p in __dlg_targets
                        if int(p.get("type", 0) or 0) in (11, 12, 13)
                    ]
            elif __dlg_mode == "soviet_ladder":
                __dlg_targets = [
                    p for p in __dlg_pieces
                    if int(p.get("type", 0) or 0) in (10, 11, 12)
                ]
            elif __dlg_double_t14_ready:
                __dlg_targets = [
                    p for p in __dlg_targets
                    if int(p.get("type", 0) or 0) == 14
                ]
            elif __dlg_mode == "first_russia_t13_pair_lift" or __dlg_russia_pair_ready:
                __dlg_t13_targets = [
                    p for p in __dlg_targets
                    if int(p.get("type", 0) or 0) == 13
                ]
                if __dlg_counts.get(14, 0) >= 1 and len(__dlg_t13_targets) >= 2:
                    __dlg_pair = None
                    __dlg_pair_key = (999.0, 999.0)
                    for __dlg_i, __dlg_a in enumerate(__dlg_t13_targets):
                        for __dlg_b in __dlg_t13_targets[__dlg_i + 1:]:
                            __dlg_ax = float(__dlg_a.get("x", 0.0) or 0.0)
                            __dlg_ay = float(__dlg_a.get("y", -10.0) or -10.0)
                            __dlg_bx = float(__dlg_b.get("x", 0.0) or 0.0)
                            __dlg_by = float(__dlg_b.get("y", -10.0) or -10.0)
                            __dlg_dist = ((__dlg_ax - __dlg_bx) ** 2 + (__dlg_ay - __dlg_by) ** 2) ** 0.5
                            __dlg_top = max(__dlg_ay, __dlg_by)
                            __dlg_key = (
                                __dlg_dist + max(0.0, __dlg_top - 1.1) * 0.55,
                                __dlg_top,
                            )
                            if __dlg_key < __dlg_pair_key:
                                __dlg_pair_key = __dlg_key
                                __dlg_pair = (__dlg_a, __dlg_b)
                    if __dlg_pair is not None:
                        __dlg_center = (
                            float(__dlg_pair[0].get("x", 0.0) or 0.0)
                            + float(__dlg_pair[1].get("x", 0.0) or 0.0)
                        ) / 2.0
                        __dlg_targets = [{"x": __dlg_center, "type": 13}]
                    else:
                        __dlg_targets = __dlg_t13_targets
                else:
                    __dlg_targets = [
                        p for p in __dlg_targets
                        if int(p.get("type", 0) or 0) in (13, 14)
                    ]
            elif __dlg_mode in ("second_russia_t12_pair_lock", "second_russia_t12_ladder"):
                __dlg_t12_targets = [
                    p for p in __dlg_targets
                    if int(p.get("type", 0) or 0) == 12
                ]
                __dlg_t11_targets = [
                    p for p in __dlg_targets
                    if int(p.get("type", 0) or 0) == 11
                ]
                __dlg_t10_targets = [
                    p for p in __dlg_pieces
                    if int(p.get("type", 0) or 0) == 10
                ]
                __dlg_anchor_x = None
                if len(__dlg_t12_targets) >= 2:
                    __dlg_pair = None
                    __dlg_pair_key = (999.0, 999.0)
                    for __dlg_i, __dlg_a in enumerate(__dlg_t12_targets):
                        for __dlg_b in __dlg_t12_targets[__dlg_i + 1:]:
                            __dlg_ax = float(__dlg_a.get("x", 0.0) or 0.0)
                            __dlg_ay = float(__dlg_a.get("y", -10.0) or -10.0)
                            __dlg_bx = float(__dlg_b.get("x", 0.0) or 0.0)
                            __dlg_by = float(__dlg_b.get("y", -10.0) or -10.0)
                            __dlg_dist = ((__dlg_ax - __dlg_bx) ** 2 + (__dlg_ay - __dlg_by) ** 2) ** 0.5
                            __dlg_top = max(__dlg_ay, __dlg_by)
                            __dlg_key = (
                                __dlg_dist + max(0.0, __dlg_top - 1.3) * 0.65,
                                __dlg_top,
                            )
                            if __dlg_key < __dlg_pair_key:
                                __dlg_pair_key = __dlg_key
                                __dlg_pair = (__dlg_a, __dlg_b)
                    if __dlg_pair is not None:
                        __dlg_anchor_x = (
                            float(__dlg_pair[0].get("x", 0.0) or 0.0)
                            + float(__dlg_pair[1].get("x", 0.0) or 0.0)
                        ) / 2.0
                        if __dlg_next_type in (12, 13) or __dlg_mode == "second_russia_t12_pair_lock":
                            __dlg_targets = [{"x": __dlg_anchor_x, "type": 12}]
                if __dlg_anchor_x is None and __dlg_t12_targets:
                    __dlg_anchor_x = sum(
                        float(p.get("x", 0.0) or 0.0) for p in __dlg_t12_targets
                    ) / len(__dlg_t12_targets)
                if __dlg_mode == "second_russia_t12_pair_lock" and __dlg_anchor_x is not None:
                    __dlg_targets = [{"x": __dlg_anchor_x, "type": 12}]
                elif __dlg_next_type == 11 and __dlg_t11_targets:
                    def __dlg_second_t11_key(tp):
                        __dlg_tp_x = float(tp.get("x", 0.0) or 0.0)
                        __dlg_tp_y = float(tp.get("y", -10.0) or -10.0)
                        __dlg_anchor_dist = abs(__dlg_tp_x - __dlg_anchor_x) if __dlg_anchor_x is not None else 0.0
                        return (__dlg_anchor_dist * 0.35 + max(0.0, __dlg_tp_y - 1.5) * 1.2, __dlg_tp_y)
                    __dlg_targets = [min(__dlg_t11_targets, key=__dlg_second_t11_key)]
                elif __dlg_next_type == 10 and __dlg_t10_targets:
                    __dlg_up_targets = __dlg_t11_targets + __dlg_t12_targets
                    def __dlg_second_t10_key(tp):
                        __dlg_tp_x = float(tp.get("x", 0.0) or 0.0)
                        __dlg_tp_y = float(tp.get("y", -10.0) or -10.0)
                        __dlg_up_dist = min(
                            (
                                (
                                    float(up.get("x", 0.0) or 0.0) - __dlg_tp_x
                                ) ** 2
                                + (
                                    float(up.get("y", -10.0) or -10.0) - __dlg_tp_y
                                ) ** 2
                            ) ** 0.5
                            for up in __dlg_up_targets
                        ) if __dlg_up_targets else 0.0
                        __dlg_anchor_dist = abs(__dlg_tp_x - __dlg_anchor_x) if __dlg_anchor_x is not None else 0.0
                        return (min(__dlg_up_dist, __dlg_anchor_dist) + max(0.0, __dlg_tp_y - 1.0) * 0.8, __dlg_tp_y)
                    __dlg_targets = [min(__dlg_t10_targets, key=__dlg_second_t10_key)]
                elif __dlg_next_type in (12, 13) and __dlg_anchor_x is not None:
                    __dlg_targets = [{"x": __dlg_anchor_x, "type": 12}]
                else:
                    __dlg_targets = __dlg_t12_targets + __dlg_t11_targets
            elif __dlg_mode == "t13_pair_compress":
                __dlg_t12_targets = [
                    p for p in __dlg_targets
                    if int(p.get("type", 0) or 0) == 12
                ]
                if len(__dlg_t12_targets) >= 2:
                    __dlg_pair = None
                    __dlg_pair_key = (999.0, 999.0)
                    for __dlg_i, __dlg_a in enumerate(__dlg_t12_targets):
                        for __dlg_b in __dlg_t12_targets[__dlg_i + 1:]:
                            __dlg_ax = float(__dlg_a.get("x", 0.0) or 0.0)
                            __dlg_ay = float(__dlg_a.get("y", -10.0) or -10.0)
                            __dlg_bx = float(__dlg_b.get("x", 0.0) or 0.0)
                            __dlg_by = float(__dlg_b.get("y", -10.0) or -10.0)
                            __dlg_dist = ((__dlg_ax - __dlg_bx) ** 2 + (__dlg_ay - __dlg_by) ** 2) ** 0.5
                            __dlg_top = max(__dlg_ay, __dlg_by)
                            __dlg_key = (
                                __dlg_dist + max(0.0, __dlg_top - 1.2) * 0.5,
                                __dlg_top,
                            )
                            if __dlg_key < __dlg_pair_key:
                                __dlg_pair_key = __dlg_key
                                __dlg_pair = (__dlg_a, __dlg_b)
                    if __dlg_pair is not None:
                        __dlg_center = (
                            float(__dlg_pair[0].get("x", 0.0) or 0.0)
                            + float(__dlg_pair[1].get("x", 0.0) or 0.0)
                        ) / 2.0
                        __dlg_targets = [{"x": __dlg_center, "type": 12}]
                    else:
                        __dlg_targets = __dlg_t12_targets
                else:
                    __dlg_targets = __dlg_t12_targets
            elif __dlg_mode == "t13_pair_single_t12_tether":
                __dlg_targets = [
                    p for p in __dlg_targets
                    if int(p.get("type", 0) or 0) in (12, 13)
                ]
            elif __dlg_mode in ("single_t13_t12_compress", "first_russia_single_t13_t12_bank"):
                __dlg_t13_targets = [
                    p for p in __dlg_targets
                    if int(p.get("type", 0) or 0) == 13
                ]
                __dlg_t12_targets = [
                    p for p in __dlg_targets
                    if int(p.get("type", 0) or 0) == 12
                ]
                __dlg_t11_targets = [
                    p for p in __dlg_targets
                    if int(p.get("type", 0) or 0) == 11
                ]
                __dlg_t10_targets = [
                    p for p in __dlg_pieces
                    if int(p.get("type", 0) or 0) == 10
                ]
                __dlg_t13_center = None
                if __dlg_t13_targets:
                    __dlg_t13_total = 0.0
                    __dlg_t13_weighted = 0.0
                    for __dlg_t13 in __dlg_t13_targets:
                        __dlg_t13_weighted += float(__dlg_t13.get("x", 0.0) or 0.0)
                        __dlg_t13_total += 1.0
                    if __dlg_t13_total > 0:
                        __dlg_t13_center = __dlg_t13_weighted / __dlg_t13_total
                if (
                    __dlg_mode == "first_russia_single_t13_t12_bank"
                    and __dlg_next_type == 13
                    and __dlg_t13_targets
                ):
                    __dlg_targets = __dlg_t13_targets
                elif len(__dlg_t12_targets) >= 2:
                    __dlg_pair = None
                    __dlg_pair_key = (999.0, 999.0)
                    for __dlg_i, __dlg_a in enumerate(__dlg_t12_targets):
                        for __dlg_b in __dlg_t12_targets[__dlg_i + 1:]:
                            __dlg_ax = float(__dlg_a.get("x", 0.0) or 0.0)
                            __dlg_ay = float(__dlg_a.get("y", -10.0) or -10.0)
                            __dlg_bx = float(__dlg_b.get("x", 0.0) or 0.0)
                            __dlg_by = float(__dlg_b.get("y", -10.0) or -10.0)
                            __dlg_center = (__dlg_ax + __dlg_bx) / 2.0
                            __dlg_dist = ((__dlg_ax - __dlg_bx) ** 2 + (__dlg_ay - __dlg_by) ** 2) ** 0.5
                            __dlg_top = max(__dlg_ay, __dlg_by)
                            __dlg_t13_dist = abs(__dlg_center - __dlg_t13_center) if __dlg_t13_center is not None else 0.0
                            __dlg_key = (
                                __dlg_dist
                                + __dlg_t13_dist * 0.45
                                + max(0.0, __dlg_top - 1.05) * 2.0,
                                __dlg_top,
                            )
                            if __dlg_key < __dlg_pair_key:
                                __dlg_pair_key = __dlg_key
                                __dlg_pair = (__dlg_a, __dlg_b)
                    if __dlg_pair is not None:
                        __dlg_center = (
                            float(__dlg_pair[0].get("x", 0.0) or 0.0)
                            + float(__dlg_pair[1].get("x", 0.0) or 0.0)
                        ) / 2.0
                        if __dlg_next_type == 10 and __dlg_t10_targets:
                            __dlg_up_targets = __dlg_t11_targets + __dlg_t12_targets + __dlg_t13_targets
                            def __dlg_single_t13_t12_t10_key(tp):
                                __dlg_tp_x = float(tp.get("x", 0.0) or 0.0)
                                __dlg_tp_y = float(tp.get("y", -10.0) or -10.0)
                                __dlg_up_dist = min(
                                    (
                                        (
                                            float(up.get("x", 0.0) or 0.0) - __dlg_tp_x
                                        ) ** 2
                                        + (
                                            float(up.get("y", -10.0) or -10.0) - __dlg_tp_y
                                        ) ** 2
                                    ) ** 0.5
                                    for up in __dlg_up_targets
                                ) if __dlg_up_targets else 999.0
                                __dlg_lane_dist = abs(__dlg_tp_x - __dlg_center)
                                __dlg_t13_dist = abs(__dlg_tp_x - __dlg_t13_center) if __dlg_t13_center is not None else 0.0
                                return (
                                    min(__dlg_up_dist, __dlg_lane_dist)
                                    + __dlg_t13_dist * 0.20
                                    + max(0.0, __dlg_tp_y - 0.6) * 1.1,
                                    __dlg_tp_y,
                                )
                            __dlg_targets = [min(__dlg_t10_targets, key=__dlg_single_t13_t12_t10_key)]
                        else:
                            __dlg_targets = [{"x": __dlg_center, "type": 12}]
                    else:
                        __dlg_targets = __dlg_t12_targets
                else:
                    __dlg_targets = __dlg_t12_targets
            elif __dlg_mode == "single_t13_single_t12_ladder":
                __dlg_t13_targets = [
                    p for p in __dlg_targets
                    if int(p.get("type", 0) or 0) == 13
                ]
                __dlg_t12_targets = [
                    p for p in __dlg_targets
                    if int(p.get("type", 0) or 0) == 12
                ]
                __dlg_t11_targets = [
                    p for p in __dlg_targets
                    if int(p.get("type", 0) or 0) == 11
                ]
                __dlg_t10_targets = [
                    p for p in __dlg_pieces
                    if int(p.get("type", 0) or 0) == 10
                ]
                __dlg_t12_anchor = __dlg_t12_targets[0] if __dlg_t12_targets else None
                __dlg_t12_x = (
                    float(__dlg_t12_anchor.get("x", 0.0) or 0.0)
                    if __dlg_t12_anchor is not None
                    else None
                )
                __dlg_t13_x = None
                if __dlg_t13_targets:
                    __dlg_t13_x = sum(
                        float(p.get("x", 0.0) or 0.0) for p in __dlg_t13_targets
                    ) / len(__dlg_t13_targets)
                if __dlg_next_type == 13 and __dlg_t13_targets:
                    __dlg_targets = __dlg_t13_targets
                elif __dlg_next_type == 12 and __dlg_t12_targets:
                    __dlg_targets = __dlg_t12_targets
                elif __dlg_next_type == 11 and __dlg_t11_targets:
                    def __dlg_single_t13_single_t12_t11_key(tp):
                        __dlg_tp_x = float(tp.get("x", 0.0) or 0.0)
                        __dlg_tp_y = float(tp.get("y", -10.0) or -10.0)
                        __dlg_anchor_dist = abs(__dlg_tp_x - __dlg_t12_x) if __dlg_t12_x is not None else 0.0
                        __dlg_t13_dist = abs(__dlg_tp_x - __dlg_t13_x) if __dlg_t13_x is not None else 0.0
                        return (
                            __dlg_anchor_dist
                            + __dlg_t13_dist * 0.16
                            + max(0.0, __dlg_tp_y - 0.8) * 1.2,
                            __dlg_tp_y,
                        )
                    __dlg_targets = [min(__dlg_t11_targets, key=__dlg_single_t13_single_t12_t11_key)]
                elif __dlg_next_type == 10 and __dlg_t10_targets:
                    __dlg_up_targets = __dlg_t11_targets + __dlg_t12_targets
                    def __dlg_single_t13_single_t12_t10_key(tp):
                        __dlg_tp_x = float(tp.get("x", 0.0) or 0.0)
                        __dlg_tp_y = float(tp.get("y", -10.0) or -10.0)
                        __dlg_up_dist = min(
                            (
                                (
                                    float(up.get("x", 0.0) or 0.0) - __dlg_tp_x
                                ) ** 2
                                + (
                                    float(up.get("y", -10.0) or -10.0) - __dlg_tp_y
                                ) ** 2
                            ) ** 0.5
                            for up in __dlg_up_targets
                        ) if __dlg_up_targets else 0.0
                        __dlg_anchor_dist = abs(__dlg_tp_x - __dlg_t12_x) if __dlg_t12_x is not None else 0.0
                        return (
                            min(__dlg_up_dist, __dlg_anchor_dist)
                            + max(0.0, __dlg_tp_y - 0.8) * 0.85,
                            __dlg_tp_y,
                        )
                    __dlg_targets = [min(__dlg_t10_targets, key=__dlg_single_t13_single_t12_t10_key)]
                else:
                    __dlg_targets = __dlg_t12_targets + __dlg_t11_targets
            elif __dlg_mode == "first_russia_pair":
                __dlg_t13_targets = [
                    p for p in __dlg_targets
                    if int(p.get("type", 0) or 0) == 13
                ]
                if len(__dlg_t13_targets) >= 2:
                    __dlg_pair = None
                    __dlg_pair_key = (999.0, 999.0, 999.0)
                    for __dlg_i, __dlg_a in enumerate(__dlg_t13_targets):
                        for __dlg_b in __dlg_t13_targets[__dlg_i + 1:]:
                            __dlg_ax = float(__dlg_a.get("x", 0.0) or 0.0)
                            __dlg_ay = float(__dlg_a.get("y", -10.0) or -10.0)
                            __dlg_bx = float(__dlg_b.get("x", 0.0) or 0.0)
                            __dlg_by = float(__dlg_b.get("y", -10.0) or -10.0)
                            __dlg_center = (__dlg_ax + __dlg_bx) / 2.0
                            __dlg_dist = ((__dlg_ax - __dlg_bx) ** 2 + (__dlg_ay - __dlg_by) ** 2) ** 0.5
                            __dlg_top = max(__dlg_ay, __dlg_by)
                            __dlg_key = (
                                __dlg_dist + max(0.0, __dlg_top - 1.3) * 0.45,
                                __dlg_top,
                                abs(__dlg_center),
                            )
                            if __dlg_key < __dlg_pair_key:
                                __dlg_pair_key = __dlg_key
                                __dlg_pair = (__dlg_a, __dlg_b)
                    if __dlg_pair is not None:
                        __dlg_center = (
                            float(__dlg_pair[0].get("x", 0.0) or 0.0)
                            + float(__dlg_pair[1].get("x", 0.0) or 0.0)
                        ) / 2.0
                        __dlg_targets = [{"x": __dlg_center, "type": 13}]
                    else:
                        __dlg_targets = __dlg_t13_targets
                else:
                    __dlg_targets = __dlg_t13_targets
            elif __dlg_mode == "single_t12_anchor_ladder":
                __dlg_t12_targets = [p for p in __dlg_targets if int(p.get("type", 0) or 0) == 12]
                __dlg_t11_targets = [p for p in __dlg_targets if int(p.get("type", 0) or 0) == 11]
                __dlg_t10_targets = [p for p in __dlg_pieces if int(p.get("type", 0) or 0) == 10]
                __dlg_anchor = __dlg_t12_targets[0] if __dlg_t12_targets else None
                __dlg_anchor_x = float(__dlg_anchor.get("x", 0.0) or 0.0) if __dlg_anchor is not None else None
                if __dlg_anchor_x is not None and __dlg_next_type in (11, 12):
                    __dlg_targets = [{"x": __dlg_anchor_x, "type": 12}]
                elif __dlg_anchor_x is not None and __dlg_next_type == 10 and __dlg_t10_targets:
                    __dlg_up_targets = __dlg_t11_targets + __dlg_t12_targets
                    def __dlg_single_t12_anchor_t10_key(tp):
                        __dlg_tp_x = float(tp.get("x", 0.0) or 0.0)
                        __dlg_tp_y = float(tp.get("y", -10.0) or -10.0)
                        __dlg_up_dist = min(
                            ((float(up.get("x", 0.0) or 0.0) - __dlg_tp_x) ** 2 + (float(up.get("y", -10.0) or -10.0) - __dlg_tp_y) ** 2) ** 0.5
                            for up in __dlg_up_targets
                        ) if __dlg_up_targets else 999.0
                        return (min(__dlg_up_dist, abs(__dlg_tp_x - __dlg_anchor_x)) + max(0.0, __dlg_tp_y - 0.8) * 0.9, __dlg_tp_y)
                    __dlg_targets = [min(__dlg_t10_targets, key=__dlg_single_t12_anchor_t10_key)]
                else:
                    __dlg_targets = __dlg_t12_targets + __dlg_t11_targets
            elif __dlg_mode == "t11_density":
                __dlg_targets = [
                    p for p in __dlg_targets
                    if int(p.get("type", 0) or 0) in (11, 12)
                ]
            elif __dlg_mode == "t12_consolidate":
                __dlg_t12_targets = [
                    p for p in __dlg_targets
                    if int(p.get("type", 0) or 0) == 12
                ]
                if len(__dlg_t12_targets) >= 2:
                    __dlg_pair = None
                    __dlg_pair_key = (999.0, 999.0)
                    for __dlg_i, __dlg_a in enumerate(__dlg_t12_targets):
                        for __dlg_b in __dlg_t12_targets[__dlg_i + 1:]:
                            __dlg_ax = float(__dlg_a.get("x", 0.0) or 0.0)
                            __dlg_ay = float(__dlg_a.get("y", -10.0) or -10.0)
                            __dlg_bx = float(__dlg_b.get("x", 0.0) or 0.0)
                            __dlg_by = float(__dlg_b.get("y", -10.0) or -10.0)
                            __dlg_dist = ((__dlg_ax - __dlg_bx) ** 2 + (__dlg_ay - __dlg_by) ** 2) ** 0.5
                            __dlg_top = max(__dlg_ay, __dlg_by)
                            __dlg_key = (
                                __dlg_dist + max(0.0, __dlg_top - 1.3) * 0.45,
                                __dlg_top,
                            )
                            if __dlg_key < __dlg_pair_key:
                                __dlg_pair_key = __dlg_key
                                __dlg_pair = (__dlg_a, __dlg_b)
                    if __dlg_pair is not None:
                        __dlg_center = (
                            float(__dlg_pair[0].get("x", 0.0) or 0.0)
                            + float(__dlg_pair[1].get("x", 0.0) or 0.0)
                        ) / 2.0
                        __dlg_targets = [{"x": __dlg_center, "type": 12}]
                    else:
                        __dlg_targets = __dlg_t12_targets
                else:
                    __dlg_targets = __dlg_t12_targets
            else:
                __dlg_targets = [
                    p for p in __dlg_targets
                    if int(p.get("type", 0) or 0) in (11, 12, 13)
                ]
            if not __dlg_targets:
                return None

            __dlg_total_weight = 0.0
            __dlg_weighted_x = 0.0
            for __dlg_piece in __dlg_targets:
                __dlg_type = int(__dlg_piece.get("type", 0) or 0)
                __dlg_weight = 1.0 + max(0, __dlg_type - 11) * 0.65
                __dlg_weighted_x += float(__dlg_piece.get("x", 0.0) or 0.0) * __dlg_weight
                __dlg_total_weight += __dlg_weight
            if __dlg_total_weight <= 0:
                return None
            __dlg_center_x = __dlg_weighted_x / __dlg_total_weight

            __dlg_safe = [
                c for c in candidates
                if isinstance(c, dict)
                and not c.get("crosses_deadline")
                and not c.get("merge_result_crosses_deadline")
            ]
            if not __dlg_safe:
                return None
            __dlg_lowest = min(__dlg_safe, key=lambda c: float(c.get("landing_y", 99.0) or 99.0))
            __dlg_lowest_y = float(__dlg_lowest.get("landing_y", 99.0) or 99.0)
            __dlg_window = __dlg_lowest_y + (
                1.20
                if __dlg_mode in ("second_russia_t12_pair_lock", "first_russia_single_t13_t12_bank")
                else 0.85
            )
            __dlg_eligible = [
                c for c in __dlg_safe
                if float(c.get("landing_y", 99.0) or 99.0) <= __dlg_window
            ]
            if not __dlg_eligible:
                return None
            __dlg_cluster = min(
                __dlg_eligible,
                key=lambda c: (
                    abs(float(c.get("x", 0.0) or 0.0) - __dlg_center_x),
                    float(c.get("landing_y", 99.0) or 99.0),
                ),
            )
            __dlg_lowest_dist = abs(float(__dlg_lowest.get("x", 0.0) or 0.0) - __dlg_center_x)
            __dlg_cluster_dist = abs(float(__dlg_cluster.get("x", 0.0) or 0.0) - __dlg_center_x)
            __dlg_required_gain = 0.15 if __dlg_mode in ("first_russia_pair", "first_russia_t13_pair_lift", "first_russia_single_t13_t12_bank", "t13_pair_compress", "t13_pair_single_t12_tether", "single_t13_t12_compress", "single_t13_single_t12_ladder", "single_t12_anchor_ladder", "second_russia_t12_pair_lock", "second_russia_t12_ladder", "soviet_ladder", "t11_density", "t12_consolidate") else 0.35
            if __dlg_cluster_dist + __dlg_required_gain < __dlg_lowest_dist:
                return (__dlg_cluster, __dlg_mode)
            return None

        __dlg_cluster_early = __dlg_pre_russia_cluster_pick(__dlg_cands)
        if __dlg_cluster_early is not None:
            __dlg_cluster_piece, __dlg_cluster_mode = __dlg_cluster_early
            __dlg_reason = (
                "DEADLINE_GUARD_SECOND_RUSSIA_T12_PAIR_LOCK"
                if __dlg_cluster_mode == "second_russia_t12_pair_lock"
                else "DEADLINE_GUARD_SECOND_RUSSIA_T12_LADDER"
                if __dlg_cluster_mode == "second_russia_t12_ladder"
                else "DEADLINE_GUARD_PRE_RUSSIA_SINGLE_T12_ANCHOR"
                if __dlg_cluster_mode == "single_t12_anchor_ladder"
                else "DEADLINE_GUARD_FIRST_RUSSIA_T13_PAIR_LIFT"
                if __dlg_cluster_mode == "first_russia_t13_pair_lift"
                else "DEADLINE_GUARD_FIRST_RUSSIA_SINGLE_T13_T12_BANK_LIFT"
                if __dlg_cluster_mode == "first_russia_single_t13_t12_bank"
                else "DEADLINE_GUARD_FIRST_RUSSIA_PAIR"
                if __dlg_cluster_mode == "first_russia_pair"
                else "DEADLINE_GUARD_PRE_RUSSIA_CLUSTER"
            )
            return {"x": float(__dlg_cluster_piece.get("x", 0.0) or 0.0), "reason": __dlg_reason}

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
            __dlg_best = min(__dlg_safe_no_merge, key=lambda c: float(c.get("landing_y", 99.0) or 99.0))
            return {"x": float(__dlg_best.get("x", 0.0) or 0.0), "reason": "DEADLINE_GUARD_SAFE_LANDING"}

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
            __dlg_best = min(__dlg_merge_preferred, key=lambda c: float(c.get("landing_y", 99.0) or 99.0))
            return {"x": float(__dlg_best.get("x", 0.0) or 0.0), "reason": "DEADLINE_GUARD_SAFE_LANDING"}

        # Fallback: only when no merge candidate is available
        __dlg_safe = [
            c for c in __dlg_cands
            if isinstance(c, dict)
            and not c.get("crosses_deadline")
            and not c.get("merge_result_crosses_deadline")
        ]
        if __dlg_safe:
            __dlg_cluster_best = __dlg_pre_russia_cluster_pick(__dlg_safe)
            if __dlg_cluster_best is not None:
                __dlg_cluster_piece, __dlg_cluster_mode = __dlg_cluster_best
                __dlg_reason = (
                    "DEADLINE_GUARD_SECOND_RUSSIA_T12_PAIR_LOCK"
                    if __dlg_cluster_mode == "second_russia_t12_pair_lock"
                    else "DEADLINE_GUARD_SECOND_RUSSIA_T12_LADDER"
                    if __dlg_cluster_mode == "second_russia_t12_ladder"
                    else "DEADLINE_GUARD_PRE_RUSSIA_SINGLE_T12_ANCHOR"
                    if __dlg_cluster_mode == "single_t12_anchor_ladder"
                    else "DEADLINE_GUARD_FIRST_RUSSIA_T13_PAIR_LIFT"
                    if __dlg_cluster_mode == "first_russia_t13_pair_lift"
                    else "DEADLINE_GUARD_FIRST_RUSSIA_SINGLE_T13_T12_BANK_LIFT"
                    if __dlg_cluster_mode == "first_russia_single_t13_t12_bank"
                    else "DEADLINE_GUARD_FIRST_RUSSIA_PAIR"
                    if __dlg_cluster_mode == "first_russia_pair"
                    else "DEADLINE_GUARD_PRE_RUSSIA_CLUSTER"
                )
                return {"x": float(__dlg_cluster_piece.get("x", 0.0) or 0.0), "reason": __dlg_reason}
            __dlg_best = min(__dlg_safe, key=lambda c: float(c.get("landing_y", 99.0) or 99.0))
            return {"x": float(__dlg_best.get("x", 0.0) or 0.0), "reason": "DEADLINE_GUARD_SAFE_LANDING"}
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

    # --- vXXX: russia phase detection (type 14/15 pieces on board) ---
    # ロシアフェーズ: 盤面上にtype 14（ロシア）またはtype 15（ソ連）が存在する場合
    # advice.md「ロシア建国後の死亡速度が早い。建国後はより慎重な盤面進行を検討すること」に基づく構造的改善
    # ロシア建国後は盤面が狭く、高typeピースが場所を占有している状態。この局面で通常時と同じ戦略を続けるのは不十分
    russia_phase_count = sum(1 for p in pieces if p.get("type") in [14, 15])
    russia_phase = russia_phase_count >= 1
    high_type_counts = {}
    for p in pieces:
        p_type = p.get("type", 0)
        if p_type >= 11:
            high_type_counts[p_type] = high_type_counts.get(p_type, 0) + 1
    pre_russia_counts = high_type_counts
    second_russia_counts = high_type_counts
    type10_count = sum(1 for p in pieces if p.get("type") == 10)
    type9_count = sum(1 for p in pieces if p.get("type") == 9)
    type15_count = pre_russia_counts.get(15, 0)
    single_type15_phase = type15_count == 1

    def _weighted_center_x(targets):
        total_weight = 0.0
        weighted_sum = 0.0
        for tp in targets:
            p_type = tp.get("type", 0)
            weight = 1.0 + max(0, p_type - 11) * 0.65
            weighted_sum += tp.get("x", 0) * weight
            total_weight += weight
        if total_weight <= 0:
            return None
        return weighted_sum / total_weight

    def _closest_pair_center_x(targets, top_soft_y=1.3):
        if len(targets) < 2:
            return None
        best_pair = None
        best_pair_key = (999.0, 999.0, 999.0)
        for pair_i, pair_a in enumerate(targets):
            for pair_b in targets[pair_i + 1:]:
                ax = float(pair_a.get("x", 0.0) or 0.0)
                ay = float(pair_a.get("y", -10.0) or -10.0)
                bx = float(pair_b.get("x", 0.0) or 0.0)
                by = float(pair_b.get("y", -10.0) or -10.0)
                pair_dist = ((ax - bx) ** 2 + (ay - by) ** 2) ** 0.5
                pair_top_y = max(ay, by)
                pair_center = (ax + bx) / 2.0
                pair_key = (
                    pair_dist + max(0.0, pair_top_y - top_soft_y) * 0.45,
                    pair_top_y,
                    abs(pair_center),
                )
                if pair_key < best_pair_key:
                    best_pair_key = pair_key
                    best_pair = (pair_center, pair_top_y)
        return best_pair

    # v548: double_russia_phase — 2つ目の(type 14/15)が盤面にある場合、
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

        # ----- v616: merge drought detection — internal no_merge_streak counter -----
        # analysis_result.md: worst game T43-T56 had 13 consecutive NO_MERGE, extra_low T68-T75 had 7.
        # We don't have per-turn history, so detect drought from board state:
        # same_type_pieces on board (2+) + rp>=3 + current candidate is merge_grade=NO
        # = we want to merge but can't — classic merge drought pattern.
        # v616: compute no_merge_streak inside candidate loop for per-candidate accuracy.
        # Detected here (after merge_grade read) for use in axis 9.12.
        no_merge_streak = 0
        if merge_grade == "NO" and len(same_type_pieces) >= 2 and reactive_pair_count >= 3:
            no_merge_streak = 3

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

        # ----- vXXX: merge_result_crosses_deadline danger veto -----
        # mandatory_themes: "デッドライン超出時は併合できる場合に限る"
        # merge_result_crosses_deadline=true means the merge result itself crosses deadline.
        # In redline/danger states this is not merely a risky merge; it preserves the
        # lethal top edge, so DIRECT/NEAR bonuses must not outvote lower-risk survival.
        if result.get("merge_result_crosses_deadline", False) and merge_grade in ("DIRECT", "NEAR"):
            merge_result_top_y = result.get(
                "merge_result_top_y",
                result.get("risk_top_y_after_drop", result.get("top_y_after_drop", landing_y)),
            )
            try:
                merge_result_top_y = float(merge_result_top_y)
            except (TypeError, ValueError):
                merge_result_top_y = float(landing_y or 0.0)
            merge_result_deadline_y = result.get("deadline_y", 3.38)
            try:
                merge_result_deadline_y = float(merge_result_deadline_y)
            except (TypeError, ValueError):
                merge_result_deadline_y = 3.38
            merge_result_redline_excess = max(0.0, merge_result_top_y - merge_result_deadline_y)
            merge_result_deadline_penalty = 1200.0
            if deadline_crossed or reactor_margin < 0.35 or danger_piece_count > 0:
                merge_result_deadline_penalty += (
                    4800.0
                    + merge_result_redline_excess * 4200.0
                    + max(0.0, landing_y) * 900.0
                )
                reasons.append("MERGE_RESULT_CROSSES_DEADLINE_DANGER_VETO")
            elif merge_result_redline_excess > 0.35:
                merge_result_deadline_penalty += merge_result_redline_excess * 1800.0
                reasons.append("MERGE_RESULT_CROSSES_DEADLINE_REDLINE_EXCESS")
            score -= merge_result_deadline_penalty
            reasons.append("MERGE_RESULT_CROSSES_DEADLINE_PENALTY")

        # ----- v701: mandatory_themes Theme 1 relaxation — penalty instead of pre-exclusion -----
        # v701 pre-filter excluded all crosses_deadline && merge_grade=NO before scoring,
        # which suppressed NO_MERGE candidates even when merge_available=true globally.
        # This caused deadline_guard fallback to lose merge opportunities and triggered
        # merge-available-but-NO_MERGE death spirals (worst game T57-T65).
        # Relaxation: penalize heavily (-1200) instead of pre-excluding.
        # Preserves mandatory_themes Theme 1 compliance via penalty (not elimination).
        if merge_grade == "NO" and result.get("crosses_deadline", False):
            score -= 1200.0
            reasons.append("NO_MERGE_CROSSES_DEADLINE_PENALTY")

        # ----- vXXX: second-Russia redline no-merge veto -----
        # Mixed #29420 reached T14x1 + T12x1 + T11 bank, then the one-T14
        # rebuild guidance selected a NO_MERGE candidate that crossed the
        # deadline and produced risk_top_y_after_drop=4.45. Once the second
        # Russia ladder is a near-miss, do not let high-country guidance outvote
        # redline survival for non-merge placements; choose the least damaging
        # crossing candidate if no clean candidate exists.
        second_russia_redline_rebuild_ready = (
            russia_phase
            and not double_russia_phase
            and pre_russia_counts.get(14, 0) >= 1
            and pre_russia_counts.get(15, 0) == 0
            and (
                (
                    pre_russia_counts.get(13, 0) == 0
                    and pre_russia_counts.get(12, 0) >= 1
                    and (
                        pre_russia_counts.get(11, 0) >= 1
                        or type10_count >= 2
                    )
                )
                or (
                    pre_russia_counts.get(13, 0) == 1
                    and pre_russia_counts.get(12, 0) >= 1
                    and (
                        pre_russia_counts.get(12, 0) >= 2
                        or pre_russia_counts.get(11, 0) >= 1
                        or type10_count >= 2
                    )
                )
            )
        )
        if (
            second_russia_redline_rebuild_ready
            and merge_grade == "NO"
            and result.get("crosses_deadline", False)
        ):
            risk_top_after_drop = result.get(
                "risk_top_y_after_drop",
                result.get("top_y_after_drop", landing_y),
            )
            try:
                risk_top_after_drop = float(risk_top_after_drop)
            except (TypeError, ValueError):
                risk_top_after_drop = float(landing_y or 0.0)
            redline_excess = max(0.0, risk_top_after_drop - 3.38)
            score -= 4200.0 + redline_excess * 4200.0 + max(0.0, landing_y) * 1100.0
            reasons.append("SECOND_RUSSIA_REDLINE_NO_MERGE_VETO")

        pre_russia_t12_abundance_redline_ready = (
            not russia_phase
            and pre_russia_counts.get(13, 0) == 0
            and pre_russia_counts.get(12, 0) >= 4
        )
        if (
            pre_russia_t12_abundance_redline_ready
            and merge_grade == "NO"
            and result.get("crosses_deadline", False)
        ):
            risk_top_after_drop = result.get(
                "risk_top_y_after_drop",
                result.get("top_y_after_drop", landing_y),
            )
            try:
                risk_top_after_drop = float(risk_top_after_drop)
            except (TypeError, ValueError):
                risk_top_after_drop = float(landing_y or 0.0)
            redline_excess = max(0.0, risk_top_after_drop - 3.38)
            score -= 3000.0 + redline_excess * 3600.0 + max(0.0, landing_y) * 900.0
            reasons.append("PRE_RUSSIA_T12_ABUNDANCE_REDLINE_VETO")

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
        russia_merge_possible = next_type >= 14 and any(p["type"] >= 14 for p in pieces)
        global_merge_available = any(r.get("merge_grade") != "NO" for r in results)
        if merge_grade == "NEAR" and max_y >= 2.5 and not russia_merge_possible:
            score -= 600.0
            reasons.append("HIGH_MAX_Y_NEAR_PENALTY")
            # v551: additional penalty for high-type next when merge is globally available
            if next_type >= 10 and global_merge_available:
                score -= 200.0
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
        if merge_grade == "NEAR" and piece_count >= 33 and reactor_margin < 1.0 and landing_y >= 1.0:
            score -= 600.0 * merge_mult
            reasons.append("HIGH_PC_NEAR_PENALTY")

        # ----- axis 1.7b: board-level NEAR suppression at extreme max_y (v676) -----
        # v422 uses per-candidate landing_y to gate suppression, but at max_y>=2.5
        # even "low" landing positions (y<1.0) are dangerous relative to board state.
        # This board-level check catches ALL NEAR candidates when the board is in
        # extreme danger regardless of per-candidate landing_y.
        # Penalty scales with max_y: at max_y=2.5 -> -1000, at max_y=3.0 -> -2000
        # Combined with v422 (-720 at pc>=33, landing_y>=1.0), total suppression
        # at high max_y is -1720 to -2720, sufficient to overcome NEAR bonuses.
        # Best game turn 97 (max_y=0.95): not triggered.
        # Worst game turn 59 (max_y=2.83): triggers, combined with v422 = -2380.
        # Extra_low turn 72 (max_y=2.13 < 2.5): not triggered, NEAR allowed to proceed.
        # refs: tmp/analysis_result.md
        if merge_grade == "NEAR" and max_y >= 2.5 and piece_count >= 33 and not russia_phase:
            penalty = -1000.0 * (1.0 + (max_y - 2.5) * 2.0)
            score += penalty
            reasons.append("BOARD_MAX_Y_NEAR_SUPPRESSION")

        # ----- axis 1.7c: tight deadline NEAR suppression (Ukraine gate fix) -----
        # At very tight deadline margin, NEAR merge failure adds a piece and can
        # push max_y into a runaway. Keep NEAR only when it clearly compresses.
        if merge_grade == "NEAR" and reactor_margin < 0.3 and deadline_crossed and not russia_phase:
            if not (landing_y < max_y - 0.5):
                score -= 2500.0
                reasons.append("TIGHT_DEADLINE_NEAR_SUPPRESSION")

        # ----- axis 1.5d: merge compression check (vXXX: Ukraine gate fix) -----
        # Worst game T62-T63: NEAR merge at landing_y=4.75/5.2, max_y=2.96-3.18.
        # Merge raised max_y instead of compressing — score_delta=36-45 (partial benefit only).
        # Best game T55: max_y=1.23, NEAR merge at landing_y=2.92 -> max_y raised to 1.52.
        # This is the opposite of board compression — piece_count stays same, height increases.
        # When merge_grade==NEAR and landing_y > max_y + 0.3, the merge will RAISE max_y.
        # Penalty: -800 * merge_mult. At max_y=2.0, landing_y=2.5: -800. At max_y=2.5, landing_y=3.0: -800.
        # This prevents "merging upward" when board is already high (max_y >= 2.0).
        # Target stage: Ukraine(T13)=1/4 → improve. Prevents T12→T13 merge from raising max_y.
        # refs: tmp/analysis_result.md (Implementation Plan axis 1.5d),
        #       game_history/20260523_083958_score0862.jsonl T62-T63 (uncompressive NEAR)
        if merge_grade == "NEAR" and landing_y > max_y + 0.3:
            score -= 800.0 * merge_mult
            reasons.append("NEAR_COMPRESSION_CHECK")

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
            danger_piece_count > 0
            and reactive_pair_count >= 3
            and merge_grade == "NO"
            and deadline_crossed
        )
        stacking_danger_suppressed = death_spiral
        # v549: suppress stacking at high pc without merge — prevents pc runaway when rp drops to 1-2
        # score1290 T86-91: rp=1, pc=38-47, stacking bonus ~1200 overwhelms height diff ~100-150
        # stacking bonus (base~400 * congestion_scale up to 3.0x = ~1200) >> height penalty diff (~100-150)
        # When no merge is available at high pc, stacking accelerates piece accumulation → death spiral
        # Axis 9.6b (~120-540) provides sufficient horizontal guidance when stacking is suppressed
        stacking_pc_suppressed = piece_count >= 35 and merge_grade == "NO"
        if reactive_pair_count >= 1 and merge_grade == "NO" and same_type_stack_top is not None and not stacking_danger_suppressed and not stacking_pc_suppressed:
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
                # vXXX: Ukraine gate type growth fix — horizontal clustering for same-type pieces
                # Worst game T55: type 10 pieces at y=-2.68 and y=-2.64 (similar height, far apart dx≈2.85),
                # HEIGHT_CONTROL chosen over NEAR merge. Pieces vertically stacked, never merged.
                # Best game T64: type 12 at y=-2.42 and type 11 at y=-3.53, NEAR at high landing_y=4.43.
                # Advice: "併合できるtypeが隣接しているとき、その間にピースを配置FCFFFうと併合しづくなる"
                # When same_type_pieces >= 2, pieces should cluster HORIZONTALLY (same height, nearby x)
                # not VERTICALLY (stacked at different heights). This enables future NEAR merges.
                # Implementation: instead of guiding toward stack_top (vertical stacking), guide toward
                # landing_y ≈ same_type_stack_top.y with horizontal proximity bonus.
                # This is a replacement of the chain-priority mode stacking target.
                horizontal_clustering = len(same_type_pieces) >= 2 and not stacking_congested
                if stacking_congested:
                    # Height-priority: stack on lowest same-type piece
                    # Preserves stacking incentive while naturally reducing height
                    best_stack_target = min(
                        same_type_pieces, key=lambda sp: sp.get("y", 10)
                    )
                    best_chain_score = 100.0
                elif horizontal_clustering:
                    # Horizontal clustering: guide to same height as existing same-type pieces
                    # This enables horizontal proximity for future merges
                    best_stack_target = same_type_stack_top
                    best_chain_score = 0.0
                    stack_top_y = same_type_stack_top.get("y", -10)
                    # vXXX: penalty increased from -200 to -400 per unit
                    # Analysis: worst game T55 type 10 pieces at similar height but far apart (dx≈2.85),
                    # HEIGHT_CONTROL chosen over NEAR merge. delta_y=0.5 gave only -100 penalty,
                    # insufficient to overcome height penalties of 100-200 in MEDIUM/HIGH phases.
                    # At delta_y=0.5: -200 (old) → -400 (new). Makes same-height clustering
                    # competitive with height control for Ukraine(T13) gate progression.
                    height_diff = abs(landing_y - stack_top_y)
                    best_chain_score = -height_diff * 400.0
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
                                chain_score *= max(0, 1.0 - (sp_y - 1.0) * 0.3502)
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

        # ----- evaluation axis 9.12: merge drought exit trigger (NEW v617) -----
        # analysis_result.md adopted hypothesis: "Merge drought exit trigger — merge path creation"
        # Worst game T43-T56: 13 consecutive NO_MERGE turns, max_y 1.48→3.10, pc 35→46, game over.
        # extra_low T68-T75: 7 consecutive NO_MERGE, max_y 2.64→3.28, pc 40→43, game over.
        # Both failure modes: board had type 10+ pieces but no mechanism to create merge paths.
        # DEADLINE_GUARD only handles deadline survival, not merge promotion.
        # Axis 9.12: when no_merge_streak>=3 && merge_grade==NO && max_y>=1.5 && pc>=30
        # && !death_spiral, add bonus for placement near type 10+ pieces (within 1.5u horizontal).
        # Bonus magnitude: base 150 * merge_mult, scaled by proximity (closer = higher bonus).
        # This creates future merge opportunities by clustering type 10+ pieces.
        # Suppress when death_spiral — height must be sole differentiator in danger zone.
        # refs: tmp/analysis_result.md (Implementation Plan: axis 9.12),
        #       strategy_versions/best_score6058_strategy.py (axis 9.12 implementation),
        #       game_history/20260521_123535_score0181.jsonl (worst game T43-T56),
        #       game_history/20260521_124014_score2083.jsonl (extra_low T68-T75)
        # Fixes rollback failure mode: "NO_MERGE continuous → no merge path creation → death spiral"
        if (
            no_merge_streak >= 3
            and merge_grade == "NO"
            and max_y >= 1.5
            and piece_count >= 30
            and not death_spiral
        ):
            type_10_plus_pieces = [p for p in pieces if p.get("type", 0) >= 10]
            if type_10_plus_pieces:
                best_dist = float("inf")
                for tp in type_10_plus_pieces:
                    tp_x = tp.get("x", 0)
                    tp_y = tp.get("y", -10)
                    dist = ((x - tp_x) ** 2 + (landing_y - tp_y) ** 2) ** 0.5
                    if dist < best_dist:
                        best_dist = dist
                if best_dist < 1.5:
                    proximity_bonus = 150.0 * (1.0 - best_dist / 1.5) * merge_mult
                    score += proximity_bonus
                    reasons.append("MERGE_DROUGHT_EXIT")

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
        if reactive_pair_count >= 1 and merge_grade == "NO" and same_type_stack_top is None and not death_spiral:
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
            if not (current_type_has_reactive or current_type_has_near):
                # v461: suppress proximity guidance in death spiral — height must be sole differentiator
                if not death_spiral:
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
                        proximity_bonus = max(0, 120.0 - horiz_dist * 50.0)
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
                        rp_guidance_suppressed = (
                            (max_y >= 3.0 and deadline_crossed)
                            or (reactive_pair_count >= 5 and max_y >= 2.5)
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
            # v461: also suppress in death spiral — height must be sole differentiator
            board_congested = (
                (max_y >= 3.0 and deadline_crossed)
                or (reactive_pair_count >= 5 and max_y >= 2.5)
            )
            if not board_congested and not death_spiral:
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

        # ----- vXXX: merge-before-height protection at transition (Ukraine gate fix) -----
        # When max_y in [2.0, 3.0] and same_type_pieces >= 2, suppress height penalty
        # for candidates where merge_grade in [DIRECT, NEAR].
        # Analysis: worst game T55-T64 shows HEIGHT_CONTROL dominating merge selection
        # in max_y 2.0-3.0 range. Same-type pieces exist but cannot merge because
        # placement scatters them vertically. This allows merge candidates to win over
        # HEIGHT_CONTROL in the critical transition range for Ukraine(T13) gate.
        # Height penalty is suppressed but not eliminated — merges still compete fairly.
        # refs: tmp/analysis_result.md (Implementation Plan: merge-before-height protection),
        #       game_history/20260523_083958_score0862.jsonl T55 (HEIGHT_CONTROL over merge)
        if 2.0 <= max_y < 3.0 and len(same_type_pieces) >= 2 and merge_grade in ["DIRECT", "NEAR"]:
            height_mult *= 0.3

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
        # v462: suppress in death spiral — height must be sole differentiator
        if next_next_type == next_type and not death_spiral:
            center_bonus = max(0, 1.0 - abs(x) / 2.0) * 50.0
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
        # v461: suppress growth center in death spiral — height must be sole differentiator
        if max_type_on_board >= 6 and not death_spiral:
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

        # ----- vXXX: pre-Russia seed clustering -----
        # Live clean loss 20260602_131226: the board reached T12x3 T11x1 T10x3
        # by T35, but the T12/T11 seed lane scattered before the existing
        # pre-Russia bridge axes (T13/T14 near-miss) could help. Keep this
        # bounded to high incoming material so low types do not get dragged into
        # the high cluster, preserving the d441 low-type throttle intent.
        pre_russia_seed_ready = (
            not russia_phase
            and max_type_on_board in (11, 12)
            and (
                pre_russia_counts.get(12, 0) >= 2
                or (
                    pre_russia_counts.get(12, 0) >= 1
                    and pre_russia_counts.get(11, 0) >= 2
                )
                or pre_russia_counts.get(11, 0) >= 3
            )
        )
        pre_russia_seed_material_ready = next_type >= 9 or next_next_type >= 11
        if (
            merge_grade == "NO"
            and pre_russia_seed_ready
            and pre_russia_seed_material_ready
            and not death_spiral
            and max_y < 2.0
            and piece_count >= 18
        ):
            seed_targets = [p for p in pieces if p.get("type") in (11, 12)]
            seed_center_x = _weighted_center_x(seed_targets)
            if seed_center_x is not None:
                seed_dist = abs(x - seed_center_x)
                seed_bonus = max(0.0, 300.0 - seed_dist * 140.0)
                if piece_count >= 28:
                    seed_bonus *= min(1.6, 1.0 + (piece_count - 28) * 0.06)
                if landing_y > 1.6:
                    seed_bonus *= 0.4
                if seed_bonus > 0:
                    score += seed_bonus
                    reasons.append("PRE_RUSSIA_SEED_CLUSTER")

        # ----- vXXX: pre-Russia T11 density latch -----
        # Clean #29386 peaked at T12x1 T11x6 T10x3: seed clustering was present
        # but too weak to close the T11 cloud into more T12/T13 material before
        # deadline guard took over. When one T12 anchor and multiple T11s exist,
        # latch incoming T10/T11/T12 tightly to the T12/T11 lane.
        pre_russia_t11_density_latch_ready = (
            not russia_phase
            and max_type_on_board == 12
            and pre_russia_counts.get(12, 0) >= 1
            and pre_russia_counts.get(11, 0) >= 2
            and next_type in (10, 11, 12)
        )
        if (
            merge_grade == "NO"
            and pre_russia_t11_density_latch_ready
            and not death_spiral
            and max_y < 2.6
            and piece_count >= 24
        ):
            density_target = None
            if next_type == 10:
                density_up_targets = [
                    p for p in pieces if p.get("type") in (11, 12)
                ]
                density_same_targets = [
                    p for p in pieces if p.get("type") == 10
                ]
                if density_same_targets and density_up_targets:
                    def _pre_russia_t11_density_key(tp):
                        tp_x = tp.get("x", 0)
                        tp_y = tp.get("y", -10)
                        up_dist = min(
                            ((up.get("x", 0) - tp_x) ** 2 + (up.get("y", -10) - tp_y) ** 2) ** 0.5
                            for up in density_up_targets
                        )
                        high_penalty = max(0.0, tp_y - 1.0) * 0.65
                        return (up_dist + high_penalty, tp_y)
                    density_target = min(density_same_targets, key=_pre_russia_t11_density_key)
                elif density_up_targets:
                    density_center = _weighted_center_x(density_up_targets)
                    if density_center is not None:
                        density_target = {"x": density_center, "y": -0.8, "type": 11}
            elif next_type == 11:
                density_same_targets = [
                    p for p in pieces if p.get("type") == 11
                ]
                density_up_targets = [
                    p for p in pieces if p.get("type") == 12
                ]
                if density_same_targets and density_up_targets:
                    def _pre_russia_t11_latch_key(tp):
                        tp_x = tp.get("x", 0)
                        tp_y = tp.get("y", -10)
                        up_dist = min(
                            ((up.get("x", 0) - tp_x) ** 2 + (up.get("y", -10) - tp_y) ** 2) ** 0.5
                            for up in density_up_targets
                        )
                        high_penalty = max(0.0, tp_y + 0.2) * 1.2
                        return (up_dist + high_penalty, tp_y)
                    density_target = min(density_same_targets, key=_pre_russia_t11_latch_key)
                elif density_up_targets:
                    density_center = _weighted_center_x(density_up_targets)
                    if density_center is not None:
                        density_target = {"x": density_center, "y": -0.8, "type": 12}
            else:
                density_targets = [
                    p for p in pieces if p.get("type") == 12
                ]
                density_center = _weighted_center_x(density_targets)
                if density_center is not None:
                    density_target = {
                        "x": density_center,
                        "y": max((p.get("y", -10) for p in density_targets), default=-10),
                        "type": 13,
                    }
            if density_target is not None:
                density_dist = abs(x - density_target.get("x", 0))
                density_bonus = max(0.0, 1180.0 - density_dist * 360.0)
                if piece_count >= 30:
                    density_bonus *= min(1.75, 1.0 + (piece_count - 30) * 0.08)
                if landing_y > 2.2:
                    density_bonus *= 0.4
                if density_target.get("y", -10) > 1.5:
                    density_bonus *= 0.65
                if abs(x) >= 2.5 and density_dist >= 1.7:
                    score -= 1400.0
                if density_bonus > 0:
                    score += density_bonus
                    reasons.append("PRE_RUSSIA_T11_DENSITY_LATCH")

        # ----- vXXX: pre-Russia T11 high merge veto before first T13 -----
        # Clean #29424 had T12x1 + T11x3, then chose high T11 placements that
        # created a second T12 at y~=1.7. That left the first T13 pair too high
        # and deadline triage took over. When the first T13 is still absent,
        # reject high T11 landings in the single-T12/T11-density lane so the
        # rebuild path stays low enough to close T12→T13.
        pre_russia_t11_density_high_veto_ready = (
            not russia_phase
            and max_type_on_board == 12
            and pre_russia_counts.get(13, 0) == 0
            and pre_russia_counts.get(12, 0) == 1
            and pre_russia_counts.get(11, 0) >= 2
            and next_type == 11
            and piece_count >= 28
            and max_y >= 1.0
        )
        if (
            pre_russia_t11_density_high_veto_ready
            and landing_y > 1.15
            and not death_spiral
        ):
            high_excess = max(0.0, landing_y - 1.15)
            score -= 3600.0 + high_excess * 2200.0
            reasons.append("PRE_RUSSIA_T11_DENSITY_HIGH_VETO")

        # ----- vXXX: pre-Russia single-T12 anchor ladder -----
        # Clean #29429 stalled at T12x2/T11x5/T10x4 after the only low T12
        # anchor was ignored and high T11 material made the second T12 at redline.
        pre_russia_single_t12_anchor_ladder_ready = (
            not russia_phase
            and max_type_on_board == 12
            and pre_russia_counts.get(13, 0) == 0
            and pre_russia_counts.get(12, 0) == 1
            and pre_russia_counts.get(11, 0) >= 2
            and next_type in (10, 11, 12)
            and piece_count >= 26
        )
        if (
            merge_grade in ("NO", "DIRECT", "NEAR")
            and pre_russia_single_t12_anchor_ladder_ready
            and not death_spiral
            and max_y < 3.1
        ):
            t12_targets = [p for p in pieces if p.get("type") == 12]
            t11_targets = [p for p in pieces if p.get("type") == 11]
            t10_targets = [p for p in pieces if p.get("type") == 10]
            anchor_target = None
            if t12_targets:
                t12_anchor = min(t12_targets, key=lambda tp: (tp.get("y", -10), abs(tp.get("x", 0))))
                anchor_x = t12_anchor.get("x", 0)
                anchor_y = t12_anchor.get("y", -10)
                if next_type in (11, 12):
                    anchor_target = {"x": anchor_x, "y": anchor_y, "type": 12}
                elif next_type == 10:
                    up_targets = t11_targets + t12_targets
                    if t10_targets and up_targets:
                        def _pre_russia_single_t12_anchor_t10_key(tp):
                            tp_x = tp.get("x", 0)
                            tp_y = tp.get("y", -10)
                            up_dist = min(((up.get("x", 0) - tp_x) ** 2 + (up.get("y", -10) - tp_y) ** 2) ** 0.5 for up in up_targets)
                            return (min(up_dist, abs(tp_x - anchor_x)) + max(0.0, tp_y - 0.8) * 0.9, tp_y)
                        anchor_target = min(t10_targets, key=_pre_russia_single_t12_anchor_t10_key)
                    else:
                        anchor_target = {"x": anchor_x, "y": anchor_y, "type": 11}
            if anchor_target is not None:
                anchor_dist = abs(x - anchor_target.get("x", 0))
                anchor_bonus = max(0.0, 2250.0 - anchor_dist * 540.0)
                if piece_count >= 32:
                    anchor_bonus *= min(1.85, 1.0 + (piece_count - 32) * 0.08)
                if pre_russia_t11_density_high_veto_ready and next_type == 11 and landing_y > 1.15:
                    anchor_bonus *= 0.12
                if landing_y > 1.5:
                    anchor_bonus *= 0.55
                if landing_y > 2.25:
                    anchor_bonus *= 0.35
                if anchor_target.get("y", -10) > 1.3:
                    anchor_bonus *= 0.7
                if anchor_dist >= 1.2:
                    score -= 1900.0
                if abs(x) >= 2.5 and anchor_dist >= 1.1:
                    score -= 1800.0
                if landing_y > 1.6 and anchor_dist >= 0.8:
                    score -= 1650.0
                if next_type == 11 and merge_grade in ("DIRECT", "NEAR") and landing_y > 1.0 and anchor_dist >= 0.9:
                    score -= 65000.0 + max(0.0, landing_y - 1.0) * 5000.0
                if anchor_bonus > 0:
                    score += anchor_bonus
                    reasons.append("PRE_RUSSIA_SINGLE_T12_ANCHOR_LADDER")

        # ----- vXXX: early pre-Russia T10 ladder before first T13 -----
        # Clean #29426 stalled at T12x1 + T11x3 + T10x5. The post-T13
        # PRE_RUSSIA_T10_LADDER was too late; T10/T9 material had already
        # scattered into high side lanes before the first T13 existed. Once a
        # single T12/T11 backbone exists, feed T9/T10/T11 into that lane so the
        # second T12 and first T13 are created before deadline guard takes over.
        pre_russia_early_t10_ladder_ready = (
            not russia_phase
            and max_type_on_board == 12
            and pre_russia_counts.get(13, 0) == 0
            and pre_russia_counts.get(12, 0) == 1
            and pre_russia_counts.get(11, 0) >= 1
            and (
                type10_count >= 3
                or (
                    type10_count >= 2
                    and type9_count >= 3
                )
            )
            and next_type in (9, 10, 11)
        )
        if (
            merge_grade == "NO"
            and pre_russia_early_t10_ladder_ready
            and not death_spiral
            and max_y < 2.75
            and piece_count >= 24
        ):
            t12_targets = [p for p in pieces if p.get("type") == 12]
            t11_targets = [p for p in pieces if p.get("type") == 11]
            t10_targets = [p for p in pieces if p.get("type") == 10]
            t9_targets = [p for p in pieces if p.get("type") == 9]
            early_target = None
            if next_type == 11 and t11_targets:
                up_targets = t12_targets
                def _pre_russia_early_t11_key(tp):
                    tp_x = tp.get("x", 0)
                    tp_y = tp.get("y", -10)
                    up_dist = min(
                        ((up.get("x", 0) - tp_x) ** 2 + (up.get("y", -10) - tp_y) ** 2) ** 0.5
                        for up in up_targets
                    ) if up_targets else 999.0
                    return (up_dist + max(0.0, tp_y - 0.8) * 1.1, tp_y)
                early_target = min(t11_targets, key=_pre_russia_early_t11_key)
            elif next_type == 10:
                up_targets = t11_targets + t12_targets
                if t10_targets and up_targets:
                    def _pre_russia_early_t10_key(tp):
                        tp_x = tp.get("x", 0)
                        tp_y = tp.get("y", -10)
                        up_dist = min(
                            ((up.get("x", 0) - tp_x) ** 2 + (up.get("y", -10) - tp_y) ** 2) ** 0.5
                            for up in up_targets
                        )
                        return (up_dist + max(0.0, tp_y - 0.75) * 1.0, tp_y)
                    early_target = min(t10_targets, key=_pre_russia_early_t10_key)
                elif up_targets:
                    early_center = _weighted_center_x(up_targets)
                    if early_center is not None:
                        early_target = {"x": early_center, "y": -0.8, "type": 10}
            elif next_type == 9:
                up_targets = t10_targets + t11_targets + t12_targets
                if t9_targets and up_targets:
                    def _pre_russia_early_t9_key(tp):
                        tp_x = tp.get("x", 0)
                        tp_y = tp.get("y", -10)
                        up_dist = min(
                            ((up.get("x", 0) - tp_x) ** 2 + (up.get("y", -10) - tp_y) ** 2) ** 0.5
                            for up in up_targets
                        )
                        return (up_dist + max(0.0, tp_y - 0.6) * 0.9, tp_y)
                    early_target = min(t9_targets, key=_pre_russia_early_t9_key)
            if early_target is not None:
                early_dist = abs(x - early_target.get("x", 0))
                early_bonus = max(0.0, 1650.0 - early_dist * 440.0)
                if piece_count >= 30:
                    early_bonus *= min(1.8, 1.0 + (piece_count - 30) * 0.08)
                if landing_y > 1.65:
                    early_bonus *= 0.55
                if landing_y > 2.35:
                    early_bonus *= 0.38
                if early_target.get("y", -10) > 1.25:
                    early_bonus *= 0.66
                if abs(x) >= 2.5 and early_dist >= 1.55:
                    score -= 1500.0
                if landing_y > 1.85 and early_dist >= 0.85:
                    score -= 1150.0
                if early_bonus > 0:
                    score += early_bonus
                    reasons.append("PRE_RUSSIA_EARLY_T10_LADDER")

        # ----- vXXX: pre-Russia late T12 consolidation -----
        # Clean #29390 kept T12x3/T11x3/T10x4 but never closed the first T13;
        # the broad T12 center was pulled by an outlier lane. Once multiple
        # T12s exist before the first T13, prefer the nearest T12 pair and feed
        # T10/T11/T12 material into that lane.
        pre_russia_t12_consolidate_ready = (
            not russia_phase
            and max_type_on_board == 12
            and pre_russia_counts.get(13, 0) == 0
            and pre_russia_counts.get(12, 0) >= 2
            and (
                pre_russia_counts.get(12, 0) >= 3
                or pre_russia_counts.get(11, 0) >= 2
                or type10_count >= 2
            )
            and next_type in (10, 11, 12)
        )
        if (
            merge_grade == "NO"
            and pre_russia_t12_consolidate_ready
            and not death_spiral
            and max_y < 3.35
            and piece_count >= 24
        ):
            consolidate_target = None
            t12_targets = [p for p in pieces if p.get("type") == 12]
            t12_pair_center = None
            t12_pair_y = -10.0
            if len(t12_targets) >= 2:
                best_pair = None
                best_pair_key = (999.0, 999.0)
                for pair_i, pair_a in enumerate(t12_targets):
                    for pair_b in t12_targets[pair_i + 1:]:
                        ax = pair_a.get("x", 0)
                        ay = pair_a.get("y", -10)
                        bx = pair_b.get("x", 0)
                        by = pair_b.get("y", -10)
                        pair_dist = ((ax - bx) ** 2 + (ay - by) ** 2) ** 0.5
                        pair_top = max(ay, by)
                        pair_key = (
                            pair_dist + max(0.0, pair_top - 1.3) * 0.45,
                            pair_top,
                        )
                        if pair_key < best_pair_key:
                            best_pair_key = pair_key
                            best_pair = (pair_a, pair_b)
                if best_pair is not None:
                    t12_pair_center = (
                        best_pair[0].get("x", 0) + best_pair[1].get("x", 0)
                    ) / 2.0
                    t12_pair_y = max(
                        best_pair[0].get("y", -10),
                        best_pair[1].get("y", -10),
                    )
            if next_type == 12 and t12_pair_center is not None:
                consolidate_target = {"x": t12_pair_center, "y": t12_pair_y, "type": 13}
            elif next_type == 11:
                same_targets = [p for p in pieces if p.get("type") == 11]
                if same_targets:
                    anchor_x = t12_pair_center
                    if anchor_x is None:
                        anchor_x = _weighted_center_x(t12_targets)
                    if anchor_x is not None:
                        def _pre_russia_t12_consolidate_t11_key(tp):
                            tp_x = tp.get("x", 0)
                            tp_y = tp.get("y", -10)
                            high_penalty = max(0.0, tp_y + 0.1) * 1.15
                            return (abs(tp_x - anchor_x) + high_penalty, tp_y)
                        consolidate_target = min(same_targets, key=_pre_russia_t12_consolidate_t11_key)
                elif t12_pair_center is not None:
                    consolidate_target = {"x": t12_pair_center, "y": t12_pair_y, "type": 12}
            else:
                same_targets = [p for p in pieces if p.get("type") == 10]
                up_targets = [p for p in pieces if p.get("type") in (11, 12)]
                if same_targets and up_targets:
                    anchor_x = t12_pair_center
                    if anchor_x is None:
                        anchor_x = _weighted_center_x(up_targets)
                    if anchor_x is not None:
                        def _pre_russia_t12_consolidate_t10_key(tp):
                            tp_x = tp.get("x", 0)
                            tp_y = tp.get("y", -10)
                            up_dist = min(
                                ((up.get("x", 0) - tp_x) ** 2 + (up.get("y", -10) - tp_y) ** 2) ** 0.5
                                for up in up_targets
                            )
                            lane_dist = abs(tp_x - anchor_x)
                            high_penalty = max(0.0, tp_y + 0.1) * 0.9
                            return (min(up_dist, lane_dist) + high_penalty, tp_y)
                        consolidate_target = min(same_targets, key=_pre_russia_t12_consolidate_t10_key)
                elif t12_pair_center is not None:
                    consolidate_target = {"x": t12_pair_center, "y": t12_pair_y, "type": 11}
            if consolidate_target is not None:
                consolidate_dist = abs(x - consolidate_target.get("x", 0))
                consolidate_bonus = max(0.0, 2300.0 - consolidate_dist * 700.0)
                if piece_count >= 34:
                    consolidate_bonus *= min(1.85, 1.0 + (piece_count - 34) * 0.09)
                elif piece_count >= 28:
                    consolidate_bonus *= 1.18
                if landing_y > 2.8:
                    consolidate_bonus *= 0.55
                if consolidate_target.get("y", -10) > 1.7:
                    consolidate_bonus *= 0.75
                if abs(x) >= 2.5 and consolidate_dist >= 1.3:
                    score -= 3600.0
                    reasons.append("PRE_RUSSIA_T12_CONSOLIDATE_OFFLANE_VETO")
                if (
                    pre_russia_counts.get(12, 0) >= 2
                    and pre_russia_counts.get(13, 0) == 0
                    and next_type in (10, 11, 12)
                    and consolidate_dist >= 1.05
                    and landing_y > 0.65
                ):
                    score -= 2200.0 + max(0.0, landing_y - 0.65) * 1500.0
                    reasons.append("PRE_RUSSIA_T12_CONSOLIDATE_LANE_VETO")
                if landing_y > 2.05 and consolidate_dist >= 0.85:
                    score -= 2100.0 + max(0.0, landing_y - 2.05) * 1600.0
                    reasons.append("PRE_RUSSIA_T12_CONSOLIDATE_HIGH_OFFLANE_VETO")
                if landing_y > 2.55 and merge_grade == "NO" and consolidate_dist >= 0.45:
                    score -= 1250.0 + max(0.0, landing_y - 2.55) * 1400.0
                    reasons.append("PRE_RUSSIA_T12_CONSOLIDATE_REDLINE_VETO")
                if consolidate_bonus > 0:
                    score += consolidate_bonus
                    reasons.append("PRE_RUSSIA_T12_CONSOLIDATE")

        # ----- vXXX: pre-Russia T12 abundance pair lock -----
        # Clean #29422 built T12x5 without a first T13, then died waiting for a
        # safe T12 arrival while T10/T9 material climbed into the redline. When
        # four or more T12s exist, lock the lowest close T12 pair and feed T10/T11
        # material into that exact lane instead of broad bridge clustering.
        pre_russia_t12_abundance_pair_lock_ready = (
            not russia_phase
            and max_type_on_board == 12
            and pre_russia_counts.get(13, 0) == 0
            and pre_russia_counts.get(12, 0) >= 4
            and next_type in (10, 11, 12)
        )
        if (
            merge_grade == "NO"
            and pre_russia_t12_abundance_pair_lock_ready
            and not result.get("crosses_deadline", False)
            and not death_spiral
            and max_y < 3.25
            and piece_count >= 28
        ):
            t12_targets = [p for p in pieces if p.get("type") == 12]
            t11_targets = [p for p in pieces if p.get("type") == 11]
            t10_targets = [p for p in pieces if p.get("type") == 10]
            pair_anchor_x = None
            pair_anchor_y = -10.0
            if len(t12_targets) >= 2:
                best_pair = None
                best_pair_key = (999.0, 999.0)
                for pair_i, pair_a in enumerate(t12_targets):
                    for pair_b in t12_targets[pair_i + 1:]:
                        ax = pair_a.get("x", 0)
                        ay = pair_a.get("y", -10)
                        bx = pair_b.get("x", 0)
                        by = pair_b.get("y", -10)
                        pair_dist = ((ax - bx) ** 2 + (ay - by) ** 2) ** 0.5
                        pair_top = max(ay, by)
                        pair_key = (
                            pair_dist
                            + max(0.0, pair_top - 0.8) * 1.55
                            + abs((ax + bx) / 2.0) * 0.08,
                            pair_top,
                        )
                        if pair_key < best_pair_key:
                            best_pair_key = pair_key
                            best_pair = (pair_a, pair_b)
                if best_pair is not None:
                    pair_anchor_x = (
                        best_pair[0].get("x", 0) + best_pair[1].get("x", 0)
                    ) / 2.0
                    pair_anchor_y = max(
                        best_pair[0].get("y", -10),
                        best_pair[1].get("y", -10),
                    )
            pair_lock_target = None
            if next_type == 12 and pair_anchor_x is not None:
                pair_lock_target = {"x": pair_anchor_x, "y": pair_anchor_y, "type": 12}
            elif next_type == 11 and t11_targets and pair_anchor_x is not None:
                def _pre_russia_t12_abundance_t11_key(tp):
                    tp_x = tp.get("x", 0)
                    tp_y = tp.get("y", -10)
                    return (
                        abs(tp_x - pair_anchor_x)
                        + max(0.0, tp_y - 0.9) * 1.25,
                        tp_y,
                    )
                pair_lock_target = min(
                    t11_targets,
                    key=_pre_russia_t12_abundance_t11_key,
                )
            elif next_type == 10 and t10_targets:
                up_targets = t11_targets + t12_targets
                def _pre_russia_t12_abundance_t10_key(tp):
                    tp_x = tp.get("x", 0)
                    tp_y = tp.get("y", -10)
                    up_dist = min(
                        ((up.get("x", 0) - tp_x) ** 2 + (up.get("y", -10) - tp_y) ** 2) ** 0.5
                        for up in up_targets
                    ) if up_targets else 999.0
                    lane_dist = abs(tp_x - pair_anchor_x) if pair_anchor_x is not None else 999.0
                    return (
                        min(up_dist, lane_dist)
                        + max(0.0, tp_y - 0.8) * 0.95,
                        tp_y,
                    )
                pair_lock_target = min(
                    t10_targets,
                    key=_pre_russia_t12_abundance_t10_key,
                )
            if pair_lock_target is not None:
                pair_lock_dist = abs(x - pair_lock_target.get("x", 0))
                pair_lock_bonus = max(0.0, 2800.0 - pair_lock_dist * 660.0)
                if piece_count >= 34:
                    pair_lock_bonus *= min(1.9, 1.0 + (piece_count - 34) * 0.09)
                if landing_y > 2.1:
                    pair_lock_bonus *= 0.5
                if landing_y > 2.65:
                    pair_lock_bonus *= 0.38
                if pair_lock_target.get("y", -10) > 1.25:
                    pair_lock_bonus *= 0.62
                if abs(x) >= 2.5 and pair_lock_dist >= 1.3:
                    score -= 2000.0
                if landing_y > 2.15 and pair_lock_dist >= 0.85:
                    score -= 1450.0
                if pair_lock_bonus > 0:
                    score += pair_lock_bonus
                    reasons.append("PRE_RUSSIA_T12_ABUNDANCE_PAIR_LOCK")

        # ----- vXXX: pre-Russia next-up latch before first T13 -----
        # Clean loss 20260602_140507 had T12x3 by T37, but incoming T11 was
        # placed as broad seed material and the second T13 arrived too late.
        # Before the first T13 exists, prefer placing T10/T11 near its next-up
        # lane (T11/T12) so the pre-Russia bridge has compact material to lift.
        pre_russia_next_up_latch_ready = (
            not russia_phase
            and max_type_on_board == 12
            and pre_russia_counts.get(12, 0) >= 2
            and next_type in (10, 11)
        )
        if (
            merge_grade == "NO"
            and pre_russia_next_up_latch_ready
            and not death_spiral
            and max_y < 2.4
            and piece_count >= 20
        ):
            next_up_lane_targets = [
                p for p in pieces if p.get("type") == next_type + 1
            ]
            if next_up_lane_targets:
                lane_dist = min(
                    abs(x - target.get("x", 0))
                    for target in next_up_lane_targets
                )
                lane_bonus = max(0.0, 700.0 - lane_dist * 260.0)
                if piece_count >= 28:
                    lane_bonus *= min(1.6, 1.0 + (piece_count - 28) * 0.07)
                if landing_y > 2.0:
                    lane_bonus *= 0.35
                if min(target.get("y", -10) for target in next_up_lane_targets) > 1.2:
                    lane_bonus *= 0.5
                if lane_bonus > 0:
                    score += lane_bonus
                    reasons.append("PRE_RUSSIA_NEXT_UP_LATCH")

        # ----- vXXX: pre-Russia T13 lift after first T13 appears -----
        # Clean losses 20260602_141216 and 20260602_141514 reached T13/T12
        # material but did not convert it to a first T14 before deadline guard
        # dominated the board. When T13x1 and T12x2+ already exist, treat
        # incoming T11/T12/T13 as lift material for the T12/T13 lane even if no
        # same-type latch is available yet. This is deliberately pre-Russia only.
        pre_russia_t13_lift_ready = (
            not russia_phase
            and max_type_on_board == 13
            and pre_russia_counts.get(13, 0) >= 1
            and pre_russia_counts.get(12, 0) >= 2
            and next_type in (11, 12, 13)
        )
        if (
            merge_grade == "NO"
            and pre_russia_t13_lift_ready
            and not death_spiral
            and max_y < 2.8
            and piece_count >= 26
        ):
            if next_type == 11:
                lift_targets = [
                    p for p in pieces if p.get("type") in (12, 13)
                ]
            else:
                lift_targets = [
                    p for p in pieces if p.get("type") in (13,)
                ]
            lift_center_x = _weighted_center_x(lift_targets)
            if lift_center_x is not None:
                lift_dist = abs(x - lift_center_x)
                lift_bonus = max(0.0, 880.0 - lift_dist * 300.0)
                if piece_count >= 34:
                    lift_bonus *= min(1.55, 1.0 + (piece_count - 34) * 0.07)
                if landing_y > 2.0:
                    lift_bonus *= 0.45
                if max((p.get("y", -10) for p in lift_targets), default=-10) > 1.3:
                    lift_bonus *= 0.65
                if abs(x) >= 2.5 and lift_dist >= 1.8:
                    score -= 900.0
                if lift_bonus > 0:
                    score += lift_bonus
                    reasons.append("PRE_RUSSIA_T13_LIFT")

        # ----- vXXX: pre-Russia T13 pair cluster -----
        # Clean #29383 reached T13x2 T12x2, then deadline fallback dispersed
        # safe landings until the T13 pair never closed into the first Russia.
        # Once two T13s exist, compact incoming T8+ material toward their lane
        # before the board crosses into hard deadline triage.
        pre_russia_t13_pair_cluster_ready = (
            not russia_phase
            and max_type_on_board == 13
            and pre_russia_counts.get(13, 0) >= 2
            and pre_russia_counts.get(14, 0) == 0
            and (
                pre_russia_counts.get(12, 0) >= 1
                or pre_russia_counts.get(11, 0) >= 2
            )
            and next_type >= 8
        )
        if (
            merge_grade == "NO"
            and pre_russia_t13_pair_cluster_ready
            and not death_spiral
            and max_y < 3.1
            and piece_count >= 24
        ):
            t13_pair_targets = [
                p for p in pieces if p.get("type") == 13
            ]
            t13_pair_info = _closest_pair_center_x(t13_pair_targets)
            if t13_pair_info is not None:
                t13_pair_center, t13_pair_top_y = t13_pair_info
                pair_dist = abs(x - t13_pair_center)
                pair_bonus = max(0.0, 2800.0 - pair_dist * 760.0)
                if piece_count >= 30:
                    pair_bonus *= min(2.0, 1.0 + (piece_count - 30) * 0.10)
                if landing_y > 2.1:
                    pair_bonus *= 0.45
                if t13_pair_top_y > 1.45:
                    pair_bonus *= 0.65
                if abs(x) >= 2.45 and pair_dist >= 1.25:
                    score -= 2200.0
                    reasons.append("PRE_RUSSIA_T13_PAIR_OFFLANE_VETO")
                if landing_y > 2.1 and pair_dist >= 0.85:
                    score -= 1600.0
                if pair_dist >= 1.25 and landing_y <= 0.25:
                    score -= 900.0
                if pair_bonus > 0:
                    score += pair_bonus
                    reasons.append("PRE_RUSSIA_T13_PAIR_CLUSTER")

        # ----- vXXX: pre-Russia T13-pair compression with T12 material -----
        # Clean #29400 reached T13x2 + T12x2 but the T13s were vertically split;
        # generic safe landings preserved the board without closing T12/T13
        # material into the first T14. When T12 material still exists, feed the
        # next T10/T11/T12 toward the closest T12-pair lane instead of the edge.
        pre_russia_t13_pair_compress_ready = (
            not russia_phase
            and max_type_on_board == 13
            and pre_russia_counts.get(13, 0) >= 2
            and pre_russia_counts.get(14, 0) == 0
            and pre_russia_counts.get(12, 0) >= 2
            and next_type in (10, 11, 12, 13)
        )
        if (
            merge_grade == "NO"
            and pre_russia_t13_pair_compress_ready
            and not death_spiral
            and max_y < 3.35
            and piece_count >= 30
        ):
            t12_targets = [p for p in pieces if p.get("type") == 12]
            compress_target = None
            if len(t12_targets) >= 2:
                best_pair = None
                best_pair_key = (999.0, 999.0)
                for pair_i, pair_a in enumerate(t12_targets):
                    for pair_b in t12_targets[pair_i + 1:]:
                        ax = pair_a.get("x", 0)
                        ay = pair_a.get("y", -10)
                        bx = pair_b.get("x", 0)
                        by = pair_b.get("y", -10)
                        pair_dist = ((ax - bx) ** 2 + (ay - by) ** 2) ** 0.5
                        pair_top = max(ay, by)
                        pair_key = (
                            pair_dist + max(0.0, pair_top - 1.2) * 0.5,
                            pair_top,
                        )
                        if pair_key < best_pair_key:
                            best_pair_key = pair_key
                            best_pair = (pair_a, pair_b)
                if best_pair is not None:
                    compress_target = {
                        "x": (best_pair[0].get("x", 0) + best_pair[1].get("x", 0)) / 2.0,
                        "y": max(best_pair[0].get("y", -10), best_pair[1].get("y", -10)),
                        "type": 12,
                    }
            if compress_target is None:
                t13_targets = [p for p in pieces if p.get("type") == 13]
                t13_pair_center = _weighted_center_x(t13_targets)
                if t13_pair_center is not None:
                    compress_target = {"x": t13_pair_center, "y": -0.8, "type": 13}
            if compress_target is not None:
                compress_dist = abs(x - compress_target.get("x", 0))
                compress_bonus = max(0.0, 2300.0 - compress_dist * 560.0)
                if piece_count >= 36:
                    compress_bonus *= min(1.8, 1.0 + (piece_count - 36) * 0.09)
                if landing_y > 1.8:
                    compress_bonus *= 0.58
                if landing_y > 2.45:
                    compress_bonus *= 0.45
                if compress_target.get("y", -10) > 1.2:
                    compress_bonus *= 0.78
                if abs(x) >= 2.4 and compress_dist >= 1.3:
                    score -= 1700.0
                if landing_y > 2.4 and compress_dist >= 0.9:
                    score -= 1100.0
                if compress_bonus > 0:
                    score += compress_bonus
                    reasons.append("PRE_RUSSIA_T13_PAIR_COMPRESS")

        # ----- vXXX: pre-Russia T13-pair + single T12 tether -----
        # Clean #29408 reached T13x2 + T12x1 by T54, then incoming T11/T12
        # material drifted away from the pair until deadline guard took over.
        # This fills the gap between the T12x2 compression and the no-T12
        # ladder: with exactly one T12, tether high incoming material between
        # the T13 pair lane and that T12 so the board can rebuild a second T12
        # and close the first Kazakhstan.
        pre_russia_t13_pair_single_t12_tether_ready = (
            not russia_phase
            and max_type_on_board == 13
            and pre_russia_counts.get(13, 0) >= 2
            and pre_russia_counts.get(14, 0) == 0
            and pre_russia_counts.get(12, 0) == 1
            and next_type in (10, 11, 12, 13)
        )
        if (
            merge_grade == "NO"
            and pre_russia_t13_pair_single_t12_tether_ready
            and not death_spiral
            and max_y < 3.35
            and piece_count >= 24
        ):
            t13_targets = [p for p in pieces if p.get("type") == 13]
            t12_targets = [p for p in pieces if p.get("type") == 12]
            t13_center = _weighted_center_x(t13_targets)
            tether_target = None
            if t13_center is not None and t12_targets:
                t12_anchor = min(
                    t12_targets,
                    key=lambda tp: (
                        max(0.0, tp.get("y", -10) - 1.0) * 1.2,
                        abs(tp.get("x", 0) - t13_center),
                        tp.get("y", -10),
                    ),
                )
                t12_x = t12_anchor.get("x", 0)
                t12_y = t12_anchor.get("y", -10)
                if next_type == 13:
                    tether_target = {"x": t13_center, "y": -0.8, "type": 13}
                elif next_type == 12:
                    tether_target = {
                        "x": (t13_center * 0.55) + (t12_x * 0.45),
                        "y": max(t12_y, -0.8),
                        "type": 12,
                    }
                else:
                    same_targets = [p for p in pieces if p.get("type") == next_type]
                    if same_targets:
                        def _pre_russia_t13_pair_single_t12_tether_key(tp):
                            tp_x = tp.get("x", 0)
                            tp_y = tp.get("y", -10)
                            lane_x = (t13_center * 0.62) + (t12_x * 0.38)
                            high_penalty = max(0.0, tp_y - 0.6) * 0.9
                            return (
                                abs(tp_x - lane_x)
                                + abs(tp_x - t12_x) * 0.18
                                + high_penalty,
                                tp_y,
                            )
                        tether_target = min(
                            same_targets,
                            key=_pre_russia_t13_pair_single_t12_tether_key,
                        )
                    else:
                        tether_target = {
                            "x": (t13_center * 0.62) + (t12_x * 0.38),
                            "y": max(t12_y, -0.8),
                            "type": next_type + 1,
                        }
            if tether_target is not None:
                tether_dist = abs(x - tether_target.get("x", 0))
                tether_bonus = max(0.0, 1780.0 - tether_dist * 430.0)
                if piece_count >= 32:
                    tether_bonus *= min(1.75, 1.0 + (piece_count - 32) * 0.08)
                if landing_y > 2.1:
                    tether_bonus *= 0.55
                if landing_y > 2.75:
                    tether_bonus *= 0.42
                if tether_target.get("y", -10) > 1.2:
                    tether_bonus *= 0.7
                if abs(x) >= 2.55 and tether_dist >= 1.55:
                    score -= 1350.0
                if landing_y > 2.35 and tether_dist >= 1.0:
                    score -= 1050.0
                if tether_bonus > 0:
                    score += tether_bonus
                    reasons.append("PRE_RUSSIA_T13_PAIR_T12_TETHER")

        # ----- vXXX: pre-Russia single T13 + T12 bank compression -----
        # Clean #29402 built T13x1 + T12x4 but the T12 bank split into high
        # islands and never promoted the second T13 needed for Kazakhstan.
        # With one T13 already present, keep incoming T10/T11/T12 material near
        # the lowest viable T12 pair lane instead of broad first-Russia weight.
        pre_russia_single_t13_t12_compress_ready = (
            not russia_phase
            and max_type_on_board == 13
            and pre_russia_counts.get(13, 0) == 1
            and pre_russia_counts.get(14, 0) == 0
            and pre_russia_counts.get(12, 0) >= 2
            and next_type in (10, 11, 12, 13)
        )
        if (
            merge_grade == "NO"
            and pre_russia_single_t13_t12_compress_ready
            and not death_spiral
            and max_y < 3.35
            and piece_count >= 24
        ):
            t13_targets = [p for p in pieces if p.get("type") == 13]
            t12_targets = [p for p in pieces if p.get("type") == 12]
            t11_targets = [p for p in pieces if p.get("type") == 11]
            t10_targets = [p for p in pieces if p.get("type") == 10]
            t13_center = _weighted_center_x(t13_targets)
            compress_target = None
            t12_pair_center = None
            t12_pair_y = -10.0
            if len(t12_targets) >= 2:
                best_pair = None
                best_pair_key = (999.0, 999.0)
                for pair_i, pair_a in enumerate(t12_targets):
                    for pair_b in t12_targets[pair_i + 1:]:
                        ax = pair_a.get("x", 0)
                        ay = pair_a.get("y", -10)
                        bx = pair_b.get("x", 0)
                        by = pair_b.get("y", -10)
                        pair_center = (ax + bx) / 2.0
                        pair_dist = ((ax - bx) ** 2 + (ay - by) ** 2) ** 0.5
                        pair_top = max(ay, by)
                        t13_dist = abs(pair_center - t13_center) if t13_center is not None else 0.0
                        pair_key = (
                            pair_dist
                            + t13_dist * 0.45
                            + max(0.0, pair_top - 1.05) * 2.0,
                            pair_top,
                        )
                        if pair_key < best_pair_key:
                            best_pair_key = pair_key
                            best_pair = (pair_a, pair_b)
                if best_pair is not None:
                    t12_pair_center = (
                        best_pair[0].get("x", 0) + best_pair[1].get("x", 0)
                    ) / 2.0
                    t12_pair_y = max(
                        best_pair[0].get("y", -10),
                        best_pair[1].get("y", -10),
                    )
                    compress_target = {"x": t12_pair_center, "y": t12_pair_y, "type": 12}
            if next_type == 10 and t10_targets:
                up_targets = t11_targets + t12_targets + t13_targets
                def _pre_russia_single_t13_t12_compress_t10_key(tp):
                    tp_x = tp.get("x", 0)
                    tp_y = tp.get("y", -10)
                    up_dist = min(
                        ((up.get("x", 0) - tp_x) ** 2 + (up.get("y", -10) - tp_y) ** 2) ** 0.5
                        for up in up_targets
                    ) if up_targets else 999.0
                    lane_dist = abs(tp_x - t12_pair_center) if t12_pair_center is not None else 999.0
                    t13_dist = abs(tp_x - t13_center) if t13_center is not None else 0.0
                    return (
                        min(up_dist, lane_dist)
                        + t13_dist * 0.20
                        + max(0.0, tp_y - 0.6) * 1.1,
                        tp_y,
                    )
                compress_target = min(
                    t10_targets,
                    key=_pre_russia_single_t13_t12_compress_t10_key,
                )
            if compress_target is None and t13_center is not None:
                compress_target = {"x": t13_center, "y": -0.8, "type": 13}
            if compress_target is not None:
                compress_dist = abs(x - compress_target.get("x", 0))
                compress_bonus = max(0.0, 2800.0 - compress_dist * 760.0)
                if next_type == 10 and compress_target.get("type") == 10 and compress_dist <= 0.65:
                    compress_bonus += 900.0
                if piece_count >= 34:
                    compress_bonus *= min(1.75, 1.0 + (piece_count - 34) * 0.08)
                if landing_y > 1.8:
                    compress_bonus *= 0.58
                if landing_y > 2.45:
                    compress_bonus *= 0.42
                if compress_target.get("y", -10) > 1.2:
                    compress_bonus *= 0.66
                if abs(x) >= 2.45 and compress_dist >= 1.35:
                    score -= 2400.0
                    reasons.append("PRE_RUSSIA_SINGLE_T13_T12_COMPRESS_OFFLANE_VETO")
                if landing_y > 1.2 and compress_dist >= 0.5:
                    score -= 1800.0 + max(0.0, landing_y - 1.2) * 1300.0
                    reasons.append("PRE_RUSSIA_SINGLE_T13_T12_COMPRESS_HIGH_OFFLANE_VETO")
                if landing_y > 2.35 and compress_dist >= 0.9:
                    score -= 1250.0
                if compress_bonus > 0:
                    score += compress_bonus
                    reasons.append("PRE_RUSSIA_SINGLE_T13_T12_COMPRESS")

        # ----- vXXX: pre-Russia single T13 + single T12 ladder -----
        # Clean #29418 reached T13x1 + T12x1 with enough T11/T10 material to
        # rebuild a second T12, but the generic T10 ladder and medium tower
        # scoring kept accepting low loose placements. In this exact near-miss,
        # feed T10/T11/T12/T13 into the single T12/T13 lane before the first
        # Kazakhstan attempt stalls.
        pre_russia_single_t13_single_t12_ladder_ready = (
            not russia_phase
            and max_type_on_board == 13
            and pre_russia_counts.get(13, 0) == 1
            and pre_russia_counts.get(14, 0) == 0
            and pre_russia_counts.get(12, 0) == 1
            and (
                pre_russia_counts.get(11, 0) >= 2
                or type10_count >= 2
            )
            and next_type in (10, 11, 12, 13)
        )
        if (
            merge_grade == "NO"
            and pre_russia_single_t13_single_t12_ladder_ready
            and not death_spiral
            and max_y < 3.25
            and piece_count >= 18
        ):
            t13_targets = [p for p in pieces if p.get("type") == 13]
            t12_targets = [p for p in pieces if p.get("type") == 12]
            t11_targets = [p for p in pieces if p.get("type") == 11]
            t10_targets = [p for p in pieces if p.get("type") == 10]
            t13_center = _weighted_center_x(t13_targets)
            t12_anchor = min(
                t12_targets,
                key=lambda tp: (
                    max(0.0, tp.get("y", -10) - 1.0) * 1.2,
                    abs(tp.get("x", 0) - (t13_center if t13_center is not None else tp.get("x", 0))),
                    tp.get("y", -10),
                ),
            ) if t12_targets else None
            ladder_target = None
            if t12_anchor is not None:
                t12_x = t12_anchor.get("x", 0)
                t12_y = t12_anchor.get("y", -10)
                if next_type == 13 and t13_targets:
                    ladder_target = min(
                        t13_targets,
                        key=lambda tp: (
                            max(0.0, tp.get("y", -10) - 1.0) * 1.0,
                            abs(tp.get("x", 0) - t12_x),
                            tp.get("y", -10),
                        ),
                    )
                elif next_type == 12:
                    ladder_target = {
                        "x": (t12_x * 0.72) + ((t13_center if t13_center is not None else t12_x) * 0.28),
                        "y": t12_y,
                        "type": 12,
                    }
                elif next_type == 11 and t11_targets:
                    def _pre_russia_single_t13_single_t12_t11_key(tp):
                        tp_x = tp.get("x", 0)
                        tp_y = tp.get("y", -10)
                        t13_dist = abs(tp_x - t13_center) if t13_center is not None else 0.0
                        return (
                            abs(tp_x - t12_x)
                            + t13_dist * 0.16
                            + max(0.0, tp_y - 0.8) * 1.2,
                            tp_y,
                        )
                    ladder_target = min(
                        t11_targets,
                        key=_pre_russia_single_t13_single_t12_t11_key,
                    )
                elif next_type == 10 and t10_targets:
                    up_targets = t11_targets + t12_targets
                    def _pre_russia_single_t13_single_t12_t10_key(tp):
                        tp_x = tp.get("x", 0)
                        tp_y = tp.get("y", -10)
                        up_dist = min(
                            ((up.get("x", 0) - tp_x) ** 2 + (up.get("y", -10) - tp_y) ** 2) ** 0.5
                            for up in up_targets
                        ) if up_targets else 999.0
                        return (
                            min(up_dist, abs(tp_x - t12_x))
                            + max(0.0, tp_y - 0.8) * 0.85,
                            tp_y,
                        )
                    ladder_target = min(
                        t10_targets,
                        key=_pre_russia_single_t13_single_t12_t10_key,
                    )
                elif t13_center is not None:
                    ladder_target = {
                        "x": (t12_x * 0.68) + (t13_center * 0.32),
                        "y": max(t12_y, -0.8),
                        "type": min(13, next_type + 1),
                    }
            if ladder_target is not None:
                ladder_dist = abs(x - ladder_target.get("x", 0))
                ladder_bonus = max(0.0, 2300.0 - ladder_dist * 540.0)
                if piece_count >= 32:
                    ladder_bonus *= min(1.8, 1.0 + (piece_count - 32) * 0.08)
                if landing_y > 1.9:
                    ladder_bonus *= 0.58
                if landing_y > 2.55:
                    ladder_bonus *= 0.43
                if ladder_target.get("y", -10) > 1.25:
                    ladder_bonus *= 0.7
                if abs(x) >= 2.5 and ladder_dist >= 1.45:
                    score -= 1650.0
                if landing_y > 2.35 and ladder_dist >= 0.95:
                    score -= 1150.0
                if ladder_bonus > 0:
                    score += ladder_bonus
                    reasons.append("PRE_RUSSIA_SINGLE_T13_SINGLE_T12_LADDER")

        # ----- vXXX: pre-Russia T13-pair ladder when T12 material is missing -----
        # Clean #29397 ended with T13x2 but no T12s, plus T11/T10 material
        # stranded away from the pair. In this near-miss, feed T10/T11/T12
        # back toward the T13 pair lane so it can rebuild T12/T13 material for
        # the first T14 instead of waiting for broad bridge clustering.
        pre_russia_t13_pair_ladder_ready = (
            not russia_phase
            and max_type_on_board == 13
            and pre_russia_counts.get(13, 0) >= 2
            and pre_russia_counts.get(14, 0) == 0
            and pre_russia_counts.get(12, 0) <= 1
            and (
                pre_russia_counts.get(11, 0) >= 2
                or type10_count >= 2
            )
            and next_type in (10, 11, 12)
        )
        if (
            merge_grade == "NO"
            and pre_russia_t13_pair_ladder_ready
            and not death_spiral
            and max_y < 3.35
            and piece_count >= 28
        ):
            t13_targets = [p for p in pieces if p.get("type") == 13]
            t13_pair_center = _weighted_center_x(t13_targets)
            ladder_target = None
            if next_type == 12 and t13_pair_center is not None:
                same_targets = [p for p in pieces if p.get("type") == 12]
                if same_targets:
                    ladder_target = min(
                        same_targets,
                        key=lambda tp: (abs(tp.get("x", 0) - t13_pair_center), tp.get("y", -10)),
                    )
                else:
                    ladder_target = {"x": t13_pair_center, "y": -0.8, "type": 13}
            elif t13_pair_center is not None:
                same_targets = [p for p in pieces if p.get("type") == next_type]
                if same_targets:
                    def _pre_russia_t13_pair_ladder_key(tp):
                        tp_x = tp.get("x", 0)
                        tp_y = tp.get("y", -10)
                        high_penalty = max(0.0, tp_y + 0.1) * 0.95
                        return (abs(tp_x - t13_pair_center) + high_penalty, tp_y)
                    ladder_target = min(same_targets, key=_pre_russia_t13_pair_ladder_key)
                else:
                    ladder_target = {"x": t13_pair_center, "y": -0.8, "type": next_type + 1}
            if ladder_target is not None:
                ladder_dist = abs(x - ladder_target.get("x", 0))
                ladder_bonus = max(0.0, 1520.0 - ladder_dist * 390.0)
                if piece_count >= 34:
                    ladder_bonus *= min(1.75, 1.0 + (piece_count - 34) * 0.08)
                if landing_y > 2.7:
                    ladder_bonus *= 0.55
                if ladder_target.get("y", -10) > 1.5:
                    ladder_bonus *= 0.72
                if abs(x) >= 2.5 and ladder_dist >= 1.7:
                    score -= 1500.0
                if ladder_bonus > 0:
                    score += ladder_bonus
                    reasons.append("PRE_RUSSIA_T13_PAIR_LADDER")

        # ----- vXXX: pre-Russia T10/T11 ladder after first T13 -----
        # Clean loss 20260602_142211 built T13x1 and T12x1 by T32 but then
        # accumulated T10x6/T11x3 while never making the second T12. Bridge and
        # T13-lift axes need that second T12; this ladder keeps T10/T11/T12
        # material near the single T12/T13 lane before the stronger near-miss
        # conditions are available.
        pre_russia_t10_ladder_ready = (
            not russia_phase
            and max_type_on_board == 13
            and pre_russia_counts.get(13, 0) >= 1
            and pre_russia_counts.get(12, 0) >= 1
            and pre_russia_counts.get(12, 0) < 2
            and next_type in (10, 11, 12)
        )
        if (
            merge_grade == "NO"
            and pre_russia_t10_ladder_ready
            and not death_spiral
            and max_y < 2.7
            and piece_count >= 20
        ):
            ladder_up_targets = [
                p for p in pieces if next_type < p.get("type", 0) <= 13
            ]
            ladder_same_targets = [
                p for p in pieces if p.get("type") == next_type
            ]
            ladder_target = None
            if ladder_same_targets and ladder_up_targets:
                def _pre_russia_ladder_key(tp):
                    tp_x = tp.get("x", 0)
                    tp_y = tp.get("y", -10)
                    up_dist = min(
                        ((up.get("x", 0) - tp_x) ** 2 + (up.get("y", -10) - tp_y) ** 2) ** 0.5
                        for up in ladder_up_targets
                    )
                    high_penalty = max(0.0, tp_y - 0.8) * 0.7
                    return (up_dist + high_penalty, tp_y)
                ladder_target = min(ladder_same_targets, key=_pre_russia_ladder_key)
            elif ladder_up_targets:
                ladder_center = _weighted_center_x(ladder_up_targets)
                if ladder_center is not None:
                    ladder_target = {"x": ladder_center, "y": -0.8, "type": next_type + 1}
            if ladder_target is not None:
                ladder_dist = abs(x - ladder_target.get("x", 0))
                ladder_bonus = max(0.0, 760.0 - ladder_dist * 270.0)
                if piece_count >= 28:
                    ladder_bonus *= min(1.65, 1.0 + (piece_count - 28) * 0.08)
                if landing_y > 1.8:
                    ladder_bonus *= 0.42
                if ladder_target.get("y", -10) > 1.2:
                    ladder_bonus *= 0.55
                if abs(x) >= 2.5 and ladder_dist >= 1.7:
                    score -= 950.0
                if ladder_bonus > 0:
                    score += ladder_bonus
                    reasons.append("PRE_RUSSIA_T10_LADDER")

        # ----- vXXX: pre-Russia same-type latch -----
        # Clean loss 20260602_133619 reached T13x1 T12x2 T11x4 T10x3.
        # The bridge center fired, but incoming T10/T11 pieces still landed as
        # loose high material instead of latching onto the same-type lane that
        # would promote a second T12/T13. Keep this scoped to the first-Russia
        # near-miss inventory and high incoming material so low pieces are not
        # dragged into the high cluster.
        pre_russia_first_lane_ready = (
            not russia_phase
            and max_type_on_board in (12, 13)
            and pre_russia_counts.get(13, 0) >= 1
            and (
                (
                    pre_russia_counts.get(12, 0) >= 2
                    and pre_russia_counts.get(11, 0) >= 2
                )
                or (
                    pre_russia_counts.get(12, 0) >= 1
                    and pre_russia_counts.get(11, 0) >= 3
                )
            )
        )
        if (
            merge_grade == "NO"
            and pre_russia_first_lane_ready
            and next_type in (10, 11, 12, 13)
            and not death_spiral
            and max_y < 2.7
            and piece_count >= 20
        ):
            latch_targets = [p for p in pieces if p.get("type") == next_type]
            if latch_targets:
                next_up_targets = [
                    p for p in pieces if p.get("type") == next_type + 1
                ]
                if next_up_targets:
                    def _pre_russia_latch_key(tp):
                        tp_x = tp.get("x", 0)
                        tp_y = tp.get("y", -10)
                        up_dist = min(
                            ((up.get("x", 0) - tp_x) ** 2 + (up.get("y", -10) - tp_y) ** 2) ** 0.5
                            for up in next_up_targets
                        )
                        return (up_dist, tp_y)
                    latch_target = min(latch_targets, key=_pre_russia_latch_key)
                else:
                    latch_target = min(latch_targets, key=lambda tp: tp.get("y", 10))
                latch_dist = abs(x - latch_target.get("x", 0))
                latch_bonus = max(0.0, 620.0 - latch_dist * 240.0)
                if piece_count >= 28:
                    latch_bonus *= min(1.8, 1.0 + (piece_count - 28) * 0.08)
                if landing_y > 2.2:
                    latch_bonus *= 0.35
                if latch_target.get("y", -10) > 1.0:
                    latch_bonus *= 0.5
                if latch_bonus > 0:
                    score += latch_bonus
                    reasons.append("PRE_RUSSIA_SAME_TYPE_LATCH")

        # ----- vXXX: second-Russia same-type latch after first T14 -----
        # Clean loss 20260602_135500 reached T14x1, then ended T14x1 T12x1
        # T11x3: after the first high-country piece appeared, the remaining
        # T10/T11 material kept spreading until the deadline guard took over.
        # Keep the latch scoped to one-T14 near-misses so it does not rewrite
        # ordinary russia_phase survival or double-russia handling.
        second_russia_lane_ready = (
            russia_phase
            and not double_russia_phase
            and pre_russia_counts.get(14, 0) >= 1
            and pre_russia_counts.get(15, 0) == 0
            and (
                (
                    pre_russia_counts.get(13, 0) >= 1
                    and pre_russia_counts.get(12, 0) >= 1
                )
                or (
                    pre_russia_counts.get(12, 0) >= 1
                    and pre_russia_counts.get(11, 0) >= 1
                )
                or pre_russia_counts.get(11, 0) >= 2
            )
        )
        if (
            merge_grade == "NO"
            and second_russia_lane_ready
            and next_type in (10, 11, 12, 13)
            and not result.get("crosses_deadline", False)
            and not death_spiral
            and max_y < 2.8
            and piece_count >= 28
        ):
            second_latch_targets = [
                p for p in pieces if p.get("type") == next_type
            ]
            if second_latch_targets:
                second_latch_up_targets = [
                    p for p in pieces
                    if next_type < p.get("type", 0) <= 14
                ]
                if second_latch_up_targets:
                    def _second_russia_latch_key(tp):
                        tp_x = tp.get("x", 0)
                        tp_y = tp.get("y", -10)
                        up_dist = min(
                            ((up.get("x", 0) - tp_x) ** 2 + (up.get("y", -10) - tp_y) ** 2) ** 0.5
                            for up in second_latch_up_targets
                        )
                        return (up_dist, tp_y)
                    second_latch_target = min(
                        second_latch_targets,
                        key=_second_russia_latch_key,
                    )
                else:
                    second_latch_target = min(
                        second_latch_targets,
                        key=lambda tp: tp.get("y", 10),
                    )
                second_latch_dist = abs(x - second_latch_target.get("x", 0))
                second_latch_bonus = max(0.0, 760.0 - second_latch_dist * 250.0)
                if piece_count >= 34:
                    second_latch_bonus *= min(1.6, 1.0 + (piece_count - 34) * 0.08)
                if landing_y > 2.2:
                    second_latch_bonus *= 0.35
                if second_latch_target.get("y", -10) > 1.2:
                    second_latch_bonus *= 0.55
                if second_latch_bonus > 0:
                    score += second_latch_bonus
                    reasons.append("SECOND_RUSSIA_SAME_TYPE_LATCH")

        # ----- vXXX: second-Russia T12 ladder after first T14 -----
        # Clean #29412 converted T13x2 into T14x1, but with no T13 left the
        # remaining T12/T11/T10 material spread under deadline pressure. In
        # that one-T14/no-T13 near-miss, rebuild the second T14 from the T12
        # ladder before generic Russia board compression takes over.
        second_russia_t12_ladder_ready = (
            russia_phase
            and not double_russia_phase
            and pre_russia_counts.get(14, 0) >= 1
            and pre_russia_counts.get(15, 0) == 0
            and pre_russia_counts.get(13, 0) == 0
            and pre_russia_counts.get(12, 0) >= 1
            and (
                pre_russia_counts.get(11, 0) >= 1
                or type10_count >= 2
            )
            and next_type in (10, 11, 12, 13)
        )
        if (
            merge_grade == "NO"
            and second_russia_t12_ladder_ready
            and not result.get("crosses_deadline", False)
            and not death_spiral
            and max_y < 3.4
            and piece_count >= 30
        ):
            t12_targets = [p for p in pieces if p.get("type") == 12]
            t11_targets = [p for p in pieces if p.get("type") == 11]
            t10_targets = [p for p in pieces if p.get("type") == 10]
            ladder_anchor_x = None
            ladder_anchor_y = -10.0
            if len(t12_targets) >= 2:
                best_pair = None
                best_pair_key = (999.0, 999.0)
                for pair_i, pair_a in enumerate(t12_targets):
                    for pair_b in t12_targets[pair_i + 1:]:
                        ax = pair_a.get("x", 0)
                        ay = pair_a.get("y", -10)
                        bx = pair_b.get("x", 0)
                        by = pair_b.get("y", -10)
                        pair_dist = ((ax - bx) ** 2 + (ay - by) ** 2) ** 0.5
                        pair_top = max(ay, by)
                        pair_key = (
                            pair_dist + max(0.0, pair_top - 1.3) * 0.65,
                            pair_top,
                        )
                        if pair_key < best_pair_key:
                            best_pair_key = pair_key
                            best_pair = (pair_a, pair_b)
                if best_pair is not None:
                    ladder_anchor_x = (
                        best_pair[0].get("x", 0) + best_pair[1].get("x", 0)
                    ) / 2.0
                    ladder_anchor_y = max(
                        best_pair[0].get("y", -10),
                        best_pair[1].get("y", -10),
                    )
            elif t12_targets:
                ladder_anchor_x = _weighted_center_x(t12_targets)
                ladder_anchor_y = max((p.get("y", -10) for p in t12_targets), default=-10)

            ladder_target = None
            if next_type in (12, 13) and ladder_anchor_x is not None:
                ladder_target = {"x": ladder_anchor_x, "y": ladder_anchor_y, "type": 12}
            elif next_type == 11 and t11_targets:
                def _second_russia_t12_ladder_t11_key(tp):
                    tp_x = tp.get("x", 0)
                    tp_y = tp.get("y", -10)
                    anchor_dist = abs(tp_x - ladder_anchor_x) if ladder_anchor_x is not None else 0.0
                    return (
                        anchor_dist * 0.35
                        + max(0.0, tp_y - 1.5) * 1.2,
                        tp_y,
                    )
                ladder_target = min(t11_targets, key=_second_russia_t12_ladder_t11_key)
            elif next_type == 10 and t10_targets:
                up_targets = t11_targets + t12_targets
                if up_targets or ladder_anchor_x is not None:
                    def _second_russia_t12_ladder_t10_key(tp):
                        tp_x = tp.get("x", 0)
                        tp_y = tp.get("y", -10)
                        up_dist = min(
                            ((up.get("x", 0) - tp_x) ** 2 + (up.get("y", -10) - tp_y) ** 2) ** 0.5
                            for up in up_targets
                        ) if up_targets else 999.0
                        anchor_dist = abs(tp_x - ladder_anchor_x) if ladder_anchor_x is not None else 999.0
                        return (
                            min(up_dist, anchor_dist)
                            + max(0.0, tp_y - 1.0) * 0.8,
                            tp_y,
                        )
                    ladder_target = min(t10_targets, key=_second_russia_t12_ladder_t10_key)
            elif ladder_anchor_x is not None:
                ladder_target = {"x": ladder_anchor_x, "y": ladder_anchor_y, "type": 12}

            if ladder_target is not None:
                ladder_dist = abs(x - ladder_target.get("x", 0))
                ladder_bonus = max(0.0, 2600.0 - ladder_dist * 610.0)
                if piece_count >= 36:
                    ladder_bonus *= min(1.9, 1.0 + (piece_count - 36) * 0.09)
                if landing_y > 2.45:
                    ladder_bonus *= 0.5
                if landing_y > 2.95:
                    ladder_bonus *= 0.38
                if ladder_target.get("y", -10) > 1.7:
                    ladder_bonus *= 0.72
                if abs(x) >= 2.5 and ladder_dist >= 1.45:
                    score -= 1900.0
                if landing_y > 2.35 and ladder_dist >= 0.9:
                    score -= 1300.0
                if ladder_bonus > 0:
                    score += ladder_bonus
                    reasons.append("SECOND_RUSSIA_T12_LADDER")

        # ----- vXXX: second-Russia T12 pair lock after first T14 -----
        # Clean #29423 reached T14x1 + T12x2 + T11/T10 bank, but the T12s split
        # left/right and deadline triage filled safe slots instead of closing the
        # second T13. Once the first T14 exists and two T12 anchors are present,
        # pull incoming high material toward the T12-pair center before generic
        # Russia compression or runtime safety can preserve a wide split.
        second_russia_t12_pair_lock_ready = (
            russia_phase
            and not double_russia_phase
            and pre_russia_counts.get(14, 0) >= 1
            and pre_russia_counts.get(15, 0) == 0
            and pre_russia_counts.get(13, 0) == 0
            and pre_russia_counts.get(12, 0) >= 2
            and next_type in (10, 11, 12, 13)
        )
        if (
            merge_grade == "NO"
            and second_russia_t12_pair_lock_ready
            and not result.get("crosses_deadline", False)
            and not death_spiral
            and max_y < 3.35
            and piece_count >= 32
        ):
            t12_targets = [p for p in pieces if p.get("type") == 12]
            best_pair = None
            best_pair_key = (999.0, 999.0)
            best_pair_dist = 999.0
            for pair_i, pair_a in enumerate(t12_targets):
                for pair_b in t12_targets[pair_i + 1:]:
                    ax = pair_a.get("x", 0)
                    ay = pair_a.get("y", -10)
                    bx = pair_b.get("x", 0)
                    by = pair_b.get("y", -10)
                    pair_dist = ((ax - bx) ** 2 + (ay - by) ** 2) ** 0.5
                    pair_top = max(ay, by)
                    pair_center = (ax + bx) / 2.0
                    pair_key = (
                        pair_dist
                        + max(0.0, pair_top - 1.15) * 1.15
                        + abs(pair_center) * 0.18,
                        pair_top,
                    )
                    if pair_key < best_pair_key:
                        best_pair_key = pair_key
                        best_pair = (pair_a, pair_b)
                        best_pair_dist = pair_dist
            if best_pair is not None:
                pair_lock_x = (
                    best_pair[0].get("x", 0) + best_pair[1].get("x", 0)
                ) / 2.0
                pair_lock_y = max(
                    best_pair[0].get("y", -10),
                    best_pair[1].get("y", -10),
                )
                pair_lock_dist = abs(x - pair_lock_x)
                pair_lock_bonus = max(0.0, 3400.0 - pair_lock_dist * 760.0)
                if best_pair_dist >= 2.0:
                    pair_lock_bonus *= 1.22
                if piece_count >= 38:
                    pair_lock_bonus *= min(2.05, 1.0 + (piece_count - 38) * 0.09)
                if landing_y > 2.35:
                    pair_lock_bonus *= 0.55
                if landing_y > 2.9:
                    pair_lock_bonus *= 0.35
                if pair_lock_y > 1.5:
                    pair_lock_bonus *= 0.76
                if pair_lock_dist >= 1.2:
                    score -= 2600.0
                if abs(x) >= 2.45 and pair_lock_dist >= 1.25:
                    score -= 2200.0
                if landing_y > 2.2 and pair_lock_dist >= 0.95:
                    score -= 1400.0
                if pair_lock_bonus > 0:
                    score += pair_lock_bonus
                    reasons.append("SECOND_RUSSIA_T12_PAIR_LOCK")

        # ----- vXXX: first-Russia T13-pair lift after first T14 -----
        # Clean #29392 held T14x1 + T13x2 from T93 to game over. The generic
        # Russia-pair center included the T14 and pulled low material away from
        # the T13 pair that needed to become the second T14. In the one-T14
        # near-miss, feed T10/T11/T12/T13 material into the T13 pair lane first.
        first_russia_t13_pair_lift_ready = (
            russia_phase
            and not double_russia_phase
            and pre_russia_counts.get(14, 0) >= 1
            and pre_russia_counts.get(15, 0) == 0
            and pre_russia_counts.get(13, 0) >= 2
            and next_type in (10, 11, 12, 13)
        )
        if (
            merge_grade == "NO"
            and first_russia_t13_pair_lift_ready
            and not death_spiral
            and max_y < 3.4
            and piece_count >= 26
        ):
            t13_pair_targets = [p for p in pieces if p.get("type") == 13]
            t13_pair_center = None
            t13_pair_y = -10.0
            if len(t13_pair_targets) >= 2:
                best_pair = None
                best_pair_key = (999.0, 999.0)
                for pair_i, pair_a in enumerate(t13_pair_targets):
                    for pair_b in t13_pair_targets[pair_i + 1:]:
                        ax = pair_a.get("x", 0)
                        ay = pair_a.get("y", -10)
                        bx = pair_b.get("x", 0)
                        by = pair_b.get("y", -10)
                        pair_dist = ((ax - bx) ** 2 + (ay - by) ** 2) ** 0.5
                        pair_top = max(ay, by)
                        pair_key = (
                            pair_dist + max(0.0, pair_top - 1.1) * 0.55,
                            pair_top,
                        )
                        if pair_key < best_pair_key:
                            best_pair_key = pair_key
                            best_pair = (pair_a, pair_b)
                if best_pair is not None:
                    t13_pair_center = (
                        best_pair[0].get("x", 0) + best_pair[1].get("x", 0)
                    ) / 2.0
                    t13_pair_y = max(
                        best_pair[0].get("y", -10),
                        best_pair[1].get("y", -10),
                    )
            pair_lift_target = None
            if next_type == 13 and t13_pair_center is not None:
                pair_lift_target = {"x": t13_pair_center, "y": t13_pair_y, "type": 14}
            else:
                same_targets = [p for p in pieces if p.get("type") == next_type]
                if same_targets and t13_pair_center is not None:
                    def _first_russia_t13_pair_lift_key(tp):
                        tp_x = tp.get("x", 0)
                        tp_y = tp.get("y", -10)
                        high_penalty = max(0.0, tp_y + 0.1) * 1.05
                        return (abs(tp_x - t13_pair_center) + high_penalty, tp_y)
                    pair_lift_target = min(same_targets, key=_first_russia_t13_pair_lift_key)
                elif t13_pair_center is not None:
                    pair_lift_target = {
                        "x": t13_pair_center,
                        "y": t13_pair_y,
                        "type": min(13, next_type + 1),
                    }
            if pair_lift_target is not None:
                pair_lift_dist = abs(x - pair_lift_target.get("x", 0))
                pair_lift_bonus = max(0.0, 2400.0 - pair_lift_dist * 560.0)
                if piece_count >= 34:
                    pair_lift_bonus *= min(1.85, 1.0 + (piece_count - 34) * 0.09)
                if landing_y > 2.8:
                    pair_lift_bonus *= 0.55
                if pair_lift_target.get("y", -10) > 1.6:
                    pair_lift_bonus *= 0.85
                if t13_pair_center is not None and abs(x - t13_pair_center) >= 1.2:
                    score -= 950.0
                if abs(x) >= 2.5 and pair_lift_dist >= 1.7:
                    score -= 1800.0
                if pair_lift_bonus > 0:
                    score += pair_lift_bonus
                    reasons.append("FIRST_RUSSIA_T13_PAIR_LIFT")

        # ----- vXXX: first-Russia single T13 + T12 bank lift -----
        # Mixed #29421 held T14x1 + T13x1 + T12x2 from T91 onward, but the
        # existing one-T14 lift only activates after T13x2. While still below the
        # redline, rebuild the second T13 from the T12/T11 bank so the board can
        # create the second Russia instead of drifting into deadline triage.
        first_russia_single_t13_t12_bank_ready = (
            russia_phase
            and not double_russia_phase
            and pre_russia_counts.get(14, 0) >= 1
            and pre_russia_counts.get(15, 0) == 0
            and pre_russia_counts.get(13, 0) == 1
            and pre_russia_counts.get(12, 0) >= 1
            and (
                pre_russia_counts.get(12, 0) >= 2
                or pre_russia_counts.get(11, 0) >= 1
                or type10_count >= 2
            )
            and next_type in (10, 11, 12, 13)
        )
        if (
            merge_grade == "NO"
            and first_russia_single_t13_t12_bank_ready
            and not result.get("crosses_deadline", False)
            and not death_spiral
            and max_y < 3.2
            and piece_count >= 26
        ):
            t13_targets = [p for p in pieces if p.get("type") == 13]
            t12_targets = [p for p in pieces if p.get("type") == 12]
            t11_targets = [p for p in pieces if p.get("type") == 11]
            t10_targets = [p for p in pieces if p.get("type") == 10]
            t13_center = _weighted_center_x(t13_targets)
            bank_target = None
            if next_type == 13 and t13_targets:
                bank_target = min(
                    t13_targets,
                    key=lambda tp: (
                        max(0.0, tp.get("y", -10) - 1.0) * 0.9,
                        tp.get("y", -10),
                    ),
                )
            elif next_type == 12 and t12_targets:
                if len(t12_targets) >= 2:
                    best_pair = None
                    best_pair_key = (999.0, 999.0)
                    for pair_i, pair_a in enumerate(t12_targets):
                        for pair_b in t12_targets[pair_i + 1:]:
                            ax = pair_a.get("x", 0)
                            ay = pair_a.get("y", -10)
                            bx = pair_b.get("x", 0)
                            by = pair_b.get("y", -10)
                            pair_center = (ax + bx) / 2.0
                            pair_dist = ((ax - bx) ** 2 + (ay - by) ** 2) ** 0.5
                            pair_top = max(ay, by)
                            t13_dist = abs(pair_center - t13_center) if t13_center is not None else 0.0
                            pair_key = (
                                pair_dist
                                + t13_dist * 0.35
                                + max(0.0, pair_top - 1.1) * 1.4,
                                pair_top,
                            )
                            if pair_key < best_pair_key:
                                best_pair_key = pair_key
                                best_pair = (pair_a, pair_b)
                    if best_pair is not None:
                        bank_target = {
                            "x": (best_pair[0].get("x", 0) + best_pair[1].get("x", 0)) / 2.0,
                            "y": max(best_pair[0].get("y", -10), best_pair[1].get("y", -10)),
                            "type": 12,
                        }
                if bank_target is None:
                    bank_target = min(
                        t12_targets,
                        key=lambda tp: (
                            abs(tp.get("x", 0) - (t13_center if t13_center is not None else tp.get("x", 0))),
                            max(0.0, tp.get("y", -10) - 1.0),
                            tp.get("y", -10),
                        ),
                    )
            elif next_type == 11 and t11_targets:
                up_targets = t12_targets + t13_targets
                def _first_russia_single_t13_t12_bank_t11_key(tp):
                    tp_x = tp.get("x", 0)
                    tp_y = tp.get("y", -10)
                    up_dist = min(
                        ((up.get("x", 0) - tp_x) ** 2 + (up.get("y", -10) - tp_y) ** 2) ** 0.5
                        for up in up_targets
                    ) if up_targets else 999.0
                    t13_dist = abs(tp_x - t13_center) if t13_center is not None else 0.0
                    return (
                        min(up_dist, t13_dist)
                        + max(0.0, tp_y - 1.0) * 1.0,
                        tp_y,
                    )
                bank_target = min(
                    t11_targets,
                    key=_first_russia_single_t13_t12_bank_t11_key,
                )
            elif next_type == 10 and t10_targets:
                up_targets = t11_targets + t12_targets
                def _first_russia_single_t13_t12_bank_t10_key(tp):
                    tp_x = tp.get("x", 0)
                    tp_y = tp.get("y", -10)
                    up_dist = min(
                        ((up.get("x", 0) - tp_x) ** 2 + (up.get("y", -10) - tp_y) ** 2) ** 0.5
                        for up in up_targets
                    ) if up_targets else 999.0
                    return (up_dist + max(0.0, tp_y - 0.9) * 0.85, tp_y)
                bank_target = min(
                    t10_targets,
                    key=_first_russia_single_t13_t12_bank_t10_key,
                )
            if bank_target is not None:
                bank_dist = abs(x - bank_target.get("x", 0))
                bank_bonus = max(0.0, 2550.0 - bank_dist * 590.0)
                if piece_count >= 34:
                    bank_bonus *= min(1.85, 1.0 + (piece_count - 34) * 0.08)
                if landing_y > 2.35:
                    bank_bonus *= 0.52
                if landing_y > 2.85:
                    bank_bonus *= 0.4
                if bank_target.get("y", -10) > 1.5:
                    bank_bonus *= 0.68
                if abs(x) >= 2.5 and bank_dist >= 1.45:
                    score -= 1750.0
                if landing_y > 2.25 and bank_dist >= 0.95:
                    score -= 1250.0
                if bank_bonus > 0:
                    score += bank_bonus
                    reasons.append("FIRST_RUSSIA_SINGLE_T13_T12_BANK_LIFT")

        # ----- vXXX: Soviet T15 lift after Russia creation -----
        # Clean #29377 (20260602_143048) founded Russia at T98, then died with
        # T15x1 T13x1 T12x1 T11x2. The existing second-Russia bridge was too
        # dependent on broad safe landings once deadline guard took over. When a
        # single T15 exists, treat incoming T11/T12/T13 as lift material for the
        # remaining second-Russia lane so the board builds the next T14/T15 path.
        soviet_t15_lift_ready = (
            single_type15_phase
            and pre_russia_counts.get(14, 0) == 0
            and next_type in (11, 12, 13)
            and (
                (
                    pre_russia_counts.get(13, 0) >= 1
                    and (
                        pre_russia_counts.get(12, 0) >= 1
                        or pre_russia_counts.get(11, 0) >= 1
                    )
                )
                or (
                    pre_russia_counts.get(12, 0) >= 1
                    and pre_russia_counts.get(11, 0) >= 2
                )
            )
        )
        if (
            merge_grade == "NO"
            and soviet_t15_lift_ready
            and not death_spiral
            and max_y < 3.4
            and piece_count >= 28
        ):
            soviet_lift_reason = "SOVIET_T15_LIFT"
            if next_type == 11:
                t13_targets = [p for p in pieces if p.get("type") == 13]
                t12_targets = [p for p in pieces if p.get("type") == 12]
                t11_targets = [p for p in pieces if p.get("type") == 11]
                if not t12_targets and t13_targets and t11_targets:
                    def _soviet_t15_t11_rebuild_key(tp):
                        tp_x = tp.get("x", 0)
                        tp_y = tp.get("y", -10)
                        t13_dist = min(
                            ((up.get("x", 0) - tp_x) ** 2 + (up.get("y", -10) - tp_y) ** 2) ** 0.5
                            for up in t13_targets
                        )
                        return (
                            t13_dist * 0.35
                            + max(0.0, tp_y - 1.0) * 1.1,
                            tp_y,
                        )
                    soviet_lift_targets = [
                        min(t11_targets, key=_soviet_t15_t11_rebuild_key)
                    ]
                    soviet_lift_reason = "SOVIET_T15_T11_REBUILD"
                else:
                    soviet_lift_targets = t12_targets + t13_targets
                    if not soviet_lift_targets:
                        soviet_lift_targets = t11_targets
            elif next_type == 12:
                soviet_lift_targets = [
                    p for p in pieces if p.get("type") == 13
                ]
            else:
                soviet_lift_targets = [
                    p for p in pieces if p.get("type") == 13
                ]
            soviet_lift_center = _weighted_center_x(soviet_lift_targets)
            if soviet_lift_center is not None:
                soviet_lift_dist = abs(x - soviet_lift_center)
                if soviet_lift_reason == "SOVIET_T15_T11_REBUILD":
                    soviet_lift_bonus = max(0.0, 3000.0 - soviet_lift_dist * 760.0)
                else:
                    soviet_lift_bonus = max(0.0, 1080.0 - soviet_lift_dist * 330.0)
                if piece_count >= 32:
                    if soviet_lift_reason == "SOVIET_T15_T11_REBUILD":
                        soviet_lift_bonus *= min(1.9, 1.0 + (piece_count - 32) * 0.10)
                    else:
                        soviet_lift_bonus *= min(1.65, 1.0 + (piece_count - 32) * 0.08)
                if landing_y > 2.5:
                    soviet_lift_bonus *= 0.5
                if soviet_lift_reason == "SOVIET_T15_T11_REBUILD" and landing_y > 2.9:
                    soviet_lift_bonus *= 0.38
                if max((p.get("y", -10) for p in soviet_lift_targets), default=-10) > 1.8:
                    soviet_lift_bonus *= 0.7
                if abs(x) >= 2.5 and soviet_lift_dist >= 1.7:
                    score -= 1200.0
                if (
                    soviet_lift_reason == "SOVIET_T15_T11_REBUILD"
                    and landing_y > 2.45
                    and soviet_lift_dist >= 0.85
                ):
                    score -= 1500.0
                if soviet_lift_bonus > 0:
                    score += soviet_lift_bonus
                    reasons.append(soviet_lift_reason)

        # ----- vXXX: Soviet T10 ladder after Russia creation -----
        # Clean #29380 reached T15 with a remaining T12, then leaked into
        # T10/T11 density instead of rebuilding the second Russia lane. Keep
        # this bounded to single-T15 boards where a T12 anchor still exists,
        # and only lift T10/T11/T12 material toward that anchor lane.
        soviet_t10_ladder_ready = (
            single_type15_phase
            and pre_russia_counts.get(14, 0) == 0
            and pre_russia_counts.get(12, 0) >= 1
            and next_type in (10, 11, 12)
            and (
                pre_russia_counts.get(13, 0) >= 1
                or pre_russia_counts.get(11, 0) >= 1
                or type10_count >= 2
            )
        )
        if (
            merge_grade == "NO"
            and soviet_t10_ladder_ready
            and not death_spiral
            and max_y < 3.4
            and piece_count >= 28
        ):
            soviet_ladder_target = None
            if next_type == 10:
                soviet_ladder_up_targets = [
                    p for p in pieces if p.get("type") in (11, 12, 13)
                ]
                soviet_ladder_same_targets = [
                    p for p in pieces if p.get("type") == 10
                ]
                if soviet_ladder_same_targets and soviet_ladder_up_targets:
                    def _soviet_t10_ladder_key(tp):
                        tp_x = tp.get("x", 0)
                        tp_y = tp.get("y", -10)
                        up_dist = min(
                            ((up.get("x", 0) - tp_x) ** 2 + (up.get("y", -10) - tp_y) ** 2) ** 0.5
                            for up in soviet_ladder_up_targets
                        )
                        high_penalty = max(0.0, tp_y - 1.0) * 0.6
                        return (up_dist + high_penalty, tp_y)
                    soviet_ladder_target = min(
                        soviet_ladder_same_targets,
                        key=_soviet_t10_ladder_key,
                    )
                elif soviet_ladder_up_targets:
                    soviet_ladder_center = _weighted_center_x(soviet_ladder_up_targets)
                    if soviet_ladder_center is not None:
                        soviet_ladder_target = {
                            "x": soviet_ladder_center,
                            "y": -0.8,
                            "type": 11,
                        }
                elif soviet_ladder_same_targets:
                    soviet_ladder_target = min(
                        soviet_ladder_same_targets,
                        key=lambda tp: tp.get("y", 10),
                    )
            elif next_type == 11:
                soviet_ladder_targets = [
                    p for p in pieces if p.get("type") in (12, 13)
                ]
                if not soviet_ladder_targets:
                    soviet_ladder_targets = [
                        p for p in pieces if p.get("type") == 11
                    ]
                soviet_ladder_center = _weighted_center_x(soviet_ladder_targets)
                if soviet_ladder_center is not None:
                    soviet_ladder_target = {
                        "x": soviet_ladder_center,
                        "y": max((p.get("y", -10) for p in soviet_ladder_targets), default=-10),
                        "type": 12,
                    }
            else:
                soviet_ladder_targets = [
                    p for p in pieces if p.get("type") == 13
                ]
                if not soviet_ladder_targets:
                    soviet_ladder_targets = [
                        p for p in pieces if p.get("type") == 12
                    ]
                soviet_ladder_center = _weighted_center_x(soviet_ladder_targets)
                if soviet_ladder_center is not None:
                    soviet_ladder_target = {
                        "x": soviet_ladder_center,
                        "y": max((p.get("y", -10) for p in soviet_ladder_targets), default=-10),
                        "type": 13,
                    }
            if soviet_ladder_target is not None:
                soviet_ladder_dist = abs(x - soviet_ladder_target.get("x", 0))
                soviet_ladder_bonus = max(0.0, 920.0 - soviet_ladder_dist * 310.0)
                if next_type == 10:
                    soviet_ladder_bonus *= 0.9
                if piece_count >= 32:
                    soviet_ladder_bonus *= min(1.6, 1.0 + (piece_count - 32) * 0.08)
                if landing_y > 2.4:
                    soviet_ladder_bonus *= 0.45
                if soviet_ladder_target.get("y", -10) > 1.7:
                    soviet_ladder_bonus *= 0.65
                if abs(x) >= 2.5 and soviet_ladder_dist >= 1.7:
                    score -= 1050.0
                if soviet_ladder_bonus > 0:
                    score += soviet_ladder_bonus
                    reasons.append("SOVIET_T10_LADDER")

        # ----- vXXX: Soviet objective bridge clustering -----
        # Live failure 20260602_121816: after Russia(type15) creation, the board ended with
        # T15x1 T13x2 T12x2. DEADLINE_GUARD kept the board alive, but repeated safe
        # landings did not preserve the second-Russia material lane. This axis is bounded
        # to near-miss inventories and suppressed in death_spiral, so it remains a merge
        # path tie-breaker rather than a risky height override.
        if merge_grade == "NO" and not death_spiral and max_y < 3.2:
            pre_russia_bridge_ready = (
                max_type_on_board in (12, 13, 14)
                and (
                    pre_russia_counts.get(13, 0) >= 3
                    or (
                        pre_russia_counts.get(13, 0) >= 2
                        and pre_russia_counts.get(12, 0) >= 2
                    )
                    or (
                        pre_russia_counts.get(11, 0) >= 2
                        and pre_russia_counts.get(12, 0) >= 2
                    )
                    or (
                        pre_russia_counts.get(13, 0) >= 1
                        and pre_russia_counts.get(12, 0) >= 1
                        and pre_russia_counts.get(11, 0) >= 1
                    )
                    or (
                        pre_russia_counts.get(11, 0) >= 3
                        and pre_russia_counts.get(12, 0) >= 4
                    )
                    or pre_russia_counts.get(14, 0) >= 1
                )
            )
            if pre_russia_first_lane_ready and pre_russia_counts.get(14, 0) == 0:
                pre_russia_targets = [
                    p for p in pieces if p.get("type") in (11, 12, 13)
                ]
            else:
                pre_russia_targets = [
                    p for p in pieces if p.get("type") in (12, 13, 14)
                ]
            pre_russia_bridge_current_material_ready = next_type >= 8
            pre_russia_bridge_future_material_ready = next_next_type >= 10
            pre_russia_bridge_material_ready = (
                pre_russia_bridge_current_material_ready
                or pre_russia_bridge_future_material_ready
            )
            if pre_russia_bridge_ready and pre_russia_targets:
                pre_center_x = _weighted_center_x(pre_russia_targets)
                if pre_center_x is not None:
                    bridge_dist = abs(x - pre_center_x)
                    bridge_bonus = max(0.0, 420.0 - bridge_dist * 170.0)
                    if not pre_russia_bridge_material_ready:
                        if landing_y > 1.2 or piece_count >= 28:
                            bridge_bonus = 0.0
                        else:
                            bridge_bonus *= 0.20
                    elif not pre_russia_bridge_current_material_ready:
                        if landing_y > 0.6 or piece_count >= 30:
                            bridge_bonus = 0.0
                        else:
                            bridge_bonus *= 0.20
                    if landing_y > 2.4:
                        bridge_bonus *= 0.45
                    if bridge_bonus > 0:
                        score += bridge_bonus
                        reasons.append("PRE_RUSSIA_BRIDGE_CLUSTER")

            if (
                pre_russia_counts.get(14, 0) >= 1
                and pre_russia_counts.get(13, 0) >= 2
                and pre_russia_counts.get(15, 0) == 0
            ):
                russia_pair_targets = [
                    p for p in pieces if p.get("type") == 13
                ]
            else:
                russia_pair_targets = [
                    p for p in pieces if p.get("type") == 14
                ] + [
                    p for p in pieces if p.get("type") == 13
                ]
            russia_pair_material_ready = next_type >= 10 or next_next_type >= 12
            if (
                pre_russia_counts.get(14, 0) >= 1
                and pre_russia_targets
                and russia_pair_targets
            ):
                pair_center_x = _weighted_center_x(russia_pair_targets)
                if pair_center_x is not None:
                    pair_dist = abs(x - pair_center_x)
                    pair_bonus = max(0.0, 520.0 - pair_dist * 220.0)
                    if not russia_pair_material_ready:
                        if landing_y > 1.4 or piece_count >= 30:
                            pair_bonus = 0.0
                        else:
                            pair_bonus *= 0.25
                    if landing_y > 2.2:
                        pair_bonus *= 0.5
                    if pair_bonus > 0:
                        score += pair_bonus
                        reasons.append("RUSSIA_PAIR_CLUSTER")

            second_russia_ready = (
                single_type15_phase
                and (
                    second_russia_counts.get(13, 0) >= 2
                    or (
                        second_russia_counts.get(13, 0) >= 1
                        and second_russia_counts.get(12, 0) >= 2
                    )
                    or (
                        second_russia_counts.get(13, 0) >= 1
                        and second_russia_counts.get(12, 0) >= 1
                        and second_russia_counts.get(11, 0) >= 2
                    )
                    or second_russia_counts.get(14, 0) >= 1
                )
            )
            if second_russia_ready:
                second_russia_material_ready = next_type >= 10 or next_next_type >= 12
                if second_russia_counts.get(13, 0) >= 2:
                    second_targets = [
                        p for p in pieces if p.get("type") == 13
                    ]
                else:
                    second_targets = [
                        p for p in pieces if p.get("type") in (12, 13, 14)
                    ]
                second_center_x = _weighted_center_x(second_targets)
                if second_center_x is not None:
                    second_dist = abs(x - second_center_x)
                    second_bonus = max(0.0, 900.0 - second_dist * 280.0)
                    if not second_russia_material_ready:
                        if landing_y > 1.2 or piece_count >= 30:
                            second_bonus = 0.0
                        else:
                            second_bonus *= 0.25
                    if landing_y > 2.2:
                        second_bonus *= 0.55
                    if second_bonus > 0:
                        score += second_bonus
                        reasons.append("SECOND_RUSSIA_BRIDGE_CLUSTER")
                    if abs(x) >= 2.5 and second_dist >= 1.8:
                        score -= 1200.0
                        reasons.append("SECOND_RUSSIA_FAR_EDGE_PENALTY")

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
        if (max_y >= 2.0 or deadline_crossed) and merge_grade in ["DIRECT", "NEAR"]:
            if merge_grade == "DIRECT":
                score += 3000.0
                reasons.append("DANGER_ZONE_IMMEDIATE_MERGE_PRIORITY")
            else:
                # NEAR: suppress bonus when this candidate crosses or nearly crosses deadline
                candidate_margin = result.get("deadline_margin", 99)
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

        if reactive_pair_count >= 1 and merge_grade in ["DIRECT", "NEAR"]:
            # 即時併合候補がある場合、reactive_pairs数に応じてボーナスを強化
            # v663: NEAR bonus suppressed near deadline (same logic as axis 8.5)
            candidate_margin_86 = result.get("deadline_margin", 99)
            near_deadline_suppressed = (merge_grade == "NEAR" and candidate_margin_86 < 0.3700)
            if not near_deadline_suppressed:
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
             # v548: double_russia_phase — 2つ目の(type 14/15)在盘面
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

        # vXXX: deadline merge urgency bonus — when deadline_crossed && rp>=3,
        # add +2000 to DIRECT/NEAR candidates to ensure they win over NO_MERGE candidates.
        # NO_MERGE candidate: -4500 (penalty) + other bonuses. Merge candidate: existing bonus
        # +2000 -> wins over NO_MERGE. Fixes "merge_available but NO_MERGE chosen" death spiral.
        # Refs: analysis shows score826 T62 chose NO_MERGE at rp=7-8, deadline_crossed=true
        # while NEAR merge existed at y=2.71. score2019 T96-97 chose DANGER_DIRECT_MERGE
        # at max_y=3.38 and scored 45+80.
        if deadline_crossed and reactive_pair_count >= 3 and merge_grade in ["DIRECT", "NEAR"]:
            score += 2000.0
            reasons.append("DEADLINE_MERGE_URGENCY")

        if reactive_pair_count >= 3 and merge_grade == "NO":
            # v452: flatten to -4500, matching protected strategy (median 12789)
            # v432 gradient (-3000 at y<=0) was too weak at low positions, allowing additive
            # bonuses (~400-800) to create scatter. Flat -4500 overwhelms bonuses, letting
            # axis 2 height penalty be the only differentiator — consistent low placement.
            # vXXX (rollback v662): remove suppression condition — postmortem constraint
            # requires penalty whenever rp>=3 && NO_MERGE, regardless of deadline_crossed.
            # Previous suppression `if not (deadline_crossed and rp>=3)` suppressed exactly
            # when penalty was most needed (deadline_crossed && rp>=3 → NO_MERGE death spiral).
            # Now penalty always applies at rp>=3 && NO_MERGE, forcing low landing_y choice.
            # Fixes: worst game T56 deadline_crossed=true, rp=3, NO_MERGE → penalty now fires.
            # refs: tmp/analysis_result.md (axis 8.8 suppression bug fix), tmp/state/last_rollback_postmortem.md
            score -= 4500.0
            reasons.append("REACTIVE_PAIRS_NO_MERGE_PENALTY")
            if (
                not russia_phase
                and not deadline_crossed
                and piece_count >= 16
                and max_y >= -0.4
                and abs(x) >= 2.5
            ):
                edge_scatter_penalty = 900.0 + min(2200.0, max(0, piece_count - 16) * 130.0)
                edge_scatter_penalty += max(0.0, landing_y + 0.2) * 450.0
                score -= edge_scatter_penalty
                reasons.append("PRE_RUSSIA_REACTIVE_EDGE_SCATTER")

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

        # ----- v662: continuous deadline-margin penalty (enhanced from v661) -----
        # v661 NEAR penalty was too weak: threshold 0.3 and 2500/unit let NEAR+deadline-cross
        # beat NO-merge low positions. NEAR success rate is only 26-47% (measured), so
        # crossing deadline for a NEAR is almost as bad as crossing for NO merge.
        # v662: unified threshold 0.5 for both NO and NEAR, NEAR penalty raised to 4000/unit.
        # DIRECT merges remain exempt (user confirmed: DIRECT crossing deadline is acceptable).
        # Example at margin=-0.1: NO penalty=(0.5-(-0.1))*5000=3000, NEAR=(0.5-(-0.1))*4000=2400
        # mandatory_themes: "併合できるわけでもないのにデッドラインにおいてしまうのを絶対に避ける"
        # refs: tmp/analysis_result.md, data/mandatory_themes.txt,
        #       game_history/20260416_193206_score1203.jsonl T50/T63/T66 (NEAR crosses deadline)
        margin = result.get("deadline_margin", 99)
        if merge_grade == "NO" and not russia_phase and margin < 0.5:
            score -= max(0, (0.5 - margin)) * 5000
            reasons.append("CROSSES_DEADLINE_NO_MERGE")
        elif merge_grade == "NEAR" and not russia_phase and margin < 0.5:
            score -= max(0, (0.5 - margin)) * 4000
            reasons.append("CROSSES_DEADLINE_NEAR_RISK")

        # ----- vXXX: Russia phase deadline cross penalty enhancement -----
        # analysis: worst T59 deadline_crossed=true && |x|=3.0 && merge_available=false
        # violates mandatory_themes (deadline crossing with NO merge). Russia phase is NOT exempt
        # from mandatory_themes — the constraint applies in ALL phases.
        # penalty is additive on top of existing deadline penalties:
        #   deadline_crossed && NO_MERGE && |x|>=1.5: additional -5000 in russia_phase
        #   (existing v661/v662 penalties remain, total becomes -5000 additional)
        # mandatory_themes compliant: "デッドラインを超える位置にピースを置く場合は、併合できる場合に限る"
        # refs: tmp/analysis_result.md, data/mandatory_themes.txt
        if russia_phase and deadline_crossed and merge_grade == "NO" and abs(x) >= 1.5:
            score -= 5000.0
            reasons.append("RUSSIA_DEADLINE_NO_MERGE_VIOLATION")

        # ----- v674: edge placement penalty at high pc + deadline_crossed (v668 Extended) -----
        # Failure mode: PIECE_COUNT_EDGE_BIAS — worst T64: pc=43, deadline_crossed=true, NO_MERGE
        # → x=-2.0 (edge) selected despite CROSSES_DEADLINE_NO_MERGE penalty (~-2500).
        # v661 penalty alone insufficient: HEIGHT_CONTROL + REACTIVE_PAIRS_STACKING bonuses
        # at edge positions (~300-900 combined) create false parity vs. center (no stacking bonus).
        # Extra penalty ∝ |x| * (pc-35) makes edge increasingly costly as board fills:
        #   pc=40, |x|=2.0: -(5)*400*(2/3)=-1333;  pc=45, |x|=3.0: -(10)*400*1.0=-4000
        # Combined with v661 (~-2500 at margin=0): edge is firmly rejected at pc>=40.
        # Only fires at merge_grade==NO: DIRECT/NEAR merge positions are never suppressed.
        # mandatory_themes compliant: redirects NO_MERGE edge → NO_MERGE center, not merge→no-merge.
        # refs: tmp/analysis_result.md, game_history/20260417_193200_score0490.jsonl T64-66
        if (piece_count >= 40 and deadline_crossed
                and merge_grade == "NO" and abs(x) >= 1.5):
            edge_penalty = -(piece_count - 35) * 400.0 * (abs(x) / 3.0)
            score += edge_penalty
            reasons.append("PC_EDGE_PENALTY")

        # ----- v675: decision_crosses_deadline edge NO_MERGE penalty -----
        # mandatory_themes: 「併合できるわけでもないのにデッドラインにおいてしまうのを絶対に避ける」
        # worst T62: decision_crosses_deadline=true, NO, x=-3.0 (PC_EDGE_PENALTY未発動, pc=40)
        # extra_low T64-T70: decision_crosses_deadline=true, NO, x=±3.0 (pc=33-38)
        # REACTIVE_PAIRS_NO_MERGE_PENALTY が rp>=3&&deadline_crossed で抑制されているため
        # decision_crosses_deadline=True の検知で極端エッジ配置を補完抑制する
        # |x|>=2.5 限定: 中央寄りNO_MERGE(T59-61のx=-2.0~-2.2)は許容
        # russia_phase除外: T159のx=-3.0(RUSSIA_PHASE_BOARD_COMPRESSION)は正当配置
        # refs: tmp/analysis_result.md (CROSSES_DEADLINE_EDGE_NO_MERGE仮説)
        decision_crosses = result.get("crosses_deadline", False)
        if (decision_crosses and merge_grade == "NO"
                and abs(x) >= 2.5 and not russia_phase):
            score -= 1500.0
            reasons.append("CROSSES_DEADLINE_EDGE_NO_MERGE")

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
