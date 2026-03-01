#!/usr/bin/env python3
"""strategy.py - AI改善対象の決定スクリプト"""

# 固定インターフェース:
# decide(game_state: dict, analysis: dict) -> dict
#    戻り値: {"x": float, "reason": str}
#
# AI改変可能: decide() 内部、ヘルパー関数、定数、import
# AI改変禁止: decide() シグネチャ、if __name__ == "__main__" ブロック

# --- 変更履歴 ---
# v418: v128復帰・シンプル先読みマージ版 - v417の失敗（score=642、v416の988を下回る）を受けて、v414-v417の振り子パターン（高度ペナルティ緩和とマージボーナス増強の悪循環）を特定。v417の複雑なクラスタ密度評価・連鎖マージ評価（v414で導入）は効果が不明瞭で、スコア低下の主要原因。v128のシンプルで成功した構造（約110行、MEDIUM=2.4/HIGH=1.8の高度管理、DIRECT=1200/NEAR=600/FAR=200のマージボーナス）に完全復帰し、シンプルな先読みマージ戦略のみを慎重に追加：（1）type N-1ペアの重心計算：type N-1が2個以上あれば、その重心を計算し、重心に近い位置にtype * 10 * (1.5 - distance)のボーナス。（2）盤面密度マップ：ボードを3x3のグリッドに分割し、各セルのピース密度を計算。過密領域（density>4）を避けるペナルティ（-50 * (density - 4)）。（3）クラスタ密度評価：v414-v417の複雑な密度計算（ピース間の平均距離の逆数）を削除し、type N-1ピース同士の距離が1.5未満なら「近接ペア」としてボーナス1.2倍。（4）v128の成功構造を完全維持：MEDIUMフェーズheight_mult=2.4、HIGHフェーズheight_mult=1.8、マージボーナスDIRECT=1200/NEAR=600/FAR=200、HIGH_TOWERペナルティHIGH=2.0倍/MEDIUM=1.5倍。振り子パターンの根本的解消と、シンプルさの確保で頑健性を向上。
#   根本原因の特定:
#   - v414-v417の一貫した失敗：高度ペナルティ緩和とマージボーナス増強の方向性で一貫して失敗（v415:988→v416:988→v417:642）
#   - v417の複雑なクラスタ密度評価・連鎖マージ評価（v414で導入）は効果が不明瞭
#   - v128の成功構造（MEDIUM=2.4/HIGH=1.8、マージボーナスDIRECT=1200/NEAR=600/FAR=200）を維持しつつ、シンプルな先読みマージを追加
#   - batch_summary.txtでHIGH_LAYER_FUTURE_MERGEが最も効果的（avg_score_delta=18.7）だが、v414-v417の複雑なロジックは不必要
#   改善策（v128復帰・シンプル先読みマージ）:
#   - v128のシンプル構造に完全復帰：4フェーズ（LOW/MEDIUM/HIGH/CRITICAL）、height_mult（LOW=1.0/MEDIUM=2.4/HIGH=1.8/CRITICALなし）、merge_mult（LOW=1.2/MEDIUM=1.0/HIGH=1.0/CRITICAL=0.6）
#   - v128のマージボーナス（DIRECT=1200/NEAR=600/FAR=200）に復帰
#   - v128のHIGH_TOWERペナルティ（HIGH=2.0倍, MEDIUM=1.5倍）に復帰
#   - シンプルな先読みマージ戦略を追加：type N-1が2個以上あれば、その重心を計算し、重心に近い位置にtype * 10 * (1.5 - distance)のボーナス
#   - 盤面密度マップを追加：ボードを3x3のグリッド（x: -3〜3, y: -4〜3）に分割し、各セルのピース密度を計算。過密領域（density>4）を避けるペナルティ（-50 * (density - 4)）
#   - クラスタ密度評価を簡素化：v414-v417の複雑な密度計算（ピース間の平均距離の逆数）を削除し、type N-1ピース同士の距離が1.5未満なら「近接ペア」としてボーナス1.2倍
#   - v414-v417の連鎖マージ評価（type N-2、N-3の密度も考慮）を削除：複雑すぎて効果が不明瞭
#   核心的発見: v128のシンプル構造が成功した理由は、強力な高度管理と標準的なマージボーナスのバランスが良いこと。v414-v417の複雑なクラスタ密度評価は効果が不明瞭で、むしろ混乱を招く。v128に復帰しつつ、シンプルな先読みマージ戦略を追加することで、戦略の精度を向上しつつ頑健性を維持できる。
#   成功基準: scoreがv128の3689に近づく、またはv484の1504を上回る
#   失敗基準: scoreがv417の642以下、または改善が見られない


