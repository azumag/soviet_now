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
# v223: 連続的height_penalty係数導入・HIGHフェーズ緩和版 - v222の失敗（スコア2371、反応ペア閾値8が機能せず、max_y=2.93でCRITICAL直前）を受けて、閾値調整ではなく「連続的な高度管理」へブレイクスルーを実施。v222履歴分析で特定した問題: - 反応ペア数が全ターンで最大3（閾値8には一度も到達しない） - 常にLOW_REACTIVEモード：height_penalty=20.0（緩和）で固定され、高度管理が効いていない - 盤面がmax_y=2.93まで上昇（CRITICAL閾値3.0の直前） - HIGHフェーズ（Turn 46-100）でmax_yが2.93まで上昇 根本原因: - v222の閾値8は実際のゲームデータでは機能していないため、常にheight_penalty=20.0（緩和）で固定され、高度管理が効いていない - 閾値8→4→8のシャッフルパターンは、「ある閾値に合わせる」のではなく「実際のゲームで機能しない閾値を選んでいる」という堂々巡り - 反応ペアが実際には増えている（0個→1個→2個→3個）が、閾値8に到達しないためheight_penalty係数が全く変化しない 解決策策（振り子パターン解消・ブレイクスルー）: - 連続的なheight_penalty係数を導入：反応ペア数に応じて滑らかに変化させる（0-3個:20.0、4-6個:35.0、7個以上:45.0） - HIGHフェーズのheight_multを1.4に緩和：v218の1.6から緩和し、マージ機会を最大化 - v218の成功要素を維持：マージボーナス1800/900/300、HIGH_TOWER削除 - 閾値ではなく反応ペア数の実測値に応じた動的調整で、実際のゲームデータを直接反映させる仕組み - v218のシンプル構造（約135行）を維持。
# v225-v226: CRITICAL高度管理強化・HIGHフェーズheight_mult強化版 - v225の失敗（スコア1372、max_y=3.39でCRITICAL到達）を受けて、CRITICALモードでの高度管理強化を図るブレイクスルーを実施。v226履歴分析で特定した問題: - max_y=3.39でCRITICALフェーズ到達、94ターンで早期終了 - HIGH_CRITICAL（max_y>=2.6）のheight_penalty_coeff=150.0は弱すぎ、高度管理が効いていない - max_yはTurn 88から94で3.59→3.39に急増加 - HIGHフェーズheight_mult=2.0固定は不十分、v218の2.4よりも緩和 根本原因: - v226のCRITICAL高度管理（height_penalty=150.0）は盤面の急上昇を防ぐには不十分 - max_y>=2.6の強化は遅すぎて、HIGHフェーズでの高度管理を阻害した可能性がある - v218の成功構造（height_mult 2.4、マージボーナス1800/900/300）は実際には強力すぎた 解決策（ブレイクスルー）: - CRITICALモード高度管理強化：max_yに応じた動的係数を導入：max_y=3.0で150.0、3.2で200.0、2.4で100.0 - max_y>=2.6以上でHIGH_CRITICALモード発動：max_y>=2.6、height_penalty=150.0 - HIGHフェーズheight_multを2.0に強化：v218の1.6よりも厳格な高度管理 - マージボーナス強化維持：DIRECT=1800/NEAR=900/FAR=300 - HIGH_TOWERペナルティ完全削除 - バランス補正緩和：HIGHフェーズで40.0（v218の50.0→40.0に緩和） - v218のシンプル構造を維持：2段階height_penalty係数（<8: 20.0、>=8: 50.0）
# v226: CRITICAL高度管理強化版の失敗 - バッチ統計（avg=1197.8、stddev=645.9）でスコアのばらつきが極端に大きい。decision_reason分布でLOW_REACTIVE_HIGH_LAYERが35.9%を占める圧倒的支配。HIGHフェーズでの高度管理が過剰に厳格（height_penalty_coeff=150.0-250.0）。HIGH_TOWERペナルティ削除により高度管理が崩壊。反応ペア閾値8は実際のゲームでは機能していない（最大5ペア）。ベストゲーム（2097点、40ターン）vsワーストゲーム（300点、16ターン）の比較で、安定した高度管理の重要性を確認。
# v227: v218完全復帰・HIGHフェーズ高度管理最適化版 - v226の失敗（スコアのばらつき、高度管理過剰）を受けて、v218の成功構造を完全復帰しつつHIGHフェーズ高度管理を最適化。v226履歴分析とバッチデータから特定した根本原因: - HIGH_TOWERペナルティ振り子パターン（v172-v174、v222-v226）：「削除すると盤面が高くなる→復帰するとマージが減る→また削除...」の堂々巡りを断ち切るため、HIGH_TOWERを1.5倍で再導入（v218の成功設定） - HIGHフェーズheight_mult振り子パターン（v222:1.4、v224:1.4、v226:2.0）：v218の2.4は強すぎ、v226の2.0は弱すぎ、中間値2.2を採用 - 反応ペア閾値8は実際のゲームデータでは機能していない（最大5ペア）：閾値4に調整し、実際のゲームデータに基づく動的調整を実現 - MEDIUMフェーズheight_mult振り子：v226の1.4は緩和しすぎ、v218の1.4を維持しつつv128の1.8の中間値として1.6を採用 - マージボーナス強化：v128の強力な値（DIRECT=1200/NEAR=600/FAR=200）を採用 - v218のシンプル構造を維持：2段階height_penalty係数（<4: 20.0、>=4: 50.0）、約120行


