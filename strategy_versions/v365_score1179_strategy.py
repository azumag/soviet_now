#!/usr/bin/env python3
"""strategy.py - AI改善対象の決定スクリプト"""

# 固定インターフェース:
# decide(game_state: dict, analysis: dict) -> dict
#    戻り値: {"x": float, "reason": str}
#
# AI改変可能: decide() 内部、ヘルパー関数、定数、import
# AI改変禁止: decide() シグネチャ、if __name__ == "__main__" ブロック

# --- 変更履歴 ---
# v333: 振り子パターン回避・マージボーナス動的化版 - v332のスコア低下（avg=725、v128の3689から大幅低下）を受けて、振り子パターンを回避し、マージボーナスに動的調整を導入。
#   v332バッチ分析から特定した問題:
#   - decision_reasonの偏り: HEIGHT_CONTROL(31.7%)とHIGH_LAYER(18.3%)が支配的で、マージ関連(DIRECT_MERGE 3.3%、NEAR_MERGE 8.3%)が圧倒的に少ない
#   - ベストvsワースト: merge_rate(24.1% vs 9.7%)の差がスコア差(783点 vs 667点)に直結
#   - v332はv128と「同じ構造」と主張しているが、スコアが大幅に異なる
#   - v128の成功要因は「HIGHフェーズでマージを優先」する動的な判断ロジックにあった可能性
#   - 振り子パターン検出: v331→v332で「バランス補正」「連鎖マージボーナス」「ドリフト緩和」の追加→削除が発生
#   根本原因:
#   - v332はv128のパラメータを「コピー」しているが、動的な判断ロジックが欠如
#   - 分析ボーナスの活用不足: analysis[\"reactor\"]のreactive_pairs、near_pairs情報を活用していない
#   - マージ機会の動的評価: 盤面のtype N-1のペア数から将来のマージ期待値を計算していない
#   - 振り子パターン回避の必要性: 「Aを追加→削除→再追加」を繰り返すなら、Aではなく周囲の設計を改善すべき
#   解決策（振り子パターン回避・マージボーナス動的化）:
#   - 振り子回避のための第三の選択肢: v128の静的なパラメータ調整（balance_strength強化、merge_mult変更）ではなく、analysis情報を活用した動的評価を導入
#   - マージ期待値ボーナス導入: 盤面のtype N-1のペア数と距離から、将来マージ期待値を計算。analysis[\"reactor\"]のnear_pairs情報も活用
#   - 高度評価の動的化: 盤面のmax_yとnextのタイプを考慮し、HIGHフェーズへの移行戦略を動的に調整
#   - reactor情報活用: analysis[\"reactor\"][\"reactive_pairs\"]（反応性ペア）とnear_pairs（近接ペア）をスコアリングに反映
#   - スコアリングの簡素化: v128の5要素構造を維持しつつつ、マージ評価を動的化することで柔軟性を確保
#   - 振り子パターン完全回避: v331で追加した複雑な条件分岐（連鎖マージボーナス、ドリフト緩和条件）を導入せず、v128のシンプル構造を維持
#   核心的発見: v128の成功は「静的なパラメータ調整」ではなく、「盤面情報を活用した動的な判断」にあった可能性。振り子パターンを回避し、analysis情報を活用した動的評価を導入することで、v332のシンプルさを維持しつつつ、スコアを改善する。
#   成功基準: avg_scoreが1000以上、またはmerge_rateが15%以上、またはavg_scoreがv332の725.0以上
#   失敗基準: avg_scoreが500未満、またはmerge_rateが10%未満、またはavg_scoreがv332の725.0未満
# [BEST:3689] v128: HIGHフェーズマージ優先版
# [BEST:2335] v42: v19復活・v31/v29複雑化要素削除版
# [BEST:1509] v328: HIGHフェーズマージ強化・v42ベース版


