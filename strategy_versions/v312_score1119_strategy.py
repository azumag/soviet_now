#!/usr/bin/env python3
"""strategy.py - AI改善対象の決定スクリプト"""

# 固定インターフェース:
# decide(game_state: dict, analysis: dict) -> dict
#    戻り値: {"x": float, "reason": str}
#
# AI改変可能: decide() 内部、ヘルパー関数、定数、import
# AI改変禁止: decide() シグネチャ、if __name__ == "__main__" ブロック

# --- 変更履歴 ---
# [BEST:3689] v128: HIGHフェーズマージ優先版
# [BEST:2335] v42: v19復活・v31/v29複雑化要素削除版
# v274 (1805): v229ベース - シンプル構造で1805点達成
# v298: v287の強化版・ペアボーナス強化とマージ優先抑制版 - v287の失敗（avg=1319.2、stddev=661.9、PAIR_COUNT発動率不明、HEIGHT_CONTROL支配21.7%）を受けて、ペアボーナスを強化し、マージ機会がある場合はHEIGHT_CONTROLを抑制する。
#   v287バッチ分析から特定した問題:
#   - PAIR_COUNT発動率が低い: type N-1のピースがない場合や距離1.5以内のペアがない場合、ボーナスが発動しない
#   - HEIGHT_CONTROLが支配的: 21.7%の決定でHEIGHT_CONTROLが選択され、マージ優先が弱い
#   - ペアボーナスが弱い: 1ペア=200点、2ペア=400点、3ペア=600点（最大600点）ではマージボーナス（1200/600/200点）に劣る
#   根本原因:
#   - ペアボーナスの強度不足: マージボーナスと比較して、ペアボーナスが弱すぎるため、マージ機会より高度管理が優先される
#   - マージ機会でのHEIGHT_CONTROL発動: merge_gradeが"NO"でないにもかかわらず、HEIGHT_CONTROLが発動してマージ機会を損失する
#   解決策（ペアボーナス強化とマージ優先抑制）:
#   - ペアボーナス強化: 1ペア=400点、2ペア=800点、3ペア=1200点（最大1200点、2倍強化）
#   - マージ機会でのHEIGHT_CONTROL抑制: merge_gradeが"NO"でない場合、HEIGHT_CONTROLを発動しない
#   - v42/v128の成功要素維持: マージボーナス=1200/600/200、HIGHフェーズheight_mult=1.8、HIGH_TOWERペナルティ1.3倍
#   - 振り子パターン解消: �雑な期待値機能の「入れるか入れないか」ではなく、シンプルなボーナス強化でマージ優先を徹底
# v299: v298の失敗修正・v128完全復帰版 - v298の失敗（avg=953.0、stddev=262.5、PAIR_COUNT発動率1.8%のみ）を受けて、PAIR_COUNTロジックが不要であることを確認し、v128のシンプル構造に完全復帰する。
#   v298バッチ分析から特定した問題:
#   - PAIR_COUNT機能ほぼ不発動: PAIR_COUNT_2_HIGH_TOWERが5回（1.8%）のみ発動。count_nearby_pairs()関数が複雑化しているが、ほとんど機能していない
#   - HEIGHT_CONTROL支配継続: HEIGHT_CONTROLが86回（31.5%）で支配的だが、avg_score_deltaはわずか14.9点
#   - v298の複雑化が逆効果: ペアボーナス強化・マージ機会でのHEIGHT_CONTROL抑制という複雑なロジックを追加したが、実際にはペアボーナスが発動しておらず、コード量を増やすだけ
#   根本原因:
#   - PAIR_COUNTロジック自体が不要: v42/v128はPAIR_COUNTなしでBESTスコア2335/3689を達成。PAIR_COUNTは「将来のマージ可能性」を反映するが、実際の盤面ではtype N-1のピースが少ないか、距離1.5以内の近接ペアが存在しないことが多く、機能しない
#   - v42/v128の成功構造が薄れている: ペアボーナス導入で、v42のシンプルかつ強力なマージボーナス（DIRECT=1200/NEAR=600/FAR=200）が曇っている
#   - 不要な条件分岐: has_merge_opportunityトラッキングと「マージ機会がない場合のみHEIGHT_CONTROLを理由にする」ロジックが複雑化
#   解決策（v128完全復帰）:
#   - PAIR_COUNTロジック完全削除: count_nearby_pairs()関数を削除し、ペアボーナス計算ブロックを削除
#   - v128のシンプル構造完全復帰: マージボーナス=1200/600/200、height_mult LOW=1.0/MEDIUM=2.4/HIGH=1.8/CRITICAL=1.0、HIGH_TOWERペナルティ1.3倍（HIGH）、1.5倍（MEDIUM）
#   - 不要なトラッキング変数削除: has_merge_opportunityトラッキングを削除し、シンプルなreason生成
#   - 振り子パターン完全解消: v285→v286（期待値削除→簡素化再導入）→v287（完全削除）→v298（ペアボーナス強化）というサイクルを打ち切り、実証済みのv128構造に復帰
#   - 人工化学理論との整合: PAIR_COUNTのような複雑なロジックではなく、"マージ優先・高度管理緩和"というシンプルな原則に忠実な実装
# v309: バランス補正緩和・マージボーナス強化版 - v299-v308（v128構造）の失敗（avg=946.5、stddev=465.5、HEIGHT_CONTROL支配27.3%、merge_rateの差が決定的）を受けて、スコアリングのバランスを根本的に改善。
#   v299-v308バッチ分析から特定した問題:
#   - マージ率が決定的: 高スコア群 merge_rate=16.9% vs 低スコア群 merge_rate=6.9%
#   - HEIGHT_CONTROLが支配的: 27.3%で、avg_score_delta=15.6点と低い
#   - スコアのばらつき: stddev=465.5と大きく、戦略が不安定
#   - ベストスコア（3689）と同じ構造なのに、バッチ平均スコア（946.5）が約25%
#   根本原因:
#   - バランス補正や高度管理ペナルティが強すぎて、マージ機会を犠牲にしている
#   - HIGHフェーズのbalance_strength=40.0が過剰で、バランス補正がマージ判断を阻害している
#   - マージボーナスの強度が不足していて、バランス補正や高度管理ペナルティに負けている
#   解決策（バランス補正緩和・マージボーナス強化）:
#   - バランス補正の強度を緩和: 全フェーズ一律balance_strength=20.0に統一（HIGH=40.0→20.0、MEDIUM=30.0→20.0）
#   - マージボーナス強化: DIRECT_MERGE=1200→1500、NEAR_MERGE=600→800（FAR_MERGE=200は維持）
#   - HIGHフェーズ高度管理緩和: height_multを1.8から1.5に下げ、マージ機会の確保を優先
#   - HIGH_TOWERペナルティ緩和: 1.3倍から1.2倍に下げ、マージ機会の確保を優先
#   - マージ機会がある場合、HEIGHT_CONTROLを発動しないように設定: reason生成時にマージ理由がある場合はHEIGHT_CONTROLを追加しない


