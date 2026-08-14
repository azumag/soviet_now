# core/streaming_shim.sh - 探索モード専用: 配信系 sink の no-op 再定義
#
# ルール:
#   - 配信系「sink」(出力先が配信機能の関数)だけをここに置く。
#   - 探索モード (EXPLORE_MODE=1) では broadcast/ と soren91_control.sh が
#     source されないため、コア (eloop.sh / soren_loop.sh / eloop_improve.sh /
#     strategy/*.sh) から参照される配信系関数を最後に再定義して実体を呼ばない。
#   - 配信モード (EXPLORE_MODE=0) では何も定義しない (guard 内のみ)。
#   - このファイルは AI 書き換え対象外の安定レイヤー。
#   - AI が eloop.sh 等に「新しい配信系関数呼び出し」を追加した場合、
#     command-not-found (非致命、stderr 1行) になる。その関数をこの一覧に
#     追加する運用とする。
#
# 依存: core/config.sh が先に source 済みであること (EXPLORE_MODE を使用)。

if [ "${EXPLORE_MODE:-0}" = "1" ]; then

	_stream_noop() {
		local kind="${1:-unknown}" msg="${2:-}"
		[ -n "$msg" ] || return 0
		# レート制限付き breadcrumb (デバッグ用)。毎回書くとログが膨れるため、
		# 同一 kind は 60 秒に 1 回だけ記録する。
		local marker="${TMP_DEBUG_DIR:-tmp/debug}/explore_noop_${kind}.ts"
		local now interval
		now=$(date +%s)
		interval=60
		if [ -f "$marker" ]; then
			local last
			last=$(cat "$marker" 2>/dev/null || echo 0)
			case "$last" in '' | *[!0-9]*) last=0 ;; esac
			[ $((now - last)) -lt "$interval" ] && return 0
		fi
		mkdir -p "${TMP_DEBUG_DIR:-tmp/debug}" 2>/dev/null || true
		printf '%s\n' "$now" >"$marker" 2>/dev/null || true
		log "[EXPLORE-NOOP] ${kind}: ${msg}" >&2 || true
	}

	# --- outbound_queue sink ---
	enqueue_chat_message() {
		_stream_noop chat "${1:-}"
		return 0
	}
	enqueue_audio_text() {
		_stream_noop audio_text "${1:-}"
		return 0
	}
	enqueue_audio_file() {
		_stream_noop audio_file "${1:-}"
		return 0
	}

	# --- Twitch クリップ sink (core/version.sh 定義) ---
	_create_twitch_clip() { return 0; }

	# --- broadcast 参照関数 (eloop.sh の建国祝賀からのみ) ---
	generate_russia_celebration() {
		_stream_noop celebration "russia"
		return 0
	}
	generate_soviet_celebration() {
		_stream_noop celebration "soviet"
		return 0
	}
	_refresh_radio_intro_for_playback_file() { return 0; }
	_radio_clear_state() { return 0; }
	_cancel_russia_celebration_worker() { return 0; }
	_kill_comment_gen() { return 0; }
	# strategy/regression.sh のロールバック時通知 (broadcast/radio_corners.sh 定義)
	start_radio_corner_rollback() { return 0; }

	# --- soren91 / meriken (配信モードの実体は soren91_control.sh にあり、未 source) ---
	# 探索モードでは soren91 は起動せず、「停止中」として扱う。
	soren91_start() {
		_stream_noop soren91 "start"
		return 0
	}
	soren91_stop() { return 0; }
	soren91_cleanup() { return 0; }
	soren91_is_running() { return 1; }
	soren91_improve() { return 0; }
	soren91_stop_in_progress() { return 1; }
	soren91_handover() { return 0; }
	soren91_harvest_hung_improve() { return 0; }
	_soren91_enabled() { return 1; } # 定時メリケン枠を無効化
	_soren91_switch_obs_layout() { return 0; }
	_soren91_stop_in_progress() { return 1; }
	_soren91_session_improve() { return 0; }
	_soren91_active() { return 1; }
	manual_meriken_mode_is_enabled() { return 1; }
	manual_meriken_mode_enable() { return 0; }
	manual_meriken_mode_disable() { return 0; }
	# 定時枠の終了時刻を返す関数。探索モードでは 0 を返し即終了させる。
	scheduled_meriken_time_begin() {
		echo 0
		return 0
	}
	scheduled_meriken_time_end_label() { return 1; }

	# --- オーバーレイ orchestration (obs/overlay スクリプト guard と二重防御) ---
	# PID を返す関数は空文字を返し、caller の PID 生存チェックを false にする。
	_improve_overlay_generate_once() { return 0; }
	_improve_overlay_show() { return 0; }
	_improve_overlay_hide() { return 0; }
	_improve_overlay_hide_after() { return 0; }
	_improve_overlay_watch_start() {
		echo ""
		return 0
	}
	_wildcard_parallel_obs_show() { return 0; }
	_wildcard_parallel_obs_restore() { return 0; }

	# --- helpers.sh の Web 取得失敗通知 (overlay のみ抑止し、検知ログは残す) ---
	_notify_webfetch_failure() {
		local label="${1:-AI}" agent="${2:-unknown}" text="${3:-}" context="${4:-}"
		_contains_webfetch_failure_text "$text" || return 1
		log "[${label}] Web取得失敗を検出; on-air本文から除去済み (agent=${agent}${context:+ context=${context}})" >&2 || true
		return 0
	}

fi
