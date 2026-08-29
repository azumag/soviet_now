# broadcast/radio_state.sh - ラジオ状態管理, 音声割り込み, キュー

_radio_gc_stale_state() {
	local current mode corner ts owner_pid now age
	current=$(cat "$RADIO_STATE_FILE" 2>/dev/null) || return 0
	IFS=':' read -r mode corner ts owner_pid _ <<<"$current"
	case "$ts" in
	'' | *[!0-9]*) return 0 ;;
	esac
	now=$(date +%s)
	age=$((now - ts))
	[ "$age" -le "$RADIO_STATE_STALE_SEC" ] && return 0
	case "$owner_pid" in
	'' | *[!0-9]*) owner_pid="" ;;
	esac
	if [ -n "$owner_pid" ] && kill -0 "$owner_pid" 2>/dev/null; then
		return 0
	fi
	rm -f "$RADIO_STATE_FILE"
	log "[RADIO:${corner:-unknown}] stale state clear: mode=${mode:-unknown} age=${age}s"
}

_radio_set_state() {
	local mode="$1" corner="$2" detail="${3:-}"
	[ -n "$mode" ] || return 1
	[ -n "$corner" ] || return 1
	_radio_gc_stale_state
	printf '%s:%s:%s:%s\n' "$mode" "$corner" "$(date +%s)" "$$" >"$RADIO_STATE_FILE"
	case "$mode" in
	generating|verifying|queued|playing)
		if [ -x ./overlay_notify.sh ]; then
			local _ov_body="corner=${corner}"
			[ -n "$detail" ] && _ov_body="${_ov_body} | ${detail}"
			./overlay_notify.sh radio "ラジオ ${corner} ${mode}" "$_ov_body" "info" >/dev/null 2>&1 || true
		fi
		;;
	esac
}

# 自分のコーナーの状態ファイルだけ安全に削除 (並列実行の競合防止)
_radio_clear_state() {
	local my_corner="$1" reason="${2:-}"
	local current
	_radio_gc_stale_state
	current=$(cat "$RADIO_STATE_FILE" 2>/dev/null) || return 0
	case "$current" in
	*":${my_corner}:"*)
		rm -f "$RADIO_STATE_FILE"
		[ -n "$reason" ] && log "[RADIO:${my_corner}] state clear: ${reason}"
		;;
	esac
}

_interrupt_current_audio_playback() {
	local reason="${1:-priority_audio}"
	local cs_line owner owner_pid say_pid
	cs_line=$(cat "tmp/.say_queue/current_source" 2>/dev/null || true)
	owner=$(printf '%s' "$cs_line" | awk -F'|' 'NR==1{print $1}')
	owner_pid="${owner%%:*}"
	say_pid=$(cat "tmp/.say_queue/pid" 2>/dev/null || true)

	case "$say_pid" in
	'' | *[!0-9]*) say_pid="" ;;
	esac
	case "$owner_pid" in
	'' | *[!0-9]*) owner_pid="" ;;
	esac

	if [ -n "$say_pid" ] && kill -0 "$say_pid" 2>/dev/null; then
		log "[AUDIO] child停止: pid=${say_pid} reason=${reason}"
		kill -9 "$say_pid" 2>/dev/null || true
	fi
	if [ -n "$owner_pid" ] && kill -0 "$owner_pid" 2>/dev/null; then
		log "[AUDIO] enqueue停止: pid=${owner_pid} reason=${reason}"
		kill "$owner_pid" 2>/dev/null || true
		sleep 1
		kill -9 "$owner_pid" 2>/dev/null || true
	fi

	local waited=0
	while [ -d "tmp/.say_queue/.lock" ] && [ "$waited" -lt 30 ]; do
		sleep 0.2
		waited=$((waited + 1))
	done
	rm -f "tmp/.say_queue/pid" 2>/dev/null || true
}

_play_priority_audio_file() {
	local audio_file="$1" corner_name="$2"
	[ -s "$audio_file" ] || return 1
	local radio_vo_speaker=""
	radio_vo_speaker=$(_radio_voicevox_speaker_override "$corner_name" 2>/dev/null || true)
	_interrupt_current_audio_playback "priority:${corner_name}"
	local _play_detail=""
	_play_detail=$(_radio_generation_debug_summary "$audio_file" 2>/dev/null || true)
	_radio_set_state "playing" "$corner_name" "$_play_detail"
	_refresh_radio_intro_for_playback_file "$audio_file" "$corner_name"
	# ラジオ読み上げ中はコメントが割り込まない（読み上げ終了後にコメント再生へ）。
	# コメント返信の生成（chat_worker）は別プロセスで並行進行するため、再生順序だけ
	# ラジオ優先にしてもコメントの作成は遅れない。
	SAY_VOICEVOX_SPEAKER_OVERRIDE="$radio_vo_speaker" \
		SAY_DISABLE_COMMENT_YIELD=1 \
		SAY_CONTEXT_LABEL="radio:${corner_name}" ./say_enqueue.sh "$audio_file" "$RADIO_SAY_RATE" 0
}

