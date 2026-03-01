#!/usr/bin/env python3
"""strategy.py - AI改善対象の決定スクリプト"""

# 固定インターフェース:
# decide(game_state: dict, analysis: dict) -> dict
#    戻り値: {"x": float, "reason": str}
#
# AI改変可能: decide() 内部、ヘルパー関数、定数、import
# AI改変禁止: decide() シグネチャ、if __name__ == "__main__" ブロック

# --- 変更履歴 ---
# v419: マージ絶対優先・超単純化版 - v418の失敗（scoreがv412の1345を大幅に下回る）を受けて、v418の単純化アプローチをさらに進め、マージを絶対優先する。v418では高度ペナルティを完全削除し、マージボーナスをDIRECT 2000点、NEAR 1000点、FAR 600点に増強したが、それでも失敗した。これは、先読みマージボーナスの距離係数(1.5 - distance)が厳しすぎて、広範囲でのマージ機会を見逃しているためと考えられる。また、ドリフトペナルティや左右バランス補正が依然としてマージの邪魔をしている可能性がある。マージボーナスをさらに増強し、先読みマージボーナスの距離係数を緩和し、ドリフトペナルティを最小限に抑えることで、マージを絶対優先する。
#   根本原因の特定:
#   - v418の先読みマージボーナスの距離係数は max(0, 1.5 - distance) で、distanceが1.5以上だとボーナスが0になる。これは広範囲でのマージ機会を見逃している
#   - v418のドリフトペナルティは 20.0 で、ドリフトが1.0あれば20点のペナルティになる。これはマージボーナスを打ち消す可能性がある
#   - v418の左右バランス補正は 10.0 で、バランスが偏っているとペナルティになる。これはマージの邪魔をしている可能性がある
#   - v418のマージボーナスは DIRECT 2000点だが、他のペナルティの影響でマージが選択されない可能性がある
#   - batch_summary.txtでHIGH_LAYER_FUTURE_MERGEが最も効果的だが、v418では距離係数が厳しすぎて効果が発揮されていない
#   改善策（マージ絶対優先・超単純化）:
#   - マージボーナスを超増強: DIRECT 2000→2500点、NEAR 1000→1500点、FAR 600→800点（マージを絶対優先）
#   - 先読みマージボーナスの距離係数を緩和: max(0, 2.0 - distance) に変更（v418の1.5から2.0へ、広範囲で適用）
#   - ドリフトペナルティを最小限: 20.0→10.0 に軽減（v418からさらに軽減）
#   - 左右バランス補正を削除: 完全に削除してマージの邪魔をしない
#   - nextNext中央寄せボーナスを削除: マージより優先しないようにする
#   - v418の既存ロジック（type N-1追加ボーナスなど）を維持
#   核心的発見: v418の単純化アプローチは正しいが、距離係数が厳しすぎて広範囲でのマージ機会を見逃している。距離係数を緩和し、ドリフトペナルティを最小限に抑えることで、マージを絶対優先できる。
#   成功基準: scoreがv412の1345を上回る、またはHIGH_LAYER_FUTURE_MERGEの割合が45%以上
#   失敗基準: scoreがv418の988以下、または改善が見られない
# v421: HIGHフェーズマージ強化・マージチェーン予測追加版 - v420のbatch_summary.txtでFUTURE_MERGEが62.7%を占めるが、実際のマージ（DIRECT/NEAR/FAR）はわずか4.5%であることが確認された。v420の先読みボーナスが支配的すぎて実際のマージを見逃している問題を特定。v420のbatch_summary.txtでNEAR_MERGE_FUTURE_MERGEが32.8%を占め、この組み合わせが最も効果的（avg_score_delta=14.1）であることが確認された。改善策：（1）HIGHフェーズでのマージ確保を強化：HIGH_TOWERペナルティをv128の1.3倍から1.0倍に緩和（高度管理とマージのバランス調整）、（2）マージチェーン予測：type N-2ペアの存在確認を追加し、連鎖マージの可能性を評価、（3）reactor情報の活用：near_pairsとreactive_pairsをスコアリングに追加し、盤面のマージ環境を評価、（4）マージグレードボーナスのフェーズ別調整：MEDIUMフェーズでmerge_multを1.0から1.1に増強し、中盤でのマージを促進。v420の成功構造（フェーズ判定、マージグレードボーナス、高度ペナルティ、バランス補正、nextNext中央寄せボーナス、先読みマージボーナス）を維持しつつ、HIGHフェーズでのマージ機会損失とマージチェーンの欠如を改善。
#   根本原因の特定:
#   - v420の先読みボーナスが支配的すぎて、実際のマージ（DIRECT/NEAR/FAR）の評価が不十分
#   - v420のHIGH_TOWERペナルティが1.3倍と強すぎて、HIGHフェーズでのマージ選択が制限されている
#   - マージチェーン予測が不足しており、連鎖マージの可能性が評価されていない
#   - reactor情報（near_pairs、reactive_pairs）が活用されていない
#   改善策（HIGHフェーズマージ強化・マージチェーン予測追加）:
#   - HIGH_TOWERペナルティを1.3倍から1.0倍に緩和（高度管理とマージのバランス調整）
#   - マージチェーン予測を追加：type N-2ペアの存在確認、連鎖マージの可能性評価
#   - reactor情報の活用：near_pairsとreactive_pairsをスコアリングに追加
#   - MEDIUMフェーズでmerge_multを1.0から1.1に増強（中盤でのマージを促進）
#   - v420の既存ロジック（フェーズ判定、マージグレードボーナス、高度ペナルティ、バランス補正、nextNext中央寄せボーナス、先読みマージボーナス）を維持
#   核心的発見: v420の先読みボーナスが支配的すぎて実際のマージを見逃している。HIGH_TOWERペナルティを緩和し、マージチェーン予測とreactor情報を追加することで、実際のマージと連鎖マージの両方を評価できる。
#   成功基準: scoreがv412の1345を上回る、または実際のマージ率（DIRECT/NEAR/FAR）が20%以上
#   失敗基準: scoreがv420の1156以下、または実際のマージ率が15%以下
# v422: v421ベース・v419のマージ強化追加版 - v421の成功（v412の1345を上回る）をベースに、v419のマージ絶対優先の改善点（マージボーナス増強、ドリフトペナルティ軽減）を追加する。v421ではフェーズ判定、高度ペナルティ、バランス補正、nextNext中央寄せボーナス、先読みマージボーナス、マージチェーン予測、reactor情報を活用した複雑なロジックで成功しているが、実際のマージ率（DIRECT/NEAR/FAR）は依然として低い可能性がある。v419の改善点をv421に追加することで、実際のマージをさらに強化し、より高いスコアを目指す。
#   根本原因の特定:
#   - v421のマージボーナスは DIRECT 1200点、NEAR 600点、FAR 200点だが、v419の2500/1500/800点に比べて小さい
#   - v421のドリフトペナルティは 30.0 だが、v419の10.0に比べて重い
#   - v421の先読みマージボーナスは type * 10.0 だが、v419のtype * 15.0に比べて小さい
#   - v421の距離係数は max(0, 3.0 - distance) だが、v419の2.0に比べて広範囲すぎる可能性がある
#   改善策（v421ベース・v419のマージ強化追加）:
#   - マージボーナスをv419レベルに増強: DIRECT 1200→2500点、NEAR 600→1500点、FAR 200→800点
#   - ドリフトペナルティをv419レベルに軽減: 30.0→10.0
#   - 先読みマージボーナスの重みを増強: type * 10.0→type * 15.0
#   - 先読みマージボーナスの距離係数を緩和: max(0, 3.0 - distance)→max(0, 2.0 - distance)
#   - v421の既存ロジック（フェーズ判定、高度ペナルティ、バランス補正、nextNext中央寄せボーナス、マージチェーン予測、reactor情報）を維持
#   核心的発見: v421の複雑なロジックは成功しているが、マージボーナスが小さくドリフトペナルティが重い。v419のマージ絶対優先の改善点を追加することで、実際のマージをさらに強化できる。
#   成功基準: scoreがv421を上回る、または実際のマージ率（DIRECT/NEAR/FAR）が25%以上
#   失敗基準: scoreがv421以下、または実際のマージ率が15%以下


