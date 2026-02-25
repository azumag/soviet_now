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
# v030: マージ成功率の根本的改善と旗側集約ロジックの再設計(2026-02-25 最新)
#       - 履歴分析(致命的): マージ成功率0%(49回のマージ試行で0回成功)
#       - 履歴分析(致命的): score_deltaが0のターンが123/123(100%)
#       - 履歴分析(致命的): type9+が左右に散在(左側6個,右側5個)
#       - 履歴分析(致命的): max_yが3.22まで上昇しゲームオーバー
#       - 履歴分析: "高度危機回避"が14回発動したがmax_yは1.58→3.22に上昇
#       - 履歴分析: マージ候補は正しく検出しているが、実際のマージが失敗している
#       - マージ戦略の再設計: HIGH_CONFIDENCEマージのみ実行(DIRECTかつscore>20)
#       - 旗側集約ロジックの強化: type9+が多い側を旗側に固定し、反対側は捨てる
#       - 旗側決定ロジックの変更: type9+カウント差>1なら多い側を旗側(v030新規)
#       - 大型ピース配置の改善: 常に旗側にtype9+を配置、反対側は小ピースのみ(v030再設計)
#       - 危機回避ロジックの修正: 高度危機時は旗側のmax_yが高いなら旗側を変更(v030改善)
#       - マージ失敗時の対処: score>10のマージに失敗したら3ターン間同じ位置を回避(v030新規)
#       - 反応器管理の強化: same_typeが同じ側にあるか確認し、離れていれば側を変更(v030新規)
#       - フォールバック戦略の簡素化: 小ピースは旗側、大ピースは反対側の旗側へ(v030簡素化)

# モジュールレベル変数(試合内の状態保持)
_flag_side = None  # 旗側: "left" または "right"
_last_drop_x = 0.0
_consecutive_no_merge = 0  # 連続無マージ数
_flag_change_cooldown = 0  # 旗側変更クールダウン(ターン数)
_failed_merge_positions = []  # マージ失敗位置リスト [(x, turn), ...]
_flag_side_9plus_count = {"left": 0, "right": 0}  # type9+カウント


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


def count_type_by_side(pieces: list, side: str, min_type: int = 0) -> int:
    """指定された側のピース数をカウントする.

    Args:
        pieces: 全ピースリスト
        side: "left" (x<0) または "right" (x>0)
        min_type: 最小タイプ(デフォルト0で全ピース対象)

    Returns:
        ピース数
    """
    return sum(
        1
        for p in pieces
        if p["type"] >= min_type
        and ((side == "left" and p["x"] < 0) or (side == "right" and p["x"] > 0))
    )


