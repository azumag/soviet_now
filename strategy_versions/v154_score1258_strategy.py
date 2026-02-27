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
# v152: v128ベース・バランス補正強化・高度管理緩和版 - v151の失敗（スコア835、HIGHフェーズマージなし・height_mult=1.8高すぎ・HIGH_TOWERペナルティ不要・バランス補正不十分）を受けて、v128成功設定をベースにバランス補正強化と高度管理緩和を実施するブレイクスルーを採用。履歴分析でv151の失敗原因を特定：（1）HIGHフェーズ（turns 59-81）でマージ発生なし：max_y=1.21-2.98だが、マージは0回。履歴分析でマージはHIGHフェーズでも発生している（turns 68: max_y=1.85、turns 79-80: max_y=2.5）。特にturn 68でmax_y=1.85でもマージが発生しているため、height_mult=1.8は高すぎ、HIGH_TOWERペナルティは不要。（2）マージ発生時の盤面高さ：turns 68, 79-80のマージはmax_y=1.85-2.5で発生。マージは高さ1.0-2.5でも発生するため、HIGH_LAYER閾値を0.5から0.3に下げる必要がある。（3）予測精度の低さ：merge_available=trueでも実際のマージは発生していない。予測精度が低い状況下では、予測に依存した戦略（マージボーナス一律強化、NO_MERGEペナルティ）は本質的に危険。（4）バランス補正が不十分：盤面の左右不均衡を是正できていない。（1）v128成功設定をベースに維持：マージボーナス（DIRECT=1200/NEAR=600/FAR=200）、drift_penalty=30、MEDIUMフェーズ設定（height_mult=2.4、MEDIUM_TOWER=1.5倍）を維持。（2）HIGHフェーズ高度管理緩和：height_multをv128の1.8から1.6に緩和し、マージ機会を確保。HIGH_TOWERペナルティをv128の1.3倍から1.0倍に削除し、高い位置でのマージを回避しない。（3）バランス補正強化：HIGHフェーズのbalance_strengthをv128の40.0から50.0に強化。MEDIUMフェーズもv128の30.0から40.0に強化。予測精度が低い状況下では、バランス補正を強めることで予測ミスの影響を最小限に抑え、マージ可能な位置を回避しない安全装置として機能。（4）中央寄せボーナス強化：v128の一律50.0から60.0に強化し、盤面の左右不均衡を是正。HIGHフェーズでの中央寄せを促進し、マージ機会を確保。（5）HIGH_LAYER閾値調整：v128の0.5から0.3に下げ、マージ機会を確保。履歴分析でマージは高さ1.0-2.5でも発生するため、閾値を下げる必要がある。（6）予測に依存しない一律構造の維持：マージボーナス一律強化やNO_MERGEペナルティなどの予測ベース戦略は採用せず、一律構造で頑健性を確保。（7）ブレイクスルー：予測精度が低い状況下では、予測に依存した戦略（マージボーナス一律強化、NO_MERGEペナルティ）は本質的に危険。一律構造でv42の頑健性とバランス補正の安全装置機能を維持し、バランス補正強化で予測ミスの影響を最小限に抑え、高度管理緩和でマージ機会を確保。予測を前提としない一律構造で頑健性を確保する。コード量維持（約65行）。失敗（スコア704）：履歴分析でv152の失敗原因を特定：（1）HIGH_TOWERペナルティとバランス補正の振り子パターンが復活：HIGH_TOWERはv150削除→v151復帰→v152削除、バランス補正はv150弱体化→v151復帰→v152強化。（2）v152の改善はv128から逸脱：height_mult=1.6、HIGH_TOWER=1.0倍、バランス補正=50.0/40.0、中央寄せ=60.0はv128（1.8、1.3倍、40.0/30.0、50.0）から完全に逸脱。（3）スコアは704と大幅悪化：v128の3689点から激減、v152の改善は完全に失敗。（4）予測精度は依然として低い：merge_available=trueの5ターン中1回しかマージが発生していない（20%）。（5）バランス補正強化の副作用：50.0/40.0に強化したが、中央寄せを強制しすぎてマージ可能な位置を回避。turns 59-66で連続HIGH_TOWER、xが-3.0, -2.8, 3.0, 1.0と極端に振れる。（6）height_mult緩和の失敗：1.6に緩和したが、スコアは704と非常に低い。v128の1.8の方がはるかに成功している。
# v153: v128完全復帰・予測前提回避版 - v152の失敗（スコア704、HIGH_TOWERペナルティとバランス補正の振り子パターン復活・v128からの逸脱が原因）を受けて、v128の成功設定を完全復帰するブレイクスルーを採用。履歴分析でv152の失敗原因を特定：（1）振り子パターン確定：HIGH_TOWERはv150削除→v151復帰→v152削除、バランス補正はv150弱体化→v151復帰→v152強化。予測に依存したパラメータ調整が振り子を繰り返している。（2）v152の改善はv128から逸脱：height_mult=1.6、HIGH_TOWER=1.0倍、バランス補正=50.0/40.0、中央寄せ=60.0はv128（1.8、1.3倍、40.0/30.0、50.0）から完全に逸脱。（3）「v128をさらに緩和すればスコアが上がる」という前提が間違い：v128は3689点を達成しており、これ以上の緩和は不要。v152はv128から逸脱したことで失敗。（4）予測精度は依然として低い：merge_available=trueの5ターン中1回しかマージが発生していない（20%）。（5）バランス補正強化の副作用：50.0/40.0に強化したが、中央寄せを強制しすぎてマージ可能な位置を回避。turns 59-66で連続HIGH_TOWER、xが-3.0, -2.8, 3.0, 1.0と極端に振れる。（6）v128の成功要因：予測を前提としない一律構造でv42の頑健性とバランス補正の安全装置機能を維持し、HIGHフェーズ高度管理緩和（height_mult=1.8、HIGH_TOWER=1.3倍）でマージ優先のバランスをとった。（1）v128成功設定の完全復帰：height_mult=1.8、HIGH_TOWER=1.3倍、バランス補正=40.0/30.0、中央寄せ=50.0を採用し、予測を前提としない一律構造に復帰。（2）予測前提回避：予測に依存したパラメータ調整（緩和・強化）をやめ、一律構造で頑健性を確保。（3）振り子パターン解消：HIGH_TOWERとバランス補正の「削除↔復帰↔強化」の振り子を、v128成功設定の完全復帰で解消。（4）ブレイクスルー：v128は3689点を達成しており、これ以上の緩和や強化は不要。v152のv128からの逸脱が失敗の主因。予測精度が低い状況下では、一律構造でv42の頑健性とバランス補正の安全装置機能を維持し、v128の高度管理緩和でマージ優先のバランスをとるのが最適。コード量削減（約65行→約60行）。
# v154: 履歴データベース動的高度管理版 - v153の失敗（スコア1405、v128設定完全復帰だがHIGHフェーズマージ率低）を受けて、履歴データに基づいた動的高度管理を導入するブレイクスルーを採用。履歴分析でv153の失敗原因を特定：（1）v128履歴からマージが発生する高度範囲を特定：マージは主にmax_y 1.0-2.5で発生（特に1.5-2.0付近で多く発生）。（2）予測に依存した戦略は失敗：reactive_pairs、merge_available等の予測ベース戦略は全て失敗。（3）履歴データは客観的事実：過去のマージ発生位置は客観的であり、予測精度に依存しない。（4）v128のheight_mult=1.8は全フェーズ一律：マージが発生しやすい高度（1.0-2.5）でも、一律に高度管理ペナルティを適用していたためマージ機会損失。（5）HIGH_TOWERペナルティの1.3倍：高い位置でのマージを回避している。（1）履歴データベース高度管理：v128履歴から「マージがよく発生する高度範囲（1.0-2.5）」を特定し、その範囲内では高度管理ペナルティを軽減する動的高度管理を導入。予測に依存せず、客観的事実（履歴データ）に基づく。（2）CRITICALフェーズのマージ絶対優先：height_multを1.0に設定し、高度管理ペナルティを最小化してマージ機会最大化。（3）HIGHフェーズ高度管理動的調整：landing_yが1.0-2.5の場合、height_multiplierを0.5に軽減（履歴データベースからマージがよく発生する高度範囲）。landing_yが2.5-3.0の場合、height_multiplierを2.0に強化（マージが発生しにくい高度範囲）。（4）HIGH_TOWERペナルティ調整：HIGHフェーズでlanding_y>0.5の場合、height_multiplierを1.3倍にする（v128設定）が、履歴データベース範囲（1.0-2.5）内では1.0倍に軽減（マージ優先）。（5）バランス補正強化：HIGHフェーズのbalance_strengthをv128の40.0から50.0に強化し、マージ可能な位置を確保。（6）中央寄せボーナス強化：一律50.0から60.0に強化し、盤面の左右不均衡を是正。（7）ブレイクスルー：予測に依存せず、履歴データ（客観的事実）に基づいた動的高度管理でマージ機会確保。v128の成功要素（v42頑健構造）を維持しつつ、履歴データベースで高度管理を最適化。予測精度が低い状況下でも、履歴データは客観的であり信頼性が高い。コード量微増（約60行→約70行）。


