#!/bin/bash
# eloop.sh - Self-Improving Strategy Loop
#
# 外側ループ: strategy_runner.py で1試合自律プレイ → AI で strategy.py 改善 → 次試合
# jloop.sh のヘルパー関数を再利用。

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

COMMANDS="commands.txt"
GAME_STATE="game_state.json"
AI_TIMEOUT=600

# strategy 関連
STRATEGY_FILE="strategy.py"
STRATEGY_VERSIONS_DIR="strategy_versions"
HISTORY_DIR="game_history"
HISTORY_FILE="$HISTORY_DIR/latest.jsonl"

# 使用モデル
MODEL_PRIMARY="glm"
MODEL_FALLBACK="opencode:glmflash"

# カウンタ
GAME_NUM=0

mkdir -p "$STRATEGY_VERSIONS_DIR" "$HISTORY_DIR"

#--- ヘルパー (jloop.sh と共通) ---
commands_empty() { [ -z "$(tr -d '[:space:]' <"$COMMANDS" 2>/dev/null)" ]; }
log() { echo "[$(date '+%H:%M:%S')] $*"; }

is_game_over() {
	local s
	s=$(python3 -c "import json; print(json.load(open('$GAME_STATE')).get('state',''))" 2>/dev/null)
	[ "$s" = "GAMEOVER" ] || [ "$s" = "STOP" ]
}

is_move_state() {
	local s
	s=$(python3 -c "import json; print(json.load(open('$GAME_STATE')).get('state',''))" 2>/dev/null)
	[ "$s" = "MOVE" ]
}

wait_commands_done() {
	for _ in $(seq 1 20); do
		commands_empty && return 0
		sleep 1
	done
	log "TIMEOUT: commands未消化 → クリア"
	echo "" >"$COMMANDS"
}

#--- ジョークコマンドをランダムに表示 ---
_maybe_show_joke() {
	# 約10%の確率で発動
	[ $((RANDOM % 10)) -ne 0 ] && return
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

#--- スピナー (ジョーク付き) ---
_spinner_pid=0
start_spinner() {
	local label="$1"
	(
		local frames=('⣾' '⣽' '⣻' '⢿' '⡿' '⣟' '⣯' '⣷')
		local i=0 start=$SECONDS
		while true; do
			local e=$((SECONDS - start))
			local m=$((e / 60)) s=$((e % 60))
			printf '\r  \033[1;35m%s\033[0m \033[1m%s\033[0m \033[2m%d:%02d\033[0m  ' \
				"${frames[i % ${#frames[@]}]}" "$label" "$m" "$s" >&2
			sleep 0.12
			((i++))
			# 約600回(≒72秒)ごとにジョーク判定
			if [ $((i % 600)) -eq 0 ]; then
				_maybe_show_joke
			fi
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
	local pf="$1"
	shift
	local p
	p=$(cat "$pf" 2>/dev/null) || return 1
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

	# プロンプトを一時ファイルに書き出し（シェルエスケープ問題を回避）
	local prompt_file
	prompt_file=$(mktemp /tmp/eloop_prompt.XXXXXX)
	printf '%s' "$prompt" >"$prompt_file"
	log "[CMD] prompt to $prompt_file ($(wc -c <"$prompt_file" | tr -d ' ') bytes)"

	case "$type" in
	glm)
		opencode run "$(cat "$prompt_file")" --agent="zai" &
		;;
	gemini)
		gemini -p "$(cat "$prompt_file")" -y -s &
		;;
	gemini-flash)
		gemini -p "$(cat "$prompt_file")" -y -s --model=gemini-2.5-flash &
		;;
	sonnet)
		claude -p "$(cat "$prompt_file")" --model=sonnet --permission-mode=acceptEdits &
		;;
	opus)
		claude -p "$(cat "$prompt_file")" --model=opus --permission-mode=acceptEdits &
		;;
	claude)
		claude -p "$(cat "$prompt_file")" --model=Haiku --permission-mode=acceptEdits &
		;;
	opencode)
		opencode run "$(cat "$prompt_file")" --agent="${agent:-glmflash}" &
		;;
	esac
	local cmd_pid=$!

	start_spinner "$type thinking..."

	(sleep "$AI_TIMEOUT" && kill "$cmd_pid" 2>/dev/null && log "AI TIMEOUT (${AI_TIMEOUT}s)") &
	local timer_pid=$!

	trap "stop_spinner; kill $cmd_pid $timer_pid 2>/dev/null; wait $cmd_pid $timer_pid 2>/dev/null; log 'Interrupted'; trap - INT; return 130" INT

	wait "$cmd_pid" 2>/dev/null
	local ret=$?

	stop_spinner
	kill "$timer_pid" 2>/dev/null
	wait "$timer_pid" 2>/dev/null
	trap - INT

	# 一時プロンプトファイルを削除
	rm -f "$prompt_file"

	return $ret
}

