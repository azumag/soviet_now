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
# v93: HIGHフェーズ一律高度管理緩和版 - v92の失敗（スコア1251、HIGHフェーズでreactive_pairs=5-6あるのにマージ率0%、max_y=3.43でCRITICAL到達し即ゲームオーバー）を受けて、v84→v90→v91→v92の予測ベース振子パターンを断ち切るため、第三の選択肢を導入。履歴分析でv92のHIGHフェーズ高度管理（50.0*2.2*1.3=143.0）が厳しすぎて、マージボーナス（1500）を圧倒し、AIが「NO_MERGE_Pナルティを受けながら低い位置に落とす」ことを選択してマージ機会を逸していることを特定。v90/v91の予測ベース緩和は失敗したが、「HIGHフェーズで一律に高度管理を緩和する」というアイデアは正しかった。v93では：（1）HIGHフェーズのheight_multiplierを一律50.0から35.0に緩和（予測不要、一律緩和でマージ機会確保）、（2）v84の成功要素（merge_grade強化1500/800/300、NO_MERGEペナルティ-200、max_y動的調整）を維持、（3）予測ベース緩和（merge_gradeベース/has_mergeベース/reactive_pairsベース）は一切採用しない（振子完全回避）。v84のシンプル構造を維持しつつ、HIGHフェーズでのマージ機会確保を構造的に改善。コード量増加なし（約110行）。
# v94: HIGHフェーズ大幅緩和版 - v93の失敗（スコア1115、HIGHフェーズで「HIGH_TOWER_NO_MERGE_PENALTY」が12/13ターン連続、マージ機会逸失）を受けて、一律35.0への緩和では不十分で、まだ高度管理がマージボーナスを圧倒していることを特定。着地y=2.0でペナルティ≈200になり、マージボーナス(1500)に対してまだ強すぎる。v94では：（1）HIGHフェーズのheight_multiplierを35.0から25.0に大幅緩和（着地y=2.0でペナルティ≈110に削減）、（2）HIGH_TOWERペナルティ(1.3倍)を完全廃止（マージ機会最大化）、（3）LOW_MERGEボーナスを100から300に強化（低い位置でマージできるならさらに優先）。v84の成功要素（merge_grade強化1500/800/300、NO_MERGEペナルティ-200、max_y動的調整）を維持しつつ、HIGHフェーズでの高度管理の影響を最小限に抑え、マージボーナスを支配的にする。予測ベース緩和は一切採用せず、一律緩和で振子完全回避。コード量微減（約105行）。
# v95: reactor情報活用・NO_MERGEペナルティ廃止版 - v94の失敗（スコア1170、HIGHフェーズでHIGH_LAYER_NO_MERGE_PENALTYが69%支配的）を受けて、履歴分析でNO_MERGEペナルティ(-200)が強すぎて、AIが「NO_MERGE_PENALTYを受けながら低い位置に落とす」ことを選択してマージ機会を逸していることを特定。v93/v94の一律緩和では不十分であり、v31のreactive_pairs条件（>=3）を緩和（>=4にして頻発させる）してreactor情報活用を導入するが、v31と異なり予測ベースの高度管理緩和は一切しない。v95では：（1）NO_MERGEペナルティを完全廃止（NO_MERGE_PENALTYがあるとAIが「マージなし」を避けるため、予測ではNEAR_MERGEでも実際にはマージしない位置を選んでいる可能性がある）、（2）HIGHフェーズでreactive_pairs>=4の時、高度管理を大幅に緩和（height_multiplier=15.0、着地y=2.0でペナルティ≈66に削減）、（3）v84の成功要素（merge_grade強化1500/800/300、max_y動的調整）を維持、（4）予測ベース緩和（merge_gradeベース/has_mergeベース）は一切採用しない。v31の振り子パターンを回避し、v94の一律緩和を超える構造的改善を実現。コード量微増（約110行）。


