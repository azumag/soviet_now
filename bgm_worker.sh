#!/bin/bash
# bgm_worker.sh - CLIゲーム中のBGM再生 (game-aware BGM loop)。
#
# soren本編にはゲーム内BGMがあるが、docichのCLIゲーム (robots/gnurobots/
# nsnake等) の画面ではChromiumが死んでいるため無音になる。このworkerは
# docich canonicalのactive gameを見て、CLIゲーム表示中だけsorenのBGM音源を
# ストリーム用sinkへループ再生する。soren本編中・docich停止中は停止する
# (soren側のBGMと二重にならない)。
#
# 検出: DOCICH_CANONICAL (既定 /home/ubuntu/docich/run-soren-live/game_switch.json)
# 音源: SOREN_BGM_FILE (既定 sorenのインターナショナル.ogg)
# 出力: SOREN_BGM_SINK (既定 soren_null。ffmpegが.monitorを配信へ載せる)
# 音量: SOREN_BGM_VOLUME (既定25。読み上げの下に敷く控えめな音量)
# 起動: systemd soren-bgm.service (Restart)。supervisor管理外のため既存workerに触れない。

CANONICAL="${DOCICH_CANONICAL:-/home/ubuntu/docich/run-soren-live/game_switch.json}"
BGM_FILE="${SOREN_BGM_FILE:-/home/ubuntu/soren/sorengame/assets/BGM/インターナショナル.ogg}"
SINK="${SOREN_BGM_SINK:-soren_null}"
VOL="${SOREN_BGM_VOLUME:-25}"
TAG="soren-bgm-loop"

cli_game_active() {
	[ -f "$CANONICAL" ] || return 1
	[ -f "$BGM_FILE" ] || return 1
	local game=""
	game=$(python3 -c "import json,sys; d=json.load(open('$CANONICAL')); a=d.get('active') or {}; print(a.get('game') or '')" 2>/dev/null)
	[ -n "$game" ]
}

while :; do
	if cli_game_active; then
		if ! pgrep -f "$TAG" >/dev/null 2>&1; then
			PULSE_SINK="$SINK" ffplay -nodisp -loop 0 -volume "$VOL" -window_title "$TAG" "$BGM_FILE" >/dev/null 2>&1 &
		fi
	else
		pkill -f "$TAG" 2>/dev/null || true
	fi
	sleep 10
done
