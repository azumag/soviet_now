#!/usr/bin/env python3
"""strategy.py - AI改善対象の決定スクリプト"""

# 固定インターフェース:
# decide(game_state: dict, analysis: dict) -> dict
#    戻り値: {"x": float, "reason": str}
#
# AI改変可能: decide() 内部、ヘルパー関数、定数、import
# AI改変禁止: decide() シグネチャ、if __name__ == "__main__" ブロック

# --- 変更履歴 ---
# [BEST:3689] v128: HIGHフェーズマージ優先版
# [BEST:2335] v42: v19復活・v31/v29複雑化要素削除版
# v274 (1805): v229ベース - シンプル構造で1805点達成
# v231: v230期待値係数強化版 - 期待値係数を15.0→20.0に強化し、期待値ボーナスの影響力を向上
import math


def calculate_merge_expectation(pieces, x, next_type, next_next_type, reactor):
    """マージ期待値を計算

    Args:
        pieces: 全ピースのリスト
        x: ピースを置くX座標
        next_type: 次のピースタイプ
        next_next_type: 次の次のピースタイプ
        reactor: リアクター状態

    Returns:
        expectation: マージ期待値（0〜100）
    """
    if next_type == 0:
        return 0.0

    # type N-1のピースを抽出（type next_type - 1）
    target_type = next_type - 1
    if target_type < 1:
        target_type = 13  # ループする場合

    # type N-1のピースを抽出
    type_minus_1_pieces = [p for p in pieces if p["type"] == target_type]

    if not type_minus_1_pieces:
        return 0.0

    # type N-1のペアを検出（距離1.5以内）
    pairs = []
    for i, p1 in enumerate(type_minus_1_pieces):
        for p2 in type_minus_1_pieces[i + 1 :]:
            dist = math.sqrt((p1["x"] - p2["x"]) ** 2 + (p1["y"] - p2["y"]) ** 2)
            if dist < 1.5:
                pairs.append((p1, p2, dist))

    if not pairs:
        return 0.0

    # ペアの重心を計算
    center_x = sum((p1["x"] + p2["x"]) / 2 for p1, p2, _ in pairs) / len(pairs)
    center_y = sum((p1["y"] + p2["y"]) / 2 for p1, p2, _ in pairs) / len(pairs)

    # ペアの重心からの距離を計算（距離が近いほど期待値が高い）
    dist_to_center = math.sqrt((x - center_x) ** 2 + (0 - center_y) ** 2)

    # ペアの数と重心からの距離から期待値を計算
    # ペアの数が多いほど期待値が高い
    # 重心から近いほど期待値が高い
    pair_count_bonus = min(len(pairs) * 20.0, 75.0)  # 最大75点（v231: 15.0→20.0に強化）
    distance_bonus = (
        max(0, 3.0 - dist_to_center) * 15.0
    )  # 最大45点（v231: 10.0→15.0に強化）

    expectation = pair_count_bonus + distance_bonus

    return min(expectation, 100.0)


