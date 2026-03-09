#!/usr/bin/env python3
"""strategy.py - AI改善対象の決定スクリプト"""

# 固定インターフェース:
# decide(game_state: dict, analysis: dict) -> dict
#    戻り値: {"x": float, "reason": str}
#
# AI改変可能: decide() 内部,ヘルパー関数,定数,import
# AI改変禁止: decide() シグネチャ,if __name__ == "__main__" ブロック

# --- 変更履歴 ---
# [BEST:2325] v19: CRITICALフェーズ導入版 - HIGHフェーズのheight_mult過剰を修正、CRITICALフェーズ（max_y>3.0）を新設。CRITICALでは併合絶対優先（merge_mult=0.6、height_multなし、height_penaltyシンプル化）。MEDIUMフェーズheight_mult微増（2.2→2.4）でHIGH到達遅延、HIGHフェーズheight_mult微減（2.8→2.6）で併合機会確保
# [BEST:2335] v42: v19復活・v31/v29複雑化要素削除版 - v41の失敗（スコア558）を受けて、v41がv31から取り入れたreactive_pairsとhas_mergeによる複雑な条件分岐を削除。v19のシンプル構造（DIRECT=1200/NEAR=600/FAR=200、height_penalty=50*height_mult、drift_penalty=30）に復活。v19のCRITICALフェーズ（merge_mult=0.6）を維持。コード量削減（約140行→約110行）で頑健性を確保
# v50-v64: has_merge/reactive_pairs条件の振り子パターンと閾値シャッフル
# [BEST:2346] v84: HIGHフェーズ併合優先・構造改善版 - v83の失敗（スコア1065、HIGHフェーズ併合率低）を受けて、振り子パターン完全回避で根本的な構造改善を実施。chain reaction緩和は完全廃止（v82の失敗から学ぶ）。代わりにHIGHフェーズでの併合確保を優先：（1）merge_gradeボーナス強化（DIRECT=1500/NEAR=800/FAR=300で併合の質を重視）、（2）HIGHフェーズ高度管理緩和（height_mult=2.2に減、HIGH_TOWERペナルティ1.3倍に減）、（3）併合なし位置にNO_MERGEペナルティ（-150）、（4）max_yに応じた動的調整（盤面が高いほど併合優先、低いほど高度管理優先）。v42のシンプル構造を維持しつつ、HIGHフェーズでの併合機会確保を構造的に改善。コード量増加なし（約110行）。
# v93-v96: 振り子パターン（一律緩和→reactive_pairs活用→NO_MERGEペナルティ廃止→NO_MERGEペナルティ復活）- v93: height_multiplier 50.0→35.0、v94: 35.0→25.0、v95: reactive_pairs>=4で15.0・NO_MERGEペナルティ廃止、v96: reactive_pairs>=2で25.0・NO_MERGEペナルティ-150復活。v96にはreactive_pairsがlist型の時のバグがありturn 54以降でエラー発生。
# v123-v125: MEDIUMフェーズheight_multの振り子パターン（v122:2.2→v123:2.4→v124:2.2→v125:1.8）
# [BEST:3689] v126: v125復活・HIGHフェーズ併合強化版 - v125の失敗（スコア1694、HIGHフェーズで併合機会損失）を受けて、v125の併合ボーナス縮小（DIRECT=600/NEAR=300/FAR=100）をv42の強力な値（DIRECT=1200/NEAR=600/FAR=200）に復活。MEDIUMフェーズのheight_mult振り子パターン解消のためにv42の2.4を採用し、v122の2.2（弱すぎ）とv125の1.8（強すぎ）の中間ではなく、v42の成功構造を採用。HIGHフェーズではv84の構造を参考に、height_multを2.4から2.2に緩和し、NO_MERGEペナルティ-150を導入して併合を強制的に促す。v42のシンプル構造（約110行）を維持しつつ、v84のHIGHフェーズ併合優先の成功要素を組み合わせる。振り子パターン（v122-v125のheight_mult振り子、v125の併合ボーナス縮小）を根本的に解消。コード量微増（約115行）。
# v127: NO_MERGEペナルティ削除・v42完全復帰版 - v126の失敗（スコア1291、HIGHフェーズ併合率0%）を受けて、NO_MERGEペナルティの有効性を検証。履歴分析でHIGHフェーズ9ターン中5ターンでNO_MERGEペナルティが適用されたが、いずれもmerge_available=false（実際には併合不可）。NO_MERGEペナルティは「併合予測が正確であること」を前提とするが、HIGHフェーズでは併合予測の精度が低いため、効果がなく、むしろ誤検出で低い位置を選び併合機会を損なっている。（1）NO_MERGEペナルティを完全削除：v42/v2335/v2325の成功構造に復帰し、併合予測を前提としないシンプルなアプローチに戻る。（2）v42の併合ボーナス（DIRECT=1200/NEAR=600/FAR=200）とheight_mult（MEDIUM=2.4/HIGH=2.6）を維持：併合は「ボーナス」として扱う。（3）HIGHフェーズ高度管理緩和のみをv84から採用：HIGH_TOWERペナルティを1.3倍に減（v42の2.0倍から）。v42のシンプル構造（約110行）を完全復帰し、v84のHIGHフェーズでの併合優先の成功要素を一部（高度管理緩和のみ）取り入れる。振り子パターン（NO_MERGEペナルティの追加・削除）を第三の選択肢（併合予測を前提としない）で解消。コード量削減（約110行）。
# v128: HIGHフェーズ併合優先版 - v127の失敗（スコア724、HIGHフェーズ10ターン中9ターンで併合不可）を受けて、HIGHフェーズでの併合機会損失を特定。履歴分析でv127の高度管理がHIGHフェーズで過剰に強化されていることが原因を特定（HIGHフェーズのdecision_reasonはHIGH_TOWERが1回だが、HIGH_LAYERが5回で高度管理が支配的）。（1）HIGHフェーズ高度管理大幅緩和：height_multをv42の2.6から1.8に大幅に引き下げ（v84の2.2よりも緩和し、併合優先を徹底）。（2）併合ボーナス強化：v42の強力な値（DIRECT=1200/NEAR=600/FAR=200）を維持し、高度管理緩和と組み合わせて併合をHIGHフェーズの主要目標にする。（3）HIGHフェーズHIGH_TOWERペナルティ緩和：v84の1.3倍を維持し、height_mult大幅緩和と相乗効果。（4）v42のシンプル構造を維持：NO_MERGEペナルティの「入れるか入れないか」の振り子を回避し、第三の選択肢（併合ボーナス強化・高度管理大幅緩和）を採用。振り子パターン（NO_MERGEペナルティ、height_multiplier微調整）をHIGHフェーズでの併合優先徹底で解消。コード量維持（約110行）。


