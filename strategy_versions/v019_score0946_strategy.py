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
# v031: ロジック順序の根本的修正と旗側max_y管理の強化(2026-02-25 最新)
#       - 履歴分析(致命的): Turn 62で旗側max_y=1.73だが旗側="left"のままtype11を左側にドロップ
#       - 履歴分析(致命的): 大型ピース配置ロジックが旗側max_y管理ロジックより先に実行されている
#       - 履歴分析(致命的): Turn 63でmax_y=3.04になりtype11(y=3.04)でゲームオーバー
#       - 履歴分析(致命的): Turn 61でスコア+159のマージがあるが、その後Turn 62-63でゲームオーバー
#       - 履歴分析: マージ成功率は22回中15回増加(68%)、スコアは0→903に増加
#       - 履歴分析: type9+が左右に均等に分布(左側5個、右側5個)、旗側集約失敗
#       - ロジック順序の根本的修正: 高度危機回避をマージ戦略より優先
#       - ロジック順序の根本的修正: 旗側max_y管理を大型ピース配置より優先
#       - 高度危機回避の早期化: max_y>1.2で発動(v029: 1.5→1.2,早期発動)
#       - 旗側max_y管理の超厳格化: 旗側max_y>1.2なら即時旗側変更(v029: 1.5→1.2,早期回避)
#       - 旗側変更ロジックの緩和: max_y>0.5で旗側max_y>反対側なら旗側変更(v029: 0.8→0.5)
#       - クールダウン中旗側回避の強化: 旗側max_y>1.2なら即時反対側にドロップ(v029: 1.0→1.2)
#       - 中程度危機回避の条件緩和: max_y>0.7で発動(v029: 0.6→0.7)
#       - シェイク戦略の発動条件緩和: 無マージ5ターンで発動(v029: 4→5,慎重化)
#       - 旗側決定ロジックの変更: type9+があればmax_yが低い側を旗側にする(v029維持)
#       - 旗側変更クールダウンの短縮: 2ターン(v029維持)
#
# v032: マージ戦略の最優先化と高度危機回避条件の調整(2026-02-25 最新)
#       - 履歴分析(致命的): Turn 60で左側max_y=1.65、右側max_y=1.41だが履歴は左側にドロップ
#       - 履歴分析(致命的): Turn 61で左側にtype10をドロップし、type10マージでスコア+159
#       - 履歴分析(致命的): Turn 62で左側max_y=1.73でtype11を左側にドロップし、max_yが3.04に上昇
#       - 履歴分析(致命的): v031ではTurn 60-62で高度危機回避が発動し、右側にドロップ
#       - 履歴分析(致命的): v031ではTurn 61のマージが発生せず、スコア増加なし
#       - 履歴分析: マージ戦略を高度危機回避より優先すべきか検討
#       - 履歴分析: 高度危機回避とマージ戦略のトレードオフ
#       - マージ戦略の最優先化: マージ判定を高度危機回避より優先(v032新規)
#       - 緊急時マージ戦略: max_y>1.2かつマージ可能ならscore>0のマージを許容(v032新規)
#       - 高度危機回避の条件維持: max_y>1.2で発動(v031維持)
#       - 旗側max_y管理の超厳格化: 旗側max_y>1.2なら即時旗側変更(v031維持)
#       - 旗側変更ロジックの緩和: max_y>0.5で旗側max_y>反対側なら旗側変更(v031維持)
#       - クールダウン中旗側回避の強化: 旗側max_y>1.2なら即時反対側にドロップ(v031維持)
#
# v033: 高度危機回避の早期化と旗側max_y管理の根本的改善(2026-02-25 最新)
#       - 履歴分析(致命的): マージ成功率が0%(26回中0回成功)
#       - 履歴分析(致命的): Turn 47でmax_y=1.63に急増し、高度危機回避が発動
#       - 履歴分析(致命的): Turn 47以降、max_yは1.63→2.09→2.10→1.86→1.77→1.66→1.66→1.65→1.37→1.35→2.27→2.28→2.29→2.39→2.79→2.93→2.92→2.78→2.79→2.84→2.85と上昇
#       - 履歴分析(致命的): 最終盤面type9+が左右に分散(左側4個、右側5個)
#       - 履歴分析(致命的): 右側max_y=3.01でtype11がデッドライン超過
#       - 履歴分析(致命的): 旗側max_y管理が不十分で、旗側変更が遅い
#       - 履歴分析: クールダウン中旗側回避が正しく動いていない
#       - 高度危機回避の早期化: max_y>1.0で発動(v032: 1.2→1.0,早期発動)
#       - 旗側max_y管理の超厳格化: 旗側max_y>1.0なら即時旗側回避(v032: 1.2→1.0)
#       - クールダウン中旗側回避の修正: 旗側max_y>1.0なら即時反対側にドロップ(v032: 1.2→1.0)
#       - 旗側変更ロジックの緩和: max_y>0.4で旗側max_y>反対側なら旗側変更(v032: 0.5→0.4)
#       - 中程度危機回避の緩和: max_y>0.5で発動(v032: 0.7→0.5,早期発動)
#       - 緊急時マージ戦略: max_y>1.0かつマージ可能ならscore>-10のマージを許容(v033: 0→-10)
#       - 旗側変更クールダウンの短縮: 2ターン(v032維持)
#       - 大型ピース旗側配置の改善: 常にmax_yが低い側にドロップ(v032維持)
#       - シェイク戦略の発動条件緩和: 無マージ5ターンで発動(v032維持)

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

    # --- v032改善: マージ戦略の最優先化(マージ判定を高度危機回避より優先) ---
    mergeable_results = []
    for r in results:
        grade = r.get("merge_grade", "NO")
        if grade in ["DIRECT", "NEAR"] and r.get("has_merge", False):
            mergeable_results.append(r)

    if mergeable_results:
        # v033改善: 緊急時マージ戦略(max_y>1.0かつマージ可能ならscore>-10のマージを許容)
        if max_y > 1.0:
            # v033改善: スコア>-10のマージを許容(v032: 0→-10)
            valid_merges = [r for r in mergeable_results if r.get("score", 0) > -10]
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

    # --- v033改善: 高度危機回避の早期化(max_y>1.0で最優先発動) ---
    if max_y > 1.0:
        left_max_y = calculate_side_max_y(pieces, "left")
        right_max_y = calculate_side_max_y(pieces, "right")

        # 常により低い側にドロップ
        lower_side = "left" if left_max_y < right_max_y else "right"
        target_x = 2.8 if lower_side == "right" else -2.8

        _consecutive_no_merge += 1
        _last_drop_x = target_x
        return {
            "x": target_x,
            "reason": f"高度危機回避 x={target_x:.2f} (max_y={max_y:.2f})",
        }

    # --- v033改善: 旗側max_y管理の超厳格化(旗側max_y>1.0なら即時旗側回避) ---
    if _flag_side is not None:
        left_max_y = calculate_side_max_y(pieces, "left")
        right_max_y = calculate_side_max_y(pieces, "right")

        if _flag_side == "left":
            flag_side_max_y = left_max_y
            opposite_side_max_y = right_max_y
        else:
            flag_side_max_y = right_max_y
            opposite_side_max_y = left_max_y

        # v033改善: 旗側max_y>1.0なら即時反対側にドロップ(v032: 1.2→1.0)
        if flag_side_max_y > 1.0 and flag_side_max_y > opposite_side_max_y:
            # 反対側にドロップ
            target_x = 2.8 if _flag_side == "left" else -2.8
            _consecutive_no_merge += 1
            _last_drop_x = target_x
            return {
                "x": target_x,
                "reason": f"旗側回避 x={target_x:.2f} (旗側max_y={flag_side_max_y:.2f})",
            }

    # --- v032改善: 旗側変更ロジックの緩和 ---
    if _flag_side is not None and _flag_change_cooldown == 0:
        left_max_y = calculate_side_max_y(pieces, "left")
        right_max_y = calculate_side_max_y(pieces, "right")
        flag_side_max_y = left_max_y if _flag_side == "left" else right_max_y
        opposite_side_max_y = right_max_y if _flag_side == "left" else left_max_y

        # v033改善: max_y>0.4で旗側max_y>反対側なら旗側変更(v032: 0.5→0.4)
        if max_y > 0.4 and flag_side_max_y > opposite_side_max_y:
            _flag_side = "right" if _flag_side == "left" else "left"
            # v029改善: クールダウンを2ターンに短縮
            _flag_change_cooldown = 2

    # --- v033改善: クールダウン中旗側回避の修正 ---
    if _flag_side is not None:
        left_max_y = calculate_side_max_y(pieces, "left")
        right_max_y = calculate_side_max_y(pieces, "right")

        if _flag_side == "left":
            flag_side_max_y = left_max_y
            opposite_side_max_y = right_max_y
        else:
            flag_side_max_y = right_max_y
            opposite_side_max_y = left_max_y

        # v033改善: 旗側max_y>1.0なら即時反対側にドロップ(v032: 1.2→1.0)
        if flag_side_max_y > 1.0 and flag_side_max_y > opposite_side_max_y:
            # 反対側にドロップ
            target_x = 2.8 if _flag_side == "left" else -2.8
            _consecutive_no_merge += 1
            _last_drop_x = target_x
            return {
                "x": target_x,
                "reason": f"クールダウン中旗側回避 x={target_x:.2f} (旗側max_y={flag_side_max_y:.2f})",
            }

    # --- v033改善: 中程度危機回避の緩和(max_y>0.5で発動) ---
    if max_y > 0.5:
        left_max_y = calculate_side_max_y(pieces, "left")
        right_max_y = calculate_side_max_y(pieces, "right")

        # v029改善: より高い側にドロップしてピースを減らす
        higher_side = "left" if left_max_y > right_max_y else "right"
        target_x = 2.8 if higher_side == "right" else -2.8

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

    # --- v031改善: シェイク戦略の発動条件緩和(無マージ5ターンで発動) ---
    _consecutive_no_merge += 1
    if _consecutive_no_merge >= 5 and next_type <= 5:
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

    # --- v029改善: 次のピース保護(旗側max_yが高い場合,旗側にドロップしない) ---
    next_next = game_state.get("nextNext", {})
    next_next_type = next_next.get("type", 0)
    if next_next_type > 0 and next_next_type == next_type:
        left_max_y = calculate_side_max_y(pieces, "left")
        right_max_y = calculate_side_max_y(pieces, "right")
        flag_side_max_y = left_max_y if _flag_side == "left" else right_max_y

        # v029改善: 旗側max_yが高い場合,旗側にドロップしない
        if flag_side_max_y < 1.0:
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
