#!/bin/bash
# sloop.sh - Simple state loop for Soren game AI
#
# OBSERVE(目) → DECIDE(頭) → EXECUTE(手) のシンプル3段構成
# 状態遷移はすべて sloop.sh が管理（AIはSTATE.mdを触らない）

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

STATE_FILE="STATE.md"
COMMANDS="commands.txt"
AI_TIMEOUT=300

# OBSERVE以外で使うモデル（primary / fallback）
MODEL_PRIMARY="glm"
MODEL_FALLBACK="opencode:glmflash"

mkdir -p tmp
[ -f "$STATE_FILE" ] || echo "WAIT_READY" > "$STATE_FILE"

#--- ヘルパー ---
set_state() { echo "$1" > "$STATE_FILE"; log "→ $1"; }
commands_empty() { [ -z "$(tr -d '[:space:]' < "$COMMANDS" 2>/dev/null)" ]; }
is_game_over() { grep -q 'GAME_OVER: true' tmp/observe.md 2>/dev/null; }
log() { echo "[$(date '+%H:%M:%S')] $*"; }

#--- 履歴保存（rm の代わりに mv でタイムスタンプ付き保存） ---
save_to_history() {
    local file="$1"
    [ -f "$file" ] || return 0
    mkdir -p tmp/history
    local ts=$(date '+%Y%m%d_%H%M%S')
    local base=$(basename "$file")
    mv "$file" "tmp/history/${ts}_${base}"
}
save_all_to_history() {
    for f in tmp/observe.md tmp/plan.md tmp/think.md tmp/analyze.md; do
        save_to_history "$f"
    done
}
clear_history() { rm -rf tmp/history; }

#--- スピナー表示（AI応答待ち中） ---
_spinner_pid=0

# ジョークコマンドをランダムに表示（低確率）
_maybe_show_joke() {
    # 約10%の確率で発動
    [ $((RANDOM % 20)) -ne 0 ] && return
    printf '\r\033[K' >&2

    # 利用可能なジョークを収集
    local jokes=()
    command -v sl       &>/dev/null && jokes+=("sl")
    command -v fortune  &>/dev/null && command -v cowsay &>/dev/null && jokes+=("fortune_cowsay")
    command -v toilet   &>/dev/null && jokes+=("toilet")
    command -v figlet   &>/dev/null && jokes+=("figlet")
    command -v nyancat  &>/dev/null && jokes+=("nyancat")
    command -v aafire   &>/dev/null && jokes+=("aafire")
    command -v boxes    &>/dev/null && command -v fortune &>/dev/null && jokes+=("boxes")
    command -v genact   &>/dev/null && jokes+=("genact")
    command -v cmatrix  &>/dev/null && jokes+=("cmatrix")
    command -v lolcat   &>/dev/null && command -v fortune &>/dev/null && jokes+=("lolcat")
    command -v tty-clock &>/dev/null && jokes+=("tty-clock")
    [ ${#jokes[@]} -eq 0 ] && return

    local pick="${jokes[$((RANDOM % ${#jokes[@]}))]}"

    # フルスクリーン系は代替バッファを使って画面を汚さない
    local fullscreen=0
    case "$pick" in nyancat|aafire|cmatrix|tty-clock) fullscreen=1 ;; esac
    [ "$fullscreen" -eq 1 ] && tput smcup >&2 2>/dev/null

    case "$pick" in
        sl)
            timeout 4 sl -l >&2 2>/dev/null || true ;;
        fortune_cowsay)
            fortune 2>/dev/null | cowsay >&2 2>/dev/null || true
            sleep 2 ;;
        toilet)
            echo "THINKING..." | toilet --gay 2>/dev/null >&2 || true
            sleep 1 ;;
        figlet)
            echo "THINKING..." | figlet >&2 2>/dev/null || true
            sleep 1 ;;
        nyancat)
            timeout 4 nyancat >&2 2>/dev/null || true ;;
        aafire)
            timeout 4 aafire >&2 2>/dev/null || true ;;
        boxes)
            fortune 2>/dev/null | boxes >&2 2>/dev/null || true
            sleep 2 ;;
        genact)
            timeout 5 genact >&2 2>/dev/null || true ;;
        cmatrix)
            timeout 4 cmatrix -b >&2 2>/dev/null || true ;;
        lolcat)
            fortune 2>/dev/null | lolcat >&2 2>/dev/null || true
            sleep 2 ;;
        tty-clock)
            timeout 4 tty-clock -scC 1 >&2 2>/dev/null || true ;;
    esac

    [ "$fullscreen" -eq 1 ] && tput rmcup >&2 2>/dev/null
    printf '\r\033[K' >&2
}

