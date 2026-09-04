#!/usr/bin/env bash
# tests/test_twitch_chat_daemon_tls_e2e.sh - docich issue #38 の end-to-end テスト。
#
# 実際の twitch_chat_daemon.sh を、ローカルのmock TLS IRCサーバ
# (tests/fixtures/twitch_tls_mock_server.py, 自己署名証明書)へ本物のTLSハンドシェイク
# (openssl s_client)で接続させて検証する。実Twitch API/IRCサーバへは一切接続しない。
#
# 検証する受入条件(docich issue #38):
#   1) 正しい証明書/hostname/channel identity → 通常コメント取得 + role付きcommand到達
#   2) 信頼していないCAの証明書 → 接続・処理を拒否(fail closed)
#   3) hostname不一致の証明書 → 接続・処理を拒否
#   4) channel identity(room-id)不一致 → 接続・処理を拒否(このセッションを全て破棄)
#   5) reconnect後もmsg-id重複は二重処理しない/rate limitはcooldownファイル経由で
#      再接続をまたいで有効
#   6) 複数コメントの受信順序がraw.logに保持される
set -u
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
MOCK_SERVER="$ROOT/tests/fixtures/twitch_tls_mock_server.py"
WORK=$(mktemp -d "${TMPDIR:-/tmp}/twitch_tls_e2e_test.XXXXXX")
trap 'kill $(jobs -p) 2>/dev/null; rm -rf "$WORK"' EXIT

pass=0; fail=0
ok() { pass=$((pass+1)); }
ng() { fail=$((fail+1)); echo "FAIL: $*"; }

CHANNEL="azumagbanjo"
BROADCASTER_ID="999000"

# --- 証明書生成ヘルパー ---
gen_cert() {
    local cn="$1" outdir="$2"
    mkdir -p "$outdir"
    openssl req -x509 -newkey rsa:2048 -nodes \
        -keyout "$outdir/key.pem" -out "$outdir/cert.pem" -days 2 \
        -subj "/CN=${cn}" -addext "subjectAltName=DNS:${cn}" >/dev/null 2>&1
}

wait_for_file() {
    local f="$1" tries="${2:-100}"
    local i=0
    while [ "$i" -lt "$tries" ]; do
        [ -s "$f" ] && return 0
        sleep 0.05
        i=$((i+1))
    done
    return 1
}

# mock TLS サーバを起動し、実際にbindしたポート番号をstdoutへ出す。
start_mock_server() {
    local cert="$1" key="$2" script="$3" readyfile="$4" accept_count="${5:-1}"
    python3 "$MOCK_SERVER" --port 0 --cert "$cert" --key "$key" --script "$script" \
        --ready-file "$readyfile" --accept-count "$accept_count" --send-delay-ms 5 --linger-ms 200 \
        >/dev/null 2>>"$WORK/mock_server_stderr.log" &
    MOCK_SERVER_PID=$!
    wait_for_file "$readyfile" 100 || { echo "mock server did not become ready" >&2; return 1; }
    cat "$readyfile"
}

# 各シナリオ用にdaemon一式(stub scripts含む)をコピーした作業ディレクトリを作る。
setup_daemon_copy() {
    local dest="$1"
    mkdir -p "$dest/lib" "$dest/tmp/debug" "$dest/config"
    cp "$ROOT/twitch_chat_daemon.sh" "$dest/"
    cp "$ROOT/lib/twitch_command_registry.sh" "$ROOT/lib/twitch_tls_transport.sh" "$dest/lib/"
    cp "$ROOT/lib/outbound_queue.sh" "$dest/lib/" 2>/dev/null || true
    cat > "$dest/obs_control.sh" <<EOF
#!/bin/bash
echo "\$(date +%s) obs_control.sh \$*" >> "\$SPY_LOG"
echo "stream-start:started"
exit 0
EOF
    cat > "$dest/twitch_clip.sh" <<EOF
#!/bin/bash
echo "\$(date +%s) twitch_clip.sh \$*" >> "\$SPY_LOG"
exit 0
EOF
    cat > "$dest/repair_wave_link.sh" <<EOF
#!/bin/bash
echo "\$(date +%s) repair_wave_link.sh \$*" >> "\$SPY_LOG"
exit 0
EOF
    chmod +x "$dest/obs_control.sh" "$dest/twitch_clip.sh" "$dest/repair_wave_link.sh"
}

