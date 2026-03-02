#!/usr/bin/env python3
"""strategy.py - AI改善対象の決定スクリプト (v603: v602失敗・v42成功構造完全復帰版)"""

# 固定インターフェース:
# decide(game_state: dict, analysis: dict) -> dict
#    戻り値: {"x": float, "reason": str}
#
# AI改変可能: decide() 内部、ヘルパー関数、定数、import
# AI改変禁止: decide() シグネチャ、if __name__ == "__main__" ブロック

# --- 変更履歴 ---
# [BEST:2346] v42: v19復活・v31/v29複雑化要素削除版 - v41の失敗（スコア558）を受けて、v41がv31から取り入れたreactive_pairsとhas_mergeによる複雑な条件分岐を削除。v19のシンプル構造（DIRECT=1200/NEAR=600/FAR=200、height_penalty=50*height_mult、drift_penalty=30）に復活。v19のCRITICALフェーズ（merge_mult=0.6）を維持。コード量削減（約140行→約110行）で頑健性を確保
# v550: v549ベース・ reactor情報統合・HIGH_LAYERマージ強化版
# v546: 動的ドリフトペナルティ・HIGHフェーズマージ強化版
# v542: v540/v128単純化版
# v602: v551ベース・マージ品質重視・早期ペナルティ緩和版 - v602はスコア798に失敗。EARLY_HIGH_TOWER_PENALTYとDRIFT_NO_MERGE回避が過激で、ゲームが68ターンでmax_y=2.9で早期終了。batch_summary分析でDRIFT_NO_MERGEが10.3%を占めるがavg_score_delta=4.3と非常に低く、マージ機会を見逃していることが判明。v603ではv42の成功構造に完全復帰し、CRITICALフェーズのみを保持。
# v603: v602失敗・v42成功構造完全復帰版 - v602の失敗（スコア798、68ターン、max_y=2.9で早期終了）を受けて、過度な保守性を全て削除。v42のシンプルで成功した構造（DIRECT=1200/NEAR=600/FAR=200、height_penalty=50*height_mult、drift_penalty=30）に完全復帰しつつ、v19のCRITICALフェーズ（merge_mult=0.6、height_multなし）のみを保持。EARLY_HIGH_TOWER_PENALTY、複雑なドリフト品質調整、HIGH_LAYERボーナス複雑化等の失敗要因を全て削除し、シンプルかつ頑健な構造に戻す。
#   batch_summary分析(v602失敗):
#   - DRIFT_NO_MERGE: 10.3%、avg_score_delta=4.3 (非常に低い、マージ回避)
#   - EARLY_HIGH_TOWER_PENALTY: 頻繁出現、序盤過度に保守的
#   - ゲーム終了時max_y=2.9 (68ターンで早期終了、高さ管理失敗)
#   - merge_rate=16.2% (低すぎる、"品質重視"の割には成功率低い)
#   - v42は2346点を達成、v128-like構造は平均1170.8点
#   従来の複雑化（v31のreactive_pairs、v50-v64の振り子パターン、v84のマージ優先過多等）は全て失敗の原因
#   根本的発見: v42のシンプル構造が2346点を達成した成功体験に戻り、必要な機能のみ（CRITICALフェーズ）を保持


def decide(game_state: dict, analysis: dict) -> dict:
    """v603: v602失敗・v42成功構造完全復帰版 - v602の過度な保守性（EARLY_HIGH_TOWER_PENALTY、DRIFT_NO_MERGE回避、複雑なドリフト品質調整）がゲームを68ターンmax_y=2.9で早期終了させた。batch_summaryでDRIFT_NO_MERGEのavg_score_delta=4.3と極端に低いことを特定。v603ではv42のシンプルで成功した構造に完全復帰しつつ、v19のCRITICALフェーズ（merge_mult=0.6、height_multなし）のみを保持。EARLY_HIGH_TOWER_PENALティと複雑なドリフト調整を削除し、頑健なシンプル構造に戻す。"""

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
        height_mult = 2.4  # v42の設定
        merge_mult = 1.0
    elif max_y < 3.0:
        phase = "HIGH"
        height_mult = 2.6  # v42の設定
        merge_mult = 1.0
    else:
        phase = "CRITICAL"
        height_mult = 1.0  # CRITICAL: height_multなし（v42の設定）
        merge_mult = 0.6  # v42の設定（v19のCRITICALフェーズ）

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

        # === v603: v42成功構造完全復帰 ===

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

        # 2. 高度によるペナルティ（v42の一律計算を維持）
        height_penalty = landing_y * 50.0 * height_mult

        # 高盤面での追加ペナルティ（v42の設定を維持）
        if phase == "HIGH" and landing_y > 0.5:
            height_penalty *= 2.0
            reasons.append("HIGH_TOWER")
        elif phase == "MEDIUM" and landing_y > 0.5:
            height_penalty *= 1.5
            reasons.append("MEDIUM_TOWER")
        elif landing_y > 0.0:
            reasons.append("HIGH_LAYER")

        score -= height_penalty

        # 3. ドリフトによるペナルティ（v42の一律30.0を維持）
        drift_penalty = (abs(drift_x) + drift_unc) * 30.0
        score -= drift_penalty

        # 4. 左右バランス補正（v42の設定を維持）
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

        # 5. nextNextが同じタイプなら中央寄せボーナス（v42の設定を維持）
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
            "merge_history": [],
        }
    except Exception as e:
        analysis = {
            "results": [],
            "same_type": [],
            "reactor": {},
            "merge_history": [],
            "error": str(e),
        }

    result = decide(game_state, analysis)
    print(json.dumps(result, ensure_ascii=False, indent=2))
