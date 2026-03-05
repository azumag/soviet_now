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
PAST_RADIO_TOPICS="tmp/past_radio_topics.txt"
PAST_NEWS_READ="tmp/.past_news_read.txt"
PAST_NEWS_READ_KEYS="tmp/.past_news_read_keys.txt"

IMPROVE_STATE_FILE="tmp/improve_state.json"
IMPROVE_AI_LOG_FILE="tmp/improve_ai.log"
IMPROVE_AI_LOG_KEEP_LINES=2000
IMPROVE_AI_LOG_TRIM_LINES=4000
ACCUMULATED_GAMES_FILE="tmp/accumulated_games.json"
ROLLING_SCORES_FILE="tmp/rolling_scores.json"
REJECTED_HASHES_FILE="tmp/rejected_hashes.txt"
REGRESSION_ROLLBACK_DONE=0
REGRESSION_ROLLBACK_HASH=""
MIN_GAMES_BEFORE_IMPROVE=12
MIN_GAMES_FOR_BEST_ROLLBACK=12
RANK_LCB_Z=1.28
RANK_WEIGHT_P50=0.55
RANK_WEIGHT_P25=0.30
RANK_WEIGHT_LCB=0.15
REGRESSION_COMPOSITE_RATIO=0.82
REGRESSION_P25_RATIO=0.80
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
mkdir -p "$STRATEGY_VERSIONS_DIR" "$STRATEGY_HASH_ARCHIVE_DIR" "$HISTORY_DIR" "$COMMENT_QUEUE_DIR" "tmp/.twitch_chat" tmp
