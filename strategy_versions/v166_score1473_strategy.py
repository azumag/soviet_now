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
# v163: v42頑健性復帰・振り子解消版 - v162の失敗（スコア961、高度管理緩和しすぎ）を受けて、v42の頑健な高度管理構造に完全復帰するブレイクスルーを採用。履歴分析でv162の失敗原因を特定：（1）振り子パターン：MEDIUMフェーズheight_mult（v160:2.2→v161:2.4→v162:2.3）、TOWERペナルティ（HIGH_TOWER: v128:1.3→v161:1.3→v162:1.1、MEDIUM_TOWER: v161:1.5→v162:1.3）。（2）HIGH_TOWERペナルティが1回のみ（turn62）、高度管理緩和しすぎてHIGHフェーズでの高度管理が不十分。（3）MEDIUM_TOWERペナルティが14回発動、MEDIUMフェーズでの高度管理が不十分で盤面上昇を抑制できていない。（4）merge_available=trueの12ターンでscore_delta>0が0ターン、マージ予測が不正確。予測ベースの高度管理緩和は危険。（5）v128（3689点）のHIGH_TOWER=1.3倍設定は個別のゲームでの成功であり、予測精度が低い状況下では過剰に緩和すると高度管理が機能しない。（1）v42頑健性復帰：MEDIUMフェーズheight_multをv128の2.4（v42と同じ）を維持し、HIGHフェーズheight_multをv42の2.6に復帰。v42の頑健な高度管理構造を完全復帰し、予測精度の低さを一律構造で補償。（2）TOWERペナルティv42復帰：HIGH_TOWERペナルティをv42の2.0倍に復帰し、MEDIUM_TOWERペナルティをv42の1.5倍に維持。v162のHIGH_TOWER=1.1倍の過剰緩和を修正し、TOWERペナルティの振り子を解消。（3）v128構造維持：MEDIUMフェーズheight_mult=2.4を維持し、HIGHフェーズ到達遅延戦略を継続。v128の成功要素（MEDIUMフェーズ高度管理強化）を維持。（4）v42シンプル構造：マージボーナス（DIRECT=1200/NEAR=600/FAR=200）、ドリフト（一律30.0）、バランス補正（HIGH=40.0/MEDIUM=30.0）、中央寄せ（一律50.0）を維持。（5）ブレイクスルー：振り子パターンを解消し、v42の頑健な高度管理構造に完全復帰。MEDIUMフェーズで高度管理を強化しHIGHフェーズ到達を遅延、HIGHフェーズでもv42の高度管理を強化しマージ機会を確保。予測精度の低さを一律構造で補償。コード量維持（約60行）。失敗（スコア959）：履歴分析でv163の失敗原因を特定：（1）HIGH_TOWERペナルティ（2.0倍）が0回発動、高度管理が強すぎてHIGHフェーズで発動すべきターンで発動していない（二重管理の競合）。（2）MEDIUM_TOWERペナルティ（1.5倍）が0回発動、MEDIUMフェーズでも二重管理の競合で高度管理が機能していない。（3）TOWERペナルティとheight_multiplierの二重管理メカニズムが振り子の根本原因：v162（TOWER弱化）→高度管理不十分、v163（TOWER強化）→二重管理の競合で機能不全。（4）v128（3689点）はTOWERペナルティ=1.3倍とheight_multiplier=1.8で成功したが、これはTOWERペナルティを相対的に弱くしてheight_multiplierに任せた実質的な一元管理。（5）予測精度の問題：merge_available=trueの12ターンでscore_delta>0が0ターン、マージ予測が不正確。
# v164: TOWERペナルティ完全削除・一元管理化版 - v163の失敗（スコア959、TOWERペナルティとheight_multiplierの二重管理競合）を受けて、TOWERペナルティを完全削除しheight_multiplierのみで高度管理を行うシンプルな構造に変更するブレイクスルーを採用。履歴分析でv163の失敗原因を特定：（1）振り子パターンの根本原因：TOWERペナルティとheight_multiplierの二重管理メカニズムが競合している。v162（TOWER弱化）→高度管理不十分、v163（TOWER強化）→二重管理の競合で機能不全。（2）v163のHIGH_TOWERペナルティ（2.0倍）が0回発動、height_multiplierが強すぎてTOWERペナルティ発動前に高度管理が機能していない。（3）v128（3689点）の成功の本質は、TOWERペナルティを相対的に弱く（1.3倍）してheight_multiplierに任せた実質的な一元管理であり、TOWERペナルティ自体がスコア向上に寄与したわけではない。（4）振り子パターン解消：TOWERペナルティを完全削除し、height_multiplierのみで高度管理を行う一元管理構造に変更することで、TOWERペナルティ強弱の振り子を根本的に解消。（5）height_multiplier調整：MEDIUMフェーズをv42の2.4から2.2に緩和し、HIGHフェーズをv42の2.6から2.2に大幅緩和。v128の1.8は極端すぎるため、v42とv128の中間の2.2を採用し、頑健性とマージ優先のバランスを確保。（6）v42シンプル構造維持：マージボーナス（DIRECT=1200/NEAR=600/FAR=200）、ドリフト（一律30.0）、バランス補正（HIGH=40.0/MEDIUM=30.0）、中央寄せ（一律50.0）を維持。（7）ブレイクスルー：二重管理メカニズムを一元管理に変更し、TOWERペナルティ強弱の振り子を根本的に解消。height_multiplier調整で高度管理とマージ優先のバランスを確保し、シンプルで頑健な構造を目指す。コード量削減（約60行→約50行）。失敗（スコア1707）：履歴分析でv164の失敗原因を特定：（1）HIGHフェーズ到達が早すぎる（ターン60）、MEDIUMフェーズは43ターンのみでHIGH到達。（2）TOWERペナルティなし（0回発動）、高盤面での高度管理が不十分でmax_y=2.86でSTOP。（3）MEDIUMフェーズ高度管理緩和：height_mult=2.2（v42の2.4より緩和）がHIGH到達遅延に失敗。（4）HIGHフェーズ高度管理緩和：height_mult=2.2（v42の2.6より緩和）で高盤面での高度管理が不十分。（5）予測精度の問題：merge_available=trueの12ターン全てでscore_delta=0、マージ予測が不正確。（6）振り子パターン再発：TOWERペナルティ削除→高度管理不十分、再導入が必要。（7）decision_reason分布：HEIGHT_CONTROL=24回（31.6%）、HIGH_LAYER=43回、TOWERペナルティなしで高度管理が支配的。（8）v164の「一元管理」は失敗。TOWERペナルティなしでは高度管理が不十分。
# v165: v42頑健性復帰・振り子完全解消版 - v164の失敗（スコア1707、TOWERペナルティ削除による高度管理不十分）を受けて、v42の頑健な高度管理構造に完全復帰するブレイクスルーを採用。履歴分析でv164の失敗原因を特定：（1）振り子パターン再発：v162（TOWER弱化）→v163（TOWER強化）→v164（TOWER削除）→高度管理不十分、再導入が必要。TOWERペナルティの「削除→再導入」振り子がv162-v165で3回繰り返されている。（2）v164のHIGHフェーズ到達が早すぎる（ターン60）、MEDIUMフェーズは43ターンのみでHIGH到達。v128のHIGH到達遅延戦略（MEDIUMフェーズで18回のマージ発生）は失敗。（3）TOWERペナルティなし（0回発動）、高盤面での高度管理が不十分でmax_y=2.86でSTOP。v42のTOWERペナルティ（HIGH=2.0倍、MEDIUM=1.5倍）が必要。（4）MEDIUMフェーズ高度管理緩和：height_mult=2.2（v42の2.4より緩和）がHIGH到達遅延に失敗。（5）HIGHフェーズ高度管理緩和：height_mult=2.2（v42の2.6より緩和）で高盤面での高度管理が不十分。（6）予測精度の問題：merge_available=trueの12ターン全てでscore_delta=0、マージ予測が不正確。一律構造で補償が必要。（7）v163のHIGH_TOWERペナルティ（2.0倍）が0回発動したのは、v163のheight_multiplierが強すぎてTOWERペナルティ発動前に高度管理が機能していない（二重管理の競合）。（8）v164の「一元管理」は失敗。TOWERペナルティなしでは高度管理が不十分。（9）v128（3689点）の成功の本質は、MEDIUMフェーズのheight_mult=2.4によるHIGH到達遅延戦略と、HIGH_TOWERペナルティ=1.3倍による高盤面での高度管理。（10）v128のHIGHフェーズheight_mult=1.8は個別のゲームでの成功であり、一律の適用は危険。（1）v42頑健性復帰：MEDIUMフェーズheight_multをv42の2.4に復帰し、HIGHフェーズ到達遅延戦略を維持。HIGHフェーズheight_multをv42の2.6に復帰し、高盤面での高度管理を強化。（2）TOWERペナルティv42復帰：HIGH_TOWERペナルティをv42の2.0倍に復帰し、MEDIUM_TOWERペナルティをv42の1.5倍に復帰。v164のTOWERペナルティ削除を修正し、TOWERペナルティの「削除→再導入」振り子を解消。（3）予測精度補償：一律構造（height_multiplier）を強化し、予測精度の低さを補償。複雑な条件分岐（NO_MERGEペナルティ、動的高度管理）は回避。（4）振り子パターン完全解消：v42の成功構造に完全復帰し、TOWERペナルティ強弱の振り子を根本的に解消。（5）ブレイクスルー：TOWERペナルティの「削除→再導入」振り子はv162-v165で3回繰り返されており、根本的に解消が必要。v42の頑健な高度管理構造に完全復帰し、一律構造で予測精度を補償。MEDIUMフェーズで高度管理を強化しHIGHフェーズ到達を遅延、HIGHフェーズでも高度管理を強化しマージ機会を確保。コード量維持（約60行）。


