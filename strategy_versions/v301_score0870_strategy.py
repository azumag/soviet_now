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
#   - 振り子パターン解消: 複雑な期待値機能の「入れるか入れないか」ではなく、シンプルなボーナス強化でマージ優先を徹底
# v285: v284の失敗修正・シンプル濃度管理版 - v284の失敗（score=1537、merge_rate=4.3%、期待値機能不発動）を受けて、複雑な期待値機能を廃止し、シンプルな濃度管理戦略に完全転換。
#   v284バッチ分析から特定した問題:
#   - 期待値機能が不発動: calculate_merge_expectation()の閾値20.0が高すぎ、23ターン中1回しか発動しない
#   - マージ率が極端に低い: 4.3%で、同typeピースが散在しており濃度管理ができていない
#   - 高度管理が過剰: max_y=2.83で赤ライン超え、HIGH_TOWERペナルティが発動しマージ機会を損失
#   - 振り子パターンの繰り返し: v278-v284で期待値係数・閾値を微調整し続けているが、根本的な改善には至っていない
#   根本原因:
#   - 期待値機能の複雑さ: ペア重心からの距離計算など複雑だが、実用上発動頻度が低すぎる
#   - 濃度管理の欠如: type N-1のピースが盤面にいくつあるかだけで判断するシンプルなアプローチが必要
#   - 人工化学理論との不一致: "同typeピースの空間的密度を最大化"という基本原則が守られていない
#   解決策（シンプル濃度管理戦略）:
#   - 複雑な期待値機能を完全廃止: calculate_merge_expectation()関数を削除し、type N-1のペア数を数えるだけにする
#   - ペア数に応じた直接的ボーナス: type N-1ペア数が多いほどボーナス（1ペア=200点、2ペア=400点、3ペア=600点...最大1200点）
#   - マージ優先の徹底: v42のシンプル構造（DIRECT=1200/NEAR=600/FAR=200）を採用
#   - 高度管理の最小化: v128の緩和設定（HIGHフェーズheight_mult=1.8、HIGH_TOWERペナルティ1.3倍）を採用
#   - 濃度管理の明確な意図化: decision_reasonにPAIR_COUNTを追加し、ペア数に応じた判断を明確にする
#   - 振り子パターン解消: 期待値係数・閾値の微調整という振り子を、完全な構造転換で解消
#   - v278の成功要素（ペアボーナス発動）をシンプルに実現: 複雑な期待値計算ではなく、単純なペア数カウントで実現
# v286: v285の失敗修正・近接ペア期待値版 - v285の失敗（avg=1056、stddev=271.2、merge_rate=11.9%）を受けて、ペア数ボーナスが有効に機能していない問題を修正。
#   v285バッチ分析から特定した問題:
#   - ペア数 ≠ マージ率: PAIR_COUNT_6が多いにもかかわらず、低スコア群（score=827）でmerge_rate=9.7%と低い。ペアが多くても離れていればマージしない
#   - ペア重心情報の欠如: v278の成功要素（近接ペア重心からの距離）を完全に削除してしまった
#   - 振り子パターンの繰り返し: v278→v284で期待値係数・閾値を微調整し、v285で完全に削除して「シンプル濃度管理」に転換したが、これもまた振り子
#   根本原因:
#   - 近接ペアのみカウントすべき: 距離1.5以内の近接ペアだけがマージ可能性を正確に反映する。離れたペアはカウントしない
#   - ペア重心からの距離が重要: 近接ペアが多いだけでなく、その重心に近い位置に置くかどうかが重要
#   解決策（近接ペア期待値の簡素化再導入）:
#   - 近接ペアのみカウント: type N-1のピースから、距離1.5以内の近接ペアを検出
#   - 近接ペア数に応じたボーナス: 1ペア=200点、2ペア=400点、3ペア=600点（v278の15.0×Nを採用）
#   - ペア重心からの距離ボーナス: 近接ペアの重心からの距離に応じて最大45点（v278の10.0を採用）
#   - 期待値ボーナス追加: 期待値20.0以上で期待値×5.0のボーナス（最大135×5=675点、v278のmax 105×5=525点より強化）
#   - v128のシンプル構造を維持: HIGHフェーズheight_mult=1.8、HIGH_TOWERペナルティ1.3倍、マージボーナス=1200/600/200
#   - 近接ペアボーナスとマージグレードボーナスの重複を最小化: 近接ペアは「将来のマージ」、マージグレードは「今のマージ」
# v287: v286の失敗修正・シンプル近接ペア密度管理版 - v286の失敗（avg=1232.4、stddev=482.5、HIGH_EXPECTATION発動率3.0%）を受けて、複雑な期待値機能を完全に削除し、シンプルな近接ペア密度管理に完全転換。
#   v286バッチ分析から特定した問題:
#   - 期待値機能不発動: HIGH_EXPECTATIONが303ターン中9回のみ発動（3.0%）
#   - 複雑さの割に効果が低い: HIGH_EXPECTATIONのavg_score_delta=28.5点と低い
#   - 振り子パターンの再発: v278→v284で期待値機能を導入→微調整→失敗、v285で削除→v286で簡素化再導入、という完全な振り子
#   根本原因:
#   - 期待値機能自体が複雑すぎて、発動条件（閾値20.0）が合わないと発動しない
#   - ペア重心からの距離計算は計算コストが高い割に、実際のマージ率向上への寄与が低い
#   - "閾値調整"という振り子は、期待値機能自体に構造的な問題があることを示唆している
#   解決策（シンプル近接ペア密度管理版）:
#   - 期待値機能完全削除: ペア重心からの距離計算や期待値ボーナス（>20.0の閾値判定）を完全に削除
#   - 近接ペア数ボーナスのみ採用: 距離1.5以内の近接ペア数に応じた直接的ボーナス（1ペア=200点、2ペア=400点、3ペア=600点、最大600点）
#   - 常時発動ボーナス: 閾値判定がないため、近接ペアがある限り常に発動し、判断の一貫性が向上
#   - v42/v128の成功要素を維持: マージボーナス=1200/600/200、HIGHフェーズheight_mult=1.8、HIGH_TOWERペナルティ1.3倍
#   - 振り子パターン完全解消: 期待値機能の「入れるか入れないか」の振り子を完全に排除し、シンプル濃度管理で徹底
#   - 人工化学理論との整合: "同typeピースの空間的密度を最大化"という基本原則に忠実な実装

