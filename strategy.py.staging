#!/usr/bin/env python3
"""strategy.py - ソ連パズルゲームの AI ドロップ位置決定スクリプト

ゲーム概要:
  - ピースをドロップし、同type同士が接触すると併合 (N+N → N+1)
  - スコアテーブル: type1=1, type2=3, type3=6, ..., typeN = N*(N+1)/2
  - ボード: x ∈ [-3.0, +3.0], 床 y=-4.48, デッドライン y=3.32
  - プレイヤーが制御できるのはドロップX座標のみ

決定ロジック (6つの評価軸):
  1. 併合ボーナス        — 即座に併合できる位置に高得点 (DIRECT > NEAR > FAR)
  2. 高度ペナルティ      — 着地位置が高いほど減点 (フェーズで重み変動)
  3. ドリフトペナルティ    — ポリゴン形状による着地後のズレを減点
  4. 左右バランス補正    — ピース数の偏りを是正する方向にボーナス
  5. nextNext中央寄せ    — 次の次と同typeなら中央に寄せて併合準備
  6. 連鎖併合ボーナス    — 併合後にさらに連鎖できる可能性を評価 (動的調整版)

フェーズ (盤面の最高Y座標で判定):
  LOW      (max_y < 0.8) : 序盤。併合優先 (merge_mult=1.2)
  MEDIUM   (0.8 ≤ max_y < 1.8) : 中盤。高度管理を強化 (height_mult=2.2)
  HIGH     (1.8 ≤ max_y < 3.0) : 終盤。併合機会確保 (height_mult=1.8)
  CRITICAL (3.0 ≤ max_y) : 危険。DIRECT併合最優先で盤面圧縮 (NEAR は慎重に)
"""

# 固定インターフェース:
# decide(game_state: dict, analysis: dict) -> dict
#    戻り値: {"x": float, "reason": str}
#
# AI改変可能: decide() 内部,ヘルパー関数,定数,import
# AI改変禁止: decide() シグネチャ,if __name__ == "__main__" ブロック

# --- 変更履歴 ---
# [BEST:3689] v126: v42ベース・HIGHフェーズ併合強化版
# v152: CHAIN_MERGE大幅強化版 - batch_summary分析でNEAR_MERGE_HIGH_LAYER_CHAIN_MERGEのavg_score_delta=56.0と非常に高いことを確認。
# v151でchain_merge_bonusを200.0に強化したが、CHAIN_MERGEの選択率はまだ低い（5.8%）。CHAIN_MERGEをさらに強化し、HEIGHT_CONTROLの選択率を減らすことでスコア向上を目指す。
# chain_merge_bonusの係数を200.0→300.0に大幅強化し、chain_distanceを3.0→3.5に緩和して、より広範囲の連鎖可能性を評価する。
# v153: CHAIN_MERGE超強化版 - batch_summary分析でNEAR_MERGE_HIGH_LAYER_CHAIN_MERGEのavg_score_delta=44.7、
# NEAR_MERGE_CHAIN_MERGEのavg_score_delta=58.7と非常に高いことを確認。
# v152でchain_merge_bonusを300.0に強化したが、CHAIN_MERGEの選択率はまだ低い（16.4%）。
# CHAIN_MERGEをさらに強化し、HEIGHT_CONTROL（選択率27.4%、avg_score_delta=2.0）の選択率を減らすことでスコア向上を目指す。
# chain_merge_bonusの係数を300.0→400.0に超強化し、chain_distanceを3.5→4.0にさらに緩和して、
# より広範囲の連鎖可能性を評価し、CHAIN_MERGE選択率を15%以上に引き上げる。
# v154: 動的連鎖併合ボーナス版 - v153のCHAIN_MERGE選択率がまだ低い(約20%)こと、HEIGHT_CONTROL(26.5%, avg_score_delta=2.9)が依然として選択されていることから、着地高に応じてCHAIN_MERGEの判定距離とボーナス係数を動的に調整するロジックを追加。
# landing_yが高いほどchain_distance_maxを拡大（3.5 + landing_y*0.5）し、chain_bonus_multiplierも強化（400.0 + landing_y*100.0）することで、HIGH_LAYER状況でのCHAIN_MERGE選択を強制的に誘導し、HEIGHT_CONTROLを減らす。構造的変更で振り子パターンを回避。

