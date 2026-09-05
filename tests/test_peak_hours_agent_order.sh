#!/usr/bin/env bash
# ピーク時間帯 (既定 JST 10-13, 15-19) のエージェント候補順序入替えを検証する。
# 実コードをそのまま抽出して source する（core/helpers.sh の判定/並べ替え関数、
# core/config.sh の既定値、呼び出し箇所の結線をテスト対象にする）。
set -u

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
HELPERS_SRC="$ROOT/core/helpers.sh"
CONFIG_SRC="$ROOT/core/config.sh"
RADIO_SRC="$ROOT/broadcast/radio_engine.sh"
COMMENT_SRC="$ROOT/broadcast/comment.sh"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

FAIL=0
ok() { echo "ok - $1"; }
not_ok() { echo "not ok - $1"; FAIL=1; }

# --- 実関数を抽出して source ---
sed -n '/^_peak_hours_to_minutes()/,/^}/p'    "$HELPERS_SRC" >"$TMP/fn_min.sh"
sed -n '/^_is_peak_hours()/,/^}/p'            "$HELPERS_SRC" >"$TMP/fn_peak.sh"
sed -n '/^_peak_priority_agent_list()/,/^}/p' "$HELPERS_SRC" >"$TMP/fn_order.sh"

log() { printf 'LOG: %s\n' "$*" >>"$TMP/log.txt"; }
: >"$TMP/log.txt"
. "$TMP/fn_min.sh"
. "$TMP/fn_peak.sh"
. "$TMP/fn_order.sh"

# ============================================================
# A. 時刻トークン変換 _peak_hours_to_minutes
# ============================================================
check_min() {
	local input="$1" expect="$2" got
	got=$(_peak_hours_to_minutes "$input" 2>/dev/null)
	[ "$got" = "$expect" ] && ok "minutes($input)=$expect" || not_ok "minutes($input)=$expect (got '$got')"
}
check_min "10" 600
check_min "10:30" 630
check_min "1030" 630
check_min "0930" 570
check_min "930" 570
check_min "24" 1440
check_min "00" 0

check_min_invalid() {
	local input="$1" got rc
	got=$(_peak_hours_to_minutes "$input" 2>/dev/null)
	rc=$?
	if [ "$rc" -ne 0 ] && [ -z "$got" ]; then
		ok "minutes($input) invalid rejected"
	else
		not_ok "minutes($input) invalid rejected (rc=$rc got='$got')"
	fi
}
check_min_invalid ""
check_min_invalid "abc"
check_min_invalid "25"
check_min_invalid "9:60"
check_min_invalid "10:"
check_min_invalid "-1"

# ============================================================
# B. 境界値 _is_peak_hours <HHMM>（既定ウィンドウ 10-13,15-19）
# ============================================================
unset PEAK_HOURS_WINDOWS PEAK_HOURS_TEST_NOW

check_offpeak() {
	local hhmm="$1"
	if _is_peak_hours "$hhmm"; then
		not_ok "is_peak($hhmm) expected off-peak"
	else
		ok "is_peak($hhmm) off-peak"
	fi
}
check_peak() {
	local hhmm="$1"
	if _is_peak_hours "$hhmm"; then
		ok "is_peak($hhmm) peak"
	else
		not_ok "is_peak($hhmm) expected peak"
	fi
}

for h in 0000 0959 1300 1301 1459 1900 2359; do check_offpeak "$h"; done
for h in 1000 1001 1259 1500 1830 1859; do check_peak "$h"; done

# ============================================================
# C. 時刻注入の優先順位
# ============================================================
PEAK_HOURS_TEST_NOW=1100
if _is_peak_hours; then
	ok "now-override alone -> peak"
else
	not_ok "now-override alone -> peak"
fi
if _is_peak_hours 0900; then
	not_ok "explicit arg beats override -> off-peak"
else
	ok "explicit arg beats override -> off-peak"
fi
unset PEAK_HOURS_TEST_NOW

# real `date` スタブ (TZ強制取得パスの疎通確認)
date() { printf '1600'; }
if _is_peak_hours; then
	ok "date() stub (1600) -> peak"
else
	not_ok "date() stub (1600) -> peak"
fi
unset -f date

# ============================================================
# D. ウィンドウ指定のパース
# ============================================================
PEAK_HOURS_WINDOWS="15-19"
if _is_peak_hours 1100; then not_ok "single-window 1100 off-peak"; else ok "single-window 1100 off-peak"; fi
if _is_peak_hours 1600; then ok "single-window 1600 peak"; else not_ok "single-window 1600 peak"; fi

