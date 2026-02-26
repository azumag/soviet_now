#!/usr/bin/env python3
"""strategy.py - AI改善対象の決定スクリプト"""

# 固定インターフェース:
# decide(game_state: dict, analysis: dict) -> dict
#    戻り値: {"x": float, "reason": str}
#
# AI改変可能: decide() 内部,ヘルパー関数,定数,import
# AI改変禁止: decide() シグネチャ,if __name__ == "__main__" ブロック

# --- 変更履歴 ---
# v39: マージ強制・ボーナス強化版 - v38の失敗（スコア912点、HIGHフェーズでマージ率0%、MEDIUMフェーズでマージ率0%）を受けて、v38のマージなしペナルティが機能していないことを特定。履歴分析でHIGH_TOWER_NO_MERGEが16回選択され、マージなし位置が支配的。マージなしペナルティを大幅に強化（HIGH: -500、MEDIUM: -300）し、マージボーナスも強化（DIRECT: 1800/NEAR: 900/FAR: 400）。v19のシンプル構造を維持しつつ、マージ強制を最大化。コード量約120行で更にシンプル化。
# v40: 振り子解消・reactor活用版 - v39の失敗（スコア1305点、HIGHフェーズでマージ率0%）を受けて、マージなしペナルティの振り子パターン（v30導入→v31削除→v39再導入）を特定。HIGHフェーズではマージ機会自体が極端に限られており、ペナルティを強化してもマージを強制できず、単にスコアを下げるだけ。マージなしペナルティを**完全削除**し、v19の成功パラメータ（DIRECT=1200/NEAR=600/FAR=200）に戻す。v31のreactor活用のアイデを採用しつつ、条件をreactive_pairs>=3から>=2に緩和。chain reaction時にheight_multiplierを35.0に緩和し、height_penalty_factorも0.8に緩和。v19のシンプル構造を維持しつつ、コード量約100行で更にシンプル化。
# v41: chain reaction検出緩和・v31成功要素復活版 - v40の失敗（スコア1540点、マージ率0%、スコア停滞）を受けて、履歴分析で全ターンでTOWERペナルティ（MEDIUM_TOWER: 3, HIGH_TOWER: 1）が発動、マージ関連の理由が1つもないを特定。reactor_reactive_pairsが全ターンで0、chain reaction未発生、v40の検出条件（reactive_pairs >= 2）が厳しすぎることが原因。v31の成功要素（has_merge時のdrift_penalty緩和0.5）が削除されていることも問題。chain reaction検出条件を>=2から>=1に緩和し、height_multiplierを30.0にさらに緩和、has_merge時のdrift_penalty緩和（0.5）を再導入、height_penalty_factorも0.8から0.7に緩和。v19のシンプル構造を維持しつつ、マージ機会確保を最大化。コード量約95行で更にシンプル化。


