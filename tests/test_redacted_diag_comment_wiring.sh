#!/usr/bin/env bash
# tests/test_redacted_diag_comment_wiring.sh - docich#33
#
# End-to-end test of the actual production wiring: broadcast/comment.sh's
# _ingest_stream_bug_reports_redacted() / _maybe_gc_redacted_diag_spool(),
# sourced and called exactly as chat_worker.sh/youtube_worker.sh would.
#
# Verifies (against fixture dirs only, never the real tmp/diag_* state):
#   (a) event記録に生本文/user名が入らない
#   (b) 生本文はrestricted spoolにのみ0600で入る
#   (c) spoolエントリがTTL経過後にgcで実際に消える
#   (d) この経路からCoding Agent(codex/claude)が0回起動する
#   (e) CODEX_BUG_DISPATCH_ENABLED(#32既定0)の値に関わらずingestionは動く
#      (=2つのフラグが独立している)
#   (f) ingestionが失敗しても呼び出し元(コメント処理)は壊れない(fail-open)
#   (g) 通常カテゴリ(stream_bug_report以外)は記録されない
set -u

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TMP="$(mktemp -d)"
trap 'chmod -R u+rwx "$TMP" 2>/dev/null; rm -rf "$TMP"' EXIT

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

COMMENT="$ROOT/broadcast/comment.sh"
CONFIG="$ROOT/core/config.sh"

# ---------------------------------------------------------------------------
# static: wiring exists, and the ingestion path never references the
# diagnostic runner/agent (recording only, no auto-diagnosis).
# ---------------------------------------------------------------------------
check "grep -q '_ingest_stream_bug_reports_redacted' '$COMMENT'" \
	"comment.shに_ingest_stream_bug_reports_redacted呼び出しが配線されている"
check "grep -q 'REDACTED_DIAG_INGEST_ENABLED' '$CONFIG'" \
	"core/config.shにREDACTED_DIAG_INGEST_ENABLEDの既定値が定義されている"
check "grep -q 'REDACTED_DIAG_INGEST_ENABLED:-1' '$CONFIG'" \
	"REDACTED_DIAG_INGEST_ENABLEDの既定は1(event記録+spool分離は既定で有効)"
check "! grep -q 'diagnostics_runner' '$COMMENT'" \
	"comment.shはdiagnostics_runner.shを一切呼ばない(記録のみ、自動診断はしない)"
check "! grep -q 'diagnostics_runner' '$CONFIG'" \
	"core/config.shもdiagnostics_runner.shに触れない"

# ---------------------------------------------------------------------------
# spy for codex/claude: 起動されたら検知する(#32のテストと同じ手法)
# ---------------------------------------------------------------------------
SPY_LOG="$TMP/spy.log"
: >"$SPY_LOG"
SPYBIN="$TMP/spybin"
mkdir -p "$SPYBIN"
for fake in codex claude; do
	cat >"$SPYBIN/$fake" <<EOF
#!/bin/sh
echo "SPAWNED:\$0:\$*" >>"$SPY_LOG"
exit 1
EOF
	chmod +x "$SPYBIN/$fake"
done
export PATH="$SPYBIN:$PATH"

SENTINEL="SENTINEL_TOKEN_DO_NOT_LEAK_wiring_$$"
VIEWER_USER="viewer_wiring_test_$$"

_run_ingest() {
	# args: events_dir spool_dir dispatch_enabled ingest_enabled classification_json source
	local events_dir="$1" spool_dir="$2" dispatch_enabled="$3" ingest_enabled="$4" classification="$5" source="$6"
	ELOOP_LIB_DIR="$ROOT" \
		CODEX_BUG_DISPATCH_ENABLED="$dispatch_enabled" \
		REDACTED_DIAG_INGEST_ENABLED="$ingest_enabled" \
		REDACTED_DIAG_EVENTS_DIR="$events_dir" \
		REDACTED_DIAG_SPOOL_DIR="$spool_dir" \
		REDACTED_DIAG_SPOOL_TTL_SEC="${REDACTED_DIAG_SPOOL_TTL_SEC_OVERRIDE:-86400}" \
		bash -c '
			source "'"$ROOT"'/lib/outbound_queue.sh"
			source "'"$COMMENT"'"
			_ingest_stream_bug_reports_redacted "$1" "$2"
		' _ "$classification" "$source"
}