def decide(game_state: dict, analysis: dict) -> dict:
    """履歴データに基づいた動的高度管理を導入。予測に依存せず、v128履歴から特定した「マージがよく発生する高度範囲（1.0-2.5）」では高度管理ペナルティを軽減し、CRITICALフェーズではマージ絶対優先。"""

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
        height_mult = 2.4  # v154: v42の2.4を維持（MEDIUMフェーズの安定性確保）
        merge_mult = 1.0
    elif max_y < 3.0:
        phase = "HIGH"
        height_mult = 1.8  # v154: v128の1.8を維持（履歴データベースで動的調整）
        merge_mult = 1.0
    else:
        phase = "CRITICAL"
        height_mult = 1.0  # v154: CRITICALではheight_multなし（マージ絶対優先）
        merge_mult = 0.6  # v154: v42の0.6を維持

    # 次のピース情報
    next_piece = game_state.get("next", {})
    next_next_piece = game_state.get("nextNext", {})
    next_type = next_piece.get("type", 0)
    next_next_type = next_next_piece.get("type", 0)

    # v154: 履歴データベース（v128履歴から特定）
    # マージがよく発生する高度範囲: 1.0-2.5（特に1.5-2.0付近で多く発生）
    MERGE_FAVOR_HEIGHT_LOW = 1.0
    MERGE_FAVOR_HEIGHT_HIGH = 2.5

    for result in results:
        x = result["x"]
        landing_y = result.get("landing_y", 0)
        drift_x = result.get("drift_x", 0)
        drift_unc = result.get("drift_unc", 0)
        merge_grade = result.get("merge_grade", "NO")

        score = 0.0
        reasons = []

        # === v154: 履歴データベース動的高度管理 ===

        # 1. マージグレードによるスコア（v154: v42の値を維持）
        if merge_grade == "DIRECT":
            score += 1200.0 * merge_mult
            reasons.append("DIRECT_MERGE")
        elif merge_grade == "NEAR":
            score += 600.0 * merge_mult
            reasons.append("NEAR_MERGE")
        elif merge_grade == "FAR":
            score += 200.0 * merge_mult
            reasons.append("FAR_MERGE")

        # 2. 高度によるペナルティ（v154: 履歴データベース動的高度管理）
        # v154: 履歴データベース（マージがよく発生する高度範囲: 1.0-2.5）
        height_multiplier = height_mult

        # v154: 履歴データベース範囲（1.0-2.5）ではheight_multiplierを軽減
        if (
            phase == "HIGH"
            and landing_y >= MERGE_FAVOR_HEIGHT_LOW
            and landing_y <= MERGE_FAVOR_HEIGHT_HIGH
        ):
            height_multiplier *= (
                0.5  # マージがよく発生する高度範囲では高度管理を半分に軽減
            )
            reasons.append("MERGE_FAVOR_ZONE")
        elif (
            phase == "HIGH" and landing_y > MERGE_FAVOR_HEIGHT_HIGH and landing_y < 3.0
        ):
            height_multiplier *= 2.0  # マージが発生しにくい高度範囲では高度管理を強化
            reasons.append("MERGE_UNLIKELY_ZONE")

        height_penalty = landing_y * 50.0 * height_multiplier

        # HIGH_TOWERペナルティ（v154: v128の1.3倍を維持）
        # v154: 履歴データベース範囲（1.0-2.5）内ではHIGH_TOWERペナルティを軽減
        if phase == "HIGH" and landing_y > 0.5:
            if (
                landing_y >= MERGE_FAVOR_HEIGHT_LOW
                and landing_y <= MERGE_FAVOR_HEIGHT_HIGH
            ):
                height_penalty *= (
                    1.0  # マージがよく発生する高度範囲ではHIGH_TOWERペナルティなし
                )
                reasons.append("HIGH_TOWER_MERGE_FAVOR")
            else:
                height_penalty *= 1.3  # v154: v128の1.3倍を維持
                reasons.append("HIGH_TOWER")
        elif phase == "MEDIUM" and landing_y > 0.5:
            height_penalty *= 1.5  # v154: v42の1.5倍を維持
            reasons.append("MEDIUM_TOWER")
        elif landing_y > 0.0:
            reasons.append("HIGH_LAYER")

        score -= height_penalty

        # 3. ドリフトによるペナルティ（v154: v42の一律30.0を維持）
        drift_penalty = (abs(drift_x) + drift_unc) * 30.0
        score -= drift_penalty

        # 4. 左右バランス補正（v154: バランス補正強化）
        balance_strength = 20.0
        if phase == "HIGH":
            balance_strength = 50.0  # v154: v128の40.0から50.0に強化
        elif phase == "MEDIUM":
            balance_strength = 40.0  # v154: v128の30.0から40.0に強化

        left_count = sum(1 for p in pieces if p["x"] < 0)
        right_count = len(pieces) - left_count
        balance_bias = (right_count - left_count) / (len(pieces) if pieces else 1)

        balance_penalty = x * balance_bias * balance_strength
        score -= abs(balance_penalty)

        # 5. nextNextが同じタイプなら中央寄せボーナス（v154: 中央寄せボーナス強化）
        if next_next_type == next_type:
            center_bonus = (
                max(0, 1.0 - abs(x) / 2.0) * 60.0
            )  # v154: v128の50.0から60.0に強化
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
