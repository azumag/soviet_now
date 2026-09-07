# lib/ai_generate.sh - AI生成の共通ディスパッチ
#
# コメント生成とラジオ生成の AI 呼び出しを統一するバックエンド。
# モデル選択・フォールバック・タイムアウト・エラー検出を一箇所で管理する。
#
# 使い方:
#   ai_generate "RADIO" "$prompt_file" "$primary_agent" "$fallback_agent"
#   output は stdout に返る。呼び出し元がファイルに書くかキューに積むか判断する。

_ai_guard_model_output() {
	local guard_root="${ELOOP_LIB_DIR:-.}"
	# C4 (common_parts_chat_c4.md C-S2): 出力ガードの正典は docich 側。
	# DOCICH_BIN が利用可能なら docich ai-guard へ委譲し、無い環境 (CI・探索モード等)
	# では従来どおりローカルの model_output_guard.py を使う (互換維持・fail-open)。
	local _docich_bin="${DOCICH_BIN:-}"
	if [ -z "$_docich_bin" ]; then
		_docich_bin="$(command -v docich 2>/dev/null || true)"
	fi
	if [ -n "$_docich_bin" ] && [ -x "$_docich_bin" ]; then
		"$_docich_bin" ai-guard
		return $?
	fi
	python3 "$guard_root/lib/model_output_guard.py"
}

# ai_generate_list がモデルをバックオフするのは、プロバイダが明示的に
# レート制限を返した場合だけにする。形式不正・空出力・認証/CLI失敗は
# 次の候補へフォールバックするが、モデル自体は止めない。
AI_RATE_LIMIT_RC=79

# 改善ゲート打ち切りを通常のモデル失敗(rc=1)と区別する専用リターンコード。
# ゲート放棄はモデル呼び出し自体が発生しないため、attempt/fail 統計に
# 計上せず gate_giveup イベントとして分離する (2026-08-22 ユーザー指摘)。
AI_GATE_GIVEUP_RC=91

# 生成レーンの混雑打ち切りを provider 失敗と区別する専用コード。
# AI_GENERATION_QUEUE_MAX_WAIT_SEC=0 (既定) では従来どおり無期限待機。
# 補助的な短時間処理だけが明示的に上限を指定できる。
AI_QUEUE_GIVEUP_RC=92

_ai_rate_limit_text_detected() {
	printf '%s' "${1:-}" | grep -Eiq \
		'(^|[^[:alnum:]])429([^[:alnum:]]|$)|too[[:space:]_-]*many[[:space:]_-]*requests|rate[[:space:]_-]*limit([[:space:]_-]*(ed|exceeded))?|quota[[:space:]_-]*(exceeded|exhausted|limit)|resource[[:space:]_-]*exhausted|usage[[:space:]_-]*limit'
}

# === 統一バックエンド ===

AI_GENERATION_QUEUE_LAST_TOKEN=""

_ai_lock_sanitize_key() {
	local raw="${1:-default}"
	printf '%s' "$raw" |
		tr '[:upper:]' '[:lower:]' |
		sed -E 's/[^a-z0-9._-]+/_/g; s/^_+//; s/_+$//'
}

_ai_model_is_local() {
	local model="$1" item norm_model norm_item
	norm_model=$(_ai_lock_sanitize_key "$model")
	for item in ${LOCAL_LLM_AGENTS:-qwen35e qwen3.5:9b gemma4nt ccogent gemma4nt_ccogent}; do
		norm_item=$(_ai_lock_sanitize_key "$item")
		[ -n "$norm_item" ] || continue
		[ "$norm_model" = "$norm_item" ] && return 0
	done
	return 1
}

_ai_queue_lock_scope() {
	local label="${1:-AI}" rest scope
	# レーン制御: 放送系 (RADIO/NEWS/JIJI/CELEBRATION) はモデル・provider差に関わらず
	# 単一の "radio" ロックへ畳んで直列化する。モデル別に並行すると放送系が
	# 複数同時に走るため (2026-08-21 実測: prepass 2重起動)。AI_RADIO_LANE_LOCK=0
	# で従来のモデル別スコープへ戻せる。
	case "$label" in
	RADIO* | NEWS* | JIJI* | CELEBRATION*)
		if [ "${AI_RADIO_LANE_LOCK:-1}" = "1" ]; then
			printf '%s' "radio"
			return 0
		fi
		;;
	COMMENT*)
		# コメント返しもレーン化して直列化する (同一モデル内は元々 _opencode_run_lock
		# 等で直列だが、モデルが違うと並行し得た)。
		if [ "${AI_COMMENT_LANE_LOCK:-1}" = "1" ]; then
			printf '%s' "comment"
			return 0
		fi
		;;
	esac
	case "$label" in
	*:local:*) printf '%s' "local"; return 0 ;;
	*:remote:*)
		rest="${label#*:remote:}"
		scope="remote:$rest"
		printf '%s' "$(_ai_lock_sanitize_key "$scope")"
		return 0
		;;
	esac
	printf '%s' "$(_ai_lock_sanitize_key "$label")"
}

# 改善ジョブ (eloop_improve runtime) が現在稼働中かどうか。
# improve_state.json の status=="running" と PID 生存を両方見る。
# improve.lock はデータファイル (ゲーム履歴) で常時存在するため信号に使えない。
_improve_job_active() {
	local state_file="${IMPROVE_STATE_FILE:-${TMP_STATE_DIR:-tmp/state}/improve_state.json}"
	[ -f "$state_file" ] || return 1
	local probe
	probe=$(python3 - "$state_file" <<'PY' 2>/dev/null || true
import json, sys
try:
    with open(sys.argv[1]) as f:
        d = json.load(f)
except Exception:
    print("inactive"); raise SystemExit
if str(d.get("status", "")) != "running":
    print("inactive"); raise SystemExit
try:
    pid = int(d.get("pid") or 0)
except Exception:
    pid = 0
started = d.get("started_at") or 0
try:
    started = int(started)
except Exception:
    started = 0
import os, time
alive = False
if pid > 0:
    try:
        os.kill(pid, 0)
        alive = True
    except ProcessLookupError:
        alive = False
    except PermissionError:
        alive = True
stale = 7200
if started > 0 and (time.time() - started) > stale:
    alive = False
print("active" if alive else "inactive")
PY
) 
	[ "$probe" = "active" ]
}

_ai_generation_queue_lock_dir() {
	local label="${1:-AI}" base scope
	if [ -n "${AI_GENERATION_QUEUE_LOCK_DIR:-}" ]; then
		printf '%s\n' "$AI_GENERATION_QUEUE_LOCK_DIR"
		return 0
	fi
	if [ -n "${ELOOP_LIB_DIR:-}" ]; then
		base="$ELOOP_LIB_DIR/tmp/state/.ai_generation_locks"
	else
		base="tmp/state/.ai_generation_locks"
	fi
	scope=$(_ai_queue_lock_scope "$label")
	printf '%s/%s\n' "$base" "${scope:-default}"
}

_ai_queue_label() {
	local label="$1" provider="$2" model="$3" locality="remote"
	if _ai_model_is_local "$model"; then
		locality="local"
	fi
	printf '%s:%s:%s:%s' "$label" "$locality" "$provider" "$model"
}

AI_GENERATION_QUEUE_GUARD_FD=""
AI_GENERATION_QUEUE_GUARD_DIR=""

# Serialize lock-directory claim/reap/release.  The generation lock itself is
# intentionally held for the whole AI call, while this guard is held only for
# the few filesystem operations that change ownership.  Linux production uses
# flock(2), which is released automatically if a contender dies.  Platforms
# without flock use a fail-closed mkdir guard; it is never reaped by a second
# contender, so safety is preserved even though a crashed guard owner may make
# that single acquisition fail after the bounded wait.
_ai_generation_queue_guard_enter() {
	local lock_dir="$1" guard_file guard_dir fd waited=0
	guard_file="${lock_dir}.owner_guard.lock"
	guard_dir="${lock_dir}.owner_guard.d"
	mkdir -p "$(dirname "$lock_dir")" 2>/dev/null || return 1
	AI_GENERATION_QUEUE_GUARD_FD=""
	AI_GENERATION_QUEUE_GUARD_DIR=""

	if command -v flock >/dev/null 2>&1; then
		exec {fd}>>"$guard_file" || return 1
		if ! flock -x "$fd" 2>/dev/null; then
			eval "exec ${fd}>&-" 2>/dev/null || true
			return 1
		fi
		AI_GENERATION_QUEUE_GUARD_FD="$fd"
		return 0
	fi

	while ! mkdir "$guard_dir" 2>/dev/null; do
		if [ "$waited" -ge "${AI_GENERATION_QUEUE_GUARD_MAX_WAIT_SEC:-30}" ]; then
			log "[AIQ] generation owner guard unavailable; fail closed (${guard_dir})" >&2
			return 1
		fi
		sleep 1
		waited=$((waited + 1))
	done
	AI_GENERATION_QUEUE_GUARD_DIR="$guard_dir"
	return 0
}

_ai_generation_queue_guard_leave() {
	local fd="${AI_GENERATION_QUEUE_GUARD_FD:-}" guard_dir="${AI_GENERATION_QUEUE_GUARD_DIR:-}"
	case "$fd" in
	'' | *[!0-9]*) ;;
	*)
		flock -u "$fd" 2>/dev/null || true
		eval "exec ${fd}>&-" 2>/dev/null || true
		;;
	esac
	if [ -n "$guard_dir" ]; then
		rmdir "$guard_dir" 2>/dev/null || true
	fi
	AI_GENERATION_QUEUE_GUARD_FD=""
	AI_GENERATION_QUEUE_GUARD_DIR=""
}

