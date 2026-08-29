#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

fail() {
	echo "FAIL: $*" >&2
	exit 1
}

ai_knowledge_prompt=$(sed -n '/^start_radio_corner_ai_knowledge()/,/^start_radio_corner_soviet_quiz()/p' broadcast/radio_corners.sh)

if printf '%s\n' "$ai_knowledge_prompt" | grep -q 'このゲーム配信自体がClaude Codeで動いている'; then
	fail "AI知識コーナーにClaude Code固定の配信説明が残っています"
fi

if printf '%s\n' "$ai_knowledge_prompt" | grep -q 'この配信自体がClaude Code'; then
	fail "AI知識コーナーにClaude Code固定の実行環境説明が残っています"
fi

printf '%s\n' "$ai_knowledge_prompt" | grep -q 'この配信全体が特定企業・特定製品のAIやコーディングエージェントだけで動いているとは説明しないこと' ||
	fail "特定製品だけで配信が動くと断定しない指示がありません"

printf '%s\n' "$ai_knowledge_prompt" | grep -q 'このプロンプトから現在の担当製品は特定できない' ||
	fail "現在の担当製品を推測しない指示がありません"

echo "PASS: AI knowledge corner keeps stream runtime descriptions product-neutral"
