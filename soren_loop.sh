#!/bin/bash
# soren_loop.sh - Soren Evolution Loop 親スクリプト
#
# ユーザーが実行するエントリーポイント。安定した薄いラッパー。
# eloop.sh が AI 改善で書き換わっても、このスクリプトは生き残り、
# 次のイテレーションで自動的に新しいコードを読み込む。
#
# アーキテクチャ:
#   soren_loop.sh (このファイル) — メインループ、初期化、クリーンアップ
#   eloop_lib.sh  — 共通ライブラリ (ヘルパー/ラジオ/コメント/AI/バリデーション)
#   eloop.sh      — 1試合のゲームプレイ関数 (毎試合 source で最新版を読み込み)
#   eloop_improve.sh — バックグラウンド改善サブプロセス

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# --- 多重起動防止 ---
LOCKFILE="tmp/soren_loop.lock"
mkdir -p tmp
if [ -f "$LOCKFILE" ]; then
	old_pid=$(cat "$LOCKFILE" 2>/dev/null)
	if [ -n "$old_pid" ] && kill -0 "$old_pid" 2>/dev/null; then
		echo "ERROR: soren_loop.sh is already running (PID=$old_pid). Aborting."
		exit 1
	fi
fi
echo $$ > "$LOCKFILE"

# --- 共通ライブラリ読み込み ---
source ./eloop_lib.sh

# --- グローバル状態 ---
GAME_NUM=$(cat "$GAME_COUNT_FILE" 2>/dev/null || echo 0)
IMPROVE_PID=0

# --- 初期化 ---
log "=== Soren Evolution Loop ==="
log "strategy.py → 1game → adaptive improve → repeat"

# Twitchチャットデーモン起動
./twitch_chat.sh start azumagbanjo

# クリーンアップ trap
trap 'cleanup_all; exit' EXIT INT TERM

# 前回の孤児コメントプレイヤーを掃除してから起動
stop_comment_player
start_comment_player

# 前回中断時のリカバリ
recover_strategy_backup

# 初期バリデーション
if [ ! -f "$STRATEGY_FILE" ]; then
	log "ERROR: $STRATEGY_FILE が見つかりません"
	exit 1
fi
if ! validate_strategy; then
	log "ERROR: 初期バリデーション失敗"
	exit 1
fi

# 前回中断した改善プロセスの状態復元
check_and_harvest_improvement

# MOVE状態待ち
wait_for_move || {
	log "ゲームが起動していません"
	exit 1
}

# --- メインループ: 1試合ずつ ---
while true; do
	# eloop_lib.sh / eloop.sh を毎回 source (書き換え時に反映)
	if ! source ./eloop_lib.sh 2>/dev/null; then
		log "WARNING: eloop_lib.sh の読み込みに失敗 (前回の定義で続行)"
	fi
	if ! source ./eloop.sh 2>/dev/null; then
		log "WARNING: eloop.sh の読み込みに失敗 (前回の定義で続行)"
	fi

	# 前回の改善が完了したか確認
	check_and_harvest_improvement

	# ニュース取得 & ニュースコーナー (3ゲームに1回、バックグラウンド)
	if (( GAME_NUM % 3 == 1 )); then
		fetch_and_play_news &
	fi

	# ラジオトーク (3ゲームに1回、バックグラウンド)
	if (( GAME_NUM % 3 == 0 )); then
		start_random_radio_corner &
	fi

	# 1試合プレイ
	play_one_game

	# 後処理 (スコア記録, バージョン保存, git commit 等)
	post_game_bookkeeping

	# アダプティブ改善トリガー
	trigger_adaptive_improvement

	# 次の試合準備
	prepare_next_game

	_maybe_show_joke
	sleep 2
done
