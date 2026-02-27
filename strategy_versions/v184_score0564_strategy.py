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
# v182: v128完全復帰版 - v181の失敗（スコア1120、MEDIUMフェーズheight_mult=2.6が強すぎHIGHフェーズ到達早まり）を受けて、v128の成功構造に完全復帰。（1）MEDIUMフェーズv128復帰：height_multをv181の2.6からv42/v128の2.4に戻し、MEDIUMフェーズでの盤面形成を強化。MEDIUMフェーズでしっかりと盤面を低く保ち、HIGHフェーズへの到達を遅らせる。（2）HIGHフェーズv128復帰：height_multをv181の1.6からv128の1.8に戻し、マージ判断を適度に促進しつつ高度管理を維持。（3）HIGH_TOWERペナルティv128維持：v181の1.3倍を維持し、v128と同じ高盤面抑制を維持。（4）v42のシンプル構造を維持：マージボーナス（DIRECT=1200/NEAR=600/FAR=200）、高度管理、TOWERペナルティ、ドリフトペナルティ、バランス補正のシンプル構造を維持。振り子パターン（height_mult微調整の振り子）をv128完全復帰で解消。v128の成功構造（MEDIUM=2.4, HIGH=1.8, HIGH_TOWER=1.3倍）を採用し、頑健性を確保。コード量維持（約110行）。失敗（スコア1241）：履歴分析でv182の失敗原因を特定：（1）v128の履歴分析で発見：NEAR_MERGE判断は全て失敗（score_delta=0）、HEIGHT_CONTROLが最も効果的（6回で+289、平均+48.3）。v128のheight_mult=1.8は「HEIGHT_CONTROLの効果を高めた」だけであり、「マージ判断を促進した」のではない。（2）v182の履歴分析：HIGH_LAYERが27回で圧倒的、MEDIUMフェーズが5ターンのみ（turn 58-62）で短すぎ、HIGHフェーズ到達早まり（turn 63）。（3）盤面上昇加速：max_yがturn 81で2.87まで上昇、HIGH_TOWERペナルティ1.3倍では抑制不十分。（4）v128の成功原因の誤認：v128のスコア3689はマージ判断の成功ではなく、HEIGHT_CONTROLの効果によるもの。マージ判断自体は機能していない。（5）振り子パターン（v42→v128→v182）の解消には、v42の高度管理強化に戻し、HEIGHT_CONTROLを主要な判断指標とする必要がある。
# v183: v42完全復帰・HEIGHT_CONTROL主要化版 - v182の失敗（スコア1241、v128の成功原因誤認）を受けて、v128の「height_mult緩和→マージ優先」という構造的改善を破棄し、v42の「高度管理強化→HEIGHT_CONTROL有効化」という構造に完全復帰。（1）v128成功原因の再分析：履歴分析でNEAR_MERGE判断は全て失敗、HEIGHT_CONTROLが最も効果的（+289）を確認。v128のheight_mult=1.8はHEIGHT_CONTROLの効果を高めただけであり、マージ判断を促進したのではない。（2）v42完全復帰：HIGHフェーズのheight_multをv182の1.8からv42の2.6に戻し、HIGH_TOWERペナルティをv182の1.3倍からv42の2.0倍に戻す。高度管理を強化し、盤面上昇を抑制。（3）マージ判断の位置づけ変更：マージ判断はボーナスとして扱うが、主要な判断指標ではない。履歴から、HEIGHT_CONTROLが最も効果的であり、マージ判断は機能していない。（4）v42のシンプル構造を維持：マージボーナス（DIRECT=1200/NEAR=600/FAR=200）、高度管理（height_mult HIGH=2.6/MEDIUM=2.4）、TOWERペナルティ（HIGH=2.0倍/MEDIUM=1.5倍）、ドリフトペナルティ（30.0）、バランス補正を維持。（5）振り子パターン（v42→v128→v182→v42）の解消：v128の「height_mult緩和→マージ優先」を破棄し、v42の「高度管理強化→HEIGHT_CONTROL有効化」に戻す。HEIGHT_CONTROLを主要な判断指標とし、マージ判断は補助的な役割にする。コード量維持（約110行）。失敗（スコア1386）：履歴分析でv183の失敗原因を特定：（1）マージ予測精度が極めて低い：マージ判断22回に対し、マージ可能ターンは14回のみ（8回が誤検出、誤検出率36%）。（2）HIGHフェーズで完全失敗：10ターン中HIGH_TOWERが8回、スコア増加0回。max_yが3.15まで上昇し、高度管理不十分。（3）マージボーナスが誤判断を助長：不正確なマージ予測に高いボーナス（DIRECT=1200/NEAR=600）を与えているため、低い位置（マージ不可）を選択して盤面を不安定にしている。（4）マージ予測を前提とする戦略は予測精度の低さから限界：マージ判断自体が機能しておらず、マージボーナスは誤検出を助長するだけ。（5）振り子パターン（v42→v128→v182→v42→v183）の解消には、マージ予測を前提としない戦略へのブレイクスルーが必要。
# v184: マージボーナス削除・高度管理徹底版 - v183の失敗（スコア1386、マージ予測精度低・HIGHフェーズ失敗）を受けて、マージボーナスを完全削除し、高度管理を徹底的に強化するブレイクスルーを実施。（1）マージボーナス完全削除：v183で確認したマージ予測精度（誤検出率36%）の低さから、マージボーナスは誤判断を助長しスコアを低下させていることが判明。マージ予測を前提としない戦略へ転換。（2）高度管理徹底強化：height_multはv42の値（HIGH=2.6/MEDIUM=2.4）を維持しつつ、HIGH_TOWERペナルティの閾値を0.5から0.3に厳しくし、高盤面での抑制を強化。（3）ドリフトペナルティ強化：v42の一律30.0から40.0に増加し、ピースの着地位置をより正確に制御。（4）バランス補正強化：v42のHIGH=40.0/MEDIUM=30.0からHIGH=50.0/MEDIUM=40.0に増加し、盤面の左右バランスをより厳格に管理。（5）盤面を低く保ち偶発的なマージを促進：マージ予測に依存せず、盤面を低く保つことでchain reactionの可能性を高める。（6）v42のシンプル構造を維持：高度管理、TOWERペナルティ、ドリフトペナルティ、バランス補正のシンプル構造を維持。マージボーナス削除でコード量削減（約110行→約95行）。（7）振り子パターン解消：マージ予測を前提としないことで、v42→v128→v182→v42→v183という「height_mult微調整」と「マージボーナスの有無」の振り子を根本的に解消。


