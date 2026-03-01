#!/usr/bin/env python3
"""strategy.py - AI改善対象の決定スクリプト"""

# 固定インターフェース:
# decide(game_state: dict, analysis: dict) -> dict
#    戻り値: {"x": float, "reason": str}
#
# AI改変可能: decide() 内部、ヘルパー関数、定数、import
# AI改変禁止: decide() シグネチャ、if __name__ == "__main__" ブロック

# --- 変更履歴 ---
# v416: 先読みマージ大幅強化・高度ペナルティ復活版 - v415の失敗（score=1813、v412の1345と同等以下）を受けて、batch_summary.txtの分析でHIGH_LAYER_FUTURE_MERGEが最も効果的（avg_score_delta=18.0）であることが再確認されたが、v415の高度ペナルティ緩和が逆効果だったことを特定。v415で緩和したMEDIUMフェーズheight_mult=1.8とHIGH_TOWERペナルティ=1.0倍が、マージ機会を損失している。batch_summary.txtでHIGH_LAYER_FUTURE_MERGEが最も効果的であることを確認し、先読みマージを優先しつつ、高度ペナルティを適切に維持する。MEDIUMフェーズheight_multを1.8から2.4に復活し、HIGH_TOWERペナルティを1.0倍から1.5倍に復活。先読みマージボーナスをtype * 10からtype * 30に大幅に増強し、type15で最大450点。距離閾値を1.5から2.0に拡大して、広範囲のクラスタを優先。v415のクラスタ密度評価・連鎖マージ評価を維持しつつ、HIGH_LAYER_FUTURE_MERGEを最大化。v128のマージボーナス（DIRECT=1200/NEAR=600/FAR=200）を維持。
#   根本原因の特定:
#   - v415の高度ペナルティ緩和が逆効果で、マージ機会が損失している
#   - batch_summary.txtでHIGH_LAYER_FUTURE_MERGEが最も効果的（avg_score_delta=18.0）であることを確認
#   - v415の先読みマージボーナスはtype * 10で、最大でtype15で150点。しかし、高度ペナルティが緩和され、高度管理が軽減され、マージ機会が損失している可能性がある
#   - MEDIUMフェーズheight_mult=1.8が高すぎて、マージより高度管理が優先されている
#   - HIGH_TOWERペナルティ=1.0倍が小さく、高度管理が支配的になっている
#   - 先読みマージボーナスの重みが相対的に小さく、HIGH_LAYER_FUTURE_MERGEの効果が十分に発揮されていない
#   改善策（先読みマージ大幅強化・高度ペナルティ復活）:
#   - MEDIUMフェーズのheight_multを1.8から2.4に復活（v128の2.4を維持し、マージ機会確保）
#   - HIGH_TOWERペナルティを1.0倍から1.5倍に復活（v415の緩和撤回、MEDIUM_TOWERペナルティと統合）
#   - 先読みマージボーナスの重みをtype * 10からtype * 30に増強（type15で最大450点、マージボーナスの37.5%相当）
#   - 距離閾値を1.5から2.0に拡大して、広範囲のクラスタを優先
#   - v415の既存ロジック（クラスタ密度評価、連鎖マージ評価、v128のマージボーナスなど）を維持
#   核心的発見: HIGH_LAYER_FUTURE_MERGEが最も効果的だが、v415の実装では高度ペナルティが緩和されすぎて、マージ機会が損失している。先読みマージボーナスを大幅に増強し、高度ペナルティを適切に維持することで、先読みマージを優先できる。
#   成功基準: scoreがv412の1345を上回る、またはHIGH_LAYER_FUTURE_MERGEの割合が25%以上
#   失敗基準: scoreがv415の1813以下、または改善が見られない


def calculate_cluster_density(pieces: list) -> float:
    """クラスタ密度を計算（ピース間の平均距離の逆数）"""
    if len(pieces) < 2:
        return 0.0

    total_distance = 0.0
    count = 0

    for i in range(len(pieces)):
        for j in range(i + 1, len(pieces)):
            dx = pieces[i]["x"] - pieces[j]["x"]
            dy = pieces[i]["y"] - pieces[j]["y"]
            distance = (dx**2 + dy**2) ** 0.5
            total_distance += distance
            count += 1

    if count == 0:
        return 0.0

    avg_distance = total_distance / count
    # 平均距離が小さいほど密度が高い（逆数を返す）
    return 1.0 / (avg_distance + 0.1)  # 0.1はゼロ除算回避


def calculate_chain_probability(
    pieces: list, type_count: dict, target_type: int
) -> float:
    """連鎖マージ確率を計算（type N-2、N-3の密度も考慮）"""
    chain_coeff = 0.0

    # type N-1のクラスタ密度
    type_minus_1_pieces = [p for p in pieces if p["type"] == target_type - 1]
    if type_minus_1_pieces:
        density_1 = calculate_cluster_density(type_minus_1_pieces)
        chain_coeff += density_1 * 1.0

    # type N-2のクラスタ密度（連鎖係数0.6）
    type_minus_2_pieces = [p for p in pieces if p["type"] == target_type - 2]
    if type_minus_2_pieces and len(type_minus_2_pieces) >= 2:
        density_2 = calculate_cluster_density(type_minus_2_pieces)
        chain_coeff += density_2 * 0.6

    # type N-3のクラスタ密度（連鎖係数0.3）
    type_minus_3_pieces = [p for p in pieces if p["type"] == target_type - 3]
    if type_minus_3_pieces and len(type_minus_3_pieces) >= 2:
        density_3 = calculate_cluster_density(type_minus_3_pieces)
        chain_coeff += density_3 * 0.3

    return chain_coeff