spy_count() { grep -c "$1" "$SPY_LOG" 2>/dev/null || true; }

# daemonを1セッションだけ実行して終了させる(TWITCH_CHAT_DAEMON_TEST_SINGLE_SESSION=1)。
# side effectのbackground subshellが書き終わるのを少し待つ。
run_daemon_once() {
    local dest="$1"; shift
    ( cd "$dest" && timeout 15 env "$@" \
        TWITCH_CHAT_DAEMON_TEST_SINGLE_SESSION=1 \
        CHAT_INGEST_OVERLAY_NOTIFY=0 \
        bash "$dest/twitch_chat_daemon.sh" "$CHANNEL" \
        >"$dest/daemon_stdout.log" 2>"$dest/daemon_stderr.log" )
    local rc=$?
    sleep 0.5
    return $rc
}

# =====================================================================
# シナリオ1: 正しい証明書 + 正しいhostname + 正しいchannel identity
#   → 通常コメントを取得でき、role付きcommand(!音声修復, role=operator)にも到達する。
# =====================================================================
S1="$WORK/s1"
setup_daemon_copy "$S1"
CERT1="$WORK/cert_good"
gen_cert "irc.chat.twitch.tv" "$CERT1"
cat > "$S1/script.txt" <<'EOF'
:tmi.twitch.tv 001 justinfan :Welcome
@emote-only=0;room-id=999000;subs-only=0 :tmi.twitch.tv ROOMSTATE #azumagbanjo
@display-name=Viewer1;id=msg-101;user-id=1001 :viewer1!viewer1@viewer1.tmi.twitch.tv PRIVMSG #azumagbanjo :こんにちは、今日も配信たのしみ
@badges=broadcaster/1;display-name=Broadcaster;id=msg-102;user-id=555 :broadcaster!broadcaster@broadcaster.tmi.twitch.tv PRIVMSG #azumagbanjo :!音声修復
EOF
PORT1=$(start_mock_server "$CERT1/cert.pem" "$CERT1/key.pem" "$S1/script.txt" "$WORK/ready1")
: > "$WORK/spy1.log"
run_daemon_once "$S1" \
    TWITCH_CHAT_DIR="$S1/.twitch_chat" \
    TWITCH_IRC_TLS_HOST=127.0.0.1 \
    TWITCH_IRC_TLS_PORT="$PORT1" \
    TWITCH_IRC_TLS_VERIFY_HOST=irc.chat.twitch.tv \
    TWITCH_IRC_TLS_CAFILE="$CERT1/cert.pem" \
    TWITCH_BROADCASTER_ID="$BROADCASTER_ID" \
    SPY_LOG="$WORK/spy1.log"
daemon1_rc=$?
[ "$daemon1_rc" -eq 0 ] && ok || ng "scenario1: daemon must exit 0 (rc=$daemon1_rc); stderr: $(cat "$S1/daemon_stderr.log")"
RAW1="$S1/.twitch_chat/raw.log"
[ -f "$RAW1" ] && grep -q "こんにちは、今日も配信たのしみ" "$RAW1" && ok || ng "scenario1: normal comment must be captured via real TLS transport"
SPY_LOG="$WORK/spy1.log"
for _ in 1 2 3 4 5 6 7 8 9 10; do [ -s "$SPY_LOG" ] && break; sleep 0.2; done
[ "$(spy_count repair_wave_link.sh)" -eq 1 ] && ok || ng "scenario1: role-gated command (audio_repair) must fire once transport is authenticated, count=$(spy_count repair_wave_link.sh)"
grep -q "confirmed:channel=azumagbanjo room-id=999000" "$S1/.twitch_chat/transport_identity.log" 2>/dev/null && ok || ng "scenario1: transport_identity.log must record the confirmation"

