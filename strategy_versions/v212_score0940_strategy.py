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
# v210: HIGH_TOWER緩和・マージグレード高度制限導入版 - v209の失敗（スコア1574、HIGHフェーズ期間短縮・HIGH_TOWER発動率高）を受けて、HIGHフェーズでのマージ機会確保を目的にブレイクスルーを実施。v209履歴分析で特定した問題: - HIGHフェーズ期間が9ターンしかない（9.8%）、v128ではもっと長かった - HIGH_TOWER発動率が77.8%（7/9ターン）、マージ優先が機能していない - Turn 73-91でスコア伸び悩み（score_delta=0）、max_yが1.5〜2.29の間で推移 - Turn 92でCRITICAL到達（max_y=3.15）でゲーム終了 - v128のHIGH_TOWERペナルティ1.3倍は、v128では機能していたが、現在のピース配列では過剰な可能性がある 解決策（振り子パターン解消のブレイクスルー）: - HIGH_TOWERペナルティを1.3倍から1.1倍に緩和：マージ機会を確保 - マージグレードによる高度制限を導入：DIRECTマージでは高度制限なし、NEARマージでは高度制限緩和、FARマージでは高度制限緩和 - MEDIUMフェーズheight_multを2.4→2.2に微調整：MEDIUM→HIGH遷移をスムーズに - v128の成功構造を維持：HIGHフェーズheight_mult=1.8、TOWER閾値0.5 - v128のバランス補正強度（HIGH=40.0/MEDIUM=30.0/LOW=20.0）を維持 - v128のMEDIUM_TOWERペナルティ（1.5倍）を維持 - ドリフトペナルティ一律30.0を維持 - nextNextが同じタイプなら中央寄せボーナス50.0を維持 - v128のシンプル構造（約110行）を維持しつつ、マージグレード高度制限を追加（約120行）
# v211: HIGH_TOWER完全削除・v128成功構造完全復帰版 - v210の失敗（スコア1511、HIGH_TOWER緩和・マージグレード高度制限導入）を受けて、振り子パターンを根本的に解消するブレイクスルーを実施。v210履歴分析で特定した問題: - HIGH_TOWERペナルティの緩和（1.3倍→1.1倍）は効果がなく、スコアが低下（v208:1710→v210:1511） - マージグレード高度制限を導入したが、複雑化しただけで改善に寄与していない - HIGH_TOWERペナルティ自体が現在のピース配列では過剰であり、微調整では不十分 - v208でheight_penalty係数50.0が使用されていた可能性があり、これがマージを阻害している可能性がある 根本原因: - HIGH_TOWERペナルティの倍率調整（1.3倍→1.2倍→1.1倍）は振り子パターン - マージグレード高度制限は複雑化しただけで、v128のシンプル構造よりも劣化 - v208でheight_penalty係数50.0が使用されていた可能性があり、v128の40.0に戻す必要がある - HIGH_TOWERペナルティ自体が現在のピース配列では過剰であり、完全削除が必要 解決策（振り子パターン解消のブレイクスルー）: - HIGH_TOWERペナルティを完全削除：v128の1.3倍緩和ではなく、完全削除でマージ機会を最大化 - マージグレード高度制限の簡素化：v210の複雑なロジックを削除し、シンプルに - v128の成功構造を完全復帰：height_penalty係数40.0、その他v128の設定を完全復帰 - マージグレード高度制限：DIRECT:1.0, NEAR:0.8, FAR:0.6（v210と同じだがシンプルに実装） - 振り子パターン（HIGH_TOWER倍率調整、マージグレード高度制限の複雑化）を回避し、v128の成功構造に完全回帰
# v212: マージインセンティブ一律強化・MEDIUM_TOWER緩和版 - v211の失敗（スコア1300、マージ機会極めて少ない・MEDIUMフェーズ短縮）を受けて、振り子パターンを回避しつつマージ機会を最大化するブレイクスルーを実施。v211履歴分析で特定した問題: - マージ機会が極めて少ない（59ターン中7回、11.9%） - 実際にマージ実行は1回のみ（Turn 40 DIRECT_MERGE） - HIGHフェーズ（21ターン）でのマージ機会損失（4回のみ） - MEDIUMフェーズが短い（12ターン、20.3%）- MEDIUM_TOWERペナルティ（1.5倍）が厳しすぎ - v128のマージボーナス（DIRECT=1200/NEAR=600/FAR=200）は、現在のピース配列では不十分 - HIGH_TOWERペナルティの有無にかかわらず、マージ機会が不足している 根本原因: - v211はHIGH_TOWERペナルティを完全削除したが、マージ機会は増えなかった - マージボーナスが不足しており、マージインセンティブが弱い - MEDIUM_TOWERペナルティ（1.5倍）が厳しすぎて、MEDIUM→HIGHの遷移が急速に進んでいる - v208（1710点）の成功要素を取り入れていない 解決策（振り子パターン解消のブレイクスルー）: - マージボーナスを一律20%強化：全フェーズでマージボーナスを20%増加 - MEDIUM_TOWERペナルティを緩和：1.5倍から1.2倍に減少 - v128の成功構造を維持：HIGHフェーズheight_mult=1.8、TOWER閾値0.5、HIGH_TOWERペナルティ1.3倍（v128の設定を復帰） - v128のバランス補正強度（HIGH=40.0/MEDIUM=30.0/LOW=20.0）を維持 - ドリフトペナルティ一律30.0を維持 - nextNextが同じタイプなら中央寄せボーナス50.0を維持 - v128のシンプル構造（約110行）を維持 - v208の成功要素（MEDIUMフェーズheight_mult=2.4）とv211のHIGH_TOWER削除を組み合わせる


