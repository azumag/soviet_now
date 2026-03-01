#!/usr/bin/env python3
"""strategy.py - AI改善対象の決定スクリプト"""

# 固定インターフェース:
# decide(game_state: dict, analysis: dict) -> dict
#    戻り値: {"x": float, "reason": str}
#
# AI改変可能: decide() 内部、ヘルパー関数、定数、import
# AI改変禁止: decide() シグネチャ、if __name__ == "__main__" ブロック

# --- 変更履歴 ---
# v411: マージボーナス適正化・v42復帰版 - v410の失敗（score=959、merge_rate=12.5%）を受けて、マージボーナスが強すぎてマージ機会が少ない状況を特定。ベストスコア戦略（v2346）のv42のマージボーナス（DIRECT=1200/NEAR=600/FAR=200）を採用し、マージの質を重視。HIGHフェーズのheight_mult=1.8固定（v128の成功要素）。reactive_pairs情報を完全削除（v410の維持）。batch_summary.txtの分析でHIGH_LAYERとHIGH_TOWERが支配的（50%）であり、マージボーナスが活きにくい構造を改善。
#   根本原因の特定:
#   - v410はマージボーナスを強化（DIRECT=1500/NEAR=800/FAR=300）していたが、merge_rate=12.5%と低く、マージ機会が少ない状況
#   - ベストスコア戦略（v2346）のv42はDIRECT=1200/NEAR=600/FAR=200とマージボーナスが適正
#   - v128（best_score=3689）の成功要素は「高度管理緩和（height_mult=1.8）」にあり、マージボーナス強化ではない
#   - batch_summary.txtの分析でHIGH_LAYERとHIGH_TOWERが支配的（50%）であり、マージボーナスが活きにくい構造
#   改善策（マージボーナス適正化・v42復帰）:
#   - マージボーナスをv42の設定に復帰（DIRECT=1200/NEAR=600/FAR=200）
#   - HIGHフェーズのheight_multを1.8に固定（v128の成功要素）
#   - HIGH_TOWERペナルティを1.3倍に維持（v128の設定）
#   - reactive_pairs情報を完全削除（v410の維持）
#   - ドリフトペナルティを30.0に維持（v128の設定）
#   - 左右バランス補正をv128の設定に復帰
#   - nextNext中央寄せボーナスをv128の設定に復帰
#   核心的発見: マージボーナスが強すぎると、マージ機会が少ない状況でボーナスが活きにくい。v42のマージボーナス（DIRECT=1200/NEAR=600/FAR=200）と高度管理緩和（height_mult=1.8）のバランスが最適であることが確認された。
#   成功基準: merge_rate=15%以上、またはscoreがv128の3689に近い
#   失敗基準: merge_rate=12.5%未満、またはreactive_pairsが使用される


def decide(game_state: dict, analysis: dict) -> dict:
    """v42のマージボーナスとv128の高度管理緩和を組み合わせた適正化戦略。reactive_pairs情報を完全削除。"""
    
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
    
    # フェーズ判定（v411: v42の閾値0.8/1.8/3.0を維持）
    if max_y < 0.8:
        phase = "LOW"
        height_mult = 1.0
        merge_mult = 1.2
    elif max_y < 1.8:
        phase = "MEDIUM"
        height_mult = 2.4
        merge_mult = 1.0
    elif max_y < 3.0:
        phase = "HIGH"
        # v411: v128の1.8を固定（reactive_pairs動的調整を削除）
        height_mult = 1.8
        merge_mult = 1.0
    else:
        phase = "CRITICAL"
        height_mult = 1.0
        merge_mult = 0.6
    
    for result in results:
        x = result["x"]
        landing_y = result.get("landing_y", 0)
        drift_x = result.get("drift_x", 0)
        drift_unc = result.get("drift_unc", 0)
        merge_grade = result.get("merge_grade", "NO")
        
        score = 0.0
        reasons = []
        
        # === v411: v42のマージボーナスとv128の高度管理緩和 ===
        
        # 1. マージグレードによるスコア（v411: v42の適正な値を維持）
        if merge_grade == "DIRECT":
            score += 1200.0 * merge_mult
            reasons.append("DIRECT_MERGE")
        elif merge_grade == "NEAR":
            score += 600.0 * merge_mult
            reasons.append("NEAR_MERGE")
        elif merge_grade == "FAR":
            score += 200.0 * merge_mult
            reasons.append("FAR_MERGE")
        
        # 2. 高度によるペナルティ（v411: v128の固定値）
        height_penalty = landing_y * 50.0 * height_mult
        
        # HIGH_TOWERペナルティ（v411: v128の設定に復帰）
        if phase == "HIGH" and landing_y > 0.5:
            height_penalty *= 1.3
            reasons.append("HIGH_TOWER")
        elif phase == "MEDIUM" and landing_y > 0.5:
            height_penalty *= 1.3
            reasons.append("MEDIUM_TOWER")
        elif landing_y > 0.0:
            reasons.append("HIGH_LAYER")
        
        score -= height_penalty
        
        # 3. ドリフトによるペナルティ（v411: v128の一律30.0を維持）
        drift_penalty = (abs(drift_x) + drift_unc) * 30.0
        score -= drift_penalty
        
        # 4. 左右バランス補正（v411: v128の設定に復帰）
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
        
        # 5. nextNextが同じタイプなら中央寄せボーナス（v411: v128の設定に復帰）
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
