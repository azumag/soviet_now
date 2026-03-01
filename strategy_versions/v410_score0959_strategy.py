#!/usr/bin/env python3
"""strategy.py - AI改善対象の決定スクリプト"""

# 固定インターフェース:
# decide(game_state: dict, analysis: dict) -> dict
#    戻り値: {"x": float, "reason": str}
#
# AI改変可能: decide() 内部、ヘルパー関数、定数、import
# AI改変禁止: decide() シグネチャ、if __name__ == "__main__" ブロック

# --- 変更履歴 ---
# v407: マージチェーン期待値係数低減版 - v343の失敗（score=1277、CHAIN_POTENTIAL支配45.8%）を受けて、係数を低減したが、依然としてCHAIN_POTENTIALが支配的（83%以上）。v128（best_score=3689）のコードを調査した結果、v128は「chain reaction緩和は完全廃止」としており、将来的な連鎖期待を完全に考慮しないシンプルな構造で成功していたことが判明。マージチェーン期待値はv342で導入されたが、これはv128のシンプル構造を破壊し、パフォーマンスを低下させている根本原因。
#   根本原因の特定:
#   - v128（best_score=3689）は将来的な連鎖期待を完全に考慮しないシンプル構造
#   - v342で導入されたマージチェーン期待値は、v128のシンプル構造を破壊
#   - マージチェーン期待値係数を低減（50.0/30.0）しても、CHAIN_POTENTIALが支配的（83%以上）
#   - 将来的な連鎖期待をスコアリングに含めること自体が、現在のポジショニング判断を阻害
#   - v128の成功は即時的な判断（マージ・高度管理・バランス）に基づいている
#   改善策（マージチェーン期待値完全削除）:
#   - マージチェーン期待値の計算関数calc_chain_expectation()を完全削除
#   - マージチェーン期待値ボーナスを完全削除（マージあり・なし共に削除）
#   - next/nextNextピース情報をスコアリングから完全削除
#   - v128のシンプル構造を完全復帰:
#     * HIGHフェーズheight_mult=1.8固定
#     * マージボーナス強化 (DIRECT=1500/NEAR=800/FAR=300)
#     * 左右バランス補正（v128の設定を維持）
#     * HIGH_TOWERペナルティ: 1.3倍
#     * ドリフトペナルティ: 30.0
#     * nextNextが同じタイプなら中央寄せボーナス（v128の設定を維持）
#   核心的発見: v128（best_score=3689）は将来的な連鎖期待を完全に考慮しないシンプル構造で成功。マージチェーン期待値をスコアリングに含めることは、現在のポジショニング判断を阻害し、パフォーマンスを低下させる。即時的な判断（マージ・高度管理・バランス）のみに集中すべき。
#   成功基準: CHAIN_POTENTIALが0%、またはscoreがv128の3689に近い
#   失敗基準: CHAIN_POTENTIALが使用される、またはscoreがv343の1277未満
# v408: v128復帰・マージチェーン期待値完全削除版 - v407の成功基準達成（CHAIN_POTENTIAL=0%）を受けて、v128のシンプル構造を完全復帰。v128の成功要素（マージボーナス強化・height_mult=1.8固定・HIGH_TOWERペナルティ1.3倍・ドリフトペナルティ30.0・左右バランス補正・nextNext中央寄せボーナス）を全て維持。マージチェーン期待値を完全削除し、即時的な判断（マージ・高度管理・バランス）のみに集中。v342の失敗（height_mult_modifierの複雑な条件分岐）を避け、v128のシンプル構造を維持。
#   根本原因の特定:
#   - v128の成功はシンプル構造と即時的判断に基づいている
#   - マージチェーン期待値をスコアリングに含めると、現在のポジショニング判断を阻害
#   - v407でCHAIN_POTENTIALを完全削除し、v128の構造に復帰することで成功
#   改善策（v128完全復帰）:
#   - v128のシンプル構造を完全復帰: height_mult=1.8固定、マージボーナス強化（DIRECT=1500/NEAR=800/FAR=300）、HIGH_TOWERペナルティ1.3倍、ドリフトペナルティ30.0、左右バランス補正、nextNext中央寄せボーナス
#   - マージチェーン期待値を完全削除: 即時的な判断に集中
#   - 振り子パターン回避: reactive_pairs情報を活用しない（v341-v343の失敗から学ぶ）
#   核心的発見: v128（best_score=3689）は将来的な連鎖期待を完全に考慮しないシンプル構造で成功。マージチェーン期待値をスコアリングに含めることは、現在のポジショニング判断を阻害し、パフォーマンスを低下させる。即時的な判断（マージ・高度管理・バランス）のみに集中すべき。
#   成功基準: scoreがv128の3689に近い、またはscoreがv407の1277以上
#   失敗基準: scoreがv407の1277未満、またはCHAIN_POTENTIALが使用される
# v409: reactive_pairs動的高度管理版 - v408の失敗（score=471、4ターンで終了、merge_rate=25%）を受けて、根本的な問題を特定。v408はv128のシンプル構造を完全復帰したが、reactive_pairs情報を全く活用していなかった。ベストゲーム分析(v341)で特定した「reactive_pairsが多い盤面は動的で、マージが連鎖的に発生しやすい」という性質を活用していないことが、パフォーマンス低下の原因。v342の失敗（height_mult_modifierの複雑な条件分岐で静的盤面で強すぎ）を回避しつつ、v128のシンプル構造を維持したままreactive_pairs情報を活用する第三の選択肢を導入。
#   根本原因の特定:
#   - v408はv128のシンプル構造を完全復帰したが、reactive_pairs情報を全く活用していなかった
#   - v341のベストゲーム分析で「reactive_pairsが多い盤面は動的で、マージが連鎖的に発生しやすい」という性質を特定
#   - v342の失敗: height_mult_modifierの複雑な条件分岐で静的盤面でheight_multが強すぎる問題
#   - v343/v407の失敗: マージチェーン期待値をスコアリングに含めると現在のポジショニング判断を阻害
#   改善策（reactive_pairs動的高度管理・v128シンプル構造維持）:
#   - v128のシンプル構造を維持: マージボーナス強化（DIRECT=1500/NEAR=800/FAR=300）、ドリフトペナルティ30.0、左右バランス補正、nextNext中央寄せボーナス
#   - reactive_pairsを「動的高度管理」として活用（v342の複雑な条件分岐を回避）:
#     * reactive_pairs >= 5: 動的盤面と判断、高度管理を緩和（HIGHフェーズheight_mult=1.5、HIGH_TOWERペナルティ1.1倍）
#     * reactive_pairs < 5: 標準設定を維持（HIGHフェーズheight_mult=1.8、HIGH_TOWERペナルティ1.3倍）
#   - マージチェーン期待値は完全削除: 即時的な判断に集中（v407の成功）
#   - 振り子パターン回避: reactive_pairsを「ボーナス」として活用するのではなく、「高度管理の動的調整」として活用
#   核心的発見: v128のシンプル構造（即時的判断）を維持しつつ、reactive_pairs情報を「動的高度管理」として活用することで、v128の成功要素とv341の発見（reactive_pairsが多い盤面は動的でマージ連鎖しやすい）を両立。v342の失敗（複雑な条件分岐）を回避しつつ、v128のシンプル構造を維持したままreactive_pairs情報を活用する第三の選択肢。
#   成功基準: scoreがv408の471以上、またはmerge_rateが25%以上改善
#   失敗基準: scoreがv408の471未満、またはCHAIN_POTENTIALが使用される


