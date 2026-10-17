#!/usr/bin/env bash
# #2373: 自分自身の投稿は返信対象のコメント一覧に混ざらず、専用セクション
# (self_chat_context) に自分の既発言として出ること。
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

cd "$ROOT"
export ELOOP_LIB_DIR="$ROOT"
source broadcast/comment.sh

export previous_comments_context="（なし）"
export recent_spoken_comment_context="（なし）"
export viewer_memory_context="（該当する投稿者別メモなし）"
export current_time="12:00"
export time_period="午後"
export comment_batch_context="（なし）"
export strategy_advice_candidates="（なし）"
export comment_advice_candidates="（なし）"
export codex_advice_candidates="（なし）"
export comment_advice_context="（なし）"
export comment_followup_hints="（なし）"
export past_topics="（なし）"
export celebration_history_context="（なし）"
export comment_thumbnail_ocr_context="（なし）"
export game_state_context="（なし）"
export comment_ops_context="（なし）"
export _comment_persona="テスト用ペルソナ"
export _comment_ui_memo="（なし）"
export _comment_channel_intro="（なし）"
export sing_reference="（なし）"
export _prediction_cycle_games="0"
export self_chat_context="配信アカウント（dociai）として、あなた自身が過去に投稿したチャット発言です。返信対象ではありません。
- dociai: レトロゲームコーナーを開始します。"

out="$TMP/prompt.txt"
_build_category_prompt default "alice: こんにちは" '[]' "$out"

# 自分自身の投稿セクションが展開され、自分の投稿が載っている。
grep -qF "【あなた自身が投稿したチャット発言（返信対象ではない）】" "$out"
grep -qF "dociai: レトロゲームコーナーを開始します。" "$out"
grep -qF "alice: こんにちは" "$out"
# リテラルが残っていない (envsubst 許可リスト漏れの検出)。
if grep -qF '${self_chat_context}' "$out"; then
  echo "self_chat_context が展開されていません" >&2
  exit 1
fi
# 自分の投稿は返信対象リストに混ざらない。
python3 - "$out" <<'PY'
import sys

text = open(sys.argv[1], encoding="utf-8").read()
head, _, tail = text.partition("【あなた自身が投稿したチャット発言（返信対象ではない）】")
assert tail, "自分自身の投稿セクションがありません"
# 返信対象コメント一覧は専用セクションより前だけにあり、そこに自己投稿は入らない。
assert "alice: こんにちは" in head, "視聴者コメントが返信対象から消えています"
assert "レトロゲームコーナーを開始します。" not in head, "自己投稿が返信対象に混ざっています"
assert "レトロゲームコーナーを開始します。" in tail, "自己投稿が自分セクションにありません"
PY

# 追加契約 (返信生成の出力契約) に自己投稿の扱いが入っている。
contract_out="$TMP/contract.txt"
: >"$contract_out"
_append_comment_reply_contract "$contract_out"
grep -qF "【自分自身の投稿の扱い（最重要）】" "$contract_out"
grep -qF "自分の投稿を第三者のコメントとして紹介・お礼・感謝・返信しないこと" "$contract_out"
grep -qF "推測で自分の発言にしないこと" "$contract_out"

printf 'comment_self_context_prompt: self posts separated from reply targets\n'
