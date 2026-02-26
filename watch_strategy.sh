#!/bin/bash
# watch_strategy.sh — strategy.py の変更を監視し、AIに解説させてsayで読み上げる
#
# 使い方: ./watch_strategy.sh
# 停止: Ctrl+C

set -euo pipefail
cd "$(dirname "$0")"

STRATEGY="strategy.py"
VERSIONS_DIR="strategy_versions"
BEST_SCORE_FILE="best_score.txt"
COMMENTARY_FILE="tmp/strategy_commentary.txt"

# --- 設定 ---
AI_AGENT="zai"
AI_FALLBACK="glmflash"
SAY_VOICE=""  # macOS say のボイス（空ならデフォルト）
SAY_RATE=250  # 読み上げ速度

mkdir -p tmp

# --- ユーティリティ ---
log() {
  echo "[$(date '+%H:%M:%S')] $*"
}

# 最新N個のバージョンファイルを取得（v0XX_score* のみ、新しい順）
get_recent_versions() {
  local count="${1:-3}"
  ls -t "$VERSIONS_DIR"/v[0-9]*_score*_strategy.py 2>/dev/null | head -n "$count"
}

# ベストスコアのstrategyファイルを取得
get_best_strategy() {
  if [[ -f "$BEST_SCORE_FILE" ]]; then
    local best_score
    best_score=$(cat "$BEST_SCORE_FILE" | tr -d '[:space:]')
    local best_file="$VERSIONS_DIR/best_score${best_score}_strategy.py"
    if [[ -f "$best_file" ]]; then
      echo "$best_file"
      return
    fi
  fi
  # フォールバック: best_score* の最新
  ls -t "$VERSIONS_DIR"/best_score*_strategy.py 2>/dev/null | head -1
}

# AIに解説を生成させる
generate_commentary() {
  local diff_content="$1"
  local context="$2"

  local prompt
  prompt=$(cat <<'PROMPT_END'
あなたはゲーム実況の解説者です。
「ソ連スイカゲーム」というパズルゲームをAIが自動プレイしています。
このゲームは、同じ種類のピースをくっつけると合体してより大きなピースになる落ちものパズルです。
うまく合体させてコンボをつなぐと高得点になります。

いまAIの作戦（strategy.py）が自動的に書き換わりました。
以下の差分と参考情報をもとに、どんな工夫がされたのかを、ゲームを知らない一般の人にもわかるように解説してください。

【出力ルール】
- 3〜5文で簡潔にまとめる（音声読み上げに使います）
- プログラミング用語やコード上の変数名は絶対に使わない
- 「今回の作戦変更では〜」のような語り口で始める
- たとえ話や日常的な言葉を使って、何が変わったか直感的にわかるようにする
- 過去の作戦や最高スコアとの比較があれば「前回は〜だったのが、今回は〜に変わった」のように触れる
- スコアが上がりそうか下がりそうか、期待感を一言添える
- マークダウンや記号は使わない。読み上げ用のプレーンテキストのみ
- 出力は解説文のみ。前置きや補足説明は不要

PROMPT_END
)

  prompt+=$'\n\n【差分】\n'"$diff_content"
  prompt+=$'\n\n【参考: 過去バージョンとの比較コンテキスト】\n'"$context"

  log "AI解説生成中 (agent: $AI_AGENT)..."

  local result
  if result=$(opencode run --agent "$AI_AGENT" "$prompt" 2>/dev/null); then
    if [[ -n "$result" ]]; then
      echo "$result"
      return 0
    fi
  fi

  log "フォールバック: $AI_FALLBACK で再試行..."
  if result=$(opencode run --agent "$AI_FALLBACK" "$prompt" 2>/dev/null); then
    if [[ -n "$result" ]]; then
      echo "$result"
      return 0
    fi
  fi

  log "AI解説生成に失敗しました"
  echo "strategy.pyが更新されましたが、解説の生成に失敗しました。"
  return 1
}

# say で読み上げ
speak() {
  local text="$1"
  local say_args=(-r "$SAY_RATE")
  if [[ -n "$SAY_VOICE" ]]; then
    say_args+=(-v "$SAY_VOICE")
  fi
  say "${say_args[@]}" "$text" &
}

# --- メイン処理: strategy.py 変更時のハンドラ ---
on_strategy_changed() {
  log "strategy.py の変更を検出!"

  # 1. 直前のバージョンとのdiff生成
  local recent_versions
  recent_versions=$(get_recent_versions 3)
  local latest_version
  latest_version=$(echo "$recent_versions" | head -1)

  local diff_content=""
  if [[ -n "$latest_version" && -f "$latest_version" ]]; then
    diff_content=$(diff -u "$latest_version" "$STRATEGY" 2>/dev/null || true)
    if [[ -z "$diff_content" ]]; then
      log "差分なし（最新バージョンと同一）。スキップ。"
      return
    fi
  else
    diff_content="(比較対象のバージョンファイルが見つかりません。現在の strategy.py の内容を解説します)"
    diff_content+=$'\n\n'"$(head -80 "$STRATEGY")"
  fi

  # 2. 過去3バージョンとベストの比較コンテキスト作成
  local context=""

  # 過去バージョンの変更履歴ヘッダー抽出
  local i=0
  while IFS= read -r vfile; do
    [[ -z "$vfile" ]] && continue
    i=$((i + 1))
    local vname
    vname=$(basename "$vfile")
    # 変更履歴セクションを抽出
    local changelog
    changelog=$(grep -A5 '変更履歴' "$vfile" 2>/dev/null | head -8 || echo "(履歴なし)")
    context+="--- 過去バージョン${i}: ${vname} ---"$'\n'
    context+="$changelog"$'\n\n'
  done <<< "$recent_versions"

  # ベストスコアstrategyとの比較
  local best_file
  best_file=$(get_best_strategy)
  if [[ -n "$best_file" && -f "$best_file" ]]; then
    local best_name
    best_name=$(basename "$best_file")
    local best_changelog
    best_changelog=$(grep -A5 '変更履歴' "$best_file" 2>/dev/null | head -8 || echo "(履歴なし)")
    local best_diff
    best_diff=$(diff -u "$best_file" "$STRATEGY" 2>/dev/null | head -60 || true)
    context+="--- ベストスコア版: ${best_name} ---"$'\n'
    context+="$best_changelog"$'\n'
    context+="差分(先頭60行):"$'\n'"$best_diff"$'\n'
  fi

  # 現在のベストスコア
  if [[ -f "$BEST_SCORE_FILE" ]]; then
    context+=$'\n'"現在のベストスコア: $(cat "$BEST_SCORE_FILE")"
  fi

  # 3. AI解説生成
  local commentary
  commentary=$(generate_commentary "$diff_content" "$context")

  # 4. 保存 & 表示
  echo "$commentary" > "$COMMENTARY_FILE"
  log "--- AI解説 ---"
  echo "$commentary"
  log "---------------"

  # 5. 読み上げ
  speak "$commentary"
  log "読み上げ開始"
}

# --- メインループ ---
log "strategy.py を監視開始..."
log "Ctrl+C で停止"

# fswatch で strategy.py を監視
# -1: 1イベントずつ処理（バッチしない）
# --event Updated: ファイル更新イベントのみ
fswatch --event Updated -0 "$STRATEGY" | while IFS= read -r -d '' event; do
  # 短い間隔で複数イベントが来ることがあるのでデバウンス
  sleep 1
  # 溜まったイベントを消費
  while IFS= read -r -d '' -t 0.1 _; do :; done

  on_strategy_changed
done