def decide(game_state: dict, analysis: dict) -> dict:
    """HIGHフェーズで併合を優先し、高度管理を大幅に緩和。併合をHIGHフェーズの主要目標にする。"""

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
        height_mult = 2.4  # v128: v42の2.4を維持
        merge_mult = 1.0
    elif max_y < 3.0:
        phase = "HIGH"
        height_mult = 1.8  # v128: HIGHフェーズ高度管理大幅緩和（v42の2.6から1.8へ、併合優先を徹底）
        merge_mult = 1.0
    else:
        phase = "CRITICAL"
        height_mult = 1.0  # CRITICAL: height_multなし
        merge_mult = 0.6  # v128: v42の0.6を維持

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

        # === v128: HIGHフェーズ併合優先 ===

        # 1. 併合グレードによるスコア（v128: v42の強力な値を維持）
        if merge_grade == "DIRECT":
            score += 1200.0 * merge_mult
            reasons.append("DIRECT_MERGE")
        elif merge_grade == "NEAR":
            score += 600.0 * merge_mult
            reasons.append("NEAR_MERGE")
        elif merge_grade == "FAR":
            score += 200.0 * merge_mult
            reasons.append("FAR_MERGE")
        # v128: NO_MERGEペナルティの「入れるか入れないか」の振り子を回避し、第三の選択肢（高度管理大幅緩和）を採用

        # 2. 高度によるペナルティ（v128: HIGHフェーズ高度管理大幅緩和）
        height_penalty = landing_y * 50.0 * height_mult

        # HIGH_TOWERペナルティ（v128: v84の緩和設定を維持）
        if phase == "HIGH" and landing_y > 0.5:
            height_penalty *= 1.3  # v128: v84の1.3倍を採用（v42の2.0倍から減、height_mult大幅緩和と相乗効果）
            reasons.append("HIGH_TOWER")
        elif phase == "MEDIUM" and landing_y > 0.5:
            height_penalty *= 1.5  # v128: v42の1.5倍を維持
            reasons.append("MEDIUM_TOWER")
        elif landing_y > 0.0:
            reasons.append("HIGH_LAYER")

        score -= height_penalty

        # 3. ドリフトによるペナルティ（v128: v42の一律30.0を維持）
        drift_penalty = (abs(drift_x) + drift_unc) * 30.0
        score -= drift_penalty

        # 4. 左右バランス補正（v128: v42の設定を維持）
        balance_strength = 20.0
        if phase == "HIGH":
            balance_strength = 40.0  # v128: v42の40.0を維持
        elif phase == "MEDIUM":
            balance_strength = 30.0  # v128: v42の30.0を維持

        left_count = sum(1 for p in pieces if p["x"] < 0)
        right_count = len(pieces) - left_count
        balance_bias = (right_count - left_count) / (len(pieces) if pieces else 1)

        balance_penalty = x * balance_bias * balance_strength
        score -= abs(balance_penalty)

        # 5. nextNextが同じタイプなら中央寄せボーナス（v128: v42の一律50.0を維持）
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
