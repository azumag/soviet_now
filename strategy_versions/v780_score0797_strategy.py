#!/usr/bin/env python3
"""strategy.py - ソ連パズルゲームの AI ドロップ位置決定スクリプト

ゲーム概要:
  - ピースをドロップし、同type同士が接触するとマージ (N+N → N+1)
  - スコアテーブル: type1=1, type2=3, type3=6, ..., typeN = N*(N+1)/2
  - ボード: x ∈ [-3.0, +3.0], 床 y=-4.48, デッドライン y=3.32
  - プレイヤーが制御できるのはドロップX座標のみ

決定ロジック (8つの評価軸):
  1. マージボーナス   — 即座にマージできる位置に高得点 (DIRECT > NEAR > FAR)
  2. 高度ペナルティ   — 着地位置が高いほど減点 (フェーズで重み変動)
  3. Reactor保護      — 連鎖進行中は高い位置を回避
  4. ドリフトペナルティ — ポリゴン形状による着地後のズレを減点
  5. 左右バランス補正 — ピース数の偏りを是正する方向にボーナス
  6. nextNext中央寄せ — 次の次と同typeなら中央に寄せてマージ準備
  7. 同type集約       — 同typeピースが近くに多い位置にボーナス (将来マージ準備)
  8. 盤面整理度スコア — 盤面全体の同type集約度を評価 (v141: 新規追加)

フェーズ (盤面の最高Y座標で判定):
  LOW      (max_y < 0.8) : 序盤。マージ優先 (merge_mult=1.2)
  MEDIUM   (0.8 ≤ max_y < 1.8) : 中盤。高度管理を強化 (height_mult=2.4)
  HIGH     (1.8 ≤ max_y < 3.0) : 終盤。マージ機会確保 (height_mult=1.8)
  CRITICAL (3.0 ≤ max_y) : 危険。DIRECTマージ最優先で盤面圧縮 (NEAR は慎重に)
"""

# 固定インターフェース:
# decide(game_state: dict, analysis: dict) -> dict
#    戻り値: {"x": float, "reason": str}
#
# AI改変可能: decide() 内部,ヘルパー関数,定数,import
# AI改変禁止: decide() シグネチャ,if __name__ == "__main__" ブロック

# --- 変更履歴 ---
# [BEST:3689] v126: v42ベース・HIGHフェーズマージ強化版
# v139: 同type集約ボーナス追加版 - mergesの個別距離データで将来マージ確率を評価
# v140: CRITICALフェーズマージ戦略反転 — merge_mult=0.6(マージ抑制)を廃止。
# マージは2個→1個でネット-1ピース=盤面圧縮の最良手段。CRITICALこそマージ優先すべき。
# ただしNEAR(成功率68.5%)は失敗→即死リスクがあるため、DIRECTのみ1.5倍ボーナス、
# NEARは通常の0.5倍に据え置き。merge_gradeごとに分岐するCRITICAL専用ロジック。
# v141: 盤面整理度スコア追加 - 盤面全体の同type集約度を評価。各typeの最短ペア距離を計算し、
# 近くに集まっているtypeほどボーナス。同typeが分散している盤面をペナルティして、将来のマージ機会を損なう配置を抑制。

