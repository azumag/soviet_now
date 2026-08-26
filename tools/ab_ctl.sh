#!/usr/bin/env bash
# tools/ab_ctl.sh - インターリーブ A/B の開始/状況/停止/終了 (soren ルートで実行)。
#   start <path|hash> [pattern]  root(A) と代替(B) の A/B を次の試合から開始 (REGRESSION_DISABLED=1、improve を pause)
#   status                        腕ごとの集計 (tools/ab_report.py)
#   stop                          B 腕を止める (記録は残す; 次の試合から root のみ)
#   finish <A|B> [reason]         勝者を root に確定し、gate を元に戻す (B なら root を差し替え)。improve pause は触らない
#   simulate <games.jsonl>        逐次判定の推移を再生 (tools/ab_decide.py --trail)
# 途中の手動デプロイは manual_meriken_mode の境界 pause を使う (A/B 状態は触らない)。
set -u
cd "$(dirname "$0")/.." || exit 1
source ./eloop_lib.sh >/dev/null 2>&1 || { echo "eloop_lib.sh を読めません"; exit 1; }
STATE="${AB_STATE_FILE:-$TMP_STATE_DIR/ab_state.json}"
GAMES="${AB_GAMES_FILE:-$TMP_STATE_DIR/ab_games.jsonl}"
ALT="${AB_ALT_FILE:-$TMP_STATE_DIR/ab_alt_strategy.py}"
ABORT="${AB_ABORT_FILE:-$TMP_STATE_DIR/ab_abort}"
hash_of() { python3 extract_decide_hash.py "$1" 2>/dev/null || echo ""; }

cmd="${1:-status}"
case "$cmd" in
start)
	src="${2:-}"
	pattern="${3:-ABBA}"
	[ -n "$src" ] || { echo "usage: $0 start <path|hash> [pattern]"; exit 1; }
	if [ ! -f "$src" ]; then
		found=""
		for cand in "${STRATEGY_HASH_ARCHIVE_DIR:-strategy_versions/by_hash}/${src}.py" "${STRATEGY_HASH_PERMANENT_ARCHIVE_DIR:-strategy_versions_archive/by_hash}/${src}.py"; do
			[ -f "$cand" ] || continue
			[ "$(hash_of "$cand")" = "$src" ] || continue
			found="$cand"
			break
		done
		[ -n "$found" ] || { echo "代替戦略が見つかりません (パスでも hash でも解決不可): $src"; exit 1; }
		src="$found"
	fi
	[ -f "$STATE" ] && { echo "既に A/B 状態があります ($STATE)。finish/stop してから。"; exit 1; }
	[ -f "$ACTIVE_BRANCH_FILE" ] && { echo "active_branch.json があります。先に整理が必要。"; exit 1; }
	bundle=$(mktemp -d "${TMP_STATE_DIR}/ab_bundle.XXXXXX")
	cp -p "$src" "$bundle/strategy.py"
	if _ab_start_from_bundle "$bundle" "$pattern"; then
		echo "A/B 開始: A=$(hash_of "$STRATEGY_FILE") (root) B=$(hash_of "$src") ($src) pattern=$pattern — 次の試合から有効。revert 先 tmp/revert_strategy.py=A"
		grep -E "^(REGRESSION_DISABLED|SOREN_AB_ALT_STRATEGY|SOREN_AB_PATTERN)=" .env
		rm -rf "$bundle"
	else
		rm -rf "$bundle"
		echo "A/B 開始失敗 (ログ参照)"
		exit 1
	fi
	;;
status)
	[ -f "$STATE" ] || { echo "A/B 状態なし"; exit 0; }
	python3 -c "import json;print(json.dumps(json.load(open('$STATE')),ensure_ascii=False))"
	python3 tools/ab_report.py --games "$GAMES" --state "$STATE"
	;;
stop)
	./set_toggle.sh "SOREN_AB_ALT_STRATEGY=" >/dev/null || exit 1
	echo "B 腕を停止 (次の試合から root のみ)。記録 $GAMES は残置。finish <A|B> で確定。"
	;;
finish)
	winner="${2:-}"
	[ "$winner" = "A" ] || [ "$winner" = "B" ] || { echo "usage: $0 finish <A|B>"; exit 1; }
	[ -f "$STATE" ] || { echo "A/B 状態なし"; exit 1; }
	_ab_finish "$winner" "manual:${3:-}" || { echo "finish 失敗 (ログ参照)"; exit 1; }
	echo "A/B 終了: winner=$winner root=$(hash_of "$STRATEGY_FILE")。B 採用ならリポジトリ側の strategy.py も合わせて commit すること。"
	;;
simulate)
	f="${2:-}"
	[ -f "$f" ] || { echo "usage: $0 simulate <ab_games.jsonl> [state.json]"; exit 1; }
	python3 tools/ab_decide.py --games "$f" --state "${3:-/dev/null}" --trail
	;;
*)
	echo "usage: $0 start <path|hash> [pattern] | status | stop | finish <A|B> [reason] | simulate <games.jsonl>"
	exit 1
	;;
esac
