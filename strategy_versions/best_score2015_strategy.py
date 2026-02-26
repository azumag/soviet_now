#!/usr/bin/env python3
"""strategy.py - AI改善対象の決定スクリプト"""

# 固定インターフェース:
# decide(game_state: dict, analysis: dict) -> dict
#    戻り値: {"x": float, "reason": str}
#
# AI改変可能: decide() 内部,ヘルパー関数,定数,import
# AI改変禁止: decide() シグネチャ,if __name__ == "__main__" ブロック

# --- 変更履歴 ---
# [BEST:604] v0: ランダム配置（ベースライン）
# [BEST:1486] v1: マージ重視戦略（DIRECT/NEAR優先、高度管理、ドリフト最小化）
# [BEST:1615] v3: 重量バランス導入版 - ピースタイプに応じた重量化、フェーズ制導入、高度管理調整
# [BEST:2015] v8: 重量バランス削除・フェーズ制再設計 - v5のロジックをベースに、重量バランス振り子パターンを解消、フェーズ閾値0.8/1.8調整
# v9: HIGHフェーズマージ強化版 - v8の構造を維持、HIGHフェーズmerge_mult 0.8→1.0、height_mult 3.0→2.2、マージなしペナルティ緩和
# v10: reactor活用・二段階スコアリング版 - reactor情報活用、マージあり/なしでスコアリングを分ける、HIGHフェーズ高度管理微強化


def decide(game_state: dict, analysis: dict) -> dict:
    """reactor情報を活用し、マージあり/なしで二段階のスコアリングを行う."""

    results = analysis.get("results", [])

    if not results:
        return {"x": 0.0, "reason": "no analysis data"}

    best_x = 0.0
    best_score = -float("inf")
    best_reason = ""

    # 盤面情報
    pieces = game_state.get("pieces", [])
    max_y = max([p["y"] for p in pieces]) if pieces else -4.0

    # フェーズ判定（v8の閾値0.8/1.8を維持）
    if max_y < 0.8:
        phase = "LOW"
        height_mult = 1.0
        merge_mult = 1.2
    elif max_y < 1.8:
        phase = "MEDIUM"
        height_mult = 2.0
        merge_mult = 1.0
    else:
        phase = "HIGH"
        height_mult = 2.4  # v10: 2.2→2.4に微増（高度管理強化）
        merge_mult = 1.0

    # 左右バランス計算（カウントベース）
    left_count = sum(1 for p in pieces if p["x"] < 0)
    right_count = len(pieces) - left_count
    balance_bias = (right_count - left_count) / (len(pieces) if pieces else 1)

    # 次のピース情報
    next_piece = game_state.get("next", {})
    next_next_piece = game_state.get("nextNext", {})
    next_type = next_piece.get("type", 0)
    next_next_type = next_next_piece.get("type", 0)

    # reactor情報（v10で新規活用）
    reactor = analysis.get("reactor", {})
    reactive_pairs_raw = reactor.get("reactive_pairs", 0)
    reactive_pairs = (
        len(reactive_pairs_raw)
        if isinstance(reactive_pairs_raw, list)
        else reactive_pairs_raw
    )

    for result in results:
        x = result["x"]
        landing_y = result.get("landing_y", 0)
        drift_x = result.get("drift_x", 0)
        drift_unc = result.get("drift_unc", 0)
        merge_grade = result.get("merge_grade", "NO")
        has_merge = result.get("has_merge", False)

        score = 0.0
        reasons = []

        # === v10: 二段階スコアリング ===
        # マージがある場合とない場合で、異なるスコアリングを行う

        if merge_grade in ("DIRECT", "NEAR", "FAR"):
            # --- マージがある場合：マージ優先、高度ペナルティ軽減 ---

            # 1. マージグレードによるスコア（重点）
            if merge_grade == "DIRECT":
                score += 1300.0 * merge_mult  # v10: 1200→1300に強化
                reasons.append("DIRECT_MERGE")
            elif merge_grade == "NEAR":
                score += 700.0 * merge_mult  # v10: 600→700に強化
                reasons.append("NEAR_MERGE")
            elif merge_grade == "FAR":
                score += 200.0 * merge_mult  # v10: 150→200に強化
                reasons.append("FAR_MERGE")

            # 2. 高度によるスコア（軽減：マージがあるならある程度許容）
            if phase == "HIGH":
                height_penalty = landing_y * 30.0  # v10: 通常の60%（高度管理緩和）
            elif phase == "MEDIUM":
                height_penalty = landing_y * 25.0
            else:
                height_penalty = landing_y * 20.0

            if phase == "HIGH" and landing_y > 0.5:
                reasons.append("HIGH_TOWER")
            elif landing_y > 0.0:
                reasons.append("HIGH_LAYER")

            score -= height_penalty

            # 3. ドリフトによるペナルティ（軽め）
            drift_penalty = (abs(drift_x) + drift_unc) * 25.0
            score -= drift_penalty

            # 4. reactorチェインボーナス（v10新規）
            # reactive_pairs >= 4 ならチェイン中なので、マージをさらに優先
            if reactive_pairs >= 4:
                score += 300.0
                reasons.append("REACTOR_CHAIN")

        else:
            # --- マージがない場合：高度・ドリフト・バランスを厳しく評価 ---

            # 1. マージなしペナルティ（フェーズに応じて強化）
            no_merge_penalty = 200.0
            if phase == "HIGH":
                no_merge_penalty *= 1.6  # v10: 1.5→1.6に微増
            elif phase == "MEDIUM":
                no_merge_penalty *= 1.5
            score -= no_merge_penalty

            # 2. 高度によるスコア（厳しく）
            height_penalty = landing_y * 60.0 * height_mult

            # 高盤面での追加ペナルティ
            if phase == "HIGH":
                height_penalty *= 1.5
                reasons.append("HIGH_TOWER")
            elif phase == "MEDIUM" and landing_y > 0.5:
                height_penalty *= 1.3
                reasons.append("MEDIUM_TOWER")
            elif landing_y > 0.0:
                reasons.append("HIGH_LAYER")

            score -= height_penalty

            # 3. ドリフトによるペナルティ（厳しく）
            drift_penalty = (abs(drift_x) + drift_unc) * 35.0  # v10: 30→35に強化
            score -= drift_penalty

            # 4. 左右バランス補正（フェーズに応じて強化）
            balance_strength = 20.0
            if phase == "HIGH":
                balance_strength = 40.0  # v10: 35→40に強化
            elif phase == "MEDIUM":
                balance_strength = 25.0

            balance_penalty = x * balance_bias * balance_strength
            score -= abs(balance_penalty)

        # 5. nextNextが同じタイプなら中央寄せボーナス（共通）
        if next_next_type == next_type:
            center_bonus = max(0, 1.0 - abs(x) / 2.0) * 40.0
            score += center_bonus
            reasons.append("NEXT_SAME")

        # スコア更新
        if score > best_score:
            best_score = score
            best_x = x
            best_reason = "_".join(reasons) if reasons else "HEIGHT_CONTROL"

    # 安全な範囲内にクリップ
    best_x = max(-3.0, min(3.0, best_x))
    best_x = round(best_x, 2)

    return {"x": best_x, "reason": best_reason}


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
