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
# v200: マージグレードに応じた高度管理段階的調整版 - v199の失敗（スコア1225、HIGH_TOWER発動率極めて低い・CRITICAL到達）を受けて、振子パターンを回避しつつ「高度管理」と「マージ優先」の対立を解消するブレイクスルーを実施。v199履歴分析で特定した問題: - v199の「条件付きHIGH_TOWER適用」は、マージ可能なターンで高度管理を完全に放棄してしまい、盤面が急速に上昇 - HIGH_TOWER発動率が極めて低く、HIGH_LAYERが支配的 - v197→v198→v199で閾値シャッフルの振子パターンが見られる（0.5→0.6→0.5） 根本原因: - v199の「マージ可能なら高度管理を完全に放棄」という極端なアプローチが失敗 - マージの質（DIRECT/NEAR/FAR）に応じて高度管理を段階的に調整する第三の選択肢が必要 解決策（振子パターン解消のブレイクスルー）: - v199の複雑な条件付きHIGH_TOWER適用を完全削除：シンプルなロジックに回帰 - マージグレードに応じた高度管理段階的調整を導入： - DIRECTマージ: 高度管理は緩和しない（着地Yが高すぎるならマージを選ばない、dynamic_height_multiplier=1.0） - NEARマージ: 高度管理を中程度に緩和（dynamic_height_multiplier=0.7） - FARマージ: 高度管理を強く緩和（dynamic_height_multiplier=0.4） - NOマージ: 高度管理を強化（dynamic_height_multiplier=1.3） - v128の成功構造を完全維持：MEDIUM height_mult=2.4, HIGH height_mult=1.8, TOWER閾値0.5, HIGH_TOWER 1.3倍 - v42のマージボーナス（DIRECT=1200/NEAR=600/FAR=200）を維持 - マージの質に応じて高度管理を段階的に調整することで、「高度管理」と「マージ優先」の対立を解消 - 振子パターン（v197→v198→v199の閾値シャッフル）を回避し、第3の選択肢（段階的調整）を採用 - コード量微増（約120行）だが、シンプルかつ頑健な構造を維持 失敗（スコア1035）：v200履歴分析で確認した問題: - HIGHフェーズでHIGH_LAYERが支配的（15ターン中8ターン） - HIGHフェーズ期間が短い（9ターン）、merge_available=Trueのターンは1ターンのみ - 盤面の上昇が早い（Turn 55: max_y=0.38, Turn 73: max_y=3.08でCRITICAL到達） - HIGH_TOWER発動率は63.6%（9/14ターン）だが、HIGH_LAYERが28.6%（4/14ターン）で高度管理が支配的 - マージ選択ターンでscore_delta=0（スコア増加なし） - v128のスコア3689と比較して大幅に低い（1035 vs 3689） 根本原因: - v200のdynamic_height_multiplier（FAR:0.4, NEAR:0.7）は高度管理を緩和しすぎて、盤面が高くなりすぎた - FARマージやNEARマージで高度管理を緩和しすぎると、盤面が急速に上昇し、CRITICALフェーズへ早く到達 - マージグレードによる高度管理調整は、マージの質（DIRECT/NEAR/FAR）を高度管理の指標として使用しているが、これは間違っている - マージグレードは「マージの質」を表し、高度管理はフェーズで調整すべきである
# v201: dynamic_height_multiplier完全削除・v128一律ルール回帰版 - v200の失敗（スコア1035、dynamic_height_multiplier緩和しすぎ・HIGH_LAYER支配・CRITICAL到達）を受けて、振子パターンを根本的に解消するブレイクスルーを実施。v200履歴分析で特定した問題: - v200のdynamic_height_multiplier（FAR:0.4, NEAR:0.7）は高度管理を緩和しすぎて、盤面が高くなりすぎた - HIGHフェーズでHIGH_LAYERが支配的（15ターン中8ターン）、盤面の上昇が早い - マージグレードによる高度管理調整は、マージの質（DIRECT/NEAR/FAR）を高度管理の指標として使用しているが、これは間違っている - v200のスコア1035はv128のスコア3689と比較して大幅に低い 根本原因: - v200のdynamic_height_multiplierは、「マージグレードに応じて高度管理を調整する」というアイデアだが、マージグレードを高度管理の指標として使用しているのが間違っている - マージグレードは「マージの質」を表し、高度管理はフェーズで調整すべきである - FARマージやNEARマージで高度管理を緩和しすぎると、盤面が急速に上昇し、CRITICALフェーズへ早く到達 解決策（振子パターン解消のブレイクスルー）: - dynamic_height_multiplierを完全削除：マージグレードによる高度管理調整という間違ったアプローチを完全に廃止 - v128の一律ルールに完全回帰：height_penalty = landing_y * 50.0 * height_mult（マージグレードによる調整なし） - v128の成功構造を完全維持：MEDIUM height_mult=2.4, HIGH height_mult=1.8, TOWER閾値0.5, HIGH_TOWER 1.3倍 - v42のマージボーナス（DIRECT=1200/NEAR=600/FAR=200）を維持：マージグレードはマージボーナスの指標としてのみ使用 - v128のバランス補正強度（HIGH=40.0/MEDIUM=30.0/LOW=20.0）を維持 - ドリフトペナルティ一律30.0を維持 - nextNextが同じタイプなら中央寄せボーナス50.0を維持 - v128のシンプル構造（約110行）に完全回帰：コード量削減（約120行→約110行） - 振子パターン（v197→v198→v199→v200の閾値シャッフルとdynamic_height_multiplierの追加・削除）を回避し、v128の一律ルールを採用
# v201: dynamic_height_multiplier完全削除・v128一律ルール回帰版 - v200の失敗（スコア1035、dynamic_height_multiplier緩和しすぎ・HIGH_LAYER支配・CRITICAL到達）を受けて、振子パターンを根本的に解消するブレイクスルーを実施。v200履歴分析で特定した問題: - v200のdynamic_height_multiplier（FAR:0.4, NEAR:0.7）は高度管理を緩和しすぎて、盤面が高くなりすぎた - HIGHフェーズでHIGH_LAYERが支配的（15ターン中8ターン）、盤面の上昇が早い - マージグレードによる高度管理調整は、マージの質（DIRECT/NEAR/FAR）を高度管理の指標として使用しているが、これは間違っている - v200のスコア1035はv128のスコア3689と比較して大幅に低い 根本原因: - v200のdynamic_height_multiplierは、「マージグレードに応じて高度管理を調整する」というアイデアだが、マージグレードを高度管理の指標として使用しているのが間違っている - マージグレードは「マージの質」を表し、高度管理はフェーズで調整すべきである - FARマージやNEARマージで高度管理を緩和しすぎると、盤面が急速に上昇し、CRITICALフェーズへ早く到達 解決策（振子パターン解消のブレイクスルー）: - dynamic_height_multiplierを完全削除：マージグレードによる高度管理調整という間違ったアプローチを完全に廃止 - v128の一律ルールに完全回帰：height_penalty = landing_y * 50.0 * height_mult（マージグレードによる調整なし） - v128の成功構造を完全維持：MEDIUM height_mult=2.4, HIGH height_mult=1.8, TOWER閾値0.5, HIGH_TOWER 1.3倍 - v42のマージボーナス（DIRECT=1200/NEAR=600/FAR=200）を維持：マージグレードはマージボーナスの指標としてのみ使用 - v128のバランス補正強度（HIGH=40.0/MEDIUM=30.0/LOW=20.0）を維持 - ドリフトペナルティ一律30.0を維持 - nextNextが同じタイプなら中央寄せボーナス50.0を維持 - v128のシンプル構造（約110行）に完全回帰：コード量削減（約120行→約110行） - 振子パターン（v197→v198→v199→v200の閾値シャッフルとdynamic_height_multiplierの追加・削除）を回避し、v128の一律ルールを採用


