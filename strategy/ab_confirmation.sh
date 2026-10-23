#!/bin/bash
# strategy/ab_confirmation.sh - A/B の selection と独立 confirmation を分離する。
#
# 新しく開始した A/B だけを対象にする。第1標本で ADOPT になっても B を root へ
# 適用せず、その標本を履歴へ退避して games_recorded=0 から同じ事前登録規則で
# 第2標本を集める。第2標本も ADOPT のときだけ既存 _ab_finish B へ進む。
# 既に走っている state（confirmation_required が無い）は従来挙動を維持する。

_ab_confirmation_truthy() {
	case "${1:-}" in
		1 | true | True | TRUE | yes | YES) return 0 ;;
		*) return 1 ;;
	esac
}

_ab_confirmation_mark_new_state() {
	[ -f "$AB_STATE_FILE" ] || return 1
	python3 - "$AB_STATE_FILE" <<'PY'
import json, os, sys, time
p = sys.argv[1]
st = json.load(open(p, encoding="utf-8"))
# setdefault にして、明示的な将来の opt-out / migration を上書きしない。
st.setdefault("confirmation_required", True)
st.setdefault("confirmation_version", 1)
st.setdefault("confirmation_phase", "selection")
st.setdefault("verified_improvement", False)
st.setdefault("selection_started_at", time.strftime("%Y-%m-%dT%H:%M:%S"))
tmp = p + ".tmp"
with open(tmp, "w", encoding="utf-8") as fh:
    json.dump(st, fh, ensure_ascii=False, indent=1)
os.replace(tmp, p)
PY
}

