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
# v313: v128構造完全復帰版 - v309の失敗（avg=1122.2、stddev=221.6、HEIGHT_CONTROL支配21.5%、マージ決定率20%）を受けて、振り子パターンを解消し、v42の動的バランス調整とv128のマージ優先を統合。
#   v309バッチ分析から特定した問題:
#   - マージボーナス過強化のパラドックス: DIRECT=1500/NEAR=800にもかかわらず、マージ決定率は20%程度。かえってマージ機会損失を招いている
#   - バランス補正一律化の副作用: v42の段階的設定（LOW=20.0/HIGH=40.0/MEDIUM=30.0）が盤面の状態に応じた適切な調整を可能にしていた。一律20.0は盤面応答性を削いでいる
#   - HIGHフェーズheight_mult緩和の失敗: v128の1.8がマージ機会確保に最適だった、v309の1.5では高度管理が緩すぎ盤面が崩壊している
#   - 振り子パターン: v299→v309で「v128復帰」→「調整」のサイクルを繰り返し、v128の成功要素を損なっている
#   根本原因:
#   - �数パラメータ同時調整の失敗: v309の「バランス補正一律化 + マージボーナス強化 + 高度管理緩和」が、v128の成功バランス（マージボーナス強度 vs 高度管理緩和度 vs バランス補正動的調整）を崩した
#   - v42の動的バランス調整の喪失: 段階的なbalance_strengthは、盤面の状態に応じた適切な調整を可能にしていた。一律化はこの盤面応答性を削いだ
#   - マージ機会抑制ロジックの複雑化: 「マージ理由がある場合はHEIGHT_CONTROLを発動しない」は、v128のシンプル構造を複雑化させるだけ
#   解決策（v128基本構造への完全復帰）:
#   - マージボーナス: v128の値（DIRECT=1200、NEAR=600、FAR=200）に完全復帰
#   - 高度管理: v128の設定（height_mult HIGH=1.8、HIGH_TOWERペナルティ1.3倍）に完全復帰
#   - バランス補正: v42の段階的設定（LOW=20.0、HIGH=40.0、MEDIUM=30.0）に完全復帰
#   - マージ機会抑制ロジック削除: v309で追加した「マージ理由がある場合はHEIGHT_CONTROLを発動しない」を削除し、v128のシンプルなreason生成に復帰
#   - v42の動的バランス調整とv128のマージ優先を統合: 実証済みの2つの成功要素を組み合わせ、安定した高スコアを目指す


def decide(game_state: dict, analysis: dict) -> dict:
    """v128基本構造への完全復帰版。v42の動的バランス調整とv128のマージ優先を統合。"""

    results = analysis.get("results", [])

    if not results:
        return {"x": 0.0, "reason": "no analysis data"}

    best_x = 0.0
    best_score = -float("inf")
    best_reason = ""

    # 盤面情報
    pieces = game_state.get("pieces", [])
    max_y = max([p["y"] for p in pieces]) if pieces else -4.0

    # フェーズ判定（v42/v128の閾値0.8/1.8/3.0を維持）
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
        height_mult = 1.8  # v128: HIGHフェーズ高度管理大幅緩和（v42の2.6から1.8へ、マージ優先を徹底）
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

        # === v128: HIGHフェーズマージ優先 ===

        # 1. マージグレードによるスコア（v128: v42の強力な値を維持）
        if merge_grade == "DIRECT":
            score += 1200.0 * merge_mult
            reasons.append("DIRECT_MERGE")
        elif merge_grade == "NEAR":
            score += 600.0 * merge_mult
            reasons.append("NEAR_MERGE")
        elif merge_grade == "FAR":
            score += 200.0 * merge_mult
            reasons.append("FAR_MERGE")
        # v128: NO_MERGEペナルティの「入れるか入れないか」の振り子を回避し、第三の選択肢（高度管理大幅緩和）を採用

        # 2. 高度によるペナルティ（v128: HIGHフェーズ高度管理大幅緩和）
        height_penalty = landing_y * 50.0 * height_mult

        # HIGH_TOWERペナルティ（v128: v84の緩和設定を維持）
        if phase == "HIGH" and landing_y > 0.5:
            height_penalty *= 1.3  # v128: v84の1.3倍を採用（v42の2.0倍から減、height_mult大幅緩和と相乗効果）
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

        # 4. 左右バランス補正（v42の段階的設定を維持）
        balance_strength = 20.0
        if phase == "HIGH":
            balance_strength = 40.0  # v128: v42の40.0を維持
        elif phase == "MEDIUM":
            balance_strength = 30.0  # v128: v42の30.0を維持

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

        # スコア更新（v128: シンプルなreason生成）
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
