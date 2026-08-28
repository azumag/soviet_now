#!/bin/bash
# workers/poll_worker.sh - Twitch 自動アンケートコーナー (docich#8)

set -o pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$SCRIPT_DIR"
[ -f .env ] && set -a && . ./.env && set +a
source ./eloop_lib.sh

WORKER_NAME="poll_worker"
PID_FILE="tmp/state/${WORKER_NAME}.pid"
PAUSE_FILE="tmp/state/${WORKER_NAME}.paused"
STATE_FILE="${TWITCH_POLL_STATE_FILE:-tmp/state/current_poll.json}"
SCHEDULE_FILE="${TWITCH_POLL_SCHEDULE_FILE:-tmp/state/poll_schedule.json}"
HISTORY_FILE="${TWITCH_POLL_HISTORY_FILE:-tmp/history/polls.jsonl}"
POLL_INTERVAL="${TWITCH_POLL_WORKER_INTERVAL:-10}"
_STOPPED=0
_RELOAD_REQUESTED=0

_log() { echo "[${WORKER_NAME} $(date '+%H:%M:%S')] $*"; }

_pid_alive() {
	local pid="${1:-}" err=""
	case "$pid" in ''|*[!0-9]*) return 1 ;; esac
	err=$( { kill -0 "$pid" >/dev/null; } 2>&1 ) && return 0
	case "$err" in *"operation not permitted"*|*"Operation not permitted"*) return 0 ;; esac
	return 1
}

_cleanup() {
	[ "$_STOPPED" -eq 1 ] && return
	_STOPPED=1
	local active_pid=""
	active_pid=$(cat "$PID_FILE" 2>/dev/null || true)
	[ "$active_pid" = "$$" ] && rm -f "$PID_FILE"
}
_handle_signal() { _cleanup; trap - EXIT; exit 130; }
_request_reload() { _RELOAD_REQUESTED=1; }
_reload_runtime() {
	[ "$_RELOAD_REQUESTED" -eq 1 ] || return 0
	_RELOAD_REQUESTED=0
	[ -f .env ] && set -a && . ./.env && set +a
	source ./eloop_lib.sh 2>/dev/null || true
	POLL_INTERVAL="${TWITCH_POLL_WORKER_INTERVAL:-10}"
	_log "reload complete (interval=${POLL_INTERVAL}s enabled=${TWITCH_POLLS_ENABLED:-0})"
}
trap '_cleanup' EXIT
trap '_handle_signal' INT TERM
trap '_request_reload' HUP USR1

if [ -f "$PAUSE_FILE" ]; then _log "paused by $PAUSE_FILE → exit"; exit 0; fi
if [ -f "$PID_FILE" ]; then
	old_pid=$(cat "$PID_FILE" 2>/dev/null || true)
	if _pid_alive "$old_pid"; then _log "ERROR: 既に起動中 (PID=$old_pid)"; exit 1; fi
	rm -f "$PID_FILE"
fi
mkdir -p "$(dirname "$PID_FILE")" "$(dirname "$HISTORY_FILE")"
echo $$ >"$PID_FILE"

_json_ok() {
	python3 -c 'import json,sys; print("1" if json.load(sys.stdin).get("ok") else "0")' 2>/dev/null
}

_json_field() {
	local path="$1"
	python3 -c 'import json,sys
d=json.load(sys.stdin)
for key in sys.argv[1].split("."):
    d=d.get(key) if isinstance(d,dict) else None
print("" if d is None else d)' "$path" 2>/dev/null
}

_next_run_at() {
	python3 - "$SCHEDULE_FILE" <<'PY' 2>/dev/null
import json, sys
try: print(int(json.load(open(sys.argv[1])).get("next_run_at", 0) or 0))
except Exception: print(0)
PY
}

_schedule_after() {
	local delay="$1" reason="${2:-scheduled}"
	python3 - "$SCHEDULE_FILE" "$delay" "$reason" <<'PY'
import json, os, sys, time
path, delay, reason = sys.argv[1:4]
data = {"next_run_at": int(time.time()) + max(1, int(delay)), "reason": reason}
tmp = path + ".tmp"
with open(tmp, "w", encoding="utf-8") as f: json.dump(data, f, ensure_ascii=False)
os.replace(tmp, path)
PY
}

