# broadcast/scheduler.sh - 非同期ジョブスケジュール, Twitchクリップ, audio trigger


#=== ニュース: 毎ゲーム取得 & 再生 ===

_news_fetch_status_snapshot() {
	local status_file="tmp/state/.news_fetch_status.json"
	[ -f "$status_file" ] || return 1
	python3 - "$status_file" <<'PY' 2>/dev/null
import json
import sys

path = sys.argv[1]
data = json.load(open(path))
status = str(data.get("status", "") or "")
source_count = int(data.get("source_count", 0) or 0)
fetched_sources = int(data.get("fetched_source_count", 0) or 0)
fetched_items = int(data.get("fetched_item_count", 0) or 0)
candidate_items = int(data.get("candidate_item_count", 0) or 0)
selected_items = int(data.get("selected_item_count", 0) or 0)
filter_breakdown = data.get("filter_breakdown") or {}

labels = {
    "missing_identity": "無効",
    "past_title": "既読タイトル",
    "past_link": "既読URL",
    "past_link_hash": "既読URLハッシュ",
    "duplicate_title": "今回タイトル重複",
    "duplicate_link": "今回URL重複",
    "duplicate_link_hash": "今回URLハッシュ重複",
}
reason_order = [
    "past_title",
    "past_link",
    "past_link_hash",
    "duplicate_title",
    "duplicate_link",
    "duplicate_link_hash",
    "missing_identity",
]

parts = []
for key in reason_order:
    count = int(filter_breakdown.get(key, 0) or 0)
    if count > 0:
        parts.append(f"{labels.get(key, key)}={count}")
filter_summary = ", ".join(parts)

messages = {
    "ok": f"取得成功: selected={selected_items}件 (sources={fetched_sources}/{source_count}, fetched={fetched_items}, candidates={candidate_items})",
    "stale_cache_restored": f"取得失敗のため前回成功キャッシュを復元 (sources={fetched_sources}/{source_count})",
    "fetch_failed": f"取得失敗: RSS取得成功 source=0/{source_count}",
    "all_seen_or_filtered": f"取得成功だが未読候補なし (sources={fetched_sources}/{source_count}, fetched={fetched_items}, candidates={candidate_items})"
    + (f" | filter={filter_summary}" if filter_summary else ""),
    "render_empty": f"取得成功だが本文生成結果が空 (selected={selected_items})",
    "running": "取得状態不明: fetch_news.py が途中終了した可能性",
}

message = messages.get(status, f"取得状態不明: status={status or 'unknown'}")
print(f"{status}|{message}")
PY
}

fetch_and_play_news() {
	local game_num="$1" score="$2"
	local news_fetch_status="" news_fetch_message="" news_status_line=""
	# 旧呼び出し（引数なし）でも、起動時点の値を固定して後段に渡す
	[ -z "$game_num" ] && game_num=$(cat "$GAME_COUNT_FILE" 2>/dev/null || echo 0)
	[ -z "$score" ] && score=$(_last_score)

	log "[NEWS] ニュース取得..."
	./fetch_news.sh 2>/dev/null
	news_status_line=$(_news_fetch_status_snapshot || true)
	if [ -n "$news_status_line" ]; then
		IFS='|' read -r news_fetch_status news_fetch_message <<<"$news_status_line"
	fi

	if [ -f "tmp/news.txt" ] && [ -s "tmp/news.txt" ]; then
		if [ "$news_fetch_status" = "stale_cache_restored" ] && [ -n "$news_fetch_message" ]; then
			log "[NEWS] ${news_fetch_message}"
		fi
		if ! start_radio_corner_news "$game_num" "$score"; then
			log "[NEWS] 読み上げ対象の未読ニュースなし、スキップ"
		fi
	else
		if [ -n "$news_fetch_message" ]; then
			log "[NEWS] ${news_fetch_message}"
		else
			log "[NEWS] ニュースなし、スキップ"
		fi
	fi
}

