#!/bin/bash
# 探索モード (EXPLORE_MODE=1) では音声合成・再生を行わない
[ "${EXPLORE_MODE:-0}" = "1" ] && exit 0
# say_enqueue.sh - mkdirロックベースのsayキュー（FIFO順次再生）
#
# 使い方: ./say_enqueue.sh [--no-preempt] [--render-only <wav_file>] [--wav]
#           [--wav-playlist <playlist> --caption-chunks <chunks_file>]
#           <content_file> [rate] [pre_delay_sec]
#
# --no-preempt: 後方互換のため受け付ける（現在は常に順次再生）
#
# 動作:
#   1. コンテンツコピー
#   2. mkdirロック取得（取得できるまで待機）
#   3. ロック内: 前のsay PID待ち
#   4. ロック内: say 再生（異常終了時はリトライ）
#   5. ロック解放
#   6. クリーンアップ

set -uo pipefail
cd "$(dirname "$0")"
# .env を毎回読み込んで、リアルタイムに VOICEVOX_URL 等の設定を反映させる
[ -f .env ] && . ./.env
source lib/outbound_queue.sh 2>/dev/null || true
if [ -f lib/closed_captions.sh ] && source lib/closed_captions.sh; then
	:
else
	# Partial/rollback deployments must never break the existing audio path.
	docich_cc_init() { :; }
	docich_cc_is_enabled() { return 1; }
	docich_cc_start_plan() { return 1; }
	docich_cc_prepare() { return 1; }
	docich_cc_commit() { return 1; }
	docich_cc_clear() { return 0; }
	docich_cc_cleanup() { :; }
fi

# Linux 判定: SOREN_OBS_PLATFORM=linux が最優先、無ければ uname。
# Linux では BlackHole/afplay/audiotoolbox/say の代わりに
# PulseAudio null-sink (SAY_AUDIO_DEVICE=soren_null) へ paplay/ffplay で再生する。
IS_LINUX=0
case "${SOREN_OBS_PLATFORM:-$(uname -s 2>/dev/null || echo Darwin)}" in
linux | Linux | linux-gnu) IS_LINUX=1 ;;
esac

# フラグ処理
NO_PREEMPT=false
WAV_MODE=false
RENDER_ONLY=false
RENDER_OUTPUT=""
WAV_PLAYLIST_MODE=false
WAV_PLAYLIST_FILE=""
CAPTION_CHUNKS_FILE=""
while true; do
	case "${1:-}" in
	--no-preempt)
		NO_PREEMPT=true
		shift
		;;
	--wav)
		WAV_MODE=true
		shift
		;;
	--render-only)
		RENDER_ONLY=true
		RENDER_OUTPUT="${2:?Usage: say_enqueue.sh --render-only <wav_file> <content_file> [rate]}"
		shift 2
		;;
	--wav-playlist)
		WAV_PLAYLIST_MODE=true
		WAV_PLAYLIST_FILE="${2:?Usage: say_enqueue.sh --wav-playlist <playlist> --caption-chunks <chunks_file> <content_file> [rate]}"
		shift 2
		;;
	--caption-chunks)
		CAPTION_CHUNKS_FILE="${2:?Usage: say_enqueue.sh --wav-playlist <playlist> --caption-chunks <chunks_file> <content_file> [rate]}"
		shift 2
		;;
	*) break ;;
	esac
done

QUEUE_DIR="tmp/.say_queue"
mkdir -p "$QUEUE_DIR"

CONTENT_FILE="${1:?Usage: say_enqueue.sh [--no-preempt] <content_file> [rate]}"
RATE="${2:-120}"
SAY_RETRY_MAX="${SAY_RETRY_MAX:-6}"
SAY_RETRY_SLEEP_SEC="${SAY_RETRY_SLEEP_SEC:-2}"
SAY_RETRY_MAX_SLEEP_SEC="${SAY_RETRY_MAX_SLEEP_SEC:-20}"
SAY_TRUNCATE_RATIO="${SAY_TRUNCATE_RATIO:-0.85}"
SAY_TRUNCATE_GRACE_SEC="${SAY_TRUNCATE_GRACE_SEC:-3}"
SAY_TRUNCATE_MIN_EXPECTED_SEC="${SAY_TRUNCATE_MIN_EXPECTED_SEC:-15}"
# 音声が一定時間流れた後の異常終了は、同じ WAV を先頭から再試行すると
# 既に聞こえた部分が二重になる。短い起動失敗だけは従来どおり再試行する。
SAY_PARTIAL_PLAYBACK_NO_RETRY_SEC="${SAY_PARTIAL_PLAYBACK_NO_RETRY_SEC:-2}"
case "$SAY_PARTIAL_PLAYBACK_NO_RETRY_SEC" in
''|*[!0-9]*) SAY_PARTIAL_PLAYBACK_NO_RETRY_SEC=2 ;;
esac
SAY_HANG_EXTRA_SEC="${SAY_HANG_EXTRA_SEC:-120}"
SAY_CHUNK_GAP_SEC="${SAY_CHUNK_GAP_SEC:-0.5}"

# --- speaking状態管理（Twitch広告スヌーズ用） ---
SPEAKING_STATE_FILE="${SPEAKING_STATE_FILE:-tmp/state/speaking.json}"
SPEAKING_GRACE_SEC="${SPEAKING_GRACE_SEC:-3}"
_speaking_enter() {
	local _reason="${1:-tts}"
	mkdir -p "$(dirname "$SPEAKING_STATE_FILE")" 2>/dev/null || true
	python3 - "$SPEAKING_STATE_FILE" "$_reason" <<'PY' 2>/dev/null || true
import json, sys, time, os
from pathlib import Path
path, reason = sys.argv[1], sys.argv[2] if len(sys.argv)>2 else "tts"
state={"speaking": True, "since": int(time.time()), "reason": reason, "pid": os.getpid()}
# atomic write
tmp = str(path) + ".tmp"
os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
with open(tmp, "w", encoding="utf-8") as f:
    json.dump(state, f, ensure_ascii=False)
    f.write("\n")
os.replace(tmp, path)
PY
	# Twitch広告スヌーズを非同期で試行（失敗してもTTSは継続）
	if [ -f "lib/twitch_ads.sh" ]; then
		# shellcheck source=/dev/null
		source "lib/twitch_ads.sh" 2>/dev/null || true
		if type twitch_ads_maybe_snooze >/dev/null 2>&1; then
			twitch_ads_maybe_snooze "$_reason" >/dev/null 2>&1 &
		fi
	fi
	# 長時間再生中は 240s ごとに再スヌーズ（5分効果をつなぐ）
	(
		local _poll="${TWITCH_SNOOZE_POLL_SEC:-240}"
		case "$_poll" in ''|*[!0-9]*) _poll=240 ;; esac
		while [ -f "$SPEAKING_STATE_FILE" ] && [ "$_poll" -gt 0 ]; do
			sleep "$_poll" 2>/dev/null || break
			[ -f "$SPEAKING_STATE_FILE" ] || break
			if [ -f "lib/twitch_ads.sh" ]; then
				source "lib/twitch_ads.sh" 2>/dev/null || true
				if type twitch_ads_maybe_snooze >/dev/null 2>&1; then
					twitch_ads_maybe_snooze "speaking_poll" >/dev/null 2>&1 || true
				fi
			fi
		done
	) &
	SPEAKING_POLL_PID=$!
}
_speaking_leave() {
	# grace期間後にキュー残を確認し、残があれば speaking を維持
	local _grace="${SPEAKING_GRACE_SEC:-3}"
	case "$_grace" in ''|*[!0-9]*) _grace=3 ;; esac
	[ -n "${SPEAKING_POLL_PID:-}" ] && kill "$SPEAKING_POLL_PID" 2>/dev/null || true
	wait "${SPEAKING_POLL_PID:-}" 2>/dev/null || true
	SPEAKING_POLL_PID=""
	# 連続キュー対策: tmp/.comment_queue と tmp/.say_queue の残をチェック
	if [ "$_grace" -gt 0 ]; then
		sleep "$_grace" 2>/dev/null || true
	fi
	local _cq="${COMMENT_QUEUE_DIR:-tmp/.comment_queue}"
	local _sq="tmp/.say_queue"
	if ls "$_cq"/comment_*.txt 2>/dev/null | grep -q .; then
		return 0
	fi
	if ls "$_sq"/*.txt 2>/dev/null | grep -q .; then
		return 0
	fi
	rm -f "$SPEAKING_STATE_FILE" 2>/dev/null || true
}
# speaking状態は _cleanup でクリア（既存 trap と統合）
# _speaking_leave は _cleanup 内で呼ばれる

# timeout コマンド解決 (macOS coreutils vs GNU) — 原因調査中は無効化
TIMEOUT_CMD=""
# for _tc in timeout gtimeout; do
# 	if command -v "$_tc" >/dev/null 2>&1; then
# 		TIMEOUT_CMD="$_tc"
# 		break
# 	fi
# done
# デフォルト値（VOICEVOX合成の外側タイムアウト; 秒指定なしはSIGKILLまで待機は危険）
VOICEVOX_SYNTH_TIMEOUT_SEC="${VOICEVOX_SYNTH_TIMEOUT_SEC:-90}"
VOICEVOX_SYNTH_KILL_AFTER_SEC="${VOICEVOX_SYNTH_KILL_AFTER_SEC:-10}"

# --- VOICEVOX ASMR (ささやき系) 話者選択 ---
# _pick_asmr_voicevox_speaker: ささやき系から "ID|名前/スタイル" を stdout に返す（粛清フィルタ適用）
_pick_asmr_voicevox_speaker() {
	local vo_url="${VOICEVOX_URL:-http://127.0.0.1:50021}"
	curl -s --max-time 3 "$vo_url/speakers" 2>/dev/null | python3 -c "
import json, sys, random
try:
    with open('config/voicevox_exclude_ids.txt') as f:
        import re; exclude_ids = {int(m.group()) for l in f for m in [re.match(r'\d+', l.strip())] if m}
except FileNotFoundError:
    exclude_ids = set()
speakers = json.load(sys.stdin)
pool = [(s['name'], st['id'], st['name']) for s in speakers for st in s.get('styles', []) if st.get('type', 'talk') == 'talk' and 'ささやき' in st['name'] and st['id'] not in exclude_ids]
if pool:
    name, sid, style = random.choice(pool)
    print(f'{sid}|{name}/{style}', end='')
else:
    print('36|四国めたん/ささやき', end='')
" 2>/dev/null
}

# --- VOICEVOX ランダム話者選択 ---
# _pick_random_voicevox_speaker: "ID|名前/スタイル" を stdout に返す
_pick_random_voicevox_speaker() {
	local vo_url="${VOICEVOX_URL:-http://127.0.0.1:50021}"
	curl -s --max-time 3 "$vo_url/speakers" 2>/dev/null | python3 -c "
import json, sys, random
exclude = {'玄野武宏','白上虎太郎','後鬼','ちび式じい','†聖騎士 紅桜†','栗田まろん','Voidoll'}
try:
    with open('config/voicevox_exclude_ids.txt') as f:
        import re; exclude_ids = {int(m.group()) for l in f for m in [re.match(r'\d+', l.strip())] if m}
except FileNotFoundError:
    exclude_ids = set()
speakers = json.load(sys.stdin)
exclude_styles = {'ささやき', 'セクシー'}
pool = [(s['name'], st['id'], st['name']) for s in speakers if s['name'] not in exclude for st in s.get('styles', []) if st.get('type', 'talk') == 'talk' and st['id'] not in exclude_ids and not any(k in st['name'] for k in exclude_styles)]
if pool:
    name, sid, style = random.choice(pool)
    print(f'{sid}|{name}/{style}', end='')
else:
    print('3|ずんだもん/ノーマル', end='')
" 2>/dev/null
}

# --- COEIROINK TTS切替 ---
# tmp/coeiroink_voice.txt の内容で動作を決定:
#   "random"        → 毎回ランダムに話者選択
#   "uuid|styleId"  → 固定話者
#   ファイルなし     → macOS say
# !wakana/!moko で固定、!random でランダム、!say で無効化
_COE_VOICES=(
	"3c37646f-3881-5374-2a83-149267990abc|0"          # つくよみちゃん れいせい
	"d41bcbd9-f4a9-4e10-b000-7a431568dd01|100"        # AI声優-金苗 のーまる
	"fb1a910e-208f-11ee-8dde-0242ac1c000c|981131762"  # モコちゃん よろこび
	"fb1a910e-208f-11ee-8dde-0242ac1c000c|981131765"  # モコちゃん ろぼろぼ
	"8e99d620-87d3-11ed-870a-0242ac1c000c|905192261"  # ワカナ normal
	"9bf2ab50-c756-11ec-9374-0242ac1c0002|1403759395" # ナースロボ 通常
	"6e0539ea-a6a7-11f0-8d2f-0242ac1c000c|172697038"  # AⅡowa β
	"f0d1a286-64dd-11ee-babd-0242ac1c000c|1486940343" # 芽々子 のーまる
)
if [ -f "tmp/coeiroink_voice.txt" ]; then
	_coe_line=$(cat "tmp/coeiroink_voice.txt" 2>/dev/null)
	if [ "$_coe_line" = "random" ]; then
		_coe_pick="${_COE_VOICES[$((RANDOM % ${#_COE_VOICES[@]}))]}"
		COEIROINK_SPEAKER_UUID="${_coe_pick%%|*}"
		COEIROINK_STYLE_ID="${_coe_pick##*|}"
	else
		COEIROINK_SPEAKER_UUID="${_coe_line%%|*}"
		COEIROINK_STYLE_ID="${_coe_line##*|}"
	fi
	USE_COEIROINK="${USE_COEIROINK:-1}"
else
	USE_COEIROINK="${USE_COEIROINK:-0}"
	COEIROINK_SPEAKER_UUID="${COEIROINK_SPEAKER_UUID:-8e99d620-87d3-11ed-870a-0242ac1c000c}"
	COEIROINK_STYLE_ID="${COEIROINK_STYLE_ID:-905192261}"
fi

# --- VOICEVOX TTS切替 ---
# tmp/voicevox_voice.txt があれば VOICEVOX を使用 (COEIROINK より優先)
# ファイル内容: speaker ID (例: 109) or "random"
#
# 声の永続化: リトライ時に同じ声を使うため、ランダム選択結果を
# <content_file>.voice サイドカーファイルに保存する。
# サイドカーが存在する場合はランダム選択をスキップ。
USE_VOICEVOX=0
VOICEVOX_SPEAKER="${VOICEVOX_SPEAKER:-108}"
SAY_VOICEVOX_SPEAKER_OVERRIDE="${SAY_VOICEVOX_SPEAKER_OVERRIDE:-}"
# コンテンツファイルのサイドカー .voice ファイルをチェック（リトライ時の声の一貫性）
# deferred ラジオファイルのみ対象（コメント等では不要なので作成しない）
_content_file="${1:-}"
_voice_sidecar=""
if [ -n "$_content_file" ] && [[ "$_content_file" == */.radio_deferred_queue/* ]]; then
	_voice_base="${_content_file%.playing}"
	_voice_base="${_voice_base%.txt}"
	_voice_sidecar="${_voice_base}.voice"
fi
if [ -n "$_voice_sidecar" ] && [ -f "$_voice_sidecar" ]; then
	_saved_speaker=$(cat "$_voice_sidecar" 2>/dev/null)
	if [ -n "$_saved_speaker" ]; then
		VOICEVOX_SPEAKER="$_saved_speaker"
		USE_VOICEVOX=1
		USE_COEIROINK=0
		VOICEVOX_RANDOM_MODE=0
	fi
elif [ -f "tmp/voicevox_voice.txt" ]; then
	_vo_line=$(cat "tmp/voicevox_voice.txt" 2>/dev/null)
	if [ "$_vo_line" = "random" ]; then
		_vo_result=$(_pick_random_voicevox_speaker)
		VOICEVOX_SPEAKER="${_vo_result%%|*}"
		VOICEVOX_RANDOM_VOICE_NAME="${_vo_result#*|}"
		VOICEVOX_RANDOM_MODE=1
		# サイドカーに保存（リトライ時に同じ声を使う）
		if [ -n "$_voice_sidecar" ]; then
			echo "$VOICEVOX_SPEAKER" >"$_voice_sidecar" 2>/dev/null || true
		fi
	else
		VOICEVOX_SPEAKER="$_vo_line"
		VOICEVOX_RANDOM_MODE=0
	fi
	USE_VOICEVOX=1
	USE_COEIROINK=0
fi

# 呼び出し元が明示した speaker override を最優先する。
if [ -n "$SAY_VOICEVOX_SPEAKER_OVERRIDE" ]; then
	VOICEVOX_SPEAKER="$SAY_VOICEVOX_SPEAKER_OVERRIDE"
	VOICEVOX_RANDOM_MODE=0
	VOICEVOX_RANDOM_VOICE_NAME=""
	USE_VOICEVOX=1
	USE_COEIROINK=0
fi

PID_FILE="$QUEUE_DIR/pid"
LOCK_DIR="$QUEUE_DIR/.lock"
LOCK_OWNER_FILE="$LOCK_DIR/owner_pid"
LOCK_HEARTBEAT_FILE="$LOCK_DIR/heartbeat"
CURRENT_SOURCE_FILE="$QUEUE_DIR/current_source"
PLAYED_LOG_FILE="$QUEUE_DIR/played.log"
DEBUG_LOG_FILE="$QUEUE_DIR/debug.log"
LAST_RADIO_PLAYED_FILE="tmp/state/radio_talk_played"
LOCK_STALE_SEC=180
VOICEVOX_SYNTH_LOCK="$QUEUE_DIR/.voicevox_synth_lock"
VOICEVOX_SYNTH_OWNER_FILE="$VOICEVOX_SYNTH_LOCK/owner_pid"
VOICEVOX_SYNTH_HEARTBEAT_FILE="$VOICEVOX_SYNTH_LOCK/heartbeat"
VOICEVOX_SYNTH_LOCK_STALE_SEC="${VOICEVOX_SYNTH_LOCK_STALE_SEC:-180}"
VOICEVOX_SYNTH_LOCK_DEAD_GRACE_SEC="${VOICEVOX_SYNTH_LOCK_DEAD_GRACE_SEC:-10}"
VOICEVOX_SYNTH_LOCK_HUNG_SEC="${VOICEVOX_SYNTH_LOCK_HUNG_SEC:-60}"
VOICEVOX_SYNTH_PRIORITY_WAIT_DIR="$QUEUE_DIR/.voicevox_synth_priority_waiters"
VOICEVOX_SYNTH_PRIORITY_WAIT_FILE=""
VOICEVOX_SYNTH_PRIORITY_WAIT_HELD=0
VOICEVOX_SYNTH_PRIORITY_WAIT_STALE_SEC="${VOICEVOX_SYNTH_PRIORITY_WAIT_STALE_SEC:-60}"
VOICEVOX_SYNTH_LOCK_BASE="$VOICEVOX_SYNTH_LOCK"
VOICEVOX_SYNTH_DISTRIBUTED="${VOICEVOX_SYNTH_DISTRIBUTED:-1}"
VOICEVOX_SYNTH_ACTIVE_URL=""
VOICEVOX_SYNTH_ACTIVE_LOCK=""

# --- VOICEVOX分散合成ヘルパー (Tailscaleチェーン: windows→mac→vm local) ---
# VOICEVOX_URLS="http://windows:50021,http://mac:50021,http://127.0.0.1:50021" を
# speech.py と同じロジックで解釈し、キューされていたら次はmac→localへ分散する。
# 空いているエンドポイントのロックだけを mkdir で非ブロック取得し、取得できたら
# VOICEVOX_ACTIVE_URL / VOICEVOX_URLS をそのURL優先に書き換えて docich へ渡す。
_voicevox_urls_chain() {
	python3 - <<'PY' 2>/dev/null
import os, re
raw = os.environ.get("VOICEVOX_URLS", "")
urls = []
if raw and raw.strip():
    for token in re.split(r"[,\s]+", raw):
        token = token.strip().rstrip("/")
        if token and token not in urls:
            urls.append(token)
else:
    primary = os.environ.get("VOICEVOX_URL_PRIMARY", "") or os.environ.get("VOICEVOX_URL_REMOTE", "")
    fallback = os.environ.get("VOICEVOX_URL_FALLBACK", "") or "http://127.0.0.1:50021"
    direct = os.environ.get("VOICEVOX_URL", "") or (primary or "http://127.0.0.1:50021")
    local = os.environ.get("VOICEVOX_URL_LOCAL", "") or "http://127.0.0.1:50021"
    for cand in (primary, direct, fallback, local):
        cand = cand.strip().rstrip("/")
        if cand and cand not in urls:
            urls.append(cand)
for u in urls:
    print(u)
PY
}

_voicevox_url_hash() {
	local url="$1" h=""
	if command -v md5sum >/dev/null 2>&1; then
		h=$(printf '%s' "$url" | md5sum 2>/dev/null | awk '{print $1}')
	elif command -v md5 >/dev/null 2>&1; then
		h=$(printf '%s' "$url" | md5 -q 2>/dev/null)
	else
		h=$(printf '%s' "$url" | cksum 2>/dev/null | awk '{print $1}')
	fi
	printf '%s' "${h:0:12}"
}

_voicevox_state_file() {
	if [ -n "${VOICEVOX_STATE_FILE:-}" ]; then
		printf '%s' "$VOICEVOX_STATE_FILE"
	else
		printf '%s' "tmp/state/voicevox_endpoints.json"
	fi
}

_voicevox_endpoint_status() {
	local url="$1" sf
	sf=$(_voicevox_state_file)
	python3 - "$url" "$sf" <<'PY' 2>/dev/null
import json, time, sys
url = sys.argv[1]
sf = sys.argv[2]
try:
    data = json.loads(open(sf, encoding="utf-8").read())
except Exception:
    print("ready")
    sys.exit(0)
ep = data.get("endpoints", {}).get(url, {})
if ep.get("disabled"):
    print("disabled")
elif ep.get("next_retry_at") and float(ep.get("next_retry_at", 0)) > time.time():
    print("backoff")
else:
    print("ready")
PY
}

_voicevox_reorder_urls() {
	local selected="$1"
	python3 - "$selected" <<'PY' 2>/dev/null
import os, re, sys
selected = sys.argv[1]
raw = os.environ.get("VOICEVOX_URLS", "")
urls = []
if raw and raw.strip():
    for token in re.split(r"[,\s]+", raw):
        token = token.strip().rstrip("/")
        if token and token not in urls:
            urls.append(token)
else:
    primary = os.environ.get("VOICEVOX_URL_PRIMARY", "") or os.environ.get("VOICEVOX_URL_REMOTE", "")
    fallback = os.environ.get("VOICEVOX_URL_FALLBACK", "") or "http://127.0.0.1:50021"
    direct = os.environ.get("VOICEVOX_URL", "") or (primary or "http://127.0.0.1:50021")
    local = os.environ.get("VOICEVOX_URL_LOCAL", "") or "http://127.0.0.1:50021"
    for cand in (primary, direct, fallback, local):
        cand = cand.strip().rstrip("/")
        if cand and cand not in urls:
            urls.append(cand)
if selected in urls:
    urls.remove(selected)
    urls.insert(0, selected)
print(",".join(urls))
PY
}

if [ ! -s "$CONTENT_FILE" ]; then
	echo "[say_enqueue] content file missing or empty: $CONTENT_FILE" >&2
	exit 1
fi
if [ "$WAV_PLAYLIST_MODE" = "true" ]; then
	if [ "$WAV_MODE" = "true" ] || [ "$RENDER_ONLY" = "true" ]; then
		echo "[say_enqueue] --wav-playlist cannot be combined with --wav or --render-only" >&2
		exit 2
	fi
	if [ ! -s "$WAV_PLAYLIST_FILE" ] || [ ! -s "$CAPTION_CHUNKS_FILE" ]; then
		echo "[say_enqueue] wav playlist and caption chunks are required" >&2
		exit 2
	fi
elif [ -n "$CAPTION_CHUNKS_FILE" ]; then
	echo "[say_enqueue] --caption-chunks requires --wav-playlist" >&2
	exit 2
fi

# ユニークトークン（PID + ランダム + 秒 で衝突回避）
MY_TOKEN="${BASHPID:-$$}_${RANDOM}_$(date +%s)"
MY_OWNER="${BASHPID:-$$}:${MY_TOKEN}"
MY_CONTENT="$QUEUE_DIR/content_${MY_TOKEN}.txt"
LOCK_HELD=0
VOICEVOX_SYNTH_LOCK_HELD=0
VOICEVOX_STREAM_HB_PID=""
LAUNCHED_SAY_PID=""
LAUNCHED_EXPECTED_SEC=0
CHROME_AUDIO_USED=0

# コンテンツをキュー用にコピー（元ファイルが消されても安全）
cp "$CONTENT_FILE" "$MY_CONTENT"
docich_cc_init "$MY_TOKEN" "$MY_CONTENT"

# 読み上げ修正: よくある誤読を事前に置換（WAVモード時はスキップ）
if [ "$WAV_MODE" = "false" ]; then
	if [ "$IS_LINUX" = "1" ]; then
		sed -i \
			-e 's/zoumotu3/ザモートゥ/g' \
			-e 's/静寂/せいじゃく/g' \
			-e 's/地政学的/ちせいがくてき/g' \
			-e 's/地政学/ちせいがく/g' \
			-e 's/WILDCARD/ワイルドカード/g' \
			-e 's/NISA/ニーサ/g' \
			-e 's/MAKE AMERICA GREAT AGAIN/メイクア・メリケン・グレートアゲイン/g' \
			-e 's/Make America Great Again/メイクア・メリケン・グレートアゲイン/g' \
			-e 's/MAGA/マガ/g' \
			-e 's/RTA IN JAPAN/アールティーエー・インジャパン/g' \
			-e 's/RTA in Japan/アールティーエー・インジャパン/g' \
			-e 's/MADE IN CHINA/メイドインチャイナ/g' \
			-e 's/Made in China/メイドインチャイナ/g' \
			"$MY_CONTENT"
	else
		sed -i '' \
			-e 's/zoumotu3/ザモートゥ/g' \
			-e 's/静寂/せいじゃく/g' \
			-e 's/地政学的/ちせいがくてき/g' \
			-e 's/地政学/ちせいがく/g' \
			-e 's/WILDCARD/ワイルドカード/g' \
			-e 's/NISA/ニーサ/g' \
			-e 's/MAKE AMERICA GREAT AGAIN/メイクア・メリケン・グレートアゲイン/g' \
			-e 's/Make America Great Again/メイクア・メリケン・グレートアゲイン/g' \
			-e 's/MAGA/マガ/g' \
			-e 's/RTA IN JAPAN/アールティーエー・インジャパン/g' \
			-e 's/RTA in Japan/アールティーエー・インジャパン/g' \
			-e 's/MADE IN CHINA/メイドインチャイナ/g' \
			-e 's/Made in China/メイドインチャイナ/g' \
			"$MY_CONTENT"
	fi
	# 国名置換はゲーム由来の本文だけが明示的に有効化する。汎用TTSでは
	# T-34やType 2 diabetesを国名と誤認せず、従来の小文字化だけを行う。
	if [ "${SAY_REPLACE_COUNTRY_REFERENCES:-0}" = "1" ]; then
		if ! python3 lib/normalize_speech_text.py --country-names "$MY_CONTENT" 2>/dev/null; then
			echo "[say_enqueue] ゲーム国名の正規化に失敗したため再生を中止します" >&2
			exit 1
		fi
	else
		python3 lib/normalize_speech_text.py "$MY_CONTENT" 2>/dev/null || true
	fi
fi

_infer_source_label() {
	local path="$1" base corner
	base=$(basename "$path")
	case "$path" in
	*"tmp/.comment_queue/"*.playing | *"tmp/.comment_queue/"*.txt)
		echo "comment"
		return 0
		;;
	*"tmp/.radio_deferred_queue/radio_"*.playing | *"tmp/.radio_deferred_queue/radio_"*.txt)
		corner=$(printf '%s' "$base" | sed -E 's/^radio_[0-9]+_[0-9]+_([^_]+)_.*/\1/')
		[ -n "$corner" ] && [ "$corner" != "$base" ] && {
			echo "radio:${corner}"
			return 0
		}
		;;
	*"/tmp/eloop_radio_talk_"*)
		echo "radio"
		return 0
		;;
	*"tmp/debug/radio_soviet_celebration.txt")
		echo "radio:celebration"
		return 0
		;;
	*"tmp/radio_celebration.txt")
		echo "radio:celebration"
		return 0
		;;
	*"radio_russia_celebration.txt")
		echo "radio:russia"
		return 0
		;;
	esac
	return 1
}

