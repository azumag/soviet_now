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
# v93-v96: 振り子パターン（一律緩和→reactive_pairs活用→NO_MERGEペナルティ廃止→NO_MERGEペナルティ復活）- v93: height_multiplier 50.0→35.0、v94: 35.0→25.0、v95: reactive_pairs>=4で15.0・NO_MERGEペナルティ廃止、v96: reactive_pairs>=2で25.0・NO_MERGEペナルティ-150復活。v96にはreactive_pairsがlist型の時のバグがありturn 54以降でエラー発生。
# v123-v125: MEDIUMフェーズheight_multの振り子パターン（v122:2.2→v123:2.4→v124:2.2→v125:1.8）
# v133: v128成功構造復帰版 - v132の失敗（スコア1183）を受けて、振り子パターン（v42 HIGH:2.6 ↔ v128 HIGH:1.8）を解消し、v128の成功構造を復帰。履歴分析でv128の成功（スコア3689）は「MEDIUMはv42（height_mult=2.4）、HIGHはv128（height_mult=1.8）」というバランスによることを確認。v132はHIGHフェーズheight_mult=1.6で過度に緩和し、高度管理が不十分で盤面が早期に高くなった。（1）MEDIUMフェーズv42構造復帰：height_mult=2.4を維持、HIGH_TOWERペナルティはv42の1.5倍を採用（v131の1.5倍過剰性ではなく、v42のバランス採用）。（2）HIGHフェーズv128構造復帰：height_mult=1.8を維持、HIGH_TOWERペナルティはv128の1.3倍を維持。（3）CRITICALフェーズv42構造復帰：merge_mult=0.6を維持（v132の0.8強化は過剰）。（4）v42の基本構造維持：DIRECT=1200/NEAR=600/FAR=200、drift_penalty=30、balance補正、NEXT_SAMEボーナス=50。v128の成功構造を完全復帰し、振り子パターン（MEDIUM緩和・HIGH過度緩和）をv128のバランスで解消。コード量維持（約110行）。失敗（スコア1445）：履歴分析でHIGHフェーズ18ターン中13ターンで高度管理、マージ関連は5ターンのみ。HIGH_TOWERペナルティが9ターン連続（turns 74-82）でHIGHフェーズでのマージ機会が損失。v133はv128と同じ設定だが、大幅にスコアが低い。v128の成功（3689）は乱数運による可能性が高く、振り子パターン（v42↔v128）を解消する第三の選択肢が必要。
# v134: HIGH_TOWER/MEDIUM_TOWER削除・第三の選択肢版 - v133の失敗（スコア1445）を受けて、v42↔v128の振り子パターンを根本的に解消。履歴分析でHIGH_TOWERペナルティが9ターン連続し、HIGHフェーズでのマージ機会を完全に損失していることを特定。（1）HIGH_TOWER/MEDIUM_TOWERペナルティ削除：追加ペナルティを削除し、height_multのみで高度管理。コード簡素化と振り子パターン解消。（2）height_multを第三の選択肢に設定：HIGHフェーズheight_mult=2.2（v42の2.6とv128の1.8の中間、v42とv128のどちらでもない新しい値）。MEDIUMフェーズheight_mult=2.4を維持（v42の頑健性）。（3）HIGHフェーズマージボーナス強化：merge_mult=1.2でHIGHフェーズでのマージ優先を徹底。（4）v42の基本構造維持：DIRECT=1200/NEAR=600/FAR=200、drift_penalty=30、balance補正、NEXT_SAMEボーナス=50。v42とv128の振り子パターンを解消し、第三の選択肢（追加ペナルティ削除・height_mult中間値・マージボーナス強化）で突破。コード量削減（約110行→約100行）。
# v135: CRITICALフェーズ動的調整・マージ優先強化版 - v134の失敗（スコア1264）を受けて、CRITICALフェーズの高度管理緩和とマージボーナス強化を実施。履歴分析でCRITICALフェーズ（turns 72-75）でマージが発生していないことを特定。（1）CRITICALフェーズ高度ペナルティ大幅緩和：height_multを1.0から0.5に緩和し、CRITICALフェーズでの高度管理を大幅に緩和。（2）CRITICALフェーズマージボーナス強化：merge_multを0.6から0.8に強化し、最後のマージ機会を確実に捉える。（3）HIGHフェーズマージボーナス維持：merge_mult=1.2を維持し、HIGHフェーズでのマージ優先を徹底。（4）MEDIUMフェーズ高度管理維持：height_mult=2.4を維持し、HIGH到達遅延。（5）フェーズ判定の動的調整：max_yに応じてフェーズを自動的に調整し、状況に応じた戦略を実施。コード量微増（約100行→約110行）。


