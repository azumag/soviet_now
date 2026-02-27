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
# v187: v128完全復帰・density削除版 - v186の失敗（スコア755、densityボーナス副作用・振り子パターン）を受けて、v128の成功構造に完全復帰するブレイクスルーを実施。（1）densityボーナス完全削除：v184-v186の振り子パターンを解消。densityボーナスは高層配置を助長する副作用があり、HIGH_LAYER/MEDIUM_TOWERが36.1%と支配的。v128のシンプルな構造に復帰。（2）マージボーナス復帰：v128の成功構造を復帰。マージボーナス（DIRECT=1200/NEAR=600/FAR=200）は主要な判断指標ではなく、補助的な役割。マージボーナスは密な配置を促進し、偶発的なマージを確保するインセンティブとして機能。（3）HIGHフェーズheight_mult=1.8に緩和：v128の成功構造を復帰。盤面を低く保ちつつマージ機会を確保。v186のheight_mult=2.2と比較し、v128の1.8を維持することで、HIGHフェーズでのマージ機会確保と高度管理のバランスをとる。（4）HIGH_TOWERペナルティ閾値0.5に緩和：v128の成功構造を復帰。v186の0.5を維持し、v128の1.3倍を採用。（5）ドリフトペナルティ30.0に復帰：v128の成功構造を復帰。v186の40.0から30.0に戻す。（6）バランス補正を左右カウントベースに戻す：v128のシンプルな構造を復帰。重心ベースは理論的に正確だが、v128のスコア3689を達成したカウントベースの方が実績がある。（7）v42のシンプル構造を維持：マージボーナス（DIRECT=1200/NEAR=600/FAR=200）、高度管理（height_mult MEDIUM=2.4/HIGH=1.8）、TOWERペナルティ（HIGH=1.3倍/MEDIUM=1.5倍、閾値0.5）、ドリフトペナルティ（30.0）、バランス補正（HIGH=40.0/MEDIUM=30.0/LOW=20.0）のシンプル構造を維持。振り子パターン（v184-v186）をv128完全復帰で解消。コード量削減（約105行→約110行）。失敗（スコア540）：履歴分析でv187の失敗原因を特定：（1）盤面上昇速度がv128よりはるかに速い：turn 34でmax_y=-0.52 → turn 51でmax_y=3.19。1ターンあたり平均0.16上昇。（2）HIGHフェーズでマージが1回も発生していない：turn 34-50（HIGHフェーズ17ターン）でマージ頻度0回。（3）HIGHフェーズのheight_mult=1.8が緩和しすぎ：v128のheight_mult=1.8はv128の盤面で機能したが、v187の盤面では機能していない。（4）HIGH_TOWERペナルティが不十分：v187のHIGH_TOWERペナルティは1.3倍だが、height_mult=1.8との組み合わせでは、高層配置を十分に抑制できていない。（5）v128の成功構造を復帰したが、盤面上昇速度の抑制に失敗：v128のheight_mult=1.8は盤面上昇を抑制するためではなく、マージ機会を確保するための緩和だった。v187では盤面上昇速度が速く、まずは盤面上昇を抑制する必要がある。（6）振り子パターン（v185-v187のdensityボーナス追加・削除）が継続：v186の失敗（755）でdensityボーナス削除したが、v187の失敗（540）でまた別の問題が発生。（7）振り子パターン解消には、densityボーナスの追加も削除もせず、盤面上昇速度を抑制するアプローチが必要。
# v188: 高度管理強化・重心ベース版 - v187の失敗（スコア540、盤面上昇速度速・HIGHフェーズマージ0回）を受けて、盤面上昇速度を抑制する根本的な改善を実施。（1）HIGHフェーズheight_multを2.6に戻す：v187のheight_mult=1.8が緩和しすぎ、v128と同じ構造に復帰したにもかかわらず盤面上昇速度がはるかに速い。v42のheight_mult=2.6に戻し、盤面上昇を抑制。（2）HIGH_TOWERペナルティを1.5倍に強化：v187のHIGH_TOWERペナルティ1.3倍は不十分。v42の2.0倍とv187の1.3倍の中間値1.5倍を採用し、高層配置を抑制。（3）バランス補正を重心ベースに変更：v186の重心ベース計算を採用し、盤面の重心から遠い位置へのドロップをペナルティとする。より正確なバランス評価。（4）ドリフトペナルティを35.0に微増：v187の30.0から35.0に微増し、正確なドロップを促進。（5）v186のバランス補正を採用しつつ、densityボーナスは削除：v186の失敗原因はdensityボーナスの副作用だが、バランス補正は有効。バランス補正のみを採用。（6）振り子パターン（v185-v187のdensityボーナス追加・削除）を第三の選択肢で解消：densityボーナスは追加も削除もせず、盤面上昇速度を抑制するアプローチ（height_mult強化・HIGH_TOWERペナルティ強化・重心ベースバランス補正）を採用。（7）v42のシンプル構造を維持：マージボーナス（DIRECT=1200/NEAR=600/FAR=200）、高度管理（height_mult MEDIUM=2.4/HIGH=2.6）、TOWERペナルティ（HIGH=1.5倍/MEDIUM=1.5倍、閾値0.5）、ドリフトペナルティ（35.0）、バランス補正（重心ベース）のシンプル構造を維持。コード量維持（約110行）。失敗（スコア1427）：履歴分析でv188の失敗原因を特定：（1）HIGHフェーズでマージがほとんど発生していない：HIGHフェーズ16ターン中、マージは2回（12.5%）のみ。HIGH_TOWERが12回（80%）を占める。（2）重心ベースバランス補正の副作用：盤面の重心xは-0.17（左側に寄っている）。重心ベース補正は重心から遠い位置へのドロップをペナルティするが、右側へのドロップは大きなペナルティ（x=3.0で158.6）を受ける。これにより、バランス改善を促進できていない。（3）盤面のバランスが悪化：HIGHフェーズのドロップ位置は左側8回、中央6回、右側4回で、左側へのドロップが多い。（4）振り子パターン（v186-v188）が継続：重心ベースバランス補正（v186）→左右カウントベース（v187）→重心ベース（v188）で振り子。（5）HIGHフェーズマージ率が低い原因：重心ベース補正が右側へのドロップを阻害し、バランス改善とマージ機会のトレードオフを生んでいる。（6）振り子パターン解消には、v128/v42統合が必要：v128の左右カウントベースとv42の強固な高度管理を統合。
# v189: v128/v42統合・カウントベース版 - v188の失敗（スコア1427、HIGHフェーズマージ率12.5%）を受けて、v188の問題を特定し、v128/v42統合を実施。（1）重心ベースバランス補正の副作用を解消：v186-v188の振り子パターン（重心ベース↔カウントベース）を解消し、v128の成功した左右カウントベースに戻す。重心ベース補正は重心から遠い位置へのドロップをペナルティするが、左右カウントベースは少ない側へのドロップをボーナスとして扱う。（2）マージボーナス強化：HIGHフェーズでのマージを優先するため、マージボーナスを強化する。v84の強力な値（DIRECT=1500/NEAR=800/FAR=300）を採用し、v128/v42（DIRECT=1200/NEAR=600/FAR=200）よりもマージの質を重視。（3）HIGHフェーズheight_multをv128とv42の中間値に緩和：v42のheight_mult=2.6は盤面上昇を抑制するが、マージ機会を損なう。v128のheight_mult=1.8はマージ機会を確保するが、盤面上昇速度が速い。中間値2.2を採用し、盤面上昇を抑制しつつマージ機会を確保。（4）TOWERペナルティ閾値を強化：v42のv42の0.3を参考に、v188の0.5から0.4に厳しくし、高層配置を抑制。（5）HIGH_TOWERペナルティを緩和：v128の1.3倍に戻し、v188の1.5倍から緩和。height_mult緩和（2.6→2.2）と相乗効果。（6）MEDIUM_TOWERペナルティを強化：v42のv42の1.5倍を参考に、v188の1.5倍から1.8倍に強化。（7）バランス補正強度を調整：v128のv128の値（HIGH=40.0/MEDIUM=30.0/LOW=20.0）を採用し、v188のv188の値（HIGH=50.0/MEDIUM=40.0）より緩和。（8）ドリフトペナルティをv128に戻す：v188の35.0から30.0に戻し、v128の成功構造を維持。（9）v128/v42統合のバランス：v128の成功要素（左右カウントベース、height_mult=1.8緩和、バランス補正v128の値）とv42の成功要素（height_mult MEDIUM=2.4強化、TOWERペナルティ0.3閾値、HIGH_TOWER 2.0倍強化）を統合。コード量維持（約110行）。


