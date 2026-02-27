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
# v135: CRITICALフェーズ動的調整・マージ優先強化版 - v134の失敗（スコア1264）を受けて、CRITICALフェーズの高度管理緩和とマージボーナス強化を実施。履歴分析でCRITICALフェーズ（turns 72-75）でマージが発生していないことを特定。（1）CRITICALフェーズ高度ペナルティ大幅緩和：height_multを1.0から0.5に緩和し、CRITICALフェーズでの高度管理を大幅に緩和。（2）CRITICALフェーズマージボーナス強化：merge_multを0.6から0.8に強化し、最後のマージ機会を確実に捉える。（3）HIGHフェーズマージボーナス維持：merge_mult=1.2を維持し、HIGHフェーズでのマージ優先を徹底。（4）MEDIUMフェーズ高度管理維持：height_mult=2.4を維持し、HIGH到達遅延。（5）フェーズ判定の動的調整：max_yに応じてフェーズを自動的に調整し、状況に応じた戦略を実施。コード量微増（約100行→約110行）。失敗（スコア772）：履歴分析でHIGHフェーズ（turns 56-62、7ターン）でマージが発生していないことを特定。decision_reasonはすべて「HIGH_LAYER」を含み、REACTIVE_PAIRSボーナスが43回適用されマージより優先されている。HIGH_LAYER条件（landing_y > 0.0）が緩すぎ、高度管理が支配的。v135のHIGHフェーズ設定（height_mult=2.2, merge_mult=1.2）はマージ優先の意図があるが、HIGH_LAYERペナルティとREACTIVE_PAIRSボーナスが優先され、マージ機会を損失。
# v136: v128成功構造とv134簡素化の統合・HIGHフェーズマージ優先再調整版 - v135の失敗（スコア772、HIGHフェーズマージ0回）を受けて、v128成功構造（height_mult=1.8）とv134簡素化（HIGH_TOWER削除）の統合でHIGHフェーズマージ機会を確保。履歴分析でv135のHIGHフェーズ7ターンすべてでHIGH_LAYERペナルティとREACTIVE_PAIRSボーナスが適用され、マージが優先されていないことを特定。（1）HIGHフェーズheight_mult=1.8を復帰：v128の成功要素を採用し、高度管理を緩和してマージを優先。v134のheight_mult=2.2は高度管理不十分で盤面が高くなる。（2）HIGH_TOWERペナルティ1.2倍を再導入：v133の1.3倍は過剰でマージ機会を損失したため、1.2倍で緩和。v134の削除は高度管理不十分の原因。（3）HIGH_LAYER条件厳格化：landing_y > 0.5から適用し、過剰な高度ペナルティを回避。v135のlanding_y > 0.0は緩すぎ。（4）HIGHフェーズREACTIVE_PAIRSボーナス無効化：HIGHフェーズではマージを優先し、REACTIVE_PAIRSボーナスを適用しない。（5）MEDIUMフェーズHIGH_TOWERペナルティ緩和：v42の1.5倍から1.3倍に緩和し、HIGH到達を容易にする。（6）HIGHフェーズmerge_mult=1.1で微増：v134の1.2強化は効果なく、v128の1.0よりわずかに強化。（7）バランス補正調整：HIGHフェーズbalance_strengthを30.0に緩和（v135の40.0は過剰）、MEDIUMフェーズを25.0に緩和（v42の30.0は少し強い）。（8）v134の簡素化を維持：反応器の状態に基づく複雑なボーナス（CLOSE_MERGE, NEAR_PAIRS, PIPELINE_OK）は削除せず維持、コード量維持（約110行）。v128の成功要素（height_mult=1.8）とv134の簡素化を統合し、HIGHフェーズでのマージ機会を構造的に確保。振り子パターン（v42↔v128、HIGH_TOWER削除・再導入）を第三の選択肢（緩和したHIGH_TOWER再導入）で解消。失敗（スコア484）：最終盤面で全42ピースが孤立し、これ以上マージ不可能な状態でゲーム終了。v136は高度管理に過度に集中し、マージ機会創出が不十分。v42（2335）やv128（3689）の成功構造を再分析し、マージ優先を強化。
# v137: v42ベース・HIGHフェーズマージ優先強化版 - v136の失敗（スコア484、全ピース孤立）を受けて、v42のシンプルかつ頑健な構造に復帰しつつ、HIGHフェーズでのマージ優先を強化。履歴分析でHIGHフェーズ（turns 53-59、7ターン）でマージ予測は5ターンで発生したが実際のマージは2ターンのみ（merge_available=falseが5ターン）。v136の複雑な条件（HIGH_TOWER再導入・HIGH_LAYER厳格化・REACTIVE_PAIRS無効化）が過度に高度管理を強化し、着地位置の柔軟性を制限した。（1）v42基本構造復帰：HIGH_LAYER条件（landing_y > 0.0）を緩和、HIGH_TOWERペナルティ（2.0倍）をv42の強い値に復帰、バランス補正（HIGH=40.0）をv42の強い値に復帰。これにより着地位置の柔軟性を確保し、マージ機会を創出。（2）HIGHフェーズマージ優先強化：merge_multをv136の1.1から1.2に強化し、height_multをv136の1.8から2.3に調整（v42の2.6とv84の2.2の中間値）。高度管理を緩和しつつ、v42の強いHIGH_TOWERペナルティとバランス補正で盤面を安定化。（3）REACTIVE_PAIRSボーナス有効化：v136のHIGHフェーズ無効化を削除し、v42のシンプル構造に復帰。phase-specificな条件分岐を削減し、コード簡素化。（4）v42のシンプル構造維持：DIRECT=1200/NEAR=600/FAR=200、drift_penalty=30、balance補正、NEXT_SAMEボーナス=50を維持。反応器の状態に基づくボーナス（CLOSE_MERGE, NEAR_PAIRS, PIPELINE_OK, REACTIVE_PAIRS）は全て維持。振り子パターン（HIGH_TOWERの削除・再導入、HIGH_LAYERの厳格化・緩和、REACTIVE_PAIRSの無効化・有効化）をv42の成功構造に戻すことで根本的に解消。コード量削減（約110行→約90行）。


