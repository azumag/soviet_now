#!/usr/bin/env python3
"""strategy.py - AI改善対象の決定スクリプト"""

# 固定インターフェース:
# decide(game_state: dict, analysis: dict) -> dict
#    戻り値: {"x": float, "reason": str}
#
# AI改変可能: decide() 内部,ヘルパー関数,定数,import
# AI改変禁止: decide() シグネチャ,if __name__ == "__main__" ブロック

# --- 変更履歴 ---
# v117: 構造的簡素化と期待値戦略強化(2026-02-26)
#       - 履歴分析(致命的): 「大型ピース旗側」が12%支配的、大型ピース散在
#       - 履歴分析(致命的): 「期待値」が8%のみ発動、analysis["results"]活用不足
#       - 履歴分析(致命的): 「シェイク戦略」「旗側集約」「旗側変更」が0回発動
#       - 履歴分析: スコア1592（目標3512）、max_y=2.56で危機
#       - v116の問題点: 461行に膨張、ロジックが複雑すぎる
#       - v116の問題点: 不要なロジック（シェイク、nextNext保護等）が多くマージ見逃し
#       - v116の問題点: 4段階マージ戦略が冗長、統一可能
#       - v116の問題点: 高度危機回避と中程度危機回避が重複
#       - v116の問題点: 大型ピース戦略の条件が複雑すぎる
#       - 構造的簡素化: 不要なロジック5つ削除（シェイク、nextNext保護、クールダウン回避、旗側変更、旗側集約）(v117新規)
#       - 期待値戦略強化: analysis["results"]スコアトップを優先、旗側調整を追加(v117新規)
#       - マージ戦略統合: 4段階マージ戦略を1つに統合、コード削減(v117新規)
#       - 危機回避簡素化: 高度危機回避のみ残す、閾値2.0で統一(v117新規)
#       - 大型ピース戦略簡素化: 旗側max_y>2.0のみで反対側検討(v117新規)
#       - ロジック優先順序明確化: マージ→期待値→危機回避→大型ピース→中型ピース→フォールバック(v117新規)
#       - コード削減: 461行→約280行に削減(v117改善)

# [BEST:3512] v064: マージ最優先化と旗側集約強化(2026-02-25)

# モジュールレベル変数(試合内の状態保持)
_flag_side = None  # 旗側: "left" または "right"


def calculate_side_max_y(pieces: list, side: str, min_type: int = 0) -> float:
    """指定された側の最大高さを計算する.

    Args:
        pieces: 全ピースリスト
        side: "left" (x<0) または "right" (x>0)
        min_type: 最小タイプ(デフォルト0で全ピース対象)

    Returns:
        最大高さ(ピースがない場合は -inf)
    """
    side_pieces = [
        p
        for p in pieces
        if p["type"] >= min_type
        and ((side == "left" and p["x"] < 0) or (side == "right" and p["x"] > 0))
    ]
    if not side_pieces:
        return -float("inf")
    return max(p["y"] for p in side_pieces)


