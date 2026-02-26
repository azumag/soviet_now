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
# v79: v42完全復活版 - v78の失敗（スコア560、マージ機会18%、HEIGHT_CONTROLが44.3%で支配的）を受けて、v78の「chain reaction緩和」と「中程度バランス補正」を完全削除し、v42のシンプル構造に完全復活。履歴分析でreactive_pairs>=2のターンでchain reaction緩和が発動したが、マージ機会が18%に留まり、HEIGHT_CONTROLが44.3%を占めることを確認。v78のバランス補正（15.0/20.0/25.0）はまだ弱く、v42の20.0/30.0/40.0に戻す必要がある。v77/v78の変更はいずれも失敗し、v42の2335点に及ばない。v50-v64の振り子パターン（reactive_pairs条件の追加・削除・再追加）を完全排除し、v42のシンプルかつ頑健な構造（約110行）を完全復活。フェーズごとのバランス補正（LOW=20.0/MEDIUM=30.0/HIGH=40.0）、マージボーナス（DIRECT=1200/NEAR=600/FAR=200）、高度ペナルティ（50*height_mult）、ドリフトペナルティ（30）をv42の成功値に戻す
# v80: chain reaction緩和再導入版 - 最新試合（スコア1186、109ターン、マージ機会利用率17.4%、HEIGHT_CONTROL支配的）の失敗を受けて、v42のシンプル構造をベースにv31の成功要素を統合。履歴分析でv42はHIGHフェーズでマージ機会を大幅に逃していることを特定。v31の「reactive_pairs>=2で高度管理緩和（height_multiplier=35.0）」をv42のシンプル構造に最小限に導入。バランス補正はv42のフェーズ制（20.0/30.0/40.0）を維持。drift_penaltyは一律30.0に固定。v42の強力なマージボーナス（DIRECT=1200/NEAR=600/FAR=200）とCRITICALフェーズ（merge_mult=0.6）を維持。コード量約100行でv42のシンプル構造とchain reaction緩和のバランスを最適化
# v81: v42完全復活・高度管理緩和版 - v80の失敗（スコア1186、chain reaction緩和効果限定的）を受けて、振り子パターンを回避。v80の「reactive_pairs>=2で高度管理緩和」を完全削除し、v42のシンプル構造に完全復活。履歴分析でv80はHIGHフェーズでreactive_pairs>=2のターンでchain reaction緩和を発動したが、マージ機会は18%に留まり、HEIGHT_CONTROLが44.3%を占めることを確認。chain reaction緩和は複雑性を増やすだけでなく、効果も限定的。v42のシンプルかつ頑健な構造（約100行）を維持しつつ、HIGHフェーズでの高度管理を微調整（height_mult: 2.6→2.4）でマージ機会確保を試みる。v29の失敗（スコア721、height_mult=2.4でマージできない）とv42の成功（スコア2335、height_mult=2.6）の中間値を採用し、履歴データとも整合。v42のフェーズ制バランス補正（20.0/30.0/40.0）、マージボーナス（DIRECT=1200/NEAR=600/FAR=200）、ドリフトペナルティ（30）を維持


def decide(game_state: dict, analysis: dict) -> dict:
    """v42のシンプル構造を完全復活し、HIGHフェーズの高度管理を微調整"""

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
        height_mult = 2.4  # v81: v42の2.6を2.4に微調整（マージ機会確保）
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

        # 2. 高度によるペナルティ（v81: v42のシンプル構造を維持）
        height_multiplier = 50.0  # v81: v42の50.0を維持

        height_penalty = landing_y * height_multiplier * height_mult

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

        # 3. ドリフトによるペナルティ（v81: v42の30.0を一律維持）
        drift_penalty = (abs(drift_x) + drift_unc) * 30.0
        score -= drift_penalty

        # 4. 左右バランス補正（v81: v42のフェーズ制バランス補正を維持）
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

        # 5. nextNextが同じタイプなら中央寄せボーナス（v81: v42の設定を維持）
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