_ab_confirmation_set_verified() {
	local value="$1" reason="${2:-}"
	[ -f "$AB_STATE_FILE" ] || return 1
	python3 - "$AB_STATE_FILE" "$value" "$reason" <<'PY'
import json, os, sys, time
p, value, reason = sys.argv[1:4]
st = json.load(open(p, encoding="utf-8"))
st["verified_improvement"] = value == "1"
if value == "1":
    st["confirmation_passed_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    st["confirmation_reason"] = reason
else:
    st.pop("confirmation_passed_at", None)
    st.pop("confirmation_reason", None)
tmp = p + ".tmp"
with open(tmp, "w", encoding="utf-8") as fh:
    json.dump(st, fh, ensure_ascii=False, indent=1)
os.replace(tmp, p)
PY
}

_ab_begin_independent_confirmation() {
	local reason="${1:-ADOPT}" history ts selection_file selection_report
	[ -f "$AB_STATE_FILE" ] || return 1
	[ -s "$AB_GAMES_FILE" ] || { log "[AB-CONFIRM] selection 標本が空 → fail-closed"; return 1; }
	history="${AB_HISTORY_DIR:-tmp/history}"
	ts=$(date +%Y%m%d_%H%M%S)
	mkdir -p "$history" || return 1
	selection_file="$history/ab_${ts}_selection_games.jsonl"
	selection_report="$history/ab_${ts}_selection_report.txt"
	[ ! -e "$selection_file" ] || { log "[AB-CONFIRM] selection archive 衝突 → fail-closed"; return 1; }

	# レポートは best-effort。判定に使った生標本そのものは必ず保存する。
	python3 tools/ab_report.py --games "$AB_GAMES_FILE" --state "$AB_STATE_FILE" >"$selection_report" 2>/dev/null || true

	# 安全側の順序: 先に selection 標本を退避して live decision file を空にし、
	# 最後に state を confirmation phase へ進める。途中停止しても旧 selection を
	# confirmation と誤認して B を適用する方向には進まない。
	mv "$AB_GAMES_FILE" "$selection_file" || return 1
	: >"$AB_GAMES_FILE" || return 1
	python3 - "$AB_STATE_FILE" "$selection_file" "$reason" <<'PY' || return 1
import json, os, sys, time
p, selection_file, reason = sys.argv[1:4]
st = json.load(open(p, encoding="utf-8"))
try:
    n = sum(1 for line in open(selection_file, encoding="utf-8") if line.strip())
except Exception:
    n = 0
st.update({
    "confirmation_phase": "confirmation",
    "selection_finished_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    "selection_reason": reason,
    "selection_games": n,
    "selection_games_file": os.path.basename(selection_file),
    "games_recorded": 0,
    "verified_improvement": False,
})
tmp = p + ".tmp"
with open(tmp, "w", encoding="utf-8") as fh:
    json.dump(st, fh, ensure_ascii=False, indent=1)
os.replace(tmp, p)
PY
	AB_ARM=""; AB_HASH=""; AB_SOURCE=""; AB_IDX=""; AB_HELPERS=""; AB_EXTRA_ENV=""
	export AB_EXTRA_ENV
	log "[AB-CONFIRM] selection ADOPT を保留: 独立 confirmation を開始 (selection=$(basename "$selection_file"))"
	return 0
}

# 新規 start だけ confirmation_required を付与する。patch 導入前から存在する
# ab_state.json には触れないので、実験途中で判定契約を書き換えない。
unset -f _ab_start_from_bundle_without_confirmation 2>/dev/null || true
if declare -F _ab_start_from_bundle >/dev/null 2>&1; then
	eval "$(declare -f _ab_start_from_bundle | sed '1s/^_ab_start_from_bundle /_ab_start_from_bundle_without_confirmation /')"
fi

_ab_start_from_bundle() {
	if ! declare -F _ab_start_from_bundle_without_confirmation >/dev/null 2>&1; then
		return 1
	fi
	_ab_start_from_bundle_without_confirmation "$@" || return $?
	if [ -f "$AB_STATE_FILE" ]; then
		_ab_confirmation_mark_new_state || {
			log "[AB-CONFIRM] confirmation state 初期化失敗 → A維持でA/Bを終了"
			# confirmation_required を永続化できない state を継続すると、後で
			# 旧1段階finishへfall-throughしてしまう。必ずA側で閉じる。
			if declare -F _ab_finish >/dev/null 2>&1; then
				_ab_finish A "confirmation_init_failed" >/dev/null 2>&1 || true
			fi
			return 1
		}
		log "[AB-CONFIRM] independent confirmation required for new A/B"
	fi
	return 0
}

# ab_integrity.sh が提供する env-only promotion wrapper も含めた現在の finish を包む。
# B の第1勝利は live apply せず、第2の独立標本で再度勝った場合だけ既存 finish に渡す。
unset -f _ab_finish_without_confirmation 2>/dev/null || true
if declare -F _ab_finish >/dev/null 2>&1; then
	eval "$(declare -f _ab_finish | sed '1s/^_ab_finish /_ab_finish_without_confirmation /')"
fi

_ab_finish() {
	if ! declare -F _ab_finish_without_confirmation >/dev/null 2>&1; then
		return 1
	fi
	local winner="$1" reason="${2:-}" required phase rc
	if [ "$winner" != "B" ] || [ ! -f "$AB_STATE_FILE" ]; then
		_ab_finish_without_confirmation "$@"
		return $?
	fi
	required=$(_ab_state_get confirmation_required)
	_ab_confirmation_truthy "$required" || {
		_ab_finish_without_confirmation "$@"
		return $?
	}
	phase=$(_ab_state_get confirmation_phase)
	case "$phase" in
		selection | "")
			_ab_begin_independent_confirmation "$reason"
			return $?
			;;
		confirmation)
			# verified_improvement は独立 confirmation を通った場合に限る。
			# underlying apply が失敗したら state が残るので false へ戻す。
			_ab_confirmation_set_verified 1 "$reason" || return 1
			if _ab_finish_without_confirmation B "CONFIRMED independent: $reason"; then
				log "[AB-CONFIRM] independent confirmation passed → B adopt"
				return 0
			else
				rc=$?
				_ab_confirmation_set_verified 0 "" >/dev/null 2>&1 || true
				log "[AB-CONFIRM] confirmation は勝ったが B apply/finish 失敗 → verified を取消"
				return "$rc"
			fi
			;;
		*)
			log "[AB-CONFIRM] unknown confirmation_phase=$phase → B adopt fail-closed"
			return 1
			;;
	esac
}
