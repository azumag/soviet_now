#!/bin/bash
# 全COEIROINK話者・スタイルのテスト再生
# Usage: ./test_all_voices.sh
cd "$(dirname "$0")"

TEXT="本日は晴天なり。ソビエト連邦のパズルゲームへようこそ。国家を併合して、偉大なる連邦を作り上げましょう。"

voices=(
"6e0539ea-a6a7-11f0-8d2f-0242ac1c000c|172697038|AⅡowa|β"
    "8e99d620-87d3-11ed-870a-0242ac1c000c|905192261|ワカナ|normal"
    "8e99d620-87d3-11ed-870a-0242ac1c000c|905192262|ワカナ|sweet"
    "8e99d620-87d3-11ed-870a-0242ac1c000c|905192263|ワカナ|whisper"
    "9bf2ab50-c756-11ec-9374-0242ac1c0002|1403759395|ナースロボ＿タイプＴ|通常"
    "9bf2ab50-c756-11ec-9374-0242ac1c0002|1403759396|ナースロボ＿タイプＴ|内緒話"
    "9bf2ab50-c756-11ec-9374-0242ac1c0002|1403759397|ナースロボ＿タイプＴ|淡々"
    "fb1a910e-208f-11ee-8dde-0242ac1c000c|981131759|すーぱーAIモコちゃん|のーまる"
    "fb1a910e-208f-11ee-8dde-0242ac1c000c|981131760|すーぱーAIモコちゃん|なれーしょん"
    "fb1a910e-208f-11ee-8dde-0242ac1c000c|981131761|すーぱーAIモコちゃん|ろうどく"
    "fb1a910e-208f-11ee-8dde-0242ac1c000c|981131762|すーぱーAIモコちゃん|よろこび"
    "fb1a910e-208f-11ee-8dde-0242ac1c000c|981131763|すーぱーAIモコちゃん|なきごえ"
    "fb1a910e-208f-11ee-8dde-0242ac1c000c|981131764|すーぱーAIモコちゃん|ひそひそ"
    "fb1a910e-208f-11ee-8dde-0242ac1c000c|981131765|すーぱーAIモコちゃん|ろぼろぼ"
    "fb1a910e-208f-11ee-8dde-0242ac1c000c|981131766|すーぱーAIモコちゃん|あいまい"
    "297a5b91-f88a-6951-5841-f1e648b2e594|30|KANA|のーまる"
    "3c37646f-3881-5374-2a83-149267990abc|0|つくよみちゃん|れいせい"
)

total=${#voices[@]}
i=0
for entry in "${voices[@]}"; do
    IFS='|' read -r uuid style_id name style_name <<< "$entry"
    i=$((i + 1))
    echo ""
    echo "=== [$i/$total] $name ($style_name) ==="
    wav="/tmp/coe_test_${i}.wav"
    if SPEAKER_UUID="$uuid" STYLE_ID="$style_id" \
       ./coeiroink_tts.sh -o "$wav" "$TEXT" 2>&1; then
        echo "  -> 再生中..."
        afplay "$wav"
        rm -f "$wav"
        echo "  -> OK"
    else
        echo "  -> FAILED"
    fi
done

echo ""
echo "=== 完了: $total パターン テスト済み ==="
