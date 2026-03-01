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
# v420: v128成功構造復活・先読みボーナス慎重追加版 - v419の失敗（score=1156、v412の1345を大幅に下回る）を受けて、v419の振り子パターン（左右バランス補正の完全削除→削除のまま、type N-1がない時の不自然なcenter_x=0.0設定）を特定。v419のbatch_summary.txtでFUTURE_MERGEが62.7%を占めるが、実際のマージ（DIRECT/NEAR/FAR）はわずか4.5%で、先読みボーナスが支配的すぎて実際のマージを見逃していることを確認。v128（score=3689）の成功構造をベースに復活：（1）マージボーナスをv128の強力な値（DIRECT=1200/NEAR=600/FAR=200）に戻す（v419の2500/1500/800は過剰で、FAR_MERGEの評価が不適切）、（2）v128のHIGHフェーズ高度管理緩和（height_mult=1.8）を採用、（3）左右バランス補正を復活（v419の完全削除は盤面偏りを無視し失敗）、（4）先読みマージボーナスを慎重に追加：type N-1が0個の場合はボーナスを付与しない（v419の不自然なcenter_x=0.0を回避）、距離係数をv419の2.0より緩やかに広げる（max(0, 3.0 - distance)）、ボーナス重みはv128のシンプル構造を尊重してtype * 10.0に設定、type N-1が1個以上の場合にのみ追加ボーナスtype * 5.0を付与。v128のシンプルで堅牢な構造を維持しつつ、v419の先読みマージのアイデアを慎重に統合。振り子パターン（左右バランス補正の削除と復活）を第三の選択肢（v128の成功構造をベースに先読みボーナスを慎重に追加）で解決。
#   根本的発見: v419の失敗は「左右バランス補正を削除したことで盤面が極端に偏った」と「type N-1がない時に不自然にcenter_x=0.0としたことで、盤面の実際の分布を無視した先読みボーナスが計算された」の2点。v128の成功構造（マージボーナス強力、HIGHフェーズ高度管理緩和、バランス補正維持）を土台にし、先読みボーナスを「type N-1がある場合にのみ付与する」という慎重な条件で追加することで、実際のマージと将来のマージの両方を評価できる。
#   成功基準: scoreがv412の1345を上回る、または実際のマージ率（DIRECT/NEAR/FAR）が20%以上
#   失敗基準: scoreがv419の1156以下、または先読みボーナスがv419のように支配的（50%以上）になる


def decide(game_state: dict, analysis: dict) -> dict:
    """v420: v128成功構造復活・先読みボーナス慎重追加版"""

    results = analysis.get("results", [])

    if not results:
        return {"x": 0.0, "reason": "no analysis data"}

    best_x = 0.0
    best_score = -float("inf")
    best_reason = ""

    # 盤面情報
    pieces = game_state.get("pieces", [])
    max_y = max([p["y"] for p in pieces]) if pieces else -4.0

    # フェーズ判定（v128の閾値0.8/1.8/3.0を採用）
    if max_y < 0.8:
        phase = "LOW"
        height_mult = 1.0
        merge_mult = 1.2
    elif max_y < 1.8:
        phase = "MEDIUM"
        height_mult = 2.4  # v128の2.4を維持
        merge_mult = 1.0
    elif max_y < 3.0:
        phase = "HIGH"
        height_mult = (
            1.8  # v420: v128のHIGHフェーズ高度管理緩和（1.8）を採用（マージ優先を徹底）
        )
        merge_mult = 1.0
    else:
        phase = "CRITICAL"
        height_mult = 1.0  # CRITICAL: height_multなし
        merge_mult = 0.6  # v420: v128の0.6を維持

    # nextNextピース情報
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

        # === v420: v128成功構造復活 ===

        # 1. マージグレードによるスコア（v420: v128の強力な値に戻す）
        if merge_grade == "DIRECT":
            score += 1200.0 * merge_mult  # v420: v128の1200に戻す（v419の2500は過剰）
            reasons.append("DIRECT_MERGE")
        elif merge_grade == "NEAR":
            score += 600.0 * merge_mult  # v420: v128の600に戻す（v419の1500は過剰）
            reasons.append("NEAR_MERGE")
        elif merge_grade == "FAR":
            score += 200.0 * merge_mult  # v420: v128の200に戻す（v419の800は過剰）
            reasons.append("FAR_MERGE")

        # 2. 高度によるペナルティ（v420: v128の設定を維持）
        height_penalty = landing_y * 50.0 * height_mult

        # HIGH_TOWERペナルティ（v420: v128の1.3倍を採用）
        if phase == "HIGH" and landing_y > 0.5:
            height_penalty *= 1.3  # v420: v128の1.3倍を採用（HIGHフェーズ高度管理緩和）
            reasons.append("HIGH_TOWER")
        elif phase == "MEDIUM" and landing_y > 0.5:
            height_penalty *= 1.5  # v420: v128の1.5倍を維持
            reasons.append("MEDIUM_TOWER")
        elif landing_y > 0.0:
            reasons.append("HIGH_LAYER")

        score -= height_penalty

        # 3. ドリフトによるペナルティ（v420: v128の一律30.0を維持）
        drift_penalty = (abs(drift_x) + drift_unc) * 30.0
        score -= drift_penalty

        # 4. 左右バランス補正（v420: v419の完全削除を撤回し、v128の設定を復活）
        balance_strength = 20.0
        if phase == "HIGH":
            balance_strength = 40.0  # v420: v128の40.0を維持
        elif phase == "MEDIUM":
            balance_strength = 30.0  # v420: v128の30.0を維持

        left_count = sum(1 for p in pieces if p["x"] < 0)
        right_count = len(pieces) - left_count
        balance_bias = (right_count - left_count) / (len(pieces) if pieces else 1)

        balance_penalty = x * balance_bias * balance_strength
        score -= abs(balance_penalty)

        # 5. nextNextが同じタイプなら中央寄せボーナス（v420: v128の設定を維持）
        if next_next_type == next_type:
            center_bonus = max(0, 1.0 - abs(x) / 2.0) * 50.0
            score += center_bonus
            reasons.append("NEXT_SAME")

        # 6. 先読みマージボーナス（v420: type N-1がある場合にのみ慎重に追加）
        future_merge_bonus = 0.0
        for target_type in [next_type, next_next_type]:
            if target_type > 1:
                prev_type_pieces = [p for p in pieces if p["type"] == target_type - 1]

                # v420: type N-1が0個の場合はボーナスを付与しない（v419の不自然なcenter_x=0.0を回避）
                if len(prev_type_pieces) >= 1:
                    center_x = sum(p["x"] for p in prev_type_pieces) / len(
                        prev_type_pieces
                    )
                    distance = abs(x - center_x)

                    # v420: 距離係数を広げる: max(0, 3.0 - distance) （v419の2.0より緩和）
                    bonus = (
                        max(0, 3.0 - distance) * target_type * 10.0
                    )  # v420: v128のシンプル構造を尊重し、type * 10.0に設定
                    future_merge_bonus += bonus

                    # v420: type N-1が1個以上の場合、追加ボーナス（type * 5.0）
                    future_merge_bonus += target_type * 5.0

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
