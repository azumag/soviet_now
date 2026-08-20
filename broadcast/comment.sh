# broadcast/comment.sh - コメント応答生成, コンテキスト構築, advice抽出

#=== コメント関連 ===

_kill_comment_gen() {
	local pidfile="tmp/.twitch_chat/comment_gen.pid"
	local statefile="$COMMENT_GEN_STATE_FILE"
	if [ -f "$pidfile" ]; then
		local raw old_pid old_ppid live_ppid live_cmd
		raw=$(cat "$pidfile" 2>/dev/null || true)
		old_pid="${raw%%|*}"
		case "$old_pid" in
		'' | *[!0-9]*) old_pid="" ;;
		esac
		if [ "$raw" != "$old_pid" ]; then
			old_ppid=$(printf '%s' "$raw" | awk -F'|' '{print $2}')
			case "$old_ppid" in
			'' | *[!0-9]*) old_ppid="" ;;
			esac
		else
			old_ppid=""
		fi
		if [ -n "$old_pid" ] && kill -0 "$old_pid" 2>/dev/null; then
			live_ppid=$(ps -o ppid= -p "$old_pid" 2>/dev/null | tr -d ' ')
			live_cmd=$(ps -o command= -p "$old_pid" 2>/dev/null || true)
			if [[ "$live_cmd" == *"/workers/chat_worker.sh"* ]] || [[ "$live_cmd" == *"/workers/youtube_worker.sh"* ]]; then
				log "[COMMENT] stale comment_gen pid points to worker → killスキップ (PID=$old_pid)"
			elif [ -f "$statefile" ] && { [ -z "$old_ppid" ] || [ "$old_ppid" = "$live_ppid" ]; }; then
				pkill -P "$old_pid" 2>/dev/null
				kill "$old_pid" 2>/dev/null
				log "[COMMENT] 前回のコメント生成プロセス停止 (PID=$old_pid)"
			else
				log "[COMMENT] stale comment_gen pid検出 → killスキップ (PID=$old_pid, ppid_file=${old_ppid:-?}, ppid_live=${live_ppid:-?})"
			fi
		fi
		rm -f "$pidfile"
	fi
	rm -f "$statefile"
	rm -f "$COMMENT_BATCH_INFLIGHT_FILE"
}

COMMENT_PLAYED_HASHES_FILE="tmp/.comment_queue/played_hashes.txt"

# macOS の md5 と Linux の md5sum の差を吸収する。VM では md5 が存在しない
# ため、md5 -q の失敗を空文字として扱うとコメントの重複抑止が全て無効になる。
_comment_hash_text() {
	local text="${1:-}"
	if declare -F _outbound_chat_hash >/dev/null 2>&1; then
		_outbound_chat_hash "$text"
		return $?
	fi
	if command -v md5 >/dev/null 2>&1; then
		printf '%s' "$text" | md5 -q 2>/dev/null && return 0
	fi
	if command -v md5sum >/dev/null 2>&1; then
		printf '%s' "$text" | md5sum 2>/dev/null | awk '{print $1}'
		return $?
	fi
	return 1
}

_comment_hash_file() {
	local file="${1:-}"
	[ -f "$file" ] || return 1
	if command -v md5 >/dev/null 2>&1; then
		md5 -q "$file" 2>/dev/null && return 0
	fi
	if command -v md5sum >/dev/null 2>&1; then
		md5sum "$file" 2>/dev/null | awk '{print $1}'
		return $?
	fi
	return 1
}

_comment_failure_backoff_remaining() {
	local backoff_file="${COMMENT_FAILURE_BACKOFF_FILE:-${TMP_STATE_DIR:-tmp/state}/comment_generation_backoff_until}"
	local now backoff_until remaining
	[ -f "$backoff_file" ] || {
		printf '0\n'
		return 0
	}
	now=$(date +%s)
	backoff_until=$(cat "$backoff_file" 2>/dev/null || printf '0')
	case "$backoff_until" in
	'' | *[!0-9]*) backoff_until=0 ;;
	esac
	remaining=$((backoff_until - now))
	if [ "$remaining" -le 0 ]; then
		rm -f "$backoff_file" 2>/dev/null || true
		printf '0\n'
		return 0
	fi
	printf '%s\n' "$remaining"
}

_comment_failure_backoff_set() {
	local backoff_sec="${1:-${COMMENT_FAILURE_BACKOFF_SEC:-${AI_AGENT_BACKOFF_SEC:-600}}}"
	local backoff_file="${COMMENT_FAILURE_BACKOFF_FILE:-${TMP_STATE_DIR:-tmp/state}/comment_generation_backoff_until}"
	local backoff_tmp
	case "$backoff_sec" in
	'' | *[!0-9]*) backoff_sec=600 ;;
	esac
	[ "$backoff_sec" -lt 1 ] && backoff_sec=1
	mkdir -p "$(dirname "$backoff_file")" 2>/dev/null || true
	backoff_tmp="${backoff_file}.tmp.$$"
	printf '%s\n' "$(( $(date +%s) + backoff_sec ))" >"$backoff_tmp" &&
		mv -f "$backoff_tmp" "$backoff_file"
}

_comment_failure_backoff_clear() {
	local backoff_file="${COMMENT_FAILURE_BACKOFF_FILE:-${TMP_STATE_DIR:-tmp/state}/comment_generation_backoff_until}"
	rm -f "$backoff_file" 2>/dev/null || true
}

# コメント全体の待機は、明示的なレート制限が確認できた場合だけ設定する。
# 形式不正・空出力・通常のCLI/provider失敗は次の監視周期で再試行する。
_comment_handle_generation_failure() {
	local generation_rate_limited="${1:-false}"
	if [ "$generation_rate_limited" = "true" ]; then
		local comment_failure_backoff_sec="${COMMENT_FAILURE_BACKOFF_SEC:-${AI_AGENT_BACKOFF_SEC:-600}}"
		case "$comment_failure_backoff_sec" in
		'' | *[!0-9]*) comment_failure_backoff_sec=600 ;;
		esac
		[ "$comment_failure_backoff_sec" -lt 1 ] && comment_failure_backoff_sec=1
		_comment_failure_backoff_set "$comment_failure_backoff_sec"
		log "[COMMENT] コメント返し生成失敗（明示的レート制限・pending維持・${comment_failure_backoff_sec}秒後に再試行）"
	else
		_comment_failure_backoff_clear
		log "[COMMENT] コメント返し生成失敗（形式/通常失敗、バックオフなし・次周期に再試行）"
	fi
}

_comment_meta_sidecar_path() {
	local target="$1"
	case "$target" in
	*.playing) printf '%s.meta.json' "${target%.playing}" ;;
	*.txt) printf '%s.meta.json' "${target%.txt}" ;;
	*) printf '%s.meta.json' "$target" ;;
	esac
}

_comment_clear_generation_meta() {
	local target="$1"
	[ -n "$target" ] || return 0
	rm -f "$(_comment_meta_sidecar_path "$target")" 2>/dev/null || true
}

_comment_generation_debug_summary() {
	local target="$1"
	local sidecar
	sidecar=$(_comment_meta_sidecar_path "$target")
	[ -f "$sidecar" ] || return 1
	python3 - "$sidecar" <<'PY'
import json
import sys

path = sys.argv[1]
try:
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
except Exception:
    raise SystemExit(1)

chain = data.get("chain") or {}
parts = [
    f"mode={data.get('mode') or '-'}",
    f"model={data.get('model') or 'unknown'}",
    f"attempt={data.get('attempt') or 0}",
    f"chars={data.get('chars') or 0}",
]
if chain.get("primary"):
    parts.append(f"primary={chain['primary']}")
if chain.get("secondary"):
    parts.append(f"secondary={chain['secondary']}")
if chain.get("tertiary"):
    parts.append(f"tertiary={chain['tertiary']}")
print(" ".join(parts))
PY
}

_comment_store_generation_meta() {
	local target="$1" mode="$2" model="$3" batch_hash="$4" attempt="$5" chars="$6" primary="$7" secondary="$8" tertiary="$9"
	local speech_metadata_file="${10:-}"
	[ -n "$target" ] || return 0
	local sidecar
	sidecar=$(_comment_meta_sidecar_path "$target")
	mkdir -p "$(dirname "$COMMENT_GENERATION_HISTORY_FILE")" 2>/dev/null || true
	python3 - "$sidecar" "$COMMENT_GENERATION_HISTORY_FILE" "$COMMENT_GENERATION_HISTORY_KEEP" "$target" "$mode" "$model" "$batch_hash" "$attempt" "$chars" "$primary" "$secondary" "$tertiary" "$speech_metadata_file" <<'PY'
import json
import os
import sys
from datetime import datetime, timezone
from collections import deque

sidecar, history_file, keep_raw, target, mode, model, batch_hash, attempt_raw, chars_raw, primary, secondary, tertiary, speech_metadata_file = sys.argv[1:14]

def to_int(raw: str) -> int:
    try:
        return int(raw)
    except Exception:
        return 0

keep = max(1, to_int(keep_raw) or 500)

now = datetime.now(timezone.utc).isoformat()
payload = {
    "generated_at": now,
    "target_file": target,
    "queue_file": os.path.basename(target),
    "mode": mode or "",
    "model": model or "unknown",
    "batch_hash": batch_hash or "",
    "attempt": to_int(attempt_raw),
    "chars": to_int(chars_raw),
    "chain": {
        "primary": primary or "",
        "secondary": secondary or "",
        "tertiary": tertiary or "",
        "final": model or "unknown",
    },
}

if speech_metadata_file:
    try:
        with open(speech_metadata_file, "r", encoding="utf-8") as f:
            speech_payload = json.load(f)
        speech_segments = speech_payload.get("speech_segments")
        if not speech_payload.get("bilingual") or not isinstance(speech_segments, list) or not speech_segments:
            raise ValueError("invalid bilingual speech metadata")
        payload["bilingual"] = True
        payload["speech_segments"] = speech_segments
    except Exception as exc:
        print(f"invalid bilingual speech metadata: {exc}", file=sys.stderr)
        raise SystemExit(1)

with open(sidecar, "w", encoding="utf-8") as f:
    json.dump(payload, f, ensure_ascii=False, indent=2)
    f.write("\n")

history_dir = os.path.dirname(history_file)
if history_dir:
    os.makedirs(history_dir, exist_ok=True)

recent = deque(maxlen=max(0, keep - 1))
if os.path.exists(history_file):
    try:
        with open(history_file, "r", encoding="utf-8") as f:
            for line in f:
                line = line.rstrip("\n")
                if line:
                    recent.append(line)
    except Exception:
        recent.clear()

recent.append(json.dumps(payload, ensure_ascii=False))
tmp_path = history_file + ".tmp"
with open(tmp_path, "w", encoding="utf-8") as f:
    for line in recent:
        f.write(line + "\n")
os.replace(tmp_path, history_file)
PY
}

get_comment_backlog_counts() {
	local queued playing
	queued=$(ls -1 "$COMMENT_QUEUE_DIR"/comment_*.txt 2>/dev/null | wc -l | tr -d ' ')
	playing=$(ls -1 "$COMMENT_QUEUE_DIR"/comment_*.playing 2>/dev/null | wc -l | tr -d ' ')
	queued=${queued:-0}
	playing=${playing:-0}
	echo "${queued} ${playing}"
}

is_comment_backlog_high() {
	local threshold="${1:-4}"
	local basis="${2:-total}" # total | queued
	local queued playing total
	local value
	read -r queued playing <<<"$(get_comment_backlog_counts)"
	queued=${queued:-0}
	playing=${playing:-0}
	total=$((queued + playing))
	case "$basis" in
	queued) value="$queued" ;;
	*) value="$total" ;;
	esac
	[ "$value" -ge "$threshold" ]
}

_comment_needs_thumbnail_context() {
	local comments="$1"
	[ -n "$comments" ] || return 1
	python3 - "$comments" <<'PY'
import re
import sys

text = sys.argv[1] if len(sys.argv) > 1 else ""
patterns = (
    r"画面|スクショ|スクリーン|映像|見え|見えて|表示|配信|サムネ|盤面",
    r"今の|いまの|現在|いまどう|今どう|どうなって|状況|状態",
    r"スコア|点数|記録|順位|rank|ランク|建国|ソ連|ロシア",
    r"type|ピース|next|ネクスト|hold|ホールド|デッドライン|ゲームオーバー",
)
for pattern in patterns:
    if re.search(pattern, text, re.I):
        raise SystemExit(0)
raise SystemExit(1)
PY
}

_is_improve_running() {
	if command -v _wildcard_parallel_active >/dev/null 2>&1 && _wildcard_parallel_active; then
		return 0
	fi

	local state status pid
	state=$(_read_improve_state 2>/dev/null || true)
	[ -n "$state" ] || return 1
	read -r status pid <<<"$(printf '%s' "$state" | python3 -c '
import json, sys
try:
    data = json.loads(sys.stdin.read() or "{}")
except Exception:
    data = {}
status = str(data.get("status", "") or "")
pid = data.get("pid", 0)
try:
    pid = int(pid)
except Exception:
    pid = 0
print(status, pid)
' 2>/dev/null)"
	[ "$status" = "running" ] || return 1
	case "$pid" in
	'' | *[!0-9]*) return 1 ;;
	esac
	[ "$pid" -gt 0 ] || return 1
	kill -0 "$pid" 2>/dev/null
}

_is_recent_comment_batch_processed() {
	local batch_hash="$1"
	[ -n "$batch_hash" ] || return 1
	[ -f "$COMMENT_BATCH_HISTORY_FILE" ] || return 1
	local now
	now=$(date +%s)
	awk -F'|' -v h="$batch_hash" -v now="$now" -v ttl="$COMMENT_BATCH_DEDUP_TTL" '
		$2 == h && (now - $1) <= ttl { found=1 }
		END { exit(found ? 0 : 1) }
	' "$COMMENT_BATCH_HISTORY_FILE" 2>/dev/null
}

_is_comment_batch_inflight() {
	local batch_hash="$1"
	[ -n "$batch_hash" ] || return 1
	[ -f "$COMMENT_BATCH_INFLIGHT_FILE" ] || return 1
	local now ts hash pid
	now=$(date +%s)
	IFS='|' read -r ts hash pid <"$COMMENT_BATCH_INFLIGHT_FILE" 2>/dev/null || return 1
	case "$ts" in
	'' | *[!0-9]*) return 1 ;;
	esac
	[ "$hash" = "$batch_hash" ] || return 1
	if [ $((now - ts)) -gt "$COMMENT_BATCH_DEDUP_TTL" ]; then
		rm -f "$COMMENT_BATCH_INFLIGHT_FILE" 2>/dev/null || true
		return 1
	fi
	case "$pid" in
	'' | *[!0-9]*) return 0 ;;
	esac
	if kill -0 "$pid" 2>/dev/null; then
		return 0
	fi
	rm -f "$COMMENT_BATCH_INFLIGHT_FILE" 2>/dev/null || true
	return 1
}

_mark_comment_batch_inflight() {
	local batch_hash="$1" pid="${2:-}"
	[ -n "$batch_hash" ] || return 0
	printf '%s|%s|%s\n' "$(date +%s)" "$batch_hash" "$pid" >"$COMMENT_BATCH_INFLIGHT_FILE"
}

_clear_comment_batch_inflight() {
	local batch_hash="${1:-}"
	[ -f "$COMMENT_BATCH_INFLIGHT_FILE" ] || return 0
	if [ -z "$batch_hash" ]; then
		rm -f "$COMMENT_BATCH_INFLIGHT_FILE" 2>/dev/null || true
		return 0
	fi
	local ts hash pid
	IFS='|' read -r ts hash pid <"$COMMENT_BATCH_INFLIGHT_FILE" 2>/dev/null || {
		rm -f "$COMMENT_BATCH_INFLIGHT_FILE" 2>/dev/null || true
		return 0
	}
	[ "$hash" = "$batch_hash" ] && rm -f "$COMMENT_BATCH_INFLIGHT_FILE" 2>/dev/null || true
}

_mark_comment_batch_processed() {
	local batch_hash="$1"
	[ -n "$batch_hash" ] || return 0
	local now tmpf
	now=$(date +%s)
	tmpf=$(mktemp /tmp/eloop_comment_batch_history_XXXXXXXX)
	{
		if [ -f "$COMMENT_BATCH_HISTORY_FILE" ]; then
			awk -F'|' -v now="$now" -v ttl="$COMMENT_BATCH_DEDUP_TTL" -v h="$batch_hash" '
				NF >= 2 && $1 ~ /^[0-9]+$/ && (now - $1) <= (ttl * 3) && $2 != h { print }
			' "$COMMENT_BATCH_HISTORY_FILE" 2>/dev/null
		fi
		echo "${now}|${batch_hash}"
	} >"$tmpf"
	mv "$tmpf" "$COMMENT_BATCH_HISTORY_FILE"
}

# 個別コメント行の重複フィルタ: 処理済み行ハッシュに存在する行を除外して返す
_filter_already_processed_comment_lines() {
	local comments="$1"
	[ -n "$comments" ] || return 0
	[ -f "$COMMENT_PROCESSED_LINES_FILE" ] || {
		printf '%s' "$comments"
		return 0
	}
	local now filtered_count=0 total_count=0
	now=$(date +%s)
	local result=""
	while IFS= read -r line; do
		[ -n "$line" ] || continue
		total_count=$((total_count + 1))
		local line_hash
		line_hash=$(_comment_hash_text "$line" 2>/dev/null || echo "")
		[ -n "$line_hash" ] || {
			result="${result:+${result}
}${line}"
			filtered_count=$((filtered_count + 1))
			continue
		}
		if awk -F'|' -v h="$line_hash" -v now="$now" -v ttl="$COMMENT_PROCESSED_LINES_TTL" \
			'$2 == h && (now - $1) <= ttl { found=1 } END { exit(found ? 0 : 1) }' \
			"$COMMENT_PROCESSED_LINES_FILE" 2>/dev/null; then
			: # 処理済み → スキップ
		else
			result="${result:+${result}
}${line}"
			filtered_count=$((filtered_count + 1))
		fi
	done <<<"$comments"
	if [ "$filtered_count" -lt "$total_count" ]; then
		log "[COMMENT] 個別行フィルタ: ${total_count}行中 $((total_count - filtered_count))行を処理済みとして除外" >&2
	fi
	[ -n "$result" ] && printf '%s' "$result"
	return 0
}

