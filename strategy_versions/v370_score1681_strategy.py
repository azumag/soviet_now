#!/usr/bin/env python3
"""strategy.py - AI改善対象の決定スクリプト"""

# 固定インターフェース:
# decide(game_state: dict, analysis: dict) -> dict
#    戻り値: {"x": float, "reason": str}
#
# AI改変可能: decide() 内部、ヘルパー関数、定数、import
# AI改変禁止: decide() シグネチャ、if __name__ == "__main__" ブロック

# --- 変更履歴 ---
# v334: reactor中核化・マージ評価簡素化版 - v333の失敗(avg=999.5、目標1000未達)を受けて、reactor情報を中核に据え、マージ評価を簡素化。
#   v333バッチ分析から特定した問題:
#   - マージ率11.8%: 目標15%に届かず、直接マージ率は8.5%のみ
#   - decision_reasonの偏り: HEIGHT_CONTROL(21.8%)が支配的で、HIGH_LAYER(4.2%)がマージを阻害
#   - ベストvsワースト: merge_rate(14.3% vs 9.8%)の差がスコア差(1179 vs 875)に直結
#   - マージ期待値計算の効果不透明: merge_expectationボーナスは実装されているが、履歴分析で効果が見えない
#   - reactor情報活用が表面的: reactive_pairsとnear_pairsのボーナスはあるが、マージ評価と統合されていない
#   動的評価がシンプルさを損なっている: v128のシンプル構造を維持しつつ、動的評価を導入しようとしたが、結果として複雑化
#   根本原因:
#   - v333はマージ期待値計算を新規導入したが、計算ロジックが複雑でマージ機会の評価が不正確
#   - near_pairs情報を活用していながら、マージ期待値計算で距離計算を自前で行い、情報の重複
#   - reactive_pairsとnear_pairsはマージ機会の指標だが、マージ評価の中心に据えていない
#   - HIGHフェーズ高度管理の緩和(1.8)が過剰で、盤面が高くなりすぎ、マージ機会を損失
#   解決策（reactor中核化・マージ評価簡素化）:
#   - reactor情報を中核に: near_pairs=0ならマージ機会なしと判断、マージ優先度をreactor情報から直接導出
#   - マージ期待値計算を簡素化: 複雑な距離計算を削除、near_pairs情報を活用
#   - 動的評価のシンプル化: merge_bonusの動的調整(merge_mult)のみ導入、merge_expectationボーナスは削除
#   - HIGHフェーズ高度管理緩和の巻き戻し: v128の1.8をv42の2.6に戻し、安定性確保
#   - merge_bonus計算の単純化: merge_bonus = ベース値 * merge_mult * nextNextボーナス係数
#   - reactor情報の活用強化: near_pairs >= 2ならマージ機会ありと判断、merge_bonusを優先
#   - マージ機会が少ないなら高度管理優先: near_pairs=0ならheight_penaltyを強化
#   核心的発見: reactor情報(near_pairs)はマージ機会の指標だが、v333では表面的にしか活用していない。near_pairsを中核に据え、マージ評価を簡素化することで、v128のシンプルさを維持しつつ、マージ機会を正確に評価。HIGHフェーズ高度管理はv42の安定設定に戻すことで、過剰な盤面上昇を抑制。
#   成功基準: avg_scoreが1000以上、またはmerge_rateが15%以上、またはavg_scoreがv333の999.5以上
#   失敗基準: avg_scoreがv332の725.0未満、またはmerge_rateが10%未満、またはavg_scoreがv333の999.5未満
# [BEST:3689] v128: HIGHフェーズマージ優先版
# [BEST:2335] v42: v19復活・v31/v29複雑化要素削除版
# [BEST:1509] v328: HIGHフェーズマージ強化・v42ベース版