_build_manual_strategy_diff() {
	local latest_commit prev_commit diff_text real_changes
	latest_commit=$(git log --format=%H -n 1 -- "$STRATEGY_FILE" 2>/dev/null | head -n 1)
	prev_commit=$(git log --format=%H -n 2 -- "$STRATEGY_FILE" 2>/dev/null | tail -n 1)

	if [ -n "$latest_commit" ] && [ -n "$prev_commit" ]; then
		diff_text=$(git diff --unified=1 "$prev_commit" "$latest_commit" -- "$STRATEGY_FILE" 2>/dev/null || true)
		real_changes=$(printf '%s\n' "$diff_text" | grep '^[+-]' | grep -v '^[+-][+-][+-]' | grep -v '^[+-][[:space:]]*$' | head -n 60 || true)
		if [ -n "$real_changes" ]; then
			printf '%s\n' "$diff_text" | sed -n '1,220p'
			return 0
		fi
	fi

	if [ -n "$latest_commit" ]; then
		git show --stat --oneline "$latest_commit" -- "$STRATEGY_FILE" 2>/dev/null || true
	fi
}

_dispatch_manual_audio_trigger() {
	local cmd_file="$1" game_num="$2" score="$3"
	[ -f "$cmd_file" ] || return 1

	local cmd_line cmd_name recent_scores best_score strategy_diff
	cmd_line=$(sed 's/#.*$//' "$cmd_file" 2>/dev/null | sed '/^[[:space:]]*$/d' | head -n 1 | tr '[:upper:]' '[:lower:]')
	cmd_name=$(printf '%s' "$cmd_line" | awk '{print $1}')

	[ -n "$cmd_name" ] || {
		log "[MANUAL] 空の音声トリガーを破棄: $(basename "$cmd_file")"
		return 1
	}

	case "$cmd_name" in
	news)
		log "[MANUAL] news トリガー受付: $(basename "$cmd_file")"
		fetch_and_play_news "$game_num" "$score" &
		;;
	soviet)
		log "[MANUAL] soviet トリガー受付 (sovietカテゴリtheme): $(basename "$cmd_file")"
		start_radio_corner_theme "$game_num" "$score" "soviet" &
		;;
	strategy)
		log "[MANUAL] strategy トリガー受付: $(basename "$cmd_file")"
		recent_scores=$(_recent_scores 12 | tr '\n' ' ' | sed 's/ $//')
		[ -z "$recent_scores" ] && recent_scores="${score:-0}"
		best_score=$(cat best_score.txt 2>/dev/null || echo 0)
		strategy_diff=$(_build_manual_strategy_diff)
		if [ -z "$strategy_diff" ]; then
			strategy_diff="直近の strategy.py 差分は取得できなかった。直近スコア推移と最新改善の狙いを中心に解説すること。"
		fi
		start_radio_corner_strategy "$strategy_diff" "$recent_scores" "$game_num" "$best_score" &
		;;
	theme)
		log "[MANUAL] theme トリガー受付: $(basename "$cmd_file")"
		start_radio_corner_theme "$game_num" "$score" &
		;;
	weather)
		log "[MANUAL] weather トリガー受付: $(basename "$cmd_file")"
		start_radio_corner_weather "$game_num" "$score" &
		;;
	fortune)
		log "[MANUAL] fortune トリガー受付: $(basename "$cmd_file")"
		start_radio_corner_fortune "$game_num" "$score" &
		;;
	market)
		log "[MANUAL] market トリガー受付: $(basename "$cmd_file")"
		start_radio_corner_market "$game_num" "$score" &
		;;
	dinner)
		log "[MANUAL] dinner トリガー受付: $(basename "$cmd_file")"
		start_radio_corner_dinner "$game_num" "$score" &
		;;
	deals)
		log "[MANUAL] deals トリガー受付: $(basename "$cmd_file")"
		start_radio_corner_deals "$game_num" "$score" &
		;;
	survival)
		log "[MANUAL] survival トリガー受付: $(basename "$cmd_file")"
		start_radio_corner_survival "$game_num" "$score" &
		;;
	rakugo)
		log "[MANUAL] rakugo トリガー受付: $(basename "$cmd_file")"
		start_radio_corner_rakugo "$game_num" "$score" &
		;;
	jiji)
		log "[MANUAL] jiji トリガー受付: $(basename "$cmd_file")"
		_run_jiji_corner_guarded "$game_num" "$score" &
		;;
	*)
		log "[MANUAL] 未知の音声トリガーを破棄: $(basename "$cmd_file") cmd=${cmd_name}"
		return 1
		;;
	esac

	return 0
}

