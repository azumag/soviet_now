#!/usr/bin/env bash
# tools/build_ops_brief.sh - handoff.md の最新セクション見出しから
#   prompts/ops_brief.md（コメント返しプロンプトへ埋め込む「直近の裏側の改修」メモ）を生成する。
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

python3 - "$src" "$items" "$out" <<'PY'
import re
import sys
from pathlib import Path

src = Path(sys.argv[1])
try:
    items = max(1, int(sys.argv[2]))
except Exception:
    items = 3
out = Path(sys.argv[3])

# 見出し例: "## 2026-08-26 21:2x-21:5x JST — 同じニュースを1日4回読み上げた問題を修正"
# 日付/時刻の前置きは視聴者向けに不要なので落とし、topic 部分だけを残す。
HEAD = re.compile(r"^##\s+(.*)$")
LEAD_DASH = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}\s.*?[—–]\s*")
# 見出しに全角ダッシュが無い場合の保険: 日付〜JST までを落とす。
LEAD_PLAIN = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}[0-9:x\s\-]*(?:JST)?\s*")

topics = []
for raw in src.read_text(encoding="utf-8", errors="ignore").splitlines():
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
PY