_ai_generation_queue_enter() {
	local label="${1:-AI}"
	local lock_dir
	local wait_sec="${AI_GENERATION_QUEUE_WAIT_SEC:-2}"
	local stale_sec="${AI_GENERATION_QUEUE_STALE_SEC:-900}"
	local max_wait_sec="${AI_GENERATION_QUEUE_MAX_WAIT_SEC:-0}"
	local waited=0 token now mt age owner_summary="" owner_pid="" reap_reason=""
	lock_dir=$(_ai_generation_queue_lock_dir "$label")

	case "$wait_sec" in
	'' | *[!0-9]*) wait_sec=2 ;;
	esac
	[ "$wait_sec" -lt 1 ] && wait_sec=1
	case "$stale_sec" in
	'' | *[!0-9]*) stale_sec=900 ;;
	esac
	[ "$stale_sec" -lt 60 ] && stale_sec=60
	case "$max_wait_sec" in
	'' | *[!0-9]*) max_wait_sec=0 ;;
	esac

	mkdir -p "$(dirname "$lock_dir")" 2>/dev/null || true
	token="${BASHPID:-$$}:$RANDOM:$(date +%s)"

	while :; do
		_ai_generation_queue_guard_enter "$lock_dir" || {
			AI_GENERATION_QUEUE_LAST_TOKEN=""
			return 1
		}
		if mkdir "$lock_dir" 2>/dev/null; then
			{
				printf 'token=%s\n' "$token"
				printf 'pid=%s\n' "${BASHPID:-$$}"
				printf 'label=%s\n' "$label"
				printf 'started_at=%s\n' "$(date '+%F %T')"
			} >"$lock_dir/owner" 2>/dev/null || true
			_ai_generation_queue_guard_leave
			AI_GENERATION_QUEUE_LAST_TOKEN="$token"
			[ "$waited" -gt 0 ] && log "[AIQ:${label}] generation slot acquired after ${waited}s" >&2
			return 0
		fi

		owner_pid=$(sed -n 's/^pid=//p' "$lock_dir/owner" 2>/dev/null | head -n 1)
		owner_summary=$(tr '\n' ' ' <"$lock_dir/owner" 2>/dev/null | sed 's/[[:space:]]\+/ /g')
		now=$(date +%s)
		mt=$(stat -f %m "$lock_dir" 2>/dev/null) \
			|| mt=$(stat -c %Y "$lock_dir" 2>/dev/null) \
			|| mt="$now"
		age=$((now - mt))
		reap_reason=""
		case "$owner_pid" in
		'' | *[!0-9]*) ;;
		*)
			if ! kill -0 "$owner_pid" 2>/dev/null; then
				reap_reason="dead generation lock owner cleared (pid=${owner_pid})"
			fi
			;;
		esac
		if [ -z "$reap_reason" ] && [ "$age" -gt "$stale_sec" ]; then
			reap_reason="stale generation lock cleared (age=${age}s)"
		fi

		if [ -n "$reap_reason" ]; then
			# The guard covers both deletion and the replacement claim.  No follower
			# can act on an observation of the old owner after a new owner appears.
			rm -rf "$lock_dir" 2>/dev/null || true
			if mkdir "$lock_dir" 2>/dev/null; then
				{
					printf 'token=%s\n' "$token"
					printf 'pid=%s\n' "${BASHPID:-$$}"
					printf 'label=%s\n' "$label"
					printf 'started_at=%s\n' "$(date '+%F %T')"
				} >"$lock_dir/owner" 2>/dev/null || true
				_ai_generation_queue_guard_leave
				log "[AIQ:${label}] ${reap_reason}" >&2
				AI_GENERATION_QUEUE_LAST_TOKEN="$token"
				[ "$waited" -gt 0 ] && log "[AIQ:${label}] generation slot acquired after ${waited}s" >&2
				return 0
			fi
		fi

		_ai_generation_queue_guard_leave
		if [ "$waited" -eq 0 ] || [ $((waited % 30)) -eq 0 ]; then
			log "[AIQ:${label}] queued: waiting for generation slot${owner_summary:+ (${owner_summary})}" >&2
		fi
		if [ "$max_wait_sec" -gt 0 ] && [ "$waited" -ge "$max_wait_sec" ]; then
			log "[AIQ:${label}] generation slot wait exceeded ${max_wait_sec}s${owner_summary:+ (${owner_summary})}" >&2
			AI_GENERATION_QUEUE_LAST_TOKEN=""
			return "$AI_QUEUE_GIVEUP_RC"
		fi
		sleep "$wait_sec"
		waited=$((waited + wait_sec))
	done
}

_ai_generation_queue_leave() {
	local token="${1:-}" label="${2:-AI}"
	local lock_dir current_token=""
	[ -n "$token" ] || return 0
	lock_dir=$(_ai_generation_queue_lock_dir "$label")
	_ai_generation_queue_guard_enter "$lock_dir" || {
		log "[AIQ:${label}] generation owner guard unavailable; skip unsafe release" >&2
		return 1
	}
	if [ -d "$lock_dir" ]; then
		current_token=$(sed -n 's/^token=//p' "$lock_dir/owner" 2>/dev/null | head -n 1)
		if [ "$current_token" = "$token" ]; then
			rm -rf "$lock_dir" 2>/dev/null || true
		else
			log "[AIQ:${label}] generation lock owner changed; skip release" >&2
		fi
	fi
	_ai_generation_queue_guard_leave
	return 0
}

# 改善ジョブ稼働中、新規の放送系AI生成を待機させるゲート。
# - RADIO_GEN_STARTED_AT (コーナー生成開始時刻) が改善ジョブの started_at より前なら
#   待たせない (= 生成開始済みの放送はキャンセルせず完走させる)。
# - 待機は AI_RADIO_IMPROVE_WAIT_MAX_SEC で打ち切り、超過時は rc=AI_GATE_GIVEUP_RC (この生成だけ諦める)。
# - 読み上げ・TTS はここを通らないため影響を受けない。
_ai_radio_improve_gate() {
	local label="${1:-RADIO}"
	[ "${AI_RADIO_IMPROVE_GATE:-1}" = "1" ] || return 0
	# 同一 dispatch 内の二重待機防止 (_ai_dispatch がゲート通過後に立てる)。
	[ "${AI_GATE_PASSED_FOR_DISPATCH:-0}" = "1" ] && return 0
	_improve_job_active || return 0
	local state_file="${IMPROVE_STATE_FILE:-${TMP_STATE_DIR:-tmp/state}/improve_state.json}"
	local improve_started gen_started waited=0
	improve_started=$(python3 -c 'import json,sys;print(int(json.load(open(sys.argv[1])).get("started_at") or 0))' "$state_file" 2>/dev/null || echo 0)
	gen_started="${RADIO_GEN_STARTED_AT:-}"
	case "$gen_started" in '' | *[!0-9]*) gen_started="" ;;
	esac
	if [ -n "$gen_started" ] && [ "$improve_started" -gt 0 ] && [ "$gen_started" -lt "$improve_started" ]; then
		log "[AIQ:${label}] improve active but this generation started earlier -> proceed without cancel" >&2
		return 0
	fi
	local wait_sec="${AI_GENERATION_QUEUE_WAIT_SEC:-2}"
	local max_wait="${AI_RADIO_IMPROVE_WAIT_MAX_SEC:-1200}"
	case "$wait_sec" in '' | *[!0-9]*) wait_sec=2 ;;
	esac
	case "$max_wait" in '' | *[!0-9]*) max_wait=1200 ;;
	esac
	log "[AIQ:${label}] improve cycle active -> radio generation waits (max=${max_wait}s)" >&2
	while _improve_job_active; do
		if [ "$waited" -ge "$max_wait" ]; then
			log "[AIQ:${label}] improve still active after ${waited}s -> give up this generation" >&2
			return "$AI_GATE_GIVEUP_RC"
		fi
		sleep "$wait_sec"
		waited=$((waited + wait_sec))
		if [ $((waited % 30)) -lt "$wait_sec" ]; then
			log "[AIQ:${label}] improve active -> still waiting (${waited}s/${max_wait}s)" >&2
		fi
	done
	log "[AIQ:${label}] improve finished -> proceed after ${waited}s wait" >&2
	return 0
}

_ai_generation_queue_run() {
	local label="${1:-AI}"
	shift
	local token rc
	if [ "${AI_GENERATION_QUEUE_ENABLED:-1}" != "1" ]; then
		"$@"
		return $?
	fi
	case "$label" in
	RADIO* | NEWS* | JIJI* | CELEBRATION*)
		# 改善中は新規放送生成を待機 (開始済み生成はキャンセルしない)。
		# 打ち切りコード (AI_GATE_GIVEUP_RC) をそのまま伝播させる。
		_ai_radio_improve_gate "$label"
		local _qgate_rc=$?
		[ "$_qgate_rc" -eq 0 ] || return "$_qgate_rc"
		;;
	COMMENT*) ;;
	*)
		"$@"
		return $?
		;;
	esac
	_ai_generation_queue_enter "$label"
	local _queue_rc=$?
	[ "$_queue_rc" -eq 0 ] || return "$_queue_rc"
	token="$AI_GENERATION_QUEUE_LAST_TOKEN"
	"$@"
	rc=$?
	_ai_generation_queue_leave "$token" "$label"
	return "$rc"
}

OPENCODE_RUN_LOCK_LAST_TOKEN=""

_opencode_run_lock_dir() {
	local agent="${1:-}" base scope
	if [ -n "${OPENCODE_RUN_LOCK_DIR:-}" ]; then
		printf '%s\n' "$OPENCODE_RUN_LOCK_DIR"
	elif [ -n "$agent" ]; then
		if [ -n "${ELOOP_LIB_DIR:-}" ]; then
			base="$ELOOP_LIB_DIR/tmp/state/.opencode_run_locks"
		else
			base="tmp/state/.opencode_run_locks"
		fi
		if _ai_model_is_local "$agent"; then
			scope="local"
		else
			scope="remote:opencode:$agent"
		fi
		printf '%s/%s\n' "$base" "$(_ai_lock_sanitize_key "$scope")"
	elif [ -n "${ELOOP_LIB_DIR:-}" ]; then
		printf '%s/tmp/state/.opencode_run_lock\n' "$ELOOP_LIB_DIR"
	else
		printf 'tmp/state/.opencode_run_lock\n'
	fi
}

_opencode_xdg_state_home() {
	if [ -n "${OPENCODE_XDG_STATE_HOME:-}" ]; then
		printf '%s\n' "$OPENCODE_XDG_STATE_HOME"
	elif [ -n "${ELOOP_LIB_DIR:-}" ]; then
		printf '%s/tmp/state/xdg_state\n' "$ELOOP_LIB_DIR"
	else
		printf 'tmp/state/xdg_state\n'
	fi
}

_opencode_xdg_data_home() {
	if [ -n "${OPENCODE_XDG_DATA_HOME:-}" ]; then
		printf '%s\n' "$OPENCODE_XDG_DATA_HOME"
	elif [ -n "${ELOOP_LIB_DIR:-}" ]; then
		printf '%s/tmp/state/xdg_data\n' "$ELOOP_LIB_DIR"
	else
		printf 'tmp/state/xdg_data\n'
	fi
}