def decide(game_state: dict, analysis: dict) -> dict:
    """HIGHフェーズでマージを優先し、高度管理を大幅に緩和。マージをHIGHフェーズの主要目標にする。"""

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
        height_mult = 2.4  # v128: v42の2.4を維持
        merge_mult = 1.0
    elif max_y < 3.0:
        phase = "HIGH"
        height_mult = 1.5  # v309: HIGHフェーズ高度管理緩和（v128の1.8から1.5へ、マージ機会確保を優先）
        merge_mult = 1.0
    else:
        phase = "CRITICAL"
        height_mult = 1.0  # CRITICAL: height_multなし
        merge_mult = 0.6  # v128: v42の0.6を維持

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

        # === v309: バランス補正緩和・マージボーナス強化 ===

        # 1. マージグレードによるスコア（v309: マージボーナス強化）
        if merge_grade == "DIRECT":
            score += 1500.0 * merge_mult  # v309: 1200→1500に強化
            reasons.append("DIRECT_MERGE")
        elif merge_grade == "NEAR":
            score += 800.0 * merge_mult  # v309: 600→800に強化
            reasons.append("NEAR_MERGE")
        elif merge_grade == "FAR":
            score += 200.0 * merge_mult
            reasons.append("FAR_MERGE")
        # v309: マージボーナス強化により、バランス補正や高度管理ペナルティに対してマージが優先される

        # 2. 高度によるペナルティ（v128: HIGHフェーズ高度管理大幅緩和）
        height_penalty = landing_y * 50.0 * height_mult

        # HIGH_TOWERペナルティ（v309: 緩和設定）
        if phase == "HIGH" and landing_y > 0.5:
            height_penalty *= 1.2  # v309: 1.3→1.2に緩和、マージ機会確保を優先
            reasons.append("HIGH_TOWER")
        elif phase == "MEDIUM" and landing_y > 0.5:
            height_penalty *= 1.5  # v128: v42の1.5倍を維持
            reasons.append("MEDIUM_TOWER")
        elif landing_y > 0.0:
            reasons.append("HIGH_LAYER")

        score -= height_penalty

        # 3. ドリフトによるペナルティ（v128: v42の一律30.0を維持）
        drift_penalty = (abs(drift_x) + drift_unc) * 30.0
        score -= drift_penalty

        # 4. 左右バランス補正（v309: 緩和設定）
        balance_strength = (
            20.0  # v309: 全フェーズ一律20.0に統一（HIGH=40.0→20.0、MEDIUM=30.0→20.0）
        )
        # v309: バランス補正の強度を緩和し、マージ機会の確保を優先

        left_count = sum(1 for p in pieces if p["x"] < 0)
        right_count = len(pieces) - left_count
        balance_bias = (right_count - left_count) / (len(pieces) if pieces else 1)

        balance_penalty = x * balance_bias * balance_strength
        score -= abs(balance_penalty)

        # 5. nextNextが同じタイプなら中央寄せボーナス（v128: v42の一律50.0を維持）
        if next_next_type == next_type:
            center_bonus = max(0, 1.0 - abs(x) / 2.0) * 50.0
            score += center_bonus
            reasons.append("NEXT_SAME")

        # スコア更新（v309: マージ機会がある場合、HEIGHT_CONTROLを発動しない）
        if score > best_score:
            best_score = score
            best_x = x
            # v309: マージ理由がある場合はHEIGHT_CONTROLを発動しない
            if reasons:
                best_reason = "_".join(reasons)
            else:
                best_reason = "HEIGHT_CONTROL"

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
