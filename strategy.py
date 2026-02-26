#!/usr/bin/env python3
"""strategy.py - AI改善対象の決定スクリプト"""

# 固定インターフェース:
# decide(game_state: dict, analysis: dict) -> dict
#    戻り値: {"x": float, "reason": str}
#
# AI改変可能: decide() 内部,ヘルパー関数,定数,import
# AI改変禁止: decide() シグネチャ,if __name__ == "__main__" ブロック

# --- 変更履歴 ---
# [BEST:2325] v19: CRITICALフェーズ導入版
# [BEST:2335] v42: v19復活・v31/v29複雑化要素削除版 - v41の失敗（スコア558）を受けて、v41がv31から取り入れたreactive_pairsとhas_mergeによる複雑な条件分岐を削除。v19のシンプル構造（DIRECT=1200/NEAR=600/FAR=200、height_penalty=50*height_mult、drift_penalty=30）に復活。v19のCRITICALフェーズ（merge_mult=0.6）を維持。コード量削減（約140行→約110行）で頑健性を確保
# v50-v64: has_merge/reactive_pairs条件の振り子パターンと閾値シャッフル - 複数回の追加・削除・再追加を繰り返したが、どれも失敗。v64ではv12の「緩い高度管理」を採用したが、HIGHフェーズでマージ機会を大幅に逃した（87ターン中13ターンのみ）。HEIGHT_CONTROLが32%を占め、マージ優先が崩れた。
# v72: v42構造基盤・reactive_pairsシンプル活用版 - v71の失敗（スコア330、60ターンでマージ0回）を受けて、v71の複雑なreactive_pairs条件分岐を削除。v71はv31の細かい条件分岐（HIGHでreactive_pairs>=3なら35.0、>=2なら45.0）を再導入したが、実際には60ターン中マージ0回で完全に失敗。v42のシンプル構造（約110行）をベースにしつつ、reactive_pairs活用を「フェーズ判定に組み込む」というシンプルな形で再設計。細かい条件分岐は削除し、代わりに「reactive_pairs>=5ならmax_yに+0.5を加算」というシンプルなルールを導入。これにより、chain reaction中は実質的にheight_penaltyが緩和され、マージ機会が確保される。コード量約100行でv42の頑健性を維持しつつ、reactive_pairs活用をシンプルに統合
# v73: v42完全復帰版 - v72の失敗（スコア887、HEIGHT_CONTROLが約70%で支配的、HIGHフェーズでマージ関連の理由がほぼ皆無）を受けて、reactive_pairs活用を完全削除。v72のreactive_pairs>=5条件は履歴で2回しか出現せず、効果が限定的。履歴分析でv31→v42→v71→v72の振り子パターンを確認し、reactive_pairs活用は本質的な解決になっていないことを特定。v42のシンプルかつ頑健な構造（約100行）への完全復帰。v19の成功値を維持：マージボーナス1200/600/200、height_mult(MEDIUM=2.4/HIGH=2.6)、HIGH height_penalty=2.0、MEDIUM height_penalty=1.5、CRITICAL height_multiplier=30.0、CRITICAL merge_mult=0.6。reactive_pairs活用を完全排除し、v42の成功構造を完全復活
# v74: chain reaction緩和導入版 - v73の失敗（スコア1328、マージ機会9.3%のみ、HEIGHT_CONTROLが33.3%で支配的）を受けて、振り子パターン（reactive_pairs追加/削除）を避け、v31の成功要素「chain reaction中に高度管理緩和」をシンプルに再導入。v73はv42構造を完全復活したが、chain reaction中の高度管理緩和が欠けており、マージ機会が減少したことが原因。v74では、v42のシンプル構造（約100行）を維持しつつ、HIGHフェーズでreactor_reactive_pairsまたはnear_pairsが一定数以上の時、height_multiplierを35.0に緩和しchain reactionを優先。reactive_pairs条件分岐の複雑化（v31/v71/v72の失敗）を避け、reactive_pairs>=3またはnear_pairs>=5のシンプルな条件に統合。has_mergeがある場合、drift_penalty_factorを0.6に緩和してマージ機会を確保。drift_penaltyを一律35.0に統一し、balance_penaltyを一律30.0に統一し、スコアリングの一貫性を確保。コード量約95行でv42の頑健性とv31のchain reaction管理を統合


