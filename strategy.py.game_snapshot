#!/usr/bin/env python3
"""strategy.py - AI改善対象の決定スクリプト (v547: HIGH_LAYER強化・ドリフト簡略化版)"""

# 固定インターフェース:
# decide(game_state: dict, analysis: dict) -> dict
#    戻り値: {"x": float, "reason": str}
#
# AI改変可能: decide() 内部、ヘルパー関数、定数、import
# AI改変禁止: decide() シグネチャ、if __name__ == "__main__" ブロック

# --- 変更履歴 ---
# v542: v540/v128単純化版 - v422の複雑さを除去し、v540(2176点)とv128の成功構造に戻る。batch_summary.txtでv540が最高点2176を達成し、v128-like構造は平均1170.8点を出したことが確認された。v422の複雑なロジック（先読みマージボーナス支配62.7%、チェーン予測、reactor情報）は実際のマージ率を低下させていた。
#   根本原因の特定:
#   - v422のFUTURE_MERGEが62.7%を占めるが、実際のマージ(DIRECT/NEAR/FAR)は4.5%しかない
#   - 先読みボーナスが支配的すぎて、実際のマージ機会を見逃している
#   - v422のチェーン予測、reactor情報(near_pairs、reactive_pairs)はオーバーヘッド
#   - v422のmerge_bonus: DIRECT 2500/NEAR 1500/FAR 800 は大きすぎて判断を歪める
#   改善策(v540/v128単純化):
#   - マージボーナスをv128レベルに戻す: DIRECT 1200/NEAR 600/FAR 200（実用的なバランス）
#   - 先読みマージボーナスとチェーン予測を削除（実際のマージに集中）
#   - reactor情報(near_pairs、reactive_pairs)を削除（単純化）
#   - フェーズ判定と高度ペナルティを維持（v128の成功要素）
#   - ドリフトペナルティ、バランス補正、中央寄せボーナスを維持（基本制御）
#   核心的発見: v540とv128の単純構造が最高点2176と平均1170.8点を出した。複雑さを除去し、実際のマージに集中することでスコア向上。
#   成功基準: scoreがv540の2176に近づく、または平均がv422を上回る
#   失敗基準: scoreがv422以下、または実際のマージ率が5%以下
# v546: 動的ドリフトペナルティ・HIGHフェーズマージ強化版 - v542のbatch_summary分析で、「HIGH_LAYERのavg_score_deltaが29.5（高スコア群で32.1%）」と高いことを特定。HIGH_LAYER（0.5以下の高さ）でのスコア獲得が重要であることが判明。v546ではドリフトペナルティを動的に計算し、盤面の状況に応じて調整。HIGHフェーズではドリフトペナルティを緩和し、マージ機会を確保。MEDIUMフェーズではドリフトペナルティを強化し、正確な着地予測でHIGH到達遅延。また、HIGHフェーズではバランス補正を緩和し（balance_strengthを20.0に減）、マージ優先を実現。MEDIUMフェーズではバランス補正を維持しつつ、HIGH到達遅延を狙う。v542の基本構造を維持しつつ、動的ペナルティ計算でスコア向上。
#   バッチ統計分析結果:
#   - avg_score_delta: HIGH_CONTROL=15.0, HIGH_LAYER=29.5, MEDIUM_TOWER=16.5, HIGH_TOWER=2.3
#   - 高スコア群のreason上位5: HEIGHT_CONTROL(32.1%), NEAR_MERGE(11.5%), HIGH_LAYER(10.9%), MEDIUM_TOWER(8.3%), HIGH_TOWER(7.7%)
#   - 低スコア群のreason上位5: HEIGHT_CONTROL(28.0%), NEAR_MERGE(15.3%), NEAR_MERGE_HIGH_LAYER(11.0%), HIGH_LAYER(10.2%), NEAR_MERGE_MEDIUM_TOWER(6.8%)
#   改善策:
#   - 動的ドリフトペナルティ: ドリフト不確定性(drift_unc)が大きいほどペナルティを強化（マージ失敗リスクが高いため）
#   - HIGHフェーズドリフト緩和: HIGHフェーズではドリフトペナルティを半減（マージ機会確保優先）
#   - HIGHフェーズバランス緩和: balance_strengthを40.0から20.0に減（マージ優先）
#   - HIGH_LAYERボーナス: 0.5以下の高さでの着地にボーナスを追加（HIGH_LAYERでのスコア獲得促進）
# v547: HIGH_LAYER強化・ドリフト簡略化版 - v546のbatch_summary分析で、「HIGH_LAYER_BONUSのavg_score_deltaが19.9で非常に高い」ことを特定。HIGH_LAYER_BONUSは機能しているが、ボーナス値が30点と小さすぎる（merge_bonusのDIRECT 1200点と比較して小さすぎる）。また、HIGH_LAYERのavg_score_deltaが6.7と低く、HIGH_LAYER_BONUS（19.9）と大きく乖離している。v547ではHIGH_LAYERボーナスを段階的に強化し（0.5以下:100点、0.5-0.8:50点、0.8-1.0:20点）、HIGH_LAYER全体の価値を高める。また、ドリフトペナルティの動的調整を削除し、v128の一律30.0に戻すことでシンプル化。HIGH_TOWERペナルティを1.3倍から1.5倍に強化し、バランス補正をv128の40.0に戻すことで、v546のHIGH_LAYERボーナスの成功要素とv128のバランス補正の成功要素を組み合わせる。さらに、next/nextNextのタイプを考慮したマージ期待値ボーナスを追加し、将来のマージが期待できる位置を優先する。
#   バッチ統計分析結果:
#   - HIGH_LAYER_BONUS: 11.3%, avg_score_delta=19.9 (非常に高い)
#   - HIGH_LAYER: 7.0%, avg_score_delta=6.7 (低い)
#   - 乖離: HIGH_LAYER_BONUSは有効だが、HIGH_LAYER全体の価値が低い
#   - 高スコア群のmerge_rate: 14.8% (低スコア群の11.6%より高い)
#   - ベストゲーム(1558点)の特徴: NEAR_MERGE_HIGH_LAYER_BONUSが頻繁に見られる
#   改善策:
#   - HIGH_LAYERボーナス強化: 0.5以下:100点（v546の30点から3.3倍）、0.5-0.8:50点、0.8-1.0:20点
#   - ドリフトペナルティ簡略化: v128の一律30.0に戻す (abs(drift_x) + drift_unc) * 30.0
#   - HIGH_TOWERペナルティ強化: 1.3倍から1.5倍に（v128の1.5倍に近づける）
#   - バランス補正強化: v128の40.0に戻す（HIGHフェーズでバランスを重視）
#   - マージ期待値ボーナス: next/nextNextのタイプを考慮し、将来のマージが期待できる位置にボーナス


