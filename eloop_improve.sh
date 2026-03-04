#!/bin/bash
# eloop_improve.sh - バックグラウンド改善サブプロセス
#
# soren_loop.sh から trigger_adaptive_improvement() 経由でバックグラウンド実行される。
# Phase C: バッチサマリー生成 → AI改善 → バリデーション → git commit
# Phase D: ラジオトーク生成
#
# Usage: ./eloop_improve.sh <history_files> <scores> <soviet> <game_num> <turns>

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

source ./eloop_lib.sh

# --- 引数 ---
HISTORY_FILES="$1"
SCORES="$2"
SOVIET="$3"
GAME_NUM_SNAPSHOT="$4"
TURNS_SNAPSHOT="$5"

# 進捗モニタリング用メタ情報
IMPROVE_SELF_PID="${BASHPID:-$$}"
IMPROVE_STATE_JSON=$(_read_improve_state)
IMPROVE_BASE_HASH=$(echo "$IMPROVE_STATE_JSON" | python3 -c "import json,sys; print(json.load(sys.stdin).get('strategy_hash_before',''))" 2>/dev/null || echo "")
[ -z "$IMPROVE_BASE_HASH" ] && IMPROVE_BASE_HASH=$(md5 -q "$STRATEGY_FILE" 2>/dev/null | cut -c1-8)
IMPROVE_STARTED_AT=$(echo "$IMPROVE_STATE_JSON" | python3 -c "import json,sys,time; print(int(json.load(sys.stdin).get('started_at', int(time.time()))))" 2>/dev/null || date +%s)
RUN_CMD_LOG_FILE="${RUN_CMD_LOG_FILE:-$IMPROVE_AI_LOG_FILE}"
mkdir -p "$(dirname "$RUN_CMD_LOG_FILE")" 2>/dev/null || true
_trim_log_file "$RUN_CMD_LOG_FILE" "$IMPROVE_AI_LOG_KEEP_LINES" "$IMPROVE_AI_LOG_TRIM_LINES"
printf '[%s] [IMPROVE] attached pid=%s game=%s\n' "$(date '+%H:%M:%S')" "$IMPROVE_SELF_PID" "${GAME_NUM_SNAPSHOT:-?}" >>"$RUN_CMD_LOG_FILE" 2>/dev/null || true
export RUN_CMD_LOG_FILE

_improve_progress() {
	local phase="$1" progress="$2" detail="$3"
	_write_improve_state "running" "$IMPROVE_SELF_PID" "$IMPROVE_BASE_HASH" "$phase" "$progress" "$detail" "$IMPROVE_STARTED_AT"
}

# ゲーム範囲を算出
GAME_NUMS_LIST=()
for hf in $HISTORY_FILES; do
	[ -f "$hf" ] && GAME_NUMS_LIST+=("$hf")
