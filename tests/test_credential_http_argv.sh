#!/usr/bin/env bash
# tests/test_credential_http_argv.sh - docich#39
#
# twitch_polls.sh / youtube_chat.sh の HTTP client が Authorization / Client-Id /
# client_secret / refresh_token / API key を curl の argv に載せなくなったこと
# (かつ機能は壊れていないこと) を、ダミーの sentinel を使い、実際にローカル
# mock HTTP サーバへ curl を打たせて実測する。
#
# 検証方法の説明 (なぜ argv に出ないか):
#   curl の `-K -` (--config -) はオプションを標準入力から読む。ヘッダや
#   client_secret 等の値は printf でパイプし、curl の argv 配列には一切
#   渡していない。/proc/*/cmdline (Linux) や `ps` の COMMAND 欄
#   (Linux/macOS共通) はプロセスの argv だけを表示し、標準入力の内容は
#   含まないため、この方式なら secret は cmdline に出ない。
#
# macOS代替: このリポジトリの開発機はmacOSで /proc が無いため、
#   - argv (/proc/*/cmdline 相当) の代替検証には `ps -o command= -p PID` を使う
#     (Linux/macOS共通のオプションで、プロセスのargvを文字列化して見せる)。
#   - process environment の代替検証には BSD ps の `eww` 拡張
#     (`ps eww -p PID`) を使う。Linuxでは `/proc/PID/environ` が使える。
# 実行中の curl プロセスを ps/proc で覗くには生きている必要があるため、
# mock サーバはわざと応答を遅延させ、その間に snapshot を取る。

set -u

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TMP="$(mktemp -d)"
trap 'kill $(jobs -p) 2>/dev/null; rm -rf "$TMP"' EXIT

ok=0
fail=0
check() {
	local condition="$1" message="$2"
	if eval "$condition"; then
		printf 'ok - %s\n' "$message"
		ok=$((ok + 1))
	else
		printf 'not ok - %s\n' "$message"
		fail=$((fail + 1))
	fi
}

IS_LINUX=0
[ "$(uname -s)" = "Linux" ] && IS_LINUX=1

# _snapshot_argv PID -> stdout: そのPIDのargvを1行の文字列として出す。
# Linux: /proc/PID/cmdline (NUL区切りをスペースへ)。macOS等: `ps -o command=`。
_snapshot_argv() {
	local pid="$1"
	if [ "$IS_LINUX" = "1" ] && [ -r "/proc/$pid/cmdline" ]; then
		tr '\0' ' ' <"/proc/$pid/cmdline" 2>/dev/null
	else
		ps -o command= -p "$pid" 2>/dev/null
	fi
}

# _snapshot_env PID -> stdout: そのPIDのprocess environmentを出す。
# Linux: /proc/PID/environ。macOS等: `ps eww` (BSD ps の環境変数表示拡張)。
_snapshot_env() {
	local pid="$1"
	if [ "$IS_LINUX" = "1" ] && [ -r "/proc/$pid/environ" ]; then
		tr '\0' '\n' <"/proc/$pid/environ" 2>/dev/null
	else
		ps eww -p "$pid" 2>/dev/null
	fi
}

SENTINEL="SENTINEL_HTTP_ARGV_DO_NOT_LEAK_7c1e4a"
SENTINEL_SECRET2="SENTINEL_CLIENT_SECRET_9d3f21"
SENTINEL_REFRESH="SENTINEL_REFRESH_TOKEN_b6a870"

PORT=$((20000 + (RANDOM % 20000)))
LOG_FILE="$TMP/requests.log"
: >"$LOG_FILE"
python3 "$ROOT/tests/support/mock_http_server.py" "$PORT" "$LOG_FILE" 1.2 &
SERVER_PID=$!

# サーバ起動待ち
for _ in $(seq 1 50); do
	curl -fsS --max-time 1 "http://127.0.0.1:$PORT/__ready" >/dev/null 2>&1 && break
	sleep 0.1
done
: >"$LOG_FILE"

last_request_field() {
	python3 - "$LOG_FILE" "$1" <<'PY'
import json, sys
path, field = sys.argv[1], sys.argv[2]
lines = [l for l in open(path, encoding="utf-8") if l.strip()]
if not lines:
	print("")
	raise SystemExit(0)
rec = json.loads(lines[-1])
value = rec
for part in field.split("."):
	value = value.get(part, {}) if isinstance(value, dict) else ""
print(value if isinstance(value, str) else json.dumps(value, ensure_ascii=False))
PY
}