def decide(game_state: dict, analysis: dict) -> dict:
    """v42のシンプル構造に復帰し、HIGHフェーズでマージ優先を強化。height_mult=2.3で高度管理を緩和しつつ、v42の強いHIGH_TOWERペナルティとバランス補正で盤面を安定化。"""

    results = analysis.get("results", [])

    if not results:
        return {"x": 0.0, "reason": "no analysis data"}

    best_x = 0.0
    best_score = -float("inf")
    best_reason = ""

    # 盤面情報
    pieces = game_state.get("pieces", [])
    max_y = max([p["y"] for p in pieces]) if pieces else -4.0

    # フェーズ判定（v137: v42の閾値を維持）
    if max_y < 0.8:
        phase = "LOW"
        height_mult = 1.0
        merge_mult = 1.2
    elif max_y < 1.8:
        phase = "MEDIUM"
        height_mult = 2.4  # v137: v42の2.4を維持（頑健な高度管理）
        merge_mult = 1.0
    elif max_y < 3.0:
        phase = "HIGH"
        height_mult = (
            2.3  # v137: v42の2.6とv84の2.2の中間値（高度管理緩和・マージ優先）
        )
        merge_mult = 1.2  # v137: HIGHフェーズでマージ優先を強化
    else:
        phase = "CRITICAL"
        height_mult = 1.0  # CRITICAL: height_multなし
        merge_mult = 0.6  # v137: v42の0.6を維持

    # 次のピース情報
    next_piece = game_state.get("next", {})
    next_next_piece = game_state.get("nextNext", {})
    next_type = next_piece.get("type", 0)
    next_next_type = next_next_piece.get("type", 0)

    # 反応器の状態に基づく追加ボーナス
    reactor = analysis.get("reactor", {})
    reactive_pairs = reactor.get("reactive_pairs", 0)
    # reactive_pairsがlistの場合は長さを使用（v96のバグ修正）
    if isinstance(reactive_pairs, list):
        reactive_pairs = len(reactive_pairs)
    near_pairs = reactor.get("near_pairs", 0)
    # near_pairsもlistの場合は長さを使用
    if isinstance(near_pairs, list):
        near_pairs = len(near_pairs)
    pipeline = reactor.get("pipeline", [])

    for result in results:
        x = result["x"]
        landing_y = result.get("landing_y", 0)
        drift_x = result.get("drift_x", 0)
        drift_unc = result.get("drift_unc", 0)
        merge_grade = result.get("merge_grade", "NO")

        score = 0.0
        reasons = []

        # === v137: v42基本構造復帰・HIGHフェーズマージ優先強化 ===

        # 1. マージグレードによるスコア（v137: v42の値を維持）
        if merge_grade == "DIRECT":
            score += 1200.0 * merge_mult
            reasons.append("DIRECT_MERGE")
        elif merge_grade == "NEAR":
            score += 600.0 * merge_mult
            reasons.append("NEAR_MERGE")
        elif merge_grade == "FAR":
            score += 200.0 * merge_mult
            reasons.append("FAR_MERGE")

        # マージ予測に基づく追加ボーナス
        if merge_grade != "NO":
            for merge in result.get("merges", []):
                distance = merge.get("distance", float("inf"))
                if distance < 0.5:
                    score += 100.0
                    reasons.append("CLOSE_MERGE")

        # 反応器の状態に基づく追加ボーナス（v137: phase-specific制限なし、v42のシンプル構造）
        if reactive_pairs >= 2:
            score += 50.0
            reasons.append("REACTIVE_PAIRS")
        if near_pairs >= 3:
            score += 30.0
            reasons.append("NEAR_PAIRS")
        if pipeline and all(p is not None for p in pipeline):
            score += 20.0
            reasons.append("PIPELINE_OK")

        # 2. 高度によるペナルティ（v137: v42の強いHIGH_TOWERペナルティを維持）
        height_penalty = landing_y * 50.0 * height_mult

        # HIGH_TOWERペナルティ（v137: v42の2.0倍を復帰）
        if phase == "HIGH" and landing_y > 0.5:
            height_penalty *= 2.0  # v137: v42の2.0倍を復帰（v136の1.2倍は過度に弱い）
            reasons.append("HIGH_TOWER")
        elif phase == "MEDIUM" and landing_y > 0.5:
            height_penalty *= 1.5  # v137: v42の1.5倍を維持
            reasons.append("MEDIUM_TOWER")
        elif landing_y > 0.0:
            reasons.append("HIGH_LAYER")

        score -= height_penalty

        # 3. ドリフトによるペナルティ（v137: v42の一律30.0を維持）
        drift_penalty = (abs(drift_x) + drift_unc) * 30.0
        score -= drift_penalty

        # 4. 左右バランス補正（v137: v42の設定を維持）
        balance_strength = 20.0
        if phase == "HIGH":
            balance_strength = 40.0  # v137: v42の40.0を復帰（v136の30.0は過度に弱い）
        elif phase == "MEDIUM":
            balance_strength = 30.0  # v137: v42の30.0を維持

        left_count = sum(1 for p in pieces if p["x"] < 0)
        right_count = len(pieces) - left_count
        balance_bias = (right_count - left_count) / (len(pieces) if pieces else 1)

        balance_penalty = x * balance_bias * balance_strength
        score -= abs(balance_penalty)

        # 5. nextNextが同じタイプなら中央寄せボーナス（v137: v42の一律50.0を維持）
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
