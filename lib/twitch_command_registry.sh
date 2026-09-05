# lib/twitch_command_registry.sh - Twitch chat command 認可 registry (deny-by-default)
#
# 背景 (docich issue #37):
#   twitch_chat_daemon.sh は irc.chat.twitch.tv:6667 への平文(非TLS)IRC接続で受信した
#   PRIVMSG/USERNOTICEのtags(display-name, badges, user-id等)を、そのままOBS配信開始や
#   Elgato Wave Link再起動などのside effectの認可根拠にしていた。この接続はTLSも
#   署名検証も無いため、tagsの内容を認可の根拠にするのは安全ではない。
#
#   認証済みtransport (docich issue #38) が完了して明示的に有効化されるまでは、
#   viewer以外のroleを要求するcommandは常にdenyする。viewer role(誰でも到達できる
#   権限、特権の昇格を伴わない)のcommandのみ、従来どおり許可し得る。
#
# 使い方:
#   source lib/twitch_command_registry.sh
#   if twitch_cmd_authorize "clip" "$user_id" "$is_mod_or_broadcaster"; then
#       # side effect 本体はここでのみ実行する
#   fi
#
# registryに登録の無いcommand idはtwitch_cmd_authorizeが常にdeny(fail closed)する。

declare -gA _TWITCH_CMD_ROLE=()
declare -gA _TWITCH_CMD_SIDE_EFFECT=()
declare -gA _TWITCH_CMD_RATE_LIMIT=()
declare -gA _TWITCH_CMD_CONFIRMATION=()
declare -gA _TWITCH_CMD_IDEMPOTENCY=()
declare -gA _TWITCH_CMD_AUDIT=()

TWITCH_CMD_AUDIT_LOG="${TWITCH_CMD_AUDIT_LOG:-${TWITCH_CHAT_DIR:-tmp/.twitch_chat}/command_audit.log}"

# 認証済みtransport (#38) が完了し、明示的に "1" を設定するまでは常に 0。
# 平文IRC badges/display-nameタグを認可根拠にしないための deny-by-default gate。
# 「未確認」注記: #38 は本PR時点で未完了のため、この値は既定0のまま運用する想定。
TWITCH_TRANSPORT_AUTHENTICATED="${TWITCH_TRANSPORT_AUTHENTICATED:-0}"

# twitch_cmd_register <id> <required_role> <side_effect:true|false> <rate_limit_sec>
#                      <confirmation:true|false> <idempotency-note> <audit:true|false>
twitch_cmd_register() {
    local id="$1" role="$2" side_effect="$3" rate_limit="$4" confirmation="$5" idempotency="$6" audit="$7"
    _TWITCH_CMD_ROLE["$id"]="$role"
    _TWITCH_CMD_SIDE_EFFECT["$id"]="$side_effect"
    _TWITCH_CMD_RATE_LIMIT["$id"]="$rate_limit"
    _TWITCH_CMD_CONFIRMATION["$id"]="$confirmation"
    _TWITCH_CMD_IDEMPOTENCY["$id"]="$idempotency"
    _TWITCH_CMD_AUDIT["$id"]="$audit"
}

twitch_cmd_registered() {
    [ -n "${_TWITCH_CMD_ROLE[$1]+x}" ]
}

twitch_cmd_field() {
    local id="$1" field="$2"
    case "$field" in
    role) printf '%s' "${_TWITCH_CMD_ROLE[$id]:-}" ;;
    side_effect) printf '%s' "${_TWITCH_CMD_SIDE_EFFECT[$id]:-}" ;;
    rate_limit) printf '%s' "${_TWITCH_CMD_RATE_LIMIT[$id]:-}" ;;
    confirmation) printf '%s' "${_TWITCH_CMD_CONFIRMATION[$id]:-}" ;;
    idempotency) printf '%s' "${_TWITCH_CMD_IDEMPOTENCY[$id]:-}" ;;
    audit) printf '%s' "${_TWITCH_CMD_AUDIT[$id]:-}" ;;
    esac
}

twitch_cmd_list_ids() {
    local id
    for id in "${!_TWITCH_CMD_ROLE[@]}"; do printf '%s\n' "$id"; done
}

twitch_cmd_audit_log() {
    local id="$1" user_id="$2" decision="$3" detail="${4:-}"
    mkdir -p "$(dirname "$TWITCH_CMD_AUDIT_LOG")" 2>/dev/null || true
    printf '%s\tcmd=%s\tuser_id=%s\tdecision=%s\t%s\n' \
        "$(date '+%Y-%m-%dT%H:%M:%S%z')" "$id" "${user_id:-unknown}" "$decision" "$detail" \
        >>"$TWITCH_CMD_AUDIT_LOG" 2>/dev/null || true
}