# =====================================================================
# シナリオ2: 証明書は正しい形式だが、クライアントがそのCAを信頼していない
#   (mockサーバの自己署名証明書をCAfileで明示的に信頼させない)
#   → ハンドシェイクが失敗し、以後のデータは一切処理されない。
# =====================================================================
S2="$WORK/s2"
setup_daemon_copy "$S2"
cp "$S1/script.txt" "$S2/script.txt"
PORT2=$(start_mock_server "$CERT1/cert.pem" "$CERT1/key.pem" "$S2/script.txt" "$WORK/ready2")
: > "$WORK/spy2.log"
# 未知/無関係な自己署名CAを使う(=空ファイルだと openssl s_client がCAfile読み込み
# 自体に失敗し、TCP接続すら行われずmockサーバがacceptで待ち続けてしまうため、
# 「サーバの証明書を検証したが信頼していない」という本来のシナリオにならない。
# 実際にTLSハンドシェイクさせた上でチェーン検証を失敗させるため、構文的に正しいが
# サーバの証明書とは無関係な別のCA証明書を使う。
gen_cert "unrelated-test-ca.example" "$WORK/cert_unrelated"
run_daemon_once "$S2" \
    TWITCH_CHAT_DIR="$S2/.twitch_chat" \
    TWITCH_IRC_TLS_HOST=127.0.0.1 \
    TWITCH_IRC_TLS_PORT="$PORT2" \
    TWITCH_IRC_TLS_VERIFY_HOST=irc.chat.twitch.tv \
    TWITCH_IRC_TLS_CAFILE="$WORK/cert_unrelated/cert.pem" \
    TWITCH_BROADCASTER_ID="$BROADCASTER_ID" \
    SPY_LOG="$WORK/spy2.log"
daemon2_rc=$?
[ "$daemon2_rc" -eq 0 ] && ok || ng "scenario2: daemon must still exit 0 (reconnect-loop style failure, not a crash), rc=$daemon2_rc"
[ ! -s "$S2/.twitch_chat/raw.log" ] && ok || ng "scenario2: untrusted CA must not yield any captured comment"
SPY_LOG="$WORK/spy2.log"
[ ! -s "$SPY_LOG" ] && ok || ng "scenario2: untrusted CA must not trigger any side effect"
[ ! -s "$S2/.twitch_chat/transport_identity.log" ] && ok || ng "scenario2: untrusted CA must never reach channel identity confirmation"
grep -qi "certificate\|verify" "$S2/.twitch_chat/tls_handshake.log" 2>/dev/null && ok || ng "scenario2: tls_handshake.log must record a verification failure"

# =====================================================================
# シナリオ3: 証明書は信頼できるが、期待するhostnameと一致しない
#   → ハンドシェイクが失敗し、以後のデータは一切処理されない。
# =====================================================================
S3="$WORK/s3"
setup_daemon_copy "$S3"
cp "$S1/script.txt" "$S3/script.txt"
PORT3=$(start_mock_server "$CERT1/cert.pem" "$CERT1/key.pem" "$S3/script.txt" "$WORK/ready3")
: > "$WORK/spy3.log"
run_daemon_once "$S3" \
    TWITCH_CHAT_DIR="$S3/.twitch_chat" \
    TWITCH_IRC_TLS_HOST=127.0.0.1 \
    TWITCH_IRC_TLS_PORT="$PORT3" \
    TWITCH_IRC_TLS_VERIFY_HOST=evil.example.com \
    TWITCH_IRC_TLS_CAFILE="$CERT1/cert.pem" \
    TWITCH_BROADCASTER_ID="$BROADCASTER_ID" \
    SPY_LOG="$WORK/spy3.log"
