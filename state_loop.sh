#!/bin/bash
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

STATE_FILE="STATE.md"
COMMANDS="commands.txt"
GAME_STATE="game_state.json"
MAX_WAIT=20
CLAUDE_FLAGS="--permission-mode=acceptEdits --model=Haiku"
AI_TIMEOUT=60

mkdir -p tmp
[ -f "$STATE_FILE" ] || echo "WAIT_READY" > "$STATE_FILE"
[ -f "tmp/turn_count.txt" ] || echo "0" > "tmp/turn_count.txt"

get_state() { head -n 1 "$STATE_FILE" 2>/dev/null | tr -d '\n\r'; }

set_state() {
    local turn=$(cat tmp/turn_count.txt 2>/dev/null || echo 0)
    echo -e "$1\nturn=$turn\ntimestamp=$(date +%s)" > "$STATE_FILE"
    echo "[$(date '+%H:%M:%S')] → $1 (turn $turn)"
}

commands_empty() { [ -z "$(tr -d '[:space:]' < "$COMMANDS" 2>/dev/null)" ]; }
file_mtime() { stat -f %m "$1" 2>/dev/null || echo 0; }

is_game_over() {
    # ゲームオーバー判定はAIのOBSERVE結果(tmp/observe.md)から取得
    grep -q 'GAME_OVER: true' tmp/observe.md 2>/dev/null
}

has_cursor() {
    python3 -c "import json,sys; d=json.load(open('$GAME_STATE')); sys.exit(0 if d.get('cursor') else 1)" 2>/dev/null
}

# デフォルト座標（盤面中央）
default_drop() {
    echo "[FALLBACK] AI失敗、中央にドロップ"
    echo "650,350" > tmp/plan.md
}

run_ai() {
    local prompt_file="$1" output_file="$2"
    shift 2
    local context_files=("$@")

    rm -f "$output_file"
    local prompt=$(cat "$prompt_file" 2>/dev/null)
    [ -z "$prompt" ] && { echo "[ERROR] missing: $prompt_file"; return 1; }

    # コンテキストファイルをインライン化
    local context=""
    for f in "${context_files[@]}"; do
        if [ -f "$f" ]; then
            context+="
--- $f ---
$(cat "$f")
---
"
        fi
    done
    if [ -n "$context" ]; then
        prompt="以下は参照データです:
${context}
${prompt}"
    fi

    run_claude_once() {
        local out_file="$1"
        shift

        rm -f "$out_file" tmp/ai_stdout.log tmp/ai_stderr.log
        timeout $AI_TIMEOUT "$@" > "$out_file" 2>tmp/ai_stderr.log
        local rc=$?
        cp "$out_file" tmp/ai_stdout.log 2>/dev/null || true
        return $rc
    }

    # GLM-4.7 (primary) - envで環境変数設定
    echo "[AI] GLM-4.7 で実行中... → $output_file"
    run_claude_once "$output_file" \
        env ANTHROPIC_BASE_URL=http://localhost:8787 ANTHROPIC_DEFAULT_HAIKU_MODEL=glm-4.7 \
        claude -p "$prompt" $CLAUDE_FLAGS
    local exit_code=$?
    if [ $exit_code -ne 0 ]; then
        echo "[EXIT] GLM-4.7 exit code: $exit_code"
    fi
    if [ -s "$output_file" ]; then
        echo "--- $output_file ---"
        cat "$output_file"
        echo "---"
        return 0
    fi

    # Haiku (fallback)
    echo "[AI] GLM-4.7失敗 → Haiku にフォールバック..."
    run_claude_once "$output_file" \
        env ANTHROPIC_DEFAULT_HAIKU_MODEL=claude-haiku-4-5-20251001 \
        claude -p "$prompt" $CLAUDE_FLAGS
    local haiku_exit=$?
    if [ $haiku_exit -ne 0 ]; then
        echo "[EXIT] Haiku exit code: $haiku_exit"
    fi
    if [ -s "$output_file" ]; then
        echo "--- $output_file ---"
        cat "$output_file"
        echo "---"
        return 0
    fi
    echo "[ERROR] AI didn't write: $output_file"
    return 1
}

echo "=== Soren Game State Machine ==="

READY_WAIT=0

