# strategy/ab_gate.sh - 改善候補を root に適用せず、インターリーブ A/B (strategy/ab_interleave.sh) で採否を
# 判定するゲート。既定オフ (.env AB_GATE_ENABLED=1 で有効、AB_GATE_DRY_RUN=1 なら判定をログするだけ)。
#
# 流れ: 改善ジョブ (eloop_improve.sh) が候補を tmp/state/ab_candidate/ に出力 (_ab_gate_emit_candidate)
#   → 境界 (_ab_gate_before_game) で root(A) vs 候補(B) の A/B を自動開始 (_ab_start_from_bundle)
#   → 試合ごと (_ab_gate_after_game) に tools/ab_decide.py の逐次判定 → ADOPT なら root 差し替え、
#     REJECT_*/ABORT なら棄却 (_ab_finish)。時間順ゲート (check_regression) は A/B 中 REGRESSION_DISABLED=1。
AB_CANDIDATE_DIR="${AB_CANDIDATE_DIR:-${TMP_STATE_DIR:-tmp/state}/ab_candidate}"
AB_ALT_HELPERS_DIR="${AB_ALT_HELPERS_DIR:-${TMP_STATE_DIR:-tmp/state}/ab_alt_helpers}"
AB_HISTORY_DIR="${AB_HISTORY_DIR:-tmp/history}"
AB_HISTORY_FILE="${AB_HISTORY_FILE:-${AB_HISTORY_DIR:-tmp/history}/ab_history.jsonl}"
AB_REJECTED_FILE="${REJECTED_HASHES_FILE:-${TMP_STATE_DIR:-tmp/state}/rejected_hashes.txt}"

_ab_gate_enabled() {
	[ "$(_ab_env_value AB_GATE_ENABLED)" = "1" ]
}

# 未設定 (空) は dry-run 扱い = 安全側
_ab_gate_dry_run() {
	local v
	v=$(_ab_env_value AB_GATE_DRY_RUN)
	[ -z "$v" ] || [ "$v" = "1" ]
}

_ab_gate_candidate_pending() {
	[ -f "$AB_CANDIDATE_DIR/meta.json" ] && [ -f "$AB_CANDIDATE_DIR/strategy.py" ]
}

_ab_meta_get() {
	python3 - "$AB_CANDIDATE_DIR/meta.json" "$1" <<'PY' 2>/dev/null || echo ""
import json, sys
try:
    v = json.load(open(sys.argv[1])).get(sys.argv[2], "")
    print("" if v is None else v)
except Exception:
    print("")
PY
}

# 改善ジョブの harvest ディレクトリから候補を取り出す (root は触らない)。
_ab_gate_emit_candidate() {
	local hd="$1" base="$2" gnum="${3:-}" scores="${4:-}" cand tmpd
	[ -f "$hd/strategy.py.staging" ] || { log "[AB-GATE] harvest に strategy.py.staging がない"; return 1; }
	cand=$(_ab_hash "$hd/strategy.py.staging")
	if [ -z "$cand" ] || [ "$cand" = "$base" ]; then
		log "[AB-GATE] candidate hash が空か base と同一 (${cand:0:12}) → 出力しない"
		return 1
	fi
	tmpd="$AB_CANDIDATE_DIR.tmp"
	rm -rf "$tmpd"
	mkdir -p "$tmpd" || return 1
	cp -p "$hd/strategy.py.staging" "$tmpd/strategy.py" || return 1
	if [ -d "$hd/strategy_helpers" ]; then
		cp -R "$hd/strategy_helpers" "$tmpd/strategy_helpers" || return 1
	fi
	if [ -s "$hd/logs/change_log.txt" ]; then
		cp -p "$hd/logs/change_log.txt" "$tmpd/change_log.txt" 2>/dev/null || true
	fi
	python3 - "$tmpd/meta.json" "$base" "$cand" "$gnum" "$scores" <<'PY' || return 1
import json, sys, time
json.dump({"base_hash": sys.argv[2], "cand_hash": sys.argv[3], "game_num": sys.argv[4], "scores": sys.argv[5],
           "created_at": int(time.time()), "created_iso": time.strftime("%Y-%m-%dT%H:%M:%S")},
          open(sys.argv[1], "w", encoding="utf-8"), ensure_ascii=False, indent=1)
PY
	rm -rf "$AB_CANDIDATE_DIR"
	mv "$tmpd" "$AB_CANDIDATE_DIR" || return 1
	log "[AB-GATE] candidate_ready: base=${base:0:12} cand=${cand:0:12}"
	return 0
}

