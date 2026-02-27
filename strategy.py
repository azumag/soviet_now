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
# v123: reactive_pairs活用・MEDIUMフェーズ復帰版 - v122の失敗（スコア1371、HIGH_TOWERペナルティ支配でマージ機会損失）を受けて、reactive_pairs情報を活用しマージ機会を確保。履歴分析でTurn 67（reactive_pairs=5）とTurn 90-91（reactive_pairs=4）でHIGH_TOWERペナルティが出ておりマージ機会を損なっていることを特定。（1）MEDIUMフェーズのheight_multを2.2から2.4に戻す（v42の2.4に復帰、v122の緩和は過剰）。（2）HIGHフェーズでreactive_pairs>=2の時はHIGH_TOWERペナルティを無効化（マージ可能状態を尊重）。（3）reactive_pairsはanalysis["reactor"]["reactive_pairs"]から取得。v42のシンプル構造（約110行）を維持しつつ、reactive_pairs条件のみ追加。コード量微増（約115行）。
# v124: v42構造復帰・balance強化・MEDIUM緩和版 - v123の失敗（スコア442、Turn 43-47でエラー発生）を受けて、振り子パターンとreactive_pairs条件バグを解消し、v42の成功構造をベースにv122の有効要素とbalance強化を組み合わせる。（1）振り子パターン回避：MEDIUMフェーズheight_multはv122の2.2を採用し維持（2.2→2.4の振り子停止）。（2）reactive_pairs条件を完全削除（バグの原因、複雑化の元）。（3）v42のbalance強化を復活：HIGHフェーズbalance_strength=40.0、MEDIUMフェーズ=30.0（v122/v123の一律20.0は弱すぎ）。（4）v42のシンプル構造に復帰：マージボーナス（DIRECT=1200/NEAR=600/FAR=200）、drift_penalty一律30.0、center_bonus一律50.0。（5）v122のMEDIUM緩和を維持：MEDIUMフェーズheight_mult=2.2（履歴でMEDIUMフェーズ長期化・MEDIUM_TOWERペナルティ頻発）。振り子パターン回避と構造的改善を同時に実現。コード量削減（約110行）。
# v125: マージ前提戦略廃棄・高度管理徹底版 - v124の失敗（スコア1209、MEDIUMフェーズ12ターン中11回マージ予測・7回MEDIUM_TOWERペナルティ）を受けて、マージ予測を前提とする戦略の破綻を特定。履歴分析でマージ予測に頼って高い位置にドロップし続けていることを確認（LOWフェーズ24ターン中9回HIGH_LAYER、MEDIUMフェーズ12ターン中11回マージ予測）。（1）マージボーナス50%縮小（DIRECT=1200→600/NEAR=600→300/FAR=200→100）：予測ミス時のリスクを軽減し、マージは「ボーナス」として扱う。（2）MEDIUMフェーズ高度管理強化（height_mult=2.2→1.8）：MEDIUM_TOWERペナルティ頻発（履歴で7回）を抑制し、低い位置を確実に確保。（3）HIGHフェーズ高度管理微調整（height_mult=2.6→2.4）：MEDIUMフェーズ強化とのバランス調整。（4）CRITICALフェーズマージ優先（merge_mult=0.6→0.8）：height_mult縮小の代償としてマージを優先。（5）v42のシンプル構造を維持（balance_strength微調整、drift_penalty一律30.0、center_bonus一律50.0）。振り子パターン（v122/v123/v124のheight_mult振り子）を根本的に解消し、高度管理を徹底優先するブレイクスルー戦略。コード量維持（約110行）。


def decide(game_state: dict, analysis: dict) -> dict:
    """マージ予測を前提とせず、高度管理を徹底優先。マージはボーナスとして扱う。"""

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
        height_mult = 1.8  # v125: MEDIUMフェーズ高度管理強化（2.2→1.8）
        merge_mult = 1.0
    elif max_y < 3.0:
        phase = "HIGH"
        height_mult = 2.4  # v125: HIGHフェーズ高度管理微調整（2.6→2.4）
        merge_mult = 1.0
    else:
        phase = "CRITICAL"
        height_mult = 1.0
        merge_mult = 0.8  # v125: CRITICALフェーズマージ優先（0.6→0.8）

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

        # 1. マージグレードによるスコア（v125: 50%縮小、マージはボーナス）
        if merge_grade == "DIRECT":
            score += 600.0 * merge_mult  # v125: 1200→600（50%縮小）
            reasons.append("DIRECT_MERGE")
        elif merge_grade == "NEAR":
            score += 300.0 * merge_mult  # v125: 600→300（50%縮小）
            reasons.append("NEAR_MERGE")
        elif merge_grade == "FAR":
            score += 100.0 * merge_mult  # v125: 200→100（50%縮小）
            reasons.append("FAR_MERGE")

        # 2. 高度によるペナルティ
        height_penalty = landing_y * 50.0 * height_mult

        # HIGH_TOWERペナルティ（v125: v42の設定を維持）
        if phase == "HIGH" and landing_y > 0.5:
            height_penalty *= 2.0
            reasons.append("HIGH_TOWER")
        elif phase == "MEDIUM" and landing_y > 0.5:
            height_penalty *= 1.5
            reasons.append("MEDIUM_TOWER")
        elif landing_y > 0.0:
            reasons.append("HIGH_LAYER")

        score -= height_penalty

        # 3. ドリフトによるペナルティ（一律30.0）
        drift_penalty = (abs(drift_x) + drift_unc) * 30.0
        score -= drift_penalty

        # 4. 左右バランス補正（v125: 微調整）
        balance_strength = 20.0
        if phase == "HIGH":
            balance_strength = 35.0  # v125: 40.0→35.0（微調整）
        elif phase == "MEDIUM":
            balance_strength = 25.0  # v125: 30.0→25.0（微調整）

        left_count = sum(1 for p in pieces if p["x"] < 0)
        right_count = len(pieces) - left_count
        balance_bias = (right_count - left_count) / (len(pieces) if pieces else 1)

        balance_penalty = x * balance_bias * balance_strength
        score -= abs(balance_penalty)

        # 5. nextNextが同じタイプなら中央寄せボーナス（一律50.0）
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
