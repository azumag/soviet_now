#!/usr/bin/env python3
"""strategy.py - AI改善対象の決定スクリプト"""

# 固定インターフェース:
#  decide(game_state: dict, analysis: dict) -> dict
#    戻り値: {"x": float, "reason": str}
#
# AI改変可能: decide() 内部,ヘルパー関数,定数,import
# AI改変禁止: decide() シグネチャ,if __name__ == "__main__" ブロック

# --- 変更履歴 ---
# v064: マージ最優先化と旗側集約強化(2026-02-25)
#       - 履歴分析(致命的): Turn 79 (max_y=1.25, merge_available=True, grade=NEAR)で「高度危機回避」優先されマージ見逃し
#       - 履歴分析(致命的): Turn 83-84 (max_y=1.78-1.79, merge_available=True)で「高度危機回避」連続優先されマージ見逃し
#       - 履歴分析(致命的): Turn 77-88で「高度危機回避」が5ターン連続発動しmax_yが1.24→2.96に急増
#       - 履歴分析(致命的): Turn 81で「旗側変更」直後に「高度危機回避」発動し旗変更無意味化
#       - 履歴分析: 最終盤面type9+が左右散在(左側type9 y=1.15, type10 y=-1.77, type11 y=2.16 / 右側type9 y=-2.17, type10 y=-2.15, type11 y=1.76)
#       - v063の問題点: max_y>1.0で低スコアマージがない場合、positive_merge_resultsに進まず「高度危機回避」が優先
#       - v063の問題点: 旗側max_y管理の発動条件が緩すぎ(旗側max_y>1.8のみで即時旗側変更が発動せず)
#       - マージ最優先化: max_y>1.0で低スコアマージがない場合もpositive_merge_resultsをチェック(v064新規)
#       - マージ戦戦略の最優先化: マージ判定を旗側変更より優先(危機回避よりさらに優先)(v064新規)
#       - 高度危機回避マージ抑制: マージが可能な場合は高度危機回避を抑制(v064新規)
#       - 旗側max_y管理厳格化: 旗側max_y>1.5で即時旗側変更(v063: 1.8→1.5,早期化)
#       - 旗側max_y管理緊急化: 旗側max_y>1.2かつ反対側<0.8なら旗側変更(v064新規)
#       - 旗側集約強化: type9+が旗側にない場合、低い側を旗側にする(v064新規)
#       - クールダウン短縮: 旗側変更クールダウンを3ターンに設定(v063: 2→3,安定性向上)
#       - 連続危機回避抑制: 前回が「高度危機回避」の場合、次は「旗側変更」を優先(v064新規)
#
# v063: 旗側変更優先化と左右交互ドロップ防止(2026-02-25)
#       - 履歴分析(致命的): Turn 51-54でmax_y=0.06→1.00に急増し旗側変更発動
#       - 履歴分析(致命的): Turn 53でmerge_available=trueだが「旗側変更」優先されマージ見逃し
#       - 履歴分析(致命的): Turn 54でmerge_available=trueだが「高度危機回避」優先されマージ見逃し
#       - 履歴分析(致命的): Turn 61,63,67で左右交互に旗側変更しmax_yが2.61に急増
#       - 履歴分析(致命的): Turn 70で旗側max_y=1.90に達したが旗側変更なし
#       - 履歴分析: 最終盤面type9+が左右散在(左側type11 y=2.28, 右側type9 y=2.35)
#       - v062の問題点: 高度危機回避を旗側変更より優先すると、両側が高い時に左右交互ドロップ
#       - v062の問題点: 旗側変更ロジックが反対側が低い条件を考慮不足
#       - 旗側変更優先化: 高度危機回避より旗側変更を優先(v063新規)
#       - 旗側変更条件厳格化: 反対側max_y<1.2かつ旗側max_y>反対側+0.3なら旗側変更(v063新規)
#       - 旗側max_y管理強化: 旗側max_y>1.8かつ反対側<1.2なら即時旗側変更(v063新規)
#       - 旗側max_y管理緊急化: 反対側<1.0かつ旗側max_y>2.0なら即時旗側変更(v063新規)
#       - クールダウン中旗側回避緩和: max_y<=0.6で発動(v062: 0.8→0.6)
#       - 中程度危機回避条件厳格化: max_y>1.2で発動(v062: 0.3→1.2)
#       - 高度危機回避条件緩和: max_y>1.0で発動(v062維持)
#       - マージ戦略の閾値維持: 致命的時(max_y>2.0, score>-15), 超緊急時(max_y>1.5, score>-8), 緊急時(max_y>1.0, score>-5)(v062維持)
#       - 大型ピース旗側配置緩和: 旗側max_y>1.5かつ反対側<0.8なら旗側変更(v062: 1.0→1.5緩和)
#       - 旗側決定ロジックの簡素化維持: type9+があれば常にmax_yが低い側を旗側(v060維持)

