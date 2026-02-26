#!/usr/bin/bin/env python3
"""strategy.py - AI kaizen target no kettei script"""

# Kotei interface
# decide(game_state: dict, analysis: dict) -> dict
#    modori chi: {"x": float, "reason": str}
#
# AI kaizen kanou: decide() naibu, herupakansuu, teisuu, import
# AI kaizen kinshi: decide() signecha, if __name__ == "__main__" burokku

# --- Henkou rireki ---
# [BEST:2325] v19: CRITICAL phase do-nyuu ban
# [BEST:2335] v42: v19 fukkatsu
# v50-v64: has_merge/reactive_pairs条件の振り子パターンと閾値シャッフル - 複数回の追加・削除・再追加を繰り返したが、どれも失敗。v64ではv12の「緩い高度管理」を採用したが、HIGHフェーズでマージ機会を大幅に逃した（87ターン中13ターンのみ）。HEIGHT_CONTROLが32%を占め、マージ優先が崩れた。
# v65: v42完全復活版 - 振り子パターンの破壊のため、v42のシンプルな成功構造を完全復活。height_mult: MEDIUM=2.4/HIGH=2.6（v42の成功値）、HIGH height_penalty=2.0、MEDIUM height_penalty=1.5。CRITICALフェーズではマージ絶対優先（balance_strength緩和）。複雑な条件分岐（has_merge、reactive_pairs、NO_MERGE_PENALTI）は完全排除。コード量約110行でv42の頑健な構造を維持。


def decide(game_state: dict, analysis: dict) -> dict:
    """v42のシンプルかつ頑健な構造を完全復活"""

    results = analysis.get("results", [])

    if not results:
        return {"x": 0.0, "reason": "no analysis data"}

    best_x = 0.0
    best_score = -float("inf")
    best_reason = ""

    # 盤面情報
    pieces = game_state.get("pieces", [])
    max_y = max([p["y"] for p in pieces]) if pieces else -4.0

    # フェーズ判定（v42の設定を完全復活）
    if max_y < 0.8:
        phase = "LOW"
        height_mult = 1.0
        merge_mult = 1.2
    elif max_y < 1.8:
        phase = "MEDIUM"
        height_mult = 2.4  # v42: MEDIUM phase height_mult
        merge_mult = 1.0
    elif max_y < 3.0:
        phase = "HIGH"
        height_mult = 2.6  # v42: HIGH phase height_mult
        merge_mult = 1.0
    else:
        phase = "CRITICAL"
        height_mult = 1.0  # CRITICAL: height_multなし
        merge_mult = 0.6  # v42: CRITICALでマージボーナス強調

    # 次のピース情報
    next_piece = game_state.get("next", {})
    next_next_piece = game_state.get("nextNext", {})
    next_type = next_piece.get("type", 0)
    next_next_type = next_next_piece.get("type", 0)

    for result in results:
        x = result["x"]
        landing_y = result.get("landing_y", 0)
        drift_x = result.get("drift_x", 0)
        drift_unc = result.get("drift_unc", 0)
        merge_grade = result.get("merge_grade", "NO")

        score = 0.0
        reasons = []

        # 1. マージグレードによるスコア（v42の設定を完全復活）
        if merge_grade == "DIRECT":
            score += 1200.0 * merge_mult  # v42の強力なボーナス
            reasons.append("DIRECT_MERGE")
        elif merge_grade == "NEAR":
            score += 600.0 * merge_mult  # v42の強力なボーナス
            reasons.append("NEAR_MERGE")
        elif merge_grade == "FAR":
            score += 200.0 * merge_mult  # v42の強力なボーナス
            reasons.append("FAR_MERGE")

        # 2. 高度によるペナルティ（v42の設定を完全復活）
        if phase == "CRITICAL":
            # CRITICALフェーズではheight_penaltyシンプル化（chain reaction狙い）
            height_penalty = landing_y * 40.0
            if landing_y > 1.0:
                reasons.append("CRITICAL_HEIGHT")
        else:
            height_penalty = landing_y * 50.0 * height_mult

            # 高盤面での追加ペナルティ（v42の設定）
            if phase == "HIGH" and landing_y > 0.5:
                height_penalty *= 2.0  # v42: HIGHフェーズ強化
                reasons.append("HIGH_TOWER")
            elif phase == "MEDIUM" and landing_y > 0.5:
                height_penalty *= 1.5  # v42: MEDIUMフェーズ
                reasons.append("MEDIUM_TOWER")
            elif landing_y > 0.0:
                reasons.append("HIGH_LAYER")

        score -= height_penalty

        # 3. ドリフトによるペナルティ（v42の設定）
        if phase == "HIGH":
            drift_penalty = (abs(drift_x) + drift_unc) * 40.0  # v42: HIGHフェーズ強化
        elif phase == "MEDIUM":
            drift_penalty = (abs(drift_x) + drift_unc) * 35.0  # v42: MEDIUMフェーズ
        else:  # LOW, CRITICAL
            drift_penalty = (abs(drift_x) + drift_unc) * 30.0
        score -= drift_penalty

        # 4. 左右バランス補正（v42の設定）
        balance_strength = 20.0
        if phase == "HIGH":
            balance_strength = 40.0  # v42: HIGHフェーズ強化
        elif phase == "MEDIUM":
            balance_strength = 30.0  # v42: MEDIUMフェーズ
        # CRITICALフェーズではバランス補正緩和（マージ優先）

        left_count = sum(1 for p in pieces if p["x"] < 0)
        right_count = len(pieces) - left_count
        balance_bias = (right_count - left_count) / (len(pieces) if pieces else 1)

        balance_penalty = x * balance_bias * balance_strength
        score -= abs(balance_penalty)

        # 5. nextNextが同じタイプなら中央寄せボーナス（v42の設定）
        if next_next_type == next_type:
            if phase == "CRITICAL":
                center_bonus = (
                    max(0, 1.0 - abs(x) / 2.0) * 60.0
                )  # v42: CRITICALフェーズ強化
            else:
                center_bonus = max(0, 1.0 - abs(x) / 2.0) * 50.0  # v42の基本値
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


# --- AI kaizen kinshi zon ---
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
