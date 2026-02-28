#!/usr/bin/env python3
"""strategy.py - AI改善対象の決定スクリプト"""

# 固定インターフェース:
# decide(game_state: dict, analysis: dict) -> dict
#    戻り値: {"x": float, "reason": str}
#
# AI改変可能: decide() 内部、ヘルパー関数、定数、import
# AI改変禁止: decide() シグネチャ、if __name__ == "__main__" ブロック

# --- 変更履歴 ---
# v337: マージ品質応答・高度管理調整版 - v336のavg=707.5（v128の3689から大幅低下）を受けて、将来マージ期待値ボーナスを削除し、マージ品質に応じた高度管理調整を導入。
#   v336バッチ分析から特定した問題:
#   - avg_score 707.5: v128の3689から大幅低下、振り子パターンを回避しきれていない
#   - 将来マージ期待値ボーナスの効果不透明: FUTURE_MERGE_HIGHが5.9%出ているが、スコアへの寄与が不明
#   - ワーストゲームの特徴: HEIGHT_CONTROLが支配的で、マージ機会を損失。ターン数58ターンで早期ゲームオーバー
#   - ベストゲームの特徴: NEAR_MERGE関連が多く、マージに成功。ターン数78ターンで長期戦
#   - HIGHフェーズのheight_mult=1.8: v128から継承しているが、マージ機会がない時の高度管理が弱い
#   - 将来マージ期待値ボーナスの矛盾: merge_grade=="NO"の場合に期待値ボーナスを追加しているが、マージできないなら高度管理優先すべき
#   振り子パターン検出: v335(動的切り替え, avg=507.3)→v336(静的切り替え, avg=707.5)→v337(マージ品質応答調整, 目標1000+)
#   根本原因:
#   - v336はv128のシンプル構造に復帰したが、HIGHフェーズのheight_mult=1.8は「マージ優先」を意味し、マージ機会がない時の高度管理が弱い
#   - 将来マージ期待値ボーナスは複雑で効果が薄い。マージできないなら、将来マージできるとしても、今は高度管理を優先すべき
#   - 動的切り替え(near_pairs>=2)は判定が不正確で、振り子パターンを引き起こす
#   解決策（マージ品質応答・高度管理調整）:
#   - 将来マージ期待値計算の削除: 複雑で効果が薄いボーナスを削除し、シンプル構造を維持
#   - マージ品質ごとの高度管理調整: merge_gradeごとにheight_penaltyの乗数を変える静的調整を導入
#     * merge_grade=="NO": height_penalty *= 1.3（高度管理強化）
#     * merge_grade=="FAR": 変更なし（基準値）
#     * merge_grade=="NEAR"/"DIRECT": height_penalty *= 0.8（高度管理緩和）
#   - HIGHフェーズのHIGH_TOWERペナルティ調整: merge_gradeごとの乗数と統合し、一貫した高度管理を実現
#   - CENTERボーナスの強化: nextNextが同じタイプなら中央寄せボーナスを強化（50.0 → 80.0）
#   - reactive_pairsボーナスの調整: 20.0 → 30.0（微増）
#   核心的発見: 動的切り替えの振り子パターンを回避するために、第三の選択肢を取る。動的切り替えはしない（near_pairs>=2の判定は不正確）。代わりに、merge_gradeごとに高度管理の強度を変える静的調整を導入する。これにより、v128のシンプル構造を維持しつつ、マージ品質に応じた高度管理を実現し、振り子パターンを回避する。
#   成功基準: avg_scoreがv336の707.5以上、またはmerge_rateが15%以上、またはavg_scoreがv128の3689以上
#   失敗基準: avg_scoreがv335の507.3未満、またはmerge_rateが10%未満、またはavg_scoreがv336の707.5未満
# [BEST:3689] v128: HIGHフェーズマージ優先版
# [BEST:2335] v42: v19復活・v31/v29複雑化要素削除版
# [BEST:1509] v328: HIGHフェーズマージ強化・v42ベース版


