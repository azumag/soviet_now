#!/bin/bash
# core/strategy_runtime.sh - strategy.py の実行スナップショットと原子的反映

_strategy_runtime_lock_path() {
	printf '%s\n' "${STRATEGY_APPLY_LOCK_FILE:-${TMP_STATE_DIR:-tmp/state}/strategy_apply.lock}"
}

_strategy_runtime_atomic_copy_unlocked() {
	local source_file="$1" target_file="$2"
	local target_dir target_base temp_file
	[ -f "$source_file" ] || return 1
	target_dir=$(dirname "$target_file")
	target_base=$(basename "$target_file")
	mkdir -p "$target_dir" || return 1
	temp_file=$(mktemp "$target_dir/.${target_base}.apply.XXXXXX") || return 1
	if ! cp "$source_file" "$temp_file"; then
		rm -f "$temp_file"
		return 1
	fi
	if ! mv -f "$temp_file" "$target_file"; then
		rm -f "$temp_file"
		return 1
	fi
}

strategy_runtime_atomic_apply_then() {
	local source_file="$1" target_file="${2:-$STRATEGY_FILE}" callback="${3:-}"
	local lock_file
	shift 3
	lock_file=$(_strategy_runtime_lock_path)
	mkdir -p "$(dirname "$lock_file")" || return 1
	if command -v flock >/dev/null 2>&1; then
		(
			flock -x 9 || exit 1
			_strategy_runtime_atomic_copy_unlocked "$source_file" "$target_file" || exit 1
			[ -z "$callback" ] || "$callback" "$@"
		) 9>"$lock_file"
	else
		_strategy_runtime_atomic_copy_unlocked "$source_file" "$target_file" || return 1
		[ -z "$callback" ] || "$callback" "$@"
	fi
}

strategy_runtime_atomic_apply() {
	strategy_runtime_atomic_apply_then "$1" "${2:-$STRATEGY_FILE}" ""
}

_strategy_runtime_apply_bundle_unlocked() {
	local source_file="$1" target_file="$2" helpers_source="$3" helpers_target="$4"
	local target_dir target_base strategy_temp
	local helpers_parent helpers_base helpers_stage="" helpers_previous=""

	[ -f "$source_file" ] || return 1
	target_dir=$(dirname "$target_file")
	target_base=$(basename "$target_file")
	mkdir -p "$target_dir" || return 1
	strategy_temp=$(mktemp "$target_dir/.${target_base}.apply.XXXXXX") || return 1
	if ! cp "$source_file" "$strategy_temp"; then
		rm -f "$strategy_temp"
		return 1
	fi

	if [ -d "$helpers_source" ]; then
		helpers_parent=$(dirname "$helpers_target")
		helpers_base=$(basename "$helpers_target")
		mkdir -p "$helpers_parent" || {
			rm -f "$strategy_temp"
			return 1
		}
		helpers_stage=$(mktemp -d "$helpers_parent/.${helpers_base}.apply.XXXXXX") || {
			rm -f "$strategy_temp"
			return 1
		}
		if ! cp -RL "$helpers_source"/. "$helpers_stage"/; then
			rm -rf "$helpers_stage"
			rm -f "$strategy_temp"
			return 1
		fi
		[ -f "$helpers_stage/__init__.py" ] || : >"$helpers_stage/__init__.py"

		if [ -e "$helpers_target" ] || [ -L "$helpers_target" ]; then
			helpers_previous=$(mktemp -d "$helpers_parent/.${helpers_base}.previous.XXXXXX") || {
				rm -rf "$helpers_stage"
				rm -f "$strategy_temp"
				return 1
			}
			rmdir "$helpers_previous" || return 1
			if ! mv "$helpers_target" "$helpers_previous"; then
				rm -rf "$helpers_stage"
				rm -f "$strategy_temp"
				return 1
			fi
		fi
		if ! mv "$helpers_stage" "$helpers_target"; then
			[ -n "$helpers_previous" ] && mv "$helpers_previous" "$helpers_target" 2>/dev/null || true
			rm -rf "$helpers_stage"
			rm -f "$strategy_temp"
			return 1
		fi
	fi

	if ! mv -f "$strategy_temp" "$target_file"; then
		if [ -n "$helpers_stage" ]; then
			rm -rf "$helpers_target"
			[ -n "$helpers_previous" ] && mv "$helpers_previous" "$helpers_target" 2>/dev/null || true
		fi
		rm -f "$strategy_temp"
		return 1
	fi
	[ -n "$helpers_previous" ] && rm -rf "$helpers_previous"
}

strategy_runtime_atomic_apply_bundle_then() {
	local source_file="$1" target_file="${2:-$STRATEGY_FILE}"
	local helpers_source="${3:-}" helpers_target="${4:-strategy_helpers}" callback="${5:-}"
	local lock_file
	shift 5
	lock_file=$(_strategy_runtime_lock_path)
	mkdir -p "$(dirname "$lock_file")" || return 1
	if command -v flock >/dev/null 2>&1; then
		(
			flock -x 9 || exit 1
			_strategy_runtime_apply_bundle_unlocked "$source_file" "$target_file" "$helpers_source" "$helpers_target" || exit 1
			[ -z "$callback" ] || "$callback" "$@"
		) 9>"$lock_file"
	else
		_strategy_runtime_apply_bundle_unlocked "$source_file" "$target_file" "$helpers_source" "$helpers_target" || return 1
		[ -z "$callback" ] || "$callback" "$@"
	fi
}

strategy_runtime_atomic_apply_bundle() {
	strategy_runtime_atomic_apply_bundle_then "$1" "${2:-$STRATEGY_FILE}" "${3:-}" "${4:-strategy_helpers}" ""
}

_strategy_runtime_snapshot_unlocked() {
	local source_file="$1" runtime_dir="$2" record_file="$3" helpers_source="$4" before_snapshot="${5:-}"
	case "$runtime_dir" in
	'' | / | .) return 1 ;;
	esac
	[ -z "$before_snapshot" ] || "$before_snapshot" || return 1
	rm -rf "$runtime_dir"
	mkdir -p "$runtime_dir" || return 1
	cp "$source_file" "$runtime_dir/strategy.py" || {
		rm -rf "$runtime_dir"
		return 1
	}
	if [ -d "$helpers_source" ]; then
		cp -RL "$helpers_source" "$runtime_dir/strategy_helpers" || {
			rm -rf "$runtime_dir"
			return 1
		}
	else
		mkdir -p "$runtime_dir/strategy_helpers" || return 1
		: >"$runtime_dir/strategy_helpers/__init__.py"
	fi
	_strategy_runtime_atomic_copy_unlocked "$runtime_dir/strategy.py" "$record_file" || {
		rm -rf "$runtime_dir"
		return 1
	}
}

strategy_runtime_create_game_snapshot() {
	local source_file="$1" runtime_dir="$2"
	local record_file="${3:-${source_file}.game_snapshot}" helpers_source="${4:-strategy_helpers}" before_snapshot="${5:-}"
	local lock_file
	lock_file=$(_strategy_runtime_lock_path)
	mkdir -p "$(dirname "$lock_file")" || return 1
	if command -v flock >/dev/null 2>&1; then
		(
			flock -x 9 || exit 1
			_strategy_runtime_snapshot_unlocked "$source_file" "$runtime_dir" "$record_file" "$helpers_source" "$before_snapshot"
		) 9>"$lock_file"
	else
		_strategy_runtime_snapshot_unlocked "$source_file" "$runtime_dir" "$record_file" "$helpers_source" "$before_snapshot"
	fi
}