_cancel_russia_celebration_worker() {
	local worker_pid=""
	worker_pid=$(cat "$RUSSIA_CELEBRATION_WORKER_PID_FILE" 2>/dev/null || true)
	case "$worker_pid" in
	'' | *[!0-9]*) worker_pid="" ;;
	esac
	if [ -n "$worker_pid" ] && kill -0 "$worker_pid" 2>/dev/null; then
		log "[RUSSIA] worker停止: pid=${worker_pid}"
		kill "$worker_pid" 2>/dev/null || true
		sleep 1
		kill -9 "$worker_pid" 2>/dev/null || true
	fi
	rm -f "$RUSSIA_CELEBRATION_WORKER_PID_FILE" "$TMP_DEBUG_DIR/radio_russia_celebration.txt" 2>/dev/null || true
}

_radio_mark_done() {
	local done_marker="$1"
	[ -n "$done_marker" ] || return 0
	touch "$done_marker"
	# 古い重複防止マーカーを掃除（最新200件だけ保持）
	local old_markers
	old_markers=$(ls -1t $TMP_MARKERS_DIR/.radio_done_* 2>/dev/null | tail -n +201 || true)
	if [ -n "$old_markers" ]; then
		echo "$old_markers" | xargs rm -f 2>/dev/null || true
	fi
}

_radio_history_sidecar_path() {
	local target="$1"
	case "$target" in
	*.playing) printf '%s.history' "${target%.playing}" ;;
	*.txt) printf '%s.history' "${target%.txt}" ;;
	*) printf '%s.history' "$target" ;;
	esac
}

_radio_meta_sidecar_path() {
	local target="$1"
	case "$target" in
	*.playing) printf '%s.meta.json' "${target%.playing}" ;;
	*.txt) printf '%s.meta.json' "${target%.txt}" ;;
	*) printf '%s.meta.json' "$target" ;;
	esac
}

# 再生完了したラジオ原稿を backups/radio_scripts/<date>/ へ退避する。
# 再生後は .playing と .ready.wav が削除されるため、原稿 .txt はこの時点で
# コピーしておかないと失われる。.history (要約) と .meta.json も一緒に保存する。
_radio_backup_script() {
	local target="$1" backup_dir date_dir base played_txt orig_name
	[ -n "$target" ] && [ -f "$target" ] || return 0
	base=$(basename "$target")
	date_dir="backups/radio_scripts/$(date +%Y%m%d)"
	mkdir -p "$date_dir" 2>/dev/null || return 0
	played_txt="${target%.playing}.txt"
	# 元の .txt 名 (radio_*.txt) で保存する
	orig_name="${base%.playing}.txt"
	# .playing は元の .txt が rename されたものなので、中身を .txt として保存する
	if [ -f "$played_txt" ]; then
		cp -p "$played_txt" "$date_dir/$orig_name" 2>/dev/null || true
	elif [ -f "$target" ]; then
		cp -p "$target" "$date_dir/$orig_name" 2>/dev/null || true
	fi
	[ -f "${target%.playing}.history" ] && cp -p "${target%.playing}.history" "$date_dir/${orig_name%.txt}.history" 2>/dev/null || true
	[ -f "${target%.playing}.meta.json" ] && cp -p "${target%.playing}.meta.json" "$date_dir/${orig_name%.txt}.meta.json" 2>/dev/null || true
}

_radio_clear_generation_meta() {
	local target="$1"
	[ -n "$target" ] || return 0
	rm -f "$(_radio_meta_sidecar_path "$target")" 2>/dev/null || true
}

_radio_generation_debug_summary() {
	local target="$1"
	local sidecar
	sidecar=$(_radio_meta_sidecar_path "$target")
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
]
if data.get("corner"):
    parts.append(f"corner={data['corner']}")
if chain.get("primary"):
    parts.append(f"primary={chain['primary']}")
if chain.get("secondary"):
    parts.append(f"secondary={chain['secondary']}")
if chain.get("tertiary"):
    parts.append(f"tertiary={chain['tertiary']}")
print(" ".join(parts))
PY
}

_radio_copy_generation_meta() {
	local src="$1" dst="$2"
	[ -n "$src" ] || return 0
	[ -n "$dst" ] || return 0
	local src_meta dst_meta
	src_meta=$(_radio_meta_sidecar_path "$src")
	dst_meta=$(_radio_meta_sidecar_path "$dst")
	[ -f "$src_meta" ] || return 0
	cp "$src_meta" "$dst_meta" 2>/dev/null || true
}

