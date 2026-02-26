#!/usr/bin/env python3
"""strategy.py - AI改善対象の決定スクリプト"""

# 固定インターフェース:
# decide(game_state: dict, analysis: dict) -> dict
#    戻り値: {"x": float, "reason": str}
#
# AI改変可能: decide() 内部,ヘルパー関数,定数,import
# AI改変禁止: decide() シグネチャ,if __name__ == "__main__" ブロック

# --- 変更履歴 ---
# [BEST:2325] v19: CRITICALフェーズ導入版 - HIGHフェーズのheight_mult過剰を修正、CRITICALフェーズ（max_y>3.0）を新設。CRITICALではマージ絶対優先（merge_mult=0.6、height_multなし、height_penaltyシンプル化）。MEDIUMフェーズheight_mult微増（2.2→2.4）でHIGH到達遅延、HIGHフェーズheight_mult微減（2.8→2.6）でマージ機会確保
# [BEST:2335] v42: v19復活・v31/v29複雑化要素削除版 - v41の失敗（スコア558）を受けて、v41がv31から取り入れたreactive_pairsとhas_mergeによる複雑な条件分岐を削除。v19のシンプル構造（DIRECT=1200/NEAR=600/FAR=200、height_penalty=50*height_mult、drift_penalty=30）に復活。v19のCRITICALフェーズ（merge_mult=0.6）を維持。コード量削減（約140行→約110行）で頑健性を確保
# [BEST:2335] v43: HIGHフェーズmerge促進版 - v42の失敗（スコア1861、HIGHフェーズでマージ率低迷）を改善。HIGHフェーズでhas_merge=trueの場合、height_penaltyを50%に緩和してマージ位置を選択しやすくする。v31の複雑なreactive_pairsロジックは採用せず、シンプルなhas_merge条件のみで改善。v19/v42のシンプル構造を維持しつつ、HIGHフェーズでのマージ機会確保を強化
# v45: HIGHフェーズ高度管理大幅緩和版 - v44の失敗（スコア1766、HIGHフェーズ14ターンでscore_delta=0、HEIGHT_CONTROLが78%選択されマージ機会を逃し続ける）を受けて、HIGHフェーズのheight_multiplierを50.0→30.0に大幅緩和し、マージ可能な位置を選択しやすくする。CRITICALフェーズのheight_multiplierを40.0→50.0に強化し、マージ絶対優先を徹底。MEDIUMフェーズのheight_multを2.4→2.2に微調整（HIGH到達を少し遅延）。v42のシンプル構造を維持、has_merge/reactive_pairsの複雑な条件分岐は追加しない
# v46: HIGH_TOWER追加ペナルティ削除・mergeボーナス強化版 - v45の失敗（スコア1395、HIGHフェーズ14ターンでscore_delta=0、HIGH_TOWER追加ペナルティ2.0倍がマージ機会を完全に潰している）を受けて、HIGH_TOWERの追加ペナルティを2.0倍から削除（v12の1.5倍にもしない、完全に削除）。HIGHフェーズのmerge_multを1.2→1.5に強化し、height_multiplierを30.0→20.0に緩和。MEDIUMフェーズのmerge_multを1.2→1.5に強化（HIGH到達遅延）、height_multiplierを35.0→30.0に緩和。バランス補正をv12の構造に近づけ（HIGH=35.0、MEDIUM=25.0、LOW=20.0）。ドリフトペナルティをv19の設定に戻す。v42のシンプル構造を維持
# v47: HIGHフェーズbalance_strength大幅緩和版 - v46の失敗（スコア984、HIGHフェーズ25ターンでHEIGHT_CONTROLが72%と支配的、マージ機会を逃し続ける）を受けて、HIGHフェーズのbalance_strengthを35.0→15.0に大幅緩和（左右バランス重視がマージ位置選択を阻害していると判断）。HIGHフェーズのheight_multiplierを20.0→25.0に微増（高すぎる位置へのドロップを抑制）。HIGHフェーズのcenter_bonusを50.0→40.0に微減。v42のシンプル構造（DIRECT=1200/NEAR=600/FAR=200）を維持し、複雑な条件分岐は追加しない