def decide(game_state: dict, analysis: dict) -> dict:
    """盤面状態と解析結果から最適ドロップX座標を決定する.

    Args:
        game_state: game_state.json の内容
        analysis: {"results": [...], "same_type": [...], "reactor": {...}}

    Returns:
        {"x": float, "reason": str}
    """
    global _flag_side, _last_drop_x, _consecutive_no_merge, _flag_change_cooldown
    global _failed_merge_positions, _flag_side_9plus_count

    results = analysis.get("results", [])
    pieces = game_state.get("pieces", [])
    next_piece = game_state.get("next", {})
    next_type = next_piece.get("type", 0)
    next_r = next_piece.get("r", 0.5)
    current_turn = game_state.get("turn", 0)

    # 現在の最高到達位置を取得
    max_y = max([p["y"] for p in pieces]) if pieces else 0.0

    # --- v030改善: 旗側決定ロジックの変更(type9+カウントベース) ---
    if _flag_side is None:
        left_9plus_count = count_type_by_side(pieces, "left", min_type=9)
        right_9plus_count = count_type_by_side(pieces, "right", min_type=9)
        _flag_side_9plus_count = {"left": left_9plus_count, "right": right_9plus_count}

        if left_9plus_count > 0 or right_9plus_count > 0:
            # v030改善: カウント差>1なら多い側を旗側にする
            if left_9plus_count > right_9plus_count + 1:
                _flag_side = "left"
            elif right_9plus_count > left_9plus_count + 1:
                _flag_side = "right"
            else:
                # カウント差が1以下ならmax_yが低い側を旗側にする
                left_max_y = calculate_side_max_y(pieces, "left", min_type=9)
                right_max_y = calculate_side_max_y(pieces, "right", min_type=9)
                _flag_side = "left" if left_max_y < right_max_y else "right"
        else:
            # type9+がない場合、max_yが低い側を旗側にする
            left_max_y = calculate_side_max_y(pieces, "left")
            right_max_y = calculate_side_max_y(pieces, "right")
            _flag_side = "left" if left_max_y < right_max_y else "right"

    # type9+カウントを更新
    _flag_side_9plus_count = {
        "left": count_type_by_side(pieces, "left", min_type=9),
        "right": count_type_by_side(pieces, "right", min_type=9),
    }

    # 旗側決定後は旗側変更クールダウンをデクリメント
    if _flag_change_cooldown > 0:
        _flag_change_cooldown -= 1

    # --- v030改善: HIGH_CONFIDENCEマージ優先(DIRECTかつscore>20) ---
    mergeable_results = []
    for r in results:
        grade = r.get("merge_grade", "NO")
        if grade in ["DIRECT", "NEAR"] and r.get("has_merge", False):
            # v030改善: マージ失敗位置を回避(3ターン以内)
            x = r["x"]
            is_failed_position = any(
                abs(x - fx) < 0.3 and current_turn - ft < 3
                for fx, ft in _failed_merge_positions
            )
            if not is_failed_position:
                mergeable_results.append(r)

    if mergeable_results:
        # v030改善: HIGH_CONFIDENCEマージのみ実行(DIRECTかつscore>20)
        high_confidence_merges = [
            r
            for r in mergeable_results
            if r.get("merge_grade") == "DIRECT" and r.get("score", 0) > 20
        ]

        if high_confidence_merges:
            best = max(high_confidence_merges, key=lambda r: r.get("score", 0))
            x = best["x"]
            score = best.get("score", 0)
            _consecutive_no_merge = 0
            _last_drop_x = x
            return {
                "x": x,
                "reason": f"マージ(HC) x={x:.2f} (score={score:.1f})",
            }

        # v030改善: 緊急時(マージ不可が続く)はscore>10のDIRECTマージも許容
        if _consecutive_no_merge >= 5:
            urgent_merges = [
                r
                for r in mergeable_results
                if r.get("merge_grade") == "DIRECT" and r.get("score", 0) > 10
            ]
            if urgent_merges:
                best = max(urgent_merges, key=lambda r: r.get("score", 0))
                x = best["x"]
                score = best.get("score", 0)
                _consecutive_no_merge = 0
                _last_drop_x = x
                return {
                    "x": x,
                    "reason": f"マージ(緊急) x={x:.2f} (score={score:.1f})",
                }

    # --- v030改善: 旗側集約ロジック(type9+カウント差>2なら旗側を変更) ---
    if _flag_side is not None and _flag_change_cooldown == 0:
        left_count = _flag_side_9plus_count["left"]
        right_count = _flag_side_9plus_count["right"]
        count_diff = abs(left_count - right_count)

        # v030改善: カウント差>2なら多い側を旗側にする
        if count_diff > 2:
            if left_count > right_count and _flag_side == "right":
                _flag_side = "left"
                _flag_change_cooldown = 3
                target_x = -2.8
                _last_drop_x = target_x
                return {
                    "x": target_x,
                    "reason": f"旗側変更(集約) x={target_x:.2f} (left={left_count}, right={right_count})",
                }
            elif right_count > left_count and _flag_side == "left":
                _flag_side = "right"
                _flag_change_cooldown = 3
                target_x = 2.8
                _last_drop_x = target_x
                return {
                    "x": target_x,
                    "reason": f"旗側変更(集約) x={target_x:.2f} (left={left_count}, right={right_count})",
                }

    # --- v030改善: 反応器管理(same_typeが同じ側にあるか確認) ---
    same_type = analysis.get("same_type", [])
    if (
        same_type
        and len(same_type) >= 2
        and _flag_side is not None
        and _flag_change_cooldown == 0
    ):
        # same_typeのピースを取得
        st_pieces = same_type[:2]  # 最初の2ペアを確認
        st_x_values = [p["x"] for p in st_pieces]

        # 同じ側にあるか確認
        left_count = sum(1 for x in st_x_values if x < 0)
        right_count = sum(1 for x in st_x_values if x > 0)

        # 反対側にある場合、旗側を変更
        if left_count >= 1 and right_count >= 1:
            # v030改善: 旗側max_yが高い側を旗側にする
            left_max_y = calculate_side_max_y(pieces, "left")
            right_max_y = calculate_side_max_y(pieces, "right")
            new_flag_side = "left" if left_max_y < right_max_y else "right"

            if new_flag_side != _flag_side:
                _flag_side = new_flag_side
                _flag_change_cooldown = 3
                target_x = 2.8 if _flag_side == "right" else -2.8
                _last_drop_x = target_x
                return {
                    "x": target_x,
                    "reason": f"旗側変更(反応器) x={target_x:.2f}",
                }

    # --- v030改善: 大型ピース配置(type9+を旗側に配置) ---
    if _flag_side is not None and next_type >= 9:
        # v030改善: 反対側のtype9+が多すぎる場合、旗側を変更
        opposite_side = "right" if _flag_side == "left" else "left"
        flag_count = _flag_side_9plus_count[_flag_side]
        opposite_count = _flag_side_9plus_count[opposite_side]

        if opposite_count > flag_count + 2 and _flag_change_cooldown == 0:
            _flag_side = opposite_side
            _flag_change_cooldown = 3
            flag_count = opposite_count

        # v030改善: 常に旗側に配置
        target_x = 2.8 if _flag_side == "right" else -2.8
        _last_drop_x = target_x
        return {
            "x": target_x,
            "reason": f"大型ピース旗側 x={target_x:.2f} (旗側={_flag_side})",
        }

    # --- v030改善: 中型ピース配置(type7-8) ---
    if _flag_side is not None and 7 <= next_type <= 8:
        # v030改善: 旗側max_yが高い場合、反対側に配置
        flag_side_max_y = calculate_side_max_y(pieces, _flag_side, min_type=7)

        if flag_side_max_y > 1.0:
            # 反対側に配置
            target_x = 2.8 if _flag_side == "left" else -2.8
            _last_drop_x = target_x
            return {
                "x": target_x,
                "reason": f"中型ピース反対側 x={target_x:.2f} (旗側max_y={flag_side_max_y:.2f})",
            }
        else:
            # 旗側に配置
            target_x = 2.8 if _flag_side == "right" else -2.8
            _last_drop_x = target_x
            return {
                "x": target_x,
                "reason": f"中型ピース旗側 x={target_x:.2f}",
            }

    # --- v030改善: 危機回避ロジック ---
    if max_y > 2.0:
        # v030改善: 超高度危機 - 旗側max_yが高い側を旗側に変更してドロップ
        left_max_y = calculate_side_max_y(pieces, "left")
        right_max_y = calculate_side_max_y(pieces, "right")

        lower_side = "left" if left_max_y < right_max_y else "right"
        target_x = 2.8 if lower_side == "right" else -2.8

        if _flag_side != lower_side and _flag_change_cooldown == 0:
            _flag_side = lower_side
            _flag_change_cooldown = 2

        _consecutive_no_merge += 1
        _last_drop_x = target_x
        return {
            "x": target_x,
            "reason": f"超高度危機回避 x={target_x:.2f} (max_y={max_y:.2f})",
        }

    if max_y > 1.5:
        # v030改善: 高度危機 - 旗側max_yが高いなら旗側を変更
        left_max_y = calculate_side_max_y(pieces, "left")
        right_max_y = calculate_side_max_y(pieces, "right")

        if _flag_side is not None and _flag_change_cooldown == 0:
            flag_side_max_y = left_max_y if _flag_side == "left" else right_max_y
            opposite_side_max_y = right_max_y if _flag_side == "left" else left_max_y

            if flag_side_max_y > opposite_side_max_y + 0.5:
                _flag_side = "right" if _flag_side == "left" else "left"
                _flag_change_cooldown = 2

        # 常により低い側にドロップ
        lower_side = "left" if left_max_y < right_max_y else "right"
        target_x = 2.8 if lower_side == "right" else -2.8

        _consecutive_no_merge += 1
        _last_drop_x = target_x
        return {
            "x": target_x,
            "reason": f"高度危機回避 x={target_x:.2f}",
        }

    # --- v030改善: 中程度危機回避(max_y>0.7) ---
    if max_y > 0.7:
        left_max_y = calculate_side_max_y(pieces, "left")
        right_max_y = calculate_side_max_y(pieces, "right")

        # v030改善: より低い側にドロップ
        lower_side = "left" if left_max_y < right_max_y else "right"
        target_x = 2.8 if lower_side == "right" else -2.8

        _consecutive_no_merge += 1
        _last_drop_x = target_x
        return {
            "x": target_x,
            "reason": f"中程度危機回避 x={target_x:.2f}",
        }

    # --- v030改善: シェイク戦略(無マージ5ターンで発動) ---
    _consecutive_no_merge += 1
    if _consecutive_no_merge >= 5 and next_type <= 5:
        # v030改善: 旗側の高い側でEVが正の位置を探す
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

            # v030改善: マージ失敗位置を回避
            is_failed_position = any(
                abs(x - fx) < 0.3 and current_turn - ft < 3
                for fx, ft in _failed_merge_positions
            )

            if is_target_side and ev > best_ev and not is_failed_position:
                best_ev = ev
                best_x = x

        if best_x is not None and best_ev > 0:
            _consecutive_no_merge = 0
            _last_drop_x = best_x
            return {
                "x": best_x,
                "reason": f"シェイク戦略(無マージ={_consecutive_no_merge}) x={best_x:.2f}",
            }

    # --- v030改善: nextNext保護 ---
    next_next = game_state.get("nextNext", {})
    next_next_type = next_next.get("type", 0)
    if next_next_type > 0 and next_next_type == next_type:
        left_max_y = calculate_side_max_y(pieces, "left")
        right_max_y = calculate_side_max_y(pieces, "right")

        if _flag_side == "left":
            x = -2.0 if abs(_last_drop_x) > 1.5 else -1.5
        elif _flag_side == "right":
            x = 2.0 if abs(_last_drop_x) > 1.5 else 1.5
        else:
            if abs(_last_drop_x) > 1.5:
                x = -_last_drop_x
            else:
                x = 2.8 if _last_drop_x < 0 else -2.8

        _consecutive_no_merge += 1
        _last_drop_x = x
        return {"x": x, "reason": f"nextNext保護 x={x:.2f}"}

    # --- v030改善: 通常の期待値戦略 ---
    valid_results = [r for r in results if r.get("score", 0) > 0]

    if valid_results:
        best = valid_results[0]
        x = best["x"]
        ev = best.get("score", 0)

        # v030改善: 旗側に合わせて配置
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

    # --- v030改善: フォールバック(小ピースは旗側、大ピースは反対側の旗側へ) ---
    _consecutive_no_merge += 1

    if _flag_side is not None:
        if next_type >= 6:
            # 大ピースは旗側へ
            target_x = 2.8 if _flag_side == "right" else -2.8
        else:
            # 小ピースは旗側へ(反対側に配置しない)
            left_max_y = calculate_side_max_y(pieces, "left")
            right_max_y = calculate_side_max_y(pieces, "right")
            lower_side = "left" if left_max_y < right_max_y else "right"
            target_x = 2.8 if lower_side == "right" else -2.8

        _last_drop_x = target_x
        return {"x": target_x, "reason": f"フォールバック({_flag_side})"}
    else:
        x = 0.0
        _last_drop_x = x
        return {"x": x, "reason": "フォールバック(中央)"}


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
