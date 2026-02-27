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
# [BEST:2346] v84: HIGHフェーズマージ優先・構造改善版 - v83の失敗（スコア1065、HIGHフェーズマージ率低）を受けて、振り子パターン完全回避で根本的な構造改善を実施。chain reaction緩和は完全廃止（v82の失敗から学ぶ）。代わりにHIGHフェーズでのマージ確保を優先：（1）merge_gradeボーナス強化（DIRECT=1500/NEAR=800/FAR=300でマージの質を重視）、（2）HIGHフェーズ高度管理緩和（height_mult=2.2に減、HIGH_TOWERペナルティ1.3倍に減）、（3）マージなし位置にNO_MERGEペナルティ（-150）、（4）max_yに応じた動的調整（盤面が高いほどマージ優先、低いほど高度管理優先）。v42のシンプル構造を維持しつつ、HIGHフェーズでのマージ機会を構造的に確保。コード量増加なし（約110行）。
# v93-v96: 振り子パターン（一律緩和→reactive_pairs活用→NO_MERGEペナルティ廃止→NO_MERGEペナルティ復活）- v93: height_multiplier 50.0→35.0、v94: 35.0→25.0、v95: reactive_pairs>=4で15.0・NO_MERGEペナルティ廃止、v96: reactive_pairs>=2で25.0・NO_MERGEペナルティ-150復活。v96にはreactive_pairsがlist型の時のバグがありturn 54以降でエラー発生。
# v123-v125: MEDIUMフェーズheight_multの振り子パターン（v122:2.2→v123:2.4→v124:2.2→v125:1.8）
# v126-v128: NO_MERGEペナルティとHIGH_TOWER削除の振り子パターン - v126: NO_MERGE追加、v127: NO_MERGE削除、v128: 高度管理緩和
# v129-v137: HIGH_TOWERペナルティの振り子パターン（v134:削除→v136:1.2倍→v137:2.0倍）- 一律のHIGH_TOWERペナルティが「削除すると高度管理不十分」「再導入するとマージ機会損失」の振り子を繰り返している。
# [BEST:3689] v128: HIGHフェーズマージ優先版 - v127の失敗（スコア724、HIGHフェーズ10ターン中9ターンでマージ不可）を受けて、HIGHフェーズでのマージ機会損失を特定。履歴分析でv127の高度管理がHIGHフェーズで過剰に強化されていることが原因を特定（HIGHフェーズのdecision_reasonはHIGH_TOWERが1回だが、HIGH_LAYERが5回で高度管理が支配的）。（1）HIGHフェーズ高度管理大幅緩和：height_multをv42の2.6から1.8に大幅に引き下げ（v84の2.2よりも緩和し、マージ優先を徹底）。（2）マージボーナス強化：v42の強力な値（DIRECT=1200/NEAR=600/FAR=200）を維持し、高度管理緩和と組み合わせてマージをHIGHフェーズの主要目標にする。（3）HIGHフェーズHIGH_TOWERペナルティ緩和：v84の1.3倍を維持し、height_mult大幅緩和と相乗効果。（4）v42のシンプル構造を維持：NO_MERGEペナルティの「入れるか入れないか」の振り子を回避し、第三の選択肢（マージボーナス強化・高度管理大幅緩和）を採用。振り子パターン（NO_MERGEペナルティ、height_multiplier微調整）をHIGHフェーズでのマージ優先徹底で解消。コード量維持（約110行）。
# v166: 二重管理バランス調整版 - v165の失敗（スコア1473、HIGH_TOWER過剰発動・HIGHフェーズ到達早すぎ・マージ予測精度0%）を受けて、二重管理のバランスを調整するブレイクスルーを採用。履歴分析でv165の失敗原因を特定：（1）二重管理メカニズムの振り子：v162（TOWER弱化）→v163（TOWER強化）→v164（TOWER削除）→v165（TOWER復帰）で4回繰り返されている。（2）HIGH_TOWERペナルティ過剰発動：HIGHフェーズ33ターン中33ターンで発動（100%）、height_multiplier=2.6が強すぎる。（3）マージ予測精度0%：merge_available=trueの13ターン中score_delta>0が0ターン、一律構造での補償が必要。（4）HIGHフェーズ到達早すぎ（turn48）、2997点ゲーム（turn87）と比較して39ターン早い。（5）v42（成功：MEDIUM=2.4/HIGH=2.6/HIGH_TOWER=2.0倍）、v128（成功：MEDIUM=2.4/HIGH=1.8/HIGH_TOWER=1.3倍）、v164（失敗：TOWERなし/MEDIUM=2.2/HIGH=2.2）の中間値を採用。（6）v128のHIGH_TOWER=1.3倍は個別のゲームでの成功であり、一律適用は危険。v42の2.0倍とv128の1.3倍の中間値1.5倍を採用。（7）v128のHIGHフェーズheight_mult=1.8も個別のゲームでの成功であり、一律適用は危険。v42の2.6とv128の1.8の中間値2.2を採用。（8）MEDIUMフェーズはv42/v165の2.4を維持し、HIGH到達遅延戦略を継続。（9）ブレイクスルー：TOWERペナルティの強弱を振るのではなく、height_multiplierを中間値（2.2）に緩和し、TOWERペナルティも中間値（HIGH_TOWER=1.5倍）に緩和することで、二重管理のバランスを改善。振り子パターンを第三の選択肢（中間値調整）で解消。一律構造での高度管理を維持しつつ、HIGHフェーズでのマージ機会を確保。コード量維持（約60行）。失敗（スコア872）：履歴分析でv166の失敗原因を特定：（1）HIGHフェーズ到達が早すぎる（turn22）、v128はturn31。（2）HIGH_TOWERペナルティ過剰発動：2回中2回発動（100%）、v166も同様の過剰発動。（3）マージ予測精度0%：merge_available=trueの4ターン中、実際にスコアが伸びたのは0ターン。（4）HIGHフェーズでのマージ機会が不十分：v166のHIGHフェーズ33ターン中、マージ予測はあったが実際のスコア獲得なし。（5）v166のHIGHフェーズheight_mult=2.2はまだ強すぎる可能性がある。v128の成功設定（HIGH_TOWER=1.3倍、height_mult=1.8）は個別のゲームでの成功であり、一律適用は危険。（6）TOWERペナルティとheight_multiplierの二重管理が振り子の根本原因。
# v168: TOWERペナルティ削除・マージボーナス強化版 - v166の失敗（スコア872、HIGH_TOWER過剰発動・マージ予測精度0%）を受けて、TOWERペナルティを完全削除しマージボーナスを強化するブレイクスルーを採用。履歴分析でv166の失敗原因を特定：（1）振り子パターン再発：HIGH_TOWERペナルティ（v128:1.3倍→v165:2.0倍→v166:1.5倍→v167:1.5倍）、HIGHフェーズheight_mult（v42:2.6→v128:1.8→v164:2.2→v165:2.6→v166:2.2）。（2）TOWERペナルティの存在自体が振り子の根本原因：v164（TOWER削除）→v165（TOWER復帰）→v166（TOWER緩和）→v167（TOWER緩和維持）で繰り返されている。（3）v128（3689点）の成功の本質は、HIGHフェーズheight_mult=1.8による高度管理緩和であり、HIGH_TOWERペナルティ=1.3倍は補助的要素。TOWERペナルティ自体がスコア向上に決定的に寄与したわけではない。（4）v164の失敗はTOWERペナルティ削除後のheight_mult=2.2が強すぎたため。v128のheight_mult=1.8を採用し、マージボーナスを強化してマージ機会を確保。（5）ブレイクスルー：TOWERペナルティを完全削除し、v128のHIGHフェーズheight_mult=1.8を採用。v84のマージボーナス強化（DIRECT=1500/NEAR=800/FAR=300）を組み合わせ、HIGHフェーズでのマージ機会を構造的に確保。一律構造での高度管理を維持し、予測精度の低さを補償。振り子パターンを第三の選択肢（TOWER削除・height_mult緩和・マージ強化）で解消。コード量削減（約60行→約50行）。失敗（スコア2109）：履歴分析でv168の失敗原因を特定：（1）マージ予測精度が低い：merge_available=trueのターンで実際にスコアが伸びたターンが限定的。（2）TOWER削除の副作用：高度管理が緩和されすぎ、マージ機会を優先しきれない。（3）マージボーナス強化（v84の値）の個別性：一律適用には危険。（4）HIGHフェーズ到達が早い（turn22付近でHIGHへ）。（5）decision_reason分布：HIGH_LAYERが支配的、マージが優先されていない。
# v169: TOWER_MERGE機能導入・HIGHフェーズ高度管理強化版 - v168の失敗（スコア2109、マージ機会損失）を受けて、振り子パターンを根本的に解消するブレイクスルーを採用。履歴分析でv168の失敗原因を特定：（1）TOWERペナルティの「強化/削除」振り子がv128-v168で6回繰り返されている。（2）v128の成功（3689点）は個別のゲームでの成功であり、一律適用は危険。（3）v42の成功（2335点）は一律構造の頑健性による。（4）マージ予測精度が低い：merge_available=trueでもscore_delta=0。（5）TOWER削除は高度管理緩和しすぎ、マージ機会を優先しきれない。（6）ブレイクスルー：TOWERペナルティの強弱調整ではなく、**マージ時の高度管理機能（TOWER_MERGE）**を導入。TOWERペナルティは維持しつつ、マージしたピースの着地位置が高い場合に追加ペナルティを課すことで、マージと高度管理の両立を実現。（7）HIGHフェーズheight_multをv42とv128の中間に設定：v42の2.6とv128の1.8の中間値2.4を採用し、高度管理を強化しつつマージ機会を確保。（8）マージボーナスはv42の値を維持：一律構造で頑健性を確保。（9）一律構造の維持：予測精度の低さを補償。（10）振り子パターン根本解消：TOWERペナルティの強弱調整ではなく、マージ時の高度管理機能でマージと高度管理の両立を図る。コード量微増（約60行）。


