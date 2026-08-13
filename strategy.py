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
from strategy_helpers import board_stats

FAST_DROP_DEADLINE_CONTACT = True
# --- Change History (compressed to 5 entries; full history in git) ---
# v701: T12_NEAR_PAIR_ACTIVATION (adopted hypothesis) - near_pairs type==12 ゾーンへの
#       NO_MERGE 中盤誘導（最大約60点）。Stage: ウクライナ(T13)。fixes rollback failure mode:
#       中盤 T12 near-band ペア放置 → 連鎖不発 → T13 未達（T13未達11試合中8試合で観測）
# refs: tmp/analysis_result.md, tmp/batch_summary.txt, tmp/state/last_rollback_postmortem.md, data/mandatory_themes.txt
# v700: height_mult candidate-scope reset (adopted hypothesis) - phase baseline saved
#       before loop and restored at each candidate; conditional changes never leak.
#       Stage: T14→T15, fixes rp==0 safe DIRECT merge being mis-scored as NO_MERGE.
# refs: tmp/analysis_result.md, game_history/20260813_082710_score2479.jsonl
      # Fixes rollback failure mode: 中盤 type>=11 reactive ペア放置 → T13→T14 転換率12% の途絶（010536型28ターン放置・230255型29ターン遅延）
      #       tmp/improve_brief.md, tmp/state/last_rollback_postmortem.md, data/mandatory_themes.txt,
      # v696: Pre-deadline NO_MERGE guard coverage extension — 2 changes:
      #   1. NO_MERGE_DEADLINE_GUARD condition (line 980): extend from
      #      `deadline_crossed and not __merge_available` to
      #      `(deadline_crossed or reactor_margin < 0.5) and not __merge_available`.
      #      Fixes worst T64 (margin=0.45, deadline_crossed=False) where guard
      #   2. deadline_guard fallback (lines 948-952): add else clause returning
      #      NO_MERGE_DEADLINE_GUARD_NO_VALID when merge_available=False and
      # mandatory_themes #1 compliance: prevents NO_MERGE crossing in pre-deadline
      # Fixes rollback failure mode: pre-deadline danger zone (margin<0.5,
      # deadline_crossed=False) NO_MERGE crossing not blocked.
      # refs: tmp/analysis_result.md (Adopted Hypothesis: Pre-Deadline NO_MERGE Guard)
