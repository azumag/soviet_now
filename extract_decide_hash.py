#!/usr/bin/env python3
"""extract_decide_hash.py - decide()関数本体をAST抽出してMD5ハッシュを返す

Usage: python3 extract_decide_hash.py [strategy.py]
Output: MD5ハッシュ文字列 (stdout)
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


def extract_decide_body(filepath):
    """decide()関数のAST本体を抽出し、コメント・docstring除去した正規化コードを返す"""
    with open(filepath, "r", encoding="utf-8") as f:
        source = f.read()

    tree = ast.parse(source)

    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "decide":
            # docstringを除去
            body = node.body
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                body = body[1:]

            # ASTをダンプして正規化（コメントは含まれない）
            normalized = stable_ast_dump(ast.Module(body=body, type_ignores=[]))
            return normalized

    return ""


def compute_hash(filepath):
    """decide()のMD5ハッシュを計算"""
    body = extract_decide_body(filepath)
    if not body:
        return ""
    return hashlib.md5(body.encode("utf-8")).hexdigest()[:12]


if __name__ == "__main__":
    filepath = sys.argv[1] if len(sys.argv) > 1 else "strategy.py"
    h = compute_hash(filepath)
    if h:
        print(h)
    else:
        print("ERROR: decide() not found", file=sys.stderr)
        sys.exit(1)