_radio_store_generation_meta() {
	local target="$1" corner="$2" mode="$3" model="$4" game_num="$5" score="$6" attempt="$7" topic="$8" selected_news="$9" primary="${10}" secondary="${11}" tertiary="${12}"
	[ -n "$target" ] || return 0
	local sidecar
	sidecar=$(_radio_meta_sidecar_path "$target")
	mkdir -p "$(dirname "$RADIO_GENERATION_HISTORY_FILE")" 2>/dev/null || true
	python3 - "$sidecar" "$RADIO_GENERATION_HISTORY_FILE" "$RADIO_GENERATION_HISTORY_KEEP" "$target" "$corner" "$mode" "$model" "$game_num" "$score" "$attempt" "$topic" "$selected_news" "$primary" "$secondary" "$tertiary" <<'PY'
import json
import os
import sys
from datetime import datetime, timezone
from collections import deque

sidecar, history_file, keep_raw, target, corner, mode, model, game_num_raw, score_raw, attempt_raw, topic, selected_news, primary, secondary, tertiary = sys.argv[1:16]

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
    "corner": corner or "",
    "mode": mode or "",
    "model": model or "unknown",
    "game_num": to_int(game_num_raw),
    "score": to_int(score_raw),
    "attempt": to_int(attempt_raw),
    "topic": topic or "",
    "selected_news": selected_news or "",
    "chain": {
        "primary": primary or "",
        "secondary": secondary or "",
        "tertiary": tertiary or "",
        "final": model or "unknown",
    },
}

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

_radio_store_spoken_history_line() {
	local target="$1" history_line="$2"
	[ -n "$target" ] || return 1
	[ -n "$history_line" ] || return 1
	local sidecar
	sidecar=$(_radio_history_sidecar_path "$target")
	printf '%s\n' "$history_line" >"$sidecar"
}

_radio_clear_spoken_history_line() {
	local target="$1"
	[ -n "$target" ] || return 0
	local sidecar
	sidecar=$(_radio_history_sidecar_path "$target")
	rm -f "$sidecar"
}

_radio_append_spoken_history_line() {
	local history_line="$1"
	[ -n "$history_line" ] || return 0
	{
		[ -f "$PAST_RADIO_TOPICS" ] && grep -E '^\[[0-9]{2}:[0-9]{2}\] Game#[0-9]+ ' "$PAST_RADIO_TOPICS" 2>/dev/null || true
		printf '%s\n' "$history_line"
	} | tail -100 >"${PAST_RADIO_TOPICS}.tmp" && mv "${PAST_RADIO_TOPICS}.tmp" "$PAST_RADIO_TOPICS"
}

_radio_commit_spoken_history_for_file() {
	local target="$1"
	[ -n "$target" ] || return 0
	local sidecar history_line
	sidecar=$(_radio_history_sidecar_path "$target")
	[ -f "$sidecar" ] || return 0
	history_line=$(cat "$sidecar" 2>/dev/null)
	_radio_clear_spoken_history_line "$target"
	[ -n "$history_line" ] && _radio_append_spoken_history_line "$history_line"
}

# deferred radio queue に滞留している未再生の radio_*.txt 本文数を返す。
# サイドカー (.mode / .ready.wav / .history など) は本文ではないため数えない。
_radio_deferred_queue_count() {
	local queue_dir="${RADIO_DEFERRED_QUEUE_DIR:-tmp/.radio_deferred_queue}"
	[ -d "$queue_dir" ] || { printf '0'; return 0; }
	find "$queue_dir" -maxdepth 1 -name 'radio_*.txt' -type f 2>/dev/null | wc -l | tr -d ' '
}

_enqueue_deferred_radio_talk() {
	local talk_file="$1" game_num="$2" corner_name="$3" expected_mode="${4:-}" history_line="${5:-}"
	[ -s "$talk_file" ] || return 1
	mkdir -p "$RADIO_DEFERRED_QUEUE_DIR" 2>/dev/null || true
	local deferred_file
	deferred_file="$RADIO_DEFERRED_QUEUE_DIR/radio_$(date +%s)_${game_num}_${corner_name}_${RANDOM}.txt"
	cp "$talk_file" "$deferred_file" 2>/dev/null || return 1
	_radio_copy_generation_meta "$talk_file" "$deferred_file" 2>/dev/null || true
	_broadcast_mark_expected_mode "$deferred_file" "$expected_mode" 2>/dev/null || true
	[ -n "$history_line" ] && _radio_store_spoken_history_line "$deferred_file" "$history_line" 2>/dev/null || true
	echo "$deferred_file"
}

_radio_audio_base_path() {
	local target="$1"
	case "$target" in
	*.playing) printf '%s' "${target%.playing}" ;;
	*.txt) printf '%s' "${target%.txt}" ;;
	*) printf '%s' "$target" ;;
	esac
}

_radio_ready_wav_path() {
	printf '%s.ready.wav' "$(_radio_audio_base_path "$1")"
}

_radio_ready_bundle_path() {
	printf '%s.bundle' "$(_radio_ready_wav_path "$1")"
}

