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
  3. ドリフトペナルティ — ポリゴン形状による着地後のズレを減点
  4. 左右バランス補正 — ピース数の偏りを是正する方向にボーナス
  5. 盤面平坦度スコア — 盤面のY座標分散を評価 (動的調整版)

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
# [BEST:2325] v42: シンプル5要素構造、MEDIUMフェーズheight_mult=2.4
# v148: v42構造ベース + 盤面平坦度動的調整 - v147の失敗（スコア分散stddev=828.7、ワースト555点の早期終了）を受けて、v147の複雑な盤面整理度スコアを削除し、v42のシンプル5要素構造に完全復帰。代わりに盤面平坦度スコアを動的調整で追加：LOWフェーズでは平坦度を無視（flatness_weight=0.0）し、HIGH/CRITICALフェーズでは平坦度を重視（flatness_weight=1.0-2.0）することで、盤面の状態に応じた戦略切り替えを実現し、スコア安定性を向上させる。MEDIUMフェーズのheight_multをv147の2.3からv42の2.4に戻し、v42の成功構造をベースにする。

# 併合結果のスコア: type N の併合で N*(N+1)/2 点獲得
# 例: type1+1→2 で +3点, type8+8→9 で +45点, type14+14→15 で +120点
SCORE_TABLE = {i: i * (i + 1) // 2 for i in range(1, 17)}


def calculate_board_flatness(pieces):
    """v148: 盤面の平坦度を計算

    盤面のY座標の分散（標準偏差）を計算し、分散が小さいほどボーナスを与える。
    平坦な盤面はピースが均一に配置され、隙間が少なく、連鎖反応が起きやすい。

    Args:
        pieces: 全ピースのリスト [{id, type, x, y, ...}, ...]

    Returns:
        flatness_score: 平坦度スコア（高いほど盤面が平坦）
    """
    if not pieces or len(pieces) < 2:
        return 0.0

    # Y座標のリストを作成
    y_values = [p["y"] for p in pieces]

    # 標準偏差を計算
    import math

    mean_y = sum(y_values) / len(y_values)
    variance = sum((y - mean_y) ** 2 for y in y_values) / len(y_values)
    std_dev = math.sqrt(variance)

    # 平坦度スコア：標準偏差が小さいほど高いボーナス
    # 標準偏差が0.5以下：強いボーナス（非常に平坦）
    # 標準偏差が1.5以下：普通のボーナス（平坦）
    # 標準偏差が2.5以上：ペナルティ（不均一）

    flatness_score = 0.0
    if std_dev < 0.5:
        # 非常に平坦：強いボーナス
        flatness_score = (0.5 - std_dev) * 200.0
    elif std_dev < 1.5:
        # 平坦：普通のボーナス
        flatness_score = (1.5 - std_dev) * 50.0
    elif std_dev > 2.5:
        # 不均一：ペナルティ
        flatness_score = -(std_dev - 2.5) * 50.0

    return flatness_score


def decide(game_state: dict, analysis: dict) -> dict:
    """v148: v42構造ベース + 盤面平坦度動的調整

    v147の失敗（スコア分散stddev=828.7、ワースト555点の早期終了）を受けて、
    v147の複雑な盤面整理度スコアを削除し、v42のシンプル5要素構造に完全復帰。
    代わりに盤面平坦度スコアを動的調整で追加：LOWフェーズでは平坦度を無視し、
    HIGH/CRITICALフェーズでは平坦度を重視することで、盤面の状態に応じた戦略切り替えを実現。
    MEDIUMフェーズのheight_multをv147の2.3からv42の2.4に戻し、v42の成功構造をベースにする。

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
    # v148: v42の閾値0.8/1.8/3.0を維持
    if max_y < 0.8:
        phase = "LOW"
        height_mult = 1.0  # 低い盤面では高度ペナルティ弱め
        merge_mult = 1.2  # 併合ボーナス20%増で積極的に狙う
    elif max_y < 1.8:
        phase = "MEDIUM"
        height_mult = 2.4  # v148: v42の2.4に戻す（v147の2.3から）
        merge_mult = 1.0
    elif max_y < 3.0:
        phase = "HIGH"
        height_mult = 1.8  # v148: v42の1.8を維持
        merge_mult = 1.0
    else:
        phase = "CRITICAL"
        height_mult = 1.0  # CRITICALでは高度ペナルティ基本値のみ
        merge_mult = 0.6  # v148: v42の0.6を維持

    # --- 次のピース情報 ---
    next_piece = game_state.get("next", {})
    next_next_piece = game_state.get("nextNext", {})
    next_type = next_piece.get("type", 0)
    next_next_type = next_next_piece.get("type", 0)

    # --- Type別併合ボーナス計算 ---
    # 併合結果のtype (next_type+1) が高いほどスコア価値が高い
    merge_result_type = min(next_type + 1, 16)
    type_merge_bonus = SCORE_TABLE.get(merge_result_type, 10) * 10 + 300

    # --- v148: 盤面平坦度スコア計算 ---
    current_flatness = calculate_board_flatness(pieces)

    # --- v148: 盤面平坦度スコアの動的調整 ---
    # 盤面が高いほど平坦度を重視（盤面の状態に応じた戦略切り替え）
    # LOWフェーズでは平坦度を無視し、HIGH/CRITICALフェーズでは平坦度を重視
    if max_y < 0.8:          # LOWフェーズ
        flatness_weight = 0.0  # 低い盤面では平坦度は無視（併合優先）
    elif max_y < 1.8:        # MEDIUMフェーズ
        flatness_weight = 0.5  # 中程度に考慮
    elif max_y < 3.0:        # HIGHフェーズ
        flatness_weight = 1.0  # 重要だが絶対的ではない
    else:                   # CRITICALフェーズ
        flatness_weight = 2.0  # 非常に重要（平坦でないと崩れる）

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
        if merge_grade == "DIRECT":
            direct_mult = 1.5 if phase == "CRITICAL" else 1.0
            score += type_merge_bonus * merge_mult * direct_mult
            reasons.append("DIRECT_MERGE")
        elif merge_grade == "NEAR":
            near_mult = 0.3 if phase == "CRITICAL" else 0.5
            score += type_merge_bonus * near_mult * merge_mult
            reasons.append("NEAR_MERGE")
        elif merge_grade == "FAR":
            score += type_merge_bonus * 0.20 * merge_mult
            reasons.append("FAR_MERGE")

        # ----- 評価軸 2: 高度ペナルティ -----
        height_penalty = landing_y * 50.0 * height_mult

        if phase == "HIGH" and landing_y > 0.5:
            height_penalty *= 1.3
            reasons.append("HIGH_TOWER")
        elif phase == "MEDIUM" and landing_y > 0.5:
            height_penalty *= 1.5
            reasons.append("MEDIUM_TOWER")
        elif landing_y > 0.0:
            reasons.append("HIGH_LAYER")

        score -= height_penalty

        # ----- 評価軸 3: ドリフトペナルティ -----
        drift_penalty = (abs(drift_x) + drift_unc) * 30.0
        score -= drift_penalty

        # ----- 評価軸 4: 左右バランス補正 -----
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

        # ----- 評価軸 5: 盤面平坦度スコア (v148: 動的調整版) -----
        score += current_flatness * flatness_weight
        if flatness_weight > 1.0:
            reasons.append("BOARD_FLAT_CRITICAL")
        elif flatness_weight > 0.5:
            reasons.append("BOARD_FLAT")
        elif flatness_weight > 0.0:
            reasons.append("BOARD_FLAT_MEDIUM")

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
