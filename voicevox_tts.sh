#!/bin/bash
# voicevox_tts.sh - docich 正典への薄いブリッジ (C4 / docs/common_parts_tts_c4.md)
#
# 合成の正典は docich 側 src/docich/speech.py (CLI: docich voicevox synth)。
# 本スクリプトは既存の呼び出し契約 (argv/env/rc) を維持したまま docich へ委譲する。
# docich が無い環境では明確なエラーで停止する (二重管理を避けるため旧実装は保持しない。
# 旧実装は git 履歴に残る)。
#
# Usage (docich 委譲前と同一):
#   ./voicevox_tts.sh --speakers          # 話者一覧表示
#   ./voicevox_tts.sh --test              # テスト音声生成+再生
#   ./voicevox_tts.sh "テキスト"          # テキストを音声合成+再生
#   ./voicevox_tts.sh -o out.wav "テキスト"  # ファイル出力
#   ./voicevox_tts.sh -o out.wav -f file     # ファイルから読み込み
#   ./voicevox_tts.sh -f file                # 合成+再生 (出力 /tmp/voicevox_$$.wav)
#
# 環境変数: VOICEVOX_URL / VOICEVOX_SPEAKER / VOICEVOX_PITCH / VOICEVOX_TEMPO /
#   VOICEVOX_INTONATION / VOICEVOX_MAX_CHARS / VOICEVOX_TIMEOUT は docich 側
#   SpeechConfig.from_env が同じ名前で読む。読み替え辞書は
#   config/voicevox_word_replace.txt を既定で渡す (VOICEVOX_WORD_REPLACE_FILE で上書き可)。

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ヘルプは docich なしで表示できる (旧実装と同一動作)
if [ "${1:-}" = "--help" ] || [ "${1:-}" = "-h" ] || [ -z "${1:-}" ]; then
	echo "Usage:"
	echo "  $0 --speakers           話者一覧表示"
	echo "  $0 --test               テスト音声生成+再生"
	echo "  $0 \"テキスト\"            音声合成+再生"
	echo "  $0 -o out.wav \"テキスト\"  ファイル出力"
	echo "  $0 -o out.wav -f file    ファイルから読み込み"
	echo "  $0 -f file               合成+再生"
	echo ""
	echo "Environment variables:"
	echo "  VOICEVOX_URL      (default: http://127.0.0.1:50021)"
	echo "  VOICEVOX_SPEAKER  (default: 3 = ずんだもん ノーマル)"
	echo "  DOCICH_BIN        docich 実行ファイルのパス (既定: PATH から探索)"
	exit 0
fi

# 旧実装と同様に .env 由来の VOICEVOX 設定を読む
[ -z "${VOICEVOX_URL:-}" ] && [ -f "$SCRIPT_DIR/.env" ] && . "$SCRIPT_DIR/.env"

DOCICH_BIN="${DOCICH_BIN:-}"
if [ -z "$DOCICH_BIN" ]; then
	DOCICH_BIN="$(command -v docich 2>/dev/null || true)"
fi
if [ -z "$DOCICH_BIN" ]; then
	echo "ERROR: docich が見つかりません (C4 ラッパ)。DOCICH_BIN を設定するか PATH に docich を入れてください" >&2
	exit 1
fi

export VOICEVOX_WORD_REPLACE_FILE="${VOICEVOX_WORD_REPLACE_FILE:-${SCRIPT_DIR}/config/voicevox_word_replace.txt}"

_play_and_cleanup() {
	local output="$1"
	afplay -d "${SAY_AUDIO_DEVICE:-}" "$output"
	rm -f "$output"
}

case "${1:-}" in
	--speakers)
		exec "$DOCICH_BIN" voicevox speakers
		;;
	--test)
		tmp_file="$(mktemp)"
		printf 'テスト音声です。VOICEVOXが正常に動作しています。\n' >"$tmp_file"
		output="/tmp/voicevox_test.wav"
		"$DOCICH_BIN" voicevox synth -f "$tmp_file" -o "$output"
		rm -f "$tmp_file"
		echo "Playing: $output"
		_play_and_cleanup "$output"
		;;
	-o)
		output="$2"
		shift 2
		if [ "${1:-}" = "-f" ]; then
			"$DOCICH_BIN" voicevox synth -f "$2" -o "$output"
		else
			tmp_file="$(mktemp)"
			printf '%s\n' "$*" >"$tmp_file"
			"$DOCICH_BIN" voicevox synth -f "$tmp_file" -o "$output"
			rm -f "$tmp_file"
		fi
		echo "Saved: $output"
		;;
	-f)
		output="/tmp/voicevox_$$.wav"
		"$DOCICH_BIN" voicevox synth -f "$2" -o "$output"
		_play_and_cleanup "$output"
		;;
	*)
		tmp_file="$(mktemp)"
		printf '%s\n' "$*" >"$tmp_file"
		output="/tmp/voicevox_$$.wav"
		"$DOCICH_BIN" voicevox synth -f "$tmp_file" -o "$output"
		rm -f "$tmp_file"
		_play_and_cleanup "$output"
		;;
esac
