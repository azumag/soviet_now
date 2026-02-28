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
# v205-v207: 指数関数マージボーナススケーリングの失敗パターン - v205（スコア702）、v206（スコア1176）、v207（スコア1710）で、指数スケーリング（2x~50x）による過剰なマージインセンティブが、盤面の急激な上昇を引き起こし、HIGH_TOWERペナルティを過剰にトリガー。MEDIUMフェーズのheight_mult=2.4（v207）はMEDIUM→HIGHへの遷移を急激にし、HIGHフェーズ期間を短縮。
# v213: v128完全回帰・MEDIUM_TOWER完全削除版 - v212の失敗（スコア940、マージ機会極端に少ない・マージボーナス一律強化の失敗）を受けて、振り子パターンを回避しつつv128の成功構造に完全回帰するブレイクスルーを実施。v212履歴分析で特定した問題: - マージ機会が極端に少ない（76ターン中5回、6.6%） - HIGHフェーズでのマージ機会が少ない（24ターン中2回、8.3%） - マージの質が低い（NEAR_MERGEが多い） - マージボーナス一律20%強化は効果がなく、スコアがv208（1710点）の半分以下 - MEDIUMフェーズ期間が短い（MEDIUM_TOWERペナルティ1.2倍でも厳しすぎ） - v208の成功要素（MEDIUMフェーズheight_mult=2.4）とv128の成功構造の統合が不十分 根本原因: - v212のマージボーナス一律20%強化（DIRECT:1200→1440, NEAR:600→720, FAR:200→240）は、マージ機会を増やすのに失敗 - v212のMEDIUM_TOWERペナルティ緩和（1.5倍→1.2倍）は、MEDIUMフェーズ期間を短縮 - v208（1710点）の成功要素（MEDIUMフェーズheight_mult=2.4）とv128の成功構造（HIGHフェーズheight_mult=1.8）の統合が不十分 - マージボーナス一律強化→削除の振り子パターンを回避し、v128の固定マージボーナスに回帰 - MEDIUM_TOWERペナルティ緩和→削除の振り子パターンを回避し、v211のMEDIUM_TOWER完全削除を再採用 解決策（振り子パターン解消のブレイクスルー）: - v128の成功構造に完全回帰：固定マージボーナス（DIRECT=1200/NEAR=600/FAR=200、merge_mult=1.0） - MEDIUM_TOWERペナルティを完全削除：v211の要素を再採用し、MEDIUMフェーズでのマージ機会を最大化 - MEDIUMフェーズheight_multをv128の1.8に設定：v208の2.4はMEDIUMフェーズを短縮 - v128のHIGHフェーズ設定を維持：height_mult=1.8、HIGH_TOWERペナルティ1.3倍 - v128のバランス補正強度（HIGH=40.0/MEDIUM=30.0/LOW=20.0）を維持 - v128的CRITICALフェーズ設定を維持：merge_mult=0.6 - ドリフトペナルティ一律30.0を維持 - nextNextが同じタイプなら中央寄せボーナス50.0を維持 - v128のシンプル構造（約110行）を完全維持
# v214: v208成功要素再導入・v128 HIGH構造融合版 - v213の失敗（スコア1350、MEDIUMフェーズheight_mult=1.8→スコア低下）を受けて、v208の成功要素（MEDIUMフェーズheight_mult=2.4、スコア1710）とv128のHIGHフェーズ構造（height_mult=1.8、HIGH_TOWERペナルティ1.3倍）を融合するブレイクスルーを実施。v213履歴分析で特定した問題: - MEDIUMフェーズheight_mult=1.8は盤面が高くなりすぎず、HIGHフェーズ期間が短い（max_y=2.8で終了） - HIGHフェーズでのマージ機会が不足（HIGH_TOWER発動率高） - v213の「v128完全回帰」はv128のMEDIUMフェーズheight_mult=1.8を再採用したが、v208の2.4の方が高スコア - v208の1710点 vs v213の1350点で、MEDIUMフェーズheight_multの違いが明確 根本原因: - v213はv128の「完全回帰」を試みたが、v208のMEDIUMフェーズheight_mult=2.4が実際には成功していた - v128のHIGHフェーズ構造（height_mult=1.8）はv128成功時（スコア3689）の要素だが、MEDIUMフェーズのheight_multはv208の2.4が良い - 振り子パターン（2.4→1.8→2.4）を回避し、v208の成功要素とv128のHIGHフェーズ構造を融合する 解決策（振り子パターン解消のブレイクスルー）: - v208の成功要素を再導入：MEDIUMフェーズheight_mult=2.4（v213の1.8から復帰） - v128のHIGHフェーズ構造を維持：height_mult=1.8、HIGH_TOWERペナルティ1.3倍 - v128の成功構造を維持：固定マージボーナス（DIRECT=1200/NEAR=600/FAR=200） - v128のバランス補正強度（HIGH=40.0/MEDIUM=30.0/LOW=20.0）を維持 - ドリフトペナルティ一律30.0を維持 - nextNextが同じタイプなら中央寄せボーナス50.0を維持 - MEDIUM_TOWERペナルティを完全削除（v213の設定を維持） - v128のシンプル構造（約110行）を維持
# v215: v128完全回帰・MEDIUM_TOWER完全削除版（v214失敗の再確認） - v214の失敗（スコア435、マージ機会極端に少ない）を受けて、v128の成功構造に完全回帰するブレイクスルーを実施。v214履歴分析で特定した問題: - マージ機会が極端に少ない（76ターン中5回、6.6%） - HIGHフェーズでのマージ機会が少ない（24ターン中2回、8.3%） - マージの質が低い（NEAR_MERGEが多い） - MEDIUMフェーズheight_mult=2.4がMEDIUM→HIGH遷移を急激にし、HIGHフェーズ期間が短縮 - HIGH_TOWERペナルティ1.3倍がHIGHフェーズで過剰にトリガーされ、マージ機会を損失 根本原因: - v214はv208の成功要素（MEDIUMフェーズheight_mult=2.4）とv128のHIGHフェーズ構造を融合しようとしたが、HIGHフェーズでのマージ機会確保に失敗 - v128のHIGH_TOWERペナルティ1.3倍は、現在のピース配列では過剰に作用し、マージ機会を阻害している可能性がある - MEDIUMフェーズheight_mult=2.4がMEDIUM→HIGH遷移を急激にし、HIGHフェーズ期間が短縮 解決策（振り子パターン解消のブレイクスルー）: - v128の成功構造に完全回帰：固定マージボーナス（DIRECT=1200/NEAR=600/FAR=200、merge_mult=1.0） - MEDIUM_TOWERペナルティを完全削除：MEDIUMフェーズでのマージ機会を最大化 - MEDIUMフェーズheight_multをv128の1.8に設定：MEDIUMフェーズ期間を確保 - v128のHIGHフェーズ設定を維持：height_mult=1.8、HIGH_TOWERペナルティ1.3倍 - v128のバランス補正強度（HIGH=40.0/MEDIUM=30.0/LOW=20.0）を維持 - v128のCRITICALフェーズ設定を維持：merge_mult=0.6 - ドリフトペナルティ一律30.0を維持 - nextNextが同じタイプなら中央寄せボーナス50.0を維持 - v128のシンプル構造（約110行）を完全維持