# twitch_cmd_authorize <id> <user_id> <is_mod_or_broadcaster: true|false>
# 戻り値0=許可, 1=拒否。全ての決定を audit ログへ残す。
#
#   - registryに無いcommand id                → deny (fail closed)
#   - required_role=viewer                     → 常にallow (特権の昇格が無いため)
#   - required_role!=viewer かつ transport未認証 → 常にdeny (#38完了まで)
#   - required_role!=viewer かつ transport認証済み → stable user_id必須 + role一致で allow
twitch_cmd_authorize() {
    local id="$1" user_id="${2:-}" is_mod_or_broadcaster="${3:-false}"
    if ! twitch_cmd_registered "$id"; then
        twitch_cmd_audit_log "$id" "$user_id" "deny" "not-registered"
        return 1
    fi
    local role
    role=$(twitch_cmd_field "$id" role)
    if [ "$role" = "viewer" ]; then
        twitch_cmd_audit_log "$id" "$user_id" "allow" "role=viewer"
        return 0
    fi
    if [ "${TWITCH_TRANSPORT_AUTHENTICATED:-0}" != "1" ]; then
        twitch_cmd_audit_log "$id" "$user_id" "deny" "role=$role transport-not-authenticated"
        return 1
    fi
    # 認証済みtransport後の想定経路 (#38完了後): 表示名ではなくstable user-idの
    # 検証済み値が必須。badges等の検証済みroleとの一致も必要。
    if [ -z "$user_id" ]; then
        twitch_cmd_audit_log "$id" "$user_id" "deny" "role=$role missing-stable-user-id"
        return 1
    fi
    case "$role" in
    moderator | broadcaster | operator)
        if [ "$is_mod_or_broadcaster" = "true" ]; then
            twitch_cmd_audit_log "$id" "$user_id" "allow" "role=$role"
            return 0
        fi
        ;;
    esac
    twitch_cmd_audit_log "$id" "$user_id" "deny" "role=$role insufficient-privilege"
    return 1
}

# --- レート制限 / idempotency 用の汎用 cooldown ファイルヘルパー ---

twitch_cmd_rate_limited() {
    local cooldown_file="$1" cooldown_sec="$2"
    local now_ts last_ts age
    now_ts=$(date +%s)
    last_ts=$(cat "$cooldown_file" 2>/dev/null || echo 0)
    case "$last_ts" in '' | *[!0-9]*) last_ts=0 ;; esac
    age=$((now_ts - last_ts))
    [ "$age" -lt "$cooldown_sec" ]
}

twitch_cmd_mark_rate_limit() {
    local cooldown_file="$1"
    mkdir -p "$(dirname "$cooldown_file")" 2>/dev/null || true
    date +%s >"$cooldown_file"
}

# --- registry 定義本体 ---
# id, required_role, side_effect, rate_limit_sec, confirmation, idempotency, audit
twitch_cmd_registry_init() {
    twitch_cmd_register "clip" "viewer" "true" "30" "false" \
        "cooldown-file:CLIP_COOLDOWN_FILE" "true"
    twitch_cmd_register "stream_start" "operator" "true" "60" "false" \
        "cooldown-file:STREAM_START_COOLDOWN_FILE + obs_control.sh側no-op" "true"
    twitch_cmd_register "audio_repair" "operator" "true" "3600" "false" \
        "cooldown-file:WAVE_LINK_REPAIR_COOLDOWN_FILE" "true"
    twitch_cmd_register "pitch" "moderator" "true" "0" "false" \
        "last-write-wins-per-speaker-id:config/voicevox_pitch_map.txt" "true"
    twitch_cmd_register "tempo" "moderator" "true" "0" "false" \
        "last-write-wins-per-speaker-id:config/voicevox_tempo_map.txt" "true"
    twitch_cmd_register "voice_style" "viewer" "true" "0" "false" \
        "last-write-wins:tmp/coeiroink_voice.txt|tmp/voicevox_voice.txt" "true"
    twitch_cmd_register "asmr" "viewer" "true" "0" "false" \
        "flag-file-overwrite:tmp/voicevox_asmr.txt" "true"
    twitch_cmd_register "ntrob" "viewer" "true" "0" "false" \
        "flag-file-overwrite:tmp/voicevox_oneshot_speaker.txt" "true"
    twitch_cmd_register "doushi" "viewer" "true" "0" "false" \
        "flag-file-overwrite:tmp/voicevox_dousi.txt" "true"
}

twitch_cmd_registry_init
