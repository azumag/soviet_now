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

# ゲーム範囲を算出
GAME_NUMS_LIST=()
for hf in $HISTORY_FILES; do
	[ -f "$hf" ] && GAME_NUMS_LIST+=("$hf")
done
NUM_GAMES=${#GAME_NUMS_LIST[@]}
[ "$NUM_GAMES" -lt 1 ] && NUM_GAMES=1

# --- Phase C: 分析 & 戦略改善 ---

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

# AI で strategy.py 改善
# ゲーム実行中にstrategy.pyが壊れないよう、一時ファイルで作業し、
# バリデーション成功後にのみアトミックに差し替える
strategy_diff=""
log "[IMPROVE] AI改善 (${NUM_GAMES}試合分)..."

STAGING_FILE="${STRATEGY_FILE}.staging"
cp "$STRATEGY_FILE" "$STAGING_FILE"

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

# 参照データ (AIにはstagingファイルを編集させる)
improve_ref_files="$STAGING_FILE $batch_summary_file"
[ -n "$best_game_path" ] && [ -f "$best_game_path" ] && improve_ref_files="$improve_ref_files $best_game_path"
improve_ref_files="$improve_ref_files $GAME_STATE $past_strategy_files $hall_of_fame_files"

for retry in $(seq 1 3); do
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
- **既存コードは一切削除するな。新しいロジックを追加する形で改善せよ**
- **行数ハードゲート: コード行数が元の80%未満だと自動リジェクトされる。コードを短くする改善は通らない**
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
	if diff -q "$STRATEGY_FILE" "$STAGING_FILE" >/dev/null 2>&1; then
		log "[IMPROVE] 差分なし (retry $retry/3)"
		VALIDATE_ERROR="AIが strategy.py.staging を変更しなかった。必ず strategy.py.staging を編集して改善すること。"
		continue
	fi

	# 行数チェック: 既存ロジックの無断削除を防止
	original_lines=$(grep -c '' "$STRATEGY_FILE" 2>/dev/null || echo 0)
	staging_lines=$(grep -c '' "$STAGING_FILE" 2>/dev/null || echo 0)
	# コメント行を除いたコード行数で比較
	original_code=$(grep -v '^\s*#' "$STRATEGY_FILE" | grep -v '^\s*$' | wc -l | tr -d ' ')
	staging_code=$(grep -v '^\s*#' "$STAGING_FILE" | grep -v '^\s*$' | wc -l | tr -d ' ')
	min_code=$((original_code * 80 / 100))  # 80%未満ならリジェクト
	if [ "$staging_code" -lt "$min_code" ]; then
		log "[IMPROVE] コード行数が大幅に減少: ${original_code}行→${staging_code}行 (最低${min_code}行必要)"
		VALIDATE_ERROR="コード行数が大幅に減少した (${original_code}→${staging_code}行)。既存ロジックを削除せず、新しいロジックを追加する形で改善せよ。削除は avg_score_delta が負のロジックのみ許可。"
		continue
	fi

	# stagingファイルを直接バリデーション (strategy.pyには触らない)
	if validate_strategy "$STAGING_FILE"; then
		log "[IMPROVE] バリデーション成功"
		strategy_diff=$(diff -u "$STRATEGY_FILE" "$STAGING_FILE" 2>/dev/null || true)
		real_changes=$(echo "$strategy_diff" | grep '^[+-]' | grep -v '^[+-][+-][+-]' | grep -v '^[+-][[:space:]]*$' | wc -l | tr -d ' ')
		[ "${real_changes:-0}" -lt 2 ] && strategy_diff=""
		# バリデーション成功 → アトミックに差し替え
		mv "$STAGING_FILE" "$STRATEGY_FILE"
		python3 trim_changelog.py "$STRATEGY_FILE" 3 2>/dev/null
		improve_ok=true
		break
	fi
done

# 失敗してもstrategy.pyは一切触っていないので復元不要
rm -f "$STAGING_FILE"

# git commit
# ゲーム範囲を算出してコミットメッセージに含める
first_score=$(echo "$SCORES" | awk '{print $1}')
last_score=$(echo "$SCORES" | awk '{print $NF}')
if [ "$NUM_GAMES" -eq 1 ]; then
	git add -A
	git commit -m "eloop Improve after game #${GAME_NUM_SNAPSHOT}" 2>/dev/null || true
else
	git add -A
	git commit -m "eloop Improve after ${NUM_GAMES} games (scores: ${SCORES})" 2>/dev/null || true
fi

# --- Phase D: ラジオトーク生成 ---
best_score_now=$(cat best_score.txt 2>/dev/null || echo 0)
start_radio_talk "${last_score:-0}" "$TURNS_SNAPSHOT" "$GAME_NUM_SNAPSHOT" "$best_score_now" \
	"$strategy_diff" "$SOVIET" "$SCORES" "" "" ""

# ラジオトーク終了を待つ (サブシェル内で完結するため)
wait
