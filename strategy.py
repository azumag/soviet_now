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
# v50-v52: fukuzuka chousei shippai - height_multiplier do-nyu, has_merge ni yoru drift_penalty kanwa wa zenkou koka nashi
# v52: sukoa 1319 (besuto 2335 no 56%) - MEDIUM height_mult 2.4->2.2 ni sageta tame HIGH toutatsu ga hayaku, turn 36 de HIGH, turn 38 de akarain越え. Furi-ko pattern kaihi suru tame v42 no shinpuru kouzou wo kanzen fukkatsu
# v53: v42 kanzen fukkatsu・MEDIUM phase merge bi-zou ban - v52 no shippai (sukoa 1319, akarain越え) ni ukete, v42 no shinpuru kouzou wo kanzen fukkatsu. MEDIUM phase height_mult wo 2.4 ni modoshi (v42 no seikou chi), has_merge ni yoru drift_penalty kanzu wo sakujou (furi-ko pattern kaihi). v42 no zen parameter wo fukkatsu shitsu, rireki bunseki ni motozuki MEDIUM phase de no merge wo sokushin suru tame merge_mult wo 1.0->1.1 ni bi-zou. koodo ryou iji (yaku 110 gyou) de shimple katsu ken na kouzou wo kakuhu
# v54: v42 has_merge joken do-nyuu・MEDIUM phase merge kyosei ban - v53 no shippai (sukoa 757, HIGH toutatsu soka, merge shuuseki 2/11) ni ukete, v42 no shinpuru kouzou wo iji shitsu MEDIUM phase ni gen'gen shite has_merge joken wo do-nyuu. merge_mult wo v53 no 1.1 kara v42 no 1.0 ni modoshi (height_penalty ni katenai tame). MEDIUM phase de has_merge ga aru baai, height_penalty_factor=0.6 (40% kanwa), drift_penalty_factor=0.8 (20% kanwa) ni settei shi merge wo kyosei. v52 no shippai (height_mult 2.2) wo kaihi suru tame height_mult wa v42 no 2.4 wo iji. v31 no fukuzuka (HIGH phase has_merge, reactive_pairs) wo kaihi suru tame has_merge joken wa MEDIUM phase ni gen'gen shite HIGH phase wa v42 no parameta wo iji. koodo ryou zouchi (yaku 115 gyou) de v42 no shinpuru kouzou wo hoji


def decide(game_state: dict, analysis: dict) -> dict:
    """v42 no shinpuru kouzou wo iji shitsu MEDIUM phase ni has_merge joken wo do-nyuu"""

    results = analysis.get("results", [])

    if not results:
        return {"x": 0.0, "reason": "no analysis data"}

    best_x = 0.0
    best_score = -float("inf")
    best_reason = ""

    # banmen jouhou
    pieces = game_state.get("pieces", [])
    max_y = max([p["y"] for p in pieces]) if pieces else -4.0

    # phase handei (v54: v42 no shikichi wo iji)
    if max_y < 0.8:
        phase = "LOW"
        height_mult = 1.0
        merge_mult = 1.2
    elif max_y < 1.8:
        phase = "MEDIUM"
        height_mult = 2.4  # v54: v42 no 2.4 wo iji (v52 no shippai kaihi)
        merge_mult = 1.0  # v54: v53 no 1.1 kara v42 no 1.0 ni modosu
    elif max_y < 3.0:
        phase = "HIGH"
        height_mult = 2.6  # v54: v42 no 2.6 wo iji
        merge_mult = 1.0
    else:
        phase = "CRITICAL"
        height_mult = 1.0
        merge_mult = 0.6  # v54: v42 no 0.6 wo iji (merge yuusen)

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

        # 1. merge grade ni yoru sukou (v54: v42 no chi wo iji)
        if merge_grade == "DIRECT":
            score += 1200.0 * merge_mult
            reasons.append("DIRECT_MERGE")
        elif merge_grade == "NEAR":
            score += 600.0 * merge_mult  # v54: v42 no 600 wo iji
            reasons.append("NEAR_MERGE")
        elif merge_grade == "FAR":
            score += 200.0 * merge_mult
            reasons.append("FAR_MERGE")

        # 2. koudou ni yoru penalty (v54: MEDIUM phase de has_merge ga aru baai kanwa)
        if phase == "CRITICAL":
            height_penalty = landing_y * 40.0
            if landing_y > 1.0:
                reasons.append("CRITICAL_HEIGHT")
        else:
            # v54: MEDIUM phase de has_merge ga aru baai height_penalty wo 40% kanwa
            height_penalty_factor = 1.0
            if phase == "MEDIUM" and has_merge:
                height_penalty_factor = 0.6  # MEDIUM merge kyosei

            height_penalty = landing_y * 50.0 * height_mult * height_penalty_factor

            if phase == "HIGH" and landing_y > 0.5:  # v54: v42 no 0.5 wo iji
                height_penalty *= 2.0
                reasons.append("HIGH_TOWER")
            elif phase == "MEDIUM" and landing_y > 0.5:
                height_penalty *= 1.5
                reasons.append("MEDIUM_TOWER")
            elif landing_y > 0.0:
                reasons.append("HIGH_LAYER")

        score -= height_penalty

        # 3. drift ni yoru penalty (v54: MEDIUM phase de has_merge ga aru baai kanwa)
        drift_penalty_factor = 1.0
        if phase == "MEDIUM" and has_merge:
            drift_penalty_factor = 0.8  # MEDIUM merge kyosei

        drift_penalty = (abs(drift_x) + drift_unc) * 30.0 * drift_penalty_factor
        score -= drift_penalty

        # 4. sayuu baransho (v54: v42 no chi wo iji)
        balance_strength = 20.0
        if phase == "HIGH":
            balance_strength = 40.0  # v54: v42 no 40.0 wo iji
        elif phase == "MEDIUM":
            balance_strength = 30.0

        left_count = sum(1 for p in pieces if p["x"] < 0)
        right_count = len(pieces) - left_count
        balance_bias = (right_count - left_count) / (len(pieces) if pieces else 1)

        balance_penalty = x * balance_bias * balance_strength
        score -= abs(balance_penalty)

        # 5. nextNext ga onaji type nara chuuyuse bonus (v54: v42 no chi wo iji)
        if next_next_type == next_type:
            center_bonus = max(0, 1.0 - abs(x) / 2.0) * 50.0
            score += center_bonus
            reasons.append("NEXT_SAME")

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