# 候補が「改善ジョブ開始時刻以降に、現 root を base として」出力されているか (harvest の成功判定用)。
_ab_gate_candidate_ready_since() {
	local since="${1:-0}" root_hash="$2" created base
	_ab_gate_candidate_pending || return 1
	created=$(_ab_meta_get created_at)
	base=$(_ab_meta_get base_hash)
	[ -n "$created" ] && [ "$created" -ge "${since:-0}" ] 2>/dev/null || return 1
	[ -z "$root_hash" ] || [ "$base" = "$root_hash" ] || return 1
	return 0
}

# root(A) と bundle (strategy.py [+ strategy_helpers/]) の A/B を次試合から開始する (tools/ab_ctl.sh start と共通)。
_ab_start_from_bundle() {
	local dir="$1" pattern="${2:-ABBA}" src a b helpers_src="" pause_pre=0 reg_before
	src="$dir/strategy.py"
	[ -f "$src" ] || { log "[AB] 候補ファイルがない: $src"; return 1; }
	[ -f "$AB_STATE_FILE" ] && { log "[AB] 既に A/B 状態がある ($AB_STATE_FILE)"; return 1; }
	a=$(_ab_hash "${STRATEGY_FILE:-strategy.py}")
	b=$(_ab_hash "$src")
	# 同一 hash は原則禁止（差が無い A/B）。ただし腕別 env が異なる場合だけは
	# 「同じ戦略・解析器モードだけ違う」実験として許可する。
	if [ -z "$a" ] || [ -z "$b" ]; then
		log "[AB] hash 不正 (a=${a:0:12} b=${b:0:12})"
		return 1
	fi
	if [ "$a" = "$b" ] && [ "${AB_A_ENV:-}" = "${AB_B_ENV:-}" ]; then
		log "[AB] hash 不正 (a=${a:0:12} b=${b:0:12}, 腕別 env も同一)"
		return 1
	fi
	[ -d "$dir/strategy_helpers" ] && helpers_src="$dir/strategy_helpers"
	python3 -m py_compile "$src" >/dev/null 2>&1 || { log "[AB] 候補が py_compile 失敗"; return 1; }
	if command -v validate_strategy_with_helpers >/dev/null 2>&1; then
		if ! validate_strategy_with_helpers "$src" "${helpers_src:-strategy_helpers}" >/dev/null 2>&1; then
			log "[AB] 候補がバリデータで拒否された"
			return 1
		fi
	fi
	mkdir -p "$(dirname "$AB_STATE_FILE")" "$AB_HISTORY_DIR"
	cp -p "$src" "$AB_ALT_FILE" || return 1
	rm -rf "$AB_ALT_HELPERS_DIR"
	if [ -n "$helpers_src" ]; then
		cp -R "$helpers_src" "$AB_ALT_HELPERS_DIR" || return 1
	fi
	cp -p "${STRATEGY_FILE:-strategy.py}" tmp/revert_strategy.py 2>/dev/null || true
	if command -v _archive_strategy_snapshot_by_hash >/dev/null 2>&1; then
		_archive_strategy_snapshot_by_hash "${STRATEGY_FILE:-strategy.py}" >/dev/null 2>&1 || true
		_archive_strategy_snapshot_by_hash "$AB_ALT_FILE" >/dev/null 2>&1 || true
	fi
	[ -f "${TMP_STATE_DIR:-tmp/state}/improve_daemon.paused" ] && pause_pre=1
	reg_before=$(_ab_env_value REGRESSION_DISABLED)
	rm -f "$AB_ABORT_FILE"
	: >"$AB_GAMES_FILE"
	python3 - "$AB_STATE_FILE" "$a" "$b" "$pattern" "$src" "${GAME_COUNT_FILE:-game_count.txt}" "$pause_pre" "${reg_before:-0}" "$([ -n "$helpers_src" ] && echo 1 || echo 0)" <<'PY' || return 1
import json, os, sys, time
def _count(p):
    try:
        return int(open(p).read().strip())
    except Exception:
        return None
st = {"a_hash": sys.argv[2], "b_hash": sys.argv[3], "pattern": sys.argv[4], "alt_source": sys.argv[5],
      "started_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "games_recorded": 0, "game_num_start": _count(sys.argv[6]),
      "pause_preexisting": int(sys.argv[7]), "regression_disabled_before": sys.argv[8], "alt_helpers": int(sys.argv[9]),
      # 腕ごとの追加環境変数 ("KEY=VALUE KEY2=VALUE2"、既定は空)。解析器モード等の A/B に使う。
      "a_env": os.environ.get("AB_A_ENV", ""), "b_env": os.environ.get("AB_B_ENV", "")}
json.dump(st, open(sys.argv[1], "w", encoding="utf-8"), ensure_ascii=False, indent=1)
PY
	./set_toggle.sh REGRESSION_DISABLED=1 >/dev/null 2>&1 &&
		./set_toggle.sh "SOREN_AB_ALT_STRATEGY=$AB_ALT_FILE" >/dev/null 2>&1 &&
		./set_toggle.sh "SOREN_AB_PATTERN=$pattern" >/dev/null 2>&1 || { log "[AB] set_toggle 失敗"; return 1; }
	log "[AB] start A=${a:0:12} (root) B=${b:0:12} ($src) pattern=$pattern helpers=${helpers_src:-none}"
	return 0
}