daemon3_rc=$?
[ "$daemon3_rc" -eq 0 ] && ok || ng "scenario3: daemon must still exit 0, rc=$daemon3_rc"
[ ! -s "$S3/.twitch_chat/raw.log" ] && ok || ng "scenario3: hostname mismatch must not yield any captured comment"
SPY_LOG="$WORK/spy3.log"
[ ! -s "$SPY_LOG" ] && ok || ng "scenario3: hostname mismatch must not trigger any side effect"
grep -qi "hostname mismatch" "$S3/.twitch_chat/tls_handshake.log" 2>/dev/null && ok || ng "scenario3: tls_handshake.log must record a hostname mismatch"

# =====================================================================
# シナリオ4: TLSは正しく確立するが、ROOMSTATEのroom-idが期待値(TWITCH_BROADCASTER_ID)
#   と一致しない(誤ったchannel/乗っ取りの模擬) → セッション全体を破棄する。
#   ROOMSTATEの直後にrole付きcommandを送っても実行されないことまで確認する。
# =====================================================================
S4="$WORK/s4"
setup_daemon_copy "$S4"
cat > "$S4/script.txt" <<'EOF'
:tmi.twitch.tv 001 justinfan :Welcome
@emote-only=0;room-id=999000;subs-only=0 :tmi.twitch.tv ROOMSTATE #azumagbanjo
@badges=broadcaster/1;display-name=Broadcaster;id=msg-201;user-id=555 :broadcaster!broadcaster@broadcaster.tmi.twitch.tv PRIVMSG #azumagbanjo :!音声修復
@display-name=Viewer1;id=msg-202;user-id=1001 :viewer1!viewer1@viewer1.tmi.twitch.tv PRIVMSG #azumagbanjo :通常コメントも流れない
EOF
PORT4=$(start_mock_server "$CERT1/cert.pem" "$CERT1/key.pem" "$S4/script.txt" "$WORK/ready4")
: > "$WORK/spy4.log"
run_daemon_once "$S4" \
    TWITCH_CHAT_DIR="$S4/.twitch_chat" \
    TWITCH_IRC_TLS_HOST=127.0.0.1 \
    TWITCH_IRC_TLS_PORT="$PORT4" \
    TWITCH_IRC_TLS_VERIFY_HOST=irc.chat.twitch.tv \
    TWITCH_IRC_TLS_CAFILE="$CERT1/cert.pem" \
    TWITCH_BROADCASTER_ID="333000" \
    SPY_LOG="$WORK/spy4.log"
daemon4_rc=$?
[ "$daemon4_rc" -eq 0 ] && ok || ng "scenario4: daemon must still exit 0, rc=$daemon4_rc"
grep -q "reject:room-id-mismatch" "$S4/.twitch_chat/transport_identity.log" 2>/dev/null && ok || ng "scenario4: transport_identity.log must record the room-id mismatch rejection"
grep -q "transport identity rejected" "$S4/.twitch_chat/daemon_reconnect.log" 2>/dev/null && ok || ng "scenario4: daemon_reconnect.log must record the disconnect reason"
SPY_LOG="$WORK/spy4.log"
[ ! -s "$SPY_LOG" ] && ok || ng "scenario4: audio_repair must not fire when channel identity is rejected"
[ ! -s "$S4/.twitch_chat/raw.log" ] && ok || ng "scenario4: no line from a channel-identity-rejected session should be trusted (session dropped at ROOMSTATE)"

