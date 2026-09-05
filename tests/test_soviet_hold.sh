#!/usr/bin/env bash
set -eo pipefail

ROOT=$(cd "$(dirname "$0")/.." && pwd)
cd "$ROOT"

log() { :; }

source core/config.sh
source core/game_state.sh
source eloop.sh

tmp_dir=$(mktemp -d)
trap 'rm -rf "$tmp_dir"' EXIT

export TMP_STATE_DIR="$tmp_dir/state"
mkdir -p "$TMP_STATE_DIR" "$tmp_dir/markers"
export TMP_MARKERS_DIR="$tmp_dir/markers"
export SOVIET_HOLD_SEC=600
export COMMANDS="$tmp_dir/commands"
export GAME_STATE="$tmp_dir/game_state.json"
echo "sentinel" >"$COMMANDS"

pass=0
check() { pass=$((pass + 1)); }

# 1. hold ファイルなし → 非保持
! _soviet_hold_active
check

# 2. 新規 hold ファイル → 保持中・残り時間あり
date +%s >"$TMP_STATE_DIR/.soviet_hold_since"
_soviet_hold_active
[ "$(_soviet_hold_remaining)" -gt 0 ]
check

# 3. send_retry は保持中は何も送らず rc=0
send_retry
[ "$(cat "$COMMANDS")" = "sentinel" ]
check

# 4. prepare_next_game / play_one_game は保持中スキップ
prepare_next_game
LAST_SOVIET="true"
play_one_game
[ "$LAST_SOVIET" = "false" ]
check

# 5. 期限切れ hold ファイル → 非保持＋掃除
echo $(( $(date +%s) - 601 )) >"$TMP_STATE_DIR/.soviet_hold_since"
! _soviet_hold_active
[ ! -f "$TMP_STATE_DIR/.soviet_hold_since" ]
check

# 6. 不正な hold ファイル → 非保持＋掃除
echo "broken" >"$TMP_STATE_DIR/.soviet_hold_since"
! _soviet_hold_active
[ ! -f "$TMP_STATE_DIR/.soviet_hold_since" ]
check

# 7. SOVIET_HOLD_SEC=0 → 保持しない
date +%s >"$TMP_STATE_DIR/.soviet_hold_since"
SOVIET_HOLD_SEC=0
! _soviet_hold_active
[ ! -f "$TMP_STATE_DIR/.soviet_hold_since" ]
SOVIET_HOLD_SEC=600
check

# 8. handle_soviet_celebration の重複抑止: 初回だけ履歴追記、2回目はスキップ
append_count=0
_append_celebration_history() { append_count=$((append_count + 1)); }
export SOVIET_CELEBRATION_ENABLED=0
rm -f "$TMP_STATE_DIR/.soviet_hold_since"
handle_soviet_celebration 5312 178 49106
[ "$append_count" -eq 1 ]
[ -f "$TMP_STATE_DIR/.soviet_hold_since" ]
handle_soviet_celebration 5312 1 49107
[ "$append_count" -eq 1 ]
check

echo "soviet hold tests: OK ($pass groups)"
