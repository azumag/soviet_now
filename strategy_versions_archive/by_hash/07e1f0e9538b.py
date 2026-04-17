#!/usr/bin/env python3
"""strategy.py - AI改善対象の決定スクリプト"""

# 固定インターフェース:
# decide(game_state: dict, analysis: dict) -> dict
#    戻り値: {"x": float, "reason": str}
#
# AI改変可能: decide() 内部、ヘルパー関数、定数、import
# AI改変禁止: decide() シグネチャ、if __name__ == "__main__" ブロック

# --- 変更履歴 ---
# v414: クラスタ密度評価付き先読みマージ強化版 - v413の失敗（score=988、v412の1345を下回る）を受けて、batch_summary.txtの分析でHIGH_LAYER_FUTURE_MERGEが最も効果的（avg_score_delta=18.7）であることを特定。v413の先読みマージボーナスはクラスタ密度を考慮しておらず、type * 5の重みも不足（type15で最大75点）。クラスタ密度評価（type N-1のピース間の平均距離の逆数）を導入し、ボーナス重みをtype * 7.5に増強。また、連鎖マージ評価（type N-2、N-3の密度も考慮）を追加し、将来のマージ連鎖の可能性を評価。密度が高いクラスタ付近を優先し、連鎖マージを促進することで、高typeのマージを加速させる。
#   根本原因の特定:
#   - v413の先読みマージボーナスは距離のみを考慮しておらず、クラスタ密度を無視
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
# v415: 先読みマージ優先化・高度ペナルティ緩和版 - v414の失敗（score=988、v412の1345を下回る）を受けて、batch_summary.txtの分析でHIGH_LAYER_FUTURE_MERGEが最も効果的（avg_score_delta=18.7）であることが再確認されたが、v414のクラスタ密度評価は実装されているものの、ボーナスが相対的に小さく、高度ペナルティが支配的になっていることを特定。先読みマージボーナスの重みをtype * 7.5からtype * 10に増強し、MEDIUMフェーズのheight_multを2.2から1.8に緩和してマージ機会をさらに確保。また、FAR_MERGEのボーナスを200から300に増強し、FARマージも優先するように変更。さらに、先読みマージボーナスの適用条件を緩和し、type N-1が1個以上でも適用（ただし、密度係数を0.7倍に軽減）。
#   根本原因の特定:
#   - v414の先読みマージボーナスはtype * 7.5で、最大でtype15で112.5点。しかし、高度ペナルティはlanding_y * 50 * height_multで、高度2.0であれば100点以上になるため、相対的に小さい
#   - MEDIUMフェーズのheight_mult=2.2が高すぎて、マージより高度管理が優先されている
#   - FAR_MERGEのボーナス200点が小さく、FARマージが活用されていない
#   - 先読みマージボーナスはtype N-1が2個以上で適用されるが、条件が厳しすぎて適用頻度が低い
#   - batch_summary.txtでHIGH_LAYER_FUTURE_MERGEが最も効果的だが、v414ではボーナスが小さくて効果が発揮されていない
#   改善策（先読みマージ優先化・高度ペナルティ緩和）:
#   - 先読みマージボーナスの重みをtype * 7.5からtype * 10に増強（type15で最大150点）
#   - MEDIUMフェーズのheight_multを2.2から1.8に緩和（v128のHIGHフェーズと同じレベル）
#   - FAR_MERGEのボーナスを200から300に増強（FARマージを優先）
#   - 先読みマージボーナスの適用条件を緩和: type N-1が2個以上→1個以上
#   - type N-1が1個の場合、密度係数を0.7倍に軽減（密度がないため）
#   - v414の既存ロジック（クラスタ密度評価、連鎖マージ評価、HIGH_TOWER=1.0倍など）を維持
#   核心的発見: HIGH_LAYER_FUTURE_MERGEが最も効果的だが、v414の実装ではボーナスが小さくて効果が発揮されていない。先読みマージボーナスを大幅に増強し、高度ペナルティを緩和することで、先読みマージを優先できる。
#   成功基準: scoreがv412の1345を上回る、またはHIGH_LAYER_FUTURE_MERGEの割合が25%以上
#   失敗基準: scoreがv414の988以下、または改善が見られない
# v416: 高度ペナルティ大幅緩和・先読みマージ適用条件完全緩和版 - v415の失敗（score=988、v412の1345を下回る）を受けて、batch_summary.txtの分析でHIGH_LAYER_FUTURE_MERGEが最も効果的（avg_score_delta=18.7）であることが再確認されたが、v415の先読みマージボーナス（type * 10、最大150点）は高度ペナルティ（landing_y * 50 * height_mult）に比べて依然として小さいことを特定。MEDIUMフェーズのheight_mult=1.8でも、高度2.0であれば180点のペナルティになるため、先読みマージボーナスが打ち消されてしまう。さらに、type N-1が0個の場合は先読みマージボーナスが適用されないため、適用頻度が低い。高度ペナルティを大幅に緩和し、先読みマージボーナスの適用条件を完全に緩和（type N-1が0個以上で適用）することで、先読みマージをより積極的に活用する。
#   根本原因の特定:
#   - v415の先読みマージボーナスはtype * 10で、最大でtype15で150点。しかし、高度ペナルティはlanding_y * 50 * height_multで、高度2.0であれば100 * 1.8 = 180点になるため、相対的に小さい
#   - MEDIUMフェーズのheight_mult=1.8が依然として高く、マージより高度管理が優先されている
#   - HIGH_TOWERペナルティ1.0倍がMEDIUMフェーズでも適用され、マージ機会を損失
#   - type N-1が0個の場合は先読みマージボーナスが適用されないため、適用頻度が低い
#   - batch_summary.txtでHIGH_LAYER_FUTURE_MERGEが最も効果的だが、v415ではボーナスが小さくて効果が発揮されていない
#   改善策（高度ペナルティ大幅緩和・先読みマージ適用条件完全緩和）:
#   - 先読みマージボーナスの重みをtype * 10からtype * 12に増強（type15で最大180点）
#   - MEDIUMフェーズのheight_multを1.8から1.5に緩和（高度2.0で150点のペナルティ）
#   - HIGH_TOWERペナルティを1.0倍から0.8倍に軽減（高度ペナルティのさらなる緩和）
#   - 先読みマージボーナスの適用条件を完全に緩和: type N-1が1個以上→0個以上
#   - type N-1が0個の場合、密度係数を0.4倍に軽減（密度がないため）
#   - type N-1が1個の場合、密度係数を0.6倍に軽減（v415の0.7倍からさらに緩和）
#   - FAR_MERGEのボーナスを300から400に増強（FARマージをより優先）
#   - v415の既存ロジック（クラスタ密度評価、連鎖マージ評価など）を維持
#   核心的発見: 高度ペナルティが依然として支配的で、先読みマージボーナスが打ち消されている。高度ペナルティを大幅に緩和し、先読みマージボーナスの適用条件を完全に緩和することで、先読みマージをより積極的に活用できる。
#   成功基準: scoreがv412の1345を上回る、またはHIGH_LAYER_FUTURE_MERGEの割合が30%以上
#   失敗基準: scoreがv415の988以下、または改善が見られない


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
    """v416: 高度ペナルティ大幅緩和・先読みマージ適用条件完全緩和版"""

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

    # フェーズ判定（v416: 高度ペナルティ大幅緩和）
    if max_y < 0.8:
        phase = "LOW"
        height_mult = 1.0
        merge_mult = 1.2
    elif max_y < 1.8:
        phase = "MEDIUM"
        height_mult = 1.5  # v416: v415の1.8から緩和（高度2.0で150点のペナルティ）
        merge_mult = 1.0
    elif max_y < 3.0:
        phase = "HIGH"
        # v416: v415の1.8を維持
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

        # === v416: 高度ペナルティ大幅緩和・先読みマージ適用条件完全緩和 ===

        # 1. マージグレードによるスコア（v416: FAR_MERGEボーナス増強）
        if merge_grade == "DIRECT":
            score += 1200.0 * merge_mult
            reasons.append("DIRECT_MERGE")
        elif merge_grade == "NEAR":
            score += 600.0 * merge_mult
            reasons.append("NEAR_MERGE")
        elif merge_grade == "FAR":
            score += 400.0 * merge_mult  # v416: 300から400に増強（FARマージをより優先）
            reasons.append("FAR_MERGE")

        # 2. 高度によるペナルティ（v416: MEDIUMフェーズ高度管理緩和）
        height_penalty = landing_y * 50.0 * height_mult

        # HIGH_TOWERペナルティ（v416: 0.8倍に軽減）
        if phase == "HIGH" and landing_y > 0.5:
            height_penalty *= 0.8  # v416: v415の1.0倍から0.8倍に軽減
            reasons.append("HIGH_TOWER")
        elif phase == "MEDIUM" and landing_y > 0.5:
            height_penalty *= 0.8  # v416: v415の1.0倍から0.8倍に軽減
            reasons.append("MEDIUM_TOWER")
        elif landing_y > 0.0:
            reasons.append("HIGH_LAYER")

        score -= height_penalty

        # 3. ドリフトによるペナルティ（v416: v415の一律30.0を維持）
        drift_penalty = (abs(drift_x) + drift_unc) * 30.0
        score -= drift_penalty

        # 4. 左右バランス補正（v416: v415の設定を維持）
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

        # 5. nextNextが同じタイプなら中央寄せボーナス（v416: v415の設定を維持）
        if next_next_type == next_type:
            center_bonus = max(0, 1.0 - abs(x) / 2.0) * 50.0
            score += center_bonus
            reasons.append("NEXT_SAME")

        # 6. 先読みマージボーナス（v416: 重み増強・適用条件完全緩和）
        # next/nextNextがtype Nのとき、type N-1が0個以上でも適用（v416: 1個以上から緩和）
        future_merge_bonus = 0.0
        for target_type in [next_type, next_next_type]:
            # v416: type N-1が0個以上でも適用（適用条件を完全に緩和）
            if target_type > 1:
                prev_type_pieces = [p for p in pieces if p["type"] == target_type - 1]

                # v416: type N-1が0個でも適用（center_xを0として計算）
                if prev_type_pieces:
                    center_x = sum(p["x"] for p in prev_type_pieces) / len(
                        prev_type_pieces
                    )
                else:
                    center_x = 0.0

                distance = abs(x - center_x)

                # v416: クラスタ密度評価（v415を維持）
                if prev_type_pieces:
                    cluster_density = calculate_cluster_density(prev_type_pieces)
                else:
                    cluster_density = 0.0

                # v416: type N-1が0個の場合、密度係数を0.4倍に軽減
                # v416: type N-1が1個の場合、密度係数を0.6倍に軽減（v415の0.7倍からさらに緩和）
                if len(prev_type_pieces) == 0:
                    cluster_density *= 0.4
                elif len(prev_type_pieces) == 1:
                    cluster_density *= 0.6

                # v416: 連鎖マージ評価（v415を維持）
                chain_prob = calculate_chain_probability(
                    pieces, type_count, target_type
                )

                # v416: 重みtype * 12に増強（v415の10から）
                # v416: 距離閾値1.5を維持、密度係数と連鎖係数を乗算
                bonus = (
                    max(0, 1.5 - distance)
                    * target_type
                    * 12.0  # v416: v415の10から12に増強
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
