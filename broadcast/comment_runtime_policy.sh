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
	generate_comment_response \
	_comment_runtime_policy_base_generate_comment_response \
	'_comment_runtime_policy_base_generate_comment_response' || true

_append_comment_reply_contract() {
	local out_file="$1"
	_comment_runtime_policy_base_append_comment_reply_contract "$out_file" || return 1
	cat >>"$out_file" <<'COMMENTRUNTIMEPOLICY'

【視聴者の呼称・連続カード通知の最終契約】
- 視聴者本人の名前を呼ぶときは、必ず「同志○○」の形にしてください。「○○さん」「○○様」「○○くん」「○○ちゃん」のような通常の敬称で呼ばないでください。これは通常モード・メリケンAIモードとも共通です。
- カードガチャ通知で実際にカードを獲得した人物を呼ぶ場合も「同志○○」と呼んでください。通知を投稿したbot/配信者名ではなく、本文の「AがBを獲得しました」のAが獲得者です。
- 今回の返信対象が、同じ視聴者による連続したカードガチャ獲得通知だけで2件以上ある場合は例外的に、1件ずつ同じ挨拶を繰り返さず、獲得カードをまとめて1段落で返してください。「同志A」と最初に1回呼び、今回引いたカード群への反応をまとめます。各カードを百科事典のように個別解説せず、特に面白い1〜2点へ絞ってください。
- カード通知と通常コメントが混在する場合、別視聴者の通知が混ざる場合、または質問・訂正が含まれる場合は、上のまとめ例外を使わず、元の1コメント1段落・順序維持の契約を守ってください。
COMMENTRUNTIMEPOLICY
}

_comment_runtime_policy_has_plain_honorific_address() {
	python3 -c '
import re, sys
text = sys.stdin.read().replace("\r\n", "\n").replace("\r", "\n")
# Only inspect paragraph starts, where the reply contract places viewer
# addresses. Generic audience phrases are not individual-name addresses.
allowed = ("みなさん", "皆さん")
for para in re.split(r"\n\s*\n+", text):
    head = para.lstrip()
    if not head:
        continue
    if head.startswith(allowed):
        continue
    if re.match(r"^@?[^\s、。！？!?：:,]{1,48}さん[、,：:]", head):
        raise SystemExit(0)
raise SystemExit(1)
'
}

_is_valid_comment_talk() {
	local talk="$1"
	_comment_runtime_policy_base_is_valid_comment_talk "$talk" || return 1
	# Do not silently accept a regression back to ordinary -san viewer address.
	# The generator will retry using the final prompt contract above.
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
	grep -Eq 'が[[:space:]]*【[^】]{1,80}】.{0,320}を獲得しました' "$path" 2>/dev/null
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

generate_comment_response() {
	local source="${1:-twitch}"
	# Debounce before the base function takes its pending snapshot. New arrivals
	# reset the quiet window, bounded by COMMENT_DEBOUNCE_MAX_SEC. Card bursts get
	# a slightly wider default window so consecutive draws land in one prompt.
	_comment_debounce_wait "$source" || return 0
	_comment_runtime_policy_base_generate_comment_response "$@"
}