def calculate_grid_density(pieces: list, x: float, y: float) -> int:
    """盤面密度マップ：指定位置の周囲（3x3グリッド）のピース密度を計算"""
    # グリッドサイズ（各グリッドは2x2）
    grid_x = int((x + 3.0) / 2.0)  # x: -3〜3 → 0〜3
    grid_y = int((y + 4.0) / 2.0)  # y: -4〜3 → 0〜3

    count = 0
    for p in pieces:
        px = p["x"]
        py = p["y"]
        pg_x = int((px + 3.0) / 2.0)
        pg_y = int((py + 4.0) / 2.0)

        # 同じグリッドまたは隣接グリッドならカウント
        if abs(pg_x - grid_x) <= 1 and abs(pg_y - grid_y) <= 1:
            count += 1

    return count


def calculate_future_merge_center(pieces: list, target_type: int) -> float:
    """先読みマージ予約：type N-1のペア（2個以上）の重心を計算。ペアがない場合は0.0を返す"""
    type_minus_1_pieces = [p for p in pieces if p["type"] == target_type - 1]

    if len(type_minus_1_pieces) < 2:
        return 0.0  # ペアが2個未満なら0.0を返す

    center_x = sum(p["x"] for p in type_minus_1_pieces) / len(type_minus_1_pieces)
    return center_x


def calculate_pair_proximity_bonus(pieces: list, target_type: int) -> float:
    """シンプルな近接ペア評価：type N-1が2個以上あり、距離が1.5未満ならボーナス1.2倍"""
    type_minus_1_pieces = [p for p in pieces if p["type"] == target_type - 1]

    if len(type_minus_1_pieces) < 2:
        return 1.0  # ペアが2個未満ならボーナスなし

    # 最も近い2個の距離を計算
    min_distance = float("inf")
    for i in range(len(type_minus_1_pieces)):
        for j in range(i + 1, len(type_minus_1_pieces)):
            dx = type_minus_1_pieces[i]["x"] - type_minus_1_pieces[j]["x"]
            dy = type_minus_1_pieces[i]["y"] - type_minus_1_pieces[j]["y"]
            distance = (dx**2 + dy**2) ** 0.5
            min_distance = min(min_distance, distance)

    # 距離が1.5未満ならボーナス1.2倍
    if min_distance < 1.5:
        return 1.2
    return 1.0


