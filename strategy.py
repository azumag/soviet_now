#!/usr/bin/env python3
"""strategy.py - AI改善対象の決定スクリプト"""

# 固定インターフェース:
# decide(game_state: dict, analysis: dict) -> dict
#    戻り値: {"x": float, "reason": str}
#
# AI改変可能: decide() 内部,ヘルパー関数,定数,import
# AI改変禁止: decide() シグネチャ,if __name__ == "__main__" ブロック

# --- 変更履歴 ---
# [BEST:2325] v19: CRITICALフェーズ導入版 - HIGHフェーズのheight_mult過剰を修正、CRITICALフェーズ（max_y>3.0）を新設。CRITICALではマージ絶対優先（merge_mult=0.6、height_multなし、height_penaltyシンプル化）。MEDIUMフェーズheight_mult微増（2.2→2.4）でHIGH到達遅延、HIGHフェーズheight_mult微減（2.8→2.6）でマージ機会確保
# [BEST:2335] v42: v19復活・v31/v29複雑化要素削除版 - v41の失敗（スコア558）を受けて、v41がv31から取り入れたreactive_pairsとhas_mergeによる複雑な条件分岐を削除。v19のシンプル構造（DIRECT=1200/NEAR=600/FAR=200、height_penalty=50*height_mult、drift_penalty=30）に復活。v19のCRITICALフェーズ（merge_mult=0.6）を維持。コード量削減（約140行→約110行）で頑健性を確保
# v50: LOW/MEDIUM高度管理強化・HIGHフェーズ高度管理維持版 - v49の失敗（スコア657、HIGH/CRITICALフェーズでスコア停滞、HIGHフェーズ20ターン中マージ3回のみ）を受けて、LOW/MEDIUMフェーズでの盤面構造改善によりHIGH/CRITICALフェーズでのマージ機会を増やすアプローチを採用。v48のheight_multiplier概念を再導入するが、HIGH/CRITICALフェーズには適用せずv42の高度管理を維持。LOWフェーズ: height_multiplier=30.0（盤面フラット化）、MEDIUMフェーズ: height_multiplier=35.0（中層の均質化）。v48の失敗（HIGHフェーズheight_multiplier緩和→赤ライン越え）を回避するため、HIGH/CRITICALフェーズはv42の高度管理を維持。NEAR_MERGEボーナスの400点は維持。v42のシンプル構造を維持しつつ、LOW/MEDIUMフェーズでの盤面構造改善でHIGH/CRITICALフェーズでのマージ機会を最大化
# v51: v42完全復活・HIGHフェーズマージ促進版 - v50の失敗（スコア667、HIGHフェーズでマージ機会0回）を受けて、v42のシンプル構造を完全復活。v48のheight_multiplierを削除（振り子パターン解消）。NEAR_MERGEボーナスをv42の600に戻す（v49の400は過度に悲観的）。HIGHフェーズでのマージ促進として、has_mergeによるdrift_penalty緩和（0.6倍）を追加（v29のアイデアを採用だが、reactive_pairsのような複雑条件は使用しない）。コード量削減（190行→約120行）でシンプルかつ頑健な構造を確保
# v52: MEDIUM高度管理緩和・HIGHマージ優先化版 - v51の失敗（スコア1020、HIGHフェーズでマージ機会を逃しmax_y=2.99で停滞）を受けて、履歴分析に基づきv42構造の微調整を行う。MEDIUMフェーズのheight_multを2.4→2.2に微減（HIGH到達遅延、v28の成功事例を参考）。HIGHフェーズでのマージ優先度を上げるため、NEAR_MERGEボーナスを600→700に微増（v12の750成功事例を参考、過度な増加は避ける）。HIGH_TOWERペナルティの閾値を0.5→0.8に緩和し、中程度の高度位置でのマージ機会を活用。v42のシンプル構造とdrift_penalty緩和（0.6倍）を維持。コード量維持（約120行）で頑健性を確保


def decide(game_state: dict, analysis: dict) -> dict:
    """v42のシンプル構造をベースに、MEDIUMフェーズ高度管理緩和とHIGHフェーズマージ優先化を実施"""

    results = analysis.get("results", [])

    if not results:
        return {"x": 0.0, "reason": "no analysis data"}

    best_x = 0.0
    best_score = -float("inf")
    best_reason = ""

    # 盤面情報
    pieces = game_state.get("pieces", [])
    max_y = max([p["y"] for p in pieces]) if pieces else -4.0

    # フェーズ判定（v52: MEDIUMのheight_multのみ微調整）
    if max_y < 0.8:
        phase = "LOW"
        height_mult = 1.0
        merge_mult = 1.2
    elif max_y < 1.8:
        phase = "MEDIUM"
        height_mult = 2.2  # v52: v42の2.4から2.2に微減（HIGH到達遅延）
        merge_mult = 1.0
    elif max_y < 3.0:
        phase = "HIGH"
        height_mult = 2.6  # v52: v42の2.6を維持
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
        has_merge = result.get("has_merge", False)

        score = 0.0
        reasons = []

        # 1. マージグレードによるスコア（v52: NEAR_MERGEボーナスを700に微増）
        if merge_grade == "DIRECT":
            score += 1200.0 * merge_mult
            reasons.append("DIRECT_MERGE")
        elif merge_grade == "NEAR":
            score += 700.0 * merge_mult  # v52: v42の600から700に微増
            reasons.append("NEAR_MERGE")
        elif merge_grade == "FAR":
            score += 200.0 * merge_mult
            reasons.append("FAR_MERGE")

        # 2. 高度によるペナルティ（v52: HIGH_TOWER閾値を0.5→0.8に緩和）
        height_penalty = landing_y * 50.0 * height_mult

        if phase == "HIGH" and landing_y > 0.8:  # v52: 0.5から0.8に緩和
            height_penalty *= 2.0
            reasons.append("HIGH_TOWER")
        elif phase == "MEDIUM" and landing_y > 0.5:
            height_penalty *= 1.5
            reasons.append("MEDIUM_TOWER")
        elif landing_y > 0.0:
            reasons.append("HIGH_LAYER")

        score -= height_penalty

        # 3. ドリフトによるペナルティ（v52: HIGHフェーズでhas_mergeなら0.6に緩和）
        drift_penalty_factor = 1.0
        if phase == "HIGH" and has_merge:
            drift_penalty_factor = 0.6  # マージ機会確保

        drift_penalty = (abs(drift_x) + drift_unc) * 30.0 * drift_penalty_factor
        score -= drift_penalty

        # 4. 左右バランス補正（v52: v42と同じ）
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

        # 5. nextNextが同じタイプなら中央寄せボーナス（v52: v42と同じ）
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