_has_processed_comment_line() {
	local comments="$1"
	[ -n "$comments" ] || return 1
	[ -f "$COMMENT_PROCESSED_LINES_FILE" ] || return 1
	local now line line_hash
	now=$(date +%s)
	while IFS= read -r line; do
		[ -n "$line" ] || continue
		line_hash=$(_comment_hash_text "$line" 2>/dev/null || echo "")
		[ -n "$line_hash" ] || continue
		if awk -F'|' -v h="$line_hash" -v now="$now" -v ttl="$COMMENT_PROCESSED_LINES_TTL" \
			'$2 == h && (now - $1) <= ttl { found=1 } END { exit(found ? 0 : 1) }' \
			"$COMMENT_PROCESSED_LINES_FILE" 2>/dev/null; then
			return 0
		fi
	done <<<"$comments"
	return 1
}

# 処理成功後に個別コメント行のハッシュを記録する
_record_processed_comment_lines() {
	local comments="$1"
	[ -n "$comments" ] || return 0
	local now tmpf
	now=$(date +%s)
	tmpf=$(mktemp /tmp/eloop_comment_lines_XXXXXXXX)
	{
		# 既存エントリからTTL内のものを保持
		if [ -f "$COMMENT_PROCESSED_LINES_FILE" ]; then
			awk -F'|' -v now="$now" -v ttl="$COMMENT_PROCESSED_LINES_TTL" \
				'NF >= 2 && $1 ~ /^[0-9]+$/ && (now - $1) <= ttl { print }' \
				"$COMMENT_PROCESSED_LINES_FILE" 2>/dev/null
		fi
		# 新しい行ハッシュを追加
		while IFS= read -r line; do
			[ -n "$line" ] || continue
			local line_hash
			line_hash=$(_comment_hash_text "$line" 2>/dev/null || echo "")
			[ -n "$line_hash" ] && echo "${now}|${line_hash}"
		done <<<"$comments"
	} | tail -n "$COMMENT_PROCESSED_LINES_MAX" >"$tmpf"
	mv "$tmpf" "$COMMENT_PROCESSED_LINES_FILE"
}

_format_comment_batch_context() {
	python3 -c '
import sys

lines = [ln.strip() for ln in sys.stdin.read().splitlines() if ln.strip()]
items = []
for ln in lines:
    if ": " in ln:
        user, msg = ln.split(": ", 1)
    else:
        user, msg = "不明", ln
    items.append((user.strip(), msg.strip(), ln))

for i, (user, msg, raw) in enumerate(items, start=1):
    prev_raw = items[i - 2][2] if i > 1 else "（なし）"
    next_raw = items[i][2] if i < len(items) else "（なし）"
    same_user_prev = "あり" if i > 1 and items[i - 2][0] == user else "なし"
    print(f"[{i}] {user}: {msg}")
    print(f"  直前: {prev_raw}")
    print(f"  直後: {next_raw}")
    print(f"  直前が同一ユーザー: {same_user_prev}")
    print("")
'
}

