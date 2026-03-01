#!/usr/bin/env python3
"""strategy.py - AI改善対象の決定スクリプト"""

# 固定インターフェース:
# decide(game_state: dict, analysis: dict) -> dict
#    戻り値: {"x": float, "reason": str}
#
# AI改変可能: decide() 内部、ヘルパー関数、定数、import
# AI改変禁止: decide() シグネチャ、if __name__ == "__main__" ブロック

# --- 変更履歴 ---
# v341: reactive_pairs活用・HIGHフェーズ構造改善版 - v340の失敗（avg_score=1153.7、HEIGHT_CONTROL支配28.9%）を受けて、ベストゲーム（score=1735）の成功要因を分析。ベストゲームではターン70以降にreactive_pairsが1以上に増加し、スコアが825から1735へ急上昇。ターン86-90でreactive_pairsが3-6に達し、大きな連鎖爆発を誘発。このことから「reactive_pairsが多い盤面は動的で、マージが連鎖的に発生しやすい」という性質を特定。v340はreactive_pairs情報を活用していなかったため、動的な盤面でのマージ優先ができなかった。
#   ベストゲームの成功要因の分析:
#   - ターン70でreactive_pairs=1に増加、スコア825→1489へ急増
#   - ターン86でreactive_pairs=6になり、スコア1614→1680へ増加
#   - ターン88でreactive_pairs=3になり、最終スコア1735へ到達
#   - 終盤にmax_y=2.7まで上昇したが、連鎖によるスコア獲得で救済
#   根本原因の特定:
#   - v340はreactive_pairs情報を活用せず、盤面の動的特性を無視していた
#   - 高度管理（height_mult=1.7）が強すぎ、動的な盤面でマージ機会を逃していた
#   - staticなスコアリングのみで、将来の連鎖可能性を考慮していなかった
#   改善策（reactive_pairs活用・HIGHフェーズ構造改善）:
#   - reactive_pairs活用ボーナス導入: reactive_pairsが多いほどマージボーナスを強化
#     * reactive_pairs >= 5: マージボーナス×2.0（連鎖爆発期待）
#     * reactive_pairs >= 3: マージボーナス×1.5（連鎖期待）
#     * reactive_pairs >= 1: マージボーナス×1.1（動的盤面）
#   - HIGHフェーズ高度管理緩和: height_multをv340の1.7から1.5に緩和（マージ機会確保）
#   - マージボーナス強化: v338の強力な値を採用（DIRECT=1500/NEAR=800/FAR=300）
#   - v340のシンプル構造を維持: CENTER_PAIR、reactive_pairs/near_pairsボーナスは削除
#   核心的発見: reactive_pairsは「盤面の動的特性」の指標。動的な盤面ではマージを優先し、静的な盤面では高度管理を行うことで、両方の状況で最適な判断が可能になる。ベストゲームの連鎖爆発はこの戦略で再現可能。
#   成功基準: avg_scoreがv340の1153.7以上、またはavg_scoreがv128の3689以上
#   失敗基準: avg_scoreがv340の1153.7未満
# v342: 動的高度管理・マージチェーン期待値版 - v341の失敗（avg_score=1265.0、HEIGHT_CONTROL支配28.5%、HEIGHT_TOWER avg_score_delta低い）を受けて、根本的な問題を特定。v341はreactive_pairsボーナスを導入したが、height_multは静的（HIGH=1.5）のままで、動的盤面でのマージ機会損失を十分に解消していなかった。batch_summary.txtの分析で、NEAR_MERGE_MEDIUM_TOWERのavg_score_delta=42.7（高スコア増分）はマージと高度管理のバランスが重要であることを示唆。ベストゲーム（score=1694）のターン推移を分析すると、reactive_pairsの増加とスコア上昇が同期（ターン50: reactive_pairs=2→ターン56: reactive_pairs=6、スコア847→1357）。これらの発見から、reactive_pairsを盤面の動的/静的特性の指標として活用し、動的な盤面では高度管理を緩和してマージを優先し、静的な盤面では高度管理を強化する「動的高度管理」戦略を導入。
#   根本原因の特定:
#   - v341はreactive_pairsボーナスを導入したが、height_multは静的（HIGH=1.5）のままで、動的盤面でのマージ機会損失を十分に解消していなかった
#   - batch_summary.txtでHEIGHT_CONTROLが28.5%と依然として支配的（v340の28.9%からほとんど改善されていない）
#   - HIGH_TOWERのavg_score_delta=11.2が低く、HIGHフェーズでの高度管理が過剰
#   - ベストゲームのターン推移分析で、reactive_pairsの増加とスコア上昇が同期（ターン50: reactive_pairs=2→ターン56: reactive_pairs=6、スコア847→1357）
#   - NEAR_MERGE_MEDIUM_TOWERのavg_score_delta=42.7は、マージと高度管理のバランスが重要であることを示唆
#   改善策（動的高度管理・マージチェーン期待値）:
#   - reactive_pairsに応じたheight_multの動的調整:
#     * reactive_pairs=0（静的盤面）: height_mult=2.0（高度管理強化、v128の1.8より強い）
#     * reactive_pairs=1-2: height_mult=1.8（標準、v128と同等）
#     * reactive_pairs=3-4: height_mult=1.6（緩和、v341の1.5よりやや強い）
#     * reactive_pairs>=5: height_mult=1.4（大幅緩和、マージ優先）
#   - マージチェーン期待値の導入: type N-1のペア数×係数 + type Nの存在で将来マージの可能性を評価
#     * type N-1のペア数をpiecesからカウント
#     * type N-1のペア数が多いほど、将来のtype Nが生成される可能性が高い
#     * 既存のtype Nが存在すれば、マージ後の連鎖可能性が高まる
#     * 連鎖マージの期待値をスコアリングに反映（マージボーナスに追加）
#   - HIGH_TOWERペナルティの動的調整:
#     * 静的盤面では強いペナルティ（1.5倍）
#     * 動的盤面では緩和（1.1倍）
#   - マージボーナス強化: v341の強力な値（DIRECT=1500/NEAR=800/FAR=300）を維持
#   - v341のreactive_pairsボーナスは削除（動的高度管理で代用）
#   核心的発見: reactive_pairsは「盤面の動的特性」の指標。動的な盤面（reactive_pairs多い）では高度管理を緩和してマージを優先し、静的な盤面（reactive_pairs少ない）では高度管理を強化することで、ベストゲームの成功要因（reactive_pairs増加→スコア上昇）を構造的に再現。マージチェーン期待値を導入することで、将来の連鎖マージの可能性をスコアリングに反映。
#   成功基準: avg_scoreがv341の1265.0以上、またはHEIGHT_CONTROL占有率が25%以下、またはavg_scoreがv128の3689以上
#   失敗基準: avg_scoreがv341の1265.0未満、またはHEIGHT_CONTROL占有率が28%以上
# v343: v128復帰・マージチェーン期待値強化版 - v342の失敗（avg_score=1914.7、HEIGHT_CONTROL支配13.1%、CHAIN_POTENTIAL avg_score_delta=24.0）を受けて、根本的な問題を特定。v342はheight_mult_modifierを導入したが、静的盤面でheight_mult=1.8*1.2=2.16と強すぎる問題を特定。また、マージチェーン期待値の係数が小さすぎる（50.0/30.0）ため、効果が表れていない。ベストゲーム（score=2495）のターン推移を分析すると、静的盤面での判断が重要であり、reactive_pairsが0のターンが多いことが分かる。v128（avg_score=3689、HIGHフェーズheight_mult=1.8固定）のシンプル構造が最適であることが分かる。また、マージチェーン期待値の係数を強化（100.0/60.0）することで、将来の連鎖マージの可能性をよりスコアリングに反映できる。
#   根本原因の特定:
#   - v342はheight_mult_modifierを導入したが、静的盤面でheight_multが1.8*1.2=2.16と強すぎる問題を特定
#   - マージチェーン期待値の係数が小さすぎる（50.0/30.0）のため、効果が表れていない
#   - tower_penalty_multが大きすぎる（1.5/1.3/1.2/1.1）のため、HIGH_TOWERペナルティが過剰
#   - ベストゲームのターン推移分析で、静的盤面での判断が重要であることが分かる
#   - v128（avg_score=3689、HIGHフェーズheight_mult=1.8固定）のシンプル構造が最適であることが分かる
#   改善策（v128復帰・マージチェーン期待値強化）:
#   - height_mult_modifierを削除し、v128の固定値（HIGHフェーズ=1.8）に戻す
#   - マージチェーン期待値の係数を強化:
#     * next_chain_expectation: 50.0 → 100.0 (2倍)
#     * next_next_chain_expectation: 30.0 → 60.0 (2倍)
#   - tower_penalty_multを緩和:
#     * MEDIUM_TOWER: 1.5 → 1.3
#     * HIGH_TOWER: 2.0 → 1.3
#   - HIGH_TOWER: 1.3 → 1.1
#   - v128の成功要素を維持:
#     * マージボーナス強化 (DIRECT=1500/NEAR=800/FAR=300)
#     * シンプルな構造（複雑化要素なし）
#     * 左右バランス補正（v128の設定を維持）
#   核心的発見: v342の動的高度管理は逆効果であり、v128のシンプル構造に復帰すべき。マージチェーン期待値の係数を強化することで、将来の連鎖マージの可能性をよりスコアリングに反映。ベストゲームでも静的盤面での判断が重要であることから、v128のシンプルな構造が最適である。
#   成功基準: avg_scoreがv342の1914.7以上、またはavg_scoreがv128の3689以上、またはHEIGHT_CONTROL占有率が10%以下
#   失敗基準: avg_scoreがv342の1914.7未満、またはHEIGHT_CONTROL占有率が15%以上
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


