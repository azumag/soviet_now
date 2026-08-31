#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

cd "$ROOT"
export ELOOP_LIB_DIR="$ROOT"
source broadcast/comment.sh

export previous_comments_context="SHARED_TIMELINE_MARKER: aliceとbobの会話"
export recent_spoken_comment_context="SPOKEN_REPLY_MARKER"
export viewer_memory_context="PERSONAL_MEMORY_MARKER: alice本人の過去"
export current_time="12:00"
export time_period="午後"
export comment_batch_context="今回の共通文脈"
export strategy_advice_candidates="（なし）"
export comment_advice_candidates="（なし）"
export codex_advice_candidates="（なし）"
export comment_advice_context="（なし）"
export comment_followup_hints="（なし）"
export past_topics="（なし）"
export celebration_history_context="（なし）"
export comment_thumbnail_ocr_context="（なし）"
export game_state_context="（なし）"
export comment_ops_context="（なし）"
export _comment_persona="テスト用ペルソナ"
export _comment_ui_memo="（なし）"
export _comment_channel_intro="（なし）"
export sing_reference="（なし）"
export _prediction_cycle_games="0"

out="$TMP/prompt.txt"
_build_category_prompt default "alice: 続きです" '[]' "$out"

grep -qF "SHARED_TIMELINE_MARKER" "$out"
grep -qF "PERSONAL_MEMORY_MARKER" "$out"
grep -qF "additive context, not a replacement" "$out"

printf 'comment_viewer_memory_prompt: shared timeline + additive personal memory passed\n'
