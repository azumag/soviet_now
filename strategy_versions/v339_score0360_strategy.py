#!/usr/bin/env python3
"""strategy.py - AI改善対象の決定スクリプト"""

# 固定インターフェース:
# decide(game_state: dict, analysis: dict) -> dict
#    戻り値: {"x": float, "reason": str}
#
# AI改変可能: decide() 内部、ヘルパー関数、定数、import
# AI改変禁止: decide() シグネチャ、if __name__ == "__main__" ブロック

# --- 変更履歴 ---
# v325: 高度抑制緩和・中間フェーズの発見 - v324の失敗（avg=961.0、merge_rate=15.7%）を受けて、振り子パターンを打破する根本的なアプローチを検討。
#   v324バッチ分析から特定した問題:
#   - MEDIUMフェーズ高度管理過剰: height_mult=2.4によりHIGH_LAYERが22.6%で支配的。盤面成長を抑制しすぎてmerge機会を損失
#   - マージボーナス不足: MEDIUMフェーズでDIRECT=1200は小さすぎ、マージを十分に促進できていない
#   - ベスト vs ワーストの比較: ベストは78ターン・19.2% merge率、ワーストは59ターン・13.6%。ターン数の差がスコア差の最大要因
#   - PRECISION_CONTROL支配: 25.7%でまだ支配的。高度管理とマージボーナスのバランスが悪く、「マージしないで低い位置に置く」が最適解になっている
#   根本原因:
#   - v324はv321（高度無視）とv322（高度管理強化）の「中間」を目指したが、両方の弱点を引き継いだ
#   - MEDIUMフェーズのheight_mult=2.4はv322の設定だが、HIGHフェーズでのマージ優先（height_mult=0.0）との対比が強すぎる
#   - 0.8-1.8という「中間フェーズ」の存在自体が疑わしい。LOW（0.8未満）からHIGH（1.8以上）への急遷移が自然か？
#   解決策（高度抑制緩和・中間フェーズの発見）:
#   - MEDIUMフェーズの高度管理大幅緩和: height_multを2.4から1.2に半減し、盤面成長を許容。HIGH到達を早め、merge機会を増やす
#   - マージボーナス全フェーズ一律化: v321の強力な値（DIRECT=3000/NEAR=1000/FAR=300）を採用し、merge_gradeボーナスを圧倒的に優先
#   - HIGHフェーズの高度管理廃止維持: v324のheight_mult=0.0は維持し、HIGHではマージのみを追う
#   - CRITICALフェーズの即時崩壊防止: height_mult=0.5を維持し、max_y>3.0での急上昇を抑制
#   - バランス補正一律化維持: v324のbalance_strength=15.0は有効なので維持し、シンプルさを確保
#   - nextNext中央寄せボーナス維持: v324の50.0ボーナスは有効なので維持
#   核心的発見: MEDIUMフェーズを「盤面成長を許容しつつmergeを促進するフェーズ」と定義し直す。height_mult=1.2とmerge_mult=1.0のバランスで、盤面をHIGHへ成長させつつmerge機会を確保。
#   成功基準: avg_scoreが1500以上、またはマージ決定率が20%以上
#   失敗基準: avg_scoreがv324の961.0未満、または盤面崩壊で即敗北率が30%以上
# [BEST:3689] v128: HIGHフェーズマージ優先版
# [BEST:2335] v42: v19復活・v31/v29複雑化要素削除版


def decide(game_state: dict, analysis: dict) -> dict:
    """高度抑制緩和・中間フェーズの発見版。MEDIUMフェーズの高度管理を緩和し、盤面成長を許容してmerge機会を増やす。"""

    results = analysis.get("results", [])

    if not results:
        return {"x": 0.0, "reason": "no analysis data"}

    best_x = 0.0
    best_score = -float("inf")
    best_reason = ""

    # 盤面情報
    pieces = game_state.get("pieces", [])
    max_y = max([p["y"] for p in pieces]) if pieces else -4.0

    # フェーズ判定（v325: MEDIUMフェーズの高度管理を大幅緩和）
    if max_y < 0.8:
        phase = "LOW"
        height_mult = 0.0
        merge_mult = 1.2
    elif max_y < 1.8:
        phase = "MEDIUM"
        height_mult = 1.2  # v325: v324の2.4から1.2に半減、盤面成長を許容
        merge_mult = 1.0
    elif max_y < 3.0:
        phase = "HIGH"
        height_mult = 0.0  # v325: v324通り高度管理廃止、マージ優先
        merge_mult = 1.0
    else:
        phase = "CRITICAL"
        height_mult = 0.5  # v325: v324の0.5を維持、即時崩壊防止
        merge_mult = 1.0

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

        # === v325: 高度抑制緩和・中間フェーズの発見 ===

        # 1. マージグレードによるスコア（v325: v321の強力な値を一律採用）
        if merge_grade == "DIRECT":
            score += 3000.0 * merge_mult  # v325: v321の3000を採用、圧倒的優先
            reasons.append("DIRECT_MERGE")
        elif merge_grade == "NEAR":
            score += 1000.0 * merge_mult  # v325: v321の1000を採用
            reasons.append("NEAR_MERGE")
        elif merge_grade == "FAR":
            score += 300.0 * merge_mult  # v325: v321の300を採用
            reasons.append("FAR_MERGE")
        # v325: フェーズ感応化を廃止し、一律の強力なボーナスでmergeを促進

        # 2. 高度によるペナルティ（v325: MEDIUMフェーズの高度管理を大幅緩和）
        height_penalty = landing_y * 50.0 * height_mult

        # HIGH_TOWERペナルティ（v325: MEDIUMフェーズでも緩和）
        if phase == "HIGH" and landing_y > 0.5:
            # v325: HIGHフェーズでは高度管理廃止なのでHIGH_TOWERペナルティなし
            pass
        elif phase == "MEDIUM" and landing_y > 0.5:
            height_penalty *= 1.3  # v325: v324の1.5倍から1.3倍に緩和
            reasons.append("MEDIUM_TOWER")
        elif phase == "CRITICAL" and landing_y > 0.5:
            height_penalty *= 1.1  # v325: v324の1.1倍を維持
            reasons.append("CRITICAL_TOWER")
        elif landing_y > 0.0:
            reasons.append("HIGH_LAYER")

        score -= height_penalty

        # 3. ドリフトによるペナルティ（v325: v324の一律30.0を維持）
        drift_penalty = (abs(drift_x) + drift_unc) * 30.0
        score -= drift_penalty

        # 4. 左右バランス補正（v325: v324の一律15.0を維持）
        balance_strength = 15.0

        left_count = sum(1 for p in pieces if p["x"] < 0)
        right_count = len(pieces) - left_count
        balance_bias = (right_count - left_count) / (len(pieces) if pieces else 1)

        balance_penalty = x * balance_bias * balance_strength
        score -= abs(balance_penalty)

        # 5. nextNextが同じタイプなら中央寄せボーナス（v325: v324の50.0を維持）
        if next_next_type == next_type:
            center_bonus = max(0, 1.0 - abs(x) / 2.0) * 50.0
            score += center_bonus
            reasons.append("NEXT_SAME")

        # スコア更新
        if score > best_score:
            best_score = score
            best_x = x
            best_reason = "_".join(reasons) if reasons else "PRECISION_CONTROL"

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
