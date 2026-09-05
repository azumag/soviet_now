#!/usr/bin/env bash
# コメント返しプロンプトへ「いまの配信・運用状況メモ」が入ることを検証する。
#
# 背景: 視聴者は画面の作業中バナーや改善モードを見て「今なにしてるの」と聞いてくるが、
# コメント返しプロンプトには VM の運用状態も裏側の改修内容も一切入っていなかった。
# _build_comment_ops_context がライブ状態ファイル + prompts/ops_brief.md から
# 短い箇条書きを作り、テンプレートの ${comment_ops_context} へ埋め込まれる。
# プロンプト肥大を避けるため、文字数上限で必ず切られることも確認する。
set -u

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SRC="$ROOT/broadcast/comment.sh"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

FAIL=0
ok() { echo "ok - $1"; }
not_ok() { echo "not ok - $1"; FAIL=1; }

# --- 実関数を抽出して source ---
sed -n '/^_build_comment_ops_context()/,/^}/p' "$SRC" >"$TMP/fn_ops.sh"
[ -s "$TMP/fn_ops.sh" ] || { not_ok "extract _build_comment_ops_context"; exit 1; }
# shellcheck source=/dev/null
. "$TMP/fn_ops.sh"

ELOOP_LIB_DIR="$TMP"
TMP_STATE_DIR="tmp/state"
mkdir -p "$TMP/tmp/state" "$TMP/prompts"
CODEX_WORK_OVERLAY_STATE_FILE="$TMP/tmp/state/codex_work_indicator.json"
IMPROVE_STATE_FILE="$TMP/tmp/state/improve_state.json"
AB_STATE_FILE="$TMP/tmp/state/ab_state.json"
export ELOOP_LIB_DIR TMP_STATE_DIR CODEX_WORK_OVERLAY_STATE_FILE IMPROVE_STATE_FILE AB_STATE_FILE

# --- 1. 状態ファイルが1つも無くても落ちず、既定文言を返す ---
out=$(_build_comment_ops_context main)
[ -n "$out" ] && ok "状態ファイル無しでも出力する" || not_ok "状態ファイル無しで空出力"
case "$out" in *"作業中バナー: 出ていない"*) ok "バナー無し時は「出ていない」" ;; *) not_ok "バナー無し時の文言: $out" ;; esac
case "$out" in *"戦略改善: 待機中"*) ok "improve 無し時は待機中" ;; *) not_ok "improve 無し時の文言: $out" ;; esac
case "$out" in *"メリケンAIは待機中"*) ok "main モードはメリケンAI待機中" ;; *) not_ok "main モードの文言: $out" ;; esac
case "$out" in *"A/B"*) not_ok "A/B 状態が無いのに A/B 行が出た" ;; *) ok "A/B 状態が無ければ A/B 行を出さない" ;; esac

# --- 2. 作業中バナー / 改善中 / A/B / soren91 モードが反映される ---
printf '%s' '{"active":true,"ts":1,"title":"ニュース重複を修正中","body":"同じニュースを繰り返し読む問題を直しています。"}' >"$CODEX_WORK_OVERLAY_STATE_FILE"
printf '%s' '{"status":"running","phase":"analyze","progress":30}' >"$IMPROVE_STATE_FILE"
printf '%s' '{"a_hash":"aaaa","b_hash":"bbbb","games_recorded":88}' >"$AB_STATE_FILE"
out=$(_build_comment_ops_context soren91)
case "$out" in *"ニュース重複を修正中"*) ok "バナーのタイトルが入る" ;; *) not_ok "バナータイトル欠落: $out" ;; esac
case "$out" in *"同じニュースを繰り返し読む問題を直しています。"*) ok "バナーの本文が入る" ;; *) not_ok "バナー本文欠落: $out" ;; esac
case "$out" in *"戦略改善: 実行中(analyze)"*) ok "改善中は phase 付きで実行中" ;; *) not_ok "改善中の文言: $out" ;; esac
case "$out" in *"メイン画面はメリケンAI"*) ok "soren91 モードが反映される" ;; *) not_ok "soren91 モードの文言: $out" ;; esac
case "$out" in *"記録済み88試合"*) ok "A/B の記録試合数が入る" ;; *) not_ok "A/B 試合数欠落: $out" ;; esac
case "$out" in *aaaa*|*bbbb*) not_ok "戦略 hash がプロンプトへ漏れている: $out" ;; *) ok "戦略 hash は出力しない" ;; esac

