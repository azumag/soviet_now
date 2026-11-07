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

# --- 6. pause マーカーは「現在の所有者の」ものだけが意図的な停止を表す ---
# docich 切替後も tmp/state/direct_stream.paused は旧 supervisor の再起動抑止として残る (ADR 0007 §5)。
# それを運用者の一時停止と誤認して監査を永久にスキップしてはならない。
# 結果マーカー(JSON)の decision が出るまで待ち、decision の値で判定する。
decision_of() { python3 -c "import json,sys;print(json.load(open(sys.argv[1])).get('decision',''))" "$1" 2>/dev/null; }
run_paused_case() {
	# $1=名前 $2=.envの所有者行 $3=置くmarker $4=期待(skipped_paused|not_skipped)
	local name="$1" envline="$2" marker="$3" want="$4" dir="$TMP/pz_$1"
	make_tree "$dir" "$envline"
	[ -n "$marker" ] && printf '{"paused":true}\n' >"$dir/tmp/state/$marker.paused"
	launch "$dir"
	local mk="$dir/tmp/state/stream_noon_audit/$DAY.json"
	check 'wait_for "$mk" "\"decision\""' "$name: 監査が結果を記録する"
	local got; got=$(decision_of "$mk")
	if [ "$want" = skipped_paused ]; then
		check '[ "$got" = skipped_paused ]' "$name: 現在の所有者のmarkerなので skipped_paused (decision=$got)"
		check '[ ! -s "$dir/calls.log" ]' "$name: stop を呼ばない"
	else
		check '[ -n "$got" ] && [ "$got" != skipped_paused ]' "$name: 他方の所有者のmarkerは無視して監査する (decision=$got)"
	fi
	halt "$dir"
}
run_paused_case "docich+docichマーカー" "DOCICH_STREAM_OWNER=docich" docich_stream skipped_paused
run_paused_case "docich+旧マーカーだけ"  "DOCICH_STREAM_OWNER=docich" direct_stream   not_skipped
run_paused_case "soren+旧マーカー"       "DOCICH_STREAM_OWNER=soren"  direct_stream   skipped_paused
run_paused_case "soren+docichマーカーだけ" "DOCICH_STREAM_OWNER=soren" docich_stream   not_skipped

# 稼働中のワーカーが旧所有者(soren)で起動し、後から .env が docich に切り替わっても、
# 監査の直前に所有者を読み直す: 旧マーカーが残っていても skipped_paused にならない。
DX="$TMP/pz_switch_midrun"
make_tree "$DX" "DOCICH_STREAM_OWNER=soren"
printf '{"paused":true}\n' >"$DX/tmp/state/direct_stream.paused"
printf '%s\n' "$((NOON - 3600))" >"$DX/now.txt"            # 正午前: 監査はまだ走らない
launch "$DX"
sleep 2
printf 'DOCICH_STREAM_OWNER=docich\n' >"$DX/.env"          # 切替: .env を書き換える
printf '%s\n' "$NOON" >"$DX/now.txt"
MX="$DX/tmp/state/stream_noon_audit/$DAY.json"
check 'wait_for "$MX" "\"decision\""' 'midrun: 監査が結果を記録する'
gx=$(decision_of "$MX")
check '[ -n "$gx" ] && [ "$gx" != skipped_paused ]' "midrun: 起動時soren・切替後docich・旧marker残存でもスキップしない (decision=$gx)"
halt "$DX"

# 不正な所有者へ変わったら監査は配信に触れない
DB="$TMP/pz_bad_midrun"
make_tree "$DB" "DOCICH_STREAM_OWNER=soren"
printf '%s\n' "$((NOON - 3600))" >"$DB/now.txt"
launch "$DB"
sleep 2
printf 'DOCICH_STREAM_OWNER=Docich\n' >"$DB/.env"
printf '%s\n' "$NOON" >"$DB/now.txt"
MB="$DB/tmp/state/stream_noon_audit/$DAY.json"
check 'wait_for "$MB" "\"decision\""' 'bad-midrun: 監査が結果を記録する'
check '[ "$(decision_of "$MB")" = skipped_bad_owner ]' 'bad-midrun: 不正な所有者は skipped_bad_owner'
check '[ ! -s "$DB/calls.log" ]' 'bad-midrun: 配信に触れない'
halt "$DB"

printf '\n結果: ok=%d fail=%d\n' "$ok" "$fail"
[ "$fail" -eq 0 ]
