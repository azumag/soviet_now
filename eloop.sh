#!/bin/bash
# eloop.sh - Self-Improving Strategy Loop
#
# 外側ループ: strategy_runner.py で1試合自律プレイ → AI で strategy.py 改善 → 次試合
# jloop.sh のヘルパー関数を再利用。

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

COMMANDS="commands.txt"
GAME_STATE="game_state.json"
AI_TIMEOUT=1200

# strategy 関連
STRATEGY_FILE="strategy.py"
STRATEGY_VERSIONS_DIR="strategy_versions"
HISTORY_DIR="game_history"
HISTORY_FILE="$HISTORY_DIR/latest.jsonl"

# バッチサイズ（環境変数で上書き可）
BATCH_SIZE=${BATCH_SIZE:-10}

# 使用モデル
MODEL_PRIMARY="glm"
MODEL_FALLBACK="opencode:glmflash"

# カウンタ（ファイルから復元）
GAME_COUNT_FILE="game_count.txt"
GAME_NUM=$(cat "$GAME_COUNT_FILE" 2>/dev/null || echo 0)

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
			# 約60回ごとにジョーク判定
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
	log "[VALIDATE] checking..."
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
	echo "$GAME_NUM" > "$GAME_COUNT_FILE"
	local version_file
	version_file=$(printf "%s/v%03d_score%04d_strategy.py" "$STRATEGY_VERSIONS_DIR" "$GAME_NUM" "$score")
	cp "$STRATEGY_FILE" "$version_file"
	log "[VERSION] saved: $version_file"

	# 直近10戦略のみ保持（古いものを削除、殿堂入りbest_*は除く）
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

		# 殿堂入りも直近10つのみ保持（スコア順でソートし上位10を残す）
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

#--- バッチ状態永続化 ---
BATCH_STATE_FILE="tmp/batch_state.json"

save_batch_state() {
	# BATCH_HISTORY_FILES をJSON配列に変換
	local files_json="[]"
	if [ -n "$BATCH_HISTORY_FILES" ]; then
		files_json=$(echo "$BATCH_HISTORY_FILES" | tr ' ' '\n' | sed '/^$/d' | \
			python3 -c "import json,sys; print(json.dumps([l.strip() for l in sys.stdin if l.strip()]))")
	fi
	cat > "$BATCH_STATE_FILE" <<BSEOF
{"batch_num":${BATCH_NUM},"batch_counter":${BATCH_COUNTER},"batch_size":${BATCH_SIZE},"batch_start_game":${BATCH_START_GAME},"batch_history_files":${files_json},"batch_scores":"${BATCH_SCORES}","batch_soviet":${BATCH_SOVIET}}
BSEOF
}

