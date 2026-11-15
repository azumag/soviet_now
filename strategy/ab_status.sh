#!/bin/bash
# strategy/ab_status.sh - A/B 実験状態を runtime/ab-state ブランチへ publish する (soviet_now#865)
#
# 目的: strategy.py を変える PR の merge 側から「本番で A/B が走っているか」を
# 機械的に見えるようにする。実験状態は tmp/state/ab_state.json にあるが、本番
# runtime は git 管理外 projection で repo 側から読めない。この層は実験
# lifecycle の各イベントで最小限の status JSON を組み立て、persist clone
# (SOREN_PERSIST_REPO) 経由で専用ブランチ runtime/ab-state の ab_status.json
# へ push する。.github/workflows/ab-guard.yml がそのブランチを読んで
# strategy.py / strategy_helpers を触る PR に警告を出す。
#
# 方針:
#   - best-effort: clone/git/ネットワークが無い環境では何もせず 0 を返し、
#     実験やゲームループを止めない。push 失敗は tmp/state/ab_status_publish.json
#     の last_error とログに残す。
#   - 秘匿情報を出さない: decide hash / 試合数 / ISO 時刻 / phase 名のみ。
#     絶対パス・ファイル内容・env の値は載せない。
#   - push は内容が変わった時、または AB_STATUS_HEARTBEAT_SEC (既定1800s) 経過時のみ。
#   - push は persist.lock (persist.sh と同じ flock) で直列化する。両者が共有する
#     worktree の checkout を同時に動かさないため。
#
# 環境変数:
#   AB_STATUS_PUBLISH_ENABLED=0        publish を無効化 (既定は有効)
#   AB_STATUS_BRANCH=runtime/ab-state  publish 先ブランチ
#   AB_STATUS_FILE=tmp/state/ab_status.json  ローカル status の置き場所
#   AB_STATUS_HEARTBEAT_SEC=1800       内容が変わらなくても push する間隔
#   SOREN_PERSIST_REPO                 publish に使う clone (既定 /home/ubuntu/soren-persist)

AB_STATUS_FILE="${AB_STATUS_FILE:-${TMP_STATE_DIR:-tmp/state}/ab_status.json}"
AB_STATUS_PUBLISH_STATE_FILE="${AB_STATUS_PUBLISH_STATE_FILE:-${TMP_STATE_DIR:-tmp/state}/ab_status_publish.json}"
AB_STATUS_BRANCH="${AB_STATUS_BRANCH:-runtime/ab-state}"
AB_STATUS_REPO_FILE="ab_status.json"

# stdout は _ab_status_push の戻り値 (commit sha) として capture されるため、
# ここの出力は必ず stderr へ送る。
_ab_status_log() {
	printf '%s\n' "[AB-STATUS] $*" >&2
}

# 現在の実験状態を最小限の JSON にする。実験が無ければ active=false だけを返す。
_ab_status_build() {
	local out_file="${1:-$AB_STATUS_FILE}"
	mkdir -p "$(dirname "$out_file")" 2>/dev/null || return 1
	python3 - "$out_file" "$AB_STATE_FILE" "$AB_ABORT_FILE" "$AB_CANDIDATE_DIR" "${STRATEGY_FILE:-strategy.py}" <<'PY' || return 1
import hashlib
import json
import os
import subprocess
import sys
import time

out_file, state_file, abort_file, cand_dir, strategy_file = sys.argv[1:6]

def _hash(path):
    try:
        r = subprocess.run(
            ["python3", "extract_decide_hash.py", path],
            capture_output=True, text=True, timeout=30,
        )
        h = r.stdout.strip()
        return h or None
    except Exception:
        return None

st = {}
try:
    with open(state_file, encoding="utf-8") as fh:
        st = json.load(fh)
except Exception:
    st = {}

games_file = os.path.join(os.path.dirname(state_file), "ab_games.jsonl")
ledger_rows = 0
try:
    with open(games_file, encoding="utf-8") as fh:
        ledger_rows = sum(1 for line in fh if line.strip())
except Exception:
    ledger_rows = 0

root_hash = _hash(strategy_file)
a_hash = st.get("a_hash") or None
status = {
    "schema_version": 1,
    "observed_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    "observed_epoch": int(time.time()),
    "active": bool(st),
    "aborted": os.path.exists(abort_file),
    "candidate_pending": os.path.isfile(os.path.join(cand_dir, "meta.json"))
    and os.path.isfile(os.path.join(cand_dir, "strategy.py")),
    "a_hash": a_hash,
    "b_hash": st.get("b_hash") or None,
    "pattern": st.get("pattern") or None,
    "primary": st.get("primary") or None,
    "started_at": st.get("started_at") or None,
    "games_recorded": int(st.get("games_recorded") or 0),
    "ledger_rows": ledger_rows,
    "confirmation_phase": st.get("confirmation_phase") or None,
    "confirmation_required": bool(st.get("confirmation_required")),
    "verified_improvement": bool(st.get("verified_improvement")),
    "retroactive": st.get("retroactive") or None,
    "root_hash": root_hash,
    "root_matches_a": (root_hash == a_hash) if a_hash else None,
}
tmp = out_file + ".tmp"
with open(tmp, "w", encoding="utf-8") as fh:
    json.dump(status, fh, ensure_ascii=False, indent=1)
    fh.write("\n")
os.replace(tmp, out_file)
# volatile フィールドを除いた fingerprint を stdout へ返す。
key = dict(status)
key.pop("observed_at", None)
key.pop("observed_epoch", None)
print(hashlib.sha256(json.dumps(key, sort_keys=True).encode()).hexdigest())
PY
}

