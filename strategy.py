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
# v138: 反応器状態連動型高度管理版 - v137の失敗（スコア743、HIGH/MEDIUMフェーズで高度管理が支配的）を受けて、HIGH_TOWERペナルティの一律適用を廃止し、反応器状態（reactive_pairs）に基づく動的高度管理を実施。履歴分析でHIGH/MEDIUMフェーズでreactive_pairsが高値（>=2）なのに、一律のHIGH_TOWERペナルティがマージを阻害していることを特定。（1）反応器準備状態判定：reactive_pairs >= 2でマージ準備状態と判定。（2）マージ準備状態時の高度緩和：reactive_pairs >= 2ならHIGH_TOWER/MEDIUM_TOWERペナルティを0.5倍に大幅緩和し、高い位置でもマージを狙う。（3）非準備状態時の高度強化：reactive_pairs < 2ならHIGH_TOWER/MEDIUM_TOWERペナルティを2.0倍（HIGH）/1.5倍（MEDIUM）に強化し、低い位置で着地させ反応器を準備させる。（4）v42の基本構造維持：DIRECT=1200/NEAR=600/FAR=200、drift_penalty=30、balance補正、NEXT_SAMEボーナス=50。反応器ボーナス（CLOSE_MERGE, NEAR_PAIRS, PIPELINE_OK, REACTIVE_PAIRS）は全て維持。一律削除vs一律強化の振り子パターンを、反応器状態連動による動的調整で第三の選択肢で解消。コード量微増（約90行→約100行）。失敗（スコア835）：履歴分析でHIGHフェーズマージ率12.5%、反応器準備状態マージ率25%と低い。v138の高度緩和（0.5倍）が不十分で、HIGHフェーズheight_mult=2.3が大きい。HIGHフェーズ8ターン全てで「HIGH_TOWER_RELAXED」が適用されているが、マージは1回のみ。
# v139: 反応器準備状態での高度管理完全無効化版 - v138の失敗（スコア835、高度緩和不十分）を受けて、反応器準備状態での高度管理を完全無効化し、真の第三の選択肢を実現。履歴分析でHIGHフェーズ8ターン全てで反応器準備状態（reactive_pairs >= 2）だが、マージ率は12.5%と低いことを特定。v138の高度緩和（0.5倍）が不十分で、HIGHフェーズheight_mult=2.3が大きいため、高度ペナルティ（1.0 * 50 * 2.3 * 0.5 = 57.5）が依然として大きい。（1）高度管理完全無効化：反応器準備状態では高度ペナルティを0.0倍に完全無効化し、マージを完全優先。v138の0.5倍緩和では不十分で、マージ機会が損失。（2）HIGHフェーズheight_mult緩和：2.3→2.0に緩和し、v128の1.8とv42の2.6の中間値を採用。高度管理緩和と相乗効果でマージ機会確保。（3）MEDIUMフェーズheight_mult緩和：2.4→2.2に緩和し、HIGH到達を容易にする。v42の2.4は強すぎ、v128の1.8は弱すぎ。（4）反応器ボーナス強化：50.0→60.0に強化し、マージ優先を徹底。（5）非準備状態時の高度管理強化維持：HIGH=2.0倍、MEDIUM=1.5倍で反応器準備を促進。（6）v42のシンプル構造維持：DIRECT=1200/NEAR=600/FAR=200、drift_penalty=30、balance補正、NEXT_SAMEボーナス=50。反応器ボーナス（CLOSE_MERGE, NEAR_PAIRS, PIPELINE_OK）は削除しシンプル化。（7）振り子パターン解消：v129-v137のHIGH_TOWER削除↔再導入の振り子を、反応器準備状態での完全無効化で解消。「一律削除vs一律強化」ではなく「状況に応じた動的調整」で真の第三の選択肢を実現。コード量微減（約100行→約90行）。失敗（スコア488）：履歴分析でHIGH/MEDIUMフェーズで「HIGH_TOWER_DISABLED」「MEDIUM_TOWER_DISABLED」が過剰に発動し、高度管理が全く機能していない。反応器準備状態判定（reactive_pairs >= 2）が緩すぎ、マージ可能でない「疑似準備状態」で高度管理を無効化し盤面が急上昇。MEDIUMフェーズ（turns 47-55）で9ターン中9ターンがMEDIUM_TOWER_DISABLED、max_yが1.99から3.91へ急上昇しCRITICALフェーズ到達。反応器状態連動型高度管理の根本的な欠陥：準備状態の判定自体が不正確で、それを高度管理のスイッチとして使用すると、不正確な判定がそのまま高度管理の欠陥になる。v42（2335点）と比較すると、v42は反応器ボーナスなし・一律HIGH_TOWERで高スコア達成。「反応器準備でマージ優先」という直感と、「準備状態の判定が不正確」という事実が矛盾。
# v140: v42完全復帰・反応器状態連動削除版 - v139の失敗（スコア488、反応器準備状態判定緩すぎ・高度管理全く機能せず）を受けて、反応器状態連動型高度管理を完全に廃止し、v42のシンプルな一律構造に完全復帰。履歴分析でHIGH/MEDIUMフェーズで「HIGH_TOWER_DISABLED」「MEDIUM_TOWER_DISABLED」が過剰に発動し、reactive_pairs >= 2の準備状態判定が緩すぎることを特定。MEDIUMフェーズ9ターン全てでMEDIUM_TOWER_DISABLED、max_yが1.99から3.91へ急上昇。（1）反応器状態連動型高度管理完全廃止：v138-v139の反応器準備状態判定（reactive_pairs >= 2）と、それに基づく高度緩和/強化ロジックを全て削除。準備状態の判定自体が不正確で、これを高度管理のスイッチに使用すると欠陥が増幅する。（2）v42の一律HIGH_TOWER(2.0倍)/MEDIUM_TOWER(1.5倍)を復帰：反応器状態にかかわらず一律で高度管理を適用し、頑健性を確保。（3）反応器ボーナス完全削除：v42は反応器ボーナス（CLOSE_MERGE, NEAR_PAIRS, PIPELINE_OK, REACTIVE_PAIRS）なしで2335点達成。これらのボーナスはマージ予測の精度に依存するが、履歴では精度が低く、誤検出で低い位置を選びマージ機会を損なっている。（4）v42のシンプル構造完全復帰：DIRECT=1200/NEAR=600/FAR=200、height_penalty=50*height_mult、drift_penalty=30、balance補正、NEXT_SAMEボーナス=50、HIGH_TOWER(2.0倍)/MEDIUM_TOWER(1.5倍)。v42(2335点)はv139(488点)の4.8倍、明白に優れている。振り子パターン（HIGH_TOWER削除↔再導入、高度管理微調整の振子）をv42完全復帰で断ち切る。「反応器状態連動で第三の選択肢」というアプローチ自体が誤りで、v42の「一律で頑健」こそが正解。コード量削減（約90行→約70行）。


