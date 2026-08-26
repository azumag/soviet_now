# strategy/ab_interleave.sh - インターリーブ A/B (試合ごとに root(A) と代替戦略(B) を交互に実プレイ)
#
# 背景 (2026-08-26): 時間順の窓比較は ±150–250 の漂流があり戦略差 (<300) を測れない (v732/v733・静止待ち・
# v738 で帰属を誤った)。runner は毎試合 root ではなくスナップショット (strategy_runtime_create_game_snapshot)
# を読み、帳簿 (rolling_scores / version / played hash) もスナップショットの hash で付くので、A/B は
# 「スナップショットの元ファイルを試合ごとに選ぶ」だけで root には触れない。
#
# 有効条件 (すべて満たすときだけ B 腕を混ぜる。満たさなければ黙って root だけを打つ = fail-closed):
#   SOREN_AB_ALT_STRATEGY が非空 / ab_state.json あり / abort マーカーなし / .env の REGRESSION_DISABLED=1 /
#   improve_daemon.paused あり / improve.lock なし / 代替ファイルの hash が state.b_hash / root の hash が state.a_hash
# 腕の選択: SOREN_AB_PATTERN (既定 ABBA) を games_recorded (記録済み試合数) で巡回。不成立試合 (rc 75) は
# 記録されないので同じ腕を打ち直し、ブロックの均衡が保たれる。
AB_STATE_FILE="${AB_STATE_FILE:-${TMP_STATE_DIR:-tmp/state}/ab_state.json}"
AB_GAMES_FILE="${AB_GAMES_FILE:-${TMP_STATE_DIR:-tmp/state}/ab_games.jsonl}"
AB_ALT_FILE="${AB_ALT_FILE:-${TMP_STATE_DIR:-tmp/state}/ab_alt_strategy.py}"
AB_ABORT_FILE="${AB_ABORT_FILE:-${TMP_STATE_DIR:-tmp/state}/ab_abort}"
AB_ARM=""
AB_HASH=""
AB_SOURCE=""
AB_IDX=""
AB_HELPERS=""

_ab_hash() {
	python3 extract_decide_hash.py "$1" 2>/dev/null || echo ""
}

# .env の実効値 (core/config.sh は REGRESSION_DISABLED=0 を固定するので変数ではなく .env を見る)
_ab_env_value() {
	local key="$1" line
	line=$(grep -E "^${key}=" "${AB_ENV_FILE:-.env}" 2>/dev/null | tail -n 1) || true
	line="${line#*=}"
	line="${line%\"}"
	line="${line#\"}"
	line="${line%\'}"
	line="${line#\'}"
	printf '%s' "$line"
}

_ab_state_get() {
	python3 - "$AB_STATE_FILE" "$1" <<'PY' 2>/dev/null || echo ""
import json, sys
try:
    d = json.load(open(sys.argv[1]))
    v = d.get(sys.argv[2], "")
    print("" if v is None else v)
except Exception:
    print("")
PY
}

_ab_active() {
	local reason="" a b ha hb
	[ -n "${SOREN_AB_ALT_STRATEGY:-}" ] || return 1
	if [ -f "$AB_ABORT_FILE" ]; then
		reason="abort marker"
	elif [ ! -f "$AB_STATE_FILE" ]; then
		reason="no state file"
	elif [ "$(_ab_env_value REGRESSION_DISABLED)" != "1" ]; then
		reason="REGRESSION_DISABLED!=1 in .env"
	elif [ ! -f "${TMP_STATE_DIR:-tmp/state}/improve_daemon.paused" ] && ! { command -v _ab_gate_enabled >/dev/null 2>&1 && _ab_gate_enabled; }; then
		reason="improve daemon not paused (and AB gate off)"
	elif [ -f "${IMPROVE_LOCK_FILE:-tmp/improve.lock}" ] && ! { command -v _ab_gate_enabled >/dev/null 2>&1 && _ab_gate_enabled; }; then
		reason="improve lock present (and AB gate off)"
	elif [ ! -f "$AB_ALT_FILE" ]; then
		reason="alt strategy file missing"
	else
		a=$(_ab_state_get a_hash)
		b=$(_ab_state_get b_hash)
		ha=$(_ab_hash "${STRATEGY_FILE:-strategy.py}")
		hb=$(_ab_hash "$AB_ALT_FILE")
		if [ -z "$a" ] || [ "$ha" != "$a" ]; then
			reason="root hash ${ha:0:12} != a_hash ${a:0:12}"
		elif [ -z "$b" ] || [ "$hb" != "$b" ]; then
			reason="alt hash ${hb:0:12} != b_hash ${b:0:12}"
		fi
	fi
	if [ -n "$reason" ]; then
		log "[AB] inactive: $reason"
		return 1
	fi
	return 0
}