def decide(game_state: dict, analysis: dict) -> dict:
    """v422: v421ベース・v419のマージ強化追加版"""

    results = analysis.get("results", [])

    if not results:
        return {"x": 0.0, "reason": "no analysis data"}

    best_x = 0.0
    best_score = -float("inf")
    best_reason = ""

    # 盤面情報
    pieces = game_state.get("pieces", [])
    max_y = max([p["y"] for p in pieces]) if pieces else -4.0

    # フェーズ判定（v422: v421の設定を維持）
    if max_y < 0.8:
        phase = "LOW"
        height_mult = 1.0
        merge_mult = 1.2
    elif max_y < 1.8:
        phase = "MEDIUM"
        height_mult = 2.4  # v422: v421の2.4を維持
        merge_mult = 1.1  # v422: v421の1.1を維持
    elif max_y < 3.0:
        phase = "HIGH"
        height_mult = 1.8  # v422: v421の1.8を維持
        merge_mult = 1.0
    else:
        phase = "CRITICAL"
        height_mult = 1.0  # CRITICAL: height_multなし
        merge_mult = 0.6  # v422: v421の0.6を維持

    # nextNextピース情報
    next_piece = game_state.get("next", {})
    next_next_piece = game_state.get("nextNext", {})
    next_type = next_piece.get("type", 0)
    next_next_type = next_next_piece.get("type", 0)

    # reactor情報の活用（v422: v421のnear_pairs、reactive_pairsを維持）
    reactor = analysis.get("reactor", {})
    near_pairs = reactor.get("near_pairs", [])
    reactive_pairs = reactor.get("reactive_pairs", 0)

    for result in results:
        x = result["x"]
        landing_y = result.get("landing_y", 0)
        drift_x = result.get("drift_x", 0)
        drift_unc = result.get("drift_unc", 0)
        merge_grade = result.get("merge_grade", "NO")

        score = 0.0
        reasons = []

        # === v422: v421ベース・v419のマージ強化追加 ===

        # 1. マージグレードによるスコア（v422: v419の超増強値を採用）
        if merge_grade == "DIRECT":
            score += 2500.0 * merge_mult  # v422: v421の1200→v419の2500に増強
            reasons.append("DIRECT_MERGE")
        elif merge_grade == "NEAR":
            score += 1500.0 * merge_mult  # v422: v421の600→v419の1500に増強
            reasons.append("NEAR_MERGE")
        elif merge_grade == "FAR":
            score += 800.0 * merge_mult  # v422: v421の200→v419の800に増強
            reasons.append("FAR_MERGE")

        # 2. 高度によるペナルティ（v422: v421の設定を維持）
        height_penalty = landing_y * 50.0 * height_mult

        # HIGH_TOWERペナルティ（v422: v421の1.0倍を維持）
        if phase == "HIGH" and landing_y > 0.5:
            height_penalty *= 1.0  # v422: v421の1.0倍を維持
            reasons.append("HIGH_TOWER")
        elif phase == "MEDIUM" and landing_y > 0.5:
            height_penalty *= 1.5  # v422: v421の1.5倍を維持
            reasons.append("MEDIUM_TOWER")
        elif landing_y > 0.0:
            reasons.append("HIGH_LAYER")

        score -= height_penalty

        # 3. ドリフトによるペナルティ（v422: v419の軽減値を採用）
        drift_penalty = (abs(drift_x) + drift_unc) * 10.0  # v422: v421の30.0→v419の10.0に軽減
        score -= drift_penalty

        # 4. 左右バランス補正（v422: v421の設定を維持）
        balance_strength = 20.0
        if phase == "HIGH":
            balance_strength = 40.0  # v422: v421の40.0を維持
        elif phase == "MEDIUM":
            balance_strength = 30.0  # v422: v421の30.0を維持

        left_count = sum(1 for p in pieces if p["x"] < 0)
        right_count = len(pieces) - left_count
        balance_bias = (right_count - left_count) / (len(pieces) if pieces else 1)

        balance_penalty = x * balance_bias * balance_strength
        score -= abs(balance_penalty)

        # 5. nextNextが同じタイプなら中央寄せボーナス（v422: v421の設定を維持）
        if next_next_type == next_type:
            center_bonus = max(0, 1.0 - abs(x) / 2.0) * 50.0
            score += center_bonus
            reasons.append("NEXT_SAME")

        # 6. 先読みマージボーナス（v422: v419の増強値を採用）
        future_merge_bonus = 0.0
        for target_type in [next_type, next_next_type]:
            if target_type > 1:
                prev_type_pieces = [p for p in pieces if p["type"] == target_type - 1]

                # v422: type N-1が0個の場合はボーナスを付与しない（v421を維持）
                if len(prev_type_pieces) >= 1:
                    center_x = sum(p["x"] for p in prev_type_pieces) / len(
                        prev_type_pieces
                    )
                    distance = abs(x - center_x)

                    # v422: v421の3.0からv419の2.0に緩和し、重みをtype * 10.0→type * 15.0に増強
                    bonus = (
                        max(0, 2.0 - distance) * target_type * 15.0
                    )  # v422: v421のtype * 10.0→v419のtype * 15.0に増強
                    future_merge_bonus += bonus

                    # v422: type N-1が1個以上の場合、追加ボーナス（v421のtype * 5.0→v419のtype * 10.0に増強）
                    future_merge_bonus += target_type * 10.0

        if future_merge_bonus > 0:
            score += future_merge_bonus
            reasons.append("FUTURE_MERGE")

        # 7. マージチェーン予測（v422: v421の設定を維持）
        chain_bonus = 0.0
        for target_type in [next_type, next_next_type]:
            if target_type > 2:
                prev2_type_pieces = [p for p in pieces if p["type"] == target_type - 2]

                # v422: type N-2が1個以上の場合、連鎖マージの可能性を評価
                if len(prev2_type_pieces) >= 1:
                    # type N-2ペアがあれば、type N-1ペアがマージしてtype Nが生まれる可能性がある
                    # そのtype Nが既に盤面にあるなら、連鎖マージの可能性がある
                    prev1_type_pieces = [
                        p for p in pieces if p["type"] == target_type - 1
                    ]

                    # type N-1ペアが存在し、かつtype Nが盤面にあれば、連鎖マージの可能性がある
                    if len(prev1_type_pieces) >= 2:
                        chain_bonus += target_type * 15.0  # 連鎖マージの可能性ボーナス

        if chain_bonus > 0:
            score += chain_bonus
            reasons.append("CHAIN_MERGE")

        # 8. reactor情報の活用（v422: v421のnear_pairs、reactive_pairsを維持）
        # v422: near_pairsの数に応じてボーナスを付与（マージ環境が良い場合）
        near_pairs_bonus = min(len(near_pairs), 5) * 20.0  # 最多5ペアまで
        score += near_pairs_bonus
        reasons.append("NEAR_PAIRS")

        # v422: reactive_pairsに応じてボーナスを付与（反応器が活性化している場合）
        reactive_pairs_count = len(reactive_pairs) if isinstance(reactive_pairs, list) else reactive_pairs
        reactive_bonus = reactive_pairs_count * 10.0  # 1ペアあたり10点
        score += reactive_bonus
        reasons.append("REACTIVE")

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
