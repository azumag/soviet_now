#!/usr/bin/env python3
"""strategy.py - AI改善対象の決定スクリプト"""

# 固定インターフェース:
# decide(game_state: dict, analysis: dict) -> dict
#    戻り値: {"x": float, "reason": str}
#
# AI改変可能: decide() 内部、ヘルパー関数、定数、import
# AI改変禁止: decide() シグネチャ、if __name__ == "__main__" ブロック

# --- 変更履歴 ---
# v329: ターン数最大化アプローチ（第2版） - v329初版の失敗（23ターン・232点）を受けて、精度ボーナスの過大補正を修正。
#   v329初版分析から特定した問題:
#   - 精度ボーナス過大: drift < 0.05で+1000が高すぎて、マージ機会を回避してしまっている
#   - ターン数過少: 23ターン（目標90+）と大幅に不足。保守的すぎる
#   - スコア過少: 232点（v328の1509点より大幅に低い）
#   - マージボーナス過少: DIRECT=600が低すぎて、マージを十分に促進していない
#   根本原因:
#   - v329初版は「精度=長期的安定性」という前提で精度ボーナスを1000に設定したが、これは間違い
#   - 実際には「精度が高すぎるとマージ機会を逃してターン数が減る」
#   - ターン数最大化の正しい意味は「無理にマージせず、自然な配置で長くプレイする」こと
#   - パラドックス解釈の修正: 高スコア群のmerge_rateが低いのは「マージを逃している」のではなく「無理にマージしていない」
#   解決策（ターン数最大化アプローチ第2版）:
#   - 精度ボーナス大幅削減: 1000→200、500→100、200→50。精度は重要だが、絶対的ではない
#   - マージボーナス強化: DIRECT=1200、NEAR=600、FAR=200に戻す。マージ機会を適度に評価
#   - 高度ペナルティ軽減維持: height_multを低めに設定（LOW=0.8/MEDIUM=1.5/HIGH=1.2/CRITICAL=1.0）
#   - 先読みマージ準備維持: next/type Nなら、type N-1の重心付近にボーナス（200/400）
#   - マージ後価値ボーナス維持: マージ後のタイプが大きいほどボーナス（100/200/300）
#   - 連鎖マージボーナス維持: 直近5ターン内にマージがあったらボーナス（100/300）
#   - 最適マージボーナス削除: drift < 0.05かつmergeの+500は削除。精度とマージの相乗効果は過大評価
#   - バランス補正維持: 一律20.0でフェーズ感応化は破棄
#   核心的発見: ターン数最大化は「精度優先」ではなく「自然な配置」。無理にマージせず、盤面が自然に成長するように配置し、マージ機会があれば活用する。
#   成功基準: avg_scoreが1500以上、またはavg_turnsが70以上
#   失敗基準: avg_scoreが1000未満、またはavg_turnsが50未満
# [BEST:3689] v128: HIGHフェーズマージ優先版
# [BEST:2335] v42: v19復活・v31/v29複雑化要素削除版


