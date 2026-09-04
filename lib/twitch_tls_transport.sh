# lib/twitch_tls_transport.sh - Twitch IRC 用 TLS transport 補助 (docich issue #38)
#
# 背景 (docich issue #37 / #38):
#   twitch_chat_daemon.sh は irc.chat.twitch.tv:6667 への平文(非TLS)IRC接続を使って
#   いた。平文接続はネットワーク経路上の中間者が IRCv3 tags (badges= 等) を書き換えても
#   受信側からは検知できず、tagsをそのまま認可根拠にできない一因になっていた
#   (#37 の deny-by-default gate はこれを踏まえた暫定策)。
#
#   本ファイルはその接続を irc.chat.twitch.tv:6697 への TLS 接続へ切り替えるための
#   補助関数を提供する。実際の接続確立(coproc)は呼び出し側(twitch_chat_daemon.sh)が
#   行い、ここでは以下のみを担当する:
#     - openssl s_client の引数構築(証明書検証 + hostname検証を必須にする)
#     - IRC ROOMSTATE 行からの room-id / channel 名抽出
#     - 期待する channel identity (TWITCH_BROADCASTER_ID + channel名) との一致判定
#     - 認証済みtransport経由で扱う identity (channel-id/user-id/badges) の schema検証
#
# 設計判断:
#   - 本番接続では -CAfile を明示指定しない(OSのデフォルト信頼ストアを使う)。
#     TWITCH_IRC_TLS_CAFILE はテスト専用のCA差し替え口であり、本番運用では絶対に
#     設定しないこと(信頼ストアを弱める操作になるため)。
#   - -verify_return_error を指定するため、証明書チェーン検証・hostname検証の
#     いずれかが失敗した場合、openssl はアプリケーションデータを一切流さずに
#     ハンドシェイクを打ち切る。この時 coproc の読み取り側は即座に EOF になり、
#     呼び出し側の既存の再接続ロジックに自然に合流する(=データを一切信頼しない
#     fail closed。追加のエラー判定コードを要しない)。
#   - channel identity (room-id) の確認は IRC application層の追加防御であり、
#     TWITCH_BROADCASTER_ID が設定されている場合のみ成立しうる。未設定なら
#     twitch_tls_identity_confirmed は常に reject を返す(=フェイルクローズ)。

TWITCH_IRC_TLS_HOST="${TWITCH_IRC_TLS_HOST:-irc.chat.twitch.tv}"
TWITCH_IRC_TLS_PORT="${TWITCH_IRC_TLS_PORT:-6697}"
# 検証対象hostname(かつSNI送信値)。TWITCH_IRC_TLS_HOSTがテストでIP(127.0.0.1等)に
# 差し替えられても、証明書検証は常に本来のTwitchホスト名に対して行う。
TWITCH_IRC_TLS_VERIFY_HOST="${TWITCH_IRC_TLS_VERIFY_HOST:-irc.chat.twitch.tv}"
# テスト専用: ローカルmock TLSサーバの自己署名証明書を信頼するためのCA差し替え口。
# 本番では絶対に設定しないこと(未設定 = OSデフォルトの信頼ストアを使う)。
TWITCH_IRC_TLS_CAFILE="${TWITCH_IRC_TLS_CAFILE:-}"

declare -ga TWITCH_TLS_ARGS=()

# openssl s_client の引数を組み立てて TWITCH_TLS_ARGS に格納する。
# 証明書チェーン検証(-verify_return_error)とhostname検証(-verify_hostname)を
# 必須にし、-quiet でセッション/証明書情報を標準出力(IRC行の読み取り元)から除外する
# (診断情報は呼び出し側が指定するstderrリダイレクト先にのみ出る)。
twitch_tls_build_args() {
    TWITCH_TLS_ARGS=(
        -connect "${TWITCH_IRC_TLS_HOST}:${TWITCH_IRC_TLS_PORT}"
        -servername "${TWITCH_IRC_TLS_VERIFY_HOST}"
        -verify_hostname "${TWITCH_IRC_TLS_VERIFY_HOST}"
        -verify_return_error
        -quiet
    )
    if [ -n "$TWITCH_IRC_TLS_CAFILE" ]; then
        TWITCH_TLS_ARGS+=(-CAfile "$TWITCH_IRC_TLS_CAFILE")
    fi
}

