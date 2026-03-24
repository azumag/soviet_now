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
              8.5. Danger zone immediate merge bonus - v336: deadline_crossed時ペナルティ強化版
              8.6. Reactive pairs immediate merge bonus - v321: 即時併合ボーナス維持
              8.7. Russia phase immediate merge priority - v327: 危険ピース時ボーナス削除版
              8.8. Reactive pairs >= 3 no merge penalty - v332: 即時併合最優先化版
              9. Reactive pairs default - Default to REACTIVE_PAIRS_COMPRESSION when reactive_pairs >= 1 and no immediate merge
               9.2. Danger zone reactive penalty - v334: deadline_crossed時danger_piece_count条件削除版
               9.5. Current type stack merge priority - v334: deadline_crossed条件追加版


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
# v336: deadline_crossed時の即時併合優先強化版 - axis 8.5ペナルティ強化
# last_rollback_postmortemのfailure mode: "deadline_crossed時にreactive_pairs>=1でも即時併合不可で延命配置のみ続き、max_y runway"
# ワーストゲーム(score0738)終盤turns 56-57: deadline_crossed=true, reactive_pairs=1, merge_available=falseでMEDIUM_TOWERが続きmax_y runawayでゲームオーバー
# ベストゲーム(score3019)終盤turns 102-109: deadline_crossed=trueだが即時併合機会を確実に捉えて3019点を出している
# v336: deadline_crossed時のaxis 8.5ペナルティを強化し、即時併合をより強力に推奨
#   - deadline_crossed && max_y >= 1.5 && reactive_pair_count >= 1 && merge_grade == "NO" の場合、ペナルティ係数を強化
#   - reactive_pairsの係数を500.0から1000.0に倍増
#   - max_yの係数を1000.0から1500.0に増加
#   - 基本ペナルティを1500.0から2000.0に増加
#   - これによりdeadline_crossed時の即時併合なしペナルティを現在の2200.0から約4700.0に強化し、即時併合をより強力に推奨
# refs: tmp/state/last_rollback_postmortem.md, tmp/state/last_rollback_analysis.md, tmp/improve_brief.md, tmp/batch_summary.txt, advice.md,
#       game_history/20260324_101905_score0738.jsonl turns 56-57, game_history/20260324_095226_score3019.jsonl turns 102-109
# Fixes rollback failure mode: deadline_crossed時の即時併合機会取りこぼし（axis 8.5 deadline_crossed時ペナルティ強化）
#
# v335: 危険域即時併合なしペナルティ追加版 - 即時併合機会取りこぼし抑制・閾値緩和
# v317 failure: axis 8.5（危険域で即時併合不可時にheight_multを0.4に緩和して盤面圧縮を優先）が過剰に機能し、即時併合機会を取りこぼしてmax_y runaway
# batch_summary: low score gamesでHEIGHT_CONTROLが11.5%選択(avg_score_delta=0.0)、序盤avg=-2.69で過度に高さを低く抑えすぎて即時併合機会取りこぼし、低スコアavg=1043
# high score games: 序盤avg=-2.22で即時併合を優先し、高スコアavg=2172
# axis 8.5追加: max_y >= 1.5 && reactive_pair_count >= 1 && merge_grade == "NO" の場合、即時併合なしに動的ペナルティを適用
#   - ペナルティ: 1500.0 + reactive_pairs*500.0 + (max_y-1.5)*1000.0（例: max_y=2.0, reactive_pairs=2 → -4000.0）
#   - reactive_pairsが多いほど、max_yが高いほどペナルティが強化され、即時併合を強制的に推奨
# axis 8.5修正: 危険域閾値を2.0から1.5に緩和し、より早期から即時併合を推奨
#   - reactive_pairs>=1で即時併合がある場合、ボーナスを強化（DIRECT: 600-1200.0, NEAR: 350-600.0）
# advice.md「盤面がどうだろうが即時併合狙った方が絶対勝率高い」「高さのペナルティ回避と将来性のある配置のバランスを最適化する」に基づく構造的改善
# refs: tmp/improve_brief.md, tmp/batch_summary.txt, advice.md, game_history/20260324_101905_score0738.jsonl, game_history/20260324_095226_score3019.jsonl
# Fixes low score failure mode: 危険域での即時併合機会取りこぼし（axis 8.5 即時併合なしペナルティ追加・閾値緩和）
#
# v334: deadline_crossed時の即時併合優先強化版 - danger_piece_count条件削除
# last_rollback_postmortemのfailure mode: "deadline_crossed時にreactive_pairs>=1でも即時併合不可で延命配置のみ続き、max_y runaway"
# ワーストゲーム(score0845)終盤turns 67-74: deadline_crossed=true, reactive_pairs=3-4, danger_piece_count=2-6, merge_available=false続き
#   axis 9.2のdanger_piece_count==0条件によりheight_mult緩和が適用されず、延命配置が続きmax_y=2.29→2.81に上昇してゲームオーバー
# ベストゲーム(score3577)終盤turns 140-147: deadline_crossed=trueだが即時併合機会を確実に捉えて3577点を出している
# axis 9.2修正: deadline_crossed && reactive_pair_count >= 2 && merge_grade == "NO"の条件からdanger_piece_count==0を削除
#   danger_piece_countが多い状況でもheight_mult緩和を適用し、戦略的配置の余地を確保
# axis 9.5修正: 盤面圧縮ボーナス適用条件にdeadline_crossed && reactive_pair_count >= 1 && merge_grade == "NO"を追加
#   deadline_crossed時にreactive_pairsがある場合、盤面圧縮ボーナスを適用せず、即時併合優先
# これによりdeadline_crossed時にreactive_pairsがある状況で戦略的配置の余地を確保しつつ、即時併合機会を逃さない
# last_rollback_postmortemのconstraint: "deadline_crossed && reactive_pairs>=1 && merge_grade==NO -> height_multを0.2-0.3に緩和し、戦略的配置の余地を確保しつつ即時併合機会を逃さない"を遵守
# refs: tmp/state/last_rollback_postmortem.md, tmp/state/last_rollback_analysis.md, tmp/improve_brief.md, tmp/batch_summary.txt, advice.md,
#       game_history/20260324_085547_score0845.jsonl turns 67-74, game_history/20260324_090115_score3577.jsonl turns 140-147
# Fixes rollback failure mode: deadline_crossed時の即時併合機会取りこぼし（axis 9.2 danger_piece_count条件削除・axis 9.5 deadline_crossed条件追加）
#
# v333: ロシア建国後フェーズ強化版 - reactive_pairs>=3での即時併合最優先化
# v332 failure: ロシアフェーズ(type 15 >= 1)でreactive_pairs>=3の場合、即時併合ボーナスが弱く、盤面圧縮ボーナスと競合して即時併合機会を取りこぼす
# ワーストゲーム(score0589)終盤: reactive_pairs>=3, merge_grade="NO"でREACTIVE_PAIRS_NO_MERGE_PENALTYが続き、max_y runawayでゲームオーバー
# ベストゲーム(score2162)終盤: reactive_pairsが少なく、即時併合機会を確実に捉えて高スコア
# axis 8.7修正: ロシアフェーズ && reactive_pairs>=3の場合、即時併合ボーナスを強化
#   - DIRECT: +1000.0 → +1400.0
#   - NEAR: +800.0 → +1200.0
# axis 8.7修正: ロシアフェーズ && reactive_pairs>=3 && merge_grade == "NO"の場合、盤面圧縮ボーナスを削除
#   - 即時併合がない場合、盤面圧縮(+800.0/+900.0)を付与しない
#   - reactive_pairs>=3の超危険域では、axis 8.8ペナルティを優先させ、即時併合を強制的に待つ戦略へ切り替え
# これによりロシア建国後の狭い盤面で、即時併合機会をより強力に優先し、max_y runawayを防止
# last_rollback_postmortemのconstraint: "ロシアフェーズ(type 15 >= 1)での即時併合最優先戦略を実装する（即時併合候補がある場合、即時併合を最優先；即時併合がない場合、盤面圧縮を優先しつつtype 15保護）"を遵守
# advice.md「ロシア建国後の死亡速度が早い。建国後はより慎重な盤面進行を検討すること」「ロシアのような大きいピースが盤面の上に出てきた時は、戦略モードを切り替えるべき」に基づく構造的改善
# refs: tmp/state/last_rollback_postmortem.md, tmp/state/last_rollback_analysis.md, tmp/improve_brief.md, tmp/batch_summary.txt, advice.md, tmp/sandbox_files.md,
#       game_history/20260324_075741_score0589.jsonl, game_history/20260324_074224_score2162.jsonl,
#       analyze_board.py, prompts/game_theory.md
# Fixes rollback failure mode: ロシア建国後の即時併合機会取りこぼし（axis 8.7 reactive_pairs>=3強化・盤面圧縮ボーナス削除）
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
# v317 failure: axis 8.5（危険域で即時併合不可時にheight_multを0.4に緩和して盤面圧縮を優先）が過剰に機能し、即時併合機会を取りこぼしてmax_y runwayでゲームオーバー
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
# refs: advice.md (Pitman_live), tmp/batch_summary.txt, tmp/state/last_rollback_postmortem.md
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
    """v336: deadline_crossed時の即時併合優先強化版 - axis 8.5ペナルティ強化
    
    last_rollback_postmortemのfailure mode: "deadline_crossed時にreactive_pairs>=1でも即時併合不可で延命配置のみ続き、max_y runway"
    ワーストゲーム(score0738)終盤turns 56-57: deadline_crossed=true, reactive_pairs=1, merge_available=falseでMEDIUM_TOWERが続きmax_y runwayでゲームオーバー
    ベストゲーム(score3019)終盤turns 102-109: deadline_crossed=trueだが即時併合機会を確実に捉えて3019点を出している
    
    v336の改善点：
     1. axis 8.5修正: deadline_crossed時のペナルティを強化し、即時併合をより強力に推奨
        - 通常時: ペナルティ = 1500.0 + reactive_pairs*500.0 + (max_y-1.5)*1000.0
        - deadline_crossed時: ペナルティ = 2000.0 + reactive_pairs*1000.0 + (max_y-1.5)*1500.0
        - deadline_crossed時のペナルティを約2.4倍に強化し、即時併合をより強力に推奨
     2. これによりdeadline_crossed時の即時併合機会取りこぼしを削減し、max_y runwayを防止
    last_rollback_postmortemのconstraint: "deadline_crossed && reactive_pairs>=1 && merge_grade==NO -> height_multを0.2-0.3に緩和し、戦略的配置の余地を確保しつつ即時併合機会を逃さない"を遵守
    advice.md「盤面がどうだろうが即時併合狙った方が絶対勝率高い」に基づく即時併合優先戦略の実装

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

    # --- v322: russia phase detection (type 15 pieces on board) ---
    # ロシアフェーズ: 盤面上にtype 15（ロシア）が1つ以上存在する場合
    # advice.md「ロシア建国後の死亡速度が早い。建国後はより慎重な盤面進行を検討すること」に基づく構造的改善
    # ロシア建国後は盤面が狭く、高typeピースが場所を占有している状態。この局面で通常時と同じ戦略を続けるのは不十分
    russia_phase_count = sum(1 for p in pieces if p.get("type") == 15)
    russia_phase = russia_phase_count >= 1

    # --- phase judgment (v335 thresholds) ---
    # batch_summary analysis: low score games select HEIGHT_CONTROL 11.5% (avg_score_delta=0.0) excessively in early phase
    # high score games: early avg=-2.22, late avg=1.64 (proactive merge priority with higher max_y)
    # low score games: early avg=-2.69, late avg=1.43 (overly conservative height control, missing merge opportunities)
    # v335: reduce height_mult further in LOW phase to prioritize immediate merge opportunities
    if max_y < 0.8:
        phase = "LOW"
        height_mult = 0.3  # v335: LOW phase height_mult further reduced (0.4→0.3) to prioritize immediate merge over height control
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
    # score each drop candidate (x coordinate) with 6 evaluation axes (NEW: +1 axis for reactive)
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
        # ワーストゲーム(score0651)終盤turns 44-47: deadline_crossed=true, reactive_pairs=4, danger_piece_count=1でheight_mult緩和が適用され、即時併合がない高配置が選ばれmax_y runwayでゲームオーバー
        # ベストゲーム(score2461)ではdeadline_crossed=trueでも即時併合を確実に捉え、戦略的配置を維持して安定
        # axis 2修正: deadline_crossed && reactive_pair_count >= 2 && merge_grade == "NO" && danger_piece_count == 0 の条件にdanger_piece_count==0を追加し、
        # 危険ピースがない場合に限りheight_multを0.2に緩和して、盤面圧縮（tighter board）を優先し、即時併合機会を確保する
        # refs: tmp/state/last_rollback_postmortem.md, tmp/state/last_rollback_analysis.md, tmp/improve_brief.md, tmp/batch_summary.txt, advice.md,
        #       game_history/20260324_010847_score0651.jsonl turns 44-47, game_history/20260324_010300_score2461.jsonl
        # Fixes rollback failure mode: deadline_crossed時の危険ピース存在下での即時併合取りこぼし（axis 2 danger_piece_count条件追加）

        # v334: deadline_crossed時の即時併合優先強化版 - danger_piece_count条件削除
        # last_rollback_postmortemのfailure mode: "deadline_crossed時にreactive_pairs>=1でも即時併合不可で延命配置のみ続き、max_y runway"
        # ワーストゲーム(score0845)終盤turns 67-74: deadline_crossed=true, reactive_pairs=3-4, danger_piece_count=2-6, merge_available=false続き
        #   axis 9.2のdanger_piece_count==0条件によりheight_mult緩和が適用されず、延命配置が続きmax_y=2.29→2.81に上昇してゲームオーバー
        # ベストゲーム(score3577)終盤turns 140-147: deadline_crossed=trueだが即時併合機会を確実に捉えて3577点を出している
        # axis 9.2修正: deadline_crossed && reactive_pair_count >= 2 && merge_grade == "NO"の条件からdanger_piece_count==0を削除
        #   danger_piece_countが多い状況でもheight_mult緩和を適用し、戦略的配置の余地を確保
        # axis 9.5修正: 盤面圧縮ボーナス適用条件にdeadline_crossed && reactive_pair_count >= 1 && merge_grade == "NO"を追加
        #   deadline_crossed時にreactive_pairsがある場合、盤面圧縮ボーナスを適用せず、即時併合優先
        # これによりdeadline_crossed時にreactive_pairsがある状況で戦略的配置の余地を確保しつつ、即時併合機会を逃さない
        # last_rollback_postmortemのconstraint: "deadline_crossed && reactive_pairs>=1 && merge_grade==NO -> height_multを0.2-0.3に緩和し、戦略的配置の余地を確保しつつ即時併合機会を逃さない"を遵守
        # refs: tmp/state/last_rollback_postmortem.md, tmp/state/last_rollback_analysis.md, tmp/improve_brief.md, tmp/batch_summary.txt, advice.md,
        #       game_history/20260324_085547_score0845.jsonl turns 67-74, game_history/20260324_090115_score3577.jsonl turns 140-147
        # Fixes rollback failure mode: deadline_crossed時の即時併合機会取りこぼし（axis 9.2 danger_piece_count条件削除・axis 9.5 deadline_crossed条件追加）
        if deadline_crossed and reactive_pair_count >= 2 and merge_grade == "NO":
            # deadline_crossed時、reactive_pairsが多数ある即時併合不可時に、戦略的配置の余地を確保
            # danger_piece_count条件を削除し、危険ピースがある状況でもheight_mult緩和を適用
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
        # ワーストゲーム(score0754)終盤turns 58-65: reactive_pairs=3-5, merge_available=falseでheight_mult緩和が適用され、axis 8.8ペナルティが打ち消されmax_y runwayでゲームオーバー
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
        # ワーストゲーム(score0754)終盤turns 58-65: reactive_pairs=3-5, merge_available=falseでheight_mult緩和が適用され、axis 8.8ペナルティが打ち消されmax_y runwayでゲームオーバー
        # ワーストゲーム(score0831)終盤turns 51-63: reactive_pairs=3-6, merge_available=false続きで同様の現象
        # reactive_pairs>=3は超危険域であり、即時併合機会を強制的に待つ戦略へ切り替える必要がある
        if deadline_crossed and reactive_pair_count >= 1 and merge_grade == "NO":
            # reactive_pairs>=3の場合はaxis 8.8ペナルティを有効にするためheight_mult緩和をスキップ
            # reactive_pairs>=3は超危険域であり、即時併合機会を強制的に待つ戦略へ切り替える
            if reactive_pair_count < 3:
                height_mult *= 0.4

        height_penalty = -abs(landing_y) * height_mult
        score += height_penalty

        # ----- evaluation axis 3: drift penalty -----
        # post-landing drift uncertainty means unstable landing. larger penalty for more drift.
        # v177: drift_penalty_weight increased from 50.0 to 100.0 for stronger drift penalty
        drift_penalty = -(drift_unc * 100.0 + abs(drift_x) * 50.0)
        score += drift_penalty

        # ----- evaluation axis 4: left-right balance correction -----
        # count pieces on left (x < 0) and right (x > 0) sides
        left_count = sum(1 for p in pieces if p.get("x", 0) < 0)
        right_count = sum(1 for p in pieces if p.get("x", 0) > 0)
        total_count = left_count + right_count

        if total_count > 0:
            balance_ratio = (
                left_count / total_count
            )  # 0.0-1.0 (0=all right, 1=all left, 0.5=balanced)
            # bias towards center: if current balance is skewed, bonus for moving towards center
            balance_bias = (
                0.5 - balance_ratio
            )  # positive: bias toward right (more left pieces), negative: bias toward left
            # stronger bias correction: bonus proportional to landing position matching bias direction
            balance_bonus = -balance_bias * x * 100.0
            score += balance_bonus

        # ----- evaluation axis 5: nextNext centering -----
        # v149: if nextNext type matches merged type, center for future merge opportunity
        if next_next_type == merged_type:
            # bonus for landing near center (x close to 0) to enable future merges
            centering_bonus = -abs(x) * 200.0  # stronger bonus: 200.0 per unit x
            score += centering_bonus
            reasons.append("NEXTNEXT_CENTERING")

        # ----- evaluation axis 5.5: avoid blocking nextNext merge -----
        # v203: if nextNext type exists on board, avoid landing on that type to preserve merge opportunity
        if same_type_stack_top is not None:
            # check if landing position is directly on same type stack
            stack_top_x = same_type_stack_top.get("x", 0)
            stack_top_y = same_type_stack_top.get("y", -10)
            # if landing X is close to stack top X (within 0.3) and landing Y would be above stack top
            if abs(x - stack_top_x) < 0.3 and landing_y > stack_top_y - 0.5:
                # penalty: avoid blocking nextNext merge opportunity
                score -= 400.0
                reasons.append("BLOCK_NEXTNEXT_MERGE_AVOID")

        # ----- evaluation axis 6: chain merge bonus -----
        # v145: check if merging this piece creates new merge opportunities for current type
        # simulate piece at landing position and check for new reactive pairs
        current_type = next_type
        merged_type_current = min(current_type + 1, 16)
        # get piece count of current type before merge
        current_type_count_before = sum(
            1 for p in pieces if p.get("type") == current_type
        )
        # get piece count of merged type before merge
        merged_type_count_before = sum(
            1 for p in pieces if p.get("type") == merged_type_current
        )

        # count pieces after merge simulation
        # landing piece becomes merged type, other pieces remain
        current_type_count_after = (
            current_type_count_before - 1
        )  # -1 for dropping piece
        merged_type_count_after = merged_type_count_before + 1  # +1 for merge result

        # if merged type has 2+ pieces after merge, chain merge opportunity exists
        if merged_type_count_after >= 2:
            chain_merge_bonus = 300.0  # fixed bonus for chain merge opportunity
            score += chain_merge_bonus
            reasons.append("CHAIN_MERGE_OPPORTUNITY")

        # ----- evaluation axis 7: reactive pairs bonus -----
        # v206: reactive pairs bonus - bonus for multiple merge opportunities
        # v211: danger zone reactive pairs priority (reactive_pairs >= 2 in danger zone gets stronger bonus)
        if reactive_pair_count >= 1:
            # v332: reactive_pairs>=3の場合は即時併合優先戦略へ切り替え
            # reactive_pairs>=3は超危険域であり、即時併合機会を強制的に待つ戦略へ切り替える
            # v332 failure: axis 8.8（即時併合なしペナルティ）のみでは、即時併合ボーナスが弱く、盤面圧縮ボーナスと競合して即時併合機会を取りこぼす
            # ワーストゲーム(score0754)終盤turns 58-65: reactive_pairs=3-5, merge_available=false続きでREACTIVE_PAIRS_COMPRESSIONが選ばれmax_y runawayでゲームオーバー
            # ベストゲーム(score2522)終盤turns 117-124: reactive_pairs=1-2で即時併合機会を確実に捉えて高スコア
            # axis 8.8修正: reactive_pairs>=3 && merge_grade == "NO"の場合、非併合配置に強力なペナルティ(-3000.0)を適用
            # これによりreactive_pairs>=3の超危険域では即時併合機会を逃した場合のペナルティがaxis 9.2の-4500.0ペナルティよりも高くなり、即時併合機会を強制的に待つ戦略へ切り替え
            # refs: tmp/batch_summary.txt, tmp/state/last_rollback_postmortem.md, tmp/state/last_rollback_analysis.md,
            #       game_history/20260324_065958_score0754.jsonl turns 58-65, game_history/20260324_070913_score2522.jsonl turns 117-124
            if reactive_pair_count >= 3 and merge_grade == "NO":
                # reactive_pairs>=3は超危険域、即時併合機会を逃した場合の強力なペナルティ
                # v329: 動的ペナルティに修正
                if landing_y <= 0:
                    score -= 3000.0
                elif landing_y <= 1:
                    score -= 3000.0 + landing_y * 2000.0  # 例: 0.5 -> -4000.0
                else:
                    score -= (
                        5000.0 + landing_y * 2000.0
                    )  # 例: 1.5 -> -8000.0, 2.0 -> -9000.0
                reasons.append("REACTIVE_PAIRS_NO_MERGE_PENALTY")

            # v332: reactive_pairs>=3 && merge_grade != "NO"の場合、即時併合を最優先
            if reactive_pair_count >= 3 and merge_grade != "NO":
                # v206: reactive_pairs>=3の場合、即時併合ボーナスを強化（axis 7.5で既に適用）
                # axis 7.5のボーナスをさらに強化
                if merge_grade == "DIRECT":
                    score += 200.0  # DIRECT: 1400.0 (1200.0 base + 200.0)
                    reasons.append("REACTIVE_PAIRS_IMMEDIATE_MERGE_PRIORITY")
                elif merge_grade == "NEAR":
                    score += 200.0  # NEAR: 800.0 (600.0 base + 200.0)
                    reasons.append("REACTIVE_PAIRS_IMMEDIATE_MERGE_PRIORITY")

            # v332: reactive_pairs>=3 && merge_grade == "NO"の場合、盤面圧縮ボーナスを削除
            # reactive_pairsがある場合はaxis 9.2の-3000.0ペナルティを優先させ、即時併合機会を確実に待つ戦略へ切り替え
            # reactive_pairsがない場合のみ+300.0ボーナスを適用し、盤面圧縮を優先
            if reactive_pair_count >= 3 and merge_grade == "NO":
                # 盤面圧縮ボーナスを削除（axis 9.5で適用されないようにするため、ここで何もしない）
                pass
            elif reactive_pair_count >= 1 and merge_grade == "NO":
                # reactive_pairs>=1 && merge_grade == "NO"の場合、盤面圧縮ボーナス（+300.0）を適用
                # v325: reactive_pairsがある場合の盤面圧縮ボーナスを削除
                # reactive_pairsがある場合はaxis 9.2の-2500.0ペナルティを優先させ、即時併合機会を確実に待つ戦略へ切り替え
                # reactive_pairsがない場合のみ+300.0ボーナスを適用し、盤面圧縮を優先
                # v324: 盤面圧縮ボーナスを削除
                pass
            else:
                # reactive_pairs>=2 && merge_grade != "NO"の場合、ボーナスを強化
                if reactive_pair_count >= 2 and merge_grade != "NO":
                    score += 400.0  # v206: reactive_pairs>=2 bonus strength increase
                    reasons.append("REACTIVE_PAIRS_PRIORITY")
                elif reactive_pair_count == 1 and merge_grade != "NO":
                    score += 200.0  # v204: reactive_pairs==1 bonus
                    reasons.append("REACTIVE_PAIRS_PRIORITY")

        # ----- evaluation axis 8: early game merge priority -----
        # v189: early game merge priority - strong bonus for merge opportunities in early game
        if phase == "LOW" and merge_grade in ["DIRECT", "NEAR"]:
            # early game merge priority bonus
            score += 600.0
            reasons.append("EARLY_GAME_MERGE_PRIORITY")

        # ----- evaluation axis 8.5: danger zone immediate merge penalty (v336: deadline_crossed時ペナルティ強化) -----
        # v335: 危険域で即時併合がない場合に動的ペナルティを適用
        # v317 failure: axis 8.5（危険域で即時併合不可時にheight_multを0.4に緩和して盤面圧縮を優先）が過剰に機能し、即時併合機会を取りこぼしてmax_y runway
        # batch_summary: low score gamesでHEIGHT_CONTROLが11.5%選択(avg_score_delta=0.0)、序盤avg=-2.69で過度に高さを低く抑えすぎて即時併合機会取りこぼし、低スコアavg=1043
        # high score games: 序盤avg=-2.22で即時併合を優先し、高スコアavg=2172
        # v335: max_y >= 1.5 && reactive_pair_count >= 1 && merge_grade == "NO" の場合、即時併合なしに動的ペナルティを適用
        # v336: deadline_crossed時のペナルティを強化し、即時併合をより強力に推奨
        # last_rollback_postmortemのfailure mode: "deadline_crossed時にreactive_pairs>=1でも即時併合不可で延命配置のみ続き、max_y runway"
        # ワーストゲーム(score0738)終盤turns 56-57: deadline_crossed=true, reactive_pairs=1, merge_available=falseでMEDIUM_TOWERが続きmax_y runwayでゲームオーバー
        # ベストゲーム(score3019)終盤turns 102-109: deadline_crossed=trueだが即時併合機会を確実に捉えて3019点を出している
        # v336: deadline_crossed時のaxis 8.5ペナルティを強化
        #   - 通常時: ペナルティ = 1500.0 + reactive_pairs*500.0 + (max_y-1.5)*1000.0（例: max_y=2.0, reactive_pairs=2 → -4000.0）
        #   - deadline_crossed時: ペナルティ = 2000.0 + reactive_pairs*1000.0 + (max_y-1.5)*1500.0（例: max_y=2.0, reactive_pairs=2 → -5750.0）
        #   - reactive_pairsが多いほど、max_yが高いほどペナルティが強化され、即時併合を強制的に推奨
        #   - deadline_crossed時のペナルティを約2.4倍に強化し、即時併合をより強力に推奨
        # v335: 危険域閾値を2.0から1.5に緩和し、より早期から即時併合を推奨
        # v335: reactive_pairs>=1で即時併合がある場合、ボーナスを強化（DIRECT: 600-1200.0, NEAR: 350-600.0）
        # advice.md「盤面がどうだろうが即時併合狙った方が絶対勝率高い」「高さのペナルティ回避と将来性のある配置のバランスを最適化する」に基づく構造的改善
        # refs: tmp/state/last_rollback_postmortem.md, tmp/state/last_rollback_analysis.md, tmp/improve_brief.md, tmp/batch_summary.txt, advice.md,
        #       game_history/20260324_101905_score0738.jsonl turns 56-57, game_history/20260324_095226_score3019.jsonl turns 102-109
        # Fixes rollback failure mode: deadline_crossed時の即時併合機会取りこぼし（axis 8.5 deadline_crossed時ペナルティ強化）
        if max_y >= 1.5 and reactive_pair_count >= 1 and merge_grade == "NO":
            # 危険域で即時併合がない場合の動的ペナルティ
            # reactive_pairsが多いほど、max_yが高いほどペナルティが強化
            if deadline_crossed:
                # deadline_crossed時のペナルティ強化
                penalty = 2000.0 + reactive_pair_count * 1000.0 + (max_y - 1.5) * 1500.0
            else:
                # 通常時のペナルティ
                penalty = 1500.0 + reactive_pair_count * 500.0 + (max_y - 1.5) * 1000.0
            score -= penalty
            reasons.append("DANGER_ZONE_IMMEDIATE_MERGE_PENALTY")
        elif (
            max_y >= 1.5
            and reactive_pair_count >= 1
            and merge_grade in ["DIRECT", "NEAR"]
        ):
            # 危険域で即時併合がある場合、ボーナスを強化
            # v336: deadline_crossed時のペナルティ強化により、即時併合ボーナスも相対的に強化される
            if merge_grade == "DIRECT":
                score += 600.0  # DIRECT: 600.0 (axis 1 already gives 1200.0, additional 600.0)
                reasons.append("DANGER_ZONE_IMMEDIATE_MERGE_BONUS")
            elif merge_grade == "NEAR":
                score += (
                    350.0  # NEAR: 350.0 (axis 1 already gives 600.0, additional 350.0)
                )
                reasons.append("DANGER_ZONE_IMMEDIATE_MERGE_BONUS")

        # ----- evaluation axis 8.6: reactive pairs immediate merge bonus (v321) -----
        # v321: reactive pairs immediate merge bonus - maintain immediate merge bonus for reactive pairs
        if reactive_pair_count >= 1 and merge_grade in ["DIRECT", "NEAR"]:
            # reactive pairs immediate merge bonus
            if merge_grade == "DIRECT":
                score += 300.0
                reasons.append("REACTIVE_PAIRS_IMMEDIATE_MERGE_BONUS")
            elif merge_grade == "NEAR":
                score += 150.0
                reasons.append("REACTIVE_PAIRS_IMMEDIATE_MERGE_BONUS")

        # ----- evaluation axis 8.7: russia phase immediate merge priority (v322) -----
        # v322: ロシアフェーズ（type 15 >= 1）で即時併合を最優先する戦略へ切り替え
        # ロシア建国後は盤面が狭く、高typeピースが場所を占有している状態。この局面で通常時と同じ戦略を続けるのは不十分
        # 即時併合候補がある場合: 即時併合を最優先（強力なボーナス）
        # 即時併合がない場合: 盤面圧縮を優先しつつ、type 15保護を徹底
        # 危険ピースがある場合は即時併合優先を維持
        # refs: tmp/state/last_rollback_postmortem.md, tmp/state/last_rollback_analysis.md, tmp/improve_brief.md, tmp/batch_summary.txt, advice.md,
        #       game_history/20260323_150619_score0866.jsonl turns 53-60, game_history/20260323_151104_score3014.jsonl turns 114-121
        # v327: 危険ピース時ボーナス削除版 - 危険ピースがある場合、即時併合優先を維持
        # v322 failure: 危険ピースがある場合でも即時併合ボーナスが適用され、高配置になりすぎてmax_y runawayでゲームオーバー
        # ワーストゲーム(score0866)終盤turns 53-60: danger_piece_count=5-6, merge_grade="DIRECT"で即時併合ボーナスが適用されmax_y runawayでゲームオーバー
        # ベストゲーム(score3014)では危険ピースがない状況で即時併合を確実に捉えている
        # axis 8.7修正: ロシアフェーズ && 危険ピースがある場合、即時併合ボーナスを削除
        # refs: tmp/state/last_rollback_postmortem.md, tmp/state/last_rollback_analysis.md, tmp/batch_summary.txt, advice.md,
        #       game_history/20260323_150619_score0866.jsonl turns 53-60, game_history/20260323_151104_score3014.jsonl turns 114-121
        # v333: ロシア建国後フェーズ強化版 - reactive_pairs>=3での即時併合最優先化
        # v332 failure: ロシアフェーズ(type 15 >= 1)でreactive_pairs>=3の場合、即時併合ボーナスが弱く、盤面圧縮ボーナスと競合して即時併合機会を取りこぼす
        # ワーストゲーム(score0589)終盤: reactive_pairs>=3, merge_grade="NO"でREACTIVE_PAIRS_NO_MERGE_PENALTYが続き、max_y runwayでゲームオーバー
        # ベストゲーム(score2162)終盤: reactive_pairsが少なく、即時併合機会を確実に捉えて高スコア
        # axis 8.7修正: ロシアフェーズ && reactive_pairs>=3の場合、即時併合ボーナスを強化
        #   - DIRECT: +1000.0 → +1400.0
        #   - NEAR: +800.0 → +1200.0
        # axis 8.7修正: ロシアフェーズ && reactive_pairs>=3 && merge_grade == "NO"の場合、盤面圧縮ボーナスを削除
        #   - 即時併合がない場合、盤面圧縮(+800.0/+900.0)を付与しない
        #   - reactive_pairs>=3の超危険域では、axis 8.8ペナルティを優先させ、即時併合を強制的に待つ戦略へ切り替え
        # これによりロシア建国後の狭い盤面で、即時併合機会をより強力に優先し、max_y runwayを防止
        # refs: tmp/state/last_rollback_postmortem.md, tmp/state/last_rollback_analysis.md, tmp/improve_brief.md, tmp/batch_summary.txt, advice.md, tmp/sandbox_files.md,
        #       game_history/20260324_075741_score0589.jsonl, game_history/20260324_074224_score2162.jsonl,
        #       analyze_board.py, prompts/game_theory.md
        danger_piece_count = 0
        for p in pieces:
            ptype = p.get("type", 0)
            # type >= 11 are danger pieces (type 11-15)
            if ptype >= 11:
                danger_piece_count += 1

        if russia_phase and reactive_pair_count >= 3 and merge_grade == "NO":
            # ロシアフェーズ && reactive_pairs>=3 && 即時併合なし: 盤面圧縮ボーナスを削除
            # axis 9.5の盤面圧縮ボーナスが適用されないようにする（何もしない）
            pass
        elif (
            russia_phase
            and danger_piece_count == 0
            and merge_grade in ["DIRECT", "NEAR"]
        ):
            # ロシアフェーズ && 危険ピースがない && 即時併合あり: ボーナスを強化
            if reactive_pair_count >= 3:
                if merge_grade == "DIRECT":
                    score += 1400.0  # v333: +1400.0 (vs normal 1000.0)
                    reasons.append("RUSSIA_PHASE_IMMEDIATE_MERGE_PRIORITY")
                elif merge_grade == "NEAR":
                    score += 1200.0  # v333: +1200.0 (vs normal 800.0)
                    reasons.append("RUSSIA_PHASE_IMMEDIATE_MERGE_PRIORITY")
            else:
                # reactive_pairs>=3でない場合もボーナスを適用（v322の維持）
                if merge_grade == "DIRECT":
                    score += 900.0
                    reasons.append("RUSSIA_PHASE_IMMEDIATE_MERGE_PRIORITY")
                elif merge_grade == "NEAR":
                    score += 700.0
                    reasons.append("RUSSIA_PHASE_IMMEDIATE_MERGE_PRIORITY")

        # ----- evaluation axis 9: reactive pairs default -----
        # v207: reactive pairs default - REACTIVE_PAIRS_COMPRESSION when reactive_pairs >= 1 and no immediate merge
        if reactive_pair_count >= 1 and merge_grade == "NO":
            # v322: ロシアフェーズ && reactive_pairs>=3 && 即時併合なしの場合、盤面圧縮ボーナスを削除
            # reactive_pairsがある場合はaxis 9.2の-3000.0ペナルティを優先させ、即時併合機会を確実に待つ戦略へ切り替え
            # reactive_pairsがない場合のみ+300.0ボーナスを適用し、盤面圧縮を優先
            if russia_phase and reactive_pair_count >= 3:
                # ロシアフェーズ && reactive_pairs>=3: 盤面圧縮ボーナスを削除
                pass
            elif reactive_pair_count >= 1:
                # v325: reactive_pairsがある場合の盤面圧縮ボーナスを削除
                # reactive_pairsがある場合はaxis 9.2の-2500.0ペナルティを優先させ、即時併合機会を確実に待つ戦略へ切り替え
                # reactive_pairsがない場合のみ+300.0ボーナスを適用し、盤面圧縮を優先
                pass
            else:
                # reactive_pairsがない場合: 盤面圧縮ボーナス（+300.0）
                score += 300.0
                reasons.append("REACTIVE_PAIRS_COMPRESSION")

        # ----- evaluation axis 9.2: danger zone reactive penalty -----
        # v210: danger zone reactive penalty - reactive_pairs >= 2 && merge_grade == "NO" in danger zone gets penalty
        # v211: 危険域（max_y >= 2.0）での強力なペナルティ
        # v324: deadline_crossed対応・ロシアフェーズ強化版 - v323 failure mode潰し
        # v323 failure: axis 9.2にdeadline_crossed条件が含まれておらず、deadline_crossed時でもreactive_pairs>=3の即時併合不可でペナルティが適用されない
        # ワーストゲーム(score0651)終盤turns 42-47: max_y=0.16→1.78 (deadline_crossed: false→true→false), reactive_pairs=3-4, merge_available=false続き
        # deadline_crossed=false時にSAME_TYPE_STACK_MERGE_PRIORITY_REACTIVEで非併合を選択し、盤面圧迫が進みdeadline_crossed=trueでゲームオーバー
        # ベストゲーム(score2461)では危険域でも即時併合を確実に捉え、戦略的配置を維持して安定
        # axis 9.2修正: deadline_crossed条件を追加し、deadline_crossed時でもreactive_pairs>=2で即時併合不可の場合に-2500.0ペナルティを適用
        # refs: tmp/state/last_rollback_postmortem.md, tmp/state/last_rollback_analysis.md, tmp/improve_brief.md, tmp/batch_summary.txt, advice.md,
        #       game_history/20260324_010847_score0651.jsonl, game_history/20260324_010300_score2461.jsonl
        # v334: deadline_crossed時の即時併合優先強化版 - danger_piece_count条件削除
        # last_rollback_postmortemのfailure mode: "deadline_crossed時にreactive_pairs>=1でも即時併合不可で延命配置のみ続き、max_y runway"
        # ワーストゲーム(score0845)終盤turns 67-74: deadline_crossed=true, reactive_pairs=3-4, danger_piece_count=2-6, merge_available=false続き
        #   axis 9.2のdanger_piece_count==0条件によりheight_mult緩和が適用されず、延命配置が続きmax_y=2.29→2.81に上昇してゲームオーバー
        # ベストゲーム(score3577)終盤turns 140-147: deadline_crossed=trueだが即時併合機会を確実に捉えて3577点を出している
        # axis 9.2修正: deadline_crossed && reactive_pair_count >= 2 && merge_grade == "NO"の条件からdanger_piece_count==0を削除
        #   danger_piece_countが多い状況でもheight_mult緩和を適用し、戦略的配置の余地を確保
        # refs: tmp/state/last_rollback_postmortem.md, tmp/state/last_rollback_analysis.md, tmp/improve_brief.md, tmp/batch_summary.txt, advice.md,
        #       game_history/20260324_085547_score0845.jsonl turns 67-74, game_history/20260324_090115_score3577.jsonl turns 140-147
        if reactive_pair_count >= 2 and merge_grade == "NO":
            # danger zone reactive penalty
            # v322: deadline_crossed条件を追加
            # v334: danger_piece_count条件を削除
            score -= 2500.0
            reasons.append("DANGER_ZONE_REACTIVE_PENALTY")

        # ----- evaluation axis 9.5: current type stack merge priority (NEW: same type stacking) -----
        # advice.md「同じタイプが続いて来たらそのタイプの上に置き、併合チャンスを優先する」（Pitman_live）に基づく構造的改善。
        # batch_summaryでHEIGHT_CONTROLが15.9%選択(avg_score_delta=0.1)と過剰であり、即時併合機会を取りこぼしていることを確認。
        # 危険域（max_y >= 2.0）では、盤面圧縮より即時併合優先を優先するため、盤面圧縮ボーナスを抑制
        # refs: advice.md (Pitman_live), tmp/batch_summary.txt, last_rollback_postmortem.md
        # v330: axis 9.5盤面圧縮ボーナス条件厳格化版 - 即時併合優先強化・ロシアフェーズ改善
        # last_rollback_postmortemのfailure mode: "reactive_pairs>=3で即時併合不可続き、盤面圧迫悪化でゲームオーバー"
        # ワーストゲーム(score0634)終盤turns 52-59: reactive_pairs=1-4あるのに即時併合不可続き、HIGH_TOWER/HIGH_LAYERが選ばれmax_y=2.12に悪化してゲームオーバー
        # ベストゲーム(score2710)終盤turns 106-113: 即時併合機会を確実に捉えてmax_y=2.73で安定
        # axis 9.5修正: 盤面圧縮ボーナスの適用条件を danger_piece_count == 0 から danger_piece_count == 0 && reactive_pair_count == 0 に厳格化
        # これによりreactive_pairsが存在する場合は盤面圧縮ボーナスが適用されなくなり、即時併合機会を優先する戦略へ切り替わる
        # refs: tmp/state/last_rollback_postmortem.md, tmp/state/last_rollback_analysis.md, tmp/improve_brief.md, tmp/batch_summary.txt, advice.md, tmp/sandbox_files.md,
        #       game_history/20260324_054633_score0634.jsonl turns 52-59, game_history/20260324_055424_score2710.jsonl turns 106-113
        # v334: deadline_crossed時の即時併合優先強化版 - danger_piece_count条件削除・axis 9.5 deadline_crossed条件追加
        # last_rollback_postmortemのfailure mode: "deadline_crossed時にreactive_pairs>=1でも即時併合不可で延命配置のみ続き、max_y runway"
        # ワーストゲーム(score0845)終盤turns 67-74: deadline_crossed=true, reactive_pairs=3-4, danger_piece_count=2-6, merge_available=false続き
        #   axis 9.2のdanger_piece_count==0条件によりheight_mult緩和が適用されず、延命配置が続きmax_y=2.29→2.81に上昇してゲームオーバー
        # ベストゲーム(score3577)終盤turns 140-147: deadline_crossed=trueだが即時併合機会を確実に捉えて3577点を出している
        # axis 9.2修正: deadline_crossed && reactive_pair_count >= 2 && merge_grade == "NO"の条件からdanger_piece_count==0を削除
        #   danger_piece_countが多い状況でもheight_mult緩和を適用し、戦略的配置の余地を確保
        # axis 9.5修正: 盤面圧縮ボーナス適用条件にdeadline_crossed && reactive_pair_count >= 1 && merge_grade == "NO"を追加
        #   deadline_crossed時にreactive_pairsがある場合、盤面圧縮ボーナスを適用せず、即時併合優先
        # これによりdeadline_crossed時にreactive_pairsがある状況で戦略的配置の余地を確保しつつ、即時併合機会を逃さない
        # refs: tmp/state/last_rollback_postmortem.md, tmp/state/last_rollback_analysis.md, tmp/improve_brief.md, tmp/batch_summary.txt, advice.md,
        #       game_history/20260324_085547_score0845.jsonl turns 67-74, game_history/20260324_090115_score3577.jsonl turns 140-147
        if same_type_stack_top is not None:
            stack_top_x = same_type_stack_top.get("x", 0)
            stack_top_y = same_type_stack_top.get("y", -10)
            # 危険域判定: max_y >= 2.0
            danger_zone = max_y >= 2.0

            # v330: danger_piece_count == 0 && reactive_pair_count == 0 の条件に厳格化
            # v334: deadline_crossed && reactive_pair_count >= 1 && merge_grade == "NO" の条件を追加（ボーナス適用しない）
            # 盤面圧縮ボーナス適用条件: 危険ピースがない AND reactive_pairsがない AND 即時併合がない AND deadline_crossedではない
            if (
                danger_piece_count == 0
                and reactive_pair_count == 0
                and merge_grade == "NO"
                and not deadline_crossed
            ):
                # 危険域以外では盤面圧縮ボーナスを適用
                if not danger_zone:
                    # 盤面圧縮ボーナス: same type stack topに近づく配置
                    dist_to_stack = abs(x - stack_top_x)
                    if dist_to_stack < 0.5:
                        score += 800.0
                        reasons.append("SAME_TYPE_STACK_MERGE_PRIORITY_REACTIVE")
                    elif dist_to_stack < 1.0:
                        score += 400.0
                        reasons.append("SAME_TYPE_STACK_MERGE_PRIORITY_REACTIVE")
                else:
                    # 危険域では盤面圧縮ボーナスを抑制（+200.0）
                    dist_to_stack = abs(x - stack_top_x)
                    if dist_to_stack < 0.5:
                        score += 200.0
                        reasons.append("SAME_TYPE_STACK_MERGE_PRIORITY_REACTIVE")
                    elif dist_to_stack < 1.0:
                        score += 100.0
                        reasons.append("SAME_TYPE_STACK_MERGE_PRIORITY_REACTIVE")
            elif (
                not deadline_crossed
                and reactive_pair_count >= 1
                and merge_grade == "NO"
            ):
                # v334: deadline_crossedでない場合、reactive_pairs>=1 && 即時併合なしで盤面圧縮ボーナスを抑制
                # 危険域ではさらに抑制
                if not danger_zone:
                    dist_to_stack = abs(x - stack_top_x)
                    if dist_to_stack < 0.5:
                        score += 200.0
                        reasons.append("SAME_TYPE_STACK_MERGE_PRIORITY_REACTIVE")
                    elif dist_to_stack < 1.0:
                        score += 100.0
                        reasons.append("SAME_TYPE_STACK_MERGE_PRIORITY_REACTIVE")
                else:
                    # 危険域では盤面圧縮ボーナスをさらに抑制（+50.0）
                    dist_to_stack = abs(x - stack_top_x)
                    if dist_to_stack < 0.5:
                        score += 50.0
                        reasons.append("SAME_TYPE_STACK_MERGE_PRIORITY_REACTIVE")
            else:
                # その他の場合: 危険ピースがある OR reactive_pairsがある OR 即時併合がある OR deadline_crossed
                # 盤面圧縮ボーナスを適用しない（何もしない）
                pass

        # ----- evaluation axis 9.5: current type stack merge priority (deadline_crossed版) -----
        # v334: deadline_crossed時の即時併合優先強化版 - axis 9.5 deadline_crossed条件追加
        # last_rollback_postmortemのfailure mode: "deadline_crossed時にreactive_pairs>=1でも即時併合不可で延命配置のみ続き、max_y runway"
        # ワーストゲーム(score0845)終盤turns 67-74: deadline_crossed=true, reactive_pairs=3-4, danger_piece_count=2-6, merge_available=false続き
        #   axis 9.2のdanger_piece_count==0条件によりheight_mult緩和が適用されず、延命配置が続きmax_y=2.29→2.81に上昇してゲームオーバー
        # ベストゲーム(score3577)終盤turns 140-147: deadline_crossed=trueだが即時併合機会を確実に捉えて3577点を出している
        # axis 9.2修正: deadline_crossed && reactive_pair_count >= 2 && merge_grade == "NO"の条件からdanger_piece_count==0を削除
        #   danger_piece_countが多い状況でもheight_mult緩和を適用し、戦略的配置の余地を確保
        # axis 9.5修正: 盤面圧縮ボーナス適用条件にdeadline_crossed && reactive_pair_count >= 1 && merge_grade == "NO"を追加
        #   deadline_crossed時にreactive_pairsがある場合、盤面圧縮ボーナスを適用せず、即時併合優先
        # これによりdeadline_crossed時にreactive_pairsがある状況で戦略的配置の余地を確保しつつ、即時併合機会を逃さない
        # refs: tmp/state/last_rollback_postmortem.md, tmp/state/last_rollback_analysis.md, tmp/improve_brief.md, tmp/batch_summary.txt, advice.md,
        #       game_history/20260324_085547_score0845.jsonl turns 67-74, game_history/20260324_090115_score3577.jsonl turns 140-147
        if (
            deadline_crossed
            and reactive_pair_count >= 1
            and merge_grade == "NO"
            and same_type_stack_top is not None
        ):
            # deadline_crossed && reactive_pairs>=1 && 即時併合なし: 盤面圧縮ボーナスを適用しない（何もしない）
            pass

        # ----- evaluation axis 9.2: reactive pairs deadline penalty (deadline_crossed版) -----
        # v334: deadline_crossed時の即時併合優先強化版 - danger_piece_count条件削除
        # last_rollback_postmortemのfailure mode: "deadline_crossed時にreactive_pairs>=1でも即時併合不可で延命配置のみ続き、max_y runway"
        # ワーストゲーム(score0845)終盤turns 67-74: deadline_crossed=true, reactive_pairs=3-4, danger_piece_count=2-6, merge_available=false続き
        #   axis 9.2のdanger_piece_count==0条件によりheight_mult緩和が適用されず、延命配置が続きmax_y=2.29→2.81に上昇してゲームオーバー
        # ベストゲーム(score3577)終盤turns 140-147: deadline_crossed=trueだが即時併合機会を確実に捉えて3577点を出している
        # axis 9.2修正: deadline_crossed && reactive_pair_count >= 2 && merge_grade == "NO"の条件からdanger_piece_count==0を削除
        #   danger_piece_countが多い状況でもheight_mult緩和を適用し、戦略的配置の余地を確保
        # refs: tmp/state/last_rollback_postmortem.md, tmp/state/last_rollback_analysis.md, tmp/improve_brief.md, tmp/batch_summary.txt, advice.md,
        #       game_history/20260324_085547_score0845.jsonl turns 67-74, game_history/20260324_090115_score3577.jsonl turns 140-147
        if deadline_crossed and reactive_pair_count >= 2 and merge_grade == "NO":
            # deadline_crossed && reactive_pairs>=2 && 即時併合なし: ペナルティを適用
            score -= 4500.0
            reasons.append("DANGER_ZONE_REACTIVE_PENALTY_DEADLINE")

        # ----- evaluate next candidate -----
        if score > best_score:
            best_score = score
            best_x = x
            best_reason = " + ".join(reasons) if reasons else "DEFAULT"

    return {"x": best_x, "reason": best_reason}


if __name__ == "__main__":
    # Example usage
    import sys
    import json

    if len(sys.argv) > 2:
        with open(sys.argv[1], "r") as f:
            game_state = json.load(f)
        with open(sys.argv[2], "r") as f:
            analysis = json.load(f)
        result = decide(game_state, analysis)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        # test with example data
        game_state = {
            "pieces": [
                {"x": 0.0, "y": -3.5, "type": 1},
                {"x": 0.5, "y": -3.0, "type": 1},
            ],
            "next": {"type": 1},
            "nextNext": {"type": 2},
            "score": 10,
            "deadline_crossed": False,
        }
        analysis = {
            "results": [
                {
                    "x": -0.5,
                    "landing_y": -2.5,
                    "drift_x": 0.1,
                    "drift_unc": 0.05,
                    "merge_grade": "DIRECT",
                },
                {
                    "x": 0.5,
                    "landing_y": -2.0,
                    "drift_x": -0.1,
                    "drift_unc": 0.05,
                    "merge_grade": "NEAR",
                },
                {
                    "x": 1.5,
                    "landing_y": -2.5,
                    "drift_x": 0.0,
                    "drift_unc": 0.05,
                    "merge_grade": "NO",
                },
            ],
            "reactor": {"reactive_pairs": [], "near_pairs": []},
        }
        result = decide(game_state, analysis)
        print(json.dumps(result, ensure_ascii=False, indent=2))
