#!/bin/bash
# eloop.sh - Self-Improving Strategy Loop
#
# 外側ループ: strategy_runner.py で1試合自律プレイ → AI で strategy.py 改善 → 次試合
# jloop.sh のヘルパー関数を再利用。

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

COMMANDS="commands.txt"
GAME_STATE="game_state.json"
AI_TIMEOUT=600

# strategy 関連
STRATEGY_FILE="strategy.py"
BEST_STRATEGY_FILE="best_strategy.py"
STRATEGY_VERSIONS_DIR="strategy_versions"
HISTORY_DIR="game_history"
HISTORY_FILE="$HISTORY_DIR/latest.jsonl"

# 使用モデル
MODEL_PRIMARY="glm"
MODEL_FALLBACK="opencode:glmflash"

# カウンタ
GAME_NUM=0

mkdir -p "$STRATEGY_VERSIONS_DIR" "$HISTORY_DIR"

#--- ヘルパー (jloop.sh と共通) ---
commands_empty() { [ -z "$(tr -d '[:space:]' <"$COMMANDS" 2>/dev/null)" ]; }
log() { echo "[$(date '+%H:%M:%S')] $*"; }

is_game_over() {
	local s
	s=$(python3 -c "import json; print(json.load(open('$GAME_STATE')).get('state',''))" 2>/dev/null)
	[ "$s" = "GAMEOVER" ] || [ "$s" = "STOP" ]
}

is_move_state() {
	local s
	s=$(python3 -c "import json; print(json.load(open('$GAME_STATE')).get('state',''))" 2>/dev/null)
	[ "$s" = "MOVE" ]
}

wait_commands_done() {
	for _ in $(seq 1 20); do
		commands_empty && return 0
		sleep 1
	done
	log "TIMEOUT: commands未消化 → クリア"
	echo "" >"$COMMANDS"
}

#--- スピナー ---
_spinner_pid=0
start_spinner() {
	local label="$1"
	(
		local frames=('⣾' '⣽' '⣻' '⢿' '⡿' '⣟' '⣯' '⣷')
		local i=0 start=$SECONDS
		while true; do
			local e=$((SECONDS - start))
			local m=$((e / 60)) s=$((e % 60))
			printf '\r  \033[1;35m%s\033[0m \033[1m%s\033[0m \033[2m%d:%02d\033[0m  ' \
				"${frames[i % ${#frames[@]}]}" "$label" "$m" "$s" >&2
			sleep 0.12
			((i++))
		done
	) &
	_spinner_pid=$!
}
stop_spinner() {
	if [ "${_spinner_pid:-0}" -ne 0 ]; then
		kill "$_spinner_pid" 2>/dev/null
		wait "$_spinner_pid" 2>/dev/null
		printf '\r\033[K' >&2
		_spinner_pid=0
	fi
}

#--- プロンプト構築 ---
build_prompt() {
	local pf="$1"
	shift
	local p
	p=$(cat "$pf" 2>/dev/null) || return 1
	local c=""
	for f in "$@"; do
		[ -f "$f" ] && c+=$'\n--- '"$f"$' ---\n'"$(cat "$f")"$'\n---\n'
	done
	[ -n "$c" ] && p="参照データ:${c}
${p}"
	echo "$p"
}

#--- コマンド実行 ---
run_cmd() {
	local spec="$1" prompt="$2"
	local type="${spec%%:*}" agent="${spec#*:}"
	[ "$type" = "$agent" ] && agent=""

	case "$type" in
	glm)
		opencode run "'$prompt'" --agent="zai" &
		;;
	gemini)
		gemini -p "$prompt" -y -s &
		;;
	gemini-flash)
		gemini -p "$prompt" -y -s --model=gemini-2.5-flash &
		;;
	sonnet)
		claude -p "$prompt" --model=sonnet --permission-mode=acceptEdits &
		;;
	opus)
		claude -p "$prompt" --model=opus --permission-mode=acceptEdits &
		;;
	claude)
		claude -p "$prompt" --model=Haiku --permission-mode=acceptEdits &
		;;
	opencode)
		opencode run "'$prompt'" --agent="${agent:-glmflash}" &
		;;
	esac
	local cmd_pid=$!

	start_spinner "$type thinking..."

	(sleep "$AI_TIMEOUT" && kill "$cmd_pid" 2>/dev/null && log "AI TIMEOUT (${AI_TIMEOUT}s)") &
	local timer_pid=$!

	trap "stop_spinner; kill $cmd_pid $timer_pid 2>/dev/null; wait $cmd_pid $timer_pid 2>/dev/null; log 'Interrupted'; trap - INT; return 130" INT

	wait "$cmd_pid" 2>/dev/null
	local ret=$?

	stop_spinner
	kill "$timer_pid" 2>/dev/null
	wait "$timer_pid" 2>/dev/null
	trap - INT

	return $ret
}

