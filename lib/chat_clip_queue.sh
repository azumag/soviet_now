#!/bin/bash
# Chat-only clip delivery definitions; safe to source at a worker tick boundary.
# No startup, signal, environment or pause-state changes occur while sourcing.

# --- Clip queue 消化 ---
_process_clip_queue() {
    # Record events have an independent durable receipt, never a numeric Soren
    # game marker. The adapter reuses twitch_clip.sh and its existing credentials.
    TWITCH_CLIP_ENABLED="${TWITCH_CLIP_ENABLED:-0}" EXPLORE_MODE="${EXPLORE_MODE:-0}" python3 ./tools/record_clip_queue.py "$CLIP_QUEUE_DIR" 2>>"$TMP_DEBUG_DIR/twitch_clip.log" || true
	local queue_file
	local failed_dir="$CLIP_QUEUE_DIR/failed"
	mkdir -p "$failed_dir" || return 1
	for queue_file in "$CLIP_QUEUE_DIR"/*.json; do
		[ -f "$queue_file" ] || continue
        case "$(basename "$queue_file")" in record_*) continue ;; esac

		# JSON パース
		local event_msg game_id delay event_kind attempts parsed
		parsed=$(python3 -c "
import json, sys, shlex
d = json.load(open(sys.argv[1]))
assert isinstance(d, dict)
assert str(d.get('game_id', '')).isdigit() or not d.get('game_id')
assert isinstance(d.get('event_msg', ''), str)
assert isinstance(d.get('event_kind', 'generic'), str)
assert isinstance(d.get('delay', 0), int) and 0 <= d.get('delay', 0) <= 300
assert isinstance(d.get('attempts', 0), int) and 0 <= d.get('attempts', 0) <= 3
print(f'event_msg={shlex.quote(d.get(\"event_msg\",\"\"))}')
print(f'game_id={shlex.quote(str(d.get(\"game_id\",\"\")))}')
print(f'delay={shlex.quote(str(d.get(\"delay\",0)))}')
print(f'event_kind={shlex.quote(d.get(\"event_kind\",\"generic\"))}')
print(f'attempts={d.get(\"attempts\",0)}')
" "$queue_file" 2>/dev/null) || {
			_log "WARN: clip parse failed: $(basename "$queue_file") → failed"
			mv "$queue_file" "$failed_dir/" 2>/dev/null || true
			continue
		}
		eval "$parsed"

		# ソ連建国は同じ試合の一般クリップに抑止されない。
		# ソ連建国済みなら、その後のハイスコア等は従来どおり抑止する。
		local clip_marker=""
		if [ -n "$game_id" ]; then
			clip_marker="$TMP_MARKERS_DIR/.twitch_clip_game_${game_id}"
			if [ "$event_kind" = "soviet" ]; then
				clip_marker="${clip_marker}_soviet"
			elif [ -d "${clip_marker}_soviet" ]; then
				_log "clip skip: Soviet clip already claimed for game $game_id"
				if [ -f "${clip_marker}_soviet/failed_rc" ]; then
					mv "$queue_file" "$failed_dir/" 2>/dev/null || true
				else
					mv "$queue_file" "$CLIP_QUEUE_DONE_DIR/" 2>/dev/null || true
				fi
				continue
			fi
			if ! mkdir "$clip_marker" 2>/dev/null; then
				_log "clip skip: already claimed for game $game_id"
				if [ -f "$clip_marker/failed_rc" ]; then
					mv "$queue_file" "$failed_dir/" 2>/dev/null || true
				else
					mv "$queue_file" "$CLIP_QUEUE_DONE_DIR/" 2>/dev/null || true
				fi
				continue
			fi
		fi

		# delay 待機 (1秒単位で stop チェック)
		if [ "${delay:-0}" -gt 0 ] 2>/dev/null; then
			_log "clip waiting ${delay}s (game=${game_id:-?})"
			local waited=0
			while [ "$waited" -lt "$delay" ]; do
				if [ -f tmp/stop ]; then
					[ -z "$clip_marker" ] || rmdir "$clip_marker" 2>/dev/null || true
					return 0
				fi
				sleep 1
				waited=$((waited + 1))
			done
		fi

		_log "clip creating: ${event_msg} (game=${game_id:-?})"
		local clip_rc=0
		./twitch_clip.sh "$event_msg" "$event_kind" 2>>"$TMP_DEBUG_DIR/twitch_clip.log" || clip_rc=$?
		attempts=$((attempts + 1))
		if [ "$clip_rc" -eq 75 ] && [ "$attempts" -lt 3 ]; then
			# 未作成と判明した一時障害だけ次のtickで再試行する。
			# POST timeoutなど結果不明の失敗は、自動再作成しない。
			if python3 - "$queue_file" "$attempts" <<'PY'
import json, os, sys
path, attempts = sys.argv[1:]
with open(path) as f:
    event = json.load(f)
event.update(attempts=int(attempts), delay=0)
temporary = path + '.retry'
with open(temporary, 'w') as f:
    json.dump(event, f, ensure_ascii=False)
os.replace(temporary, path)
PY
			then
				[ -z "$clip_marker" ] || rmdir "$clip_marker" 2>/dev/null || true
				_log "clip retry scheduled (game=${game_id:-?}, attempt=$attempts/3)"
				return 0
			fi
		fi
		if [ "$clip_rc" -ne 0 ]; then
			_log "WARN: clip failed (game=${game_id:-?}, rc=$clip_rc, attempts=$attempts)"
			[ -z "$clip_marker" ] || printf '%s\n' "$clip_rc" > "$clip_marker/failed_rc"
			mv "$queue_file" "$failed_dir/" 2>/dev/null || true
			continue
		fi

		mv "$queue_file" "$CLIP_QUEUE_DONE_DIR/" 2>/dev/null || rm -f "$queue_file"
	done

	# done/ クリーンアップ (1時間超)
	find "$CLIP_QUEUE_DONE_DIR" -name '*.json' -mmin +60 -delete 2>/dev/null || true
}