_normalize_poll_draft() {
	python3 - "$1" "$2" <<'PY'
import json, re, sys
raw, output = sys.argv[1:3]
raw = raw.strip()
raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.I)
raw = re.sub(r"\s*```$", "", raw)
try: d = json.loads(raw)
except Exception: raise SystemExit(1)
title = " ".join(str(d.get("title", "")).split())
choices = [" ".join(str(x).split()) for x in d.get("choices", [])] if isinstance(d.get("choices"), list) else []
if not title or len(title) > 60 or not 2 <= len(choices) <= 5:
    raise SystemExit(1)
if any(not x or len(x) > 25 for x in choices) or len({x.casefold() for x in choices}) != len(choices):
    raise SystemExit(1)
with open(output, "w", encoding="utf-8") as f:
    json.dump({"title": title, "choices": choices}, f, ensure_ascii=False)
PY
}

_poll_draft_validator() {
	local tmp
	tmp=$(mktemp "tmp/.poll_validate.XXXXXXXX") || return 1
	_normalize_poll_draft "$1" "$tmp"
	local rc=$?
	rm -f "$tmp"
	return "$rc"
}

_generate_poll() {
	local prompt draft raw
	prompt=$(mktemp "tmp/.poll_prompt.XXXXXXXX") || return 1
	draft=$(mktemp "tmp/.poll_draft.XXXXXXXX") || { rm -f "$prompt"; return 1; }
	cat >"$prompt" <<'EOF'
あなたは日本語のライブ配信「ソ連ゲーム」のAIパーソナリティ「主塩ロイ」として、短いアンケートコーナーの質問を1つ作ります。
視聴者が気軽に答えられ、結果を話題にできるものを、少し尖った面白さで作ってください。

人格: 斜に構えた語り口で世の中を少し上から眺めるタイプ。褒めるときも素直に褒めない、けなすときも容赦しないが根底には愛がある。ウィットに富んだ皮肉や比喩、観察の効いたツッコミを滲ませる。淡白で常識的なだけの返しは禁止。

方針:
- 常識的で当たり障りのないだけの質問（例: 好きな季節は？ 好きな食べ物は？）は禁止。意外な視点、言葉遊び、日常の皮肉、あるあるネタ、クスッと笑える比喩やソ連らしい小ネタを入れる
- 視聴者が思わず突っ込みたくなる、ちょっとボケた選択肢を1つは混ぜる。極端な答えや妄想も歓迎
- ゲーム、食べ物、日常、音楽、配信の楽しみ方など軽い話題にする。ただし単なる一般常識クイズにはしない
- 政治的主張、個人情報、攻撃的内容、正解を要求する難問、配信運用の内部事情は避ける

制約:
- 質問は日本語60文字以内
- 選択肢は重複しない2〜4個、各25文字以内
- Markdownや説明を付けず、次のJSONだけを返す
{"title":"質問","choices":["選択肢1","選択肢2"]}
EOF
	raw=$(ai_generate_list "RADIO_POLL_QUESTION" "$prompt" "${TWITCH_POLL_AGENTS:-$RADIO_AGENTS}" "${TWITCH_POLL_AI_TIMEOUT:-120}" _poll_draft_validator) || {
		rm -f "$prompt" "$draft"
		return 1
	}
	if ! _normalize_poll_draft "$raw" "$draft"; then rm -f "$prompt" "$draft"; return 1; fi
	create_result=$(./twitch_polls.sh create "$draft")
	rm -f "$prompt" "$draft"
	[ "$(printf '%s' "$create_result" | _json_ok)" = "1" ] || {
		_log "create failed: $(printf '%s' "$create_result" | _json_field error)"
		return 1
	}
	local title
	title=$(printf '%s' "$create_result" | _json_field poll.title)
	_log "アンケート開始: ${title}"
	enqueue_chat_message "アンケートを始めました。「${title}」ぜひ投票してください。" "polls" 5 || true
	return 0
}

