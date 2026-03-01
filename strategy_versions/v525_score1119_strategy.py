#!/usr/bin/env python3
"""strategy.py - AI改善対象の決定スクリプト"""

# 固定インターフェース:
# decide(game_state: dict, analysis: dict) -> dict
#    戻り値: {"x": float, "reason": str}
#
# AI改変可能: decide() 内部、ヘルパー関数、定数、import
# AI改変禁止: decide() シグネチャ、if __name__ == "__main__" ブロック

# --- 変更履歴 ---
# v417: マージボーナス大幅増強・高度管理極端緩和版 - v416の失敗（score=988、v412の1345を下回る）を受けて、batch_summary.txtの分析でHIGH_LAYER_FUTURE_MERGEが最も効果的（avg_score_delta=18.7）であることが再確認されたが、v416のマージボーナスと先読みマージボーナスは高度ペナルティに対して依然として小さいことを特定。v416では先読みマージボーナスがtype * 12で最大180点、高度ペナルティは高度2.0で150点だが、ドリフトやバランスのペナルティも加わると、マージボーナスが打ち消されてしまう。さらに、クラスタ密度係数の軽減（0個: 0.4倍、1個: 0.6倍）が過剰で、密度がない場合のボーナスが小さすぎる。マージボーナスと先読みマージボーナスを大幅に増強し、高度ペナルティを極端に緩和し、クラスタ密度係数の軽減を緩和することで、マージを最優先する。
#   根本原因の特定:
#   - v416のマージボーナスはDIRECT 1200点、NEAR 600点、FAR 400点。高度ペナルティ（高度2.0で150点）+ ドリフトペナルティ（約30点）+ バランスペナルティ（約30点）で210点になるため、マージボーナスが相対的に小さい
#   - v416の先読みマージボーナスはtype * 12で最大180点だが、クラスタ密度係数や連鎖係数が小さいと、実際のボーナスはさらに小さくなる
#   - v416のクラスタ密度係数の軽減（0個: 0.4倍、1個: 0.6倍）が過剰で、密度がない場合のボーナスが小さすぎる
#   - v416の連鎖マージ係数（N-2: 0.6、N-3: 0.3）が小さく、連鎖マージの評価が不十分
#   - v416のMEDIUMフェーズのheight_mult=1.5が依然として高く、マージより高度管理が優先されている
#   - batch_summary.txtでHIGH_LAYER_FUTURE_MERGEが最も効果的だが、v416ではボーナスが小さくて効果が発揮されていない
#   改善策（マージボーナス大幅増強・高度管理極端緩和）:
#   - マージボーナスを大幅に増強: DIRECT 1200→1350点、NEAR 600→700点、FAR 400→500点（マージを最優先）
#   - 先読みマージボーナスの重みをtype * 12からtype * 15に増強（type15で最大225点）
#   - MEDIUMフェーズのheight_multを1.5から1.3に緩和（高度2.0で130点のペナルティ、v416の150点からさらに緩和）
#   - クラスタ密度係数の軽減を緩和: 0個: 0.4→0.5倍、1個: 0.6→0.8倍（密度がない場合のボーナスを増強）
#   - 連鎖マージ係数を増強: N-2: 0.6→0.8倍、N-3: 0.3→0.5倍（連鎖マージの評価を強化）
#   - v416の既存ロジック（HIGH_TOWERペナルティ0.8倍など）を維持
#   核心的発見: マージボーナスと先読みマージボーナスが高度ペナルティに対して依然として小さく、マージが最優先されていない。マージボーナスを大幅に増強し、高度ペナルティを極端に緩和することで、マージを最優先できる。
#   成功基準: scoreがv412の1345を上回る、またはHIGH_LAYER_FUTURE_MERGEの割合が35%以上
#   失敗基準: scoreがv416の988以下、または改善が見られない
# v418: 極端な単純化・高度ペナルティ完全削除版 - v417の失敗（score=988、v412の1345を下回る）を受けて、v415-v417のアプローチ（高度ペナルティ緩和、マージボーナス増強、クラスタ密度係数調整）がすべて失敗していることを特定。v417ではマージボーナスを最大1350点に増強し、高度ペナルティを高度2.0で130点にまで緩和したが、それでもscore=988とv412の1345を大幅に下回った。このことは、現在のアプローチ（複雑なクラスタ密度評価、連鎖マージ評価、高度ペナルティ調整）が根本的に間違っていることを示している。v412が成功した当時のシンプルなロジック（高度ペナルティは基本的に適用しない、マージボーナスを単純に大きくする）に戻し、計算を極端に単純化することで、マージを最優先する。
#   根本原因の特定:
#   - v417の高度ペナルティは高度2.0で130点だが、これでもマージボーナスを打ち消している可能性がある
#   - v417のクラスタ密度係数（0個: 0.5倍、1個: 0.8倍）が計算を複雑にし、ボーナスの予測不可能性を増している
#   - v417の連鎖マージ係数（N-2: 0.8、N-3: 0.5）が計算を複雑にし、ボーナスの予測不可能性を増している
#   - v415-v417のアプローチは「高度ペナルティを緩和しながらマージボーナスを増強する」という相反する目的を持っており、バランスが取れていない
#   - v412が成功した当時は、高度ペナルティは基本的に適用せず、マージボーナスを単純に大きくしていた
#   - batch_summary.txtでHIGH_LAYER_FUTURE_MERGEが最も効果的だが、v417では複雑な計算がボーナスの予測不可能性を増している
#   改善策（極端な単純化・高度ペナルティ完全削除）:
#   - 高度ペナルティを完全削除（MEDIUMフェーズでも適用しない）
#   - マージボーナスを大幅増強: DIRECT 1350→2000点、NEAR 700→1000点、FAR 500→600点（マージを最優先）
#   - 先読みマージボーナスを単純化（クラスタ密度・連鎖係数を削除、重みtype * 15）
#   - 先読みマージボーナスの基本式: max(0, 1.5 - distance) * target_type * 15.0
#   - type N-1が1個以上の場合、追加ボーナス: target_type * 10.0（マージ機会確保）
#   - クラスタ密度評価・連鎖マージ評価を完全削除（計算を単純化）
#   - HIGH_TOWERペナルティを削除（高度ペナルティの完全削除の一環）
#   - ドリフトペナルティを20.0に軽減（v417の30.0から）
#   - 左右バランス補正を10.0に軽減（v417の20.0-40.0から）
#   - v417の既存ロジック（nextNextが同じタイプなら中央寄せボーナスなど）を維持
#   核心的発見: v415-v417の複雑なアプローチは失敗しており、v412のシンプルなロジックに戻す必要がある。高度ペナルティを完全削除し、計算を極端に単純化することで、マージを最優先できる。
#   成功基準: scoreがv412の1345を上回る、またはHIGH_LAYER_FUTURE_MERGEの割合が40%以上
#   失敗基準: scoreがv417の988以下、または改善が見られない
# v419: マージ絶対優先・超単純化版 - v418の失敗（scoreがv412の1345を大幅に下回る）を受けて、v418の単純化アプローチをさらに進め、マージを絶対優先する。v418では高度ペナルティを完全削除し、マージボーナスをDIRECT 2000点、NEAR 1000点、FAR 600点に増強したが、それでも失敗した。これは、先読みマージボーナスの距離係数(1.5 - distance)が厳しすぎて、広範囲でのマージ機会を見逃しているためと考えられる。また、ドリフトペナルティや左右バランス補正が依然としてマージの邪魔をしている可能性がある。マージボーナスをさらに増強し、先読みマージボーナスの距離係数を緩和し、ドリフトペナルティを最小限に抑えることで、マージを絶対優先する。
#   根本原因の特定:
#   - v418の先読みマージボーナスの距離係数は max(0, 1.5 - distance) で、distanceが1.5以上だとボーナスが0になる。これは広範囲でのマージ機会を見逃している
#   - v418のドリフトペナルティは 20.0 で、ドリフトが1.0あれば20点のペナルティになる。これはマージボーナスを打ち消す可能性がある
#   - v418の左右バランス補正は 10.0 で、バランスが偏っているとペナルティになる。これはマージの邪魔をしている可能性がある
#   - v418のマージボーナスは DIRECT 2000点だが、他のペナルティの影響でマージが選択されない可能性がある
#   - batch_summary.txtでHIGH_LAYER_FUTURE_MERGEが最も効果的だが、v418では距離係数が厳しすぎて効果が発揮されていない
#   改善策（マージ絶対優先・超単純化）:
#   - マージボーナスを超増強: DIRECT 2000→2500点、NEAR 1000→1500点、FAR 600→800点（マージを絶対優先）
#   - 先読みマージボーナスの距離係数を緩和: max(0, 2.0 - distance) に変更（v418の1.5から2.0へ、広範囲で適用）
#   - ドリフトペナルティを最小限: 20.0→10.0 に軽減（v418からさらに軽減）
#   - 左右バランス補正を削除: 完全に削除してマージの邪魔をしない
#   - nextNext中央寄せボーナスを削除: マージより優先しないようにする
#   - v418の既存ロジック（type N-1追加ボーナスなど）を維持
#   核心的発見: v418の単純化アプローチは正しいが、距離係数が厳しすぎて広範囲でのマージ機会を見逃している。距離係数を緩和し、ドリフトペナルティを最小限に抑えることで、マージを絶対優先できる。
#   成功基準: scoreがv412の1345を上回る、またはHIGH_LAYER_FUTURE_MERGEの割合が45%以上
#   失敗基準: scoreがv418の988以下、または改善が見られない


