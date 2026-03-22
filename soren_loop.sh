#!/bin/bash
# soren_loop.sh - Soren Evolution Loop 親スクリプト
#
# ユーザーが実行するエントリーポイント。安定した薄いラッパー。
# eloop.sh が AI 改善で書き換わっても、このスクリプトは生き残り、
# 次のイテレーションで自動的に新しいコードを読み込む。
#
# アーキテクチャ:
#   soren_loop.sh (このファイル) — メインループ、初期化、クリーンアップ
#   eloop_lib.sh  — 全モジュールsource shim (core/, strategy/, broadcast/, infra/)
#   eloop.sh      — 1試合のゲームプレイ関数 (毎試合 source で最新版を読み込み)
#   eloop_improve.sh — バックグラウンド改善サブプロセス

# 親プロセスから SIGINT/SIGTERM 無視状態を継承していると Ctrl-C が効かない。
# その場合でも確実に停止できるよう、起動直後にシグナル既定動作へ戻して再execする。
if [ -z "${SOREN_SIGRESET_DONE:-}" ]; then
	export SOREN_SIGRESET_DONE=1
	exec python3 - "$0" "$@" <<'PY'
import os
import signal
import sys

targets = {signal.SIGINT, signal.SIGTERM}
if hasattr(signal, "SIGQUIT"):
    targets.add(signal.SIGQUIT)

# 1) 無視ハンドラ継承を解除
for sig in targets:
    try:
        signal.signal(sig, signal.SIG_DFL)
    except Exception:
        pass

# 2) ブロックされたシグナルマスクも解除（Ctrl-Cが届かないケース対策）
try:
    signal.pthread_sigmask(signal.SIG_UNBLOCK, targets)
except Exception:
    pass

os.execv("/bin/bash", ["/bin/bash", sys.argv[1], *sys.argv[2:]])
PY
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# 端末起動時は Ctrl-C キー定義を標準化し、届かない状況を明示する
if [ -t 0 ]; then
	# screen/tmux配下などで ^C が文字入力扱いになるケースを防ぐ
	stty intr '^C' isig 2>/dev/null || true
	_self_pgid=$(ps -p $$ -o pgid= 2>/dev/null | tr -d ' ')
	_tty_pgid=$(ps -p $$ -o tpgid= 2>/dev/null | tr -d ' ')
	if [ -n "$_self_pgid" ] && [ -n "$_tty_pgid" ] && [ "$_self_pgid" != "$_tty_pgid" ]; then
		echo "[WARN] この端末では soren_loop が前面PGではないため Ctrl-C は届きません。" >&2
		echo "[WARN] soren_loop を実行した端末に戻るか、fg で前面に戻して停止してください。" >&2
	fi
fi

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
rm -f tmp/stop

# --- 環境変数読み込み ---
[ -f .env ] && set -a && . ./.env && set +a

# --- 共通ライブラリ読み込み ---
source ./eloop_lib.sh

# --- グローバル状態 ---
GAME_NUM=$(cat "$GAME_COUNT_FILE" 2>/dev/null || echo 0)
IMPROVE_PID=0
HALT_STRATEGY_AFTER_SOVIET=0
STOP_REQUESTED=0
_SOREN_CLEANED_UP=0

_cleanup_once() {
	local reason="${1:-unknown}"
	if [ "${_SOREN_CLEANED_UP:-0}" -eq 1 ]; then
		return 0
	fi
	_SOREN_CLEANED_UP=1
	cleanup_all "$reason"
}

_handle_stop_signal() {
	local sig="${1:-INT}"
	STOP_REQUESTED=1
	rm -f tmp/stop
	log "[SIGNAL] ${sig} を受信: 停止処理に入ります"
	trap - INT TERM
	_cleanup_once "signal:${sig}"
	trap - EXIT
	exit 130
}

_handle_exit() {
	local rc=$?
	if [ "${STOP_REQUESTED:-0}" -eq 1 ]; then
		return 0
	fi
	_cleanup_once "exit:${rc}"
}

_abort_if_interrupted() {
	local rc="${1:-0}"
	local stage="${2:-unknown}"
	if [ "${STOP_REQUESTED:-0}" -eq 1 ] || [ "$rc" -eq 130 ] || [ "$rc" -eq 143 ]; then
		log "[SIGNAL] ${stage} で割り込みを検出 (rc=${rc})"
		exit 130
	fi
	return 0
}

# --- 初期化 ---
log "=== Soren Evolution Loop ==="
log "strategy.py → 1game → adaptive improve → repeat"

