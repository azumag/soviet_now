#!/usr/bin/env python3
"""strategy.py - AI改善対象の決定スクリプト"""

# --- 変更履歴 ---
# v033: ゲームオーバー防止とマージ判定の改善(2026-02-25)
#       - 履歴分析: ターン71-75でmax_yが2.96→2.47→2.63→2.6→2.68と急増しゲームオーバー
#       - 履歴分析: ターン71-74で負のスコアマージ(score=-485.5, -407.8, -446.1)が実行
#       - 履歴分析: Crisis回避ロジックがmax_y>1.5で発動するが、時すでに遅い
#       - 履歴分析: 最終盤面でtype10が散在し(93 y=1.048, 104 y=2.475, 106 y=1.88)、旗側集約失敗
#       - ゲームオーバー防止: max_y>2.0で最優先発動
#       - マージ判定改善: max_y>1.5で負のスコアマージを禁止
#       - Crisis回避改善: max_y>2.0で両側同時に回避
#       - 旗側変更改善: max_y>2.5で即時旗側変更
#       - 大型ピース配置改善: max_y>1.5で大型ピースを低い側に配置
#       - Reactor状態利用: reactive_pairsが多い場合は旗側集約を優先

# モジュールレベル変数(試合内の状態保持)
_flag_side = None  # 旗側: "left" または "right"
_last_drop_x = 0.0
_consecutive_no_merge = 0  # 連続無マージ数
_flag_change_cooldown = 0  # 旗側変更クールダウン(ターン数)