_remember_comment_reply_text() {
	local remembered_text="$1"
	[ -n "$remembered_text" ] || return 0
	mkdir -p "$COMMENT_SPOKEN_HISTORY_DIR" 2>/dev/null || true
	local history_file prune_from old_files text_hash hash_file
	text_hash=$(_comment_hash_text "$remembered_text" 2>/dev/null || true)
	hash_file="$COMMENT_SPOKEN_HISTORY_DIR/.reply_hashes"
	if [ -n "$text_hash" ] && grep -qF "$text_hash" "$hash_file" 2>/dev/null; then
		return 0
	fi
	history_file="$COMMENT_SPOKEN_HISTORY_DIR/$(date '+%Y%m%d_%H%M%S')_${RANDOM}.txt"
	printf '%s\n' "$remembered_text" >"$history_file" 2>/dev/null || return 0
	if [ -n "$text_hash" ]; then
		printf '%s\n' "$text_hash" >>"$hash_file" 2>/dev/null || true
		tail -"$COMMENT_SPOKEN_HISTORY_MAX_FILES" "$hash_file" >"${hash_file}.tmp" 2>/dev/null &&
			mv "${hash_file}.tmp" "$hash_file" 2>/dev/null || true
	fi
	prune_from=$((COMMENT_SPOKEN_HISTORY_MAX_FILES + 1))
	old_files=$(ls -1t "$COMMENT_SPOKEN_HISTORY_DIR"/*.txt 2>/dev/null | tail -n +"$prune_from" || true)
	if [ -n "$old_files" ]; then
		printf '%s\n' "$old_files" | xargs rm -f 2>/dev/null || true
	fi
}

_remember_spoken_comment() {
	local spoken_file="$1"
	[ -s "$spoken_file" ] || return 0
	local remembered_text
	remembered_text=$(cat "$spoken_file" 2>/dev/null | _clean_comment_talk | _sanitize_onair_text)
	[ -n "$remembered_text" ] || return 0
	_remember_comment_reply_text "$remembered_text"
}

_current_playing_comment_file() {
	[ -f "tmp/.say_queue/current_source" ] || return 1
	local cs_line phase src_file
	cs_line=$(cat "tmp/.say_queue/current_source" 2>/dev/null || true)
	phase=$(printf '%s' "$cs_line" | awk -F'|' 'NR==1{print $2}')
	src_file=$(printf '%s' "$cs_line" | awk -F'|' 'NR==1{print $3}')
	[ "$phase" = "playing" ] || return 1
	case "$src_file" in
	*comment_*.playing | *comment_*.txt)
		[ -f "$src_file" ] || return 1
		printf '%s' "$src_file"
		return 0
		;;
	esac
	return 1
}

_build_recent_spoken_comment_context() {
	local current_file=""
	current_file=$(_current_playing_comment_file || true)
	python3 - "$COMMENT_SPOKEN_HISTORY_DIR" "$COMMENT_SPOKEN_PROMPT_ITEMS" "$COMMENT_SPOKEN_PROMPT_MAX_CHARS" "$COMMENT_SPOKEN_ITEM_MAX_CHARS" "$current_file" <<'PY'
import glob
import os
import re
import sys
import time

history_dir = sys.argv[1]
history_limit = max(0, int(sys.argv[2]))
total_limit = max(200, int(sys.argv[3]))
item_limit = max(80, int(sys.argv[4]))
current_file = sys.argv[5] if len(sys.argv) > 5 else ""


def collapse(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def excerpt(path: str) -> str:
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            raw = f.read()
    except Exception:
        return ""
    kept = []
    for raw_line in raw.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if re.match(r'^[✗✕×].*\b(read|glob|grep|ls|edit|write|multiedit)\b.*\bfailed\b', line, re.I):
            continue
        if re.match(r'^[✱→►▸]\s*(read|glob|grep|ls|edit|write|multiedit)\b', line, re.I):
            continue
        if re.match(r'^(read|glob|grep|ls|edit|write|multiedit)\b', line, re.I):
            continue
        if re.match(r'^(error|warning)\s*:', line, re.I):
            continue
        if re.search(r'file not found:|no such file or directory|permission denied|invalid arguments|could not find oldstring|no changes to apply', line, re.I):
            continue
        kept.append(raw_line)
    text = collapse("\n".join(kept))
    if len(text) > item_limit:
        text = text[:item_limit].rstrip() + "..."
    return text


entries = []
seen = set()
if current_file and os.path.isfile(current_file):
    entries.append(("再生中", os.path.getmtime(current_file), current_file))
    seen.add(os.path.realpath(current_file))

history_files = sorted(glob.glob(os.path.join(history_dir, "*.txt")))
if history_limit > 0:
    history_files = history_files[-history_limit:]
for path in reversed(history_files):
    real_path = os.path.realpath(path)
    if real_path in seen:
        continue
    entries.append(("", os.path.getmtime(path), path))

lines = []
used = 0
for tag, ts, path in entries:
    text = excerpt(path)
    if not text:
        continue
    stamp = time.strftime("%H:%M", time.localtime(ts))
    line = f"[{tag} {stamp}] {text}" if tag else f"[{stamp}] {text}"
    if used and used + len(line) + 1 > total_limit:
        break
    if not used and len(line) > total_limit:
        keep = max(40, total_limit - 16)
        line = line[:keep].rstrip() + "..."
    lines.append(line)
    used += len(line) + 1

print("\n".join(lines) if lines else "（なし）")
PY
}

_build_comment_followup_hints() {
	local batch_file="$1"
	local current_file=""
	current_file=$(_current_playing_comment_file || true)
	python3 - "$batch_file" "$COMMENT_SPOKEN_HISTORY_DIR" "$COMMENT_SPOKEN_PROMPT_ITEMS" "$current_file" <<'PY'
import glob
import os
import re
import sys

batch_file, history_dir, history_limit, current_file = sys.argv[1:5]
try:
    history_limit = int(history_limit)
except Exception:
    history_limit = 10

if not os.path.isfile(batch_file):
    print("（なし）")
    raise SystemExit(0)

def collapse(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()

def parse_line(line: str):
    m = re.match(r"([^:]{1,40}):\s*(.+)$", line)
    if m:
        return m.group(1).strip(), m.group(2).strip()
    return "", line.strip()

def sanitize_text(path: str) -> str:
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            raw = f.read()
    except Exception:
        return ""
    kept = []
    for raw_line in raw.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if re.match(r'^[✗✕×].*\b(read|glob|grep|ls|edit|write|multiedit)\b.*\bfailed\b', line, re.I):
            continue
        if re.match(r'^[✱→►▸]\s*(read|glob|grep|ls|edit|write|multiedit)\b', line, re.I):
            continue
        if re.match(r'^(read|glob|grep|ls|edit|write|multiedit)\b', line, re.I):
            continue
        if re.match(r'^(error|warning)\s*:', line, re.I):
            continue
        kept.append(raw_line)
    return collapse("\n".join(kept))

def is_short_followup(text: str) -> bool:
    norm = collapse(text)
    if not norm:
        return False
    markers = (
        "なんだ", "なんですね", "そうなんだ", "なるほど", "へえ", "ほう",
        "しらなかった", "知らなかった", "たしかに", "確かに", "そういうこと",
        "すごい", "助かる", "面白い", "おもしろい", "わかる"
    )
    if any(marker in norm for marker in markers):
        return True
    if len(norm) <= 18:
        return True
    if re.fullmatch(r'[!！?？wW笑ー\s]+', norm):
        return True
    return False

def extract_terms(text: str):
    norm = collapse(text)
    patterns = [
        r'[「『]([^」』]{1,24})[」』]',
        r'([^\s、。！？]{2,24})(?:なんだ|なんですね|ってこと|って|とは)',
        r'([A-Za-z][A-Za-z0-9_+\-]{1,24})',
        r'([ァ-ヶー]{2,24})',
    ]
    stop = {"それ", "これ", "あれ", "さっき", "今の", "その話", "この話", "こと", "感じ"}
    out = []
    for pat in patterns:
        for m in re.finditer(pat, norm):
            term = collapse(m.group(1))
            if len(term) < 2 or term in stop:
                continue
            out.append(term)
    if not out and len(norm) <= 20:
        out.append(norm[:20])
    seen = set()
    dedup = []
    for term in out:
        key = term.lower()
        if key in seen:
            continue
        seen.add(key)
        dedup.append(term)
    return dedup[:4]

recent_texts = []
seen_paths = set()
if current_file and os.path.isfile(current_file):
    seen_paths.add(os.path.realpath(current_file))
    text = sanitize_text(current_file)
    if text:
        recent_texts.append(text)

history_files = sorted(glob.glob(os.path.join(history_dir, "*.txt")))
if history_limit > 0:
    history_files = history_files[-history_limit:]
for path in reversed(history_files):
    real = os.path.realpath(path)
    if real in seen_paths:
        continue
    seen_paths.add(real)
    text = sanitize_text(path)
    if text:
        recent_texts.append(text)

recent_texts = recent_texts[:6]
recent_blob = "\n".join(recent_texts)
recent_blob_lower = recent_blob.lower()

hints = []
seen_hints = set()
with open(batch_file, "r", encoding="utf-8", errors="ignore") as f:
    batch_lines = [line.strip() for line in f if line.strip()]

for line in batch_lines:
    user, text = parse_line(line)
    if not is_short_followup(text):
        continue
    matched_term = ""
    for term in extract_terms(text):
        if term in recent_blob or term.lower() in recent_blob_lower:
            matched_term = term
            break
    if matched_term:
        hint = f"- {user or 'リスナー'}: 「{matched_term}」は直近返答で説明済み。今回は説明を最初から繰り返さず、反応・感想・別角度の補足を組み合わせて会話として厚めに返す"
    else:
        hint = f"- {user or 'リスナー'}: 短い反応コメントの可能性が高い。短い返答で済ませず、感想や驚きへの返答を先に置き、理由・文脈・軽い問いかけのどれかを足して広げる"
    if hint in seen_hints:
        continue
    seen_hints.add(hint)
    hints.append(hint)
    if len(hints) >= 4:
        break

print("\n".join(hints) if hints else "（なし）")
PY
}

_build_comment_game_context() {
	local gs_file="${1:-$GAME_STATE}"
	python3 - "$gs_file" "score_history.txt" "$RUSSIA_CREATION_HISTORY_FILE" "$SOVIET_CREATION_HISTORY_FILE" "${MIN_GAMES_BEFORE_IMPROVE:-12}" <<'PY'
import json
import statistics
import sys
from pathlib import Path

path = sys.argv[1]
score_history_file = Path(sys.argv[2])
russia_history_file = Path(sys.argv[3])
soviet_history_file = Path(sys.argv[4])
try:
    prediction_cycle_games = max(1, int(sys.argv[5]))
except Exception:
    prediction_cycle_games = 12

STATE_LABELS = {
    "MOVE": "プレイ中",
    "WAITING": "待機中",
    "RESULT": "結果画面",
    "GAMEOVER": "ゲームオーバー",
    "START": "開始前",
}

def read_scores(path: Path):
    rows = []
    if not path.exists():
        return rows
    try:
        for raw in path.read_text(encoding="utf-8", errors="ignore").splitlines():
            cols = raw.strip().split("\t")
            if not cols:
                continue
            try:
                score = int(cols[-1])
            except Exception:
                continue
            rows.append(score)
    except Exception:
        return []
    return rows

def read_creation_entries(path: Path):
    rows = []
    if not path.exists():
        return rows
    try:
        lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
    except Exception:
        return rows
    for raw in lines:
        cols = raw.strip().split("\t")
        if len(cols) < 5:
            continue
        _iso_ts, local_ts, game_num, score, turns = cols[:5]
        try:
            game_num_i = int(game_num)
        except Exception:
            game_num_i = None
        try:
            score_i = int(score)
        except Exception:
            score_i = None
        try:
            turns_i = int(turns)
        except Exception:
            turns_i = None
        rows.append({
            "local_ts": local_ts.strip(),
            "game_num": game_num_i,
            "score": score_i,
            "turns": turns_i,
        })
    return rows

def fmt_int(value):
    if value is None:
        return "不明"
    try:
        return f"{int(value)}"
    except Exception:
        return "不明"

def summarize_scores(scores):
    if not scores:
        return "score_history.txt は未取得"
    total = len(scores)
    ordered = sorted(scores)
    latest = scores[-1]
    recent12 = scores[-12:]
    recent50 = scores[-50:]
    recent200 = scores[-200:]
    recent1000 = scores[-1000:]
    parts = [
        f"終了スコア履歴件数={total}",
        f"全体平均={sum(scores) / total:.0f}",
        f"全体中央値={statistics.median(scores):.0f}",
        f"全体最高={max(scores)}",
        f"全体p90={ordered[max(0, min(total - 1, int(total * 0.90) - 1))]}",
        f"直近終了スコア={latest}",
        f"直近12平均={sum(recent12) / len(recent12):.0f}",
        f"直近12最高={max(recent12)}",
    ]
    if len(recent50) >= 2:
        parts.append(f"直近50平均={sum(recent50) / len(recent50):.0f}")
        parts.append(f"直近50中央値={statistics.median(recent50):.0f}")
    if len(recent200) >= 2:
        parts.append(f"直近200平均={sum(recent200) / len(recent200):.0f}")
    if len(recent1000) >= 2:
        parts.append(f"直近1000平均={sum(recent1000) / len(recent1000):.0f}")
    return ", ".join(parts)

def summarize_creation(label, entries, score_history_count):
    if not entries:
        return f"{label}: total=0, 直近なし"
    total = len(entries)
    last = entries[-1]
    known_game_numbers = [row["game_num"] for row in entries if row["game_num"] is not None]
    latest_known_game = max(known_game_numbers) if known_game_numbers else None
    bits = [
        f"{label}: total={total}",
        f"直近={last['local_ts'] or '不明'}",
    ]
    if last["game_num"] is not None:
        bits.append(f"Game#{last['game_num']}")
        if score_history_count and score_history_count >= last["game_num"]:
            bits.append(f"そこから約{score_history_count - last['game_num']}ゲーム経過")
    if last["score"] is not None:
        bits.append(f"score={last['score']}")
    if last["turns"] is not None:
        bits.append(f"turns={last['turns']}")
    if score_history_count:
        bits.append(f"score_history件数基準の通算率目安={total / score_history_count * 100:.2f}%")
    if latest_known_game:
        for recent_window in (100, 500, 1000):
            recent_entries = [
                row for row in entries
                if row["game_num"] is not None and row["game_num"] > latest_known_game - recent_window
            ]
            bits.append(f"履歴Game#基準の直近{recent_window}ゲーム内={len(recent_entries)}回")
    return ", ".join(bits)

try:
    with open(path, "r", encoding="utf-8") as f:
        gs = json.load(f)
except Exception:
    print("（game_state.json を読めませんでした）")
    raise SystemExit(0)

state = gs.get("state", "?")
score = gs.get("score")
record = gs.get("record", 0)
make_soren_count = gs.get("makeSorenCount")
piece_count = gs.get("pieceCount")
pieces = gs.get("pieces") if isinstance(gs.get("pieces"), list) else []
type_counts = {}
max_type = None
for piece in pieces:
    try:
        t = int(piece.get("type"))
    except Exception:
        continue
    type_counts[t] = type_counts.get(t, 0) + 1
    max_type = t if max_type is None else max(max_type, t)
top_types = sorted(type_counts.items(), key=lambda item: (-item[0], item[1]))[:6]
top_type_text = ", ".join(
    f"type{t}x{count}" for t, count in top_types
) if top_types else "盤面ピース情報なし"
next_piece = gs.get("next") if isinstance(gs.get("next"), dict) else {}
next_next_piece = gs.get("nextNext") if isinstance(gs.get("nextNext"), dict) else {}
next_type = next_piece.get("type", "?")
next_next_type = next_next_piece.get("type", "?")

scores = read_scores(score_history_file)
russia_entries = read_creation_entries(russia_history_file)
soviet_entries = read_creation_entries(soviet_history_file)
total_games = len(scores)
cycle_completed = total_games % prediction_cycle_games if total_games else None
cycle_position = (cycle_completed + 1) if cycle_completed is not None else None

print("ライブ局面は生成から読み上げまでにズレやすい。返答の中心は終了ゲーム履歴・建国履歴の全体統計に置くこと。")
print(f"ライブ状態補助: state={state}({STATE_LABELS.get(str(state), '不明')}), snapshot_score={fmt_int(score)}, record={fmt_int(record)}, makeSorenCount={fmt_int(make_soren_count)}, pieceCount={fmt_int(piece_count)}")
print(f"ライブ盤面補助: next=type{next_type}, nextNext=type{next_next_type}, 最大type={fmt_int(max_type)}, 上位type={top_type_text}")
print(f"全体スコア統計: {summarize_scores(scores)}")
if cycle_position is not None:
    print(f"予想サイクル目安: score_history基準で{prediction_cycle_games}ゲーム中{cycle_position}ゲーム目相当（完了済み {cycle_completed}/{prediction_cycle_games}）")
print("全体建国統計: " + summarize_creation("ロシア建国", russia_entries, total_games))
print("全体建国統計: " + summarize_creation("ソ連建国", soviet_entries, total_games))
print("返答ルール: スコア進捗・建国回数・状態を聞かれたら、まず全体統計と直近ウィンドウ統計を使う。ライブ局面の snapshot_score や盤面補助は、今この瞬間の断定ではなく補足としてだけ扱う。数字が無い時だけ不明と言い、推測で回数やスコアを作らない。")
PY
}

_build_comment_celebration_history_context() {
	python3 - "$RUSSIA_CREATION_HISTORY_FILE" "$SOVIET_CREATION_HISTORY_FILE" "$COMMENT_CELEBRATION_HISTORY_ITEMS" <<'PY'
import sys
from pathlib import Path

russia_file = Path(sys.argv[1])
soviet_file = Path(sys.argv[2])
limit = max(1, int(sys.argv[3]))


def read_entries(path: Path):
    items = []
    if not path.exists():
        return items
    try:
        lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
    except Exception:
        return items
    for raw in lines:
        cols = raw.strip().split("\t")
        if len(cols) < 5:
            continue
        _iso_ts, local_ts, game_num, score, turns = cols[:5]
        items.append((local_ts.strip(), game_num.strip(), score.strip(), turns.strip()))
    return items[-limit:]


def render_block(label: str, path: Path):
    rows = read_entries(path)
    if not rows:
        return f"{label}:\n- まだ履歴なし"
    lines = [f"{label}:"]
    for local_ts, game_num, score, turns in reversed(rows):
        parts = [local_ts]
        if game_num:
            parts.append(f"Game#{game_num}")
        parts.append(f"score={score}")
        parts.append(f"turns={turns}")
        lines.append("- " + " / ".join(parts))
    return "\n".join(lines)


print(render_block("ロシア建国", russia_file))
print("")
print(render_block("ソ連建国", soviet_file))
PY
}

_read_advice_context_tail() {
	local advice_file="$1"
	local max_lines="${2:-40}"
	[ -f "$advice_file" ] || {
		printf '%s' "（なし）"
		return 0
	}
	local context=""
	context=$(tail -n "$max_lines" "$advice_file" 2>/dev/null | sed -E '/^[[:space:]]*$/d; /^[[:space:]]*-?[[:space:]]*（なし）[[:space:]]*$/d')
	if [ -n "$context" ]; then
		printf '%s' "$context"
	else
		printf '%s' "（なし）"
	fi
}

_sanitize_comment_prompt_context() {
	python3 -c "$(
		cat <<'PY'
import re
import sys

ERROR_PATTERNS = [
    r"申し訳(?:ありません|ございません|ない).*(?:エラーメッセージ|提供|ユーザーメッセージ|指示|タスク|情報|スクリーンショット)",
    r"(?:エラーメッセージ|ユーザーメッセージ|具体的な指示|明確な指示|具体的なタスク).*(?:提供されてい|見当たりません|ありません|ない|不足)",
    r"(?:何も言えません|語ることはできません|控えておくべき|確認させてください|どうすればよい|何を.*すれば)",
    r"(?:tool_call|tool_result|assistant_response|System Context|permission denied|no such file or directory|file not found|read failed|edit failed|write failed)",
    r"(?:unexpected token|syntaxerror|referenceerror|typeerror|could not find oldstring|no changes to apply|rejected permission)",
    r"(?:invalid bearer token|authentication_error|api error|request_id|invalid token|not logged in|please run /login|rate limit|too many requests|429\b|quota|usage limit)",
    r"(?:ユーザーからの.*指示がなく|システム設定.*だけが提供|具体的なタスク指示が見当たりません)",
    r"(?:提供いただいた|提供していただいた).*(?:実際の記事本文|本文ではなく|テンプレート|管理情報|テキスト).*?(?:含まれていない|ありません|成立していません)",
    r"(?:検索|WebFetch|インターネット|外部.*アクセス).*(?:できません|ありません|許可|確認できません)",
]
compiled = [re.compile(p, re.I) for p in ERROR_PATTERNS]

def bad(line: str) -> bool:
    stripped = line.strip()
    if not stripped:
        return False
    if re.match(r"^(error|warning)\s*:", stripped, re.I):
        return True
    if re.match(r"^[✗✕×].*\b(read|glob|grep|ls|edit|write|multiedit)\b.*\bfailed\b", stripped, re.I):
        return True
    if re.match(r"^[✱→►▸]\s*(read|glob|grep|ls|edit|write|multiedit)\b", stripped, re.I):
        return True
    return any(p.search(stripped) for p in compiled)

kept = []
for raw in sys.stdin.read().splitlines():
    if bad(raw):
        continue
    kept.append(raw.rstrip())

text = "\n".join(line for line in kept if line.strip()).strip()
print(text if text else "（なし）", end="")
PY
	)"
}

_extract_structured_advice_from_comments() {
	local batch_file="$1"
	local fallback_mode="${2:-main}"
	[ -f "$batch_file" ] || return 0
	python3 - "$batch_file" "$fallback_mode" <<'PY'
import re
import sys

path = sys.argv[1]
fallback_mode = sys.argv[2] if len(sys.argv) > 2 else "main"

try:
    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        lines = [line.strip() for line in f if line.strip()]
except Exception:
    raise SystemExit(0)

strategy_terms = (
    "戦略", "盤面", "併合", "連鎖", "next", "nextnext", "next-next", "hold",
    "type", "高さ", "左", "右", "上に", "下に", "置く", "置き", "積む",
    "積み", "デッドライン", "ゲームオーバー", "merge", "sandwich", "サンドイッチ",
    "おじゃま", "garbage", "rank", "順位", "ピース", "盤面タイプ", "drop", "gauge"
)
comment_terms = (
    "コメント", "コメント返し", "返答", "返信", "読み上げ", "しゃべり", "話し方",
    "口調", "文量", "長め", "短め", "テンポ", "語尾", "言い回し", "実況",
    "試合中コメント", "順位コメント", "戦略説明", "説明文", "カードガチャ",
    "カード説明", "発音", "読み", "ラジオ", "ニュース", "ニーサ", "nisa"
)
system_terms = (
    "仕組み", "システム", "改善ループ", "自動改善", "戦略改善", "次のループ",
    "codex", "コーデックス", "拾える", "拾って", "取り入れ", "反映", "修正依頼",
    "意見", "要望", "監視", "watchdog", "ワーカー", "worker", "daemon",
    "runtime", "ランタイム", "show-status", "show_status", "dashboard",
    "ダッシュボード", "overlay", "オーバーレイ", "obs", "分類器", "classifier",
    "コメント分類", "ログ", "レポート", "恒久対応", "運用"
)
directive_terms = (
    "して", "しろ", "すべき", "したほうがいい", "した方がいい", "やめて",
    "避けて", "見るべき", "見て", "考えて", "意識して", "優先", "禁止",
    "改善して", "直して", "変えて", "分けて", "保存して", "参照して",
    "読んで", "増やして", "減らして", "別にして", "統一して", "変換して",
    "長くして", "短くして", "伸ばして", "抑えて", "残して", "今まで通り",
    "ほうがいい", "方がいい", "べき",
    "いかん", "だめ", "ダメ", "するな", "しないで", "するといかん", "するとだめ",
    "よくない", "まずい", "やばい", "危ない", "注意", "気をつけ",
    "取り入れ", "反映", "拾える", "拾って", "残して", "メモして",
)
noise_terms = (
    "レイド", "nightbot", "show-status", "show_status", "dashboard", "blackhole",
    "ffmpeg", "url", "http://", "https://"
)
main_terms = (
    "中華ai", "strategy.py", "[main]", "[soren]"
)
soren91_terms = (
    "メリケン", "メリケンai", "soren91", "[soren91]", "対戦版", "91人",
    "おじゃま", "hold", "next", "nextnext", "順位", "相手", "試合", "盤面タイプ"
)

strategy_terms_norm = tuple(term.lower().replace(" ", "") for term in strategy_terms)
comment_terms_norm = tuple(term.lower().replace(" ", "") for term in comment_terms)
system_terms_norm = tuple(term.lower().replace(" ", "") for term in system_terms)
noise_terms_norm = tuple(term.lower().replace(" ", "") for term in noise_terms)
main_terms_norm = tuple(term.lower().replace(" ", "") for term in main_terms)
soren91_terms_norm = tuple(term.lower().replace(" ", "") for term in soren91_terms)


def collapse(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def parse_line(line: str):
    m = re.match(r"([^:]{1,40}):\s*(.+)$", line)
    if m:
        return m.group(1).strip(), m.group(2).strip()
    return "", line


def clean_body(text: str) -> str:
    body = collapse(text)
    if body.startswith("[") and body.endswith("]"):
        body = body[1:-1].strip()
    return body


def looks_like_internal_error(raw: str) -> bool:
    patterns = (
        r"申し訳(?:ありません|ございません|ない).*(?:エラーメッセージ|提供|ユーザーメッセージ|指示|タスク|情報|スクリーンショット)",
        r"(?:エラーメッセージ|ユーザーメッセージ|具体的な指示|明確な指示|具体的なタスク).*(?:提供されてい|見当たりません|ありません|ない|不足)",
        r"(?:何も言えません|語ることはできません|控えておくべき|確認させてください|どうすればよい|何を.*すれば)",
        r"(?:tool_call|tool_result|assistant_response|System Context|permission denied|no such file or directory|file not found|read failed|edit failed|write failed)",
        r"(?:unexpected token|syntaxerror|referenceerror|typeerror|could not find oldstring|no changes to apply|rejected permission)",
        r"(?:invalid bearer token|authentication_error|api error|request_id|invalid token|not logged in|please run /login|rate limit|too many requests|429\b|quota|usage limit)",
    )
    return any(re.search(pattern, raw, re.I) for pattern in patterns)


def has_any(norm: str, terms) -> bool:
    return any(term in norm for term in terms)


def has_directive(raw: str) -> bool:
    return any(term in raw for term in directive_terms)


def looks_like_comment_advice(raw: str) -> bool:
    if len(raw) < 5:
        return False
    norm = raw.lower().replace(" ", "")
    has_comment = has_any(norm, comment_terms_norm)
    if has_comment and (has_directive(raw) or "改善" in raw or "今まで通り" in raw):
        return True
    if ("nisa" in norm or "ニーサ" in raw) and any(term in raw for term in ("変換", "読み", "発音", "呼び")):
        return True
    if ("試合中コメント" in raw or "順位コメント" in raw) and any(term in raw for term in ("1回", "一回", "減ら", "抑え", "禁止")):
        return True
    return False


def looks_like_system_advice(raw: str) -> bool:
    if len(raw) < 6:
        return False
    norm = raw.lower().replace(" ", "")
    if not has_any(norm, system_terms_norm):
        return False
    if has_directive(raw) or any(term in raw for term in ("改善", "修正", "意見", "要望", "依頼", "問題", "不具合")):
        return True
    return False


def looks_like_strategy_advice(raw: str) -> bool:
    if len(raw) < 6:
        return False
    norm = raw.lower().replace(" ", "")
    if looks_like_system_advice(raw) and not has_any(norm, strategy_terms_norm):
        return False
    if looks_like_comment_advice(raw):
        return False
    has_game = has_any(norm, strategy_terms_norm) or bool(re.search(r"type\s*[a-z0-9]+", raw, re.I))
    noisy = has_any(norm, noise_terms_norm)
    if noisy and not has_game:
        return False
    if has_game and has_directive(raw):
        return True
    if "改善" in raw and has_game:
        return True
    if raw.startswith("[") and raw.endswith("]") and has_game:
        return True
    return False


def detect_mode(raw: str) -> str:
    norm = raw.lower().replace(" ", "")
    has_main = has_any(norm, main_terms_norm)
    has_soren91 = has_any(norm, soren91_terms_norm)
    if has_soren91 and not has_main:
        return "soren91"
    if has_main and not has_soren91:
        return "main"
    return fallback_mode


seen = set()
for line in lines:
    user, text = parse_line(line)
    body = clean_body(text)
    if not body:
        continue
    if looks_like_internal_error(body):
        continue
    kind = None
    mode = "-"
    if looks_like_system_advice(body):
        kind = "codex"
    elif looks_like_comment_advice(body):
        kind = "comment"
    elif looks_like_strategy_advice(body):
        kind = "strategy"
        mode = detect_mode(body)
    if not kind:
        continue
    item = f"{user}: {body}" if user else body
    if len(item) > 220:
        item = item[:217].rstrip() + "..."
    key = (kind, mode, item)
    if key in seen:
        continue
    seen.add(key)
    print(f"{kind}\t{mode}\t{item}")
PY
}

_extract_named_block() {
	local marker="$1"
	python3 - "$marker" <<'PY'
import re
import sys

marker = sys.argv[1]
tag = f"==={marker}==="
text = sys.stdin.read()

patterns = [
    re.compile(rf"(?ms)^[ \t]*{re.escape(tag)}[ \t]*\n(.*?)\n^[ \t]*{re.escape(tag)}[ \t]*$"),
    re.compile(rf"(?ms)^[ \t]*{re.escape(tag)}[ \t]*\n(.*?)(?=\n^[ \t]*===[A-Z_]+===[ \t]*$|\Z)"),
]

for pattern in patterns:
    match = pattern.search(text)
    if match:
        print(match.group(1).strip())
        raise SystemExit(0)
PY
}

_remove_named_block() {
	local marker="$1"
	python3 - "$marker" <<'PY'
import re
import sys

marker = sys.argv[1]
tag = f"==={marker}==="
text = sys.stdin.read()

patterns = [
    re.compile(rf"(?ms)\n?^[ \t]*{re.escape(tag)}[ \t]*\n.*?\n^[ \t]*{re.escape(tag)}[ \t]*\n?"),
    re.compile(rf"(?ms)\n?^[ \t]*{re.escape(tag)}[ \t]*\n.*?(?=\n^[ \t]*===[A-Z_]+===[ \t]*$|\Z)"),
]

updated = text
for pattern in patterns:
    updated, count = pattern.subn("\n", updated, count=1)
    if count:
        break

sys.stdout.write(updated)
PY
}

#=== コメント分類器 ===

_extract_comment_classification_json() {
	python3 -c '
import json
import re
import sys

text = sys.stdin.read().strip()
text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.I)
text = re.sub(r"\s*```$", "", text)

decoder = json.JSONDecoder()
for match in re.finditer(r"\[", text):
    try:
        value, _ = decoder.raw_decode(text[match.start():])
    except Exception:
        continue
    if isinstance(value, list):
        print(json.dumps(value, ensure_ascii=False, separators=(",", ":")))
        raise SystemExit(0)
raise SystemExit(1)
'
}

_validate_comment_classification_json() {
	python3 -c '
import json
import sys

try:
    data = json.load(sys.stdin)
except Exception:
    raise SystemExit(1)
if not isinstance(data, list) or not data:
    raise SystemExit(1)
indices = []
for item in data:
    if not isinstance(item, dict):
        raise SystemExit(1)
    if not item.get("category"):
        raise SystemExit(1)
    try:
        index = int(item.get("index"))
    except Exception:
        raise SystemExit(1)
    if index < 1:
        raise SystemExit(1)
    indices.append(index)
    if "is_english" in item and not isinstance(item["is_english"], bool):
        raise SystemExit(1)
if sorted(indices) != list(range(1, len(data) + 1)):
    raise SystemExit(1)
'
}

_normalize_comment_classification_json() {
	python3 -c '
import json
import sys

try:
    data = json.load(sys.stdin)
except Exception:
    raise SystemExit(1)
if not isinstance(data, list) or not data:
    raise SystemExit(1)
for item in data:
    if isinstance(item, dict) and item.get("category") == "short_reaction":
        item["category"] = "chitchat"
    if isinstance(item, dict):
        # Keep old classifier responses usable while making the language
        # decision explicit for the live generation path.
        value = item.get("is_english", False)
        if not isinstance(value, bool):
            value = str(value).strip().lower() in {"1", "true", "yes", "y"}
        item["is_english"] = value
print(json.dumps(data, ensure_ascii=False, separators=(",", ":")))
'
}

_comment_enforce_english_safety() {
	local classification_json="$1" comments_file="$2"
	python3 - "$classification_json" "$comments_file" <<'PY'
import json
import re
import sys

try:
    rows = json.loads(sys.argv[1])
except Exception:
    raise SystemExit(1)
try:
    source = [line.rstrip("\n") for line in open(sys.argv[2], encoding="utf-8", errors="ignore") if line.strip()]
except OSError:
    source = []

if not isinstance(rows, list) or len(rows) != len(source):
    raise SystemExit(1)
indices = []
for position, row in enumerate(rows, 1):
    if not isinstance(row, dict):
        raise SystemExit(1)
    try:
        index = int(row.get("index"))
    except Exception:
        raise SystemExit(1)
    if index != position:
        raise SystemExit(1)
    indices.append(index)
if indices != list(range(1, len(source) + 1)):
    raise SystemExit(1)

japanese_re = re.compile(r"[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff]")
cyrillic_re = re.compile(r"[\u0400-\u04ff]")
url_re = re.compile(r"(?:https?://|www\.)\S+", re.IGNORECASE)

def row_comment(row):
    text = str(row.get("comment") or "").strip()
    if text:
        return text
    try:
        idx = int(row.get("index", 0) or 0)
    except Exception:
        idx = 0
    if 1 <= idx <= len(source):
        raw = source[idx - 1]
        return raw.split(": ", 1)[1] if ": " in raw else raw
    return ""

def obvious_non_english_noise(text):
    text = url_re.sub(" ", text).strip()
    if japanese_re.search(text) or cyrillic_re.search(text):
        return True
    tokens = re.findall(r"[A-Za-z]+(?:'[A-Za-z]+)?", text.lower())
    if not tokens:
        return True
    if len(tokens) >= 2 and len(set(tokens)) == 1:
        return True
    if len(tokens) >= 3 and len(set(tokens)) / len(tokens) < 0.67:
        return True
    return False

for position, row in enumerate(rows, 1):
    raw = source[position - 1]
    if ": " in raw:
        user, comment = raw.split(": ", 1)
    else:
        user, comment = "", raw
    # The source batch, not model-echoed fields, is authoritative for the
    # translation mapping and protects against index/user/comment drift.
    row["index"] = position
    row["user"] = user
    row["comment"] = comment
    if row.get("is_english") is True and obvious_non_english_noise(comment):
        row["is_english"] = False
print(json.dumps(rows, ensure_ascii=False, separators=(",", ":")))
PY
}

_comment_normalize_classification_for_comments() {
	local classification_json="$1" comments_file="$2" normalized=""
	normalized=$(printf '%s' "$classification_json" | _normalize_comment_classification_json 2>/dev/null || true)
	[ -n "$normalized" ] || return 1
	_comment_enforce_english_safety "$normalized" "$comments_file"
}

_classify_comments_heuristic() {
	local comments_file="$1"
	[ -f "$comments_file" ] || return 1
	python3 - "$comments_file" "${ELOOP_LIB_DIR:-.}/lib/comment_bilingual.py" <<'PY'
import json
import re
import sys

path = sys.argv[1]
helper_path = sys.argv[2] if len(sys.argv) > 2 else "lib/comment_bilingual.py"
try:
    import importlib.util
    spec = importlib.util.spec_from_file_location("comment_bilingual", helper_path)
    language_helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(language_helper)
except Exception:
    language_helper = None
rows = []
try:
    lines = [line.rstrip("\n") for line in open(path, encoding="utf-8", errors="ignore") if line.strip()]
except OSError:
    raise SystemExit(1)

def classify(user: str, comment: str) -> str:
    text = comment.strip()
    lower = text.lower()
    compact = re.sub(r"\s+", "", lower)
    system_user = user.lower() in {"wizebot", "nightbot", "streamelements", "streamlabs"}
    strategy_hint = re.search(r"戦略|盤面|併合|連鎖|next|nextnext|hold|type\s*[a-z0-9]+|高さ|左|右|置|積|デッドライン|ゲームオーバー|merge|drop|ピース|ロシア|ソ連|建国|おじゃま|相手|順位|盤面タイプ", text, re.I)
    advice_hint = re.search(r"したほうが|した方が|ほうが|方が|ほうがいい|方がいい|よくない|良くない|すべき|べき|狙|優先|避け|やめ|見るべき|考え|意識|改善|閾値|なら|よりも|だめ|ダメ|危ない|注意", text)
    stream_bug_terms = (
        "配信", "映像", "画面", "表示", "出てない", "出ない", "消えた", "止まった",
        "固ま", "フリーズ", "重い", "遅延", "音", "無音", "音楽", "bgm", "音声", "読み上げ", "voicevox",
        "tts", "obs", "overlay", "オーバーレイ", "eventoverlay", "dashboard",
        "ダッシュボード", "show_status", "show-status", "ステータス", "コメント",
        "拾えて", "拾ってない", "反応しない", "返信", "返答", "worker", "ワーカー",
        "chat_worker", "youtube_worker", "audio_worker", "監視", "watchdog", "分類器",
        "classifier", "codex", "コーデックス", "不具合", "バグ", "壊れ", "動いてない",
        "動いてねえ", "動いてねぇ", "動かない", "動かん", "動いていない",
        "不調", "いつもと違う", "record", "レコード",
    )
    stream_bug_failure = re.search(r"不具合|バグ|壊れ|止ま|固ま|フリーズ|出てない|出ない|出なくなる|でなくなる|消え|拾えてない|拾ってない|反応しない|動いてない|動いてね[えぇ]|動かない|動かん|動いていない|聞こえない|聞こえん|聞こえなくなる|鳴らない|鳴らなくなる|無音|遅延|ずれ|ない|無い|なし|無し|不調|いつもと違う", text, re.I)
    stream_bug_hint = any(term.lower().replace(" ", "") in compact for term in stream_bug_terms)
    if system_user and ("raid" in lower or "レイド" in text):
        return "raid"
    if system_user or "配信が終了" in text or "配信が再開" in text or "新しいステータス" in text:
        return "other"
    if stream_bug_hint and stream_bug_failure and not strategy_hint:
        return "stream_bug_report"
    if re.search(r"が【.+?】.+?を獲得しました", text):
        return "card_gacha"
    if "bits" in lower or "cheer" in lower:
        return "bits"
    if "sub" in lower or "サブスク" in text:
        return "subscription"
    if "歌" in text or "うた" in text:
        return "sing_request"
    if strategy_hint and advice_hint:
        return "strategy_advice"
    if "?" in text or "？" in text:
        if re.search(r"ゲーム|スコア|盤面|戦略|ロシア|ソ連|建国|何点|何試合", text):
            return "game_question"
        return "general_question"
    if re.search(r"スコア|点|ロシア|ソ連|建国|ウクライナ|カザフ|盤面|落下|テンポ", text):
        return "game_status"
    if re.search(r"したほうが|すべき|狙|置|改善|閾値|ワーカー|返答|コメント", text):
        return "strategy_advice" if re.search(r"戦略|置|狙|スコア|閾値", text) else "comment_advice"
    return "chitchat"

for idx, raw in enumerate(lines, 1):
    if ": " in raw:
        user, comment = raw.split(": ", 1)
    else:
        user, comment = "", raw
    is_english = bool(
        language_helper and language_helper.looks_like_english(comment)
    )
    rows.append({
        "index": idx,
        "user": user,
        "comment": comment,
        "category": classify(user, comment),
        "is_english": is_english,
    })

if not rows:
    raise SystemExit(1)
print(json.dumps(rows, ensure_ascii=False, separators=(",", ":")))
PY
}

_comment_category_allows_advice_append() {
	local category="${1:-}"
	case "$category" in
	card_gacha | chitchat | bits | subscription | sing_request | raid | other | stream_bug_report)
		return 1
		;;
	*)
		return 0
		;;
	esac
}

_queue_stream_bug_reports_from_classification() {
	local classification_json="$1" source="${2:-unknown}" batch_hash="${3:-}"
	[ -n "$classification_json" ] || return 0
	[ "${CODEX_BUG_DISPATCH_ENABLED:-1}" = "1" ] || return 0
	python3 - "$classification_json" "$source" "$batch_hash" \
		"${CODEX_BUG_QUEUE_DIR:-tmp/codex_bug_queue}" \
		"${CODEX_BUG_DISPATCH_DEDUP_FILE:-tmp/state/codex_bug_dispatch_hashes.log}" <<'PY'
import hashlib
import json
import os
import re
import sys
import tempfile
import time
from pathlib import Path

classification_raw, source, batch_hash, queue_dir_raw, dedup_file_raw = sys.argv[1:6]

try:
    rows = json.loads(classification_raw)
except Exception:
    raise SystemExit(0)
if not isinstance(rows, list):
    raise SystemExit(0)

queue_dir = Path(queue_dir_raw)
dedup_file = Path(dedup_file_raw)
queue_dir.mkdir(parents=True, exist_ok=True)
dedup_file.parent.mkdir(parents=True, exist_ok=True)

now = int(time.time())
keep_sec = 24 * 3600
seen = set()
kept = []
if dedup_file.exists():
    try:
        with dedup_file.open("r", encoding="utf-8", errors="ignore") as f:
            for raw in f:
                raw = raw.strip()
                if not raw:
                    continue
                parts = raw.split("|", 1)
                if len(parts) != 2:
                    continue
                try:
                    ts = int(parts[0])
                except Exception:
                    continue
                digest = parts[1]
                if now - ts <= keep_sec:
                    seen.add(digest)
                    kept.append((ts, digest))
    except Exception:
        seen.clear()
        kept.clear()

def normalize(text):
    return re.sub(r"\s+", " ", str(text or "")).strip()

queued = []
for item in rows:
    if not isinstance(item, dict):
        continue
    if item.get("category") != "stream_bug_report":
        continue
    user = normalize(item.get("user"))
    comment = normalize(item.get("comment"))
    if not comment:
        continue
    digest = hashlib.sha256(f"{source}\0{user}\0{comment}".encode("utf-8")).hexdigest()
    if digest in seen:
        continue
    payload = {
        "created_at": now,
        "source": source,
        "batch_hash": batch_hash,
        "hash": digest,
        "index": item.get("index"),
        "user": user,
        "comment": comment,
        "category": "stream_bug_report",
        "status": "pending",
    }
    fd, tmp = tempfile.mkstemp(prefix=".bug_", suffix=".json", dir=str(queue_dir))
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2, sort_keys=True)
        f.write("\n")
    dest = queue_dir / f"{now}_{digest[:16]}.json"
    os.replace(tmp, dest)
    seen.add(digest)
    kept.append((now, digest))
    queued.append(dest.name)

if queued:
    tmp_path = dedup_file.with_suffix(dedup_file.suffix + ".tmp")
    with tmp_path.open("w", encoding="utf-8") as f:
        for ts, digest in kept[-500:]:
            f.write(f"{ts}|{digest}\n")
    os.replace(tmp_path, dedup_file)
    print("\n".join(queued))
PY
}

_classify_comments_with_edit_contract() {
	local classifier_prompt_file="$1" output_file="$2" primary="$3" fallback="$4" timeout_sec="$5"
	local base_prompt agent prev_agent edit_prompt candidate_rc raw_json classification
	[ -s "$classifier_prompt_file" ] || return 1
	[ -n "$output_file" ] || return 1
	base_prompt=$(cat "$classifier_prompt_file")
	for agent in "$primary" "$fallback"; do
		[ -n "$agent" ] || continue
		[ "$agent" = "-" ] && continue
		[ "$agent" = "$prev_agent" ] && continue
		prev_agent="$agent"
		: >"$output_file"
		edit_prompt="${base_prompt}

【出力契約】
通常のチャット返答ではなく、次のファイルを編集して分類結果を書いてください。

出力ファイル: ${output_file}

必須:
- ${output_file} のファイル全体を、有効な JSON 配列だけで上書きする
- Markdown、コードフェンス、説明文、前置き、後書きは禁止
- stdout に JSON を出す必要はない
- 書き込み後、追加の説明は不要"
		local prev_timeout="${RUN_CMD_TIMEOUT_SEC:-}"
		local prev_tag="${RUN_CMD_LOG_TAG:-}"
		RUN_CMD_TIMEOUT_SEC="$timeout_sec"
		RUN_CMD_LOG_TAG="COMMENT_CLASSIFIER:${agent}"
		run_cmd "$agent" "$edit_prompt" >/dev/null 2>&1
		candidate_rc=$?
		if [ -n "$prev_timeout" ]; then RUN_CMD_TIMEOUT_SEC="$prev_timeout"; else unset RUN_CMD_TIMEOUT_SEC; fi
		if [ -n "$prev_tag" ]; then RUN_CMD_LOG_TAG="$prev_tag"; else unset RUN_CMD_LOG_TAG; fi
		raw_json=$(cat "$output_file" 2>/dev/null)
		classification=$(printf '%s' "$raw_json" | _extract_comment_classification_json 2>/dev/null || true)
		if [ -n "$classification" ] && printf '%s' "$classification" | _validate_comment_classification_json 2>/dev/null; then
			classification=$(printf '%s' "$classification" | _normalize_comment_classification_json 2>/dev/null || printf '%s' "$classification")
			printf '%s\n%s' "$agent" "$classification"
			return 0
		fi
		log "[COMMENT] 分類器 edit契約失敗: agent=${agent} rc=${candidate_rc} output=${raw_json:0:120}" >&2
	done
	return 1
}

# stdout契約でも、非空というだけで採用せず、有効なJSON分類だけを成功扱いにする。
_is_valid_comment_classification_output() {
	local raw="$1" classification
	classification=$(printf '%s' "$raw" | _extract_comment_classification_json 2>/dev/null || true)
	[ -n "$classification" ] || return 1
	printf '%s' "$classification" | _validate_comment_classification_json 2>/dev/null
}

_classify_comments() {
	local comments_file="$1"
	[ -f "$comments_file" ] || return 1
	local classifier_prompt_file
	classifier_prompt_file=$(mktemp /tmp/eloop_comment_classifier_XXXXXXXX)
	local comments_text
	comments_text=$(cat "$comments_file")
	if [ -z "$comments_text" ]; then
		rm -f "$classifier_prompt_file"
		return 1
	fi
	export comments_text
	envsubst '${comments_text}' <"$ELOOP_LIB_DIR/prompts/comment_classifier.md" >"$classifier_prompt_file"
	if [ "${COMMENT_CLASSIFIER_AI_ENABLED:-0}" != "1" ]; then
		classification=$(_classify_comments_heuristic "$comments_file" 2>/dev/null || true)
		rm -f "$classifier_prompt_file"
		if [ -n "$classification" ]; then
			if ! classification=$(_comment_normalize_classification_for_comments "$classification" "$comments_file" 2>/dev/null); then
				log "[COMMENT] 分類器: heuristic分類の整合性検証失敗" >&2
				return 1
			fi
			log "[COMMENT] 分類器: model=heuristic mode=local" >&2
			printf '%s' "$classification"
			return 0
		fi
		return 1
	fi
	local model="${COMMENT_CLASSIFIER_AGENT:-codex:minimax-m3}"
	local fallback="${COMMENT_CLASSIFIER_FALLBACK:-codex:minimax-m3}"
	local timeout_sec="${COMMENT_CLASSIFIER_TIMEOUT:-90}"
	local edit_model="${COMMENT_CLASSIFIER_EDIT_AGENT:-codex:minimax-m3}"
	local edit_fallback="${COMMENT_CLASSIFIER_EDIT_FALLBACK:-codex:minimax-m3}"
	local edit_timeout_sec="${COMMENT_CLASSIFIER_EDIT_TIMEOUT:-45}"
	local classification raw_classification classifier_output_file classifier_edit_file classifier_model_used edit_result
	mkdir -p "$ELOOP_LIB_DIR/tmp/debug/comment_classifier" 2>/dev/null || true
	classifier_edit_file="$ELOOP_LIB_DIR/tmp/debug/comment_classifier/classification_$(date +%Y%m%d_%H%M%S)_${RANDOM}.json"
	if ! : >"$classifier_edit_file" 2>/dev/null; then
		classifier_edit_file=$(mktemp /tmp/eloop_comment_classifier_json_XXXXXXXX)
	fi
	edit_result=$(_classify_comments_with_edit_contract "$classifier_prompt_file" "$classifier_edit_file" "$edit_model" "$edit_fallback" "$edit_timeout_sec" 2>/dev/null || true)
	if [ -n "$edit_result" ]; then
		classifier_model_used=$(printf '%s' "$edit_result" | sed -n '1p')
		classification=$(printf '%s' "$edit_result" | sed '1d')
		if classification=$(_comment_normalize_classification_for_comments "$classification" "$comments_file" 2>/dev/null); then
			rm -f "$classifier_prompt_file" "$classifier_edit_file"
			log "[COMMENT] 分類器: model=${classifier_model_used:-$model} mode=edit" >&2
			printf '%s' "$classification"
			return 0
		fi
		log "[COMMENT] 分類器: edit結果の整合性検証失敗 -> stdout契約へフォールバック" >&2
	fi

	classifier_output_file=$(mktemp /tmp/eloop_comment_classifier_output_XXXXXXXX)
	if ai_generate "COMMENT_CLASSIFIER" "$classifier_prompt_file" "$model" "$fallback" "$timeout_sec" \
		"_is_valid_comment_classification_output" >"$classifier_output_file"; then
		classifier_model_used="${AI_GENERATE_LAST_AGENT:-$model}"
	else
		classifier_model_used="${AI_GENERATE_LAST_AGENT:-}"
	fi
	raw_classification=$(cat "$classifier_output_file" 2>/dev/null)
	rm -f "$classifier_output_file"
	rm -f "$classifier_prompt_file" "$classifier_edit_file"
	if [ -z "$raw_classification" ]; then
		log "[COMMENT] 分類器: 空出力/タイムアウト" >&2
		return 1
	fi
	classification=$(printf '%s' "$raw_classification" | _extract_comment_classification_json 2>/dev/null)
	if [ -z "$classification" ]; then
		log "[COMMENT] 分類器: JSON配列抽出失敗 -> ${raw_classification:0:200}" >&2
		return 1
	fi
	if ! printf '%s' "$classification" | _validate_comment_classification_json 2>/dev/null; then
		log "[COMMENT] 分類器: JSON不正 -> ${classification:0:200}" >&2
		return 1
	fi
	if ! classification=$(_comment_normalize_classification_for_comments "$classification" "$comments_file" 2>/dev/null); then
		log "[COMMENT] 分類器: 整合性検証失敗（元のAI分類は採用しない）" >&2
		return 1
	fi
	log "[COMMENT] 分類器: model=${classifier_model_used:-$model} mode=stdout_fallback" >&2
	printf '%s' "$classification"
	return 0
}

_comment_classification_english_count() {
	local classification_json="$1"
	python3 - "$classification_json" <<'PY'
import json
import sys
try:
    rows = json.loads(sys.argv[1])
except Exception:
    rows = []
print(sum(1 for row in rows if isinstance(row, dict) and row.get("is_english") is True))
PY
}

_comment_build_translation_prompt() {
	local classification_json="$1"
	local helper_path="${ELOOP_LIB_DIR:-.}/lib/comment_bilingual.py"
	local japanese_text_file=""
	japanese_text_file=$(mktemp /tmp/eloop_comment_translation_source_XXXXXXXX 2>/dev/null || true)
	[ -n "$japanese_text_file" ] || return 1
	cat >"$japanese_text_file" || {
		rm -f "$japanese_text_file"
		return 1
	}
	python3 - "$classification_json" "$helper_path" "$japanese_text_file" <<'PY'
import importlib.util
import json
import sys

classification_raw, helper_path, japanese_text_path = sys.argv[1:4]
try:
    rows = json.loads(classification_raw)
except Exception:
    raise SystemExit(1)
try:
    spec = importlib.util.spec_from_file_location("comment_bilingual", helper_path)
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
except Exception:
    raise SystemExit(1)

rows = [row for row in rows if isinstance(row, dict)]
rows.sort(key=lambda row: int(row.get("index", 0) or 0))
try:
    with open(japanese_text_path, encoding="utf-8") as source:
        paragraphs = helper.split_plain_paragraphs(source.read())
except OSError:
    raise SystemExit(1)
if len(paragraphs) != len(rows):
    raise SystemExit(1)
targets = [(row, paragraph) for row, paragraph in zip(rows, paragraphs) if row.get("is_english") is True]
if not targets:
    raise SystemExit(1)

print("You translate selected Japanese Twitch replies into natural spoken English.")
print("Return exactly one English paragraph for each target, in the order shown below.")
print("Separate paragraphs with one blank line. Output only the translations: no labels, markers, Markdown, explanations, or Japanese text.")
print("Use polite, conversational English. If a Japanese display name is present, omit it or use an ASCII name only.")
print()
for row, paragraph in targets:
    index = row.get("index", "?")
    comment = str(row.get("comment") or "").replace("\n", " ").strip()
    print(f"Target {index} (viewer comment: {comment})")
    print("Japanese reply to translate:")
    print(paragraph)
    print()
PY
	local rc=$?
	rm -f "$japanese_text_file"
	return "$rc"
}

_comment_is_valid_translation_candidate() {
	local raw="$1"
	[ -n "$raw" ] || return 1
	_contains_provider_error_text "$raw" && return 1
	printf '%s' "$raw" | grep -Eiq 'tool_call|tool_result|assistant_response|^analysis$|^final$|^assistant$|^provider[[:space:]]*[:=]|^model[[:space:]]*[:=]|^agent[[:space:]]*[:=]' && return 1
	return 0
}

# 翻訳は補助処理。翻訳プロバイダの失敗を通常コメント生成の agent
# backoff ファイルへ波及させないため、ai_generate_list は使わず dispatch
# だけを順番に呼ぶ。翻訳に失敗しても日本語返信を採用する。
_comment_generate_translation() {
	local prompt_file="$1" agent_list="$2" timeout_sec="$3" last_agent_file="${4:-}"
	local agents=() agent output rc attempted=0
	[ -n "$last_agent_file" ] && : >"$last_agent_file"
	case "$timeout_sec" in
	'' | *[!0-9]*) timeout_sec=20 ;;
	esac
	[ "$timeout_sec" -lt 1 ] && timeout_sec=1
	IFS=',' read -ra agents <<<"$agent_list"
	for agent in "${agents[@]}"; do
		agent="${agent#${agent%%[![:space:]]*}}"
		agent="${agent%${agent##*[![:space:]]}}"
		[ -n "$agent" ] || continue
		if [ "$attempted" -ge 2 ]; then
			log "[COMMENT_TRANSLATION] agent試行上限(2)に到達" >&2
			break
		fi
		if declare -F _ai_backoff_check >/dev/null 2>&1 && ! _ai_backoff_check "$agent"; then
			log "[COMMENT_TRANSLATION] backoff skip: ${agent} (no force retry)" >&2
			continue
		fi
		attempted=$((attempted + 1))
		output=$(_ai_dispatch "COMMENT_TRANSLATION" "$agent" "$prompt_file" "$timeout_sec")
		rc=$?
		if [ "$rc" -eq 0 ] && [ -n "$output" ] && _comment_is_valid_translation_candidate "$output"; then
			[ -n "$last_agent_file" ] && printf '%s\n' "$agent" >"$last_agent_file"
			printf '%s' "$output"
			return 0
		fi
		log "[COMMENT_TRANSLATION] ${agent} failed/invalid → next agent (no shared backoff)" >&2
	done
	return 1
}

# 分類結果をパースしてカテゴリ別にコメントをグループ化
_group_comments_by_category() {
	local classification_json="$1"
	[ -z "$classification_json" ] && return 1
	python3 - "$classification_json" <<'PY'
import json
import sys

json_str = sys.argv[1] if len(sys.argv) > 1 else sys.stdin.read()
try:
    data = json.loads(json_str)
except Exception:
    raise SystemExit(1)

categories = {}
for item in data:
    idx = item.get("index", 0)
    user = item.get("user", "")
    comment = item.get("comment", "")
    cat = item.get("category", "chitchat")
    if cat not in categories:
        categories[cat] = []
    categories[cat].append((idx, user, comment))

# Output in format: category|index|user: comment
for cat, items in categories.items():
    for idx, user, comment in sorted(items, key=lambda x: x[0]):
        print(f"{cat}|{idx}|{user}: {comment}")
PY
}

# ガチャの「コンプリート(全種制覇)」言及を一度きりにする。
# 「N 種中 N 種所持」(owned==total) を達成したユーザーを検出し、
#   - 既に祝福済み(GACHA_COMPLETED_USERS_FILE に記録済み)なら「触れないで」指示
#   - 今回が初達成なら「一度だけ祝う」指示を出し、ファイルに追記して以後は抑制
# 結果の指示文を GACHA_COMPLETION_NOTE に格納する。
_build_gacha_completion_note() {
	local comments_block="$1"
	local list_file="${GACHA_COMPLETED_USERS_FILE:-${TMP_HISTORY_DIR:-tmp/history}/gacha_completed_users.txt}"
	GACHA_COMPLETION_NOTE=""
	[ -n "$comments_block" ] || return 0
	mkdir -p "$(dirname "$list_file")" 2>/dev/null || true
	GACHA_COMPLETION_NOTE=$(_GACHA_BLOCK="$comments_block" python3 - "$list_file" <<'PY' 2>/dev/null || true
import sys, os, re

list_file = sys.argv[1]
text = os.environ.get("_GACHA_BLOCK", "")

# 既に祝福済みのコンプリートユーザーを読み込む(小文字キーで照合)
known = {}
try:
    with open(list_file, encoding="utf-8", errors="ignore") as f:
        for line in f:
            name = line.strip()
            if name:
                known.setdefault(name.lower(), name)
except FileNotFoundError:
    pass

known_keys = set(known.keys())
count_re = re.compile(r"(\d+)\s*種中\s*(\d+)\s*種所持")

def extract_user(line):
    s = line.strip()
    if ": " in s:
        head, rest = s.split(": ", 1)
        m = re.match(r"\s*@?([^\s　:]+)\s*が", rest)
        if m:
            return m.group(1).lstrip("@")
        h = head.strip().lstrip("@")
        if h and not h.startswith("["):
            return h
        s = rest.strip()
    m = re.match(r"\s*@?([^\s　:]+)\s*が", s)
    if m:
        return m.group(1).lstrip("@")
    return ""

already, newly, newly_keys = [], [], set()
for raw in text.splitlines():
    line = raw.strip()
    if not line:
        continue
    m = count_re.search(line)
    if not m:
        continue
    total, owned = int(m.group(1)), int(m.group(2))
    if total <= 0 or owned < total:
        continue  # 未コンプリート
    user = extract_user(line)
    if not user:
        continue
    key = user.lower()
    if key in known_keys:
        disp = known[key]
        if disp not in already:
            already.append(disp)
        continue
    if key in newly_keys:
        continue
    newly_keys.add(key)
    newly.append(user)

# 初コンプリート達成者を記録(以後は抑制対象になる)
if newly:
    try:
        with open(list_file, "a", encoding="utf-8") as f:
            for u in newly:
                f.write(u + "\n")
    except OSError:
        pass

out = []
if already:
    out.append("次のユーザーは過去に全種コンプリート達成済みで、既に何度も祝福しています: " + "、".join(already) + "。")
    out.append("このユーザーにはコンプリート・全種制覇・制覇・112種制覇といった達成自体には一切触れず、今回引いたカードそのものの話だけをしてください。")
if newly:
    out.append("次のユーザーは今回が初のコンプリート達成です: " + "、".join(newly) + "。一度だけ達成を祝ってください。")
print("\n".join(out))
PY
)
}

# カテゴリ別の追加指示を取得
# 指定カテゴリのプロンプトを構築
_build_category_prompt() {
	local category="$1"
	local comments_block="$2"
	local classifications="${3:-}"
	# Map sub-categories to their template names
	local template_category="$category"
	case "$category" in
	game_question | game_status) template_category="game" ;;
	strategy_advice | comment_advice | general_question) template_category="default" ;;
	subscription | bits | other) template_category="default" ;;
	esac
	local template_file="$ELOOP_LIB_DIR/prompts/comment_response_${template_category}.md"
	if [ ! -f "$template_file" ]; then
		template_file="$ELOOP_LIB_DIR/prompts/comment_response_default.md"
	fi
	[ -f "$template_file" ] || return 1
	local out_file="$4"
	[ -z "$out_file" ] && return 1
	# ガチャのコンプリート言及を一度きりにするための指示文を組み立てる
	local gacha_completion_note=""
	if [ "$category" = "card_gacha" ]; then
		_build_gacha_completion_note "$comments_block"
		gacha_completion_note="$GACHA_COMPLETION_NOTE"
	fi
	[ -n "$gacha_completion_note" ] || gacha_completion_note="（今回はコンプリート関連の特記事項はありません。）"
	export gacha_completion_note
	export CATEGORY_COMMENTS="$comments_block"
	export COMMENT_CLASSIFICATIONS="$classifications"
	export twitch_comments_for_prompt="$comments_block"
	envsubst '${CATEGORY_COMMENTS} ${COMMENT_CLASSIFICATIONS} ${twitch_comments_for_prompt} ${_comment_persona} ${current_time} ${time_period} ${comment_batch_context} ${strategy_advice_candidates} ${comment_advice_candidates} ${codex_advice_candidates} ${comment_advice_context} ${previous_comments_context} ${recent_spoken_comment_context} ${comment_followup_hints} ${past_topics} ${celebration_history_context} ${comment_thumbnail_ocr_context} ${PAST_RADIO_TOPICS} ${RUSSIA_CREATION_HISTORY_FILE} ${SOVIET_CREATION_HISTORY_FILE} ${ROLLING_SCORES_FILE} ${game_state_context} ${_comment_ui_memo} ${_comment_channel_intro} ${sing_reference} ${_prediction_cycle_games} ${gacha_completion_note}' <"$template_file" >"$out_file"
}

_extract_sing_score() {
	python3 -c "$(cat <<'PY'
import json
import re
import sys

def iter_json_objects(s):
    """文字列中のトップレベル JSON オブジェクトを順に返す (ネスト対応)。"""
    i = 0
    n = len(s)
    while True:
        start = s.find("{", i)
        if start < 0:
            return
        depth = 0
        in_str = False
        esc = False
        j = start
        while j < n:
            c = s[j]
            if in_str:
                if esc:
                    esc = False
                elif c == "\\":
                    esc = True
                elif c == '"':
                    in_str = False
            else:
                if c == '"':
                    in_str = True
                elif c == "{":
                    depth += 1
                elif c == "}":
                    depth -= 1
                    if depth == 0:
                        yield s[start:j + 1]
                        i = j + 1
                        break
            j += 1
        else:
            return


def is_score(payload):
    return isinstance(payload, dict) and isinstance(payload.get("notes"), list)


text = sys.stdin.read()
lines = text.splitlines(keepends=True)
marker_re = re.compile(r"^[ \t]*===SING===[ \t]*$")

# 1) ===SING=== マーカーで囲われたブロックを優先して抽出
for start_idx, line in enumerate(lines):
    if not marker_re.match(line.rstrip("\n")):
        continue
    for end_idx in range(start_idx + 1, len(lines) + 1):
        candidate = "".join(lines[start_idx + 1:end_idx]).strip()
        if not candidate:
            continue
        try:
            payload = json.loads(candidate)
        except Exception:
            continue
        if is_score(payload):
            sys.stdout.write(candidate)
            raise SystemExit(0)
    raise SystemExit(1)

# 2) マーカー無しでも本文中の楽譜 JSON ("notes" を含む) を拾う
for obj in iter_json_objects(text):
    try:
        payload = json.loads(obj)
    except Exception:
        continue
    if is_score(payload):
        sys.stdout.write(obj)
        raise SystemExit(0)

raise SystemExit(1)
PY
)"
}

_remove_sing_score_block() {
	python3 -c "$(cat <<'PY'
import json
import re
import sys

def iter_json_objects(s):
    i = 0
    n = len(s)
    while True:
        start = s.find("{", i)
        if start < 0:
            return
        depth = 0
        in_str = False
        esc = False
        j = start
        while j < n:
            c = s[j]
            if in_str:
                if esc:
                    esc = False
                elif c == "\\":
                    esc = True
                elif c == '"':
                    in_str = False
            else:
                if c == '"':
                    in_str = True
                elif c == "{":
                    depth += 1
                elif c == "}":
                    depth -= 1
                    if depth == 0:
                        yield s[start:j + 1]
                        i = j + 1
                        break
            j += 1
        else:
            return


def is_score(payload):
    return isinstance(payload, dict) and isinstance(payload.get("notes"), list)


text = sys.stdin.read()
lines = text.splitlines(keepends=True)
marker_re = re.compile(r"^[ \t]*===SING===[ \t]*$")

# 1) ===SING=== マーカーで囲われた楽譜ブロックを全て除去
out_lines = []
i = 0
n = len(lines)
while i < n:
    if marker_re.match(lines[i].rstrip("\n")):
        buf = []
        j = i + 1
        while j < n and not marker_re.match(lines[j].rstrip("\n")):
            buf.append(lines[j])
            j += 1
        has_close = j < n and marker_re.match(lines[j].rstrip("\n"))
        candidate = "".join(buf).strip()
        is_sc = False
        if candidate:
            try:
                is_sc = is_score(json.loads(candidate))
            except Exception:
                pass
        if is_sc:
            i = j + 1 if has_close else j
            continue
        # 楽譜でない ===SING=== 行はマーカー行を残さず除去 (行そのものを落とす)
        if not candidate:
            i = j + 1 if has_close else j
            continue
        out_lines.append(lines[i])
        i += 1
        continue
    out_lines.append(lines[i])
    i += 1
text = "".join(out_lines)

# 2) 残ったインライン楽譜 JSON ("notes" を含む) を本文から除去
out = []
last_end = 0
dropped = False
for obj in iter_json_objects(text):
    try:
        if not is_score(json.loads(obj)):
            continue
    except Exception:
        continue
    out.append(text[last_end:text.find(obj, last_end)])
    last_end = text.find(obj, last_end) + len(obj)
    dropped = True
out.append(text[last_end:])
text = "".join(out)

if dropped and not text.strip():
    text = ""
sys.stdout.write(text)
PY
)"
}

_resolve_strategy_advice_file() {
	local mode="${1:-main}"
	if [ "$mode" = "soren91" ]; then
		printf '%s' "$SOREN91_STRATEGY_ADVICE_FILE"
	else
		printf '%s' "$MAIN_STRATEGY_ADVICE_FILE"
	fi
}

_strip_strategy_advice_mode_prefix() {
	printf '%s' "$1" | sed -E 's/^\[(main|soren|soren91)\][[:space:]]*//I'
}

_detect_strategy_advice_target_mode() {
	local advice_item="$1"
	local fallback_mode="${2:-main}"
	local normalized=""
	normalized=$(printf '%s' "$advice_item" | tr '[:upper:]' '[:lower:]')
	case "$normalized" in
	*"[soren91]"* | *"メリケン"* | *"メリケンai"* | *"soren91"* | *"対戦版"* | *"おじゃま"* | *"hold"* | *"next"* | *"順位"* | *"相手"* | *"盤面タイプ"*)
		printf '%s' "soren91"
		;;
	*"[main]"* | *"[soren]"* | *"中華ai"* | *"strategy.py"*)
		printf '%s' "main"
		;;
	*)
		printf '%s' "$fallback_mode"
		;;
	esac
}