PEAK_HOURS_WINDOWS="22-02"
if _is_peak_hours 2300; then ok "overnight 2300 peak"; else not_ok "overnight 2300 peak"; fi
if _is_peak_hours 0100; then ok "overnight 0100 peak"; else not_ok "overnight 0100 peak"; fi
if _is_peak_hours 0300; then not_ok "overnight 0300 off-peak"; else ok "overnight 0300 off-peak"; fi

PEAK_HOURS_WINDOWS="10:30-11:00"
if _is_peak_hours 1029; then not_ok "hhmm-window 1029 off-peak"; else ok "hhmm-window 1029 off-peak"; fi
if _is_peak_hours 1030; then ok "hhmm-window 1030 peak"; else not_ok "hhmm-window 1030 peak"; fi
if _is_peak_hours 1100; then not_ok "hhmm-window 1100 off-peak"; else ok "hhmm-window 1100 off-peak"; fi

PEAK_HOURS_WINDOWS=" 10-13 , 15-19 "
if _is_peak_hours 1100; then ok "whitespace windows 1100 peak"; else not_ok "whitespace windows 1100 peak"; fi

PEAK_HOURS_WINDOWS="abc,10-13"
if _is_peak_hours 1100; then ok "malformed token skipped, 1100 peak"; else not_ok "malformed token skipped, 1100 peak"; fi
if _is_peak_hours 0900; then not_ok "malformed token skipped, 0900 off-peak"; else ok "malformed token skipped, 0900 off-peak"; fi

PEAK_HOURS_WINDOWS="garbage"
if _is_peak_hours 1100; then not_ok "fully invalid windows -> never peak"; else ok "fully invalid windows -> never peak"; fi

# 明示的な空文字 = ウィンドウなし = 常に非ピーク（unset時のみ既定値が使われる `-` 展開の回帰確認）
PEAK_HOURS_WINDOWS=""
if _is_peak_hours 1100; then not_ok "explicit empty windows -> disabled (never peak)"; else ok "explicit empty windows -> disabled (never peak)"; fi
if _is_peak_hours 1600; then not_ok "explicit empty windows -> disabled (never peak, 2nd default window)"; else ok "explicit empty windows -> disabled (never peak, 2nd default window)"; fi

unset PEAK_HOURS_WINDOWS
if _is_peak_hours 1100; then ok "unset windows -> falls back to default (peak)"; else not_ok "unset windows -> falls back to default (peak)"; fi

# ============================================================
# E. 並べ替え _peak_priority_agent_list
# ============================================================
unset PEAK_HOURS_WINDOWS PEAK_HOURS_AGENT_SWAP_ENABLED PEAK_HOURS_PRIORITY_AGENT

PEAK_HOURS_TEST_NOW=1100
got=$(_peak_priority_agent_list "codex:deepseek-v4-flash,codex:minimax-m3")
[ "$got" = "codex:minimax-m3,codex:deepseek-v4-flash" ] && ok "peak reorder: deepseek,minimax -> minimax,deepseek" || not_ok "peak reorder: deepseek,minimax -> minimax,deepseek (got '$got')"

PEAK_HOURS_TEST_NOW=1400
got=$(_peak_priority_agent_list "codex:deepseek-v4-flash,codex:minimax-m3")
[ "$got" = "codex:deepseek-v4-flash,codex:minimax-m3" ] && ok "off-peak: no reorder" || not_ok "off-peak: no reorder (got '$got')"

PEAK_HOURS_TEST_NOW=1100
in_list="codex:deepseek-v4-flash,codex:minimax-m3"
out_list=$(_peak_priority_agent_list "$in_list")
in_sorted=$(printf '%s' "$in_list" | tr ',' '\n' | sort | tr '\n' ',')
out_sorted=$(printf '%s' "$out_list" | tr ',' '\n' | sort | tr '\n' ',')
[ "$in_sorted" = "$out_sorted" ] && ok "peak reorder: no candidate lost" || not_ok "peak reorder: no candidate lost (in='$in_sorted' out='$out_sorted')"

got=$(_peak_priority_agent_list "a,codex:minimax-m3,b,c")
[ "$got" = "codex:minimax-m3,a,b,c" ] && ok "peak reorder: relative order preserved" || not_ok "peak reorder: relative order preserved (got '$got')"

