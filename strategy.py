#!/usr/bin/env python3
"""strategy.py - AI改善対象の決定スクリプト"""

# 固定インターフェース:
# decide(game_state: dict, analysis: dict) -> dict
#    戻り値: {"x": float, "reason": str}
#
# AI改変可能: decide() 内部,ヘルパー関数,定数,import
# AI改変禁止: decide() シグネチャ,if __name__ == "__main__" ブロック

# --- 変更履歴 ---
# [BEST:2325] v19: CRITICALフェーズ導入版
# [BEST:2335] v42: v19復活・v31/v29複雑化要素削除版
# v50-v64: has_merge/reactive_pairs条件の振り子パターンと閾値シャッフル - 複数回の追加・削除・再追加を繰り返したが、どれも失敗。v64ではv12の「緩い高度管理」を採用したが、HIGHフェーズでマージ機会を大幅に逃した（87ターン中13ターンのみ）。HEIGHT_CONTROLが32%を占め、マージ優先が崩れた。
# v69: MEDIUM/HIGHフェーズ統一マージ機会確保版 - v68の失敗（スコア968、HIGHフェーズでマージ可能ターン0/8）を受けて、履歴分析でMEDIUMフェーズ（12ターン中2ターン、16.7%）とHIGHフェーズ（8ターン中0ターン、0%）でマージ機会が不足していることを特定。v42のシンプル構造を維持しつつ、MEDIUM/HIGHフェーズの両方でマージ機会を確保するための統一戦略を導入。MEDIUMフェーズの追加ペナルティを導入しHIGH到達遅延しつつ、HIGHフェーズのマージボーナス強化（merge_mult=1.0→1.2）と高度管理緩和（height_mult=2.6→2.4、height_penalty=1.5→1.3）でマージ機会を最大化。CRITICALフェーズのheight_multiplierはv42の30.0を維持。コード量約100行でv42のシンプル構造を維持
# v70: v42完全復帰・振り子破壊版 - v69の失敗（スコア1789、HIGHフェーズでマージ可能ターン2/19=10.5%、MEDIUMフェーズでマージ可能ターン0/10=0%）を受けて、振り子パターン（v65-v69で追加ペナルティの追加・削除・再追加）を破壊。v65-v69の複雑化（MEDIUM_TOWER、HIGH_TOWERの1.3倍ペナルティ、merge_multの変動）を完全削除し、v42のシンプルかつ頑健な構造に完全復帰。height_mult: MEDIUM=2.4/HIGH=2.6（v42の成功値）、HIGH height_penalty=2.0、MEDIUM height_penalty=1.5（v42の成功値）。CRITICALフェーズのheight_multiplier=30.0（v42の成功値）、merge_mult=0.6（v42の成功値）。マージボーナス1200/600/200（v42の成功値）。追加条件分岐（has_merge、reactive_pairs、MEDIUM_TOWERの1.3倍等）は完全排除。コード量約90行でv42の成功構造を完全復活
# v71: v31のreactive_pairs活用再導入・MEDIUMフェーズ拡張版 - v70の失敗（スコア770、HEIGHT_CONTROLが33.9%で支配的、マージ機会9.7%のみ）を受けて、v31のreactive_pairs活用を再導入。v70はv42構造への信仰からv31のreactive_pairs活用を削除したが、これはスコア低下の直接原因。履歴分析でv31（スコア1376）の成功要素を特定し、v42構造に統合。HIGHフェーズでreactive_pairs >= 3の時、height_multiplierを35.0に大幅緩和（chain reaction優先）。MEDIUMフェーズでも同様の緩和を新規導入（reactive_pairs >= 2でheight_multiplier=40.0）。CRITICALフェーズではchain reactionを最優先（height_multiplierを25.0に緩和）。has_merge条件は削除し、reactive_pairsのみでchain reactionを判断。コード量約120行でv31の成功要素をv42構造に統合