start_spinner() {
    local label="$1"
    (
        _maybe_show_joke
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

#--- プロンプト構築: prompt_file [context_files...] ---
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

#--- コマンド実行（バックグラウンド+wait方式でCtrl+C対応） ---
run_cmd() {
    local spec="$1" prompt="$2"
    local type="${spec%%:*}" agent="${spec#*:}"
    [ "$type" = "$agent" ] && agent=""

    # AIコマンドをバックグラウンドで起動
    case "$type" in
	glm)
        opencode run "'$prompt'" --agent="zai" & ;;
    gemini)
        gemini -p "$prompt" -y -s & ;;
    gemini-flash)
        gemini -p "$prompt" -y -s --model=gemini-2.5-flash & ;;
    gemini-flash-light)
        gemini -p "$prompt" -y -s --model=gemini-2.5-flash-light & ;;
    glmclaude)
        env ANTHROPIC_BASE_URL=http://localhost:8787 \
            ANTHROPIC_DEFAULT_HAIKU_MODEL=glm-4.6v \
            claude -p "$prompt" --model=Haiku --permission-mode=acceptEdits & ;;
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

    # スピナー開始
    start_spinner "$type thinking..."

    # タイムアウト用キラー（バックグラウンド）
    ( sleep "$AI_TIMEOUT" && kill "$cmd_pid" 2>/dev/null && echo "[$(date '+%H:%M:%S')] AI TIMEOUT (${AI_TIMEOUT}s)" ) &
    local timer_pid=$!

    # Ctrl+C でAIプロセス・タイマー・スピナーを全部kill
    trap "stop_spinner; kill $cmd_pid $timer_pid 2>/dev/null; wait $cmd_pid $timer_pid 2>/dev/null; log 'Interrupted'; trap - INT; return 130" INT

    # wait は Ctrl+C で即座に中断可能（timeout と違い SIGINT を受け取れる）
    wait "$cmd_pid" 2>/dev/null
    local ret=$?

    # スピナー停止 + タイマー後片付け
    stop_spinner
    kill "$timer_pid" 2>/dev/null
    wait "$timer_pid" 2>/dev/null
    trap - INT

    return $ret
}