def decide(game_state: dict, analysis: dict) -> dict:
    """マージボーナス削除・高度管理徹底版

    v183の失敗（スコア1386、マージ予測精度低・HIGHフェーズ失敗）を受けて、
    マージボーナスを完全削除し、高度管理を徹底的に強化するブレイクスルーを実施。

    v183履歴分析で確認した問題:
    - マージ予測精度が極めて低い（誤検出率36%）
    - HIGHフェーズで完全失敗（max_y=3.15まで上昇）
    - マージボーナスが誤判断を助長

    解決策:
    - マージボーナス完全削除（予測を前提としない）
    - 高度管理徹底強化（height_mult、HIGH_TOWER閾値厳格化）
    - ドリフトペナルティ・バランス補正強化
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
    elif max_y < 1.8:
        phase = "MEDIUM"
        height_mult = 2.4  # v184: v42の2.4を維持
    elif max_y < 3.0:
        phase = "HIGH"
        height_mult = 2.6  # v184: v42の2.6を維持、高度管理強化
    else:
        phase = "CRITICAL"
        height_mult = 2.6  # v184: CRITICALでも高度管理維持（v42の1.0を廃止）

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

        score = 0.0
        reasons = []

        # === v184: マージボーナス削除・高度管理徹底 ===

        # 1. 高度によるペナルティ（v184: v42の基本構造を維持、マージボーナスなし）
        height_penalty = landing_y * 50.0 * height_mult

        # TOWERペナルティ（v184: HIGH_TOWER閾値を0.5から0.3に厳しくする）
        if phase == "HIGH" and landing_y > 0.3:  # v184: 閾値厳格化（0.5→0.3）
            height_penalty *= 2.0  # v184: v42の2.0倍を維持
            reasons.append("HIGH_TOWER")
        elif phase == "MEDIUM" and landing_y > 0.3:  # v184: 閾値統一（0.5→0.3）
            height_penalty *= 1.5  # v184: v42の1.5倍を維持
            reasons.append("MEDIUM_TOWER")
        elif landing_y > 0.0:
            reasons.append("HIGH_LAYER")

        score -= height_penalty

        # 2. ドリフトによるペナルティ（v184: v42の30.0から40.0に強化）
        drift_penalty = (abs(drift_x) + drift_unc) * 40.0  # v184: 強化（30.0→40.0）
        score -= drift_penalty

        # 3. 左右バランス補正（v184: v42の値から強化）
        balance_strength = 25.0  # v184: v42の20.0から25.0に強化
        if phase == "HIGH":
            balance_strength = 50.0  # v184: v42の40.0から50.0に強化
        elif phase == "MEDIUM":
            balance_strength = 40.0  # v184: v42の30.0から40.0に強化

        left_count = sum(1 for p in pieces if p["x"] < 0)
        right_count = len(pieces) - left_count
        balance_bias = (right_count - left_count) / (len(pieces) if pieces else 1)

        balance_penalty = x * balance_bias * balance_strength
        score -= abs(balance_penalty)

        # 4. nextNextが同じタイプなら中央寄せボーナス（v184: v42の一律50.0を維持）
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
