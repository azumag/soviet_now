#!/usr/bin/env bash
# tests/test_codex_agent_env_redaction.sh - docich#39
#
# codex_bug_dispatcher.sh が実際にCoding Agent(codex)を起動する経路
# (CODEX_BUG_DISPATCH_ENABLED=1) で、
#   1) agentプロセスのprocess environmentにcredential sentinelが渡らない
#      (env -i + non-secret allowlistで起動している)
#   2) agentへ渡すprompt / agentが書くoutput / dispatcherのlogのいずれにも
#      sentinelの生値が最終成果物として残らない(secret redactorが効く)
#   3) 機能自体(queue処理・成否記録)は壊れていない
# ことを、ダミーの sentinel を使って実測する。
#
# codexは実際には起動せず、PATHにspyスタブを挿す。spyはenv -i配下で
# 動くため、置換された値を自分の環境変数からは読めない(=独自の秘密は
# 持ち得ない)。argv中の `-o <path>` から出力先を特定し、その隣に自分の
# process environmentをダンプする(argvはenv -iの影響を受けないためspy
# でも見える。これは本物のcodexプロセスにも同じことが言える=agentの
# 起動引数自体は元々non-secretで、今回の変更点はenvironmentの方)。
#
# 注意: core/config.sh は CODEX_BUG_DISPATCH_LOG_DIR / _LOCK_DIR /
# _LAST_FILE を(CODEX_BUG_QUEUE_DIRと違い)env上書き不可な形で
# $TMP_STATE_DIR/$TMP_DEBUG_DIR から常に導出する(docich#39とは無関係な
# 既存の挙動)。そのため、このリポジトリ(このworktree)配下の実パス
# tmp/debug/codex_bug_dispatch 等を実際に使い、実行前後のfind差分で
# このテストが作った成果物だけを特定し、テスト終了時に必ず削除する。

set -u

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TMP="$(mktemp -d)"

REAL_LOG_DIR="$ROOT/tmp/debug/codex_bug_dispatch"
REAL_LOCK_DIR="$ROOT/tmp/state/.codex_bug_dispatch.lock"
REAL_LAST_FILE="$ROOT/tmp/state/codex_bug_dispatch_last.ts"
MARKER="$TMP/.marker"

cleanup() {
	# このテストが作った成果物(マーカーより新しいファイル)だけを削除する。
	if [ -d "$REAL_LOG_DIR" ]; then
		find "$REAL_LOG_DIR" -maxdepth 1 -type f -newer "$MARKER" -exec rm -f {} + 2>/dev/null
	fi
	rm -rf "$REAL_LOCK_DIR" 2>/dev/null
	[ -f "$REAL_LAST_FILE" ] && [ "$REAL_LAST_FILE" -nt "$MARKER" ] && rm -f "$REAL_LAST_FILE" 2>/dev/null
	rm -rf "$TMP"
}
trap cleanup EXIT

ok=0
fail=0
check() {
	local condition="$1" message="$2"
	if eval "$condition"; then
		printf 'ok - %s\n' "$message"
		ok=$((ok + 1))
	else
		printf 'not ok - %s\n' "$message"
		fail=$((fail + 1))
	fi
}

DISPATCHER="$ROOT/codex_bug_dispatcher.sh"
SENTINEL="SENTINEL_CODEX_ENV_DO_NOT_LEAK_3fa8c1"

mkdir -p "$REAL_LOG_DIR"
sleep 1.1 # ファイルmtimeの解像度差を避けるため、マーカーと実行の間に余裕を持たせる
touch "$MARKER"

# --- spy codex stub -------------------------------------------------------
SPYBIN="$TMP/spybin"
mkdir -p "$SPYBIN"
cat >"$SPYBIN/codex" <<EOF
#!/bin/sh
out=""
prev=""
for a in "\$@"; do
	if [ "\$prev" = "-o" ]; then out="\$a"; fi
	prev="\$a"
done
if [ -n "\$out" ]; then
	dir=\$(dirname "\$out")
	env >"\$dir/spy_env_dump_test39.txt"
	printf 'agent finished. debug note: $SENTINEL\n' >"\$out"
fi
exit 1
EOF
chmod +x "$SPYBIN/codex"
export PATH="$SPYBIN:$PATH"

# --- fake viewer report (comment自体にもsentinelを混入させ、prompt経由の
#     漏れも同時に検証する) --------------------------------------------------
QUEUE_DIR="$TMP/queue"
mkdir -p "$QUEUE_DIR"
now_ts=$(date +%s)
python3 - "$QUEUE_DIR" "$now_ts" "$SENTINEL" <<'PY'
import json
import sys
from pathlib import Path

queue_dir, now_ts, sentinel = sys.argv[1], int(sys.argv[2]), sys.argv[3]
data = {
	"created_at": now_ts,
	"source": "twitch",
	"batch_hash": "b1",
	"hash": "deadbeef0",
	"index": 1,
	"user": "viewer_test",
	"comment": f"OBSソースが落ちた。ついでにこの文字列も混入: {sentinel}",
	"category": "stream_bug_report",
	"status": "pending",
}
Path(queue_dir, f"{now_ts}_deadbeef0.json").write_text(
	json.dumps(data, ensure_ascii=False), encoding="utf-8"
)
PY

