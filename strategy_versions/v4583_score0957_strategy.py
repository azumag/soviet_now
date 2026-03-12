#!/usr/bin/env python3
"""strategy.py - Soviet Puzzle Game AI Drop Position Script

Game Overview:
  - Drop pieces, merge same type pieces (N+N -> N+1)
  - Score table: type1=1, type2=3, type3=6, ..., typeN = N*(N+1)/2
  - Board: x in [-3.0, +3.0], floor y=-4.48, deadline y=3.32
  - Player controls only drop X coordinate

Decision Logic (9 evaluation axes):
     1. Merge bonus - High score for immediate merge (DIRECT > NEAR > FAR)
     2. Height penalty - Penalty for high landing position (varies by phase)
     3. Drift penalty - Penalty for post-landing drift due to polygon shape
     4. Left-right balance correction - Bonus for correcting piece count bias
     5. nextNext centering - Center for next merge opportunity if nextNext same type
     6. Chain merge bonus - Evaluate possibility of further merges after merge
     7. Early game merge priority - Bonus for NEAR merge in early game (max_y < -2.0)
     8. MEDIUM_TOWER promotion - Bonus for merge candidates at higher landing in MEDIUM phase
     9. v161: Medium phase immediate merge priority - Filter to merge candidates when reactive_pairs >= 2 in MEDIUM phase
     10. v165: Reactive merge priority - Direct bonus for merge when reactive_pairs >= 2
     11. v166: Danger compression priority - Board compression based on deadline info when max_y >= 2.0

Phases (determined by board max Y):
   LOW      (max_y < 0.8) : Early game. Merge priority (merge_mult=1.2)
   MEDIUM   (0.8 <= max_y < 1.8) : Mid game. Height management (height_mult=1.4), immediate merge when reactive_pairs >= 2
   HIGH     (1.8 <= max_y < 3.0) : Late game. Merge opportunity (height_mult=1.8), immediate merge when reactive_pairs >= 2
   CRITICAL (3.0 <= max_y) : Danger. DIRECT merge priority, board compression

Fixed interface:
   decide(game_state: dict, analysis: dict) -> dict
      Returns: {"x": float, "reason": str}

AI modifiable: decide() body, helper functions, constants, imports
AI prohibited: decide() signature, if __name__ == "__main__" block

# --- Change History ---
# [BEST:5310] v159: reactor情報活用による危険局面即時併合優先版
# v160: 危険局面フィルタリング早期化強化版 - max_y>=1.8かつreactive_pairs>=3で併合機会のみを評価対象
#   - 危険局面でのFARマージボーナスを強化（200.0→1200.0）し、いずれかの併合機会を確保
#   - ワーストゲーム(score0467)の失敗パターン分析に基づき、危険局面の閾値を厳密化
# v161: 中盤フェーズでの即時併合優先強化版 - ワーストゲーム(score0606)の失敗パターン分析に基づき、中盤フェーズでの即時併合機会の見逃しを回避
#   - 中盤フェーズ(0.8 <= max_y < 1.8)では reactive_pairs >= 2 の段階で併合候補のみを評価対象にする
#   - HIGH/CRITICALフェーズでは reactive_pairs >= 3 でフィルタリング発動（v160の条件を維持）
#   - これにより、盤面がまだ圧縮可能な段階で即時併合を優先し、盤面圧迫を回避する
# refs: tmp/batch_summary.txt, tmp/improve_brief.md, game_history/20260311_022246_score0606.jsonl turns 62-69, game_history/20260311_025551_score2766.jsonl turns 135-141
# v162: 中盤低反応器活性化によるSANDWICH_AVOID強化版 - batch_summaryでHEIGHT_CONTROLが29.7%選択(avg_score_delta=2.2)と過剰であることを確認
#   - ワーストゲーム(score0328, score0849)の終盤分析で、中盤フェーズでreactive_pairsが少ない場合、HEIGHT_CONTROLが選択され早期ゲームオーバーとなる失敗パターンを特定
#   - 中盤フェーズ(0.8 <= max_y < 1.8)でreactive_pairs <= 1の場合、SANDWICH_AVOIDを有効化して即時併合を優先
#   - 即時併合による盤面圧縮を促進し、HEIGHT_CONTROL選択を抑制してスコア安定性を向上させる
# refs: tmp/batch_summary.txt, tmp/improve_brief.md, tmp/advice.md, game_history/20260311_224024_score0328.jsonl, game_history/20260311_222520_score0849.jsonl
# v163: 高反応器ペナル削減による即時併合強化版 - ワーストゲーム(score0594, score0645)の終盤8ターン分析で、reactive_pairs=3-7あるにもかかわらずHIGH_TOWERが選択され続け即時併合を逃している失敗パターンを特定
#   - 危険局面ではないがreactive_pairsが高い状況で、height_penaltyが強すぎて併合機会を優先できない問題を解消
#   - max_y >= 1.0かつreactive_pairs >= 3の場合、height_multiplierを動的に削減して即時併合を優先
#   - height_multiplier = max(base_value * (1.0 - (reactive_pair_count - 2) * 0.15), min_value)
#   - MEDIUMフェーズ: base=15.0, min=5.0 (削減率最大67%)
#   - HIGHフェーズ: base=30.0, min=10.0 (削減率最大67%)
# refs: tmp/batch_summary.txt, tmp/improve_brief.md, game_history/20260312_004246_score0594.jsonl turns 58-65, game_history/20260312_002337_score0645.jsonl turns 58-65
# v164: 即時併合直接ボーナスによる強化版 - ワーストゲーム(score0463)の失敗パターン分析で、v163のペナルティ削減ロジックがdangerous_situationフラグによってブロックされる問題を特定
#   - v163のhigh_reactivity_zone条件は、dangerous_situationがTrueの場合に適用されず、即時併合が抑制される
#   - ベスト戦略(best_score5310)のREACTIVE_MERGE_PRIORITY（reactive_pairs >= 2で+500.0ボーナス）を採用し、ペナルティ削減アプローチを置換
#   - reactive_pairs >= 3の場合、DIRECT/NEARマージに+500.0ボーナスを付与し、即時併合を強制的に優先
#   - これにより、危険局面でも即時併合が選択され、HIGH_TOWER選択を抑制してスコア安定性を向上させる
# refs: tmp/batch_summary.txt, tmp/improve_brief.md, game_history/20260312_012424_score0463.jsonl turns 60-64
# v165: 危険局面即時併合優先閾値引き下げ版 - batch_summaryでHEIGHT_CONTROLが28.5%選択(avg_score_delta=0.9)と過剰であることを確認
#   - ワーストゲーム(score0472)の終盤8ターン分析で、max_y>=2.0かつreactive_pairs=4-5あるにもかかわらずHIGH_TOWER/HIGH_LAYERが選択され続け即時併合機会を逃している失敗パターンを特定
#   - ベスト戦略(best_score5310)の閾値に合わせて、危険局面での即時併合優先閾値をreactive_pairs >= 3からreactive_pairs >= 2に引き下げ
#   - max_y >= 2.0かつreactive_pairs >= 2の危険局面で、即時併合をより早期から優先し、HEIGHT_CONTROL選択を抑制してスコア安定性を向上させる
# refs: tmp/batch_summary.txt, tmp/improve_brief.md, tmp/advice.md, game_history/20260312_073925_score0472.jsonl, game_history/20260312_074619_score1782.jsonl, strategy_versions/best_score5310_strategy.py, tmp/change_log.txt
# v166: 危険局面deadline情報活用による盤面圧縮評価軸追加 - batch_summaryでHEIGHT_CONTROLが28.5%選択(avg_score_delta=0.9)と過剰であることを確認
#   - 危険局面(max_y >= 2.0)で即時併合機会がない場合、deadline_marginとdanger_piece_countを活用して盤面圧縮を促進する新しい評価軸を追加
#   - deadline_marginが小さい（deadlineに近い）ほど、着地Yが低い配置をより優先
#   - danger_piece_countが多いほど、盤面圧縮をより優先
#   - 未活用情報(deadline関連)を活用することで、危険局面での盤面圧縮戦略を強化し、HEIGHT_CONTROL選択を抑制してスコア安定性を向上させる
# refs: tmp/batch_summary.txt, tmp/improve_brief.md, tmp/advice.md, game_history/20260312_073925_score0472.jsonl, game_history/20260312_074619_score1782.jsonl, tmp/change_log.txt
# v167: 危険局面reactive_pairs活用による盤面圧縮調整版 - batch_summaryでHEIGHT_CONTROLが28.8%選択(avg_score_delta=0.9)と過剰であることを確認
#   - ワーストゲーム(score0624)の終盤8ターン分析で、max_y>=2.0かつreactive_pairs=4-6あるにもかかわらずmerge_available=falseの状況で
#     HIGH_TOWER/HIGH_LAYERが選択され続け、即時併合機会を逃している失敗パターンを特定
#   - reactive_pairsが多い状況では、盤面の高さを下げるだけでなくreactive_pairsを活かす配置を優先する必要がある
#   - reactive_pair_countに応じて盤面圧縮の強度を動的に調整し、reactive_pairsを活用する配置を優先する
#   - reactive_compression_factor = max(0.3, 1.0 - (reactive_pair_count - 2) * 0.15) により、reactive_pairsが多いほど盤面圧縮を弱めてreactive_pairs活用を優先
# refs: tmp/batch_summary.txt, tmp/improve_brief.md, tmp/advice.md, game_history/20260312_084756_score0624.jsonl, game_history/20260312_084231_score2819.jsonl, game_history/20260312_082803_score0640.jsonl, tmp/change_log.txt
"""

