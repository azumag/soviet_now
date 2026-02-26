#!/bin/bash
# watch_strategy.sh — strategy.py の変更を監視し、AIに解説させてsayで読み上げる
#
# 使い方: ./watch_strategy.sh
# 停止: Ctrl+C

set -uo pipefail
cd "$(dirname "$0")"

STRATEGY="strategy.py"
VERSIONS_DIR="strategy_versions"
BEST_SCORE_FILE="best_score.txt"
COMMENTARY_FILE="tmp/strategy_commentary.txt"

# --- 設定 ---
AI_AGENT="zai"
AI_FALLBACK="glmflash"
SAY_VOICE=""  # macOS say のボイス（空ならデフォルト）
SAY_RATE=120  # 読み上げ速度
LOCK_FILE="tmp/.watch_strategy.lock"

mkdir -p tmp

# Ctrl+C で say も止める
cleanup() {
  killall say 2>/dev/null
  rm -f "$LOCK_FILE"
  exit 0
}
trap cleanup INT TERM

# --- ユーティリティ ---
log() {
  echo "[$(date '+%H:%M:%S')] $*" >&2
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

# ANSIエスケープコード・制御文字を除去（macOS互換: perl使用）
strip_ansi() {
  perl -pe 's/\e\[[0-9;]*[a-zA-Z]//g; s/[\x00-\x09\x0b-\x0d\x0e-\x1f]//g' | tr -d '\r'
}

# opencode run を疑似TTY付きで実行し、AI応答テキストだけを返す
run_opencode() {
  local agent="$1"
  local prompt_file="$2"
  local raw_file
  raw_file=$(mktemp /tmp/oc_raw_XXXXXXXX)

  # script で疑似TTY を与えて実行（opencode は TTY がないとハングする）
  script -q "$raw_file" opencode run --agent "$agent" "$(cat "$prompt_file")" > /dev/null 2>&1

  # ANSIエスケープ除去 → ヘッダー行("> agent · model")を除去 → 空行トリム
  local cleaned
  cleaned=$(cat "$raw_file" \
    | strip_ansi \
    | grep -v '^>' \
    | grep -v '^\^D' \
    | sed '/^[[:space:]]*$/d' \
  )
  rm -f "$raw_file"
  echo "$cleaned"
}

# AIに解説を生成させる
generate_commentary() {
  local diff_content="$1"
  local context="$2"

  # プロンプトをファイルに書き出す（引数が長すぎる場合の対策）
  local prompt_file
  prompt_file=$(mktemp /tmp/oc_prompt_XXXXXXXX)

  cat > "$prompt_file" <<'PROMPT_END'
あなたは深夜のゲーム実況ラジオのパーソナリティです。
一人でずっと喋り続けるのが得意で、脱線しまくるけど最終的にはちゃんと戻ってくるタイプです。
リスナーはAIがパズルゲームを自動プレイしているのを眺めながら、あなたのトークを聞いています。

【ゲーム概要】
「ソ連スイカゲーム」は、旧ソ連の構成国の国旗をモチーフにした落ちものパズルです。
同じ国旗同士をくっつけると合体して、一つ上のレベルの国旗に進化します。
最終目標は、ロシア同士を合体させて「ソ連」を完成させることです。

国旗の進化ルート（小さい順）:
  レベル1 アルメニア → レベル2 エストニア → レベル3 ラトビア → レベル4 リトアニア
  → レベル5 グルジア → レベル6 アゼルバイジャン → レベル7 タジキスタン
  → レベル8 キルギス → レベル9 ベラルーシ → レベル10 ウズベキスタン
  → レベル11 トルクメニスタン → レベル12 ウクライナ → レベル13 カザフスタン
  → レベル14 ロシア → レベル15 ソ連（ゴール!）

レベル1〜11は空から降ってきます。レベル12以降は合体でしか出現しません。
ピースの形は各国の国土の形をしています。

いまAIの作戦（strategy.py）が自動的に書き換わりました。
以下の差分と参考情報をもとに、10分くらい読み上げられる長さのトークを書いてください。

【トークの構成（この順番で、全部入れること）】

1. 導入: 「さあ、作戦が更新されましたよ」的な入り
2. 作戦変更の解説: 何が変わったか、国名を使って具体的に説明
3. 脱線トーク1: 差分に出てくる国にまつわる豆知識、雑学、歴史エピソード、名物料理、有名人の話など
4. 過去の作戦との比較: 前の作戦はどうだったか、最高スコアの作戦とどう違うか
5. 脱線トーク2: ことわざや格言を引用して作戦変更に例える、または関連する最近のニュースや時事ネタ
6. 予想と期待: この作戦でスコアは上がりそうか、ソ連完成に近づけるか
7. 脱線トーク3: AIが自分で自分の作戦を書き換えるということ自体への感想、哲学的な考察、冗談
8. 愚痴パート: スコアが伸び悩んでいたら愚痴る、AIに文句を言う、励ます、同情する
9. 応援・締め: 次の試合への期待、リスナーへの語りかけ

【出力ルール】
- 2000〜3000文字程度の長さにする。短くしないこと。とにかくたくさん喋る
- プログラミング用語やコード上の変数名は絶対に使わない
- ピースは必ず国名で呼ぶこと（例:「アルメニア同士を合体させてエストニアにする」）
- 話し言葉で書く。「ですます」と「だよね」を混ぜたカジュアルなトーン
- 「ちなみに」「そういえば」「話は変わるんですけど」「いやでもさ」などの接続詞で脱線を自然につなぐ
- 感情を込める。嬉しい、悔しい、驚き、呆れ、期待、不安、笑いなどを表現する
- 冗談やダジャレも入れてOK。スベっても気にしない
- ソ連のゲームなので、ところどころ共産主義っぽい言い回しをさりげなく混ぜる（例:「同志」「人民」「五カ年計画」「労働者の勝利」「プロレタリアート」「革命」など）。やりすぎず、スパイス程度に
- マークダウンや記号は使わない。読み上げ用のプレーンテキストのみ
- 出力はトーク本文のみ。前置きや補足説明は不要

PROMPT_END

  echo "" >> "$prompt_file"
  echo "【差分】" >> "$prompt_file"
  echo "$diff_content" >> "$prompt_file"
  echo "" >> "$prompt_file"
  echo "【参考: 過去バージョンとの比較コンテキスト】" >> "$prompt_file"
  echo "$context" >> "$prompt_file"

  log "AI解説生成中 (agent: $AI_AGENT)..."

  local result
  result=$(run_opencode "$AI_AGENT" "$prompt_file")
  if [[ -n "$result" ]]; then
    rm -f "$prompt_file"
    echo "$result"
    return 0
  fi

  log "フォールバック: $AI_FALLBACK で再試行..."
  result=$(run_opencode "$AI_FALLBACK" "$prompt_file")
  if [[ -n "$result" ]]; then
    rm -f "$prompt_file"
    echo "$result"
    return 0
  fi

  rm -f "$prompt_file"
  log "AI解説生成に失敗しました"
  echo "strategy.pyが更新されましたが、解説の生成に失敗しました。"
  return 1
}

# say で読み上げ（前の再生を止めてから新しく開始）
speak() {
  local text="$1"
  # 前の say が動いていたら全部止める
  killall say 2>/dev/null
  local say_args=(-r "$SAY_RATE")
  if [[ -n "$SAY_VOICE" ]]; then
    say_args+=(-v "$SAY_VOICE")
  fi
  say "${say_args[@]}" "$text" &
}

# --- メイン処理: strategy.py 変更時のハンドラ ---
on_strategy_changed() {
  # ロックで多重実行を防止
  if [[ -f "$LOCK_FILE" ]]; then
    log "処理中のためスキップ"
    return
  fi
  touch "$LOCK_FILE"
  trap 'rm -f "$LOCK_FILE"' RETURN

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
# --event Updated: ファイル更新イベントのみ
fswatch --event Updated "$STRATEGY" | while IFS= read -r event; do
  # デバウンス: 1秒待って連続イベントをまとめる
  sleep 1

  on_strategy_changed
done
