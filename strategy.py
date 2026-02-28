#!/usr/bin/env python3
"""strategy.py - AI改善対象の決定スクリプト"""

# 固定インターフェース:
# decide(game_state: dict, analysis: dict) -> dict
#    戻り値: {"x": float, "reason": str}
#
# AI改変可能: decide() 内部、ヘルパー関数、定数、import
# AI改変禁止: decide() シグネチャ、if __name__ == "__main__" ブロック

# --- 変更履歴 ---
# v321: 激進的マージ優先・高度ペナルティ全廃版 - v313-v320の停滞（avg=1226.2、stddev=743.9、HEIGHT_CONTROL支配40%・avg_score_delta=7.3）を受けて、振り子パターンを根本的に断ち切る革命的変更。
#   v313-v320バッチ分析から特定した問題:
#   - HEIGHT_CONTROLが支配的かつ低効果: 20.5%の決定でavg_score_delta=7.1のみ。高度管理がマージ機会を犠牲にしている
#   - マージ機会が極めて少ない: DIRECT_MERGEが2.2%、NEAR_MERGEが7.0%のみ。マージ優先が機能していない
#   - バッチ平均とベストスコアの乖離: ベスト2391点なのにavg=1226.2、戦略が極めて不安定（stddev=743.9）
#   - v313-v320が完全に同一: 同一戦略を異なる乱数シードでテストしているだけ、改良が進んでいない
#   - スコアリング計算のパラドックス: DIRECT_MERGE=1200点でもheight_mult=1.8・balance/driftペナルティにより、実際にはマージが選択されない
#   根本原因:
#   - マージボーナスが相対的に弱すぎる: height/drift/balanceペナルティがマージボーナスを中和・逆転している
#   - 高度ペナルティとマージ優先のトレードオフが本質的に解決不可能: マージ位置（盤面上方）と高度回避（盤面下方）が矛盾
#   - v128構造が局所最適にハマっている: height_multをどう調整しても、マージ機会確保と高度管理の両立は不可能
#   解決策（激進的マージ優先・高度ペナルティ全廃）:
#   - マージボーナス2.5倍強化: DIRECT=3000/NEAR=1000/FAR=300にし、マージを圧倒的に有利にする
#   - 高度ペナルティ完全廃止: height_penalty計算を完全削除。盤面の高さは無視し、マージ機会のみを見る
#   - precisionペナルティのみ維持: drift_penaltyとbalance_penaltyは維持し、着地位置の精度を担保
#   - 振り子パターン根本解決: 「height_multを調整する」のではなく、「height_multという概念自体を廃止」することでトレードオフを解消
#   - chain reaction仮説検証: マージを強制的に連鎖させ、盤面振動・連鎖マージがスコアブレイクスルーになるかを実験
#   - リスク: 高度管理を全くしないため、盤面崩壊のリスクは高いが、chain reactionが起こればリスクは報酬に変わる
#   - 成功基準: マージ決定率が20%以上（v313の8%から大幅改善）し、avg_scoreが1500以上になれば成功
#   - 失敗基準: avg_scoreがv313の1122以下、または盤面崩壊で即敗北率が50%以上
# [BEST:3689] v128: HIGHフェーズマージ優先版
# [BEST:2335] v42: v19復活・v31/v29複雑化要素削除版


def decide(game_state: dict, analysis: dict) -> dict:
    """v321: 激進的マージ優先・高度ペナルティ全廃版。マージを圧倒的に優先し、高度管理を完全に放棄する革命的戦略。"""

    results = analysis.get("results", [])

    if not results:
        return {"x": 0.0, "reason": "no analysis data"}

    best_x = 0.0
    best_score = -float("inf")
    best_reason = ""

    # 盤面情報
    pieces = game_state.get("pieces", [])

    # 次のピース情報
    next_piece = game_state.get("next", {})
    next_next_piece = game_state.get("nextNext", {})
    next_type = next_piece.get("type", 0)
    next_next_type = next_next_piece.get("type", 0)

    for result in results:
        x = result["x"]
        drift_x = result.get("drift_x", 0)
        drift_unc = result.get("drift_unc", 0)
        merge_grade = result.get("merge_grade", "NO")

        score = 0.0
        reasons = []

        # === v321: 激進的マージ優先 ===

        # 1. マージグレードによるスコア（v321: 2.5倍強化）
        if merge_grade == "DIRECT":
            score += 3000.0  # v321: 1200→3000に2.5倍強化
            reasons.append("DIRECT_MERGE")
        elif merge_grade == "NEAR":
            score += 1000.0  # v321: 600→1000に強化
            reasons.append("NEAR_MERGE")
        elif merge_grade == "FAR":
            score += 300.0  # v321: 200→300に強化
            reasons.append("FAR_MERGE")
        # v321: マージボーナス2.5倍強化により、precisionペナルティを完全に凌駕する

        # 2. 高度ペナルティ完全廃止（v321: 革命的変更）
        # height_penalty計算を完全削除。盤面の高さは無視し、マージ機会のみを見る。
        # これにより「マージ位置（盤面上方）vs高度回避（盤面下方）」のトレードオフを根本的に解消

        # 3. ドリフトによるペナルティ（v321: 精度確保のため維持）
        drift_penalty = (abs(drift_x) + drift_unc) * 30.0
        score -= drift_penalty

        # 4. 左右バランス補正（v321: 精度確保のため維持）
        left_count = sum(1 for p in pieces if p["x"] < 0)
        right_count = len(pieces) - left_count
        balance_bias = (right_count - left_count) / (len(pieces) if pieces else 1)
        balance_strength = 20.0  # v321: 全フェーズ一律20.0に簡素化

        balance_penalty = x * balance_bias * balance_strength
        score -= abs(balance_penalty)

        # 5. nextNextが同じタイプなら中央寄せボーナス（v321: 50.0維持）
        if next_next_type == next_type:
            center_bonus = max(0, 1.0 - abs(x) / 2.0) * 50.0
            score += center_bonus
            reasons.append("NEXT_SAME")

        # スコア更新（v321: マージがなければPRECISION_CONTROL）
        if score > best_score:
            best_score = score
            best_x = x
            best_reason = "_".join(reasons) if reasons else "PRECISION_CONTROL"

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
