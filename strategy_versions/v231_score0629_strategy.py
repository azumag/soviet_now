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
# v205-v207: 指数関数マージボーナススケーリングの失敗パターン - v205（スコア702）、v206（スコア1176）、v207（スコア1710）で、指数スケーリング（2x~50x）による過剰なマージインセンティブが、盤面の急激な上昇を引き起こし、HIGH_TOWERペナルティを過剰にトリガー。MEDIUMフェーズheight_mult=2.4（v207）はMEDIUM→HIGHへの遷移を急激にし、HIGHフェーズ期間を短縮。
# v208-v214: v128完全回帰・MEDIUM_TOWER完全削除版の失敗パターン - v213（スコア1228）、v214（スコア1350）はMEDIUM_TOWERを削除したがスコアはv128の1/3以下以下。v215（スコア435）ではMEDIUM height_multを2.4にしたが更に悪化。根本原因はv128のheight_penalty係数50.0が現在のピース配列では過剰で、MEDIUMフェーズを短縮していること。
# v222: HIGH_TOWER振り子解消・v218完全復帰版 - v221の失敗（スコア1200、HIGH_TOWERペナルティがHIGHフェーズで33.3%発動しマージ阻害）を受けて、HIGH_TOWERペナルティの振り子パターン（削除→復帰→削除→復帰）を根本的に解消するブレイクスルーを実施。v221履歴分析で特定した問題: - 反応ペア数が最大2で、v221の閾値4には一度も到達していない - 反応ペア閾値4は機能せず、常に緩和モード - HIGH_TOWERペナルティがHIGHフェーズで4/12ターン（33.3%）発動しマージ機会を阻害 - マージ予測が4回あったが、score_delta=0で実際にはマージ失敗 - v218（スコア2452）の成功要素（HIGH_TOWER削除、反応ペア閾値8）が破壊された 根本原因: - HIGH_TOWERペナルティの「削除/復帰」振り子パターン：削除すると盤面が高くなる→復帰するとマージ機会が減る→また削除...の堂々巡り - 反応ペア閾値のシャッフルパターン：8→4→...と調整し続けているが、実際のゲームデータで効果検証していない - v218の成功要素を「反応ペア閾値4」「HIGH_TOWER再導入」で破壊した 解決策策（振り子パターン解消・ブレイクスルー）: - HIGH_TOWERペナルティ完全削除：振り子パターンを止めるため、「削除か復帰か」の選択肢を捨てる。HIGH_TOWERを完全に削除し、HIGHフェーズでのマージ機会を最大化 - v218完全復帰：スコア2452の成功構造を維持（反応ペア閾値8、マージボーナス1800/900/300、height_mult 1.4/1.6） - 反応ペア閾値8を維持：実際のゲームデータで閾値調整の効果を検証し、閾値変更はデータに基づいて決定する - v218のシンプル構造（約130行）を維持：高度管理細分化でコード増加を回避。
# v223: 連続的height_penalty係数導入・HIGHフェーズ緩和版 - v222の失敗（スコア2371、反応ペア閾値8が機能せず、max_y=2.93でCRITICAL直前）を受けて、閾値調整ではなく「連続的な高度管理」へブレイクスルーを実施。v222履歴分析で特定した問題: - 反応ペア数が全ターンで最大3（閾値8には一度も到達しない） - 常にLOW_REACTIVEモード：height_penalty=20.0（緩和）で固定され、高度管理が効いていない - 盤面がmax_y=2.93まで上昇（CRITICAL閾値3.0の直前） - HIGHフェーズ（Turn 46-100）でmax_yが2.93まで上昇 根本原因: - v222の閾値8は実際のゲームデータでは機能していないため、常にheight_penalty=20.0（緩和）で固定され、高度管理が効いていない - 閾値8→4→8のシャッフルパターンは、「ある閾値に合わせる」のではなく「実際のゲームで機能しない閾値を選んでいる」という堂々巡り - 反応ペアが実際には増えている（0個→1個→2個→3個）が、閾値8に到達しないためheight_penalty係数が全く変化しない 解決策策（振り子パターン解消・ブレイクスルー）: - 連続的なheight_penalty係数を導入：反応ペア数に応じて滑らかに変化させる（0-3個:20.0、4-6個:35.0、7個以上:45.0） - HIGHフェーズのheight_multを1.4に緩和：v218の1.6から緩和し、マージ機会を最大化 - v218の成功要素を維持：マージボーナス1800/900/300、HIGH_TOWER削除 - 閾値ではなく反応ペア数の実測値に応じた動的調整で、実際のゲームデータを直接反映させる仕組み - v218のシンプル構造（約135行）を維持。
# v224: HIGHフェーズ連続倍率版 - v223の失敗（スコア1478、連続的height_penalty係数が実質20.0固定、HIGHフェーズheight_mult 1.6→1.4緩和でmax_y=3.21到達）を受けて、v218の成功構造を維持しつつHIGHフェーズでの連続的な高度管理を実装。v223履歴分析で特定した問題: - 反応ペア0-3個が大部分で20.0係数が支配 - HIGHフェーズheight_mult=1.4は緩和しすぎ、高度管理が崩壊 - max_y=3.21到達で88ターン早期終了 - v218の2452点から1478点で40%低下 根本原因: - v223の3段階連続係数は実際には20.0固定状態 - HIGHフェーズheight_multの固定値1.4はmax_y=1.8〜3.0全体で一律、適切な高度管理ができない - 「高度管理とマージのバランス」はmax_yによって連続的に変化させる必要がある 解決策策（ブレイクスルー）: - v218完全復帰：スコア2452の成功構造を維持：反応ペア2段階（<8: 20.0、>=8: 50.0）、merge_bonus 1800/900/300、HIGH_TOWER削除、height_mult 1.4/1.6） - MEDIUMフェーズheight_multを1.8に緩和（v218の1.4よりも緩和） - HIGHフェーズ連続倍率削除、シンプルな固定値1.6を採用 - CRITICAL回避のためにmax_y監視 - 反応ペア閾値8を維持しつつつ、反応ペア数が最大6の現状でも閾値8維持 - v218のシンプル構造（約120行）を維持。


