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
# v144: マージグレード連動型高度管理緩和版 - v143の失敗（スコア1053、HIGHフェーズマージ率0%）を受けて、一律の高度管理を廃止し、マージグレードに応じた高度管理緩和を導入。履歴分析でHIGHフェーズ6ターン全てでmerge_available=False、decision_reason=HIGH_LAYERで高度管理が支配的であることを特定。v128の成功（3689点）は盤面構成に依存し、v142が同じ設定で失敗（1004点）したことから、一律の設定では対応できないことを学ぶ。（1）マージグレード連動型高度管理：merge_gradeに応じてheight_multiplierを動的に調整。DIRECT/NEARマージ可能な位置ではheight_multiplierを0.5倍に大幅緩和し、マージ機会を確保。FAR/NOマージの位置ではheight_multiplierを1.5倍に強化し、高度管理を徹底。（2）HIGHフェーズ設定緩和：height_multをv143の1.6から1.2に緩和し、HIGH_TOWERペナルティをv128の1.3倍で復帰。v142の失敗から学び、HIGH_TOWER閾値を0.5に戻す。ただし、マージグレード連動型高度管理と組み合わせることで、HIGH_TOWERペナルティの影響を緩和する。（3）v42の頑健な基本構造維持：DIRECT=1200/NEAR=600/FAR=200、drift_penalty=30、balance補正、NEXT_SAMEボーナス=50、MEDIUMフェーズheight_mult=2.4、MEDIUM_TOWER=1.5倍はv42の設定を維持し、HIGH到達までの安定性を確保。MEDIUMフェーズでは一律の高度管理を維持し、振り子パターンを回避。（4）振り子パターン解消：v142-v143のHIGH_TOWER削除↔復帰の振れ子を、「一律設定」から「マージグレード連動型」に変更することで解消。HIGHフェーズでは高度管理の強度をマージの質に応じて動的に調整し、一律のHIGH_TOWERペナルティの問題を回避。（5）ブレイクスルー：v128の成功設定（height_mult=1.8, HIGH_TOWER=1.3倍）は盤面構成に依存し、v142で失敗したことから、一律の高度管理では対応できない。マージグレード連動型高度管理は、マージ可能な位置では高度管理を緩和し、マージ不可能な位置では高度管理を強化することで、盤面構成に依存しない頑健なアプローチを実現。コード量微増（約75行）。失敗（スコア787）：履歴分析でHIGHフェーズ（turns 42-68, max_y 1.62-2.89）マージ可能ターン4回（turns 42, 64, 67, 68）に対し実際のマージ成功は1回（Turn 68）のみ、マージ成功率25%。HIGH_TOWERペナルティが支配的でマージ機会を損失（Turn 42, 64, 67でmerge_available=trueだが高度管理を優先しマージ失敗）。マージグレード連動型高度管理の失敗：マージ予測の精度が低く（v142履歴でMEDIUMフェーズNEAR_MERGE判断ターンの77%で実際にマージせず）、誤検出で高度管理緩和を誤トリガーし、低い位置を選んでマージ機会を損失。予測ベースの高度管理緩和は本質的に危険。
# v145: 予測ベース削除・一律高度管理復帰版 - v144の失敗（スコア787、HIGHフェーズマージ成功率25%、マージ予測精度低・予測ベース緩和が危険）を受けて、マージグレード連動型高度管理を完全廃止し、一律の高度管理に復帰するブレイクスルーを採用。履歴分析でv144のマージグレード連動型高度管理が失敗した根本原因を特定：マージ予測の精度が低く、誤検出で高度管理緩和を誤トリガーし、マージ機会を損失。予測を前提とする戦略は本質的に危険。（1）マージグレード連動型高度管理の完全廃止：v144のmerge_gradeに応じた動的高度管理（DIRECT/NEAR:0.5倍、FAR:1.0倍、NO:1.5倍）を完全削除。予測ベースの緩和は精度不足でマージ機会を損失するため、一律の高度管理に復帰し、予測を前提としないシンプルな構造を採用。（2）HIGHフェーズ高度管理緩和：v144のheight_mult=1.2からv128の1.8に緩和。v143のheight_mult=1.6でHIGHフェーズ6ターン全てでmerge_available=Falseだったが、height_mult=1.8に緩和することでマージ機会を確保。v128の成功設定（3689点）のheight_mult=1.8を採用し、HIGHフェーズでのマージ優先を実現。（3）HIGH_TOWERペナルティの完全削除：v142-v144のHIGH_TOWER振れ子を解消。v143の失敗（HIGH_TOWER削除だがheight_mult=1.6で依然として高度管理過剰）から学び、height_mult=1.8に緩和した上でHIGH_TOWERペナルティを完全削除。一律の高度管理で頑健性を確保しつつ、HIGHフェーズでの高度管理を緩和しマージを優先。（4）v42の頑健な基本構造維持：DIRECT=1200/NEAR=600/FAR=200、drift_penalty=30、balance補正、NEXT_SAMEボーナス=50、MEDIUMフェーズheight_mult=2.4、MEDIUM_TOWER=1.5倍はv42の設定を維持し、HIGH到達までの安定性を確保。MEDIUMフェーズでは一律の高度管理を維持し、振れ子パターンを回避。（5）振れ子パターン解消：v142-v144の「HIGH_TOWER削除↔復帰↔予測ベース緩和」の振れ子を、一律の高度管理とHIGHフェーズ緩和で解消。予測を前提としない一律構造で頑健性を確保しつつ、HIGHフェーズでの高度管理を緩和しマージを優先。（6）ブレイクスルー：マージ予測を前提とするアプローチ（v144のマージグレード連動型高度管理）は本質的に危険。一律の高度管理でv42の頑健性とv128のマージ優先のバランスをとる。予測を前提としない一律構造で頑健性を確保し、HIGHフェーズでの高度管理を緩和しマージを優先するシンプルで頑健なアプローチを実現。コード量削減（約75行→約65行）。失敗（スコア2716）：履歴分析でマージ成功率0.0%（12回マージ可能ターンで0回成功）。予測精度が非常に低いことが根本原因：merge_available=trueとbest_merge_grade=DIRECT/NEARでも実際のマージは発生していない（score_delta=0）。予測ベースの戦略は本質的に危険。v128のheight_mult=1.8は依然として高すぎ、HIGHフェーズ（max_y 2.0-2.8範囲）でマージ可能なターンでもHEIGHT_CONTROLを選んでマージ機会を損失している。
# v146: マージ優先徹底・高度管理最適化版 - v145の失敗（スコア2716、マージ成功率0.0%、予測精度低・height_mult=1.8が依然として高すぎ）を受けて、マージボーナス強化・NO_MERGEペナルティ追加・高度管理緩和の3点セットでマージ優先を徹底。履歴分析でマージ成功率0.0%の根本原因を特定：予測精度が非常に低く、merge_available=trueとbest_merge_grade=DIRECT/NEARでも実際のマージは発生していない（score_delta=0）。予測ベースの戦略は本質的に危険。予測に依存せず、一律の強いマージボーナスでマージ機会を強制的に確保するブレイクスルーを採用。（1）マージボーナス強化：v84の成功戦略（DIRECT=1500/NEAR=800/FAR=300）を基準に、v42の安定性を考慮して調整。DIRECT=1400/NEAR=700/FAR=250でマージの質を重視。v42の1200/600/200より強く、v84の1500/800/300より安定。（2）NO_MERGEペナルティ追加：v84の成功戦略（-150）を基準に、さらに強い-200を導入。マージ機会のない位置に対して一律でペナルティを与え、マージ優先を徹底。（3）高度管理最適化：MEDIUMフェーズはv42の頑健な設定（height_mult=2.4、MEDIUM_TOWER=1.5倍）を維持。HIGHフェーズはv128のheight_mult=1.8より緩和し、v42の2.6より大幅に緩和してheight_mult=1.4に設定。HIGH_TOWERペナルティはv128の成功設定（1.3倍）を復帰し、マージ優先と高度管理のバランスをとる。（4）v42の頑健な基本構造維持：drift_penalty=30、balance補正、NEXT_SAMEボーナス=50、MEDIUMフェーズheight_mult=2.4、MEDIUM_TOWER=1.5倍はv42の設定を維持し、HIGH到達までの安定性を確保。（5）振れ子パターン解消：v142-v145の「HIGH_TOWER削除↔復帰↔height_mult緩和↔予測ベース緩和」の振れ子を、一律の強いマージボーナス+NO_MERGEペナルティ+高度管理緩和で解消。予測に依存せず、一律の強いインセンティブでマージ優先を徹底。（6）ブレイクスルー：予測精度が低い状況下では、予測に依存した高度管理緩和は本質的に危険。一律の強いマージボーナスとNO_MERGEペナルティで、予測に依存せずマージ優先を徹底し、高度管理を緩和することでマージ機会を強制的に確保。v42の頑健性とv84のマージ優先のバランスをとる。コード量微増（約70行）。


