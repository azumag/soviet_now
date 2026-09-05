#!/bin/bash
# tests/test_update_stream_game.sh - update_stream_game.sh の回帰テスト (curl stub)。
set -u

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BIN="$ROOT/update_stream_game.sh"
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
mkdir -p "$TMP/bin" "$TMP/games"

# --- curl stub ---
cat >"$TMP/bin/curl" <<'SH'
#!/bin/bash
out="" method="GET" url=""
while [ "$#" -gt 0 ]; do
  case "$1" in
    -o) out="$2"; shift 2 ;;
    -w) want_code=1; shift 2 ;;
    -X) method="$2"; shift 2 ;;
    -H) shift 2 ;;
    -d) body="$2"; shift 2 ;;
    http*) url="$1"; shift ;;
    *) shift ;;
  esac
done
emit() { # $1=text: -o があればファイルへ、無ければ stdout へ
  if [ -n "$out" ]; then printf '%s' "$1" >"$out"; else printf '%s' "$1"; fi
  if [ "${want_code:-}" = "1" ]; then printf '%s' "${STUB_HTTP_CODE:-200}"; fi
}
# 既定応答 (${VAR:-...} は既定値内の } と衝突するため if で設定する)
if [ -z "${STUB_VALIDATE+x}" ]; then STUB_VALIDATE='{"client_id":"cid","login":"tester","scopes":["channel:manage:broadcast"]}'; fi
if [ -z "${STUB_CHANNELS+x}" ]; then STUB_CHANNELS='{"data":[{"title":"old","game_id":"1","game_name":"Old"}]}'; fi
if [ -z "${STUB_GAMES+x}" ]; then STUB_GAMES='{"data":[{"id":"11585","name":"Robots"}]}'; fi
if [ -z "${STUB_SEARCH+x}" ]; then STUB_SEARCH='{"data":[{"id":"11585","name":"Robots"}]}'; fi
case "$url" in
  *oauth2/validate*)
    emit "$STUB_VALIDATE" ;;
  *helix/channels*\&broadcaster_id=*|*helix/channels\?broadcaster_id=*)
    if [ "$method" = "PATCH" ]; then
      printf '%s' "${body:-}" >"${STUB_PATCH_OUT:-/dev/null}"
      printf '1' >>"${STUB_PATCH_COUNT:-/dev/null}"
      # Twitch PATCH /helix/channels は成功時 204
      if [ -n "$out" ]; then : >"$out"; fi
      printf '204'
    else
      emit "$STUB_CHANNELS"
    fi ;;
  *helix/games*)
    emit "$STUB_GAMES" ;;
  *helix/search/categories*)
    emit "$STUB_SEARCH" ;;
  *) emit '{"message":"unexpected"}' ;;
esac
SH
chmod +x "$TMP/bin/curl"
export PATH="$TMP/bin:$PATH"

# --- fake game toml / ops_brief ---
cat >"$TMP/games/robots.toml" <<'TOML'
[game]
name = "robots"
title = "Robots"
adapter = "cli"

[twitch]
category_id = "11585"
category_name = "Robots"
title_prefix = "[Robots]"
TOML
cat >"$TMP/games/broken.toml" <<'TOML'
[game]
name = "broken"
adapter = "cli"
TOML
cat >"$TMP/games/badid.toml" <<'TOML'
[game]
name = "badid"
adapter = "cli"

[twitch]
category_id = "abc"
category_name = "X"
title_prefix = "[X]"
TOML
printf '# brief\n- 直近の作業メモ\n- 古いメモ\n' >"$TMP/ops_brief.md"

export TWITCH_CLIENT_ID=test-client TWITCH_BROADCASTER_ID=test-bid
export TWITCH_GAME_TOKEN=test-token
export STREAM_GAME_LOG_FILE="$TMP/game.log"
export OPS_BRIEF_FILE="$TMP/ops_brief.md"
export STREAM_DAY_EPOCH=2026-03-14 STREAM_DAY_TZ=Asia/Tokyo
export STUB_PATCH_OUT="$TMP/patch_body" STUB_PATCH_COUNT="$TMP/patch_count"
rm -f "$STUB_PATCH_OUT" "$STUB_PATCH_COUNT"

pass=0 fail=0
ok() { pass=$((pass + 1)); printf 'ok %d - %s\n' "$pass" "$1"; }
not_ok() { fail=$((fail + 1)); printf 'not ok - %s\n' "$1"; }

# 1. dry-run: prefix + day + activity + strategy の組成
export STUB_CHANNELS='{"data":[{"title":"old","game_id":"1","game_name":"Old"}]}'
out="$(TWITCH_GAME_TOKEN=test-token "$BIN" --game robots --games-dir "$TMP/games" --strategy "root継続" --dry-run 2>"$TMP/e1")"
echo "$out" | grep -q '^\[Robots\] day[0-9]* 直近の作業メモ root継続$' && ok "compose title" || not_ok "compose title: $out"
[ -f "$STUB_PATCH_COUNT" ] && not_ok "dry-run must not PATCH" || ok "dry-run no PATCH"

