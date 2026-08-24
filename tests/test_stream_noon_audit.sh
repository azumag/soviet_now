#!/usr/bin/env bash
# stream_noon_audit ワーカーの回帰テスト。
# - JST 正午を過ぎた日に1回だけ監査が走る (日次マーカー)
# - started_at の JST 时刻が許容誤差以内なら何もしない
# - ずれていれば正規手順 stop → respawn 待機 → (必要なら) 自前起動で張り直す
# - 意図的 pause 中 / 非稼働 / started_at 不正は触れない
# - supervisor 側 respawn を検出できれば自前起動しない
set -u

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TMP="$(mktemp -d)"
cleanup() {
	if [ "${KEEP_TMP:-0}" = "1" ]; then
		echo "KEEP_TMP=$TMP"
	else
		rm -rf "$TMP"
	fi
}
trap cleanup EXIT

ok=0
fail=0
check() {
	local condition="$1" message="$2"
	if eval "$condition"; then
		printf 'ok - %s\n' "$message"
		ok=$((ok + 1))
	else
		printf 'not ok - %s\n' "$message"
		fail=$((fail + 1))
	fi
}

# 今日の JST 正午 epoch
NOON=$(python3 -c 'import time;t=int(time.time());print((t+32400)//86400*86400+43200-32400)')
NOON_DAY2=$((NOON + 86400))

emit_status() {
	local path="$1" running="$2" started="${3:-}"
	if [ "$running" = "1" ]; then
		printf '{"backend":"ffmpeg","running":true,"state":"running","pid":111,"started_at":%s}\n' "$started" >"$path"
	else
		printf '{"backend":"ffmpeg","running":false,"state":"stopped"}\n' >"$path"
	fi
}

emit_twitch_id() {
	local dir="$1" stream_id="$2"
	printf '"%s"\n' "$stream_id" >"$dir/twitch_id.txt"
}

make_stubs() {
	local dir="$1"
	mkdir -p "$dir/bin" "$dir/state" "$dir/logs"
	cat >"$dir/bin/now.sh" <<EOF
#!/usr/bin/env bash
cat "$dir/now.txt"
EOF
	cat >"$dir/bin/status.sh" <<EOF
#!/usr/bin/env bash
cat "$dir/status.json"
EOF
	cat >"$dir/bin/stop.sh" <<EOF
#!/usr/bin/env bash
echo "stop" >> "$dir/calls.log"
emit() { printf '{"backend":"ffmpeg","running":%s}\n' "\$1" > "$dir/status.json"; }
emit false
exit 0
EOF
	cat >"$dir/bin/run.sh" <<EOF
#!/usr/bin/env bash
echo "run" >> "$dir/calls.log"
if [ ! -f "$dir/run_no_flip" ]; then
	printf '{"backend":"ffmpeg","running":true,"state":"running","pid":222,"started_at":%s}\n' "\${STREAM_NOON_TEST_NEW_STARTED:-9999999999}" > "$dir/status.json"
	printf '"%s"\n' "\${STREAM_NOON_TEST_NEW_TWITCH_ID:-rotated}" > "$dir/twitch_id.txt"
fi
exit 0
EOF
	cat >"$dir/bin/twitch.sh" <<EOF
#!/usr/bin/env bash
if [ -f "$dir/twitch_id.txt" ]; then
	printf '{"data":{"user":{"stream":{"id":%s}}}}\n' "\$(cat "$dir/twitch_id.txt")"
else
	printf '{"data":{"user":null}}\n'
fi
EOF
	chmod +x "$dir/bin/"*.sh
	: >"$dir/calls.log"
	printf '%s\n' "$NOON" >"$dir/now.txt"
}