def decide(game_state: dict, analysis: dict) -> dict:
    """v31の成功要素を復活しつつ、chain reaction検出条件を現実的な値に緩和"""

    results = analysis.get("results", [])

    if not results:
        return {"x": 0.0, "reason": "no analysis data"}

    best_x = 0.0
    best_score = -float("inf")
    best_reason = ""

    # 盤面情報
    pieces = game_state.get("pieces", [])
    max_y = max([p["y"] for p in pieces]) if pieces else -4.0

    # reactor情報（v41: v31の成功要素をベースに検出条件を緩和）
    reactor = analysis.get("reactor", {})
    reactive_pairs_raw = reactor.get("reactive_pairs", 0)
    reactive_pairs = (
        len(reactive_pairs_raw)
        if isinstance(reactive_pairs_raw, list)
        else reactive_pairs_raw
    )

    # フェーズ判定（v41: v19の閾値0.8/1.8/3.0を維持）
    if max_y < 0.8:
        phase = "LOW"
        height_mult = 1.0
        merge_mult = 1.2
    elif max_y < 1.8:
        phase = "MEDIUM"
        height_mult = 2.4  # v41: v19の2.4を維持（HIGH到達遅延）
        merge_mult = 1.0
    elif max_y < 3.0:
        phase = "HIGH"
        height_mult = 2.6  # v41: v19の2.6を維持（マージ機会確保）
        merge_mult = 1.0
    else:
        phase = "CRITICAL"
        height_mult = 1.0  # CRITICAL: height_multなし
        merge_mult = 0.6  # v41: v19の0.6を維持（マージ優先）

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

        # === v41: chain reaction検出緩和・v31成功要素復活戦略 ===

        # 1. マージグレードによるスコア（v41: v19の成功値を維持）
        if merge_grade == "DIRECT":
            score += 1200.0 * merge_mult  # v41: v19の1200を維持
            reasons.append("DIRECT_MERGE")
        elif merge_grade == "NEAR":
            score += 600.0 * merge_mult  # v41: v19の600を維持
            reasons.append("NEAR_MERGE")
        elif merge_grade == "FAR":
            score += 200.0 * merge_mult  # v41: v19の200を維持
            reasons.append("FAR_MERGE")

        # 2. 高度によるスコア（v41: chain reaction時にさらに緩和）
        if phase == "CRITICAL":
            # CRITICALフェーズではheight_multiplier強化（v19の40.0を維持）
            height_multiplier = 40.0
            height_penalty = landing_y * height_multiplier
            if landing_y > 1.0:
                reasons.append("CRITICAL_HEIGHT")
        else:
            # v41: chain reaction時（reactive_pairs >= 1）に大幅緩和（v40の>=2から緩和）
            height_penalty_factor = 1.0
            height_multiplier = 50.0

            if reactive_pairs >= 1:
                # chain reaction中は高度管理を大幅に緩和（v40の35.0から30.0にさらに緩和）
                height_multiplier = 30.0
                height_penalty_factor = 0.7  # v41: v40の0.8から0.7に緩和

            height_penalty = (
                landing_y * height_mult * height_multiplier * height_penalty_factor
            )

            # 高盤面での追加ペナルティ（CRITICALフェーズでは適用しない）
            if phase == "HIGH" and landing_y > 0.5:
                height_penalty *= 2.0  # v41: v19の2.0を維持
                reasons.append("HIGH_TOWER")
            elif phase == "MEDIUM" and landing_y > 0.5:
                height_penalty *= 1.5  # v41: v19の1.5を維持
                reasons.append("MEDIUM_TOWER")
            elif landing_y > 0.0:
                reasons.append("HIGH_LAYER")

        score -= height_penalty

        # 3. ドリフトによるペナルティ（v41: v31のhas_merge時緩和を再導入）
        drift_penalty_factor = 1.0
        if phase == "HIGH" and has_merge:
            drift_penalty_factor = 0.5  # v41: v31の成功値を再導入

        if phase == "HIGH":
            drift_penalty = (abs(drift_x) + drift_unc) * 35.0 * drift_penalty_factor
        elif phase == "MEDIUM":
            drift_penalty = (abs(drift_x) + drift_unc) * 35.0  # v41: v19の35.0を維持
        else:  # LOW, CRITICAL
            drift_penalty = (abs(drift_x) + drift_unc) * 30.0
        score -= drift_penalty

        # 4. 左右バランス補正（v41: v19の値を維持）
        balance_strength = 20.0
        if phase == "HIGH":
            balance_strength = 40.0  # v41: v19の40.0を維持
        elif phase == "MEDIUM":
            balance_strength = 30.0  # v41: v19の30.0を維持
        # CRITICALフェーズではバランス補正緩和（マージ優先）

        left_count = sum(1 for p in pieces if p["x"] < 0)
        right_count = len(pieces) - left_count
        balance_bias = (right_count - left_count) / (len(pieces) if pieces else 1)

        balance_penalty = x * balance_bias * balance_strength
        score -= abs(balance_penalty)

        # 5. nextNextが同じタイプなら中央寄せボーナス（v41: v19の設定を維持）
        if next_next_type == next_type:
            if phase == "CRITICAL":
                center_bonus = max(0, 1.0 - abs(x) / 2.0) * 60.0  # v41: v19の成功値
            else:
                center_bonus = max(0, 1.0 - abs(x) / 2.0) * 50.0  # v41: v19の成功値
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