def decide(game_state: dict, analysis: dict) -> dict:
    """HIGHフェーズでbalance_strengthを大幅緩和し、左右バランス重視を抑制してマージ位置を選択しやすくする"""

    results = analysis.get("results", [])

    if not results:
        return {"x": 0.0, "reason": "no analysis data"}

    best_x = 0.0
    best_score = -float("inf")
    best_reason = ""

    # 盤面情報
    pieces = game_state.get("pieces", [])
    max_y = max([p["y"] for p in pieces]) if pieces else -4.0

    # フェーズ判定
    if max_y < 0.8:
        phase = "LOW"
        height_mult = 1.0
        merge_mult = 1.2
    elif max_y < 1.8:
        phase = "MEDIUM"
        height_mult = 2.4
        merge_mult = 1.5
    elif max_y < 3.0:
        phase = "HIGH"
        height_mult = 2.6
        merge_mult = 1.5
    else:
        phase = "CRITICAL"
        height_mult = 1.0
        merge_mult = 0.6

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

        # 1. マージグレードによるスコア
        if merge_grade == "DIRECT":
            score += 1200.0 * merge_mult
            reasons.append("DIRECT_MERGE")
        elif merge_grade == "NEAR":
            score += 600.0 * merge_mult
            reasons.append("NEAR_MERGE")
        elif merge_grade == "FAR":
            score += 200.0 * merge_mult
            reasons.append("FAR_MERGE")

        # 2. 高度によるペナルティ
        if phase == "CRITICAL":
            height_multiplier = 50.0
            height_penalty = landing_y * height_multiplier
            if landing_y > 1.0:
                reasons.append("CRITICAL_HEIGHT")
        else:
            if phase == "HIGH":
                height_multiplier = 25.0  # v47: v46の20.0から微増
            elif phase == "MEDIUM":
                height_multiplier = 30.0
            else:  # LOW
                height_multiplier = 50.0

            height_penalty = landing_y * height_multiplier * height_mult

            if phase == "HIGH" and landing_y > 0.5:
                reasons.append("HIGH_LAYER")  # v47: HIGH_TOWER削除を維持
            elif phase == "MEDIUM" and landing_y > 0.5:
                height_penalty *= 1.3
                reasons.append("MEDIUM_TOWER")
            elif landing_y > 0.0:
                reasons.append("HIGH_LAYER")

        score -= height_penalty

        # 3. ドリフトによるペナルティ
        if phase == "HIGH":
            drift_penalty = (abs(drift_x) + drift_unc) * 30.0
        elif phase == "MEDIUM":
            drift_penalty = (abs(drift_x) + drift_unc) * 30.0
        else:  # LOW, CRITICAL
            drift_penalty = (abs(drift_x) + drift_unc) * 30.0
        score -= drift_penalty

        # 4. 左右バランス補正
        balance_strength = 20.0
        if phase == "HIGH":
            balance_strength = 15.0  # v47: v46の35.0から大幅緩和
        elif phase == "MEDIUM":
            balance_strength = 25.0  # v47: v46の25.0を維持
        # CRITICALフェーズではバランス補正緩和（マージ優先）

        left_count = sum(1 for p in pieces if p["x"] < 0)
        right_count = len(pieces) - left_count
        balance_bias = (right_count - left_count) / (len(pieces) if pieces else 1)

        balance_penalty = x * balance_bias * balance_strength
        score -= abs(balance_penalty)

        # 5. nextNextが同じタイプなら中央寄せボーナス
        if next_next_type == next_type:
            if phase == "CRITICAL":
                center_bonus = max(0, 1.0 - abs(x) / 2.0) * 60.0
            else:
                center_bonus = (
                    max(0, 1.0 - abs(x) / 2.0) * 40.0
                )  # v47: v46の50.0から微減
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
