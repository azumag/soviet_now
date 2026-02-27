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
# v185: 盤面スコア導入・密度重視版 - v184の失敗（スコア564、偶発的なマージ促進インセンティブ消失・盤面密度無視）を受けて、マージ予測を前提としない盤面スコアを導入するブレイクスルーを実施。（1）盤面スコア導入：着地位置周辺のピース密度を評価し、密な配置をボーナス。マージ予測に依存せず、偶発的なマージの可能性を高める。density_score = sum(exp(-distance)) for each piece within 1.0 distance。（2）盤面密度ボーナス強化：密度ボーナスを150.0とし、密度重視を明確化。MEDIUMフェーズで盤面を低く保ちつつ密な配置、HIGHフェーズで偶発的なマージを促進。（3）v184の構造を維持：マージボーナス削除、高度管理強化（height_mult HIGH=2.6/MEDIUM=2.4、HIGH_TOWER閾値0.3）、ドリフトペナルティ強化（40.0）を維持。（4）バランス補正改善：重心ベースの計算に変更（より正確なバランス評価）。（5）振り子パターン解消：盤面スコア導入により、マージ予測を前提としない戦略へブレイクスルー。偶発的なマージを促進するインセンティブを復活。コード量微増（約95行→約100行）。失敗（スコア707）：履歴分析でv185の失敗原因を特定：（1）density_bonusが着地位置の高さを考慮していない：density_bonusはdistanceのみで計算しているため、高層（y>2.0）でピースを固めても大きなボーナスになり、盤面上昇を加速させる悪循環。（2）max_yが3.71まで上昇：turn 67でmax_y=3.71に達し、v184(2.89)よりも盤面が高い。HIGHフェーズでMEDIUM_TOWERが11回頻発し、高層での配置抑制が機能していない。（3）densityボーナスが高層配置を助長：v185のdensity_bonus = 150.0 * (1.0 - distance)は着地位置yを考慮していないため、高層での密な配置を過度に評価している。（4）v128の成功要素とv184の高度管理強化の統合が必要：v128のheight_mult=1.8はHIGHフェーズでマージ機会確保に有効だが、マージ予測精度が低いため、densityボーナスのようなマージ予測不要のインセンティブと組み合わせる必要がある。（5）振り子パターン解消には、密度ボーナスの高さ依存化とheight_multの適切化が必要。
# v186: 高さ依存密度ボーナス導入版 - v185の失敗（スコア707、density_bonusが高層配置を助長）を受けて、密度ボーナスの高さ依存化とheight_multの適切化を実施。（1）密度ボーナスの高さ依存化：density_bonusに高度減衰係数を導入し、高層ほど密度ボーナスを抑制。height_factor = max(0, 1.0 - landing_y / 3.0)で、y=0で1.0、y=1.5で0.5、y=3.0で0.0。（2）height_multの適切化：v128(1.8)とv184(2.6)の中間値2.2を採用し、v128のマージ機会確保とv184の高度管理強化のバランスをとる。（3）HIGH_TOWERペナルティ閾値適切化：v184の0.3から0.5に緩和し、v128の成功要素（1.3倍）と組み合わせることで、適切な高層抑制を実現。（4）マージボーナス削除維持：v184/v185の方針を継続（予測精度低のため）。（5）重心ベースバランス補正維持：v185の改善点を維持。（6）振り子パターン解消：密度ボーナスの高さ依存化により、v185の「高層配置助長」とv184の「高度管理強化」の矛盾を解消。height_multの適切化により、v128(1.8)とv184(2.6)の振り子を回避。コード量微増（約100行→約105行）。失敗（スコア755）：履歴分析でv186の失敗原因を特定：（1）densityボーナスの副作用で高層配置が助長：HIGH_LAYERとMEDIUM_TOWERが36.1%と支配的。densityボーナスは着地位置周辺の密度をボーナスとするが、高層での密な配置を過度に評価し、HIGH_LAYER/MEDIUM_TOWER判断を誘発している。（2）densityボーナスが盤面を複雑化：densityボーナスは着地位置周辺のピースを全て走査し、距離計算を行うため計算コストが高い。また、高さ依存係数の導入でロジックが複雑化。（3）振り子パターン（v184-v186）が継続：マージボーナス削除とdensityボーナス追加/高さ依存化で、堂々巡りを繰り返している。「削除すると偶発的なマージ促進インセンティブ消失」「追加すると高層配置助長」という矛盾を解消できない。（4）v128の成功構造の再確認：v128のスコア3689は「マージボーナス（補助的）+ height_mult=1.8（HIGHフェーズ緩和）」のシンプルな構造で達成。マージボーナスは主要な判断指標ではなく、補助的な役割で十分。height_mult=1.8は盤面を低く保ちつつマージ機会を確保。（5）振り子パターン解消には、v128の成功構造への完全復帰が必要。
# v187: v128完全復帰・density削除版 - v186の失敗（スコア755、densityボーナス副作用・振り子パターン）を受けて、v128の成功構造に完全復帰するブレイクスルーを実施。（1）densityボーナス完全削除：v184-v186の振り子パターンを解消。densityボーナスは高層配置を助長する副作用があり、HIGH_LAYER/MEDIUM_TOWERが36.1%と支配的。v128のシンプルな構造に復帰。（2）マージボーナス復帰：v128の成功構造を復帰。マージボーナス（DIRECT=1200/NEAR=600/FAR=200）は主要な判断指標ではなく、補助的な役割。マージボーナスは密な配置を促進し、偶発的なマージを確保するインセンティブとして機能。（3）HIGHフェーズheight_mult=1.8に緩和：v128の成功構造を復帰。盤面を低く保ちつつマージ機会を確保。v186のheight_mult=2.2と比較し、v128の1.8を維持することで、HIGHフェーズでのマージ機会確保と高度管理のバランスをとる。（4）HIGH_TOWERペナルティ閾値0.5に緩和：v128の成功構造を復帰。v186の0.5を維持し、v128の1.3倍を採用。（5）ドリフトペナルティ30.0に復帰：v128の成功構造を復帰。v186の40.0から30.0に戻す。（6）バランス補正を左右カウントベースに戻す：v128のシンプルな構造を復帰。重心ベースは理論的に正確だが、v128のスコア3689を達成したカウントベースの方が実績がある。（7）v42のシンプル構造を維持：マージボーナス（DIRECT=1200/NEAR=600/FAR=200）、高度管理（height_mult MEDIUM=2.4/HIGH=1.8）、TOWERペナルティ（HIGH=1.3倍/MEDIUM=1.5倍、閾値0.5）、ドリフトペナルティ（30.0）、バランス補正（HIGH=40.0/MEDIUM=30.0/LOW=20.0）のシンプル構造を維持。振り子パターン（v184-v186）をv128完全復帰で解消。コード量削減（約105行→約110行）。


