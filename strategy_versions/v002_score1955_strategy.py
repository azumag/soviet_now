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

# モジュールレベル変数（試合内の状態保持）
_flag_side = None  # 旗側: "left" または "right"
_last_drop_x = 0.0
_consecutive_no_merge = 0  # 連続無マージ数


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

    # --- 1. マージ可能なら最優先（DIRECT/NEAR） ---
    mergeable_results = []
    for r in results:
        grade = r.get("merge_grade", "NO")
        if grade in ["DIRECT", "NEAR"] and r.get("has_merge", False):
            mergeable_results.append(r)

    if mergeable_results:
        # DIRECTマージがあれば最優先
        direct_merges = [
            r for r in mergeable_results if r.get("merge_grade") == "DIRECT"
        ]
        if direct_merges:
            best = max(direct_merges, key=lambda r: r.get("score", 0))
            x = best["x"]
            score = best.get("score", 0)
            _last_drop_x = x
            return {"x": x, "reason": f"DIRECTマージ x={x:.2f} (score={score:.1f})"}

        # NEARマージがあれば選択
        best = max(mergeable_results, key=lambda r: r.get("score", 0))
        x = best["x"]
        score = best.get("score", 0)
        _last_drop_x = x
        return {"x": x, "reason": f"NEARマージ x={x:.2f} (score={score:.1f})"}

    # --- 2. 高さ危機回避 ---
    if max_y > 1.5:
        # 危機的状況：旗側と反対側にドロップして高さを下げる
        if _flag_side == "left":
            x = 3.0  # 旗側左なら右側にドロップ
        else:
            x = -3.0  # 旗側右なら左側にドロップ

        _consecutive_no_merge += 1
        _last_drop_x = x
        return {"x": x, "reason": f"危機回避(max_y={max_y:.2f}) x={x:.2f}"}

    # --- 3. 旗側集約戦略 ---
    if _flag_side is not None and results:
        # type9+ の次のピースなら旗側から配置
        if next_type >= 9:
            if _flag_side == "left":
                # 左側から（-3.0～-0.5）
                for r in results:
                    if r["x"] < 0:
                        x = r["x"]
                        ev = r.get("score", 0)
                        _last_drop_x = x
                        return {
                            "x": x,
                            "reason": f"大型ピース旗側 x={x:.2f} (EV={ev:.1f})",
                        }
            else:
                # 右側から（0.5～3.0）
                for r in results:
                    if r["x"] > 0:
                        x = r["x"]
                        ev = r.get("score", 0)
                        _last_drop_x = x
                        return {
                            "x": x,
                            "reason": f"大型ピース旗側 x={x:.2f} (EV={ev:.1f})",
                        }

    # --- 4. 次のピース保護（nextNextのマージ経路を塞がない） ---
    # nextNextと同じtypeがあれば、そのマージ経路を保護
    next_next = game_state.get("nextNext", {})
    next_next_type = next_next.get("type", 0)
    if next_next_type > 0 and next_next_type == next_type:
        # 同じtypeが続く場合、現在のドロップと次のドロップを分ける
        if abs(_last_drop_x) > 1.5:
            # 前回が端側なら、今回も端側（ただし反対側）
            x = -_last_drop_x
        else:
            # 前回が中央なら、端側に配置
            x = 2.5 if _last_drop_x < 0 else -2.5

        _consecutive_no_merge += 1
        _last_drop_x = x
        return {"x": x, "reason": f"nextNext保護 x={x:.2f}"}

    # --- 5. シェイク戦略（連続無マージ対策） ---
    _consecutive_no_merge += 1
    if _consecutive_no_merge >= 5:
        # 小ピースで下層を揺らす
        if next_type <= 4:
            # 旗側と反対側に小ピースをドロップしてマージを誘発
            if _flag_side == "left":
                x = 3.0
            else:
                x = -3.0

            _last_drop_x = x
            return {
                "x": x,
                "reason": f"シェイク戦略(無マージ={_consecutive_no_merge}) x={x:.2f}",
            }

    # --- 6. 通常の期待値戦略 ---
    # EVがマイナスの結果は避ける
    valid_results = [r for r in results if r.get("score", 0) > -100]

    if valid_results:
        best = valid_results[0]
        x = best["x"]
        ev = best.get("score", 0)

        # 旗側に合わせて配置
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

    # --- 7. フォールバック: 中央 ---
    _consecutive_no_merge += 1
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