SCORE_TABLE = {i: i * (i + 1) // 2 for i in range(1, 17)}


def decide(game_state: dict, analysis: dict) -> dict:
    """v167: 危険局面reactive_pairs活用による盤面圧縮調整版

    batch_summaryでHEIGHT_CONTROLが28.8%選択(avg_score_delta=0.9)と過剰であることを確認。
    ワーストゲーム(score0624)の終盤8ターン分析で、max_y>=2.0かつreactive_pairs=4-6あるにもかかわらず
    merge_available=falseの状況でHIGH_TOWER/HIGH_LAYERが選択され続け、即時併合機会を逃している失敗パターンを特定。
    reactive_pairsが多い状況では、盤面の高さを下げるだけでなくreactive_pairsを活かす配置を優先する必要がある。
    reactive_pair_countに応じて盤面圧縮の強度を動的に調整し、reactive_pairsを活用する配置を優先する。

    v167の改善点：
      1. 危険局面reactive_pairs活用による盤面圧縮調整
         - 危険局面(max_y >= 2.0)でmerge_available=falseの場合、reactive_pair_countに応じて盤面圧縮の強度を動的に調整
         - reactive_compression_factor = max(0.3, 1.0 - (reactive_pair_count - 2) * 0.15) により、reactive_pairsが多いほど盤面圧縮を弱めてreactive_pairs活用を優先
         - reactive_pairs=3で70%、reactive_pairs=4で55%、reactive_pairs=5で40%、reactive_pairs=6で25%の盤面圧縮強度に調整
         - reactive_pairsが少ない状況では盤面圧縮を優先し、reactive_pairsが多い状況ではreactive_pairs活用を優先
      2. 即時併合機会の見逃し回避
         - ワーストゲーム(score0624)のturn 59-65でreactive_pairs=4-6あるにもかかわらずHIGH_TOWER/HIGH_LAYERが選択される失敗を解消
         - reactive_pairsが多い状況では、盤面の高さを下げるだけでなくreactive_pairsを活かす配置を優先し、即時併合機会を確保
      3. v162/v164/v165/v166の改善を維持
         - ベスト戦略(best_score5310)の閾値に合わせて、危険局面での即時併合優先閾値を引き下げ
         - 中盤フェーズ(0.8 <= max_y < 1.8)でreactive_pairs <= 1の場合、SANDWICH_AVOIDを有効化
         - reactive_pairsが少ない状況での即時併合見逃しを回避
         - 危険局面deadline情報活用による盤面圧縮評価軸を維持しつつ、reactive_pairs活用による調整を追加
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

    # --- reactor information ---
    reactor = analysis.get("reactor", {})
    reactive_pairs = reactor.get("reactive_pairs", [])
    reactive_pair_count = len(reactive_pairs) if isinstance(reactive_pairs, list) else 0

    # --- v167: 危険局面reactive_pairs活用による盤面圧縮調整版 ---
    # batch_summaryでHEIGHT_CONTROLが28.8%選択(avg_score_delta=0.9)と過剰であることを確認
    # ワーストゲーム(score0624)の終盤8ターン分析で、max_y>=2.0かつreactive_pairs=4-6あるにもかかわらず
    # merge_available=falseの状況でHIGH_TOWER/HIGH_LAYERが選択され続け、即時併合機会を逃している失敗パターンを特定
    # reactive_pairsが多い状況では、盤面の高さを下げるだけでなくreactive_pairsを活かす配置を優先する必要がある
    # reactive_pair_countに応じて盤面圧縮の強度を動的に調整し、reactive_pairsを活用する配置を優先する
    # refs: tmp/batch_summary.txt, tmp/improve_brief.md, tmp/advice.md, game_history/20260312_084756_score0624.jsonl, game_history/20260312_084231_score2819.jsonl, game_history/20260312_082803_score0640.jsonl, tmp/change_log.txt
    deadline = analysis.get("deadline", {})
    deadline_margin = deadline.get("deadline_margin", 0.0)
    danger_piece_count = deadline.get("danger_piece_count", 0)

    # --- phase judgment (v42 thresholds) ---
    if max_y < 0.8:
        phase = "LOW"
        height_mult = 1.0
        merge_mult = 1.2
    elif max_y < 1.8:
        phase = "MEDIUM"
        height_mult = 1.4
        merge_mult = 1.0
    elif max_y < 3.0:
        phase = "HIGH"
        height_mult = 1.8
        merge_mult = 1.0
    else:
        phase = "CRITICAL"
        height_mult = 1.0
        merge_mult = 0.6

    # --- v165: 危険局面即時併合優先閾値引き下げ版 ---
    # batch_summaryでHEIGHT_CONTROLが28.5%選択(avg_score_delta=0.9)と過剰であることを確認
    # ワーストゲーム(score0472)の終盤8ターン分析で、max_y>=2.0かつreactive_pairs=4-5あるにもかかわらず
    # HIGH_TOWER/HIGH_LAYERが選択され続け即時併合機会を逃している失敗パターンを特定
    # ベスト戦略(best_score5310)のREACTIVE_MERGE_PRIORITYアプローチを採用し、危険局面での即時併合優先閾値を引き下げ
    # max_y >= 2.0かつreactive_pairs >= 2の危険局面で、即時併合をより早期から優先する
    # refs: tmp/batch_summary.txt, tmp/improve_brief.md, tmp/advice.md, game_history/20260312_073925_score0472.jsonl, game_history/20260312_074619_score1782.jsonl, strategy_versions/best_score5310_strategy.py, tmp/change_log.txt

    # --- v164: 即時併合直接ボーナスによる強化版 ---
    # v163のペナルティ削減ロジック(high_reactivity_zone)は削除済み
    # ベスト戦略(best_score5310)のREACTIVE_MERGE_PRIORITYアプローチを採用し、直接ボーナス方式に切り替えた
    # reactive_pairs >= 2の場合、DIRECT/NEARマージに+500.0ボーナスを付与
    # 危険局面でもボーナスが適用されるため、即時併合が選択される
    # refs: tmp/batch_summary.txt, tmp/improve_brief.md, game_history/20260312_012424_score0463.jsonl turns 60-64

    # --- v161: 中盤フェーズでの即時併合優先強化版 ---
    # ワーストゲーム(score0606)の失敗パターン分析に基づき、中盤フェーズでの即時併合機会の見逃しを回避
    # 中盤フェーズ(0.8 <= max_y < 1.8)では reactive_pairs >= 2 の段階で併合候補のみを評価対象にする
    # 危険局面(max_y >= 2.0)では reactive_pairs >= 2 でフィルタリング発動
    # これにより、盤面がまだ圧縮可能な段階で即時併合を優先し、盤面圧迫を回避する
    dangerous_situation_medium = phase == "MEDIUM" and reactive_pair_count >= 2
    dangerous_situation_high = max_y >= 2.0 and reactive_pair_count >= 2
    dangerous_situation = dangerous_situation_medium or dangerous_situation_high

    # --- next piece information ---
    next_piece = game_state.get("next", {})
    next_next_piece = game_state.get("nextNext", {})
    next_type = next_piece.get("type", 0)
    next_next_type = next_next_piece.get("type", 0)

    # =======================================================================
    #  v160: 危険局面での候補フィルタリング
    # =======================================================================
    if dangerous_situation:
        merge_results = [
            r for r in results if r.get("merge_grade") in ["DIRECT", "NEAR", "FAR"]
        ]
        if merge_results:
            filtered_results = merge_results
        else:
            # 全候補を評価（マージ機会がない場合のフォールバック）
            filtered_results = results
    else:
        filtered_results = results

    # =======================================================================
    #  score each drop candidate (x coordinate) with 8 evaluation axes
    # =======================================================================
    for result in filtered_results:
        x = result["x"]
        landing_y = result.get("landing_y", 0)
        drift_x = result.get("drift_x", 0)
        drift_unc = result.get("drift_unc", 0)
        merge_grade = result.get("merge_grade", "NO")

        score = 0.0
        reasons = []

        # ----- evaluation axis 1: merge bonus -----
        if merge_grade == "DIRECT":
            score += 1200.0 * merge_mult
            reasons.append("DIRECT_MERGE")
        elif merge_grade == "NEAR":
            score += 600.0 * merge_mult
            reasons.append("NEAR_MERGE")
        elif merge_grade == "FAR":
            # v160: 危険局面ではFARマージボーナスを強化（200.0→1200.0）
            # ベストゲーム（score3064）のようなmax_y>2.5の状況でも盤面を圧縮するため、
            # 盤面圧縮優先とFARマージの安全性を確保
            far_bonus = 1200.0 if dangerous_situation else 200.0
            score += far_bonus * merge_mult
            reasons.append("FAR_MERGE")

        # ----- evaluation axis 2: height penalty -----
        # 盤面圧縮優先戦略
        # max_yが高い盤面では、併合候補の中でも着地Yが低いものを優先する傾向がある
        # LOW/MEDIUMフェーズのheight_multを維持（1.0, 1.4）し、HIGHフェーズはheight_mult=1.8
        height_multiplier = 30.0 if phase != "LOW" else 15.0

        # v165: 危険局面での高さペナルティ無効化閾値引き下げ版
        # max_y >= 2.0かつreactive_pairs >= 2の危険局面では、即時併合を優先するためにheight_multiplierを0.0に設定
        # ベスト戦略(best_score5310)の閾値に合わせて引き下げ、より早期から即時併合を優先
        # refs: game_history/20260312_073925_score0472.jsonl, game_history/20260312_074619_score1782.jsonl, strategy_versions/best_score5310_strategy.py
        if max_y >= 2.0 and reactive_pair_count >= 2:
            height_multiplier = 0.0

        height_penalty = landing_y * height_multiplier * height_mult

        if phase == "HIGH" and landing_y > 0.5:
            height_penalty *= 2.0
            reasons.append("HIGH_TOWER")
        elif phase == "MEDIUM" and landing_y > 0.5:
            height_penalty *= 1.5
            reasons.append("MEDIUM_TOWER")
        elif landing_y > 0.0:
            reasons.append("HIGH_LAYER")

        score -= height_penalty

        # ----- v165: 即時併合直接ボーナス閾値引き下げ版 -----
        # v163のペナルティ削減ロジックは削除済み
        # ベスト戦略(best_score5310)のREACTIVE_MERGE_PRIORITYアプローチを採用
        # reactive_pairs >= 2の場合、DIRECT/NEARマージに+500.0ボーナスを付与
        # 危険局面(dangerous_situation=True)でもボーナスが適用されるため、即時併合が選択される
        # refs: game_history/20260312_073925_score0472.jsonl turns 56-63, game_history/20260312_074619_score1782.jsonl turns 86-93, strategy_versions/best_score5310_strategy.py
        if reactive_pair_count >= 2 and merge_grade in ["DIRECT", "NEAR"]:
            score += 500.0
            reasons.append("REACTIVE_MERGE_PRIORITY")

        # ----- evaluation axis 3: drift penalty -----
        drift_penalty = (abs(drift_x) + drift_unc) * 30.0
        score -= drift_penalty

        # ----- evaluation axis 4: left-right balance correction -----
        balance_strength = 20.0
        if phase == "HIGH":
            balance_strength = 50.0
        elif phase == "MEDIUM":
            balance_strength = 40.0

        left_count = sum(1 for p in pieces if p["x"] < 0)
        right_count = len(pieces) - left_count
        balance_bias = (right_count - left_count) / (len(pieces) if pieces else 1)

        balance_penalty = x * balance_bias * balance_strength
        score -= abs(balance_penalty)

        # ----- evaluation axis 5: nextNext centering -----
        if next_next_type == next_type:
            center_bonus = max(0, 1.0 - abs(x) / 2.0) * 50.0
            score += center_bonus
            reasons.append("NEXT_SAME")

        # ----- evaluation axis 5.5: sandwich situation avoidance (v162: 中盤低反応器活性化版) -----
        # 盤面A・nextB・nextNextAの状況で、A上にBを置くとnextNextの併合機会を逃す
        # ワーストゲーム(score0328, score0849)の失敗パターン分析に基づき、中盤フェーズでreactive_pairsが少ない場合、
        # 即時併合を逃してHEIGHT_CONTROLを選択すると早期ゲームオーバーになる問題を解消。
        #
        # v162の改善点：
        # 1. 中盤フェーズ(0.8 <= max_y < 1.8)でreactive_pairs <= 1の場合、SANDWICH_AVOIDを有効化
        #    - 危険局面ではないが、盤面圧迫を回避するために即時併合を優先
        #    - reactive_pairs <= 1 は盤面がまだ疎で、即時併合による盤面圧縮が効果的である状況
        # 2. 即時併合機会の見逃し回避
        #    - batch_summaryでHEIGHT_CONTROLが29.7%選択(avg_score_delta=2.2)と過剰であることを確認
        #    - 低スコア群でHEIGHT_CONTROL選択が高く、中盤での即時併合見逃しが早期ゲームオーバーの一因
        # refs: tmp/batch_summary.txt, tmp/improve_brief.md, game_history/20260311_224024_score0328.jsonl, game_history/20260311_222520_score0849.jsonl
        medium_phase_low_reactivity = phase == "MEDIUM" and reactive_pair_count <= 1
        if (
            (not dangerous_situation or medium_phase_low_reactivity)
            and next_type != 0
            and next_next_type != 0
            and next_type != next_next_type
        ):
            # 盤面上に next_type の駒があるかチェック
            has_next_type_on_board = any(p.get("type") == next_type for p in pieces)
            if has_next_type_on_board and merge_grade in ["DIRECT", "NEAR"]:
                # 即時併合候補なら、nextNext の併合機会を守るためにボーナスを与える
                # 中盤低反応器活性化の場合、ボーナスを強化して即時併合をより強力に優先
                bonus_amount = 400.0 if medium_phase_low_reactivity else 300.0
                score += bonus_amount
                reasons.append("SANDWICH_AVOID")

        # ----- evaluation axis 6: chain merge bonus (v159: 基礎距離4.0版） -----
        # ワーストゲームではmax_y>2.5の盤面でもchain_mergeが有効だった可能性がある
        # そのため、着地低でchain_mage_bonusが高い配置を評価する
        # ワーストゲームでの盤面圧縮戦略（盤面を下げる）と整合させるため、v159のパラメータを維持

        if merge_grade in ["DIRECT", "NEAR"] and result.get("merges"):
            merges = result["merges"]
            if merges:
                best_merge = min(merges, key=lambda m: m.get("dist", float("inf")))
                target_x = best_merge.get("x", 0)
                target_y = best_merge.get("y", 0)

                # v159: 着地高に応じた動的調整
                chain_distance_max = 4.0 + landing_y * 0.6
                chain_bonus_multiplier = 450.0 + landing_y * 150.0

                nearby_pieces = []
                for p in pieces:
                    if p.get("type") == next_type:
                        dist = (
                            (p["x"] - target_x) ** 2 + (p["y"] - target_y) ** 2
                        ) ** 0.5
                        if dist < chain_distance_max:
                            nearby_pieces.append((dist, p))

                # sort by distance
                nearby_pieces.sort(key=lambda x: x[0])

                # bonus calculation from closest 3 pieces using dynamic multiplier
                # 1st: (chain_distance_max - dist) * chain_bonus_multiplier
                # 2nd: (chain_distance_max - dist) * chain_bonus_multiplier * 0.5
                # 3rd: (chain_distance_max - dist) * chain_bonus_multiplier * 0.25
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

        # ----- evaluation axis 7: early game merge priority (NEW) -----
        # 危険局面（max_y >= 1.8, reactive_pairs >= 3）は、盤面圧縮が最優先
        # 早期マージで盤面を下げることは、後の盤面圧縮を容易にする
        # max_y < -2.0 かつ piece_count <= 15 の場合、EARLY_MERGE_PRIORITYを適用
        early_game = max_y < -2.0
        piece_count_threshold = 15

        if early_game and merge_grade == "NEAR":
            score += 500.0  # 危険局面での強力な早期マージ優先
            reasons.append("EARLY_MERGE_PRIORITY")

        # ----- evaluation axis 8: MEDIUM_TOWER selection promotion (v174 from v3805) -----
        # ベストゲーム(score3064)の戦略から採用
        # HIGHフェーズのheight_multiplier=1.8に対して、MEDIUMフェーズでは1.4と差をつけることで、
        # 着地が高い場合でも、マージ候補の中からMEDIUM_TOWERを選んで盤面を下げる
        if phase == "MEDIUM" and landing_y > 0.5:
            score += 200.0  # ベストゲームでの成功パラメータ
            reasons.append("MEDIUM_TOWER_PROMOTION")
        elif phase == "HIGH" and landing_y > 0.5:
            # HIGHフェーズでのMEDIUM_TOWERは高すぎるため、適用なし
            # そのままHIGH_TOWERとして評価（height_penalty *= 2.0）
            pass

        # ----- v167: 危険局面reactive_pairs活用による盤面圧縮調整版 -----
        # batch_summaryでHEIGHT_CONTROLが28.8%選択(avg_score_delta=0.9)と過剰であることを確認
        # ワーストゲーム(score0624)の終盤8ターン分析で、max_y>=2.0かつreactive_pairs=4-6あるにもかかわらず
        # merge_available=falseの状況でHIGH_TOWER/HIGH_LAYERが選択され続け、即時併合機会を逃している失敗パターンを特定
        # reactive_pairsが多い状況では、盤面の高さを下げるだけでなくreactive_pairsを活かす配置を優先する必要がある
        # reactive_pair_countに応じて盤面圧縮の強度を動的に調整し、reactive_pairsを活用する配置を優先する
        # refs: tmp/batch_summary.txt, tmp/improve_brief.md, tmp/advice.md, game_history/20260312_084756_score0624.jsonl, game_history/20260312_084231_score2819.jsonl, tmp/change_log.txt
        if max_y >= 2.0 and merge_grade == "NO":
            # reactive_pairsが多いほど、盤面圧縮の優先度を下げてreactive_pairsを活かす配置を優先
            # reactive_pairsは将来の併合機会であり、これを活かすことで盤面圧縮を促進できる
            # reactive_pair_count >= 3 の場合、盤面圧縮ボーナスを削減してreactive_pairs活用を優先
            reactive_compression_factor = max(0.3, 1.0 - (reactive_pair_count - 2) * 0.15)
            
            if deadline_margin < 0.0:
                # deadline_marginが負の値（deadlineを超えている）場合、絶対値が大きいほどより危険
                # 着地Yが低い配置にボーナスを付与（reactive_compression_factorで調整）
                danger_compression_bonus = ((-deadline_margin) * 100.0 + danger_piece_count * 50.0) * reactive_compression_factor
                score -= landing_y * danger_compression_bonus * 0.5
                reasons.append("DANGER_COMPRESSION")
            else:
                # deadline_marginが正の値（deadlineを超えていない）場合でも、着地Yが低い配置を優先
                # danger_piece_countが多いほど、盤面圧縮をより優先（reactive_compression_factorで調整）
                danger_compression_bonus = danger_piece_count * 30.0 * reactive_compression_factor
                score -= landing_y * danger_compression_bonus * 0.3
                reasons.append("DANGER_COMPRESSION")

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
