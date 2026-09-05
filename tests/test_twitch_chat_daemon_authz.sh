#!/usr/bin/env bash
# twitch_chat_daemon.sh の side-effect認可 統合テスト (docich issue #37)。
#
# 実際の twitch_chat_daemon.sh を(nc の代わりにファイル入力で)フルで走らせ、
# obs_control.sh / twitch_clip.sh / repair_wave_link.sh をspy stubに差し替えて
# 呼び出し回数を検証する。実物のOBS/プロセス制御は一切起動しない。
set -u
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
WORK=$(mktemp -d "${TMPDIR:-/tmp}/twitch_daemon_authz_test.XXXXXX")
trap 'rm -rf "$WORK"' EXIT

pass=0; fail=0
ok() { pass=$((pass+1)); }
ng() { fail=$((fail+1)); echo "FAIL: $*"; }

cp "$ROOT/twitch_chat_daemon.sh" "$WORK/"
mkdir -p "$WORK/lib" "$WORK/tmp/debug" "$WORK/config"
cp "$ROOT/lib/twitch_command_registry.sh" "$WORK/lib/"
cp "$ROOT/lib/outbound_queue.sh" "$WORK/lib/" 2>/dev/null || true

SPY_LOG="$WORK/spy_calls.log"
: > "$SPY_LOG"

cat > "$WORK/obs_control.sh" <<EOF
#!/bin/bash
echo "\$(date +%s) obs_control.sh \$*" >> "$SPY_LOG"
echo "stream-start:started"
exit 0
EOF
cat > "$WORK/twitch_clip.sh" <<EOF
#!/bin/bash
echo "\$(date +%s) twitch_clip.sh \$*" >> "$SPY_LOG"
exit 0
EOF
cat > "$WORK/repair_wave_link.sh" <<EOF
#!/bin/bash
echo "\$(date +%s) repair_wave_link.sh \$*" >> "$SPY_LOG"
exit 0
EOF
chmod +x "$WORK/obs_control.sh" "$WORK/twitch_clip.sh" "$WORK/repair_wave_link.sh"

spy_count() { grep -c "$1" "$SPY_LOG" 2>/dev/null || true; }

# --- 合成IRC入力 (nc の代わりにこのファイルを read する。テスト専用の差し替え口) ---
IRC_INPUT="$WORK/irc_input.txt"
{
    # 1) viewer(badgeなし)の自然文 → stream_start は role=operator, 常にdeny(#38まで)
    printf '@display-name=Viewer1;id=msg-001;user-id=1001 :viewer1!viewer1@viewer1.tmi.twitch.tv PRIVMSG #azumagbanjo :配信を開始してください\r\n'
    # 2) spoofed broadcaster badge の自然文 → 未認証transportなのでbadgeがあってもdeny
    printf '@badges=broadcaster/1;display-name=NotRealBroadcaster;id=msg-002;user-id=2002 :spoofed!spoofed@spoofed.tmi.twitch.tv PRIVMSG #azumagbanjo :配信を開始して\r\n'
    # 3) broadcaster badge の !音声修復 → role=operator, 常にdeny
    printf '@badges=broadcaster/1;display-name=Broadcaster;id=msg-003;user-id=3003 :broadcaster!broadcaster@broadcaster.tmi.twitch.tv PRIVMSG #azumagbanjo :!音声修復\r\n'
    # 4) moderator badge の !pitch → role=moderator, 常にdeny
    printf '@badges=moderator/1;display-name=ModTest;id=msg-004;user-id=4004 :modtest!modtest@modtest.tmi.twitch.tv PRIVMSG #azumagbanjo :!pitch 3 1.25\r\n'
    # 5) viewer(badgeなし) !clip → role=viewer なので許可(TWITCH_CLIP_CMD_ENABLED=1時)
    printf '@display-name=ClipViewer;id=msg-005;user-id=5005 :clipviewer!clipviewer@clipviewer.tmi.twitch.tv PRIVMSG #azumagbanjo :!clip\r\n'
    # 6) 直後に同一視聴者が !clip を再送 → cooldown(idempotency)で多重実行されない
    printf '@display-name=ClipViewer;id=msg-006;user-id=5005 :clipviewer!clipviewer@clipviewer.tmi.twitch.tv PRIVMSG #azumagbanjo :!clip\r\n'
    # 7) 通常コメント → raw.log への取得は回帰させない
    printf '@display-name=Viewer2;id=msg-007;user-id=2007 :viewer2!viewer2@viewer2.tmi.twitch.tv PRIVMSG #azumagbanjo :こんにちは、今日も配信たのしみ\r\n'
    # 8) !ASMR (role=viewer, 特権昇格なし) → 許可された既存viewer機能は維持
    printf '@display-name=Viewer3;id=msg-008;user-id=2008 :viewer3!viewer3@viewer3.tmi.twitch.tv PRIVMSG #azumagbanjo :!ASMR こんにちは\r\n'
    # 9) !wakana (role=viewer) → 許可された既存viewer機能は維持
    printf '@display-name=Viewer4;id=msg-009;user-id=2009 :viewer4!viewer4@viewer4.tmi.twitch.tv PRIVMSG #azumagbanjo :!wakana\r\n'
} > "$IRC_INPUT"

