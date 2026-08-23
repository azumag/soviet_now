#!/usr/bin/env python3
"""extract_decide_hash.py - strategy policyをAST抽出してMD5ハッシュを返す

Usage: python3 extract_decide_hash.py [strategy.py]
Output: MD5ハッシュ文字列 (stdout)

Top-level helper functions called by decide() are included recursively.  A
helper-only behavior change must not be mixed into the same rolling-score or
rollback identity.  Strategies whose decide() calls no local helper retain the
legacy decide-body hash, so existing archives keep their historical IDs.
"""

import ast
import hashlib
import sys


def stable_ast_dump(node):
    """Return a cross-Python-version-stable AST dump.

    Python 3.9 and 3.14 differ in how `ast.dump()` renders empty fields such as
    `keywords=[]` or `orelse=[]`. The runtime and dashboard both key strategy
    history off this hash, so the dump must not depend on the interpreter
    version.
    """
    if isinstance(node, ast.AST):
        fields = []
        for field in getattr(node, "_fields", ()):
            value = getattr(node, field)
            if value == [] or value is None:
                continue
            fields.append(f"{field}={stable_ast_dump(value)}")
        if fields:
            return f"{node.__class__.__name__}({', '.join(fields)})"
        return f"{node.__class__.__name__}()"
    if isinstance(node, list):
        return "[" + ", ".join(stable_ast_dump(item) for item in node) + "]"
    return repr(node)


def _body_without_docstring(node):
    body = node.body
    if (
        body
        and isinstance(body[0], ast.Expr)
        and isinstance(body[0].value, ast.Constant)
        and isinstance(body[0].value.value, str)
    ):
        return body[1:]
    return body


def _called_local_function_names(node):
    return {
        child.func.id
        for child in ast.walk(node)
        if isinstance(child, ast.Call) and isinstance(child.func, ast.Name)
    }


def _normalized_function(node):
    """Normalize a helper while ignoring comments, docstrings, and locations."""
    parts = [
        f"name={node.name!r}",
        f"args={stable_ast_dump(node.args)}",
        "body=" + stable_ast_dump(
            ast.Module(body=_body_without_docstring(node), type_ignores=[])
        ),
    ]
    if node.decorator_list:
        parts.append(f"decorators={stable_ast_dump(node.decorator_list)}")
    if node.returns is not None:
        parts.append(f"returns={stable_ast_dump(node.returns)}")
    return "FunctionDef(" + ", ".join(parts) + ")"


def extract_decide_body_from_source(source):
    """Extract normalized decide policy from source text."""
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError, TypeError):
        return ""

    top_functions = {
        node.name: node
        for node in tree.body
        if isinstance(node, ast.FunctionDef)
    }
    decide = top_functions.get("decide")
    if decide is not None:
        # Keep the exact legacy normalization when no local helpers are called.
        normalized = stable_ast_dump(
            ast.Module(body=_body_without_docstring(decide), type_ignores=[])
        )

        reachable = set()
        queue = sorted(_called_local_function_names(decide))
        while queue:
            name = queue.pop(0)
            if name == "decide" or name in reachable or name not in top_functions:
                continue
            reachable.add(name)
            queue.extend(sorted(_called_local_function_names(top_functions[name])))

        if reachable:
            helper_policy = "|".join(
                _normalized_function(top_functions[name])
                for name in sorted(reachable)
            )
            normalized += "|local_helpers=" + helper_policy
        return normalized

    return ""


def extract_decide_body(filepath):
    """Extract normalized decide policy, including reachable local helpers."""
    with open(filepath, "r", encoding="utf-8") as f:
        return extract_decide_body_from_source(f.read())


def compute_hash_from_source(source):
    """Compute a strategy-policy hash directly from source text."""
    body = extract_decide_body_from_source(source)
    if not body:
        return ""
    return hashlib.md5(body.encode("utf-8")).hexdigest()[:12]


def compute_hash(filepath):
    """Compute the stable strategy-policy MD5 prefix."""
    with open(filepath, "r", encoding="utf-8") as f:
        return compute_hash_from_source(f.read())


if __name__ == "__main__":
    filepath = sys.argv[1] if len(sys.argv) > 1 else "strategy.py"
    h = compute_hash(filepath)
    if h:
        print(h)
    else:
        print("ERROR: decide() not found", file=sys.stderr)
        sys.exit(1)
