#!/usr/bin/env bash
# tools/build_ops_brief.sh - handoff.md の最新セクション見出しから
#   prompts/ops_brief.md（コメント返し用の内部運用メモ）と、
#   prompts/viewer_title.md（明示された視聴者向け配信タイトル候補）を生成する。
#
# コメント返しプロンプトは肥大させたくないので、ここでは見出しの topic 部分だけを
# 数行・各行短く切り出す。本文や hash・ファイル名は入れない。
#
# usage: tools/build_ops_brief.sh [handoff.md path] [items]
#   handoff.md 省略時は ../../handoff.md → ./handoff.md の順に探す。
#   items 省略時は 3。
set -euo pipefail
cd "$(dirname "$0")/.." || exit 1

# 自動探索は docich チェックアウトのレイアウト (docich/handoff.md) だけ。
# VM (/home/ubuntu/soren) には古い handoff.md が残っているので ./handoff.md は拾わない
# (拾うと古い内容が本番プロンプトへ入る)。VM では生成せず、Mac で生成したものを配布する。
#
# docich は複数 worktree で使われ、worktree ごとの handoff.md は独立している。生成ソースを
# 一本化するため、linked worktree から実行しても常に main worktree の handoff.md (= 正本) を
# 読む。DOCICH_HANDOFF_FILE で明示的に上書きできる (第1引数のパス指定が最優先)。
src="${1:-}"
if [ -z "$src" ] && [ -n "${DOCICH_HANDOFF_FILE:-}" ] && [ -f "$DOCICH_HANDOFF_FILE" ]; then
	src="$DOCICH_HANDOFF_FILE"
fi
if [ -z "$src" ]; then
	repo_root="$(cd ../.. && pwd 2>/dev/null || true)"
	main_wt=""
	if [ -n "$repo_root" ]; then
		main_wt="$(git -C "$repo_root" worktree list --porcelain 2>/dev/null | awk '/^worktree /{print substr($0,10); exit}')"
	fi
	if [ -n "$main_wt" ] && [ -f "$main_wt/handoff.md" ]; then
		src="$main_wt/handoff.md"
	fi
fi
if [ -z "$src" ] && [ -f "../../handoff.md" ]; then
	src="../../handoff.md"
fi
[ -n "$src" ] && [ -f "$src" ] || {
	echo "handoff.md が見つかりません。docich チェックアウトで実行するか、パスを引数で渡してください。" >&2
	exit 1
}

items="${2:-3}"
out="prompts/ops_brief.md"
viewer_out="prompts/viewer_title.md"

python3 - "$src" "$items" "$out" "$viewer_out" <<'PY'
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

src = Path(sys.argv[1])
try:
    items = max(1, int(sys.argv[2]))
except Exception:
    items = 3
out = Path(sys.argv[3])
viewer_out = Path(sys.argv[4])

# 見出し例: "## 2026-08-26 21:2x-21:5x JST — 同じニュースを1日4回読み上げた問題を修正"
# 日付/時刻の前置きは視聴者向けに不要なので落とし、topic 部分だけを残す。
HEAD = re.compile(r"^##\s+(.*)$")
LEAD_DASH = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}\s.*?[—–]\s*")
# 見出しに全角ダッシュが無い場合の保険: 日付〜JST までを落とす。
LEAD_PLAIN = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}[0-9:x\s\-]*(?:JST)?\s*")

raw_lines = src.read_text(encoding="utf-8", errors="ignore").splitlines()
topics = []
for raw in raw_lines:
    m = HEAD.match(raw.strip())
    if not m:
        continue
    topic = m.group(1).strip()
    stripped = LEAD_DASH.sub("", topic).strip()
    if stripped == topic:
        stripped = LEAD_PLAIN.sub("", topic).strip()
    topic = stripped
    topic = re.sub(r"\s+", " ", topic)
    # 課題番号 (Issue #23 / docich#10 / issue-23) は視聴者向けメモに要らない内部識別子。
    # 読み上げに出ないよう、見出しの段階で落とす。
    topic = re.sub(r"(?i)\b(?:issue|docich)\s*#?\s*-?\d+\b", "", topic)
    topic = re.sub(r"^[\s:：,、。\-–—]+", "", topic)
    topic = re.sub(r"\s+", " ", topic).strip()
    if not topic:
        continue
    if len(topic) > 70:
        topic = topic[:69].rstrip("、。 ,.") + "…"
    topics.append(topic)
    if len(topics) >= items:
        break