def decide(game_state: dict, analysis: dict) -> dict:
    """v419: マージ絶対優先・超単純化版"""

    results = analysis.get("results", [])

    if not results:
        return {"x": 0.0, "reason": "no analysis data"}

    best_x = 0.0
    best_score = -float("inf")
    best_reason = ""

    # 盤面情報
    pieces = game_state.get("pieces", [])

    # nextNextピース情報（先読みマージボーナス計算用）
    next_piece = game_state.get("next", {})
    next_next_piece = game_state.get("nextNext", {})
    next_type = next_piece.get("type", 0)
    next_next_type = next_next_piece.get("type", 0)

    for result in results:
        x = result["x"]
        drift_x = result.get("drift_x", 0)
        drift_unc = result.get("drift_unc", 0)
        merge_grade = result.get("merge_grade", "NO")

        score = 0.0
        reasons = []

        # === v419: マージ絶対優先・超単純化 ===

        # 1. マージグレードによるスコア（v419: 超増強）
        if merge_grade == "DIRECT":
            score += 2500.0  # v419: 2000から2500に超増強
            reasons.append("DIRECT_MERGE")
        elif merge_grade == "NEAR":
            score += 1500.0  # v419: 1000から1500に超増強
            reasons.append("NEAR_MERGE")
        elif merge_grade == "FAR":
            score += 800.0  # v419: 600から800に超増強
            reasons.append("FAR_MERGE")

        # 2. v419: 高度ペナルティは完全削除（v418から維持）

        # 3. ドリフトによるペナルティ（v419: 最小限に軽減）
        drift_penalty = (abs(drift_x) + drift_unc) * 10.0  # v419: 20.0から10.0に最小限に軽減
        score -= drift_penalty

        # 4. v419: 左右バランス補正を完全削除（マージの邪魔をしない）

        # 5. v419: nextNext中央寄せボーナスを完全削除（マージより優先しない）

        # 6. 先読みマージボーナス（v419: 距離係数を緩和して広範囲で適用）
        future_merge_bonus = 0.0
        for target_type in [next_type, next_next_type]:
            if target_type > 1:
                prev_type_pieces = [p for p in pieces if p["type"] == target_type - 1]

                # type N-1が0個でも適用（center_xを0として計算）
                if prev_type_pieces:
                    center_x = sum(p["x"] for p in prev_type_pieces) / len(
                        prev_type_pieces
                    )
                else:
                    center_x = 0.0

                distance = abs(x - center_x)

                # v419: 距離係数を緩和: max(0, 2.0 - distance) （v418の1.5から2.0へ）
                bonus = max(0, 2.0 - distance) * target_type * 15.0
                future_merge_bonus += bonus

                # v419: type N-1が1個以上の場合、追加ボーナス（マージ機会確保）
                if len(prev_type_pieces) >= 1:
                    future_merge_bonus += target_type * 10.0

        if future_merge_bonus > 0:
            score += future_merge_bonus
            reasons.append("FUTURE_MERGE")

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
