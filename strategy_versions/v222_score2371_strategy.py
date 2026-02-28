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
# v208-v214: v128完全回帰・MEDIUM_TOWER完全削除版の失敗パターン - v213（スコア1228）、v214（スコア1350）はMEDIUM_TOWERを削除したがスコアはv128の1/3以下。v215（スコア435）ではMEDIUM height_multを2.4にしたが更に悪化。根本原因はv128のheight_penalty係数50.0が現在のピース配列では過剰で、MEDIUMフェーズを短縮していること。
# v220: 反応ペア数高度管理細分化・HIGHフェーズv128回帰版 - v219の失敗（スコア1474、74ターンで終了、CRITICALフェーズ到達）を受けて、反応ペア動的height_penalty係数の問題を特定。v219履歴分析で特定した問題: - 反応ペア数が74ターン中一度も8以上に達していない（常に0-4の範囲） - 結果としてheight_penalty係数は常に20.0（緩和モード）で固定され、高度管理が弱すぎる - HIGHフェーズのheight_mult=1.6はv128の1.8よりも緩和しすぎており、盤面が高くなりすぎている（max_y=3.44でCRITICAL到達） - score_deltaが小さく（最大45点）、大きな連鎖が発生していない 根本原因: - v218の反応ペア閾値8は高すぎて、実際のゲームでは機能していない - 反応ペア数に応じた高度管理の細分化が不十分（2段階のみ） - HIGHフェーズのheight_multを1.6に緩和しすぎた結果、v128よりも盤面が高くなりすぎている 解決策（振り子パターン解消のブレイクスルー）: - 反応ペア数を4段階に細分化：（0-1個: height_mult緩和・height_penalty=20.0）、（2-3個: 標準・height_penalty=35.0）、（4-7個: 強化・height_penalty=45.0）、（8以上: 最強化・height_penalty=50.0） - HIGHフェーズのheight_multをv128の1.8に回帰：反応ペア数に応じて1.4-2.0の範囲で調整 - MEDIUMフェーズもheight_mult細分化：反応ペア数に応じて1.2-1.8の範囲で調整 - v218のマージボーナス強化（DIRECT=1800/NEAR=900/FAR=300）を維持 - v218のHIGH_TOWERペナルティ完全削除を維持 - v128のシンプル構造を維持しつつ、反応ペア数に応じた高度管理を細分化。
# v221: v218成功要素ベース・HIGH_TOWER再導入版 - v220の失敗（スコア1612、反応ペア閾値8が機能せず）を受けて、v218の成功要素をベースに根本的な改善を実施。v220履歴分析で特定した問題: - 反応ペア数が103ターン中一度も8以上に達していない（常に0-6の範囲） - 結果としてheight_penalty係数は常に20.0（緩和モード）で固定され、高度管理が弱すぎる - max_y=3.39でCRITICALフェーズ到達 - HIGH_TOWERペナルティ削除が、盤面が高くなる原因 根本原因: - v220の反応ペア閾値8は高すぎて、実際のゲームでは機能していない - v218のHIGH_TOWERペナルティ削除が、HIGHフェーズでの高度管理を弱体化した - 4段階細分化がv218のシンプルさを損なった - HIGHフェーズheight_mult=1.8はv220の設定だが、v3689の1.3倍HIGH_TOWERとは相性が悪い 解決策（振り子パターン解消のブレイクスルー）: - 反応ペア閾値を8→4に下げ：実際のゲームで機能する閾値に調整 - HIGH_TOWERペナルティ再導入：v3689の1.3倍を復活 - 高度管理は3段階化：反応ペア<4: 20.0、4-6: 35.0、>6: 45.0 - HIGHフェーズheight_mult=1.6に調整：v218の成功値とv3689の1.3倍HIGH_TOWERのバランス - v218のマージボーナス強化（DIRECT=1800/NEAR=900/FAR=300）を維持 - v218のシンプル構造を維持：コード量をv220の約140行からv218の約130行に削減
# v222: HIGH_TOWER振り子解消・v218完全復帰版 - v221の失敗（スコア1200、HIGH_TOWERペナルティがHIGHフェーズで33.3%発動しマージ阻害）を受けて、HIGH_TOWERペナルティの振り子パターン（削除→復帰→削除→復帰）を根本的に解消するブレイクスルーを実施。v221履歴分析で特定した問題: - 反応ペア数が最大2で、v221の閾値4には一度も到達していない - 反応ペア閾値4は機能せず、常に緩和モード - HIGH_TOWERペナルティがHIGHフェーズで4/12ターン（33.3%）発動し、マージ機会を阻害 - マージ予測が4回あったが、score_delta=0で実際にはマージ失敗 - v218（スコア2452）の成功要素（HIGH_TOWER削除、反応ペア閾値8）が破壊された 根本原因: - HIGH_TOWERペナルティの「削除/復帰」振り子パターン：削除すると盤面が高くなる→復帰するとマージ機会が減る→また削除...の堂々巡り - 反応ペア閾値のシャッフルパターン：8→4→...と調整し続けているが、実際のゲームデータで効果検証していない - v218の成功要素を「反応ペア閾値4」「HIGH_TOWER再導入」で破壊した 解決策（振り子パターン解消のブレイクスルー）: - HIGH_TOWERペナルティ完全削除：振り子パターンを止めるため、「削除か復帰か」の選択肢を捨てる。HIGH_TOWERを完全に削除し、HIGHフェーズでのマージ機会を最大化 - v218完全復帰：スコア2452の成功構造を維持（反応ペア閾値8、マージボーナス1800/900/300、height_mult 1.4/1.6） - 反応ペア閾値8を維持：実際のゲームデータで閾値調整の効果を検証し、閾値変更はデータに基づいて決定する - v218のシンプル構造（約130行）を維持：高度管理細分化でコード増加を回避


