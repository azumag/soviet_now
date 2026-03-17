# core/game_state.sh - is_game_over, wait_for_move, send_retry 等


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

wait_for_move() {
	log "MOVE状態を待機中..."
	local waited=0
	while [ $waited -lt 60 ]; do
		[ -f tmp/stop ] && return 130
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

send_retry() {
	log "retry送信..."
	_clear_stale_commands_if_any "before retry"
	echo "retry" >"$COMMANDS"
	wait_commands_done
	sleep 1

	local waited=0
	while [ $waited -lt 60 ]; do
		local rs
		rs=$(python3 -c "
import json
try:
    d = json.load(open('$GAME_STATE'))
    s = d.get('state','')
    score = d.get('score', 0)
    n = len(d.get('pieces',[]))
    if s == 'MOVE' and ((score <= 0 and n <= 4) or n <= 2):
        print(f'ready|{s}|{score}|{n}')
    elif s == 'GAMEOVER' or s == 'STOP':
        print(f'still_over|{s}|{score}|{n}')
    else:
        print(f'waiting|{s}|{score}|{n}')
except:
    print('waiting|?|?|?')
" 2>/dev/null)

		local rs_kind rs_state rs_score rs_pieces
		IFS='|' read -r rs_kind rs_state rs_score rs_pieces <<<"$rs"
		case "$rs" in
		ready*)
			log "新ゲーム検出 (${waited}s, state=${rs_state:-?}, score=${rs_score:-?}, pieces=${rs_pieces:-?})"
			return 0
			;;
		still_over*)
			if [ $((waited % 20)) -eq 0 ] && [ $waited -gt 0 ]; then
				log "まだGAMEOVER/STOP (${waited}s, state=${rs_state:-?}, score=${rs_score:-?}, pieces=${rs_pieces:-?}) → retry再送"
				echo "retry" >"$COMMANDS"
				wait_commands_done
			fi
			;;
		esac
		if [ $((waited % 10)) -eq 0 ] && [ "$waited" -gt 0 ] && [ "${rs_kind:-waiting}" = "waiting" ]; then
			log "retry待機中 ${waited}s (state=${rs_state:-?}, score=${rs_score:-?}, pieces=${rs_pieces:-?})"
		fi
		[ -f tmp/stop ] && return 130
		sleep 2
		waited=$((waited + 2))
	done
	log "WARNING: 新ゲーム検知タイムアウト"
	return 1
}
