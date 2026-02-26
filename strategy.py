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
# v003: スコアリング関数の全面的改善。連続的なマージ評価、左右バランス考慮、強力な高さペナルティ
# v004: 旗側管理導入・危機閾値改善・マージ重視化。大型ピースの旗側集約、危機閾値1.8→0.8、マージ優先度大幅強化


def _calc_max_y(pieces: list) -> float:
    """盤面の最大高さを計算."""
    if not pieces:
        return -5.0
    return max(p.get("y", -5.0) for p in pieces)


def _determine_flag_side(pieces: list) -> int:
    """旗側を決定する。最も大きいピース(type >= 8)が多い側を旗側とする."""
    large_pieces = [p for p in pieces if p.get("type", 0) >= 8]
    if not large_pieces:
        return 1  # デフォルトは右側

    left_count = sum(1 for p in large_pieces if p["x"] < 0)
    right_count = len(large_pieces) - left_count

    if right_count > left_count:
        return 1  # 右側が旗
    elif left_count > right_count:
        return -1  # 左側が旗
    else:
        return 1  # 同数ならデフォルト右側


def _find_merge_candidate(results: list) -> dict:
    """マージ可能な候補を探す."""
    for r in results:
        if r.get("merge_grade") in ("DIRECT", "NEAR") and r.get("has_merge"):
            return r
    return None


def _score_candidate(
    r: dict, flag_side: int, max_y: float, is_crisis: bool, pieces: list
) -> float:
    """候補をスコアリングする。

    評価項目（優先順位順）:
    1. マージ可能性（最重要）
    2. 着地位置（低いほど良い）
    3. 旗側配置（大型ピース集約のため）
    4. 大型ピースへの近接（集約強化）
    """
    base_score = r.get("score", 0)

    # マージボーナス（最重要）
    merge_grade = r.get("merge_grade", "NO")
    if merge_grade == "DIRECT":
        base_score += 5000  # 直接接触マージは超優先
    elif merge_grade == "NEAR":
        base_score += 3000  # 近接マージも優先
    elif merge_grade == "CLOSE":
        base_score += 1000  # 閉鎖距離マージ
    elif merge_grade == "NO" and r.get("has_merge", False):
        base_score += 500  # マージ可能だが遠い

    # 旗側ボーナス（大型ピース集約）
    x = r.get("x", 0)
    if (flag_side == 1 and x > 0) or (flag_side == -1 and x < 0):
        base_score += 800
    elif x == 0:
        base_score += 400  # 中央は中間スコア

    # 大型ピース(type 8+)への近接ボーナス
    landing_y = r.get("landing_y", -5)
    for p in pieces:
        if p.get("type", 0) >= 8 and p["y"] > -3:
            dist = ((x - p["x"]) ** 2 + (landing_y - p["y"]) ** 2) ** 0.5
            if dist < 2.0:
                base_score += (2.0 - dist) * 200

    # 高さペナルティ（危機時は強化）
    if is_crisis:
        # 危機時：着地位置が高いと厳しいペナルティ
        base_score -= landing_y * 2000
        if landing_y > max_y:
            base_score -= 5000  # max_y以上は致命的
    else:
        # 通常時：高さペナルティは緩やか
        base_score -= landing_y * 100

    return base_score


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
    flag_side = _determine_flag_side(pieces)

    # 危機閾値を大幅に下げて早期対策（1.8 → 0.8）
    is_crisis = max_y > 0.8

    # 危機状態：直接マージ可能な手を最優先
    if is_crisis:
        merge_candidate = _find_merge_candidate(results)
        if merge_candidate:
            return {
                "x": merge_candidate["x"],
                "reason": f"危機マージ優先 x={merge_candidate['x']:.2f} (grade={merge_candidate.get('merge_grade', 'NO')})",
            }

    # 全候補をスコアリング（旗側・マージ・高さを総合評価）
    scored = [
        (r, _score_candidate(r, flag_side, max_y, is_crisis, pieces)) for r in results
    ]
    best_result, best_score = max(scored, key=lambda x: x[1])

    # 評価要因をreasonに記載
    merge_grade = best_result.get("merge_grade", "NO")
    is_flag_side = (flag_side == 1 and best_result["x"] > 0) or (
        flag_side == -1 and best_result["x"] < 0
    )
    side_str = "旗側" if is_flag_side else "対側"

    return {
        "x": best_result["x"],
        "reason": f"総合スコア x={best_result['x']:.2f} (grade={merge_grade}, {side_str}, score={best_score:.0f})",
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
