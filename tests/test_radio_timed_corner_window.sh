#!/usr/bin/env bash
# Timed radio slots may be caught up after their target, but must never fire early.
set -euo pipefail
ROOT=$(cd "$(dirname "$0")/.." && pwd)
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
sed -n '/^schedule_nonessential_audio_jobs()/,/^}/p' "$ROOT/broadcast/scheduler.sh" >"$TMP/schedule.sh"

run_case() {
  local minute="$1" expected="$2" label="$3"
  local case_dir="$TMP/case_$minute"
  mkdir -p "$case_dir/markers" "$case_dir/state"
  (
    cd "$case_dir"
    TMP_MARKERS_DIR="$case_dir/markers"
    TMP_STATE_DIR="$case_dir/state"
    GAME_COUNT_FILE="$case_dir/game_count.txt"
    printf '100\n' >"$GAME_COUNT_FILE"
    TEST_MINUTE="$minute"
    date() {
      case "${1:-}" in
        +%H) printf '01\n' ;;
        +%M) printf '%s\n' "$TEST_MINUTE" ;;
        +%Y%m%d) printf '20260907\n' ;;
        -d) printf '20260906\n' ;;
        *) command date "$@" ;;
      esac
    }
    log() { printf '%s\n' "$*" >>"$case_dir/log"; }
    _last_score() { printf '0\n'; }
    get_comment_backlog_counts() { printf '0 0\n'; }
    is_comment_backlog_high() { return 1; }
    _radio_generation_blocked_by_backpressure() { return 1; }
    _radio_generation_blocked_by_peak_hour_queue() { return 1; }
    _try_game_corner() { return 0; }
    fetch_and_play_news() { log 'CALL news'; }
    _run_jiji_corner_guarded() { log 'CALL jiji'; }
    start_random_radio_corner() { log 'CALL theme'; }
    for fn in finance danger_zone music_knowledge health rakugo breakfast weather wiki sightseeing lunch fortune devil_dict ai_knowledge soviet_quiz market bluegrass dinner redefine soviet_lifehack world_dinner whatday zaitech deals fudosan survival night_snack local_japan; do
      eval "start_radio_corner_${fn}() { log 'CALL random'; }"
    done
    # shellcheck disable=SC1090
    . "$TMP/schedule.sh"
    schedule_nonessential_audio_jobs 100 0
    wait
  )
  local actual
  actual=$(grep '^CALL ' "$case_dir/log" 2>/dev/null | sort -u | paste -sd, - || true)
  if [ "$actual" != "$expected" ]; then
    echo "FAIL: $label expected=$expected actual=${actual:-none}" >&2
    cat "$case_dir/log" >&2 2>/dev/null || true
    return 1
  fi
  echo "PASS: $label"
}

# At 01:00 only the 01:00 news slot is due. 01:05 and 01:15 are future work.
run_case 00 'CALL news' 'future timed slots do not fire early'
# A missed 01:05 slot is still eligible seven minutes later.
run_case 12 'CALL news,CALL random,CALL theme' 'late slots remain catch-up eligible'

# At 01:21 only the 01:15 jiji slot is still inside the late catch-up window.
run_case 21 'CALL jiji' 'expired slots are not replayed indefinitely'


run_dead_owner_recovery_case() {
  local case_dir="$TMP/dead_owner"
  mkdir -p "$case_dir/markers/.timed_corner_inflight_20260907_news_01_00" "$case_dir/state"
  printf '999999\n' >"$case_dir/markers/.timed_corner_inflight_20260907_news_01_00/owner"
  (
    cd "$case_dir"
    TMP_MARKERS_DIR="$case_dir/markers"
    TMP_STATE_DIR="$case_dir/state"
    GAME_COUNT_FILE="$case_dir/game_count.txt"
    printf '100\n' >"$GAME_COUNT_FILE"
    date() {
      case "${1:-}" in
        +%H) printf '01\n' ;;
        +%M) printf '00\n' ;;
        +%Y%m%d) printf '20260907\n' ;;
        -d) printf '20260906\n' ;;
        *) command date "$@" ;;
      esac
    }
    log() { printf '%s\n' "$*" >>"$case_dir/log"; }
    _last_score() { printf '0\n'; }
    get_comment_backlog_counts() { printf '0 0\n'; }
    is_comment_backlog_high() { return 1; }
    _radio_generation_blocked_by_backpressure() { return 1; }
    _radio_generation_blocked_by_peak_hour_queue() { return 1; }
    _try_game_corner() { return 0; }
    fetch_and_play_news() { log 'CALL news'; }
    _run_jiji_corner_guarded() { :; }
    start_random_radio_corner() { :; }
    for fn in finance danger_zone music_knowledge health rakugo breakfast weather wiki sightseeing lunch fortune devil_dict ai_knowledge soviet_quiz market bluegrass dinner redefine soviet_lifehack world_dinner whatday zaitech deals fudosan survival night_snack local_japan; do
      eval "start_radio_corner_${fn}() { :; }"
    done
    # shellcheck disable=SC1090
    . "$TMP/schedule.sh"
    schedule_nonessential_audio_jobs 100 0
    wait
  )
  grep -q '^CALL news$' "$case_dir/log" || {
    echo "FAIL: dead timed-corner owner did not recover" >&2
    return 1
  }
  [ ! -e "$case_dir/markers/.timed_corner_inflight_20260907_news_01_00" ] || {
    echo "FAIL: recovered inflight marker remained" >&2
    return 1
  }
  echo "PASS: dead timed-corner owner is reclaimed"
}

