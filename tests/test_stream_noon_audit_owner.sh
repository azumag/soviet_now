#!/usr/bin/env bash
# stream_noon_audit のencoder所有者対応 (docich ADR 0007) の回帰テスト。
# - 既定(soren)は従来のstop/run/pause markerのまま
# - docich所有ならstream_supervisor stop、docich pause marker、自前起動しない
# - 所有者は張り直しの直前に .env から読み直す (稼働中に切替えても追従)
# - 不正値は配信に触れない
set -u

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

ok=0
fail=0
check() {
	if eval "$1"; then
		printf 'ok - %s\n' "$2"
		ok=$((ok + 1))
	else
		printf 'not ok - %s\n' "$2"
		fail=$((fail + 1))
	fi
}

NOON=$(python3 -c 'import time;t=int(time.time());print((t+32400)//86400*86400+43200-32400)')
DAY=$(python3 -c "print(($NOON+32400)//86400)")

# --- 1. 既定値の表 (lib/stream_owner_commands.sh) ---
# shellcheck source=lib/stream_owner_commands.sh
source "$ROOT/lib/stream_owner_commands.sh"
stream_owner_defaults soren /r /d
check '[ "$STREAM_OWNER_STOP_CMD" = "python3 lib/direct_stream.py stop" ] && [ "$STREAM_OWNER_RUN_CMD" = "./direct_stream.sh run" ] && [ "$STREAM_OWNER_PAUSE_MARKER" = "tmp/state/direct_stream.paused" ]' 'soren: 従来のコマンドとマーカー'
stream_owner_defaults "" /r /d
check '[ "$STREAM_OWNER_STOP_CMD" = "python3 lib/direct_stream.py stop" ]' '空は既定のsoren'
stream_owner_defaults docich /srv/soren /srv/docich
check '[ "$STREAM_OWNER_STOP_CMD" = "env PYTHONPATH=/srv/docich/src python3 -m docich.stream_supervisor --root /srv/soren stop" ]' 'docich: stream_supervisor stop (--root と PYTHONPATH を明示)'
check '[ "$STREAM_OWNER_RUN_CMD" = "true" ] && [ "$STREAM_OWNER_PAUSE_MARKER" = "tmp/state/docich_stream.paused" ]' 'docich: 自前起動なし・docichのマーカー'
for bad in Docich " docich" "soren " both 1; do
	check '! stream_owner_defaults "$bad" /r /d' "不正値 [$bad] は拒否"
done

