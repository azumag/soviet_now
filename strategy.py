#!/usr/bin/env python3
"""strategy.py - AI改善対象の決定スクリプト"""

# 固定インターフェース:
# decide(game_state: dict, analysis: dict) -> dict
#    戻り値: {"x": float, "reason": str}
#
# AI改変可能: decide() 内部、ヘルパー関数、定数、import
# AI改変禁止: decide() シグネチャ、if __name__ == "__main__" ブロック

# --- 変更履歴 ---
# v411: マージボーナス適正化・v42復帰版 - v410の失敗（score=959、merge_rate=12.5%）を受けて、マージボーナスが強すぎてマージ機会が少ない状況を特定。ベストスコア戦略（v2346）のv42のマージボーナス（DIRECT=1200/NEAR=600/FAR=200）を採用し、マージの質を重視。HIGHフェーズのheight_mult=1.8固定（v128の成功要素）。reactive_pairs情報を完全削除（v410の維持）。batch_summary.txtの分析でHIGH_LAYERとHIGH_TOWERが支配的（50%）であり、マージボーナスが活きにくい構造を改善。
#   根本原因の特定:
#   - v410はマージボーナスを強化（DIRECT=1500/NEAR=800/FAR=300）していたが、merge_rate=12.5%と低く、マージ機会が少ない状況
#   - ベストスコア戦略（v2346）のv42はDIRECT=1200/NEAR=600/FAR=200とマージボーナスが適正
#   - v128（best_score=3689）の成功要素は「高度管理緩和（height_mult=1.8）」にあり、マージボーナス強化ではない
#   - batch_summary.txtの分析でHIGH_LAYERとHIGH_TOWERが支配的（50%）であり、マージボーナスが活きにくい構造
#   改善策（マージボーナス適正化・v42復帰）:
#   - マージボーナスをv42の設定に復帰（DIRECT=1200/NEAR=600/FAR=200）
#   - HIGHフェーズのheight_multを1.8に固定（v128の成功要素）
#   - HIGH_TOWERペナルティを1.3倍に維持（v128の設定）
#   - reactive_pairs情報を完全削除（v410の維持）
#   - ドリフトペナルティを30.0に維持（v128の設定）
#   - 左右バランス補正をv128の設定に復帰
#   - nextNext中央寄せボーナスをv128の設定に復帰
#   核心的発見: マージボーナスが強すぎると、マージ機会が少ない状況でボーナスが活きにくい。v42のマージボーナス（DIRECT=1200/NEAR=600/FAR=200）と高度管理緩和（height_mult=1.8）のバランスが最適であることが確認された。
#   成功基準: merge_rate=15%以上、またはscoreがv128の3689に近い
#   失敗基準: merge_rate=12.5%未満、またはreactive_pairsが使用される
# v412: MEDIUMフェーズHIGH_TOWER修正・先読みマージ追加版 - v411の失敗（avg_score=987、merge_rate=11%）を受けて、v411とv128の差異を特定。v128ではMEDIUMフェーズのHIGH_TOWERペナルティが1.5倍だが、v411では1.3倍に誤って設定されていた。v128の成功要素（1.5倍）を復活。また、batch_summary.txtの分析で高スコア群（merge_rate=14.9%）と低スコア群（merge_rate=7.5%）の差がmerge_rateにあることを特定。「next/nextNextが来たときにtype N-1が2個以上あれば、その近くに落とす」先読みマージ戦略を追加し、将来のマージ連鎖を構築することで高type（type10-16）のマージを優先する。
#   根本原因の特定:
#   - v411とv128の決定的な差異: MEDIUMフェーズのHIGH_TOWERペナルティ（v411:1.3倍、v128:1.5倍）
#   - v411のコメント「HIGH_TOWERペナルティを1.3倍に維持（v128の設定）」は誤りであり、v128では1.5倍
#   - 高スコア群と低スコア群の最大の差はmerge_rate（14.9% vs 7.5%）であり、マージ機会の確保が重要
#   - type12-16はマージでのみ生成され、スコアは78-120点。type1（1点）の120倍近く価値が高い
#   - 先読み戦略がないため、next/nextNextが来たときに適切な位置にピースを落とせていない
#   改善策（MEDIUMフェーズHIGH_TOWER修正・先読みマージ追加）:
#   - MEDIUMフェーズのHIGH_TOWERペナルティを1.3倍から1.5倍に修正（v128の成功要素を正確に復活）
#   - 先読みマージボーナスを追加: next/nextNextがtype Nのとき、type N-1が2個以上あればその重心付近にボーナス
#   - 高typeほどボーナスを大きく（type1=3点、type10=30点、type15=45点）して、高typeマージを優先
#   - 距離閾値2.0以内ならボーナスを付与し、遠いほどボーナスが減少
#   - 振り子パターン回避: MEDIUMフェーズのHIGH_TOWERペナルティの「1.3倍→1.5倍→1.3倍」を解消し、v128の1.5倍で固定
#   核心的発見: v411とv128の設定がほぼ同じだが、MEDIUMフェーズのHIGH_TOWERペナルティの微小な差（1.3倍 vs 1.5倍）がスコアに大きな影響を与えている。また、先読みマージ戦略を追加することで、将来のマージ連鎖を構築し、高typeのマージを優先することが重要。
#   成功基準: merge_rate=15%以上、またはavg_scoreがv411の987を大幅に上回る
#   失敗基準: merge_rate=12.5%未満、またはscoreがv411と同等以下
# v413: MEDIUMフェーズ高度管理緩和・HIGH_TOWERペナルティ軽減版 - v412の失敗（merge_rate=11%未満、HIGH_TOWERペナルティ過剰）を受けて、batch_summary.txtの分析でMEDIUM_TOWERペナルティがHIGH_TOWERと同程度に支配的であることを特定。MEDIUMフェーズのheight_multを2.4から2.2に緩和し、HIGH_TOWERペナルティを1.5倍から1.0倍に軽減。先読みマージボーナスの重みをtype * 5に増強して高typeマージを優先。
#   根本原因の特定:
#   - v412のHIGH_TOWERペナルティ1.5倍がMEDIUMフェーズでも適用され、マージ機会を損失
#   - batch_summary.txtでMEDIUM_TOWERペナルティがHIGH_TOWERと同程度に発生している（9.7% vs 8.7%）
#   - MEDIUMフェーズのheight_mult=2.4が高すぎて、マージより高度管理が優先されている
#   - 先読みマージボーナスの重みが小さく（type * 3）、高typeマージの優先度が低い
#   改善策（MEDIUMフェーズ高度管理緩和・HIGH_TOWERペナルティ軽減）:
#   - MEDIUMフェーズのheight_multを2.4から2.2に緩和（マージ機会確保）
#   - HIGH_TOWERペナルティを1.5倍から1.0倍に軽減（MEDIUM_TOWERペナルティと統合）
#   - 先読みマージボーナスの重みをtype * 3からtype * 5に増強（高typeマージを優先）
#   - 距離閾値を2.0から1.5に縮小してより近くに配置するように
#   核心的発見: MEDIUMフェーズの高度管理が過剰でマージ機会が損失している。HIGH_TOWERペナルティもMEDIUMフェーズで適用され、マージより高度管理が優先されている。先読みマージボーナスの重みを増強することで、高typeマージの優先度を上げることが重要。
#   成功基準: merge_rate=12%以上、またはscoreがv412の1345を上回る
#   失敗基準: merge_rate=10%未満、またはscoreがv412の1345と同等以下


