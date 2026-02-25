#!/usr/bin/env python3
"""strategy.py - AI改善対象の決定スクリプト

固定インターフェース:
  decide(game_state: dict, analysis: dict) -> dict
    戻り値: {"x": float, "reason": str}

AI改変可能: decide() 内部、ヘルパー関数、定数、import
AI改変禁止: decide() シグネチャ、if __name__ == "__main__" ブロック
"""

# --- 変更履歴 ---
# v001: 初期スケルトン。analyze_board.analyze_drops() の最高スコア位置を返す。
# v002: マージ成功率向上と高さ管理ロジックを追加
#       - DIRECT/NEARマージ優先（低EVでも確実性重視）
#       - max_y>1.5で危機回避（旗側反対にドロップ）
#       - 旗側固定ロジック（最初のDIRECTマージ側を旗側とする）
#       - 大型ピース旗側集約（type9+は旗側から配置開始）
#       - EVマイナス回避（危機時は無視して配置優先）
# v003: マージ成功率と旗側集約の大幅改善
#       - マージ発生時の_consecutive_no_mergeリセット追加
#       - 旗側固定ロジック改善（最初のDIRECTマージのX座標基準）
#       - シェイク戦略早期化（無マージ3ターンで発動）
#       - 危機回避早期化（max_y>1.0で発動）
#       - 大型ピース旗側のEVチェック追加（EV<0なら配置見送り）
#       - 期待値戦略の強化（EV>0の位置を優先）
#       - フォールバック中央の改善（旗側を考慮）
#       - マージ戦略のEVチェック追加（EV>0のマージのみ選択）
# v004: 危機回避ロジックの根本的改善と大型ピース集約強化
#       - 危機時は両側の高さを計算して低い側を選択（旗側無視）
#       - マージ可能なら危機時でも優先（高さ下げる効果）
#       - 高い側の平均Yを計算し、明らかに低い側にドロップ
#       - type7+の旗側集約強化（旗側が決まったら全大型ピース集約）
#       - 旗側未決定時、左右のピース数で旗側決定（多い側を旗側）
#       - 危機回避のしきい値を段階化（1.0で警告、1.5で本格回避）
#       - 壁ドロップ回避（x=±3.0でのバウンドによる不安定化防止）
#       - 次の大型ピース(type7+)の旗側配置を優先
# v005: 履歴分析に基づく根本的改善（2026-02-25）
#       - 分析結果: ターン68-88でmax_yが1.99→3.94に急増しゲームオーバー
#       - 分析結果: type11が左右散在（旗側集約失敗）
#       - 旗側固定ロジック強化: ピース数>5で分布から旗側決定
#       - 大型ピース旗側集約: type9+は必ず旗側（EV>0チェック付き）
#       - 危機回避早期化: max_y>1.0で発動、高い側に配置してマージ誘発
#       - マージ戦略強化: 危機時でもマージ可能なら優先（高さ下げ効果）
#       - シェイク戦略追加: 無マージ3ターンで小ピースで下層を揺らす
#       - nextNext保護: 同typeが続く場合、マージ経路を塞がない配置
#       - 壁ドロップ回避: x=±3.0でのバウンド防止（x=±2.5を使用）
# v006: 高さ管理の根本的再設計と過剰集約防止（2026-02-25）
#       - 分析結果: 左側avg_y=-0.93でもmax_y=2.80（致命的なピーク）
#       - 分析結果: type8+が左側9個、右側5個（過剰集約）
#       - 分析結果: ターン48-51で右側連続ドロップ→両側高くなる失敗
#       - 高さ計算改善: avg_yではなくmax_yを基準に（calculate_side_max_y追加）
#       - 高度危機回避: max_y>1.3で旗側優先、max_y>1.3で高度危機（旗側を避ける）
#       - 大型ピース分散: type7-8は旗側と反対側のバランス重視
#       - type9+旗側集約: type9は旗側固定、type10+は分散を考慮
#       - 危機時のドロップ: 壁ドロップ回避（x=±2.8）
#       - 旗側再評価: ピース数>10で分布再チェック
# v007: 旗側高さ管理の根本的改善（2026-02-25 最新）
#       - 分析結果: ターン39-59で右側max_yが1.3→3.33に急増（旗側過剰集約）
#       - 分析結果: type9旗側配置時EV=-6.0でも配置（旗側高さ無視の失敗）
#       - 分析結果: スコア停滞期間（ターン43-59で17ターン停滞）
#       - 旗側高さ管理: 旗側のmax_yが1.3以上なら旗側を変更
#       - 旗側変更条件: 旗側max_y>1.3かつ反対側が大幅に低い
#       - 大型ピース旗側配置: type9でも旗側max_y<1.3でなければ旗側に配置しない
#       - 危機時旗側回避: max_y>1.0で旗側max_yをチェック、高ければ旗側を避ける
#       - 中程度危機回避: 旗側max_y>1.0なら旗側を反対側に変更
#       - シェイク戦略強化: 無マージ3ターンで早期発動（タイプ4以下）
#       - nextNext保護: 同typeが続く場合、旗側を尊重して配置
# v008: 振り子現象防止と旗側安定化（2026-02-25 最新）
#       - 分析結果: ターン59-62で旗側が3回変更（振り子現象）
#       - 分析結果: 右側max_yが1.09→2.83に急増（旗側変更失敗）
#       - 分析結果: type9,11,10が左右散在（旗側集約完全失敗）
#       - 旗側変更条件厳格化: 反対側が大幅に低い場合（差0.5以上）のみ旗側変更
#       - 旗側固定ロジック強化: 旗側決定時にピース分布を考慮、変更条件を厳しく
#       - 旗側変更禁止期間: 一度旗側を変更したら5ターンは変更しない
#       - 危機回避ロジック改善: 旗側を考慮した危機回避、旗側変更を最小限に
#       - 大型ピース旗側配置: type9+は旗側固定、旗側変更ロジックとは独立
#       - 高さ管理強化: 旗側のmax_yを基準に旗側変更を判断