# ---------------------------------------------------------------------------
# A. twitch_polls.sh を実物のまま、mockサーバへ向けて実行する (TWITCH_API_BASE)
# ---------------------------------------------------------------------------
WORKDIR_A="$TMP/twitch"
mkdir -p "$WORKDIR_A/tmp/state"
export TWITCH_POLLS_ENABLED=1
export TWITCH_POLLS_TOKEN="$SENTINEL"
export TWITCH_CLIENT_ID="$SENTINEL_SECRET2"
export TWITCH_BROADCASTER_ID="broadcaster-1"
export TMP_STATE_DIR="$WORKDIR_A/tmp/state"
export TWITCH_API_BASE="http://127.0.0.1:$PORT/helix"

(
	cd "$ROOT" && ./twitch_polls.sh live >"$TMP/twitch_live.out" 2>"$TMP/twitch_live.err"
) &
TWITCH_PID=$!
sleep 0.4
# curl はこの supervisor shell の孫プロセスとして生きているはず。argv/env を覗く。
CURL_PID=$(pgrep -n -f "curl.*127.0.0.1:$PORT" 2>/dev/null || true)
if [ -n "$CURL_PID" ]; then
	argv_snapshot=$(_snapshot_argv "$CURL_PID")
	env_snapshot=$(_snapshot_env "$CURL_PID")
	check "! printf '%s' \"\$argv_snapshot\" | grep -q '$SENTINEL'" \
		"twitch_polls.sh: 実行中curlのargvにTOKENが現れない (実測argv: ${argv_snapshot:0:200})"
	check "! printf '%s' \"\$argv_snapshot\" | grep -q '$SENTINEL_SECRET2'" \
		"twitch_polls.sh: 実行中curlのargvにCLIENT_IDが現れない"
	check "! printf '%s' \"\$env_snapshot\" | grep -q '$SENTINEL'" \
		"twitch_polls.sh: 実行中curlのprocess environmentにTOKENが現れない (代替: $([ "$IS_LINUX" = 1 ] && echo '/proc/PID/environ' || echo 'ps eww'))"
else
	check "false" "twitch_polls.sh: 遅延中のcurlプロセスをpgrepで捕捉できた (捕捉できず、この2件は未実測)"
	check "false" "(argv/env snapshotは前項の捕捉失敗により未実測)"
fi
wait "$TWITCH_PID" 2>/dev/null

twitch_auth_header=$(last_request_field "headers.Authorization")
twitch_client_header=$(last_request_field "headers.Client-Id")
check "[ \"\$twitch_auth_header\" = \"Bearer $SENTINEL\" ]" \
	"twitch_polls.sh: mockサーバにAuthorizationヘッダが正しく届く (実測: '${twitch_auth_header}')"
check "[ \"\$twitch_client_header\" = \"$SENTINEL_SECRET2\" ]" \
	"twitch_polls.sh: mockサーバにClient-Idヘッダが正しく届く (実測: '${twitch_client_header}')"
twitch_out=$(cat "$TMP/twitch_live.out")
check "printf '%s' \"\$twitch_out\" | python3 -c 'import json,sys; d=json.load(sys.stdin); assert d[\"ok\"]' 2>/dev/null" \
	"twitch_polls.sh: mock応答を正常に処理して ok:true を返す (実測: ${twitch_out})"

# ---------------------------------------------------------------------------
# B. youtube_chat.sh と同一の仕組み (lib/curl_secure.sh + curl -K -) を
#    実際の関数コピーで直接呼び、Bearer token / API key(URLクエリ) /
#    OAuth client_secret・refresh_token が argv に出ないことを確認する。
#    (youtube_chat.sh 自体は起動時に .env 全体を読み worker daemon 化するため、
#    ここでは同スクリプトが定義する _api_get / _send_api / _oauth_access_token
#    と全く同じ curl 呼び出しパターンを直接検証する)
# ---------------------------------------------------------------------------
# shellcheck source=/dev/null
source "$ROOT/lib/curl_secure.sh"