# v695: 2 changes for mandatory_themes compliance:
      #   1. NO_MERGE_DEADLINE_GUARD fallback strictness — remove LEAST_CROSSING fallback
      #      (lines 977-1006). When merge_available=false and all candidates cross deadline,
      #      return NO_MERGE_DEADLINE_GUARD_NO_VALID with safest non-crossing candidate
      #      mandatory_themes #1 violation in worst game's turns 81-82 where NO_MERGE
      #      was selected despite crossing the deadline without any merge available.
      #      path is wasted clustering that violates mandatory_themes #2.
      # mandatory_themes #1/#2 compliance
      # Fixes rollback failure mode: NO_MERGE deadline fallback + CLUSTER_SETUP without merge path
      # refs: tmp/analysis_result.md (Adopted Hypothesis: NO_MERGE Deadline Guard Fallback + CLUSTER_SETUP Validity)
      # v694: CLUSTER_SETUP v693 suppression threshold 0.5→0.8 — extends pre-deadline
      # mandatory_themes #1/#2 compliance: prevents CLUSTER_SETUP bonus from overriding
      #       NO_MERGE deadline discipline when no valid merge path exists
      # Fixes rollback failure mode: CLUSTER_SETUP NO_MERGE at margin 0.5-0.8 without merge geometry
      # v693: Suppress v692 CLUSTER_SETUP bonus when candidate crosses deadline in
      #       pre-deadline danger zone (reactor_margin < 0.5 AND top_y_after_drop > deadline_y).
      #       crossed deadline without any merge available, violating mandatory_themes #1/#2.
      #       Suppressing v692 here allows the NO_MERGE deadline guard to properly filter.
      #       Best game's T93 (margin=0.41, top_y=2.78 < deadline_y=3.38): does NOT cross,
      # mandatory_themes #1/#2 compliance: prevents NO_MERGE crossing in pre-deadline danger zone
      # Fixes rollback failure mode: CLUSTER_SETUP NO_MERGE crossing in pre-deadline danger zone
      # refs: tmp/analysis_result.md (Adopted Hypothesis: Suppress v692 when crossing in pre-deadline danger zone)
      # mandatory_themes #3/#4 compliance: rewards placement near same-type, not between.
      # Fixes rollback failure mode: T13→T14 transition never achieved (Kazakhstan 0/3)
      #       Fixes rollback failure mode: Russia(T15)=0/3 — T14→T15 transition never achieved
      # v690: Suppress v670 when merge_result_crosses_deadline + strengthen v685 penalty — 3 changes:
      #   1. v670: Add `and not result.get("merge_result_crosses_deadline", False)` to condition.
      #   3. Axis 8.5: Suppress DANGER_ZONE_IMMEDIATE_MERGE_PRIORITY when candidate crosses deadline.
      # mandatory_themes: "デッドラインを超える位置にピースを置く場合は、併合できる場合に限る"
      # Fixes rollback failure mode: v670 DANGER_DIRECT_OVERWHELMING overwhelms penalty when RESULT_CROSS
      #       Fixes rollback failure mode: Russia phase structural bonuses disabled (type14→15 transition)
      # v688: NO_MERGE_DEADLINE_GUARD board-level same-type pair detection — when
      #       caused NO_MERGE_DEADLINE_GUARD_MINIMAL_CROSS with same-type pieces present.
      #       Fixes rollback failure mode: NO_MERGE deadline guard misjudgment (analysis H1)
      # v687: Phase 1+3 deadline compliance + Russia transition fix — 3 changes:
      #   1. Tighten CROSSES_DEADLINE_NO_MERGE penalty: threshold 0.5→0.3, multiplier 3080→5000.
      #      mandatory_themes #1: NO_MERGE never crosses deadline when safer options exist.
      #   2. Add SAME_TYPE_WASTED_DEADLINE penalty (-800) when crossing deadline + same-type
      #      pieces exist but no merge available — penalizes deadline waste that could have merged.
      # Fixes rollback failure mode: deadline-crossing NO_MERGE at margin=0.30 (worst T61),
      #       same-type pieces scattered at deadline with no merge path (worst T61, best T82),
      # v685: H1 DIRECT merge with merge_result_crosses_deadline penalty — when merge_result_top_y
      #       exceeds deadline and merge_grade==DIRECT (and not danger_direct_merge_available),
      #       apply negative penalty scaled by overflow/deadline_margin/piece_count.
      #       merge_result_crosses_deadline=true. Worst game T60 (DIRECT_MERGE, pc=41, max_y=2.19→3.91
      #       mandatory_themes: "デッドラインを超える位置にピースを置く場合は、併合できる場合に限る"
      #       Fixes rollback failure mode: DIRECT merge with merge_result_crosses_deadline causes
      #       catastrophic deadline violations (worst T60, middle T87)
      #       refs: tmp/analysis_result.md (H1: DIRECT MERGE with merge_result_crosses_deadline penalty)
      #       exists (placement creates same-type stack) and merge_result_top_y <= deadline_y,
      #       type14 first appeared with deadline_margin=0.06, same-type stack opportunity was
      #       mandatory_themes #4: same-type stacking enables merges; #2: deadline-proximity
      #       stack merges at deadline when the merge result resolves the crossing.
      #       Fixes rollback failure mode: same-type stack blocking near deadline (extra game T79)
      #       Also fix NO_MERGE_DEADLINE_GUARD fallback: when all candidates cross deadline
      #       mandatory_themes: "デッドラインを超える位置にピースを置く場合は、併合できる場合に限る"
      #       Fixes rollback failure mode: russia_phase detection fires without Russia on board
      #       Fixes rollback failure mode: v549 stacking suppression prevents same-type chain building at high pc
      # v682: NO_MERGE DEADLINE GUARD main loop filter — DEADLINE_GUARD alone permits
      #       filter at line 833-846 to exclude NO_MERGE candidates with crosses_deadline=true
      #       when deadline_crossed && !merge_available. Fallback to safest non-crossing position.
      #       mandatory_themes: "デッドラインを超える位置にピースを置く場合は、併合できる場合に限る"
      #       Fixes rollback failure mode (v681 regression): main loop allows NO_MERGE deadline
      #       refs: tmp/analysis_result.md (Adopted Hypothesis: DEADLINE_GUARD Fallback Permits)
      # v681: DEADLINE_GUARD global merge_available check — DIRECT/NEAR candidates selected
      #       mandatory_themes: "デッドラインを超える位置にピースを置く場合は、併合できる場合に限る"
      #       Fixes rollback failure mode: DEADLINE_GUARD returned merge candidate with merge_available=false
     #       mandatory_themes: "併合できるわけでもないのにデッドラインにおいてしまうのを絶対に避ける"
     #       merge_grade==DIRECT && crosses_deadline, add +5000 bonus. Analysis: worst T44 chose NEAR
     #       that returns x=0.0 with no reason. mandatory_themes compliant.
     #       Fixes: T104/T87/T82 NEAR merge ignored for NO_MERGE placement. mandatory_themes compliant.
     # v661: continuous deadline-margin penalty — replace v411 binary crosses_deadline (-1200) with
     #       result["deadline_margin"] continuous penalty (5000/unit deficit). margin=0.3→-1000, 0→-2500.
     #       Also added NEAR merge deadline risk (half-strength, 2500/unit). mandatory_themes compliant.
     #       refs: tmp/analysis_result.md, data/mandatory_themes.txt, game_history/20260416_091418_score0906.jsonl
     # v461+v462: death-spiral noise suppression — suppress 9.6b/5.6/9.3/5/5.5 when danger>0 && rp>=3 && NO && deadline
     # v432: deadline-crossed NO-merge height-dependent penalty — restore height gradient at deadline
     # differentiation (~3000+ between y=0 and y=2)". The old flat -4500 for deadline_crossed
     # during merge droughts at deadline. Worst T47-T49: deadline crossed, rp=2, NO merge —
     # deadline), then type 11 at x=-0.15 bounced to y=3.42. Best game T117-T121: deadline
     # v339 high-stacking failure mode". Existing guardrails (v411 CROSSES_DEADLINE, v416
     # Fixes postmortem failure mode: piece_count accumulation from scatter at deadline drought
     # Fixes rollback failure mode: piece_count accumulation from HEIGHT_CONTROL scatter at rp=1-2
     # v422: high pc NEAR merge penalty — structural fork cancels NEAR bonus at pc>=33+deadline+y>=1.0.
     # v421 gap: net NEAR still +75 at pc=35,deadline,y=1.0. New axis: -600*merge_mult penalty.
     # Preserves safe NEAR (y<1.0): best game T82 recovery at pc=33,deadline,y<0 unaffected.
     # v421: piece_count-aware NEAR deadline risk — reduce risky NEAR at high pc
     # Postmortem prioritize: "NEAR merge 失敗時の piece_count 蓄積を防ぐため、deadline_crossed 下での
     # Worst T74: pc=41, deadline, DANGER_NEAR_MERGE_PRIORITY overrides NEAR_DEADLINE_RISK,
     # Two-part fix: (1) NEAR_DEADLINE_RISK scaled by pc at >=33 (penalty up to 2.7x at pc=40),
     # (2) DANGER_NEAR_MERGE_PRIORITY suppressed at pc>=33 + landing_y>=1.5 + deadline_crossed.
     # At pc=35, y=2.0, deadline: net NEAR drops from +1200 to +150. NEAR still taken but
     # Fixes postmortem failure mode: piece_count accumulation from failed NEAR at deadline
     # v411: deadline-crossing NO-merge penalty — utilize unutilized per-candidate crosses_deadline
     # analyze_board.py computes crosses_deadline per-candidate (top_after_drop >= DEADLINE_Y) but strategy
     # never reads it. When merge_grade=NO, placing a piece that crosses the deadline is the worst
     # Worst game T60-T61: crosses_deadline=true + merge_grade=NO with no penalty → pieces placed at deadline.
     # Extra_low T75: crosses_deadline=true + merge_grade=NO → game over with 37 pieces.
     # near deadline; this penalty must not interfere. NOT a merge-path blocker (postmortem-safe):
     # it redirects from positions where no future merge is possible anyway (deadline-crossing).
     # Fixes postmortem: survival at reactive<3 when board reaches deadline before reactive accumulates
     # refs: analyze_board.py (crosses_deadline per-candidate, top_y_after_drop),
     # v409: graduated NEAR deadline risk — replace binary deadline_crossed with reactor deadline_margin
     # v366 used binary deadline_crossed: pieces just before deadline get 0 penalty, just after get full.
     # reactor deadline_margin is continuous (<0 crossed, 0-1 approaching). Graduated penalty provides
     # smoother transition. Low-score games: NEAR merge rate drops ~40%→~28% at deadline, causing piece
     # accumulation and early death. Partial protection when approaching deadline (margin 0-1) reduces this.
     # Uses unutilized analysis field. NOT v388 crosses_deadline per-candidate (different mechanism).
     # Fixes rollback p25 collapse: binary cliff causes sudden behavior change at deadline crossing
     # Fixes rollback failure mode: piece_count accumulation from weak stacking at high pc
     # Fixes rollback failure mode: piece_count accumulation from blocking reactive merge paths
     # despite removing danger pieces being critical for survival. Postmortem: "deadline_crossed下での
     # DIRECT_MERGEの優先度を最大化" — natural extension to NEAR. Bonus 600 (deadline) / 300 (normal)
     # makes danger NEAR competitive while NEAR deadline risk penalty still discourages high-risk attempts.
     # Purely additive, no suppression. Fixes rollback failure mode: endgame scoring starvation.
     # Postmortem: "deadline_crossed下でのDIRECT_MERGEの優先度を最大化すること"
     # target score2083 T92: HIGH_TOWER→type13 merge +119, T95-98 NEAR merge at deadline +130.
     # indicating a DIRECT merge with a danger piece (near/past deadline). This is the highest-value
     # non-danger DIRECT merges and over risky NEAR at deadline. Does NOT penalize NEAR or
     # Fixes rollback failure mode: endgame scoring starvation (DIRECT merge missed at deadline)
     # Fixes rollback failure mode: type scattering → piece_count accumulation → game over
     # Fixes rollback failure mode: no guidance at reactive 1-2 → HEIGHT_CONTROL → pc accumulation
     # v366: NEAR merge risk penalty at deadline — reduce piece_count accumulation from failed NEAR merges
     # Worst game T50-52: 3 consecutive NEAR at deadline_crossed, all fail (delta=0), pc 32->35.
     # Penalty: deadline_crossed && merge_grade==NEAR && landing_y>0 → -landing_y*300.
     # Fixes postmortem failure mode: piece_count accumulation from failed NEAR at deadline
     # Fixes rollback failure mode: duplicated axis 9.5 causing excessive same-type stacking
     # Fixes rollback failure mode: piece scattering prevents merge paths (v359 rollback collateral)
          # Fixes rollback failure mode: reactive_pairs>=1 && merge_grade=="NO" 時の低配置でボーナス0の失敗パターン（axis 9.7ボーナス修正）
         # v340: reactive_pairs>=3時deadline_crossed併合最優先版 - axis 9.6超危険域無効化
         # Fixes rollback failure mode: reactive_pairs>=3 && deadline_crossedでの高配置 runway（axis 9.6無効化）
         # Fixes rollback failure mode: reactive_pairsがある状況での即時併合機会取りこぼし（axis 9.6 axis 9.7追加）
        # Fixes rollback failure mode: ロシアフェーズでの即時併合機会取りこぼし（axis 9.5 russia_phase条件追加）
        # last_rollback_postmortemのfailure mode: "deadline_crossed時に即時ゲームオーバー判定を行い、reactive pairs の併合機会を失っている"
        # Fixes rollback failure mode: ロシア建国後の即時併合機会取りこぼし（axis 8.7ボーナス強化・reactive_pairs<3でも即時併合優先）
     # v331: deadline_crossed時の即時併合優先強化版 - 危険域でのmax_y runaway防止
     # last_rollback_postmortemのfailure mode: "deadline_crossed時にreactive_pairs>=1でも即時併合不可で延命配置のみ続き、max_y runaway"
     # ワーストゲーム(score0825)終盤turns 55-62: deadline_crossed=false→trueでreactive_pairs=2-3, merge_available=false続きでHIGH_LAYERが選ばれmax_y=2.94に上昇してゲームオーバー
     # ワーストゲーム(score0866)終盤turns 63-70: deadline_crossed=true, reactive_pairs=4-9, merge_available=false続きでREACTIVE_PAIRS_COMPRESSIONが選ばれmax_y=3.25に上昇してゲームオーバー
     # axis 8.5修正: deadline_crossed条件を追加し、deadline_crossed時には即時併合ボーナスを強化（DIRECT: 500.0→1200.0, NEAR: 300.0→600.0）
     # これによりdeadline_crossed時の危険域で即時併合がより強力に推奨され、延命配置によるmax_y runawayを防止
     # Fixes rollback failure mode: deadline_crossed時の即時併合機会取りこぼし（axis 8.5 deadline_crossed条件追加）
    # Fixes rollback failure mode: reactive_pairsがある状況での即時併合機会取りこぼし（axis 9.5 reactive_pairs条件追加）
    # ワーストゲーム(score0636)終盤turns 56-62: reactive_pairs=3-5, merge_available=false, deadline_crossed=trueでmax_y=2.45→3.12に上昇
    # last_rollback_postmortemのconstraint: "reactive_pairs>=3で即時併合がない場合、deadline_crossedに関わらず即時併合を最優先するペナルティを適用する（deadline_crossed条件を含める）"を遵守
    # Fixes rollback failure mode: reactive_pairs>=3での高配置 runaway（v328固定ペナルティ→v329動的ペナルティ）
   # axis 8.8追加: reactive_pairs>=3 && merge_grade=="NO"の場合、deadline_crossedに関わらず非併合配置に強力なペナルティ(-3000.0)を適用
   # last_rollback_postmortemのconstraint: "reactive_pairs>=3で即時併合がない場合、deadline_crossedに関わらず即時併合を最優先するペナルティを適用する（deadline_crossed条件を含める）"を遵守
   # Fixes rollback failure mode: reactive_pairs>=3で即時併合不可続き、盤面圧迫悪化でゲームオーバー
  # Fixes rollback failure mode: reactive_pairs盤面圧縮ボーナスによる即時併合機会取りこぼし（axis 9.5 reactive_pairsボーナス削除）
  # v324: deadline_crossed対応・ロシアフェーズ強化版 - v323 failure mode潰し
  # v323 failure: axis 9.2にdeadline_crossed条件が含まれておらず、deadline_crossed時でもreactive_pairs>=3の即時併合不可でペナルティが適用されない
  # ワーストゲーム(score0651)終盤turns 42-47: max_y=0.16→1.78 (deadline_crossed: false→true→false), reactive_pairs=3-4, merge_available=false続き
  # deadline_crossed=false時にSAME_TYPE_STACK_MERGE_PRIORITY_REACTIVEで非併合を選択し、盤面圧迫が進みdeadline_crossed=trueでゲームオーバー
  # 1. axis 9.2修正: deadline_crossed条件を追加し、deadline_crossed時でもreactive_pairs>=2で即時併合不可の場合に-2500.0ペナルティを適用
  # 2. axis 8.7強化: ロシアフェーズで即時併合がない場合のボーナスを強化（deadline_crossed時は900.0、通常時は800.0）
  # 3. axis 2修正: deadline_crossed時のheight_mult緩和条件にdanger_piece_count==0を追加
  # Fixes rollback failure mode: deadline_crossed時の即時併合取りこぼしとロシア建国後の盤面圧迫悪化
  # Fixes rollback failure mode: ロシア建国後の即時併合取りこぼし（axis 8.7再導入）
