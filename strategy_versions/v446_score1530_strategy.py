#!/usr/bin/env python3
"""strategy.py - AI改善対象の決定スクリプト"""

# 固定インターフェース:
# decide(game_state: dict, analysis: dict) -> dict
#    戻り値: {"x": float, "reason": str}
#
# AI改変可能: decide() 内部、ヘルパー関数、定数、import
# AI改変禁止: decide() シグネチャ、if __name__ == "__main__" ブロック

# --- 変更履歴 ---
# v412: MEDIUMフェーズHIGH_TOWER修正・先読みマージ追加版 - v411の失敗（avg_score=987、merge_rate=11%）を受けて、v411とv128の差異を特定。v128ではMEDIUMフェーズのHIGH_TOWERペナルティが1.5倍だが、v411では1.3倍に誤って設定されていた。v128の成功要素（1.5倍）を復活。また、batch_summary.txtの分析で高スコア群（merge_rate=14.9%）と低スコア群（merge_rate=7.5%）の差がmerge_rateにあることを特定。「next/nextNextが来たときにtype N-1が2個以上あれば、その近くに落とす」先読みマージ戦略を追加し、将来のマージ連鎖を構築することで高type（type10-16）のマージを優先する。
#   根本原因の特定:
#   - v411とv128の決定的な差異: MEDIUMフェーズのHIGH_TOWERペナルティ（v411:1.3倍、v128:1.5倍）
#   - v411のコメント「HIGH_TOWERペナルティを1.3倍に維持（v128の設定）」は誤りであり、v128では1.5倍
#   - 高スコア群と低スコア群の最大の差はmerge_rate（14.9% vs 7.5%）であり、マージ機会の確保が重要
#   - type12-16はマージでのみ生成され、スコアは78-120点。type1（1点）の120倍近く価値が高い
#   - 先読み戦略がないため、next/nextNextが来たときに適切な位置にピースを落とせていない
#   改善策（MEDIUMフェーズHIGH_TOWER修正・先読みマージ追加）:
#   - MEDIUMフェーズのHIGH_TOWERペナルティを1.3倍から1.5倍に修正（v128の成功要素を正確に復活）
#   - 先読みマージボーナスを追加: next/nextNextがtype Nのとき、type N-1が2個以上あればその重心付近にボーナス
#   - 高typeほどボーナスを大きく（type1=3点、type10=30点、type15=45点）して、高typeマージを優先
#   - 距離閾値2.0以内ならボーナスを付与し、遠いほどボーナスが減少
#   - 振り子パターン回避: MEDIUMフェーズのHIGH_TOWERペナルティの「1.3倍→1.5倍→1.3倍」を解消し、v128の1.5倍で固定
#   核心的発見: v411とv128の設定がほぼ同じだが、MEDIUMフェーズのHIGH_TOWERペナルティの微小な差（1.3倍 vs 1.5倍）がスコアに大きな影響を与えている。また、先読みマージ戦略を追加することで、将来のマージ連鎖を構築し、高typeのマージを優先することが重要。
#   成功基準: merge_rate=15%以上、またはavg_scoreがv411の987を大幅に上回る
#   失敗基準: merge_rate=12.5%未満、またはscoreがv411と同等以下
# v413: MEDIUMフェーズ高度管理緩和・HIGH_TOWERペナルティ軽減版 - v412の失敗（merge_rate=11%未満、HIGH_TOWERペナルティ過剰）を受けて、batch_summary.txtの分析でMEDIUM_TOWERペナルティがHIGH_TOWERと同程度に支配的であることを特定。MEDIUMフェーズのheight_multを2.4から2.2に緩和し、HIGH_TOWERペナルティを1.5倍から1.0倍に軽減。先読みマージボーナスの重みをtype * 5に増強して高typeマージを優先。
#   根本原因の特定:
#   - v412のHIGH_TOWERペナルティ1.5倍がMEDIUMフェーズでも適用され、マージ機会を損失
#   - batch_summary.txtでMEDIUM_TOWERペナルティがHIGH_TOWERと同程度に発生している（9.7% vs 8.7%）
#   - MEDIUMフェーズのheight_mult=2.4が高すぎて、マージより高度管理が優先されている
#   - 先読みマージボーナスの重みが小さく（type * 3）、高typeマージの優先度が低い
#   改善策（MEDIUMフェーズ高度管理緩和・HIGH_TOWERペナルティ軽減）:
#   - MEDIUMフェーズのheight_multを2.4から2.2に緩和（マージ機会確保）
#   - HIGH_TOWERペナルティを1.5倍から1.0倍に軽減（MEDIUM_TOWERペナルティと統合）
#   - 先読みマージボーナスの重みをtype * 3からtype * 5に増強（高typeマージを優先）
#   - 距離閾値を2.0から1.5に縮小してより近くに配置するように
#   核心的発見: MEDIUMフェーズの高度管理が過剰でマージ機会が損失している。HIGH_TOWERペナルティもMEDIUMフェーズで適用され、マージより高度管理が優先されている。先読みマージボーナスの重みを増強することで、高typeマージの優先度を上げることが重要。
#   成功基準: merge_rate=12%以上、またはscoreがv412の1345を上回る
#   失敗基準: merge_rate=10%未満、またはscoreがv412の1345と同等以下
# v414: クラスタ密度評価付き先読みマージ強化版 - v413の失敗（score=988、v412の1345を下回る）を受けて、batch_summary.txtの分析でHIGH_LAYER_FUTURE_MERGEが最も効果的（avg_score_delta=18.7）であることを特定。v413の先読みマージボーナスはクラスタ密度を考慮しておらず、type * 5の重みも不足（type15で最大75点）。クラスタ密度評価（type N-1のピース間の平均距離の逆数）を導入し、ボーナス重みをtype * 7.5に増強。また、連鎖マージ評価（type N-2、N-3の密度も考慮）を追加し、将来のマージ連鎖の可能性を評価。密度が高いクラスタ付近を優先し、連鎖マージを促進することで、高typeのマージを加速させる。
#   根本原因の特定:
#   - v413の先読みマージボーナスは距離のみを考慮しており、クラスタ密度を無視
#   - type * 5の重みが不足（type15で75点 vs マージボーナス1200）
#   - マージ連鎖の可能性（type N-2、N-3など）を評価していない
#   - batch_summary.txtでHIGH_LAYER_FUTURE_MERGEが最も効果的
#   改善策（クラスタ密度評価付き先読みマージ強化）:
#   - クラスタ密度評価を導入: type N-1のピース間の平均距離を計算し、密度係数（1.0/平均距離）を乗算
#   - ボーナス重みをtype * 5からtype * 7.5に増強（type15で最大112.5点）
#   - 連鎖マージ評価を追加: type Nだけでなく、N-2、N-3の密度も考慮し、連鎖係数（N:1.0、N-2:0.6、N-3:0.3）を乗算
#   - v413の既存ロジック（height_mult=2.2、HIGH_TOWER=1.0倍など）を維持
#   核心的発見: 先読みマージ戦略自体は有効だが、クラスタ密度を考慮していないため、分散したピースを優先してしまいがち。クラスタ密度評価と連鎖マージ評価を追加することで、密度の高いクラスタ付近を優先し、将来の連鎖マージを促進できる。
#   成功基準: scoreがv412の1345を上回る、またはHIGH_LAYER_FUTURE_MERGEの割合が20%以上
#   失敗基準: scoreがv413の988以下、またはクラスタ密度評価の効果が見られない


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
    """v414: クラスタ密度評価付き先読みマージ強化版"""

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

    # フェーズ判定（v413: MEDIUMフェーズ高度管理緩和）
    if max_y < 0.8:
        phase = "LOW"
        height_mult = 1.0
        merge_mult = 1.2
    elif max_y < 1.8:
        phase = "MEDIUM"
        height_mult = 2.2  # v413: v412の2.4から緩和（マージ機会確保）
        merge_mult = 1.0
    elif max_y < 3.0:
        phase = "HIGH"
        # v413: v128の1.8を固定（v411の維持）
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

        # === v414: クラスタ密度評価付き先読みマージ強化 ===

        # 1. マージグレードによるスコア（v413: v42の適正な値を維持）
        if merge_grade == "DIRECT":
            score += 1200.0 * merge_mult
            reasons.append("DIRECT_MERGE")
        elif merge_grade == "NEAR":
            score += 600.0 * merge_mult
            reasons.append("NEAR_MERGE")
        elif merge_grade == "FAR":
            score += 200.0 * merge_mult
            reasons.append("FAR_MERGE")

        # 2. 高度によるペナルティ（v413: MEDIUMフェーズ高度管理緩和・HIGH_TOWERペナルティ軽減）
        height_penalty = landing_y * 50.0 * height_mult

        # HIGH_TOWERペナルティ（v413: MEDIUMフェーズも1.0倍に軽減）
        if phase == "HIGH" and landing_y > 0.5:
            height_penalty *= 1.0  # v413: v412の1.3倍から1.0倍に軽減
            reasons.append("HIGH_TOWER")
        elif phase == "MEDIUM" and landing_y > 0.5:
            height_penalty *= (
                1.0  # v413: v412の1.5倍から1.0倍に軽減（MEDIUM_TOWERと統合）
            )
            reasons.append("MEDIUM_TOWER")
        elif landing_y > 0.0:
            reasons.append("HIGH_LAYER")

        score -= height_penalty

        # 3. ドリフトによるペナルティ（v413: v128の一律30.0を維持）
        drift_penalty = (abs(drift_x) + drift_unc) * 30.0
        score -= drift_penalty

        # 4. 左右バランス補正（v413: v128の設定を維持）
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

        # 5. nextNextが同じタイプなら中央寄せボーナス（v413: v128の設定を維持）
        if next_next_type == next_type:
            center_bonus = max(0, 1.0 - abs(x) / 2.0) * 50.0
            score += center_bonus
            reasons.append("NEXT_SAME")

        # 6. 先読みマージボーナス（v414: クラスタ密度評価・連鎖マージ評価・重み増強）
        # next/nextNextがtype Nのとき、type N-1が2個以上あれば、その重心付近に落とすことで将来のマージを確保
        future_merge_bonus = 0.0
        for target_type in [next_type, next_next_type]:
            if target_type > 1 and type_count.get(target_type - 1, 0) >= 2:
                prev_type_pieces = [p for p in pieces if p["type"] == target_type - 1]
                if prev_type_pieces:
                    center_x = sum(p["x"] for p in prev_type_pieces) / len(
                        prev_type_pieces
                    )
                    distance = abs(x - center_x)

                    # v414: クラスタ密度評価
                    cluster_density = calculate_cluster_density(prev_type_pieces)

                    # v414: 連鎖マージ評価
                    chain_prob = calculate_chain_probability(
                        pieces, type_count, target_type
                    )

                    # v414: 重みtype * 7.5に増強（v413のtype * 5から）
                    # v414: 距離閾値1.5を維持、密度係数と連鎖係数を乗算
                    bonus = (
                        max(0, 1.5 - distance)
                        * target_type
                        * 7.5
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
