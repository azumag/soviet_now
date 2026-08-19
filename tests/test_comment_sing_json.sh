#!/usr/bin/env bash
# Regression: 楽譜 JSON は必ず抽出され、読み上げ本文から除去される。
# 過去のバグ: python3 - <<'PY' (heredoc) が stdin を奪うため、パイプで渡した
# attempt_talk を _extract_sing_score が読めず、JSON がそのまま TTS に流れていた。
set -u

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SRC="$ROOT/broadcast/comment.sh"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

FAIL=0
ok() { echo "ok - $1"; }
not_ok() { echo "not ok - $1"; FAIL=1; }

sed -n '/^_extract_sing_score()/,/^}/p' "$SRC" >"$TMP/fn_extract.sh"
sed -n '/^_remove_sing_score_block()/,/^}/p' "$SRC" >"$TMP/fn_remove.sh"
. "$TMP/fn_extract.sh"
. "$TMP/fn_remove.sh"

# 1) マーカーあり楽譜は抽出でき、本文から除去される
MARKER_IN='歌ってみます\n===SING===\n{"notes":[{"key":60,"frame_length":45,"lyric":"き"}]}\n===SING===\nよいしょ'
EXTRACTED=$(printf '%b' "$MARKER_IN" | _extract_sing_score || true)
if echo "$EXTRACTED" | grep -q '"notes"'; then
	ok "extract reads piped marker JSON (heredoc stdin regression)"
else
	not_ok "extract did NOT read piped marker JSON"
fi

REMOVED=$(printf '%b' "$MARKER_IN" | _remove_sing_score_block)
if printf '%s' "$REMOVED" | grep -q 'notes'; then
	not_ok "marker JSON leaked into spoken text"
else
	ok "marker JSON removed from spoken text"
fi

# 2) マーカーなしのインライン楽譜も抽出・除去される
BARE_IN='はい、歌います {"notes":[{"key":60,"frame_length":45,"lyric":"き"}]} どうぞ！'
EXTRACTED=$(printf '%s' "$BARE_IN" | _extract_sing_score || true)
if echo "$EXTRACTED" | grep -q '"notes"'; then
	ok "extract reads bare inline JSON without marker"
else
	not_ok "extract did NOT read bare inline JSON"
fi
REMOVED=$(printf '%s' "$BARE_IN" | _remove_sing_score_block)
if printf '%s' "$REMOVED" | grep -q 'notes'; then
	not_ok "bare JSON leaked into spoken text"
else
	ok "bare JSON removed from spoken text"
fi

# 3) 通常コメント本文は無傷
NORMAL='今日の盤面、すごいですね。この調子で頑張ります。'
RESULT=$(printf '%s' "$NORMAL" | _remove_sing_score_block)
if [ "$RESULT" = "$NORMAL" ]; then
	ok "normal comment text preserved untouched"
else
	not_ok "normal comment text was altered"
fi

exit "$FAIL"
