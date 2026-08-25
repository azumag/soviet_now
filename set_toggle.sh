#!/usr/bin/env bash
# set_toggle.sh - 帯域脱出機構 (D + E + F) のトグルを .env で安全に切替
#
# 使用例:
#   ./set_toggle.sh DIVERSITY_PREMIUM_ENABLED=1
#   ./set_toggle.sh TABU_ENABLED=1 WILDCARD_ENABLED=1
#   ./set_toggle.sh status                     # 現在値を表示
#
# 効果は次の reload_runtime_toggles 呼び出し (≈ 10秒以内) で反映される。
# soren_loop.sh の再起動は不要。

set -euo pipefail

ENV_FILE=".env"

ALLOWED_KEYS=(
	# 帯域脱出機構 D / E / F
	DIVERSITY_PREMIUM_ENABLED
	DIVERSITY_PREMIUM_WEIGHT
	EXPLORE_GAP_MAX_RATIO
	TABU_ENABLED
	TABU_DISTANCE_THRESHOLD
	TABU_RETAIN
	TABU_DECAY_GAMES
	WILDCARD_ENABLED
	WILDCARD_TRIGGER_STAGNATION
	WILDCARD_PARAM_COUNT_MIN
	WILDCARD_PARAM_COUNT_MAX
	WILDCARD_PERTURB_RATIO_MIN
	WILDCARD_PERTURB_RATIO_MAX
	WILDCARD_PATIENCE_GAMES
	REGRESSION_DISABLED
	OBJECTIVE_ANCHOR_PRIORITY_ENABLED
	# サイクル長・粛清閾値
	MIN_GAMES_BEFORE_IMPROVE
	MIN_GAMES_BEFORE_REGRESSION
	MIN_GAMES_FOR_BEST_ROLLBACK
	BRANCH_MAX_DEPTH
	BRANCH_MAX_GAMES
	BRANCH_PATIENCE
	BRANCH_HARD_COMP_GAP
	BRANCH_HARD_P50_GAP
	BRANCH_HARD_P25_GAP
	BRANCH_HARD_MIN_BREACH_COUNT
	REGRESSION_MIN_COMP_GAP
	REGRESSION_MIN_P50_GAP
	REGRESSION_MIN_P25_GAP
	REGRESSION_MIN_BREACH_COUNT
	EARLY_COMP_TOP_GAP_ENABLED
	EARLY_COMP_TOP_GAP_MIN_GAMES
	EARLY_COMP_TOP_GAP_MIN_RATIO
	STAGE_ACHIEVEMENT_REGRESSION_ENABLED
	STAGE_GATE_STAT_ENABLED
	STAGE_GATE_NONINFERIOR_GRACE
	ANALYZE_BOARD_VERTICAL_LANE_DIRECT
	ANALYZE_BOARD_MERGE_TOP_MODEL
	ANALYZE_BOARD_WALL_CLAMP
	SOREN_SETTLE_REQUIRED
	SOREN_SETTLE_MAX_SPEED2
	SOREN_SETTLE_MAX_AWAKE
	STAGE_ACHIEVEMENT_REGRESSION_MIN_GAMES
	STAGE_ACHIEVEMENT_GATE_MIN_RATE
	STAGE_ACHIEVEMENT_GATE_TYPES
	# Dashboard
	DASHBOARD_SHOW_WHILE_PLAYING
	DASHBOARD_CHART_GAMES
	OBS_DASHBOARD_VISIBILITY_ENABLED
	OBS_DASHBOARD_SCENE
	OBS_DASHBOARD_SOURCE
	MODEL_IMPROVE
	MODEL_IMPROVE_LIST
	MODEL_IMPROVE_PEAK_LIST
	IMPROVE_PEAK_CHAIN_ENABLED
	# ピーク時間帯回避 (改善ロック)
	IMPROVE_PEAK_HOUR_DEFER_ENABLED
	IMPROVE_PEAK_HOUR_UTC_RANGES
	IMPROVE_PEAK_DEFER_MAX_WAIT_SEC
	IMPROVE_PEAK_DEFER_FORCE_ACC_PCT
	# ピーク時間帯のエージェント優先順位入替え (ラジオ/コメント生成)
	PEAK_HOURS_AGENT_SWAP_ENABLED
	PEAK_HOURS_WINDOWS
	PEAK_HOURS_TZ
	PEAK_HOURS_PRIORITY_AGENT
	PEAK_HOURS_QUEUE_GATE_ENABLED
)

is_allowed() {
	local k="$1"
	for a in "${ALLOWED_KEYS[@]}"; do
		[ "$a" = "$k" ] && return 0
	done
	return 1
}

print_status() {
	echo "=== 帯域脱出機構トグル (現在値) ==="
	for k in "${ALLOWED_KEYS[@]}"; do
		v=$(grep -E "^${k}=" "$ENV_FILE" 2>/dev/null | tail -1 | cut -d= -f2- || true)
		if [ -z "$v" ]; then
			echo "  $k = (default / not in .env)"
		else
			echo "  $k = $v"
		fi
	done
	echo
	echo "=== ランタイム実体 (現プロセス) ==="
	echo "  DIVERSITY_PREMIUM_ENABLED=${DIVERSITY_PREMIUM_ENABLED:-?}"
	echo "  TABU_ENABLED=${TABU_ENABLED:-?}"
	echo "  WILDCARD_ENABLED=${WILDCARD_ENABLED:-?}"
}

if [ $# -eq 0 ] || [ "$1" = "-h" ] || [ "$1" = "--help" ]; then
	cat <<EOF
usage: $0 KEY=VALUE [KEY=VALUE ...]
       $0 status

Allowed keys:
  ${ALLOWED_KEYS[*]}

Effects take effect at the next reload_runtime_toggles call (≈ 10s within
running soren_loop.sh). No restart required.
EOF
	exit 0
fi

if [ "$1" = "status" ]; then
	print_status
	exit 0
fi

[ -f "$ENV_FILE" ] || : >"$ENV_FILE"

# バックアップ
cp "$ENV_FILE" "${ENV_FILE}.bak.$(date +%s)"

ts=$(date +%s)
for arg in "$@"; do
	case "$arg" in
	*=*) ;;
	*)
		echo "[ERROR] invalid form: $arg (expected KEY=VALUE)" >&2
		exit 2
		;;
	esac
	key="${arg%%=*}"
	val="${arg#*=}"
	if ! is_allowed "$key"; then
		echo "[ERROR] key not in allowlist: $key" >&2
		echo "        allowed: ${ALLOWED_KEYS[*]}" >&2
		exit 2
	fi
	# 既存行を削除して新規追記 (重複防止)
	if grep -qE "^${key}=" "$ENV_FILE"; then
		# sed -i は GNU/BSD 互換のため tmp file 経由
		tmp=$(mktemp)
		grep -v -E "^${key}=" "$ENV_FILE" >"$tmp"
		mv "$tmp" "$ENV_FILE"
	fi
	printf '%s=%s\n' "$key" "$val" >>"$ENV_FILE"
	echo "[set_toggle] $key=$val"
done

# .env mtime を更新 (touch だけで reload trigger になる)
touch "$ENV_FILE"

echo
echo "Done. soren_loop.sh が起動中なら次の改善・regression 判定で自動反映される。"
echo "即時確認したい場合: ./set_toggle.sh status"