SOURCE_LABEL="${SAY_CONTEXT_LABEL:-}"
if [ -z "$SOURCE_LABEL" ]; then
	SOURCE_LABEL=$(_infer_source_label "$CONTENT_FILE" 2>/dev/null || true)
fi

_log() {
	echo "[say_enqueue $(date '+%H:%M:%S')] $*" >&2
	echo "[say_enqueue $(date '+%H:%M:%S') PID=$$/${BASHPID:-?}] $* | file=$CONTENT_FILE token=$MY_TOKEN label=${SOURCE_LABEL:-unknown}" >>tmp/.say_queue/debug.log
}

# GNU stat accepts -f with a different meaning and can emit filesystem details
# before returning non-zero. Keep the BSD and GNU assignments separate so a
# failed BSD-style probe can never be mixed with the GNU epoch value.
_file_mtime_epoch() {
	local target="$1" mtime
	mtime=$(stat -f %m "$target" 2>/dev/null) \
		|| mtime=$(stat -c %Y "$target" 2>/dev/null) \
		|| mtime=0
	case "$mtime" in
	'' | *[!0-9]*) mtime=0 ;;
	esac
	printf '%s\n' "$mtime"
}

_append_played_log() {
	local status="$1" now_h now_ts
	now_h=$(date '+%H:%M:%S')
	now_ts=$(date +%s)
	printf '[%s] %s [%s] %s\n' "$now_h" "$status" "${SOURCE_LABEL:-unknown}" "$CONTENT_FILE" >>"$PLAYED_LOG_FILE"
	if [ -f "$PLAYED_LOG_FILE" ] && [ "$(wc -l <"$PLAYED_LOG_FILE")" -gt 500 ]; then
		tail -200 "$PLAYED_LOG_FILE" >"${PLAYED_LOG_FILE}.tmp" && mv "${PLAYED_LOG_FILE}.tmp" "$PLAYED_LOG_FILE"
	fi
	case "${SOURCE_LABEL:-}" in
	radio:*)
		if [ "$status" = "played" ]; then
			printf '%s|%s|%s\n' "$now_ts" "${SOURCE_LABEL#radio:}" "$CONTENT_FILE" >"$LAST_RADIO_PLAYED_FILE"
		fi
		;;
	esac
}

_text_is_strategy_meta_failure() {
	local text
	text=$(printf '%s' "$1" | tr '\r\n' '  ' | sed 's/[[:space:]]\+/ /g')
	[ -n "$text" ] || return 0
	printf '%s' "$text" | grep -Eiq '申し訳(ありません|ございません|ない).*(エラーメッセージ|提供|ユーザーメッセージ|指示|タスク|情報|スクリーンショット)' && return 0
	printf '%s' "$text" | grep -Eiq '(エラーメッセージ|ユーザーメッセージ|具体的な指示|明確な指示|具体的なタスク).*(提供されてい|見当たりません|ありません|ない|不足)' && return 0
	printf '%s' "$text" | grep -Eiq '(入力|依頼|プロンプト|コンテキスト|戦略ヘッダー|本文).*(提供されてい|与えられてい|見当たりません|ありません|ない|不足)' && return 0
	printf '%s' "$text" | grep -Eiq '(エラー|エラーメッセージ).*(詳細|内容|原因|情報).*(提供されてい|見当たりません|ありません|ない|不足|不明)' && return 0
	printf '%s' "$text" | grep -Eiq '(ツール|権限|許可|WebFetch|検索|外部アクセス).*(確認|必要|できません|ありません|ない)' && return 0
	printf '%s' "$text" | grep -Eiq '(何も言えません|語ることはできません|控えておくべき|確認させてください|どうすればよい|何を.*すれば)' && return 0
	return 1
}

_is_lock_owner() {
	[ -d "$LOCK_DIR" ] || return 1
	[ "$(cat "$LOCK_OWNER_FILE" 2>/dev/null || true)" = "$MY_OWNER" ]
}

_touch_lock_heartbeat() {
	_is_lock_owner || return 0
	echo "$MY_OWNER" >"$LOCK_OWNER_FILE" 2>/dev/null || true
	date +%s >"$LOCK_HEARTBEAT_FILE" 2>/dev/null || true
}

_set_current_source() {
	local phase="${1:-waiting}"
	_is_lock_owner || return 0
	printf '%s|%s|%s|%s|%s\n' "$MY_OWNER" "$phase" "$CONTENT_FILE" "$(date +%s)" "${SOURCE_LABEL:-}" >"$CURRENT_SOURCE_FILE" 2>/dev/null || true
}

_clear_current_source_if_owner() {
	local owner
	owner=$(awk -F'|' 'NR==1{print $1}' "$CURRENT_SOURCE_FILE" 2>/dev/null || true)
	[ -n "$owner" ] || return 0
	[ "$owner" = "$MY_OWNER" ] || return 0
	rm -f "$CURRENT_SOURCE_FILE" 2>/dev/null || true
}

_has_pending_comment_queue() {
	ls tmp/.comment_queue/comment_*.txt >/dev/null 2>&1
}

# テキストの世代判定用ハッシュ。md5/md5sum が無い環境ではサイズで代用する
# (再開の可否判定にしか使わないため、衝突耐性より可搬性を優先する)。
_hash_content_file() {
	local f="${1:-}"
	[ -s "$f" ] || {
		printf 'empty\n'
		return 0
	}
	if command -v md5sum >/dev/null 2>&1; then
		md5sum "$f" 2>/dev/null | awk '{print $1}'
	elif command -v md5 >/dev/null 2>&1; then
		md5 -q "$f" 2>/dev/null
	else
		printf 'size%s\n' "$(wc -c <"$f" 2>/dev/null | tr -d ' ')"
	fi
}

# コメントの滞留を検出する。未再生 (.txt) だけでなく、既に取り出されて
# 合成・再生中の (.playing) も「コメント消化中」として扱う。
_comment_backlog_pending() {
	local dir="${COMMENT_QUEUE_DIR:-tmp/.comment_queue}"
	ls "$dir"/comment_*.txt >/dev/null 2>&1 && return 0
	ls "$dir"/comment_*.playing >/dev/null 2>&1 && return 0
	return 1
}

# 背景ラジオ (ニュース等) の事前合成は、コメントが1件でも溜まっていたら
# 合成の途中でも即座に打ち切って VOICEVOX を明け渡す (ユーザー指示 2026-08-26)。
# 順番待ちで粘るとチャンク単位の ping-pong になり、コメント側の合成時間が
# 実測で約2倍になっていた (2026-08-26 17:34-17:42 の audio_worker.log)。
# 打ち切っても合成済みチャンクは捨てず、コメントが捌けたら続きから再開する。
_radio_render_should_abort_for_comment() {
	case "${RADIO_RENDER_COMMENT_ABORT:-1}" in
	0 | false | no | off) return 1 ;;
	esac
	_voicevox_synth_is_background_render || return 1
	_comment_backlog_pending
}

_radio_should_yield_to_comment() {
	# deferred radio is launched from the comment player itself.
	# If it keeps yielding to newly queued comments, the comment player blocks
	# on this process and can never drain that backlog.
	case "${SAY_DISABLE_COMMENT_YIELD:-0}" in
	1 | true | yes)
		return 1
		;;
	esac

	case "${SOURCE_LABEL:-}" in
	radio | radio:*) ;;
	*)
		return 1
		;;
	esac

	_has_pending_comment_queue
}

_should_skip_stale_soren91_ranking() {
	# soren91:ranking_comment で、かつ soren91 モード開始フラグより古い場合はスキップ
	case "${SOURCE_LABEL:-}" in
	soren91:ranking_comment)
		# フラグファイルを確認
		local flag_file="tmp/.soren91_mode_active"
		if [ -f "$flag_file" ]; then
			local flag_ts
			flag_ts=$(cat "$flag_file" 2>/dev/null || echo "0")
			local current_ts
			current_ts=$(date +%s)000
			# フラグのタイムスタンプ（秒をミリ秒に変換）
			local flag_ts_ms=$((flag_ts * 1000))
			# フラグが5分以内に作成された場合、古い ranking_comment をスキップ
			if [ $((current_ts - flag_ts_ms)) -lt 300000 ]; then
				_log "soren91 mode active, skipping old ranking_comment"
				return 0
			fi
		fi
		;;
	esac
	return 1
}

_yield_turn_to_pending_comment() {
	_is_lock_owner || return 1
	_log "pending comment を優先するため ${SOURCE_LABEL:-unknown} が順番を譲る"
	_release_lock
	sleep 1
	return 0
}

# mkdirロック: アトミックな排他制御（macOS互換）
_acquire_lock() {
	while ! mkdir "$LOCK_DIR" 2>/dev/null; do
		# stale lock検出: 所有PIDが死んでおり、heartbeatも古い場合のみ強制解除
		if [ -d "$LOCK_DIR" ]; then
			local lock_owner_raw lock_owner_pid lock_hb now lock_age owner_alive=false
			lock_owner_raw=$(cat "$LOCK_OWNER_FILE" 2>/dev/null || true)
			lock_owner_pid="${lock_owner_raw%%:*}"
			case "$lock_owner_pid" in
			'' | *[!0-9]*) lock_owner_pid="" ;;
			esac
			if [ -n "$lock_owner_pid" ] && kill -0 "$lock_owner_pid" 2>/dev/null; then
				owner_alive=true
			fi
			lock_hb=$(cat "$LOCK_HEARTBEAT_FILE" 2>/dev/null || true)
			case "$lock_hb" in
			'' | *[!0-9]*)
				lock_hb=$(_file_mtime_epoch "$LOCK_DIR")
				;;
			esac
			now=$(date +%s)
			# heartbeat が読めない場合はstale判定しない（誤検出で重複再生を防ぐ）
			case "$lock_hb" in
			'' | *[!0-9]* | 0) lock_age=0 ;;
			*) lock_age=$((now - lock_hb)) ;;
			esac
			if [ "$owner_alive" = false ] && [ "$lock_age" -gt "$LOCK_STALE_SEC" ]; then
				_log "stale lock検出 (owner=${lock_owner_pid:-?}, ${lock_age}秒) → 強制解除"
				rm -f "$LOCK_OWNER_FILE" "$LOCK_HEARTBEAT_FILE" 2>/dev/null
				rmdir "$LOCK_DIR" 2>/dev/null
				continue
			fi
		fi
		sleep 0.5
	done
	echo "$MY_OWNER" >"$LOCK_OWNER_FILE" 2>/dev/null || {
		rmdir "$LOCK_DIR" 2>/dev/null
		return 1
	}
	date +%s >"$LOCK_HEARTBEAT_FILE" 2>/dev/null || true
	LOCK_HELD=1
	return 0
}

_release_lock() {
	[ "$LOCK_HELD" -eq 1 ] || return 0
	if ! _is_lock_owner; then
		_log "ロック解放スキップ: 所有者不一致"
		LOCK_HELD=0
		return 0
	fi
	_clear_current_source_if_owner
	rm -f "$LOCK_OWNER_FILE" "$LOCK_HEARTBEAT_FILE" 2>/dev/null
	rmdir "$LOCK_DIR" 2>/dev/null
	LOCK_HELD=0
}

_is_voicevox_synth_lock_owner() {
	[ -d "$VOICEVOX_SYNTH_LOCK" ] || return 1
	[ "$(cat "$VOICEVOX_SYNTH_OWNER_FILE" 2>/dev/null || true)" = "$MY_OWNER" ]
}

_touch_voicevox_synth_lock_heartbeat() {
	_is_voicevox_synth_lock_owner || return 0
	echo "$MY_OWNER" >"$VOICEVOX_SYNTH_OWNER_FILE" 2>/dev/null || true
	date +%s >"$VOICEVOX_SYNTH_HEARTBEAT_FILE" 2>/dev/null || true
}

