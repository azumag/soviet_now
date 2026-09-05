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
import re
import sys
import ast

target = sys.argv[1]
helpers_dir = sys.argv[2] if len(sys.argv) > 2 else "strategy_helpers"  # unused: no host import/exec happens here (issue #34)

# .py.staging ファイルを扱う。以前はここで exec() してモジュール化していたが、
# 未信頼のAI生成候補をhost processでexec/importすること自体が issue #34 の脆弱性
# だったため撤去した。以降このスクリプトは ast.parse のみを使う静的検証であり、
# compile()/exec()/importlib で候補コードを実行することは一切しない。
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


def _main_guard_descendant_ids(tree):
    """`if __name__ == "__main__":` はCLIスモークテスト専用のブロックで、AI改変
    禁止ゾーン (strategy.py 本体のコメント参照) かつ decide() dispatch では一切
    実行されない。deny gate の対象から除外する (例: そこでの open()/sys 利用は
    正当)。除外は静的な構造マッチのみで、実行はしない。
    """
    skip_ids = set()

    def is_main_guard(node):
        if not isinstance(node, ast.If):
            return False
        test = node.test
        if not isinstance(test, ast.Compare) or len(test.ops) != 1 or not isinstance(test.ops[0], ast.Eq):
            return False
        operands = [test.left] + list(test.comparators)
        names = [o for o in operands if isinstance(o, ast.Name)]
        consts = [o for o in operands if isinstance(o, ast.Constant)]
        if len(names) != 1 or len(consts) != 1:
            return False
        return names[0].id == "__name__" and consts[0].value == "__main__"

    for node in tree.body:
        if is_main_guard(node):
            for child in ast.walk(node):
                skip_ids.add(id(child))
    return skip_ids


# allowlist方式: strategy.py / strategy_helpers / strategy_versions の既存の
# 正当な候補が実際に使っているものだけを許可する (2026-09-04 実測で確認)。
_AST_GATE_ALLOWED_IMPORT_MODULES = {
    "math", "random", "typing", "dataclasses", "itertools", "functools",
    "collections", "statistics", "enum",
    "json", "sys",  # __main__ ガード内のCLIスモークテストのみで使用
    "strategy_helpers", "analyze_board",
}

_AST_GATE_DENIED_CALL_NAMES = {
    "eval", "exec", "compile", "__import__",
    "open", "input", "breakpoint",
    "globals", "vars", "locals",
    "getattr", "setattr", "delattr", "hasattr",
}

_AST_GATE_DENIED_BARE_NAMES = {
    "os", "subprocess", "socket", "shutil", "pathlib", "urllib", "requests",
    "importlib", "ctypes", "pickle", "marshal", "pty", "tempfile", "shelve",
    "sqlite3", "http", "ftplib", "smtplib", "__builtins__",
}

_AST_GATE_DUNDER_RE = re.compile(r"^__.+__$")