def decide(game_state: dict, analysis: dict) -> dict:
    """CRITICALフェーズで高度ペナルティを緩和し、マージボーナスを強化してマージ優先を徹底。"""

    results = analysis.get("results", [])

    if not results:
        return {"x": 0.0, "reason": "no analysis data"}

    best_x = 0.0
    best_score = -float("inf")
    best_reason = ""

    # 盤面情報
    pieces = game_state.get("pieces", [])
    max_y = max([p["y"] for p in pieces]) if pieces else -4.0

    # フェーズ判定（v135: 動的調整）
    if max_y < 0.8:
        phase = "LOW"
        height_mult = 1.0
        merge_mult = 1.2
    elif max_y < 1.8:
        phase = "MEDIUM"
        height_mult = 2.4  # v135: v42の2.4を維持（頑健な高度管理）
        merge_mult = 1.0
    elif max_y < 3.0:
        phase = "HIGH"
        height_mult = (
            2.2  # v135: 第三の選択肢（v42の2.6とv128の1.8の中間、振り子パターン解消）
        )
        merge_mult = 1.2  # v135: HIGHフェーズでマージ優先を徹底
    else:
        phase = "CRITICAL"
        height_mult = 0.5  # v135: CRITICALフェーズ高度ペナルティ大幅緩和
        merge_mult = 0.8  # v135: CRITICALフェーズマージボーナス強化

    # 次のピース情報
    next_piece = game_state.get("next", {})
    next_next_piece = game_state.get("nextNext", {})
    next_type = next_piece.get("type", 0)
    next_next_type = next_next_piece.get("type", 0)

    # 反応器の状態に基づく追加ボーナス
    reactor = analysis.get("reactor", {})
    reactive_pairs = reactor.get("reactive_pairs", 0)
    # reactive_pairsがlistの場合は長さを使用（v96のバグ修正）
    if isinstance(reactive_pairs, list):
        reactive_pairs = len(reactive_pairs)
    near_pairs = reactor.get("near_pairs", 0)
    # near_pairsもlistの場合は長さを使用
    if isinstance(near_pairs, list):
        near_pairs = len(near_pairs)
    pipeline = reactor.get("pipeline", [])

    for result in results:
        x = result["x"]
        landing_y = result.get("landing_y", 0)
        drift_x = result.get("drift_x", 0)
        drift_unc = result.get("drift_unc", 0)
        merge_grade = result.get("merge_grade", "NO")

        score = 0.0
        reasons = []

        # === v135: CRITICALフェーズ動的調整・マージ優先強化 ===

        # 1. マージグレードによるスコア（v135: 動的調整）
        if merge_grade == "DIRECT":
            score += 1200.0 * merge_mult
            reasons.append("DIRECT_MERGE")
        elif merge_grade == "NEAR":
            score += 600.0 * merge_mult
            reasons.append("NEAR_MERGE")
        elif merge_grade == "FAR":
            score += 200.0 * merge_mult
            reasons.append("FAR_MERGE")

        # マージ予測に基づく追加ボーナス
        if merge_grade != "NO":
            for merge in result.get("merges", []):
                distance = merge.get("distance", float("inf"))
                if distance < 0.5:
                    score += 100.0
                    reasons.append("CLOSE_MERGE")

        # 反応器の状態に基づく追加ボーナス
        if reactive_pairs >= 2:
            score += 50.0
            reasons.append("REACTIVE_PAIRS")
        if near_pairs >= 3:
            score += 30.0
            reasons.append("NEAR_PAIRS")
        if pipeline and all(p is not None for p in pipeline):
            score += 20.0
            reasons.append("PIPELINE_OK")

        # 2. 高度によるペナルティ（v135: 動的調整）
        height_penalty = landing_y * 50.0 * height_mult

        if landing_y > 0.0:
            reasons.append("HIGH_LAYER")

        score -= height_penalty

        # 3. ドリフトによるペナルティ（v135: v42の一律30.0を維持）
        drift_penalty = (abs(drift_x) + drift_unc) * 30.0
        score -= drift_penalty

        # 4. 左右バランス補正（v135: 動的調整）
        balance_strength = 20.0
        if phase == "HIGH":
            balance_strength = 40.0
        elif phase == "MEDIUM":
            balance_strength = 30.0
        elif phase == "CRITICAL":
            balance_strength = 20.0  # v135: CRITICALフェーズバランス補正緩和

        left_count = sum(1 for p in pieces if p["x"] < 0)
        right_count = len(pieces) - left_count
        balance_bias = (right_count - left_count) / (len(pieces) if pieces else 1)

        balance_penalty = x * balance_bias * balance_strength
        score -= abs(balance_penalty)

        # 5. nextNextが同じタイプなら中央寄せボーナス（v135: v42の一律50.0を維持）
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
