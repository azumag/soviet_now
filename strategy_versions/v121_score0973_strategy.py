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
# [BEST:2335] v42: v19復活・v31/v29複雑化要素削除版 - v41の失敗（スコア558）を受けて、v41がv31から取り入れたreactive_pairsとhas_mergeによる複雑な条件分岐を削除。v19のシンプル構造（DIRECT=1200/NEAR=600/FAR=200、height_penalty=50*height_mult、drift_penalty=30）に復活。v19のCRITICALフェーズ（merge_mult=0.6）を維持。コード量削減（約140行→約110行）で頑健性を確保
# v50-v64: has_merge/reactive_pairs条件の振り子パターンと閾値シャッフル
# [BEST:2346] v84: HIGHフェーズマージ優先・構造改善版 - v83の失敗（スコア1065、HIGHフェーズマージ率低）を受けて、振り子パターン完全回避で根本的な構造改善を実施。chain reaction緩和は完全廃止（v82の失敗から学ぶ）。代わりにHIGHフェーズでのマージ確保を優先：（1）merge_gradeボーナス強化（DIRECT=1500/NEAR=800/FAR=300でマージの質を重視）、（2）HIGHフェーズ高度管理緩和（height_mult=2.2に減、HIGH_TOWERペナルティ1.3倍に減）、（3）マージなし位置にNO_MERGEペナルティ（-150）、（4）max_yに応じた動的調整（盤面が高いほどマージ優先、低いほど高度管理優先）。v42のシンプル構造を維持しつつ、HIGHフェーズでのマージ機会確保を構造的に改善。コード量増加なし（約110行）。
# v93-v96: 振り子パターン（一律緩和→reactive_pairs活用→NO_MERGEペナルティ廃止→NO_MERGEペナルティ復活）- v93: height_multiplier 50.0→35.0、v94: 35.0→25.0、v95: reactive_pairs>=4で15.0・NO_MERGEペナルティ廃止、v96: reactive_pairs>=2で25.0・NO_MERGEペナルティ-150復活。v96にはreactive_pairsがlist型の時のバグがありturn 54以降でエラー発生。
# v119: HIGHフェーズ高度管理強緩和・マージ優先版 - v118の失敗（スコア1125、HIGHフェーズ17ターンでマージ成功率5.9%・HIGH_TOWERペナルティ7回出現・max_yが2.66まで上昇）を受けて、v118のHIGHフェーズ高度管理緩和が不十分であることを特定。履歴分析でHIGHフェーズではHEIGHT_CONTROLが支配的であり、マージ機会が失われていることを確認。v84のHIGHフェーズ高度管理緩和（height_multiplier 25.0、HIGH_TOWERペナルティ1.3倍）をより強く採用し、v42の成功要素（merge_gradeボーナス1200/600/200）を維持。（1）HIGHフェーズのheight_multiplierを45.0から30.0に大幅に緩和（v84の25.0に近づける）。（2）HIGHフェーズのheight_multを2.4から2.6に戻す（v42の2.6を維持）。（3）HIGH_TOWERペナルティを1.5倍から1.3倍に緩和（v84の1.3倍を採用）。（4）HIGHフェーズのmerge_multを1.0から1.2に強化（マージ優先）。（5）MEDIUMフェーズはv42の厳格な高度管理を維持（height_multiplier 50.0、height_mult 2.4）。v42のシンプル構造（約110行）をベースに、v84のHIGHフェーズ高度管理強緩和を採用。コード量変更なし（約110行）。
# v120: MEDIUM高度管理緩和・HIGHマージ強化版 - v119の失敗（スコア1616、HIGHフェーズ2ターンのみ・MEDIUMフェーズHEIGHT_CONTROL支配・Turn 88でCRITICAL到達max_y=3.19）を受けて、MEDIUMフェーズの高度管理が過剰であることを特定。履歴分析でHIGHフェーズはheight_mult=2.6が大きすぎてHIGH_TOWERペナルティが重くなり、マージよりも高度管理を優先していることを確認。v84のマージボーナス強化（DIRECT=1500/NEAR=800/FAR=300）を採用しつつ、v115の失敗（動的調整問題）を回避。（1）MEDIUMフェーズのheight_multiplierを50.0から35.0に緩和（v84/v42の50.0から緩和し、HIGHフェーズへの到達を少し遅らせるがマージ機会を増やす）。（2）HIGHフェーズのmerge_gradeをv84の1500/800/300に強化（v119の1200/600/200から強化、マージ優先）。（3）HIGHフェーズのheight_multiplierを30.0に維持（v119の緩和を維持）。（4）HIGHフェーズのheight_multを2.6から2.2に下げてHIGH_TOWERペナルティを軽減（マージ位置を選択しやすくする）。（5）HIGH_TOWERペナルティをv119の1.3倍に維持。（6）左右バランス補正を強化（MEDIUMフェーズでbalance_strengthを30.0から45.0、HIGHフェーズで40.0から60.0に強化、左右の片寄りを防ぐ）。（7）drift_penaltyとcenter_bonusは一律値に統一（フェーズ調整廃止）。v42のシンプル構造（約110行）をベースに、v84のマージボーナス強化とMEDIUMフェーズ高度管理緩和を採用。コード量微増（約115行）。
# v121: v42完全復帰・HIGH_TOWER強化版 - v120の失敗（スコア727、Turn 6でCRITICAL到達max_y=3.08・履歴でマージ予測NEAR_MERGEが2回失敗・HIGH_TOWERペナルティ支配的）を受けて、マージボーナス強化が予測ミスを誘発し、HIGHフェーズ高度管理緩和がCRITICAL到達を早めていることを特定。振り子パターン回避のため、v84/v120のマージボーナス強化（1500/800/300）とHIGHフェーズheight_multiplier緩和（25.0-35.0）を完全削除。v42のシンプル構造に完全復帰：（1）merge_gradeをv42の値（1200/600/200）に戻す（予測ミス時のペナルティ軽減）。（2）HIGHフェーズのheight_multiplierを50.0に戻す（v42の厳格な高度管理でCRITICAL到達を遅延）。（3）HIGH_TOWERペナルティを2.0倍に戻す（v120の1.3倍から強化）。（4）height_multiplierの一律調整を廃止（v120の35.0ではなく一律50.0）。（5）balance_strengthとcenter_bonusは一律値に統一（v120の一律化維持）。v42のシンプル構造（約110行）を完全復帰。コード量削減（約105行）。