# --- IRCv3 tags / ROOMSTATE 行からの抽出 ---

# $1=tags(先頭の@は除去済み、';'区切り) $2=タグ名 → 値をstdoutへ(無ければ非0で返す)
twitch_tls_tag_value() {
    local tags="${1:-}" name="${2:-}"
    [ -n "$tags" ] || return 1
    [ -n "$name" ] || return 1
    local val
    val=$(printf '%s\n' "$tags" | tr ';' '\n' | sed -n "s/^${name}=//p" | head -n1)
    [ -n "$val" ] || return 1
    printf '%s' "$val"
}

twitch_tls_room_id_from_tags() {
    twitch_tls_tag_value "$1" "room-id"
}

# $1 = tags除去後のIRC行 (例: ":tmi.twitch.tv ROOMSTATE #channel")
twitch_tls_channel_from_payload() {
    # sedのbracket式内での\rの扱いはBSD/GNUで差があり移植性が無いため、まず
    # 空白までを緩く切り出してから tr -d '\r' で明示的にCRを除去する
    # (IRC回線は行末に\r\nを付けるため、readで読んだ行にCRが残る)。
    printf '%s\n' "$1" | sed -n 's/^.*ROOMSTATE #\([^ ]*\).*/\1/p' | tr -d '\r'
}

# 期待するchannel名/room-idと、実際にROOMSTATEから得たchannel名/room-idを比較する。
# 戻り値: 0=一致(channel identity確認成立), 1=不一致または判定不能(fail closed)。
# 理由をstdoutに1行出す(呼び出し側がログへそのまま記録する想定)。
#   $1=expected_channel $2=expected_room_id(TWITCH_BROADCASTER_ID) $3=actual_channel $4=actual_room_id
twitch_tls_identity_confirmed() {
    local expected_channel="${1:-}" expected_room_id="${2:-}" actual_channel="${3:-}" actual_room_id="${4:-}"
    if [ -z "$expected_room_id" ]; then
        printf 'reject:no-expected-broadcaster-id-configured\n'
        return 1
    fi
    if ! twitch_identity_valid_channel_id "$expected_room_id"; then
        printf 'reject:expected-broadcaster-id-not-numeric\n'
        return 1
    fi
    if [ -z "$actual_room_id" ] || [ -z "$actual_channel" ]; then
        printf 'reject:missing-actual-identity\n'
        return 1
    fi
    if [ "$actual_room_id" != "$expected_room_id" ]; then
        printf 'reject:room-id-mismatch actual=%s expected=%s\n' "$actual_room_id" "$expected_room_id"
        return 1
    fi
    # channel名はIRCの慣習に合わせ大文字小文字を無視して比較する
    local ec al
    ec=$(printf '%s' "$expected_channel" | tr '[:upper:]' '[:lower:]')
    al=$(printf '%s' "$actual_channel" | tr '[:upper:]' '[:lower:]')
    if [ "$ec" != "$al" ]; then
        printf 'reject:channel-name-mismatch actual=%s expected=%s\n' "$actual_channel" "$expected_channel"
        return 1
    fi
    printf 'confirmed:channel=%s room-id=%s\n' "$actual_channel" "$actual_room_id"
    return 0
}

# --- 認証済みtransport経由で扱うidentityの最低限のschema検証 ---
# (形式が壊れている値を、誤ってauthorizeへ渡さないための入口バリデーション)

twitch_identity_valid_channel_id() {
    case "${1:-}" in
        ''|*[!0-9]*) return 1 ;;
        *) return 0 ;;
    esac
}

twitch_identity_valid_user_id() {
    case "${1:-}" in
        ''|*[!0-9]*) return 1 ;;
        *) return 0 ;;
    esac
}

# badges=name/version,name/version,... 形式かどうか(空文字列=badgeなし視聴者も許容)
twitch_identity_valid_badges() {
    local b="${1:-}"
    [ -z "$b" ] && return 0
    printf '%s' "$b" | grep -Eq '^[a-zA-Z0-9_-]+/[a-zA-Z0-9_-]+(,[a-zA-Z0-9_-]+/[a-zA-Z0-9_-]+)*$'
}
