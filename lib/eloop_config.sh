#!/bin/bash
# lib/eloop_config.sh - 定数定義
# ELOOP_LIB_DIR は eloop_lib.sh (shim) で設定済み

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

TMP_STATE_DIR="tmp/state"
TMP_MARKERS_DIR="tmp/markers"
TMP_HISTORY_DIR="tmp/history"
TMP_DEBUG_DIR="tmp/debug"
TMP_CACHE_DIR="tmp/cache"

RADIO_STATE_FILE="$TMP_STATE_DIR/.radio_state"
COMMENT_GEN_STATE_FILE="$TMP_STATE_DIR/.comment_gen_state"
PAST_RADIO_TOPICS="$TMP_HISTORY_DIR/past_radio_topics.txt"
PAST_NEWS_READ="$TMP_HISTORY_DIR/past_news_read.txt"
PAST_NEWS_READ_KEYS="$TMP_HISTORY_DIR/past_news_read_keys.txt"

IMPROVE_STATE_FILE="$TMP_STATE_DIR/improve_state.json"
IMPROVE_AI_LOG_FILE="$TMP_DEBUG_DIR/improve_ai.log"
IMPROVE_AI_LOG_KEEP_LINES=2000
IMPROVE_AI_LOG_TRIM_LINES=4000
ACCUMULATED_GAMES_FILE="$TMP_STATE_DIR/accumulated_games.json"
ROLLING_SCORES_FILE="$TMP_STATE_DIR/rolling_scores.json"
REJECTED_HASHES_FILE="$TMP_HISTORY_DIR/rejected_hashes.txt"
REGRESSION_ROLLBACK_DONE=0
REGRESSION_ROLLBACK_HASH=""
MIN_GAMES_BEFORE_IMPROVE=12
MIN_GAMES_BEFORE_REGRESSION="${MIN_GAMES_BEFORE_REGRESSION:-20}"
RUNTIME_RECOVERY_GATE_FILE="$TMP_STATE_DIR/.runtime_recovery_min_games"
MIN_GAMES_FOR_BEST_ROLLBACK=12
RANK_LCB_Z=1.28
RANK_WEIGHT_P50=0.55
RANK_WEIGHT_P25=0.30
RANK_WEIGHT_LCB=0.15
REGRESSION_COMPOSITE_RATIO=0.82
REGRESSION_P25_RATIO=0.80
REGRESSION_MIN_COMP_GAP="${REGRESSION_MIN_COMP_GAP:-120}"
REGRESSION_MIN_P50_GAP="${REGRESSION_MIN_P50_GAP:-100}"
REGRESSION_MIN_P25_GAP="${REGRESSION_MIN_P25_GAP:-180}"
STRATEGY_HASH_ARCHIVE_DIR="strategy_versions/by_hash"
HASH_ARCHIVE_KEEP_TOP=10
COMMENT_QUEUE_DIR="tmp/.comment_queue"
COMMENT_WATCHER_PID_FILE="tmp/.comment_queue/watcher.pid"
COMMENT_WATCHER_INTERVAL=10
COMMENT_WORKER_HEALTH_TTL=30
COMMENT_PLAYER_HEARTBEAT_FILE="tmp/.comment_queue/player.heartbeat"
COMMENT_WATCHER_HEARTBEAT_FILE="tmp/.comment_queue/watcher.heartbeat"
COMMENT_BATCH_HISTORY_FILE="tmp/.comment_queue/processed_batch_hashes.log"
COMMENT_BATCH_DEDUP_TTL=180

# --- タイムアウト・リトライ ---
WAIT_COMMANDS_TIMEOUT=20         # commands.txt 消化待ち(秒)
WAIT_MOVE_TIMEOUT=60             # MOVE状態待ち(秒)
SEND_RETRY_TIMEOUT=60            # retry後の新ゲーム検知待ち(秒)
STOP_PID_WAIT_TICKS=20           # プロセス停止の最大ポーリング回数

# --- ローリングスコア ---
ROLLING_SCORES_WINDOW=20         # ハッシュあたりの保持スコア数
REJECTED_HASHES_KEEP=20          # リジェクトハッシュ保持数
VERSION_KEEP=10                  # strategy_versions/ の保持数
HALL_OF_FAME_KEEP=10             # hall_of_fame 保持数

# --- ラジオ ---
RADIO_MIN_TALK_LENGTH=100        # トーク最小文字数
PAST_RADIO_TOPICS_KEEP=100       # 過去トピック保持数
PAST_SOVIET_TOPICS_KEEP=100      # 過去ソ連テーマ保持数
PAST_NEWS_READ_KEEP=60           # 既読ニュース保持数
PAST_NEWS_READ_KEYS_KEEP=120     # 既読キー保持数

mkdir -p "$STRATEGY_VERSIONS_DIR" "$STRATEGY_HASH_ARCHIVE_DIR" "$HISTORY_DIR" \
	"$TMP_STATE_DIR" "$TMP_MARKERS_DIR" "$TMP_HISTORY_DIR" "$TMP_DEBUG_DIR" "$TMP_CACHE_DIR" \
	"$COMMENT_QUEUE_DIR" "tmp/.twitch_chat"
