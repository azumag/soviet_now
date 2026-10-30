#!/bin/bash
# soviet_now#144: stream_bug_report intake→queue seam の characterization test。
# 本テストは現行の `broadcast/comment.sh` の動作を「固定」する（正しい挙動の
# 規範ではなく、リファクタ前の実測契約）。逸脱が意図的変更なら本テストを更新する。
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

ok=0
fail=0
pass() { echo "ok - $1"; ok=$((ok + 1)); }
not_ok() { echo "not ok - $1"; fail=$((fail + 1)); }

export ELOOP_LIB_DIR="$TMP"
export CODEX_BUG_QUEUE_DIR="$TMP/bug_queue"
export CODEX_BUG_DISPATCH_DEDUP_FILE="$TMP/state/dedup.log"
mkdir -p "$TMP/state"

source broadcast/comment.sh
log() { :; }

count_queue() { find "$CODEX_BUG_QUEUE_DIR" -name '*.json' -type f 2>/dev/null | wc -l | tr -d ' '; }
queue_field() { python3 -c 'import json,sys,glob; fs=glob.glob(sys.argv[1]+"/*.json"); print(json.load(open(fs[0])).get(sys.argv[2],"") if fs else "")' "$CODEX_BUG_QUEUE_DIR" "$1"; }

# --- gate 既定停止 (docich#32): flag 未設定なら分類されても queue しない ---
unset CODEX_BUG_DISPATCH_ENABLED || true
_queue_stream_bug_reports_from_classification '[{"category":"stream_bug_report","user":"viewer1","comment":"固まりました","index":0}]' twitch h1 >/dev/null
[ "$(count_queue)" = "0" ] && pass 'gate disabled: no queue write' || not_ok 'gate disabled wrote queue'

export CODEX_BUG_DISPATCH_ENABLED=1

# --- 分類行から queue payload を生成 ---
out=$(_queue_stream_bug_reports_from_classification '[{"category":"stream_bug_report","user":"viewer1","comment":"配信が  止まりました","index":3}]' twitch bh01)
[ "$(count_queue)" = "1" ] && pass 'bug report queued' || not_ok 'bug report not queued'
[ -n "$out" ] && pass 'queued filename printed' || not_ok 'queued filename not printed'
[ "$(queue_field source)" = "twitch" ] && pass 'payload.source' || not_ok 'payload.source mismatch'
[ "$(queue_field status)" = "pending" ] && pass 'payload.status=pending' || not_ok 'payload.status mismatch'
[ "$(queue_field batch_hash)" = "bh01" ] && pass 'payload.batch_hash' || not_ok 'payload.batch_hash mismatch'
[ "$(queue_field category)" = "stream_bug_report" ] && pass 'payload.category' || not_ok 'payload.category mismatch'
[ "$(queue_field index)" = "3" ] && pass 'payload.index preserved' || not_ok 'payload.index mismatch'
[ "$(queue_field comment)" = "配信が 止まりました" ] && pass 'comment whitespace normalized' || not_ok 'comment normalization mismatch'

# --- 他 category は queue 対象外 ---
_queue_stream_bug_reports_from_classification '[{"category":"chitchat","user":"viewer1","comment":"固まりました"},{"category":"advice","user":"v2","comment":"x"}]' twitch h2 >/dev/null
[ "$(count_queue)" = "1" ] && pass 'non-bug categories filtered' || not_ok 'non-bug category queued'

# --- 同一 source+user+comment は dedup で二度積まない ---
_queue_stream_bug_reports_from_classification '[{"category":"stream_bug_report","user":"viewer1","comment":"配信が 止まりました","index":3}]' twitch h3 >/dev/null
[ "$(count_queue)" = "1" ] && pass 'same digest deduped across calls' || not_ok 'duplicate bug report re-queued'

# --- source 違いは別 digest（同じ user+comment でも積まれる）---
_queue_stream_bug_reports_from_classification '[{"category":"stream_bug_report","user":"viewer1","comment":"配信が 止まりました"}]' youtube h4 >/dev/null
[ "$(count_queue)" = "2" ] && pass 'same text on different source queued separately' || not_ok 'cross-source dedup over-matched'

