#!/usr/bin/env python3
"""strategy.py - AI改善対象の決定スクリプト"""

# 固定インターフェース:
# decide(game_state: dict, analysis: dict) -> dict
#    戻り値: {"x": float, "reason": str}
#
# AI改変可能: decide() 内部,ヘルパー関数,定数,import
# AI改変禁止: decide() シグネチャ,if __name__ == "__main__" ブロック

# --- 変更履歴 ---
# [BEST:2325] v19: CRITICALフェーズ導入版 - HIGHフェーズのheight_mult過剰を修正、CRITICALフェーズ（max_y>3.0）を新設。CRITICALではマージ絶対優先（merge_mult=0.6、height_multなし、height_penaltyシンプル化）。MEDIUMフェーズheight_mult微増（2.2→2.4）でHIGH到達遅延、HIGHフェーズheight_mult微減（2.8→2.6）でマージ機会確保
# [BEST:2335] v42: v19復活・v31/v29複雑化要素削除版 - v41の失敗（スコア558）を受けて、v41がv31から取り入れたreactive_pairsとhas_mergeによる複雑な条件分岐を削除。v19のシンプル構造（DIRECT=1200/NEAR=600/FAR=200、height_penalty=50*height_mult、drift_penalty=30）に復活。v19のCRITICALフェーズ（merge_mult=0.6）を維持。コード量削減（約140行→約110行）で頑健性を確保
# v50-v64: has_merge/reactive_pairs条件の振り子パターンと閾値シャッフル
# [BEST:2346] v84: HIGHフェーズマージ優先・構造改善版 - v83の失敗（スコア1065、HIGHフェーズマージ率低）を受けて、振り子パターン完全回避で根本的な構造改善を実施。chain reaction緩和は完全廃止（v82の失敗から学ぶ）。代わりにHIGHフェーズでのマージ確保を優先：（1）merge_gradeボーナス強化（DIRECT=1500/NEAR=800/FAR=300でマージの質を重視）、（2）HIGHフェーズ高度管理緩和（height_mult=2.2に減、HIGH_TOWERペナルティ1.3倍に減）、（3）マージなし位置にNO_MERGEペナルティ（-150）、（4）max_yに応じた動的調整（盤面が高いほどマージ優先、低いほど高度管理優先）。v42のシンプル構造を維持しつつ、HIGHフェーズでのマージ機会確保を構造的に改善。コード量増加なし（約110行）。
# v89: マージなしペナルティ全フェーズ版 - v88の失敗（スコア1098、HIGHフェーズでマージ確保ボーナスが機能せず）を受けて、振り子パターン（height_mult振り子: v42=2.6→v84=2.2→v86=2.6→v87=2.4→v88=2.2）を断ち切るため、height_multをv42の2.6に固定し、マージ確保はマージなしペナルティで強制する構造に変更。v86のシンプル構造（約160行）を維持しつつ：（1）マージなしペナルティをHIGHフェーズ専用から全フェーズに拡大し、HIGHフェーズでは-200と強化；（2）height_multはv42の2.6を維持（安全性確保、振り子回避）；（3）v84のmerge_grade強化（1500/800/300）は採用せず、v42の成功値（1200/600/200）に戻す（ボーナス過剰による高度管理優先を解消）；（4）has_merge条件や動的調整は一切導入しない（v42の頑健性維持）。v84のマージ確保アイデアとv42のシンプル構造を融合し、height_mult振り子を回避して頑健性を確保。
# v90: merge_gradeベース高度管理緩和版 - v89の失敗（スコア1261、HIGHフェーズマージ率8%）を受けて、振り子パターン（height_mult: v42→v84→v86→v87→v88→v89で2.6/2.2/2.6/2.4/2.2/2.6、merge_grade: v42→v84→v89で1200/1500/1200）を断ち切るため、v84の成功値（height_mult=2.2）を固定し、振り子回避を確定。代わりに、HIGHフェーズでmerge_gradeに応じた高度管理緩和を導入：DIRECTでheight_multiplierを25に緩和（50→25）、NEARで40に緩和、FARで45に緩和、NOで50のまま。これにより、マージの質に応じた段階的な高度管理緩和を実現し、HIGHフェーズでのマージ機会確保を構造的に改善。v85のhas_mergeベース動的緩和とは異なり、merge_gradeベースでより直感的かつ予測可能な緩和戦略を実現。height_mult一律2.2固定で振り子完全回避、コード量微増（約120行）だが構造はシンプル維持。
# v91: reactor情報活用chain reaction緩和版 - v90の失敗（スコア762、HIGHフェーズで「HIGH_TOWER_NO_MERGE_PENALTY」が4/5ターン、マージ率0%）を受けて、振り子パターン（height_mult: v42=2.6→v84=2.2→v89=2.6→v90=2.2→v91=2.6）を断ち切るため、v42の成功値（height_mult=2.6）に戻し、予測ベースのmerge_grade緩和を完全削除。履歴分析でanalyze_boardのmerge_grade予測と実際のマージ結果が乖離し、予測ベースの高度管理緩和が誤判断を助長していることを特定。代わりにreactor情報を活用したchain reaction緩和を導入：HIGHフェーズでreactive_pairs >= 2の時、height_multiplierを35.0に緩和し、height_penalty_factorも0.6に緩和（v31の条件>=3を>=2に緩和して発動頻度を増やす）。v42のシンプル構造を維持しつつ、実際にマージが起きている状況での高度管理緩和でHIGHフェーズでのマージ機会確保を最大化。振り子完全回避、予測ベース削除、構造的改善。


