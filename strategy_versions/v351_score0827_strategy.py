#!/usr/bin/env python3
"""strategy.py - AI改善対象の決定スクリプト"""

# 固定インターフェース:
# decide(game_state: dict, analysis: dict) -> dict
#    戻り値: {"x": float, "reason": str}
#
# AI改変可能: decide() 内部、ヘルパー関数、定数、import
# AI改変禁止: decide() シグネチャ、if __name__ == "__main__" ブロック

# --- 変更履歴 ---
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
# v327: タイプ別高度ボーナス・マージ先読み版 - v326の失敗（avg=693.2、HEIGHT_CONTROL支配31.0%）を受けて、v3689の「形」のコピーではなく「本質」を採用。
#   v326バッチ分析から特定した問題:
#   - HEIGHT_CONTROL支配: 31.0%で支配的。マージ以外の戦略が支配的で、マージ率が低い（高スコア群10.4%、低スコア群8.7%）
#   - マージボーナス不足: v3689の値（DIRECT=1200）を採用したが、盤面が高くなるとマージ機会が減り効果がない
#   - ターン数差が最大要因: ベスト82ターン・merge_rate=8.5% vs ワースト53ターン・merge_rate=5.7%。ターン数の差がスコア差の最大要因
#   - max_y推移の違い: 高スコア群は終盤avg=1.14、低スコア群は終盤avg=1.67。低スコア群の方が盤面が高くなっている
#   - 振り子パターン継続: v321→v322→v323→v324→v325→v326と、マージボーナスと高度管理の間で振り子を続けている
#   根本原因:
#   - v326はv3689の「形」（マージボーナスの値、height_multの値）をコピーしたが、成功の「本質」を理解していない
#   - v3689が成功した真の理由は: (1) 盤面が高くなりすぎないように制御しつつマージ機会を逃さないバランス、(2) 小さいピースを下層に、大きいピースを上層に配置する物理的なピース配置、(3) 振動マージや連鎖を狙う動的な戦略
#   - v326はこれらを考慮しておらず、単純にマージボーナスと高度ペナルティのバランスを調整しただけ
#   解決策（タイプ別高度ボーナス・マージ先読み）:
#   - 小さいピースを下層に配置するボーナスを導入: ピースタイプが小さいほど、下層でマージした時に大きなボーナスを与える（type 1でlanding_y=-4.0時+400、type 12でlanding_y=0.0時+0）
#   - 次のピースのマージ先読み戦略を導入: next/type Nなら、盤面のtype N-1ピースの重心付近にボーナスを与え、将来のマージを促進（重心距離1.0以下で+200、0.5以下で+400）
#   - 次の次のピースも考慮: nextNext/type Mなら、盤面のtype M-1ピースの重心付近にボーナスを与え、次のターンの連鎖マージを促進（重心距離1.0以下で+100、0.5以下で+200）
#   - HIGH_TOWERペナルティを緩和: v128の1.3倍を採用し、高スコア群のmax_y=1.14程度を許容（v42の2.0倍は過剰）
#   - v3689のフェーズ感応化を維持: height_mult（MEDIUM=2.4/HIGH=1.8）とマージボーナス（DIRECT=1200/NEAR=600/FAR=200）は維持
#   核心的発見: マージの「質」を高めるために、(1) 小さいピースは下層でマージさせるべき（物理的に重いので下に沈むべき）、(2) 次のピースがtype Nなら、今のうちにtype N-1の近くに置いて将来のマージを予約する。これらによりマージ率を上げ、ターン数を増やし、スコアを向上させる。
#   成功基準: avg_scoreが1000以上、またはマージ決定率が15%以上、またはavg_turnsが70以上
#   失敗基準: avg_scoreがv326の693.2未満、または盤面崩壊で即敗北率が30%以上
# v328: HIGHフェーズマージ強化・v42ベース版 - v327の失敗（avg=1095.2、新機能が機能していない）を受けて、v327の複雑な要素を削除し、v128とv42の成功要素を融合。
#   v327バッチ分析から特定した問題:
#   - v327の新機能が機能していない: NEXT_MERGE、NEXT_NEXT_MERGE、SMALL_PIECE_DEEP が上位10に入っていない
#   - HEIGHT_CONTROL支配: 35.4%が支配的。マージ戦略が有効に機能していない
#   - 高スコア群 vs 低スコア群の比較: 高スコア群はmerge_rate=13.8%、低スコア群はmerge_rate=17.3%。低スコア群の方がmerge_rateが高いのにスコアが低い
#   - マージボーナス不足: v327はv3689の値（DIRECT=1200）を採用したが、これではマージを十分に促進できない
#   - HIGH_TOWERペナルティが緩和しすぎ: v128の1.3倍を採用したが、これはv42の2.0倍より弱すぎる
#   根本原因:
#   - v327の「タイプ別高度ボーナス・マージ先読み」は、実際のプレイではほとんど使用されず、単にコードを複雑にしただけ
#   - v327はv321の一律強化をタイプ別ボーナスで実装し直したに過ぎない。振り子パターンが続いている
#   - v128のHIGH_TOWERペナルティ1.3倍は、v42の2.0倍より緩和しすぎ、高い配置を十分に抑制できていない
#   - マージボーナス一律強化は失敗（v321、v325）。HIGHフェーズ限定強化が必要
#   解決策（HIGHフェーズマージ強化・v42ベース版）:
#   - v327の複雑な要素を削除: タイプ別高度ボーナス・マージ先読みを削除し、v42のシンプル構造に戻る
#   - v128のHIGHフェーズ高度管理緩和を採用: HIGHフェーズでheight_mult=1.8とし、マージ優先を徹底
#   - v42のHIGH_TOWERペナルティを復活: v128の1.3倍は緩和しすぎ。v42の2.0倍を採用し、HIGHフェーズでの高すぎる配置を強く抑制
#   - HIGHフェーズのみマージボーナス強化: 全フェーズ一律強化は失敗。HIGHフェーズに限ってマージボーナスを強化（merge_mult=1.5）、盤面が高い時だけマージを強力に推す
#   - v128のバランス補正・nextNext中央寄せを採用: balance_strengthと中央寄せボーナスはv128の値を採用
#   核心的発見: v327の失敗は「複雑化」にある。v128とv42の成功要素を融合し、シンプルかつ効果的なアプローチを採用。HIGHフェーズでのマージ強化は「一律強化」ではなく「フェーズ限定強化」で行う。
#   成功基準: avg_scoreが1500以上、またはマージ決定率が20%以上、またはavg_turnsが70以上
#   失敗基準: avg_scoreがv327の1095.2未満、または盤面崩壊で即敗北率が30%以上
# [BEST:3689] v128: HIGHフェーズマージ優先版
# [BEST:2335] v42: v19復活・v31/v29複雑化要素削除版


