# Runtime policy layered after broadcast/comment.sh.
# Keeps viewer-facing address style deterministic and gives short chat bursts a
# bounded quiet window before the existing comment generator snapshots pending.
# Re-sourcing eloop_lib.sh is supported: comment.sh refreshes the underlying
# functions first, then this file captures the fresh bases again.

_comment_runtime_policy_capture() {
	local name="$1" saved="$2" marker="$3" definition
	declare -F "$name" >/dev/null 2>&1 || return 1
	definition=$(declare -f "$name") || return 1
	if printf '%s' "$definition" | grep -qF "$marker"; then
		return 0
	fi
	eval "$(printf '%s\n' "$definition" | sed "1s/${name}/${saved}/")"
}

_comment_runtime_policy_capture \
	_append_comment_reply_contract \
	_comment_runtime_policy_base_append_comment_reply_contract \
	'_comment_runtime_policy_base_append_comment_reply_contract' || true
_comment_runtime_policy_capture \
	_is_valid_comment_talk \
	_comment_runtime_policy_base_is_valid_comment_talk \
	'_comment_runtime_policy_base_is_valid_comment_talk' || true
_comment_runtime_policy_capture \
	_comment_replace_country_references \
	_comment_runtime_policy_base_replace_country_references \
	'_comment_runtime_policy_base_replace_country_references' || true
_comment_runtime_policy_capture \
	generate_comment_response \
	_comment_runtime_policy_base_generate_comment_response \
	'_comment_runtime_policy_base_generate_comment_response' || true

# Card-gacha notifications reach chat as either ASCII "[tier] card" (current
# Twica production format), legacy fullwidth "【tier】card", or the multi-draw
# summary "N連ガチャで ...を獲得しました". Every detection point must accept all
# forms; keying on 【...】 alone silently missed 100% of live notifications.
_COMMENT_CARD_ACQUIRED_RE='が[[:space:]]*(【[^】]{1,80}】|\[[^]]{1,80}\])[^を]{0,240}を獲得しました'
_COMMENT_CARD_MULTI_RE='が[[:space:]]*[0-9]+[[:space:]]*連ガチャで[^を]{0,160}を獲得しました'

_append_comment_reply_contract() {
	local out_file="$1"
	_comment_runtime_policy_base_append_comment_reply_contract "$out_file" || return 1
	cat >>"$out_file" <<'COMMENTRUNTIMEPOLICY'

【視聴者の呼称・連続カード通知の最終契約】
- 視聴者本人の名前を呼ぶときは、必ず「同志○○」の形にしてください。「○○さん」「○○様」「○○くん」「○○ちゃん」のような通常の敬称で呼ばないでください。これは通常モード・メリケンAIモードとも共通です。
- カードガチャ通知で実際にカードを獲得した人物を呼ぶ場合も「同志○○」と呼んでください。通知を投稿したbot/配信者名ではなく、本文の「AがBを獲得しました」のAが獲得者です。
- カード獲得通知は「[コモン]」のような半角角括弧のことも「【コモン】」のような全角括弧のことも、また「N連ガチャで…を獲得しました」というまとめ通知のこともあります。いずれも同じカードガチャ獲得通知として扱ってください。
- 今回の返信対象が、同じ視聴者による連続したカードガチャ獲得通知だけで2件以上ある場合は例外的に、1件ずつ同じ挨拶を繰り返さず、獲得カードをまとめて1段落で返してください。「同志A」と最初に1回呼び、今回引いたカード群への反応をまとめます。各カードを百科事典のように個別解説せず、特に面白い1〜2点へ絞ってください。
- カード通知と通常コメントが混在する場合、別視聴者の通知が混ざる場合、または質問・訂正が含まれる場合は、上のまとめ例外を使わず、元の1コメント1段落・順序維持の契約を守ってください。
COMMENTRUNTIMEPOLICY
}

