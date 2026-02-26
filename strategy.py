#!/usr/bin/env python3
"""strategy.py - AI改善対象の決定スクリプト"""

# 固定インターフェース:
# decide(game_state: dict, analysis: dict) -> dict
#    戻り値: {"x": float, "reason": str}
#
# AI改変可能: decide() 内部,ヘルパー関数,定数,import
# AI改変禁止: decide() シグネチャ,if __name__ == "__main__" ブロック

# --- 変更履歴 ---
# [BEST:1212] v001: 初期版 - analysis["results"]のスコア最大を選ぶだけのベースライン
# [BEST:584] v002: 危機対応・マージ優先戦略追加
# [BEST:453] v003: スコアリング関数の全面的改善。連続的なマージ評価、左右バランス考慮、強力な高さペナルティ
# v004: 旗側管理導入・危機閾値改善・マージ重視化。大型ピースの旗側集約、危機閾値1.8→0.8、マージ優先度大幅強化
# v005: 戦略簡素化。旗側ボーナス削除・近接ボーナス削除・高さペナルティ強化・危機閾値0.5化。analysisのbase_scoreを尊重し、高さ抑制を最優先


def _calc_max_y(pieces: list) -> float:
    """盤面の最大高さを計算."""
    if not pieces:
        return -5.0
    return max(p.get("y", -5.0) for p in pieces)


def decide(game_state: dict, analysis: dict) -> dict:
    """盤面状態と解析結果から最適ドロップX座標を決定する.

    Args:
        game_state: game_state.json の内容
        analysis: {"results": [...], "same_type": [...], "reactor": {...}}

    Returns:
        {"x": float, "reason": str}
    """
    results = analysis.get("results", [])
    pieces = game_state.get("pieces", [])

    if not results:
        return {"x": 0.0, "reason": "データなし"}

    max_y = _calc_max_y(pieces)
    is_crisis = max_y > 0.5

    # 全候補をスコアリング（高さ抑制とマージのみを評価）
    scored = []
    for r in results:
        base_score = r.get("score", 0)

        # マージボーナス（analyze_boardのbase_scoreを尊重）
        merge_grade = r.get("merge_grade", "NO")
        if merge_grade == "DIRECT":
            base_score += 5000
        elif merge_grade == "NEAR":
            base_score += 3000
        elif merge_grade == "CLOSE":
            base_score += 1000
        elif merge_grade == "NO" and r.get("has_merge", False):
            base_score += 500

        # 高さペナルティ（着地位置が高いと致命的）
        landing_y = r.get("landing_y", -5)
        base_score -= landing_y * 5000

        # 危機時：着地位置がmax_y以上なら致命的ペナルティ
        if is_crisis and landing_y > max_y:
            base_score -= 10000

        scored.append((r, base_score))

    best_result, best_score = max(scored, key=lambda x: x[1])

    merge_grade = best_result.get("merge_grade", "NO")

    return {
        "x": best_result["x"],
        "reason": f"簡易スコア x={best_result['x']:.2f} (grade={merge_grade}, y={best_result.get('landing_y', -5):.2f}, score={best_score:.0f})",
    }


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
