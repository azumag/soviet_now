#!/usr/bin/env python3
"""strategy.py - AI改善対象の決定スクリプト"""

# 固定インターフェース:
#  decide(game_state: dict, analysis: dict) -> dict
#    戻り値: {"x": float, "reason": str}
#
# AI改変可能: decide() 内部,ヘルパー関数,定数,import
# AI改変禁止: decide() シグネチャ,if __name__ == "__main__" ブロック

# --- 変更履歴 ---
# v047: 反応器情報活用と旗側回避ロジックの根本的改善(2026-02-25)
#       - 履歴分析: ターン52-64で「高度危機回避」が左側(x=-2.8)に連続ドロップ
#       - 履歴分析: ターン52左側max_y=0.73, 右側旗側max_y≒0.8で左側にドロップ(誤り)
#       - 履歴分析: ターン54max_y=1.61急増, ターン55旗側max_y=1.21→1.74, ターン56max_y=1.74
#       - 履歴分析: ターン59-64「高度危機回避(旗側回避)」発動だが左側ドロップ続行(max_yは1.61→3.65)
#       - 履歴分析: turn 41でマージ候補あるが「高度危機回避」優先でマージ見逃し
#       - 履歴分析: reactor_reactive_pairsは5と高いが旗側回避が機能しなかった
#       - 高度危機回避の旗側考慮復活: 旗側max_y>1.5なら旗側回避(旗側変更なし時のみ適用)
#       - 反応器情報旗側回避活用: reactive_pairs>3なら旗側回避無効化(大型ピース到達可)
#       - 反応器情報旗側変更活用: reactive_pairs>0なら旗側変更抑制
#       - クールダウン中旗側回避の根本的改善: 常により低い側にドロップ(max_y>0.6で無効化)
#       - 旗側max_y管理と反応器統合: 旗側max_y>1.5かつreactive_pairs<=3なら旗側回避優先
#       - マージ戦略旗側フィルタリング追加: 旗側max_y>1.5かつreactive_pairs<=3なら旗側マージ回避
# v046: reactive_pairs未定義バグ修正とマージ戦略の強化(2026-02-25)
#       - 致命バグ修正: reactive_pairsが定義されておらず、常に例外が発生
#       - 履歴分析: 全83ターン中51ターンで例外発生(61%!)
#       - 履歴分析: 例外ターンでは常にdecision_x=0.0でフォールバック
#       - reactive_pairs定義追加: reactor.get("reactive_pairs", 0)で取得
#       - v045改善の適用: 旗側変更条件厳格化、反応器活用、マージ閾値緩和
#       - クールダウン中旗側回避の厳格化、高度危機回避の旗側考慮
#       - 大型ピース旗側配置の改善、シェイク戦略の発動条件緩和
# v045: 旗側変更の抑制と反応器活用の強化(2026-02-25)
#       - 履歴分析: turn 52-63で高度危機回避連続でmax_y=0.73→2.57急増(致命的)
#       - 履歴分析: turn 58-61でmerge_available=trueだが危機回避優先でマージ見逃し
#       - 履歴分析: 旗側変更がturn 12, 16, 57, 62で頻発(過多)
#       - 履歴分析: 最終盤面でtype9+が左右散在(旗側集約失敗)
#       - 履歴分析: reactor_reactive_pairs=1と低く、大型ピースが到達不能
#       - 旗側変更条件の厳格化: 旗側max_y>1.5かつ反対側max_y<0.8なら旗側変更(v044: 1.5/1.0→1.5/0.8)
#       - 旗側変更クールダウンの延長: 3ターン(v044: 2→3,頻発抑制)
#       - 反応器活用の強化: reactive_pairs>0なら旗側変更を抑制(v045新規)
#       - マージ閾値の緩和: v036の閾値を踏襲(max_y>2.0:-15, max_y>1.5:-8, max_y>1.0:-5)
#       - 高度危機回避の旗側考慮: 旗側max_y>1.5なら旗側回避を優先(v045新規)
#       - クールダウン中旗側回避の厳格化: max_y>0.5でのみ発動(v044: 0.8→0.5)
#       - 大型ピース旗側配置の改善: reactive_pairs>0なら旗側を維持(v045新規)
#       - 大型ピース旗側max_y管理: 旗側max_y>1.0なら旗側変更検討(v044維持)
#       - シェイク戦略の発動条件緩和: 無マージ4ターンで発動(v044: 3→4)

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
    reactor = analysis.get("reactor", {})

    # 反応器情報の取得
    reactive_pairs = len(reactor.get("reactive_pairs", []))

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

    # --- v045改善: マージ戦略の超最優先化(危機回避より先にマージ判定を実行) ---
    mergeable_results = []
    for r in results:
        grade = r.get("merge_grade", "NO")
        # v046修正: gradeが文字列であることを確認
        if (
            isinstance(grade, str)
            and grade in ["DIRECT", "NEAR"]
            and r.get("has_merge", False)
        ):
            mergeable_results.append(r)

    if mergeable_results:
        # v047改善: 旗側max_y>1.5かつreactive_pairs<=3なら旗側マージを回避
        if _flag_side is not None and reactive_pairs <= 3:
            left_max_y = calculate_side_max_y(pieces, "left")
            right_max_y = calculate_side_max_y(pieces, "right")
            flag_side_max_y = left_max_y if _flag_side == "left" else right_max_y

            # 旗側マージをフィルタリング
            if flag_side_max_y > 1.5:
                filtered_results = []
                for r in mergeable_results:
                    x = r["x"]
                    is_flag_side = (_flag_side == "left" and x < 0) or (
                        _flag_side == "right" and x > 0
                    )
                    if not is_flag_side:
                        filtered_results.append(r)
                mergeable_results = filtered_results

        # v045改善: 致命時マージ戦略(max_y>2.0ならscore>-15のマージを許容)
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
                    "reason": f"マージ(致命) x={x:.2f} (score={score:.1f})",
                }

        # v045改善: 超緊急時マージ戦略(max_y>1.5ならscore>-8のマージを許容)
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

        # v045改善: 緊急時マージ戦略(max_y>1.0ならscore>-5のマージを許容)
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

    # --- v047改善: 旗側変更ロジックの改善(反応器情報を活用) ---
    if _flag_side is not None and _flag_change_cooldown == 0:
        left_max_y = calculate_side_max_y(pieces, "left")
        right_max_y = calculate_side_max_y(pieces, "right")
        flag_side_max_y = left_max_y if _flag_side == "left" else right_max_y
        opposite_side_max_y = right_max_y if _flag_side == "left" else left_max_y

        # v047改善: 反応器情報旗側変更活用: reactive_pairs>0なら旗側変更抑制
        if reactive_pairs > 0:
            pass  # 旗側変更しない
        elif flag_side_max_y > 1.5 and opposite_side_max_y < 0.8:
            _flag_side = "right" if _flag_side == "left" else "left"
            _flag_change_cooldown = 3
            target_x = 2.8 if _flag_side == "right" else -2.8
            _consecutive_no_merge += 1
            _last_drop_x = target_x
            return {
                "x": target_x,
                "reason": f"旗側変更(危機) x={target_x:.2f} (旗側max_y={flag_side_max_y:.2f},反対側={opposite_side_max_y:.2f})",
            }

    # --- v045改善: 旗側max_y管理の強化(反応器情報を考慮) ---
    if _flag_side is not None and _flag_change_cooldown == 0:
        left_max_y = calculate_side_max_y(pieces, "left")
        right_max_y = calculate_side_max_y(pieces, "right")
        flag_side_max_y = left_max_y if _flag_side == "left" else right_max_y

        # v047改善: 反応器情報旗側変更活用: reactive_pairs>0なら旗側変更抑制
        if reactive_pairs > 0:
            pass  # 旗側変更しない
        elif flag_side_max_y > 1.3:
            _flag_side = "right" if _flag_side == "left" else "left"
            _flag_change_cooldown = 3
            target_x = 2.8 if _flag_side == "right" else -2.8
            _consecutive_no_merge += 1
            _last_drop_x = target_x
            return {
                "x": target_x,
                "reason": f"旗側変更(緊急) x={target_x:.2f} (旗側max_y={flag_side_max_y:.2f})",
            }

    # --- v045改善: 旗側max_y管理の緩和(反応器情報を考慮) ---
    if _flag_side is not None and _flag_change_cooldown == 0:
        left_max_y = calculate_side_max_y(pieces, "left")
        right_max_y = calculate_side_max_y(pieces, "right")
        flag_side_max_y = left_max_y if _flag_side == "left" else right_max_y
        opposite_side_max_y = right_max_y if _flag_side == "left" else left_max_y

        # v047改善: 反応器情報旗側変更活用: reactive_pairs>0なら旗側変更抑制
        if reactive_pairs > 0:
            pass  # 旗側変更しない
        elif (
            max_y > 0.6
            and flag_side_max_y > 1.1
            and flag_side_max_y > opposite_side_max_y
        ):
            _flag_side = "right" if _flag_side == "left" else "left"
            _flag_change_cooldown = 3

    # --- v047改善: クールダウン中旗側回避の根本的改善 ---
    if _flag_side is not None:
        left_max_y = calculate_side_max_y(pieces, "left")
        right_max_y = calculate_side_max_y(pieces, "right")

        if _flag_side == "left":
            flag_side_max_y = left_max_y
            opposite_side_max_y = right_max_y
        else:
            flag_side_max_y = right_max_y
            opposite_side_max_y = left_max_y

        # v047改善: max_y>0.6でクールダウン中旗側回避を無効化(危機時は通常ロジック優先)
        if max_y <= 0.6:
            if flag_side_max_y > 1.0 and flag_side_max_y > opposite_side_max_y:
                target_x = 2.8 if _flag_side == "left" else -2.8
                _consecutive_no_merge += 1
                _last_drop_x = target_x
                return {
                    "x": target_x,
                    "reason": f"クールダウン中旗側回避 x={target_x:.2f} (旗側max_y={flag_side_max_y:.2f},反対側max_y={opposite_side_max_y:.2f})",
                }

    # --- v047改善: 高度危機回避の旗側考慮復活 ---
    if max_y > 1.0:
        left_max_y = calculate_side_max_y(pieces, "left")
        right_max_y = calculate_side_max_y(pieces, "right")

        # v047改善: 旗側max_y>1.5なら旗側回避を優先(反応器状態を考慮)
        if _flag_side is not None and reactive_pairs <= 3:
            flag_side_max_y = left_max_y if _flag_side == "left" else right_max_y
            if flag_side_max_y > 1.5:
                # 旗側回避(反対側にドロップ)
                target_x = -2.8 if _flag_side == "left" else 2.8
                _consecutive_no_merge += 1
                _last_drop_x = target_x
                return {
                    "x": target_x,
                    "reason": f"高度危機回避(旗側回避) x={target_x:.2f}",
                }

        lower_side = "left" if left_max_y < right_max_y else "right"
        target_x = 2.8 if lower_side == "right" else -2.8

        _consecutive_no_merge += 1
        _last_drop_x = target_x
        return {
            "x": target_x,
            "reason": f"高度危機回避 x={target_x:.2f}",
        }

    # --- v045改善: 中程度危機回避の条件緩和(max_y>0.3で発動) ---
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

    # --- v045改善: 大型ピース旗側配置(type7-12を旗側に配置) ---
    if _flag_side is not None and 7 <= next_type <= 12:
        left_max_y = calculate_side_max_y(pieces, "left")
        right_max_y = calculate_side_max_y(pieces, "right")

        # v045改善: reactive_pairs>0なら旗側を維持
        if reactive_pairs > 0:
            # 旗側に配置
            target_x = -2.8 if _flag_side == "left" else 2.8
            _last_drop_x = target_x
            return {
                "x": target_x,
                "reason": f"大型ピース旗側 x={target_x:.2f} (旗側={_flag_side})",
            }

        # v045改善: 旗側max_y>1.0なら旗側変更してから配置
        flag_side_max_y = left_max_y if _flag_side == "left" else right_max_y
        opposite_side_max_y = right_max_y if _flag_side == "left" else left_max_y

        if flag_side_max_y > 1.0 and opposite_side_max_y < 0.8:
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

    # --- v045改善: シェイク戦略の発動条件緩和(無マージ4ターンで発動) ---
    _consecutive_no_merge += 1
    if _consecutive_no_merge >= 4 and next_type <= 4:
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

    # --- v045改善: nextNext保護 ---
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