_queue_stream_bug_reports_from_classification '[{"category":"stream_bug_report","user":"viewer1","comment":"配信が 止まりました"}]' kick h5 >/dev/null
[ "$(count_queue)" = "3" ] && pass 'kick source queued' || not_ok 'kick source not queued'

# --- 空白正規化で dedup が効く（空白差は同一 digest）---
_queue_stream_bug_reports_from_classification '[{"category":"stream_bug_report","user":"viewer1","comment":"配信が   止まりました  "}]' twitch h6 >/dev/null
[ "$(count_queue)" = "3" ] && pass 'whitespace-normalized dedup' || not_ok 'whitespace variant re-queued'

# --- user 正規化でも digest は同一 ---
_queue_stream_bug_reports_from_classification '[{"category":"stream_bug_report","user":"  viewer1  ","comment":"配信が 止まりました"}]' twitch h7 >/dev/null
[ "$(count_queue)" = "3" ] && pass 'user-normalized dedup' || not_ok 'user variant re-queued'

# --- comment 空は queue しない ---
_queue_stream_bug_reports_from_classification '[{"category":"stream_bug_report","user":"v9","comment":"   "}]' twitch h8 >/dev/null
[ "$(count_queue)" = "3" ] && pass 'empty comment skipped' || not_ok 'empty comment queued'

# --- malformed / 非 list JSON は fail-open (rc=0・書き込みなし) ---
_queue_stream_bug_reports_from_classification 'not json' twitch h9 >/dev/null
_queue_stream_bug_reports_from_classification '{"a":1}' twitch h9 >/dev/null
_queue_stream_bug_reports_from_classification '' twitch h9 >/dev/null
[ "$(count_queue)" = "3" ] && pass 'malformed classification fail-open' || not_ok 'malformed classification wrote queue'

# --- dedup file の 24h TTL: 古い ts は再 queue を許す ---
# (queue ファイル名は {epoch}_{digest16} なので、同一秒・同一 digest の再 queue は
#  os.replace で同名上書きされ件数が増えない。一意コメントで TTL を検証する)
dedup="$CODEX_BUG_DISPATCH_DEDUP_FILE"
old_ts=$(( $(date +%s) - 86401 ))
digest=$(python3 -c 'import hashlib;print(hashlib.sha256("twitch\x00vTTL\x00期限切れ確認".encode()).hexdigest())')
printf '%s|%s\n' "$old_ts" "$digest" >"$dedup"
_queue_stream_bug_reports_from_classification '[{"category":"stream_bug_report","user":"vTTL","comment":"期限切れ確認"}]' twitch h10 >/dev/null
[ "$(count_queue)" = "4" ] && pass 'dedup entries older than 24h expire' || not_ok 'expired digest still deduped'

# --- dedup file の再書き込みは tmp→replace で行われ最新 kept のみ残す ---
# (TTL で落ちたエントリが次回書き込み時に除去される契約)
recent=$(( $(date +%s) - 100 ))
printf '%s|%s\n%s|%s\n' "$old_ts" "deadbeef" "$recent" "cafef00d" >"$dedup"
_queue_stream_bug_reports_from_classification '[{"category":"stream_bug_report","user":"vN","comment":"新規不具合"}]' twitch h11 >/dev/null
! grep -q deadbeef "$dedup" && pass 'expired digest pruned on rewrite' || not_ok 'expired digest kept'
grep -q cafef00d "$dedup" && pass 'live digest preserved on rewrite' || not_ok 'live digest lost'

# ==== _ingest_stream_bug_reports_redacted (docich#33, 常時有効・fail-open) ====
export REDACTED_DIAG_EVENTS_DIR="$TMP/diag_events"
export REDACTED_DIAG_SPOOL_DIR="$TMP/spool"

eids=$(_ingest_stream_bug_reports_redacted '[{"category":"stream_bug_report","user":"viewer_secret","comment":"画面が固い不具合XYZ"}]' twitch)
[ -n "$eids" ] && pass 'redacted ingest emits event_id' || not_ok 'no event_id emitted'
eid=$(printf '%s' "$eids" | head -1)
ev="$REDACTED_DIAG_EVENTS_DIR/$eid.json"
[ -f "$ev" ] && pass 'event record written' || not_ok 'event record missing'
# イベント側に生コメント・user が入らない（redaction 契約）
! grep -q 'viewer_secret\|固い不具合XYZ' "$ev" && pass 'event carries no raw comment/user' || not_ok 'event leaked raw content'
grep -q '"redacted_context_hash"' "$ev" && pass 'event carries redacted_context_hash' || not_ok 'event missing hash'