got=$(_peak_priority_agent_list "codex:minimax-m3,a,b")
[ "$got" = "codex:minimax-m3,a,b" ] && ok "peak reorder: already first -> unchanged" || not_ok "peak reorder: already first -> unchanged (got '$got')"

got=$(_peak_priority_agent_list "a,b")
[ "$got" = "a,b" ] && ok "peak reorder: preferred absent -> unchanged" || not_ok "peak reorder: preferred absent -> unchanged (got '$got')"

got=$(_peak_priority_agent_list "a , codex:minimax-m3")
[ "$got" = "codex:minimax-m3,a" ] && ok "peak reorder: whitespace normalized" || not_ok "peak reorder: whitespace normalized (got '$got')"

got=$(_peak_priority_agent_list "")
[ "$got" = "" ] && ok "peak reorder: empty input -> empty output" || not_ok "peak reorder: empty input -> empty output (got '$got')"

PEAK_HOURS_AGENT_SWAP_ENABLED=0
got=$(_peak_priority_agent_list "codex:deepseek-v4-flash,codex:minimax-m3")
[ "$got" = "codex:deepseek-v4-flash,codex:minimax-m3" ] && ok "swap disabled -> unchanged even at peak" || not_ok "swap disabled -> unchanged even at peak (got '$got')"
unset PEAK_HOURS_AGENT_SWAP_ENABLED

got=$(_peak_priority_agent_list "a,b,c" "b")
[ "$got" = "b,a,c" ] && ok "explicit preferred arg overrides default" || not_ok "explicit preferred arg overrides default (got '$got')"

PEAK_HOURS_PRIORITY_AGENT="c"
got=$(_peak_priority_agent_list "a,b,c")
[ "$got" = "c,a,b" ] && ok "PEAK_HOURS_PRIORITY_AGENT env overrides default" || not_ok "PEAK_HOURS_PRIORITY_AGENT env overrides default (got '$got')"
unset PEAK_HOURS_PRIORITY_AGENT

# 複数優先順位（PEAK_HOURS_AGENT_PREFERENCE）: minimax > openrouter/free > local > 残り
PEAK_HOURS_AGENT_PREFERENCE="codex:minimax-m3,codex:openrouter/free,local"
got=$(_peak_priority_agent_list "codex:deepseek-v4-flash,codex:openrouter/free,local,codex:deepseek-v4-flash-free,codex:minimax-m3")
[ "$got" = "codex:minimax-m3,codex:openrouter/free,local,codex:deepseek-v4-flash,codex:deepseek-v4-flash-free" ] \
	&& ok "peak multi-preference: minimax>openrouter>local then rest original order" \
	|| not_ok "peak multi-preference order (got '$got')"
got=$(_peak_priority_agent_list "codex:deepseek-v4-flash,codex:deepseek-v4-flash-free")
[ "$got" = "codex:deepseek-v4-flash,codex:deepseek-v4-flash-free" ] \
	&& ok "peak multi-preference: no preferred -> unchanged" \
	|| not_ok "peak multi-preference no preferred (got '$got')"
unset PEAK_HOURS_AGENT_PREFERENCE

before_log_lines=$(wc -l <"$TMP/log.txt" 2>/dev/null || echo 0)
_peak_priority_agent_list "codex:deepseek-v4-flash,codex:minimax-m3" >/dev/null
after_log_lines=$(wc -l <"$TMP/log.txt" 2>/dev/null || echo 0)
[ "$before_log_lines" = "$after_log_lines" ] && ok "no log() side-channel output during reorder" || not_ok "no log() side-channel output during reorder"

unset PEAK_HOURS_TEST_NOW