lines = ["# 直近の裏側の改修 (tools/build_ops_brief.sh が handoff.md から自動生成。手で編集しない)"]
lines += [f"- {t}" for t in topics]
out.write_text("\n".join(lines) + "\n", encoding="utf-8")
print(f"{out}: {len(topics)} 件")
for t in topics:
    print(f"  - {t}")

# 公開タイトルは内部見出しから推測しない。最新セクションに開発担当が
# 明示した candidate メタデータだけを抽出し、selector の契約入力を生成する。
# - 視聴者向けタイトル (viewer_title): 公開文字列そのもの。必須。
# - 根拠 (rationale): 文面の根拠。内部メタデータで公開しない。
# - 確認段階 (status): implemented / merged / deployed / verified。
#   既定は implemented（selector は deployed/verified だけ採用する）。
# - 生成日時 (generated_at): 省略時は build 時刻 (UTC ISO-8601)。
# - 有効範囲 (valid_scope): 省略可。現在カテゴリと不一致の候補は公開しない。
META_FIELDS = {
    "viewer_title": re.compile(
        r"^\s*(?:[-*]\s*)?(?:viewer_title|視聴者向けタイトル)\s*[:：]\s*(.+?)\s*$",
        re.IGNORECASE,
    ),
    "rationale": re.compile(
        r"^\s*(?:[-*]\s*)?(?:rationale|根拠)\s*[:：]\s*(.+?)\s*$",
        re.IGNORECASE,
    ),
    "status": re.compile(
        r"^\s*(?:[-*]\s*)?(?:status|確認段階|viewer_title_status)\s*[:：]\s*(.+?)\s*$",
        re.IGNORECASE,
    ),
    "generated_at": re.compile(
        r"^\s*(?:[-*]\s*)?(?:generated_at|生成日時)\s*[:：]\s*(.+?)\s*$",
        re.IGNORECASE,
    ),
    "valid_scope": re.compile(
        r"^\s*(?:[-*]\s*)?(?:valid_scope|有効範囲)\s*[:：]\s*(.+?)\s*$",
        re.IGNORECASE,
    ),
}
latest_section = []
seen_latest = False
for raw in raw_lines:
    if HEAD.match(raw.strip()):
        if seen_latest:
            break
        seen_latest = True
        continue
    if seen_latest:
        latest_section.append(raw)

meta = {}
for raw in latest_section:
    for key, pattern in META_FIELDS.items():
        if key in meta:
            continue
        m = pattern.match(raw.strip())
        if m:
            meta[key] = re.sub(r"\s+", " ", m.group(1)).strip()

# status 既定は implemented（安全側: そのままでは公開されない）。
if "viewer_title" in meta and "status" not in meta:
    meta["status"] = "implemented"
# 生成日時は build 時刻を既定とし、freshness はこの時刻から数える。
if "viewer_title" in meta and "generated_at" not in meta:
    meta["generated_at"] = (
        datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    )

viewer_lines = [
    "# 視聴者向け配信タイトル (tools/build_ops_brief.sh が handoff.md から自動生成。手で編集しない)"
]
for key in ("viewer_title", "rationale", "status", "generated_at", "valid_scope"):
    if meta.get(key):
        viewer_lines.append(f"{key}: {meta[key]}")
viewer_out.write_text("\n".join(viewer_lines) + "\n", encoding="utf-8")
present = [k for k in ("viewer_title", "rationale", "status", "generated_at", "valid_scope") if meta.get(k)]
print(f"{viewer_out}: {len(present)} フィールド ({', '.join(present) if present else '候補なし'})")
PY
