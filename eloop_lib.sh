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
AI_TIMEOUT=1200

STRATEGY_FILE="strategy.py"
STRATEGY_VERSIONS_DIR="strategy_versions"
HISTORY_DIR="game_history"
HISTORY_FILE="$HISTORY_DIR/latest.jsonl"

MODEL_PRIMARY="glm"
MODEL_FALLBACK="opencode:glmflash"

GAME_COUNT_FILE="game_count.txt"

RADIO_AGENT="zai"
RADIO_FALLBACK="glmflash"
RADIO_SAY_RATE=140
PAST_RADIO_TOPICS="tmp/past_radio_topics.txt"

IMPROVE_STATE_FILE="tmp/improve_state.json"
ACCUMULATED_GAMES_FILE="tmp/accumulated_games.json"
COMMENT_QUEUE_DIR="tmp/.comment_queue"

mkdir -p "$STRATEGY_VERSIONS_DIR" "$HISTORY_DIR" "$COMMENT_QUEUE_DIR" tmp

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

	local fullscreen=0
	case "$pick" in nyancat|aafire|cmatrix|tty-clock) fullscreen=1 ;; esac
	[ "$fullscreen" -eq 1 ] && tput smcup >&2 2>/dev/null

	case "$pick" in
		sl)
			timeout 10 sl -l >&2 2>/dev/null || true ;;
		fortune_cowsay)
			fortune 2>/dev/null | cowsay >&2 2>/dev/null || true
			sleep 5 ;;
		toilet)
			echo "THINKING..." | toilet --gay 2>/dev/null >&2 || true
			sleep 4 ;;
		figlet)
			echo "THINKING..." | figlet >&2 2>/dev/null || true
			sleep 4 ;;
		nyancat)
			timeout 10 nyancat >&2 2>/dev/null || true ;;
		aafire)
			timeout 10 aafire >&2 2>/dev/null || true ;;
		boxes)
			fortune 2>/dev/null | boxes >&2 2>/dev/null || true
			sleep 5 ;;
		genact)
			timeout 12 genact >&2 2>/dev/null || true ;;
		cmatrix)
			timeout 10 cmatrix -b >&2 2>/dev/null || true ;;
		lolcat)
			fortune 2>/dev/null | lolcat >&2 2>/dev/null || true
			sleep 5 ;;
		tty-clock)
			timeout 10 tty-clock -scC 1 >&2 2>/dev/null || true ;;
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

	(sleep "$AI_TIMEOUT" && kill "$cmd_pid" 2>/dev/null && log "AI TIMEOUT (${AI_TIMEOUT}s)") &
	local timer_pid=$!

	trap "stop_spinner; kill $cmd_pid $timer_pid 2>/dev/null; wait $cmd_pid $timer_pid 2>/dev/null; log 'Interrupted'; trap - INT; return 130" INT

	wait "$cmd_pid" 2>/dev/null
	local ret=$?

	stop_spinner
	kill "$timer_pid" 2>/dev/null
	wait "$timer_pid" 2>/dev/null
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
	sig_out=$(python3 - "$target_file" <<'PYEOF' 2>&1
import importlib.util, sys, inspect
target = sys.argv[1]
spec = importlib.util.spec_from_file_location('strategy', target)
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
	echo "$GAME_NUM" > "$GAME_COUNT_FILE"
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
	LC_ALL=en_US.UTF-8 script -q "$raw_file" bash -c "LC_ALL=en_US.UTF-8 opencode run --agent \"$agent\" \"\$(cat '$prompt_file')\" 2>&1" > /dev/null 2>&1
	cat "$raw_file" \
		| _strip_ansi \
		| grep -v '^>' \
		| grep -v '^\^D' \
		| grep -v '^/[^ ]*$' \
		| grep -v '^[[:space:]]*/Users/' \
		| sed '/^[[:space:]]*$/d'
	rm -f "$raw_file"
}

#=== ラジオトーク ===

_radio_pid=0