def decide(game_state: dict, analysis: dict) -> dict:
    """v42のシンプルな一律構造に完全復帰。反応器状態連動型高度管理を廃止し、一律HIGH_TOWER/MEDIUM_TOWERで頑健に高度管理。"""

    results = analysis.get("results", [])

    if not results:
        return {"x": 0.0, "reason": "no analysis data"}

    best_x = 0.0
    best_score = -float("inf")
    best_reason = ""

    # 盤面情報
    pieces = game_state.get("pieces", [])
    max_y = max([p["y"] for p in pieces]) if pieces else -4.0

    # フェーズ判定（v140: v42の閾値を維持）
    if max_y < 0.8:
        phase = "LOW"
        height_mult = 1.0
        merge_mult = 1.2
    elif max_y < 1.8:
        phase = "MEDIUM"
        height_mult = 2.4  # v140: v42の2.4を維持
        merge_mult = 1.0
    elif max_y < 3.0:
        phase = "HIGH"
        height_mult = 2.6  # v140: v42の2.6を維持
        merge_mult = 1.0
    else:
        phase = "CRITICAL"
        height_mult = 1.0  # CRITICAL: height_multなし
        merge_mult = 0.6  # v140: v42の0.6を維持

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

        # === v140: v42完全復帰・一律構造 ===

        # 1. マージグレードによるスコア（v140: v42の値を維持）
        if merge_grade == "DIRECT":
            score += 1200.0 * merge_mult
            reasons.append("DIRECT_MERGE")
        elif merge_grade == "NEAR":
            score += 600.0 * merge_mult
            reasons.append("NEAR_MERGE")
        elif merge_grade == "FAR":
            score += 200.0 * merge_mult
            reasons.append("FAR_MERGE")

        # 2. 高度によるペナルティ（v140: 反応器状態連動なし、一律で計算）
        height_penalty = landing_y * 50.0 * height_mult

        # 高盤面での追加ペナルティ（v140: v42の一律設定）
        if phase == "HIGH" and landing_y > 0.5:
            height_penalty *= 2.0  # v140: v42の2.0倍
            reasons.append("HIGH_TOWER")
        elif phase == "MEDIUM" and landing_y > 0.5:
            height_penalty *= 1.5  # v140: v42の1.5倍
            reasons.append("MEDIUM_TOWER")
        elif landing_y > 0.0:
            reasons.append("HIGH_LAYER")

        score -= height_penalty

        # 3. ドリフトによるペナルティ（v140: v42の一律30.0を維持）
        drift_penalty = (abs(drift_x) + drift_unc) * 30.0
        score -= drift_penalty

        # 4. 左右バランス補正（v140: v42の設定を維持）
        balance_strength = 20.0
        if phase == "HIGH":
            balance_strength = 40.0  # v140: v42の40.0
        elif phase == "MEDIUM":
            balance_strength = 30.0  # v140: v42の30.0

        left_count = sum(1 for p in pieces if p["x"] < 0)
        right_count = len(pieces) - left_count
        balance_bias = (right_count - left_count) / (len(pieces) if pieces else 1)

        balance_penalty = x * balance_bias * balance_strength
        score -= abs(balance_penalty)

        # 5. nextNextが同じタイプなら中央寄せボーナス（v140: v42の一律50.0を維持）
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