def decide(game_state: dict, analysis: dict) -> dict:
    """マージインセンティブ一律強化・MEDIUM_TOWER緩和版

    v211の失敗（スコア1300、マージ機会極めて少ない・MEDIUMフェーズ短縮）を受けて、
    振り子パターンを回避しつつマージ機会を最大化するブレイクスルーを実施。

    v211履歴分析で特定した問題:
    - マージ機会が極めて少ない（59ターン中7回、11.9%）
    - 実際にマージ実行は1回のみ（Turn 40 DIRECT_MERGE）
    - HIGHフェーズ（21ターン）でのマージ機会損失（4回のみ）
    - MEDIUMフェーズが短い（12ターン、20.3%）- MEDIUM_TOWERペナルティ（1.5倍）が厳しすぎ
    - v128のマージボーナス（DIRECT=1200/NEAR=600/FAR=200）は、現在のピース配列では不十分
    - HIGH_TOWERペナルティの有無にかかわらず、マージ機会が不足している

    根本原因:
    - v211はHIGH_TOWERペナルティを完全削除したが、マージ機会は増えなかった
    - マージボーナスが不足しており、マージインセンティブが弱い
    - MEDIUM_TOWERペナルティ（1.5倍）が厳しすぎて、MEDIUM→HIGHの遷移が急速に進んでいる
    - v208（1710点）の成功要素を取り入れていない

    解決策（振り子パターン解消のブレイクスルー）:
    - マージボーナスを一律20%強化：全フェーズでマージボーナスを20%増加
      - DIRECT: 1200→1440, NEAR: 600→720, FAR: 200→240
    - MEDIUM_TOWERペナルティを緩和：1.5倍から1.2倍に減少
    - v128の成功構造を維持：HIGHフェーズheight_mult=1.8、TOWER閾値0.5、HIGH_TOWERペナルティ1.3倍
    - v128のバランス補正強度（HIGH=40.0/MEDIUM=30.0/LOW=20.0）を維持
    - ドリフトペナルティ一律30.0を維持
    - nextNextが同じタイプなら中央寄せボーナス50.0を維持
    - v128のシンプル構造（約110行）を維持
    - v208の成功要素（MEDIUMフェーズheight_mult=2.4）とv211のHIGH_TOWER削除を組み合わせる
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
        height_mult = 2.4  # v212: v128の2.4を維持（MEDIUMフェーズ期間を確保）
        merge_mult = 1.2  # v212: マージボーナス一律20%強化
    elif max_y < 3.0:
        phase = "HIGH"
        height_mult = 1.8  # v212: v128の1.8を維持
        merge_mult = 1.2  # v212: マージボーナス一律20%強化
    else:
        phase = "CRITICAL"
        height_mult = 1.0  # CRITICAL: height_multなし
        merge_mult = 0.7  # v212: v128の0.6→0.7に微増加（CRITICALフェーズでのマージインセンティブ強化）

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

        # === v212: マージインセンティブ一律強化・MEDIUM_TOWER緩和 ===

        # 1. マージグレードによるスコア（v212: 一律20%強化）
        if merge_grade == "DIRECT":
            score += 1440.0 * merge_mult  # v212: 1200→1440（20%強化）
            reasons.append("DIRECT_MERGE")
        elif merge_grade == "NEAR":
            score += 720.0 * merge_mult  # v212: 600→720（20%強化）
            reasons.append("NEAR_MERGE")
        elif merge_grade == "FAR":
            score += 240.0 * merge_mult  # v212: 200→240（20%強化）
            reasons.append("FAR_MERGE")

        # 2. 高度によるペナルティ（v212: v128一律ルール）
        height_penalty = landing_y * 40.0 * height_mult

        # TOWERペナルティ（v212: v128の設定に復帰）
        if phase == "HIGH" and landing_y > 0.5:  # v212: v128の閾値0.5を維持
            height_penalty *= 1.3  # v212: v128の1.3倍を復帰（v211の削除は失敗）
            reasons.append("HIGH_TOWER")
        elif phase == "MEDIUM" and landing_y > 0.5:  # v212: v42の閾値0.5を維持
            height_penalty *= 1.2  # v212: v128の1.5倍から1.2倍に緩和
            reasons.append("MEDIUM_TOWER")
        elif landing_y > 0.0:
            reasons.append("HIGH_LAYER")

        score -= height_penalty

        # 3. ドリフトによるペナルティ（一律30.0を維持）
        drift_penalty = (abs(drift_x) + drift_unc) * 30.0
        score -= drift_penalty

        # 4. 左右バランス補正（v212: v128の値を維持）
        balance_strength = 20.0
        if phase == "HIGH":
            balance_strength = 40.0  # v212: v128の40.0を維持
        elif phase == "MEDIUM":
            balance_strength = 30.0  # v212: v128の30.0を維持

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
