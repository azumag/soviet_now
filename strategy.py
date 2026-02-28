#!/usr/bin/env python3
"""strategy.py - AI改善対象の決定スクリプト"""

# 固定インターフェース:
# decide(game_state: dict, analysis: dict) -> dict
#    戻り値: {"x": float, "reason": str}
#
# AI改変可能: decide() 内部、ヘルパー関数、定数、import
# AI改変禁止: decide() シグネチャ、if __name__ == "__main__" ブロック

# --- 変更履歴 ---
# v322: 高度管理緩和・マージボーナス修正版 - v321の成功（avg=1618.2、高度ペナルティ廃止）を受けて、バッチデータからさらなる改善点を特定。
#   v321バッチ分析から特定した問題:
#   - マージボーナス過剰: DIRECT=3000にもかかわらず、DIRECT_MERGEのavg_score_delta=0.0。precisionペナルティに負けている
#   - PRECISION_CONTROL支配: 54.8%で支配的だが、avg_score_delta=23.7。マージを逃して精度だけを担保している
#   - 高スコア群の特徴: max_y推移が緩やか（序盤0.19→終盤2.34）。「序盤から盤面を緩やかに構築する」ことが成功の鍵
#   - 低スコア群の特徴: 序盤から盤面が低い（-2.63）→終盤で急上昇（1.48）。盤面崩壊リスク
#   根本原因:
#   - v321は「高度ペナルティ完全廃止」で成功したが、マージボーナスが過剰で実際には選択されていない
#   - v321の一律balance_strength=20.0は、HIGHフェーズでのバランス補正が不足
#   - v321は将来のマージ可能性を全く考慮していない
#   解決策（高度管理緩和・マージボーナス修正）:
#   - 高度ペナルティ復活・緩和: v321の「完全廃止」からv128の「高度管理緩和」へ移行。height_mult HIGH=1.8を導入し、HIGH_TOWERペナルティ1.3倍も採用。v321の成功（高度無視）とv128の成功（高度管理緩和）の第三の選択肢
#   - マージボーナス削減: v321の過剰な値（DIRECT=3000/NEAR=1000/FAR=300）をv128の成功値（DIRECT=1200/NEAR=600/FAR=200）に戻す。マージボーナスが過剰でprecisionペナルティに負けている問題を解決
#   - バランス補正の動的調整: v42の段階的設定（LOW=20.0/HIGH=40.0/MEDIUM=30.0）を復活。HIGHフェーズでのバランス補正を強化し、盤面崩壊を防ぐ
#   - v321の成功要素維持: 高度ペナルティは緩和されたheight_multで計算し、v321のような「高度無視」には戻さないが、v128の「高度管理緩和」を採用。PRECISION_CONTROLを抑制し、マージを促進
#   - 将来マージ考慮はシンプルに維持: v321のnextNextが同じタイプなら中央寄せボーナス（50.0）は有効（avg_score_delta=13.8）なので維持。複雑な先読みロジックは導入せず、シンプルさを維持
#   成功基準: avg_scoreがv321の1618.2以上、またはマージ決定率が20%以上になれば成功
#   失敗基準: avg_scoreがv321の1618.2未満、または盤面崩壊で即敗北率が30%以上
# [BEST:3689] v128: HIGHフェーズマージ優先版
# [BEST:2335] v42: v19復活・v31/v29複雑化要素削除版


def decide(game_state: dict, analysis: dict) -> dict:
    """高度管理緩和・マージボーナス修正版。v321の高度無視とv128の高度管理緩和を統合。"""

    results = analysis.get("results", [])

    if not results:
        return {"x": 0.0, "reason": "no analysis data"}

    best_x = 0.0
    best_score = -float("inf")
    best_reason = ""

    # 盤面情報
    pieces = game_state.get("pieces", [])
    max_y = max([p["y"] for p in pieces]) if pieces else -4.0

    # フェーズ判定（v42/v128の閾値0.8/1.8/3.0を維持）
    if max_y < 0.8:
        phase = "LOW"
        height_mult = 1.0
        merge_mult = 1.2
    elif max_y < 1.8:
        phase = "MEDIUM"
        height_mult = 2.4  # v322: v128の2.4を維持
        merge_mult = 1.0
    elif max_y < 3.0:
        phase = "HIGH"
        height_mult = 1.8  # v322: v128の1.8を導入（v321の完全廃止から高度管理緩和へ）
        merge_mult = 1.0
    else:
        phase = "CRITICAL"
        height_mult = 1.0  # CRITICAL: height_multなし
        merge_mult = 0.6  # v322: v128の0.6を維持

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

        # === v322: 高度管理緩和・マージボーナス修正 ===

        # 1. マージグレードによるスコア（v322: v128の成功値に復帰）
        if merge_grade == "DIRECT":
            score += 1200.0 * merge_mult  # v322: v321の3000からv128の1200に削減
            reasons.append("DIRECT_MERGE")
        elif merge_grade == "NEAR":
            score += 600.0 * merge_mult  # v322: v321の1000からv128の600に削減
            reasons.append("NEAR_MERGE")
        elif merge_grade == "FAR":
            score += 200.0 * merge_mult  # v322: v321の300からv128の200に削減
            reasons.append("FAR_MERGE")
        # v322: v321の過剰なマージボーナスを削減し、precisionペナルティに負けている問題を解決

        # 2. 高度によるペナルティ（v322: v128の高度管理緩和を導入）
        height_penalty = landing_y * 50.0 * height_mult

        # HIGH_TOWERペナルティ（v322: v128の緩和設定を維持）
        if phase == "HIGH" and landing_y > 0.5:
            height_penalty *= 1.3  # v322: v128の1.3倍を採用
            reasons.append("HIGH_TOWER")
        elif phase == "MEDIUM" and landing_y > 0.5:
            height_penalty *= 1.5  # v322: v128の1.5倍を維持
            reasons.append("MEDIUM_TOWER")
        elif landing_y > 0.0:
            reasons.append("HIGH_LAYER")

        score -= height_penalty

        # 3. ドリフトによるペナルティ（v322: v128の一律30.0を維持）
        drift_penalty = (abs(drift_x) + drift_unc) * 30.0
        score -= drift_penalty

        # 4. 左右バランス補正（v322: v42の段階的設定を復活）
        balance_strength = 20.0
        if phase == "HIGH":
            balance_strength = (
                40.0  # v322: v42の40.0を維持（HIGHフェーズでのバランス補正を強化）
            )
        elif phase == "MEDIUM":
            balance_strength = 30.0  # v322: v42の30.0を維持

        left_count = sum(1 for p in pieces if p["x"] < 0)
        right_count = len(pieces) - left_count
        balance_bias = (right_count - left_count) / (len(pieces) if pieces else 1)

        balance_penalty = x * balance_bias * balance_strength
        score -= abs(balance_penalty)

        # 5. nextNextが同じタイプなら中央寄せボーナス（v322: v321の50.0を維持、有効）
        if next_next_type == next_type:
            center_bonus = max(0, 1.0 - abs(x) / 2.0) * 50.0
            score += center_bonus
            reasons.append("NEXT_SAME")

        # スコア更新（v322: v128のシンプルなreason生成）
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
