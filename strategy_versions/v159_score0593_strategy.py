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
# v155: v128復帰・重心補正導入版 - v154の失敗（スコア1258、履歴データベース失敗・HIGHフェーズマージ率低・バランス補正強化副作用）を受けて、履歴データベースを削除し、重心補正を導入するブレイクスルーを採用。履歴分析でv154の失敗原因を特定：（1）履歴データベースの過剰信頼：予測精度が低い状況下では、履歴データ（1.0-2.5）を信用した高度管理緩和は失敗。個別のゲームでは履歴データが適用できない可能性がある。（2）複雑な動的高度管理：MERGE_FAVOR_ZONE/MERGE_UNLIKELY_ZONEの条件分岐は、予測ミスの影響を増幅し、一律構造の頑健性を損なう。（3）バランス補正の強化（50.0）：中央寄せを強制しすぎてマージ可能な位置を回避。v128の40.0の方が適切。（4）v128の成功設定：height_mult=1.8、HIGH_TOWER=1.3倍、バランス補正=40.0/30.0、中央寄せ=50.0は3689点を達成。（5）予測に依存しない一律構造の維持が重要：履歴データベースのような複雑な条件分岐は、予測精度が低い状況下では危険。（1）v128成功設定への復帰：height_mult=1.6、HIGH_TOWER=1.3倍、バランス補正=40.0/30.0、中央寄せ=50.0を採用し、一律構造に復帰。（2）履歴データベース削除：MERGE_FAVOR_ZONE/MERGE_UNLIKELY_ZONEの動的高度管理を完全削除。一律構造で頑健性を確保。（3）重心補正導入：X重心（左右バランス）とY重心（高さバランス）の両方を考慮。X重心は0に近いほど、Y重心は低いほどボーナス。予測に依存せず、物理的事実に基づく盤面の均衡性を評価。（4）HIGHフェーズ高度管理微緩和：height_multをv128の1.8から1.6に微調整し、マージ機会を確保。HIGH_TOWERペナルティはv128の1.3倍を維持。（5）ブレイクスルー：予測精度が低い状況下では、予測に依存した動的高度管理（履歴データベース）は危険。一律構造でv42の頑健性とv128の成功要素を維持し、重心補正という新しい予測非依存のアプローチで盤面の均衡性を改善。重心補正はX重心とY重心の両方を考慮し、盤面の空間的均衡性を客観的に評価。予測精度に依存せず、物理的事実（全ピースの位置）に基づくため信頼性が高い。コード量微増（約70行→約80行）。失敗（スコア1049）：履歴分析でv155の失敗原因を特定：（1）重心補正とバランス補正の重複・相殺：X_CENTER/DROP_BALANCEと左右バランス補正が競合、複雑化の割に効果なし。（2）予測精度の低さ：merge_available=trueの6ターン中1回しかスコアが伸びなかった（16.7%）。予測精度は依然として低い。（3）v128からの逸脱：height_mult=1.6はv128の1.8から逸脱、バランス補正balance_strength=20.0はv128のHIGH=40.0から逸脱。（4）HIGHフェーズマージ率：27ターン中マージ発生1回（3.7%）、v128と比較して大幅に低い。（5）decision_reasonの分布：「HIGH_TOWER」が14ターン（51.9%）で支配的。高度管理が優先されすぎてマージ機会を損失。（6）重心補正の副作用：X_CENTER/Y_CENTER/DROP_BALANCEがバランス補正と重複し、中央寄せを強制しすぎてマージ可能な位置を回避。
# v156: v128完全復帰・シンプル化版 - v155の失敗（スコア1049、重心補正失敗・HIGHフェーズマージ率低・v128設定からの逸脱）を受けて、重心補正を完全削除し、v128の設定を完全復帰するブレイクスルーを採用。履歴分析でv155の失敗原因を特定：（1）重心補正とバランス補正の重複・相殺：X_CENTER/DROP_BALANCEと左右バランス補正が競合、複雑化の割に効果なし。（2）予測精度の低さ：merge_available=trueの6ターン中1回しかスコアが伸びなかった（16.7%）。予測精度は依然として低い。（3）v128からの逸脱：height_mult=1.6はv128の1.8から逸脱、バランス補正balance_strength=20.0はv128のHIGH=40.0から逸脱。（4）HIGHフェーズマージ率：27ターン中マージ発生1回（3.7%）、v128と比較して大幅に低い。（5）decision_reasonの分布：「HIGH_TOWER」が14ターン（51.9%）で支配的。高度管理が優先されすぎてマージ機会を損失。（6）重心補正の副作用：X_CENTER/Y_CENTER/DROP_BALANCEがバランス補正と重複し、中央寄せを強制しすぎてマージ可能な位置を回避。（1）v128成功設定の完全復帰：height_mult=1.8、HIGH_TOWER=1.3倍、バランス補正balance_strength=20.0/40.0、中央寄せcenter_bonus=50.0を採用し、v128の設定を完全復帰。（2）重心補正完全削除：X_CENTER/Y_CENTER/DROP_BALANCEの重心補正を完全削除。バランス補正のみで盤面の均衡性を確保。（3）予測前提回避：予測に依存した複雑な条件分岐（重心補正、履歴データベース）を完全削除。一律構造でv42の頑健性を確保。（4）振り子パターン解消：重心補正の「追加↔削除」の振り子を、v128成功設定の完全復帰で解消。（5）ブレイクスルー：予測精度が低い状況下では、予測に依存した戦略（重心補正、履歴データベース、マージボーナス強化、NO_MERGEペナルティ）は本質的に危険。一律構造でv42の頑健性とバランス補正の安全装置機能を維持し、v128の高度管理緩和でマージ優先のバランスをとる。予測を前提としない一律構造で頑健性を確保する。コード量削減（約80行→約65行）。失敗（スコア870）：履歴分析でv156の失敗原因を特定：（1）HIGHフェーズマージ率0%：HIGHフェーズ（turns 64-65, 2ターン）でマージ発生なし。（2）HIGH_TOWERペナルティが支配的：HIGHフェーズの2ターンともHIGH_TOWERで決定。（3）スコア停滞：turns 58-65でスコア870のまま停滞。（4）v128設定への完全復帰だがスコア870と低調（v128は3689点）。（5）v156のHIGHフェーズマージ率0%は、v128のHIGHフェーズマージ率（仮定20-40%）と比較して大幅に低い。（6）振り子パターンの再発：HIGH_TOWERペナルティは「削除（v152）→復帰（v153）」の振り子があり、v156はv128設定（HIGH_TOWER=1.3倍）への復帰だが、v128設定自体がv128の成功とv156の失敗のどちらにも寄与していない可能性。（7）個別のゲームの差異：v128は3689点を達成したが、v156は870点。これは個別のゲームの運の要素や盤面の違いによる可能性があるが、v156のHIGHフェーズマージ率0%は戦略の問題と推測。
# v157: HIGH_TOWERペナルティ削除・HIGHフェーズ高度管理大幅緩和版 - v156の失敗（スコア870、HIGHフェーズマージ率0%・HIGH_TOWERペナルティ支配的・振り子パターン再発）を受けて、振り子パターンを解消するブレイクスルーを採用。履歴分析でv156の失敗原因を特定：（1）HIGHフェーズマージ率0%：HIGHフェーズ（turns 64-65, 2ターン）でマージ発生なし。（2）HIGH_TOWERペナルティが支配的：HIGHフェーズの2ターンともHIGH_TOWERで決定、merge_available=false。（3）v152の失敗は「HIGH_TOWER削除（1.0倍）+ height_mult維持（1.8）」、v157は「HIGH_TOWER削除（完全削除）+ height_mult大幅緩和（1.2）」で成功する可能性。（4）振り子パターンの解消：HIGH_TOWERペナルティの「削除↔復帰」振り子を解消するため、HIGH_TOWERペナルティを完全削除して固定。（5）height_multの微調整振り子を回避：v155（1.8→1.6）→v156（1.6→1.8）→v157（1.8→1.2）の振り子を回避し、v157ではheight_mult=1.2で固定。（6）ブレイクスルー：予測精度が低い状況下では、HIGH_TOWERペナルティのような複雑な条件分岐は危険。一律構造でv42の頑健性を維持し、HIGHフェーズでのマージ優先を徹底する。（7）HIGHフェーズ高度管理大幅緩和：height_multをv128の1.8から1.2に大幅緩和し、マージ機会最大化。（8）HIGH_TOWERペナルティ完全削除：HIGHフェーズでの高度管理ペナルティを一律で計算し、HIGH_TOWERペナルティを削除。（9）v42のシンプル構造を維持：予測に依存しない一律構造で頑健性を確保。コード量削減（約65行→約60行）。


