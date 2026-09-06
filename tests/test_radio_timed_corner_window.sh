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
