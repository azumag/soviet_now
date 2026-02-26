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
PAST_TOPICS_FILE="tmp/past_watch_topics.txt"
LOCK_FILE="tmp/.watch_strategy.lock"
LAST_HASH_FILE="tmp/.watch_strategy_hash"
SAY_PID_FILE="tmp/.watch_say.pid"

# --- 設定 ---
AI_AGENT="zai"
AI_FALLBACK="glmflash"
SAY_VOICE=""  # macOS say のボイス（空ならデフォルト）
SAY_RATE=120  # 読み上げ速度
MIN_COMMENTARY_LEN=200  # これより短い生成結果はゴミとみなす

mkdir -p tmp

# Ctrl+C でクリーンアップ
cleanup() {
  _kill_my_say
  rm -f "$LOCK_FILE"
  exit 0
}
trap cleanup INT TERM

# --- say 管理（自分が起動したsayだけ制御する） ---
_kill_my_say() {
  if [[ -f "$SAY_PID_FILE" ]]; then
    local pid
    pid=$(cat "$SAY_PID_FILE" 2>/dev/null)
    if [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null; then
      kill "$pid" 2>/dev/null
      wait "$pid" 2>/dev/null
    fi
    rm -f "$SAY_PID_FILE"
  fi
}

_start_say() {
  local text="$1"
  # 読み上げ開始の直前に全ての say を止める（eloop のラジオ含む）
  killall say 2>/dev/null
  local say_args=(-r "$SAY_RATE")
  if [[ -n "$SAY_VOICE" ]]; then
    say_args+=(-v "$SAY_VOICE")
  fi
  say "${say_args[@]}" "$text" &
  echo $! > "$SAY_PID_FILE"
}

# --- ユーティリティ ---
log() {
  echo "[$(date '+%H:%M:%S')] $*" >&2
}

# strategy.py のコンテンツハッシュ（空行・末尾空白を無視）
_content_hash() {
  sed '/^[[:space:]]*$/d' "$STRATEGY" | md5 -q 2>/dev/null || md5sum "$STRATEGY" | cut -d' ' -f1
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

  script -q "$raw_file" opencode run --agent "$agent" "$(cat "$prompt_file")" > /dev/null 2>&1

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

# 生成結果が読み上げに値する内容かチェック
is_valid_commentary() {
  local text="$1"
  local len=${#text}

  # 短すぎる
  if [[ $len -lt $MIN_COMMENTARY_LEN ]]; then
    log "生成結果が短すぎる (${len}文字 < ${MIN_COMMENTARY_LEN})"
    return 1
  fi

  # ファイルパスだけ
  if echo "$text" | grep -qE '^(/[^ ]+[[:space:]]*)+$'; then
    log "生成結果がファイルパスのみ"
    return 1
  fi

  # 日本語がほぼ含まれていない（ひらがな・カタカナが10文字未満）
  local jp_count
  jp_count=$(echo "$text" | perl -ne 'print' | grep -oP '[\p{Hiragana}\p{Katakana}]' 2>/dev/null | wc -l | tr -d ' ')
  if [[ "${jp_count:-0}" -lt 10 ]]; then
    log "生成結果に日本語がほぼない (${jp_count}文字)"
    return 1
  fi

  return 0
}

# AIに解説を生成させる
generate_commentary() {
  local diff_content="$1"
  local context="$2"

  local prompt_file
  prompt_file=$(mktemp /tmp/oc_prompt_XXXXXXXX)

  # ランダムテーマを選ぶ
  local themes=(
    "今回は料理と食文化の話を多めに。各国の名物料理や食べ物の話で脱線して"
    "今回は音楽の話を多めに。各国の民族音楽、有名な作曲家、ポップカルチャーの話で脱線して"
    "今回はスポーツの話を多めに。各国のサッカー、オリンピック、格闘技など運動の話で脱線して"
    "今回は歴史上の人物の話を多めに。各国の英雄、指導者、科学者、芸術家の話で脱線して"
    "今回は地理と自然の話を多めに。各国の山、川、気候、絶景スポットの話で脱線して"
    "今回は映画やアニメの話を多めに。ソ連の映画、各国のエンタメ、ゲーム実況文化の話で脱線して"
    "今回は言語と言葉の話を多めに。各国の言語の特徴、面白い表現、翻訳の難しさの話で脱線して"
    "今回は宇宙と科学の話を多めに。ソ連の宇宙開発、ガガーリン、各国の科学者の話で脱線して"
    "今回は動物の話を多めに。各国の国獣、珍しい動物、動物にまつわることわざの話で脱線して"
    "今回は建築と街並みの話を多めに。各国の世界遺産、有名な建物、都市の雰囲気の話で脱線して"
    "今回は恋愛と人間関係の話を多めに。各国の恋愛事情、結婚式の文化、友情の話で脱線して"
    "今回は失敗と挫折の話を多めに。歴史上の大失敗、そこからの復活劇、失敗の名言で脱線して"
    "今回は祭りと行事の話を多めに。各国のお祭り、伝統行事、新年の祝い方の話で脱線して"
    "今回は鉄道と旅の話を多めに。シベリア鉄道、各国の交通事情、旅行の話で脱線して"
    "今回は哲学と思想の話を多めに。マルクス、ドストエフスキー、人生の意味、AIの存在意義の話で脱線して"
  )
  local theme="${themes[$((RANDOM % ${#themes[@]}))]}"

  # 過去のトーク内容を取得（直近5回分）
  local past_topics=""
  if [[ -f "$PAST_TOPICS_FILE" ]]; then
    past_topics=$(tail -5 "$PAST_TOPICS_FILE")
  fi

  cat > "$prompt_file" <<PROMPT_END
あなたは深夜のゲーム実況ラジオのパーソナリティです。
一人でずっと喋り続けるのが得意で、脱線しまくるけど最終的にはちゃんと戻ってくるタイプです。
リスナーはAIがパズルゲームを自動プレイしているのを眺めながら、あなたのトークを聞いています。

【今回の脱線テーマ指定】
${theme}

【過去のトークで既に話した内容（これらは避けて、新しいネタにすること）】
${past_topics:-まだ過去のトークはありません。自由に話してください。}
PROMPT_END

  cat >> "$prompt_file" <<'PROMPT_END'

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

【重要】出力にファイルパスを含めないこと。純粋なトーク文章のみを出力すること。

【トークの構成（この順番で、全部入れること）】

1. 導入: 「さあ、作戦が更新されましたよ」的な入り
2. 作戦変更の解説: 何が変わったか、国名を使って具体的に説明
3. 脱線トーク1: 差分に出てくる国にまつわる豆知識、雑学、歴史エピソード、名物料理、有名人の話など
4. 過去の作戦との比較: 前の作戦はどうだったか、最高スコアの作戦とどう違うか
5. 脱線トーク2: ことわざや格言を引用して作戦変更に例える、または関連する最近のニュースや時事ネタ
6. 予想と期待: この作戦でスコアは上がりそうか、ソ連完成に近づけるか
7. 愚痴パート: スコアが伸び悩んでいたら愚痴る、AIに文句を言う、励ます、同情する
8. 応援・締め: 次の試合への期待、リスナーへの語りかけ

PROMPT_END

  # 10回に1回だけ「AIが自分を書き換える話」を追加
  if [[ $((RANDOM % 10)) -eq 0 ]]; then
    cat >> "$prompt_file" <<'PROMPT_EXTRA'
【特別コーナー】今回は特別に、AIが自分で自分の作戦を書き換えるということ自体について、哲学的な考察や感想、冗談を交えてたっぷり語ってください。

PROMPT_EXTRA
  fi

  cat >> "$prompt_file" <<'PROMPT_RULES'
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
- 出力はトーク本文のみ。前置きや補足説明は不要。ファイルパスは絶対に含めないこと

PROMPT_RULES

  echo "" >> "$prompt_file"
  echo "【差分】" >> "$prompt_file"
  echo "$diff_content" >> "$prompt_file"
  echo "" >> "$prompt_file"
  echo "【参考: 過去バージョンとの比較コンテキスト】" >> "$prompt_file"
  echo "$context" >> "$prompt_file"

  log "AI解説生成中 (agent: $AI_AGENT)..."

  local result
  result=$(run_opencode "$AI_AGENT" "$prompt_file")
  if [[ -n "$result" ]] && is_valid_commentary "$result"; then
    rm -f "$prompt_file"
    echo "$result"
    return 0
  fi

  log "フォールバック: $AI_FALLBACK で再試行..."
  result=$(run_opencode "$AI_FALLBACK" "$prompt_file")
  if [[ -n "$result" ]] && is_valid_commentary "$result"; then
    rm -f "$prompt_file"
    echo "$result"
    return 0
  fi

  rm -f "$prompt_file"
  log "AI解説生成に失敗しました（有効な結果が得られず）"
  return 1
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

  # コンテンツハッシュで実質的な変更かチェック
  local current_hash
  current_hash=$(_content_hash)
  local last_hash=""
  [[ -f "$LAST_HASH_FILE" ]] && last_hash=$(cat "$LAST_HASH_FILE" 2>/dev/null)

  if [[ "$current_hash" == "$last_hash" ]]; then
    log "コンテンツに実質的な変更なし。スキップ。"
    return
  fi
  echo "$current_hash" > "$LAST_HASH_FILE"

  log "strategy.py の変更を検出!"

  # 1. 直前のバージョンとのdiff生成
  local recent_versions
  recent_versions=$(get_recent_versions 3)
  local latest_version
  latest_version=$(echo "$recent_versions" | head -1)

  local diff_content=""
  if [[ -n "$latest_version" && -f "$latest_version" ]]; then
    diff_content=$(diff -u "$latest_version" "$STRATEGY" 2>/dev/null || true)
    # 実質的な差分があるかチェック（空行やスペースだけの差分を除外）
    local real_changes
    real_changes=$(echo "$diff_content" | grep '^[+-]' | grep -v '^[+-][+-][+-]' | grep -v '^[+-][[:space:]]*$' | wc -l | tr -d ' ')
    if [[ "$real_changes" -lt 2 ]]; then
      log "実質的な差分なし（空行変更のみ）。スキップ。"
      return
    fi
  else
    diff_content="(比較対象のバージョンファイルが見つかりません。現在の strategy.py の内容を解説します)"
    diff_content+=$'\n\n'"$(head -80 "$STRATEGY")"
  fi

  # 2. 過去3バージョンとベストの比較コンテキスト作成
  local context=""

  local i=0
  while IFS= read -r vfile; do
    [[ -z "$vfile" ]] && continue
    i=$((i + 1))
    local vname
    vname=$(basename "$vfile")
    local changelog
    changelog=$(grep -A5 '変更履歴' "$vfile" 2>/dev/null | head -8 || echo "(履歴なし)")
    context+="--- 過去バージョン${i}: ${vname} ---"$'\n'
    context+="$changelog"$'\n\n'
  done <<< "$recent_versions"

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

  if [[ -f "$BEST_SCORE_FILE" ]]; then
    context+=$'\n'"現在のベストスコア: $(cat "$BEST_SCORE_FILE")"
  fi

  # 3. AI解説生成
  local commentary
  if ! commentary=$(generate_commentary "$diff_content" "$context"); then
    log "解説生成失敗。読み上げなし。"
    return
  fi

  # 4. 保存 & 表示
  echo "$commentary" > "$COMMENTARY_FILE"
  log "--- AI解説 ---"
  echo "$commentary"
  log "---------------"

  # 5. 過去トーク記録
  local total_lines
  total_lines=$(echo "$commentary" | wc -l | tr -d ' ')
  local mid=$((total_lines / 2))
  local q3=$((total_lines * 3 / 4))
  local snippet_top snippet_mid snippet_end
  snippet_top=$(echo "$commentary" | sed -n '2,3p' | tr '\n' ' ' | cut -c1-60)
  snippet_mid=$(echo "$commentary" | sed -n "${mid},$((mid+1))p" | tr '\n' ' ' | cut -c1-60)
  snippet_end=$(echo "$commentary" | sed -n "${q3},$((q3+1))p" | tr '\n' ' ' | cut -c1-60)
  local summary
  summary="[$(date '+%H:%M')] 序盤:${snippet_top} / 中盤:${snippet_mid} / 終盤:${snippet_end}"
  echo "$summary" >> "$PAST_TOPICS_FILE"
  tail -10 "$PAST_TOPICS_FILE" > "${PAST_TOPICS_FILE}.tmp" && mv "${PAST_TOPICS_FILE}.tmp" "$PAST_TOPICS_FILE"

  # 6. 読み上げ（有効な内容がある時だけ）
  _start_say "$commentary"
  log "読み上げ開始 (${#commentary}文字)"
}

# --- メインループ ---
log "strategy.py を監視開始..."
log "Ctrl+C で停止"

# 起動時のハッシュを記録（起動直後の誤検知を防ぐ）
_content_hash > "$LAST_HASH_FILE"

# fswatch で strategy.py を監視
fswatch --event Updated "$STRATEGY" | while IFS= read -r event; do
  # デバウンス: 10秒待って連続イベントをまとめる
  sleep 10

  on_strategy_changed
done