process_external_audio_triggers() {
	local game_num="$1" score="$2"
	[ -z "$game_num" ] && game_num=$(cat "$GAME_COUNT_FILE" 2>/dev/null || echo 0)
	[ -z "$score" ] && score=$(_last_score)
	mkdir -p "$MANUAL_AUDIO_TRIGGER_DIR" 2>/dev/null || true

	local max_per_tick="${MANUAL_AUDIO_TRIGGER_MAX_PER_TICK:-3}"
	case "$max_per_tick" in
	''|*[!0-9]*) max_per_tick=3 ;;
	esac
	[ "$max_per_tick" -lt 1 ] && max_per_tick=1

	local qf processing count=0
	for qf in $(ls -1 "$MANUAL_AUDIO_TRIGGER_DIR"/*.cmd 2>/dev/null | sort | head -n "$max_per_tick"); do
		[ -f "$qf" ] || continue
		processing="${qf%.cmd}.processing"
		if ! mv "$qf" "$processing" 2>/dev/null; then
			continue
		fi
		_dispatch_manual_audio_trigger "$processing" "$game_num" "$score" || true
		rm -f "$processing" 2>/dev/null || true
		count=$((count + 1))
	done

	[ "$count" -gt 0 ] && log "[MANUAL] 音声トリガー処理数: ${count}"
}

#=== ラジオトーク: ディスパッチャー ===

start_random_radio_corner() {
	local game_num="$1" score="$2"
	[ -z "$game_num" ] && game_num=$(cat "$GAME_COUNT_FILE" 2>/dev/null || echo 0)
	[ -z "$score" ] && score=$(_last_score)

	# 1/3 の確率でソ連テーマ限定、それ以外は全テーマから選択
	# (全テーマからでも [soviet] タグ付きが選ばれればソ連モードになる)
	if [ $((RANDOM % 3)) -eq 0 ]; then
		log "[RADIO] コーナー選択: theme (soviet filter)"
		start_radio_corner_theme "$game_num" "$score" "soviet"
	else
		log "[RADIO] コーナー選択: theme"
		start_radio_corner_theme "$game_num" "$score"
	fi
}

schedule_nonessential_audio_jobs() {
	local game_num="$1" score="$2"
	[ -z "$game_num" ] && game_num=$(cat "$GAME_COUNT_FILE" 2>/dev/null || echo 0)
	[ -z "$score" ] && score=$(_last_score)

	# 配信演出の頻度: 改善サイクル (accumulated_games) に合わせる
	# cycle_pos=2 で雑談ラジオ、cycle_pos=5 でニュース、cycle_pos=8 で時事
	# コメント優先の判定は維持しつつ、生成は止めない。
	# 再生段で deferred キューへ回して、コメント消化後に再生する。
	local comment_backlog_skip_threshold=1
	local improve_cycle=${MIN_GAMES_BEFORE_IMPROVE:-12}
	# 蓄積数ベースでサイクル位置を決定 (粛清/リセットでもズレない)
	local acc_count=0
	if [ -f "$ACCUMULATED_GAMES_FILE" ]; then
		acc_count=$(python3 -c "import json; print(json.load(open('$ACCUMULATED_GAMES_FILE')).get('count',0))" 2>/dev/null || echo 0)
	fi
	local cycle_pos=$(( acc_count % improve_cycle ))

	local comment_queued=0 comment_playing=0 comment_total=0
	local comment_backlog_high=false
	read -r comment_queued comment_playing <<<"$(get_comment_backlog_counts)"
	comment_queued=${comment_queued:-0}
	comment_playing=${comment_playing:-0}
	comment_total=$((comment_queued + comment_playing))
	if is_comment_backlog_high "$comment_backlog_skip_threshold" "queued"; then
		comment_backlog_high=true
	fi

	# ニュース: 12ゲームサイクルの5ゲーム目
	if [ "$cycle_pos" -eq 5 ]; then
		if [ "$comment_backlog_high" = true ]; then
			log "[NEWS] comment backlog=${comment_total} (queued=${comment_queued}, playing=${comment_playing}, threshold=${comment_backlog_skip_threshold}) -> generate + deferred再生"
		fi
		fetch_and_play_news "$game_num" "$score" &
	fi

	# --- 時間帯コーナー (1日1回、±15分ウィンドウ) ---
	local current_hour current_min today timed_corner_fired=false
	current_hour=$(date +%H)
	current_min=$(date +%M)
	today=$(date +%Y%m%d)

	_try_timed_corner() {
		local name="$1" target_hh="$2" target_mm="$3"
		local marker="$TMP_MARKERS_DIR/.timed_corner_done_${today}_${name}"
		local inflight="$TMP_MARKERS_DIR/.timed_corner_inflight_${name}"
		[ -f "$marker" ] && return 1
		[ -f "$inflight" ] && return 1
		local target=$((target_hh * 60 + target_mm))
		local now=$((10#$current_hour * 60 + 10#$current_min))
		local diff=$((now - target))
		[ "$diff" -lt 0 ] && diff=$((-diff))
		[ "$diff" -le 15 ] || return 1
		touch "$inflight"
		return 0
	}

	# 成功マーカーを作成するラッパー (バックグラウンドジョブ内で使用)
	_run_timed_corner() {
		local name="$1" func="$2"
		shift 2
		if "$func" "$@"; then
			touch "$TMP_MARKERS_DIR/.timed_corner_done_${today}_${name}"
		fi
		rm -f "$TMP_MARKERS_DIR/.timed_corner_inflight_${name}"
	}

	if _try_timed_corner "rakugo" 1 0; then
		timed_corner_fired=true
		_run_timed_corner "rakugo" start_radio_corner_rakugo "$game_num" "$score" &
	fi
	if _try_timed_corner "breakfast" 7 0; then
		timed_corner_fired=true
		_run_timed_corner "breakfast" start_radio_corner_breakfast "$game_num" "$score" &
	fi
	if _try_timed_corner "weather" 8 0; then
		timed_corner_fired=true
		_run_timed_corner "weather" start_radio_corner_weather "$game_num" "$score" &
	fi
	if _try_timed_corner "lunch" 11 30; then
		timed_corner_fired=true
		_run_timed_corner "lunch" start_radio_corner_lunch "$game_num" "$score" &
	fi
	if _try_timed_corner "fortune" 12 0; then
		timed_corner_fired=true
		_run_timed_corner "fortune" start_radio_corner_fortune "$game_num" "$score" &
	fi
	if _try_timed_corner "devil_dict" 13 0; then
		timed_corner_fired=true
		_run_timed_corner "devil_dict" start_radio_corner_devil_dict "$game_num" "$score" &
	fi
	if _try_timed_corner "soviet_quiz" 14 0; then
		timed_corner_fired=true
		_run_timed_corner "soviet_quiz" start_radio_corner_soviet_quiz "$game_num" "$score" &
	fi
	if _try_timed_corner "market" 15 30; then
		timed_corner_fired=true
		_run_timed_corner "market" start_radio_corner_market "$game_num" "$score" &
	fi
	if _try_timed_corner "bluegrass" 16 0; then
		timed_corner_fired=true
		_run_timed_corner "bluegrass" start_radio_corner_bluegrass "$game_num" "$score" &
	fi
	if _try_timed_corner "dinner" 17 0; then
		timed_corner_fired=true
		_run_timed_corner "dinner" start_radio_corner_dinner "$game_num" "$score" &
	fi
	if _try_timed_corner "redefine" 17 30; then
		timed_corner_fired=true
		_run_timed_corner "redefine" start_radio_corner_redefine "$game_num" "$score" &
	fi
	if _try_timed_corner "soviet_lifehack" 18 0; then
		timed_corner_fired=true
		_run_timed_corner "soviet_lifehack" start_radio_corner_soviet_lifehack "$game_num" "$score" &
	fi
	if _try_timed_corner "world_dinner" 19 0; then
		timed_corner_fired=true
		_run_timed_corner "world_dinner" start_radio_corner_world_dinner "$game_num" "$score" &
	fi
	if _try_timed_corner "deals" 21 0; then
		timed_corner_fired=true
		_run_timed_corner "deals" start_radio_corner_deals "$game_num" "$score" &
	fi
	if _try_timed_corner "night_snack" 21 30; then
		timed_corner_fired=true
		_run_timed_corner "night_snack" start_radio_corner_night_snack "$game_num" "$score" &
	fi
	if _try_timed_corner "survival" 22 0; then
		timed_corner_fired=true
		_run_timed_corner "survival" start_radio_corner_survival "$game_num" "$score" &
	fi

	# 雑談ラジオ: 12ゲームサイクルの2ゲーム目（時間帯コーナー発火時はスキップ）
	if [ "$timed_corner_fired" = false ] && [ "$cycle_pos" -eq 2 ]; then
		if [ "$comment_backlog_high" = true ]; then
			log "[RADIO] comment backlog=${comment_total} (queued=${comment_queued}, playing=${comment_playing}, threshold=${comment_backlog_skip_threshold}) -> generate + deferred再生"
		fi
		start_random_radio_corner "$game_num" "$score" &
	fi

	# 時事ニュースコーナー: サイクル8ゲーム目
	# 改善タイミング付近はスキップ（メリケンAI起動との競合回避）
	if [ "$near_improve" != true ] && [ "$cycle_pos" -eq 8 ]; then
		if [ "$comment_backlog_high" = true ]; then
			log "[JIJI] comment backlog=${comment_total} (queued=${comment_queued}, playing=${comment_playing}, threshold=${comment_backlog_skip_threshold}) -> generate + deferred再生"
		fi
		_run_jiji_corner_guarded "$game_num" "$score" &
	fi
}

#=== lib/eloop_radio.sh から移行した関数 ===


#=== ニュース: 毎ゲーム取得 & 再生 ===

_legacy_fetch_and_play_news() {
	local game_num="$1" score="$2"
	local news_fetch_status="" news_fetch_message="" news_status_line=""
	# 旧呼び出し（引数なし）でも、起動時点の値を固定して後段に渡す
	[ -z "$game_num" ] && game_num=$(cat "$GAME_COUNT_FILE" 2>/dev/null || echo 0)
	[ -z "$score" ] && score=$(tail -1 score_history.txt 2>/dev/null | awk -F'\t' '{print $NF}' || echo 0)

	log "[NEWS] ニュース取得..."
	./fetch_news.sh 2>/dev/null
	news_status_line=$(_news_fetch_status_snapshot || true)
	if [ -n "$news_status_line" ]; then
		IFS='|' read -r news_fetch_status news_fetch_message <<<"$news_status_line"
	fi

	if [ -f "tmp/news.txt" ] && [ -s "tmp/news.txt" ]; then
		if [ "$news_fetch_status" = "stale_cache_restored" ] && [ -n "$news_fetch_message" ]; then
			log "[NEWS] ${news_fetch_message}"
		fi
		start_radio_corner_news "$game_num" "$score"
	else
		if [ -n "$news_fetch_message" ]; then
			log "[NEWS] ${news_fetch_message}"
		else
			log "[NEWS] ニュースなし、スキップ"
		fi
	fi
}

#=== ラジオトーク: ディスパッチャー ===

_legacy_start_random_radio_corner() {
	local game_num="$1" score="$2"
	[ -z "$game_num" ] && game_num=$(cat "$GAME_COUNT_FILE" 2>/dev/null || echo 0)
	[ -z "$score" ] && score=$(tail -1 score_history.txt 2>/dev/null | awk -F'\t' '{print $NF}' || echo 0)

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

_legacy_schedule_nonessential_audio_jobs() {
	local game_num="$1" score="$2"
	[ -z "$game_num" ] && game_num=$(cat "$GAME_COUNT_FILE" 2>/dev/null || echo 0)
	[ -z "$score" ] && score=$(tail -1 score_history.txt 2>/dev/null | awk -F'\t' '{print $NF}' || echo 0)

	# 配信演出の頻度 (変更しても毎ループ source で即反映)
	local news_interval_day=4
	local news_interval_night=8
	local news_night_start_hour=2
	local news_night_end_hour=5
	local news_phase=1
	local radio_interval=5
	local radio_phase=0
	local comment_backlog_skip_threshold=4

	local comment_queued=0 comment_playing=0 comment_total=0
	local skip_nonessential_radio=false
	read -r comment_queued comment_playing <<<"$(get_comment_backlog_counts)"
	comment_queued=${comment_queued:-0}
	comment_playing=${comment_playing:-0}
	comment_total=$((comment_queued + comment_playing))
	if is_comment_backlog_high "$comment_backlog_skip_threshold"; then
		skip_nonessential_radio=true
	fi

	local current_hour current_news_interval current_news_mode
	current_hour=$(date +%H)
	if (( 10#$current_hour >= news_night_start_hour && 10#$current_hour < news_night_end_hour )); then
		current_news_interval="$news_interval_night"
		current_news_mode="night"
	else
		current_news_interval="$news_interval_day"
		current_news_mode="day"
	fi

	if [ "$current_news_mode" != "${LAST_NEWS_MODE:-}" ]; then
		log "[NEWS] schedule mode=${current_news_mode} interval=${current_news_interval} (night: ${news_night_start_hour}:00-${news_night_end_hour}:00)"
		LAST_NEWS_MODE="$current_news_mode"
	fi

	if (( game_num % current_news_interval == news_phase )); then
		if [ "$skip_nonessential_radio" = true ]; then
			log "[NEWS] skip: comment backlog=${comment_total} (queued=${comment_queued}, playing=${comment_playing}, threshold=${comment_backlog_skip_threshold})"
		else
			fetch_and_play_news "$game_num" "$score" &
		fi
	fi

	if (( game_num % radio_interval == radio_phase )); then
		if [ "$skip_nonessential_radio" = true ]; then
			log "[RADIO] skip random corner: comment backlog=${comment_total} (queued=${comment_queued}, playing=${comment_playing}, threshold=${comment_backlog_skip_threshold})"
		else
			start_random_radio_corner "$game_num" "$score" &
		fi
	fi

	# 時事ニュースコーナー: サイクル8ゲーム目
	if (( game_num % ${MIN_GAMES_BEFORE_IMPROVE:-12} == 8 )); then
		if [ "$skip_nonessential_radio" = true ]; then
			log "[JIJI] skip: comment backlog=${comment_total} (queued=${comment_queued}, playing=${comment_playing}, threshold=${comment_backlog_skip_threshold})"
		else
			_run_jiji_corner_guarded "$game_num" "$score" &
		fi
	fi
}
