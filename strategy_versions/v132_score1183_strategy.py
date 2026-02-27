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
# v130: v42完全復帰・振り子パターン解消 - v129の高スコア3689が乱数運によるものと判断し、v42完全復帰版をv130として実行。履歴分析でv130の失敗（スコア665）を特定：HIGHフェーズ16ターン中15ターンでマージ不可、decision_reasonは全て高度管理（HIGH_TOWER/HIGH_LAYER/MEDIUM_TOWER）。height_mult=2.6の高度ペナルティがマージボーナス（DIRECT:1200）を圧倒し、HIGHフェーズでマージを優先できない構造的問題を特定。最終盤面でピース75がy=4.029で到達不能になり、レッドライン警告が発動。v42への完全復帰が失敗したのは、v42の成功が「特定の運の良いゲーム」に依存していた可能性があるため。
# v131: v42・v128統合構造版 - v130の失敗（スコア665）を受けて、振り子パターン（v42↔v128の完全復帰）を第三の選択肢で解消。履歴分析でv130のheight_mult=2.6はHIGHフェーズで高度ペナルティを過剰に強化し、マージボーナスを圧倒することを特定（landing_y=1.0以上で高度ペナルティ130.0+がDIRECT_MERGEボーナス1200に匹敵）。v128のHIGHフェーズマージ優先構造（height_mult=1.8）はHIGHフェーズでのマージ機会確保に効果的であることが履歴データから確認。（1）HIGHフェーズheight_mult=1.8を復帰：v128の成功要素を採用し、マージをHIGHフェーズの主要目標にする。（2）MEDIUMフェーズheight_mult=2.4を維持：v42の頑健な高度管理を維持し、HIGH到達遅延。（3）HIGHフェーズHIGH_TOWERペナルティ=1.3倍を採用：v128の緩和設定を維持し、height_mult緩和と相乗効果。（4）v42の基本構造を維持：DIRECT=1200/NEAR=600/FAR=200、drift_penalty=30、balance補正、NEXT_SAMEボーナス=50。（5）NO_MERGEペナルティは導入しない：マージ予測を前提としないシンプルなアプローチ。v42とv128の成功要素を統合し、振り子パターンを第三の選択肢（MEDIUMはv42、HIGHはv128）で解消。コード量維持（約110行）。失敗（スコア915）：履歴分析でMEDIUMフェーズ6ターン中5ターンでMEDIUM_TOWER、HIGHフェーズ11ターン中7ターンでHIGH_TOWER、マージ発生は2回のみ（ターン16と25）。v131の統合構造が機能していない：MEDIUMフェーズのHIGH_TOWERペナルティ1.5倍が過剰で、HIGH到達時に盤面が硬直化、HIGHフェーズでマージ機会が損失。CRITICALフェーズ2ターン中0回でマージ優先できず。
# v132: 全フェーズ高度管理緩和・マージ優先版 - v131の失敗（スコア915）を受けて、統合構造の根本的欠陥を特定。履歴分析でMEDIUMフェーズのHIGH_TOWERペナルティ1.5倍が過剰で、HIGH到達時に盤面が硬直化することが原因。（1）MEDIUMフェーズ高度管理緩和：HIGH_TOWERペナルティをv131の1.5倍から1.3倍に緩和し、HIGH到達を容易にする。（2）HIGHフェーズ高度管理緩和：height_multをv131の1.8から1.6に緩和し、マージ優先を徹底。（3）HIGHフェーズHIGH_TOWERペナルティ維持：v128の1.3倍を維持し、height_mult緩和と相乗効果。（4）CRITICALフェーズマージ優先強化：merge_multをv131の0.6から0.8に強化し、最後のマージ機会を確実に捉える。（5）v42の基本構造維持：DIRECT=1200/NEAR=600/FAR=200、drift_penalty=30、balance補正、NEXT_SAMEボーナス=50。v128の成功要素（高度管理緩和）を全フェーズに拡張し、マージ優先を徹底。振り子パターン（v131のMEDIUM過剰強化・HIGH緩和）を全フェーズ一律緩和で解消。コード量維持（約110行）。


def decide(game_state: dict, analysis: dict) -> dict:
    """全フェーズで高度管理を緩和し、マージ優先を徹底。"""

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
        height_mult = 2.4  # v132: v42の2.4を維持
        merge_mult = 1.0
    elif max_y < 3.0:
        phase = "HIGH"
        height_mult = 1.6  # v132: height_multを1.8から1.6に緩和（マージ優先を徹底）
        merge_mult = 1.0
    else:
        phase = "CRITICAL"
        height_mult = 1.0  # CRITICAL: height_multなし
        merge_mult = 0.8  # v132: merge_multを0.6から0.8に強化（マージ優先を徹底）

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

        # === v132: 全フェーズ高度管理緩和・マージ優先 ===

        # 1. マージグレードによるスコア（v132: v42の強力な値を維持）
        if merge_grade == "DIRECT":
            score += 1200.0 * merge_mult
            reasons.append("DIRECT_MERGE")
        elif merge_grade == "NEAR":
            score += 600.0 * merge_mult
            reasons.append("NEAR_MERGE")
        elif merge_grade == "FAR":
            score += 200.0 * merge_mult
            reasons.append("FAR_MERGE")

        # 2. 高度によるペナルティ（v132: HIGHフェーズ高度管理緩和）
        height_penalty = landing_y * 50.0 * height_mult

        # HIGH_TOWERペナルティ（v132: MEDIUM/HIGH共に1.3倍に統一）
        if phase == "HIGH" and landing_y > 0.5:
            height_penalty *= 1.3  # v132: v128の1.3倍を維持
            reasons.append("HIGH_TOWER")
        elif phase == "MEDIUM" and landing_y > 0.5:
            height_penalty *= 1.3  # v132: v131の1.5倍から1.3倍に緩和
            reasons.append("MEDIUM_TOWER")
        elif landing_y > 0.0:
            reasons.append("HIGH_LAYER")

        score -= height_penalty

        # 3. ドリフトによるペナルティ（v132: v42の一律30.0を維持）
        drift_penalty = (abs(drift_x) + drift_unc) * 30.0
        score -= drift_penalty

        # 4. 左右バランス補正（v132: v42の設定を維持）
        balance_strength = 20.0
        if phase == "HIGH":
            balance_strength = 40.0
        elif phase == "MEDIUM":
            balance_strength = 30.0

        left_count = sum(1 for p in pieces if p["x"] < 0)
        right_count = len(pieces) - left_count
        balance_bias = (right_count - left_count) / (len(pieces) if pieces else 1)

        balance_penalty = x * balance_bias * balance_strength
        score -= abs(balance_penalty)

        # 5. nextNextが同じタイプなら中央寄せボーナス（v132: v42の一律50.0を維持）
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