_opencode_sync_auth_to_xdg() {
	local src="${OPENCODE_AUTH_SOURCE:-$HOME/.local/share/opencode/auth.json}"
	local dst_dir dst
	[ -s "$src" ] || return 0
	dst_dir="$(_opencode_xdg_data_home)/opencode"
	dst="$dst_dir/auth.json"
	mkdir -p "$dst_dir" 2>/dev/null || return 0
	if [ ! -s "$dst" ] || ! cmp -s "$src" "$dst" 2>/dev/null; then
		cp "$src" "$dst" 2>/dev/null || true
	fi
}

_opencode_cleanup_internal_locks() {
	local locks_dir stale_sec now lock mt age
	locks_dir="$(_opencode_xdg_state_home)/opencode/locks"
	stale_sec="${OPENCODE_INTERNAL_LOCK_STALE_SEC:-60}"
	case "$stale_sec" in
	'' | *[!0-9]*) stale_sec=60 ;;
	esac
	[ "$stale_sec" -lt 10 ] && stale_sec=10
	[ -d "$locks_dir" ] || return 0
	now=$(date +%s)
	while IFS= read -r lock; do
		[ -n "$lock" ] || continue
		mt=$(stat -f %m "$lock" 2>/dev/null) \
			|| mt=$(stat -c %Y "$lock" 2>/dev/null) \
			|| mt="$now"
		age=$((now - mt))
		if [ "$age" -gt "$stale_sec" ]; then
			rm -rf "$lock" 2>/dev/null || true
		fi
	done < <(find "$locks_dir" -mindepth 1 -maxdepth 1 -type d -name '*.lock' 2>/dev/null)
}

_opencode_run_lock_enter() {
	local label="${1:-opencode}"
	local agent="${2:-}"
	local lock_dir wait_sec stale_sec max_wait_sec waited=0 token now mt age owner_summary="" owner_pid="" postmortem_stale_sec
	if [ "${OPENCODE_RUN_LOCK_ENABLED:-1}" != "1" ]; then
		OPENCODE_RUN_LOCK_LAST_TOKEN=""
		return 0
	fi
	lock_dir=$(_opencode_run_lock_dir "$agent")
	wait_sec="${OPENCODE_RUN_LOCK_WAIT_SEC:-2}"
	stale_sec="${OPENCODE_RUN_LOCK_STALE_SEC:-1800}"
	max_wait_sec="${OPENCODE_RUN_LOCK_MAX_WAIT_SEC:-0}"
	case "$wait_sec" in
	'' | *[!0-9]*) wait_sec=2 ;;
	esac
	[ "$wait_sec" -lt 1 ] && wait_sec=1
	case "$stale_sec" in
	'' | *[!0-9]*) stale_sec=1800 ;;
	esac
	[ "$stale_sec" -lt 60 ] && stale_sec=60
	case "$max_wait_sec" in
	'' | *[!0-9]*) max_wait_sec=0 ;;
	esac

	mkdir -p "$(dirname "$lock_dir")" 2>/dev/null || true
	token="${BASHPID:-$$}:$RANDOM:$(date +%s)"

	while ! mkdir "$lock_dir" 2>/dev/null; do
		now=$(date +%s)
		mt=$(stat -f %m "$lock_dir" 2>/dev/null) \
			|| mt=$(stat -c %Y "$lock_dir" 2>/dev/null) \
			|| mt="$now"
		age=$((now - mt))
		owner_pid=$(sed -n 's/^pid=//p' "$lock_dir/owner" 2>/dev/null | head -n 1)
		owner_summary=$(tr '\n' ' ' <"$lock_dir/owner" 2>/dev/null | sed 's/[[:space:]]\+/ /g')
		postmortem_stale_sec="${ROLLBACK_POSTMORTEM_OPENCODE_LOCK_STALE_SEC:-240}"
		case "$postmortem_stale_sec" in
		'' | *[!0-9]*) postmortem_stale_sec=240 ;;
		esac
		[ "$postmortem_stale_sec" -lt 60 ] && postmortem_stale_sec=60
		if [ "$max_wait_sec" -gt 0 ] && [ "$postmortem_stale_sec" -ge "$max_wait_sec" ]; then
			postmortem_stale_sec=$((max_wait_sec - wait_sec))
			[ "$postmortem_stale_sec" -lt 60 ] && postmortem_stale_sec=60
		fi
		if [[ "$owner_summary" == *ROLLBACK-POSTMORTEM* ]] && [ "$age" -gt "$postmortem_stale_sec" ]; then
			log "[OPENCODE:${label}] stale rollback-postmortem run lock cleared (age=${age}s, ${owner_summary})" >&2
			rm -rf "$lock_dir" 2>/dev/null || true
			continue
		fi
		if [[ "$owner_pid" =~ ^[0-9]+$ ]] && ! kill -0 "$owner_pid" 2>/dev/null; then
			log "[OPENCODE:${label}] stale run lock cleared (dead owner pid=${owner_pid}, age=${age}s)" >&2
			rm -rf "$lock_dir" 2>/dev/null || true
			continue
		fi
		if [ "$age" -gt "$stale_sec" ]; then
			log "[OPENCODE:${label}] stale run lock cleared (age=${age}s)" >&2
			rm -rf "$lock_dir" 2>/dev/null || true
			continue
		fi
		if [ "$waited" -eq 0 ] || [ $((waited % 30)) -eq 0 ]; then
			log "[OPENCODE:${label}] queued: waiting for opencode slot${owner_summary:+ (${owner_summary})}" >&2
		fi
		if [ "$max_wait_sec" -gt 0 ] && [ "$waited" -ge "$max_wait_sec" ]; then
			log "[OPENCODE:${label}] opencode slot wait exceeded ${max_wait_sec}s${owner_summary:+ (${owner_summary})}" >&2
			OPENCODE_RUN_LOCK_LAST_TOKEN=""
			return 124
		fi
		sleep "$wait_sec"
		waited=$((waited + wait_sec))
	done

	{
		printf 'token=%s\n' "$token"
		printf 'pid=%s\n' "${BASHPID:-$$}"
		printf 'label=%s\n' "$label"
		printf 'started_at=%s\n' "$(date '+%F %T')"
	} >"$lock_dir/owner" 2>/dev/null || true
	OPENCODE_RUN_LOCK_LAST_TOKEN="$token"
	[ "$waited" -gt 0 ] && log "[OPENCODE:${label}] opencode slot acquired after ${waited}s" >&2
	return 0
}

_opencode_run_lock_leave() {
	local token="${1:-}" label="${2:-opencode}" agent="${3:-}"
	local lock_dir current_token=""
	[ -n "$token" ] || return 0
	lock_dir=$(_opencode_run_lock_dir "$agent")
	[ -d "$lock_dir" ] || return 0
	current_token=$(sed -n 's/^token=//p' "$lock_dir/owner" 2>/dev/null | head -n 1)
	if [ "$current_token" = "$token" ]; then
		rm -rf "$lock_dir" 2>/dev/null || true
	else
		log "[OPENCODE:${label}] run lock owner changed; skip release" >&2
	fi
}

# _ai_call_claude LABEL PROMPT_FILE [MODEL] [TIMEOUT]
_ai_call_claude_unqueued() {
	local label="$1" prompt_file="$2"
	local model="${3:-$RADIO_CLAUDE_MODEL}"
	local timeout_sec="${4:-${RADIO_CLAUDE_TIMEOUT:-120}}"
	local output stderr_file stderr_preview provider_error=false login_error=false rate_limited=false

	[ -s "$prompt_file" ] || return 1
	log "[${label}] claude call (model=$model, prompt=$(wc -c <"$prompt_file" | tr -d ' ')B)" >&2
	stderr_file=$(mktemp /tmp/ai_claude_stderr_XXXXXXXX)
	output=$(cat "$prompt_file" | timeout "$timeout_sec" claude -p --model "$model" 2>"$stderr_file")
	local rc=$?
	if [ -s "$stderr_file" ]; then
		stderr_preview=$(head -c 500 "$stderr_file")
		log "[${label}] claude stderr: $stderr_preview" >&2
	fi
	if _contains_provider_error_text "$output" || { [ -n "${stderr_preview:-}" ] && _contains_provider_error_text "$stderr_preview"; }; then
		provider_error=true
	fi
	if { [ "$rc" -ne 0 ] || [ -n "${stderr_preview:-}" ]; } && [ -n "${stderr_preview:-}" ]; then
		if _ai_rate_limit_text_detected "$stderr_preview"; then
			rate_limited=true
		fi
	fi
	if _contains_claude_login_error_text "$output" || { [ -n "${stderr_preview:-}" ] && _contains_claude_login_error_text "$stderr_preview"; }; then
		login_error=true
	fi
	[ "$login_error" = "true" ] && log "[${label}] claude unavailable: not logged in" >&2
	rm -f "$stderr_file"
	if [ $rc -eq 124 ]; then
		log "[${label}] claude timeout (${timeout_sec}s, model=$model)" >&2
		[ "$rate_limited" = "true" ] && return "$AI_RATE_LIMIT_RC"
		return 1
	fi
	if [ "$provider_error" = "true" ]; then
		log "[${label}] claude provider/auth error (model=$model)" >&2
		[ "$rate_limited" = "true" ] && return "$AI_RATE_LIMIT_RC"
		return 1
	fi
	if [ $rc -ne 0 ]; then
		log "[${label}] claude failed (rc=$rc, model=$model)" >&2
		[ "$rate_limited" = "true" ] && return "$AI_RATE_LIMIT_RC"
		return 1
	fi
	printf '%s' "$output"
}

