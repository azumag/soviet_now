#!/usr/bin/env python3
"""strategy.py - AI改善対象の決定スクリプト (v542: v540/v128-based simplified version)"""

# 固定インターフェース:
# decide(game_state: dict, analysis: dict) -> dict
#    戻り値: {"x": float, "reason": str}
#
# AI改変可能: decide() 内部、ヘルパー関数、定数、import
# AI改変禁止: decide() シグネチャ、if __name__ == "__main__" ブロック

# --- 変更履歴 ---
# v549: v542バランス補正強化・高度管理強化版 - v542の失敗（score=1553、max_y=3.74でCRITICALフェーズ突入）を受けて、根本原因を特定し改善。v542の問題点：（1）バランス補正が10.0と弱すぎて盤面の左側に片寄り、（2）HIGH_TOWERペナルティが1.3倍で高度管理が不十分。v2335（2325点）とv3689（3689点）の成功戦略を分析し、バランス補正を20.0/30.0/40.0に強化、HIGH_TOWERペナルティを1.5倍に強化。v128のheight_mult=1.8はv3689で成功しているため維持。v542のマージボーナス（DIRECT=1200/NEAR=600/FAR=200）とドリフトペナルティ（20.0）は維持。根本原因の特定: v542ではバランス補正が10.0と弱く、max_yが3.74まで上昇してCRITICALフェーズに突入。HIGH_TOWERペナルティも1.3倍で、MEDIUMフェーズの1.5倍より弱い矛盾がある。改善策: （1）バランス補正強化：初期値を20.0に戻す（v2335/v3689の成功値）、（2）HIGH_TOWERペナルティ強化：1.3倍→1.5倍にしてMEDIUMと同じ強さに、（3）v128のheight_mult=1.8維持：v3689で成功している設定を活かす。v542のマージ優先の構造は維持しつつ、バランス制御と高度管理を強化して盤面崩壊を防止。
#   成功基準: scoreがv542の1553を上回る、またはmax_yが3.0以下に収まる
#   失敗基準: scoreがv542以下、またはmax_yが3.74以上


def decide(game_state: dict, analysis: dict) -> dict:
    """v549: v542バランス補正強化・高度管理強化版"""

    results = analysis.get("results", [])

    if not results:
        return {"x": 0.0, "reason": "no analysis data"}

    best_x = 0.0
    best_score = -float("inf")
    best_reason = ""

    # 盤面情報
    pieces = game_state.get("pieces", [])
    max_y = max([p["y"] for p in pieces]) if pieces else -4.0

    # フェーズ判定（v549: v542の閾値0.8/1.8/3.0を維持）
    if max_y < 0.8:
        phase = "LOW"
        height_mult = 1.0
        merge_mult = 1.2
    elif max_y < 1.8:
        phase = "MEDIUM"
        height_mult = 2.4  # v549: v542の2.4を維持
        merge_mult = 1.0
    elif max_y < 3.0:
        phase = "HIGH"
        height_mult = 1.8  # v549: v542の1.8を維持（v3689成功値）
        merge_mult = 1.0
    else:
        phase = "CRITICAL"
        height_mult = 1.0  # CRITICAL: height_multなし
        merge_mult = 0.6  # v549: v542の0.6を維持

    # nextNextピース情報
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

        # === v549: バランス補正強化・高度管理強化 ===

        # 1. マージグレードによるスコア（v549: v542の値を維持）
        if merge_grade == "DIRECT":
            score += 1200.0 * merge_mult
            reasons.append("DIRECT_MERGE")
        elif merge_grade == "NEAR":
            score += 600.0 * merge_mult
            reasons.append("NEAR_MERGE")
        elif merge_grade == "FAR":
            score += 200.0 * merge_mult
            reasons.append("FAR_MERGE")

        # 2. 高度によるペナルティ（v549: v542の設定を維持）
        height_penalty = landing_y * 50.0 * height_mult

        # HIGH_TOWERペナルティ（v549: v542の1.3倍→v2335/v3689参考に1.5倍に強化）
        if phase == "HIGH" and landing_y > 0.5:
            height_penalty *= (
                1.5  # v549: 1.3倍→1.5倍に強化（MEDIUMと同じ強さで高度管理重視）
            )
            reasons.append("HIGH_TOWER")
        elif phase == "MEDIUM" and landing_y > 0.5:
            height_penalty *= 1.5  # v549: v542の1.5倍を維持
            reasons.append("MEDIUM_TOWER")
        elif landing_y > 0.0:
            reasons.append("HIGH_LAYER")

        score -= height_penalty

        # 3. ドリフトによるペナルティ（v549: v542の20.0を維持）
        drift_penalty = (abs(drift_x) + drift_unc) * 20.0
        score -= drift_penalty

        # 4. 左右バランス補正（v549: v542の10.0→v2335/v3689の20.0に強化）
        balance_strength = 20.0  # v549: v542の10.0→20.0に強化（v2335/v3689成功値）
        if phase == "HIGH":
            balance_strength = 40.0  # v549: v542の40.0を維持
        elif phase == "MEDIUM":
            balance_strength = 30.0  # v549: v542の20.0→30.0に強化（v2335成功値）

        left_count = sum(1 for p in pieces if p["x"] < 0)
        right_count = len(pieces) - left_count
        balance_bias = (right_count - left_count) / (len(pieces) if pieces else 1)

        balance_penalty = x * balance_bias * balance_strength
        score -= abs(balance_penalty)

        # 5. nextNextが同じタイプなら中央寄せボーナス（v549: v542の設定を維持）
        if next_next_type == next_type:
            center_bonus = max(0, 1.0 - abs(x) / 2.0) * 50.0
            score += center_bonus
            reasons.append("NEXT_SAME")

        # 6. type N-1の存在による追加ボーナス（v549: v542の設定を維持）
        if next_type > 1:
            prev_type_pieces = [p for p in pieces if p["type"] == next_type - 1]
            if len(prev_type_pieces) >= 1:
                score += next_type * 5.0  # v549: v542のtype * 5.0を維持
                reasons.append("TYPE_PREV")

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