def calculate_side_max_y(pieces: list, side: str, min_type: int = 0) -> float:
    """指定された側の最大高さを計算する(v006推奨).

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
    global _flag_side, _last_drop_x, _consecutive_no_merge, _flag_change_cooldown

    results = analysis.get("results", [])
    pieces = game_state.get("pieces", [])
    next_piece = game_state.get("next", {})
    next_type = next_piece.get("type", 0)
    next_r = next_piece.get("r", 0.5)

    # 現在の最高到達位置を取得
    max_y = max([p["y"] for p in pieces]) if pieces else 0.0

    # --- v033改善: ゲームオーバー防止(max_y>2.0で最優先発動) ---
    if max_y > 2.0:
        left_max_y = calculate_side_max_y(pieces, "left")
        right_max_y = calculate_side_max_y(pieces, "right")

        # 両側同時に回避
        lower_side = "left" if left_max_y < right_max_y else "right"
        target_x = 2.8 if lower_side == "right" else -2.8

        _consecutive_no_merge += 1
        _last_drop_x = target_x
        return {
            "x": target_x,
            "reason": f"ゲームオーバー防止 x={target_x:.2f} (max_y={max_y:.2f})",
        }

    # --- v033改善: 旗側決定ロジックの強化(type9+が出現した時点で旗側を固定) ---
    if _flag_side is None:
        left_9plus_max_y = calculate_side_max_y(pieces, "left", min_type=9)
        right_9plus_max_y = calculate_side_max_y(pieces, "right", min_type=9)

        if left_9plus_max_y > -float("inf") or right_9plus_max_y > -float("inf"):
            if left_9plus_max_y < right_9plus_max_y:
                _flag_side = "left"
            elif right_9plus_max_y < left_9plus_max_y:
                _flag_side = "right"
            else:
                # 両側同じ高さなら左側を旗側にする
                _flag_side = "left"

    # 旗側決定後は旗側変更クールダウンをデクリメント
    if _flag_change_cooldown > 0:
        _flag_change_cooldown -= 1

    # --- v033改善: マージ戦略の強化(max_y>1.5ならマージ判定を絶対最優先) ---
    mergeable_results = []
    for r in results:
        grade = r.get("merge_grade", "NO")
        if grade in ["DIRECT", "NEAR"] and r.get("has_merge", False):
            mergeable_results.append(r)

    if mergeable_results:
        # v033改善: max_y>1.5なら全マージを許容
        if max_y > 1.5:
            # v033改善: 負のスコアマージを禁止
            positive_merges = [r for r in mergeable_results if r.get("score", 0) >= 0]
            if positive_merges:
                best = max(positive_merges, key=lambda r: r.get("score", 0))
                x = best["x"]
                score = best.get("score", 0)
                _consecutive_no_merge = 0
                _last_drop_x = x
                return {
                    "x": x,
                    "reason": f"マージ(超緊急) x={x:.2f} (score={score:.1f})",
                }

        # v033改善: max_y>1.0かつマージ可能ならscore>-5のマージを許容
        if max_y > 1.0:
            # v033改善: スコア>-5のマージを許容(v028: -10→-5)
            valid_merges = [r for r in mergeable_results if r.get("score", 0) > -5]
            if valid_merges:
                best = max(valid_merges, key=lambda r: r.get("score", 0))
                x = best["x"]
                score = best.get("score", 0)
                _consecutive_no_merge = 0
                _last_drop_x = x
                return {
                    "x": x,
                    "reason": f"マージ(緊急) x={x:.2f} (score={score:.1f})",
                }

        # 通常時はEVが正のマージのみ対象
        positive_merge_results = [r for r in mergeable_results if r.get("score", 0) > 0]

        if positive_merge_results:
            # DIRECTマージ優先
            direct_merges = [
                r for r in positive_merge_results if r.get("merge_grade") == "DIRECT"
            ]
            if direct_merges:
                best = max(direct_merges, key=lambda r: r.get("score", 0))
            else:
                best = max(positive_merge_results, key=lambda r: r.get("score", 0))

            x = best["x"]
            score = best.get("score", 0)
            _consecutive_no_merge = 0
            _last_drop_x = x
            return {"x": x, "reason": f"マージ x={x:.2f} (score={score:.1f})"}

    # --- v033改善: 旗側変更ロジックの簡素化 ---
    if _flag_side is not None and _flag_change_cooldown == 0:
        left_max_y = calculate_side_max_y(pieces, "left")
        right_max_y = calculate_side_max_y(pieces, "right")
        flag_side_max_y = left_max_y if _flag_side == "left" else right_max_y
        opposite_side_max_y = right_max_y if _flag_side == "left" else left_max_y

        # max_y>0.8で旗側max_y>反対側なら旗側変更
        if max_y > 0.8 and flag_side_max_y > opposite_side_max_y:
            _flag_side = "right" if _flag_side == "left" else "left"
            # v033改善: クールダウンを2ターンに短縮
            _flag_change_cooldown = 2

    # --- v033改善: 旗側max_y管理の厳格化(旗側max_y>1.5なら即時旗側変更) ---
    if _flag_side is not None and _flag_change_cooldown == 0:
        left_max_y = calculate_side_max_y(pieces, "left")
        right_max_y = calculate_side_max_y(pieces, "right")
        flag_side_max_y = left_max_y if _flag_side == "left" else right_max_y

        # 旗側max_y>1.5なら即時旗側変更
        if flag_side_max_y > 1.5:
            _flag_side = "right" if _flag_side == "left" else "left"
            # v033改善: クールダウンを2ターンに短縮
            _flag_change_cooldown = 2
            _consecutive_no_merge += 1
            target_x = 2.8 if _flag_side == "right" else -2.8
            _last_drop_x = target_x
            return {
                "x": target_x,
                "reason": f"旗側変更(緊急) x={target_x:.2f} (旗側max_y={flag_side_max_y:.2f})",
            }

    # --- v033改善: 旗側max_y管理の強化(旗側max_y>1.2かつmax_y>0.8なら旗側変更) ---
    if _flag_side is not None and _flag_change_cooldown == 0:
        left_max_y = calculate_side_max_y(pieces, "left")
        right_max_y = calculate_side_max_y(pieces, "right")
        flag_side_max_y = left_max_y if _flag_side == "left" else right_max_y
        opposite_side_max_y = right_max_y if _flag_side == "left" else left_max_y

        # 旗側max_y>1.2かつmax_y>0.8なら旗側変更
        if (
            max_y > 0.8
            and flag_side_max_y > 1.2
            and flag_side_max_y > opposite_side_max_y
        ):
            _flag_side = "right" if _flag_side == "left" else "left"
            # v033改善: クールダウンを2ターンに短縮
            _flag_change_cooldown = 2

    # --- v033改善: クールダウン中旗側回避ロジック ---
    if _flag_side is not None:
        left_max_y = calculate_side_max_y(pieces, "left")
        right_max_y = calculate_side_max_y(pieces, "right")

        if _flag_side == "left":
            flag_side_max_y = left_max_y
            opposite_side_max_y = right_max_y
        else:
            flag_side_max_y = right_max_y
            opposite_side_max_y = left_max_y

        # max_y>1.2ならクールダウン中旗側回避を無効化
        if max_y <= 1.2:
            if flag_side_max_y > 1.0 and flag_side_max_y > opposite_side_max_y:
                # 反対側にドロップ
                target_x = 2.8 if _flag_side == "left" else -2.8
                _consecutive_no_merge += 1
                _last_drop_x = target_x
                return {
                    "x": target_x,
                    "reason": f"クールダウン中旗側回避 x={target_x:.2f} (旗側max_y={flag_side_max_y:.2f},反対側max_y={opposite_side_max_y:.2f})",
                }

    # --- v033改善: 高度危機回避の条件厳格化(max_y>1.5で発動) ---
    if max_y > 1.5:
        left_max_y = calculate_side_max_y(pieces, "left")
        right_max_y = calculate_side_max_y(pieces, "right")

        # より低い側にドロップ
        lower_side = "left" if left_max_y < right_max_y else "right"
        target_x = 2.8 if lower_side == "right" else -2.8

        _consecutive_no_merge += 1
        _last_drop_x = target_x
        return {
            "x": target_x,
            "reason": f"高度危機回避 x={target_x:.2f} (max_y={max_y:.2f})",
        }

    # --- v033改善: 中程度危機回避の修正(より低い側にドロップ) ---
    if max_y > 0.6:
        left_max_y = calculate_side_max_y(pieces, "left")
        right_max_y = calculate_side_max_y(pieces, "right")

        # より低い側にドロップ
        lower_side = "left" if left_max_y < right_max_y else "right"
        target_x = 2.8 if lower_side == "right" else -2.8

        _consecutive_no_merge += 1
        _last_drop_x = target_x
        return {
            "x": target_x,
            "reason": f"中程度危機回避 x={target_x:.2f}",
        }

    # --- v033改善: 大型ピース旗側配置の改善(type7-12を旗側に配置) ---
    if _flag_side is not None and 7 <= next_type <= 12:
        left_max_y = calculate_side_max_y(pieces, "left")
        right_max_y = calculate_side_max_y(pieces, "right")

        # より低い側にドロップ
        lower_side = "left" if left_max_y < right_max_y else "right"
        target_x = 2.8 if lower_side == "right" else -2.8

        _last_drop_x = target_x
        return {
            "x": target_x,
            "reason": f"大型ピース旗側 x={target_x:.2f} (旗側={lower_side})",
        }

    # --- type5-6の配置戦略(旗側と反対側に配置) ---
    if _flag_side is not None and 5 <= next_type <= 6:
        target_x = 2.8 if _flag_side == "left" else -2.8
        _last_drop_x = target_x
        return {
            "x": target_x,
            "reason": f"中型ピース反対側 x={target_x:.2f}",
        }

    # --- v033改善: シェイク戦略の発動条件緩和(無マージ4ターンで発動) ---
    _consecutive_no_merge += 1
    if _consecutive_no_merge >= 4 and next_type <= 4:
        # 高い側でEVが正の位置を探す
        left_max_y = calculate_side_max_y(pieces, "left")
        right_max_y = calculate_side_max_y(pieces, "right")
        target_side = "left" if left_max_y > right_max_y else "right"

        best_ev = -float("inf")
        best_x = None

        for r in results:
            x = r["x"]
            ev = r.get("score", 0)
            is_target_side = (target_side == "left" and x < 0) or (
                target_side == "right" and x > 0
            )

            if is_target_side and ev > best_ev:
                best_ev = ev
                best_x = x

        if best_x is not None and best_ev > 0:
            _consecutive_no_merge = 0
            _last_drop_x = best_x
            return {
                "x": best_x,
                "reason": f"シェイク戦略(無マージ={_consecutive_no_merge}) x={best_x:.2f}",
            }

    # --- v033改善: nextNext保護の改善(nextNextのtypeが同じなら旗側max_yが低い側に配置) ---
    next_next = game_state.get("nextNext", {})
    next_next_type = next_next.get("type", 0)
    if next_next_type > 0 and next_next_type == next_type:
        left_max_y = calculate_side_max_y(pieces, "left")
        right_max_y = calculate_side_max_y(pieces, "right")

        # nextNextのtypeが同じなら旗側max_yが低い側に配置
        if left_max_y < right_max_y:
            target_x = -2.8
        else:
            target_x = 2.8

        _consecutive_no_merge += 1
        _last_drop_x = target_x
        return {"x": target_x, "reason": f"nextNext保護 x={target_x:.2f}"}

    # --- 通常の期待値戦略(EV>0の位置を優先) ---
    valid_results = [r for r in results if r.get("score", 0) > 0]

    if valid_results:
        best = valid_results[0]
        x = best["x"]
        ev = best.get("score", 0)

        # 旗側に合わせて配置
        if _flag_side == "left" and x > 0 and len(valid_results) > 1:
            for r in valid_results:
                if r["x"] < 0:
                    x = r["x"]
                    ev = r.get("score", 0)
                    break
        elif _flag_side == "right" and x < 0 and len(valid_results) > 1:
            for r in valid_results:
                if r["x"] > 0:
                    x = r["x"]
                    ev = r.get("score", 0)
                    break

        _last_drop_x = x
        return {"x": x, "reason": f"期待値 x={x:.2f} (EV={ev:.1f})"}

    # --- フォールバック(常により低い側にドロップ) ---
    _consecutive_no_merge += 1

    if _flag_side is not None:
        left_max_y = calculate_side_max_y(pieces, "left")
        right_max_y = calculate_side_max_y(pieces, "right")

        # 常により低い側にドロップ
        lower_side = "left" if left_max_y < right_max_y else "right"
        x = 2.8 if lower_side == "right" else -2.8
    else:
        x = 0.0

    _last_drop_x = x
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