_radio_render_marker_path() {
	printf '%s.rendering' "$(_radio_audio_base_path "$1")"
}

_radio_render_retry_path() {
	printf '%s.render_retry' "$(_radio_audio_base_path "$1")"
}

# 事前生成WAVと、その元になったキュー本文のハッシュを同じ世代として
# 扱う。本文だけが再生直前に更新されても、対応するWAVを再利用しない。
_radio_render_meta_path() {
	printf '%s.render_meta' "$(_radio_audio_base_path "$1")"
}

_radio_text_hash() {
	local target="$1"
	[ -f "$target" ] || return 1
	if command -v sha256sum >/dev/null 2>&1; then
		sha256sum "$target" | awk '{print $1}'
	elif command -v shasum >/dev/null 2>&1; then
		shasum -a 256 "$target" | awk '{print $1}'
	else
		cksum "$target" | awk '{print $1 ":" $2}'
	fi
}

_radio_render_meta_hash() {
	local target="$1" meta hash _stamp
	meta=$(_radio_render_meta_path "$target")
	[ -f "$meta" ] || return 1
	read -r hash _stamp <"$meta" || true
	[ -n "$hash" ] || return 1
	printf '%s\n' "$hash"
}

_radio_write_render_meta() {
	local target="$1" source_hash="$2" meta tmp
	[ -n "$target" ] && [ -n "$source_hash" ] || return 1
	meta=$(_radio_render_meta_path "$target")
	tmp="${meta}.tmp.${BASHPID:-$$}"
	if printf '%s %s\n' "$source_hash" "$(date +%s)" >"$tmp" 2>/dev/null &&
		mv "$tmp" "$meta" 2>/dev/null; then
		return 0
	fi
	rm -f "$tmp" 2>/dev/null || true
	return 1
}

_radio_clear_render_meta() {
	rm -f "$(_radio_render_meta_path "$1")" 2>/dev/null || true
}

_radio_deferred_render_in_progress() {
	local target="$1" marker marker_pid marker_ts marker_age
	marker=$(_radio_render_marker_path "$target")
	[ -f "$marker" ] || return 1
	read -r marker_pid marker_ts <"$marker" || true
	case "$marker_pid" in '' | *[!0-9]*) marker_pid="" ;; esac
	case "$marker_ts" in '' | *[!0-9]*) marker_ts=0 ;; esac
	marker_age=$(($(date +%s) - marker_ts))
	[ "$marker_age" -lt 0 ] && marker_age=0
	if [ -n "$marker_pid" ] && kill -0 "$marker_pid" 2>/dev/null && [ "$marker_age" -le "$RADIO_STATE_STALE_SEC" ]; then
		return 0
	fi
	rm -f "$marker" 2>/dev/null || true
	return 1
}

_radio_sync_deferred_time_before_render() {
	local qf="$1" deferred_corner="$2"
	[ -f "$qf" ] || return 0

	# say_enqueue --render-only が本文を読んでいる間は本文を更新しない。
	# 次の audio_worker 周期で、render_meta と本文を再照合する。
	_radio_deferred_render_in_progress "$qf" && return 2

	local ready_wav ready_bundle before_hash after_hash render_hash time_precision
	ready_wav=$(_radio_ready_wav_path "$qf")
	ready_bundle=$(_radio_ready_bundle_path "$qf")
	render_hash=$(_radio_render_meta_hash "$qf" 2>/dev/null || true)
	before_hash=$(_radio_text_hash "$qf" 2>/dev/null || true)
	time_precision=hour
	[ "${RADIO_TIME_ANNOUNCE_MINUTES:-0}" = "1" ] && time_precision=minute
	_refresh_radio_intro_for_playback_file "$qf" "$deferred_corner" "$time_precision"
	after_hash=$(_radio_text_hash "$qf" 2>/dev/null || true)

	if [ -s "$ready_wav" ]; then
		if [ -z "$after_hash" ] || [ -z "$render_hash" ] || [ "$render_hash" != "$after_hash" ]; then
			rm -f "$ready_wav" "${ready_wav}.tmp" "$(_radio_render_marker_path "$qf")"
			rm -rf "$ready_bundle" 2>/dev/null || true
			_radio_clear_render_meta "$qf"
			log "[RADIO:deferred] 時報本文と事前音声の世代不一致 → 再レンダリング: $(basename "$qf")"
		fi
	else
		# 失敗した旧レンダーのbundle/metaだけが残る場合は、次のrenderを
		# 旧世代と誤認しないように掃除する。
		rm -rf "$ready_bundle" 2>/dev/null || true
		_radio_clear_render_meta "$qf"
	fi

	# ハッシュ取得ができない環境でも本文更新自体は継続する。変化の有無は
	# render_meta が存在する通常経路で判定し、未導入WAVは安全側で再生成する。
	[ -n "$before_hash" ] && [ -n "$after_hash" ] || return 0
	return 0
}