# 併合結果のスコア: type N の併合で N*(N+1)/2 点獲得
# 例: type1+1→2 で +3点, type8+8→9 で +45点, type14+14→15 で +120点
SCORE_TABLE = {i: i * (i + 1) // 2 for i in range(1, 17)}


def decide(game_state: dict, analysis: dict) -> dict:
    """v154: 動的連鎖併合ボーナス版

    v153のCHAIN_MERGE選択率がまだ低い(約20%)こと、HEIGHT_CONTROL(26.5%, avg_score_delta=2.9)が依然として選択されていることから、
    着地高に応じてCHAIN_MERGEの判定距離とボーナス係数を動的に調整するロジックを追加。
    landing_yが高いほどchain_distance_maxを拡大（3.5 + landing_y*0.5）し、chain_bonus_multiplierも強化（400.0 + landing_y*100.0）することで、
    HIGH_LAYER状況でのCHAIN_MERGE選択を強制的に誘導し、HEIGHT_CONTROLを減らす。

    Args:
        game_state: ゲーム状態 (pieces, next, nextNext, score 等)
        analysis: analyze_board.py の解析結果
            - results: 各ドロップX候補ごとの着地情報
                - x: ドロップX座標
                - landing_y: 推定着地Y座標 (高い=危険)
                - drift_x/drift_unc: ポリゴン形状による着地後ドリフト
                - merge_grade: 最良併合判定 (DIRECT/NEAR/FAR/NO)
                - merges: 各同typeピースへの個別距離・併合判定
            - reactor: 反応器状態 (reactive_pairs, near_pairs 等)

    Returns:
        {"x": ドロップX座標, "reason": 選択理由}
    """

    results = analysis.get("results", [])

    if not results:
        return {"x": 0.0, "reason": "no analysis data"}

    best_x = 0.0
    best_score = -float("inf")
    best_reason = ""

    # --- 盤面情報の収集 ---
    pieces = game_state.get("pieces", [])
    max_y = max([p["y"] for p in pieces]) if pieces else -4.0

    # --- フェーズ判定 (v42の閾値) ---
    if max_y < 0.8:
        phase = "LOW"
        height_mult = 1.0  # 低い盤面では高度ペナルティ弱め
        merge_mult = 1.2  # 併合ボーナス20%増で積極的に狙う
    elif max_y < 1.8:
        phase = "MEDIUM"
        height_mult = 1.8  # v151: height_multを2.2→1.8に緩和、併合機会確保
        merge_mult = 1.0
    elif max_y < 3.0:
        phase = "HIGH"
        height_mult = 1.8  # HIGHでは少し緩和して併合機会を確保
        merge_mult = 1.0
    else:
        phase = "CRITICAL"
        height_mult = 1.0  # CRITICALでは高度ペナルティ基本値のみ
        merge_mult = 0.6  # v42: CRITICALフェーズ併合抑制

    # --- 次のピース情報 ---
    next_piece = game_state.get("next", {})
    next_next_piece = game_state.get("nextNext", {})
    next_type = next_piece.get("type", 0)
    next_next_type = next_next_piece.get("type", 0)

    # --- Type別併合ボーナス計算 ---
    # 併合結果のtype (next_type+1) が高いほどスコア価値が高い
    # 例: type1併合 → bonus=330, type5併合 → bonus=510, type14併合 → bonus=1660
    merge_result_type = min(next_type + 1, 16)
    type_merge_bonus = SCORE_TABLE.get(merge_result_type, 10) * 10 + 300

    # --- v149: 併合後のtypeを事前計算（連鎖判定用） ---
    merged_type = min(next_type + 1, 16)

    # =======================================================================
    #  各ドロップ候補 (x座標) を6つの評価軸でスコアリング
    # =======================================================================
    for result in results:
        x = result["x"]
        landing_y = result.get("landing_y", 0)
        drift_x = result.get("drift_x", 0)
        drift_unc = result.get("drift_unc", 0)
        merge_grade = result.get("merge_grade", "NO")  # DIRECT/NEAR/FAR/NO

        score = 0.0
        reasons = []

        # ----- 評価軸 1: 併合ボーナス -----
        # analyze_board が判定した merge_grade に応じてボーナス
        # DIRECT: ターゲットに直撃 (成功率95.7%)
        # NEAR:   着地後に接触圏内 (成功率68.5%)
        # FAR:    ドリフトで接触する可能性あり (低確率)
        if merge_grade == "DIRECT":
            score += 1200.0 * merge_mult
            reasons.append("DIRECT_MERGE")
        elif merge_grade == "NEAR":
            score += 600.0 * merge_mult
            reasons.append("NEAR_MERGE")
        elif merge_grade == "FAR":
            score += 200.0 * merge_mult
            reasons.append("FAR_MERGE")

        # ----- 評価軸 2: 高度ペナルティ -----
        # 着地Y座標が高いほど大きなペナルティ。フェーズのheight_multで重み調整。
        # さらにHIGH/MEDIUMで着地が高い(>0.5)場合は追加倍率
        height_penalty = landing_y * 50.0 * height_mult

        if phase == "HIGH" and landing_y > 0.5:
            height_penalty *= 2.0
            reasons.append("HIGH_TOWER")
        elif phase == "MEDIUM" and landing_y > 0.5:
            height_penalty *= 1.5
            reasons.append("MEDIUM_TOWER")
        elif landing_y > 0.0:
            reasons.append("HIGH_LAYER")

        score -= height_penalty

        # ----- 評価軸 3: ドリフトペナルティ -----
        # ポリゴン形状のピースは着地後に転がる。ドリフト量と不確実性が大きいほど
        # 狙った位置からズレるリスクが高い
        drift_penalty = (abs(drift_x) + drift_unc) * 30.0
        score -= drift_penalty

        # ----- 評価軸 4: 左右バランス補正 (v148: 強化版) -----
        # 左右のピース数の偏りを是正する方向にボーナス。
        # balance_bias > 0 なら右が多い → 左(x<0)に置くとペナルティ減
        # v148: 盤面が高いほどbalance_strengthを大きくし、バランス制御を厳しく
        balance_strength = 20.0
        if phase == "HIGH":
            balance_strength = 50.0  # v148: HIGHではバランス制御をさらに厳しく（40.0→50.0）
        elif phase == "MEDIUM":
            balance_strength = 35.0  # v148: MEDIUMでもバランス制御を強化（30.0→35.0）

        left_count = sum(1 for p in pieces if p["x"] < 0)
        right_count = len(pieces) - left_count
        balance_bias = (right_count - left_count) / (len(pieces) if pieces else 1)

        balance_penalty = x * balance_bias * balance_strength
        score -= abs(balance_penalty)

        # ----- 評価軸 5: nextNext中央寄せ -----
        # nextNextが今のnextと同typeなら、次も併合チャンスがある。
        # 中央付近に置いておけば次ターンでどちらの方向にも併合しやすい
        if next_next_type == next_type:
            center_bonus = max(0, 1.0 - abs(x) / 2.0) * 50.0
            score += center_bonus
            reasons.append("NEXT_SAME")

        # ----- 評価軸 6: 連鎖併合ボーナス (v154: 動的調整版) -----
        # 併合が成功した場合、連鎖してさらに併合できるか評価
        # v154: 着地高に応じてCHAIN_MERGEの判定距離とボーナス係数を動的に調整
        # landing_yが高いほど、より広範囲で連鎖可能性を評価し、ボーナスも強化
        if merge_grade in ["DIRECT", "NEAR"] and result.get("merges"):
            merges = result["merges"]
            if merges:
                # 最良の併合ターゲット（距離が最も近い）を取得
                best_merge = min(merges, key=lambda m: m.get("dist", float("inf")))
                target_x = best_merge.get("x", 0)
                target_y = best_merge.get("y", 0)

                # v154: 動的パラメータ計算
                # 着地が高いほど、判定距離を拡大し、ボーナスを強化
                # 例: landing_y=0.0 → chain_distance_max=3.5, multiplier=400.0
                # 例: landing_y=1.0 → chain_distance_max=4.0, multiplier=500.0
                # 例: landing_y=2.0 → chain_distance_max=4.5, multiplier=600.0
                chain_distance_max = 3.5 + max(0, landing_y) * 0.5
                chain_bonus_multiplier = 400.0 + max(0, landing_y) * 100.0

                # 併合後のtype (merged_type) のピースが盤面上にあるか確認
                for p in pieces:
                    if p.get("type") == merged_type:
                        dist = ((p["x"] - target_x) ** 2 + (p["y"] - target_y) ** 2) ** 0.5
                        if dist < chain_distance_max:
                            # 連鎖可能性がある: 距離が近いほど大きなボーナス
                            # v154: 動的係数chain_bonus_multiplierを使用
                            chain_bonus = (chain_distance_max - dist) * chain_bonus_multiplier
                            score += chain_bonus
                            reasons.append("CHAIN_MERGE")
                            break  # 1つ見つかれば十分

        # ----- 最良候補の更新 -----
        if score > best_score:
            best_score = score
            best_x = x
            best_reason = "_".join(reasons) if reasons else "HEIGHT_CONTROL"

    # ドロップ範囲 [-3.0, +3.0] にクリップ
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
