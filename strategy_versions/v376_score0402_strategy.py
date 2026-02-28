#!/usr/bin/env python3
"""strategy.py - AI改善対象の決定スクリプト"""

# 固定インターフェース:
# decide(game_state: dict, analysis: dict) -> dict
#    戻り値: {"x": float, "reason": str}
#
# AI改変可能: decide() 内部、ヘルパー関数、定数、import
# AI改変禁止: decide() シグネチャ、if __name__ == "__main__" ブロック

# --- 変更履歴 ---
# v336: v128完全復帰・先読みマージ強化版 - v335の大失敗(avg=507.3)を受けて、動的切り替えを完全廃止しv128のシンプルな構造に復帰。
#   v335バッチ分析から特定した問題:
#   - avg_score 507.3: v128の3689から大幅低下、動的切り替えは完全に失敗
#   - merge_rate 9.4%: 目標15%に届かず、動的切り替えがマージ機会を損失
#   - ターン数62.7: v128の90ターンより短く、早くゲームオーバー
#   - 動的切り替えの失敗: merge_available判定(near_pairs>=2)が不正確で、height_multを間違えて調整
#   - 振り子パターン検出: v334(height_mult=2.6, avg=1681)→v335(動的切り替え, avg=507.3)→v370(height_mult=2.6, avg=1681)
#   根本原因:
#   - v335の動的切り替えは、v128の成功要因（シンプルで頑健なHIGHフェーズ戦略）を壊した
#   - merge_availableの判定が不正確で、マージ機会がない時に誤ってheight_mult=1.8に緩和し、安定性を損なった
#   - reactor情報を動的調整に使ったが、v128はreactor情報なしで3689を達成しており、動的調整は不要
#   - v335の複雑な条件分岐（merge_availableによる動的切り替え）は、振り子パターンの典型
#   解決策（v128完全復帰・先読みマージ強化）:
#   - 動的切り替え完全廃止: HIGHフェーズで一貫してheight_mult=1.8を適用（v128の成功設定）
#   - v128のシンプル構造完全復帰: phase判定、height_penalty、drift_penalty、balance_penaltyをv128のまま採用
#   - reactor情報の活用方法変更: 動的調整ではなく、静的なボーナスとして活用（v128の成功を維持）
#   - 先読みマージ強化: 盤面のtype N-1ピースの位置から、将来のマージ期待値を計算してスコアリングに反映
#   - HIGHフェーズ振動戦略: 盤面を高めに保ち、振動による連鎖マージを促進（v128の成功要因を維持）
#   核心的発見: v335の動的切り替えは振り子パターン（v334→v335→v334）。v128の成功は「動的切り替え」ではなく「シンプルで頑健なHIGHフェーズ戦略」にある。v128の構造を完全復帰しつつ、先読みマージ強化でv128の3689を超えることを目指す。
#   成功基準: avg_scoreがv335の507.3以上、またはmerge_rateが15%以上、またはavg_scoreがv128の3689以上
#   失敗基準: avg_scoreがv334の1681未満、またはmerge_rateが10%未満、またはavg_scoreがv335の507.3未満
# [BEST:3689] v128: HIGHフェーズマージ優先版
# [BEST:2335] v42: v19復活・v31/v29複雑化要素削除版
# [BEST:1509] v328: HIGHフェーズマージ強化・v42ベース版