# モジュールレベル変数(試合内の状態保持)
_flag_side = None  # 旗側: "left" または "right"
_last_drop_x = 0.0
_consecutive_no_merge = 0  # 連続無マージ数
_flag_change_cooldown = 0  # 旗側変更クールダウン(ターン数)
_last_was_high_crisis = False  # v064: 前回が高度危機回避だったかどうか


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
    global \
        _flag_side, \
        _last_drop_x, \
        _consecutive_no_merge, \
        _flag_change_cooldown, \
        _last_was_high_crisis

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

    # --- v053改善: 旗側決定ロジックの簡素化(type9+があれば常にmax_yが低い側を旗側) ---
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

    # --- v064改善: 旗側集約強化(type9+が旗側にない場合、低い側を旗側にする) ---
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
                _flag_change_cooldown = 3
            elif right_9plus_max_y < left_9plus_max_y and _flag_side != "right":
                _flag_side = "right"
                _flag_change_cooldown = 3

    # 旗側決定後は旗側変更クールダウンをデクリメント
    if _flag_change_cooldown > 0:
        _flag_change_cooldown -= 1

    # --- v064改善: マージ戦略の超最優先化(旗側変更より優先, 高度危機回避よりさらに優先) ---
    mergeable_results = []
    for r in results:
        grade = r.get("merge_grade", "NO")
        # v054改善: gradeが文字列であることを確認
        if (
            isinstance(grade, str)
            and grade in ["DIRECT", "NEAR"]
            and r.get("has_merge", False)
        ):
            mergeable_results.append(r)

    if mergeable_results:
        # v036改善: 致命的時マージ戦略(max_y>2.0ならscore>-15のマージを許容)
        if max_y > 2.0:
            valid_merges = [r for r in mergeable_results if r.get("score", 0) > -15]
            if valid_merges:
                best = max(valid_merges, key=lambda r: r.get("score", 0))
                x = best["x"]
                score = best.get("score", 0)
                _consecutive_no_merge = 0
                _last_was_high_crisis = False
                _last_drop_x = x
                return {
                    "x": x,
                    "reason": f"マージ(致命) x={x:.2f} (score={score:.1f})",
                }

        # v036改善: 超緊急時マージ戦略(max_y>1.5ならscore>-8のマージを許容)
        if max_y > 1.5:
            valid_merges = [r for r in mergeable_results if r.get("score", 0) > -8]
            if valid_merges:
                best = max(valid_merges, key=lambda r: r.get("score", 0))
                x = best["x"]
                score = best.get("score", 0)
                _consecutive_no_merge = 0
                _last_was_high_crisis = False
                _last_drop_x = x
                return {
                    "x": x,
                    "reason": f"マージ(超緊急) x={x:.2f} (score={score:.1f})",
                }

        # v064改善: 緊急時マージ戦略の改善(max_y>1.0ならpositive_merge_resultsもチェック)
        if max_y > 1.0:
            valid_merges = [r for r in mergeable_results if r.get("score", 0) > -5]
            if valid_merges:
                best = max(valid_merges, key=lambda r: r.get("score", 0))
                x = best["x"]
                score = best.get("score", 0)
                _consecutive_no_merge = 0
                _last_was_high_crisis = False
                _last_drop_x = x
                return {
                    "x": x,
                    "reason": f"マージ(緊急) x={x:.2f} (score={score:.1f})",
                }

            # v064改善: max_y>1.0で低スコアマージがない場合もpositive_merge_resultsをチェック
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
                _last_drop_x = x
                return {"x": x, "reason": f"マージ x={x:.2f} (score={score:.1f})"}

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
            _last_drop_x = x
            return {"x": x, "reason": f"マージ x={x:.2f} (score={score:.1f})"}

    # --- v064改善: 旗側max_y管理の厳格化(旗側max_y>1.5で即時旗側変更) ---
    if _flag_side is not None and _flag_change_cooldown == 0:
        left_max_y = calculate_side_max_y(pieces, "left")
        right_max_y = calculate_side_max_y(pieces, "right")
        flag_side_max_y = left_max_y if _flag_side == "left" else right_max_y
        opposite_side_max_y = right_max_y if _flag_side == "left" else left_max_y

        if flag_side_max_y > 1.5 and opposite_side_max_y < 1.2:
            _flag_side = "right" if _flag_side == "left" else "left"
            _flag_change_cooldown = 3
            target_x = 2.8 if _flag_side == "right" else -2.8
            _consecutive_no_merge += 1
            _last_was_high_crisis = False
            _last_drop_x = target_x
            return {
                "x": target_x,
                "reason": f"旗側変更(緊急) x={target_x:.2f} (旗側max_y={flag_side_max_y:.2f},反対側={opposite_side_max_y:.2f})",
            }

    # --- v064改善: 旗側max_y管理の緊急化(旗側max_y>1.2かつ反対側<0.8なら旗側変更) ---
    if _flag_side is not None and _flag_change_cooldown == 0:
        left_max_y = calculate_side_max_y(pieces, "left")
        right_max_y = calculate_side_max_y(pieces, "right")
        flag_side_max_y = left_max_y if _flag_side == "left" else right_max_y
        opposite_side_max_y = right_max_y if _flag_side == "left" else left_max_y

        if flag_side_max_y > 1.2 and opposite_side_max_y < 0.8:
            _flag_side = "right" if _flag_side == "left" else "left"
            _flag_change_cooldown = 3
            target_x = 2.8 if _flag_side == "right" else -2.8
            _consecutive_no_merge += 1
            _last_was_high_crisis = False
            _last_drop_x = target_x
            return {
                "x": target_x,
                "reason": f"旗側変更 x={target_x:.2f} (旗側max_y={flag_side_max_y:.2f},反対側={opposite_side_max_y:.2f})",
            }

    # --- v063改善: 旗側変更ロジックの強化(反対側が十分低い場合のみ旗側変更) ---
    if _flag_side is not None and _flag_change_cooldown == 0:
        left_max_y = calculate_side_max_y(pieces, "left")
        right_max_y = calculate_side_max_y(pieces, "right")
        flag_side_max_y = left_max_y if _flag_side == "left" else right_max_y
        opposite_side_max_y = right_max_y if _flag_side == "left" else left_max_y

        # v063改善: 旗側max_y>反対側max_y+0.3かつ反対側max_y<1.2なら即時旗側変更
        if flag_side_max_y > opposite_side_max_y + 0.3 and opposite_side_max_y < 1.2:
            _flag_side = "right" if _flag_side == "left" else "left"
            _flag_change_cooldown = 3
            target_x = 2.8 if _flag_side == "right" else -2.8
            _consecutive_no_merge += 1
            _last_was_high_crisis = False
            _last_drop_x = target_x
            return {
                "x": target_x,
                "reason": f"旗側変更 x={target_x:.2f} (旗側max_y={flag_side_max_y:.2f},反対側={opposite_side_max_y:.2f})",
            }

    # --- v064改善: 高度危機回避マージ抑制(マージが可能な場合は高度危機回避を抑制) ---
    if max_y > 1.0 and not _last_was_high_crisis:
        # マージ可能かチェック
        merge_possible = False
        for r in results:
            grade = r.get("merge_grade", "NO")
            if (
                isinstance(grade, str)
                and grade in ["DIRECT", "NEAR"]
                and r.get("has_merge", False)
            ):
                merge_possible = True
                break

        if merge_possible:
            # マージが可能ならフォールバックへ進まず、通常処理へ
            pass
        else:
            left_max_y = calculate_side_max_y(pieces, "left")
            right_max_y = calculate_side_max_y(pieces, "right")

            lower_side = "left" if left_max_y < right_max_y else "right"
            target_x = 2.8 if lower_side == "right" else -2.8

            _consecutive_no_merge += 1
            _last_was_high_crisis = True
            _last_drop_x = target_x
            return {
                "x": target_x,
                "reason": f"高度危機回避 x={target_x:.2f}",
            }
    elif max_y > 1.0 and _last_was_high_crisis:
        # v064改善: 連続危機回避抑制(前回が高度危機回避の場合、次は旗側変更を優先)
        _last_was_high_crisis = False  # リセットして次のループで旗側変更を試す
        # フォールバックに進ませる
        pass

    # --- v063改善: クールダウン中旗側回避の根本的簡素化(max_y<=0.6かつ旗側が高い場合のみ回避) ---
    if _flag_side is not None:
        left_max_y = calculate_side_max_y(pieces, "left")
        right_max_y = calculate_side_max_y(pieces, "right")

        if _flag_side == "left":
            flag_side_max_y = left_max_y
            opposite_side_max_y = right_max_y
        else:
            flag_side_max_y = right_max_y
            opposite_side_max_y = left_max_y

        # v063改善: max_y<=0.6かつ旗側が高い場合のみ旗側回避を有効化
        if max_y <= 0.6:
            if flag_side_max_y > 1.0 and flag_side_max_y > opposite_side_max_y:
                target_x = 2.8 if _flag_side == "left" else -2.8
                _consecutive_no_merge += 1
                _last_was_high_crisis = False
                _last_drop_x = target_x
                return {
                    "x": target_x,
                    "reason": f"クールダウン中旗側回避 x={target_x:.2f} (旗側max_y={flag_side_max_y:.2f},反対側max_y={opposite_side_max_y:.2f})",
                }

    # --- v063改善: 中程度危機回避の条件厳格化(max_y>1.2で発動) ---
    if max_y > 1.2 and not _last_was_high_crisis:
        left_max_y = calculate_side_max_y(pieces, "left")
        right_max_y = calculate_side_max_y(pieces, "right")

        lower_side = "left" if left_max_y < right_max_y else "right"
        target_x = 2.8 if lower_side == "right" else -2.8

        _consecutive_no_merge += 1
        _last_was_high_crisis = False
        _last_drop_x = target_x
        return {
            "x": target_x,
            "reason": f"中程度危機回避 x={target_x:.2f}",
        }

    # --- v063改善: 大型ピース旗側配置条件緩和(旗側max_y>1.5かつ反対側<0.8なら旗側変更) ---
    if _flag_side is not None and 7 <= next_type <= 12:
        left_max_y = calculate_side_max_y(pieces, "left")
        right_max_y = calculate_side_max_y(pieces, "right")

        # v063改善: 旗側max_y>1.5かつ反対側<0.8なら旗側変更
        flag_side_max_y = left_max_y if _flag_side == "left" else right_max_y
        opposite_side_max_y = right_max_y if _flag_side == "left" else left_max_y

        if flag_side_max_y > 1.5 and opposite_side_max_y < 0.8:
            _flag_side = "right" if _flag_side == "left" else "left"
            _flag_change_cooldown = 3

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

    # --- v054改善: シェイク戦略の発動条件緩和(無マージ3ターンで発動) ---
    _consecutive_no_merge += 1
    if _consecutive_no_merge >= 3 and next_type <= 4:
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
            _last_drop_x = best_x
            return {
                "x": best_x,
                "reason": f"シェイク戦略(無マージ={_consecutive_no_merge}) x={best_x:.2f}",
            }

    # --- v054改善: nextNext保護 ---
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