#--- AIステップ ---
run_ai() {
	local label="$1" primary="$2" fallback="$3" pf="$4" expect="$5"
	shift 5
	local prompt
	prompt=$(build_prompt "$pf" "$@")
	if [ -z "$prompt" ]; then
		log "[$label] prompt missing"
		return 1
	fi

	# expectファイルの変更検出用にタイムスタンプを記録 (rm -f は行わない — AI失敗時にファイルが消えるのを防止)
	local expect_mtime_before=""
	if [ -n "$expect" ] && [ -f "$expect" ]; then
		expect_mtime_before=$(stat -f '%m' "$expect" 2>/dev/null)
	fi

	log "[$label] primary=$primary"
	run_cmd "$primary" "$prompt"
	if [ -n "$expect" ]; then
		local expect_mtime_after=""
		[ -f "$expect" ] && expect_mtime_after=$(stat -f '%m' "$expect" 2>/dev/null)
		if [ -s "$expect" ] && [ "$expect_mtime_after" != "$expect_mtime_before" ]; then
			log "[$label] primary OK ($expect written)"
			return 0
		fi
	else
		[ $? -eq 0 ] && return 0
	fi

	log "[$label] primary failed → fallback=$fallback"
	run_cmd "$fallback" "$prompt"
	if [ -n "$expect" ]; then
		local expect_mtime_fb=""
		[ -f "$expect" ] && expect_mtime_fb=$(stat -f '%m' "$expect" 2>/dev/null)
		if [ ! -s "$expect" ] || [ "$expect_mtime_fb" = "$expect_mtime_before" ]; then
			log "[$label] fallback also failed ($expect not written)"
			return 1
		fi
	fi
}

#--- strategy.py バリデーション ---
# 結果を VALIDATE_ERROR に格納（リトライ用）
VALIDATE_ERROR=""

validate_strategy() {
	log "[VALIDATE] strategy.py をチェック中..."
	VALIDATE_ERROR=""

	# 1. decide() の存在チェック
	local sig_out
	sig_out=$(python3 - <<'PYEOF' 2>&1
import importlib.util, sys, inspect
spec = importlib.util.spec_from_file_location('strategy', 'strategy.py')
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
if not hasattr(mod, 'decide'):
    print('ERROR: decide() not found')
    sys.exit(1)
sig = inspect.signature(mod.decide)
params = list(sig.parameters.keys())
if len(params) < 2:
    print(f'ERROR: decide() needs 2+ params, got {len(params)}: {params}')
    sys.exit(1)
print(f'OK: decide({", ".join(params)})')
PYEOF
)
	if [ $? -ne 0 ]; then
		VALIDATE_ERROR="decide()シグネチャチェック失敗: $sig_out"
		log "[VALIDATE] $VALIDATE_ERROR"
		return 1
	fi

	# 2. テスト実行 (game_state.json があれば)
	if [ -f "$GAME_STATE" ]; then
		local test_out
		test_out=$(python3 strategy.py "$GAME_STATE" 2>&1)
		if [ $? -ne 0 ]; then
			VALIDATE_ERROR="テスト実行失敗: $test_out"
			log "[VALIDATE] $VALIDATE_ERROR"
			return 1
		fi
		# JSON出力チェック
		if ! echo "$test_out" | python3 -c "import json,sys; d=json.load(sys.stdin); assert 'x' in d" 2>/dev/null; then
			VALIDATE_ERROR="テスト出力にxフィールドなし: $test_out"
			log "[VALIDATE] $VALIDATE_ERROR"
			return 1
		fi
		log "[VALIDATE] テスト実行OK"
	fi

	return 0
}

