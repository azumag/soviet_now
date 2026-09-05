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
    "missing_published_at": "公開日時なし",
    "stale_published_at": "期限切れ",
    "past_title": "既読タイトル",
    "past_link": "既読URL",
    "past_link_hash": "既読URLハッシュ",
    "duplicate_title": "今回タイトル重複",
    "duplicate_link": "今回URL重複",
    "duplicate_link_hash": "今回URLハッシュ重複",
}
reason_order = [
    "stale_published_at",
    "missing_published_at",
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
	else
		if [ -n "$news_fetch_message" ]; then
			log "[NEWS] ${news_fetch_message}"
		else
			log "[NEWS] ニュース取得失敗"
		fi
	fi
	# 取得成功/失敗に関わらずコーナーを起動（失敗時はAI自主探索モード）
	# 重複起動防止: 事前チェックして生成中の場合はスキップ
	if _try_game_corner "$game_num" "news"; then
		start_radio_corner_news "$game_num" "$score"
	else
		log "[RADIO:news] duplicate skip: scheduler pre-check for game=${game_num}"
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
		if _try_game_corner "$game_num" "news"; then
			fetch_and_play_news "$game_num" "$score" &
		else
			log "[MANUAL] news トリガー重複スキップ: game=${game_num}"
		fi
		;;
	soviet)
		log "[MANUAL] soviet トリガー受付 (sovietカテゴリtheme): $(basename "$cmd_file")"
		if _try_game_corner "$game_num" "theme"; then
			start_radio_corner_theme "$game_num" "$score" "soviet" &
		else
			log "[MANUAL] soviet トリガー重複スキップ: game=${game_num}"
		fi
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
		if _try_game_corner "$game_num" "strategy"; then
			start_radio_corner_strategy "$strategy_diff" "$recent_scores" "$game_num" "$best_score" &
		else
			log "[MANUAL] strategy トリガー重複スキップ: game=${game_num}"
		fi
		;;
	theme)
		log "[MANUAL] theme トリガー受付: $(basename "$cmd_file")"
		if _try_game_corner "$game_num" "theme"; then
			start_radio_corner_theme "$game_num" "$score" &
		else
			log "[MANUAL] theme トリガー重複スキップ: game=${game_num}"
		fi
		;;
	weather)
		log "[MANUAL] weather トリガー受付: $(basename "$cmd_file")"
		if _try_game_corner "$game_num" "weather"; then
			start_radio_corner_weather "$game_num" "$score" &
		else
			log "[MANUAL] weather トリガー重複スキップ: game=${game_num}"
		fi
		;;
	fortune)
		log "[MANUAL] fortune トリガー受付: $(basename "$cmd_file")"
		if _try_game_corner "$game_num" "fortune"; then
			start_radio_corner_fortune "$game_num" "$score" &
		else
			log "[MANUAL] fortune トリガー重複スキップ: game=${game_num}"
		fi
		;;
	market)
		log "[MANUAL] market トリガー受付: $(basename "$cmd_file")"
		if _try_game_corner "$game_num" "market"; then
			start_radio_corner_market "$game_num" "$score" &
		else
			log "[MANUAL] market トリガー重複スキップ: game=${game_num}"
		fi
		;;
	dinner)
		log "[MANUAL] dinner トリガー受付: $(basename "$cmd_file")"
		if _try_game_corner "$game_num" "dinner"; then
			start_radio_corner_dinner "$game_num" "$score" &
		else
			log "[MANUAL] dinner トリガー重複スキップ: game=${game_num}"
		fi
		;;
	deals)
		log "[MANUAL] deals トリガー受付: $(basename "$cmd_file")"
		if _try_game_corner "$game_num" "deals"; then
			start_radio_corner_deals "$game_num" "$score" &
		else
			log "[MANUAL] deals トリガー重複スキップ: game=${game_num}"
		fi
		;;
	survival)
		log "[MANUAL] survival トリガー受付: $(basename "$cmd_file")"
		if _try_game_corner "$game_num" "survival"; then
			start_radio_corner_survival "$game_num" "$score" &
		else
			log "[MANUAL] survival トリガー重複スキップ: game=${game_num}"
		fi
		;;
	rakugo)
		log "[MANUAL] rakugo トリガー受付: $(basename "$cmd_file")"
		if _try_game_corner "$game_num" "rakugo"; then
			start_radio_corner_rakugo "$game_num" "$score" &
		else
			log "[MANUAL] rakugo トリガー重複スキップ: game=${game_num}"
		fi
		;;
	jiji)
		log "[MANUAL] jiji トリガー受付: $(basename "$cmd_file")"
		if _try_game_corner "$game_num" "jiji"; then
			_run_jiji_corner_guarded "$game_num" "$score" &
		else
			log "[MANUAL] jiji トリガー重複スキップ: game=${game_num}"
		fi
		;;
	health)
		log "[MANUAL] health トリガー受付: $(basename "$cmd_file")"
		if _try_game_corner "$game_num" "health"; then
			start_radio_corner_health "$game_num" "$score" &
		else
			log "[MANUAL] health トリガー重複スキップ: game=${game_num}"
		fi
		;;
	wiki)
		log "[MANUAL] wiki トリガー受付: $(basename "$cmd_file")"
		if _try_game_corner "$game_num" "wiki"; then
			start_radio_corner_wiki "$game_num" "$score" &
		else
			log "[MANUAL] wiki トリガー重複スキップ: game=${game_num}"
		fi
		;;
	sightseeing)
		log "[MANUAL] sightseeing トリガー受付: $(basename "$cmd_file")"
		if _try_game_corner "$game_num" "sightseeing"; then
			start_radio_corner_sightseeing "$game_num" "$score" &
		else
			log "[MANUAL] sightseeing トリガー重複スキップ: game=${game_num}"
		fi
		;;
	whatday)
		log "[MANUAL] whatday トリガー受付: $(basename "$cmd_file")"
		if _try_game_corner "$game_num" "whatday"; then
			start_radio_corner_whatday "$game_num" "$score" &
		else
			log "[MANUAL] whatday トリガー重複スキップ: game=${game_num}"
		fi
		;;
	zaitech)
		log "[MANUAL] zaitech トリガー受付: $(basename "$cmd_file")"
		if _try_game_corner "$game_num" "zaitech"; then
			start_radio_corner_zaitech "$game_num" "$score" &
		else
			log "[MANUAL] zaitech トリガー重複スキップ: game=${game_num}"
		fi
		;;
	fudosan)
		log "[MANUAL] fudosan トリガー受付: $(basename "$cmd_file")"
		if _try_game_corner "$game_num" "fudosan"; then
			start_radio_corner_fudosan "$game_num" "$score" &
		else
			log "[MANUAL] fudosan トリガー重複スキップ: game=${game_num}"
		fi
		;;
	local_japan)
		log "[MANUAL] local_japan トリガー受付: $(basename "$cmd_file")"
		if _try_game_corner "$game_num" "local_japan"; then
			start_radio_corner_local_japan "$game_num" "$score" &
		else
			log "[MANUAL] local_japan トリガー重複スキップ: game=${game_num}"
		fi
		;;
	finance)
		log "[MANUAL] finance トリガー受付: $(basename "$cmd_file")"
		if _try_game_corner "$game_num" "finance"; then
			start_radio_corner_finance "$game_num" "$score" &
		else
			log "[MANUAL] finance トリガー重複スキップ: game=${game_num}"
		fi
		;;
	danger_zone)
		log "[MANUAL] danger_zone トリガー受付: $(basename "$cmd_file")"
		if _try_game_corner "$game_num" "danger_zone"; then
			start_radio_corner_danger_zone "$game_num" "$score" &
		else
			log "[MANUAL] danger_zone トリガー重複スキップ: game=${game_num}"
		fi
		;;
	ai_knowledge)
		log "[MANUAL] ai_knowledge トリガー受付: $(basename "$cmd_file")"
		if _try_game_corner "$game_num" "ai_knowledge"; then
			start_radio_corner_ai_knowledge "$game_num" "$score" &
		else
			log "[MANUAL] ai_knowledge トリガー重複スキップ: game=${game_num}"
		fi
		;;
	music_knowledge)
		log "[MANUAL] music_knowledge トリガー受付: $(basename "$cmd_file")"
		if _try_game_corner "$game_num" "music_knowledge"; then
			start_radio_corner_music_knowledge "$game_num" "$score" &
		else
			log "[MANUAL] music_knowledge トリガー重複スキップ: game=${game_num}"
		fi
		;;
	breakfast)
		log "[MANUAL] breakfast トリガー受付: $(basename "$cmd_file")"
		if _try_game_corner "$game_num" "breakfast"; then
			start_radio_corner_breakfast "$game_num" "$score" &
		else
			log "[MANUAL] breakfast トリガー重複スキップ: game=${game_num}"
		fi
		;;
	lunch)
		log "[MANUAL] lunch トリガー受付: $(basename "$cmd_file")"
		if _try_game_corner "$game_num" "lunch"; then
			start_radio_corner_lunch "$game_num" "$score" &
		else
			log "[MANUAL] lunch トリガー重複スキップ: game=${game_num}"
		fi
		;;
	devil_dict)
		log "[MANUAL] devil_dict トリガー受付: $(basename "$cmd_file")"
		if _try_game_corner "$game_num" "devil_dict"; then
			start_radio_corner_devil_dict "$game_num" "$score" &
		else
			log "[MANUAL] devil_dict トリガー重複スキップ: game=${game_num}"
		fi
		;;
	soviet_quiz)
		log "[MANUAL] soviet_quiz トリガー受付: $(basename "$cmd_file")"
		if _try_game_corner "$game_num" "soviet_quiz"; then
			start_radio_corner_soviet_quiz "$game_num" "$score" &
		else
			log "[MANUAL] soviet_quiz トリガー重複スキップ: game=${game_num}"
		fi
		;;
	bluegrass)
		log "[MANUAL] bluegrass トリガー受付: $(basename "$cmd_file")"
		if _try_game_corner "$game_num" "bluegrass"; then
			start_radio_corner_bluegrass "$game_num" "$score" &
		else
			log "[MANUAL] bluegrass トリガー重複スキップ: game=${game_num}"
		fi
		;;
	redefine)
		log "[MANUAL] redefine トリガー受付: $(basename "$cmd_file")"
		if _try_game_corner "$game_num" "redefine"; then
			start_radio_corner_redefine "$game_num" "$score" &
		else
			log "[MANUAL] redefine トリガー重複スキップ: game=${game_num}"
		fi
		;;
	soviet_lifehack)
		log "[MANUAL] soviet_lifehack トリガー受付: $(basename "$cmd_file")"
		if _try_game_corner "$game_num" "soviet_lifehack"; then
			start_radio_corner_soviet_lifehack "$game_num" "$score" &
		else
			log "[MANUAL] soviet_lifehack トリガー重複スキップ: game=${game_num}"
		fi
		;;
	world_dinner)
		log "[MANUAL] world_dinner トリガー受付: $(basename "$cmd_file")"
		if _try_game_corner "$game_num" "world_dinner"; then
			start_radio_corner_world_dinner "$game_num" "$score" &
		else
			log "[MANUAL] world_dinner トリガー重複スキップ: game=${game_num}"
		fi
		;;
	night_snack)
		log "[MANUAL] night_snack トリガー受付: $(basename "$cmd_file")"
		if _try_game_corner "$game_num" "night_snack"; then
			start_radio_corner_night_snack "$game_num" "$score" &
		else
			log "[MANUAL] night_snack トリガー重複スキップ: game=${game_num}"
		fi
		;;
	*)
		log "[MANUAL] 未知の音声トリガーを破棄: $(basename "$cmd_file") cmd=${cmd_name}"
		return 1
		;;
	esac

	return 0
}