def decide(game_state: dict, analysis: dict) -> dict:
    """v42のシンプル構造に戻し、reactor情報を活用したchain reaction緩和を導入"""

    results = analysis.get("results", [])

    if not results:
        return {"x": 0.0, "reason": "no analysis data"}

    best_x = 0.0
    best_score = -float("inf")
    best_reason = ""

    # 盤面情報
    pieces = game_state.get("pieces", [])
    max_y = max([p["y"] for p in pieces]) if pieces else -4.0

    # reactor情報（v91で新規活用）
    reactor = analysis.get("reactor", {})
    reactive_pairs_raw = reactor.get("reactive_pairs", 0)
    reactive_pairs = (
        len(reactive_pairs_raw)
        if isinstance(reactive_pairs_raw, list)
        else reactive_pairs_raw
    )

    # フェーズ判定（v91: v42の閾値0.8/1.8/3.0を維持）
    if max_y < 0.8:
        phase = "LOW"
        height_mult = 1.0
        merge_mult = 1.2
    elif max_y < 1.8:
        phase = "MEDIUM"
        height_mult = 2.4  # v91: v42の2.4を維持
        merge_mult = 1.0
    elif max_y < 3.0:
        phase = "HIGH"
        height_mult = 2.6  # v91: v42の2.6に戻す（振り子回避）
        merge_mult = 1.0
    else:
        phase = "CRITICAL"
        height_mult = 1.0  # CRITICAL: height_multなし
        merge_mult = 0.6  # v91: v42の0.6を維持

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
        has_merge = result.get("has_merge", False)

        score = 0.0
        reasons = []

        # === v91: reactor情報活用chain reaction緩和戦略 ===

        # 1. マージグレードによるスコア（v91: v42の成功値を維持）
        if merge_grade == "DIRECT":
            score += 1200.0 * merge_mult  # v91: v42の1200を維持
            reasons.append("DIRECT_MERGE")
        elif merge_grade == "NEAR":
            score += 600.0 * merge_mult  # v91: v42の600を維持
            reasons.append("NEAR_MERGE")
        elif merge_grade == "FAR":
            score += 200.0 * merge_mult  # v91: v42の200を維持
            reasons.append("FAR_MERGE")

        # 2. 高度によるスコア（v91: chain reaction時に緩和）
        if phase == "CRITICAL":
            # CRITICALフェーズではheight_multiplier強化（v42の40.0を維持）
            height_multiplier = 40.0
            height_penalty = landing_y * height_multiplier
            if landing_y > 1.0:
                reasons.append("CRITICAL_HEIGHT")
        else:
            # v91: HIGHフェーズでchain reaction中（reactive_pairs >= 2）なら緩和
            height_penalty_factor = 1.0
            if phase == "HIGH" and reactive_pairs >= 2:
                height_multiplier = 35.0  # chain reaction中は緩和（v31の35.0を維持）
                reasons.append("CHAIN_REACTION")
            else:
                height_multiplier = 50.0

            height_penalty = (
                landing_y * height_mult * height_multiplier * height_penalty_factor
            )

            # 高盤面での追加ペナルティ（CRITICALフェーズでは適用しない）
            if phase == "HIGH" and landing_y > 0.5:
                height_penalty *= 2.0  # v91: v42の2.0を維持
                reasons.append("HIGH_TOWER")
            elif phase == "MEDIUM" and landing_y > 0.5:
                height_penalty *= 1.5  # v91: v42の1.5を維持
                reasons.append("MEDIUM_TOWER")
            elif landing_y > 0.0:
                reasons.append("HIGH_LAYER")

        score -= height_penalty

        # 3. ドリフトによるペナルティ（v91: v42の一律計算を維持）
        drift_penalty = (abs(drift_x) + drift_unc) * 30.0
        score -= drift_penalty

        # 4. 左右バランス補正（v91: v42の設定を維持）
        balance_strength = 20.0
        if phase == "HIGH":
            balance_strength = 40.0  # v91: v42の40.0を維持
        elif phase == "MEDIUM":
            balance_strength = 30.0  # v91: v42の30.0を維持

        left_count = sum(1 for p in pieces if p["x"] < 0)
        right_count = len(pieces) - left_count
        balance_bias = (right_count - left_count) / (len(pieces) if pieces else 1)

        balance_penalty = x * balance_bias * balance_strength
        score -= abs(balance_penalty)

        # 5. nextNextが同じタイプなら中央寄せボーナス（v91: v42の設定を維持）
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
