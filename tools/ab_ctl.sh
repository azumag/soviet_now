#!/usr/bin/env bash
# tools/ab_ctl.sh - インターリーブ A/B の開始/状況/停止/終了 (soren ルートで実行)。
#   start <path|hash> [pattern]  root(A) と代替(B) の A/B を次の試合から開始 (REGRESSION_DISABLED=1、improve を pause)
#   status                        腕ごとの集計 (tools/ab_report.py)
#   stop                          B 腕を止める (記録は残す; 次の試合から root のみ)
#   finish <A|B>                  勝者を root に確定し、gate/improve を元に戻す (B なら root を差し替え)
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
		# hash 指定: by_hash / 永久アーカイブから解決 (eloop.sh の _find_strategy_archive_for_hash は eloop_lib では読めない)
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
	[ -f "$IMPROVE_LOCK_FILE" ] && { echo "improve.lock があります。改善が終わってから (or 手動で回収)。"; exit 1; }
	[ -f "$ACTIVE_BRANCH_FILE" ] && { echo "active_branch.json があります。_clear_active_branch 相当の整理が必要。"; exit 1; }
	a=$(hash_of "$STRATEGY_FILE"); b=$(hash_of "$src")
	[ -n "$a" ] && [ -n "$b" ] || { echo "hash 取得失敗 (a=$a b=$b)"; exit 1; }
	[ "$a" != "$b" ] || { echo "代替戦略の hash が root と同じです ($a)"; exit 1; }
	if ! validate_strategy_with_helpers "$src" strategy_helpers >/dev/null 2>&1; then echo "代替戦略がバリデータで拒否されました"; exit 1; fi
	python3 -m py_compile "$src" || exit 1
	mkdir -p "$TMP_STATE_DIR"
	cp -p "$src" "$ALT"
	cp -p "$STRATEGY_FILE" tmp/revert_strategy.py
	_archive_strategy_snapshot_by_hash "$STRATEGY_FILE" >/dev/null 2>&1 || true
	_archive_strategy_snapshot_by_hash "$ALT" >/dev/null 2>&1 || true
	touch "$TMP_STATE_DIR/improve_daemon.paused"
	rm -f "$ABORT"
	: > "$GAMES"
	python3 - "$STATE" "$a" "$b" "$pattern" "$src" "${GAME_COUNT_FILE:-game_count.txt}" <<'PY'
import json, sys, time, os
def _count(p):
    try:
        return int(open(p).read().strip())
    except Exception:
        return None
st = {"a_hash": sys.argv[2], "b_hash": sys.argv[3], "pattern": sys.argv[4], "alt_source": sys.argv[5], "started_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
      "games_recorded": 0, "game_num_start": _count(sys.argv[6])}
json.dump(st, open(sys.argv[1], "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print(json.dumps(st, ensure_ascii=False))
PY
	./set_toggle.sh REGRESSION_DISABLED=1 >/dev/null && ./set_toggle.sh "SOREN_AB_ALT_STRATEGY=$ALT" >/dev/null && ./set_toggle.sh "SOREN_AB_PATTERN=$pattern" >/dev/null || { echo "set_toggle 失敗"; exit 1; }
	echo "A/B 開始: A=$a (root) B=$b ($src) pattern=$pattern — 次の試合から有効。revert 先 tmp/revert_strategy.py=A"
	grep -E "^(REGRESSION_DISABLED|SOREN_AB_ALT_STRATEGY|SOREN_AB_PATTERN)=" .env
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
	a=$(python3 -c "import json;print(json.load(open('$STATE')).get('a_hash',''))"); b=$(python3 -c "import json;print(json.load(open('$STATE')).get('b_hash',''))")
	./set_toggle.sh "SOREN_AB_ALT_STRATEGY=" >/dev/null || exit 1
	if [ "$winner" = "B" ]; then
		[ "$(hash_of "$ALT")" = "$b" ] || { echo "代替ファイルの hash が state と違います"; exit 1; }
		cp -p "$STRATEGY_FILE" tmp/revert_strategy.py
		strategy_runtime_atomic_apply "$ALT" "$STRATEGY_FILE" || { echo "root 差し替え失敗"; exit 1; }
		[ "$(hash_of "$STRATEGY_FILE")" = "$b" ] || { echo "差し替え後の hash 不一致"; exit 1; }
		_archive_strategy_snapshot_by_hash "$STRATEGY_FILE" >/dev/null 2>&1 || true
		win_hash="$b"
	else
		win_hash="$a"
	fi
	_clear_accumulated_data >/dev/null 2>&1 || true
	_seed_current_strategy_run_from_rolling "$win_hash" >/dev/null 2>&1 || true
	_promote_current_strategy_to_anchor "$win_hash" >/dev/null 2>&1 || true
	_refresh_best_strategy_anchor "" >/dev/null 2>&1 || true
	./set_toggle.sh REGRESSION_DISABLED=0 >/dev/null || exit 1
	rm -f "$TMP_STATE_DIR/improve_daemon.paused" "$ABORT"
	mkdir -p tmp/history
	ts=$(date +%Y%m%d_%H%M%S)
	python3 tools/ab_report.py --games "$GAMES" --state "$STATE" | tee "tmp/history/ab_${ts}_report.txt"
	mv "$STATE" "tmp/history/ab_${ts}_state.json"; cp "$GAMES" "tmp/history/ab_${ts}_games.jsonl"
	echo "A/B 終了: winner=$winner root=$(hash_of "$STRATEGY_FILE") (記録 tmp/history/ab_${ts}_*)。リポジトリ側の strategy.py も合わせて commit すること。"
	;;
*)
	echo "usage: $0 start <path|hash> [pattern] | status | stop | finish <A|B>"
	exit 1
	;;
esac
