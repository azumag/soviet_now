#!/usr/bin/env python3
"""strategy.py - AI改善対象の決定スクリプト"""

# 固定インターフェース:
# decide(game_state: dict, analysis: dict) -> dict
#    戻り値: {"x": float, "reason": str}
#
# AI改変可能: decide() 内部,ヘルパー関数,定数,import
# AI改変禁止: decide() シグネチャ,if __name__ == "__main__" ブロック

# --- 変更履歴 ---
# [BEST:604] v0: ランダム配置（ベースライン）
# [BEST:1486] v1: マージ重視戦略（DIRECT/NEAR優先、高度管理、ドリフト最小化）
# [BEST:1615] v3: 重量バランス導入版 - ピースタイプに応じた重量化、フェーズ制導入、高度管理調整
# [BEST:2015] v8: 重量バランス削除・フェーズ制再設計 - v5のロジックをベースに、重量バランス振り子パターンを解消、フェーズ閾値0.8/1.8調整
# v11: HIGHフェーズ高度管理強化版 - v10の高度管理緩和を修正、reactorチェインボーナス削除（未使用）、マージなし時のheight_penalty/no_merge_penaltyを強化
# v12: 一貫性重視・シンプル化版 - 二段階スコアリング廃止、v8の構造に戻しつつマージボーナス強化、HIGHフェーズ高度管理緩和、左右バランス計算簡素化
# v13: マージ積極化・reactor活用版 - v12をベースにマージボーナス大幅強化、HIGHフェーズ高度ペナルティ緩和、reactive_pairs活用でチェイン中のマージ優先


def decide(game_state: dict, analysis: dict) -> dict:
    """v12のシンプルな構造を維持しつつ、マージ積極化とreactor活用でスコア向上."""

    results = analysis.get("results", [])

    if not results:
        return {"x": 0.0, "reason": "no analysis data"}

    best_x = 0.0
    best_score = -float("inf")
    best_reason = ""

    # 盤面情報
    pieces = game_state.get("pieces", [])
    max_y = max([p["y"] for p in pieces]) if pieces else -4.0

    # フェーズ判定（v8の閾値0.8/1.8を維持）
    if max_y < 0.8:
        phase = "LOW"
        height_mult = 1.0
        merge_mult = 1.2
    elif max_y < 1.8:
        phase = "MEDIUM"
        height_mult = 1.8  # v13: v12の2.0→1.8に緩和（マージ機会確保）
        merge_mult = 1.0
    else:
        phase = "HIGH"
        height_mult = 2.0  # v13: v12の2.4→2.0に緩和（高度ペナルティ軽減）
        merge_mult = 1.1  # v13: HIGHフェーズでもマージ強化（1.0→1.1）

    # 左右バランス計算（簡素化：カウントベースだがv8と同じ）
    left_count = sum(1 for p in pieces if p["x"] < 0)
    right_count = len(pieces) - left_count
    balance_bias = (right_count - left_count) / (len(pieces) if pieces else 1)

    # 次のピース情報
    next_piece = game_state.get("next", {})
    next_next_piece = game_state.get("nextNext", {})
    next_type = next_piece.get("type", 0)
    next_next_type = next_next_piece.get("type", 0)

    # reactor情報（v13で本格活用）
    reactor = analysis.get("reactor", {})
    reactive_pairs_raw = reactor.get("reactive_pairs", 0)
    reactive_pairs = (
        len(reactive_pairs_raw)
        if isinstance(reactive_pairs_raw, list)
        else reactive_pairs_raw
    )

    # reactorチェインボーナス（reactive_pairs >= 3ならチェイン中と判断）
    in_chain = reactive_pairs >= 3

    for result in results:
        x = result["x"]
        landing_y = result.get("landing_y", 0)
        drift_x = result.get("drift_x", 0)
        drift_unc = result.get("drift_unc", 0)
        merge_grade = result.get("merge_grade", "NO")
        has_merge = result.get("has_merge", False)

        score = 0.0
        reasons = []

        # 1. マージグレードによるスコア（v13: ボーナス大幅強化）
        if merge_grade == "DIRECT":
            base_bonus = 1450.0  # v13: 1350→1450に強化
            if in_chain:
                base_bonus *= 1.15  # チェイン中ならさらに+15%
            score += base_bonus * merge_mult
            reasons.append("DIRECT_MERGE")
        elif merge_grade == "NEAR":
            base_bonus = 850.0  # v13: 750→850に強化
            if in_chain:
                base_bonus *= 1.12
            score += base_bonus * merge_mult
            reasons.append("NEAR_MERGE")
        elif merge_grade == "FAR":
            base_bonus = 350.0  # v13: 250→350に強化
            if in_chain:
                base_bonus *= 1.10
            score += base_bonus * merge_mult
            reasons.append("FAR_MERGE")
        else:
            # マージなしはペナルティ（v13: 全体緩和だがフェーズで調整）
            no_merge_penalty = 250.0  # v12: 200→250に増加
            if phase == "HIGH":
                no_merge_penalty *= 1.3  # v12: 1.6→1.3に緩和
            elif phase == "MEDIUM":
                no_merge_penalty *= 1.3
            else:
                no_merge_penalty *= 1.0
            score -= no_merge_penalty
            reasons.append("NO_MERGE")

        # 2. 高度によるスコア（v13: v12より緩和）
        height_penalty = landing_y * 50.0 * height_mult

        # 高盤面での追加ペナルティ（v13: 1.5→1.3倍に緩和）
        if phase == "HIGH":
            height_penalty *= 1.3  # v12: 1.5→1.3に緩和
            reasons.append("HIGH_TOWER")
        elif phase == "MEDIUM" and landing_y > 0.5:
            height_penalty *= 1.2  # v12: 1.3→1.2に緩和
            reasons.append("MEDIUM_TOWER")
        elif landing_y > 0.0:
            reasons.append("HIGH_LAYER")

        score -= height_penalty

        # 3. ドリフトによるペナルティ
        drift_penalty = (abs(drift_x) + drift_unc) * 30.0
        score -= drift_penalty

        # 4. 左右バランス補正（v8と同様だが少し緩和）
        balance_strength = 18.0  # v12: 20.0→18.0に緩和
        if phase == "HIGH":
            balance_strength = 32.0  # v12: 35.0→32.0に緩和
        elif phase == "MEDIUM":
            balance_strength = 22.0  # v12: 25.0→22.0に緩和

        balance_penalty = x * balance_bias * balance_strength
        score -= abs(balance_penalty)

        # 5. reactorチェインボーナス（v13新規）
        # reactive_pairs >= 3 ならチェイン中なので、マージをさらに優先
        if in_chain and merge_grade in ("DIRECT", "NEAR", "FAR"):
            score += 200.0
            reasons.append("CHAIN_BONUS")

        # 6. nextNextが同じタイプなら中央寄せボーナス
        if next_next_type == next_type:
            center_bonus = max(0, 1.0 - abs(x) / 2.0) * 40.0
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
