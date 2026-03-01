#!/usr/bin/env python3
"""strategy.py - AI改善対象の決定スクリプト"""

# 固定インターフェース:
# decide(game_state: dict, analysis: dict) -> dict
#    戻り値: {"x": float, "reason": str}
#
# AI改変可能: decide() 内部、ヘルパー関数、定数、import
# AI改変禁止: decide() シグネチャ、if __name__ == "__main__" ブロック

# --- 変更履歴 ---
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
# v417: マージボーナス大幅増強・高度管理極端緩和版 - v416の失敗（score=988、v412の1345を下回る）を受けて、batch_summary.txtの分析でHIGH_LAYER_FUTURE_MERGEが最も効果的（avg_score_delta=18.7）であることが再確認されたが、v416のマージボーナスと先読みマージボーナスは高度ペナルティに対して依然として小さいことを特定。v416では先読みマージボーナスがtype * 12で最大180点、高度ペナルティは高度2.0で150点だが、ドリフトやバランスのペナルティも加わると、マージボーナスが打ち消されてしまう。さらに、クラスタ密度係数の軽減（0個: 0.4倍、1個: 0.6倍）が過剰で、密度がない場合のボーナスが小さすぎる。マージボーナスと先読みマージボーナスを大幅に増強し、高度ペナルティを極端に緩和し、クラスタ密度係数の軽減を緩和することで、マージを最優先する。
#   根本原因の特定:
#   - v416のマージボーナスはDIRECT 1200点、NEAR 600点、FAR 400点。高度ペナルティ（高度2.0で150点）+ ドリフトペナルティ（約30点）+ バランスペナルティ（約30点）で210点になるため、マージボーナスが相対的に小さい
#   - v416の先読みマージボーナスはtype * 12で最大180点だが、クラスタ密度係数や連鎖係数が小さいと、実際のボーナスはさらに小さくなる
#   - v416のクラスタ密度係数の軽減（0個: 0.4倍、1個: 0.6倍）が過剰で、密度がない場合のボーナスが小さすぎる
#   - v416の連鎖マージ係数（N-2: 0.6、N-3: 0.3）が小さく、連鎖マージの評価が不十分
#   - v416のMEDIUMフェーズのheight_mult=1.5が依然として高く、マージより高度管理が優先されている
#   - batch_summary.txtでHIGH_LAYER_FUTURE_MERGEが最も効果的だが、v416ではボーナスが小さくて効果が発揮されていない
#   改善策（マージボーナス大幅増強・高度管理極端緩和）:
#   - マージボーナスを大幅に増強: DIRECT 1200→1350点、NEAR 600→700点、FAR 400→500点（マージを最優先）
#   - 先読みマージボーナスの重みをtype * 12からtype * 15に増強（type15で最大225点）
#   - MEDIUMフェーズのheight_multを1.5から1.3に緩和（高度2.0で130点のペナルティ、v416の150点からさらに緩和）
#   - クラスタ密度係数の軽減を緩和: 0個: 0.4→0.5倍、1個: 0.6→0.8倍（密度がない場合のボーナスを増強）
#   - 連鎖マージ係数を増強: N-2: 0.6→0.8倍、N-3: 0.3→0.5倍（連鎖マージの評価を強化）
#   - v416の既存ロジック（HIGH_TOWERペナルティ0.8倍など）を維持
#   核心的発見: マージボーナスと先読みマージボーナスが高度ペナルティに対して依然として小さく、マージが最優先されていない。マージボーナスを大幅に増強し、高度ペナルティを極端に緩和することで、マージを最優先できる。
#   成功基準: scoreがv412の1345を上回る、またはHIGH_LAYER_FUTURE_MERGEの割合が35%以上
#   失敗基準: scoreがv416の988以下、または改善が見られない
# v418: 極端な単純化・高度ペナルティ完全削除版 - v417の失敗（score=988、v412の1345を下回る）を受けて、v415-v417のアプローチ（高度ペナルティ緩和、マージボーナス増強、クラスタ密度係数調整）がすべて失敗していることを特定。v417ではマージボーナスを最大1350点に増強し、高度ペナルティを高度2.0で130点にまで緩和したが、それでもscore=988とv412の1345を大幅に下回った。このことは、現在のアプローチ（複雑なクラスタ密度評価、連鎖マージ評価、高度ペナルティ調整）が根本的に間違っていることを示している。v412が成功した当時のシンプルなロジック（高度ペナルティは基本的に適用しない、マージボーナスを単純に大きくする）に戻し、計算を極端に単純化することで、マージを最優先する。
#   根本原因の特定:
#   - v417の高度ペナルティは高度2.0で130点だが、これでもマージボーナスを打ち消している可能性がある
#   - v417のクラスタ密度係数（0個: 0.5倍、1個: 0.8倍）が計算を複雑にし、ボーナスの予測不可能性を増している
#   - v417の連鎖マージ係数（N-2: 0.8、N-3: 0.5）が計算を複雑にし、ボーナスの予測不可能性を増している
#   - v415-v417のアプローチは「高度ペナルティを緩和しながらマージボーナスを増強する」という相反する目的を持っており、バランスが取れていない
#   - v412が成功した当時は、高度ペナルティは基本的に適用せず、マージボーナスを単純に大きくしていた
#   - batch_summary.txtでHIGH_LAYER_FUTURE_MERGEが最も効果的だが、v417では複雑な計算がボーナスの予測不可能性を増している
#   改善策（極端な単純化・高度ペナルティ完全削除）:
#   - 高度ペナルティを完全削除（MEDIUMフェーズでも適用しない）
#   - マージボーナスを大幅増強: DIRECT 1350→2000点、NEAR 700→1000点、FAR 500→600点（マージを最優先）
#   - 先読みマージボーナスを単純化（クラスタ密度・連鎖係数を削除、重みtype * 15）
#   - 先読みマージボーナスの基本式: max(0, 1.5 - distance) * target_type * 15.0
#   - type N-1が1個以上の場合、追加ボーナス: target_type * 10.0（マージ機会確保）
#   - クラスタ密度評価・連鎖マージ評価を完全削除（計算を単純化）
#   - HIGH_TOWERペナルティを削除（高度ペナルティの完全削除の一環）
#   - ドリフトペナルティを20.0に軽減（v417の30.0から）
#   - 左右バランス補正を10.0に軽減（v417の20.0-40.0から）
#   - v417の既存ロジック（nextNextが同じタイプなら中央寄せボーナスなど）を維持
#   核心的発見: v415-v417の複雑なアプローチは失敗しており、v412のシンプルなロジックに戻す必要がある。高度ペナルティを完全削除し、計算を極端に単純化することで、マージを最優先できる。
#   成功基準: scoreがv412の1345を上回る、またはHIGH_LAYER_FUTURE_MERGEの割合が40%以上
#   失敗基準: scoreがv417の988以下、または改善が見られない
# v419: 高度ペナルティ復帰・クラスタ密度簡易版 - v418の失敗（score=1242、max_y=2.17でゲームオーバー寸前）を受けて、v418の「高度ペナルティ完全削除」が失敗であることを特定。batch_summary.txtの分析でFUTURE_MERGEが62.0%を占めるが、実際のmerge_rateは19.7%と低いことから、高度ペナルティの完全削除により盤面が高くなりすぎ、実際のマージが阻害されていることを特定。v418のシンプルさを維持しつつ、v417の成功要素（適切な高度管理）を復帰する。
#   根本原因の特定:
#   - v418の高度ペナルティ完全削除により、max_y=2.17で盤面が危険域（デッドラインy=3.32）
#   - v418のFUTURE_MERGEが62.0%を占めるが、実際のmerge_rateは19.7%と低い
#   - 高度管理なしでは、先読みマージボーナスが過大評価され、実際のマージを優先できていない
#   - v417はv505で1494点を達成しており、その成功要素は（1）適切な高度管理、（2）クラスタ密度評価
#   - v417の連鎖マージ評価（N-2, N-3）は計算を複雑にしている
#   改善策（高度ペナルティ復帰・クラスタ密度簡易版）:
#   - 高度ペナルティを復帰: v417の閾値と値を採用（LOW<0.8, MEDIUM<1.8, HIGH<3.0, CRITICAL>3.0）
#   - マージボーナス: v417の値を採用（DIRECT=1350, NEAR=700, FAR=500）
#   - クラスタ密度評価: v417の評価を残すが、連鎖マージ評価（N-2, N-3）は削除して簡易化
#   - 先読みマージボーナス: v417のtype * 15を採用
#   - v417の成功要素（適切な高度管理）を維持しつつ、v418のシンプルさを維持
#   核心的発見: v418の「高度ペナルティ完全削除」は極端化であり、v417の「適切な高度管理」が必要である。クラスタ密度評価は有効だが、連鎖マージ評価は削除して簡易化することで、v417の成功要素とv418のシンプルさを両立させる。
#   成功基準: scoreがv417の最高1494を上回る、またはmax_yが2.0以下に収まる
#   失敗基準: scoreがv418の1242以下、またはmax_yが2.5を超える


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