def decide(game_state: dict, analysis: dict) -> dict:
    """reactor情報に応じてHIGHフェーズの高度管理を動的に緩和し、NO_MERGEペナルティを廃止"""

    results = analysis.get("results", [])

    if not results:
        return {"x": 0.0, "reason": "no analysis data"}

    best_x = 0.0
    best_score = -float("inf")
    best_reason = ""

    # 盤面情報
    pieces = game_state.get("pieces", [])
    max_y = max([p["y"] for p in pieces]) if pieces else -4.0

    # フェーズ判定（v95: v84の閾値0.8/1.8/3.0を維持）
    if max_y < 0.8:
        phase = "LOW"
        height_mult = 1.0
        merge_mult = 1.2
    elif max_y < 1.8:
        phase = "MEDIUM"
        height_mult = 2.2  # v95: v84の2.2を維持
        merge_mult = 1.0
    elif max_y < 3.0:
        phase = "HIGH"
        height_mult = 2.2  # v95: v84の2.2を維持
        merge_mult = 1.0
    else:
        phase = "CRITICAL"
        height_mult = 1.0  # CRITICAL: height_multなし
        merge_mult = 0.6  # v95: v84の0.6を維持

    # reactor情報（v95: reactive_pairsを取得）
    reactor = analysis.get("reactor", {})
    reactive_pairs = reactor.get("reactive_pairs", 0)

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

        # === v95: reactor情報活用・NO_MERGEペナルティ廃止版 ===

        # 1. マージグレードによるスコア（v95: v84の強化値を維持）
        if merge_grade == "DIRECT":
            score += 1500.0 * merge_mult  # v95: v84の1500を維持
            reasons.append("DIRECT_MERGE")
        elif merge_grade == "NEAR":
            score += 800.0 * merge_mult  # v95: v84の800を維持
            reasons.append("NEAR_MERGE")
        elif merge_grade == "FAR":
            score += 300.0 * merge_mult  # v95: v84の300を維持
            reasons.append("FAR_MERGE")

        # 2. 高度によるスコア（v95: reactor情報に応じて動的緩和）
        if phase == "CRITICAL":
            # CRITICALフェーズではheight_multiplier強化（v84の設定を維持）
            height_multiplier = 40.0
            height_penalty = landing_y * height_multiplier
            if landing_y > 1.0:
                reasons.append("CRITICAL_HEIGHT")
        elif phase == "HIGH":
            # v95: reactor情報に応じて動的高度管理緩和
            # v31のreactive_pairs>=3を>=4に緩和して頻発させる
            # 予測ベースの高度管理緩和は一切しない（v31の失敗から学ぶ）
            if len(reactive_pairs) >= 4:
                # v95: reactive_pairs>=4で高度管理を大幅に緩和
                height_multiplier = 15.0  # v95: v94の25.0からさらに緩和
                reasons.append("HIGH_LAYER_REACTIVE")
            else:
                # v95: reactive_pairs<4ではv94の設定を維持
                height_multiplier = 25.0

            height_penalty = landing_y * height_mult * height_multiplier

            # v95: HIGH_TOWERペナルティ(1.3倍)は廃止（v94の設定を維持）
            if landing_y > 0.0:
                reasons.append("HIGH_LAYER")
        else:
            # LOW, MEDIUMフェーズでは一律50.0
            height_multiplier = 50.0

            height_penalty = landing_y * height_mult * height_multiplier

            # 高盤面での追加ペナルティ（v95: v84の設定を維持）
            if phase == "MEDIUM" and landing_y > 0.5:
                height_penalty *= 1.5  # v95: v84の1.5倍を維持
                reasons.append("MEDIUM_TOWER")
            elif landing_y > 0.0:
                reasons.append("HIGH_LAYER")

        score -= height_penalty

        # 3. v95: NO_MERGEペナルティを完全廃止
        # NO_MERGE_PENALTYがあるとAIが「マージなし」を避けるため、
        # 予測ではNEAR_MERGEでも実際にはマージしない位置を選んでいる可能性がある
        # v94のNO_MERGE_PENALTY(-200)は廃止

        # 4. ドリフトによるペナルティ（v95: 一律で計算）
        drift_penalty = (abs(drift_x) + drift_unc) * 30.0
        score -= drift_penalty

        # 5. 左右バランス補正（v95: v84の設定を維持）
        balance_strength = 20.0
        if phase == "HIGH":
            balance_strength = 40.0  # v95: v84の40.0を維持
        elif phase == "MEDIUM":
            balance_strength = 30.0  # v95: v84の30.0を維持

        left_count = sum(1 for p in pieces if p["x"] < 0)
        right_count = len(pieces) - left_count
        balance_bias = (right_count - left_count) / (len(pieces) if pieces else 1)

        balance_penalty = x * balance_bias * balance_strength
        score -= abs(balance_penalty)

        # 6. nextNextが同じタイプなら中央寄せボーナス（v95: v84の設定を維持）
        if next_next_type == next_type:
            center_bonus = max(0, 1.0 - abs(x) / 2.0) * 50.0
            score += center_bonus
            reasons.append("NEXT_SAME")

        # 7. v95: max_yに応じた動的調整（v84の成功要素を維持）
        # 盤面が高いほどマージ優先、低いほど高度管理優先
        if phase in ["HIGH", "CRITICAL"]:
            if landing_y < 0.3 and merge_grade != "NO":
                # 低い位置でマージできるならさらに優先
                score += 300.0  # v95: v94の300を維持
                reasons.append("LOW_MERGE_BONUS")

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