def decide(game_state: dict, analysis: dict) -> dict:
    """HIGHフェーズマージ強化・v42ベース版。v327の複雑な要素を削除し、v128とv42の成功要素を融合。"""

    results = analysis.get("results", [])

    if not results:
        return {"x": 0.0, "reason": "no analysis data"}

    best_x = 0.0
    best_score = -float("inf")
    best_reason = ""

    # 盤面情報
    pieces = game_state.get("pieces", [])
    max_y = max([p["y"] for p in pieces]) if pieces else -4.0

    # フェーズ判定（v328: v128の閾値0.8/1.8/3.0を維持）
    if max_y < 0.8:
        phase = "LOW"
        height_mult = 1.0  # v328: v128の1.0を維持
        merge_mult = 1.2
    elif max_y < 1.8:
        phase = "MEDIUM"
        height_mult = 2.4  # v328: v128の2.4を維持、HIGH到達遅延
        merge_mult = 1.0
    elif max_y < 3.0:
        phase = "HIGH"
        height_mult = 1.8  # v328: v128の1.8を維持、高度管理緩和・マージ優先
        merge_mult = 1.5  # v328: HIGHフェーズのみマージボーナス強化（一律強化は失敗）
    else:
        phase = "CRITICAL"
        height_mult = 1.0  # v328: v128の1.0を維持
        merge_mult = 0.6  # v328: v128の0.6を維持、マージ絶対優先

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

        # === v328: HIGHフェーズマージ強化・v42ベース版 ===

        # 1. マージグレードによるスコア（v328: v42の値を採用、HIGHフェーズのみ強化）
        if merge_grade == "DIRECT":
            score += 1200.0 * merge_mult  # v328: v42の1200を維持、HIGHフェーズでは1.5倍
            reasons.append("DIRECT_MERGE")
        elif merge_grade == "NEAR":
            score += 600.0 * merge_mult  # v328: v42の600を維持、HIGHフェーズでは1.5倍
            reasons.append("NEAR_MERGE")
        elif merge_grade == "FAR":
            score += 200.0 * merge_mult  # v3328: v42の200を維持、HIGHフェーズでは1.5倍
            reasons.append("FAR_MERGE")
        # v328: マージボーナス一律強化は失敗。HIGHフェーズ限定強化を採用

        # 2. 高度によるペナルティ（v328: v128のフェーズ感応化を維持）
        height_penalty = landing_y * 50.0 * height_mult

        # HIGH_TOWERペナルティ（v328: v42の2.0倍を採用、v128の1.3倍は緩和しすぎ）
        if phase == "HIGH" and landing_y > 0.5:
            height_penalty *= 2.0  # v328: v42の2.0倍を採用、v128の1.3倍は緩和しすぎ
            reasons.append("HIGH_TOWER")
        elif phase == "MEDIUM" and landing_y > 0.5:
            height_penalty *= 1.5  # v328: v42の1.5倍を維持
            reasons.append("MEDIUM_TOWER")
        elif landing_y > 0.0:
            reasons.append("HIGH_LAYER")

        score -= height_penalty

        # 3. ドリフトによるペナルティ（v328: v42の一律30.0を維持）
        drift_penalty = (abs(drift_x) + drift_unc) * 30.0
        score -= drift_penalty

        # 4. 左右バランス補正（v328: v128のフェーズ感応化を維持）
        if phase == "HIGH":
            balance_strength = 40.0  # v328: v128の40.0を維持
        elif phase == "MEDIUM":
            balance_strength = 30.0  # v328: v128の30.0を維持
        else:
            balance_strength = 20.0  # v328: v128の20.0を維持

        left_count = sum(1 for p in pieces if p["x"] < 0)
        right_count = len(pieces) - left_count
        balance_bias = (right_count - left_count) / (len(pieces) if pieces else 1)

        balance_penalty = x * balance_bias * balance_strength
        score -= abs(balance_penalty)

        # 5. nextNextが同じタイプなら中央寄せボーナス（v328: v128の一律50.0を維持）
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