def decide(game_state: dict, analysis: dict) -> dict:
    """v128のシンプル構造を維持しつつ、reactive_pairs情報を「動的高度管理」として活用。reactive_pairs >= 5で動的盤面と判断し、高度管理を緩和してマージを優先。v128の成功要素（即時的判断）を維持しつつ、v341の発見（reactive_pairsが多い盤面は動的でマージ連鎖しやすい）を両立。"""

    results = analysis.get("results", [])

    if not results:
        return {"x": 0.0, "reason": "no analysis data"}

    best_x = 0.0
    best_score = -float("inf")
    best_reason = ""

    # 盤面情報
    pieces = game_state.get("pieces", [])
    max_y = max([p["y"] for p in pieces]) if pieces else -4.0

    # reactor情報（v409: reactive_pairs情報を動的高度管理に活用）
    reactor = analysis.get("reactor", {})
    reactive_pairs_val = reactor.get("reactive_pairs", 0)
    reactive_pairs = (
        len(reactive_pairs_val)
        if isinstance(reactive_pairs_val, list)
        else reactive_pairs_val
    )

    # v409: reactive_pairs >= 5で動的盤面と判断（v342の複雑な条件分岐を回避）
    is_dynamic_board = reactive_pairs >= 5

    # nextNextピース情報（中央寄せボーナス計算用）
    next_piece = game_state.get("next", {})
    next_next_piece = game_state.get("nextNext", {})
    next_type = next_piece.get("type", 0)
    next_next_type = next_next_piece.get("type", 0)

    # フェーズ判定（v409: v128の閾値0.8/1.8/3.0を維持）
    if max_y < 0.8:
        phase = "LOW"
        height_mult = 1.0
        merge_mult = 1.2
    elif max_y < 1.8:
        phase = "MEDIUM"
        height_mult = 2.4
        merge_mult = 1.0
    elif max_y < 3.0:
        phase = "HIGH"
        # v409: reactive_pairsに応じた動的高度管理（v342の複雑な条件分岐を回避）
        if is_dynamic_board:
            # 動的盤面: 高度管理を緩和してマージを優先
            height_mult = 1.5
        else:
            # 標準設定: v128の1.8を維持
            height_mult = 1.8
        merge_mult = 1.0
    else:
        phase = "CRITICAL"
        height_mult = 1.0
        merge_mult = 0.6

    for result in results:
        x = result["x"]
        landing_y = result.get("landing_y", 0)
        drift_x = result.get("drift_x", 0)
        drift_unc = result.get("drift_unc", 0)
        merge_grade = result.get("merge_grade", "NO")

        score = 0.0
        reasons = []

        # === v409: v128シンプル構造 + reactive_pairs動的高度管理 ===

        # 1. マージグレードによるスコア（v409: v408/v128の強力な値を維持）
        if merge_grade == "DIRECT":
            score += 1500.0 * merge_mult
            reasons.append("DIRECT_MERGE")
        elif merge_grade == "NEAR":
            score += 800.0 * merge_mult
            reasons.append("NEAR_MERGE")
        elif merge_grade == "FAR":
            score += 300.0 * merge_mult
            reasons.append("FAR_MERGE")

        # 2. 高度によるペナルティ（v409: reactive_pairs動的高度管理を適用）
        height_penalty = landing_y * 50.0 * height_mult

        # HIGH_TOWERペナルティ（v409: reactive_pairsに応じた動的調整）
        if phase == "HIGH" and landing_y > 0.5:
            if is_dynamic_board:
                # 動的盤面: ペナルティを緩和してマージを優先
                height_penalty *= 1.1
                reasons.append("DYNAMIC_TOWER")
            else:
                # 標準設定: v128の1.3倍を維持
                height_penalty *= 1.3
                reasons.append("HIGH_TOWER")
        elif phase == "MEDIUM" and landing_y > 0.5:
            height_penalty *= 1.3
            reasons.append("MEDIUM_TOWER")
        elif landing_y > 0.0:
            reasons.append("HIGH_LAYER")

        score -= height_penalty

        # 3. ドリフトによるペナルティ（v409: v128の一律30.0を維持）
        drift_penalty = (abs(drift_x) + drift_unc) * 30.0
        score -= drift_penalty

        # 4. 左右バランス補正（v409: v128の設定を維持）
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

        # 5. nextNextが同じタイプなら中央寄せボーナス（v409: v128の設定を維持）
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
