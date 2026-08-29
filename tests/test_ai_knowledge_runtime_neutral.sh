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

printf '%s\n' "$ai_knowledge_prompt" | grep -q '_radio_topic_needs_runtime_evidence "$topic"' ||
	fail "テーマに必要な場合だけ実行構成を読む条件分岐がありません"

printf '%s\n' "$ai_knowledge_prompt" | grep -q '配信の実装に触れない本文では読まないこと' ||
	fail "無関係な本文でVM/handoffを読まない指示がありません"

printf '%s\n' "$ai_knowledge_prompt" | grep -q 'prompts/ops_brief.md、core/config.sh の RADIO_\* 設定、broadcast/radio_engine.sh' ||
	fail "必要時に確認するローカル根拠ファイルが指定されていません"

if printf '%s\n' "$ai_knowledge_prompt" | grep -q '\${runtime_evidence}'; then
	fail "VM/handoff本文を注入する旧方式が残っています"
fi

if grep -q '_radio_runtime_evidence_block' broadcast/radio_persona.sh broadcast/radio_corners.sh; then
	fail "VM/handoff本文を生成して注入するヘルパーが残っています"
fi

printf '%s\n' "$ai_knowledge_prompt" | grep -q '.env は全文を読まず' ||
	fail "VM実効設定を安全に限定確認する指示がありません"

ELOOP_LIB_DIR="$PWD" bash -c '
	source broadcast/radio_persona.sh
	_radio_topic_needs_runtime_evidence "コード生成AI（Copilot・Claude Code・Cursor等）"
	! _radio_topic_needs_runtime_evidence "画像生成AI（Stable Diffusion等）"
' || fail "実行構成を読むテーマ境界が正しくありません"

echo "PASS: AI knowledge corner keeps stream runtime descriptions product-neutral"
