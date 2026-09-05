# lib/curl_secure.sh - docich#39
#
# curl の Authorization/Client-Id/client_secret 等を argv へ出さないための
# 共通ヘルパー。curl の `-K/--config` はファイル(または `-` で標準入力)から
# オプションを読む。パイプ/ヒアドキュメントで渡した内容は子プロセスの
# argv (=/proc/*/cmdline や `ps` で見える文字列) には現れない。渡した文字列は
# FD (標準入力) 経由でだけ curl プロセスへ渡り、カーネルが argv として保持する
# 配列には一切乗らない。
#
# 使い方:
#   cfg=$(_curl_cfg_build header "Authorization: Bearer ${TOKEN}" \
#                          header "Client-Id: ${CLIENT_ID}")
#   printf '%s' "$cfg" | curl -sS -X "$method" "$url" -K - ...
#
# 注意: `_curl_cfg_line` 単体を `$(...)` で複数回呼んで文字列連結すると、
# コマンド置換が各呼び出しの末尾改行を落とすため行が融合し、2つ目以降の
# directive を curl が正しく読めなくなる。複数行を組み立てる時は必ず
# `_curl_cfg_build` (配列 + printf '%s\n' で改行を保証) を使うこと。

# _curl_cfg_escape VALUE
# curl config file の `"..."` 値として埋め込めるようエスケープする
# (バックスラッシュ→\\\\ , ダブルクォート→\\" )。
_curl_cfg_escape() {
	printf '%s' "$1" | sed -e 's/\\/\\\\/g' -e 's/"/\\"/g'
}

# _curl_cfg_line DIRECTIVE VALUE
# `directive = "escaped value"` の1行を出力する (末尾改行つき)。
# 単独行しか要らない呼び出し元向け。複数行を連結するなら
# _curl_cfg_build を使うこと (上の注意参照)。
_curl_cfg_line() {
	printf '%s = "%s"\n' "$1" "$(_curl_cfg_escape "$2")"
}

# _curl_cfg_build DIRECTIVE1 VALUE1 [DIRECTIVE2 VALUE2 ...]
# directive/value のペアを複数受け取り、各行の間に実改行を保証した
# curl config テキストを stdout へ返す。
_curl_cfg_build() {
	local -a lines=()
	while [ "$#" -ge 2 ]; do
		lines+=("$(_curl_cfg_line "$1" "$2")")
		shift 2
	done
	[ "${#lines[@]}" -eq 0 ] || printf '%s\n' "${lines[@]}"
}