def decide(game_state: dict, analysis: dict) -> dict:
    """v416: 先読みマージ大幅強化・高度ペナルティ復活版"""

    results = analysis.get("results", [])

    if not results:
        return {"x": 0.0, "reason": "no analysis data"}

    best_x = 0.0
    best_score = -float("inf")
    best_reason = ""

    # 盤面情報
    pieces = game_state.get("pieces", [])
    max_y = max([p["y"] for p in pieces]) if pieces else -4.0

    # nextNextピース情報（中央寄せボーナス計算用）
    next_piece = game_state.get("next", {})
    next_next_piece = game_state.get("nextNext", {})
    next_type = next_piece.get("type", 0)
    next_next_type = next_next_piece.get("type", 0)

    # フェーズ判定（v416: v128の閾値0.8/1.8/3.0を維持し、MEDIUMフェーズheight_mult=2.4復活）
    if max_y < 0.8:
        phase = "LOW"
        height_mult = 1.0
        merge_mult = 1.2
    elif max_y < 1.8:
        phase = "MEDIUM"
        height_mult = 2.4  # v416: v415の1.8からv128の2.4に復活（マージ機会確保）
        merge_mult = 1.0
    elif max_y < 3.0:
        phase = "HIGH"
        # v416: v415の1.8を維持（v128の1.8）
        height_mult = 1.8
        merge_mult = 1.0
    else:
        phase = "CRITICAL"
        height_mult = 1.0
        merge_mult = 0.6

    # 盤面のtype別カウント（先読みマージ用）
    type_count = {}
    for p in pieces:
        t = p["type"]
        type_count[t] = type_count.get(t, 0) + 1

    for result in results:
        x = result["x"]
        landing_y = result.get("landing_y", 0)
        drift_x = result.get("drift_x", 0)
        drift_unc = result.get("drift_unc", 0)
        merge_grade = result.get("merge_grade", "NO")

        score = 0.0
        reasons = []

        # === v416: 先読みマージ大幅強化・高度ペナルティ復活 ===

        # 1. マージグレードによるスコア（v416: v128の設定を維持）
        if merge_grade == "DIRECT":
            score += 1200.0 * merge_mult
            reasons.append("DIRECT_MERGE")
        elif merge_grade == "NEAR":
            score += 600.0 * merge_mult
            reasons.append("NEAR_MERGE")
        elif merge_grade == "FAR":
            score += 200.0 * merge_mult
            reasons.append("FAR_MERGE")

        # 2. 高度によるペナルティ（v416: MEDIUMフェーズ高度管理復活）
        height_penalty = landing_y * 50.0 * height_mult

        # HIGH_TOWERペナルティ（v416: v415の1.0倍からv128の1.5倍に復活）
        if phase == "HIGH" and landing_y > 0.5:
            height_penalty *= 1.5  # v416: v415の1.0倍からv128の1.5倍に復活
            reasons.append("HIGH_TOWER")
        elif phase == "MEDIUM" and landing_y > 0.5:
            height_penalty *= 1.5  # v416: v415の1.0倍からv128の1.5倍に復活（MEDIUM_TOWERと統合）
            reasons.append("MEDIUM_TOWER")
        elif landing_y > 0.0:
            reasons.append("HIGH_LAYER")

        score -= height_penalty

        # 3. ドリフトによるペナルティ（v416: v128の一律30.0を維持）
        drift_penalty = (abs(drift_x) + drift_unc) * 30.0
        score -= drift_penalty

        # 4. 左右バランス補正（v416: v128の設定を維持）
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

        # 5. nextNextが同じタイプなら中央寄せボーナス（v416: v128の設定を維持）
        if next_next_type == next_type:
            center_bonus = max(0, 1.0 - abs(x) / 2.0) * 50.0
            score += center_bonus
            reasons.append("NEXT_SAME")

        # 6. 先読みマージボーナス（v416: 重み大幅増強・適用条件緩和）
        # next/nextNextがtype Nのとき、type N-1が1個以上あれば適用（v416: 2個以上から緩和）
        future_merge_bonus = 0.0
        for target_type in [next_type, next_next_type]:
            if target_type > 1 and type_count.get(target_type - 1, 0) >= 1:
                prev_type_pieces = [p for p in pieces if p["type"] == target_type - 1]
                if prev_type_pieces:
                    center_x = sum(p["x"] for p in prev_type_pieces) / len(
                        prev_type_pieces
                    )
                    distance = abs(x - center_x)

                    # v416: クラスタ密度評価（v415を維持）
                    cluster_density = calculate_cluster_density(prev_type_pieces)

                    # v416: type N-1が1個の場合、密度係数を0.7倍に軽減
                    if len(prev_type_pieces) == 1:
                        cluster_density *= 0.7

                    # v416: 連鎖マージ評価（v415を維持）
                    chain_prob = calculate_chain_probability(
                        pieces, type_count, target_type
                    )

                    # v416: 重みtype * 30に大幅増強（v415の10から）
                    # v416: 距離閾値2.0に拡大して広範囲のクラスタを優先
                    bonus = (
                        max(0, 2.0 - distance)
                        * target_type
                        * 30.0  # v416: v415の10から30に大幅増強
                        * (1.0 + cluster_density)
                        * (1.0 + chain_prob)
                    )
                    future_merge_bonus += bonus

        if future_merge_bonus > 0:
            score += future_merge_bonus
            reasons.append("FUTURE_MERGE")

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