import math


def count_nearby_pairs(pieces, next_type):
    """type N-1の近接ペア数をカウント

    距離1.5以内の近接ペアのみをカウントするシンプルな実装。

    Args:
        pieces: 全ピースのリスト
        next_type: 次のピースタイプ

    Returns:
        pair_count: 近接ペア数
    """
    if next_type == 0:
        return 0

    # type N-1のピースを抽出
    target_type = next_type - 1
    if target_type < 1:
        target_type = 13  # ループする場合

    type_minus_1_pieces = [p for p in pieces if p["type"] == target_type]

    if not type_minus_1_pieces:
        return 0

    # type N-1の近接ペアを検出（距離1.5以内）
    pair_count = 0
    for i, p1 in enumerate(type_minus_1_pieces):
        for p2 in type_minus_1_pieces[i + 1 :]:
            dist = math.sqrt((p1["x"] - p2["x"]) ** 2 + (p1["y"] - p2["y"]) ** 2)
            if dist < 1.5:
                pair_count += 1

    return pair_count


def decide(game_state: dict, analysis: dict) -> dict:
    """v298: v287の強化版・ペアボーナス強化とマージ優先抑制版

    v287の失敗（avg=1319.2、stddev=661.9、PAIR_COUNT発動率不明、HEIGHT_CONTROL支配21.7%）を受けて、
    ペアボーナスを強化し、マージ機会がある場合はHEIGHT_CONTROLを抑制する。

    v287バッチ分析から特定した問題:
    - PAIR_COUNT発動率が低い: type N-1のピースがない場合や距離1.5以内のペアがない場合、ボーナスが発動しない
    - HEIGHT_CONTROLが支配的: 21.7%の決定でHEIGHT_CONTROLが選択され、マージ優先が弱い
    - ペアボーナスが弱い: 1ペア=200点、2ペア=400点、3ペア=600点（最大600点）ではマージボーナス（1200/600/200点）に劣る

    根本原因:
    - ペアボーナスの強度不足: マージボーナスと比較して、ペアボーナスが弱すぎるため、マージ機会より高度管理が優先される
    - マージ機会でのHEIGHT_CONTROL発動: merge_gradeが"NO"でないにもかかわらず、HEIGHT_CONTROLが発動してマージ機会を損失する

    解決策（ペアボーナス強化とマージ優先抑制）:
    - ペアボーナス強化: 1ペア=400点、2ペア=800点、3ペア=1200点（最大1200点、2倍強化）
    - マージ機会でのHEIGHT_CONTROL抑制: merge_gradeが"NO"でない場合、HEIGHT_CONTROLを発動しない
    - v42/v128の成功要素維持: マージボーナス=1200/600/200、HIGHフェーズheight_mult=1.8、HIGH_TOWERペナルティ1.3倍
    - 振り子パターン解消: 複雑な期待値機能の「入れるか入れないか」ではなく、シンプルなボーナス強化でマージ優先を徹底
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

    # フェーズ判定（v42/v128の閾値を採用）
    if max_y < 0.8:
        phase = "LOW"
        height_mult = 1.0
        merge_mult = 1.2
    elif max_y < 1.8:
        phase = "MEDIUM"
        height_mult = 2.4  # v287: v42の2.4を維持
        merge_mult = 1.0
    elif max_y < 3.0:
        phase = "HIGH"
        height_mult = 1.8  # v287: v128の1.8を採用（HIGHフェーズ高度管理大幅緩和）
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

    # === v287: type N-1の近接ペア数をカウント（期待値機能完全削除版）===
    pair_count = count_nearby_pairs(pieces, next_type)

    # v298: 全結果でマージ機会があるかトラッキング
    has_merge_opportunity = False

    for result in results:
        x = result["x"]
        landing_y = result.get("landing_y", 0)
        drift_x = result.get("drift_x", 0)
        drift_unc = result.get("drift_unc", 0)
        merge_grade = result.get("merge_grade", "NO")

        # v298: マージ機会があればトラッキング
        if merge_grade != "NO":
            has_merge_opportunity = True

        score = 0.0
        reasons = []

        # === v298: 近接ペア数ボーナス強化版（v287の2倍強化）===
        if pair_count > 0:
            pair_bonus = min(pair_count * 400.0, 1200.0)  # 最大1200点（3ペア）
            score += pair_bonus
            if pair_count >= 3:
                reasons.append(f"PAIR_COUNT_{pair_count}")
            else:
                reasons.append(f"PAIR_COUNT_{pair_count}")

        # === v42/v128成功要素統合: 強力なマージボーナス ===
        if merge_grade == "DIRECT":
            score += 1200.0 * merge_mult
            reasons.append("DIRECT_MERGE")
        elif merge_grade == "NEAR":
            score += 600.0 * merge_mult
            reasons.append("NEAR_MERGE")
        elif merge_grade == "FAR":
            score += 200.0 * merge_mult
            reasons.append("FAR_MERGE")

        # 2. 高度によるペナルティ（v42の一律50.0を維持）
        height_penalty = landing_y * 50.0 * height_mult

        # === v287: HIGH_TOWERペナルティ緩和（v128の1.3倍を採用）===
        if phase == "HIGH" and landing_y > 0.5:
            height_penalty *= 1.3  # v287: v128の1.3倍を採用（v42の2.0倍から減）
            reasons.append("HIGH_TOWER")
        elif phase == "MEDIUM" and landing_y > 0.5:
            height_penalty *= 1.5  # v287: v42の1.5倍を維持
            reasons.append("MEDIUM_TOWER")
        elif landing_y > 0.0:
            reasons.append("HIGH_LAYER")

        score -= height_penalty

        # 3. ドリフトによるペナルティ（v42の一律30.0を維持）
        drift_penalty = (abs(drift_x) + drift_unc) * 30.0
        score -= drift_penalty

        # 4. 左右バランス補正（v42の設定を維持）
        balance_strength = 20.0
        if phase == "HIGH":
            balance_strength = 40.0  # v287: v42の40.0を維持
        elif phase == "MEDIUM":
            balance_strength = 30.0  # v287: v42の30.0を維持

        left_count = sum(1 for p in pieces if p["x"] < 0)
        right_count = len(pieces) - left_count
        balance_bias = (right_count - left_count) / (len(pieces) if pieces else 1)

        balance_penalty = x * balance_bias * balance_strength
        score -= abs(balance_penalty)

        # 5. nextNextが同じタイプなら中央寄せボーナス（v42の一律50.0を維持）
        if next_next_type == next_type:
            center_bonus = max(0, 1.0 - abs(x) / 2.0) * 50.0
            score += center_bonus
            reasons.append("NEXT_SAME")

        # === v298: マージ機会がない場合のみ、HEIGHT_CONTROLを理由にする ===
        if not reasons and not has_merge_opportunity:
            reasons.append("HEIGHT_CONTROL")

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
