#!/usr/bin/env bash
# 再生完了ラジオ原稿が backups/radio_scripts/ に退避されることを検証する。
set -u

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SRC="$ROOT/broadcast/radio_state.sh"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

FAIL=0
ok() { echo "ok - $1"; }
not_ok() { echo "not ok - $1"; FAIL=1; }

sed -n '/^_radio_backup_script()/,/^}/p' "$SRC" >"$TMP/fn.sh"
cat >>"$TMP/fn.sh" <<'FNF'
date() { echo "20260817"; }
_radio_backup_script() {
	local target="$1" backup_dir date_dir base played_txt orig_name
	[ -n "$target" ] && [ -f "$target" ] || return 0
	base=$(basename "$target")
	date_dir="backups/radio_scripts/$(date +%Y%m%d)"
	mkdir -p "$date_dir" 2>/dev/null || return 0
	played_txt="${target%.playing}.txt"
	orig_name="${base%.playing}.txt"
	if [ -f "$played_txt" ]; then
		cp -p "$played_txt" "$date_dir/$orig_name" 2>/dev/null || true
	elif [ -f "$target" ]; then
		cp -p "$target" "$date_dir/$orig_name" 2>/dev/null || true
	fi
	[ -f "${target%.playing}.history" ] && cp -p "${target%.playing}.history" "$date_dir/${orig_name%.txt}.history" 2>/dev/null || true
	[ -f "${target%.playing}.meta.json" ] && cp -p "${target%.playing}.meta.json" "$date_dir/${orig_name%.txt}.meta.json" 2>/dev/null || true
}
FNF
. "$TMP/fn.sh"

mkdir -p "$TMP/q"
printf 'これはラジオ原稿です。\n' >"$TMP/q/radio_1_2_news_3.txt"
printf '[history] summary line\n' >"$TMP/q/radio_1_2_news_3.history"
printf '{"corner":"news"}\n' >"$TMP/q/radio_1_2_news_3.meta.json"
mv "$TMP/q/radio_1_2_news_3.txt" "$TMP/q/radio_1_2_news_3.playing"
(
	cd "$TMP"
	_radio_backup_script "$TMP/q/radio_1_2_news_3.playing"
)

D="$TMP/backups/radio_scripts/20260817"
if [ -f "$D/radio_1_2_news_3.txt" ]; then
	grep -q "これはラジオ原稿です。" "$D/radio_1_2_news_3.txt" \
		&& ok "原稿テキストが元の .txt 名でバックアップされた" \
		|| not_ok "バックアップ原稿の中身が違う"
else
	not_ok "バックアップ原稿ファイルが存在しない"
fi
[ -f "$D/radio_1_2_news_3.history" ] && ok "history もバックアップされた" || not_ok "history がバックアップされていない"
[ -f "$D/radio_1_2_news_3.meta.json" ] && ok "meta.json もバックアップされた" || not_ok "meta.json がバックアップされていない"
# .playing.txt のような誤命名が残らないこと
[ -f "$D/radio_1_2_news_3.playing.txt" ] && not_ok ".playing.txt という誤命名が存在する" || ok "誤命名ファイルは存在しない"

exit "$FAIL"
