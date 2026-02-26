#!/bin/bash
# jloop.sh - JSON-based state loop for Soren game AI
#
# game_state.json → DECIDE(頭) → EXECUTE(手) のシンプル2段構成
# 画像認識不要。JSブリッジからの構造化データで判断する。

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

STATE_FILE="STATE.md"
COMMANDS="commands.txt"
GAME_STATE="game_state.json"
AI_TIMEOUT=600
PLAN_FILE="tmp/plan.md"
PLAN_JSON="tmp/plan.json"
BOARD_ANALYSIS="tmp/board_analysis.md"

# 座標系（実行系と一致させる）
GAME_X_MIN=-3.0
GAME_X_MAX=3.0
CANVAS_X_MIN=410
CANVAS_X_MAX=830

# 使用モデル（primary / fallback）
MODEL_PRIMARY="glm"
MODEL_FALLBACK="opencode:glmflash"

mkdir -p tmp
[ -f "$STATE_FILE" ] || echo "WAIT_READY" >"$STATE_FILE"

#--- ヘルパー ---
set_state() {
	echo "$1" >"$STATE_FILE"
	log "→ $1"
}
commands_empty() { [ -z "$(tr -d '[:space:]' <"$COMMANDS" 2>/dev/null)" ]; }
log() { echo "[$(date '+%H:%M:%S')] $*"; }

# game_state.jsonからゲームオーバー判定
is_game_over() {
	local s
	s=$(python3 -c "import json,sys; d=json.load(open('$GAME_STATE')); print(d.get('state',''))" 2>/dev/null)
	[ "$s" = "GAMEOVER" ] || [ "$s" = "STOP" ]
}

# game_state.jsonからMOVE状態か判定
is_move_state() {
	local s
	s=$(python3 -c "import json,sys; d=json.load(open('$GAME_STATE')); print(d.get('state',''))" 2>/dev/null)
	[ "$s" = "MOVE" ]
}

# 盤面が静止しているか判定（全ピースの速度が小さい）
is_board_settled() {
	python3 -c "
import json
try:
    d = json.load(open('$GAME_STATE'))
    pieces = d.get('pieces', [])
    if not pieces:
        print('true')
    else:
        max_v = max(abs(p.get('vx',0))**2 + abs(p.get('vy',0))**2 for p in pieces)
        print('true' if max_v < 0.1 else 'false')
except Exception:
    print('false')
" 2>/dev/null
}

#--- 履歴保存 ---
save_to_history() {
	local file="$1"
	[ -f "$file" ] || return 0
	mkdir -p tmp/history
	local ts=$(date '+%Y%m%d_%H%M%S')
	local base=$(basename "$file")
	mv "$file" "tmp/history/${ts}_${base}"
}
save_all_to_history() {
	# game_state.jsonはコピーして保存（元ファイルは残す）
	if [ -f "$GAME_STATE" ]; then
		mkdir -p tmp/history
		cp "$GAME_STATE" "tmp/history/$(date '+%Y%m%d_%H%M%S')_game_state.json"
	fi
	for f in "$PLAN_FILE" "$PLAN_JSON" tmp/think.md "$BOARD_ANALYSIS"; do
		save_to_history "$f"
	done
}
clear_history() { rm -rf tmp/history; }

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

#--- 待機ヘルパー ---
wait_commands_done() {
	for _ in $(seq 1 20); do
		commands_empty && return 0
		sleep 1
	done
	log "TIMEOUT: commands未消化 → クリア"
	echo "" >"$COMMANDS"
}