_release_voicevox_synth_lock() {
	[ "$VOICEVOX_SYNTH_LOCK_HELD" -eq 1 ] || return 0
	if ! _is_voicevox_synth_lock_owner; then
		VOICEVOX_SYNTH_LOCK_HELD=0
		VOICEVOX_SYNTH_LOCK="$VOICEVOX_SYNTH_LOCK_BASE"
		VOICEVOX_SYNTH_OWNER_FILE="$VOICEVOX_SYNTH_LOCK/owner_pid"
		VOICEVOX_SYNTH_HEARTBEAT_FILE="$VOICEVOX_SYNTH_LOCK/heartbeat"
		VOICEVOX_SYNTH_ACTIVE_URL=""
		VOICEVOX_SYNTH_ACTIVE_LOCK=""
		unset VOICEVOX_ACTIVE_URL 2>/dev/null || true
		return 0
	fi
	rm -f "$VOICEVOX_SYNTH_OWNER_FILE" "$VOICEVOX_SYNTH_HEARTBEAT_FILE" 2>/dev/null
	rmdir "$VOICEVOX_SYNTH_LOCK" 2>/dev/null
	VOICEVOX_SYNTH_LOCK_HELD=0
	VOICEVOX_SYNTH_LOCK="$VOICEVOX_SYNTH_LOCK_BASE"
	VOICEVOX_SYNTH_OWNER_FILE="$VOICEVOX_SYNTH_LOCK/owner_pid"
	VOICEVOX_SYNTH_HEARTBEAT_FILE="$VOICEVOX_SYNTH_LOCK/heartbeat"
	VOICEVOX_SYNTH_ACTIVE_URL=""
	VOICEVOX_SYNTH_ACTIVE_LOCK=""
	unset VOICEVOX_ACTIVE_URL 2>/dev/null || true
}

_voicevox_synth_is_background_render() {
	case "${SOURCE_LABEL:-}" in
	radio_render:*) return 0 ;;
	esac
	return 1
}

_voicevox_gc_priority_waiters() {
	[ -d "$VOICEVOX_SYNTH_PRIORITY_WAIT_DIR" ] || return 0
	local waiter waiter_pid waiter_ts waiter_age now
	now=$(date +%s)
	for waiter in "$VOICEVOX_SYNTH_PRIORITY_WAIT_DIR"/*.wait; do
		[ -f "$waiter" ] || continue
		read -r waiter_pid waiter_ts <"$waiter" || true
		case "$waiter_pid" in '' | *[!0-9]*) waiter_pid="" ;; esac
		case "$waiter_ts" in
		'' | *[!0-9]*) waiter_ts=$(_file_mtime_epoch "$waiter") ;;
		esac
		case "$waiter_ts" in '' | *[!0-9]* | 0) waiter_age=0 ;; *) waiter_age=$((now - waiter_ts)) ;; esac
		if { [ -z "$waiter_pid" ] || ! kill -0 "$waiter_pid" 2>/dev/null; } \
			&& [ "$waiter_age" -gt "$VOICEVOX_SYNTH_PRIORITY_WAIT_STALE_SEC" ]; then
			rm -f "$waiter" 2>/dev/null || true
		fi
	done
	rmdir "$VOICEVOX_SYNTH_PRIORITY_WAIT_DIR" 2>/dev/null || true
}

_voicevox_priority_waiter_exists() {
	_voicevox_gc_priority_waiters
	local waiter
	for waiter in "$VOICEVOX_SYNTH_PRIORITY_WAIT_DIR"/*.wait; do
		[ -f "$waiter" ] && return 0
	done
	return 1
}

_register_voicevox_priority_waiter() {
	_voicevox_synth_is_background_render && return 0
	mkdir -p "$VOICEVOX_SYNTH_PRIORITY_WAIT_DIR" 2>/dev/null || return 1
	VOICEVOX_SYNTH_PRIORITY_WAIT_FILE="$VOICEVOX_SYNTH_PRIORITY_WAIT_DIR/${MY_TOKEN}.wait"
	printf '%s %s\n' "${BASHPID:-$$}" "$(date +%s)" >"$VOICEVOX_SYNTH_PRIORITY_WAIT_FILE" 2>/dev/null || return 1
	VOICEVOX_SYNTH_PRIORITY_WAIT_HELD=1
	return 0
}

_touch_voicevox_priority_waiter() {
	[ "$VOICEVOX_SYNTH_PRIORITY_WAIT_HELD" -eq 1 ] || return 0
	[ -n "$VOICEVOX_SYNTH_PRIORITY_WAIT_FILE" ] || return 0
	printf '%s %s\n' "${BASHPID:-$$}" "$(date +%s)" >"$VOICEVOX_SYNTH_PRIORITY_WAIT_FILE" 2>/dev/null || true
}

_unregister_voicevox_priority_waiter() {
	[ "$VOICEVOX_SYNTH_PRIORITY_WAIT_HELD" -eq 1 ] || return 0
	[ -n "$VOICEVOX_SYNTH_PRIORITY_WAIT_FILE" ] && rm -f "$VOICEVOX_SYNTH_PRIORITY_WAIT_FILE" 2>/dev/null || true
	rmdir "$VOICEVOX_SYNTH_PRIORITY_WAIT_DIR" 2>/dev/null || true
	VOICEVOX_SYNTH_PRIORITY_WAIT_HELD=0
	VOICEVOX_SYNTH_PRIORITY_WAIT_FILE=""
}

# 合成ロック待ち時間をコンテキストで変える:
#   - コメント・改善進捗などの前景音声は長く待つ（必ず合成・再生に到達させる）
#   - ラジオ render は前景音声へ順番を譲るが、ラジオ自身は終了せず待って
#     同じ事前合成世代を継続する（途中チャンクを捨てて先頭からやり直さない）
_voicevox_synth_lock_wait_sec() {
	case "${SOURCE_LABEL:-}" in
	comment | comment:*)
		printf '%s' "${VOICEVOX_SYNTH_LOCK_WAIT_COMMENT_SEC:-180}"
		;;
	radio_render:*)
		printf '%s' "${VOICEVOX_SYNTH_LOCK_WAIT_RADIO_SEC:-15}"
		;;
	*)
		# 改善進捗など comment: 接頭辞を持たない前景音声も、背景ラジオより優先する。
		printf '%s' "${VOICEVOX_SYNTH_LOCK_WAIT_FOREGROUND_SEC:-${VOICEVOX_SYNTH_LOCK_WAIT_COMMENT_SEC:-180}}"
		;;
	esac
}

# チャンク合成タイムアウト: コメントは長め（確実に完読）、ラジオは短め（待たせない）
_voicevox_synth_timeout_sec() {
	case "${SOURCE_LABEL:-}" in
	comment | comment:*)
		printf '%s' "${VOICEVOX_COMMENT_SYNTH_TIMEOUT_SEC:-180}"
		;;
	radio_render:* | radio | radio:*)
		printf '%s' "${VOICEVOX_RADIO_SYNTH_TIMEOUT_SEC:-90}"
		;;
	*)
		printf '%s' "${VOICEVOX_SYNTH_TIMEOUT_SEC:-120}"
		;;
	esac
}

# 長文コメントだけは、全チャンクの合成完了を待たずに再生を開始する。
# render-only やラジオの完成品生成は従来の全量事前合成を維持する。
_is_streaming_comment_source() {
	case "${SOURCE_LABEL:-}" in
	comment | comment:* | improve_progress | system_progress | wildcard_progress | monitor_report | meriken_time | soren91:*)
		return 0
		;;
	esac
	return 1
}

# ストリーミング経路で使う VOICEVOX の実行時設定を準備する。
# 合成ロックはここでは取得しないため、再生ロックを待つ前にVOICEVOXを
# 占有しない。短文・render-onlyの従来経路は既存の設定処理をそのまま使う。
_prepare_voicevox_runtime_params() {
	if [ -f "tmp/voicevox_oneshot_speaker.txt" ]; then
		case "${SOURCE_LABEL:-}" in
		comment | comment:*)
			VOICEVOX_SPEAKER=$(cat "tmp/voicevox_oneshot_speaker.txt" 2>/dev/null)
			VOICEVOX_RANDOM_VOICE_NAME=""
			VOICEVOX_RANDOM_MODE=0
			rm -f "tmp/voicevox_oneshot_speaker.txt"
			_log "ワンショットスピーカー: $VOICEVOX_SPEAKER"
			;;
		esac
	fi

	if [ -f "tmp/voicevox_dousi.txt" ]; then
		case "${SOURCE_LABEL:-}" in
		comment | comment:*)
			rm -f "tmp/voicevox_dousi.txt"
			USE_VOICEVOX=0
			USE_COEIROINK=0
			_log "同志mode: macOS say (VOICEVOXストリーミングをスキップ)"
			;;
		esac
	fi

	if [ "${USE_VOICEVOX:-0}" = "1" ] && [ -f "tmp/voicevox_asmr.txt" ]; then
		case "${SOURCE_LABEL:-}" in
		comment | comment:*)
			local _asmr_result=""
			_asmr_result=$(_pick_asmr_voicevox_speaker 2>/dev/null || echo "")
			VOICEVOX_SPEAKER="${_asmr_result%%|*}"
			VOICEVOX_RANDOM_VOICE_NAME="${_asmr_result#*|}"
			VOICEVOX_RANDOM_MODE=1
			rm -f "tmp/voicevox_asmr.txt"
			_log "ASMR mode: speaker=$VOICEVOX_SPEAKER ($VOICEVOX_RANDOM_VOICE_NAME)"
			;;
		esac
	fi

	[ "${USE_VOICEVOX:-0}" = "1" ] || return 0
	if [ -f "config/voicevox_exclude_ids.txt" ] && grep -q "^${VOICEVOX_SPEAKER}\\b" "config/voicevox_exclude_ids.txt" 2>/dev/null; then
		_log "speaker=$VOICEVOX_SPEAKER は粛清済み → 再選択"
		local _reroll=""
		_reroll=$(_pick_random_voicevox_speaker)
		VOICEVOX_SPEAKER="${_reroll%%|*}"
		VOICEVOX_RANDOM_VOICE_NAME="${_reroll#*|}"
	fi

	PRE_SYNTH_PITCH=""
	PRE_SYNTH_TEMPO=""
	PRE_SYNTH_INTONATION=""
	local vo_pitch="" vo_tempo="" vo_intonation=""
	[ -f "config/voicevox_pitch_map.txt" ] && vo_pitch=$(grep "^${VOICEVOX_SPEAKER}|" "config/voicevox_pitch_map.txt" 2>/dev/null | tail -1 | cut -d'|' -f2)
	[ -f "config/voicevox_tempo_map.txt" ] && vo_tempo=$(grep "^${VOICEVOX_SPEAKER}|" "config/voicevox_tempo_map.txt" 2>/dev/null | tail -1 | cut -d'|' -f2)
	[ -f "config/voicevox_intonation_map.txt" ] && vo_intonation=$(grep "^${VOICEVOX_SPEAKER}|" "config/voicevox_intonation_map.txt" 2>/dev/null | tail -1 | cut -d'|' -f2)
	PRE_SYNTH_PITCH="$vo_pitch"
	PRE_SYNTH_TEMPO="$vo_tempo"
	PRE_SYNTH_INTONATION="$vo_intonation"
	_log "VOICEVOX speaker=$VOICEVOX_SPEAKER${VOICEVOX_RANDOM_VOICE_NAME:+ ($VOICEVOX_RANDOM_VOICE_NAME)}${vo_pitch:+ pitch=$vo_pitch}${vo_tempo:+ tempo=$vo_tempo}${vo_intonation:+ intonation=$vo_intonation}"
}

_acquire_voicevox_synth_lock_legacy() {
	local timeout_sec="${1:-30}" waited=0 max_waits background_render=0
	local priority_waited=0 priority_wait_limit=0 lock_wait_limit="${timeout_sec}"
	VOICEVOX_SYNTH_LOCK_BUSY_REASON=""
	_voicevox_synth_is_background_render && background_render=1
	if [ "$background_render" -eq 1 ]; then
		# 背景ラジオは前景（コメント等）を優先するが、無期限待ちはデッドロックする。
		# 2026-08-24の実測で comment 180s 待ち vs radio 無期限待ちが循環デッドロックした。
		# 有限化し、タイムアウト後は現世代を一時保留して後で再試行する（進捗は捨てない）。
		priority_wait_limit="${VOICEVOX_RADIO_PRIORITY_WAIT_SEC:-90}"
		case "$priority_wait_limit" in
		'' | *[!0-9]*) priority_wait_limit=90 ;;
		esac
		lock_wait_limit="${VOICEVOX_RADIO_LOCK_WAIT_SEC:-60}"
		case "$lock_wait_limit" in
		'' | *[!0-9]*) lock_wait_limit=60 ;;
		esac
	fi
	if [ "$background_render" -eq 0 ]; then
		_register_voicevox_priority_waiter || true
	fi
	if [ "$lock_wait_limit" -gt 0 ]; then
		max_waits=$((lock_wait_limit * 2))
	else
		max_waits=0
	fi
	while true; do
		if [ "$background_render" -eq 1 ] && _voicevox_priority_waiter_exists; then
			# 旧実装はここで rc=75 を返して一時ファイルを全削除していた。
			# コメントが一定間隔で入るとラジオは毎回チャンク0から再開し、
			# ready.wavに到達できない。背景プロセスはaudio worker本体とは
			# 別のため、前景音声が終わるまで待つ方が安全である。
			if [ "$priority_waited" -eq 0 ] || [ $((priority_waited % 60)) -eq 0 ]; then
				_log "優先音声の合成完了待ち (background radio)"
			fi
			priority_waited=$((priority_waited + 1))
			if [ "$priority_wait_limit" -gt 0 ] && [ "$priority_waited" -ge $((priority_wait_limit * 2)) ]; then
				VOICEVOX_SYNTH_LOCK_BUSY_REASON="priority_waiter"
				return 1
			fi
			_touch_voicevox_priority_waiter
			sleep 0.5
			continue
		fi
		priority_waited=0
		if mkdir "$VOICEVOX_SYNTH_LOCK" 2>/dev/null; then
			break
		fi
		if [ -d "$VOICEVOX_SYNTH_LOCK" ]; then
			local lock_owner_raw lock_owner_pid lock_hb now lock_age owner_alive=false
			lock_owner_raw=$(cat "$VOICEVOX_SYNTH_OWNER_FILE" 2>/dev/null || true)
			lock_owner_pid="${lock_owner_raw%%:*}"
			case "$lock_owner_pid" in
			'' | *[!0-9]*) lock_owner_pid="" ;;
			esac
			if [ -n "$lock_owner_pid" ] && kill -0 "$lock_owner_pid" 2>/dev/null; then
				owner_alive=true
			fi
			lock_hb=$(cat "$VOICEVOX_SYNTH_HEARTBEAT_FILE" 2>/dev/null || true)
			case "$lock_hb" in
			'' | *[!0-9]*)
				lock_hb=$(_file_mtime_epoch "$VOICEVOX_SYNTH_LOCK")
				;;
			esac
			now=$(date +%s)
			case "$lock_hb" in
			'' | *[!0-9]* | 0) lock_age=0 ;;
			*) lock_age=$((now - lock_hb)) ;;
			esac
			if [ "$owner_alive" = false ] && [ "$lock_age" -gt "${VOICEVOX_SYNTH_LOCK_DEAD_GRACE_SEC:-10}" ]; then
				_log "VOICEVOX合成 stale lock検出 (owner=${lock_owner_pid:-?}, ${lock_age}秒, dead) → 強制解除"
				rm -f "$VOICEVOX_SYNTH_OWNER_FILE" "$VOICEVOX_SYNTH_HEARTBEAT_FILE" 2>/dev/null
				rmdir "$VOICEVOX_SYNTH_LOCK" 2>/dev/null
				continue
			fi
			if [ "$owner_alive" = true ] && [ "$lock_age" -gt "${VOICEVOX_SYNTH_LOCK_HUNG_SEC:-60}" ]; then
				_log "VOICEVOX合成 hung lock検出 (owner=${lock_owner_pid:-?}, ${lock_age}秒, heartbeat停止) → 強制解除"
				rm -f "$VOICEVOX_SYNTH_OWNER_FILE" "$VOICEVOX_SYNTH_HEARTBEAT_FILE" 2>/dev/null
				rmdir "$VOICEVOX_SYNTH_LOCK" 2>/dev/null
				continue
			fi
		fi
		_touch_voicevox_priority_waiter
		sleep 0.5
		waited=$((waited + 1))
		if [ "$max_waits" -gt 0 ] && [ "$waited" -ge "$max_waits" ]; then
			VOICEVOX_SYNTH_LOCK_BUSY_REASON="timeout"
			_unregister_voicevox_priority_waiter
			return 1
		fi
	done
	echo "$MY_OWNER" >"$VOICEVOX_SYNTH_OWNER_FILE" 2>/dev/null || {
		rmdir "$VOICEVOX_SYNTH_LOCK" 2>/dev/null
		VOICEVOX_SYNTH_LOCK_BUSY_REASON="owner_write_failed"
		_unregister_voicevox_priority_waiter
		return 1
	}
	date +%s >"$VOICEVOX_SYNTH_HEARTBEAT_FILE" 2>/dev/null || true
	VOICEVOX_SYNTH_LOCK_HELD=1
	_unregister_voicevox_priority_waiter
	return 0
}

_acquire_voicevox_synth_lock() {
	# 分散モード: VOICEVOX_URLS チェーンの空きエンドポイントへ分散。windowsでキューならmac→localへ回す。
	if [ "${VOICEVOX_SYNTH_DISTRIBUTED:-1}" = "1" ]; then
		local _chain_urls=()
		while IFS= read -r _u; do [ -n "$_u" ] && _chain_urls+=("$_u"); done < <(_voicevox_urls_chain)
		if [ ${#_chain_urls[@]} -gt 1 ]; then
			local timeout_sec="${1:-30}" waited=0 max_waits background_render=0
			local priority_waited=0 priority_wait_limit=0 lock_wait_limit="${timeout_sec}"
			VOICEVOX_SYNTH_LOCK_BUSY_REASON=""
			_voicevox_synth_is_background_render && background_render=1
			if [ "$background_render" -eq 1 ]; then
				priority_wait_limit="${VOICEVOX_RADIO_PRIORITY_WAIT_SEC:-90}"
				case "$priority_wait_limit" in ''|*[!0-9]*) priority_wait_limit=90;; esac
				lock_wait_limit="${VOICEVOX_RADIO_LOCK_WAIT_SEC:-60}"
				case "$lock_wait_limit" in ''|*[!0-9]*) lock_wait_limit=60;; esac
			fi
			if [ "$background_render" -eq 0 ]; then
				_register_voicevox_priority_waiter || true
			fi
			if [ "$lock_wait_limit" -gt 0 ]; then
				max_waits=$((lock_wait_limit * 2))
			else
				max_waits=0
			fi
			while true; do
				if [ "$background_render" -eq 1 ] && _voicevox_priority_waiter_exists; then
					if [ "$priority_waited" -eq 0 ] || [ $((priority_waited % 60)) -eq 0 ]; then
						_log "優先音声の合成完了待ち (background radio)"
					fi
					priority_waited=$((priority_waited + 1))
					if [ "$priority_wait_limit" -gt 0 ] && [ "$priority_waited" -ge $((priority_wait_limit * 2)) ]; then
						VOICEVOX_SYNTH_LOCK_BUSY_REASON="priority_waiter"
						return 1
					fi
					_touch_voicevox_priority_waiter
					sleep 0.5
					continue
				fi
				priority_waited=0
				# ready → backoff の順で空きロックを探す（backoffは失敗直後の端点を避ける）
				local _found=0 _selected_url="" _selected_hash="" _selected_lock=""
				for _status in ready backoff; do
					for _url in "${_chain_urls[@]}"; do
						if [ "$(_voicevox_endpoint_status "$_url")" != "$_status" ]; then
							continue
						fi
						local _hash _lock_dir _owner_file _hb_file
						_hash=$(_voicevox_url_hash "$_url")
						_lock_dir="${VOICEVOX_SYNTH_LOCK_BASE}.${_hash}"
						_owner_file="${_lock_dir}/owner_pid"
						_hb_file="${_lock_dir}/heartbeat"
						if mkdir "$_lock_dir" 2>/dev/null; then
							_selected_url="$_url"
							_selected_hash="$_hash"
							_selected_lock="$_lock_dir"
							_found=1
							break 2
						fi
						if [ -d "$_lock_dir" ]; then
							local lock_owner_raw lock_owner_pid lock_hb now lock_age owner_alive=false
							lock_owner_raw=$(cat "$_owner_file" 2>/dev/null || true)
							lock_owner_pid="${lock_owner_raw%%:*}"
							case "$lock_owner_pid" in ''|*[!0-9]*) lock_owner_pid="";; esac
							if [ -n "$lock_owner_pid" ] && kill -0 "$lock_owner_pid" 2>/dev/null; then
								owner_alive=true
							fi
							lock_hb=$(cat "$_hb_file" 2>/dev/null || true)
							case "$lock_hb" in ''|*[!0-9]*) lock_hb=$(_file_mtime_epoch "$_lock_dir");; esac
							now=$(date +%s)
							case "$lock_hb" in ''|*[!0-9]*|0) lock_age=0;; *) lock_age=$((now - lock_hb));; esac
							if [ "$owner_alive" = false ] && [ "$lock_age" -gt "${VOICEVOX_SYNTH_LOCK_DEAD_GRACE_SEC:-10}" ]; then
								_log "VOICEVOX分散 stale lock検出 (url=$_url, owner=${lock_owner_pid:-?}, ${lock_age}秒, dead) → 強制解除"
								rm -f "$_owner_file" "$_hb_file" 2>/dev/null
								rmdir "$_lock_dir" 2>/dev/null
								if mkdir "$_lock_dir" 2>/dev/null; then
									_selected_url="$_url"
									_selected_hash="$_hash"
									_selected_lock="$_lock_dir"
									_found=1
									break 2
								fi
							fi
							if [ "$owner_alive" = true ] && [ "$lock_age" -gt "${VOICEVOX_SYNTH_LOCK_HUNG_SEC:-60}" ]; then
								_log "VOICEVOX分散 hung lock検出 (url=$_url, owner=${lock_owner_pid:-?}, ${lock_age}秒) → 強制解除"
								rm -f "$_owner_file" "$_hb_file" 2>/dev/null
								rmdir "$_lock_dir" 2>/dev/null
								if mkdir "$_lock_dir" 2>/dev/null; then
									_selected_url="$_url"
									_selected_hash="$_hash"
									_selected_lock="$_lock_dir"
									_found=1
									break 2
								fi
							fi
						fi
					done
				done
				# disabled は最後の手段: 他にenabledが無い時だけ試す
				if [ "$_found" -eq 0 ]; then
					for _url in "${_chain_urls[@]}"; do
						if [ "$(_voicevox_endpoint_status "$_url")" != "disabled" ]; then
							continue
						fi
						local _hash _lock_dir
						_hash=$(_voicevox_url_hash "$_url")
						_lock_dir="${VOICEVOX_SYNTH_LOCK_BASE}.${_hash}"
						if mkdir "$_lock_dir" 2>/dev/null; then
							_selected_url="$_url"
							_selected_hash="$_hash"
							_selected_lock="$_lock_dir"
							_found=1
							break
						fi
					done
				fi
				if [ "$_found" -eq 1 ]; then
					echo "$MY_OWNER" >"${_selected_lock}/owner_pid" 2>/dev/null || {
						rmdir "${_selected_lock}" 2>/dev/null
						VOICEVOX_SYNTH_LOCK_BUSY_REASON="owner_write_failed"
						_unregister_voicevox_priority_waiter
						return 1
					}
					date +%s >"${_selected_lock}/heartbeat" 2>/dev/null || true
					VOICEVOX_SYNTH_LOCK="$_selected_lock"
					VOICEVOX_SYNTH_OWNER_FILE="${_selected_lock}/owner_pid"
					VOICEVOX_SYNTH_HEARTBEAT_FILE="${_selected_lock}/heartbeat"
					VOICEVOX_SYNTH_LOCK_HELD=1
					VOICEVOX_SYNTH_ACTIVE_URL="$_selected_url"
					VOICEVOX_SYNTH_ACTIVE_LOCK="$_selected_lock"
					export VOICEVOX_ACTIVE_URL="$_selected_url"
					export VOICEVOX_URLS="$(_voicevox_reorder_urls "$_selected_url")"
					_log "VOICEVOX分散ロック取得: $_selected_url (hash $_selected_hash)"
					_unregister_voicevox_priority_waiter
					return 0
				fi
				_touch_voicevox_priority_waiter
				sleep 0.5
				waited=$((waited + 1))
				if [ "$max_waits" -gt 0 ] && [ "$waited" -ge "$max_waits" ]; then
					VOICEVOX_SYNTH_LOCK_BUSY_REASON="timeout"
					_unregister_voicevox_priority_waiter
					return 1
				fi
			done
		fi
	fi
	# フォールバック: 単一URLまたは分散無効時は従来のグローバルロック
	_acquire_voicevox_synth_lock_legacy "$@"
}

# クリーンアップ: 終了時にロック解放 + 自分のコンテンツ削除
_cleanup() {
	_speaking_leave 2>/dev/null || true
	if [ -n "${VOICEVOX_STREAM_HB_PID:-}" ]; then
		kill "$VOICEVOX_STREAM_HB_PID" 2>/dev/null || true
		wait "$VOICEVOX_STREAM_HB_PID" 2>/dev/null || true
		VOICEVOX_STREAM_HB_PID=""
	fi
	docich_cc_cleanup
	_unregister_voicevox_priority_waiter
	_clear_current_source_if_owner
	_release_voicevox_synth_lock
	_release_lock
	rm -f "$MY_CONTENT"
	rm -f "${MY_CONTENT%.txt}_pre.wav" 2>/dev/null
	rm -f "${MY_CONTENT%.txt}_chunks.txt" 2>/dev/null
	rm -f "${MY_CONTENT%.txt}_wav_playlist.txt" 2>/dev/null
	rm -f "${MY_CONTENT%.txt}_wav_playlist.txt.concat" 2>/dev/null
	rm -f "${MY_CONTENT%.txt}_render_chunks.txt" 2>/dev/null
	rm -f "${MY_CONTENT%.txt}_render_playlist.txt" 2>/dev/null
	if [ -n "${RENDER_OUTPUT:-}" ]; then
		rm -rf "${RENDER_OUTPUT}.bundle.tmp.${MY_TOKEN}" 2>/dev/null
	fi
	rm -rf "$QUEUE_DIR/stream_${MY_TOKEN}" 2>/dev/null
}
trap '_cleanup' EXIT

if [ "${SOURCE_LABEL:-}" = "soren91:strategy" ] && _text_is_strategy_meta_failure "$(cat "$MY_CONTENT" 2>/dev/null || true)"; then
	_log "soren91 strategy meta failure detected; skip TTS"
	_append_played_log "skipped_meta_failure"
	exit 0
fi

_log "queued (token=${MY_TOKEN})"

_sleep_with_heartbeat() {
	local sec="${1:-1}" waited=0
	while [ "$waited" -lt "$sec" ]; do
		_touch_lock_heartbeat
		sleep 1
		waited=$((waited + 1))
	done
}

_resolve_audio_device_index() {
	local name="$1"
	# 数値ならそのまま返す
	case "$name" in
	*[!0-9]*) ;; # 非数値→名前解決へ
	'') return 1 ;;
	*)
		echo "$name"
		return 0
		;;
	esac
	if [ "$IS_LINUX" = "1" ]; then
		# PulseAudio の sink 名を解決。存在すればそのまま sink 名を返す
		# （paplay --device は sink 名を直接受け付ける）。
		if command -v pactl >/dev/null 2>&1; then
			# .monitor suffix は先に取り除いてから sink 一覧と照合する
			# （soren_null.monitor -> soren_null）。
			local sink_candidate="${name%.monitor}"
			if pactl list short sinks 2>/dev/null | awk '{print $2}' | grep -Fxq "$sink_candidate"; then
				echo "$sink_candidate"
				return 0
			fi
			if pactl list short sinks 2>/dev/null | awk '{print $2}' | grep -Fxq "$name"; then
				echo "$name"
				return 0
			fi
		fi
		echo "[say_enqueue] audio device not found: $name (pactl)" >&2
		return 1
	fi
	local devices line idx alt_name
	devices=$(ffmpeg -y -f lavfi -i sine=frequency=1:duration=0.001 -f audiotoolbox -list_devices true "" 2>&1)

	# まずは完全一致
	line=$(printf '%s\n' "$devices" | grep -F "$name" | head -1)
	if [ -z "$line" ]; then
		# CoreAudio 側で表記揺れした場合に備えて緩めに解決
		alt_name=$(printf '%s' "$name" | tr '[:upper:]' '[:lower:]')
		line=$(printf '%s\n' "$devices" | awk -v needle="$alt_name" '
            BEGIN { IGNORECASE = 1 }
            {
                hay = tolower($0)
                if (index(hay, needle) > 0) {
                    print
                    exit
                }
            }')
	fi
	if [ -z "$line" ] && printf '%s' "$name" | grep -qi 'blackhole'; then
		line=$(printf '%s\n' "$devices" | awk '
            BEGIN { IGNORECASE = 1 }
            /blackhole/ {
                print
                exit
            }')
	fi
	if [ -n "$line" ]; then
		idx=$(printf '%s\n' "$line" | sed -n 's/.*\[\([0-9][0-9]*\)\].*/\1/p')
		if [ -n "$idx" ]; then
			echo "$idx"
			return 0
		fi
	fi
	echo "[say_enqueue] audio device not found: $name" >&2
	return 1
}

