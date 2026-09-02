#!/usr/bin/env bash
set -eo pipefail

ROOT=$(cd "$(dirname "$0")/.." && pwd)
cd "$ROOT"

source core/config.sh
source broadcast/comment.sh
set -u

tmp_dir=$(mktemp -d)
trap 'rm -rf "$tmp_dir"' EXIT

RUSSIA_CREATION_HISTORY_FILE="$tmp_dir/russia.tsv"
SOVIET_CREATION_HISTORY_FILE="$tmp_dir/soviet.tsv"
SOVIET_CREATION_ARCHIVE_FILE="$tmp_dir/archive.tsv"
COMMENT_CELEBRATION_HISTORY_ITEMS=5

printf '%s\t%s\t%s\t%s\t%s\n' \
  '2026-06-03T06:26:52+09:00' '2026-06-03 06:26 JST' '29557' '6527' '' \
  >"$SOVIET_CREATION_ARCHIVE_FILE"

context=$(_build_comment_celebration_history_context)
grep -Fq 'ソ連建国: 累計1回' <<<"$context"
grep -Fq '2026-06-03 06:26 JST / Game#29557 / score=6527' <<<"$context"
! grep -Fq 'ソ連建国:'$'\n''- まだ履歴なし' <<<"$context"

# Runtime history can contain the archived achievement again. It must not
# inflate the all-time count, while a genuinely new achievement must be added.
printf '%s\t%s\t%s\t%s\t%s\n' \
  '2026-06-03T06:26:52+09:00' '2026-06-03 06:26 JST' '29557' '6527' '178' \
  '2026-09-02T20:00:00+09:00' '2026-09-02 20:00 JST' '40000' '7000' '190' \
  >"$SOVIET_CREATION_HISTORY_FILE"

context=$(_build_comment_celebration_history_context)
grep -Fq 'ソ連建国: 累計2回' <<<"$context"
[ "$(grep -Fc 'Game#29557' <<<"$context")" -eq 1 ]
grep -Fq 'Game#40000' <<<"$context"

echo 'comment celebration history tests: OK'
