#!/bin/bash
# strategy/ab_integrity.sh - A/B 実験の実体整合性を保つ fail-closed 層
#
# ab_state.json の A/B hash は実験開始時に固定される。一方 strategy.py は deploy / restore
# 等で実験中に外部から差し替わり得る。ab_interleave.sh の _ab_active() は hash 不一致を
# 検出して B 腕を止めるが、state 自体は残すため dashboard が A/B active と誤表示し、
# Strategy Comparison の current(root) と A/B の A が食い違って見える。
#
# この層は _ab_active() の既存 fail-closed 判定を保ったまま、root(A) または alt(B) の
# hash drift を検出した実験を「勝敗なしの stale」として退役させる。root は変更せず、
# B を rejected にも入れない。証跡は tmp/history へ保存する。

_ab_integrity_retire_stale() {
	local kind="$1" observed="$2" expected="$3"
	[ -f "$AB_STATE_FILE" ] || return 0

	local history="${AB_HISTORY_DIR:-tmp/history}" ts reg_before reason
	ts=$(date +%Y%m%d_%H%M%S)
	mkdir -p "$history" || return 1
	reason="${kind}: observed=${observed:-?} expected=${expected:-?}"
	reg_before=$(_ab_state_get regression_disabled_before)
	[ -n "$reg_before" ] || reg_before=0

	# state に退役理由を確定してから archive する。A/B の勝敗ではないため winner は付けない。
	python3 - "$AB_STATE_FILE" "$reason" "$kind" "$observed" "$expected" <<'PY' 2>/dev/null || true
import json, os, sys, time
p, reason, kind, observed, expected = sys.argv[1:6]
try:
    st = json.load(open(p, encoding="utf-8"))
except Exception:
    st = {}
st.update({
    "aborted_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    "abort_reason": reason,
    "retired_reason": kind,
    "observed_hash": observed,
    "expected_hash": expected,
})
tmp = p + ".tmp"
with open(tmp, "w", encoding="utf-8") as fh:
    json.dump(st, fh, ensure_ascii=False, indent=1)
os.replace(tmp, p)
PY

	# 実験開始前の runtime toggle へ戻す。外部 set_toggle は .env を更新するだけなので、
	# 長命 shell 側の SOREN_AB_ALT_STRATEGY も明示的に空にする。
	./set_toggle.sh "SOREN_AB_ALT_STRATEGY=" >/dev/null 2>&1 || true
	./set_toggle.sh "REGRESSION_DISABLED=$reg_before" >/dev/null 2>&1 || true
	SOREN_AB_ALT_STRATEGY=""
	export SOREN_AB_ALT_STRATEGY
	AB_EXTRA_ENV=""
	export AB_EXTRA_ENV
	AB_ARM=""
	AB_HASH=""
	AB_SOURCE=""
	AB_IDX=""
	AB_HELPERS=""
	rm -f "$AB_ABORT_FILE"

	# 実験データを証跡として退避する。state を最後に動かし、その存在を lifecycle lock とする。
	[ ! -f "$AB_GAMES_FILE" ] || mv "$AB_GAMES_FILE" "$history/ab_${ts}_games_stale.jsonl" || return 1
	if [ -f "$AB_ALT_FILE" ]; then
		mv "$AB_ALT_FILE" "$history/ab_${ts}_alt_stale.py" || return 1
	fi
	if [ -d "${AB_ALT_HELPERS_DIR:-${TMP_STATE_DIR:-tmp/state}/ab_alt_helpers}" ]; then
		mv "${AB_ALT_HELPERS_DIR:-${TMP_STATE_DIR:-tmp/state}/ab_alt_helpers}" "$history/ab_${ts}_alt_helpers_stale" || return 1
	fi
	mv "$AB_STATE_FILE" "$history/ab_${ts}_state_stale.json" || return 1

	log "[AB] stale experiment retired: $reason (root unchanged; no winner/reject)"
	return 0
}

# ab_interleave.sh の判定を包み、hash drift のときだけ lifecycle を閉じる。
# その他の一時的な inactive 条件 (pause/lock/toggle 等) は従来挙動を変えない。
unset -f _ab_active_without_integrity 2>/dev/null || true
if declare -F _ab_active >/dev/null 2>&1; then
	eval "$(declare -f _ab_active | sed '1s/^_ab_active /_ab_active_without_integrity /')"
fi

_ab_active() {
	if ! declare -F _ab_active_without_integrity >/dev/null 2>&1; then
		return 1
	fi
	_ab_active_without_integrity
	local rc=$?
	[ "$rc" -ne 0 ] || return 0
	[ -f "$AB_STATE_FILE" ] || return "$rc"

	local a b ha hb
	a=$(_ab_state_get a_hash)
	b=$(_ab_state_get b_hash)
	ha=$(_ab_hash "${STRATEGY_FILE:-strategy.py}")
	if [ -n "$a" ] && [ -n "$ha" ] && [ "$ha" != "$a" ]; then
		_ab_integrity_retire_stale "stale_base" "$ha" "$a" || true
		return "$rc"
	fi

	if [ -f "$AB_ALT_FILE" ]; then
		hb=$(_ab_hash "$AB_ALT_FILE")
		if [ -n "$b" ] && [ -n "$hb" ] && [ "$hb" != "$b" ]; then
			_ab_integrity_retire_stale "stale_candidate" "$hb" "$b" || true
		fi
	fi
	return "$rc"
}