# ゲームベースのコーナーの重複起動防止ヘルパー (scheduler-level pre-check)
# done_marker と inflight_marker の存在チェックのみ (corner関数のmkdirに委譲)
# 戻り値0=起動可、1=スキップ (doneまたはin-flight)
_try_game_corner() {
	local game_num="$1" corner_name="$2"
	[ -z "$game_num" ] && return 1
	[ -z "$corner_name" ] && return 1

	local done_marker="$TMP_MARKERS_DIR/.radio_done_${game_num}_${corner_name}"
	local inflight_dir="$TMP_MARKERS_DIR/.radio_inflight_${game_num}_${corner_name}"

	# news/jiji は時事性が命: soren試合が進まない間 (CLIゲーム表示中等) も止めない。
	# 同一game_numでも前回から一定時間を超えたら再発行を許可する (既定12分。
	# timed枠自体が15分間隔のため、凍結中も設計どおりの刻みで流れる)。
	case "$corner_name" in
	news | jiji)
		if [ -f "$done_marker" ]; then
			local _done_age=0
			_done_age=$(($(date +%s) - $(stat -c %Y "$done_marker" 2>/dev/null || stat -f %m "$done_marker" 2>/dev/null || echo 0)))
			if [ "$_done_age" -ge "${NEWS_REPEAT_SEC:-720}" ]; then
				rm -f "$done_marker"
			fi
		fi
		;;
	esac

	# 既に生成済み (done marker は corner 関数が生成成功后作成する)
	[ -f "$done_marker" ] && return 1

	# 既に生成中 (inflight は corner 関数の mkdir が作成する)
	# 'existence チェックのみ' — 実際のロックは corner 関数の mkdir に委譲
	[ -d "$inflight_dir" ] && return 1

	# 起動可能
	return 0
}

