#!/bin/bash
# lib/strategy_archive.sh - strategy ハッシュアーカイブ (.py / .py.gz) の
# 透過 reader ヘルパー (docich#392 Phase A)。
#
# writer は従来どおり平文 `<hash>.py` を書く。reader は候補パスを各
# ディレクトリごとに `<hash>.py.gz` → `<hash>.py` の順で解決し、読み出しは
# gz を透過的に解凍する。下流 (`cp` / `validate_strategy` / `grep` /
# `extract_decide_hash.py`) は平文を前提とするため、resolver は必要に応じて
# 作業アーカイブへ平文展開してから返す。
#
# `.py` しか無い現状では候補順・挙動ともに従来と同じ（純加算的）。

# 1ディレクトリ分の候補パスを gz 優先で出力する。
strategy_archive_dir_candidates() {
	local dir="$1" hash="$2"
	[ -n "$dir" ] || return 0
	[ -n "$hash" ] || return 0
	printf '%s\n' "${dir}/${hash}.py.gz" "${dir}/${hash}.py"
}

# 作業アーカイブ → 永久アーカイブの順に候補パスを出力する。
# $2 に 0 を渡すと永久アーカイブを含めない（既定は含める）。
strategy_archive_candidates() {
	local hash="$1" include_permanent="${2:-1}"
	strategy_archive_dir_candidates "${STRATEGY_HASH_ARCHIVE_DIR:-strategy_versions/by_hash}" "$hash"
	if [ "$include_permanent" = "1" ] && [ -n "${STRATEGY_HASH_PERMANENT_ARCHIVE_DIR:-}" ]; then
		strategy_archive_dir_candidates "$STRATEGY_HASH_PERMANENT_ARCHIVE_DIR" "$hash"
	fi
}

# gz を透過的に解凍して stdout へ出力する。
strategy_archive_read() {
	local path="$1"
	[ -n "$path" ] || return 1
	[ -f "$path" ] || return 1
	case "$path" in
	*.gz) gzip -dc -- "$path" ;;
	*) cat -- "$path" ;;
	esac
}

# src (.py.gz 可) を平文 dst へ複製する。
strategy_archive_copy() {
	local src="$1" dst="$2"
	[ -n "$src" ] && [ -n "$dst" ] || return 1
	[ -f "$src" ] || return 1
	case "$src" in
	*.gz) gzip -dc -- "$src" >"$dst" ;;
	*) cp -f -- "$src" "$dst" ;;
	esac
}

# hash に一致する候補を解決し、平文で読めるパスを出力する。
# `$1`=hash / `$2` に 0 を渡すと hash 照合を省き実在のみで解決する /
# `$3` に 0 を渡すと永久アーカイブを探索対象から外す。
# 一致候補が永久アーカイブ側 (または .gz) なら作業アーカイブへ平文で置いてから返す。
strategy_archive_resolve_plaintext() {
	local hash="$1" inspect="${2:-1}" include_permanent="${3:-1}" target cand actual
	[ -n "$hash" ] || return 1
	target="${STRATEGY_HASH_ARCHIVE_DIR:-strategy_versions/by_hash}/${hash}.py"
	while IFS= read -r cand; do
		[ -f "$cand" ] || continue
		if [ "$inspect" = "1" ]; then
			actual=$(python3 extract_decide_hash.py "$cand" 2>/dev/null || echo "")
			[ "$actual" = "$hash" ] || continue
		fi
		if [ "$cand" = "$target" ]; then
			printf '%s\n' "$cand"
			return 0
		fi
		mkdir -p "$(dirname "$target")" 2>/dev/null || true
		if strategy_archive_copy "$cand" "$target"; then
			printf '%s\n' "$target"
			return 0
		fi
		return 1
	done < <(strategy_archive_candidates "$hash" "$include_permanent")
	return 1
}