# --- ワーカーを一時ツリーへ複製して実行する (相対パスが一時ツリーに落ちる) ---
make_tree() {
	local dir="$1" owner_line="$2"
	mkdir -p "$dir/workers" "$dir/lib" "$dir/tmp/state" "$dir/logs" "$dir/bin" "$dir/fakedocich/src/docich"
	cp "$ROOT/workers/stream_noon_audit.sh" "$dir/workers/"
	cp "$ROOT/lib/poll_wait.sh" "$ROOT/lib/stream_owner_commands.sh" "$dir/lib/"
	[ -n "$owner_line" ] && printf '%s\n' "$owner_line" >"$dir/.env"
	: >"$dir/calls.log"
	printf '%s\n' "$NOON" >"$dir/now.txt"
	printf '{"backend":"ffmpeg","running":true,"state":"running","pid":111,"started_at":%s}\n' "$((NOON - 6 * 3600))" >"$dir/status.json"
	cat >"$dir/bin/now.sh" <<NOWEOF
#!/usr/bin/env bash
cat "$dir/now.txt"
NOWEOF
	cat >"$dir/bin/status.sh" <<STATEOF
#!/usr/bin/env bash
cat "$dir/status.json"
STATEOF
	cat >"$dir/bin/twitch.sh" <<'TWEOF'
#!/usr/bin/env bash
printf '{"data":{"user":null}}\n'
TWEOF
	# legacy stop (lib/direct_stream.py stop) and the docich stop both flip the status to
	# stopped, record what they saw, then emulate the owner's respawn one second later.
	cat >"$dir/lib/direct_stream.py" <<LEGEOF
import os, subprocess, sys
base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
open(os.path.join(base, "calls.log"), "a").write("legacy-stop marker=%s\n" % os.path.exists(os.path.join(base, "tmp/state/direct_stream.paused")))
open(os.path.join(base, "status.json"), "w").write('{"backend":"ffmpeg","running":false,"state":"stopped"}\n')
subprocess.Popen([sys.executable, "-c", "import time,sys;time.sleep(1);open(sys.argv[1],'w').write(sys.argv[2])",
  os.path.join(base, "status.json"), '{"backend":"ffmpeg","running":true,"state":"running","pid":222,"started_at":%d}\n' % ($NOON + 130)], start_new_session=True)
LEGEOF
	touch "$dir/fakedocich/src/docich/__init__.py"
	cat >"$dir/fakedocich/src/docich/stream_supervisor.py" <<DOCEOF
import os, subprocess, sys
argv = sys.argv[1:]
root = argv[argv.index("--root") + 1]
open(os.path.join(root, "calls.log"), "a").write("docich-stop argv=%s docich_marker=%s legacy_marker=%s\n" % (
  " ".join(argv), os.path.exists(os.path.join(root, "tmp/state/docich_stream.paused")),
  os.path.exists(os.path.join(root, "tmp/state/direct_stream.paused"))))
open(os.path.join(root, "status.json"), "w").write('{"backend":"ffmpeg","running":false,"state":"stopped"}\n')
subprocess.Popen([sys.executable, "-c", "import time,sys;time.sleep(1);open(sys.argv[1],'w').write(sys.argv[2])",
  os.path.join(root, "status.json"), '{"backend":"ffmpeg","running":true,"state":"running","pid":222,"started_at":%d}\n' % ($NOON + 130)], start_new_session=True)
DOCEOF
	chmod +x "$dir/bin/"*.sh
}

launch() {
	local dir="$1"
	(
		export STREAM_NOON_AUDIT_PID_FILE="$dir/tmp/state/noon.pid"
		export STREAM_NOON_AUDIT_STATE_DIR="$dir/tmp/state/stream_noon_audit"
		export STREAM_NOON_AUDIT_LOG_FILE="$dir/logs/direct_stream.log"
		export STREAM_NOON_AUDIT_POLL_SEC=1 STREAM_NOON_AUDIT_RESPAWN_WAIT_SEC=8 STREAM_NOON_AUDIT_STOP_TIMEOUT_SEC=3
		export STREAM_NOON_AUDIT_OFFLINE_HOLD_SEC=0 STREAM_NOON_AUDIT_SESSION_ROTATE_WAIT_SEC=2
		export STREAM_NOON_AUDIT_NOW_CMD="bash $dir/bin/now.sh" STREAM_NOON_AUDIT_STATUS_CMD="bash $dir/bin/status.sh"
		export STREAM_NOON_AUDIT_GQL_CMD="bash $dir/bin/twitch.sh" DOCICH_ROOT="$dir/fakedocich"
		unset STREAM_NOON_AUDIT_STOP_CMD STREAM_NOON_AUDIT_RUN_CMD STREAM_NOON_AUDIT_STREAM_PAUSE_MARKER DOCICH_STREAM_OWNER
		cd "$dir" || exit 1
		bash workers/stream_noon_audit.sh >>"$dir/worker.log" 2>&1 &
		echo $! >"$dir/worker.pid"
	)
}

halt() {
	local pid
	pid=$(cat "$1/worker.pid" 2>/dev/null) || return 0
	kill "$pid" 2>/dev/null || true
	for _ in $(seq 1 20); do kill -0 "$pid" 2>/dev/null || return 0; sleep 0.25; done
}

wait_for() {
	local file="$1" pattern="$2" i=0
	while [ $i -lt "${3:-80}" ]; do
		grep -qE "$pattern" "$file" 2>/dev/null && return 0
		sleep 0.5
		i=$((i + 1))
	done
	return 1
}