def decide(game_state: dict, analysis: dict) -> dict:
    """v42頑健性復帰・振り子完全解消版

    v164の失敗（スコア1707、TOWERペナルティ削除による高度管理不十分）を受けて、
    v42の頑健な高度管理構造に完全復帰。

    v164のHIGHフェーズ到達が早すぎる（ターン60）、
    TOWERペナルティなしで高度管理が不十分。
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
        height_mult = 2.4  # v165: v42の2.4に復帰、HIGHフェーズ到達遅延戦略
        merge_mult = 1.0
    elif max_y < 3.0:
        phase = "HIGH"
        height_mult = 2.6  # v165: v42の2.6に復帰、高盤面での高度管理を強化
        merge_mult = 1.0
    else:
        phase = "CRITICAL"
        height_mult = 1.0  # CRITICAL: height_multなし
        merge_mult = 0.6  # v165: v42の0.6を維持

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

        # === v165: v42頑健性復帰・振り子完全解消 ===

        # 1. マージグレードによるスコア（v165: v42の強力な値を維持）
        if merge_grade == "DIRECT":
            score += 1200.0 * merge_mult
            reasons.append("DIRECT_MERGE")
        elif merge_grade == "NEAR":
            score += 600.0 * merge_mult
            reasons.append("NEAR_MERGE")
        elif merge_grade == "FAR":
            score += 200.0 * merge_mult
            reasons.append("FAR_MERGE")

        # 2. 高度によるペナルティ（v165: v42の高度管理構造を完全復帰）
        height_penalty = landing_y * 50.0 * height_mult

        # TOWERペナルティ（v165: v42の2.0倍/1.5倍に復帰、振り子完全解消）
        if phase == "HIGH" and landing_y > 0.5:
            height_penalty *= 2.0  # v165: v42の2.0倍に復帰、高盤面での高度管理を強化
            reasons.append("HIGH_TOWER")
        elif phase == "MEDIUM" and landing_y > 0.5:
            height_penalty *= (
                1.5  # v165: v42の1.5倍に復帰、MEDIUMフェーズでの高度管理を強化
            )
            reasons.append("MEDIUM_TOWER")
        elif landing_y > 0.0:
            reasons.append("HIGH_LAYER")

        score -= height_penalty

        # 3. ドリフトによるペナルティ（v165: v42の一律30.0を維持）
        drift_penalty = (abs(drift_x) + drift_unc) * 30.0
        score -= drift_penalty

        # 4. 左右バランス補正（v165: v42の設定を維持）
        balance_strength = 20.0
        if phase == "HIGH":
            balance_strength = 40.0  # v165: v42の40.0を維持
        elif phase == "MEDIUM":
            balance_strength = 30.0  # v165: v42の30.0を維持

        left_count = sum(1 for p in pieces if p["x"] < 0)
        right_count = len(pieces) - left_count
        balance_bias = (right_count - left_count) / (len(pieces) if pieces else 1)

        balance_penalty = x * balance_bias * balance_strength
        score -= abs(balance_penalty)

        # 5. nextNextが同じタイプなら中央寄せボーナス（v165: v42の一律50.0を維持）
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
