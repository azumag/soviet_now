#!/usr/bin/env python3
"""strategy.py - AI改善対象の決定スクリプト

固定インターフェース:
  decide(game_state: dict, analysis: dict) -> dict
    戻り値: {"x": float, "reason": str}

AI改変可能: decide() 内部、ヘルパー関数、定数、import
AI改変禁止: decide() シグネチャ、if __name__ == "__main__" ブロック
"""

# --- 変更履歴 ---
# v001: 初期スケルトン。analyze_board.analyze_drops() の最高スコア位置を返す。
# v002: マージ成功率向上と高さ管理ロジックを追加
#       - DIRECT/NEARマージ優先（低EVでも確実性重視）
#       - max_y>1.5で危機回避（旗側反対にドロップ）
#       - 旗側固定ロジック（最初のDIRECTマージ側を旗側とする）
#       - 大型ピース旗側集約（type9+は旗側から配置開始）
#       - EVマイナス回避（危機時は無視して配置優先）
# v003: マージ成功率と旗側集約の大幅改善
#       - マージ発生時の_consecutive_no_mergeリセット追加
#       - 旗側固定ロジック改善（最初のDIRECTマージのX座標基準）
#       - シェイク戦略早期化（無マージ3ターンで発動）
#       - 危機回避早期化（max_y>1.0で発動）
#       - 大型ピース旗側のEVチェック追加（EV<0なら配置見送り）
#       - 期待値戦略の強化（EV>0の位置を優先）
#       - フォールバック中央の改善（旗側を考慮）
#       - マージ戦略のEVチェック追加（EV>0のマージのみ選択）
# v004: 危機回避ロジックの根本的改善と大型ピース集約強化
#       - 危機時は両側の高さを計算して低い側を選択（旗側無視）
#       - マージ可能なら危機時でも優先（高さ下げる効果）
#       - 高い側の平均Yを計算し、明らかに低い側にドロップ
#       - type7+の旗側集約強化（旗側が決まったら全大型ピース集約）
#       - 旗側未決定時、左右のピース数で旗側決定（多い側を旗側）
#       - 危機回避のしきい値を段階化（1.0で警告、1.5で本格回避）
#       - 壁ドロップ回避（x=±3.0でのバウンドによる不安定化防止）
#       - 次の大型ピース(type7+)の旗側配置を優先
# v005: 履歴分析に基づく根本的改善（2026-02-25）
#       - 分析結果: ターン68-88でmax_yが1.99→3.94に急増しゲームオーバー
#       - 分析結果: type11が左右散在（旗側集約失敗）
#       - 旗側固定ロジック強化: ピース数>5で分布から旗側決定
#       - 大型ピース旗側集約: type9+は必ず旗側（EV>0チェック付き）
#       - 危機回避早期化: max_y>1.0で発動、高い側に配置してマージ誘発
#       - マージ戦略強化: 危機時でもマージ可能なら優先（高さ下げ効果）
#       - シェイク戦略追加: 無マージ3ターンで小ピースで下層を揺らす
#       - nextNext保護: 同typeが続く場合、マージ経路を塞がない配置
#       - 壁ドロップ回避: x=±3.0でのバウンド防止（x=±2.5を使用）

# モジュールレベル変数（試合内の状態保持）
_flag_side = None  # 旗側: "left" または "right"
_last_drop_x = 0.0
_consecutive_no_merge = 0  # 連続無マージ数


def calculate_side_height(pieces: list, side: str) -> float:
    """指定された側の平均高さを計算する。

    Args:
        pieces: 全ピースリスト
        side: "left" (x<0) または "right" (x>0)

    Returns:
        平均高さ（ピースがない場合は -inf）
    """
    side_pieces = [
        p
        for p in pieces
        if (side == "left" and p["x"] < 0) or (side == "right" and p["x"] > 0)
    ]
    if not side_pieces:
        return -float("inf")
    return sum(p["y"] for p in side_pieces) / len(side_pieces)


