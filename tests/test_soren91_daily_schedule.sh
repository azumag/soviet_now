#!/bin/bash
set -u

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
TEST_TMP="$(mktemp -d)"
trap 'rm -rf "$TEST_TMP"' EXIT

pass=0
fail=0
ok() { pass=$((pass + 1)); printf 'ok %s - %s\n' "$pass" "$1"; }
not_ok() { fail=$((fail + 1)); printf 'not ok %s - %s\n' "$((pass + fail))" "$1"; }
assert() {
	local label="$1"
	shift
	if "$@"; then ok "$label"; else not_ok "$label"; fi
}
not_daily_should_start() { ! soren91_daily_should_start; }

export ELOOP_LIB_DIR="$TEST_TMP/root"
export TMP_STATE_DIR="$TEST_TMP/state"
export SOREN91_ENABLED=1
export SOREN91_DAILY_ENABLED=1
export SOREN91_DAILY_EARLIEST_HOUR=8
export SOREN91_DAILY_LATEST_START_HOUR=22
export SOREN91_DAILY_DURATION_SEC=120
export SOREN91_DAILY_RETRY_SEC=60
export SOREN91_DAILY_MAX_ATTEMPTS=2
export SOREN91_DAILY_STATE_FILE="$TMP_STATE_DIR/soren91_daily.json"
mkdir -p "$ELOOP_LIB_DIR" "$TMP_STATE_DIR"
log() { :; }

# shellcheck source=../soren91_control.sh
source "$ROOT_DIR/soren91_control.sh"

due1=$(_soren91_daily_ensure_plan)
due2=$(_soren91_daily_ensure_plan)
assert "同じ日の予定時刻は再生成しない" test "$due1" = "$due2"
assert "予定状態を保存する" python3 -c 'import json,sys; d=json.load(open(sys.argv[1])); raise SystemExit(0 if d["status"]=="planned" and d["attempts"]==0 else 1)' "$SOREN91_DAILY_STATE_FILE"

python3 - "$SOREN91_DAILY_STATE_FILE" <<'PY'
import json, sys, time
p=sys.argv[1]; d=json.load(open(p)); d['due_epoch']=int(time.time())-1; json.dump(d,open(p,'w'))
PY
assert "予定時刻後は開始対象になる" soren91_daily_should_start
before=$(date +%s)
end_epoch=$(soren91_daily_begin)
after=$(date +%s)
assert "開始時刻から指定秒数の終了時刻を返す" test "$end_epoch" -ge "$((before + 120))"
assert "終了時刻は実行時間の上限内" test "$end_epoch" -le "$((after + 120))"
assert "running状態は再起動後も読める" test "$(soren91_daily_active_end_epoch)" = "$end_epoch"

soren91_daily_mark_failed
assert "初回失敗は再試行予定へ戻す" python3 -c 'import json,sys,time; d=json.load(open(sys.argv[1])); raise SystemExit(0 if d["status"]=="planned" and d["attempts"]==1 and d["due_epoch"]>time.time() else 1)' "$SOREN91_DAILY_STATE_FILE"
python3 - "$SOREN91_DAILY_STATE_FILE" <<'PY'
import json, sys, time
p=sys.argv[1]; d=json.load(open(p)); d['due_epoch']=int(time.time())-1; json.dump(d,open(p,'w'))
PY
soren91_daily_begin >/dev/null
soren91_daily_mark_failed
assert "最大回数の失敗後はその日の再試行を止める" python3 -c 'import json,sys; d=json.load(open(sys.argv[1])); raise SystemExit(0 if d["status"]=="failed" and d["attempts"]==2 else 1)' "$SOREN91_DAILY_STATE_FILE"
assert "failed状態は開始対象にならない" not_daily_should_start

python3 - "$SOREN91_DAILY_STATE_FILE" <<'PY'
import json, sys
p=sys.argv[1]; d=json.load(open(p)); d['local_day']='2000-01-01'; json.dump(d,open(p,'w'))
PY
_soren91_daily_ensure_plan >/dev/null
assert "日付が変わると新しいplanned状態になる" python3 -c 'import json,sys; d=json.load(open(sys.argv[1])); raise SystemExit(0 if d["local_day"]!="2000-01-01" and d["status"]=="planned" and d["attempts"]==0 else 1)' "$SOREN91_DAILY_STATE_FILE"
soren91_daily_mark_completed
assert "完了後は同じ日に二重発火しない" not_daily_should_start

assert "共有Chromeの隔離コンテキスト設定をrunnerへ渡す" grep -q 'SOREN91_SHARED_ISOLATED_CONTEXT' "$ROOT_DIR/soren91_control.sh"
assert "VMの実CDPポートをrunnerへ渡す" grep -q "SOREN_CDP_PORT='\${SOREN_CDP_PORT:-9222}'" "$ROOT_DIR/soren91_control.sh"
assert "低解像度viewportをrunnerへ渡す" grep -q 'SOREN91_VIEWPORT_WIDTH' "$ROOT_DIR/soren91_control.sh"
assert "Soren91窓をCDPで全画面化する" grep -q "Browser.setWindowBounds" "$ROOT_DIR/soren91/main.mjs"
assert "古いPIDはSoren91所有確認後だけ停止する" grep -q '_soren91_pid_is_owned_player' "$ROOT_DIR/soren91_control.sh"
assert "Soren91を共通ゲームエリアへ配置する" grep -q 'installDirectGameStage' "$ROOT_DIR/soren91/main.mjs"
assert "Soren91中は通常ゲーム描画を凍結する" grep -q "setNormalGameLifecycle(browser, 'frozen')" "$ROOT_DIR/soren91/main.mjs"
assert "公開ゲームからVMローカルoverlayへ直接fetchしない" grep -q 'installInlineDirectBroadcastOverlay' "$ROOT_DIR/soren91/main.mjs"
assert "日次枠はゲーム画面readyを待つ" grep -q 'soren91_wait_ready' "$ROOT_DIR/soren_loop.sh"
assert "Soren91本体がready markerを書く" grep -q 'Soren91 ready marker written' "$ROOT_DIR/soren91/main.mjs"
assert "隔離コンテキスト終了時は共有ゲーム全体を掃除しない" python3 - "$ROOT_DIR/soren91/main.mjs" <<'PY'
import sys
s=open(sys.argv[1], encoding='utf-8').read()
needle='if (ownsContext && context) {'
i=s.index(needle)
block=s[i:s.index('// soren91 is a GUEST', i)]
raise SystemExit(0 if 'context.close()' in block and '} else {' in block and 'closeSharedSoren91Pages' in block.split('} else {',1)[1] else 1)
PY

printf 'passed=%s failed=%s\n' "$pass" "$fail"
test "$fail" -eq 0