cd "$WORK"
env \
    TWITCH_CHAT_DIR="$WORK/.twitch_chat" \
    TWITCH_CHAT_DAEMON_TEST_INPUT="$IRC_INPUT" \
    STREAM_START_ON_COMMENT_ENABLED=1 \
    TWITCH_CLIP_CMD_ENABLED=1 \
    CHAT_INGEST_OVERLAY_NOTIFY=0 \
    TWITCH_IGNORE_AUTHORS="dociai azumagdev" \
    bash "$WORK/twitch_chat_daemon.sh" azumagbanjo >"$WORK/daemon_stdout.log" 2>"$WORK/daemon_stderr.log"
daemon_rc=$?
[ "$daemon_rc" -eq 0 ] && ok || ng "daemon exited 0 (rc=$daemon_rc); stderr: $(cat "$WORK/daemon_stderr.log")"

# spy呼び出しがある場合、バックグラウンド(&)subshellの書き込み待ちを少し待つ
for _ in 1 2 3 4 5 6 7 8 9 10; do
    [ -s "$SPY_LOG" ] || [ -f "$WORK/.twitch_chat/raw.log" ] && break
    sleep 0.1
done
sleep 0.3

RAW_LOG="$WORK/.twitch_chat/raw.log"

# --- 受入条件1/2: viewer・roleなし・spoofed display-name・未認証transportからの
#     role付きeventで OBS/file/process side effect が 0回 ---
[ "$(spy_count obs_control.sh)" -eq 0 ] && ok || ng "obs_control.sh must not be spawned (natural-language stream start), count=$(spy_count obs_control.sh)"
[ "$(spy_count repair_wave_link.sh)" -eq 0 ] && ok || ng "repair_wave_link.sh must not be spawned (!音声修復), count=$(spy_count repair_wave_link.sh)"
[ ! -f "$WORK/config/voicevox_pitch_map.txt" ] && ok || ng "pitch config file must not be written (!pitch, unauthenticated mod badge)"

# --- 受入条件3: 許可identity(role=viewerの明示許可command)だけ成功する ---
[ "$(spy_count twitch_clip.sh)" -eq 1 ] && ok || ng "twitch_clip.sh must be spawned exactly once for the first !clip, count=$(spy_count twitch_clip.sh)"

# --- 受入条件4: 重複command(2回目の!clip)はrate limitで多重実行されない ---
# (上のcount==1のアサーションが2回連続!clipを1回に抑えたことも同時に証明する)

# --- 受入条件5: 通常コメント/許可済みviewer機能(!ASMR, !wakana)は維持される ---
[ -f "$RAW_LOG" ] && grep -q "こんにちは、今日も配信たのしみ" "$RAW_LOG" && ok || ng "normal comment must still be captured in raw.log"
[ -f "$WORK/tmp/voicevox_asmr.txt" ] && ok || ng "!ASMR viewer feature must still work (flag file written)"
[ -f "$WORK/tmp/coeiroink_voice.txt" ] && grep -q "8e99d620-87d3-11ed-870a-0242ac1c000c" "$WORK/tmp/coeiroink_voice.txt" && ok || ng "!wakana viewer feature must still work (voice file written)"

# --- audit ログにdeny決定が残っている(観測可能性の確認) ---
AUDIT_LOG="$WORK/.twitch_chat/command_audit.log"
[ -f "$AUDIT_LOG" ] && grep -q "cmd=stream_start.*decision=deny" "$AUDIT_LOG" && ok || ng "audit log must record stream_start deny"
[ -f "$AUDIT_LOG" ] && grep -q "cmd=audio_repair.*decision=deny" "$AUDIT_LOG" && ok || ng "audit log must record audio_repair deny"
[ -f "$AUDIT_LOG" ] && grep -q "cmd=pitch.*decision=deny" "$AUDIT_LOG" && ok || ng "audit log must record pitch deny"
[ -f "$AUDIT_LOG" ] && grep -q "cmd=clip.*decision=allow" "$AUDIT_LOG" && ok || ng "audit log must record clip allow"

echo "test_twitch_chat_daemon_authz: pass=$pass fail=$fail"
[ "$fail" -eq 0 ]
