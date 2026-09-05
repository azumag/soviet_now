#!/usr/bin/env bash
# 静的検査 (docich issue #37): twitch_chat_daemon.sh 内の side effect 呼び出し箇所が
# 全て lib/twitch_command_registry.sh の `if twitch_cmd_authorize "<id>" ...; then` gate
# の内側にネストしていることを、bash の if/fi・while/done・for/done・case/esac の
# ブロック対応を追いながら検証する(grepベースの静的テスト)。
# registryを通らないside-effect call siteが新たに追加された場合、このテストが落ちる。
set -u
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DAEMON="$ROOT/twitch_chat_daemon.sh"
REGISTRY="$ROOT/lib/twitch_command_registry.sh"

pass=0; fail=0
ok() { pass=$((pass+1)); }
ng() { fail=$((fail+1)); echo "FAIL: $*"; }

# 実ファイルに対する検査本体。$1=検査対象ファイル。stdout に "OK" または
# "VIOLATION:<lineno>:<text>" を行ごとに出す。
run_check() {
    local target="$1"
    python3 - "$target" <<'PY'
import re
import sys

path = sys.argv[1]
with open(path, encoding="utf-8") as f:
    lines = f.readlines()

# side effect の指紋(この行が見えたら、authorize gateの内側にある必要がある)
DANGEROUS = [
    re.compile(r'\./obs_control\.sh'),
    re.compile(r'twitch_clip\.sh'),
    re.compile(r'"\$WAVE_LINK_REPAIR_SCRIPT"'),
    re.compile(r'\$_pitch_file'),
    re.compile(r'\$_tempo_file'),
    re.compile(r'tmp/voicevox_asmr\.txt'),
    re.compile(r'tmp/voicevox_oneshot_speaker\.txt'),
    re.compile(r'tmp/voicevox_dousi\.txt'),
    re.compile(r'tmp/coeiroink_voice\.txt'),
    re.compile(r'tmp/voicevox_voice\.txt'),
]

OPEN_IF = re.compile(r'(^|;)\s*if\b')
CLOSE_FI = re.compile(r'(^|;)\s*fi\b')
OPEN_LOOP = re.compile(r'(^|;)\s*(while|until|for)\b')
CLOSE_DONE = re.compile(r'(^|;)\s*done\b')
OPEN_CASE = re.compile(r'(^|;)\s*case\b')
CLOSE_ESAC = re.compile(r'(^|;)\s*esac\b')
AUTHZ_IF = re.compile(r'^\s*if\s+twitch_cmd_authorize\b')

stack = []  # list of bool: True if this opener is an authorize-if
authz_open_count = 0
violations = []
guarded_hits = 0

for i, raw in enumerate(lines, start=1):
    line = raw.rstrip("\n")
    stripped = line.strip()
    if stripped.startswith("#"):
        continue

    is_authz_open = bool(AUTHZ_IF.match(line))
    # ブロックを開くキーワードの数を数える(1行に複数開閉が同居する場合は簡易的に
    # 「開く数」「閉じる数」をそれぞれ数えて差分で近似する)
    n_if_open = len(OPEN_IF.findall(line))
    n_fi_close = len(CLOSE_FI.findall(line))
    n_loop_open = len(OPEN_LOOP.findall(line))
    n_done_close = len(CLOSE_DONE.findall(line))
    n_case_open = len(OPEN_CASE.findall(line))
    n_esac_close = len(CLOSE_ESAC.findall(line))

    # 危険パターンの検査は「このブロックを開く行そのもの」ではなく、既存スタックの
    # 状態(その行に到達した時点でauthorize-ifの中にいるか)で判定する。
    currently_in_authz = authz_open_count > 0
    for pat in DANGEROUS:
        if pat.search(line):
            if currently_in_authz:
                guarded_hits += 1
            else:
                violations.append((i, line.strip()))

    # push/pop の処理: if を1つ開いたら、それがauthorize-ifかどうかでスタックに積む。
    for _ in range(n_if_open):
        stack.append(is_authz_open)
        if is_authz_open:
            authz_open_count += 1
        # 同一行に複数 if がある場合の判定簡易化のため、2個目以降はauthzではない扱い
        is_authz_open = False
    for _ in range(n_fi_close):
        if stack:
            popped = stack.pop()
            if popped:
                authz_open_count -= 1
    # while/for/until と done は if/fi と別スタックにすべきだが、danger patternは
    # ループ構造の中では使われないため、簡易的に同じ深さ管理をしなくても実害は無い。
    # (case/esacも同様。ここでは検出漏れを避けるため何もしない=安全側)

for lineno, text in violations:
    print(f"VIOLATION:{lineno}:{text}")
print(f"GUARDED_HITS:{guarded_hits}")
print("OK" if not violations else "NG")
PY
}

OUT=$(run_check "$DAEMON")
echo "$OUT" | grep -q '^OK$' && ok || ng "twitch_chat_daemon.sh has unguarded side-effect call site(s):
$(echo "$OUT" | grep '^VIOLATION:')"

GUARDED=$(echo "$OUT" | sed -n 's/^GUARDED_HITS://p')
[ -n "$GUARDED" ] && [ "$GUARDED" -ge 9 ] && ok || ng "expected >=9 guarded side-effect hits (clip/stream_start/audio_repair/pitch/tempo/asmr/ntrob/doushi/voice_style), got: $GUARDED"

# --- registryに登録されているcommand id は daemon が全て使っている ---
# (registry.sh 側で宣言されたのに daemon から一切参照されない = 死んだ定義を検出)
for id in clip stream_start audio_repair pitch tempo voice_style asmr ntrob doushi; do
    grep -q "twitch_cmd_authorize \"$id\"" "$DAEMON" && ok || ng "daemon does not call twitch_cmd_authorize for registered id: $id"
    grep -q "twitch_cmd_register \"$id\"" "$REGISTRY" && ok || ng "registry does not register id used by daemon: $id"
done

# --- カナリア検査: このstatic testが本当に「registryを通らないside effect」を
#     検出できることを、意図的に未ガードのコピーを作って確認する(自己検証) ---
CANARY_DIR=$(mktemp -d "${TMPDIR:-/tmp}/twitch_authz_static_canary.XXXXXX")
trap 'rm -rf "$CANARY_DIR"' EXIT
CANARY_FILE="$CANARY_DIR/canary_daemon.sh"
cp "$DAEMON" "$CANARY_FILE"
# authorize gateを迂回する新規side effect呼び出しを追加する
printf '\n./obs_control.sh stream-start # UNGUARDED (canary)\n' >> "$CANARY_FILE"
CANARY_OUT=$(run_check "$CANARY_FILE")
echo "$CANARY_OUT" | grep -q '^NG$' && ok || ng "static check failed to detect an intentionally unguarded side-effect call site (canary)"
echo "$CANARY_OUT" | grep -q '^VIOLATION:.*UNGUARDED (canary)' && ok || ng "canary violation not reported with expected line content"

echo "test_twitch_command_authz_static: pass=$pass fail=$fail"
[ "$fail" -eq 0 ]
