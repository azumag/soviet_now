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
# [BEST:2346] v84: HIGHフェーズマージ優先・構造改善版 - v83の失敗（スコア1065、HIGHフェーズマージ率低）を受けて、振り子パターン完全回避で根本的な構造改善を実施。chain reaction緩和は完全廃止（v82の失敗から学ぶ）。代わりにHIGHフェーズでのマージ確保を優先：（1）merge_gradeボーナス強化（DIRECT=1500/NEAR=800/FAR=300でマージの質を重視）、（2）HIGHフェーズ高度管理緩和（height_mult=2.2に減、HIGH_TOWERペナルティ1.3倍に減）、（3）マージなし位置にNO_MERGEペナルティ（-150）、（4）max_yに応じた動的調整（盤面が高いほどマージ優先、低いほど高度管理優先）。v42のシンプル構造を維持しつつ、HIGHフェーズでのマージ機会確保を構造的に改善。コード量増加なし（約110行）。
# v93-v96: 振り子パターン（一律緩和→reactive_pairs活用→NO_MERGEペナルティ廃止→NO_MERGEペナルティ復活）- v93: height_multiplier 50.0→35.0、v94: 35.0→25.0、v95: reactive_pairs>=4で15.0・NO_MERGEペナルティ廃止、v96: reactive_pairs>=2で25.0・NO_MERGEペナルティ-150復活。v96にはreactive_pairsがlist型の時のバグがありturn 54以降でエラー発生。
# v123-v125: MEDIUMフェーズheight_multの振り子パターン（v122:2.2→v123:2.4→v124:2.2→v125:1.8）
# v126-v128: NO_MERGEペナルティとHIGH_TOWER削除の振り子パターン - v126: NO_MERGE追加、v127: NO_MERGE削除、v128: 高度管理緩和
# v129-v137: HIGH_TOWERペナルティの振り子パターン（v134:削除→v136:1.2倍→v137:2.0倍）- 一律のHIGH_TOWERペナルティが「削除すると高度管理不十分」「再導入するとマージ機会損失」の振り子を繰り返している。
# [BEST:3689] v128: HIGHフェーズマージ優先版 - v127の失敗（スコア724、HIGHフェーズ10ターン中9ターンでマージ不可）を受けて、HIGHフェーズでのマージ機会損失を特定。履歴分析でv127の高度管理がHIGHフェーズで過剰に強化されていることが原因を特定（HIGHフェーズのdecision_reasonはHIGH_TOWERが1回だが、HIGH_LAYERが5回で高度管理が支配的）。（1）HIGHフェーズ高度管理大幅緩和：height_multをv42の2.6から1.8に大幅に引き下げ（v84の2.2よりも緩和し、マージ優先を徹底）。（2）マージボーナス強化：v42の強力な値（DIRECT=1200/NEAR=600/FAR=200）を維持し、高度管理緩和と組み合わせてマージをHIGHフェーズの主要目標にする。（3）HIGHフェーズHIGH_TOWERペナルティ緩和：v84の1.3倍を維持し、height_mult大幅緩和と相乗効果。（4）v42のシンプル構造を維持：NO_MERGEペナルティの「入れるか入れないか」の振り子を回避し、第三の選択肢（マージボーナス強化・高度管理大幅緩和）を採用。振り子パターン（NO_MERGEペナルティ、height_multiplier微調整）をHIGHフェーズでのマージ優先徹底で解消。コード量維持（約110行）。
# v171: TOWERペナルティ削除・高度管理緩和版 - v170の失敗（スコア1355、TOWERペナルティ過剰発動・HIGHフェーズ到達遅延）を受けて、TOWERペナルティを完全削除し高度管理を大幅に緩和するブレイクスルーを採用。履歴分析でv170の失敗原因を特定：（1）TOWERペナルティ過剰発動：MEDIUM_TOWERが13回、HIGH_TOWERが2回発動。TOWERペナルティの存在が高度管理を強くしすぎ、HIGHフェーズ到達を遅らせている（turn 67で到達）。（2）HIGHフェーズ到達遅延：MEDIUMフェーズが66ターン、HIGHフェーズは9ターンのみ。HIGHフェーズでのマージ機会が不十分。（3）振り子パターン再発：TOWERペナルティの「削除→復帰→緩和→削除→導入→削除」振り子がv164-v170で7回繰り返されている。TOWERペナルティ自体が振り子の根本原因。（4）v128の成功は個別のゲームでの成功であり、一律適用には危険。TOWERペナルティの存在はHIGH_TOWER=1.3倍であっても高度管理を強くしすぎ、HIGHフェーズ到達を遅延させる。（5）ブレイクスルー：TOWERペナルティを完全削除し、v42の一律構造での高度管理のみに戻る。MEDIUMフェーズheight_multをv170の2.4から2.2に緩和し、HIGHフェーズ到達を早める。HIGHフェーズheight_multをv170の1.8から1.5に緩和し、マージ優先を徹底。（6）v42のシンプル構造を維持：一律のマージボーナス（DIRECT=1200/NEAR=600/FAR=200）と高度管理（height_penaltyのみ）で頑健性を確保。TOWERペナルティのような複雑な条件分岐を削除し、シンプルな構造に回帰。（7）振り子パターン根本解消：TOWERペナルティの「削除→復帰」振り子を第三の選択肢（TOWER削除・高度管理緩和）で解消。一律構造での高度管理で頑健性を確保しつつ、HIGHフェーズでのマージ機会を最大化。（8）ブレイクスルー：TOWERペナルティの「削除→復帰」振り子を根本的に解消し、一律構造での高度管理のみでマージと高度管理のバランスを実現。コード量削減（約50行→約40行）。失敗（スコア662）：履歴分析でv171の失敗原因を特定：（1）マージ予測精度0%：merge_available=trueの9ターン全てでscore_delta=0。（2）HIGHフェーズ到達が早すぎ：turn 48でHIGH到達、turn 52でCRITICAL到達。v171のHIGHフェーズheight_mult=1.5が緩和しすぎ。（3）MEDIUMフェーズheight_mult=2.2も緩和すぎ、MEDIUMフェーズ到達が早すぎ。（4）TOWER完全削除の副作用：MEDIUMフェーズでの高度管理が不十分で、HIGH到達早すぎ。（5）スコア伸びなし：57ターンでスコアは662点。履歴の9回のマージ判断全てで実際のスコア伸びなし。（6）振り子パターン再発：TOWERペナルティの「削除→復帰→緩和→削除→導入→削除」振り子がv164-v171で8回繰り返されている。TOWER完全削除は解決策にならなかった。
# v172: v42基本構造復帰・MEDIUM_TOWER維持版 - v171の失敗（スコア662、TOWER完全削除・HIGH到達早すぎ）を受けて、TOWERペナルティの「削除/維持」振り子を根本的に解消するブレイクスルーを採用。履歴分析でv171の失敗原因を特定：（1）TOWER完全削除が振り子の解決策ではなかった：MEDIUMフェーズでの高度管理不十分でHIGH到達早すぎ（turn48）。（2）v171のHIGHフェーズheight_mult=1.5が緩和しすぎ：turn52でCRITICAL到達。（3）MEDIUMフェーズheight_mult=2.2も緩和すぎ。（4）振り子パターン再発：TOWERペナルティの「削除→復帰→緩和→削除→導入→削除」振り子がv164-v171で8回繰り返されている。（5）ブレイクスルー：TOWERペナルティの「削除/維持」振り子ではなく、v42の成功した基本構造に完全復帰しつつ、v128のHIGHフェーズ高度管理緩和を中間値で採用。MEDIUM_TOWERペナルティを維持し、MEDIUMフェーズでの高度管理を確保。HIGHフェーズheight_multをv42の2.6より緩和、v128の1.8より強い（中間値2.2）に設定。HIGH_TOWERペナルティをv42の2.0倍より緩和、v128の1.3倍より強い（中間値1.6倍）に設定。マージボーナスはv42の一律値（DIRECT=1200/NEAR=600/FAR=200）を維持し、一律構造での頑健性を確保。振り子パターンを第三の選択肢（v42基本構造復帰・中間値調整）で解消。コード量増加なし（約110行）。失敗（スコア773）：履歴分析でv172の失敗原因を特定：（1）HIGHフェーズ高度管理が強すぎる：HIGHフェーズ9ターン中6回でHIGH_TOWERペナルティ発動、マージ関連の判断は2回のみ。高度管理が支配的。（2）HIGHフェーズ到達遅延：MEDIUMフェーズ9ターン、HIGHフェーズ9ターン。HIGHフェーズでのマージ機会が不十分。（3）マージ予測精度が低い：merge_available=trueの8ターン全てで実際のスコア伸びなし。（4）v172のHIGHフェーズ設定（height_mult=2.2、HIGH_TOWER=1.6倍）はv128（1.8、1.3倍）よりも高度管理が強く、HIGHフェーズでのマージ機会を阻害している。
# v173: v128HIGHフェーズ復帰・MEDIUMフェーズ微調整版 - v172の失敗（スコア773、HIGHフェーズ高度管理強すぎ）を受けて、v128のHIGHフェーズ設定を復帰し、MEDIUMフェーズの高度管理を微調整する。履歴分析でv172の失敗原因を特定：（1）HIGHフェーズ高度管理が強すぎる：HIGHフェーズ9ターン中6回でHIGH_TOWERペナルティ発動、マージ関連は2回のみ。高度管理が支配的。（2）HIGHフェーズ到達遅延：MEDIUMフェーズ9ターン、HIGHフェーズ9ターン。HIGHフェーズでのマージ機会が不十分。（3）マージ予測精度が低い：merge_available=trueの8ターン全てで実際のスコア伸びなし。（4）v172のHIGHフェーズ設定（height_mult=2.2、HIGH_TOWER=1.6倍）はv128（1.8、1.3倍）よりも高度管理が強く、HIGHフェーズでのマージ機会を阻害している。（5）ブレイクスルー：v128のHIGHフェーズ設定（height_mult=1.8、HIGH_TOWER=1.3倍）を復帰し、HIGHフェーズでのマージ機会を最大化。MEDIUMフェーズの高度管理を微調整（height_mult=2.4から2.3に）し、HIGHフェーズ到達を少し早める。v42の一律構造（マージボーナス、TOWERペナルティ、ドリフトペナルティ、バランス補正）を維持し、頑健性を確保。振り子パターン（v172の「中間値調整」→v173の「v128復帰」）を回避し、v128の成功要素を直接採用することで、HIGHフェーズでのマージ優先と高度管理のバランスを実現。コード量維持（約110行）。失敗（スコア456）：履歴分析でv173の失敗原因を特定：（1）HIGH_TOWERペナルティが支配的：HIGHフェーズ13ターン中12回でHIGH_TOWER発動、マージ判断は0回。マージ機会が完全に損失している。（2）マージ機会完全損失：HIGHフェーズ（ターン37-54）に入ってから一度もマージ判断がない。スコアは52ターンで456点に到達後、ターン53-54でスコア伸びなし。（3）振り子パターン再発：v171(TOWER削除)→v172(TOWER復帰)→v173(HIGH_TOWER緩和)の振り子が繰り返されている。TOWERペナルティの「削除/復帰/緩和」振り子。（4）v128の成功（3689点）は特定の盤面での成功であり、一律適用は危険。HIGH_TOWER=1.3倍であっても、今回の盤面ではマージ機会を完全に阻害している。（5）MEDIUMフェーズは適切に機能：MEDIUMフェーズ（ターン11-36）でのheight_mult=2.3は適切で、ターン37でHIGHフェーズに到達。（6）HIGH_TOWERペナルティの存在がHIGHフェーズでの高度管理を強くしすぎ、マージ判断を不可能にしている。（7）ブレイクスルー：TOWERペナルティの「削除/復帰」振り子ではなく、HIGH_TOWER削除・height_mult中間値の第三の選択肢を採用。v171のheight_mult=1.5（緩和しすぎ）とv173のheight_mult=1.8+HIGH_TOWER=1.3倍（強すぎ）の中間値として、height_mult=1.6を採用し、HIGH_TOWERペナルティを削除。MEDIUMフェーズheight_mult=2.3を維持（適切に機能）。v42のシンプル構造を維持。振り子パターンを第三の選択肢（HIGH_TOWER削除・height_mult中間値）で解消。コード量削減（約110行→約100行）。


