# broadcast/radio_themes.sh - テーマ選択, マッチング, 使用済みマーク


#=== ラジオトーク: テーマ選択 ===

_write_radio_theme_pick_status() {
	local status="$1" filter_category="$2" source_count="$3" deduped_count="$4" available_count="$5" history_reset="$6" used_default_fallback="$7" selected_category="$8" selected_theme="$9"
	python3 - "$RADIO_THEME_PICK_STATUS_FILE" "$status" "$filter_category" "$source_count" "$deduped_count" "$available_count" "$history_reset" "$used_default_fallback" "$selected_category" "$selected_theme" "${10:-false}" <<'PY' >/dev/null 2>&1
import json
import sys
from datetime import datetime, timezone

out_file, status, filter_category, source_count, deduped_count, available_count, history_reset, used_default_fallback, selected_category, selected_theme = sys.argv[1:11]

def to_int(value: str) -> int:
    try:
        return int(value)
    except Exception:
        return 0

payload = {
    "status": status,
    "filter_category": filter_category,
    "source_theme_count": to_int(source_count),
    "deduped_theme_count": to_int(deduped_count),
    "available_theme_count": to_int(available_count),
    "history_reset": history_reset == "true",
    "history_exhausted": sys.argv[11] == "true",
    "used_default_fallback": used_default_fallback == "true",
    "selected_category": selected_category,
    "selected_theme": selected_theme,
    "updated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
}

with open(out_file, "w", encoding="utf-8") as f:
    json.dump(payload, f, ensure_ascii=False, indent=2)
    f.write("\n")
PY
}

_radio_theme_key_from_body() {
	python3 "$ELOOP_LIB_DIR/lib/radio_theme_history.py" key --body="$1"
}

_radio_theme_recent_match_mode() {
	local theme_body="$1"
	local history_bodies_file="${2:-$PAST_RADIO_THEME_BODIES}"
	local history_keys_file="${3:-$PAST_RADIO_THEME_KEYS}"
	python3 "$ELOOP_LIB_DIR/lib/radio_theme_history.py" match --body="$theme_body" \
		--catalog "$ELOOP_LIB_DIR/data/radio_themes.txt" \
		--bodies "$history_bodies_file" --keys "$history_keys_file" \
		--keep "${PAST_RADIO_THEME_HISTORY_KEEP:-400}"
}

_radio_mark_theme_used() {
	local theme_body="$1"
	local past_keys_file="${PAST_RADIO_THEME_KEYS:-$TMP_HISTORY_DIR/.past_radio_themes.txt}"
	local past_bodies_file="${PAST_RADIO_THEME_BODIES:-$TMP_HISTORY_DIR/past_radio_theme_bodies.txt}"
	local theme_key=""
	theme_key=$(_radio_theme_key_from_body "$theme_body")
	[ -n "$theme_key" ] && echo "$theme_key" >>"$past_keys_file"
	echo "$theme_body" >>"$past_bodies_file"
	tail -"${PAST_RADIO_THEME_HISTORY_KEEP:-400}" "$past_keys_file" >"${past_keys_file}.tmp" && mv "${past_keys_file}.tmp" "$past_keys_file"
	tail -"${PAST_RADIO_THEME_HISTORY_KEEP:-400}" "$past_bodies_file" >"${past_bodies_file}.tmp" && mv "${past_bodies_file}.tmp" "$past_bodies_file"
}