def decide(game_state: dict, analysis: dict) -> dict:
    """TOWER_MERGE機能導入・HIGHフェーズ高度管理強化版

    v168の失敗（スコア2109、マージ機会損失）を受けて、
    振り子パターンを根本的に解消するブレイクスルーを採用。

    TOWERペナルティの「強化/削除」振り子ではなく、
    マージ時の高度管理機能（TOWER_MERGE）を導入。
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
        height_mult = 2.4  # v169: v42の2.4を維持、HIGHフェーズ到達遅延戦略
        merge_mult = 1.0
    elif max_y < 3.0:
        phase = "HIGH"
        height_mult = 2.4  # v169: v42の2.6とv128の1.8の中間値、高度管理強化
        merge_mult = 1.0
    else:
        phase = "CRITICAL"
        height_mult = 1.0  # CRITICAL: height_multなし
        merge_mult = 0.6  # v169: v42の0.6を維持

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

        # === v169: TOWER_MERGE機能導入・HIGHフェーズ高度管理強化 ===

        # 1. マージグレードによるスコア（v169: v42の値を維持、一律構造で頑健性を確保）
        if merge_grade == "DIRECT":
            score += 1200.0 * merge_mult  # v169: v42の1200を維持
            reasons.append("DIRECT_MERGE")
        elif merge_grade == "NEAR":
            score += 600.0 * merge_mult  # v169: v42の600を維持
            reasons.append("NEAR_MERGE")
        elif merge_grade == "FAR":
            score += 200.0 * merge_mult  # v169: v42の200を維持
            reasons.append("FAR_MERGE")

        # 2. 高度によるペナルティ（v169: v42の構造を維持）
        height_penalty = landing_y * 50.0 * height_mult

        # TOWERペナルティ（v169: v42とv128の中間値で調整）
        if phase == "HIGH" and landing_y > 0.5:
            height_penalty *= 1.6  # v169: v42の2.0倍とv128の1.3倍の中間値
            reasons.append("HIGH_TOWER")
        elif phase == "MEDIUM" and landing_y > 0.5:
            height_penalty *= 1.5  # v169: v42の1.5倍を維持
            reasons.append("MEDIUM_TOWER")
        elif landing_y > 0.0:
            reasons.append("HIGH_LAYER")

        score -= height_penalty

        # 3. TOWER_MERGEペナルティ（v169: 新機能、マージ時の高度管理）
        # マージしたピースの着地位置が高い場合に追加ペナルティ
        if merge_grade in ["DIRECT", "NEAR", "FAR"] and landing_y > 0.0:
            # マージした場合、着地位置に応じてペナルティ
            # 着地が高いほど大きいペナルティ（高さ*100）
            tower_merge_penalty = landing_y * 100.0
            score -= tower_merge_penalty
            reasons.append("TOWER_MERGE")

        # 4. ドリフトによるペナルティ（v169: v42の一律30.0を維持）
        drift_penalty = (abs(drift_x) + drift_unc) * 30.0
        score -= drift_penalty

        # 5. 左右バランス補正（v169: v42の設定を維持）
        balance_strength = 20.0
        if phase == "HIGH":
            balance_strength = 40.0  # v169: v42の40.0を維持
        elif phase == "MEDIUM":
            balance_strength = 30.0  # v169: v42の30.0を維持

        left_count = sum(1 for p in pieces if p["x"] < 0)
        right_count = len(pieces) - left_count
        balance_bias = (right_count - left_count) / (len(pieces) if pieces else 1)

        balance_penalty = x * balance_bias * balance_strength
        score -= abs(balance_penalty)

        # 6. nextNextが同じタイプなら中央寄せボーナス（v169: v42の一律50.0を維持）
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