#--- バージョン管理 ---
save_strategy_version() {
	local score="$1"
	GAME_NUM=$((GAME_NUM + 1))
	local version_file
	version_file=$(printf "%s/v%03d_score%04d_strategy.py" "$STRATEGY_VERSIONS_DIR" "$GAME_NUM" "$score")
	cp "$STRATEGY_FILE" "$version_file"
	log "[VERSION] saved: $version_file"

	# 直近3戦略のみ保持（古いものを削除、殿堂入りbest_*は除く）
	local total
	total=$(ls -1 "$STRATEGY_VERSIONS_DIR"/v[0-9]*_strategy.py 2>/dev/null | wc -l | tr -d ' ')
	local delete_count=$((total - 3))
	if [ "$delete_count" -gt 0 ]; then
		ls -1 "$STRATEGY_VERSIONS_DIR"/v[0-9]*_strategy.py | sort | head -n "$delete_count" | while read -r f; do
			rm -f "$f"
			log "[VERSION] pruned: $(basename "$f")"
		done
	fi
}

#--- ベスト管理 ---
update_best() {
	local current_score="$1"
	local best_score
	best_score=$(cat best_score.txt 2>/dev/null || echo 0)

	if [ "${current_score:-0}" -gt "${best_score:-0}" ]; then
		log "🏆 NEW HIGH SCORE: $current_score (prev: $best_score)"
		echo "$current_score" >best_score.txt

		# 殿堂入り保存（スコアをファイル名に）
		local hall_file
		hall_file=$(printf "%s/best_score%04d_strategy.py" "$STRATEGY_VERSIONS_DIR" "$current_score")
		cp "$STRATEGY_FILE" "$hall_file"
		log "[HALL OF FAME] saved: $hall_file"

		# strategy.py の変更履歴に [BEST:スコア] タグを付与
		python3 tag_best_changelog.py "$STRATEGY_FILE" "$current_score" 2>/dev/null
		python3 tag_best_changelog.py "$hall_file" "$current_score" 2>/dev/null

		# 殿堂入りも直近3つのみ保持（スコア順でソートし上位3つを残す）
		local best_total
		best_total=$(ls -1 "$STRATEGY_VERSIONS_DIR"/best_score*_strategy.py 2>/dev/null | wc -l | tr -d ' ')
		local best_delete=$((best_total - 3))
		if [ "$best_delete" -gt 0 ]; then
			ls -1 "$STRATEGY_VERSIONS_DIR"/best_score*_strategy.py | sort | head -n "$best_delete" | while read -r f; do
				rm -f "$f"
				log "[HALL OF FAME] pruned: $(basename "$f")"
			done
		fi

		return 0
	else
		log "Score: $current_score (best: $best_score)"
		return 1
	fi
}

#--- 履歴アーカイブ ---
archive_history() {
	local score="$1"
	local ts
	ts=$(date '+%Y%m%d_%H%M%S')
	if [ -f "$HISTORY_FILE" ]; then
		local archive
		archive=$(printf "%s/%s_score%04d.jsonl" "$HISTORY_DIR" "$ts" "$score")
		cp "$HISTORY_FILE" "$archive"
		log "[ARCHIVE] $archive"
	fi
}

#--- MOVE状態待ち ---
wait_for_move() {
	log "MOVE状態を待機中..."
	local waited=0
	while [ $waited -lt 60 ]; do
		if [ -f "$GAME_STATE" ] && is_move_state; then
			log "MOVE状態検出"
			return 0
		fi
		sleep 2
		waited=$((waited + 2))
		# 待ち時間中にジョーク
		[ $((waited % 10)) -eq 0 ] && _maybe_show_joke
	done
	log "TIMEOUT: MOVE状態待ち"
	return 1
}