# --- 2a. メリケンAI(soren91) の在否は .env のトグルから決まる（worker の環境変数は当てにしない） ---
printf 'SOREN91_ENABLED=0\nSOREN91_DAILY_ENABLED=0\n' >"$TMP/.env"
out=$(_build_comment_ops_context main)
case "$out" in *"メリケンAI(ソ連ゲーム91)はいま停止中で登場しない"*) ok "soren91 無効時は停止中と言う" ;; *) not_ok "soren91 無効時の文言: $out" ;; esac
printf 'SOREN91_ENABLED=1\nSOREN91_DAILY_ENABLED=1\n' >"$TMP/.env"
out=$(_build_comment_ops_context main)
case "$out" in *"戦略改善中の代打と1日1回の枠で登場"*) ok "日次枠 有効時はその旨を言う" ;; *) not_ok "日次枠有効時の文言: $out" ;; esac
printf 'SOREN91_ENABLED=1\nSOREN91_DAILY_ENABLED=0\n' >"$TMP/.env"
out=$(_build_comment_ops_context main)
case "$out" in *"戦略改善に入った時だけ登場"*) ok "日次枠 無効時は改善時のみと言う" ;; *) not_ok "日次枠無効時の文言: $out" ;; esac
out=$(_build_comment_ops_context soren91)
case "$out" in *"メイン画面はメリケンAI"*) ok "soren91 稼働中は .env より実モードを優先" ;; *) not_ok "soren91 モード優先の文言: $out" ;; esac
rm -f "$TMP/.env"

# --- 2b. 停止中のチャネルポイント予想は「停止中」と明示され、稼働中は行自体が出ない ---
case "$out" in *"チャネルポイント予想"*) not_ok "予想が動いているのに停止中の行が出た: $out" ;; *) ok "予想稼働中は行を出さない" ;; esac
: >"$TMP/tmp/state/prediction_worker.paused"
out=$(_build_comment_ops_context main)
case "$out" in *"チャネルポイント予想(サナエトークン): いまは停止中"*) ok "予想停止中は明示する" ;; *) not_ok "予想停止中の文言: $out" ;; esac
rm -f "$TMP/tmp/state/prediction_worker.paused"

# --- 3. ops_brief.md の見出しが「直近の裏側の改修」として入り、件数上限で切られる ---
cat >"$TMP/prompts/ops_brief.md" <<'BRIEF'
# 自動生成 (このコメント行は読み飛ばす)
- ニュース重複の修正
- Bluesky 告知の実装
- ポッドキャストの保持日数変更
- 4件目は上限で捨てられるはず
BRIEF
out=$(_build_comment_ops_context main)
case "$out" in *"ニュース重複の修正"*) ok "brief 1件目が入る" ;; *) not_ok "brief 1件目欠落: $out" ;; esac
case "$out" in *"ポッドキャストの保持日数変更"*) ok "brief 3件目が入る" ;; *) not_ok "brief 3件目欠落: $out" ;; esac
case "$out" in *"4件目は上限"*) not_ok "brief が既定3件を超えて入った: $out" ;; *) ok "brief は既定3件で打ち切る" ;; esac
case "$out" in *"自動生成 (このコメント行"*) not_ok "brief の # コメント行が入った" ;; *) ok "brief の # コメント行は無視する" ;; esac

# --- 4. 全体が文字数上限で必ず切られる（プロンプト肥大の防止） ---
long_body=$(python3 -c 'print("あ" * 400)')
python3 - "$CODEX_WORK_OVERLAY_STATE_FILE" "$long_body" <<'PY'
import json, sys
with open(sys.argv[1], "w", encoding="utf-8") as f:
    json.dump({"active": True, "ts": 1, "title": "長い作業", "body": sys.argv[2]}, f, ensure_ascii=False)