# _try_game_corner の後処理用: inflight markerを削除 (生成失敗時)
_cancel_game_corner_inflight() {
	local game_num="$1" corner_name="$2"
	[ -z "$game_num" ] || [ -z "$corner_name" ] && return 0
	rmdir "$TMP_MARKERS_DIR/.radio_inflight_${game_num}_${corner_name}" 2>/dev/null || true
}

process_external_audio_triggers() {
	local game_num="$1" score="$2"
	[ -z "$game_num" ] && game_num=$(cat "$GAME_COUNT_FILE" 2>/dev/null || echo 0)
	[ -z "$score" ] && score=$(_last_score)
	mkdir -p "$MANUAL_AUDIO_TRIGGER_DIR" 2>/dev/null || true

	local max_per_tick="${MANUAL_AUDIO_TRIGGER_MAX_PER_TICK:-3}"
	case "$max_per_tick" in
	'' | *[!0-9]*) max_per_tick=3 ;;
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

	# 通常テーマ一覧から選ぶ。ソ連タグ付きテーマは一覧内に残すが、強制的には寄せない。
	log "[RADIO] コーナー選択: theme"
	if _try_game_corner "$game_num" "theme"; then
		start_radio_corner_theme "$game_num" "$score"
	else
		log "[RADIO:theme] duplicate skip: scheduler pre-check for game=${game_num}"
	fi
}