def decide(game_state: dict, analysis: dict) -> dict:
    """振り子パターン回避・マージボーナス動的化版。v128のシンプル構造を維持しつつつ、analysis情報を活用して動的な評価を導入。"""

    results = analysis.get("results", [])

    if not results:
        return {"x": 0.0, "reason": "no analysis data"}

    best_x = 0.0
    best_score = -float("inf")
    best_reason = ""

    # 盤面情報
    pieces = game_state.get("pieces", [])
    max_y = max([p["y"] for p in pieces]) if pieces else -4.0

    # reactor情報（v333: 新規活用）
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

    # フェーズ判定（v333: v128の閾値0.8/1.8/3.0を維持）
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
        height_mult = 1.8
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

    # マージ期待値計算（v333: 新規導入）
    # 盤面のtype N-1のピース数をカウント
    target_type = next_type - 1 if next_type > 1 else None
    merge_expectation = 0.0
    if target_type:
        target_pieces = [p for p in pieces if p["type"] == target_type]
        # type N-1が2個以上あれば、マージしてtype Nになる確率が高い
        if len(target_pieces) >= 2:
            # ペア間の距離が近いほどマージ確率が高い
            min_pair_dist = 3.0
            for i in range(len(target_pieces)):
                for j in range(i + 1, len(target_pieces)):
                    dist = abs(target_pieces[i]["x"] - target_pieces[j]["x"])
                    min_pair_dist = min(min_pair_dist, dist)
            # 距離に応じた期待値（近いほど高い）
            if min_pair_dist < 0.5:
                merge_expectation = 300.0
            elif min_pair_dist < 1.0:
                merge_expectation = 150.0
            elif min_pair_dist < 1.5:
                merge_expectation = 75.0
        elif len(target_pieces) == 1:
            # 1個なら、nextNextでタイプNが来たときにマージする確率
            if next_next_type == next_type + 1:
                merge_expectation = 100.0

    for result in results:
        x = result["x"]
        landing_y = result.get("landing_y", 0)
        drift_x = result.get("drift_x", 0)
        drift_unc = result.get("drift_unc", 0)
        merge_grade = result.get("merge_grade", "NO")

        score = 0.0
        reasons = []

        # === v333: 振り子パターン回避・マージボーナス動的化 ===

        # 1. マージグレードによるスコア（v333: v128の値を維持しつつ、動的調整）
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

        # マージ期待値ボーナス（v333: 新規導入）
        if merge_grade != "NO":
            # マージ可能なら期待値を上乗
            merge_bonus *= 1.0 + merge_expectation / 600.0
        else:
            # マージ不可でも期待値があるなら、将来マージのためにボーナス
            if merge_expectation > 0:
                merge_bonus += merge_expectation * 0.5

        score += merge_bonus

        # 2. 高度によるペナルティ（v333: v128のフェーズ感応化を維持）
        height_penalty = landing_y * 50.0 * height_mult

        # HIGH_TOWERペナルティ（v333: v128の緩和設定を維持）
        if phase == "HIGH" and landing_y > 0.5:
            height_penalty *= 1.3
            reasons.append("HIGH_TOWER")
        elif phase == "MEDIUM" and landing_y > 0.5:
            height_penalty *= 1.5
            reasons.append("MEDIUM_TOWER")
        elif landing_y > 0.0:
            reasons.append("HIGH_LAYER")

        score -= height_penalty

        # 3. ドリフトによるペナルティ（v333: v128の一律30.0を維持）
        drift_penalty = (abs(drift_x) + drift_unc) * 30.0
        score -= drift_penalty

        # 4. 左右バランス補正（v333: v128のフェーズ感応化を維持）
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

        # 5. nextNextが同じタイプなら中央寄せボーナス（v333: v128の一律50.0を維持）
        if next_next_type == next_type:
            center_bonus = max(0, 1.0 - abs(x) / 2.0) * 50.0
            score += center_bonus
            reasons.append("NEXT_SAME")

        # 6. reactor情報活用ボーナス（v333: 新規導入）
        # reactive_pairsが多いほど、盤面が活発でマージが起きやすい
        if reactive_pairs >= 3:
            score += 50.0
            reasons.append("REACTIVE")
        elif reactive_pairs >= 1:
            score += 20.0

        # near_pairsが多いほど、マージ機会が多い
        if near_pairs >= 2:
            score += 30.0
            reasons.append("NEAR_PAIR")

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