def decide(game_state: dict, analysis: dict) -> dict:
    """reactor中核化・マージ評価簡素化版。reactor情報を中核に据え、マージ評価を簡素化。"""

    results = analysis.get("results", [])

    if not results:
        return {"x": 0.0, "reason": "no analysis data"}

    best_x = 0.0
    best_score = -float("inf")
    best_reason = ""

    # 盤面情報
    pieces = game_state.get("pieces", [])
    max_y = max([p["y"] for p in pieces]) if pieces else -4.0

    # reactor情報（v334: 中核化）
    reactor = analysis.get("reactor", {})
    # reactive_pairsとnear_pairsはリストの場合、その長さを取得
    reactive_pairs_val = reactor.get("reactive_pairs", 0)
    reactive_pairs = (
        len(reactive_pairs_val)
        if isinstance(reactive_pairs_val, list)
        else reactive_pairs_val
    )
    near_pairs_val = reactor.get("near_pairs", 0)
    near_pairs = (
        len(near_pairs_val) if isinstance(near_pairs_val, list) else near_pairs_val
    )

    # フェーズ判定（v334: v42の安定設定を採用）
    if max_y < 0.8:
        phase = "LOW"
        height_mult = 1.0
        merge_mult = 1.2
    elif max_y < 1.8:
        phase = "MEDIUM"
        height_mult = 2.4  # v42: 安定値
        merge_mult = 1.0
    elif max_y < 3.0:
        phase = "HIGH"
        height_mult = 2.6  # v334: v128の1.8からv42の2.6に戻し、安定性確保
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

    # マージ機会判定（v334: reactor情報を中核に）
    # near_pairsが2以上あればマージ機会あり、merge_multを強化
    merge_available = near_pairs >= 2
    if merge_available:
        merge_mult *= 1.5  # マージ機会があるならmerge_bonusを強化
        reasons_prefix = ["MERGE_AVAILABLE"]
    else:
        merge_mult *= 0.8  # マージ機会が少ないならmerge_bonusを弱め、高度管理優先

    for result in results:
        x = result["x"]
        landing_y = result.get("landing_y", 0)
        drift_x = result.get("drift_x", 0)
        drift_unc = result.get("drift_unc", 0)
        merge_grade = result.get("merge_grade", "NO")

        score = 0.0
        reasons = []

        # === v334: reactor中核化・マージ評価簡素化 ===

        # 1. マージグレードによるスコア（v334: 簡素化・動的調整）
        merge_bonus = 0.0
        if merge_grade == "DIRECT":
            merge_bonus = 1200.0 * merge_mult
            reasons.append("DIRECT_MERGE")
        elif merge_grade == "NEAR":
            merge_bonus = 600.0 * merge_mult
            reasons.append("NEAR_MERGE")
        elif merge_grade == "FAR":
            merge_bonus = 200.0 * merge_mult
            reasons.append("FAR_MERGE")

        # nextNextが同じタイプならボーナス係数（v334: 動的調整）
        if next_next_type == next_type:
            merge_bonus *= 1.2  # nextNextが同じならmerge_bonusを20%強化
            reasons.append("NEXT_SAME")

        score += merge_bonus

        # 2. 高度によるペナルティ（v334: v42の安定設定を採用）
        height_penalty = landing_y * 50.0 * height_mult

        # HIGH_TOWERペナルティ（v334: v42の安定設定を採用）
        if phase == "HIGH" and landing_y > 0.5:
            height_penalty *= 2.0  # v42: 2.0倍（v128の1.3倍より強化）
            reasons.append("HIGH_TOWER")
        elif phase == "MEDIUM" and landing_y > 0.5:
            height_penalty *= 1.5  # v42: 1.5倍
            reasons.append("MEDIUM_TOWER")
        elif landing_y > 0.0:
            reasons.append("HIGH_LAYER")

        score -= height_penalty

        # 3. ドリフトによるペナルティ（v334: v42の一律30.0を維持）
        drift_penalty = (abs(drift_x) + drift_unc) * 30.0
        score -= drift_penalty

        # 4. 左右バランス補正（v334: v42の安定設定を採用）
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

        # 5. nextNextが同じタイプなら中央寄せボーナス（v334: v42の一律50.0を維持）
        # ※merge_bonusの動的調整で既に使用済みだが、中央寄せボーナスは別途追加
        if next_next_type == next_type:
            center_bonus = max(0, 1.0 - abs(x) / 2.0) * 50.0
            score += center_bonus
            reasons.append("CENTER")

        # 6. reactor情報活用ボーナス（v334: 中核化、活用強化）
        # reactive_pairsが多いほど、盤面が活発でマージが起きやすい
        if reactive_pairs >= 3:
            score += 50.0
            reasons.append("REACTIVE")
        elif reactive_pairs >= 1:
            score += 20.0

        # near_pairsが多いほど、マージ機会が多い（v334: 中核化）
        if near_pairs >= 2:
            score += 100.0  # v334: ボーナス強化（v333の30.0から100.0へ）
            reasons.append("NEAR_PAIR")
        elif near_pairs == 1:
            score += 30.0
            reasons.append("NEAR_PAIR")

        # マージ機会が少ないなら高度管理優先（v334: reactor中核化）
        if near_pairs == 0 and landing_y < 0.0:
            # マージ機会がないなら、盤面を下げる（高度管理優先）
            score -= abs(landing_y) * 20.0
            reasons.append("HEIGHT_CONTROL")

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
