#!/bin/bash
# batch_commentary.sh - 改善サイクル単位の成績要約と音声解説
#
# Usage: ./batch_commentary.sh <accumulated_games.json> <cycle_size>
# 呼び出し元は試合ごとのチャットを積まず、サイクル閾値で一度だけ
# このスクリプトをバックグラウンドで起動する。入力履歴は先に固定する。

set -o pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"
[ -f .env ] && set -a && . ./.env && set +a
source ./eloop_lib.sh

ACC_FILE="${1:-${ACCUMULATED_GAMES_FILE:-tmp/state/accumulated_games.json}}"
CYCLE_SIZE="${2:-${MIN_GAMES_BEFORE_IMPROVE:-12}}"
STATE_DIR="${BATCH_COMMENTARY_STATE_DIR:-tmp/state/batch_commentary}"
DEBUG_DIR="${BATCH_COMMENTARY_DEBUG_DIR:-tmp/debug/batch_commentary}"
LOCK_DIR="$STATE_DIR/.lock"
RETRY_FILE="$STATE_DIR/retry.json"
AGENT_SOURCE="${BATCH_COMMENTARY_AGENTS:-}"
TIMEOUT_SEC="${BATCH_COMMENTARY_TIMEOUT_SEC:-120}"
MAX_CHARS="${BATCH_COMMENTARY_MAX_CHARS:-420}"
CONTEXT_MAX_CHARS="${BATCH_COMMENTARY_CONTEXT_MAX_CHARS:-2400}"
RETRY_BASE_SEC="${BATCH_COMMENTARY_RETRY_BASE_SEC:-300}"
RETRY_MAX_SEC="${BATCH_COMMENTARY_RETRY_MAX_SEC:-3600}"

_log() { echo "[batch_commentary $(date '+%H:%M:%S')] $*" >&2; }
_num() {
  case "${1:-}" in ''|*[!0-9]*) echo "$2" ;; *) echo "$1" ;; esac
}

# 設定ファイルや環境変数に一時的な sentinel/typo が残っていても、
# バッチ解説だけが止まらないように有効な候補だけを残す。
_valid_agent_list() {
  local raw="${1:-}" item out=""
  local old_ifs="$IFS"
  IFS=',' read -ra _agent_items <<< "$raw"
  IFS="$old_ifs"
  for item in "${_agent_items[@]}"; do
    item="${item#"${item%%[![:space:]]*}"}"
    item="${item%"${item##*[![:space:]]}"}"
    [ -n "$item" ] || continue
    if _ai_agent_spec_valid "$item"; then
      [ -n "$out" ] && out+=","
      out+="$item"
    else
      _log "invalid AI agent skipped: $item"
    fi
  done
  printf '%s' "$out"
}

AGENTS="$(_valid_agent_list "$AGENT_SOURCE")"
if [ -z "$AGENTS" ]; then
  AGENTS="$(_valid_agent_list "${RADIO_AGENTS:-}")"
fi
if [ -z "$AGENTS" ]; then
  AGENTS="$(_valid_agent_list "${AI_COMMON_AGENTS:-}")"
fi
CYCLE_SIZE=$(_num "$CYCLE_SIZE" 12)
TIMEOUT_SEC=$(_num "$TIMEOUT_SEC" 120)
MAX_CHARS=$(_num "$MAX_CHARS" 420)
CONTEXT_MAX_CHARS=$(_num "$CONTEXT_MAX_CHARS" 2400)
RETRY_BASE_SEC=$(_num "$RETRY_BASE_SEC" 300)
RETRY_MAX_SEC=$(_num "$RETRY_MAX_SEC" 3600)
[ "$CYCLE_SIZE" -gt 0 ] || CYCLE_SIZE=12
[ "$TIMEOUT_SEC" -gt 0 ] || TIMEOUT_SEC=120
[ "$MAX_CHARS" -ge 120 ] || MAX_CHARS=420
[ "$CONTEXT_MAX_CHARS" -ge 420 ] || CONTEXT_MAX_CHARS=2400
[ "$RETRY_BASE_SEC" -gt 0 ] || RETRY_BASE_SEC=300
[ "$RETRY_MAX_SEC" -ge "$RETRY_BASE_SEC" ] || RETRY_MAX_SEC="$RETRY_BASE_SEC"
mkdir -p "$STATE_DIR" "$DEBUG_DIR" 2>/dev/null || exit 1

_json_value() {
  python3 - "$@" <<'PY'
import hashlib
import json
import sys
path, expression = sys.argv[1:3]
try:
    with open(path, encoding="utf-8") as handle:
        value = json.load(handle)
except Exception:
    raise SystemExit(1)
if expression == "count":
    print(int(value.get("count", 0) or 0))
elif expression == "batch_id":
    try:
        start = max(0, int(sys.argv[3]))
        end = max(start, int(sys.argv[4]))
    except (IndexError, TypeError, ValueError):
        start, end = 0, None
    files = [str(x) for x in value.get("files", []) or [] if x]
    selected_files = files[start:end]
    scores = str(value.get("scores", "") or "").split()
    raw_scores = str(value.get("raw_scores", "") or "").split()
    payload = json.dumps({
        "count": len(selected_files),
        "files": selected_files,
        "scores": " ".join(scores[start:end]),
        "raw_scores": " ".join(raw_scores[start:end]),
        "hash": str(value.get("hash", "") or ""),
    }, ensure_ascii=False, sort_keys=True).encode("utf-8")
    print(hashlib.sha256(payload).hexdigest()[:24])
elif expression == "files":
    try:
        start = max(0, int(sys.argv[3]))
        end = max(start, int(sys.argv[4]))
    except (IndexError, TypeError, ValueError):
        start, end = 0, None
    for item in (value.get("files", []) or [])[start:end]:
        if item:
            print(str(item))
PY
}

count=$(_json_value "$ACC_FILE" count 2>/dev/null || echo 0)
case "$count" in ''|*[!0-9]*) count=0 ;; esac
batch_end=$((count / CYCLE_SIZE * CYCLE_SIZE))
[ "$batch_end" -ge "$CYCLE_SIZE" ] || exit 0
batch_start=$((batch_end - CYCLE_SIZE))
batch_id=$(_json_value "$ACC_FILE" batch_id "$batch_start" "$batch_end" 2>/dev/null || true)
[ -n "$batch_id" ] || exit 1