_ab_select_arm() {
	local pattern n
	pattern=$(printf '%s' "${SOREN_AB_PATTERN:-ABBA}" | tr -cd 'AB')
	[ -n "$pattern" ] || pattern="AB"
	n=$(_ab_state_get games_recorded)
	case "$n" in
	'' | *[!0-9]*) n=0 ;;
	esac
	AB_IDX="$n"
	AB_ARM="${pattern:$((n % ${#pattern})):1}"
	AB_HELPERS=""
	if [ "$AB_ARM" = "B" ]; then
		AB_SOURCE="$AB_ALT_FILE"
		AB_HASH=$(_ab_state_get b_hash)
		[ -d "${AB_ALT_HELPERS_DIR:-${TMP_STATE_DIR:-tmp/state}/ab_alt_helpers}" ] && AB_HELPERS="${AB_ALT_HELPERS_DIR:-${TMP_STATE_DIR:-tmp/state}/ab_alt_helpers}"
	else
		AB_ARM="A"
		AB_SOURCE="${STRATEGY_FILE:-strategy.py}"
		AB_HASH=$(_ab_state_get a_hash)
	fi
	log "[AB] idx=$AB_IDX arm=$AB_ARM hash=${AB_HASH:0:12} src=$AB_SOURCE${AB_HELPERS:+ helpers=$AB_HELPERS}"
}

# 腕・hash・スコアを ab_games.jsonl に追記し games_recorded を進める。
# runner が実際にその腕を打ったかを snapshot 記録と archive の strategy_hash で突き合わせ、不一致は tainted。
_ab_record_game() {
	local score="$1" eval="$2" turns="$3" archive="$4" played="" hist=""
	[ -n "${AB_ARM:-}" ] || return 0
	if [ -f "${STRATEGY_FILE:-strategy.py}.game_snapshot" ]; then
		played=$(_ab_hash "${STRATEGY_FILE:-strategy.py}.game_snapshot")
	fi
	if [ -n "$archive" ] && [ -f "$archive" ]; then
		hist=$(python3 - "$archive" <<'PY' 2>/dev/null || echo ""
import json, sys
for line in open(sys.argv[1], encoding="utf-8"):
    try:
        h = json.loads(line).get("strategy_hash")
    except Exception:
        continue
    if h:
        print(h)
        break
PY
)
	fi
	python3 - "$AB_STATE_FILE" "$AB_GAMES_FILE" "$AB_IDX" "$AB_ARM" "$AB_HASH" "$played" "$hist" "$score" "$eval" "$turns" "$archive" "${GAME_NUM:-}" <<'PY' || log "[AB] record failed"
import json, os, sys, time
state_file, games_file, idx, arm, h, played, hist, score, ev, turns, archive, game_num = sys.argv[1:13]
def num(v):
    try:
        return float(v)
    except Exception:
        return None
tainted = bool((played and played != h) or (hist and hist != h))
rec = {"idx": int(idx or 0), "arm": arm, "hash": h, "played_hash": played, "history_hash": hist, "score": num(score),
       "eval": num(ev), "turns": num(turns), "archive": os.path.basename(archive) if archive else "", "game_num": game_num,
       "ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "tainted": tainted}
# 試合ごとの指標 (game_history は直近しか残らないので記録時に取り出す)
try:
    rs = []
    with open(archive, encoding="utf-8") as fh:
        for line in fh:
            try:
                rs.append(json.loads(line))
            except Exception:
                pass
    if rs:
        merges = 0
        multi = 0
        for i in range(1, len(rs)):
            m = 1 - ((rs[i].get("piece_count") or 0) - (rs[i - 1].get("piece_count") or 0))
            if m >= 1:
                merges += m
            if m >= 2:
                multi += 1
        mx = 0
        for r in rs:
            for p in (r.get("state_snapshot") or {}).get("pieces") or []:
                mx = max(mx, p.get("type", 0) or 0)
        pc = {r.get("turn"): r.get("piece_count") for r in rs}
        rec.update({"merges_per_turn": round(merges / max(1, len(rs)), 4), "multi_merge_turns": multi,
                    "pieces_at_20": pc.get(20), "pieces_at_40": pc.get(40), "max_type": mx, "t14": int(mx >= 14), "t15": int(mx >= 15),
                    "crossings": sum(1 for r in rs if r.get("decision_crosses_deadline"))})
except Exception:
    pass
with open(games_file, "a", encoding="utf-8") as fh:
    fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
try:
    st = json.load(open(state_file))
except Exception:
    st = {}
st["games_recorded"] = int(st.get("games_recorded", 0) or 0) + 1
st["last_arm"] = arm
tmp = state_file + ".tmp"
with open(tmp, "w", encoding="utf-8") as fh:
    json.dump(st, fh, ensure_ascii=False, indent=1)
os.replace(tmp, state_file)
print("[AB] recorded idx=%s arm=%s eval=%s tainted=%s" % (idx, arm, ev, tainted))
PY
}

_ab_abort() {
	local reason="$1"
	touch "$AB_ABORT_FILE"
	python3 - "$AB_STATE_FILE" "$reason" <<'PY' 2>/dev/null || true
import json, os, sys, time
try:
    st = json.load(open(sys.argv[1]))
except Exception:
    st = {}
st["aborted_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
st["abort_reason"] = sys.argv[2]
tmp = sys.argv[1] + ".tmp"
json.dump(st, open(tmp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
os.replace(tmp, sys.argv[1])
PY
	log "[AB] abort: $reason (B 腕は以後打たない; ab_abort を消すまで root のみ)"
}

_ab_is_arm_hash() {
	local h="$1" a b
	[ -n "$h" ] || return 1
	a=$(_ab_state_get a_hash)
	b=$(_ab_state_get b_hash)
	[ "$h" = "$a" ] || [ "$h" = "$b" ]
}