# 2. 実PATCH: body に title+game_id が入る
rm -f "$STUB_PATCH_OUT" "$STUB_PATCH_COUNT"
"$BIN" --game robots --games-dir "$TMP/games" --strategy "root継続" >/dev/null 2>&1
[ -f "$STUB_PATCH_COUNT" ] && ok "PATCH executed" || not_ok "PATCH not executed"
python3 - "$STUB_PATCH_OUT" <<'PY' 2>/dev/null && ok "PATCH body" || not_ok "PATCH body: $(cat "$STUB_PATCH_OUT" 2>/dev/null)"
import json, sys
d = json.load(open(sys.argv[1]))
assert d["game_id"] == "11585", d
assert d["title"].startswith("[Robots] day"), d
PY

# 3. 冪等: 同一 title+game なら PATCH しない (exit 0)
rm -f "$STUB_PATCH_COUNT"
cur_title="$(python3 -c "import json; print(json.load(open('$STUB_PATCH_OUT'))['title'])")"
export STUB_CHANNELS="{\"data\":[{\"title\":\"$cur_title\",\"game_id\":\"11585\",\"game_name\":\"Robots\"}]}"
"$BIN" --game robots --games-dir "$TMP/games" --strategy "root継続" >/dev/null 2>&1
rc=$?
[ "$rc" = "0" ] && [ ! -f "$STUB_PATCH_COUNT" ] && ok "idempotent skip" || not_ok "idempotent skip rc=$rc"
export STUB_CHANNELS='{"data":[{"title":"old","game_id":"1","game_name":"Old"}]}'

# 4. toml 不在 → exit 1 / [twitch] 不在 → exit 1 / 非数値 id → exit 1
"$BIN" --game nosuch --games-dir "$TMP/games" >/dev/null 2>&1
[ "$?" = "1" ] && ok "missing toml rc=1" || not_ok "missing toml rc"
"$BIN" --game broken --games-dir "$TMP/games" >/dev/null 2>&1
[ "$?" = "1" ] && ok "missing [twitch] rc=1" || not_ok "missing [twitch] rc"
"$BIN" --game badid --games-dir "$TMP/games" >/dev/null 2>&1
[ "$?" = "1" ] && ok "non-numeric id rc=1" || not_ok "non-numeric id rc"

# 5. scope 不足 → exit 3
export STUB_VALIDATE='{"client_id":"cid","login":"tester","scopes":["channel:manage:predictions"]}'
"$BIN" --game robots --games-dir "$TMP/games" >/dev/null 2>&1
[ "$?" = "3" ] && ok "missing scope rc=3" || not_ok "missing scope rc"
export STUB_VALIDATE='{"client_id":"cid","login":"tester","scopes":["channel:manage:broadcast"]}'

# 6. token 優先順位: GAME > TITLE (TITLE を無効値にしても成功する)
export TWITCH_GAME_TOKEN=test-token TWITCH_TITLE_TOKEN=bogus
"$BIN" --game robots --games-dir "$TMP/games" --dry-run >/dev/null 2>&1
[ "$?" = "0" ] && ok "token precedence" || not_ok "token precedence"
unset TWITCH_TITLE_TOKEN

# 7. --verify 一致 → 0 / 不一致 → 5
export STUB_GAMES='{"data":[{"id":"11585","name":"Robots"}]}'
"$BIN" --verify --game robots --games-dir "$TMP/games" >/dev/null 2>&1
[ "$?" = "0" ] && ok "verify match" || not_ok "verify match"
export STUB_GAMES='{"data":[{"id":"11585","name":"Robots 2"}]}'
"$BIN" --verify --game robots --games-dir "$TMP/games" >/dev/null 2>&1
[ "$?" = "5" ] && ok "verify mismatch rc=5" || not_ok "verify mismatch rc"

# 8. --resolve は候補を表示し PATCH しない
rm -f "$STUB_PATCH_COUNT"
export STUB_SEARCH='{"data":[{"id":"11585","name":"Robots"},{"id":"313473","name":"Robots Love Ice Cream"}]}'
out="$("$BIN" --resolve "Robots" 2>/dev/null)"
echo "$out" | grep -q '11585 | Robots' && ok "resolve list" || not_ok "resolve list: $out"
[ ! -f "$STUB_PATCH_COUNT" ] && ok "resolve no PATCH" || not_ok "resolve PATCHed"

# 9. --title-only は game_id を維持する
rm -f "$STUB_PATCH_OUT"
export STUB_CHANNELS='{"data":[{"title":"old","game_id":"999","game_name":"Keep"}]}'
"$BIN" --game robots --games-dir "$TMP/games" --title-only >/dev/null 2>&1
python3 - "$STUB_PATCH_OUT" <<'PY' 2>/dev/null && ok "title-only keeps game" || not_ok "title-only: $(cat "$STUB_PATCH_OUT" 2>/dev/null)"
import json, sys
d = json.load(open(sys.argv[1]))
assert d["game_id"] == "999", d
assert d["title"].startswith("[Robots] day"), d
PY
export STUB_CHANNELS='{"data":[{"title":"old","game_id":"1","game_name":"Old"}]}'

# 10. 140字制限
WORDS="$(python3 -c "print('あ' * 200)")"
out="$("$BIN" --game robots --games-dir "$TMP/games" --activity "$WORDS" --strategy "$WORDS" --dry-run 2>/dev/null)"
[ "${#out}" -le 140 ] && ok "title <= 140" || not_ok "title too long: ${#out}"

printf '\n%d passed, %d failed\n' "$pass" "$fail"
[ "$fail" = "0" ]
