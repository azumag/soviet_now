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
# v123-v125: MEDIUMフェーズheight_multの振り子パターン（v122:2.2→v123:2.4→v124:2.2→v125:1.8）
# v126-v128: NO_MERGEペナルティとHIGH_TOWER削除の振り子パターン - v126: NO_MERGE追加、v127: NO_MERGE削除、v128: 高度管理緩和
# v129-v137: HIGH_TOWERペナルティの振り子パターン（v134:削除→v136:1.2倍→v137:2.0倍）- 一律のHIGH_TOWERペナルティが「削除すると高度管理不十分」「再導入するとマージ機会損失」の振り子を繰り返している。
# v153: v128完全復帰・予測前提回避版 - v152の失敗（スコア704、HIGH_TOWERペナルティとバランス補正の振り子パターン復活・v128からの逸脱が原因）を受けて、v128の成功設定を完全復帰するブレイクスルーを採用。履歴分析でv152の失敗原因を特定：（1）振り子パターン確定：HIGH_TOWERはv150削除→v151復帰→v152削除、バランス補正はv150弱体化→v151復帰→v152強化。予測に依存したパラメータ調整が振り子を繰り返している。（2）v152の改善はv128から逸脱：height_mult=1.6、HIGH_TOWER=1.0倍、バランス補正=50.0/40.0、中央寄せ=60.0はv128（1.8、1.3倍、40.0/30.0、50.0）から完全に逸脱。（3）「v128をさらに緩和すればスコアが上がる」という前提が間違い：v128は3689点を達成しており、これ以上の緩和は不要。v152はv128から逸脱したことで失敗。（4）予測精度は依然として低い：merge_available=trueの5ターン中1回しかマージが発生していない（20%）。（5）バランス補正強化の副作用：50.0/40.0に強化したが、中央寄せを強制しすぎてマージ可能な位置を回避。turns 59-66で連続HIGH_TOWER、xが-3.0, -2.8, 3.0, 1.0と極端に振れる。（6）v128の成功要因：予測を前提としない一律構造でv42の頑健性とバランス補正の安全装置機能を維持し、HIGHフェーズ高度管理緩和（height_mult=1.8、HIGH_TOWER=1.3倍）でマージ優先のバランスをとった。（1）v128成功設定の完全復帰：height_mult=1.8、HIGH_TOWER=1.3倍、バランス補正=40.0/30.0、中央寄せ=50.0を採用し、予測を前提としない一律構造に復帰。（2）予測前提回避：予測に依存したパラメータ調整（緩和・強化）をやめ、一律構造で頑健性を確保。（3）振り子パターン解消：HIGH_TOWERとバランス補正の「削除↔復帰↔強化」の振り子を、v128成功設定の完全復帰で解消。（4）ブレイクスルー：v128は3689点を達成しており、これ以上の緩和や強化は不要。v152のv128からの逸脱が失敗の主因。予測精度が低い状況下では、一律構造でv42の頑健性とバランス補正の安全装置機能を維持し、v128の高度管理緩和でマージ優先のバランスをとるのが最適。コード量削減（約65行→約60行）。
# v154: 履歴データベース動的高度管理版 - v153の失敗（スコア1405、v128設定完全復帰だがHIGHフェーズマージ率低）を受けて、履歴データに基づいた動的高度管理を導入するブレイクスルーを採用。履歴分析でv153の失敗原因を特定：（1）v128履歴からマージが発生する高度範囲を特定：マージは主にmax_y 1.0-2.5で発生（特に1.5-2.0付近で多く発生）。（2）予測に依存した戦略は失敗：reactive_pairs、merge_available等の予測ベース戦略は全て失敗。（3）履歴データは客観的事実：過去のマージ発生位置は客観的であり、予測精度に依存しない。（4）v128のheight_mult=1.8は全フェーズ一律：マージが発生しやすい高度（1.0-2.5）でも、一律に高度管理ペナルティを適用していたためマージ機会損失。（5）HIGH_TOWERペナルティの1.3倍：高い位置でのマージを回避している。（1）履歴データベース高度管理：v128履歴から「マージがよく発生する高度範囲（1.0-2.5）」を特定し、その範囲内では高度管理ペナルティを軽減する動的高度管理を導入。予測に依存せず、客観的事実（履歴データ）に基づく。（2）CRITICALフェーズのマージ絶対優先：height_multを1.0に設定し、高度管理ペナルティを最小化してマージ機会最大化。（3）HIGHフェーズ高度管理動的調整：landing_yが1.0-2.5の場合、height_multiplierを0.5に軽減（履歴データベースからマージがよく発生する高度範囲）。landing_yが2.5-3.0の場合、height_multiplierを2.0に強化（マージが発生しにくい高度範囲）。（4）HIGH_TOWERペナルティ調整：HIGHフェーズでlanding_y>0.5の場合、height_multiplierを1.3倍にする（v128設定）が、履歴データベース範囲（1.0-2.5）内では1.0倍に軽減（マージ優先）。（5）バランス補正強化：HIGHフェーズのbalance_strengthをv128の40.0から50.0に強化し、マージ可能な位置を確保。（6）中央寄せボーナス強化：一律50.0から60.0に強化し、盤面の左右不均衡を是正。（7）ブレイクスルー：予測に依存せず、履歴データ（客観的事実）に基づいた動的高度管理でマージ機会確保。v128の成功要素（v42頑健構造）を維持しつつ、履歴データベースで高度管理を最適化。予測精度が低い状況下でも、履歴データは客観的であり信頼性が高い。コード量微増（約60行→約70行）。失敗（スコア1258）：履歴分析でv154の失敗原因を特定：（1）履歴データベースの過剰信頼：予測精度が低い状況下では、履歴データ（1.0-2.5）を信用した高度管理緩和は失敗。履歴データは客観的だが、個別のゲームでは適用できない可能性がある。（2）HIGHフェーズマージ率：16ターン中2回（12.5%）で、v128（3689点）と比較して大幅に低い。（3）複雑な動的高度管理：MERGE_FAVOR_ZONE/MERGE_UNLIKELY_ZONEの条件分岐は、予測ミスの影響を増幅。（4）バランス補正の強化（50.0）：中央寄せを強制しすぎてマージ可能な位置を回避。turns 59-82でHIGH_TOWER連続、xが極端に振れる。（5）予測精度は依然として低い：merge_available=trueの13ターン中、実際にスコアが伸びたのはturns 26, 40, 74, 83の4回のみ（31%）。
# v155: v128復帰・重心補正導入版 - v154の失敗（スコア1258、履歴データベース失敗・HIGHフェーズマージ率低・バランス補正強化副作用）を受けて、履歴データベースを削除し、重心補正を導入するブレイクスルーを採用。履歴分析でv154の失敗原因を特定：（1）履歴データベースの過剰信頼：予測精度が低い状況下では、履歴データ（1.0-2.5）を信用した高度管理緩和は失敗。個別のゲームでは履歴データが適用できない可能性がある。（2）複雑な動的高度管理：MERGE_FAVOR_ZONE/MERGE_UNLIKELY_ZONEの条件分岐は、予測ミスの影響を増幅し、一律構造の頑健性を損なう。（3）バランス補正の強化（50.0）：中央寄せを強制しすぎてマージ可能な位置を回避。v128の40.0の方が適切。（4）v128の成功設定：height_mult=1.8、HIGH_TOWER=1.3倍、バランス補正=40.0/30.0、中央寄せ=50.0は3689点を達成。（5）予測に依存しない一律構造の維持が重要：履歴データベースのような複雑な条件分岐は、予測精度が低い状況下では危険。（1）v128成功設定への復帰：height_mult=1.6、HIGH_TOWER=1.3倍、バランス補正=40.0/30.0、中央寄せ=50.0を採用し、一律構造に復帰。（2）履歴データベース削除：MERGE_FAVOR_ZONE/MERGE_UNLIKELY_ZONEの動的高度管理を完全削除。一律構造で頑健性を確保。（3）重心補正導入：X重心（左右バランス）とY重心（高さバランス）の両方を考慮。X重心は0に近いほど、Y重心は低いほどボーナス。予測に依存せず、物理的事実に基づく盤面の均衡性を評価。（4）HIGHフェーズ高度管理微緩和：height_multをv128の1.8から1.6に微調整し、マージ機会を確保。HIGH_TOWERペナルティはv128の1.3倍を維持。（5）ブレイクスルー：予測精度が低い状況下では、予測に依存した動的高度管理（履歴データベース）は危険。一律構造でv42の頑健性とv128の成功要素を維持し、重心補正という新しい予測非依存のアプローチで盤面の均衡性を改善。重心補正はX重心とY重心の両方を考慮し、盤面の空間的均衡性を客観的に評価。予測精度に依存せず、物理的事実（全ピースの位置）に基づくため信頼性が高い。コード量微増（約70行→約80行）。


