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
# v77: マージ優先・バランス大幅削減版 - v76の失敗（スコア752、マージ機会7.8%のみ、HEIGHT_CONTROLが39.1%で支配的）を受けて、v42のバランス補正（balance_strength=20.0/30.0/40.0）が強すぎて、マージ機会を大幅に制限していることを特定。履歴分析で盤面が左右不均等（X=-2に12個、X=1に5個）で、バランス補正のくせに不均一になっていることを確認。v77では、v42のシンプル構造を維持しつつ、バランス補正を一律5.0に固定（v42の20.0/30.0/40.0から大幅削減）。マージ時の高度ペナルティを緩和（DIRECT: 30%、NEAR: 50%）し、reactive_pairs>=2の時、height_multiplierを35.0に緩和（v31の成功要素をシンプルに再導入）。drift_penaltyを35.0に強化し、balance_penaltyを一律5.0に固定し、マージ優先の戦略に転換。コード量約110行でv42のシンプル構造とマージ優先戦略を統合
# v78: 振り子回避・中程度バランス補正版 - v77の失敗（スコア908、マージ機会7.8%のみ、HEIGHT_CONTROLが22%で支配的、バランス補正過剰削減で盤面不均等）を受けて、振り子パターンの根本原因を分析。v31/v50-v64の「chain reaction時の高度管理緩和」が振り子を引き起こしていた（reactive_pairs>=3→>=2→削除→再追加）。v77のバランス補正一律5.0は削減しすぎて盤面不均等を招き、逆にマージ機会を減らしていた。v78では、振り子パターンを回避し、v42のフェーズ制バランス補正（20.0/30.0/40.0）より緩やかだがv77の一律5.0より強い中程度のバランス補正（LOW=15.0/MEDIUM=20.0/HIGH=25.0）を導入。v77のマージ時の高度ペナルティ緩和（DIRECT=0.3/NEAR=0.5）を削除してv42のシンプル構造に戻す。chain reaction時の高度管理緩和を控えめに実装（reactive_pairs>=2、height_multiplier=50.0→35.0、v31の>=3を緩和）。drift_penaltyを一律30.0に戻し、シンプル構造（約100行）を維持しつつ、バランス補正とchain reaction緩和のバランスを最適化
# v79: v42完全復活版 - v78の失敗（スコア560、マージ機会18%、HEIGHT_CONTROLが44.3%で支配的）を受けて、v78の「chain reaction緩和」と「中程度バランス補正」を完全削除し、v42のシンプル構造に完全復活。履歴分析でreactive_pairs>=2のターンでchain reaction緩和が発動したが、マージ機会が18%に留まり、HEIGHT_CONTROLが44.3%を占めることを確認。v78のバランス補正（15.0/20.0/25.0）はまだ弱く、v42の20.0/30.0/40.0に戻す必要がある。v77/v78の変更はいずれも失敗し、v42の2335点に及ばない。v50-v64の振り子パターン（reactive_pairs条件の追加・削除・再追加）を完全排除し、v42のシンプルかつ頑健な構造（約110行）を完全復活。フェーズごとのバランス補正（LOW=20.0/MEDIUM=30.0/HIGH=40.0）、マージボーナス（DIRECT=1200/NEAR=600/FAR=200）、高度ペナルティ（50*height_mult）、ドリフトペナルティ（30）をv42の成功値に戻す


def decide(game_state: dict, analysis: dict) -> dict:
    """v42のシンプルかつ頑健な構造を完全復活"""

    results = analysis.get("results", [])

    if not results:
        return {"x": 0.0, "reason": "no analysis data"}

    best_x = 0.0
    best_score = -float("inf")
    best_reason = ""

    # 盤面情報
    pieces = game_state.get("pieces", [])
    max_y = max([p["y"] for p in pieces]) if pieces else -4.0

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

        # 1. マージグレードによるスコア（v42: v19の強力な値を維持）
        if merge_grade == "DIRECT":
            score += 1200.0 * merge_mult  # v42: v19の1200を維持
            reasons.append("DIRECT_MERGE")
        elif merge_grade == "NEAR":
            score += 600.0 * merge_mult  # v42: v19の600を維持
            reasons.append("NEAR_MERGE")
        elif merge_grade == "FAR":
            score += 200.0 * merge_mult  # v42: v19の200を維持
            reasons.append("FAR_MERGE")

        # 2. 高度によるペナルティ（v42: v19のシンプル構造）
        if phase == "CRITICAL":
            # CRITICALフェーズではheight_multiplier強化（v42: v19の30.0を維持）
            height_multiplier = 30.0
            height_penalty = landing_y * height_multiplier
            if landing_y > 1.0:
                reasons.append("CRITICAL_HEIGHT")
        else:
            height_multiplier = 50.0  # v42: v19の50.0を維持
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

        # 3. ドリフトによるペナルティ（v42: v19の30.0を維持）
        drift_penalty = (abs(drift_x) + drift_unc) * 30.0
        score -= drift_penalty

        # 4. 左右バランス補正（v42: v19のフェーズ制バランス補正）
        balance_strength = 20.0
        if phase == "MEDIUM":
            balance_strength = 30.0
        elif phase == "HIGH":
            balance_strength = 40.0
        # CRITICALフェーズではバランス補正緩和（マージ優先）

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
