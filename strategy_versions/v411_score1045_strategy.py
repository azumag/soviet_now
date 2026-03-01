#!/usr/bin/env python3
"""strategy.py - AI改善対象の決定スクリプト"""

# 固定インターフェース:
# decide(game_state: dict, analysis: dict) -> dict
#    戻り値: {"x": float, "reason": str}
#
# AI改変可能: decide() 内部、ヘルパー関数、定数、import
# AI改変禁止: decide() シグネチャ、if __name__ == "__main__" ブロック

# --- 変更履歴 ---
# v410: v128完全復帰・reactive_pairs完全削除版 - v409の失敗（score=824、9ターンで終了、merge_rate=11.1%）を受けて、振り子パターンを完全回避。v409で導入したreactive_pairs動的高度管理が逆効果であったことを確認。ベストスコア達成時（v128:3689、v2346:2346）の共通点は「シンプルな固定構造」であることが判明。reactive_pairs情報を活用する動的調整は過度な複雑化と判断。v128の成功要素を完全復帰：HIGHフェーズheight_mult=1.8固定、マージボーナス強化（DIRECT=1500/NEAR=800/FAR=300）、左右バランス補正、ドリフトペナルティ30.0、nextNext中央寄せボーナス。reactive_pairsを全く使用しないシンプル構造で安定した高スコアを目指す。
#   根本原因の特定:
#   - v409はreactive_pairs>=5で動的盤面と判断し、height_mult=1.5に緩和したが、実際にはマージ機会が増えなかった
#   - 最新ゲーム（score=824、9ターン）でreactive_pairs>=5のターンが3ターンあり、いずれもmerge_available=false
#   - reactive_pairsが多い＝マージが連鎖しやすいというv341の発見が、実データでは裏付けられていなかった
#   - v409の動的高度管理（height_mult=1.5/HIGH_TOWER=1.1x）は、マージを優先するのに十分ではなかった
#   - v128（score=3689）の成功はシンプル構造と即時的判断に基づいている
#   改善策（v128完全復帰・振り子パターン完全回避）:
#   - reactive_pairs情報を全く使用しない（v409の動的高度管理を削除）
#   - v128のシンプル構造を完全復帰: HIGHフェーズheight_mult=1.8固定、マージボーナス強化（DIRECT=1500/NEAR=800/FAR=300）、左右バランス補正、ドリフトペナルティ30.0、nextNext中央寄せボーナス
#   - フェーズ判定閾値をv128の0.8/1.8/3.0に維持
#   - MEDIUM/HIGHフェーズのbalance_strengthをv128の設定に復帰（MEDIUM=30.0/HIGH=40.0）
#   - CRITICALフェーズのmerge_mult=0.6を維持
#   核心的発見: reactive_pairs情報を活用する動的調整は過度な複雑化であり、v128のシンプルな固定構造が最も安定して高いスコアを達成できる。振り子パターン（reactive_pairs動的調整の追加・削除）を完全に回避する。
#   成功基準: scoreがv128の3689に近い、またはmerge_rateが25%以上
#   失敗基準: scoreが824未満、またはreactive_pairsが使用される


def decide(game_state: dict, analysis: dict) -> dict:
    """v128のシンプル構造を完全復帰し、reactive_pairs情報を全く使用しない。即時的な判断（マージ・高度管理・バランス）のみに集中。振り子パターンを完全回避。"""

    results = analysis.get("results", [])

    if not results:
        return {"x": 0.0, "reason": "no analysis data"}

    best_x = 0.0
    best_score = -float("inf")
    best_reason = ""

    # 盤面情報
    pieces = game_state.get("pieces", [])
    max_y = max([p["y"] for p in pieces]) if pieces else -4.0

    # nextNextピース情報（中央寄せボーナス計算用）
    next_piece = game_state.get("next", {})
    next_next_piece = game_state.get("nextNext", {})
    next_type = next_piece.get("type", 0)
    next_next_type = next_next_piece.get("type", 0)

    # フェーズ判定（v410: v128の閾値0.8/1.8/3.0を維持）
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
        # v410: v128の1.8を完全固定（reactive_pairs動的調整を削除）
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

        # === v410: v128完全復帰・reactive_pairs完全削除 ===

        # 1. マージグレードによるスコア（v410: v128の強力な値を維持）
        if merge_grade == "DIRECT":
            score += 1500.0 * merge_mult
            reasons.append("DIRECT_MERGE")
        elif merge_grade == "NEAR":
            score += 800.0 * merge_mult
            reasons.append("NEAR_MERGE")
        elif merge_grade == "FAR":
            score += 300.0 * merge_mult
            reasons.append("FAR_MERGE")

        # 2. 高度によるペナルティ（v410: v128の固定値）
        height_penalty = landing_y * 50.0 * height_mult

        # HIGH_TOWERペナルティ（v410: v128の1.3倍を維持）
        if phase == "HIGH" and landing_y > 0.5:
            height_penalty *= 1.3
            reasons.append("HIGH_TOWER")
        elif phase == "MEDIUM" and landing_y > 0.5:
            height_penalty *= 1.3
            reasons.append("MEDIUM_TOWER")
        elif landing_y > 0.0:
            reasons.append("HIGH_LAYER")

        score -= height_penalty

        # 3. ドリフトによるペナルティ（v410: v128の一律30.0を維持）
        drift_penalty = (abs(drift_x) + drift_unc) * 30.0
        score -= drift_penalty

        # 4. 左右バランス補正（v410: v128の設定を維持）
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

        # 5. nextNextが同じタイプなら中央寄せボーナス（v410: v128の設定を維持）
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
