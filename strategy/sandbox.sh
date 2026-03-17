# strategy/sandbox.sh - validate_strategy, create/harvest/destroy_sandbox


#=== strategy.py バリデーション ===

VALIDATE_ERROR=""

validate_strategy() {
	# 引数でファイルパスを指定可能 (デフォルト: strategy.py)
	local target_file="${1:-strategy.py}"
	log "[VALIDATE] checking $target_file..."
	VALIDATE_ERROR=""

	local sig_out
	sig_out=$(
		python3 - "$target_file" <<'PYEOF' 2>&1
import sys, inspect, types
target = sys.argv[1]

# .py.staging ファイルを扱うため、exec() でモジュールを作成
with open(target, 'r', encoding='utf-8') as f:
    source = f.read()

mod = types.ModuleType('strategy')
exec(source, mod.__dict__)

if not hasattr(mod, 'decide'):
    print('ERROR: decide() not found')
    sys.exit(1)
sig = inspect.signature(mod.decide)
params = list(sig.parameters.keys())
if len(params) < 2:
    print(f'ERROR: decide() needs 2+ params, got {len(params)}: {params}')
    sys.exit(1)
print(f'OK: decide({", ".join(params)})')
PYEOF
	)
	if [ $? -ne 0 ]; then
		VALIDATE_ERROR="decide()シグネチャチェック失敗: $sig_out"
		log "[VALIDATE] $VALIDATE_ERROR"
		return 1
	fi

	if [ -f "$GAME_STATE" ]; then
		local test_out
		test_out=$(python3 "$target_file" "$GAME_STATE" 2>&1)
		if [ $? -ne 0 ]; then
			VALIDATE_ERROR="テスト実行失敗: $test_out"
			log "[VALIDATE] $VALIDATE_ERROR"
			return 1
		fi
		if ! echo "$test_out" | python3 -c "import json,sys; d=json.load(sys.stdin); assert 'x' in d" 2>/dev/null; then
			VALIDATE_ERROR="テスト出力にxフィールドなし: $test_out"
			log "[VALIDATE] $VALIDATE_ERROR"
			return 1
		fi
		log "[VALIDATE] テスト実行OK"
	fi

	return 0
}

_realpath_safe() {
	python3 - "$1" <<'PY'
import os
import sys

path = sys.argv[1] if len(sys.argv) > 1 else ""
if not path:
    raise SystemExit(1)
print(os.path.realpath(path))
PY
}

_path_is_under_dir() {
	local path="$1" base="$2"
	local rp rb
	rp=$(_realpath_safe "$path" 2>/dev/null) || return 1
	rb=$(_realpath_safe "$base" 2>/dev/null) || return 1
	[ "$rp" = "$rb" ] && return 0
	case "$rp" in
	"$rb"/*) return 0 ;;
	*) return 1 ;;
	esac
}

create_sandbox() {
	local sandbox_dir
	mkdir -p "$ELOOP_LIB_DIR/tmp" 2>/dev/null || true
	sandbox_dir=$(mktemp -d "$ELOOP_LIB_DIR/tmp/.soren_sandbox_XXXXXX" 2>/dev/null) || {
		log "[SANDBOX] 作成失敗"
		return 1
	}

	local src dst
	for src in "$@"; do
		[ -n "$src" ] || continue
		[ -e "$src" ] || continue
		[ -L "$src" ] && continue
		# ../を含むパスはsandbox外参照の危険があるため拒否
		case "$src" in
		../*|*/../*|*/..) log "[SANDBOX] パス拒否 (..含む): $src"; continue ;;
		esac
		dst="$sandbox_dir/$src"
		mkdir -p "$(dirname "$dst")"
		if [ -d "$src" ]; then
			mkdir -p "$dst"
			rsync -a --no-links "$src"/ "$dst"/ 2>/dev/null || cp -RL "$src"/. "$dst"/ 2>/dev/null || true
		else
			cp "$src" "$dst" 2>/dev/null || true
		fi
	done

	# サンドボックス内の改善対象
	if [ ! -f "$sandbox_dir/strategy.py" ] && [ -f "$STRATEGY_FILE" ]; then
		cp "$STRATEGY_FILE" "$sandbox_dir/strategy.py" 2>/dev/null || true
	fi
	if [ -f "$sandbox_dir/strategy.py" ]; then
		cp "$sandbox_dir/strategy.py" "$sandbox_dir/strategy.py.staging" 2>/dev/null || true
	fi

	mkdir -p "$sandbox_dir/strategy_helpers"
	if [ -d "strategy_helpers" ]; then
		rsync -a --no-links "strategy_helpers"/ "$sandbox_dir/strategy_helpers"/ 2>/dev/null || cp -RL "strategy_helpers"/. "$sandbox_dir/strategy_helpers"/ 2>/dev/null || true
	fi
	[ -f "$sandbox_dir/strategy_helpers/__init__.py" ] || : > "$sandbox_dir/strategy_helpers/__init__.py"

	echo "$sandbox_dir"
}

