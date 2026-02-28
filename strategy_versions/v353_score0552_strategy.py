#!/usr/bin/env python3
"""strategy.py - AI改善対象の決定スクリプト"""

# 固定インターフェース:
# decide(game_state: dict, analysis: dict) -> dict
#    戻り値: {"x": float, "reason": str}
#
# AI改変可能: decide() 内部、ヘルパー関数、定数、import
# AI改変禁止: decide() シグネチャ、if __name__ == "__main__" ブロック

# --- 変更履歴 ---
# v329: ターン数最大化アプローチ - v328の失敗（avg=1509.0、パラドックス発見）を受けて、「マージボーナス増強」から「ターン数最大化」へのパラダイムシフト。
#   v328バッチ分析から特定した問題:
#   - パラドックス発見: 高スコア群（2116）はmerge_rate=13.6%、低スコア群（827）はmerge_rate=15.9%。低スコア群の方がmerge_rateが高いのにスコアが低い
#   - ターン数の差が最大要因: ベスト110ターン vs ワースト78ターン。32ターンの差がスコア差の最大要因
#   - 高スコア群の特徴: 終盤max_y=1.57（低スコア群は1.47）、高スコア群の方が盤面が高いのにスコアが高い
#   - ドリフトの重要性: 高スコア群はドリフト制御が精密で、低スコア群はドリフトが大きく盤面崩壊しやすい
#   - 振り子パターン継続: v321→v322→v323→v324→v325→v326→v327→v328と、マージボーナスと高度管理の間で振り子を続けている
#   根本原因:
#   - v328までの全バージョンは「マージ率を上げてスコアを上げる」という前提で設計されていた
#   - しかし実際のデータは「ターン数が多い方がスコアが高い」という事実を示している
#   - マージ率が高い盤面は、小さいピースを無理してマージして盤面を不安定にし、早期にゲームオーバーになる
#   - 高度管理を強化しすぎると、マージ機会を逃して盤面が成長せず、ターン数が増えない
#   解決策（ターン数最大化アプローチ）:
#   - パラダイムシフト: 「マージボーナス増強」から「ターン数最大化」へ
#   - ドリフト精度ボーナス導入: drift < 0.05で+1000、0.1未満で+500。精密な配置は長期的な安定性に貢献
#   - 先読みマージ準備: next/type Nなら、type N-1の重心付近に+200/400ボーナス。将来のマージを予約し、ターンを節約
#   - マージ後価値ボーナス: マージ後のタイプが大きいほど+100/200/300ボーナス。大きなピースほど価値が高い
#   - 連鎖マージボーナス: 直近5ターン内にマージがあったら+100、2つ以上なら+300。連鎖でターンを効率的に使う
#   - ターン数最適化: drift < 0.05かつmerge_grade != NOなら+500。精密なマージで盤面安定とターン節約
#   - マージボーナス一律化: フェーズ感応化を破棄し、一律のボーナス値（DIRECT=600/NEAR=300/FAR=100）を採用
#   - 高度ペナルティ軽減: height_multを下げ（LOW=0.6/MEDIUM=1.2/HIGH=1.0/CRITICAL=0.8）、盤面成長を許容しターン数を増やす
#   核心的発見: 「マージ率が高いとスコアが高い」という前提は間違い。「ターン数が多いとスコアが高い」という事実に基づき、精密なドリフト制御、先読み、連鎖を重視するアプローチを採用。
#   成功基準: avg_scoreが1800以上、またはavg_turnsが90以上、またはmerge_rateが15%以上
#   失敗基準: avg_scoreがv328の1509.0未満、または盤面崩壊で即敗北率が30%以上
# [BEST:3689] v128: HIGHフェーズマージ優先版
# [BEST:2335] v42: v19復活・v31/v29複雑化要素削除版


def decide(game_state: dict, analysis: dict) -> dict:
    """ターン数最大化アプローチ。マージボーナス増強から精密なドリフト制御・先読み・連鎖重視へ。"""

    results = analysis.get("results", [])

    if not results:
        return {"x": 0.0, "reason": "no analysis data"}

    best_x = 0.0
    best_score = -float("inf")
    best_reason = ""

    # 盤面情報
    pieces = game_state.get("pieces", [])
    max_y = max([p["y"] for p in pieces]) if pieces else -4.0

    # フェーズ判定（v329: ターン数最大化のため高度ペナルティを軽減）
    if max_y < 0.8:
        phase = "LOW"
        height_mult = 0.6  # v329: 軽減、盤面成長を許容
        merge_mult = 1.0
    elif max_y < 1.8:
        phase = "MEDIUM"
        height_mult = 1.2  # v329: 軽減、HIGH到達を早期に許容
        merge_mult = 1.0
    elif max_y < 3.0:
        phase = "HIGH"
        height_mult = 1.0  # v329: 軽減、マージ優先を徹底
        merge_mult = 1.0
    else:
        phase = "CRITICAL"
        height_mult = 0.8  # v329: 軽減、マージ絶対優先
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

        # === v329: ターン数最大化アプローチ ===

        # 1. ドリフト精度ボーナス（精密な配置は長期的な安定性に貢献）
        drift_total = abs(drift_x) + drift_unc
        if drift_total < 0.05:
            score += 1000.0
            reasons.append("PRECISION_HIGH")
        elif drift_total < 0.10:
            score += 500.0
            reasons.append("PRECISION_MED")
        elif drift_total < 0.15:
            score += 200.0
            reasons.append("PRECISION_LOW")

        # 2. マージグレードによるスコア（一律化、フェーズ感応化は破棄）
        if merge_grade == "DIRECT":
            score += 600.0  # v329: 一律のボーナス値、フェーズ感応化は破棄
            reasons.append("DIRECT_MERGE")
        elif merge_grade == "NEAR":
            score += 300.0
            reasons.append("NEAR_MERGE")
        elif merge_grade == "FAR":
            score += 100.0
            reasons.append("FAR_MERGE")

        # 3. 高度によるペナルティ（軽減、盤面成長を許容）
        height_penalty = landing_y * 30.0 * height_mult  # v329: 50.0から30.0に軽減

        # HIGH_TOWERペナルティ（v329: 軽減、盤面成長を許容）
        if phase == "HIGH" and landing_y > 0.5:
            height_penalty *= 1.3  # v329: 2.0倍から1.3倍に軽減
            reasons.append("HIGH_TOWER")
        elif phase == "MEDIUM" and landing_y > 0.5:
            height_penalty *= 1.2  # v329: 1.5倍から1.2倍に軽減
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

        # 7. ターン数最適化（精密なマージで盤面安定とターン節約）
        if drift_total < 0.05 and merge_grade != "NO":
            score += 500.0
            reasons.append("OPTIMAL_MERGE")

        # 8. 左右バランス補正（v329: 一律20.0を採用、フェーズ感応化は破棄）
        balance_strength = 20.0  # v329: 一律20.0、フェーズ感応化は破棄
        left_count = sum(1 for p in pieces if p["x"] < 0)
        right_count = len(pieces) - left_count
        balance_bias = (right_count - left_count) / (len(pieces) if pieces else 1)
        balance_penalty = x * balance_bias * balance_strength
        score -= abs(balance_penalty)

        # 9. nextNext中央寄せ（v329: 削除、ターン数最大化にフォーカス）
        # v329: nextNext中央寄せは削除。ターン数最大化にフォーカス。

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