# DROP:X → canvas座標に変換してcommands.txtへ書き込み
write_drop_command() {
	local game_x="$1"
	# game X (-3.0〜+3.0) → canvas X (410〜830)
	local canvas_x
	canvas_x=$(python3 -c "
x = float('$game_x')
x = max($GAME_X_MIN, min($GAME_X_MAX, x))
cx = int((x + 3.0) / 6.0 * ($CANVAS_X_MAX - $CANVAS_X_MIN) + $CANVAS_X_MIN)
print(cx)
" 2>/dev/null)
	[ -z "$canvas_x" ] && canvas_x=620
	log "DROP game_x=$game_x → canvas=${canvas_x},350"
	echo "${canvas_x},350" >"$COMMANDS"
}

clamp_game_x() {
	local raw="$1"
	python3 - "$raw" <<'PY' 2>/dev/null
import sys
try:
    x = float(sys.argv[1])
    x = max(-3.0, min(3.0, x))
    print(f"{x:.3f}")
except Exception:
    pass
PY
}

extract_drop_from_plan_json() {
	[ -f "$PLAN_JSON" ] || return 0
	python3 - "$PLAN_JSON" <<'PY' 2>/dev/null
import json, sys
path = sys.argv[1]
try:
    d = json.load(open(path))
except Exception:
    sys.exit(0)
for key in ("x_game", "drop_x", "x"):
    v = d.get(key)
    if isinstance(v, (int, float)):
        print(v)
        break
    if isinstance(v, str):
        try:
            print(float(v))
            break
        except Exception:
            pass
PY
}

extract_drop_from_plan_md() {
	[ -f "$PLAN_FILE" ] || return 0
	head -n 1 "$PLAN_FILE" | sed -nE 's/^[[:space:]]*DROP:[[:space:]]*([+-]?[0-9]+([.][0-9]+)?).*/\1/p'
}

extract_drop_from_board_analysis() {
	[ -f "$BOARD_ANALYSIS" ] || return 0
	python3 - "$BOARD_ANALYSIS" <<'PY' 2>/dev/null
import re, sys
text = open(sys.argv[1]).read()
m = re.search(r'推奨ドロップ:\s*DROP:([+-]?\d+(?:\.\d+)?)', text)
if m:
    print(m.group(1))
PY
}

write_plan_json() {
	local x="$1"
	local source="$2"
	python3 - "$PLAN_JSON" "$x" "$source" <<'PY' 2>/dev/null
import json, sys, datetime
path, x, source = sys.argv[1], float(sys.argv[2]), sys.argv[3]
payload = {
    "x_game": round(x, 3),
    "source": source,
    "updated_at": datetime.datetime.now().isoformat(timespec="seconds"),
}
with open(path, "w") as f:
    json.dump(payload, f, ensure_ascii=False, indent=2)
PY
}

normalize_plan_md_first_line() {
	local x="$1"
	if [ -f "$PLAN_FILE" ]; then
		tail -n +2 "$PLAN_FILE" >"${PLAN_FILE}.body" 2>/dev/null || true
		{
			printf "DROP:%s\n" "$x"
			cat "${PLAN_FILE}.body" 2>/dev/null
		} >"${PLAN_FILE}.tmp"
		mv "${PLAN_FILE}.tmp" "$PLAN_FILE"
		rm -f "${PLAN_FILE}.body"
	else
		cat >"$PLAN_FILE" <<EOF
DROP:${x}
理由: 自動補完（plan.jsonから復元）
物理予測: 解析レポートに基づく保守実行
EOF
	fi
}

resolve_drop_x() {
	local raw=""
	raw=$(extract_drop_from_plan_json)
	[ -z "$raw" ] && raw=$(extract_drop_from_plan_md)
	[ -z "$raw" ] && raw=$(extract_drop_from_board_analysis)
	[ -z "$raw" ] && raw="0.0"
	clamp_game_x "$raw"
}

apply_safe_fallback_plan() {
	local reason="$1"
	local raw_x
	raw_x=$(extract_drop_from_board_analysis)
	[ -z "$raw_x" ] && raw_x="0.0"
	local x
	x=$(clamp_game_x "$raw_x")
	[ -z "$x" ] && x="0.000"
	cat >"$PLAN_FILE" <<EOF
DROP:${x}
理由: ${reason}
物理予測: フェイルセーフで解析推奨座標を採用
EOF
	write_plan_json "$x" "fallback"
	log "[DECIDE] fallback plan applied DROP:${x}"
}

normalize_ai_plan_or_fallback() {
	local raw_x
	raw_x=$(extract_drop_from_plan_json)
	local source="ai_json"
	if [ -z "$raw_x" ]; then
		raw_x=$(extract_drop_from_plan_md)
		source="ai_md"
	fi
	if [ -z "$raw_x" ]; then
		apply_safe_fallback_plan "AI出力からDROP座標を抽出できなかった"
		return 1
	fi

	local x
	x=$(clamp_game_x "$raw_x")
	if [ -z "$x" ]; then
		apply_safe_fallback_plan "AI出力のDROP座標が不正だった"
		return 1
	fi

	normalize_plan_md_first_line "$x"
	write_plan_json "$x" "$source"
	return 0
}

#=== メインループ ===
log "=== Soren JSON Loop (jloop) ==="
SETTLE_WAIT=0

while true; do
	state=$(head -n 1 "$STATE_FILE" 2>/dev/null | tr -d '\n\r')
	case "$state" in

	WAIT_READY)
		if ! commands_empty; then
			SETTLE_WAIT=0
			sleep 1
			continue
		fi

		# game_state.jsonが存在しない場合は待つ
		if [ ! -f "$GAME_STATE" ]; then
			sleep 2
			continue
		fi

		# ゲームオーバーチェック
		if is_game_over; then
			log "GAME OVER detected"
			set_state "GAME_OVER"
			continue
		fi

		# MOVE状態でなければ待つ（DROP中など）
		if ! is_move_state; then
			SETTLE_WAIT=0
			NOMOVE_WAIT=$((${NOMOVE_WAIT:-0} + 1))
			if [ "$NOMOVE_WAIT" -ge 15 ]; then
				cur_state=$(python3 -c "import json; print(json.load(open('$GAME_STATE')).get('state','?'))" 2>/dev/null)
				log "waiting for MOVE (current: $cur_state, ${NOMOVE_WAIT}s)"
				NOMOVE_WAIT=0
			fi
			sleep 1
			continue
		fi
		NOMOVE_WAIT=0

		# 盤面静止を待つ（ピースが落ち着くまで）
		settled=$(is_board_settled)
		if [ "$settled" != "true" ]; then
			SETTLE_WAIT=0
			sleep 1
			continue
		fi

		# 2回連続で静止確認してからDECIDEへ
		SETTLE_WAIT=$((SETTLE_WAIT + 1))
		if [ $SETTLE_WAIT -ge 2 ]; then
			SETTLE_WAIT=0
			set_state "DECIDE"
		fi
		sleep 1
		;;

	#--- 頭（JSON分析+ドロップ決定） ---
	DECIDE)
		save_all_to_history

		# DECIDE時点のスナップショット（AI思考中にgame_state.jsonが変わっても安全）
		cp "$GAME_STATE" tmp/state_snapshot.json 2>/dev/null

		# 盤面空間解析（スナップショットを使用）
		python3 analyze_board.py tmp/state_snapshot.json "$BOARD_ANALYSIS" 2>/dev/null

		if run_ai DECIDE "$MODEL_PRIMARY" "$MODEL_FALLBACK" \
			prompts/jdecide.md "$PLAN_FILE" "$BOARD_ANALYSIS" STRATEGY.md think.md best_strategy.md; then
			normalize_ai_plan_or_fallback || true
		else
			apply_safe_fallback_plan "AI推論が失敗したためフェイルセーフ"
		fi

		set_state "EXECUTE"
		;;

	#--- 手（plan.mdからDROP座標を抽出して実行） ---
	EXECUTE)
		[ -f think.md ] && cat think.md
		[ -f "$PLAN_FILE" ] && cat "$PLAN_FILE"
		[ -f "$PLAN_JSON" ] && cat "$PLAN_JSON"

		drop_x=$(resolve_drop_x)
		[ -z "$drop_x" ] && drop_x="0.000"
		log "[EXECUTE] resolved DROP:${drop_x}"

		write_drop_command "$drop_x"
		wait_commands_done
		sleep 2
		set_state "WAIT_READY"
		;;

	#--- ゲームオーバー振り返り ---
	GAME_OVER)
		# ハイスコア判定・保存
		current_score=$(python3 -c "import json; print(json.load(open('$GAME_STATE')).get('score',0))" 2>/dev/null)
		best_score=$(cat best_score.txt 2>/dev/null || echo 0)
		if [ "${current_score:-0}" -gt "${best_score:-0}" ]; then
			log "🏆 NEW HIGH SCORE: $current_score (prev: $best_score)"
			echo "$current_score" > best_score.txt
			cp STRATEGY.md best_strategy.md
			cp "$GAME_STATE" best_game_state.json
			[ -f think.md ] && cp think.md best_think.md
		else
			log "Score: $current_score (best: $best_score)"
		fi

		# AppleDoubleファイルを削除（履歴ノイズ回避）
		find tmp/history -name '._*' -delete 2>/dev/null
		run_ai GAME_OVER sonnet "$MODEL_PRIMARY" \
			prompts/postmortem.md "" "$GAME_STATE" STRATEGY.md think.md best_strategy.md
		[ -f tmp/postmortem.md ] && cat tmp/postmortem.md
		sleep 2

		if is_game_over; then
			log "confirmed game over → retry"
			echo "retry" >"$COMMANDS"
			wait_commands_done
			sleep 3
		else
			log "false positive → skip retry"
		fi

		clear_history
		rm -f "$PLAN_FILE" "$PLAN_JSON" tmp/think.md "$BOARD_ANALYSIS"
		set_state "WAIT_RESTART"
		;;

	#--- リトライ後のゲーム再起動待ち ---
	WAIT_RESTART)
		# game_state.jsonがMOVEかつpieces少(新ゲーム開始)になるまで待つ
		rs=$(python3 -c "
import json
try:
    d = json.load(open('$GAME_STATE'))
    s = d.get('state','')
    n = len(d.get('pieces',[]))
    # 新ゲーム: MOVE状態でピースが少ない（リトライ直後）
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
			log "new game detected → resuming"
			SETTLE_WAIT=0
			set_state "WAIT_READY"
			;;
		still_over)
			# まだGAMEOVER状態 → retryがまだ効いていない
			RESTART_WAIT=$((${RESTART_WAIT:-0} + 1))
			if [ "$RESTART_WAIT" -ge 30 ]; then
				log "RESTART TIMEOUT: re-sending retry"
				echo "retry" >"$COMMANDS"
				wait_commands_done
				RESTART_WAIT=0
			fi
			sleep 2
			;;
		*)
			# 初期化中（DROP等の中間状態）
			RESTART_WAIT=0
			sleep 2
			;;
		esac
		;;

	*)
		log "Unknown: $state"
		set_state "WAIT_READY"
		;;
	esac
	sleep 0.5
done
