#!/usr/bin/env bash
# tests/test_wildcard_parallel_browser_env.sh - docich#39
#
# wildcard_parallel.py の候補Chrome(browser)起動、および AI生成の
# WILDCARD候補strategy.pyを実行するstrategy_runner.py起動の両方が使う
# 共通ベース環境 `_browser_base_env()` が、credential sentinelを一切
# 含まないことを実測する(env -i + non-secret allowlist方式)。
#
# 実プロセスを起動せず、モジュールを直接importして関数を単体で呼ぶ
# (Chrome/Playwright起動やゲームエンジン実行は重く、かつこのリポジトリの
# CI/dev環境に依存するため)。os.environ.copy()が本当に無くなったことは
# 静的grepでも二重に確認する。

set -u

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

ok=0
fail=0
check() {
	local condition="$1" message="$2"
	if eval "$condition"; then
		printf 'ok - %s\n' "$message"
		ok=$((ok + 1))
	else
		printf 'not ok - %s\n' "$message"
		fail=$((fail + 1))
	fi
}

# ---------------------------------------------------------------------------
# A. 静的確認: wildcard_parallel.py に os.environ.copy() が残っていない
#    (候補Chrome/strategy_runner.py へ渡す環境を組み立てる箇所は
#    全て allowlist ベースの _browser_base_env() 経由になっている)
# ---------------------------------------------------------------------------
# コメント中の言及(docich#39の説明文)ではなく、実際の代入文だけを数える。
copy_calls=$(grep -cE '(^|[^#[:alnum:]_])[A-Za-z_]+ = os\.environ\.copy\(\)' "$ROOT/wildcard_parallel.py" || true)
check "[ \"$copy_calls\" -eq 0 ]" \
	"wildcard_parallel.pyにos.environ.copy()の代入文が残っていない (実測: ${copy_calls}件、コメント中の言及は対象外)"

launch_env_for_uses_helper=$(awk '/def launch_env_for/,/^    def launch_direct/' "$ROOT/wildcard_parallel.py" | grep -c '_browser_base_env()')
check "[ \"$launch_env_for_uses_helper\" -ge 1 ]" \
	"launch_env_for(候補Chrome起動)が_browser_base_env()を使っている"

# 候補Chrome起動(launch_env_for)用と、strategy_runner.py起動用の
# 2箇所がそれぞれ_browser_base_env()を使っているはず。
base_env_call_sites=$(grep -c 'env = _browser_base_env()' "$ROOT/wildcard_parallel.py")
check "[ \"$base_env_call_sites\" -eq 2 ]" \
	"候補Chrome起動とstrategy_runner.py起動の両方が_browser_base_env()を使っている (実測: ${base_env_call_sites}箇所)"

# ---------------------------------------------------------------------------
# B. 動的確認: sentinelを使い、_browser_base_env()の戻り値にcredential系
#    変数が一切含まれないこと、かつ allowlist変数は含まれることを実測する。
# ---------------------------------------------------------------------------
SENTINEL="SENTINEL_BROWSER_ENV_DO_NOT_LEAK_9e21bb"

result=$(
	cd "$ROOT" && \
	TWITCH_OAUTH_TOKEN="$SENTINEL" \
	TWITCH_CLIENT_SECRET="$SENTINEL" \
	YOUTUBE_API_KEY="$SENTINEL" \
	YOUTUBE_OAUTH_REFRESH_TOKEN="$SENTINEL" \
	DISCORD_WEBHOOK_URL="$SENTINEL" \
	OBS_WEBSOCKET_PASSWORD="$SENTINEL" \
	SOME_RANDOM_UNLISTED_VAR="$SENTINEL" \
	DISPLAY=":99" \
	PULSE_SERVER="unix:/run/user/1001/pulse/native" \
	python3 -c "
import importlib.util, sys, json
spec = importlib.util.spec_from_file_location('wildcard_parallel', 'wildcard_parallel.py')
mod = importlib.util.module_from_spec(spec)
sys.modules['wildcard_parallel'] = mod
spec.loader.exec_module(mod)
env = mod._browser_base_env()
print(json.dumps(env))
"
)

check "[ -n \"\$result\" ]" "_browser_base_env()を実際に呼び出して結果を取得できる"

leak_count=$(printf '%s' "$result" | python3 -c "
import json, sys
env = json.load(sys.stdin)
sentinel = '$SENTINEL'
hits = [k for k, v in env.items() if v == sentinel]
print(len(hits))
")
check "[ \"$leak_count\" -eq 0 ]" \
	"_browser_base_env()の戻り値にcredential sentinelが一切含まれない (実測: ${leak_count}件のキーで一致)"

has_display=$(printf '%s' "$result" | python3 -c "
import json, sys
env = json.load(sys.stdin)
print('1' if env.get('DISPLAY') == ':99' else '0')
")
check "[ \"$has_display\" = \"1\" ]" \
	"allowlist経由でDISPLAYが正しくbrowser用envへ渡る (Xvfb描画に必須)"

has_pulse=$(printf '%s' "$result" | python3 -c "
import json, sys
env = json.load(sys.stdin)
print('1' if env.get('PULSE_SERVER', '').startswith('unix:') else '0')
")
check "[ \"$has_pulse\" = \"1\" ]" \
	"allowlist経由でPULSE_SERVERが正しくbrowser用envへ渡る (音声出力に必須)"

no_unlisted=$(printf '%s' "$result" | python3 -c "
import json, sys
env = json.load(sys.stdin)
print('1' if 'SOME_RANDOM_UNLISTED_VAR' not in env else '0')
")
check "[ \"$no_unlisted\" = \"1\" ]" \
	"allowlistに無い変数は(sentinelでなくても)渡らない"

printf '1..%d\n' "$((ok + fail))"
printf '%s passed, %s failed\n' "$ok" "$fail"
[ "$fail" -eq 0 ]
