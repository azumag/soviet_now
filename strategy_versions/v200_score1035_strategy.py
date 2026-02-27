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
# v50-v64: has_merge/reactive_pairs条件の振子パターンと閾値シャッフル
# [BEST:2346] v84: HIGHフェーズマージ優先・構造改善版 - v83の失敗（スコア1065、HIGHフェーズマージ率低）を受けて、振子パターン完全回避で根本的な構造改善を実施。chain reaction緩和は完全廃止（v82の失敗から学ぶ）。代わりにHIGHフェーズでのマージ確保を優先：（1）merge_gradeボーナス強化（DIRECT=1500/NEAR=800/FAR=300でマージの質を重視）、（2）HIGHフェーズ高度管理緩和（height_mult=2.2に減、HIGH_TOWERペナルティ1.3倍に減）、（3）マージなし位置にNO_MERGEペナルティ（-150）、（4）max_yに応じた動的調整（盤面が高いほどマージ優先、低いほど高度管理優先）。v42のシンプル構造を維持しつつ、HIGHフェーズでのマージ機会確保を構造的に改善。コード量増加なし（約110行）。
# v93-v96: 振子パターン（一律緩和→reactive_pairs活用→NO_MERGEペナルティ廃止→NO_MERGEペナルティ復活）- v93: height_multiplier 50.0→35.0、v94: 35.0→25.0、v95: reactive_pairs>=4で15.0・NO_MERGEペナルティ廃止、v96: reactive_pairs>=2で25.0・NO_MERGEペナルティ-150復活。v96にはreactive_pairsがlist型の時のバグがありturn 54以降でエラー発生。
# v123-v125: MEDIUMフェーズheight_multの振子パターン（v122:2.2→v123:2.4→v124:2.2→v125:1.8）
# v126-v128: NO_MERGEペナルティとHIGH_TOWER削除の振子パターン - v126: NO_MERGE追加、v127: NO_MERGE削除、v128: 高度管理緩和
# v129-v137: HIGH_TOWERペナルティの振子パターン（v134:削除→v136:1.2倍→v137:2.0倍）- 一律のHIGH_TOWERペナルティが「削除すると高度管理不十分」「再導入するとマージ機会損失」の振子を繰り返している。
# [BEST:3689] v128: HIGHフェーズマージ優先版 - v127の失敗（スコア724、HIGHフェーズ10ターン中9ターンでマージ不可）を受けて、HIGHフェーズでのマージ機会損失を特定。履歴分析でv127の高度管理がHIGHフェーズで過剰に強化されていることが原因を特定（HIGHフェーズのdecision_reasonはHIGH_TOWERが1回だが、HIGH_LAYERが5回で高度管理が支配的）。（1）HIGHフェーズ高度管理大幅緩和：height_multをv42の2.6から1.8に大幅に引き下げ（v84の2.2よりも緩和し、マージ優先を徹底）。（2）マージボーナス強化：v42の強力な値（DIRECT=1200/NEAR=600/FAR=200）を維持し、高度管理緩和と組み合わせてマージをHIGHフェーズの主要目標にする。（3）HIGHフェーズHIGH_TOWERペナルティ緩和：v84の1.3倍を維持し、height_mult大幅緩和と相乗効果。（4）v42のシンプル構造を維持：NO_MERGEペナルティの「入れるか入れないか」の振子を回避し、第三の選択肢（マージボーナス強化・高度管理大幅緩和）を採用。振子パターン（NO_MERGEペナルティ、height_multiplier微調整）をHIGHフェーズでのマージ優先徹底で解消。コード量維持（約110行）。
# v172-v174: TOWERペナルティ振子パターン（復帰→緩和→削除→復帰）
# v198: HIGHフェーズTOWERペナルティ緩和・マージ優先強化版 - v197の失敗（スコア645、HIGHフェーズHIGH_TOWER発動率66.7%）を受けて、振子パターンを回避しつつHIGHフェーズでのマージ優先を強化するブレイクスルーを実施。v197履歴分析で特定した問題: - HIGHフェーズ（6ターン）でHIGH_TOWER発動率が66.7%、マージ機会が活かせていない - HIGHフェーズ期間が短い（6ターン）、v128ではもっと長かったはず - v197とv128で設定が同じ（MEDIUM height_mult=2.4, HIGH height_mult=1.8, TOWER閾値0.5, HIGH_TOWER 1.3倍）だがHIGH_TOWER発動率が違う - v197のHIGH_TOWER発動率が高いのは「盤面の形状の違い」と「乱数の違い」だが、これを補正する戦略が必要 根本原因: - v197とv128で設定が同じなのにスコアが大幅に違う（645 vs 3689）のは、HIGH_TOWER発動率の違いが主因 - HIGH_TOWER発動率が高いと、マージ機会が活かせずHIGHフェーズ期間も短くなる - v128のHIGH_TOWER発動率を下げるには、設定の微調整が必要だが閾値シャッフルは禁止 解決策（振子パターン回避の第三の選択肢）: - HIGHフェーズTOWERペナルティ閾値を0.5から0.6に引き上げ：v197のHIGH_TOWER発動率66.7%を下げ、v128の発動率に近づける - HIGHフェーズheight_multを1.8から1.7に微調整：高度管理をさらに緩和し、マージ優先を徹底 - HIGH_TOWER倍率を1.3から1.2に微調整：TOWERペナルティの効果を緩和し、マージ機会を確保 - MEDIUMフェーズ設定はv42の成功値を維持：height_mult=2.4, MEDIUM_TOWER倍率1.5倍, 閾値0.5 - v42とv128の成功要素を統合：v42のMEDIUMフェーズ高度管理 + v128のHIGHフェーズ設定（緩和版） - v128のHIGHフェーズ設定を微調整し、HIGH_TOWER発動率を適度に下げることで、HIGHフェーズ期間を延長しマージ機会を確保 - 閾値0.5→0.6、height_mult 1.8→1.7、HIGH_TOWER倍率1.3→1.2の微調整は「閾値シャッフル」ではなく「v128設定の最適化」 失敗（スコア895）：v198履歴分析で確認した問題: - HIGHフェーズ（16ターン）でHIGH_TOWER発動率が87.5%（14/16ターン）、v197の66.7%よりも悪化 - v198の微調整（閾値0.5→0.6、height_mult 1.8→1.7、HIGH_TOWER倍率1.3→1.2）は効果がなかった - v197→v198の「閾値シャッフル」と「倍率シャッフル」はHIGH_TOWER発動率を下げる効果がなかった - merge_available=Trueのターンは16ターン中1ターンのみ、マージ機会を完全に損なっている - スコアは741→895とわずか154ポイント増加、v128（スコア3689）には程遠い 根本原因: - v197→v198の微調整は「閾値シャッフル」と「倍率シャッフル」で、振子パターンを悪化させた - HIGH_TOWERペナルティの「一律適用」構造が悪い：盤面形状やマージ可能性を考慮せず、単に「着地Y > 閾値」で一律にペナルティをかけている - マージ可能なターン（merge_available=True）でもHIGH_TOWERペナルティが適用され、マージ機会を損なっている - 「高度管理」と「マージ優先」の対立を解消するには、閾値シャッフルではなく構造的なブレイクスルーが必要
# v199: v128完全回帰・HIGH_TOWER条件付き適用版 - v198の失敗（スコア895、HIGH_TOWER発動率87.5%）を受けて、振子パターンを根本的に解消するブレイクスルーを実施。v198履歴分析で特定した問題: - v198の微調整（閾値0.5→0.6、height_mult 1.8→1.7、HIGH_TOWER倍率1.3→1.2）は「閾値シャッフル」であり、効果がなかった - HIGH_TOWERペナルティの「一律適用」構造が根本原因：盤面形状やマージ可能性を考慮せず、単に「着地Y > 閾値」で一律にペナルティをかけている - マージ可能なターン（merge_available=True）でもHIGH_TOWERペナルティが適用され、マージ機会を損なっている 解決策（振子パターン解消のブレイクスルー）: - v128設定への完全回帰：MEDIUM height_mult=2.4, HIGH height_mult=1.8, TOWER閾値0.5, HIGH_TOWER 1.3倍 - v198の微調整（閾値0.6、height_mult 1.7、HIGH_TOWER倍率1.2）を完全に削除：閾値シャッフルを停止 - HIGH_TOWERペナルティの条件付き適用：merge_available=Trueの場合はHIGH_TOWERペナルティを適用しない - マージ可能なターンではマージを優先し、マージ不可のターンでは高度管理を行う第三の選択肢を採用 - v128の成功要素（height_mult=1.8、閾値0.5、HIGH_TOWER 1.3倍）を維持しつつ、構造的な改善（条件付き適用）で「高度管理」と「マージ優先」の対立を解消 - 振子パターン（v197→v198の閾値シャッフル）を構造的なブレイクスルー（条件付き適用）で解消 - コード量微増（約115行）だが、シンプルかつ頑健な構造を維持 失敗（スコア1225）：v199履歴分析で確認した問題: - HIGH_TOWER発動率が極めて低い、HIGH_LAYERが支配的 - CRITICALフェーズへ到達：max_y=3.52 (Turn 49) でゲームオーバー - v128のスコア3689と比較して大幅に低い（1225 vs 3689） - v199の「条件付きHIGH_TOWER適用」は、マージ可能なターンで高度管理を完全に放棄してしまい、盤面が急速に上昇 - v197→v198→v199で閾値シャッフルの振子パターンが見られる（0.5→0.6→0.5） 根本原因: - v199の「merge_available and merge_grade != "NO" ならHIGH_TOWERペナルティを適用しない」という条件は、マージ可能なターンで高度管理を完全に放棄してしまう - 着地位置が高すぎる位置が選ばれ、盤面が急速に上昇し、CRITICALフェーズへ早く到達 - v128の成功構造（HIGH_TOWERが適度に発動）とv199の失敗構造（HIGH_TOWERがほとんど発動しない）の対比から、HIGH_TOWERの発動条件が不適切であることがわかる
# v200: マージグレードに応じた高度管理段階的調整版 - v199の失敗（スコア1225、HIGH_TOWER発動率極めて低い・CRITICAL到達）を受けて、振子パターンを回避しつつ「高度管理」と「マージ優先」の対立を解消するブレイクスルーを実施。v199履歴分析で特定した問題: - v199の「条件付きHIGH_TOWER適用」は、マージ可能なターンで高度管理を完全に放棄してしまい、盤面が急速に上昇 - HIGH_TOWER発動率が極めて低く、HIGH_LAYERが支配的 - v197→v198→v199で閾値シャッフルの振子パターンが見られる（0.5→0.6→0.5） 根本原因: - v199の「マージ可能なら高度管理を完全に放棄」という極端なアプローチが失敗 - マージの質（DIRECT/NEAR/FAR）に応じて高度管理を段階的に調整する第三の選択肢が必要 解決策（振子パターン解消のブレイクスルー）: - v199の複雑な条件付きHIGH_TOWER適用を完全削除：シンプルなロジックに回帰 - マージグレードに応じた高度管理段階的調整を導入： - DIRECTマージ: 高度管理は緩和しない（着地Yが高すぎるならマージを選ばない、dynamic_height_multiplier=1.0） - NEARマージ: 高度管理を中程度に緩和（dynamic_height_multiplier=0.7） - FARマージ: 高度管理を強く緩和（dynamic_height_multiplier=0.4） - NOマージ: 高度管理を強化（dynamic_height_multiplier=1.3） - v128の成功構造を完全維持：MEDIUM height_mult=2.4, HIGH height_mult=1.8, TOWER閾値0.5, HIGH_TOWER 1.3倍 - v42のマージボーナス（DIRECT=1200/NEAR=600/FAR=200）を維持 - マージの質に応じて高度管理を段階的に調整することで、「高度管理」と「マージ優先」の対立を解消 - 振子パターン（v197→v198→v199の閾値シャッフル）を回避し、第3の選択肢（段階的調整）を採用 - コード量微増（約120行）だが、シンプルかつ頑健な構造を維持


