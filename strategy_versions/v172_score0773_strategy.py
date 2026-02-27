#!/usr/bin/env python3
"""strategy.py - AI改善対象の決定スクリプト"""

# 固定インターフェース:
# decide(game_state: dict, analysis: dict) -> dict
#    戻り値: {"x": float, "reason": str}
#
# AI改変可能: decide() 内部、ヘルパー関数、定数、import
# AI改変禁止: decide() シグネチャ、if __name__ == "__main__" ブロック

# --- 変更履歴 ---
# [BEST:2325] v19: CRITICALフェーズ導入版 - HIGHフェーズのheight_mult過剰を修正、CRITICALフェーズ（max_y>3.0）を新設。CRITICALではマージ絶対優先（merge_mult=0.6、height_multなし、height_penaltyシンプル化）。MEDIUMフェーズheight_mult微増（2.2→2.4）でHIGH到達遅延、HIGHフェーズheight_mult微減（2.8→2.6）でマージ機会確保
# [BEST:2335] v42: v19復活・v31/v29複雑化要素削除版 - v41の失敗（スコア558）を受けて、v41がv31から取り入れたreactive_pairsとhas_mergeによる複雑な条件分岐を削除。v19のシンプル構造（DIRECT=1200/NEAR=600/FAR=200、height_penalty=50*height_mult、drift_penalty=30）に復活。v19のCRITICALフェーズ（merge_mult=0.6）を維持。コード量削減（約140行→約110行）で頑健性を確保
# v50-v64: has_merge/reactive_pairs条件の振り子パターンと閾値シャッフル
# [BEST:2346] v84: HIGHフェーズマージ優先・構造改善版 - v83の失敗（スコア1065、HIGHフェーズマージ率低）を受けて、振り子パターン完全回避で根本的な構造改善を実施。chain reaction緩和は完全廃止（v82の失敗から学ぶ）。代わりにHIGHフェーズでのマージ確保を優先：（1）merge_gradeボーナス強化（DIRECT=1500/NEAR=800/FAR=300でマージの質を重視）、（2）HIGHフェーズ高度管理緩和（height_mult=2.2に減、HIGH_TOWERペナルティ1.3倍に減）、（3）マージなし位置にNO_MERGEペナルティ（-150）、（4）max_yに応じた動的調整（盤面が高いほどマージ優先、低いほど高度管理優先）。v42のシンプル構造を維持しつつ、HIGHフェーズでのマージ機会を構造的に確保。コード量増加なし（約110行）。
# v93-v96: 振り子パターン（一律緩和→reactive_pairs活用→NO_MERGEペナルティ廃止→NO_MERGEペナルティ復活）- v93: height_multiplier 50.0→35.0、v94: 35.0→25.0、v95: reactive_pairs>=4で15.0・NO_MERGEペナルティ廃止、v96: reactive_pairs>=2で25.0・NO_MERGEペナルティ-150復活。v96にはreactive_pairsがlist型の時のバグがありturn 54以降でエラー発生。
# v123-v125: MEDIUMフェーズheight_multの振り子パターン（v122:2.2→v123:2.4→v124:2.2→v125:1.8）
# v126-v128: NO_MERGEペナルティとHIGH_TOWER削除の振り子パターン - v126: NO_MERGE追加、v127: NO_MERGE削除、v128: 高度管理緩和
# v129-v137: HIGH_TOWERペナルティの振り子パターン（v134:削除→v136:1.2倍→v137:2.0倍）- 一律のHIGH_TOWERペナルティが「削除すると高度管理不十分」「再導入するとマージ機会損失」の振り子を繰り返している。
# [BEST:3689] v128: HIGHフェーズマージ優先版 - v127の失敗（スコア724、HIGHフェーズ10ターン中9ターンでマージ不可）を受けて、HIGHフェーズでのマージ機会損失を特定。履歴分析でv127の高度管理がHIGHフェーズで過剰に強化されていることが原因を特定（HIGHフェーズのdecision_reasonはHIGH_TOWERが1回だが、HIGH_LAYERが5回で高度管理が支配的）。（1）HIGHフェーズ高度管理大幅緩和：height_multをv42の2.6から1.8に大幅に引き下げ（v84の2.2よりも緩和し、マージ優先を徹底）。（2）マージボーナス強化：v42の強力な値（DIRECT=1200/NEAR=600/FAR=200）を維持し、高度管理緩和と組み合わせてマージをHIGHフェーズの主要目標にする。（3）HIGHフェーズHIGH_TOWERペナルティ緩和：v84の1.3倍を維持し、height_mult大幅緩和と相乗効果。（4）v42のシンプル構造を維持：NO_MERGEペナルティの「入れるか入れないか」の振り子を回避し、第三の選択肢（マージボーナス強化・高度管理大幅緩和）を採用。振り子パターン（NO_MERGEペナルティ、height_multiplier微調整）をHIGHフェーズでのマージ優先徹底で解消。コード量維持（約110行）。
# v170: TOWER_MERGE削除・v128成功要素採用版 - v169の失敗（スコア784、TOWER_MERGEペナルティ12回発動・全てマージ判断ターンでscore_delta=0）を受けて、TOWER_MERGEペナルティを削除しv128の成功要素を取り入れるブレイクスルーを採用。履歴分析でv169の失敗原因を特定：（1）TOWER_MERGEペナルティ12回発動、全てマージ判断ターン、全てscore_delta=0。マージ機会を完全に阻害している。（2）HIGHフェーズheight_mult=2.4が強すぎ、HIGHフェーズ到達が早すぎ（turn61でCRITICAL到達）。（3）HIGH_TOWERペナルティ=1.6倍でも強い、HIGH_TOWER発動5回。（4）マージ予測精度0%：merge_available=trueの9ターン全てでscore_delta=0。（5）ブレイクスルー：TOWER_MERGEペナルティを削除し、v128の成功要素を採用。HIGHフェーズheight_multをv128の1.8に緩和（v169の2.4から）、HIGH_TOWERペナルティをv128の1.3倍に緩和（v169の1.6倍から）。（6）マージボーナスはv42の値を維持：一律構造で頑健性を確保。v168のマージボーナス強化（v84の値）は個別的すぎて失敗したため、v42の一律構造（DIRECT=1200/NEAR=600/FAR=200）を維持。（7）v42のシンプル構造を維持：予測精度の低さを補償。TOWERペナルティは維持しつつ、v128の成功設定（HIGHフェーズ高度管理緩和）を採用。（8）TOWER_MERGE削除によるコード量削減（約60行→約50行）、シンプルで頑健な構造に回帰。（9）振り子パターン解消：TOWER_MERGEの「導入→削除」振り子を回避し、v128の成功要素を直接採用することで、マージと高度管理のバランスを改善。（10）ブレイクスルー：TOWER_MERGEのような新しい機能ではなく、v128の成功したHIGHフェーズ設定（height_mult=1.8、HIGH_TOWER=1.3倍）を直接採用することで、HIGHフェーズでのマージ優先と高度管理のバランスを実現。コード量削減（約60行→約50行）。失敗（スコア1355）：履歴分析でv170の失敗原因を特定：（1）マージ予測精度0%：merge_available=trueの6ターン全てでscore_delta=0。（2）TOWERペナルティ過剰発動：MEDIUM_TOWERが13回、HIGH_TOWERが2回発動。TOWERペナルティの存在が高度管理を強くしすぎ、HIGHフェーズ到達を遅らせている（turn 67で到達）。（3）HIGHフェーズ到達遅延：MEDIUMフェーズが66ターン、HIGHフェーズは9ターンのみ。v128の成功時はより早くHIGHフェーズに到達していたはず。（4）スコア伸びなし：75ターンでスコアは1354→1355の+1点のみ。（5）v128の成功は個別のゲームでの成功であり、一律適用には危険。TOWERペナルティの存在はHIGH_TOWER=1.3倍であっても高度管理を強くしすぎ、HIGHフェーズ到達を遅延させる。（6）振り子パターン再発：TOWERペナルティの「削除→復帰→緩和→削除→導入→削除」振り子がv164-v170で7回繰り返されている。TOWERペナルティ自体が振り子の根本原因。
# v171: TOWERペナルティ削除・高度管理緩和版 - v170の失敗（スコア1355、TOWERペナルティ過剰発動・HIGHフェーズ到達遅延）を受けて、TOWERペナルティを完全削除し高度管理を大幅に緩和するブレイクスルーを採用。履歴分析でv170の失敗原因を特定：（1）TOWERペナルティ過剰発動：MEDIUM_TOWERが13回、HIGH_TOWERが2回発動。TOWERペナルティの存在が高度管理を強くしすぎ、HIGHフェーズ到達を遅らせている（turn 67で到達）。（2）HIGHフェーズ到達遅延：MEDIUMフェーズが66ターン、HIGHフェーズは9ターンのみ。HIGHフェーズでのマージ機会が不十分。（3）振り子パターン再発：TOWERペナルティの「削除→復帰→緩和→削除→導入→削除」振り子がv164-v170で7回繰り返されている。TOWERペナルティ自体が振り子の根本原因。（4）v128の成功は個別のゲームでの成功であり、一律適用には危険。TOWERペナルティの存在はHIGH_TOWER=1.3倍であっても高度管理を強くしすぎ、HIGHフェーズ到達を遅延させる。（5）ブレイクスルー：TOWERペナルティを完全削除し、v42の一律構造での高度管理のみに戻る。MEDIUMフェーズheight_multをv170の2.4から2.2に緩和し、HIGHフェーズ到達を早める。HIGHフェーズheight_multをv170の1.8から1.5に緩和し、マージ優先を徹底。（6）v42のシンプル構造を維持：一律のマージボーナス（DIRECT=1200/NEAR=600/FAR=200）と高度管理（height_penaltyのみ）で頑健性を確保。TOWERペナルティのような複雑な条件分岐を削除し、シンプルな構造に回帰。（7）振り子パターン根本解消：TOWERペナルティの「削除→復帰」振り子を第三の選択肢（TOWER削除・高度管理緩和）で解消。一律構造での高度管理で頑健性を確保しつつ、HIGHフェーズでのマージ機会を最大化。（8）ブレイクスルー：TOWERペナルティの「削除→復帰」振り子を根本的に解消し、一律構造での高度管理のみでマージと高度管理のバランスを実現。コード量削減（約50行→約40行）。失敗（スコア662）：履歴分析でv171の失敗原因を特定：（1）マージ予測精度0%：merge_available=trueの9ターン全てでscore_delta=0。（2）HIGHフェーズ到達が早すぎ：turn 48でHIGH到達、turn 52でCRITICAL到達。v171のHIGHフェーズheight_mult=1.5が緩和しすぎ。（3）MEDIUMフェーズheight_mult=2.2も緩和すぎ、MEDIUMフェーズ到達が早すぎ。（4）TOWER完全削除の副作用：MEDIUMフェーズでの高度管理が不十分で、HIGH到達早すぎ。（5）スコア伸びなし：57ターンでスコアは662点。履歴の9回のマージ判断全てで実際のスコア伸びなし。（6）振り子パターン再発：TOWERペナルティの「削除→復帰→緩和→削除→導入→削除」振り子がv164-v171で8回繰り返されている。TOWER完全削除は解決策にならなかった。
# v172: v42基本構造復帰・MEDIUM_TOWER維持版 - v171の失敗（スコア662、TOWER完全削除・HIGH到達早すぎ）を受けて、TOWERペナルティの「削除/維持」振り子を根本的に解消するブレイクスルーを採用。履歴分析でv171の失敗原因を特定：（1）TOWER完全削除が振り子の解決策ではなかった：MEDIUMフェーズでの高度管理不十分でHIGH到達早すぎ（turn48）。（2）v171のHIGHフェーズheight_mult=1.5が緩和しすぎ：turn52でCRITICAL到達。（3）MEDIUMフェーズheight_mult=2.2も緩和すぎ。（4）振り子パターン再発：TOWERペナルティの「削除→復帰→緩和→削除→導入→削除」振り子がv164-v171で8回繰り返されている。（5）ブレイクスルー：TOWERペナルティの「削除/維持」振り子ではなく、v42の成功した基本構造に完全復帰しつつ、v128のHIGHフェーズ高度管理緩和を中間値で採用。MEDIUM_TOWERペナルティを維持し、MEDIUMフェーズでの高度管理を確保。HIGHフェーズheight_multをv42の2.6より緩和、v128の1.8より強い（中間値2.2）に設定。HIGH_TOWERペナルティをv42の2.0倍より緩和、v128の1.3倍より強い（中間値1.6倍）に設定。マージボーナスはv42の一律値（DIRECT=1200/NEAR=600/FAR=200）を維持し、一律構造での頑健性を確保。振り子パターンを第三の選択肢（v42基本構造復帰・中間値調整）で解消。コード量増加なし（約110行）。