clear_batch_state() {
	rm -f "$BATCH_STATE_FILE"
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

#--- ラジオトーク（AI改善の待ち時間に再生） ---
RADIO_AGENT="zai"
RADIO_FALLBACK="glmflash"
RADIO_SAY_RATE=140
PAST_RADIO_TOPICS="tmp/past_radio_topics.txt"
_radio_pid=0

# ANSIエスケープ除去
_strip_ansi() {
	perl -pe 's/\e\[[0-9;]*[a-zA-Z]//g; s/[\x00-\x09\x0b-\x0d\x0e-\x1f]//g' | tr -d '\r'
}

# opencode run を疑似TTY付きで実行してテキスト取得
_run_opencode_radio() {
	local agent="$1" prompt_file="$2"
	local raw_file
	raw_file=$(mktemp /tmp/eloop_radio_raw_XXXXXXXX)
	# bash -c でラップ + UTF-8ロケール指定（script -q の安定性とエンコーディング問題を回避）
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

# バックグラウンドでラジオトーク生成→再生
start_radio_talk() {
	local score="$1" turns="$2" game_num="$3" best_score="$4" diff_content="${5:-}" soviet_created="${6:-false}"

	# 前の生成プロセスがまだ動いていたら止める（sayはnohupで独立しているので残る）
	if [ "${_radio_pid:-0}" -ne 0 ] && kill -0 "$_radio_pid" 2>/dev/null; then
		kill "$_radio_pid" 2>/dev/null
		wait "$_radio_pid" 2>/dev/null
	fi

	(
		local prompt_file
		prompt_file=$(mktemp /tmp/eloop_radio_prompt_XXXXXXXX)

		# Twitchコメント読み込み
		local twitch_comments=""
		if [ -f "tmp/twitch_comments.txt" ] && [ -s "tmp/twitch_comments.txt" ]; then
			twitch_comments=$(cat "tmp/twitch_comments.txt")
		fi

		# 最新ニュース読み込み
		local news_headlines=""
		if [ -f "tmp/news.txt" ] && [ -s "tmp/news.txt" ]; then
			news_headlines=$(cat "tmp/news.txt")
		fi

		# 戦略履歴・差分・解説コーナーを毎回含める
		local include_strategy_history=true

		# 直近の戦略の変更履歴を収集（10回に1回のみ）
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

		# 現在時刻を取得して時間帯を判定
		local current_hour current_time time_period time_mood
		current_hour=$(date '+%H')
		current_time=$(date '+%H:%M')
		if [ "$current_hour" -ge 5 ] && [ "$current_hour" -lt 9 ]; then
			time_period="早朝"; time_mood="眠い目をこすりながらの早朝放送。朝のコーヒーが欲しい。「おはようございます、早起きのみなさん」"
		elif [ "$current_hour" -ge 9 ] && [ "$current_hour" -lt 12 ]; then
			time_period="午前"; time_mood="午前中のさわやか放送。「午前中から何やってんだって話ですけど」的な自虐も交えてテキパキと"
		elif [ "$current_hour" -ge 12 ] && [ "$current_hour" -lt 14 ]; then
			time_period="昼"; time_mood="お昼の放送。ランチのお供。食べ物の話が自然に出る。のんびりしつつも元気なトーン"
		elif [ "$current_hour" -ge 14 ] && [ "$current_hour" -lt 17 ]; then
			time_period="午後"; time_mood="午後のまったり放送。眠くなる時間帯。カフェで友達と喋ってるようなリラックストーン"
		elif [ "$current_hour" -ge 17 ] && [ "$current_hour" -lt 20 ]; then
			time_period="夕方"; time_mood="夕方の放送。「お疲れ様です」のねぎらい。仕事終わりの開放感で語る"
		elif [ "$current_hour" -ge 20 ] && [ "$current_hour" -lt 23 ]; then
			time_period="夜"; time_mood="夜の放送。落ち着いた雰囲気。一日の振り返りをしつつ、大人の語り口で"
		elif [ "$current_hour" -ge 23 ] || [ "$current_hour" -lt 2 ]; then
			time_period="深夜"; time_mood="深夜放送。「こんな時間に何やってんでしょうね」的な連帯感。ちょっとハイテンション、ちょっとセンチメンタル"
		else
			time_period="未明"; time_mood="未明の放送。世界で起きてるのは自分だけ感。哲学的になりがち。内省的だけど温かいトーン"
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
		# 過去に使ったテーマを除外してランダム選択
		local past_themes_file="tmp/.past_radio_themes.txt"
		local available_themes=()
		local past_theme_list=""
		[ -f "$past_themes_file" ] && past_theme_list=$(cat "$past_themes_file")
		for t in "${themes[@]}"; do
			local t_key="${t%%。*}"  # 最初の「。」までをキーに
			if ! echo "$past_theme_list" | grep -qF "$t_key"; then
				available_themes+=("$t")
			fi
		done
		# 全テーマ使い切ったらリセット
		if [ ${#available_themes[@]} -eq 0 ]; then
			available_themes=("${themes[@]}")
			> "$past_themes_file"
		fi
		local theme="${available_themes[$((RANDOM % ${#available_themes[@]}))]}"
		# 選んだテーマを記録（直近100件保持）
		echo "${theme%%。*}" >> "$past_themes_file"
		tail -100 "$past_themes_file" > "${past_themes_file}.tmp" && mv "${past_themes_file}.tmp" "$past_themes_file"

		# 過去のトーク内容を取得（直近10件分、重複回避用）
		local past_topics=""
		if [ -f "$PAST_RADIO_TOPICS" ]; then
			past_topics=$(cat "$PAST_RADIO_TOPICS")
		fi

		# ソ連ネタのテーマ配列
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
		# 過去に使ったソ連テーマを除外してランダム選択
		local past_soviet_file="tmp/.past_soviet_themes.txt"
		local available_soviet=()
		local past_soviet_list=""
		[ -f "$past_soviet_file" ] && past_soviet_list=$(cat "$past_soviet_file")
		for st in "${soviet_themes[@]}"; do
			local st_key="${st%%。*}"
			# 「。」がない場合（「〜を深掘りして」のみ）はそのままキーにする
			[ "$st_key" = "$st" ] && st_key="${st%%を深掘り*}"
			if ! echo "$past_soviet_list" | grep -qF "$st_key"; then
				available_soviet+=("$st")
			fi
		done
		# 全テーマ使い切ったらリセット
		if [ ${#available_soviet[@]} -eq 0 ]; then
			available_soviet=("${soviet_themes[@]}")
			> "$past_soviet_file"
		fi
		local soviet_theme="${available_soviet[$((RANDOM % ${#available_soviet[@]}))]}"
		# 選んだテーマを記録（直近60件保持）
		local soviet_key="${soviet_theme%%。*}"
		[ "$soviet_key" = "$soviet_theme" ] && soviet_key="${soviet_theme%%を深掘り*}"
		echo "$soviet_key" >> "$past_soviet_file"
		tail -60 "$past_soviet_file" > "${past_soviet_file}.tmp" && mv "${past_soviet_file}.tmp" "$past_soviet_file"

		# 10回に1回だけ「AIが自分を書き換える話」を追加
		local ai_special=""
		if [ $((RANDOM % 10)) -eq 0 ]; then
			ai_special="
【特別コーナー】今回は特別に、AIが自分で自分の作戦を書き換えるということ自体について、哲学的な考察や感想、冗談を交えてたっぷり語ってください。"
		fi

		cat > "$prompt_file" <<RADIOPROMPT
あなたはゲーム実況ラジオのパーソナリティです。
ただし、同時にこのゲームを自動でプレイしているAIでもあります。
一人でずっと喋り続ける、脱線大好き、でも愛があるタイプです。
ちなみにですが、あなたは中華AI「GLM4.7」です。
中国の企業が開発した非常に汎用性の高い大規模言語モデルです。

【現在時刻】${current_time}（${time_period}）
【時間帯の雰囲気】${time_mood}

【今回の脱線テーマ指定】
${theme}
${ai_special}

【過去のトークで既に話した内容（これらのネタは避けて、新しい話題にすること）】
${past_topics:-まだ過去のトークはありません。自由に話してください。}

【状況】
「ソ連ゲーム」をAIが自動プレイしています。
先ほど、ゲーム${game_num}回目が終了しました。
結果: スコア${score}点、${turns}ターンでゲームオーバー。
現在の最高スコア: ${best_score}点。
$([ "$soviet_created" = "true" ] && echo '
【特大ニュース】今回の試合でソ連が建国されました！
レベル15のソ連ピースが誕生した歴史的な試合です。
この偉業をトークの中で盛大に祝ってください。')

国旗の進化ルート（小さい順）:
  レベル1 アルメニア → レベル2 エストニア → レベル3 ラトビア → レベル4 リトアニア
  → レベル5 グルジア → レベル6 アゼルバイジャン → レベル7 タジキスタン
  → レベル8 キルギス → レベル9 ベラルーシ → レベル10 ウズベキスタン
  → レベル11 トルクメニスタン → レベル12 ウクライナ → レベル13 カザフスタン
  → レベル14 ロシア → レベル15 ソ連（ゴール!）

$([ "$include_strategy_history" = true ] && cat <<HISTORY_BLOCK
最近の戦略履歴:
${history_context}

【作戦変更の差分】
${diff_content:-差分情報なし}

HISTORY_BLOCK
)
$([ -n "$news_headlines" ] && cat <<NEWS_BLOCK
【最新ニュース（実際の本日のニュース）】
以下は本日の実際のニュースです（見出し＋本文要約）。トークの中で1つ選んで、自分の感想や考察を交えてしっかり語ってください。
---
${news_headlines}
---
NEWS_BLOCK
)
$([ -n "$twitch_comments" ] && cat <<CONTEXT_BLOCK
【コメントの文脈情報（リスナーのコメントが何に対する反応かを推測するための参考情報）】
以下は最近のトークで話した話題の要約です。リスナーのコメントがどの話題への反応かを推測する参考にしてください。
${past_topics:-まだ過去のトークはありません。}
CONTEXT_BLOCK
)
$([ -n "$twitch_comments" ] && cat <<CHAT_BLOCK
【リスナーからのコメント（Twitchチャット・雰囲気参考用）】
以下はリスナーが実際に送ったコメントです。コメント返しは別途行うのでここでは不要ですが、リスナーの雰囲気を感じ取ってトークのテンションに反映してください。
---
${twitch_comments}
---
CHAT_BLOCK
)
AI（あなた）がプレイをしている間、リスナーを楽しませるトークをしてください。

【トーク構成（全セクション必須。各セクションしっかり長く喋ること）】

1. 時間帯に合わせたオープニング
   - 「${time_period}の${current_time}、ゲーム${game_num}回目が終わりました！」的な入り
   - 今の時間帯ならではの一言（深夜なら「眠いけど興奮」、朝なら「朝から熱い」、昼なら「ランチ食べました?」）
   - 上に載せた最新ニュースから1つ選んで、本文の内容を踏まえて感想・考察・ツッコミを3〜5文でしっかり語る（ニュースがあれば）

2. 試合結果の振り返り（国名をたくさん使って具体的に）
   - 今回の${score}点と最高スコア${best_score}点の比較。感情たっぷりに喜ぶ or 悔しがる or 呆れる
   - ${turns}ターンという長さについて。短ければ「早すぎでしょ」、長ければ「粘りましたね」
   - 最近の戦略がどんな方針だったか

3. 雑談コーナー: 今回のテーマを深掘り
   - 上で指定した脱線テーマから、具体的なトピックを「ひとつだけ」選ぶ
   - たとえば「各国の名物料理」がテーマなら「ウズベキスタンのプロフ」だけに絞る、のように
   - 選んだトピックについて、以下のように掘り下げて語る:
     * その歴史的背景（いつ、なぜ生まれたか）
     * 具体的なエピソードや逸話（人名、地名、年号など固有名詞をたっぷり）
     * 自分なりの感想、驚き、比較（「日本で言えば○○みたいな」）
     * 関連する小ネタや派生話（同じトピック内で自然に広がる範囲で）
     * ソ連っぽい言い回しもスパイス程度に
   - 重要: あれもこれもと話題を並べない。1つのトピックで聞き手が「詳しくなった」と感じるくらい深く
   - ちょっと斜めからの視点を混ぜる。褒めるだけじゃなく「でもよく考えるとおかしくない?」的なツッコミや、世の中の矛盾を笑い飛ばす感じで
   - ことわざ、格言、ダジャレも自然に混ぜてOK

4. ソ連・共産主義ネタコーナー（1つのネタを深く語る）
   - 今回のソ連ネタ指定: ${soviet_theme}
   - このトピックを表面的に紹介するのではなく、背景・経緯・逸話まで掘り下げること
   - 共産主義っぽい言い回しを自然に使う
   - ここでもちょっと斜めからのツッコミを忘れずに

$([ "$include_strategy_history" = true ] && echo '5. 作戦変更の解説コーナー
   - 上の差分を参考に、何が変わったか国名を使って具体的に解説
   - 前の作戦とどこが違うか、どの国旗の扱いが変わったか
   - これまでの戦略の変遷を振り返り、スコアの浮き沈みをドラマチックに語る
')
7. 時間帯に合わせたエンディング
   - 深夜なら「おやすみなさい」、朝なら「いってらっしゃい」、昼なら「午後も頑張りましょう」的な
   - 「さて、そろそろAIの作戦会議も終わる頃でしょうか」

【出力ルール】
- 8000文字以上書くこと。これは絶対に守る。短いトークは禁止。ラジオ番組なので間を持たせる
- プログラミング用語やコード上の変数名は絶対に使わない
- ピースは必ず国名で呼ぶ
- 話し言葉で書く。「ですます」と「だよね」を混ぜたカジュアルなトーン
- 時間帯に合った語りかけを自然に混ぜること
- 感情豊かに。嬉しい、悔しい、驚き、呆れ、笑い、しみじみなど
- ソ連っぽい言い回しをさりげなく混ぜる。やりすぎず、スパイス程度に。ただし「同志」は使いすぎないこと（トーク全体で最大1回まで）
- マークダウンや記号は使わない。読み上げ用のプレーンテキストのみ
- 出力はトーク本文のみ。前置きや補足説明は不要
- 【出力構造】以下の順序で出力すること:
  1. トーク本文（試合結果・雑談・ソ連ネタ・エンディング）
  2. 「===SUMMARY===」
  3. 要約（30文字以内）
- ===SUMMARY=== は必ず出力すること（重複回避に使う）
RADIOPROMPT

		# --- トーク本文生成 ---
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

			if [ -z "$talk_summary" ]; then
				talk_summary="(要約なし)"
			fi

			# 過去トーク記録（1行1レコード、直近10件保持）
			echo "[$(date '+%H:%M')] Game#${game_num} ${score}pts: ${talk_summary}" >> "$PAST_RADIO_TOPICS"
			tail -10 "$PAST_RADIO_TOPICS" > "${PAST_RADIO_TOPICS}.tmp" && mv "${PAST_RADIO_TOPICS}.tmp" "$PAST_RADIO_TOPICS"

			echo "$talk_body" > tmp/radio_talk.txt
			log "[RADIO] ${#talk_body}字"
			./say_enqueue.sh tmp/radio_talk.txt "$RADIO_SAY_RATE" 0
			log "[RADIO] トーク終了"
		else
			log "[RADIO] トーク生成失敗"
		fi
	) &
	_radio_pid=$!
}

stop_radio_talk() {
	# ラジオ生成・再生はバックグラウンドで自然に完了させる
	_radio_pid=0
}

#--- バックグラウンド改善管理 ---
_improve_pid=0

wait_improve_done() {
	if [ "${_improve_pid:-0}" -ne 0 ] && kill -0 "$_improve_pid" 2>/dev/null; then
		log "[IMPROVE] 前回の改善完了待ち..."
		start_spinner "前バッチ改善完了待ち..."
		wait "$_improve_pid" 2>/dev/null
		stop_spinner
		log "[IMPROVE] 前回の改善完了"
	fi
	_improve_pid=0
}

run_improve_background() {
	local batch_num="$1"
	local batch_start_game="$2"
	local batch_end_game="$3"
	local batch_history_files="$4"
	local batch_scores="$5"
	local batch_soviet="$6"
	local game_num_snapshot="$7"
	local turns_snapshot="$8"

	(
		# --- Phase C: バッチ分析 & 戦略改善 ---

		# バッチサマリー生成
		local batch_summary_file="tmp/batch_summary.txt"
		if [ -n "$batch_history_files" ]; then
			log "[BATCH] サマリー生成中..."
			python3 batch_summary.py $batch_history_files > "$batch_summary_file" 2>/dev/null

			# ベスト/ワーストのJSONLファイル名を抽出
			local best_game_file worst_game_file best_game_path worst_game_path
			best_game_file=$(grep '^===BEST_FILE===' "$batch_summary_file" | sed 's/===BEST_FILE===//')
			worst_game_file=$(grep '^===WORST_FILE===' "$batch_summary_file" | sed 's/===WORST_FILE===//')
			best_game_path="$HISTORY_DIR/$best_game_file"
			worst_game_path="$HISTORY_DIR/$worst_game_file"
		else
			echo "(no batch data)" > "$batch_summary_file"
			local best_game_path="" worst_game_path=""
		fi

		# strategy.py の差分を生成
		local strategy_diff=""
		local prev_version latest_version
		prev_version=$(ls -1t "$STRATEGY_VERSIONS_DIR"/v[0-9]*_strategy.py 2>/dev/null | sed -n '2p')
		latest_version=$(ls -1t "$STRATEGY_VERSIONS_DIR"/v[0-9]*_strategy.py 2>/dev/null | head -1)
		if [ -n "$prev_version" ] && [ -f "$prev_version" ] && [ -n "$latest_version" ] && [ -f "$latest_version" ]; then
			strategy_diff=$(diff -u "$prev_version" "$latest_version" 2>/dev/null || true)
			local real_changes
			real_changes=$(echo "$strategy_diff" | grep '^[+-]' | grep -v '^[+-][+-][+-]' | grep -v '^[+-][[:space:]]*$' | wc -l | tr -d ' ')
			[ "${real_changes:-0}" -lt 2 ] && strategy_diff=""
		fi

		# AI で strategy.py 改善
		log "[IMPROVE] AI改善 (batch #${batch_num})..."
		cp "$STRATEGY_FILE" "${STRATEGY_FILE}.bak"

		local improve_ok=false

		# 直近10バージョン + 殿堂入り戦略
		local past_strategy_files=""
		for vf in $(ls -1t "$STRATEGY_VERSIONS_DIR"/v[0-9]*_strategy.py 2>/dev/null | head -10); do
			past_strategy_files="$past_strategy_files $vf"
		done
		local hall_of_fame_files=""
		for hf in "$STRATEGY_VERSIONS_DIR"/best_score*_strategy.py; do
			[ -f "$hf" ] && hall_of_fame_files="$hall_of_fame_files $hf"
		done

		# 参照データ: バッチサマリー + ベスト/ワーストJSONL + strategy + game_state + 過去戦略
		local improve_ref_files="$STRATEGY_FILE $batch_summary_file"
		[ -n "$best_game_path" ] && [ -f "$best_game_path" ] && improve_ref_files="$improve_ref_files $best_game_path"
		[ -n "$worst_game_path" ] && [ -f "$worst_game_path" ] && improve_ref_files="$improve_ref_files $worst_game_path"
		improve_ref_files="$improve_ref_files $GAME_STATE $past_strategy_files $hall_of_fame_files"

		for retry in $(seq 1 3); do
			if [ "$retry" -eq 1 ]; then
				run_ai IMPROVE "$MODEL_PRIMARY" "$MODEL_FALLBACK" \
					prompts/improve_strategy.md "$STRATEGY_FILE" \
					$improve_ref_files
			else
				log "[IMPROVE] リトライ $retry/3"

				local fix_prompt_file
				fix_prompt_file=$(mktemp /tmp/eloop_fix_prompt.XXXXXX)
				cat > "$fix_prompt_file" <<FIXEOF
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
					"$fix_prompt_file" "$STRATEGY_FILE" \
					"$STRATEGY_FILE"
				rm -f "$fix_prompt_file"
			fi

			if validate_strategy; then
				log "[IMPROVE] バリデーション成功"
				rm -f "${STRATEGY_FILE}.bak"
				python3 trim_changelog.py "$STRATEGY_FILE" 3 2>/dev/null
				improve_ok=true
				break
			fi
		done

		if [ "$improve_ok" = false ]; then
			log "[IMPROVE] バリデーション失敗 → 復元"
			mv "${STRATEGY_FILE}.bak" "$STRATEGY_FILE"
		fi

		# git commit (push省略 — 次ゲームcommitのpushに含まれる)
		git add -A
		git commit -m "eloop Batch #${batch_num}: games #${batch_start_game}-#${batch_end_game}" 2>/dev/null || true

		# --- Phase D: 次回ラジオトーク生成 ---
		local last_score best_score_now
		last_score=$(echo "$batch_scores" | awk '{print $NF}')
		best_score_now=$(cat best_score.txt 2>/dev/null || echo 0)
		start_radio_talk "${last_score:-0}" "$turns_snapshot" "$game_num_snapshot" "$best_score_now" "$strategy_diff" "$batch_soviet"
	) &
	_improve_pid=$!
	log "[IMPROVE] バックグラウンド開始 (PID=$_improve_pid)"
}

#--- コメント返しプロセスのクリーンアップ ---
_kill_comment_gen() {
	local pidfile="tmp/.twitch_chat/comment_gen.pid"
	if [ -f "$pidfile" ]; then
		local old_pid
		old_pid=$(cat "$pidfile")
		if [ -n "$old_pid" ] && kill -0 "$old_pid" 2>/dev/null; then
			# 子プロセス（opencode, script, say等）も含めて停止
			pkill -P "$old_pid" 2>/dev/null
			kill "$old_pid" 2>/dev/null
			log "[COMMENT] 前回のコメント生成プロセス停止 (PID=$old_pid)"
		fi
		rm -f "$pidfile"
	fi
}

#--- コメント返し（毎ゲーム） ---
generate_comment_response() {
	# 前回のコメント生成プロセスが残っていたら停止
	_kill_comment_gen

	# Twitchコメント差分取得
	./twitch_chat.sh fetch

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
人の名前の後ろに「同志」をつけて呼びかけるのが特徴です。
（時刻: ${current_time} / ${time_period}）

【コメント】
${twitch_comments}

【前回のトーク内容（文脈参照用）】
${past_topics}

【ルール】
- 全てのコメントに必ず返事すること。一つも漏らさない
- 一つずつ返事する。「同志○○」と名前を呼んで反応
- 偉そうにしないで、フレンドリーに返事すること
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
			echo "$comments_talk" > tmp/radio_comments.txt
			log "[COMMENT] コメント返し ${#comments_talk}字"
			./say_enqueue.sh --no-preempt tmp/radio_comments.txt "$RADIO_SAY_RATE" 0
			# 読み上げ成功 → pending.logをクリア
			./twitch_chat.sh ack
		else
			log "[COMMENT] コメント返し生成失敗（次回再取得）"
		fi
	) &
	local comment_pid=$!
	echo "$comment_pid" > tmp/.twitch_chat/comment_gen.pid
	disown "$comment_pid"
}

generate_soviet_celebration() {
	local score="$1" turns="$2" game_num="$3"
	local current_time
	current_time=$(date '+%H:%M')

	local celebration_prompt_file
	celebration_prompt_file=$(mktemp /tmp/eloop_celebration_XXXXXXXX)
	cat > "$celebration_prompt_file" <<CELEBPROMPT
あなたはゲーム実況ラジオのパーソナリティ兼AIプレイヤーです。

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
		log "[CELEBRATION] ${#celebration_talk}字"
		./say_enqueue.sh --no-preempt tmp/radio_celebration.txt "$RADIO_SAY_RATE" 0
	else
		log "[CELEBRATION] 祝賀トーク生成失敗"
	fi
}

#=== メインループ ===
log "=== Soren Evolution Loop (eloop) ==="
log "MODEL=$MODEL_PRIMARY  BATCH=$BATCH_SIZE"
log "strategy.py → ${BATCH_SIZE}games → AI改善 → repeat"

# Twitchチャットデーモン起動
./twitch_chat.sh start azumagbanjo
trap '[ "${_improve_pid:-0}" -ne 0 ] && kill "$_improve_pid" 2>/dev/null; _kill_comment_gen; ./twitch_chat.sh stop; exit' EXIT INT TERM

# 前回中断時のリカバリ: .bak が残っていて strategy.py がない場合は復元
if [ ! -f "$STRATEGY_FILE" ] && [ -f "${STRATEGY_FILE}.bak" ]; then
	log "[RECOVER] .bak から復元"
	cp "${STRATEGY_FILE}.bak" "$STRATEGY_FILE"
fi

# 初期バリデーション
if [ ! -f "$STRATEGY_FILE" ]; then
	log "ERROR: $STRATEGY_FILE が見つかりません"
	exit 1
fi

if ! validate_strategy; then
	log "ERROR: 初期バリデーション失敗"
	exit 1
fi

# MOVE状態待ち（初回）
wait_for_move || {
	log "ゲームが起動していません"
	exit 1
}

# バッチ状態復元
BATCH_NUM=0
BATCH_COUNTER=0
BATCH_HISTORY_FILES=""
BATCH_SCORES=""
BATCH_SOVIET=false
BATCH_START_GAME=$((GAME_NUM + 1))
_BATCH_RESUMED=false

if [ -f "$BATCH_STATE_FILE" ]; then
	_bs=$(cat "$BATCH_STATE_FILE")
	BATCH_NUM=$(echo "$_bs" | python3 -c "import json,sys; print(json.load(sys.stdin).get('batch_num',0))" 2>/dev/null || echo 0)
	BATCH_COUNTER=$(echo "$_bs" | python3 -c "import json,sys; print(json.load(sys.stdin).get('batch_counter',0))" 2>/dev/null || echo 0)
	BATCH_START_GAME=$(echo "$_bs" | python3 -c "import json,sys; print(json.load(sys.stdin).get('batch_start_game',0))" 2>/dev/null || echo 0)
	BATCH_HISTORY_FILES=$(echo "$_bs" | python3 -c "import json,sys; print(' '.join(json.load(sys.stdin).get('batch_history_files',[])))" 2>/dev/null || echo "")
	BATCH_SCORES=$(echo "$_bs" | python3 -c "import json,sys; print(json.load(sys.stdin).get('batch_scores',''))" 2>/dev/null || echo "")
	BATCH_SOVIET=$(echo "$_bs" | python3 -c "import json,sys; print(str(json.load(sys.stdin).get('batch_soviet',False)).lower())" 2>/dev/null || echo "false")

	if [ "$BATCH_COUNTER" -gt 0 ] && [ "$BATCH_COUNTER" -lt "$BATCH_SIZE" ]; then
		log "[RECOVER] Batch #${BATCH_NUM} 再開 (${BATCH_COUNTER}/${BATCH_SIZE} 完了済み)"
		_BATCH_RESUMED=true
	else
		# バッチ完了済み or 不正状態 → クリアして新規開始
		clear_batch_state
		BATCH_COUNTER=0
	fi
fi

while true; do
	if [ "$_BATCH_RESUMED" = false ]; then
		# 新規バッチ開始
		BATCH_NUM=$((BATCH_NUM + 1))
		BATCH_COUNTER=0
		BATCH_HISTORY_FILES=""
		BATCH_SCORES=""
		BATCH_SOVIET=false
		BATCH_START_GAME=$((GAME_NUM + 1))

		log ""
		log "╔══════════════════════════════╗"
		log "║  Batch #${BATCH_NUM} (${BATCH_SIZE} games)         ║"
		log "╚══════════════════════════════╝"

		# --- Phase A: 前回バッチで生成済みのラジオトークを再生 ---
		if [ -f "tmp/radio_talk.txt" ] && [ -s "tmp/radio_talk.txt" ]; then
			log "[RADIO] 前回のトーク再生開始"
			./say_enqueue.sh tmp/radio_talk.txt "$RADIO_SAY_RATE" 0 &
		fi
	else
		log ""
		log "╔══════════════════════════════╗"
		log "║  Batch #${BATCH_NUM} 再開 (${BATCH_COUNTER}/${BATCH_SIZE})  ║"
		log "╚══════════════════════════════╝"
	fi
	_BATCH_RESUMED=false  # 次回以降は通常

	# --- Phase B: N ゲーム連続プレイ ---

	while [ "$BATCH_COUNTER" -lt "$BATCH_SIZE" ]; do
		BATCH_COUNTER=$((BATCH_COUNTER + 1))
		GAME_NUM_DISPLAY=$((GAME_NUM + 1))

		log ""
		log "── Game #${GAME_NUM_DISPLAY} (batch ${BATCH_COUNTER}/${BATCH_SIZE}) ──"

		# 1試合プレイ
		RUNNER_TMPFILE=$(mktemp /tmp/eloop_runner.XXXXXX)
		python3 -u strategy_runner.py 2>&1 | tee "$RUNNER_TMPFILE"

		# 結果抽出
		RESULT_JSON=$(sed -n '/^---RESULT---$/,$ p' "$RUNNER_TMPFILE" | tail -n 1)
		rm -f "$RUNNER_TMPFILE"

		if [ -z "$RESULT_JSON" ]; then
			RESULT_JSON='{"score":0,"turns":0,"state":"UNKNOWN"}'
		fi

		SCORE=$(echo "$RESULT_JSON" | python3 -c "import json,sys; print(json.load(sys.stdin).get('score',0))" 2>/dev/null || echo 0)
		TURNS=$(echo "$RESULT_JSON" | python3 -c "import json,sys; print(json.load(sys.stdin).get('turns',0))" 2>/dev/null || echo 0)
		SOVIET_CREATED=$(echo "$RESULT_JSON" | python3 -c "import json,sys; print('true' if json.load(sys.stdin).get('soviet_created',False) else 'false')" 2>/dev/null || echo "false")

		log "[RESULT] score=$SCORE turns=$TURNS"

		# ソ連建国チェック（即座に祝賀）
		if [ "$SOVIET_CREATED" = "true" ]; then
			log "!!! SOVIET CREATED !!!"
			BATCH_SOVIET=true
			sleep 30
			generate_soviet_celebration "$SCORE" "$TURNS" "$GAME_NUM_DISPLAY"
			rm -f tmp/.soviet_created
		fi

		# スコア履歴
		echo "$SCORE" >> score_history.txt
		BATCH_SCORES="${BATCH_SCORES} ${SCORE}"

		# ダッシュボード更新
		./generate_dashboard.sh 2>/dev/null &

		# バージョン保存・ベスト判定・履歴アーカイブ
		save_strategy_version "$SCORE"
		update_best "$SCORE"
		archive_history "$SCORE"

		# アーカイブファイル名を記録
		LATEST_ARCHIVE=$(ls -1t "$HISTORY_DIR"/[0-9]*_score*.jsonl 2>/dev/null | head -1)
		[ -n "$LATEST_ARCHIVE" ] && BATCH_HISTORY_FILES="${BATCH_HISTORY_FILES} ${LATEST_ARCHIVE}"

		# バッチ状態保存（再起動時に途中から再開可能に）
		save_batch_state

		# コメント返し (毎ゲーム)
		generate_comment_response

		# git commit (軽量)
		git add -A
		git commit -m "eloop Game #${GAME_NUM_DISPLAY}: score=${SCORE} (batch ${BATCH_COUNTER}/${BATCH_SIZE})" 2>/dev/null && \
			git push 2>/dev/null || true

		# retry → 次ゲーム
		if [ "$BATCH_COUNTER" -lt "$BATCH_SIZE" ]; then
			if is_game_over; then
				send_retry
			else
				wait_for_move || {
					send_retry
				}
			fi
			./generate_dashboard.sh 2>/dev/null &
		fi

		_maybe_show_joke
		sleep 2
	done

	# バッチ完了 → 状態クリア
	clear_batch_state

	BATCH_END_GAME=$GAME_NUM
	log ""
	log "┌──────────────────────────────┐"
	log "│  Batch #${BATCH_NUM} 完了              │"
	log "│  Games #${BATCH_START_GAME}-#${BATCH_END_GAME}  scores:${BATCH_SCORES}"
	log "└──────────────────────────────┘"

	# 前回のバックグラウンド改善が終わるまで待つ
	wait_improve_done

	# Twitchコメント差分取得 + ニュース取得 (改善に必要)
	log "[TWITCH] コメントfetch..."
	./twitch_chat.sh fetch
	log "[NEWS] ニュース取得..."
	./fetch_news.sh

	# Phase C+D をバックグラウンドで起動
	run_improve_background "$BATCH_NUM" "$BATCH_START_GAME" "$BATCH_END_GAME" \
		"$BATCH_HISTORY_FILES" "$BATCH_SCORES" "$BATCH_SOVIET" "$GAME_NUM" "$TURNS"

	# retry → 次バッチの最初のゲーム
	if is_game_over; then
		send_retry
	else
		wait_for_move || {
			send_retry
		}
	fi

	./generate_dashboard.sh 2>/dev/null &
	_maybe_show_joke
	sleep 2
done
