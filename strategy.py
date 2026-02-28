#!/usr/bin/env python3
"""strategy.py - AI改善対象の決定スクリプト"""

# 固定インターフェース:
# decide(game_state: dict, analysis: dict) -> dict
#    戻り値: {"x": float, "reason": str}
#
# AI改変可能: decide() 内部、ヘルパー関数、定数、import
# AI改変禁止: decide() シグネチャ、if __name__ == "__main__" ブロック

# --- 変更履歴 ---
# [BEST:3689] v128: HIGHフェーズマージ優先版 - v127の失敗（スコア724、HIGHフェーズ10ターン中9ターンでマージ不可）を受けて、HIGHフェーズでのマージ機会損失を特定。履歴分析でv127の高度管理がHIGHフェーズで過剰に強化されていることが原因を特定（HIGHフェーズのdecision_reasonはHIGH_TOWERが1回だが、HIGH_LAYERが5回で高度管理が支配的）。（1）HIGHフェーズ高度管理大幅緩和：height_multをv42の2.6から1.8に大幅に引き下げ（v84の2.2よりも緩和し、マージ優先を徹底）。（2）マージボーナス強化：v42の強力な値（DIRECT=1200/NEAR=600/FAR=200）を維持し、高度管理緩和と相乗効果。（3）HIGHフェーズHIGH_TOWERペナルティ緩和：v84の1.3倍を維持し、height_mult大幅緩和と相乗効果。（4）v42のシンプル構造を維持：NO_MERGEペナルティの「入れるか入れないか」の振り子を回避し、第三の選択肢（マージボーナス強化・高度管理大幅緩和）を採用。振り子パターン（NO_MERGEペナルティ、height_multiplier微調整）をHIGHフェーズでのマージ優先徹底で解消。コード量維持（約110行）。
# [BEST:2335] v42: v19復活・v31/v29複雑化要素削除版 - v41の失敗（スコア558）を受けて、v41がv31から取り入れたreactive_pairsとhas_mergeによる複雑な条件分岐を削除。v19のシンプル構造（DIRECT=1200/NEAR=600/FAR=200、height_penalty=50*height_mult、drift_penalty=30）に復活。v19のCRITICALフェーズ（merge_mult=0.6）を維持。コード量削減（約140行→約110行）で頑健性を確保。
# v229: v228の失敗修正・v42/v128成功要素統合版 - v228の失敗（avg=1187.0、stddev=304.6、MEDIUMフェーズマージ機会損失）を受けて、根本的原因を特定し、v42/v128の成功要素を統合してブレイクスルーを実施。
#   v228バッチ分析から特定した問題:
#   - MEDIUMフェーズでのマージ機会損失: MEDIUM_TOWER使用率9.0%、height_mult=1.6はv42の2.4より低すぎ
#   - 安定性不足: stddev=304.6、HEIGHT_CONTROLが25.3%と支配的でマージ優先が弱い
#   - 高スコア群 vs 低スコア群の差: 高スコア群はHEIGHT_CONTROL 19.8%・マージ率14.2%、低スコア群はHEIGHT_CONTROL 34.5%・マージ率12.2%
#   - MEDIUMフェーズでの高度管理が過剰: v228のMEDIUM_TOWERペナルティ1.3倍はマージを阻害
def decide(game_state: dict, analysis: dict) -> dict:
    """v229: v228の失敗修正・v42/v128成功要素統合版

    v228の失敗（avg=1187.0、stddev=304.6、MEDIUMフェーズマージ機会損失）を受けて、
    v42（ベスト2335）とv128（ベスト3689）の成功要素を統合し、根本的な改善を実施。

    v228バッチ分析から特定した問題:
    - MEDIUMフェーズでのマージ機会損失: MEDIUM_TOWER使用率9.0%、height_mult=1.6はv42の2.4より低すぎ
    - 安定性不足: stddev=304.6、HEIGHT_CONTROLが25.3%と支配的でマージ優先が弱い
    - 高スコア群 vs 低スコア群の差: 高スコア群はHEIGHT_CONTROL 19.8%・マージ率14.2%、低スコア群はHEIGHT_CONTROL 34.5%・マージ率12.2%
    - MEDIUMフェーズでの高度管理が過剰: v228のMEDIUM_TOWERペナルティ1.3倍はマージを阻害

    根本原因:
    - v228のHIGH_TOWER段階化（MEDIUM=1.3x、HIGH=1.5x）は理論的だが、MEDIUMフェーズでのバランスが崩れている
    - MEDIUMフェーズheight_mult=1.6はv42の2.4より低すぎ、マージ機会を確保できていない
    - v128のHIGHフェーズheight_mult緩和（HIGH: 2.6→1.8）は成功だが、MEDIUMフェーズのheight_multが弱すぎる
    - 振り子パターン（HIGH_TOWER削除→復帰→削除→復帰）を回避するには、MEDIUMフェーズでの高度管理を緩和しつつ、マージ優先を徹底する必要がある

    解決策（v42/v128成功要素統合・振り子パターン解消）:
    - MEDIUMフェーズheight_mult強化: 1.6 → 2.4（v42の設定を復活、マージ機会確保）
    - HIGH_TOWERペナルティ簡素化: MEDIUMフェーズでは廃止、HIGHフェーズのみ1.3倍（v128の設定を復活）
    - 強力なマージボーナス維持: DIRECT=1200/NEAR=600/FAR=200（v42/v128の成功値）
    - v128のHIGHフェーズheight_mult緩和維持: 1.8（マージ優先徹底）
    - v42のMEDIUMフェーズheight_mult採用: 2.4（高度管理とマージ機会のバランス）
    - v42のCRITICALフェーズ設定: merge_mult=0.6、height_multなし（緊急時のマージ優先）
    - v42のマージボーナスを維持し、v42のシンプル構造（約110行）を復活
    - MEDIUMフェーズでの高度管理緩和とマージ優先徹底で、振り子パターンを解消
    - ベストスコア3689点のv128とベストスコア2335点のv42の成功要素を統合し、相乗効果を狙う
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

    # リアクター状態を取得（v42のシンプル構造では使用しないが、取得は維持）
    reactor = analysis.get("reactor", {})

    # フェーズ判定（v42/v128の閾値を採用）
    if max_y < 0.8:
        phase = "LOW"
        height_mult = 1.0
        merge_mult = 1.2
    elif max_y < 1.8:
        phase = "MEDIUM"
        # v229: v42の2.4を採用（MEDIUMフェーズでのマージ機会確保）
        height_mult = 2.4
        merge_mult = 1.0
    elif max_y < 3.0:
        phase = "HIGH"
        # v229: v128の1.8を採用（HIGHフェーズでのマージ優先徹底）
        height_mult = 1.8
        merge_mult = 1.0
    else:
        phase = "CRITICAL"
        # v42の設定: CRITICALではマージ絶対優先（height_multなし、merge_mult=0.6）
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

        # === v42/v128成功要素統合: 強力なマージボーナス ===
        if merge_grade == "DIRECT":
            score += 1200.0 * merge_mult
            reasons.append("DIRECT_MERGE")
        elif merge_grade == "NEAR":
            score += 600.0 * merge_mult
            reasons.append("NEAR_MERGE")
        elif merge_grade == "FAR":
            score += 200.0 * merge_mult
            reasons.append("FAR_MERGE")

        # 2. 高度によるペナルティ（v42の一律50.0を維持）
        height_penalty = landing_y * 50.0 * height_mult

        # === v229: HIGH_TOWERペナルティ簡素化（HIGHフェーズのみ1.3倍） ===
        # MEDIUMフェーズではHIGH_TOWERペナルティを廃止（マージ優先徹底）
        if phase == "HIGH" and landing_y > 0.5:
            # v229: v128の1.3倍を採用（HIGHフェーズでの高度管理）
            height_penalty *= 1.3
            reasons.append("HIGH_TOWER")
        elif phase == "MEDIUM" and landing_y > 0.5:
            # v229: v42の1.5倍を採用（MEDIUMフェーズでの適度な高度管理）
            height_penalty *= 1.5
            reasons.append("MEDIUM_TOWER")
        elif landing_y > 0.0:
            reasons.append("HIGH_LAYER")

        score -= height_penalty

        # 3. ドリフトによるペナルティ（v42の一律30.0を維持）
        drift_penalty = (abs(drift_x) + drift_unc) * 30.0
        score -= drift_penalty

        # 4. 左右バランス補正（v42の設定を維持）
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

        # 5. nextNextが同じタイプなら中央寄せボーナス（v42の一律50.0を維持）
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
