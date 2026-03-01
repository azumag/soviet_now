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
# [BEST:3689] v128: HIGHフェーズマージ優先版
# [BEST:2335] v42: v19復活・v31/v29複雑化要素削除版
# [BEST:1509] v328: HIGHフェーズマージ強化・v42ベース版


def decide(game_state: dict, analysis: dict) -> dict:
    """動的高度管理とマージチェーン期待値を導入。reactive_pairsに応じたheight_multの動的調整で、動的盤面ではマージ優先、静的盤面では高度管理強化。"""

    results = analysis.get("results", [])

    if not results:
        return {"x": 0.0, "reason": "no analysis data"}

    best_x = 0.0
    best_score = -float("inf")
    best_reason = ""

    # 盤面情報
    pieces = game_state.get("pieces", [])
    max_y = max([p["y"] for p in pieces]) if pieces else -4.0

    # reactor情報（v342: 動的高度管理のため盤面の動的特性を活用）
    reactor = analysis.get("reactor", {})
    reactive_pairs_val = reactor.get("reactive_pairs", 0)
    reactive_pairs = (
        len(reactive_pairs_val)
        if isinstance(reactive_pairs_val, list)
        else reactive_pairs_val
    )

    # 次のピース情報（v342: マージチェーン期待値計算用）
    next_piece = game_state.get("next", {})
    next_next_piece = game_state.get("nextNext", {})
    next_type = next_piece.get("type", 0)
    next_next_type = next_next_piece.get("type", 0)

    # マージチェーン期待値の計算（v342: type N-1のペア数とtype Nの存在から将来マージ可能性を評価）
    def calc_chain_expectation(pieces, target_type):
        """target_typeのマージチェーン期待値を計算"""
        if target_type <= 1:
            return 0.0  # type1はマージ不可

        # type N-1のピースをカウント
        type_n_minus_1_count = sum(1 for p in pieces if p["type"] == target_type - 1)

        # type Nが既に存在するか
        has_type_n = any(p["type"] == target_type for p in pieces)

        # マージチェーン期待値: type N-1が多いほど、type Nが生成される可能性が高い
        # type Nが既に存在すれば、マージ後の連鎖可能性が高まる
        if type_n_minus_1_count == 0:
            return 0.0
        elif type_n_minus_1_count == 1:
            base_expectation = 1.0
        elif type_n_minus_1_count == 2:
            base_expectation = 2.5  # 2個あれば必ずマージしてtype Nが生成される
        else:
            base_expectation = 4.0  # 3個以上あれば連鎖期待が高い

        # type Nが既に存在すれば、連鎖期待値を倍増
        if has_type_n:
            base_expectation *= 1.5

        return base_expectation

    # nextとnextNextのマージチェーン期待値
    next_chain_expectation = calc_chain_expectation(pieces, next_type)
    next_next_chain_expectation = calc_chain_expectation(pieces, next_next_type)

    # reactive_pairsに基づくheight_multの動的調整（v342: 動的高度管理）
    # 静的盤面では高度管理強化、動的盤面では緩和
    if reactive_pairs == 0:
        # 静的盤面: 高度管理強化（v128の1.8より強い）
        height_mult_modifier = 1.2  # ベースheight_multを1.2倍に強化
        tower_penalty_mult = 1.5  # HIGH_TOWERペナルティ強化
    elif reactive_pairs <= 2:
        # 標準: v128と同等
        height_mult_modifier = 1.0
        tower_penalty_mult = 1.3
    elif reactive_pairs <= 4:
        # 緩和: v341の1.5よりやや強い
        height_mult_modifier = 0.9
        tower_penalty_mult = 1.2
    else:
        # 動的盤面（reactive_pairs>=5）: 大幅緩和、マージ優先
        height_mult_modifier = 0.8
        tower_penalty_mult = 1.1

    # フェーズ判定（v342: v128の閾値0.8/1.8/3.0を維持）
    if max_y < 0.8:
        phase = "LOW"
        height_mult_base = 1.0 * height_mult_modifier
        merge_mult = 1.2
    elif max_y < 1.8:
        phase = "MEDIUM"
        height_mult_base = 2.4 * height_mult_modifier
        merge_mult = 1.0
    elif max_y < 3.0:
        phase = "HIGH"
        # v342: v128の1.8をベースに動的調整
        height_mult_base = 1.8 * height_mult_modifier
        merge_mult = 1.0
    else:
        phase = "CRITICAL"
        height_mult_base = 1.0  # CRITICAL: height_multなし
        merge_mult = 0.6  # v128の0.6を維持

    for result in results:
        x = result["x"]
        landing_y = result.get("landing_y", 0)
        drift_x = result.get("drift_x", 0)
        drift_unc = result.get("drift_unc", 0)
        merge_grade = result.get("merge_grade", "NO")

        score = 0.0
        reasons = []

        # === v342: 動的高度管理・マージチェーン期待値 ===

        # 1. マージグレードによるスコア（v342: v341の強力な値を維持）
        if merge_grade == "DIRECT":
            score += 1500.0 * merge_mult
            reasons.append("DIRECT_MERGE")
        elif merge_grade == "NEAR":
            score += 800.0 * merge_mult
            reasons.append("NEAR_MERGE")
        elif merge_grade == "FAR":
            score += 300.0 * merge_mult
            reasons.append("FAR_MERGE")

        # マージチェーン期待値ボーナス（v342: 将来の連鎖マージの可能性をスコアに反映）
        if merge_grade != "NO":
            chain_bonus = (next_chain_expectation * 50.0) + (
                next_next_chain_expectation * 30.0
            )
            score += chain_bonus
            if chain_bonus > 50.0:
                reasons.append("CHAIN")
        else:
            # マージなしの場合、チェーン期待値が高いならペナルティを軽減
            chain_relief = (next_chain_expectation * 30.0) + (
                next_next_chain_expectation * 20.0
            )
            if chain_relief > 0:
                score += chain_relief
                reasons.append("CHAIN_POTENTIAL")

        # 2. 高度によるペナルティ（v342: 動的高度管理を適用）
        height_penalty = landing_y * 50.0 * height_mult_base

        # HIGH_TOWERペナルティ（v342: 動的調整を適用）
        if phase == "HIGH" and landing_y > 0.5:
            height_penalty *= tower_penalty_mult  # 動的調整
            reasons.append("HIGH_TOWER")
        elif phase == "MEDIUM" and landing_y > 0.5:
            height_penalty *= 1.5
            reasons.append("MEDIUM_TOWER")
        elif landing_y > 0.0:
            reasons.append("HIGH_LAYER")

        score -= height_penalty

        # 3. ドリフトによるペナルティ（v342: v128の一律30.0を維持）
        drift_penalty = (abs(drift_x) + drift_unc) * 30.0
        score -= drift_penalty

        # 4. 左右バランス補正（v342: v128の設定を維持）
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

        # 5. nextNextが同じタイプなら中央寄せボーナス（v342: v128の設定を維持）
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
