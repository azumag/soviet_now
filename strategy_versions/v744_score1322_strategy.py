#!/usr/bin/env python3
"""strategy.py - AI改善対象の決定スクリプト"""

# 固定インターフェース:
# decide(game_state: dict, analysis: dict) -> dict
#    戻り値: {"x": float, "reason": str}
#
# AI改変可能: decide() 内部,ヘルパー関数,定数,import
# AI改変禁止: decide() シグネチャ,if __name__ == "__main__" ブロック

# --- 変更履歴 ---
# [BEST:3689] v126: v42ベース・HIGHフェーズマージ強化版
# v135: HIGHフェーズマージ機会確保版 - v134のスコア分散（avg=826.5, min=319, max=1834）を受けて、batch_summary分析でHIGHフェーズでのマージ機会不足を特定。HIGH_TOWER_REACTOR_PROTECTのavg_score_delta=2.2と低いが、NEAR_MERGEのavg_score_delta=28.0と高価値。v134のHIGHフェーズheight_mult=1.9ではマージ機会が不足しており、ベスト戦略v128（2346点）のHIGHフェーズ高度管理（height_mult=1.8）を取り入れることで改善。v134のMEDIUMフェーズ緩和（2.2）を維持しつつ、HIGHフェーズheight_multを1.9→1.8に戻し、HIGH_TOWERペナルティ1.3倍を維持。v128の成功構造をベースにしつつ、v134のMEDIUMフェーズ緩和を組み合わせることで、HIGHフェーズでのマージ率向上と高度管理のバランス改善。コード量維持（約110行）。
# v136: MEDIUMフェーズ高度管理強化・スコア安定版 - v135のスコア分散（avg=863.5, min=647, max=1017, stddev=136.6）を受けて、batch_summary分析でMEDIUMフェーズheight_mult=2.2の緩和がスコア分散を助長していることを特定。高スコア群（avg=983）vs 低スコア群（avg=744）で239点差があり、安定性が不足。v42/v128の成功構造（MEDIUMフェーズheight_mult=2.4）を採用し、HIGHフェーズへの移行期での高度管理を強化することで、スコア安定性を向上させる。v135のHIGHフェーズ設定（height_mult=1.8、HIGH_TOWERペナルティ1.3倍）を維持しつつ、MEDIUMフェーズをv42の成功値に戻すことで、v128の成功構造に近づき、スコア分散を抑制しつつ平均スコアを向上。単一パラメータ調整で振り子パターン回避。コード量維持（約110行）。
# v137: HIGHフェーズv42復帰・盤面密度評価追加版 - v136のスコア分散（avg=1248.8, stddev=512.8）とワーストゲームの早期終了（51ターン579点、CRITICALフェーズy=3.167）を受けて、v42のHIGHフェーズheight_mult=2.6を復帰し、高度管理を強化。同時に盤面密度評価ロジック（density_bonus）とnext活用ロジック（proximity_bonus）を追加して、盤面の均一化とマージ機会確保を両立。v42/v128の成功構造をベースにしつつ、新しい評価基準を導入することで、HIGHフェーズでの高度管理強化とマージ機会確保を両立。コード量微増（約130行）。

