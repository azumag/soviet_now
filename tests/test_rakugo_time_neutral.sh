#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

fail() {
	echo "FAIL: $*" >&2
	exit 1
}

grep -q 'rakugo) announce="創作落語コーナーです。"' broadcast/radio_engine.sh ||
	fail "落語の案内が時間帯に依存しない文言ではありません"

rakugo_prompt=$(sed -n '/^start_radio_corner_rakugo()/,/^start_radio_corner_breakfast()/p' broadcast/radio_corners.sh)
if printf '%s\n' "$rakugo_prompt" | grep -q '深夜'; then
	fail "落語プロンプトに深夜固定の文言が残っています"
fi

grep -q '現在の時間帯に合うオープニング' broadcast/radio_corners.sh ||
	fail "落語プロンプトが現在の時間帯に合わせる指定ではありません"

grep -A40 'local _random_pool=(' broadcast/scheduler.sh | grep -q '"rakugo"' ||
	fail "落語がランダムコーナー候補に含まれていません"

echo "PASS: rakugo wording is time-neutral and remains in the random pool"