_estimate_audio_duration_sec() {
	local file="$1" d
	d=""
	if command -v ffprobe >/dev/null 2>&1; then
		d=$(ffprobe -v error -show_entries format=duration -of default=nk=1:nw=1 "$file" 2>/dev/null | head -1)
		case "$d" in
		'' | N/A | *[!0-9.]*) d="" ;;
		esac
	fi
	if [ -z "$d" ] && command -v afinfo >/dev/null 2>&1; then
		d=$(afinfo "$file" 2>/dev/null | sed -n 's/.*estimated duration: \([0-9.][0-9.]*\) sec.*/\1/p' | head -1)
		case "$d" in
		'' | *[!0-9.]*) d="" ;;
		esac
	fi
	if [ -z "$d" ]; then
		echo 0
		return 0
	fi
	awk -v v="$d" 'BEGIN { if (v < 0) v = 0; printf "%d\n", (v + 0.5) }'
}

_estimate_text_duration_sec() {
	local file="$1" rate="${2:-150}" chars chars_per_sec
	chars=$(wc -m <"$file" 2>/dev/null | tr -d '[:space:]')
	case "$chars" in
	'' | *[!0-9]*) chars=0 ;;
	esac
	if [ "$chars" -le 0 ]; then
		echo 0
		return 0
	fi
	chars_per_sec=$(awk -v r="$rate" '
BEGIN {
    cps = 5.0
    if (r > 0) cps = cps * (r / 150.0)
    if (cps < 2.5) cps = 2.5
    printf "%.3f\n", cps
}')
	awk -v c="$chars" -v cps="$chars_per_sec" '
BEGIN {
    sec = int((c / cps) + 0.999)
    if (sec < 8) sec = 8
	print sec
}'
}

_launch_bg_exec() {
	local cleanup_file="$1"
	shift
	nohup bash -c '
		trap "" INT TERM
		cleanup_file="$1"
		shift
		if [ -n "$cleanup_file" ]; then
			trap '"'"'[ -n "$cleanup_file" ] && rm -f "$cleanup_file"'"'"' EXIT
		fi
		exec "$@"
	' _ "$cleanup_file" "$@" >/dev/null 2>&1 &
}

# Linux 専用: paplay を優先し、無ければ ffplay へフォールバックする。
# device 引数は _resolve_audio_device_index が返した sink 名。
# paplay が使える場合は明示された sink を維持し、両方無ければ失敗を返す。
_linux_play_bg() {
	local audio_file="$1" device="${2:-${SAY_AUDIO_DEVICE:-default}}" cleanup_file="${3:-}"
	local pulse_latency="${SAY_PULSE_LATENCY_MS:-80}"
	# 声量は SAY_PLAY_VOLUME_LINEAR (0..65536, 既定 65536 = 100%) で制御。
	# 1.3倍 = 85197 を .env で設定すると声を 30% 大きくできる。
	# 環境変数に無い場合は .env から直接読む（audio_worker/supervisor の環境が古くても確実に適用）。
	local say_volume="${SAY_PLAY_VOLUME_LINEAR:-}"
	if [ -z "$say_volume" ] && [ -f .env ]; then
		say_volume=$(grep -E "^SAY_PLAY_VOLUME_LINEAR=" .env 2>/dev/null | tail -1 | cut -d= -f2- | tr -d "\r")
	fi
	[ -z "$say_volume" ] && say_volume=65536
	case "$say_volume" in
	'' | *[!0-9]*)  say_volume=65536 ;;
	esac
	[ "$say_volume" -lt 0 ] && say_volume=0
	# 声量: SAY_PLAY_VOLUME_LINEAR (0..65536, 100% = 65536) を paplay --volume へ渡す。
	# 65536 超は PulseAudio がブーストとして扱うためクランプしない。
	local play_volume="$say_volume"
	if command -v paplay >/dev/null 2>&1; then
		_launch_bg_exec "$cleanup_file" env PULSE_LATENCY_MSEC="$pulse_latency" paplay --device="$device" --volume="$play_volume" "$audio_file"
		return 0
	fi
	if command -v ffplay >/dev/null 2>&1; then
		# ffplay は PulseAudio 既定出力へ流す（既定 sink = soren_null 運用）
		_launch_bg_exec "$cleanup_file" env PULSE_LATENCY_MSEC="$pulse_latency" ffplay -nodisp -autoexit -loglevel error -fflags nobuffer "$audio_file"
		return 0
	fi
	_log "[say_enqueue] Linux 再生プレイヤーがありません (paplay/ffplay 未導入)"
	return 1
}

_launch_afplay_bg() {
	local audio_file="$1" cleanup_file="${2:-}"
	if [ "$IS_LINUX" = "1" ]; then
		_linux_play_bg "$audio_file" "${SAY_AUDIO_DEVICE:-default}" "$cleanup_file"
		return $?
	fi
	# afplay has no output-device selector; -d is debug mode, not a device flag.
	# Device-targeted WAV playback uses ffmpeg/audiotoolbox. This helper is only
	# for default-output fallback.
	_launch_bg_exec "$cleanup_file" afplay "$audio_file"
}

_launch_ffmpeg_bg() {
	local audio_file="$1" device_index="$2" cleanup_file="${3:-}"
	if [ "$IS_LINUX" = "1" ]; then
		# device_index は _resolve_audio_device_index が返した sink 名
		_linux_play_bg "$audio_file" "${device_index:-${SAY_AUDIO_DEVICE:-default}}" "$cleanup_file"
		return $?
	fi
	_launch_bg_exec "$cleanup_file" ffmpeg -y -loglevel error -i "$audio_file" -f audiotoolbox -audio_device_index "$device_index" ""
}

_launch_chrome_wav_bg() {
	local audio_file="$1" cleanup_file="${2:-}"
	local label="${SOREN_CHROME_AUDIO_OUTPUT_LABEL:-${SAY_AUDIO_DEVICE:-}}"
	if [ "$IS_LINUX" = "1" ]; then
		# Linux では Chrome CDP 経由の sink 指定は不安定なため、PulseAudio 既定出力へ
		# paplay/ffplay で直接再生する（null-sink が既定出力なので配信へ乗る）。
		_linux_play_bg "$audio_file" "${SAY_AUDIO_DEVICE:-default}" "$cleanup_file"
		return $?
	fi
	CHROME_AUDIO_USED=1
	_launch_bg_exec "$cleanup_file" node ./chrome_audio_player.mjs "$audio_file" "$label"
}

_stop_chrome_audio_players() {
	if [ "$IS_LINUX" = "1" ]; then
		return 0
	fi
	local label="${SOREN_CHROME_AUDIO_OUTPUT_LABEL:-${SAY_AUDIO_DEVICE:-}}"
	node ./chrome_audio_player.mjs --stop "$label" >/dev/null 2>&1 || true
}

_launch_say_bg() {
	local rate="$1" content_file="$2"
	if [ "$IS_LINUX" = "1" ]; then
		# Linux に say は存在しない。device 解決失敗時の最終フォールバックなので、
		# ここでは何も再生せず failure を返す（呼び出し元がリトライ/スキップ処理）。
		_log "say は macOS 専用のため Linux ではスキップ (content=${content_file})"
		return 1
	fi
	if [ -n "${SAY_AUDIO_DEVICE:-}" ]; then
		_launch_bg_exec "" say -a "$SAY_AUDIO_DEVICE" -r "$rate" -f "$content_file"
	else
		_launch_bg_exec "" say -r "$rate" -f "$content_file"
	fi
}

_kill_player_pid() {
	local pid="$1"
	[ -n "$pid" ] || return 0
	kill "$pid" 2>/dev/null || true
	sleep 1
	kill -9 "$pid" 2>/dev/null || true
}

PLAYER_WAIT_RC=0
PLAYER_WAIT_ELAPSED=0
PLAYER_WAIT_TIMED_OUT=0
_wait_for_player_pid() {
	local player_pid="$1" expected_sec="${2:-0}" touch_synth_lock="${3:-0}"
	local start_ts now_ts max_wait_sec=0
	PLAYER_WAIT_RC=0
	PLAYER_WAIT_ELAPSED=0
	PLAYER_WAIT_TIMED_OUT=0
	start_ts=$(date +%s)
	if [ "${expected_sec:-0}" -gt 0 ]; then
		max_wait_sec=$((expected_sec + SAY_TRUNCATE_GRACE_SEC + SAY_HANG_EXTRA_SEC))
	fi
	while kill -0 "$player_pid" 2>/dev/null; do
		_touch_lock_heartbeat
		[ "$touch_synth_lock" -eq 1 ] && _touch_voicevox_synth_lock_heartbeat
		sleep 1
		if [ "$max_wait_sec" -gt 0 ]; then
			now_ts=$(date +%s)
			PLAYER_WAIT_ELAPSED=$((now_ts - start_ts))
			if [ "$PLAYER_WAIT_ELAPSED" -gt "$max_wait_sec" ]; then
				PLAYER_WAIT_TIMED_OUT=1
				_log "say再生ハング疑い (elapsed=${PLAYER_WAIT_ELAPSED}s, expected=${expected_sec}s, max=${max_wait_sec}s) → 強制終了"
				_kill_player_pid "$player_pid"
				break
			fi
		fi
	done
	wait "$player_pid" 2>/dev/null
	PLAYER_WAIT_RC=$?
	if [ "$PLAYER_WAIT_TIMED_OUT" -eq 1 ]; then
		PLAYER_WAIT_RC=99
	fi
	now_ts=$(date +%s)
	PLAYER_WAIT_ELAPSED=$((now_ts - start_ts))
	[ "$PLAYER_WAIT_RC" -eq 0 ]
}

_is_truncated_playback() {
	local elapsed="${1:-0}" expected="${2:-0}"
	awk -v e="$elapsed" -v x="$expected" -v min="$SAY_TRUNCATE_MIN_EXPECTED_SEC" -v ratio="$SAY_TRUNCATE_RATIO" -v grace="$SAY_TRUNCATE_GRACE_SEC" '
BEGIN {
    if (x < min) exit 1
    if ((e + grace) < (x * ratio)) exit 0
    exit 1
}'
}

_partial_playback_already_heard() {
	local elapsed="${1:-0}"
	[ "${SAY_PARTIAL_PLAYBACK_NO_RETRY_SEC:-0}" -gt 0 ] || return 1
	[ "$elapsed" -ge "$SAY_PARTIAL_PLAYBACK_NO_RETRY_SEC" ]
}

# --- VOICEVOX ストリーミングTTS用ヘルパー ---