# 直近 publish 状態 (content_key / published_at / commit / last_error) を読む。
_ab_status_publish_state_get() {
	python3 - "$AB_STATUS_PUBLISH_STATE_FILE" "$1" <<'PY' 2>/dev/null || echo ""
import json, sys
try:
    v = json.load(open(sys.argv[1])).get(sys.argv[2], "")
    print("" if v is None else v)
except Exception:
    print("")
PY
}

_ab_status_publish_state_write() {
	local content_key="$1" published_at="$2" commit="${3:-}" last_error="${4:-}"
	mkdir -p "$(dirname "$AB_STATUS_PUBLISH_STATE_FILE")" 2>/dev/null || return 0
	python3 - "$AB_STATUS_PUBLISH_STATE_FILE" "$content_key" "$published_at" "$commit" "$last_error" <<'PY' 2>/dev/null || true
import json, os, sys
p, content_key, published_at, commit, last_error = sys.argv[1:6]
d = {"content_key": content_key, "published_at": int(published_at or 0), "commit": commit or "", "last_error": last_error or ""}
tmp = p + ".tmp"
with open(tmp, "w", encoding="utf-8") as fh:
    json.dump(d, fh, ensure_ascii=False)
os.replace(tmp, p)
PY
}

# status JSON を persist clone の AB_STATUS_BRANCH へ commit + push する。
# persist.lock で persist_strategy_improve と直列化する (共有 worktree)。
_ab_status_push_locked() {
	local repo="$1" branch="$2" status_file="$3" remote_ref remote_head
	remote_ref="refs/remotes/origin/$branch"
	remote_head=""
	if git -C "$repo" fetch --quiet origin "refs/heads/$branch:$remote_ref" 2>/dev/null; then
		remote_head="$(git -C "$repo" rev-parse "$remote_ref" 2>/dev/null || true)"
	else
		git -C "$repo" update-ref -d "$remote_ref" >/dev/null 2>&1 || true
	fi
	# main を起点にブランチを再構築する。remote 側に履歴があればそれへ積む。
	local base="origin/main"
	[ -n "$remote_head" ] && base="$remote_ref"
	git -C "$repo" fetch --quiet origin main >/dev/null 2>&1 || true
	if ! git -C "$repo" checkout --quiet -B "$branch" "$base" >/dev/null 2>&1; then
		_ab_status_log "push: checkout -B $branch $base failed"
		return 1
	fi
	cp "$status_file" "$repo/$AB_STATUS_REPO_FILE" || return 1
	if ! git -C "$repo" add -- "$AB_STATUS_REPO_FILE" 2>/dev/null; then
		_ab_status_log "push: git add failed"
		return 1
	fi
	if git -C "$repo" diff --cached --quiet; then
		# 同一内容。heartbeat 時刻だけ publish state 側で進める。
		git -C "$repo" rev-parse HEAD 2>/dev/null || true
		return 0
	fi
	local msg
	msg="chore(ab): status $(python3 -c 'import json,sys; d=json.load(open(sys.argv[1])); print(d.get("observed_at","?"))' "$status_file" 2>/dev/null || echo update)"
	if ! git -C "$repo" commit --quiet -m "$msg" >/dev/null 2>&1; then
		_ab_status_log "push: commit failed"
		return 1
	fi
	if [ -n "$remote_head" ]; then
		git -C "$repo" push --quiet \
			--force-with-lease="refs/heads/$branch:$remote_head" \
			origin "HEAD:refs/heads/$branch" >/dev/null 2>&1 && {
			git -C "$repo" rev-parse HEAD 2>/dev/null || true
			return 0
		}
	else
		git -C "$repo" push --quiet origin "HEAD:refs/heads/$branch" >/dev/null 2>&1 && {
			git -C "$repo" rev-parse HEAD 2>/dev/null || true
			return 0
		}
	fi
	_ab_status_log "push: git push failed (conflict or network)"
	return 1
}