# _ai_call_minimax LABEL PROMPT_FILE [MODEL] [TIMEOUT]
_ai_call_minimax_unqueued() {
	local label="$1" prompt_file="$2"
	local model="${3:-${MINIMAX_MODEL:-MiniMax-M2.7}}"
	local timeout_sec="${4:-${RADIO_CLAUDE_TIMEOUT:-120}}"
	local output_file output raw_failure rate_limited=false

	[ -s "$prompt_file" ] || return 1
	log "[${label}] minimax call (model=$model, prompt=$(wc -c <"$prompt_file" | tr -d ' ')B)" >&2
	output_file=$(mktemp /tmp/ai_minimax_output_XXXXXXXX)
	_run_minimax_claude_prompt_file "$prompt_file" "$output_file" "$model" "$timeout_sec" "acceptEdits"
	local rc=$?
	local stderr_preview="${MINIMAX_CLAUDE_LAST_STDERR:-}"
	local provider_error="${MINIMAX_CLAUDE_LAST_PROVIDER_ERROR:-false}"
	local login_error="${MINIMAX_CLAUDE_LAST_LOGIN_ERROR:-false}"
	[ -n "$stderr_preview" ] && log "[${label}] minimax stderr: $stderr_preview" >&2
	if [ $rc -ne 0 ] || [ -n "$stderr_preview" ]; then
		raw_failure="$stderr_preview"
		_ai_rate_limit_text_detected "$raw_failure" && rate_limited=true
	fi
	[ "$login_error" = "true" ] && log "[${label}] minimax unavailable: not logged in" >&2
	if [ $rc -eq 124 ]; then
		rm -f "$output_file"
		log "[${label}] minimax timeout (${timeout_sec}s, model=$model)" >&2
		[ "$rate_limited" = "true" ] && return "$AI_RATE_LIMIT_RC"
		return 1
	fi
	if [ "$provider_error" = "true" ]; then
		rm -f "$output_file"
		log "[${label}] minimax provider/auth error (model=$model)" >&2
		[ "$rate_limited" = "true" ] && return "$AI_RATE_LIMIT_RC"
		return 1
	fi
	if [ $rc -ne 0 ]; then
		rm -f "$output_file"
		log "[${label}] minimax failed (rc=$rc, model=$model)" >&2
		[ "$rate_limited" = "true" ] && return "$AI_RATE_LIMIT_RC"
		return 1
	fi
	output=$(cat "$output_file" 2>/dev/null)
	rm -f "$output_file"
	printf '%s' "$output"
}

# _ai_call_ollama LABEL PROMPT_FILE [MODEL] [TIMEOUT]
_ai_call_ollama_unqueued() {
	local label="$1" prompt_file="$2"
	local model="${3:-${RADIO_OLLAMA_MODEL:-qwen3.5:9b}}"
	local timeout_sec="${4:-${RADIO_OLLAMA_TIMEOUT:-180}}"
	local prompt output stderr_file stderr_preview rate_limited=false

	[ -s "$prompt_file" ] || return 1
	log "[${label}] ollama call (model=$model, prompt=$(wc -c <"$prompt_file" | tr -d ' ')B)" >&2
	prompt=$(cat "$prompt_file")
	stderr_file=$(mktemp /tmp/ai_ollama_stderr_XXXXXXXX)
	output=$(
		ANTHROPIC_AUTH_TOKEN="ollama" \
			ANTHROPIC_BASE_URL="$OLLAMA_BASE_URL" \
			ANTHROPIC_API_KEY="" \
			timeout "$timeout_sec" claude -p "$prompt" --model="$model" --permission-mode=acceptEdits 2>"$stderr_file"
	)
	local rc=$?
	if [ -s "$stderr_file" ]; then
		stderr_preview=$(head -c 500 "$stderr_file")
		log "[${label}] ollama stderr: $stderr_preview" >&2
	fi
	if [ $rc -ne 0 ] && [ -n "$stderr_preview" ]; then
		_ai_rate_limit_text_detected "$stderr_preview" && rate_limited=true
	fi
	if [ $rc -eq 124 ]; then
		log "[${label}] ollama timeout (${timeout_sec}s, model=$model)" >&2
		rm -f "$stderr_file"
		[ "$rate_limited" = "true" ] && return "$AI_RATE_LIMIT_RC"
		return 1
	fi
	if [ $rc -ne 0 ]; then
		log "[${label}] ollama failed (rc=$rc, model=$model)" >&2
		rm -f "$stderr_file"
		[ "$rate_limited" = "true" ] && return "$AI_RATE_LIMIT_RC"
		return 1
	fi
	if _contains_provider_error_text "$output"; then
		log "[${label}] ollama provider error (model=$model)" >&2
		rm -f "$stderr_file"
		[ "$rate_limited" = "true" ] && return "$AI_RATE_LIMIT_RC"
		return 1
	fi
	rm -f "$stderr_file"
	printf '%s' "$output"
}

_ai_call_claude() {
	local label="${1:-AI}" model="${3:-$RADIO_CLAUDE_MODEL}"
	_ai_generation_queue_run "$(_ai_queue_label "$label" "claude" "$model")" _ai_call_claude_unqueued "$@"
}

_ai_call_minimax() {
	local label="${1:-AI}" model="${3:-${MINIMAX_MODEL:-MiniMax-M2.7}}"
	_ai_generation_queue_run "$(_ai_queue_label "$label" "minimax" "$model")" _ai_call_minimax_unqueued "$@"
}

_ai_call_ollama() {
	local label="${1:-AI}" model="${3:-${RADIO_OLLAMA_MODEL:-qwen3.5:9b}}"
	_ai_generation_queue_run "$(_ai_queue_label "$label" "ollama" "$model")" _ai_call_ollama_unqueued "$@"
}

# === ローカル LLM (Tailscale 経由・無料) ===

# _ai_call_local_llm LABEL PROMPT_FILE [MODEL] [TIMEOUT]
_ai_call_local_llm_unqueued() {
	local label="$1" prompt_file="$2"
	local model="${3:-${LOCAL_LLM_MODEL:-gemma4:12b}}"
	local timeout_sec="${4:-${LOCAL_LLM_TIMEOUT:-180}}"
	local base_url="${LOCAL_LLM_BASE_URL:-http://100.112.104.102:11434}"
	local output rc stderr_file stderr_preview body_file rate_limited=false

	[ -s "$prompt_file" ] || return 1
	log "[${label}] local LLM call (model=$model, prompt=$(wc -c <"$prompt_file" | tr -d ' ')B)" >&2
	stderr_file=$(mktemp /tmp/ai_local_llm_stderr_XXXXXXXX)
	body_file=$(mktemp /tmp/ai_local_llm_body_XXXXXXXX)
	python3 - "$prompt_file" "$body_file" "$model" <<'PY'
import json
import sys

prompt_file, body_file, model = sys.argv[1:4]
with open(prompt_file, encoding="utf-8") as f:
    prompt = f.read()
body = {
    "model": model,
    "messages": [{"role": "user", "content": prompt}],
    "stream": False,
    "temperature": 0.7,
    "num_predict": 1600,
}
with open(body_file, "w", encoding="utf-8") as f:
    json.dump(body, f, ensure_ascii=False)
PY
	output=$(timeout "$timeout_sec" curl -sS --max-time "$timeout_sec" \
		"$base_url/v1/chat/completions" \
		-H 'Content-Type: application/json' \
		--data-binary @"$body_file" 2>"$stderr_file")
	rc=$?
	rm -f "$body_file"
	stderr_preview=$(head -c 500 "$stderr_file" 2>/dev/null || true)
	if [ $rc -ne 0 ] && [ -n "$stderr_preview" ]; then
		_ai_rate_limit_text_detected "$stderr_preview" && rate_limited=true
	fi
	if [ $rc -eq 124 ]; then
		log "[${label}] local LLM timeout (${timeout_sec}s, model=$model)" >&2
		rm -f "$stderr_file"
		[ "$rate_limited" = "true" ] && return "$AI_RATE_LIMIT_RC"
		return 1
	fi
	if [ $rc -ne 0 ]; then
		log "[${label}] local LLM failed (rc=$rc, model=$model)" >&2
		rm -f "$stderr_file"
		[ "$rate_limited" = "true" ] && return "$AI_RATE_LIMIT_RC"
		return 1
	fi
	local content
	content=$(printf '%s' "$output" | python3 -c '
import json
import sys

try:
    data = json.load(sys.stdin)
    print(data["choices"][0]["message"].get("content") or "")
except Exception:
    print("")
' 2>/dev/null)
	rm -f "$stderr_file"
	if [ -z "$content" ]; then
		log "[${label}] local LLM empty output (model=$model)" >&2
		return 1
	fi
	if _contains_provider_error_text "$content"; then
		log "[${label}] local LLM provider error (model=$model)" >&2
		[ "$rate_limited" = "true" ] && return "$AI_RATE_LIMIT_RC"
		return 1
	fi
	printf '%s' "$content"
}

_ai_call_local_llm() {
	local label="${1:-AI}" model="${3:-${LOCAL_LLM_MODEL:-gemma4:12b}}"
	_ai_generation_queue_run "$(_ai_queue_label "$label" "local" "$model")" _ai_call_local_llm_unqueued "$@"
}

# === Opencode (free tier via opencode CLI) ===

# _ai_call_opencode LABEL AGENT PROMPT_FILE [TIMEOUT]
# opencode:deepseek-v4-flash-free などは opencode CLI で直接呼ぶ。
# 検証済み: /snap/bin/opencode run --model opencode/deepseek-v4-flash-free は litellm の zen/v1 429 と異なり成功する。
_ai_call_opencode_unqueued() {
	local label="$1" agent="$2" prompt_file="$3"
	local timeout_sec="${4:-90}"
	local model="opencode/${agent#opencode:}"
	case "$agent" in
	vercel:*) model="vercel/${agent#vercel:}" ;;
	amd:*) model="amd-token-factory/${agent#amd:}" ;;
	minimax-api:*) model="minimax-api/${agent#minimax-api:}" ;;
	opencode-go/*) model="$agent" ;;
	opencode-go:*) model="opencode-go/${agent#opencode-go:}" ;;
	opencode/*) model="$agent" ;;
	opencode:*) model="opencode/${agent#opencode:}" ;;
	*/*) model="$agent" ;;
	esac
	# opencode binary: prefer /snap/bin/opencode, fallback to opencode
	# (OPENCODE_BIN はテスト時のスタブ差し替え用。本番では未設定でよい)
	local opencode_bin="${OPENCODE_BIN:-/snap/bin/opencode}"
	[ -x "$opencode_bin" ] || opencode_bin="opencode"
	[ -s "$prompt_file" ] || { _ai_error_preview_set "empty prompt file"; return 1; }
	local out_file stderr_file stderr_preview rc cleaned rate_limited=false
	local opencode_agent_args=()
	case "$agent" in
	vercel:*|amd:*|minimax-api:*)
		case "$label" in
		*prepass*|*PREPASS*|*RESEARCH*) opencode_agent_args=(--agent soren-research) ;;
		*) opencode_agent_args=(--agent soren-lite) ;;
		esac
		;;
	esac
	out_file=$(mktemp /tmp/ai_opencode_out_XXXXXXXX)
	stderr_file=$(mktemp /tmp/ai_opencode_stderr_XXXXXXXX)
	case "$timeout_sec" in
	'' | *[!0-9]*) timeout_sec=90 ;;
	esac
	[ "$timeout_sec" -lt 1 ] && timeout_sec=1
	log "[${label}] opencode call (model=$model, prompt=$(wc -c <"$prompt_file" | tr -d ' ')B)" >&2
	# opencode run --model は prompt を引数として渡す。timeout でラップする。
	# free枠の上流ではストリームが reasoning 途中で切れて rc!=0・空出力に
	# なる一過性の障害が多いため、タイムアウトとレート制限以外は1回だけ
	# 同一モデルで再試行する（OPENCODE_ABORT_RETRY=0 で無効化）。
	local _oc_attempts=1 _oc_try=1 _oc_start _oc_elapsed
	if [ "${OPENCODE_ABORT_RETRY:-1}" = "1" ] && [[ "$agent" != vercel:* ]]; then
		_oc_attempts=2
	fi
	while :; do
		_oc_start=$(date +%s)
		timeout --kill-after=10s "$timeout_sec" "$opencode_bin" run "${opencode_agent_args[@]}" --model "$model" "$(cat "$prompt_file")" >"$out_file" 2>"$stderr_file"
		rc=$?
		_oc_elapsed=$(( $(date +%s) - _oc_start ))
		cleaned=""
		if [ "$rc" -eq 124 ]; then
			break
		fi
		if [ "$rc" -eq 0 ] && [ -s "$out_file" ]; then
			# reasoning-only応答はraw出力としては非空でも本文が空になる。
			# ここで本文を検査し、空なら一過性失敗と同じ再試行経路へ戻す。
			cleaned=$(_ai_strip_reasoning_blocks <"$out_file")
			if [ -n "$cleaned" ] && ! _contains_provider_error_text "$cleaned"; then
				break
			fi
		fi
		stderr_preview=$(head -c 4000 "$stderr_file" 2>/dev/null || true)
		if _ai_rate_limit_text_detected "$stderr_preview"; then
			break
		fi
		if [ "$_oc_try" -ge "$_oc_attempts" ]; then
			break
		fi
		_oc_try=$((_oc_try + 1))
		log "[${label}] opencode transient failure (rc=$rc, elapsed=${_oc_elapsed}s, model=$model) → retry once" >&2
		sleep "${OPENCODE_ABORT_RETRY_WAIT_SEC:-2}"
	done
	_oc_elapsed=$(( $(date +%s) - _oc_start ))
	stderr_preview=$(head -c 4000 "$stderr_file" 2>/dev/null || true)
	if [ $rc -ne 0 ] || [ ! -s "$out_file" ]; then
		_ai_rate_limit_text_detected "$stderr_preview" && rate_limited=true
		# opencode の stderr にも rate limit が出ることがある
		if [ -n "$stderr_preview" ] && _ai_rate_limit_text_detected "$stderr_preview"; then
			rate_limited=true
		fi
	fi
	if [ $rc -eq 124 ]; then
		log "[${label}] opencode timeout (${timeout_sec}s, model=$model)" >&2
		_ai_error_preview_set "timeout after ${timeout_sec}s"
		rm -f "$out_file" "$stderr_file"
		[ "$rate_limited" = "true" ] && return "$AI_RATE_LIMIT_RC"
		return 1
	fi
	if [ $rc -ne 0 ]; then
		log "[${label}] opencode failed (rc=$rc, model=$model)" >&2
		[ -n "$stderr_preview" ] && log "[${label}] opencode stderr: $stderr_preview" >&2
		_ai_error_preview_set "rc=$rc (${_oc_elapsed}s): $(_ai_error_preview_from_text "$stderr_preview")"
		rm -f "$out_file" "$stderr_file"
		[ "$rate_limited" = "true" ] && return "$AI_RATE_LIMIT_RC"
		return 1
	fi
	cleaned=$(cat "$out_file" 2>/dev/null | _ai_strip_reasoning_blocks)
	rm -f "$out_file" "$stderr_file"
	if [ -z "$cleaned" ]; then
		log "[${label}] opencode empty after cleanup (model=$model)" >&2
		_ai_error_preview_set "empty output (${_oc_elapsed}s)"
		[ "$rate_limited" = "true" ] && return "$AI_RATE_LIMIT_RC"
		return 1
	fi
	if _contains_provider_error_text "$cleaned"; then
		log "[${label}] opencode provider error (model=$model)" >&2
		_ai_error_preview_set "provider error: $(_ai_error_preview_from_text "$cleaned")"
		[ "$rate_limited" = "true" ] && return "$AI_RATE_LIMIT_RC"
		return 1
	fi
	printf '%s' "$cleaned"
}