# テキストを句点・読点で ~N文字チャンクに分割
_split_tts_text() {
	local text="$1" max_chars="${2:-100}" hard_split="${3:-0}"
	python3 -c "
import sys
text = sys.argv[1]
max_len = int(sys.argv[2])
hard_split = sys.argv[3] == '1'
chunks = []

def append_piece(piece):
    piece = piece.strip()
    while len(piece) > max_len:
        chunks.append(piece[:max_len])
        piece = piece[max_len:]
    if piece:
        if chunks and len(chunks[-1]) + len(piece) <= max_len:
            chunks[-1] += piece
        else:
            chunks.append(piece)

def append_sentence(sentence):
    if not hard_split:
        if len(sentence) <= max_len:
            if chunks and len(chunks[-1]) + len(sentence) <= max_len:
                chunks[-1] += sentence
            else:
                chunks.append(sentence)
            return
        parts = sentence.split('\u3001')
        buf = ''
        for part in parts:
            candidate = buf + ('\u3001' if buf else '') + part
            if len(candidate) > max_len and buf:
                chunks.append(buf)
                buf = part
            else:
                buf = candidate
        if buf:
            chunks.append(buf)
        return
    if len(sentence) <= max_len:
        append_piece(sentence)
        return
    parts = sentence.split('\u3001')
    buf = ''
    for part in parts:
        candidate = buf + ('\u3001' if buf else '') + part
        if buf and len(candidate) > max_len:
            append_piece(buf)
            buf = part
        else:
            buf = candidate
    if buf:
        append_piece(buf)

for line in text.split('\n'):
    for sent in line.split('\u3002'):
        sent = sent.strip()
        if not sent:
            continue
        sent += '\u3002'
        append_sentence(sent)
for c in chunks:
    print(c)
" "$text" "$max_chars" "$hard_split"
}

# 単一チャンクをVOICEVOXで合成（voicevox_tts.shの再分割を抑止）
# timeout で外側からもkill保証（VOICEVOX起動濟みだがcurlが返らない場合に対応）
_synthesize_chunk() {
	local text="$1" output="$2"
	local _chunk_timeout
	_chunk_timeout=$(_voicevox_synth_timeout_sec)
	# 合成は文字数に比例して数十秒かかる（高負荷時）。30秒では長いチャンクが
	# タイムアウトで失敗するため、コンテキスト別 timeout を使う。
	# 注意: 変数代入を bash -c の文字列に混ぜるとコメント行で代入が壊れ、
	# VOICEVOX_SPEAKER が voicevox_tts.sh に渡らずデフォルト(ずんだもん)になる。
	# 必ず env/コマンド前置き形式で渡すこと。
	if [ -n "$TIMEOUT_CMD" ]; then
		$TIMEOUT_CMD -k "$VOICEVOX_SYNTH_KILL_AFTER_SEC" "$_chunk_timeout" \
			env \
				VOICEVOX_SPEAKER="$VOICEVOX_SPEAKER" \
				VOICEVOX_PITCH="${PRE_SYNTH_PITCH:-}" \
				VOICEVOX_TEMPO="${PRE_SYNTH_TEMPO:-}" \
				VOICEVOX_INTONATION="${PRE_SYNTH_INTONATION:-}" \
				VOICEVOX_TIMEOUT="$_chunk_timeout" \
				VOICEVOX_MAX_CHARS=99999 \
				./voicevox_tts.sh -o "$output" "$text" 2>/dev/null && [ -s "$output" ]
	else
		VOICEVOX_SPEAKER="$VOICEVOX_SPEAKER" \
			VOICEVOX_PITCH="${PRE_SYNTH_PITCH:-}" \
			VOICEVOX_TEMPO="${PRE_SYNTH_TEMPO:-}" \
			VOICEVOX_INTONATION="${PRE_SYNTH_INTONATION:-}" \
			VOICEVOX_TIMEOUT="$_chunk_timeout" \
			VOICEVOX_MAX_CHARS=99999 \
			./voicevox_tts.sh -o "$output" "$text" 2>/dev/null && [ -s "$output" ]
	fi
}

# 子孫プロセスまで含めて終了させる。voicevox_tts.sh は
# timeout -> env -> bash -> docich と多段になるため、直下の PID を
# kill しても実際の合成リクエストが残る。
_kill_process_tree() {
	local pid="${1:-}" child
	case "$pid" in '' | *[!0-9]*) return 0 ;; esac
	for child in $(pgrep -P "$pid" 2>/dev/null); do
		_kill_process_tree "$child"
	done
	kill -TERM "$pid" 2>/dev/null || true
}

# 背景ラジオの事前合成をコメント滞留で即座に打ち切れるようにしたラッパ。
# rc: 0=成功 / 1=失敗 / 9=コメント優先で中断
_synthesize_chunk_yielding() {
	local text="$1" output="$2"
	case "${RADIO_RENDER_COMMENT_ABORT:-1}" in
	0 | false | no | off)
		_synthesize_chunk "$text" "$output"
		return $?
		;;
	esac
	if ! _voicevox_synth_is_background_render; then
		_synthesize_chunk "$text" "$output"
		return $?
	fi
	local poll="${RADIO_RENDER_COMMENT_ABORT_POLL_SEC:-1}"
	case "$poll" in '' | *[!0-9]*) poll=1 ;; esac
	[ "$poll" -gt 0 ] || poll=1
	local synth_pid="" rc=0
	_synthesize_chunk "$text" "$output" &
	synth_pid=$!
	while kill -0 "$synth_pid" 2>/dev/null; do
		if _comment_backlog_pending; then
			_kill_process_tree "$synth_pid"
			wait "$synth_pid" 2>/dev/null
			rm -f "$output" 2>/dev/null || true
			return 9
		fi
		_touch_voicevox_synth_lock_heartbeat
		sleep "$poll"
	done
	wait "$synth_pid"
	rc=$?
	[ "$rc" -eq 0 ] && [ -s "$output" ] && return 0
	rm -f "$output" 2>/dev/null || true
	return 1
}

_launch_stream_wav() {
	local wav_file="$1"
	if [ -n "${SAY_AUDIO_DEVICE:-}" ]; then
		local device_index
		device_index=$(_resolve_audio_device_index "$SAY_AUDIO_DEVICE") || {
			_log "audio device解決失敗 (${SAY_AUDIO_DEVICE}) → Chrome/BlackHole再生にフォールバック"
			_launch_chrome_wav_bg "$wav_file"
			return $?
		}
		_launch_ffmpeg_bg "$wav_file" "$device_index"
		return $?
	else
		_launch_afplay_bg "$wav_file"
		return $?
	fi
}

