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
# v107: v84完全復帰・振り子解消版（再） - v105の失敗（スコア480、HIGHフェーズでreactive_pairs>=2が発動し続けてheight_multiplier=20.0に緩和、max_yが1.98→4.12へ急上昇）を受けて、堂々巡りしているNO_MERGE_PENALTYとreactive_pairsの振り子を完全解消。v95→v105で「NO_MERGE_PENALTY廃止→復活→廃除」と「reactive_pairs導入→不使用→導入」を繰り返していた。reactive_pairsによる動的高度管理緩和は、HIGHフェーズでの盤面急上昇を招く危険な仕組みであることがv105で証明された。v84の成功構造（merge_grade=1500/800/300強化、NO_MERGE_PENALTY=-150、HIGH_TOWERペナルティ1.3倍、reactive_pairs不使用、一律厳格な高度管理）に完全復帰。v84のベストスコア2346の成功要素は「シンプルかつ厳格な高度管理」であり、複雑な条件分岐は頑健性を損なうのみ。コード量削減（約90行）。
# v108: HIGH_TOWERペナルティ強化版 - v107の失敗（スコア817、HIGHフェーズでHIGH_TOWER_NO_MERGE_PENALTYが10.3%出現）を受けて、v107がv84構造に復帰していたがHIGH_TOWERペナルティが1.3倍と緩すぎたことが判明。履歴分析でHIGHフェーズのmax_yが2.08→2.93へ急上昇（turn 57→67）を特定。HIGH_TOWERペナルティをv84の1.3倍からv19/v42の2.0倍に強化し、高度管理の厳格さを確保。LOW_MERGE_BONUSロジックを盤面の高さに応じて動的に調整（max_yが高いほどマージ優先度を上げる）。v84の成功構造を維持しつつ、HIGHフェーズでの盤面上昇を抑制。
# v109: v42構造復帰・height_mult強化版 - v108の失敗（スコア2322、盤面がHIGHフェーズmax_y=2.83まで上昇）を受けて、v108がv84構造に復帰していたがMEDIUM/HIGHフェーズのheight_multが低すぎた（2.2）ことが判明。v108はv84のmerge_grade強化（1500/800/300）とNO_MERGE_PENALティ（-150）を維持しつつ、v19/v42のheight_mult（MEDIUM=2.4, HIGH=2.6）を採用していないため、盤面上昇を招いた。v42のシンプル構造（約110行）をベースに、v84のmerge_grade強化とNO_MERGE_PENALティを採用し、v19/v42のheight_multを採用。v84のHIGH_TOWERペナルティ1.3倍に戻す（2.0倍強化は過剰）。振り子パターン完全解消、構造的改善。コード量微増（約115行）。


def decide(game_state: dict, analysis: dict) -> dict:
    """v42構造をベースにv84のmerge_grade強化とv19/v42のheight_multを採用"""

    results = analysis.get("results", [])

    if not results:
        return {"x": 0.0, "reason": "no analysis data"}

    best_x = 0.0
    best_score = -float("inf")
    best_reason = ""

    # 盤面情報
    pieces = game_state.get("pieces", [])
    max_y = max([p["y"] for p in pieces]) if pieces else -4.0

    # フェーズ判定（v19/v42の閾値0.8/1.8/3.0を採用）
    if max_y < 0.8:
        phase = "LOW"
        height_mult = 1.0
        merge_mult = 1.2
    elif max_y < 1.8:
        phase = "MEDIUM"
        height_mult = 2.4  # v109: v19/v42の2.4を採用
        merge_mult = 1.0
    elif max_y < 3.0:
        phase = "HIGH"
        height_mult = 2.6  # v109: v19/v42の2.6を採用
        merge_mult = 1.0
    else:
        phase = "CRITICAL"
        height_mult = 1.0  # CRITICAL: height_multなし
        merge_mult = 0.6  # v19/v42の0.6を維持

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
        has_merge = result.get("has_merge", False)

        score = 0.0
        reasons = []

        # === v109: v42構造をベースにv84のmerge_grade強化を採用 ===

        # 1. マージグレードによるスコア（v84の強化値を採用）
        if merge_grade == "DIRECT":
            score += 1500.0 * merge_mult  # v84の1500を採用
            reasons.append("DIRECT_MERGE")
        elif merge_grade == "NEAR":
            score += 800.0 * merge_mult  # v84の800を採用
            reasons.append("NEAR_MERGE")
        elif merge_grade == "FAR":
            score += 300.0 * merge_mult  # v84の300を採用
            reasons.append("FAR_MERGE")

        # 2. 高度によるスコア（v42の一律50.0で厳格な高度管理）
        if phase == "CRITICAL":
            # CRITICALフェーズではheight_multiplier強化（v19/v42の設定を採用）
            height_multiplier = 40.0
            height_penalty = landing_y * height_multiplier
            if landing_y > 1.0:
                reasons.append("CRITICAL_HEIGHT")
        elif phase == "HIGH":
            # v109: v42の一律50.0で厳格な高度管理
            height_multiplier = 50.0
            height_penalty = landing_y * height_mult * height_multiplier

            # v109: HIGH_TOWERペナルティ1.3倍に戻す（2.0倍強化は過剰）
            if landing_y > 0.5:
                height_penalty *= 1.3  # v84の1.3倍を採用
                reasons.append("HIGH_TOWER")
            elif landing_y > 0.0:
                reasons.append("HIGH_LAYER")
        else:
            # LOW, MEDIUMフェーズでは一律50.0
            height_multiplier = 50.0
            height_penalty = landing_y * height_mult * height_multiplier

            # 高盤面での追加ペナルティ（v42の設定を採用）
            if phase == "MEDIUM" and landing_y > 0.5:
                height_penalty *= 1.5  # v42の1.5倍を採用
                reasons.append("MEDIUM_TOWER")
            elif landing_y > 0.0:
                reasons.append("HIGH_LAYER")

        score -= height_penalty

        # 3. NO_MERGEペナルティ（v84の-150を採用）
        if phase == "HIGH" and merge_grade == "NO":
            score -= 150.0  # v84の150を採用
            reasons.append("NO_MERGE_PENALTY")

        # 4. ドリフトによるペナルティ（一律で計算）
        drift_penalty = (abs(drift_x) + drift_unc) * 30.0
        score -= drift_penalty

        # 5. 左右バランス補正（v42の設定を採用）
        balance_strength = 20.0
        if phase == "HIGH":
            balance_strength = 40.0  # v42の40.0を採用
        elif phase == "MEDIUM":
            balance_strength = 30.0  # v42の30.0を採用

        left_count = sum(1 for p in pieces if p["x"] < 0)
        right_count = len(pieces) - left_count
        balance_bias = (right_count - left_count) / (len(pieces) if pieces else 1)

        balance_penalty = x * balance_bias * balance_strength
        score -= abs(balance_penalty)

        # 6. nextNextが同じタイプなら中央寄せボーナス（v42の設定を採用）
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