_radio_render_retry_waiting() {
	local target="$1" retry_file retry_count="" retry_at="" now
	retry_file=$(_radio_render_retry_path "$target")
	[ -f "$retry_file" ] || return 1
	read -r retry_count retry_at <"$retry_file" || true
	case "$retry_count" in '' | *[!0-9]*) retry_count="" ;; esac
	case "$retry_at" in '' | *[!0-9]*) retry_at="" ;; esac
	if [ -z "$retry_count" ] || [ -z "$retry_at" ]; then
		rm -f "$retry_file" 2>/dev/null || true
		return 1
	fi
	now=$(date +%s)
	[ "$retry_at" -gt "$now" ]
}

_radio_schedule_deferred_render_retry() {
	local target="$1" retry_file retry_count="" _old_retry_at=""
	local retry_base="${RADIO_RENDER_RETRY_BASE_SEC:-30}"
	local retry_max="${RADIO_RENDER_RETRY_MAX_SEC:-300}"
	local retry_delay retry_at retry_step retry_tmp
	retry_file=$(_radio_render_retry_path "$target")
	if [ -f "$retry_file" ]; then
		read -r retry_count _old_retry_at <"$retry_file" || true
	fi
	case "$retry_count" in '' | *[!0-9]*) retry_count=0 ;; esac
	case "$retry_base" in '' | *[!0-9]*) retry_base=30 ;; esac
	case "$retry_max" in '' | *[!0-9]*) retry_max=300 ;; esac
	[ "$retry_base" -gt 0 ] || retry_base=1
	[ "$retry_max" -ge "$retry_base" ] || retry_max="$retry_base"
	retry_count=$((retry_count + 1))
	retry_delay="$retry_base"
	retry_step=1
	while [ "$retry_step" -lt "$retry_count" ] && [ "$retry_delay" -lt "$retry_max" ]; do
		retry_delay=$((retry_delay * 2))
		[ "$retry_delay" -le "$retry_max" ] || retry_delay="$retry_max"
		retry_step=$((retry_step + 1))
	done
	retry_at=$(($(date +%s) + retry_delay))
	retry_tmp="${retry_file}.tmp.${BASHPID:-$$}"
	printf '%s %s\n' "$retry_count" "$retry_at" >"$retry_tmp" 2>/dev/null \
		&& mv "$retry_tmp" "$retry_file" 2>/dev/null \
		|| rm -f "$retry_tmp" 2>/dev/null
	printf '%s %s %s\n' "$retry_count" "$retry_delay" "$retry_at"
}

# コメント優先で合成を中断した場合の再試行予約。
# これは「失敗」ではなく意図的な譲りなので、指数バックオフを進めずに
# 短い固定間隔で再開する。実際の再開は _play_deferred_radio_queue_once の
# コメント残数ゲートでさらに抑えられるため、短くても暴走しない。
_radio_schedule_deferred_render_yield_retry() {
	local target="$1" retry_file retry_count="" _old_retry_at="" retry_at retry_tmp
	local retry_delay="${RADIO_RENDER_COMMENT_YIELD_RETRY_SEC:-20}"
	case "$retry_delay" in '' | *[!0-9]*) retry_delay=20 ;; esac
	[ "$retry_delay" -gt 0 ] || retry_delay=1
	retry_file=$(_radio_render_retry_path "$target")
	if [ -f "$retry_file" ]; then
		read -r retry_count _old_retry_at <"$retry_file" || true
	fi
	case "$retry_count" in '' | *[!0-9]*) retry_count=0 ;; esac
	retry_at=$(($(date +%s) + retry_delay))
	retry_tmp="${retry_file}.tmp.${BASHPID:-$$}"
	printf '%s %s\n' "$retry_count" "$retry_at" >"$retry_tmp" 2>/dev/null \
		&& mv "$retry_tmp" "$retry_file" 2>/dev/null \
		|| rm -f "$retry_tmp" 2>/dev/null
	printf '%s %s %s\n' "$retry_count" "$retry_delay" "$retry_at"
}

_radio_clear_deferred_render_retry() {
	rm -f "$(_radio_render_retry_path "$1")" 2>/dev/null || true
}

