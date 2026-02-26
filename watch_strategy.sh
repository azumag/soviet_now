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

# --- 設定 ---
AI_AGENT="zai"
AI_FALLBACK="glmflash"
SAY_RATE=120  # 読み上げ速度
MIN_COMMENTARY_LEN=200  # これより短い生成結果はゴミとみなす

mkdir -p tmp

# Ctrl+C でクリーンアップ
cleanup() {
  rm -f "$LOCK_FILE"
  exit 0
}
trap cleanup INT TERM

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

  # bash -c でラップ + UTF-8ロケール指定（script -q の安定性とエンコーディング問題を回避）
  LC_ALL=en_US.UTF-8 script -q "$raw_file" bash -c "LC_ALL=en_US.UTF-8 opencode run --agent \"$agent\" \"\$(cat '$prompt_file')\" 2>&1" > /dev/null 2>&1

  local cleaned
  cleaned=$(cat "$raw_file" \
    | strip_ansi \
    | grep -v '^>' \
    | grep -v '^\^D' \
    | grep -v '^/[^ ]*$' \
    | grep -v '^[[:space:]]*/Users/' \
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

  # 現在時刻を取得して時間帯を判定
  local current_hour
  current_hour=$(date '+%H')
  local current_time
  current_time=$(date '+%H:%M')
  local time_period=""
  local time_mood=""

  if [[ $current_hour -ge 5 && $current_hour -lt 9 ]]; then
    time_period="早朝"
    time_mood="眠い目をこすりながらの早朝放送。朝のコーヒーが欲しい。鳥のさえずりが聞こえてきそう。「おはようございます、早起きの同志たち」的なテンション。朝日が昇るように希望を感じる語り口で"
  elif [[ $current_hour -ge 9 && $current_hour -lt 12 ]]; then
    time_period="午前"
    time_mood="午前中のさわやか放送。エンジンがかかってきた。仕事中にこっそり聞いてるリスナーへの語りかけ。「午前中から何やってんだって話ですけど」的な自虐も交えつつ、テキパキしたトーン"
  elif [[ $current_hour -ge 12 && $current_hour -lt 14 ]]; then
    time_period="昼"
    time_mood="お昼の放送。ランチのお供。食べ物の話が自然に出る。「お昼ご飯何食べました?」的な問いかけ。午後への活力を込めて、のんびりしつつも元気なトーン"
  elif [[ $current_hour -ge 14 && $current_hour -lt 17 ]]; then
    time_period="午後"
    time_mood="午後のまったり放送。眠くなる時間帯。「午後の紅茶タイムにお届けする」的な雰囲気。ちょっとだるいけど楽しい、カフェで友達と喋ってるようなリラックストーン"
  elif [[ $current_hour -ge 17 && $current_hour -lt 20 ]]; then
    time_period="夕方"
    time_mood="夕方の放送。一日の終わりが近い。「お疲れ様です」のねぎらい。夕焼けがきれい。晩ご飯のことが気になる。仕事終わりのビールのような開放感で語る"
  elif [[ $current_hour -ge 20 && $current_hour -lt 23 ]]; then
    time_period="夜"
    time_mood="夜の放送。落ち着いた雰囲気。「夜のしじまにお届けする」。一日の振り返りをしつつ、ちょっとしんみりもする。酒が進むような、大人の語り口で"
  elif [[ $current_hour -ge 23 || $current_hour -lt 2 ]]; then
    time_period="深夜"
    time_mood="深夜放送。テンションがおかしくなる時間帯。「こんな時間に何やってんでしょうね、お互い」的な連帯感。夜更かし仲間への語りかけ。ちょっとハイテンション、ちょっとセンチメンタル"
  else
    time_period="未明"
    time_mood="未明の放送。世界で起きてるのは自分だけ感。「朝が来る前の静かな時間」。哲学的になりがち。人生について語りたくなる。星空の下で独り言を言うような、内省的だけど温かいトーン"
  fi

  # ランダムテーマを選ぶ（40種類）
  local themes=(
    # 食文化系
    "各国の名物料理の話。アルメニアのドルマ、ウズベキスタンのプロフ、グルジアのハチャプリなど具体的なメニュー名で脱線して"
    "お酒と飲み物の話。ロシアのウォッカ、グルジアワイン、各国のお茶文化、乾杯の作法で脱線して"
    "パンと小麦の話。ナン、ラヴァシュ、黒パン。各国の主食とそれにまつわる文化で脱線して"
    "各国の屋台飯・ストリートフードの話。市場の活気、値段、匂い、旅行者の体験談風に脱線して"
    # 芸術・エンタメ系
    "各国の民族音楽と楽器の話。ドゥドゥク、バラライカ、コムズなど固有の楽器とその音色で脱線して"
    "ソ連時代の映画・アニメの話。エイゼンシュテイン、タルコフスキー、チェブラーシカなどで脱線して"
    "文学と詩の話。プーシキン、ドストエフスキー、各国の叙事詩、口承文学で脱線して"
    "現代のポップカルチャー。各国のSNS事情、YouTuber、ゲーム文化、ミーム文化で脱線して"
    "絵画と美術の話。各国の伝統模様、イコン画、絨毯のデザイン、色彩感覚の違いで脱線して"
    # 歴史・政治系
    "ソ連崩壊前後の各国のドラマ。独立の瞬間、初代大統領、国旗が変わった日の話で脱線して"
    "シルクロードの話。交易路、キャラバン、東西文化の交差点としての中央アジアで脱線して"
    "冷戦時代の面白エピソード。スパイ合戦、宇宙開発競争、キッチン討論で脱線して"
    "各国の独立記念日と建国神話。どうやって国ができたか、伝説の英雄の話で脱線して"
    # 自然・地理系
    "山と高原の話。コーカサス山脈、天山山脈、パミール高原。登山や絶景で脱線して"
    "川と湖と海の話。カスピ海、バイカル湖、アラル海の悲劇、各国の水辺の暮らしで脱線して"
    "砂漠とステップの話。カラクム砂漠、カザフステップ、遊牧民の暮らし、星空で脱線して"
    "各国の気候と四季の話。極寒のシベリア、温暖な黒海沿岸、気候が人の性格に与える影響で脱線して"
    # 暮らし・文化系
    "各国の結婚式と恋愛事情。伝統的な婚礼、結納の風習、プロポーズの文化で脱線して"
    "子育てと教育の話。ソ連時代の教育制度、各国の学校、子供の遊びで脱線して"
    "お祭りと年中行事の話。ナウルーズ、マスレニツァ、各国の新年の祝い方で脱線して"
    "ファッションと民族衣装の話。各国の伝統衣装、刺繍の模様、おしゃれ事情で脱線して"
    "住まいと建築の話。ユルタ、ダーチャ、ソ連式アパート、各国の世界遺産建築で脱線して"
    # 科学・技術系
    "宇宙開発の話。ガガーリン、スプートニク、バイコヌール宇宙基地、宇宙犬ライカで脱線して"
    "数学と科学の天才たち。各国出身の科学者、発明、ノーベル賞受賞者で脱線して"
    "チェスの話。カスパロフ、カルポフ、各国のチェス文化、AI対人間の対局で脱線して"
    "鉄道と交通の話。シベリア鉄道、各国の地下鉄の美しさ、旅のロマンで脱線して"
    # スポーツ・ゲーム系
    "各国のサッカー事情。旧ソ連代表、各国リーグ、ワールドカップの思い出で脱線して"
    "格闘技と武術の話。レスリング、サンボ、各国の伝統的な格闘技で脱線して"
    "オリンピックの話。ソ連の金メダルラッシュ、各国のオリンピック選手、感動のエピソードで脱線して"
    "ボードゲームとパズルの話。テトリス誕生秘話、各国のゲーム文化、パズルの数学で脱線して"
    # 思想・哲学系
    "哲学と思想の話。マルクス、レーニン、でも実はもっと深い各国の哲学者の話で脱線して"
    "ことわざと民間伝承の話。各国の面白いことわざ、おばあちゃんの知恵、迷信で脱線して"
    "夢と睡眠の話。各国の夢占い、睡眠文化、不眠症、夢に出てくる国の話で脱線して"
    # ユニーク系
    "各国の変な法律・珍しい風習。意外なルール、文化の違いで驚く話で脱線して"
    "各国のお土産とショッピングの話。マトリョーシカ以外の名産品、市場でのぼったくり体験風に脱線して"
    "各国の郵便と手紙の話。切手コレクション、国際郵便、文通文化で脱線して"
    "各国の乗り物・車の話。ラーダ、トラバント、旧ソ連の車文化、タクシー事情で脱線して"
    "各国の迷信・おまじない・ジンクスの話。黒猫、割れた鏡、数字の吉凶で脱線して"
    "温泉とサウナの話。ロシアのバーニャ、各国の入浴文化、健康法で脱線して"
    "各国の軍事パレードと式典の話。赤の広場、独立記念日のパレード、軍楽隊の音楽で脱線して"
  )
  local theme="${themes[$((RANDOM % ${#themes[@]}))]}"

  # 過去のトーク内容を取得（直近10回分）
  local past_topics=""
  if [[ -f "$PAST_TOPICS_FILE" ]]; then
    past_topics=$(tail -10 "$PAST_TOPICS_FILE")
  fi

  cat > "$prompt_file" <<PROMPT_END
あなたはゲーム実況ラジオのパーソナリティです。
一人でずっと喋り続けるのが得意で、脱線しまくるけど最終的にはちゃんと戻ってくるタイプです。
リスナーはAIがパズルゲームを自動プレイしているのを眺めながら、あなたのトークを聞いています。

【現在時刻】${current_time}（${time_period}）
【時間帯の雰囲気】${time_mood}

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

1. 時間帯に合わせた挨拶と導入: 上の時間帯の雰囲気に合わせた入り。「こんな時間に作戦更新ですよ」的な
2. 作戦変更の解説: 何が変わったか、国名を使って具体的に説明
3. 脱線トーク1: 上で指定されたテーマに沿って、具体的なエピソード・固有名詞・数字を出してたっぷり語る
4. 過去の作戦との比較: 前の作戦はどうだったか、最高スコアの作戦とどう違うか
5. 脱線トーク2: まったく別の角度から脱線。今の時間帯ならではの話（深夜なら怪談や星の話、朝なら目覚ましの話、昼なら食事の話など）
6. 予想と期待: この作戦でスコアは上がりそうか、ソ連完成に近づけるか
7. 脱線トーク3: ことわざ・格言・ダジャレ・ジョーク・都市伝説・最近気になったことなど、自由に
8. 愚痴パート: スコアが伸び悩んでいたら愚痴る、AIに文句を言う、励ます、同情する
9. 応援・締め: 次の試合への期待、時間帯に合わせたリスナーへの語りかけ

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
- 時間帯に合った語りかけを忘れないこと。深夜なら眠いネタ、朝ならコーヒーのネタ、昼なら腹減ったネタなど
- 「ちなみに」「そういえば」「話は変わるんですけど」「いやでもさ」「あ、そうだ」「ところでさ」などの接続詞で脱線を自然につなぐ
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
  # eloopのラジオトークが再生中なら二重再生を避けてスキップ
  if [[ -f "tmp/.radio_active" ]]; then
    log "eloopラジオ再生中 → スキップ"
    return
  fi

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

  # 4. トーク本文と要約を分離
  local commentary_body commentary_summary
  commentary_body=$(echo "$commentary" | sed '/^===SUMMARY===/,$d')
  commentary_summary=$(echo "$commentary" | sed -n '/^===SUMMARY===/,$ p' | tail -n +2)

  # 要約が取れなかった場合のフォールバック
  if [[ -z "$commentary_summary" ]]; then
    commentary_summary="(要約なし)"
  fi

  # 本文のみを保存 & 表示
  echo "$commentary_body" > "$COMMENTARY_FILE"
  log "--- AI解説 ---"
  echo "$commentary_body"
  log "---------------"

  # 5. 過去トーク記録（AI生成の要約、直近10件保持）
  echo "[$(date '+%H:%M')] ${commentary_summary}" >> "$PAST_TOPICS_FILE"
  tail -10 "$PAST_TOPICS_FILE" > "${PAST_TOPICS_FILE}.tmp" && mv "${PAST_TOPICS_FILE}.tmp" "$PAST_TOPICS_FILE"

  # 6. say_enqueue で読み上げ（前のトークが終わるまで待つ、プリエンプション対応）
  # AI生成中にeloopラジオが始まった場合の再チェック
  if [[ -f "tmp/.radio_active" ]]; then
    log "eloopラジオが開始された → 読み上げスキップ"
    return
  fi
  log "読み上げキュー登録 (${#commentary_body}文字)"
  ./say_enqueue.sh "$COMMENTARY_FILE" "$SAY_RATE"
  # say_enqueue がプリエンプトされた場合は何も読み上げずに戻る
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
