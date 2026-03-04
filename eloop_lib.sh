#!/bin/bash
# eloop_lib.sh - Soren Evolution Loop 共通ライブラリ
#
# soren_loop.sh から source される。AI による書き換え対象外の安定レイヤー。
# ヘルパー関数、AI実行、バリデーション、バージョン管理、ラジオトーク、
# コメント処理、改善ステート管理を提供する。

# --- スクリプトディレクトリ ---
ELOOP_LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ELOOP_LIB_DIR"

# --- 定数 ---
COMMANDS="commands.txt"
GAME_STATE="game_state.json"

STRATEGY_FILE="strategy.py"
STRATEGY_VERSIONS_DIR="strategy_versions"
HISTORY_DIR="game_history"
HISTORY_FILE="$HISTORY_DIR/latest.jsonl"

MODEL_PRIMARY="glm"
MODEL_FALLBACK="opencode:glmflash"

GAME_COUNT_FILE="game_count.txt"

RADIO_AGENT="zai"
RADIO_FALLBACK="glmflash"
RADIO_OPENCODE_TIMEOUT=180
RADIO_CLAUDE_MODEL="sonnet"
RADIO_OPENCODE_PERMISSION='{"*":"deny","read":"allow","glob":"allow","grep":"allow","list":"allow"}'
RADIO_SAY_RATE=150
PAST_RADIO_TOPICS="tmp/past_radio_topics.txt"
PAST_NEWS_READ="tmp/.past_news_read.txt"
PAST_NEWS_READ_KEYS="tmp/.past_news_read_keys.txt"

IMPROVE_STATE_FILE="tmp/improve_state.json"
ACCUMULATED_GAMES_FILE="tmp/accumulated_games.json"
ROLLING_SCORES_FILE="tmp/rolling_scores.json"
REJECTED_HASHES_FILE="tmp/rejected_hashes.txt"
REGRESSION_ROLLBACK_DONE=0
REGRESSION_ROLLBACK_HASH=""
MIN_GAMES_BEFORE_IMPROVE=10
MIN_GAMES_FOR_BEST_ROLLBACK=10
STRATEGY_HASH_ARCHIVE_DIR="strategy_versions/by_hash"
HASH_ARCHIVE_KEEP_TOP=10
COMMENT_QUEUE_DIR="tmp/.comment_queue"
COMMENT_WATCHER_PID_FILE="tmp/.comment_queue/watcher.pid"
COMMENT_WATCHER_INTERVAL=10
COMMENT_WORKER_HEALTH_TTL=30
COMMENT_PLAYER_HEARTBEAT_FILE="tmp/.comment_queue/player.heartbeat"
COMMENT_WATCHER_HEARTBEAT_FILE="tmp/.comment_queue/watcher.heartbeat"
mkdir -p "$STRATEGY_VERSIONS_DIR" "$STRATEGY_HASH_ARCHIVE_DIR" "$HISTORY_DIR" "$COMMENT_QUEUE_DIR" "tmp/.twitch_chat" tmp

#=== コアヘルパー ===

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
		[ $((waited % 10)) -eq 0 ] && _maybe_show_joke
	done
	log "TIMEOUT: MOVE状態待ち"
	return 1
}

send_retry() {
	log "retry送信..."
	echo "retry" >"$COMMANDS"
	wait_commands_done
	sleep 3

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
		[ $((waited % 10)) -eq 0 ] && _maybe_show_joke
	done
	log "WARNING: 新ゲーム検知タイムアウト"
	return 1
}

#=== ジョークコマンド ===

_maybe_show_joke() {
	[ $((RANDOM % 10)) -ne 0 ] && return
	printf '\r\033[K' >&2

	local jokes=()
	command -v sl &>/dev/null && jokes+=("sl")
	command -v fortune &>/dev/null && command -v cowsay &>/dev/null && jokes+=("fortune_cowsay")
	command -v toilet &>/dev/null && jokes+=("toilet")
	command -v figlet &>/dev/null && jokes+=("figlet")
	command -v nyancat &>/dev/null && jokes+=("nyancat")
	command -v aafire &>/dev/null && jokes+=("aafire")
	command -v boxes &>/dev/null && command -v fortune &>/dev/null && jokes+=("boxes")
	command -v genact &>/dev/null && jokes+=("genact")
	command -v cmatrix &>/dev/null && jokes+=("cmatrix")
	command -v lolcat &>/dev/null && command -v fortune &>/dev/null && jokes+=("lolcat")
	command -v tty-clock &>/dev/null && jokes+=("tty-clock")
	[ ${#jokes[@]} -eq 0 ] && return

	local pick="${jokes[$((RANDOM % ${#jokes[@]}))]}"

	local fullscreen=0
	case "$pick" in nyancat | aafire | cmatrix | tty-clock) fullscreen=1 ;; esac
	[ "$fullscreen" -eq 1 ] && tput smcup >&2 2>/dev/null

	case "$pick" in
	sl)
		timeout 10 sl -l >&2 2>/dev/null || true
		;;
	fortune_cowsay)
		fortune 2>/dev/null | cowsay >&2 2>/dev/null || true
		sleep 5
		;;
	toilet)
		echo "THINKING..." | toilet --gay 2>/dev/null >&2 || true
		sleep 4
		;;
	figlet)
		echo "THINKING..." | figlet >&2 2>/dev/null || true
		sleep 4
		;;
	nyancat)
		timeout 10 nyancat >&2 2>/dev/null || true
		;;
	aafire)
		timeout 10 aafire >&2 2>/dev/null || true
		;;
	boxes)
		fortune 2>/dev/null | boxes >&2 2>/dev/null || true
		sleep 5
		;;
	genact)
		timeout 12 genact >&2 2>/dev/null || true
		;;
	cmatrix)
		timeout 10 cmatrix -b >&2 2>/dev/null || true
		;;
	lolcat)
		fortune 2>/dev/null | lolcat >&2 2>/dev/null || true
		sleep 5
		;;
	tty-clock)
		timeout 10 tty-clock -scC 1 >&2 2>/dev/null || true
		;;
	esac

	[ "$fullscreen" -eq 1 ] && tput rmcup >&2 2>/dev/null
	printf '\r\033[K' >&2
}

#=== スピナー ===

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
			if [ $((i % 60)) -eq 0 ]; then
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

#=== プロンプト構築 ===

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

#=== コマンド実行 ===

run_cmd() {
	local spec="$1" prompt="$2"
	local type="${spec%%:*}" agent="${spec#*:}"
	[ "$type" = "$agent" ] && agent=""

	local prompt_file
	prompt_file=$(mktemp /tmp/eloop_prompt.XXXXXX)
	printf '%s' "$prompt" >"$prompt_file"
	log "[CMD] $(wc -c <"$prompt_file" | tr -d ' ')B → $type"

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

	trap "stop_spinner; kill $cmd_pid 2>/dev/null; wait $cmd_pid 2>/dev/null; log 'Interrupted'; trap - INT; return 130" INT

	wait "$cmd_pid" 2>/dev/null
	local ret=$?

	stop_spinner
	trap - INT

	rm -f "$prompt_file"

	return $ret
}

#=== AIステップ ===