def decide(game_state: dict, analysis: dict) -> dict:
    """v31のreactive_pairs活用を再導入し、MEDIUM/HIGH/CRITICALフェーズでchain reactionを最大化"""

    results = analysis.get("results", [])

    if not results:
        return {"x": 0.0, "reason": "no analysis data"}

    best_x = 0.0
    best_score = -float("inf")
    best_reason = ""

    # 盤面情報
    pieces = game_state.get("pieces", [])
    max_y = max([p["y"] for p in pieces]) if pieces else -4.0

    # reactor情報（v31から再導入）
    reactor = analysis.get("reactor", {})
    reactive_pairs_raw = reactor.get("reactive_pairs", 0)
    reactive_pairs = (
        len(reactive_pairs_raw)
        if isinstance(reactive_pairs_raw, list)
        else reactive_pairs_raw
    )

    # フェーズ判定（v42の閾値0.8/1.8/3.0を維持）
    if max_y < 0.8:
        phase = "LOW"
        height_mult = 1.0
        merge_mult = 1.0
    elif max_y < 1.8:
        phase = "MEDIUM"
        height_mult = 2.4  # v42の成功値
        merge_mult = 1.0
    elif max_y < 3.0:
        phase = "HIGH"
        height_mult = 2.6  # v42の成功値
        merge_mult = 1.0
    else:
        phase = "CRITICAL"
        height_mult = 1.0  # CRITICAL: height_multなし
        merge_mult = 0.6  # v42の成功値（chain reaction狙い）

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

        # 1. マージグレードによるスコア（v42の強力な値）
        if merge_grade == "DIRECT":
            score += 1200.0 * merge_mult  # v42の成功値
            reasons.append("DIRECT_MERGE")
        elif merge_grade == "NEAR":
            score += 600.0 * merge_mult  # v42の成功値
            reasons.append("NEAR_MERGE")
        elif merge_grade == "FAR":
            score += 200.0 * merge_mult  # v42の成功値
            reasons.append("FAR_MERGE")

        # 2. 高度によるペナルティ（v71: reactive_pairs活用でchain reaction時に緩和）
        if phase == "CRITICAL":
            # CRITICALフェーズではchain reactionを最優先
            height_multiplier = 25.0  # v71: v42の30.0から緩和（chain reaction狙い）
            height_penalty = landing_y * height_multiplier
            if landing_y > 1.0:
                reasons.append("CRITICAL_HEIGHT")
        elif phase == "HIGH":
            # v71: HIGHフェーズでreactive_pairsに応じて段階的に緩和
            if reactive_pairs >= 3:
                height_multiplier = 35.0  # chain reaction中は大幅緩和（v31の成功値）
                reasons.append("CHAIN_REACTION")
            elif reactive_pairs >= 2:
                height_multiplier = 45.0  # chain reaction開始時は緩和
                reasons.append("CHAIN_START")
            else:
                height_multiplier = 50.0  # v42の標準値

            height_penalty = landing_y * height_mult * height_multiplier

            # 高盤面での追加ペナルティ（v42の成功値）
            if landing_y > 0.5:
                height_penalty *= 2.0  # v42の成功値
                reasons.append("HIGH_TOWER")
            elif landing_y > 0.0:
                reasons.append("HIGH_LAYER")
        elif phase == "MEDIUM":
            # v71: MEDIUMフェーズでreactive_pairsに応じて緩和（新規導入）
            if reactive_pairs >= 2:
                height_multiplier = 40.0  # chain reaction開始時は緩和
                reasons.append("CHAIN_START")
            elif reactive_pairs >= 1:
                height_multiplier = 45.0  # chain reaction準備時は微緩和
            else:
                height_multiplier = 50.0  # v42の標準値

            height_penalty = landing_y * height_mult * height_multiplier

            # 高盤面での追加ペナルティ（v42の成功値）
            if landing_y > 0.5:
                height_penalty *= 1.5  # v42の成功値
                reasons.append("MEDIUM_TOWER")
            elif landing_y > 0.0:
                reasons.append("HIGH_LAYER")
        else:  # LOW
            height_penalty = landing_y * 50.0

        score -= height_penalty

        # 3. ドリフトによるペナルティ（v42の設定）
        if phase == "HIGH":
            drift_penalty = (abs(drift_x) + drift_unc) * 35.0  # v42のHIGHフェーズ
        elif phase == "MEDIUM":
            drift_penalty = (abs(drift_x) + drift_unc) * 35.0  # v42のMEDIUMフェーズ
        else:  # LOW, CRITICAL
            drift_penalty = (abs(drift_x) + drift_unc) * 30.0
        score -= drift_penalty

        # 4. 左右バランス補正（v42の設定）
        balance_strength = 20.0
        if phase == "HIGH":
            balance_strength = 40.0  # v42のHIGHフェーズ
        elif phase == "MEDIUM":
            balance_strength = 30.0  # v42のMEDIUMフェーズ
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
                )  # v42のCRITICALフェーズ
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
