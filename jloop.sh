#!/bin/bash
# jloop.sh - JSON-based state loop for Soren game AI
#
# game_state.json → DECIDE(頭) → EXECUTE(手) のシンプル2段構成
# 画像認識不要。JSブリッジからの構造化データで判断する。

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

STATE_FILE="STATE.md"
COMMANDS="commands.txt"
GAME_STATE="game_state.json"
AI_TIMEOUT=300

# 使用モデル（primary / fallback）
MODEL_PRIMARY="glm"
MODEL_FALLBACK="opencode:glmflash"

mkdir -p tmp
[ -f "$STATE_FILE" ] || echo "WAIT_READY" > "$STATE_FILE"

#--- ヘルパー ---
set_state() { echo "$1" > "$STATE_FILE"; log "→ $1"; }
commands_empty() { [ -z "$(tr -d '[:space:]' < "$COMMANDS" 2>/dev/null)" ]; }
log() { echo "[$(date '+%H:%M:%S')] $*"; }

# game_state.jsonからゲームオーバー判定
is_game_over() {
    local s
    s=$(python3 -c "import json,sys; d=json.load(open('$GAME_STATE')); print(d.get('state',''))" 2>/dev/null)
    [ "$s" = "GAMEOVER" ] || [ "$s" = "STOP" ]
}

# game_state.jsonからMOVE状態か判定
is_move_state() {
    local s
    s=$(python3 -c "import json,sys; d=json.load(open('$GAME_STATE')); print(d.get('state',''))" 2>/dev/null)
    [ "$s" = "MOVE" ]
}

# 盤面が静止しているか判定（全ピースの速度が小さい）
is_board_settled() {
    python3 -c "
import json, sys
try:
    d = json.load(open('$GAME_STATE'))
    pieces = d.get('pieces', [])
    if not pieces:
        print('true'); sys.exit()
    max_v = max(abs(p.get('vx',0))**2 + abs(p.get('vy',0))**2 for p in pieces)
    print('true' if max_v < 0.1 else 'false')
except:
    print('false')
" 2>/dev/null
}

#--- 履歴保存 ---
save_to_history() {
    local file="$1"
    [ -f "$file" ] || return 0
    mkdir -p tmp/history
    local ts=$(date '+%Y%m%d_%H%M%S')
    local base=$(basename "$file")
    mv "$file" "tmp/history/${ts}_${base}"
}
save_all_to_history() {
    for f in tmp/plan.md tmp/think.md tmp/state_snapshot.json tmp/board_analysis.md; do
        save_to_history "$f"
    done
}
clear_history() { rm -rf tmp/history; }

#--- スピナー ---
_spinner_pid=0
start_spinner() {
    local label="$1"
    (
        local frames=('⣾' '⣽' '⣻' '⢿' '⡿' '⣟' '⣯' '⣷')
        local i=0 start=$SECONDS
        while true; do
            local e=$(( SECONDS - start ))
            local m=$((e/60)) s=$((e%60))
            printf '\r  \033[1;35m%s\033[0m \033[1m%s\033[0m \033[2m%d:%02d\033[0m  ' \
                "${frames[i % ${#frames[@]}]}" "$label" "$m" "$s" >&2
            sleep 0.12
            ((i++))
        done
    ) &
    _spinner_pid=$!
}
stop_spinner() {
    if [ "${_spinner_pid:-0}" -ne 0 ]; then
        kill "$_spinner_pid" 2>/dev/null
        wait "$_spinner_pid" 2>/dev/null
        printf '\r\033[K' >&2
        _spinner_pid=0
    fi
}

#--- プロンプト構築 ---
build_prompt() {
    local pf="$1"; shift
    local p; p=$(cat "$pf" 2>/dev/null) || return 1
    local c=""
    for f in "$@"; do
        [ -f "$f" ] && c+=$'\n--- '"$f"$' ---\n'"$(cat "$f")"$'\n---\n'
    done
    [ -n "$c" ] && p="参照データ:${c}
${p}"
    echo "$p"
}

#--- コマンド実行 ---
run_cmd() {
    local spec="$1" prompt="$2"
    local type="${spec%%:*}" agent="${spec#*:}"
    [ "$type" = "$agent" ] && agent=""

    case "$type" in
    glm)
        opencode run "'$prompt'" --agent="zai" & ;;
    gemini)
        gemini -p "$prompt" -y -s & ;;
    gemini-flash)
        gemini -p "$prompt" -y -s --model=gemini-2.5-flash & ;;
    sonnet)
        claude -p "$prompt" --model=sonnet --permission-mode=acceptEdits & ;;
    opus)
        claude -p "$prompt" --model=opus --permission-mode=acceptEdits & ;;
    claude)
        claude -p "$prompt" --model=Haiku --permission-mode=acceptEdits & ;;
    opencode)
        opencode run "'$prompt'" --agent="${agent:-glmflash}" & ;;
    esac
    local cmd_pid=$!

    start_spinner "$type thinking..."

    ( sleep "$AI_TIMEOUT" && kill "$cmd_pid" 2>/dev/null && log "AI TIMEOUT (${AI_TIMEOUT}s)" ) &
    local timer_pid=$!

    trap "stop_spinner; kill $cmd_pid $timer_pid 2>/dev/null; wait $cmd_pid $timer_pid 2>/dev/null; log 'Interrupted'; trap - INT; return 130" INT

    wait "$cmd_pid" 2>/dev/null
    local ret=$?

    stop_spinner
    kill "$timer_pid" 2>/dev/null
    wait "$timer_pid" 2>/dev/null
    trap - INT

    return $ret
}

