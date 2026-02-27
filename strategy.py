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
# v118: v42シンプル構造 + v84高度管理緩和版 - v117の失敗（スコア928、履歴でHIGH_TOWERペナルティ7回出現・max_yが2.87まで上昇、HEIGHT_CONTROLが50%を占める）を受けて、高度管理が過剰であることを特定し、v84のHIGHフェーズ高度管理緩和を採用。（1）HIGHフェーズのheight_multiplierを50.0から45.0に緩和（v42の厳格な高度管理からv84の緩和へ中間の値）。（2）HIGHフェーズのheight_multを2.6から2.4に緩和（v42の2.6からv84の2.2の中間）。（3）HIGH_TOWERペナルティを2.0倍から1.5倍に緩和（v42の2.0倍からv84の1.3倍の中間）。（4）merge_gradeはv42の値（1200/600/200）を維持（v116の失敗から学び、強化は過剰）。（5）動的調整は全廃（一律で計算）。v42のシンプル構造（約110行）をベースに、v84のHIGHフェーズ高度管理緩和を採用。コード量変更なし（約110行）。
# v119: HIGHフェーズ高度管理強緩和・マージ優先版 - v118の失敗（スコア1125、HIGHフェーズ17ターンでマージ成功率5.9%・HIGH_TOWERペナルティ7回出現・max_yが2.66まで上昇）を受けて、v118のHIGHフェーズ高度管理緩和が不十分であることを特定。履歴分析でHIGHフェーズではHEIGHT_CONTROLが支配的であり、マージ機会が失われていることを確認。v84のHIGHフェーズ高度管理緩和（height_multiplier 25.0、HIGH_TOWERペナルティ1.3倍）をより強く採用し、v42の成功要素（merge_gradeボーナス1200/600/200）を維持。（1）HIGHフェーズのheight_multiplierを45.0から30.0に大幅に緩和（v84の25.0に近づける）。（2）HIGHフェーズのheight_multを2.4から2.6に戻す（v42の2.6を維持）。（3）HIGH_TOWERペナルティを1.5倍から1.3倍に緩和（v84の1.3倍を採用）。（4）HIGHフェーズのmerge_multを1.0から1.2に強化（マージ優先）。（5）MEDIUMフェーズはv42の厳格な高度管理を維持（height_multiplier 50.0、height_mult 2.4）。v42のシンプル構造（約110行）をベースに、v84のHIGHフェーズ高度管理強緩和を採用。コード量変更なし（約110行）。
# v120: MEDIUM高度管理緩和・HIGHマージ強化版 - v119の失敗（スコア1616、HIGHフェーズ2ターンのみ・MEDIUMフェーズHEIGHT_CONTROL支配・Turn 88でCRITICAL到達max_y=3.19）を受けて、MEDIUMフェーズの高度管理が過剰であることを特定。履歴分析でHIGHフェーズはheight_mult=2.6が大きすぎてHIGH_TOWERペナルティが重くなり、マージよりも高度管理を優先していることを確認。v84のマージボーナス強化（DIRECT=1500/NEAR=800/FAR=300）を採用しつつ、v115の失敗（動的調整問題）を回避。（1）MEDIUMフェーズのheight_multiplierを50.0から35.0に緩和（v84/v42の50.0から緩和し、HIGHフェーズへの到達を少し遅らせるがマージ機会を増やす）。（2）HIGHフェーズのmerge_gradeをv84の1500/800/300に強化（v119の1200/600/200から強化、マージ優先）。（3）HIGHフェーズのheight_multiplierを30.0に維持（v119の緩和を維持）。（4）HIGHフェーズのheight_multを2.6から2.2に下げてHIGH_TOWERペナルティを軽減（マージ位置を選択しやすくする）。（5）HIGH_TOWERペナルティをv119の1.3倍に維持。（6）左右バランス補正を強化（MEDIUMフェーズでbalance_strengthを30.0から45.0、HIGHフェーズで40.0から60.0に強化、左右の片寄りを防ぐ）。（7）drift_penaltyとcenter_bonusは一律値に統一（フェーズ調整廃止）。v42のシンプル構造（約110行）をベースに、v84のマージボーナス強化とMEDIUMフェーズ高度管理緩和を採用。コード量微増（約115行）。


def decide(game_state: dict, analysis: dict) -> dict:
    """v42のシンプル構造をベースに、v84のマージボーナス強化とMEDIUMフェーズ高度管理緩和を採用。"""

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
        height_mult = 2.4  # v120: v42の2.4を維持
        merge_mult = 1.0
    elif max_y < 3.0:
        phase = "HIGH"
        height_mult = (
            2.2  # v120: v119の2.6からv84の2.2に下げてHIGH_TOWERペナルティを軽減
        )
        merge_mult = 1.0  # v120: merge_grade強化により1.0に戻す（ボーナス強化で十分）
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

        # 1. マージグレードによるスコア（v120: v84の1500/800/300を強化）
        if merge_grade == "DIRECT":
            score += 1500.0 * merge_mult  # v120: v119の1200からv84の1500に強化
            reasons.append("DIRECT_MERGE")
        elif merge_grade == "NEAR":
            score += 800.0 * merge_mult  # v120: v119の600からv84の800に強化
            reasons.append("NEAR_MERGE")
        elif merge_grade == "FAR":
            score += 300.0 * merge_mult  # v120: v119の200からv84の300に強化
            reasons.append("FAR_MERGE")

        # 2. 高度によるペナルティ（一律で計算、動的調整なし）
        if phase == "HIGH":
            height_penalty = landing_y * 30.0 * height_mult  # v120: v119の30.0を維持
        else:
            height_penalty = (
                landing_y * 35.0 * height_mult
            )  # v120: LOW/MEDIUM/CRITICALはv42の50.0から35.0に緩和

        # HIGH_TOWERペナルティ（v120: v119の1.3倍を維持）
        if phase == "HIGH" and landing_y > 0.5:
            height_penalty *= 1.3  # v120: v119の1.3倍を維持
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

        # 4. 左右バランス補正（v120: 強化して左右の片寄りを防ぐ）
        balance_strength = 20.0
        if phase == "HIGH":
            balance_strength = 60.0  # v120: v119の40.0から60.0に強化
        elif phase == "MEDIUM":
            balance_strength = 45.0  # v120: v119の30.0から45.0に強化

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