def decide(game_state: dict, analysis: dict) -> dict:
    """HIGH_TOWERペナルティを完全削除し、一律構造で盤面均衡を最大化。

    予測に依存したHIGH_TOWERのような条件分岐を削除し、代わりに：
    1. 一律のheight_penaltyのみで高度管理
    2. ピース分布の均一性を評価する新規指標
    3. analysis["reactor"]["near_pairs"]を活用したマージ促進

    予測精度に依存せず、物理的事実に基づく一律構造で頑健性を確保。
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
        height_mult = 2.4  # v159: MEDIUMで高度管理強化、HIGH到達遅延
        merge_mult = 1.0
    elif max_y < 3.0:
        phase = "HIGH"
        height_mult = 1.4  # v159: v128の1.8とv157の1.2の中間、均衡
        merge_mult = 1.2  # v159: HIGHフェーズでマージ優先
    else:
        phase = "CRITICAL"
        height_mult = 1.0  # CRITICAL: height_multなし
        merge_mult = 0.6  # v159: v42の0.6を維持

    # 次のピース情報
    next_piece = game_state.get("next", {})
    next_next_piece = game_state.get("nextNext", {})
    next_type = next_piece.get("type", 0)
    next_next_type = next_next_piece.get("type", 0)

    # reactor情報取得
    reactor = analysis.get("reactor", {})
    near_pairs = reactor.get("near_pairs", [])

    for result in results:
        x = result["x"]
        landing_y = result.get("landing_y", 0)
        drift_x = result.get("drift_x", 0)
        drift_unc = result.get("drift_unc", 0)
        merge_grade = result.get("merge_grade", "NO")

        score = 0.0
        reasons = []

        # === v159: 一律構造・盤面均衡最大化 ===

        # 1. マージグレードによるスコア（一律ボーナス、ペナルティなし）
        if merge_grade == "DIRECT":
            score += 1200.0 * merge_mult
            reasons.append("DIRECT_MERGE")
        elif merge_grade == "NEAR":
            score += 600.0 * merge_mult
            reasons.append("NEAR_MERGE")
        elif merge_grade == "FAR":
            score += 200.0 * merge_mult
            reasons.append("FAR_MERGE")

        # 2. 高度によるペナルティ（一律、HIGH_TOWERの追加倍率なし）
        height_penalty = landing_y * 50.0 * height_mult
        if landing_y > 0.0:
            reasons.append("HIGH_LAYER")
        score -= height_penalty

        # 3. ピース分布の均一性ボーナス（v159: 新規）
        # 左中右のピース数を計算し、均等であるほどボーナス
        left_count = sum(1 for p in pieces if p["x"] < -1.0)
        center_count = sum(1 for p in pieces if -1.0 <= p["x"] < 1.0)
        right_count = sum(1 for p in pieces if p["x"] >= 1.0)
        total = len(pieces)
        if total > 0:
            expected = total / 3.0
            # 均一性を測る指標（0が均一）
            distribution_penalty = (
                (
                    abs(left_count - expected)
                    + abs(center_count - expected)
                    + abs(right_count - expected)
                )
                / total
                * 20.0
            )
            score -= distribution_penalty

        # 4. 同typeピースへの距離ボーナス（v159: 新規）
        # near_pairs情報を活用、予測に依存せずマージ機会確保
        if near_pairs:
            for pair in near_pairs:
                # near_pairsはタプル形式: (id1, id2, type, gap)
                pair_id1, pair_id2, pair_type, gap = pair
                # ピース位置を取得
                p1 = next((p for p in pieces if p["id"] == pair_id1), None)
                p2 = next((p for p in pieces if p["id"] == pair_id2), None)
                if p1 and p2:
                    mid_x = (p1["x"] + p2["x"]) / 2
                    dx = x - mid_x
                    distance_bonus = max(0, 1.0 - abs(dx) / 2.0) * 30.0
                    score += distance_bonus
                    if distance_bonus > 10.0:
                        reasons.append("NEAR_PAIR")

        # 5. ドリフトによるペナルティ（一律）
        drift_penalty = (abs(drift_x) + drift_unc) * 30.0
        score -= drift_penalty

        # 6. 左右バランス補正（一律、HIGH_TOWER削除でバランス補正を維持）
        balance_strength = 20.0
        if phase == "HIGH":
            balance_strength = 30.0  # v159: HIGH_TOWER削除でバランス補正強化
        elif phase == "MEDIUM":
            balance_strength = 30.0

        left_count_balance = sum(1 for p in pieces if p["x"] < 0)
        right_count = len(pieces) - left_count_balance
        balance_bias = (right_count - left_count_balance) / (
            len(pieces) if pieces else 1
        )

        balance_penalty = x * balance_bias * balance_strength
        score -= abs(balance_penalty)

        # 7. nextNextが同じタイプなら中央寄せボーナス
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