#--- AIステップ ---
run_ai() {
	local label="$1" primary="$2" fallback="$3" pf="$4" expect="$5"
	shift 5
	local prompt
	prompt=$(build_prompt "$pf" "$@")
	if [ -z "$prompt" ]; then
		log "[$label] prompt missing"
		return 1
	fi

	[ -n "$expect" ] && rm -f "$expect"

	log "[$label] primary=$primary"
	run_cmd "$primary" "$prompt"
	if [ -n "$expect" ]; then
		[ -s "$expect" ] && {
			log "[$label] primary OK ($expect written)"
			return 0
		}
	else
		[ $? -eq 0 ] && return 0
	fi

	log "[$label] primary failed → fallback=$fallback"
	run_cmd "$fallback" "$prompt"
	if [ -n "$expect" ] && [ ! -s "$expect" ]; then
		log "[$label] fallback also failed ($expect not written)"
		return 1
	fi
}

#--- strategy.py バリデーション ---
validate_strategy() {
	log "[VALIDATE] strategy.py をチェック中..."

	# 1. decide() の存在チェック
	if ! python3 - <<'PYEOF' 2>&1; then
import importlib.util, sys, inspect
spec = importlib.util.spec_from_file_location('strategy', 'strategy.py')
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
if not hasattr(mod, 'decide'):
    print('ERROR: decide() not found')
    sys.exit(1)
sig = inspect.signature(mod.decide)
params = list(sig.parameters.keys())
if len(params) < 2:
    print(f'ERROR: decide() needs 2+ params, got {len(params)}: {params}')
    sys.exit(1)
print(f'OK: decide({", ".join(params)})')
PYEOF
		log "[VALIDATE] decide() シグネチャチェック失敗"
		return 1
	fi

	# 2. テスト実行 (game_state.json があれば)
	if [ -f "$GAME_STATE" ]; then
		local test_out
		test_out=$(python3 strategy.py "$GAME_STATE" 2>&1)
		if [ $? -ne 0 ]; then
			log "[VALIDATE] テスト実行失敗: $test_out"
			return 1
		fi
		# JSON出力チェック
		if ! echo "$test_out" | python3 -c "import json,sys; d=json.load(sys.stdin); assert 'x' in d" 2>/dev/null; then
			log "[VALIDATE] テスト出力にxフィールドなし: $test_out"
			return 1
		fi
		log "[VALIDATE] テスト実行OK"
	fi

	return 0
}

#--- バージョン管理 ---
save_strategy_version() {
	local score="$1"
	GAME_NUM=$((GAME_NUM + 1))
	local version_file
	version_file=$(printf "%s/v%03d_score%04d_strategy.py" "$STRATEGY_VERSIONS_DIR" "$GAME_NUM" "$score")
	cp "$STRATEGY_FILE" "$version_file"
	log "[VERSION] saved: $version_file"
}

#--- ベスト管理 ---
update_best() {
	local current_score="$1"
	local best_score
	best_score=$(cat best_score.txt 2>/dev/null || echo 0)

	if [ "${current_score:-0}" -gt "${best_score:-0}" ]; then
		log "🏆 NEW HIGH SCORE: $current_score (prev: $best_score)"
		echo "$current_score" >best_score.txt
		cp "$STRATEGY_FILE" "$BEST_STRATEGY_FILE"
		cp "$GAME_STATE" best_game_state.json
		return 0
	else
		log "Score: $current_score (best: $best_score)"
		return 1
	fi
}

#--- 履歴アーカイブ ---
archive_history() {
	local score="$1"
	local ts
	ts=$(date '+%Y%m%d_%H%M%S')
	if [ -f "$HISTORY_FILE" ]; then
		local archive
		archive=$(printf "%s/%s_score%04d.jsonl" "$HISTORY_DIR" "$ts" "$score")
		cp "$HISTORY_FILE" "$archive"
		log "[ARCHIVE] $archive"
	fi
}

#--- MOVE状態待ち ---
wait_for_move() {
	log "MOVE状態を待機中..."
	local waited=0
	while [ $waited -lt 60 ]; do
		if [ -f "$GAME_STATE" ] && is_move_state; then
			log "MOVE状態検出"
			return 0
		fi
		sleep 2
		waited=$((waited + 2))
	done
	log "TIMEOUT: MOVE状態待ち"
	return 1
}