_ai_call_opencode() {
	local label="${1:-AI}" agent="${2:-opencode}"
	_ai_generation_queue_run "$(_ai_queue_label "$label" "opencode" "$agent")" _ai_call_opencode_unqueued "$@"
}

# === Codex (統一ハーネス) ===

# codex:<model> は指定モデルを使い、codex または従来形式の agent 名は
# CODEX_MODEL にフォールバックする。従来の agent 名を壊さず、明示した
# codex モデルだけを確実に -m へ渡す。
_ai_codex_model_from_agent() {
	local agent="${1:-}" model=""
	case "$agent" in
	codex:*) model="${agent#codex:}" ;;
	*) model="${CODEX_MODEL:-amd-token-factory-deepseek-v4-flash}" ;;
	esac
	[ -n "$model" ] || model="${CODEX_MODEL:-amd-token-factory-deepseek-v4-flash}"
	printf '%s' "$model"
}

# MiniMax 系が最終出力へ含めることがある非公開推論ブロックを、本文ごと除去する。
# タグだけを消すと推論本文が読み上げ対象に残るため、複数行を一括処理する。
_ai_strip_reasoning_blocks() {
	python3 -c '
import re
import sys

text = sys.stdin.read()
for tag in ("think", "analysis"):
    text = re.sub(
        rf"<{tag}\b[^>]*>.*?</{tag}\s*>",
        "",
        text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    text = re.sub(
        rf"<{tag}\b[^>]*>.*\Z",
        "",
        text,
        flags=re.IGNORECASE | re.DOTALL,
    )
text = re.sub(
    r"</?(?:final|assistant_response)\b[^>]*>",
    "",
    text,
    flags=re.IGNORECASE,
)
sys.stdout.write(text.strip())
'
}

# _ai_call_codex LABEL AGENT PROMPT_FILE [TIMEOUT]
#   codex CLI 経由で agent が示すモデルを呼ぶ。
_ai_call_codex_unqueued() {
	local label="$1" agent="$2" prompt_file="$3"
	local timeout_sec="${4:-${CODEX_TIMEOUT:-300}}"
	local model
	model=$(_ai_codex_model_from_agent "$agent")
	local codex_bin="${CODEX_BIN:-codex}"
	local out_file stderr_file stderr_preview raw_failure rc cleaned rate_limited=false
	[ -s "$prompt_file" ] || { _ai_error_preview_set "empty prompt file"; return 1; }
	out_file=$(mktemp /tmp/ai_codex_out_XXXXXXXX)
	stderr_file=$(mktemp /tmp/ai_codex_stderr_XXXXXXXX)
	case "$timeout_sec" in
	'' | *[!0-9]*) timeout_sec=300 ;;
	esac
	[ "$timeout_sec" -lt 1 ] && timeout_sec=1
	log "[${label}] codex call (model=$model, prompt=$(wc -c <"$prompt_file" | tr -d ' ')B)" >&2
	# stdin を /dev/null へ固定する。codex exec は stdin がパイプだとプロンプト
	# 引数ありでも `<stdin>` ブロックとして読み込むため、呼び出し元の stdin
	# 状態に応じて出力が汚れたりブロックしたりするのを防ぐ。
	timeout --kill-after=10s "$timeout_sec" "$codex_bin" exec \
		--skip-git-repo-check -m "$model" -o "$out_file" "$(cat "$prompt_file")" \
		</dev/null >/dev/null 2>"$stderr_file"
	rc=$?
	stderr_preview=$(head -c 4000 "$stderr_file" 2>/dev/null || true)
	raw_failure="$stderr_preview"
	# -o の本文はモデル出力であり、通常の失敗時に「rate limit」という
	# 語を含むだけでもバックオフを発火させてしまうため判定対象にしない。
	# 明示的なプロバイダ診断はCLIのstderr（rc非0/出力欠落時）だけを見る。
	if [ $rc -ne 0 ] || [ ! -s "$out_file" ]; then
		_ai_rate_limit_text_detected "$raw_failure" && rate_limited=true
	fi
	if [ $rc -eq 124 ]; then
		log "[${label}] codex timeout (${timeout_sec}s, model=$model)" >&2
		_ai_error_preview_set "timeout after ${timeout_sec}s"
		rm -f "$out_file"
		rm -f "$stderr_file"
		[ "$rate_limited" = "true" ] && return "$AI_RATE_LIMIT_RC"
		return 1
	fi
	if [ $rc -ne 0 ]; then
		log "[${label}] codex failed (rc=$rc, model=$model)" >&2
		_ai_error_preview_set "rc=$rc: $(_ai_error_preview_from_text "$stderr_preview")"
		rm -f "$out_file"
		rm -f "$stderr_file"
		[ "$rate_limited" = "true" ] && return "$AI_RATE_LIMIT_RC"
		return 1
	fi
	cleaned=$(_ai_strip_reasoning_blocks <"$out_file")
	rm -f "$out_file"
	rm -f "$stderr_file"
	if [ -z "$cleaned" ]; then
		log "[${label}] codex empty after cleanup (model=$model)" >&2
		_ai_error_preview_set "empty output"
		[ "$rate_limited" = "true" ] && return "$AI_RATE_LIMIT_RC"
		return 1
	fi
	if _contains_provider_error_text "$cleaned"; then
		log "[${label}] codex provider error (model=$model)" >&2
		_ai_error_preview_set "provider error: $(_ai_error_preview_from_text "$cleaned")"
		[ "$rate_limited" = "true" ] && return "$AI_RATE_LIMIT_RC"
		return 1
	fi
	printf '%s' "$cleaned"
}

_ai_call_codex() {
	local label="${1:-AI}" agent="${2:-codex}"
	_ai_generation_queue_run "$(_ai_queue_label "$label" "codex" "$agent")" _ai_call_codex_unqueued "$@"
}

# === 統一ディスパッチャ ===