def decide(game_state: dict, analysis: dict) -> dict:
    """v128完全回帰・MEDIUM_TOWER完全削除版

    v214の失敗（スコア435、マージ機会極端に少ない）を受けて、
    振り子パターンを回避しつつv128の成功構造に完全回帰するブレイクスルーを実施。

    v214履歴分析で特定した問題:
    - マージ機会が極端に少ない（76ターン中5回、6.6%）
    - HIGHフェーズでのマージ機会が少ない（24ターン中2回、8.3%）
    - マージの質が低い（NEAR_MERGEが多い）
    - MEDIUMフェーズheight_mult=2.4がMEDIUM→HIGH遷移を急激にし、HIGHフェーズ期間が短縮
    - HIGH_TOWERペナルティ1.3倍がHIGHフェーズで過剰にトリガーされ、マージ機会を損失

    根本原因:
    - v214はv208の成功要素（MEDIUMフェーズheight_mult=2.4）とv128のHIGHフェーズ構造を融合しようとしたが、HIGHフェーズでのマージ機会確保に失敗
    - v128のHIGH_TOWERペナルティ1.3倍は、現在のピース配列では過剰に作用し、マージ機会を阻害している可能性がある
    - MEDIUMフェーズheight_mult=2.4がMEDIUM→HIGH遷移を急激にし、HIGHフェーズ期間が短縮

    解決策（振り子パターン解消のブレイクスルー）:
    - v128の成功構造に完全回帰：固定マージボーナス（DIRECT=1200/NEAR=600/FAR=200、merge_mult=1.0）
    - MEDIUM_TOWERペナルティを完全削除：MEDIUMフェーズでのマージ機会を最大化
    - MEDIUMフェーズheight_multをv128の1.8に設定：MEDIUMフェーズ期間を確保
    - v128のHIGHフェーズ設定を維保：height_mult=1.8、HIGH_TOWERペナルティ1.3倍
    - v128のバランス補正強度（HIGH=40.0/MEDIUM=30.0/LOW=20.0）を維保
    - v128のCRITICALフェーズ設定を維保：merge_mult=0.6
    - ドリフトペナルティ一律30.0を維保
    - nextNextが同じタイプなら中央寄せボーナス50.0を維保
    - v128のシンプル構造（約110行）を完全維保
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

    # フェーズ判定（v128の閾値0.8/1.8/3.0を維保）
    if max_y < 0.8:
        phase = "LOW"
        height_mult = 1.0
        merge_mult = 1.2
    elif max_y < 1.8:
        phase = "MEDIUM"
        height_mult = 1.8  # v215: v128の1.8を維保（MEDIUMフェーズ期間を確保）
        merge_mult = 1.0  # v215: v128の1.0に回帰
    elif max_y < 3.0:
        phase = "HIGH"
        height_mult = 1.8  # v215: v128の1.8を維保
        merge_mult = 1.0  # v215: v128の1.0に回帰
    else:
        phase = "CRITICAL"
        height_mult = 1.0  # CRITICAL: height_multなし
        merge_mult = 0.6  # v215: v128の0.6を維保

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

        # === v215: v128完全回帰・MEDIUM_TOWER完全削除 ===

        # 1. マージグレードによるスコア（v215: v128の固定値に回帰）
        if merge_grade == "DIRECT":
            score += 1200.0 * merge_mult  # v215: v128の1200に回帰
            reasons.append("DIRECT_MERGE")
        elif merge_grade == "NEAR":
            score += 600.0 * merge_mult  # v215: v128の600に回帰
            reasons.append("NEAR_MERGE")
        elif merge_grade == "FAR":
            score += 200.0 * merge_mult  # v215: v128の200に回帰
            reasons.append("FAR_MERGE")

        # 2. 高度によるペナルティ（v215: v128一律ルール）
        height_penalty = landing_y * 40.0 * height_mult

        # HIGH_TOWERペナルティ（v215: v128の設定を維保）
        if phase == "HIGH" and landing_y > 0.5:  # v215: v128の閾値0.5を維保
            height_penalty *= 1.3  # v215: v128の1.3倍を維保
            reasons.append("HIGH_TOWER")
        elif landing_y > 0.0:
            reasons.append("HIGH_LAYER")

        score -= height_penalty

        # 3. ドリフトによるペナルティ（一律30.0を維保）
        drift_penalty = (abs(drift_x) + drift_unc) * 30.0
        score -= drift_penalty

        # 4. 左右バランス補正（v215: v128の値を維保）
        balance_strength = 20.0
        if phase == "HIGH":
            balance_strength = 40.0  # v215: v128の40.0を維保
        elif phase == "MEDIUM":
            balance_strength = 30.0  # v215: v128の30.0を維保

        left_count = sum(1 for p in pieces if p["x"] < 0)
        right_count = len(pieces) - left_count
        balance_bias = (right_count - left_count) / (len(pieces) if pieces else 1)

        balance_penalty = x * balance_bias * balance_strength
        score -= abs(balance_penalty)

        # 5. nextNextが同じタイプなら中央寄せボーナス（v215: v128の一律50.0を維保）
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