def decide(game_state: dict, analysis: dict) -> dict:
    """マージボーナス強化・NO_MERGEペナルティ追加・高度管理緩和でマージ優先を徹底。予測に依存せず一律の強いインセンティブでマージ機会を確保。"""

    results = analysis.get("results", [])

    if not results:
        return {"x": 0.0, "reason": "no analysis data"}

    best_x = 0.0
    best_score = -float("inf")
    best_reason = ""

    # 盤面情報
    pieces = game_state.get("pieces", [])
    max_y = max([p["y"] for p in pieces]) if pieces else -4.0

    # フェーズ判定（v42の閾値を維持）
    if max_y < 0.8:
        phase = "LOW"
        height_mult = 1.0
        merge_mult = 1.2
    elif max_y < 1.8:
        phase = "MEDIUM"
        height_mult = 2.4  # v42の2.4を維持（MEDIUMフェーズの安定性確保）
        merge_mult = 1.0
    elif max_y < 3.0:
        phase = "HIGH"
        height_mult = 1.4  # v146: v128の1.8より緩和、v42の2.6より大幅に緩和
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

        # === v146: マージ優先徹底・高度管理最適化 ===

        # 1. マージグレードによるスコア（v146: マージボーナス強化）
        if merge_grade == "DIRECT":
            score += 1400.0 * merge_mult  # v146: 1200→1400に強化
            reasons.append("DIRECT_MERGE")
        elif merge_grade == "NEAR":
            score += 700.0 * merge_mult  # v146: 600→700に強化
            reasons.append("NEAR_MERGE")
        elif merge_grade == "FAR":
            score += 250.0 * merge_mult  # v146: 200→250に強化
            reasons.append("FAR_MERGE")
        else:  # NO merge
            score -= 200.0  # v146: NO_MERGEペナルティ-200を導入
            reasons.append("NO_MERGE")

        # 2. 高度によるペナルティ（v146: 一律で計算）
        height_penalty = landing_y * 50.0 * height_mult

        # v146: MEDIUMフェーズではMEDIUM_TOWERペナルティを維持（v42の設定）
        # v146: HIGHフェーズではHIGH_TOWERペナルティをv128の1.3倍で復帰し、高度管理とマージ優先のバランスをとる
        if phase == "MEDIUM" and landing_y > 0.5:
            height_penalty *= 1.5  # v42の1.5倍を維持
            reasons.append("MEDIUM_TOWER")
        elif phase == "HIGH" and landing_y > 0.5:
            height_penalty *= 1.3  # v146: v128の成功設定（1.3倍）を復帰
            reasons.append("HIGH_TOWER")
        elif landing_y > 0.0:
            reasons.append("HIGH_LAYER")

        score -= height_penalty

        # 3. ドリフトによるペナルティ（v42の一律30.0を維持）
        drift_penalty = (abs(drift_x) + drift_unc) * 30.0
        score -= drift_penalty

        # 4. 左右バランス補正（v42の設定を維持）
        balance_strength = 20.0
        if phase == "HIGH":
            balance_strength = 40.0  # v42の40.0を維持
        elif phase == "MEDIUM":
            balance_strength = 30.0  # v42の30.0を維持

        left_count = sum(1 for p in pieces if p["x"] < 0)
        right_count = len(pieces) - left_count
        balance_bias = (right_count - left_count) / (len(pieces) if pieces else 1)

        balance_penalty = x * balance_bias * balance_strength
        score -= abs(balance_penalty)

        # 5. nextNextが同じタイプなら中央寄せボーナス（v42の一律50.0を維持）
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