# Twitchチャットデーモン起動
./twitch_chat.sh start azumagbanjo

# クリーンアップ trap
trap '_handle_exit' EXIT
trap '_handle_stop_signal INT' INT
trap '_handle_stop_signal TERM' TERM

# 前回の孤児コメントプレイヤー/ウォッチャーを掃除してから起動
stop_comment_player
stop_comment_watcher
start_comment_player
start_comment_watcher

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
wait_for_move
wait_rc=$?
_abort_if_interrupted "$wait_rc" "wait_for_move(initial)"
if [ "$wait_rc" -ne 0 ]; then
	log "ゲームが起動していません"
	exit 1
fi

# --- メインループ: 1試合ずつ ---
while true; do
	# stop-file チェック (stop_soren.sh からの停止要求)
	if [ -f tmp/stop ]; then
		log "[STOP] Stop file detected"
		rm -f tmp/stop
		exit 130
	fi

	# .env を毎試合再読込（再起動なしで設定変更を反映）
	[ -f .env ] && set -a && . ./.env && set +a

	# eloop_lib.sh は全モジュールをsourceするshim
	if ! source ./eloop_lib.sh 2>/dev/null; then
		log "WARNING: eloop_lib.sh の読み込みに失敗 (前回の定義で継続)"
	fi
	if ! source ./eloop.sh 2>/dev/null; then
		log "WARNING: eloop.sh の読み込みに失敗 (前回の定義で続行)"
	fi

	# ゲーム番号を毎試合読み直す
	GAME_NUM=$(cat "$GAME_COUNT_FILE" 2>/dev/null || echo 0)

	# 前回の改善が完了したか確認
	check_and_harvest_improvement
	# コメント系ワーカーは壊れたPIDファイルからも自己回復させる
	start_comment_player
	start_comment_watcher
	process_external_audio_triggers "$GAME_NUM" "$(_last_score)"

	# ソ連建国後は strategy 実行を止め、コメント系のみ維持する
	if [ "${HALT_STRATEGY_AFTER_SOVIET:-0}" -eq 1 ]; then
		log "[HALT] strategy停止中: コメント返し/読み上げのみ継続"
		sleep 5
		continue
	fi

	# 改善中は中華AIのゲームプレイを一時停止 (soren91が代わりにプレイ中)
	if command -v manual_meriken_mode_is_enabled >/dev/null 2>&1 && manual_meriken_mode_is_enabled; then
		if command -v soren91_is_running >/dev/null 2>&1 && ! soren91_is_running 2>/dev/null; then
			soren91_start 2>/dev/null || true
		fi
		log "[PAUSE] manual_meriken_mode: ゲームプレイ一時停止 (メリケンAI手動モード)"
		sleep 10
		continue
	fi
	if _is_improve_running; then
		log "[PAUSE] 改善中: ゲームプレイ一時停止 (メリケンAIが代打中)"
		sleep 10
		continue
	fi

	# 改善完了が22時台だった場合: soren91を停止せずメリケンAIタイムに移行
	if [ "${MERIKEN_TIME_PENDING:-0}" -eq 1 ]; then
		MERIKEN_TIME_PENDING=0
		if command -v _soren91_enabled >/dev/null 2>&1 && _soren91_enabled; then
			log "[MERIKEN_TIME] 改善完了→22時台: メリケンAIタイム開始 (1時間)"
			# soren91は既に動いているのでstartは不要。アナウンスのみ
			{
				_mt_file=$(mktemp /tmp/eloop_meriken_time.XXXXXX)
				printf '%s\n' "22時から23時はメリケンAIによるソ連91対戦部門になりました。皆様の挑戦お待ちしております" > "$_mt_file"
				SAY_VOICEVOX_SPEAKER_OVERRIDE="${SOREN91_VOICEVOX_SPEAKER:-46}" SAY_CONTEXT_LABEL="meriken_time:announce" ./say_enqueue.sh "$_mt_file" "$RADIO_SAY_RATE" 0 2>/dev/null || true
				rm -f "$_mt_file"
			} &
			./twitch_chat.sh send "22時から23時はメリケンAIによるソ連91対戦部門になりました。皆様の挑戦お待ちしております 【91人対戦】ソ連ゲーム91 - たアケイク https://unityroom.com/games/sorengame91" 2>/dev/null &
			# 1時間ループ: soren91が動いている間、中華AIは休止
			_meriken_end=$(($(date +%s) + 3600))
			while [ "$(date +%s)" -lt "$_meriken_end" ]; do
				[ -f tmp/stop ] && break
				start_comment_player 2>/dev/null || true
				start_comment_watcher 2>/dev/null || true
				sleep 15
			done
			log "[MERIKEN_TIME] メリケンAIタイム終了"
			soren91_stop 2>/dev/null || true
		fi
	fi

	# 非同期ジョブに渡すため、試合開始時点の値を固定
	SCHEDULE_GAME_NUM="$GAME_NUM"
	SCHEDULE_SCORE=$(_last_score)
	schedule_nonessential_audio_jobs "$SCHEDULE_GAME_NUM" "$SCHEDULE_SCORE"

	# 1試合プレイ
	play_one_game
	play_rc=$?
	_abort_if_interrupted "$play_rc" "play_one_game"
	if [ "$play_rc" -eq "${PLAY_RECOVERED_RETRY_RC:-75}" ]; then
		log "[RECOVERY] decide例外リカバリ済み: この試合の後処理をスキップして次へ"
		sleep 2
		continue
	fi

	# 後処理 (スコア記録, バージョン保存, git commit 等)
	post_game_bookkeeping
	post_rc=$?
	_abort_if_interrupted "$post_rc" "post_game_bookkeeping"

	# 定期 tmp/ クリーンアップ (50ゲームごと)
	if (( GAME_NUM % 50 == 0 )); then
		cleanup_tmp_files
	fi

	# ソ連建国達成後は retry を含む次ゲーム操作を行わない
	if [ "${HALT_STRATEGY_AFTER_SOVIET:-0}" -eq 1 ]; then
		log "[HALT] retry・次ゲーム操作を停止"
		sleep 5
		continue
	fi

	# アダプティブ改善トリガー
	trigger_adaptive_improvement
	improve_rc=$?
	_abort_if_interrupted "$improve_rc" "trigger_adaptive_improvement"

	# 22時台メリケンAIタイム: 改善サイクル区切り（蓄積0かつファイルあり=改善直後）で22時台なら1時間メリケンモード
	# ファイルなし(初回起動)では発火しない。MERIKEN_TIME_PENDINGパスとは別の入口。
	_meriken_acc_count=-1
	if [ -f "$ACCUMULATED_GAMES_FILE" ]; then
		_meriken_acc_count=$(python3 -c "import json; print(json.load(open('$ACCUMULATED_GAMES_FILE')).get('count',0))" 2>/dev/null || echo -1)
	fi
	if [ "${_meriken_acc_count}" -eq 0 ] && [ "$(date +%H)" = "22" ]; then
		if command -v _soren91_enabled >/dev/null 2>&1 && _soren91_enabled; then
			log "[MERIKEN_TIME] サイクル区切り+22時台: メリケンAIタイム開始 (1時間)"
			soren91_start 2>/dev/null || true
			# メリケンAIタイム専用アナウンス (読み上げ + Twitch投稿)
			{
				_mt_file=$(mktemp /tmp/eloop_meriken_time.XXXXXX)
				printf '%s\n' "22時から23時はメリケンAIによるソ連91対戦部門になりました。皆様の挑戦お待ちしております" > "$_mt_file"
				SAY_VOICEVOX_SPEAKER_OVERRIDE="${SOREN91_VOICEVOX_SPEAKER:-46}" SAY_CONTEXT_LABEL="meriken_time:announce" ./say_enqueue.sh "$_mt_file" "$RADIO_SAY_RATE" 0 2>/dev/null || true
				rm -f "$_mt_file"
			} &
			./twitch_chat.sh send "22時から23時はメリケンAIによるソ連91対戦部門になりました。皆様の挑戦お待ちしております 【91人対戦】ソ連ゲーム91 - たアケイク https://unityroom.com/games/sorengame91" 2>/dev/null &
			# 1時間ループ: soren91が動いている間、中華AIは休止
			_meriken_end=$(($(date +%s) + 3600))
			while [ "$(date +%s)" -lt "$_meriken_end" ]; do
				# 外部からの停止要求チェック
				[ -f tmp/stop ] && break
				# コメント系ワーカーは維持
				start_comment_player 2>/dev/null || true
				start_comment_watcher 2>/dev/null || true
				sleep 15
			done
			log "[MERIKEN_TIME] メリケンAIタイム終了"
			soren91_stop 2>/dev/null || true
		fi
	fi

	# 次の試合準備
	prepare_next_game
	next_rc=$?
	_abort_if_interrupted "$next_rc" "prepare_next_game"

	sleep 2
done