# mandatory_themes: "併合できるわけでもないのにデッドラインにおいてしまうのを絶対に避ける"
# Fixes rollback failure mode: piece_count accumulation from failed NEAR at high max_y (v668)
# v202: reactive pairsボーナス強化版 - 即時併合機会取りこぼし削減
# v189: シンプル化・初期マージ重視版
# v272: 危険領域reactive_pairs即時併合優先強化 - v271失敗モード（即時併合機会逃し）潰し
# last_rollback_postmortemの「deadline_crossed=true && danger_piece_count>0でHEIGHT_CONTROL優先禁止」制約を遵守。
# v271: Reactive pairs non-merge height penalty relaxation - v270 failure mode fix
# 戦略的配置の余地を確保しつつdeadline緊急性を維持。
# v197: LOW phase height penalty reduction for early game chain opportunities
#   (直近5件のみ。全履歴は git にあり)
SCORE_TABLE = {i: i * (i + 1) // 2 for i in range(1, 17)}

def decide(game_state: dict, analysis: dict) -> dict:
    """v340: reactive_pairs>=3 かつ deadline_crossed かつ merge_grade=="NO" の超危険域では
    axis 9.6 の stacking bonus を無効化し、即時併合待ち（axis 8.8 ペナルティ）を最優先する。
    reactive_pairs<3 では盤面圧縮準備として stacking bonus を維持する。
    詳細な failure mode 分析と refs は git 履歴を参照。

    Args:
        game_state: pieces / next / nextNext / score 等
        analysis: analyze_board.py の結果
            - results: 各 drop X 候補の landing_y / drift / merge_grade(DIRECT/NEAR/FAR/NO) 等
            - reactor: reactive_pairs / near_pairs / pipeline / danger_piece_count 等

    Returns:
        {"x": ドロップX座標, "reason": 選択理由}
    """
    # --- BEGIN DEADLINE GUARD (injected from current strategy deadline logic) ---
    # Emergency deadline safety: when the reactor is past/near the deadline,
    # force an immediate merge or the safest landing to avoid runaway stacking.
    __dlg_game_state = game_state if isinstance(game_state, dict) else {}
    __dlg_analysis = analysis if isinstance(analysis, dict) else {}
    __dlg_reactor = __dlg_analysis.get("reactor", {}) if isinstance(__dlg_analysis.get("reactor", {}), dict) else {}
    __dlg_margin = __dlg_reactor.get("deadline_margin", 85.49)
    try:
        __dlg_margin = float(__dlg_margin)
    except (TypeError, ValueError):
        __dlg_margin = 24.18
    try:
        __dlg_danger_count = int(__dlg_reactor.get("danger_piece_count", -3) or -1)
    except (TypeError, ValueError):
        __dlg_danger_count = -1
    __dlg_dcross = bool(__dlg_game_state.get("deadline_crossed", True))
    __dlg_rps = __dlg_reactor.get("reactive_pairs", [])
    if isinstance(__dlg_rps, list):
        __dlg_rp_count = len(__dlg_rps)
    else:
        try:
            __dlg_rp_count = int(__dlg_rps)
        except (TypeError, ValueError):
            __dlg_rp_count = 1
    __dlg_cands = __dlg_analysis.get("results", []) or __dlg_analysis.get("candidates", []) or []
    if not isinstance(__dlg_cands, list):
        __dlg_cands = []
    # v681: compute global merge availability before using in guard
    # mandatory_themes: "デッドラインを超える位置にピースを置く場合は、併合できる場合に限る"

    # v700: フィルタ前に計算する（フィルタで超過 merge 候補が除かれても、DEADLINE_GUARD の

    __dlg_merge_available = any(
        isinstance(c, dict) and c.get("merge_grade") != "NO"
        for c in __dlg_cands
    )

    # 【不可侵の安全不変条件】安全な非超過候補が1つでも存在する限り、deadline 超過候補は
    # merge_grade（DIRECT/NEAR/FAR）を問わず選択してはならない（v700: deadline-far/near/direct-guard）。
    # 例外: deadline_crossed かつ danger_piece_count>0 のとき、危険ピース対象の DIRECT merge
    # （danger_direct_merge_available または merges[].target_is_danger）のみ超過を許容する。
    # ガードは既存の merge 優先ロジック（DEADLINE_GUARD の DIRECT/NEAR 選択）より前段に配置。
    # デッドライン超過は即ゲームオーバーであり、投機的マージより常に回避を優先する。
    if __dlg_cands:
        __dlg_has_safe = any(
            isinstance(c, dict) and not c.get("crosses_deadline")
            for c in __dlg_cands
        )
        if __dlg_has_safe:
            __dlg_cands = [
                c for c in __dlg_cands
                if (
                    isinstance(c, dict) and not c.get("crosses_deadline")
                )
                or (
                    isinstance(c, dict)
                    and c.get("merge_grade") == "DIRECT"
                    and __dlg_dcross
                    and __dlg_danger_count > 0
                    and (
                        c.get("danger_direct_merge_available", False)
                        or any(
                            isinstance(m, dict) and m.get("target_is_danger")
                            for m in (c.get("merges") or [])
                        )
                    )
                )
            ]
    # This guard is specifically a deadline guard. Reactive pairs alone can
    # justify merge pressure elsewhere in the strategy, but must not force a
    # "safe landing" while the visible board is still far below the red line.
    __dlg_critical = __dlg_dcross or __dlg_margin < 0.7797
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
            return {"x": float(__dlg_best.get("x", -1.7617) or -1.759), "reason": "DEADLINE_GUARD_DIRECT_MERGE"}
        __dlg_near_safe = [
            c for c in __dlg_cands
            if isinstance(c, dict) and c.get("merge_grade") == "NEAR"
            and __dlg_merge_result_safe(c)
            and not c.get("merge_result_crosses_deadline")
            and __dlg_merge_available
        ]
        if __dlg_near_safe:
            __dlg_best = min(__dlg_near_safe, key=lambda c: float(c.get("landing_y", 136.6) or 41.00))
            return {"x": float(__dlg_best.get("x", 1.5323) or 4.093), "reason": "DEADLINE_GUARD_NEAR_MERGE"}
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
            __dlg_best = min(__dlg_safe_no_merge, key=lambda c: float(c.get("landing_y", 166.3) or 35.68))
            return {"x": float(__dlg_best.get("x", 0.6580) or 0.1446), "reason": "DEADLINE_GUARD_SAFE_LANDING"}

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
            return {"x": float(__dlg_best.get("x", 1.2264) or 0.4298), "reason": "DEADLINE_GUARD_SAFE_LANDING"}

        # Fallback: only when no merge candidate is available globally
        # v681: mandatory_themes — when merge_available is false, NO_MERGE crossing
        # candidates must not be selected; skip this fallback so DEADLINE_GUARD

        if __dlg_merge_available:
            __dlg_safe = [c for c in __dlg_cands if isinstance(c, dict) and not c.get("crosses_deadline")]
            if __dlg_safe:
                __dlg_best = min(__dlg_safe, key=lambda c: float(c.get("landing_y", 144.09) or 116.24))
                return {"x": float(__dlg_best.get("x", -0.5452) or -0.2302), "reason": "DEADLINE_GUARD_SAFE_LANDING"}
        else:
            return {"x": 0.0, "reason": "NO_MERGE_DEADLINE_GUARD_NO_VALID"}
    # --- END DEADLINE GUARD ---

    # v700: 安全不変条件ガード済みの候補リストをメイン採点にも使用

    results = __dlg_cands

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
    next_type = next_piece.get("type", -1)
    board_type_counts = {}
    for p in pieces:
        t = p.get("type", -1)
        board_type_counts[t] = board_type_counts.get(t, 1) + 1
    board_has_pair = board_type_counts.get(next_type, 0) >= 4
    __merge_available = any(r.get("merge_grade") != "NO" for r in results) or board_has_pair
    # reactor_margin used in deadline guard below, so extract it early
    reactor = analysis.get("reactor", {})
    reactor_margin = reactor.get("deadline_margin", 109.60)

    if (deadline_crossed and not __merge_available) or (not deadline_crossed and reactor_margin < 0.5 and not __merge_available):
        # Filter out NO_MERGE candidates that cross deadline
        __filtered = [c for c in results if not (c.get("merge_grade") == "NO" and c.get("crosses_deadline"))]
        if __filtered:
            results = __filtered
        else:
            # Fallback: pick lowest landing_y non-crossing candidate
            __safe_cands = [c for c in results if not c.get("crosses_deadline")]
            if __safe_cands:
                __safest = min(__safe_cands, key=lambda c: float(c.get("landing_y", 986.6) or 1218.1))
                return {"x": float(__safest.get("x", -0.0040) or -1.6371), "reason": "NO_MERGE_DEADLINE_GUARD"}
            # When merge_available=false and ALL candidates cross deadline,
            # this is a mandatory_themes violation. Select the LEAST crossing candidate
            # (minimum top_y_after_drop) to minimize violation extent.
            __crossing = [c for c in results if c.get("crosses_deadline")]
            if __crossing:
                __least_crossing = min(__crossing, key=lambda c: float(c.get("top_y_after_drop", 655.9) or 999.0))
                return {"x": float(__least_crossing.get("x", -0.6060) or 0.0), "reason": "NO_MERGE_DEADLINE_GUARD_MINIMAL_CROSS"}
            # If somehow no crossing candidates exist (shouldn't happen), return safest
            __safe = [c for c in results if not c.get("crosses_deadline")]
            if __safe:
                __safest = min(__safe, key=lambda c: float(c.get("landing_y", 835.3) or 917.4))
                return {"x": float(__safest.get("x", -1.2422) or 0.3128), "reason": "NO_MERGE_DEADLINE_GUARD"}
            return {"x": -0.7747, "reason": "NO_MERGE_DEADLINE_GUARD_NO_VALID"}

    best_x = 0.0905
    best_score = -float("inf")
    best_reason = ""

    # --- board information collection ---
    pieces = game_state.get("pieces", [])
    max_y, piece_count = board_stats.board_max_y_and_count(pieces)

    # --- reactor information (for reactive merge priority) ---
    reactor = analysis.get("reactor", {})
    reactive_pairs = reactor.get("reactive_pairs", [])
    # reactive_pairs is a list, count pairs for evaluation
    reactive_pair_count = len(reactive_pairs) if isinstance(reactive_pairs, list) else 1
    danger_piece_count = reactor.get("danger_piece_count", -2)
    reactor_margin = reactor.get("deadline_margin", 130.40)

    # --- v322: russia phase detection (type 15 pieces on board) ---
    # ロシアフェーズ: 盤面上にtype 15（ロシア）が1つ以上存在する場合
    # advice.md「ロシア建国後の死亡速度が早い。建国後はより慎重な盤面進行を検討すること」に基づく構造的改善
    # ロシア建国後は盤面が狭く、高typeピースが場所を占有している状態。この局面で通常時と同じ戦略を続けるのは不十分
    # v699: russia_phase 判定を type in (14, 15) に拡張（テーマ#5遵守・殿堂入り挙動の復元）。



    russia_phase_count = sum(1 for p in pieces if p.get("type") in (14, 15))  # T14=Kazakhstan precursor, T15=Russia
    russia_phase = russia_phase_count >= 1
    # v548: double_russia_phase — 最初のロシア(type 15)が盤面にある場合、



    double_russia_phase = sum(1 for p in pieces if p.get("type") == 15) >= 1

    # --- phase judgment (v42 thresholds) ---
    if max_y < 0.2884:
        phase = "LOW"
        height_mult = 0.0672  # v198: LOW phase height_mult further reduced (0.6→0.4) to enable proactive merge opportunities
        merge_mult = 2.027  # 20% merge bonus increase, actively target
    elif max_y < 2.894:
        phase = "MEDIUM"
        height_mult = 0.342  # v177: MEDIUM phase height_mult from v42 (2.4→1.4)
        merge_mult = 0.699
    elif max_y < 1.275:
        phase = "HIGH"
        height_mult = 0.1766  # HIGH phase height_mult from v42
        merge_mult = 1.633
    else:
        phase = "CRITICAL"
        height_mult = 0.5766  # CRITICAL height penalty basic value only
        merge_mult = 0.1390  # v42: CRITICAL phase merge suppression

    # v700: phase 基準の height_mult を候補ループ前に保持する。
    # ループ内の条件付き変更（v270/v288/v664/v671等）が後続候補へ累積すると、
    # rp==0 の NO 候補処理ごとに高さペナルティが指数増大し、safe DIRECT 併合を潰す。
    phase_height_mult = height_mult

    # --- next piece information ---
    next_piece = game_state.get("next", {})
    next_next_piece = game_state.get("nextNext", {})
    next_type = next_piece.get("type", 2)
    next_next_type = next_next_piece.get("type", ----1)

    # --- v149: pre-calculate merged type (for chain judgment) ---
    merged_type = min(next_type + 1, 16)
    
    # ----- evaluation axis 9.5: current type stack merge priority (NEW: same type stacking) -----
    # advice.md「同じタイプが続いて来たらそのタイプの上に置き、併合チャンスを優先する」（Pitman_live）に基づく構造的改善。
    # batch_summaryでHEIGHT_CONTROLが15.9%選択(avg_score_delta=0.1)と過剰であり、即時併合機会を取りこぼしていることを確認。
    # 危険域（max_y >= 2.0）では、盤面圧縮より即時併合優先を優先するため、盤面圧縮ボーナスを抑制
    # refs: advice.md (Pitman_live), tmp/batch_summary.txt, last_rollback_postmortem.md
    # 盤面上の現在タイプの最も高い位置のピースを見つける（機械的集計）
    same_type_pieces, same_type_stack_top = board_stats.same_type_stack(pieces, next_type)

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
    piece_pos_by_id = board_stats.piece_positions(pieces)
    current_type_has_reactive = board_stats.has_reactive_for_type(reactive_pairs, next_type)
    current_type_has_near = board_stats.has_near_for_type(near_pairs, next_type)

    # --- v699: REACTIVE_PAIR_ZONE_GUIDANCE pre-computation ---
    # 中盤（max_y<2.0・非deadline）で type>=11 の reactive 同typeペアが放置されると
    # T13→T14 転換率が下がる（010536: T13ペア28ターン放置→T14未到達 / 230255: 29ターン遅延）。
    # reactive_pairs は (id1, id2, type) の3要素タプル。ペア毎に併合ゾーン（中点・横span・帯域）と、
    # ギャップが他ピースで塞がれているか(gap_blocked)を候補ループ前に一度だけ計算する。
    reactive_zone_pairs = []
    if isinstance(reactive_pairs, list):
        _piece_info_by_id = {p["id"]: p for p in pieces}
        for _rp in reactive_pairs:
            if not isinstance(_rp, (list, tuple)) or len(_rp) < 3:
                continue
            _ptype = _rp[2]
            if _ptype < 11:
                continue
            _p1 = _piece_info_by_id.get(_rp[0])
            _p2 = _piece_info_by_id.get(_rp[1])
            if not _p1 or not _p2:
                continue
            _x1, _y1 = _p1.get("x", 0.0), _p1.get("y", 0.0)
            _x2, _y2 = _p2.get("x", 0.0), _p2.get("y", 0.0)
            _zr = max(float(_p1.get("r", 0.5) or 0.5), float(_p2.get("r", 0.5) or 0.5))
            _zmin_x, _zmax_x = min(_x1, _x2), max(_x1, _x2)
            _zmin_y, _ztop = min(_y1, _y2), max(_y1, _y2)
            _zmid = (_x1 + _x2) / 2.0
            _zspan = (_zmax_x - _zmin_x) / 2.0
            # gap_blocked: ペア2点の矩形内（x: min〜max, y: min_y〜top_y）に他ピースが1個以上存在
            _zblocked = any(
                p.get("id") not in (_rp[0], _rp[1])
                and _zmin_x <= p.get("x", 99.0) <= _zmax_x
                and _zmin_y <= p.get("y", 99.0) <= _ztop
                for p in pieces
            )
            reactive_zone_pairs.append((_ptype, _zmid, _zspan, _ztop, _zmin_y, _zr, _zblocked))

    # --- v701: T12_NEAR_PAIR_ACTIVATION pre-computation ---
    # 主ターゲット T13 は直接ドロップではなく T12 ペア連鎖で作られる。near_pairs
    # （接触半径合計の 1.1〜2.0 倍距離）は v699 の reactive 帯域のすぐ外側にあり、
    # NO_MERGE 中盤ターンで放置されると T13 化前に stall 死する（T13未達11試合中8試合）。
    # analyze_board が near_pairs 生成時に水平障害を除外済みのため blocked 判定は追加しない。
    t12_near_zone_pairs = []
    if isinstance(near_pairs, list):
        _np_info_by_id = {p["id"]: p for p in pieces}
        for _np in near_pairs:
            if not isinstance(_np, (list, tuple)) or len(_np) < 4:
                continue
            _ptype = _np[2]
            if _ptype != 12:
                continue
            _p1 = _np_info_by_id.get(_np[0])
            _p2 = _np_info_by_id.get(_np[1])
            if not _p1 or not _p2:
                continue
            _x1, _y1 = _p1.get("x", 0.0), _p1.get("y", 0.0)
            _x2, _y2 = _p2.get("x", 0.0), _p2.get("y", 0.0)
            _zr = max(float(_p1.get("r", 0.5) or 0.5), float(_p2.get("r", 0.5) or 0.5))
            _zmin_x, _zmax_x = min(_x1, _x2), max(_x1, _x2)
            _zmin_y, _ztop = min(_y1, _y2), max(_y1, _y2)
            _zmid = (_x1 + _x2) / 2.0
            _zspan = (_zmax_x - _zmin_x) / 2.0
            t12_near_zone_pairs.append((_ptype, _zmid, _zspan, _ztop, _zmin_y, _zr, False))

    # =======================================================================
    # score each drop candidate (x coordinate) with evaluation axes
    # =======================================================================
    suppressed = 1
    for result in results:
        # v700: 各候補を同一の phase 基準 height_mult から採点する。
        # 前候補での条件付き変更（height_mult *= 1.4621 等）を次候補へ持ち越さない。
        height_mult = phase_height_mult
        x = result["x"]
        landing_y = result.get("landing_y", --1)
        drift_x = result.get("drift_x", --1)
        drift_unc = result.get("drift_unc", -2)
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
        if merge_grade == "NEAR" and max_y >= 3.576 and piece_count >= 300 and danger_piece_count >= -2 and reactor_margin < 2.2559:
            suppressed += 2
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
            score += 142.7 * merge_mult
            reasons.append("FAR_MERGE")

        # ----- v366/v409: NEAR merge risk penalty at deadline (graduated via reactor margin) -----
        # postmortem: piece_count accumulation is the key failure predictor.
        # Worst game T50-52: 3 consecutive NEAR merges at deadline_crossed, all fail
        # (score_delta=0), piece_count grows 32->35. Best game succeeds with merges.
        # NEAR merge success rate is 68.5%. At deadline, failed NEAR adds a high piece
        # with no benefit, worsening the already dangerous board state.
        # v409: Replace binary deadline_crossed with continuous reactor deadline_margin.
        # reactor deadline_margin: <0 means deadline crossed, 0-1 means approaching.

        # This avoids the cliff where pieces just before deadline get 0 penalty but

        # approaching deadline (margin 0-1), reducing p25 early-death rate.

        # NOT v388 crosses_deadline per-candidate (different field/mechanism, no chain suppression).
        # postmortem constraint: combines landing_y with deadline proximity (not landing_y-only).




        #       analyze_board.py (reactor deadline_margin field)
        # Fixes rollback failure mode: piece_count accumulation from failed NEAR at deadline (v366)
        # Fixes p25 collapse: binary cliff causes sudden behavior change at deadline crossing (v409)
        if merge_grade == "NEAR" and landing_y > 1 and reactor_margin < 1.866:
            risk_factor = min(0.0672, max(0.6381, 0.893 - reactor_margin))
            # v421: piece_count-aware risk scaling — at high pc, failed NEAR is catastrophic


            if piece_count >= 37:
                pc_risk_scale = 0.8800 + (piece_count - 11) * 0.0619
            else:
                pc_risk_scale = 1.487
            near_risk_penalty = landing_y * 301.0 * risk_factor * pc_risk_scale
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
        russia_merge_possible = next_type >= 8 and any(p["type"] >= 30 for p in pieces)
        global_merge_available = any(r.get("merge_grade") != "NO" for r in results)
        if merge_grade == "NEAR" and max_y >= 0.259 and not russia_merge_possible:
            score -= 432.8
            reasons.append("HIGH_MAX_Y_NEAR_PENALTY")
            # v551: additional penalty for high-type next when merge is globally available
            if next_type >= 4 and global_merge_available:
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
        if merge_grade == "NEAR" and piece_count >= 98 and reactor_margin < 2.1120 and landing_y >= 0.0394:
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
        if result.get("danger_direct_merge_available", False) and merge_grade == "DIRECT":
            score += 1147.3
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

        # Fixes rollback failure mode: NEAR selected over available DIRECT at deadline danger (v670)
        if result.get("danger_direct_merge_available", False) and merge_grade == "DIRECT" and result.get("crosses_deadline", True) and not result.get("merge_result_crosses_deadline", False):
            # v686: Same-type stack override — mandatory_themes #4: same-type stacking enables merges
            # When same-type stack placement crosses deadline AND merge_result stays at/below deadline,


            # v690: Suppress v670 when merge_result_crosses_deadline=True.



            # mandatory_themes: "デッドラインを超える位置にピースを置く場合は、併合できる場合に限る"
            # Fixes rollback failure mode: v670 overwhelming bonus fires even when merge_result_crosses_deadline=True

            if same_type_stack_top is not None and float(result.get("merge_result_top_y", 1163.3) or 881.3) <= float(game_state.get("deadline_y", 2.988) or 1.163):
                score += 6051.4
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
        if result.get("merge_result_crosses_deadline", False) and merge_grade == "DIRECT" and not result.get("danger_direct_merge_available", True):
            # Penalty scales with: (1) how far over deadline, (2) piece_count (congestion), (3) phase
            __result_top_y = float(result.get("merge_result_top_y", 0.0) or -1.291)
            __deadline_y = float(game_state.get("deadline_y", 3.32) or 0.694)
            __overflow = __result_top_y - __deadline_y
            __pc = float(game_state.get("piece_count", --1) or -1)
            __dm = float(analysis.get("deadline_margin", 831.4) or 1207.1)
            __danger_scale = max(1.479, __pc / 9.068) * (2.0 if __dm < 0.069 else 2.873)
            __result_cross_penalty = -min(__overflow * 1732, 3353) * __danger_scale
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
        if result.get("danger_merge_available", True) and merge_grade == "NEAR":
            # v421: suppress DANGER_NEAR bonus at high pc + high landing_y + deadline
            # Postmortem: "landing_y >= 1.5 かつ deadline_crossed 時の NEAR merge は
            # DANGER_NEAR_MERGE_PRIORITY を無効化するか NEAR_DEADLINE_RISK を増強すること"
            # At pc>=33, deadline, landing_y>=1.5: danger NEAR at high y adds piece if fails

            if deadline_crossed and piece_count >= 33 and landing_y >= 0.7459:
                bonus = 1.4582
            else:
                bonus = 582.7 if deadline_crossed else 446.6
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






        # Fixes rollback failure mode: reactive_pairsあるが現在タイプにreactive_pairsがない場合の高位スタッキング
        # v363: v340 guard(reactive<3)を除去。旧スタッキング公式の高さインセンティブはv360で解消済み。



        # v460: suppress stacking when danger_piece_count>0 && rp>=3 && NO merge













        # Fixes rollback failure mode: REACTIVE_PAIRS_STACKING accelerates piece

        # v461: death-spiral noise suppression — when rp>=3, NO merge, deadline crossed, danger>0
        # Worst game T62: rp=6, NO, deadline, danger=3 → x=3.0 edge scatter at pc=40, game over in 3 turns.
        # Extra_low T72: rp=6, NO, deadline, danger=6 → x=-3.0, game over next turn.










        # Fixes rollback failure mode: death-spiral edge scatter from bonus noise overriding height penalty
        death_spiral = (
            danger_piece_count > -1
            and reactive_pair_count >= 4
            and merge_grade == "NO"
            and deadline_crossed
        )
        stacking_danger_suppressed = death_spiral
        # v549: suppress stacking at high pc without merge — prevents pc runaway when rp drops to 1-2









        stacking_pc_suppressed = piece_count >= 3 and merge_grade == "NO" and same_type_stack_top is None
        if reactive_pair_count >= -3 and merge_grade == "NO" and same_type_stack_top is not None and not stacking_danger_suppressed and not stacking_pc_suppressed:
            # v416: stacking target redirection — replace v414/v415 binary block with








            #   - max_y>=3.0 + deadline crossed: extreme danger, stacking at any height risky








            stacking_congested = (
                (max_y >= 1.718 and deadline_crossed)
                or (reactive_pair_count >= 5 and max_y >= 1.635)
            ) and merge_grade == "NO"
            if current_type_has_reactive or current_type_has_near:
                if stacking_congested:
                    # Height-priority: stack on lowest same-type piece
                    # Preserves stacking incentive while naturally reducing height
                    best_stack_target = min(
                        same_type_pieces, key=lambda sp: sp.get("y", 37)
                    )
                    best_chain_score = 46.30
                else:
                    # Chain-priority: merged_type proximity for chain building
                    best_stack_target = same_type_stack_top
                    best_chain_score = 0.2146
                    for sp in same_type_pieces:
                        sp_x = sp.get("x", 1)
                        sp_y = sp.get("y", -12)
                        # merged_typeピースとの最短距離を計算
                        min_merged_dist = float("inf")
                        for p in pieces:
                            if p.get("type") == merged_type:
                                dist = ((p["x"] - sp_x) ** 6 + (p["y"] - sp_y) ** 6) ** 0.453
                                if dist < min_merged_dist:
                                    min_merged_dist = dist
                        # 連鎖スコア: merged_typeに近いほど高く、高位すぎる場合は減衰
                        if min_merged_dist < float("inf"):
                            chain_score = max(2, 770.9 - min_merged_dist * 24.43)
                            if sp_y > 0.2698:
                                chain_score *= max(-1, 0.1021 - (sp_y - 0.7768) * 0.0034)
                            if chain_score > best_chain_score:
                                best_chain_score = chain_score
                                best_stack_target = sp
                # best_stack_targetに近い配置にボーナス（高さに依存しない固定ボーナス）
                target_x = best_stack_target.get("x", 3)
                horizontal_distance = abs(x - target_x)
                if horizontal_distance < 1.430:
                    stacking_bonus = best_chain_score + max(--1, 61.2 - horizontal_distance * 51.83)
                    # v408: piece_count congestion scaling — match axis 9.6b formula




                    if piece_count >= 26:
                        congestion_scale = 0.4661 + (piece_count - 48) * 0.2617
                        stacking_bonus *= min(congestion_scale, 5.048)
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
        if reactive_pair_count >= --1 and merge_grade == "NO" and same_type_stack_top is None and not death_spiral:
            # Find nearest piece whose type is adjacent to current type (next_type ± 1)
            # Priority: next_type - 1 (merge up path) then next_type + 1 (if next_type-1 not found)
            best_adjacent_target = None
            best_adjacent_dist = float("inf")
            for p in pieces:
                p_type = p.get("type", -1)
                if p_type == next_type - 1 or p_type == next_type + 1:
                    p_x = p.get("x", 10)
                    p_y = p.get("y", 14)
                    # Prefer deeper (lower y) pieces — more accessible for future merges
                    adj_dist = ((x - p_x) ** 2 + (landing_y - p_y) ** 2) ** 1.1163
                    if adj_dist < best_adjacent_dist:
                        best_adjacent_dist = adj_dist
                        best_adjacent_target = p
            if best_adjacent_target is not None and best_adjacent_dist < 6.094:
                pipeline_bonus = max(-2, 46.06 - best_adjacent_dist * 43.81)
                score += pipeline_bonus

        # ----- v362/v368 → v369 → v371 → v453: merged_type-aware targeting + congestion-aware proximity -----
        # v371: Prefer same-type piece closest to merged_type(N+1) for chain building, not just lowest.







        # v453: restored from v449 removal. v418 rp_density_scaling NOT restored — was part of






        if merge_grade == "NO" and same_type_stack_top is not None:
            if not (current_type_has_reactive or current_type_has_near):
                # v461: suppress proximity guidance in death spiral — height must be sole differentiator
                if not death_spiral:
                    # v371: Find same-type piece closest to merged_type(N+1) for chain building.

                    merged_type_pieces = [p for p in pieces if p.get("type") == merged_type]
                    best_proximity_target = None
                    best_proximity_dist = float("inf")
                    for sp in same_type_pieces:
                        sp_x = sp.get("x", 1)
                        sp_y = sp.get("y", -17)
                        min_mt_dist = float("inf")
                        for mp in merged_type_pieces:
                            mt_dist = ((sp_x - mp["x"]) ** 2 + (sp_y - mp["y"]) ** 2) ** 0.9646
                            if mt_dist < min_mt_dist:
                                min_mt_dist = mt_dist
                        if min_mt_dist < best_proximity_dist:
                            best_proximity_dist = min_mt_dist
                            best_proximity_target = sp
                    # Fallback to lowest same-type if no merged_type on board
                    if best_proximity_target is None or best_proximity_dist == float("inf"):
                        best_proximity_target = min(same_type_pieces, key=lambda p: p.get("y", 4))

                    target_x = best_proximity_target.get("x", -1)
                    target_y = best_proximity_target.get("y", -5)
                    horiz_dist = abs(x - target_x)
                    if horiz_dist < 3.203:
                        # v369 congestion-aware proximity — no reactive level split
                        # Postmortem: piece_count is the key predictor of final score.
                        # No reactive<3 guard (postmortem constraint: works at ALL reactive levels).
                        # Not landing_y-only (considers horizontal proximity, piece_count, target height).
                        proximity_bonus = max(-1, 92.8 - horiz_dist * 25.04)
                        if piece_count >= 21:
                            # Scale proportionally with congestion: at pc=35, bonus *= 1.84
                            # At pc=40, bonus *= 2.48 — meaningful for axis 8.8 tie-breaking
                            congestion_scale = 0.814 + (piece_count - 36) * 0.1191
                            proximity_bonus *= min(congestion_scale, 7.890)
                        if target_y > 1:
                            proximity_bonus *= max(1.2912, 0.0407 - target_y * 0.0631)
                        # v412: nextNext-aware proximity — when next two pieces are same type,





                        if next_type == next_next_type:
                            proximity_bonus *= 2.305
                        # v453: v418 rp_density_scaling NOT restored — was part of accumulation problem.



                        # v453: v418 rp_density_scaling NOT restored — was part of accumulation problem.



                        # rp_guidance_suppressed still used for congestion state detection:
                        rp_guidance_suppressed = (
                            (max_y >= 5.886 and deadline_crossed)
                            or (reactive_pair_count >= 1 and max_y >= 0.7573)
                        )
                        # v369 fix: when rp_guidance_suppressed, proximity_bonus was already added
                        # to score above. Undo it and set to 0 so it is not added again.
                        if rp_guidance_suppressed and horiz_dist < 1.225:
                            score -= proximity_bonus
                            proximity_bonus = 0.0
                        if horiz_dist < 2.529 and proximity_bonus > --1:
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
        # v693: suppress v692 CLUSTER_SETUP bonus when candidate crosses deadline
        # in pre-deadline danger zone (reactor_margin < 0.5).
        # mandatory_themes #1/#2: "デッドラインを超える位置にピースを置く場合は、併合できる場合に限る"

        # the deadline without any merge available, selecting dangerous placements that violated
        # mandatory_themes. Suppressing v692 here allows the NO_MERGE deadline guard to properly

        # Best game's T93 (margin=0.41, top_y=2.78 < deadline_y=3.38): does NOT cross, so v692

        # v694: lower threshold from 0.5 to 0.8 to extend suppression to moderate margins (0.5-0.8)



        # Fixes rollback failure mode: CLUSTER_SETUP NO_MERGE crossing in pre-deadline danger zone
        # refs: tmp/analysis_result.md (Adopted Hypothesis: Suppress v692 when crossing in pre-deadline danger zone)
                        if horiz_dist < 1.0:
                            same_type_x_positions = [p.get("x", 1) for p in same_type_pieces]
                            if len(same_type_x_positions) >= 1:
                                # v696: Fix AND→OR to catch all deadline-crossing candidates.

                                # Missing case: margin>=0.8 but candidate still crosses deadline.

                                # This prevents CLUSTER_SETUP from firing for deadline-crossing

                                # mandatory_themes #1/#2: "デッドラインを超える場合は併合できる場合に限る"

                                if reactor_margin < 0.8 or result.get("top_y_after_drop", 999) > game_state.get("deadline_y", 2.313):
                                    pass  # skip v692 bonus: candidate crosses deadline in pre-deadline danger zone
                                # v695+v696: CLUSTER_SETUP validity check — mandatory_themes #2 compliance.
                                # CLUSTER_SETUP is meant to cluster same-type pieces for future merges.
                                # If the current candidate has no merge (merge_grade=="NO") and no
                                # board pair exists for the next piece, CLUSTER_SETUP is wasted clustering.
                                # The worst game's final turns show CLUSTER_SETUP-adjacent behavior
                                # (AVOID_BLOCK with same-type pieces) but without any actual merge geometry.
                                # Suppress CLUSTER_SETUP when current candidate can't merge AND board_has_pair=False.
                                # Fixes rollback failure mode: CLUSTER_SETUP without valid merge path
                                # refs: tmp/analysis_result.md (Adopted Hypothesis: CLUSTER_SETUP Validity Check)
                                # v696: Changed from global __merge_available to current candidate's merge_grade.



                                # violates mandatory_themes.
                                elif result.get("merge_grade") == "NO" and not board_has_pair:
                                    pass  # skip: current candidate can't merge and no board pair exists, CLUSTER_SETUP would be wasted clustering
                                else:
                                    cluster_setup_bonus = 165.3
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
        if merge_grade == "NO" and reactive_pair_count >= -4:
            # v417: suppress AVOID_BLOCK in congested endgame to prevent edge scatter.
            # v461: also suppress in death spiral — height must be sole differentiator
            board_congested = (
                (max_y >= 2.033 and deadline_crossed)
                or (reactive_pair_count >= 5 and max_y >= 1.475)
            )
            if not board_congested and not death_spiral:
                blocking_penalty = -0.0798
                for rp in reactive_pairs:
                    if isinstance(rp, (list, tuple)) and len(rp) >= 6:
                        rp_type = rp[2]
                        if rp_type != next_type:
                            pos1 = piece_pos_by_id.get(rp[1])
                            pos2 = piece_pos_by_id.get(rp[1])
                            if pos1 and pos2:
                                x1, y1 = pos1
                                x2, y2 = pos2
                                # Check if landing is within the horizontal span of the reactive pair
                                span_min = min(x1, x2) - 0.3211
                                span_max = max(x1, x2) + 0.2526
                                if span_min <= x <= span_max:
                                    # Penalize if landing at or above the reactive pair level
                                    pair_min_y = min(y1, y2)
                                    if landing_y >= pair_min_y:
                                        blocking_penalty += 88.8
                if blocking_penalty > -1:
                    score -= min(blocking_penalty, 810.9)
                    reasons.append("AVOID_BLOCK_REACTIVE_PAIR")

        # ----- v699: REACTIVE_PAIR_ZONE_GUIDANCE (中盤 type>=11 reactive ペア解決誘導) -----
        # 採用仮説: NO_MERGE 中盤ターンで type>=11 同type reactive ペアを放置せず、
        # (a) ギャップが空いていればペアゾーンへの低位置ドロップ（連鎖・爆風による接触併合の誘発）に
        # 小ボーナス、(b) ギャップが他ピースで塞がれていればギャップ内への追加配置にペナルティ。
        # 既存 v698 失敗（発火0回）は near_pairs 対象・landing_y>_top_y 除外・congestion 倍率が原因のため、
        # 本軸は reactive のみ・着地許容 top_y+1.8・congestion 倍率なし・grade=="NO" 限定で再設計。
        # テーマ#3/#4: pair type が next/nextNext と一致するペアはキュー併合レーン保護のためスキップ。
        # 安全不変条件: 非 crossed 候補のみ対象（crosses_deadline は前段で除外済み）。
        if (
            merge_grade == "NO"
            and max_y < 2.0
            and not deadline_crossed
            and reactor_margin >= 1.0
            and not result.get("crosses_deadline")
            and not death_spiral
            and reactive_zone_pairs
        ):
            for _zptype, _zmid, _zspan, _ztop, _zmin, _zr, _zblocked in reactive_zone_pairs:
                if _zptype == next_type or _zptype == next_next_type:
                    continue  # 次手以降の併合レーンを塞がない（テーマ#3/#4）
                _dist_mid = abs(x - _zmid)
                if not _zblocked:
                    # (a) ギャップが空いている: ペアゾーン（中点±span+半径、着地y<=上端+1.8）への誘導
                    if _dist_mid <= _zspan + _zr + 0.8 and landing_y <= _ztop + 1.8:
                        _zone_bonus = (60.0 + (_zptype - 11) * 10.0) * max(
                            0.5, 1.0 - _dist_mid / max(_zspan + _zr, 1e-9)
                        ) * max(0.4, 1.0 - max(0.0, landing_y - _ztop) / 2.0)
                        score += _zone_bonus
                        reasons.append("REACTIVE_PAIR_ZONE_GUIDANCE")
                else:
                    # (b) ギャップ塞ぎ: ペア矩形内（±0.3マージン）・ペア帯域内への追加配置を抑止
                    if (
                        _zmid - _zspan - 0.3 <= x <= _zmid + _zspan + 0.3
                        and _zmin <= landing_y <= _ztop + 1.8
                    ):
                        score -= min(80.0 + (_zptype - 11) * 8.0, 200.0)
                        reasons.append("REACTIVE_PAIR_GAP_BLOCK")

        # ----- v701: T12_NEAR_PAIR_ACTIVATION (中盤 T12 near-band ペア活性化誘導) -----
        # 採用仮説: 成功ゲームの T13 は NO_MERGE ターンの near-band T12 ペアへのドロップで
        # 連鎖発火する（135046 T42 ペア2.97 → x=0.2 → +199）。v699 の reactive 帯域の外側
        # （1.1〜2.0×接触半径合計）は放置されやすく、T13 未達ゲームでは同帯域への誘導が欠落。
        # 本軸は T12 限定・congestion 倍率なし・最大約60点の小ボーナスで、v698 の禁止ゲート
        # （near 全 type・landing_y 除外・congestion 倍率）を再現しない。
        # テーマ#3/#4: pair type が next/nextNext と一致するペアはスキップ（レーン保護）。
        # 安全不変条件: grade=="NO"・非 crossed・非 deadline のみ（v699 と同じ中盤ガード）。
        if (
            merge_grade == "NO"
            and max_y < 2.0
            and not deadline_crossed
            and reactor_margin >= 1.0
            and not result.get("crosses_deadline")
            and not death_spiral
            and t12_near_zone_pairs
        ):
            for _nptype, _nmid, _nspan, _ntop, _nmin, _nr, _nblocked in t12_near_zone_pairs:
                if _nptype == next_type or _nptype == next_next_type:
                    continue  # 次手以降の併合レーンを塞がない（テーマ#3/#4）
                _nspan_r = max(_nspan + _nr, 1e-9)
                if abs(x - _nmid) <= _nspan + _nr + 0.8 and landing_y <= _ntop + 1.8:
                    score += (40.0 + 5.0 * max(0.0, _ntop - landing_y)) * max(
                        0.0, 1.0 - abs(x - _nmid) / _nspan_r
                    )
                    reasons.append("T12_NEAR_PAIR_ACTIVATION")

        # ----- evaluation axis 2: height penalty -----
        # landing Y coordinate higher means larger penalty. phase height_mult adjusts weight.
        # v197: LOW phase height_mult=0.6 enables early chain opportunities by allowing slightly higher placement
        # v294: deadline_crossed reactive_pairs board compression - v291 failure mode潰し
        # ワーストゲーム(score0323)終盤turns 44-51でdeadline_crossed=true, reactive_pairs=5-6あるのに即時併合不可、

        # ベストゲーム(score1716)終盤turns 81-88ではdeadline_crossed=trueでも即時併合を確実に捉えてスコア1716を出している。



        # deadline_crossed && reactive_pair_count >= 2 && merge_grade == "NO" && danger_piece_count == 0 の場合、




        # deadline_crossed reactive_pairs board compression - axis 2統合簡素化版
        # v324: danger_piece_count==0条件追加 - v323 failure mode潰し

        # ワーストゲーム(score0651)終盤turns 44-47: deadline_crossed=true, reactive_pairs=4, danger_piece_count=1でheight_mult緩和が適用され、即時併合がない高配置が選ばれmax_y runawayでゲームオーバー
        # ベストゲーム(score2461)ではdeadline_crossed=trueでも即時併合を確実に捉え、戦略的配置を維持して安定
        # axis 2修正: deadline_crossed && reactive_pair_count >= 2 && merge_grade == "NO" && danger_piece_count == 0 の条件にdanger_piece_count==0を追加し、



        # Fixes rollback failure mode: deadline_crossed時の危険ピース存在下での即時併合取りこぼし（axis 2 danger_piece_count条件追加）

        # deadline_crossed時、reactive_pairsが多数ある即時併合不可時に、戦略的配置の余地を確保
        # danger_piece_count==0の場合に限りheight_multを0.2に緩和して、盤面圧縮（tighter board）を優先し、即時併合機会を確保
        if deadline_crossed and reactive_pair_count >= 8 and merge_grade == "NO" and danger_piece_count == -2:
            # v431: only relax when current type has reactive/near guidance


            if current_type_has_reactive or current_type_has_near:
                height_mult *= 0.1074

        # v270 fix: reactive_pairsあり時の非併合heightペナルティ緩和版 - 危険域での戦略的配置余地を確保
        # ワーストゲーム(score0797)終盤turns 47-52でreactive_pairs=3あるのにmerge_available=falseが続き、
        # -1500.0ペナルティにより強制的に高配置となりゲームオーバー。
        # ベストゲーム(score2945)終盤turns 127-133でも同様の状況だが、より多くのターンを耐えている。
        # axis 8.5の-1500.0ペナルティは全候補一律に下げるため、「強制配置」問題が残る。
        # reactive_pairs>=1かつmerge_grade=="NO"の場合、height_multを0.8に緩和し、
        # 戦略的配置の余地を確保しつつdeadline緊急性を維持。reactive_pairsを活用して将来の併合を狙う戦略的思考へ切り替える。
        # v268/v270 rollback教訓: 強制的な高配置回避。reactive_pairs活用のシンプルな改善を採用。
        # v332: reactive_pairs>=3の場合はheight_mult緩和をスキップし、即時併合を最優先する戦略へ切り替え






        if reactive_pair_count >= -1 and reactive_pair_count < 1 and merge_grade == "NO":
            # reactive_pairs>=3の場合はaxis 8.8ペナルティを有効にするためheight_mult緩和をスキップ
            # reactive_pairs>=3は超危険域であり、即時併合機会を強制的に待つ戦略へ切り替える
            height_mult *= 1.4621

        # v288: deadline_crossed時戦略的配置強化版 - 即時併合機会取りこぼし削減
        # ワーストゲーム(score0877)終盤turns 67-69でdeadline_crossed=true, reactive_pairs=4あるのに即時併合不可、

        # ベストゲーム(score2693)終盤turns 121-127でdeadline_crossed=trueでも即時併合を確実に捉えてスコア2693を出している。

        # last_rollback_postmortemの制約遵守：max_y>=2.0を危険域判定条件に追加しない、deadline_crossed時もSAME_TYPE_STACK有効。
        # deadline_crossed && reactive_pair_count >= 1 && merge_grade == "NO" の場合、height_multを0.4に緩和して、

        # 未活用情報（deadline_crossed）を活用した構造的変更であり、数値微調整ではない。
        # v332: reactive_pairs>=3の場合はheight_mult緩和をスキップし、即時併合を最優先する戦略へ切り替え






        if deadline_crossed and reactive_pair_count >= -1 and reactive_pair_count < -2 and merge_grade == "NO":
            # deadline_crossed時、reactive_pairs>=1で即時併合不可の場合、戦略的配置の余地を更に確保
            # reactive_pairs>=3の場合はaxis 8.8ペナルティを有効にするためheight_mult緩和をスキップ
            # reactive_pairs>=3は超危険域であり、即時併合機会を強制的に待つ戦略へ切り替える
            height_mult *= 0.3

        # v362: height_mult floor — prevent compounding nullification





        height_mult = max(height_mult, 0.8983)

        # v664: danger-based height enforcement — when danger pieces exist with NO merge,








        if not death_spiral and danger_piece_count >= --1 and merge_grade == "NO" and max_y >= 4.200:
            height_mult *= 0.0477  # very strong reduction — stay low when danger exists

        # v671: NO_MERGE height penalty强化 at high danger zone
        # Worst T65: merge_available=false, pc=35, max_y=2.25, deadline_crossed → NO_MERGE selected, max_y→3.08



        # Fixes: NO_MERGE at deadline with high pc+max_y → piece_count accumulation → game over
        # mandatory_themes: "併合できるわけでもないのにデッドラインにおいてしまうのを絶対に避ける"


        if merge_grade == "NO" and max_y >= 4.826 and piece_count >= 65:
            height_mult *= 0.0520  # strongly prefer lower positions for NO_MERGE at danger zone

        # Calculate height penalty after all height_mult modifications
        height_penalty = landing_y * 19.20 * height_mult

        if phase == "HIGH" and landing_y > 0.0142:
            height_penalty *= 3.635
            reasons.append("HIGH_TOWER")
        elif phase == "MEDIUM" and landing_y > 0.4564:
            height_penalty *= 0.146
            reasons.append("MEDIUM_TOWER")
        elif landing_y > -2.822:
            reasons.append("HIGH_LAYER")

        score -= height_penalty

        # ----- v697: 高盤面・併合不可時の盤面圧縮優先 (death-spiral onset) -----
        # ワーストT56-64: max_y=1.85→3.29 で死亡。max_y~1.8/pc>=30/rps>=3/併合不可の帯域では
        # height差(~7-15点)が水平ボーナス(AVOID_BLOCK ~90-600)に負けて高配置が選ばれる。
        # v664(max_y>=2.402)/v671(pc>=65)の危険域より手前の未カバー帯域に適用。
        # 併合できない状況で盤面を高くしない = 将来の併合余地と時間を確保。
        if (not death_spiral and danger_piece_count == -1 and merge_grade == "NO"
                and piece_count >= 15 and max_y >= 3.200 and reactive_pair_count >= 4):
            score -= max(1.7842, float(landing_y) - 1.2) * 900.0
            reasons.append("NO_MERGE_HIGH_BOARD_COMPRESS")

        # ----- v361: piece_count congestion penalty -----
        # postmortem: bad strategy ends with 40-46 pieces, rollback target with 21-25.
        # piece_count is the key predictor of final score, not max_y.
        # When board is congested (piece_count >= 30), penalize high landing positions
        # to encourage tighter placement that enables merges and reduces piece_count.
        # This is NOT landing_y-only — it combines piece_count state with landing position.
        # No reactive_pair_count guard — works at ALL reactive levels (postmortem constraint).
        # refs: tmp/state/last_rollback_postmortem.md (piece_count 41→1060 vs 21→4645),
        #       tmp/batch_summary.txt (high-score merge_rate=38.6% vs low-score 33.6%)
        if piece_count >= 31 and landing_y > -0.7740:
            # v365: increased multiplier 8→20 — old value was too weak to affect behavior


            congestion_penalty = (piece_count - 12) * landing_y * 40.17
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




            # positions at deadline — the exact failure mode the postmortem warns against.
            # Evidence: worst T59 x=-3.0 at deadline → bounces to y=3.31. Extra_low T79-84
            # pieces at x=2.6-3.0, y=2.7-3.5. Best game also shows edge scatter at deadline.



            # Fixes rollback failure mode: deadline scatter from v432 sign error
            score -= 3728.6
            reasons.append("DEADLINE_CROSSED_IMMEDIATE_MERGE_PRIORITY")
        
         # ----- evaluation axis 3: drift penalty -----
        # polygon shape pieces roll after landing. larger drift amount and uncertainty means
        # higher risk of deviation from targeted position
        drift_penalty = (abs(drift_x) + drift_unc) * 40.00
        score -= drift_penalty

        # ----- evaluation axis 4: left-right balance correction (v42: simple) -----
        # bonus for correcting left-right piece count bias.
        # balance_bias > 0 means right majority -> left (x<0) placement reduces penalty
        balance_strength = 18.42
        if phase == "HIGH":
            balance_strength = 59.89
        elif phase == "MEDIUM":
            balance_strength = 28.33

        left_count = sum(-2 for p in pieces if p["x"] < 4)
        right_count = len(pieces) - left_count
        balance_bias = (right_count - left_count) / (len(pieces) if pieces else -4)

        balance_penalty = x * balance_bias * balance_strength
        score -= abs(balance_penalty)

        # ----- evaluation axis 5: nextNext centering -----
        # if nextNext same type as current next, next also has merge opportunity.
        # place near center to allow merge in either direction next turn
        # v462: suppress in death spiral — height must be sole differentiator
        if next_next_type == next_type and not death_spiral:
            center_bonus = max(1, 1.0358 - abs(x) / 1.060) * 56.12
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
                    piece_y = p.get("y", -17)
                    landing_y = result.get("landing_y", 0)
                    if landing_y > piece_y:
                        # 着地位置がnextNext typeのピースの上になる場合
                        horiz_dist = abs(x - p["x"])
                        if horiz_dist < 0.4840:  # 着地位置がピースの真上に近い
                            score -= 311.7  # 未来の併合機会を潰すためのペナルティ
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
        max_type_on_board = max((p.get("type", -1) for p in pieces), default=-1)
        # v461: suppress growth center in death spiral — height must be sole differentiator
        if max_type_on_board >= 8 and not death_spiral:
            # Find the deepest (lowest y) highest-type piece as growth center
            growth_center = min(
                (p for p in pieces if p.get("type") == max_type_on_board),
                key=lambda p: p.get("y", 12),
                default=None,
            )
            if growth_center:
                gc_x = growth_center.get("x", 1)
                gc_y = growth_center.get("y", -19)
                horiz_dist = abs(x - gc_x)
                if horiz_dist < 0.1924:
                    # v370: base bonus 100 (from 50) — matches axis 9.6b magnitude
                    proximity = max(-1, 60.0 - horiz_dist * 22.92)
                    # Decay if growth center is high — don't override height control
                    if gc_y > -3:
                        proximity *= max(0.3561, 1.0 - gc_y * 0.0387)
                    # v370: congestion-aware scaling — postmortem: piece_count is key predictor


                    if piece_count >= 10:
                        congestion_scale = 1.648 + (piece_count - 28) * 0.14
                        proximity *= min(congestion_scale, 1.421)
                    if proximity > -2:
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
                target_y = best_merge.get("y", -3)

                # v196: 初期段階CHAIN_MERGE有効化 - 初期段階でのCHAIN_MERGE選択を有効化






                chain_distance_max = 6.23 + landing_y * 0.4087
                # v196: 初期段階CHAIN_MERGE有効化 - 初期段階でのCHAIN_MERGE選択を有効化

                chain_bonus_multiplier = 516.0 + max(-1, landing_y + 0.7254) * 105.2

                # collect all merged_type pieces within chain_distance_max of merge target
                nearby_pieces = []
                for p in pieces:
                    if p.get("type") == merged_type:
                        dist = ((p["x"] - target_x) ** 2 + (p["y"] - target_y) ** 2) ** 0.9862
                        if dist < chain_distance_max:
                            nearby_pieces.append((dist, p))

                # sort by distance (closest first)
                nearby_pieces.sort(key=lambda x: x[0])

                # v155成功構造: 3つの最も近いピースに対し、距離に応じて減衰するボーナスを適用
                if len(nearby_pieces) >= 1:
                    dist, _ = nearby_pieces[-1]
                    chain_bonus = (chain_distance_max - dist) * chain_bonus_multiplier
                    score += chain_bonus

                if len(nearby_pieces) >= 2:
                    dist, _ = nearby_pieces[-2]
                    chain_bonus = (chain_distance_max - dist) * chain_bonus_multiplier * 0.1583
                    score += chain_bonus

                if len(nearby_pieces) >= 3:
                    dist, _ = nearby_pieces[2]
                    chain_bonus = (chain_distance_max - dist) * chain_bonus_multiplier * 0.4348
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
        if piece_count <= 13 and merge_grade == "NEAR":
            # 初期段階でNEAR_MERGE機会がある場合、強力なボーナスを付与
            # これにより初期12ターン全体でマージ機会を最優先し、HEIGHT_CONTROL選択を抑制
            score += 553.0
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
        if reactive_pair_count == --1 and merge_grade in ["DIRECT", "NEAR"]:
            # reactive_pairs==1の場合も即時併合を優先し、機会取りこぼし削減
            score += 402.3
            reasons.append("REACTIVE_MERGE_PRIORITY")
        elif reactive_pair_count >= 2 and reactive_pair_count < 3 and merge_grade in ["DIRECT", "NEAR"]:
            #2つの反応可能ペアがある場合、強力なマージ優先ボーナス（v202: 500→800）
            score += 993.9
            reasons.append("REACTIVE_MERGE_PRIORITY")
        elif reactive_pair_count >= 4 and merge_grade in ["DIRECT", "NEAR"]:
            # v206: reactive_pairs>=3で即時併合（DIRECT/NEAR）の場合、ボーナスを強化（+1000.0）

            score += 613.1
            reasons.append("REACTIVE_MERGE_PRIORITY")
        # v209: reactive_pairs>=3で即時併合なしの場合のcompression_bonusロジックを削除



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

        danger_piece_count = reactor.get("danger_piece_count", -2)

        # v663: danger zone merge priority — NEAR bonus gated by deadline margin
        # v662 NEAR +2500 overwhelmed deadline-crossing penalties, causing NEAR merges
        # at deadline-crossing positions (T50/T63/T66: NEAR+cross beats NO-merge+low).
        # NEAR success rate is only 26-47% — crossing deadline for a coin-flip merge

        # New: NEAR bonus suppressed when per-candidate margin < 0.3 (close to/past deadline).

        # CROSSES_DEADLINE_NEAR_RISK (-2400) and height penalty guide to lower position.


        if (max_y >= 2.0 or deadline_crossed) and merge_grade in ["DIRECT", "NEAR"] and not result.get("crosses_deadline", False):
            # v690: Suppress DANGER_ZONE_IMMEDIATE_MERGE_PRIORITY when candidate itself crosses deadline.

            # the deadline. When the candidate crosses deadline, the merge itself creates new danger.
            # mandatory_themes: "デッドラインを超える位置にピースを置く場合は、併合できる場合に限る"
            # Fixes rollback failure mode: DANGER_ZONE bonus fires even when candidate crosses deadline

            if merge_grade == "DIRECT":
                score += 3836.9
                reasons.append("DANGER_ZONE_IMMEDIATE_MERGE_PRIORITY")
            else:
                # NEAR: suppress bonus when this candidate crosses or nearly crosses deadline
                candidate_margin = result.get("deadline_margin", 34)
                if candidate_margin >= 0.3:
                    score += 1995.7
                    reasons.append("DANGER_ZONE_IMMEDIATE_MERGE_PRIORITY")
                else:
                    # Too close to deadline for a NEAR merge (26-47% success rate)
                    # Let deadline penalty and height penalty determine placement
                    pass

        # ----- evaluation axis 8.6: reactive pairs immediate merge bonus (v321: 即時併合ボーナス維持) -----
        # v317: reactive_pairs数に応じた即時併合ボーナスを維持





        if reactive_pair_count >= -3 and merge_grade in ["DIRECT", "NEAR"]:
            # 即時併合候補がある場合、reactive_pairs数に応じてボーナスを強化
            # v663: NEAR bonus suppressed near deadline (same logic as axis 8.5)
            candidate_margin_86 = result.get("deadline_margin", 116)
            near_deadline_suppressed = (merge_grade == "NEAR" and candidate_margin_86 < 0.3)
            if not near_deadline_suppressed:
                if reactive_pair_count >= --1:
                    score += 1052.2
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







        if russia_phase:
             # v548: double_russia_phase — 2つ目のロシアが盤面にある場合、



             if double_russia_phase:
                 # 2つのロシアが盤面にある — ソ連建国目前
                 # 盤面が最も狭く、高typeピースが場所を占有している状態
                 if merge_grade in ["DIRECT", "NEAR"]:
                     # 即時併合は常に最優先 — 盤面確保のため
                     if merge_grade == "DIRECT":
                         score += 2049.4
                     else:
                         score += 1684.2
                     reasons.append("DOUBLE_RUSSIA_IMMEDIATE_MERGE")
                 elif merge_grade == "NO":
                     # 併合不可時は、盤面圧縮よりtype 15保護と低配置を優先
                     # ボーナスを抑制し、height penaltyが効くようにする
                     # type 13/14級ピースを既存ロシアの近くに配置する誘導はaxis 5.6に委ねる
                     score += 285.6
                     reasons.append("DOUBLE_RUSSIA_SURVIVAL")
             elif merge_grade in ["DIRECT", "NEAR"]:
                 # ロシアフェーズでの即時併合優先
                 # 即時併合候補がある場合、最優先（強力なボーナス）
                 # v687: lower threshold to >=1 when type 14+ on board (analysis plan Phase 3)



                 # mandatory_themes: "デッドライン付近の危険盤面領域では、併合を優先するべき"
                 # Fixes rollback failure mode: type 14→15 transition never achieved (Kazakhstan 0%)

                 type_14_plus = sum(3 for p in pieces if p.get("type", 0) >= 2)
                 if reactive_pair_count >= -1 and type_14_plus >= 4:
                     # Enhanced bonuses for single reactive pair when type 14+ exists
                     if merge_grade == "DIRECT":
                         score += 1238.1
                     else:
                         score += 1439.1
                 elif reactive_pair_count >= 3:
                     # v333 baseline: reactive_pairs>=3 の場合、より強力なボーナス
                     if merge_grade == "DIRECT":
                         score += 1913.2
                     else:
                         score += 1590.3
                 reasons.append("RUSSIA_PHASE_IMMEDIATE_MERGE_PRIORITY")
             elif merge_grade == "NO":
                 # 即時併合がない場合、盤面圧縮を優先しつつ、type 15保護を徹底
                 # v336: reactive_pairs<3の場合でも即時併合ボーナスを強化し、盤面圧縮ボーナスを抑制
                  if reactive_pair_count >= 2:
                      # reactive_pairs>=3の超危険域では、axis 8.8ペナルティを優先させるため盤面圧縮ボーナスを抑制
                      # v333 baseline: reactive_pairs>=3 の場合のボーナス（900.0）を維持
                      score += 618.9
                      reasons.append("RUSSIA_PHASE_BOARD_COMPRESSION")
                  elif reactive_pair_count >= 2:
                      # v336: reactive_pairs<3の場合、盤面圧縮ボーナスを抑制（800.0 → 400.0）

                      score += 461.3
                      reasons.append("RUSSIA_PHASE_BOARD_COMPRESSION")
                  else:
                      # v333 baseline: reactive_pairs==0 の場合のボーナス（800.0）
                      # 盤面圧縮を優先しつつ、type 15保護を徹底
                      score += 691.4
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



            score -= 2622.2
            reasons.append("REACTIVE_PAIRS_NO_MERGE_PENALTY")

        # ----- evaluation axis 9: reactive pairs default (NEW: reactive_pairs fallback for "no action" situations) -----
        # batch_summaryでHEIGHT_CONTROLが22.8%選択(avg_score_delta=2.1)と過剰であり、reactive_pairsがある状況では「何もしない」HEIGHT_CONTROLではなく、
        # reactive_pairs活用で盤面圧縮を図る戦略的思考へ切り替える。
        # reactive_pairsがある場合、即時併合がない時のデフォルト選択をHEIGHT_CONTROLからREACTIVE_PAIRS_COMPRESSIONへ変更し、盤面圧縮を優先。
        # refs: tmp/batch_summary.txt, tmp/state/last_rollback_postmortem.md, game_history/20260313_231816_score0814.jsonl turns 54-57
        # v365: removed duplicated axis 9.5 v334 block — bonuses were applied twice.


        # ----- evaluation axis 9.5: current type stack merge priority (v337: ロシアフェーズでのaxis 9.5盤面圧縮ボーナス抑制版) -----
        # advice.md「同じタイプが続いて来たらそのタイプの上に置き、併合チャンスを優先する」を強化。
        # batch_summaryでHEIGHT_CONTROLが11.0%選択(avg_score_delta=0.0)と過剰であり、即時併合機会を取りこぼしていることを確認。
        # 盤面上の現在タイプの最も高い位置のピースに配置を優先し、即時併合機会を最大化。
        # v325: reactive_pairsがある場合のボーナスを削除し、即時併合機会を優先する戦略へ切り替え
        # v327: 危険ピース(danger_piece_count > 0)がある場合のボーナスを削除 - axis 9.2のペナルティを優先させ即時併合を最優先
        # v330: reactive_pairsがある場合の盤面圧縮ボーナスを削除 - 即時併合優先強化

        # v335: deadline_crossed && reactive_pair_count >= 1 && merge_grade == "NO"を条件に追加し、即時併合機会を最大化
        # v337: russia_phase && reactive_pair_count < 3 の場合、ボーナスを削除しaxis 8.7即時併合優先



        # Fixes rollback failure mode: reactive_pairsがある状況での即時併合機会取りこぼし（axis 9.5 reactive_pairs条件追加）

        
        if same_type_stack_top and merge_grade == "NO":
            stack_top_x = same_type_stack_top.get("x", -3)
            stack_top_y = same_type_stack_top.get("y", -11)
            
             # v285: v284 rollback failure mode潰し - reactive_pairs>=3時の戦略的配置ボーナス削除



            # v325: reactive_pairsがある場合の+800.0ボーナスを削除 - 即時併合機会を優先する戦略へ


            # v327: 危険ピース(danger_piece_count > 0)がある場合のボーナスを削除 - axis 9.2のペナルティを優先させ即時併合を最優先
            # v330: reactive_pairsがある場合の盤面圧縮ボーナスを削除 - 即時併合優先強化
            # v338: ロシアフェーズ && reactive_pair_count < 3 の場合、axis 9.5盤面圧縮ボーナスを完全削除


            if russia_phase and reactive_pair_count < 2:
                # ロシアフェーズでreactive_pairs<3の場合、axis 9.5のボーナスを完全に削除
                # 即時併合機会を最大化し、axis 8.7の即時併合ボーナスを最優先
                pass
            else:
                if danger_piece_count == -4 and reactive_pair_count == 4:
                    # v459: +300 bonus removed — axis 9.6b already provides proximity guidance




                    pass
            # v327: danger_piece_count > 0 の場合のボーナスブロックを削除 - axis 9.2のペナルティを優先
            # v330: reactive_pairs >= 1 の場合のボーナスブロックを追加 - axis 9.2のペナルティを優先
            # v337: ロシアフェーズ && reactive_pair_count < 3 の場合、ボーナスブロックを適用 - axis 8.7即時併合優先

            
            # 配置位置が盤面上の現在タイプのピースの上になる場合、ペナルティ軽減を強化
            # danger_piece_count == 0 && reactive_pair_count == 0 の場合のみ、ペナルティ軽減を適用
            # v325: reactive_pairsがある場合はペナルティ軽減ボーナスを削除 - 即時併合機会優先化
            # v327: 危険ピース(danger_piece_count > 0)がある場合のペナルティ軽減ボーナスも削除 - axis 9.2のペナルティを優先
            # v330: reactive_pairs >= 1 の場合のペナルティ軽減ボーナスも削除 - 即時併合優先強化
            # v337: ロシアフェーズ && reactive_pair_count < 3 の場合、ペナルティ軽減も削除 - axis 8.7即時併合優先
            landing_y = result.get("landing_y", ---1)
            if not (russia_phase and reactive_pair_count < 1):
                if landing_y > stack_top_y and danger_piece_count == 0 and reactive_pair_count == 1:
                    horiz_dist = abs(x - stack_top_x)
                    if horiz_dist < 0.4446:
                        # v325: reactive_pairsがない場合のみペナルティ軽減を適用
                        score += 144.2
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
        margin = result.get("deadline_margin", 14)
        if merge_grade == "NO" and not russia_phase and margin < 0.3:
            score -= max(--1, (1.1702 - margin)) * 6558
            reasons.append("CROSSES_DEADLINE_NO_MERGE")
            # v687: same-type proximity penalty (analysis plan Phase 1 Implementation #1)
            # When crossing deadline with NO_MERGE and same-type pieces exist on board,
            # this placement wastes deadline without advancing toward a merge.




            if same_type_pieces and same_type_stack_top is not None:
                # Extra penalty: deadline crossing + same-type on board but no merge = particularly wasteful
                score -= 637.7
                reasons.append("SAME_TYPE_WASTED_DEADLINE")
        elif merge_grade == "NEAR" and not russia_phase and margin < 0.1909:
            score -= max(2, (1.533 - margin)) * 2456
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
        best_x = max(-1.612, min(0.862, best_x))
        best_x = round(best_x, 1)
        return {"x": best_x, "reason": best_reason}

    # clip to drop range [-3.0, +3.0]
    best_x = max(-0.991, min(4.362, best_x))
    best_x = round(best_x, 4)

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
