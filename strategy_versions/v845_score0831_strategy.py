#!/usr/bin/env python3
"""strategy.py - ソ連パズルゲームの AI ドロップ位置決定スクリプト

ゲーム概要:
  - ピースをドロップし、同type同士が接触すると併合 (N+N → N+1)
  - スコアテーブル: type1=1, type2=3, type3=6, ..., typeN = N*(N+1)/2
  - ボード: x ∈ [-3.0, +3.0], 床 y=-4.48, デッドライン y=3.32
  - プレイヤーが制御できるのはドロップX座標のみ

決定ロジック (5つの評価軸):
  1. 併合ボーナス   — 即座に併合できる位置に高得点 (DIRECT > NEAR > FAR)
  2. 高度ペナルティ   — 着地位置が高いほど減点 (フェーズで重み変動)
  3. Reactor保護      — 連鎖進行中は高い位置を回避
  4. ドリフトペナルティ — ポリゴン形状による着地後のズレを減点
  5. 左右バランス補正 — ピース数の偏りを是正する方向にボーナス
  6. nextNext中央寄せ — 次の次と同typeなら中央に寄せて併合準備

フェーズ (盤面の最高Y座標で判定):
  LOW      (max_y < 0.8) : 序盤。併合優先 (merge_mult=1.2)
  MEDIUM   (0.8 ≤ max_y < 1.8) : 中盤。高度管理を強化 (height_mult=2.4)
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
# v147: v126構造復帰・HIGHフェーズ積極化版 - v146の失敗（平均1334.4点、分散大きい、NEAR_MERGE選択率6.4%）を受けて、
# v126のシンプルな5要素構造に完全復帰。v141〜v143で追加した盤面整理度/平坦度スコアを削除し、
# MEDIUMフェーズのheight_multをv126の2.4に戻し、HIGHフェーズheight_multをv126の1.8に戻すことで、
# HIGHフェーズでのNEAR_MERGE機会を積極的に確保。batch_summaryでNEAR_MERGEの平均スコアデルタが33〜51点と高いことを確認。
# 盤面クラスタリング/平坦度は、v142/v146での動的調整が複雑すぎ効果不透明と判断し、削除。
# v126の成功構造をベースにしつつ、HIGHフェーズでの併合機会確保を強化。コード量削減（約240行→約140行）。

# 併合結果のスコア: type N の併合で N*(N+1)/2 点獲得
# 例: type1+1→2 で +3点, type8+8→9 で +45点, type14+14→15 で +120点
SCORE_TABLE = {i: i * (i + 1) // 2 for i in range(1, 17)}


def decide(game_state: dict, analysis: dict) -> dict:
    """v147: v126構造復帰・HIGHフェーズ積極化版

    v126のシンプルな5要素構造に完全復帰し、HIGHフェーズでのNEAR_MERGE機会を確保。
    v141〜v143で追加した盤面整理度/平坦度スコアは複雑すぎ効果不透明と判断し、削除。
    MEDIUMフェーズのheight_multをv126の2.4に戻し、HIGHフェーズheight_multをv126の1.8に戻すことで、
    バランスの取れた高度管理と積極的な併合戦略を実現。

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

    # --- フェーズ判定 ---
    # 盤面の最高Y座標で4段階に分類。フェーズごとに高度ペナルティと併合ボーナスの
    # 重みを変える。LOW: 併合狙い / MEDIUM-HIGH: 高度管理 / CRITICAL: 生存優先
    if max_y < 0.8:
        phase = "LOW"
        height_mult = 1.0  # 低い盤面では高度ペナルティ弱め
        merge_mult = 1.2  # 併合ボーナス20%増で積極的に狙う
    elif max_y < 1.8:
        phase = "MEDIUM"
        height_mult = 2.4  # v147: v126の2.4に復帰（高度管理強化）
        merge_mult = 1.0
    elif max_y < 3.0:
        phase = "HIGH"
        height_mult = 1.8  # v147: v126の1.8に復帰（併合機会確保）
        merge_mult = 1.0
    else:
        phase = "CRITICAL"
        height_mult = 1.0  # CRITICALでは高度ペナルティ基本値のみ
        merge_mult = 0.6  # v147: v126の0.6を維持（CRITICALフェーズ併合抑制）

    # --- Reactor状態 (連鎖反応の検出) ---
    # reactive_pairs: 接触圏内の同typeペア数。連鎖中は盤面が不安定なので
    # 高い位置への配置をさらにペナルティする
    reactor = analysis.get("reactor", {})
    reactive_pairs = reactor.get("reactive_pairs", [])
    if isinstance(reactive_pairs, list):
        reactor_penalty_scale = len(reactive_pairs)
    else:
        reactor_penalty_scale = 0

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

    # =======================================================================
    #  各ドロップ候補 (x座標) を5つの評価軸でスコアリング
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
        #
        # v140: CRITICALフェーズでは併合=盤面圧縮(2個→1個)なので最優先。
        # ただしNEARは失敗→即死リスクがあるため、DIRECTのみ1.5倍強化。
        if merge_grade == "DIRECT":
            direct_mult = (
                1.5 if phase == "CRITICAL" else 1.0
            )  # v140: CRITICAL時DIRECT強化
            score += type_merge_bonus * merge_mult * direct_mult
            reasons.append("DIRECT_MERGE")
        elif merge_grade == "NEAR":
            near_mult = (
                0.3 if phase == "CRITICAL" else 0.5
            )  # v140: CRITICAL時NEARは慎重に
            score += type_merge_bonus * near_mult * merge_mult
            reasons.append("NEAR_MERGE")
        elif merge_grade == "FAR":
            score += type_merge_bonus * 0.20 * merge_mult
            reasons.append("FAR_MERGE")

        # ----- 評価軸 2: 高度ペナルティ -----
        # 着地Y座標が高いほど大きなペナルティ。フェーズのheight_multで重み調整。
        # さらにHIGH/MEDIUMで着地が高い(>0.5)場合は追加倍率
        height_penalty = landing_y * 50.0 * height_mult

        if phase == "HIGH" and landing_y > 0.5:
            height_penalty *= 2.0  # HIGH_TOWER: デッドライン接近でさらに厳しく
            reasons.append("HIGH_TOWER")
        elif phase == "MEDIUM" and landing_y > 0.5:
            height_penalty *= 1.5  # MEDIUM_TOWER: MEDIUMでの高い着地を強く抑制
            reasons.append("MEDIUM_TOWER")
        elif landing_y > 0.0:
            reasons.append("HIGH_LAYER")

        score -= height_penalty

        # ----- 評価軸 3: Reactor保護 -----
        # 連鎖反応(reactive_pairs)が進行中は、高い位置に落とすと
        # 連鎖を妨害するリスクがある。ペア数に比例してペナルティ増加
        if reactor_penalty_scale > 0 and landing_y > 0.0:
            score -= landing_y * 20.0 * reactor_penalty_scale
            if "REACTOR_PROTECT" not in reasons:
                reasons.append("REACTOR_PROTECT")

        # ----- 評価軸 4: ドリフトペナルティ -----
        # ポリゴン形状のピースは着地後に転がる。ドリフト量と不確実性が大きいほど
        # 狙った位置からズレるリスクが高い
        drift_penalty = (abs(drift_x) + drift_unc) * 30.0
        score -= drift_penalty

        # ----- 評価軸 5: 左右バランス補正 -----
        # 左右のピース数の偏りを是正する方向にボーナス。
        # balance_bias > 0 なら右が多い → 左(x<0)に置くとペナルティ減
        # フェーズが高いほど balance_strength を強くして偏りを厳しく制御
        balance_strength = 20.0
        if phase == "HIGH":
            balance_strength = 40.0  # 終盤は左右バランスが崩れると即ゲームオーバー
        elif phase == "MEDIUM":
            balance_strength = 30.0

        left_count = sum(1 for p in pieces if p["x"] < 0)
        right_count = len(pieces) - left_count
        balance_bias = (right_count - left_count) / (len(pieces) if pieces else 1)

        balance_penalty = x * balance_bias * balance_strength
        score -= abs(balance_penalty)

        # ----- 評価軸 6: nextNext中央寄せ -----
        # nextNextが今のnextと同typeなら、次も併合チャンスがある。
        # 中央付近に置いておけば次ターンでどちらの方向にも併合しやすい
        if next_next_type == next_type:
            center_bonus = max(0, 1.0 - abs(x) / 2.0) * 50.0
            score += center_bonus
            reasons.append("NEXT_SAME")

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
