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
# v162: v128構造維持・高度管理緩和版 - v161の失敗（スコア1063、HIGHフェーズマージ率0%・高度管理過剰）を受けて、v128の構造を維持しつつ高度管理を戦略的に緩和するブレイクスルーを採用。履歴分析でv161の失敗原因を特定：（1）HIGH_TOWERペナルティ（1.3倍）が4回発動、HIGHフェーズでの高度管理が過剰でマージ機会を阻害。（2）MEDIUM_TOWERペナルティ（1.5倍）が6回発動、MEDIUMフェーズでの高度管理も過剰で盤面上昇を抑制。（3）予測精度の問題：merge_available=trueの6ターン中、実際にスコアが伸びたのは3回（50%）。予測ベースの高度管理は危険。（4）v128（3689点）とのスコア差は大幅で、v128の設定の盲目的コピーは不適切。v128は個別のゲームでの成功であり、戦略設定の微調整が必要。（5）v161はv128の完全復帰だが、HIGHフェーズマージ率0%で1063点と低調。v128の設定はHIGH_TOWERペナルティ1.3倍とMEDIUM_TOWERペナルティ1.5倍が予測精度に依存しており、予測精度が低い状況下では過剰に機能する可能性がある。（1）MEDIUMフェーズ高度管理緩和：height_multをv128の2.4から2.3に緩和し、盤面上昇を少し促進。MEDIUM_TOWERペナルティを1.5倍から1.3倍に緩和し、MEDIUMフェーズでのスコア獲得を促進。（2）HIGHフェーズ高度管理緩和：height_multをv128の1.8から1.6に緩和し、マージ優先を徹底。HIGH_TOWERペナルティを1.3倍から1.1倍に大幅に緩和し、HIGHフェーズでのマージ機会を確保。（3）v128の構造維持：マージボーナス（DIRECT=1200/NEAR=600/FAR=200）、バランス補正（HIGH=40.0/MEDIUM=30.0）、中央寄せ（50.0）を維持。（4）予測精度の低さを考慮：予測ベースの高度管理（HIGH_TOWER、MEDIUM_TOWER）を緩和し、一律構造での高度管理を強化。（5）振り子パターン回避：v160（MEDIUM=2.2）→v161（MEDIUM=2.4）→v162（MEDIUM=2.3）の振り子ではなく、v128構造を維持しつつ戦略的緩和。（6）ブレイクスルー：予測精度が低い状況下では、v128のHIGH_TOWERペナルティ（1.3倍）とMEDIUM_TOWERペナルティ（1.5倍）は過剰に機能する。一律構造での高度管理（height_mult）をメインにし、TOWERペナルティを緩和して予測精度の低さを補償。MEDIUMフェーズで盤面上昇を促進し、HIGHフェーズでマージ機会を確保する。コード量維持（約60行）。失敗（スコア961）：履歴分析でv162の失敗原因を特定：（1）HIGH_TOWERペナルティが1回のみ（turn62）、高度管理緩和しすぎてHIGHフェーズでの高度管理が不十分。（2）MEDIUM_TOWERペナルティが14回発動、MEDIUMフェーズでの高度管理が不十分で盤面上昇を抑制できていない。（3）merge_available=trueの12ターンでscore_delta>0が0ターン、マージ予測が不正確。（4）CRITICALフェーズ（max_y=3.48）に到達してゲームオーバー、v128のCRITICAL到達（max_y=3.0）よりも高い。（5）振り子パターン：MEDIUMフェーズheight_mult（v160:2.2→v161:2.4→v162:2.3）、TOWERペナルティ（HIGH_TOWER: v128:1.3→v161:1.3→v162:1.1、MEDIUM_TOWER: v161:1.5→v162:1.3）。（6）v162の高度管理緩和は逆効果で、v42の頑健な高度管理構造に戻す必要がある。
# v163: v42頑健性復帰・振り子解消版 - v162の失敗（スコア961、高度管理緩和しすぎ）を受けて、v42の頑健な高度管理構造に完全復帰するブレイクスルーを採用。履歴分析でv162の失敗原因を特定：（1）振り子パターン：MEDIUMフェーズheight_mult（v160:2.2→v161:2.4→v162:2.3）、TOWERペナルティ（HIGH_TOWER: v128:1.3→v161:1.3→v162:1.1、MEDIUM_TOWER: v161:1.5→v162:1.3）。（2）HIGH_TOWERペナルティが1回のみ（turn62）、高度管理緩和しすぎてHIGHフェーズでの高度管理が不十分。（3）MEDIUM_TOWERペナルティが14回発動、MEDIUMフェーズでの高度管理が不十分で盤面上昇を抑制できていない。（4）merge_available=trueの12ターンでscore_delta>0が0ターン、マージ予測が不正確。予測ベースの高度管理緩和は危険。（5）v128（3689点）のHIGH_TOWER=1.3倍設定は個別のゲームでの成功であり、予測精度が低い状況下では過剰に緩和すると高度管理が機能しない。（1）v42頑健性復帰：MEDIUMフェーズheight_multをv128の2.4（v42と同じ）を維持し、HIGHフェーズheight_multをv42の2.6に復帰。v42の頑健な高度管理構造を完全復帰し、予測精度の低さを一律構造で補償。（2）TOWERペナルティv42復帰：HIGH_TOWERペナルティをv42の2.0倍に復帰し、MEDIUM_TOWERペナルティをv42の1.5倍に維持。v162のHIGH_TOWER=1.1倍の過剰緩和を修正し、TOWERペナルティの振り子を解消。（3）v128構造維持：MEDIUMフェーズheight_mult=2.4を維持し、HIGHフェーズ到達遅延戦略を継続。v128の成功要素（MEDIUMフェーズ高度管理強化）を維持。（4）v42シンプル構造：マージボーナス（DIRECT=1200/NEAR=600/FAR=200）、ドリフト（一律30.0）、バランス補正（HIGH=40.0/MEDIUM=30.0）、中央寄せ（一律50.0）を維持。（5）ブレイクスルー：振り子パターンを解消し、v42の頑健な高度管理構造に完全復帰。MEDIUMフェーズで高度管理を強化しHIGHフェーズ到達を遅延、HIGHフェーズでもv42の高度管理を強化しマージ機会を確保。予測精度の低さを一律構造で補償。コード量維持（約60行）。失敗（スコア959）：履歴分析でv163の失敗原因を特定：（1）HIGH_TOWERペナルティ（2.0倍）が0回発動、高度管理が強すぎてHIGHフェーズで発動すべきターンで発動していない（二重管理の競合）。（2）MEDIUM_TOWERペナルティ（1.5倍）が0回発動、MEDIUMフェーズでも二重管理の競合で高度管理が機能していない。（3）TOWERペナルティとheight_multiplierの二重管理メカニズムが振り子の根本原因：v162（TOWER弱化）→高度管理不十分、v163（TOWER強化）→二重管理の競合で機能不全。（4）v128（3689点）はTOWERペナルティ=1.3倍とheight_multiplier=1.8で成功したが、これはTOWERペナルティを相対的に弱くしてheight_multiplierに任せた実質的な一元管理。（5）予測精度の問題：merge_available=trueの12ターンでscore_delta>0が0ターン、マージ予測が不正確。
# v164: TOWERペナルティ完全削除・一元管理化版 - v163の失敗（スコア959、TOWERペナルティとheight_multiplierの二重管理競合）を受けて、TOWERペナルティを完全削除しheight_multiplierのみで高度管理を行うシンプルな構造に変更するブレイクスルーを採用。履歴分析でv163の失敗原因を特定：（1）振り子パターンの根本原因：TOWERペナルティとheight_multiplierの二重管理メカニズムが競合している。v162（TOWER弱化）→高度管理不十分、v163（TOWER強化）→二重管理の競合で機能不全。（2）v163のHIGH_TOWERペナルティ（2.0倍）が0回発動、height_multiplierが強すぎてTOWERペナルティ発動前に高度管理が機能していない。（3）v128（3689点）の成功の本質は、TOWERペナルティを相対的に弱く（1.3倍）してheight_multiplierに任せた実質的な一元管理であり、TOWERペナルティ自体がスコア向上に寄与したわけではない。（4）振り子パターン解消：TOWERペナルティを完全削除し、height_multiplierのみで高度管理を行う一元管理構造に変更することで、TOWERペナルティ強弱の振り子を根本的に解消。（5）height_multiplier調整：MEDIUMフェーズをv42の2.4から2.2に緩和し、HIGHフェーズをv42の2.6から2.2に大幅緩和。v128の1.8は極端すぎるため、v42とv128の中間の2.2を採用し、頑健性とマージ優先のバランスを確保。（6）v42シンプル構造維持：マージボーナス（DIRECT=1200/NEAR=600/FAR=200）、ドリフト（一律30.0）、バランス補正（HIGH=40.0/MEDIUM=30.0）、中央寄せ（一律50.0）を維持。（7）ブレイクスルー：二重管理メカニズムを一元管理に変更し、TOWERペナルティ強弱の振り子を根本的に解消。height_multiplier調整で高度管理とマージ優先のバランスを確保し、シンプルで頑健な構造を目指す。コード量削減（約60行→約50行）。