PY
out=$(COMMENT_OPS_CONTEXT_MAX_CHARS=300 _build_comment_ops_context main)
len=$(printf '%s' "$out" | python3 -c 'import sys;print(len(sys.stdin.read()))')
[ "$len" -le 301 ] && ok "上限 300 で切られる (len=$len)" || not_ok "上限を超えた (len=$len)"
out=$(_build_comment_ops_context main)
len=$(printf '%s' "$out" | python3 -c 'import sys;print(len(sys.stdin.read()))')
[ "$len" -le 901 ] && ok "既定上限 900 以内 (len=$len)" || not_ok "既定上限を超えた (len=$len)"

# --- 5. テンプレート側に ${comment_ops_context} が埋め込まれている ---
for t in comment_template.md comment_response.md comment_response_default.md comment_response_game.md comment_response_chitchat.md comment_response_raid.md; do
	if grep -q '\${comment_ops_context}' "$ROOT/prompts/$t"; then
		ok "prompts/$t に comment_ops_context がある"
	else
		not_ok "prompts/$t に comment_ops_context が無い"
	fi
done

# --- 6. comment.sh の envsubst 許可リストへ入っている（入れ忘れると変数が生文字で残る） ---
subst_lines=$(grep -c 'envsubst .*\${comment_ops_context}' "$SRC")
[ "$subst_lines" -eq 3 ] && ok "envsubst 3箇所すべてに登録済み" || not_ok "envsubst の登録数が $subst_lines (期待 3)"
grep -q 'game_state_context comment_ops_context ' "$SRC" && ok "export 済み" || not_ok "comment_ops_context が export されていない"

# --- 7. レイドの自配信紹介がテンプレ直書きではなくメモ由来になっている ---
raid="$ROOT/prompts/comment_response_raid.md"
grep -q '\${_comment_channel_intro}' "$raid" && ok "レイドテンプレに紹介メモがある" || not_ok "レイドテンプレに紹介メモが無い"
grep -q "we stream various content from speedruns" "$raid" && not_ok "レイドテンプレに古い自配信紹介の直書きが残っている" || ok "古い自配信紹介の直書きが消えている"
grep -q "only plays the sequel" "$raid" && not_ok "レイドテンプレに古いメリケンAI条件の直書きが残っている" || ok "古いメリケンAI条件の直書きが消えている"
grep -q "戦略改善モードの時だけ続編" "$ROOT/prompts/comment_template.md" && not_ok "comment_template に古いメリケンAI条件が残っている" || ok "comment_template の古いメリケンAI条件が消えている"
grep -q "1日1回の短い枠だけソ連ゲーム91" "$ROOT/prompts/comment_channel_intro_main.md" && not_ok "紹介メモがメリケンAIの登場条件を直書きしている" || ok "紹介メモは登場条件を直書きせず運用状況メモへ委ねる"
for f in comment_channel_intro_main.md comment_channel_intro_soren91.md; do
	if grep -q "Twitch・YouTube・Kick" "$ROOT/prompts/$f"; then
		ok "prompts/$f が3プラットフォーム同時配信を伝える"
	else
		not_ok "prompts/$f に同時配信の記載が無い"
	fi
done

# --- 8. ゲーム/切替の文脈（メイン画面のゲーム・ソレン文脈への切返し） ---
# docich canonical が「いま画面に出ているゲーム」を持ち、Soren lifecycle が切替の進行を
# 持つ。コメント返しはメイン画面のゲームに合わせ、ソレンゲームの話は画面が変わっても
# ソレン文脈で答えること。ゲームが別ゲームのときは「メイン画面はソ連ゲーム本編」前提の
# メリケンAI補足を出さない。
SOREN_LIFECYCLE_DIR="$TMP/tmp/state/game_lifecycle"
SOREN_GAME_STATE_FILE="$TMP/game_state.json"
SOREN_GAME_COUNT_FILE="$TMP/game_count.txt"
SOREN_IMPROVE_PAUSED_FILE="$TMP/tmp/state/improve_daemon.paused"
DOCICH_GAME_SWITCH_CANONICAL_FILE="$TMP/canonical.json"
mkdir -p "$SOREN_LIFECYCLE_DIR"
export SOREN_LIFECYCLE_DIR SOREN_GAME_STATE_FILE SOREN_GAME_COUNT_FILE SOREN_IMPROVE_PAUSED_FILE DOCICH_GAME_SWITCH_CANONICAL_FILE