launch_worker() {
	local dir="$1" extra_env="$2"
	(
		export STREAM_NOON_AUDIT_PID_FILE="$dir/state/noon_audit.pid"
		export STREAM_NOON_AUDIT_PAUSE_FILE="$dir/state/noon_audit.paused"
		export STREAM_NOON_AUDIT_STATE_DIR="$dir/state/stream_noon_audit"
		export STREAM_NOON_AUDIT_STREAM_PAUSE_MARKER="$dir/state/direct_stream.paused"
		export STREAM_NOON_AUDIT_LOG_FILE="$dir/logs/direct_stream.log"
		export STREAM_NOON_AUDIT_POLL_SEC=1
		export STREAM_NOON_AUDIT_TOLERANCE_SEC="${TOLERANCE_SEC:-600}"
		export STREAM_NOON_AUDIT_RESPAWN_WAIT_SEC="${RESPAWN_WAIT_SEC:-6}"
		export STREAM_NOON_AUDIT_STOP_TIMEOUT_SEC=3
		export STREAM_NOON_AUDIT_OFFLINE_HOLD_SEC="${OFFLINE_HOLD_SEC:-0}"
		export STREAM_NOON_AUDIT_NOW_CMD="bash $dir/bin/now.sh"
		export STREAM_NOON_AUDIT_STATUS_CMD="bash $dir/bin/status.sh"
		export STREAM_NOON_AUDIT_STOP_CMD="bash $dir/bin/stop.sh"
		export STREAM_NOON_AUDIT_RUN_CMD="bash $dir/bin/run.sh"
		export STREAM_NOON_AUDIT_GQL_CMD="bash $dir/bin/twitch.sh"
		if [ -n "$extra_env" ]; then
			eval "export $extra_env"
		fi
		cd "$ROOT" || exit 1
		bash workers/stream_noon_audit.sh >>"$dir/worker.log" 2>&1 &
		echo $! >"$dir/worker.pid"
	)
}

wait_for_marker_field() {
	local marker="$1" pattern="$2" i=0 max="${3:-60}"
	while [ $i -lt "$max" ]; do
		if [ -f "$marker" ] && grep -qE "$pattern" "$marker" 2>/dev/null; then
			return 0
		fi
		sleep 0.5
		i=$((i + 1))
	done
	return 1
}

stop_worker() {
	local dir="$1" pid="" i=0
	[ -f "$dir/worker.pid" ] || return 0
	pid=$(cat "$dir/worker.pid")
	kill "$pid" 2>/dev/null || true
	rm -f "$dir/worker.pid"
	# 実際に死ぬまで待つ (後続 launch との pidfile レースを防ぐ)
	while [ $i -lt 20 ]; do
		kill -0 "$pid" 2>/dev/null || return 0
		sleep 0.25
		i=$((i + 1))
	done
	echo "WARN: worker $pid が停止しない" >&2
}

calls_count() {
	local n=""
	n=$(grep -c . "$1/calls.log" 2>/dev/null)
	case "$n" in ''|*[!0-9]*) n=0 ;; esac
	echo "$n"
}

