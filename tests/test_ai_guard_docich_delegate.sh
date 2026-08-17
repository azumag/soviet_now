#!/usr/bin/env bash
# _ai_guard_model_output が DOCICH_BIN へ委譲し、無い環境ではローカル python に
# フォールバックすることを検証する (C4, common_parts_chat_c4.md C-S2)。
set -u

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SRC="$ROOT/lib/ai_generate.sh"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

FAIL=0
ok() { echo "ok - $1"; }
not_ok() { echo "not ok - $1"; FAIL=1; }

# 関数定義のみを source
sed -n '/^_ai_guard_model_output()/,/^}/p' "$SRC" >"$TMP/fn.sh"
. "$TMP/fn.sh"

# スタブ: DOCICH_BIN へ渡された argv を記録し、固定出力を返す
mkdir -p "$TMP/bin"
cat >"$TMP/bin/docich" <<'STUB'
#!/usr/bin/env bash
echo "delegated:$*" >&2
printf 'docich-guarded-output\n'
STUB
chmod +x "$TMP/bin/docich"

out=$(printf 'raw input\n' | DOCICH_BIN="$TMP/bin/docich" _ai_guard_model_output)
if [ "$out" = "docich-guarded-output" ]; then
	ok "DOCICH_BIN が設定されていれば docich ai-guard へ委譲する"
else
	not_ok "docich 委譲が機能していない (out=$out)"
fi

# DOCICH_BIN 未設定・PATH に docich 無し → ローカル python フォールバック
# (PATH は python3 を通す最小構成に絞り、システムの docich を拾わないようにする)
out2=$(printf 'こんにちは。\n' | (
	unset DOCICH_BIN
	export ELOOP_LIB_DIR="$ROOT"
	export PATH="$(dirname "$(command -v python3)"):/usr/bin:/bin"
	_ai_guard_model_output
) 2>/dev/null)
if [ "$out2" = "こんにちは。" ]; then
	ok "DOCICH_BIN 無しではローカル model_output_guard.py へフォールバックする"
else
	not_ok "ローカルフォールバックが機能していない (out2=$out2)"
fi

exit "$FAIL"