# --- 2. 既定 (所有者未設定) = 従来経路 ---
D1="$TMP/legacy"
make_tree "$D1" ""
launch "$D1"
M1="$D1/tmp/state/stream_noon_audit/$DAY.json"
check 'wait_for "$M1" "\"outcome\": \"restarted\""' 'soren: 張り直しが完了する'
check 'grep -q "^legacy-stop marker=True" "$D1/calls.log"' 'soren: lib/direct_stream.py stop を旧pause marker付きで呼ぶ'
check '! grep -q "docich-stop" "$D1/calls.log"' 'soren: docichのstopは呼ばない'
check '[ ! -f "$D1/tmp/state/direct_stream.paused" ] && [ ! -f "$D1/tmp/state/docich_stream.paused" ]' 'soren: markerは解除済み'
halt "$D1"

# --- 3. docich所有 ---
D2="$TMP/docich"
make_tree "$D2" "DOCICH_STREAM_OWNER=docich"
launch "$D2"
M2="$D2/tmp/state/stream_noon_audit/$DAY.json"
check 'wait_for "$M2" "\"outcome\": \"restarted\""' 'docich: 張り直しが完了する'
check 'grep -q "^docich-stop argv=.*--root $D2 stop docich_marker=True legacy_marker=False" "$D2/calls.log"' 'docich: stream_supervisor stop をdocichのmarkerだけで呼ぶ'
check '! grep -q "legacy-stop" "$D2/calls.log"' 'docich: 旧 lib/direct_stream.py stop は呼ばない'
check '[ ! -f "$D2/tmp/state/docich_stream.paused" ] && [ ! -f "$D2/tmp/state/direct_stream.paused" ]' 'docich: markerは解除済み・旧markerは作らない'
check '[ "$(grep -c "^docich-stop" "$D2/calls.log")" = "1" ]' 'docich: stopは1回だけ'
halt "$D2"

# --- 4. 稼働中に所有者が切り替わっても張り直しの直前に読み直す ---
D3="$TMP/switch"
make_tree "$D3" "DOCICH_STREAM_OWNER=soren"
printf '%s\n' "$((NOON - 3600))" >"$D3/now.txt"          # 正午前: 監査は走らない
launch "$D3"
sleep 2
printf 'DOCICH_STREAM_OWNER=docich\n' >"$D3/.env"        # 引き継ぎで .env を書き換える
printf '%s\n' "$NOON" >"$D3/now.txt"
M3="$D3/tmp/state/stream_noon_audit/$DAY.json"
check 'wait_for "$M3" "\"outcome\": \"restarted\""' 'switch: 切替後の監査が完了する'
check 'grep -q "^docich-stop" "$D3/calls.log" && ! grep -q "legacy-stop" "$D3/calls.log"' 'switch: 起動時はsorenでも張り直しはdocich経路になる'
halt "$D3"

# --- 5. 不正な所有者 ---
D4="$TMP/invalid"
make_tree "$D4" "DOCICH_STREAM_OWNER=Docich"
launch "$D4"
sleep 2
check '! kill -0 "$(cat "$D4/worker.pid")" 2>/dev/null' 'invalid: ワーカーは起動せず終了する'
check '[ ! -s "$D4/calls.log" ] && [ ! -d "$D4/tmp/state/stream_noon_audit" ]' 'invalid: 配信に触れない・監査マーカーも作らない'
check 'grep -q "DOCICH_STREAM_OWNER は soren か docich" "$D4/worker.log"' 'invalid: 理由をログに残す'
halt "$D4"

# --- 6. どちらの所有者のpauseも「意図的な停止」として尊重する ---
for marker in direct_stream docich_stream; do
	DP="$TMP/paused_$marker"
	make_tree "$DP" "DOCICH_STREAM_OWNER=docich"
	printf '{"paused":true}\n' >"$DP/tmp/state/$marker.paused"
	launch "$DP"
	check 'wait_for "$DP/tmp/state/stream_noon_audit/$DAY.json" "skipped_paused"' "paused($marker): 監査は skipped_paused で触れない"
	check '[ ! -s "$DP/calls.log" ]' "paused($marker): stop を呼ばない"
	halt "$DP"
done

printf '\n結果: ok=%d fail=%d\n' "$ok" "$fail"
[ "$fail" -eq 0 ]