def decide(game_state: dict, analysis: dict) -> dict:
    """ターン数最大化アプローチ（第2版）。精度ボーナスを削減し、自然な配置でターン数を最大化。"""

    results = analysis.get("results", [])

    if not results:
        return {"x": 0.0, "reason": "no analysis data"}

    best_x = 0.0
    best_score = -float("inf")
    best_reason = ""

    # 盤面情報
    pieces = game_state.get("pieces", [])
    max_y = max([p["y"] for p in pieces]) if pieces else -4.0

    # フェーズ判定（v329第2版: 高度ペナルティ軽減を維持）
    if max_y < 0.8:
        phase = "LOW"
        height_mult = 0.8  # v329第2版: 軽減維持、盤面成長を許容
        merge_mult = 1.0
    elif max_y < 1.8:
        phase = "MEDIUM"
        height_mult = 1.5  # v329第2版: 軽減維持、HIGH到達を早期に許容
        merge_mult = 1.0
    elif max_y < 3.0:
        phase = "HIGH"
        height_mult = 1.2  # v329第2版: 軽減維持、マージ優先を徹底
        merge_mult = 1.0
    else:
        phase = "CRITICAL"
        height_mult = 1.0  # v329第2版: 軽減維持、マージ絶対優先
        merge_mult = 1.0

    # 次のピース情報
    next_piece = game_state.get("next", {})
    next_next_piece = game_state.get("nextNext", {})
    next_type = next_piece.get("type", 0)
    next_next_type = next_next_piece.get("type", 0)

    # マージ履歴（連鎖マージボーナス用）
    merge_history = analysis.get("merge_history", [])

    for result in results:
        x = result["x"]
        landing_y = result.get("landing_y", 0)
        drift_x = result.get("drift_x", 0)
        drift_unc = result.get("drift_unc", 0)
        merge_grade = result.get("merge_grade", "NO")

        score = 0.0
        reasons = []

        # === v329第2版: ターン数最大化アプローチ（精度ボーナス削減版） ===

        # 1. ドリフト精度ボーナス（大幅削減、精度は重要だが絶対的ではない）
        drift_total = abs(drift_x) + drift_unc
        if drift_total < 0.05:
            score += 200.0  # v329第2版: 1000→200に大幅削減
            reasons.append("PRECISION_HIGH")
        elif drift_total < 0.10:
            score += 100.0  # v329第2版: 500→100に削減
            reasons.append("PRECISION_MED")

        # 2. マージグレードによるスコア（v328の値に戻す、一律化は破棄）
        if merge_grade == "DIRECT":
            score += 1200.0  # v329第2版: v328の1200に戻す
            reasons.append("DIRECT_MERGE")
        elif merge_grade == "NEAR":
            score += 600.0  # v329第2版: v328の600に戻す
            reasons.append("NEAR_MERGE")
        elif merge_grade == "FAR":
            score += 200.0  # v329第2版: v328の200に戻す
            reasons.append("FAR_MERGE")

        # 3. 高度によるペナルティ（軽減維持、盤面成長を許容）
        height_penalty = landing_y * 40.0 * height_mult  # v329第2版: 30.0→40.0に微増

        # HIGH_TOWERペナルティ（v329第2版: 軽減維持、盤面成長を許容）
        if phase == "HIGH" and landing_y > 0.5:
            height_penalty *= 1.3  # v329第2版: 1.3倍を維持
            reasons.append("HIGH_TOWER")
        elif phase == "MEDIUM" and landing_y > 0.5:
            height_penalty *= 1.2  # v329第2版: 1.2倍を維持
            reasons.append("MEDIUM_TOWER")
        elif landing_y > 0.0:
            reasons.append("HIGH_LAYER")

        score -= height_penalty

        # 4. 先読みマージ準備（next/type Nなら、type N-1の重心付近に配置）
        if next_type > 1:
            # type N-1のピースを探す
            target_pieces = [p for p in pieces if p["type"] == next_type - 1]
            if target_pieces:
                # 重心を計算
                center_x = sum(p["x"] for p in target_pieces) / len(target_pieces)
                distance = abs(x - center_x)
                if distance < 0.5:
                    score += 400.0
                    reasons.append("LOOKAHEAD_HIGH")
                elif distance < 1.0:
                    score += 200.0
                    reasons.append("LOOKAHEAD_MED")

        # 5. マージ後価値ボーナス（マージ後のタイプが大きいほど価値が高い）
        if merge_grade != "NO":
            current_type = next_piece.get("type", 0)
            # マージ後のタイプは current_type + 1（同じタイプ同士でマージするとき）
            merged_type = current_type + 1
            if merged_type >= 8:
                score += 300.0
                reasons.append("HIGH_VALUE")
            elif merged_type >= 6:
                score += 200.0
                reasons.append("MED_VALUE")
            elif merged_type >= 4:
                score += 100.0
                reasons.append("LOW_VALUE")

        # 6. 連鎖マージボーナス（直近5ターン内にマージがあったらボーナス）
        recent_merges = sum(1 for m in merge_history[-5:] if m)
        if recent_merges >= 2:
            score += 300.0
            reasons.append("CHAIN_MERGE")
        elif recent_merges >= 1:
            score += 100.0
            reasons.append("RECENT_MERGE")

        # v329第2版: 最適マージボーナス（drift < 0.05かつmerge）は削除。相乗効果は過大評価

        # 7. 左右バランス補正（一律20.0、フェーズ感応化は破棄）
        balance_strength = 20.0
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
            "merge_history": [],  # TODO: 履歴管理が必要
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