_append_advice_item_to_file() {
	local advice_file="$1"
	local advice_item="$2"
	local log_label="$3"
	advice_item=$(printf '%s' "$advice_item" | tr '\n' ' ' | sed -E 's/[[:space:]]+/ /g; s/^ //; s/ $//')
	case "$advice_item" in
	"" | "（なし）" | "なし" | "（アドバイスなし）" | なし* | （アドバイスなし）*)
		return 0
		;;
	esac
	local advice_line="- $advice_item"
	[ -f "$advice_file" ] || : >"$advice_file"
	if grep -qE '^[[:space:]]*-?[[:space:]]*（なし）[[:space:]]*$' "$advice_file" 2>/dev/null; then
		grep -vxE '^[[:space:]]*-?[[:space:]]*（なし）[[:space:]]*$' "$advice_file" >"${advice_file}.tmp" 2>/dev/null || true
		mv "${advice_file}.tmp" "$advice_file" 2>/dev/null || true
	fi
	if grep -qxF -- "$advice_line" "$advice_file" 2>/dev/null; then
		return 0
	fi
	printf '%s\n' "$advice_line" >>"$advice_file"
	if [ -f "$advice_file" ] && [ "$(wc -l <"$advice_file")" -gt 150 ]; then
		tail -150 "$advice_file" >"${advice_file}.tmp"
		mv "${advice_file}.tmp" "$advice_file"
	fi
	log "[COMMENT] ${log_label}追記 → $advice_file"
}

