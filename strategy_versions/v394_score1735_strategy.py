#!/usr/bin/env python3
"""strategy.py - AI改善対象の決定スクリプト"""

# 固定インターフェース:
# decide(game_state: dict, analysis: dict) -> dict
#    戻り値: {"x": float, "reason": str}
#
# AI改変可能: decide() 内部、ヘルパー関数、定数、import
# AI改変禁止: decide() シグネチャ、if __name__ == "__main__" ブロック

# --- 変更履歴 ---
# v340: マージ優先・高度管理緩和版 - v339の失敗（avg_score=1179.4、HEIGHT_CONTROL支配）を受けて、根本的な問題を特定。v339はv128のheight_mult=1.8が強すぎてHEIGHT_CONTROLが支配的になったことが原因。v338のマージボーナス強化は正しい方向だったが、reactive_pairs/near_pairsボーナスの追加が複雑化を招いた。そこで、マージボーナス強化（DIRECT=1500/NEAR=800/FAR=300）を維持しつつ、reactive_pairs/near_pairsボーナスを削除し、HIGHフェーズのheight_multを1.8から1.7に緩和。CENTER_PAIRボーナス（次の2ピースが同じ場合の中央寄せ）は効果が薄いため削除。これにより、マージを優先しつつHEIGHT_CONTROLの支配を抑制し、v339のシンプルさを維持。
#   根本原因の特定:
#   - v128のheight_mult=1.8 + HIGH_TOWERペナルティ1.3倍の組み合わせで、HIGHフェーズでマージを優先しすぎていなかった
#   - v338のマージボーナス強化（DIRECT=1500/NEAR=800/FAR=300）は正しい方向だったが、reactive_pairs/near_pairsボーナスの追加が複雑化を招いた
#   - CENTER_PAIRボーナスは効果が薄く、コード行数増加の原因
#   改善策（マージ優先・高度管理緩和）:
#   - マージボーナス強化: v338の値を採用（DIRECT=1500/NEAR=800/FAR=300）
#   - HIGHフェーズheight_mult緩和: 1.8 → 1.7（v128より緩和、v42の2.6よりは強い）
#   - CENTER_PAIRボーナス削除: 次の2ピースが同じ場合の中央寄せボーナスは削除
#   - reactive_pairs/near_pairsボーナス削除: v338の複雑化要因を削除
#   核心的発見: v338のマージボーナス強化を維持しつつ、複雑化要因を削除することで、マージ優先とシンプルさを両立させる。height_multの微調整（1.7）でHEIGHT_CONTROLの支配を抑制。
#   成功基準: avg_scoreがv339の1179.4以上、またはmerge_rateが15%以上、またはavg_scoreがv338の1425.2以上
#   失敗基準: avg_scoreがv339の1179.4未満、またはmerge_rateが11%未満、またはavg_scoreがv338の1425.2未満
# [BEST:3689] v128: HIGHフェーズマージ優先版
# [BEST:2335] v42: v19復活・v31/v29複雑化要素削除版
# [BEST:1509] v328: HIGHフェーズマージ強化・v42ベース版


def decide(game_state: dict, analysis: dict) -> dict:
    """マージを優先しつつ、高度管理を緩和。v338のマージボーナス強化を維持し、複雑化要因を削除。"""

    results = analysis.get("results", [])

    if not results:
        return {"x": 0.0, "reason": "no analysis data"}

    best_x = 0.0
    best_score = -float("inf")
    best_reason = ""

    # 盤面情報
    pieces = game_state.get("pieces", [])
    max_y = max([p["y"] for p in pieces]) if pieces else -4.0

    # フェーズ判定（v340: v128の閾値0.8/1.8/3.0を維持）
    if max_y < 0.8:
        phase = "LOW"
        height_mult = 1.0
        merge_mult = 1.2
    elif max_y < 1.8:
        phase = "MEDIUM"
        height_mult = 2.4
        merge_mult = 1.0
    elif max_y < 3.0:
        phase = "HIGH"
        height_mult = 1.7  # v340: v128の1.8から1.7に緩和、HEIGHT_CONTROLの支配を抑制
        merge_mult = 1.0
    else:
        phase = "CRITICAL"
        height_mult = 1.0
        merge_mult = 0.6

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

        # === v340: マージ優先・高度管理緩和 ===

        # 1. マージグレードによるスコア（v340: v338のマージボーナス強化を採用）
        if merge_grade == "DIRECT":
            score += 1500.0 * merge_mult  # v340: v338の1500.0を採用
            reasons.append("DIRECT_MERGE")
        elif merge_grade == "NEAR":
            score += 800.0 * merge_mult  # v340: v338の800.0を採用
            reasons.append("NEAR_MERGE")
        elif merge_grade == "FAR":
            score += 300.0 * merge_mult  # v340: v338の300.0を採用
            reasons.append("FAR_MERGE")

        # 2. 高度によるペナルティ（v340: height_multを1.7に緩和）
        height_penalty = landing_y * 50.0 * height_mult

        # HIGH_TOWERペナルティ（v340: v128の1.3倍を維持）
        if phase == "HIGH" and landing_y > 0.5:
            height_penalty *= 1.3  # v340: v128の1.3倍を維持
            reasons.append("HIGH_TOWER")
        elif phase == "MEDIUM" and landing_y > 0.5:
            height_penalty *= 1.5
            reasons.append("MEDIUM_TOWER")
        elif landing_y > 0.0:
            reasons.append("HIGH_LAYER")

        score -= height_penalty

        # 3. ドリフトによるペナルティ（v340: v128の一律30.0を維持）
        drift_penalty = (abs(drift_x) + drift_unc) * 30.0
        score -= drift_penalty

        # 4. 左右バランス補正（v340: v128の設定を維持）
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
