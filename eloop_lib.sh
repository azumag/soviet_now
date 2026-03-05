#!/bin/bash
# eloop_lib.sh - Soren Evolution Loop 共通ライブラリ
#
# soren_loop.sh から source される。AI による書き換え対象外の安定レイヤー。
# 各モジュールは lib/ 配下に分割されている。
#
# モジュール一覧:
#   lib/eloop_config.sh      — 定数定義
#   lib/eloop_core.sh        — コアヘルパー (log, wait, commands)
#   lib/eloop_ai.sh          — AI実行・スピナー・プロンプト構築
#   lib/eloop_sandbox.sh     — バリデーション・サンドボックス
#   lib/eloop_version.sh     — バージョン管理
#   lib/eloop_radio.sh       — ラジオトーク・オーディオ管理
#   lib/eloop_comment.sh     — コメントプレイヤー・ウォッチャー・生成
#   lib/eloop_improve.sh     — 改善ステート管理
#   lib/eloop_regression.sh  — ローリングスコア・リグレッション検知
#   lib/eloop_cleanup.sh     — プロセス管理・クリーンアップ

# --- スクリプトディレクトリ ---
ELOOP_LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ELOOP_LIB_DIR"

source "$ELOOP_LIB_DIR/lib/eloop_config.sh"
source "$ELOOP_LIB_DIR/lib/eloop_core.sh"
source "$ELOOP_LIB_DIR/lib/eloop_ai.sh"
source "$ELOOP_LIB_DIR/lib/eloop_sandbox.sh"
source "$ELOOP_LIB_DIR/lib/eloop_version.sh"
source "$ELOOP_LIB_DIR/lib/eloop_radio.sh"
source "$ELOOP_LIB_DIR/lib/eloop_comment.sh"
source "$ELOOP_LIB_DIR/lib/eloop_improve.sh"
source "$ELOOP_LIB_DIR/lib/eloop_regression.sh"
source "$ELOOP_LIB_DIR/lib/eloop_cleanup.sh"