# --- 1. 位相が許容誤差以内 → 何もしない ---
D1="$TMP/case_aligned"
make_stubs "$D1"
emit_status "$D1/status.json" 1 "$((NOON - 60))"
launch_worker "$D1" ""
M1="$D1/state/stream_noon_audit/$(python3 -c "print(($NOON+32400)//86400)").json"
check 'wait_for_marker_field "$M1" "no_action"' 'aligned: 監査が走って no_action を記録'
sleep 1
check '[ "$(calls_count "$D1")" = "0" ]' 'aligned: stop/run を呼ばない'
check '[ ! -f "$D1/state/direct_stream.paused" ]' 'aligned: pause marker を作らない'
stop_worker "$D1"

# --- 2. 日内は再監査しない ---
printf '%s\n' "$((NOON + 3600))" >"$D1/now.txt"
launch_worker "$D1" ""
sleep 3
check '[ "$(calls_count "$D1")" = "0" ]' '同日の2回目 tick では再監査しない'
stop_worker "$D1"

# --- 3. ずれ → supervisor respawn 検出で自前起動なしに張り直し ---
D2="$TMP/case_drift_supervisor"
make_stubs "$D2"
emit_status "$D2/status.json" 1 "$((NOON + 100 - 10800))"
emit_twitch_id "$D2" "old-session"
launch_worker "$D2" ""
M2="$D2/state/stream_noon_audit/$(python3 -c "print(($NOON+32400)//86400)").json"
# supervisor 相当: stop 後2秒で新しい started_at の配信を立ち上げる
(
	for _ in $(seq 1 40); do
		grep -q '^stop$' "$D2/calls.log" 2>/dev/null && break
		sleep 0.2
	done
	sleep 1
	emit_status "$D2/status.json" 1 "$((NOON + 130))"
	emit_twitch_id "$D2" "new-session"
) &
sup_pid=$!
check 'wait_for_marker_field "$M2" "\"outcome\": \"restarted\""' 'drift: 張り直しが完了する'
kill "$sup_pid" 2>/dev/null || true
check '[ "$(grep -c "^stop$" "$D2/calls.log")" = "1" ]' 'drift: stop は正規手経由で1回'
check '[ ! -f "$D2/state/direct_stream.paused" ]' 'drift: pause marker は最終的に解除される'
check '[ "$(grep -c "^run$" "$D2/calls.log")" = "0" ]' 'drift: supervisor respawn 検出時は run を呼ばない'
check 'wait_for_marker_field "$M2" "\"started_after\": \"$((NOON + 130))\""' 'drift: marker に新しい started_at を記録'
check 'wait_for_marker_field "$M2" "\"session_before\": \"old-session\""' 'drift: marker に旧Twitch配信IDを記録'
check 'wait_for_marker_field "$M2" "\"session_after\": \"new-session\""' 'drift: marker に新Twitch配信IDを記録'
stop_worker "$D2"

# --- 4. ずれ → respawn しない場合の自前起動フォールバック ---
D3="$TMP/case_drift_fallback"
make_stubs "$D3"
export STREAM_NOON_TEST_NEW_STARTED=$((NOON + 200))
emit_status "$D3/status.json" 1 "$((NOON + 100 - 18000))"
launch_worker "$D3" ""
M3="$D3/state/stream_noon_audit/$(python3 -c "print(($NOON+32400)//86400)").json"
check 'wait_for_marker_field "$M3" "\"outcome\": \"restarted\""' 'fallback: 自前起動で復旧を記録'
check '[ "$(grep -c "^run$" "$D3/calls.log")" = "1" ]' 'fallback: run コマンドを1回呼ぶ'
check '[ ! -f "$D3/state/direct_stream.paused" ]' 'fallback: pause marker は解除済み'
stop_worker "$D3"
unset STREAM_NOON_TEST_NEW_STARTED

# --- 4b. フォールバックでも復旧不能 → restart_failed とマーカー保持 ---
# run.sh を無反応化 (run_no_flip) し、status は stop 後も false のまま。
# 初期状態が running のため keepdead 的な書き込み競合は不要かつ有害 (監査読みに
# 先勝ちすると skipped_not_running になる)。
D3B="$TMP/case_restart_failed"
make_stubs "$D3B"
touch "$D3B/run_no_flip"
emit_status "$D3B/status.json" 1 "$((NOON + 100 - 18000))"
RESPAWN_WAIT_SEC=2 launch_worker "$D3B" ""
M3B="$D3B/state/stream_noon_audit/$(python3 -c "print(($NOON+32400)//86400)").json"
check 'wait_for_marker_field "$M3B" "restart_failed" 120' 'restart_failed: 復旧不能を記録'
check '[ ! -f "$D3B/state/direct_stream.paused" ]' 'restart_failed: それでも pause marker は解除される'
stop_worker "$D3B"

# --- 5. 意図的 pause 中は触れない ---
D4="$TMP/case_paused"
make_stubs "$D4"
touch "$D4/state/direct_stream.paused"
emit_status "$D4/status.json" 1 "$((NOON + 100 - 10800))"
launch_worker "$D4" ""
M4="$D4/state/stream_noon_audit/$(python3 -c "print(($NOON+32400)//86400)").json"
check 'wait_for_marker_field "$M4" "skipped_paused"' 'paused: skipped_paused を記録'
check '[ -f "$D4/state/direct_stream.paused" ]' 'paused: マーカーを消さない'
check '[ "$(calls_count "$D4")" = "0" ]' 'paused: stop/run を呼ばない'
stop_worker "$D4"

# --- 5b. TwitchセッションID不変ならVM再起動だけでは成功扱いにしない ---
D4B="$TMP/case_session_not_rotated"
make_stubs "$D4B"
touch "$D4B/run_no_flip"
emit_status "$D4B/status.json" 1 "$((NOON + 100 - 18000))"
emit_twitch_id "$D4B" "same-session"
RESPAWN_WAIT_SEC=2 launch_worker "$D4B" ""
M4B="$D4B/state/stream_noon_audit/$(python3 -c "print(($NOON+32400)//86400)").json"
# supervisor相当がローカル配信だけ張り直し、Twitch上のIDは旧ままのケース。
(
	for _ in $(seq 1 20); do
		grep -q '^stop$' "$D4B/calls.log" 2>/dev/null && break
		sleep 0.2
	done
	sleep 0.5
	emit_status "$D4B/status.json" 1 "$((NOON + 180))"
) &
session_sup_pid=$!
check 'wait_for_marker_field "$M4B" "restart_failed" 120' 'session_same: Twitch ID不変は失敗として記録'
check 'wait_for_marker_field "$M4B" "\"session_before\": \"same-session\""' 'session_same: 旧IDを記録'
check 'wait_for_marker_field "$M4B" "\"session_after\": \"same-session\""' 'session_same: 変更なしの後IDを記録'
kill "$session_sup_pid" 2>/dev/null || true
stop_worker "$D4B"

# --- 5c. Twitch OFFLINE 確認中は supervisor を抑止し、30秒相当の保持後に復帰 ---
D4C="$TMP/case_offline_hold"
make_stubs "$D4C"
emit_status "$D4C/status.json" 1 "$((NOON + 100 - 18000))"
emit_twitch_id "$D4C" "old-session"
(
	for _ in $(seq 1 40); do
		grep -q '^stop$' "$D4C/calls.log" 2>/dev/null && break
		sleep 0.2
	done
	rm -f "$D4C/twitch_id.txt"
	python3 -c 'import time; print(time.time())' >"$D4C/offline_started_at"
	for _ in $(seq 1 60); do
		[ ! -f "$D4C/state/direct_stream.paused" ] && break
		sleep 0.2
	done
	python3 -c 'import time; print(time.time())' >"$D4C/marker_removed_at"
	emit_status "$D4C/status.json" 1 "$((NOON + 190))"
	emit_twitch_id "$D4C" "held-new-session"
) &
offline_hold_sup_pid=$!
RESPAWN_WAIT_SEC=6 OFFLINE_HOLD_SEC=1 launch_worker "$D4C" ""
M4C="$D4C/state/stream_noon_audit/$(python3 -c "print(($NOON+32400)//86400)").json"
check 'wait_for_marker_field "$M4C" "\"outcome\": \"restarted\""' 'offline_hold: 保持後に張り直す'
check '[ "$(grep -c "^stop$" "$D4C/calls.log")" = "1" ]' 'offline_hold: stop は1回'
check '[ ! -f "$D4C/state/direct_stream.paused" ]' 'offline_hold: pause marker は解除される'
check '[ "$(grep -c "^run$" "$D4C/calls.log")" = "0" ]' 'offline_hold: 保持後は respawn を待つ'
check 'awk "BEGIN { getline removed < ARGV[1]; getline started < ARGV[2]; exit ((removed-started)>=1.05)?0:1 }" "$D4C/marker_removed_at" "$D4C/offline_started_at"' 'offline_hold: Twitch OFFLINE 後に marker を保持する'
check 'wait_for_marker_field "$M4C" "\"session_after\": \"held-new-session\""' 'offline_hold: 新Twitch ID を記録'
kill "$offline_hold_sup_pid" 2>/dev/null || true
stop_worker "$D4C"

# --- 6. 配信が止まっている場合は任せる ---
D5="$TMP/case_not_running"
make_stubs "$D5"
emit_status "$D5/status.json" 0
launch_worker "$D5" ""
M5="$D5/state/stream_noon_audit/$(python3 -c "print(($NOON+32400)//86400)").json"
check 'wait_for_marker_field "$M5" "skipped_not_running"' 'not_running: skipped_not_running を記録'
check '[ "$(calls_count "$D5")" = "0" ]' 'not_running: 触れない'
stop_worker "$D5"

# --- 7. started_at 不正は安全側スキップ ---
D6="$TMP/case_bad_status"
make_stubs "$D6"
printf '{"backend":"ffmpeg","running":true,"state":"running","pid":333}\n' >"$D6/status.json"
launch_worker "$D6" ""
M6="$D6/state/stream_noon_audit/$(python3 -c "print(($NOON+32400)//86400)").json"
check 'wait_for_marker_field "$M6" "skipped_bad_status"' 'bad_status: 安全側でスキップ'
check '[ "$(calls_count "$D6")" = "0" ]' 'bad_status: 触れない'
stop_worker "$D6"

# --- 8. 許容誤差境界 ---
D7="$TMP/case_boundary_ok"
make_stubs "$D7"
TOLERANCE_SEC=600 emit_status "$D7/status.json" 1 "$((NOON - 600))"
TOLERANCE_SEC=600 launch_worker "$D7" ""
M7="$D7/state/stream_noon_audit/$(python3 -c "print(($NOON+32400)//86400)").json"
check 'wait_for_marker_field "$M7" "no_action"' 'boundary: 差=600s ちょうどは許容'
stop_worker "$D7"

D8="$TMP/case_boundary_over"
make_stubs "$D8"
emit_status "$D8/status.json" 1 "$((NOON - 601))"
TOLERANCE_SEC=600 RESPAWN_WAIT_SEC=2 launch_worker "$D8" ""
M8="$D8/state/stream_noon_audit/$(python3 -c "print(($NOON+32400)//86400)").json"
check 'wait_for_marker_field "$M8" "restart_required|restarted"' 'boundary: 差=601s は是正対象'
check '[ "$(grep -c "^stop$" "$D8/calls.log")" -ge 1 ]' 'boundary: 是正で stop が走る'
stop_worker "$D8"

# --- 9. 翌日はまた監査する ---
D9="$TMP/case_next_day"
make_stubs "$D9"
emit_status "$D9/status.json" 1 "$((NOON - 60))"
launch_worker "$D9" ""
M9A="$D9/state/stream_noon_audit/$(python3 -c "print(($NOON+32400)//86400)").json"
wait_for_marker_field "$M9A" "no_action" || true
stop_worker "$D9"
printf '%s\n' "$((NOON_DAY2 + 100))" >"$D9/now.txt"
emit_status "$D9/status.json" 1 "$((NOON_DAY2 + 100 - 9000))"
launch_worker "$D9" ""
M9B="$D9/state/stream_noon_audit/$(python3 -c "print(($NOON_DAY2+32400)//86400)").json"
check 'wait_for_marker_field "$M9B" "restart_required|restarted"' 'next_day: 翌日のずれは別途是正される'
stop_worker "$D9"

# --- 10. disabled なら即座に抜ける ---
D10="$TMP/case_disabled"
make_stubs "$D10"
emit_status "$D10/status.json" 1 "$((NOON + 100 - 10800))"
launch_worker "$D10" "STREAM_NOON_AUDIT_ENABLED=0"
sleep 2
check '[ ! -f "$D10/state/stream_noon_audit" ] && [ -z "$(ls "$D10/state/stream_noon_audit" 2>/dev/null)" ]' 'disabled: 監査マーカーを作らない'
check '[ "$(calls_count "$D10")" = "0" ]' 'disabled: 何も呼ばない'
stop_worker "$D10"

printf '\n結果: ok=%d fail=%d\n' "$ok" "$fail"
[ "$fail" -eq 0 ]