_append_strategy_advice_item() {
	local advice_item="$1"
	local fallback_mode="${2:-main}"
	advice_item=$(_strip_strategy_advice_mode_prefix "$advice_item")
	local target_mode=""
	target_mode=$(_detect_strategy_advice_target_mode "$advice_item" "$fallback_mode")
	local advice_file=""
	advice_file=$(_resolve_strategy_advice_file "$target_mode")
	_append_advice_item_to_file "$advice_file" "$advice_item" "戦略アドバイス(${target_mode})"
}

_append_comment_advice_item() {
	local advice_item="$1"
	_append_advice_item_to_file "$COMMENT_ADVICE_FILE" "$advice_item" "コメント改善アドバイス"
}

_append_codex_advice_item() {
	local advice_item="$1"
	_append_advice_item_to_file "$CODEX_ADVICE_FILE" "$advice_item" "Codex改善アドバイス"
}

_append_soviet_theme_item() {
	local theme_item="$1"
	theme_item=$(printf '%s' "$theme_item" | tr '\n' ' ' | sed -E 's/[[:space:]]+/ /g; s/^ //; s/ $//')
	[ -n "$theme_item" ] || return 0
	if printf '%s\n' "$theme_item" | grep -Eq '獲得しました|連ガチャ|カードガチャ|【[A-Za-zァ-ヶー一-龠0-9 ]+】|[0-9]+[[:space:]]*種中'; then
		log "[COMMENT] ソ連テーマ追加スキップ（ガチャ/獲得文）: $theme_item"
		return 0
	fi
	if ! printf '%s\n' "$theme_item" | grep -Eq 'ソ連|ソビエト|ロシア|共産|冷戦|東側|東欧|KGB|スターリン|レーニン|フルシチョフ|ゴルバチョフ|ブレジネフ|ワルシャワ条約|コミンテルン|ボリシェヴィキ|チェルノブイリ|ガガーリン|スプートニク|宇宙開発|赤軍|クレムリン|バルト三国|ウクライナ|カザフ'; then
		log "[COMMENT] ソ連テーマ追加スキップ（非ソ連テーマ）: $theme_item"
		return 0
	fi
	# 「を深掘りして」で終わっていなければ付与
	if ! echo "$theme_item" | grep -q 'を深掘りして$'; then
		theme_item="${theme_item}を深掘りして"
	fi
	local soviet_themes_file="$ELOOP_LIB_DIR/data/radio_soviet_themes.txt"
	[ -f "$soviet_themes_file" ] || return 0
	# 重複チェック: キー部分（最初の句点 or 「を深掘り」の前）で既存テーマと比較
	local theme_key="${theme_item%%。*}"
	[ "$theme_key" = "$theme_item" ] && theme_key="${theme_item%%を深掘り*}"
	if grep -qF "$theme_key" "$soviet_themes_file" 2>/dev/null; then
		log "[COMMENT] ソ連テーマ重複スキップ: $theme_key"
		return 0
	fi
	printf '%s\n' "$theme_item" >>"$soviet_themes_file"
	log "[COMMENT] ソ連テーマ自動追加: $theme_item"
}

