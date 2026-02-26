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
# [BEST:2185] v12: 一貫性重視・シンプル化版 - 二段階スコアリング廃止、v8の構造に戻しつつマージボーナス強化、HIGHフェーズ高度管理緩和、左右バランス計算簡素化
# [BEST:2325] v19: CRITICALフェーズ導入版 - HIGHフェーズのheight_mult過剰を修正、CRITICALフェーズ（max_y>3.0）を新設。CRITICALではマージ絶対優先（merge_mult=0.6、height_multなし、height_penaltyシンプル化）。MEDIUMフェーズheight_mult微増（2.2→2.4）でHIGH到達遅延、HIGHフェーズheight_mult微減（2.8→2.6）でマージ機会確保
# v22: HIGHフェーズ改善版 - v19の振り子パターン（CRITICALあり/なしの繰り返し）を解消。CRITICALフェーズを廃止し、HIGHフェーズでマージを強化。v19のHIGHフェーズ（merge_mult=1.0, height_mult=2.6）をベースに、マージボーナス強化（merge_mult=1.2）と高度管理緩和（height_mult=2.0）を実現。マージなしペナルティ完全削除、シンプルな3フェーズ構造に統一
# v23: v19構造復活・CRITICALフェーズ閾値修正版 - v22のCRITICALフェーズ廃止は失敗（スコア1442 vs v19の2325）。v19の成功構造を復活し、CRITICALフェーズ（閾値3.0）を再導入。v22のマージボーナス強化（HIGH: merge_mult=1.2）とMEDIUMフェーズ高度管理緩和（height_mult=1.6）を採用。CRITICALでheight_multiplierを50.0→40.0に緩和し、chain reaction機会を最大化
# v24: v19完全復活版 - v23の「v22の要素」（HIGHフェーズmerge_mult=1.2強化、MEDIUMフェーズheight_mult=1.6緩和）が失敗。v19のHIGHフェーズ設定（height_mult=2.6, merge_mult=1.0）を完全復活。MEDIUMフェーズheight_multをv19の2.4に戻し、HIGH到達を遅らせてマージ機会を最大化


def decide(game_state: dict, analysis: dict) -> dict:
    """v19の成功構造を完全復活し、HIGHフェーズでchain reactionを最大化"""

    results = analysis.get("results", [])

    if not results:
        return {"x": 0.0, "reason": "no analysis data"}

    best_x = 0.0
    best_score = -float("inf")
    best_reason = ""

    # 盤面情報
    pieces = game_state.get("pieces", [])
    max_y = max([p["y"] for p in pieces]) if pieces else -4.0

    # フェーズ判定（v24: v19の閾値3.0を完全復活）
    if max_y < 0.8:
        phase = "LOW"
        height_mult = 1.0
        merge_mult = 1.2
    elif max_y < 1.8:
        phase = "MEDIUM"
        height_mult = 2.4  # v24: v19の2.4に復活（HIGH到達遅延）
        merge_mult = 1.0
    elif max_y < 3.0:
        phase = "HIGH"
        height_mult = 2.6  # v24: v19の2.6を復活（盤面の起伏確保）
        merge_mult = 1.0  # v24: v19の1.0に復活（マージ重視）
    else:
        phase = "CRITICAL"  # v24: v19のCRITICALフェーズ維持
        height_mult = 1.0  # CRITICAL: height_multなし
        merge_mult = 1.0  # v24: v19の0.6ではなく、v22/v23の1.0に統一

    # 左右バランス計算（簡素化：カウントベース）
    left_count = sum(1 for p in pieces if p["x"] < 0)
    right_count = len(pieces) - left_count
    balance_bias = (right_count - left_count) / (len(pieces) if pieces else 1)

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
        has_merge = result.get("has_merge", False)

        score = 0.0
        reasons = []

        # 1. マージグレードによるスコア（v24: v19の値を復活）
        if merge_grade == "DIRECT":
            score += 1200.0 * merge_mult  # v24: v19の1200を維持
            reasons.append("DIRECT_MERGE")
        elif merge_grade == "NEAR":
            score += 600.0 * merge_mult  # v24: v19の600を維持
            reasons.append("NEAR_MERGE")
        elif merge_grade == "FAR":
            score += 200.0 * merge_mult  # v24: v19の200を維持
            reasons.append("FAR_MERGE")
        # v24: マージなしペナルティ完全削除（v19/v22/v23維持）

        # 2. 高度によるスコア（v24: v19の設定を完全復活）
        if phase == "CRITICAL":
            # CRITICALフェーズではheight_multiplier緩和（chain reaction狙い）
            height_multiplier = 40.0  # v23維持
            height_penalty = landing_y * height_multiplier
            if landing_y > 1.0:
                reasons.append("CRITICAL_HEIGHT")
        else:
            height_penalty = landing_y * 50.0 * height_mult

            # 高盤面での追加ペナルティ（CRITICALフェーズでは適用しない）
            if phase == "HIGH" and landing_y > 0.5:
                height_penalty *= 2.0  # v24: v19の2.0を復活
                reasons.append("HIGH_TOWER")
            elif phase == "MEDIUM" and landing_y > 0.5:
                height_penalty *= 1.5  # v24: v19の1.5を復活
                reasons.append("MEDIUM_TOWER")
            elif landing_y > 0.0:
                reasons.append("HIGH_LAYER")

        score -= height_penalty

        # 3. ドリフトによるペナルティ（v24: v19の設定を完全復活）
        if phase == "HIGH":
            drift_penalty = (abs(drift_x) + drift_unc) * 40.0  # v24: v19の値を維持
        elif phase == "MEDIUM":
            drift_penalty = (abs(drift_x) + drift_unc) * 35.0  # v24: v19の値を維持
        else:  # LOW, CRITICAL
            drift_penalty = (abs(drift_x) + drift_unc) * 30.0
        score -= drift_penalty

        # 4. 左右バランス補正（v24: v19の設定を完全復活）
        balance_strength = 20.0
        if phase == "HIGH":
            balance_strength = 40.0  # v24: v19の値を維持
        elif phase == "MEDIUM":
            balance_strength = 30.0  # v24: v19の値を維持
        # CRITICALフェーズではバランス補正緩和（マージ優先）

        balance_penalty = x * balance_bias * balance_strength
        score -= abs(balance_penalty)

        # 5. nextNextが同じタイプなら中央寄せボーナス（v24: v19の設定を完全復活）
        if next_next_type == next_type:
            if phase == "CRITICAL":
                center_bonus = (
                    max(0, 1.0 - abs(x) / 2.0) * 60.0
                )  # v24: v19のCRITICAL強化
            else:
                center_bonus = max(0, 1.0 - abs(x) / 2.0) * 50.0  # v24: v19の値を維持
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
