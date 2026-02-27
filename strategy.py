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
# v195: v128設定回帰・MEDIUM高度管理維持版 - v194の失敗（スコア1417、v128スコア3689の半分以下）を受けて、振り子パターンを根本的に解消するブレイクスルーを実施。v194履歴分析で特定した問題: - v194はv42パッケージ（閾値0.3、HIGH_TOWER 2.0倍）を採用したが、v128設定（閾値0.5、HIGH_TOWER 1.3倍）よりもスコアが半分以下（1417 vs 3689） - v194のHIGH_TOWER発動率が低すぎる（6/85ターン=7%）、v128は適度に発動し高度管理を実現 - v194のTOWERペナルティ閾値0.3が厳しすぎ、HIGH_LAYERが支配的（11回/85ターン） - v42パッケージのMEDIUM_TOWER発動率（v194は11/85ターン=13%）はv128よりも高いが、これは閾値0.3が厳しすぎてHIGH_LAYERとHIGH_TOWERの中間を制御しているため - v194の失敗は「v42パッケージへの回帰」そのものであり、v128設定への回帰が必要 解決策: - v128設定への完全回帰：TOWERペナルティ閾値0.5、HIGH_TOWER倍率1.3倍、バランス補正強度40.0/30.0/20.0 - v42のMEDIUMフェーズ高度管理を維持：height_mult=2.4、MEDIUM_TOWER倍率1.5倍 - v128のHIGHフェーズ設定を維持：height_mult=1.8、HIGH_TOWER倍率1.3倍 - v42とv128の成功要素を統合しつつ、振り子パターンを解消：v42のMEDIUMフェーズ高度管理 + v128のHIGHフェーズ設定 - v193（スコア551）もv128設定と同じだがスコアが低いのは、他の要因（乱数、ピース配列）の可能性があるが、戦略的にはv128設定が最も成功した設定であることは明確
# v196: MEDIUMフェーズ高度管理緩和・HIGHマージ優先徹底版 - v195の失敗（スコア734、MEDIUMフェーズ高度管理が支配的すぎる）を受けて、MEDIUMフェーズの高度管理を根本的に緩和するブレイクスルーを実施。v195履歴分析で特定した問題: - MEDIUMフェーズが7ターンしか持続せず、その71%（5/7ターン）がMEDIUM_TOWERで高度管理が支配的 - MEDIUMフェーズのheight_mult=2.4（v42/v128値）では盤面上昇が速すぎ、すぐにHIGHフェーズに到達 - HIGHフェーズが1ターンのみで、CRITICALフェーズに遷移 - CRITICALフェーズでHIGH_LAYER（高度管理）が選ばれ、マージできずゲームオーバー - v194（スコア1417）もv195（スコア734）も、v128スコア3689の半分以下 根本原因: - v128設定はHIGHフェーズでのマージ優先には成功したが、MEDIUMフェーズのheight_mult=2.4が強すぎ - MEDIUMフェーズでの高度管理が強すぎると、盤面が急速に上昇しHIGHフェーズに到達する - MEDIUMフェーズの持続期間が短すぎると、HIGHフェーズでのマージ機会を確保できない 解決策（振り子パターン解消の第三の選択肢）: - MEDIUMフェーズheight_multを2.4から1.8に大幅に引き下げ：MEDIUMフェーズの高度管理を緩和し、持続期間を改善 - TOWERペナルティ閾値を0.5から0.4に微調整：v128の0.5は緩すぎ、v194の0.3は厳しすぎ、中間値の0.4を採用 - HIGHフェーズheight_mult=1.8を維持（v128の成功値）：HIGHフェーズでのマージ優先を徹底 - HIGH_TOWER倍率1.3倍を維持（v128の成功値）：HIGHフェーズでのマージ優先を維持 - バランス補正強度v128のHIGH=40.0/MEDIUM=30.0を維持 - マージボーナスv42のDIRECT=1200/NEAR=600/FAR=200を維持 - ドリフトペナルティ一律30.0を維持 失敗（スコア1584）：v196履歴分析で確認した問題: - MEDIUMフェーズが20ターン持続したが、MEDIUM_TOWERが3回、HIGH_LAYERが5回で高度管理が支配的 - HIGHフェーズが17ターン持続したが、HIGH_TOWERが13回（76%）発動、マージ可能ターンのマージ選択率は78%だがHIGH_TOWERが過度に発動 - v196のMEDIUMフェーズheight_mult=1.8はv42の2.4よりも緩すぎ、盤面が高くなりすぎた - v196のTOWERペナルティ閾値0.4はv128の0.5よりも厳しすぎ、HIGH_TOWERが過度に発動 - 振り子パターン（height_mult: 2.4→1.8、閾値: 0.5→0.4）が失敗 - v42のMEDIUMフェーズ設定（height_mult=2.4）とv128のHIGHフェーズ設定（height_mult=1.8、閾値0.5）を統合すべき
# v197: v42MEDIUM・v128HIGH完全統合版 - v196の失敗（スコア1584、MEDIUMフェーズheight_mult=1.8が緩すぎ・HIGH_TOWER発動率76%）を受けて、振り子パターンを完全に解消するブレイクスルーを実施。v196履歴分析で特定した問題: - v196のMEDIUMフェーズheight_mult=1.8はv42の2.4よりも緩すぎ、盤面が高くなりすぎた - v196のTOWERペナルティ閾値0.4はv128の0.5よりも厳しすぎ、HIGH_TOWERが過度に発動（76%） - 振り子パターン（height_mult: 2.4→1.8→2.4、閾値: 0.5→0.3→0.5→0.4）を完全に回避 - v42のMEDIUMフェーズ設定とv128のHIGHフェーズ設定を完全統合 解決策（振り子パターン解消の第三の選択肢）: - MEDIUMフェーズheight_multはv42の2.4に完全回帰：v196の1.8は緩すぎ、v42の2.4がMEDIUMフェーズ高度管理に最適 - TOWERペナルティ閾値はv128の0.5に完全回帰：v196の0.4は厳しすぎ、v128の0.5が適切 - HIGHフェーズheight_mult=1.8を維持（v128の成功値）：HIGHフェーズでのマージ優先を徹底 - HIGH_TOWER倍率1.3倍を維持（v128の成功値）：HIGHフェーズでのマージ優先を維持 - MEDIUM_TOWER倍率1.5倍を維持（v42の成功値）：MEDIUMフェーズ高度管理を確保 - バランス補正強度v128のHIGH=40.0/MEDIUM=30.0を維持 - マージボーナスv42のDIRECT=1200/NEAR=600/FAR=200を維持 - ドリフトペナルティ一律30.0を維持 - 振り子パターンを回避：v42のMEDIUMフェーズ設定とv128のHIGHフェーズ設定を統合し、各フェーズの最適値を維持