done_file="$STATE_DIR/${batch_id}.done.json"
[ -f "$done_file" ] && exit 0
if ! mkdir "$LOCK_DIR" 2>/dev/null; then exit 0; fi
_cleanup_lock() { rm -rf "$LOCK_DIR" 2>/dev/null || true; }
trap _cleanup_lock EXIT

now=$(date +%s)
if [ -f "$RETRY_FILE" ]; then
  next_retry=$(python3 - "$RETRY_FILE" "$batch_id" <<'PY'
import json, sys
try:
    data = json.load(open(sys.argv[1], encoding="utf-8"))
except Exception:
    data = {}
if str(data.get("batch_id", "")) == sys.argv[2]:
    print(int(data.get("next_retry_at", 0) or 0))
PY
)
  case "$next_retry" in ''|*[!0-9]*) next_retry=0 ;; esac
  [ "$next_retry" -gt "$now" ] && exit 0
fi

summary_file="$DEBUG_DIR/${batch_id}.summary.txt"
prompt_file="$DEBUG_DIR/${batch_id}.prompt.txt"
explanation_file="$DEBUG_DIR/${batch_id}.explanation.txt"
files=()
while IFS= read -r history_file; do
  [ -n "$history_file" ] && [ -f "$history_file" ] || continue
  files+=("$history_file")
done < <(_json_value "$ACC_FILE" files "$batch_start" "$batch_end" 2>/dev/null || true)
[ "${#files[@]}" -gt 0 ] || { _log "no readable history files: batch=$batch_id"; exit 1; }
batch_count="${#files[@]}"

python3 batch_summary.py "${files[@]}" >"$summary_file" 2>/dev/null || {
  _log "summary generation failed: batch=$batch_id"
  exit 1
}
summary_context=$(python3 - "$summary_file" "$CONTEXT_MAX_CHARS" <<'PY'
import sys
path, limit_raw = sys.argv[1:3]
try:
    limit = max(120, int(limit_raw))
except Exception:
    limit = 2400
try:
    text = open(path, encoding="utf-8", errors="ignore").read()
except Exception:
    text = ""
lines = [line for line in text.splitlines()
         if not line.startswith("===BEST_FILE===")
         and not line.startswith("===WORST_FILE===")]
print("\n".join(lines).strip()[:limit])
PY
)

cat >"$prompt_file" <<PROMPT
あなたはソ連ゲーム配信の成績解説者です。以下は改善ループ1バッチ（${batch_count}試合）の実測サマリーです。この情報だけを根拠に、音声ワーカーがそのまま読める日本語の解説を1本作ってください。

条件:
- 150〜${MAX_CHARS}字程度、です・ます調。
- 一人称は「私」を使い、「僕」「俺」「自分」は使わない。
- 平均・最高・最低スコア、建国到達率、併合率や主な判断傾向など、実測値を2〜4個入れる。
- 良かった点、今回の課題、次の改善で見る点を自然な文章で短くまとめる。
- 見出し、箇条書き、JSON、コード、Markdown、英語、AI自身の作業説明は出さない。
- 数値や到達状況が不足する場合は、推測で補わず「今回の記録では確認できません」と述べる。

