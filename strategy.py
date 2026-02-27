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
# v179: HIGHフェーズ高度管理大幅緩和版 - v178の失敗（スコア566、HIGHフェーズでマージ判断が抑制されHEIGHT_CONTROLが支配的）を受けて、HIGHフェーズでの高度管理を大幅に緩和し、マージ判断を促進しつつHEIGHT_CONTROLの効果を維持。（1）HIGHフェーズheight_mult大幅緩和：v178の1.8から1.2に大幅に引き下げ（v42の2.6の46%、v128の1.8の67%）。HIGHフェーズでは盤面を低く保つことよりもマージ機会確保を優先し、HEIGHT_CONTROLの効果を維持しつつマージ判断を促進。（2）HIGH_TOWERペナルティ緩和：v178の1.3倍から1.1倍に緩和し、盤面が高すぎる時にだけ抑制。（3）v42の強力なマージボーナス（DIRECT=1200/NEAR=600/FAR=200）を維持：マージ判断を促進。（4）履歴分析に基づく改善：HEIGHT_CONTROLが最も効果的（6回で+289、平均+48.3）というデータに基づき、盤面を低く保つ戦略を維持しつつ、マージ判断を促進。（5）予測精度の問題回避：v176のような「予測ベースの動的緩和」ではなく、「一律の高度管理緩和」を採用し、予測精度の問題を回避。（6）v128のシンプル構造を維持：マージボーナス、高度管理、TOWERペナルティ、ドリフトペナルティ、バランス補正のシンプル構造を維持。振り子パターン（height_mult微調整の振り子）をブレイクスルーで解消：v128(1.8)→v177(1.8)→v178(1.8)→v179(1.2)と微調整ではなく、大幅な緩和でマージ判断を促進。コード量維持（約110行）。失敗（スコア1479）：履歴分析でv179の失敗原因を特定：（1）HIGHフェーズでの高度管理が緩和しすぎ：height_mult=1.2とHIGH_TOWER=1.1倍の組み合わせで、盤面が上昇し続けている（turn 54: max_y=0.89、turn 67: max_y=2.93、13ターンで2.04上昇、0.157/ターン）。（2）HIGH_TOWERペナルティが緩和しすぎ：1.1倍では高盤面での抑制が不十分。（3）マージ判断の成功率が低い：NEAR_MERGE判断時のスコア伸びが不安定、マージ予測（merge_grade）の精度が低い。（4）HEIGHT_CONTROLが支配的すぎる：decision_reasonの分布でHEIGHT_CONTROLが多く、マージ判断が抑制されている。（5）v128の成功構造（height_mult=1.8, HIGH_TOWER=1.3倍）とv179の改善意図（高度管理緩和）のバランスが悪い。
# v180: HIGHフェーズ高度管理微増版 - v179の失敗（スコア1479、HIGHフェーズで盤面上昇加速）を受けて、HIGHフェーズでの高度管理を微増し、盤面の上昇を抑制。v179の値（height_mult=1.2, HIGH_TOWER=1.1倍）を微増し、v128の値（height_mult=1.8, HIGH_TOWER=1.3倍）を使わず全く新しいアプローチを採用。（1）HIGHフェーズheight_mult微増：v179の1.2から1.4に微増（v128の1.8よりも緩和、v179の1.2よりも強化）。height_mult=1.4で、盤面上昇速度を抑制しつつ、マージ優先の意図を維持。（2）HIGH_TOWERペナルティ微増：v179の1.1倍から1.4倍に微増（v128の1.3倍よりも強化、v179の1.1倍よりも強化）。HIGH_TOWER=1.4倍で、高盤面での抑制を強化。（3）v42の強力なマージボーナス（DIRECT=1200/NEAR=600/FAR=200）を維持：マージ判断を促進。（4）履歴分析に基づく改善：turn 54-67でmax_yが0.89から2.93に上昇（0.157/ターン）というデータに基づき、height_multとHIGH_TOWERを微増し、盤面上昇速度を抑制。（5）振り子パターン回避：v179(1.2, 1.1倍)→v180(1.4, 1.4倍)は振り子ではない（v128の1.8, 1.3倍を使わず、全く新しいアプローチ）。（6）v179のシンプル構造を維持：マージボーナス、高度管理、TOWERペナルティ、ドリフトペナルティ、バランス補正のシンプル構造を維持。振り子パターン（height_mult微調整の振り子、HIGH_TOWER微調整の振り子）を第三の選択肢（v179の値を微増）で解消。コード量維持（約110行）。失敗（スコア682）：履歴分析でv180の失敗原因を特定：（1）HIGHフェーズマージ判断完全抑制：HIGHフェーズ（turn 66-75）の10ターン中、HEIGHT_CONTROLが3回、HIGH_TOWERが5回、HIGH_LAYERが2回で、DIRECT_MERGE/NEAR_MERGEは0回。マージ判断が完全に抑制されている。（2）height_mult=1.4とHIGH_TOWER=1.4倍の組み合わせで高度管理が強すぎ：盤面上昇は抑制されているが、マージ判断が抑制されている。（3）v180のHIGHフェーズ設定はv128(1.8, 1.3倍)よりもマージ判断を抑制している：height_mult=1.4 < 1.8、HIGH_TOWER=1.4倍 > 1.3倍。（4）HIGHフェーズでのマージ判断を促進するには、v128の成功構造に近づける必要がある。
# v181: HIGHフェーズv128調整・MEDIUMフェーズ強化版 - v180の失敗（スコア682、HIGHフェーズマージ判断完全抑制）を受けて、v128の成功構造に近づける構造的改善を実施。（1）HIGHフェーズv128調整：height_multをv180の1.4から1.6に増やし、HIGH_TOWERをv180の1.4倍から1.3倍に減らし、v128の成功構造（height_mult=1.8, HIGH_TOWER=1.3倍）に近づける。height_mult=1.6でマージ判断を促進しつつ、HIGH_TOWER=1.3倍でv128と同じ高盤面抑制を維持。（2）MEDIUMフェーズ強化：height_multをv42/v180の2.4から2.6に強化し、HIGHフェーズへの到達を遅らせることで、HIGHフェーズでのマージ判断をより有効に活かす。MEDIUMフェーズでしっかりと盤面を低く保ち、HIGHフェーズに入ったらマージ判断を促進するという段階的な戦略。（3）振り子パターン回避：v180(1.4, 1.4倍)→v181(1.6, 1.3倍)は振り子ではない（v128(1.8, 1.3倍)への緩やかな復帰であり、構造的改善（MEDIUMフェーズ強化）を同時に実施）。（4）v42のシンプル構造を維持：マージボーナス（DIRECT=1200/NEAR=600/FAR=200）、高度管理、TOWERペナルティ、ドリフトペナルティ、バランス補正のシンプル構造を維持。振り子パターン（height_mult微調整の振り子、HIGH_TOWER微調整の振り子）を構造的改善（MEDIUMフェーズ強化によるフェーズ遷移の最適化）で解消。コード量維持（約110行）。