# ---------------------------------------------------------------------------
# A. 正常系: stream_bug_report分類のコメントが実際の分類経路(同じ
#    classification_json形式)を通ってevent+spoolに分離される。
#    CODEX_BUG_DISPATCH_ENABLED=0(#32の既定)でも動くことを確認 = 要件(e)。
# ---------------------------------------------------------------------------
EVENTS_A="$TMP/events_a"
SPOOL_A="$TMP/spool_a"
classification_a=$(python3 - "$SENTINEL" "$VIEWER_USER" <<'PY'
import json, sys
sentinel, user = sys.argv[1:3]
rows = [
    {"index": 1, "user": user, "comment": f"配信の音が急に無音になった、直して。{sentinel}", "category": "stream_bug_report", "is_english": False},
    {"index": 2, "user": "someone_else", "comment": "こんにちは配信お疲れ様です", "category": "chitchat", "is_english": False},
]
print(json.dumps(rows, ensure_ascii=False))
PY
)
event_ids_a=$(_run_ingest "$EVENTS_A" "$SPOOL_A" "0" "1" "$classification_a" "twitch")
check "[ -n \"$event_ids_a\" ]" "正常系: CODEX_BUG_DISPATCH_ENABLED=0(#32既定)でもevent_idが返る (実測: ${event_ids_a:-<empty>})"

event_count_a=$(find "$EVENTS_A" -maxdepth 1 -type f -name '*.json' 2>/dev/null | wc -l | tr -d ' ')
check "[ \"$event_count_a\" -eq 1 ]" "(g) stream_bug_report 1件のみevent化される(chitchatは対象外) (実測: ${event_count_a}件)"

event_file_a=$(find "$EVENTS_A" -maxdepth 1 -type f -name '*.json' | head -1)
event_blob_a=$(cat "$event_file_a" 2>/dev/null)
check "! printf '%s' \"\$event_blob_a\" | grep -q \"$SENTINEL\"" "(a) eventファイルに生comment本文(sentinel)が入らない"
check "! printf '%s' \"\$event_blob_a\" | grep -q \"$VIEWER_USER\"" "(a) eventファイルにuser名が入らない"
check "printf '%s' \"\$event_blob_a\" | grep -q 'redacted_context_hash'" "(a) eventファイルにredacted_context_hashは記録される"
check "printf '%s' \"\$event_blob_a\" | grep -q '\"category\": \"stream_bug_report\"'" "eventにcategoryが記録される"

spool_file_a=$(find "$SPOOL_A" -maxdepth 1 -type f -name '*.json' | head -1)
check "[ -n \"$spool_file_a\" ]" "spoolファイルが1件作られる"
spool_blob_a=$(cat "$spool_file_a" 2>/dev/null)
check "printf '%s' \"\$spool_blob_a\" | grep -q \"$SENTINEL\"" "(b) 生comment本文はspoolに入っている(恒久保存ではなく短期TTL)"
check "printf '%s' \"\$spool_blob_a\" | grep -q \"$VIEWER_USER\"" "(b) user名もspoolに入っている"
spool_perm_a=$(stat -f '%Lp' "$spool_file_a" 2>/dev/null || stat -c '%a' "$spool_file_a")
check "[ \"$spool_perm_a\" = \"600\" ]" "(b) spoolファイルは0600 (実測: $spool_perm_a)"

spawn_count_a=$(wc -l <"$SPY_LOG" | tr -d ' ')
check "[ \"$spawn_count_a\" -eq 0 ]" "(d) この経路でcodex/claudeが0回起動 (実測: ${spawn_count_a}回)"
check "! find \"$TMP\" -name 'prompt_*' 2>/dev/null | grep -q ." "(d) agentへ渡すprompt成果物も一切生成されない"

# ---------------------------------------------------------------------------
# B. TTL: 短いTTLで記録したエントリがgcで実際に消える(恒久保存にならない)。
# ---------------------------------------------------------------------------
EVENTS_B="$TMP/events_b"
SPOOL_B="$TMP/spool_b"
classification_b=$(python3 - "$SENTINEL" <<'PY'
import json, sys
sentinel = sys.argv[1]
rows = [{"index": 1, "user": "viewer_ttl", "comment": f"配信固まった {sentinel}", "category": "stream_bug_report", "is_english": False}]
print(json.dumps(rows, ensure_ascii=False))
PY
)
REDACTED_DIAG_SPOOL_TTL_SEC_OVERRIDE=5 event_ids_b=$(REDACTED_DIAG_SPOOL_TTL_SEC_OVERRIDE=5 _run_ingest "$EVENTS_B" "$SPOOL_B" "0" "1" "$classification_b" "twitch")
spool_count_before=$(find "$SPOOL_B" -maxdepth 1 -type f -name '*.json' 2>/dev/null | wc -l | tr -d ' ')
check "[ \"$spool_count_before\" -eq 1 ]" "TTLテスト: gc前はspoolエントリが1件存在する"