# ラジオ生成と同じ ai_generate_list() から呼ばれる候補検証。
# 非空でも、読み上げ本文へ整形した結果が無効なら次のCodexモデルへ進める。
_comment_is_valid_generation_candidate() {
	local raw="$1" cleaned
	[ -n "$raw" ] || return 1
	_contains_provider_error_text "$raw" && return 1
	cleaned=$(_clean_comment_talk "$raw" 1)
	cleaned=$(printf '%s' "$cleaned" | _sanitize_onair_text)
	[ -n "$cleaned" ] || return 1
	_is_valid_comment_talk "$cleaned"
}

generate_comment_response() {
	local viewer_chat_source="${1:-twitch}"
	local viewer_chat_script="./twitch_chat.sh"
	local viewer_chat_outfile="tmp/twitch_comments.txt"
	local viewer_chat_dir="tmp/.twitch_chat"
	local viewer_chat_label="Twitch"
	case "$viewer_chat_source" in
	youtube|YouTube|yt)
		viewer_chat_source="youtube"
		viewer_chat_script="./youtube_chat.sh"
		viewer_chat_outfile="${YOUTUBE_CHAT_OUTFILE:-tmp/youtube_comments.txt}"
		viewer_chat_dir="${YOUTUBE_CHAT_DIR:-tmp/.youtube_chat}"
		viewer_chat_label="YouTube"
		;;
	twitch|Twitch|"")
		viewer_chat_source="twitch"
		;;
	*)
		log "[COMMENT] unknown viewer chat source: $viewer_chat_source"
		return 1
		;;
	esac
	_kill_comment_gen
	mkdir -p "tmp/.twitch_chat" "$viewer_chat_dir" "tmp/.viewer_chat"

	# 先に未読を取得。生成失敗時はpendingを維持し、成功時のみ処理済み行を削除する。
	"$viewer_chat_script" fetch

	local twitch_comments=""
	if [ -f "$viewer_chat_outfile" ] && [ -s "$viewer_chat_outfile" ]; then
		twitch_comments=$(cat "$viewer_chat_outfile")
	fi
	[ -z "$twitch_comments" ] && return

	# Provider障害時はpendingを保持したまま待機し、短周期の再試行で429を長引かせない。
	local comment_failure_backoff_remaining=0
	comment_failure_backoff_remaining=$(_comment_failure_backoff_remaining)
	if [ "$comment_failure_backoff_remaining" -gt 0 ]; then
		return 0
	fi

	# 個別コメント行の重複フィルタ（ack-batch失敗で残留した行を除外）
	local twitch_comments_original="$twitch_comments"
	twitch_comments=$(_filter_already_processed_comment_lines "$twitch_comments")
	if [ -z "$twitch_comments" ]; then
		log "[COMMENT] 全コメント行が個別重複チェックにより処理済み → スキップ"
		# pending.log から残留行を消化する
		local ack_tmp
		ack_tmp=$(mktemp /tmp/eloop_comment_ack_XXXXXXXX 2>/dev/null || echo "${viewer_chat_dir}/comment_ack_$(date +%s)_${RANDOM}.txt")
		printf '%s\n' "$twitch_comments_original" >"$ack_tmp"
		"$viewer_chat_script" ack-batch "$ack_tmp"
		rm -f "$ack_tmp"
		return
	fi

	# コメントが画面や現在状態を参照している時だけサムネイルOCRを使う。
	# 通常雑談で毎回OCRすると、返信開始前に数十秒詰まることがある。
	local comment_screenshot="tmp/.comment_queue/comment_screenshot.jpg"
	local comment_thumbnail_ocr_context="（通常コメントのためサムネイルOCR省略）"
	if [ "$viewer_chat_source" = "twitch" ] && _comment_needs_thumbnail_context "$twitch_comments" && curl -sf -o "$comment_screenshot" -m 5 "https://static-cdn.jtvnw.net/previews-ttv/live_user_azumagbanjo-1280x720.jpg" 2>/dev/null; then
		log "[COMMENT] 配信サムネイル取得: $comment_screenshot"
		local comment_thumbnail_ocr_json=""
		local comment_ocr_script="$ELOOP_LIB_DIR/soren91/result_screen_ocr.mjs"
		if [ -f "$comment_ocr_script" ]; then
			comment_thumbnail_ocr_json=$(timeout --kill-after=5s 25s node "$comment_ocr_script" "$comment_screenshot" 2>/dev/null || true)
			if [ -n "$comment_thumbnail_ocr_json" ]; then
				comment_thumbnail_ocr_context=$(printf '%s' "$comment_thumbnail_ocr_json" | python3 -c "import json,sys; d=json.load(sys.stdin); lines=(d.get('lines') or [])[:8]; print('\n'.join(f'- {line}' for line in lines) if lines else '（OCRで読める文字なし）')" 2>/dev/null)
			else
				comment_thumbnail_ocr_context="（配信サムネイルOCR失敗）"
			fi
		fi
	else
		rm -f "$comment_screenshot"
	fi
	rm -f "$comment_screenshot" 2>/dev/null || true

	local comment_batch_file=""
	comment_batch_file=$(mktemp /tmp/eloop_comment_batch_XXXXXXXX 2>/dev/null || true)
	[ -z "$comment_batch_file" ] && comment_batch_file="${viewer_chat_dir}/comment_batch_$(date +%s)_${RANDOM}.txt"
	# ack-batch用にオリジナル全行を書き込む（フィルタ済み行も pending から確実に消化するため）
	printf '%s\n' "$twitch_comments_original" >"$comment_batch_file"

	local comment_batch_hash=""
	comment_batch_hash=$(_comment_hash_text "$twitch_comments" 2>/dev/null || echo "")
	if _is_recent_comment_batch_processed "$comment_batch_hash"; then
		log "[COMMENT] 同一コメントバッチを直近で処理済みのためスキップ (batch=$comment_batch_hash)"
		"$viewer_chat_script" ack-batch "$comment_batch_file"
		rm -f "$comment_batch_file"
		return
	fi
	if _is_comment_batch_inflight "$comment_batch_hash"; then
		log "[COMMENT] 同一コメントバッチを生成中のためスキップ (batch=$comment_batch_hash)"
		rm -f "$comment_batch_file"
		return
	fi

	local twitch_comments_for_prompt
	twitch_comments_for_prompt=$(printf '%s' "$twitch_comments" | sed 's|https\?://[^ \t]*||g')
	if [ -z "$twitch_comments_for_prompt" ]; then
		log "[COMMENT] 有効なコメント本文がないため返信生成をスキップ"
		if "$viewer_chat_script" ack-batch "$comment_batch_file"; then
			_record_processed_comment_lines "$twitch_comments"
		else
			log "[COMMENT] ack-batch 失敗 → 個別行ハッシュ記録で次回重複除外"
			_record_processed_comment_lines "$twitch_comments"
		fi
		_mark_comment_batch_processed "$comment_batch_hash"
		rm -f "$comment_batch_file"
		return
	fi

	local comment_prompt_batch_file=""
	comment_prompt_batch_file=$(mktemp /tmp/eloop_comment_prompt_batch_XXXXXXXX 2>/dev/null || true)
	[ -z "$comment_prompt_batch_file" ] && comment_prompt_batch_file="${viewer_chat_dir}/comment_prompt_batch_$(date +%s)_${RANDOM}.txt"
	printf '%s\n' "$twitch_comments_for_prompt" >"$comment_prompt_batch_file"
	local english_comment_count=0

	local past_topics=""
	past_topics=$(_radio_past_topics_block)
	local game_state_context=""
	game_state_context=$(_build_comment_game_context "$GAME_STATE")
	local celebration_history_context=""
	celebration_history_context=$(_build_comment_celebration_history_context)

	local comment_context_history_file="${COMMENT_CONTEXT_HISTORY_FILE:-tmp/.viewer_chat/comment_context_history.log}"
	mkdir -p "$(dirname "$comment_context_history_file")" 2>/dev/null || true
	local previous_comments_context=""
	[ -f "$comment_context_history_file" ] && previous_comments_context=$(tail -12 "$comment_context_history_file" 2>/dev/null | _sanitize_comment_prompt_context)
	# 重複追記防止: 直前の内容と同一でなければ追記
	local _last_context_lines=""
	if [ -f "$comment_context_history_file" ]; then
		local _new_line_count
		_new_line_count=$(printf '%s\n' "$twitch_comments_for_prompt" | wc -l)
		_last_context_lines=$(tail -"${_new_line_count}" "$comment_context_history_file" 2>/dev/null)
	fi
	if [ "$_last_context_lines" != "$twitch_comments_for_prompt" ]; then
		printf '%s\n' "$twitch_comments_for_prompt" >>"$comment_context_history_file"
	fi
	if [ -f "$comment_context_history_file" ] && [ "$(wc -l <"$comment_context_history_file")" -gt 300 ]; then
		tail -300 "$comment_context_history_file" >"${comment_context_history_file}.tmp"
		mv "${comment_context_history_file}.tmp" "$comment_context_history_file"
	fi

	local comment_batch_context=""
	comment_batch_context=$(printf '%s\n' "$twitch_comments_for_prompt" | _format_comment_batch_context | _sanitize_comment_prompt_context)
	local recent_spoken_comment_context=""
	recent_spoken_comment_context=$(_build_recent_spoken_comment_context | _sanitize_comment_prompt_context)
	local comment_followup_hints=""
	comment_followup_hints=$(_build_comment_followup_hints "$comment_prompt_batch_file" | _sanitize_comment_prompt_context)
	local comment_mode_for_advice=""
	comment_mode_for_advice=$(_broadcast_host_mode 2>/dev/null || printf '%s' "main")
	local structured_advice_candidates=""
	structured_advice_candidates=$(_extract_structured_advice_from_comments "$comment_prompt_batch_file" "$comment_mode_for_advice")
	local strategy_advice_candidates=""
	local strategy_advice_candidates_main=""
	local strategy_advice_candidates_soren91=""
	local comment_advice_candidates=""
	local codex_advice_candidates=""
	if [ -n "$structured_advice_candidates" ]; then
		while IFS=$'\t' read -r advice_kind advice_mode advice_text; do
			[ -n "$advice_text" ] || continue
			case "$advice_kind" in
			strategy)
				if [ "$advice_mode" = "main" ]; then
					if [ -n "$strategy_advice_candidates_main" ]; then
						strategy_advice_candidates_main="${strategy_advice_candidates_main}
$advice_text"
					else
						strategy_advice_candidates_main="$advice_text"
					fi
				elif [ "$advice_mode" = "soren91" ]; then
					if [ -n "$strategy_advice_candidates_soren91" ]; then
						strategy_advice_candidates_soren91="${strategy_advice_candidates_soren91}
$advice_text"
					else
						strategy_advice_candidates_soren91="$advice_text"
					fi
				fi
				;;
			comment)
				if [ -n "$comment_advice_candidates" ]; then
					comment_advice_candidates="${comment_advice_candidates}
$advice_text"
				else
					comment_advice_candidates="$advice_text"
				fi
				;;
			codex)
				if [ -n "$codex_advice_candidates" ]; then
					codex_advice_candidates="${codex_advice_candidates}