harvest_sandbox() {
	local sandbox_dir="$1"
	[ -n "$sandbox_dir" ] || return 1
	[ -d "$sandbox_dir" ] || return 1

	local sandbox_real
	sandbox_real=$(_realpath_safe "$sandbox_dir" 2>/dev/null) || return 1
	if ! _path_is_under_dir "$sandbox_real" "$ELOOP_LIB_DIR/tmp"; then
		log "[SANDBOX] harvest拒否: 不正なsandboxパス $sandbox_real"
		return 1
	fi
	case "$(basename "$sandbox_real")" in
		.soren_sandbox_*) ;;
		*)
			log "[SANDBOX] harvest拒否: sandbox名が不正 $sandbox_real"
			return 1
			;;
	esac

	local harvest_dir
	harvest_dir=$(mktemp -d "$ELOOP_LIB_DIR/tmp/.sandbox_harvest_XXXXXX" 2>/dev/null) || return 1

	if [ -f "$sandbox_dir/strategy.py.staging" ]; then
		rsync -a --no-links "$sandbox_dir/strategy.py.staging" "$harvest_dir/" 2>/dev/null || cp "$sandbox_dir/strategy.py.staging" "$harvest_dir/" 2>/dev/null || {
			rm -rf "$harvest_dir" 2>/dev/null
			return 1
		}
	fi

	if [ -d "$sandbox_dir/strategy_helpers" ]; then
		mkdir -p "$harvest_dir/strategy_helpers"
		rsync -a --no-links "$sandbox_dir/strategy_helpers"/ "$harvest_dir/strategy_helpers"/ 2>/dev/null || \
			cp -RL "$sandbox_dir/strategy_helpers"/. "$harvest_dir/strategy_helpers"/ 2>/dev/null || true
	fi

	if find "$harvest_dir" -type l 2>/dev/null | grep -q .; then
		log "[SANDBOX] harvest拒否: symlink混入を検出"
		rm -rf "$harvest_dir" 2>/dev/null
		return 1
	fi

	if find "$harvest_dir" -type f -links +1 2>/dev/null | grep -q .; then
		log "[SANDBOX] harvest拒否: hard link検出"
		rm -rf "$harvest_dir" 2>/dev/null
		return 1
	fi

	if ! _path_is_under_dir "$harvest_dir" "$ELOOP_LIB_DIR/tmp"; then
		log "[SANDBOX] harvest拒否: 不正なharvestパス"
		rm -rf "$harvest_dir" 2>/dev/null
		return 1
	fi

	echo "$harvest_dir"
}

destroy_sandbox() {
	local sandbox_dir="$1"
	[ -n "$sandbox_dir" ] || return 0
	[ -e "$sandbox_dir" ] || return 0

	local sandbox_real
	sandbox_real=$(_realpath_safe "$sandbox_dir" 2>/dev/null) || return 1
	if ! _path_is_under_dir "$sandbox_real" "$ELOOP_LIB_DIR/tmp"; then
		log "[SANDBOX] destroy拒否: 不正なsandboxパス $sandbox_real"
		return 1
	fi
	case "$(basename "$sandbox_real")" in
		.soren_sandbox_*)
			rm -rf "$sandbox_real" 2>/dev/null || return 1
			;;
		*)
			log "[SANDBOX] destroy拒否: sandbox名が不正 $sandbox_real"
			return 1
			;;
	esac
}

check_host_integrity() {
	local before_file="$1"
	[ -f "$before_file" ] || return 0

	local after_file before_sorted after_sorted
	after_file=$(mktemp /tmp/eloop_host_after.XXXXXX) || return 0
	before_sorted=$(mktemp /tmp/eloop_host_before_sorted.XXXXXX) || {
		rm -f "$after_file"
		return 0
	}
	after_sorted=$(mktemp /tmp/eloop_host_after_sorted.XXXXXX) || {
		rm -f "$after_file" "$before_sorted"
		return 0
	}

	git status --porcelain -- "$STRATEGY_FILE" strategy_helpers tmp/change_log.txt >"$after_file" 2>/dev/null || {
		rm -f "$after_file" "$before_sorted" "$after_sorted"
		return 0
	}
	sort "$before_file" >"$before_sorted" 2>/dev/null || true
	sort "$after_file" >"$after_sorted" 2>/dev/null || true

	local added_lines host_changed=false
	added_lines=$(comm -13 "$before_sorted" "$after_sorted" 2>/dev/null || true)
	if [ -n "$added_lines" ]; then
		log "[SANDBOX] WARNING: AI改善中にapply対象ファイルのホスト変化を検出"
		printf '%s\n' "$added_lines" | head -20 | while read -r line; do
			[ -n "$line" ] && log "[SANDBOX] host_change: $line"
		done
		host_changed=true
	fi

	rm -f "$after_file" "$before_sorted" "$after_sorted"
	$host_changed && return 1 || return 0
}

validate_strategy_with_helpers() {
	local target_file="$1"
	local helpers_dir="${2:-strategy_helpers}"
	if ! validate_strategy "$target_file"; then
		return 1
	fi

	if [ -d "$helpers_dir" ]; then
		if find "$helpers_dir" -type l 2>/dev/null | grep -q .; then
			VALIDATE_ERROR="strategy_helpers に symlink が含まれる"
			log "[VALIDATE] $VALIDATE_ERROR"
			return 1
		fi
		if [ ! -f "$helpers_dir/__init__.py" ]; then
			VALIDATE_ERROR="strategy_helpers/__init__.py が不足"
			log "[VALIDATE] $VALIDATE_ERROR"
			return 1
		fi

		local helper_out
		helper_out=$(
			python3 - "$helpers_dir" <<'PYEOF' 2>&1
import os
import sys

helpers = sys.argv[1]
if not os.path.isdir(helpers):
    print("OK: no helpers dir")
    raise SystemExit(0)

checked = 0
for root, _, files in os.walk(helpers):
    for fn in files:
        if not fn.endswith(".py"):
            continue
        path = os.path.join(root, fn)
        with open(path, "r", encoding="utf-8") as f:
            src = f.read()
        compile(src, path, "exec")
        checked += 1

print(f"OK: helper syntax files={checked}")
PYEOF
		)
		if [ $? -ne 0 ]; then
			VALIDATE_ERROR="strategy_helpers 構文検証失敗: $helper_out"
			log "[VALIDATE] $VALIDATE_ERROR"
			return 1
		fi
		log "[VALIDATE] strategy_helpers 検証OK"
	fi

	return 0
}