# 許可するエージェント指定を一箇所で検証する。
# 未知の値を Codex の既定モデルへ黙って流すと、設定 typo や一時的な
# sentinel が別モデルの呼び出しに化けるため、生成前に明示的に拒否する。
_ai_agent_spec_valid() {
	local agent="${1:-}" model=""
	case "$agent" in
	codex)
		return 0
		;;
	codex:*)
		model="${agent#codex:}"
		;;
	opencode:*)
		model="${agent#opencode:}"
		;;
	opencode-go:*)
		model="${agent#opencode-go:}"
		;;
	vercel:*)
		[ -n "${VERCEL_FREE_AGENTS:-}" ] || return 1
		model="${agent#vercel:}"
		;;
	amd:*)
		model="${agent#amd:}"
		;;
	minimax-api:*)
		model="${agent#minimax-api:}"
		;;
	local)
		return 0
		;;
	local:*)
		model="${agent#local:}"
		;;
	*)
		return 1
		;;
	esac
	[ -n "$model" ]
}

# _ai_dispatch LABEL AGENT PROMPT_FILE [TIMEOUT]
#   agent 識別子に基づいて適切なバックエンドを呼ぶ。
#   stdout: 生成テキスト
_ai_resolved_model_from_agent() {
	local agent="${1:-}" resolved_model
	resolved_model="$agent"
	case "$agent" in
	codex:*) resolved_model="${agent#codex:}" ;;
	opencode-go:*) resolved_model="opencode-go/${agent#opencode-go:}" ;;
	opencode:*) resolved_model="opencode/${agent#opencode:}" ;;
	vercel:*) resolved_model="vercel/${agent#vercel:}" ;;
	amd:*) resolved_model="amd-token-factory/${agent#amd:}" ;;
	minimax-api:*) resolved_model="minimax-api/${agent#minimax-api:}" ;;
	local:*) resolved_model="${agent#local:}" ;;
	local) resolved_model="${LOCAL_LLM_MODEL:-gemma4:12b}" ;;
	*) resolved_model="${CODEX_MODEL:-amd-token-factory-deepseek-v4-flash}" ;;
	esac
	printf '%s' "$resolved_model"
}

_ai_dispatch() {
	local label="$1" agent="$2" prompt_file="$3"
	local timeout_override="${4:-}"
	local validator="${AI_DISPATCH_VALIDATOR:-}"
	if ! _ai_agent_spec_valid "$agent"; then
		log "[${label}] invalid agent spec ignored: ${agent:-<empty>}" >&2
		return 2
	fi
	local resolved_model="$(_ai_resolved_model_from_agent "$agent")"
	# 改善ゲートをモデル呼び出しと attempt 記録の前に判定する。
	# 打ち切り時は gate_giveup イベントだけを記録し、モデル呼び出しが無いにも
	# 関わらず attempt/fail 統計が膨らむのを防ぐ (2026-08-22 ユーザー指摘)。
	# 通過後は AI_GATE_PASSED_FOR_DISPATCH を立てて内側キューゲートの二重待機を防ぐ。
	local _ai_gate_scoped=0
	case "$label" in
	RADIO* | NEWS* | JIJI* | CELEBRATION*)
		_ai_radio_improve_gate "$label"
		local _gate_rc=$?
		if [ "$_gate_rc" -eq "$AI_GATE_GIVEUP_RC" ]; then
			_ai_stats_record "gate_giveup" "$label" "$agent" "" "$resolved_model"
			log "[${label}] improve gate gave up before model call (agent=$agent)" >&2
			return "$AI_GATE_GIVEUP_RC"
		fi
		AI_GATE_PASSED_FOR_DISPATCH=1
		_ai_gate_scoped=1
		;;
	esac
	_ai_stats_record "attempt" "$label" "$agent" "" "$resolved_model"
	AI_DISPATCH_ERROR_PREVIEW=""
	local _dispatch_err_file
	_dispatch_err_file=$(mktemp /tmp/ai_err_preview_XXXXXXXX)
	: >"$_dispatch_err_file"
	AI_DISPATCH_ERROR_PREVIEW_FILE="$_dispatch_err_file"

	# プロンプトと生成結果をログディレクトリに保存
	local _dispatch_log_dir="tmp/debug/ai_dispatch"
	mkdir -p "$_dispatch_log_dir" 2>/dev/null || true
	local _dispatch_ts
	_dispatch_ts=$(date +%Y%m%d_%H%M%S)
	local _dispatch_tag="${_dispatch_ts}_${label}_${agent}"
	_dispatch_tag=$(printf '%s' "$_dispatch_tag" | tr '/:' '_')
	if [ -s "$prompt_file" ]; then
		cp "$prompt_file" "$_dispatch_log_dir/${_dispatch_tag}_prompt.txt" 2>/dev/null || true
	fi

	local _dispatch_output_file="$_dispatch_log_dir/${_dispatch_tag}_output.txt"

	# codex:<model> は Codex CLI、opencode:<model> / opencode-go:<model> は
	# OpenCode CLIへ渡す。従来形式のエージェント識別子は CODEX_MODEL を使う。
	# local[:<model>] は Tailscale 経由の無料ローカル LLM へ直接送る。
	local _codex_timeout="$timeout_override"
	case "$agent" in
	codex | codex:*)
		if [[ "$label" == COMMENT* ]] && [ -z "$_codex_timeout" ]; then
			_codex_timeout="${COMMENT_CODEX_TIMEOUT:-90}"
		fi
		if [[ "$label" == RADIO* ]] && [ -z "$_codex_timeout" ]; then
			_codex_timeout="${RADIO_CODEX_TIMEOUT:-240}"
		fi
		_ai_call_codex "$label" "$agent" "$prompt_file" "$_codex_timeout" | tee "$_dispatch_output_file"
		;;
	local:* | local)
		local _local_model=""
		[ "$agent" = "local" ] || _local_model="${agent#local:}"
		_ai_call_local_llm "$label" "$prompt_file" "$_local_model" "$timeout_override" | tee "$_dispatch_output_file"
		;;
	opencode-go:*|opencode:*|vercel:*|amd:*|minimax-api:*)
		local _opencode_timeout="$timeout_override"
		if [[ "$agent" == vercel:* ]]; then
			_opencode_timeout="${VERCEL_OPENCODE_TIMEOUT:-20}"
		fi
		if [[ "$label" == COMMENT* ]] && [ -z "$_opencode_timeout" ]; then
			_opencode_timeout="${COMMENT_CODEX_TIMEOUT:-90}"
		fi
		if [[ "$label" == RADIO* ]] && [ -z "$_opencode_timeout" ]; then
			_opencode_timeout="${RADIO_CODEX_TIMEOUT:-240}"
		fi
		_ai_call_opencode "$label" "$agent" "$prompt_file" "$_opencode_timeout" | tee "$_dispatch_output_file"
		;;
	*)
		if [[ "$label" == COMMENT* ]] && [ -z "$_codex_timeout" ]; then
			_codex_timeout="${COMMENT_CODEX_TIMEOUT:-90}"
		fi
		if [[ "$label" == RADIO* ]] && [ -z "$_codex_timeout" ]; then
			_codex_timeout="${RADIO_CODEX_TIMEOUT:-240}"
		fi
		_ai_call_codex "$label" "$agent" "$prompt_file" "$_codex_timeout" | tee "$_dispatch_output_file"
		;;
	esac
	local _dispatch_rc=${PIPESTATUS[0]}
	# dispatch スコープのゲート通過フラグはここで必ず解除する
	# (同一シェル内の次の候補・次の生成に漏出させない)。
	if [ "$_ai_gate_scoped" -eq 1 ]; then
		AI_GATE_PASSED_FOR_DISPATCH=0
	fi
	AI_DISPATCH_ERROR_PREVIEW_FILE=""
	local _dispatch_error_preview
	# 書き手側 (_ai_error_preview_from_text) で160字へ切り詰め済みのため
	# ここでバイト切断するとマルチバイト文字を壊す。行単位で読む。
	_dispatch_error_preview=$(head -n 1 "$_dispatch_err_file" 2>/dev/null || true)
	rm -f "$_dispatch_err_file"
	if [ "$_dispatch_rc" -eq "$AI_QUEUE_GIVEUP_RC" ]; then
		_ai_stats_record "queue_giveup" "$label" "$agent" "" "$resolved_model"
		log "[${label}] generation queue gave up before model call (agent=$agent)" >&2
		[ -s "$_dispatch_output_file" ] || rm -f "$_dispatch_output_file" 2>/dev/null
		return "$AI_QUEUE_GIVEUP_RC"
	fi
	if [ "$_dispatch_rc" -eq 0 ]; then
		local _dispatch_output
		_dispatch_output=$(cat "$_dispatch_output_file" 2>/dev/null || true)
		if [ -n "$_dispatch_output" ] && { [ -z "$validator" ] || "$validator" "$_dispatch_output"; }; then
			if [ "${AI_DISPATCH_RECORD_WINNER:-0}" = "1" ]; then
				_ai_stats_record "winner" "$label" "$agent" "$_dispatch_rc" "$resolved_model"
			fi
			_ai_stats_record "ok" "$label" "$agent" "$_dispatch_rc" "$resolved_model"
			_ai_fail_streak_clear "$agent"
		elif [ -n "$_dispatch_output" ] && [ -n "$validator" ]; then
			log "[${label}] ${agent} returned invalid output" >&2
			_ai_stats_record "fail" "$label" "$agent" "$_dispatch_rc" "$resolved_model" "invalid output rejected by validator"
		else
			log "[${label}] ${agent} returned empty output" >&2
			_ai_stats_record "fail" "$label" "$agent" "$_dispatch_rc" "$resolved_model" "empty output"
		fi
	else
		_ai_stats_record "fail" "$label" "$agent" "$_dispatch_rc" "$resolved_model" "${_dispatch_error_preview:-${AI_DISPATCH_ERROR_PREVIEW:-}}"
	fi
	# 空出力ならログファイル削除
	[ -s "$_dispatch_output_file" ] || rm -f "$_dispatch_output_file" 2>/dev/null
	return "$_dispatch_rc"
}

# === フォールバック付き生成 ===

# ai_generate LABEL PROMPT_FILE PRIMARY_AGENT [FALLBACK_AGENT] [TIMEOUT] [VALIDATOR]
#   PRIMARY_AGENT で生成を試み、失敗したら FALLBACK_AGENT にフォールバック。
#   stdout: 生成テキスト
#   メタデータ: AI_GENERATE_LAST_AGENT に実際に使用した agent を設定
AI_GENERATE_LAST_AGENT=""