def decide(game_state: dict, analysis: dict) -> dict:
    """v227: v218完全復帰・HIGHフェーズ高度管理最適化版

    v226の失敗（スコアavg=1197.8、stddev=645.9、ばらつき極大）を受けて、
    v218の成功構造を完全復帰しつつHIGHフェーズ高度管理を最適化するブレイクスルーを実施。

    v226履歴分析とバッチデータから特定した根本原因:
    - HIGH_TOWERペナルティ振り子パターン（v172-v174、v222-v226）:
      「削除すると盤面が高くなる→復帰するとマージが減る→また削除...」の堂々巡り
    - HIGHフェーズheight_mult振り子パターン（v222:1.4、v224:1.4、v226:2.0）:
      v218の2.4は強すぎ、v226の2.0は弱すぎ
    - 反応ペア閾値8は機能していない（実際のゲームで最大5ペア）:
      閾値8には一度も到達せず、height_penalty係数が20.0固定
    - MEDIUMフェーズheight_mult振り子:
      v226の1.4は緩和しすぎ、v218の1.4を維持しつつ調整が必要
    - ベストvsワースト比較:
      ベスト（2097点、40ターン）: 安定した高度管理、max_y推移0.27→2.53→1.52→2.59
      ワースト（300点、16ターン）: max_y=-1.22→2.34→3.27→早期終了

    解決策（ブレイクスルー）:
    - HIGH_TOWERペナルティ振り子解消:
      * HIGH_TOWERペナルティを1.5倍で再導入（v218の成功設定）
      * 堂々巡りの「削除か復帰か」の二択を捨て、v218のバランスを採用
    - HIGHフェーズ高度管理最適化:
      * v218の2.4とv226の2.0の中間値2.2を採用
      * ベストゲームのmax_y推移を参考に、過度な厳格化を回避
    - 反応ペア閾値最適化:
      * 閾値8から4に調整（実際のゲームデータで最大5ペア）
      * ベストゲームの反応ペア推移（0→2→5→2→0）を反映
    - MEDIUMフェーズheight_mult調整:
      * v218の1.4を維持しつつv128の1.8の中間値として1.6を採用
    - マージボーナス強化:
      * v128の強力な値（DIRECT=1200/NEAR=600/FAR=200）を採用
      * HIGHフェーズでのマージ機会最大化
    - v218のシンプル構造を維持:
      * 2段階height_penalty係数（<4: 20.0、>=4: 50.0）
      * 約120行構造を維持
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

    # v227: 2段階height_penalty係数（閾値4に調整、実際のゲームデータに基づく）
    if reactive_pairs < 4:
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
        # v227: v218の1.4を維持しつつv128の1.8の中間値として1.6を採用
        height_mult = 1.6
        merge_mult = 1.0
    elif max_y < 3.0:
        phase = "HIGH"
        # v227: v218の2.4とv226の2.0の中間値2.2を採用（高度管理最適化）
        height_mult = 2.2
        merge_mult = 1.0
    else:
        phase = "CRITICAL"
        height_mult = 1.0
        merge_mult = 0.6

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

        # 1. マージグレードによるスコア（v128の強力な値）
        if merge_grade == "DIRECT":
            score += 1200.0 * merge_mult
            reasons.append("DIRECT_MERGE")
        elif merge_grade == "NEAR":
            score += 600.0 * merge_mult
            reasons.append("NEAR_MERGE")
        elif merge_grade == "FAR":
            score += 200.0 * merge_mult
            reasons.append("FAR_MERGE")

        # 2. 高度によるペナルティ
        height_penalty = landing_y * height_penalty_coeff * height_mult

        # v227: HIGH_TOWERペナルティを1.5倍で再導入（v218の成功設定）
        if phase == "HIGH" and landing_y > 0.5:
            height_penalty *= 1.5
            reasons.append("HIGH_TOWER")
        elif phase == "MEDIUM" and landing_y > 0.5:
            height_penalty *= 1.5
            reasons.append("MEDIUM_TOWER")
        elif landing_y > 0.0:
            reasons.append("HIGH_LAYER")

        score -= height_penalty

        # 3. ドリフトによるペナルティ（v218の一律30.0を維持）
        drift_penalty = (abs(drift_x) + drift_unc) * 30.0
        score -= drift_penalty

        # 4. 左右バランス補正（v218の設定を維持）
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

        # 5. nextNextが同じタイプなら中央寄せボーナス（v218の一律50.0を維持）
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