def decide(game_state: dict, analysis: dict) -> dict:
    """マージ品質応答・高度管理調整版。将来マージ期待値ボーナスを削除し、マージ品質に応じた高度管理調整を導入。"""

    results = analysis.get("results", [])

    if not results:
        return {"x": 0.0, "reason": "no analysis data"}

    best_x = 0.0
    best_score = -float("inf")
    best_reason = ""

    # 盤面情報
    pieces = game_state.get("pieces", [])
    max_y = max([p["y"] for p in pieces]) if pieces else -4.0

    # reactor情報（v337: 静的なボーナスとして活用）
    reactor = analysis.get("reactor", {})
    reactive_pairs_val = reactor.get("reactive_pairs", 0)
    reactive_pairs = (
        len(reactive_pairs_val)
        if isinstance(reactive_pairs_val, list)
        else reactive_pairs_val
    )
    near_pairs_val = reactor.get("near_pairs", 0)
    near_pairs = (
        len(near_pairs_val) if isinstance(near_pairs_val, list) else near_pairs_val
    )

    # フェーズ判定（v337: v128の閾値0.8/1.8/3.0を採用、動的切り替えなし）
    if max_y < 0.8:
        phase = "LOW"
        height_mult = 1.0
        merge_mult = 1.2
    elif max_y < 1.8:
        phase = "MEDIUM"
        height_mult = 2.4  # v128: v42の2.4を維持
        merge_mult = 1.0
    elif max_y < 3.0:
        phase = "HIGH"
        height_mult = 1.8  # v337: v128の1.8を維持、動的切り替えなし
        merge_mult = 1.0
    else:
        phase = "CRITICAL"
        height_mult = 1.0
        merge_mult = 0.6

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

        # === v337: マージ品質応答・高度管理調整 ===

        # 1. マージグレードによるスコア（v337: v128の値を維持）
        merge_bonus = 0.0
        if merge_grade == "DIRECT":
            merge_bonus = 1200.0 * merge_mult
            reasons.append("DIRECT_MERGE")
        elif merge_grade == "NEAR":
            merge_bonus = 600.0 * merge_mult
            reasons.append("NEAR_MERGE")
        elif merge_grade == "FAR":
            merge_bonus = 200.0 * merge_mult
            reasons.append("FAR_MERGE")

        # nextNextが同じタイプならボーナス係数（v337: v128の値を維持）
        if next_next_type == next_type:
            merge_bonus *= 1.2
            reasons.append("NEXT_SAME")

        score += merge_bonus

        # 2. 高度によるペナルティ（v337: v128の値を維持）
        height_penalty = landing_y * 50.0 * height_mult

        # HIGH_TOWERペナルティ（v337: マージ品質応答調整を導入）
        if phase == "HIGH" and landing_y > 0.5:
            # v337: マージ品質ごとの高度管理調整
            # マージできるなら高度管理緩和、マージできないなら高度管理強化
            if merge_grade == "NO":
                height_penalty *= 1.3  # 高度管理強化
                reasons.append("HEIGHT_STRICT")
            elif merge_grade == "FAR":
                height_penalty *= 1.3  # 基準値
                reasons.append("HIGH_TOWER")
            elif merge_grade == "NEAR":
                height_penalty *= 1.0  # 高度管理緩和（v128の1.3倍から緩和）
                reasons.append("HIGH_TOWER_RELAX")
            elif merge_grade == "DIRECT":
                height_penalty *= 0.8  # 高度管理さらに緩和
                reasons.append("HIGH_TOWER_RELAX_MORE")
        elif phase == "MEDIUM" and landing_y > 0.5:
            height_penalty *= 1.5
            reasons.append("MEDIUM_TOWER")
        elif landing_y > 0.0:
            reasons.append("HIGH_LAYER")

        score -= height_penalty

        # 3. ドリフトによるペナルティ（v337: v128の一律30.0を維持）
        drift_penalty = (abs(drift_x) + drift_unc) * 30.0
        score -= drift_penalty

        # 4. 左右バランス補正（v337: v128の値を維持）
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

        # 5. nextNextが同じタイプなら中央寄せボーナス（v337: 強化）
        if next_next_type == next_type:
            center_bonus = max(0, 1.0 - abs(x) / 2.0) * 80.0  # v337: 50.0 → 80.0に強化
            score += center_bonus
            if "CENTER" not in reasons:
                reasons.append("CENTER")

        # 6. reactor情報活用ボーナス（v337: 静的なボーナスとして活用）
        # reactive_pairsが多いほど、盤面が活発でマージが起きやすい
        if reactive_pairs >= 3:
            score += 50.0
            reasons.append("REACTIVE")
        elif reactive_pairs >= 1:
            score += 30.0  # v337: 20.0 → 30.0に微増

        # near_pairsが多いほど、マージ機会が多い
        if near_pairs >= 2:
            score += 30.0
            reasons.append("NEAR_PAIR")

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