def check_ast_deny_gate(source, filename):
    """暫定deny gate (issue #34): 動的import、dunder探索、file/process/network系
    callを含む候補をrejectする。ast.parse のみを使い、compile()/exec()/import で
    候補コードを実行することは絶対にしない。
    """
    tree = ast.parse(source, filename=filename)
    skip_ids = _main_guard_descendant_ids(tree)

    def walk_relevant(node):
        for child in ast.iter_child_nodes(node):
            if id(child) in skip_ids:
                continue
            yield child
            yield from walk_relevant(child)

    violations = []
    for node in walk_relevant(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            if isinstance(node, ast.Import):
                mod_names = [a.name.split(".")[0] for a in node.names]
            else:
                mod_names = [(node.module or "").split(".")[0]]
            for m in mod_names:
                if m not in _AST_GATE_ALLOWED_IMPORT_MODULES:
                    violations.append(f"line {node.lineno}: import not allowlisted: {m!r}")
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.func.id in _AST_GATE_DENIED_CALL_NAMES:
                violations.append(f"line {node.lineno}: denied call: {node.func.id}()")
        elif isinstance(node, ast.Attribute):
            if _AST_GATE_DUNDER_RE.match(node.attr):
                violations.append(f"line {node.lineno}: dunder attribute access: .{node.attr}")
            elif isinstance(node.value, ast.Name) and node.value.id == "sys" and node.attr == "modules":
                violations.append(f"line {node.lineno}: denied attribute: sys.modules")
        elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
            if node.id in _AST_GATE_DENIED_BARE_NAMES or _AST_GATE_DUNDER_RE.match(node.id):
                violations.append(f"line {node.lineno}: denied identifier: {node.id}")

    if violations:
        print("ERROR: ast-deny-gate rejected candidate:")
        for v in violations[:20]:
            print(f"  - {v}")
        sys.exit(1)


check_ast_deny_gate(source, target)


def check_decide_exists_via_ast(source, filename):
    """decide() の存在・引数個数のみを AST で静的確認する。以前はここで exec した
    モジュールに実データを渡して呼び出し、出力契約(x/reasonの型・範囲)まで検証
    していたが、それは未信頼候補のhost exec そのものだったため issue #34 で撤去。
    振る舞い検証はOS隔離runner (issue #35) が実装され次第そちらで行う。
    """
    tree = ast.parse(source, filename=filename)
    decide_node = None
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == "decide":
            decide_node = node
            break
    if decide_node is None:
        print("ERROR: decide() not found")
        sys.exit(1)
    args = decide_node.args
    params = [a.arg for a in args.posonlyargs + args.args + args.kwonlyargs]
    if args.vararg:
        params.append(args.vararg.arg)
    if args.kwarg:
        params.append(args.kwarg.arg)
    if len(params) < 2:
        print(f"ERROR: decide() needs 2+ params, got {len(params)}: {params}")
        sys.exit(1)
    print(f'OK: decide({", ".join(params)})')


check_decide_exists_via_ast(source, target)
PYEOF
	)
	if [ $? -ne 0 ]; then
		VALIDATE_ERROR="strategy validation failed: $sig_out"
		log "[VALIDATE] $VALIDATE_ERROR"
		return 1
	fi

	# 2026-09-04 (#34): 以前はここで $target_file を `python3 "$target_file" "$GAME_STATE"`
	# として host process 上で直接実行し、decide() の実出力契約を検証していた。これは
	# 未信頼のAI生成候補をhostでそのままexecする経路そのものであり、issue #34の脆弱性
	# だったため撤去した。振る舞い検証(実際にdecide()を呼んで出力を見る)はOS隔離runner
	# (issue #35) が実装され次第、その中でのみ行う。ここまでの静的検証(構文/decide()存在/
	# AST deny gate)を通過した候補のみが次段(validate_strategy_with_helpers の隔離runner
	# 可否チェック)へ進む。
	if [ -f "$GAME_STATE" ]; then
		log "[VALIDATE] ランタイムsmoke testはhost execのため撤去済み (issue #34/#35)。静的検証のみで判定。"
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

# OS隔離runner (issue #35: strategy/isolated_runner/run_isolated.py)。
# bubblewrap優先・無ければunshare+setpriv+chrootのフォールバックで、非特権UID・
# read-only root・tmpfs workdir・networkなし・env空のサンドボックスを実際に
# 一往復させ (`run_isolated.py probe`)、uidが下がっている・networkが塞がれて
# いる・read-only領域への書込みが拒否される・出力チャンネルにだけ書けることを
# 実測確認して初めて真になる。バイナリの存在チェックだけでは真にしない。
# Linux以外 (macOS等) や bwrap/unshare が無い環境では常に偽を返す —
# これは意図した fail-closed の挙動でありバグではない。
# 他の関数からと違い、環境変数での上書きは意図的に提供しない (この関数の本体で
# ${...} 展開を使わないこと)。再有効化は「実際に隔離が機能する環境を用意する」
# という運用行為でのみ行われ、コード上のトグルは提供しない。
_strategy_isolated_runner_available() {
	command -v python3 >/dev/null 2>&1 || return 1
	[ -f "strategy/isolated_runner/run_isolated.py" ] || return 1
	python3 strategy/isolated_runner/run_isolated.py probe >/dev/null 2>&1
}