#--- リトライ (新ゲーム開始) ---
send_retry() {
	log "retry送信..."
	echo "retry" >"$COMMANDS"
	wait_commands_done
	sleep 3

	# 新ゲーム検知待ち
	local waited=0
	while [ $waited -lt 60 ]; do
		local rs
		rs=$(python3 -c "
import json
try:
    d = json.load(open('$GAME_STATE'))
    s = d.get('state','')
    n = len(d.get('pieces',[]))
    if s == 'MOVE' and n <= 2:
        print('ready')
    elif s == 'GAMEOVER' or s == 'STOP':
        print('still_over')
    else:
        print('waiting')
except:
    print('waiting')
" 2>/dev/null)

		case "$rs" in
		ready)
			log "新ゲーム検出"
			return 0
			;;
		still_over)
			if [ $((waited % 20)) -eq 0 ] && [ $waited -gt 0 ]; then
				log "まだGAMEOVER → retry再送"
				echo "retry" >"$COMMANDS"
				wait_commands_done
			fi
			;;
		esac
		sleep 2
		waited=$((waited + 2))
		# 待ち時間中にジョーク
		[ $((waited % 10)) -eq 0 ] && _maybe_show_joke
	done
	log "WARNING: 新ゲーム検知タイムアウト"
	return 1
}

#=== メインループ ===
log "=== Soren Evolution Loop (eloop) ==="
log "MODEL_PRIMARY=$MODEL_PRIMARY MODEL_FALLBACK=$MODEL_FALLBACK"
log "strategy.py → strategy_runner.py → AI改善 → repeat"

# 前回中断時のリカバリ: .bak が残っていて strategy.py がない場合は復元
if [ ! -f "$STRATEGY_FILE" ] && [ -f "${STRATEGY_FILE}.bak" ]; then
	log "[RECOVER] $STRATEGY_FILE が消失 → ${STRATEGY_FILE}.bak から復元"
	cp "${STRATEGY_FILE}.bak" "$STRATEGY_FILE"
fi

# 初期バリデーション
if [ ! -f "$STRATEGY_FILE" ]; then
	log "ERROR: $STRATEGY_FILE が見つかりません"
	exit 1
fi

if ! validate_strategy; then
	log "ERROR: 初期 strategy.py のバリデーション失敗"
	exit 1
fi

# MOVE状態待ち（初回）
wait_for_move || {
	log "ゲームが起動していません。soviet_local.mjs を先に起動してください。"
	exit 1
}