def decide(game_state: dict, analysis: dict) -> dict:
    """v547: HIGH_LAYER強化・ドリフト簡略化版"""

    results = analysis.get("results", [])

    if not results:
        return {"x": 0.0, "reason": "no analysis data"}

    best_x = 0.0
    best_score = -float("inf")
    best_reason = ""

    # 盤面情報
    pieces = game_state.get("pieces", [])
    max_y = max([p["y"] for p in pieces]) if pieces else -4.0

    # フェーズ判定（v547: v128の設定を維持）
    if max_y < 0.8:
        phase = "LOW"
        height_mult = 1.0
        merge_mult = 1.2
    elif max_y < 1.8:
        phase = "MEDIUM"
        height_mult = 2.4  # v128の設定
        merge_mult = 1.0
    elif max_y < 3.0:
        phase = "HIGH"
        height_mult = 1.8  # v128の設定
        merge_mult = 1.0
    else:
        phase = "CRITICAL"
        height_mult = 1.0  # CRITICAL: height_multなし
        merge_mult = 0.6

    # nextNextピース情報
    next_piece = game_state.get("next", {})
    next_next_piece = game_state.get("nextNext", {})
    next_type = next_piece.get("type", 0)
    next_next_type = next_next_piece.get("type", 0)

    # マージ期待値計算用: ピースタイプごとのスコア
    merge_score_table = {
        1: 1,
        2: 3,
        3: 6,
        4: 10,
        5: 15,
        6: 21,
        7: 28,
        8: 36,
        9: 45,
        10: 55,
        11: 66,
        12: 78,
        13: 91,
        14: 105,
        15: 120,
    }

    # マージ期待値を計算: next/nextNextが盤面のどのタイプとマージするか
    def calc_merge_expectation(x_pos, pieces, next_type_val, next_next_type_val):
        """x_posに置いた場合、将来のマージ期待値を計算"""
        expectation = 0.0

        # next_typeがマージする候補を検索
        for p in pieces:
            p_type = p.get("type", 0)
            if p_type == next_type_val - 1:  # next_typeとマージ可能
                # 距離を計算
                dist = abs(p["x"] - x_pos)
                if dist < 1.0:  # 近距離なら高い期待値
                    expectation += merge_score_table.get(next_type_val, 0) * 0.5

        # next_next_typeも考慮
        for p in pieces:
            p_type = p.get("type", 0)
            if p_type == next_next_type_val - 1:  # next_next_typeとマージ可能
                dist = abs(p["x"] - x_pos)
                if dist < 1.5:  # 少し遠くてもOK
                    expectation += merge_score_table.get(next_next_type_val, 0) * 0.3

        return expectation

    for result in results:
        x = result["x"]
        landing_y = result.get("landing_y", 0)
        drift_x = result.get("drift_x", 0)
        drift_unc = result.get("drift_unc", 0)
        merge_grade = result.get("merge_grade", "NO")

        score = 0.0
        reasons = []

        # === v547: HIGH_LAYER強化・ドリフト簡略化版 ===

        # 1. マージグレードによるスコア（v547: v128の実用的値を維持）
        if merge_grade == "DIRECT":
            score += 1200.0 * merge_mult
            reasons.append("DIRECT_MERGE")
        elif merge_grade == "NEAR":
            score += 600.0 * merge_mult
            reasons.append("NEAR_MERGE")
        elif merge_grade == "FAR":
            score += 200.0 * merge_mult
            reasons.append("FAR_MERGE")

        # 2. 高度によるペナルティ（v547: v128の設定を維持）
        height_penalty = landing_y * 50.0 * height_mult

        # MEDIUM/HIGHフェーズのタワー判定（v547: HIGH_TOWERペナルティを1.5倍に強化）
        if phase == "HIGH" and landing_y > 0.5:
            height_penalty *= 1.5  # v547: v128の1.5倍に近づける（v546の1.3倍から強化）
            reasons.append("HIGH_TOWER")
        elif phase == "MEDIUM" and landing_y > 0.5:
            height_penalty *= 1.5  # v128の設定
            reasons.append("MEDIUM_TOWER")
        elif landing_y > 0.0:
            # v547: HIGH_LAYERボーナスを段階的に強化
            if landing_y <= 0.5:
                score += 100.0  # v547: v546の30点から3.3倍に強化
                reasons.append("HIGH_LAYER_LOW")
            elif landing_y <= 0.8:
                score += 50.0  # v547: 新規追加
                reasons.append("HIGH_LAYER_MID")
            elif landing_y <= 1.0:
                score += 20.0  # v547: 新規追加
                reasons.append("HIGH_LAYER_HIGH")
            else:
                reasons.append("HIGH_LAYER")

        score -= height_penalty

        # 3. ドリフトによるペナルティ（v547: v128の一律30.0に戻す）
        drift_penalty = (abs(drift_x) + drift_unc) * 30.0
        score -= drift_penalty

        # 4. 左右バランス補正（v547: v128の40.0に戻す）
        balance_strength = 20.0
        if phase == "HIGH":
            balance_strength = 40.0  # v547: v128の40.0に戻す（v546の20.0から強化）
        elif phase == "MEDIUM":
            balance_strength = 30.0  # v128の設定

        left_count = sum(1 for p in pieces if p["x"] < 0)
        right_count = len(pieces) - left_count
        balance_bias = (right_count - left_count) / (len(pieces) if pieces else 1)

        balance_penalty = x * balance_bias * balance_strength
        score -= abs(balance_penalty)

        # 5. nextNextが同じタイプなら中央寄せボーナス（v547: v128の設定を維持）
        if next_next_type == next_type:
            center_bonus = max(0, 1.0 - abs(x) / 2.0) * 50.0
            score += center_bonus
            reasons.append("NEXT_SAME")

        # 6. マージ期待値ボーナス（v547: 新規追加）
        merge_expectation = calc_merge_expectation(x, pieces, next_type, next_next_type)
        if merge_expectation > 0:
            score += merge_expectation
            reasons.append("MERGE_EXPECT")

        # スコア更新
        if score > best_score:
            best_score = score
            best_x = x
            best_reason = "_".join(reasons) if reasons else "HEIGHT_CONTROL"

    # 安全な範囲内にクリップ
    best_x = max(-3.0, min(3.0, best_x))
    best_x = round(best_x, 2)

    return {"x": best_x, "reason": best_reason}


# --- AI改変禁止ゾーン ---
if __name__ == "__main__":
    import json
    import sys

    # スタンドアロンテスト用
    gs_path = sys.argv[1] if len(sys.argv) > 1 else "game_state.json"

    try:
        game_state = json.load(open(gs_path))
    except Exception as e:
        print(json.dumps({"error": str(e)}))
        sys.exit(1)

    # analyze_board から解析データ取得
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
            "merge_history": [],
        }
    except Exception as e:
        analysis = {
            "results": [],
            "same_type": [],
            "reactor": {},
            "merge_history": [],
            "error": str(e),
        }

    result = decide(game_state, analysis)
    print(json.dumps(result, ensure_ascii=False, indent=2))