def decide(game_state: dict, analysis: dict) -> dict:
    """盤面状態と解析結果から最適ドロップX座標を決定する.

    Args:
        game_state: game_state.json の内容
        analysis: {"results": [...], "same_type": [...], "reactor": {...}}

    Returns:
        {"x": float, "reason": str}
    """
    global _flag_side

    results = analysis.get("results", [])
    pieces = game_state.get("pieces", [])
    next_piece = game_state.get("next", {})
    next_type = next_piece.get("type", 0)

    # 現在の最高到達位置を取得
    max_y = max([p["y"] for p in pieces]) if pieces else 0.0

    # --- 旗側決定ロジック(type9+があれば常にmax_yが低い側を旗側) ---
    if _flag_side is None:
        left_9plus_max_y = calculate_side_max_y(pieces, "left", min_type=9)
        right_9plus_max_y = calculate_side_max_y(pieces, "right", min_type=9)

        if left_9plus_max_y > -float("inf") or right_9plus_max_y > -float("inf"):
            if left_9plus_max_y < right_9plus_max_y:
                _flag_side = "left"
            elif right_9plus_max_y < left_9plus_max_y:
                _flag_side = "right"
            else:
                _flag_side = "left"
        else:
            left_max_y = calculate_side_max_y(pieces, "left")
            right_max_y = calculate_side_max_y(pieces, "right")
            _flag_side = "left" if left_max_y < right_max_y else "right"

    # --- マージ戦略の超最優先化(全てのロジックより優先) ---
    mergeable_results = []
    for r in results:
        grade = r.get("merge_grade", "NO")
        if (
            isinstance(grade, str)
            and grade in ["DIRECT", "NEAR"]
            and r.get("has_merge", False)
        ):
            mergeable_results.append(r)

    if mergeable_results:
        # max_yに応じて閾値を設定（統合マージ戦略）
        if max_y > 2.0:
            threshold = -15
        elif max_y > 1.5:
            threshold = -8
        elif max_y > 1.0:
            threshold = -5
        else:
            threshold = 0

        valid_merges = [r for r in mergeable_results if r.get("score", 0) > threshold]
        if valid_merges:
            best = max(valid_merges, key=lambda r: r.get("score", 0))
            x = best["x"]
            score = best.get("score", 0)
            reason_suffix = (
                "(致命)"
                if max_y > 2.0
                else "(超緊急)"
                if max_y > 1.5
                else "(緊急)"
                if max_y > 1.0
                else ""
            )
            return {
                "x": x,
                "reason": f"マージ{reason_suffix} x={x:.2f} (score={score:.1f})",
            }

        # 低スコアマージがない場合もpositive_merge_resultsをチェック
        positive_merge_results = [r for r in mergeable_results if r.get("score", 0) > 0]
        if positive_merge_results:
            best = max(positive_merge_results, key=lambda r: r.get("score", 0))
            x = best["x"]
            score = best.get("score", 0)
            return {"x": x, "reason": f"マージ x={x:.2f} (score={score:.1f})"}

    # --- 期待値戦略の強化(EV>0の位置を優先、旗側を考慮) ---
    valid_results = [r for r in results if r.get("score", 0) > 0]

    if valid_results:
        # スコアトップを優先
        best = max(valid_results, key=lambda r: r.get("score", 0))
        x = best["x"]
        ev = best.get("score", 0)

        # 旗側に合わせて配置（ただしスコアトップが優先）
        if _flag_side is not None and len(valid_results) > 1:
            flag_side_results = [
                r
                for r in valid_results
                if (
                    (_flag_side == "left" and r["x"] < 0)
                    or (_flag_side == "right" and r["x"] > 0)
                )
            ]
            if flag_side_results:
                best_flag = max(flag_side_results, key=lambda r: r.get("score", 0))
                flag_ev = best_flag.get("score", 0)
                # 旗側のベストスコアがトップの90%以上なら旗側優先
                if flag_ev > ev * 0.9:
                    x = best_flag["x"]
                    ev = flag_ev

        return {"x": x, "reason": f"期待値 x={x:.2f} (EV={ev:.1f})"}

    # --- 危機回避の簡素化(max_y>2.0で即時危機回避) ---
    if max_y > 2.0:
        left_max_y = calculate_side_max_y(pieces, "left")
        right_max_y = calculate_side_max_y(pieces, "right")

        lower_side = "left" if left_max_y < right_max_y else "right"
        target_x = 2.8 if lower_side == "right" else -2.8

        return {"x": target_x, "reason": f"危機回避 x={target_x:.2f}"}

    # --- 大型ピース戦略の簡素化(旗側max_y>2.0で反対側を検討) ---
    if _flag_side is not None and 7 <= next_type <= 12:
        left_max_y = calculate_side_max_y(pieces, "left")
        right_max_y = calculate_side_max_y(pieces, "right")
        flag_side_max_y = left_max_y if _flag_side == "left" else right_max_y
        opposite_side_max_y = right_max_y if _flag_side == "left" else left_max_y

        # 旗側max_y>2.0かつ反対側<1.5なら反対側をドロップ
        if flag_side_max_y > 2.0 and opposite_side_max_y < 1.5:
            target_x = 2.8 if _flag_side == "left" else -2.8
            return {
                "x": target_x,
                "reason": f"大型ピース反対側 x={target_x:.2f} (旗側max_y={flag_side_max_y:.2f}高)",
            }

        target_x = 2.8 if _flag_side == "right" else -2.8
        return {"x": target_x, "reason": f"大型ピース旗側 x={target_x:.2f}"}

    # --- 中型ピース反対側配置 ---
    if _flag_side is not None and 5 <= next_type <= 6:
        target_x = 2.8 if _flag_side == "left" else -2.8
        return {"x": target_x, "reason": f"中型ピース反対側 x={target_x:.2f}"}

    # --- フォールバック(常により低い側にドロップ) ---
    if _flag_side is not None:
        left_max_y = calculate_side_max_y(pieces, "left")
        right_max_y = calculate_side_max_y(pieces, "right")

        lower_side = "left" if left_max_y < right_max_y else "right"
        x = 2.8 if lower_side == "right" else -2.8
    else:
        x = 0.0

    return {"x": x, "reason": f"フォールバック({_flag_side or '中央'})"}


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