def decide(game_state: dict, analysis: dict) -> dict:
    """v128完全復帰・density削除版

    v186の失敗（スコア755、densityボーナス副作用・振り子パターン）を受けて、
    v128の成功構造に完全復帰するブレイクスルーを実施。

    v186履歴分析で確認した問題:
    - densityボーナスの副作用で高層配置が助長（HIGH_LAYER/MEDIUM_TOWERが36.1%）
    - densityボーナスが盤面を複雑化
    - 振り子パターン（v184-v186）が継続

    解決策:
    - densityボーナス完全削除
    - マージボーナス復帰（補助的役割）
    - HIGHフェーズheight_mult=1.8に緩和
    - v128のシンプル構造に完全復帰
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

    # フェーズ判定（v128: v42の閾値0.8/1.8/3.0を維持）
    if max_y < 0.8:
        phase = "LOW"
        height_mult = 1.0
        merge_mult = 1.2
    elif max_y < 1.8:
        phase = "MEDIUM"
        height_mult = 2.4  # v187: v128の2.4を維持
        merge_mult = 1.0
    elif max_y < 3.0:
        phase = "HIGH"
        height_mult = 1.8  # v187: v128の1.8を維持、HIGHフェーズ高度管理大幅緩和
        merge_mult = 1.0
    else:
        phase = "CRITICAL"
        height_mult = 1.0  # CRITICAL: height_multなし
        merge_mult = 0.6  # v187: v42の0.6を維持

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

        # === v187: v128完全復帰・density削除 ===

        # 1. マージグレードによるスコア（v187: v128の値を維持）
        if merge_grade == "DIRECT":
            score += 1200.0 * merge_mult
            reasons.append("DIRECT_MERGE")
        elif merge_grade == "NEAR":
            score += 600.0 * merge_mult
            reasons.append("NEAR_MERGE")
        elif merge_grade == "FAR":
            score += 200.0 * merge_mult
            reasons.append("FAR_MERGE")
        # v187: マージボーナスは補助的役割として維持。主要な判断指標ではない。

        # 2. 高度によるペナルティ（v187: v128のHIGHフェーズheight_mult=1.8を維持）
        height_penalty = landing_y * 50.0 * height_mult

        # HIGH_TOWERペナルティ（v187: v128の設定を維持）
        if phase == "HIGH" and landing_y > 0.5:
            height_penalty *= 1.3  # v187: v128の1.3倍を維持
            reasons.append("HIGH_TOWER")
        elif phase == "MEDIUM" and landing_y > 0.5:
            height_penalty *= 1.5  # v187: v42の1.5倍を維持
            reasons.append("MEDIUM_TOWER")
        elif landing_y > 0.0:
            reasons.append("HIGH_LAYER")

        score -= height_penalty

        # 3. ドリフトによるペナルティ（v187: v128の30.0を維持）
        drift_penalty = (abs(drift_x) + drift_unc) * 30.0
        score -= drift_penalty

        # 4. 左右バランス補正（v187: v128の左右カウントベースを維持）
        balance_strength = 20.0
        if phase == "HIGH":
            balance_strength = 40.0  # v187: v128の40.0を維持
        elif phase == "MEDIUM":
            balance_strength = 30.0  # v187: v128の30.0を維持

        left_count = sum(1 for p in pieces if p["x"] < 0)
        right_count = len(pieces) - left_count
        balance_bias = (right_count - left_count) / (len(pieces) if pieces else 1)

        balance_penalty = x * balance_bias * balance_strength
        score -= abs(balance_penalty)

        # 5. nextNextが同じタイプなら中央寄せボーナス（v187: v128の一律50.0を維持）
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