# ============================================================
# F. 既定値の結線（config.sh 統合）
# ============================================================
(
	set +u
	cd "$TMP" || exit 1
	unset PEAK_HOURS_WINDOWS PEAK_HOURS_PRIORITY_AGENT PEAK_HOURS_AGENT_SWAP_ENABLED RADIO_AGENTS COMMENT_AGENTS
	ELOOP_LIB_DIR="$TMP"
	# shellcheck disable=SC1090
	. "$CONFIG_SRC" >/dev/null 2>&1
	printf '%s|%s|%s|%s|%s\n' \
		"$PEAK_HOURS_WINDOWS" "$PEAK_HOURS_PRIORITY_AGENT" "$PEAK_HOURS_AGENT_SWAP_ENABLED" \
		"$RADIO_AGENTS" "$COMMENT_AGENTS" >"$TMP/config_defaults.out"
) 2>/dev/null
config_got=$(cat "$TMP/config_defaults.out" 2>/dev/null)
config_expect="10-13,15-19|opencode:muse-spark-1.3-contributor-free|1|opencode:muse-spark-1.3-contributor-free,opencode:muse-spark-1.2-contributor-free,vercel:minimax/minimax-m3-free,vercel:poolside/laguna-s-2.1-free,vercel:inclusionai/ling-3.0-flash-fin,vercel:zai/glm-5.3-flash,vercel:xiaomi/mimo-v2.5,vercel:alibaba/qwen3.8-flash,vercel:xiaomi/mimo-v2.5-pro,amd:DeepSeek-V4-Flash,minimax-api:MiniMax-M3,opencode-go:muse-spark-1.3-contributor,opencode-go:muse-spark-1.2-contributor,opencode-go:deepseek-v4-flash|opencode:muse-spark-1.3-contributor-free,opencode:muse-spark-1.2-contributor-free,vercel:minimax/minimax-m3-free,vercel:poolside/laguna-s-2.1-free,vercel:inclusionai/ling-3.0-flash-fin,vercel:zai/glm-5.3-flash,vercel:xiaomi/mimo-v2.5,vercel:alibaba/qwen3.8-flash,vercel:xiaomi/mimo-v2.5-pro,amd:DeepSeek-V4-Flash,minimax-api:MiniMax-M3,opencode-go:muse-spark-1.3-contributor,opencode-go:muse-spark-1.2-contributor,opencode-go:deepseek-v4-flash"
[ "$config_got" = "$config_expect" ] && ok "config.sh defaults wired correctly" || not_ok "config.sh defaults wired correctly (got '$config_got')"

empty_vercel_chain=$(
	set +u
	cd "$TMP" || exit 1
	VERCEL_FREE_AGENTS=""
	ELOOP_LIB_DIR="$TMP"
	. "$CONFIG_SRC" >/dev/null 2>&1
	printf '%s' "$AI_COMMON_AGENTS"
)
case "$empty_vercel_chain" in
	*vercel:*|*,,*) not_ok "empty VERCEL_FREE_AGENTS disables Vercel cleanly (got '$empty_vercel_chain')" ;;
	*) ok "empty VERCEL_FREE_AGENTS disables Vercel cleanly" ;;
esac

custom_vercel_chain=$(
	set +u
	cd "$TMP" || exit 1
	VERCEL_FREE_AGENTS="vercel:custom-free"
	ELOOP_LIB_DIR="$TMP"
	. "$CONFIG_SRC" >/dev/null 2>&1
	printf '%s' "$AI_COMMON_AGENTS"
)
case "$custom_vercel_chain" in
	*vercel:custom-free*) ok "custom VERCEL_FREE_AGENTS is preserved" ;;
	*) not_ok "custom VERCEL_FREE_AGENTS is preserved (got '$custom_vercel_chain')" ;;
esac

category_b_chain=$(
	set +u
	cd "$TMP" || exit 1
	unset VERCEL_FREE_AGENTS
	VERCEL_CATEGORY_A_AGENTS="vercel:a-free"
	VERCEL_CATEGORY_B_AGENTS="vercel:b-credit"
	ELOOP_LIB_DIR="$TMP"
	. "$CONFIG_SRC" >/dev/null 2>&1
	printf '%s' "$AI_COMMON_AGENTS"
)
case "$category_b_chain" in
	*vercel:a-free,vercel:b-credit*) ok "Vercel category A precedes category B" ;;
	*) not_ok "Vercel A/B category order (got '$category_b_chain')" ;;
esac

common_order=$(printf '%s' "$config_got" | cut -d'|' -f4)
muse_pos=${common_order%%opencode-go:muse-spark-1.3-contributor*}
amd_pos=${common_order%%amd:DeepSeek-V4-Flash*}
minimax_pos=${common_order%%minimax-api:MiniMax-M3*}
vercel_m3_pos=${common_order%%vercel:minimax/minimax-m3-free*}
[ "${#vercel_m3_pos}" -lt "${#amd_pos}" ] \
	&& ok "Vercel free chain precedes paid providers" \
	|| not_ok "Vercel free chain order (got '$common_order')"
[ "${#amd_pos}" -lt "${#muse_pos}" ] \
	&& ok "paid chain: AMD DeepSeek precedes muse" \
	|| not_ok "paid chain order (got '$common_order')"
[ "${#minimax_pos}" -lt "${#muse_pos}" ] \
	&& ok "paid chain: MiniMax precedes muse" \
	|| not_ok "paid MiniMax order (got '$common_order')"
