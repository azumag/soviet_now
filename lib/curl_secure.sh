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

# _curl_secure_exec COMMAND [ARGS...] - docich#71
#
# 任意のコマンドを最小環境で実行する。親workerが `.env` を `set -a` で
# source しているため全secretがexportされており、素の `curl` 呼び出しでは
# 子プロセスの environ (`/proc/PID/environ`) へ TOKEN等が継承される。
# `env -i` + 非secret allowlist だけを渡すことで継承を断つ。
# stdin/stdout/stderr の FD は維持されるため、
# `printf '%s' "$cfg" | _curl_secure_exec curl -K - ...` の形で使う。
# proxy/CA系は環境によって curl の到達性に必須のため、設定時のみ継承する。
_curl_secure_exec() {
	local -a _allow=()
	local _name _val
	_allow+=("PATH=${PATH:-/usr/bin:/bin}")
	for _name in HOME LANG LC_ALL LC_MESSAGES LANGUAGE TZ TMPDIR TEMP TMP \
		http_proxy https_proxy all_proxy HTTP_PROXY HTTPS_PROXY ALL_PROXY \
		no_proxy NO_PROXY CURL_CA_BUNDLE SSL_CERT_FILE SSL_CERT_DIR; do
		eval "_val=\${${_name}:-}"
		[ -n "${_val:-}" ] && _allow+=("${_name}=${_val}")
	done
	command env -i "${_allow[@]}" "$@"
}

# _curl_secure_run [CURL_ARGS...] - docich#71
#
# curl を最小環境で実行する薄いラッパ。secret自体は argv/environ ではなく
# stdin の `-K -` config 経由で渡すこと (docich#39 と併用)。
# 使い方:
#   printf '%s' "$cfg" | _curl_secure_run -sS --max-time 20 -K - ...
_curl_secure_run() {
	_curl_secure_exec curl "$@"
}
