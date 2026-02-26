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
# v50-v55: MEDIUM phase has_merge joken shippai - v53-v54 de MEDIUM phase ni has_merge joken wo do-nyuu (height_penalty_factor=0.6, drift_penalty_factor=0.8) shita ga, sukoa wa v42 (2335) kara v54 (706) made teimen ka. has_merge joken ha "merge ga aru baai penalty wo kanwa suru" to iu kangaedakedo, "hinkitsu na merge (koudou ga takai, drift ga ookii) wo shuutoku suru" kekka to nari, zentaise no sukoa wo teimen saseteita. v55 de has_merge joken wo sakujo shi, v42 no shimpulu kouzou (penalty kanwa nashi, futsuu no merge scoring) ni kanzen fukkatsu suru. CRITICAL phase ha v19 no sekkei wo iji (merge_mult=0.6 de merge yuusen).
# v56-v58: has_merge joken no furiko - v56 de reactive_pairs joken wo do-nyuu, v57 de has_merge joken wo sai-do-nyuu, v58 de has_merge joken wo sakujo. has_merge joken ha fukkusu kai shippai shiteiru (v50-v55, v57) no de, kono element wo do-nyuu suru no wa yame.
# v59: HIGH phase kouki koudou kanwa-ban - v58 no shippai (sukoa 1146, HIGH phase de score_delta=0 de 21 turn kan zen teimen) wo uke, HIGH phase kouki de koudou kanwa wo sai-shishou. v56 no fukutsu na joken bun-ki (reactive_pairs>=3) wo sakujo shi, max_y>2.5 to iu shimpuru na joken dake de koudou kanwa wo kanwa shite chain reaction kikai wo saidaika. v57 no has_merge joken ni yoru furiko pattern wo kaihi (has_merge joken nashi, NO_MERGE_PENALTI nai). v42 no ganjina kihon kouzou wo iji shi, code ryuu wa yaku 115-gyou de v42 no shimpuru kouzou wo iji.
# v60: reactive_pairs katsuyou - v59 no shippai (sukoa 771, HIGH phase de merge rate 0%・HIGH_PHASE_LATE_RELAX hataki 1-kai dake) wo uke, max_y>2.5 joken (hataki 1-kai dake, kouka nashi) wo sakujo shi, reactive_pairs katsuyou de merge sokushin. v42 no shimpuru kouzou (DIRECT=1200/NEAR=600/FAR=200, height_penalty=50*height_mult, drift_penalty=30, balance hosei) wo kanzen iji. HIGH phase de reactive_pairs >= 3 no baai, height_multiplier wo 35.0 ni kanwa (chain reaction chu no merge yuusen). MEDIUM/HIGH phase de merge nashi position ni karuku no NO_MERGE_PENALTI (-50) wo tsuika (v12 no kousei youso wo keiryou-ka). has_merge joken no furiko pattern wo kaihou (reactive_pairs joken de merge sokushin). v31 no reactive_pairs katsuyou to v12 no merge nashi penalty no seikou youso wo v42 no ganjina kouzou ni tougou. code ryuu wa yaku 130-gyou de v59 no 115-gyou kara fukutsu-ka naku seikou youso wo tougou
# v61: v42 kanzen fukkatsu-ban - v60 no shippai (sukoa 1063, CRITICAL phase de merge available=false to NO_MERGE_PENALTI (-50) ga shinkushuuka) wo uke, v42 no shimpuru kouzou e kanzen fukkatsu. v60 de tsuika shita NO_MERGE_PENALTI wo sakujo (v42 ha kono penalty nashi de 2335 point wo tassei). v60 de tsuika shita reactive_pairs joken bun-ki (HIGH phase de height_multiplier=35.0 kanwa) wo sakujo (v59 no shippai pattern to onaji). v42 no seikou shita seikaku chi wo kanzen iji: DIRECT=1200/NEAR=600/FAR=200, height_penalty=50*height_mult, drift_penalty=30 (phase bunnki nashi), balance_strength: LOW=20/MEDIUM=30/HIGH=40/CRITICAL=fuka, center_bonus: CRITICAL=60/others=50. Code ryuu wa yaku 100-gyou ni genshou, v42 no "simpulu kote kouzou" wo kanzen fukkatsu
# v62: merge bonus sakugen/koudou kanwa kouka - v61 no shippai (sukoa 1783, HIGH phase de 81% score_delta=0, CRITICAL phase de game over) wo uke, v42 no "merge bonus kajou / koudou kanwa fujuubun" na kadai wo kouzouteki ni kaiketsu. has_merge/reactive_pairs joken no furiko pattern wo kaihi (tsuika shinai). v42 no merge bonus (1200/600/200) wo 70% sakugen (840/420/140) shi, HIGH phase height_multiplier wo 2.6→4.0 ni kyokuka (50*4.0*2.0=400). HIGH phase height penalty wo 60% zouchi (landing_y>0.5 de ×3.2). Code ryuu wa yaku 100-gyou de v61 no onaji kazu, shimpulu kouzou wo iji
# v63: v42完全復活版 - v62の失敗（スコア1783、HIGHフェーズで81%のscore_delta=0、CRITICALフェーズでgame over）を受けて、v42のシンプルかつ頑健な構造に完全復活。merge bonusをv42の値に戻す（DIRECT=1200/NEAR=600/FAR=200）、HIGHフェーズのheight_multを2.6に戻す、HIGHフェーズのheight_penaltyを2.0に戻す。NO_MERGE_PENALTIやreactive_pairsによる過度な複雑化を排除し、v42の基本構造を維持。コード量は約110行に削減
# v64: v12の緩い高度管理・v42のシンプル構造統合版 - v63の失敗（スコア969、HIGHフェーズで51%が非マージ）を受けて、v12の成功要因「より緩い高度管理」をv42のシンプル構造に統合。MEDIUMフェーズheight_multを2.4→2.0に減、HIGHフェーズheight_multを2.6→2.4に減、HIGHフェーズheight_penaltyを2.0→1.5に減、MEDIUMフェーズheight_penaltyを1.5→1.3に減。v12の「マージなしペナルティ」は追加しない（振り子パターン回避）。v42のシンプル構造（DIRECT=1200/NEAR=600/FAR=200、drift_penalty=30）を完全維持。コード量は約110行でv63と同じ、シンプル構造を維持


