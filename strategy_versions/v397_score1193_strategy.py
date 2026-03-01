#!/usr/bin/env python3
"""strategy.py - AI改善対象の決定スクリプト"""

# 固定インターフェース:
# decide(game_state: dict, analysis: dict) -> dict
#    戻り値: {"x": float, "reason": str}
#
# AI改変可能: decide() 内部、ヘルパー関数、定数、import
# AI改変禁止: decide() シグネチャ、if __name__ == "__main__" ブロック

# --- 変更履歴 ---
# v341: reactive_pairs活用・HIGHフェーズ構造改善版 - v340の失敗（avg_score=1153.7、HEIGHT_CONTROL支配28.9%）を受けて、ベストゲーム（score=1735）の成功要因を分析。ベストゲームではターン70以降にreactive_pairsが1以上に増加し、スコアが825から1735へ急上昇。ターン86-90でreactive_pairsが3-6に達し、大きな連鎖爆発を誘発。このことから「reactive_pairsが多い盤面は動的で、マージが連鎖的に発生しやすい」という性質を特定。v340はreactive_pairs情報を活用していなかったため、動的な盤面でのマージ優先ができなかった。
#   ベストゲームの成功要因の分析:
#   - ターン70でreactive_pairs=1に増加、スコア825→1489へ急増
#   - ターン86でreactive_pairs=6になり、スコア1614→1680へ増加
#   - ターン88でreactive_pairs=3になり、最終スコア1735へ到達
#   - 終盤にmax_y=2.7まで上昇したが、連鎖によるスコア獲得で救済
#   根本原因の特定:
#   - v340はreactive_pairs情報を活用せず、盤面の動的特性を無視していた
#   - 高度管理（height_mult=1.7）が強すぎ、動的な盤面でマージ機会を逃していた
#   - staticなスコアリングのみで、将来の連鎖可能性を考慮していなかった
#   改善策（reactive_pairs活用・HIGHフェーズ構造改善）:
#   - reactive_pairs活用ボーナス導入: reactive_pairsが多いほどマージボーナスを強化
#     * reactive_pairs >= 5: マージボーナス×2.0（連鎖爆発期待）
#     * reactive_pairs >= 3: マージボーナス×1.5（連鎖期待）
#     * reactive_pairs >= 1: マージボーナス×1.1（動的盤面）
#   - HIGHフェーズ高度管理緩和: height_multをv340の1.7から1.5に緩和（マージ機会確保）
#   - マージボーナス強化: v338の強力な値を採用（DIRECT=1500/NEAR=800/FAR=300）
#   - v340のシンプル構造を維持: CENTER_PAIR、reactive_pairs/near_pairsボーナスは削除
#   核心的発見: reactive_pairsは「盤面の動的特性」の指標。動的な盤面ではマージを優先し、静的な盤面では高度管理を行うことで、両方の状況で最適な判断が可能になる。ベストゲームの連鎖爆発はこの戦略で再現可能。
#   成功基準: avg_scoreがv340の1153.7以上、またはavg_scoreがv128の3689以上
#   失敗基準: avg_scoreがv340の1153.7未満
# [BEST:3689] v128: HIGHフェーズマージ優先版
# [BEST:2335] v42: v19復活・v31/v29複雑化要素削除版
# [BEST:1509] v328: HIGHフェーズマージ強化・v42ベース版


def decide(game_state: dict, analysis: dict) -> dict:
    """reactive_pairsを活用し、HIGHフェーズでマージ機会を確保。盤面の動的特性を考慮。"""

    results = analysis.get("results", [])

    if not results:
        return {"x": 0.0, "reason": "no analysis data"}

    best_x = 0.0
    best_score = -float("inf")
    best_reason = ""

    # 盤面情報
    pieces = game_state.get("pieces", [])
    max_y = max([p["y"] for p in pieces]) if pieces else -4.0

    # reactor情報（v341: 盤面の動的特性を活用）
    reactor = analysis.get("reactor", {})
    reactive_pairs_val = reactor.get("reactive_pairs", 0)
    reactive_pairs = (
        len(reactive_pairs_val)
        if isinstance(reactive_pairs_val, list)
        else reactive_pairs_val
    )

    # reactive_pairsに基づくマージボーナス係数（v341: 動的盤面でマージ優先）
    if reactive_pairs >= 5:
        merge_reactive_mult = 2.0  # 連鎖爆発期待
    elif reactive_pairs >= 3:
        merge_reactive_mult = 1.5  # 連鎖期待
    elif reactive_pairs >= 1:
        merge_reactive_mult = 1.1  # 動的盤面
    else:
        merge_reactive_mult = 1.0  # 静的盤面

    # フェーズ判定（v341: v128の閾値0.8/1.8/3.0を維持）
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
        height_mult = 1.5  # v341: v340の1.7から1.5に緩和、マージ機会確保
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

        # === v341: reactive_pairs活用・HIGHフェーズ構造改善 ===

        # 1. マージグレードによるスコア（v341: v338の強力な値＋reactive_pairs活用）
        if merge_grade == "DIRECT":
            score += (
                1500.0 * merge_mult * merge_reactive_mult
            )  # v341: reactive_pairs活用
            reasons.append("DIRECT_MERGE")
        elif merge_grade == "NEAR":
            score += (
                800.0 * merge_mult * merge_reactive_mult
            )  # v341: reactive_pairs活用
            reasons.append("NEAR_MERGE")
        elif merge_grade == "FAR":
            score += (
                300.0 * merge_mult * merge_reactive_mult
            )  # v341: reactive_pairs活用
            reasons.append("FAR_MERGE")

        # 2. 高度によるペナルティ（v341: HIGHフェーズ高度管理緩和）
        height_penalty = landing_y * 50.0 * height_mult

        # HIGH_TOWERペナルティ（v341: v128の1.3倍を維持）
        if phase == "HIGH" and landing_y > 0.5:
            height_penalty *= 1.3  # v341: v128の1.3倍を維持
            reasons.append("HIGH_TOWER")
        elif phase == "MEDIUM" and landing_y > 0.5:
            height_penalty *= 1.5
            reasons.append("MEDIUM_TOWER")
        elif landing_y > 0.0:
            reasons.append("HIGH_LAYER")

        score -= height_penalty

        # 3. ドリフトによるペナルティ（v341: v128の一律30.0を維持）
        drift_penalty = (abs(drift_x) + drift_unc) * 30.0
        score -= drift_penalty

        # 4. 左右バランス補正（v341: v128の設定を維持）
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
