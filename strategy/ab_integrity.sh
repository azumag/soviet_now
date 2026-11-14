#!/bin/bash
# strategy/ab_integrity.sh - A/B 実験の実体整合性を保つ fail-closed 層
#
# ab_state.json の A/B hash は実験開始時に固定される。一方 strategy.py は deploy / restore
# 等で実験中に外部から差し替わり得る。ab_interleave.sh の _ab_active() は hash 不一致を
# 検出して B 腕を止めるが、state 自体は残すため dashboard が A/B active と誤表示し、
# Strategy Comparison の current(root) と A/B の A が食い違って見える。
#
# この層は _ab_active() の既存 fail-closed 判定を保ったまま、root(A) または alt(B) の
# hash drift と、継続不能な state/candidate 欠損を検出した実験を「勝敗なしの stale」として
# 退役させる。root は変更せず、B を rejected にも入れない。証跡は tmp/history へ保存する。
#
# root drift 時の扱いは .env の AB_ON_ROOT_DRIFT で選ぶ (soviet_now#865):
#   retire (既定): 従来どおり stale 退役する。
#   rebase       : stale 退役せず A を観測された新 root へ張り替え、B をそのまま
#                  新 A との測定を最初からやり直す (confirmation 中なら selection へ戻す)。
#                  収集済み標本は tmp/history/ab_<ts>_games_rootdrift.jsonl へ保全されるが、
#                  旧 root との比較値なので判定標本には混ぜない。

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
	command -v _ab_status_publish_async >/dev/null 2>&1 && _ab_status_publish_async "retire:$kind"
	return 0
}

# AB_ON_ROOT_DRIFT=rebase: root(A) の drift を観測した時、実験を stale 退役せず
# A を新 root へ張り替えて測定をやり直す。B (alt file/hash) はそのまま残し、
# 旧 A との標本は判定へ混ぜず history へ保全する。confirmation 中でも
# selection へ戻す (旧 root への selection ADOPT は新 root へは引き継げない)。
_ab_integrity_rebase_root() {
	local observed="$1" expected="$2"
	[ -f "$AB_STATE_FILE" ] || return 1

	local history="${AB_HISTORY_DIR:-tmp/history}" ts
	ts=$(date +%Y%m%d_%H%M%S)
	mkdir -p "$history" || return 1

	# 旧 root との標本は保全するが live ledger からは外す (判定へ混ぜない)。
	if [ -f "$AB_GAMES_FILE" ]; then
		mv "$AB_GAMES_FILE" "$history/ab_${ts}_games_rootdrift.jsonl" || return 1
	fi
	: >"$AB_GAMES_FILE" || return 1

	# 新 root の policy bundle を取り直す (bundle は着手一式の同一性)。
	local bundle_a=""
	bundle_a=$(python3 tools/policy_bundle.py --strategy "${STRATEGY_FILE:-strategy.py}" 2>/dev/null || echo "")
	python3 - "$AB_STATE_FILE" "$observed" "$expected" "$bundle_a" "$history/ab_${ts}_games_rootdrift.jsonl" <<'PY' || return 1
import json, os, sys, time
p, observed, expected, bundle_a, drift_file = sys.argv[1:6]
st = json.load(open(p, encoding="utf-8"))
events = st.get("root_drift_events")
if not isinstance(events, list):
    events = []
events.append({
    "at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    "from": expected,
    "to": observed,
    "games": int(st.get("games_recorded") or 0),
    "phase": st.get("confirmation_phase") or "selection",
    "selection_games_file": st.get("selection_games_file") or "",
    "archived_games": os.path.basename(drift_file),
})
st["root_drift_events"] = events
st["a_hash"] = observed
if bundle_a:
    st["a_policy_bundle"] = bundle_a
st["games_recorded"] = 0
st.pop("last_arm", None)
st["rebased_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
st["rebase_reason"] = "root_drift"
# 旧 root への selection 結果は新 root へ引き継げないため測定を最初からやり直す。
for k in ("confirmation_phase", "selection_finished_at", "selection_reason",
          "selection_games", "selection_games_file", "verified_improvement",
          "confirmation_passed_at", "confirmation_reason"):
    st.pop(k, None)
if st.get("confirmation_required"):
    st["confirmation_phase"] = "selection"
    st["verified_improvement"] = False
    st["selection_started_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
tmp = p + ".tmp"
with open(tmp, "w", encoding="utf-8") as fh:
    json.dump(st, fh, ensure_ascii=False, indent=1)
os.replace(tmp, p)
PY

	AB_ARM=""; AB_HASH=""; AB_SOURCE=""; AB_IDX=""; AB_HELPERS=""; AB_EXTRA_ENV=""
	export AB_EXTRA_ENV
	log "[AB] root drift: A を新 root へ張り替えて測定を再開 (${expected:0:12} → ${observed:0:12}, policy=rebase, 旧標本は history へ保全)"
	command -v _ab_status_publish_async >/dev/null 2>&1 && _ab_status_publish_async "rebase_root"
	return 0
}

# ab_interleave.sh の判定を包み、実体整合性が壊れたときだけ lifecycle を閉じる。
# pause/lock/toggle 等の一時的な inactive 条件だけなら従来挙動を変えない。
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

	# state が lifecycle lock なのに期待 hash が欠けている場合、その実験は安全に再開できない。
	# 一方、実体側 hash の一時的な計算失敗 (ha/hb が空) は証拠を消さず従来どおり fail-closed に留める。
	if [ -z "$a" ]; then
		_ab_integrity_retire_stale "stale_state_a_hash_missing" "" "a_hash" || true
		return "$rc"
	fi
	if [ -z "$b" ]; then
		_ab_integrity_retire_stale "stale_state_b_hash_missing" "" "b_hash" || true
		return "$rc"
	fi

	ha=$(_ab_hash "${STRATEGY_FILE:-strategy.py}")
	if [ -n "$ha" ] && [ "$ha" != "$a" ]; then
		case "$(_ab_env_value AB_ON_ROOT_DRIFT)" in
		rebase)
			# 新 root 上で B を測り直す。rebase に失敗した場合だけ stale 退役へ。
			_ab_integrity_rebase_root "$ha" "$a" ||
				_ab_integrity_retire_stale "stale_base" "$ha" "$a" || true
			;;
		*)
			_ab_integrity_retire_stale "stale_base" "$ha" "$a" || true
			;;
		esac
		return "$rc"
	fi

	# state が残っているのに candidate が消えているのは継続不能。部分退役が途中で止まった場合も
	# 次回判定で state を最後まで閉じられるよう stale として扱う。
	if [ ! -f "$AB_ALT_FILE" ]; then
		_ab_integrity_retire_stale "stale_candidate_missing" "missing" "$b" || true
		return "$rc"
	fi

	hb=$(_ab_hash "$AB_ALT_FILE")
	if [ -n "$hb" ] && [ "$hb" != "$b" ]; then
		_ab_integrity_retire_stale "stale_candidate" "$hb" "$b" || true
	fi
	return "$rc"
}