def decide(game_state: dict, analysis: dict) -> dict:
    """v231: v230期待値係数強化版

    v230の成功構造を維持しつつ、期待値係数を強化し、期待値機能の影響力を向上。

    v230の問題点（バッチ分析から特定）:
    - 期待値ボーナス（max 500点）に対し、期待値自体が最大105点に制限
    - 期待値係数15.0では、ペアボーナスmax75点 + 距離ボーナスmax30点 = 105点
    - マージボーナス（DIRECT=1200/NEAR=600/FAR=200）と比較して影響力が弱い

    解決策（期待値係数強化）:
    - ペアボーナス係数: 15.0 → 20.0（ペア数の重要度を強化）
    - 距離ボーナス係数: 10.0 → 15.0（重心からの距離の重要度を強化）
    - 期待値最大値: 75点 + 45点 = 120点（期待値ボーナスmax 600点まで強化）
    - v274のシンプル構造を維持：期待値機能を追加しつつシンプルさを保つ
    - 高度ペナルティ緩和条件緩和：期待値40.0以上で緩和（v230の50.0→40.0に変更）
    """

    results = analysis.get("results", [])

    if not results:
        return {"x": 0.0, "reason": "no analysis data"}

    best_x = 0.0
    best_score = -float("inf")
    best_reason = ""

    # 盤面情報
    pieces = game_state.get("pieces", [])
    max_y = max([p["y"] for p in pieces]) if pieces else -4.0

    # リアクター状態を取得
    reactor = analysis.get("reactor", {})

    # フェーズ判定（v42/v128の閾値を採用）
    if max_y < 0.8:
        phase = "LOW"
        height_mult = 1.0
        merge_mult = 1.2
    elif max_y < 1.8:
        phase = "MEDIUM"
        height_mult = 2.4  # v274: v42の2.4を採用
        merge_mult = 1.0
    elif max_y < 3.0:
        phase = "HIGH"
        height_mult = 1.8  # v274: v128の1.8を採用
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

    for result in results:
        x = result["x"]
        landing_y = result.get("landing_y", 0)
        drift_x = result.get("drift_x", 0)
        drift_unc = result.get("drift_unc", 0)
        merge_grade = result.get("merge_grade", "NO")

        score = 0.0
        reasons = []

        # === v231: マージ期待値計算（期待値係数強化）===
        merge_expectation = calculate_merge_expectation(
            pieces, x, next_type, next_next_type, reactor
        )

        # マージ期待値ボーナス（期待値が高いほどボーナスが大きい）
        if merge_expectation > 20.0:
            score += (
                merge_expectation * 5.0
            )  # 最大600点（v231: max 105点*5=525点→max 120点*5=600点）
            if merge_expectation > 50.0:
                reasons.append("HIGH_EXPECTATION")
            else:
                reasons.append("MERGE_EXPECTATION")

        # === v42/v128成功要素統合: 強力なマージボーナス ===
        if merge_grade == "DIRECT":
            score += 1200.0 * merge_mult
            reasons.append("DIRECT_MERGE")
        elif merge_grade == "NEAR":
            score += 600.0 * merge_mult
            reasons.append("NEAR_MERGE")
        elif merge_grade == "FAR":
            score += 200.0 * merge_mult
            reasons.append("FAR_MERGE")

        # 2. 高度によるペナルティ（v42の一律50.0を維持）
        height_penalty = landing_y * 50.0 * height_mult

        # === v231: マージ期待値が高い場合、高度ペナルティを緩和（条件緩和）===
        if merge_expectation > 40.0:  # v231: v230の50.0→40.0に緩和
            # 高い期待値がある場合、高度ペナルティを50%緩和
            height_penalty *= 0.5

        # === HIGH_TOWERペナルティ簡素化（HIGHフェーズのみ1.3倍）===
        # MEDIUMフェーズではHIGH_TOWERペナルティを廃止（マージ優先徹底）
        if phase == "HIGH" and landing_y > 0.5:
            height_penalty *= 1.3
            reasons.append("HIGH_TOWER")
        elif phase == "MEDIUM" and landing_y > 0.5:
            # v274: v42の1.5倍を採用（MEDIUMフェーズでの適度な高度管理）
            height_penalty *= 1.5
            reasons.append("MEDIUM_TOWER")
        elif landing_y > 0.0:
            reasons.append("HIGH_LAYER")

        score -= height_penalty

        # 3. ドリフトによるペナルティ（v42の一律30.0を維持）
        drift_penalty = (abs(drift_x) + drift_unc) * 30.0
        score -= drift_penalty

        # 4. 左右バランス補正（v42の設定を維持）
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

        # 5. nextNextが同じタイプなら中央寄せボーナス（v42の一律50.0を維持）
        if next_next_type == next_type:
            center_bonus = max(0, 1.0 - abs(x) / 2.0) * 50.0
            score += center_bonus
            reasons.append("NEXT_SAME")

        # === マージ期待値が高い場合、HEIGHT_CONTROLを上書き ===
        if not reasons and merge_expectation > 30.0:
            reasons.append("MERGE_EXPECTATION")
        elif not reasons:
            reasons.append("HEIGHT_CONTROL")

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
        }
    except Exception as e:
        analysis = {"results": [], "same_type": [], "reactor": {}, "error": str(e)}

    result = decide(game_state, analysis)
    print(json.dumps(result, ensure_ascii=False, indent=2))