while true; do
    state=$(get_state)
    case "$state" in

    "WAIT_READY")
        if commands_empty && has_cursor; then
            READY_WAIT=0
            turn=$(($(cat tmp/turn_count.txt) + 1))
            echo "$turn" > tmp/turn_count.txt
            set_state "OBSERVE"
        elif [ $READY_WAIT -ge 15 ]; then
            READY_WAIT=0
            echo "[STUCK] cursor待ちタイムアウト(30秒) → 画面クリックでリカバリ"
            echo "640,360" > "$COMMANDS"
            sleep 3
            if has_cursor; then
                turn=$(($(cat tmp/turn_count.txt) + 1))
                echo "$turn" > tmp/turn_count.txt
                set_state "OBSERVE"
            else
                echo "[STUCK] リカバリ失敗 → retry で再スタート"
                echo "retry" > "$COMMANDS"
                sleep 5
                set_state "WAIT_READY"
            fi
        else
            READY_WAIT=$((READY_WAIT+1))
            # commandsが残っていたらクリア
            commands_empty || { echo "[CLEAR] commands.txtをクリア"; echo "" > "$COMMANDS"; }
            [ $((READY_WAIT % 5)) -eq 0 ] && echo "[WAIT] cursor待ち... (${READY_WAIT}回/15)"
            sleep 2
        fi ;;

    "OBSERVE")
        # game_state.json の gameOver フラグで先行判定
        if python3 -c "import json; d=json.load(open('$GAME_STATE')); exit(0 if d.get('gameOver') else 1)" 2>/dev/null; then
            echo "[!] game_state.json判定: GAME OVER (cursor=false, next=false)"
            echo -e "# 盤面観察\n\nGAME_OVER: true\n理由: cursor/nextが検出できない（ゲームオーバー画面）" > tmp/observe.md
            set_state "GAME_OVER"
        else
            # 前回のobserve結果をクリア（ゲームオーバー誤引継ぎ防止）
            rm -f tmp/observe.md
            if run_ai "prompts/observe.md" "tmp/observe.md"; then
                # AIのOBSERVE結果からゲームオーバー判定
                if is_game_over; then
                    echo "[!] AI判定: GAME OVER"
                    set_state "GAME_OVER"
                else
                    set_state "ANALYZE"
                fi
            else
                echo "[SKIP] OBSERVE失敗 → デフォルトで続行"
                echo "OBSERVE失敗 - スクリーンショット確認不可" > tmp/observe.md
                set_state "ANALYZE"
            fi
        fi ;;

    "ANALYZE")
        if run_ai "prompts/analyze.md" "tmp/analyze.md" "tmp/observe.md" "STRATEGY.md" "think.md"; then
            set_state "PLAN"
        else
            echo "[SKIP] ANALYZE失敗 → デフォルトドロップ"
            default_drop
            set_state "EXECUTE"
        fi ;;

    "PLAN")
        if run_ai "prompts/plan.md" "tmp/plan.md" "tmp/analyze.md"; then
            set_state "EXECUTE"
        else
            echo "[SKIP] PLAN失敗 → デフォルトドロップ"
            default_drop
            set_state "EXECUTE"
        fi ;;

    "EXECUTE")
        coord=$(head -n 1 tmp/plan.md | grep -oE '[0-9]+,[0-9]+' | head -1)
        [ -z "$coord" ] && coord=$(grep -oE '[0-9]+,\s*[0-9]+' tmp/plan.md | head -1 | tr -d ' ')
        [ -z "$coord" ] && coord="650,350"

        x=$(echo "$coord" | cut -d',' -f1)
        [ "$x" -lt 400 ] 2>/dev/null && x=400
        [ "$x" -gt 900 ] 2>/dev/null && x=900
        coord="${x},350"

        echo "[EXECUTE] $coord"
        echo "$coord" > "$COMMANDS"
        file_mtime "$GAME_STATE" > tmp/last_mtime.txt
        set_state "WAIT_LANDED" ;;

    "WAIT_LANDED")
        w=0
        while true; do
            if commands_empty; then
                [ "$(file_mtime $GAME_STATE)" != "$(cat tmp/last_mtime.txt 2>/dev/null)" ] && break
            fi
            w=$((w+1))
            if [ $w -ge $MAX_WAIT ]; then
                echo "[TIMEOUT] WAIT_LANDED → 強制続行"
                echo "" > "$COMMANDS"
                break
            fi
            sleep 1
        done
        sleep 1
        set_state "WAIT_READY" ;;

    "GAME_OVER")
        run_ai "prompts/postmortem.md" "tmp/postmortem.md" "tmp/observe.md" "STRATEGY.md" "think.md" || echo "[WARN] postmortem失敗、続行"
        echo "retry" > "$COMMANDS"
        set_state "WAIT_RETRY" ;;

    "WAIT_RETRY")
        w=0
        while ! commands_empty; do
            w=$((w+1))
            if [ $w -ge $MAX_WAIT ]; then
                echo "[TIMEOUT] WAIT_RETRY → 強制続行"
                echo "" > "$COMMANDS"
                break
            fi
            sleep 1
        done
        sleep 3
        set_state "WAIT_READY" ;;

    *) echo "[ERROR] Unknown: $state"; set_state "WAIT_READY" ;;
    esac
    sleep 0.5
done
