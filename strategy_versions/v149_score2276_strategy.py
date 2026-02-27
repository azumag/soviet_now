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
# v147: v128復帰・一律構造最終版 - v146の失敗（スコア608、マージ成功率0.0%、NO_MERGEペナルティが予測精度の低さを悪化・height_mult=1.4の過度な緩和で高度管理不十分）を受けて、予測ベースの戦略を完全断念し、v128の成功設定に完全復帰するブレイクスルーを採用。履歴分析でv144-v146の予測ベース戦略が失敗した根本原因を特定：マージ予測の精度が低く、予測を前提とする戦略は本質的に危険。（1）予測ベース戦略の完全断念：v144のマージグレード連動型高度管理、v146の一律強いインセンティブ（マージボーナス強化+NO_MERGEペナルティ）など、予測を前提とする全てのアプローチを完全削除。予測ベースの戦略は精度不足でマージ機会を損失し、スコアを劇的に低下させる（v146の608点はv145の2716点より大幅に低い）ため、一律構造への復帰を決定。（2）v128成功設定の完全復帰：height_mult=1.8、HIGH_TOWER=1.3倍を採用し、予測を前提としない一律構造に復帰。v128の成功（3689点）は、予測を前提としない一律構造でv42の頑健性とマージ優先のバランスをとったことを示している。（3）NO_MERGEペナルティの完全削除：v146のNO_MERGEペナルティ-200を完全削除。NO_MERGEペナルティは「予測が正確であること」を前提とするが、予測精度が低いため誤検出で低い位置を選びマージ機会を損失。一律構造ではマージ予測を前提としないため、NO_MERGEペナルティは不要。（4）v42マージボーナスの完全復帰：DIRECT=1200/NEAR=600/FAR=200を採用し、v146の強化（1400/700/250）を削除。予測精度が低い状況下では、一律の強いマージボーナスも誤発動し、NO_MERGEペナルティと同様にマージ機会を損失。一律構造ではv42の設定で十分。（5）v42頑健基本構造の完全復帰：drift_penalty=30、balance補正、NEXT_SAMEボーナス=50、MEDIUMフェーズheight_mult=2.4、MEDIUM_TOWER=1.5倍はv42の設定を完全復帰。v42の頑健な基本構造でHIGH到達までの安定性を確保し、HIGHフェーズではv128の高度管理緩和でマージを優先。（6）振れ子パターン解消：v144-v146の「予測ベース緩和↔予測ベース削除↔一律強いインセンティブ」の振れ子を、予測ベース戦略の完全断念で解消。予測を前提としない一律構造で頑健性を確保し、v42の頑健性とv128のマージ優先のバランスをとる。（7）ブレイクスルー：予測精度が低い状況下では、予測に依存した戦略は本質的に危険。一律構造でv42の頑健性とv128のマージ優先のバランスをとる。予測を前提としない一律構造で頑健性を確保する。コード量削減（約70行→約65行）。失敗（スコア704）：履歴分析でHIGHフェーズ（max_y 1.8-2.5範囲）でのマージ成功率極めて低い（merge_available=trueのターンでも実際のマージは発生していない）。v128成功設定と全く同じ設定を使用したにもかかわらず、スコアは3689点から704点に劇的に低下した。根本原因：予測精度がv128よりさらに低くなっており、強いバランス補正（HIGHフェーズ40.0）が予測ミスを増幅し、マージ可能な位置を回避してマージ機会を損失している。最終盤面を見ると左側が大幅に空いており、バランス補正が過剰に機能している。予測精度が低い状況下では、一律の強いバランス補正はマージ機会を損失する要因となる。
# v148: バランス補正大幅弱体化版 - v147の失敗（スコア704、予測精度低さ・強いバランス補正がマージ機会損失）を受けて、予測精度の低さに対処するための第三の選択肢を採用。履歴分析でv147の失敗原因を特定：予測精度がv128よりさらに低く、強いバランス補正（HIGHフェーズ40.0）が予測ミスを増幅し、マージ可能な位置を回避してマージ機会を損失。最終盤面で左側が大幅に空いていることから、バランス補正が過剰に機能していることが明らか。（1）バランス補正の大幅弱体化：HIGHフェーズのbalance_strengthを40.0から10.0に大幅に弱体化（v42の1/4）。MEDIUMフェーズも30.0から15.0に弱体化（v42の1/2）。予測精度が低い状況下では、強いバランス補正は予測ミスを増幅しマージ機会を損失するため、バランス補正を大幅に弱めることでマージの機会を強制的に確保。（2）一律構造の維持：v42の頑健な基本構造を維持。マージボーナス（DIRECT=1200/NEAR=600/FAR=200）、drift_penalty=30、NEXT_SAMEボーナス=50、MEDIUMフェーズheight_mult=2.4、MEDIUM_TOWER=1.5倍はv42の設定を維持し、HIGH到達までの安定性を確保。マージボーナス強化やNO_MERGEペナルティなどの予測ベース戦略は採用せず、一律構造で頑健性を確保。（3）height_mult維持：v128のheight_mult=1.8を維持し、HIGHフェーズ高度管理緩和でマージ優先。HIGH_TOWERペナルティはv128の1.3倍を維持し、高度管理とマージ優先のバランスをとる。（4）ブレイクスルー：予測精度が低い状況下では、予測に依存した戦略（マージボーナス強化、NO_MERGEペナルティ、マージグレード連動型高度管理）は本質的に危険。一律の強いバランス補正も予測ミスを増幅しマージ機会を損失するため、バランス補正を大幅に弱めることで予測に依存せずマージの機会を強制的に確保。一律構造で頑健性を確保しつつ、バランス補正弱体化でマージ優先を実現するシンプルで頑健なアプローチを実現。コード量維持（約65行）。失敗（スコア1209）：履歴分析でHIGHフェーズ（turns 41-85, max_y 0.42-3.88）でのマージ可能ターン数は2回のみ、実際にマージしたターンは0回。予測精度が非常に低く、merge_available=trueでbest_merge_grade=NEARでも実際のマージは発生していない（score_delta=0）。reactive_pairsが多いターン（turns 70, 82: reactive_pairs=4,6）でもマージできておらず、height_mult=1.8が依然として高すぎ、高い位置でのマージを避けていることが根本原因。バランス補正弱体化は部分的に有効だが、マージ機会の確保には不十分。
# v149: reactive_pairs動的高度管理版 - v148の失敗（スコア1209、reactive_pairsが多いターンでもマージできず、height_mult=1.8が高すぎ）を受けて、reactive_pairsに応じた動的高度管理を導入するブレイクスルーを採用。履歴分析でv148の失敗原因を特定：reactive_pairsが多いターン（turns 70, 82: reactive_pairs=4,6）でもマージできておらず、height_mult=1.8が依然として高すぎ、高い位置でのマージを避けている。reactive_pairsは盤面のマージ可能性を表す指標であり、reactive_pairsが多い場合はマージを積極的に狙うべき。（1）reactive_pairsに応じた高度管理：reactive_pairs == 0-2: 高度管理優先（height_multiplier = 通常値の1.2倍）、reactive_pairs == 3-5: バランス（height_multiplier = 通常値）、reactive_pairs >= 6: マージ優先（height_multiplier = 通常値の0.6倍）。reactive_pairsを整数として扱う（analysis["reactor"]["reactive_pairs"]はlist型の場合len()で取得）。（2）HIGHフェーズheight_multの緩和：v128の1.8からv84の2.2に緩和し、reactive_pairs動的調整と組み合わせる。v84の成功設定（2.2）を基準に、reactive_pairs動的調整で柔軟に高度管理を制御。（3）HIGH_TOWERペナルティの削除：HIGHフェーズではHIGH_TOWERペナルティを削除し、reactive_pairs動的調整で高度管理を制御。一律のHIGH_TOWERペナルティは「削除すると高度管理不十分」「再導入するとマージ機会損失」の振り子を繰り返しているため、reactive_pairs動的調整で代替。（4）MEDIUMフェーズheight_multの維持：v42/v128の2.4を維持し、MEDIUMフェーズでの安定性を確保。MEDIUMフェーズではreactive_pairs動的調整を適用せず、一律構造で頑健性を確保。（5）バランス補正の維持：v148の設定（HIGH=10.0, MEDIUM=15.0）を維持。予測精度が低い状況下では、バランス補正を弱めることが重要であることはv148で実証済み。（6）v42マージボーナスの維持：DIRECT=1200/NEAR=600/FAR=200を維持し、一律の強いマージボーナスを採用しない。予測精度が低い状況下では、一律の強いマージボーナスは誤発動し、マージ機会を損失する。（7）ブレイクスルー：予測精度が低い状況下では、予測に依存した戦略（マージボーナス強化、NO_MERGEペナルティ）は本質的に危険。一律の強いバランス補正も予測ミスを増幅するため弱体化。reactive_pairs動的高度管理は、盤面の実際の状態（reactive_pairs）に応じて高度管理を動的に調整し、一律の設定の問題（高度管理過剰→マージ機会損失、高度管理緩和→CRITICAL到達）を解消する第三の選択肢。予測を前提とせず、盤面の実際の状態に基づいて動的に高度管理を調整することで、マージ機会を強制的に確保。コード量微増（約70行）。