ai_generate() {
	local label="$1" prompt_file="$2" primary="$3"
	local fallback="${4:-}"
	local timeout_override="${5:-}"
	local validator="${6:-}"
	local output

	AI_GENERATE_LAST_AGENT=""

	# Primary
	output=$(_ai_dispatch "$label" "$primary" "$prompt_file" "$timeout_override")
	rc=$?
	if [ "$rc" -eq "$AI_QUEUE_GIVEUP_RC" ]; then
		return "$AI_QUEUE_GIVEUP_RC"
	fi
	if [ "$rc" -eq 0 ] && [ -n "$output" ] && { [ -z "$validator" ] || "$validator" "$output"; }; then
		AI_GENERATE_LAST_AGENT="$primary"
		printf '%s' "$output"
		return 0
	fi
	if [ "$rc" -eq 0 ] && [ -n "$output" ] && [ -n "$validator" ]; then
		log "[${label}] primary ($primary) returned invalid output" >&2
	fi

	# Fallback
	if [ -n "$fallback" ]; then
		log "[${label}] primary ($primary) failed → fallback ($fallback)" >&2
		output=$(_ai_dispatch "$label" "$fallback" "$prompt_file" "$timeout_override")
		rc=$?
		if [ "$rc" -eq "$AI_QUEUE_GIVEUP_RC" ]; then
			return "$AI_QUEUE_GIVEUP_RC"
		fi
		if [ "$rc" -eq 0 ] && [ -n "$output" ] && { [ -z "$validator" ] || "$validator" "$output"; }; then
			AI_GENERATE_LAST_AGENT="$fallback"
			printf '%s' "$output"
			return 0
		fi
		if [ "$rc" -eq 0 ] && [ -n "$output" ] && [ -n "$validator" ]; then
			log "[${label}] fallback ($fallback) returned invalid output" >&2
		fi
	fi

	log "[${label}] all agents failed (primary=$primary, fallback=${fallback:-none})" >&2
	return 1
}

# === AI 利用統計 ===

# _ai_stats_record EVENT LABEL AGENT RC [RESOLVED_MODEL]
#   モデル呼び出しを 1 日 1 ファイルの JSONL へ記録する。stdout には一切出さない
#   (生成テキストへ混入を避ける)。events: attempt / ok / fail。RC は呼び出し元の
#   return code (空文字可)。resolved_model は実際に CLI へ渡したモデル名。
#   フォールバックの最終結果は ai_generate_list 側で "winner"/"all_failed" として記録する。
_ai_stats_record() {
	local event="$1" label="$2" agent="$3" rc="${4:-}" resolved_model="${5:-}" error_preview="${6:-}"
	[ -n "$event" ] || return 0
	local stats_dir="_ai_stats_dir"
	if [ -n "${AI_STATS_DIR:-}" ]; then
		stats_dir="$AI_STATS_DIR"
	elif [ -n "${ELOOP_LIB_DIR:-}" ]; then
		stats_dir="$ELOOP_LIB_DIR/tmp/state/ai_stats"
	else
		stats_dir="tmp/state/ai_stats"
	fi
	mkdir -p "$stats_dir" 2>/dev/null || true
	local ts day line
	ts=$(date +%s)
	day=$(date +%Y%m%d)
	line=$(printf '{"ts":%s,"day":"%s","event":"%s","label":"%s","agent":"%s","rc":"%s","resolved_model":"%s"}' \
		"$ts" "$day" "$event" "$label" "${agent:-}" "${rc:-}" "${resolved_model:-}")
	if [ -n "$error_preview" ]; then
		# JSON文字列として安全な形へエスケープしてから付与する
		error_preview=${error_preview//\\/\\\\}
		error_preview=${error_preview//\"/\\\"}
		error_preview=${error_preview//$'\n'/ }
		error_preview=${error_preview//$'\r'/ }
		error_preview=${error_preview//$'\t'/ }
		local base="${line%\}}"
		line="${base},\"error\":\"${error_preview}\"}"
	fi
	printf '%s\n' "$line" >>"$stats_dir/${day}.jsonl" 2>/dev/null || true
}

# === 失敗理由の観測 (ai_stats への error フィールド供給) ===

# 直近の _ai_dispatch 呼び出し内での失敗概要。低レベル呼び出し関数が設定し、
# _ai_dispatch が fail 記録時に読む。stderr が worker の外で廃棄されても
# 失敗理由が ai_stats JSONL に残るようにするための経路。
# 低レベル呼び出しは tee パイプラインの左辺（サブシェル）で走るため、
# 変数だけでなくファイル (_ai_error_preview_set) 経由でも受け渡す。
AI_DISPATCH_ERROR_PREVIEW=""
AI_DISPATCH_ERROR_PREVIEW_FILE=""

_ai_error_preview_set() {
	AI_DISPATCH_ERROR_PREVIEW="$1"
	if [ -n "${AI_DISPATCH_ERROR_PREVIEW_FILE:-}" ]; then
		printf '%s\n' "$1" >"$AI_DISPATCH_ERROR_PREVIEW_FILE" 2>/dev/null || true
	fi
}

_ai_error_preview_from_text() {
	local text="$1"
	# CLIのstderrは切断位置によって不正なUTF-8を含むことがある。perl -CSD
	# はそこで致命終了し、errorフィールドが欠落していたため、バイト列として
	# 制御文字だけを除去してからUTF-8として解釈する。
	text=$(printf '%s' "$text" | perl -pe '
		s/\e\[[0-9;]*[a-zA-Z]//g;
		s/[\x00-\x09\x0b-\x1f\x7f]/ /g;
	')
	if [ ${#text} -gt 200 ]; then
		printf '%s' "${text:0:70} …${#text}ch… ${text: -90}"
	else
		printf '%s' "$text" | tr '\n' ' ' | tr -s ' '
	fi
}

# === 連続失敗サーキットブレーカ ===

# provider/CLI 失敗 (rc!=0) が連続した回数を agent 単位で記録する。
# ai_generate_list はこの回数で短いバックオフを段階的に延長し、ダウン中の
# モデルへ短周期で再試行し続ける無駄を防ぐ。成功時 (_ai_dispatch ok) に解除。
_ai_fail_streak_dir() {
	if [ -n "${AI_FAIL_STREAK_DIR:-}" ]; then
		printf '%s\n' "$AI_FAIL_STREAK_DIR"
	elif [ -n "${ELOOP_LIB_DIR:-}" ]; then
		printf '%s/tmp/state/ai_fail_streak\n' "$ELOOP_LIB_DIR"
	else
		printf 'tmp/state/ai_fail_streak\n'
	fi
}

_ai_fail_streak_file() {
	local agent="$1"
	printf '%s/%s\n' "$(_ai_fail_streak_dir)" "$(_ai_lock_sanitize_key "$agent")"
}

_ai_fail_streak_record() {
	local agent="$1" f n
	f=$(_ai_fail_streak_file "$agent")
	mkdir -p "$(_ai_fail_streak_dir)" 2>/dev/null || true
	n=$(cat "$f" 2>/dev/null || echo 0)
	case "$n" in '' | *[!0-9]*) n=0 ;; esac
	n=$((n + 1))
	printf '%s\n' "$n" >"$f" 2>/dev/null || true
	printf '%s\n' "$n"
}

_ai_fail_streak_clear() {
	local f n
	f=$(_ai_fail_streak_file "$1")
	if [ -f "$f" ]; then
		n=$(cat "$f" 2>/dev/null || echo 0)
		case "$n" in
		'' | *[!0-9]*) n=0 ;;
		esac
		rm -f "$f" 2>/dev/null || true
		# 連続失敗が積み上がっていたモデルの復旧は運用上の重要シグナルなので記録する
		if [ "$n" -ge 3 ]; then
			log "[AI] ${1} recovered after ${n} consecutive failures (streak cleared)" >&2
		fi
	fi
	return 0
}

# === バックオフ付きエージェントリスト ===

_ai_backoff_dir() {
	if [ -n "${AI_BACKOFF_DIR:-}" ]; then
		printf '%s\n' "$AI_BACKOFF_DIR"
	elif [ -n "${ELOOP_LIB_DIR:-}" ]; then
		printf '%s/tmp/state/ai_backoff\n' "$ELOOP_LIB_DIR"
	else
		printf 'tmp/state/ai_backoff\n'
	fi
}

# コメント・ラジオでは、一度 primary から fallback へ進んだ後に同じ
# unavailable provider を毎回待たない。agent単位のbackoffファイルは両経路で
# 共有されるため、どちらかでDeepSeekが失敗すれば両方が5時間直接MiniMaxへ進む。
_ai_backoff_sec_for_label() {
	local label="${1:-AI}" value default_value
	case "$label" in
	COMMENT*) value="${COMMENT_AGENT_BACKOFF_SEC:-18000}"; default_value=18000 ;;
	RADIO*) value="${RADIO_AGENT_BACKOFF_SEC:-18000}"; default_value=18000 ;;
	*) value="${AI_AGENT_BACKOFF_SEC:-600}"; default_value=600 ;;
	esac
	case "$value" in
	'' | *[!0-9]*) value="$default_value" ;;
	esac
	[ "$value" -lt 1 ] && value="$default_value"
	printf '%s\n' "$value"
}

# _ai_backoff_sec_for_agent AGENT [LABEL] → モデル別バックオフ秒数
#  agent は "codex:<model>" または "local[:<model>]" の形。モデル名をキーに
#  AI_BACKOFF_SEC_ITEMS ("name:sec name:sec ...") から引く。該当なしはラベル既定
#  (COMMENT/RADIO=18000, その他=600) へフォールバック。
_ai_backoff_sec_for_agent() {
	local agent="$1" label="${2:-AI}" model="" item name sec
	case "$agent" in
	local:* | local)
		model="local"
		;;
	codex:*)
		model="${agent#codex:}"
		;;
	opencode-go:*)
		model="${agent#opencode-go:}"
		;;
	opencode:*)
		model="${agent#opencode:}"
		;;
	vercel:*)
		model="vercel/${agent#vercel:}"
		;;
	amd:*)
		model="${agent#amd:}"
		;;
	minimax-api:*)
		model="${agent#minimax-api:}"
		;;
	*)
		model="$agent"
		;;
	esac
	for item in ${AI_BACKOFF_SEC_ITEMS:-}; do
		name="${item%%:*}"
		sec="${item#*:}"
		if [ "$name" = "$model" ]; then
			printf '%s\n' "$sec"
			return 0
		fi
	done
	_ai_backoff_sec_for_label "$label"
}