while true; do
	GAME_NUM_DISPLAY=$((GAME_NUM + 1))
	log ""
	log "========================================="
	log "  Game #${GAME_NUM_DISPLAY}"
	log "========================================="

	#--- Step 1: strategy_runner.py で1試合プレイ ---
	log "[PLAY] strategy_runner.py 実行中..."
	RUNNER_TMPFILE=$(mktemp /tmp/eloop_runner.XXXXXX)
	python3 -u strategy_runner.py 2>&1 | tee "$RUNNER_TMPFILE"
	RUNNER_EXIT=${PIPESTATUS[0]}

	# 結果抽出 (---RESULT--- 以降のJSON)
	RESULT_JSON=$(sed -n '/^---RESULT---$/,$ p' "$RUNNER_TMPFILE" | tail -n 1)
	rm -f "$RUNNER_TMPFILE"

	if [ -z "$RESULT_JSON" ]; then
		log "WARNING: strategy_runner.py の結果取得失敗"
		RESULT_JSON='{"score":0,"turns":0,"state":"UNKNOWN"}'
	fi

	SCORE=$(echo "$RESULT_JSON" | python3 -c "import json,sys; print(json.load(sys.stdin).get('score',0))" 2>/dev/null || echo 0)
	TURNS=$(echo "$RESULT_JSON" | python3 -c "import json,sys; print(json.load(sys.stdin).get('turns',0))" 2>/dev/null || echo 0)

	log "[RESULT] Score=$SCORE, Turns=$TURNS"

	#--- Step 2: バージョン保存 ---
	save_strategy_version "$SCORE"

	#--- Step 3: ベスト判定 ---
	update_best "$SCORE"

	#--- Step 4: 履歴アーカイブ ---
	archive_history "$SCORE"

	#--- Step 5+6: AI で strategy.py 改善 (バリデーション失敗時リトライ) ---
	log "[IMPROVE] AI による strategy.py 改善..."

	# バックアップ
	cp "$STRATEGY_FILE" "${STRATEGY_FILE}.bak"

	IMPROVE_OK=false
	MAX_IMPROVE_RETRIES=3

	# 直近3バージョン + 殿堂入り戦略を収集
	PAST_STRATEGY_FILES=""
	for vf in $(ls -1t "$STRATEGY_VERSIONS_DIR"/v[0-9]*_strategy.py 2>/dev/null | head -3); do
		PAST_STRATEGY_FILES="$PAST_STRATEGY_FILES $vf"
	done
	HALL_OF_FAME_FILES=""
	for hf in "$STRATEGY_VERSIONS_DIR"/best_score*_strategy.py; do
		[ -f "$hf" ] && HALL_OF_FAME_FILES="$HALL_OF_FAME_FILES $hf"
	done

	for retry in $(seq 1 "$MAX_IMPROVE_RETRIES"); do
		if [ "$retry" -eq 1 ]; then
			# 初回: 通常の改善プロンプト
			run_ai IMPROVE "$MODEL_PRIMARY" "$MODEL_FALLBACK" \
				prompts/improve_strategy.md "$STRATEGY_FILE" \
				"$STRATEGY_FILE" "$HISTORY_FILE" "$GAME_STATE" $PAST_STRATEGY_FILES $HALL_OF_FAME_FILES
		else
			# リトライ: エラー内容を伝えて修正させる
			log "[IMPROVE] リトライ $retry/$MAX_IMPROVE_RETRIES (エラー: $VALIDATE_ERROR)"

			# 修正プロンプトを一時ファイルに作成
			FIX_PROMPT_FILE=$(mktemp /tmp/eloop_fix_prompt.XXXXXX)
			cat > "$FIX_PROMPT_FILE" <<FIXEOF
strategy.py のバリデーションが失敗した。以下のエラーを修正せよ。

## エラー内容
$VALIDATE_ERROR

## 修正ルール
- strategy.py を修正して上記エラーを解消せよ
- decide(game_state, analysis) のシグネチャは変更禁止
- if __name__ == "__main__" ブロックは変更禁止
- decide() は必ず {"x": float, "reason": str} を返すこと
- Write ツールで strategy.py に書き込むこと
FIXEOF
			run_ai "FIX(${retry})" "$MODEL_PRIMARY" "$MODEL_FALLBACK" \
				"$FIX_PROMPT_FILE" "$STRATEGY_FILE" \
				"$STRATEGY_FILE"
			rm -f "$FIX_PROMPT_FILE"
		fi

		# バリデーション
		if validate_strategy; then
			log "[IMPROVE] バリデーション成功 → 新strategy採用"
			rm -f "${STRATEGY_FILE}.bak"
			python3 trim_changelog.py "$STRATEGY_FILE" 3 2>/dev/null
			IMPROVE_OK=true
			break
		fi
	done

	if [ "$IMPROVE_OK" = false ]; then
		log "[IMPROVE] ${MAX_IMPROVE_RETRIES}回リトライ後もバリデーション失敗 → 前バージョンに復元"
		mv "${STRATEGY_FILE}.bak" "$STRATEGY_FILE"
	fi

	#--- Step 7: git commit & push ---
	log "[GIT] コミット&プッシュ..."
	git add -A
	git commit -m "eloop Game #${GAME_NUM_DISPLAY}: score=${SCORE}, turns=${TURNS}" 2>/dev/null && \
		git push 2>/dev/null && \
		log "[GIT] push完了" || \
		log "[GIT] 変更なし or push失敗"

	#--- Step 8: retry → 新ゲーム ---
	if is_game_over; then
		send_retry
	else
		log "GAMEOVER未検出 → MOVE状態待ち"
		wait_for_move || {
			log "ゲーム停止 → retry試行"
			send_retry
		}
	fi

	_maybe_show_joke
	sleep 2
done