schedule_nonessential_audio_jobs() {
	# 2026-06-03: ラジオ自動生成 一時disable (ユーザー指示)。RADIO_GENERATION_ENABLED=0 で
	# 全自動ラジオ生成(時刻ベース/新試合コーナー)を停止。radio_worker が毎tick この関数を
	# re-source するため .env 変更が即反映(worker再起動不要)。手動トリガーは別経路で生存。
	[ "${RADIO_GENERATION_ENABLED:-1}" = "0" ] && return 0
	local game_num="$1" score="$2"
	[ -z "$game_num" ] && game_num=$(cat "$GAME_COUNT_FILE" 2>/dev/null || echo 0)
	[ -z "$score" ] && score=$(_last_score)

	# issue #5: deferred radio queue が MAX 件超で滞留中は新規ラジオ生成を開始しない。
	# 5 件以下に戻るまで自動生成を抑制し、再開時にマーカーを解除して 1 回だけログする。
	if _radio_generation_blocked_by_backpressure; then
		return 0
	fi

	# ピーク時間帯限定: キューに 1 件でも残っている間は新規ラジオ生成を開始しない
	# （issue #5 の MAX=5 より厳しい「空になるまで待つ」方式。同一tick内で複数コーナーの
	# 時刻窓が重なった場合はまとめて生成されうる）。ピーク外は無効。
	if _radio_generation_blocked_by_peak_hour_queue; then
		return 0
	fi

	# ラジオスケジュール: 時刻ベースのみ
	# hh:00,30 = news / hh:05 = theme / hh:15,45 = jiji
	# コメント優先の判定は維持しつつ、生成は止めない。
	# 再生段で deferred キューへ回して、コメント消化後に再生する。
	local comment_backlog_skip_threshold=1

	local comment_queued=0 comment_playing=0 comment_total=0
	local comment_backlog_high=false
	read -r comment_queued comment_playing <<<"$(get_comment_backlog_counts)"
	comment_queued=${comment_queued:-0}
	comment_playing=${comment_playing:-0}
	comment_total=$((comment_queued + comment_playing))
	if is_comment_backlog_high "$comment_backlog_skip_threshold" "queued"; then
		comment_backlog_high=true
	fi

	# ニュース: 時刻ベースで毎時00,30に取得（15分間隔）
	# --- 戦略改善ラジオ: pending ファイルがある場合に生成 ---
	_handle_pending_strategy_radio() {
		local pending_file="tmp/state/pending_strategy_radio.json"
		[ -f "$pending_file" ] || return 0

		# 既に生成済みかチェック（game_num で重複回避）
		local pending_game_num
		pending_game_num=$(python3 -c "import json; print(json.load(open('$pending_file')).get('game_num',0))" 2>/dev/null || echo 0)
		local already_played=false
		if [ -f "tmp/state/radio_talk_played" ]; then
			if grep -q "|strategy|.*game_num.*${pending_game_num}" tmp/state/radio_talk_played 2>/dev/null; then
				already_played=true
			fi
		fi
		if [ "$already_played" = true ]; then
			rm -f "$pending_file"
			return 0
		fi

		log "[STRATEGY_RADIO] pending detected for game ${pending_game_num}"
		local strategy_diff game_num best_score scores
		strategy_diff=$(python3 -c "import json; print(json.load(open('$pending_file')).get('strategy_diff',''))" 2>/dev/null || echo "")
		game_num=$(python3 -c "import json; print(json.load(open('$pending_file')).get('game_num',$game_num))" 2>/dev/null || echo "$game_num")
		best_score=$(python3 -c "import json; print(json.load(open('$pending_file')).get('best_score',0))" 2>/dev/null || echo 0)
		scores=$(python3 -c "import json; print(json.load(open('$pending_file')).get('scores',''))" 2>/dev/null || echo "")

		[ -z "$strategy_diff" ] && return 0

		if _try_game_corner "$game_num" "strategy"; then
			start_radio_corner_strategy "$strategy_diff" "$scores" "$game_num" "$best_score" &
		else
			log "[RADIO:strategy] duplicate skip: scheduler pre-check for game=${game_num}"
		fi
		rm -f "$pending_file"
	}
	_handle_pending_strategy_radio

	# --- 1時間に1回のランダムコーナー ---
	local current_hour current_min today timed_corner_fired=false
	current_hour=$(date +%H)
	current_min=$(date +%M)
	today=$(date +%Y%m%d)

	_try_timed_corner() {
		local marker_key="$1" target_hh="$2" target_mm="$3"
		local marker="$TMP_MARKERS_DIR/.timed_corner_done_${today}_${marker_key}"
		local inflight="$TMP_MARKERS_DIR/.timed_corner_inflight_${today}_${marker_key}"
		[ -f "$marker" ] && return 1
		if ! mkdir "$inflight" 2>/dev/null; then
			return 1 # another scheduler beat us
		fi
		local target=$((10#$target_hh * 60 + 10#$target_mm))
		local now=$((10#$current_hour * 60 + 10#$current_min))
		local diff=$((now - target))
		[ "$diff" -lt 0 ] && diff=$((-diff))
		[ "$diff" -le 15 ] || {
			rmdir "$inflight" 2>/dev/null
			return 1
		}
		return 0
	}

	# 成功マーカーを作成するラッパー (バックグラウンドジョブ内で使用)
	_run_timed_corner() {
		local marker_key="$1" func="$2"
		shift 2
		"$func" "$@" &
		local _bg_pid=$!
		wait "$_bg_pid"
		local _exit_code=$?
		if [ "$_exit_code" -eq 0 ]; then
			touch "$TMP_MARKERS_DIR/.timed_corner_done_${today}_${marker_key}"
		fi
		rmdir "$TMP_MARKERS_DIR/.timed_corner_inflight_${today}_${marker_key}" 2>/dev/null
	}

	# stale inflight marker クリーンアップ (前日以前を一掃)
	local _yesterday_marker_inf=$TMP_MARKERS_DIR/.timed_corner_inflight_$(date -d yesterday +%Y%m%d)_*
	rm -f $_yesterday_marker_inf 2>/dev/null
	# 無日付の legacy marker のみ削除 (日付付き marker は保護)
	for _f in "$TMP_MARKERS_DIR"/.timed_corner_inflight_*; do
		[ -e "$_f" ] || continue
		case "$(basename "$_f")" in
		.timed_corner_inflight_[0-9]*) ;;
		*) rm -f "$_f" ;;
		esac
	done

	# ランダムコーナー: 1時間に1回、プールからランダムに1つ選ぶ
	# 毎時05分のウィンドウで発火（themeのスロットと同じタイミング）
	local _random_corner_marker="random_corner_${current_hour}"
	if _try_timed_corner "$_random_corner_marker" "$current_hour" 5; then
		local _random_pool=(
			"finance"
			"danger_zone"
			"music_knowledge"
			"health"
			"rakugo"
			"breakfast"
			"weather"
			"wiki"
			"sightseeing"
			"lunch"
			"fortune"
			"devil_dict"
			"ai_knowledge"
			"soviet_quiz"
			"market"
			"bluegrass"
			"dinner"
			"redefine"
			"soviet_lifehack"
			"world_dinner"
			"whatday"
			"zaitech"
			"deals"
			"fudosan"
			"survival"
			"night_snack"
			"local_japan"
		)
		local _pick="${_random_pool[$((RANDOM % ${#_random_pool[@]}))]}"
		log "[RADIO:random] 1時間に1回のランダムコーナー: ${_pick} (hour=${current_hour})"
		case "$_pick" in
		finance) _run_timed_corner "$_random_corner_marker" start_radio_corner_finance "$game_num" "$score" & ;;
		danger_zone) _run_timed_corner "$_random_corner_marker" start_radio_corner_danger_zone "$game_num" "$score" & ;;
		music_knowledge) _run_timed_corner "$_random_corner_marker" start_radio_corner_music_knowledge "$game_num" "$score" & ;;
		health) _run_timed_corner "$_random_corner_marker" start_radio_corner_health "$game_num" "$score" & ;;
		rakugo) _run_timed_corner "$_random_corner_marker" start_radio_corner_rakugo "$game_num" "$score" & ;;
		breakfast) _run_timed_corner "$_random_corner_marker" start_radio_corner_breakfast "$game_num" "$score" & ;;
		weather) _run_timed_corner "$_random_corner_marker" start_radio_corner_weather "$game_num" "$score" & ;;
		wiki) _run_timed_corner "$_random_corner_marker" start_radio_corner_wiki "$game_num" "$score" & ;;
		sightseeing) _run_timed_corner "$_random_corner_marker" start_radio_corner_sightseeing "$game_num" "$score" & ;;
		lunch) _run_timed_corner "$_random_corner_marker" start_radio_corner_lunch "$game_num" "$score" & ;;
		fortune) _run_timed_corner "$_random_corner_marker" start_radio_corner_fortune "$game_num" "$score" & ;;
		devil_dict) _run_timed_corner "$_random_corner_marker" start_radio_corner_devil_dict "$game_num" "$score" & ;;
		ai_knowledge) _run_timed_corner "$_random_corner_marker" start_radio_corner_ai_knowledge "$game_num" "$score" & ;;
		soviet_quiz) _run_timed_corner "$_random_corner_marker" start_radio_corner_soviet_quiz "$game_num" "$score" & ;;
		market) _run_timed_corner "$_random_corner_marker" start_radio_corner_market "$game_num" "$score" & ;;
		bluegrass) _run_timed_corner "$_random_corner_marker" start_radio_corner_bluegrass "$game_num" "$score" & ;;
		dinner) _run_timed_corner "$_random_corner_marker" start_radio_corner_dinner "$game_num" "$score" & ;;
		redefine) _run_timed_corner "$_random_corner_marker" start_radio_corner_redefine "$game_num" "$score" & ;;
		soviet_lifehack) _run_timed_corner "$_random_corner_marker" start_radio_corner_soviet_lifehack "$game_num" "$score" & ;;
		world_dinner) _run_timed_corner "$_random_corner_marker" start_radio_corner_world_dinner "$game_num" "$score" & ;;
		whatday) _run_timed_corner "$_random_corner_marker" start_radio_corner_whatday "$game_num" "$score" & ;;
		zaitech) _run_timed_corner "$_random_corner_marker" start_radio_corner_zaitech "$game_num" "$score" & ;;
		deals) _run_timed_corner "$_random_corner_marker" start_radio_corner_deals "$game_num" "$score" & ;;
		fudosan) _run_timed_corner "$_random_corner_marker" start_radio_corner_fudosan "$game_num" "$score" & ;;
		survival) _run_timed_corner "$_random_corner_marker" start_radio_corner_survival "$game_num" "$score" & ;;
		night_snack) _run_timed_corner "$_random_corner_marker" start_radio_corner_night_snack "$game_num" "$score" & ;;
		local_japan) _run_timed_corner "$_random_corner_marker" start_radio_corner_local_japan "$game_num" "$score" & ;;
		esac
		timed_corner_fired=true
	fi

	# ニュース/jiji/theme は1日1回ではなく時刻スロットごとに実行する。
	# 日付＋コーナー名だけの marker だと、その日の最初の1回で以後すべて止まる。
	local recurring_hour
	recurring_hour=$(date +%H)

	# ニュース: 毎時00,30に取得・再生（news/jiji交互、15分間隔）
	if _try_timed_corner "news_${recurring_hour}_00" "$recurring_hour" 0; then
		timed_corner_fired=true
		_run_timed_corner "news_${recurring_hour}_00" fetch_and_play_news "$game_num" "$score" &
	elif _try_timed_corner "news_${recurring_hour}_30" "$recurring_hour" 30; then
		timed_corner_fired=true
		_run_timed_corner "news_${recurring_hour}_30" fetch_and_play_news "$game_num" "$score" &
	fi

	# 雑談ラジオ: 毎時05（1時間に1回）
	if _try_timed_corner "theme_${recurring_hour}_05" "$recurring_hour" 5; then
		timed_corner_fired=true
		_run_timed_corner "theme_${recurring_hour}_05" start_random_radio_corner "$game_num" "$score" &
	fi

	# 時事ニュース(jiji): 毎時15,45（ニュースと交互、15分間隔）
	if _try_timed_corner "jiji_${recurring_hour}_15" "$recurring_hour" 15; then
		_run_timed_corner "jiji_${recurring_hour}_15" _run_jiji_corner_guarded "$game_num" "$score" &
	elif _try_timed_corner "jiji_${recurring_hour}_45" "$recurring_hour" 45; then
		_run_timed_corner "jiji_${recurring_hour}_45" _run_jiji_corner_guarded "$game_num" "$score" &
	fi
}

