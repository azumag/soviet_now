#!/usr/bin/env python3
"""strategy.py - AI改善対象の決定スクリプト"""

# 固定インターフェース:
# decide(game_state: dict, analysis: dict) -> dict
#    戻り値: {"x": float, "reason": str}
#
# AI改変可能: decide() 内部、ヘルパー関数、定数、import
# AI改変禁止: decide() シグネチャ、if __name__ == "__main__" ブロック

# --- 変更履歴 ---
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


def decide(game_state: dict, analysis: dict) -> dict:
    """v421: HIGHフェーズマージ強化・マージチェーン予測追加版"""

    results = analysis.get("results", [])

    if not results:
        return {"x": 0.0, "reason": "no analysis data"}

    best_x = 0.0
    best_score = -float("inf")
    best_reason = ""

    # 盤面情報
    pieces = game_state.get("pieces", [])
    max_y = max([p["y"] for p in pieces]) if pieces else -4.0

    # フェーズ判定（v421: MEDIUMフェーズmerge_multを1.1に増強）
    if max_y < 0.8:
        phase = "LOW"
        height_mult = 1.0
        merge_mult = 1.2
    elif max_y < 1.8:
        phase = "MEDIUM"
        height_mult = 2.4  # v421: v420の2.4を維持
        merge_mult = 1.1  # v421: 1.0から1.1に増強（中盤でのマージを促進）
    elif max_y < 3.0:
        phase = "HIGH"
        height_mult = 1.8  # v421: v420の1.8を維持
        merge_mult = 1.0
    else:
        phase = "CRITICAL"
        height_mult = 1.0  # CRITICAL: height_multなし
        merge_mult = 0.6  # v421: v420の0.6を維持

    # nextNextピース情報
    next_piece = game_state.get("next", {})
    next_next_piece = game_state.get("nextNext", {})
    next_type = next_piece.get("type", 0)
    next_next_type = next_next_piece.get("type", 0)

    # reactor情報の活用（v421: near_pairs、reactive_pairsをスコアリングに追加）
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

        # === v421: HIGHフェーズマージ強化・マージチェーン予測追加 ===

        # 1. マージグレードによるスコア（v421: v420の強力な値を維持）
        if merge_grade == "DIRECT":
            score += 1200.0 * merge_mult
            reasons.append("DIRECT_MERGE")
        elif merge_grade == "NEAR":
            score += 600.0 * merge_mult
            reasons.append("NEAR_MERGE")
        elif merge_grade == "FAR":
            score += 200.0 * merge_mult
            reasons.append("FAR_MERGE")

        # 2. 高度によるペナルティ（v421: HIGH_TOWERペナルティを1.0倍に緩和）
        height_penalty = landing_y * 50.0 * height_mult

        # HIGH_TOWERペナルティ（v421: 1.3倍から1.0倍に緩和）
        if phase == "HIGH" and landing_y > 0.5:
            height_penalty *= 1.0  # v421: 1.0倍に緩和（高度管理とマージのバランス調整）
            reasons.append("HIGH_TOWER")
        elif phase == "MEDIUM" and landing_y > 0.5:
            height_penalty *= 1.5  # v421: v420の1.5倍を維持
            reasons.append("MEDIUM_TOWER")
        elif landing_y > 0.0:
            reasons.append("HIGH_LAYER")

        score -= height_penalty

        # 3. ドリフトによるペナルティ（v421: v420の一律30.0を維持）
        drift_penalty = (abs(drift_x) + drift_unc) * 30.0
        score -= drift_penalty

        # 4. 左右バランス補正（v421: v420の設定を維持）
        balance_strength = 20.0
        if phase == "HIGH":
            balance_strength = 40.0  # v421: v420の40.0を維持
        elif phase == "MEDIUM":
            balance_strength = 30.0  # v421: v420の30.0を維持

        left_count = sum(1 for p in pieces if p["x"] < 0)
        right_count = len(pieces) - left_count
        balance_bias = (right_count - left_count) / (len(pieces) if pieces else 1)

        balance_penalty = x * balance_bias * balance_strength
        score -= abs(balance_penalty)

        # 5. nextNextが同じタイプなら中央寄せボーナス（v421: v420の設定を維持）
        if next_next_type == next_type:
            center_bonus = max(0, 1.0 - abs(x) / 2.0) * 50.0
            score += center_bonus
            reasons.append("NEXT_SAME")

        # 6. 先読みマージボーナス（v421: v420の設定を維持）
        future_merge_bonus = 0.0
        for target_type in [next_type, next_next_type]:
            if target_type > 1:
                prev_type_pieces = [p for p in pieces if p["type"] == target_type - 1]

                # v421: type N-1が0個の場合はボーナスを付与しない
                if len(prev_type_pieces) >= 1:
                    center_x = sum(p["x"] for p in prev_type_pieces) / len(
                        prev_type_pieces
                    )
                    distance = abs(x - center_x)

                    # v421: 距離係数を広げる: max(0, 3.0 - distance)
                    bonus = (
                        max(0, 3.0 - distance) * target_type * 10.0
                    )  # v421: type * 10.0
                    future_merge_bonus += bonus

                    # v421: type N-1が1個以上の場合、追加ボーナス（type * 5.0）
                    future_merge_bonus += target_type * 5.0

        if future_merge_bonus > 0:
            score += future_merge_bonus
            reasons.append("FUTURE_MERGE")

        # 7. マージチェーン予測（v421: type N-2ペアの存在確認を追加）
        chain_bonus = 0.0
        for target_type in [next_type, next_next_type]:
            if target_type > 2:
                prev2_type_pieces = [p for p in pieces if p["type"] == target_type - 2]

                # v421: type N-2が1個以上の場合、連鎖マージの可能性を評価
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

        # 8. reactor情報の活用（v421: near_pairs、reactive_pairsをスコアリングに追加）
        # v421: near_pairsの数に応じてボーナスを付与（マージ環境が良い場合）
        near_pairs_bonus = min(len(near_pairs), 5) * 20.0  # 最多5ペアまで
        score += near_pairs_bonus
        reasons.append("NEAR_PAIRS")

        # v421: reactive_pairsに応じてボーナスを付与（反応器が活性化している場合）
        reactive_bonus = reactive_pairs * 10.0  # 1ペアあたり10点
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