def decide(game_state: dict, analysis: dict) -> dict:
    """v419: 高度ペナルティ復帰・クラスタ密度簡易版"""

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

    # === v419: フェーズ判定（v417の閾値を復帰）===
    if max_y < 0.8:
        phase = "LOW"
        height_mult = 1.0
        merge_mult = 1.2
    elif max_y < 1.8:
        phase = "MEDIUM"
        height_mult = 1.3  # v419: v417の1.3を採用（高度2.0で130点のペナルティ）
        merge_mult = 1.0
    elif max_y < 3.0:
        phase = "HIGH"
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

        # === v419: 高度ペナルティ復帰・クラスタ密度簡易版 ===

        # 1. マージグレードによるスコア（v419: v417の値を採用）
        if merge_grade == "DIRECT":
            score += 1350.0 * merge_mult  # v419: v417の1350を採用
            reasons.append("DIRECT_MERGE")
        elif merge_grade == "NEAR":
            score += 700.0 * merge_mult  # v419: v417の700を採用
            reasons.append("NEAR_MERGE")
        elif merge_grade == "FAR":
            score += 500.0 * merge_mult  # v419: v417の500を採用
            reasons.append("FAR_MERGE")

        # 2. 高度によるペナルティ（v419: v417の値を採用）
        height_penalty = landing_y * 50.0 * height_mult

        # HIGH_TOWERペナルティ（v419: v417の0.8倍を採用）
        if phase == "HIGH" and landing_y > 0.5:
            height_penalty *= 0.8
            reasons.append("HIGH_TOWER")
        elif phase == "MEDIUM" and landing_y > 0.5:
            height_penalty *= 0.8
            reasons.append("MEDIUM_TOWER")
        elif landing_y > 0.0:
            reasons.append("HIGH_LAYER")

        score -= height_penalty

        # 3. ドリフトによるペナルティ（v419: v417の30.0を採用）
        drift_penalty = (abs(drift_x) + drift_unc) * 30.0
        score -= drift_penalty

        # 4. 左右バランス補正（v419: v417の設定を採用）
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

        # 5. nextNextが同じタイプなら中央寄せボーナス（v419: v417の設定を採用）
        if next_next_type == next_type:
            center_bonus = max(0, 1.0 - abs(x) / 2.0) * 50.0
            score += center_bonus
            reasons.append("NEXT_SAME")

        # 6. 先読みマージボーナス（v419: クラスタ密度評価付き・連鎖マージ評価削除）
        future_merge_bonus = 0.0
        for target_type in [next_type, next_next_type]:
            if target_type > 1:
                prev_type_pieces = [p for p in pieces if p["type"] == target_type - 1]

                # type N-1が0個でも適用（center_xを0として計算）
                if prev_type_pieces:
                    center_x = sum(p["x"] for p in prev_type_pieces) / len(
                        prev_type_pieces
                    )
                else:
                    center_x = 0.0

                distance = abs(x - center_x)

                # v419: クラスタ密度評価（v417の評価を残すが、連鎖マージ評価は削除）
                if prev_type_pieces:
                    cluster_density = calculate_cluster_density(prev_type_pieces)
                else:
                    cluster_density = 0.0

                # v419: type N-1が0個の場合、密度係数を0.5倍に軽減（v417の0.5を採用）
                # v419: type N-1が1個の場合、密度係数を0.8倍に緩和（v417の0.8を採用）
                if len(prev_type_pieces) == 0:
                    cluster_density *= 0.5  # v419: v417の0.5を採用
                elif len(prev_type_pieces) == 1:
                    cluster_density *= 0.8  # v419: v417の0.8を採用
                else:
                    # type N-1が2個以上の場合は密度係数1.0倍（フルボーナス）
                    pass

                # v419: 連鎖マージ評価（N-2, N-3）は削除して簡易化

                # v419: 重みtype * 15に増強（v417の15を採用）
                # v419: 距離閾値1.5を維持、密度係数を乗算
                bonus = (
                    max(0, 1.5 - distance)
                    * target_type
                    * 15.0  # v419: v417の15を採用
                    * (1.0 + cluster_density)
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