_comment_runtime_policy_repair_viewer_addresses() {
	local batch_file="${1:-}"
	[ -s "$batch_file" ] || {
		cat
		return 0
	}
	python3 -c '
import re, sys

batch_path = sys.argv[1]
text = sys.stdin.read()

try:
    with open(batch_path, "r", encoding="utf-8", errors="replace") as f:
        batch_lines = f.read().replace("\r\n", "\n").replace("\r", "\n").splitlines()
except OSError:
    raise SystemExit(2)

names = set()

def add_name(value):
    value = value.strip()
    if value.startswith("@"):
        value = value[1:].strip()
    if not value or len(value) > 64:
        return
    if any(ch in value for ch in "\r\n\t"):
        return
    names.add(value)

for raw in batch_lines:
    line = raw.strip()
    if not line:
        continue
    body = line
    if ": " in line:
        head, body = line.split(": ", 1)
        add_name(head)
    # Card-gacha posts are often emitted by a bot; the actual viewer is the
    # person before "が【...】...を獲得しました", not the posting account.
    for match in re.finditer(r"(?:^|\s)(.{1,64}?)\s*が\s*(?:【[^】]{1,80}】|\[[^\]]{1,80}\]|(?:[0-9]+\s*連ガチャで)).{0,320}?を獲得しました", body):
        candidate = match.group(1).strip()
        if candidate and not any(ch in candidate for ch in "、。！？!?：:【】"):
            add_name(candidate)

if not names:
    sys.stdout.write(text)
    raise SystemExit(0)

honorifics = ("さん", "様", "くん", "ちゃん")
punctuation = ("、", ",", "：", ":")
ordered_names = sorted(names, key=len, reverse=True)
parts = re.split(r"(\n\s*\n+)", text.replace("\r\n", "\n").replace("\r", "\n"))

for i, para in enumerate(parts):
    if not para or re.fullmatch(r"\n\s*\n+", para):
        continue
    leading_len = len(para) - len(para.lstrip())
    leading = para[:leading_len]
    body = para[leading_len:]
    if not body or body.startswith(("同志", "みなさん", "皆さん")):
        continue
    repaired = None
    for name in ordered_names:
        for mention in (name, "@" + name):
            candidates = (mention,) + tuple(mention + suffix for suffix in honorifics)
            for candidate in candidates:
                if not body.startswith(candidate):
                    continue
                tail = body[len(candidate):]
                if tail.startswith(punctuation):
                    repaired = "同志" + name + tail
                    break
            if repaired is not None:
                break
        if repaired is not None:
            break
    if repaired is not None:
        parts[i] = leading + repaired

sys.stdout.write("".join(parts))
' "$batch_file"
}

_comment_replace_country_references() {
	local normalized batch_file="${comment_batch_file:-}"
	normalized=$(_comment_runtime_policy_base_replace_country_references) || return 1
	if [ -n "$batch_file" ] && [ -s "$batch_file" ]; then
		printf '%s' "$normalized" | _comment_runtime_policy_repair_viewer_addresses "$batch_file"
		return $?
	fi
	printf '%s' "$normalized"
}

_comment_runtime_policy_has_plain_honorific_address() {
	python3 -c '
import re, sys
text = sys.stdin.read().replace("\r\n", "\n").replace("\r", "\n")
# Only inspect paragraph starts, where the reply contract places viewer
# addresses. Generic audience phrases and already-repaired 同志 addresses are
# not ordinary-honorific regressions.
allowed = ("同志", "みなさん", "皆さん")
for para in re.split(r"\n\s*\n+", text):
    head = para.lstrip()
    if not head:
        continue
    if head.startswith(allowed):
        continue
    if re.match(r"^@?[^\s、。！？!?：:,]{1,48}(?:さん|様|くん|ちゃん)[、,：:]", head):
        raise SystemExit(0)
raise SystemExit(1)
'
}

_is_valid_comment_talk() {
	local talk="$1"
	_comment_runtime_policy_base_is_valid_comment_talk "$talk" || return 1
	# Known viewer names are repaired deterministically before this validator.
	# Any ordinary honorific that remains unresolved is unsafe/ambiguous and
	# keeps the existing full-regeneration fallback rather than guessing.
	if printf '%s' "$talk" | _comment_runtime_policy_has_plain_honorific_address; then
		return 1
	fi
	return 0
}

_comment_debounce_fetch() {
	local source="${1:-twitch}"
	case "$source" in
	twitch|Twitch|"") ./twitch_chat.sh fetch 2>/dev/null || true ;;
	youtube|YouTube|yt) ./youtube_chat.sh fetch 2>/dev/null || true ;;
	kick|Kick) ./kick_chat.sh fetch 2>/dev/null || true ;;
	*) return 1 ;;
	esac
}