_ab_status_push() {
	local repo="${SOREN_PERSIST_REPO:-/home/ubuntu/soren-persist}"
	local status_file="$1"
	[ -d "$repo/.git" ] || return 2
	command -v git >/dev/null 2>&1 || return 2
	# persist と同じく repo-local identity を lazy に用意する。
	if ! git -C "$repo" config user.email >/dev/null 2>&1; then
		git -C "$repo" config user.name "${SOREN_PERSIST_USER_NAME:-docich-vm}" >/dev/null 2>&1 || return 1
		git -C "$repo" config user.email "${SOREN_PERSIST_USER_EMAIL:-9018513+azumag@users.noreply.github.com}" >/dev/null 2>&1 || return 1
	fi
	local rc=0
	if command -v flock >/dev/null 2>&1; then
		(
			exec 9>>"$repo/.git/persist.lock" || exit 1
			flock 9 || exit 1
			_ab_status_push_locked "$repo" "$AB_STATUS_BRANCH" "$status_file" || exit $?
		)
		rc=$?
	else
		_ab_status_push_locked "$repo" "$AB_STATUS_BRANCH" "$status_file"
		rc=$?
	fi
	return "$rc"
}

# lifecycle イベントから呼ぶ本体。内容が変わった時か heartbeat 間隔経過時のみ push。
# 失敗しても呼び出し側の実験・ゲーム進行には影響させない。
_ab_status_publish() {
	local reason="${1:-event}"
	[ "$(_ab_env_value AB_STATUS_PUBLISH_ENABLED)" = "0" ] && return 0
	local now heartbeat key last_key published_at
	now=$(date +%s)
	key=$(_ab_status_build "$AB_STATUS_FILE") || {
		_ab_status_log "status build failed ($reason)"
		_ab_status_publish_state_write "" "$now" "" "build_failed"
		return 0
	}
	last_key=$(_ab_status_publish_state_get content_key)
	published_at=$(_ab_status_publish_state_get published_at)
	case "$published_at" in '' | *[!0-9]*) published_at=0 ;; esac
	heartbeat="${AB_STATUS_HEARTBEAT_SEC:-1800}"
	case "$heartbeat" in '' | *[!0-9]*) heartbeat=1800 ;; esac
	if [ -n "$last_key" ] && [ "$last_key" = "$key" ] && [ $((now - published_at)) -lt "$heartbeat" ]; then
		return 0
	fi
	local push_rc commit_out
	commit_out=$(_ab_status_push "$AB_STATUS_FILE" 2>/dev/null)
	push_rc=$?
	if [ "$push_rc" -eq 2 ]; then
		# clone 非整備の環境 (dev checkout / CI)。status file だけ残して終わる。
		return 0
	fi
	if [ "$push_rc" -eq 0 ]; then
		_ab_status_publish_state_write "$key" "$now" "$(printf '%s' "$commit_out" | tail -n1)" ""
		_ab_status_log "published ${AB_STATUS_BRANCH} ($reason)"
	else
		_ab_status_publish_state_write "$key" "$published_at" "" "push_failed"
		_ab_status_log "publish failed ($reason) → 次回イベントで再試行"
	fi
	return 0
}

# ゲームループを止めないよう publish は裏で行う。
_ab_status_publish_async() {
	( _ab_status_publish "${1:-event}" ) >/dev/null 2>&1 &
	return 0
}