# 候補ファイルを実際に隔離runnerへ通し、receiptをtmp/state配下に保存したうえで
# 合否を返す。呼び出し元は _strategy_isolated_runner_available が真の場合のみ
# ここへ到達する。
#
# rollout: SOREN_ISOLATED_RUNNER_MODE (未設定時は "shadow") で二段階にする。
#   shadow  (既定) : 評価は必ず実行してreceiptを記録するが、自動適用ゲートには
#                    反映しない (常にfail-closdedを維持)。旧runner
#                    (strategy_runner.py 直接呼び出し) との score/decision
#                    比較が十分に蓄積されるまでの既定状態。
#   enforce : receiptのgateが"pass"の場合のみ適用を許可する。runner障害
#             (timeout/OOM/crash/hash不一致/schema不一致など) の場合は
#             host execへのfallbackはせず、fail-closedのまま既存の
#             known-good strategyを維持する。
# このモード切替は「host execの再有効化」ではなく「新しい隔離ゲートの
# rollout段階」なので、issue #34で禁止された類の環境変数トグルとは性質が
# 異なる (このモードがどちらでも、AI候補がhostでexec/importされることは無い)。
_strategy_isolated_runner_evaluate() {
	local target_file="$1"
	local helpers_dir="${2:-strategy_helpers}"
	local mode="${SOREN_ISOLATED_RUNNER_MODE:-shadow}"
	local receipt_dir="${TMP_STATE_DIR:-tmp/state}/isolated_runner_receipts"
	mkdir -p "$receipt_dir" 2>/dev/null || true
	local receipt_out="$receipt_dir/receipt_$(date +%Y%m%d_%H%M%S)_$$.json"

	python3 strategy/isolated_runner/run_isolated.py evaluate \
		--target "$target_file" --helpers "$helpers_dir" \
		--receipt-out "$receipt_out" --mode "$mode" >/dev/null 2>&1

	local summary="(receipt読み取り失敗)"
	local gate=""
	if [ -f "$receipt_out" ]; then
		summary=$(python3 -c '
import json, sys
try:
    d = json.load(open(sys.argv[1], encoding="utf-8"))
except Exception as e:
    print(f"receipt parse error: {e}")
    raise SystemExit(0)
print(
    f"gate={d.get(\"gate\")} backend={d.get(\"backend\")} "
    f"reason={d.get(\"gate_reason\", \"\")[:300]}"
)
' "$receipt_out" 2>/dev/null)
		gate=$(python3 -c '
import json, sys
try:
    d = json.load(open(sys.argv[1], encoding="utf-8"))
    print(d.get("gate", ""))
except Exception:
    print("")
' "$receipt_out" 2>/dev/null)
	fi

	log "[VALIDATE] isolated runner (mode=$mode) $summary (receipt: $receipt_out)"

	if [ "$mode" = "enforce" ]; then
		[ "$gate" = "pass" ] && return 0
		return 1
	fi

	# shadow: 評価とreceipt記録のみ行い、自動適用ゲートは常にfail-closedのまま。
	return 1
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

	if ! _strategy_isolated_runner_available; then
		VALIDATE_ERROR="OS隔離runner未導入のため自動適用をfail-closedで停止 (issue #34/#35): 静的検証(構文/decide()存在/AST deny gate)は通過したが、host上でAI候補を実行しないため適用は保留し、既存のknown-good strategyを維持する"
		log "[VALIDATE] $VALIDATE_ERROR"
		return 1
	fi

	if ! _strategy_isolated_runner_evaluate "$target_file" "$helpers_dir"; then
		VALIDATE_ERROR="OS隔離runner評価がpassにならなかったため適用を見送り、既存のknown-good strategyを維持する (mode=${SOREN_ISOLATED_RUNNER_MODE:-shadow}。詳細はtmp/state/isolated_runner_receipts/配下のreceiptとログ参照)"
		log "[VALIDATE] $VALIDATE_ERROR"
		return 1
	fi

	return 0
}
