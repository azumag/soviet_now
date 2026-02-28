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
# v326: v3689構造回帰版 - v325の失敗（avg=993.5、stddev=545.3、merge_rate=14.9%）を受けて、振り子パターンを完全に破棄し、v3689（BEST:3689）の成功構造を採用。
#   v325バッチ分析から特定した問題:
#   - MEDIUMフェーズ高度管理過剰緩和: height_mult=1.2に下げたが、盤面がHIGHに到達するのが早すぎる（max_y序盤: -2.74→終盤: 1.35）
#   - PRECISION_CONTROL支配悪化: 31.7%とv324の25.7%から悪化。精度に固執しすぎている
#   - HIGH_LAYER支配維持: 18.3%で支配的。高度管理がまだ強すぎる
#   - マージボーナス一律化の失敗: v321の強力な値（DIRECT=3000）を採用したが、盤面が高くなるとマージ機会が減り効果がない
#   - スコアのばらつき極端: stddev=545.3（平均の約55%）。戦略が安定していない
#   - ベストvsワーストの差: ベスト(1762)は90ターン・merge_rate=16.7%、ワースト(360)は51ターン・merge_rate=13.7%。ターン数の差が最大要因
#   根本原因:
#   - v325の「高度抑制緩和」は逆効果。MEDIUMフェーズでheight_multを下げると、HIGHフェーズ到達が早すぎ、HIGHフェーズ（height_mult=0.0）で制御不能になる
#   - v321のマージボーナス一律強化は失敗。盤面の高さに応じたフェーズ感応化が必要
#   - 振り子パターン: v321（高度無視）→v322（高度管理強化）→v323（中間）→v324（フェーズ感応）→v325（一律強化）と、マージボーナスと高度管理の間で振り子を続けている
#   v3689成功構造の再評価:
#   - avg_score=3689という圧倒的結果（BESTスコア）
#   - MEDIUMフェーズ: height_mult=2.4で高度管理強化、HIGH到達遅延
#   - HIGHフェーズ: height_mult=1.8で高度管理緩和、マージ優先徹底
#   - バランス補正: balance_strength=20.0/40.0/30.0でフェーズ感応化
#   - マージボーナス: v42の値（DIRECT=1200/NEAR=600/FAR=200）を採用
#   - フェーズ閾値: v42の0.8/1.8/3.0を維持
#   解決策（v3689構造回帰）:
#   - v325の一律化アプローチを完全破棄。v3689のフェーズ感応化アプローチを採用
#   - MEDIUMフェーズ: height_mult=2.4に戻し、HIGH到達遅延。盤面成長を抑制し、安定性を確保
#   - HIGHフェーズ: height_mult=1.8に緩和し、マージ優先徹底。v321の「高度管理廃止」とv322の「高度管理強化」の中間を採用
#   - CRITICALフェーズ: height_mult=1.0、merge_mult=0.6でマージ絶対優先
#   - バランス補正: v3689の20.0/40.0/30.0を採用し、フェーズ感応化
#   - マージボーナス: v3689の値（DIRECT=1200/NEAR=600/FAR=200）を採用。一律強化は失敗
#   - nextNext中央寄せ: v3689の一律50.0を採用
#   核心的発見: v3689の成功は「MEDIUMで高度管理強化してHIGH到達遅延、HIGHで高度管理緩和してマージ優先」というフェーズ感応化にある。v325の一律化は振り子パターンの典型。
#   成功基準: avg_scoreがv3689の70%以上（約2500以上）、またはavg_scoreがv325の2.5倍以上（約2500以上）
#   失敗基準: avg_scoreがv325の993.5未満、または盤面崩壊で即敗北率が30%以上
# [BEST:3689] v128: HIGHフェーズマージ優先版
# [BEST:2335] v42: v19復活・v31/v29複雑化要素削除版


def decide(game_state: dict, analysis: dict) -> dict:
    """v3689構造回帰版。MEDIUMで高度管理強化してHIGH到達遅延、HIGHで高度管理緩和してマージ優先徹底。"""

    results = analysis.get("results", [])

    if not results:
        return {"x": 0.0, "reason": "no analysis data"}

    best_x = 0.0
    best_score = -float("inf")
    best_reason = ""

    # 盤面情報
    pieces = game_state.get("pieces", [])
    max_y = max([p["y"] for p in pieces]) if pieces else -4.0

    # フェーズ判定（v326: v3689の閾値0.8/1.8/3.0を採用）
    if max_y < 0.8:
        phase = "LOW"
        height_mult = 1.0  # v326: v3689の1.0を採用
        merge_mult = 1.2
    elif max_y < 1.8:
        phase = "MEDIUM"
        height_mult = 2.4  # v326: v3689の2.4を採用、HIGH到達遅延
        merge_mult = 1.0
    elif max_y < 3.0:
        phase = "HIGH"
        height_mult = 1.8  # v326: v3689の1.8を採用、高度管理緩和・マージ優先
        merge_mult = 1.0
    else:
        phase = "CRITICAL"
        height_mult = 1.0  # v326: v3689の1.0を採用
        merge_mult = 0.6  # v326: v3689の0.6を採用、マージ絶対優先

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

        # === v326: v3689構造回帰 ===

        # 1. マージグレードによるスコア（v326: v3689の値を採用）
        if merge_grade == "DIRECT":
            score += 1200.0 * merge_mult  # v326: v3689の1200を採用
            reasons.append("DIRECT_MERGE")
        elif merge_grade == "NEAR":
            score += 600.0 * merge_mult  # v326: v3689の600を採用
            reasons.append("NEAR_MERGE")
        elif merge_grade == "FAR":
            score += 200.0 * merge_mult  # v326: v3689の200を採用
            reasons.append("FAR_MERGE")
        # v326: v325の一律強化（3000/1000/300）は失敗。v3689のフェーズ感応化を採用

        # 2. 高度によるペナルティ（v326: v3689のフェーズ感応化）
        height_penalty = landing_y * 50.0 * height_mult

        # HIGH_TOWERペナルティ（v326: v3689の設定を採用）
        if phase == "HIGH" and landing_y > 0.5:
            height_penalty *= 1.3  # v326: v3689の1.3倍を採用
            reasons.append("HIGH_TOWER")
        elif phase == "MEDIUM" and landing_y > 0.5:
            height_penalty *= 1.5  # v326: v3689の1.5倍を採用
            reasons.append("MEDIUM_TOWER")
        elif landing_y > 0.0:
            reasons.append("HIGH_LAYER")

        score -= height_penalty

        # 3. ドリフトによるペナルティ（v326: v3689の一律30.0を採用）
        drift_penalty = (abs(drift_x) + drift_unc) * 30.0
        score -= drift_penalty

        # 4. 左右バランス補正（v326: v3689のフェーズ感応化）
        if phase == "HIGH":
            balance_strength = 40.0  # v326: v3689の40.0を採用
        elif phase == "MEDIUM":
            balance_strength = 30.0  # v326: v3689の30.0を採用
        else:
            balance_strength = 20.0  # v326: v3689の20.0を採用

        left_count = sum(1 for p in pieces if p["x"] < 0)
        right_count = len(pieces) - left_count
        balance_bias = (right_count - left_count) / (len(pieces) if pieces else 1)

        balance_penalty = x * balance_bias * balance_strength
        score -= abs(balance_penalty)

        # 5. nextNextが同じタイプなら中央寄せボーナス（v326: v3689の一律50.0を採用）
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