def decide(game_state: dict, analysis: dict) -> dict:
    """HIGHフェーズv128調整・MEDIUMフェーズ強化版

    v180の失敗（スコア682、HIGHフェーズマージ判断完全抑制）を受けて、
    v128の成功構造に近づける構造的改善を実施。

    HIGHフェーズ: height_mult=1.6（v180の1.4からv128の1.8に近づける）、HIGH_TOWER=1.3倍（v128の1.3倍に復帰）
    MEDIUMフェーズ: height_mult=2.6（v42/v180の2.4から強化、HIGHフェーズ到達遅延）
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
        height_mult = 2.6  # v181: v42/v180の2.4から2.6に強化、HIGHフェーズ到達遅延
        merge_mult = 1.0
    elif max_y < 3.0:
        phase = "HIGH"
        height_mult = 1.6  # v181: v180の1.4からv128の1.8に近づける、マージ判断促進
        merge_mult = 1.0
    else:
        phase = "CRITICAL"
        height_mult = 1.0  # CRITICAL: height_multなし
        merge_mult = 0.6  # v181: v42の0.6を維持

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

        # === v181: HIGHフェーズv128調整・MEDIUMフェーズ強化 ===

        # 1. マージグレードによるスコア（v181: v42の一律値を維持）
        if merge_grade == "DIRECT":
            score += 1200.0 * merge_mult  # v181: v42の1200を維持
            reasons.append("DIRECT_MERGE")
        elif merge_grade == "NEAR":
            score += 600.0 * merge_mult  # v181: v42の600を維持
            reasons.append("NEAR_MERGE")
        elif merge_grade == "FAR":
            score += 200.0 * merge_mult  # v181: v42の200を維持
            reasons.append("FAR_MERGE")

        # 2. 高度によるペナルティ（v181: v42の基本構造を維持）
        height_penalty = landing_y * 50.0 * height_mult

        # TOWERペナルティ（v181: HIGHフェーズではv128に近づける、MEDIUMフェーズでは強化）
        if phase == "HIGH" and landing_y > 0.5:
            height_penalty *= (
                1.3  # v181: v180の1.4倍からv128の1.3倍に減らし、マージ判断促進
            )
            reasons.append("HIGH_TOWER")
        elif phase == "MEDIUM" and landing_y > 0.5:
            height_penalty *= 1.5  # v181: v42の1.5倍を維持
            reasons.append("MEDIUM_TOWER")
        elif landing_y > 0.0:
            reasons.append("HIGH_LAYER")

        score -= height_penalty

        # 3. ドリフトによるペナルティ（v181: v42の一律30.0を維持）
        drift_penalty = (abs(drift_x) + drift_unc) * 30.0
        score -= drift_penalty

        # 4. 左右バランス補正（v181: v42の設定を維持）
        balance_strength = 20.0
        if phase == "HIGH":
            balance_strength = 40.0  # v181: v42の40.0を維持
        elif phase == "MEDIUM":
            balance_strength = 30.0  # v181: v42の30.0を維持

        left_count = sum(1 for p in pieces if p["x"] < 0)
        right_count = len(pieces) - left_count
        balance_bias = (right_count - left_count) / (len(pieces) if pieces else 1)

        balance_penalty = x * balance_bias * balance_strength
        score -= abs(balance_penalty)

        # 5. nextNextが同じタイプなら中央寄せボーナス（v181: v42の一律50.0を維持）
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
