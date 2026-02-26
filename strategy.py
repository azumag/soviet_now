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
# v75: HIGHフェーズ一律緩和版 - v74の失敗（スコア1150、HIGHフェーズ7ターンでマージ0回、reactive_pairs>=3なのにマージ未選択）を受けて、振り子パターン（reactive_pairs追加/削除）を完全解消し、reactive_pairs条件を完全削除。v74のchain reaction条件（reactive_pairs>=3またはnear_pairs>=5）はHIGHフェーズでほとんど発動せず（HIGHフェーズ7ターン全てでreactive_pairs>=3なのにマージ0回）、chain reaction中の高度管理緩和が機能していなかった。v75では、v42のシンプル構造（約100行）を維持しつつ、HIGHフェーズ全体でheight_multiplierを一律35.0に緩和しマージ機会を確保。reactive_pairs条件を完全削除し、chain reaction中のhas_mergeによるdrift_penalty緩和も削除し、シンプルで一貫性のある高度管理緩和を実現。drift_penaltyを一律30.0に戻し、balance_penaltyを一律30.0に維持し、v42の頑健性を維持しつつHIGHフェーズでのマージ機会を最大化。コード量約85行でv42の頑健性とシンプルな一貫性のある緩和戦略を統合
# v76: v42完全復活版 - v75の失敗（スコア828、HIGHフェーズ一律緩和が効果なし、HEIGHT_CONTROLが23回で支配的）を受けて、v75の一律化（height_multiplier=35.0, balance_strength=30.0）を削除し、v42のフェーズごとの設定に完全復活。履歴分析でHIGHフェーズ（4ターン）でマージ25%のみ、MEDIUMフェーズ（5ターン）でマージ0回を確認。v75の一律緩和はマージ機会を増やすどころか、v42の2335点から828点に大幅低下。振り子パターン（v31→v42→v74→v75、v29→v30→v31→v42）の根本原因は「HIGHフェーズ一律緩和」という発想自体の誤り。v42のシンプル構造（約110行）を完全復活し、フェーズごとの設定を再導入：HIGHフェーズheight_multiplier=50.0（v42の値）、balance_strength（LOW=20.0/MEDIUM=30.0/HIGH=40.0）。一律化を削除し、v42の頑健なフェーズ制を維持
# v77: マージ優先・バランス大幅削減版 - v76の失敗（スコア752、マージ機会7.8%のみ、HEIGHT_CONTROLが39.1%で支配的）を受けて、v42のバランス補正（balance_strength=20.0/30.0/40.0）が強すぎて、マージ機会を大幅に制限していることを特定。履歴分析で盤面が左右不均等（X=-2に12個、X=1に5個）で、バランス補正のくせに不均一になっていることを確認。v77では、v42のシンプル構造を維持しつつ、バランス補正を一律5.0に固定（v42の20.0/30.0/40.0から大幅削減）。マージ時の高度ペナルティを緩和（DIRECT: 30%、NEAR: 50%）し、reactive_pairs>=2の時、height_multiplierを35.0に緩和（v31の成功要素をシンプルに再導入）。drift_penaltyを35.0に強化し、balance_penaltyを一律5.0に固定し、マージ優先の戦略に転換。コード量約110行でv42のシンプル構造とマージ優先戦略を統合


def decide(game_state: dict, analysis: dict) -> dict:
    """v42のシンプル構造を維持しつつ、バランス補正を大幅に削減しマージ優先に転換"""

    results = analysis.get("results", [])

    if not results:
        return {"x": 0.0, "reason": "no analysis data"}

    best_x = 0.0
    best_score = -float("inf")
    best_reason = ""

    # 盤面情報
    pieces = game_state.get("pieces", [])
    max_y = max([p["y"] for p in pieces]) if pieces else -4.0

    # reactor情報（v31の成功要素を再導入）
    reactor = analysis.get("reactor", {})
    reactive_pairs_raw = reactor.get("reactive_pairs", 0)
    reactive_pairs = (
        len(reactive_pairs_raw)
        if isinstance(reactive_pairs_raw, list)
        else reactive_pairs_raw
    )

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

        # 2. 高度によるペナルティ（v77: マージ時の高度ペナルティ緩和を導入）
        if phase == "CRITICAL":
            # CRITICALフェーズではheight_multiplier強化（v42: v19の30.0を維持）
            height_multiplier = 30.0
            height_penalty = landing_y * height_multiplier
            if landing_y > 1.0:
                reasons.append("CRITICAL_HEIGHT")
        else:
            # v77: マージ時の高度ペナルティ緩和
            height_penalty_factor = 1.0
            if merge_grade == "DIRECT":
                height_penalty_factor = 0.3  # DIRECTマージは高度ペナルティ大幅緩和
            elif merge_grade == "NEAR":
                height_penalty_factor = 0.5  # NEARマージは高度ペナルティ緩和

            # v77: chain reaction中は高度管理緩和（v31の成功要素）
            if reactive_pairs >= 2:
                height_multiplier = 35.0  # chain reaction中は高度管理緩和
            else:
                height_multiplier = 50.0  # v42: 全フェーズで50.0

            height_penalty = (
                landing_y * height_mult * height_multiplier * height_penalty_factor
            )

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

        # 3. ドリフトによるペナルティ（v77: 強化してマージ優先）
        drift_penalty = (abs(drift_x) + drift_unc) * 35.0  # v77: 30.0から35.0に強化
        score -= drift_penalty

        # 4. 左右バランス補正（v77: 一律5.0に固定、大幅削減）
        balance_strength = 5.0  # v77: v42の20.0/30.0/40.0から5.0に大幅削減

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
