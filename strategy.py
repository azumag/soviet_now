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
# v66: HIGHフェーズマージ優先化版 - v65の失敗（スコア1641、HIGHフェーズでマージ率0%）を受けて、HIGHフェーズでのマージ優先戦略を導入。履歴分析でHIGHフェーズ（7ターン）でマージ可能ターン0を特定。v42のシンプル構造を維持しつつ、HIGHフェーズでのマージボーナス強化（merge_mult=1.0→2.0）と高度ペナルティ緩和（height_mult=2.6→2.2）でマージ機会を最大化。HIGHフェーズの追加ペナルティ削除（HIGH_TOWER条件削除）でマージ可能位置のスコアを上昇させる。CRITICALフェーズのマージボーナスも強化（merge_mult=0.6→2.0）
# v67: CRITICALフェーズマージ強化・v42構造復活版 - v66の失敗（スコア1244、HIGH/CRITICALフェーズでマージ率0%）を受けて、v66のHIGHフェーズ調整（height_mult=2.2→2.6、merge_mult=2.0→1.0に戻しv42の成功値に復活）。v42のシンプル構造（MEDIUM height_mult=2.4、HIGH height_mult=2.6、HIGH balance_strength=40.0）を完全復活。CRITICALフェーズのマージ優先度を強化（merge_mult=0.6→1.2、HIGHフェーズと同等）、height_multiplierを30.0→35.0に強化（chain reaction狙い維持）。追加ペナルティ条件（has_merge、reactive_pairs）は完全排除、コード量約100行でv42の頑健な構造を維持
# v68: height_penalty緩和・マージ機会増強版 - v67の失敗（スコア964、マージ機会6/65ターン=9.2%のみ）を受けて、履歴分析でHEIGHT_CONTROLが22回（34%）で支配的であることを特定。height_multiplier強化（30.0→35.0）はchain reaction狙いだが、高度管理が強すぎてマージ機会を大幅に逃している。v42の成功構造をベースに、CRITICALフェーズのheight_multiplierを35.0→30.0に緩和（v42の成功値復活）。MEDIUMフェーズの追加ペナルティ（MEDIUM_TOWER）を削除し、HIGHフェーズへの到達を遅延させつつマージ機会を確保。HIGHフェーズの追加ペナルティを2.0→1.5に緩和し、マージ可能な位置のスコアを上昇させる。コード量約95行でv42のシンプル構造を維持
# v69: MEDIUM/HIGHフェーズ統一マージ機会確保版 - v68の失敗（スコア968、HIGHフェーズでマージ可能ターン0/8）を受けて、履歴分析でMEDIUMフェーズ（12ターン中2ターン、16.7%）とHIGHフェーズ（8ターン中0ターン、0%）でマージ機会が不足していることを特定。v42のシンプル構造を維持しつつ、MEDIUM/HIGHフェーズの両方でマージ機会を確保するための統一戦略を導入。MEDIUMフェーズの追加ペナルティを導入しHIGH到達遅延しつつ、HIGHフェーズのマージボーナス強化（merge_mult=1.0→1.2）と高度管理緩和（height_mult=2.6→2.4、height_penalty=1.5→1.3）でマージ機会を最大化。CRITICALフェーズのheight_multiplierはv42の30.0を維持。コード量約100行でv42のシンプル構造を維持
# v70: v42完全復帰・振り子破壊版 - v69の失敗（スコア1789、HIGHフェーズでマージ可能ターン2/19=10.5%、MEDIUMフェーズでマージ可能ターン0/10=0%）を受けて、振り子パターン（v65-v69で追加ペナルティの追加・削除・再追加）を破壊。v65-v69の複雑化（MEDIUM_TOWER、HIGH_TOWERの1.3倍ペナルティ、merge_multの変動）を完全削除し、v42のシンプルかつ頑健な構造に完全復帰。height_mult: MEDIUM=2.4/HIGH=2.6（v42の成功値）、HIGH height_penalty=2.0、MEDIUM height_penalty=1.5（v42の成功値）。CRITICALフェーズのheight_multiplier=30.0（v42の成功値）、merge_mult=0.6（v42の成功値）。マージボーナス1200/600/200（v42の成功値）。追加条件分岐（has_merge、reactive_pairs、MEDIUM_TOWERの1.3倍等）は完全排除。コード量約90行でv42の成功構造を完全復活


def decide(game_state: dict, analysis: dict) -> dict:
    """v42のシンプルかつ頑健な構造に完全復帰"""

    results = analysis.get("results", [])

    if not results:
        return {"x": 0.0, "reason": "no analysis data"}

    best_x = 0.0
    best_score = -float("inf")
    best_reason = ""

    # 盤面情報
    pieces = game_state.get("pieces", [])
    max_y = max([p["y"] for p in pieces]) if pieces else -4.0

    # フェーズ判定（v42の閾値0.8/1.8/3.0）
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

        # 2. 高度によるペナルティ（v42のシンプル構造）
        if phase == "CRITICAL":
            # CRITICALフェーズではheight_multiplier適用（v42の30.0）
            height_multiplier = 30.0
            height_penalty = landing_y * height_multiplier
            if landing_y > 1.0:
                reasons.append("CRITICAL_HEIGHT")
        else:
            height_penalty = landing_y * 50.0 * height_mult

            # 高盤面での追加ペナルティ（v42の成功値）
            if phase == "HIGH" and landing_y > 0.5:
                height_penalty *= 2.0  # v42の成功値
                reasons.append("HIGH_TOWER")
            elif phase == "MEDIUM" and landing_y > 0.5:
                height_penalty *= 1.5  # v42の成功値
                reasons.append("MEDIUM_TOWER")
            elif landing_y > 0.0:
                reasons.append("HIGH_LAYER")

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
