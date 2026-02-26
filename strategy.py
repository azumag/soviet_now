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
# v80: chain reaction緩和再導入版 - 最新試合（スコア1186、109ターン、マージ機会利用率17.4%、HEIGHT_CONTROL支配的）の失敗を受けて、v42のシンプル構造をベースにv31の成功要素を統合。履歴分析でv42はHIGHフェーズでマージ機会を大幅に逃していることを特定。v31の「reactive_pairs>=2で高度管理緩和（height_multiplier=35.0）」をv42のシンプル構造に最小限に導入。バランス補正はv42のフェーズ制（20.0/30.0/40.0）を維持。drift_penaltyは一律30.0に固定。v42の強力なマージボーナス（DIRECT=1200/NEAR=600/FAR=200）とCRITICALフェーズ（merge_mult=0.6）を維持。コード量約100行でv42のシンプル構造とchain reaction緩和のバランスを最適化
# v81: v42完全復活・高度管理緩和版 - v80の失敗（スコア1186、chain reaction緩和効果限定的）を受けて、振り子パターンを回避。v80の「reactive_pairs>=2で高度管理緩和」を完全削除し、v42のシンプル構造に完全復活。履歴分析でv80はHIGHフェーズでreactive_pairs>=2のターンでchain reaction緩和を発動したが、マージ機会は18%に留まり、HEIGHT_CONTROLが44.3%を占めることを確認。chain reaction緩和は複雑性を増やすだけでなく、効果も限定的。v42のシンプルかつ頑健な構造（約100行）を維持しつつ、HIGHフェーズでの高度管理を微調整（height_mult: 2.6→2.4）でマージ機会確保を試みる。v29の失敗（スコア721、height_mult=2.4でマージできない）とv42の成功（スコア2335、height_mult=2.6）の中間値を採用し、履歴データとも整合。v42のフェーズ制バランス補正（20.0/30.0/40.0）、マージボーナス（DIRECT=1200/NEAR=600/FAR=200）、ドリフトペナルティ（30）を維持
# v82: HIGHフェーズchain reaction最適化版 - v81の失敗（スコア1293、HIGHフェーズ8ターン、HIGH_TOWERペナルティ2倍が強すぎ、赤ライン接触）を受けて、v31の「reactive_pairsによる高度管理緩和」を改良版で再導入。履歴分析でHIGHフェーズでのマージ率は62.5%だがHIGHフェーズが8ターンと短すぎることを特定。HIGH_TOWERペナルティを2.0倍→1.5倍に緩和し、reactive_pairs>=2でheight_penalty_factor=0.7に段階的緩和。reactive_pairs>=4でchain reaction中としてheight_penalty_factor=0.5に大幅緩和。これはv31の「reactive_pairs>=3」閾値を下げ、ペナルティ倍率も緩和した改良版。v81のheight_mult=2.4をv42の2.6に戻し（赤ライン防止）、HIGHフェーズでのchain reactionを活用してマージ機会を最大化。v42のシンプル構造を維持しつつ、より頻繁にchain reactionを活用


def decide(game_state: dict, analysis: dict) -> dict:
    """HIGHフェーズでchain reaction時の高度管理を段階的に緩和"""

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

    # フェーズ判定（v82: v42の閾値0.8/1.8/3.0を維持）
    if max_y < 0.8:
        phase = "LOW"
        height_mult = 1.0
        merge_mult = 1.2
    elif max_y < 1.8:
        phase = "MEDIUM"
        height_mult = 2.4  # v82: v42の2.4を維持
        merge_mult = 1.0
    elif max_y < 3.0:
        phase = "HIGH"
        height_mult = 2.6  # v82: v81の2.4からv42の2.6に戻す（赤ライン防止）
        merge_mult = 1.0
    else:
        phase = "CRITICAL"
        height_mult = 1.0  # CRITICAL: height_multなし
        merge_mult = 0.6  # v82: v42の0.6を維持

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

        # 1. マージグレードによるスコア（v82: v42の強力な値を維持）
        if merge_grade == "DIRECT":
            score += 1200.0 * merge_mult  # v82: v42の1200を維持
            reasons.append("DIRECT_MERGE")
        elif merge_grade == "NEAR":
            score += 600.0 * merge_mult  # v82: v42の600を維持
            reasons.append("NEAR_MERGE")
        elif merge_grade == "FAR":
            score += 200.0 * merge_mult  # v82: v42の200を維持
            reasons.append("FAR_MERGE")

        # 2. 高度によるペナルティ（v82: HIGHフェーズでchain reaction時の段階的緩和）
        height_penalty_factor = 1.0

        if phase == "CRITICAL":
            # CRITICALフェーズではheight_multiplier強化（v42の40.0を維持）
            height_penalty = landing_y * 40.0
            if landing_y > 1.0:
                reasons.append("CRITICAL_HEIGHT")
        else:
            # v82: HIGHフェーズでreactive_pairsに応じて段階的緩和
            if phase == "HIGH":
                if reactive_pairs >= 4:
                    height_penalty_factor = 0.5  # chain reaction中：大幅緩和
                elif reactive_pairs >= 2:
                    height_penalty_factor = 0.7  # chain reaction開始：緩和

            height_penalty = landing_y * 50.0 * height_mult * height_penalty_factor

            # 高盤面での追加ペナルティ（v82: HIGHフェーズで緩和）
            if phase == "HIGH" and landing_y > 0.5:
                height_penalty *= 1.5  # v82: v42の2.0から緩和
                reasons.append("HIGH_TOWER")
            elif phase == "MEDIUM" and landing_y > 0.5:
                height_penalty *= 1.5  # v82: v42の1.5を維持
                reasons.append("MEDIUM_TOWER")
            elif landing_y > 0.0:
                reasons.append("HIGH_LAYER")

        score -= height_penalty

        # 3. ドリフトによるペナルティ（v82: v42の一律30.0を維持）
        drift_penalty = (abs(drift_x) + drift_unc) * 30.0
        score -= drift_penalty

        # 4. 左右バランス補正（v82: v42のフェーズ制バランス補正を維持）
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

        # 5. nextNextが同じタイプなら中央寄せボーナス（v82: v42の設定を維持）
        if next_next_type == next_type:
            if phase == "CRITICAL":
                center_bonus = (
                    max(0, 1.0 - abs(x) / 2.0) * 60.0
                )  # v82: v42のCRITICAL強化
            else:
                center_bonus = max(0, 1.0 - abs(x) / 2.0) * 50.0  # v82: v42の基本値
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
