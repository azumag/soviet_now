#!/usr/bin/env python3
"""strategy.py - AI改善対象の決定スクリプト"""

# 固定インターフェース:
# decide(game_state: dict, analysis: dict) -> dict
#    戻り値: {"x": float, "reason": str}
#
# AI改変可能: decide() 内部、ヘルパー関数、定数、import
# AI改変禁止: decide() シグネチャ、if __name__ == "__main__" ブロック

# --- 変更履歴 ---
# [BEST:2325] v19: CRITICALフェーズ導入版 - HIGHフェーズのheight_mult過剰を修正、CRITICALフェーズ（max_y>3.0）を新設。CRITICALではマージ絶対優先（merge_mult=0.6、height_multなし、height_penaltyシンプル化）。MEDIUMフェーズheight_mult微増（2.2→2.4）でHIGH到達遅延、HIGHフェーズheight_mult微減（2.8→2.6）でマージ機会確保
# [BEST:2335] v42: v19復活・v31/v29複雑化要素削除版 - v41の失敗（スコア558）を受けて、v41がv31から取り入れたreactive_pairsとhas_mergeによる複雑な条件分岐を削除。v19のシンプル構造（DIRECT=1200/NEAR=600/FAR=200、height_penalty=50*height_mult、drift_penalty=30）に復活。v19のCRITICALフェーズ（merge_mult=0.6）を維持。コード量削減（約140行→約110行）で頑健性を確保
# v50-v64: has_merge/reactive_pairs条件の振り子パターンと閾値シャッフル
# [BEST:2346] v84: HIGHフェーズマージ優先・構造改善版 - v83の失敗（スコア1065、HIGHフェーズマージ率低）を受けて、振り子パターン完全回避で根本的な構造改善を実施。chain reaction緩和は完全廃止（v82の失敗から学ぶ）。代わりにHIGHフェーズでのマージ確保を優先：（1）merge_gradeボーナス強化（DIRECT=1500/NEAR=800/FAR=300でマージの質を重視）、（2）HIGHフェーズ高度管理緩和（height_mult=2.2に減、HIGH_TOWERペナルティ1.3倍に減）、（3）マージなし位置にNO_MERGEペナルティ（-150）、（4）max_yに応じた動的調整（盤面が高いほどマージ優先、低いほど高度管理優先）。v42のシンプル構造を維持しつつ、HIGHフェーズでのマージ機会確保を構造的に改善。コード量増加なし（約110行）。
# v93-v96: 振り子パターン（一律緩和→reactive_pairs活用→NO_MERGEペナルティ廃止→NO_MERGEペナルティ復活）- v93: height_multiplier 50.0→35.0、v94: 35.0→25.0、v95: reactive_pairs>=4で15.0・NO_MERGEペナルティ廃止、v96: reactive_pairs>=2で25.0・NO_MERGEペナルティ-150復活。v96にはreactive_pairsがlist型の時のバグがありturn 54以降でエラー発生。
# v123-v125: MEDIUMフェーズheight_multの振り子パターン（v122:2.2→v123:2.4→v124:2.2→v125:1.8）
# v126-v128: NO_MERGEペナルティとHIGH_TOWER削除の振り子パターン - v126: NO_MERGE追加、v127: NO_MERGE削除、v128: 高度管理緩和
# v129-v137: HIGH_TOWERペナルティの振り子パターン（v134:削除→v136:1.2倍→v137:2.0倍）- 一律のHIGH_TOWERペナルティが「削除すると高度管理不十分」「再導入するとマージ機会損失」の振り子を繰り返している。
# [BEST:3689] v128: HIGHフェーズマージ優先版 - v127の失敗（スコア724、HIGHフェーズ10ターン中9ターンでマージ不可）を受けて、HIGHフェーズでのマージ機会損失を特定。履歴分析でv127の高度管理がHIGHフェーズで過剰に強化されていることが原因を特定（HIGHフェーズのdecision_reasonはHIGH_TOWERが1回だが、HIGH_LAYERが5回で高度管理が支配的）。（1）HIGHフェーズ高度管理大幅緩和：height_multをv42の2.6から1.8に大幅に引き下げ（v84の2.2よりも緩和し、マージ優先を徹底）。（2）マージボーナス強化：v42の強力な値（DIRECT=1200/NEAR=600/FAR=200）を維持し、高度管理緩和と組み合わせてマージをHIGHフェーズの主要目標にする。（3）HIGHフェーズHIGH_TOWERペナルティ緩和：v84の1.3倍を維持し、height_mult大幅緩和と相乗効果。（4）v42のシンプル構造を維持：NO_MERGEペナルティの「入れるか入れないか」の振り子を回避し、第三の選択肢（マージボーナス強化・高度管理大幅緩和）を採用。振り子パターン（NO_MERGEペナルティ、height_multiplier微調整）をHIGHフェーズでのマージ優先徹底で解消。コード量維持（約110行）。
# v172-v174: TOWERペナルティ振り子パターン（復帰→緩和→削除→復帰）
# v191: v42統合・MEDIUMフェーズ高度管理強化版 - v190の失敗（スコア809、MEDIUMフェーズ高度管理不十分・HIGHフェーズHIGH_TOWER 100%発動）を受けて、v42の成功した高度管理パラメータを採用し、MEDIUMフェーズでの高度管理を根本的に強化するブレイクスルーを実施。（1）TOWERペナルティ閾値をv42の0.3に下げる：v190の0.5は緩すぎ、v42の成功した閾値0.3を採用。高層配置をより厳しく抑制し、MEDIUMフェーズの持続時間を確保。（2）MEDIUM_TOWERペナルティをv42の2.0倍に強化：v190の1.5倍から強化。v42の成功値2.0倍を採用し、MEDIUMフェーズでの高度管理を徹底。（3）バランス補正はv128の左右カウントベースを維持：v188の失敗から学び、重心ベース補正は副作用が大きいため採用せず。（4）MEDIUMフェーズheight_mult=2.6を維持：v190の強化値を維持。TOWERペナルティ閾値0.3と強化倍率の相乗効果で、MEDIUMフェーズでの高度管理を強化。（5）v42とv128の成功要素を統合：v42のTOWERペナルティ閾値0.3とMEDIUM_TOWER 2.0倍、v128のHIGHフェーズ構造（height_mult=1.8、HIGH_TOWER 1.3倍）を統合。（6）振り子パターン解消の第三の選択肢：TOWERペナルティ閾値の微調整（0.5→0.4→0.5）ではなく、v42の成功した閾値0.3を採用。これは「閾値の微調整」ではなく、「v42の成功したパラメータを採用」すること。コード量維持（約110行）。
# v192: v42完全復活・MEDIUMフェーズ高度管理採用版 - v191の失敗（スコア2072、MEDIUMフェーズ高度管理不足・HIGHフェーズHIGH_TOWER 100%発動）を受けて、v42の成功したMEDIUMフェーズ高度管理を完全採用するブレイクスルーを実施。v191履歴分析で確認した問題: - MEDIUMフェーズ高度管理不足（盤面上昇速度0.109/ターン、v128の0.056/ターンの約2倍） - MEDIUMフェーズ持続期間短縮（3ターンのみ、v128は7ターン） - HIGHフェーズでHIGH_TOWERが100%発動（10ターン中10回、マージを阻害） - HIGHフェーズマージ率低（20%、2回/10ターン） - MEDIUMフェーズheight_mult=2.6は強すぎ、v42の2.4が最適 - v191の変更は逆効果：TOWERペナルティ閾値0.3のみで機能せず、v42の「高度管理パッケージ全体」が必要。解決策: - MEDIUMフェーズheight_multをv42の2.4に戻す - HIGHフェーズheight_multをv42の2.6に戻す - MEDIUM_TOWER倍率をv42の1.5倍に戻す - HIGH_TOWER倍率をv42の2.0倍に強化 - TOWERペナルティ閾値0.3は維持 - バランス補正強度をv42の値に戻す（MEDIUM: 25.0、HIGH: 35.0、LOW: 15.0） - v42の「高度管理パッケージ」を完全採用。失敗（スコア1235）：v192履歴分析で確認した問題: - HIGHフェーズheight_mult=2.6は盤面上昇を強く抑制しすぎ、マージ機会を損なう（HIGHフェーズマージ率17%） - v42のHIGHフェーズheight_mult=2.6はv128の1.8よりも強すぎ - v128のHIGHフェーズ（height_mult=1.8）がスコア3689を達成 - 振り子パターン解消には、v42のHIGHフェーズheight_mult=2.6ではなく、v128の1.8を採用すべき
# v193: v42/v128統合・MEDIUM高度管理・HIGHマージ優先版 - v192の失敗（スコア1235、HIGHフェーズheight_mult=2.6が強すぎ・HIGHフェーズマージ率17%）を受けて、v42のMEDIUMフェーズ高度管理とv128のHIGHフェーズ設定を統合するブレイクスルーを実施。v192履歴分析とv128（スコア3689）の比較から、v128のHIGHフェーズ設定がv42のHIGHフェーズ設定よりも優れていることを特定。（1）MEDIUMフェーズheight_multはv42の2.4を維持：v128でも採用されている値で、MEDIUMフェーズ高度管理を確保。（2）HIGHフェーズheight_multはv128の1.8を採用：v192のv42値（2.6）は強すぎ、v128の1.8がHIGHフェーズでのマージ優先を可能にする（v128スコア3689）。（3）TOWERペナルティ閾値はv128の0.5を採用：v192のv42値（0.3）は厳しすぎ、MEDIUM_TOWERが支配的になるのを防ぐ。（4）MEDIUM_TOWER倍率はv42の1.5倍を採用：v191の2.0倍は強すぎ、v42の1.5倍がMEDIUMフェーズ高度管理に適している。（5）HIGH_TOWER倍率はv128の1.3倍を採用：HIGHフェーズでのマージ優先を維持。（6）バランス補正強度はv128の値を採用（HIGH=40.0/MEDIUM=30.0/LOW=20.0）：v191のv42値（35.0/25.0/15.0）より強化。（7）ドリフトペナルティ一律30.0を維持。（8）マージボーナスはv42の値（DIRECT=1200/NEAR=600/FAR=200）を維持。（9）v42とv128の成功要素を統合：v42のMEDIUMフェーズ高度管理（height_mult=2.4、MEDIUM_TOWER=1.5倍）とv128のHIGHフェーズ設定（height_mult=1.8、HIGH_TOWER=1.3倍、閾値0.5）を統合。振り子パターン（v42のHIGHフェーズheight_mult=2.6 vs v128の1.8）を第三の選択肢（v42のMEDIUMフェーズ高度管理 + v128のHIGHフェーズ設定）で解消。コード量維持（約110行）。