def determine_flag_side_from_distribution(pieces: list) -> str:
    """盤面のピース分布から旗側を決定する（旗側未決定時）。

    Args:
        pieces: 全ピースリスト

    Returns:
        "left" または "right"
    """
    left_count = len([p for p in pieces if p["x"] < 0])
    right_count = len([p for p in pieces if p["x"] > 0])
    return "left" if left_count >= right_count else "right"


def decide(game_state: dict, analysis: dict) -> dict:
    """盤面状態と解析結果から最適ドロップX座標を決定する。

    Args:
        game_state: game_state.json の内容
        analysis: {"results": [...], "same_type": [...], "reactor": {...}}

    Returns:
        {"x": float, "reason": str}
    """
    global _flag_side, _last_drop_x, _consecutive_no_merge

    results = analysis.get("results", [])
    pieces = game_state.get("pieces", [])
    next_piece = game_state.get("next", {})
    next_type = next_piece.get("type", 0)
    next_r = next_piece.get("r", 0.5)

    # 現在の最高到達位置を取得
    max_y = max([p["y"] for p in pieces]) if pieces else 0.0

    # --- 旗側固定ロジック ---
    # まだ旗側が決まっていない場合、最初のDIRECTマージを見つけたら旗側とする
    if _flag_side is None and results:
        for r in results:
            if r.get("merge_grade") == "DIRECT" and r.get("has_merge", False):
                _flag_side = "left" if r["x"] < 0 else "right"
                break

    # 旗側がまだ決まっていない場合、ピース分布から決定
    if _flag_side is None and len(pieces) > 5:
        _flag_side = determine_flag_side_from_distribution(pieces)

    # --- 1. マージ可能なら最優先（DIRECT/NEAR）---
    # 危機時でもマージは高さを下げる効果があるので優先
    mergeable_results = []
    for r in results:
        grade = r.get("merge_grade", "NO")
        if grade in ["DIRECT", "NEAR"] and r.get("has_merge", False):
            mergeable_results.append(r)

    if mergeable_results:
        # EVが正のマージのみ対象
        positive_merge_results = [r for r in mergeable_results if r.get("score", 0) > 0]

        if positive_merge_results:
            # 危機時は、高い側のマージを優先
            if max_y > 1.0:
                left_avg_y = calculate_side_height(pieces, "left")
                right_avg_y = calculate_side_height(pieces, "right")
                target_side = "left" if left_avg_y > right_avg_y else "right"

                # ターゲット側のマージを探す
                side_merges = [
                    r
                    for r in positive_merge_results
                    if (target_side == "left" and r["x"] < 0)
                    or (target_side == "right" and r["x"] > 0)
                ]
                if side_merges:
                    best = max(side_merges, key=lambda r: r.get("score", 0))
                else:
                    best = max(positive_merge_results, key=lambda r: r.get("score", 0))
            else:
                # 通常時はDIRECTマージ優先
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
            # マージ発生時は無マージカウントをリセット
            _consecutive_no_merge = 0
            _last_drop_x = x
            return {"x": x, "reason": f"マージ x={x:.2f} (score={score:.1f})"}

    # --- 2. 高さ危機回避（根本的改善） ---
    if max_y > 1.5:
        # 両側の高さを計算して低い側を選択
        left_avg_y = calculate_side_height(pieces, "left")
        right_avg_y = calculate_side_height(pieces, "right")

        # 明らかに低い側にドロップ（差が0.5以上）
        if left_avg_y > right_avg_y + 0.5:
            # 右側が低い
            target_x = 2.5  # 壁ドロップ回避
            _consecutive_no_merge += 1
            _last_drop_x = target_x
            return {
                "x": target_x,
                "reason": f"危機回避(左側高い L={left_avg_y:.1f} R={right_avg_y:.1f}) x={target_x:.2f}",
            }
        elif right_avg_y > left_avg_y + 0.5:
            # 左側が低い
            target_x = -2.5  # 壁ドロップ回避
            _consecutive_no_merge += 1
            _last_drop_x = target_x
            return {
                "x": target_x,
                "reason": f"危機回避(右側高い L={left_avg_y:.1f} R={right_avg_y:.1f}) x={target_x:.2f}",
            }
        else:
            # 両側が同じくらいなら旗側優先
            if _flag_side == "left":
                target_x = -2.5
            else:
                target_x = 2.5
            _consecutive_no_merge += 1
            _last_drop_x = target_x
            return {"x": target_x, "reason": f"危機回避(旗側) x={target_x:.2f}"}

    # --- 3. 旗側集約戦略（強化：type9+から適用） ---
    if _flag_side is not None and results:
        # type9+ の次のピースなら旗側から配置（EVチェック付き）
        if next_type >= 9:
            best_ev = -float("inf")
            best_x = None

            if _flag_side == "left":
                # 左側から（-3.0～-0.5）
                for r in results:
                    if r["x"] < 0:
                        ev = r.get("score", 0)
                        if ev > best_ev:
                            best_ev = ev
                            best_x = r["x"]
            else:
                # 右側から（0.5～3.0）
                for r in results:
                    if r["x"] > 0:
                        ev = r.get("score", 0)
                        if ev > best_ev:
                            best_ev = ev
                            best_x = r["x"]

            if best_x is not None and best_ev > -100:  # EVが極端に悪くなければ配置
                _last_drop_x = best_x
                return {
                    "x": best_x,
                    "reason": f"大型ピース旗側 x={best_x:.2f} (EV={best_ev:.1f})",
                }

    # --- 4. シェイク戦略（早期化：無マージ3ターンで発動） ---
    _consecutive_no_merge += 1
    if _consecutive_no_merge >= 3 and next_type <= 4:
        # 小ピースで下層を揺らす
        # 高い側でEVが正の位置を探す（高さを下げる効果）
        left_avg_y = calculate_side_height(pieces, "left")
        right_avg_y = calculate_side_height(pieces, "right")
        target_side = "left" if left_avg_y > right_avg_y else "right"

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
            _last_drop_x = best_x
            return {
                "x": best_x,
                "reason": f"シェイク戦略(無マージ={_consecutive_no_merge}) x={best_x:.2f}",
            }

    # --- 5. 次のピース保護（nextNextのマージ経路を塞がない） ---
    # nextNextと同じtypeがあれば、そのマージ経路を保護
    next_next = game_state.get("nextNext", {})
    next_next_type = next_next.get("type", 0)
    if next_next_type > 0 and next_next_type == next_type:
        # 同じtypeが続く場合、現在のドロップと次のドロップを分ける
        # ただし、旗側が決まっている場合は旗側を尊重
        if _flag_side == "left":
            x = -2.5 if abs(_last_drop_x) > 1.5 else -2.0
        elif _flag_side == "right":
            x = 2.5 if abs(_last_drop_x) > 1.5 else 2.0
        else:
            # 旗側未決定時は従来ロジック
            if abs(_last_drop_x) > 1.5:
                x = -_last_drop_x
            else:
                x = 2.5 if _last_drop_x < 0 else -2.5

        _consecutive_no_merge += 1
        _last_drop_x = x
        return {"x": x, "reason": f"nextNext保護 x={x:.2f}"}

    # --- 6. 通常の期待値戦略（EV>0の位置を優先） ---
    # EVが正の結果のみ対象
    valid_results = [r for r in results if r.get("score", 0) > 0]

    if valid_results:
        best = valid_results[0]
        x = best["x"]
        ev = best.get("score", 0)

        # 旗側に合わせて配置（ただしEVが優先）
        if _flag_side == "left" and x > 0 and len(valid_results) > 1:
            # 旗側左なのに右側を選ぼうとした場合、左側から探す
            for r in valid_results:
                if r["x"] < 0:
                    x = r["x"]
                    ev = r.get("score", 0)
                    break
        elif _flag_side == "right" and x < 0 and len(valid_results) > 1:
            # 旗側右なのに左側を選ぼうとした場合、右側から探す
            for r in valid_results:
                if r["x"] > 0:
                    x = r["x"]
                    ev = r.get("score", 0)
                    break

        _last_drop_x = x
        return {"x": x, "reason": f"期待値 x={x:.2f} (EV={ev:.1f})"}

    # --- 7. フォールバック: 旗側側の中央 ---
    _consecutive_no_merge += 1
    if _flag_side == "left":
        x = -1.5
    elif _flag_side == "right":
        x = 1.5
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