def decide(game_state: dict, analysis: dict) -> dict:
    """v128完全復帰・先読みマージ強化版。動的切り替えを廃止し、v128のシンプル構造に復帰しつつ、先読みマージ戦略を強化。"""

    results = analysis.get("results", [])

    if not results:
        return {"x": 0.0, "reason": "no analysis data"}

    best_x = 0.0
    best_score = -float("inf")
    best_reason = ""

    # 盤面情報
    pieces = game_state.get("pieces", [])
    max_y = max([p["y"] for p in pieces]) if pieces else -4.0

    # reactor情報（v336: 静的なボーナスとして活用）
    reactor = analysis.get("reactor", {})
    reactive_pairs_val = reactor.get("reactive_pairs", 0)
    reactive_pairs = (
        len(reactive_pairs_val)
        if isinstance(reactive_pairs_val, list)
        else reactive_pairs_val
    )
    near_pairs_val = reactor.get("near_pairs", 0)
    near_pairs = (
        len(near_pairs_val) if isinstance(near_pairs_val, list) else near_pairs_val
    )

    # フェーズ判定（v336: v128の閾値0.8/1.8/3.0を採用、動的切り替えなし）
    if max_y < 0.8:
        phase = "LOW"
        height_mult = 1.0
        merge_mult = 1.2
    elif max_y < 1.8:
        phase = "MEDIUM"
        height_mult = 2.4  # v128: v42の2.4を維持
        merge_mult = 1.0
    elif max_y < 3.0:
        phase = "HIGH"
        height_mult = 1.8  # v336: v128の1.8を維持、動的切り替えなし
        merge_mult = 1.0
    else:
        phase = "CRITICAL"
        height_mult = 1.0
        merge_mult = 0.6

    # 次のピース情報
    next_piece = game_state.get("next", {})
    next_next_piece = game_state.get("nextNext", {})
    next_type = next_piece.get("type", 0)
    next_next_type = next_next_piece.get("type", 0)

    # 先読みマージ期待値計算（v336: 新規導入）
    # 盤面のtype N-1のピース数と位置から、将来のマージ期待値を計算
    future_merge_expectation = 0.0
    reasons_prefix = []
    if next_type > 1:
        target_type = next_type - 1
        target_pieces = [p for p in pieces if p["type"] == target_type]
        if len(target_pieces) >= 2:
            # type N-1が2個以上あれば、マージしてtype Nになる確率が高い
            # ペア間の距離が近いほどマージ確率が高い
            min_pair_dist = 3.0
            for i in range(len(target_pieces)):
                for j in range(i + 1, len(target_pieces)):
                    dist = abs(target_pieces[i]["x"] - target_pieces[j]["x"])
                    min_pair_dist = min(min_pair_dist, dist)
            # 距離に応じた期待値（近いほど高い）
            if min_pair_dist < 0.5:
                future_merge_expectation = 300.0
                reasons_prefix.append("FUTURE_MERGE_HIGH")
            elif min_pair_dist < 1.0:
                future_merge_expectation = 150.0
                reasons_prefix.append("FUTURE_MERGE_MID")
            elif min_pair_dist < 1.5:
                future_merge_expectation = 75.0
                reasons_prefix.append("FUTURE_MERGE_LOW")
        elif len(target_pieces) == 1:
            # 1個なら、nextNextでタイプNが来たときにマージする確率
            if next_next_type == next_type:
                future_merge_expectation = 100.0
                reasons_prefix.append("FUTURE_MERGE_NEXT")

    for result in results:
        x = result["x"]
        landing_y = result.get("landing_y", 0)
        drift_x = result.get("drift_x", 0)
        drift_unc = result.get("drift_unc", 0)
        merge_grade = result.get("merge_grade", "NO")

        score = 0.0
        reasons = reasons_prefix.copy()

        # === v336: v128完全復帰・先読みマージ強化 ===

        # 1. マージグレードによるスコア（v336: v128の値を維持）
        merge_bonus = 0.0
        if merge_grade == "DIRECT":
            merge_bonus = 1200.0 * merge_mult
            reasons.append("DIRECT_MERGE")
        elif merge_grade == "NEAR":
            merge_bonus = 600.0 * merge_mult
            reasons.append("NEAR_MERGE")
        elif merge_grade == "FAR":
            merge_bonus = 200.0 * merge_mult
            reasons.append("FAR_MERGE")

        # nextNextが同じタイプならボーナス係数（v336: v128の値を維持）
        if next_next_type == next_type:
            merge_bonus *= 1.2
            reasons.append("NEXT_SAME")

        score += merge_bonus

        # 2. 先読みマージ期待値ボーナス（v336: 新規導入）
        # 今マージできなくても、将来マージが期待できるならボーナス
        if future_merge_expectation > 0 and merge_grade == "NO":
            # マージ不可でも期待値があるなら、将来マージのためにボーナス
            score += future_merge_expectation * 0.5

        # 3. 高度によるペナルティ（v336: v128の値を維持、動的切り替えなし）
        height_penalty = landing_y * 50.0 * height_mult

        # HIGH_TOWERペナルティ（v336: v128の緩和設定を維持、動的切り替えなし）
        if phase == "HIGH" and landing_y > 0.5:
            height_penalty *= 1.3  # v128: v84の1.3倍を採用
            reasons.append("HIGH_TOWER")
        elif phase == "MEDIUM" and landing_y > 0.5:
            height_penalty *= 1.5
            reasons.append("MEDIUM_TOWER")
        elif landing_y > 0.0:
            reasons.append("HIGH_LAYER")

        score -= height_penalty

        # 4. ドリフトによるペナルティ（v336: v128の一律30.0を維持）
        drift_penalty = (abs(drift_x) + drift_unc) * 30.0
        score -= drift_penalty

        # 5. 左右バランス補正（v336: v128の値を維持）
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

        # 6. nextNextが同じタイプなら中央寄せボーナス（v336: v128の一律50.0を維持）
        if next_next_type == next_type:
            center_bonus = max(0, 1.0 - abs(x) / 2.0) * 50.0
            score += center_bonus
            if "CENTER" not in reasons:
                reasons.append("CENTER")

        # 7. reactor情報活用ボーナス（v336: 静的なボーナスとして活用、動的調整なし）
        # reactive_pairsが多いほど、盤面が活発でマージが起きやすい
        if reactive_pairs >= 3:
            score += 50.0
            reasons.append("REACTIVE")
        elif reactive_pairs >= 1:
            score += 20.0

        # near_pairsが多いほど、マージ機会が多い
        if near_pairs >= 2:
            score += 30.0
            reasons.append("NEAR_PAIR")

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