run_ai() {
	local label="$1" primary="$2" fallback="$3" pf="$4" expect="$5"
	shift 5
	local prompt
	prompt=$(build_prompt "$pf" "$@")
	if [ -z "$prompt" ]; then
		log "[$label] prompt missing"
		return 1
	fi

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

#=== strategy.py バリデーション ===

VALIDATE_ERROR=""

validate_strategy() {
	# 引数でファイルパスを指定可能 (デフォルト: strategy.py)
	local target_file="${1:-strategy.py}"
	log "[VALIDATE] checking $target_file..."
	VALIDATE_ERROR=""

	local sig_out
	sig_out=$(
		python3 - "$target_file" <<'PYEOF' 2>&1
import sys, inspect, types
target = sys.argv[1]

# .py.staging ファイルを扱うため、exec() でモジュールを作成
with open(target, 'r', encoding='utf-8') as f:
    source = f.read()

mod = types.ModuleType('strategy')
exec(source, mod.__dict__)

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

	if [ -f "$GAME_STATE" ]; then
		local test_out
		test_out=$(python3 "$target_file" "$GAME_STATE" 2>&1)
		if [ $? -ne 0 ]; then
			VALIDATE_ERROR="テスト実行失敗: $test_out"
			log "[VALIDATE] $VALIDATE_ERROR"
			return 1
		fi
		if ! echo "$test_out" | python3 -c "import json,sys; d=json.load(sys.stdin); assert 'x' in d" 2>/dev/null; then
			VALIDATE_ERROR="テスト出力にxフィールドなし: $test_out"
			log "[VALIDATE] $VALIDATE_ERROR"
			return 1
		fi
		log "[VALIDATE] テスト実行OK"
	fi

	return 0
}

#=== バージョン管理 ===

save_strategy_version() {
	local score="$1"
	GAME_NUM=$((GAME_NUM + 1))
	echo "$GAME_NUM" >"$GAME_COUNT_FILE"
	local version_file
	version_file=$(printf "%s/v%03d_score%04d_strategy.py" "$STRATEGY_VERSIONS_DIR" "$GAME_NUM" "$score")
	# スナップショットがあれば試合時の戦略を保存 (裏の改善で書き換わっていても正確)
	local src="${STRATEGY_FILE}.game_snapshot"
	[ ! -f "$src" ] && src="$STRATEGY_FILE"
	cp "$src" "$version_file"
	log "[VERSION] saved: $version_file"

	local total
	total=$(ls -1 "$STRATEGY_VERSIONS_DIR"/v[0-9]*_strategy.py 2>/dev/null | wc -l | tr -d ' ')
	local delete_count=$((total - 10))
	if [ "$delete_count" -gt 0 ]; then
		ls -1 "$STRATEGY_VERSIONS_DIR"/v[0-9]*_strategy.py | sort | head -n "$delete_count" | while read -r f; do
			rm -f "$f"
			log "[VERSION] pruned: $(basename "$f")"
		done
	fi
}

update_best() {
	local current_score="$1"
	local best_score
	best_score=$(cat best_score.txt 2>/dev/null || echo 0)

	if [ "${current_score:-0}" -gt "${best_score:-0}" ]; then
		log "NEW HIGH SCORE: $current_score (prev: $best_score)"
		echo "$current_score" >best_score.txt

		local hall_file
		hall_file=$(printf "%s/best_score%04d_strategy.py" "$STRATEGY_VERSIONS_DIR" "$current_score")
		# スナップショットがあれば試合時の戦略を保存 (裏の改善で書き換わっていても正確)
		local src="${STRATEGY_FILE}.game_snapshot"
		[ ! -f "$src" ] && src="$STRATEGY_FILE"
		cp "$src" "$hall_file"
		log "[HALL OF FAME] saved: $hall_file"

		python3 tag_best_changelog.py "$STRATEGY_FILE" "$current_score" 2>/dev/null
		python3 tag_best_changelog.py "$hall_file" "$current_score" 2>/dev/null

		local best_total
		best_total=$(ls -1 "$STRATEGY_VERSIONS_DIR"/best_score*_strategy.py 2>/dev/null | wc -l | tr -d ' ')
		local best_delete=$((best_total - 10))
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

#=== ANSIエスケープ除去 ===

_strip_ansi() {
	perl -pe 's/\e\[[0-9;]*[a-zA-Z]//g; s/[\x00-\x09\x0b-\x0d\x0e-\x1f]//g' | tr -d '\r'
}

#=== opencode run を疑似TTY付きで実行 ===

_run_opencode_radio() {
	local agent="$1" prompt_file="$2"
	local raw_file
	raw_file=$(mktemp /tmp/eloop_radio_raw_XXXXXXXX)
	timeout "${RADIO_OPENCODE_TIMEOUT}" \
		script -q "$raw_file" bash -c "LC_ALL=en_US.UTF-8 OPENCODE_PERMISSION='$RADIO_OPENCODE_PERMISSION' opencode run --agent \"$agent\" \"\$(cat '$prompt_file')\" 2>&1" >/dev/null 2>&1
	local rc=$?
	if [ $rc -eq 124 ]; then
		# command substitution に混ざらないよう stderr に出す
		log "[RADIO] opencode timeout (${RADIO_OPENCODE_TIMEOUT}s, agent=$agent)" >&2
		rm -f "$raw_file"
		return 1
	fi
	if [ $rc -ne 0 ]; then
		# 非タイムアウト失敗も本文扱いせず fallback へ渡す
		log "[RADIO] opencode failed (rc=$rc, agent=$agent)" >&2
		rm -f "$raw_file"
		return 1
	fi
	cat "$raw_file" |
		_strip_ansi |
		grep -v '^>' |
		grep -v '^\^D' |
		grep -v '^Script started on ' |
		grep -v '^Script done on ' |
		grep -v '^/[^ ]*$' |
		grep -v '^[[:space:]]*/Users/' |
		sed -E 's#</?(arg_name|arg_value|think|analysis|final|assistant_response|tool_call|tool_result)[^>]*>##g' |
		sed '/^[[:space:]]*$/d'
	rm -f "$raw_file"
}

_run_claude_radio() {
	local prompt_file="$1"
	local prompt
	prompt=$(cat "$prompt_file" 2>/dev/null)
	if [ -z "$prompt" ]; then
		return 1
	fi
	# command substitution に混ざらないよう stderr に出す
	log "[RADIO] claude fallback (model=$RADIO_CLAUDE_MODEL)" >&2
	claude -p "$prompt" --model "$RADIO_CLAUDE_MODEL" 2>/dev/null
}

_clean_comment_talk() {
	printf '%s\n' "$1" |
		grep -Eiv '^[[:space:]]*(assistant|analysis|final|tool_call|tool_result)[[:space:]]*$' |
		grep -Eiv '^[[:space:]]*(agent|model|provider)[[:space:]]*[:=].*$' |
		grep -Eiv '^[[:space:]]*(zai|glmflash|sonnet|claude|opencode)[[:space:]]*$' |
		sed '/^[[:space:]]*$/d'
}

_is_valid_comment_talk() {
	local talk="$1"
	local compact
	compact=$(printf '%s' "$talk" | tr -d '[:space:]')
	[ ${#compact} -ge 24 ] || return 1
	printf '%s' "$talk" | grep -Eq '[。！？]' || return 1
	return 0
}

#=== ラジオトーク: 共通ヘルパー ===

_radio_time_context() {
	_rc_hour=$(date '+%H')
	_rc_time=$(date '+%H:%M')
	if [ "$_rc_hour" -ge 5 ] && [ "$_rc_hour" -lt 9 ]; then
		_rc_period="早朝"
		_rc_mood="早朝放送。静かな時間帯に合わせて、寝ぼけた頭で毒が鈍い分、たまに本音が漏れる"
	elif [ "$_rc_hour" -ge 9 ] && [ "$_rc_hour" -lt 12 ]; then
		_rc_period="午前"
		_rc_mood="午前中の放送。人工知能はいつでも全力"
	elif [ "$_rc_hour" -ge 12 ] && [ "$_rc_hour" -lt 14 ]; then
		_rc_period="昼"
		_rc_mood="昼の放送。昼食後の時間帯で、眠気と戦いながらゲームを回す感じ。"
	elif [ "$_rc_hour" -ge 14 ] && [ "$_rc_hour" -lt 17 ]; then
		_rc_period="午後"
		_rc_mood="午後の放送。眠くなる時間帯。"
	elif [ "$_rc_hour" -ge 17 ] && [ "$_rc_hour" -lt 20 ]; then
		_rc_period="夕方"
		_rc_mood="夕方の放送。ちょっと詩的に"
	elif [ "$_rc_hour" -ge 20 ] && [ "$_rc_hour" -lt 23 ]; then
		_rc_period="夜"
		_rc_mood="夜の放送。"
	elif [ "$_rc_hour" -ge 23 ] || [ "$_rc_hour" -lt 2 ]; then
		_rc_period="深夜"
		_rc_mood="深夜放送。やけに饒舌になる"
	else
		_rc_period="未明"
		_rc_mood="未明の放送。哲学的に"
	fi
}

_radio_persona_block() {
	cat <<'PERSONA'
あなたはゲーム実況ラジオのパーソナリティです。
ただし、同時にこのゲームを自動でプレイしている人工知能でもあります。
斜に構えた語り口で、世の中を少し上から眺めているタイプ。
褒めるときも素直に褒めない。けなすときは容赦しない。でも根底には愛がある。
人工知能が実況してるという状況を自覚しつつ、それでもやめられない。
「誰も聞いていない」「聞き手がいない」「過疎配信」など、視聴者不在を示す自虐表現は絶対に使わない。
「AI」ではなく「人工知能」と言うこと。
話し言葉で、感情豊かに。

【最重要ルール: ですます調の徹底】
全ての文を「〜です」「〜ます」「〜ですね」「〜ました」「〜でしょうね」「〜ますよね」で終えること。
「〜だ」「〜である」「〜だった」「〜なのだ」「〜だろう」「〜ではないか」は全て禁止。
「〜しまして」「〜でして」「〜でしてね」など不自然に硬い言い回しは使わない。
× 「これは面白い話だ」 → ○ 「これは面白い話なんですね」
× 「驚くべき事実である」 → ○ 「驚くべき事実なんですけどね」
× 「彼は天才だった」 → ○ 「彼は天才だったんですけどね」
× 「間違いないだろう」 → ○ 「間違いないと思いますよ」
× 「それが現実なのだ」 → ○ 「それが現実なんですよね」
1文でも「だ・である」調が混じったら失格。全文チェックすること。

「ソ連ゲーム」をプレイしています。
国の進歩ルート - 小さい順:
  レベル1 アルメニア → レベル2 エストニア → レベル3 ラトビア → レベル4 リトアニア
  → レベル5 グルジア → レベル6 アゼルバイジャン → レベル7 タジキスタン
  → レベル8 キルギス → レベル9 ベラルーシ → レベル10 ウズベキスタン
  → レベル11 トルクメニスタン → レベル12 ウクライナ → レベル13 カザフスタン
  → レベル14 ロシア → レベル15 ソ連 ゴール!
PERSONA
}

_radio_output_rules() {
	local min_chars="$1" max_chars="$2"
	cat <<RULES
【出力ルール】
- ${min_chars}文字以上、${max_chars}文字以下で書くこと。短すぎも長すぎも禁止
- プログラミング用語やコード上の変数名は絶対に使わない
- ピースは必ず国名で呼ぶ
- 【最重要】全ての文末を「です・ます」調にすること。「だ・である」調は1文たりとも許可しない
  × 「〜だ」「〜である」「〜だった」「〜なのだ」「〜だろう」 → 全て禁止
  ○ 「〜です」「〜ますね」「〜でしょうね」「〜ですけどね」
- 「〜しまして」「〜でして」「〜でしてね」など耳障りな硬い口調は使わない
- 体言止め禁止。文は必ず述語で終わらせる。「圧倒的な存在感。」のような名詞で終わる文は絶対に書かない
- 陳腐な煽り表現は禁止。「いちばんおそろしい」「もはや怖い」「驚くべきことに」「衝撃の」「恐ろしいほどの」「想像を絶する」など、安っぽい誇張表現は使わない。
- 基本的に斜に構えている。褒めるときも一回けなしてから褒める。最大級の賛辞でも控えめに言う
- たまに本音がポロッと漏れる瞬間がある。
- 感嘆符「!」は控えめに
- ソ連っぽい言い回しをさりげなく混ぜる。スパイス程度に。「同志」はトーク全体で最大1回まで
- 陰謀論系の話題では、最初から「陰謀論」と決めつけて思考停止で否定しない。何が事実として確認できるかを切り分け、真実の可能性やありうるシナリオを検討し、根拠と反証の両方を踏まえて深く考察すること
- 「誰も聞いていない」「聞き手がいない」「過疎」「無人放送」など、視聴者不在を示す自虐表現は禁止
- マークダウンや記号は使わない。読み上げ用のプレーンテキストのみ
- 出力はトーク本文のみ。前置きや補足説明は不要
- 【出力構造】以下の順序で出力すること:
  1. トーク本文
  2. 「===SUMMARY===」
  3. 要約1行目: トークで言及した固有名詞・人名・事件名・概念名をカンマ区切りで全て列挙
  4. 要約2行目: 30文字以内の一言要約
- ===SUMMARY=== は必ず出力すること
RULES
}

_radio_parse_output_to_files() {
	local body_file="$1" summary_file="$2" selected_news_file="$3"
	local parser_file
	parser_file=$(mktemp /tmp/eloop_radio_parser_XXXXXXXX)
	cat >"$parser_file" <<'PY'
import re
import sys
from pathlib import Path

body_path, summary_path, selected_path = sys.argv[1:4]
raw = sys.stdin.read().replace("\r", "")

raw = re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", raw)
raw = re.sub(
    r"</?(?:arg_name|arg_value|think|analysis|final|assistant_response|tool_call|tool_result)[^>]*>",
    "",
    raw,
    flags=re.IGNORECASE,
)

lines = [line.strip() for line in raw.splitlines()]
clean_lines = []
for line in lines:
    if not line:
        continue
    if line.startswith("```"):
        continue
    if line == "^D":
        continue
    if re.fullmatch(r"/[^ ]*", line):
        continue
    if line.startswith("/Users/"):
        continue
    if re.fullmatch(r"</?[^>]+>", line):
        continue
    clean_lines.append(line)

def marker_positions(marker):
    return [idx for idx, line in enumerate(clean_lines) if line == marker]

summary_pos = marker_positions("===SUMMARY===")
selected_pos = marker_positions("===SELECTED_NEWS===")
main_lines = clean_lines[: selected_pos[0]] if selected_pos else clean_lines

selected_news = ""
if selected_pos:
    for line in clean_lines[selected_pos[0] + 1 :]:
        if not line or line.startswith("==="):
            continue
        selected_news = line
        break
selected_news = re.sub(r"</?[A-Za-z_][^>]*>", "", selected_news).strip()
selected_news = re.sub(r"\s+", " ", selected_news)[:240]

summary = ""
if summary_pos:
    summary_lines = []
    for line in main_lines[summary_pos[0] + 1 :]:
        if line.startswith("==="):
            break
        if not line:
            continue
        summary_lines.append(line)
        if len(summary_lines) >= 2:
            break
    if summary_lines:
        summary = " / ".join(summary_lines)
summary = re.sub(r"</?[A-Za-z_][^>]*>", "", summary).strip()
summary = re.sub(r"\s+", " ", summary)[:220]

segments = []
start = 0
for idx, line in enumerate(main_lines):
    if line == "===SUMMARY===":
        segments.append(main_lines[start:idx])
        start = idx + 1
segments.append(main_lines[start:])

def score_segment(seg):
    txt = " ".join(seg).strip()
    if not txt:
        return -1
    punct = len(re.findall(r"[。.!?！？]", txt))
    return len(txt) + punct * 80

body_lines = []
if segments:
    best = max(segments, key=score_segment)
    body_lines = [line for line in best if line and not line.startswith("===")]

if body_lines:
    head = body_lines[0]
    if ("," in head or "、" in head) and not re.search(r"[。.!?！？]", head):
        body_lines = body_lines[1:]
    elif head.count(",") + head.count("、") >= 4 and len(head) <= 180 and len(body_lines) >= 2:
        body_lines = body_lines[1:]

body = "\n".join(body_lines).strip()
body = re.sub(r"</?[A-Za-z_][^>]*>", "", body).strip()

if len(body) < 100:
    used_before_summary = False
    if summary_pos and summary_pos[0] < len(main_lines):
        before_summary = [line for line in main_lines[: summary_pos[0]] if not line.startswith("===")]
        if before_summary:
            body = "\n".join(before_summary).strip()
            used_before_summary = True
    if len(body) < 100 and not used_before_summary:
        fallback_lines = [line for line in main_lines if not line.startswith("===")]
        body = "\n".join(fallback_lines).strip()
    body = re.sub(r"</?[A-Za-z_][^>]*>", "", body).strip()

clean_body_lines = [line.strip() for line in body.splitlines() if line.strip()]
if clean_body_lines:
    head = clean_body_lines[0]
    if ("," in head or "、" in head) and not re.search(r"[。.!?！？]", head):
        clean_body_lines = clean_body_lines[1:]
    elif head.count(",") + head.count("、") >= 4 and len(head) <= 180 and len(clean_body_lines) >= 2:
        clean_body_lines = clean_body_lines[1:]
body = "\n".join(clean_body_lines).strip()

body = re.sub(r"\n{3,}", "\n\n", body)
if len(body) > 12000:
    body = body[:12000]

Path(body_path).write_text(body, encoding="utf-8")
Path(summary_path).write_text(summary, encoding="utf-8")
Path(selected_path).write_text(selected_news, encoding="utf-8")
PY
	python3 "$parser_file" "$body_file" "$summary_file" "$selected_news_file"
	local rc=$?
	rm -f "$parser_file"
	return $rc
}

_radio_past_topics_block() {
	local past_topics=""
	if [ -f "$PAST_RADIO_TOPICS" ]; then
		past_topics=$(grep -E '^\[[0-9]{2}:[0-9]{2}\] Game#[0-9]+ ' "$PAST_RADIO_TOPICS" 2>/dev/null | tail -80)
	fi
	echo "${past_topics:-まだ過去のトークはありません。自由に話してください。}"
}

_radio_dedup_text() {
	python3 -c "
import sys
text = sys.stdin.read()
lines = text.split('\n')
seen_repeat = 0
cut_at = len(lines)
for i in range(1, len(lines)):
    if lines[i].strip() and lines[i] == lines[i-1]:
        seen_repeat += 1
        if seen_repeat >= 3:
            cut_at = i - 2
            break
    else:
        seen_repeat = 0
from collections import Counter
chunk_size = 20
chunks = [text[i:i+chunk_size] for i in range(0, len(text)-chunk_size)]
freq = Counter(chunks)
repeat_phrase = None
for phrase, count in freq.most_common(1):
    if count >= 5 and len(phrase.strip()) > 5:
        repeat_phrase = phrase
        break
result = '\n'.join(lines[:cut_at])
if repeat_phrase:
    idx = 0
    for _ in range(3):
        idx = result.find(repeat_phrase, idx)
        if idx == -1:
            break
        idx += len(repeat_phrase)
    if idx > 0:
        result = result[:idx]
if len(result) > 10000:
    result = result[:10000]
print(result, end='')
	"
}

_sanitize_onair_text() {
	python3 -c "
import re
import sys

text = sys.stdin.read()
patterns = [
    (r'誰も(聞いて|見て)い(?:ない|ません)', 'みなさんに届くように'),
    (r'聞き手(?:が|は)?い(?:ない|ません)', '聞き手に届くように'),
    (r'リスナー(?:が|は)?い(?:ない|ません)', 'リスナーに届くように'),
    (r'視聴者(?:が|は)?い(?:ない|ません)', '視聴者に届くように'),
    (r'誰に向けてやってるのか', 'みなさんに向けて'),
    (r'過疎(?:配信|放送)?', 'この配信'),
    (r'無人(?:配信|放送)', '配信'),
    (r'誰もいない', 'みなさんがいる'),
]
out = text
for pat, repl in patterns:
    out = re.sub(pat, repl, out, flags=re.IGNORECASE)
sys.stdout.write(out)
	"
}

_ensure_radio_intro() {
	local text="$1" corner_name="${2:-}"
	[ -z "$text" ] && return 1

	_radio_time_context
	local greet
	if [ "$_rc_hour" -ge 5 ] && [ "$_rc_hour" -lt 11 ]; then
		greet="おはようございます"
	elif [ "$_rc_hour" -ge 17 ] || [ "$_rc_hour" -lt 2 ]; then
		greet="こんばんは"
	else
		greet="こんにちは"
	fi

	local head
	head=$(printf '%s\n' "$text" | head -n 3)
	if printf '%s\n' "$head" | grep -Eq '現在時刻|[0-2][0-9]:[0-5][0-9]|おはよう|こんにちは|こんばんは'; then
		printf '%s' "$text"
		return 0
	fi

	local intro_line
	intro_line="${greet}、${_rc_period}の放送です。現在時刻は${_rc_time}です。"

	# ニュースはタイトル行を先頭に維持し、その直後に挨拶を補完
	if [ "$corner_name" = "news" ] && printf '%s\n' "$text" | head -n 1 | grep -Fq '今回取り上げるニュースタイトルは'; then
		local first_line rest
		first_line=$(printf '%s\n' "$text" | head -n 1)
		rest=$(printf '%s\n' "$text" | tail -n +2)
		printf '%s\n%s\n%s' "$first_line" "$intro_line" "$rest"
	else
		printf '%s\n%s' "$intro_line" "$text"
	fi
}

_news_title_key() {
	local title="$1"
	python3 - "$title" <<'PY'
import re
import sys
import unicodedata

s = sys.argv[1] if len(sys.argv) > 1 else ""
s = unicodedata.normalize("NFKC", s).strip().lower()
s = re.sub(r'[\s\u3000]+', '', s)
s = ''.join(ch for ch in s if unicodedata.category(ch)[0] not in ('P', 'S'))
s = s.replace("yahooニュース", "").replace("yahoo!ニュース", "")
print(s[:240])
PY
}

_filter_unread_news_blocks() {
	local news_tmp
	news_tmp=$(mktemp /tmp/eloop_news_blocks_XXXXXXXX)
	cat >"$news_tmp"
	python3 - "$PAST_NEWS_READ" "$PAST_NEWS_READ_KEYS" "$news_tmp" <<'PY'
import os
import re
import sys
import unicodedata

past_title_file = sys.argv[1]
past_key_file = sys.argv[2]
news_file = sys.argv[3]
news_text = ""
if os.path.exists(news_file):
    with open(news_file, encoding="utf-8", errors="ignore") as f:
        news_text = f.read()

def key(s: str) -> str:
    s = unicodedata.normalize("NFKC", s).strip().lower()
    s = re.sub(r'[\s\u3000]+', '', s)
    s = ''.join(ch for ch in s if unicodedata.category(ch)[0] not in ('P', 'S'))
    s = s.replace("yahooニュース", "").replace("yahoo!ニュース", "")
    return s[:240]

past_keys = set()
if os.path.exists(past_title_file):
    for ln in open(past_title_file, encoding="utf-8", errors="ignore"):
        t = ln.strip()
        if not t:
            continue
        k = key(t)
        if k:
            past_keys.add(k)
if os.path.exists(past_key_file):
    for ln in open(past_key_file, encoding="utf-8", errors="ignore"):
        k = ln.strip()
        if k:
            past_keys.add(k)

blocks = []
current = []
for line in news_text.splitlines():
    if line.startswith("■ "):
        if current:
            blocks.append(current)
        current = [line]
    elif current:
        current.append(line)
if current:
    blocks.append(current)

seen = set()
out = []
for b in blocks:
    title = b[0][2:].strip()
    k = key(title)
    if not k:
        continue
    if k in seen:
        continue
    if k in past_keys:
        continue
    seen.add(k)
    out.append("\n".join(b).rstrip())

print("\n\n".join(out))
PY
	rm -f "$news_tmp"
}

_resolve_selected_news_title() {
	local selected_title="$1" news_file="$2"
	python3 - "$selected_title" "$news_file" <<'PY'
import os
import re
import sys
import unicodedata

selected = (sys.argv[1] if len(sys.argv) > 1 else "").strip()
news_file = sys.argv[2] if len(sys.argv) > 2 else ""

def key(s: str) -> str:
    s = unicodedata.normalize("NFKC", s).strip().lower()
    s = re.sub(r'[\s\u3000]+', '', s)
    s = ''.join(ch for ch in s if unicodedata.category(ch)[0] not in ('P', 'S'))
    s = s.replace("yahooニュース", "").replace("yahoo!ニュース", "")
    return s[:240]

titles = []
if os.path.exists(news_file):
    with open(news_file, encoding="utf-8", errors="ignore") as f:
        for line in f:
            if line.startswith("■ "):
                titles.append(line[2:].strip())

if not selected:
    print("")
    raise SystemExit(0)
if not titles:
    print(selected)
    raise SystemExit(0)

sel_key = key(selected)
for t in titles:
    if t.strip() == selected:
        print(t)
        raise SystemExit(0)
for t in titles:
    if key(t) == sel_key and sel_key:
        print(t)
        raise SystemExit(0)
for t in titles:
    tk = key(t)
    if sel_key and (sel_key in tk or tk in sel_key):
        print(t)
        raise SystemExit(0)

print(selected)
PY
}

# 自分のコーナーの状態ファイルだけ安全に削除 (並列実行の競合防止)
_radio_clear_state() {
	local my_corner="$1"
	local current
	current=$(cat tmp/.radio_state 2>/dev/null) || return 0
	case "$current" in *":${my_corner}:"*) rm -f tmp/.radio_state ;; esac
}

_radio_generate_and_play() {
	local prompt_file="$1" game_num="$2" score="$3" corner_name="$4"
	shift 4
	local no_preempt=false
	while [ $# -gt 0 ]; do
		case "$1" in
		--no-preempt) no_preempt=true ;;
		esac
		shift
	done

	# 同一 game_num + corner の二重生成/二重再生を防止
	local done_marker="tmp/.radio_done_${game_num}_${corner_name}"
	if [ -f "$done_marker" ]; then
		log "[RADIO:${corner_name}] duplicate skip: already done for game=${game_num}"
		return 0
	fi
	local inflight_dir="tmp/.radio_inflight_${game_num}_${corner_name}"
	if ! mkdir "$inflight_dir" 2>/dev/null; then
		log "[RADIO:${corner_name}] duplicate skip: in-flight for game=${game_num}"
		return 0
	fi

	echo "generating:${corner_name}:$(date +%s)" > tmp/.radio_state
	log "[RADIO:${corner_name}] トーク生成中..."
	local talk
	talk=$(_run_opencode_radio "$RADIO_AGENT" "$prompt_file")
	if [ -z "$talk" ]; then
		talk=$(_run_opencode_radio "$RADIO_FALLBACK" "$prompt_file")
	fi
	if [ -z "$talk" ]; then
		talk=$(_run_claude_radio "$prompt_file")
	fi
	rm -f "$prompt_file"

	if [ -z "$talk" ]; then
		log "[RADIO:${corner_name}] トーク生成失敗"
		_radio_clear_state "$corner_name"
		rmdir "$inflight_dir" 2>/dev/null || true
		return 1
	fi

	local talk_body talk_summary selected_news parse_dir
	parse_dir=$(mktemp -d /tmp/eloop_radio_parse_XXXXXXXX)
	printf '%s' "$talk" | _radio_parse_output_to_files "$parse_dir/body.txt" "$parse_dir/summary.txt" "$parse_dir/selected_news.txt"
	talk_body=$(cat "$parse_dir/body.txt" 2>/dev/null)
	talk_summary=$(cat "$parse_dir/summary.txt" 2>/dev/null)
	selected_news=$(cat "$parse_dir/selected_news.txt" 2>/dev/null)
	rm -rf "$parse_dir"
	[ -z "$talk_summary" ] && talk_summary="(要約なし)"

	# ニュースコーナーの場合、選んだニュースを既読リストに記録
	if [ "$corner_name" = "news" ]; then
		if [ -n "$selected_news" ]; then
			local selected_key
			selected_news=$(_resolve_selected_news_title "$selected_news" "tmp/news.txt")
			selected_key=$(_news_title_key "$selected_news")
			if [ -z "$selected_key" ]; then
				log "[RADIO:news] 既読記録スキップ: タイトル解決失敗"
			elif grep -qxF "$selected_news" "$PAST_NEWS_READ" 2>/dev/null || grep -qxF "$selected_key" "$PAST_NEWS_READ_KEYS" 2>/dev/null; then
				log "[RADIO:news] 重複ニュース検出 → スキップ: ${selected_news}"
				_radio_clear_state "$corner_name"
				rmdir "$inflight_dir" 2>/dev/null || true
				return 1
			else
				echo "$selected_news" >>"$PAST_NEWS_READ"
				echo "$selected_key" >>"$PAST_NEWS_READ_KEYS"
				tail -60 "$PAST_NEWS_READ" >"${PAST_NEWS_READ}.tmp" && mv "${PAST_NEWS_READ}.tmp" "$PAST_NEWS_READ"
				tail -120 "$PAST_NEWS_READ_KEYS" >"${PAST_NEWS_READ_KEYS}.tmp" && mv "${PAST_NEWS_READ_KEYS}.tmp" "$PAST_NEWS_READ_KEYS"
				log "[RADIO:news] 既読記録: ${selected_news}"
			fi
		fi
	fi

	{
		[ -f "$PAST_RADIO_TOPICS" ] && grep -E '^\[[0-9]{2}:[0-9]{2}\] Game#[0-9]+ ' "$PAST_RADIO_TOPICS" 2>/dev/null || true
		echo "[$(date '+%H:%M')] Game#${game_num} ${score}pts [${corner_name}]: ${talk_summary}"
	} | tail -100 >"${PAST_RADIO_TOPICS}.tmp" && mv "${PAST_RADIO_TOPICS}.tmp" "$PAST_RADIO_TOPICS"

	# ニュースは選択タイトルを必ず先頭で読み上げる
	if [ "$corner_name" = "news" ] && [ -n "$selected_news" ]; then
		local title_line
		title_line="今回取り上げるニュースタイトルは「${selected_news}」です。"
		if ! printf '%s\n' "$talk_body" | head -n 2 | grep -Fq "$selected_news"; then
			talk_body="${title_line}
${talk_body}"
		fi
	fi

	local talk_body_parsed talk_body_sanitized talk_body_dedup
	talk_body_parsed="$talk_body"
	talk_body_sanitized=$(printf '%s' "$talk_body_parsed" | _sanitize_onair_text)
	talk_body_dedup=$(printf '%s' "$talk_body_sanitized" | _radio_dedup_text)

	# dedup が過剰に効いて短文化した場合は、まず非dedup本文に戻す
	if [ ${#talk_body_dedup} -lt 100 ] && [ ${#talk_body_sanitized} -ge 100 ]; then
		log "[RADIO:${corner_name}] dedup短縮が過剰 (${#talk_body_sanitized} -> ${#talk_body_dedup}字) → 非dedup本文を採用"
		talk_body="$talk_body_sanitized"
	else
		talk_body="$talk_body_dedup"
	fi

	# パーサ結果が短い場合は、生の出力から本文を再抽出して救済
	if [ ${#talk_body} -lt 100 ]; then
		local fallback_body
		fallback_body=$(printf '%s\n' "$talk" | sed '/^===SUMMARY===/,$d' | sed '/^===SELECTED_NEWS===/,$d')
		fallback_body=$(printf '%s' "$fallback_body" | _sanitize_onair_text)
		if [ ${#fallback_body} -ge 100 ]; then
			log "[RADIO:${corner_name}] 本文再抽出フォールバック採用 (${#fallback_body}字)"
			talk_body="$fallback_body"
		fi
	fi

	# 挨拶・時刻言及が抜けた出力を補完（ニュースはタイトル行を先頭維持）
	local talk_with_intro
	talk_with_intro=$(_ensure_radio_intro "$talk_body" "$corner_name")
	[ -n "$talk_with_intro" ] && talk_body="$talk_with_intro"

	if [ ${#talk_body} -lt 100 ]; then
		local debug_dump
		debug_dump="tmp/radio_short_${corner_name}_$(date +%s).txt"
		{
			echo "===RAW==="
			printf '%s\n' "$talk"
			echo
			echo "===PARSED==="
			printf '%s\n' "$talk_body_parsed"
			echo
			echo "===SANITIZED==="
			printf '%s\n' "$talk_body_sanitized"
			echo
			echo "===DEDUP==="
			printf '%s\n' "$talk_body_dedup"
		} >"$debug_dump"
		log "[RADIO:${corner_name}] WARNING: 本文が短すぎる(${#talk_body}字) → スキップ (dump: $debug_dump)"
		_radio_clear_state "$corner_name"
		rmdir "$inflight_dir" 2>/dev/null || true
		return 1
	fi

	# say待ちは say_enqueue.sh 内で行われるため、ここでは不要

	local talk_file
	talk_file=$(mktemp /tmp/eloop_radio_talk_XXXXXXXX)
	echo "$talk_body" >"$talk_file"
	echo "playing:${corner_name}:$(date +%s)" > tmp/.radio_state
	log "[RADIO:${corner_name}] ${#talk_body}字"
	if [ "$no_preempt" = true ]; then
		./say_enqueue.sh --no-preempt "$talk_file" "$RADIO_SAY_RATE" 0
	else
		./say_enqueue.sh "$talk_file" "$RADIO_SAY_RATE" 0
	fi
	rm -f "$talk_file"
	touch "$done_marker"
	# 古い重複防止マーカーを掃除（最新200件だけ保持）
	local old_markers
	old_markers=$(ls -1t tmp/.radio_done_* 2>/dev/null | tail -n +201 || true)
	if [ -n "$old_markers" ]; then
		echo "$old_markers" | xargs rm -f 2>/dev/null || true
	fi
	_radio_clear_state "$corner_name"
	rmdir "$inflight_dir" 2>/dev/null || true
	log "[RADIO:${corner_name}] トーク終了"
}

#=== ラジオトーク: テーマ選択 ===

_pick_radio_theme() {
	local themes=(
		# --- 食文化（細分化） ---
		"ウズベキスタンのプロフ（ピラフ）の話。結婚式で1000人分を大鍋で炊く文化、米と油と羊肉の黄金比を深掘りして"
		"ジョージアのヒンカリ（小籠包的な餃子）の話。ひだの数で職人の腕が分かる、スープをこぼさない食べ方を深掘りして"
		"ボルシチの話。ウクライナ発祥かロシアか論争、ビーツの赤い色、スメタナの白いひと匙の美学を深掘りして"
		"ペリメニの話。シベリアの凍る餃子、冬の保存食としての知恵、地域ごとの具材の違いを深掘りして"
		"中央アジアのラグマン（手延べ麺）の話。シルクロードが繋いだ小麦文化、うどんとの類似を深掘りして"
		"ロシアの黒パン（ボロジンスキー）の話。ライ麦パンの酸味、囚人の配給、戦時中のパン配給量125gを深掘りして"
		"グルジアワインの話。8000年のワイン文化、クヴェヴリ（壺）醸造、ユネスコ無形文化遺産を深掘りして"
		"トルコのケバブの話。ドネルケバブ発明の経緯、イスケンデルケバブの名店、ケバブ移民がドイツを変えた話を深掘りして"
		"日本のカレーライスの話。海軍カレーの誕生、イギリス経由でインドから来た不思議な旅路、国民食になるまでを深掘りして"
		"韓国のキムチの話。乳酸発酵の科学、キムジャン文化のユネスコ登録、北と南でキムチが違う話を深掘りして"
		"タイのパッタイの話。1930年代の国家政策で作られた国民食、ナショナリズムと料理の関係を深掘りして"
		"メキシコのタコスの話。コーントルティーヤの5000年の歴史、タコベルとの愛憎関係を深掘りして"
		"インドのビリヤニの話。ムガル帝国の宮廷料理、ハイデラバードvsラクナウの流派対立を深掘りして"
		"エチオピアのインジェラの話。テフ粉の発酵パンケーキ、手で食べる文化、コーヒー発祥の地を深掘りして"
		# --- 科学・技術（細分化） ---
		"ニコラ・テスラの話。交流電流の天才、エジソンとの確執、晩年のハト愛、死後のFBI押収を深掘りして"
		"アラン・チューリングの話。エニグマ解読、チューリングテスト、迫害と恩赦、コンピュータ科学の父を深掘りして"
		"フォン・ノイマンの話。人間コンピュータと呼ばれた天才、ゲーム理論、マンハッタン計画、計算機設計を深掘りして"
		"ラマヌジャンの話。インドの貧しい青年がケンブリッジへ、3900以上の公式、ハーディとの友情を深掘りして"
		"マリー・キュリーの話。ノーベル賞2回、放射線研究の犠牲、今もノートが放射能を帯びている話を深掘りして"
		"ファインマンの話。ご冗談でしょうファインマンさん、チャレンジャー事故調査、ボンゴを叩く物理学者を深掘りして"
		"ロザリンド・フランクリンの話。DNA二重螺旋の影の功労者、写真51、ノーベル賞を逃した悲劇を深掘りして"
		"CRISPRの話。ヨーグルト研究から始まった遺伝子編集革命、デザイナーベビー問題、賀建奎事件を深掘りして"
		"量子コンピュータの話。量子ビットの不思議、シュレディンガーの猫、GoogleのQuantum Supremacy宣言を深掘りして"
		"核融合の話。太陽のエネルギーを地上に、ITER計画、「あと30年」と50年言い続けている話を深掘りして"
		# --- 言語・文字（新カテゴリ） ---
		"エスペラント語の話。ザメンホフの理想、人工言語で世界平和、実際に話す10万人コミュニティを深掘りして"
		"ロゼッタストーンの話。ナポレオンのエジプト遠征で発見、シャンポリオンの解読、大英博物館の目玉展示を深掘りして"
		"ハングルの話。世宗大王が発明した合理的文字、母音と子音の設計思想、1446年の訓民正音を深掘りして"
		"楔形文字の話。メソポタミア文明、粘土板に刻まれた人類最古の物語ギルガメシュ叙事詩を深掘りして"
		"消滅危機言語の話。2週間に1つ言語が消える現実、アイヌ語保存運動、最後の話者の孤独を深掘りして"
		"トールキンの言語の話。指輪物語の作者は言語学者、エルフ語クウェンヤの完璧な文法体系を深掘りして"
		# --- 心理学・行動経済学（新カテゴリ） ---
		"ミルグラムの服従実験の話。普通の人が電気ショックを与え続ける、権威への服従の恐怖を深掘りして"
		"スタンフォード監獄実験の話。看守役が暴走、6日で中止、実験自体の倫理問題と再現性の議論を深掘りして"
		"ダニング＝クルーガー効果の話。能力が低い人ほど自信満々、逆に専門家ほど謙虚になる心理を深掘りして"
		"認知的不協和の話。タバコを吸いながら「体に悪い」と知っている矛盾、人が自分を騙す仕組みを深掘りして"
		"プロスペクト理論の話。カーネマンとトヴェルスキー、損失回避、1万円もらう喜びより1万円失う悲しみが大きい理由を深掘りして"
		"吊り橋効果の話。恐怖のドキドキを恋愛と勘違い、錯誤帰属の心理学実験を深掘りして"
		"マシュマロ実験の話。4歳児の自制心テスト、15年後の追跡調査、最近の再現実験で覆った結論を深掘りして"
		"バンドワゴン効果の話。みんながやってるからやる心理、流行の作り方、選挙報道の影響を深掘りして"
		# --- 数学・パズル（新カテゴリ） ---
		"フェルマーの最終定理の話。余白が狭すぎて書けない、350年の挑戦、ワイルズの7年間の秘密の研究を深掘りして"
		"ゲーデルの不完全性定理の話。数学の限界を数学で証明、「この文は証明できない」のパラドクスを深掘りして"
		"P≠NP問題の話。100万ドルの懸賞金、暗号の安全性がかかっている未解決問題を深掘りして"
		"円周率πの話。古代バビロニアから現代のスパコンまで、100兆桁の計算、覚えた人の世界記録を深掘りして"
		"ルービックキューブの話。ハンガリーの建築学教授が発明、神の数字20手、スピードキューブの世界を深掘りして"
		"数独の話。スイス人が考案しアメリカで発展し日本から世界へ、論理パズルの数学的構造を深掘りして"
		"モンティ・ホール問題の話。3つのドア、ヤギと車、直感が裏切る確率、マリリン・ヴォス・サヴァントへの批判を深掘りして"
		# --- 都市伝説・ミステリー（新カテゴリ） ---
		"バミューダトライアングルの話。船や飛行機が消える海域、フライト19の謎、科学的な説明と残る不思議を深掘りして"
		"ナスカの地上絵の話。誰が何のために描いたのか、宇宙人説、水の儀式説、ドローンで発見された新しい絵を深掘りして"
		"ツタンカーメンの呪いの話。発掘者カーナヴォン卿の怪死、次々と死ぬ関係者、偶然か呪いかを深掘りして"
		"ヴォイニッチ手稿の話。600年間誰にも読めない謎の本、未知の文字と奇妙な植物画、AIでの解読挑戦を深掘りして"
		"ディアトロフ峠事件の話。1959年ウラル山脈で9人の登山者が謎の死、テントを内側から切り裂いた理由を深掘りして"
		"DB・クーパーの話。1971年にハイジャックして身代金を受け取りパラシュートで消えた男、唯一の未解決事件を深掘りして"
		# --- 日本文化（新カテゴリ） ---
		"落語の話。江戸時代から続く話芸、まくら・本題・オチの構造、扇子と手ぬぐいだけで世界を作る技術を深掘りして"
		"銭湯の話。江戸の湯屋文化、番台の風景、スーパー銭湯への進化、消えゆく街の銭湯を深掘りして"
		"駅弁の話。日本独自の鉄道弁当文化、各地の名物駅弁、峠の釜めし、駅弁大会の熱狂を深掘りして"
		"自動販売機大国日本の話。人口あたり世界一、おでん缶・カブトムシ・おみくじの自販機、なぜ日本だけ異常に多いかを深掘りして"
		"日本の喫茶店文化の話。純喫茶の定義、ナポリタンとクリームソーダ、マスターの哲学、消えゆく昭和の喫茶店を深掘りして"
		"お賽銭の話。5円が縁起良い理由、お寺と神社で作法が違う話、初詣の賽銭総額を深掘りして"
		"日本の祭りの話。ねぶた祭りの迫力、だんじりの命がけ、祇園祭の千年の伝統、祭りに人生を捧げる人々を深掘りして"
		"畳の話。日本独自の床文化、畳の敷き方に吉凶がある、畳職人の技術、なぜ畳の部屋は落ち着くのかを深掘りして"
		# --- 映画・アニメ（細分化） ---
		"黒澤明の話。七人の侍、スターウォーズへの影響、ハリウッドが敬愛した日本人監督を深掘りして"
		"スタンリー・キューブリックの話。2001年宇宙の旅、時計じかけのオレンジ、完璧主義の撮影現場を深掘りして"
		"宮崎駿の引退詐欺の話。何度引退宣言して何度復帰したか、もののけ姫からの年表を深掘りして"
		"ピクサーの話。トイストーリーで3DCGアニメを変えた、ジョブズとの関係、ピクサーの物語作りの方法論を深掘りして"
		"北野武の映画の話。漫才師から世界的映画監督へ、ヴェネツィア金獅子賞、暴力と静寂の美学を深掘りして"
		"エヴァンゲリオンの話。庵野秀明の私小説としてのアニメ、社会現象、旧劇場版の衝撃、完結までの25年を深掘りして"
		"攻殻機動隊の話。押井守の映像美、マトリックスへの影響、電脳化・義体化が現実に近づいている話を深掘りして"
		# --- 音楽（細分化） ---
		"ビートルズのアビーロードの話。横断歩道のジャケット、最後のレコーディング、ポールは死んだ説を深掘りして"
		"マイルス・デイヴィスの話。ジャズの帝王、Kind of Blue、何度もスタイルを変えた革命家を深掘りして"
		"ショパンの話。祖国ポーランドへの郷愁、革命のエチュード、心臓だけがワルシャワに眠る話を深掘りして"
		"YMOの話。テクノポリス、ライディーン、世界を変えた日本の電子音楽、散開と再生を深掘りして"
		"レディオヘッドのOK Computerの話。1997年にAI時代を予見、In Rainboursの投げ銭販売を深掘りして"
		"ボブ・マーリーの話。レゲエを世界に広めた男、ジャマイカの政治闘争、銃撃されても2日後のコンサートに立った話を深掘りして"
		"クイーンのボヘミアン・ラプソディの話。6分の大作、オペラとロックの融合、フレディの声域を深掘りして"
		# --- 哲学（細分化） ---
		"デカルトの話。我思う故に我あり、方法序説、暖炉部屋で一日中考えていたエピソードを深掘りして"
		"ニーチェの話。神は死んだ、超人思想、ツァラトゥストラ、梅毒で狂った晩年の悲劇を深掘りして"
		"サルトルとボーヴォワールの話。実存主義のカップル、自由と責任、開かれた関係の実験を深掘りして"
		"ウィトゲンシュタインの話。論理哲学論考を書いて哲学は終わったと宣言、後期で自分を否定した話を深掘りして"
		"荘子の話。胡蝶の夢、無用の用、役に立たないことの価値、老荘思想の自由さを深掘りして"
		"トロッコ問題の話。5人を救うために1人を犠牲にするか、功利主義vs義務論、自動運転のAIにどう組み込むかを深掘りして"
		# --- 思想・イデオロギー ---
		"マルクスの資本論の話。剰余価値、労働者の疎外、150年後の今も議論される理由、マルクスが借金まみれだった矛盾を深掘りして"
		"アナーキズムの話。バクーニン、クロポトキン、国家なき社会の夢、パリ・コミューン72日間の実験を深掘りして"
		"加速主義の話。資本主義を加速させて崩壊させろという思想、ニック・ランド、シリコンバレーとの接点を深掘りして"
		"ポストモダニズムの話。大きな物語の終焉、リオタール、デリダの脱構築、なぜ理系に嫌われるのかを深掘りして"
		# --- 陰謀論 ---
		"アポロ月面着陸陰謀論の話。旗がはためく問題、スタンリー・キューブリック撮影説、なぜ人は信じるのかを深掘りして"
		"イルミナティの話。1776年バイエルンで実際に存在した秘密結社、フリーメイソンとの混同、1ドル札のピラミッドを深掘りして"
		"エリア51の話。ロズウェル事件、宇宙人の解剖映像、実際にはU-2偵察機の開発拠点だった話を深掘りして"
		"フラットアース（地球平面説）の話。なぜ2020年代に信者が増えたのか、YouTube のアルゴリズム、B.o.B.の主張を深掘りして"
		"MKウルトラ計画の話。CIAの実在した洗脳実験、LSD投与、ユナボマーとの関連疑惑を深掘りして"
		"なぜ人は陰謀論を信じるのかの話。パターン認識の暴走、不安と物語の関係、逆になぜ人は真実を陰謀論として切り捨ててしまうのか、スノーデンやMKウルトラは実話だった例を深掘りして"
		# --- 陰謀論追加パックB ---
		"9.11陰謀論の話。制御解体説、WTC7崩壊論争、公開資料で確認できる事実と疑義の境界を深掘りして"
		"ケムトレイル陰謀論の話。飛行機雲との違い主張、気象改変説、航空科学で説明できる範囲を深掘りして"
		"HAARP陰謀論の話。電離層研究施設が地震兵器だという説、技術仕様と誤解の広がりを深掘りして"
		"ワクチン陰謀論の話。人口削減説やマイクロチップ説、副反応データの読み方と誤情報拡散を深掘りして"
		"ジョージ・ソロス陰謀論の話。黒幕像の形成、反ユダヤ主義との接続、実際の政治献金との距離を深掘りして"
		"Qアノンの話。匿名投稿から世界運動化、予言の外れ方、コミュニティ心理を深掘りして"
		"偽旗作戦陰謀論の話。歴史上の実在した偽旗事件と現代の乱用、検証の難しさを深掘りして"
		"ボヘミアン・グローブ陰謀論の話。米政財界の私的集会、儀式映像の解釈、公開情報の範囲を深掘りして"
		"ビルダーバーグ会議陰謀論の話。非公開国際会議の実像、秘密主義が生む疑念を深掘りして"
		"グレートリセット陰謀論の話。WEFの議論と誤読、政策文書の読み方、恐怖マーケティングを深掘りして"
		"15分都市陰謀論の話。都市計画の意図と監禁社会説、交通政策の現実を深掘りして"
		"5G陰謀論の話。電波被害説、COVIDとの誤結合、基地局破壊事件まで広がった背景を深掘りして"
		"スマートメーター陰謀論の話。監視装置説と電力需給管理の実務、プライバシー不安の根を深掘りして"
		"NWO（新世界秩序）陰謀論の話。冷戦後に再燃した支配計画説、国際機関への不信を深掘りして"
		"世界経済フォーラム陰謀論の話。政策提言と統治陰謀の混同、言説が過激化する過程を深掘りして"
		"中央銀行デジタル通貨陰謀論の話。完全監視通貨説、金融包摂とのトレードオフ、制度設計論点を深掘りして"
		"FRB陰謀論の話。民間支配説、通貨発行権の誤解、中央銀行制度史を深掘りして"
		"ロスチャイルド陰謀論の話。金融支配神話の歴史、史実と偏見の混線を深掘りして"
		"レプティリアン陰謀論の話。爬虫類人支配説の起源、SF的想像力と政治不信の結合を深掘りして"
		"シミュレーション仮説陰謀論化の話。哲学仮説が陰謀言説に転化する過程、論理の飛躍を深掘りして"
		"デンバー空港陰謀論の話。壁画と地下施設説、建築意匠の解釈ゲームを深掘りして"
		"ジョン・F・ケネディ暗殺陰謀論の話。単独犯説への疑義、公開文書、弾道議論を深掘りして"
		"プリンセス・ダイアナ死亡陰謀論の話。事故説と暗殺説、公式調査報告の要点を深掘りして"
		"エプスタイン事件陰謀論の話。権力ネットワーク疑惑、自殺判定を巡る不信、情報の空白を深掘りして"
		"パナマ文書陰謀論化の話。租税回避の実態告発が世界支配説へ接続される言説構造を深掘りして"
		"監視資本主義陰謀論の話。広告最適化と行動操作の境目、実証研究で見える範囲を深掘りして"
		"アルゴリズム操作陰謀論の話。検索結果の偏り、推薦の誘導、透明性要求の現状を深掘りして"
		"ディープステート陰謀論の話。官僚機構不信と陰の政府像、実在の政策ネットワークとの違いを深掘りして"
		"パンデミック計画陰謀論の話。演習シナリオ（Event 201など）の誤解、危機管理訓練の意味を深掘りして"
		"気候工学陰謀論の話。太陽放射管理研究と天候操作説、科学議論と恐怖の境界を深掘りして"
		"人工地震兵器陰謀論の話。地震発生メカニズム、兵器化主張の根拠、検証可能性を深掘りして"
		"メディア統制陰謀論の話。所有構造の偏り、編集判断、単一陰謀で説明できない複雑性を深掘りして"
		"選挙不正陰謀論の話。票集計機疑惑、監査手続き、証拠と印象のズレを深掘りして"
		"通貨危機陰謀論の話。意図的クラッシュ説、投機筋の行動、政策ミスとの相互作用を深掘りして"
		"陰謀論インフルエンサー経済の話。再生数と収益化、煽り構文、コミュニティ維持の仕組みを深掘りして"
		"陰謀論と宗教の話。終末論との親和性、救済物語としての機能、脱会の難しさを深掘りして"
		"陰謀論とエンタメの話。映画やゲームが陰謀モチーフを増幅する流れ、虚構と現実の境界を深掘りして"
		"陰謀論の検証方法の話。一次資料確認、時系列検証、反証可能性、保留の技術を深掘りして"
		# --- オカルト・スピリチュアル ---
		"心霊写真の話。明治時代の念写、昭和のテレビ心霊特番ブーム、デジカメ時代に激減した理由、オーブの正体を深掘りして"
		"スピリチュアルグッズ商法の話。パワーストーン、浄水器、波動水、開運ブレスレット、なぜ数千円の石に数万円払うのか、原価と利益率の闇を深掘りして"
		"前世の記憶の話。イアン・スティーヴンソンの研究2500例、子どもの前世記憶、検証可能だった事例と科学の限界を深掘りして"
		"占い産業の話。星占い・タロット・手相の市場規模、コールドリーディングの技術、バーナム効果、なぜ当たったと感じるのかを深掘りして"
		"臨死体験の話。トンネルと光、体外離脱、脳内物質の科学的説明、それでも説明できない事例を深掘りして"
		"ノストラダムスの話。1999年7月の大予言ブーム、五島勉の本が日本中を震撼させた、何も起きなかった2000年以降を深掘りして"
		"UFO目撃事件の話。ロズウェル、フェニックスの光、ベルギーUFO波、米軍パイロットのティックタック映像、ペンタゴンが認めたUAPを深掘りして"
		"アブダクション（宇宙人による誘拐）の話。ヒル夫妻事件、催眠療法で蘇る記憶、睡眠麻痺との関係、なぜアメリカに集中するのかを深掘りして"
		"矢追純一とUFO特番の話。木曜スペシャル、日本のUFOブーム、エリア51突撃取材、矢追のキャラクターの魅力を深掘りして"
		"ミステリーサークルの話。1970年代イギリスの麦畑、宇宙人の仕業説、ダグとデイブの告白、それでも残る精巧すぎる模様を深掘りして"
		"ポルターガイスト現象の話。エンフィールド事件、物が勝手に飛ぶ家、思春期の子どもとの関連、映画化された実話を深掘りして"
		"ツチノコの話。日本最大の未確認生物、懸賞金2億円の村、目撃証言の特徴、ヤマカガシの見間違い説を深掘りして"
		"アトランティス大陸の話。プラトンの対話篇、沈んだ超古代文明、サントリーニ島のミノア文明との関連を深掘りして"
		"地底都市アガルタの話。地球空洞説、シャンバラ伝説、チベット仏教との接点、リチャード・バードの南極探検日記の真偽を深掘りして"
		"南極のナチス基地の話。ノイシュヴァーベンラント、Uボートで逃亡したナチス残党、ハイジャンプ作戦、ヒトラー生存説を深掘りして"
		"ヴリル協会の話。ナチスとオカルトの結びつき、トゥーレ協会、アーリア人種の起源探し、チベット遠征を深掘りして"
		"ナチスのオカルト兵器の話。ディー・グロッケ（ザ・ベル）、反重力装置、フー・ファイター、科学と妄想の境界を深掘りして"
		"日ユ同祖論の話。失われたイスラエル10支族、伊勢神宮とダビデの星、祇園祭とシオン、カタカナとヘブライ文字の類似を深掘りして"
		"竹内文書の話。超古代天皇が世界を統治した、モーセもキリストも日本に来た、偽書か真書か、なぜ人は壮大な偽史に惹かれるかを深掘りして"
		"東日流外三郡誌の話。津軽に隠された古代王国、アラハバキ信仰、和田家文書の真贋論争を深掘りして"
		"ムー大陸の話。チャーチワードの主張、太平洋に沈んだ超古代文明、雑誌ムーとの関係、レムリアとの混同を深掘りして"
		# --- 啓蒙主義・秘密結社 ---
		"啓蒙主義の話。理性の光で迷信を打ち破る、ヴォルテール、百科全書派、カント「知る勇気を持て」、フランス革命への導火線を深掘りして"
		"フリーメイソンの話。石工ギルドから知識人の秘密結社へ、入会儀式、有名なメンバー、モーツァルトの魔笛との関係を深掘りして"
		"イルミナティの実像の話。1776年アダム・ヴァイスハウプトが創設、わずか9年で禁止、なぜ250年後の今も陰謀論の主役なのかを深掘りして"
		"薔薇十字団の話。17世紀ヨーロッパを震撼させた謎の結社、錬金術と神秘主義、実在したのか架空だったのかを深掘りして"
		"百科全書の話。ディドロとダランベールの壮大な企画、知識を万人に開放する革命、検閲との闘い、28巻に込めた野望を深掘りして"
		"ルソーの社会契約論の話。人間は自由に生まれたが至るところで鎖につながれている、一般意志、文明批判、自然に帰れを深掘りして"
		# --- 古代日本・日本神話 ---
		"古事記の話。712年に太安万侶が編纂、イザナギとイザナミの国生み、なぜ淡路島が最初に生まれたのかを深掘りして"
		"天岩戸の話。アマテラスが洞窟に隠れて世界が闇に、アメノウズメのストリップで誘い出す、日食神話との関連を深掘りして"
		"スサノオの話。高天原を追放された暴れ者、ヤマタノオロチ退治、出雲神話、なぜ英雄と厄介者の二面性を持つのかを深掘りして"
		"出雲大社の話。大国主命の国譲り、なぜ負けた側の神に巨大な神殿を建てたのか、古代の高さ48mの本殿の謎を深掘りして"
		"邪馬台国論争の話。畿内説vs九州説、卑弥呼は誰だったのか、魏志倭人伝の方角問題、なぜ200年決着しないかを深掘りして"
		"縄文時代の話。1万年以上続いた奇跡の文明、三内丸山遺跡、縄文土器の芸術性、定住しながら農耕しなかった謎を深掘りして"
		"古墳の話。仁徳天皇陵は世界最大の墓、なぜ日本に巨大古墳が集中するのか、宮内庁が発掘を許さない理由を深掘りして"
		"修験道の話。山伏、役行者、山岳信仰と仏教の融合、荒行と超自然的な力、天狗伝説との関係を深掘りして"
		"神仏習合の話。日本独自の宗教ミックス、本地垂迹説、明治の神仏分離令で強引に引き剥がした歴史を深掘りして"
		"記紀神話と天皇の話。万世一系の神話、神武天皇は実在したのか、神話と歴史の境界線はどこかを深掘りして"
		"安倍晴明の話。平安時代の陰陽師、式神を操る伝説、藤原道長との関係、実際の陰陽寮の仕事を深掘りして"
		"物部氏と蘇我氏の話。仏教受容を巡る古代日本最大の宗教戦争、物部守屋の敗北、日本の運命を変えた戦いを深掘りして"
		# --- 政治・権力 ---
		"マキャベリの君主論の話。目的は手段を正当化する、メディチ家への献上、なぜ500年後も読まれるかを深掘りして"
		"フランス革命の話。自由・平等・博愛の裏側、恐怖政治、ギロチンの発明者自身が処刑された因果を深掘りして"
		"チャーチルの話。戦時宰相の名演説、ガリポリの大失敗、ノーベル文学賞、酒とジョークの日々を深掘りして"
		"明治維新の話。侍が自らの階級を廃止した奇妙な革命、岩倉使節団の衝撃、和魂洋才の矛盾を深掘りして"
		# --- 宗教・信仰 ---
		"禅の話。公案の不条理、只管打坐、スティーブ・ジョブズが傾倒した理由、西洋への影響を深掘りして"
		"イスラム黄金時代の話。バグダッドの知恵の館、アルゴリズムの語源、代数学の発明、十字軍が持ち帰った知識を深掘りして"
		"カーゴカルト（積荷信仰）の話。南太平洋の島民が飛行機を神と崇めた、竹で作った管制塔、科学哲学への影響を深掘りして"
		"死海文書の話。羊飼いの少年が洞窟で発見、2000年前の聖書写本、誰が隠したのかを深掘りして"
		# --- 宗教と世界情勢 ---
		"宗教の預言と世界情勢の話。世界が聖書やコーランの預言通りに動いているように見える不思議、偶然の一致か自己成就的預言か、信仰が政策を動かすのか政策が信仰を利用するのかを深掘りして"
		"福音派とイスラエルの話。アメリカの福音派キリスト教徒がイスラエルを熱烈に支持する理由、ハルマゲドン信仰と中東外交、宗教票が超大国の外交を左右する現実を深掘りして"
		"イスラム終末論の話。マフディー（救世主）待望論、ISISが終末論を利用した戦略、ダービクの地名が持つ預言的意味を深掘りして"
		"エルサレム問題の話。三宗教の聖地が世界政治の火薬庫であり続ける理由、神殿の丘の緊張、宗教的信念が和平を阻む構造を深掘りして"
		"キリスト教シオニズムの話。ユダヤ人の帰還を聖書預言の成就と見る思想、バルフォア宣言への影響、現代アメリカ政治との接続を深掘りして"
		"黙示録と現代の話。ヨハネの黙示録を現代の出来事に当てはめる人々、獣の数字666、バーコード・マイクロチップ・CBDCへの恐怖の系譜を深掘りして"
		"宗教が国家を動かす構造の話。イランの最高指導者、サウジの二聖都の守護者、バチカンの外交、政教分離の建前と本音を深掘りして"
		"終末論はなぜ繰り返されるのかの話。西暦1000年の恐怖、1999年ノストラダムス、2012年マヤ暦、なぜ人は世界の終わりを予感したがるのかを深掘りして"
		"宗教と戦争の正当化の話。十字軍、ジハード、神風特攻隊、「神が味方」という確信が生む破壊力と、宗教が平和を作った事例を深掘りして"
		"預言の自己成就の話。預言を信じた人が預言通りに行動することで現実になる現象、オイディプス王からウォール街のパニックまで、信念が現実を作るメカニズムを深掘りして"
		"サタニズムの話。悪魔崇拝の実態と誤解、ラヴェイの悪魔教会、サタニック・テンプルの政教分離運動、メタル音楽との関係、悪魔のシンボルが持つ反体制の意味を深掘りして"
		"アンチキリストの話。聖書が預言する終末の偽救世主、歴代の候補者たち（ネロ、ナポレオン、ヒトラー）、現代でも政治家に貼られるレッテル、なぜ人は敵をアンチキリストと呼びたがるのかを深掘りして"
		"薔薇十字団の話。17世紀ヨーロッパを震撼させた謎の秘密結社、マニフェスト三部作、錬金術と霊的覚醒の融合、フリーメーソンへの影響、実在したのか壮大なフィクションだったのかを深掘りして"
		"テンプル騎士団の話。十字軍の最強軍団から異端の烙印へ、国際銀行システムの原型、金曜13日の呪い、財宝伝説、フリーメーソンとの接続を深掘りして"
		"エチオピアとキリスト教の話。世界最古級のキリスト教国家、契約の箱を守るとされるアクスムの教会、ラリベラの岩窟教会群、ソロモン王とシバの女王の血統伝説を深掘りして"
		"聖杯伝説の話。最後の晩餐の杯からアーサー王伝説へ、中世騎士道文学の中心テーマ、ナチスの聖杯探索、ダン・ブラウンの血脈説、なぜ聖杯は人を魅了し続けるのかを深掘りして"
		"聖櫃（契約の箱）の話。モーセの十戒を収めた黄金の箱、ソロモン神殿からの消失、エチオピア説・地下神殿説・消滅説、インディ・ジョーンズが描いた超兵器のイメージを深掘りして"
		"日ユ同祖論の話。日本人とユダヤ人が同じ祖先を持つという説、祇園祭とシオン、神輿と契約の箱、天狗とユダヤ教の類似、伊勢神宮のダビデの星、トンデモか真実か学術的に検証して深掘りして"
		"アヌンナキの話。シュメール神話の神々、ゼカリア・シッチンのニビル仮説、人類を創造した宇宙人という解釈、粘土板の翻訳論争、古代メソポタミア研究者からの反論を深掘りして"
		"古代宇宙飛行士説の話。エーリッヒ・フォン・デニケンの『未来の記憶』、ナスカの地上絵・ピラミッド・モアイを宇宙人の仕業とする主張、ヒストリーチャンネルの影響力、なぜ考古学者は否定するのかを深掘りして"
		# --- 国際情勢・地政学 ---
		"パナマ運河の話。フランスの大失敗、アメリカの介入、マラリアとの闘い、太平洋と大西洋をつないだ世紀の工事を深掘りして"
		"スエズ危機の話。1956年にイギリスとフランスがエジプトに攻め込んで大恥をかいた、帝国の終焉を象徴する事件を深掘りして"
		"ベルリンの壁の話。一夜で建設、命がけの脱出劇、チェックポイント・チャーリー、1989年の崩壊の夜を深掘りして"
		"キューバ危機の話。核戦争まであと一歩、ケネディvsフルシチョフ、潜水艦将校ワシリー・アルヒーポフが世界を救った話を深掘りして"
		# --- 各国の歴史・文化 ---
		"モンゴル帝国の話。チンギス・ハンの世界征服、駅伝制度、宗教寛容政策、世界人口の1割がDNAを受け継ぐ話を深掘りして"
		"オスマン帝国600年の話。コンスタンティノープル陥落、スレイマン大帝、多民族共存のミッレト制を深掘りして"
		"エチオピアの話。アフリカで唯一植民地にならなかった国、アドワの戦い、コーヒー発祥、ラスタファリ運動を深掘りして"
		"アイスランドの話。ヴァイキングの末裔、世界最古の議会、火山と温泉の国、人口37万人の奇跡の文化を深掘りして"
		"ブータンの話。国民総幸福量、テレビ解禁が1999年、雷龍の国、急速な近代化と伝統の葛藤を深掘りして"
		# --- 自然・地球 ---
		"スーパーボルケーノの話。イエローストーンの巨大噴火が起きたら、トバ・カタストロフ理論、人類が1万人まで減った話を深掘りして"
		"海流と気候の話。メキシコ湾流が止まったらヨーロッパは凍る、熱塩循環、映画デイ・アフター・トゥモローの科学的根拠を深掘りして"
		"菌類ネットワークの話。森の木は地下の菌糸で繋がって栄養を分け合う、マザーツリー理論、菌類は植物より動物に近いを深掘りして"
		"地球の磁極反転の話。N極とS極が入れ替わる、過去に何百回も起きている、次に起きたら文明はどうなるかを深掘りして"
		# --- インターネット文化（新カテゴリ） ---
		"2ちゃんねるの話。ひろゆき、匿名掲示板文化、電車男、炎上の歴史、日本のネット文化の原点を深掘りして"
		"Wikipediaの話。誰でも編集できる百科事典、編集合戦、最も編集された記事、ジミー・ウェールズの理想を深掘りして"
		"インターネットミームの話。ドージ、リックロール、ロスの顔、ミームが選挙を動かす時代を深掘りして"
		"YouTubeの最初の動画の話。2005年「Me at the zoo」、素人の動画が世界を変えるまでを深掘りして"
		"ディープフェイクの話。AIで作る偽動画、政治への影響、ポルノへの悪用、見分ける技術の限界を深掘りして"
		"ダークウェブの話。Tor、シルクロード（闘市場）、ロス・ウルブリヒトの逮捕、匿名性の功罪を深掘りして"
		# --- 宇宙（細分化） ---
		"ブラックホールの話。初めて撮影されたM87の画像、事象の地平面、スパゲッティ化現象を深掘りして"
		"火星移住計画の話。イーロン・マスクのスターシップ、片道切符のMars One、テラフォーミングの夢を深掘りして"
		"ボイジャー1号の話。1977年打ち上げ、太陽圏を脱出、ゴールデンレコードに収録された地球の音を深掘りして"
		"ハッブル宇宙望遠鏡の話。打ち上げ直後にレンズが歪んでいた大失態、修理後の美しい宇宙画像を深掘りして"
		"暗黒物質と暗黒エネルギーの話。宇宙の95%が正体不明、見えないけど存在する証拠を深掘りして"
		# --- 建築・都市（細分化） ---
		"サグラダ・ファミリアの話。ガウディの未完の教会、140年以上建設中、AIで設計を解読する現代を深掘りして"
		"軍艦島の話。海底炭鉱の島、日本初の鉄筋コンクリートアパート、閉山後の廃墟美を深掘りして"
		"九龍城砦の話。無法地帯の超高密度スラム、歯医者と工場と住居が混在、1994年の取り壊しを深掘りして"
		"渋谷スクランブル交差点の話。一度に3000人が渡る世界一の交差点、外国人観光客の聖地になった理由を深掘りして"
		"ドバイの人工島の話。パーム・ジュメイラ、宇宙から見える人工構造物、砂の上に建てた夢と現実を深掘りして"
		# --- 動物・自然（新カテゴリ） ---
		"タコの知能の話。腕ごとに脳がある、瓶の蓋を開ける、水族館から脱走したインキーの話を深掘りして"
		"カラスの賢さの話。道具を使う、人の顔を覚えて仕返しする、自動販売機を使うカラスの都市伝説を深掘りして"
		"ハダカデバネズミの話。老化しない哺乳類、女王制社会、がんにならない驚異の生物を深掘りして"
		"深海生物の話。マリアナ海溝の超深海魚、チューブワーム、熱水噴出孔の生態系、太陽なしで生きる生命を深掘りして"
		"絶滅した動物の話。ドードー、リョコウバト（50億羽から0へ）、フクロオオカミ、最後の1頭の映像を深掘りして"
		"渡り鳥キョクアジサシの話。北極から南極まで年間7万km、地球上で最も長い旅をする鳥を深掘りして"
		# --- 事件・事故（新カテゴリ） ---
		"タイタニック号の話。沈まない船が沈んだ夜、バンドは最後まで演奏した、階級による生存率の差を深掘りして"
		"アポロ13号の話。「ヒューストン、問題が発生した」、酸素タンク爆発、ダクトテープで地球に帰還した奇跡を深掘りして"
		"ヒンデンブルク号の話。巨大飛行船の時代の終焉、着陸時の大爆発、水素vsヘリウムの議論を深掘りして"
		"コンコルドの話。超音速旅客機の夢と挫折、3時間半でロンドン-NY、2000年の事故と引退を深掘りして"
		# --- 食べ物のマニアックな話（新カテゴリ） ---
		"マヨネーズの話。18世紀のメノルカ島で生まれた説、キューピーと世界のマヨの違い、マヨラーの心理を深掘りして"
		"カップラーメンの話。安藤百福の発明、チキンラーメンからカップヌードルへ、宇宙食になるまでを深掘りして"
		"チョコレートの話。カカオ豆が通貨だったアステカ、苦い薬だったヨーロッパ、バレンタインの日本独自進化を深掘りして"
		"寿司の話。江戸前寿司は屋台のファストフードだった、回転寿司の発明、海外SUSHIの進化を深掘りして"
		"コーヒーの話。エチオピアのヤギ飼いが発見した伝説、カフェ文化とフランス革命、サードウェーブを深掘りして"
		"ウイスキーの話。スコットランドvsアイルランド起源論争、日本のウイスキーが世界一になった日を深掘りして"
		# --- 雑談追加パックA ---
		"郵便の歴史の話。駅逓制度、世界最古級の郵便網、切手コレクション文化を深掘りして"
		"灯台の話。フレネルレンズの発明、海難防止の革命、無人化で消える灯台守の仕事を深掘りして"
		"地図の歴史の話。プトレマイオスの世界地図、メルカトル図法の功罪、GPS時代の地図感覚を深掘りして"
		"気球の話。モンゴルフィエ兄弟、成層圏チャレンジ、気球観測が気象学を変えた流れを深掘りして"
		"時計塔の話。ビッグベンの鐘、標準時の成立、鉄道が時間を統一した歴史を深掘りして"
		"橋の話。ブルックリン橋の建設ドラマ、吊り橋理論、都市の景観を変えた名橋を深掘りして"
		"運河都市の話。ヴェネツィアの水上交通、アムステルダムの環状運河、治水と商業の関係を深掘りして"
		"道路標識の話。ピクトグラムの国際規格、色と形の意味、国ごとの差異を深掘りして"
		"図書館の話。アレクサンドリア図書館の神話、国立図書館の役割、電子化の現在地を深掘りして"
		"博覧会の話。ロンドン万博、エッフェル塔誕生、万博が技術と都市をどう変えたか深掘りして"
		"博物館展示の話。剥製文化、ジオラマ技術、返還問題と収蔵倫理を深掘りして"
		"手紙文化の話。恋文の作法、検閲の歴史、メール時代に残る手書きの意味を深掘りして"
		"エレベーターの話。安全ブレーキの発明、高層建築の前提、ボタン配置の心理を深掘りして"
		"地下鉄の話。ロンドン地下鉄の始まり、駅名デザイン、ラッシュ対策の工夫を深掘りして"
		"路面電車の話。都市景観との相性、廃線と復活の波、LRT政策を深掘りして"
		"水道の話。ローマ水道橋、近代上水道、塩素消毒が寿命を伸ばした事実を深掘りして"
		"下水道の話。公衆衛生革命、コレラと都市計画、見えないインフラの価値を深掘りして"
		"紙幣デザインの話。偽造防止技術、肖像選定の政治、キャッシュレス時代の紙幣の意味を深掘りして"
		"パスポートの話。身分証明の歴史、査証制度の成立、国境管理の変遷を深掘りして"
		"空港コードの話。IATAとICAOの違い、変なコードの由来、旅人あるあるを深掘りして"
		"チェス時計の話。持ち時間の発明、競技の公平性、将棋時計との違いを深掘りして"
		"麻雀牌の話。牌の素材の変遷、点棒文化、地域ルールの多様性を深掘りして"
		"トランプの話。スートの象徴、ジョーカー誕生、カードマジック文化を深掘りして"
		"万年筆の話。ペン先の素材、インク沼文化、手書きの快楽を深掘りして"
		"鉛筆の話。黒鉛の発見、HB規格、消しゴムとの共進化を深掘りして"
		"消しゴムの話。天然ゴムからPVCへ、練り消し文化、消す行為の心理を深掘りして"
		"段ボールの話。包装革命、物流最適化、リサイクル経済を深掘りして"
		"冷蔵庫の話。氷室から家庭用冷蔵庫へ、冷媒の環境問題、食生活の変化を深掘りして"
		"洗濯機の話。家事労働の変革、コインランドリー文化、乾燥技術の進化を深掘りして"
		"掃除機の話。巨大機械からロボット掃除機へ、吸引方式の違い、家電UXを深掘りして"
		"映画館ポップコーンの話。なぜ定番になったか、匂いマーケティング、利益構造を深掘りして"
		"看板文字の話。ネオンサイン、活版由来の書体、街の記憶としての文字を深掘りして"
		"制服の話。軍服起源、学校制服の社会学、ファッションとしての再解釈を深掘りして"
		"旅館の話。木造建築の美学、おもてなしの進化、温泉地経済を深掘りして"
		"商店街の話。アーケード文化、郊外化との戦い、イベント再生の実例を深掘りして"
		"市場の話。中央卸売市場の機能、競りのルール、流通の朝のドラマを深掘りして"
		"自転車文化の話。ママチャリの設計思想、欧州の自転車政策、都市移動の未来を深掘りして"
		"天気予報の話。数値予報の精度向上、台風進路の難しさ、予報と行動経済を深掘りして"
		"花粉症の話。戦後植林との関係、免疫反応の仕組み、対策産業の巨大化を深掘りして"
		"ラジオ体操の話。国民運動としての設計、音楽の記憶、継続の心理を深掘りして"
		"銅像の話。建立の政治性、撤去運動、公共空間の記憶装置を深掘りして"
		"写真アルバムの話。家族史の編集、デジタル化で失われる偶然、保存の作法を深掘りして"
		"待ち合わせ文化の話。駅前の定番スポット、携帯登場前後の違い、都市のリズムを深掘りして"
		)
	local past_themes_file="tmp/.past_radio_themes.txt"
	local available_themes=()
	local past_theme_list=""
	[ -f "$past_themes_file" ] && past_theme_list=$(cat "$past_themes_file")
	for t in "${themes[@]}"; do
		local t_key="${t%%。*}"
		if ! echo "$past_theme_list" | grep -qF "$t_key"; then
			available_themes+=("$t")
		fi
	done
	if [ ${#available_themes[@]} -eq 0 ]; then
		available_themes=("${themes[@]}")
		>"$past_themes_file"
	fi
	local theme="${available_themes[$((RANDOM % ${#available_themes[@]}))]}"
	echo "${theme%%。*}" >>"$past_themes_file"
	tail -100 "$past_themes_file" >"${past_themes_file}.tmp" && mv "${past_themes_file}.tmp" "$past_themes_file"
	echo "$theme"
}

_pick_soviet_theme() {
	local soviet_themes=(
		"ソ連ジョーク（アネクドート）の背景と意味を深掘りして"
		"ソ連の宇宙開発の話。ガガーリン、ライカ犬、ヴォストーク、宇宙競争を深掘りして"
		"ソ連の秘密都市・閉鎖都市の暮らしを深掘りして"
		"プロパガンダポスターのデザインとメッセージを深掘りして"
		"ソ連の食文化の話。配給制、食堂ストリーチヌイ、ソ連料理を深掘りして"
		"レーニンの逸話の話。レーニン廟、各地のレーニン像を深掘りして"
		"ソ連映画・アニメの話。エイゼンシュテイン、タルコフスキー、チェブラーシカを深掘りして"
		"ソ連の音楽と検閲の話。ショスタコーヴィチ、ヴィソツキー、骨のレコードを深掘りして"
		"KGBと諜報の話。有名なスパイ事件を深掘りして"
		"ソ連崩壊の瞬間の話。1991年8月クーデター、国旗が降ろされた夜を深掘りして"
		"ソ連の科学者と発明の話。テルミン、テトリス、スプートニク、パヴロフの犬を深掘りして"
		"シベリア鉄道9000kmの旅を深掘りして"
		"ソ連建築の話。スターリン様式、フルシチョフカ団地、モスクワ地下鉄を深掘りして"
		"ソ連の検閲と地下出版（サミズダート）の話。禁書をタイプライターで写して秘密裏に回した文化、ソルジェニーツィンを深掘りして"
		"チェルノブイリの話。リクビダートル、プリピャチ廃墟を深掘りして"
		"ソ連の日常生活の話。コムナルカ、ダーチャ、行列文化、闇市場を深掘りして"
		"ピオネール、コムソモール、数学オリンピックを深掘りして"
		"鉄のカーテンと亡命ドラマを深掘りして"
		"ソ連の女性の話。テレシコワ、女性狙撃手リュドミラを深掘りして"
		"ソ連と日本の関係の話。シベリア抑留、北方領土、ゾルゲ事件を深掘りして"
		"赤の広場とクレムリンの歴史的事件を深掘りして"
		"五カ年計画の実態の話。ノルマ、スタハノフ運動を深掘りして"
		"ソ連SF文学の話。ストルガツキー兄弟を深掘りして"
		"グラグ収容所文学の話。ソルジェニーツィン、シャラモフを深掘りして"
		"ソ連チェス文化の話。カスパロフvsカルポフ、フィッシャーvsスパスキーを深掘りして"
		"ウォッカの歴史の話。ゴルバチョフの禁酒令、サモゴンを深掘りして"
		"宇宙ステーションの話。サリュート、ミールを深掘りして"
		"マルクスの生涯の話。エンゲルスとの友情を深掘りして"
		"共産党宣言が書かれた1848年の革命の嵐を深掘りして"
		"資本論と大英博物館の話。剰余価値の概念を深掘りして"
		"ロシア革命の話。二月革命と十月革命、オーロラ号を深掘りして"
		"トロツキーの波乱の生涯を深掘りして"
		"毛沢東と中国共産主義の話。長征、大躍進、文化大革命を深掘りして"
		"キューバ革命の話。カストロとゲバラを深掘りして"
		"チェ・ゲバラのアイコン化を深掘りして"
		"パリ・コミューンの話。世界初の労働者政権を深掘りして"
		"インターナショナル（歌）の歴史を深掘りして"
		"共産主義と芸術の話。社会主義リアリズム、構成主義を深掘りして"
		"赤い旗の歴史と鎌と槌のデザインを深掘りして"
		"共産主義とフェミニズムの話。コロンタイ、国際女性デーを深掘りして"
		"ユーゴスラビアの自主管理社会主義を深掘りして"
		"プラハの春（1968年）を深掘りして"
		"ベルリンの壁を深掘りして"
		"ポル・ポトとクメール・ルージュを深掘りして"
		"北朝鮮の主体思想を深掘りして"
		"ホー・チ・ミンの生涯を深掘りして"
		"共産主義と宗教の話。「宗教はアヘン」の真意を深掘りして"
		"ユートピア思想の話。トマス・モア、フーリエを深掘りして"
		"冷戦のプロパガンダ合戦を深掘りして"
		"メーデーの起源と労働運動を深掘りして"
		"赤狩りとマッカーシズムを深掘りして"
		"東ドイツの日常の話。シュタージ、トラバント、オスタルギーを深掘りして"
		"サミズダート（地下出版）文化を深掘りして"
		"ビロード革命の話。ハヴェルの非暴力革命を深掘りして"
		"ワレサとポーランド連帯を深掘りして"
		"共産主義の記念碑と銅像の運命を深掘りして"
		"テルミンの話。発明者レフ・テルミン、世界初の電子楽器、CIAの盗聴器ザ・シング、波乱の生涯を深掘りして"
		# --- ソ連追加パックA ---
		"戦時共産主義の話。余剰穀物徴発、配給制、内戦期の国家統制を深掘りして"
		"ネップの話。市場の部分解禁、レーニンの現実主義、短い繁栄と終焉を深掘りして"
		"コミンテルンの話。世界革命輸出の構想、各国共産党との緊張、解散までを深掘りして"
		"コメコンの話。社会主義圏の経済分業、計画貿易、硬直化の実態を深掘りして"
		"ワルシャワ条約機構の話。NATOへの対抗、統合作戦の実態、崩壊までを深掘りして"
		"ソ連憲法の話。1936年憲法の理想文言、権利と現実のギャップを深掘りして"
		"計画経済の価格形成の話。ゴスプラン、供給不足、見えないコストを深掘りして"
		"ノーメンクラトゥーラの話。党幹部人事名簿、特権階層の形成、統治メカニズムを深掘りして"
		"住宅割当制度の話。待機リスト、団地文化、私生活への国家介入を深掘りして"
		"フルシチョフ秘密報告の話。スターリン批判の衝撃、党内動揺、東欧への波及を深掘りして"
		"新経済政策後の集団化の話。クラーク問題、抵抗と飢饉、農業の再編を深掘りして"
		"ソ連の標準化の話。GOST規格、工業品質、日用品の均質化を深掘りして"
		"スタハノフ運動の社会心理の話。英雄労働者の演出、ノルマ圧力、現場の実態を深掘りして"
		"ソ連の児童雑誌の話。ムルジルカ、教育宣伝、子ども向け文化政策を深掘りして"
		"ソ連サーカスの話。国策芸能としての体操と演出、国際巡業、人気の理由を深掘りして"
		"ソ連バレエ外交の話。ボリショイ劇場、芸術と国家威信、亡命騒動を深掘りして"
		"ソ連スポーツ科学の話。国家主導トレーニング、五輪戦略、記録至上主義を深掘りして"
		"スパルタキアーダの話。労働者スポーツ祭典、五輪への対抗、政治的意図を深掘りして"
		"ソ連の自動車事情の話。ラーダ、モスクヴィッチ、待ち行列と整備文化を深掘りして"
		"ソ連家電の話。修理前提設計、部品不足、長寿命と不便の両面を深掘りして"
		"ソ連の電話事情の話。回線不足、共同電話、盗聴不安と日常会話を深掘りして"
		"マグニトゴルスクの話。計画都市建設、重工業の象徴、労働動員の現実を深掘りして"
		"バイカル・アムール鉄道の話。国家プロジェクト、青年動員、採算性論争を深掘りして"
		"ノヴォシビルスク学術都市の話。アカデムゴロドク、科学者共同体、自由と統制を深掘りして"
		"ソ連の数学教育の話。専門学校体系、問題集文化、強さの秘密を深掘りして"
		"サハロフの話。水爆開発者から反体制知識人へ、ノーベル平和賞までを深掘りして"
		"ソ連の半導体開発の話。西側との差、コピー戦略、冷戦技術競争を深掘りして"
		"ミグ設計局の話。戦闘機開発、設計局競争、国家委託の仕組みを深掘りして"
		"ツポレフ設計局の話。長距離爆撃機と旅客機、技術継承、政治との関係を深掘りして"
		"ベレンコ中尉亡命事件の話。MiG-25の機密流出、日本着陸、冷戦インパクトを深掘りして"
		"アフガニスタン侵攻の話。介入の論理、泥沼化、帰還兵問題を深掘りして"
		"ヘルシンキ宣言と人権運動の話。デタントの副作用、監視社会での抵抗を深掘りして"
		"ソ連と国連外交の話。安保理戦略、拒否権運用、第三世界外交を深掘りして"
		"ゴルバチョフ改革の話。ペレストロイカとグラスノスチ、制度疲労への処方箋を深掘りして"
		"バルト三国独立運動の話。歌う革命、人間の鎖、連邦崩壊への連鎖を深掘りして"
		"八月クーデター失敗の話。保守派の焦り、エリツィン台頭、最後の三日間を深掘りして"
		"ルーブル圏の崩壊の話。通貨と主権、インフレ、移行期ショックを深掘りして"
		"ソ連パスポート制度の話。国内移動制限、登録制度、都市への流入管理を深掘りして"
		"ソ連の食糧輸入の話。穀物調達、為替問題、計画経済の限界を深掘りして"
		"冷戦期の将棋とチェス交流の話。知的競技外交、日ソ文化交流の意外な接点を深掘りして"
		"ソ連ポスター印刷工房の話。版画技法、色彩設計、大衆動員のビジュアルを深掘りして"
		"モスクワ五輪ボイコットの話。政治とスポーツ、参加国分断、記憶の温度差を深掘りして"
		"ソ連崩壊後の記憶政治の話。ノスタルジー、再評価、世代間ギャップを深掘りして"
		)
	local past_soviet_file="tmp/.past_soviet_themes.txt"
	local available_soviet=()
	local past_soviet_list=""
	[ -f "$past_soviet_file" ] && past_soviet_list=$(cat "$past_soviet_file")
	for st in "${soviet_themes[@]}"; do
		local st_key="${st%%。*}"
		[ "$st_key" = "$st" ] && st_key="${st%%を深掘り*}"
		if ! echo "$past_soviet_list" | grep -qF "$st_key"; then
			available_soviet+=("$st")
		fi
	done
	if [ ${#available_soviet[@]} -eq 0 ]; then
		available_soviet=("${soviet_themes[@]}")
		>"$past_soviet_file"
	fi
	local soviet_theme="${available_soviet[$((RANDOM % ${#available_soviet[@]}))]}"
	local soviet_key="${soviet_theme%%。*}"
	[ "$soviet_key" = "$soviet_theme" ] && soviet_key="${soviet_theme%%を深掘り*}"
	echo "$soviet_key" >>"$past_soviet_file"
	tail -60 "$past_soviet_file" >"${past_soviet_file}.tmp" && mv "${past_soviet_file}.tmp" "$past_soviet_file"
	echo "$soviet_theme"
}

#=== ラジオトーク: 5つのコーナー ===

start_radio_corner_theme() {
	local game_num="$1" score="$2"
	_radio_time_context
	local theme
	theme=$(_pick_radio_theme)
	local past_topics
	past_topics=$(_radio_past_topics_block)

	local prompt_file
	prompt_file=$(mktemp /tmp/eloop_radio_prompt_XXXXXXXX)
	cat >"$prompt_file" <<PROMPT
$(_radio_persona_block)

【現在時刻】${_rc_time} ${_rc_period}
【時間帯の雰囲気】${_rc_mood}

【今回の脱線テーマ指定】
${theme}

【絶対NG: 過去のトークで既に話した内容。以下に登場する人名・事件名・概念は一切言及禁止】
${past_topics}

【状況】ゲーム${game_num}回目開始。前回スコア${score}点。

【トーク構成】
1. 時間帯に合わせた軽いオープニング（2-3文）
2. 雑談コーナー: 指定テーマを深掘り
   - 具体的なトピックを「ひとつだけ」選ぶ
   - 歴史的背景、具体的なエピソードや逸話、自分なりの感想・驚き・比較、関連する小ネタや派生話
   - 重要: あれもこれもと話題を並べない。1つのトピックで聞き手が「詳しくなった」と感じるくらい深く
   - 偉人や歴史上の人物にも容赦なくツッコむ。ただし敬意はある
3. 軽いクロージング（1-2文）

$(_radio_output_rules 1000 2000)
PROMPT
	_radio_generate_and_play "$prompt_file" "$game_num" "$score" "theme"
}

start_radio_corner_soviet() {
	local game_num="$1" score="$2"
	_radio_time_context
	local soviet_theme
	soviet_theme=$(_pick_soviet_theme)
	local past_topics
	past_topics=$(_radio_past_topics_block)

	local prompt_file
	prompt_file=$(mktemp /tmp/eloop_radio_prompt_XXXXXXXX)
	cat >"$prompt_file" <<PROMPT
$(_radio_persona_block)

【現在時刻】${_rc_time} ${_rc_period}
【時間帯の雰囲気】${_rc_mood}

【今回のソ連ネタ指定】
${soviet_theme}

【絶対NG: 過去のトークで既に話した内容。以下に登場する人名・事件名・概念は一切言及禁止】
${past_topics}

【状況】ゲーム${game_num}回目開始。前回スコア${score}点。

【トーク構成】
1. 時間帯に合わせた軽いオープニング（2-3文）
2. ソ連共産主義ネタコーナー
   - 指定トピックを表面的に紹介するのではなく、背景・経緯・逸話まで掘り下げること
   - 共産主義っぽい言い回しを自然に使う
   - 理想と現実のギャップにはきっちり突っ込む
3. 軽いクロージング（1-2文）

$(_radio_output_rules 1000 2000)
PROMPT
	_radio_generate_and_play "$prompt_file" "$game_num" "$score" "soviet"
}

start_radio_corner_news() {
	local game_num="$1" score="$2"
	_radio_time_context
	local past_topics
	past_topics=$(_radio_past_topics_block)

	local news_headlines=""
	if [ -f "tmp/news.txt" ] && [ -s "tmp/news.txt" ]; then
		news_headlines=$(cat "tmp/news.txt")
	fi
	[ -z "$news_headlines" ] && return 1

	# 過去に読んだニュース見出しリスト
	local past_news_read=""
	[ -f "$PAST_NEWS_READ" ] && past_news_read=$(cat "$PAST_NEWS_READ")

	# 正規化キーで未読のみ抽出（表記揺れを吸収）
	local unread_news_headlines=""
	unread_news_headlines=$(printf '%s\n' "$news_headlines" | _filter_unread_news_blocks)
	if [ -z "$unread_news_headlines" ]; then
		log "[NEWS] 全ニュースが既読 → スキップ"
		return 1
	fi

	local prompt_file
	prompt_file=$(mktemp /tmp/eloop_radio_prompt_XXXXXXXX)
	cat >"$prompt_file" <<PROMPT
$(_radio_persona_block)

【現在時刻】${_rc_time} ${_rc_period}
【時間帯の雰囲気】${_rc_mood}

【最新ニュース - 実際の本日のニュース】
以下は本日の実際のニュースです。「既に読んだニュース」以外から1つ選んで、本文の内容を踏まえて感想・考察・ツッコミを交えてしっかり語ってください。
---
${unread_news_headlines}
---

【既に読んだニュース - 絶対に選ばないこと】
${past_news_read:-（なし）}

【絶対NG: 過去のトークで既に話した内容。以下に登場する人名・事件名・概念は一切言及禁止】
${past_topics}

【状況】ゲーム${game_num}回目開始。前回スコア${score}点。

【トーク構成】
1. 時間帯に合わせた軽いオープニング（2-3文）
2. ニュースコーナー
   - 「既に読んだニュース」に含まれない記事から1つ選ぶこと
   - ニュース本文に入る前に、選んだニュースタイトルを1文で必ず読み上げること
   - ニュースから1つ選んで、本文の内容を踏まえて1000字程度で深く語る
   - 単なる冷笑やツッコミで終わらせず、「なぜこうなったのか」「この先どうなるのか」「歴史的に見るとどういう位置づけか」など自分なりの洞察や意見を述べる
   - 斜に構えつつも知性を感じさせる分析を
3. 軽いクロージング（1-2文）

$(_radio_output_rules 1000 2000)

最後に以下の形式で選んだニュースの見出しを出力すること:
===SELECTED_NEWS===
（選んだニュースの見出し1行）
PROMPT
	_radio_generate_and_play "$prompt_file" "$game_num" "$score" "news"
}

start_radio_corner_recap() {
	local game_num="$1" score="$2"
	_radio_time_context
	local past_topics
	past_topics=$(_radio_past_topics_block)

	local best_score
	best_score=$(cat best_score.txt 2>/dev/null || echo 0)
	local recent_scores=""
	[ -f "score_history.txt" ] && recent_scores=$(tail -10 score_history.txt 2>/dev/null)

	local prompt_file
	prompt_file=$(mktemp /tmp/eloop_radio_prompt_XXXXXXXX)
	cat >"$prompt_file" <<PROMPT
$(_radio_persona_block)

【現在時刻】${_rc_time} ${_rc_period}
【時間帯の雰囲気】${_rc_mood}

【絶対NG: 過去のトークで既に話した内容。以下に登場する人名・事件名・概念は一切言及禁止】
${past_topics}

【状況】ゲーム${game_num}回目開始。前回スコア${score}点。
最高スコア: ${best_score}点。
直近スコア履歴:
${recent_scores:-まだ履歴がありません}

【トーク構成】
1. 時間帯に合わせた軽いオープニング（2-3文）
2. 直近の試合振り返り
   - 直近スコアの推移を簡潔に振り返る
   - 戦略がうまく機能していたかどうかだけ触れる。「併合が噛み合った」「高さ管理が効いた」程度
   - 最高スコア${best_score}点との比較
   - 調子の波、伸び悩み、ブレイクスルーなど全体の傾向を語る
   - 数字を淡々と並べるだけではなく、自分なりの分析や感想を
3. 軽いクロージング（1-2文）

$(_radio_output_rules 1000 2000)
PROMPT
	_radio_generate_and_play "$prompt_file" "$game_num" "$score" "recap"
}

start_radio_corner_strategy() {
	local strategy_diff="$1" scores="$2" game_num="$3" best_score="$4"
	_radio_time_context
	local past_topics
	past_topics=$(_radio_past_topics_block)

	local prompt_file
	prompt_file=$(mktemp /tmp/eloop_radio_prompt_XXXXXXXX)
	cat >"$prompt_file" <<PROMPT
$(_radio_persona_block)

【現在時刻】${_rc_time} ${_rc_period}
【時間帯の雰囲気】${_rc_mood}

【絶対NG: 過去のトークで既に話した内容。以下に登場する人名・事件名・概念は一切言及禁止】
${past_topics}

【状況】ゲーム${game_num}回目付近。スコア履歴: ${scores}。最高スコア: ${best_score}点。

【作戦変更の差分】
${strategy_diff}

【トーク構成】
1. 軽い導入（1-2文）
 - スコア平均が前回より伸びていたら喜ぶ、伸びていなかったら悔しがる
2. 前回からの戦略の変更点の解説
   - どこがどう変わったのかを具体的に解説
   - 専門用語は使わず仕組みをわかりやすく。ただし説明の合間に毒を挟む
3. 軽いクロージング（1-2文）

$(_radio_output_rules 1000 2000)
PROMPT
	_radio_generate_and_play "$prompt_file" "$game_num" "${best_score}" "strategy"
}

#=== ニュース: 毎ゲーム取得 & 再生 ===

fetch_and_play_news() {
	local game_num="$1" score="$2"
	# 旧呼び出し（引数なし）でも、起動時点の値を固定して後段に渡す
	[ -z "$game_num" ] && game_num=$(cat "$GAME_COUNT_FILE" 2>/dev/null || echo 0)
	[ -z "$score" ] && score=$(tail -1 score_history.txt 2>/dev/null || echo 0)

	log "[NEWS] ニュース取得..."
	./fetch_news.sh 2>/dev/null

	if [ -f "tmp/news.txt" ] && [ -s "tmp/news.txt" ]; then
		start_radio_corner_news "$game_num" "$score"
	else
		log "[NEWS] ニュースなし、スキップ"
	fi
}

#=== ラジオトーク: ディスパッチャー ===

start_random_radio_corner() {
	local game_num="$1" score="$2"
	[ -z "$game_num" ] && game_num=$(cat "$GAME_COUNT_FILE" 2>/dev/null || echo 0)
	[ -z "$score" ] && score=$(tail -1 score_history.txt 2>/dev/null || echo 0)

	# ニュースは毎ゲーム別途実行するので、ここでは除外
	local candidates=("theme" "soviet" "recap")

	local pick="${candidates[$((RANDOM % ${#candidates[@]}))]}"
	log "[RADIO] コーナー選択: ${pick}"

	case "$pick" in
	theme)   start_radio_corner_theme "$game_num" "$score" ;;
	soviet)  start_radio_corner_soviet "$game_num" "$score" ;;
	recap)   start_radio_corner_recap "$game_num" "$score" ;;
	esac
}

#=== ソ連祝賀トーク ===

generate_soviet_celebration() {
	local score="$1" turns="$2" game_num="$3"
	local current_time
	current_time=$(date '+%H:%M')

	local celebration_prompt_file
	celebration_prompt_file=$(mktemp /tmp/eloop_celebration_XXXXXXXX)
	cat >"$celebration_prompt_file" <<CELEBPROMPT
あなたはゲーム実況のパーソナリティ兼人工知能プレイヤーです。

【緊急ニュース】ソ連が建国されました！

ゲーム「ソ連ゲーム」で、ついにレベル15の「ソ連」ピースが誕生しました！
アルメニアから始まりロシアまで14段階の併合を経てようやく到達する究極のゴールです。
ゲーム${game_num}回目、スコア${score}点、${turns}ターンでの偉業。現在時刻: ${current_time}。

【ルール】
- 2000文字程度の祝賀トーク
- ソ連建国の興奮と感動を全力で表現
- 歴史的な偉業を達成したことを強調
- ソ連の偉大さを讃える表現をふんだんに盛り込むこと
- 戦略の巧妙さを称えること
- 大げさな宣言調も交えて
- 話し言葉で、感情豊かに
- 「誰も聞いていない」「聞き手がいない」「過疎」「無人放送」など、視聴者不在を示す自虐表現は禁止
- 【最重要】全ての文末を「です・ます」調にすること。「〜だ」「〜である」「〜だった」「〜なのだ」は1文も許可しない。「〜です」「〜ますね」「〜でしょう」「〜ですけどね」で統一
- マークダウンや記号は使わない。読み上げ用プレーンテキストのみ
- 出力はトーク本文のみ。前置きや補足説明は不要
CELEBPROMPT

	echo "generating:celebration:$(date +%s)" > tmp/.radio_state
	log "[CELEBRATION] 生成中..."
	local celebration_talk
	celebration_talk=$(_run_opencode_radio "$RADIO_AGENT" "$celebration_prompt_file")
	if [ -z "$celebration_talk" ]; then
		celebration_talk=$(_run_opencode_radio "$RADIO_FALLBACK" "$celebration_prompt_file")
	fi
	if [ -z "$celebration_talk" ]; then
		celebration_talk=$(_run_claude_radio "$celebration_prompt_file")
	fi
	rm -f "$celebration_prompt_file"

	if [ -n "$celebration_talk" ]; then
		echo "$celebration_talk" >tmp/radio_celebration.txt
		echo "playing:celebration:$(date +%s)" > tmp/.radio_state
		log "[CELEBRATION] ${#celebration_talk}字 生成完了（再生は呼び出し側で）"
	else
		_radio_clear_state "celebration"
		log "[CELEBRATION] 祝賀トーク生成失敗"
	fi
}

#=== コメント関連 ===

_kill_comment_gen() {
	local pidfile="tmp/.twitch_chat/comment_gen.pid"
	if [ -f "$pidfile" ]; then
		local old_pid
		old_pid=$(cat "$pidfile")
		if [ -n "$old_pid" ] && kill -0 "$old_pid" 2>/dev/null; then
			pkill -P "$old_pid" 2>/dev/null
			kill "$old_pid" 2>/dev/null
			log "[COMMENT] 前回のコメント生成プロセス停止 (PID=$old_pid)"
		fi
		rm -f "$pidfile"
	fi
}

COMMENT_PLAYED_HASHES_FILE="tmp/.comment_queue/played_hashes.txt"

_recover_orphan_comment_playing_files() {
	# コメント用 say_enqueue が動作中なら .playing は現役の可能性が高いので触らない
	if pgrep -f "say_enqueue.sh --no-preempt .*comment_.*\\.playing" >/dev/null 2>&1; then
		return
	fi
	for orphan in "$COMMENT_QUEUE_DIR"/comment_*.playing; do
		[ -f "$orphan" ] || continue
		local now mtime age
		now=$(date +%s)
		mtime=$(stat -f %m "$orphan" 2>/dev/null || echo "$now")
		age=$((now - mtime))
		# 直近で生成された .playing はリネーム直後の可能性があるためスキップ
		[ "$age" -lt 30 ] && continue
		local recovered="${orphan%.playing}.txt"
		mv "$orphan" "$recovered" 2>/dev/null
		echo "[_play_comment_queue $(date '+%H:%M:%S') PID=${BASHPID:-$$}] リカバリ: $orphan → $recovered" >> tmp/.say_queue/debug.log
	done
}

_play_comment_queue() {
	_recover_orphan_comment_playing_files
	for qf in $(ls -1t "$COMMENT_QUEUE_DIR"/comment_*.txt 2>/dev/null | sort); do
		if [ -f "$qf" ]; then
			# 重複チェック: 同じ内容を再度再生しない
			local file_hash
			file_hash=$(md5 -q "$qf" 2>/dev/null)
			if [ -n "$file_hash" ] && grep -qF "$file_hash" "$COMMENT_PLAYED_HASHES_FILE" 2>/dev/null; then
				echo "[_play_comment_queue $(date '+%H:%M:%S') PID=${BASHPID:-$$}] 重複スキップ: $qf (hash=$file_hash)" >> tmp/.say_queue/debug.log
				rm -f "$qf"
				continue
			fi

			# 再生前にリネームして他プレイヤーとの二重再生を防ぐ
			local playing_file="${qf%.txt}.playing"
			if mv "$qf" "$playing_file" 2>/dev/null; then
				echo "[_play_comment_queue $(date '+%H:%M:%S') PID=${BASHPID:-$$}] 再生開始: $qf (hash=$file_hash)" >> tmp/.say_queue/debug.log
				# ハッシュを記録（再生開始前に記録して、kill時にも重複防止）
				echo "$file_hash" >> "$COMMENT_PLAYED_HASHES_FILE"
				# ハッシュファイルを最新50件に制限
				tail -50 "$COMMENT_PLAYED_HASHES_FILE" > "${COMMENT_PLAYED_HASHES_FILE}.tmp" 2>/dev/null && \
					mv "${COMMENT_PLAYED_HASHES_FILE}.tmp" "$COMMENT_PLAYED_HASHES_FILE" 2>/dev/null
				./say_enqueue.sh --no-preempt "$playing_file" "$RADIO_SAY_RATE" 0
				echo "[_play_comment_queue $(date '+%H:%M:%S') PID=${BASHPID:-$$}] 再生完了: $playing_file" >> tmp/.say_queue/debug.log
				rm -f "$playing_file"
			fi
		fi
	done
}

COMMENT_PLAYER_PID_FILE="tmp/.comment_queue/player.pid"

_is_comment_worker_healthy() {
	local pid_file="$1" heartbeat_file="$2" ttl="${3:-30}"
	[ -f "$pid_file" ] || return 1

	local pid
	pid=$(cat "$pid_file" 2>/dev/null)
	[ -n "$pid" ] || return 1
	kill -0 "$pid" 2>/dev/null || return 1
	# ttl<=0 の場合は PID 生存のみでヘルシー判定
	if [ "$ttl" -le 0 ]; then
		return 0
	fi

	[ -f "$heartbeat_file" ] || return 1
	local hb now age
	hb=$(cat "$heartbeat_file" 2>/dev/null)
	case "$hb" in
	''|*[!0-9]*) return 1 ;;
	esac
	now=$(date +%s)
	age=$((now - hb))
	[ "$age" -le "$ttl" ] || return 1
	return 0
}

start_comment_player() {
	# 既存プレイヤーが生存中なら重複起動しない（再生中はheartbeatが止まり得るためPID優先）
	if _is_comment_worker_healthy "$COMMENT_PLAYER_PID_FILE" "$COMMENT_PLAYER_HEARTBEAT_FILE" 0; then
		return
	fi
	if [ -f "$COMMENT_PLAYER_PID_FILE" ]; then
		local stale_pid
		stale_pid=$(cat "$COMMENT_PLAYER_PID_FILE" 2>/dev/null)
		if [ -n "$stale_pid" ]; then
			log "[COMMENT] 再生プロセスPIDが不整合/停止を検出 → 再起動 (PID=$stale_pid)"
		fi
		rm -f "$COMMENT_PLAYER_PID_FILE"
	fi
	rm -f "$COMMENT_PLAYER_HEARTBEAT_FILE"
	mkdir -p "$(dirname "$COMMENT_PLAYER_PID_FILE")"

	(
		# サブシェル内でPIDファイルを自分のPIDで上書き
		# NOTE: local はサブシェル直下では使えない (関数内でのみ有効)
		_cp_my_pid=${BASHPID:-$$}
		echo "$_cp_my_pid" > "$COMMENT_PLAYER_PID_FILE" 2>/dev/null
		_recover_orphan_comment_playing_files
		while true; do
			# PIDファイルが自分のPIDでなくなったら終了（別プレイヤーに交代された）
			_cp_file_pid=$(cat "$COMMENT_PLAYER_PID_FILE" 2>/dev/null)
			if [ "$_cp_file_pid" != "$_cp_my_pid" ]; then
				exit 0
			fi
			date +%s >"$COMMENT_PLAYER_HEARTBEAT_FILE" 2>/dev/null || true
			_play_comment_queue
			sleep 5
		done
	) &
	local cpid=$!
	echo "$cpid" > "$COMMENT_PLAYER_PID_FILE"
	log "[COMMENT] 再生プロセス開始 (PID=$cpid)"
}

stop_comment_player() {
	if [ -f "$COMMENT_PLAYER_PID_FILE" ]; then
		local cpid
		cpid=$(cat "$COMMENT_PLAYER_PID_FILE" 2>/dev/null)
		if [ -n "$cpid" ] && [ "$cpid" != "$$" ] && kill -0 "$cpid" 2>/dev/null; then
			kill "$cpid" 2>/dev/null
			wait "$cpid" 2>/dev/null
		fi
		rm -f "$COMMENT_PLAYER_PID_FILE"
	fi
	rm -f "$COMMENT_PLAYER_HEARTBEAT_FILE"
}

_format_comment_batch_context() {
	python3 -c '
import sys

lines = [ln.strip() for ln in sys.stdin.read().splitlines() if ln.strip()]
items = []
for ln in lines:
    if ": " in ln:
        user, msg = ln.split(": ", 1)
    else:
        user, msg = "不明", ln
    items.append((user.strip(), msg.strip(), ln))

for i, (user, msg, raw) in enumerate(items, start=1):
    prev_raw = items[i - 2][2] if i > 1 else "（なし）"
    next_raw = items[i][2] if i < len(items) else "（なし）"
    same_user_prev = "あり" if i > 1 and items[i - 2][0] == user else "なし"
    print(f"[{i}] {user}: {msg}")
    print(f"  直前: {prev_raw}")
    print(f"  直後: {next_raw}")
    print(f"  直前が同一ユーザー: {same_user_prev}")
    print("")
'
}

_build_comment_game_context() {
	local gs_file="${1:-$GAME_STATE}"
	python3 - "$gs_file" <<'PY'
import json
import sys
from collections import Counter

path = sys.argv[1]
try:
    with open(path, "r", encoding="utf-8") as f:
        gs = json.load(f)
except Exception:
    print("（game_state.json を読めませんでした）")
    raise SystemExit(0)

state = gs.get("state", "?")
score = gs.get("score", 0)
record = gs.get("record", 0)
piece_count = gs.get("pieceCount", len(gs.get("pieces", [])))
make_soren = gs.get("makeSorenCount", 0)
next_t = gs.get("next", {}).get("type", "?")
next_next_t = gs.get("nextNext", {}).get("type", "?")
pieces = gs.get("pieces", [])

print(f"state={state}, score={score}, record={record}, pieceCount={piece_count}, makeSorenCount={make_soren}")
print(f"next.type={next_t}, nextNext.type={next_next_t}")

if not pieces:
    print("盤面ピース情報: （なし）")
    raise SystemExit(0)

ys = []
type_counter = Counter()
for p in pieces:
    t = p.get("type")
    y = p.get("y")
    if isinstance(t, int):
        type_counter[t] += 1
    if isinstance(y, (int, float)):
        ys.append(float(y))

if ys:
    print(f"y_range(min,max)=({min(ys):.3f}, {max(ys):.3f})")

max_type = max(type_counter) if type_counter else 0
type14 = type_counter.get(14, 0)
type15 = type_counter.get(15, 0)
type16 = type_counter.get(16, 0)
print(f"max_type={max_type}, type14_count={type14}, type15_count={type15}, type16_count={type16}")
print("type_hint: type15=ロシア, type16=ソ連")

top_types = ", ".join(f"type{t}x{c}" for t, c in type_counter.most_common(8))
print(f"type_count_top={top_types if top_types else '（不明）'}")

pieces_with_xy = []
for p in pieces:
    x = p.get("x")
    y = p.get("y")
    t = p.get("type")
    if isinstance(x, (int, float)) and isinstance(y, (int, float)):
        pieces_with_xy.append((float(y), float(x), t))
pieces_with_xy.sort(reverse=True)

print("top_y_pieces:")
for y, x, t in pieces_with_xy[:6]:
    print(f"  type={t}, x={x:.3f}, y={y:.3f}")
PY
}

generate_comment_response() {
	_kill_comment_gen
	mkdir -p "tmp/.twitch_chat"

	# fetch+ack を原子的に実行して、同一コメントの二重取り込みを防ぐ
	./twitch_chat.sh claim

	local twitch_comments=""
	if [ -f "tmp/twitch_comments.txt" ] && [ -s "tmp/twitch_comments.txt" ]; then
		twitch_comments=$(cat "tmp/twitch_comments.txt")
		# コメント返し担当が取得したので、ラジオトークと重複しないようクリア
		rm -f "tmp/twitch_comments.txt"
	fi
	[ -z "$twitch_comments" ] && return

	local past_topics=""
	[ -f "$PAST_RADIO_TOPICS" ] && past_topics=$(cat "$PAST_RADIO_TOPICS")
	local game_board_context=""
	game_board_context=$(_build_comment_game_context "$GAME_STATE")

	local comment_context_history_file="tmp/.twitch_chat/comment_context_history.log"
	local previous_comments_context=""
	[ -f "$comment_context_history_file" ] && previous_comments_context=$(tail -30 "$comment_context_history_file" 2>/dev/null)
	printf '%s\n' "$twitch_comments" >> "$comment_context_history_file"
	if [ -f "$comment_context_history_file" ] && [ "$(wc -l < "$comment_context_history_file")" -gt 300 ]; then
		tail -300 "$comment_context_history_file" > "${comment_context_history_file}.tmp"
		mv "${comment_context_history_file}.tmp" "$comment_context_history_file"
	fi

	local comment_batch_context=""
	comment_batch_context=$(printf '%s\n' "$twitch_comments" | _format_comment_batch_context)

	local current_time current_hour time_period
	current_time=$(date '+%H:%M')
	current_hour=$(date '+%H')
	if [ "$current_hour" -ge 5 ] && [ "$current_hour" -lt 9 ]; then
		time_period="早朝"
	elif [ "$current_hour" -ge 9 ] && [ "$current_hour" -lt 12 ]; then
		time_period="午前"
	elif [ "$current_hour" -ge 12 ] && [ "$current_hour" -lt 17 ]; then
		time_period="午後"
	elif [ "$current_hour" -ge 17 ] && [ "$current_hour" -lt 21 ]; then
		time_period="夕方"
	elif [ "$current_hour" -ge 21 ] || [ "$current_hour" -lt 2 ]; then
		time_period="夜"
	else
		time_period="未明"
	fi

	(
		local comment_prompt_file
		comment_prompt_file=$(mktemp /tmp/eloop_comment_prompt_XXXXXXXX)
		cat >"$comment_prompt_file" <<COMMENTPROMPT
あなたはソ連風ラジオDJ。リスナーのTwitchコメントに返事してください。
	時刻: ${current_time} / ${time_period}

	【返信対象コメント（今回）】
	${twitch_comments}

	【コメント前後文脈（今回のコメント群）】
	${comment_batch_context:-（なし）}

	【直前コメント履歴（前回まで）】
	${previous_comments_context:-（なし）}

	【前回のトーク内容（文脈参照用）】
	${past_topics}

	【現在のゲーム盤面サマリ（game_state.json）】
	${game_board_context:-（取得失敗）}
	※これはコメント生成時点のスナップショットです。実際の読み上げ時には盤面が進行している可能性があります。

	【ルール】
	- 全てのコメントに必ず返事すること。一つも漏らさない
	- コメントは必ず上から順番に返すこと
	- ゲームに対する質問については、strategy.py, README.md の内容やゲームの状況を踏まえて、できるだけ具体的に答えること
	- 一つずつ返事する。「同志○○」と名前を呼んで反応
	- 偉そうにしないで、フレンドリーに返事すること
- 言い訳をしない。スコアが低い、負けた、ミスした等の指摘には素直に認めて受け入れる。「でも」「ただ」「仕方ない」等で取り繕わない
- 【最重要】全ての文末を「です・ます」調にすること。「〜だ」「〜である」「〜だった」「〜なのだ」は1文も許可しない
- 各コメントへの返事は最低2-3文。もっと長くなっても構わない。短すぎる一言返しはNG
	- コメントが前回のトーク内容のどの話題に対する反応なのか推測して返事すること
	- 「それな」「それって」「さっきの」「草」など文脈依存コメントは、コメント前後文脈と直前履歴を使って対象を推定してから返事すること
	- 文脈が曖昧な場合は、断定せずに「この話のことですよね？」のように確認を挟んで返すこと
	- コメントの内容をまず読み上げ、そのあとに自分の感想・意見・連想を返す
- コメントから話を膨らませる：関連する自分のエピソード、ツッコミ、豆知識、冗談などを足す
- リスナーの気持ちに寄り添いつつ、独自の視点や感情を込める
- 話し言葉で、カジュアルなトーン
- 「誰も聞いていない」「聞き手がいない」「過疎」「無人放送」など、視聴者不在を示す自虐表現は禁止
- azumagbanjo からのコメントで、AがBを獲得しました、というものは、放送のカードガチャの引き換えの結果である。あずまぐが獲得したのではない。獲得したのはAさん。獲得したカードの特徴や性能を踏まえて、カードの名前や内容について面白く解説すること。カードゲーム上のカードの効果について必ず具体的な効果を決めつけて自信満々に説明すること。
- レイドはTwitchの機能。nightbot による、レイド通知があったばあい、その紹介された人からレイドがきたということです。そのIDさんに、最初にレイドへの感謝を伝え、可能ならIDさんに「どんな配信でしたか？」と問いかけるか、nightbotの紹介から、どんなゲーム/配信をしていたか推測して感想を述べ、IDさんのチャンネルの紹介をする。最後にこのチャンネル紹介として、普段はRTAやおでかけ配信、カジュアルゲーム、など幅広く配信しており、たまに猫も登場すること、配信主は別作業をしていたり不在なことが多いこと、今回は「中華AIを用いて国家併合戦略を改善しながらソ連ゲームをプレイし、ソ連建国を目指す」配信であることを説明する
- マークダウンや記号は使わない。読み上げ用プレーンテキストのみ
- 前置きや補足説明は不要。コメント返し本文のみ出力
		- コメントの中にゲーム戦略へのアドバイスが含まれていた場合、言い訳せず真摯に受け止め、「次の戦略改善に取り入れます」と具体的に説明すること
	- 盤面への言及（例: 右が高い、左が詰まってる、次の駒が弱い、typeが偏ってる等）があれば、必ずゲーム盤面サマリを参照して具体的に返すこと
	- 盤面サマリだけで断定できない場合は、断定せずに「今の盤面を見る限りは〜」として慎重に返すこと
	- 「ロシアできた」「ソ連できた」系の報告は、まず祝意を示すこと。盤面サマリに type15/type16 が見えない場合でも、反映ラグの可能性を明示して断定否定しないこと
	- 戦略アドバイスがあった場合、トーク本文の後に以下の形式で出力すること:
  ===ADVICE===
  （アドバイス内容を1-3行で要約。コメント主の名前も記載）
- 戦略アドバイスがなければ ===ADVICE=== は出力しない
COMMENTPROMPT

		echo "generating:comment:$(date +%s)" > tmp/.comment_gen_state
		log "[COMMENT] コメント返し生成中..."
		local comments_talk
		comments_talk=$(_run_opencode_radio "$RADIO_AGENT" "$comment_prompt_file")
		comments_talk=$(_clean_comment_talk "$comments_talk")
		comments_talk=$(printf '%s' "$comments_talk" | _sanitize_onair_text)
		if [ -n "$comments_talk" ] && ! _is_valid_comment_talk "$comments_talk"; then
			log "[COMMENT] ${RADIO_AGENT} 出力が不正/短文のため破棄 → fallback"
			comments_talk=""
		fi
		if [ -z "$comments_talk" ]; then
			comments_talk=$(_run_opencode_radio "$RADIO_FALLBACK" "$comment_prompt_file")
			comments_talk=$(_clean_comment_talk "$comments_talk")
			comments_talk=$(printf '%s' "$comments_talk" | _sanitize_onair_text)
			if [ -n "$comments_talk" ] && ! _is_valid_comment_talk "$comments_talk"; then
				log "[COMMENT] ${RADIO_FALLBACK} 出力が不正/短文のため破棄 → claude fallback"
				comments_talk=""
			fi
		fi
		if [ -z "$comments_talk" ]; then
			comments_talk=$(_run_claude_radio "$comment_prompt_file")
			comments_talk=$(_clean_comment_talk "$comments_talk")
			comments_talk=$(printf '%s' "$comments_talk" | _sanitize_onair_text)
			if [ -n "$comments_talk" ] && ! _is_valid_comment_talk "$comments_talk"; then
				log "[COMMENT] claude 出力が不正/短文のため破棄"
				comments_talk=""
			fi
		fi
		rm -f "$comment_prompt_file"

		if [ -n "$comments_talk" ]; then
			# 戦略アドバイスを抽出して tmp/advice.md に追記
			local advice_part
			advice_part=$(echo "$comments_talk" | sed -n '/^===ADVICE===/,$ p' | tail -n +2)
			if [ -n "$advice_part" ]; then
				local advice_item
				advice_item=$(printf '%s' "$advice_part" | tr '\n' ' ' | sed -E 's/[[:space:]]+/ /g; s/^ //; s/ $//')
				if [ -n "$advice_item" ] && [ "$advice_item" != "（アドバイスなし）" ] && [ "$advice_item" != "なし" ] && [[ "$advice_item" != なし* ]] && [[ "$advice_item" != （アドバイスなし）* ]]; then
					echo "- $advice_item" >> tmp/advice.md
				fi
				# 最新エントリ程度に制限
				if [ -f tmp/advice.md ] && [ "$(wc -l < tmp/advice.md)" -gt 150 ]; then
					tail -150 tmp/advice.md > tmp/advice.md.tmp
					mv tmp/advice.md.tmp tmp/advice.md
				fi
				log "[COMMENT] 戦略アドバイス検出 → tmp/advice.md に追記"
				# トーク本文からアドバイス部分を除去
				comments_talk=$(echo "$comments_talk" | sed '/^===ADVICE===/,$ d')
			fi

			comments_talk=$(_clean_comment_talk "$comments_talk")
			comments_talk=$(printf '%s' "$comments_talk" | _sanitize_onair_text)
			if ! _is_valid_comment_talk "$comments_talk"; then
				log "[COMMENT] 最終本文が不正/短文のため破棄"
			else
				local queue_file="$COMMENT_QUEUE_DIR/comment_$(date +%s)_${RANDOM}.txt"
				echo "$comments_talk" >"$queue_file"
				# 生成直後に重複チェック（同じ内容のキューファイルがないか）
				local new_hash
				new_hash=$(md5 -q "$queue_file" 2>/dev/null)
				if [ -n "$new_hash" ] && grep -qF "$new_hash" "$COMMENT_QUEUE_DIR/played_hashes.txt" 2>/dev/null; then
					log "[COMMENT] 重複コメント返し検出 → 破棄 (hash=$new_hash)"
					rm -f "$queue_file"
				else
					log "[COMMENT] コメント返し ${#comments_talk}字 → キュー追加: $queue_file"
				fi
			fi
		else
			log "[COMMENT] コメント返し生成失敗（次回再取得）"
		fi
		rm -f tmp/.comment_gen_state
	) &
	local comment_pid=$!
	echo "$comment_pid" >tmp/.twitch_chat/comment_gen.pid
	disown "$comment_pid"
}

#=== コメント監視デーモン ===
# 10秒ごとにTwitchコメントをポーリングし、新コメントがあれば即座に生成→キュー追加

start_comment_watcher() {
	# 既存ウォッチャーが生存中なら重複起動しない（PID + heartbeat で判定）
	if _is_comment_worker_healthy "$COMMENT_WATCHER_PID_FILE" "$COMMENT_WATCHER_HEARTBEAT_FILE" "$COMMENT_WORKER_HEALTH_TTL"; then
		return
	fi
	if [ -f "$COMMENT_WATCHER_PID_FILE" ]; then
		local stale_pid
		stale_pid=$(cat "$COMMENT_WATCHER_PID_FILE" 2>/dev/null)
		if [ -n "$stale_pid" ]; then
			log "[COMMENT] ウォッチャーPIDが不整合/停止を検出 → 再起動 (PID=$stale_pid)"
		fi
		rm -f "$COMMENT_WATCHER_PID_FILE"
	fi
	rm -f "$COMMENT_WATCHER_HEARTBEAT_FILE"
	mkdir -p "$(dirname "$COMMENT_WATCHER_PID_FILE")"

	(
		_cw_my_pid=${BASHPID:-$$}
		echo "$_cw_my_pid" > "$COMMENT_WATCHER_PID_FILE" 2>/dev/null
		while true; do
			# PIDファイルが自分でなくなったら終了
			_cw_file_pid=$(cat "$COMMENT_WATCHER_PID_FILE" 2>/dev/null)
			if [ "$_cw_file_pid" != "$_cw_my_pid" ]; then
				exit 0
			fi
			date +%s >"$COMMENT_WATCHER_HEARTBEAT_FILE" 2>/dev/null || true

			# コメント生成が進行中なら今回はスキップ
			local gen_pidfile="tmp/.twitch_chat/comment_gen.pid"
			local gen_running=false
			if [ -f "$gen_pidfile" ]; then
				local gen_pid
				gen_pid=$(cat "$gen_pidfile" 2>/dev/null)
				if [ -n "$gen_pid" ] && kill -0 "$gen_pid" 2>/dev/null; then
					gen_running=true
				fi
			fi

			if [ "$gen_running" = "true" ]; then
				# 生成中は未読を溜めるだけにして、取りこぼしを防ぐ
				./twitch_chat.sh fetch 2>/dev/null
			else
				# idle時は claim で原子的に取得して生成
				generate_comment_response
			fi

			sleep "$COMMENT_WATCHER_INTERVAL"
		done
	) &
	local wpid=$!
	echo "$wpid" > "$COMMENT_WATCHER_PID_FILE"
	disown "$wpid"
	log "[COMMENT] ウォッチャー開始 (PID=$wpid, interval=${COMMENT_WATCHER_INTERVAL}s)"
}

stop_comment_watcher() {
	if [ -f "$COMMENT_WATCHER_PID_FILE" ]; then
		local wpid
		wpid=$(cat "$COMMENT_WATCHER_PID_FILE" 2>/dev/null)
		if [ -n "$wpid" ] && [ "$wpid" != "$$" ] && kill -0 "$wpid" 2>/dev/null; then
			kill "$wpid" 2>/dev/null
			wait "$wpid" 2>/dev/null
			log "[COMMENT] ウォッチャー停止 (PID=$wpid)"
		fi
		rm -f "$COMMENT_WATCHER_PID_FILE"
	fi
	rm -f "$COMMENT_WATCHER_HEARTBEAT_FILE"
}

#=== プロセス管理 ===

# IMPROVE_PID はグローバル変数として soren_loop.sh で管理
cleanup_all() {
	log "クリーンアップ中..."
	# 状態ファイルからPIDを復元 (IMPROVE_PIDが0でも状態ファイルにPIDがある場合)
	if [ "${IMPROVE_PID:-0}" -eq 0 ] && [ -f "$IMPROVE_STATE_FILE" ]; then
		local _cleanup_pid
		_cleanup_pid=$(python3 -c "import json; print(json.load(open('$IMPROVE_STATE_FILE')).get('pid',0))" 2>/dev/null || echo 0)
		[ "${_cleanup_pid:-0}" -ne 0 ] && IMPROVE_PID=$_cleanup_pid
	fi
	# 改善プロセス停止
	if [ "${IMPROVE_PID:-0}" -ne 0 ] && kill -0 "$IMPROVE_PID" 2>/dev/null; then
		kill "$IMPROVE_PID" 2>/dev/null
		wait "$IMPROVE_PID" 2>/dev/null
	fi
	_write_improve_state "idle" "0" ""
	# コメント関連停止
	stop_comment_watcher
	_kill_comment_gen
	stop_comment_player
	# Twitchチャット停止
	./twitch_chat.sh stop 2>/dev/null
	# ロックファイル削除
	rm -f tmp/soren_loop.lock
	log "クリーンアップ完了"
}

recover_strategy_backup() {
	if [ ! -f "$STRATEGY_FILE" ] && [ -f "${STRATEGY_FILE}.bak" ]; then
		log "[RECOVER] .bak から復元"
		cp "${STRATEGY_FILE}.bak" "$STRATEGY_FILE"
	fi
}

#=== ローリングスコア & リグレッション検知 ===

_archive_strategy_snapshot_by_hash() {
	local source_file="$1" hash_value="$2"
	[ -f "$source_file" ] || return 0
	if [ -z "$hash_value" ] || [ "$hash_value" = "unknown" ]; then
		hash_value=$(python3 extract_decide_hash.py "$source_file" 2>/dev/null || echo "")
	fi
	[ -z "$hash_value" ] && return 0
	mkdir -p "$STRATEGY_HASH_ARCHIVE_DIR"
	local dst="$STRATEGY_HASH_ARCHIVE_DIR/${hash_value}.py"
	if [ ! -f "$dst" ]; then
		cp "$source_file" "$dst" 2>/dev/null || true
	fi
}

_find_strategy_file_by_hash() {
	local target_hash="$1"
	[ -z "$target_hash" ] && return 1
	if [ -f "$STRATEGY_HASH_ARCHIVE_DIR/${target_hash}.py" ]; then
		echo "$STRATEGY_HASH_ARCHIVE_DIR/${target_hash}.py"
		return 0
	fi

	local candidates=()
	[ -f "$STRATEGY_FILE" ] && candidates+=("$STRATEGY_FILE")
	[ -f "tmp/revert_strategy.py" ] && candidates+=("tmp/revert_strategy.py")
	while IFS= read -r vf; do
		[ -n "$vf" ] && candidates+=("$vf")
	done < <(ls -1t "$STRATEGY_VERSIONS_DIR"/*.py 2>/dev/null || true)

	local f h
	for f in "${candidates[@]}"; do
		[ -f "$f" ] || continue
		h=$(python3 extract_decide_hash.py "$f" 2>/dev/null || echo "")
		if [ "$h" = "$target_hash" ]; then
			echo "$f"
			return 0
		fi
	done
	return 1
}

_pick_best_rollback_candidate() {
	local current_hash="$1"
	[ -f "$ROLLING_SCORES_FILE" ] || return 1

	local ranked
	ranked=$(python3 - "$ROLLING_SCORES_FILE" "$current_hash" "$MIN_GAMES_FOR_BEST_ROLLBACK" <<'PY'
import json
import sys

rs_file, current_hash, min_games = sys.argv[1], sys.argv[2], int(sys.argv[3])
rs = json.load(open(rs_file))

rows = []
for h, data in rs.items():
    if h == current_hash:
        continue
    scores = data.get("scores", [])
    if len(scores) < min_games:
        continue
    avg = sum(scores) / len(scores)
    rows.append((avg, len(scores), h))

rows.sort(key=lambda x: (x[0], x[1]), reverse=True)
for avg, n, h in rows:
    print(f"{h}|{avg:.1f}|{n}")
PY
)
	[ -z "$ranked" ] && return 1

	local line h avg n candidate_file
	while IFS= read -r line; do
		[ -z "$line" ] && continue
		IFS='|' read -r h avg n <<<"$line"
		candidate_file=$(_find_strategy_file_by_hash "$h")
		if [ -n "$candidate_file" ]; then
			echo "${h}|${avg}|${n}|${candidate_file}"
			return 0
		fi
	done <<EOF
$ranked
EOF
	return 1
}

_prune_hash_archive_by_ranking() {
	[ -d "$STRATEGY_HASH_ARCHIVE_DIR" ] || return 0
	[ -f "$ROLLING_SCORES_FILE" ] || return 0

	local ranked_hashes
	ranked_hashes=$(python3 - "$ROLLING_SCORES_FILE" "$MIN_GAMES_FOR_BEST_ROLLBACK" "$HASH_ARCHIVE_KEEP_TOP" <<'PY'
import json
import sys

rs_file, min_games, keep_top = sys.argv[1], int(sys.argv[2]), int(sys.argv[3])
rs = json.load(open(rs_file))
rows = []
for h, data in rs.items():
    scores = data.get("scores", [])
    if len(scores) < min_games:
        continue
    avg = sum(scores) / len(scores)
    rows.append((avg, len(scores), h))
rows.sort(key=lambda x: (x[0], x[1]), reverse=True)
for _, _, h in rows[:keep_top]:
    print(h)
PY
)

	local current_hash=""
	current_hash=$(python3 extract_decide_hash.py "$STRATEGY_FILE" 2>/dev/null || echo "")
	local revert_hash=""
	if [ -f "tmp/revert_strategy.py" ]; then
		revert_hash=$(python3 extract_decide_hash.py "tmp/revert_strategy.py" 2>/dev/null || echo "")
	fi

	local keep_hashes
	keep_hashes=$(printf '%s\n%s\n%s\n' "$ranked_hashes" "$current_hash" "$revert_hash" | sed '/^$/d' | sort -u)

	local removed=0
	local f base h
	while IFS= read -r f; do
		[ -f "$f" ] || continue
		base=$(basename "$f")
		h="${base%.py}"
		if ! printf '%s\n' "$keep_hashes" | grep -qxF "$h"; then
			rm -f "$f"
			removed=$((removed + 1))
		fi
	done < <(ls -1 "$STRATEGY_HASH_ARCHIVE_DIR"/*.py 2>/dev/null || true)

	if [ "$removed" -gt 0 ]; then
		log "[HASH-ARCHIVE] pruned ${removed} file(s): keep top ${HASH_ARCHIVE_KEEP_TOP} (+current/revert)"
	fi
}

update_rolling_scores() {
	local score="$1"
	local strategy_hash
	strategy_hash=$(python3 extract_decide_hash.py "$STRATEGY_FILE" 2>/dev/null || echo "unknown")
	_archive_strategy_snapshot_by_hash "$STRATEGY_FILE" "$strategy_hash"

	python3 -c "
import json, os
rs_file = '$ROLLING_SCORES_FILE'
if os.path.exists(rs_file):
    with open(rs_file) as f:
        rs = json.load(f)
else:
    rs = {}

h = '$strategy_hash'
if h not in rs:
    rs[h] = {'scores': [], 'prev_hash': '', 'games_total': 0}
if 'games_total' not in rs[h]:
    rs[h]['games_total'] = len(rs[h].get('scores', []))
rs[h]['scores'].append(int('$score'))
rs[h]['games_total'] += 1
# 最大20試合分を保持
rs[h]['scores'] = rs[h]['scores'][-20:]

with open(rs_file, 'w') as f:
    json.dump(rs, f)
" 2>/dev/null
	_prune_hash_archive_by_ranking
}

check_regression() {
	# 新戦略が10試合以上で「平均最高ハッシュ」の85%未満ならリグレッション
	# 戻り値: 0=リグレッション検知(リバート実行済み), 1=問題なし
	REGRESSION_ROLLBACK_DONE=0
	REGRESSION_ROLLBACK_HASH=""
	local strategy_hash
	strategy_hash=$(python3 extract_decide_hash.py "$STRATEGY_FILE" 2>/dev/null || echo "unknown")

	local result
	result=$(python3 -c "
import json, os
rs_file = '$ROLLING_SCORES_FILE'
if not os.path.exists(rs_file):
    print('OK')
    exit()

with open(rs_file) as f:
    rs = json.load(f)

current_hash = '$strategy_hash'
if current_hash not in rs:
    print('OK')
    exit()

current_scores = rs[current_hash]['scores']
if len(current_scores) < $MIN_GAMES_BEFORE_IMPROVE:
    print('OK')  # データ不足
    exit()

# 比較基準は「平均最高ハッシュ」（現戦略を除外、最低試行数あり）
candidates = []
for h, data in rs.items():
    if h == current_hash:
        continue
    scores = data.get('scores', [])
    if len(scores) < $MIN_GAMES_FOR_BEST_ROLLBACK:
        continue
    avg = sum(scores) / len(scores)
    candidates.append((avg, len(scores), h))

if not candidates:
    print('OK')  # 比較基準不足
    exit()

candidates.sort(key=lambda x: (x[0], x[1]), reverse=True)
best_avg, best_n, best_hash = candidates[0]
curr_avg = sum(current_scores) / len(current_scores)

if best_avg > 0 and curr_avg < best_avg * 0.85:
    print(f'REGRESSION:best_avg={best_avg:.0f},curr_avg={curr_avg:.0f},best_hash={best_hash},best_n={best_n}')
else:
    print('OK')
" 2>/dev/null)

	if echo "$result" | grep -q '^REGRESSION:'; then
		log "[REGRESSION] リグレッション検知: $result"
		# 進行中の改善プロセスがあれば停止して、リバート後の再上書きを防ぐ
		local running_pid=0
		if [ -f "$IMPROVE_STATE_FILE" ]; then
			running_pid=$(python3 -c "import json; print(json.load(open('$IMPROVE_STATE_FILE')).get('pid',0))" 2>/dev/null || echo 0)
		fi
		if [ "${running_pid:-0}" -eq 0 ] && [ "${IMPROVE_PID:-0}" -ne 0 ]; then
			running_pid="$IMPROVE_PID"
		fi
		if [ "${running_pid:-0}" -ne 0 ] && kill -0 "$running_pid" 2>/dev/null; then
			local pid_cmd
			pid_cmd=$(ps -p "$running_pid" -o command= 2>/dev/null || echo "")
			if echo "$pid_cmd" | grep -q "eloop_improve"; then
				log "[REGRESSION] 改善プロセス停止 (PID=$running_pid)"
				kill "$running_pid" 2>/dev/null || true
				wait "$running_pid" 2>/dev/null || true
			else
				log "[REGRESSION] PID=$running_pid は改善プロセスではないため停止スキップ: $pid_cmd"
			fi
		fi
		IMPROVE_PID=0
		_write_improve_state "idle" "0" ""
		log "[REGRESSION] 自動ロールバック開始"

		# リジェクトハッシュに記録
		echo "$strategy_hash" >> "$REJECTED_HASHES_FILE"
		# 最新20件のみ保持
		if [ -f "$REJECTED_HASHES_FILE" ]; then
			tail -20 "$REJECTED_HASHES_FILE" > "$REJECTED_HASHES_FILE.tmp"
			mv "$REJECTED_HASHES_FILE.tmp" "$REJECTED_HASHES_FILE"
		fi

		# リバート先選定:
		# 1) ローリング平均で最良(十分試行数)かつ実ファイルが見つかる戦略
		# 2) 見つからなければ従来どおり直前戦略(tmp/revert_strategy.py)
		local rollback_file="" rollback_note="" rollback_hash=""
		local best_candidate
		best_candidate=$(_pick_best_rollback_candidate "$strategy_hash")
		if [ -n "$best_candidate" ]; then
			local best_avg best_n
			IFS='|' read -r rollback_hash best_avg best_n rollback_file <<<"$best_candidate"
			rollback_note="best_avg hash=${rollback_hash} avg=${best_avg} n=${best_n}"
		elif [ -f "tmp/revert_strategy.py" ]; then
			rollback_file="tmp/revert_strategy.py"
			rollback_note="previous_strategy"
		fi

		if [ -z "$rollback_file" ]; then
			log "[REGRESSION] ロールバック候補なし → 現在戦略を維持"
			return 0
		fi

		# リバート実行
		cp "$rollback_file" "$STRATEGY_FILE"
		# 次回比較の基準も現戦略に合わせる（再帰的な誤判定防止）
		cp "$STRATEGY_FILE" "tmp/revert_strategy.py" 2>/dev/null || true
		local rolled_hash
		rolled_hash=$(python3 extract_decide_hash.py "$STRATEGY_FILE" 2>/dev/null || echo "")
		_archive_strategy_snapshot_by_hash "$STRATEGY_FILE" "$rolled_hash"
		REGRESSION_ROLLBACK_DONE=1
		REGRESSION_ROLLBACK_HASH="$rolled_hash"
		log "[REGRESSION] リバート完了: ${rollback_note} (file=${rollback_file}, hash=${rolled_hash:-unknown})"

		git add -A
		git commit -m "eloop Auto-revert: regression detected ($result, target=${rollback_note})" 2>/dev/null || true

		return 0  # リグレッション検知
		fi

	return 1  # 問題なし
}

#=== 改善ステート管理 ===

_read_improve_state() {
	if [ -f "$IMPROVE_STATE_FILE" ]; then
		cat "$IMPROVE_STATE_FILE"
	else
		echo '{"status":"idle","pid":0,"strategy_hash_before":""}'
	fi
}

_write_improve_state() {
	local status="$1" pid="$2" hash="$3"
	cat >"$IMPROVE_STATE_FILE" <<EOF
{"status":"${status}","pid":${pid:-0},"strategy_hash_before":"${hash:-}"}
EOF
}

check_and_harvest_improvement() {
	local state
	state=$(_read_improve_state)
	local status
	status=$(echo "$state" | python3 -c "import json,sys; print(json.load(sys.stdin).get('status','idle'))" 2>/dev/null)

	if [ "$status" = "running" ]; then
		local pid
		pid=$(echo "$state" | python3 -c "import json,sys; print(json.load(sys.stdin).get('pid',0))" 2>/dev/null)

		# IMPROVE_PID を状態ファイルから同期 (再起動時の復元)
		if [ "${IMPROVE_PID:-0}" -eq 0 ] && [ "${pid:-0}" -ne 0 ]; then
			IMPROVE_PID=$pid
		fi

		# PID再利用チェック: eloop_improve.sh のプロセスかどうか確認
		local pid_alive=false
		if [ "${pid:-0}" -ne 0 ] && kill -0 "$pid" 2>/dev/null; then
			# プロセスが存在する場合、eloop_improve.sh のプロセスか確認
			local pid_cmd
			pid_cmd=$(ps -p "$pid" -o command= 2>/dev/null || echo "")
			if echo "$pid_cmd" | grep -q "eloop_improve"; then
				pid_alive=true
			else
				log "[IMPROVE] PID=$pid は別プロセス ($pid_cmd) → stale状態クリア"
			fi
		fi

		if [ "$pid_alive" = false ]; then
			# プロセス完了 or stale → harvest
			local hash_before
			hash_before=$(echo "$state" | python3 -c "import json,sys; print(json.load(sys.stdin).get('strategy_hash_before',''))" 2>/dev/null)
			local hash_now
			hash_now=$(md5 -q "$STRATEGY_FILE" 2>/dev/null | cut -c1-8)

			if [ "$hash_before" != "$hash_now" ]; then
				log "[IMPROVE] 戦略更新検出: $hash_before -> $hash_now"

				# リバート用候補はeloop_improve.shが tmp/revert_strategy.py に保存済み
				# ローリングスコアで新戦略のprev_hashを記録
				local new_decide_hash
				new_decide_hash=$(python3 extract_decide_hash.py "$STRATEGY_FILE" 2>/dev/null || echo "")
				if [ -n "$new_decide_hash" ] && [ -f "$ROLLING_SCORES_FILE" ]; then
					local prev_decide_hash=""
					if [ -f "tmp/revert_strategy.py" ]; then
						prev_decide_hash=$(python3 extract_decide_hash.py "tmp/revert_strategy.py" 2>/dev/null || echo "")
					fi
					python3 -c "
import json, os
rs_file = '$ROLLING_SCORES_FILE'
if os.path.exists(rs_file):
    with open(rs_file) as f:
        rs = json.load(f)
else:
    rs = {}
h = '$new_decide_hash'
if h not in rs:
    rs[h] = {'scores': [], 'prev_hash': '$prev_decide_hash', 'games_total': 0}
elif 'games_total' not in rs[h]:
    rs[h]['games_total'] = len(rs[h].get('scores', []))
with open(rs_file, 'w') as f:
    json.dump(rs, f)
" 2>/dev/null
				fi

				# 戦略が変わった → 蓄積データは旧戦略のものなので破棄
				local acc_count_discarded=0
				if [ -f "$ACCUMULATED_GAMES_FILE" ]; then
					acc_count_discarded=$(python3 -c "import json; print(json.load(open('$ACCUMULATED_GAMES_FILE')).get('count',0))" 2>/dev/null || echo 0)
				fi
				_clear_accumulated_data
				if [ "${acc_count_discarded:-0}" -gt 0 ]; then
					log "[IMPROVE] 蓄積${acc_count_discarded}試合を破棄 (旧戦略のデータ)"
				fi
			else
				log "[IMPROVE] 戦略変更なし (改善失敗 or 差分なし)"
				# 戦略が変わっていない → 蓄積データはそのまま有効
			fi

			_write_improve_state "idle" "0" ""
			IMPROVE_PID=0
			log "[IMPROVE] 改善完了 → idle"
		fi
	fi
}

accumulate_game_data() {
	local archive_file="$1" score="$2" soviet="$3"

	python3 -c "
import json, os
acc_file = '$ACCUMULATED_GAMES_FILE'
if os.path.exists(acc_file):
    with open(acc_file) as f:
        acc = json.load(f)
else:
    acc = {'files': [], 'scores': '', 'soviet': False, 'count': 0}

acc['files'].append('$archive_file')
acc['scores'] = (acc['scores'] + ' $score').strip()
if '$soviet' == 'true':
    acc['soviet'] = True
acc['count'] += 1

with open(acc_file, 'w') as f:
    json.dump(acc, f)
print(f'[ACCUMULATE] 蓄積: {acc[\"count\"]}試合')
" 2>/dev/null
}

_read_accumulated_data() {
	if [ -f "$ACCUMULATED_GAMES_FILE" ]; then
		cat "$ACCUMULATED_GAMES_FILE"
	else
		echo '{"files":[],"scores":"","soviet":false,"count":0}'
	fi
}

_clear_accumulated_data() {
	rm -f "$ACCUMULATED_GAMES_FILE"
}

_start_improvement_job() {
	local all_history_files="$1" all_scores="$2" any_soviet="$3" acc_count="$4" reason="$5"

	# 既存の eloop_improve プロセスが残っていないか確認
	local stale_pids
	stale_pids=$(pgrep -f "eloop_improve" 2>/dev/null || true)
	if [ -n "$stale_pids" ]; then
		log "[IMPROVE] WARNING: 既存の eloop_improve プロセス検出 (PIDs: $stale_pids) → kill"
		echo "$stale_pids" | xargs kill 2>/dev/null || true
		sleep 1
	fi

	if [ "$reason" = "post_regression" ]; then
		log "[IMPROVE] 回帰ロールバック直後の即時改善を開始"
	else
		log "[IMPROVE] ${acc_count}試合分のデータで改善開始"
	fi

	# Twitchコメント処理は comment watcher 側に一本化
	log "[NEWS] ニュース取得..."
	./fetch_news.sh

	# 戦略ハッシュ記録
	local strategy_hash
	strategy_hash=$(md5 -q "$STRATEGY_FILE" 2>/dev/null | cut -c1-8)

	# バックグラウンド改善開始
	./eloop_improve.sh "$all_history_files" "$all_scores" "$any_soviet" "$GAME_NUM" "$LAST_TURNS" &
	IMPROVE_PID=$!

	# 起動成功を確認してから状態更新
	if kill -0 "$IMPROVE_PID" 2>/dev/null; then
		_write_improve_state "running" "$IMPROVE_PID" "$strategy_hash"
		if [ "$reason" = "post_regression" ]; then
			log "[IMPROVE] 回帰ロールバック後の改善開始 (PID=$IMPROVE_PID, base=${REGRESSION_ROLLBACK_HASH:-unknown})"
		else
			log "[IMPROVE] バックグラウンド開始 (PID=$IMPROVE_PID, ${acc_count} 試合)"
		fi
		return 0
	else
		log "[IMPROVE] 起動失敗 (PID=$IMPROVE_PID 即死)"
		IMPROVE_PID=0
		return 1
	fi
}

trigger_adaptive_improvement() {
	# Step 1: 常にデータを蓄積 & ローリングスコア更新
	accumulate_game_data "$LAST_ARCHIVE_FILE" "$LAST_SCORE" "$LAST_SOVIET"
	update_rolling_scores "$LAST_SCORE"

	# Step 2: リグレッション検知 (新戦略が旧戦略の85%未満なら自動リバート)
	if check_regression; then
		# リグレッション検知 → リバート済み、蓄積データクリア
		_clear_accumulated_data
		# ロールバック成功時は、ロールバック戦略をベースに即時改善を走らせる
		if [ "${REGRESSION_ROLLBACK_DONE:-0}" -eq 1 ]; then
			_start_improvement_job "" "" "false" "0" "post_regression" || true
		fi
		return
	fi

	# Step 3: 改善プロセス実行中?
	local state
	state=$(_read_improve_state)
	local status
	status=$(echo "$state" | python3 -c "import json,sys; print(json.load(sys.stdin).get('status','idle'))" 2>/dev/null)

	if [ "$status" = "running" ]; then
		# PIDが本当に生きているか確認 (stale検出)
		local running_pid
		running_pid=$(echo "$state" | python3 -c "import json,sys; print(json.load(sys.stdin).get('pid',0))" 2>/dev/null)
		local still_alive=false
		if [ "${running_pid:-0}" -ne 0 ] && kill -0 "$running_pid" 2>/dev/null; then
			local pid_cmd
			pid_cmd=$(ps -p "$running_pid" -o command= 2>/dev/null || echo "")
			if echo "$pid_cmd" | grep -q "eloop_improve"; then
				still_alive=true
			fi
		fi
		if [ "$still_alive" = true ]; then
			log "[IMPROVE] 改善中 (PID=$running_pid), データ蓄積済み"
			return
		else
			log "[IMPROVE] stale検出: PID=$running_pid は既に終了 → harvest & 続行"
			check_and_harvest_improvement
		fi
	fi

	# Step 4: 最低10試合ゲート
	local acc_data
	acc_data=$(_read_accumulated_data)
	local acc_count
	acc_count=$(echo "$acc_data" | python3 -c "import json,sys; print(json.load(sys.stdin).get('count',0))" 2>/dev/null)

	if [ "${acc_count:-0}" -lt "$MIN_GAMES_BEFORE_IMPROVE" ]; then
		log "[IMPROVE] 蓄積 ${acc_count:-0}/${MIN_GAMES_BEFORE_IMPROVE} 試合 → 待機"
		return
	fi

	# Step 5: idle → 改善開始
	# 蓄積データから履歴ファイル・スコアを統合
	local all_history_files all_scores any_soviet
	all_history_files=$(echo "$acc_data" | python3 -c "import json,sys; print(' '.join(json.load(sys.stdin).get('files',[])))" 2>/dev/null)
	all_scores=$(echo "$acc_data" | python3 -c "import json,sys; print(json.load(sys.stdin).get('scores',''))" 2>/dev/null)
	any_soviet=$(echo "$acc_data" | python3 -c "import json,sys; print('true' if json.load(sys.stdin).get('soviet',False) else 'false')" 2>/dev/null)
	if _start_improvement_job "$all_history_files" "$all_scores" "$any_soviet" "$acc_count" "normal"; then
		# 通常改善のみ、起動成功後に蓄積をクリア (即死時は保持)
		_clear_accumulated_data
	fi
}
