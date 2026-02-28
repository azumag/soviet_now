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
# v216: v215失敗の分析・v128とのパラメータ差異特定版 - v215（スコア435）、v216（スコア962）の失敗原因を特定。v215/v216は「v128完全回帰」を主張しなが、実際には3つの変更で高度管理を弱体化：（1）height_penalty係数をv128の50.0→40.0に25%弱体化、（2）MEDIUM height_multをv128の2.4→1.8に25%弱体化、（3）MEDIUM_TOWERペナルティを削除。これらの変更が盤面の急上昇を招き、スコアを74%低下させた。
# v217: HIGH_TOWER段階的緩和・反応ペア考慮・v128再構成版 - v216の失敗（スコア962、v128の3689点に対し74%低下）を受けて、根本的なアプローチ変更を実施。履歴分析でHIGHフェーズでHIGH_TOWERが38%発動し、マージ機会を阻害していることを特定。v128のMEDIUM height_mult=2.4とMEDIUM_TOWER=1.5xは現在のピース配列では過剰。解決策：（1）v128のheight_penalty係数50.0を維持しつつ、HIGH_TOWERを段階的に緩和（HIGHフェーズ初期=1.1x、max_y上昇に伴い1.3x→1.1x→1.0xへ）、（2）反応ペアが少ない（reactive_pairs < 8）場合にマージボーナス強化（1.5x）、マージ機会を最大化、（3）HIGHフェーズでのバランス重視（balance_strength=60.0）でドリフト・振動を活用し、連鎖反応を誘発、（4）MEDIUMフェーズのheight_multを2.4→1.6に緩和し、MEDIUM_TOWERを削除、MEDIUMフェーズ期間を確保、（5）中央寄せボーナスを段階的に導入（max_y < 1.2で有効）。v128のシンプル構造を維持しつつ、反応ペア・HIGH_TOWER段階的緩和を追加（約130行）。


def decide(game_state: dict, analysis: dict) -> dict:
    """反応ペア動的height_penalty係数・マージボーナス強化版

    v217の失敗（スコア1064、36ターンで終了、HIGH_TOWER/HIGH_LAYERが79%支配）を受けて、
    振り子パターンを完全回避し、反応ペア情報をスコアリングの核心に据えるブレイクスルーを実施。

    v217履歴分析で特定した問題:
    - 36ターンしか持続せず、max_y=2.92で終了
    - decision_reasonがHIGH_TOWER/HIGH_LAYERが27/34ターン（79%）、高度管理が支配的
    - マージ機会が4回のみ（Turn 10, 21, 25, 26）、Turn 26以降はスコア増加0
    - 反応ペア情報を補正係数1.5xにしか使っておらず、スコアリングの核心ではない
    - height_penalty係数50.0は固定値で、反応ペアの有無にかかわらず一律

    根本原因:
    - v217は反応ペア情報を取得したが、height_penalty係数は50.0の固定値のまま
    - 反応ペアが少ない時は「これ以上積めばマージできる」という物理的期待があるため、
      height_penaltyを緩和して積極的にピースを落とすべきだが、固定係数では不可能
    - マージボーナス（DIRECT=1200）はheight_penalty（height_mult×50.0）に対して弱すぎる

    解決策（振り子パターン解消のブレイクスルー）:
    - height_penalty係数の動的調整を導入：
      * 反応ペアが少ない（reactive_pairs < 8）: 20.0（緩和、積極的に落とす）
      * 反応ペアが多い（reactive_pairs >= 8）: 50.0（厳格、慎重に選ぶ）
    - マージボーナス強化：DIRECT=1800/NEAR=900/FAR=300（v217の1.5倍補正をベース値に統合）
    - 高度管理マルチプライヤ緩和：MEDIUM=1.4、HIGH=1.6（v217の1.6/1.8から緩和）
    - HIGH_TOWERペナルティ完全削除：v217の段階的緩和も削除し、単純化
    - v128のシンプル構造を維持：フェーズ判定、balance_strength、drift_penalty等
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

    # リアクター状態を取得（v218: 反応ペア情報をスコアリングの核心に）
    reactor = analysis.get("reactor", {})
    reactive_pairs_raw = reactor.get("reactive_pairs", 0)
    # reactive_pairsがリストの場合は長さを取得、整数の場合はそのまま使用
    reactive_pairs = (
        len(reactive_pairs_raw)
        if isinstance(reactive_pairs_raw, list)
        else reactive_pairs_raw
    )

    # v218: height_penalty係数の動的調整（反応ペアに応じて）
    if reactive_pairs < 8:
        height_penalty_coeff = 20.0  # 反応ペア少ない：緩和、積極的に落とす
        penalty_reason = "LOW_REACTIVE"
    else:
        height_penalty_coeff = 50.0  # 反応ペア多い：厳格、慎重に選ぶ
        penalty_reason = "HIGH_REACTIVE"

    # フェーズ判定（v128の閾値0.8/1.8/3.0を維持）
    if max_y < 0.8:
        phase = "LOW"
        height_mult = 1.0
        merge_mult = 1.2
    elif max_y < 1.8:
        phase = "MEDIUM"
        height_mult = 1.4  # v218: v217の1.6から緩和
        merge_mult = 1.0
    elif max_y < 3.0:
        phase = "HIGH"
        height_mult = 1.6  # v218: v217の1.8から緩和
        merge_mult = 1.0
    else:
        phase = "CRITICAL"
        height_mult = 1.0  # CRITICAL: height_multなし
        merge_mult = 0.6  # v217: v128の0.6を維持

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

        # === v218: 反応ペア動的height_penalty係数・マージボーナス強化 ===

        # 1. マージグレードによるスコア（v218: マージボーナス強化）
        if merge_grade == "DIRECT":
            score += 1800.0 * merge_mult  # v218: 1200→1800（1.5倍）
            reasons.append("DIRECT_MERGE")
        elif merge_grade == "NEAR":
            score += 900.0 * merge_mult  # v218: 600→900（1.5倍）
            reasons.append("NEAR_MERGE")
        elif merge_grade == "FAR":
            score += 300.0 * merge_mult  # v218: 200→300（1.5倍）
            reasons.append("FAR_MERGE")

        # height_penalty係数に応じた理由を追加
        reasons.append(penalty_reason)

        # 2. 高度によるペナルティ（v218: height_penalty係数動的調整）
        height_penalty = landing_y * height_penalty_coeff * height_mult

        # v218: HIGH_TOWERペナルティ完全削除（v217の段階的緩和も削除）
        if landing_y > 0.0:
            reasons.append("HIGH_LAYER")

        score -= height_penalty

        # 3. ドリフトによるペナルティ（一律30.0を維持）
        drift_penalty = (abs(drift_x) + drift_unc) * 30.0
        score -= drift_penalty

        # 4. 左右バランス補正（v128の設定を維持）
        balance_strength = 20.0
        if phase == "HIGH":
            balance_strength = 40.0  # v128: v128の40.0を維持
        elif phase == "MEDIUM":
            balance_strength = 30.0  # v128: v128の30.0を維持

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
