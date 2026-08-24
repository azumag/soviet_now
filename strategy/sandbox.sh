# strategy/sandbox.sh - validate_strategy, create/harvest/destroy_sandbox

validate_strategy() {
	# 引数でファイルパスを指定可能 (デフォルト: strategy.py)
	local target_file="${1:-strategy.py}"
	local helpers_dir="${2:-strategy_helpers}"
	log "[VALIDATE] checking $target_file..."
	VALIDATE_ERROR=""

	local sig_out
	sig_out=$(
		python3 - "$target_file" "$helpers_dir" <<'PYEOF' 2>&1
import os
import sys
import ast
import inspect
import types

target = sys.argv[1]
helpers_dir = sys.argv[2] if len(sys.argv) > 2 else "strategy_helpers"
root = os.getcwd()
if root not in sys.path:
    sys.path.insert(0, root)
if helpers_dir and os.path.isdir(helpers_dir):
    helper_parent = os.path.abspath(os.path.dirname(helpers_dir) or ".")
    if helper_parent not in sys.path:
        sys.path.insert(0, helper_parent)

# .py.staging ファイルを扱うため、exec() でモジュールを作成
with open(target, 'r', encoding='utf-8') as f:
    source = f.read()


def check_decide_load_before_local_assign(source, filename):
    tree = ast.parse(source, filename=filename)
    decide_node = None
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == "decide":
            decide_node = node
            break
    if decide_node is None:
        return

    params = {a.arg for a in decide_node.args.posonlyargs + decide_node.args.args + decide_node.args.kwonlyargs}
    if decide_node.args.vararg:
        params.add(decide_node.args.vararg.arg)
    if decide_node.args.kwarg:
        params.add(decide_node.args.kwarg.arg)

    class LocalCollector(ast.NodeVisitor):
        def __init__(self):
            self.locals = set()

        def visit_FunctionDef(self, node):
            if node is decide_node:
                for stmt in node.body:
                    self.visit(stmt)

        visit_AsyncFunctionDef = visit_FunctionDef

        def visit_Lambda(self, node):
            return

        def visit_ClassDef(self, node):
            return

        def visit_ListComp(self, node):
            return

        visit_SetComp = visit_ListComp
        visit_GeneratorExp = visit_ListComp
        visit_DictComp = visit_ListComp

        def visit_Name(self, node):
            if isinstance(node.ctx, (ast.Store, ast.Del)):
                self.locals.add(node.id)

    collector = LocalCollector()
    collector.visit(decide_node)
    local_names = collector.locals - params
    violations = []

    def note_load(node, assigned):
        if (
            isinstance(node.ctx, ast.Load)
            and node.id in local_names
            and node.id not in assigned
        ):
            violations.append((node.id, getattr(node, "lineno", 0), getattr(node, "col_offset", 0)))

    def visit_expr(node, assigned):
        if node is None:
            return
        if isinstance(node, ast.Name):
            note_load(node, assigned)
            return
        if isinstance(node, (ast.Lambda, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            return
        if isinstance(node, (ast.ListComp, ast.SetComp, ast.GeneratorExp, ast.DictComp)):
            # Comprehension targets have their own scope/order. Only outer iterables
            # matter for deciding whether decide() locals are already available.
            for gen in node.generators:
                visit_expr(gen.iter, assigned)
            return
        for child in ast.iter_child_nodes(node):
            visit_expr(child, assigned)

    def bind_target(target_node, assigned):
        if isinstance(target_node, ast.Name):
            assigned.add(target_node.id)
        elif isinstance(target_node, (ast.Tuple, ast.List)):
            for elt in target_node.elts:
                bind_target(elt, assigned)
        elif isinstance(target_node, ast.Starred):
            bind_target(target_node.value, assigned)
        else:
            visit_expr(target_node, assigned)

    def analyze_block(statements, incoming):
        assigned = set(incoming)
        for stmt in statements:
            if isinstance(stmt, ast.Assign):
                visit_expr(stmt.value, assigned)
                for target_node in stmt.targets:
                    bind_target(target_node, assigned)
            elif isinstance(stmt, ast.AnnAssign):
                visit_expr(stmt.value, assigned)
                bind_target(stmt.target, assigned)
            elif isinstance(stmt, ast.AugAssign):
                visit_expr(stmt.target, assigned)
                visit_expr(stmt.value, assigned)
                bind_target(stmt.target, assigned)
            elif isinstance(stmt, ast.For):
                visit_expr(stmt.iter, assigned)
                body_assigned = set(assigned)
                bind_target(stmt.target, body_assigned)
                analyze_block(stmt.body, body_assigned)
                if stmt.orelse:
                    analyze_block(stmt.orelse, set(assigned))
            elif isinstance(stmt, ast.While):
                visit_expr(stmt.test, assigned)
                analyze_block(stmt.body, set(assigned))
                analyze_block(stmt.orelse, set(assigned))
            elif isinstance(stmt, ast.If):
                visit_expr(stmt.test, assigned)
                body_out = analyze_block(stmt.body, set(assigned))
                else_out = analyze_block(stmt.orelse, set(assigned)) if stmt.orelse else set(assigned)
                assigned = body_out & else_out
            elif isinstance(stmt, ast.With):
                for item in stmt.items:
                    visit_expr(item.context_expr, assigned)
                    if item.optional_vars:
                        bind_target(item.optional_vars, assigned)
                assigned = analyze_block(stmt.body, assigned)
            elif isinstance(stmt, ast.Try):
                analyze_block(stmt.body, set(assigned))
                for handler in stmt.handlers:
                    handler_assigned = set(assigned)
                    if handler.name:
                        handler_assigned.add(handler.name)
                    analyze_block(handler.body, handler_assigned)
                analyze_block(stmt.orelse, set(assigned))
                analyze_block(stmt.finalbody, set(assigned))
            elif isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                assigned.add(stmt.name)
            elif isinstance(stmt, (ast.Return, ast.Expr, ast.Assert, ast.Delete, ast.Raise)):
                visit_expr(stmt, assigned)
            else:
                visit_expr(stmt, assigned)
        return assigned

    analyze_block(decide_node.body, params)
    if violations:
        name, line, col = violations[0]
        print(
            f"ERROR: decide() load-before-local-assign: {name} at line {line}, col {col} "
            "(would raise UnboundLocalError / cannot access local variable on some branch)"
        )
        sys.exit(1)


check_decide_load_before_local_assign(source, target)


def check_suspicious_list_number_comparisons(source, filename):
    tree = ast.parse(source, filename=filename)
    list_like_names = {"pieces", "same_type_pieces", "danger_pieces"}
    relational_ops = (ast.Gt, ast.GtE, ast.Lt, ast.LtE)

    def is_list_like_name(node):
        return isinstance(node, ast.Name) and (
            node.id in list_like_names or node.id.endswith("_pieces")
        )

    def is_number_literal(node):
        return isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and not isinstance(node.value, bool)

    for node in ast.walk(tree):
        if not isinstance(node, ast.Compare):
            continue
        operands = [node.left] + list(node.comparators)
        for op, left, right in zip(node.ops, operands, operands[1:]):
            if not isinstance(op, relational_ops):
                continue
            if is_list_like_name(left) and is_number_literal(right):
                print(
                    f"ERROR: decide() list-number-comparison: {left.id} at line {node.lineno} "
                    "compared directly to a number; use len(...) for count checks"
                )
                sys.exit(1)
            if is_number_literal(left) and is_list_like_name(right):
                print(
                    f"ERROR: decide() list-number-comparison: {right.id} at line {node.lineno} "
                    "compared directly to a number; use len(...) for count checks"
                )
                sys.exit(1)


check_suspicious_list_number_comparisons(source, target)

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

def assert_decision(label, game_state, analysis):
    result = mod.decide(game_state, analysis)
    if not isinstance(result, dict):
        print(f"ERROR: {label}: result is not dict: {type(result).__name__}")
        sys.exit(1)
    if "x" not in result:
        print(f"ERROR: {label}: missing x: {result!r}")
        sys.exit(1)
    if "reason" not in result:
        print(f"ERROR: {label}: missing reason: {result!r}")
        sys.exit(1)
    x = result["x"]
    if not isinstance(x, (int, float)) or isinstance(x, bool):
        print(f"ERROR: {label}: x is not numeric: {x!r}")
        sys.exit(1)
    if not -3.2 <= float(x) <= 3.2:
        print(f"ERROR: {label}: x out of range: {x!r}")
        sys.exit(1)
    if not isinstance(result["reason"], str) or not result["reason"].strip():
        print(f"ERROR: {label}: reason is not non-empty string: {result!r}")
        sys.exit(1)

empty_analysis_state = {"pieces": [], "next": {"type": 1, "r": 0.25}, "nextNext": {"type": 1, "r": 0.25}, "score": 0}
assert_decision("empty-analysis", empty_analysis_state, {"results": [], "same_type": [], "reactor": {}})
print(f'OK: decide({", ".join(params)})')
PYEOF
	)
	if [ $? -ne 0 ]; then
		VALIDATE_ERROR="strategy validation failed: $sig_out"
		log "[VALIDATE] $VALIDATE_ERROR"
		return 1
	fi

	if [ -f "$GAME_STATE" ]; then
		local test_out
		# 2026-08-25 fix: target_file が tmp/state/ 等リポジトリ外にコピーされている場合
		# (rollback 復元 validation)、python3 直接実行では script dir が sys.path[0] になり
		# リポジトリ直下の strategy_helpers が import できず必ず失敗していた
		# (ModuleNotFoundError → "accepted by policy" で validation が事実上無効化)。
		# リポジトリルートを PYTHONPATH に足して本来の実行文脈に揃える。
		test_out=$(PYTHONPATH="$PWD${PYTHONPATH:+:$PYTHONPATH}" python3 "$target_file" "$GAME_STATE" 2>&1)
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
		if ! echo "$test_out" | python3 -c "import json,sys; d=json.load(sys.stdin); assert isinstance(d.get('reason'), str) and d.get('reason').strip(); x=d.get('x'); assert isinstance(x,(int,float)) and not isinstance(x,bool) and -3.2 <= float(x) <= 3.2" 2>/dev/null; then
			VALIDATE_ERROR="テスト出力契約違反: $test_out"
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
		../* | */../* | */..)
			log "[SANDBOX] パス拒否 (..含む): $src"
			continue
			;;
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

	mkdir -p "$sandbox_dir/strategy_helpers" "$sandbox_dir/logs" "$sandbox_dir/data" "$sandbox_dir/tmp/state"
	if [ -d "strategy_helpers" ]; then
		rsync -a --no-links --exclude="__pycache__" --exclude="*.pyc" "strategy_helpers"/ "$sandbox_dir/strategy_helpers"/ 2>/dev/null || cp -RL "strategy_helpers"/. "$sandbox_dir/strategy_helpers"/ 2>/dev/null || true
	fi
	[ -f "$sandbox_dir/strategy_helpers/__init__.py" ] || : >"$sandbox_dir/strategy_helpers/__init__.py"
	[ -f "$sandbox_dir/data/user_review.md" ] || : >"$sandbox_dir/data/user_review.md"
	[ -f "$sandbox_dir/tmp/state/last_rollback_analysis.md" ] || : >"$sandbox_dir/tmp/state/last_rollback_analysis.md"
	[ -f "$sandbox_dir/tmp/state/last_rollback_postmortem.md" ] || : >"$sandbox_dir/tmp/state/last_rollback_postmortem.md"

	if [ -f "logs/change_log.txt" ]; then
		cp "logs/change_log.txt" "$sandbox_dir/logs/change_log.txt" 2>/dev/null || true
	fi

	# opencode が親 git repo にエスケープしないよう、サンドボックスを独立 git repo にする
	(cd "$sandbox_dir" && git init -q && git add -A && git commit -q -m "sandbox init" --no-gpg-sign) >/dev/null 2>&1 || true

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

	if [ -f "$sandbox_dir/logs/change_log.txt" ] && [ -s "$sandbox_dir/logs/change_log.txt" ]; then
		mkdir -p "$harvest_dir/logs" 2>/dev/null || true
		cp "$sandbox_dir/logs/change_log.txt" "$harvest_dir/logs/change_log.txt" 2>/dev/null || true
	fi

	if [ -d "$sandbox_dir/strategy_helpers" ]; then
		mkdir -p "$harvest_dir/strategy_helpers"
		rsync -a --no-links --exclude="__pycache__" --exclude="*.pyc" "$sandbox_dir/strategy_helpers"/ "$harvest_dir/strategy_helpers"/ 2>/dev/null ||
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

	_write_host_integrity_snapshot "$after_file" || true
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

_write_host_integrity_snapshot() {
	local out_file="$1"
	[ -n "$out_file" ] || return 1
	{
		echo "## git-status"
		git status --porcelain -- "$STRATEGY_FILE" strategy_helpers 2>/dev/null || true
		echo "## file-hashes"
		if [ -f "$STRATEGY_FILE" ]; then
			shasum "$STRATEGY_FILE" 2>/dev/null || true
		fi
		if [ -d strategy_helpers ]; then
			find strategy_helpers -type f ! -name '.DS_Store' ! -path '*/__pycache__/*' ! -name '*.pyc' -print 2>/dev/null | sort | while IFS= read -r _host_file; do
				shasum "$_host_file" 2>/dev/null || true
			done
		fi
	} >"$out_file"
}

validate_strategy_with_helpers() {
	local target_file="$1"
	local helpers_dir="${2:-strategy_helpers}"
	if ! validate_strategy "$target_file" "$helpers_dir"; then
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