# A/B を終了し、勝者を root にする (B) か棄却する (A)。記録は tmp/history へ移動。
_ab_finish() {
	local winner="$1" reason="${2:-}" a b ts win_hash root_after
	[ "$winner" = "A" ] || [ "$winner" = "B" ] || { log "[AB] finish: winner は A|B"; return 1; }
	[ -f "$AB_STATE_FILE" ] || { log "[AB] finish: 状態なし"; return 1; }
	a=$(_ab_state_get a_hash)
	b=$(_ab_state_get b_hash)
	./set_toggle.sh "SOREN_AB_ALT_STRATEGY=" >/dev/null 2>&1 || true
	ts=$(date +%Y%m%d_%H%M%S)
	mkdir -p "$AB_HISTORY_DIR"
	if [ "$winner" = "B" ]; then
		if [ "$(_ab_hash "$AB_ALT_FILE")" != "$b" ]; then
			log "[AB] finish B: 代替ファイルの hash が state と違う → 採用中止 (A を維持)"
			winner="A"
			reason="${reason};alt_hash_mismatch"
		fi
	fi
	if [ "$winner" = "B" ]; then
		cp -p "${STRATEGY_FILE:-strategy.py}" tmp/revert_strategy.py 2>/dev/null || true
		# helper は additive-only: strategy より先に配置する (import 先が無い瞬間を作らない)
		if [ -d "$AB_ALT_HELPERS_DIR" ]; then
			cp -R "$AB_ALT_HELPERS_DIR/." strategy_helpers/ 2>/dev/null || true
		fi
		if command -v strategy_runtime_atomic_apply >/dev/null 2>&1; then
			strategy_runtime_atomic_apply "$AB_ALT_FILE" "${STRATEGY_FILE:-strategy.py}" || { log "[AB] root 差し替え失敗"; return 1; }
		else
			cp -p "$AB_ALT_FILE" "${STRATEGY_FILE:-strategy.py}" || return 1
		fi
		root_after=$(_ab_hash "${STRATEGY_FILE:-strategy.py}")
		[ "$root_after" = "$b" ] || { log "[AB] 差し替え後 hash 不一致 ($root_after != $b)"; return 1; }
		command -v _archive_strategy_snapshot_by_hash >/dev/null 2>&1 && _archive_strategy_snapshot_by_hash "${STRATEGY_FILE:-strategy.py}" >/dev/null 2>&1 || true
		if [ -s "$AB_CANDIDATE_DIR/change_log.txt" ] && [ -n "${CHANGE_LOG_FILE_HOST:-}" ]; then
			cat "$AB_CANDIDATE_DIR/change_log.txt" >>"$CHANGE_LOG_FILE_HOST" 2>/dev/null || true
		fi
		command -v _clear_active_branch >/dev/null 2>&1 && _clear_active_branch >/dev/null 2>&1 || true
		win_hash="$b"
		command -v append_phyrogenetic_event >/dev/null 2>&1 && append_phyrogenetic_event "improve" "$a" "$b" "$(cat "${GAME_COUNT_FILE:-game_count.txt}" 2>/dev/null || echo 0)" "" "ab-gate adopt: $reason" "" >/dev/null 2>&1 || true
		git add strategy.py strategy_helpers/ >/dev/null 2>&1 && git commit -q -m "eloop Improve [ab-gate] adopt ${b} over ${a}: ${reason}" >/dev/null 2>&1 && git push -q >/dev/null 2>&1 || true
		log "[AB] finish: B 採用 root=${b:0:12} (revert=${a:0:12}) reason=$reason"
	else
		win_hash="$a"
		if [ -n "$b" ] && ! grep -qx "$b" "$AB_REJECTED_FILE" 2>/dev/null; then
			echo "$b" >>"$AB_REJECTED_FILE"
			tail -20 "$AB_REJECTED_FILE" >"$AB_REJECTED_FILE.tmp" 2>/dev/null && mv "$AB_REJECTED_FILE.tmp" "$AB_REJECTED_FILE"
		fi
		log "[AB] finish: A 維持 (B=${b:0:12} 棄却) reason=$reason"
	fi
	command -v _clear_accumulated_data >/dev/null 2>&1 && _clear_accumulated_data >/dev/null 2>&1 || true
	command -v _seed_current_strategy_run_from_rolling >/dev/null 2>&1 && _seed_current_strategy_run_from_rolling "$win_hash" >/dev/null 2>&1 || true
	command -v _promote_current_strategy_to_anchor >/dev/null 2>&1 && _promote_current_strategy_to_anchor "$win_hash" >/dev/null 2>&1 || true
	command -v _refresh_best_strategy_anchor >/dev/null 2>&1 && _refresh_best_strategy_anchor "" >/dev/null 2>&1 || true
	# 時間順ゲートは開始前の設定へ (gate 運用中は 1 のまま = 事実上無効)
	local reg_before
	reg_before=$(_ab_state_get regression_disabled_before)
	[ -n "$reg_before" ] || reg_before=0
	./set_toggle.sh "REGRESSION_DISABLED=$reg_before" >/dev/null 2>&1 || true
	rm -f "$AB_ABORT_FILE"
	rm -rf "$AB_ALT_HELPERS_DIR"
	python3 tools/ab_report.py --games "$AB_GAMES_FILE" --state "$AB_STATE_FILE" >"$AB_HISTORY_DIR/ab_${ts}_report.txt" 2>/dev/null || true
	python3 - "$AB_STATE_FILE" "$AB_GAMES_FILE" "$winner" "$reason" "$AB_HISTORY_FILE" "$ts" <<'PY' 2>/dev/null || true
import json, sys, time
st = json.load(open(sys.argv[1]))
rows = [json.loads(l) for l in open(sys.argv[2]) if l.strip()]
st.update({"winner": sys.argv[3], "reason": sys.argv[4], "finished_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "games": len(rows), "ts": sys.argv[6]})
with open(sys.argv[5], "a", encoding="utf-8") as fh:
    fh.write(json.dumps(st, ensure_ascii=False) + "\n")
PY
	mv "$AB_STATE_FILE" "$AB_HISTORY_DIR/ab_${ts}_state.json" 2>/dev/null || rm -f "$AB_STATE_FILE"
	mv "$AB_GAMES_FILE" "$AB_HISTORY_DIR/ab_${ts}_games.jsonl" 2>/dev/null || rm -f "$AB_GAMES_FILE"
	rm -rf "$AB_CANDIDATE_DIR" 2>/dev/null || true
	log "[AB] finish 完了: winner=$winner root=$(_ab_hash "${STRATEGY_FILE:-strategy.py}") 記録 $AB_HISTORY_DIR/ab_${ts}_*"
	return 0
}

# 境界 (play_one_game 冒頭): 候補があれば A/B を開始する。
_ab_gate_before_game() {
	_ab_gate_enabled || return 0
	[ -f "$AB_STATE_FILE" ] && return 0
	_ab_gate_candidate_pending || return 0
	local base root cand
	base=$(_ab_meta_get base_hash)
	cand=$(_ab_meta_get cand_hash)
	root=$(_ab_hash "${STRATEGY_FILE:-strategy.py}")
	if [ "$base" != "$root" ]; then
		log "[AB-GATE] 候補 ${cand:0:12} の base ${base:0:12} が現 root ${root:0:12} と違う → 破棄 (stale_base)"
		mkdir -p "$AB_HISTORY_DIR"
		mv "$AB_CANDIDATE_DIR" "$AB_HISTORY_DIR/ab_candidate_stale_$(date +%Y%m%d_%H%M%S)" 2>/dev/null || rm -rf "$AB_CANDIDATE_DIR"
		return 0
	fi
	if grep -qx "$cand" "$AB_REJECTED_FILE" 2>/dev/null; then
		log "[AB-GATE] 候補 ${cand:0:12} は棄却済み hash → 破棄"
		rm -rf "$AB_CANDIDATE_DIR"
		return 0
	fi
	if _ab_gate_dry_run; then
		if [ "$(_ab_meta_get dry_logged)" != "1" ]; then
			log "[AB-GATE] (dry-run) would start A/B: A=${root:0:12} B=${cand:0:12}"
			python3 - "$AB_CANDIDATE_DIR/meta.json" <<'PY' 2>/dev/null || true
import json, sys
p = sys.argv[1]; d = json.load(open(p)); d["dry_logged"] = 1
json.dump(d, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
PY
		fi
		return 0
	fi
	local pattern
	pattern=$(_ab_env_value SOREN_AB_PATTERN)
	[ -n "$pattern" ] || pattern="ABBA"
	if _ab_start_from_bundle "$AB_CANDIDATE_DIR" "$pattern"; then
		mkdir -p "$AB_HISTORY_DIR"
		mv "$AB_CANDIDATE_DIR" "$AB_HISTORY_DIR/ab_candidate_$(date +%Y%m%d_%H%M%S)" 2>/dev/null || rm -rf "$AB_CANDIDATE_DIR"
	else
		log "[AB-GATE] A/B 開始失敗 → 候補を破棄"
		rm -rf "$AB_CANDIDATE_DIR"
	fi
	return 0
}

# 試合後 (post_game_bookkeeping 末尾): 逐次判定して終了条件なら finish。
_ab_gate_after_game() {
	_ab_gate_enabled || return 0
	[ -f "$AB_STATE_FILE" ] || return 0
	if [ -f "$AB_ABORT_FILE" ]; then
		log "[AB-GATE] abort マーカー → finish A"
		_ab_finish A "abort:$(_ab_state_get abort_reason)" || true
		return 0
	fi
	local looks maxb fut out verdict k m ucb
	looks=$(_ab_env_value AB_GATE_LOOKS)
	maxb=$(_ab_env_value AB_GATE_MAX_BLOCKS)
	fut=$(_ab_env_value AB_GATE_FUTILITY_UCB_DELTA)
	out=$(python3 tools/ab_decide.py --games "$AB_GAMES_FILE" --state "$AB_STATE_FILE" --json ${looks:+--looks "$looks"} ${maxb:+--max-blocks "$maxb"} ${fut:+--futility-delta "$fut"} 2>/dev/null) || { log "[AB-GATE] ab_decide 失敗"; return 0; }
	verdict=$(printf '%s' "$out" | python3 -c "import sys,json; print(json.load(sys.stdin).get('verdict',''))" 2>/dev/null)
	k=$(printf '%s' "$out" | python3 -c "import sys,json; print(json.load(sys.stdin).get('k',0))" 2>/dev/null)
	m=$(printf '%s' "$out" | python3 -c "import sys,json; v=json.load(sys.stdin).get('mean_diff'); print('-' if v is None else '%.0f'%v)" 2>/dev/null)
	ucb=$(printf '%s' "$out" | python3 -c "import sys,json; v=json.load(sys.stdin).get('ucb90'); print('-' if v is None else '%.0f'%v)" 2>/dev/null)
	log "[AB-GATE] k=$k mean(B-A)=$m ucb90=$ucb verdict=$verdict"
	case "$verdict" in
	ADOPT)
		if _ab_gate_dry_run; then
			log "[AB-GATE] (dry-run) would ADOPT B"
		else
			_ab_finish B "ADOPT k=$k mean=$m" || true
		fi
		;;
	REJECT_HARM | REJECT_FUTILE | REJECT_INCONCLUSIVE | ABORT)
		if _ab_gate_dry_run; then
			log "[AB-GATE] (dry-run) would finish A ($verdict)"
		else
			_ab_finish A "$verdict k=$k mean=$m" || true
		fi
		;;
	*) : ;;
	esac
	return 0
}
