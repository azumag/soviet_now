#!/bin/bash
# strategy/ab_retroactive.sh - 直接 merge / stale 退役で判定を逃した変更へ
# 後追いの実戦 A/B 比較を付ける (soviet_now#865)。
#
# 2 つの経路で戦略が root になる:
#   - 改善ループ候補 → selection + confirmation の A/B (ab_gate.sh)
#   - PR の直接 merge → deploy reconcile → 実戦比較なしで root 置き換え
# 後者や stale 退役で判定を逃した変更には、この層の関数で「現 root (A) vs
# 対象 (B)」のインターリーブ A/B を後から開始し、実戦比較を付けられる。
#
# 関数 (tools/ab_retroactive.sh の薄い CLI が呼ぶ):
#   _ab_retro_list                     tmp/history の stale 退役実験を一覧
#   _ab_retro_show <ts>                退役実験の詳細と推奨コマンドを表示
#   _ab_retro_prev_roots [n] [--repo]  git 履歴から直近 n 個の root decide-hash を解決
#   _ab_retro_start <src> [pattern] [note] [prov_json]
#                                      現 root を A、src (path|hash) を B として A/B 開始
#   _ab_retro_start_stale <ts>         退役実験の alt file を B として A/B 開始

# tmp/history/ab_<ts>_state_stale.json を走査して 1 行/実験で表示する。
_ab_retro_list() {
	local history="${AB_HISTORY_DIR:-tmp/history}"
	[ -d "$history" ] || { echo "history がありません: $history"; return 1; }
	python3 - "$history" <<'PY'
import glob, json, os, sys
history = sys.argv[1]
rows = []
for p in sorted(glob.glob(os.path.join(history, "ab_*_state_stale.json"))):
    base = os.path.basename(p)
    ts = base[len("ab_"):-len("_state_stale.json")]
    try:
        st = json.load(open(p, encoding="utf-8"))
    except Exception:
        st = {}
    games_file = os.path.join(history, "ab_%s_games_stale.jsonl" % ts)
    n = 0
    try:
        n = sum(1 for line in open(games_file, encoding="utf-8") if line.strip())
    except Exception:
        pass
    has_alt = os.path.isfile(os.path.join(history, "ab_%s_alt_stale.py" % ts))
    rows.append({
        "ts": ts,
        "retired_reason": st.get("retired_reason") or "?",
        "phase": st.get("confirmation_phase") or "selection",
        "games": st.get("games_recorded") or n,
        "a": (st.get("a_hash") or "?")[:12],
        "b": (st.get("b_hash") or "?")[:12],
        "observed": (st.get("observed_hash") or "?")[:12],
        "alt_file": has_alt,
    })
if not rows:
    print("(stale 退役実験なし)")
    raise SystemExit(0)
for r in rows:
    print("ts=%s reason=%s phase=%s games=%s A=%s B=%s observed=%s alt=%s"
          % (r["ts"], r["retired_reason"], r["phase"], r["games"],
             r["a"], r["b"], r["observed"], r["alt_file"]))
PY
}

_ab_retro_show() {
	local ts="$1" history="${AB_HISTORY_DIR:-tmp/history}"
	local state="$history/ab_${ts}_state_stale.json"
	[ -f "$state" ] || { echo "退役 state がありません: $state"; return 1; }
	python3 - "$state" <<'PY'
import json, sys
st = json.load(open(sys.argv[1], encoding="utf-8"))
keys = ("a_hash", "b_hash", "observed_hash", "expected_hash", "retired_reason",
        "abort_reason", "aborted_at", "confirmation_phase", "games_recorded",
        "started_at", "pattern", "primary", "selection_games",
        "selection_games_file", "verified_improvement")
for k in keys:
    if k in st:
        print("  %s: %s" % (k, st[k]))
PY
	local alt="$history/ab_${ts}_alt_stale.py"
	echo
	echo "後追い比較:"
	if [ -f "$alt" ]; then
		echo "  # 退役実験の B 腕 vs 現 root:"
		echo "  ./tools/ab_retroactive.sh start-stale $ts"
	fi
	echo "  # 直接 merge で置き換わった前の root vs 現 root:"
	echo "  ./tools/ab_retroactive.sh prev-roots        # 旧 root hash を解決"
	echo "  ./tools/ab_retroactive.sh start --b <hash>  # 旧 root を B 腕として開始"
	return 0
}

# git 履歴から直近 n 件の strategy.py decide-hash を解決する。
# --repo は管理 clone (既定 SOREN_PERSIST_REPO → /home/ubuntu/soren-persist)
# で、そこでは origin/main を見る。見つからなければ cwd の HEAD を見る。
_ab_retro_prev_roots() {
	local n="${1:-5}" repo="" ref=""
	case "$n" in '' | *[!0-9]*) n=5 ;; esac
	shift || true
	while [ $# -gt 0 ]; do
		case "$1" in
		--repo) repo="${2:-}"; shift 2 ;;
		*) shift ;;
		esac
	done
	if [ -z "$repo" ] && [ -d "${SOREN_PERSIST_REPO:-/home/ubuntu/soren-persist}/.git" ]; then
		repo="${SOREN_PERSIST_REPO:-/home/ubuntu/soren-persist}"
		ref="origin/main"
	fi
	if [ -z "$repo" ]; then
		repo="."
		ref="HEAD"
	fi
	git -C "$repo" rev-parse --git-dir >/dev/null 2>&1 || { echo "git repo がありません: $repo"; return 1; }
	git -C "$repo" rev-parse --verify "$ref" >/dev/null 2>&1 || ref="HEAD"
	python3 - "$repo" "$ref" "$n" <<'PY'