_radio_start_deferred_render_if_needed() {
	local qf="$1" deferred_corner="$2" deferred_cc_text="$3" radio_vo_speaker="$4"
	local ready_wav ready_bundle marker tmp_wav tmp_bundle marker_pid marker_ts marker_age render_source_hash
	ready_wav=$(_radio_ready_wav_path "$qf")
	ready_bundle="${ready_wav}.bundle"
	if [ -s "$ready_wav" ]; then
		_radio_clear_deferred_render_retry "$qf"
		return 0
	fi
	_radio_render_retry_waiting "$qf" && return 0
	# ファイル単位のレンダーロック（重複起動抑止）。marker 作成前のレースで
	# 同一 qf に複数レンダーが並走するのを防ぐ（2026-08-24 デッドロックで4並列発生）。
	local _render_file_lock="${qf}.render_lock"
	if ! mkdir "$_render_file_lock" 2>/dev/null; then
		local _lock_age=0 _lock_mtime=0
		_lock_mtime=$(stat -f %m "$_render_file_lock" 2>/dev/null || stat -c %Y "$_render_file_lock" 2>/dev/null || echo 0)
		case "$_lock_mtime" in ''|*[!0-9]*) _lock_mtime=0;; esac
		_lock_age=$(($(date +%s) - _lock_mtime))
		if [ "$_lock_age" -gt "$RADIO_STATE_STALE_SEC" ]; then
			rmdir "$_render_file_lock" 2>/dev/null || true
			mkdir "$_render_file_lock" 2>/dev/null || return 0
		else
			return 0
		fi
	fi
	marker=$(_radio_render_marker_path "$qf")
	if [ -f "$marker" ]; then
		read -r marker_pid marker_ts <"$marker" || true
		case "$marker_pid" in ''|*[!0-9]*) marker_pid="" ;; esac
		case "$marker_ts" in ''|*[!0-9]*) marker_ts=0 ;; esac
		marker_age=$(($(date +%s) - marker_ts))
		if [ -n "$marker_pid" ] && kill -0 "$marker_pid" 2>/dev/null && [ "$marker_age" -le "$RADIO_STATE_STALE_SEC" ]; then
			rmdir "$_render_file_lock" 2>/dev/null || true
			return 0
		fi
		rm -f "$marker" 2>/dev/null || true
	fi

	tmp_wav="${ready_wav}.tmp"
	tmp_bundle="${tmp_wav}.bundle"
	render_source_hash=$(_radio_text_hash "$qf" 2>/dev/null || true)
	rm -rf "$tmp_bundle" 2>/dev/null || true
	(
		trap 'rm -f "$marker" 2>/dev/null || true; rmdir "$_render_file_lock" 2>/dev/null || true' EXIT TERM INT
		local render_rc=0 retry_count=0 retry_delay=0 retry_at=0
		if SAY_CC_TEXT="$deferred_cc_text" SAY_VOICEVOX_SPEAKER_OVERRIDE="$radio_vo_speaker" SAY_CONTEXT_LABEL="radio_render:${deferred_corner:-deferred}" \
			./say_enqueue.sh --render-only "$tmp_wav" "$qf" "$RADIO_SAY_RATE" 0; then
			if [ -s "$tmp_bundle/playlist.txt" ] && [ -s "$tmp_bundle/captions.txt" ]; then
				rm -rf "$ready_bundle" 2>/dev/null || true
			fi
			if [ -s "$tmp_bundle/playlist.txt" ] && [ -s "$tmp_bundle/captions.txt" ] \
				&& mv "$tmp_bundle" "$ready_bundle" 2>/dev/null \
				&& mv "$tmp_wav" "$ready_wav" 2>/dev/null && [ -s "$ready_wav" ]; then
				_radio_write_render_meta "$qf" "$render_source_hash" 2>/dev/null || true
				_radio_clear_deferred_render_retry "$qf"
				log "[RADIO:deferred] 事前音声生成完了: $(basename "$ready_wav") (字幕同期bundle付き)"
			else
				rm -f "$tmp_wav" "$ready_wav" 2>/dev/null || true
				rm -rf "$tmp_bundle" "$ready_bundle" 2>/dev/null || true
				read -r retry_count retry_delay retry_at <<<"$(_radio_schedule_deferred_render_retry "$qf")"
				log "[RADIO:deferred] 事前音声生成保存を再試行予約: $(basename "$qf") retry=${retry_count} in=${retry_delay}s"
			fi
		else
			render_rc=$?
			rm -f "$tmp_wav" 2>/dev/null || true
			rm -rf "$tmp_bundle" 2>/dev/null || true
			if [ "$render_rc" -eq 75 ]; then
				read -r retry_count retry_delay retry_at <<<"$(_radio_schedule_deferred_render_yield_retry "$qf")"
				log "[RADIO:deferred] コメント優先で合成を中断・保留（合成済みチャンクは保持）: $(basename "$qf") retry=${retry_count} in=${retry_delay}s"
			else
				read -r retry_count retry_delay retry_at <<<"$(_radio_schedule_deferred_render_retry "$qf")"
				log "[RADIO:deferred] 事前音声生成を再試行予約: $(basename "$qf") rc=${render_rc} retry=${retry_count} in=${retry_delay}s"
			fi
		fi
		rm -f "$marker" 2>/dev/null || true
		rmdir "$_render_file_lock" 2>/dev/null || true
	) >>logs/audio_worker.log 2>&1 &
	printf '%s %s\n' "$!" "$(date +%s)" >"$marker"
	log "[RADIO:deferred] 事前音声生成開始: $(basename "$qf")"
	return 0
}