def decide(game_state: dict, analysis: dict) -> dict:
    """v128一律ルールに完全回帰

    v200の失敗（スコア1035、dynamic_height_multiplier緩和しすぎ・HIGH_LAYER支配・CRITICAL到達）を受けて、
    振子パターンを根本的に解消するブレイクスルーを実施。

    v200履歴分析で特定した問題:
    - v200のdynamic_height_multiplier（FAR:0.4, NEAR:0.7）は高度管理を緩和しすぎて、盤面が高くなりすぎた
    - HIGHフェーズでHIGH_LAYERが支配的（15ターン中8ターン）、盤面の上昇が早い
    - マージグレードによる高度管理調整は、マージの質（DIRECT/NEAR/FAR）を高度管理の指標として使用しているが、これは間違っている
    - v200のスコア1035はv128のスコア3689と比較して大幅に低い

    根本原因:
    - v200のdynamic_height_multiplierは、「マージグレードに応じて高度管理を調整する」というアイデアだが、
      マージグレードを高度管理の指標として使用しているのが間違っている
    - マージグレードは「マージの質」を表し、高度管理はフェーズで調整すべきである
    - FARマージやNEARマージで高度管理を緩和しすぎると、盤面が急速に上昇し、CRITICALフェーズへ早く到達

    解決策（振子パターン解消のブレイクスルー）:
    - dynamic_height_multiplierを完全削除：マージグレードによる高度管理調整という間違ったアプローチを完全に廃止
    - v128の一律ルールに完全回帰：height_penalty = landing_y * 50.0 * height_mult（マージグレードによる調整なし）
    - v128の成功構造を完全維持：MEDIUM height_mult=2.4, HIGH height_mult=1.8, TOWER閾値0.5, HIGH_TOWER 1.3倍
    - v42のマージボーナス（DIRECT=1200/NEAR=600/FAR=200）を維持：マージグレードはマージボーナスの指標としてのみ使用
    - v128のバランス補正強度（HIGH=40.0/MEDIUM=30.0/LOW=20.0）を維持
    - ドリフトペナルティ一律30.0を維持
    - nextNextが同じタイプなら中央寄せボーナス50.0を維持
    - v128のシンプル構造（約110行）に完全回帰：コード量削減（約120行→約110行）
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
        height_mult = 2.4  # v201: v128の2.4を維持
        merge_mult = 1.0
    elif max_y < 3.0:
        phase = "HIGH"
        height_mult = 1.8  # v201: v128の1.8を維持
        merge_mult = 1.0
    else:
        phase = "CRITICAL"
        height_mult = 1.0  # CRITICAL: height_multなし
        merge_mult = 0.6  # v201: v128の0.6を維持

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

        # === v201: v128一律ルールに完全回帰 ===

        # 1. マージグレードによるスコア（v128の値を維持）
        if merge_grade == "DIRECT":
            score += 1200.0 * merge_mult
            reasons.append("DIRECT_MERGE")
        elif merge_grade == "NEAR":
            score += 600.0 * merge_mult
            reasons.append("NEAR_MERGE")
        elif merge_grade == "FAR":
            score += 200.0 * merge_mult
            reasons.append("FAR_MERGE")
        # v201: マージグレードによる高度管理調整は完全削除

        # 2. 高度によるペナルティ（v201: 一律ルール、dynamic_height_multiplier削除）
        height_penalty = landing_y * 50.0 * height_mult

        # HIGH_TOWERペナルティ（v201: v128の設定を維持）
        if phase == "HIGH" and landing_y > 0.5:  # v201: v128の閾値0.5を維持
            height_penalty *= 1.3  # v201: v128の1.3倍を維持
            reasons.append("HIGH_TOWER")
        elif phase == "MEDIUM" and landing_y > 0.5:  # v201: v128の閾値0.5を維持
            height_penalty *= 1.5  # v201: v42の1.5倍を維持
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
            balance_strength = 40.0  # v201: v128の40.0を維持
        elif phase == "MEDIUM":
            balance_strength = 30.0  # v201: v128の30.0を維持

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