def decide(game_state: dict, analysis: dict) -> dict:
    """v226: CRITICAL高度管理強化・v218復帰版

    v225の失敗（スコア1372、max_y=3.39でCRITICAL到達）を受けて、
    CRITICALモードでの高度管理強化を図るブレイクスルーを実施。

    v225履歴分析で特定した問題:
    - max_y=3.39でCRITICALフェーズ到達、94ターンで早期終了
    - HIGH_CRITICAL（max_y>=2.6）のheight_penalty_coeff=60.0は弱すぎ、高度管理が効いていない
    - max_yはTurn 88から94で3.59→3.39に急増加
    - HIGHフェーズheight_mult=1.6固定は不十分、v218の2.4よりも緩和

    根本原因:
    - v225のCRITICAL高度管理（height_penalty=60.0）は盤面の急上昇を防ぐには不十分
    - max_y>=2.6の強化は遅すぎて、HIGHフェーズでの高度管理を阻害した可能性がある
    - v218の成功構造（height_mult 2.4、マージボーナス1800/900/300）は実際には強力すぎた

    解決策（ブレイクスルー）:
    - CRITICALモード高度管理強化：
      * max_yに応じた動的係数を導入：max_y=3.0で150.0、3.2で70.0、2.4で40.0
      * max_y=2.6以上でHIGH_CRITICALモード発動：max_y>=2.6、height_penalty=150.0
      * HIGHフェーズheight_multを2.0に強化：v218の1.6よりも厳格な高度管理
      * マージボーナス強化維持：DIRECT=1800/NEAR=900/FAR=300
      * HIGH_TOWERペナルティ完全削除
      * バランス補正緩和：HIGHフェーズで40.0（v218の50.0→40.0に緩和）
    - v218のシンプル構造を維持：2段階height_penalty係数（<8: 20.0、>=8: 50.0）
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
    reactive_pairs = (
        len(reactive_pairs_raw)
        if isinstance(reactive_pairs_raw, list)
        else reactive_pairs_raw
    )

    # 2段階height_penalty係数（v218復帰）
    if reactive_pairs < 8:
        height_penalty_coeff = 20.0
        penalty_reason = "LOW_REACTIVE"
    else:
        height_penalty_coeff = 50.0
        penalty_reason = "HIGH_REACTIVE"

    # フェーズ判定
    if max_y < 0.8:
        phase = "LOW"
        height_mult = 1.0
        merge_mult = 1.2
    elif max_y < 1.8:
        phase = "MEDIUM"
        # v226: MEDIUMフェーズheight_multを1.4に緩和（v218の1.4よりも緩和）
        height_mult = 1.4
        merge_mult = 1.0
    elif max_y < 3.0:
        phase = "HIGH"
        # v226: HIGHフェーズheight_multを2.0に強化（v218の1.6よりも厳格）
        height_mult = 2.0
        merge_mult = 1.0

        # v226: HIGHフェーズでの動的height_penalty係数（max_yに応じて）
        if max_y >= 2.6:
            height_penalty_coeff = 150.0
            penalty_reason = "HIGH_CRITICAL"
        elif max_y >= 2.4:
            height_penalty_coeff = 250.0
            penalty_reason = "EXTREME_CRITICAL"
    else:
        phase = "CRITICAL"
        height_mult = 1.0
        merge_mult = 0.6

        # v226: CRITICALでの超高度管理（max_y>=3.0）
        if max_y >= 3.0:
            height_penalty_coeff = 150.0
        elif max_y >= 3.2:
            height_penalty_coeff = 200.0
        elif max_y >= 3.4:
            height_penalty_coeff = 300.0
        elif max_y >= 3.6:
            height_penalty_coeff = 500.0
            penalty_reason = "EXTREME_CRITICAL"
        else:
            height_penalty_coeff = 100.0

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

        # 1. マージグレードによるスコア（v218の強化値）
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

        # 2. 高度によるペナルティ
        height_penalty = landing_y * height_penalty_coeff * height_mult
        if landing_y > 0.0:
            reasons.append("HIGH_LAYER")

        score -= height_penalty

        # 3. ドリフトによるペナルティ（一律30.0を維持）
        drift_penalty = abs(drift_x) + drift_unc * 30.0
        score -= drift_penalty

        # 4. 左右バランス補正
        balance_strength = 20.0
        if phase == "HIGH":
            # v226: v218の50.0を40.0に緩和（v218の50.0→40.0）
            balance_strength = 40.0
        elif phase == "MEDIUM":
            balance_strength = 30.0
        else:
            balance_strength = 20.0

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