_comment_debounce_outfile() {
	local source="${1:-twitch}"
	case "$source" in
	twitch|Twitch|"") printf '%s' 'tmp/twitch_comments.txt' ;;
	youtube|YouTube|yt) printf '%s' "${YOUTUBE_CHAT_OUTFILE:-tmp/youtube_comments.txt}" ;;
	kick|Kick) printf '%s' "${KICK_CHAT_OUTFILE:-tmp/kick_comments.txt}" ;;
	*) return 1 ;;
	esac
}

_comment_debounce_signature() {
	local path="$1"
	[ -s "$path" ] || {
		printf '%s' empty
		return 0
	}
	if command -v sha256sum >/dev/null 2>&1; then
		sha256sum "$path" 2>/dev/null | awk '{print $1}'
	elif command -v shasum >/dev/null 2>&1; then
		shasum -a 256 "$path" 2>/dev/null | awk '{print $1}'
	else
		cksum "$path" 2>/dev/null | awk '{print $1 ":" $2}'
	fi
}

_comment_debounce_is_card_batch() {
	local path="$1"
	[ -s "$path" ] || return 1
	grep -Eq "$_COMMENT_CARD_ACQUIRED_RE|$_COMMENT_CARD_MULTI_RE" "$path" 2>/dev/null
}

_comment_debounce_now() {
	date +%s
}

_comment_debounce_sleep() {
	sleep 1
}

_comment_debounce_uint() {
	local value="$1" fallback="$2" max="$3"
	case "$value" in
	''|*[!0-9]*) value="$fallback" ;;
	esac
	[ "$value" -gt "$max" ] 2>/dev/null && value="$max"
	printf '%s' "$value"
}

_comment_debounce_wait() {
	local source="${1:-twitch}" outfile="" quiet card_quiet max_wait
	outfile=$(_comment_debounce_outfile "$source") || return 1
	quiet=$(_comment_debounce_uint "${COMMENT_DEBOUNCE_SEC:-3}" 3 30)
	card_quiet=$(_comment_debounce_uint "${COMMENT_CARD_DEBOUNCE_SEC:-6}" 6 30)
	max_wait=$(_comment_debounce_uint "${COMMENT_DEBOUNCE_MAX_SEC:-12}" 12 60)
	[ "$max_wait" -lt "$quiet" ] && max_wait="$quiet"
	[ "$max_wait" -lt "$card_quiet" ] && max_wait="$card_quiet"

	_comment_debounce_fetch "$source" || true
	[ -s "$outfile" ] || return 1
	[ "$quiet" -eq 0 ] && [ "$card_quiet" -eq 0 ] && return 0

	local started last_changed now sig new_sig target_quiet
	started=$(_comment_debounce_now)
	last_changed="$started"
	sig=$(_comment_debounce_signature "$outfile")

	while true; do
		now=$(_comment_debounce_now)
		target_quiet="$quiet"
		if _comment_debounce_is_card_batch "$outfile"; then
			target_quiet="$card_quiet"
		fi
		if [ $((now - last_changed)) -ge "$target_quiet" ] || [ $((now - started)) -ge "$max_wait" ]; then
			return 0
		fi
		[ -f tmp/stop ] && return 1
		_comment_debounce_sleep
		_comment_debounce_fetch "$source" || true
		new_sig=$(_comment_debounce_signature "$outfile")
		if [ "$new_sig" != "$sig" ]; then
			sig="$new_sig"
			last_changed=$(_comment_debounce_now)
		fi
	done
}

# Normalized identity of the card recipient when the batch is made up of
# nothing but card notifications from one viewer. Empty output means "do not
# consolidate" (mixed content, multiple viewers, or malformed lines).
_comment_debounce_card_consolidation_key() {
	local path="$1"
	[ -s "$path" ] || return 1
	python3 - "$path" <<'PY' 2>/dev/null
import re
import sys
import unicodedata

path = sys.argv[1]

CARD_RE = re.compile(r"が\s*(?:【[^】]{1,80}】|\[[^\]]{1,80}\])\s*[^を]{0,360}?を獲得しました")
MULTI_RE = re.compile(r"が\s*[0-9]+\s*連ガチャで\s*[^を]{0,200}?を獲得しました")
PREFIX_RE = re.compile(r"^(?:\[(?:BITS|SUB|視聴記録)\]\s*)+", re.IGNORECASE)
VIEWER_RE = re.compile(
    r"^\s*@?(?P<viewer>.{1,64}?)\s*が\s*(?:【[^】]{1,80}】|\[[^\]]{1,80}\]|[0-9]+\s*連ガチャで)"
)

try:
    with open(path, encoding="utf-8", errors="replace") as f:
        lines = [line.strip() for line in f if line.strip()]
except OSError:
    raise SystemExit(1)
if not lines:
    raise SystemExit(1)

viewers = set()
for line in lines:
    body = PREFIX_RE.sub("", line)
    if not (CARD_RE.search(body) or MULTI_RE.search(body)):
        raise SystemExit(1)
    match = VIEWER_RE.match(body)
    if not match:
        raise SystemExit(1)
    name = match.group("viewer").strip().lstrip("@")
    name = unicodedata.normalize("NFKC", name)
    name = re.sub(r"\s+", " ", name).strip().casefold()
    if not name:
        raise SystemExit(1)
    viewers.add(name)
if len(viewers) != 1:
    raise SystemExit(1)
sys.stdout.write(next(iter(viewers)))
PY
}

