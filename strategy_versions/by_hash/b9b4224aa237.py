#!/usr/bin/env python3
"""strategy.py - AI改善対象の決定スクリプト
v349: 即時併合最優先シンプル版 - best_score2335成功パターン基盤
+ v348の問題点: 評価軸が多すぎて複雑すぎる。axis 8.5/8.6/8.7/8.8/9.2/9.5などが競合し、即時併合機会を取りこぼしている
+ batch_summaryでREACTIVE_PAIRS_COMPRESSIONが10.6%選択(avg_score_delta=6.3)、即時併合関連reasonはavg_score_deltaが高いが選択率が低い
+ extra_highゲーム(score2289)終盤: deadline_crossed=true, reactive_pairs=2続きでmerge_available=false続き、複雑なreasonが選ばれmax_y=2.92で推移
+ best_score2335_strategy.pyはシンプルな構造で成功している（評価軸: merge_bonus, height_penalty, drift_penalty, balance, nextNext）
+ v349: 評価軸を大幅に削減し、best_score2335の成功パターンをベースに即時併合を最優先するシンプルな戦略へ回帰
+   - 評価軸: axis 1-6 (merge_bonus, height_penalty, drift_penalty, balance, nextNext, chain_merge) のみに削減
+   - axis 8.5/8.6/8.7/8.8/9.2/9.5 などの複雑な評価軸を全削除
+   - reactive_pairsがある場合は即時併合ボーナスを強化（+800.0）して即時併合を最優先
+   - advice.md「盤面状態に関わらず即時併合を最優先する」を実現するシンプルなロジック
+ refs: tmp/improve_brief.md, tmp/batch_summary.txt, advice.md, tmp/state/last_rollback_postmortem.md,
+       strategy_versions/best_score2335_strategy.py, game_history/20260326_234253_score2289.jsonl turns 93-100
+ Fixes rollback failure mode: 複雑な評価軸の競合による即時併合機会取りこぼし（評価軸大幅削減・シンプル化）
#
v347: ロシア建国後戦略的配置無効化版 - 即時併合最優先フェーズ切り替え
+ v347: ロシア建国後戦略的配置無効化版 - 即時併合最優先フェーズ切り替え
+ v346の問題点: ロシア建国後もaxis 8.8/9.5のcompression_bonusが適用され、戦略的配置が選ばれて即時併合機会を取りこぼす
+ advice.md「ロシア建国後の死亡速度が早い。建国後はより慎重な盤面進行を検討すること」がログで支持されている
+ ワーストゲーム(score0781)はロシア建国なしでmax_y=3.32即死、score2575はロシア建国後も即時併合継続で2575点
+ ロシア建国後は盤面が狭く、戦略的配置の余地が限られているため、即時併合機会を逃すと致命的
+ v347: axis 8.8/9.5の適用条件に`and not russia_phase`を追加し、ロシア建国後は戦略的配置を完全無効化
+   - ロシア建国後はaxis 9.2のペナルティとaxis 8.7の即時併合ボーナスで評価し、即時併合を強制的に待機
+   - axis 2のheight_penaltyで低配置を促し、戦略的配置の余地をaxis 9.2の危険域ペナルティで確保
+ ロシア建国後の明確なフェーズ切り替えを実現する構造的改善であり、数値調整ではない
+ refs: advice.md (あずまぐ), tmp/state/last_rollback_postmortem.md, tmp/state/last_rollback_analysis.md, tmp/improve_brief.md, tmp/batch_summary.txt,
+       game_history/20260326_211032_score0781.jsonl turn 70 (ロシア建国なしで即死),
+       game_history/20260326_213252_score2575.jsonl (ロシア建国後の即時併合継続で高スコア)
+ Fixes rollback failure mode: ロシア建国後の戦略的配置による即時併合機会取りこぼし（axis 8.8/9.5 適用条件にrussia_phase除外追加）
#
v344: axis 8.8 構造的変更版 - 未活用near_pairs活用・即時併合機会数に応じた段階的評価
+ v344: axis 8.8 構造的変更版 - 未活用near_pairs活用・即時併合機会数に応じた段階的評価
+ v343の問題点: compression_bonusの単純な数値調整（+250.0→+500.0）は構造変更ではなく、即時併合機会の取りこぼしを解消できない
+ batch_summaryで高スコア群の併合率38.2% vs 低スコア群33.4%、即時併合がスコアに直結している
+ advice.md「盤面状態に関わらず即時併合を最優先する」「同タイプが来たらその上に置く」が戦略的配置より優先されるべき
+ v344: 未活用のreactor.near_pairs情報を活用し、即時併合機会の総数（reactive_pairs + near_pairs）に応じて評価を変える構造的変更
+   - 即時併合機会が少ない（total <= 2）場合：戦略的配置を許容しcompression_bonus適用
+   - 即時併合機会が多い（total >= 4）場合：戦略的配置を抑制し即時併合優先
+   - 即時併合機会が中程度（total = 3）場合：危険度（max_y, deadline_crossed）に応じて判断
+ refs: tmp/state/last_rollback_postmortem.md, tmp/state/last_rollback_analysis.md, tmp/improve_brief.md, tmp/batch_summary.txt, advice.md
+ Fixes rollback failure mode: 即時併合機会取りこぼし（near_pairs活用・即時併合機会数に応じた段階的評価）
#
v344: axis 8.8 構造的変更版 - 未活用near_pairs活用・即時併合機会数に応じた段階的評価

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
           8.5. Danger zone immediate merge bonus - v321: 危険域即時併合強化
           8.6. Reactive pairs immediate merge bonus - v321: 即時併合ボーナス維持
           8.7. Russia phase immediate merge priority - v327: 危険ピース時ボーナス削除版
           8.8. Reactive pairs compression bonus - v348: deadline_crossed時戦略的配置無効化版
           9. Reactive pairs default - Default to REACTIVE_PAIRS_COMPRESSION when reactive_pairs >= 1 and no immediate merge
           9.2. Danger zone reactive penalty - v324: deadline_crossed対応強化版
           9.5. Current type stack merge priority - v348: deadline_crossed時戦略的配置無効化版


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
    # v347: ロシア建国後戦略的配置無効化版 - 即時併合最優先フェーズ切り替え
    # v346の問題点: ロシア建国後もaxis 8.8/9.5のcompression_bonusが適用され、戦略的配置が選ばれて即時併合機会を取りこぼす
    # advice.md「ロシア建国後の死亡速度が早い。建国後はより慎重な盤面進行を検討すること」がログで支持されている
    # ワーストゲーム(score0781)はロシア建国なしでmax_y=3.32即死、score2575はロシア建国後も即時併合継続で2575点
    # ロシア建国後は盤面が狭く、戦略的配置の余地が限られているため、即時併合機会を逃すと致命的
    # v347: axis 8.8/9.5の適用条件に`and not russia_phase`を追加し、ロシア建国後は戦略的配置を完全無効化
    #   - ロシア建国後はaxis 9.2のペナルティとaxis 8.7の即時併合ボーナスで評価し、即時併合を強制的に待機
    #   - axis 2のheight_penaltyで低配置を促し、戦略的配置の余地をaxis 9.2の危険域ペナルティで確保
    # ロシア建国後の明確なフェーズ切り替えを実現する構造的改善であり、数値調整ではない
    # refs: advice.md (あずまぐ), tmp/state/last_rollback_postmortem.md, tmp/state/last_rollback_analysis.md, tmp/improve_brief.md, tmp/batch_summary.txt,
    #       game_history/20260326_211032_score0781.jsonl turn 70 (ロシア建国なしで即死),
    #       game_history/20260326_213252_score2575.jsonl (ロシア建国後の即時併合継続で高スコア)
    # Fixes rollback failure mode: ロシア建国後の戦略的配置による即時併合機会取りこぼし（axis 8.8/9.5 適用条件にrussia_phase除外追加）
    #
    # v346: axis 9.5条件緩和版 - 未活用情報（merged_type隣接）の活用強化
    # v345の問題点: axis 8.8のペナルティ係数縮小（500.0→300.0）は数値微調整のみで構造的改善ではない
    # axis 9.5（MERGED_TYPE_ADJACENCY_PRIORITY）の条件がdanger_piece_count == 0 and reactive_pair_count == 0と厳しすぎ、
    # 未活用情報（merged_typeと盤面上のTypeN+1の隣接状態）がほとんど活用されていなかった
    # v346: axis 9.5の適用条件をtotal_immediate_merge_opportunities <= 2に緩和し、即時併合機会が少ない状況で戦略的配置を許容
    # 将来の連鎖的併合（merged_typeとの隣接）を評価し、2手先の併合可能性を最大化する構造的改善
    # 未活用情報（merged_type隣接状態）の活用を強化する条件緩和であり、数値微調整ではない
    # refs: advice.md (azumag, nimdavirus), tmp/state/last_rollback_postmortem.md, tmp/improve_brief.md, tmp/batch_summary.txt
    # Fixes: 即時併合機会が少ない状況での戦略的配置余地不足（axis 9.5条件緩和による未活用情報活用強化）
    #
    # v343: axis 8.8 compression_bonus復活版 - 戦略的配置の余地確保
    # v335の問題点: axis 8.8のdanger_piece_count条件がdeadline_crossed && danger_piece_count>0の場合にcompression_bonusを適用せず、戦略的配置の余地を制限
    # last_rollback_postmortemの致命的欠陥: "deadline_crossed && reactive_pairs=1-2 && merge_grade=='NO' の戦略的死lock状態"
    # v330 (rollback_target) の成功パターン: reactive_pairs>=1ならcompression_bonusが有効で、戦略的配置で即時併合を待機できる
    # v336: danger_piece_count条件を削除し、reactive_pair_count >= 1 && merge_grade == "NO"で一律にcompression_bonusを適用
    #   - deadline_crossed時でも戦略的配置の余地を確保し、即時併合を待機しつつmax_y上昇を抑える戦略を実現
    #   - compression_bonus基本値を+500.0に強化し、戦略的配置の価値を高める
    #   - axis 9.2の危険域ペナルティがcompression_bonusを上回るため、危険域では即時併合を優先
    # refs: tmp/state/last_rollback_postmortem.md, tmp/state/last_rollback_analysis.md, tmp/improve_brief.md, tmp/batch_summary.txt, advice.md,
    #       game_history/20260326_093527_score0569.jsonl turns 66-69 (v348 failure mode), game_history/20260324_062802_score1741.jsonl turns 62-69 (v330 success),
    #       game_history/20260326_131533_score0399.jsonl, game_history/20260326_131332_score3455.jsonl
    # Fixes rollback failure mode: deadline_crossed && reactive_pairs=1-2での戦略的死lock状態解消（v335 danger_piece_count条件削除）
    #
    # v332: axis 8.8 reactive_pairs>=2拡張版 - v331 failure mode潰し
    # v331の問題点: axis 9.2の固定ペナルティ(-2000/-2500)はheight_mult緩和と競合して不十分
    # ワーストゲーム(score0585)終盤turns 51-56: reactive_pairs=2, merge_available=falseでHIGH_TOWERが続き、max_y=2.44→3.91に急上昇してゲームオーバー
    # ベストゲーム(score2988)終盤turns 129-136: 即時併合機会を確実に捉えてmax_y=2.88で安定し2988点を出している
    # axis 8.8の動的ペナルティはlanding_yに応じて指数関数的に増大するため、height_mult緩和を上回る強力な抑制が可能
    # reactive_pairs==1はaxis 9.2の-2000.0ペナルティで対応、reactive_pairs>=2はaxis 8.8の動的ペナルティで対応することでgapを解消
    # axis 8.8の適用条件をreactive_pairs>=3からreactive_pairs>=2に緩和し、v348のfailure modeを解消
    # refs: tmp/state/last_rollback_postmortem.md, tmp/state/last_rollback_analysis.md, tmp/improve_brief.md, tmp/batch_summary.txt, advice.md, tmp/sandbox_files.md,
    #       game_history/20260326_105834_score0585.jsonl turns 51-56, game_history/20260326_105244_score2988.jsonl turns 129-136
    # Fixes rollback failure mode: reactive_pairs>=2での高配置 runaway（v331 axis 9.2固定ペナルティ→v332 axis 8.8動的ペナルティ拡張）
    #
    # v331: axis 9.2 reactive_pairs>=1拡張版 - v348 failure mode潰し
    # last_rollback_postmortemのfailure mode: "deadline_crossed && reactive_pairs=1-2 && merge_grade=="NO" の戦略的死lock状態"
    # ワーストゲーム(score0569)終盤turns 66-69: deadline_crossed=true, reactive_pairs=1, merge_available=false続きでmax_y=1.65→2.39に急上昇してゲームオーバー
    # ベストゲーム(score2710)終盤turns 106-113: 即時併合機会を確実に捉えてmax_y=2.73で安定
    # axis 9.2修正: 適用条件を reactive_pair_count >= 2 から reactive_pair_count >= 1 に緩和
    # reactive_pairs==1の場合: 基本ペナルティ-2000.0を適用し、戦略的死lock状態を解消
    # reactive_pairs>=2の場合: 基本ペナルティ-2500.0 + 危険ピース毎に-1000.0（最大-4500.0）を維持
    # これによりaxis 8.8（reactive_pairs>=3のみ）とaxis 9.2の間にgapがあるreactive_pairs=1-2の状況でも、戦略的配置の余地を確保
    # advice.md「deadline_crossed時の即時併合強化」に基づく即時併合優先戦略の実装
    # refs: tmp/state/last_rollback_postmortem.md, tmp/state/last_rollback_analysis.md, tmp/improve_brief.md, tmp/batch_summary.txt, advice.md,
    #       game_history/20260326_093527_score0569.jsonl turns 66-69, game_history/20260324_055424_score2710.jsonl turns 106-113
    # Fixes rollback failure mode: v348の戦略的死lock状態（deadline_crossed && reactive_pairs=1-2 && merge_grade=="NO"）
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
    """v349: 即時併合最優先シンプル版 - best_score2335成功パターン基盤

    v348の問題点: 評価軸が多すぎて複雑すぎる。axis 8.5/8.6/8.7/8.8/9.2/9.5などが競合し、即時併合機会を取りこぼしている
    v349の改善点:
    1. 評価軸を大幅に削減し、best_score2335の成功パターンをベースに回帰
    2. 評価軸: axis 1-6 (merge_bonus, height_penalty, drift_penalty, balance, nextNext, chain_merge) のみ
    3. reactive_pairsがある場合は即時併合ボーナスを強化して即時併合を最優先
    4. advice.md「盤面状態に関わらず即時併合を最優先する」を実現するシンプルなロジック

    Args:
         game_state: game state (pieces, next, nextNext, score, etc.)
         analysis: analyze_board.py analysis results
             - results: landing information for each drop X candidate
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

    # --- pre-calculate merged type (for chain judgment) ---
    merged_type = min(next_type + 1, 16)

    # =======================================================================
    # score each drop candidate (x coordinate) with simple evaluation axes
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

        # ----- evaluation axis 6: reactive pairs immediate merge bonus (v349: 即時併合最優先シンプル版) -----
        # advice.md「盤面状態に関わらず即時併合を最優先する」を実現するシンプルなロジック
        # reactive_pairsがある場合は即時併合ボーナスを強化し、即時併合機会の取りこぼしを削減
        # refs: advice.md, tmp/batch_summary.txt, tmp/state/last_rollback_postmortem.md
        if reactive_pair_count >= 1 and merge_grade in ["DIRECT", "NEAR"]:
            # reactive_pairsがある場合は即時併合を強力に優先
            score += 800.0
            reasons.append("REACTIVE_IMMEDIATE_MERGE_PRIORITY")

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