def decide(game_state: dict, analysis: dict) -> dict:
    """v128のシンプル構造に完全復帰し、マージチェーン期待値を完全削除。静的盤面ではheight_mult=1.8（固定）で安定した高度管理、即時的な判断（マージ・高度管理・バランス）のみに集中。v342で導入されたマージチェーン期待値はパフォーマンスを低下させたため、v128（best_score=3689）の成功構造を完全復帰。"""

    results = analysis.get("results", [])

    if not results:
        return {"x": 0.0, "reason": "no analysis data"}

    best_x = 0.0
    best_score = -float("inf")
    best_reason = ""

    # 盤面情報
    pieces = game_state.get("pieces", [])
    max_y = max([p["y"] for p in pieces]) if pieces else -4.0

    # reactor情報
    reactor = analysis.get("reactor", {})

    # nextNextピース情報（中央寄せボーナス計算用）
    next_piece = game_state.get("next", {})
    next_next_piece = game_state.get("nextNext", {})
    next_type = next_piece.get("type", 0)
    next_next_type = next_next_piece.get("type", 0)

    # フェーズ判定（v128: 0.8/1.8/3.0の閾値）
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
        # v408: v128の1.8を固定値で使用（v342のheight_mult_modifier削除）
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

        # === v408: v128復帰・マージチェーン期待値完全削除 ===

        # 1. マージグレードによるスコア（v408: v341の強力な値を維持）
        if merge_grade == "DIRECT":
            score += 1500.0 * merge_mult
            reasons.append("DIRECT_MERGE")
        elif merge_grade == "NEAR":
            score += 800.0 * merge_mult
            reasons.append("NEAR_MERGE")
        elif merge_grade == "FAR":
            score += 300.0 * merge_mult
            reasons.append("FAR_MERGE")

        # 2. 高度によるペナルティ（v408: v128の固定値）
        height_penalty = landing_y * 50.0 * height_mult

        # HIGH_TOWERペナルティ（v408: v128の設定に復帰）
        if phase == "HIGH" and landing_y > 0.5:
            # v408: HIGH_TOWERペナルティ1.3倍を維持
            height_penalty *= 1.3
            reasons.append("HIGH_TOWER")
        elif phase == "MEDIUM" and landing_y > 0.5:
            height_penalty *= 1.3
            reasons.append("MEDIUM_TOWER")
        elif landing_y > 0.0:
            reasons.append("HIGH_LAYER")

        score -= height_penalty

        # 3. ドリフトによるペナルティ（v408: v128の一律30.0を維持）
        drift_penalty = (abs(drift_x) + drift_unc) * 30.0
        score -= drift_penalty

        # 4. 左右バランス補正（v408: v128の設定を維持）
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

        # 5. nextNextが同じタイプなら中央寄せボーナス（v408: v128の設定を維持）
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
