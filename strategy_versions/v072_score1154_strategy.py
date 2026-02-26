#!/usr/bin/env python3
"""strategy.py - AI改善対象の決定スクリプト"""

# 固定インターフェース:
#  decide(game_state: dict, analysis: dict) -> dict
#    戻り値: {"x": float, "reason": str}
#
# AI改変可能: decide() 内部,ヘルパー関数,定数,import
# AI改変禁止: decide() シグネチャ,if __name__ == "__main__" ブロック

# --- 変更履歴 ---
# v071: 旗側変更ロジックの根本的再構築と中程度危機回避の発動抑制(2026-02-26)
#       - 履歴分析(致命的): Turn 59-65で「中程度危機回避」が7ターン連続発動しmax_yが1.72→3.07に急増
#       - 履歴分析(致命的): Turn 66でmax_y=3.07で「高度危機回避」発動（発動遅すぎ）
#       - 履歴分析(致命的): Turn 62-67で6ターン連続でマージ見逃し
#       - 履歴分析(致命的): 最終盤面でtype9+が左右散在(type9:左1.78/右1.54, type10:左2.6/右1.12, type11:左1.64/右1.54)
#       - v070の問題点: 旗側変更ロジックが反対側max_yを考慮不足（旗側max_y>反対側max_y+0.5かつ反対側max_y<1.0の条件が緩すぎ）
#       - v070の問題点: 旗側max_y管理の発動条件が緊急ではない（旗側max_y>1.0で反対側<0.5のみで即時旗側変更が発動せず）
#       - v070の問題点: 中程度危機回避の発動抑制が不完全（連続発動を完全に抑制できていない）
#       - v070の問題点: 高度危機回避の閾値が高すぎ（1.8で発動、max_yが既に致命的）
#       - 旗側max_y管理の超緊急化: 旗側max_y>0.9かつ反対側<0.4なら即時旗側変更(v071新規)
#       - 旗側max_y管理の緊急化: 旗側max_y>反対側max_y+0.4かつ反対側max_y<0.9なら旗側変更(v071: 反対側max_yを厳密化)
#       - 旗側変更ロジックの根本的再構築: 反対側max_yを厳密に考慮し、反対側が低い場合のみ旗側変更(v071新規)
#       - 高度危機回避の早期化: 閾値を1.8→1.6に緩和、発動タイミングの早期化(v071修正)
#       - 大型ピース旗側変更の慎重化: 旗側max_y>1.0かつ反対側<0.7なら旗側変更(v071: 厳格化)
#       - 中程度危機回避の条件厳格化: max_y>1.3で発動(v070: 1.2→1.3, 厳格化)
#       - 中程度危機回避の発動抑制: 前回高度危機回避の場合は発動禁止(v071新規)
#       - 連続危機回避の完全抑制: 前回が高度危機回避の場合、次は旗側変更を優先(v070維持)
#       - シェイク戦略の発動条件緩和: 無マージ2ターンで発動(v070: 3→2)
#
# v070: 連続危機回避の完全抑制とマージ戦略の強化(2026-02-26)
#       - 履歴分析(致命的): Turn 62-63 (max_y=2.34, merge_available=True)で「高度危機回避」2ターン連続発動しマージ見逃し
#       - 履歴分析(致命的): Turn 51-54でmax_yが0.19→1.52に急増し中程度危機回避が連続発動
#       - 履歴分析(致命的): Turn 62でtype11がy=3.057でデッドラインオーバーしゲームオーバー
#       - 履歴分析(致命的): 最終盤面でtype9が左右散在(左側2個、右側1個)、type11が左側で孤立
#       - v069の問題点: 高度危機回避の閾値(1.7)が高すぎ、発動が遅れmax_yが急上昇した
#       - v069の問題点: 連続危機回避を抑制するロジックが不完全、Turn 62-63で2ターン連続発動
#       - v069の問題点: クールダウン1ターンが短すぎ、左右交互ドロップの原因
#       - v069の問題点: 旗側max_y管理の発動条件が緩すぎ(旗側max_y>1.1のみで即時旗側変更が発動せず)
#       - v069の問題点: 中型ピース旗側配置が旗側max_y上昇を加速
#       - 高度危機回避の再厳格化: 閾値を1.7→1.8に厳格化、両側差を0.5→0.6に厳格化(v070修正)
#       - 連続危機回避の完全抑制: 高度危機回避は1ターンのみ発動し、次は必ず旗側変更またはマージ(v070新規)
#       - 旗側変更クールダウン延長: 1ターン→2ターン、左右交互ドロップ防止(v070修正)
#       - 旗側max_y管理の超緊急化: 旗側max_y>1.0で即時旗側変更(v069: 1.1→1.0, 早期化)
#       - 旗側max_y管理の緊急化: 旗側max_y>0.6かつ反対側<0.3なら旗側変更(v069: 0.8→0.6, 0.4→0.3, 緊急化)
#       - マージ戦略の超緊急閾値追加: max_y>1.5でscore>-8のマージを許容(v070新規)
#       - 中型ピース旗側配置の慎重化: 旗側max_y>0.8なら反対側配置(v070新規)
#       - シェイク戦略の発動緩和: 無マージ2ターンで発動(v069: 3ターン→2ターン)
#
# v069: マージ絶対最優先化と高度危機回避抑制(2026-02-26)
#       - 履歴分析(致命的): Turn 66 (max_y=2.02, merge_available=True, grade=NEAR)で「高度危機回避」優先されマージ見逃し
#       - 履歴分析(致命的): Turn 65-72で「高度危機回避」が8ターン連続発動しmax_yが1.64→2.82に急増
#       - 履歴分析(致命的): 最終盤面でtype9+が左右散在(左側6個、右側6個)
#       - 履歴分析: 左側type9 max_y=2.82でデッドライン(y≒2.5)を超えゲームオーバー
#       - 履歴分析: 最終状態でtype7に対してmergeable=0、高度危機回避が唯一の選択肢だった
#       - v068の問題点: 高度危機回避(max_y>1.5)がマージ判定より前におり、マージ見逃しを引き起こした
#       - v068の問題点: 高度危機回避が発動しすぎ、高さの低い側へのドロップが逆にmax_yを上昇させた
#       - v068の問題点: 旗側変更の発動が遅すぎ(旗側max_y>1.2)、max_yが急上昇した後では旗側変更が間に合わない
#       - マージ絶対最優先化: 高度危機回避の前にマージ可能チェックを追加し、マージ可能なら即時実行(v069新規)
#       - 高度危機回避抑制: 高度危機回避の閾値を厳格化(max_y>1.7で発動)(v068: 1.5→1.7, 厳格化)
#       - 高度危機回避条件: 両側のmax_y差が0.5以上ある場合のみ発動(高さの低い側へのドロップが無意味な場合を回避)
#       - 高度危機回避時の旗側回避: max_y>1.7で旗側max_y>1.5かつ反対側<0.5なら旗側変更を優先(v069新規)
#       - 旗側変更早期化: 旗側max_y>1.1で即時旗側変更(v068: 1.2→1.1, 早期化)
#       - 旗側変更緊急化: 旗側max_y>0.8かつ反対側<0.4なら旗側変更(v068: 0.7→0.8, 早期化)
#       - 緊急時マージ閾値緩和: max_y>1.4ならscore>-8のマージを許容(v069新規)
#       - 中型ピース反対側配置を廃止: type5-6も旗側に配置し、旗側集約を強化(v069修正)