printf '%s' '{"active":{"game":"robots","generation":16},"phase":"ready"}' >"$DOCICH_GAME_SWITCH_CANONICAL_FILE"
printf '%s' '{"request_id":"u1","schema":1}' >"$SOREN_LIFECYCLE_DIR/request.json"
printf '%s' '{"request_id":"u1","schema":1,"status":"stopped"}' >"$SOREN_LIFECYCLE_DIR/ack.json"
printf '%s' '{"state":"STOP","score":2125}' >"$SOREN_GAME_STATE_FILE"
printf '%s' '49049' >"$SOREN_GAME_COUNT_FILE"
touch "$SOREN_IMPROVE_PAUSED_FILE"
out=$(_build_comment_ops_context main)
case "$out" in *"いまのメイン画面: robots"*) ok "メイン画面が robots と出る" ;; *) not_ok "メイン画面行: $out" ;; esac
case "$out" in *"画面が違ってもソレンゲームの話として答える"*) ok "ソレン文脈への切返しルールが入る" ;; *) not_ok "切返しルール欠落: $out" ;; esac
case "$out" in *"切替完了"*"停止済み"*) ok "lifecycle stopped を切替完了として出す" ;; *) not_ok "stopped 文言: $out" ;; esac
case "$out" in *"最終試合score=2125"*) ok "ソレン最終試合のスコアが入る" ;; *) not_ok "最終試合スコア欠落: $out" ;; esac
case "$out" in *"通算49049試合"*) ok "通算試合数が入る" ;; *) not_ok "通算試合数欠落: $out" ;; esac
case "$out" in *"戦略改善: 手動休止中"*) ok "operator pause を優先して出す" ;; *) not_ok "手動休止文言: $out" ;; esac
case "$out" in *"メイン画面は私(中華AI)のソレンゲーム本編"*) not_ok "別ゲーム表示中にソレン本編前提が出た" ;; *) ok "別ゲーム表示中はソレン本編前提を出さない" ;; esac

# boundary 進行中
printf '%s' '{"request_id":"u2","schema":1}' >"$SOREN_LIFECYCLE_DIR/request.json"
printf '%s' '{"request_id":"u2","schema":1,"status":"boundary"}' >"$SOREN_LIFECYCLE_DIR/ack.json"
rm -f "$SOREN_IMPROVE_PAUSED_FILE"
out=$(_build_comment_ops_context main)
case "$out" in *"試合境界を確認済み"*) ok "boundary を切替手続き中として出す" ;; *) not_ok "boundary 文言: $out" ;; esac

# docich idle + lifecycle 無し = ソレンゲーム継続中(従来前提)
rm -f "$SOREN_LIFECYCLE_DIR/request.json" "$SOREN_LIFECYCLE_DIR/ack.json" "$DOCICH_GAME_SWITCH_CANONICAL_FILE"
out=$(_build_comment_ops_context main)
case "$out" in *"メイン画面: ソレンゲーム(docich管理外で継続中)"*) ok "lifecycle 無しはソレン継続中" ;; *) not_ok "ソレン継続中文言: $out" ;; esac
case "$out" in *"メリケンAIは待機中"*) ok "ソレン画面ではメリケンAI補足を出す" ;; *) not_ok "メリケン補足欠落: $out" ;; esac

# 別ゲーム表示中でも文字数上限は効く
printf '%s' '{"active":{"game":"robots","generation":16},"phase":"ready"}' >"$DOCICH_GAME_SWITCH_CANONICAL_FILE"
printf '%s' '{"request_id":"u1","schema":1}' >"$SOREN_LIFECYCLE_DIR/request.json"
printf '%s' '{"request_id":"u1","schema":1,"status":"stopped"}' >"$SOREN_LIFECYCLE_DIR/ack.json"
out=$(COMMENT_OPS_CONTEXT_MAX_CHARS=300 _build_comment_ops_context main)
[ "${#out}" -le 320 ] && ok "別ゲーム表示中も文字数上限で切る" || not_ok "文字数上限が効いていない (${#out})"

[ "$FAIL" -eq 0 ] && echo "PASS" || echo "FAIL"
exit "$FAIL"