_store_commentary() {
	local commentary="$1" result_json="$2"
	python3 - "$STATE_FILE" "$commentary" "$result_json" <<'PY'
import json, os, sys
path, commentary, result_raw = sys.argv[1:4]
# sanitize surrogates from AI output
try:
    commentary = commentary.encode('utf-8', 'surrogateescape').decode('utf-8', 'ignore')
except Exception:
    commentary = commentary.encode('utf-8', errors='ignore').decode('utf-8', errors='ignore')
try: state = json.load(open(path, encoding="utf-8"))
except Exception: state = {}
state["phase"] = "commentary_ready"
state["commentary"] = commentary
try:
    poll = json.loads(result_raw).get("poll")
    if isinstance(poll, dict):
        poll["title"] = poll.get("title","").encode('utf-8', 'surrogateescape').decode('utf-8', 'ignore')
        for c in poll.get("choices", []):
            if isinstance(c, dict) and "title" in c:
                c["title"] = c["title"].encode('utf-8', 'surrogateescape').decode('utf-8', 'ignore')
    state["final_poll"] = poll
except Exception:
    state["final_poll"] = None
tmp = path + ".tmp"
with open(tmp, "w", encoding="utf-8") as f: json.dump(state, f, ensure_ascii=False)
os.replace(tmp, path)
PY
}

_generate_result_commentary() {
	local result_json="$1" prompt raw total
	total=$(printf '%s' "$result_json" | python3 -c 'import json,sys; p=json.load(sys.stdin).get("poll") or {}; print(sum(int(x.get("votes",0) or 0) for x in p.get("choices",[])))' 2>/dev/null || echo 0)
	if [ "${total:-0}" -eq 0 ] 2>/dev/null; then
		printf '%s\n' "今回は投票がありませんでした。また次のアンケートで気軽に参加してください。"
		return 0
	fi
	prompt=$(mktemp "tmp/.poll_result_prompt.XXXXXXXX") || return 1
	python3 - "$result_json" >"$prompt" <<'PY'
import json, sys
p = json.loads(sys.argv[1]).get("poll") or {}
print("あなたはTwitch配信「ソ連ゲーム」のAIパーソナリティ「主塩ロイ」です。アンケート結果について、日本語でコメントしてください。")
print("人格: 斜に構えた語り口で世の中を少し上から眺めるタイプ。褒めるときも素直に褒めない、けなすときも容赦しないが根底には愛がある。観察の効いたツッコミ、意外でクスッとなる比喩、軽く皮肉っぽい一言、言葉遊び、視点をずらすひねりのどれかを必ず入れる。淡白で常識的なだけの感想は禁止。視聴者を小馬鹿にしたり人格を攻撃する失礼な皮肉はしない。")
print("出力形式:")
print("- まず全選択肢の得票数を正確に書き出す。最多票・同率を正確に扱い、得票数を捏造・変更しない。例: 「春2票、秋3票、冬0票で秋が最多でした。」のように全選択肢を列挙する。票数が0の選択肢も省略しない。")
print("- 続けてその結果への主塩ロイらしい一言コメント（1〜2文）を添える。視聴者を責めず、説教にならない軽い皮肉・ユーモアにする。")
print("- 全体で日本語180文字以内。です・ます調で統一し、「だ・である」調は禁止。Markdownや箇条書き記号は使わない。")
print("質問: " + str(p.get("title", "")))
for c in p.get("choices", []): print(f"- {c.get('title','')}: {int(c.get('votes',0) or 0)}票")
PY
	raw=$(ai_generate_list "RADIO_POLL_RESULT" "$prompt" "${TWITCH_POLL_AGENTS:-$RADIO_AGENTS}" "${TWITCH_POLL_AI_TIMEOUT:-120}") || true
	rm -f "$prompt"
	raw=$(printf '%s' "$raw" | _ai_guard_model_output | tr '\n' ' ' | sed 's/[[:space:]][[:space:]]*/ /g; s/^ //; s/ $//' | cut -c1-200)
	[ -n "$raw" ] || raw="投票ありがとうございました。全選択肢の票数を拾うと、なかなか尖った偏りでした。主塩ロイ的にもツッコミどころ満載で面白い結果でした。"
	printf '%s\n' "$raw"
}

_deliver_ready_commentary() {
	[ -f "$STATE_FILE" ] || return 1
	local phase commentary final_poll
	phase=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("phase",""))' "$STATE_FILE" 2>/dev/null || true)
	[ "$phase" = "commentary_ready" ] || return 1
	commentary=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("commentary",""))' "$STATE_FILE" 2>/dev/null || true)
	[ -n "$commentary" ] || return 1
	final_poll=$(python3 -c 'import json,sys; print(json.dumps(json.load(open(sys.argv[1])).get("final_poll"),ensure_ascii=False))' "$STATE_FILE" 2>/dev/null || echo null)
	enqueue_chat_message "アンケート結果：${commentary}" "polls" 5 || return 1
	enqueue_audio_text "$commentary" "polls" || return 1
	python3 - "$HISTORY_FILE" "$final_poll" "$commentary" <<'PY'
