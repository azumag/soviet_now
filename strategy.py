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
# v34: CHAIN_REACTION強化・フェーズ調整版 - v33のCHAIN_REACTIONボーナス（300点）が弱すぎるのを修正。NEAR_MERGE_CHAIN_REACTION: +600点、DIRECT_MERGE_CHAIN_REACTION: +1200点。MEDIUM_TOWERペナルティを1.5倍→1.2倍、HIGH_TOWERペナルティを2.0倍→1.5倍で高盤面でもドロップする機会を確保。CRITICALフェーズの定義をmax_y>2.5に変更（より早い段階でマージ優先）。HEIGHT_CONTROLでマージがない場合-100点ペナルティでマージを強制。reactorのpipeline健全性チェックを追加
# v35: chain reactionスコアリング修正版 - v34の失敗（スコア586、chain reaction 7回全てマージ失敗）を受けて、CHAIN_REACTIONボーナス追加ではなくheight_multiplierを動的調整。v19の成功構造に戻しつつ（フェーズ閾値0.8/1.8/3.0）、chain reaction（reactive_pairs >= 3）時にheight_multiplierを20.0に大幅緩和、reactive_pairs >= 4ならさらに10.0に緩和。v34の不要なロジックを削除（CHAIN_REACTIONボーナス追加、HEIGHT_CONTROLペナルティ追加、CRITICAL閾値変更を全て削除）。v19の成功パラメータに復活（height_mult 1.0/2.4/2.6/1.0、merge_mult 1.2/1.0/1.0/0.6、マージボーナス 1200/600/200）


def decide(game_state: dict, analysis: dict) -> dict:
    """v19の成功構造をベースに、chain reaction時にheight_multiplierを動的に調整"""

    results = analysis.get("results", [])

    if not results:
        return {"x": 0.0, "reason": "no analysis data"}

    best_x = 0.0
    best_score = -float("inf")
    best_reason = ""

    # 盤面情報
    pieces = game_state.get("pieces", [])
    max_y = max([p["y"] for p in pieces]) if pieces else -4.0

    # reactor情報（v35: chain reaction判定に使用）
    reactor = analysis.get("reactor", {})
    reactive_pairs_raw = reactor.get("reactive_pairs", 0)
    reactive_pairs = (
        len(reactive_pairs_raw)
        if isinstance(reactive_pairs_raw, list)
        else reactive_pairs_raw
    )

    # フェーズ判定（v35: v19の成功閾値0.8/1.8/3.0に復活）
    if max_y < 0.8:
        phase = "LOW"
        height_mult = 1.0
        merge_mult = 1.2
    elif max_y < 1.8:
        phase = "MEDIUM"
        height_mult = 2.4  # v35: v19の2.4を維持
        merge_mult = 1.0
    elif max_y < 3.0:
        phase = "HIGH"
        height_mult = 2.6  # v35: v19の2.6を維持
        merge_mult = 1.0
    else:
        phase = "CRITICAL"
        height_mult = 1.0  # v35: v19の1.0を維持
        merge_mult = 0.6  # v35: v19の0.6を維持

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

        # 1. マージグレードによるスコア（v35: v19の成功値）
        if merge_grade == "DIRECT":
            score += 1200.0 * merge_mult  # v35: v19の1200を維持
            reasons.append("DIRECT_MERGE")
        elif merge_grade == "NEAR":
            score += 600.0 * merge_mult  # v35: v19の600を維持
            reasons.append("NEAR_MERGE")
        elif merge_grade == "FAR":
            score += 200.0 * merge_mult  # v35: v19の200を維持
            reasons.append("FAR_MERGE")

        # 2. 高度によるスコア（v35: chain reaction時に動的に緩和）
        if phase == "CRITICAL":
            # CRITICALフェーズではheight_multiplier強化（v19の40.0を維持）
            height_multiplier = 40.0
            height_penalty = landing_y * height_multiplier
            if landing_y > 1.0:
                reasons.append("CRITICAL_HEIGHT")
        else:
            # v35: chain reaction時にheight_multiplierを大幅緩和
            height_multiplier = 50.0  # v35: v19の50.0を維持
            if phase == "HIGH" and reactive_pairs >= 4:
                # chain reaction進行中（reactive_pairs >= 4）: 大幅緩和
                height_multiplier = 10.0
            elif phase == "HIGH" and reactive_pairs >= 3:
                # chain reaction開始（reactive_pairs >= 3）: 緩和
                height_multiplier = 20.0
            elif phase == "HIGH" and has_merge:
                # HIGHフェーズでマージがある場合: 微緩和
                height_multiplier = 40.0

            height_penalty = landing_y * height_mult * height_multiplier

            # 高盤面での追加ペナルティ（v35: v19の成功値）
            if phase == "HIGH" and landing_y > 0.5:
                height_penalty *= 2.0  # v35: v19の2.0を維持
                reasons.append("HIGH_TOWER")
            elif phase == "MEDIUM" and landing_y > 0.5:
                height_penalty *= 1.5  # v35: v19の1.5を維持
                reasons.append("MEDIUM_TOWER")
            elif landing_y > 0.0:
                reasons.append("HIGH_LAYER")

        score -= height_penalty

        # 3. ドリフトによるペナルティ（v35: v19の成功値）
        if phase == "HIGH":
            drift_penalty = (abs(drift_x) + drift_unc) * 35.0  # v35: v19の35.0を維持
        elif phase == "MEDIUM":
            drift_penalty = (abs(drift_x) + drift_unc) * 35.0  # v35: v19の35.0を維持
        else:  # LOW, CRITICAL
            drift_penalty = (abs(drift_x) + drift_unc) * 30.0
        score -= drift_penalty

        # 4. 左右バランス補正（v35: v19の成功値）
        balance_strength = 20.0
        if phase == "HIGH":
            balance_strength = 40.0  # v35: v19の40.0を維持
        elif phase == "MEDIUM":
            balance_strength = 30.0  # v35: v19の30.0を維持
        # CRITICALフェーズではバランス補正緩和（マージ優先）

        left_count = sum(1 for p in pieces if p["x"] < 0)
        right_count = len(pieces) - left_count
        balance_bias = (right_count - left_count) / (len(pieces) if pieces else 1)

        balance_penalty = x * balance_bias * balance_strength
        score -= abs(balance_penalty)

        # 5. nextNextが同じタイプなら中央寄せボーナス（v35: v19の設定を維持）
        if next_next_type == next_type:
            if phase == "CRITICAL":
                center_bonus = (
                    max(0, 1.0 - abs(x) / 2.0) * 60.0
                )  # v35: v19のCRITICAL強化
            else:
                center_bonus = max(0, 1.0 - abs(x) / 2.0) * 50.0  # v35: v19の50.0を維持
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