# spool 側は raw を持つが 0600 + expires_at
sp="$REDACTED_DIAG_SPOOL_DIR/$eid.json"
[ -f "$sp" ] && pass 'restricted spool entry written' || not_ok 'spool entry missing'
grep -q 'viewer_secret' "$sp" && pass 'spool holds raw user' || not_ok 'spool lost raw user'
grep -q '"expires_at"' "$sp" && pass 'spool entry has expires_at' || not_ok 'spool missing expires_at'
[ "$(stat -f %Lp "$sp" 2>/dev/null || stat -c %a "$sp" 2>/dev/null)" = "600" ] && pass 'spool entry mode 0600' || not_ok 'spool entry mode not 0600'

# 他 category / 空 comment は取り込まない・fail-open
before=$(find "$REDACTED_DIAG_EVENTS_DIR" -name '*.json' | wc -l | tr -d ' ')
_ingest_stream_bug_reports_redacted '[{"category":"chitchat","user":"u","comment":"c"},{"category":"stream_bug_report","user":"u","comment":"  "}]' youtube >/dev/null
_ingest_stream_bug_reports_redacted 'garbage' kick >/dev/null
after=$(find "$REDACTED_DIAG_EVENTS_DIR" -name '*.json' | wc -l | tr -d ' ')
[ "$before" = "$after" ] && pass 'redacted ingest filters + fail-open' || not_ok 'redacted ingest over-ingested'

# gate env で無効化
export REDACTED_DIAG_INGEST_ENABLED=0
_ingest_stream_bug_reports_redacted '[{"category":"stream_bug_report","user":"u2","comment":"new bug"}]' twitch >/dev/null
[ "$(find "$REDACTED_DIAG_EVENTS_DIR" -name '*.json' | wc -l | tr -d ' ')" = "$after" ] && pass 'REDACTED_DIAG_INGEST_ENABLED=0 disables' || not_ok 'ingest gate ignored'
unset REDACTED_DIAG_INGEST_ENABLED

# ==== golden corpus を seam に通す end-to-end 駆動 ====
# corpus の bug 行を分類 JSON へ写像し、媒体別に現行 seam へ流す。
# twitch: viewer_002 の dup は 1 件のみ、youtube: 別 source は別 digest で積まれる、
# kick: 1 件。空コメント edge は捨てられる。
fresh_dir="$TMP/corpus_queue"; fresh_dedup="$TMP/corpus_state/d.log"
export CODEX_BUG_QUEUE_DIR="$fresh_dir" CODEX_BUG_DISPATCH_DEDUP_FILE="$fresh_dedup"
for media in twitch youtube kick; do
	cj=$(python3 - "$media" <<'PY'
import json, sys
media = sys.argv[1]
rows = []
for line in open(f"tests/fixtures/comment_pipeline/{media}_golden.jsonl", encoding="utf-8"):
    line = line.strip()
    if not line:
        continue
    r = json.loads(line)
    rows.append({"index": len(rows), "user": r["user"], "comment": r["comment"],
                 "category": r["expected_category"], "is_english": bool(r.get("is_english"))})
print(json.dumps(rows, ensure_ascii=False))
PY
)
	_queue_stream_bug_reports_from_classification "$cj" "$media" "corpus_$media" >/dev/null
done
n=$(find "$fresh_dir" -name '*.json' -type f | wc -l | tr -d ' ')
# twitch bug=2行だが同一 user+comment →1, youtube bug=2行(別user/別comment)→2, kick bug=1
[ "$n" = "4" ] && pass 'golden corpus: dup suppressed / cross-source queued (4 files)' || not_ok "corpus dispatch count=$n (expected 4)"

echo "== $ok passed, $fail failed =="
[ "$fail" = "0" ]

echo "== $ok passed, $fail failed =="
[ "$fail" = "0" ]