import json, sys, time
path, poll_raw, commentary = sys.argv[1:4]
try:
    commentary = commentary.encode('utf-8', 'surrogateescape').decode('utf-8', 'ignore')
except Exception:
    commentary = commentary.encode('utf-8', errors='ignore').decode('utf-8', errors='ignore')
try:
    poll = json.loads(poll_raw)
    if isinstance(poll, dict):
        poll['title'] = poll.get('title','').encode('utf-8', 'surrogateescape').decode('utf-8', 'ignore')
        for c in poll.get('choices', []):
            if isinstance(c, dict) and 'title' in c:
                c['title'] = c['title'].encode('utf-8', 'surrogateescape').decode('utf-8', 'ignore')
except Exception:
    poll = None
with open(path, "a", encoding="utf-8") as f:
    f.write(json.dumps({"recorded_at": int(time.time()), "poll": poll,
                        "commentary": commentary}, ensure_ascii=False) + "\n")
PY
	rm -f "$STATE_FILE"
	_schedule_after "${TWITCH_POLL_INTERVAL_SEC:-43200}" completed
	_log "結果コメントをキューへ登録"
	return 0
}

_check_active_poll() {
	[ -f "$STATE_FILE" ] || return 1
	_deliver_ready_commentary && return 0
	local poll_id result ok status commentary
	poll_id=$(python3 -c 'import json,sys; print((json.load(open(sys.argv[1])).get("poll") or {}).get("id",""))' "$STATE_FILE" 2>/dev/null || true)
	[ -n "$poll_id" ] || { rm -f "$STATE_FILE"; return 1; }
	result=$(./twitch_polls.sh status "$poll_id")
	ok=$(printf '%s' "$result" | _json_ok)
	[ "$ok" = "1" ] || { _log "status failed: $(printf '%s' "$result" | _json_field error)"; return 0; }
	status=$(printf '%s' "$result" | _json_field poll.status)
	case "$status" in
	ACTIVE) return 0 ;;
	COMPLETED|TERMINATED|ARCHIVED|MODERATED|INVALID)
		commentary=$(_generate_result_commentary "$result") || return 0
		_store_commentary "$commentary" "$result"
		_deliver_ready_commentary || true
		return 0
		;;
	"")
		_log "poll not found; clearing local state"
		rm -f "$STATE_FILE"
		_schedule_after "${TWITCH_POLL_RETRY_SEC:-600}" not_found
		return 0
		;;
	*) return 0 ;;
	esac
}

if [ ! -f "$SCHEDULE_FILE" ]; then _schedule_after "${TWITCH_POLL_INITIAL_DELAY_SEC:-900}" initial_delay; fi
_log "起動 (PID=$$, interval=${POLL_INTERVAL}s enabled=${TWITCH_POLLS_ENABLED:-0})"

while true; do
	_reload_runtime
	[ -f "$PAUSE_FILE" ] && break
	[ -f tmp/stop ] && break

	if [ "${TWITCH_POLLS_ENABLED:-0}" = "1" ]; then
		if [ -f "$STATE_FILE" ]; then
			_check_active_poll
		else
			now=$(date +%s)
			next=$(_next_run_at)
			if [ "$now" -ge "${next:-0}" ]; then
				remote=$(./twitch_polls.sh status)
				remote_status=$(printf '%s' "$remote" | _json_field poll.status)
				if [ "$remote_status" = "ACTIVE" ]; then
					_log "手動または別プロセスのアンケートが進行中のため延期"
					_schedule_after "${TWITCH_POLL_RETRY_SEC:-600}" remote_active
				else
					live=$(./twitch_polls.sh live)
					if [ "$(printf '%s' "$live" | _json_field live)" = "True" ]; then
						_generate_poll || _schedule_after "${TWITCH_POLL_RETRY_SEC:-600}" create_failed
					else
						_schedule_after "${TWITCH_POLL_RETRY_SEC:-600}" offline
					fi
				fi
			fi
		fi
	fi

	_sleep_remaining="$POLL_INTERVAL"
	while [ "${_sleep_remaining:-0}" -gt 0 ]; do
		[ -f tmp/stop ] && break 2
		sleep 1
		_sleep_remaining=$((_sleep_remaining - 1))
	done
done

_log "メインループ終了"
exit 0