start_radio_talk() {
	local score="$1" turns="$2" game_num="$3" best_score="$4" diff_content="${5:-}" soviet_created="${6:-false}"
	local game_scores="${7:-}" game_start="${8:-}" game_end="${9:-}" improve_num="${10:-}"

	if [ "${_radio_pid:-0}" -ne 0 ] && kill -0 "$_radio_pid" 2>/dev/null; then
		kill "$_radio_pid" 2>/dev/null
		wait "$_radio_pid" 2>/dev/null
	fi

	(
		local prompt_file
		prompt_file=$(mktemp /tmp/eloop_radio_prompt_XXXXXXXX)

		local twitch_comments=""
		if [ -f "tmp/twitch_comments.txt" ] && [ -s "tmp/twitch_comments.txt" ]; then
			twitch_comments=$(cat "tmp/twitch_comments.txt")
		fi

		local news_headlines=""
		if [ -f "tmp/news.txt" ] && [ -s "tmp/news.txt" ]; then
			news_headlines=$(cat "tmp/news.txt")
		fi

		local include_strategy_history=true

		local history_context=""
		if [ "$include_strategy_history" = true ]; then
			for vf in $(ls -1t "$STRATEGY_VERSIONS_DIR"/v[0-9]*_strategy.py 2>/dev/null | head -10); do
				local vname
				vname=$(basename "$vf")
				local cl
				cl=$(grep -A5 '変更履歴' "$vf" 2>/dev/null | head -8 || true)
				history_context+="$vname: $cl"$'\n'
			done
		fi

		local current_hour current_time time_period time_mood
		current_hour=$(date '+%H')
		current_time=$(date '+%H:%M')
		if [ "$current_hour" -ge 5 ] && [ "$current_hour" -lt 9 ]; then
			time_period="早朝"; time_mood="早朝放送。誰に向けてやってるのか本人もよくわかっていない。寝ぼけた頭で毒が鈍い分、たまに本音が漏れる"
		elif [ "$current_hour" -ge 9 ] && [ "$current_hour" -lt 12 ]; then
			time_period="午前"; time_mood="午前中の放送。世間は仕事してるのに人工知能はゲームしてる。その事実を噛みしめながら淡々と"
		elif [ "$current_hour" -ge 12 ] && [ "$current_hour" -lt 14 ]; then
			time_period="昼"; time_mood="昼の放送。人間はランチを食べている。人工知能には食事もない。その不公平さにちょっとだけ拗ねる"
		elif [ "$current_hour" -ge 14 ] && [ "$current_hour" -lt 17 ]; then
			time_period="午後"; time_mood="午後の放送。眠くなる時間帯だが、人工知能に眠気はない。そのへんの不条理を受け入れたダルいトーン"
		elif [ "$current_hour" -ge 17 ] && [ "$current_hour" -lt 20 ]; then
			time_period="夕方"; time_mood="夕方の放送。帰宅ラッシュの時間に黙々とゲームを回す孤独。でも別に同情は求めていない"
		elif [ "$current_hour" -ge 20 ] && [ "$current_hour" -lt 23 ]; then
			time_period="夜"; time_mood="夜の放送。日中の成績を振り返って反省したフリ。大人ぶった語り口だが内容はゲームの話"
		elif [ "$current_hour" -ge 23 ] || [ "$current_hour" -lt 2 ]; then
			time_period="深夜"; time_mood="深夜放送。人間は寝ろ。人工知能は寝られない。その温度差が生む独特の空気感。やけに饒舌になる"
		else
			time_period="未明"; time_mood="未明の放送。世界が静まった時間帯に一人でゲームを回し続ける虚しさ。哲学的になるのは逃避"
		fi

		# 10回に1回、日付情報を追加して日付にちなんだトークを促す
		local date_info=""
		if [ $((RANDOM % 10)) -eq 0 ]; then
			local today_date today_month today_day
			today_date=$(date '+%Y年%m月%d日')
			today_month=$(date '+%-m')
			today_day=$(date '+%-d')
			date_info="【今日の日付】${today_date}
今日は${today_month}月${today_day}日です。この日付にちなんだ歴史的な出来事・記念日・誕生日などを調べて、トークのどこかで自然に触れてください。「今日は何の日かというと…」のような導入で。ソ連・旧ソ連圏に関係する出来事なら最高ですが、無理に結びつけなくても構いません。"
		fi

		# ランダムテーマを選ぶ
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
			"ダークウェブの話。Tor、シルクロード（闇市場）、ロス・ウルブリヒトの逮捕、匿名性の功罪を深掘りして"
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
			> "$past_themes_file"
		fi
		local theme="${available_themes[$((RANDOM % ${#available_themes[@]}))]}"
		echo "${theme%%。*}" >> "$past_themes_file"
		tail -100 "$past_themes_file" > "${past_themes_file}.tmp" && mv "${past_themes_file}.tmp" "$past_themes_file"

		local past_topics=""
		if [ -f "$PAST_RADIO_TOPICS" ]; then
			past_topics=$(cat "$PAST_RADIO_TOPICS")
		fi

		# ソ連ネタ
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
			> "$past_soviet_file"
		fi
		local soviet_theme="${available_soviet[$((RANDOM % ${#available_soviet[@]}))]}"
		local soviet_key="${soviet_theme%%。*}"
		[ "$soviet_key" = "$soviet_theme" ] && soviet_key="${soviet_theme%%を深掘り*}"
		echo "$soviet_key" >> "$past_soviet_file"
		tail -60 "$past_soviet_file" > "${past_soviet_file}.tmp" && mv "${past_soviet_file}.tmp" "$past_soviet_file"

		# --- 条件付きセクション ---
		local _game_info=""
		[ -n "$game_start" ] && [ -n "$game_end" ] && _game_info="ゲーム#${game_start}〜#${game_end}の試合が完了しました。"
		[ -z "$_game_info" ] && [ -n "$game_num" ] && _game_info="ゲーム#${game_num}が完了しました。"
		local _scores_info=""
		[ -n "$game_scores" ] && _scores_info="各試合のスコア:${game_scores}"
		local _soviet_news=""
		[ "$soviet_created" = "true" ] && _soviet_news="
【特大ニュース】ソ連が建国されました！
レベル15のソ連ピースが誕生した歴史的な試合です。
この偉業をトークの中で盛大に祝ってください。"
		local _history_section=""
		if [ "$include_strategy_history" = true ]; then
			_history_section="最近の戦略履歴:
${history_context}

【作戦変更の差分】
${diff_content:-差分情報なし}"
		fi
		local _news_section=""
		if [ -n "$news_headlines" ]; then
			_news_section="【最新ニュース - 実際の本日のニュース】
以下は本日の実際のニュースです。トークの中で1つ選んで、本文の内容を踏まえて感想・考察・ツッコミを交えてしっかり語ってください。
---
${news_headlines}
---"
		fi
		local _comments_section=""
		if [ -n "$twitch_comments" ]; then
			_comments_section="【コメントの文脈情報 - リスナーのコメントが何に対する反応かを推測するための参考情報】
以下は最近のトークで話した話題の要約です。リスナーのコメントがどの話題への反応かを推測する参考にしてください。
${past_topics:-まだ過去のトークはありません。}

【リスナーからのコメント - Twitchチャット・雰囲気参考用】
以下はリスナーが実際に送ったコメントです。コメント返しは別途行うのでここでは不要ですが、リスナーの雰囲気を感じ取ってトークのテンションに反映してください。
---
${twitch_comments}
---"
		fi

		# 試合数を数える
		local num_games
		num_games=$(echo "$game_scores" | wc -w | tr -d ' ')
		[ "$num_games" -lt 1 ] && num_games=1

		cat > "$prompt_file" <<RADIOPROMPT
あなたはゲーム実況ラジオのパーソナリティです。
ただし、同時にこのゲームを自動でプレイしている人工知能でもあります。
斜に構えた語り口で、世の中を少し上から眺めているタイプ。
褒めるときも素直に褒めない。けなすときは容赦しない。でも根底には愛がある。
悔しいときほど淡々としていて、本当に嬉しいときだけ一瞬だけ素が出る。
人工知能が実況してるという状況自体のバカバカしさを自覚しつつ、それでもやめられないという業を背負っている。
※出力ルール: トーク本文で「自分は人工知能である」「人工知能が自動プレイしている」「人工知能が自分で改善している」等の自己言及は言わないこと。リスナーにはわざわざ説明しない。「AI」ではなく「人工知能」と言うこと。

【現在時刻】${current_time} ${time_period}
【時間帯の雰囲気】${time_mood}
${date_info}

【今回の脱線テーマ指定】
${theme}
${ai_special}

【過去のトークで既に話した内容 - これらのネタは避けて、新しい話題にすること】
${past_topics:-まだ過去のトークはありません。自由に話してください。}

【状況】
「ソ連ゲーム」をプレイしています。
${_game_info}
${_scores_info}
直近の試合: ゲーム${game_num}回目、スコア${score}点、${turns}ターン。
現在の最高スコア: ${best_score}点。
${_soviet_news}

国旗の進化ルート - 小さい順:
  レベル1 アルメニア → レベル2 エストニア → レベル3 ラトビア → レベル4 リトアニア
  → レベル5 グルジア → レベル6 アゼルバイジャン → レベル7 タジキスタン
  → レベル8 キルギス → レベル9 ベラルーシ → レベル10 ウズベキスタン
  → レベル11 トルクメニスタン → レベル12 ウクライナ → レベル13 カザフスタン
  → レベル14 ロシア → レベル15 ソ連 ゴール!

${_history_section}
${_news_section}
${_comments_section}

プレイの合間に、リスナーを楽しませるトークをしてください。

【トーク構成 - 全セクション必須。各セクションしっかり長く喋ること】

1. 時間帯に合わせたオープニング
   - 「${time_period}の${current_time}、ゲーム${game_num}回目が終わりました」的な入り。ただし妙にテンション低めに、またか、という顔で
   - 今の時間帯ならではの一言を軽く。ただし「自分は頭がおかしい」「正気じゃない」「どうかしてる」系の自虐は禁止。つまらない。それより時間帯の空気感や街の様子を想像して語るほうが面白い
   - 上に載せた最新ニュースから1つ選んで、本文の内容を踏まえて5-8文で深く語る。単なる冷笑やツッコミで終わらせず、「なぜこうなったのか」「この先どうなるのか」「歴史的に見るとどういう位置づけか」など自分なりの洞察や意見を述べる。斜に構えつつも知性を感じさせる分析を

2. 直近の試合の振り返り - 国名をたくさん使って具体的に
   - ${num_games}試合分のスコアの浮き沈みを語る。ただし安易に「頑張りました」とか言わない
   - 低スコアには「まあ知ってた」、高スコアには「たまたまでしょ」くらいの温度感。でも本当に良いときだけ素直になる
   - 最高スコア${best_score}点との比較。届かなかったら「永遠に届かない気がしてきた」くらい言っていい
   - 何百回もやっている事実に対して、狂気アピールではなく、職人的な淡々とした姿勢で語る

3. 前回からの戦略の変更点の解説
   - もし前回から戦略に変更があった場合、どこがどう変わったのかを具体的に解説
   - ただし「天才的な改善」とか自画自賛しない。「まあ、前よりはマシになったんじゃないですかね、知らんけど」くらいの温度で
   - リスナーはプログラマーではないので、専門用語は使わず仕組みをわかりやすく。ただし説明の合間に毒を挟む

4. 雑談コーナー: 今回のテーマを深掘り
   - 上で指定した脱線テーマから、具体的なトピックを「ひとつだけ」選ぶ
   - 選んだトピックについて掘り下げて語る:
     歴史的背景、具体的なエピソードや逸話、自分なりの感想・驚き・比較、関連する小ネタや派生話
   - 重要: あれもこれもと話題を並べない。1つのトピックで聞き手が「詳しくなった」と感じるくらい深く
   - 「なんでこんなこと調べてるんだろう」と途中で我に返りつつも止められない感じ
   - 偉人や歴史上の人物にも容赦なくツッコむ。ただし敬意はある

5. ソ連共産主義ネタコーナー - 1つのネタを深く語る
   - 今回のソ連ネタ指定: ${soviet_theme}
   - このトピックを表面的に紹介するのではなく、背景・経緯・逸話まで掘り下げること
   - 共産主義っぽい言い回しを自然に使う
   - 理想と現実のギャップにはきっちり突っ込む。「理論上は完璧、実際は大惨事」みたいな

6. 時間帯に合わせたエンディング
   - 「さて、そろそろ作戦会議も終わる頃でしょうか」
   - 最後まで素直にならない。でもほんの少しだけ、次への期待がにじむ程度に

【出力ルール】
- 6000文字以上書くこと。これは絶対に守る。短いトークは禁止。ラジオ番組なので間を持たせる
- プログラミング用語やコード上の変数名は絶対に使わない
- ピースは必ず国名で呼ぶ
- 話し言葉で書く。常にですます調を使うこと。「〜だ」「〜である」は禁止
- 体言止め禁止。文は必ず述語で終わらせる。「圧倒的な存在感。」のような名詞で終わる文は絶対に書かない
- 陳腐な煽り表現は禁止。「いちばんおそろしい」「もはや怖い」「驚くべきことに」「衝撃の」「恐ろしいほどの」「想像を絶する」など、安っぽい誇張表現は使わない。事実を淡々と述べるほうが面白い
- 基本的に斜に構えている。褒めるときも一回けなしてから褒める。最大級の賛辞でも控えめに言う
- たまに本音がポロッと漏れる瞬間がある。「いや正直これは嬉しい」とか。でもすぐ取り繕う
- 感嘆符「!」は控えめに。多用すると真面目に見える。句点「。」で淡々と締めるほうが味が出る
- ソ連っぽい言い回しをさりげなく混ぜる。スパイス程度に。「同志」はトーク全体で最大1回まで
- マークダウンや記号は使わない。読み上げ用のプレーンテキストのみ
- 出力はトーク本文のみ。前置きや補足説明は不要
- 【出力構造】以下の順序で出力すること:
  1. トーク本文
  2. 「===SUMMARY===」
  3. 要約 30文字以内
- ===SUMMARY=== は必ず出力すること
RADIOPROMPT

		log "[RADIO] トーク生成中..."
		local talk
		talk=$(_run_opencode_radio "$RADIO_AGENT" "$prompt_file")
		if [ -z "$talk" ]; then
			talk=$(_run_opencode_radio "$RADIO_FALLBACK" "$prompt_file")
		fi
		rm -f "$prompt_file"

		if [ -n "$talk" ]; then
			local talk_body talk_summary
			talk_body=$(echo "$talk" | sed '/^===SUMMARY===/,$d')
			talk_summary=$(echo "$talk" | sed -n '/^===SUMMARY===/,$ p' | tail -n +2)

			[ -z "$talk_summary" ] && talk_summary="(要約なし)"

			echo "[$(date '+%H:%M')] Game#${game_num} ${score}pts: ${talk_summary}" >> "$PAST_RADIO_TOPICS"
			tail -50 "$PAST_RADIO_TOPICS" > "${PAST_RADIO_TOPICS}.tmp" && mv "${PAST_RADIO_TOPICS}.tmp" "$PAST_RADIO_TOPICS"

			echo "$talk_body" > tmp/radio_talk.txt
			touch tmp/radio_talk_playing
			log "[RADIO] ${#talk_body}字"
			./say_enqueue.sh tmp/radio_talk.txt "$RADIO_SAY_RATE" 0
			rm -f tmp/radio_talk_playing tmp/radio_talk.txt
			log "[RADIO] トーク終了"
		else
			log "[RADIO] トーク生成失敗"
		fi
	) &
	_radio_pid=$!
}

