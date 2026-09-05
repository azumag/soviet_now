#!/bin/bash
set -u

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
mkdir -p "$TMP/bin" "$TMP/tmp/state" "$TMP/lib"
cp "$ROOT/twitch_polls.sh" "$TMP/twitch_polls.sh"
cp "$ROOT/lib/curl_secure.sh" "$TMP/lib/curl_secure.sh"
chmod +x "$TMP/twitch_polls.sh"

cat >"$TMP/bin/curl" <<'SH'
#!/bin/bash
out="" method="GET" url=""
while [ "$#" -gt 0 ]; do
  case "$1" in
    -o) out="$2"; shift 2 ;;
    -X) method="$2"; shift 2 ;;
    http*) url="$1"; shift ;;
    *) shift ;;
  esac
done
case "$url:$method" in
  *helix/streams*:GET)
    printf '%s' '{"data":[{"id":"stream-1"}]}' >"$out" ;;
  *helix/polls*:POST)
    printf '%s' '{"data":[{"id":"poll-1","title":"好きな季節は？","choices":[{"id":"a","title":"春","votes":0},{"id":"b","title":"秋","votes":0}],"status":"ACTIVE","duration":120}]}' >"$out" ;;
  *helix/polls*id=poll-1*:GET)
    printf '%s' '{"data":[{"id":"poll-1","title":"好きな季節は？","choices":[{"id":"a","title":"春","votes":2},{"id":"b","title":"秋","votes":3}],"status":"COMPLETED","duration":120}]}' >"$out" ;;
  *helix/polls*:GET)
    printf '%s' '{"data":[]}' >"$out" ;;
  *) printf '%s' '{"message":"unexpected"}' >"$out"; printf '500'; exit 0 ;;
esac
printf '200'
SH
chmod +x "$TMP/bin/curl"

export PATH="$TMP/bin:$PATH"
export TWITCH_POLLS_ENABLED=1
export TWITCH_POLLS_TOKEN=test-token
export TWITCH_CLIENT_ID=test-client
export TWITCH_BROADCASTER_ID=test-broadcaster
export TMP_STATE_DIR="$TMP/tmp/state"

pass=0 fail=0
ok() { pass=$((pass + 1)); printf 'ok %d - %s\n' "$pass" "$1"; }
not_ok() { fail=$((fail + 1)); printf 'not ok - %s\n' "$1"; }
assert_json() {
  local json="$1" expr="$2" label="$3"
  if printf '%s' "$json" | python3 -c "import json,sys; d=json.load(sys.stdin); assert $expr" 2>/dev/null; then ok "$label"; else not_ok "$label: $json"; fi
}

live=$(cd "$TMP" && ./twitch_polls.sh live)
assert_json "$live" 'd["ok"] and d["live"]' "ライブ状態を取得"

printf '%s' '{"title":"好きな季節は？","choices":["春","秋"]}' >"$TMP/draft.json"
created=$(cd "$TMP" && ./twitch_polls.sh create "$TMP/draft.json")
assert_json "$created" 'd["ok"] and d["poll"]["id"] == "poll-1"' "アンケートを作成"
if [ -s "$TMP/tmp/state/current_poll.json" ]; then ok "作成stateを永続化"; else not_ok "作成stateを永続化"; fi

status=$(cd "$TMP" && ./twitch_polls.sh status poll-1)
assert_json "$status" 'd["poll"]["status"] == "COMPLETED" and d["poll"]["choices"][1]["votes"] == 3' "終了結果と得票数を取得"

printf '%s' '{"title":"壊れた質問","choices":["一つだけ"]}' >"$TMP/invalid.json"
invalid=$(cd "$TMP" && ./twitch_polls.sh create "$TMP/invalid.json")
assert_json "$invalid" 'not d["ok"] and d["error"] == "invalid_draft"' "不正な選択肢数を拒否"

disabled=$(cd "$TMP" && TWITCH_POLLS_ENABLED=0 ./twitch_polls.sh status)
assert_json "$disabled" 'not d["ok"] and d["error"] == "disabled"' "無効時はAPI操作しない"

worker="$ROOT/workers/poll_worker.sh"
config="$ROOT/core/config.sh"
if grep -Fq 'TWITCH_POLL_INTERVAL_SEC="${TWITCH_POLL_INTERVAL_SEC:-43200}"' "$config" && grep -Fq 'TWITCH_POLL_INTERVAL_SEC:-43200' "$worker"; then
  ok "既定間隔は12時間"; else not_ok "既定間隔は12時間"; fi
for needle in 'ai_generate_list "RADIO_POLL_QUESTION"' 'ai_generate_list "RADIO_POLL_RESULT"' 'enqueue_chat_message "アンケート結果：${commentary}"' 'enqueue_audio_text "$commentary" "polls"' '手動または別プロセスのアンケートが進行中'; do
  if grep -Fq "$needle" "$worker"; then ok "worker wiring: $needle"; else not_ok "worker wiring: $needle"; fi
done

printf '1..%d\n' "$((pass + fail))"
[ "$fail" -eq 0 ]