def decide(game_state: dict, analysis: dict) -> dict:
    """TOWERペナルティ完全削除・一元管理化版

    v163の失敗（スコア959、TOWERペナルティとheight_multiplierの二重管理競合）を受けて、
    TOWERペナルティを完全削除しheight_multiplierのみで高度管理を行うシンプルな構造に変更。

    v163のHIGH_TOWERペナルティ（2.0倍）が0回発動、
    TOWERペナルティとheight_multiplierの二重管理が振り子の根本原因。
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
        height_mult = 2.2  # v164: v42の2.4から2.2に緩和、MEDIUMフェーズでの高度管理緩和
        merge_mult = 1.0
    elif max_y < 3.0:
        phase = "HIGH"
        height_mult = 2.2  # v164: v42の2.6から2.2に大幅緩和、v128の1.8とv42の2.6の中間
        merge_mult = 1.0
    else:
        phase = "CRITICAL"
        height_mult = 1.0  # CRITICAL: height_multなし
        merge_mult = 0.6  # v164: v42の0.6を維持

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

        # === v164: TOWERペナルティ完全削除・一元管理化 ===

        # 1. マージグレードによるスコア（v164: v42の強力な値を維持）
        if merge_grade == "DIRECT":
            score += 1200.0 * merge_mult
            reasons.append("DIRECT_MERGE")
        elif merge_grade == "NEAR":
            score += 600.0 * merge_mult
            reasons.append("NEAR_MERGE")
        elif merge_grade == "FAR":
            score += 200.0 * merge_mult
            reasons.append("FAR_MERGE")

        # 2. 高度によるペナルティ（v164: height_multiplierのみで一元管理、TOWERペナルティ削除）
        height_penalty = landing_y * 50.0 * height_mult

        if landing_y > 0.0:
            reasons.append("HIGH_LAYER")

        score -= height_penalty

        # 3. ドリフトによるペナルティ（v164: v42の一律30.0を維持）
        drift_penalty = (abs(drift_x) + drift_unc) * 30.0
        score -= drift_penalty

        # 4. 左右バランス補正（v164: v42の設定を維持）
        balance_strength = 20.0
        if phase == "HIGH":
            balance_strength = 40.0  # v164: v42の40.0を維持
        elif phase == "MEDIUM":
            balance_strength = 30.0  # v164: v42の30.0を維持

        left_count = sum(1 for p in pieces if p["x"] < 0)
        right_count = len(pieces) - left_count
        balance_bias = (right_count - left_count) / (len(pieces) if pieces else 1)

        balance_penalty = x * balance_bias * balance_strength
        score -= abs(balance_penalty)

        # 5. nextNextが同じタイプなら中央寄せボーナス（v164: v42の一律50.0を維持）
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