# _ai_backoff_check AGENT  → 0: 使用可 / 1: バックオフ中
_ai_backoff_check() {
	local agent="$1" key bf_file now bf_until
	key=$(_ai_lock_sanitize_key "$agent")
	bf_file="$(_ai_backoff_dir)/${key}"
	[ -f "$bf_file" ] || return 0
	now=$(date +%s)
	bf_until=$(cat "$bf_file" 2>/dev/null || echo "0")
	case "$bf_until" in ''|*[!0-9]*) bf_until=0 ;; esac
	if [ "$now" -lt "$bf_until" ]; then
		return 1
	fi
	rm -f "$bf_file" 2>/dev/null || true
	return 0
}

# _ai_backoff_set AGENT [SEC]
_ai_backoff_set() {
	local agent="$1" backoff_sec="${2:-${AI_AGENT_BACKOFF_SEC:-600}}"
	local key bf_file bf_until
	key=$(_ai_lock_sanitize_key "$agent")
	mkdir -p "$(_ai_backoff_dir)" 2>/dev/null || true
	bf_file="$(_ai_backoff_dir)/${key}"
	bf_until=$(( $(date +%s) + backoff_sec ))
	printf '%s\n' "$bf_until" > "$bf_file" 2>/dev/null || true
}

# _ai_backoff_remaining AGENT  → 残りバックオフ秒数 (0: バックオフなし)
_ai_backoff_remaining() {
	local agent="$1" key bf_file now bf_until rem
	key=$(_ai_lock_sanitize_key "$agent")
	bf_file="$(_ai_backoff_dir)/${key}"
	[ -f "$bf_file" ] || { printf '0\n'; return; }
	now=$(date +%s)
	bf_until=$(cat "$bf_file" 2>/dev/null || echo "0")
	case "$bf_until" in ''|*[!0-9]*) bf_until=0 ;; esac
	rem=$(( bf_until - now ))
	[ "$rem" -lt 0 ] && rem=0
	printf '%s\n' "$rem"
}

# ai_generate_list LABEL PROMPT_FILE AGENT_LIST [TIMEOUT] [VALIDATOR] [LAST_AGENT_FILE] [FAILURE_KIND_FILE]
#   AGENT_LIST: カンマ区切りのエージェント識別子（優先度順）
#     例: "opencode:minimax-m3,opencode:qwen35pgo,qwen35e,opencode:glmflash"
#   バックオフ中のエージェントをスキップし、明示的なレート制限を返した
#   エージェントだけにバックオフを設定する。形式不正・空出力・通常の
#   CLI/provider失敗は、次候補へ進むがモデルのバックオフにはしない。
#   全エージェントがバックオフ中の場合は期限を尊重して再試行せず、呼び出し元へ失敗を返す。
#   stdout: 生成テキスト
#   AI_GENERATE_LAST_AGENT に実際に使用したエージェントを設定
#   LAST_AGENT_FILE を指定すると、command substitution の外側でも実使用モデルを読める。
AI_GENERATE_LIST_LAST_AGENT=""

ai_generate_list() {
	local label="$1" prompt_file="$2" agent_list_raw="$3"
	local timeout_override="${4:-}"
	local validator="${5:-}"
	local last_agent_file="${6:-}"
	local failure_kind_file="${7:-}"
	local _bd agent output rc _rem attempted_count=0 saw_rate_limit=0
	local saved_validator="${AI_DISPATCH_VALIDATOR:-}"

	AI_DISPATCH_VALIDATOR="$validator"

	AI_GENERATE_LAST_AGENT=""
	AI_GENERATE_LIST_LAST_AGENT=""
	[ -n "$last_agent_file" ] && : >"$last_agent_file"
	[ -n "$failure_kind_file" ] && : >"$failure_kind_file"

	_bd=$(_ai_backoff_dir)
	mkdir -p "$_bd" 2>/dev/null || true

	# カンマ区切りをリストに展開 (bash配列)
	local agents=()
	local _IFS_save="$IFS"
	IFS=',' read -ra agents <<< "$agent_list_raw"
	IFS="$_IFS_save"

	local skipped_backoff=()

	# 第1パス: バックオフ中でないエージェントを順に試行
	for agent in "${agents[@]}"; do
		# 前後の空白を除去
		agent="${agent#"${agent%%[![:space:]]*}"}"
		agent="${agent%"${agent##*[![:space:]]}"}"
		[ -z "$agent" ] && continue
		if ! _ai_agent_spec_valid "$agent"; then
			log "[${label}] invalid agent spec skipped: ${agent}" >&2
			continue
		fi

		if ! _ai_backoff_check "$agent"; then
			_rem=$(_ai_backoff_remaining "$agent")
			log "[${label}] backoff skip: ${agent} (${_rem}s remaining)" >&2
			skipped_backoff+=("$agent")
			continue
		fi

		attempted_count=$((attempted_count + 1))
		output=$(_ai_dispatch "$label" "$agent" "$prompt_file" "$timeout_override")
		rc=$?
		# 改善ゲートの打ち切りはモデル非依存で、実際のprovider呼び出しも発生していない。
		# fallbackを続けると全候補へ偽のfail streak/backoffを付けるため、その場で伝播する。
		if [ "$rc" -eq "$AI_GATE_GIVEUP_RC" ]; then
			[ -n "$failure_kind_file" ] && printf 'gate_giveup\n' >"$failure_kind_file"
			AI_DISPATCH_VALIDATOR="$saved_validator"
			return "$AI_GATE_GIVEUP_RC"
		fi
		if [ "$rc" -eq "$AI_QUEUE_GIVEUP_RC" ]; then
			[ -n "$failure_kind_file" ] && printf 'queue_giveup\n' >"$failure_kind_file"
			AI_DISPATCH_VALIDATOR="$saved_validator"
			return "$AI_QUEUE_GIVEUP_RC"
		fi
		AI_DISPATCH_VALIDATOR=""
		if [ "$rc" -eq 0 ] && [ -n "$output" ] && { [ -z "$validator" ] || "$validator" "$output"; }; then
			AI_GENERATE_LAST_AGENT="$agent"
			AI_GENERATE_LIST_LAST_AGENT="$agent"
			[ -n "$last_agent_file" ] && printf '%s\n' "$agent" >"$last_agent_file"
			_ai_stats_record "winner" "$label" "$agent" "0" "$(_ai_resolved_model_from_agent "$agent")"
			printf '%s' "$output"
			AI_DISPATCH_VALIDATOR="$saved_validator"
			return 0
		fi
		if [ "$rc" -eq 0 ] && [ -n "$output" ] && [ -n "$validator" ]; then
			log "[${label}] ${agent} returned invalid output → fallback" >&2
		else
			log "[${label}] ${agent} failed → fallback" >&2
		fi
		if [ "$rc" -eq "$AI_RATE_LIMIT_RC" ]; then
			saw_rate_limit=1
			local agent_backoff_sec
			agent_backoff_sec=$(_ai_backoff_sec_for_agent "$agent" "$label")
			log "[${label}] ${agent} explicit rate limit → backoff ${agent_backoff_sec}s" >&2
			_ai_backoff_set "$agent" "$agent_backoff_sec"
		else
			# プロバイダ/CLI 失敗 (rc!=0) は短いバックオフ
			# (AI_BACKOFF_FAILURE_SEC) に留める。一過性の障害で無料枠が
			# 1日級にパークされるのを防ぐ。形式不正・空出力 (rc=0) は「モデルは
			# 動くが品質が悪い」ためバックオフしない（呼び出し元のリトライ）。
			if [ "$rc" -ne 0 ] && [ -n "$agent" ]; then
				local failure_backoff_sec="${AI_BACKOFF_FAILURE_SEC:-300}"
				case "$failure_backoff_sec" in
				'' | *[!0-9]*) failure_backoff_sec=300 ;;
				esac
				[ "$failure_backoff_sec" -lt 1 ] && failure_backoff_sec=300
				# 連続失敗サーキットブレーカ: 短いバックオフの再試行が
				# 失敗し続ける場合、base×2→×4→×8（既定300なら最大2400秒、
				# AI_FAILURE_STREAK_MAX_BACKOFF_SEC 既定3600が上限）へ段階延長する。
				local _streak _shift _streak_max="${AI_FAILURE_STREAK_MAX_BACKOFF_SEC:-3600}"
				case "$_streak_max" in
				'' | *[!0-9]*) _streak_max=3600 ;;
				esac
				_streak=$(_ai_fail_streak_record "$agent")
				if [ "$_streak" -gt 1 ]; then
					_shift=$((_streak - 1))
					[ "$_shift" -gt 3 ] && _shift=3
					failure_backoff_sec=$((failure_backoff_sec * (1 << _shift)))
					[ "$failure_backoff_sec" -gt "$_streak_max" ] && failure_backoff_sec="$_streak_max"
				fi
				log "[${label}] ${agent} provider failure → short backoff ${failure_backoff_sec}s (outcome=${rc}, streak=${_streak})" >&2
				_ai_backoff_set "$agent" "$failure_backoff_sec"
			else
				log "[${label}] ${agent} no model backoff (outcome=${rc})" >&2
			fi
		fi
	done

	# 全候補がバックオフ中なら、期限を尊重して即時失敗する。以前の
	# 「末尾agentだけ強制再試行」は429を連打し、明示的なレート制限を
	# 回避してしまうため廃止した。期限切れは次回の通常チェックで解除される。
	if [ "$attempted_count" -eq 0 ] && [ ${#skipped_backoff[@]} -gt 0 ]; then
		log "[${label}] all agents are in explicit rate-limit backoff; retry later" >&2
		saw_rate_limit=1
	fi

	log "[${label}] all agents failed (list=${agent_list_raw})" >&2
	local resolved_models=""
	for agent in "${agents[@]}"; do
		agent="${agent#"${agent%%[![:space:]]*}"}"
		agent="${agent%"${agent##*[![:space:]]}"}"
		[ -n "$agent" ] || continue
		if [ -n "$resolved_models" ]; then
			resolved_models="${resolved_models},$(_ai_resolved_model_from_agent "$agent")"
		else
			resolved_models="$(_ai_resolved_model_from_agent "$agent")"
		fi
	done
	_ai_stats_record "all_failed" "$label" "" "" "$resolved_models"
	if [ -n "$failure_kind_file" ]; then
		if [ "$saw_rate_limit" -eq 1 ]; then
			printf 'rate_limit\n' >"$failure_kind_file"
		else
			printf 'failed\n' >"$failure_kind_file"
		fi
	fi
	AI_DISPATCH_VALIDATOR="$saved_validator"
	return 1
}