def decide(game_state: dict, analysis: dict) -> dict:
    """v42のシンプル構造に完全復帰し、予測ミスに頑健な頑健な戦略を採用。"""

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
        height_mult = 2.4  # v121: v42の2.4を維持
        merge_mult = 1.0
    elif max_y < 3.0:
        phase = "HIGH"
        height_mult = 2.6  # v121: v42の2.6を維持
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

        # 1. マージグレードによるスコア（v121: v42の値を維持）
        if merge_grade == "DIRECT":
            score += 1200.0 * merge_mult  # v121: v120の1500からv42の1200に戻す
            reasons.append("DIRECT_MERGE")
        elif merge_grade == "NEAR":
            score += 600.0 * merge_mult  # v121: v120の800からv42の600に戻す
            reasons.append("NEAR_MERGE")
        elif merge_grade == "FAR":
            score += 200.0 * merge_mult  # v121: v120の300からv42の200に戻す
            reasons.append("FAR_MERGE")

        # 2. 高度によるペナルティ（一律で計算）
        height_penalty = (
            landing_y * 50.0 * height_mult
        )  # v121: v120の35.0からv42の50.0に戻す

        # HIGH_TOWERペナルティ（v121: v42の2.0倍を維持）
        if phase == "HIGH" and landing_y > 0.5:
            height_penalty *= 2.0  # v121: v120の1.3倍からv42の2.0倍に強化
            reasons.append("HIGH_TOWER")
        elif phase == "MEDIUM" and landing_y > 0.5:
            height_penalty *= 1.5
            reasons.append("MEDIUM_TOWER")
        elif landing_y > 0.0:
            reasons.append("HIGH_LAYER")

        score -= height_penalty

        # 3. ドリフトによるペナルティ（一律30.0）
        drift_penalty = (abs(drift_x) + drift_unc) * 30.0
        score -= drift_penalty

        # 4. 左右バランス補正（一律）
        balance_strength = 20.0  # v121: v120のフェーズ調整を廃止、一律値に統一

        left_count = sum(1 for p in pieces if p["x"] < 0)
        right_count = len(pieces) - left_count
        balance_bias = (right_count - left_count) / (len(pieces) if pieces else 1)

        balance_penalty = x * balance_bias * balance_strength
        score -= abs(balance_penalty)

        # 5. nextNextが同じタイプなら中央寄せボーナス（一律50.0）
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