# モジュールレベル変数(試合内の状態保持)
_flag_side = None  # 旗側: "left" または "right"
_last_drop_x = 0.0
_consecutive_no_merge = 0  # 連続無マージ数
_flag_change_cooldown = 0  # 旗側変更クールダウン(ターン数)
_last_was_high_crisis = False  # 前回が高度危機回避だったかどうか
_consecutive_crisis = 0  # v070: 連続危機回避数


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
    global \
        _flag_side, \
        _last_drop_x, \
        _consecutive_no_merge, \
        _flag_change_cooldown, \
        _last_was_high_crisis, \
        _consecutive_crisis

    results = analysis.get("results", [])
    pieces = game_state.get("pieces", [])
    next_piece = game_state.get("next", {})
    next_type = next_piece.get("type", 0)
    next_r = next_piece.get("r", 0.5)
    reactor = analysis.get("reactor", {})

    # 反応器情報の取得
    reactive_pairs = len(reactor.get("reactive_pairs", []))

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

    # --- 旗側集約強化(type9+が旗側にない場合、低い側を旗側にする) ---
    if _flag_side is not None and _flag_change_cooldown == 0:
        left_9plus_max_y = calculate_side_max_y(pieces, "left", min_type=9)
        right_9plus_max_y = calculate_side_max_y(pieces, "right", min_type=9)
        left_9plus_count = len([p for p in pieces if p["type"] >= 9 and p["x"] < 0])
        right_9plus_count = len([p for p in pieces if p["type"] >= 9 and p["x"] > 0])

        # 旗側にtype9+がない場合、より低い側を旗側にする
        if (
            _flag_side == "left" and left_9plus_count == 0 and right_9plus_count > 0
        ) or (
            _flag_side == "right" and right_9plus_count == 0 and left_9plus_count > 0
        ):
            if left_9plus_max_y < right_9plus_max_y and _flag_side != "left":
                _flag_side = "left"
                _flag_change_cooldown = 2  # v070: 1→2
            elif right_9plus_max_y < left_9plus_max_y and _flag_side != "right":
                _flag_side = "right"
                _flag_change_cooldown = 2  # v070: 1→2

    # 旗側決定後は旗側変更クールダウンをデクリメント
    if _flag_change_cooldown > 0:
        _flag_change_cooldown -= 1

    # --- マージ戦略の絶対的最優先化(高度危機回避より前にチェック) ---
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
        # v070改善: 超緊急時マージ戦略(max_y>1.5ならscore>-8のマージを許容)
        if max_y > 1.5:  # v070新規
            valid_merges = [r for r in mergeable_results if r.get("score", 0) > -8]
            if valid_merges:
                best = max(valid_merges, key=lambda r: r.get("score", 0))
                x = best["x"]
                score = best.get("score", 0)
                _consecutive_no_merge = 0
                _last_was_high_crisis = False
                _consecutive_crisis = 0
                _last_drop_x = x
                return {
                    "x": x,
                    "reason": f"マージ(超緊急) x={x:.2f} (score={score:.1f})",
                }

        # 致命的時マージ戦略(max_y>2.0ならscore>-15のマージを許容)
        if max_y > 2.0:
            valid_merges = [r for r in mergeable_results if r.get("score", 0) > -15]
            if valid_merges:
                best = max(valid_merges, key=lambda r: r.get("score", 0))
                x = best["x"]
                score = best.get("score", 0)
                _consecutive_no_merge = 0
                _last_was_high_crisis = False
                _consecutive_crisis = 0
                _last_drop_x = x
                return {
                    "x": x,
                    "reason": f"マージ(致命) x={x:.2f} (score={score:.1f})",
                }

        # 緊急時マージ戦略(max_y>1.3ならscore>-5のマージを許容)
        if max_y > 1.3:
            valid_merges = [r for r in mergeable_results if r.get("score", 0) > -5]
            if valid_merges:
                best = max(valid_merges, key=lambda r: r.get("score", 0))
                x = best["x"]
                score = best.get("score", 0)
                _consecutive_no_merge = 0
                _last_was_high_crisis = False
                _consecutive_crisis = 0
                _last_drop_x = x
                return {
                    "x": x,
                    "reason": f"マージ(緊急) x={x:.2f} (score={score:.1f})",
                }

            # max_y>1.3で低スコアマージがない場合もpositive_merge_resultsをチェック
            positive_merge_results = [
                r for r in mergeable_results if r.get("score", 0) > 0
            ]
            if positive_merge_results:
                # DIRECTマージ優先
                direct_merges = [
                    r
                    for r in positive_merge_results
                    if r.get("merge_grade") == "DIRECT"
                ]
                if direct_merges:
                    best = max(direct_merges, key=lambda r: r.get("score", 0))
                else:
                    best = max(positive_merge_results, key=lambda r: r.get("score", 0))

                x = best["x"]
                score = best.get("score", 0)
                _consecutive_no_merge = 0
                _last_was_high_crisis = False
                _consecutive_crisis = 0
                _last_drop_x = x
                return {"x": x, "reason": f"マージ x={x:.2f} (score={score:.1f})"}

        # 中程度緊急時マージ戦略(max_y>1.1ならscore>-3のマージを許容)
        if max_y > 1.1:
            valid_merges = [r for r in mergeable_results if r.get("score", 0) > -3]
            if valid_merges:
                best = max(valid_merges, key=lambda r: r.get("score", 0))
                x = best["x"]
                score = best.get("score", 0)
                _consecutive_no_merge = 0
                _last_was_high_crisis = False
                _consecutive_crisis = 0
                _last_drop_x = x
                return {
                    "x": x,
                    "reason": f"マージ(中緊急) x={x:.2f} (score={score:.1f})",
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
            _last_was_high_crisis = False
            _consecutive_crisis = 0
            _last_drop_x = x
            return {"x": x, "reason": f"マージ x={x:.2f} (score={score:.1f})"}

    # --- v071改善: 旗側max_y管理の超緊急化(旗側max_y>0.9かつ反対側<0.4なら即時旗側変更) ---
    if _flag_side is not None and _flag_change_cooldown == 0:
        left_max_y = calculate_side_max_y(pieces, "left")
        right_max_y = calculate_side_max_y(pieces, "right")
        flag_side_max_y = left_max_y if _flag_side == "left" else right_max_y
        opposite_side_max_y = right_max_y if _flag_side == "left" else left_max_y

        if flag_side_max_y > 0.9 and opposite_side_max_y < 0.4:  # v071: 超緊急化
            _flag_side = "right" if _flag_side == "left" else "left"
            _flag_change_cooldown = 2
            target_x = 2.8 if _flag_side == "right" else -2.8
            _consecutive_no_merge += 1
            _last_was_high_crisis = False
            _consecutive_crisis = 0
            _last_drop_x = target_x
            return {
                "x": target_x,
                "reason": f"旗側変更(超緊急) x={target_x:.2f} (旗側max_y={flag_side_max_y:.2f},反対側={opposite_side_max_y:.2f})",
            }

    # --- v071改善: 旗側max_y管理の緊急化(旗側max_y>反対側max_y+0.4かつ反対側max_y<0.9なら旗側変更) ---
    if _flag_side is not None and _flag_change_cooldown == 0:
        left_max_y = calculate_side_max_y(pieces, "left")
        right_max_y = calculate_side_max_y(pieces, "right")
        flag_side_max_y = left_max_y if _flag_side == "left" else right_max_y
        opposite_side_max_y = right_max_y if _flag_side == "left" else left_max_y

        if (
            flag_side_max_y > opposite_side_max_y + 0.4 and opposite_side_max_y < 0.9
        ):  # v071: 反対側max_yを厳密化
            _flag_side = "right" if _flag_side == "left" else "left"
            _flag_change_cooldown = 2
            target_x = 2.8 if _flag_side == "right" else -2.8
            _consecutive_no_merge += 1
            _last_was_high_crisis = False
            _consecutive_crisis = 0
            _last_drop_x = target_x
            return {
                "x": target_x,
                "reason": f"旗側変更 x={target_x:.2f} (旗側max_y={flag_side_max_y:.2f},反対側={opposite_side_max_y:.2f})",
            }

    # --- v071改善: 旗側変更ロジックの根本的再構築(反対側max_yを厳密に考慮) ---
    if _flag_side is not None and _flag_change_cooldown == 0:
        left_max_y = calculate_side_max_y(pieces, "left")
        right_max_y = calculate_side_max_y(pieces, "right")
        flag_side_max_y = left_max_y if _flag_side == "left" else right_max_y
        opposite_side_max_y = right_max_y if _flag_side == "left" else left_max_y

        # v071: 反対側が低い場合のみ旗側変更
        if flag_side_max_y > opposite_side_max_y + 0.4 and opposite_side_max_y < 0.9:
            _flag_side = "right" if _flag_side == "left" else "left"
            _flag_change_cooldown = 2
            target_x = 2.8 if _flag_side == "right" else -2.8
            _consecutive_no_merge += 1
            _last_was_high_crisis = False
            _consecutive_crisis = 0
            _last_drop_x = target_x
            return {
                "x": target_x,
                "reason": f"旗側変更 x={target_x:.2f} (旗側max_y={flag_side_max_y:.2f},反対側={opposite_side_max_y:.2f})",
            }

    # --- v071改善: 連続危機回避の完全抑制 ---
    # 前回が高度危機回避だった場合、次は高度危機回避を禁止
    if _last_was_high_crisis:
        _last_was_high_crisis = False
        _consecutive_crisis = 0
        # フォールバックへ進んで旗側変更やマージを試す
        pass
    else:
        # --- v071改善: 高度危機回避の早期化 ---
        # 閾値を1.8→1.6に緩和し、両側のmax_y差が0.5以上ある場合のみ発動
        if max_y > 1.6:  # v071: 1.8→1.6, 早期化
            left_max_y = calculate_side_max_y(pieces, "left")
            right_max_y = calculate_side_max_y(pieces, "right")

            # 両側の差が十分大きい場合のみ発動
            if abs(left_max_y - right_max_y) > 0.5:  # v071: 0.6→0.5
                lower_side = "left" if left_max_y < right_max_y else "right"
                target_x = 2.8 if lower_side == "right" else -2.8

                # 旗側max_y>1.3かつ反対側<0.6なら旗側変更を優先
                if _flag_side is not None:
                    flag_side_max_y = (
                        left_max_y if _flag_side == "left" else right_max_y
                    )
                    opposite_side_max_y = (
                        right_max_y if _flag_side == "left" else left_max_y
                    )

                    if (
                        flag_side_max_y > 1.3
                        and opposite_side_max_y < 0.6
                        and _flag_change_cooldown == 0
                    ):
                        _flag_side = "right" if _flag_side == "left" else "left"
                        _flag_change_cooldown = 2
                        target_x = 2.8 if _flag_side == "right" else -2.8

                _consecutive_no_merge += 1
                _last_was_high_crisis = True
                _consecutive_crisis += 1
                _last_drop_x = target_x
                return {
                    "x": target_x,
                    "reason": f"高度危機回避 x={target_x:.2f}",
                }

    # --- クールダウン中旗側回避の根本的簡素化(max_y<=0.6かつ旗側が高い場合のみ回避) ---
    if _flag_side is not None:
        left_max_y = calculate_side_max_y(pieces, "left")
        right_max_y = calculate_side_max_y(pieces, "right")

        if _flag_side == "left":
            flag_side_max_y = left_max_y
            opposite_side_max_y = right_max_y
        else:
            flag_side_max_y = right_max_y
            opposite_side_max_y = left_max_y

        # max_y<=0.6かつ旗側が高い場合のみ旗側回避を有効化
        if max_y <= 0.6:
            if flag_side_max_y > 1.0 and flag_side_max_y > opposite_side_max_y:
                target_x = 2.8 if _flag_side == "left" else -2.8
                _consecutive_no_merge += 1
                _last_was_high_crisis = False
                _consecutive_crisis = 0
                _last_drop_x = target_x
                return {
                    "x": target_x,
                    "reason": f"旗側回避 x={target_x:.2f} (旗側max_y={flag_side_max_y:.2f},反対側max_y={opposite_side_max_y:.2f})",
                }

    # --- v071改善: 中程度危機回避の発動抑制(前回高度危機回避の場合は発動禁止) ---
    if _last_was_high_crisis:
        _last_was_high_crisis = False
        _consecutive_crisis = 0
        # フォールバックへ進んで旗側変更やマージを試す
        pass
    else:
        # --- v071改善: 中程度危機回避の条件厳格化(max_y>1.3で発動) ---
        if max_y > 1.3:  # v071: 1.2→1.3, 厳格化
            left_max_y = calculate_side_max_y(pieces, "left")
            right_max_y = calculate_side_max_y(pieces, "right")

            lower_side = "left" if left_max_y < right_max_y else "right"
            target_x = 2.8 if lower_side == "right" else -2.8

            _consecutive_no_merge += 1
            _last_was_high_crisis = False
            _consecutive_crisis = 0
            _last_drop_x = target_x
            return {
                "x": target_x,
                "reason": f"中程度危機回避 x={target_x:.2f}",
            }

    # --- 大型ピース旗側配置(旗側max_y>1.0かつ反対側<0.7なら旗側変更) ---
    if _flag_side is not None and 7 <= next_type <= 12:
        left_max_y = calculate_side_max_y(pieces, "left")
        right_max_y = calculate_side_max_y(pieces, "right")

        # 旗側max_y>1.0かつ反対側<0.7なら旗側変更
        flag_side_max_y = left_max_y if _flag_side == "left" else right_max_y
        opposite_side_max_y = right_max_y if _flag_side == "left" else left_max_y

        if flag_side_max_y > 1.0 and opposite_side_max_y < 0.7:  # v071: 厳格化
            _flag_side = "right" if _flag_side == "left" else "left"
            _flag_change_cooldown = 2

        lower_side = "left" if left_max_y < right_max_y else "right"
        target_x = 2.8 if lower_side == "right" else -2.8

        _last_drop_x = target_x
        return {
            "x": target_x,
            "reason": f"大型ピース旗側 x={target_x:.2f} (旗側={lower_side})",
        }

    # --- v070改善: 中型ピース旗側配置の慎重化(旗側max_y>0.8なら反対側配置) ---
    if _flag_side is not None and 5 <= next_type <= 6:
        left_max_y = calculate_side_max_y(pieces, "left")
        right_max_y = calculate_side_max_y(pieces, "right")
        flag_side_max_y = left_max_y if _flag_side == "left" else right_max_y

        # v070改善: 旗側max_y>0.8なら反対側配置
        if flag_side_max_y > 0.8:
            target_x = 2.8 if _flag_side == "left" else -2.8
        else:
            target_x = 2.8 if _flag_side == "right" else -2.8

        _last_drop_x = target_x
        return {
            "x": target_x,
            "reason": f"中型ピース旗側 x={target_x:.2f}",
        }

    # --- v070改善: シェイク戦略の発動条件緩和(無マージ2ターンで発動) ---
    _consecutive_no_merge += 1
    if _consecutive_no_merge >= 2 and next_type <= 4:  # v070: 3→2
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
            _last_was_high_crisis = False
            _consecutive_crisis = 0
            _last_drop_x = best_x
            return {
                "x": best_x,
                "reason": f"シェイク戦略(無マージ={_consecutive_no_merge}) x={best_x:.2f}",
            }

    # --- nextNext保護 ---
    next_next = game_state.get("nextNext", {})
    next_next_type = next_next.get("type", 0)
    if next_next_type > 0 and next_next_type == next_type:
        left_max_y = calculate_side_max_y(pieces, "left")
        right_max_y = calculate_side_max_y(pieces, "right")

        if left_max_y < right_max_y:
            target_x = -2.8
        else:
            target_x = 2.8

        _consecutive_no_merge += 1
        _last_was_high_crisis = False
        _consecutive_crisis = 0
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