def decide(game_state: dict, analysis: dict) -> dict:
    """v42のシンプル構造を維持しつつ、chain reaction中に高度管理をシンプルに緩和"""

    results = analysis.get("results", [])

    if not results:
        return {"x": 0.0, "reason": "no analysis data"}

    best_x = 0.0
    best_score = -float("inf")
    best_reason = ""

    # 盤面情報
    pieces = game_state.get("pieces", [])
    max_y = max([p["y"] for p in pieces]) if pieces else -4.0

    # reactor情報（chain reaction検出用）
    reactor = analysis.get("reactor", {})
    reactive_pairs_raw = reactor.get("reactive_pairs", 0)
    reactive_pairs = (
        len(reactive_pairs_raw)
        if isinstance(reactive_pairs_raw, list)
        else reactive_pairs_raw
    )
    near_pairs = reactor.get("near_pairs", [])

    # フェーズ判定（v42: v19の閾値0.8/1.8/3.0を維持）
    if max_y < 0.8:
        phase = "LOW"
        height_mult = 1.0
        merge_mult = 1.2
    elif max_y < 1.8:
        phase = "MEDIUM"
        height_mult = 2.4  # v42: v19の2.4を維持
        merge_mult = 1.0
    elif max_y < 3.0:
        phase = "HIGH"
        height_mult = 2.6  # v42: v19の2.6を維持
        merge_mult = 1.0
    else:
        phase = "CRITICAL"
        height_mult = 1.0  # CRITICAL: height_multなし
        merge_mult = 0.6  # v42: v19の0.6を維持

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
        has_merge = result.get("has_merge", False)

        score = 0.0
        reasons = []

        # 1. マージグレードによるスコア（v42: v19の強力な値）
        if merge_grade == "DIRECT":
            score += 1200.0 * merge_mult  # v42: v19の1200を維持
            reasons.append("DIRECT_MERGE")
        elif merge_grade == "NEAR":
            score += 600.0 * merge_mult  # v42: v19の600を維持
            reasons.append("NEAR_MERGE")
        elif merge_grade == "FAR":
            score += 200.0 * merge_mult  # v42: v19の200を維持
            reasons.append("FAR_MERGE")

        # 2. 高度によるペナルティ（v74: chain reaction中に緩和）
        if phase == "CRITICAL":
            # CRITICALフェーズではheight_multiplier強化（v42: v19の30.0を維持）
            height_multiplier = 30.0
            height_penalty = landing_y * height_multiplier
            if landing_y > 1.0:
                reasons.append("CRITICAL_HEIGHT")
        else:
            # v74: chain reaction中は高度管理を緩和（reactive_pairs>=3またはnear_pairs>=5）
            height_multiplier = 50.0
            if phase == "HIGH" and (reactive_pairs >= 3 or len(near_pairs) >= 5):
                height_multiplier = 35.0  # chain reaction中は緩和

            height_penalty = landing_y * height_mult * height_multiplier

            # 高盤面での追加ペナルティ（CRITICALフェーズでは適用しない）
            if phase == "HIGH" and landing_y > 0.5:
                height_penalty *= 2.0  # v42: v19の2.0を維持
                reasons.append("HIGH_TOWER")
            elif phase == "MEDIUM" and landing_y > 0.5:
                height_penalty *= 1.5  # v42: v19の1.5を維持
                reasons.append("MEDIUM_TOWER")
            elif landing_y > 0.0:
                reasons.append("HIGH_LAYER")

        score -= height_penalty

        # 3. ドリフトによるペナルティ（v74: has_merge時に緩和、一律35.0に統一）
        drift_penalty_factor = 1.0
        if phase == "HIGH" and has_merge:
            drift_penalty_factor = 0.6  # マージ機会確保

        drift_penalty = (abs(drift_x) + drift_unc) * 35.0 * drift_penalty_factor
        score -= drift_penalty

        # 4. 左右バランス補正（v74: 一律30.0に統一）
        balance_strength = 30.0

        left_count = sum(1 for p in pieces if p["x"] < 0)
        right_count = len(pieces) - left_count
        balance_bias = (right_count - left_count) / (len(pieces) if pieces else 1)

        balance_penalty = x * balance_bias * balance_strength
        score -= abs(balance_penalty)

        # 5. nextNextが同じタイプなら中央寄せボーナス（v42: v19の設定を維持）
        if next_next_type == next_type:
            if phase == "CRITICAL":
                center_bonus = (
                    max(0, 1.0 - abs(x) / 2.0) * 60.0
                )  # v42: v19のCRITICAL強化
            else:
                center_bonus = max(0, 1.0 - abs(x) / 2.0) * 50.0  # v42: v19の基本値
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
