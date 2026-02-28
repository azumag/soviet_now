#!/usr/bin/env python3
"""strategy.py - AI改善対象の決定スクリプト"""

# 固定インターフェース:
# decide(game_state: dict, analysis: dict) -> dict
#    戻り値: {"x": float, "reason": str}
#
# AI改変可能: decide() 内部、ヘルパー関数、定数、import
# AI改変禁止: decide() シグネチャ、if __name__ == "__main__" ブロック

# --- 変更履歴 ---
# [BEST:3689] v128: HIGHフェーズマージ優先版 - v127の失敗（スコア724、HIGHフェーズ10ターン中9ターンでマージ不可）を受けて、HIGHフェーズでのマージ機会損失を特定。履歴分析でv127の高度管理がHIGHフェーズで過剰に強化されていることが原因を特定（HIGHフェーズのdecision_reasonはHIGH_TOWERが1回だが、HIGH_LAYERが5回で高度管理が支配的）。（1）HIGHフェーズ高度管理大幅緩和：height_multをv42の2.6から1.8に大幅に引き下げ（v84の2.2よりも緩和し、マージ優先を徹底）。（2）マージボーナス強化：v42の強力な値（DIRECT=1200/NEAR=600/FAR=200）を維持し、高度管理緩和と相乗効果。（3）HIGHフェーズHIGH_TOWERペナルティ緩和：v84の1.3倍を維持し、height_mult大幅緩和と相乗効果。（4）v42のシンプル構造を維持：NO_MERGEペナルティの「入れるか入れないか」の振り子を回避し、第三の選択肢（マージボーナス強化・高度管理大幅緩和）を採用。振り子パターン（NO_MERGEペナルティ、height_multiplier微調整）をHIGHフェーズでのマージ優先徹底で解消。コード量維持（約110行）。
# v172-v174: TOWERペナルティ振り子パターン（復帰→緩和→削除→復帰）
# v205-v207: 指数関数マージボーナススケーリングの失敗パターン - v205（スコア702）、v206（スコア1176）、v207（スコア1710）で、指数スケーリング（2x~50x）による過剰なマージインセンティブが、盤面の急激な上昇を引き起こし、HIGH_TOWERペナルティを過剰にトリガー。MEDIUMフェーズのheight_mult=2.4（v207）はMEDIUM→HIGHへの遷移を急激にし、HIGHフェーズでのマージ機会を損失。
# v208: v128回帰・MEDIUM遷移改善版 - v205-v207の失敗（指数スケーリング過剰、HIGH_TOWER100%発動、CRITICAL到達）を受けて、振り子パターンを回避しv128の成功構造への回帰を実施。v207履歴分析で特定した問題: - 指数スケーリング（2x~50x）が過剰で盤面が高くなりすぎた - MEDIUMフェーズheight_mult=2.4がMEDIUM→HIGH遷移を急激にし、HIGHフェーズ期間が短縮 - CRITICALフェーズ到達（max_y=3.54）でゲーム終了 - HIGH_TOWERがHIGHフェーズで100%発動しマージ機会を損失 根本原因: - v128の成功構造（固定マージボーナス、シンプルなルール）を複雑な指数スケーリングで破壊した - MEDIUMフェーズのheight_mult=2.4はv42の遺産で、HIGHへの遷移を急激にする - v128のシンプルさが重要：固定マージボーナス（DIRECT=1200/NEAR=600/FAR=200）、height_mult=1.8（HIGH/LOW）、フェーズごとの明確なルール 解決策（振り子パターン解消のブレイクスルー）: - 指数関数スケーリングを完全に削除：v128の固定マージボーナスに回帰（DIRECT=1200/NEAR=600/FAR=200） - MEDIUMフェーズheight_multを2.4→1.8に引き下げ：MEDIUM→HIGH遷移を滑らかにし、HIGHフェーズ期間を確保 - v128の成功構造を維持：HIGHフェーズheight_mult=1.8、TOWER閾値0.5、HIGH_TOWERペナルティ1.3倍（v207の0.5倍緩和は失敗） - v128のバランス補正強度（HIGH=40.0/MEDIUM=30.0/LOW=20.0）を維持 - v128のMEDIUM_TOWERペナルティ（1.5倍）を維持 - ドリフトペナルティ一律30.0を維持 - nextNextが同じタイプなら中央寄せボーナス50.0を維持 - v128のシンプル構造（約110行）を完全に回帰し、MEDIUM height_multの調整のみ実施