def decide(game_state: dict, analysis: dict) -> dict:
    """v413: MEDIUMフェーズ高度管理緩和・HIGH_TOWERペナルティ軽減版"""

    results = analysis.get("results", [])

    if not results:
        return {"x": 0.0, "reason": "no analysis data"}

    best_x = 0.0
    best_score = -float("inf")
    best_reason = ""

    # 盤面情報
    pieces = game_state.get("pieces", [])
    max_y = max([p["y"] for p in pieces]) if pieces else -4.0

    # nextNextピース情報（中央寄せボーナス計算用）
    next_piece = game_state.get("next", {})
    next_next_piece = game_state.get("nextNext", {})
    next_type = next_piece.get("type", 0)
    next_next_type = next_next_piece.get("type", 0)

    # フェーズ判定（v413: MEDIUMフェーズ高度管理緩和）
    if max_y < 0.8:
        phase = "LOW"
        height_mult = 1.0
        merge_mult = 1.2
    elif max_y < 1.8:
        phase = "MEDIUM"
        height_mult = 2.2  # v413: v412の2.4から緩和（マージ機会確保）
        merge_mult = 1.0
    elif max_y < 3.0:
        phase = "HIGH"
        # v413: v128の1.8を固定（v411の維持）
        height_mult = 1.8
        merge_mult = 1.0
    else:
        phase = "CRITICAL"
        height_mult = 1.0
        merge_mult = 0.6

    # 盤面のtype別カウント（先読みマージ用）
    type_count = {}
    for p in pieces:
        t = p["type"]
        type_count[t] = type_count.get(t, 0) + 1

    for result in results:
        x = result["x"]
        landing_y = result.get("landing_y", 0)
        drift_x = result.get("drift_x", 0)
        drift_unc = result.get("drift_unc", 0)
        merge_grade = result.get("merge_grade", "NO")

        score = 0.0
        reasons = []

        # === v412: v128のMEDIUMフェーズHIGH_TOWERペナルティ修正 + 先読みマージ追加 ===

        # 1. マージグレードによるスコア（v412: v42の適正な値を維持）
        if merge_grade == "DIRECT":
            score += 1200.0 * merge_mult
            reasons.append("DIRECT_MERGE")
        elif merge_grade == "NEAR":
            score += 600.0 * merge_mult
            reasons.append("NEAR_MERGE")
        elif merge_grade == "FAR":
            score += 200.0 * merge_mult
            reasons.append("FAR_MERGE")

        # 2. 高度によるペナルティ（v413: MEDIUMフェーズ高度管理緩和・HIGH_TOWERペナルティ軽減）
        height_penalty = landing_y * 50.0 * height_mult

        # HIGH_TOWERペナルティ（v413: MEDIUMフェーズも1.0倍に軽減）
        if phase == "HIGH" and landing_y > 0.5:
            height_penalty *= 1.0  # v413: v412の1.3倍から1.0倍に軽減
            reasons.append("HIGH_TOWER")
        elif phase == "MEDIUM" and landing_y > 0.5:
            height_penalty *= (
                1.0  # v413: v412の1.5倍から1.0倍に軽減（MEDIUM_TOWERと統合）
            )
            reasons.append("MEDIUM_TOWER")
        elif landing_y > 0.0:
            reasons.append("HIGH_LAYER")

        score -= height_penalty

        # 3. ドリフトによるペナルティ（v412: v128の一律30.0を維持）
        drift_penalty = (abs(drift_x) + drift_unc) * 30.0
        score -= drift_penalty

        # 4. 左右バランス補正（v412: v128の設定を維持）
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

        # 5. nextNextが同じタイプなら中央寄せボーナス（v412: v128の設定を維持）
        if next_next_type == next_type:
            center_bonus = max(0, 1.0 - abs(x) / 2.0) * 50.0
            score += center_bonus
            reasons.append("NEXT_SAME")

        # 6. 先読みマージボーナス（v413: 重み増強・距離閾値縮小）
        # next/nextNextがtype Nのとき、type N-1が2個以上あれば、その重心付近に落とすことで将来のマージを確保
        future_merge_bonus = 0.0
        for target_type in [next_type, next_next_type]:
            if target_type > 1 and type_count.get(target_type - 1, 0) >= 2:
                prev_type_pieces = [p for p in pieces if p["type"] == target_type - 1]
                if prev_type_pieces:
                    center_x = sum(p["x"] for p in prev_type_pieces) / len(
                        prev_type_pieces
                    )
                    distance = abs(x - center_x)
                    # v413: 重みtype * 5に増強、距離閾値1.5に縮小
                    bonus = max(0, 1.5 - distance) * target_type * 5
                    future_merge_bonus += bonus

        if future_merge_bonus > 0:
            score += future_merge_bonus
            reasons.append("FUTURE_MERGE")

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
            "merge_history": [],
        }
    except Exception as e:
        analysis = {
            "results": [],
            "same_type": [],
            "reactor": {},
            "merge_history": [],
            "error": str(e),
        }

    result = decide(game_state, analysis)
    print(json.dumps(result, ensure_ascii=False, indent=2))
