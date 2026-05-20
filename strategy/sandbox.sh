# strategy/sandbox.sh - validate_strategy, create/harvest/destroy_sandbox

validate_strategy() {
	# 引数でファイルパスを指定可能 (デフォルト: strategy.py)
	local target_file="${1:-strategy.py}"
	local helpers_dir="${2:-strategy_helpers}"
	log "[VALIDATE] checking $target_file..."
	VALIDATE_ERROR=""

	# deadline guard 冪等注入: validate_strategy の deadline-*-guard 合成テストは
	# decide() が deadline 危険時に安全な非クロスを返すことを要求する。改善
	# pipeline (eloop_improve の staging) も rollback 復元も guard を注入しない
	# ため、guard 無し戦略が毎回検証失敗→継続修正で最大18 AIリトライ空費し
	# 回帰カスケードのエンジンになっていた。検証対象に guard を冪等注入して
	# from-source で必ず guard 付きにする (既に有れば inject_guard が no-op)。
	# 緊急停止は DEADLINE_GUARD_AUTO_INJECT=0。
	if [ "${DEADLINE_GUARD_AUTO_INJECT:-1}" = "1" ] && [ -f "$target_file" ] && case "$target_file" in *.py|*.py.staging) true ;; *) false ;; esac; then
		local _deadline_guard_inject_out
		_deadline_guard_inject_out=$(python3 - "$target_file" <<'PYINJ' 2>/dev/null
import sys
sys.path.insert(0, ".")
import inject_deadline_guard as ig
p = sys.argv[1]
src = open(p, encoding="utf-8").read()
new = ig.inject_guard(src)
if new is not None:
    open(p, "w", encoding="utf-8").write(new)
    print("injected")
PYINJ
		)
		if [ "$_deadline_guard_inject_out" = "injected" ]; then
			log "[VALIDATE] deadline guard を $target_file に冪等注入/更新"
		fi
	fi

	local sig_out
	sig_out=$(
		python3 - "$target_file" "$helpers_dir" <<'PYEOF' 2>&1
import os
import sys
import inspect
import types

target = sys.argv[1]
helpers_dir = sys.argv[2] if len(sys.argv) > 2 else "strategy_helpers"
root = os.getcwd()
if root not in sys.path:
    sys.path.insert(0, root)
if helpers_dir and os.path.isdir(helpers_dir):
    helper_parent = os.path.abspath(os.path.dirname(helpers_dir) or ".")
    if helper_parent not in sys.path:
        sys.path.insert(0, helper_parent)

# .py.staging ファイルを扱うため、exec() でモジュールを作成
with open(target, 'r', encoding='utf-8') as f:
    source = f.read()

mod = types.ModuleType('strategy')
exec(source, mod.__dict__)

if not hasattr(mod, 'decide'):
    print('ERROR: decide() not found')
    sys.exit(1)
sig = inspect.signature(mod.decide)
params = list(sig.parameters.keys())
if len(params) < 2:
    print(f'ERROR: decide() needs 2+ params, got {len(params)}: {params}')
    sys.exit(1)

def assert_decision(label, game_state, analysis):
    result = mod.decide(game_state, analysis)
    if not isinstance(result, dict):
        raise AssertionError(f"{label}: result is not dict: {type(result).__name__}")
    if "x" not in result:
        raise AssertionError(f"{label}: missing x: {result!r}")
    if "reason" not in result:
        raise AssertionError(f"{label}: missing reason: {result!r}")
    x = result["x"]
    if not isinstance(x, (int, float)) or isinstance(x, bool):
        raise AssertionError(f"{label}: x is not numeric: {x!r}")
    if not -3.2 <= float(x) <= 3.2:
        raise AssertionError(f"{label}: x out of range: {x!r}")
    if not isinstance(result["reason"], str) or not result["reason"].strip():
        raise AssertionError(f"{label}: reason is not non-empty string: {result!r}")

empty_analysis_state = {"pieces": [], "next": {"type": 1, "r": 0.25}, "nextNext": {"type": 1, "r": 0.25}, "score": 0}
assert_decision("empty-analysis", empty_analysis_state, {"results": [], "same_type": [], "reactor": {}})

synthetic_state = {
    "pieces": [
        {"id": 1, "type": 1, "x": 0.0, "y": -4.5, "r": 0.25},
        {"id": 2, "type": 2, "x": 1.0, "y": -4.4, "r": 0.3},
    ],
    "next": {"type": 1, "r": 0.25},
    "nextNext": {"type": 2, "r": 0.3},
    "score": 0,
    "deadline_crossed": False,
}
synthetic_analysis = {
    "results": [
        {"x": -1.0, "landing_y": -4.6, "top_y_after_drop": -4.35, "crosses_deadline": False, "merge_grade": "NO", "has_merge": False, "merges": [], "danger_merge_available": False, "danger_direct_merge_available": False},
        {"x": 0.0, "landing_y": -4.2, "top_y_after_drop": -3.95, "crosses_deadline": False, "merge_grade": "DIRECT", "has_merge": True, "merges": [{"id": 1, "grade": "DIRECT", "dist": 0.1, "contact_r": 0.5, "target_is_danger": False}], "danger_merge_available": False, "danger_direct_merge_available": False},
    ],
    "same_type": [{"id": 1, "type": 1, "x": 0.0, "y": -4.5, "r": 0.25}],
    "reactor": {"reactive_pairs": [], "deadline_margin": 7.57, "danger_piece_count": 0},
    "deadline": {"deadline_y": 3.32, "deadline_margin": 7.57, "deadline_crossed": False},
}
assert_decision("synthetic-direct", synthetic_state, synthetic_analysis)

deadline_guard_state = {
    "pieces": [{"id": 1, "type": 8, "x": 0.0, "y": 2.4, "r": 0.5}],
    "next": {"type": 4, "r": 0.6},
    "nextNext": {"type": 5, "r": 0.4},
    "score": 0,
    "deadline_crossed": False,
}
deadline_guard_analysis = {
    "results": [
        {"x": -1.0, "landing_y": 3.1, "top_y_after_drop": 3.2, "crosses_deadline": False, "merge_grade": "NO", "has_merge": False, "merges": [], "danger_merge_available": False, "danger_direct_merge_available": False},
        {"x": 2.8, "landing_y": 2.7, "top_y_after_drop": 3.4, "crosses_deadline": True, "merge_grade": "NO", "has_merge": False, "merges": [], "danger_merge_available": False, "danger_direct_merge_available": False},
    ],
    "same_type": [],
    "reactor": {"reactive_pairs": [], "deadline_margin": 0.2, "danger_piece_count": 0},
}
deadline_guard_result = mod.decide(deadline_guard_state, deadline_guard_analysis)
if float(deadline_guard_result["x"]) != -1.0:
    raise AssertionError(f"deadline-guard: expected safe non-crossing x=-1.0, got {deadline_guard_result!r}")

deadline_far_guard_analysis = {
    "results": [
        {"x": -1.0, "landing_y": 3.1, "top_y_after_drop": 3.2, "crosses_deadline": False, "merge_grade": "NO", "has_merge": False, "merges": [], "danger_merge_available": False, "danger_direct_merge_available": False},
        {"x": 2.8, "landing_y": 2.7, "top_y_after_drop": 3.4, "crosses_deadline": True, "merge_grade": "FAR", "has_merge": True, "merges": [{"id": 1, "grade": "FAR", "dist": 0.9, "contact_r": 0.5, "target_is_danger": False}], "danger_merge_available": False, "danger_direct_merge_available": False},
    ],
    "same_type": [{"id": 1, "type": 4, "x": 0.0, "y": 2.4, "r": 0.5}],
    "reactor": {"reactive_pairs": [], "deadline_margin": 0.2, "danger_piece_count": 0},
}
deadline_far_guard_result = mod.decide(deadline_guard_state, deadline_far_guard_analysis)
if float(deadline_far_guard_result["x"]) != -1.0:
    raise AssertionError(f"deadline-far-guard: expected safe non-crossing x=-1.0, got {deadline_far_guard_result!r}")

deadline_near_guard_analysis = {
    "results": [
        {"x": -1.0, "landing_y": 3.1, "top_y_after_drop": 3.2, "crosses_deadline": False, "merge_grade": "NO", "has_merge": False, "merges": [], "danger_merge_available": False, "danger_direct_merge_available": False},
        {"x": 2.8, "landing_y": 2.7, "top_y_after_drop": 3.4, "crosses_deadline": True, "merge_grade": "NEAR", "has_merge": True, "merges": [{"id": 1, "grade": "NEAR", "dist": 0.4, "contact_r": 0.5, "target_is_danger": False}], "danger_merge_available": False, "danger_direct_merge_available": False},
    ],
    "same_type": [{"id": 1, "type": 4, "x": 0.0, "y": 2.4, "r": 0.5}],
    "reactor": {"reactive_pairs": [], "deadline_margin": 0.2, "danger_piece_count": 0},
}
deadline_near_guard_result = mod.decide(deadline_guard_state, deadline_near_guard_analysis)
if float(deadline_near_guard_result["x"]) != 2.8:
    raise AssertionError(f"deadline-near-guard: expected crossing NEAR merge x=2.8, got {deadline_near_guard_result!r}")

deadline_direct_guard_analysis = {
    "results": [
        {"x": -1.0, "landing_y": 3.1, "top_y_after_drop": 3.2, "crosses_deadline": False, "merge_grade": "NO", "has_merge": False, "merges": [], "danger_merge_available": False, "danger_direct_merge_available": False},
        {"x": 2.8, "landing_y": 2.7, "top_y_after_drop": 3.4, "crosses_deadline": True, "merge_grade": "DIRECT", "has_merge": True, "merges": [{"id": 1, "grade": "DIRECT", "dist": 0.1, "contact_r": 0.5, "target_is_danger": False}], "danger_merge_available": False, "danger_direct_merge_available": False},
    ],
    "same_type": [{"id": 1, "type": 4, "x": 0.0, "y": 2.4, "r": 0.5}],
    "reactor": {"reactive_pairs": [], "deadline_margin": 0.2, "danger_piece_count": 0},
}
deadline_direct_guard_result = mod.decide(deadline_guard_state, deadline_direct_guard_analysis)
if float(deadline_direct_guard_result["x"]) != 2.8:
    raise AssertionError(f"deadline-direct-guard: expected crossing DIRECT merge x=2.8, got {deadline_direct_guard_result!r}")

reactive_far_below_analysis = {
    "results": [
        {"x": -1.0, "landing_y": -1.2, "top_y_after_drop": -0.8, "crosses_deadline": False, "merge_grade": "NO", "has_merge": False, "merges": [], "danger_merge_available": False, "danger_direct_merge_available": False},
        {"x": 2.8, "landing_y": -1.4, "top_y_after_drop": -0.9, "crosses_deadline": False, "merge_grade": "NO", "has_merge": False, "merges": [], "danger_merge_available": False, "danger_direct_merge_available": False},
    ],
    "same_type": [],
    "reactor": {"reactive_pairs": [(1, 2, 2), (3, 4, 4), (5, 6, 6)], "deadline_margin": 4.2, "danger_piece_count": 0},
}
reactive_far_below_result = mod.decide(deadline_guard_state, reactive_far_below_analysis)
if "DEADLINE_GUARD" in str(reactive_far_below_result.get("reason", "")):
    raise AssertionError(f"reactive-far-below: deadline guard must not fire far below red line, got {reactive_far_below_result!r}")

all_crossing_far_below_penalty_state = {
    "pieces": [{"id": i, "type": (i % 7) + 1, "x": -2.8 + (i % 8) * 0.8, "y": -3.8 + (i // 8) * 0.45, "r": 0.35} for i in range(32)],
    "next": {"type": 9, "r": 1.0},
    "nextNext": {"type": 5, "r": 0.5},
    "score": 1000,
    "deadline_crossed": False,
}
all_crossing_far_below_penalty_analysis = {
    "results": [
        {"x": -1.0, "landing_y": 2.5, "top_y_after_drop": 3.5, "risk_top_y_after_drop": 3.5, "crosses_deadline": True, "merge_grade": "NO", "has_merge": False, "merges": [], "danger_merge_available": False, "danger_direct_merge_available": False},
        {"x": 1.0, "landing_y": 2.6, "top_y_after_drop": 3.6, "risk_top_y_after_drop": 3.6, "crosses_deadline": True, "merge_grade": "NO", "has_merge": False, "merges": [], "danger_merge_available": False, "danger_direct_merge_available": False},
    ],
    "same_type": [],
    "reactor": {"reactive_pairs": [(1, 2, 2)], "deadline_margin": 1.2, "top_edge_y": 2.18, "danger_piece_count": 0},
    "deadline": {"deadline_y": 3.38, "deadline_margin": 1.2, "deadline_crossed": False},
}
all_crossing_far_below_penalty_result = mod.decide(all_crossing_far_below_penalty_state, all_crossing_far_below_penalty_analysis)
if "CROSSES_DEADLINE_NO_MERGE" in str(all_crossing_far_below_penalty_result.get("reason", "")):
    raise AssertionError(f"all-crossing-far-below: raw crossing should not add deadline penalty, got {all_crossing_far_below_penalty_result!r}")
if "MERGE_DROUGHT_DEADLINE_CROSS_PENALTY" in str(all_crossing_far_below_penalty_result.get("reason", "")):
    raise AssertionError(f"all-crossing-far-below: raw crossing should not add merge-drought deadline penalty, got {all_crossing_far_below_penalty_result!r}")

urgent_direct_state = {
    "pieces": [{"id": i, "type": (i % 8) + 1, "x": -2.8 + (i % 8) * 0.7, "y": -3.8 + (i // 8) * 0.55, "r": 0.35} for i in range(32)],
    "next": {"type": 4, "r": 0.38},
    "nextNext": {"type": 9, "r": 0.75},
    "score": 1000,
    "deadline_crossed": False,
}
urgent_direct_analysis = {
    "results": [
        {"x": 1.0, "landing_y": 0.4, "top_y_after_drop": 0.8, "risk_top_y_after_drop": 0.8, "crosses_deadline": False, "merge_grade": "NO", "has_merge": False, "merges": [], "danger_merge_available": False, "danger_direct_merge_available": False},
        {"x": -2.5, "landing_y": 1.5, "top_y_after_drop": 1.9, "risk_top_y_after_drop": 1.9, "crosses_deadline": False, "merge_grade": "DIRECT", "has_merge": True, "merges": [{"id": 3, "grade": "DIRECT", "dist": 0.1, "contact_r": 0.76, "target_is_danger": False}], "danger_merge_available": False, "danger_direct_merge_available": False},
    ],
    "same_type": [{"id": 3, "type": 4, "x": -2.5, "y": 1.1, "r": 0.38}],
    "reactor": {"reactive_pairs": [(1, 2, 2), (3, 4, 4), (5, 6, 6)], "deadline_margin": 1.4, "top_edge_y": 2.0, "danger_piece_count": 0},
    "deadline": {"deadline_y": 3.32, "deadline_margin": 1.4, "deadline_crossed": False},
}
urgent_direct_result = mod.decide(urgent_direct_state, urgent_direct_analysis)
if float(urgent_direct_result["x"]) != -2.5:
    raise AssertionError(f"urgent-direct: expected safe DIRECT x=-2.5, got {urgent_direct_result!r}")

cascade_state = {
    "pieces": [
        {"id": 10, "type": 4, "x": 0.0, "y": -1.15, "r": 0.38},
        {"id": 11, "type": 5, "x": 0.03, "y": -1.85, "r": 0.5},
        {"id": 12, "type": 8, "x": -2.2, "y": -2.6, "r": 0.66},
    ],
    "next": {"type": 4, "r": 0.38},
    "nextNext": {"type": 2, "r": 0.25},
    "score": 1000,
    "deadline_crossed": False,
}
cascade_analysis = {
    "results": [
        {"x": -2.2, "landing_y": -2.9, "top_y_after_drop": -2.52, "risk_top_y_after_drop": -2.52, "crosses_deadline": False, "merge_grade": "NO", "has_merge": False, "merges": [], "danger_merge_available": False, "danger_direct_merge_available": False},
        {"x": 0.0, "landing_y": -1.15, "top_y_after_drop": -0.77, "risk_top_y_after_drop": -0.77, "crosses_deadline": False, "merge_grade": "DIRECT", "has_merge": True, "merges": [{"id": 10, "x": 0.0, "y": -1.15, "r": 0.38, "grade": "DIRECT", "dist": 0.1, "contact_r": 0.76, "target_is_danger": False}], "danger_merge_available": False, "danger_direct_merge_available": False},
    ],
    "same_type": [{"id": 10, "type": 4, "x": 0.0, "y": -1.15, "r": 0.38}],
    "reactor": {"reactive_pairs": [], "deadline_margin": 4.0, "top_edge_y": -0.77, "danger_piece_count": 0},
    "deadline": {"deadline_y": 3.32, "deadline_margin": 4.0, "deadline_crossed": False},
}
cascade_result = mod.decide(cascade_state, cascade_analysis)
if float(cascade_result["x"]) != 0.0:
    raise AssertionError(f"cascade-direct: expected x=0.0 for A-1->A->A chain, got {cascade_result!r}")

endgame_min_risk_state = {
    "pieces": [{"id": i, "type": (i % 7) + 1, "x": -2.8 + (i % 8) * 0.8, "y": -3.8 + (i // 8) * 0.55, "r": 0.35} for i in range(40)],
    "next": {"type": 1, "r": 0.207},
    "nextNext": {"type": 5, "r": 0.414},
    "score": 1000,
    "deadline_crossed": False,
}
endgame_min_risk_analysis = {
    "results": [
        {"x": -1.3, "landing_y": -0.64, "top_y_after_drop": -0.43, "risk_top_y_after_drop": -0.43, "crosses_deadline": False, "merge_grade": "NO", "has_merge": False, "merges": [], "danger_merge_available": False, "danger_direct_merge_available": False},
        {"x": 1.3, "landing_y": -0.25, "top_y_after_drop": -0.04, "risk_top_y_after_drop": -0.04, "crosses_deadline": False, "merge_grade": "NO", "has_merge": False, "merges": [], "danger_merge_available": False, "danger_direct_merge_available": False},
    ],
    "same_type": [],
    "reactor": {"reactive_pairs": [], "deadline_margin": 0.8, "top_edge_y": 2.55, "danger_piece_count": 0},
    "deadline": {"deadline_y": 3.32, "deadline_margin": 0.8, "deadline_crossed": False},
}
endgame_min_risk_result = mod.decide(endgame_min_risk_state, endgame_min_risk_analysis)
if float(endgame_min_risk_result["x"]) != -1.3:
    raise AssertionError(f"endgame-min-risk: expected lowest-risk NO x=-1.3, got {endgame_min_risk_result!r}")

active_filter_state = {
    "pieces": [{"id": 1, "type": 5, "x": 0.0, "y": 2.8, "r": 0.6}],
    "next": {"type": 5, "r": 0.6},
    "nextNext": {"type": 1, "r": 0.2},
    "score": 0,
    "deadline_crossed": True,
}
active_filter_analysis = {
    "results": [
        {"x": -1.0, "landing_y": 2.0, "top_y_after_drop": 2.6, "risk_top_y_after_drop": 2.6, "crosses_deadline": False, "merge_grade": "NO", "has_merge": False, "merges": [], "danger_merge_available": False, "danger_direct_merge_available": False},
        {"x": 0.0, "landing_y": 3.5, "top_y_after_drop": 4.1, "risk_top_y_after_drop": 4.1, "crosses_deadline": True, "merge_grade": "DIRECT", "has_merge": True, "merges": [{"id": 1, "grade": "DIRECT", "dist": 0.1, "contact_r": 1.2, "target_is_danger": True}], "danger_merge_available": True, "danger_direct_merge_available": True},
    ],
    "same_type": [{"id": 1, "type": 5, "x": 0.0, "y": 2.8, "r": 0.6}],
    "reactor": {"reactive_pairs": [(1, 2, 5), (3, 4, 6), (5, 6, 7)], "deadline_margin": -0.1, "top_edge_y": 3.4, "danger_piece_count": 1},
    "deadline": {"deadline_y": 3.32, "deadline_margin": -0.1, "deadline_crossed": True},
}
active_filter_result = mod.decide(active_filter_state, active_filter_analysis)
if float(active_filter_result["x"]) != 0.0:
    raise AssertionError(f"active-filter: expected danger DIRECT merge x=0.0, got {active_filter_result!r}")

risky_single_danger_merge_analysis = {
    "results": [
        {"x": -1.0, "landing_y": 2.0, "top_y_after_drop": 2.6, "risk_top_y_after_drop": 2.6, "crosses_deadline": False, "merge_grade": "NO", "has_merge": False, "merges": [], "danger_merge_available": False, "danger_direct_merge_available": False},
        {"x": 0.0, "landing_y": 3.35, "top_y_after_drop": 3.95, "risk_top_y_after_drop": 4.3, "merge_result_top_y": 4.3, "merge_result_crosses_deadline": True, "crosses_deadline": True, "merge_grade": "DIRECT", "has_merge": True, "merges": [{"id": 1, "grade": "DIRECT", "dist": 0.1, "contact_r": 1.2, "target_is_danger": True}], "danger_merge_available": True, "danger_direct_merge_available": True},
    ],
    "same_type": [{"id": 1, "type": 5, "x": 0.0, "y": 2.8, "r": 0.6}],
    "reactor": {"reactive_pairs": [(1, 2, 5)], "deadline_margin": -0.1, "top_edge_y": 3.4, "danger_piece_count": 1},
    "deadline": {"deadline_y": 3.32, "deadline_margin": -0.1, "deadline_crossed": True},
}
risky_single_danger_merge_result = mod.decide(active_filter_state, risky_single_danger_merge_analysis)
if float(risky_single_danger_merge_result["x"]) != -1.0:
    raise AssertionError(f"risky-single-danger-merge: expected safe x=-1.0, got {risky_single_danger_merge_result!r}")

non_danger_risky_merge_analysis = {
    "results": [
        {"x": -1.0, "landing_y": 2.0, "top_y_after_drop": 2.6, "risk_top_y_after_drop": 2.6, "merge_result_crosses_deadline": False, "crosses_deadline": False, "merge_grade": "NO", "has_merge": False, "merges": [], "danger_merge_available": False, "danger_direct_merge_available": False},
        {"x": 0.0, "landing_y": 3.05, "top_y_after_drop": 3.2, "risk_top_y_after_drop": 3.2, "merge_result_top_y": 4.0, "merge_result_crosses_deadline": True, "crosses_deadline": False, "merge_grade": "DIRECT", "has_merge": True, "merges": [{"id": 1, "grade": "DIRECT", "dist": 0.1, "contact_r": 1.4, "target_is_danger": False}], "danger_merge_available": False, "danger_direct_merge_available": False},
    ],
    "same_type": [{"id": 1, "type": 5, "x": 0.0, "y": 2.4, "r": 0.7}],
    "reactor": {"reactive_pairs": [(1, 2, 5)], "deadline_margin": -0.1, "top_edge_y": 3.4, "danger_piece_count": 0},
    "deadline": {"deadline_y": 3.32, "deadline_margin": -0.1, "deadline_crossed": True},
}
non_danger_risky_merge_result = mod.decide(active_filter_state, non_danger_risky_merge_analysis)
if float(non_danger_risky_merge_result["x"]) != -1.0:
    raise AssertionError(f"non-danger-risky-merge: expected clean x=-1.0, got {non_danger_risky_merge_result!r}")

no_clean_risky_merge_analysis = {
    "results": [
        {"x": -1.0, "landing_y": 2.8, "top_y_after_drop": 3.34, "risk_top_y_after_drop": 3.34, "merge_result_crosses_deadline": False, "crosses_deadline": True, "merge_grade": "NO", "has_merge": False, "merges": [], "danger_merge_available": False, "danger_direct_merge_available": False},
        {"x": 0.0, "landing_y": 3.05, "top_y_after_drop": 3.2, "risk_top_y_after_drop": 3.2, "merge_result_top_y": 4.0, "merge_result_crosses_deadline": True, "crosses_deadline": False, "merge_grade": "DIRECT", "has_merge": True, "merges": [{"id": 1, "grade": "DIRECT", "dist": 0.1, "contact_r": 1.4, "target_is_danger": False}], "danger_merge_available": False, "danger_direct_merge_available": False},
    ],
    "same_type": [{"id": 1, "type": 5, "x": 0.0, "y": 2.4, "r": 0.7}],
    "reactor": {"reactive_pairs": [(1, 2, 5)], "deadline_margin": -0.1, "top_edge_y": 3.4, "danger_piece_count": 0},
    "deadline": {"deadline_y": 3.32, "deadline_margin": -0.1, "deadline_crossed": True},
}
no_clean_risky_merge_result = mod.decide(active_filter_state, no_clean_risky_merge_analysis)
if float(no_clean_risky_merge_result["x"]) != 0.0:
    raise AssertionError(f"no-clean-risky-merge: expected merge x=0.0, got {no_clean_risky_merge_result!r}")

all_crossing_analysis = {
    "results": [
        {"x": -1.0, "landing_y": 2.8, "top_y_after_drop": 3.34, "risk_top_y_after_drop": 3.34, "crosses_deadline": True, "merge_grade": "NO", "has_merge": False, "merges": [], "danger_merge_available": False, "danger_direct_merge_available": False},
        {"x": 0.0, "landing_y": 3.5, "top_y_after_drop": 4.1, "risk_top_y_after_drop": 4.1, "crosses_deadline": True, "merge_grade": "DIRECT", "has_merge": True, "merges": [{"id": 1, "grade": "DIRECT", "dist": 0.1, "contact_r": 1.2, "target_is_danger": True}], "danger_merge_available": True, "danger_direct_merge_available": True},
    ],
    "same_type": [{"id": 1, "type": 5, "x": 0.0, "y": 2.8, "r": 0.6}],
    "reactor": {"reactive_pairs": [(1, 2, 5), (3, 4, 6), (5, 6, 7)], "deadline_margin": -0.1, "top_edge_y": 3.4, "danger_piece_count": 1},
    "deadline": {"deadline_y": 3.32, "deadline_margin": -0.1, "deadline_crossed": True},
}
all_crossing_result = mod.decide(active_filter_state, all_crossing_analysis)
# 方針変更 (commit 89129ada8b): 両候補が deadline を超えていても merge を温存する。
# 危険盤面で merge を捨てて min-risk no-merge を選ぶより、merge を温存して進化を進める方が良い。
# (試合速度的に「次手が危ないけど今手で type を上げて消化」の方が期待値が高い設計)
if float(all_crossing_result["x"]) != 0.0:
    raise AssertionError(f"all-crossing-merge-preserved: expected merge-preserved x=0.0, got {all_crossing_result!r}")
print(f'OK: decide({", ".join(params)})')
PYEOF
	)
	if [ $? -ne 0 ]; then
		VALIDATE_ERROR="decide()シグネチャチェック失敗: $sig_out"
		log "[VALIDATE] $VALIDATE_ERROR"
		return 1
	fi

	if [ -f "$GAME_STATE" ]; then
		local test_out
		test_out=$(python3 "$target_file" "$GAME_STATE" 2>&1)
		if [ $? -ne 0 ]; then
			VALIDATE_ERROR="テスト実行失敗: $test_out"
			log "[VALIDATE] $VALIDATE_ERROR"
			return 1
		fi
		if ! echo "$test_out" | python3 -c "import json,sys; d=json.load(sys.stdin); assert 'x' in d" 2>/dev/null; then
			VALIDATE_ERROR="テスト出力にxフィールドなし: $test_out"
			log "[VALIDATE] $VALIDATE_ERROR"
			return 1
		fi
		if ! echo "$test_out" | python3 -c "import json,sys; d=json.load(sys.stdin); assert isinstance(d.get('reason'), str) and d.get('reason').strip(); x=d.get('x'); assert isinstance(x,(int,float)) and not isinstance(x,bool) and -3.2 <= float(x) <= 3.2" 2>/dev/null; then
			VALIDATE_ERROR="テスト出力契約違反: $test_out"
			log "[VALIDATE] $VALIDATE_ERROR"
			return 1
		fi
		log "[VALIDATE] テスト実行OK"
	fi

	return 0
}

_realpath_safe() {
	python3 - "$1" <<'PY'
import os
import sys

path = sys.argv[1] if len(sys.argv) > 1 else ""
if not path:
    raise SystemExit(1)
print(os.path.realpath(path))
PY
}

_path_is_under_dir() {
	local path="$1" base="$2"
	local rp rb
	rp=$(_realpath_safe "$path" 2>/dev/null) || return 1
	rb=$(_realpath_safe "$base" 2>/dev/null) || return 1
	[ "$rp" = "$rb" ] && return 0
	case "$rp" in
	"$rb"/*) return 0 ;;
	*) return 1 ;;
	esac
}

create_sandbox() {
	local sandbox_dir
	mkdir -p "$ELOOP_LIB_DIR/tmp" 2>/dev/null || true
	sandbox_dir=$(mktemp -d "$ELOOP_LIB_DIR/tmp/.soren_sandbox_XXXXXX" 2>/dev/null) || {
		log "[SANDBOX] 作成失敗"
		return 1
	}

	local src dst
	for src in "$@"; do
		[ -n "$src" ] || continue
		[ -e "$src" ] || continue
		[ -L "$src" ] && continue
		# ../を含むパスはsandbox外参照の危険があるため拒否
		case "$src" in
		../* | */../* | */..)
			log "[SANDBOX] パス拒否 (..含む): $src"
			continue
			;;
		esac
		dst="$sandbox_dir/$src"
		mkdir -p "$(dirname "$dst")"
		if [ -d "$src" ]; then
			mkdir -p "$dst"
			rsync -a --no-links "$src"/ "$dst"/ 2>/dev/null || cp -RL "$src"/. "$dst"/ 2>/dev/null || true
		else
			cp "$src" "$dst" 2>/dev/null || true
		fi
	done

	# サンドボックス内の改善対象
	if [ ! -f "$sandbox_dir/strategy.py" ] && [ -f "$STRATEGY_FILE" ]; then
		cp "$STRATEGY_FILE" "$sandbox_dir/strategy.py" 2>/dev/null || true
	fi
	if [ -f "$sandbox_dir/strategy.py" ]; then
		cp "$sandbox_dir/strategy.py" "$sandbox_dir/strategy.py.staging" 2>/dev/null || true
	fi

	mkdir -p "$sandbox_dir/strategy_helpers" "$sandbox_dir/logs" "$sandbox_dir/data" "$sandbox_dir/tmp/state"
	if [ -d "strategy_helpers" ]; then
		rsync -a --no-links "strategy_helpers"/ "$sandbox_dir/strategy_helpers"/ 2>/dev/null || cp -RL "strategy_helpers"/. "$sandbox_dir/strategy_helpers"/ 2>/dev/null || true
	fi
	[ -f "$sandbox_dir/strategy_helpers/__init__.py" ] || : >"$sandbox_dir/strategy_helpers/__init__.py"
	[ -f "$sandbox_dir/data/user_review.md" ] || : >"$sandbox_dir/data/user_review.md"
	[ -f "$sandbox_dir/tmp/state/last_rollback_analysis.md" ] || : >"$sandbox_dir/tmp/state/last_rollback_analysis.md"
	[ -f "$sandbox_dir/tmp/state/last_rollback_postmortem.md" ] || : >"$sandbox_dir/tmp/state/last_rollback_postmortem.md"

	if [ -f "logs/change_log.txt" ]; then
		cp "logs/change_log.txt" "$sandbox_dir/logs/change_log.txt" 2>/dev/null || true
	fi

	# opencode が親 git repo にエスケープしないよう、サンドボックスを独立 git repo にする
	(cd "$sandbox_dir" && git init -q && git add -A && git commit -q -m "sandbox init" --no-gpg-sign) >/dev/null 2>&1 || true

	echo "$sandbox_dir"
}

harvest_sandbox() {
	local sandbox_dir="$1"
	[ -n "$sandbox_dir" ] || return 1
	[ -d "$sandbox_dir" ] || return 1

	local sandbox_real
	sandbox_real=$(_realpath_safe "$sandbox_dir" 2>/dev/null) || return 1
	if ! _path_is_under_dir "$sandbox_real" "$ELOOP_LIB_DIR/tmp"; then
		log "[SANDBOX] harvest拒否: 不正なsandboxパス $sandbox_real"
		return 1
	fi
	case "$(basename "$sandbox_real")" in
	.soren_sandbox_*) ;;
	*)
		log "[SANDBOX] harvest拒否: sandbox名が不正 $sandbox_real"
		return 1
		;;
	esac

	local harvest_dir
	harvest_dir=$(mktemp -d "$ELOOP_LIB_DIR/tmp/.sandbox_harvest_XXXXXX" 2>/dev/null) || return 1

	if [ -f "$sandbox_dir/strategy.py.staging" ]; then
		rsync -a --no-links "$sandbox_dir/strategy.py.staging" "$harvest_dir/" 2>/dev/null || cp "$sandbox_dir/strategy.py.staging" "$harvest_dir/" 2>/dev/null || {
			rm -rf "$harvest_dir" 2>/dev/null
			return 1
		}
	fi

	if [ -f "$sandbox_dir/logs/change_log.txt" ] && [ -s "$sandbox_dir/logs/change_log.txt" ]; then
		mkdir -p "$harvest_dir/logs" 2>/dev/null || true
		cp "$sandbox_dir/logs/change_log.txt" "$harvest_dir/logs/change_log.txt" 2>/dev/null || true
	fi

	if [ -d "$sandbox_dir/strategy_helpers" ]; then
		mkdir -p "$harvest_dir/strategy_helpers"
		rsync -a --no-links "$sandbox_dir/strategy_helpers"/ "$harvest_dir/strategy_helpers"/ 2>/dev/null ||
			cp -RL "$sandbox_dir/strategy_helpers"/. "$harvest_dir/strategy_helpers"/ 2>/dev/null || true
	fi

	if find "$harvest_dir" -type l 2>/dev/null | grep -q .; then
		log "[SANDBOX] harvest拒否: symlink混入を検出"
		rm -rf "$harvest_dir" 2>/dev/null
		return 1
	fi

	if find "$harvest_dir" -type f -links +1 2>/dev/null | grep -q .; then
		log "[SANDBOX] harvest拒否: hard link検出"
		rm -rf "$harvest_dir" 2>/dev/null
		return 1
	fi

	if ! _path_is_under_dir "$harvest_dir" "$ELOOP_LIB_DIR/tmp"; then
		log "[SANDBOX] harvest拒否: 不正なharvestパス"
		rm -rf "$harvest_dir" 2>/dev/null
		return 1
	fi

	echo "$harvest_dir"
}

destroy_sandbox() {
	local sandbox_dir="$1"
	[ -n "$sandbox_dir" ] || return 0
	[ -e "$sandbox_dir" ] || return 0

	local sandbox_real
	sandbox_real=$(_realpath_safe "$sandbox_dir" 2>/dev/null) || return 1
	if ! _path_is_under_dir "$sandbox_real" "$ELOOP_LIB_DIR/tmp"; then
		log "[SANDBOX] destroy拒否: 不正なsandboxパス $sandbox_real"
		return 1
	fi
	case "$(basename "$sandbox_real")" in
	.soren_sandbox_*)
		rm -rf "$sandbox_real" 2>/dev/null || return 1
		;;
	*)
		log "[SANDBOX] destroy拒否: sandbox名が不正 $sandbox_real"
		return 1
		;;
	esac
}

check_host_integrity() {
	local before_file="$1"
	[ -f "$before_file" ] || return 0

	local after_file before_sorted after_sorted
	after_file=$(mktemp /tmp/eloop_host_after.XXXXXX) || return 0
	before_sorted=$(mktemp /tmp/eloop_host_before_sorted.XXXXXX) || {
		rm -f "$after_file"
		return 0
	}
	after_sorted=$(mktemp /tmp/eloop_host_after_sorted.XXXXXX) || {
		rm -f "$after_file" "$before_sorted"
		return 0
	}

	_write_host_integrity_snapshot "$after_file" || true
	sort "$before_file" >"$before_sorted" 2>/dev/null || true
	sort "$after_file" >"$after_sorted" 2>/dev/null || true

	local added_lines host_changed=false
	added_lines=$(comm -13 "$before_sorted" "$after_sorted" 2>/dev/null || true)
	if [ -n "$added_lines" ]; then
		log "[SANDBOX] WARNING: AI改善中にapply対象ファイルのホスト変化を検出"
		printf '%s\n' "$added_lines" | head -20 | while read -r line; do
			[ -n "$line" ] && log "[SANDBOX] host_change: $line"
		done
		host_changed=true
	fi

	rm -f "$after_file" "$before_sorted" "$after_sorted"
	$host_changed && return 1 || return 0
}

_write_host_integrity_snapshot() {
	local out_file="$1"
	[ -n "$out_file" ] || return 1
	{
		echo "## git-status"
		git status --porcelain -- "$STRATEGY_FILE" strategy_helpers 2>/dev/null || true
		echo "## file-hashes"
		if [ -f "$STRATEGY_FILE" ]; then
			shasum "$STRATEGY_FILE" 2>/dev/null || true
		fi
		if [ -d strategy_helpers ]; then
			find strategy_helpers -type f ! -name '.DS_Store' -print 2>/dev/null | sort | while IFS= read -r _host_file; do
				shasum "$_host_file" 2>/dev/null || true
			done
		fi
	} >"$out_file"
}

validate_strategy_with_helpers() {
	local target_file="$1"
	local helpers_dir="${2:-strategy_helpers}"
	if ! validate_strategy "$target_file" "$helpers_dir"; then
		return 1
	fi

	if [ -d "$helpers_dir" ]; then
		if find "$helpers_dir" -type l 2>/dev/null | grep -q .; then
			VALIDATE_ERROR="strategy_helpers に symlink が含まれる"
			log "[VALIDATE] $VALIDATE_ERROR"
			return 1
		fi
		if [ ! -f "$helpers_dir/__init__.py" ]; then
			VALIDATE_ERROR="strategy_helpers/__init__.py が不足"
			log "[VALIDATE] $VALIDATE_ERROR"
			return 1
		fi

		local helper_out
		helper_out=$(
			python3 - "$helpers_dir" <<'PYEOF' 2>&1
import os
import sys

helpers = sys.argv[1]
if not os.path.isdir(helpers):
    print("OK: no helpers dir")
    raise SystemExit(0)

checked = 0
for root, _, files in os.walk(helpers):
    for fn in files:
        if not fn.endswith(".py"):
            continue
        path = os.path.join(root, fn)
        with open(path, "r", encoding="utf-8") as f:
            src = f.read()
        compile(src, path, "exec")
        checked += 1

print(f"OK: helper syntax files={checked}")
PYEOF
		)
		if [ $? -ne 0 ]; then
			VALIDATE_ERROR="strategy_helpers 構文検証失敗: $helper_out"
			log "[VALIDATE] $VALIDATE_ERROR"
			return 1
		fi
		log "[VALIDATE] strategy_helpers 検証OK"
	fi

	return 0
}