_comment_card_hold_state_path() {
	printf '%s/comment_card_hold_%s' "${COMMENT_CARD_HOLD_STATE_DIR:-tmp/state}" "$1"
}

_comment_card_hold_clear() {
	rm -f "$(_comment_card_hold_state_path "$1")" 2>/dev/null || true
}

# Non-blocking carry-over for consecutive single draws by one viewer. The
# second-scale debounce cannot catch production draws, which are tens of
# seconds to minutes apart, so a card-only same-viewer batch is held across
# worker ticks until the viewer stops drawing (quiet window) or the hard
# ceiling is reached. Returns 0 = generate now, 1 = keep holding this tick.
# Holding never blocks the worker loop, so outbound chat/clip consumption
# keeps running while cards accumulate.
_comment_card_consolidation_gate() {
	local source="$1" outfile key state_file quiet max now sig
	local s_key="" s_sig="" s_started="" s_last=""
	[ "${COMMENT_CARD_CONSOLIDATE_ENABLED:-1}" = "1" ] || return 0
	outfile=$(_comment_debounce_outfile "$source") || return 0
	[ -s "$outfile" ] || {
		_comment_card_hold_clear "$source"
		return 0
	}
	key=$(_comment_debounce_card_consolidation_key "$outfile" 2>/dev/null || true)
	if [ -z "$key" ]; then
		_comment_card_hold_clear "$source"
		return 0
	fi
	quiet=$(_comment_debounce_uint "${COMMENT_CARD_CONSOLIDATE_QUIET_SEC:-20}" 20 300)
	max=$(_comment_debounce_uint "${COMMENT_CARD_CONSOLIDATE_MAX_SEC:-180}" 180 600)
	[ "$max" -lt "$quiet" ] && max="$quiet"
	state_file=$(_comment_card_hold_state_path "$source")
	mkdir -p "$(dirname "$state_file")" 2>/dev/null || true
	now=$(_comment_debounce_now)
	sig=$(_comment_debounce_signature "$outfile")
	if [ -f "$state_file" ]; then
		IFS=$'\t' read -r s_key s_sig s_started s_last <"$state_file" 2>/dev/null || true
	fi
	case "$s_started" in '' | *[!0-9]*) s_started="" ;; esac
	case "$s_last" in '' | *[!0-9]*) s_last="" ;; esac
	if [ "$s_key" = "$key" ] && [ -n "$s_started" ]; then
		if [ "$s_sig" != "$sig" ]; then
			s_last="$now"
			printf '%s\t%s\t%s\t%s\n' "$key" "$sig" "$s_started" "$s_last" >"$state_file"
		fi
	else
		s_started="$now"
		s_last="$now"
		printf '%s\t%s\t%s\t%s\n' "$key" "$sig" "$s_started" "$s_last" >"$state_file"
	fi
	if [ $((now - s_last)) -ge "$quiet" ] || [ $((now - s_started)) -ge "$max" ]; then
		_comment_card_hold_clear "$source"
		return 0
	fi
	return 1
}

generate_comment_response() {
	local source="${1:-twitch}"
	# Debounce before the base function takes its pending snapshot. New arrivals
	# reset the quiet window, bounded by COMMENT_DEBOUNCE_MAX_SEC. Card bursts get
	# a slightly wider default window so consecutive draws land in one prompt;
	# card-only same-viewer batches are then carried over across ticks.
	_comment_debounce_wait "$source" || return 0
	_comment_card_consolidation_gate "$source" || return 0
	_comment_runtime_policy_base_generate_comment_response "$@"
}