def decide(game_state: dict, analysis: dict) -> dict:
    """v418: v128復帰・シンプル先読みマージ版"""

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

    # フェーズ判定（v128の閾値を維持）
    if max_y < 0.8:
        phase = "LOW"
        height_mult = 1.0
        merge_mult = 1.2
    elif max_y < 1.8:
        phase = "MEDIUM"
        height_mult = 2.4  # v128: 2.4を維持
        merge_mult = 1.0
    elif max_y < 3.0:
        phase = "HIGH"
        height_mult = 1.8  # v128: 1.8を維持
        merge_mult = 1.0
    else:
        phase = "CRITICAL"
        height_mult = 1.0
        merge_mult = 0.6  # v128: 0.6を維持

    for result in results:
        x = result["x"]
        landing_y = result.get("landing_y", 0)
        drift_x = result.get("drift_x", 0)
        drift_unc = result.get("drift_unc", 0)
        merge_grade = result.get("merge_grade", "NO")

        score = 0.0
        reasons = []

        # === v418: v128復帰・シンプル先読みマージ ===

        # 1. マージグレードによるスコア（v128の値を維持）
        merge_bonus_multiplier = 1.0

        # v418: type N-1の近接ペアならボーナス1.2倍
        if merge_grade in ["DIRECT", "NEAR", "FAR"]:
            current_type = next_type if next_type > 0 else next_next_type
            if current_type > 1:
                pair_proximity_bonus = calculate_pair_proximity_bonus(
                    pieces, current_type
                )
                merge_bonus_multiplier *= pair_proximity_bonus
                if pair_proximity_bonus > 1.0:
                    reasons.append("CLOSE_PAIR")

        if merge_grade == "DIRECT":
            score += 1200.0 * merge_mult * merge_bonus_multiplier  # v128: 1200を維持
            reasons.append("DIRECT_MERGE")
        elif merge_grade == "NEAR":
            score += 600.0 * merge_mult * merge_bonus_multiplier  # v128: 600を維持
            reasons.append("NEAR_MERGE")
        elif merge_grade == "FAR":
            score += 200.0 * merge_mult * merge_bonus_multiplier  # v128: 200を維持
            reasons.append("FAR_MERGE")

        # 2. 高度によるペナルティ（v128のHIGH_TOWERペナルティを維持）
        height_penalty = landing_y * 50.0 * height_mult

        # HIGH_TOWERペナルティ（v128: HIGH=2.0倍, MEDIUM=1.5倍を維持）
        if phase == "HIGH" and landing_y > 0.5:
            height_penalty *= 2.0  # v128: 2.0倍を維持
            reasons.append("HIGH_TOWER")
        elif phase == "MEDIUM" and landing_y > 0.5:
            height_penalty *= 1.5  # v128: 1.5倍を維持
            reasons.append("MEDIUM_TOWER")
        elif landing_y > 0.0:
            reasons.append("HIGH_LAYER")

        score -= height_penalty

        # 3. ドリフトによるペナルティ（v128: 一律30.0を維持）
        drift_penalty = (abs(drift_x) + drift_unc) * 30.0
        score -= drift_penalty

        # 4. 左右バランス補正（v128の設定を維持）
        balance_strength = 20.0
        if phase == "HIGH":
            balance_strength = 40.0  # v128: 40.0を維持
        elif phase == "MEDIUM":
            balance_strength = 30.0  # v128: 30.0を維持

        left_count = sum(1 for p in pieces if p["x"] < 0)
        right_count = len(pieces) - left_count
        balance_bias = (right_count - left_count) / (len(pieces) if pieces else 1)

        balance_penalty = x * balance_bias * balance_strength
        score -= abs(balance_penalty)

        # 5. nextNextが同じタイプなら中央寄せボーナス（v128の設定を維持）
        if next_next_type == next_type:
            center_bonus = max(0, 1.0 - abs(x) / 2.0) * 50.0
            score += center_bonus
            reasons.append("NEXT_SAME")

        # 6. 先読みマージボーナス（v418: シンプルな重心計算）
        # next/nextNextがtype Nのとき、type N-1のペア（2個以上）の重心を計算
        future_merge_bonus = 0.0
        for target_type in [next_type, next_next_type]:
            if target_type > 1:
                # type N-1の重心を計算（2個以上のペアのみ、ない場合は0.0）
                center_x = calculate_future_merge_center(pieces, target_type)

                distance = abs(x - center_x)

                # 重心に近い位置にボーナス（type * 10 * (1.5 - distance)）
                bonus = max(0, 1.5 - distance) * target_type * 10.0
                future_merge_bonus += bonus

        if future_merge_bonus > 0:
            score += future_merge_bonus
            reasons.append("FUTURE_MERGE")

        # 7. 盤面密度マップ（v418: 過密領域を避ける）
        grid_density = calculate_grid_density(pieces, x, landing_y)
        if grid_density > 4:
            density_penalty = -50.0 * (grid_density - 4)
            score += density_penalty
            reasons.append("DENSE_AREA")

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