# --- dispatcherの実shellが持つ「credential相当」の環境変数 ----------------
# (本番の.envがsourceされた状態を模す。名前にTOKEN/SECRET等を含めることで
#  lib/secret_redactor.pyのname-basedマッチ対象になる)
export SOREN_TEST_TWITCH_OAUTH_TOKEN="$SENTINEL"

export CODEX_BUG_QUEUE_DIR="$QUEUE_DIR"
export CODEX_BUG_QUARANTINE_DIR="$QUEUE_DIR/quarantined"
export CODEX_BUG_QUARANTINE_NOTICE_FILE="$TMP/quarantine_notice.log"
export CODEX_BUG_DISPATCH_ENABLED=1
export CODEX_BUG_DISPATCH_MIN_INTERVAL_SEC=0

bash "$DISPATCHER" run >"$TMP/run.out" 2>"$TMP/run.err"

# ---------------------------------------------------------------------------
# A. 機能確認: enabled時はqueueが処理され、spyが呼ばれ、failed(rc!=0)扱いになる
# ---------------------------------------------------------------------------
spy_env_dump="$REAL_LOG_DIR/spy_env_dump_test39.txt"
spy_called=0
[ -f "$spy_env_dump" ] && spy_called=1
check "[ \"$spy_called\" -eq 1 ]" "CODEX_BUG_DISPATCH_ENABLED=1でcodex(spy)が実際に起動される"

failed_count=$(find "$QUEUE_DIR/failed" -maxdepth 1 -type f -name '*.json' 2>/dev/null | wc -l | tr -d ' ')
check "[ \"$failed_count\" -eq 1 ]" "spyがrc!=0を返すとreportがfailedとして記録される (実測: ${failed_count}件)"

prompt_files=$(find "$REAL_LOG_DIR" -maxdepth 1 -type f -name 'prompt_*' -newer "$MARKER" 2>/dev/null)
output_files=$(find "$REAL_LOG_DIR" -maxdepth 1 -type f -name 'last_*' -newer "$MARKER" 2>/dev/null)
log_files=$(find "$REAL_LOG_DIR" -maxdepth 1 -type f -name 'run_*.log' -newer "$MARKER" 2>/dev/null)
check "[ -n \"\$prompt_files\" ]" "prompt成果物が生成される"
check "[ -n \"\$output_files\" ]" "output成果物が生成される"
check "[ -n \"\$log_files\" ]" "log成果物が生成される"

# ---------------------------------------------------------------------------
# B. agent(spy)自身のprocess environmentにsentinelが渡らない (env -i + allowlist)
# ---------------------------------------------------------------------------
if [ "$spy_called" -eq 1 ]; then
	check "! grep -q '$SENTINEL' \"$spy_env_dump\"" \
		"Coding Agent(spy)自身のprocess environmentにsentinelが現れない (実測: $(wc -l < "$spy_env_dump" | tr -d ' ')行の環境を確認)"
	check "grep -q '^PATH=' \"$spy_env_dump\"" \
		"allowlistの効果でPATHはagentに渡っている(env -iで単純に全消ししたのではない)"
	check "grep -q '^HOME=' \"$spy_env_dump\"" \
		"allowlistの効果でHOMEはagentに渡っている"
	check "! grep -qi 'TWITCH\\|OAUTH\\|SECRET\\|CLIENT_ID' \"$spy_env_dump\"" \
		"credential系の変数名がagentのprocess environmentに一切現れない"
else
	check "false" "(spy未起動のためB系4件は未実測)"
	check "false" "(同上)"
	check "false" "(同上)"
	check "false" "(同上)"
fi

# ---------------------------------------------------------------------------
# C. 最終成果物(prompt/output/log)にsentinelの生値が残らない (redactor)
# ---------------------------------------------------------------------------
if [ -n "$prompt_files" ]; then
	check "! grep -q '$SENTINEL' $prompt_files" \
		"promptの最終成果物にsentinelの生値が残らない(comment経由の混入もredact)"
	check "grep -q 'REDACTED' $prompt_files" \
		"promptの最終成果物にREDACTEDマーカーが記録される"
else
	check "false" "(prompt未生成のため未実測)"
	check "false" "(同上)"
fi
if [ -n "$output_files" ]; then
	check "! grep -q '$SENTINEL' $output_files" \
		"outputの最終成果物にsentinelの生値が残らない(agent自身の出力もredact)"
else
	check "false" "(output未生成のため未実測)"
fi
if [ -n "$log_files" ]; then
	check "! grep -q '$SENTINEL' $log_files" \
		"logの最終成果物にsentinelの生値が残らない"
else
	check "false" "(log未生成のため未実測)"
fi

# raw_*(redact前の一時ファイル)がlog_dir配下に残っていない(掃除されている)こと
raw_leftover=$(find "$REAL_LOG_DIR" -maxdepth 1 -type f -name '.raw_*' -newer "$MARKER" 2>/dev/null | wc -l | tr -d ' ')
check "[ \"$raw_leftover\" -eq 0 ]" \
	"redact前の一時ファイル(.raw_*)が最終的に残っていない (実測: ${raw_leftover}件)"

printf '1..%d\n' "$((ok + fail))"
printf '%s passed, %s failed\n' "$ok" "$fail"
[ "$fail" -eq 0 ]