def decide(game_state: dict, analysis: dict) -> dict:
    """v128/v42統合・カウントベース版

    v188の失敗（スコア1427、HIGHフェーズマージ率12.5%）を受けて、
    v128/v42統合を実施。

    v188履歴分析で確認した問題:
    - HIGHフェーズでマージがほとんど発生していない（12.5%）
    - 重心ベースバランス補正の副作用（右側へのドロップが大きなペナルティ）
    - 振り子パターン（v186-v188の重心ベース↔カウントベース）が継続

    解決策:
    - 重心ベースバランス補正から左右カウントベースに戻す（v128の成功要素）
    - マージボーナス強化（HIGHフェーズでのマージを優先）
    - HIGHフェーズheight_multをv128とv42の中間値に緩和
    - TOWERペナルティ閾値を強化（v42の0.3を参考）
    - HIGH_TOWERペナルティを緩和（v128の1.3倍に戻す）
    - MEDIUM_TOWERペナルティを強化（1.8倍）
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

    # フェーズ判定
    if max_y < 0.8:
        phase = "LOW"
        height_mult = 1.0
        merge_mult = 1.2
    elif max_y < 1.8:
        phase = "MEDIUM"
        height_mult = 2.4  # v189: v42の2.4を維持
        merge_mult = 1.0
    elif max_y < 3.0:
        phase = "HIGH"
        height_mult = 2.2  # v189: v128(1.8)とv42(2.6)の中間値
        merge_mult = 1.0
    else:
        phase = "CRITICAL"
        height_mult = 1.0  # CRITICAL: height_multなし
        merge_mult = 0.6  # v189: v42の0.6を維持

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

        # === v189: v128/v42統合・カウントベース ===

        # 1. マージグレードによるスコア（v189: マージボーナス強化）
        if merge_grade == "DIRECT":
            score += 1500.0 * merge_mult  # v189: v84の強力な値
            reasons.append("DIRECT_MERGE")
        elif merge_grade == "NEAR":
            score += 800.0 * merge_mult  # v189: v84の強力な値
            reasons.append("NEAR_MERGE")
        elif merge_grade == "FAR":
            score += 300.0 * merge_mult  # v189: v84の強力な値
            reasons.append("FAR_MERGE")

        # 2. 高度によるペナルティ（v189: HIGHフェーズheight_mult=2.2）
        height_penalty = landing_y * 50.0 * height_mult

        # TOWERペナルティ（v189: v42の閾値0.3を参考、HIGH_TOWERはv128の1.3倍）
        if phase == "HIGH" and landing_y > 0.4:  # v189: 閾値を0.4に厳しく
            height_penalty *= 1.3  # v189: v128の1.3倍に戻す
            reasons.append("HIGH_TOWER")
        elif phase == "MEDIUM" and landing_y > 0.4:  # v189: 閾値を0.4に厳しく
            height_penalty *= 1.8  # v189: v42の1.5倍を強化
            reasons.append("MEDIUM_TOWER")
        elif landing_y > 0.0:
            reasons.append("HIGH_LAYER")

        score -= height_penalty

        # 3. ドリフトによるペナルティ（v189: v128の30.0に戻す）
        drift_penalty = (abs(drift_x) + drift_unc) * 30.0
        score -= drift_penalty

        # 4. 左右バランス補正（v189: 左右カウントベースに戻す、v128の値を採用）
        balance_strength = 20.0
        if phase == "HIGH":
            balance_strength = 40.0  # v189: v128の40.0を維持
        elif phase == "MEDIUM":
            balance_strength = 30.0  # v189: v128の30.0を維持

        left_count = sum(1 for p in pieces if p["x"] < 0)
        right_count = len(pieces) - left_count
        balance_bias = (right_count - left_count) / (len(pieces) if pieces else 1)

        balance_penalty = x * balance_bias * balance_strength
        score -= abs(balance_penalty)

        # 5. nextNextが同じタイプなら中央寄せボーナス（v189: v42の一律50.0を維持）
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
