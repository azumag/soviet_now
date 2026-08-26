#!/bin/bash
# eloop_lib.sh - 全モジュールをsourceするshim
#
# soren_loop.sh から source される。AI による書き換え対象外の安定レイヤー。
# 各機能は core/, strategy/, broadcast/, infra/ のモジュールに分割されている。

ELOOP_LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ELOOP_LIB_DIR"

[ -f "$ELOOP_LIB_DIR/.env" ] && set -a && . "$ELOOP_LIB_DIR/.env" && set +a

# Layer 0: 定数・初期化
source "$ELOOP_LIB_DIR/core/config.sh"
source "$ELOOP_LIB_DIR/core/runtime_toggles.sh"
source "$ELOOP_LIB_DIR/lib/outbound_queue.sh"
# Layer 1: コアヘルパー
source "$ELOOP_LIB_DIR/core/helpers.sh"
source "$ELOOP_LIB_DIR/core/game_state.sh"
source "$ELOOP_LIB_DIR/core/strategy_runtime.sh"
# Layer 1.5: AI共通ディスパッチ (helpers.sh に依存)
source "$ELOOP_LIB_DIR/lib/ai_generate.sh"
# Layer 2: 戦略インフラ
source "$ELOOP_LIB_DIR/strategy/ai.sh"
source "$ELOOP_LIB_DIR/strategy/sandbox.sh"
source "$ELOOP_LIB_DIR/core/version.sh"
source "$ELOOP_LIB_DIR/core/phyrogenetic.sh"
source "$ELOOP_LIB_DIR/strategy/ab_interleave.sh"
source "$ELOOP_LIB_DIR/strategy/ab_gate.sh"
source "$ELOOP_LIB_DIR/strategy/improve.sh"
source "$ELOOP_LIB_DIR/strategy/regression.sh"
# Layer 3: 放送系（配信モードのみ。探索モードでは source しない）
# 探索モードでは配信系関数は core/streaming_shim.sh の no-op 定義で代替される。
if [ "${EXPLORE_MODE:-0}" != "1" ]; then
	source "$ELOOP_LIB_DIR/broadcast/radio_state.sh"
	source "$ELOOP_LIB_DIR/broadcast/radio_engine.sh"
	source "$ELOOP_LIB_DIR/broadcast/radio_persona.sh"
	source "$ELOOP_LIB_DIR/broadcast/radio_themes.sh"
	source "$ELOOP_LIB_DIR/broadcast/radio_news.sh"
	source "$ELOOP_LIB_DIR/broadcast/radio_quality.sh"
	source "$ELOOP_LIB_DIR/broadcast/radio_factcheck.sh"
	source "$ELOOP_LIB_DIR/broadcast/radio_corners.sh"
	source "$ELOOP_LIB_DIR/broadcast/radio_celebration.sh"
	source "$ELOOP_LIB_DIR/broadcast/comment.sh"
	source "$ELOOP_LIB_DIR/broadcast/comment_lib.sh"
	source "$ELOOP_LIB_DIR/broadcast/scheduler.sh"
fi
# Layer 4: インフラ
source "$ELOOP_LIB_DIR/infra/cleanup.sh"
[ -f "$ELOOP_LIB_DIR/lib/bridge_recovery.sh" ] && source "$ELOOP_LIB_DIR/lib/bridge_recovery.sh"
# Layer 5: soren91 integration (配信モードのみ)
if [ "${EXPLORE_MODE:-0}" != "1" ]; then
	[ -f "$ELOOP_LIB_DIR/soren91_control.sh" ] && source "$ELOOP_LIB_DIR/soren91_control.sh"
fi
# Layer 6: 探索モード shim (EXPLORE_MODE=1 のとき配信系 sink を no-op 定義)
source "$ELOOP_LIB_DIR/core/streaming_shim.sh"