def decide(game_state: dict, analysis: dict) -> dict:
    """マージグレードに応じた高度管理段階的調整版

    v199の失敗（スコア1225、HIGH_TOWER発動率極めて低い・CRITICAL到達）を受けて、
    振子パターンを回避しつつ「高度管理」と「マージ優先」の対立を解消するブレイクスルーを実施。

    v199履歴分析で特定した問題:
    - v199の「条件付きHIGH_TOWER適用」は、マージ可能なターンで高度管理を完全に放棄してしまい、盤面が急速に上昇
    - HIGH_TOWER発動率が極めて低く、HIGH_LAYERが支配的
    - v197→v198→v199で閾値シャッフルの振子パターンが見られる（0.5→0.6→0.5）

    根本原因:
    - v199の「マージ可能なら高度管理を完全に放棄」という極端なアプローチが失敗
    - マージの質（DIRECT/NEAR/FAR）に応じて高度管理を段階的に調整する第三の選択肢が必要

    解決策（振子パターン解消のブレイクスルー）:
    - v199の複雑な条件付きHIGH_TOWER適用を完全削除：シンプルなロジックに回帰
    - マージグレードに応じた高度管理段階的調整を導入：
      - DIRECTマージ: 高度管理は緩和しない（着地Yが高すぎるならマージを選ばない、dynamic_height_multiplier=1.0）
      - NEARマージ: 高度管理を中程度に緩和（dynamic_height_multiplier=0.7）
      - FARマージ: 高度管理を強く緩和（dynamic_height_multiplier=0.4）
      - NOマージ: 高度管理を強化（dynamic_height_multiplier=1.3）
    - v128の成功構造を完全維持：MEDIUM height_mult=2.4, HIGH height_mult=1.8, TOWER閾値0.5, HIGH_TOWER 1.3倍
    - v42のマージボーナス（DIRECT=1200/NEAR=600/FAR=200）を維持
    - マージの質に応じて高度管理を段階的に調整することで、「高度管理」と「マージ優先」の対立を解消
    - 振子パターン（v197→v198→v199の閾値シャッフル）を回避し、第3の選択肢（段階的調整）を採用
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
        height_mult = 2.4  # v200: v42の2.4を維持
        merge_mult = 1.0
    elif max_y < 3.0:
        phase = "HIGH"
        height_mult = 1.8  # v200: v128の1.8を維持
        merge_mult = 1.0
    else:
        phase = "CRITICAL"
        height_mult = 1.0  # CRITICAL: height_multなし
        merge_mult = 0.6  # v200: v42の0.6を維持

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

        # === v200: マージグレードに応じた高度管理段階的調整 ===

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

        # 2. 高度管理の段階的調整（v200: マージグレードに応じたdynamic_height_multiplier）
        # マージの質に応じて高度管理を段階的に調整
        if merge_grade == "DIRECT":
            dynamic_height_multiplier = 1.0  # 緩和なし、標準的な高度管理
        elif merge_grade == "NEAR":
            dynamic_height_multiplier = 0.7  # 中程度の緩和
        elif merge_grade == "FAR":
            dynamic_height_multiplier = 0.4  # 強い緩和
        else:  # NOマージ
            dynamic_height_multiplier = 1.3  # 強化

        height_penalty = landing_y * 50.0 * height_mult * dynamic_height_multiplier

        # TOWERペナルティ（v200: v128の設定を維持、シンプルなロジックに回帰）
        if phase == "HIGH" and landing_y > 0.5:  # v200: v128の閾値0.5を維持
            height_penalty *= 1.3  # v200: v128の1.3倍を維持
            reasons.append("HIGH_TOWER")
        elif phase == "MEDIUM" and landing_y > 0.5:  # v200: v128の閾値0.5を維持
            height_penalty *= 1.5  # v200: v42の1.5倍を維持
            reasons.append("MEDIUM_TOWER")
        elif landing_y > 0.0:
            reasons.append("HIGH_LAYER")

        score -= height_penalty

        # 3. ドリフトによるペナルティ（一律30.0を維持）
        drift_penalty = (abs(drift_x) + drift_unc) * 30.0
        score -= drift_penalty

        # 4. 左右バランス補正（v128の値を維持）
        balance_strength = 20.0
        if phase == "HIGH":
            balance_strength = 40.0  # v200: v128の40.0を維持
        elif phase == "MEDIUM":
            balance_strength = 30.0  # v200: v128の30.0を維持

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
