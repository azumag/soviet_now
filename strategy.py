#!/usr/bin/env python3
"""strategy.py - Soviet Puzzle Game AI Drop Position Script

Game Overview:
  - Drop pieces, merge same type pieces (N+N -> N+1)
- Score table: type1=1, type2=3, type3=6, ..., typeN = N*(N+1)/2
- Board: x in [-3.0, +3.0], floor y=-4.48, deadline y=3.32
  - Player controls only drop X coordinate

  Decision Logic (10 evaluation axes):
     1. Merge bonus - High score for immediate merge (DIRECT > NEAR > FAR)
    2. Height penalty - Penalty for high landing position (varies by phase)
     3. Drift penalty - Penalty for post-landing drift due to polygon shape
     4. Left-right balance correction - Bonus for correcting piece count bias
      5. nextNext centering - Center for next merge opportunity if nextNext same type
       5.5. Avoid blocking nextNext merge - Penalty for landing on same-type piece when nextNext matches
       6. Chain merge bonus - Evaluate possibility of further merges after merge
        7. Reactive pairs bonus - Bonus for multiple merge opportunities (reactor info utilization, v206: enhanced)
       8. Early game merge priority - Strong bonus for merge opportunities in early game
        8.5. Reactive pairs board compression - Bonus for dense placement when reactive_pairs >= 3 and no immediate merge (v206: reduced)
        9. Reactive pairs default - Default to REACTIVE_PAIRS_COMPRESSION when reactive_pairs >= 1 and no immediate merge
        9.5. Current type stack merge priority - v277: Same type stacking enhanced (reactive>=1:+800.0, reactive==0:+300.0, deadline_crossed: always active)

Phases (determined by board max Y):
     LOW      (max_y < 0.8) : Early game. Merge priority (merge_mult=1.2)
     MEDIUM   (0.8 <= max_y < 1.8) : Mid game. Height management (height_mult=1.4)
     HIGH     (1.8 <= max_y < 3.0) : Late game. Merge opportunity (height_mult=1.8)
     CRITICAL (3.0 <= max_y) : Danger. DIRECT merge priority, board compression (NEAR carefully)
"""

# Fixed interface:
# decide(game_state: dict, analysis: dict) -> dict
#    Returns: {"x": float, "reason": str}
#
# AI modifiable: decide() body, helper functions, constants, imports
# AI prohibited: decide() signature, if __name__ == "__main__" block

# --- Change History ---
# v300: deadline_crossed時即時併合優先強化版 - v297 failure mode潰し
# deadline_crossed時はaxis 8.5/axis 8.6の即時併合優先を最優先し、axis 9.5戦略的配置ボーナスを抑制
# v297失敗モード（reactive_pairs>=3 && deadline_crossed && merge_grade=="NO" && danger_piece_count==0でheight_mult=0.5緩和によるmax_y runaway）を潰す
# 即時併合機会の取りこぼしを削減し、deadline_crossed時のmax_y上昇を抑制することで下振れ耐性を向上
# ワーストゲーム(score0423)終盤turns 46-53でdeadline_crossed=true, reactive_pairs=4-5, merge_available=false続き、
# 戦略的配置が続きmax_y=2.43でゲームオーバー。
# ベストゲーム(score2301)終盤turns 111-118ではdeadline_crossed=trueでも即時併合を確実に捉えてスコア319点稼ぎ、max_y=2.90→2.22で回復。
# refs: tmp/improve_brief.md, tmp/batch_summary.txt, tmp/state/last_rollback_postmortem.md, tmp/state/last_rollback_analysis.md, advice.md,
#       game_history/20260321_130219_score0423.jsonl turns 46-53, game_history/20260321_131045_score2301.jsonl turns 111-118
#
# v300の変更点:
# 1. axis 8.5（危険領域即時併合優先）ボーナス強化: 1個+900.0, 2個+1100.0, 3個以上+1300.0
# 2. axis 8.6（reactive_pairs即時併合優先）ボーナス強化: 1個+900.0, 2個+1400.0, 3個以上+1700.0
# 3. 即時併合不可時の戦略的配置ボーナス抑制: reactive_pairs>=1 → 200.0, 危険あり → 25.0
# 4. axis 9.5の適用条件に `not deadline_crossed` を追加し、deadline_crossed時はaxis 8.6の即時併合優先を最優先
# 5. reactive_pairs>=3で即時併合不可の場合、height_mult緩和を0.5→0.7に強化し、deadline_crossed時のmax_y上昇を抑制


def decide(game_state, analysis):
    """AI decision function - must return {"x": float, "reason": str}"""
    # TODO: Implement decide function
    return {"x": 0.0, "reason": "HEIGHT_CONTROL"}


 # --- AI modification prohibited zone ---
if __name__ == "__main__":
    import json
    import sys

    # standalone test
    gs_path = sys.argv[1] if len(sys.argv) > 1 else "game_state.json"

    try:
        game_state = json.load(open(gs_path))
    except Exception as e:
        print(json.dumps({"error": str(e)}))
        sys.exit(1)

    # get analysis data from analyze_board
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