done
NUM_GAMES=${#GAME_NUMS_LIST[@]}
[ "$NUM_GAMES" -lt 1 ] && NUM_GAMES=1

# --- Phase C: 分析 & 戦略改善 ---
_improve_progress "summary" "5" "building_batch_summary"

# バッチサマリー生成
batch_summary_file="tmp/batch_summary.txt"
if [ -n "$HISTORY_FILES" ]; then
	log "[IMPROVE] サマリー生成中 (${NUM_GAMES}試合)..."
	python3 batch_summary.py $HISTORY_FILES > "$batch_summary_file" 2>/dev/null

	best_game_file=$(grep '^===BEST_FILE===' "$batch_summary_file" | sed 's/===BEST_FILE===//')
	worst_game_file=$(grep '^===WORST_FILE===' "$batch_summary_file" | sed 's/===WORST_FILE===//')
	best_game_path="$HISTORY_DIR/$best_game_file"
	worst_game_path="$HISTORY_DIR/$worst_game_file"
else
	echo "(no game data)" > "$batch_summary_file"
	best_game_path=""
	worst_game_path=""
fi
_improve_progress "summary_done" "15" "batch_summary_ready"

# AI で strategy.py 改善
# ゲーム実行中にstrategy.pyが壊れないよう、一時ファイルで作業し、
# バリデーション成功後にのみアトミックに差し替える
strategy_diff=""
log "[IMPROVE] AI改善 (${NUM_GAMES}試合分)..."
_improve_progress "ai_prepare" "20" "prepare_staging"

STAGING_FILE="${STRATEGY_FILE}.staging"
cp "$STRATEGY_FILE" "$STAGING_FILE"

# リバート用に改善前のstrategy.pyを保存
cp "$STRATEGY_FILE" "tmp/revert_strategy.py"

# 改善前のdecide()ハッシュを記録
HASH_BEFORE=$(python3 extract_decide_hash.py "$STRATEGY_FILE" 2>/dev/null || echo "")

improve_ok=false

# 直近10バージョン + 殿堂入り戦略
past_strategy_files=""
for vf in $(ls -1t "$STRATEGY_VERSIONS_DIR"/v[0-9]*_strategy.py 2>/dev/null | head -10); do
	past_strategy_files="$past_strategy_files $vf"
done
hall_of_fame_files=""
for hf in "$STRATEGY_VERSIONS_DIR"/best_score*_strategy.py; do
	[ -f "$hf" ] && hall_of_fame_files="$hall_of_fame_files $hf"
done

# 変更履歴ファイル (振り子パターン防止)
CHANGE_LOG_FILE="tmp/change_log.txt"

# 参照データ (AIにはstagingファイルを編集させる)
improve_ref_files="$STAGING_FILE $batch_summary_file"
[ -f "$CHANGE_LOG_FILE" ] && improve_ref_files="$improve_ref_files $CHANGE_LOG_FILE"
[ -f "tmp/advice.md" ] && [ -s "tmp/advice.md" ] && improve_ref_files="$improve_ref_files tmp/advice.md"
[ -n "$worst_game_path" ] && [ -f "$worst_game_path" ] && improve_ref_files="$improve_ref_files $worst_game_path"
improve_ref_files="$improve_ref_files $GAME_STATE $past_strategy_files $hall_of_fame_files"

for retry in $(seq 1 3); do
	_improve_progress "ai_retry${retry}" "$((25 + (retry - 1) * 15))" "ai_edit_and_validate"
	if [ "$retry" -eq 1 ]; then
		run_ai IMPROVE "$MODEL_PRIMARY" "$MODEL_FALLBACK" \
			prompts/improve_strategy.md "$STAGING_FILE" \
			$improve_ref_files
	else
		log "[IMPROVE] リトライ $retry/3 (前回エラー: ${VALIDATE_ERROR:0:80})"

		# stagingをオリジナルに戻してからリトライ
		cp "$STRATEGY_FILE" "$STAGING_FILE"

		fix_prompt_file=$(mktemp /tmp/eloop_fix_prompt.XXXXXX)
		cat > "$fix_prompt_file" <<FIXEOF
前回の strategy.py.staging 改善でバリデーションが失敗した。strategy.py.staging はオリジナルに戻してある。
以下のエラーを踏まえて、改めて改善せよ。

## 前回のエラー
$VALIDATE_ERROR

## 修正ルール
- strategy.py.staging を改善して上記エラーを回避せよ
- 1回の改善で1つの変更のみ。シンプルに保て
- decide(game_state, analysis) のシグネチャは変更禁止
- if __name__ == "__main__" ブロックは変更禁止
- decide() は必ず {"x": float, "reason": str} を返すこと
- Write ツールで strategy.py.staging に書き込むこと
FIXEOF
		run_ai "FIX(${retry})" "$MODEL_PRIMARY" "$MODEL_FALLBACK" \
			"$fix_prompt_file" "$STAGING_FILE" \
			$improve_ref_files
		rm -f "$fix_prompt_file"
	fi

	# 差分チェック
	_improve_progress "validate_retry${retry}" "$((30 + (retry - 1) * 15))" "diff_and_validation_checks"
	if diff -q "$STRATEGY_FILE" "$STAGING_FILE" >/dev/null 2>&1; then
		log "[IMPROVE] 差分なし (retry $retry/3)"
		VALIDATE_ERROR="AIが strategy.py.staging を変更しなかった。必ず strategy.py.staging を編集して改善すること。"
		continue
	fi

	# stagingファイルを直接バリデーション (strategy.pyには触らない)
	if validate_strategy "$STAGING_FILE"; then
		log "[IMPROVE] バリデーション成功"

		# ハッシュベース反復防止: 最近リジェクトされたハッシュと同一なら拒否
		HASH_STAGING=$(python3 extract_decide_hash.py "$STAGING_FILE" 2>/dev/null || echo "")
		REJECTED_HASHES_FILE="tmp/rejected_hashes.txt"

		if [ -n "$HASH_STAGING" ] && [ -f "$REJECTED_HASHES_FILE" ]; then
			if grep -qF "$HASH_STAGING" "$REJECTED_HASHES_FILE"; then
				log "[IMPROVE] ハッシュ反復検出: $HASH_STAGING (過去にリジェクト済み)"
				VALIDATE_ERROR="この変更は過去にリジェクトされた戦略と同一 (hash=$HASH_STAGING)。別のアプローチを試せ。"
				continue
			fi
		fi

		# 改善前と同一ハッシュなら差分なしとして扱う
		if [ -n "$HASH_STAGING" ] && [ "$HASH_STAGING" = "$HASH_BEFORE" ]; then
			log "[IMPROVE] decide()本体に実質的変更なし (hash=$HASH_STAGING)"
			VALIDATE_ERROR="decide()関数の本体に実質的な変更がない (コメントのみの変更)。ロジックを変更せよ。"
			continue
		fi

		strategy_diff=$(diff -u "$STRATEGY_FILE" "$STAGING_FILE" 2>/dev/null || true)
		real_changes=$(echo "$strategy_diff" | grep '^[+-]' | grep -v '^[+-][+-][+-]' | grep -v '^[+-][[:space:]]*$' | wc -l | tr -d ' ')
		[ "${real_changes:-0}" -lt 2 ] && strategy_diff=""
		# 変更履歴ログに記録 (振り子パターン防止)
			if [ -n "$strategy_diff" ]; then
				{
				echo "=== $(date '+%Y-%m-%d %H:%M') Game#${GAME_NUM_SNAPSHOT} scores=${SCORES} ==="
				echo "$strategy_diff" | grep '^[+-]' | grep -v '^[+-][+-][+-]' | head -20
				echo ""
			} >> "$CHANGE_LOG_FILE"
			# 最新50エントリのみ保持
			if [ -f "$CHANGE_LOG_FILE" ] && [ "$(wc -l < "$CHANGE_LOG_FILE")" -gt 500 ]; then
				tail -500 "$CHANGE_LOG_FILE" > "$CHANGE_LOG_FILE.tmp"
				mv "$CHANGE_LOG_FILE.tmp" "$CHANGE_LOG_FILE"
			fi
			fi
			# バリデーション成功 → アトミックに差し替え
			_improve_progress "apply" "80" "apply_validated_strategy"
			mv "$STAGING_FILE" "$STRATEGY_FILE"
			python3 trim_changelog.py "$STRATEGY_FILE" 3 2>/dev/null
			improve_ok=true
			break
		fi
done

# 失敗してもstrategy.pyは一切触っていないので復元不要
_improve_progress "post_validate" "85" "finalizing"
rm -f "$STAGING_FILE"

# git commit
# ゲーム範囲を算出してコミットメッセージに含める
first_score=$(echo "$SCORES" | awk '{print $1}')
last_score=$(echo "$SCORES" | awk '{print $NF}')
_improve_progress "git_commit" "90" "commit_changes"
if [ "$NUM_GAMES" -eq 1 ]; then
	git add -A
	git commit -m "eloop Improve after game #${GAME_NUM_SNAPSHOT}" 2>/dev/null || true
else
	git add -A
	git commit -m "eloop Improve after ${NUM_GAMES} games (scores: ${SCORES})" 2>/dev/null || true
fi

# --- Phase D: 戦略解説コーナー (変更があった場合のみ) ---
if [ -n "$strategy_diff" ]; then
	_improve_progress "radio" "95" "strategy_commentary"
	best_score_now=$(cat best_score.txt 2>/dev/null || echo 0)
	start_radio_corner_strategy "$strategy_diff" "$SCORES" "$GAME_NUM_SNAPSHOT" "$best_score_now"
fi

_improve_progress "done" "100" "awaiting_harvest"
