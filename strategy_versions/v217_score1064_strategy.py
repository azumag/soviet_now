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
    """HIGH_TOWER段階的緩和・反応ペア考慮・v128再構成版

    v216の失敗（スコア962、v128の3689点に対し74%低下）を受けて、
    根本的なアプローチ変更を実施。

    v216履歴分析で特定した問題:
    - v215/v216は「v128完全回帰」を主張しなが、実際には3つの変更で高度管理を弱体化
      * height_penalty係数: v128の50.0→40.0に25%弱体化
      * MEDIUM height_mult: v128の2.4→1.8に25%弱体化
      * MEDIUM_TOWERペナルティ: 削除
    - 盤面の急上昇によりゲーム期間が短縮（max_y=2.46で終了）
    - HIGHフェーズでHIGH_TOWERが38%発動（5/13ターン）、マージ機会を阻害
    - 反応ペア情報を活用していない
    - MEDIUMフェーズのheight_mult=2.4とMEDIUM_TOWER=1.5xは現在のピース配列では過剰

    根本原因:
    - v128の成功パラメータは当時のピース配列に最適化されていたが、現在の配列では過剰
    - 特にMEDIUMフェーズの高度管理（height_mult=2.4, MEDIUM_TOWER=1.5x）が強すぎ
    - HIGH_TOWERペナルティ1.3倍はHIGHフェーズで過剰に作用し、マージを阻害
    - 反応ペアが少ない場合にマージを優先するロジックがない

    解決策（振り子パターン解消のブレイクスルー）:
    - v128のheight_penalty係数50.0を維持：高度管理の基盤を維持
    - MEDIUMフェーズのheight_multを2.4→1.6に緩和：MEDIUMフェーズ期間を確保
    - MEDIUM_TOWERペナルティを削除：v214/v213の成功要素を再採用
    - HIGH_TOWERペナルティを段階的に緩和：HIGHフェーズ初期=1.1x、max_y上昇に伴い緩和
    - 反応ペアが少ない（reactive_pairs < 8）場合にマージボーナス強化（1.5x）
    - HIGHフェーズでのバランス重視（balance_strength=60.0）：ドリフト・振動を活用し連鎖反応を誘発
    - 中央寄せボーナスを段階的に導入（max_y < 1.2で有効）
    - v128のマージボーナス（DIRECT=1200/NEAR=600/FAR=200）を維持
    - ドリフトペナルティ一律30.0を維持
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

    # フェーズ判定（v217: v128の閾値0.8/1.8/3.0を維持）
    if max_y < 0.8:
        phase = "LOW"
        height_mult = 1.0
        merge_mult = 1.2
    elif max_y < 1.8:
        phase = "MEDIUM"
        height_mult = 1.6  # v217: v128の2.4から1.6に緩和（MEDIUMフェーズ期間確保）
        merge_mult = 1.0
    elif max_y < 3.0:
        phase = "HIGH"
        height_mult = 1.8  # v217: v128の1.8を維持
        merge_mult = 1.0
    else:
        phase = "CRITICAL"
        height_mult = 1.0  # CRITICAL: height_multなし
        merge_mult = 0.6  # v217: v128の0.6を維持

    # HIGH_TOWERペナルティ係数を動的に調整
    if phase == "HIGH":
        # max_yに応じて段階的に緩和
        if max_y < 2.0:
            high_tower_mult = 1.1  # HIGHフェーズ初期：緩和
        elif max_y < 2.5:
            high_tower_mult = 1.05  # 中盤：さらに緩和
        else:
            high_tower_mult = 1.0  # 終盤：解除
    else:
        high_tower_mult = 1.0

    # 反応ペアが少ない場合にマージボーナス強化
    if reactive_pairs < 8:
        merge_boost = 1.5
        merge_reason = "LOW_REACTIVE"
    else:
        merge_boost = 1.0
        merge_reason = ""

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

        # === v217: HIGH_TOWER段階的緩和・反応ペア考慮 ===

        # 1. マージグレードによるスコア（v217: v128の固定値を維持、反応ペア補正追加）
        effective_merge_mult = merge_mult * merge_boost
        if merge_grade == "DIRECT":
            score += 1200.0 * effective_merge_mult
            reasons.append("DIRECT_MERGE")
        elif merge_grade == "NEAR":
            score += 600.0 * effective_merge_mult
            reasons.append("NEAR_MERGE")
        elif merge_grade == "FAR":
            score += 200.0 * effective_merge_mult
            reasons.append("FAR_MERGE")

        if merge_reason:
            reasons.append(merge_reason)

        # 2. 高度によるペナルティ（v217: v128一律ルール、height_penalty係数50.0を維持）
        height_penalty = landing_y * 50.0 * height_mult

        # HIGH_TOWERペナルティ（v217: 段階的緩和を導入）
        if phase == "HIGH" and landing_y > 0.5:
            height_penalty *= high_tower_mult
            if high_tower_mult > 1.0:
                reasons.append("HIGH_TOWER")
        elif landing_y > 0.0:
            reasons.append("HIGH_LAYER")

        score -= height_penalty

        # 3. ドリフトによるペナルティ（一律30.0を維持）
        drift_penalty = (abs(drift_x) + drift_unc) * 30.0
        score -= drift_penalty

        # 4. 左右バランス補正（v217: HIGHフェーズでバランス重視）
        balance_strength = 20.0
        if phase == "HIGH":
            balance_strength = 60.0  # v217: v128の40.0から強化（ドリフト・振動を活用）
        elif phase == "MEDIUM":
            balance_strength = 30.0  # v217: v128の30.0を維持
        else:  # LOW/CRITICAL
            balance_strength = 20.0

        left_count = sum(1 for p in pieces if p["x"] < 0)
        right_count = len(pieces) - left_count
        balance_bias = (right_count - left_count) / (len(pieces) if pieces else 1)

        balance_penalty = x * balance_bias * balance_strength
        score -= abs(balance_penalty)

        # 5. nextNextが同じタイプなら中央寄せボーナス（v217: 段階的に導入）
        if next_next_type == next_type and max_y < 1.2:
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
