#!/usr/bin/env python3
"""strategy.py - AI改善対象の決定スクリプト"""

# 固定インターフェース:
#  decide(game_state: dict, analysis: dict) -> dict
#    戻り値: {"x": float, "reason": str}
#
# AI改変可能: decide() 内部,ヘルパー関数,定数,import
# AI改変禁止: decide() シグネチャ,if __name__ == "__main__" ブロック

# --- 変更履歴 ---
# v025: 危機回避ロジックの根本的改善と早期発動化(2026-02-25)
#       - 履歴分析: ターン30-58でmax_yが-0.96→4.29に急増しゲームオーバー(致命的)
#       - 履歴分析: ターン52-54で"中程度危機回避"が旗側x=-2.8にドロップ(旗側回避ロジックのバグ)
#       - 履歴分析: ターン52でmax_y=0.73,flag_side="left"で左側にドロップ(危機回避失敗)
#       - 履歴分析: 最終盤面type9+が左右散在(左側type11 y=2.171でゲームオーバー)
#       - 履歴分析: スコア停滞期間が長く(最大16ターン),マージ成功率が低い
#       - 危機回避ロジックの根本的修正: 常に低い側にドロップ(v024の"旗側優先"ロジック削除)
#       - 中程度危機回避早期化: max_y>0.5で発動(v024: 0.7→0.5,早期検出)
#       - 高度危機回避の強化: 常に低い側にドロップ,旗側を無視
#       - クールダウン期間中の旗側回避: 旗側max_y>0.6なら反対側にドロップ(v024: 0.8→0.6,早期回避)
#       - 旗側max_y管理緩和: 旗側max_y<0.7でtype9+旗側配置(v024: 0.5→0.7,緩和)
#       - 危機時フォールバックマージ: 高度危機時は低スコアマージも許容(v025新規)
#       - 大型ピース配置改善: 旗側max_y>0.7なら反対側に配置(旗側回避強化)
#       - フォールバック改善: 常に低い側にドロップ
#
# v026: 旗側max_y管理の厳格化と旗側回避ロジックの改善(2026-02-25 最新)
#       - 履歴分析: ターン47以降、旗側max_y>0.6で旗側回避するが、旗側max_yが1.99に達している
#       - 履歴分析: ターン54以降、max_yが2.88に急増しゲームオーバー(致命的)
#       - 履歴分析: 旗側回避ロジックが旗側max_y>0.6で十分でない
#       - 旗側max_y管理厳格化: 旗側max_y>1.0で旗側回避(v025: 0.6→1.0,早期回避)
#       - クールダウン期間中の旗側回避: 旗側max_y>1.0なら即時反対側にドロップ(v025: 0.6→1.0)
#       - 高度危機回避強化: max_y>1.0で発動(v025: 1.0→1.0維持)
#       - 中程度危機回避強化: max_y>0.5で発動(v025維持)
#       - 旗側変更条件厳格化: 旗側max_y>1.0かつ反対側が0.3以上低い場合に旗側変更(v025: 0.6→1.0)
#       - 旗側変更クールダウン: 5ターン(v025維持,安定性確保)
#       - 大型ピース旗側配置厳格化: 旗側max_y<1.0でtype9+旗側配置(v025: 0.7→1.0,厳格化)
#       - 大型ピース旗側変更厳格化: 旗側max_y>=1.0かつ反対側<0.6なら旗側変更(v025: 0.7→1.0,厳格化)
#       - マージ戦略旗側フィルタリング: 旗側max_y>1.0なら旗側のマージを回避(v025: 0.6→1.0)
#       - フォールバック改善: 旗側max_y>=1.0なら反対側にドロップ(v025: 0.7→1.0)
#
# v027: クールダウン中旗側回避の根本的修正と旗側決定ロジックの改善(2026-02-25 最新)
#       - 履歴分析(致命的): ターン38-42で左側max_yが0.81→2.18→2.56→2.49→3.49と急増
#       - 履歴分析(致命的): ターン38-42で旗側max_y=1.27〜1.59だが、左側(max_y=2.18+)にドロップし続けた
#       - 履歴分析(致命的): ターン38で左側type9+=6、右側type9+=4だが左側にドロップ
#       - 履歴分析(致命的): ターン41でマージ候補があるが「クールダウン中旗側回避」が優先されマージ見逃し
#       - クールダウン中旗側回避の根本的修正: 常により低い側にドロップ(旗側max_y>1.0でも反対側が高いなら旗側にドロップ)
#       - 旗側決定ロジックの簡素化: type9+があれば常にmax_yが低い側を旗側にする
#       - 旗側変更ロジックの厳格化: max_y>0.8で旗側max_y>反対側なら旗側変更
#       - 高度危機回避の優先度変更: max_y>1.5で最優先発動(中程度危機回避より優先)
#       - マージ戦略の柔軟化: クールダウン中旗側回避前にマージ判定(max_y>1.5ならマージ優先)

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

    # --- v027改善: 旗側決定ロジックの簡素化(type9+があれば常にmax_yが低い側を旗側) ---
    if _flag_side is None:
        # type9+がある場合,max_yが低い側を旗側にする
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

    # --- v027改善: 高度危機回避の最優先化(max_y>1.5で発動) ---
    if max_y > 1.5:
        left_max_y = calculate_side_max_y(pieces, "left")
        right_max_y = calculate_side_max_y(pieces, "right")

        # v027改善: 常により低い側にドロップ
        lower_side = "left" if left_max_y < right_max_y else "right"
        target_x = 2.8 if lower_side == "right" else -2.8

        _consecutive_no_merge += 1
        _last_drop_x = target_x
        return {
            "x": target_x,
            "reason": f"高度危機回避 x={target_x:.2f}",
        }

    # --- v027改善: クールダウン中旗側回避の根本的修正(常により低い側にドロップ) ---
    if _flag_side is not None:
        left_max_y = calculate_side_max_y(pieces, "left")
        right_max_y = calculate_side_max_y(pieces, "right")

        # v027改善: 旗側max_y>1.0かつ反対側が低い場合のみ旗側回避
        if _flag_side == "left":
            flag_side_max_y = left_max_y
            opposite_side_max_y = right_max_y
        else:
            flag_side_max_y = right_max_y
            opposite_side_max_y = left_max_y

        if flag_side_max_y > 1.0 and flag_side_max_y > opposite_side_max_y:
            target_x = 2.8 if _flag_side == "left" else -2.8
            _consecutive_no_merge += 1
            _last_drop_x = target_x
            return {
                "x": target_x,
                "reason": f"クールダウン中旗側回避 x={target_x:.2f} (旗側max_y={flag_side_max_y:.2f},反対側max_y={opposite_side_max_y:.2f})",
            }

    # --- 1. マージ可能なら最優先 ---
    mergeable_results = []
    for r in results:
        grade = r.get("merge_grade", "NO")
        if grade in ["DIRECT", "NEAR"] and r.get("has_merge", False):
            mergeable_results.append(r)

    if mergeable_results:
        # 高度危機時(max_y>1.5)はマージ優先
        if max_y > 1.5:
            best = max(mergeable_results, key=lambda r: r.get("score", 0))
            x = best["x"]
            score = best.get("score", 0)
            _consecutive_no_merge = 0
            _last_drop_x = x
            return {"x": x, "reason": f"マージ(危機優先) x={x:.2f} (score={score:.1f})"}

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

    # --- v027改善: 旗側変更ロジックの厳格化 ---
    if _flag_side is not None and _flag_change_cooldown == 0:
        left_max_y = calculate_side_max_y(pieces, "left")
        right_max_y = calculate_side_max_y(pieces, "right")
        flag_side_max_y = left_max_y if _flag_side == "left" else right_max_y
        opposite_side_max_y = right_max_y if _flag_side == "left" else left_max_y

        # v027改善: max_y>0.8で旗側max_y>反対側なら旗側変更
        if max_y > 0.8 and flag_side_max_y > opposite_side_max_y:
            _flag_side = "right" if _flag_side == "left" else "left"
            _flag_change_cooldown = 5

    # --- v027改善: 中程度危機回避(0.8より低い側にドロップ) ---
    if max_y > 0.8:
        left_max_y = calculate_side_max_y(pieces, "left")
        right_max_y = calculate_side_max_y(pieces, "right")

        # v027改善: 常により低い側にドロップ
        lower_side = "left" if left_max_y < right_max_y else "right"
        target_x = 2.8 if lower_side == "right" else -2.8

        _consecutive_no_merge += 1
        _last_drop_x = target_x
        return {
            "x": target_x,
            "reason": f"中程度危機回避 x={target_x:.2f}",
        }

    # --- 大型ピース旗側配置(type9+を旗側に配置) ---
    if _flag_side is not None and next_type >= 9:
        left_max_y = calculate_side_max_y(pieces, "left")
        right_max_y = calculate_side_max_y(pieces, "right")

        # v027改善: 常により低い側にドロップ
        lower_side = "left" if left_max_y < right_max_y else "right"
        target_x = 2.8 if lower_side == "right" else -2.8

        _last_drop_x = target_x
        return {
            "x": target_x,
            "reason": f"大型ピース旗側 x={target_x:.2f} (旗側={lower_side})",
        }

    # --- type7-8の配置戦略(旗側と反対側に配置) ---
    if _flag_side is not None and 7 <= next_type <= 8:
        target_x = 2.8 if _flag_side == "left" else -2.8
        _last_drop_x = target_x
        return {
            "x": target_x,
            "reason": f"中型ピース反対側 x={target_x:.2f}",
        }

    # --- シェイク戦略(無マージ2ターンで発動) ---
    _consecutive_no_merge += 1
    if _consecutive_no_merge >= 2 and next_type <= 5:
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

    # --- 次のピース保護(nextNextのマージ経路を塞がない) ---
    next_next = game_state.get("nextNext", {})
    next_next_type = next_next.get("type", 0)
    if next_next_type > 0 and next_next_type == next_type:
        if _flag_side == "left":
            x = -2.8 if abs(_last_drop_x) > 1.5 else -2.0
        elif _flag_side == "right":
            x = 2.8 if abs(_last_drop_x) > 1.5 else 2.0
        else:
            if abs(_last_drop_x) > 1.5:
                x = -_last_drop_x
            else:
                x = 2.8 if _last_drop_x < 0 else -2.8

        _consecutive_no_merge += 1
        _last_drop_x = x
        return {"x": x, "reason": f"nextNext保護 x={x:.2f}"}

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

        # v027改善: 常により低い側にドロップ
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