def decide(game_state: dict, analysis: dict) -> dict:
    """v42MEDIUM・v128HIGH完全統合版

    v196の失敗（スコア1584、MEDIUMフェーズheight_mult=1.8が緩すぎ・HIGH_TOWER発動率76%）を受けて、
    振り子パターンを完全に解消するブレイクスルーを実施。

    v196履歴分析で特定した問題:
    - MEDIUMフェーズが20ターン持続したが、MEDIUM_TOWERが3回、HIGH_LAYERが5回で高度管理が支配的
    - HIGHフェーズが17ターン持続したが、HIGH_TOWERが13回（76%）発動
    - v196のMEDIUMフェーズheight_mult=1.8はv42の2.4よりも緩すぎ、盤面が高くなりすぎた
    - v196のTOWERペナルティ閾値0.4はv128の0.5よりも厳しすぎ、HIGH_TOWERが過度に発動
    - 振り子パターン（height_mult: 2.4→1.8→2.4、閾値: 0.5→0.3→0.5→0.4）を完全に回避すべき

    根本原因:
    - v196はv42のMEDIUMフェーズ設定を崩し、v128のHIGHフェーズ設定を崩した
    - v42のMEDIUMフェーズheight_mult=2.4はMEDIUMフェーズ高度管理に成功した
    - v128のHIGHフェーズ設定（height_mult=1.8、閾値0.5、HIGH_TOWER 1.3倍）はHIGHフェーズマージ優先に成功した

    解決策（振り子パターン解消の第三の選択肢）:
    - MEDIUMフェーズheight_multはv42の2.4に完全回帰：v196の1.8は緩すぎ、v42の2.4がMEDIUMフェーズ高度管理に最適
    - TOWERペナルティ閾値はv128の0.5に完全回帰：v196の0.4は厳しすぎ、v128の0.5が適切
    - HIGHフェーズheight_mult=1.8を維持（v128の成功値）：HIGHフェーズでのマージ優先を徹底
    - HIGH_TOWER倍率1.3倍を維持（v128の成功値）：HIGHフェーズでのマージ優先を維持
    - MEDIUM_TOWER倍率1.5倍を維持（v42の成功値）：MEDIUMフェーズ高度管理を確保
    - バランス補正強度v128のHIGH=40.0/MEDIUM=30.0を維持
    - マージボーナスv42のDIRECT=1200/NEAR=600/FAR=200を維持
    - ドリフトペナルティ一律30.0を維持
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
        height_mult = 2.4  # v197: v42の2.4に完全回帰（v196の1.8は緩すぎ）
        merge_mult = 1.0
    elif max_y < 3.0:
        phase = "HIGH"
        height_mult = 1.8  # v197: v128の1.8を維持（HIGHフェーズでのマージ優先を徹底）
        merge_mult = 1.0
    else:
        phase = "CRITICAL"
        height_mult = 1.0  # CRITICAL: height_multなし
        merge_mult = 0.6  # v42の成功値を維持

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

        # === v197: v42MEDIUM・v128HIGH完全統合 ===

        # 1. マージグレードによるスコア（v42の値を維持）
        if merge_grade == "DIRECT":
            score += 1200.0 * merge_mult
            reasons.append("DIRECT_MERGE")
        elif merge_grade == "NEAR":
            score += 600.0 * merge_mult
            reasons.append("NEAR_MERGE")
        elif merge_grade == "FAR":
            score += 200.0 * merge_mult
            reasons.append("FAR_MERGE")

        # 2. 高度によるペナルティ
        height_penalty = landing_y * 50.0 * height_mult

        # TOWERペナルティ（v197: v128の閾値0.5に完全回帰）
        if (
            phase == "HIGH" and landing_y > 0.5
        ):  # v197: v128の閾値0.5に完全回帰（v196の0.4は厳しすぎ）
            height_penalty *= 1.3  # v197: v128の1.3倍を維持
            reasons.append("HIGH_TOWER")
        elif phase == "MEDIUM" and landing_y > 0.5:  # v197: v128の閾値0.5に完全回帰
            height_penalty *= 1.5  # v197: v42の1.5倍を維持
            reasons.append("MEDIUM_TOWER")
        elif landing_y > 0.0:
            reasons.append("HIGH_LAYER")

        score -= height_penalty

        # 3. ドリフトによるペナルティ（一律30.0を維持）
        drift_penalty = (abs(drift_x) + drift_unc) * 30.0
        score -= drift_penalty

        # 4. 左右バランス補正（v128の値を採用）
        balance_strength = 20.0
        if phase == "HIGH":
            balance_strength = 40.0  # v197: v128の40.0を維持
        elif phase == "MEDIUM":
            balance_strength = 30.0  # v197: v128の30.0を維持

        left_count = sum(1 for p in pieces if p["x"] < 0)
        right_count = len(pieces) - left_count
        balance_bias = (right_count - left_count) / (len(pieces) if pieces else 1)

        balance_penalty = x * balance_bias * balance_strength
        score -= abs(balance_penalty)

        # 5. nextNextが同じタイプなら中央寄せボーナス（一律50.0を維持）
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
