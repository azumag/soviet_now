#!/bin/bash
# eloop_lib.sh - 全モジュールをsourceするshim
#
# soren_loop.sh から source される。AI による書き換え対象外の安定レイヤー。
# 各機能は core/, strategy/, broadcast/, infra/ のモジュールに分割されている。

ELOOP_LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ELOOP_LIB_DIR"
unset ANTHROPIC_AUTH_TOKEN

# Layer 0: 定数・初期化
source "$ELOOP_LIB_DIR/core/config.sh"
# Layer 1: コアヘルパー
source "$ELOOP_LIB_DIR/core/helpers.sh"
source "$ELOOP_LIB_DIR/core/game_state.sh"
# Layer 2: 戦略インフラ
source "$ELOOP_LIB_DIR/strategy/ai.sh"
source "$ELOOP_LIB_DIR/strategy/sandbox.sh"
source "$ELOOP_LIB_DIR/core/version.sh"
source "$ELOOP_LIB_DIR/core/phyrogenetic.sh"
source "$ELOOP_LIB_DIR/strategy/improve.sh"
source "$ELOOP_LIB_DIR/strategy/regression.sh"
# Layer 3: 放送系
source "$ELOOP_LIB_DIR/broadcast/radio_state.sh"
source "$ELOOP_LIB_DIR/broadcast/radio_engine.sh"
source "$ELOOP_LIB_DIR/broadcast/radio_persona.sh"
source "$ELOOP_LIB_DIR/broadcast/radio_themes.sh"
source "$ELOOP_LIB_DIR/broadcast/radio_news.sh"
source "$ELOOP_LIB_DIR/broadcast/radio_factcheck.sh"
source "$ELOOP_LIB_DIR/broadcast/radio_corners.sh"
source "$ELOOP_LIB_DIR/broadcast/radio_celebration.sh"
source "$ELOOP_LIB_DIR/broadcast/comment.sh"
source "$ELOOP_LIB_DIR/broadcast/comment_worker.sh"
source "$ELOOP_LIB_DIR/broadcast/scheduler.sh"
# Layer 4: インフラ
source "$ELOOP_LIB_DIR/infra/cleanup.sh"
