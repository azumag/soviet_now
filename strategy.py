#!/usr/bin/env python3
"""strategy.py - AI改善対象の決定スクリプト (v543: v128構造完全復帰版)"""

# 固定インターフェース:
# decide(game_state: dict, analysis: dict) -> dict
#    戻り値: {"x": float, "reason": str}
#
# AI改変可能: decide() 内部、ヘルパー関数、定数、import
# AI改変禁止: decide() シグネチャ、if __name__ == "__main__" ブロック

# --- 変更履歴 ---
# v543: v128構造完全復帰版 - v542の失敗（scoreがv540の2176を大幅に下回る）を受けて、v542のv422複雑ロジックを完全に破棄し、v540とv128の成功構造に完全復帰する。v540は最高点2176を達成、v128は平均1170.8点という安定した成績を持つ。v422の複雑なロジック（reactor情報、チェーン予測、先読みマージボーナス）はオーバーヘッドで、実際のマージ率を低下させていた。v540/v128のシンプルな構造に完全復帰することで、実際のマージ率を向上し、安定して高いスコアを目指す。
#   根本原因の特定:
#   - v542はv422の複雑なロジックを継承していたが、そのロジックはオーバーヘッド
#   - batch_summary.txtでFUTURE_MERGEが62.7%を占めるが、実際のマージ（DIRECT/NEAR/FAR）は4.5%しかない
#   - v422のreactor情報（near_pairs、reactive_pairs）とチェーン予測は、判断を複雑にするだけでなく、実際のマージを見逃している
#   - v540は最高点2176を達成、v128は平均1170.8点という安定した成績がある
#   改善策（v128構造完全復帰）:
#   - マージボーナスをv128レベルに設定: DIRECT 1200/NEAR 600/FAR 200
#   - reactor情報（near_pairs、reactive_pairs）を完全削除
#   - チェーン予測を完全削除
#   - 先読みマージボーナスを完全削除
#   - フェーズ判定と高度ペナルティをv128設定に復帰: height_mult (LOW=1.0, MEDIUM=2.4, HIGH=1.8)
#   - HIGH_TOWERペナルティをv128の1.3倍に戻す（HIGHフェーズでの強化）
#   - ドリフトペナルティを20.0に設定（v128の標準値）
#   - 左右バランス補正を20.0（HIGHフェーズで40.0、MEDIUMフェーズで20.0）に設定
#   - nextNext中央寄せボーナスを50.0に維持
#   - type N-1の存在ボーナスをv128のtype*5.0に設定
#   - v540/v128の成功構造を完全再構築
#   核心的発見: v540とv128のシンプルな構造が実際には最も効果的であることが判明。v422の複雑なロジック（reactor情報、チェーン予測、先読みマージボーナス）は、判断を複雑にするだけでなく、実際のマージを見逃している。v128構造（シンプルでバランスよく、実際のマージを重視）に完全復帰することで、実際のマージ率を向上させ、安定して高いスコアを出す。
#   成功基準: scoreがv540の2176に近づく、または平均がv128の1170.8を上回る
#   失敗基準: scoreがv542以下、または平均がv542以下


def decide(game_state: dict, analysis: dict) -> dict:
    """v543: v128構造完全復帰版"""

    results = analysis.get("results", [])

    if not results:
        return {"x": 0.0, "reason": "no analysis data"}

    best_x = 0.0
    best_score = -float("inf")
    best_reason = ""

    # 盤面情報
    pieces = game_state.get("pieces", [])
    max_y = max([p["y"] for p in pieces]) if pieces else -4.0

    # フェーズ判定（v543: v128設定に完全復帰）
    if max_y < 0.8:
        phase = "LOW"
        height_mult = 1.0
        merge_mult = 1.2
    elif max_y < 1.8:
        phase = "MEDIUM"
        height_mult = 2.4
        merge_mult = 1.0
    elif max_y < 3.0:
        phase = "HIGH"
        height_mult = 1.8
        merge_mult = 1.0
    else:
        phase = "CRITICAL"
        height_mult = 1.0
        merge_mult = 0.6

    # nextNextピース情報
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

        # === v543: v128構造完全復帰 ===

        # 1. マージグレードによるスコア（v543: v128レベルに設定）
        if merge_grade == "DIRECT":
            score += 1200.0 * merge_mult
            reasons.append("DIRECT_MERGE")
        elif merge_grade == "NEAR":
            score += 600.0 * merge_mult
            reasons.append("NEAR_MERGE")
        elif merge_grade == "FAR":
            score += 200.0 * merge_mult
            reasons.append("FAR_MERGE")

        # 2. 高度によるペナルティ（v543: v128設定を復帰）
        height_penalty = landing_y * 50.0 * height_mult

        # HIGH_TOWERペナルティ（v543: v128の1.3倍を復帰）
        if phase == "HIGH" and landing_y > 0.5:
            height_penalty *= 1.3
            reasons.append("HIGH_TOWER")
        elif phase == "MEDIUM" and landing_y > 0.5:
            height_penalty *= 1.5
            reasons.append("MEDIUM_TOWER")
        elif landing_y > 0.0:
            reasons.append("HIGH_LAYER")

        score -= height_penalty

        # 3. ドリフトによるペナルティ（v543: v128の20.0に設定）
        drift_penalty = (abs(drift_x) + drift_unc) * 20.0
        score -= drift_penalty

        # 4. 左右バランス補正（v543: v128の設定に復帰）
        balance_strength = 20.0
        if phase == "HIGH":
            balance_strength = 40.0
        elif phase == "MEDIUM":
            balance_strength = 20.0

        left_count = sum(1 for p in pieces if p["x"] < 0)
        right_count = len(pieces) - left_count
        balance_bias = (right_count - left_count) / (len(pieces) if pieces else 1)

        balance_penalty = x * balance_bias * balance_strength
        score -= abs(balance_penalty)

        # 5. nextNextが同じタイプなら中央寄せボーナス（v543: v540/v128の50.0を維持）
        if next_next_type == next_type:
            center_bonus = max(0, 1.0 - abs(x) / 2.0) * 50.0
            score += center_bonus
            reasons.append("NEXT_SAME")

        # 6. type N-1の存在による追加ボーナス（v543: v128のtype*5.0に設定）
        if next_type > 1:
            prev_type_pieces = [p for p in pieces if p["type"] == next_type - 1]
            if len(prev_type_pieces) >= 1:
                score += next_type * 5.0
                reasons.append("TYPE_PREV")

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