# =====================================================================
# シナリオ5: reconnect(2回の独立したdaemon実行)をまたいだ重複防止とrate limit。
#   同一 $CHAT_DIR を2回のセッションで共有し、同じ msg-id の !clip を再送する。
# =====================================================================
S5="$WORK/s5"
setup_daemon_copy "$S5"
cat > "$S5/script_a.txt" <<'EOF'
:tmi.twitch.tv 001 justinfan :Welcome
@emote-only=0;room-id=999000;subs-only=0 :tmi.twitch.tv ROOMSTATE #azumagbanjo
@display-name=ClipViewer;id=dup-msg-1;user-id=5005 :clipviewer!clipviewer@clipviewer.tmi.twitch.tv PRIVMSG #azumagbanjo :!clip
EOF
cp "$S5/script_a.txt" "$S5/script_b.txt"
PORT5A=$(start_mock_server "$CERT1/cert.pem" "$CERT1/key.pem" "$S5/script_a.txt" "$WORK/ready5a")
: > "$WORK/spy5.log"
run_daemon_once "$S5" \
    TWITCH_CHAT_DIR="$S5/.twitch_chat" \
    TWITCH_IRC_TLS_HOST=127.0.0.1 \
    TWITCH_IRC_TLS_PORT="$PORT5A" \
    TWITCH_IRC_TLS_VERIFY_HOST=irc.chat.twitch.tv \
    TWITCH_IRC_TLS_CAFILE="$CERT1/cert.pem" \
    TWITCH_BROADCASTER_ID="$BROADCASTER_ID" \
    TWITCH_CLIP_CMD_ENABLED=1 \
    SPY_LOG="$WORK/spy5.log"
daemon5a_rc=$?
# 「再接続」を模擬: 同じCHAT_DIR(=同じ dedup ファイル/cooldownファイル)に対して
# 2回目のセッションを、同一msg-idで即座に実行する。
PORT5B=$(start_mock_server "$CERT1/cert.pem" "$CERT1/key.pem" "$S5/script_b.txt" "$WORK/ready5b")
run_daemon_once "$S5" \
    TWITCH_CHAT_DIR="$S5/.twitch_chat" \
    TWITCH_IRC_TLS_HOST=127.0.0.1 \
    TWITCH_IRC_TLS_PORT="$PORT5B" \
    TWITCH_IRC_TLS_VERIFY_HOST=irc.chat.twitch.tv \
    TWITCH_IRC_TLS_CAFILE="$CERT1/cert.pem" \
    TWITCH_BROADCASTER_ID="$BROADCASTER_ID" \
    TWITCH_CLIP_CMD_ENABLED=1 \
    SPY_LOG="$WORK/spy5.log"
daemon5b_rc=$?
[ "$daemon5a_rc" -eq 0 ] && [ "$daemon5b_rc" -eq 0 ] && ok || ng "scenario5: both reconnect sessions must exit 0 (rc=$daemon5a_rc,$daemon5b_rc)"
SPY_LOG="$WORK/spy5.log"
for _ in 1 2 3 4 5 6 7 8 9 10; do [ -s "$SPY_LOG" ] && break; sleep 0.2; done
[ "$(spy_count twitch_clip.sh)" -eq 1 ] && ok || ng "scenario5: !clip cooldown must persist across reconnect (2 sessions, same msg), count=$(spy_count twitch_clip.sh)"
dup_count=$(grep -c "dup-msg-1" "$S5/.twitch_chat/raw.log" 2>/dev/null || true)
[ "${dup_count:-0}" -eq 1 ] && ok || ng "scenario5: msg-id dedup must persist across reconnect, raw.log occurrences=$dup_count"