#--- AIステップ ---
run_ai() {
    local label="$1" primary="$2" fallback="$3" pf="$4" expect="$5"; shift 5
    local prompt; prompt=$(build_prompt "$pf" "$@")
    if [ -z "$prompt" ]; then
        log "[$label] prompt missing"; return 1
    fi

    [ -n "$expect" ] && rm -f "$expect"

    log "[$label] primary=$primary"
    run_cmd "$primary" "$prompt"
    if [ -n "$expect" ]; then
        [ -s "$expect" ] && { log "[$label] primary OK ($expect written)"; return 0; }
    else
        [ $? -eq 0 ] && return 0
    fi

    log "[$label] primary failed → fallback=$fallback"
    run_cmd "$fallback" "$prompt"
    if [ -n "$expect" ] && [ ! -s "$expect" ]; then
        log "[$label] fallback also failed ($expect not written)"
        return 1
    fi
}

#--- 待機ヘルパー ---
wait_commands_done() {
    for _ in $(seq 1 20); do commands_empty && return 0; sleep 1; done
    log "TIMEOUT: commands未消化 → クリア"
    echo "" > "$COMMANDS"
}

# DROP:X → canvas座標に変換してcommands.txtへ書き込み
write_drop_command() {
    local game_x="$1"
    # game X (-3.2〜+3.2) → canvas X (410〜830)
    local canvas_x
    canvas_x=$(python3 -c "
x = float('$game_x')
x = max(-3.2, min(3.2, x))
cx = int((x + 3.0) / 6.0 * (830 - 410) + 410)
print(cx)
" 2>/dev/null)
    [ -z "$canvas_x" ] && canvas_x=620
    log "DROP game_x=$game_x → canvas=${canvas_x},350"
    echo "${canvas_x},350" > "$COMMANDS"
}

#=== メインループ ===
log "=== Soren JSON Loop (jloop) ==="
SETTLE_WAIT=0

while true; do
    state=$(head -n 1 "$STATE_FILE" 2>/dev/null | tr -d '\n\r')
    case "$state" in

    WAIT_READY)
        if ! commands_empty; then
            SETTLE_WAIT=0
            sleep 1
            continue
        fi

        # game_state.jsonが存在しない場合は待つ
        if [ ! -f "$GAME_STATE" ]; then
            sleep 2; continue
        fi

        # ゲームオーバーチェック
        if is_game_over; then
            log "GAME OVER detected"
            set_state "GAME_OVER"
            continue
        fi

        # MOVE状態でなければ待つ（DROP中など）
        if ! is_move_state; then
            SETTLE_WAIT=0
            sleep 1; continue
        fi

        # 盤面静止を待つ（ピースが落ち着くまで）
        settled=$(is_board_settled)
        if [ "$settled" != "true" ]; then
            SETTLE_WAIT=0
            sleep 1; continue
        fi

        # 2回連続で静止確認してからDECIDEへ
        SETTLE_WAIT=$((SETTLE_WAIT + 1))
        if [ $SETTLE_WAIT -ge 2 ]; then
            SETTLE_WAIT=0
            set_state "DECIDE"
        fi
        sleep 1 ;;

    #--- 頭（JSON分析+ドロップ決定） ---
    DECIDE)
        save_all_to_history

        # 現在の状態スナップショットを保存
        cp "$GAME_STATE" tmp/state_snapshot.json 2>/dev/null

        # 盤面空間解析（マージ可否・着地予測・推奨ドロップ）
        python3 analyze_board.py "$GAME_STATE" tmp/board_analysis.md 2>/dev/null

        run_ai DECIDE "$MODEL_PRIMARY" "$MODEL_FALLBACK" \
            prompts/jdecide.md tmp/plan.md tmp/board_analysis.md STRATEGY.md think.md

        set_state "EXECUTE" ;;

    #--- 手（plan.mdからDROP座標を抽出して実行） ---
    EXECUTE)
        [ -f think.md ] && cat think.md
        [ -f tmp/plan.md ] && cat tmp/plan.md

        # DROP:X形式を抽出
        drop_x=$(grep -oE 'DROP:[0-9.eE+-]+' tmp/plan.md 2>/dev/null | head -1 | cut -d: -f2)

        if [ -z "$drop_x" ]; then
            # フォールバック: 数値を探す
            drop_x=$(grep -oE '[-]?[0-9]+\.[0-9]+' tmp/plan.md 2>/dev/null | head -1)
        fi

        [ -z "$drop_x" ] && drop_x="0.0"

        write_drop_command "$drop_x"
        wait_commands_done
        sleep 2
        set_state "WAIT_READY" ;;

    #--- ゲームオーバー振り返り ---
    GAME_OVER)
        run_ai GAME_OVER sonnet "$MODEL_PRIMARY" \
            prompts/postmortem.md "" "$GAME_STATE" STRATEGY.md think.md
        [ -f tmp/postmortem.md ] && cat tmp/postmortem.md
        sleep 2

        if is_game_over; then
            log "confirmed game over → retry"
            echo "retry" > "$COMMANDS"
            wait_commands_done
            sleep 3
        else
            log "false positive → skip retry"
        fi

        clear_history
        rm -f tmp/plan.md tmp/think.md tmp/state_snapshot.json
        set_state "WAIT_READY" ;;

    *)  log "Unknown: $state"
        set_state "WAIT_READY" ;;
    esac
    sleep 0.5
done
