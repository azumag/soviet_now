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


def _calc_max_y(pieces: list) -> float:
    """盤面の最大高さを計算."""
    if not pieces:
        return -5.0
    return max(p.get("y", -5.0) for p in pieces)


def _find_safe_drop_x(pieces: list) -> float:
    """ピースが少ないエリアの中央Xを返す."""
    if not pieces:
        return 0.0

    left_pieces = [p for p in pieces if p["x"] < -0.5]
    right_pieces = [p for p in pieces if p["x"] > 0.5]
    center_pieces = [p for p in pieces if -0.5 <= p["x"] <= 0.5]

    # どちらかが少ない方へ
    if len(left_pieces) < len(right_pieces):
        return -2.0
    elif len(right_pieces) < len(left_pieces):
        return 2.0
    else:
        return 0.0


def _find_merge_candidate(results: list) -> dict:
    """マージ可能な候補を探す."""
    for r in results:
        if r.get("merge_grade") in ("DIRECT", "NEAR") and r.get("has_merge"):
            return r
    return None


def _find_same_type_drop(results: list, pieces: list, next_type: int) -> dict:
    """同typeのピース近くにドロップする候補を探す."""
    same_type_pieces = [p for p in pieces if p["type"] == next_type]
    if not same_type_pieces:
        return None

    target_y = max(p["y"] for p in same_type_pieces)
    best = None
    best_dist = float("inf")

    for r in results:
        # 着地位置が同typeピースのy座標以下
        if r.get("landing_y", -10) <= target_y:
            # 同typeピースとの距離を計算
            min_dist = min(((r["x"] - p["x"]) ** 2) ** 0.5 for p in same_type_pieces)
            if min_dist < best_dist:
                best_dist = min_dist
                best = r

    return best


def _score_candidate(r: dict, max_y: float, is_crisis: bool) -> float:
    """候補をスコアリングする."""
    base_score = r.get("score", 0)

    # マージボーナス
    if r.get("merge_grade") == "DIRECT":
        base_score += 1000
    elif r.get("merge_grade") == "NEAR":
        base_score += 500

    # 危機状態での高さペナルティ
    if is_crisis:
        base_score -= r.get("landing_y", -5) * 200

    # 着地位置が高いペナルティ
    if r.get("landing_y", -5) > max_y:
        base_score -= 100

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
    next_type = game_state.get("next", {}).get("type", 0)

    if not results:
        return {"x": 0.0, "reason": "データなし"}

    max_y = _calc_max_y(pieces)
    is_crisis = max_y > 2.0

    # 危機状態：マージ可能な手を優先
    if is_crisis:
        merge_candidate = _find_merge_candidate(results)
        if merge_candidate:
            return {
                "x": merge_candidate["x"],
                "reason": f"危機マージ優先 x={merge_candidate['x']:.2f} (grade={merge_candidate.get('merge_grade', 'NO')})",
            }

    # 同typeのピース近くにドロップ
    same_type_drop = _find_same_type_drop(results, pieces, next_type)
    if same_type_drop:
        return {
            "x": same_type_drop["x"],
            "reason": f"同type近接 x={same_type_drop['x']:.2f} (type={next_type})",
        }

    # 全候補をスコアリング
    scored = [(r, _score_candidate(r, max_y, is_crisis)) for r in results]
    best_result, best_score = max(scored, key=lambda x: x[1])

    return {
        "x": best_result["x"],
        "reason": f"総合スコア x={best_result['x']:.2f} (score={best_score:.1f})",
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