_pick_radio_theme() {
	local filter_category="${1:-}"
	local theme_file="$ELOOP_LIB_DIR/data/radio_themes.txt"
	local themes=()
	local used_default_fallback=false
	local history_reset=false
	local history_exhausted=false
	# One catalog process and one matching process, not one fork per candidate.
	while IFS= read -r _line; do
		[ -n "$_line" ] && themes+=("$_line")
	done < <(python3 "$ELOOP_LIB_DIR/lib/radio_theme_history.py" catalog \
		--catalog "$theme_file" --category "$filter_category")
	if [ ${#themes[@]} -eq 0 ]; then
		used_default_fallback=true
		themes=("世界の料理と文化の話。各国の食卓と暮らしの違いを深掘りして")
	fi

	local past_themes_file="${PAST_RADIO_THEME_KEYS:-$TMP_HISTORY_DIR/.past_radio_themes.txt}"
	local past_theme_bodies_file="${PAST_RADIO_THEME_BODIES:-$TMP_HISTORY_DIR/past_radio_theme_bodies.txt}"
	local available_themes=()
	if [ ! -f "$past_theme_bodies_file" ] && [ -f "$past_themes_file" ]; then
		cp "$past_themes_file" "$past_theme_bodies_file" 2>/dev/null || cat "$past_themes_file" >"$past_theme_bodies_file" 2>/dev/null || true
	fi
	local t
	while IFS= read -r t; do
		[ -n "$t" ] && available_themes+=("$t")
	done < <(printf '%s\n' "${themes[@]}" | python3 "$ELOOP_LIB_DIR/lib/radio_theme_history.py" available \
		--catalog "$theme_file" --bodies "$past_theme_bodies_file" --keys "$past_themes_file" \
		--keep "${PAST_RADIO_THEME_HISTORY_KEEP:-400}")
	if [ ${#available_themes[@]} -eq 0 ]; then
		history_exhausted=true
		# Retain all categories' history and only reuse the least recent matches.
		while IFS= read -r t; do
			[ -n "$t" ] && available_themes+=("$t")
		done < <(printf '%s\n' "${themes[@]}" | python3 "$ELOOP_LIB_DIR/lib/radio_theme_history.py" oldest \
			--catalog "$theme_file" --bodies "$past_theme_bodies_file" --keys "$past_themes_file" \
			--keep "${PAST_RADIO_THEME_HISTORY_KEEP:-400}")
		[ ${#available_themes[@]} -gt 0 ] || return 1
	fi
	local theme="${available_themes[$((RANDOM % ${#available_themes[@]}))]}"
	local theme_body="$theme"
	local theme_cat=""
	if [[ "$theme" == \[soviet\]\ * ]]; then
		theme_cat="soviet"
		theme_body="${theme#\[soviet\] }"
	fi
	_radio_mark_theme_used "$theme_body"
	_write_radio_theme_pick_status \
		"ok" \
		"$filter_category" \
		"${#themes[@]}" \
		"${#themes[@]}" \
		"${#available_themes[@]}" \
		"$history_reset" \
		"$used_default_fallback" \
		"$theme_cat" \
		"$theme_body" \
		"$history_exhausted"
	# カテゴリ付きの場合はタブ区切りで返す: [soviet]\tテーマ本文
	if [ -n "$theme_cat" ]; then
		printf '[%s]\t%s\n' "$theme_cat" "$theme_body"
	else
		echo "$theme_body"
	fi
}

#=== lib/eloop_radio.sh から移行した関数 ===


#=== ラジオトーク: テーマ選択 ===

_radio_fetch_theme_grounding_context() {
	local corner_name="$1" theme="$2"
	[ "${RADIO_WEB_GROUNDING_ENABLED:-1}" = "1" ] || return 0
	[ -n "$theme" ] || return 0

	local grounding_context="" prompt_seed=""
	if typeset -f _radio_fetch_web_grounding >/dev/null 2>&1; then
		prompt_seed=$(printf '【今回の脱線テーマ指定】\n%s\n' "$theme")
		grounding_context=$(_radio_fetch_web_grounding "$corner_name" "$prompt_seed")
	fi

	if [ -z "$grounding_context" ]; then
		grounding_context=$(python3 "$ELOOP_LIB_DIR/fetch_radio_grounding.py" \
			--corner "$corner_name" \
			--query "$theme" \
			--ttl-sec "${RADIO_WEB_GROUNDING_TTL_SEC:-21600}" \
			--max-sources "${RADIO_WEB_GROUNDING_MAX_SOURCES:-3}" \
			--cache-dir "$RADIO_WEB_GROUNDING_CACHE_DIR" 2>/dev/null || true)
		if [ -n "$grounding_context" ]; then
			log "[RADIO:${corner_name}] theme grounding取得成功(fallback)" >&2
		fi
	fi

	printf '%s' "$grounding_context"
}

_pick_soviet_theme() {
	local soviet_themes=()
	while IFS= read -r _line; do
		[ -n "$_line" ] && soviet_themes+=("$_line")
	done < "$ELOOP_LIB_DIR/data/radio_soviet_themes.txt"
	local past_soviet_file="$TMP_HISTORY_DIR/.past_soviet_themes.txt"
	local available_soviet=()
	local past_soviet_list=""
	[ -f "$past_soviet_file" ] && past_soviet_list=$(cat "$past_soviet_file")
	for st in "${soviet_themes[@]}"; do
		[ -z "$st" ] && continue
		local st_key="${st%%。*}"
		[ "$st_key" = "$st" ] && st_key="${st%%を深掘り*}"
		if ! echo "$past_soviet_list" | grep -qF "$st_key"; then
			available_soviet+=("$st")
		fi
	done
	if [ ${#available_soviet[@]} -eq 0 ]; then
		available_soviet=("${soviet_themes[@]}")
		>"$past_soviet_file"
	fi
	local soviet_theme="${available_soviet[$((RANDOM % ${#available_soviet[@]}))]}"
	local soviet_key="${soviet_theme%%。*}"
	[ "$soviet_key" = "$soviet_theme" ] && soviet_key="${soviet_theme%%を深掘り*}"
	echo "$soviet_key" >>"$past_soviet_file"
	tail -"${PAST_SOVIET_TOPICS_KEEP:-300}" "$past_soviet_file" >"${past_soviet_file}.tmp" && mv "${past_soviet_file}.tmp" "$past_soviet_file"
	echo "$soviet_theme"
}
