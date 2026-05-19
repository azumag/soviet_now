# core/runtime_toggles.sh - .env の hot-reload (帯域脱出機構トグル用)
#
# 各エントリポイントで `reload_runtime_toggles` を呼ぶと、
# .env の mtime をチェックし、変わっていればホワイトリスト対象の env var を再 export する。
# soren_loop.sh を再起動せずに DIVERSITY_PREMIUM_ENABLED 等を切り替えられる。
#
# 安全のためホワイトリスト方式: 帯域脱出機構と REGRESSION_DISABLED のみ対象。
# シークレット (API_KEY 等) や TWITCH_* には触らない。

_RUNTIME_TOGGLES_LAST_MTIME=""
_RUNTIME_TOGGLES_LAST_CHECKED=0

# ホワイトリスト (空白区切り)
_RUNTIME_TOGGLES_KEYS=$(cat <<'EOF'
DIVERSITY_PREMIUM_ENABLED
DIVERSITY_PREMIUM_WEIGHT
EXPLORE_GAP_MAX_RATIO
TABU_ENABLED
TABU_DISTANCE_THRESHOLD
TABU_RETAIN
TABU_DECAY_GAMES
WILDCARD_ENABLED
WILDCARD_TRIGGER_STAGNATION
WILDCARD_REGRESSION_STREAK
WILDCARD_PARAM_COUNT_MIN
WILDCARD_PARAM_COUNT_MAX
WILDCARD_PERTURB_RATIO_MIN
WILDCARD_PERTURB_RATIO_MAX
WILDCARD_PATIENCE_GAMES
REGRESSION_DISABLED
RUSSIA_OBJECTIVE_REGRESSION_ENABLED
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
DASHBOARD_SHOW_WHILE_PLAYING
DASHBOARD_CHART_GAMES
OBS_DASHBOARD_VISIBILITY_ENABLED
OBS_DASHBOARD_SCENE
OBS_DASHBOARD_SOURCE
MODEL_IMPROVE
EOF
)

# 過剰な stat 呼び出しを避けるため最低 10 秒間隔でチェック
_RUNTIME_TOGGLES_MIN_INTERVAL=${RUNTIME_TOGGLES_MIN_INTERVAL:-10}

_runtime_toggle_stat_mtime() {
	local path="$1"
	if stat -f %m "$path" 2>/dev/null; then
		return 0
	fi
	stat -c %Y "$path" 2>/dev/null
}

reload_runtime_toggles() {
	local env_file=".env"
	[ -f "$env_file" ] || return 0

	# rate-limit
	local now
	now=$(date +%s)
	if [ "$((now - _RUNTIME_TOGGLES_LAST_CHECKED))" -lt "$_RUNTIME_TOGGLES_MIN_INTERVAL" ]; then
		return 0
	fi
	_RUNTIME_TOGGLES_LAST_CHECKED="$now"

	local mt
	mt=$(_runtime_toggle_stat_mtime "$env_file")
	[ -z "$mt" ] && return 0
	if [ "$mt" = "$_RUNTIME_TOGGLES_LAST_MTIME" ]; then
		return 0
	fi
	_RUNTIME_TOGGLES_LAST_MTIME="$mt"

	# .env を解析し、ホワイトリストに含まれる行だけを export
	local changed=""
	local line key val old_val
	while IFS= read -r line; do
		case "$line" in
		'#'*) continue ;;
		'') continue ;;
		esac
		# KEY=VALUE のみ
		case "$line" in
		[A-Z_]*=*) ;;
		*) continue ;;
		esac
		key="${line%%=*}"
		val="${line#*=}"
		# ホワイトリストチェック
		case " $(echo $_RUNTIME_TOGGLES_KEYS) " in
		*" $key "*) ;;
		*) continue ;;
		esac
		# クォート剥がし
		case "$val" in
		\"*\") val="${val%\"}"; val="${val#\"}" ;;
		\'*\') val="${val%\'}"; val="${val#\'}" ;;
		esac
		# inline コメント除去
		val="${val%%[	 ]#*}"
		# 末尾空白除去
		val="${val%"${val##*[![:space:]]}"}"
		old_val=$(eval "echo \"\${$key:-__UNSET__}\"")
		if [ "$old_val" != "$val" ]; then
			export "$key=$val"
			changed="$changed $key=$val"
		fi
	done <"$env_file"

	if [ -n "$changed" ]; then
		# プロジェクト関数の log が定義されている時のみ呼ぶ (system /usr/bin/log と衝突回避)
		if declare -F log >/dev/null 2>&1; then
			log "[TOGGLES] hot-reload from .env:$changed"
		else
			echo "[TOGGLES] hot-reload from .env:$changed" >&2
		fi
	fi
}

# 強制再読込 (mtime cache を無視)。テスト用
reload_runtime_toggles_force() {
	_RUNTIME_TOGGLES_LAST_MTIME=""
	_RUNTIME_TOGGLES_LAST_CHECKED=0
	reload_runtime_toggles
}
