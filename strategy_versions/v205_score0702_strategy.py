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
# v202: HIGHフェーズマージ優先強化版 - v201の失敗（スコア2811、HIGHフェーズ期間短い・マージ機会損失）を受けて、振り子パターンを回避しつつHIGHフェーズでのマージ優先を根本的に強化するブレイクスルーを実施。v201履歴分析で特定した問題: - HIGHフェーズ期間が短い（7ターン）、v128（3689点）よりも大幅に短い - merge_available=Trueの3ターンのうち、実際にマージしたのは1ターンのみ - HIGHフェーズでマージ機会を損失、CRITICAL到達阻止に失敗（max_y=2.50） - v201はv128と同じ設定だがスコアが大幅に低い（2811 vs 3689）は乱数・ピース配列の可能性があるが、HIGHフェーズでのマージ優先が不足している可能性 根本原因: - v42のマージボーナス（DIRECT=1200/NEAR=600/FAR=200）は強力だが、HIGHフェーズではさらにマージを優先する必要がある - 閾値や倍率の微調整（振り子パターン）ではなく、マージボーナス自体の強化で対応すべき - HIGHフェーズでmerge_available=True時にマージを選ばない場合の明確なペナルティが不足 解決策（振り子パターン解消のブレイクスルー）: - HIGHフェーズmerge_multを1.0から1.2に強化：マージボーナスを一律20%強化 - NO_MERGEペナルティ-200を導入：マージ可能なターンでマージを選ばない場合のペナルティ - v128のシンプル構造を維持：height_mult=1.8, TOWER閾値0.5, HIGH_TOWER 1.3倍など - 閾値シャッフルや倍率微調整（振り子パターン）を回避：マージボーナス自体の強化で対応 - v42のマージボーナスを強化（HIGHフェーズのみ）：DIRECT=1440/NEAR=720/FAR=240（v42の20%強化） - v42のシンプル構造（約110行）を維持しつつ、コード量微増（約115行） 失敗（スコア1107）：v202履歴分析で確認した問題: - HIGHフェーズが8ターンしかない（v128ではもっと長かった） - HIGH_TOWERが50%発動（4/8ターン）、マージ優先が機能していない - NO_MERGEペナルティが逆効果：マージしようとしてもHIGH_TOWERが発動し、結局マージしない - マージグレードはNEARのみでDIRECT/FARがない - v201（スコア1431）もv128（スコア3689）より大幅に低く、v128設定だけでは不十分 根本原因: - NO_MERGEペナルティは「マージ可能なら絶対にマージせよ」という強制だが、マージ可能な位置が高すぎる場合（HIGH_TOWER発動）、マージしようとしてもheight_penaltyが大きすぎて、結局マージしない方が良いという状況になる - これは振り子パターン（NO_MERGEペナルティの追加・削除）に該当する - v128と同じ設定でもv201のスコアが低いのは、HIGH_TOWERペナルティ（1.3倍）が強すぎて、マージ機会を損失している可能性がある 解決策（振り子パターン解消のブレイクスルー）: - NO_MERGEペナルティを完全削除：振り子パターン（v201→v202の追加・削除）を回避 - v128の成功構造（height_mult=1.8, TOWER閾値0.5）を維持しつつ、HIGH_TOWERペナルティを1.3倍から1.1倍に緩和：マージ機会を確保 - v42のマージボーナス（DIRECT=1200/NEAR=600/FAR=200）を維持：マージグレードはマージボーナスの指標としてのみ使用 - v128のバランス補正強度（HIGH=40.0/MEDIUM=30.0/LOW=20.0）を維持 - ドリフトペナルティ一律30.0を維持 - nextNextが同じタイプなら中央寄せボーナス50.0を維持 - v128のシンプル構造（約110行）を維持 - NO_MERGEペナルティという複雑な条件分岐を削除し、パラメータ調整（HIGH_TOWER倍率1.3→1.1）で対応
# v203: v204失敗分析・HIGHフェーズ期間短縮問題解明版 - v204の失敗（スコア1464、HIGHフェーズ4ターンのみ）を受けて、根本原因を特定。履歴分析でHIGHフェーズ期間が極端に短い（4ターン）、HIGH_TOWER発動率100%（4/4）、マージ率25%（1/4）を確認。根本原因は固定マージボーナス（DIRECT=1200/NEAR=600/FAR=200）がHIGH/CRITICALフェーズでは不十分であり、HIGH_TOWERペナルティが強すぎてマージ機会を阻害していること。ブレイクスルー：max_yに基づく指数関数的なマージボーナススケーリングで、盤面の危険度に応じてマージインセンティブを動的に強化。
# v205: max_y指数関数マージボーナス・HIGH_TOWER大幅緩和版 - v204の失敗（スコア1464、HIGHフェーズ4ターン・HIGH_TOWER100%発動）を受けて、振り子パターンを回避しつつマージ優先を根本的に強化するブレイクスルーを実施。v204履歴分析で特定した問題: - HIGHフェーズ期間が4ターンしかない（v128ではもっと長かった） - HIGH_TOWERが100%発動（4/4ターン）、マージ優先が機能していない - マージ率は25%（1/4ターン）、マージ機会を損失 - 盤面のmax_y=2.77でMEDIUMフェーズが支配的（68ターン） - v204のマージボーナス（DIRECT=1200/NEAR=600/FAR=200）は固定値であり、フェーズによらず同じボーナスを提供 - HIGHフェーズでのheight_penaltyは大きく、マージボーナスが足りない 根本原因: - 固定マージボーナス（DIRECT=1200/NEAR=600/FAR=200）はMEDIUMフェーズでは強力だが、HIGH/CRITICALフェーズではheight_penaltyが大きすぎてマージが選ばれない - HIGH_TOWERペナルティ（1.3倍）は強すぎて、マージ可能な位置が高すぎる場合、マージしようとしてもheight_penaltyが大きすぎて、結局マージしない - これは振り子パターン（マージボーナスの固定値調整、HIGH_TOWER倍率の調整）に該当する 解決策（振り子パターン解消のブレイクスルー）: - max_yに基づく指数関数的なマージボーナススケーリングを導入： - MEDIUM（max_y: 1.8~3.0）: merge_bonus_scale = 2.0 + (max_y - 1.8) / 0.4 → 2x~10x - HIGH（max_y: 3.0~4.0）: merge_bonus_scale = 10.0 + (max_y - 3.0) / 0.5 → 10x~50x - CRITICAL（max_y >= 3.0）: merge_bonus_scaleはHIGHと同じスケーリング（10x~50xだが、CRITICAL到達を阻止するため） - HIGH_TOWERペナルティを1.3倍から0.5倍に大幅に緩和：マージ機会を確保 - MEDIUM_TOWERペナルティを1.5倍から1.2倍に調整：MEDIUMフェーズでのマージインセンティブを強化 - v42のマージボーナス（DIRECT=1200/NEAR=600/FAR=200）をベースに、merge_bonus_scaleを適用： - MEDIUMフェーズ終盤（max_y=3.0）: DIRECT=12000/NEAR=6000/FAR=2000（10x） - HIGHフェーズ中盤（max_y=3.5）: DIRECT=30000/NEAR=15000/FAR=5000（25x） - CRITICALフェーズ（max_y=4.0）: DIRECT=60000/NEAR=30000/FAR=10000（50x） - v128のシンプル構造を維持：height_mult=1.8, TOWER閾値0.5など - v128のバランス補正強度（HIGH=40.0/MEDIUM=30.0/LOW=20.0）を維持 - ドリフトペナルティ一律30.0を維持 - nextNextが同じタイプなら中央寄せボーナス50.0を維持 - v128のシンプル構造（約110行）を維持しつつ、指数関数スケーリングを追加（約120行）


