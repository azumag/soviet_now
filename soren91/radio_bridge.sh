#!/bin/bash
# radio_bridge.sh - soren91 から親プロジェクトの定時ラジオを呼び出すブリッジ
# Usage: radio_bridge.sh [game_num] [score]

GAME_NUM="${1:-0}"
SCORE="${2:-0}"

PARENT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PARENT_DIR" || exit 1

# soren91 モードを強制 (PIDファイルに依存しない)
soren91_is_running() { return 0; }

# 親プロジェクトのモジュールをロード (全モジュール source — radio は他レイヤーに依存)
source ./eloop_lib.sh 2>/dev/null

# 時刻ベースの定時コーナーのみチェック・実行
_check_timed_radio_corners() {
	local current_hour current_min today
	current_hour=$(date +%H)
	current_min=$(date +%M)
	today=$(date +%Y%m%d)

	_try_timed_corner() {
		local name="$1" target_hh="$2" target_mm="$3"
		local marker="$TMP_MARKERS_DIR/.timed_corner_done_${today}_${name}"
		local inflight="$TMP_MARKERS_DIR/.timed_corner_inflight_${name}"
		[ -f "$marker" ] && return 1
		[ -f "$inflight" ] && return 1
		local target=$((target_hh * 60 + target_mm))
		local now=$((10#$current_hour * 60 + 10#$current_min))
		local diff=$((now - target))
		[ "$diff" -lt 0 ] && diff=$((-diff))
		[ "$diff" -le 15 ] || return 1
		touch "$inflight"
		return 0
	}

	_run_timed_corner() {
		local name="$1" func="$2"
		shift 2
		if "$func" "$@"; then
			touch "$TMP_MARKERS_DIR/.timed_corner_done_${today}_${name}"
		fi
		rm -f "$TMP_MARKERS_DIR/.timed_corner_inflight_${name}"
	}

	local fired=0
	if _try_timed_corner "rakugo" 1 0; then
		_run_timed_corner "rakugo" start_radio_corner_rakugo "$GAME_NUM" "$SCORE" &
		fired=1
	fi
	if _try_timed_corner "finance" 4 0; then
		_run_timed_corner "finance" start_radio_corner_finance "$GAME_NUM" "$SCORE" &
		fired=1
	fi
	if _try_timed_corner "danger_zone" 5 0; then
		_run_timed_corner "danger_zone" start_radio_corner_danger_zone "$GAME_NUM" "$SCORE" &
		fired=1
	fi
	if _try_timed_corner "music_knowledge" 5 30; then
		_run_timed_corner "music_knowledge" start_radio_corner_music_knowledge "$GAME_NUM" "$SCORE" &
		fired=1
	fi
	if _try_timed_corner "health" 6 0; then
		_run_timed_corner "health" start_radio_corner_health "$GAME_NUM" "$SCORE" &
		fired=1
	fi
	if _try_timed_corner "breakfast" 7 0; then
		_run_timed_corner "breakfast" start_radio_corner_breakfast "$GAME_NUM" "$SCORE" &
		fired=1
	fi
	if _try_timed_corner "weather" 8 0; then
		_run_timed_corner "weather" start_radio_corner_weather "$GAME_NUM" "$SCORE" &
		fired=1
	fi
	if _try_timed_corner "wiki" 9 0; then
		_run_timed_corner "wiki" start_radio_corner_wiki "$GAME_NUM" "$SCORE" &
		fired=1
	fi
	if _try_timed_corner "sightseeing" 10 0; then
		_run_timed_corner "sightseeing" start_radio_corner_sightseeing "$GAME_NUM" "$SCORE" &
		fired=1
	fi
	if _try_timed_corner "lunch" 11 30; then
		_run_timed_corner "lunch" start_radio_corner_lunch "$GAME_NUM" "$SCORE" &
		fired=1
	fi
	if _try_timed_corner "fortune" 12 0; then
		_run_timed_corner "fortune" start_radio_corner_fortune "$GAME_NUM" "$SCORE" &
		fired=1
	fi
	if _try_timed_corner "devil_dict" 13 0; then
		_run_timed_corner "devil_dict" start_radio_corner_devil_dict "$GAME_NUM" "$SCORE" &
		fired=1
	fi
	if _try_timed_corner "ai_knowledge" 13 30; then
		_run_timed_corner "ai_knowledge" start_radio_corner_ai_knowledge "$GAME_NUM" "$SCORE" &
		fired=1
	fi
	if _try_timed_corner "soviet_quiz" 14 0; then
		_run_timed_corner "soviet_quiz" start_radio_corner_soviet_quiz "$GAME_NUM" "$SCORE" &
		fired=1
	fi
	if _try_timed_corner "market" 15 30; then
		_run_timed_corner "market" start_radio_corner_market "$GAME_NUM" "$SCORE" &
		fired=1
	fi
	if _try_timed_corner "bluegrass" 16 0; then
		_run_timed_corner "bluegrass" start_radio_corner_bluegrass "$GAME_NUM" "$SCORE" &
		fired=1
	fi
	if _try_timed_corner "dinner" 17 0; then
		_run_timed_corner "dinner" start_radio_corner_dinner "$GAME_NUM" "$SCORE" &
		fired=1
	fi
	if _try_timed_corner "redefine" 17 30; then
		_run_timed_corner "redefine" start_radio_corner_redefine "$GAME_NUM" "$SCORE" &
		fired=1
	fi
	if _try_timed_corner "soviet_lifehack" 18 0; then
		_run_timed_corner "soviet_lifehack" start_radio_corner_soviet_lifehack "$GAME_NUM" "$SCORE" &
		fired=1
	fi
	if _try_timed_corner "world_dinner" 19 0; then
		_run_timed_corner "world_dinner" start_radio_corner_world_dinner "$GAME_NUM" "$SCORE" &
		fired=1
	fi
	if _try_timed_corner "whatday" 20 0; then
		_run_timed_corner "whatday" start_radio_corner_whatday "$GAME_NUM" "$SCORE" &
		fired=1
	fi
	if _try_timed_corner "zaitech" 20 30; then
		_run_timed_corner "zaitech" start_radio_corner_zaitech "$GAME_NUM" "$SCORE" &
		fired=1
	fi
	if _try_timed_corner "deals" 21 0; then
		_run_timed_corner "deals" start_radio_corner_deals "$GAME_NUM" "$SCORE" &
		fired=1
	fi
	if _try_timed_corner "fudosan" 21 30; then
		_run_timed_corner "fudosan" start_radio_corner_fudosan "$GAME_NUM" "$SCORE" &
		fired=1
	fi
	if _try_timed_corner "survival" 22 0; then
		_run_timed_corner "survival" start_radio_corner_survival "$GAME_NUM" "$SCORE" &
		fired=1
	fi
	if _try_timed_corner "night_snack" 22 30; then
		_run_timed_corner "night_snack" start_radio_corner_night_snack "$GAME_NUM" "$SCORE" &
		fired=1
	fi
	if _try_timed_corner "local_japan" 23 30; then
		_run_timed_corner "local_japan" start_radio_corner_local_japan "$GAME_NUM" "$SCORE" &
		fired=1
	fi

	# バックグラウンドジョブを切り離し（スクリプト終了後も継続）
	disown -a 2>/dev/null
	echo "$fired"
}

_check_timed_radio_corners
