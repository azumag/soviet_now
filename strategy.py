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
# v172-v174: TOWERペナルティ振り子パターン（復帰→緩和→削除→復帰）
# v175: MEDIUMフェーズreactive_pairs活用・HIGH_TOWER復帰版 - v174の失敗（スコア831、HIGHフェーズマージ判断不足）を受けて、v128のHIGHフェーズ設定（height_mult=1.8、HIGH_TOWER=1.3倍）に復帰し、MEDIUMフェーズでreactive_pairs>=3でheight_penaltyを10%緩和してマージ判断を促進。失敗（スコア760）：履歴分析で（1）MEDIUMフェーズ全16ターンでreactive_pairsの型エラー発生（reactive_pairsがlist型の時に`>=`比較が不可）、（2）HIGHフェーズでHIGH_TOWERが支配的（9回中9回でHIGH_TOWER発動）、マージ判断は3回のみで全てHIGH_TOWERと複合。（3）振り子パターン再発：v171(TOWER削除)→v172(TOWER復帰)→v173(HIGH_TOWER緩和)→v174(HIGH_TOWER削除)→v175(HIGH_TOWER復帰)。（4）reactive_pairsバグがv96と同様に再発。
# v176: HIGHフェーズ動的緩和・reactive_pairsバグ修正版 - v175の失敗（スコア760、MEDIUMフェーズ全ターンエラー・HIGHフェーズHIGH_TOWER支配的）を受けて、TOWERペナルティの「削除/復帰/緩和」振り子ではなく、merge_gradeに応じた動的height_penalty緩和の第三の選択肢を採用。（1）reactive_pairsバグ修正：list型の場合はlen()を使用（v96と同様の対策）。（2）HIGHフェーズでのマージ優先を徹底：merge_gradeに応じてheight_penaltyを動的に緩和（DIRECT:0.0/NEAR:0.5/FAR:0.8/NO_MERGE:0.3）し、DIRECT_MERGEではheight_multiplierなし、NO_MERGEでは盤面を低く保つ。（3）HIGH_TOWERペナルティ削除：HIGHフェーズではheight_penaltyの動的緩和のみでマージ優先を徹底し、HIGH_TOWERの「削除/復帰」振り子を回避。（4）v42の基本構造を維持：一律のマージボーナス（DIRECT=1200/NEAR=600/FAR=200）、MEDIUM_TOWERペナルティ、ドリフトペナルティ、バランス補正を維持。振り子パターン（HIGH_TOWER削除/復帰）を第三の選択肢（動的height_penalty緩和）で解消。コード量微増（約110行→約120行）。


def decide(game_state: dict, analysis: dict) -> dict:
    """HIGHフェーズ動的緩和・reactive_pairsバグ修正版

    v175の失敗（スコア760、MEDIUMフェーズ全ターンエラー・HIGHフェーズHIGH_TOWER支配的）を受けて、
    merge_gradeに応じた動的height_penalty緩和でHIGHフェーズでのマージ優先を徹底。
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

    # reactor情報
    reactor = analysis.get("reactor", {})
    reactive_pairs = reactor.get("reactive_pairs", 0)

    # v176バグ修正: reactive_pairsがlist型の場合はlen()を使用
    if isinstance(reactive_pairs, list):
        reactive_pairs = len(reactive_pairs)

    # フェーズ判定（v42の閾値0.8/1.8/3.0を維持）
    if max_y < 0.8:
        phase = "LOW"
        height_mult = 1.0
        merge_mult = 1.2
    elif max_y < 1.8:
        phase = "MEDIUM"
        height_mult = 2.4  # v176: v42の2.4を採用
        merge_mult = 1.0
    elif max_y < 3.0:
        phase = "HIGH"
        height_mult = 1.8  # v176: v128の1.8を維持
        merge_mult = 1.0
    else:
        phase = "CRITICAL"
        height_mult = 1.0  # CRITICAL: height_multなし
        merge_mult = 0.6  # v176: v42の0.6を維持

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

        # === v176: HIGHフェーズ動的緩和・reactive_pairsバグ修正 ===

        # 1. マージグレードによるスコア（v176: v42の一律値を維持）
        if merge_grade == "DIRECT":
            score += 1200.0 * merge_mult  # v176: v42の1200を維持
            reasons.append("DIRECT_MERGE")
        elif merge_grade == "NEAR":
            score += 600.0 * merge_mult  # v176: v42の600を維持
            reasons.append("NEAR_MERGE")
        elif merge_grade == "FAR":
            score += 200.0 * merge_mult  # v176: v42の200を維持
            reasons.append("FAR_MERGE")

        # 2. 高度によるペナルティ（v176: v42の基本構造を維持）
        height_penalty = landing_y * 50.0 * height_mult

        # TOWERペナルティ（v176: HIGHフェーズでは削除、MEDIUMフェーズではv42の設定を維持）
        if phase == "HIGH":
            # v176: HIGHフェーズではmerge_gradeに応じてheight_penaltyを動的に緩和
            if merge_grade == "DIRECT":
                height_relax = 0.0  # DIRECT_MERGE: height_multiplierなし
            elif merge_grade == "NEAR":
                height_relax = 0.5  # NEAR_MERGE: height_multiplierを50%緩和
            elif merge_grade == "FAR":
                height_relax = 0.8  # FAR_MERGE: height_multiplierを80%緩和
            else:  # NO_MERGE
                height_relax = (
                    0.3  # NO_MERGE: height_multiplierを30%緩和（盤面を低く保つ）
                )

            height_penalty *= height_relax
            if height_relax < 1.0:
                reasons.append(f"HEIGHT_RELAX_{height_relax:.1f}")
        elif phase == "MEDIUM" and landing_y > 0.5:
            height_penalty *= 1.5  # v176: v42の1.5倍を維持
            reasons.append("MEDIUM_TOWER")
        elif landing_y > 0.0:
            reasons.append("HIGH_LAYER")

        # MEDIUMフェーズでreactive_pairs>=3ならheight_penaltyを10%緩和（マージ判断促進）
        if phase == "MEDIUM" and reactive_pairs >= 3:
            height_penalty *= 0.9
            reasons.append("REACTIVE_BOOST")

        score -= height_penalty

        # 3. ドリフトによるペナルティ（v176: v42の一律30.0を維持）
        drift_penalty = (abs(drift_x) + drift_unc) * 30.0
        score -= drift_penalty

        # 4. 左右バランス補正（v176: v42の設定を維持）
        balance_strength = 20.0
        if phase == "HIGH":
            balance_strength = 40.0  # v176: v42の40.0を維持
        elif phase == "MEDIUM":
            balance_strength = 30.0  # v176: v42の30.0を維持

        left_count = sum(1 for p in pieces if p["x"] < 0)
        right_count = len(pieces) - left_count
        balance_bias = (right_count - left_count) / (len(pieces) if pieces else 1)

        balance_penalty = x * balance_bias * balance_strength
        score -= abs(balance_penalty)

        # 5. nextNextが同じタイプなら中央寄せボーナス（v176: v42の一律50.0を維持）
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
