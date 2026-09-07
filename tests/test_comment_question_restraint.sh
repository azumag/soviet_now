#!/usr/bin/env bash
set -euo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
template="$ROOT/prompts/comment_template.md"
comment="$ROOT/broadcast/comment.sh"

grep -q '【質問の抑制・最重要】' "$template"
grep -q '基本は感想、補足、ウィット、言い切りのどれかで自然に終える' "$template"
grep -q '直近の読み上げ履歴で質問締めが続いている場合は今回は質問しない' "$template"
grep -q '会話を続けるだけの質問は足さない' "$comment"

if rg -q '軽い問いかけのどれか|別の質問返し|自然なら相手の体験を聞く短い質問' "$template" "$comment"; then
	echo "質問を定型的に促す旧指示が残っています" >&2
	exit 1
fi

echo "comment question restraint: ok"
