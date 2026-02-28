#!/usr/bin/env python3
"""strategy.py - AI改善対象の決定スクリプト"""

# 固定インターフェース:
# decide(game_state: dict, analysis: dict) -> dict
#    戻り値: {"x": float, "reason": str}
#
# AI改変可能: decide() 内部、ヘルパー関数、定数、import
# AI改変禁止: decide() シグネチャ、if __name__ == "__main__" ブロック

# --- 変更履歴 ---
# v330: 微調整・ターン数優先版 - v329の失敗（パラドックスの誤解釈）を受けて、v328の成功構造に戻し、微調整のみ実施。
#   v329失敗分析から特定した問題:
#   - パラドックスの誤解釈: 「高スコア群はmerge_rateが低い」を「マージを避けるべき」と誤解
#   - 実際の意味: 高スコア群は「無理にマージしていない」のではなく「自然な配置でマージしている」
#   - 複雑化の失敗: v329は精度ボーナス、先読み、連鎖など多数の新機能を追加したが、どれも効果がなかった
#   - ゲーム終了: v329は23ターンで終了し、目標の90ターンに遠く及ばなかった
#   - v329の根本的な間違い: ターン数最大化は「保守的なプレイ」ではなく「盤面を安定させながら自然に成長させること」
#   根本原因:
#   - v328はavg_score=1509.0で十分に機能しており、大きな改善が必要だったわけではない
#   - パラドックスの発見は「より良い戦略」ではなく「v328の理解」を促すものだった
#   - v328の成功要素: フェーズ感応化、適度なマージボーナス、高度管理、バランス補正
#   解決策（微調整・ターン数優先版）:
#   - v329の全機能を破棄し、v328のシンプル構造に戻す
#   - 微調整のみ: マージボーナスを5%削減（DIRECT=1200→1140、NEAR=600→570、FAR=200→190）
#   - 微調整のみ: 高度ペナルティを5%削減（50.0→47.5）。盤面成長を少し許容
#   - v328の全要素維持: フェーズ閾値（0.8/1.8/3.0）、height_mult（LOW=1.0/MEDIUM=2.4/HIGH=1.8/CRITICAL=1.0）、merge_mult（HIGH=1.5）
#   - v328の全要素維持: バランス補正（20.0/30.0/40.0）、HIGH_TOWERペナルティ（2.0/1.5倍）、nextNext中央寄せ（50.0）
#   核心的発見: パラドックス解釈の誤りを修正し、v328の成功構造を維持しつつ、微調整でターン数を少し増やす。
#   成功基準: avg_scoreが1600以上（v328の1509.0より100点改善）、またはavg_turnsが90以上
#   失敗基準: avg_scoreがv328の1509.0未満
# [BEST:3689] v128: HIGHフェーズマージ優先版
# [BEST:2335] v42: v19復活・v31/v29複雑化要素削除版
# [BEST:1509] v328: HIGHフェーズマージ強化・v42ベース版


def decide(game_state: dict, analysis: dict) -> dict:
    """微調整・ターン数優先版。v328の成功構造を維持しつつ、微調整でターン数を少し増やす。"""

    results = analysis.get("results", [])

    if not results:
        return {"x": 0.0, "reason": "no analysis data"}

    best_x = 0.0
    best_score = -float("inf")
    best_reason = ""

    # 盤面情報
    pieces = game_state.get("pieces", [])
    max_y = max([p["y"] for p in pieces]) if pieces else -4.0

    # フェーズ判定（v330: v328の閾値0.8/1.8/3.0を維持）
    if max_y < 0.8:
        phase = "LOW"
        height_mult = 1.0  # v330: v328の1.0を維持
        merge_mult = 1.2
    elif max_y < 1.8:
        phase = "MEDIUM"
        height_mult = 2.4  # v330: v328の2.4を維持、HIGH到達遅延
        merge_mult = 1.0
    elif max_y < 3.0:
        phase = "HIGH"
        height_mult = 1.8  # v330: v328の1.8を維持、高度管理緩和・マージ優先
        merge_mult = 1.5  # v330: v328の1.5を維持、HIGHフェーズのみマージボーナス強化
    else:
        phase = "CRITICAL"
        height_mult = 1.0  # v330: v328の1.0を維持
        merge_mult = 0.6  # v330: v328の0.6を維持、マージ絶対優先

    # 次のピース情報
    next_piece = game_state.get("next", {})
    next_next_piece = game_state.get("nextNext", {})
    next_type = next_piece.get("type", 0)
    next_next_type = next_next_piece.get("type", 0)

    for result in results:
        x = result["x"]
        landing_y = result.get("landing_y", 0)
        drift_x = result.get("drift_x", 0)
        drift_unc = result.get("drift_unc", 0)
        merge_grade = result.get("merge_grade", "NO")

        score = 0.0
        reasons = []

        # === v330: 微調整・ターン数優先版 ===

        # 1. マージグレードによるスコア（v330: v328の値から5%削減、微調整）
        if merge_grade == "DIRECT":
            score += 1140.0 * merge_mult  # v330: v328の1200から5%削減
            reasons.append("DIRECT_MERGE")
        elif merge_grade == "NEAR":
            score += 570.0 * merge_mult  # v330: v328の600から5%削減
            reasons.append("NEAR_MERGE")
        elif merge_grade == "FAR":
            score += 190.0 * merge_mult  # v330: v328の200から5%削減
            reasons.append("FAR_MERGE")

        # 2. 高度によるペナルティ（v330: v328の値から5%削減、盤面成長を少し許容）
        height_penalty = landing_y * 47.5 * height_mult  # v330: v328の50.0から5%削減

        # HIGH_TOWERペナルティ（v330: v328の値を維持）
        if phase == "HIGH" and landing_y > 0.5:
            height_penalty *= 2.0  # v330: v328の2.0倍を維持
            reasons.append("HIGH_TOWER")
        elif phase == "MEDIUM" and landing_y > 0.5:
            height_penalty *= 1.5  # v330: v328の1.5倍を維持
            reasons.append("MEDIUM_TOWER")
        elif landing_y > 0.0:
            reasons.append("HIGH_LAYER")

        score -= height_penalty

        # 3. ドリフトによるペナルティ（v330: v328の一律30.0を維持）
        drift_penalty = (abs(drift_x) + drift_unc) * 30.0
        score -= drift_penalty

        # 4. 左右バランス補正（v330: v328のフェーズ感応化を維持）
        if phase == "HIGH":
            balance_strength = 40.0  # v330: v328の40.0を維持
        elif phase == "MEDIUM":
            balance_strength = 30.0  # v330: v328の30.0を維持
        else:
            balance_strength = 20.0  # v330: v328の20.0を維持

        left_count = sum(1 for p in pieces if p["x"] < 0)
        right_count = len(pieces) - left_count
        balance_bias = (right_count - left_count) / (len(pieces) if pieces else 1)

        balance_penalty = x * balance_bias * balance_strength
        score -= abs(balance_penalty)

        # 5. nextNextが同じタイプなら中央寄せボーナス（v330: v328の一律50.0を維持）
        if next_next_type == next_type:
            center_bonus = max(0, 1.0 - abs(x) / 2.0) * 50.0
            score += center_bonus
            reasons.append("NEXT_SAME")

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