# =====================================================================
# シナリオ6: 受信順序の保持(複数コメントがraw.logに送信順で記録される)
# =====================================================================
S6="$WORK/s6"
setup_daemon_copy "$S6"
cat > "$S6/script.txt" <<'EOF'
:tmi.twitch.tv 001 justinfan :Welcome
@emote-only=0;room-id=999000;subs-only=0 :tmi.twitch.tv ROOMSTATE #azumagbanjo
@display-name=Viewer1;id=seq-1;user-id=1001 :viewer1!viewer1@viewer1.tmi.twitch.tv PRIVMSG #azumagbanjo :SEQMARK-1
@display-name=Viewer2;id=seq-2;user-id=1002 :viewer2!viewer2@viewer2.tmi.twitch.tv PRIVMSG #azumagbanjo :SEQMARK-2
@display-name=Viewer3;id=seq-3;user-id=1003 :viewer3!viewer3@viewer3.tmi.twitch.tv PRIVMSG #azumagbanjo :SEQMARK-3
EOF
PORT6=$(start_mock_server "$CERT1/cert.pem" "$CERT1/key.pem" "$S6/script.txt" "$WORK/ready6")
: > "$WORK/spy6.log"
run_daemon_once "$S6" \
    TWITCH_CHAT_DIR="$S6/.twitch_chat" \
    TWITCH_IRC_TLS_HOST=127.0.0.1 \
    TWITCH_IRC_TLS_PORT="$PORT6" \
    TWITCH_IRC_TLS_VERIFY_HOST=irc.chat.twitch.tv \
    TWITCH_IRC_TLS_CAFILE="$CERT1/cert.pem" \
    TWITCH_BROADCASTER_ID="$BROADCASTER_ID" \
    SPY_LOG="$WORK/spy6.log"
daemon6_rc=$?
[ "$daemon6_rc" -eq 0 ] && ok || ng "scenario6: daemon must exit 0, rc=$daemon6_rc"
order=$(grep -o 'SEQMARK-[0-9]' "$S6/.twitch_chat/raw.log" 2>/dev/null | tr '\n' ',' )
[ "$order" = "SEQMARK-1,SEQMARK-2,SEQMARK-3," ] && ok || ng "scenario6: raw.log must preserve receive order, got: $order"

# =====================================================================
# シナリオ7: TWITCH_BROADCASTER_ID が未設定(運用者が未設定の既定状態)。
#   TLS自体は正しく確立してROOMSTATEも受け取るが、channel identityを検証できない
#   ("no-expected-broadcaster-id-configured")。この場合はセッションを切断せず、
#   通常コメント取得は従来通り継続しつつ、elevated commandだけ引き続きdenyのまま
#   にする(#38導入前の既定動作からの回帰を防ぐ回帰テスト)。
# =====================================================================
S7="$WORK/s7"
setup_daemon_copy "$S7"
cp "$S1/script.txt" "$S7/script.txt"
PORT7=$(start_mock_server "$CERT1/cert.pem" "$CERT1/key.pem" "$S7/script.txt" "$WORK/ready7")
: > "$WORK/spy7.log"
run_daemon_once "$S7" \
    TWITCH_CHAT_DIR="$S7/.twitch_chat" \
    TWITCH_IRC_TLS_HOST=127.0.0.1 \
    TWITCH_IRC_TLS_PORT="$PORT7" \
    TWITCH_IRC_TLS_VERIFY_HOST=irc.chat.twitch.tv \
    TWITCH_IRC_TLS_CAFILE="$CERT1/cert.pem" \
    SPY_LOG="$WORK/spy7.log"
daemon7_rc=$?
[ "$daemon7_rc" -eq 0 ] && ok || ng "scenario7: daemon must exit 0, rc=$daemon7_rc"
RAW7="$S7/.twitch_chat/raw.log"
[ -f "$RAW7" ] && grep -q "こんにちは、今日も配信たのしみ" "$RAW7" && ok || ng "scenario7: normal comment capture must NOT regress when TWITCH_BROADCASTER_ID is unset (pre-#38 default state)"
SPY_LOG="$WORK/spy7.log"
sleep 0.5
[ ! -s "$SPY_LOG" ] && ok || ng "scenario7: elevated command must still be denied without TWITCH_BROADCASTER_ID configured"
grep -q "reject:no-expected-broadcaster-id-configured" "$S7/.twitch_chat/transport_identity.log" 2>/dev/null && ok || ng "scenario7: transport_identity.log must record the soft-reject reason"

echo "test_twitch_chat_daemon_tls_e2e: pass=$pass fail=$fail"
[ "$fail" -eq 0 ]