_run_jiji_corner_guarded() {
	local game_num="$1" score="$2"

	if _try_game_corner "$game_num" "jiji"; then
		if start_radio_corner_jiji "$game_num" "$score"; then
			log "[JIJI] completed"
		else
			log "[JIJI] failed before playback/queue completion -> will retry next loop"
		fi
	else
		log "[JIJI] duplicate skip: already done/in-flight for game=${game_num}"
	fi
}

_play_deferred_radio_queue_once() {
	# コメント未消化がある間は deferred ラジオを再生しない
	local comment_queued=0 comment_playing=0 comment_total=0
	read -r comment_queued comment_playing <<<"$(get_comment_backlog_counts)"
	comment_queued=${comment_queued:-0}
	comment_playing=${comment_playing:-0}
	comment_total=$((comment_queued + comment_playing))
	[ "$comment_total" -gt 0 ] && return 0

	# say_enqueue プロセスが溜まりすぎている場合はスキップ（蓄積→重複再生を防止）
	local _say_proc_count
	_say_proc_count=$(pgrep -fc 'say_enqueue.sh' 2>/dev/null || echo 0)
	if [ "${_say_proc_count:-0}" -gt 3 ]; then
		log "[RADIO:deferred] say_enqueue プロセス過多 (${_say_proc_count}) → スキップ"
		return 0
	fi

	local stale_playing=""
	for stale_playing in "$RADIO_DEFERRED_QUEUE_DIR"/radio_*.playing; do
		[ -f "$stale_playing" ] || continue
		local stale_mtime="" stale_age=0 retry_file=""
		stale_mtime=$(stat -f %m "$stale_playing" 2>/dev/null) \
			|| stale_mtime=$(stat -c %Y "$stale_playing" 2>/dev/null) \
			|| stale_mtime=""
		case "$stale_mtime" in
		'' | *[!0-9]*) continue ;;
		esac
		stale_age=$(($(date +%s) - stale_mtime))
		[ "$stale_age" -le "$RADIO_STATE_STALE_SEC" ] && continue
		# say_enqueue がまだ動いている場合は stale 復帰しない（重複再生の原因になる）
		if pgrep -f "say_enqueue.sh.*$(basename "$stale_playing")" >/dev/null 2>&1; then
			log "[RADIO:deferred] stale だが say_enqueue 実行中 → スキップ: $(basename "$stale_playing") age=${stale_age}s"
			continue
		fi
		retry_file="${stale_playing%.playing}.txt"
		if [ -f "$retry_file" ]; then
			rm -f "$stale_playing"
			log "[RADIO:deferred] stale playing削除: $(basename "$stale_playing") age=${stale_age}s"
		else
			mv "$stale_playing" "$retry_file" 2>/dev/null || true
			log "[RADIO:deferred] stale playing復帰: $(basename "$retry_file") age=${stale_age}s"
		fi
	done

	local qf
	qf=$(ls -1 "$RADIO_DEFERRED_QUEUE_DIR"/radio_*.txt 2>/dev/null | sort | head -n 1)
	[ -n "$qf" ] || return 0
	[ -f "$qf" ] || return 0

	local deferred_corner=""
	deferred_corner=$(basename "$qf" | sed -E 's/^radio_[0-9]+_[0-9]+_([^_]+)_.*/\1/')
	local news_title_file="${qf%.txt}.news_title"
	local news_cc_file="${qf%.txt}.cc_text"
	local deferred_cc_text=""
	if [ "$deferred_corner" = "news" ] && [ -f "$news_cc_file" ]; then
		deferred_cc_text=$(cat "$news_cc_file" 2>/dev/null)
	elif [ "$deferred_corner" = "news" ] && [ -f "$news_title_file" ]; then
		local deferred_news_title
		deferred_news_title=$(cat "$news_title_file" 2>/dev/null)
		[ -n "$deferred_news_title" ] && deferred_cc_text=$(_build_cc_attribution_text "$deferred_news_title")
	fi
	# 反映前に生成を始めた子プロセスが旧ルールの出典行を付けた場合も、
	# 音声合成・字幕生成の直前で現行ポリシーを必ず適用する。
	if [ "$deferred_corner" = "news" ] && [ -f "$news_title_file" ]; then
		local deferred_news_title_for_policy
		deferred_news_title_for_policy=$(cat "$news_title_file" 2>/dev/null)
		if _strip_non_globalvoices_attribution_file "$qf" "$deferred_news_title_for_policy"; then
			local stale_ready_wav
			stale_ready_wav=$(_radio_ready_wav_path "$qf")
			rm -f "$stale_ready_wav" "$(_radio_render_meta_path "$qf")" 2>/dev/null || true
			rm -rf "${stale_ready_wav}.bundle" 2>/dev/null || true
			log "[RADIO:deferred] 非Global Voicesニュースの旧出典行を再生前に除去: $(basename "$qf")"
		fi
	fi
	local radio_vo_speaker=""
	radio_vo_speaker=$(_radio_voicevox_speaker_override "$deferred_corner" 2>/dev/null || true)
	if [ -z "$radio_vo_speaker" ]; then
		local _voice_sidecar="${qf%.txt}.voice"
		[ -f "$_voice_sidecar" ] && radio_vo_speaker=$(cat "$_voice_sidecar" 2>/dev/null || true)
	fi
	local ready_wav=""
	ready_wav=$(_radio_ready_wav_path "$qf")
	local ready_bundle="${ready_wav}.bundle"
	if [ "${RADIO_TIME_SYNC_ENABLED:-1}" = "1" ] && command -v _radio_sync_deferred_time_before_render >/dev/null 2>&1; then
		_radio_sync_deferred_time_before_render "$qf" "$deferred_corner"
		local time_sync_rc=$?
		[ "$time_sync_rc" -eq 2 ] && return 0
	fi
	if [ ! -s "$ready_wav" ]; then
		_radio_start_deferred_render_if_needed "$qf" "$deferred_corner" "$deferred_cc_text" "$radio_vo_speaker"
		return 0
	fi
	_radio_clear_deferred_render_retry "$qf"

	local playing_file="${qf%.txt}.playing"
	if mv "$qf" "$playing_file" 2>/dev/null; then
		local radio_meta_summary=""
		radio_meta_summary=$(_radio_generation_debug_summary "$playing_file" 2>/dev/null || true)
		log "[RADIO:deferred] 再生開始: $(basename "$playing_file")${radio_meta_summary:+ ($radio_meta_summary)} ready=$(basename "$ready_wav")"
		# deferred radio is executed by the comment player itself, so it must not
		# yield to comments queued after this point or playback deadlocks.
		local radio_play_rc=1
		if [ -s "$ready_bundle/playlist.txt" ] && [ -s "$ready_bundle/captions.txt" ]; then
			if SAY_CC_TEXT="$deferred_cc_text" SAY_DISABLE_COMMENT_YIELD=1 SAY_CHUNK_GAP_SEC=0 SAY_CONTEXT_LABEL="radio:${deferred_corner:-deferred}" \
				./say_enqueue.sh --no-preempt --wav-playlist "$ready_bundle/playlist.txt" --caption-chunks "$ready_bundle/captions.txt" "$playing_file" "$RADIO_SAY_RATE" 0; then
				radio_play_rc=0
			else
				radio_play_rc=$?
			fi
		else
			# 更新前に生成済みのWAVは従来経路で安全に再生する。
			if SAY_CC_TEXT="$deferred_cc_text" SAY_DISABLE_COMMENT_YIELD=1 SAY_CONTEXT_LABEL="radio:${deferred_corner:-deferred}" \
				./say_enqueue.sh --no-preempt --wav "$ready_wav" "$RADIO_SAY_RATE" 0; then
				radio_play_rc=0
			else
				radio_play_rc=$?
			fi
		fi
		if [ "$radio_play_rc" -eq 0 ]; then
			_radio_commit_spoken_history_for_file "$playing_file" 2>/dev/null || true
			_broadcast_clear_expected_mode "$playing_file" 2>/dev/null || true
			_radio_clear_generation_meta "$playing_file" 2>/dev/null || true
			_radio_backup_script "$playing_file" 2>/dev/null || true
			rm -f "$playing_file" "$ready_wav" "${ready_wav}.tmp" "$(_radio_render_marker_path "$playing_file")" "$(_radio_render_retry_path "$playing_file")" "$(_radio_render_meta_path "$playing_file")" "${playing_file%.playing}.news_title" "${playing_file%.playing}.cc_text" "${playing_file%.playing}.voice"
			rm -rf "$ready_bundle" 2>/dev/null || true
			log "[RADIO:deferred] 再生完了: $(basename "$playing_file")"
		else
			if [ -f "tmp/.say_queue/kill_flag" ]; then
				_radio_clear_spoken_history_line "$playing_file" 2>/dev/null || true
				_broadcast_clear_expected_mode "$playing_file" 2>/dev/null || true
				_radio_clear_generation_meta "$playing_file" 2>/dev/null || true
				rm -f "tmp/.say_queue/kill_flag" "$playing_file" "$ready_wav" "${ready_wav}.tmp" "$(_radio_render_marker_path "$playing_file")" "$(_radio_render_retry_path "$playing_file")" "$(_radio_render_meta_path "$playing_file")" "${playing_file%.playing}.voice"
				rm -rf "$ready_bundle" 2>/dev/null || true
				log "[RADIO:deferred] 外部killにより破棄: $(basename "$playing_file")"
			else
				local retry_file="${playing_file%.playing}.txt"
				mv "$playing_file" "$retry_file" 2>/dev/null || true
				log "[RADIO:deferred] 再生失敗 → キューへ戻す: $(basename "$retry_file")"
			fi
		fi
	fi
}