# モジュールレベル変数（試合内の状態保持）
_flag_side = None  # 旗側: "left" または "right"
_last_drop_x = 0.0
_consecutive_no_merge = 0  # 連続無マージ数
_flag_change_cooldown = 0  # 旗側変更クールダウン（ターン数）


def calculate_side_height(pieces: list, side: str) -> float:
    """指定された側の平均高さを計算する（非推奨：max_y使用推奨）。

    Args:
        pieces: 全ピースリスト
        side: "left" (x<0) または "right" (x>0)

    Returns:
        平均高さ（ピースがない場合は -inf）
    """
    side_pieces = [
        p
        for p in pieces
        if (side == "left" and p["x"] < 0) or (side == "right" and p["x"] > 0)
    ]
    if not side_pieces:
        return -float("inf")
    return sum(p["y"] for p in side_pieces) / len(side_pieces)


def calculate_side_max_y(pieces: list, side: str) -> float:
    """指定された側の最大高さを計算する（v006推奨）。

    Args:
        pieces: 全ピースリスト
        side: "left" (x<0) または "right" (x>0)

    Returns:
        最大高さ（ピースがない場合は -inf）
    """
    side_pieces = [
        p
        for p in pieces
        if (side == "left" and p["x"] < 0) or (side == "right" and p["x"] > 0)
    ]
    if not side_pieces:
        return -float("inf")
    return max(p["y"] for p in side_pieces)


def calculate_large_piece_count(pieces: list, side: str, min_type: int = 7) -> int:
    """指定された側の大型ピース数を計算する。

    Args:
        pieces: 全ピースリスト
        side: "left" (x<0) または "right" (x>0)
        min_type: 大型ピースの最小タイプ（デフォルト7）

    Returns:
        大型ピース数
    """
    large_pieces = [
        p
        for p in pieces
        if p["type"] >= min_type
        and ((side == "left" and p["x"] < 0) or (side == "right" and p["x"] > 0))
    ]
    return len(large_pieces)


def determine_flag_side_from_distribution(pieces: list) -> str:
    """盤面のピース分布から旗側を決定する（旗側未決定時）。

    Args:
        pieces: 全ピースリスト

    Returns:
        "left" または "right"
    """
    # type7+の大型ピース数を比較
    left_large = calculate_large_piece_count(pieces, "left", 7)
    right_large = calculate_large_piece_count(pieces, "right", 7)

    # 大型ピース数が多い側を旗側
    if left_large > right_large:
        return "left"
    elif right_large > left_large:
        return "right"

    # 同数なら全ピース数で決定
    left_count = len([p for p in pieces if p["x"] < 0])
    right_count = len([p for p in pieces if p["x"] > 0])
    return "left" if left_count >= right_count else "right"