: >"$LOG_FILE"
YT_BEARER="$SENTINEL"
YT_API_KEY="SENTINEL_YT_API_KEY_4471aa"
yt_url="http://127.0.0.1:$PORT/youtube/v3/videos?part=snippet&key=${YT_API_KEY}"
yt_cfg=$(_curl_cfg_build url "$yt_url" header "Authorization: Bearer ${YT_BEARER}")
(
	printf '%s' "$yt_cfg" | curl -fsS --max-time 5 -K - >"$TMP/yt_api_get.out" 2>"$TMP/yt_api_get.err"
) &
YT_PID=$!
sleep 0.4
CURL_PID2=$(pgrep -n -f "curl -fsS --max-time 5 -K -" 2>/dev/null || true)
if [ -n "$CURL_PID2" ]; then
	argv2=$(_snapshot_argv "$CURL_PID2")
	env2=$(_snapshot_env "$CURL_PID2")
	check "! printf '%s' \"\$argv2\" | grep -q '$YT_BEARER'" \
		"youtube _api_get相当: 実行中curlのargvにAuthorizationトークンが現れない"
	check "! printf '%s' \"\$argv2\" | grep -q '$YT_API_KEY'" \
		"youtube _api_get相当: 実行中curlのargvにURLクエリ内APIキーが現れない"
	check "! printf '%s' \"\$env2\" | grep -q '$YT_BEARER'" \
		"youtube _api_get相当: 実行中curlのprocess environmentにトークンが現れない"
else
	check "false" "youtube _api_get相当: 遅延中のcurlプロセスを捕捉できた (未実測)"
	check "false" "(URLクエリAPIキーのargv不在は前項の捕捉失敗により未実測)"
	check "false" "(process environment不在は前項の捕捉失敗により未実測)"
fi
wait "$YT_PID" 2>/dev/null

yt_auth_header=$(last_request_field "headers.Authorization")
yt_path=$(last_request_field "path")
check "[ \"\$yt_auth_header\" = \"Bearer $YT_BEARER\" ]" \
	"youtube _api_get相当: mockサーバにAuthorizationヘッダが正しく届く (実測: '${yt_auth_header}')"
check "printf '%s' \"\$yt_path\" | grep -q \"key=${YT_API_KEY}\"" \
	"youtube _api_get相当: mockサーバにAPIキー付きURLが正しく届く (実測path: ${yt_path})"

# OAuth refresh (client_id/client_secret/refresh_token を data-urlencode で渡す形)
: >"$LOG_FILE"
oauth_cfg=$(_curl_cfg_build \
	url "http://127.0.0.1:$PORT/oauth2/token" \
	data-urlencode "client_id=oauth-client-id" \
	data-urlencode "client_secret=${SENTINEL_SECRET2}" \
	data-urlencode "refresh_token=${SENTINEL_REFRESH}" \
	data "grant_type=refresh_token")
(
	printf '%s' "$oauth_cfg" | curl -sS --max-time 5 -o "$TMP/oauth.out" -w '%{http_code}' -K - >"$TMP/oauth_code.out" 2>"$TMP/oauth.err"
) &
OAUTH_PID=$!
sleep 0.4
CURL_PID3=$(pgrep -n -f "curl -sS --max-time 5 -o" 2>/dev/null || true)
if [ -n "$CURL_PID3" ]; then
	argv3=$(_snapshot_argv "$CURL_PID3")
	check "! printf '%s' \"\$argv3\" | grep -q '$SENTINEL_SECRET2'" \
		"youtube _oauth_access_token相当: 実行中curlのargvにclient_secretが現れない"
	check "! printf '%s' \"\$argv3\" | grep -q '$SENTINEL_REFRESH'" \
		"youtube _oauth_access_token相当: 実行中curlのargvにrefresh_tokenが現れない"
else
	check "false" "youtube _oauth_access_token相当: 遅延中のcurlプロセスを捕捉できた (未実測)"
	check "false" "(refresh_tokenのargv不在は前項の捕捉失敗により未実測)"
fi
wait "$OAUTH_PID" 2>/dev/null

oauth_body=$(last_request_field "body")
check "printf '%s' \"\$oauth_body\" | grep -q 'client_secret=${SENTINEL_SECRET2}'" \
	"youtube _oauth_access_token相当: mockサーバにclient_secretが正しく届く(form body)"
check "printf '%s' \"\$oauth_body\" | grep -q 'refresh_token=${SENTINEL_REFRESH}'" \
	"youtube _oauth_access_token相当: mockサーバにrefresh_tokenが正しく届く(form body)"

kill "$SERVER_PID" 2>/dev/null
wait "$SERVER_PID" 2>/dev/null

printf '%s\n' "$SENTINEL" "$SENTINEL_SECRET2" "$SENTINEL_REFRESH" >/dev/null # sentinel宣言のみ、実credentialは不使用

printf '1..%d\n' "$((ok + fail))"
printf '%s passed, %s failed\n' "$ok" "$fail"
[ "$fail" -eq 0 ]