# env-only A/B は strategy hash が同一なので、従来の _ab_finish B だけでは B 側の
# runtime mode が恒久設定へ昇格しない。現在必要な昇格対象は merge-top model のみとし、
# 候補 hash が root と同一で、B 側 env が 0/1/2 の単一値を持つ場合に限って反映する。
# 通常の strategy A/B (a_hash != b_hash) は一切変更しない。
_ab_envonly_merge_top_value() {
	local spec="$1"
	python3 - "$spec" <<'PY' 2>/dev/null
import shlex, sys
try:
    parts = shlex.split(sys.argv[1])
except Exception:
    raise SystemExit(1)
vals = []
for part in parts:
    if part.startswith("ANALYZE_BOARD_MERGE_TOP_MODEL="):
        vals.append(part.split("=", 1)[1])
if len(vals) != 1 or vals[0] not in {"0", "1", "2"}:
    raise SystemExit(1)
print(vals[0])
PY
}

unset -f _ab_finish_without_envonly_promotion 2>/dev/null || true
if declare -F _ab_finish >/dev/null 2>&1; then
	eval "$(declare -f _ab_finish | sed '1s/^_ab_finish /_ab_finish_without_envonly_promotion /')"
fi

_ab_finish() {
	if ! declare -F _ab_finish_without_envonly_promotion >/dev/null 2>&1; then
		return 1
	fi

	local winner="$1" a b b_env desired previous rc
	if [ "$winner" = "B" ] && [ -f "$AB_STATE_FILE" ]; then
		a=$(_ab_state_get a_hash)
		b=$(_ab_state_get b_hash)
		if [ -n "$a" ] && [ "$a" = "$b" ]; then
			b_env=$(_ab_state_get b_env)
			desired=$(_ab_envonly_merge_top_value "$b_env" 2>/dev/null || true)
			if [ -n "$desired" ]; then
				previous=$(_ab_env_value ANALYZE_BOARD_MERGE_TOP_MODEL)
				./set_toggle.sh "ANALYZE_BOARD_MERGE_TOP_MODEL=$desired" >/dev/null 2>&1 || {
					log "[AB] env-only B 採用: ANALYZE_BOARD_MERGE_TOP_MODEL=$desired の恒久化に失敗"
					return 1
				}
				command -v reload_runtime_toggles_force >/dev/null 2>&1 && reload_runtime_toggles_force >/dev/null 2>&1 || true
				if _ab_finish_without_envonly_promotion "$@"; then
					log "[AB] env-only B 採用: ANALYZE_BOARD_MERGE_TOP_MODEL=$desired を本番設定へ昇格"
					return 0
				else
					rc=$?
				fi
				./set_toggle.sh "ANALYZE_BOARD_MERGE_TOP_MODEL=$previous" >/dev/null 2>&1 || true
				command -v reload_runtime_toggles_force >/dev/null 2>&1 && reload_runtime_toggles_force >/dev/null 2>&1 || true
				log "[AB] env-only B 採用失敗: merge-top mode を ${previous:-default} へロールバック"
				return "$rc"
			fi
		fi
	fi

	_ab_finish_without_envonly_promotion "$@"
}
