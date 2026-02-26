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
# v31: chain reactionマージ最大化版 - v30の失敗（スコア1213、NO_MERGE_OPPORTUNITYペナルティ効果なし）を受けて、ペナルティ追加ではなくreactor情報活用でマージ最大化。NO_MERGE_OPPORTUNITYペナルティ削除（履歴で効果を確認）。HIGHフェーズでreactor_reactive_pairs >= 3の時、height_multiplierを35.0に大幅緩和し、height_penalty_factorも0.6に緩和（chain reaction時に高度管理を緩和してマージ最大化）。height_multはv19の2.6を維持、マージボーナスもv19の値を維持。マージあり時のdrift_penalty緩和を0.7→0.5に強化。コード量削減（168行→約140行）でv19のシンプル構造を維持
# v32: chain reaction発動頻度向上版 - v31の失敗（スコア1376、v19の2325を大幅下回る）を受けて、chain reaction高度管理緩和の発動頻度を向上。履歴分析でreactive_pairs >= 3の条件が厳しすぎてchain reaction高度管理緩和が実質的に機能していないことを特定。reactive_pairsの閾値を3→2に変更し、chain reaction発動頻度を向上。v30のNO_MERGE_OPPORTUNITYペナルティ（-200）を再導入し、マージを強制的に選択させる。v31のchain reaction時の緩和設定（height_multiplier=35.0、height_penalty_factor=0.6、drift_penalty_factor=0.5）は維持
# v33: 振り子解消・chain reactionボーナス版 - v32の失敗（スコア483）を受けて、振り子パターン（NO_MERGE_OPPORTUNITYペナルティの追加/削除/再追加）を完全解消。v32のchain reaction高度管理緩和も削除（効果不明確）。v19の成功構造に戻しつつ、reactor情報をシンプルに活用（chain reactionボーナス導入）。HIGHフェーズでreactive_pairs >= 3の時、マージボーナスを50%強化してchain reaction促進。コード量削減（約140行→約130行）。v19のCRITICALフェーズ、フェーズ閾値0.8/1.8/3.0、マージボーナス（DIRECT=1200/NEAR=600/FAR=200）を維持


def decide(game_state: dict, analysis: dict) -> dict:
    """v19の成功構造をベースに、reactor情報をシンプルに活用してchain reactionを促進"""

    results = analysis.get("results", [])

    if not results:
        return {"x": 0.0, "reason": "no analysis data"}

    best_x = 0.0
    best_score = -float("inf")
    best_reason = ""

    # 盤面情報
    pieces = game_state.get("pieces", [])
    max_y = max([p["y"] for p in pieces]) if pieces else -4.0

    # reactor情報（v33: chain reactionボーナスで活用）
    reactor = analysis.get("reactor", {})
    reactive_pairs_raw = reactor.get("reactive_pairs", 0)
    reactive_pairs = (
        len(reactive_pairs_raw)
        if isinstance(reactive_pairs_raw, list)
        else reactive_pairs_raw
    )

    # フェーズ判定（v33: v19の閾値0.8/1.8/3.0を維持）
    if max_y < 0.8:
        phase = "LOW"
        height_mult = 1.0
        merge_mult = 1.2
    elif max_y < 1.8:
        phase = "MEDIUM"
        height_mult = 2.4  # v33: v19の2.4を維持（HIGH到達遅延）
        merge_mult = 1.0
    elif max_y < 3.0:
        phase = "HIGH"
        height_mult = 2.6  # v33: v19の2.6を維持（マージ機会確保）
        merge_mult = 1.0
    else:
        phase = "CRITICAL"
        height_mult = 1.0  # CRITICAL: height_multなし
        merge_mult = 0.6  # v33: v19の0.6を維持（マージ優先）

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

        # === v33: マージ優先・chain reactionボーナス戦略 ===

        # 1. マージグレードによるスコア（v33: v19の強力な値を維持）
        if merge_grade == "DIRECT":
            score += 1200.0 * merge_mult  # v33: v19の1200を維持
            reasons.append("DIRECT_MERGE")
        elif merge_grade == "NEAR":
            score += 600.0 * merge_mult  # v33: v19の600を維持
            reasons.append("NEAR_MERGE")
        elif merge_grade == "FAR":
            score += 200.0 * merge_mult  # v33: v19の200を維持
            reasons.append("FAR_MERGE")

        # 2. chain reactionボーナス（v33新規: HIGHフェーズでreactive_pairs >= 3ならマージボーナス強化）
        if phase == "HIGH" and reactive_pairs >= 3 and has_merge:
            score += 300.0  # chain reactionボーナス
            reasons.append("CHAIN_REACTION")

        # 3. 高度によるスコア（v33: v19のシンプル構造に戻す）
        if phase == "CRITICAL":
            # CRITICALフェーズではheight_multiplier強化（v19の40.0を維持）
            height_multiplier = 40.0
            height_penalty = landing_y * height_multiplier
            if landing_y > 1.0:
                reasons.append("CRITICAL_HEIGHT")
        else:
            # v33: v19のシンプル構造に戻す（height_multiplierは50.0固定）
            height_penalty = landing_y * height_mult * 50.0

            # 高盤面での追加ペナルティ（CRITICALフェーズでは適用しない）
            if phase == "HIGH" and landing_y > 0.5:
                height_penalty *= 2.0  # v33: v19の2.0を維持
                reasons.append("HIGH_TOWER")
            elif phase == "MEDIUM" and landing_y > 0.5:
                height_penalty *= 1.5  # v33: v19の1.5を維持
                reasons.append("MEDIUM_TOWER")
            elif landing_y > 0.0:
                reasons.append("HIGH_LAYER")

        score -= height_penalty

        # 4. ドリフトによるペナルティ（v33: v19の値を維持）
        if phase == "HIGH":
            drift_penalty = (abs(drift_x) + drift_unc) * 35.0  # v33: v19の35.0を維持
        elif phase == "MEDIUM":
            drift_penalty = (abs(drift_x) + drift_unc) * 35.0  # v33: v19の35.0を維持
        else:  # LOW, CRITICAL
            drift_penalty = (abs(drift_x) + drift_unc) * 30.0
        score -= drift_penalty

        # 5. 左右バランス補正（v33: v19の値を維持）
        balance_strength = 20.0
        if phase == "HIGH":
            balance_strength = 40.0  # v33: v19の40.0を維持
        elif phase == "MEDIUM":
            balance_strength = 30.0  # v33: v19の30.0を維持
        # CRITICALフェーズではバランス補正緩和（マージ優先）

        # 簡素化されたバランス計算
        left_count = sum(1 for p in pieces if p["x"] < 0)
        right_count = len(pieces) - left_count
        balance_bias = (right_count - left_count) / (len(pieces) if pieces else 1)

        balance_penalty = x * balance_bias * balance_strength
        score -= abs(balance_penalty)

        # 6. nextNextが同じタイプなら中央寄せボーナス（v33: v19の設定を維持）
        if next_next_type == next_type:
            if phase == "CRITICAL":
                center_bonus = (
                    max(0, 1.0 - abs(x) / 2.0) * 60.0
                )  # v33: v19のCRITICAL強化
            else:
                center_bonus = max(0, 1.0 - abs(x) / 2.0) * 50.0  # v33: v19の値を維持
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