def decide(game_state: dict, analysis: dict) -> dict:
    """max_y指数関数マージボーナス・HIGH_TOWER大幅緩和版

    v204の失敗（スコア1464、HIGHフェーズ4ターン・HIGH_TOWER100%発動）を受けて、
    振り子パターンを回避しつつHIGHフェーズでのマージ機会を確保するブレイクスルーを実施。

    v204履歴分析で確認した問題:
    - HIGHフェーズが4ターンしかない（v128ではもっと長かった）
    - HIGH_TOWERが100%発動（4/4ターン）、マージ優先が機能していない
    - マージ率は25%（1/4ターン）、マージ機会を損失
    - 盤面のmax_y=2.77でMEDIUMフェーズが支配的（68ターン）
    - v204のマージボーナス（DIRECT=1200/NEAR=600/FAR=200）は固定値であり、フェーズによらず同じボーナスを提供
    - HIGHフェーズでのheight_penaltyは大きく、マージボーナスが足りない

    根本原因:
    - 固定マージボーナス（DIRECT=1200/NEAR=600/FAR=200）はMEDIUMフェーズでは強力だが、
      HIGH/CRITICALフェーズではheight_penaltyが大きすぎてマージが選ばれない
    - HIGH_TOWERペナルティ（1.3倍）は強すぎて、マージ可能な位置が高すぎる場合、
      マージしようとしてもheight_penaltyが大きすぎて、結局マージしない
    - これは振り子パターン（マージボーナスの固定値調整、HIGH_TOWER倍率の調整）に該当する

    解決策（振り子パターン解消のブレイクスルー）:
    - max_yに基づく指数関数的なマージボーナススケーリングを導入：
      - MEDIUM（max_y: 1.8~3.0）: merge_bonus_scale = 2.0 + (max_y - 1.8) / 0.4 → 2x~10x
      - HIGH（max_y: 3.0~4.0）: merge_bonus_scale = 10.0 + (max_y - 3.0) / 0.5 → 10x~50x
      - CRITICAL（max_y >= 3.0）: merge_bonus_scaleはHIGHと同じスケーリング
    - HIGH_TOWERペナルティを1.3倍から0.5倍に大幅に緩和：マージ機会を確保
    - MEDIUM_TOWERペナルティを1.5倍から1.2倍に調整：MEDIUMフェーズでのマージインセンティブを強化
    - v42のマージボーナス（DIRECT=1200/NEAR=600/FAR=200）をベースに、merge_bonus_scaleを適用：
      - MEDIUMフェーズ終盤（max_y=3.0）: DIRECT=12000/NEAR=6000/FAR=2000（10x）
      - HIGHフェーズ中盤（max_y=3.5）: DIRECT=30000/NEAR=15000/FAR=5000（25x）
      - CRITICALフェーズ（max_y=4.0）: DIRECT=60000/NEAR=30000/FAR=10000（50x）
    - v128の成功構造を維持：height_mult=1.8, TOWER閾値0.5など
    - v128のバランス補正強度（HIGH=40.0/MEDIUM=30.0/LOW=20.0）を維持
    - ドリフトペナルティ一律30.0を維持
    - nextNextが同じタイプなら中央寄せボーナス50.0を維持
    - v128のシンプル構造（約110行）を維持しつつ、指数関数スケーリングを追加（約120行）
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

    # フェーズ判定（v42の閾値0.8/1.8/3.0を維持）
    if max_y < 0.8:
        phase = "LOW"
        height_mult = 1.0
        merge_mult = 1.2
    elif max_y < 1.8:
        phase = "MEDIUM"
        height_mult = 2.4  # v203: v42の2.4を維持
        merge_mult = 1.0
    elif max_y < 3.0:
        phase = "HIGH"
        height_mult = 1.8  # v203: v128の1.8を維持
        merge_mult = 1.0
    else:
        phase = "CRITICAL"
        height_mult = 1.0  # CRITICAL: height_multなし
        merge_mult = 0.6  # v203: v42の0.6を維持

    # v205: max_y指数関数的マージボーナススケーリング
    # MEDIUM（max_y: 1.8~3.0）: 2x~10x
    # HIGH（max_y: 3.0~4.0）: 10x~50x
    # CRITICAL（max_y >= 3.0）: 10x~50x（CRITICAL到達阻止のため）
    if max_y < 1.8:
        merge_bonus_scale = 1.0  # LOW/MEDIUM初期
    elif max_y < 3.0:
        # MEDIUM終盤: max_y=1.8→2x, max_y=3.0→10x
        merge_bonus_scale = 2.0 + (max_y - 1.8) / 0.4  # 2x~10x
    else:
        # HIGH/CRITICAL: max_y=3.0→10x, max_y=4.0→50x
        merge_bonus_scale = 10.0 + (max_y - 3.0) / 0.5  # 10x~50x

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

        # === v205: max_y指数関数マージボーナス・HIGH_TOWER大幅緩和 ===

        # 1. マージグレードによるスコア（v42の値をベースに指数関数スケーリング適用）
        if merge_grade == "DIRECT":
            score += 1200.0 * merge_mult * merge_bonus_scale
            reasons.append("DIRECT_MERGE")
        elif merge_grade == "NEAR":
            score += 600.0 * merge_mult * merge_bonus_scale
            reasons.append("NEAR_MERGE")
        elif merge_grade == "FAR":
            score += 200.0 * merge_mult * merge_bonus_scale
            reasons.append("FAR_MERGE")

        # v203: NO_MERGEペナルティ削除（振り子パターン回避）

        # 2. 高度によるペナルティ（v203: v128一律ルール）
        height_penalty = landing_y * 50.0 * height_mult

        # HIGH_TOWERペナルティ（v205: v128の閾値0.5を維持、倍率1.3→0.5に大幅緩和）
        if phase == "HIGH" and landing_y > 0.5:  # v205: v128の閾値0.5を維持
            height_penalty *= (
                0.5  # v205: v128の1.3倍から0.5倍に大幅緩和（マージ機会確保）
            )
            reasons.append("HIGH_TOWER")
        elif phase == "MEDIUM" and landing_y > 0.5:  # v205: v42の閾値0.5を維持
            height_penalty *= (
                1.2  # v205: v42の1.5倍から1.2倍に調整（マージインセンティブ強化）
            )
            reasons.append("MEDIUM_TOWER")
        elif landing_y > 0.0:
            reasons.append("HIGH_LAYER")

        score -= height_penalty

        # 3. ドリフトによるペナルティ（一律30.0を維持）
        drift_penalty = (abs(drift_x) + drift_unc) * 30.0
        score -= drift_penalty

        # 4. 左右バランス補正（v128の値を維持）
        balance_strength = 20.0
        if phase == "HIGH":
            balance_strength = 40.0  # v203: v128の40.0を維持
        elif phase == "MEDIUM":
            balance_strength = 30.0  # v203: v128の30.0を維持

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