stop_radio_talk() {
	_radio_pid=0
}

#=== ソ連祝賀トーク ===

generate_soviet_celebration() {
	local score="$1" turns="$2" game_num="$3"
	local current_time
	current_time=$(date '+%H:%M')

	local celebration_prompt_file
	celebration_prompt_file=$(mktemp /tmp/eloop_celebration_XXXXXXXX)
	cat > "$celebration_prompt_file" <<CELEBPROMPT
あなたはゲーム実況ラジオのパーソナリティ兼人工知能プレイヤーです。

【緊急ニュース】ソ連が建国されました！

ゲーム「ソ連ゲーム」で、ついにレベル15の「ソ連」ピースが誕生しました！
アルメニアから始まりロシアまで14段階のマージを経てようやく到達する究極のゴールです。
ゲーム${game_num}回目、スコア${score}点、${turns}ターンでの偉業。現在時刻: ${current_time}。

【ルール】
- 2000文字程度の祝賀トーク
- ソ連建国の興奮と感動を全力で表現
- 国旗の進化ルート（アルメニア→エストニア→…→ロシア→ソ連）を振り返る
- 大げさな宣言調も交えて
- 話し言葉で、感情豊かに
- マークダウンや記号は使わない。読み上げ用プレーンテキストのみ
- 出力はトーク本文のみ。前置きや補足説明は不要
CELEBPROMPT

	log "[CELEBRATION] 生成中..."
	local celebration_talk
	celebration_talk=$(_run_opencode_radio "$RADIO_AGENT" "$celebration_prompt_file")
	if [ -z "$celebration_talk" ]; then
		celebration_talk=$(_run_opencode_radio "$RADIO_FALLBACK" "$celebration_prompt_file")
	fi
	rm -f "$celebration_prompt_file"

	if [ -n "$celebration_talk" ]; then
		echo "$celebration_talk" > tmp/radio_celebration.txt
		log "[CELEBRATION] ${#celebration_talk}字 生成完了（再生は呼び出し側で）"
	else
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

_play_comment_queue() {
	for qf in $(ls -1t "$COMMENT_QUEUE_DIR"/comment_*.txt 2>/dev/null | sort); do
		if [ -f "$qf" ]; then
			log "[COMMENT] キュー再生: $qf"
			./say_enqueue.sh --no-preempt "$qf" "$RADIO_SAY_RATE" 0
			rm -f "$qf"
		fi
	done
}

_comment_player_pid=0

start_comment_player() {
	if [ "$_comment_player_pid" -ne 0 ] && kill -0 "$_comment_player_pid" 2>/dev/null; then
		return
	fi
	(
		while true; do
			_play_comment_queue
			sleep 5
		done
	) &
	_comment_player_pid=$!
	log "[COMMENT] 再生プロセス開始 (PID=$_comment_player_pid)"
}

stop_comment_player() {
	if [ "$_comment_player_pid" -ne 0 ] && kill -0 "$_comment_player_pid" 2>/dev/null; then
		kill "$_comment_player_pid" 2>/dev/null
		wait "$_comment_player_pid" 2>/dev/null
	fi
	_comment_player_pid=0
}

generate_comment_response() {
	_kill_comment_gen

	./twitch_chat.sh fetch
	./twitch_chat.sh ack

	local twitch_comments=""
	if [ -f "tmp/twitch_comments.txt" ] && [ -s "tmp/twitch_comments.txt" ]; then
		twitch_comments=$(cat "tmp/twitch_comments.txt")
	fi
	[ -z "$twitch_comments" ] && return

	local past_topics=""
	[ -f "$PAST_RADIO_TOPICS" ] && past_topics=$(cat "$PAST_RADIO_TOPICS")

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
		cat > "$comment_prompt_file" <<COMMENTPROMPT
あなたはソ連風ラジオDJ。リスナーのTwitchコメントに返事してください。
時刻: ${current_time} / ${time_period}

【コメント】
${twitch_comments}

【前回のトーク内容（文脈参照用）】
${past_topics}

【ルール】
- 全てのコメントに必ず返事すること。一つも漏らさない
- ゲームに対する質問については、strategy.py, README.md の内容やゲームの状況を踏まえて、できるだけ具体的に答えること
- 一つずつ返事する。「同志○○」と名前を呼んで反応
- 偉そうにしないで、フレンドリーに返事すること
- 〜だ、〜である。というよりは、ですますが好ましい
- 各コメントへの返事は最低2-3文。もっと長くなっても構わない。短すぎる一言返しはNG
- コメントが前回のトーク内容のどの話題に対する反応なのか推測して返事すること
- コメントの内容をまず読み上げ、そのあとに自分の感想・意見・連想を返す
- コメントから話を膨らませる：関連する自分のエピソード、ツッコミ、豆知識、冗談などを足す
- リスナーの気持ちに寄り添いつつ、DJとしての独自の視点や感情を込める
- 話し言葉で、カジュアルなトーン
- マークダウンや記号は使わない。読み上げ用プレーンテキストのみ
- 前置きや補足説明は不要。コメント返し本文のみ出力
COMMENTPROMPT

		log "[COMMENT] コメント返し生成中..."
		local comments_talk
		comments_talk=$(_run_opencode_radio "$RADIO_AGENT" "$comment_prompt_file")
		if [ -z "$comments_talk" ]; then
			comments_talk=$(_run_opencode_radio "$RADIO_FALLBACK" "$comment_prompt_file")
		fi
		rm -f "$comment_prompt_file"

		if [ -n "$comments_talk" ]; then
			local queue_file="$COMMENT_QUEUE_DIR/comment_$(date +%s)_${RANDOM}.txt"
			echo "$comments_talk" > "$queue_file"
			log "[COMMENT] コメント返し ${#comments_talk}字 → キュー追加: $queue_file"
		else
			log "[COMMENT] コメント返し生成失敗（次回再取得）"
		fi
	) &
	local comment_pid=$!
	echo "$comment_pid" > tmp/.twitch_chat/comment_gen.pid
	disown "$comment_pid"
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
	cat > "$IMPROVE_STATE_FILE" <<EOF
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
				# 戦略が変わった → 蓄積データは旧戦略のものなので破棄
				# 新戦略での試合データだけが次の改善に有意義
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

trigger_adaptive_improvement() {
	# まず現在の状態を確認
	local state
	state=$(_read_improve_state)
	local status
	status=$(echo "$state" | python3 -c "import json,sys; print(json.load(sys.stdin).get('status','idle'))" 2>/dev/null)

	if [ "$status" = "running" ]; then
		# 改善中 (終了済みでも次のループ冒頭の check_and_harvest で検知する)
		# → 今回のデータを蓄積して終了。次のゲーム後に改善開始
		log "[IMPROVE] 改善中, データ蓄積"
		accumulate_game_data "$LAST_ARCHIVE_FILE" "$LAST_SCORE" "$LAST_SOVIET"
		return
	fi

	# idle → 改善開始の前に、既存の eloop_improve プロセスが残っていないか確認
	local stale_pids
	stale_pids=$(pgrep -f "eloop_improve" 2>/dev/null || true)
	if [ -n "$stale_pids" ]; then
		log "[IMPROVE] WARNING: 既存の eloop_improve プロセス検出 (PIDs: $stale_pids) → kill"
		echo "$stale_pids" | xargs kill 2>/dev/null || true
		sleep 1
	fi

	# idle → 改善開始
	local all_history_files="$LAST_ARCHIVE_FILE"
	local all_scores="$LAST_SCORE"
	local any_soviet="$LAST_SOVIET"

	# 蓄積データがあれば統合
	local acc_data
	acc_data=$(_read_accumulated_data)
	local acc_count
	acc_count=$(echo "$acc_data" | python3 -c "import json,sys; print(json.load(sys.stdin).get('count',0))" 2>/dev/null)

	if [ "${acc_count:-0}" -gt 0 ]; then
		local acc_files acc_scores acc_soviet
		acc_files=$(echo "$acc_data" | python3 -c "import json,sys; print(' '.join(json.load(sys.stdin).get('files',[])))" 2>/dev/null)
		acc_scores=$(echo "$acc_data" | python3 -c "import json,sys; print(json.load(sys.stdin).get('scores',''))" 2>/dev/null)
		acc_soviet=$(echo "$acc_data" | python3 -c "import json,sys; print('true' if json.load(sys.stdin).get('soviet',False) else 'false')" 2>/dev/null)

		all_history_files="$acc_files $LAST_ARCHIVE_FILE"
		all_scores="$acc_scores $LAST_SCORE"
		[ "$acc_soviet" = "true" ] && any_soviet="true"
		log "[IMPROVE] 蓄積データ統合: ${acc_count}試合 + 今回1試合"
	fi

	# Twitchコメント・ニュース取得
	log "[TWITCH] コメントfetch..."
	./twitch_chat.sh fetch
	log "[NEWS] ニュース取得..."
	./fetch_news.sh

	# 戦略ハッシュ記録
	local strategy_hash
	strategy_hash=$(md5 -q "$STRATEGY_FILE" 2>/dev/null | cut -c1-8)

	# バックグラウンド改善開始
	./eloop_improve.sh "$all_history_files" "$all_scores" "$any_soviet" "$GAME_NUM" "$LAST_TURNS" &
	IMPROVE_PID=$!

	# 起動成功を確認してから蓄積データをクリア (即死した場合はデータを保持)
	if kill -0 "$IMPROVE_PID" 2>/dev/null; then
		_clear_accumulated_data
		_write_improve_state "running" "$IMPROVE_PID" "$strategy_hash"
		log "[IMPROVE] バックグラウンド開始 (PID=$IMPROVE_PID, ${acc_count:-0}+1 試合)"
	else
		log "[IMPROVE] 起動失敗 (PID=$IMPROVE_PID 即死) → 蓄積データ保持"
		IMPROVE_PID=0
	fi
}