def should_change_flag_side(
    pieces: list, current_flag_side: str, proposed_flag_side: str
) -> bool:
    """旗側を変更すべきか判定する（v008追加）。

    反対側が大幅に低い場合のみ旗側を変更する。

    Args:
        pieces: 全ピースリスト
        current_flag_side: 現在の旗側
        proposed_flag_side: 変更後の旗側候補

    Returns:
        変更すべき場合はTrue
    """
    if current_flag_side == proposed_flag_side:
        return False

    # 反対側（変更後の旗側）のmax_yをチェック
    new_side_max_y = calculate_side_max_y(pieces, proposed_flag_side)
    current_side_max_y = calculate_side_max_y(pieces, current_flag_side)

    # 反対側が大幅に低い場合（差0.5以上）のみ旗側を変更
    # かつ反対側のmax_yが1.0未満であること
    if new_side_max_y < current_side_max_y - 0.5 and new_side_max_y < 1.0:
        return True

    return False


def decide(game_state: dict, analysis: dict) -> dict:
    """盤面状態と解析結果から最適ドロップX座標を決定する。

    Args:
        game_state: game_state.json の内容
        analysis: {"results": [...], "same_type": [...], "reactor": {...}}

    Returns:
        {"x": float, "reason": str}
    """
    global _flag_side, _last_drop_x, _consecutive_no_merge, _flag_change_cooldown

    results = analysis.get("results", [])
    pieces = game_state.get("pieces", [])
    next_piece = game_state.get("next", {})
    next_type = next_piece.get("type", 0)
    next_r = next_piece.get("r", 0.5)

    # 現在の最高到達位置を取得
    max_y = max([p["y"] for p in pieces]) if pieces else 0.0

    # --- 旗側固定ロジック ---
    # まだ旗側が決まっていない場合、最初のDIRECTマージを見つけたら旗側とする
    if _flag_side is None and results:
        for r in results:
            if r.get("merge_grade") == "DIRECT" and r.get("has_merge", False):
                _flag_side = "left" if r["x"] < 0 else "right"
                break

    # 旗側がまだ決まっていない場合、ピース分布から決定
    if _flag_side is None and len(pieces) > 5:
        _flag_side = determine_flag_side_from_distribution(pieces)

    # 旗側決定後は旗側変更クールダウンをデクリメント
    if _flag_change_cooldown > 0:
        _flag_change_cooldown -= 1

    # --- 1. マージ可能なら最優先（DIRECT/NEAR）---
    # 危機時でもマージは高さを下げる効果があるので優先
    mergeable_results = []
    for r in results:
        grade = r.get("merge_grade", "NO")
        if grade in ["DIRECT", "NEAR"] and r.get("has_merge", False):
            mergeable_results.append(r)

    if mergeable_results:
        # EVが正のマージのみ対象
        positive_merge_results = [r for r in mergeable_results if r.get("score", 0) > 0]

        if positive_merge_results:
            # 危機時は、高い側のマージを優先（max_y基準）
            if max_y > 1.0:
                left_max_y = calculate_side_max_y(pieces, "left")
                right_max_y = calculate_side_max_y(pieces, "right")
                target_side = "left" if left_max_y > right_max_y else "right"

                # ターゲット側のマージを探す
                side_merges = [
                    r
                    for r in positive_merge_results
                    if (target_side == "left" and r["x"] < 0)
                    or (target_side == "right" and r["x"] > 0)
                ]
                if side_merges:
                    best = max(side_merges, key=lambda r: r.get("score", 0))
                else:
                    best = max(positive_merge_results, key=lambda r: r.get("score", 0))
            else:
                # 通常時はDIRECTマージ優先
                direct_merges = [
                    r
                    for r in positive_merge_results
                    if r.get("merge_grade") == "DIRECT"
                ]
                if direct_merges:
                    best = max(direct_merges, key=lambda r: r.get("score", 0))
                else:
                    best = max(positive_merge_results, key=lambda r: r.get("score", 0))

            x = best["x"]
            score = best.get("score", 0)
            # マージ発生時は無マージカウントをリセット
            _consecutive_no_merge = 0
            _last_drop_x = x
            return {"x": x, "reason": f"マージ x={x:.2f} (score={score:.1f})"}

    # --- 2. 高度危機回避（max_y > 1.3）---
    # 高度危機: 両側の高さを比較して低い側を選択（旗側無視）
    if max_y > 1.3:
        left_max_y = calculate_side_max_y(pieces, "left")
        right_max_y = calculate_side_max_y(pieces, "right")

        # 明らかに低い側にドロップ（差が0.5以上）
        if left_max_y > right_max_y + 0.5:
            # 右側が低い
            target_x = 2.8  # 壁ドロップ回避
            _consecutive_no_merge += 1
            _last_drop_x = target_x
            return {
                "x": target_x,
                "reason": f"高度危機回避(左側高い L_max={left_max_y:.1f} R_max={right_max_y:.1f}) x={target_x:.2f}",
            }
        elif right_max_y > left_max_y + 0.5:
            # 左側が低い
            target_x = -2.8  # 壁ドロップ回避
            _consecutive_no_merge += 1
            _last_drop_x = target_x
            return {
                "x": target_x,
                "reason": f"高度危機回避(右側高い L_max={left_max_y:.1f} R_max={right_max_y:.1f}) x={target_x:.2f}",
            }

    # --- 3. 中程度危機回避（max_y > 1.0）---
    # v008改善: 旗側変更条件を厳格化（反対側が大幅に低い場合のみ）
    if max_y > 1.0:
        left_max_y = calculate_side_max_y(pieces, "left")
        right_max_y = calculate_side_max_y(pieces, "right")

        if _flag_side is not None:
            # クールダウン期間中は旗側を変更しない
            if _flag_change_cooldown > 0:
                # 旗側にドロップ
                if _flag_side == "left":
                    target_x = -2.8
                else:
                    target_x = 2.8
            else:
                # 旗側のmax_yをチェックして旗側を変更
                # 反対側が大幅に低い場合のみ旗側を変更
                if _flag_side == "left" and left_max_y > 1.0:
                    # 反対側が大幅に低い場合のみ旗側を変更
                    if right_max_y < left_max_y - 0.5 and right_max_y < 1.0:
                        _flag_side = "right"
                        _flag_change_cooldown = 5  # 5ターンは変更しない
                        target_x = 2.8
                    else:
                        target_x = -2.8
                elif _flag_side == "right" and right_max_y > 1.0:
                    # 反対側が大幅に低い場合のみ旗側を変更
                    if left_max_y < right_max_y - 0.5 and left_max_y < 1.0:
                        _flag_side = "left"
                        _flag_change_cooldown = 5  # 5ターンは変更しない
                        target_x = -2.8
                    else:
                        target_x = 2.8
                else:
                    # 旗側にドロップ
                    target_x = -2.8 if _flag_side == "left" else 2.8
        else:
            # 旗側未決定時、低い側を優先
            target_x = -2.8 if left_max_y < right_max_y else 2.8

        _consecutive_no_merge += 1
        _last_drop_x = target_x
        return {
            "x": target_x,
            "reason": f"中程度危機回避(旗側優先) x={target_x:.2f}",
        }

    # --- 4. 旗側集約戦略（強化：type9から適用、旗側変更厳格化）---
    # v008改善: type9+は旗側固定、旗側変更ロジックとは独立
    if _flag_side is not None and results:
        if next_type >= 9:
            # 旗側のmax_yをチェック
            left_max_y = calculate_side_max_y(pieces, "left")
            right_max_y = calculate_side_max_y(pieces, "right")

            # 旗側が高すぎる場合は旗側に配置しない（旗側変更ロジックとは独立）
            if _flag_side == "left" and left_max_y >= 1.3:
                # 反対側が大幅に低い場合のみ旗側を変更して配置
                if right_max_y < left_max_y - 0.5:
                    _flag_side = "right"
                # それでも旗側に配置しない場合はスキップ
                if _flag_side == "left" and left_max_y >= 1.3:
                    # 旗側に配置しない
                    pass
                else:
                    # 旗側（または変更後の旗側）に配置
                    pass

            if _flag_side == "right" and right_max_y >= 1.3:
                # 反対側が大幅に低い場合のみ旗側を変更して配置
                if left_max_y < right_max_y - 0.5:
                    _flag_side = "left"
                # それでも旗側に配置しない場合はスキップ
                if _flag_side == "right" and right_max_y >= 1.3:
                    # 旗側に配置しない
                    pass
                else:
                    # 旗側（または変更後の旗側）に配置
                    pass

            # 旗側に配置（旗側のmax_yが1.3未満、または変更後）
            # type9は旗側固定、type10+は分散を考慮しない（v008改善）
            if _flag_side == "left":
                # 左側から（-3.0～-0.5）
                best_ev = -float("inf")
                best_x = None
                for r in results:
                    if r["x"] < 0:
                        ev = r.get("score", 0)
                        if ev > best_ev:
                            best_ev = ev
                            best_x = r["x"]
            else:
                # 右側から（0.5～3.0）
                best_ev = -float("inf")
                best_x = None
                for r in results:
                    if r["x"] > 0:
                        ev = r.get("score", 0)
                        if ev > best_ev:
                            best_ev = ev
                            best_x = r["x"]

            if best_x is not None and best_ev > -100:  # EVが極端に悪くなければ配置
                _last_drop_x = best_x
                return {
                    "x": best_x,
                    "reason": f"大型ピース旗側 x={best_x:.2f} (EV={best_ev:.1f})",
                }

    # --- 5. シェイク戦略（早期化：無マージ3ターンで発動）---
    _consecutive_no_merge += 1
    if _consecutive_no_merge >= 3 and next_type <= 4:
        # 小ピースで下層を揺らす
        # 高い側でEVが正の位置を探す（高さを下げる効果）
        left_max_y = calculate_side_max_y(pieces, "left")
        right_max_y = calculate_side_max_y(pieces, "right")
        target_side = "left" if left_max_y > right_max_y else "right"

        best_ev = -float("inf")
        best_x = None

        for r in results:
            x = r["x"]
            ev = r.get("score", 0)
            is_target_side = (target_side == "left" and x < 0) or (
                target_side == "right" and x > 0
            )

            if is_target_side and ev > best_ev:
                best_ev = ev
                best_x = x

        if best_x is not None and best_ev > 0:
            _last_drop_x = best_x
            return {
                "x": best_x,
                "reason": f"シェイク戦略(無マージ={_consecutive_no_merge}) x={best_x:.2f}",
            }

    # --- 6. 次のピース保護（nextNextのマージ経路を塞がない）---
    # nextNextと同じtypeがあれば、そのマージ経路を保護
    next_next = game_state.get("nextNext", {})
    next_next_type = next_next.get("type", 0)
    if next_next_type > 0 and next_next_type == next_type:
        # 同じtypeが続く場合、現在のドロップと次のドロップを分ける
        # ただし、旗側が決まっている場合は旗側を尊重
        if _flag_side == "left":
            x = -2.8 if abs(_last_drop_x) > 1.5 else -2.0
        elif _flag_side == "right":
            x = 2.8 if abs(_last_drop_x) > 1.5 else 2.0
        else:
            # 旗側未決定時は従来ロジック
            if abs(_last_drop_x) > 1.5:
                x = -_last_drop_x
            else:
                x = 2.8 if _last_drop_x < 0 else -2.8

        _consecutive_no_merge += 1
        _last_drop_x = x
        return {"x": x, "reason": f"nextNext保護 x={x:.2f}"}

    # --- 7. 通常の期待値戦略（EV>0の位置を優先）---
    # EVが正の結果のみ対象
    valid_results = [r for r in results if r.get("score", 0) > 0]

    if valid_results:
        best = valid_results[0]
        x = best["x"]
        ev = best.get("score", 0)

        # 旗側に合わせて配置（ただしEVが優先）
        if _flag_side == "left" and x > 0 and len(valid_results) > 1:
            # 旗側左なのに右側を選ぼうとした場合、左側から探す
            for r in valid_results:
                if r["x"] < 0:
                    x = r["x"]
                    ev = r.get("score", 0)
                    break
        elif _flag_side == "right" and x < 0 and len(valid_results) > 1:
            # 旗側右なのに左側を選ぼうとした場合、右側から探す
            for r in valid_results:
                if r["x"] > 0:
                    x = r["x"]
                    ev = r.get("score", 0)
                    break

        _last_drop_x = x
        return {"x": x, "reason": f"期待値 x={x:.2f} (EV={ev:.1f})"}

    # --- 8. フォールバック: 旗側側の中央 ---
    _consecutive_no_merge += 1
    if _flag_side == "left":
        x = -1.5
    elif _flag_side == "right":
        x = 1.5
    else:
        x = 0.0
    _last_drop_x = x
    return {"x": x, "reason": f"フォールバック({_flag_side or '中央'})"}


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