run_live_owner_preserved_case() {
  local case_dir="$TMP/live_owner"
  mkdir -p "$case_dir/markers/.timed_corner_inflight_20260907_news_01_00" "$case_dir/state"
  printf '%s\n' "$$" >"$case_dir/markers/.timed_corner_inflight_20260907_news_01_00/owner"
  (
    cd "$case_dir"
    TMP_MARKERS_DIR="$case_dir/markers"
    TMP_STATE_DIR="$case_dir/state"
    GAME_COUNT_FILE="$case_dir/game_count.txt"
    printf '100\n' >"$GAME_COUNT_FILE"
    date() {
      case "${1:-}" in
        +%H) printf '01\n' ;;
        +%M) printf '00\n' ;;
        +%Y%m%d) printf '20260907\n' ;;
        -d) printf '20260906\n' ;;
        *) command date "$@" ;;
      esac
    }
    log() { printf '%s\n' "$*" >>"$case_dir/log"; }
    _last_score() { printf '0\n'; }
    get_comment_backlog_counts() { printf '0 0\n'; }
    is_comment_backlog_high() { return 1; }
    _radio_generation_blocked_by_backpressure() { return 1; }
    _radio_generation_blocked_by_peak_hour_queue() { return 1; }
    _try_game_corner() { return 0; }
    fetch_and_play_news() { log 'CALL news'; }
    _run_jiji_corner_guarded() { :; }
    start_random_radio_corner() { :; }
    for fn in finance danger_zone music_knowledge health rakugo breakfast weather wiki sightseeing lunch fortune devil_dict ai_knowledge soviet_quiz market bluegrass dinner redefine soviet_lifehack world_dinner whatday zaitech deals fudosan survival night_snack local_japan; do
      eval "start_radio_corner_${fn}() { :; }"
    done
    # shellcheck disable=SC1090
    . "$TMP/schedule.sh"
    schedule_nonessential_audio_jobs 100 0
    wait
  )
  [ ! -s "$case_dir/log" ] || {
    echo "FAIL: live timed-corner owner was duplicated" >&2
    cat "$case_dir/log" >&2
    return 1
  }
  [ -d "$case_dir/markers/.timed_corner_inflight_20260907_news_01_00" ] || {
    echo "FAIL: live inflight marker was removed" >&2
    return 1
  }
  echo "PASS: live timed-corner owner is preserved"
}

run_previous_day_cleanup_case() {
  local case_dir="$TMP/previous_day"
  mkdir -p "$case_dir/markers/.timed_corner_inflight_20260906_news_23_30" "$case_dir/state"
  (
    cd "$case_dir"
    TMP_MARKERS_DIR="$case_dir/markers"
    TMP_STATE_DIR="$case_dir/state"
    GAME_COUNT_FILE="$case_dir/game_count.txt"
    printf '100\n' >"$GAME_COUNT_FILE"
    date() {
      case "${1:-}" in
        +%H) printf '01\n' ;;
        +%M) printf '21\n' ;;
        +%Y%m%d) printf '20260907\n' ;;
        -d) printf '20260906\n' ;;
        *) command date "$@" ;;
      esac
    }
    log() { :; }
    _last_score() { printf '0\n'; }
    get_comment_backlog_counts() { printf '0 0\n'; }
    is_comment_backlog_high() { return 1; }
    _radio_generation_blocked_by_backpressure() { return 1; }
    _radio_generation_blocked_by_peak_hour_queue() { return 1; }
    _try_game_corner() { return 1; }
    fetch_and_play_news() { :; }
    _run_jiji_corner_guarded() { :; }
    start_random_radio_corner() { :; }
    for fn in finance danger_zone music_knowledge health rakugo breakfast weather wiki sightseeing lunch fortune devil_dict ai_knowledge soviet_quiz market bluegrass dinner redefine soviet_lifehack world_dinner whatday zaitech deals fudosan survival night_snack local_japan; do
      eval "start_radio_corner_${fn}() { :; }"
    done
    # shellcheck disable=SC1090
    . "$TMP/schedule.sh"
    schedule_nonessential_audio_jobs 100 0
    wait
  )
  [ ! -e "$case_dir/markers/.timed_corner_inflight_20260906_news_23_30" ] || {
    echo "FAIL: previous-day inflight directory was not cleaned" >&2
    return 1
  }
  echo "PASS: previous-day inflight directory is cleaned"
}

run_dead_owner_recovery_case
run_live_owner_preserved_case
run_previous_day_cleanup_case