# deferred キュー滞留による自動ラジオ生成の抑止判定。
# 0 を返す = 生成をブロック, 1 を返す = 生成を許可。
_radio_generation_blocked_by_backpressure() {
	local count=0
	count=$(_radio_deferred_queue_count)
	if [ "$count" -gt "${RADIO_DEFERRED_QUEUE_MAX:-5}" ]; then
		if [ ! -f "$TMP_MARKERS_DIR/.radio_queue_backpressure_active" ]; then
			touch "$TMP_MARKERS_DIR/.radio_queue_backpressure_active" 2>/dev/null || true
			log "[RADIO] deferred queue 滞留 ${count} 件 (> ${RADIO_DEFERRED_QUEUE_MAX:-5}) → 新規ラジオ生成を抑制"
		fi
		return 0
	fi
	if [ -f "$TMP_MARKERS_DIR/.radio_queue_backpressure_active" ]; then
		rm -f "$TMP_MARKERS_DIR/.radio_queue_backpressure_active" 2>/dev/null || true
		log "[RADIO] deferred queue ${count} 件 (≤ ${RADIO_DEFERRED_QUEUE_MAX:-5}) → ラジオ生成を再開"
	fi
	return 1
}

# ピーク時間帯限定の抑止判定。issue #5 のバックプレッシャー(MAX=5)とは独立の追加ゲートで、
# ピーク中はキューが完全に空(0件)になるまで新規生成をブロックする（issue #5より厳しい）。
# 同一tick内で複数コーナーの時刻窓が重なった場合、ゲート開放時にまとめて生成される
# ことがある（開放直後の1件ずつの厳密な直列化までは保証しない）。
# ピーク外・無効時は常に許可(1を返す)。PEAK_HOURS_QUEUE_GATE_ENABLED=0 で即無効化。
# 0 を返す = 生成をブロック, 1 を返す = 生成を許可。
_radio_generation_blocked_by_peak_hour_queue() {
	# 無効時・ピーク外はマーカーを掃除してから抜ける。マーカーを残したまま
	# 早期returnすると、次にピークへ再突入した際 "既にアクティブ" 扱いになり
	# 抑制開始ログが出ない（サイレントに止まって見える）ため。
	if [ "${PEAK_HOURS_QUEUE_GATE_ENABLED:-1}" != "1" ] || ! _is_peak_hours; then
		if [ -f "$TMP_MARKERS_DIR/.radio_peak_queue_gate_active" ]; then
			rm -f "$TMP_MARKERS_DIR/.radio_peak_queue_gate_active" 2>/dev/null || true
			log "[RADIO] ピーク時間帯終了/ゲート無効化 → キューゲートを解除"
		fi
		return 1
	fi
	local count=0
	count=$(_radio_deferred_queue_count)
	if [ "$count" -gt 0 ]; then
		if [ ! -f "$TMP_MARKERS_DIR/.radio_peak_queue_gate_active" ]; then
			touch "$TMP_MARKERS_DIR/.radio_peak_queue_gate_active" 2>/dev/null || true
			log "[RADIO] ピーク時間帯: deferred queue ${count} 件残存 → 空になるまで新規生成を抑制"
		fi
		return 0
	fi
	if [ -f "$TMP_MARKERS_DIR/.radio_peak_queue_gate_active" ]; then
		rm -f "$TMP_MARKERS_DIR/.radio_peak_queue_gate_active" 2>/dev/null || true
		log "[RADIO] ピーク時間帯: deferred queue 0件 → 新規生成を再開"
	fi
	return 1
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
		if _try_game_corner "$game_num" "news"; then
			start_radio_corner_news "$game_num" "$score"
		else
			log "[RADIO:news] duplicate skip: scheduler pre-check for game=${game_num}"
		fi
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
	local candidates=("theme" "soviet")

	local pick="${candidates[$((RANDOM % ${#candidates[@]}))]}"
	log "[RADIO] コーナー選択: ${pick}"

	case "$pick" in
	theme)
		if _try_game_corner "$game_num" "theme"; then
			start_radio_corner_theme "$game_num" "$score"
		else
			log "[RADIO:theme] duplicate skip: scheduler pre-check for game=${game_num}"
		fi
		;;
	soviet)
		if _try_game_corner "$game_num" "soviet"; then
			start_radio_corner_soviet "$game_num" "$score"
		else
			log "[RADIO:soviet] duplicate skip: scheduler pre-check for game=${game_num}"
		fi
		;;
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
	if ((10#$current_hour >= news_night_start_hour && 10#$current_hour < news_night_end_hour)); then
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

	if ((game_num % current_news_interval == news_phase)); then
		if [ "$skip_nonessential_radio" = true ]; then
			log "[NEWS] skip: comment backlog=${comment_total} (queued=${comment_queued}, playing=${comment_playing}, threshold=${comment_backlog_skip_threshold})"
		else
			fetch_and_play_news "$game_num" "$score" &
		fi
	fi

	if ((game_num % radio_interval == radio_phase)); then
		if [ "$skip_nonessential_radio" = true ]; then
			log "[RADIO] skip random corner: comment backlog=${comment_total} (queued=${comment_queued}, playing=${comment_playing}, threshold=${comment_backlog_skip_threshold})"
		else
			start_random_radio_corner "$game_num" "$score" &
		fi
	fi

	# 時事ニュースコーナー: サイクル4,8ゲーム目（2つで30分間隔相当）
	if ((game_num % ${MIN_GAMES_BEFORE_IMPROVE:-12} == 4 || game_num % ${MIN_GAMES_BEFORE_IMPROVE:-12} == 8)); then
		if [ "$skip_nonessential_radio" = true ]; then
			log "[JIJI] skip: comment backlog=${comment_total} (queued=${comment_queued}, playing=${comment_playing}, threshold=${comment_backlog_skip_threshold})"
		else
			_run_jiji_corner_guarded "$game_num" "$score" &
		fi
	fi
}