def decide(game_state: dict, analysis: dict) -> dict:
    """v128成功設定への復帰と重心補正導入。予測に依存した履歴データベースを削除し、X重心とY重心の両方を考慮した予測非依存のアプローチで盤面の均衡性を改善。"""

    results = analysis.get("results", [])

    if not results:
        return {"x": 0.0, "reason": "no analysis data"}

    best_x = 0.0
    best_score = -float("inf")
    best_reason = ""

    # 盤面情報
    pieces = game_state.get("pieces", [])
    max_y = max([p["y"] for p in pieces]) if pieces else -4.0

    # フェーズ判定（v128の閾値を維持）
    if max_y < 0.8:
        phase = "LOW"
        height_mult = 1.0
        merge_mult = 1.2
    elif max_y < 1.8:
        phase = "MEDIUM"
        height_mult = 2.4  # v155: v42の2.4を維持（MEDIUMフェーズの安定性確保）
        merge_mult = 1.0
    elif max_y < 3.0:
        phase = "HIGH"
        height_mult = 1.6  # v155: v128の1.8から1.6に微緩和、マージ機会を確保
        merge_mult = 1.0
    else:
        phase = "CRITICAL"
        height_mult = 1.0  # CRITICAL: height_multなし
        merge_mult = 0.6  # v155: v42の0.6を維持

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

        # === v155: v128復帰・重心補正導入 ===

        # 1. マージグレードによるスコア（v155: v42の値を維持）
        if merge_grade == "DIRECT":
            score += 1200.0 * merge_mult
            reasons.append("DIRECT_MERGE")
        elif merge_grade == "NEAR":
            score += 600.0 * merge_mult
            reasons.append("NEAR_MERGE")
        elif merge_grade == "FAR":
            score += 200.0 * merge_mult
            reasons.append("FAR_MERGE")

        # 2. 高度によるペナルティ（v155: v128の設定を維持、一律構造）
        height_penalty = landing_y * 50.0 * height_mult

        # HIGH_TOWERペナルティ（v155: v128の1.3倍を維持）
        if phase == "HIGH" and landing_y > 0.5:
            height_penalty *= 1.3  # v155: v128の1.3倍を維持
            reasons.append("HIGH_TOWER")
        elif phase == "MEDIUM" and landing_y > 0.5:
            height_penalty *= 1.5  # v155: v42の1.5倍を維持
            reasons.append("MEDIUM_TOWER")
        elif landing_y > 0.0:
            reasons.append("HIGH_LAYER")

        score -= height_penalty

        # 3. ドリフトによるペナルティ（v155: v42の一律30.0を維持）
        drift_penalty = (abs(drift_x) + drift_unc) * 30.0
        score -= drift_penalty

        # 4. 重心補正（v155: 新しい予測非依存のアプローチ）
        # X重心（左右バランス）とY重心（高さバランス）の両方を考慮
        # 重心が中央に近いほど、高さが低いほどボーナス

        # X重心の計算（左右バランス）
        center_bonus = 0.0
        if pieces:
            x_center = sum(p["x"] for p in pieces) / len(pieces)
            # X重心が0に近いほどボーナス
            x_center_penalty = (
                abs(x_center) * 20.0
            )  # v155: v128のバランス補正より小さい
            center_bonus -= x_center_penalty
            reasons.append("X_CENTER")

        # Y重心の計算（高さバランス）
        if pieces:
            y_center = sum(p["y"] for p in pieces) / len(pieces)
            # Y重心が低いほどボーナス（高い位置にピースがあるとペナルティ）
            y_center_penalty = (
                max(0, y_center - (-2.0)) * 15.0
            )  # v155: Y重心補正、-2.0以下でペナルティなし
            center_bonus -= y_center_penalty
            reasons.append("Y_CENTER")

        # ドロップ位置xがX重心に近いほどボーナス（バランス改善）
        if pieces:
            x_center = sum(p["x"] for p in pieces) / len(pieces)
            # xがX重心に近いほど、盤面のバランスを改善
            drop_balance_bonus = (
                max(0, 1.0 - abs(x - x_center) / 3.0) * 10.0
            )  # v155: 最大10.0ボーナス
            center_bonus += drop_balance_bonus
            reasons.append("DROP_BALANCE")

        score += center_bonus

        # 5. 左右バランス補正（v155: v128の設定を維持）
        balance_strength = 20.0
        if phase == "HIGH":
            balance_strength = 40.0  # v155: v128の40.0を維持
        elif phase == "MEDIUM":
            balance_strength = 30.0  # v155: v128の30.0を維持

        left_count = sum(1 for p in pieces if p["x"] < 0)
        right_count = len(pieces) - left_count
        balance_bias = (right_count - left_count) / (len(pieces) if pieces else 1)

        balance_penalty = x * balance_bias * balance_strength
        score -= abs(balance_penalty)

        # 6. nextNextが同じタイプなら中央寄せボーナス（v155: v128の一律50.0を維持）
        if next_next_type == next_type:
            center_bonus2 = max(0, 1.0 - abs(x) / 2.0) * 50.0
            score += center_bonus2
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