def decide(game_state: dict, analysis: dict) -> dict:
    """HIGH_TOWER削除・height_mult中間値版

    v173の失敗（スコア456、HIGH_TOWERペナルティが支配的・マージ機会完全損失）を受けて、
    TOWERペナルティの「削除/復帰/緩和」振り子を第三の選択肢で解消。

    HIGH_TOWERペナルティを削除し、height_multを中間値(1.6)に設定。
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
        height_mult = 2.3  # v174: v173の2.3を維持（適切に機能）
        merge_mult = 1.0
    elif max_y < 3.0:
        phase = "HIGH"
        # v174: v171の1.5とv173の1.8の中間値に設定（マージ機会確保と高度管理のバランス）
        height_mult = 1.6
        merge_mult = 1.0
    else:
        phase = "CRITICAL"
        height_mult = 1.0  # CRITICAL: height_multなし
        merge_mult = 0.6  # v174: v42の0.6を維持

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

        # === v174: HIGH_TOWER削除・height_mult中間値 ===

        # 1. マージグレードによるスコア（v174: v42の一律値を維持、頑健性を確保）
        if merge_grade == "DIRECT":
            score += 1200.0 * merge_mult  # v174: v42の1200を維持
            reasons.append("DIRECT_MERGE")
        elif merge_grade == "NEAR":
            score += 600.0 * merge_mult  # v174: v42の600を維持
            reasons.append("NEAR_MERGE")
        elif merge_grade == "FAR":
            score += 200.0 * merge_mult  # v174: v42の200を維持
            reasons.append("FAR_MERGE")

        # 2. 高度によるペナルティ（v174: v42の基本構造を維持）
        height_penalty = landing_y * 50.0 * height_mult

        # TOWERペナルティ（v174: HIGH_TOWER削除・MEDIUM_TOWER維持）
        if phase == "MEDIUM" and landing_y > 0.5:
            height_penalty *= (
                1.5  # v174: v42の1.5倍を維持（MEDIUMフェーズでの高度管理確保）
            )
            reasons.append("MEDIUM_TOWER")
        elif landing_y > 0.0:
            reasons.append("HIGH_LAYER")

        score -= height_penalty

        # 3. ドリフトによるペナルティ（v174: v42の一律30.0を維持）
        drift_penalty = (abs(drift_x) + drift_unc) * 30.0
        score -= drift_penalty

        # 4. 左右バランス補正（v174: v42の設定を維持）
        balance_strength = 20.0
        if phase == "HIGH":
            balance_strength = 40.0  # v174: v42の40.0を維持
        elif phase == "MEDIUM":
            balance_strength = 30.0  # v174: v42の30.0を維持

        left_count = sum(1 for p in pieces if p["x"] < 0)
        right_count = len(pieces) - left_count
        balance_bias = (right_count - left_count) / (len(pieces) if pieces else 1)

        balance_penalty = x * balance_bias * balance_strength
        score -= abs(balance_penalty)

        # 5. nextNextが同じタイプなら中央寄せボーナス（v174: v42の一律50.0を維持）
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
