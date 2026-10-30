# lib/run_journal.sh - strategy run journal の shadow 記録ラッパー (soviet_now#145)
# accumulate→evaluate の判断を append-only journal へ残すだけの shadow 経路。
# 全関数は失敗しても rc を握り、旧実装の acc/rolling/run 状態へ影響しない。

_run_journal_root() {
	printf '%s\n' "${ELOOP_LIB_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
}

_run_journal_path() {
	printf '%s\n' "${STRATEGY_RUN_JOURNAL:-${TMP_STATE_DIR:-tmp/state}/strategy_run_journal.jsonl}"
}

# _journal_begin <hash> <reason>
# 新しい評価 run を journal に開始する。同一 run_id が open のまま既に
# あれば journal 側で拒否される（重複開始の shadow 検出）。
_journal_begin() {
	local root
	root="$(_run_journal_root)"
	python3 "$root/lib/strategy_run_journal.py" \
		--journal "$(_run_journal_path)" begin "$1" \
		--reason "$2" --owner "${STRATEGY_RUN_OWNER:-strategy}" \
		${3:+--input-hash "$3"} >/dev/null 2>&1 || true
}

# _journal_append <run_id> <from_state> <to_state> <reason> [evidence_json]
_journal_append() {
	local root
	root="$(_run_journal_root)"
	python3 "$root/lib/strategy_run_journal.py" \
		--journal "$(_run_journal_path)" append "$1" \
		--from-state "$2" --to-state "$3" --reason "$4" \
		--owner "${STRATEGY_RUN_OWNER:-strategy}" \
		${5:+--evidence-json "$5"} >/dev/null 2>&1 || true
}

# _journal_finish <run_id> <outcome> <reason> [output_hash] [evidence_json]
_journal_finish() {
	local root
	root="$(_run_journal_root)"
	python3 "$root/lib/strategy_run_journal.py" \
		--journal "$(_run_journal_path)" finish "$1" \
		--to-state "$2" --reason "$3" \
		--owner "${STRATEGY_RUN_OWNER:-strategy}" \
		${4:+--output-hash "$4"} \
		${5:+--evidence-json "$5"} >/dev/null 2>&1 || true
}
