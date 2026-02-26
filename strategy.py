#!/usr/bin/env python3
"""strategy.py - AI改善対象の決定スクリプト"""

# 固定インターフェース:
# decide(game_state: dict, analysis: dict) -> dict
#    戻り値: {"x": float, "reason": str}
#
# AI改変可能: decide() 内部,ヘルパー関数,定数,import
# AI改変禁止: decide() シグネチャ,if __name__ == "__main__" ブロック

# --- 変更履歴 ---
# v36: v19復活・シンプル化版 - v35の失敗（chain reaction活用による複雑化、スコア1666点）を受けて、reactive_pairs条件分岐を完全に削除し、v19の成功構造に復活。v19（2325点）のシンプルかつ堅実な戦略を踏襲しつつ、不要なロジックを削除してコードを簡素化。
# v37: HIGHフェーズマージ優先版 - v36の失敗（スコア1072点、HIGHフェーズでマージ率37%）を受けて、HIGHフェーズでのマージ機会確保戦略を導入。履歴分析でHIGHフェーズ（16ターン）でHIGH_TOWER決定が10回を特定、高度管理が優先されすぎている。v19のシンプル構造を維持しつつ、HIGHフェーズでhas_mergeがある場合、height_penaltyを60%に緩和し、drift_penaltyも70%に緩和（v29/v30の成功要素を改良）。v19のマージボーナス（DIRECT=1200/NEAR=600/FAR=200）とheight_mult設定を維持。コード量約135行でシンプル化。
# v38: マージ率向上・シンプル化版 - v37の失敗（スコア938点、MEDIUMフェーズでマージ率5.3%、HIGHフェーズでマージ率16.7%）を受けて、v37のhas_merge条件分岐を削除し、v19のシンプル構造に復活。履歴分析でhas_merge=Trueのケースがほとんどないを特定、v37の複雑な条件分岐は効果なし。マージボーナスを強化（DIRECT=1500/NEAR=800/FAR=300）し、MEDIUMフェーズでマージなし位置に-100ペナルティ、HIGHフェーズでマージなし位置に-200ペナルティを追加（v30のNO_MERGE_OPPORTUNITYペナルティを改良）。v19のフェーズ構造（LOW/MEDIUM/HIGH/CRITICAL）を維持しつつ、マージ率向上を最優先。コード量約125行で更にシンプル化。


def decide(game_state: dict, analysis: dict) -> dict:
    """v19のシンプル構造をベースに、マージボーナス強化とマージなしペナルティでマージ率向上"""

    results = analysis.get("results", [])

    if not results:
        return {"x": 0.0, "reason": "no analysis data"}

    best_x = 0.0
    best_score = -float("inf")
    best_reason = ""

    # 盤面情報
    pieces = game_state.get("pieces", [])
    max_y = max([p["y"] for p in pieces]) if pieces else -4.0

    # フェーズ判定（v19の閾値0.8/1.8/3.0を維持）
    if max_y < 0.8:
        phase = "LOW"
        height_mult = 1.0
        merge_mult = 1.2
    elif max_y < 1.8:
        phase = "MEDIUM"
        height_mult = 2.4  # v19: HIGH到達遅延
        merge_mult = 1.0
    elif max_y < 3.0:
        phase = "HIGH"
        height_mult = 2.6  # v19: マージ機会確保
        merge_mult = 1.0
    else:
        phase = "CRITICAL"
        height_mult = 1.0  # CRITICAL: height_multなし
        merge_mult = 0.6  # v19: マージ優先

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

        # 1. マージグレードによるスコア（v38: マージボーナス強化）
        if merge_grade == "DIRECT":
            score += 1500.0 * merge_mult  # v38: 1200→1500に強化
            reasons.append("DIRECT_MERGE")
        elif merge_grade == "NEAR":
            score += 800.0 * merge_mult  # v38: 600→800に強化
            reasons.append("NEAR_MERGE")
        elif merge_grade == "FAR":
            score += 300.0 * merge_mult  # v38: 200→300に強化
            reasons.append("FAR_MERGE")

        # 2. 高度によるスコア（v38: v19のシンプル構造に戻す）
        if phase == "CRITICAL":
            # CRITICALフェーズではheight_multiplier強化（v19の40.0を維持）
            height_penalty = landing_y * 40.0
            if landing_y > 1.0:
                reasons.append("CRITICAL_HEIGHT")
        else:
            height_penalty = landing_y * 50.0 * height_mult

            # 高盤面での追加ペナルティ（CRITICALフェーズでは適用しない）
            if phase == "HIGH" and landing_y > 0.5:
                height_penalty *= 2.0  # v19の成功値
                reasons.append("HIGH_TOWER")
            elif phase == "MEDIUM" and landing_y > 0.5:
                height_penalty *= 1.5  # v19の成功値
                reasons.append("MEDIUM_TOWER")
            elif landing_y > 0.0:
                reasons.append("HIGH_LAYER")

        score -= height_penalty

        # 3. ドリフトによるペナルティ（v38: v19の値を維持）
        if phase == "HIGH":
            drift_penalty = (abs(drift_x) + drift_unc) * 40.0  # v19の成功値
        elif phase == "MEDIUM":
            drift_penalty = (abs(drift_x) + drift_unc) * 35.0  # v19の成功値
        else:  # LOW, CRITICAL
            drift_penalty = (abs(drift_x) + drift_unc) * 30.0
        score -= drift_penalty

        # 4. 左右バランス補正（v19の成功値）
        balance_strength = 20.0
        if phase == "HIGH":
            balance_strength = 40.0  # v19の成功値
        elif phase == "MEDIUM":
            balance_strength = 30.0  # v19の成功値
        # CRITICALフェーズではバランス補正緩和（マージ優先）

        left_count = sum(1 for p in pieces if p["x"] < 0)
        right_count = len(pieces) - left_count
        balance_bias = (right_count - left_count) / (len(pieces) if pieces else 1)

        balance_penalty = x * balance_bias * balance_strength
        score -= abs(balance_penalty)

        # 5. nextNextが同じタイプなら中央寄せボーナス（v19の成功値）
        if next_next_type == next_type:
            if phase == "CRITICAL":
                center_bonus = max(0, 1.0 - abs(x) / 2.0) * 60.0  # v19の成功値
            else:
                center_bonus = max(0, 1.0 - abs(x) / 2.0) * 50.0  # v19の成功値
            score += center_bonus
            reasons.append("NEXT_SAME")

        # 6. v38: マージなしペナルティ（MEDIUM/HIGHフェーズでマージを強制）
        if not has_merge:
            if phase == "HIGH":
                score -= 200.0  # HIGHフェーズでマージを強制
                if "NO_MERGE" not in reasons:
                    reasons.append("NO_MERGE")
            elif phase == "MEDIUM":
                score -= 100.0  # MEDIUMフェーズでマージを促進
                if "NO_MERGE" not in reasons:
                    reasons.append("NO_MERGE")

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