# スコアテーブル: type N = N*(N+1)/2
SCORE_TABLE = {i: i * (i + 1) // 2 for i in range(1, 17)}


def decide(game_state: dict, analysis: dict) -> dict:
    """v137: HIGHフェーズheight_multをv42の2.6に復帰し、盤面密度評価とnext活用ロジックを追加"""

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
        height_mult = 2.4  # v136: v42の2.4を維持
        merge_mult = 1.0
    elif max_y < 3.0:
        phase = "HIGH"
        height_mult = 2.6  # v137: v42の2.6に復帰（HIGHフェーズ高度管理強化）
        merge_mult = 1.0
    else:
        phase = "CRITICAL"
        height_mult = 1.0  # CRITICAL: height_multなし
        merge_mult = 0.6  # v128: v42の0.6を維持

    # Reactor状態: 連鎖中は着地位置を低く保つ
    reactor = analysis.get("reactor", {})
    reactive_pairs = reactor.get("reactive_pairs", [])
    if isinstance(reactive_pairs, list):
        reactor_penalty_scale = len(reactive_pairs)
    else:
        reactor_penalty_scale = 0

    # 次のピース情報
    next_piece = game_state.get("next", {})
    next_next_piece = game_state.get("nextNext", {})
    next_type = next_piece.get("type", 0)
    next_next_type = next_next_piece.get("type", 0)

    # Type別マージボーナス計算: マージ結果type (next_type+1) のスコア価値に基づく
    merge_result_type = min(next_type + 1, 16)
    type_merge_bonus = SCORE_TABLE.get(merge_result_type, 10) * 10 + 300

    # === v137: 盤面密度評価計算 ===
    # 左右のピース数をカウント（盤面密度評価用）
    left_count = sum(1 for p in pieces if p["x"] < 0)
    right_count = len(pieces) - left_count
    
    # 均衡ボーナス: 左右のバランスが良いほど高い
    total_pieces = len(pieces) if pieces else 1
    density_diff = abs(left_count - right_count)
    density_bonus = max(0, 20.0 - density_diff * 2.0)  # バランスが良い時最大20.0ポイント

    # === v137: next活用ロジック ===
    # nextタイプと同じタイプのピースの位置を探す（近接ボーナス計算用）
    same_type_positions = []
    for p in pieces:
        if p["type"] == next_type:
            same_type_positions.append((p["x"], p["y"]))
    
    # 盤面の最低Yを計算（低い場所ボーナス計算用）
    min_y = min([p["y"] for p in pieces]) if pieces else -4.0

    for result in results:
        x = result["x"]
        landing_y = result.get("landing_y", 0)
        drift_x = result.get("drift_x", 0)
        drift_unc = result.get("drift_unc", 0)
        merge_grade = result.get("merge_grade", "NO")

        score = 0.0
        reasons = []

        # === v131: v126ベース + NEAR_MERGEボーナス強化 ===

        # 1. Type別マージボーナス (高Typeマージほど高ボーナス)
        if merge_grade == "DIRECT":
            score += type_merge_bonus * merge_mult
            reasons.append("DIRECT_MERGE")
        elif merge_grade == "NEAR":
            score += type_merge_bonus * 0.5 * merge_mult
            reasons.append("NEAR_MERGE")
        elif merge_grade == "FAR":
            score += type_merge_bonus * 0.17 * merge_mult
            reasons.append("FAR_MERGE")

        # 2. 高度によるペナルティ
        height_penalty = landing_y * 50.0 * height_mult

        if phase == "HIGH" and landing_y > 0.5:
            height_penalty *= 2.0  # v137: v42の2.0倍に復帰（HIGHフェーズ高度管理強化）
            reasons.append("HIGH_TOWER")
        elif phase == "MEDIUM" and landing_y > 0.5:
            height_penalty *= 1.5
            reasons.append("MEDIUM_TOWER")
        elif landing_y > 0.0:
            reasons.append("HIGH_LAYER")

        score -= height_penalty

        # 3. Reactor保護: 連鎖進行中は高い位置への着地をさらにペナルティ
        if reactor_penalty_scale > 0 and landing_y > 0.0:
            score -= landing_y * 20.0 * reactor_penalty_scale
            if "REACTOR_PROTECT" not in reasons:
                reasons.append("REACTOR_PROTECT")

        # 4. ドリフトによるペナルティ
        drift_penalty = (abs(drift_x) + drift_unc) * 30.0
        score -= drift_penalty

        # 5. 左右バランス補正
        balance_strength = 20.0
        if phase == "HIGH":
            balance_strength = 40.0
        elif phase == "MEDIUM":
            balance_strength = 30.0

        balance_bias = (right_count - left_count) / total_pieces
        balance_penalty = x * balance_bias * balance_strength
        score -= abs(balance_penalty)

        # 6. nextNextが同じタイプなら中央寄せボーナス
        if next_next_type == next_type:
            center_bonus = max(0, 1.0 - abs(x) / 2.0) * 50.0
            score += center_bonus
            reasons.append("NEXT_SAME")

        # === v137: 新しい評価基準 ===

        # 7. 均衡ボーナス（盤面のバランスが良いほど高い）
        score += density_bonus
        if density_bonus > 10.0:
            reasons.append("DENSITY_BONUS")

        # 8. 近接ボーナス（nextタイプと同じタイプが近くにある場所にボーナス）
        proximity_bonus = 0.0
        for sx, sy in same_type_positions:
            dx = abs(x - sx)
            dy = abs(landing_y - sy)
            dist = (dx ** 2 + dy ** 2) ** 0.5
            if dist < 1.5:  # 近距離
                proximity_bonus = 50.0
                break
            elif dist < 2.5:  # 中距離
                proximity_bonus = 30.0
                break
            elif dist < 3.5:  # 遠距離
                proximity_bonus = 10.0
        
        score += proximity_bonus
        if proximity_bonus > 0:
            reasons.append("PROXIMITY_BONUS")

        # 9. 低い場所ボーナス（盤面の低い場所に落とすとボーナス）
        low_spot_bonus = 0.0
        if landing_y < min_y + 1.0:  # 最低位置付近
            low_spot_bonus = 30.0
        elif landing_y < min_y + 2.0:  # 低いエリア
            low_spot_bonus = 15.0
        
        score += low_spot_bonus
        if low_spot_bonus > 0:
            reasons.append("LOW_SPOT_BONUS")

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
