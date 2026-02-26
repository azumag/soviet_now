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
#
# v028: クールダウン中旗側回避ロジックのバグ修正と高度危機回避の早期化(2026-02-25 最新)
#       - 履歴分析(致命的): ターン60で旗側max_y=1.07,反対側max_y=0.96だが左側にドロップ(誤り!)
#       - 履歴分析(致命的): target_xの計算ロジックが誤っている(旗側="left"の時x=-2.8→右側にすべき)
#       - 履歴分析: 高度危機回避がターン64(max_y=1.74)で発動だが、すでに遅い
#       - 履歴分析: ターン64-67で左右交互に「高度危機回避」発動しmax_yが2.61に急増
#       - 履歴分析: 最終盤面type9+が左右散在(旗側集約失敗)
#       - 履歴分析: マージ成功率が低い(約63%)
#       - クールダウン中旗側回避ロジックのバグ修正: target_x=2.8 if _flag_side=="left" else -2.8(反対側)
#       - 高度危機回避の早期化: max_y>1.0で発動(v027: 1.5→1.0,早期発動)
#       - 中程度危機回避の条件緩和: max_y>0.6で発動(v027: 0.8→0.6,早期発動)
#       - 旗側max_y管理の厳格化: 旗側max_y>1.0なら旗側回避(v027: 1.2→1.0,早期回避)
#       - クールダウン中旗側回避の改善: 反対側が低い場合のみ旗側回避(旗側max_y>反対側max_y)
#       - 旗側決定ロジックの簡素化: type9+があれば常にmax_yが低い側を旗側にする(v027維持)
#       - 旗側変更ロジックの厳格化: max_y>0.8で旗側max_y>反対側なら旗側変更(v027維持)
#       - 旗側変更クールダウンの短縮: 3ターン(v027: 5→3,柔軟性向上)
#       - マージ戦略の強化: 高度危機時は低スコアマージも許容(スコア閾値を-10に設定)
#       - 大型ピース旗側配置の改善: 常にmax_yが低い側にドロップ(旗側max_y>1.0なら反対側)
#       - 旗側max_y管理の緩和: 旗側max_y<1.0でtype9+旗側配置(v027: 1.2→1.0,緩和)
#       - シェイク戦略の発動条件緩和: 無マージ3ターンで発動(v027: 2→3,慎重化)
#
# v029: マージ最優先化と旗側max_y管理の根本的改善(2026-02-25 最新)
#       - 履歴分析(致命的): ターン53でmerge_available=trueだが「高度危機回避」が優先されマージ見逃し
#       - 履歴分析(致命的): ターン60でmerge_available=true(DIRECT!)だが「高度危機回避」が優先されマージ見逃し
#       - 履歴分析(致命的): ターン61で旗側max_y=3.32,旗側回避失敗
#       - 履歴分析: マージ成功率19/30≈63%(max_y>1.0でマージ見逃し3回)
#       - 履歴分析: ターン46(1.01)→62(3.95)でmax_y急増,T53で2.17突破
#       - マージ戦略の最優先化: マージ判定を危機回避より優先(v028の順序入れ替え)
#       - 緊急時マージ戦略: max_y>1.0かつマージ可能ならscore>-5のマージを許容(v029新規)
#       - 高度危機回避の条件厳格化: max_y>1.5で発動(v028: 1.0→1.5,条件厳格化)
#       - 旗側max_y管理の超厳格化: 旗側max_y>1.5なら即時旗側変更(v028: 1.0→1.5)
#       - 旗側max_y管理の強化: 旗側max_y>1.2かつmax_y>0.8なら旗側変更(v029新規)
#       - クールダウン期間中旗側回避の改善: max_y>1.2ならクールダウン中旗側回避を無効化
#       - 旗側変更クールダウンの短縮: 2ターン(v028: 3→2,柔軟性向上)
#       - 中程度危機回避の積極化: より高い側にドロップしてピースを減らす(v029改善)
#       - シェイク戦略の発動条件緩和: 無マージ4ターンで発動(v028: 3→4)
#       - 次のピース保護の改善: 旗側max_yが高い場合,旗側にドロップしない(v029新規)
#
# v036: マージ戦略の超緊急時改善と旗側変更ロジックの強化(2026-02-25 最新)
#       - 履歴分析(致命的): ターン50-56でmax_y=1.22→3.15に急増しゲームオーバー
#       - 履歴分析(致命的): ターン55-56でmerge_available=trueだが「旗側変更(緊急)」が優先されマージ見逃し
#       - 履歴分析(致命的): ターン56で旗側max_y=2.28だが旗側変更で右側にドロップ(反対側max_y=2.27も高く無意味)
#       - 履歴分析: ターン48-49でマージ成功でmax_y低下(2.14→1.25)が、すぐに再上昇
#       - 履歴分析: マージ成功率50% (22回中11回成功)
#       - 履歴分析: max_y>1.0の状況でスコア閾値が厳しすぎる
#       - マージ戦略の致命的時改善: max_y>2.0ならscore>-15のマージを許容(v036新規)
#       - マージ戦略の超緊急時改善: max_y>1.5ならscore>-8のマージを許容(v036新規)
#       - マージ戦略の緊急時改善: max_y>1.0ならscore>-5のマージを許容(v036新規)
#       - 旗側変更ロジックの強化: 旗側max_y>反対側max_y+0.3かつ反対側max_y<1.5なら即時旗側変更(v036新規)
#       - 高度危機回避の優先度変更: マージ戦略より優先(v036: マージ優先)
#       - 高度危機回避の早期化: max_y>1.0で発動(v036新規)
#       - ゲームオーバー防止の早期化: max_y>1.3で発動(v036新規)
#       - 旗側max_y管理の緩和: 旗側max_y>1.3なら即時旗側変更(v036新規)
#       - 旗側max_y管理の緩和: 旗側max_y>1.1かつmax_y>0.6なら旗側変更(v036新規)
#       - クールダウン中旗側回避の無効化: max_y>0.8なら無効化(v036新規)
#       - 中程度危機回避の条件緩和: max_y>0.3で発動(v036新規)
#       - 大型ピース配置の改善: 旗側max_y>1.0なら旗側変更してから配置(v036新規)
#       - シェイク戦略の発動条件緩和: 無マージ3ターンで発動(v036新規)

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
        left_9plus_max_y = calculate_side_max_y(pieces, "left", min_type=9)
        right_9plus_max_y = calculate_side_max_y(pieces, "right", min_type=9)

        if left_9plus_max_y > -float("inf") or right_9plus_max_y > -float("inf"):
            if left_9plus_max_y < right_9plus_max_y:
                _flag_side = "left"
            elif right_9plus_max_y < left_9plus_max_y:
                _flag_side = "right"
            else:
                _flag_side = "left"

    # 旗側決定後は旗側変更クールダウンをデクリメント
    if _flag_change_cooldown > 0:
        _flag_change_cooldown -= 1

    # --- v036改善: マージ戦略の最優先化(マージ判定を危機回避より優先) ---
    mergeable_results = []
    for r in results:
        grade = r.get("merge_grade", "NO")
        if grade in ["DIRECT", "NEAR"] and r.get("has_merge", False):
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
                _last_drop_x = x
                return {
                    "x": x,
                    "reason": f"マージ(致命的) x={x:.2f} (score={score:.1f})",
                }

        # v036改善: 超緊急時マージ戦略(max_y>1.5ならscore>-8のマージを許容)
        if max_y > 1.5:
            valid_merges = [r for r in mergeable_results if r.get("score", 0) > -8]
            if valid_merges:
                best = max(valid_merges, key=lambda r: r.get("score", 0))
                x = best["x"]
                score = best.get("score", 0)
                _consecutive_no_merge = 0
                _last_drop_x = x
                return {
                    "x": x,
                    "reason": f"マージ(超緊急) x={x:.2f} (score={score:.1f})",
                }

        # v036改善: 緊急時マージ戦略(max_y>1.0ならscore>-5のマージを許容)
        if max_y > 1.0:
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

    # --- v036改善: 旗側変更ロジックの強化(旗側max_y>反対側max_y+0.3かつ反対側max_y<1.5なら即時旗側変更) ---
    if _flag_side is not None and _flag_change_cooldown == 0:
        left_max_y = calculate_side_max_y(pieces, "left")
        right_max_y = calculate_side_max_y(pieces, "right")
        flag_side_max_y = left_max_y if _flag_side == "left" else right_max_y
        opposite_side_max_y = right_max_y if _flag_side == "left" else left_max_y

        # v036改善: 旗側max_y>反対側max_y+0.3かつ反対側max_y<1.5なら即時旗側変更
        if flag_side_max_y > opposite_side_max_y + 0.3 and opposite_side_max_y < 1.5:
            _flag_side = "right" if _flag_side == "left" else "left"
            _flag_change_cooldown = 2
            target_x = 2.8 if _flag_side == "right" else -2.8
            _consecutive_no_merge += 1
            _last_drop_x = target_x
            return {
                "x": target_x,
                "reason": f"旗側変更(緊急) x={target_x:.2f} (旗側max_y={flag_side_max_y:.2f},反対側={opposite_side_max_y:.2f})",
            }

    # --- v036改善: 旗側max_y管理の厳格化(旗側max_y>1.3なら即時旗側変更) ---
    if _flag_side is not None and _flag_change_cooldown == 0:
        left_max_y = calculate_side_max_y(pieces, "left")
        right_max_y = calculate_side_max_y(pieces, "right")
        flag_side_max_y = left_max_y if _flag_side == "left" else right_max_y

        if flag_side_max_y > 1.3:
            _flag_side = "right" if _flag_side == "left" else "left"
            _flag_change_cooldown = 2
            _consecutive_no_merge += 1
            target_x = 2.8 if _flag_side == "right" else -2.8
            _last_drop_x = target_x
            return {
                "x": target_x,
                "reason": f"旗側変更(緊急) x={target_x:.2f} (旗側max_y={flag_side_max_y:.2f})",
            }

    # --- v036改善: 旗側max_y管理の強化(旗側max_y>1.1かつmax_y>0.6なら旗側変更) ---
    if _flag_side is not None and _flag_change_cooldown == 0:
        left_max_y = calculate_side_max_y(pieces, "left")
        right_max_y = calculate_side_max_y(pieces, "right")
        flag_side_max_y = left_max_y if _flag_side == "left" else right_max_y
        opposite_side_max_y = right_max_y if _flag_side == "left" else left_max_y

        if (
            max_y > 0.6
            and flag_side_max_y > 1.1
            and flag_side_max_y > opposite_side_max_y
        ):
            _flag_side = "right" if _flag_side == "left" else "left"
            _flag_change_cooldown = 2

    # --- v036改善: クールダウン中旗側回避の無効化(マージ可能なら旗側回避をスキップ) ---
    if _flag_side is not None:
        left_max_y = calculate_side_max_y(pieces, "left")
        right_max_y = calculate_side_max_y(pieces, "right")

        if _flag_side == "left":
            flag_side_max_y = left_max_y
            opposite_side_max_y = right_max_y
        else:
            flag_side_max_y = right_max_y
            opposite_side_max_y = left_max_y

        # v036改善: max_y>0.8ならクールダウン中旗側回避を無効化
        if max_y <= 0.8:
            if flag_side_max_y > 1.0 and flag_side_max_y > opposite_side_max_y:
                target_x = 2.8 if _flag_side == "left" else -2.8
                _consecutive_no_merge += 1
                _last_drop_x = target_x
                return {
                    "x": target_x,
                    "reason": f"クールダウン中旗側回避 x={target_x:.2f} (旗側max_y={flag_side_max_y:.2f},反対側max_y={opposite_side_max_y:.2f})",
                }

    # --- v036改善: 高度危機回避の早期化(max_y>1.0で発動) ---
    if max_y > 1.0:
        left_max_y = calculate_side_max_y(pieces, "left")
        right_max_y = calculate_side_max_y(pieces, "right")

        lower_side = "left" if left_max_y < right_max_y else "right"
        target_x = 2.8 if lower_side == "right" else -2.8

        _consecutive_no_merge += 1
        _last_drop_x = target_x
        return {
            "x": target_x,
            "reason": f"高度危機回避 x={target_x:.2f}",
        }

    # --- v036改善: 中程度危機回避の条件緩和(max_y>0.3で発動) ---
    if max_y > 0.3:
        left_max_y = calculate_side_max_y(pieces, "left")
        right_max_y = calculate_side_max_y(pieces, "right")

        lower_side = "left" if left_max_y < right_max_y else "right"
        target_x = 2.8 if lower_side == "right" else -2.8

        _consecutive_no_merge += 1
        _last_drop_x = target_x
        return {
            "x": target_x,
            "reason": f"中程度危機回避 x={target_x:.2f}",
        }

    # --- v036改善: 大型ピース旗側配置(type7-12を旗側に配置) ---
    if _flag_side is not None and 7 <= next_type <= 12:
        left_max_y = calculate_side_max_y(pieces, "left")
        right_max_y = calculate_side_max_y(pieces, "right")

        # v036改善: 旗側max_y>1.0なら旗側変更してから配置
        flag_side_max_y = left_max_y if _flag_side == "left" else right_max_y
        opposite_side_max_y = right_max_y if _flag_side == "left" else left_max_y

        if flag_side_max_y > 1.0 and opposite_side_max_y < 0.8:
            _flag_side = "right" if _flag_side == "left" else "left"
            _flag_change_cooldown = 2

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

    # --- v036改善: シェイク戦略の発動条件緩和(無マージ3ターンで発動) ---
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
            _last_drop_x = best_x
            return {
                "x": best_x,
                "reason": f"シェイク戦略(無マージ={_consecutive_no_merge}) x={best_x:.2f}",
            }

    # --- v036改善: nextNext保護 ---
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