def decide(game_state: dict, analysis: dict) -> dict:
    """reactive_pairsに応じた動的高度管理を導入し、マージ機会を強制的に確保。予測を前提とせず、盤面の実際の状態に基づいて高度管理を動的に調整。"""

    results = analysis.get("results", [])

    if not results:
        return {"x": 0.0, "reason": "no analysis data"}

    best_x = 0.0
    best_score = -float("inf")
    best_reason = ""

    # 盤面情報
    pieces = game_state.get("pieces", [])
    max_y = max([p["y"] for p in pieces]) if pieces else -4.0

    # reactor情報（reactive_pairsを取得）
    reactor = analysis.get("reactor", {})
    reactive_pairs = reactor.get("reactive_pairs", 0)
    # reactive_pairsがlist型の場合は長さを取得、整数型の場合はそのまま使用
    if isinstance(reactive_pairs, list):
        reactive_pairs_count = len(reactive_pairs)
    else:
        reactive_pairs_count = int(reactive_pairs)

    # フェーズ判定（v42の閾値を維持）
    if max_y < 0.8:
        phase = "LOW"
        height_mult = 1.0
        merge_mult = 1.2
    elif max_y < 1.8:
        phase = "MEDIUM"
        height_mult = 2.4  # v149: v42の2.4を維持（MEDIUMフェーズの安定性確保）
        merge_mult = 1.0
    elif max_y < 3.0:
        phase = "HIGH"
        height_mult = 2.2  # v149: v84の2.2に緩和、reactive_pairs動的調整と組み合わせる
        merge_mult = 1.0
    else:
        phase = "CRITICAL"
        height_mult = 1.0  # CRITICAL: height_multなし
        merge_mult = 0.6  # v42の0.6を維持

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

        # === v149: reactive_pairs動的高度管理 ===

        # reactive_pairsに応じたheight_multiplier調整
        # reactive_pairs == 0-2: 高度管理優先（1.2倍）
        # reactive_pairs == 3-5: バランス（1.0倍）
        # reactive_pairs >= 6: マージ優先（0.6倍）
        if phase == "HIGH":
            if reactive_pairs_count >= 6:
                height_multiplier = height_mult * 0.6  # マージ優先
                reasons.append(f"RP{reactive_pairs_count}_MERGE")
            elif reactive_pairs_count >= 3:
                height_multiplier = height_mult * 1.0  # バランス
                reasons.append(f"RP{reactive_pairs_count}_BALANCE")
            else:
                height_multiplier = height_mult * 1.2  # 高度管理優先
                reasons.append(f"RP{reactive_pairs_count}_HEIGHT")
        else:
            # MEDIUM/LOW/CRITICALフェーズでは一律のheight_multiplierを使用
            height_multiplier = height_mult

        height_penalty = landing_y * 50.0 * height_multiplier

        # v149: HIGHフェーズではHIGH_TOWERペナルティを削除し、reactive_pairs動的調整で代替
        # v149: MEDIUMフェーズではMEDIUM_TOWERペナルティを維持（v42の1.5倍）
        if phase == "MEDIUM" and landing_y > 0.5:
            height_penalty *= 1.5  # v42の1.5倍を維持
            reasons.append("MEDIUM_TOWER")
        elif landing_y > 0.0:
            reasons.append("HIGH_LAYER")

        score -= height_penalty

        # 1. マージグレードによるスコア（v149: v42の値を維持）
        if merge_grade == "DIRECT":
            score += 1200.0 * merge_mult
            reasons.append("DIRECT_MERGE")
        elif merge_grade == "NEAR":
            score += 600.0 * merge_mult
            reasons.append("NEAR_MERGE")
        elif merge_grade == "FAR":
            score += 200.0 * merge_mult
            reasons.append("FAR_MERGE")

        # 2. ドリフトによるペナルティ（v42の一律30.0を維持）
        drift_penalty = (abs(drift_x) + drift_unc) * 30.0
        score -= drift_penalty

        # 3. 左右バランス補正（v149: v148の設定を維持）
        # v149: v148の設定（HIGH=10.0, MEDIUM=15.0）を維持
        balance_strength = 20.0
        if phase == "HIGH":
            balance_strength = 10.0  # v149: v148の10.0を維持
        elif phase == "MEDIUM":
            balance_strength = 15.0  # v149: v148の15.0を維持

        left_count = sum(1 for p in pieces if p["x"] < 0)
        right_count = len(pieces) - left_count
        balance_bias = (right_count - left_count) / (len(pieces) if pieces else 1)

        balance_penalty = x * balance_bias * balance_strength
        score -= abs(balance_penalty)

        # 4. nextNextが同じタイプなら中央寄せボーナス（v42の一律50.0を維持）
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