# 事前合成済みチャンク再生: 呼び出し時点で全WAVが生成済みであること
# 呼び出し時点で再生ロック(LOCK_DIR)を保持済みであること
_play_prerendered_voicevox_chunks() {
	local playlist_file="$1"
	local playlist_dir="" playlist_entry="" wavs=()
	playlist_dir=$(cd "$(dirname "$playlist_file")" 2>/dev/null && pwd) || return 1
	while IFS= read -r _pw_line; do
		[ -n "$_pw_line" ] || continue
		case "$_pw_line" in
		/*) playlist_entry="$_pw_line" ;;
		*) playlist_entry="$playlist_dir/$_pw_line" ;;
		esac
		wavs+=("$playlist_entry")
	done <"$playlist_file"

	local total=${#wavs[@]}
	[ "$total" -gt 0 ] || return 1
	local cc_available=0 cc_prepared=0 cc_clear_after_chunk=0
	if docich_cc_prepare 0 0; then
		cc_available=1
		cc_prepared=1
	fi
	_set_current_source "playing"
	_log "事前合成済みチャンク再生開始 (${total}チャンク)"

	# 再生開始タイミングでチャットに話者名を投稿
	local vo_voice_name="${VOICEVOX_RANDOM_VOICE_NAME:-}"
	if [ -n "$vo_voice_name" ] && [ "${VOICEVOX_RANDOM_MODE:-0}" = "1" ]; then
		local _chat_msg="VOICEVOX: [$VOICEVOX_SPEAKER] $vo_voice_name"
		[ -n "${PRE_SYNTH_PITCH:-}" ] && _chat_msg="$_chat_msg pitch=$PRE_SYNTH_PITCH"
		[ -n "${PRE_SYNTH_TEMPO:-}" ] && _chat_msg="$_chat_msg tempo=$PRE_SYNTH_TEMPO"
		case "$vo_voice_name" in *もち子*) _chat_msg="$_chat_msg [(cv 明日葉よもぎ)]" ;; esac
		enqueue_chat_message "$_chat_msg" "say_enqueue"
	fi

	local i=0 chunk_wav="" play_pid="" current_expected_sec=0 play_failed=0
	for ((i = 0; i < total; i++)); do
		chunk_wav="${wavs[$i]}"
		if [ ! -s "$chunk_wav" ]; then
			_log "事前合成WAVなし: $chunk_wav"
			play_failed=1
			break
		fi
		if [ "$cc_prepared" -eq 1 ]; then
			if ! docich_cc_commit "$i"; then
				cc_available=0
				cc_prepared=0
				docich_cc_clear || true
			fi
		fi
		CHROME_AUDIO_USED=0
		if ! _launch_stream_wav "$chunk_wav"; then
			_log "再生プレイヤー起動失敗: $chunk_wav"
			play_failed=1
			break
		fi
		play_pid=$!
		current_expected_sec=$(_estimate_audio_duration_sec "$chunk_wav")
		echo "$play_pid" >"$PID_FILE"
		LAST_SAY_PID="$play_pid"
		cc_clear_after_chunk=0
		if [ "$cc_available" -eq 1 ] && [ "$i" -lt $((total - 1)) ]; then
			if docich_cc_prepare "$((i + 1))" "$((i + 1))"; then
				cc_prepared=1
			else
				cc_available=0
				cc_prepared=0
				cc_clear_after_chunk=1
			fi
		else
			cc_prepared=0
		fi
		if ! _wait_for_player_pid "$play_pid" "$current_expected_sec" 0; then
			[ "${CHROME_AUDIO_USED:-0}" = "1" ] && _stop_chrome_audio_players
			play_failed=1
			[ "${SAY_PRESERVE_PRERENDERED_CHUNKS:-0}" = "1" ] || rm -f "$chunk_wav" 2>/dev/null
			break
		fi
		if [ "$cc_clear_after_chunk" -eq 1 ]; then
			docich_cc_clear || true
		fi
		[ "${SAY_PRESERVE_PRERENDERED_CHUNKS:-0}" = "1" ] || rm -f "$chunk_wav" 2>/dev/null
		if [ "$i" -lt $((total - 1)) ] && [ -n "$SAY_CHUNK_GAP_SEC" ] && [ "$SAY_CHUNK_GAP_SEC" != "0" ]; then
			_touch_lock_heartbeat
			sleep "$SAY_CHUNK_GAP_SEC"
			_touch_lock_heartbeat
		fi
	done
	docich_cc_clear || true

	[ "$play_failed" -eq 0 ] || return 1
	_log "事前合成済みチャンク再生完了"
	return 0
}

# ストリーミング中の合成ロック・再生ロックを維持するheartbeat。
# 親プロセスがSIGKILLされた場合も、所有者PIDが消えたことを検知して
# 自身で終了する。これにより孤児heartbeatがstale判定を妨げない。
_stream_start_heartbeat() {
	local owner_pid="${MY_OWNER%%:*}"
	if [ -n "${VOICEVOX_STREAM_HB_PID:-}" ]; then
		kill "$VOICEVOX_STREAM_HB_PID" 2>/dev/null || true
		wait "$VOICEVOX_STREAM_HB_PID" 2>/dev/null || true
	fi
	(
		while kill -0 "$owner_pid" 2>/dev/null; do
			_touch_lock_heartbeat
			_touch_voicevox_synth_lock_heartbeat
			sleep 2
		done
	) &
	VOICEVOX_STREAM_HB_PID=$!
}

_stream_stop_heartbeat() {
	if [ -n "${VOICEVOX_STREAM_HB_PID:-}" ]; then
		kill "$VOICEVOX_STREAM_HB_PID" 2>/dev/null || true
		wait "$VOICEVOX_STREAM_HB_PID" 2>/dev/null || true
		VOICEVOX_STREAM_HB_PID=""
	fi
}

# 翻訳計画がまだ生成中なら待たずに字幕を見送る。音声境界で最大数十秒
# ブロックしてしまうと、ストリーミング再生の利点を失うためである。
# 0=prepare成功、1=翻訳失敗、2=まだ生成中。
_stream_prepare_caption_if_ready() {
	local chunk_index="$1" sequence="$2" plan_pid="${DOCICH_CC_PLAN_PID:-}" plan_state=""
	if [ "${DOCICH_CC_PLAN_READY:-0}" != "1" ]; then
		if [ -n "$plan_pid" ] && kill -0 "$plan_pid" 2>/dev/null; then
			plan_state=$(ps -o stat= -p "$plan_pid" 2>/dev/null | tr -d '[:space:]' || true)
			case "$plan_state" in
			Z*) : ;;
			*) return 2 ;;
			esac
		fi
		docich_cc_wait_plan || return 1
	fi
	docich_cc_prepare "$chunk_index" "$sequence"
}

# VOICEVOXの1チャンクを、既存のSAY_RETRY_MAX/backoff契約で合成する。
_stream_synthesize_voicevox_chunk() {
	local text="$1" output="$2" retry=0 backoff="$SAY_RETRY_SLEEP_SEC" synth_ok=0
	while true; do
		if _acquire_voicevox_synth_lock "$(_voicevox_synth_lock_wait_sec)"; then
			_stream_start_heartbeat
			if _synthesize_chunk "$text" "$output"; then
				synth_ok=1
				_log "ストリーミング合成完了: $output"
			else
				_log "ストリーミング合成失敗: $output"
				rm -f "$output" 2>/dev/null || true
			fi
			_stream_stop_heartbeat
			_release_voicevox_synth_lock
		else
			_log "ストリーミング合成ロック取得失敗"
		fi
		[ "$synth_ok" -eq 1 ] && return 0
		if [ "$retry" -ge "$SAY_RETRY_MAX" ]; then
			return 1
		fi
		retry=$((retry + 1))
		_log "ストリーミング合成を${backoff}秒後に再試行 ${retry}/${SAY_RETRY_MAX}"
		_sleep_with_heartbeat "$backoff"
		if [ "$backoff" -lt "$SAY_RETRY_MAX_SLEEP_SEC" ]; then
			backoff=$((backoff * 2))
			[ "$backoff" -gt "$SAY_RETRY_MAX_SLEEP_SEC" ] && backoff="$SAY_RETRY_MAX_SLEEP_SEC"
		fi
	done
}

_stream_launch_voicevox_chunk() {
	local wav_file="$1" ready_dir="${SAY_STREAM_PLAYER_READY_DIR:-}" ready_file="" ready_wait=0
	if [ -n "$ready_dir" ]; then
		mkdir -p "$ready_dir" 2>/dev/null || true
		ready_file="$ready_dir/$(basename "$wav_file").${RANDOM}.ready"
		export SAY_STREAM_PLAYER_READY_FILE="$ready_file"
	fi
	CHROME_AUDIO_USED=0
	if ! _launch_stream_wav "$wav_file"; then
		unset SAY_STREAM_PLAYER_READY_FILE 2>/dev/null || true
		STREAM_PLAY_PID=""
		STREAM_EXPECTED_SEC=0
		return 1
	fi
	STREAM_PLAY_PID="$!"
	STREAM_EXPECTED_SEC=$(_estimate_audio_duration_sec "$wav_file")
	echo "$STREAM_PLAY_PID" >"$PID_FILE"
	LAST_SAY_PID="$STREAM_PLAY_PID"
	# _launch_bg_exec はラッパーをforkして即時returnするため、次チャンクの
	# 合成へ進む前に再生プロセスが起動する小さなsettle時間を設ける。
	# 長文の待ち時間を増やさないよう既定は20ms、環境で調整可能とする。
	local settle_sec="${SAY_STREAM_PLAY_START_SETTLE_SEC:-0.02}"
	if [ "$settle_sec" != "0" ]; then
		sleep "$settle_sec"
	fi
	if [ -n "$ready_file" ]; then
		while [ ! -e "$ready_file" ] && [ "$ready_wait" -lt 200 ]; do
			sleep 0.01
			ready_wait=$((ready_wait + 1))
		done
		unset SAY_STREAM_PLAYER_READY_FILE 2>/dev/null || true
		if [ ! -e "$ready_file" ]; then
			_log "ストリーミング再生プロセスのready待ちタイムアウト: $wav_file"
			_kill_player_pid "$STREAM_PLAY_PID"
			rm -f "$ready_file" 2>/dev/null || true
			return 1
		fi
		rm -f "$ready_file" 2>/dev/null || true
	else
		unset SAY_STREAM_PLAYER_READY_FILE 2>/dev/null || true
	fi
	if ! kill -0 "$STREAM_PLAY_PID" 2>/dev/null; then
		_log "ストリーミング再生プロセスが即時終了: $wav_file"
		return 1
	fi
	return 0
}

_stream_wait_voicevox_chunk() {
	local play_pid="$1" expected_sec="${2:-0}"
	if ! _wait_for_player_pid "$play_pid" "$expected_sec" 0; then
		if [ "${PLAYER_WAIT_TIMED_OUT:-0}" -eq 0 ] && [ "${expected_sec:-0}" -gt 0 ] \
			&& _partial_playback_already_heard "${PLAYER_WAIT_ELAPSED:-0}"; then
			[ "${CHROME_AUDIO_USED:-0}" = "1" ] && _stop_chrome_audio_players
			_log "ストリーミングチャンクは既に${PLAYER_WAIT_ELAPSED:-0}秒再生済み (rc=${PLAYER_WAIT_RC:-1}, expected=${expected_sec}s) → 重複防止のため再試行せず完了扱い"
			return 0
		fi
		if [ "${CHROME_AUDIO_USED:-0}" = "1" ]; then
			_stop_chrome_audio_players
			if [ "${PLAYER_WAIT_TIMED_OUT:-0}" -eq 0 ] && [ "${expected_sec:-0}" -gt 0 ] \
				&& ! _is_truncated_playback "${PLAYER_WAIT_ELAPSED:-0}" "$expected_sec"; then
				_log "Chrome/BlackHole fallback異常終了だが終盤まで再生済み (rc=${PLAYER_WAIT_RC:-1}, elapsed=${PLAYER_WAIT_ELAPSED:-0}s, expected=${expected_sec}s) → 重複防止のため完了扱い"
				return 0
			fi
		fi
		return "${PLAYER_WAIT_RC:-1}"
	fi
	if _is_truncated_playback "${PLAYER_WAIT_ELAPSED:-0}" "$expected_sec"; then
		if [ "${PLAYER_WAIT_ELAPSED:-0}" -le 1 ]; then
			# 次チャンク合成の方が再生より長いと、待機開始時点（または観測開始
			# 直後）で既に再生が終わっている。elapsed=0/1 は途中切断の証拠では
			# なく、合成待ち中に完走しただけなので、再試行すると同じチャンクが
			# 二重に聞こえる。ここでは正常完了として扱う。
			_log "ストリーミングチャンクは合成待ち中に完了 (elapsed=${PLAYER_WAIT_ELAPSED:-0}s, expected=${expected_sec}s)"
			return 0
		fi
		_log "ストリーミングチャンク途中切断の疑い (elapsed=${PLAYER_WAIT_ELAPSED:-0}s, expected=${expected_sec}s)"
		if _partial_playback_already_heard "${PLAYER_WAIT_ELAPSED:-0}"; then
			# resume API がない現状では、同じ WAV を先頭から再試行するより
			# 既に聞こえた部分を二重にしない at-most-once を優先する。
			_log "ストリーミングチャンクは既に${PLAYER_WAIT_ELAPSED:-0}秒再生済み → 重複防止のため再試行せず完了扱い"
			return 0
		fi
		return 98
	fi
	return 0
}

# 既に一度再生を試したチャンクの再試行。既読チャンクを先頭から
# やり直さず、失敗したチャンクだけを再生する。
_stream_retry_voicevox_playback() {
	local wav_file="$1" retry=1 backoff="$SAY_RETRY_SLEEP_SEC" play_rc=1
	if [ "$retry" -gt "$SAY_RETRY_MAX" ]; then
		_log "ストリーミングチャンク異常終了 → 再試行上限"
		return 1
	fi
	_log "ストリーミングチャンクを${backoff}秒後に再試行 ${retry}/${SAY_RETRY_MAX}"
	_sleep_with_heartbeat "$backoff"
	if [ "$backoff" -lt "$SAY_RETRY_MAX_SLEEP_SEC" ]; then
		backoff=$((backoff * 2))
		[ "$backoff" -gt "$SAY_RETRY_MAX_SLEEP_SEC" ] && backoff="$SAY_RETRY_MAX_SLEEP_SEC"
	fi
	while true; do
		if _stream_launch_voicevox_chunk "$wav_file"; then
			_stream_wait_voicevox_chunk "$STREAM_PLAY_PID" "$STREAM_EXPECTED_SEC"
			play_rc=$?
		else
			play_rc=97
		fi
		[ "$play_rc" -eq 0 ] && return 0
		[ "${CHROME_AUDIO_USED:-0}" = "1" ] && _stop_chrome_audio_players
		if [ -f "$QUEUE_DIR/kill_flag" ]; then
			rm -f "$QUEUE_DIR/kill_flag"
			_log "外部killフラグ検出 → チャンク再試行中止"
			return "$play_rc"
		fi
		if [ "$retry" -ge "$SAY_RETRY_MAX" ]; then
			_log "ストリーミングチャンク異常終了 (rc=$play_rc) → 再試行上限"
			return "$play_rc"
		fi
		retry=$((retry + 1))
		_log "ストリーミングチャンク異常終了 (rc=$play_rc) → ${backoff}秒後に再試行 ${retry}/${SAY_RETRY_MAX}"
		_sleep_with_heartbeat "$backoff"
		if [ "$backoff" -lt "$SAY_RETRY_MAX_SLEEP_SEC" ]; then
			backoff=$((backoff * 2))
			[ "$backoff" -gt "$SAY_RETRY_MAX_SLEEP_SEC" ] && backoff="$SAY_RETRY_MAX_SLEEP_SEC"
		fi
	done
}

# コメント用ストリーミング再生。先頭チャンクの再生中に次チャンクを
# 合成し、チャンク単位で失敗を再試行する。長さの上限は設けず、分割した
# 全チャンクをFIFO順に処理する。
_stream_voicevox_chunks() {
	local text chunk_line total=0 stream_dir current_wav next_wav
	local i=0 play_failed=0 next_synth_ok=1 play_rc=0 cc_available=0 cc_prepared=0 cc_prepared_for=-1 cc_clear_after_chunk=0 cc_rc=1
	local chunks=()
	text=$(cat "$MY_CONTENT" 2>/dev/null || true)
	while IFS= read -r chunk_line; do
		[ -n "$chunk_line" ] && chunks+=("$chunk_line")
	done < <(_split_tts_text "$text" 100 1)
	total=${#chunks[@]}
	[ "$total" -gt 1 ] || return 1

	stream_dir="$QUEUE_DIR/stream_${MY_TOKEN}"
	mkdir -p "$stream_dir" || return 1
	if docich_cc_start_plan "${chunks[@]}"; then
		cc_available=1
	fi
	_set_current_source "playing"
	_log "ストリーミング再生開始 (${total}チャンク)"

	local vo_voice_name="${VOICEVOX_RANDOM_VOICE_NAME:-}"
	if [ -n "$vo_voice_name" ] && [ "${VOICEVOX_RANDOM_MODE:-0}" = "1" ]; then
		local _chat_msg="VOICEVOX: [$VOICEVOX_SPEAKER] $vo_voice_name"
		[ -n "${PRE_SYNTH_PITCH:-}" ] && _chat_msg="$_chat_msg pitch=$PRE_SYNTH_PITCH"
		[ -n "${PRE_SYNTH_TEMPO:-}" ] && _chat_msg="$_chat_msg tempo=$PRE_SYNTH_TEMPO"
		case "$vo_voice_name" in *もち子*) _chat_msg="$_chat_msg [(cv 明日葉よもぎ)]" ;; esac
		enqueue_chat_message "$_chat_msg" "say_enqueue"
	fi

	current_wav="$stream_dir/chunk_0.wav"
	if ! _stream_synthesize_voicevox_chunk "${chunks[0]}" "$current_wav"; then
		return 1
	fi

	for ((i = 0; i < total; i++)); do
		[ -s "$current_wav" ] || {
			_log "ストリーミングWAVなし: $current_wav"
			play_failed=1
			break
		}

		cc_prepared=0
		if [ "$cc_available" -eq 1 ]; then
			if [ "$cc_prepared_for" -eq "$i" ]; then
				# 現在の再生中に一度だけprepare済みのchunkをそのままcommitする。
				cc_prepared=1
				cc_prepared_for=-1
			else
				_stream_prepare_caption_if_ready "$i" "$i"
				cc_rc=$?
				case "$cc_rc" in
				0) cc_prepared=1 ;;
				2) : ;; # 翻訳中。音声を待たせずこのチャンクは字幕なし。
				*) cc_available=0 ;;
				esac
			fi
		fi

		if ! _stream_launch_voicevox_chunk "$current_wav"; then
			if [ "$cc_prepared" -eq 1 ]; then
				cc_available=0
				cc_prepared=0
				cc_prepared_for=-1
				docich_cc_clear || true
			fi
			if ! _stream_retry_voicevox_playback "$current_wav"; then
				play_failed=1
				break
			fi
			# 初回起動失敗時は再試行が完了しているため、次チャンクの
			# 合成をここで行ってから次のループへ進む（重複再生はしない）。
			next_synth_ok=1
			if [ "$i" -lt $((total - 1)) ]; then
				next_wav="$stream_dir/chunk_$((i + 1)).wav"
				if ! _stream_synthesize_voicevox_chunk "${chunks[$((i + 1))]}" "$next_wav"; then
					next_synth_ok=0
				fi
			fi
		else
			if [ "$cc_prepared" -eq 1 ] && ! docich_cc_commit "$i"; then
				cc_available=0
				cc_prepared=0
				cc_prepared_for=-1
				docich_cc_clear || true
			fi

			next_synth_ok=1
			if [ "$i" -lt $((total - 1)) ]; then
				next_wav="$stream_dir/chunk_$((i + 1)).wav"
				# 現在の音声再生中に次chunkを一度だけprepareし、次ループで
				# 再prepareせずcommitする。これで音声境界の待ち時間を抑えつつ、
				# docichccのPAGE_ALREADY_PREPAREDも回避する。
				if [ "$cc_available" -eq 1 ]; then
					_stream_prepare_caption_if_ready "$((i + 1))" "$((i + 1))"
					cc_rc=$?
					case "$cc_rc" in
					0) cc_prepared_for=$((i + 1)) ;;
					2) : ;;
					*)
						cc_available=0
						cc_prepared_for=-1
						[ "${DOCICH_CC_DIRTY:-0}" = "1" ] && cc_clear_after_chunk=1
						;;
					esac
				fi
				if ! _stream_synthesize_voicevox_chunk "${chunks[$((i + 1))]}" "$next_wav"; then
					next_synth_ok=0
				fi
				# 合成後にplanが完了していれば、次ループでprepareを再試行する。
			fi

			_stream_wait_voicevox_chunk "$STREAM_PLAY_PID" "$STREAM_EXPECTED_SEC"
			play_rc=$?
			if [ "$play_rc" -ne 0 ]; then
				[ "${CHROME_AUDIO_USED:-0}" = "1" ] && _stop_chrome_audio_players
				docich_cc_clear || true
				cc_available=0
				cc_prepared_for=-1
				if ! _stream_retry_voicevox_playback "$current_wav"; then
					play_failed=1
				fi
			fi
		fi
		if [ "$cc_clear_after_chunk" -eq 1 ]; then
			docich_cc_clear || true
			cc_clear_after_chunk=0
		fi

		rm -f "$current_wav" 2>/dev/null || true
		[ "$play_failed" -eq 0 ] || break
		if [ "$i" -lt $((total - 1)) ] && [ "$next_synth_ok" -eq 0 ]; then
			play_failed=1
			break
		fi
		if [ "$i" -lt $((total - 1)) ] && [ -n "$SAY_CHUNK_GAP_SEC" ] && [ "$SAY_CHUNK_GAP_SEC" != "0" ]; then
			_touch_lock_heartbeat
			sleep "$SAY_CHUNK_GAP_SEC"
			_touch_lock_heartbeat
		fi
		current_wav="$stream_dir/chunk_$((i + 1)).wav"
	done

	docich_cc_clear || true
	[ "$play_failed" -eq 0 ] || return 1
	_log "ストリーミング再生完了"
	return 0
}

_concat_prerendered_voicevox_chunks() {
	local playlist_file="$1" output_file="$2"
	local concat_list="${playlist_file}.concat"
	local wav_count=0 wav_file="" abs_wav=""
	: >"$concat_list"
	while IFS= read -r wav_file; do
		[ -n "$wav_file" ] || continue
		[ -s "$wav_file" ] || return 1
		case "$wav_file" in
		/*) abs_wav="$wav_file" ;;
		*) abs_wav="$(pwd)/$wav_file" ;;
		esac
		printf "file '%s'\n" "$abs_wav" >>"$concat_list"
		wav_count=$((wav_count + 1))
	done <"$playlist_file"
	[ "$wav_count" -gt 0 ] || return 1
	if [ "$wav_count" -eq 1 ]; then
		cp "$abs_wav" "$output_file" 2>/dev/null && [ -s "$output_file" ]
	else
		ffmpeg -hide_banner -loglevel error -y -f concat -safe 0 -i "$concat_list" -c:a pcm_s16le -f wav "$output_file" >>"$DEBUG_LOG_FILE" 2>&1 && [ -s "$output_file" ]
	fi
}

_export_prerendered_voicevox_bundle() {
	local playlist_file="$1" captions_file="$2" bundle_dir="$3"
	local bundle_tmp="${bundle_dir}.tmp.${MY_TOKEN}"
	local playlist_dir="" entry="" source_wav="" target_name=""
	local audio_count=0 caption_count=0
	[ -s "$playlist_file" ] && [ -s "$captions_file" ] || return 1
	[ ! -e "$bundle_dir" ] || return 1
	playlist_dir=$(cd "$(dirname "$playlist_file")" 2>/dev/null && pwd) || return 1
	rm -rf "$bundle_tmp" 2>/dev/null || true
	mkdir -p "$bundle_tmp" || return 1
	: >"$bundle_tmp/playlist.txt" || return 1
	while IFS= read -r entry; do
		[ -n "$entry" ] || continue
		case "$entry" in
		/*) source_wav="$entry" ;;
		*) source_wav="$playlist_dir/$entry" ;;
		esac
		[ -s "$source_wav" ] || {
			rm -rf "$bundle_tmp" 2>/dev/null || true
			return 1
		}
		printf -v target_name 'chunk_%03d.wav' "$audio_count"
		cp "$source_wav" "$bundle_tmp/$target_name" 2>/dev/null || {
			rm -rf "$bundle_tmp" 2>/dev/null || true
			return 1
		}
		printf '%s\n' "$target_name" >>"$bundle_tmp/playlist.txt"
		audio_count=$((audio_count + 1))
	done <"$playlist_file"
	while IFS= read -r entry; do
		[ -n "$entry" ] && caption_count=$((caption_count + 1))
	done <"$captions_file"
	if [ "$audio_count" -le 0 ] || [ "$audio_count" -ne "$caption_count" ]; then
		rm -rf "$bundle_tmp" 2>/dev/null || true
		return 1
	fi
	cp "$captions_file" "$bundle_tmp/captions.txt" 2>/dev/null || {
		rm -rf "$bundle_tmp" 2>/dev/null || true
		return 1
	}
	mv "$bundle_tmp" "$bundle_dir" 2>/dev/null || {
		rm -rf "$bundle_tmp" 2>/dev/null || true
		return 1
	}
	return 0
}

_launch_say() {
	LAUNCHED_EXPECTED_SEC=0
	LAUNCH_MODE="say"

	# --- Pre-synthesized WAV (--wav mode) ---
	if [ "$WAV_MODE" = "true" ] && [ -s "$MY_CONTENT" ]; then
		LAUNCHED_EXPECTED_SEC=$(_estimate_audio_duration_sec "$MY_CONTENT")
		if ! _launch_stream_wav "$MY_CONTENT"; then
			_log "WAV 再生プレイヤー起動失敗"
			LAUNCHED_SAY_PID=""
			return
		fi
		LAUNCH_MODE="wav"
		LAUNCHED_SAY_PID="$!"
		return
	fi

	# --- 事前合成済みWAV ---
	if [ -n "${PRE_SYNTH_WAV:-}" ] && [ -s "$PRE_SYNTH_WAV" ]; then
		LAUNCHED_EXPECTED_SEC=$(_estimate_audio_duration_sec "$PRE_SYNTH_WAV")
		if [ -n "${SAY_AUDIO_DEVICE:-}" ]; then
			local device_index
			device_index=$(_resolve_audio_device_index "$SAY_AUDIO_DEVICE") || {
				if ! _launch_chrome_wav_bg "$PRE_SYNTH_WAV" "$PRE_SYNTH_WAV"; then
					_log "事前合成WAV再生プレイヤー起動失敗"
					LAUNCHED_SAY_PID=""
					return
				fi
				LAUNCH_MODE="voicevox_pre"
				LAUNCHED_SAY_PID="$!"
				_log "事前合成WAV再生 (device=Chrome/BlackHole fallback)"
				return
			}
			if ! _launch_ffmpeg_bg "$PRE_SYNTH_WAV" "$device_index" "$PRE_SYNTH_WAV"; then
				_log "事前合成WAV再生プレイヤー起動失敗 (ffmpeg)"
				LAUNCHED_SAY_PID=""
				return
			fi
		else
			if ! _launch_afplay_bg "$PRE_SYNTH_WAV" "$PRE_SYNTH_WAV"; then
				_log "事前合成WAV再生プレイヤー起動失敗 (afplay)"
				LAUNCHED_SAY_PID=""
				return
			fi
		fi
		LAUNCH_MODE="voicevox_pre"
		LAUNCHED_SAY_PID="$!"
		_log "事前合成WAV再生 ($PRE_SYNTH_WAV)"
		# 再生開始タイミングでチャットに話者名を投稿
		local vo_voice_name="${VOICEVOX_RANDOM_VOICE_NAME:-}"
		if [ -n "$vo_voice_name" ] && [ "${VOICEVOX_RANDOM_MODE:-0}" = "1" ]; then
			local _chat_msg="VOICEVOX: [$VOICEVOX_SPEAKER] $vo_voice_name"
			[ -n "${PRE_SYNTH_PITCH:-}" ] && _chat_msg="$_chat_msg pitch=$PRE_SYNTH_PITCH"
			[ -n "${PRE_SYNTH_TEMPO:-}" ] && _chat_msg="$_chat_msg tempo=$PRE_SYNTH_TEMPO"
			case "$vo_voice_name" in *もち子*) _chat_msg="$_chat_msg [(cv 明日葉よもぎ)]" ;; esac
			enqueue_chat_message "$_chat_msg" "say_enqueue"
		fi
		return
	fi

	# --- 同志モード: コメント再生時に macOS say へ一時切替 ---
	if [ -f "tmp/voicevox_dousi.txt" ]; then
		case "${SOURCE_LABEL:-}" in
		comment | comment:*)
			rm -f "tmp/voicevox_dousi.txt"
			USE_VOICEVOX=0
			USE_COEIROINK=0
			_log "同志mode: macOS say"
			;;
		esac
	fi

	# --- ASMR モード: コメント再生時にささやき系ボイスへ一時切替 ---
	if [ "${USE_VOICEVOX:-0}" = "1" ] && [ -f "tmp/voicevox_asmr.txt" ]; then
		case "${SOURCE_LABEL:-}" in
		comment | comment:*)
			local _asmr_result
			_asmr_result=$(_pick_asmr_voicevox_speaker)
			VOICEVOX_SPEAKER="${_asmr_result%%|*}"
			VOICEVOX_RANDOM_VOICE_NAME="${_asmr_result#*|}"
			VOICEVOX_RANDOM_MODE=1
			rm -f "tmp/voicevox_asmr.txt"
			_log "ASMR mode: speaker=$VOICEVOX_SPEAKER ($VOICEVOX_RANDOM_VOICE_NAME)"
			;;
		esac
	fi

	# --- VOICEVOX TTS ---
	if [ "${USE_VOICEVOX:-0}" = "1" ]; then
		# 合成直前に粛清リストを再チェック — 粛清済みなら別のスピーカーに差し替え
		if [ -f "config/voicevox_exclude_ids.txt" ] && grep -q "^${VOICEVOX_SPEAKER}\b" "config/voicevox_exclude_ids.txt" 2>/dev/null; then
			_log "speaker=$VOICEVOX_SPEAKER は粛清済み → 再選択"
			_reroll=$(_pick_random_voicevox_speaker)
			VOICEVOX_SPEAKER="${_reroll%%|*}"
			VOICEVOX_RANDOM_VOICE_NAME="${_reroll#*|}"
		fi
		local vo_voice_name="${VOICEVOX_RANDOM_VOICE_NAME:-}"
		# IDごとのピッチ・テンポ設定をルックアップ
		local vo_pitch="" vo_tempo="" vo_intonation=""
		if [ -f "config/voicevox_pitch_map.txt" ]; then
			vo_pitch=$(grep "^${VOICEVOX_SPEAKER}|" "config/voicevox_pitch_map.txt" 2>/dev/null | tail -1 | cut -d'|' -f2)
		fi
		if [ -f "config/voicevox_tempo_map.txt" ]; then
			vo_tempo=$(grep "^${VOICEVOX_SPEAKER}|" "config/voicevox_tempo_map.txt" 2>/dev/null | tail -1 | cut -d'|' -f2)
		fi
		if [ -f "config/voicevox_intonation_map.txt" ]; then
			vo_intonation=$(grep "^${VOICEVOX_SPEAKER}|" "config/voicevox_intonation_map.txt" 2>/dev/null | tail -1 | cut -d'|' -f2)
		fi
		_log "VOICEVOX speaker=$VOICEVOX_SPEAKER${vo_voice_name:+ ($vo_voice_name)}${vo_pitch:+ pitch=$vo_pitch}${vo_tempo:+ tempo=$vo_tempo}${vo_intonation:+ intonation=$vo_intonation}"
		local vo_wav
		vo_wav="${MY_CONTENT%.txt}.wav"
		# フォールバック合成のタイムアウトもコンテキスト連動（コメント=長め/ラジオ=短め）
		local _fb_timeout
		_fb_timeout=$(_voicevox_synth_timeout_sec)
		# フォールバック合成時もVOICEVOX合成ロックを取得（同時1リクエスト制限）
		local _vo_synth_locked=0 _hb_pid=""
		if ! _acquire_voicevox_synth_lock "$(_voicevox_synth_lock_wait_sec)"; then
			_log "VOICEVOX合成ロック取得タイムアウト → リトライへ"
		else
			_vo_synth_locked=1
		fi
		local _vo_ok=0
		if [ "$_vo_synth_locked" -eq 1 ]; then
			# 合成中もheartbeatを更新（stale判定回避）
			(while true; do
				_touch_lock_heartbeat
				_touch_voicevox_synth_lock_heartbeat
				sleep 2
			done) &
			_hb_pid=$!
			if [ -n "$TIMEOUT_CMD" ]; then
				# 一時的にtimeoutを無効化: 原因調査中
				if VOICEVOX_SPEAKER="$VOICEVOX_SPEAKER" VOICEVOX_PITCH="$vo_pitch" VOICEVOX_TEMPO="$vo_tempo" VOICEVOX_INTONATION="$vo_intonation" VOICEVOX_TIMEOUT="$_fb_timeout" \
					./voicevox_tts.sh -o "$vo_wav" -f "$MY_CONTENT" 2>/dev/null && [ -s "$vo_wav" ]; then
					_vo_ok=1
				fi
			elif VOICEVOX_SPEAKER="$VOICEVOX_SPEAKER" VOICEVOX_PITCH="$vo_pitch" VOICEVOX_TEMPO="$vo_tempo" VOICEVOX_INTONATION="$vo_intonation" VOICEVOX_TIMEOUT="$_fb_timeout" \
				./voicevox_tts.sh -o "$vo_wav" -f "$MY_CONTENT" 2>/dev/null && [ -s "$vo_wav" ]; then
				_vo_ok=1
			fi
		fi
		if [ -n "$_hb_pid" ]; then
			kill "$_hb_pid" 2>/dev/null
			wait "$_hb_pid" 2>/dev/null
		fi
		if [ "$_vo_synth_locked" -eq 1 ]; then
			_release_voicevox_synth_lock
			_vo_synth_locked=0
		fi
		if [ "$_vo_ok" -eq 1 ]; then
			# DEBUG: log exit details when synthesis fails
			if [ "$_vo_ok" -ne 1 ]; then
				_log "DEBUG: vo_synth FAILED — _vo_synth_locked=$_vo_synth_locked _vo_ok=$_vo_ok USE_VOICEVOX=$USE_VOICEVOX USE_COEIROINK=$USE_COEIROINK"
			fi
			# vo_random 時はチャットに話者名を投稿
			if [ -n "$vo_voice_name" ] && [ "${VOICEVOX_RANDOM_MODE:-0}" = "1" ]; then
				local _chat_msg="VOICEVOX: [$VOICEVOX_SPEAKER] $vo_voice_name"
				[ -n "$vo_pitch" ] && _chat_msg="$_chat_msg pitch=$vo_pitch"
				[ -n "$vo_tempo" ] && _chat_msg="$_chat_msg tempo=$vo_tempo"
				case "$vo_voice_name" in *もち子*) _chat_msg="$_chat_msg [(cv 明日葉よもぎ)]" ;; esac
				enqueue_chat_message "$_chat_msg" "say_enqueue"
			fi
			LAUNCHED_EXPECTED_SEC=$(_estimate_audio_duration_sec "$vo_wav")
			if [ -n "${SAY_AUDIO_DEVICE:-}" ]; then
				local device_index
				device_index=$(_resolve_audio_device_index "$SAY_AUDIO_DEVICE") || {
					if ! _launch_chrome_wav_bg "$vo_wav" "$vo_wav"; then
						_log "VOICEVOX WAV再生プレイヤー起動失敗"
						LAUNCHED_SAY_PID=""
						return
					fi
					LAUNCH_MODE="voicevox"
					LAUNCHED_SAY_PID="$!"
					_log "VOICEVOX WAV再生 (device=Chrome/BlackHole fallback)"
					return
				}
				if ! _launch_ffmpeg_bg "$vo_wav" "$device_index" "$vo_wav"; then
					_log "VOICEVOX WAV再生プレイヤー起動失敗 (ffmpeg)"
					LAUNCHED_SAY_PID=""
					return
				fi
			else
				if ! _launch_afplay_bg "$vo_wav" "$vo_wav"; then
					_log "VOICEVOX WAV再生プレイヤー起動失敗 (afplay)"
					LAUNCHED_SAY_PID=""
					return
				fi
			fi
			LAUNCH_MODE="voicevox"
			LAUNCHED_SAY_PID="$!"
			return
		else
			_log "VOICEVOX合成失敗 → リトライへ"
			LAUNCHED_SAY_PID=""
			return
		fi
	fi

	# --- COEIROINK TTS ---
	if [ "${USE_COEIROINK:-0}" = "1" ]; then
		local coe_text coe_wav
		coe_text=$(cat "$MY_CONTENT" 2>/dev/null)
		coe_wav="${MY_CONTENT%.txt}.wav"
		if SPEAKER_UUID="$COEIROINK_SPEAKER_UUID" STYLE_ID="$COEIROINK_STYLE_ID" \
			./coeiroink_tts.sh -o "$coe_wav" "$coe_text" >/dev/null 2>&1 && [ -s "$coe_wav" ]; then
			LAUNCHED_EXPECTED_SEC=$(_estimate_audio_duration_sec "$coe_wav")
			if ! _launch_afplay_bg "$coe_wav" "$coe_wav"; then
				_log "COEIROINK WAV再生プレイヤー起動失敗"
				LAUNCHED_SAY_PID=""
				return
			fi
			LAUNCH_MODE="coeiroink"
			LAUNCHED_SAY_PID="$!"
			return
		else
			_log "COEIROINK合成失敗 → リトライへ"
			LAUNCHED_SAY_PID=""
			return
		fi
	fi
	# --- /COEIROINK ---

	# VOICEVOX/COEIROINK が有効な場合、フォールバックを行わずリトライ
	if [ "${USE_VOICEVOX:-0}" = "1" ] || [ "${USE_COEIROINK:-0}" = "1" ]; then
		_log "TTS合成失敗 → リトライへ（フォールバック無効）"
		LAUNCHED_SAY_PID=""
		return
	fi

	# --- Google Cloud TTS (デフォルト音声合成) ---
	if [ "${GOOGLE_TTS_FAILED:-0}" != "1" ]; then
		local gtts_mp3="/tmp/google_tts_${$}_$(date +%s).mp3"
		if ./google_tts.sh -o "$gtts_mp3" -f "$MY_CONTENT" 2>/dev/null && [ -s "$gtts_mp3" ]; then
			LAUNCHED_EXPECTED_SEC=$(_estimate_audio_duration_sec "$gtts_mp3")
			if [ "${LAUNCHED_EXPECTED_SEC:-0}" -gt 0 ]; then
				if [ -n "${SAY_AUDIO_DEVICE:-}" ] && [ "${SAY_FORCE_DIRECT:-0}" != "1" ]; then
					local device_index
					device_index=$(_resolve_audio_device_index "$SAY_AUDIO_DEVICE") || {
						_log "audio device解決失敗 → afplayフォールバック"
						if ! _launch_afplay_bg "$gtts_mp3" "$gtts_mp3"; then
							_log "Google TTS再生プレイヤー起動失敗"
							LAUNCHED_SAY_PID=""
							return
						fi
						LAUNCH_MODE="google_tts"
						LAUNCHED_SAY_PID="$!"
						return
					}
					if ! _launch_ffmpeg_bg "$gtts_mp3" "$device_index" "$gtts_mp3"; then
						_log "Google TTS再生プレイヤー起動失敗 (ffmpeg)"
						LAUNCHED_SAY_PID=""
						return
					fi
					LAUNCH_MODE="ffmpeg"
				else
					if ! _launch_afplay_bg "$gtts_mp3" "$gtts_mp3"; then
						_log "Google TTS再生プレイヤー起動失敗 (afplay)"
						LAUNCHED_SAY_PID=""
						return
					fi
					LAUNCH_MODE="google_tts"
				fi
				LAUNCHED_SAY_PID="$!"
				return
			fi
			rm -f "$gtts_mp3" 2>/dev/null || true
		else
			rm -f "$gtts_mp3" 2>/dev/null || true
		fi
		_log "Google TTS失敗 → macOS sayフォールバック"
	fi

	# --- macOS say (最終フォールバック) ---
	# Linux には say が存在しないため、このフォールバック全体を macOS のみに限定する。
	if [ "$IS_LINUX" = "1" ]; then
		_log "Linux では say フォールバックなし (Google TTS も失敗済み)"
		LAUNCHED_SAY_PID=""
		return
	fi
	if [ -n "${SAY_AUDIO_DEVICE:-}" ] && [ "${SAY_FORCE_DIRECT:-0}" != "1" ]; then
		local device_index
		device_index=$(_resolve_audio_device_index "$SAY_AUDIO_DEVICE") || {
			_log "audio device解決失敗 (${SAY_AUDIO_DEVICE}) → デフォルト出力にフォールバック"
			if ! _launch_say_bg "$RATE" "$MY_CONTENT"; then
				LAUNCHED_SAY_PID=""
				return
			fi
			LAUNCH_MODE="say"
			LAUNCHED_SAY_PID="$!"
			return
		}
		local aiff_file="${MY_CONTENT%.txt}.aiff"
		if ! say -a "${SAY_AUDIO_DEVICE:-}" -r "$RATE" -o "$aiff_file" -f "$MY_CONTENT" >/dev/null 2>&1; then
			_log "say音声生成失敗 (rc!=0)"
			LAUNCHED_SAY_PID=""
			return
		fi
		LAUNCHED_EXPECTED_SEC=$(_estimate_audio_duration_sec "$aiff_file")
		if [ "${LAUNCHED_EXPECTED_SEC:-0}" -le 0 ]; then
			_log "say音声生成失敗 (duration=${LAUNCHED_EXPECTED_SEC:-0}s)"
			rm -f "$aiff_file" 2>/dev/null || true
			LAUNCHED_SAY_PID=""
			return
		fi
		if ! _launch_ffmpeg_bg "$aiff_file" "$device_index" "$aiff_file"; then
			_log "say AIFF再生プレイヤー起動失敗"
			LAUNCHED_SAY_PID=""
			return
		fi
		LAUNCH_MODE="ffmpeg"
	else
		if ! _launch_say_bg "$RATE" "$MY_CONTENT"; then
			LAUNCHED_SAY_PID=""
			return
		fi
		LAUNCH_MODE="say"
		LAUNCHED_EXPECTED_SEC=$(_estimate_text_duration_sec "$MY_CONTENT" "$RATE")
	fi
	LAUNCHED_SAY_PID="$!"
}

# --- soren91:ranking_comment の古い再生をスキップ ---
if _should_skip_stale_soren91_ranking; then
	_log "skipping stale soren91:ranking_comment"
	_release_lock
	exit 0
fi

_play_with_retry() {
	local retry=0 backoff="$SAY_RETRY_SLEEP_SEC"
	local cc_for_retry=0 cc_prepared=0
	if [ "${DOCICH_CC_PLAN_CHUNK_COUNT:-0}" -eq 1 ]; then
		cc_for_retry=1
	fi
	LAST_SAY_PID=""
	SAY_FORCE_DIRECT=0
	GOOGLE_TTS_FAILED=0
	while true; do
		local attempt=$((retry + 1))
		_set_current_source "playing"
		_log "say開始 (attempt=${attempt}, rate=${RATE})"
		local say_pid
		cc_prepared=0
		if [ "$cc_for_retry" -eq 1 ]; then
			if docich_cc_prepare 0 0; then
				cc_prepared=1
			else
				cc_for_retry=0
			fi
		fi
		LAUNCHED_SAY_PID=""
		CHROME_AUDIO_USED=0
		_launch_say
		say_pid="${LAUNCHED_SAY_PID:-}"
		if [ -z "$say_pid" ]; then
			_log "say起動失敗"
		else
			if [ "$cc_prepared" -eq 1 ] && ! docich_cc_commit 0; then
				cc_for_retry=0
				docich_cc_clear || true
			fi
			LAST_SAY_PID="$say_pid"
			echo "$say_pid" >"$PID_FILE"
			# 初回再生開始時にCC表記をTwitchチャットに投稿
			if [ "$attempt" -eq 1 ] && [ -n "${SAY_CC_TEXT:-}" ]; then
				enqueue_chat_message "$SAY_CC_TEXT" "say_enqueue"
			fi
		fi
		local start_ts now_ts elapsed say_rc expected_sec max_wait_sec timed_out
		start_ts=$(date +%s)
		expected_sec="${LAUNCHED_EXPECTED_SEC:-0}"
		max_wait_sec=0
		timed_out=0
		# 期待尺が取れる経路（SAY_AUDIO_DEVICE経由）では、ハング監視を有効化
		if [ "${expected_sec:-0}" -gt 0 ]; then
			max_wait_sec=$((expected_sec + SAY_TRUNCATE_GRACE_SEC + SAY_HANG_EXTRA_SEC))
		fi
		if [ -z "$say_pid" ]; then
			say_rc=97
			now_ts=$(date +%s)
			elapsed=$((now_ts - start_ts))
		else
			if ! _wait_for_player_pid "$say_pid" "$expected_sec" 0; then
				timed_out="$PLAYER_WAIT_TIMED_OUT"
			fi
			say_rc="$PLAYER_WAIT_RC"
			elapsed="$PLAYER_WAIT_ELAPSED"
		fi
		if [ "$timed_out" -eq 0 ] && [ "${expected_sec:-0}" -gt 0 ] \
			&& _partial_playback_already_heard "$elapsed" \
			&& { [ "$say_rc" -ne 0 ] || _is_truncated_playback "$elapsed" "$expected_sec"; }; then
			_log "sayは既に${elapsed}秒再生済み (rc=$say_rc, expected=${expected_sec}s) → 重複防止のため再試行せず完了扱い"
			docich_cc_clear || true
			return 0
		fi
		if [ "$say_rc" -eq 0 ] && _is_truncated_playback "$elapsed" "$expected_sec"; then
			say_rc=98
			_log "say途中切断の疑い (elapsed=${elapsed}s, expected=${expected_sec}s)"
		fi
		if [ "$say_rc" -eq 0 ]; then
			docich_cc_clear || true
			return 0
		fi
		docich_cc_clear || true
		if [ "${CHROME_AUDIO_USED:-0}" = "1" ]; then
			_stop_chrome_audio_players
			if [ "${timed_out:-0}" -eq 0 ] && [ "${expected_sec:-0}" -gt 0 ] && ! _is_truncated_playback "$elapsed" "$expected_sec"; then
				_log "Chrome/BlackHole fallback異常終了だが終盤まで再生済み (rc=$say_rc, elapsed=${elapsed}s, expected=${expected_sec}s) → 重複防止のため再試行せず完了扱い"
				return 0
			fi
		fi
		# 粛清等による外部killフラグがあればリトライしない
		if [ -f "$QUEUE_DIR/kill_flag" ]; then
			rm -f "$QUEUE_DIR/kill_flag"
			_log "外部killフラグ検出 → リトライ中止"
			return "$say_rc"
		fi
		if [ "${LAUNCH_MODE:-say}" = "google_tts" ]; then
			GOOGLE_TTS_FAILED=1
			_log "Google TTS再生失敗 (rc=$say_rc) → 次回は macOS sayフォールバック"
		elif [ "${LAUNCH_MODE:-say}" = "ffmpeg" ] && [ "$SAY_FORCE_DIRECT" -eq 0 ]; then
			SAY_FORCE_DIRECT=1
			_log "ffmpeg再生失敗 (rc=$say_rc) → 次回は say 直再生へフォールバック"
		fi
		if [ "$retry" -ge "$SAY_RETRY_MAX" ]; then
			_log "say異常終了 (rc=$say_rc, elapsed=${elapsed}s, expected=${expected_sec}s) → 再試行上限"
			return "$say_rc"
		fi
		retry=$((retry + 1))
		_set_current_source "retry_wait"
		_log "say異常終了 (rc=$say_rc, elapsed=${elapsed}s, expected=${expected_sec}s) → ${backoff}s後に再試行 ${retry}/${SAY_RETRY_MAX}"
		_sleep_with_heartbeat "$backoff"
		if [ "$backoff" -lt "$SAY_RETRY_MAX_SLEEP_SEC" ]; then
			backoff=$((backoff * 2))
			[ "$backoff" -gt "$SAY_RETRY_MAX_SLEEP_SEC" ] && backoff="$SAY_RETRY_MAX_SLEEP_SEC"
		fi
	done
}

_wait_for_turn() {
	local yield_count=0
	while true; do
		_acquire_lock
		lock_ret=$?
		if [ "$lock_ret" -ne 0 ]; then
			_log "ロック取得失敗 → 諦め"
			exit 0
		fi
		_set_current_source "waiting"
		if _radio_should_yield_to_comment; then
			yield_count=$((yield_count + 1))
			_log "comment backlog を優先するため radio が順番を譲る (${yield_count})"
			_release_lock
			sleep 1
			continue
		fi
		break
	done
}

_prepare_playback_turn() {
	local pre_delay="${1:-0}" waited_pre prev_pid=""
	while true; do
		_wait_for_turn

		if [ -f "$PID_FILE" ]; then
			prev_pid=$(cat "$PID_FILE" 2>/dev/null)
			if [ -n "$prev_pid" ] && kill -0 "$prev_pid" 2>/dev/null; then
				_log "前のsay (PID=$prev_pid) がまだ再生中 → 終了待ち"
				while kill -0 "$prev_pid" 2>/dev/null; do
					if _radio_should_yield_to_comment; then
						_yield_turn_to_pending_comment
						continue 2
					fi
					_touch_lock_heartbeat
					sleep 1
				done
			fi
			rm -f "$PID_FILE"
		fi

		# 孤児say/afplayプロセス検出: ロック取得済み＝前の所有者は死んでいるので、残留プロセスはkillして進む
		local _orphan_pids _orphan_wait=0
		while true; do
			_orphan_pids=$(pgrep -x 'say|afplay' 2>/dev/null || true)
			if [ -z "$_orphan_pids" ]; then
				_orphan_pids=$(pgrep -xf "ffmpeg.*audiotoolbox" 2>/dev/null || true)
				[ -z "$_orphan_pids" ] && break
			fi
			if [ "$_orphan_wait" -ge 3 ]; then
				_log "残留say/ffmpegプロセス検出 → kill: $_orphan_pids"
				echo "$_orphan_pids" | xargs kill 2>/dev/null || true
				sleep 1
				echo "$_orphan_pids" | xargs kill -9 2>/dev/null || true
				break
			fi
			[ "$_orphan_wait" -eq 0 ] && _log "既存sayプロセス検出 → 短時間待機後にkill"
			_touch_lock_heartbeat
			sleep 1
			_orphan_wait=$((_orphan_wait + 1))
		done

		_set_current_source "waiting"
		_log "トーク開始まで ${pre_delay}秒 待機..."
		waited_pre=0
		while [ "$waited_pre" -lt "$pre_delay" ]; do
			if _radio_should_yield_to_comment; then
				_yield_turn_to_pending_comment
				continue 2
			fi
			_touch_lock_heartbeat
			sleep 1
			waited_pre=$((waited_pre + 1))
		done

		if _radio_should_yield_to_comment; then
			_yield_turn_to_pending_comment
			continue
		fi
		return 0
	done
}

# --- VOICEVOX 事前合成（ロック取得前＝前の再生中に並行合成） ---
PRE_SYNTH_WAV=""
PRE_SYNTH_PLAYLIST_FILE=""
_pre_chunks=()
STREAM_VOICEVOX_CHUNKS=0

# コメントの長文だけは、再生ロック取得後にチャンクを順次合成する。
# render-only と外部WAV bundleは完成品を必要とするため従来経路のままにする。
if [ "$WAV_MODE" = "false" ] && [ "$RENDER_ONLY" != "true" ] \
	&& [ "${USE_VOICEVOX:-0}" = "1" ] && _is_streaming_comment_source; then
	_stream_probe_text=$(cat "$MY_CONTENT" 2>/dev/null || true)
	_stream_probe_chunks=()
	while IFS= read -r _stream_probe_line; do
		[ -n "$_stream_probe_line" ] && _stream_probe_chunks+=("$_stream_probe_line")
	done < <(_split_tts_text "$_stream_probe_text" 100 1)
	if [ "${#_stream_probe_chunks[@]}" -gt 1 ]; then
		_prepare_voicevox_runtime_params
		if [ "${USE_VOICEVOX:-0}" = "1" ]; then
			STREAM_VOICEVOX_CHUNKS=1
			_log "コメント長文: 全チャンク事前合成を行わず、合成完了チャンクから順次再生"
		fi
	fi
fi

if [ "$WAV_PLAYLIST_MODE" = "true" ]; then
	_external_caption_chunks=()
	_external_playlist_count=0
	while IFS= read -r _external_line; do
		[ -n "$_external_line" ] && _external_caption_chunks+=("$_external_line")
	done <"$CAPTION_CHUNKS_FILE"
	while IFS= read -r _external_line; do
		[ -n "$_external_line" ] && _external_playlist_count=$((_external_playlist_count + 1))
	done <"$WAV_PLAYLIST_FILE"
	if [ "$_external_playlist_count" -le 0 ] || [ "$_external_playlist_count" -ne "${#_external_caption_chunks[@]}" ]; then
		_log "WAV bundle不整合: audio=${_external_playlist_count} captions=${#_external_caption_chunks[@]}"
		exit 2
	fi
	docich_cc_start_plan "${_external_caption_chunks[@]}" || true
elif [ "$WAV_MODE" = "false" ] && [ "${USE_VOICEVOX:-0}" = "1" ] \
	&& [ "$STREAM_VOICEVOX_CHUNKS" -eq 0 ]; then
			# 事前合成は同時1つに制限（VOICEVOX APIの同時リクエスト制限回避）
			if ! _acquire_voicevox_synth_lock "$(_voicevox_synth_lock_wait_sec)"; then
				if [ "${VOICEVOX_SYNTH_LOCK_BUSY_REASON:-}" = "priority_waiter" ]; then
					_log "事前合成一時保留（優先音声待ち）"
				else
					_log "事前合成スキップ（別プロセスが合成中）"
				fi
	else
		_log "事前合成開始"
		_pre_synth_hb_pid=""
		(while true; do
			_touch_voicevox_synth_lock_heartbeat
			sleep 2
		done) &
		_pre_synth_hb_pid=$!

		# ワンショットスピーカー指定 (!NTROB等)
		if [ -f "tmp/voicevox_oneshot_speaker.txt" ]; then
			case "${SOURCE_LABEL:-}" in
			comment | comment:*)
				VOICEVOX_SPEAKER=$(cat "tmp/voicevox_oneshot_speaker.txt" 2>/dev/null)
				VOICEVOX_RANDOM_VOICE_NAME=""
				VOICEVOX_RANDOM_MODE=0
				rm -f "tmp/voicevox_oneshot_speaker.txt"
				_log "ワンショットスピーカー: $VOICEVOX_SPEAKER"
				;;
			esac
		fi

		# 同志モード: macOS say へ一時切替
		if [ -f "tmp/voicevox_dousi.txt" ]; then
			case "${SOURCE_LABEL:-}" in
			comment | comment:*)
				rm -f "tmp/voicevox_dousi.txt"
				USE_VOICEVOX=0
				USE_COEIROINK=0
				_log "同志mode: macOS say (事前合成スキップ)"
				;;
			esac
		fi

		# ASMR モード
		if [ "${USE_VOICEVOX:-0}" = "1" ] && [ -f "tmp/voicevox_asmr.txt" ]; then
			case "${SOURCE_LABEL:-}" in
			comment | comment:*)
				_asmr_result=""
				_asmr_result=$(_pick_asmr_voicevox_speaker 2>/dev/null || echo "")
				VOICEVOX_SPEAKER="${_asmr_result%%|*}"
				VOICEVOX_RANDOM_VOICE_NAME="${_asmr_result#*|}"
				VOICEVOX_RANDOM_MODE=1
				rm -f "tmp/voicevox_asmr.txt"
				_log "ASMR mode: speaker=$VOICEVOX_SPEAKER ($VOICEVOX_RANDOM_VOICE_NAME)"
				;;
			esac
		fi

		if [ "${USE_VOICEVOX:-0}" = "1" ]; then
			# 粛清チェック
			if [ -f "config/voicevox_exclude_ids.txt" ] && grep -q "^${VOICEVOX_SPEAKER}\b" "config/voicevox_exclude_ids.txt" 2>/dev/null; then
				_log "speaker=$VOICEVOX_SPEAKER は粛清済み → 再選択"
				_reroll=""
				_reroll=$(_pick_random_voicevox_speaker)
				VOICEVOX_SPEAKER="${_reroll%%|*}"
				VOICEVOX_RANDOM_VOICE_NAME="${_reroll#*|}"
			fi

			# ピッチ・テンポ（スクリプトレベル変数に保存 → _launch_say でチャット投稿に使用）
			PRE_SYNTH_PITCH="" PRE_SYNTH_TEMPO="" PRE_SYNTH_INTONATION=""
			vo_pitch="" vo_tempo="" vo_intonation=""
			[ -f "config/voicevox_pitch_map.txt" ] && vo_pitch=$(grep "^${VOICEVOX_SPEAKER}|" "config/voicevox_pitch_map.txt" 2>/dev/null | tail -1 | cut -d'|' -f2)
			[ -f "config/voicevox_tempo_map.txt" ] && vo_tempo=$(grep "^${VOICEVOX_SPEAKER}|" "config/voicevox_tempo_map.txt" 2>/dev/null | tail -1 | cut -d'|' -f2)
			[ -f "config/voicevox_intonation_map.txt" ] && vo_intonation=$(grep "^${VOICEVOX_SPEAKER}|" "config/voicevox_intonation_map.txt" 2>/dev/null | tail -1 | cut -d'|' -f2)
			PRE_SYNTH_PITCH="$vo_pitch" PRE_SYNTH_TEMPO="$vo_tempo" PRE_SYNTH_INTONATION="$vo_intonation"
			vo_voice_name="${VOICEVOX_RANDOM_VOICE_NAME:-}"
			_log "VOICEVOX 事前合成 speaker=$VOICEVOX_SPEAKER${vo_voice_name:+ ($vo_voice_name)}${vo_pitch:+ pitch=$vo_pitch}${vo_tempo:+ tempo=$vo_tempo}${vo_intonation:+ intonation=$vo_intonation}"

			PRE_SYNTH_WAV="${MY_CONTENT%.txt}_pre.wav"
			PRE_SYNTH_CHUNKS_FILE=""
			PRE_SYNTH_PLAYLIST_FILE=""

			# 既存のVOICEVOX分割を字幕計画にもそのまま使い、音声経路を変えない。
			_pre_text=$(cat "$MY_CONTENT" 2>/dev/null)
			_pre_chunk_chars=100
			_pre_chunks=()
			while IFS= read -r _pc_line; do
				[ -n "$_pc_line" ] && _pre_chunks+=("$_pc_line")
			done < <(_split_tts_text "$_pre_text" "$_pre_chunk_chars")

			if [ ${#_pre_chunks[@]} -le 1 ]; then
				docich_cc_start_plan "${_pre_chunks[@]}" || true
				# 短いテキスト: 従来通り全文を1回で合成
				PRE_SINGLE_TIMEOUT=$(_voicevox_synth_timeout_sec)
				if [ -n "$TIMEOUT_CMD" ]; then
					if $TIMEOUT_CMD -k "$VOICEVOX_SYNTH_KILL_AFTER_SEC" "$VOICEVOX_SYNTH_TIMEOUT_SEC" \
						VOICEVOX_SPEAKER="$VOICEVOX_SPEAKER" VOICEVOX_PITCH="$vo_pitch" VOICEVOX_TEMPO="$vo_tempo" VOICEVOX_INTONATION="$vo_intonation" VOICEVOX_TIMEOUT="$PRE_SINGLE_TIMEOUT" \
						./voicevox_tts.sh -o "$PRE_SYNTH_WAV" -f "$MY_CONTENT" 2>/dev/null && [ -s "$PRE_SYNTH_WAV" ]; then
						_log "事前合成完了: $PRE_SYNTH_WAV"
					else
						_log "事前合成失敗 → 再生時にフォールバック"
						rm -f "$PRE_SYNTH_WAV" 2>/dev/null
						PRE_SYNTH_WAV=""
					fi
				elif VOICEVOX_SPEAKER="$VOICEVOX_SPEAKER" VOICEVOX_PITCH="$vo_pitch" VOICEVOX_TEMPO="$vo_tempo" VOICEVOX_INTONATION="$vo_intonation" VOICEVOX_TIMEOUT="$PRE_SINGLE_TIMEOUT" \
					./voicevox_tts.sh -o "$PRE_SYNTH_WAV" -f "$MY_CONTENT" 2>/dev/null && [ -s "$PRE_SYNTH_WAV" ]; then
					_log "事前合成完了: $PRE_SYNTH_WAV"
				else
					_log "事前合成失敗 → 再生時にフォールバック"
					rm -f "$PRE_SYNTH_WAV" 2>/dev/null
					PRE_SYNTH_WAV=""
				fi
			else
				# 複数チャンク: 再生ロック取得前に全チャンクを合成してから再生待ちへ入る。
				# 通常再生には従来の上限を残す。render-only は途中で切れたWAVを
				# 完成品にしないため全チャンクを対象にし、各チャンク境界で前景音声へ譲る。
				PRE_MAX_CHUNKS="${#_pre_chunks[@]}"
				if [ "$RENDER_ONLY" != "true" ]; then
					case "${SOURCE_LABEL:-}" in
					radio_render:* | radio | radio:*)
						PRE_RADIO_CHUNK_CAP="${VOICEVOX_RADIO_MAX_CHUNKS:-12}"
						case "$PRE_RADIO_CHUNK_CAP" in
						'' | *[!0-9]*) PRE_RADIO_CHUNK_CAP=12 ;;
						esac
						[ "$PRE_RADIO_CHUNK_CAP" -lt 1 ] && PRE_RADIO_CHUNK_CAP=1
						[ "$PRE_MAX_CHUNKS" -gt "$PRE_RADIO_CHUNK_CAP" ] && PRE_MAX_CHUNKS="$PRE_RADIO_CHUNK_CAP"
						;;
					esac
				fi
				if [ "$PRE_MAX_CHUNKS" -lt "${#_pre_chunks[@]}" ]; then
					_log "テキスト分割: ${#_pre_chunks[@]}チャンク → ラジオ上限${PRE_MAX_CHUNKS}で事前合成（超過分は再生時フォールバック）"
				else
					_log "テキスト分割: ${#_pre_chunks[@]}チャンク → 全チャンク事前合成"
				fi
				docich_cc_start_plan "${_pre_chunks[@]:0:PRE_MAX_CHUNKS}" || true
				_stream_dir="$QUEUE_DIR/stream_${MY_TOKEN}"
				RENDER_PARTS_DIR=""
				if [ "$RENDER_ONLY" = "true" ] && _voicevox_synth_is_background_render; then
					# コメント優先で中断しても合成済みチャンクを捨てないため、
					# ラジオ render のチャンクは render 出力に紐づく固定パスへ置く。
					# 本文・チャンク数が変わったら世代不一致として作り直す。
					_render_parts_key=$(basename "$RENDER_OUTPUT")
					_render_parts_key="${_render_parts_key%%.*}"
					case "$_render_parts_key" in
					'' | *[!A-Za-z0-9_-]*) _render_parts_key="" ;;
					esac
					if [ -n "$_render_parts_key" ]; then
						RENDER_PARTS_DIR="$QUEUE_DIR/render_${_render_parts_key}"
						_stream_dir="$RENDER_PARTS_DIR"
						_render_parts_stamp="$(_hash_content_file "$MY_CONTENT") ${PRE_MAX_CHUNKS}"
						if [ -d "$RENDER_PARTS_DIR" ] \
							&& [ "$(cat "$RENDER_PARTS_DIR/source_stamp" 2>/dev/null || true)" != "$_render_parts_stamp" ]; then
							_log "部分レンダーの世代不一致 → 破棄して作り直し: $RENDER_PARTS_DIR"
							rm -rf "$RENDER_PARTS_DIR" 2>/dev/null || true
						fi
						mkdir -p "$RENDER_PARTS_DIR" 2>/dev/null || true
						printf '%s\n' "$_render_parts_stamp" >"$RENDER_PARTS_DIR/source_stamp" 2>/dev/null || true
					fi
				fi
				mkdir -p "$_stream_dir"
				PRE_SYNTH_PLAYLIST_FILE="${MY_CONTENT%.txt}_wav_playlist.txt"
				: >"$PRE_SYNTH_PLAYLIST_FILE"
				_pre_synth_failed=0
				_pre_synth_yielded=0
				_pre_chunk_rc=0
				for ((_pc_i = 0; _pc_i < PRE_MAX_CHUNKS; _pc_i++)); do
					# コメントが溜まっていたら、次のチャンクへ進まずここで打ち切る。
					if _radio_render_should_abort_for_comment; then
						_log "コメント優先: ラジオ事前合成を中断 (チャンク$((_pc_i + 1))/${#_pre_chunks[@]} 開始前)"
						_pre_synth_failed=1
						_pre_synth_yielded=1
						break
					fi
					# チャンク間で合成ロックを一時解放し、コメント（長め待ち）が
					# 割り込めるようにする。ラジオ render は前景音声の完了を待ち、
					# 同じプロセス・同じレンダー世代で残りのチャンクを継続する。
					if [ "$_pc_i" -gt 0 ]; then
						_release_voicevox_synth_lock
						if ! _acquire_voicevox_synth_lock "$(_voicevox_synth_lock_wait_sec)"; then
							if [ "${VOICEVOX_SYNTH_LOCK_BUSY_REASON:-}" = "priority_waiter" ]; then
								_log "優先音声へ合成順を譲る (チャンク$((_pc_i + 1))) → 再生時フォールバック"
								_pre_synth_yielded=1
							else
								_log "チャンク間ロック再取得失敗 (チャンク$((_pc_i + 1))) → 再生時にフォールバック"
							fi
							_pre_synth_failed=1
							break
						fi
					fi
					# The playback list may later be read from beside the content copy,
					# while deferred bundles intentionally use paths relative to the
					# bundle.  Store locally synthesized chunks as absolute paths so the
					# two playlist formats cannot be confused.
					_pre_chunk_wav="$(pwd)/$_stream_dir/chunk_${_pc_i}.wav"
					# 前回の中断までに合成できていたチャンクはそのまま使う
					# （毎回チャンク0からやり直すと ready.wav に到達できない）。
					if [ -n "${RENDER_PARTS_DIR:-}" ] && [ -s "$_pre_chunk_wav" ]; then
						printf '%s\n' "$_pre_chunk_wav" >>"$PRE_SYNTH_PLAYLIST_FILE"
						_log "事前合成を再利用 (チャンク$((_pc_i + 1))/${#_pre_chunks[@]}): $_pre_chunk_wav"
						continue
					fi
					_touch_voicevox_synth_lock_heartbeat
					_synthesize_chunk_yielding "${_pre_chunks[$_pc_i]}" "$_pre_chunk_wav"
					_pre_chunk_rc=$?
					if [ "$_pre_chunk_rc" -eq 0 ]; then
						printf '%s\n' "$_pre_chunk_wav" >>"$PRE_SYNTH_PLAYLIST_FILE"
						_log "事前合成完了 (チャンク$((_pc_i + 1))/${#_pre_chunks[@]}): $_pre_chunk_wav"
					elif [ "$_pre_chunk_rc" -eq 9 ]; then
						_log "コメント優先: ラジオ事前合成を合成中に中断 (チャンク$((_pc_i + 1))/${#_pre_chunks[@]})"
						_pre_synth_failed=1
						_pre_synth_yielded=1
						break
					else
						_log "事前合成失敗 (チャンク$((_pc_i + 1))/${#_pre_chunks[@]}) → 再生時にフォールバック"
						rm -f "$_pre_chunk_wav" 2>/dev/null || true
						_pre_synth_failed=1
						break
					fi
				done
				if [ "$_pre_synth_failed" -eq 0 ] && [ -s "$PRE_SYNTH_PLAYLIST_FILE" ]; then
					PRE_SYNTH_WAV="$_stream_dir/chunk_0.wav"
					_log "全チャンク事前合成完了: $PRE_SYNTH_PLAYLIST_FILE"
				else
					rm -f "$PRE_SYNTH_PLAYLIST_FILE" 2>/dev/null
					if [ -n "${RENDER_PARTS_DIR:-}" ] && [ "$_stream_dir" = "${RENDER_PARTS_DIR:-}" ]; then
						# 中断・失敗しても合成済みチャンクは残し、次回の再開に使う。
						_log "合成済みチャンクを保持（次回再開用）: $RENDER_PARTS_DIR"
					else
						rm -rf "$_stream_dir" 2>/dev/null
					fi
					PRE_SYNTH_WAV=""
					PRE_SYNTH_PLAYLIST_FILE=""
				fi
			fi
		fi
		kill "$_pre_synth_hb_pid" 2>/dev/null
		wait "$_pre_synth_hb_pid" 2>/dev/null
		_release_voicevox_synth_lock
	fi
fi

if [ "$RENDER_ONLY" = "true" ]; then
	mkdir -p "$(dirname "$RENDER_OUTPUT")" 2>/dev/null || true
	_render_bundle="${RENDER_OUTPUT}.bundle"
	_render_captions_file="${MY_CONTENT%.txt}_render_chunks.txt"
	_render_playlist_file="${PRE_SYNTH_PLAYLIST_FILE:-}"
	: >"$_render_captions_file"
	for _render_chunk in "${_pre_chunks[@]}"; do
		[ -n "$_render_chunk" ] && printf '%s\n' "$_render_chunk" >>"$_render_captions_file"
	done
	if [ -z "$_render_playlist_file" ] && [ -n "${PRE_SYNTH_WAV:-}" ] && [ -s "$PRE_SYNTH_WAV" ]; then
		_render_playlist_file="${MY_CONTENT%.txt}_render_playlist.txt"
		# bundle はプレイリストのディレクトリ基準で WAV を解決するため、
		# 相対パス（tmp/.say_queue/...）のまま書くと二重にパスが付いて失敗する。
		case "$PRE_SYNTH_WAV" in
		/*) printf '%s\n' "$PRE_SYNTH_WAV" >"$_render_playlist_file" ;;
		*) printf '%s\n' "$(pwd)/$PRE_SYNTH_WAV" >"$_render_playlist_file" ;;
		esac
	fi
	if [ -n "$_render_playlist_file" ] && [ -s "$_render_playlist_file" ] \
		&& _export_prerendered_voicevox_bundle "$_render_playlist_file" "$_render_captions_file" "$_render_bundle"; then
		if [ -n "${PRE_SYNTH_PLAYLIST_FILE:-}" ] && [ -s "$PRE_SYNTH_PLAYLIST_FILE" ]; then
			_concat_prerendered_voicevox_chunks "$PRE_SYNTH_PLAYLIST_FILE" "$RENDER_OUTPUT" || true
		elif [ -n "${PRE_SYNTH_WAV:-}" ] && [ -s "$PRE_SYNTH_WAV" ]; then
			cp "$PRE_SYNTH_WAV" "$RENDER_OUTPUT" 2>/dev/null || true
		fi
		if [ -s "$RENDER_OUTPUT" ]; then
			_log "render-only 完了: $RENDER_OUTPUT (bundle=${_render_bundle})"
			[ -n "${RENDER_PARTS_DIR:-}" ] && rm -rf "$RENDER_PARTS_DIR" 2>/dev/null
			exit 0
		fi
	fi
	rm -f "$RENDER_OUTPUT" 2>/dev/null || true
	rm -rf "$_render_bundle" 2>/dev/null || true
	if [ "${_pre_synth_yielded:-0}" -eq 1 ] || [ "${VOICEVOX_SYNTH_LOCK_BUSY_REASON:-}" = "priority_waiter" ]; then
		_log "render-only 一時保留（コメント優先）"
		exit 75
	fi
	_log "render-only 失敗"
	exit 1
fi

# --- mkdirロックで排他制御 ---
PRE_DELAY="${3:-60}"
_prepare_playback_turn "$PRE_DELAY"

# speaking状態に入る（Twitch広告スヌーズ用、render-only では発火しない）
if [ "${RENDER_ONLY:-false}" != "true" ]; then
	_speaking_enter "tts:$(basename "$MY_CONTENT" 2>/dev/null | cut -c1-20)" 2>/dev/null || true
fi
# --- ロック内: say再生（単発 + 自動リトライ / 事前合成済みチャンク） ---
PLAYBACK_FAILED=0
LAST_SAY_PID=""
if [ "$WAV_PLAYLIST_MODE" = "true" ]; then
	# 外部bundleは再試行に再利用するため、個々のWAVをここでは削除しない。
	if ! SAY_PRESERVE_PRERENDERED_CHUNKS=1 _play_prerendered_voicevox_chunks "$WAV_PLAYLIST_FILE"; then
		PLAYBACK_FAILED=1
	fi
elif [ "$STREAM_VOICEVOX_CHUNKS" -eq 1 ]; then
	# コメント長文: 先頭チャンクの再生中に後続チャンクを合成する。
	if [ -n "${SAY_CC_TEXT:-}" ]; then
		enqueue_chat_message "$SAY_CC_TEXT" "say_enqueue"
	fi
	if ! _stream_voicevox_chunks; then
		PLAYBACK_FAILED=1
	fi
elif [ -n "${PRE_SYNTH_PLAYLIST_FILE:-}" ] && [ -s "$PRE_SYNTH_PLAYLIST_FILE" ]; then
	# 事前合成済みチャンク再生: ロック内では生成しない
	# CC表記をTwitchチャットに投稿
	if [ -n "${SAY_CC_TEXT:-}" ]; then
		enqueue_chat_message "$SAY_CC_TEXT" "say_enqueue"
	fi
	if ! _play_prerendered_voicevox_chunks "$PRE_SYNTH_PLAYLIST_FILE"; then
		PLAYBACK_FAILED=1
	fi
elif ! _play_with_retry; then
	PLAYBACK_FAILED=1
fi

# ロック解放（say完了後）
_release_lock

if [ "$PLAYBACK_FAILED" -eq 1 ]; then
	_log "say終了 (一部失敗あり)"
	_append_played_log "failed"
else
	_log "say終了"
	_append_played_log "played"
fi
# 自分のPIDの場合のみ削除（他プロセスが上書きした場合は残す）
[ -n "$LAST_SAY_PID" ] && [ "$(cat "$PID_FILE" 2>/dev/null)" = "$LAST_SAY_PID" ] && rm -f "$PID_FILE"
exit "$PLAYBACK_FAILED"