def decide(game_state: dict, analysis: dict) -> dict:
    """v42高度管理パッケージ採用・HIGHフェーズマージ優先版

    v193の失敗（スコア551、MEDIUMフェーズ高度管理不足・HIGHフェーズHIGH_TOWER発動率高・HIGHフェーズマージ率25%）を受けて、
    v42の「高度管理パッケージ全体」を採用し、v128のHIGHフェーズ設定を統合するブレイクスルーを実施。

    v193履歴分析で確認した問題:
    - TOWERペナルティ閾値0.5が緩すぎ（v42の0.3は厳しい）
    - バランス補正強度が強すぎ（v42は35.0/25.0/15.0、v193は40.0/30.0/20.0）
    - MEDIUMフェーズMEDIUM_TOWER発動率が高すぎ（23%、7/31ターン）
    - HIGHフェーズHIGH_TOWER発動率が高い（25%、3/12ターン）、マージ機会損失
    - v191の失敗（TOWERペナルティ閾値0.3のみ採用でバランス補正がv128の強さ）を踏まえ、v42の「高度管理パッケージ全体」が必要

    解決策:
    - TOWERペナルティ閾値をv42の0.3に厳しく（v193の0.5から）
    - バランス補正強度をv42の値に戻す（HIGH=35.0/MEDIUM=25.0/LOW=15.0）
    - HIGH_TOWER倍率をv42の2.0倍に強化（v193のv128値1.3倍から）
    - MEDIUMフェーズheight_mult=2.4を維持（v42の成功値、v128でも採用）
    - HIGHフェーズheight_mult=1.8を維持（v128の成功値、v193で採用済み）
    - v42の「高度管理パッケージ全体」を採用
    """

    results = analysis.get("results", [])

    if not results:
        return {"x": 0.0, "reason": "no analysis data"}

    best_x = 0.0
    best_score = -float("inf")
    best_reason = ""

    # 盤面情報
    pieces = game_state.get("pieces", [])
    max_y = max([p["y"] for p in pieces]) if pieces else -4.0

    # フェーズ判定（v42の閾値0.8/1.8/3.0を維持）
    if max_y < 0.8:
        phase = "LOW"
        height_mult = 1.0
        merge_mult = 1.2
    elif max_y < 1.8:
        phase = "MEDIUM"
        height_mult = 2.4  # v42の成功値を維持
        merge_mult = 1.0
    elif max_y < 3.0:
        phase = "HIGH"
        height_mult = 1.8  # v128の成功値を維持
        merge_mult = 1.0
    else:
        phase = "CRITICAL"
        height_mult = 1.0  # CRITICAL: height_multなし
        merge_mult = 0.6  # v42の成功値を維持

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

        score = 0.0
        reasons = []

        # === v194: v42高度管理パッケージ採用・HIGHフェーズマージ優先 ===

        # 1. マージグレードによるスコア（v42の値を維持）
        if merge_grade == "DIRECT":
            score += 1200.0 * merge_mult
            reasons.append("DIRECT_MERGE")
        elif merge_grade == "NEAR":
            score += 600.0 * merge_mult
            reasons.append("NEAR_MERGE")
        elif merge_grade == "FAR":
            score += 200.0 * merge_mult
            reasons.append("FAR_MERGE")

        # 2. 高度によるペナルティ
        height_penalty = landing_y * 50.0 * height_mult

        # TOWERペナルティ（v42の高度管理パッケージ全体を採用）
        if phase == "HIGH" and landing_y > 0.3:  # v42: 閾値0.3を採用
            height_penalty *= 2.0  # v42: 2.0倍を採用
            reasons.append("HIGH_TOWER")
        elif phase == "MEDIUM" and landing_y > 0.3:  # v42: 閾値0.3を採用
            height_penalty *= 1.5  # v42: 1.5倍を採用
            reasons.append("MEDIUM_TOWER")
        elif landing_y > 0.0:
            reasons.append("HIGH_LAYER")

        score -= height_penalty

        # 3. ドリフトによるペナルティ（v42: 一律30.0を維持）
        drift_penalty = (abs(drift_x) + drift_unc) * 30.0
        score -= drift_penalty

        # 4. 左右バランス補正（v42: v42の値を採用）
        balance_strength = 15.0
        if phase == "HIGH":
            balance_strength = 35.0  # v42: 35.0を採用（v193の40.0から減）
        elif phase == "MEDIUM":
            balance_strength = 25.0  # v42: 25.0を採用（v193の30.0から減）

        left_count = sum(1 for p in pieces if p["x"] < 0)
        right_count = len(pieces) - left_count
        balance_bias = (right_count - left_count) / (len(pieces) if pieces else 1)

        balance_penalty = x * balance_bias * balance_strength
        score -= abs(balance_penalty)

        # 5. nextNextが同じタイプなら中央寄せボーナス（v42: 一律50.0を維持）
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