【実測サマリー】
${summary_context}
PROMPT

_batch_commentary_valid() {
  local value="${1:-}" length
  length=${#value}
  [ "$length" -ge 40 ] && [ "$length" -le "$MAX_CHARS" ] || return 1
  printf '%s' "$value" | grep -Eq '[。！？.!?]' || return 1
  printf '%s' "$value" | grep -Eiq '(^|[[:space:]])(json|markdown|tool_call|analysis|thinking|webfetch|websearch|===)' && return 1
  case "$value" in
    *'{'*|*'}'*|'['*|*']'*) return 1 ;;
  esac
  return 0
}

# 一部のモデルは本文の前に「解説文を作成します」のような作業メモを
# 1行だけ出す。共通ガードはそのような前置きを全体拒否する設計なので、
# バッチ解説では明確な前置き行だけを取り除き、本文の安全検証を維持する。
_batch_commentary_strip_work_note() {
  python3 -c '
import re
import sys

lines = sys.stdin.read().replace("\\r", "").splitlines()
while len(lines) > 1 and lines and re.search(
    r"^(?=.*(?:作成|確認|まとめ|紹介|調整))(?=.*(?:します|しました|です)).*",
    lines[0].strip(),
    re.IGNORECASE,
):
    lines.pop(0)
print("\\n".join(lines).strip(), end="")
'
}

[ -n "$AGENTS" ] || { _log "AI agents unavailable: batch=$batch_id"; exit 1; }
_log "AI commentary generation: batch=$batch_id games=$batch_count (source=${batch_start}-${batch_end}) agents=$AGENTS"
raw_text=$(ai_generate_list "RADIO:batch_commentary" "$prompt_file" "$AGENTS" "$TIMEOUT_SEC" "_batch_commentary_valid" 2>/dev/null || true)
text=$(printf '%s' "$raw_text" | _batch_commentary_strip_work_note | _ai_guard_model_output 2>/dev/null || true)
text=$(printf '%s' "$text" | tr '\r\n' ' ' | sed -E 's/[[:space:]]+/ /g; s/^[[:space:]]+//; s/[[:space:]]+$//')

if ! _batch_commentary_valid "$text"; then
  attempt=1
  if [ -f "$RETRY_FILE" ]; then
    attempt=$(python3 - "$RETRY_FILE" "$batch_id" <<'PY'
import json, sys
try:
    data = json.load(open(sys.argv[1], encoding="utf-8"))
except Exception:
    data = {}
print(int(data.get("attempt", 0) or 0) + 1
      if str(data.get("batch_id", "")) == sys.argv[2] else 1)
PY
)
  fi
  case "$attempt" in ''|*[!0-9]*) attempt=1 ;; esac
  [ "$attempt" -le 6 ] || attempt=6
  delay=$((RETRY_BASE_SEC * (1 << (attempt - 1))))
  [ "$delay" -le "$RETRY_MAX_SEC" ] || delay="$RETRY_MAX_SEC"
  python3 - "$RETRY_FILE" "$batch_id" "$attempt" "$((now + delay))" <<'PY'
import json, os, sys
path, batch_id, attempt, next_retry = sys.argv[1:5]
tmp = path + ".tmp"
with open(tmp, "w", encoding="utf-8") as handle:
    json.dump({"batch_id": batch_id, "attempt": int(attempt),
               "next_retry_at": int(next_retry)}, handle, ensure_ascii=False)
os.replace(tmp, path)
PY
  _log "AI commentary unavailable: batch=$batch_id, retry_in=$delay s"
  exit 1
fi

printf '%s\n' "$text" >"$explanation_file"
COMMENT_AUDIO_DEDUP_TTL_SEC="${BATCH_COMMENTARY_AUDIO_DEDUP_TTL_SEC:-86400}" \
  enqueue_audio_text "$text" "batch_commentary" "${BATCH_COMMENTARY_SPEAKER:-}" || {
    _log "audio enqueue failed: batch=$batch_id"
    exit 1
  }

python3 - "$done_file" "$batch_id" "$batch_count" "$summary_file" "$explanation_file" <<'PY'
import json, os, sys, time
path, batch_id, count, summary_file, explanation_file = sys.argv[1:]
tmp = path + ".tmp"
with open(tmp, "w", encoding="utf-8") as handle:
    json.dump({"batch_id": batch_id, "count": int(count),
               "summary_file": summary_file,
               "explanation_file": explanation_file,
               "queued_at": time.time()}, handle, ensure_ascii=False)
os.replace(tmp, path)
PY
rm -f "$RETRY_FILE" 2>/dev/null || true
_log "queued: batch=$batch_id games=$batch_count (source=${batch_start}-${batch_end})"
exit 0