def decide(game_state: dict, analysis: dict) -> dict:
    """HIGH_TOWER振り子解消・v218完全復帰版

    v221の失敗（スコア1200、HIGH_TOWERペナルティがHIGHフェーズで33.3%発動しマージ阻害）を受けて、
    HIGH_TOWERペナルティの振り子パターン（削除→復帰→削除→復帰）を根本的に解消するブレイクスルーを実施。

    v221履歴分析で特定した問題:
    - 反応ペア数が最大2で、v221の閾値4には一度も到達していない
    - 反応ペア閾値4は機能せず、常に緩和モード
    - HIGH_TOWERペナルティがHIGHフェーズで4/12ターン（33.3%）発動し、マージ機会を阻害
    - マージ予測が4回あったが、score_delta=0で実際にはマージ失敗
    - v218（スコア2452）の成功要素（HIGH_TOWER削除、反応ペア閾値8）が破壊された

    根本原因:
    - HIGH_TOWERペナルティの「削除/復帰」振り子パターン：
      * 削除すると盤面が高くなる→復帰するとマージ機会が減る→また削除...の堂々巡り
    - 反応ペア閾値のシャッフルパターン：
      * 8→4→...と調整し続けているが、実際のゲームデータで効果検証していない
    - v218の成功要素を「反応ペア閾値4」「HIGH_TOWER再導入」で破壊した

    解決策（振り子パターン解消のブレイクスルー）:
    - HIGH_TOWERペナルティ完全削除：振り子パターンを止めるため、「削除か復帰か」の選択肢を捨てる。
      HIGH_TOWERを完全に削除し、HIGHフェーズでのマージ機会を最大化
    - v218完全復帰：スコア2452の成功構造を維持
      * 反応ペア閾値8
      * マージボーナス1800/900/300
      * height_mult 1.4（MEDIUM）/ 1.6（HIGH）
    - 反応ペア閾値8を維持：実際のゲームデータで閾値調整の効果を検証し、
      閾値変更はデータに基づいて決定する
    - v218のシンプル構造（約130行）を維持：高度管理細分化でコード増加を回避
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

    # リアクター状態を取得
    reactor = analysis.get("reactor", {})
    reactive_pairs_raw = reactor.get("reactive_pairs", 0)
    # reactive_pairsがリストの場合は長さを取得、整数の場合はそのまま使用
    reactive_pairs = (
        len(reactive_pairs_raw)
        if isinstance(reactive_pairs_raw, list)
        else reactive_pairs_raw
    )

    # v222: v218完全復帰 - 反応ペア閾値8、2段階高度管理
    if reactive_pairs < 8:
        height_penalty_coeff = 20.0  # 反応ペア少ない：緩和、積極的に落とす
        penalty_reason = "LOW_REACTIVE"
    else:
        height_penalty_coeff = 50.0  # 反応ペア多い：厳格、慎重に選ぶ
        penalty_reason = "HIGH_REACTIVE"

    # フェーズ判定（v218の閾値0.8/1.8/3.0を維持）
    if max_y < 0.8:
        phase = "LOW"
        height_mult = 1.0
        merge_mult = 1.2
    elif max_y < 1.8:
        phase = "MEDIUM"
        height_mult = 1.4  # v222: v218の1.4を維持
        merge_mult = 1.0
    elif max_y < 3.0:
        phase = "HIGH"
        height_mult = 1.6  # v222: v218の1.6を維持
        merge_mult = 1.0
    else:
        phase = "CRITICAL"
        height_mult = 1.0  # CRITICAL: height_multなし
        merge_mult = 0.6  # v222: v128の0.6を維持

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

        # === v222: HIGH_TOWER振り子解消・v218完全復帰 ===

        # 1. マージグレードによるスコア（v222: v218の強化値を維持）
        if merge_grade == "DIRECT":
            score += 1800.0 * merge_mult
            reasons.append("DIRECT_MERGE")
        elif merge_grade == "NEAR":
            score += 900.0 * merge_mult
            reasons.append("NEAR_MERGE")
        elif merge_grade == "FAR":
            score += 300.0 * merge_mult
            reasons.append("FAR_MERGE")

        # height_penalty係数に応じた理由を追加
        reasons.append(penalty_reason)

        # 2. 高度によるペナルティ（v222: v218の動的調整を維持）
        height_penalty = landing_y * height_penalty_coeff * height_mult

        # v222: HIGH_TOWERペナルティ完全削除（振り子パターン解消）
        if landing_y > 0.0:
            reasons.append("HIGH_LAYER")

        score -= height_penalty

        # 3. ドリフトによるペナルティ（一律30.0を維持）
        drift_penalty = (abs(drift_x) + drift_unc) * 30.0
        score -= drift_penalty

        # 4. 左右バランス補正（v218の設定を維持）
        balance_strength = 20.0
        if phase == "HIGH":
            balance_strength = 40.0  # v222: v218の40.0を維持
        elif phase == "MEDIUM":
            balance_strength = 30.0  # v222: v218の30.0を維持

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