[ "${#amd_pos}" -lt "${#muse_pos}" ] \
	&& ok "free-credit AMD DeepSeek precedes metered providers" \
	|| not_ok "AMD DeepSeek free-credit order (got '$common_order')"
metered_deepseek_pos=${common_order%%opencode-go:deepseek-v4-flash*}
[ "${#metered_deepseek_pos}" -gt "${#muse_pos}" ] \
	&& ok "metered DeepSeek is final-stage" \
	|| not_ok "metered DeepSeek final-stage order (got '$common_order')"
case "$common_order" in
	*vercel:zai/glm-5.3-flash*vercel:xiaomi/mimo-v2.5*vercel:alibaba/qwen3.8-flash*vercel:xiaomi/mimo-v2.5-pro*) ok "category B is enabled after A in cost order" ;;
	*) not_ok "category B default order (got '$common_order')" ;;
esac

(
	set +u
	unset RADIO_JIJI_RESEARCH_TIMEOUT
	ELOOP_LIB_DIR="$TMP"
	# shellcheck disable=SC1090
	. "$CONFIG_SRC" >/dev/null 2>&1
	printf '%s' "$RADIO_JIJI_RESEARCH_TIMEOUT" >"$TMP/jiji_timeout.out"
) 2>/dev/null
jiji_timeout=$(cat "$TMP/jiji_timeout.out" 2>/dev/null)
[ "$jiji_timeout" = "300" ] && ok "JIJI research timeout defaults to 300s" \
	|| not_ok "JIJI research timeout default (got '$jiji_timeout')"
jiji_wiring=$(grep -c 'RADIO_JIJI_RESEARCH_TIMEOUT:-${RADIO_OPENCODE_TIMEOUT}' "$ROOT/broadcast/radio_corners.sh")
[ "$jiji_wiring" -ge 2 ] && ok "radio_corners.sh: JIJI research timeout wired separately" \
	|| not_ok "radio_corners.sh: JIJI research timeout wiring (count=$jiji_wiring)"

# ============================================================
# G. 呼び出し箇所の結線（静的アサーション）
# ============================================================
radio_call_line=$(grep -n '_peak_priority_agent_list' "$RADIO_SRC" | head -1 | cut -d: -f1)
# 本文生成の ai_generate_list 呼び出しのみを対象にする（prepass は :prepass ラベルで別系統）。
radio_gen_line=$(grep -n 'ai_generate_list "RADIO:' "$RADIO_SRC" | grep -v ':prepass' | head -1 | cut -d: -f1)
if [ -n "$radio_call_line" ] && [ -n "$radio_gen_line" ] && [ "$radio_call_line" -lt "$radio_gen_line" ]; then
	ok "radio_engine.sh: peak reorder wired before ai_generate_list call"
else
	not_ok "radio_engine.sh: peak reorder wired before ai_generate_list call (call=$radio_call_line gen=$radio_gen_line)"
fi

comment_call_count=$(grep -c '_peak_priority_agent_list' "$COMMENT_SRC")
comment_first_call_line=$(grep -n '_peak_priority_agent_list' "$COMMENT_SRC" | head -1 | cut -d: -f1)
comment_gen_line=$(grep -n 'ai_generate_list "COMMENT"' "$COMMENT_SRC" | head -1 | cut -d: -f1)
if [ "$comment_call_count" -ge 1 ] && [ -n "$comment_first_call_line" ] && [ -n "$comment_gen_line" ] && [ "$comment_first_call_line" -lt "$comment_gen_line" ]; then
	ok "comment.sh: peak reorder wired before ai_generate_list call"
else
	not_ok "comment.sh: peak reorder wired before ai_generate_list call (count=$comment_call_count call=$comment_first_call_line gen=$comment_gen_line)"
fi

# ============================================================
# H. 既存機能（issue #5 バックプレッシャー）の非破壊確認
# ============================================================
if grep -q '_radio_generation_blocked_by_backpressure' "$ROOT/broadcast/scheduler.sh"; then
	ok "backpressure guard function still present"
else
	not_ok "backpressure guard function still present"
fi
max_count=$(grep -c 'RADIO_DEFERRED_QUEUE_MAX' "$CONFIG_SRC")
[ "$max_count" -ge 1 ] && ok "RADIO_DEFERRED_QUEUE_MAX still defined" || not_ok "RADIO_DEFERRED_QUEUE_MAX still defined"

exit "$FAIL"