# マージ結果のスコア: type N のマージで N*(N+1)/2 点獲得
# 例: type1+1→2 で +3点, type8+8→9 で +45点, type14+14→15 で +120点
SCORE_TABLE = {i: i * (i + 1) // 2 for i in range(1, 17)}


def calculate_board_clustering(pieces):
    """v141: 盤面全体の同type集約度を計算

    各typeの最短ペア距離を計算し、距離が小さいほどボーナスを与える。
    同typeが近くに集まっている盤面は、将来のマージ確率が高い。

    Args:
        pieces: 全ピースのリスト [{id, type, x, y, ...}, ...]

    Returns:
        clustering_score: 集約度スコア（高いほど同typeが集まっている）
    """
    if not pieces:
        return 0.0

    # typeごとのピースリストを作成
    type_pieces = {}
    for p in pieces:
        ptype = p["type"]
        if ptype not in type_pieces:
            type_pieces[ptype] = []
        type_pieces[ptype].append(p)

    clustering_score = 0.0

    # 各typeについて最短ペア距離を計算
    for ptype, piece_list in type_pieces.items():
        # ピースが1つ以下なら集約度は計算不可
        if len(piece_list) < 2:
            continue

        # 全ペアの距離を計算し、最短距離を見つける
        min_dist = float("inf")
        for i in range(len(piece_list)):
            for j in range(i + 1, len(piece_list)):
                p1 = piece_list[i]
                p2 = piece_list[j]
                dist = ((p1["x"] - p2["x"]) ** 2 + (p1["y"] - p2["y"]) ** 2) ** 0.5
                if dist < min_dist:
                    min_dist = dist

        # 最短距離に基づいてスコアを計算
        # 距離が小さいほど高いボーナス（同typeが集まっている）
        # 距離が大きいほどペナルティ（同typeが分散している）
        # ピース数で重み付け（大typeの集約を優先）
        piece_count = len(piece_list)
        if min_dist < 1.0:
            # 非常に近い：強いボーナス
            clustering_score += (1.0 - min_dist) * 100.0 * piece_count
        elif min_dist < 3.0:
            # 近い：普通のボーナス
            clustering_score += (1.0 - min_dist / 3.0) * 50.0 * piece_count
        else:
            # 離れている：ペナルティ
            clustering_score -= (min_dist - 3.0) * 20.0 * piece_count

    return clustering_score


def decide(game_state: dict, analysis: dict) -> dict:
    """v141: CRITICALフェーズDIRECTマージ最優先 + 同type集約ボーナス + 盤面整理度スコア

    Args:
        game_state: ゲーム状態 (pieces, next, nextNext, score 等)
        analysis: analyze_board.py の解析結果
            - results: 各ドロップX候補ごとの着地情報
                - x: ドロップX座標
                - landing_y: 推定着地Y座標 (高い=危険)
                - drift_x/drift_unc: ポリゴン形状による着地後ドリフト
                - merge_grade: 最良マージ判定 (DIRECT/NEAR/FAR/NO)
                - merges: 各同typeピースへの個別距離・マージ判定
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
    # 盤面の最高Y座標で4段階に分類。フェーズごとに高度ペナルティとマージボーナスの
    # 重みを変える。LOW: マージ狙い / MEDIUM-HIGH: 高度管理 / CRITICAL: 生存優先
    if max_y < 0.8:
        phase = "LOW"
        height_mult = 1.0     # 低い盤面では高度ペナルティ弱め
        merge_mult = 1.2      # マージボーナス20%増で積極的に狙う
    elif max_y < 1.8:
        phase = "MEDIUM"
        height_mult = 2.4     # 高度ペナルティを強化して盤面上昇を抑制
        merge_mult = 1.0
    elif max_y < 3.0:
        phase = "HIGH"
        height_mult = 1.8     # HIGHでは少し緩和してマージ機会を確保
        merge_mult = 1.0
    else:
        phase = "CRITICAL"
        height_mult = 1.0     # CRITICALでは高度ペナルティ基本値のみ
        merge_mult = 1.0      # v140: マージ抑制を廃止 (DIRECT/NEAR個別制御に移行)

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

    # --- Type別マージボーナス計算 ---
    # マージ結果のtype (next_type+1) が高いほどスコア価値が高い
    # 例: type1マージ → bonus=330, type5マージ → bonus=510, type14マージ → bonus=1660
    merge_result_type = min(next_type + 1, 16)
    type_merge_bonus = SCORE_TABLE.get(merge_result_type, 10) * 10 + 300

    # --- v141: 盤面整理度スコア計算 ---
    # 盤面全体の同type集約度を計算（全ドロップ候補で共通）
    current_clustering = calculate_board_clustering(pieces)

    # =======================================================================
    #  各ドロップ候補 (x座標) を8つの評価軸でスコアリング
    # =======================================================================
    for result in results:
        x = result["x"]
        landing_y = result.get("landing_y", 0)
        drift_x = result.get("drift_x", 0)
        drift_unc = result.get("drift_unc", 0)
        merge_grade = result.get("merge_grade", "NO")  # DIRECT/NEAR/FAR/NO

        score = 0.0
        reasons = []

        # ----- 評価軸 1: マージボーナス -----
        # analyze_board が判定した merge_grade に応じてボーナス
        # DIRECT: ターゲットに直撃 (成功率95.7%)
        # NEAR:   着地後に接触圏内 (成功率68.5%)
        # FAR:    ドリフトで接触する可能性あり (低確率)
        #
        # v140: CRITICALフェーズではマージ=盤面圧縮(2個→1個)なので最優先。
        # ただしNEARは失敗→即死リスクがあるため、DIRECTのみ1.5倍強化。
        if merge_grade == "DIRECT":
            direct_mult = 1.5 if phase == "CRITICAL" else 1.0  # v140: CRITICAL時DIRECT強化
            score += type_merge_bonus * merge_mult * direct_mult
            reasons.append("DIRECT_MERGE")
        elif merge_grade == "NEAR":
            near_mult = 0.3 if phase == "CRITICAL" else 0.5  # v140: CRITICAL時NEARは慎重に
            score += type_merge_bonus * near_mult * merge_mult
            reasons.append("NEAR_MERGE")
        elif merge_grade == "FAR":
            score += type_merge_bonus * 0.17 * merge_mult
            reasons.append("FAR_MERGE")

        # ----- 評価軸 2: 高度ペナルティ -----
        # 着地Y座標が高いほど大きなペナルティ。フェーズのheight_multで重み調整。
        # さらにHIGH/MEDIUMで着地が高い(>0.5)場合は追加倍率
        height_penalty = landing_y * 50.0 * height_mult

        if phase == "HIGH" and landing_y > 0.5:
            height_penalty *= 1.3   # HIGH_TOWER: デッドライン接近でさらに厳しく
            reasons.append("HIGH_TOWER")
        elif phase == "MEDIUM" and landing_y > 0.5:
            height_penalty *= 1.5   # MEDIUM_TOWER: MEDIUMでの高い着地を強く抑制
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
            balance_strength = 40.0   # 終盤は左右バランスが崩れると即ゲームオーバー
        elif phase == "MEDIUM":
            balance_strength = 30.0

        left_count = sum(1 for p in pieces if p["x"] < 0)
        right_count = len(pieces) - left_count
        balance_bias = (right_count - left_count) / (len(pieces) if pieces else 1)

        balance_penalty = x * balance_bias * balance_strength
        score -= abs(balance_penalty)

        # ----- 評価軸 6: nextNext中央寄せ -----
        # nextNextが今のnextと同typeなら、次もマージチャンスがある。
        # 中央付近に置いておけば次ターンでどちらの方向にもマージしやすい
        if next_next_type == next_type:
            center_bonus = max(0, 1.0 - abs(x) / 2.0) * 50.0
            score += center_bonus
            reasons.append("NEXT_SAME")

        # ----- 評価軸 7: 同type集約ボーナス (v139) -----
        # analyze_board の merges リストには、この x に落とした場合の
        # 各同typeピースまでの距離(dist)と接触距離(contact_r)が入っている。
        # 今すぐマージできなくても、同typeピースの近くに落とせば
        # 将来の物理移動・爆発衝撃波でマージする確率が上がる。
        # → 接触距離の3倍以内にいる同typeピースごとにボーナス加算
        cluster_bonus = 0.0
        for m in result.get("merges", []):
            dist = m.get("dist", 99)
            contact_r = m.get("contact_r", 1.0)
            proximity_limit = contact_r * 3.0  # この範囲内なら「近い」とみなす
            if dist < proximity_limit:
                # 線形減衰: 距離0で最大80pt、proximity_limitで0pt
                cluster_bonus += (1.0 - dist / proximity_limit) * 80.0
        if cluster_bonus > 0:
            score += cluster_bonus
            reasons.append("CLUSTER")

        # ----- 評価軸 8: 盤面整理度スコア (v141: 新規追加) -----
        # 盤面全体の同type集約度を評価。同typeが近くに集まっているほどボーナス。
        # 盤面全体の整理度スコア（全候補で共通）をそのまま加算。
        # 集約度が高い（同typeが近くに集まっている）場合は、盤面が整理されており、
        # 将来のマージ機会が多いのでボーナス。
        # 集約度が低い（同typeが分散している）場合は、盤面が整理されておらず、
        # 将来のマージ機会が少ないのでペナルティ。
        score += current_clustering
        if current_clustering > 100.0:
            reasons.append("BOARD_CLUSTERED")
        elif current_clustering < -50.0:
            reasons.append("BOARD_SCATTERED")

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