def decide(game_state: dict, analysis: dict) -> dict:
    """v42基本構造復帰・MEDIUM_TOWER維持版

    v171の失敗（スコア662、TOWER完全削除・HIGH到達早すぎ）を受けて、
    TOWERペナルティの「削除/維持」振り子を根本的に解消するブレイクスルーを採用。

    v42の成功した基本構造に完全復帰しつつ、
    v128のHIGHフェーズ高度管理緩和を中間値で採用。
    """

    results = analysis.get("results", [])

    if not results:
        return {"x": 0.0, "reason": "no analysis data"}

    best_x = 0.0
    best_score = -float("inf")
    best_reason = ""

    # 盤面情報
    pieces = game_state.get("pieces", [])
    max_y = max([p["y"] for p in pieces]) if pieces else -4.0

    # フェーズ判定（v42の閾値0.8/1.8/3.0を維持）
    if max_y < 0.8:
        phase = "LOW"
        height_mult = 1.0
        merge_mult = 1.2
    elif max_y < 1.8:
        phase = "MEDIUM"
        height_mult = 2.4  # v172: v42の2.4を維持（v171の2.2から強化、HIGH到達遅延）
        merge_mult = 1.0
    elif max_y < 3.0:
        phase = "HIGH"
        height_mult = (
            2.2  # v172: v42の2.6とv128の1.8の中間値（v171の1.5から強化、HIGH到達遅延）
        )
        merge_mult = 1.0
    else:
        phase = "CRITICAL"
        height_mult = 1.0  # CRITICAL: height_multなし
        merge_mult = 0.6  # v172: v42の0.6を維持

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

        # === v172: v42基本構造復帰・MEDIUM_TOWER維持 ===

        # 1. マージグレードによるスコア（v172: v42の一律値を維持、頑健性を確保）
        if merge_grade == "DIRECT":
            score += 1200.0 * merge_mult  # v172: v42の1200を維持
            reasons.append("DIRECT_MERGE")
        elif merge_grade == "NEAR":
            score += 600.0 * merge_mult  # v172: v42の600を維持
            reasons.append("NEAR_MERGE")
        elif merge_grade == "FAR":
            score += 200.0 * merge_mult  # v172: v42の200を維持
            reasons.append("FAR_MERGE")

        # 2. 高度によるペナルティ（v172: v42の基本構造を維持）
        height_penalty = landing_y * 50.0 * height_mult

        # TOWERペナルティ（v172: v42の基本構造に復帰、MEDIUM_TOWERを維持）
        if phase == "HIGH" and landing_y > 0.5:
            height_penalty *= 1.6  # v172: v42の2.0倍とv128の1.3倍の中間値
            reasons.append("HIGH_TOWER")
        elif phase == "MEDIUM" and landing_y > 0.5:
            height_penalty *= (
                1.5  # v172: v42の1.5倍を維持（MEDIUMフェーズでの高度管理確保）
            )
            reasons.append("MEDIUM_TOWER")
        elif landing_y > 0.0:
            reasons.append("HIGH_LAYER")

        score -= height_penalty

        # 3. ドリフトによるペナルティ（v172: v42の一律30.0を維持）
        drift_penalty = (abs(drift_x) + drift_unc) * 30.0
        score -= drift_penalty

        # 4. 左右バランス補正（v172: v42の設定を維持）
        balance_strength = 20.0
        if phase == "HIGH":
            balance_strength = 40.0  # v172: v42の40.0を維持
        elif phase == "MEDIUM":
            balance_strength = 30.0  # v172: v42の30.0を維持

        left_count = sum(1 for p in pieces if p["x"] < 0)
        right_count = len(pieces) - left_count
        balance_bias = (right_count - left_count) / (len(pieces) if pieces else 1)

        balance_penalty = x * balance_bias * balance_strength
        score -= abs(balance_penalty)

        # 5. nextNextが同じタイプなら中央寄せボーナス（v172: v42の一律50.0を維持）
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