#--- AIステップ: primary実行、失敗ならfallback ---
# 引数: label primary fallback prompt_file expect_output [context_files...]
# expect_output: AIが書くべきファイルパス（空文字""なら終了コードで判定）
run_ai() {
    local label="$1" primary="$2" fallback="$3" pf="$4" expect="$5"; shift 5
    local prompt; prompt=$(build_prompt "$pf" "$@")
    if [ -z "$prompt" ]; then
        log "[$label] prompt missing"; return 1
    fi

    # 期待出力ファイルをクリア
    [ -n "$expect" ] && rm -f "$expect"

    log "[$label] primary=$primary"
    run_cmd "$primary" "$prompt"
    # 成功判定: 期待ファイルがあればその存在チェック、なければ終了コード
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

#=== メインループ ===
log "=== Soviet Game Resolver ==="
READY_WAIT=0
_last_headline=""

# headlineが変化した時だけsayする
say_headline_if_changed() {
    local cur
    # local cur
    # cur=$(cat tmp/headline.md 2>/dev/null) || return
    # [ -z "$cur" ] && return
    # if [ "$cur" != "$_last_headline" ]; then
    #     _last_headline="$cur"
    #     echo "$cur" | say -v taro &
    # fi
}

while true; do
    state=$(head -n 1 "$STATE_FILE" 2>/dev/null | tr -d '\n\r')
    case "$state" in

    WAIT_READY)
        if commands_empty; then
            READY_WAIT=$((READY_WAIT + 1))
            # 4秒(2回)待ってからOBSERVEへ（ピース着地・マージ演出の猶予）
            if [ $READY_WAIT -ge 2 ]; then
                READY_WAIT=0
                set_state "OBSERVE"
            fi
        else
            READY_WAIT=0
        fi
        sleep 2 ;;

    #--- 目（特別: 画像認識モデル） ---
    OBSERVE)
        save_all_to_history
        observe_ok=false

        # OBSERVE用モデルリスト
        observe_models=("gemini" "sonnet" "glmclaude")
        observe_max=6
        for i in $(seq 0 $((observe_max - 1))); do
            model="${observe_models[$((i % ${#observe_models[@]}))]}"
            rm -f tmp/observe.md
            log "[OBSERVE] try $((i+1))/$observe_max: $model"
            run_cmd "$model" "$(build_prompt prompts/observe.md)"
            if grep -q 'GAME_OVER:' tmp/observe.md 2>/dev/null; then
                log "[OBSERVE] $model OK"
                observe_ok=true
                break
            fi
            log "[OBSERVE] $model → 出力なし"
        done

        say_headline_if_changed

        if ! $observe_ok; then
            log "[OBSERVE] 全モデル失敗 → DECIDE続行"
            echo -e "OBSERVE失敗（画像読み取り不可）\nGAME_OVER: false" > tmp/observe.md
        fi

        if is_game_over; then
            log "AI判定: GAME OVER"
            set_state "GAME_OVER"
        else
            set_state "DECIDE"
        fi ;;

    #--- 頭（分析+計画を1ステップで） ---
    DECIDE)
        cat tmp/observe.md
        run_ai DECIDE "$MODEL_PRIMARY" "$MODEL_FALLBACK" \
            prompts/decide.md tmp/plan.md tmp/observe.md STRATEGY.md think.md
        say_headline_if_changed
        set_state "EXECUTE" ;;

    #--- 手（機械的にドロップ） ---
    EXECUTE)
        cat think.md
        cat tmp/plan.md

        coord=$(grep -oE '[0-9]+,[0-9]+' tmp/plan.md 2>/dev/null | head -1)
        [ -z "$coord" ] && coord="650,350"
        x=$(echo "$coord" | cut -d',' -f1)
        [ "$x" -lt 400 ] 2>/dev/null && x=400
        [ "$x" -gt 900 ] 2>/dev/null && x=900
        log "DROP ${x},350"
        echo "${x},350" > "$COMMANDS"
        wait_commands_done
        sleep 3
        say_headline_if_changed
        set_state "WAIT_READY" ;;

    #--- ゲームオーバー振り返り ---
    GAME_OVER)
        run_ai GAME_OVER "$MODEL_PRIMARY" "$MODEL_FALLBACK" \
            prompts/postmortem.md "" tmp/observe.md STRATEGY.md think.md
        # 本当にgameOverか再確認してからretry
        cat tmp/postmortem.md
        sleep 2
        if is_game_over; then
            log "confirmed game over → retry"
            echo "retry" > "$COMMANDS"
            wait_commands_done
            sleep 3
        else
            log "false positive game over → skip retry"
        fi
	    say_headline_if_changed
        # 履歴クリーンアップ（postmortemで使用済み）
        clear_history
        rm -f tmp/observe.md tmp/plan.md tmp/think.md tmp/analyze.md
        set_state "WAIT_READY" ;;

    *)  log "Unknown: $state"
        set_state "WAIT_READY" ;;
    esac
    sleep 0.5
done