# 実際のcronは待たず、purge_expiredの--nowでTTL経過後の時刻を注入する
# (redacted_diag_spool_gc.pyの正規の削除ロジックそのものを呼ぶ)。
future_ts=$(($(date +%s) + 3600))
python3 "$ROOT/lib/redacted_diag_spool_gc.py" --spool-dir "$SPOOL_B" --now "$future_ts" >/dev/null 2>&1
spool_count_after=$(find "$SPOOL_B" -maxdepth 1 -type f -name '*.json' 2>/dev/null | wc -l | tr -d ' ')
check "[ \"$spool_count_after\" -eq 0 ]" "(c) TTL経過後にgcを実行すると生comment本文入りのspoolエントリが実際に消える (実測残: ${spool_count_after}件)"

# _maybe_gc_redacted_diag_spool()自体もopportunisticにgcを実行できることを確認
EVENTS_B2="$TMP/events_b2"
SPOOL_B2="$TMP/spool_b2"
GC_LAST_B2="$TMP/state_b2/gc_last.ts"
mkdir -p "$SPOOL_B2"
python3 - "$SPOOL_B2/already_expired.json" "$SENTINEL" <<'PY'
import json, sys, time
path, sentinel = sys.argv[1:3]
data = {"event_id": "preexpired", "user": "v", "comment": sentinel, "expires_at": int(time.time()) - 100}
open(path, "w").write(json.dumps(data))
PY
ELOOP_LIB_DIR="$ROOT" REDACTED_DIAG_INGEST_ENABLED=1 \
	REDACTED_DIAG_SPOOL_DIR="$SPOOL_B2" REDACTED_DIAG_SPOOL_GC_LAST_FILE="$GC_LAST_B2" \
	REDACTED_DIAG_SPOOL_GC_INTERVAL_SEC=0 \
	bash -c '
		source "'"$ROOT"'/lib/outbound_queue.sh"
		source "'"$COMMENT"'"
		_maybe_gc_redacted_diag_spool
	'
remaining_b2=$(find "$SPOOL_B2" -maxdepth 1 -type f -name '*.json' 2>/dev/null | wc -l | tr -d ' ')
check "[ \"$remaining_b2\" -eq 0 ]" "(c) comment.shの_maybe_gc_redacted_diag_spoolを直接呼んでもTTL失効エントリが消える (実測残: ${remaining_b2}件)"
spawn_count_gc=$(wc -l <"$SPY_LOG" | tr -d ' ')
check "[ \"$spawn_count_gc\" -eq 0 ]" "(d) gc経路でもcodex/claudeが0回起動 (実測: ${spawn_count_gc}回)"

# ---------------------------------------------------------------------------
# C. fail-open: ingestionが失敗してもコメント処理本体(このシェル)は死なない。
# ---------------------------------------------------------------------------
malformed_out=$(_run_ingest "$TMP/events_c1" "$TMP/spool_c1" "0" "1" "not valid json {{{" "twitch" 2>&1; echo "RC=$?")
check "printf '%s' \"\$malformed_out\" | grep -q 'RC=0'" \
	"(f) fail-open: 不正なclassification JSONでもingestion呼び出し自体は非0で落ちない"

unwritable_out=$(_run_ingest "/nonexistent_root_only_dir_$$/events" "$TMP/spool_c2" "0" "1" "$classification_b" "twitch" 2>&1; echo "RC=$?")
check "printf '%s' \"\$unwritable_out\" | grep -q 'RC=0'" \
	"(f) fail-open: 書き込み不能なevents_dirでもingestion呼び出し自体は非0で落ちない"

# ---------------------------------------------------------------------------
# D. 無効化: REDACTED_DIAG_INGEST_ENABLED=0で記録自体を止められる(運用の
#    エスケープハッチ)。この時もagent起動には無関係(常にfalseのはず)。
# ---------------------------------------------------------------------------
EVENTS_D="$TMP/events_d"
_run_ingest "$EVENTS_D" "$TMP/spool_d" "0" "0" "$classification_b" "twitch" >/dev/null 2>&1
check "[ ! -d \"$EVENTS_D\" ]" "REDACTED_DIAG_INGEST_ENABLED=0のときはevents_dirすら作られない(記録を止められる)"

printf '%s passed, %s failed\n' "$ok" "$fail"
[ "$fail" -eq 0 ]
