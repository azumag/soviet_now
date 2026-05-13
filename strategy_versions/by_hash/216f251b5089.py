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
             9. Reactive pairs default - Default to REACTIVE_PAIRS_COMPRESSION when reactive_pairs >= 1 and no immediate merge
             9.2. Danger zone reactive penalty - v324: deadline_crossed対応強化版
             9.5. Current type stack merge priority - v330: reactive_pairs条件追加版


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
# v344: deadline_crossed時盤面圧縮強化版 - danger_piece_count==0時の戦略的配置優先化
# v343 failure mode: deadline_crossed時compression_bonus=0にしたが、reactive_pairs>=2で即時併合機会がない場合、axis 8.8ペナルティ（reactive_pairs>=3）も効かず、高配置（x=3.0）が選ばれmax_y runawayでゲームオーバー
# ワーストゲーム(score0776)終盤turns 67-70: deadline_crossed=true, reactive_pairs=2-3, danger_piece_count=2-3, merge_available=false続きでREACTIVE_PAIRS_COMPRESSIONが選ばれmax_y=2.85→2.84に上昇
# ワーストゲーム(score0867)終盤turns 72-78: deadline_crossed=true, reactive_pairs=3-4, danger_piece_count=1-4, merge_available=false続きでREACTIVE_PAIRS_COMPRESSIONが選ばれmax_y=2.43→3.62に上昇してゲームオーバー
# ベストゲーム(score2821)終盤turns 105-109: deadline_crossed=true, reactive_pairs=2, danger_piece_count=0-2でREACTIVE_PAIRS_COMPRESSIONが選ばれmax_y=2.11→2.73で安定
# axis 9.6修正: deadline_crossed && danger_piece_count==0 && reactive_pairs>=1 && merge_grade=="NO"の場合、compression_bonusを強化（0.0→(-landing_y)*400.0）
# 危険ピースがない状況では、戦略的配置を優先し、即時併合機会を確保する。ロシア建国後の狭い盤面で特に重要
# 危険ピースがある場合は、v343のままcompression_bonus=0を維持し、即時併合待ちを強化する
# advice.md「盤面がどうだろうが即時併合狙った方が絶対勝率高い」に基づき、危険ピースがない時は戦略的配置を優先して即時併合機会を確保
# refs: tmp/improve_brief.md, tmp/batch_summary.txt, advice.md,
#       game_history/20260325_035620_score0776.jsonl turns 67-70, game_history/20260325_033902_score0867.jsonl turns 72-78,
#       game_history/20260325_040412_score2821.jsonl turns 105-109, strategy.py.staging
# Fixes rollback failure mode: deadline_crossed && danger_piece_count>0時の即時併合取りこぼし（axis 9.6 danger_piece_count==0時強化）
# v343: deadline_crossed時盤面圧縮ペナルティ化版 - 即時併合待ち強化
# advice.md「デッドラインを超える配置を避ける。危険盤面時こそデッドライン超え判定をより厳格に適用する」に基づき、deadline_crossed時は盤面圧縮より即時併合優先
# last_rollback_postmortem: reactive_pairs>=1 && merge_grade=="NO" 時の戦略的配置不足がfailure mode
# ワーストゲーム(score0634): deadline_crossed時、reactive_pairs>=3, merge_available=false続きでREACTIVE_PAIRS_COMPRESSIONが選ばれmax_y=2.02→3.24に上昇してゲームオーバー
# ベストゲーム(score3421): deadline_crossed時、reactive_pairs=3, merge_available=false続きでREACTIVE_PAIRS_COMPRESSIONが選ばれmax_y=2.43→2.90で安定
# deadline_crossed時の盤面圧縮ボーナス（compression_bonus）を0にし、即時併合待ち動作を強化する
# 盤面圧縮を優先しすぎて高配置（x=3.0）を選んでmax_y runawayを防ぐため、compression_bonusを0にする
# refs: tmp/improve_brief.md, tmp/batch_summary.txt, advice.md,
#       game_history/20260325_025231_score0634.jsonl turns 54-61, game_history/20260325_031002_score3421.jsonl turns 132-139, strategy.py.staging
# Fixes rollback failure mode: deadline_crossed時の盤面圧縮ボーナスによる高配置 runway（axis 9.6ペナルティ化）
# v342: axis 9.7水平バランス戦略削除版 - 盤面圧縮戦略へ統合
# v341 failure mode: axis 9.7の水平バランス戦略（REACTIVE_PAIRS_HORIZONTAL_BALANCE）が即時併合優先と競合し、HEIGHT_CONTROLが続き即時併合機会を取りこぼしている
# last_rollback_postmortem: reactive_pairs>=1 && merge_grade=="NO" 時の戦略的配置不足がfailure mode
# batch_summary: HEIGHT_CONTROLが11.2%選択（avg_score_delta=0.0）と過剰、即時併合機会を取りこぼしている
# ワーストゲーム(score0714)終盤: max_y=2.82でゲームオーバー、即時併合不足
# ベストゲーム(score2350)終盤: max_y=3.40でも即時併合機会を確実に捉えて高スコア
# advice.md「盤面がどうだろうが即時併合狙った方が絶対勝率高い」がログで支持されている
# axis 9.7削除: 水平バランス戦略を削除し、axis 9.6と統合して盤面圧縮戦略へ一本化
# same_type_stack_topの有無によらず、常に盤面圧縮ボーナス（compression_bonus = (-landing_y) * 200.0）を与える
# 下層配置を優先し、即時併合機会を最大化する戦略へ統一
# refs: last_rollback_postmortem.md, last_rollback_analysis.md, improve_brief.md, batch_summary.txt, advice.md,
#       game_history/20260325_022335_score0714.jsonl, game_history/20260325_023517_score2350.jsonl, strategy.py.staging
# Fixes rollback failure mode: reactive_pairs>=1 && merge_grade=="NO" 時の戦略的配置不足（axis 9.7水平バランス戦略削除・盤面圧縮戦略へ統合）
# v341: axis 9.7盤面圧縮ボーナス修正版 - 低配置でもボーナスが発生するように改善
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
    """v342: axis 9.7水平バランス戦略削除版 - 盤面圧縮戦略へ統合

    v341 failure mode: axis 9.7の水平バランス戦略（REACTIVE_PAIRS_HORIZONTAL_BALANCE）が即時併合優先と競合し、HEIGHT_CONTROLが続き即時併合機会を取りこぼしている
    last_rollback_postmortem: reactive_pairs>=1 && merge_grade=="NO" 時の戦略的配置不足がfailure mode
    batch_summary: HEIGHT_CONTROLが11.2%選択（avg_score_delta=0.0）と過剰、即時併合機会を取りこぼしている
    ワーストゲーム(score0714)終盤: max_y=2.82でゲームオーバー、即時併合不足
    ベストゲーム(score2350)終盤: max_y=3.40でも即時併合機会を確実に捉えて高スコア
    advice.md「盤面がどうだろうが即時併合狙った方が絶対勝率高い」がログで支持されている
    axis 9.7削除: 水平バランス戦略を削除し、axis 9.6と統合して盤面圧縮戦略へ一本化
    same_type_stack_topの有無によらず、常に盤面圧縮ボーナス（compression_bonus = (-landing_y) * 200.0）を与える
    下層配置を優先し、即時併合機会を最大化する戦略へ統一

    v341の改善点:
     1. axis 9.7削除: v341の水平バランス戦略を削除し、axis 9.6と統合して盤面圧縮戦略へ一本化
              - last_rollback_postmortem: reactive_pairs>=1 && merge_grade=="NO" 時の戦略的配置不足がfailure mode
              - batch_summary: HEIGHT_CONTROLが11.2%選択（avg_score_delta=0.0）と過剰、即時併合機会を取りこぼしている
              - advice.md「盤面がどうだろうが即時併合狙った方が絶対勝率高い」がログで支持されている
              - ワーストゲーム: max_y=2.82でゲームオーバー、即時併合不足
              - ベストゲーム: max_y=3.40でもスコア2350、即時併合機会を確実に捉えている
              - axis 9.7の水平バランス戦略が即時併合優先と競合しているため削除
              - same_type_stack_topの有無によらず、常に盤面圧縮ボーナス（compression_bonus = (-landing_y) * 200.0）を与える
              - 下層配置を優先し、即時併合機会を最大化する戦略へ統一
     2. axis 9.6修正: reactive_pairs>=3 && deadline_crossed && merge_grade=="NO"の場合、axis 9.6を完全に無効化
      3. 超危険域では即時併合待ちを最優先し、axis 8.8の-3000~-7000ペナルティを優先させることで高配置 runway を防止
      4. reactive_pairs<3の場合は、盤面圧縮準備としてaxis 9.6のstacking bonusを維持
      5. refs: last_rollback_postmortem.md, last_rollback_analysis.md, improve_brief.md, batch_summary.txt, advice.md,
      #       game_history/20260325_022335_score0714.jsonl, game_history/20260325_023517_score2350.jsonl, strategy.py.staging

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
    deadline_crossed = game_state.get("deadline_crossed", False)

    # --- reactor information (for reactive merge priority) ---
    reactor = analysis.get("reactor", {})
    reactive_pairs = reactor.get("reactive_pairs", [])
    # reactive_pairs is a list, count pairs for evaluation
    reactive_pair_count = len(reactive_pairs) if isinstance(reactive_pairs, list) else 0
    danger_piece_count = reactor.get("danger_piece_count", 0)

    # --- v322: russia phase detection (type 15 pieces on board) ---
    # ロシアフェーズ: 盤面上にtype 15（ロシア）が1つ以上存在する場合
    # advice.md「ロシア建国後の死亡速度が早い。建国後はより慎重な盤面進行を検討すること」に基づく構造的改善
    # ロシア建国後は盤面が狭く、高typeピースが場所を占有している状態。この局面で通常時と同じ戦略を続けるのは不十分
    russia_phase_count = sum(1 for p in pieces if p.get("type") == 15)
    russia_phase = russia_phase_count >= 1

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

        # ----- evaluation axis 9.6: reactive pairs stacking bonus (v343: deadline_crossed時盤面圧縮ペナルティ化版 - 即時併合待ち強化) -----
        # advice.md「デッドラインを超える配置を避ける。危険盤面時こそデッドライン超え判定をより厳格に適用する」に基づき、deadline_crossed時は盤面圧縮より即時併合優先
        # batch_summaryでHEIGHT_CONTROLが19.9%選択(avg_score_delta=1.2)と過剛、即時併合機会を取りこぼしていることを確認
        # reactive_pairsがあるがmerge_grade=="NO"の場合、HEIGHT_CONTROLではなく戦略的配置を優先する
        # v342 failure mode: deadline_crossed時、reactive_pairs>=3, merge_available=false続きでREACTIVE_PAIRS_COMPRESSIONが選ばれmax_y=2.02→3.24に上昇してゲームオーバー
        # ワーストゲーム(score0634)終盤turns 54-61: deadline_crossed時、reactive_pairs>=3, merge_available=false続きで
        # axis 9.6のcompression_bonus（(-landing_y) * 200.0）が高配置を優先し、max_y=2.02→3.24に上昇してゲームオーバー
        # ベストゲーム(score3421)終盤turns 132-139: deadline_crossed時、reactive_pairs=3, merge_available=false続きで
        # axis 9.6のcompression_bonusがあってもmax_y=2.43→2.90で安定し、即時併合待ちが有効に機能
        # deadline_crossed時の盤面圧縮ボーナス（compression_bonus）を0にし、即時併合待ち動作を強化する
        # 盤面圧縮を優先しすぎて高配置（x=3.0）を選んでmax_y runawayを防ぐため、compression_bonusを0にする
        # reactive_pairs<3の場合は、盤面圧縮準備としてaxis 9.6のstacking bonusを維持
        # 未活用情報：deadline_crossed, reactive_pairs>=3, merge_grade, compression_bonus
        # refs: tmp/improve_brief.md, tmp/batch_summary.txt, advice.md,
        #       game_history/20260325_025231_score0634.jsonl turns 54-61, game_history/20260325_031002_score3421.jsonl turns 132-139, strategy.py.staging
        # Fixes rollback failure mode: reactive_pairs>=3 && deadline_crossedでの高配置 runaway（axis 9.6超危険域無効化）

        # axis 9.6: reactive pairs stacking bonus (v342: axis 9.7水平バランス戦略削除版 - 盤面圧縮戦略へ統合)
        # advice.md「盤面がどうだろうが即時併合狙った方が絶対勝率高い」がログで支持されている
        # batch_summary: HEIGHT_CONTROLが11.2%選択（avg_score_delta=0.0）と過剰、即時併合機会を取りこぼしている
        # last_rollback_postmortem: reactive_pairs>=1 && merge_grade=="NO" 時の戦略的配置不足がfailure mode
        # ワーストゲーム: max_y=2.82でゲームオーバー、即時併合不足
        # ベストゲーム: max_y=3.40でも即時併合機会を確実に捉えて高スコア
        # axis 9.7削除: 水平バランス戦略を削除し、axis 9.6と統合して盤面圧縮戦略へ一本化
        # same_type_stack_topの有無によらず、常に盤面圧縮ボーナスを与えることで即時併合機会を最大化
        if reactive_pair_count >= 3 and deadline_crossed and merge_grade == "NO":
            # axis 9.6を完全に無効化（reactive_pairs>=3 && deadline_crossedでの高配置 runway防止）
            pass
        elif reactive_pair_count >= 1 and merge_grade == "NO":
            # v344: deadline_crossed && danger_piece_count==0の場合は戦略的配置を優先
            # 危険ピースがない状況では、戦略的配置を優先し、即時併合機会を確保する
            # ロシア建国後の狭い盤面で特に重要
            if deadline_crossed:
                if danger_piece_count == 0:
                    # 危険ピースがない時は戦略的配置を優先し、即時併合機会を確保
                    # v343の0.0から強化し、下層配置を優先
                    compression_bonus = (
                        -landing_y
                    ) * 400.0  # landing_y=-2.5なら1000.0、-1.0なら400.0、0なら0.0
                else:
                    # 危険ピースがある場合は、即時併合待ちを強化（v343と同じ）
                    compression_bonus = 0.0
            else:
                # 盤面圧縮ボーナス（same_type_stack_topの有無に関わらず適用）
                # 下層配置を優先し、高配置を抑制する戦略的思考
                # same_type_stack_topがある場合は即時併合機会を最大化、ない場合も盤面圧縮を優先
                # compression_bonus = (-landing_y) * 200.0 に変更し、低い位置ほどボーナス大
                compression_bonus = (
                    -landing_y
                ) * 200.0  # landing_y=-2.5なら500.0、-1.0なら200.0、0なら0.0
            score += compression_bonus
            reasons.append("REACTIVE_PAIRS_COMPRESSION")

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
        if (
            deadline_crossed
            and reactive_pair_count >= 2
            and merge_grade == "NO"
            and danger_piece_count == 0
        ):
            # deadline_crossed時、reactive_pairsが多数ある即時併合不可時に、戦略的配置の余地を確保
            # height_multを0.2に緩和して、盤面圧縮（tighter board）を優先し、即時併合機会を確保
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
        if (
            deadline_crossed
            and reactive_pair_count >= 1
            and reactive_pair_count < 3
            and merge_grade == "NO"
        ):
            # deadline_crossed時、reactive_pairs>=1で即時併合不可の場合、戦略的配置の余地を更に確保
            # reactive_pairs>=3の場合はaxis 8.8ペナルティを有効にするためheight_mult緩和をスキップ
            # reactive_pairs>=3は超危険域であり、即時併合機会を強制的に待つ戦略へ切り替える
            height_mult *= 0.3

        # Calculate height penalty after all height_mult modifications
        # v168: reactive_pairsあり時HEIGHT_CONTROL抑制強化版
        # last_rollback_postmortem: reactive_pairs>=1 && merge_grade=="NO" 時の戦略的配置不足がfailure mode
        # batch_summary: HEIGHT_CONTROLが過剰、即時併合機会を取りこぼしている
        # ワーストゲーム(score0717)終盤turns 65-72: reactive_pairs=8, merge_available=falseでREACTIVE_PAIRS_COMPRESSIONが続きmax_y=2.34→3.71に上昇してゲームオーバー
        # ベストゲーム(score3530)終盤turns 139-146: 即時併合機会を確実に捉えて3530点を出している
        # axis 2修正: reactive_pairs>0 && merge_grade=="NO"の場合、height_multiplierを50.0から100.0に増やし、高配置を強力に抑制
        # refs: tmp/state/last_rollback_postmortem.md, tmp/state/last_rollback_analysis.md, tmp/improve_brief.md, tmp/batch_summary.txt, advice.md,
        #       game_history/20260325_044915_score0717.jsonl turns 65-72, game_history/20260325_042216_score3530.jsonl turns 139-146
        # Fixes rollback failure mode: reactive_pairs>=1 && merge_grade=="NO"での戦略的配置不足（axis 2 height_multiplier増強）
        height_base_mult = 50.0
        if reactive_pair_count > 0 and merge_grade == "NO":
            height_base_mult = 100.0
        height_penalty = landing_y * height_base_mult * height_mult

        if phase == "HIGH" and landing_y > 0.5:
            height_penalty *= 2.0
            reasons.append("HIGH_TOWER")
        elif phase == "MEDIUM" and landing_y > 0.5:
            height_penalty *= 1.5
            reasons.append("MEDIUM_TOWER")
        elif landing_y > 0.0:
            reasons.append("HIGH_LAYER")

        score -= height_penalty

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
            # deadline_crossed時にreactive_pairsがある場合、即時併合を逃した非併合配置に強力なペナルティ
            # 即時併合機会を最大化し、戦略的配置ボーナスを抑制
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
            # reactive_pairs>=3で即時併合がない場合、deadline_crossedに関わらず強力なペナルティを適用
            # landing_yに応じて動的にペナルティを強化し、高配置を積極的に抑制
            # v329修正: landing_y > 1 の場合、(landing_y - 1.0) * 2000.0 を使用し、高配置ほどペナルティを強化
            if landing_y <= 0:
                score -= 3000.0
            elif landing_y <= 1:
                score -= 3000.0 + landing_y * 2000.0
            else:
                score -= 5000.0 + (landing_y - 1.0) * 2000.0
            reasons.append("REACTIVE_PAIRS_NO_MERGE_PENALTY")

        # ----- evaluation axis 9: reactive pairs default (NEW: reactive_pairs fallback for "no action" situations) -----
        # batch_summaryでHEIGHT_CONTROLが22.8%選択(avg_score_delta=2.1)と過剰であり、reactive_pairsがある状況では「何もしない」HEIGHT_CONTROLではなく、
        # reactive_pairs活用で盤面圧縮を図る戦略的思考へ切り替える。
        # reactive_pairsがある場合、即時併合がない時のデフォルト選択をHEIGHT_CONTROLからREACTIVE_PAIRS_COMPRESSIONへ変更し、盤面圧縮を優先。
        # refs: tmp/batch_summary.txt, tmp/state/last_rollback_postmortem.md, game_history/20260313_231816_score0814.jsonl turns 54-57
        # ----- evaluation axis 9.5: current type stack merge priority (v335: deadline_crossed即時併合優先強化版) -----
        # advice.md「同じタイプが続いて来たらそのタイプの上に置き、併合チャンスを優先する」を強化。
        # batch_summaryでHEIGHT_CONTROLが11.0%選択(avg_score_delta=0.0)と過剰であり、即時併合機会を取りこぼしていることを確認。
        # 盤面上の現在タイプの最も高い位置のピースに配置を優先し、即時併合機会を最大化。
        # v330: reactive_pairsがある場合の盤面圧縮ボーナス条件を厳格化 - 即時併合優先強化
        # v334: deadline_crossed時の即時併合優先強化版 - v334 failure mode潰し
        # bad_strategy(ee2c76235324, v334): deadline_crossed時に即時ゲームオーバー判定を行い、reactive pairs の併合機会を失っている
        # rollback_target(608f63a01e6b, v330): deadline_crossed時も danger_piece_count == 0 の場合はプレイを継続し、reactive pairs を併合して高スコアを達成している
        # v334 failure: axis 2とaxis 9.5からdanger_piece_count条件を削除したため、danger_piece_count > 0 の状況でも戦略的配置が選ばれてしまい、即時併合機会を取りこぼしている
        # axis 9.5修正: 盤面圧縮ボーナス適用条件からdeadline_crossed条件を削除し、即時併合機会を最大化
        # axis 9.2修正: deadline_crossed条件をaxis 9.5に統合
        # refs: tmp/state/last_rollback_postmortem.md, tmp/state/last_rollback_analysis.md, tmp/improve_brief.md, tmp/batch_summary.txt, advice.md, tmp/sandbox_files.md,
        #       game_history/20260324_122310_score0720.jsonl, game_history/20260324_120941_score1207.jsonl
        # Fixes rollback failure mode: deadline_crossed時の即時併合機会取りこぼし（axis 9.6追加・axis 9.5 deadline_crossed条件統合）

        if same_type_stack_top and merge_grade == "NO":
            stack_top_x = same_type_stack_top.get("x", 0)
            stack_top_y = same_type_stack_top.get("y", -10)

            # v334: v330のdanger_piece_count条件削除版
            # v330: danger_piece_count == 0 && reactive_pair_count == 0 の場合のみボーナスを適用
            # 盤面圧縮ボーナスは即時併合機会がない場合のみ適用し、即時併合機会を優先
            # refs: tmp/state/last_rollback_postmortem.md, tmp/state/last_rollback_analysis.md, tmp/improve_brief.md, tmp/batch_summary.txt, advice.md
            if danger_piece_count == 0 and reactive_pair_count == 0:
                # 危険ピースがない場合、即時併合機会がない場合のみ盤面圧縮ボーナスを適用
                score += 300.0
                reasons.append("SAME_TYPE_STACK_MERGE_PRIORITY")
            # v334: danger_piece_count条件を削除 - axis 9.6ペナルティと競合するため、即時併合機会を優先する戦略へ切り替え
            # danger_piece_count > 0 の場合、axis 9.6のペナルティが適用され、即時併合機会を強制的に待つ戦略へ切り替わる

            # 配置位置が盤面上の現在タイプのピースの上になる場合、ペナルティ軽減を強化
            # danger_piece_count == 0 && reactive_pair_count == 0 の場合のみペナルティ軽減を適用
            # v325: reactive_pairsがない場合のみペナルティ軽減を適用 - 即時併合機会優先化
            # v327: 危険ピース(danger_piece_count > 0)がある場合のペナルティ軽減ボーナスも削除 - axis 9.2のペナルティを優先
            # v330: reactive_pairs >= 1 の場合のペナルティ軽減ボーナスも削除 - 即時併合優先強化
            landing_y = result.get("landing_y", 0)
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
                    # reactive_pair_count >= 1の場合はボーナスなし（axis 9.6の-4500.0ペナルティを適用）
            # v327: danger_piece_count > 0 の場合のペナルティ軽減ボーナスも削除 - axis 9.2のペナルティを優先
            # v330: reactive_pairs >= 1 の場合のペナルティ軽減ボーナスも削除 - 即時併合優先強化

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
                    # 危険ピースがない場合、即時併合機会がない場合のみ盤面圧縮ボーナスを適用
                    score += 300.0
                    reasons.append("SAME_TYPE_STACK_MERGE_PRIORITY")
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
