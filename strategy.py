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
# v50-v55: MEDIUM phase has_merge joken shippai - v53-v54 de MEDIUM phase ni has_merge joken wo do-nyuu (height_penalty_factor=0.6, drift_penalty_factor=0.8) shita ga, sukoa wa v42 (2335) kara v54 (706) made teimen ka. has_merge joken ha "merge ga aru baai penalty wo kanwa suru" to iu kangaedakedo, "hinkitsu na merge (koudou ga takai, drift ga ookii) wo shuutoku suru" kekka to nari, zentaise no sukoa wo teimen saseteita. v55 de has_merge joken wo sakujo shi, v42 no shimpuru kouzou (penalty kanwa nashi, futsuu no merge scoring) ni kanzen fukkatsu suru. CRITICAL phase ha v19 no sekkei wo iji (merge_mult=0.6 de merge yuusen).
# v56: HIGHフェーズchain reaction支援版 - v42の失敗（スコア1593、HIGHフェーズでscore_delta停滞）を受けて、HIGHフェーズ後半でchain reactionの可能性がある場合、高度管理を緩和するシンプルな条件を導入。v31の複雑な条件分岐ではなく、max_y > 2.5かつreactive_pairs >= 3の場合、height_multiplierを35.0に大幅緩和し、drift_penaltyも0.7に緩和してchain reaction中にマージを優先。v19の基本構造（フェーズ閾値0.8/1.8/3.0、merge_mult、height_mult）は維持。コード量増加を最小限に抑え（約110行→約120行）、v42のシンプル構造を維持しつつchain reactionの機会を最大化
# v57: HIGHフェーズマージ優先版 - v56の失敗（スコア676、非マージ戦略57%・chain reaction条件は24ターン目のみ発動）を受けて、chain reactionの複雑な条件分岐を削除し、v42のシンプル構造をベースにマージ優先戦略を導入。履歴分析でMEDIUM_TOWER/HIGH_TOWERが計10回あり、マージ機会を逃していることを特定。v42のheight_multiplier（50.0）とdrift_penalty（30.0）を維持しつつ、HIGHフェーズでhas_merge=trueの場合、height_penalty_factor=0.7に緩和してマージ機会確保。NEAR_MERGEボーナスを600→750に強化（マージ重視）。HIGHフェーズでマージなしの場合、軽量のNO_MERGE_PENALTY（-100）を追加してマージをプッシュ（強制ではなく軽く誘導）。v42の頑健な構造を維持し、コード量は約115行


def decide(game_state: dict, analysis: dict) -> dict:
    """v42のシンプル構造をベースに、HIGHフェーズでのマージ機会確保を強化"""

    results = analysis.get("results", [])

    if not results:
        return {"x": 0.0, "reason": "no analysis data"}

    best_x = 0.0
    best_score = -float("inf")
    best_reason = ""

    # banmen jouhou
    pieces = game_state.get("pieces", [])
    max_y = max([p["y"] for p in pieces]) if pieces else -4.0

    # phase handei (v19/v42 no shikichi wo iji)
    if max_y < 0.8:
        phase = "LOW"
        height_mult = 1.0
        merge_mult = 1.2
    elif max_y < 1.8:
        phase = "MEDIUM"
        height_mult = 2.4
        merge_mult = 1.0
    elif max_y < 3.0:
        phase = "HIGH"
        height_mult = 2.6
        merge_mult = 1.0
    else:
        phase = "CRITICAL"
        height_mult = 1.0
        merge_mult = 0.6  # CRITICAL: merge yuusen

    # tsugi no piece jouhou
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

        # 1. merge grade ni yoru sukou (v57: NEAR_MERGE wo kyouka)
        if merge_grade == "DIRECT":
            score += 1200.0 * merge_mult
            reasons.append("DIRECT_MERGE")
        elif merge_grade == "NEAR":
            score += 750.0 * merge_mult  # v57: 600→750 ni kyouka (merge juushi)
            reasons.append("NEAR_MERGE")
        elif merge_grade == "FAR":
            score += 200.0 * merge_mult
            reasons.append("FAR_MERGE")

        # 2. koudou ni yoru penalty (v57: HIGHフェーズ de has_merge baai wa kanwa)
        if phase == "CRITICAL":
            # CRITICAL: height_penalty shimpuru-ka (merge yuusen)
            height_penalty = landing_y * 40.0
            if landing_y > 1.0:
                reasons.append("CRITICAL_HEIGHT")
        else:
            # v57: HIGHフェーズ de has_merge ga aru baai, height_penalty wo 70% ni kanwa
            height_penalty_factor = 1.0
            if phase == "HIGH" and has_merge:
                height_penalty_factor = 0.7  # has_merge toki wa kanwa

            height_penalty = landing_y * height_mult * 50.0 * height_penalty_factor

            # koudanme de no tsuika penalty (CRITICAL phase dewa tekiyou shinai)
            if phase == "HIGH" and landing_y > 0.5:
                height_penalty *= 2.0  # v57: v19 no 2.0 wo iji
                reasons.append("HIGH_TOWER")
            elif phase == "MEDIUM" and landing_y > 0.5:
                height_penalty *= 1.5  # v57: v19 no 1.5 wo iji
                reasons.append("MEDIUM_TOWER")
            elif landing_y > 0.0:
                reasons.append("HIGH_LAYER")

        score -= height_penalty

        # 3. drift ni yoru penalty (v57: v19 no 30.0 wo iji)
        drift_penalty = (abs(drift_x) + drift_unc) * 30.0
        score -= drift_penalty

        # 4. sayuu baransho
        balance_strength = 20.0
        if phase == "HIGH":
            balance_strength = 40.0
        elif phase == "MEDIUM":
            balance_strength = 30.0

        left_count = sum(1 for p in pieces if p["x"] < 0)
        right_count = len(pieces) - left_count
        balance_bias = (right_count - left_count) / (len(pieces) if pieces else 1)

        balance_penalty = x * balance_bias * balance_strength
        score -= abs(balance_penalty)

        # 5. nextNext ga onaji type nara chuuyuse bonus
        if next_next_type == next_type:
            center_bonus = max(0, 1.0 - abs(x) / 2.0) * 50.0
            score += center_bonus
            reasons.append("NEXT_SAME")

        # 6. v57: HIGHフェーズ de merge nai baai, karuryou no penalty wo kaifa
        if phase == "HIGH" and not has_merge:
            score -= 100.0  # NO_MERGE_PENALTY (kyouryoku na puusyu)
            if "NO_MERGE_PENALTY" not in reasons:
                reasons.append("NO_MERGE_PENALTY")

        # sukou kou shin
        if score > best_score:
            best_score = score
            best_x = x
            best_reason = "_".join(reasons) if reasons else "HEIGHT_CONTROL"

    # anzen na hanni ni kurippu
    best_x = max(-3.0, min(3.0, best_x))
    best_x = round(best_x, 2)

    return {"x": best_x, "reason": best_reason}


# --- AI kaizen kinshi zon ---
if __name__ == "__main__":
    import json
    import sys

    # sutandaronesuto tesuto you
    gs_path = sys.argv[1] if len(sys.argv) > 1 else "game_state.json"

    try:
        game_state = json.load(open(gs_path))
    except Exception as e:
        print(json.dumps({"error": str(e)}))
        sys.exit(1)

    # analyze_board kara kaiseki data shutoku
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