import subprocess, sys, tempfile, os
repo, ref, n = sys.argv[1], sys.argv[2], int(sys.argv[3])
def git(*a):
    return subprocess.check_output(["git", "-C", repo, *a], text=True, stderr=subprocess.DEVNULL)
shas = [s for s in git("log", "--format=%H", "-n", str(n * 4), ref, "--", "strategy.py").split() if s][: n * 4]
seen = set()
out = []
for sha in shas:
    if len(out) >= n:
        break
    try:
        src = git("show", "%s:strategy.py" % sha)
    except Exception:
        continue
    fd, tmp = tempfile.mkstemp(suffix=".py")
    try:
        with os.fdopen(fd, "w") as fh:
            fh.write(src)
        h = subprocess.run(["python3", "extract_decide_hash.py", tmp],
                           capture_output=True, text=True).stdout.strip()
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass
    if not h or h in seen:
        continue
    seen.add(h)
    subj = git("log", "-1", "--format=%cs %s", sha).strip()
    out.append("%s  %s  %s" % (h[:12], sha[:12], subj))
for line in out:
    print(line)
PY
}

# 現 root (A) vs src (B) の A/B を開始し、retroactive provenance を state に記録する。
# src は path または archive hash (strategy_archive_resolve_plaintext で解決)。
# helpers_src は任意: B 腕が依存する strategy_helpers を同梱する時に渡す。
_ab_retro_start() {
	local src="$1" pattern="${2:-ABBA}" note="${3:-}" prov="${4:-}" helpers_src="${5:-}"
	[ -n "$src" ] || { echo "usage: start <path|hash>"; return 1; }
	if [ ! -f "$src" ]; then
		local resolved=""
		resolved=$(strategy_archive_resolve_plaintext "$src" 2>/dev/null || true)
		[ -n "$resolved" ] || { echo "B 腕を解決できません (パスでも hash でも): $src"; return 1; }
		src="$resolved"
	fi
	[ -f "$AB_STATE_FILE" ] && { echo "既に A/B 状態があります ($AB_STATE_FILE)。finish/stop してから。"; return 1; }
	[ -f "${ACTIVE_BRANCH_FILE:-${TMP_STATE_DIR:-tmp/state}/active_branch.json}" ] && {
		echo "active_branch.json があります。先に整理が必要。"; return 1; }
	local bundle
	bundle=$(mktemp -d "${TMP_STATE_DIR:-tmp/state}/ab_retro_bundle.XXXXXX") || return 1
	if ! strategy_archive_copy "$src" "$bundle/strategy.py"; then
		rm -rf "$bundle"; echo "B 腕を bundle へ展開できません"; return 1
	fi
	if [ -n "$helpers_src" ] && [ -d "$helpers_src" ]; then
		cp -R "$helpers_src" "$bundle/strategy_helpers" || { rm -rf "$bundle"; return 1; }
	fi
	if _ab_start_from_bundle "$bundle" "$pattern"; then
		# state へ retroactive provenance を付ける。判定規則には触れない。
		python3 - "$AB_STATE_FILE" "$note" "$prov" "$src" <<'PY' 2>/dev/null || true
import json, os, sys, time
p, note, prov, src = sys.argv[1:5]
try:
    st = json.load(open(p, encoding="utf-8"))
except Exception:
    st = {}
retro = {"requested_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
         "requested_by": "ab_retroactive", "source": os.path.basename(src)}
if note:
    retro["note"] = note
try:
    extra = json.loads(prov) if prov else {}
except Exception:
    extra = {}
retro.update(extra)
st["retroactive"] = retro
tmp = p + ".tmp"
with open(tmp, "w", encoding="utf-8") as fh:
    json.dump(st, fh, ensure_ascii=False, indent=1)
os.replace(tmp, p)
PY
		rm -rf "$bundle"
		echo "retroactive A/B 開始: B=$src"
		return 0
	fi
	rm -rf "$bundle"
	echo "A/B 開始失敗 (ログ参照)"
	return 1
}

# 退役実験 ts の alt file (B 腕) を現 root との A/B として再測定する。
_ab_retro_start_stale() {
	local ts="$1" pattern="${2:-ABBA}" history="${AB_HISTORY_DIR:-tmp/history}"
	local state="$history/ab_${ts}_state_stale.json" alt="$history/ab_${ts}_alt_stale.py"
	local helpers="$history/ab_${ts}_alt_helpers_stale"
	[ -f "$state" ] || { echo "退役 state がありません: $state"; return 1; }
	[ -f "$alt" ] || { echo "退役実験の alt file がありません: $alt"; return 1; }
	local prov
	prov=$(python3 - "$state" "$ts" <<'PY' 2>/dev/null || echo "{}"
import json, sys
try:
    st = json.load(open(sys.argv[1], encoding="utf-8"))
except Exception:
    st = {}
print(json.dumps({
    "retro_for_ts": sys.argv[2],
    "original_a_hash": st.get("a_hash") or "",
    "original_b_hash": st.get("b_hash") or "",
    "retired_reason": st.get("retired_reason") or "",
    "original_observed_hash": st.get("observed_hash") or "",
    "original_phase": st.get("confirmation_phase") or "selection",
    "original_games": st.get("games_recorded") or 0,
}))
PY
)
	_ab_retro_start "$alt" "$pattern" "stale-retired B vs current root" "$prov" "$helpers"
}