$advice_text"
				else
					codex_advice_candidates="$advice_text"
				fi
				;;
			esac
		done <<<"$structured_advice_candidates"
	fi
	if [ "$comment_mode_for_advice" = "soren91" ]; then
		strategy_advice_candidates="$strategy_advice_candidates_soren91"
	else
		strategy_advice_candidates="$strategy_advice_candidates_main"
	fi
	local comment_advice_context=""
	comment_advice_context=$(_read_advice_context_tail "$COMMENT_ADVICE_FILE" 15 | _sanitize_comment_prompt_context)
	strategy_advice_candidates=$(printf '%s' "${strategy_advice_candidates:-}" | _sanitize_comment_prompt_context)
	strategy_advice_candidates_main=$(printf '%s' "${strategy_advice_candidates_main:-}" | _sanitize_comment_prompt_context)
	strategy_advice_candidates_soren91=$(printf '%s' "${strategy_advice_candidates_soren91:-}" | _sanitize_comment_prompt_context)
	comment_advice_candidates=$(printf '%s' "${comment_advice_candidates:-}" | _sanitize_comment_prompt_context)
	codex_advice_candidates=$(printf '%s' "${codex_advice_candidates:-}" | _sanitize_comment_prompt_context)

	# コメント分類器を実行
	local classification_json=""
	classification_json=$(_classify_comments "$comment_prompt_batch_file")
	if [ -n "$classification_json" ]; then
		english_comment_count=$(_comment_classification_english_count "$classification_json" 2>/dev/null || printf '0')
		case "$english_comment_count" in
		'' | *[!0-9]*) english_comment_count=0 ;;
		esac
		if [ "$english_comment_count" -gt 0 ]; then
			log "[COMMENT] 分類器が英語コメント${english_comment_count}件を検出 → 日本語返信を生成後、対象だけ英訳"
		fi
	fi
	local dominant_category=""
	if [ -n "$classification_json" ]; then
		dominant_category=$(python3 -c "
import json, sys
json_str = sys.stdin.read()
try:
    data = json.loads(json_str)
except Exception:
    raise SystemExit(1)
if not isinstance(data, list) or len(data) == 0:
    raise SystemExit(1)
counts = {}
for item in data:
    cat = item.get('category', 'chitchat')
    counts[cat] = counts.get(cat, 0) + 1
total = len(data)
dominant = max(counts, key=counts.get)
ratio = counts[dominant] / total
if ratio > 0.8:
    print(dominant)
else:
    if len(counts) == 1:
        print(list(counts.keys())[0])
    else:
        print('mixed')
" <<<"$classification_json")
		log "[COMMENT] 分類結果: ${dominant_category:-取得失敗} (classification: ${classification_json:0:200})"
		local queued_stream_bug_reports=""
		queued_stream_bug_reports=$(_queue_stream_bug_reports_from_classification "$classification_json" "$viewer_chat_source" "$comment_batch_hash" 2>/dev/null || true)
		if [ -n "$queued_stream_bug_reports" ]; then
			log "[COMMENT] 配信不具合レポートをCodexキューへ追加: $(printf '%s' "$queued_stream_bug_reports" | tr '\n' ' ')"
		fi
	fi
	if [ -n "$dominant_category" ] && ! _comment_category_allows_advice_append "$dominant_category"; then
		strategy_advice_candidates=""
		strategy_advice_candidates_main=""
		strategy_advice_candidates_soren91=""
		comment_advice_candidates=""
		codex_advice_candidates=""
		log "[COMMENT] 非助言カテゴリのためアドバイス保存を抑制: ${dominant_category}"
	fi

	local current_time current_hour time_period
	current_time=$(date '+%H:%M')
	current_hour=$(date '+%H')
	if [ "$current_hour" -ge 5 ] && [ "$current_hour" -lt 9 ]; then
		time_period="朝"
	elif [ "$current_hour" -ge 9 ] && [ "$current_hour" -lt 12 ]; then
		time_period="午前"
	elif [ "$current_hour" -ge 12 ] && [ "$current_hour" -lt 17 ]; then
		time_period="午後"
	elif [ "$current_hour" -ge 17 ] && [ "$current_hour" -lt 21 ]; then
		time_period="夕方"
	elif [ "$current_hour" -ge 21 ] || [ "$current_hour" -lt 2 ]; then
		time_period="夜"
	else
		time_period="未明"
	fi

	local comment_parent_pid comment_started_at
	comment_parent_pid="${BASHPID:-$(_my_pid)}"
	comment_started_at=$(date +%s)
	echo "generating:comment:${comment_started_at}" >$COMMENT_GEN_STATE_FILE
	_mark_comment_batch_inflight "$comment_batch_hash"
	export dominant_category

	(
		_cg_my_pid="${BASHPID:-$(_my_pid)}"
		local comment_speech_meta_file=""
		_cleanup_comment_gen_worker() {
			local raw file_pid file_started_at
			raw=$(cat tmp/.twitch_chat/comment_gen.pid 2>/dev/null || true)
			IFS='|' read -r file_pid _ file_started_at <<<"$raw"
			if [ "$file_pid" = "$_cg_my_pid" ] || [ "$file_started_at" = "$comment_started_at" ]; then
				rm -f tmp/.twitch_chat/comment_gen.pid
			fi
			rm -f $COMMENT_GEN_STATE_FILE
			_clear_comment_batch_inflight "$comment_batch_hash"
			[ -n "$comment_batch_file" ] && rm -f "$comment_batch_file"
			[ -n "$comment_prompt_batch_file" ] && rm -f "$comment_prompt_batch_file"
			[ -n "$comment_speech_meta_file" ] && rm -f "$comment_speech_meta_file"
		}
		trap '_cleanup_comment_gen_worker' EXIT

		local sing_reference=""
		# sing_reference は外部ファイル参照に移行済み（プロンプト埋め込み不要）

		# soren91 (メリケンAI) プレイ中はペルソナ・UI説明を切り替え
		local _comment_mode_generated=""
		_comment_mode_generated=$(_broadcast_host_mode 2>/dev/null || printf '%s' "main")
		local _comment_persona _comment_ui_memo _comment_channel_intro
		local _comment_length_policy="" _comment_retry_length_policy=""
		local _mode_suffix="main"
		[ "$_comment_mode_generated" = "soren91" ] && _mode_suffix="soren91"
		if [ "$_comment_mode_generated" = "soren91" ]; then
			strategy_advice_candidates="$strategy_advice_candidates_soren91"
		else
			strategy_advice_candidates="$strategy_advice_candidates_main"
		fi
		_comment_persona=$(cat "$ELOOP_LIB_DIR/prompts/comment_persona_${_mode_suffix}.md" 2>/dev/null)
		_comment_ui_memo=$(cat "$ELOOP_LIB_DIR/prompts/comment_ui_memo_${_mode_suffix}.md" 2>/dev/null)
		_comment_channel_intro=$(cat "$ELOOP_LIB_DIR/prompts/comment_channel_intro_${_mode_suffix}.md" 2>/dev/null)
		if [ "$_comment_mode_generated" = "soren91" ]; then
			_comment_length_policy=$'- メリケンAIモードの通常コメント返しは、各コメントにつき3-5文を基本にすること。短い反応コメントでも短い返答で十分とは考えず、感想・理由・補足・軽い問いかけのどれかを足して、会話として少し深く広げること\n- メリケンAIらしく、各返答に短い皮肉・ツッコミ・意外な比喩のどれかを一つ入れること。ただし質問の答えや真面目な話題を冗談で置き換えないこと\n- ただし azumagbanjo、azumagdev、または表示名「あずまぐ」の「AがBを獲得しました」のようなカードガチャ結果コメントだけは例外。そこだけは反応1文 + 本題2-3文を目安に、カード説明を長々広げすぎないこと'
			_comment_retry_length_policy='- 今回がメリケンAIモードなら、通常コメント返しは各コメントへ3-5文を基本にしてください。短い反応コメントでも短い返答で済ませず、会話として厚めに返してください。各返答に短い皮肉・ツッコミ・意外な比喩のどれかを一つ入れてください。ただしカードガチャ結果コメントだけは例外で、反応1文 + 本題2-3文を目安にしてください。'
		fi
		if [ -z "$_comment_persona" ]; then
			log "[COMMENT] ERROR: prompts/comment_persona_${_mode_suffix}.md not found, skip"
			return 1
		fi

		# Pre-resolve defaults for envsubst
		comment_batch_context="${comment_batch_context:-（なし）}"
		strategy_advice_candidates="${strategy_advice_candidates:-（なし）}"
		comment_advice_candidates="${comment_advice_candidates:-（なし）}"
		codex_advice_candidates="${codex_advice_candidates:-（なし）}"
		comment_advice_context="${comment_advice_context:-（なし）}"
		previous_comments_context=$(printf '%s' "${previous_comments_context:-（なし）}" | _sanitize_comment_prompt_context)
		recent_spoken_comment_context=$(printf '%s' "${recent_spoken_comment_context:-（なし）}" | _sanitize_comment_prompt_context)
		comment_followup_hints=$(printf '%s' "${comment_followup_hints:-（なし）}" | _sanitize_comment_prompt_context)
		celebration_history_context="${celebration_history_context:-（なし）}"
		comment_thumbnail_ocr_context=$(printf '%s' "${comment_thumbnail_ocr_context:-（なし）}" | _sanitize_comment_prompt_context)
		game_state_context=$(printf '%s' "${game_state_context:-（取得失敗）}" | _sanitize_comment_prompt_context)

		# Export all template variables (safe: inside subshell)
		local _prediction_cycle_games="${MIN_GAMES_BEFORE_IMPROVE:-12}"
		export _comment_persona current_time time_period twitch_comments_for_prompt \
			comment_batch_context strategy_advice_candidates comment_advice_candidates codex_advice_candidates comment_advice_context previous_comments_context \
			recent_spoken_comment_context comment_followup_hints past_topics \
			celebration_history_context comment_thumbnail_ocr_context \
			PAST_RADIO_TOPICS RUSSIA_CREATION_HISTORY_FILE SOVIET_CREATION_HISTORY_FILE ROLLING_SCORES_FILE \
			game_state_context _comment_ui_memo _comment_channel_intro _comment_length_policy sing_reference \
			_prediction_cycle_games

		# 分類結果に基づきプロンプトを選択
		local comment_prompt_file=""
		comment_prompt_file=$(mktemp /tmp/eloop_comment_prompt_XXXXXXXX)
		if [ -n "$dominant_category" ] && [ "$dominant_category" != "mixed" ]; then
			log "[COMMENT] カテゴリ別プロンプト使用: ${dominant_category}"
			local formatted_classifications=""
			if [ -n "$classification_json" ]; then
				formatted_classifications=$(python3 - "$classification_json" "$comment_prompt_batch_file" <<'PY' 2>/dev/null
import json, sys
data = json.loads(sys.argv[1])
batch_file = sys.argv[2]
source = []
try:
    with open(batch_file, 'r', encoding='utf-8', errors='ignore') as f:
        source = [line.rstrip('\n') for line in f if line.strip()]
except Exception:
    source = []
for item in data:
    idx = item.get('index', 0)
    try:
        idx_num = int(idx)
    except Exception:
        idx_num = 0
    if 1 <= idx_num <= len(source):
        raw = source[idx_num - 1]
        if ': ' in raw:
            user, comment = raw.split(': ', 1)
        else:
            user, comment = '', raw
    else:
        user, comment = '', ''
    cat = item.get('category', 'chitchat')
    english = 'english' if item.get('is_english') is True else 'japanese/other'
    if comment:
        print(f'[{idx_num}] {user}: {comment} -> {cat} -> {english}')
PY
)
				formatted_classifications=$(printf '%s' "$formatted_classifications" | _sanitize_comment_prompt_context)
			fi
			_build_category_prompt "$dominant_category" "$twitch_comments_for_prompt" "$formatted_classifications" "$comment_prompt_file" 2>/dev/null
			if [ ! -s "$comment_prompt_file" ]; then
				log "[COMMENT] カテゴリ別プロンプト生成失敗 -> デフォルトにフォールバック"
				rm -f "$comment_prompt_file"
				comment_prompt_file=$(mktemp /tmp/eloop_comment_prompt_XXXXXXXX)
				local _comment_template="$ELOOP_LIB_DIR/prompts/comment_template.md"
				if [ ! -f "$_comment_template" ]; then
					log "[COMMENT] ERROR: prompts/comment_template.md not found, skip"
					rm -f "$comment_prompt_file"
					return 1
				fi
				envsubst '${_comment_persona} ${current_time} ${time_period} ${twitch_comments_for_prompt} ${comment_batch_context} ${strategy_advice_candidates} ${comment_advice_candidates} ${codex_advice_candidates} ${comment_advice_context} ${previous_comments_context} ${recent_spoken_comment_context} ${comment_followup_hints} ${past_topics} ${celebration_history_context} ${comment_thumbnail_ocr_context} ${PAST_RADIO_TOPICS} ${RUSSIA_CREATION_HISTORY_FILE} ${SOVIET_CREATION_HISTORY_FILE} ${ROLLING_SCORES_FILE} ${game_state_context} ${_comment_ui_memo} ${_comment_channel_intro} ${sing_reference} ${_prediction_cycle_games}' \
					<"$_comment_template" >"$comment_prompt_file"
			fi
		else
			local _comment_template="$ELOOP_LIB_DIR/prompts/comment_template.md"
			if [ ! -f "$_comment_template" ]; then
				log "[COMMENT] ERROR: prompts/comment_template.md not found, skip"
				rm -f "$comment_prompt_file"
				return 1
			fi
			envsubst '${_comment_persona} ${current_time} ${time_period} ${twitch_comments_for_prompt} ${comment_batch_context} ${strategy_advice_candidates} ${comment_advice_candidates} ${codex_advice_candidates} ${comment_advice_context} ${previous_comments_context} ${recent_spoken_comment_context} ${comment_followup_hints} ${past_topics} ${celebration_history_context} ${comment_thumbnail_ocr_context} ${PAST_RADIO_TOPICS} ${RUSSIA_CREATION_HISTORY_FILE} ${SOVIET_CREATION_HISTORY_FILE} ${ROLLING_SCORES_FILE} ${game_state_context} ${_comment_ui_memo} ${_comment_channel_intro} ${sing_reference} ${_prediction_cycle_games}' \
				<"$_comment_template" >"$comment_prompt_file"
		fi

		cat >>"$comment_prompt_file" <<'JAPANESECOMMENT'

【返信生成の出力契約】
- すべてのコメントへ、元の順番どおりに日本語で返答してください。英語コメントにも、この段階では日本語の返答だけを書いてください。
- 1コメントにつき1段落とし、コメントの間は空行1行で区切ってください。言語名の見出し、制御用マーカー、Markdown、補足説明は出力しないでください。
- 英語コメントへの英語版は後段の翻訳処理で作るため、ここで英語文を混ぜたり、同じ返答を二重に書いたりしないでください。
JAPANESECOMMENT

		local comment_retry_max="${COMMENT_RESPONSE_RETRY_MAX:-3}"
		case "$comment_retry_max" in
		'' | *[!0-9]*) comment_retry_max=3 ;;
		esac
		[ "$comment_retry_max" -lt 1 ] && comment_retry_max=1
		local attempt=1 generation_ok=false
		local comment_agent_list=""
		if [ "$_comment_mode_generated" = "soren91" ]; then
			comment_agent_list="${COMMENT_SOREN91_AGENT:-codex:minimax-m3}"
			if [ -n "${COMMENT_SOREN91_FALLBACK:-}" ] && [ "$COMMENT_SOREN91_FALLBACK" != "$comment_agent_list" ]; then
				comment_agent_list="${comment_agent_list},${COMMENT_SOREN91_FALLBACK}"
			fi
		else
			comment_agent_list="${COMMENT_AGENTS:-codex:minimax-m3}"
		fi
		# ピーク時間帯は候補順序のみ入替え（MiniMax優先 / DeepSeekはフォールバックに温存）
		local comment_agent_list_before="$comment_agent_list"
		local comment_peak_note=""
		comment_agent_list=$(_peak_priority_agent_list "$comment_agent_list")
		if [ "$comment_agent_list" != "$comment_agent_list_before" ]; then
			comment_peak_note=" peak=1"
		fi
		local comments_talk="" comment_model_used="" generation_rate_limited=false
		echo "generating:comment:$(date +%s)" >$COMMENT_GEN_STATE_FILE
		log "[COMMENT] コメント返し生成中... (source=${viewer_chat_label}, agents=${comment_agent_list}${comment_peak_note}, max_retry=${comment_retry_max})"

		while [ "$attempt" -le "$comment_retry_max" ]; do
			[ -n "$comment_speech_meta_file" ] && rm -f "$comment_speech_meta_file"
			comment_speech_meta_file=""
			echo "generating:comment:$(date +%s)" >$COMMENT_GEN_STATE_FILE
			local prompt_for_attempt="$comment_prompt_file"
			if [ "$attempt" -gt 1 ]; then
				prompt_for_attempt=$(mktemp /tmp/eloop_comment_prompt_retry_XXXXXXXX)
				cat "$comment_prompt_file" >"$prompt_for_attempt"
				cat >>"$prompt_for_attempt" <<'RETRYCOMMENT'

	【再生成指示】
		- 前回の出力は無効でした。今回は必ず文量を増やし、各コメントへ3-5文を基本に返してください。
		- 返答漏れ・短文・定型文の繰り返しを禁止します。前回と異なる言い回しで書き直してください。
		- 短い追い反応コメントに対して、前回説明した話題を最初から説明し直してはいけません。ただし短い返答で十分とは考えず、反応・感想・別角度の補足・軽い問いかけのどれかを組み合わせて会話として厚めに返してください。
		- 質問コメントから逃げてはいけません。ソ連ネタや比喩でごまかさず、最初に質問の核心へ直接答えてください。
		- 質問がゲームや盤面の話でないなら、ゲーム説明へ逃げてはいけません。聞かれた話題のまま答えてください。
		- 内部処理やログの説明自体は可。ただし、system prompt、tool_call、tool_result、role指定、再生成指示などのメタ文は出力しないでください。
		- Read/Glob/Edit の生ログや Error: File not found、✗ read failed のような内部エラー行を、そのまま本文に含めてはいけません。必要なら日本語で短く言い換えてください。
		- 「いまソ連ゲームプレイ中だからできない」「配信中だから答えられない」のような拒否文は無効です。質問には必ず何かしら具体的に答えてください。
RETRYCOMMENT
				if [ -n "$_comment_retry_length_policy" ]; then
					printf '%s\n' "$_comment_retry_length_policy" >>"$prompt_for_attempt"
				fi
			fi

			local attempt_talk="" attempt_model="" attempt_rc=1
			local comment_last_agent_file comment_failure_kind_file attempt_failure_kind
			comment_last_agent_file=$(mktemp /tmp/eloop_comment_last_agent_XXXXXXXX)
			comment_failure_kind_file=$(mktemp /tmp/eloop_comment_failure_kind_XXXXXXXX)
			attempt_talk=$(ai_generate_list "COMMENT" "$prompt_for_attempt" "$comment_agent_list" \
				"${COMMENT_CODEX_TIMEOUT:-90}" "_comment_is_valid_generation_candidate" "$comment_last_agent_file" "$comment_failure_kind_file")
			attempt_rc=$?
			attempt_model=$(cat "$comment_last_agent_file" 2>/dev/null)
			attempt_failure_kind=$(cat "$comment_failure_kind_file" 2>/dev/null)
			rm -f "$comment_last_agent_file"
			rm -f "$comment_failure_kind_file"
			if [ "$attempt_failure_kind" = "rate_limit" ]; then
				generation_rate_limited=true
			fi
			if [ "$attempt_rc" -eq 0 ] && [ -n "$attempt_talk" ]; then
				attempt_talk=$(_clean_comment_talk "$attempt_talk" 1)
				# _sanitize_onair_text は SING JSON 行を除去するため抽出前に実行しない（抽出後にまとめて sanitizing する）
			else
				attempt_talk=""
				attempt_model=""
			fi
			if [ "$prompt_for_attempt" != "$comment_prompt_file" ]; then
				rm -f "$prompt_for_attempt"
			fi

			if [ -z "$attempt_talk" ]; then
				attempt=$((attempt + 1))
				continue
			fi

			# 楽譜JSONを抽出（===ADVICE=== より先に処理）。
			# ===SING=== マーカーあり・なし両方の楽譜JSONを扱い、
			# 本文から必ず除去してJSONが読み上げられないようにする。
			# sanitize 前に抽出することで、==SING== 内の JSON が sanitize で誤って除去されるのを防ぐ。
			local sing_score=""
			if echo "$attempt_talk" | grep -Eq '^[[:space:]]*===SING===|"notes"[[:space:]]*:'; then
				sing_score=$(printf '%s' "$attempt_talk" | _extract_sing_score || true)
				attempt_talk=$(printf '%s' "$attempt_talk" | _remove_sing_score_block)
				if [ -n "$sing_score" ]; then
					log "[COMMENT] 楽譜JSON抽出 (${#sing_score} chars)"
				fi
			fi
			# 歌唱宣言ありだが ===SING=== なし → デフォルト楽譜（きらきら星）で補完
			# 「歌わせていただきます」を含む敬体バリエーションも拾う。sing_request分類なら宣言句が無くても補完する。
			if [ -z "$sing_score" ] && { echo "$attempt_talk" | grep -Eq '歌わせて|歌います|歌ってみます|歌いましょう|歌をお届け|歌声をお届け|お歌|うたいます|をどうぞ' || [ "${dominant_category:-}" = "sing_request" ]; }; then
				log "[COMMENT] 歌唱宣言あり but ===SING=== なし → デフォルト楽譜で補完"
				sing_score='{"notes":[{"key":null,"frame_length":15,"lyric":""},{"key":60,"frame_length":45,"lyric":"き"},{"key":60,"frame_length":45,"lyric":"ら"},{"key":67,"frame_length":45,"lyric":"き"},{"key":67,"frame_length":45,"lyric":"ら"},{"key":69,"frame_length":45,"lyric":"ひ"},{"key":69,"frame_length":45,"lyric":"か"},{"key":67,"frame_length":90,"lyric":"る"},{"key":null,"frame_length":10,"lyric":""},{"key":65,"frame_length":45,"lyric":"お"},{"key":65,"frame_length":45,"lyric":"そ"},{"key":64,"frame_length":45,"lyric":"ら"},{"key":64,"frame_length":45,"lyric":"の"},{"key":62,"frame_length":45,"lyric":"ほ"},{"key":62,"frame_length":45,"lyric":"し"},{"key":60,"frame_length":90,"lyric":"よ"},{"key":null,"frame_length":15,"lyric":""}]}'
			fi

			# ソ連テーマを抽出
			local soviet_theme_part=""
			if echo "$attempt_talk" | grep -q '^===SOVIET_THEME==='; then
				soviet_theme_part=$(echo "$attempt_talk" | sed -n '/^===SOVIET_THEME===/,/^===SOVIET_THEME===/ p' | sed '1d;$d')
				attempt_talk=$(echo "$attempt_talk" | sed '/^===SOVIET_THEME===/,/^===SOVIET_THEME===/ d')
			fi

			# 改善メモを抽出（本文確定後に追記する）
			local comment_advice_part comment_advice_item=""
			comment_advice_part=$(printf '%s' "$attempt_talk" | _extract_named_block "COMMENT_ADVICE")
			if [ -n "$comment_advice_part" ]; then
				comment_advice_item=$(printf '%s' "$comment_advice_part" | tr '\n' ' ' | sed -E 's/[[:space:]]+/ /g; s/^ //; s/ $//')
				attempt_talk=$(printf '%s' "$attempt_talk" | _remove_named_block "COMMENT_ADVICE")
			fi

			local advice_part advice_item=""
			advice_part=$(printf '%s' "$attempt_talk" | _extract_named_block "ADVICE")
			advice_item=""
			if [ -n "$advice_part" ]; then
				advice_item=$(printf '%s' "$advice_part" | tr '\n' ' ' | sed -E 's/[[:space:]]+/ /g; s/^ //; s/ $//')
				attempt_talk=$(printf '%s' "$attempt_talk" | _remove_named_block "ADVICE")
			fi

			local codex_advice_part codex_advice_item=""
			codex_advice_part=$(printf '%s' "$attempt_talk" | _extract_named_block "CODEX_ADVICE")
			if [ -n "$codex_advice_part" ]; then
				codex_advice_item=$(printf '%s' "$codex_advice_part" | tr '\n' ' ' | sed -E 's/[[:space:]]+/ /g; s/^ //; s/ $//')
				attempt_talk=$(printf '%s' "$attempt_talk" | _remove_named_block "CODEX_ADVICE")
			fi

			attempt_talk=$(_clean_comment_talk "$attempt_talk" 1)
			attempt_talk=$(printf '%s' "$attempt_talk" | _sanitize_onair_text)
			attempt_talk=$(printf '%s' "$attempt_talk" | _normalize_radio_tone)

			# 日本語本文の検証は従来どおり行う。英語判定は分類器の結果だけを
			# 使い、生成本文の言語推測や特殊マーカー解析は行わない。
			if ! _is_valid_comment_talk "$attempt_talk"; then
				log "[COMMENT] 最終本文が不正/短文のため再生成 (attempt ${attempt}/${comment_retry_max})"
				attempt=$((attempt + 1))
				continue
			fi

			# 英語コメントがある場合だけ、対応する日本語段落を別呼出しで英訳する。
			# 翻訳の失敗・段落数不一致は日本語返信をそのまま採用し、コメント
			# キュー全体のバックオフ原因にしない。
			if [ "${english_comment_count:-0}" -gt 0 ]; then
				local translation_prompt_file="" translation_text="" translation_model=""
				translation_prompt_file=$(mktemp /tmp/eloop_comment_translation_prompt_XXXXXXXX 2>/dev/null || true)
				if [ -n "$translation_prompt_file" ] && printf '%s' "$attempt_talk" | _comment_build_translation_prompt "$classification_json" >"$translation_prompt_file" 2>/dev/null; then
					local translation_agents
					translation_agents=$(_peak_priority_agent_list "${COMMENT_TRANSLATION_AGENTS:-$comment_agent_list}")
					local translation_last_agent_file=""
					translation_last_agent_file=$(mktemp /tmp/eloop_comment_translation_agent_XXXXXXXX 2>/dev/null || true)
					translation_text=$(_comment_generate_translation "$translation_prompt_file" "$translation_agents" \
						"${COMMENT_TRANSLATION_TIMEOUT:-20}" "$translation_last_agent_file" || true)
					translation_model=$(cat "$translation_last_agent_file" 2>/dev/null || true)
					rm -f "$translation_last_agent_file"
					if [ -n "$translation_text" ]; then
						local classification_file="" japanese_file="" translation_file=""
						classification_file=$(mktemp /tmp/eloop_comment_classification_XXXXXXXX 2>/dev/null || true)
						japanese_file=$(mktemp /tmp/eloop_comment_japanese_XXXXXXXX 2>/dev/null || true)
						translation_file=$(mktemp /tmp/eloop_comment_translation_XXXXXXXX 2>/dev/null || true)
						comment_speech_meta_file=$(mktemp /tmp/eloop_comment_speech_XXXXXXXX.json 2>/dev/null || true)
						if [ -n "$classification_file" ] && [ -n "$japanese_file" ] && [ -n "$translation_file" ] && [ -n "$comment_speech_meta_file" ]; then
							printf '%s' "$classification_json" >"$classification_file"
							printf '%s' "$attempt_talk" >"$japanese_file"
							printf '%s' "$translation_text" >"$translation_file"
							if python3 "$ELOOP_LIB_DIR/lib/comment_bilingual.py" build-segments \
								--classification "$classification_file" \
								--japanese "$japanese_file" \
								--translation "$translation_file" \
								--metadata "$comment_speech_meta_file" >/dev/null 2>&1; then
								log "[COMMENT] 英語対象${english_comment_count}件を翻訳して日本語返信と順序付きマージ (model=${translation_model:-unknown})"
							else
								rm -f "$comment_speech_meta_file"
								comment_speech_meta_file=""
								log "[COMMENT] 英語翻訳の段落検証失敗 → 日本語返信のみ継続"
							fi
						fi
						rm -f "$classification_file" "$japanese_file" "$translation_file"
					else
						log "[COMMENT] 英語翻訳呼出し失敗 → 日本語返信のみ継続"
					fi
				else
					log "[COMMENT] 日本語返信の段落境界を確定できない → 日本語返信のみ継続"
				fi
				rm -f "$translation_prompt_file"
			fi

			# 歌声合成はコメントキュー投入後に実行（順序保証のため移動）

			local _comment_mode_now=""
			_comment_mode_now=$(_broadcast_host_mode 2>/dev/null || printf '%s' "main")
			if [ "$_comment_mode_now" != "$_comment_mode_generated" ]; then
				log "[COMMENT] mode changed during generation (${_comment_mode_generated} -> ${_comment_mode_now}) -> discard without ack"
				rm -f "$comment_prompt_file"
				exit 0
			fi
			if _has_processed_comment_line "$twitch_comments"; then
				log "[COMMENT] target comment was already processed during generation -> discard stale reply without ack (batch=${comment_batch_hash:-none})"
				rm -f "$comment_prompt_file"
				exit 0
			fi

			local queue_file="$COMMENT_QUEUE_DIR/comment_$(date +%s)_${RANDOM}.txt"
			echo "$attempt_talk" >"$queue_file"
			_remember_comment_reply_text "$attempt_talk"
			_broadcast_mark_expected_mode "$queue_file" "$_comment_mode_generated" 2>/dev/null || true
			if ! _comment_store_generation_meta \
				"$queue_file" \
				"$_comment_mode_generated" \
				"${attempt_model:-unknown}" \
				"${comment_batch_hash:-}" \
				"$attempt" \
				"${#attempt_talk}" \
				"$comment_agent_list" \
				"" \
				"" \
				"$comment_speech_meta_file"; then
				log "[COMMENT] 生成メタデータ保存失敗（通常コメント再生は継続）"
			fi
			[ -n "$comment_speech_meta_file" ] && rm -f "$comment_speech_meta_file"
			comment_speech_meta_file=""
			local new_hash
			new_hash=$(_comment_hash_file "$queue_file" 2>/dev/null || true)
			if [ -n "$new_hash" ] && grep -qF "$new_hash" "$COMMENT_QUEUE_DIR/played_hashes.txt" 2>/dev/null; then
				log "[COMMENT] 重複コメント返し検出 → 再生成 (hash=$new_hash, attempt ${attempt}/${comment_retry_max})"
				_broadcast_clear_expected_mode "$queue_file" 2>/dev/null || true
				_comment_clear_generation_meta "$queue_file"
				rm -f "$queue_file"
				attempt=$((attempt + 1))
				continue
			fi

			# 生成経路は enqueue_audio_text を通らず直接ファイルを作るため、
			# ここで本文単位の原子的な投入権を取得する。別の生成プロセスが
			# 同じ返信を先にキューへ積んだ場合は、現在のバッチだけ消化して
			# 2本目の音声を作らない。
			if declare -F _comment_audio_claim_enqueue_key >/dev/null 2>&1 &&
				! _comment_audio_claim_enqueue_key "$attempt_talk"; then
				log "[COMMENT] 同一本文が音声キューへ投入済みのため重複返信を破棄"
				_broadcast_clear_expected_mode "$queue_file" 2>/dev/null || true
				_comment_clear_generation_meta "$queue_file"
				rm -f "$queue_file"
				if "$viewer_chat_script" ack-batch "$comment_batch_file"; then
					_mark_comment_batch_processed "$comment_batch_hash"
					_record_processed_comment_lines "$twitch_comments"
				else
					log "[COMMENT] 重複返信破棄後の ack-batch 失敗 → 個別行ハッシュ記録"
					_record_processed_comment_lines "$twitch_comments"
					_mark_comment_batch_processed "$comment_batch_hash"
				fi
				generation_ok=true
				break
			fi

			# kill耐性: キュー追加直後にack→処理済みマークし、再生成を防ぐ
			if "$viewer_chat_script" ack-batch "$comment_batch_file"; then
				_mark_comment_batch_processed "$comment_batch_hash"
				_record_processed_comment_lines "$twitch_comments"
			else
				log "[COMMENT] ack-batch 失敗 → 個別行ハッシュ記録で次回重複除外"
				_record_processed_comment_lines "$twitch_comments"
				_mark_comment_batch_processed "$comment_batch_hash"
			fi

			# 歌声合成: 楽譜JSONが有効なら非同期で合成→キューに投入（コメント発話の後に再生されるよう順序保証）
			# コメントキュー投入後に実行し、コメントの再生順序を保証するため少し待機してから合成を開始する
			if [ -n "$sing_score" ]; then
				if echo "$sing_score" | python3 -c "import json,sys; d=json.load(sys.stdin); assert 'notes' in d" 2>/dev/null; then
					local score_file="/tmp/sing_score_$(date +%s)_$$.json"
					echo "$sing_score" >"$score_file"
					# queue_file はこの時点で既に作成済み。コメントの音声が先に再生されるよう、キュー消費を少し待つ
					local _sing_queue_file="$queue_file"
					(
						# コメントのキューが消化されるまで待機（最大60秒）。これにより「歌わせていただきます」発話の後に歌が流れる
						local _wait=0
						while [ -f "$_sing_queue_file" ] && [ "$_wait" -lt 60 ]; do
							sleep 1
							_wait=$((_wait+1))
						done
						# さらにコメントのTTS合成がlockを握っている可能性を考慮して少し待機
						sleep 2
						local sing_wav="/tmp/sing_wav_$(date +%s)_$$.wav"
						local _sing_lock="tmp/.say_queue/.voicevox_synth_lock"
						local _sing_lock_held=0 _sing_lock_wait=0
						while ! mkdir "$_sing_lock" 2>/dev/null; do
							sleep 0.5
							_sing_lock_wait=$((_sing_lock_wait + 1))
							if [ "$_sing_lock_wait" -ge 120 ]; then break; fi # 60s timeout
						done
						[ "$_sing_lock_wait" -lt 120 ] && _sing_lock_held=1
						if [ "$_sing_lock_held" -eq 1 ]; then
							if VOICEVOX_SING_HOST_MODE="$_comment_mode_generated" "$ELOOP_LIB_DIR/voicevox_sing.sh" -o "$sing_wav" "$score_file" 2>/dev/null; then
								rmdir "$_sing_lock" 2>/dev/null
								_sing_lock_held=0
								SAY_CONTEXT_LABEL="comment:sing" "$ELOOP_LIB_DIR/say_enqueue.sh" --no-preempt --wav "$sing_wav" 150 0
								rm -f "$sing_wav"
							else
								rmdir "$_sing_lock" 2>/dev/null
								_sing_lock_held=0
								log "[COMMENT] 歌声合成失敗: $score_file"
							fi
						else
							log "[COMMENT] VOICEVOX合成ロック取得タイムアウト → 歌声合成スキップ: $score_file"
						fi
						[ "$_sing_lock_held" -eq 1 ] && rmdir "$_sing_lock" 2>/dev/null
						rm -f "$score_file"
					) &
					disown $!
					log "[COMMENT] 歌声合成開始 (score=$score_file)"
				else
					log "[COMMENT] 楽譜JSONが不正のため歌声合成スキップ"
				fi
			fi

			# 本文が有効で、元コメントが助言カテゴリのときだけアドバイスを追記する。
			local _allow_advice_append=1
			if [ -n "$dominant_category" ] && ! _comment_category_allows_advice_append "$dominant_category"; then
				_allow_advice_append=0
			fi
			if [ "$_allow_advice_append" = "1" ]; then
				if [ -n "$advice_item" ] && [ "$advice_item" != "（アドバイスなし）" ] && [ "$advice_item" != "なし" ] && [[ "$advice_item" != なし* ]] && [[ "$advice_item" != （アドバイスなし）* ]]; then
					_append_strategy_advice_item "$advice_item" "$_comment_mode_generated"
				fi
				if [ -n "$comment_advice_item" ] && [ "$comment_advice_item" != "（アドバイスなし）" ] && [ "$comment_advice_item" != "なし" ] && [[ "$comment_advice_item" != なし* ]] && [[ "$comment_advice_item" != （アドバイスなし）* ]]; then
					_append_comment_advice_item "$comment_advice_item"
				fi
				if [ -n "$codex_advice_item" ] && [ "$codex_advice_item" != "（アドバイスなし）" ] && [ "$codex_advice_item" != "なし" ] && [[ "$codex_advice_item" != なし* ]] && [[ "$codex_advice_item" != （アドバイスなし）* ]]; then
					_append_codex_advice_item "$codex_advice_item"
				fi
				if [ -n "$strategy_advice_candidates_main" ]; then
					while IFS= read -r advice_line; do
						[ -n "$advice_line" ] || continue
						_append_strategy_advice_item "$advice_line" "main"
					done <<<"$strategy_advice_candidates_main"
				fi
				if [ -n "$strategy_advice_candidates_soren91" ]; then
					while IFS= read -r advice_line; do
						[ -n "$advice_line" ] || continue
						_append_strategy_advice_item "$advice_line" "soren91"
					done <<<"$strategy_advice_candidates_soren91"
				fi
				if [ -n "$comment_advice_candidates" ]; then
					while IFS= read -r advice_line; do
						[ -n "$advice_line" ] || continue
						_append_comment_advice_item "$advice_line"
					done <<<"$comment_advice_candidates"
				fi
				if [ -n "$codex_advice_candidates" ]; then
					while IFS= read -r advice_line; do
						[ -n "$advice_line" ] || continue
						_append_codex_advice_item "$advice_line"
					done <<<"$codex_advice_candidates"
				fi
			elif [ -n "$advice_item$comment_advice_item$codex_advice_item" ]; then
				log "[COMMENT] 非助言カテゴリの生成アドバイスを破棄: ${dominant_category:-unknown}"
			fi

			# ソ連テーマを追記
			if [ -n "$soviet_theme_part" ]; then
				local soviet_theme_line
				soviet_theme_line=$(printf '%s' "$soviet_theme_part" | tr '\n' ' ' | sed -E 's/[[:space:]]+/ /g; s/^ //; s/ $//')
				if [ -n "$soviet_theme_line" ]; then
					_append_soviet_theme_item "$soviet_theme_line"
				fi
			fi

			comments_talk="$attempt_talk"
			comment_model_used="$attempt_model"
			log "[COMMENT] コメント返し ${#comments_talk}字 → キュー追加: $queue_file (model=${comment_model_used:-unknown}, batch=${comment_batch_hash:-none}, attempt=${attempt}/${comment_retry_max})"
			if [ -x ./overlay_notify.sh ]; then
				local _ov_reply
				_ov_reply=$(printf '%s' "$comments_talk" | tr '\n' ' ' | sed -E 's/[[:space:]]+/ /g; s/^ //')
				[ "${#_ov_reply}" -gt 90 ] && _ov_reply="${_ov_reply:0:90}…"
				timeout "${COMMENT_OVERLAY_NOTIFY_TIMEOUT_SEC:-3}" ./overlay_notify.sh chat "コメント返信 queued" "model=${comment_model_used:-unknown} chars=${#comments_talk} attempt=${attempt}/${comment_retry_max} batch=${comment_batch_hash:-none}${_ov_reply:+ | 返信:${_ov_reply}}" "info" >/dev/null 2>&1 || true
			fi
			_comment_failure_backoff_clear
			generation_ok=true
			break
		done

		rm -f "$comment_prompt_file"

		if [ "$generation_ok" != "true" ]; then
			_comment_handle_generation_failure "$generation_rate_limited"
		fi
	) &
	local comment_pid=$!
	_mark_comment_batch_inflight "$comment_batch_hash" "$comment_pid"
	echo "${comment_pid}|${comment_parent_pid}|${comment_started_at}" >tmp/.twitch_chat/comment_gen.pid
	disown "$comment_pid"
}

# soren91 ゲーム感想は soren91/comment.mjs (generateRankingComment) で生成するため、
# 親プロジェクト側での重複生成は廃止。