def decide(game_state: dict, analysis: dict) -> dict:
    """v42のシンプル構造を維持しつつ、v12の緩い高度管理を統合"""

    results = analysis.get("results", [])

    if not results:
        return {"x": 0.0, "reason": "no analysis data"}

    best_x = 0.0
    best_score = -float("inf")
    best_reason = ""

    # 盤面情報
    pieces = game_state.get("pieces", [])
    max_y = max([p["y"] for p in pieces]) if pieces else -4.0

    # フェーズ判定（v64: v12の閾値0.8/1.8を維持、高度管理設定のみv12に近づける）
    if max_y < 0.8:
        phase = "LOW"
        height_mult = 1.0
        merge_mult = 1.2
    elif max_y < 1.8:
        phase = "MEDIUM"
        height_mult = 2.0  # v64: v42の2.4からv12の2.0に減（マージ機会増加）
        merge_mult = 1.0
    elif max_y < 3.0:
        phase = "HIGH"
        height_mult = 2.4  # v64: v42の2.6からv12の2.4に減（マージ機会増加）
        merge_mult = 1.0
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

        # 1. マージグレードによるスコア（v64: v42の値を維持）
        if merge_grade == "DIRECT":
            score += 1200.0 * merge_mult
            reasons.append("DIRECT_MERGE")
        elif merge_grade == "NEAR":
            score += 600.0 * merge_mult
            reasons.append("NEAR_MERGE")
        elif merge_grade == "FAR":
            score += 200.0 * merge_mult
            reasons.append("FAR_MERGE")

        # 2. 高度によるペナルティ（v64: v12の緩い設定を採用）
        height_penalty = landing_y * 50.0 * height_mult

        if phase == "HIGH" and landing_y > 0.5:
            height_penalty *= 1.5  # v64: v42の2.0からv12の1.5に減
            reasons.append("HIGH_TOWER")
        elif phase == "MEDIUM" and landing_y > 0.5:
            height_penalty *= 1.3  # v64: v42の1.5からv12の1.3に減
            reasons.append("MEDIUM_TOWER")
        elif landing_y > 0.0:
            reasons.append("HIGH_LAYER")

        score -= height_penalty

        # 3. ドリフトによるペナルティ（v64: v42の値を維持）
        drift_penalty = (abs(drift_x) + drift_unc) * 30.0
        score -= drift_penalty

        # 4. 左右バランス補正（v64: v42の値を維持）
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

        # 5. nextNextが同じタイプなら中央寄せボーナス（v64: v42の値を維持）
        if next_next_type == next_type:
            center_bonus = max(0, 1.0 - abs(x) / 2.0) * 50.0
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