#--- リトライ (新ゲーム開始) ---
send_retry() {
	log "retry送信..."
	echo "retry" >"$COMMANDS"
	wait_commands_done
	sleep 3

	# 新ゲーム検知待ち
	local waited=0
	while [ $waited -lt 60 ]; do
		local rs
		rs=$(python3 -c "
import json
try:
    d = json.load(open('$GAME_STATE'))
    s = d.get('state','')
    n = len(d.get('pieces',[]))
    if s == 'MOVE' and n <= 2:
        print('ready')
    elif s == 'GAMEOVER' or s == 'STOP':
        print('still_over')
    else:
        print('waiting')
except:
    print('waiting')
" 2>/dev/null)

		case "$rs" in
		ready)
			log "新ゲーム検出"
			return 0
			;;
		still_over)
			if [ $((waited % 20)) -eq 0 ] && [ $waited -gt 0 ]; then
				log "まだGAMEOVER → retry再送"
				echo "retry" >"$COMMANDS"
				wait_commands_done
			fi
			;;
		esac
		sleep 2
		waited=$((waited + 2))
	done
	log "WARNING: 新ゲーム検知タイムアウト"
	return 1
}

#=== メインループ ===
log "=== Soren Evolution Loop (eloop) ==="
log "MODEL_PRIMARY=$MODEL_PRIMARY MODEL_FALLBACK=$MODEL_FALLBACK"
log "strategy.py → strategy_runner.py → AI改善 → repeat"

# 初期バリデーション
if [ ! -f "$STRATEGY_FILE" ]; then
	log "ERROR: $STRATEGY_FILE が見つかりません"
	exit 1
fi

if ! validate_strategy; then
	log "ERROR: 初期 strategy.py のバリデーション失敗"
	exit 1
fi

# MOVE状態待ち（初回）
wait_for_move || {
	log "ゲームが起動していません。soviet_local.mjs を先に起動してください。"
	exit 1
}

while true; do
	GAME_NUM_DISPLAY=$((GAME_NUM + 1))
	log ""
	log "========================================="
	log "  Game #${GAME_NUM_DISPLAY}"
	log "========================================="

	#--- Step 1: strategy_runner.py で1試合プレイ ---
	log "[PLAY] strategy_runner.py 実行中..."
	RUNNER_TMPFILE=$(mktemp /tmp/eloop_runner.XXXXXX)
	python3 -u strategy_runner.py 2>&1 | tee "$RUNNER_TMPFILE"
	RUNNER_EXIT=${PIPESTATUS[0]}

	# 結果抽出 (---RESULT--- 以降のJSON)
	RESULT_JSON=$(sed -n '/^---RESULT---$/,$ p' "$RUNNER_TMPFILE" | tail -n 1)
	rm -f "$RUNNER_TMPFILE"

	if [ -z "$RESULT_JSON" ]; then
		log "WARNING: strategy_runner.py の結果取得失敗"
		RESULT_JSON='{"score":0,"turns":0,"state":"UNKNOWN"}'
	fi

	SCORE=$(echo "$RESULT_JSON" | python3 -c "import json,sys; print(json.load(sys.stdin).get('score',0))" 2>/dev/null || echo 0)
	TURNS=$(echo "$RESULT_JSON" | python3 -c "import json,sys; print(json.load(sys.stdin).get('turns',0))" 2>/dev/null || echo 0)

	log "[RESULT] Score=$SCORE, Turns=$TURNS"

	#--- Step 2: バージョン保存 ---
	save_strategy_version "$SCORE"

	#--- Step 3: ベスト判定 ---
	update_best "$SCORE"

	#--- Step 4: 履歴アーカイブ ---
	archive_history "$SCORE"

	#--- Step 5: AI で strategy.py 改善 ---
	log "[IMPROVE] AI による strategy.py 改善..."

	# バックアップ
	cp "$STRATEGY_FILE" "${STRATEGY_FILE}.bak"

	run_ai IMPROVE "$MODEL_PRIMARY" "$MODEL_FALLBACK" \
		prompts/improve_strategy.md "$STRATEGY_FILE" \
		"$STRATEGY_FILE" "$HISTORY_FILE" "$BEST_STRATEGY_FILE" "$GAME_STATE"

	#--- Step 6: バリデーション ---
	if validate_strategy; then
		log "[IMPROVE] バリデーション成功 → 新strategy採用"
		rm -f "${STRATEGY_FILE}.bak"
	else
		log "[IMPROVE] バリデーション失敗 → 前バージョンに復元"
		mv "${STRATEGY_FILE}.bak" "$STRATEGY_FILE"
	fi

	#--- Step 7: retry → 新ゲーム ---
	if is_game_over; then
		send_retry
	else
		log "GAMEOVER未検出 → MOVE状態待ち"
		wait_for_move || {
			log "ゲーム停止 → retry試行"
			send_retry
		}
	fi

	sleep 2
done