def decide(game_state: dict, analysis: dict) -> dict:
    """v128回帰・MEDIUM遷移改善版

    v205-v207の失敗（指数スケーリング過剰、HIGH_TOWER100%発動、CRITICAL到達）を受けて、
    v128の成功構造への回帰を実施。

    v207履歴分析で特定した問題:
    - 指数スケーリング（2x~50x）が過剰で盤面が高くなりすぎた
    - MEDIUMフェーズheight_mult=2.4がMEDIUM→HIGH遷移を急激にし、HIGHフェーズ期間が短縮
    - CRITICALフェーズ到達（max_y=3.54）でゲーム終了
    - HIGH_TOWERがHIGHフェーズで100%発動しマージ機会を損失

    根本原因:
    - v128の成功構造（固定マージボーナス、シンプルなルール）を複雑な指数スケーリングで破壊した
    - MEDIUMフェーズのheight_mult=2.4はv42の遺産で、HIGHへの遷移を急激にする
    - v128のシンプルさが重要：固定マージボーナス、height_mult=1.8、フェーズごとの明確なルール

    解決策（振り子パターン解消のブレイクスルー）:
    - 指数関数スケーリングを完全に削除：v128の固定マージボーナスに回帰（DIRECT=1200/NEAR=600/FAR=200）
    - MEDIUMフェーズheight_multを2.4→1.8に引き下げ：MEDIUM→HIGH遷移を滑らかにし、HIGHフェーズ期間を確保
    - v128の成功構造を維持：HIGHフェーズheight_mult=1.8、TOWER閾値0.5、HIGH_TOWERペナルティ1.3倍
    - v128のバランス補正強度（HIGH=40.0/MEDIUM=30.0/LOW=20.0）を維持
    - v128のMEDIUM_TOWERペナルティ（1.5倍）を維持
    - ドリフトペナルティ一律30.0を維持
    - nextNextが同じタイプなら中央寄せボーナス50.0を維持
    - v128のシンプル構造（約110行）を完全に回帰
    """

    results = analysis.get("results", [])

    if not results:
        return {"x": 0.0, "reason": "no analysis data"}

    best_x = 0.0
    best_score = -float("inf")
    best_reason = ""

    # 盤面情報
    pieces = game_state.get("pieces", [])
    max_y = max([p["y"] for p in pieces]) if pieces else -4.0

    # フェーズ判定（v42の閾値0.8/1.8/3.0を維持）
    if max_y < 0.8:
        phase = "LOW"
        height_mult = 1.0
        merge_mult = 1.2
    elif max_y < 1.8:
        phase = "MEDIUM"
        height_mult = 1.8  # v208: v42の2.4→v128の1.8に引き下げ（MEDIUM→HIGH遷移改善）
        merge_mult = 1.0
    elif max_y < 3.0:
        phase = "HIGH"
        height_mult = 1.8  # v208: v128の1.8を維持
        merge_mult = 1.0
    else:
        phase = "CRITICAL"
        height_mult = 1.0  # CRITICAL: height_multなし
        merge_mult = 0.6  # v208: v42の0.6を維持

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

        # === v208: v128回帰・MEDIUM遷移改善 ===

        # 1. マージグレードによるスコア（v128の固定値を維持、指数スケーリング削除）
        if merge_grade == "DIRECT":
            score += 1200.0 * merge_mult
            reasons.append("DIRECT_MERGE")
        elif merge_grade == "NEAR":
            score += 600.0 * merge_mult
            reasons.append("NEAR_MERGE")
        elif merge_grade == "FAR":
            score += 200.0 * merge_mult
            reasons.append("FAR_MERGE")

        # 2. 高度によるペナルティ（v128一律ルール、height_penalty係数40.0）
        height_penalty = landing_y * 40.0 * height_mult

        # HIGH_TOWERペナルティ（v128: 1.3倍緩和を復帰、マージ機会確保）
        if phase == "HIGH" and landing_y > 0.5:  # v128の閾値0.5を維持
            height_penalty *= 1.3  # v208: v128の1.3倍を復帰（v207の0.5倍緩和は失敗）
            reasons.append("HIGH_TOWER")
        elif phase == "MEDIUM" and landing_y > 0.5:  # v42の閾値0.5を維持
            height_penalty *= (
                1.5  # v208: v42の1.5倍を維持（MEDIUMフェーズ高度管理確保）
            )
            reasons.append("MEDIUM_TOWER")
        elif landing_y > 0.0:
            reasons.append("HIGH_LAYER")

        score -= height_penalty

        # 3. ドリフトによるペナルティ（一律30.0を維持）
        drift_penalty = (abs(drift_x) + drift_unc) * 30.0
        score -= drift_penalty

        # 4. 左右バランス補正（v128の値を維持）
        balance_strength = 20.0
        if phase == "HIGH":
            balance_strength = 40.0  # v208: v128の40.0を維持
        elif phase == "MEDIUM":
            balance_strength = 30.0  # v208: v128の30.0を維持

        left_count = sum(1 for p in pieces if p["x"] < 0)
        right_count = len(pieces) - left_count
        balance_bias = (right_count - left_count) / (len(pieces) if pieces else 1)

        balance_penalty = x * balance_bias * balance_strength
        score -= abs(balance_penalty)

        # 5. nextNextが同じタイプなら中央寄せボーナス（一律50.0を維持）
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
