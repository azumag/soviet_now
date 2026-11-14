#!/usr/bin/env bash
# tools/ab_retroactive.sh - 直接 merge / stale 退役で判定を逃した変更へ
# 後追いの実戦 A/B 比較を付ける (soren ルートで実行)。本体は strategy/ab_retroactive.sh。
#
#   list                          tmp/history の stale 退役実験を一覧
#   show <ts>                     退役実験の詳細と推奨コマンド
#   prev-roots [n] [--repo dir]   git 履歴から直近 n 個の root decide-hash を解決
#   start --b <path|hash> [--pattern P] [--note "..."]
#                                 現 root を A、指定を B として A/B を開始
#   start-stale <ts> [--pattern P] 退役実験の alt file を B として A/B を開始
set +u  # eloop の関数は未定義変数を参照しうるので -u は使わない (ab_ctl.sh と同じ)
cd "$(dirname "$0")/.." || exit 1
source ./eloop_lib.sh >/dev/null 2>&1 || { echo "eloop_lib.sh を読めません"; exit 1; }
[ -f strategy/ab_retroactive.sh ] && source ./strategy/ab_retroactive.sh

cmd="${1:-list}"
case "$cmd" in
list)
	_ab_retro_list
	;;
show)
	ts="${2:-}"
	[ -n "$ts" ] || { echo "usage: $0 show <ts>"; exit 1; }
	_ab_retro_show "$ts"
	;;
prev-roots)
	shift
	_ab_retro_prev_roots "$@"
	;;
start)
	shift
	src=""; pattern="ABBA"; note=""
	while [ $# -gt 0 ]; do
		case "$1" in
		--b) src="${2:-}"; shift 2 ;;
		--pattern) pattern="${2:-ABBA}"; shift 2 ;;
		--note) note="${2:-}"; shift 2 ;;
		*) src="${1:-$src}"; shift ;;
		esac
	done
	[ -n "$src" ] || { echo "usage: $0 start --b <path|hash> [--pattern P] [--note ...]"; exit 1; }
	_ab_retro_start "$src" "$pattern" "$note" "" ""
	;;
start-stale)
	ts="${2:-}"
	pattern="${4:-ABBA}"
	case "$3" in --pattern) ;; *) pattern="ABBA" ;; esac
	[ -n "$ts" ] || { echo "usage: $0 start-stale <ts> [--pattern P]"; exit 1; }
	_ab_retro_start_stale "$ts" "$pattern"
	;;
*)
	echo "usage: $0 list | show <ts> | prev-roots [n] [--repo dir] | start --b <path|hash> [--pattern P] [--note ...] | start-stale <ts> [--pattern P]"
	exit 1
	;;
esac
